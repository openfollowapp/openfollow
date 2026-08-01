# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for PsutilReadOnlyAdapter: interface/state reads, /proc route + resolv.conf DNS parsing, read-only apply."""

from __future__ import annotations

import socket
from types import SimpleNamespace

import pytest

import openfollow.network.psutil_adapter as psutil_adapter
from openfollow.network.adapter import Ipv4Config, Ipv4Method
from openfollow.network.psutil_adapter import PsutilReadOnlyAdapter

pytestmark = pytest.mark.unit


@pytest.fixture
def fake_psutil(monkeypatch):
    addrs = {
        "eth0": [
            SimpleNamespace(family=socket.AF_INET, address="192.168.1.50", netmask="255.255.255.0"),
        ],
        "lo": [
            SimpleNamespace(family=socket.AF_INET, address="127.0.0.1", netmask="255.0.0.0"),
        ],
    }
    stats = {
        "eth0": SimpleNamespace(isup=True),
        "lo": SimpleNamespace(isup=True),
    }
    monkeypatch.setattr(psutil_adapter.psutil, "net_if_addrs", lambda: addrs)
    monkeypatch.setattr(psutil_adapter.psutil, "net_if_stats", lambda: stats)
    return addrs, stats


class TestPsutilAdapter:
    def test_list_interfaces(self, fake_psutil) -> None:
        adapter = PsutilReadOnlyAdapter()
        names = {i.name for i in adapter.list_interfaces()}
        assert names == {"eth0", "lo"}

    def test_get_state_reads_address_and_prefix(self, fake_psutil, monkeypatch, tmp_path) -> None:
        resolv = tmp_path / "resolv.conf"
        resolv.write_text("nameserver 8.8.8.8\nnameserver 1.1.1.1\n")
        monkeypatch.setattr(psutil_adapter, "_RESOLV_CONF", resolv)
        monkeypatch.setattr(psutil_adapter, "_PROC_ROUTE", tmp_path / "missing.route")
        adapter = PsutilReadOnlyAdapter()
        state = adapter.get_state("eth0")
        assert state is not None
        assert state.ipv4.address == "192.168.1.50"
        assert state.ipv4.prefix == 24
        assert state.ipv4.dns == ("8.8.8.8", "1.1.1.1")

    def test_get_state_unknown_iface_returns_none(self, fake_psutil) -> None:
        assert PsutilReadOnlyAdapter().get_state("nope0") is None

    def test_apply_and_renew_return_failure(self) -> None:
        adapter = PsutilReadOnlyAdapter()
        res = adapter.apply_ipv4("eth0", Ipv4Config(method=Ipv4Method.DHCP))
        assert res.ok is False
        assert "Read-only" in res.message
        renew = adapter.renew_lease("eth0")
        assert renew.ok is False
        assert "Read-only" in renew.message

    def test_is_writable_false(self) -> None:
        assert PsutilReadOnlyAdapter().is_writable() is False

    def test_read_gateway_from_proc_route(self, monkeypatch, tmp_path) -> None:
        # Default route to 192.168.1.1 on eth0; gateway in little-endian hex.
        route = tmp_path / "route"
        # 192.168.1.1 little-endian = 0101A8C0
        route.write_text("Iface\tDestination\tGateway\tFlags\neth0\t00000000\t0101A8C0\t0003\n")
        monkeypatch.setattr(psutil_adapter, "_PROC_ROUTE", route)
        assert psutil_adapter._read_gateway("eth0") == "192.168.1.1"

    def test_read_gateway_missing_file(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(psutil_adapter, "_PROC_ROUTE", tmp_path / "missing")
        assert psutil_adapter._read_gateway("eth0") is None

    def test_read_gateway_skips_short_lines(self, monkeypatch, tmp_path) -> None:
        route = tmp_path / "route"
        route.write_text("Iface\tDestination\nshort\n")
        monkeypatch.setattr(psutil_adapter, "_PROC_ROUTE", route)
        assert psutil_adapter._read_gateway("eth0") is None

    def test_read_gateway_skips_non_default_routes(self, monkeypatch, tmp_path) -> None:
        route = tmp_path / "route"
        route.write_text(
            "Iface\tDestination\tGateway\tFlags\neth0\t01010101\t0101A8C0\t0003\n"  # destination != 0
        )
        monkeypatch.setattr(psutil_adapter, "_PROC_ROUTE", route)
        assert psutil_adapter._read_gateway("eth0") is None

    def test_read_gateway_skips_routes_without_gateway_flag(
        self,
        monkeypatch,
        tmp_path,
    ) -> None:
        route = tmp_path / "route"
        route.write_text(
            "Iface\tDestination\tGateway\tFlags\neth0\t00000000\t0101A8C0\t0001\n"  # flags=0001, no RTF_GATEWAY
        )
        monkeypatch.setattr(psutil_adapter, "_PROC_ROUTE", route)
        assert psutil_adapter._read_gateway("eth0") is None

    def test_read_gateway_handles_bad_flags(self, monkeypatch, tmp_path) -> None:
        route = tmp_path / "route"
        route.write_text("Iface\tDestination\tGateway\tFlags\neth0\t00000000\t0101A8C0\tNOTHEX\n")
        monkeypatch.setattr(psutil_adapter, "_PROC_ROUTE", route)
        assert psutil_adapter._read_gateway("eth0") is None

    def test_read_gateway_handles_bad_gateway(self, monkeypatch, tmp_path) -> None:
        route = tmp_path / "route"
        route.write_text("Iface\tDestination\tGateway\tFlags\neth0\t00000000\tNOTHEX\t0003\n")
        monkeypatch.setattr(psutil_adapter, "_PROC_ROUTE", route)
        assert psutil_adapter._read_gateway("eth0") is None

    def test_read_dns_missing_file(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(psutil_adapter, "_RESOLV_CONF", tmp_path / "missing")
        assert psutil_adapter._read_dns() == ()

    def test_read_dns_skips_non_nameserver_lines(self, monkeypatch, tmp_path) -> None:
        resolv = tmp_path / "resolv.conf"
        resolv.write_text("search example.com\nnameserver 8.8.8.8\n# comment\n")
        monkeypatch.setattr(psutil_adapter, "_RESOLV_CONF", resolv)
        assert psutil_adapter._read_dns() == ("8.8.8.8",)

    def test_read_dns_caps_at_three(self, monkeypatch, tmp_path) -> None:
        resolv = tmp_path / "resolv.conf"
        resolv.write_text("\n".join(f"nameserver 8.8.8.{i}" for i in range(1, 6)) + "\n")
        monkeypatch.setattr(psutil_adapter, "_RESOLV_CONF", resolv)
        result = psutil_adapter._read_dns()
        assert len(result) == 3

    def test_read_dns_skips_malformed_nameserver_lines(self, monkeypatch, tmp_path) -> None:
        resolv = tmp_path / "resolv.conf"
        resolv.write_text(
            "nameserver\n"  # no second token at all
            "nameserver localhost\n"  # second token but no dot
            "nameserver 8.8.8.8\n"
        )
        monkeypatch.setattr(psutil_adapter, "_RESOLV_CONF", resolv)
        assert psutil_adapter._read_dns() == ("8.8.8.8",)

    def test_netmask_to_prefix_invalid(self) -> None:
        assert psutil_adapter._netmask_to_prefix(None) is None
        assert psutil_adapter._netmask_to_prefix("not-a-mask") is None
        # non-contiguous mask
        assert psutil_adapter._netmask_to_prefix("255.0.255.0") is None

    def test_list_interfaces_handles_psutil_error(self, monkeypatch) -> None:
        def _boom():
            raise RuntimeError("psutil unavailable")

        monkeypatch.setattr(psutil_adapter.psutil, "net_if_stats", _boom)
        assert PsutilReadOnlyAdapter().list_interfaces() == []

    def test_get_state_handles_psutil_error_inside_addrs(
        self,
        fake_psutil,
        monkeypatch,
        tmp_path,
    ) -> None:
        # ``get_state`` calls ``list_interfaces`` first (which uses
        # ``net_if_addrs``); we want the *second* call inside get_state to
        # raise. Wrap it in a counter so only call #2 trips the except.
        # Patch ``_RESOLV_CONF`` / ``_PROC_ROUTE`` so the test doesn't
        # leak onto the host's real files.
        monkeypatch.setattr(psutil_adapter, "_RESOLV_CONF", tmp_path / "resolv.conf")
        monkeypatch.setattr(psutil_adapter, "_PROC_ROUTE", tmp_path / "missing.route")
        original = psutil_adapter.psutil.net_if_addrs
        call_count = {"n": 0}

        def flaky_addrs():
            call_count["n"] += 1
            if call_count["n"] == 1:
                return original()
            raise RuntimeError("addrs unavailable")

        monkeypatch.setattr(psutil_adapter.psutil, "net_if_addrs", flaky_addrs)
        adapter = PsutilReadOnlyAdapter()
        state = adapter.get_state("eth0")
        assert state is not None
        assert state.ipv4.address is None
        assert state.ipv4.prefix is None

    def test_list_interfaces_skips_link_layer_mac_when_absent(
        self,
        monkeypatch,
    ) -> None:
        import socket
        from types import SimpleNamespace

        addrs = {"eth0": [SimpleNamespace(family=socket.AF_INET, address="10.0.0.1", netmask="255.0.0.0")]}
        monkeypatch.setattr(psutil_adapter.psutil, "net_if_addrs", lambda: addrs)
        monkeypatch.setattr(psutil_adapter.psutil, "net_if_stats", lambda: {"eth0": SimpleNamespace(isup=True)})
        ifaces = PsutilReadOnlyAdapter().list_interfaces()
        assert ifaces[0].mac is None

    def test_list_interfaces_picks_mac_from_link_address(
        self,
        monkeypatch,
    ) -> None:
        from types import SimpleNamespace

        fake_family = SimpleNamespace(name="AF_PACKET")
        addrs = {
            "eth0": [
                SimpleNamespace(family=fake_family, address="aa:bb:cc:dd:ee:ff", netmask=None),
            ],
        }
        monkeypatch.setattr(psutil_adapter.psutil, "net_if_addrs", lambda: addrs)
        monkeypatch.setattr(psutil_adapter.psutil, "net_if_stats", lambda: {"eth0": SimpleNamespace(isup=True)})
        ifaces = PsutilReadOnlyAdapter().list_interfaces()
        assert ifaces[0].mac == "aa:bb:cc:dd:ee:ff"


class TestAdapterDefaults:
    """Verify concrete defaults on the base adapter trait."""

    def test_is_writable_default_true(self) -> None:
        from openfollow.network.dhcpcd_adapter import DhcpcdAdapter

        # DhcpcdAdapter inherits the default; we don't override.
        assert DhcpcdAdapter().is_writable() is True

    def test_get_ipv6_state_default_none(self) -> None:
        from openfollow.network.dhcpcd_adapter import DhcpcdAdapter

        assert DhcpcdAdapter().get_ipv6_state("eth0") is None


class TestResolvAndRouteOsError:
    def test_read_dns_handles_oserror(self, monkeypatch, tmp_path) -> None:
        """Path exists but reading it raises OSError mid-read (e.g. fs flaky)."""
        from pathlib import Path

        resolv = tmp_path / "resolv.conf"
        resolv.write_text("nameserver 8.8.8.8\n")
        monkeypatch.setattr(psutil_adapter, "_RESOLV_CONF", resolv)

        def boom(self, *args, **kwargs):
            raise OSError("flaky")

        monkeypatch.setattr(Path, "read_text", boom)
        assert psutil_adapter._read_dns() == ()

    def test_read_gateway_handles_oserror(self, monkeypatch, tmp_path) -> None:
        from pathlib import Path

        route = tmp_path / "route"
        route.write_text("Iface\tDestination\tGateway\tFlags\neth0\t00000000\t0101A8C0\t0003\n")
        monkeypatch.setattr(psutil_adapter, "_PROC_ROUTE", route)

        def boom(self, *args, **kwargs):
            raise OSError("flaky")

        monkeypatch.setattr(Path, "read_text", boom)
        assert psutil_adapter._read_gateway("eth0") is None


class TestGetStateInnerErrors:
    def test_get_state_when_iface_listed_but_no_addrs(self, monkeypatch) -> None:
        """list_interfaces returns eth0; net_if_addrs on second call returns
        no entry for eth0 – the inner loop is empty (covers 121->129)."""
        from types import SimpleNamespace

        addrs = {"eth0": []}
        stats = {"eth0": SimpleNamespace(isup=True)}
        monkeypatch.setattr(psutil_adapter.psutil, "net_if_addrs", lambda: addrs)
        monkeypatch.setattr(psutil_adapter.psutil, "net_if_stats", lambda: stats)
        monkeypatch.setattr(psutil_adapter, "_RESOLV_CONF", __import__("pathlib").Path("/nope/resolv.conf"))
        monkeypatch.setattr(psutil_adapter, "_PROC_ROUTE", __import__("pathlib").Path("/nope/route"))
        state = PsutilReadOnlyAdapter().get_state("eth0")
        assert state is not None
        assert state.ipv4.address is None

    def test_get_state_with_only_ipv6_addr_skips(self, monkeypatch) -> None:
        import socket  # noqa: F401  # imported for the AF_INET6 constant ref below
        from types import SimpleNamespace

        addrs = {
            "eth0": [
                SimpleNamespace(family=socket.AF_INET6, address="fe80::1", netmask=None),
            ],
        }
        stats = {"eth0": SimpleNamespace(isup=True)}
        monkeypatch.setattr(psutil_adapter.psutil, "net_if_addrs", lambda: addrs)
        monkeypatch.setattr(psutil_adapter.psutil, "net_if_stats", lambda: stats)
        monkeypatch.setattr(psutil_adapter, "_RESOLV_CONF", __import__("pathlib").Path("/nope/resolv.conf"))
        monkeypatch.setattr(psutil_adapter, "_PROC_ROUTE", __import__("pathlib").Path("/nope/route"))
        state = PsutilReadOnlyAdapter().get_state("eth0")
        assert state is not None
        assert state.ipv4.address is None
