# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Unit tests for openfollow.net_utils IPv4 enumeration and source-IP resolution."""

from __future__ import annotations

import socket
from types import SimpleNamespace

import pytest

import openfollow.net_utils as net_utils_module
from openfollow.net_utils import (
    get_iface_for_ip,
    get_iface_ipv4,
    get_local_ipv4_addresses,
    get_primary_local_ipv4,
    list_iface_ipv4,
    resolve_iface_ip,
    resolve_plane_source_ip,
    resolve_source_ip,
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
