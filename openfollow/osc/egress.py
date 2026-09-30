# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The address each OSC destination's pinned interface sends from.

Resolving an interface walks every adapter, far too slow for sends that run
every frame. The table is resolved when destinations are staged and kept
current by the network observer; a send only looks its interface up.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING

from openfollow.net_egress import Egress, is_loopback_host
from openfollow.net_utils import get_iface_ipv4, plane_source_iface

if TYPE_CHECKING:
    from openfollow.configuration import OscDestinationConfig


@dataclass(frozen=True)
class _Snapshot:
    station: str = ""
    # Interface -> address; "" while it has none.
    addresses: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


class OscEgressTable:
    """Swapped whole on every write, so readers on other threads need no lock."""

    def __init__(self) -> None:
        self._snapshot = _Snapshot()
        self._write_lock = threading.Lock()

    def for_destination(self, dest: OscDestinationConfig) -> Egress | None:
        """Where *dest* must leave from; None lets the OS route it."""
        snapshot = self._snapshot
        iface = plane_source_iface(dest.source_iface, snapshot.station)
        if not iface or is_loopback_host(dest.host):
            return None
        # An interface not staged yet reads as down: unpinned would be the leak.
        return Egress(iface, snapshot.addresses.get(iface, ""))

    def address_for(self, iface: str) -> str | None:
        """The address *iface* sends from, or None while it has none."""
        return self._snapshot.addresses.get(iface) or None

    def live(self) -> frozenset[Egress]:
        """Every staged interface that currently has an address."""
        return frozenset(Egress(iface, address) for iface, address in self._snapshot.addresses.items() if address)

    def restage(
        self,
        station_iface: str,
        destinations: Iterable[OscDestinationConfig],
        *,
        resolve: Callable[[str], str] = get_iface_ipv4,
    ) -> None:
        """Resolve every interface *destinations* send from. Config time only."""
        wanted = {
            plane_source_iface(dest.source_iface, station_iface)
            for dest in destinations
            if not is_loopback_host(dest.host)
        }
        addresses = {iface: resolve(iface) for iface in sorted(wanted) if iface}
        with self._write_lock:
            self._snapshot = _Snapshot(station_iface, MappingProxyType(addresses))

    def mark(self, iface: str, address: str) -> None:
        """Record *iface*'s current address; "" while it has none."""
        with self._write_lock:
            snapshot = self._snapshot
            self._snapshot = _Snapshot(snapshot.station, MappingProxyType({**snapshot.addresses, iface: address}))
