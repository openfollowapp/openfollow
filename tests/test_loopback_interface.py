# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Michel Honold and Paul Hermann
"""Loopback as a network interface where the host offers it (macOS), and never elsewhere."""

from __future__ import annotations

import socket
from types import SimpleNamespace

import pytest

import openfollow.net_utils as net_utils
from openfollow.configuration import OscDestinationConfig
from openfollow.net_adapters import display_name
from openfollow.net_egress import LOOPBACK_ONLY, Egress, strands
from openfollow.osc.egress import OscEgressTable

pytestmark = pytest.mark.unit

ADDRS = {
    "en0": [SimpleNamespace(family=socket.AF_INET, address="192.0.2.10")],
    # A Mac's lo0 with the peer alias a 3D Mouse driver adds.
    "lo0": [
        SimpleNamespace(family=socket.AF_INET, address="127.51.68.120"),
        SimpleNamespace(family=socket.AF_INET, address="127.0.0.1"),
    ],
}


@pytest.fixture
def interfaces(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(net_utils.psutil, "net_if_addrs", lambda: ADDRS)


@pytest.fixture
def on_a_mac(monkeypatch: pytest.MonkeyPatch, interfaces: None) -> None:
    monkeypatch.setattr(net_utils, "LOOPBACK_OFFERED", True)


def test_on_a_mac_lo0_resolves_to_127_0_0_1_and_never_to_a_driver_alias(on_a_mac: None) -> None:
    assert net_utils.get_iface_ipv4("lo0") == "127.0.0.1"
    assert net_utils.get_iface_ipv4("en0") == "192.0.2.10"
    assert net_utils.list_iface_ipv4() == [("en0", "192.0.2.10"), ("lo0", "127.0.0.1")]
    assert net_utils.resolve_plane_source_ip("lo0") == ("127.0.0.1", "iface")


def test_elsewhere_loopback_stays_unpinnable(interfaces: None) -> None:
    assert net_utils.get_iface_ipv4("lo0") == ""
    assert net_utils.list_iface_ipv4() == [("en0", "192.0.2.10")]
    assert net_utils.resolve_plane_source_ip("lo0") == ("", "down")


def test_a_lo0_without_127_0_0_1_has_no_address(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(net_utils, "LOOPBACK_OFFERED", True)
    monkeypatch.setattr(net_utils.psutil, "net_if_addrs", lambda: {"lo0": [ADDRS["lo0"][0]]})
    assert net_utils.get_iface_ipv4("lo0") == ""


def test_automatic_never_picks_loopback_on_a_mac(on_a_mac: None, monkeypatch: pytest.MonkeyPatch) -> None:
    assert net_utils.get_iface_for_ip("127.0.0.1") == ""
    monkeypatch.setattr(net_utils, "get_primary_local_ipv4", lambda default="": "127.0.0.1")
    assert net_utils.resolve_iface_ip("") == ""
    assert net_utils.resolve_source_ip("") == ("", "none")


def test_a_loopback_pin_is_ready_at_startup(on_a_mac: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolved at once, not after the timeout whose fallback is also 127.0.0.1."""

    def _no_wait(_seconds: float) -> None:
        raise AssertionError("a loopback pin waited")

    monkeypatch.setattr(net_utils.time, "sleep", _no_wait)
    assert net_utils.wait_for_source_ip("lo0", timeout_s=5.0) == "127.0.0.1"


@pytest.mark.parametrize(
    ("offered", "labels", "shown"),
    [(True, {}, "Loopback (lo0)"), (True, {"lo0": "Desk"}, "Desk (lo0)"), (False, {}, "lo0")],
    ids=["mac", "own-label", "elsewhere"],
)
def test_loopback_is_named_where_it_is_offered(
    monkeypatch: pytest.MonkeyPatch, offered: bool, labels: dict[str, str], shown: str
) -> None:
    monkeypatch.setattr(net_utils, "LOOPBACK_OFFERED", offered)
    assert display_name("lo0", labels) == shown


@pytest.mark.parametrize(
    ("iface", "host", "stranded"),
    [
        ("lo0", "192.0.2.50", True),
        ("lo0", "desk.example.com", True),
        ("lo0", "127.0.0.1", False),
        ("lo0", "localhost", False),
        ("lo0", "", False),
        ("en0", "192.0.2.50", False),
    ],
    ids=["lan-address", "name", "loopback", "localhost", "no-host", "lan-interface"],
)
def test_loopback_strands_only_a_destination_off_this_mac(
    monkeypatch: pytest.MonkeyPatch, iface: str, host: str, stranded: bool
) -> None:
    monkeypatch.setattr(net_utils, "LOOPBACK_OFFERED", True)
    egress = strands(iface, host)
    assert (egress == Egress(iface, "", refused=LOOPBACK_ONLY)) if stranded else egress is None


def test_without_the_offer_nothing_is_stranded() -> None:
    assert strands("lo0", "192.0.2.50") is None


def test_a_refused_egress_says_why_and_a_down_one_names_its_interface() -> None:
    assert Egress("lo0", "", refused=LOOPBACK_ONLY).reason("Loopback (lo0)") == LOOPBACK_ONLY
    assert Egress("en1", "").reason("Desk (en1)") == "interface Desk (en1) is down"


@pytest.mark.parametrize(
    ("host", "expected"),
    [("192.0.2.50", Egress("lo0", "", refused=LOOPBACK_ONLY)), ("127.0.0.1", None)],
    ids=["off-this-mac", "loopback-host"],
)
def test_an_osc_destination_on_loopback_reaches_only_this_mac(
    on_a_mac: None, host: str, expected: Egress | None
) -> None:
    table = OscEgressTable(station_addresses=lambda: frozenset({"192.0.2.10"}), addresses_of=lambda _h: ())
    dest = OscDestinationConfig(host=host, source_iface="lo0")
    table.restage("", [dest])
    assert table.for_destination(dest) == expected
