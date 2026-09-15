# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Web write path for Pi network settings.

Drives the editable network form + apply/renew endpoints end-to-end against
a live ConfigWebServer wired with a fake network backend, asserting the form
reuses ``validate_apply`` and the adapter ``apply_ipv4`` / ``renew_lease``
contract surfaced through the server's provider/handler callbacks.
"""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request

import pytest

import openfollow.web.discovery as discovery_module
from openfollow.network.adapter import ApplyResult, Ipv4Method
from openfollow.network.validate import vlan_interface_name
from openfollow.web.routes import _port_suffix
from openfollow.web.server import ConfigWebServer
from tests._ports import free_tcp_port, live_on_free_port

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
        # Which interface is answering the browser. Loopback in a test, so the
        # real lookup can never report one; the fixture patches it to this.
        self.session_iface = ""
        self.session_address = ""

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

        def _row(index: int, name: str) -> dict:
            if index == 0:
                return {
                    "name": name,
                    "is_up": True,
                    "address": self.address,
                    "prefix": self.prefix,
                    "subnet_mask": self.subnet_mask,
                    "method": self.method,
                    "router": self.router,
                    "dns": list(self.dns),
                    "lease_display": self.lease_display,
                }
            # Deliberately unlike the active interface's, so a row rendering
            # the active one's detail instead of its own is visible.
            return {
                "name": name,
                "is_up": True,
                "address": "10.9.9.9",
                "prefix": 16,
                "subnet_mask": "255.255.0.0",
                "method": "static",
                "router": "10.9.0.1",
                "dns": ["9.9.9.9"],
                "lease_display": None,
            }

        return [_row(i, name) for i, name in enumerate(self.interfaces)]

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
        if self.vlan_create_result.ok:
            # The card is re-rendered from the interface list straight after,
            # so the new link has to be in it - that is the whole point of the
            # forced re-read.
            name = vlan_interface_name(parent, vlan_id)
            self.interfaces.append(name)
            self.vlans.append({"name": name, "parent": parent, "vlan_id": vlan_id})
        return self.vlan_create_result

    def vlan_delete_handler(self, name: str) -> ApplyResult:
        self.vlans_deleted.append(name)
        return self.vlan_delete_result


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


def _outer_html(body: str, marker: str, tag: str = "div") -> str:
    """The element whose start tag carries ``marker``, children included.

    What the poll may clobber is what it *contains*; where the markup happens
    to sit in the source proves nothing, so the extent is counted.
    """
    at = body.rindex("<" + tag, 0, body.index(marker))
    open_tag, close_tag = "<" + tag, "</" + tag
    depth, pos = 0, at
    while True:
        nxt_open, nxt_close = body.find(open_tag, pos), body.find(close_tag, pos)
        assert nxt_close != -1, f"unclosed <{tag}>"
        if nxt_open != -1 and nxt_open < nxt_close:
            depth, pos = depth + 1, nxt_open + len(open_tag)
            continue
        depth, pos = depth - 1, nxt_close + len(close_tag)
        if depth == 0:
            return body[at:pos]


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
    # The server binds loopback, so the real lookups always answer "unknown".
    # Routing them through the fake is what lets a test place the session on a
    # named interface - the one thing that gates the post-apply redirect.
    import openfollow.web.routes as routes_module

    monkeypatch.setattr(routes_module, "request_local_iface", lambda _environ: fake.session_iface)
    monkeypatch.setattr(routes_module, "request_local_addr", lambda _environ: fake.session_address)
    config_path = tmp_path / "config.toml"
    config_path.write_text("controlled_marker_ids = [1]\n", encoding="utf-8")
    with live_on_free_port(
        lambda port: ConfigWebServer(
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
    ) as (_server, base):
        yield fake, base


# --------------------------------------------------------------------------- #
# View / edit toggle
# --------------------------------------------------------------------------- #


def test_status_view_offers_edit_per_row_and_writes_nothing(net_server) -> None:
    """A writable host carries no card-level mode: every row reads out its
    settings with its own Edit button, and none of them can be submitted."""
    _fake, base = net_server
    status, body = _get(base, "/section/network/status")
    assert status == 200
    assert 'id="network-config-section"' in body
    assert body.count('data-mode="view"') == 2
    assert 'data-mode="edit"' not in body
    assert body.count("/section/network/edit/") == 2  # one Edit per row
    assert ">Apply<" not in body
    assert "disabled" in body
    # The card live-polls itself; the Backend field is dropped (not user-facing).
    assert "/section/network/status" in body and "every 5s" in body
    assert "Backend" not in body
    # The view shows the current address even for DHCP, so the operator can
    # read the lease-assigned IP without entering an editor.
    assert 'name="address"' in body
    assert "10.0.0.5" in body


def test_edit_makes_exactly_the_named_row_writable(net_server) -> None:
    """Editing is per row, so opening one leaves every other adapter
    read-only - two can never be half-edited against each other."""
    _fake, base = net_server
    status, body = _get(base, "/section/network/edit/eth0")
    assert status == 200
    assert ">Apply<" in body
    assert "Renew DHCP lease" in body
    assert ">Cancel<" in body
    assert body.count('data-mode="edit"') == 1
    assert body.count('data-mode="view"') == 1
    # The editable row is the one named in the path.
    edit_row = body.split('data-adv-key="net-iface-eth0"', 1)[1].split("</details>", 1)[0]
    assert 'data-mode="edit"' in edit_row


def test_method_fields_are_all_rendered_and_tagged_for_the_client(net_server) -> None:
    """Switching method no longer round-trips: every field is in the DOM and
    the row's ``data-method`` is what decides which ones apply. A field the
    method does not allow is disabled client-side so it cannot post."""
    _fake, base = net_server
    _status, body = _get(base, "/section/network/edit/eth0")
    edit_row = body.split('data-adv-key="net-iface-eth0"', 1)[1].split("</details>", 1)[0]
    assert 'data-method="dhcp"' in edit_row
    for field in ('name="address"', 'name="subnet_mask"', 'name="router"'):
        assert field in edit_row
    # The hooks the stylesheet keys off to hide what the method excludes.
    assert 'class="group net-addressing"' in edit_row
    assert edit_row.count("net-static-only") == 4  # subnet + router, label and input each
    assert "net-dhcp-only" in edit_row  # the lease group, hidden for static


# --------------------------------------------------------------------------- #
# Apply
# --------------------------------------------------------------------------- #


def test_apply_static_calls_adapter_and_redirects_to_new_ip(net_server) -> None:
    fake, base = net_server
    fake.session_iface = "eth0"  # the browser is answered on the interface being changed
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
    # Reload the UI at the new static address - the old one just went away.
    assert "192.168.1.50" in headers.get("hx-redirect", "")


def test_apply_to_another_interface_does_not_move_the_browser(net_server) -> None:
    """Applying to an adapter that is not answering this session leaves the
    session untouched, so redirecting to the new address strands the operator
    on a network they may not be on. Linux answers for that address on
    whatever interface they *are* on, so the move succeeds and hides it - a
    VLAN given a link-local address took the browser with it."""
    fake, base = net_server
    fake.session_iface = "wlan0"  # the browser is answered on the other adapter
    _status, _body, headers = _post_resp(
        base,
        "/section/network/apply",
        {
            "iface": "eth0",
            "method": "static",
            "address": "169.254.32.55",
            "subnet_mask": "255.255.0.0",
        },
    )
    assert [iface for iface, _cfg in fake.applied] == ["eth0"]  # the write still happened
    assert "hx-redirect" not in {k.lower() for k in headers}


def test_the_session_interface_is_read_before_the_address_is_changed(net_server, monkeypatch) -> None:
    """Applying to the session's own interface is what removes the address the
    lookup resolves, so asking afterwards answers "unknown" - and the redirect
    would be suppressed in exactly the single-NIC case it exists for."""
    import openfollow.web.routes as routes_module

    fake, base = net_server
    calls: list[str] = []

    def _iface_before_and_after(_environ: str) -> str:
        # Before the apply the address is still on eth0; afterwards it is gone
        # from every interface, which is what ``get_iface_for_ip`` reports as "".
        answer = "" if fake.applied else "eth0"
        calls.append(answer)
        return answer

    monkeypatch.setattr(routes_module, "request_local_iface", _iface_before_and_after)
    _status, _body, headers = _post_resp(
        base,
        "/section/network/apply",
        {
            "iface": "eth0",
            "method": "static",
            "address": "192.168.1.50",
            "subnet_mask": "255.255.255.0",
        },
    )
    assert len(fake.applied) == 1
    assert calls[0] == "eth0"  # read while the old address was still up
    assert "192.168.1.50" in headers.get("hx-redirect", "")


def test_apply_with_no_known_session_interface_does_not_redirect(net_server) -> None:
    """Reached over IPv6, or from an address that is none of this host's, the
    session interface is unknown. Redirecting on a guess is how the operator
    ends up somewhere they cannot get back from."""
    fake, base = net_server
    fake.session_iface = ""
    _status, _body, headers = _post_resp(
        base,
        "/section/network/apply",
        {
            "iface": "eth0",
            "method": "static",
            "address": "192.168.1.50",
            "subnet_mask": "255.255.255.0",
        },
    )
    assert len(fake.applied) == 1
    assert "hx-redirect" not in {k.lower() for k in headers}


def test_the_session_row_names_the_address_it_is_answering(net_server) -> None:
    """The warning used to claim the operator was connected *over* this
    interface. It is the address that belongs to the interface, not
    necessarily the cable - saying so is what makes it checkable."""
    fake, base = net_server
    fake.session_iface = "wlan0"
    fake.session_address = "169.254.32.55"
    _status, body = _get(base, "/section/network/status")
    wlan = body.split('data-adv-key="net-iface-wlan0"', 1)[1].split("</details>", 1)[0]
    eth0 = body.split('data-adv-key="net-iface-eth0"', 1)[1].split("</details>", 1)[0]
    assert "This session" in wlan
    # The notice itself, not the badge's tooltip - both carry the address, so
    # asserting on the body alone passes with the old topology claim in place.
    notice = wlan.split('class="notice warning"', 1)[1].split("</div>", 1)[0]
    assert "answering your browser at 169.254.32.55" in notice
    assert "connected over this interface" not in notice
    assert "169.254.32.55" in wlan.split("ia-badge session", 1)[1].split(">", 1)[0]
    assert "This session" not in eth0
    assert "answering your browser" not in eth0


def test_a_rejected_address_stays_out_of_the_summary_line(net_server) -> None:
    """The fields carry back what was typed so it can be corrected. The
    summary above them reports what the adapter holds - rendering the rejected
    value there presents it as live, with the previous prefix appended and an
    up status dot beside it."""
    fake, base = net_server
    _status, body, _headers = _post_resp(
        base,
        "/section/network/apply",
        {
            "iface": "eth0",
            "method": "static",
            "address": "192.168.1.999",
            "subnet_mask": "255.255.255.0",
        },
    )
    assert fake.applied == []  # validate_apply refused it
    row = body.split('data-adv-key="net-iface-eth0"', 1)[1].split("</details>", 1)[0]
    summary = row.split("</summary>", 1)[0]
    assert "192.168.1.999" not in summary
    assert "10.0.0.5" in summary  # what the adapter actually holds
    # ... and the editable field still offers it back for correction.
    assert 'value="192.168.1.999"' in row.split("</summary>", 1)[1]


def test_the_blind_reload_is_armed_only_on_the_session_row(net_server) -> None:
    """The 6s timer navigates to the address the row was given, and fires when
    no response has arrived. On a row that is not answering this session an
    apply slower than the timer would move the browser to an adapter the
    operator may not be able to reach - the very move the server gate refuses."""
    fake, base = net_server
    fake.session_iface = "wlan0"
    _status, body = _get(base, "/section/network/edit/eth0")
    eth0 = body.split('data-adv-key="net-iface-eth0"', 1)[1].split("</details>", 1)[0]
    assert ">Apply<" in eth0  # it is the editable row
    assert "netScheduleReload" not in eth0

    _status, body = _get(base, "/section/network/edit/wlan0")
    wlan = body.split('data-adv-key="net-iface-wlan0"', 1)[1].split("</details>", 1)[0]
    assert "netScheduleReload" in wlan


def test_an_editor_warns_when_the_session_interface_is_unknown(net_server) -> None:
    """Reached over IPv6, or at an address that is none of this host's, no row
    can be marked - so the caution goes on every editor. Saying nothing is the
    one outcome that lets the operator cut their own session unwarned."""
    fake, base = net_server
    fake.session_iface = ""
    _status, body = _get(base, "/section/network/edit/eth0")
    eth0 = body.split('data-adv-key="net-iface-eth0"', 1)[1].split("</details>", 1)[0]
    assert "may be the one carrying this web session" in eth0
    # Read-only rows are not about to apply anything, so they stay quiet.
    wlan = body.split('data-adv-key="net-iface-wlan0"', 1)[1].split("</details>", 1)[0]
    assert "may be the one carrying this web session" not in wlan

    # With the session placed, the row that owns the address says so instead.
    fake.session_iface = "eth0"
    fake.session_address = "10.0.0.5"
    _status, body = _get(base, "/section/network/edit/eth0")
    eth0 = body.split('data-adv-key="net-iface-eth0"', 1)[1].split("</details>", 1)[0]
    assert "may be the one carrying this web session" not in eth0
    assert "answering your browser at 10.0.0.5" in eth0


def test_the_add_vlan_form_submits_rather_than_navigating(net_server) -> None:
    """The block is its own form with one implicit-submission-blocking field
    and no submit button of its own - Enter in the VLAN ID would fall through
    to a native GET on the page URL, creating nothing and losing the entry."""
    _fake, base = net_server
    _status, body = _get(base, "/section/network/status")
    form = body.split('class="ia-vlan-add"', 1)[1].split(">", 1)[0]
    assert 'hx-post="/section/network/vlan/create"' in form
    assert 'hx-trigger="submit"' in form
    assert '<button type="submit" class="save-btn">Create</button>' in body


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
    fake.session_iface = "eth0"
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
    assert 'data-mode="edit"' not in body  # back to every row read-only


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
    assert 'data-mode="edit"' not in body  # back to every row read-only


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


def test_read_only_host_offers_no_edit_at_all(net_server) -> None:
    fake, base = net_server
    fake.writable = False
    _, body = _get(base, "/section/network/status")
    assert "/section/network/edit/" not in body  # no per-row Edit button
    assert ">Apply<" not in body
    # Read-only mode bar points the operator at the on-screen menu instead.
    assert 'class="net-mode-pill readonly"' in body
    assert "on-screen Settings menu" in body


@pytest.mark.parametrize(
    "path",
    [
        "/section/network/edit",  # card-level Edit mode; editing is per row now
        "/section/network/status/eth0",  # expansion is the browser's, not a route
    ],
)
def test_retired_routes_are_gone(net_server, path: str) -> None:
    """Left behind they would render a card no control links to - one with an
    interface made writable by a URL rather than by its own Edit button."""
    _fake, base = net_server
    status, _body = _get(base, path)
    assert status == 404


def test_the_method_rerender_endpoint_is_gone(net_server) -> None:
    """Switching method is client-side: every field is already in the DOM, so
    a round-trip that discarded unsaved input bought nothing."""
    _fake, base = net_server
    status, _body = _post(base, "/section/network", {"iface": "eth0", "method": "static"})
    assert status in (404, 405)


def test_no_provider_renders_unavailable(tmp_path, monkeypatch) -> None:
    """A server built without the network handlers (older wiring / tests)
    renders the unavailable state instead of raising."""
    for attr in ("BeaconSender", "BeaconReceiver"):
        monkeypatch.setattr(getattr(discovery_module, attr), "start", lambda self: None)
        monkeypatch.setattr(getattr(discovery_module, attr), "stop", lambda self: None)
    config_path = tmp_path / "config.toml"
    config_path.write_text("controlled_marker_ids = [1]\n", encoding="utf-8")
    with live_on_free_port(
        lambda port: ConfigWebServer(
            config_path=str(config_path),
            host="127.0.0.1",
            port=port,
            system_name="TestSystem",
        )
    ) as (_server, base):
        status, body = _get(base, "/section/network/status")
        assert status == 200
        assert "unavailable" in body
        # apply with no handler returns the not-available banner, not a 500.
        status, body = _post(
            base,
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
            base,
            "/section/network/renew",
            {
                "iface": "eth0",
            },
        )
        assert status == 200
        assert "not available" in body


# --------------------------------------------------------------------------- #
# ConfigWebServer method-level error handling (no HTTP)
# --------------------------------------------------------------------------- #


def _make_server(tmp_path, **kwargs) -> ConfigWebServer:
    config_path = tmp_path / "config.toml"
    config_path.write_text("controlled_marker_ids = [1]\n", encoding="utf-8")
    return ConfigWebServer(
        config_path=str(config_path),
        host="127.0.0.1",
        port=free_tcp_port(),
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
    assert "<code>eth0</code>" in body
    assert "<code>wlan0</code>" in body


def test_no_row_is_forced_open_on_an_ordinary_render(net_server) -> None:
    """Which rows are expanded is the browser's to remember, so a plain render
    must force none: the 5s poll re-renders this markup, and a forced row would
    reopen one the operator had closed, every five seconds."""
    _fake, base = net_server
    _status, body = _get(base, "/section/network/status")
    assert "data-adv-force-open" not in body
    # Each row still carries the key the browser remembers it under.
    assert 'data-adv-key="net-iface-eth0"' in body
    assert 'data-adv-key="net-iface-wlan0"' in body


def test_every_row_carries_its_own_detail(net_server) -> None:
    """The editor is rendered per row rather than fetched for one interface at
    a time, so a row must show its own router / DNS / lease and not the active
    interface's."""
    _fake, base = net_server
    status, body = _get(base, "/section/network/status")
    assert status == 200
    wlan = body.split('data-adv-key="net-iface-wlan0"', 1)[1].split("</details>", 1)[0]
    assert 'value="10.9.9.9"' in wlan  # its own address, not eth0's 10.0.0.5
    assert 'value="10.9.0.1"' in wlan  # its own router
    assert 'value="9.9.9.9"' in wlan  # its own DNS
    eth0 = body.split('data-adv-key="net-iface-eth0"', 1)[1].split("</details>", 1)[0]
    assert 'value="10.0.0.5"' in eth0
    assert "1h 00m" in eth0  # its own lease; wlan0 reports none
    assert "1h 00m" not in wlan


def test_the_named_interface_is_the_one_opened_and_written(net_server) -> None:
    """The interface is named in the path, so which adapter is being edited
    can't be ambiguous - and its row opens itself so the operator sees it."""
    _fake, base = net_server
    status, body = _get(base, "/section/network/edit/wlan0")
    assert status == 200
    wlan = body.split('data-adv-key="net-iface-wlan0"', 1)[1].split("</details>", 1)[0]
    assert "data-adv-force-open" in body.split('data-adv-key="net-iface-wlan0"', 1)[1].split(">", 1)[0]
    assert 'name="iface" value="wlan0"' in wlan
    assert ">Apply<" in wlan


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
    config_path = tmp_path / "config.toml"
    config_path.write_text("controlled_marker_ids = [1]\n", encoding="utf-8")
    with live_on_free_port(
        lambda port: ConfigWebServer(
            config_path=str(config_path),
            host="127.0.0.1",
            port=port,
            system_name="TestSystem",
            network_config_provider=fake.config_provider,
        )
    ) as (_server, base):
        status, body = _get(base, "/section/network/status")
        assert status == 200
        assert "unavailable" in body


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

    config_path = tmp_path / "config.toml"
    config_path.write_text("controlled_marker_ids = [1]\n", encoding="utf-8")
    with live_on_free_port(
        lambda port: ConfigWebServer(
            config_path=str(config_path),
            host="127.0.0.1",
            port=port,
            system_name="TestSystem",
            network_config_provider=fake.config_provider,
            network_interfaces_provider=_interfaces,
        )
    ) as (_server, base):
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


# --------------------------------------------------------------------------- #
# Expanded-row survival: poll, Cancel and Scan must not move the expansion
# --------------------------------------------------------------------------- #


def test_the_poll_swaps_the_interface_list_and_nothing_else(net_server) -> None:
    """Refreshing addresses is the whole of the tick's business.

    Swapping the card wholesale took the operator's own state with it, so the
    poll had to be held off while the Add VLAN form was open - and a held poll
    is a card showing addresses that have since moved on.
    """
    _fake, base = net_server
    status, body = _get(base, "/section/network/status")
    assert status == 200
    # No trigger filter: nothing is left for the poll to wait for.
    assert 'hx-trigger="every 5s"' in body
    assert 'hx-select="#net-iface-list" hx-target="#net-iface-list" hx-swap="outerHTML"' in body
    # Plain path: the poll names no interface, so it can't reopen a closed row.
    assert 'hx-get="/section/network/status" hx-trigger="every 5s"' in body


def test_the_polled_fragment_carries_no_operator_state(net_server) -> None:
    """The addresses are inside the swapped fragment; everything the operator
    put on screen themselves is outside it."""
    _fake, base = net_server
    _status, body = _get(base, "/section/network/status")
    swapped = _outer_html(body, 'id="net-iface-list"')
    assert "net-iface-row" in swapped
    assert "ia-vlan-add" not in swapped
    assert "+ Add VLAN" not in swapped
    assert "ia-legend" not in swapped


def test_a_refused_create_keeps_its_reason_and_its_entry_through_the_poll(net_server) -> None:
    """A refusal leaves the form open with what was typed and a banner saying
    why. Both sit outside the fragment the poll replaces, so the reason is
    still on screen five seconds later, beside the entry it is about."""
    _fake, base = net_server
    _status, body = _post(
        base,
        "/section/network/vlan/create",
        {"vlan_parent": "wlan0", "vlan_id": "99999"},
    )
    assert "between 1 and 4094" in body
    assert 'hx-select="#net-iface-list"' in body
    swapped = _outer_html(body, 'id="net-iface-list"')
    assert "between 1 and 4094" not in swapped
    assert 'value="99999"' not in swapped


def test_cancel_drops_the_card_back_to_read_only(net_server) -> None:
    """Cancel has to leave Edit; targeting an edit route would re-render the
    editor and leave no exit short of a page reload. The row itself stays
    expanded because that is the browser's state, not the server's."""
    _fake, base = net_server
    status, body = _get(base, "/section/network/edit/wlan0")
    assert status == 200
    cancel = body[body.index(">Cancel<") - 400 : body.index(">Cancel<")]
    assert '/section/network/status"' in cancel
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
    fake.session_iface = "eth0"
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


def test_add_vlan_is_offered_whenever_the_host_is_writable(net_server) -> None:
    """Creating a link is its own confirmed action, not an edit of some
    interface - and with editing now per row there is no card-level Edit mode
    left to gate it behind, which would leave it unreachable."""
    fake, base = net_server
    _status, view = _get(base, "/section/network/status")
    assert "+ Add VLAN" in view
    fake.writable = False
    _status, read_only = _get(base, "/section/network/status")
    assert "+ Add VLAN" not in read_only


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
    _status, body = _get(base, "/section/network/status")
    assert "VLAN 10" in body


def test_a_refused_create_keeps_what_was_entered(net_server) -> None:
    """The Add VLAN block is hidden until opened, so an error that re-rendered
    the card closed it and threw away the parent and id - the operator had to
    retype both to read the message that told them what was wrong."""
    _fake, base = net_server
    _status, body = _post(
        base,
        "/section/network/vlan/create",
        {"vlan_parent": "wlan0", "vlan_id": "99999"},
    )
    assert "between 1 and 4094" in body
    form = body.split('class="ia-vlan-add"', 1)[1]
    assert not form.split(">", 1)[0].strip().startswith("hidden")
    assert 'value="99999"' in body
    assert '<option value="wlan0" selected>' in body


def test_a_successful_create_closes_the_form_and_edits_nothing(net_server) -> None:
    """A VLAN write is not an edit of some other adapter: the card comes back
    read-only with the new row expanded, not with an interface made writable."""
    fake, base = net_server
    _status, body = _post(
        base,
        "/section/network/vlan/create",
        {"vlan_parent": "eth0", "vlan_id": "10"},
    )
    assert fake.vlans_created == [("eth0", 10)]
    assert 'data-mode="edit"' not in body
    form = body.split('class="ia-vlan-add"', 1)[1].split(">", 1)[0]
    assert "hidden" in form
    # The new interface's row is the one opened, so it can be given an address.
    opened = body.split('data-adv-key="net-iface-eth0.10"', 1)[1].split(">", 1)[0]
    assert "data-adv-force-open" in opened


def test_a_vlan_is_not_offered_as_a_parent(net_server) -> None:
    """QinQ is out of scope, so a VLAN can't parent another one."""
    fake, base = net_server
    fake.interfaces = ["eth0", "eth0.10"]
    fake.vlans = [{"name": "eth0.10", "parent": "eth0", "vlan_id": 10}]
    _status, body = _get(base, "/section/network/status")
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
