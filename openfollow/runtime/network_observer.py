# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Keeps every network plane on the interface it was configured for.

A plane pins an **interface**, never an address. The address on that interface
is free to change – a reconnect that yields a new DHCP lease is normal and must
be followed – but the plane must never move to a different interface. When the
configured interface has no address the plane binds nothing, says so, and waits
for it to come back.

Nothing else notices these transitions on its own: the sockets pin
``IP_MULTICAST_IF`` / ``IP_ADD_MEMBERSHIP`` at open time, and an idle receive
times out rather than raising, so a plane bound to an address that has gone
away keeps looking healthy while sending nowhere.

Three properties are load-bearing and easy to lose in a refactor:

*Decisions compare against what is actually bound*, not against the previous
poll's resolution. A plane the runtime already bound correctly is left alone
rather than torn down and rebuilt, and a plane something else rebound behind
the observer's back is noticed rather than assumed still suspended.

*Suspension is debounced; resumption is not.* Applying an address or renewing a
lease runs ``nmcli con down`` then ``con up``, so the interface is legitimately
addressless for a second or more. Reacting to the first poll would mean the
Network page's own buttons drop stage output.

*A down→up transition rebuilds even when the address is unchanged.* Removing an
address drops the interface's multicast memberships and routes; getting the
same DHCP lease back does not restore them, and comparing address strings
cannot see that. Outages shorter than one poll interval are invisible to
polling and are not claimed to be handled.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from openfollow.net_utils import ResolveStatus

logger = logging.getLogger(__name__)

# How often the interface table is re-read. Enumerating adapters costs a psutil
# call and the housekeeping tick runs at 100 ms, so this throttles it to roughly
# the rate an operator would notice a cable moving.
POLL_INTERVAL_S = 1.0

# Consecutive polls an interface must look addressless before its plane is
# stopped. ``nmcli con down``/``con up`` behind Apply and Renew DHCP lease
# leaves a gap of ~1 s for a static apply and up to ~5 s for a DHCP handshake,
# so a single missing sample is not evidence of an outage.
DOWN_POLLS_BEFORE_SUSPEND = 8

# Backoff after a failed apply/suspend, so a plane whose backend keeps raising
# doesn't retry once a second for the length of a show. Each failure doubles
# the wait, up to the cap.
_RETRY_BACKOFF_S = 2.0
_RETRY_BACKOFF_MAX_S = 60.0


def _always_enabled() -> bool:
    return True


@dataclass(frozen=True)
class Plane:
    """One network function and how to point it at an interface."""

    label: str
    # Returns (bind address, status, configured interface). The address is
    # empty when the configured interface currently has none.
    resolve: Callable[[], tuple[str, ResolveStatus, str]]
    # The address this plane is bound to right now, or None when it is not
    # running. Compared against the resolution to decide whether anything needs
    # doing, so the observer never rebuilds a binding that is already correct.
    current: Callable[[], str | None]
    # Bind to the given address. Only called with a non-empty address.
    apply: Callable[[str], None]
    # Bind nothing. Called when the configured interface has no address, so no
    # traffic leaves on an interface the operator did not choose.
    suspend: Callable[[], None]
    # False when the operator has this output switched off. A disabled plane is
    # never touched and never alerts – it is not broken, it is off.
    enabled: Callable[[], bool] = _always_enabled


@dataclass
class _PlaneState:
    down_polls: int = 0
    suspended: bool = False
    # Set once the interface has been seen addressless, so the plane rebuilds
    # on the way back even when the address is byte-identical.
    saw_outage: bool = False
    failure: str = ""
    retry_at: float = 0.0
    backoff: float = _RETRY_BACKOFF_S


@dataclass
class NetworkPlaneObserver:
    """Polls each plane's configured interface and repoints or stops it."""

    planes: list[Plane]
    clock: Callable[[], float]
    _states: dict[str, _PlaneState] = field(default_factory=dict)
    _next_poll: float = 0.0

    def poll(self, *, force: bool = False) -> bool:
        """Re-resolve every plane and apply any change. Never raises.

        Returns whether the throttle let this call through, so a caller with
        its own equally expensive follow-up work can share the same interval.
        """
        now = self.clock()
        if not force and now < self._next_poll:
            return False
        self._next_poll = now + POLL_INTERVAL_S
        for plane in self.planes:
            try:
                self._poll_one(plane, now)
            except Exception as exc:  # noqa: BLE001
                # One plane's backend misbehaving must not stop the others
                # being followed – a stalled OTP rebind cannot be allowed to
                # leave PSN on a dead address. Recorded so it surfaces to the
                # operator instead of only appearing as a repeating traceback.
                self._record_failure(plane, now, exc)
        return True

    def _state(self, label: str) -> _PlaneState:
        return self._states.setdefault(label, _PlaneState())

    def _record_failure(self, plane: Plane, now: float, exc: Exception) -> None:
        state = self._state(plane.label)
        state.failure = str(exc) or exc.__class__.__name__
        state.retry_at = now + state.backoff
        state.backoff = min(state.backoff * 2, _RETRY_BACKOFF_MAX_S)
        logger.exception("Network observer: %s failed", plane.label)

    @staticmethod
    def _clear_failure(state: _PlaneState) -> None:
        state.failure = ""
        state.retry_at = 0.0
        state.backoff = _RETRY_BACKOFF_S

    def _poll_one(self, plane: Plane, now: float) -> None:
        if not plane.enabled():
            # Reset rather than carry a stale down-count or alert into the next
            # time the operator switches this output on.
            self._states[plane.label] = _PlaneState()
            return
        state = self._state(plane.label)
        if now < state.retry_at:
            return

        address, status, iface = plane.resolve()
        # "none" is nothing configured and nothing to auto-detect: the station
        # has no address at all. Binding "" would hand the plane INADDR_ANY,
        # which is the wrong network by definition.
        if status in ("down", "none"):
            self._handle_down(plane, state, iface)
            return

        state.down_polls = 0
        # Compare against the live binding, not the previous resolution: the
        # runtime may already have bound this correctly at startup, and
        # something else may have rebound it since.
        if plane.current() == address and not state.saw_outage:
            state.suspended = False
            self._clear_failure(state)
            return

        plane.apply(address)
        if state.suspended or state.saw_outage:
            logger.info(
                "%s: interface %s is back at %s; output resumed.",
                plane.label,
                iface or "auto-detect",
                address,
            )
        state.suspended = False
        state.saw_outage = False
        self._clear_failure(state)

    def _handle_down(self, plane: Plane, state: _PlaneState, iface: str) -> None:
        state.down_polls += 1
        state.saw_outage = True
        if state.down_polls < DOWN_POLLS_BEFORE_SUSPEND:
            return
        # Re-check the live binding rather than trusting the recorded flag: a
        # config save elsewhere restarts the output on a wildcard bind, which
        # would otherwise run misrouted while this still says "suspended".
        if state.suspended and plane.current() is None:
            return
        plane.suspend()
        state.suspended = True
        self._clear_failure(state)
        logger.error(
            "%s: configured interface %s has no address; output stopped until it returns "
            "(it will not be sent on another interface).",
            plane.label,
            iface or "auto-detect",
        )

    def alerts(self) -> list[str]:
        """Operator-facing lines for every plane that is not currently sending.

        Rendered on the HUD, which is the only surface an operator has when the
        interface carrying the web UI is the one that went away. Emitted in
        plane order so the rows don't reshuffle between frames.
        """
        out: list[str] = []
        for plane in self.planes:
            state = self._states.get(plane.label)
            if state is None:
                continue
            if state.suspended:
                out.append(f"{plane.label}: {self._iface_of(plane)} is down")
            elif state.failure:
                # A plane whose rebind keeps failing is just as dead as a
                # suspended one, and used to surface nowhere at all.
                out.append(f"{plane.label}: {state.failure}")
        return out

    @staticmethod
    def _iface_of(plane: Plane) -> str:
        try:
            return plane.resolve()[2] or "interface"
        except Exception:  # noqa: BLE001
            return "interface"
