# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Which physical adapter an interface name is, and the operator's label for it.

The fake sysfs trees follow a Pi 5 with a USB adapter and a VLAN: two xHCI host
controllers, each with a USB 2 and a USB 3 root hub.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from openfollow import net_adapters
from openfollow.net_adapters import (
    LABEL_MAX_LEN,
    Adapter,
    AdapterReader,
    adapter_fields,
    clean_label,
    describe,
    display_name,
    label_conflict,
    normalize_labels,
    parse_hardware_ports,
    valid_iface_name,
)

pytestmark = pytest.mark.unit

_HOST0 = "platform/axi/1000120000.pcie/1f00200000.usb/xhci-hcd.0"
_HOST1 = "platform/axi/1000120000.pcie/1f00300000.usb/xhci-hcd.1"
_BUILT_IN = "platform/axi/1000120000.pcie/1f00100000.ethernet"


def _root_hubs(sysfs: Path) -> None:
    bus = sysfs / "bus" / "usb" / "devices"
    bus.mkdir(parents=True, exist_ok=True)
    for host, hubs in ((_HOST0, ("usb1", "usb2")), (_HOST1, ("usb3", "usb4"))):
        for hub in hubs:
            target = sysfs / "devices" / host / hub
            target.mkdir(parents=True, exist_ok=True)
            (bus / hub).symlink_to(target)


def _iface(
    sysfs: Path, name: str, *, ifindex: int, device: Path | None = None, mac: str = "", uevent: str = ""
) -> Path:
    base = sysfs / "class" / "net" / name
    base.mkdir(parents=True)
    (base / "ifindex").write_text(f"{ifindex}\n")
    (base / "address").write_text(f"{mac}\n")
    (base / "uevent").write_text(f"INTERFACE={name}\n{uevent}")
    if device is not None:
        device.mkdir(parents=True, exist_ok=True)
        (base / "device").symlink_to(device)
    return base


def _usb_adapter(sysfs: Path, name: str, *, maker: str = "ASIX", product: str = "AX88179B", ifindex: int = 8) -> Path:
    _root_hubs(sysfs)
    usb = sysfs / "devices" / _HOST1 / "usb3" / "3-2"
    usb.mkdir(parents=True)
    for attr, value in (("devpath", "2"), ("busnum", "3"), ("idVendor", "0b95"), ("manufacturer", maker)):
        (usb / attr).write_text(f"{value}\n")
    (usb / "product").write_text(f"{product}\n")
    return _iface(sysfs, name, ifindex=ifindex, device=usb / "3-2:2.0", mac="9c:69:d3:ac:16:ab")


def _linux(sysfs: Path, run=None) -> AdapterReader:
    def _no_ip(*_a, **_k):
        raise OSError("no ip")

    return AdapterReader(sysfs_root=sysfs, platform="linux", run=run or _no_ip)


def _ip_answer(vlan_id: object):
    def run(*_args, **_kwargs):
        return SimpleNamespace(stdout=json.dumps([{"linkinfo": {"info_data": {"id": vlan_id}}}]))

    return run


# --- Names and labels ---------------------------------------------------------


def test_a_labelled_interface_reads_label_then_name() -> None:
    labels = {"enx9c69d3ac16ab": "Lighting"}
    assert display_name("enx9c69d3ac16ab", labels) == "Lighting (enx9c69d3ac16ab)"
    assert display_name("eth0", labels) == "eth0"
    assert display_name("", labels) == ""


def test_a_label_on_another_interface_is_taken_whatever_its_case() -> None:
    labels = {"eth0": "Production", "eth1": "Lighting"}
    assert label_conflict(labels, "eth1", "production ") == "'Production' is already the label of eth0."
    # Its own label, and an empty one, take nothing.
    assert label_conflict(labels, "eth0", "PRODUCTION") is None
    assert label_conflict(labels, "eth1", "  ") is None


def test_labels_are_compared_as_they_would_be_stored() -> None:
    """A tab or a direction mark vanishes on save, so a label carrying one would
    otherwise pass as new and land on top of the one it copies."""
    labels = {"eth1": "Lighting"}
    assert label_conflict(labels, "eth0", "Light\x09ing") == "'Lighting' is already the label of eth1."
    assert label_conflict(labels, "eth0", "Lighting\u200e") == "'Lighting' is already the label of eth1."


@pytest.mark.parametrize(
    ("raw", "stored"),
    [
        ("  Lighting  ", "Lighting"),
        ("Light\x00ing\u202e", "Lighting"),
        ("A" * (LABEL_MAX_LEN + 5), "A" * LABEL_MAX_LEN),
        (None, ""),
        (3, ""),
        (True, ""),
    ],
)
def test_a_stored_label_is_clean_text_within_the_limit(raw: object, stored: str) -> None:
    assert clean_label(raw) == stored


@pytest.mark.parametrize("name", ["eth0", "enx9c69d3ac16ab", "eth0.13", "wlan0", "en7"])
def test_kernel_interface_names_are_accepted(name: str) -> None:
    assert valid_iface_name(name)


@pytest.mark.parametrize("name", ["", "a" * 16, "../etc", ".hidden", "eth 0", "a/b", "a:b", None, 3, True])
def test_names_that_are_no_interface_or_unsafe_in_a_path_are_refused(name: object) -> None:
    assert not valid_iface_name(name)


@pytest.mark.parametrize("raw", ["Lighting", ["eth0", "Lighting"], None, 3])
def test_labels_that_are_not_a_table_are_dropped(raw: object) -> None:
    assert normalize_labels(raw) == {}


def test_stored_labels_keep_valid_names_and_clean_labels_only() -> None:
    raw = {
        " eth0 ": " Production ",
        "eth0.13": "Video",
        "bad/name": "Spare",
        3: "Number",
        "eth2": None,
        "eth3": "   ",
        "wlan0": "W" * 30,
    }
    assert normalize_labels(raw) == {"eth0": "Production", "eth0.13": "Video", "wlan0": "W" * LABEL_MAX_LEN}


def test_of_two_equal_labels_the_name_that_sorts_first_keeps_it() -> None:
    assert normalize_labels({"eth1": "lighting", "eth0": "Lighting"}) == {"eth0": "Lighting"}


# --- Reading sysfs ------------------------------------------------------------


def test_a_usb_adapter_reads_its_socket_model_and_mac(tmp_path: Path) -> None:
    _usb_adapter(tmp_path, "enx9c69d3ac16ab")
    adapter = _linux(tmp_path).read("enx9c69d3ac16ab")
    assert adapter == Adapter("enx9c69d3ac16ab", port="USB 2, port 2", model="ASIX AX88179B", mac="9c:69:d3:ac:16:ab")
    assert adapter.summary({}) == "USB 2, port 2 · ASIX AX88179B"
    assert adapter.where({}) == "USB 2, port 2"


def test_a_product_that_names_its_maker_is_not_repeated(tmp_path: Path) -> None:
    _usb_adapter(tmp_path, "enx00e04c680001", maker="Realtek", product="Realtek USB 10/100/1000 LAN")
    assert _linux(tmp_path).read("enx00e04c680001").model == "Realtek USB 10/100/1000 LAN"


def test_what_a_usb_device_says_about_itself_is_cleaned_and_bounded(tmp_path: Path) -> None:
    _usb_adapter(tmp_path, "eth1", maker="Evil\x1b[2J", product="P" * 80)
    model = _linux(tmp_path).read("eth1").model
    assert "\x1b" not in model
    assert model == "Evil[2J " + "P" * 48


def test_a_usb_device_without_strings_has_no_model(tmp_path: Path) -> None:
    base = _usb_adapter(tmp_path, "eth1")
    usb = (base / "device").resolve().parent
    (usb / "manufacturer").unlink()
    (usb / "product").unlink()
    assert _linux(tmp_path).read("eth1").model == ""


@pytest.mark.parametrize(("wireless", "port"), [(False, "Built-in Ethernet"), (True, "Built-in Wi-Fi")])
def test_an_adapter_on_the_board_is_built_in(tmp_path: Path, wireless: bool, port: str) -> None:
    base = _iface(tmp_path, "eth0", ifindex=2, device=tmp_path / "devices" / _BUILT_IN, mac="88:A2:9E:DF:04:E3")
    if wireless:
        (base / "wireless").mkdir()
    assert _linux(tmp_path).read("eth0") == Adapter("eth0", port=port, mac="88:a2:9e:df:04:e3")


def test_a_vlan_names_its_parent_and_id(tmp_path: Path) -> None:
    _iface(tmp_path, "eth0", ifindex=2, device=tmp_path / "devices" / _BUILT_IN, mac="88:a2:9e:df:04:e3")
    base = _iface(tmp_path, "eth0.13", ifindex=6, mac="88:a2:9e:df:04:e3", uevent="DEVTYPE=vlan\n")
    (base / "lower_eth0").symlink_to(tmp_path / "class" / "net" / "eth0")
    adapter = _linux(tmp_path, run=_ip_answer(13)).read("eth0.13")
    assert (adapter.vlan_parent, adapter.vlan_id, adapter.mac) == ("eth0", 13, "")
    labels = {"eth0": "Production"}
    assert adapter.summary(labels) == "VLAN 13 on Production (eth0)"
    assert adapter.vlan_text(labels) == "13 on Production (eth0)"


@pytest.mark.parametrize(
    ("name", "answer", "vlan_id"),
    [
        ("eth0.13", None, 13),  # ip failed: the station names every VLAN it creates <parent>.<id>
        ("eth0.13", _ip_answer(True), 13),
        ("eth0.13", _ip_answer("x"), 13),
        ("video", None, None),
        ("eth0.x", None, None),
    ],
)
def test_a_vlan_id_falls_back_to_the_name(tmp_path: Path, name: str, answer, vlan_id: int | None) -> None:
    base = _iface(tmp_path, name, ifindex=6, uevent="DEVTYPE=vlan\n")
    (base / "lower_eth0").symlink_to(tmp_path)
    adapter = _linux(tmp_path, run=answer).read(name)
    assert adapter.vlan_id == vlan_id
    if vlan_id is None:
        assert adapter.where({}) == "VLAN on eth0"


def test_ip_is_asked_without_a_shell_and_with_a_deadline(tmp_path: Path) -> None:
    seen: list[tuple] = []

    def run(argv, **kwargs):
        seen.append((argv, kwargs))
        return SimpleNamespace(stdout="[]")

    base = _iface(tmp_path, "eth0.7", ifindex=6, uevent="DEVTYPE=vlan\n")
    (base / "lower_eth0").symlink_to(tmp_path)
    assert _linux(tmp_path, run=run).read("eth0.7").vlan_id == 7
    argv, kwargs = seen[0]
    assert argv == ["ip", "-j", "-d", "link", "show", "dev", "eth0.7"]
    assert kwargs["timeout"] > 0
    assert kwargs.get("shell") is not True


def test_a_virtual_interface_has_no_description(tmp_path: Path) -> None:
    _iface(tmp_path, "wg0", ifindex=9, mac="00:00:00:00:00:00")
    adapter = _linux(tmp_path).read("wg0")
    assert adapter == Adapter("wg0")
    assert adapter.summary({}) == ""


@pytest.mark.parametrize("name", ["eth9", "../../etc", ""])
def test_an_absent_or_unsafe_name_reads_as_unknown(tmp_path: Path, name: str) -> None:
    assert _linux(tmp_path).read(name) == Adapter(name)


def test_an_adapter_is_read_once_per_appearance(tmp_path: Path) -> None:
    base = _usb_adapter(tmp_path, "eth1")
    reader = _linux(tmp_path)
    first = reader.read("eth1")
    (base / "address").write_text("00:e0:4c:68:00:01\n")
    assert reader.read("eth1") == first
    # Replugged: the kernel gives it a new index, so it is read again.
    (base / "ifindex").write_text("12\n")
    assert reader.read("eth1").mac == "00:e0:4c:68:00:01"


def test_the_cache_stays_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(net_adapters, "_CACHE_MAX", 1)
    _iface(tmp_path, "wg0", ifindex=9)
    _iface(tmp_path, "wg1", ifindex=10, mac="02:00:00:00:00:01")
    reader = _linux(tmp_path)
    assert reader.read("wg0") == Adapter("wg0")
    assert reader.read("wg1").mac == "02:00:00:00:00:01"
    assert len(reader._cache) == 1


def test_a_vanished_device_link_reads_without_a_model(tmp_path: Path) -> None:
    base = _usb_adapter(tmp_path, "eth1")
    (base / "device").resolve().rename(tmp_path / "gone")
    assert _linux(tmp_path).read("eth1") == Adapter("eth1", mac="9c:69:d3:ac:16:ab")


def test_a_device_link_outside_sysfs_devices_has_no_model(tmp_path: Path) -> None:
    from openfollow.net_adapters import _usb_model

    outside = tmp_path / "elsewhere"
    outside.mkdir()
    assert _usb_model(outside, tmp_path) == ""
    assert _usb_model(tmp_path / "missing", tmp_path) == ""


# --- macOS --------------------------------------------------------------------

_NETWORKSETUP = """
Hardware Port: Ethernet Adapter (en4)
Device: en4
Ethernet Address: CA:DD:87:36:64:08

Hardware Port: USB 10/100/1000 LAN
Device: en7
Ethernet Address: 00:e0:4c:68:00:01

Hardware Port: Thunderbolt Bridge
Device: bridge0
Ethernet Address: N/A

VLAN Configurations
===================
"""


def test_macos_names_each_device_by_its_hardware_port() -> None:
    assert parse_hardware_ports(_NETWORKSETUP) == {
        "en4": ("Ethernet Adapter", "ca:dd:87:36:64:08"),
        "en7": ("USB 10/100/1000 LAN", "00:e0:4c:68:00:01"),
        "bridge0": ("Thunderbolt Bridge", ""),
    }


def test_a_device_line_before_any_port_is_ignored() -> None:
    assert parse_hardware_ports("Device: en9\nEthernet Address: 01:02:03:04:05:06\n") == {}


def test_macos_reads_the_list_once_and_again_only_for_a_new_name_later() -> None:
    calls: list[list[str]] = []
    now = [100.0]

    def run(argv, **_kwargs):
        calls.append(argv)
        return SimpleNamespace(stdout=_NETWORKSETUP)

    reader = AdapterReader(platform="darwin", run=run, clock=lambda: now[0])
    assert reader.read("en7") == Adapter("en7", port="USB 10/100/1000 LAN", mac="00:e0:4c:68:00:01")
    assert reader.read("en4").port == "Ethernet Adapter"
    assert calls == [["networksetup", "-listallhardwareports"]]
    # A name it did not list waits out the refresh interval before it asks again.
    assert reader.read("en9") == Adapter("en9")
    assert len(calls) == 1
    now[0] += 11.0
    assert reader.read("en9") == Adapter("en9")
    assert len(calls) == 2


@pytest.mark.parametrize("error", [OSError("no networksetup"), subprocess.TimeoutExpired("networksetup", 2.0)])
def test_macos_without_the_list_describes_nothing(error: Exception) -> None:
    def run(*_args, **_kwargs):
        raise error

    assert AdapterReader(platform="darwin", run=run).read("en0") == Adapter("en0")


def test_other_platforms_describe_nothing() -> None:
    assert AdapterReader(platform="win32").read("Ethernet") == Adapter("Ethernet")


# --- The process-wide reader --------------------------------------------------


def test_the_shared_reader_describes_and_can_be_swapped(tmp_path: Path) -> None:
    _usb_adapter(tmp_path, "enx9c69d3ac16ab")
    previous = net_adapters.set_reader(_linux(tmp_path))
    try:
        assert net_adapters.reader().read("enx9c69d3ac16ab").port == "USB 2, port 2"
        assert describe("enx9c69d3ac16ab").model == "ASIX AX88179B"
        assert adapter_fields("enx9c69d3ac16ab", {"enx9c69d3ac16ab": "Lighting"}) == {
            "label": "Lighting",
            "description": "USB 2, port 2 · ASIX AX88179B",
            "port": "USB 2, port 2",
            "model": "ASIX AX88179B",
            "mac": "9c:69:d3:ac:16:ab",
            "vlan_on": "",
        }
    finally:
        net_adapters.set_reader(previous)


def test_a_vlan_shows_no_mac_of_its_own(tmp_path: Path) -> None:
    base = _iface(tmp_path, "eth0.13", ifindex=6, mac="88:a2:9e:df:04:e3", uevent="DEVTYPE=vlan\n")
    (base / "lower_eth0").symlink_to(tmp_path)
    previous = net_adapters.set_reader(_linux(tmp_path))
    try:
        fields = adapter_fields("eth0.13", {})
    finally:
        net_adapters.set_reader(previous)
    assert (fields["mac"], fields["vlan_on"], fields["description"]) == ("", "13 on eth0", "VLAN 13 on eth0")
