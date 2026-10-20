# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The OSC egress table: where each destination must leave from, looked up
without walking the adapters on the send path."""

from __future__ import annotations

from collections.abc import Callable, Iterable

import pytest

from openfollow.configuration import OscDestinationConfig
from openfollow.net_egress import Egress
from openfollow.osc.egress import OscEgressTable, reaches_station

pytestmark = pytest.mark.unit

_ADDRESSES = {"eth0": "192.0.2.10", "eth1": "198.51.100.10"}
# What the names in these tests resolve to; a literal resolves to itself.
_NAMES = {"console.example": ("198.51.100.20",), "station.example": ("192.0.2.10",)}


def _addresses_of(host: str) -> tuple[str, ...]:
    return _NAMES.get(host, (host,) if host[:1].isdigit() else ())


def _dest(host: str = "198.51.100.20", source_iface: str = "") -> OscDestinationConfig:
    return OscDestinationConfig(id=f"{host}-{source_iface}", host=host, source_iface=source_iface)


def _table(station_addresses: Callable[[], Iterable[str]] = lambda: (), clock: Callable[[], float] = lambda: 0.0):
    return OscEgressTable(station_addresses=station_addresses, addresses_of=_addresses_of, clock=clock)


def _staged(station: str, *dests: OscDestinationConfig, station_addresses: Iterable[str] = ()) -> OscEgressTable:
    table = _table(lambda: station_addresses)
    table.restage(station, dests, resolve=lambda iface: _ADDRESSES.get(iface, ""))
    return table


def test_an_unpinned_destination_on_an_unpinned_station_is_left_to_the_os() -> None:
    dest = _dest()
    assert _staged("", dest).for_destination(dest) is None


def test_a_pinned_destination_leaves_from_its_interface() -> None:
    dest = _dest(source_iface="eth1")
    assert _staged("eth0", dest).for_destination(dest) == Egress("eth1", "198.51.100.10")


def test_a_blank_pin_follows_the_station() -> None:
    dest = _dest()
    assert _staged("eth0", dest).for_destination(dest) == Egress("eth0", "192.0.2.10")


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost"])
def test_a_loopback_destination_is_never_pinned(host: str) -> None:
    """It never leaves the box, and binding it to a NIC could cut it off."""
    dest = _dest(host=host, source_iface="eth1")
    table = _staged("eth0", dest)
    assert table.for_destination(dest) is None
    assert table.live() == frozenset()


def test_an_interface_not_staged_yet_reads_as_down() -> None:
    """Unpinned would be the leak; a destination added since the last stage
    waits for the table to catch up."""
    table = _staged("")
    egress = table.for_destination(_dest(source_iface="eth1"))
    assert egress == Egress("eth1", "")
    assert egress is not None and egress.down


def test_a_pinned_interface_without_an_address_reads_as_down() -> None:
    dest = _dest(source_iface="eth9")
    egress = _staged("", dest).for_destination(dest)
    assert egress is not None and egress.down


def test_restage_resolves_each_interface_once_and_drops_the_unused() -> None:
    resolved: list[str] = []

    def _resolve(iface: str) -> str:
        resolved.append(iface)
        return _ADDRESSES.get(iface, "")

    table = _table()
    table.restage("eth0", [_dest(), _dest(host="198.51.100.21"), _dest(source_iface="eth1")], resolve=_resolve)
    assert sorted(resolved) == ["eth0", "eth1"]

    table.restage("eth0", [_dest()], resolve=_resolve)
    assert table.address_for("eth1") is None
    assert table.live() == frozenset({Egress("eth0", "192.0.2.10")})


def test_mark_follows_an_address_and_an_outage() -> None:
    dest = _dest(source_iface="eth1")
    table = _staged("", dest)

    table.mark("eth1", "198.51.100.99")
    assert table.for_destination(dest) == Egress("eth1", "198.51.100.99")
    assert table.address_for("eth1") == "198.51.100.99"

    table.mark("eth1", "")
    assert table.address_for("eth1") is None
    assert table.live() == frozenset()


@pytest.mark.parametrize(
    "host",
    ["192.0.2.10", "station.example"],
    ids=["another-interfaces-address", "name-for-it"],
)
def test_a_destination_at_one_of_the_stations_own_addresses_is_never_pinned(host: str) -> None:
    """A socket bound to eth1's device drops a datagram for eth0's own address
    without an error; unpinned, the kernel delivers it on this station."""
    dest = _dest(host=host, source_iface="eth1")
    assert _staged("eth0", dest, station_addresses={"127.0.0.1", "192.0.2.10"}).for_destination(dest) is None


def test_a_destination_at_the_pinned_interfaces_own_address_is_never_pinned() -> None:
    dest = _dest(host="198.51.100.10", source_iface="eth1")
    assert _staged("", dest, station_addresses={"198.51.100.10"}).for_destination(dest) is None


def test_a_destination_on_this_station_is_sent_while_its_pinned_interface_is_down() -> None:
    """Nothing leaves the box, so the outage of the pinned interface does not stop it."""
    dest = _dest(host="192.0.2.10", source_iface="eth9")
    assert _staged("", dest, station_addresses={"192.0.2.10"}).for_destination(dest) is None


@pytest.mark.parametrize(
    "host",
    ["console.example", "unresolved.example", "203.0.113.5"],
    ids=["name-for-another-host", "name-not-resolved-yet", "another-host"],
)
def test_a_destination_elsewhere_stays_pinned(host: str) -> None:
    """A name not resolved yet stays pinned: unpinned would be the leak."""
    dest = _dest(host=host, source_iface="eth1")
    table = _staged("eth0", dest, station_addresses={"192.0.2.10"})
    assert table.for_destination(dest) == Egress("eth1", "198.51.100.10")


def test_the_stations_addresses_are_read_at_most_once_a_second_and_followed() -> None:
    """Read on the send path, so not per send; an address the station gives up
    is pinned again once the read is due, rather than sent unpinned for good."""
    now = [0.0]
    owned = [{"192.0.2.10"}]
    reads: list[float] = []

    def _station_addresses() -> set[str]:
        reads.append(now[0])
        return owned[0]

    dest = _dest(host="192.0.2.10", source_iface="eth1")
    table = _table(_station_addresses, clock=lambda: now[0])
    table.restage("", [dest], resolve=lambda iface: _ADDRESSES.get(iface, ""))

    assert table.for_destination(dest) is None
    owned[0] = set()
    now[0] = 0.9
    assert table.for_destination(dest) is None
    now[0] = 1.0
    assert table.for_destination(dest) == Egress("eth1", "198.51.100.10")
    assert reads == [0.0, 1.0]


def test_reaches_station_goes_by_the_address_the_client_sends_to() -> None:
    """The client sends to a name's first address, so a later one on this station does not count."""
    assert reaches_station("multi.example", {"192.0.2.10"}, addresses_of=lambda _h: ("192.0.2.10", "203.0.113.5"))
    assert not reaches_station("multi.example", {"192.0.2.10"}, addresses_of=lambda _h: ("203.0.113.5", "192.0.2.10"))
    assert not reaches_station("", {"192.0.2.10"}, addresses_of=lambda _h: ("192.0.2.10",))
