# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Unit tests for openfollow.net_utils IPv4 enumeration and source-IP resolution."""

from __future__ import annotations

import ipaddress
import socket
import sys
import threading
from types import SimpleNamespace
from typing import Any

import pytest

import openfollow.net_utils as net_utils_module
from openfollow.net_utils import (
    BoundedResolver,
    HostLookup,
    Ipv4Route,
    get_iface_for_ip,
    get_iface_ipv4,
    get_local_ipv4_addresses,
    get_primary_local_ipv4,
    list_iface_ipv4,
    read_ipv4_routes,
    resolve_iface_ip,
    resolve_plane_source_ip,
    resolve_source_ip,
    resolve_web_bind,
    route_source,
)

pytestmark = pytest.mark.unit


def _fake_addrs(spec: dict[str, list[tuple[int, str]]]) -> dict[str, list[SimpleNamespace]]:
    """Build a psutil-shaped ``net_if_addrs`` mapping from a terse spec."""
    return {
        iface: [SimpleNamespace(family=fam, address=addr) for fam, addr in entries] for iface, entries in spec.items()
    }


class TestGetLocalIpv4Addresses:
    def test_returns_ipv4_addresses(self, monkeypatch) -> None:
        fake_addrs = {
            "en0": [
                SimpleNamespace(family=socket.AF_INET, address="192.168.1.10"),
                SimpleNamespace(family=socket.AF_INET6, address="fe80::1"),
            ],
            "lo0": [
                SimpleNamespace(family=socket.AF_INET, address="127.0.0.1"),
            ],
        }
        monkeypatch.setattr(net_utils_module.psutil, "net_if_addrs", lambda: fake_addrs)
        result = get_local_ipv4_addresses()
        assert result == {"192.168.1.10", "127.0.0.1"}

    def test_excludes_ipv6(self, monkeypatch) -> None:
        fake_addrs = {
            "en0": [
                SimpleNamespace(family=socket.AF_INET6, address="fe80::1"),
            ],
        }
        monkeypatch.setattr(net_utils_module.psutil, "net_if_addrs", lambda: fake_addrs)
        result = get_local_ipv4_addresses()
        assert result == set()

    def test_empty_interfaces(self, monkeypatch) -> None:
        monkeypatch.setattr(net_utils_module.psutil, "net_if_addrs", lambda: {})
        result = get_local_ipv4_addresses()
        assert result == set()


class TestGetPrimaryLocalIpv4:
    def test_returns_socket_based_ip(self, monkeypatch) -> None:
        class FakeSocket:
            def __init__(self, *a, **kw):
                pass

            def connect(self, addr):
                pass

            def getsockname(self):
                return ("10.0.0.5", 0)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        monkeypatch.setattr(net_utils_module.socket, "socket", FakeSocket)
        assert get_primary_local_ipv4() == "10.0.0.5"

    def test_falls_back_to_interface_scan(self, monkeypatch) -> None:
        class FakeSocket:
            def __init__(self, *a, **kw):
                pass

            def connect(self, addr):
                raise OSError("No route")

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        monkeypatch.setattr(net_utils_module.socket, "socket", FakeSocket)
        monkeypatch.setattr(
            net_utils_module,
            "get_local_ipv4_addresses",
            lambda: {"127.0.0.1", "192.168.1.50"},
        )
        result = get_primary_local_ipv4()
        assert result == "192.168.1.50"

    def test_returns_default_when_nothing_available(self, monkeypatch) -> None:
        class FakeSocket:
            def __init__(self, *a, **kw):
                pass

            def connect(self, addr):
                raise OSError("No route")

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        monkeypatch.setattr(net_utils_module.socket, "socket", FakeSocket)
        monkeypatch.setattr(
            net_utils_module,
            "get_local_ipv4_addresses",
            lambda: {"127.0.0.1"},
        )
        assert get_primary_local_ipv4() == "N/A"

    def test_custom_default(self, monkeypatch) -> None:
        class FakeSocket:
            def __init__(self, *a, **kw):
                pass

            def connect(self, addr):
                raise OSError("No route")

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        monkeypatch.setattr(net_utils_module.socket, "socket", FakeSocket)
        monkeypatch.setattr(
            net_utils_module,
            "get_local_ipv4_addresses",
            lambda: set(),
        )
        assert get_primary_local_ipv4(default="0.0.0.0") == "0.0.0.0"

    def test_skips_loopback_from_socket(self, monkeypatch) -> None:
        class FakeSocket:
            def __init__(self, *a, **kw):
                pass

            def connect(self, addr):
                pass

            def getsockname(self):
                return ("127.0.0.1", 0)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        monkeypatch.setattr(net_utils_module.socket, "socket", FakeSocket)
        monkeypatch.setattr(
            net_utils_module,
            "get_local_ipv4_addresses",
            lambda: {"10.0.0.1"},
        )
        assert get_primary_local_ipv4() == "10.0.0.1"


class TestResolveIfaceIp:
    """``resolve_iface_ip`` powers the "Auto" option in OTP's IP-
    keyed source dropdown – without it, ``multicast_expert``
    silently drops OTP data on hosts where the OS default route
    doesn't match the LAN the console expects. PSN's iface pin
    goes through :func:`resolve_source_ip` instead."""

    def test_returns_configured_when_non_empty(self, monkeypatch) -> None:
        """Operator's explicit choice always wins, regardless of what
        the primary-IP heuristic returns."""
        monkeypatch.setattr(
            net_utils_module,
            "get_primary_local_ipv4",
            lambda default="N/A": "10.0.0.99",
        )
        assert resolve_iface_ip("192.168.1.50") == "192.168.1.50"

    def test_falls_back_to_primary_when_empty(self, monkeypatch) -> None:
        """The "Auto" picker option stores ``""`` – resolve to the
        OS's primary outbound IPv4 so multicast TX pins to a real
        interface."""
        monkeypatch.setattr(
            net_utils_module,
            "get_primary_local_ipv4",
            lambda default="N/A": "10.0.0.5",
        )
        assert resolve_iface_ip("") == "10.0.0.5"

    def test_returns_empty_on_offline_host(self, monkeypatch) -> None:
        """When ``get_primary_local_ipv4`` returns its default (the
        offline-host case) ``resolve_iface_ip`` must return empty so
        callers can differentiate "no interface" from a real
        address – otherwise we'd bind to ``"N/A"`` as a literal."""
        monkeypatch.setattr(
            net_utils_module,
            "get_primary_local_ipv4",
            lambda default="N/A": default,
        )
        assert resolve_iface_ip("") == ""

    def test_treats_loopback_as_offline(self, monkeypatch) -> None:
        """A ``127.x`` primary IP means no real network – resolving
        Auto to localhost would still drop PSN traffic. Return
        empty so the caller can pass through to the OS default
        rather than pinning to loopback."""
        monkeypatch.setattr(
            net_utils_module,
            "get_primary_local_ipv4",
            lambda default="N/A": "127.0.0.1",
        )
        assert resolve_iface_ip("") == ""


# Pin the interface name (eth0/wlan0) instead of raw IP so touring devices work across networks.
# Wait-loop and polling timing are exercised under ``TestWaitForSourceIp`` below.


class TestGetIfaceIpv4:
    def test_returns_first_non_loopback_ipv4(self, monkeypatch) -> None:
        monkeypatch.setattr(
            net_utils_module.psutil,
            "net_if_addrs",
            lambda: _fake_addrs(
                {
                    "eth0": [
                        (socket.AF_INET6, "fe80::1"),
                        (socket.AF_INET, "192.168.1.50"),
                    ],
                }
            ),
        )
        assert get_iface_ipv4("eth0") == "192.168.1.50"

    def test_returns_empty_for_unknown_iface(self, monkeypatch) -> None:
        monkeypatch.setattr(
            net_utils_module.psutil,
            "net_if_addrs",
            lambda: _fake_addrs({"eth0": [(socket.AF_INET, "10.0.0.1")]}),
        )
        assert get_iface_ipv4("wlan0") == ""

    def test_returns_empty_for_blank_name(self) -> None:
        # Defensive: ``""`` is the auto-detect sentinel; the resolver
        # passes it straight in and would otherwise hit the psutil
        # path returning whatever the bare key lookup did.
        assert get_iface_ipv4("") == ""

    def test_skips_loopback_only_iface(self, monkeypatch) -> None:
        """The loopback interface has only ``127.x``, which is never a
        useful PSN bind target. Treat the same as missing."""
        monkeypatch.setattr(
            net_utils_module.psutil,
            "net_if_addrs",
            lambda: _fake_addrs({"lo": [(socket.AF_INET, "127.0.0.1")]}),
        )
        assert get_iface_ipv4("lo") == ""


class TestGetIfaceForIp:
    """Reverse lookup: given an IPv4 currently bound to some NIC,
    return the iface name. Used by HUD / Settings menu to render
    ``192.168.178.61 (eth0)`` so the operator can tell at a glance
    which NIC is carrying their traffic on a multi-homed host."""

    def test_finds_matching_iface(self, monkeypatch) -> None:
        monkeypatch.setattr(
            net_utils_module.psutil,
            "net_if_addrs",
            lambda: _fake_addrs(
                {
                    "eth0": [(socket.AF_INET, "192.168.178.59")],
                    "wlan0": [(socket.AF_INET, "10.0.0.5")],
                }
            ),
        )
        assert get_iface_for_ip("10.0.0.5") == "wlan0"

    def test_returns_empty_for_unmatched_ip(self, monkeypatch) -> None:
        """A stale IP that doesn't currently exist on this host
        returns empty – the HUD just falls back to displaying the
        bare IP without an iface suffix."""
        monkeypatch.setattr(
            net_utils_module.psutil,
            "net_if_addrs",
            lambda: _fake_addrs({"eth0": [(socket.AF_INET, "192.168.178.59")]}),
        )
        assert get_iface_for_ip("192.168.80.101") == ""

    def test_rejects_loopback(self, monkeypatch) -> None:
        """A loopback IP is never decorated with an iface suffix:
        loopback never represents a meaningful NIC choice."""
        monkeypatch.setattr(
            net_utils_module.psutil,
            "net_if_addrs",
            lambda: _fake_addrs({"lo": [(socket.AF_INET, "127.0.0.1")]}),
        )
        assert get_iface_for_ip("127.0.0.1") == ""

    def test_returns_empty_for_blank(self) -> None:
        assert get_iface_for_ip("") == ""

    @pytest.mark.parametrize(
        ("ip", "expected"),
        [("2001:db8:1::10", "eth1"), ("fe80::1", "eth1"), ("::1", "")],
        ids=["global", "link-local-with-scope", "loopback"],
    )
    def test_finds_an_ipv6_address(self, monkeypatch, ip: str, expected: str) -> None:
        """The route source towards an IPv6 camera is an IPv6 address; its adapter is named too."""
        monkeypatch.setattr(
            net_utils_module.psutil,
            "net_if_addrs",
            lambda: _fake_addrs(
                {
                    "eth0": [(socket.AF_INET, "192.168.178.59")],
                    "eth1": [(socket.AF_INET6, "2001:db8:1::10"), (socket.AF_INET6, "fe80::1%eth1")],
                    "lo": [(socket.AF_INET6, "::1")],
                }
            ),
        )
        assert get_iface_for_ip(ip) == expected


class TestListIfaceIpv4:
    def test_returns_iface_ip_pairs_sorted(self, monkeypatch) -> None:
        monkeypatch.setattr(
            net_utils_module.psutil,
            "net_if_addrs",
            lambda: _fake_addrs(
                {
                    "wlan0": [(socket.AF_INET, "10.0.0.5")],
                    "eth0": [(socket.AF_INET, "192.168.1.50")],
                }
            ),
        )
        # Sorted by iface name so the dropdown order is stable across
        # reloads – psutil's dict order is not guaranteed to be.
        assert list_iface_ipv4() == [
            ("eth0", "192.168.1.50"),
            ("wlan0", "10.0.0.5"),
        ]

    def test_excludes_loopback(self, monkeypatch) -> None:
        monkeypatch.setattr(
            net_utils_module.psutil,
            "net_if_addrs",
            lambda: _fake_addrs(
                {
                    "lo": [(socket.AF_INET, "127.0.0.1")],
                    "eth0": [(socket.AF_INET, "10.0.0.5")],
                }
            ),
        )
        assert list_iface_ipv4() == [("eth0", "10.0.0.5")]

    def test_one_entry_per_iface(self, monkeypatch) -> None:
        monkeypatch.setattr(
            net_utils_module.psutil,
            "net_if_addrs",
            lambda: _fake_addrs(
                {
                    "eth0": [
                        (socket.AF_INET, "192.168.1.50"),
                        (socket.AF_INET, "192.168.1.51"),
                    ],
                }
            ),
        )
        assert list_iface_ipv4() == [("eth0", "192.168.1.50")]

    def test_skips_ipv6_addresses(self, monkeypatch) -> None:
        monkeypatch.setattr(
            net_utils_module.psutil,
            "net_if_addrs",
            lambda: _fake_addrs(
                {
                    "eth0": [
                        (socket.AF_INET6, "fe80::1"),
                        (socket.AF_INET, "192.168.1.50"),
                    ],
                }
            ),
        )
        assert list_iface_ipv4() == [("eth0", "192.168.1.50")]


class TestResolveSourceIp:
    """``resolve_source_ip(iface, *, fallback=True)`` is the single
    resolution point: PSN server, receiver, marker-catalog sync and the
    startup wait all route through it so they agree on the same IP."""

    def test_iface_pin_resolves_to_current_ipv4(self, monkeypatch) -> None:
        """A live iface name resolves to its current non-loopback
        IPv4 – the stable pin."""
        monkeypatch.setattr(
            net_utils_module.psutil,
            "net_if_addrs",
            lambda: _fake_addrs(
                {
                    "eth0": [(socket.AF_INET, "192.168.178.59")],
                    "wlan0": [(socket.AF_INET, "10.0.0.5")],
                }
            ),
        )
        assert resolve_source_ip("eth0") == ("192.168.178.59", "iface")

    def test_falls_through_when_iface_down(self, monkeypatch) -> None:
        """Pinned iface absent / down → fall through to primary.
        Without this an iface that disappears (USB Ethernet unplugged,
        cellular modem suspended) would fail closed and disable PSN."""
        monkeypatch.setattr(
            net_utils_module.psutil,
            "net_if_addrs",
            lambda: _fake_addrs({"eth0": [(socket.AF_INET, "192.168.1.50")]}),
        )
        monkeypatch.setattr(
            net_utils_module,
            "get_primary_local_ipv4",
            lambda default="": "10.0.0.1",
        )
        assert resolve_source_ip("wlan0") == ("10.0.0.1", "primary")

    def test_empty_iface_returns_primary(self, monkeypatch) -> None:
        """Auto-detect: empty iface → primary outbound IPv4. The
        common default-config case."""
        monkeypatch.setattr(
            net_utils_module.psutil,
            "net_if_addrs",
            lambda: _fake_addrs({}),
        )
        monkeypatch.setattr(
            net_utils_module,
            "get_primary_local_ipv4",
            lambda default="": "192.168.1.50",
        )
        assert resolve_source_ip("") == ("192.168.1.50", "primary")

    def test_no_fallback_when_disabled(self, monkeypatch) -> None:
        """``fallback=False`` is for the startup wait loop: don't
        latch onto an arbitrary primary while we're still polling
        for the pinned target."""
        monkeypatch.setattr(
            net_utils_module.psutil,
            "net_if_addrs",
            lambda: _fake_addrs({}),
        )
        monkeypatch.setattr(
            net_utils_module,
            "get_primary_local_ipv4",
            lambda default="": "10.0.0.1",
        )
        assert resolve_source_ip("eth0", fallback=False) == ("", "none")

    def test_offline_host(self, monkeypatch) -> None:
        """No usable IP anywhere → ``("", "none")`` so callers can
        differentiate from a real address rather than binding to
        ``"N/A"`` as a literal string."""
        monkeypatch.setattr(
            net_utils_module.psutil,
            "net_if_addrs",
            lambda: _fake_addrs({}),
        )
        monkeypatch.setattr(
            net_utils_module,
            "get_primary_local_ipv4",
            lambda default="": "",
        )
        assert resolve_source_ip("") == ("", "none")


class TestResolvePlaneSourceIp:
    """``resolve_plane_source_ip(pin, station_iface)`` resolves one network
    plane's *configured* interface. Inheritance happens for an unset value
    only – a configured interface that is down reports ``down`` and the plane
    binds nothing rather than moving to a different network."""

    @staticmethod
    def _ifaces(monkeypatch, spec: dict[str, list[tuple[int, str]]]) -> None:
        monkeypatch.setattr(
            net_utils_module.psutil,
            "net_if_addrs",
            lambda: _fake_addrs(spec),
        )

    def test_plane_pin_wins_over_station(self, monkeypatch) -> None:
        """A live plane pin is honoured even when the station pins a
        different, equally live interface – that's the whole point of
        splitting a plane onto its own network."""
        self._ifaces(
            monkeypatch,
            {
                "eth0": [(socket.AF_INET, "192.168.1.5")],
                "eth1": [(socket.AF_INET, "10.0.0.9")],
            },
        )
        assert resolve_plane_source_ip("eth1", "eth0") == ("10.0.0.9", "iface")

    def test_blank_pin_follows_station(self, monkeypatch) -> None:
        """Blank pin = "follow station interface" – the default for every
        plane, and what keeps a single-NIC station behaving as before."""
        self._ifaces(
            monkeypatch,
            {
                "eth0": [(socket.AF_INET, "192.168.1.5")],
                "eth1": [(socket.AF_INET, "10.0.0.9")],
            },
        )
        assert resolve_plane_source_ip("", "eth0") == ("192.168.1.5", "station")

    def test_down_plane_pin_does_not_move_to_the_station_interface(self, monkeypatch) -> None:
        """The load-bearing rule: a pinned plane whose interface is gone stops.
        Rebinding it to the station interface would put show data on whatever
        network that happens to be – silently, mid-show."""
        self._ifaces(monkeypatch, {"eth0": [(socket.AF_INET, "192.168.1.5")]})
        assert resolve_plane_source_ip("eth9", "eth0") == ("", "down")

    def test_down_station_interface_does_not_auto_detect(self, monkeypatch) -> None:
        """Same rule one level up: an explicitly configured station interface
        that is down must not silently become the OS primary."""
        self._ifaces(monkeypatch, {"eth0": [(socket.AF_INET, "192.168.1.5")]})
        monkeypatch.setattr(
            net_utils_module,
            "get_primary_local_ipv4",
            lambda default="": "172.16.4.20",
        )
        assert resolve_plane_source_ip("", "eth8") == ("", "down")

    def test_a_new_address_on_the_configured_interface_is_followed(self, monkeypatch) -> None:
        """Only the interface is fixed. A reconnect that yields a different
        DHCP lease on the same NIC must resolve to the new address."""
        self._ifaces(monkeypatch, {"eth0": [(socket.AF_INET, "192.168.1.5")]})
        assert resolve_plane_source_ip("eth0", "") == ("192.168.1.5", "iface")
        self._ifaces(monkeypatch, {"eth0": [(socket.AF_INET, "192.168.1.77")]})
        assert resolve_plane_source_ip("eth0", "") == ("192.168.1.77", "iface")

    def test_no_pins_at_all_is_auto_detect(self, monkeypatch) -> None:
        """Nothing configured anywhere is not an error – auto-detect is the
        chosen behaviour, so it still resolves."""
        self._ifaces(monkeypatch, {})
        monkeypatch.setattr(
            net_utils_module,
            "get_primary_local_ipv4",
            lambda default="": "192.168.1.50",
        )
        assert resolve_plane_source_ip("", "") == ("192.168.1.50", "primary")

    def test_offline_with_nothing_configured_reports_none(self, monkeypatch) -> None:
        """Distinct from ``down``: nothing was configured, so nothing is
        broken – there is simply no address to auto-detect yet."""
        self._ifaces(monkeypatch, {})
        monkeypatch.setattr(
            net_utils_module,
            "get_primary_local_ipv4",
            lambda default="": "",
        )
        assert resolve_plane_source_ip("", "") == ("", "none")

    def test_loopback_only_primary_is_rejected(self, monkeypatch) -> None:
        """A loopback primary is not a usable bind for LAN multicast, so it
        must report "none" rather than pinning traffic to 127.x."""
        self._ifaces(monkeypatch, {})
        monkeypatch.setattr(
            net_utils_module,
            "get_primary_local_ipv4",
            lambda default="": "127.0.0.1",
        )
        assert resolve_plane_source_ip("", "") == ("", "none")

    def test_configured_interface_without_ipv4_is_down(self, monkeypatch) -> None:
        """An interface holding only IPv6 / loopback has no usable IPv4 bind.
        It is configured, so it is ``down`` – not a reason to auto-detect."""
        self._ifaces(
            monkeypatch,
            {
                "eth0": [(socket.AF_INET6, "fe80::1"), (socket.AF_INET, "127.0.0.1")],
            },
        )
        monkeypatch.setattr(
            net_utils_module,
            "get_primary_local_ipv4",
            lambda default="": "10.1.1.4",
        )
        assert resolve_plane_source_ip("", "eth0") == ("", "down")


class TestResolveMulticastIface:
    """``resolve_multicast_iface(pin, station_iface)`` resolves the interface a
    receiver takes its multicast membership on - never a bind address. It
    follows the same pin/station inheritance as the sending planes and fails
    closed the same way, and differs in one arm: nothing configured leaves the
    choice to the routing table rather than naming the auto-detected primary."""

    @staticmethod
    def _ifaces(monkeypatch, spec: dict[str, list[tuple[int, str]]]) -> None:
        monkeypatch.setattr(
            net_utils_module.psutil,
            "net_if_addrs",
            lambda: _fake_addrs(spec),
        )

    def test_nothing_configured_leaves_the_interface_unpinned(self, monkeypatch) -> None:
        """The arm that separates this from ``resolve_plane_source_ip``. A
        sender with no pin has to choose one address; a membership does not
        have to be pinned at all, and naming the auto-detected primary here
        would pin one the operator never asked for."""
        self._ifaces(monkeypatch, {"eth0": [(socket.AF_INET, "192.168.1.5")]})
        monkeypatch.setattr(
            net_utils_module,
            "get_primary_local_ipv4",
            lambda default="": "192.168.1.5",
        )
        assert net_utils_module.resolve_multicast_iface("", "") == ("", "none")

    def test_pin_wins_over_station(self, monkeypatch) -> None:
        self._ifaces(
            monkeypatch,
            {
                "eth0": [(socket.AF_INET, "192.168.1.5")],
                "eth1": [(socket.AF_INET, "10.0.0.9")],
            },
        )
        assert net_utils_module.resolve_multicast_iface("eth1", "eth0") == ("10.0.0.9", "iface")

    def test_blank_pin_follows_the_station(self, monkeypatch) -> None:
        """The chosen default: an operator who put this station on one network
        gets its receiver there too, without a second setting to find."""
        self._ifaces(
            monkeypatch,
            {
                "eth0": [(socket.AF_INET, "192.168.1.5")],
                "eth1": [(socket.AF_INET, "10.0.0.9")],
            },
        )
        assert net_utils_module.resolve_multicast_iface("", "eth0") == ("192.168.1.5", "station")

    @pytest.mark.parametrize(
        ("pin", "station"),
        [
            ("eth9", "eth0"),
            ("", "eth9"),
        ],
    )
    def test_a_configured_interface_with_no_address_is_down(self, monkeypatch, pin: str, station: str) -> None:
        """Fails closed, through either route into the pin. Falling through to
        the routing table's choice would subscribe on a network the pin exists
        to keep the station off."""
        self._ifaces(monkeypatch, {"eth0": [(socket.AF_INET, "192.168.1.5")]})
        monkeypatch.setattr(
            net_utils_module,
            "get_primary_local_ipv4",
            lambda default="": "172.16.4.20",
        )
        assert net_utils_module.resolve_multicast_iface(pin, station) == ("", "down")

    def test_a_new_address_on_the_configured_interface_is_followed(self, monkeypatch) -> None:
        """Only the interface is fixed; a fresh DHCP lease on it is not a
        reason to drop the membership."""
        self._ifaces(monkeypatch, {"eth0": [(socket.AF_INET, "192.168.1.5")]})
        assert net_utils_module.resolve_multicast_iface("eth0", "") == ("192.168.1.5", "iface")
        self._ifaces(monkeypatch, {"eth0": [(socket.AF_INET, "192.168.1.77")]})
        assert net_utils_module.resolve_multicast_iface("eth0", "") == ("192.168.1.77", "iface")


class TestPlaneSourceIface:
    def test_pin_wins(self) -> None:
        assert net_utils_module.plane_source_iface("eth1", "eth0") == "eth1"

    def test_blank_pin_inherits_the_station(self) -> None:
        assert net_utils_module.plane_source_iface("", "eth0") == "eth0"

    def test_nothing_configured_is_empty(self) -> None:
        assert net_utils_module.plane_source_iface("", "") == ""


class TestWaitForSourceIp:
    """The startup wait now understands the iface-pin model – prefer
    the pin (iface or explicit ip) while polling, fall back to the
    primary only on timeout so the box doesn't stall forever."""

    def test_returns_pinned_iface_ip_immediately(self, monkeypatch) -> None:
        monkeypatch.setattr(
            net_utils_module.psutil,
            "net_if_addrs",
            lambda: _fake_addrs({"eth0": [(socket.AF_INET, "192.168.1.50")]}),
        )
        monkeypatch.setattr(net_utils_module.time, "sleep", lambda s: None)
        assert (
            net_utils_module.wait_for_source_ip(
                iface="eth0",
                timeout_s=5,
                interval_s=1,
            )
            == "192.168.1.50"
        )

    def test_empty_iface_returns_primary_immediately(self, monkeypatch) -> None:
        """Default auto-detect config: empty pin → primary IP wins
        on the first poll, no waiting needed."""
        monkeypatch.setattr(
            net_utils_module.psutil,
            "net_if_addrs",
            lambda: _fake_addrs({}),
        )
        monkeypatch.setattr(
            net_utils_module,
            "get_primary_local_ipv4",
            lambda default="": "10.0.0.5",
        )
        slept: list[float] = []
        monkeypatch.setattr(net_utils_module.time, "sleep", lambda s: slept.append(s))
        assert (
            net_utils_module.wait_for_source_ip(
                timeout_s=5,
                interval_s=1,
            )
            == "10.0.0.5"
        )
        assert slept == []

    def test_times_out_to_primary(self, monkeypatch) -> None:
        """Pinned target never shows up, primary is available →
        return the primary so the app proceeds (degraded)."""
        monkeypatch.setattr(
            net_utils_module.psutil,
            "net_if_addrs",
            lambda: _fake_addrs({}),
        )
        monkeypatch.setattr(
            net_utils_module,
            "get_local_ipv4_addresses",
            lambda: set(),
        )
        monkeypatch.setattr(
            net_utils_module,
            "get_primary_local_ipv4",
            lambda default="": "10.0.0.99",
        )
        monkeypatch.setattr(net_utils_module.time, "sleep", lambda s: None)
        clock = iter([1000.0, 1100.0])  # deadline already past on first remaining check
        monkeypatch.setattr(net_utils_module.time, "monotonic", lambda: next(clock))
        assert (
            net_utils_module.wait_for_source_ip(
                iface="eth0",
                timeout_s=30,
                interval_s=1,
            )
            == "10.0.0.99"
        )

    def test_polls_until_iface_appears(self, monkeypatch) -> None:
        """Pinned iface not live yet → poll, sleep, retry. Once the
        iface materialises (e.g. cable plugged in, modem suspend
        ended) the wait returns its current IPv4. Exercises the
        poll-sleep branch the immediate-return paths skip."""
        # First call to net_if_addrs: iface missing. Second call: it's up.
        responses = iter(
            [
                _fake_addrs({}),
                _fake_addrs({"eth0": [(socket.AF_INET, "10.0.0.7")]}),
            ]
        )
        monkeypatch.setattr(
            net_utils_module.psutil,
            "net_if_addrs",
            lambda: next(responses),
        )
        slept: list[float] = []
        monkeypatch.setattr(net_utils_module.time, "sleep", lambda s: slept.append(s))
        # Clock advances less than timeout so we don't time out;
        # remaining stays positive so the sleep branch fires.
        clock = iter([1000.0, 1001.0])
        monkeypatch.setattr(net_utils_module.time, "monotonic", lambda: next(clock))
        assert (
            net_utils_module.wait_for_source_ip(
                iface="eth0",
                timeout_s=30,
                interval_s=0.5,
            )
            == "10.0.0.7"
        )
        assert slept == [0.5]  # waited once between polls

    def test_times_out_to_loopback_when_no_network(self, monkeypatch) -> None:
        """No pin live AND no primary → loopback signals "no network"
        to the caller (matches the prior behaviour for the offline-
        host case)."""
        monkeypatch.setattr(
            net_utils_module.psutil,
            "net_if_addrs",
            lambda: _fake_addrs({}),
        )
        monkeypatch.setattr(
            net_utils_module,
            "get_local_ipv4_addresses",
            lambda: {"127.0.0.1"},
        )
        monkeypatch.setattr(
            net_utils_module,
            "get_primary_local_ipv4",
            lambda default="": "",
        )
        monkeypatch.setattr(net_utils_module.time, "sleep", lambda s: None)
        clock = iter([1000.0, 1100.0])
        monkeypatch.setattr(net_utils_module.time, "monotonic", lambda: next(clock))
        assert (
            net_utils_module.wait_for_source_ip(
                iface="eth0",
                timeout_s=30,
                interval_s=1,
            )
            == "127.0.0.1"
        )


class TestPrimaryAddressPrefersARealLease:
    """A link-local address must never outrank a routable one.

    The offline probe always fails on a show LAN, so this branch picks the
    station's identity - the address peers and consoles reach it at. Ordering
    the raw strings puts ``169.254.x`` ahead of ``192.168.x`` (``"16"`` sorts
    before ``"19"``), so a station that self-assigned while DHCP was failing
    kept advertising that address after a real lease arrived.
    """

    @staticmethod
    def _offline(monkeypatch, addresses: set[str]) -> None:
        class FakeSocket:
            def __init__(self, *a, **kw) -> None:
                pass

            def connect(self, addr) -> None:
                raise OSError("No route")

            def __enter__(self):
                return self

            def __exit__(self, *a) -> None:
                pass

        monkeypatch.setattr(net_utils_module.socket, "socket", FakeSocket)
        monkeypatch.setattr(net_utils_module, "get_local_ipv4_addresses", lambda: addresses)

    def test_real_lease_beats_link_local(self, monkeypatch) -> None:
        self._offline(monkeypatch, {"169.254.8.31", "192.168.178.66"})
        assert get_primary_local_ipv4() == "192.168.178.66"

    def test_link_local_still_used_when_it_is_all_there_is(self, monkeypatch) -> None:
        """The DHCP-failure fallback is a working address on its own segment,
        so it is a legitimate last resort - just never a preferred one."""
        self._offline(monkeypatch, {"169.254.8.31"})
        assert get_primary_local_ipv4() == "169.254.8.31"

    def test_pick_is_numeric_not_lexicographic(self, monkeypatch) -> None:
        self._offline(monkeypatch, {"10.0.0.10", "10.0.0.9"})
        assert get_primary_local_ipv4() == "10.0.0.9"

    def test_pick_is_stable_across_calls(self, monkeypatch) -> None:
        """The reason ordering exists at all: an unordered pick flips when an
        unrelated address appears, which reads downstream as a real IP change."""
        self._offline(monkeypatch, {"192.168.1.50", "172.16.0.4", "10.1.2.3"})
        first = get_primary_local_ipv4()
        self._offline(monkeypatch, {"172.16.0.4", "10.1.2.3", "192.168.1.50"})
        assert get_primary_local_ipv4() == first


class TestAddressPreferenceOrdering:
    """The tie-break that decides which address the station reports when
    nothing is pinned and the outbound probe fails - i.e. on an offline LAN."""

    def test_a_real_lease_outranks_a_link_local(self) -> None:
        """A station that took a 169.254 address while DHCP was failing must
        stop advertising it once a real lease arrives."""
        addresses = ["169.254.8.31", "192.168.1.50"]
        assert min(addresses, key=net_utils_module._address_preference) == "192.168.1.50"

    def test_ordering_is_numeric_rather_than_lexicographic(self) -> None:
        addresses = ["10.0.0.10", "10.0.0.9"]
        assert min(addresses, key=net_utils_module._address_preference) == "10.0.0.9"

    def test_a_non_numeric_address_sorts_without_raising(self) -> None:
        """psutil yields dotted quads, so this is defence rather than a path
        with a caller - but the sort must not take the process down if that
        ever stops being true."""
        assert net_utils_module._address_preference("fe80::1") == (0, ())


class TestMulticastIfacePinning:
    """The three-state pin rule shared by the discovery beacon and catalog sync.

    An unbound multicast socket does not reach "all interfaces" - it follows the
    routing table onto one NIC the operator never chose, which is why a plane
    that fell back read as contained on whichever interface was captured.
    """

    class _Sock:
        def __init__(self, fail_on: int | None = None) -> None:
            self.fail_on = fail_on
            self.calls: list[tuple[int, int, bytes]] = []

        def setsockopt(self, level: int, opt: int, val: bytes) -> None:
            if opt == self.fail_on:
                raise OSError("iface gone")
            self.calls.append((level, opt, val))

    def test_an_address_pins_the_send_socket(self) -> None:
        sock = self._Sock()
        net_utils_module.bind_multicast_send_iface(sock, "10.0.0.5")
        assert sock.calls == [(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton("10.0.0.5"))]

    def test_blank_leaves_the_send_socket_to_the_os(self) -> None:
        """Nothing configured is not the same as a pin that failed."""
        sock = self._Sock()
        net_utils_module.bind_multicast_send_iface(sock, "")
        assert sock.calls == []

    def test_none_refuses_to_send(self) -> None:
        sock = self._Sock()
        with pytest.raises(net_utils_module.InterfaceUnavailable):
            net_utils_module.bind_multicast_send_iface(sock, None)
        assert sock.calls == []

    def test_a_failed_pin_refuses_to_send(self) -> None:
        sock = self._Sock(fail_on=socket.IP_MULTICAST_IF)
        with pytest.raises(net_utils_module.InterfaceUnavailable):
            net_utils_module.bind_multicast_send_iface(sock, "10.0.0.5")

    def test_an_address_joins_only_that_iface(self) -> None:
        sock = self._Sock()
        net_utils_module.join_multicast_group_on_iface(sock, "239.1.2.3", "10.0.0.5")
        assert sock.calls == [
            (
                socket.IPPROTO_IP,
                socket.IP_ADD_MEMBERSHIP,
                socket.inet_aton("239.1.2.3") + socket.inet_aton("10.0.0.5"),
            )
        ]

    def test_blank_joins_the_wildcard(self) -> None:
        sock = self._Sock()
        net_utils_module.join_multicast_group_on_iface(sock, "239.1.2.3", "")
        assert sock.calls[0][2].endswith(socket.inet_aton("0.0.0.0"))

    def test_none_refuses_to_join(self) -> None:
        sock = self._Sock()
        with pytest.raises(net_utils_module.InterfaceUnavailable):
            net_utils_module.join_multicast_group_on_iface(sock, "239.1.2.3", None)
        assert sock.calls == []

    def test_a_failed_join_does_not_retry_on_the_wildcard(self) -> None:
        sock = self._Sock(fail_on=socket.IP_ADD_MEMBERSHIP)
        with pytest.raises(net_utils_module.InterfaceUnavailable):
            net_utils_module.join_multicast_group_on_iface(sock, "239.1.2.3", "10.0.0.5")
        assert sock.calls == []

    def test_an_unpinned_join_failure_propagates_unchanged(self) -> None:
        """With nothing configured there is no pin to protect, so the OSError
        is the caller's own bind problem rather than an excluded interface.
        """
        sock = self._Sock(fail_on=socket.IP_ADD_MEMBERSHIP)
        with pytest.raises(OSError) as excinfo:
            net_utils_module.join_multicast_group_on_iface(sock, "239.1.2.3", "")
        assert not isinstance(excinfo.value, net_utils_module.InterfaceUnavailable)

    def test_the_error_is_an_oserror(self) -> None:
        """Each sender already wraps its socket setup in ``except OSError``;
        a sibling type would slip past those handlers and kill the thread.
        """
        assert issubclass(net_utils_module.InterfaceUnavailable, OSError)


class TestResolveWebBind:
    """The web UI is the one plane that fails OPEN.

    Every other plane going silent is diagnosable from another station; an
    unreachable config UI leaves nobody able to correct the pin that caused
    it, so an unresolvable pin serves everywhere instead of nothing.
    """

    def test_nothing_configured_serves_every_interface(self) -> None:
        assert resolve_web_bind("", "") == ("0.0.0.0", "none")

    def test_an_explicit_address_outranks_the_interface_pin(self, monkeypatch) -> None:
        monkeypatch.setattr(
            net_utils_module.psutil,
            "net_if_addrs",
            lambda: _fake_addrs({"eth0": [(socket.AF_INET, "192.168.1.5")]}),
        )
        assert resolve_web_bind("10.0.0.9", "eth0") == ("10.0.0.9", "iface")

    def test_a_live_pin_resolves_to_that_interfaces_address(self, monkeypatch) -> None:
        monkeypatch.setattr(
            net_utils_module.psutil,
            "net_if_addrs",
            lambda: _fake_addrs({"eth0": [(socket.AF_INET, "192.168.1.5")]}),
        )
        assert resolve_web_bind("", "eth0") == ("192.168.1.5", "iface")

    def test_a_pin_with_no_address_falls_back_to_every_interface(self, monkeypatch) -> None:
        monkeypatch.setattr(
            net_utils_module.psutil,
            "net_if_addrs",
            lambda: _fake_addrs({"eth0": [(socket.AF_INET, "192.168.1.5")]}),
        )
        assert resolve_web_bind("", "eth1") == ("0.0.0.0", "down")

    def test_a_pin_with_only_a_loopback_address_falls_back(self, monkeypatch) -> None:
        """A pin resolving to 127.x would take the UI off the network entirely
        while still reporting a bound address – worse than the wildcard.
        """
        monkeypatch.setattr(
            net_utils_module.psutil,
            "net_if_addrs",
            lambda: _fake_addrs({"lo": [(socket.AF_INET, "127.0.0.1")]}),
        )
        assert resolve_web_bind("", "lo") == ("0.0.0.0", "down")

    def test_it_never_falls_through_to_another_interface(self, monkeypatch) -> None:
        """The fallback is the wildcard, never a different NIC's address:
        picking one silently would hide that the pin is broken.
        """
        monkeypatch.setattr(
            net_utils_module.psutil,
            "net_if_addrs",
            lambda: _fake_addrs({"eth0": [(socket.AF_INET, "192.168.1.5")]}),
        )
        host, _status = resolve_web_bind("", "eth1")
        assert host != "192.168.1.5"


# --------------------------------------------------------------------------- #
# route_source
# --------------------------------------------------------------------------- #


class _RouteProbe:
    def __init__(self, family: int, local: str, error: int = 0) -> None:
        self.family, self.local, self.error = family, local, error
        self.options: list[tuple[int, int, int]] = []
        self.connected: tuple | None = None
        self.closed = False

    def setsockopt(self, level: int, option: int, value: int) -> None:
        self.options.append((level, option, value))

    def connect(self, address: tuple) -> None:
        if self.error:
            raise OSError(self.error, "Network is unreachable")
        self.connected = address

    def getsockname(self) -> tuple:
        return (self.local, 40000)

    def close(self) -> None:
        self.closed = True


@pytest.fixture()
def probes(monkeypatch):
    made: list[_RouteProbe] = []
    state = SimpleNamespace(made=made, local="192.0.2.10", error=0)

    def _socket(family: int, kind: int) -> _RouteProbe:
        made.append(_RouteProbe(family, state.local, state.error))
        return made[-1]

    monkeypatch.setattr(net_utils_module.socket, "socket", _socket)
    return state


def test_route_source_is_the_address_the_routing_table_sends_from(probes) -> None:
    assert route_source("198.51.100.20", 554) == "192.0.2.10"
    (probe,) = probes.made
    assert probe.connected == ("198.51.100.20", 554)
    assert probe.closed


def test_route_source_allows_a_broadcast_destination(probes) -> None:
    """A broadcast destination is refused without the option, though nothing is sent."""
    route_source("192.0.2.255")
    assert (socket.SOL_SOCKET, socket.SO_BROADCAST, 1) in probes.made[0].options


def test_route_source_needs_some_port(probes) -> None:
    route_source("198.51.100.20")
    assert probes.made[0].connected[1] != 0


def test_route_source_is_none_where_nothing_routes(probes) -> None:
    probes.error = 101
    assert route_source("203.0.113.20") is None
    assert probes.made[0].closed


def test_route_source_asks_ipv6_in_its_own_family(probes) -> None:
    probes.local = "2001:db8::10%eth0"
    assert route_source("2001:db8::20") == "2001:db8::10"
    assert probes.made[0].family == socket.AF_INET6
    assert probes.made[0].options == []


# read_ipv4_routes
# --------------------------------------------------------------------------- #

_ROUTE_HEADER = "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"


def _word(address: str) -> str:
    """An IPv4 address the way the kernel prints it: its network-order word as a native integer."""
    return f"{int.from_bytes(socket.inet_aton(address), sys.byteorder):08X}"


def _row(iface: str, dest: str, gateway: str, flags: int, metric: str, mask: str) -> str:
    return f"{iface}\t{_word(dest)}\t{_word(gateway)}\t{flags:04X}\t0\t0\t{metric}\t{_word(mask)}\t0\t0\t0\n"


def test_read_ipv4_routes_decodes_every_field(tmp_path) -> None:
    path = tmp_path / "route"
    path.write_text(
        _ROUTE_HEADER
        + _row("eth0", "0.0.0.0", "192.0.2.1", 0x3, "100", "0.0.0.0")
        + _row("eth1", "198.51.100.0", "0.0.0.0", 0x0, "0", "255.255.255.0")
    )
    assert read_ipv4_routes(path) == [
        Ipv4Route("eth0", ipaddress.IPv4Network("0.0.0.0/0"), "192.0.2.1", 100),
        Ipv4Route("eth1", ipaddress.IPv4Network("198.51.100.0/24"), "0.0.0.0", 0),
    ]


def test_read_ipv4_routes_reports_an_unreadable_table_as_unknown(tmp_path) -> None:
    """No table (macOS has no /proc) is not the same answer as no routes."""
    assert read_ipv4_routes(tmp_path / "absent") is None
    empty = tmp_path / "route"
    empty.write_text(_ROUTE_HEADER)
    assert read_ipv4_routes(empty) == []


@pytest.mark.parametrize(
    "row",
    [
        "too\tshort\n",
        "eth0\tZZZZ\t00000000\t0001\t0\t0\t0\t00FFFFFF\t0\t0\t0\n",
        "eth0\t006433C6\t00000000\t0001\t0\t0\t0\tNOTAMASK\t0\t0\t0\n",
        "eth0\t00000000\t01B2A8C0\t0003\t0\t0\tNOTANUM\t00000000\t0\t0\t0\n",
        "eth0\t1FFFFFFFF\t00000000\t0001\t0\t0\t0\t00FFFFFF\t0\t0\t0\n",
    ],
    ids=["short", "bad-destination", "bad-mask", "bad-metric", "overflow"],
)
def test_read_ipv4_routes_skips_rows_it_cannot_parse(tmp_path, row: str) -> None:
    path = tmp_path / "route"
    path.write_text(_ROUTE_HEADER + row + _row("eth1", "198.51.100.0", "0.0.0.0", 0x1, "0", "255.255.255.0"))
    assert [route.iface for route in read_ipv4_routes(path) or []] == ["eth1"]


@pytest.mark.parametrize(
    "row",
    [
        _row("eth0", "198.51.100.0", "0.0.0.0", 0x0201, "0", "255.255.255.0"),
        _row("*", "198.51.100.0", "0.0.0.0", 0x0001, "0", "255.255.255.0"),
    ],
    ids=["unreachable-or-prohibit", "blackhole"],
)
def test_read_ipv4_routes_leaves_out_a_route_that_sends_nothing(tmp_path, row: str) -> None:
    """Longest prefix would otherwise pick it and call the target reachable."""
    path = tmp_path / "route"
    path.write_text(_ROUTE_HEADER + row + _row("eth1", "198.51.0.0", "0.0.0.0", 0x1, "0", "255.255.0.0"))
    assert [route.iface for route in read_ipv4_routes(path) or []] == ["eth1"]


def test_read_ipv4_routes_reads_the_kernel_table_by_default(tmp_path, monkeypatch) -> None:
    path = tmp_path / "route"
    path.write_text(_ROUTE_HEADER + _row("eth1", "198.51.100.0", "0.0.0.0", 0x1, "0", "255.255.255.0"))
    monkeypatch.setattr(net_utils_module, "_PROC_NET_ROUTE", path)
    assert [route.iface for route in read_ipv4_routes() or []] == ["eth1"]


# --------------------------------------------------------------------------- #
# BoundedResolver
# --------------------------------------------------------------------------- #


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture()
def dns(monkeypatch):
    """``getaddrinfo`` answering from ``answers`` (a name missing there fails),
    recording every call, and failing the test on a thread that dies."""
    state = SimpleNamespace(answers={"cam.example": ["192.0.2.20"]}, calls=[], gate=None)

    def _getaddrinfo(host, port, family=0, kind=0, *_a, **_k):
        state.calls.append((host, family))
        if state.gate is not None:
            state.gate.wait(5)
        if host not in state.answers:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        return [
            (socket.AF_INET6 if ":" in a else socket.AF_INET, kind, 17, "", (a, 0)) for a in state.answers[host]
        ] * 2  # one entry per socket type, as the real call returns

    monkeypatch.setattr(net_utils_module.socket, "getaddrinfo", _getaddrinfo)
    crashes: list = []
    monkeypatch.setattr(threading, "excepthook", crashes.append)
    yield state
    if state.gate is not None:
        state.gate.set()
    for worker in [t for t in threading.enumerate() if t.name == "test-dns"]:
        worker.join(5)
    assert crashes == []


def _resolver(**kwargs) -> BoundedResolver:
    return BoundedResolver(thread_name="test-dns", **kwargs)


def _join_test_lookups() -> None:
    for worker in [t for t in threading.enumerate() if t.name == "test-dns"]:
        threading.Thread.join(worker, 5)


class TestBoundedResolver:
    @pytest.mark.parametrize("literal", ["192.0.2.20", "2001:db8::20"])
    def test_a_literal_is_its_own_answer(self, dns, literal: str) -> None:
        assert _resolver().lookup(literal, 1.0) == HostLookup("literal", (literal,))
        assert dns.calls == []

    def test_an_ipv4_resolver_refuses_an_ipv6_literal(self, dns) -> None:
        found = _resolver(family=socket.AF_INET).lookup("2001:db8::20", 1.0)
        assert found == HostLookup("literal", error="not an IPv4 address")

    def test_a_name_resolves_to_every_address_once(self, dns) -> None:
        dns.answers["cam.example"] = ["192.0.2.20", "2001:db8::20"]
        assert _resolver().lookup("cam.example", 1.0) == HostLookup("resolved", ("192.0.2.20", "2001:db8::20"))

    def test_the_family_reaches_the_lookup(self, dns) -> None:
        _resolver(family=socket.AF_INET).lookup("cam.example", 1.0)
        assert dns.calls == [("cam.example", socket.AF_INET)]

    def test_without_a_lifetime_every_call_looks_up_again(self, dns) -> None:
        resolver = _resolver()
        for _ in range(2):
            assert resolver.lookup("cam.example", 1.0).addresses == ("192.0.2.20",)
        assert len(dns.calls) == 2
        assert resolver.cached("cam.example") == ()

    def test_an_answer_is_reused_for_its_lifetime(self, dns) -> None:
        clock = _Clock()
        resolver = _resolver(ttl_s=30.0, clock=clock)
        resolver.lookup("cam.example", 1.0)
        assert resolver.lookup("cam.example", 1.0).addresses == ("192.0.2.20",)
        assert resolver.cached("cam.example") == ("192.0.2.20",)
        assert len(dns.calls) == 1

        clock.now += 30.0
        dns.answers["cam.example"] = ["192.0.2.30"]
        assert resolver.cached("cam.example") == ()
        assert resolver.lookup("cam.example", 1.0).addresses == ("192.0.2.30",)

    def test_a_failure_is_reported_with_its_reason(self, dns) -> None:
        found = _resolver().lookup("nowhere.example", 1.0)
        assert found.outcome == "failed"
        assert "not known" in found.error

    def test_a_failure_is_remembered_only_when_asked(self, dns) -> None:
        clock = _Clock()
        remembering = _resolver(failure_ttl_s=30.0, clock=clock)
        for _ in range(2):
            assert remembering.lookup("nowhere.example", 1.0).outcome == "failed"
        assert len(dns.calls) == 1
        clock.now += 30.0
        remembering.lookup("nowhere.example", 1.0)
        assert len(dns.calls) == 2

        forgetting = _resolver(ttl_s=30.0, clock=clock)
        for _ in range(2):
            forgetting.lookup("nowhere.example", 1.0)
        assert len(dns.calls) == 4

    def test_an_empty_answer_is_a_failure(self, dns) -> None:
        dns.answers["empty.example"] = []
        assert _resolver().lookup("empty.example", 1.0) == HostLookup("failed", error="no address")

    def test_a_malformed_name_is_a_failure_not_a_dead_thread(self, dns, monkeypatch) -> None:
        """IDNA encoding rejects an empty label with a ValueError, not an OSError."""

        def _idna(*_a, **_k):
            raise UnicodeError("label empty or too long")

        monkeypatch.setattr(net_utils_module.socket, "getaddrinfo", _idna)
        resolver = _resolver()
        for _ in range(2):
            found = resolver.lookup("cam..example", 1.0)
            assert found == HostLookup("failed", error="label empty or too long")

    def test_a_slow_lookup_is_waited_out_once_and_serves_the_next_caller(self, dns) -> None:
        """A caller whose budget has already passed since the lookup started does
        not wait again; the answer serves whoever asks after it ends."""
        clock = _Clock()
        dns.gate = threading.Event()
        resolver = _resolver(ttl_s=30.0, clock=clock)
        assert resolver.lookup("cam.example", 0.01) == HostLookup("pending")

        clock.now += 1.0
        # Answering while a second wait would still run tells a wait from none.
        threading.Timer(0.05, dns.gate.set).start()
        assert resolver.lookup("cam.example", 0.5) == HostLookup("pending")

        _join_test_lookups()
        assert resolver.lookup("cam.example", 0.5).addresses == ("192.0.2.20",)
        assert len(dns.calls) == 1

    def test_callers_inside_the_wait_share_it(self, dns) -> None:
        """A caller arriving while the lookup is within its budget waits on it too."""
        dns.gate = threading.Event()
        resolver = _resolver(ttl_s=30.0)
        first: list[HostLookup] = []
        waiter = threading.Thread(target=lambda: first.append(resolver.lookup("cam.example", 5.0)))
        waiter.start()
        while not dns.calls:
            threading.Event().wait(0.001)
        threading.Timer(0.05, dns.gate.set).start()
        assert resolver.lookup("cam.example", 5.0) == HostLookup("resolved", ("192.0.2.20",))
        waiter.join(5)
        assert first == [HostLookup("resolved", ("192.0.2.20",))]
        assert len(dns.calls) == 1

    def test_a_short_wait_elsewhere_does_not_shorten_another_callers(self, dns) -> None:
        """The panel's zero wait must not leave the OSC sender, with a second to
        spare, without the answer that arrives inside it."""
        dns.gate = threading.Event()
        resolver = _resolver(ttl_s=30.0)
        assert resolver.lookup("cam.example", 0.0) == HostLookup("pending")
        threading.Timer(0.05, dns.gate.set).start()
        assert resolver.lookup("cam.example", 5.0) == HostLookup("resolved", ("192.0.2.20",))
        assert len(dns.calls) == 1

    def test_a_timed_out_lookup_reads_pending_not_failed_until_its_answer_arrives(self, dns) -> None:
        """A failure lifetime remembers failures, not lookups still running: the
        panel would otherwise call a name that is resolving "not resolved"."""
        dns.gate = threading.Event()
        resolver = _resolver(ttl_s=30.0, failure_ttl_s=30.0)
        assert resolver.lookup("cam.example", 0.01).outcome == "pending"
        assert resolver.lookup("cam.example", 0.01) == HostLookup("pending")
        dns.gate.set()
        _join_test_lookups()
        assert resolver.lookup("cam.example", 1.0).outcome == "resolved"
        assert len(dns.calls) == 1

    def test_a_late_answer_is_served_only_within_its_lifetime(self, dns) -> None:
        dns.gate = threading.Event()
        resolver = _resolver()
        assert resolver.lookup("cam.example", 0.01).outcome == "pending"
        dns.gate.set()
        _join_test_lookups()
        assert resolver.lookup("cam.example", 1.0).outcome == "resolved"
        assert len(dns.calls) == 2

    def test_a_prefetched_name_is_waited_on_not_looked_up_again(self, dns) -> None:
        resolver = _resolver(ttl_s=30.0)
        resolver.prefetch("cam.example")
        assert resolver.lookup("cam.example", 5.0).addresses == ("192.0.2.20",)
        assert len(dns.calls) == 1

    def test_a_lookup_that_cannot_start_does_not_block_the_name(self, dns) -> None:
        starts: list[int] = []

        class _NoThread:
            def start(self) -> None:
                raise RuntimeError("can't start new thread")

        def _factory(**kwargs: Any) -> Any:
            starts.append(1)
            return _NoThread() if len(starts) == 1 else threading.Thread(**kwargs)

        resolver = _resolver(ttl_s=30.0, thread_factory=_factory)
        assert resolver.lookup("cam.example", 1.0) == HostLookup("skipped", error="can't start new thread")
        assert resolver.lookup("cam.example", 1.0).addresses == ("192.0.2.20",)

    def test_a_lookup_cleared_while_it_failed_to_start_leaves_the_name_free(self, dns) -> None:
        refused: list[BoundedResolver] = []

        class _ClearedThenRefused:
            def start(self) -> None:
                refused[0].clear()
                raise RuntimeError("can't start new thread")

        def _factory(**kwargs: Any) -> Any:
            return _ClearedThenRefused() if len(refused) == 1 and not dns.calls else threading.Thread(**kwargs)

        resolver = _resolver(ttl_s=30.0, thread_factory=_factory)
        refused.append(resolver)
        assert resolver.lookup("cam.example", 1.0).outcome == "skipped"
        refused.append(resolver)
        assert resolver.lookup("cam.example", 1.0).addresses == ("192.0.2.20",)

    def test_a_caller_waiting_on_a_lookup_that_could_not_start_is_told_so(self, dns) -> None:
        """Not that the name does not resolve: every waiter hears what the starter hears."""
        joined = threading.Event()
        waiting: list[HostLookup] = []

        def _clock() -> float:
            # A second caller computing its wait has joined the lookup.
            if threading.current_thread().name == "second-caller":
                joined.set()
            return 1000.0

        class _RefusedOnceJoined:
            def start(self) -> None:
                second = threading.Thread(
                    target=lambda: waiting.append(resolver.lookup("cam.example", 5.0)), name="second-caller"
                )
                second.start()
                joined.wait(5)
                raise RuntimeError("can't start new thread")

        resolver = _resolver(clock=_clock, thread_factory=lambda **_k: _RefusedOnceJoined())
        assert resolver.lookup("cam.example", 5.0) == HostLookup("skipped", error="can't start new thread")
        for thread in [t for t in threading.enumerate() if t.name == "second-caller"]:
            thread.join(5)
        assert waiting == [HostLookup("skipped", error="can't start new thread")]

    def test_too_many_names_waiting_skips_another(self, dns) -> None:
        dns.gate = threading.Event()
        dns.answers.update({"a.example": ["192.0.2.1"], "b.example": ["192.0.2.2"]})
        resolver = _resolver(max_in_flight=1)
        assert resolver.lookup("a.example", 0.01).outcome == "pending"
        assert resolver.lookup("b.example", 0.01) == HostLookup("skipped", error="too many lookups still running")
        dns.gate.set()
        for worker in [t for t in threading.enumerate() if t.name == "test-dns"]:
            worker.join(5)
        assert resolver.lookup("b.example", 1.0).addresses == ("192.0.2.2",)

    def test_the_cap_counts_a_lookup_clear_forgot(self, dns) -> None:
        """Forgotten, it still holds a thread until the resolver answers it."""
        dns.gate = threading.Event()
        dns.answers.update({"a.example": ["192.0.2.1"], "b.example": ["192.0.2.2"]})
        resolver = _resolver(max_in_flight=1)
        assert resolver.lookup("a.example", 0.01).outcome == "pending"
        resolver.clear()
        assert resolver.lookup("b.example", 0.01) == HostLookup("skipped", error="too many lookups still running")
        dns.gate.set()
        _join_test_lookups()
        assert resolver.lookup("b.example", 1.0).addresses == ("192.0.2.2",)

    def test_clear_forgets_every_answer(self, dns) -> None:
        resolver = _resolver(ttl_s=30.0)
        resolver.lookup("cam.example", 1.0)
        resolver.clear()
        assert resolver.cached("cam.example") == ()
        resolver.lookup("cam.example", 1.0)
        assert len(dns.calls) == 2

    def test_clear_drops_a_running_lookups_answer(self, dns) -> None:
        """Nothing a lookup started before ``clear()`` finds may reach a caller after it."""
        dns.gate = threading.Event()
        resolver = _resolver(ttl_s=30.0)
        assert resolver.lookup("cam.example", 0.01).outcome == "pending"
        resolver.clear()
        dns.gate.set()
        _join_test_lookups()
        assert resolver.cached("cam.example") == ()
        assert resolver.lookup("cam.example", 1.0).outcome == "resolved"
        assert len(dns.calls) == 2

    def test_a_caller_waiting_through_clear_gets_no_answer(self, dns) -> None:
        dns.gate = threading.Event()
        resolver = _resolver(ttl_s=30.0)
        results: list[HostLookup] = []
        waiter = threading.Thread(target=lambda: results.append(resolver.lookup("cam.example", 5.0)))
        waiter.start()
        while not dns.calls:
            threading.Event().wait(0.001)
        resolver.clear()
        dns.gate.set()
        waiter.join(5)
        assert results == [HostLookup("failed", error="no answer")]

    def test_an_answer_past_its_lifetime_is_dropped(self, dns) -> None:
        clock = _Clock()
        resolver = _resolver(ttl_s=30.0, clock=clock)
        dns.answers["other.example"] = ["192.0.2.30"]
        resolver.lookup("cam.example", 1.0)
        clock.now += 31.0
        resolver.lookup("other.example", 1.0)
        assert len(resolver) == 1
        assert resolver.cached("other.example") == ("192.0.2.30",)

    def test_cached_knows_a_literal_without_a_lookup(self, dns) -> None:
        assert _resolver().cached("192.0.2.20") == ("192.0.2.20",)
        assert _resolver(family=socket.AF_INET).cached("2001:db8::20") == ()
        assert dns.calls == []

    def test_a_finished_lookup_still_exiting_is_not_taken_for_a_running_one(self, dns) -> None:
        """A worker stores its answer, then takes a moment to exit. Judged by
        liveness, a caller in that moment would write "timed out" over a good
        answer and, with a failure lifetime, black the name out."""

        class _Lingering(threading.Thread):
            def start(self) -> None:
                self.run()  # the work finishes at once, but the thread never reports exiting

            def is_alive(self) -> bool:
                return True

        resolver = _resolver(failure_ttl_s=30.0, thread_factory=_Lingering)
        assert resolver.lookup("cam.example", 0.01).addresses == ("192.0.2.20",)
        assert resolver.lookup("cam.example", 0.01).addresses == ("192.0.2.20",)


@pytest.mark.parametrize(
    ("resolver", "thread_name"),
    [(net_utils_module.HOST_RESOLVER, "host-dns"), (net_utils_module.IPV4_RESOLVER, "ipv4-dns")],
    ids=["host", "ipv4"],
)
def test_the_shared_resolvers_cap_how_many_names_resolve_at_once(
    dns, resolver: BoundedResolver, thread_name: str
) -> None:
    """A LAN with no resolver must not collect a hung thread per name ever typed."""
    dns.gate = threading.Event()
    try:
        for i in range(net_utils_module.MAX_LOOKUPS_IN_FLIGHT):
            assert resolver.lookup(f"n{i}.example", 0.0).outcome == "pending"
        assert resolver.lookup("one-more.example", 0.0).outcome == "skipped"
    finally:
        dns.gate.set()
        for worker in [t for t in threading.enumerate() if t.name == thread_name]:
            worker.join(5)


class TestInterfacePresent:
    """Tells an unplugged adapter from one that is there without an address."""

    def test_an_interface_without_an_address_is_present(self, monkeypatch) -> None:
        monkeypatch.setattr(net_utils_module.psutil, "net_if_addrs", lambda: {"eth1": [], "eth0": []})
        assert net_utils_module.interface_present("eth1") is True

    def test_an_unplugged_adapter_is_not(self, monkeypatch) -> None:
        monkeypatch.setattr(net_utils_module.psutil, "net_if_addrs", lambda: {"eth0": []})
        assert net_utils_module.interface_present("eth1") is False

    def test_no_name_is_never_present(self, monkeypatch) -> None:
        monkeypatch.setattr(net_utils_module.psutil, "net_if_addrs", lambda: {"": []})
        assert net_utils_module.interface_present("") is False
