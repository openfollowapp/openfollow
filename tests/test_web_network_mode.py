# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Network mode on the web UI: the Simple card, the switch and Simple's interface picker."""

from __future__ import annotations

import json
import logging
import socket
import urllib.parse
import urllib.request
from types import SimpleNamespace

import pytest

import openfollow.net_utils as net_utils_module
from openfollow.configuration import AppConfig, OscDestinationConfig, OscDestinationsConfig, load_config, save_config
from openfollow.network.adapter import ApplyResult
from openfollow.web.routes import simple_mode_changes
from tests.test_web_network_write import _get, _post, net_server  # noqa: F401 - fixture

pytestmark = pytest.mark.integration


def _config_path(tmp_path) -> str:
    return str(tmp_path / "config.toml")


def _set(tmp_path, **fields) -> None:
    path = _config_path(tmp_path)
    cfg = load_config(path)
    for name, value in fields.items():
        parent, _, attr = name.rpartition(".")
        setattr(getattr(cfg, parent) if parent else cfg, attr, value)
    save_config(cfg, path)


def _saved(tmp_path) -> AppConfig:
    return load_config(_config_path(tmp_path))


@pytest.fixture(autouse=True)
def _no_host_route_probe(monkeypatch):
    """Automatic resolves to nothing unless a test says otherwise: never the host's own route."""
    monkeypatch.setattr(net_utils_module, "auto_station_iface", lambda: "")


@pytest.fixture()
def present(monkeypatch):
    """eth0 and wlan0 exist with addresses; Automatic resolves to eth0."""
    addrs = {
        "eth0": [SimpleNamespace(family=socket.AF_INET, address="10.0.0.5")],
        "wlan0": [SimpleNamespace(family=socket.AF_INET, address="10.9.9.9")],
    }
    monkeypatch.setattr(net_utils_module.psutil, "net_if_addrs", lambda: addrs)
    monkeypatch.setattr(net_utils_module, "auto_station_iface", lambda: "eth0")


def _restart_headers(headers: dict) -> dict:
    """What the answer tells the browser about a restart."""
    return {k: v for k, v in headers.items() if k.startswith("x-openfollow-restart")}


def _post_headers(base: str, path: str, data: dict) -> tuple[int, str, dict]:
    req = urllib.request.Request(
        f"{base}{path}",
        data=urllib.parse.urlencode(data, doseq=True).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode(), {k.lower(): v for k, v in r.headers.items()}
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), {}


# --------------------------------------------------------------------------- #
# The card
# --------------------------------------------------------------------------- #


def test_the_simple_card_shows_the_station_interface_alone(net_server, tmp_path, present) -> None:  # noqa: F811
    fake, base = net_server
    fake.vlans = [{"name": "wlan0", "parent": "eth0", "vlan_id": 20}]
    _set(tmp_path, network_mode="simple", interface_labels={"eth0": "Show LAN", "wlan0": "Spare"})
    _status, body = _get(base, "/section/network/status")
    assert 'name="network_mode" value="simple" checked' in body
    assert '<option value="" selected data-name="Automatic (Show LAN (eth0))">Automatic (Show LAN (eth0))<' in body
    assert '<option value="wlan0" data-name="Spare (wlan0)">Spare (wlan0) – 10.9.9.9</option>' in body
    # The confirm names the interface traffic leaves.
    assert 'data-from="Show LAN (eth0)"' in body
    assert body.count('class="net-iface-row"') == 1
    assert 'data-adv-key="net-iface-eth0"' in body
    # Named and labelled as in Advanced, with its Adapter block.
    assert '<span class="net-iface-name">Show LAN</span>' in body
    assert '<span class="net-iface-sub">eth0</span>' in body
    assert body.count("net-label-form") == 1
    assert 'name="label" maxlength="20"\n                                       value="Show LAN"' in body
    # A labelled adapter the card does not list is not called unplugged.
    assert "Not connected" not in body
    assert "+ Add VLAN" not in body
    assert "VLAN 20" not in body
    # A refused pick puts the picker back.
    assert 'hx-on::after-request="if (!event.detail.successful) this.reset()"' in body


def test_the_simple_card_follows_a_named_station_interface(net_server, tmp_path, present) -> None:  # noqa: F811
    _fake, base = net_server
    _set(tmp_path, network_mode="simple", psn_source_iface="wlan0")
    _status, body = _get(base, "/section/network/status")
    # Automatic names what it would pick, not the interface pinned now.
    assert '<option value="" data-name="Automatic (eth0)">Automatic (eth0)</option>' in body
    assert '<option value="wlan0" selected data-name="wlan0">wlan0 – 10.9.9.9</option>' in body
    assert 'data-from="wlan0"' in body
    assert 'data-adv-key="net-iface-wlan0"' in body
    assert 'data-adv-key="net-iface-eth0"' not in body


@pytest.mark.parametrize(("bind_host", "everywhere"), [("0.0.0.0", True), ("", True), ("10.0.0.5", False)])
def test_the_picker_says_whether_the_web_ui_listens_everywhere(
    net_server,  # noqa: F811
    tmp_path,
    present,
    monkeypatch,
    bind_host,
    everywhere,
) -> None:
    import openfollow.web.server as server_module

    # The listener decides, not the config: a pin waiting on a restart still holds it.
    monkeypatch.setattr(server_module.ConfigWebServer, "bind_host", property(lambda self: bind_host))
    _fake, base = net_server
    _set(tmp_path, network_mode="simple")
    _status, body = _get(base, "/section/network/status")
    assert ('data-from="eth0" data-web-everywhere>' in body) is everywhere
    assert ("data-web-everywhere" in body) is everywhere


def test_a_station_interface_that_is_gone_is_named(net_server, tmp_path, present) -> None:  # noqa: F811
    _fake, base = net_server
    _set(tmp_path, network_mode="simple", psn_source_iface="eth9", interface_labels={"eth9": "Old"})
    _status, body = _get(base, "/section/network/status")
    assert "Old (eth9) is not connected." in body
    assert '<option value="eth9" selected data-name="Old (eth9)">Old (eth9) – no address</option>' in body


def test_a_label_is_saved_on_the_simple_card(net_server, tmp_path, present) -> None:  # noqa: F811
    _fake, base = net_server
    _set(tmp_path, network_mode="simple")
    status, body = _post(base, "/section/network/label", {"iface": "eth0", "label": "Stage"})
    assert status == 200
    assert _saved(tmp_path).interface_labels == {"eth0": "Stage"}
    assert '<span class="net-iface-name">Stage</span>' in body
    assert '<option value="" selected data-name="Automatic (Stage (eth0))">Automatic (Stage (eth0))</option>' in body


def test_a_pinned_interface_the_backend_does_not_manage_is_not_called_unplugged(
    net_server,  # noqa: F811
    tmp_path,
    monkeypatch,
) -> None:
    addrs = {"br0": [SimpleNamespace(family=socket.AF_INET, address="198.51.100.2")]}
    monkeypatch.setattr(net_utils_module.psutil, "net_if_addrs", lambda: addrs)
    _fake, base = net_server
    _set(tmp_path, network_mode="simple", psn_source_iface="br0", interface_labels={"br0": "Bridge"})
    _status, body = _get(base, "/section/network/status")
    assert "Bridge (br0)'s addressing is not managed here." in body
    assert "br0 is not connected." not in body


def test_automatic_with_no_address_anywhere_offers_the_first_interface(net_server, tmp_path, monkeypatch) -> None:  # noqa: F811
    monkeypatch.setattr(net_utils_module, "auto_station_iface", lambda: "")
    _fake, base = net_server
    _set(tmp_path, network_mode="simple")
    _status, body = _get(base, "/section/network/status")
    assert '<option value="" selected data-name="Automatic">Automatic</option>' in body
    # The first interface, as the Operator Screen edits it, so it can be given an address.
    assert 'data-adv-key="net-iface-eth0"' in body


def test_an_unavailable_backend_still_shows_the_mode_in_force(net_server, tmp_path) -> None:  # noqa: F811
    fake, base = net_server
    fake.interfaces = []
    _status, body = _get(base, "/section/network/status")
    assert 'name="network_mode" value="advanced" checked' in body
    assert 'data-mode="advanced"' in body


def test_a_simple_station_keeps_its_picker_when_the_backend_cannot_be_read(net_server, tmp_path, present) -> None:  # noqa: F811
    fake, base = net_server
    fake.interfaces = []
    _set(tmp_path, network_mode="simple", psn_source_iface="eth9")
    _status, body = _get(base, "/section/network/status")
    assert 'id="net-station-iface"' in body
    assert '<option value="wlan0" data-name="wlan0">wlan0 – 10.9.9.9</option>' in body
    assert '<option value="eth9" selected data-name="eth9">eth9 – no address</option>' in body


def test_an_automatic_interface_the_card_does_not_manage_is_named(net_server, tmp_path, monkeypatch) -> None:  # noqa: F811
    monkeypatch.setattr(net_utils_module, "auto_station_iface", lambda: "docker0")
    _fake, base = net_server
    _set(tmp_path, network_mode="simple")
    _status, body = _get(base, "/section/network/status")
    assert "docker0's addressing is not managed here." in body


def test_a_vlan_left_over_in_simple_is_named(net_server, tmp_path, present) -> None:  # noqa: F811
    fake, base = net_server
    fake.interfaces.append("eth0.20")
    fake.vlans = [{"name": "eth0.20", "parent": "eth0", "vlan_id": 20}]
    _set(tmp_path, network_mode="simple", interface_labels={"eth0.20": "Cue"})
    _status, body = _get(base, "/section/network/status")
    assert "Cue (eth0.20) is still on this station." in body
    assert "Remove it in Advanced mode." in body


@pytest.mark.parametrize("supported", [True, False])
def test_only_a_vlan_the_backend_can_remove_is_named_as_left(net_server, tmp_path, present, supported) -> None:  # noqa: F811
    fake, base = net_server
    fake.interfaces.append("eth0.20")
    fake.kinds = {"eth0.20": "vlan"}
    fake.supports_vlans = supported
    _set(tmp_path, network_mode="simple")
    _status, body = _get(base, "/section/network/status")
    assert "still on this station" not in body


def test_the_advanced_card_lists_every_interface(net_server, tmp_path, present) -> None:  # noqa: F811
    _fake, base = net_server
    _status, body = _get(base, "/section/network/status")
    assert 'name="network_mode" value="advanced" checked' in body
    assert body.count('class="net-iface-row"') == 2
    assert 'id="net-station-iface"' not in body
    assert "still on this station" not in body


@pytest.mark.parametrize("mode", ["simple", "advanced"])
def test_the_assignment_panel_and_pointers_are_advanced_only(net_server, tmp_path, present, mode) -> None:  # noqa: F811
    _fake, base = net_server
    _set(tmp_path, network_mode=mode)
    advanced = mode == "advanced"
    _status, general = _get(base, "/section/general")
    assert ('hx-get="/section/interface_assignment"' in general) is advanced
    for path in ("/section/psn", "/section/otp_output", "/section/rttrpm_output", "/section/osc"):
        _status, body = _get(base, path)
        assert ('class="ia-pointer"' in body) is advanced, path
    _status, body = _get(base, "/section/osc_destinations")
    assert ('class="ia-pointer"' in body) is advanced


def test_the_psn_startup_advisory_stays_in_simple(net_server, tmp_path, present, monkeypatch) -> None:  # noqa: F811
    _fake, base = net_server
    _set(tmp_path, network_mode="simple")
    import openfollow.web.server as server_module

    monkeypatch.setattr(
        server_module.ConfigWebServer,
        "get_psn_source_advisory",
        lambda self: {"banner": "Station interface was down at startup."},
        raising=False,
    )
    _status, body = _get(base, "/section/psn")
    assert "ia-pointer" not in body


# --------------------------------------------------------------------------- #
# What switching to Simple would change
# --------------------------------------------------------------------------- #


def test_the_change_list_names_every_pin_and_vlan() -> None:
    cfg = AppConfig(
        network_mode="advanced",
        video_input_iface="eth1",
        web_bind_iface="eth0",
        osc_destinations=OscDestinationsConfig(
            destinations=[
                OscDestinationConfig(id="desk", name="Desk", source_iface="eth0.20"),
                OscDestinationConfig(id="x", host="198.51.100.4", port=9000, source_iface="eth1"),
            ]
        ),
    )
    cfg.otp_output.source_iface = "eth0.20"
    vlans = {"supported": True, "vlans": [{"name": "eth0.20"}, {"name": "eth0.30"}, {"name": "eth0.40"}]}
    changes = simple_mode_changes(cfg, vlans, station_iface="eth0.40", session_iface="eth0.30")
    assert [(label, iface) for _key, label, iface in changes["pins"]] == [
        ("OTP output", "eth0.20"),
        ("OSC Destination Desk", "eth0.20"),
        ("OSC Destination 198.51.100.4:9000", "eth1"),
        ("Video input", "eth1"),
        ("Web UI", "eth0"),
    ]
    assert changes["vlans"] == ["eth0.20"]
    assert changes["kept"] == [("eth0.30", "this session is connected over it")]


def test_a_host_without_vlans_removes_none() -> None:
    changes = simple_mode_changes(AppConfig(), {"supported": False, "vlans": [{"name": "eth0.20"}]}, "", "")
    assert changes == {"pins": [], "vlans": [], "kept": []}


def test_the_preview_is_read_when_the_switch_is_clicked(net_server, tmp_path, present) -> None:  # noqa: F811
    fake, base = net_server
    fake.vlans = [{"name": "eth0.20", "parent": "eth0", "vlan_id": 20}]
    _set(tmp_path, **{"otp_output.source_iface": "eth0.20", "web_bind_iface": "wlan0"})
    _set(tmp_path, interface_labels={"eth0.20": "Lighting"})
    status, body = _get(base, "/section/network/mode/preview")
    assert status == 200
    preview = json.loads(body)
    assert preview == {
        "station": "Automatic (eth0)",
        "pins": ["OTP output: Lighting (eth0.20)", "Web UI: wlan0"],
        "vlans": ["Lighting (eth0.20)"],
        "remove": ["eth0.20"],
        "kept": [],
        "restart": True,
    }


def test_the_preview_warns_of_a_restart_for_a_pin_still_in_force(net_server, present, monkeypatch) -> None:  # noqa: F811
    import openfollow.web.server as server_module

    monkeypatch.setattr(
        server_module.ConfigWebServer,
        "get_web_bind_advisory",
        lambda self: {"bind_at_start": "", "iface_at_start": "wlan0", "status": "iface"},
    )
    _fake, base = net_server
    preview = json.loads(_get(base, "/section/network/mode/preview")[1])
    assert (preview["pins"], preview["restart"]) == ([], True)


def test_a_preview_with_nothing_to_change_is_empty(net_server, present) -> None:  # noqa: F811
    _fake, base = net_server
    preview = json.loads(_get(base, "/section/network/mode/preview")[1])
    assert (preview["pins"], preview["vlans"], preview["kept"], preview["restart"]) == ([], [], [], False)


def test_the_preview_names_a_pinned_station(net_server, tmp_path, present) -> None:  # noqa: F811
    _fake, base = net_server
    _set(tmp_path, psn_source_iface="wlan0")
    assert json.loads(_get(base, "/section/network/mode/preview")[1])["station"] == "wlan0"


# --------------------------------------------------------------------------- #
# The switch
# --------------------------------------------------------------------------- #


def test_switching_to_simple_clears_every_pin_and_removes_the_vlans(net_server, tmp_path, present) -> None:  # noqa: F811
    fake, base = net_server
    fake.interfaces += ["eth0.20", "eth0.30"]
    fake.vlans = [
        {"name": "eth0.20", "parent": "eth0", "vlan_id": 20},
        {"name": "eth0.30", "parent": "eth0", "vlan_id": 30},
    ]
    fake.session_iface = "eth0.30"
    _set(
        tmp_path,
        **{"otp_output.source_iface": "eth0.20", "osc.listen_iface": "eth0.20", "video_input_iface": "eth0"},
        interface_labels={"eth0": "Show LAN", "eth0.20": "Cue", "eth0.30": "Desk"},
    )
    status, body, headers = _post_headers(
        base, "/section/network/mode", {"network_mode": "simple", "vlans": ["eth0.20", "eth0.30"]}
    )
    # Nothing to restart: the browser reloads into the new mode.
    assert (status, body, _restart_headers(headers)) == (200, "", {})
    saved = _saved(tmp_path)
    assert saved.network_mode == "simple"
    assert (saved.otp_output.source_iface, saved.osc.listen_iface, saved.video_input_iface) == ("", "", "")
    # The session's own VLAN stays, like Remove VLAN refuses it.
    assert fake.vlans_deleted == ["eth0.20"]
    # The removed VLAN's label goes with it; the kept one's stays.
    assert saved.interface_labels == {"eth0": "Show LAN", "eth0.30": "Desk"}


def test_back_in_advanced_a_removed_vlan_is_not_listed_as_unplugged(net_server, tmp_path, present) -> None:  # noqa: F811
    fake, base = net_server
    fake.interfaces.append("eth0.20")
    fake.vlans = [{"name": "eth0.20", "parent": "eth0", "vlan_id": 20}]
    _set(tmp_path, network_mode="advanced", interface_labels={"eth0.20": "Cue"})
    _post_headers(base, "/section/network/mode", {"network_mode": "simple", "vlans": "eth0.20"})
    fake.interfaces.remove("eth0.20")
    fake.vlans = []
    _post_headers(base, "/section/network/mode", {"network_mode": "advanced"})
    _status, body = _get(base, "/section/network/status")
    assert "Cue" not in body
    assert "Not connected" not in body


def test_each_switch_logs_the_mode_the_pins_cleared_and_the_vlans_removed(
    net_server,  # noqa: F811
    tmp_path,
    present,
    caplog,
) -> None:
    fake, base = net_server
    fake.interfaces.append("eth0.20")
    fake.vlans = [{"name": "eth0.20", "parent": "eth0", "vlan_id": 20}]
    _set(tmp_path, network_mode="advanced", **{"otp_output.source_iface": "wlan0"})
    with caplog.at_level(logging.INFO, logger="openfollow.web.routes"):
        _post_headers(base, "/section/network/mode", {"network_mode": "simple", "vlans": "eth0.20"})
        _post_headers(base, "/section/network/mode", {"network_mode": "advanced"})
    lines = [r.getMessage() for r in caplog.records if r.name == "openfollow.web.routes" and r.levelno == logging.INFO]
    assert lines == [
        "Network mode Advanced -> Simple; pins cleared: otp_output.source_iface (wlan0); VLANs removed: eth0.20",
        "Network mode Simple -> Advanced; pins cleared: none; VLANs removed: none",
    ]


def test_only_the_vlans_the_dialog_listed_are_removed(net_server, tmp_path, present) -> None:  # noqa: F811
    fake, base = net_server
    fake.vlans = [
        {"name": "eth0.20", "parent": "eth0", "vlan_id": 20},
        {"name": "eth0.30", "parent": "eth0", "vlan_id": 30},
    ]
    _post_headers(base, "/section/network/mode", {"network_mode": "simple", "vlans": ["eth0.20", "eth9.99"]})
    assert fake.vlans_deleted == ["eth0.20"]


def test_a_cleared_web_pin_still_in_force_restarts(net_server, tmp_path, present, monkeypatch) -> None:  # noqa: F811
    import openfollow.web.server as server_module

    monkeypatch.setattr(
        server_module.ConfigWebServer,
        "get_web_bind_advisory",
        lambda self: {"bind_at_start": "", "iface_at_start": "wlan0", "status": "iface"},
    )
    restarts: list[bool] = []
    monkeypatch.setattr(
        server_module.ConfigWebServer, "request_restart_unless_updating", lambda self: restarts.append(True) or True
    )
    _fake, base = net_server
    _post_headers(base, "/section/network/mode", {"network_mode": "simple"})
    assert restarts == [True]


def test_the_station_interfaces_own_vlan_is_kept(net_server, tmp_path, present) -> None:  # noqa: F811
    fake, base = net_server
    fake.vlans = [{"name": "eth0.20", "parent": "eth0", "vlan_id": 20}]
    _set(tmp_path, psn_source_iface="eth0.20")
    _post_headers(base, "/section/network/mode", {"network_mode": "simple", "vlans": "eth0.20"})
    assert fake.vlans_deleted == []


def test_a_failed_removal_does_not_stop_the_switch(net_server, tmp_path, present) -> None:  # noqa: F811
    fake, base = net_server
    fake.interfaces.append("eth0.20")
    fake.vlans = [{"name": "eth0.20", "parent": "eth0", "vlan_id": 20}]
    fake.vlan_delete_result = ApplyResult(ok=False, message="device busy")
    _set(tmp_path, interface_labels={"eth0.20": "Cue"})
    _post_headers(base, "/section/network/mode", {"network_mode": "simple", "vlans": "eth0.20"})
    saved = _saved(tmp_path)
    assert saved.network_mode == "simple"
    # Still on the station, so still labelled.
    assert saved.interface_labels == {"eth0.20": "Cue"}
    _status, body = _get(base, "/section/network/status")
    assert "Cue (eth0.20) is still on this station." in body


def test_a_web_ui_pin_in_force_restarts_openfollow(net_server, tmp_path, present, monkeypatch) -> None:  # noqa: F811
    import openfollow.web.server as server_module

    _set(tmp_path, web_bind_iface="wlan0")
    monkeypatch.setattr(
        server_module.ConfigWebServer,
        "get_web_bind_advisory",
        lambda self: {"bind_at_start": "", "iface_at_start": "wlan0", "status": "iface"},
    )
    restarts: list[bool] = []
    monkeypatch.setattr(
        server_module.ConfigWebServer, "request_restart_unless_updating", lambda self: restarts.append(True) or True
    )
    _fake, base = net_server
    status, body, headers = _post_headers(base, "/section/network/mode", {"network_mode": "simple"})
    assert (status, body) == (200, "")
    assert restarts == [True]
    # Names the process being restarted, so the browser waits for a new one.
    _status, info = _get(base, "/api/info")
    assert _restart_headers(headers) == {"x-openfollow-restarting": json.loads(info)["instance"]}
    assert _saved(tmp_path).web_bind_iface == ""


def test_reposting_simple_never_restarts_again(net_server, tmp_path, present, monkeypatch) -> None:  # noqa: F811
    import openfollow.web.server as server_module

    _set(tmp_path, network_mode="simple")
    monkeypatch.setattr(
        server_module.ConfigWebServer,
        "get_web_bind_advisory",
        lambda self: {"bind_at_start": "", "iface_at_start": "wlan0", "status": "iface"},
    )
    restarts: list[bool] = []
    monkeypatch.setattr(
        server_module.ConfigWebServer, "request_restart_unless_updating", lambda self: restarts.append(True) or True
    )
    _fake, base = net_server
    status, _body, headers = _post_headers(base, "/section/network/mode", {"network_mode": "simple"})
    assert (status, _restart_headers(headers), restarts) == (200, {}, [])


def test_a_restart_refused_by_an_update_says_so(net_server, tmp_path, present, monkeypatch) -> None:  # noqa: F811
    import openfollow.web.server as server_module

    _set(tmp_path, web_bind_iface="wlan0")
    monkeypatch.setattr(
        server_module.ConfigWebServer,
        "get_web_bind_advisory",
        lambda self: {"bind_at_start": "", "iface_at_start": "wlan0", "status": "iface"},
    )
    monkeypatch.setattr(server_module.ConfigWebServer, "request_restart_unless_updating", lambda self: False)
    _fake, base = net_server
    _status, body, headers = _post_headers(base, "/section/network/mode", {"network_mode": "simple"})
    # The browser says so, then reloads into Simple, whose card offers the restart again.
    assert (body, _restart_headers(headers)) == (
        "",
        {"x-openfollow-restart-refused": "An update is running. Restart once it has finished."},
    )
    _status, card = _get(base, "/section/network/status")
    assert "The web UI answers on one interface until OpenFollow restarts." in card
    assert 'onclick="openfollowRestart(' in card
    status, body, _headers = _post_headers(base, "/api/restart", {})
    assert status == 409
    assert json.loads(body)["error"] == "An update is running."


def test_the_card_restarts_for_a_pin_simple_cleared(net_server, tmp_path, present, monkeypatch) -> None:  # noqa: F811
    import openfollow.web.server as server_module

    monkeypatch.setattr(
        server_module.ConfigWebServer,
        "get_web_bind_advisory",
        lambda self: {"bind_at_start": "", "iface_at_start": "wlan0", "status": "iface"},
    )
    restarts: list[bool] = []
    monkeypatch.setattr(
        server_module.ConfigWebServer, "request_restart_unless_updating", lambda self: restarts.append(True) or True
    )
    _fake, base = net_server
    _set(tmp_path, network_mode="simple")
    _status, card = _get(base, "/section/network/status")
    tag_start = card.rindex("<button", 0, card.index('onclick="openfollowRestart('))
    assert card[tag_start : card.index("</button>", tag_start)].endswith(">Restart OpenFollow")
    status, _body, _headers = _post_headers(base, "/api/restart", {})
    assert (status, restarts) == (200, [True])


@pytest.mark.parametrize(
    ("advisory", "web_bind"),
    [
        ({"bind_at_start": "", "iface_at_start": "wlan0", "status": "down"}, ""),
        ({"bind_at_start": "", "iface_at_start": "wlan0", "status": "iface"}, "198.51.100.10"),
        ({"bind_at_start": "", "iface_at_start": "", "status": ""}, ""),
    ],
    ids=["pin-was-down", "web-bind-outranks", "no-pin"],
)
def test_no_restart_where_clearing_the_pin_moves_nothing(
    net_server,  # noqa: F811
    tmp_path,
    present,
    monkeypatch,
    advisory,
    web_bind,
) -> None:
    import openfollow.web.server as server_module

    _set(tmp_path, web_bind=web_bind)
    monkeypatch.setattr(server_module.ConfigWebServer, "get_web_bind_advisory", lambda self: advisory)
    restarts: list[bool] = []
    monkeypatch.setattr(
        server_module.ConfigWebServer, "request_restart_unless_updating", lambda self: restarts.append(True) or True
    )
    _fake, base = net_server
    assert json.loads(_get(base, "/section/network/mode/preview")[1])["restart"] is False
    _post_headers(base, "/section/network/mode", {"network_mode": "simple"})
    assert restarts == []
    _status, card = _get(base, "/section/network/status")
    assert "until OpenFollow restarts" not in card


def test_switching_to_advanced_changes_only_the_mode(net_server, tmp_path, present) -> None:  # noqa: F811
    fake, base = net_server
    fake.vlans = [{"name": "eth0.20", "parent": "eth0", "vlan_id": 20}]
    _set(tmp_path, network_mode="simple", psn_source_iface="wlan0")
    status, body, headers = _post_headers(base, "/section/network/mode", {"network_mode": "advanced"})
    assert (status, body, _restart_headers(headers)) == (200, "", {})
    saved = _saved(tmp_path)
    assert (saved.network_mode, saved.psn_source_iface) == ("advanced", "wlan0")
    assert fake.vlans_deleted == []


@pytest.mark.parametrize("raw", ["Simple", " simple "])
def test_the_switch_reads_the_mode_like_the_config_does(net_server, tmp_path, raw) -> None:  # noqa: F811
    _fake, base = net_server
    status, _text = _post(base, "/section/network/mode", {"network_mode": raw})
    assert status == 200
    assert _saved(tmp_path).network_mode == "simple"


@pytest.mark.parametrize("body", [{}, {"network_mode": ""}, {"network_mode": "expert"}, {"network_mode": "simple1"}])
def test_a_malformed_switch_is_refused(net_server, tmp_path, body) -> None:  # noqa: F811
    _fake, base = net_server
    status, _text = _post(base, "/section/network/mode", body)
    assert status == 400
    assert _saved(tmp_path).network_mode == "advanced"


# --------------------------------------------------------------------------- #
# Simple's Interface picker
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("iface", ["wlan0", ""])
def test_the_picker_saves_the_station_interface(net_server, tmp_path, present, iface) -> None:  # noqa: F811
    _fake, base = net_server
    _set(tmp_path, network_mode="simple", psn_source_iface="eth0")
    status, body = _post(base, "/section/network/station", {"psn_source_iface": iface})
    assert status == 200
    assert _saved(tmp_path).psn_source_iface == iface
    assert 'id="net-station-iface"' in body


def test_the_picker_refuses_an_interface_the_station_does_not_have(net_server, tmp_path, present) -> None:  # noqa: F811
    _fake, base = net_server
    _set(tmp_path, network_mode="simple", psn_source_iface="eth0")
    status, _body = _post(base, "/section/network/station", {"psn_source_iface": "eth9"})
    assert status == 400
    assert _saved(tmp_path).psn_source_iface == "eth0"


def test_a_simple_station_takes_no_plane_pin_from_any_section(net_server, tmp_path, present) -> None:  # noqa: F811
    _fake, base = net_server
    _set(tmp_path, network_mode="simple")
    _post(base, "/section/interface_assignment", {"otp_output.source_iface": "wlan0", "web_bind_iface": "wlan0"})
    saved = _saved(tmp_path)
    assert (saved.otp_output.source_iface, saved.web_bind_iface) == ("", "")
