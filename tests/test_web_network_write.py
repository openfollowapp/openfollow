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
        self.omit_active = False
        self.renewed: list[str] = []
        self.apply_result = ApplyResult(ok=True)
        self.renew_result = ApplyResult(ok=True)
        self.provide_rows = True
        self.supports_vlans = True
        self.vlans: list[dict] = []
        self.vlans_created: list[tuple[str, int]] = []
        self.vlans_deleted: list[str] = []
        self.vlan_create_result = ApplyResult(ok=True, message="Created.")
        self.vlan_delete_result = ApplyResult(ok=True, message="Deleted.")

    def config_provider(self, iface: str | None = None) -> dict | None:
        if not self.interfaces:
            return {"interfaces": [], "writable": self.writable, "backend": "fake"}
        if self.omit_active:
            # A snapshot that lists interfaces but names no active one: the
            # write path must not read a bind target out of it.
            return {"interfaces": self.interfaces, "writable": self.writable, "backend": "fake"}
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

    def interfaces_provider(self) -> list[dict]:
        """What the services layer emits for the interface list.

        Wired into the fixture so the list tests exercise the real row shape
        instead of the synthesised fallback ``_build_network_form_context``
        falls back to when no provider is present. Set ``provide_rows=False``
        to exercise that fallback.
        """
        if not self.provide_rows:
            return []
        return [
            {
                "name": name,
                "is_up": True,
                "address": self.address if name == self.interfaces[0] else "",
                "prefix": self.prefix if name == self.interfaces[0] else None,
                "subnet_mask": self.subnet_mask if name == self.interfaces[0] else "",
                "method": self.method,
            }
            for name in self.interfaces
        ]

    def apply_handler(self, iface: str, config: object) -> ApplyResult:
        self.applied.append((iface, config))
        return self.apply_result

    def renew_handler(self, iface: str) -> ApplyResult:
        self.renewed.append(iface)
        return self.renew_result

    def vlan_provider(self) -> dict:
        return {"supported": self.supports_vlans, "vlans": list(self.vlans)}

    def vlan_create_handler(self, parent: str, vlan_id: int) -> ApplyResult:
        self.vlans_created.append((parent, vlan_id))
        return self.vlan_create_result

    def vlan_delete_handler(self, name: str) -> ApplyResult:
        self.vlans_deleted.append(name)
        return self.vlan_delete_result


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
        network_interfaces_provider=fake.interfaces_provider,
        network_apply_handler=fake.apply_handler,
        network_renew_handler=fake.renew_handler,
        network_vlan_provider=fake.vlan_provider,
        network_vlan_create_handler=fake.vlan_create_handler,
        network_vlan_delete_handler=fake.vlan_delete_handler,
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


def test_apply_unknown_iface_touches_nothing(net_server) -> None:
    """An interface the host does not have must not be silently redirected onto
    the active one. Substituting reconfigures and bounces the NIC the operator
    is connected over - on a station that means PSN, OSC and the beacon all drop
    - and the page that posted it had only gone stale behind a pulled adapter."""
    fake, base = net_server
    status, body, headers = _post_resp(
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
    assert fake.applied == []  # neither "bogus0" nor the active interface
    assert "hx-redirect" not in {k.lower() for k in headers}
    assert "bogus0 is not present" in body
    assert "Scan" in body


def test_apply_unknown_iface_message_is_bounded(net_server) -> None:
    """The rejected name is echoed back, and a POST is not bound by IFNAMSIZ."""
    fake, base = net_server
    _status, body, _headers = _post_resp(
        base,
        "/section/network/apply",
        {"iface": "b" * 400, "method": "dhcp"},
    )
    assert fake.applied == []
    assert "b" * 400 not in body
    assert "b" * 15 in body


def test_apply_blank_iface_still_defaults_to_active(net_server) -> None:
    """Blank is the form's own default, not a forged value - it must keep
    resolving to the active interface or Apply breaks on the ordinary path."""
    fake, base = net_server
    status, _body, _headers = _post_resp(
        base,
        "/section/network/apply",
        {
            "iface": "",
            "method": "static",
            "address": "192.168.1.50",
            "subnet_mask": "255.255.255.0",
        },
    )
    assert status == 200
    assert [iface for iface, _cfg in fake.applied] == ["eth0"]


def test_apply_snapshot_without_an_active_interface_touches_nothing(net_server) -> None:
    """The snapshot names the interfaces but not which one is active, so there
    is no target to write to. Falling through to the adapter with whatever
    ``active_interface`` happened to be missing would apply somewhere nobody
    chose."""
    fake, base = net_server
    fake.omit_active = True
    status, body, _headers = _post_resp(
        base,
        "/section/network/apply",
        {"iface": "", "method": "dhcp"},
    )
    assert status == 200
    assert fake.applied == []
    assert "not available on this host" in body


def test_renew_unknown_iface_touches_nothing(net_server) -> None:
    """Same substitution, same consequence: renewing the lease on the NIC the
    operator arrived over drops the session they are holding."""
    fake, base = net_server
    _status, body, _headers = _post_resp(
        base,
        "/section/network/renew",
        {"iface": "bogus0"},
    )
    assert fake.renewed == []
    assert "bogus0 is not present" in body


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


# --------------------------------------------------------------------------- #
# Interface list (replaced the single Interface picker)
# --------------------------------------------------------------------------- #


def test_status_lists_every_interface(net_server) -> None:
    """The old picker showed one adapter at a time, so a multi-NIC station's
    layout was invisible. Every interface is now on screen at once."""
    _fake, base = net_server
    status, body = _get(base, "/section/network/status")
    assert status == 200
    assert "Interfaces on this station" in body
    assert "<code>eth0</code>" in body
    assert "<code>wlan0</code>" in body


def test_active_interface_is_expanded_and_others_offer_configure(net_server) -> None:
    """One interface is always the current one, so its detail is open; the
    others carry the button that moves the expansion to them."""
    _fake, base = net_server
    _status, body = _get(base, "/section/network/status")
    # eth0 is FakeNetwork's active interface: expanded, so no button of its own.
    assert "Configure <code>eth0</code>" in body
    # Assert on the button, not the bare path – the 5s poll also carries the
    # expanded interface in its URL, so a substring check would match that.
    assert '/section/network/status/wlan0"' in body
    buttons = [seg for seg in body.split("<button") if ">Configure</button>" in seg]
    assert any("/section/network/status/wlan0" in b for b in buttons)
    assert not any("/section/network/status/eth0" in b for b in buttons)


def test_view_mode_can_expand_an_interface_read_only(net_server) -> None:
    """DNS and lease live in the detail, so View mode has to be able to open a
    row - otherwise reading a value would mean entering Edit mode."""
    _fake, base = net_server
    status, body = _get(base, "/section/network/status/wlan0")
    assert status == 200
    assert "Configure <code>wlan0</code>" in body
    assert "disabled" in body
    assert ">Apply<" not in body


def test_configure_expands_the_named_interface(net_server) -> None:
    """The interface is named in the path, so which adapter is being edited
    can't be ambiguous."""
    _fake, base = net_server
    status, body = _get(base, "/section/network/edit/wlan0")
    assert status == 200
    assert "Configure <code>wlan0</code>" in body
    # ... and the form still carries it to /apply exactly as before.
    assert 'name="iface" value="wlan0"' in body


def test_unknown_interface_falls_back_to_active(net_server) -> None:
    """A stale or forged interface name must not reach the privileged write
    path – it sanitises to the active interface, as the picker did."""
    _fake, base = net_server
    status, body = _get(base, "/section/network/edit/../../etc/passwd")
    assert status in (200, 404)
    if status == 200:
        assert 'name="iface" value="eth0"' in body


def test_no_interfaces_renders_empty_list_not_a_crash(tmp_path, monkeypatch) -> None:
    """A host with no adapters must render the card, not raise."""
    for attr in ("BeaconSender", "BeaconReceiver"):
        monkeypatch.setattr(getattr(discovery_module, attr), "start", lambda self: None)
        monkeypatch.setattr(getattr(discovery_module, attr), "stop", lambda self: None)
    fake = FakeNetwork(interfaces=())
    port = _find_free_tcp_port()
    config_path = tmp_path / "config.toml"
    config_path.write_text("controlled_marker_ids = [1]\n", encoding="utf-8")
    server = ConfigWebServer(
        config_path=str(config_path),
        host="127.0.0.1",
        port=port,
        system_name="TestSystem",
        network_config_provider=fake.config_provider,
    )
    server.start()
    try:
        assert _wait_for_port(port)
        status, body = _get(f"http://127.0.0.1:{port}", "/section/network/status")
        assert status == 200
        assert "unavailable" in body
    finally:
        server.stop()


def test_interface_list_uses_the_richer_provider_when_wired(tmp_path, monkeypatch) -> None:
    """With the multi-interface provider wired, every row carries its own
    address and method – not just the active one."""
    for attr in ("BeaconSender", "BeaconReceiver"):
        monkeypatch.setattr(getattr(discovery_module, attr), "start", lambda self: None)
        monkeypatch.setattr(getattr(discovery_module, attr), "stop", lambda self: None)
    fake = FakeNetwork()
    calls: list[int] = []

    def _interfaces() -> list[dict]:
        calls.append(1)
        return [
            {"name": "eth0", "address": "10.0.0.5", "prefix": 24, "method": "dhcp", "is_up": True},
            {"name": "wlan0", "address": "172.16.4.20", "prefix": 24, "method": "static", "is_up": True},
        ]

    port = _find_free_tcp_port()
    config_path = tmp_path / "config.toml"
    config_path.write_text("controlled_marker_ids = [1]\n", encoding="utf-8")
    server = ConfigWebServer(
        config_path=str(config_path),
        host="127.0.0.1",
        port=port,
        system_name="TestSystem",
        network_config_provider=fake.config_provider,
        network_interfaces_provider=_interfaces,
    )
    server.start()
    try:
        assert _wait_for_port(port)
        base = f"http://127.0.0.1:{port}"
        _status, body = _get(base, "/section/network/status")
        # wlan0 is not the active interface, yet its own address is shown.
        assert "172.16.4.20" in body
        assert "Static" in body
        # A second render inside the TTL window reuses the snapshot rather
        # than shelling out to the backend again.
        before = len(calls)
        _get(base, "/section/network/status")
        assert len(calls) == before
        # Scan bypasses the cache so a freshly plugged NIC appears at once.
        _get(base, "/section/network/status?scan=1")
        assert len(calls) == before + 1
    finally:
        server.stop()


# --------------------------------------------------------------------------- #
# Expanded-row survival: poll, Cancel and Scan must not move the expansion
# --------------------------------------------------------------------------- #


def test_view_poll_carries_the_expanded_interface(net_server) -> None:
    """The 5s poll used to GET the iface-less route, so a row opened in View
    mode collapsed back to the active interface every five seconds and could
    not be read."""
    _fake, base = net_server
    status, body = _get(base, "/section/network/status/wlan0")
    assert status == 200
    assert "/section/network/status/wlan0" in body
    assert "every 5s" in body


def test_cancel_returns_to_view_mode_on_the_same_row(net_server) -> None:
    """Cancel targeted /section/network/edit, which re-rendered the editor –
    leaving Edit mode with no exit short of a page reload."""
    _fake, base = net_server
    status, body = _get(base, "/section/network/edit/wlan0")
    assert status == 200
    cancel = body[body.index(">Cancel<") - 400 : body.index(">Cancel<")]
    assert "/section/network/status/wlan0" in cancel
    assert "/section/network/edit" not in cancel


def test_scan_keeps_the_open_interface(net_server) -> None:
    """Scan dropped the iface segment, so re-reading the adapter list moved the
    expansion and discarded anything typed into the editor."""
    _fake, base = net_server
    _status, body = _get(base, "/section/network/edit/wlan0")
    assert "/section/network/edit/wlan0?scan=1" in body


def test_interface_rows_come_from_the_provider(net_server) -> None:
    """Exercises the real provider row shape rather than the synthesised
    fallback the card uses when no provider is wired."""
    _fake, base = net_server
    _status, body = _get(base, "/section/network/status")
    assert "eth0" in body and "wlan0" in body
    assert "10.0.0.5" in body


def test_card_synthesises_rows_when_no_interface_provider_is_wired(net_server) -> None:
    """A build without the richer provider (or one whose backend read failed)
    still has to render every adapter - the card degrades to the single-
    interface snapshot rather than showing an empty list."""
    fake, base = net_server
    fake.provide_rows = False
    # ?scan=1 bypasses the TTL cache the earlier requests populated.
    status, body = _get(base, "/section/network/status?scan=1")
    assert status == 200
    assert "eth0" in body and "wlan0" in body
    # Only the open interface carries detail on this path.
    assert "10.0.0.5" in body


def test_a_pending_apply_does_not_redirect_to_a_dead_address(net_server) -> None:
    """The interface never came up, so nothing is serving the new address.
    Redirecting there loses the UI and discards the explanation with the body."""
    fake, base = net_server
    fake.apply_result = ApplyResult(ok=True, pending=True, message="Saved. eth0 has no link yet.")
    status, body, headers = _post_resp(
        base,
        "/section/network/apply",
        {
            "iface": "eth0",
            "method": "static",
            "address": "192.168.9.9",
            "subnet_mask": "255.255.255.0",
        },
    )
    assert status == 200
    assert "hx-redirect" not in headers
    assert "no link yet" in body
    # The settings were still written - this is "saved", not "failed".
    assert fake.applied and fake.applied[0][0] == "eth0"


def test_a_pending_apply_does_not_advise_reconnecting(net_server) -> None:
    """The partial-failure path tells the operator to reconnect at the new
    address. For a pending apply that is the one thing they must not do."""
    fake, base = net_server
    fake.apply_result = ApplyResult(ok=True, pending=True, message="Saved. eth0 has no link yet.")
    _status, body, _headers = _post_resp(
        base,
        "/section/network/apply",
        {
            "iface": "eth0",
            "method": "static",
            "address": "192.168.9.9",
            "subnet_mask": "255.255.255.0",
        },
    )
    assert "Reconnect at" not in body
    assert "applied" not in body.lower()


def test_a_clean_apply_still_redirects(net_server) -> None:
    """The pending gate must not suppress the normal static-apply redirect."""
    fake, base = net_server
    fake.apply_result = ApplyResult(ok=True, message="Applied.")
    _status, _body, headers = _post_resp(
        base,
        "/section/network/apply",
        {
            "iface": "eth0",
            "method": "static",
            "address": "192.168.9.9",
            "subnet_mask": "255.255.255.0",
        },
    )
    assert "hx-redirect" in headers


def test_a_pending_apply_still_shows_its_warnings(net_server) -> None:
    """Pending is the one path that keeps the operator on the page in order to
    explain itself, so warnings dropped here are lost outright - there is no
    later redirect where they could resurface.

    Both backends can return pending with caveats (an ``nmcli con down``
    failure, a dhcpcd reload fallback), and those are the caveats that tell an
    operator the save is not the whole story.
    """
    fake, base = net_server
    fake.apply_result = ApplyResult(
        ok=True,
        pending=True,
        message="Saved; the settings take effect when eth0 has a link.",
        partial_failures=("dhcpcd -n: rebind refused (fell back to systemctl reload)",),
    )
    status, body = _post(
        base,
        "/section/network/apply",
        {
            "iface": "eth0",
            "method": "static",
            "address": "192.168.9.9",
            "subnet_mask": "255.255.255.0",
        },
    )
    assert status == 200
    assert "take effect when eth0 has a link" in body
    assert "rebind refused" in body, "the pending banner dropped its warnings"

# --------------------------------------------------------------------------- #
# VLAN create / delete
# --------------------------------------------------------------------------- #


def test_add_vlan_control_only_in_edit_mode(net_server) -> None:
    """Creating a link is a write, so it follows the same Edit-mode gate as
    every other write on this card."""
    _fake, base = net_server
    _status, view = _get(base, "/section/network/status")
    assert "+ Add VLAN" not in view
    _status, edit = _get(base, "/section/network/edit")
    assert "+ Add VLAN" in edit


def test_add_vlan_control_absent_on_a_backend_without_vlans(net_server) -> None:
    fake, base = net_server
    fake.supports_vlans = False
    _status, body = _get(base, "/section/network/edit")
    assert "+ Add VLAN" not in body
    assert "/section/network/vlan/create" not in body


def test_vlan_rows_carry_their_tag(net_server) -> None:
    fake, base = net_server
    fake.interfaces = ["eth0", "eth0.10"]
    fake.vlans = [{"name": "eth0.10", "parent": "eth0", "vlan_id": 10}]
    _status, body = _get(base, "/section/network/edit")
    assert "VLAN 10" in body


def test_a_vlan_is_not_offered_as_a_parent(net_server) -> None:
    """QinQ is out of scope, so a VLAN can't parent another one."""
    fake, base = net_server
    fake.interfaces = ["eth0", "eth0.10"]
    fake.vlans = [{"name": "eth0.10", "parent": "eth0", "vlan_id": 10}]
    _status, body = _get(base, "/section/network/edit")
    parent_block = body.split('name="vlan_parent"', 1)[1].split("</select>", 1)[0]
    assert "eth0" in parent_block
    assert "eth0.10" not in parent_block


def test_create_passes_parent_and_id_to_the_adapter(net_server) -> None:
    fake, base = net_server
    status, _body = _post(base, "/section/network/vlan/create", {"vlan_parent": "eth0", "vlan_id": "10"})
    assert status == 200
    assert fake.vlans_created == [("eth0", 10)]


def test_create_rejects_a_reserved_id_without_calling_the_adapter(net_server) -> None:
    fake, base = net_server
    status, body = _post(base, "/section/network/vlan/create", {"vlan_parent": "eth0", "vlan_id": "4095"})
    assert status == 200
    assert "VLAN ID must be" in body
    assert fake.vlans_created == []


def test_create_rejects_an_unknown_parent_without_calling_the_adapter(net_server) -> None:
    fake, base = net_server
    status, body = _post(base, "/section/network/vlan/create", {"vlan_parent": "eth9", "vlan_id": "10"})
    assert status == 200
    assert "not a network interface" in body
    assert fake.vlans_created == []


def test_create_rejects_a_duplicate_name_without_calling_the_adapter(net_server) -> None:
    fake, base = net_server
    fake.interfaces = ["eth0", "eth0.10"]
    fake.vlans = [{"name": "eth0.10", "parent": "eth0", "vlan_id": 10}]
    status, body = _post(base, "/section/network/vlan/create", {"vlan_parent": "eth0", "vlan_id": "10"})
    assert status == 200
    assert "already exists" in body
    assert fake.vlans_created == []


def test_create_refused_on_a_backend_without_vlans(net_server) -> None:
    fake, base = net_server
    fake.supports_vlans = False
    status, body = _post(base, "/section/network/vlan/create", {"vlan_parent": "eth0", "vlan_id": "10"})
    assert status == 200
    assert "cannot create VLAN interfaces" in body
    assert fake.vlans_created == []


def test_create_surfaces_an_adapter_failure(net_server) -> None:
    fake, base = net_server
    fake.vlan_create_result = ApplyResult(ok=False, message="parent device not found")
    status, body = _post(base, "/section/network/vlan/create", {"vlan_parent": "eth0", "vlan_id": "10"})
    assert status == 200
    assert "parent device not found" in body


def test_delete_passes_the_interface_to_the_adapter(net_server) -> None:
    fake, base = net_server
    fake.interfaces = ["eth0", "eth0.10"]
    fake.vlans = [{"name": "eth0.10", "parent": "eth0", "vlan_id": 10}]
    status, _body = _post(base, "/section/network/vlan/delete", {"iface": "eth0.10"})
    assert status == 200
    assert fake.vlans_deleted == ["eth0.10"]


def test_delete_refuses_a_non_vlan_interface(net_server) -> None:
    """The delete grant is a wildcarded ``nmcli con delete``, so the app layer
    is what keeps it off a physical NIC's profile."""
    fake, base = net_server
    status, body = _post(base, "/section/network/vlan/delete", {"iface": "eth0"})
    assert status == 200
    assert "is not a VLAN" in body
    assert fake.vlans_deleted == []


def test_delete_refuses_an_unknown_interface(net_server) -> None:
    fake, base = net_server
    status, body = _post(base, "/section/network/vlan/delete", {"iface": "eth9.10"})
    assert status == 200
    assert "is not a VLAN" in body
    assert fake.vlans_deleted == []


def test_delete_refused_on_a_backend_without_vlans(net_server) -> None:
    fake, base = net_server
    fake.supports_vlans = False
    status, body = _post(base, "/section/network/vlan/delete", {"iface": "eth0.10"})
    assert status == 200
    assert "cannot create VLAN interfaces" in body
    assert fake.vlans_deleted == []


def test_delete_surfaces_an_adapter_failure(net_server) -> None:
    fake, base = net_server
    fake.interfaces = ["eth0", "eth0.10"]
    fake.vlans = [{"name": "eth0.10", "parent": "eth0", "vlan_id": 10}]
    fake.vlan_delete_result = ApplyResult(ok=False, message="profile is in use")
    status, body = _post(base, "/section/network/vlan/delete", {"iface": "eth0.10"})
    assert status == 200
    assert "profile is in use" in body


def test_delete_control_only_renders_for_a_vlan_row(net_server) -> None:
    """The control lives inside the open editor so it can only ever be aimed
    at the interface named in that editor's header."""
    fake, base = net_server
    fake.interfaces = ["eth0", "eth0.10"]
    fake.vlans = [{"name": "eth0.10", "parent": "eth0", "vlan_id": 10}]
    _status, physical = _get(base, "/section/network/edit/eth0")
    assert "Delete VLAN" not in physical
    _status, vlan = _get(base, "/section/network/edit/eth0.10")
    assert "Delete VLAN" in vlan


def test_delete_refuses_the_interface_serving_this_request(net_server, monkeypatch) -> None:
    """Deleting the interface the browser arrived on cuts the operator's own
    session, and the page they would need to undo it is the one that just
    became unreachable. The test server binds loopback, where
    ``request_local_iface`` correctly reports no interface, so that single
    seam is patched to stand in for a real NIC connection."""
    import openfollow.web.routes as routes_module

    fake, base = net_server
    fake.interfaces = ["eth0", "eth0.10"]
    fake.vlans = [{"name": "eth0.10", "parent": "eth0", "vlan_id": 10}]
    monkeypatch.setattr(routes_module, "request_local_iface", lambda _environ: "eth0.10")
    status, body = _post(base, "/section/network/vlan/delete", {"iface": "eth0.10"})
    assert status == 200
    assert "This session is connected over eth0.10" in body
    assert fake.vlans_deleted == []


def test_delete_allows_a_vlan_this_request_did_not_arrive_on(net_server, monkeypatch) -> None:
    import openfollow.web.routes as routes_module

    fake, base = net_server
    fake.interfaces = ["eth0", "eth0.10", "eth0.20"]
    fake.vlans = [
        {"name": "eth0.10", "parent": "eth0", "vlan_id": 10},
        {"name": "eth0.20", "parent": "eth0", "vlan_id": 20},
    ]
    monkeypatch.setattr(routes_module, "request_local_iface", lambda _environ: "eth0.10")
    status, _body = _post(base, "/section/network/vlan/delete", {"iface": "eth0.20"})
    assert status == 200
    assert fake.vlans_deleted == ["eth0.20"]


def test_get_network_vlans_without_provider_reports_unsupported(tmp_path) -> None:
    srv = _make_server(tmp_path)
    assert srv.get_network_vlans() == {"supported": False, "vlans": []}


def test_get_network_vlans_swallows_provider_error(tmp_path) -> None:
    """A failed read must not leave the card offering controls whose backend
    just proved it can't answer."""

    def _boom():
        raise RuntimeError("provider down")

    srv = _make_server(tmp_path, network_vlan_provider=_boom)
    assert srv.get_network_vlans() == {"supported": False, "vlans": []}


def test_create_network_vlan_without_handler_is_unavailable(tmp_path) -> None:
    srv = _make_server(tmp_path)
    result = srv.create_network_vlan("eth0", 10)
    assert result.ok is False and "not available" in result.message


def test_create_network_vlan_swallows_handler_error(tmp_path) -> None:
    def _boom(parent, vlan_id):
        raise RuntimeError("kaboom")

    srv = _make_server(tmp_path, network_vlan_create_handler=_boom)
    result = srv.create_network_vlan("eth0", 10)
    assert result.ok is False and "kaboom" in result.message


def test_delete_network_vlan_without_handler_is_unavailable(tmp_path) -> None:
    srv = _make_server(tmp_path)
    result = srv.delete_network_vlan("eth0.10")
    assert result.ok is False and "not available" in result.message


def test_delete_network_vlan_swallows_handler_error(tmp_path) -> None:
    def _boom(name):
        raise RuntimeError("kaboom")

    srv = _make_server(tmp_path, network_vlan_delete_handler=_boom)
    result = srv.delete_network_vlan("eth0.10")
    assert result.ok is False and "kaboom" in result.message
