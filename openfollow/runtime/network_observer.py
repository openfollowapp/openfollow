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
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from openfollow.net_utils import ResolveStatus

logger = logging.getLogger(__name__)

# How often the interface table is re-read. Enumerating adapters costs a psutil
# call, and the housekeeping tick runs at 100 ms, so this throttles it to
# roughly the rate an operator would notice a cable moving.
POLL_INTERVAL_S = 1.0


@dataclass(frozen=True)
class Plane:
    """One network function and how to point it at an interface."""

    label: str
    # Returns (bind address, status, configured interface). The address is
    # empty when the configured interface currently has none.
    resolve: Callable[[], tuple[str, ResolveStatus, str]]
    # Bind to the given address. Only called with a non-empty address.
    apply: Callable[[str], None]
    # Bind nothing. Called when the configured interface has no address, so no
    # traffic leaves on an interface the operator did not choose.
    suspend: Callable[[], None]


@dataclass
class _Binding:
    iface: str
    address: str
    down: bool


@dataclass
class NetworkPlaneObserver:
    """Polls each plane's configured interface and repoints or stops it.

    Idempotent: a plane is only touched when its resolved ``(interface,
    address)`` differs from what was last applied, so a steady station does no
    work beyond the resolve.
    """

    planes: list[Plane]
    clock: Callable[[], float]
    _bindings: dict[str, _Binding] = field(default_factory=dict)
    _next_poll: float = 0.0

    def poll(self, *, force: bool = False) -> None:
        """Re-resolve every plane and apply any change. Never raises."""
        now = self.clock()
        if not force and now < self._next_poll:
            return
        self._next_poll = now + POLL_INTERVAL_S
        for plane in self.planes:
            try:
                self._poll_one(plane)
            except Exception:
                # One plane's backend misbehaving must not stop the others from
                # being followed - a stalled OTP rebind cannot be allowed to
                # leave PSN on a dead address.
                logger.exception("Network observer: %s failed", plane.label)

    def _poll_one(self, plane: Plane) -> None:
        address, status, iface = plane.resolve()
        down = status == "down"
        previous = self._bindings.get(plane.label)
        if previous is not None and previous.iface == iface and previous.address == address and previous.down == down:
            return

        if down:
            plane.suspend()
            logger.error(
                "%s: configured interface %s has no address; output stopped until it returns "
                "(it will not be sent on another interface).",
                plane.label,
                iface,
            )
        else:
            plane.apply(address)
            if previous is not None and previous.down:
                logger.info("%s: interface %s is back at %s; output resumed.", plane.label, iface, address or "auto")
            elif previous is not None:
                logger.info("%s: now bound to %s on %s.", plane.label, address or "auto", iface or "auto-detect")
        self._bindings[plane.label] = _Binding(iface=iface, address=address, down=down)

    def alerts(self) -> list[str]:
        """Operator-facing lines for every plane that is currently stopped.

        Rendered on the HUD, which is the only surface an operator has when the
        interface carrying the web UI is the one that went away.
        """
        return [
            f"{label}: {binding.iface or 'interface'} is down"
            for label, binding in sorted(self._bindings.items())
            if binding.down
        ]
