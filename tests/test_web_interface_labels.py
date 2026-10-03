# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Interface labels and adapter descriptions in the web UI.

The Network Interface card names each row by its label, shows which physical
adapter it is, lists labelled adapters that are not plugged in, and saves a
label without touching the network. Every picker and pointer then reads
"Lighting (enx…)".
"""

from __future__ import annotations

import socket
import urllib.parse
from pathlib import Path
from types import SimpleNamespace

import pytest

from openfollow import net_adapters
from openfollow import net_utils as net_utils_module
from openfollow.configuration import load_config, save_config
from tests.test_web_network_write import _get, _post, net_server  # noqa: F401 - fixture

pytestmark = pytest.mark.integration

_BUILT_IN = "platform/axi/1000120000.pcie/1f00100000.ethernet"


@pytest.fixture()
def adapters(tmp_path: Path) -> Path:
    """A sysfs tree where eth0 is the board's Ethernet and wlan0 its Wi-Fi."""
    sysfs = tmp_path / "sysfs"
    for index, (name, mac) in enumerate((("eth0", "88:a2:9e:df:04:e3"), ("wlan0", "88:a2:9e:df:04:e4")), start=2):
        base = sysfs / "class" / "net" / name
        base.mkdir(parents=True)
        (base / "ifindex").write_text(f"{index}\n")
        (base / "address").write_text(f"{mac}\n")
        device = sysfs / "devices" / _BUILT_IN / name
        device.mkdir(parents=True)
        (base / "device").symlink_to(device)
    (sysfs / "class" / "net" / "wlan0" / "wireless").mkdir()
    net_adapters.set_reader(net_adapters.AdapterReader(sysfs_root=sysfs, platform="linux"))
    return sysfs


def _labels(tmp_path: Path, labels: dict[str, str]) -> None:
    path = str(tmp_path / "config.toml")
    cfg = load_config(path)
    cfg.interface_labels = labels
    save_config(cfg, path)


def _saved(tmp_path: Path) -> dict[str, str]:
    return load_config(str(tmp_path / "config.toml")).interface_labels


def _row(body: str, name: str) -> str:
    start = body.index(f'data-adv-key="net-iface-{name}"')
    return body[start : body.index("</details>", start)]


# --- The card -------------------------------------------------------------------


def test_a_row_reads_label_then_name_and_which_adapter(net_server, adapters, tmp_path) -> None:  # noqa: F811
    _labels(tmp_path, {"eth0": "Production"})
    _fake, base = net_server
    status, body = _get(base, "/section/network/status")
    assert status == 200
    eth0 = _row(body, "eth0")
    assert '<span class="net-iface-name">Production</span>' in eth0
    assert '<span class="net-iface-sub">eth0 · Built-in Ethernet</span>' in eth0
    assert "88:a2:9e:df:04:e3" in eth0
    # An unlabelled row keeps its name on top and the adapter underneath.
    wlan0 = _row(body, "wlan0")
    assert '<span class="net-iface-name">wlan0</span>' in wlan0
    assert '<span class="net-iface-sub">Built-in Wi-Fi</span>' in wlan0


def test_a_labelled_adapter_that_is_not_plugged_in_stays_listed(net_server, tmp_path) -> None:  # noqa: F811
    _labels(tmp_path, {"enx00e04c68a1f2": "Lighting backup"})
    _fake, base = net_server
    _status, body = _get(base, "/section/network/status")
    absent = body[body.index("net-iface-absent") :]
    absent = absent[: absent.index("</form>")]
    assert '<span class="net-iface-name">Lighting backup</span>' in absent
    assert '<span class="net-iface-sub">enx00e04c68a1f2</span>' in absent
    assert '<span class="stat-chip">Not connected</span>' in absent
    assert 'hx-post="/section/network/label/forget"' in absent
    assert 'name="iface" value="enx00e04c68a1f2"' in absent


def test_a_vlan_parent_is_offered_by_label(net_server, tmp_path) -> None:  # noqa: F811
    _labels(tmp_path, {"eth0": "Production"})
    _fake, base = net_server
    _status, body = _get(base, "/section/network/status")
    assert '<option value="eth0" >Production (eth0)</option>' in body


# --- Saving a label -------------------------------------------------------------


def test_saving_a_label_stores_it_and_reopens_the_row(net_server, tmp_path) -> None:  # noqa: F811
    fake, base = net_server
    status, body = _post(base, "/section/network/label", {"iface": "wlan0", "label": " Lighting "})
    assert status == 200
    assert _saved(tmp_path) == {"wlan0": "Lighting"}
    wlan0 = _row(body, "wlan0")
    assert 'data-adv-force-open="1"' in wlan0
    assert "save-flash saved" in wlan0
    assert '<span class="net-iface-name">Lighting</span>' in wlan0
    # A label is OpenFollow's own: nothing was applied to the network.
    assert fake.applied == []


def test_a_label_keeps_letters_beyond_ascii(net_server, tmp_path) -> None:  # noqa: F811
    _fake, base = net_server
    _post(base, "/section/network/label", {"iface": "eth0", "label": "Bühne"})
    assert _saved(tmp_path) == {"eth0": "Bühne"}


def test_an_empty_label_clears_it(net_server, tmp_path) -> None:  # noqa: F811
    _labels(tmp_path, {"eth0": "Production", "wlan0": "Lighting"})
    _fake, base = net_server
    _post(base, "/section/network/label", {"iface": "eth0", "label": "  "})
    assert _saved(tmp_path) == {"wlan0": "Lighting"}


@pytest.mark.parametrize(
    ("entered", "error"),
    [
        ("production", "&#039;Production&#039; is already the label of eth0."),
        ("L" * 21, "Must be at most 20 characters."),
        ("Light\x00ing", "Remove control or text-direction characters."),
    ],
    ids=["taken", "too-long", "control-character"],
)
def test_a_refused_label_is_shown_on_its_row_and_not_saved(
    net_server,  # noqa: F811
    tmp_path,
    entered: str,
    error: str,
) -> None:
    _labels(tmp_path, {"eth0": "Production"})
    _fake, base = net_server
    status, body = _post(base, "/section/network/label", {"iface": "wlan0", "label": entered})
    assert status == 200
    wlan0 = _row(body, "wlan0")
    assert f'<span class="field-error-msg" role="alert">{error}</span>' in wlan0
    assert 'data-adv-force-open="1"' in wlan0
    assert "save-flash" not in wlan0
    assert _saved(tmp_path) == {"eth0": "Production"}


def test_a_label_that_only_differs_by_a_stripped_character_is_refused(net_server, tmp_path) -> None:  # noqa: F811
    """A tab is cleaned away on save, so "Produc<tab>tion" would otherwise be
    stored as a second "Production" and one of them silently dropped."""
    _labels(tmp_path, {"eth0": "Production"})
    _fake, base = net_server
    status, body = _post(base, "/section/network/label", {"iface": "wlan0", "label": "Produc\x09tion"})
    assert status == 200
    assert "is already the label of eth0." in _row(body, "wlan0")
    assert _saved(tmp_path) == {"eth0": "Production"}


def test_relabelling_an_interface_with_its_own_label_is_fine(net_server, tmp_path) -> None:  # noqa: F811
    _labels(tmp_path, {"eth0": "Production"})
    _fake, base = net_server
    _post(base, "/section/network/label", {"iface": "eth0", "label": "PRODUCTION"})
    assert _saved(tmp_path) == {"eth0": "PRODUCTION"}


def test_a_label_on_an_interface_the_card_does_not_list_is_refused(net_server, tmp_path) -> None:  # noqa: F811
    _fake, base = net_server
    status, _body = _post(base, "/section/network/label", {"iface": "eth9", "label": "Ghost"})
    assert status == 400
    assert _saved(tmp_path) == {}


def test_forget_frees_the_label_of_an_unplugged_adapter(net_server, tmp_path) -> None:  # noqa: F811
    _labels(tmp_path, {"enx00e04c68a1f2": "Lighting", "eth0": "Production"})
    _fake, base = net_server
    status, body = _post(base, "/section/network/label/forget", {"iface": "enx00e04c68a1f2"})
    assert status == 200
    assert "net-iface-absent" not in body
    assert _saved(tmp_path) == {"eth0": "Production"}
    # The label is free for the replacement.
    _post(base, "/section/network/label", {"iface": "wlan0", "label": "Lighting"})
    assert _saved(tmp_path) == {"eth0": "Production", "wlan0": "Lighting"}


def test_forgetting_a_label_that_is_not_there_is_refused(net_server) -> None:  # noqa: F811
    _fake, base = net_server
    status, _body = _post(base, "/section/network/label/forget", {"iface": "eth0"})
    assert status == 404


# --- On-blur check --------------------------------------------------------------


def _validate(base: str, **query: str) -> str:
    _status, body = _get(base, f"/api/validate/network/label?{urllib.parse.urlencode(query)}")
    return body


def test_blur_flags_a_label_another_interface_holds(net_server, tmp_path) -> None:  # noqa: F811
    _labels(tmp_path, {"eth0": "Production"})
    _fake, base = net_server
    assert "is already the label of eth0." in _validate(base, label="production", iface="wlan0")
    assert _validate(base, label="Production", iface="eth0") == ""
    assert _validate(base, label="Lighting", iface="wlan0") == ""
    assert "Must be at most 20 characters." in _validate(base, label="L" * 21, iface="wlan0")


# --- Pickers and pointers -------------------------------------------------------


def _ifaces(monkeypatch: pytest.MonkeyPatch, spec: dict[str, str]) -> None:
    monkeypatch.setattr(
        net_utils_module.psutil,
        "net_if_addrs",
        lambda: {
            name: [SimpleNamespace(family=socket.AF_INET, address=addr)] if addr else [] for name, addr in spec.items()
        },
    )


def test_the_picker_reads_who_then_address_or_state(net_server, adapters, tmp_path, monkeypatch) -> None:  # noqa: F811
    _ifaces(monkeypatch, {"eth0": "192.0.2.10", "wlan0": "198.51.100.5", "eth1": ""})
    _labels(tmp_path, {"eth0": "Production"})
    _fake, base = net_server
    _status, body = _get(base, "/network/interfaces/by_name?current=eth1")
    assert '<option value="eth0" >Production (eth0) – 192.0.2.10</option>' in body
    assert '<option value="wlan0" >wlan0 · Built-in Wi-Fi – 198.51.100.5</option>' in body
    assert '<option value="eth1" selected>eth1 – no address</option>' in body
    _status, body = _get(base, "/network/interfaces/by_name?current=enx00e04c68a1f2")
    assert '<option value="enx00e04c68a1f2" selected>enx00e04c68a1f2 – not connected</option>' in body


def test_the_panel_shows_a_present_interface_without_an_address_as_down(
    net_server,  # noqa: F811
    tmp_path,
    monkeypatch,
) -> None:
    _ifaces(monkeypatch, {"eth0": "192.0.2.10", "eth1": ""})
    path = str(tmp_path / "config.toml")
    cfg = load_config(path)
    cfg.psn_source_iface = "eth0"
    cfg.otp_output.source_iface = "eth1"
    save_config(cfg, path)
    _fake, base = net_server
    _status, body = _get(base, "/section/interface_assignment")
    otp_row = body[body.index("OTP output") :].split("</tr>", 1)[0]
    assert '<span class="stat-chip off">Interface down</span>' in otp_row
    station_row = body[body.index("Station default") :].split("</tr>", 1)[0]
    assert "stat-chip" not in station_row


def test_a_protocol_section_points_at_its_interface_by_label(net_server, tmp_path) -> None:  # noqa: F811
    path = str(tmp_path / "config.toml")
    cfg = load_config(path)
    cfg.psn_source_iface = "enx9c69d3ac16ab"
    cfg.otp_output.source_iface = "eth1"
    cfg.interface_labels = {"enx9c69d3ac16ab": "Lighting"}
    save_config(cfg, path)
    _fake, base = net_server
    _status, psn = _get(base, "/section/psn")
    assert '<span class="ia-pointer-value">Lighting (enx9c69d3ac16ab)</span>' in psn
    _status, otp = _get(base, "/section/otp_output")
    assert '<span class="ia-pointer-value">eth1</span>' in otp
