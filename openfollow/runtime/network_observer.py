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
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from openfollow.net_utils import ResolveStatus
from openfollow.uri_redaction import redact_uris_in_text

logger = logging.getLogger(__name__)

# How often the interface table is re-read. Enumerating adapters costs a psutil
# call and the housekeeping tick runs at 100 ms, so this throttles it to roughly
# the rate an operator would notice a cable moving.
POLL_INTERVAL_S = 1.0

# Consecutive polls an interface must look addressless before its plane is
# stopped. ``nmcli con down``/``con up`` behind Apply and Renew DHCP lease
# leaves a gap of ~1 s for a static apply and up to ~5 s for a DHCP handshake,
# so a missing sample is reported at once but is no reason to stop the plane.
DOWN_POLLS_BEFORE_SUSPEND = 8

# Backoff after a failed apply/suspend, so a plane whose backend keeps raising
# doesn't retry once a second for the length of a show. Each failure doubles
# the wait, up to the cap.
_RETRY_BACKOFF_S = 2.0
_RETRY_BACKOFF_MAX_S = 60.0


def _always_enabled() -> bool:
    return True


def _bare_name(iface: str) -> str:
    return iface


def _always_present(_iface: str) -> bool:
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
    # False when there is nothing to follow: the output is switched off, or no
    # interface pins it and the routing table chooses. Such a plane is never
    # touched and never alerts – it is not broken.
    enabled: Callable[[], bool] = _always_enabled
    # Identity across polls when labels repeat or change; defaults to the label.
    key: str = ""

    @property
    def state_key(self) -> str:
        return self.key or self.label


@dataclass
class _PlaneState:
    down_polls: int = 0
    suspended: bool = False
    # Set once the interface has been seen addressless, so the plane rebuilds
    # on the way back even when the address is byte-identical.
    saw_outage: bool = False
    failure: str = ""
    # An outage that began after the failure leads instead, until it ends.
    superseded: bool = False
    retry_at: float = 0.0
    backoff: float = _RETRY_BACKOFF_S
    # Interface name from the last resolve, so ``alerts()`` can name it without
    # re-resolving. ``alerts()`` runs on the render path.
    iface: str = ""
    address: str = ""
    # Not followed (see ``Plane.enabled``): never touched, reported as such.
    unfollowed: bool = False
    # Set once a resolve has returned, so a failure before any reads as never resolved.
    resolved: bool = False
    # False while an addressless interface does not exist at all: an adapter unplugged.
    present: bool = True


# A plane with nothing to follow (see ``Plane.enabled``).
NOT_FOLLOWED = "not followed"


@dataclass(frozen=True)
class PlaneStatus:
    """One plane as the last poll left it, for the diagnostics bundle.

    ``state`` is ``ok``, ``not followed`` (switched off, or unpinned and left
    to the routing table, so it may well be running), ``down`` (addressless,
    not yet stopped), ``stopped`` (suspended) or ``failing`` (a rebind keeps
    raising).
    """

    label: str
    iface: str
    address: str
    bound: str | None
    state: str
    detail: str = ""
    # False for a plane never resolved, whose blank ``iface`` is not auto-detect.
    resolved: bool = True


@dataclass
class NetworkPlaneObserver:
    """Polls each plane's configured interface and repoints or stops it.

    *planes* may be a provider, evaluated once per poll, for a set that changes
    at runtime; the state of a plane that leaves the set is dropped with it.
    """

    planes: Sequence[Plane] | Callable[[], Sequence[Plane]]
    clock: Callable[[], float]
    # How alerts, logs and the bundle name an interface: "Lighting (enx…)" when labelled.
    describe_iface: Callable[[str], str] = _bare_name
    # Tells an unplugged adapter ("not connected") from one without an address ("down").
    iface_present: Callable[[str], bool] = _always_present
    _states: dict[str, _PlaneState] = field(default_factory=dict)
    _next_poll: float = 0.0
    _polled: tuple[Plane, ...] = ()
    # Replaced whole once per poll, so a reader on another thread never sees a
    # half-built one.
    _status: tuple[PlaneStatus, ...] = ()

    def __post_init__(self) -> None:
        if not callable(self.planes):
            self._polled = tuple(self.planes)

    def poll(self, *, force: bool = False) -> bool:
        """Re-resolve every plane and apply any change. Never raises.

        Returns whether the throttle let this call through, so a caller with
        its own equally expensive follow-up work can share the same interval.
        """
        now = self.clock()
        if not force and now < self._next_poll:
            return False
        self._next_poll = now + POLL_INTERVAL_S
        statuses: list[PlaneStatus] = []
        for plane in self._refresh_planes():
            try:
                self._poll_one(plane, now)
            except Exception as exc:  # noqa: BLE001
                # One plane's backend misbehaving must not stop the others
                # being followed – a stalled OTP rebind cannot be allowed to
                # leave PSN on a dead address. Recorded so it surfaces to the
                # operator instead of only appearing as a repeating traceback.
                self._record_failure(plane, now, exc)
            statuses.append(self._status_of(plane))
        self._status = tuple(statuses)
        return True

    def snapshot(self) -> tuple[PlaneStatus, ...]:
        """Every plane as the last poll left it. Safe from any thread; resolves nothing."""
        return self._status

    def _status_of(self, plane: Plane) -> PlaneStatus:
        state = self._state(plane.state_key)
        if state.unfollowed and not state.failure:
            return PlaneStatus(plane.label, "", "", None, NOT_FOLLOWED, resolved=False)
        try:
            bound = plane.current()
        except Exception:  # noqa: BLE001 - the poll already recorded this plane's fault
            bound = None
        # A failing rebind or stop outranks an outage, unless the outage began after it.
        if state.failure and not state.superseded:
            return PlaneStatus(
                plane.label, state.iface, state.address, bound, "failing", state.failure, resolved=state.resolved
            )
        where = self._name(state.iface) or "the interface"
        if state.suspended:
            outage = "is not connected" if not state.present else "has no address"
            return PlaneStatus(plane.label, state.iface, "", bound, "stopped", f"{where} {outage}")
        if state.down_polls:
            outage = "not connected" if not state.present else "no address"
            detail = f"{outage} for {state.down_polls} of {DOWN_POLLS_BEFORE_SUSPEND} polls"
            return PlaneStatus(plane.label, state.iface, "", bound, "down", detail)
        return PlaneStatus(plane.label, state.iface, state.address, bound, "ok")

    def _refresh_planes(self) -> tuple[Plane, ...]:
        if not callable(self.planes):
            return self._polled
        try:
            polled = tuple(self.planes())
        except Exception:  # noqa: BLE001
            logger.exception("Network observer: listing the planes failed; keeping the last set")
            return self._polled
        keys = {plane.state_key for plane in polled}
        if any(key not in keys for key in self._states):
            self._states = {key: state for key, state in self._states.items() if key in keys}
        self._polled = polled
        return polled

    def _state(self, key: str) -> _PlaneState:
        return self._states.setdefault(key, _PlaneState())

    def _name(self, iface: str) -> str:
        """*iface* as an operator reads it; the bare name when the lookup fails."""
        if not iface:
            return ""
        try:
            return self.describe_iface(iface) or iface
        except Exception:  # noqa: BLE001 - naming must never cost an alert
            return iface

    def _record_failure(self, plane: Plane, now: float, exc: Exception) -> None:
        state = self._state(plane.state_key)
        if now < state.retry_at:
            # Already backing off: the failure behind it stands, and a resolver that keeps raising logs once.
            return
        state.failure = redact_uris_in_text(str(exc) or exc.__class__.__name__)
        state.superseded = False
        state.retry_at = now + state.backoff
        state.backoff = min(state.backoff * 2, _RETRY_BACKOFF_MAX_S)
        logger.exception("Network observer: %s failed", plane.label)

    @staticmethod
    def _clear_failure(state: _PlaneState) -> None:
        state.failure = ""
        state.superseded = False
        state.retry_at = 0.0
        state.backoff = _RETRY_BACKOFF_S

    def _poll_one(self, plane: Plane, now: float) -> None:
        if not plane.enabled():
            # Reset rather than carry a stale down-count or alert into the next
            # time the operator switches this output on.
            self._states[plane.state_key] = _PlaneState(unfollowed=True)
            return
        state = self._state(plane.state_key)
        state.unfollowed = False
        # Every poll resolves, so an outage is seen and leads from its first
        # poll; a failure's backoff holds back only apply and suspend.
        backing_off = now < state.retry_at

        address, status, iface = plane.resolve()
        state.resolved = True
        state.iface = iface
        state.address = address
        # "none" is nothing configured and nothing to auto-detect: the station
        # has no address at all. Binding "" would hand the plane INADDR_ANY,
        # which is the wrong network by definition.
        if status in ("down", "none"):
            state.present = not iface or self.iface_present(iface)
            self._handle_down(plane, state, iface, backing_off=backing_off)
            return

        state.down_polls = 0
        # Unbound until the retry: a failure the outage stood in front of leads again.
        state.superseded = False
        # Compare against the live binding, not the previous resolution: the
        # runtime may already have bound this correctly at startup, and
        # something else may have rebound it since.
        if plane.current() == address and not state.saw_outage:
            state.suspended = False
            self._clear_failure(state)
            return
        if backing_off:
            return

        plane.apply(address)
        if state.suspended or state.saw_outage:
            logger.info(
                "%s: interface %s is back at %s; output resumed.",
                plane.label,
                self._name(iface) or "auto-detect",
                address,
            )
        state.suspended = False
        state.saw_outage = False
        self._clear_failure(state)

    def _handle_down(self, plane: Plane, state: _PlaneState, iface: str, *, backing_off: bool) -> None:
        if state.down_polls == 0:
            state.superseded = bool(state.failure)
        state.down_polls += 1
        state.saw_outage = True
        if state.down_polls < DOWN_POLLS_BEFORE_SUSPEND:
            return
        # Re-check the live binding rather than trusting the recorded flag: a
        # config save elsewhere restarts the output on a wildcard bind, which
        # would otherwise run misrouted while this still says "suspended".
        if state.suspended and plane.current() is None:
            self._clear_failure(state)
            return
        if backing_off:
            return
        plane.suspend()
        state.suspended = True
        self._clear_failure(state)
        logger.error(
            "%s: configured interface %s %s; output stopped until it returns "
            "(it will not be sent on another interface).",
            plane.label,
            self._name(iface) or "auto-detect",
            "has no address" if state.present else "is not connected",
        )

    def alerts(self) -> list[str]:
        """Operator-facing lines for every plane that is not currently sending.

        Rendered on the HUD, which is the only surface an operator has when the
        interface carrying the web UI is the one that went away. Emitted in
        plane order so the rows don't reshuffle between frames.

        Reads the interface name cached by the last poll rather than resolving
        it: this runs once per rendered frame (60-120 Hz), and resolving walks
        every interface via psutil - so an outage would put that enumeration on
        the render path for as long as it lasted.
        """
        out: list[str] = []
        for plane in self._polled:
            state = self._states.get(plane.state_key)
            if state is None:
                continue
            if state.failure and not state.superseded:
                # A failed rebind or suspend leads: it says why the plane is
                # dead, also while its retry waits out the backoff.
                where = f"{self._name(state.iface)} – " if state.iface else ""
                out.append(f"{plane.label}: {where}{state.failure}")
            elif state.suspended or state.down_polls:
                # Off the show from the first addressless poll; the suspend is
                # debounced only so Apply and Renew don't tear the plane down.
                outage = "is down" if state.present else "is not connected"
                out.append(f"{plane.label}: {self._name(state.iface) or 'interface'} {outage}")
        return out
