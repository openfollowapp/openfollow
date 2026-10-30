# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Michel Honold and Paul Hermann
"""Adapters that are not plugged in but have saved settings, on the Network Interface card.

Each keeps a Not connected row, labelled or not, with the settings it comes up
with; Edit saves to them, and Forget deletes them with the VLANs on it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openfollow.configuration import load_config, save_config
from openfollow.network.adapter import ApplyResult, Ipv4Method
from openfollow.network.validate import vlan_interface_name
from tests.test_web_network_write import _get, _post, net_server  # noqa: F401 - fixture

pytestmark = pytest.mark.integration

_USB = "enx001122334455"
_VLAN = vlan_interface_name(_USB, 2)


def _saved_rows() -> list[dict]:
    """A USB adapter on a static address and a VLAN on it, as the services layer lists them."""
    return [
        {
            "name": _VLAN,
            "kind": "vlan",
            "address": "",
            "prefix": None,
            "subnet_mask": "",
            "method": "dhcp",
            "router": "",
            "dns": [],
            "lease_display": None,
            "vlan_parent": _USB,
            "vlan_id": 2,
        },
        {
            "name": _USB,
            "kind": None,
            "address": "192.0.2.5",
            "prefix": 24,
            "subnet_mask": "255.255.255.0",
            "method": "static",
            "router": "192.0.2.1",
            "dns": ["192.0.2.53"],
            "lease_display": None,
            "vlan_parent": "",
            "vlan_id": None,
        },
    ]


@pytest.fixture()
def unplugged(net_server):  # noqa: F811
    fake, base = net_server
    fake.saved = _saved_rows()
    return fake, base


def _config(tmp_path: Path, **fields) -> None:
    path = str(tmp_path / "config.toml")
    cfg = load_config(path)
    for key, value in fields.items():
        setattr(cfg, key, value)
    save_config(cfg, path)


def _labels(tmp_path: Path) -> dict[str, str]:
    return load_config(str(tmp_path / "config.toml")).interface_labels


def _row(body: str, name: str) -> str:
    start = body.index(f'data-adv-key="net-iface-{name}"')
    return body[start : body.index("</details>", start)]


def _after_row(body: str, name: str) -> str:
    """The row and the actions that follow its form, up to the next row."""
    start = body.index(f'data-adv-key="net-iface-{name}"')
    end = body.find("data-adv-key=", start + 1)
    return body[start : end if end != -1 else len(body)]


# --- The card -------------------------------------------------------------------


def test_an_unlabelled_unplugged_adapter_keeps_its_row(unplugged) -> None:
    _fake, base = unplugged
    _status, body = _get(base, "/section/network/status")
    row = _row(body, _USB)
    assert '<span class="stat-chip">Not connected</span>' in row
    assert '<span class="ia-dot down"' in row
    assert "192.0.2.5/24" in row
    assert '<span class="net-iface-method-badge">Static</span>' in row
    assert 'value="192.0.2.1"' in row and 'value="192.0.2.53"' in row
    # Nothing to renew or remove: there is no lease and no link.
    assert "Renew DHCP lease" not in row and "Lease" not in row


def test_a_vlan_on_it_follows_its_parent(unplugged) -> None:
    _fake, base = unplugged
    _status, body = _get(base, "/section/network/status")
    assert body.index(f'data-adv-key="net-iface-{_USB}"') < body.index(f'data-adv-key="net-iface-{_VLAN}"')
    vlan = _row(body, _VLAN)
    assert '<span class="stat-chip">VLAN 2</span>' in vlan
    assert '<span class="stat-chip">Not connected</span>' in vlan
    assert f'<span class="network-grid-value">{_USB}</span>' in vlan


def test_each_offers_edit_and_a_confirmed_forget(unplugged) -> None:
    _fake, base = unplugged
    _status, body = _get(base, "/section/network/status")
    parent = _after_row(body, _USB)
    assert f'hx-get="/section/network/edit/{_USB}"' in parent
    assert 'hx-post="/section/network/forget"' in parent
    assert f'data-confirm-title="Forget {_USB}?"' in parent and "data-confirm-danger" in parent
    # Only the dialog's own confirm is red: this button only asks.
    assert '<button type="submit" class="secondary">Forget</button>' in parent
    assert f'hx-confirm="Its saved settings are deleted from this station. Its VLAN {_VLAN} goes with it."' in parent
    vlan = _after_row(body, _VLAN)
    assert 'hx-confirm="Its saved settings are deleted from this station."' in vlan
    assert "Remove VLAN" not in body


def test_a_label_names_it_and_its_confirmation(unplugged, tmp_path) -> None:
    _config(tmp_path, interface_labels={_USB: "Lighting", "enx665544332211": "Spare"})
    _fake, base = unplugged
    _status, body = _get(base, "/section/network/status")
    parent = _after_row(body, _USB)
    assert '<span class="net-iface-name">Lighting</span>' in parent
    assert 'data-confirm-title="Forget Lighting?"' in parent
    assert 'hx-confirm="Its saved settings and label are deleted from this station.' in parent
    assert f'<span class="network-grid-value">Lighting ({_USB})</span>' in _row(body, _VLAN)
    # Listed once: a labelled adapter with saved settings is not also a label-only row.
    assert body.count(f'data-adv-key="net-iface-{_USB}"') == 1
    assert body.count('class="net-iface-row net-iface-absent"') == 1
    assert '<span class="net-iface-name">Spare</span>' in body


def test_a_read_only_host_lists_them_without_controls(unplugged) -> None:
    fake, base = unplugged
    fake.writable = False
    _status, body = _get(base, "/section/network/status")
    assert '<span class="stat-chip">Not connected</span>' in _row(body, _USB)
    assert "/section/network/forget" not in body
    assert f"/section/network/edit/{_USB}" not in body


def test_simple_mode_shows_none(unplugged, tmp_path) -> None:
    _config(tmp_path, network_mode="simple", psn_source_iface="eth0")
    _fake, base = unplugged
    _status, body = _get(base, "/section/network/status")
    assert _USB not in body


def test_a_present_interface_is_never_listed_twice(unplugged) -> None:
    fake, base = unplugged
    fake.saved.append({**fake.saved[1], "name": "wlan0"})
    _status, body = _get(base, "/section/network/status")
    assert body.count('data-adv-key="net-iface-wlan0"') == 1
    assert '<span class="stat-chip">Not connected</span>' not in _row(body, "wlan0")


# --- Edit and Apply ---------------------------------------------------------------


def test_edit_opens_it_with_its_own_saved_settings(unplugged) -> None:
    _fake, base = unplugged
    status, body = _get(base, f"/section/network/edit/{_USB}")
    assert status == 200
    row = _row(body, _USB)
    assert 'data-mode="edit"' in row and 'data-method="static"' in row
    assert 'value="192.0.2.5"' in row and 'value="192.0.2.1"' in row
    assert "10.0.0.5" not in row  # not the active interface's
    assert ">Apply<" in row and "Renew DHCP lease" not in row
    # It carries no session, whichever interface answers this browser.
    assert "may be the one carrying this web session" not in row
    # Its subnet would come from a lease, and there is none.
    assert 'value="dhcp_manual"' not in row
    assert "/section/network/forget" not in _after_row(body, _USB)


def test_apply_saves_to_it_and_reports_pending(unplugged) -> None:
    fake, base = unplugged
    fake.apply_result = ApplyResult(
        ok=True, pending=True, message=f"Saved; the settings take effect when {_USB} is plugged in."
    )
    status, body = _post(
        base,
        "/section/network/apply",
        {"iface": _USB, "method": "static", "address": "192.0.2.9", "subnet_mask": "255.255.255.0"},
    )
    assert status == 200
    assert [(iface, cfg.method, cfg.address) for iface, cfg in fake.applied] == [(_USB, Ipv4Method.STATIC, "192.0.2.9")]
    assert f"Saved; the settings take effect when {_USB} is plugged in." in body


def test_renew_is_refused_on_it(unplugged) -> None:
    fake, base = unplugged
    _status, body = _post(base, "/section/network/renew", {"iface": _USB})
    assert fake.renewed == []
    assert f"{_USB} is not present" in body


def test_an_unknown_interface_is_still_refused(unplugged) -> None:
    fake, base = unplugged
    _status, body = _post(base, "/section/network/apply", {"iface": "enx665544332211", "method": "dhcp"})
    assert fake.applied == []
    assert "enx665544332211 is not present" in body


def test_a_label_save_keeps_its_open_edit(unplugged, tmp_path) -> None:
    _fake, base = unplugged
    status, body = _post(
        base,
        "/section/network/label",
        {"iface": _USB, "label": "Lighting", "editing_iface": _USB, "method": "static", "address": "192.0.2.77"},
    )
    assert status == 200
    assert _labels(tmp_path) == {_USB: "Lighting"}
    row = _row(body, _USB)
    assert 'data-mode="edit"' in row and 'value="192.0.2.77"' in row


# --- Forget -----------------------------------------------------------------------


def test_forget_deletes_its_settings_and_the_labels_that_went_with_them(unplugged, tmp_path) -> None:
    _config(tmp_path, interface_labels={_USB: "Lighting", _VLAN: "Viewer", "eth0": "Production"})
    fake, base = unplugged
    status, body = _post(base, "/section/network/forget", {"iface": _USB})
    assert status == 200
    assert fake.forgotten == [_USB]
    assert fake.config_lock_held == [False]  # the network write is not made under the config lock
    assert _labels(tmp_path) == {"eth0": "Production"}
    assert "Deleted the saved settings." in body
    assert "net-iface-" + _USB not in body and _VLAN not in body


def test_a_vlan_the_delete_left_keeps_its_label(unplugged, tmp_path) -> None:
    _config(tmp_path, interface_labels={_USB: "Lighting", _VLAN: "Viewer"})
    fake, base = unplugged
    fake.forget_leaves = {_VLAN}
    _post(base, "/section/network/forget", {"iface": _USB})
    assert _labels(tmp_path) == {_VLAN: "Viewer"}


def test_a_failed_forget_keeps_the_labels_and_says_why(unplugged, tmp_path) -> None:
    _config(tmp_path, interface_labels={_USB: "Lighting"})
    fake, base = unplugged
    fake.forget_result = ApplyResult(ok=False, message=f"{_USB} is plugged in; its settings were not deleted.")
    status, body = _post(base, "/section/network/forget", {"iface": _USB})
    assert status == 200
    assert _labels(tmp_path) == {_USB: "Lighting"}
    assert "network-banner-error" in body and "is plugged in" in body


@pytest.mark.parametrize("iface", ["eth0", "enx665544332211", ""], ids=["present", "unknown", "blank"])
def test_forget_of_a_row_without_saved_settings_says_so(unplugged, iface) -> None:
    """A stale row (plugged in again, or forgotten from another browser) answers on the card, never silently."""
    fake, base = unplugged
    status, body = _post(base, "/section/network/forget", {"iface": iface})
    assert status == 200
    assert fake.forgotten == []
    assert "network-banner-error" in body and f"{iface} has no saved settings." in body
    assert f'data-adv-key="net-iface-{_USB}"' in body  # the card is current again


def test_forget_carries_the_open_edit_row(unplugged) -> None:
    _fake, base = unplugged
    _status, body = _post(
        base,
        "/section/network/forget",
        {"iface": _VLAN, "editing_iface": "eth0", "method": "static", "address": "10.0.0.77"},
    )
    row = _row(body, "eth0")
    assert 'data-mode="edit"' in row and 'value="10.0.0.77"' in row


def test_dhcp_with_manual_address_is_refused_on_it(unplugged) -> None:
    fake, base = unplugged
    _status, body = _post(
        base, "/section/network/apply", {"iface": _USB, "method": "dhcp_manual", "address": "192.0.2.9"}
    )
    assert fake.applied == []
    assert "DHCP with manual address takes its subnet from a lease, and this interface has none yet." in body
    row = _row(body, _USB)
    # What was entered stays, the choice included, so it can be corrected.
    assert 'data-mode="edit"' in row and 'value="192.0.2.9"' in row
    assert '<option value="dhcp_manual" selected>' in row


def test_a_settings_file_that_does_not_read_refuses_forget_before_any_profile_is_deleted(unplugged, tmp_path) -> None:
    """The labels could not be cleaned up afterwards, so nothing on the network is changed either."""
    fake, base = unplugged
    (tmp_path / "config.toml").write_text("not [[ valid toml", encoding="utf-8")
    status, _body = _post(base, "/section/network/forget", {"iface": _USB})
    assert status == 409
    assert fake.forgotten == []
