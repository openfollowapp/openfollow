# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Web write path for Pi network settings.

Drives the editable network form + apply/renew endpoints end-to-end against
a live ConfigWebServer wired with a fake network backend, asserting the form
reuses ``validate_apply`` and the adapter ``apply_ipv4`` / ``renew_lease``
contract surfaced through the server's provider/handler callbacks.
"""

from __future__ import annotations

import socket
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

import openfollow.web.discovery as discovery_module
from openfollow.network.adapter import ApplyResult, Ipv4Method
from openfollow.web.routes import _port_suffix
from openfollow.web.server import ConfigWebServer

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "netloc,expected",
    [
        ("192.168.1.5:8080", ":8080"),  # IPv4 + port
        ("192.168.1.5", ""),  # IPv4, no port
        ("host.local:9", ":9"),  # hostname + port
        ("[fe80::1]:8080", ":8080"),  # IPv6 literal + port
        ("[fe80::1]", ""),  # IPv6 literal, no port (was misread as :1)
    ],
)
def test_port_suffix(netloc: str, expected: str) -> None:
    assert _port_suffix(netloc) == expected


# --------------------------------------------------------------------------- #
# Fake network backend + live server
# --------------------------------------------------------------------------- #


class FakeNetwork:
    """Stand-in for the services-layer network providers/handlers."""

    def __init__(
        self,
        *,
        writable: bool = True,
        interfaces: tuple[str, ...] = ("eth0", "wlan0"),
        method: str = "dhcp",
        address: str = "10.0.0.5",
        prefix: int | None = 24,
        subnet_mask: str = "255.255.255.0",
        router: str = "10.0.0.1",
        dns: tuple[str, ...] = ("1.1.1.1",),
        lease_display: str | None = "1h 00m",
    ) -> None:
        self.writable = writable
        self.interfaces = list(interfaces)
        self.method = method
        self.address = address
        self.prefix = prefix
        self.subnet_mask = subnet_mask
        self.router = router
        self.dns = list(dns)
        self.lease_display = lease_display
        self.applied: list[tuple[str, object]] = []
        self.renewed: list[str] = []
        self.apply_result = ApplyResult(ok=True)
        self.renew_result = ApplyResult(ok=True)

    def config_provider(self, iface: str | None = None) -> dict | None:
        if not self.interfaces:
            return {"interfaces": [], "writable": self.writable, "backend": "fake"}
        active = iface if iface in self.interfaces else self.interfaces[0]
        return {
            "interfaces": self.interfaces,
            "writable": self.writable,
            "backend": "fake",
            "active_interface": active,
            "method": self.method,
            "address": self.address,
            "prefix": self.prefix,
            "subnet_mask": self.subnet_mask,
            "router": self.router,
            "dns": list(self.dns),
            "lease_display": self.lease_display,
        }

    def apply_handler(self, iface: str, config: object) -> ApplyResult:
        self.applied.append((iface, config))
        return self.apply_result

    def renew_handler(self, iface: str) -> ApplyResult:
        self.renewed.append(iface)
        return self.renew_result


def _find_free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_port(port: int, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.02)
    return False


def _get(base: str, path: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(f"{base}{path}", timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def _post(base: str, path: str, data: dict) -> tuple[int, str]:
    body = urllib.parse.urlencode(data, doseq=True).encode()
    req = urllib.request.Request(
        f"{base}{path}",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def _post_resp(base: str, path: str, data: dict) -> tuple[int, str, dict]:
    """POST that also returns lower-cased response headers (for HX-Redirect)."""
    body = urllib.parse.urlencode(data, doseq=True).encode()
    req = urllib.request.Request(
        f"{base}{path}",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        headers = {k.lower(): v for k, v in r.headers.items()}
        return r.status, r.read().decode(), headers


@pytest.fixture()
def net_server(tmp_path, monkeypatch):
    """Live ConfigWebServer wired with a FakeNetwork. Yields (fake, base)."""
    for attr in ("BeaconSender", "BeaconReceiver"):
        monkeypatch.setattr(getattr(discovery_module, attr), "start", lambda self: None)
        monkeypatch.setattr(getattr(discovery_module, attr), "stop", lambda self: None)
    fake = FakeNetwork()
    port = _find_free_tcp_port()
    config_path = tmp_path / "config.toml"
    config_path.write_text("controlled_marker_ids = [1]\n", encoding="utf-8")
    server = ConfigWebServer(
        config_path=str(config_path),
        host="127.0.0.1",
        port=port,
        system_name="TestSystem",
        network_config_provider=fake.config_provider,
        network_apply_handler=fake.apply_handler,
        network_renew_handler=fake.renew_handler,
    )
    server.start()
    assert _wait_for_port(port)
    yield fake, f"http://127.0.0.1:{port}"
    server.stop()


# --------------------------------------------------------------------------- #
# View / edit toggle
# --------------------------------------------------------------------------- #


def test_status_view_is_read_only_with_switch_link(net_server) -> None:
    fake, base = net_server
    status, body = _get(base, "/section/network/status")
    assert status == 200
    assert 'id="network-config-section"' in body
    # View-mode bar: a labelled switch link, not a Save-styled button.
    assert "Switch to edit view" in body
    assert 'class="net-mode-pill view"' in body
    assert "protected from change" in body
    assert "/section/network/edit" in body
    assert "disabled" in body  # fields disabled in the view
    assert ">Apply<" not in body  # no apply in the read-only view
    # The view live-polls itself; the Backend field is dropped (not user-facing).
    assert "/section/network/status" in body and "every 5s" in body
    assert "Backend" not in body
    # Read-only view shows current address even for DHCP so operator sees the lease-assigned IP.
    assert 'name="address"' in body
    assert "10.0.0.5" in body  # FakeNetwork's current address


def test_edit_view_enables_fields_and_actions(net_server) -> None:
    fake, base = net_server
    status, body = _get(base, "/section/network/edit")
    assert status == 200
    assert ">Apply<" in body
    assert "Renew DHCP lease" in body
    assert ">Cancel<" in body
    assert "Switch to edit view" not in body
    assert 'class="net-mode-pill edit"' in body
    assert "may disconnect" in body  # disconnect warning moved into the edit-mode bar


def test_method_change_rerenders_edit_fields(net_server) -> None:
    fake, base = net_server
    _, dhcp_body = _post(base, "/section/network", {"iface": "eth0", "method": "dhcp"})
    assert 'name="address"' not in dhcp_body
    _, static_body = _post(base, "/section/network", {"iface": "eth0", "method": "static"})
    assert 'name="address"' in static_body
    assert 'name="subnet_mask"' in static_body
    assert 'name="router"' in static_body
    assert ">Apply<" in static_body  # still the edit form, not the view
    # Renew is DHCP-only – a static config has no lease to renew.
    assert "Renew DHCP lease" not in static_body
    assert "Renew DHCP lease" in dhcp_body


# --------------------------------------------------------------------------- #
# Apply
# --------------------------------------------------------------------------- #


def test_apply_static_calls_adapter_and_redirects_to_new_ip(net_server) -> None:
    fake, base = net_server
    status, body, headers = _post_resp(
        base,
        "/section/network/apply",
        {
            "iface": "eth0",
            "method": "static",
            "address": "192.168.1.50",
            "subnet_mask": "255.255.255.0",
            "router": "192.168.1.1",
            "dns1": "1.1.1.1",
            "dns2": "8.8.8.8",
        },
    )
    assert status == 200
    assert len(fake.applied) == 1
    iface, config = fake.applied[0]
    assert iface == "eth0"
    assert config.method == Ipv4Method.STATIC
    assert config.address == "192.168.1.50"
    assert config.prefix == 24  # converted from the 255.255.255.0 mask
    assert config.router == "192.168.1.1"
    assert config.dns == ("1.1.1.1", "8.8.8.8")
    # Reload the UI at the new static address.
    assert "192.168.1.50" in headers.get("hx-redirect", "")


def test_apply_unknown_iface_defaults_to_active(net_server) -> None:
    fake, base = net_server
    status, _, headers = _post_resp(
        base,
        "/section/network/apply",
        {
            "iface": "bogus0",
            "method": "static",
            "address": "192.168.1.50",
            "subnet_mask": "255.255.255.0",
        },
    )
    assert status == 200
    assert len(fake.applied) == 1
    assert fake.applied[0][0] == "eth0"  # not the forged "bogus0"
    assert "192.168.1.50" in headers.get("hx-redirect", "")


def test_apply_dhcp_manual_redirects_to_manual_address(net_server) -> None:
    fake, base = net_server
    _, _, headers = _post_resp(
        base,
        "/section/network/apply",
        {
            "iface": "eth0",
            "method": "dhcp_manual",
            "address": "192.168.1.77",
        },
    )
    assert len(fake.applied) == 1
    assert "192.168.1.77" in headers.get("hx-redirect", "")


def test_apply_dhcp_manual_drops_forged_router_and_prefix(net_server) -> None:
    fake, base = net_server
    _post(
        base,
        "/section/network/apply",
        {
            "iface": "eth0",
            "method": "dhcp_manual",
            "address": "192.168.1.77",
            "router": "10.0.0.1",  # forged out-of-subnet gateway
            "subnet_mask": "255.255.255.0",  # forged prefix
        },
    )
    assert len(fake.applied) == 1
    _, config = fake.applied[0]
    assert config.address == "192.168.1.77"
    assert config.router is None
    assert config.prefix is None


def test_apply_dhcp_returns_view_not_redirect(net_server) -> None:
    fake, base = net_server
    status, body, headers = _post_resp(
        base,
        "/section/network/apply",
        {
            "iface": "eth0",
            "method": "dhcp",
        },
    )
    assert status == 200
    assert "hx-redirect" not in headers  # DHCP has no known address
    assert "Network settings applied." in body
    assert "Switch to edit view" in body  # back to the read-only view


def test_apply_router_outside_subnet_rejected_stays_on_edit(net_server) -> None:
    fake, base = net_server
    status, body = _post(
        base,
        "/section/network/apply",
        {
            "iface": "eth0",
            "method": "static",
            "address": "192.168.1.50",
            "subnet_mask": "255.255.255.0",
            "router": "10.0.0.1",
        },
    )
    assert status == 200
    assert fake.applied == []  # validation blocked the apply
    assert "not inside the subnet" in body
    assert "10.0.0.1" in body  # operator input preserved
    assert ">Apply<" in body  # stays on the edit form


def test_apply_invalid_subnet_mask_rejected(net_server) -> None:
    fake, base = net_server
    status, body = _post(
        base,
        "/section/network/apply",
        {
            "iface": "eth0",
            "method": "static",
            "address": "192.168.1.50",
            "subnet_mask": "255.255.0.255",
            "router": "",
        },
    )
    assert status == 200
    assert fake.applied == []
    assert "valid IPv4 netmask" in body


def test_apply_static_missing_subnet_mask_rejected(net_server) -> None:
    fake, base = net_server
    status, body = _post(
        base,
        "/section/network/apply",
        {
            "iface": "eth0",
            "method": "static",
            "address": "192.168.1.50",
            "subnet_mask": "",
            "router": "",
        },
    )
    assert status == 200
    assert fake.applied == []
    assert "valid IPv4 netmask" in body


def test_apply_failure_stays_on_edit_with_message(net_server) -> None:
    fake, base = net_server
    fake.apply_result = ApplyResult(ok=False, message="nmcli exploded")
    status, body = _post(
        base,
        "/section/network/apply",
        {
            "iface": "eth0",
            "method": "dhcp",
        },
    )
    assert status == 200
    assert len(fake.applied) == 1
    assert "Apply failed: nmcli exploded" in body
    assert ">Apply<" in body  # stays on edit so they can retry


def test_apply_dhcp_partial_failures_surfaced(net_server) -> None:
    fake, base = net_server
    fake.apply_result = ApplyResult(ok=True, partial_failures=("DNS not set",))
    _, body, headers = _post_resp(
        base,
        "/section/network/apply",
        {
            "iface": "eth0",
            "method": "dhcp",
        },
    )
    assert "hx-redirect" not in headers
    assert "Network settings applied." in body
    assert "DNS not set" in body


def test_apply_static_partial_failures_surfaced_not_redirected(net_server) -> None:
    """Static apply normally redirects to new IP, but redirect's empty body
    would drop adapter partial-failure warnings. Show banner instead."""
    fake, base = net_server
    fake.apply_result = ApplyResult(ok=True, partial_failures=("DNS not set",))
    _, body, headers = _post_resp(
        base,
        "/section/network/apply",
        {
            "iface": "eth0",
            "method": "static",
            "address": "192.168.1.50",
            "subnet_mask": "255.255.255.0",
        },
    )
    assert "hx-redirect" not in headers  # warning surfaced, not redirected
    assert "DNS not set" in body
    assert "192.168.1.50" in body  # reconnect hint points at new IP


# --------------------------------------------------------------------------- #
# Renew
# --------------------------------------------------------------------------- #


def test_renew_calls_adapter_returns_view(net_server) -> None:
    fake, base = net_server
    status, body = _post(base, "/section/network/renew", {"iface": "eth0"})
    assert status == 200
    assert fake.renewed == ["eth0"]
    assert "DHCP lease renewed." in body
    assert "Switch to edit view" in body  # back to the read-only view


def test_renew_failure_surfaces_message(net_server) -> None:
    fake, base = net_server
    fake.renew_result = ApplyResult(ok=False, message="no lease")
    _, body = _post(base, "/section/network/renew", {"iface": "eth0"})
    assert "Renew failed: no lease" in body


def test_renew_partial_failures_surfaced(net_server) -> None:
    fake, base = net_server
    fake.renew_result = ApplyResult(ok=True, partial_failures=("dns warn",))
    _, body = _post(base, "/section/network/renew", {"iface": "eth0"})
    assert "DHCP lease renewed." in body
    assert "dns warn" in body


# --------------------------------------------------------------------------- #
# Read-only host + no provider
# --------------------------------------------------------------------------- #


def test_read_only_host_shows_no_switch_link(net_server) -> None:
    fake, base = net_server
    fake.writable = False
    _, body = _get(base, "/section/network/status")
    assert "Switch to edit view" not in body
    assert ">Apply<" not in body
    # Read-only mode bar points the operator at the on-screen menu instead.
    assert 'class="net-mode-pill readonly"' in body
    assert "on-screen Settings menu" in body


def test_no_provider_renders_unavailable(tmp_path, monkeypatch) -> None:
    """A server built without the network handlers (older wiring / tests)
    renders the unavailable state instead of raising."""
    for attr in ("BeaconSender", "BeaconReceiver"):
        monkeypatch.setattr(getattr(discovery_module, attr), "start", lambda self: None)
        monkeypatch.setattr(getattr(discovery_module, attr), "stop", lambda self: None)
    port = _find_free_tcp_port()
    config_path = tmp_path / "config.toml"
    config_path.write_text("controlled_marker_ids = [1]\n", encoding="utf-8")
    server = ConfigWebServer(
        config_path=str(config_path),
        host="127.0.0.1",
        port=port,
        system_name="TestSystem",
    )
    server.start()
    assert _wait_for_port(port)
    try:
        status, body = _get(f"http://127.0.0.1:{port}", "/section/network/status")
        assert status == 200
        assert "unavailable" in body
        # apply with no handler returns the not-available banner, not a 500.
        status, body = _post(
            f"http://127.0.0.1:{port}",
            "/section/network/apply",
            {
                "iface": "eth0",
                "method": "dhcp",
            },
        )
        assert status == 200
        assert "not available" in body
        # renew with no handler is likewise the not-available banner, not a 500.
        status, body = _post(
            f"http://127.0.0.1:{port}",
            "/section/network/renew",
            {
                "iface": "eth0",
            },
        )
        assert status == 200
        assert "not available" in body
    finally:
        server.stop()


# --------------------------------------------------------------------------- #
# ConfigWebServer method-level error handling (no HTTP)
# --------------------------------------------------------------------------- #


def _make_server(tmp_path, **kwargs) -> ConfigWebServer:
    config_path = tmp_path / "config.toml"
    config_path.write_text("controlled_marker_ids = [1]\n", encoding="utf-8")
    return ConfigWebServer(
        config_path=str(config_path),
        host="127.0.0.1",
        port=_find_free_tcp_port(),
        system_name="T",
        **kwargs,
    )


def test_get_network_config_swallows_provider_error(tmp_path) -> None:
    def _boom(iface):
        raise RuntimeError("provider down")

    srv = _make_server(tmp_path, network_config_provider=_boom)
    assert srv.get_network_config() is None


def test_apply_network_without_handler_is_unavailable(tmp_path) -> None:
    srv = _make_server(tmp_path)
    result = srv.apply_network("eth0", object())
    assert result.ok is False and "not available" in result.message


def test_apply_network_swallows_handler_error(tmp_path) -> None:
    def _boom(iface, config):
        raise RuntimeError("kaboom")

    srv = _make_server(tmp_path, network_apply_handler=_boom)
    result = srv.apply_network("eth0", object())
    assert result.ok is False and "kaboom" in result.message


def test_renew_network_without_handler_is_unavailable(tmp_path) -> None:
    srv = _make_server(tmp_path)
    result = srv.renew_network("eth0")
    assert result.ok is False and "not available" in result.message


def test_renew_network_swallows_handler_error(tmp_path) -> None:
    def _boom(iface):
        raise RuntimeError("nope")

    srv = _make_server(tmp_path, network_renew_handler=_boom)
    result = srv.renew_network("eth0")
    assert result.ok is False and "nope" in result.message
