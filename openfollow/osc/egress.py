# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The address each OSC destination's pinned interface sends from.

Resolving an interface walks every adapter, far too slow for sends that run
every frame. The table is resolved when destinations are staged and kept
current by the network observer; a send only looks its interface up.

A destination at one of this station's own addresses is never pinned: the
kernel delivers it locally, and a socket bound to another interface's device
drops it without an error. The station's addresses are read at most once per
``_STATION_ADDRESSES_TTL_S``, so a send follows them without walking the
adapters each frame.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING

from openfollow.net_egress import Egress, is_loopback_host
from openfollow.net_utils import IPV4_RESOLVER, get_iface_ipv4, get_local_ipv4_addresses, plane_source_iface

if TYPE_CHECKING:
    from openfollow.configuration import OscDestinationConfig

_STATION_ADDRESSES_TTL_S = 1.0


def _ipv4_addresses_of(host: str) -> tuple[str, ...]:
    """What *host* resolves to for an OSC client, starting its lookup without waiting for it."""
    return IPV4_RESOLVER.lookup(host, 0.0).addresses


def reaches_station(
    host: str,
    station_addresses: Collection[str],
    *,
    addresses_of: Callable[[str], tuple[str, ...]] = _ipv4_addresses_of,
) -> bool:
    """Whether an OSC send to *host* goes to one of *station_addresses*, this station's own.

    The client sends to the first address a name resolves to; a name not
    resolved yet is not this station.
    """
    addresses = addresses_of(host.strip()) if host.strip() else ()
    return bool(addresses) and addresses[0] in station_addresses


@dataclass(frozen=True)
class _Snapshot:
    station: str = ""
    # Interface -> address; "" while it has none.
    addresses: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


class OscEgressTable:
    """Swapped whole on every write, so readers on other threads need no lock."""

    def __init__(
        self,
        *,
        station_addresses: Callable[[], Iterable[str]] = get_local_ipv4_addresses,
        addresses_of: Callable[[str], tuple[str, ...]] = _ipv4_addresses_of,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._snapshot = _Snapshot()
        self._write_lock = threading.Lock()
        self._read_station_addresses = station_addresses
        self._addresses_of = addresses_of
        self._clock = clock
        # (when read, addresses); swapped whole, so a reader needs no lock.
        self._station_addresses: tuple[float, frozenset[str]] | None = None

    def for_destination(self, dest: OscDestinationConfig) -> Egress | None:
        """Where *dest* must leave from; None lets the OS route it."""
        snapshot = self._snapshot
        iface = plane_source_iface(dest.source_iface, snapshot.station)
        if not iface or is_loopback_host(dest.host):
            return None
        if reaches_station(dest.host, self._current_station_addresses(), addresses_of=self._addresses_of):
            return None
        # An interface not staged yet reads as down: unpinned would be the leak.
        return Egress(iface, snapshot.addresses.get(iface, ""))

    def _current_station_addresses(self) -> frozenset[str]:
        now = self._clock()
        read = self._station_addresses
        if read is None or now - read[0] >= _STATION_ADDRESSES_TTL_S:
            read = (now, frozenset(self._read_station_addresses()))
            self._station_addresses = read
        return read[1]

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
