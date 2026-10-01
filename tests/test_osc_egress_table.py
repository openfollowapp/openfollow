# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The OSC egress table: where each destination must leave from, looked up
without walking the adapters on the send path."""

from __future__ import annotations

import pytest

from openfollow.configuration import OscDestinationConfig
from openfollow.net_egress import Egress
from openfollow.osc.egress import OscEgressTable

pytestmark = pytest.mark.unit

_ADDRESSES = {"eth0": "192.0.2.10", "eth1": "198.51.100.10"}


def _dest(host: str = "198.51.100.20", source_iface: str = "") -> OscDestinationConfig:
    return OscDestinationConfig(id=f"{host}-{source_iface}", host=host, source_iface=source_iface)


def _staged(station: str, *dests: OscDestinationConfig) -> OscEgressTable:
    table = OscEgressTable()
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

    table = OscEgressTable()
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
