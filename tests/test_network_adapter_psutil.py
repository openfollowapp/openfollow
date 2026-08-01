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
