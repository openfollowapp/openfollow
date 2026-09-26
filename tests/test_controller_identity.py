# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Where a controller is plugged in: the socket key and its label.

The fake sysfs trees follow a Pi 5: two xHCI host controllers, each with a
USB 2 and a USB 3 root hub.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from openfollow.input import controller_identity as ci
from openfollow.input.controller_identity import natural_sort_key, port_label, resolve_key, usb_host_paths

pytestmark = pytest.mark.unit

_HOST0 = "platform/axi/1000120000.pcie/1f00200000.usb/xhci-hcd.0"
_HOST1 = "platform/axi/1000120000.pcie/1f00300000.usb/xhci-hcd.1"


def _usb_device(sysfs: Path, host: str, hub: str, name: str, devpath: str) -> Path:
    device = sysfs / "devices" / host / hub / name
    device.mkdir(parents=True, exist_ok=True)
    (device / "devpath").write_text(f"{devpath}\n")
    (device / "busnum").write_text(f"{hub.removeprefix('usb')}\n")
    return device


def _node(sysfs: Path, cls: str, name: str, target: Path) -> str:
    target.mkdir(parents=True, exist_ok=True)
    entry = sysfs / "class" / cls / name
    entry.mkdir(parents=True, exist_ok=True)
    (entry / "device").symlink_to(target)
    return f"/dev/{'input/' if cls == 'input' else ''}{name}"


def _root_hubs(sysfs: Path) -> None:
    bus = sysfs / "bus" / "usb" / "devices"
    bus.mkdir(parents=True, exist_ok=True)
    for host, hubs in ((_HOST0, ("usb1", "usb2")), (_HOST1, ("usb3", "usb4"))):
        for hub in hubs:
            target = sysfs / "devices" / host / hub
            target.mkdir(parents=True, exist_ok=True)
            (bus / hub).symlink_to(target)


def test_a_pad_and_a_puck_in_one_socket_share_its_key(tmp_path: Path) -> None:
    device = _usb_device(tmp_path, _HOST1, "usb3", "3-1", "1")
    pad = _node(tmp_path, "input", "event1", device / "3-1:1.0" / "0003:3537:1082.0001" / "input" / "input5")
    puck = _node(tmp_path, "hidraw", "hidraw0", device / "3-1:1.0" / "0003:3537:1082.0001")
    assert resolve_key(pad, sysfs_root=tmp_path) == f"usb:{_HOST1}:1"
    assert resolve_key(puck, sysfs_root=tmp_path) == f"usb:{_HOST1}:1"


def test_each_interface_of_one_device_is_one_socket(tmp_path: Path) -> None:
    device = _usb_device(tmp_path, _HOST1, "usb3", "3-1", "1")
    first = _node(tmp_path, "hidraw", "hidraw0", device / "3-1:1.0" / "0003:046D:C626.0001")
    second = _node(tmp_path, "hidraw", "hidraw1", device / "3-1:1.1" / "0003:046D:C626.0002")
    assert resolve_key(first, sysfs_root=tmp_path) == resolve_key(second, sysfs_root=tmp_path)


def test_the_usb2_and_usb3_bus_of_one_controller_give_a_socket_one_key(tmp_path: Path) -> None:
    # A renumbered bus changes busnum and the root hub, never the host or devpath.
    slow = _usb_device(tmp_path, _HOST1, "usb3", "3-1", "1")
    fast = _usb_device(tmp_path, _HOST1, "usb4", "4-1", "1")
    a = _node(tmp_path, "input", "event1", slow / "3-1:1.0" / "input" / "input5")
    b = _node(tmp_path, "input", "event2", fast / "4-1:1.0" / "input" / "input6")
    assert resolve_key(a, sysfs_root=tmp_path) == resolve_key(b, sysfs_root=tmp_path)


def test_sockets_on_different_controllers_and_ports_differ(tmp_path: Path) -> None:
    left = _usb_device(tmp_path, _HOST0, "usb1", "1-1", "1")
    right = _usb_device(tmp_path, _HOST1, "usb3", "3-1", "1")
    black = _usb_device(tmp_path, _HOST0, "usb1", "1-2", "2")
    keys = {
        resolve_key(_node(tmp_path, "input", f"event{i}", dev / "x:1.0" / "input" / f"input{i}"), sysfs_root=tmp_path)
        for i, dev in enumerate((left, right, black), start=1)
    }
    assert len(keys) == 3


def test_a_hub_chain_is_part_of_the_key(tmp_path: Path) -> None:
    hub = _usb_device(tmp_path, _HOST0, "usb1", "1-1", "1")
    device = _usb_device(tmp_path, _HOST0, "usb1/1-1", "1-1.4", "1.4")
    assert hub.is_dir()
    node = _node(tmp_path, "input", "event3", device / "1-1.4:1.0" / "input" / "input9")
    assert resolve_key(node, sysfs_root=tmp_path) == f"usb:{_HOST0}:1.4"


def test_a_bluetooth_pad_is_keyed_by_its_address(tmp_path: Path) -> None:
    input_dev = tmp_path / "devices" / "platform" / "serial0" / "bluetooth" / "hci0" / "hci0:11" / "input" / "input7"
    node = _node(tmp_path, "input", "event4", input_dev)
    (input_dev / "uniq").write_text("AA:BB:CC:DD:EE:FF\n")
    assert resolve_key(node, sysfs_root=tmp_path) == "bt:aa:bb:cc:dd:ee:ff"


def test_a_bluetooth_hidraw_node_is_keyed_by_hid_uniq(tmp_path: Path) -> None:
    hid = tmp_path / "devices" / "platform" / "bluetooth" / "hci0" / "hci0:12" / "0005:057E:2009.0003"
    node = _node(tmp_path, "hidraw", "hidraw2", hid)
    (hid / "uevent").write_text("HID_ID=0005:0000057E:00002009\nHID_UNIQ=11:22:33:44:55:66\n")
    assert resolve_key(node, sysfs_root=tmp_path) == "bt:11:22:33:44:55:66"


@pytest.mark.parametrize("content", ["uniq", "uevent"])
def test_a_bluetooth_device_without_an_address_has_no_key(tmp_path: Path, content: str) -> None:
    dev = tmp_path / "devices" / "platform" / "bluetooth" / "hci0" / "hci0:13" / "input" / "input8"
    node = _node(tmp_path, "input", "event5", dev)
    if content == "uniq":
        (dev / "uniq").write_text("\n")
    else:
        (dev / "uevent").write_text("HID_UNIQ=\n")
    assert resolve_key(node, sysfs_root=tmp_path) is None


def test_a_bluetooth_path_with_no_address_anywhere_has_no_key(tmp_path: Path) -> None:
    node = _node(tmp_path, "input", "event5", tmp_path / "devices" / "platform" / "bluetooth" / "input" / "input8")
    assert resolve_key(node, sysfs_root=tmp_path) is None


def test_a_uniq_outside_bluetooth_is_not_a_key(tmp_path: Path) -> None:
    dev = tmp_path / "devices" / "platform" / "i2c-1" / "input" / "input3"
    node = _node(tmp_path, "input", "event6", dev)
    (dev / "uniq").write_text("serial\n")
    assert resolve_key(node, sysfs_root=tmp_path) is None


def test_a_device_directly_on_a_root_hub_has_no_key(tmp_path: Path) -> None:
    hub = _usb_device(tmp_path, _HOST1, "", "usb3", "0")
    node = _node(tmp_path, "input", "event7", hub / "input" / "input1")
    assert resolve_key(node, sysfs_root=tmp_path) is None


def test_a_usb_device_reporting_no_devpath_has_no_key(tmp_path: Path) -> None:
    device = _usb_device(tmp_path, _HOST1, "usb3", "3-1", "")
    node = _node(tmp_path, "input", "event1", device / "3-1:1.0" / "input" / "input5")
    assert resolve_key(node, sysfs_root=tmp_path) is None


def test_a_usb_device_outside_any_root_hub_has_no_key(tmp_path: Path) -> None:
    device = _usb_device(tmp_path, "platform/odd", "hub", "x-1", "1")
    node = _node(tmp_path, "input", "event8", device / "x-1:1.0" / "input" / "input2")
    assert resolve_key(node, sysfs_root=tmp_path) is None


@pytest.mark.parametrize("node", [None, "", "/dev/input/event99"])
def test_an_unknown_node_has_no_key(tmp_path: Path, node: str | None) -> None:
    assert resolve_key(node, sysfs_root=tmp_path) is None


def test_a_class_link_leading_outside_sysfs_devices_has_no_key(tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere" / "input" / "input1"
    node = _node(tmp_path, "input", "event9", elsewhere)
    (tmp_path / "devices").mkdir()
    assert resolve_key(node, sysfs_root=tmp_path) is None


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads files regardless of mode")
def test_an_unreadable_attribute_has_no_key(tmp_path: Path) -> None:
    device = _usb_device(tmp_path, _HOST1, "usb3", "3-1", "1")
    node = _node(tmp_path, "input", "event1", device / "3-1:1.0" / "input" / "input5")
    (device / "devpath").chmod(0)
    try:
        assert resolve_key(node, sysfs_root=tmp_path) is None
    finally:
        (device / "devpath").chmod(0o644)


def test_the_live_sysfs_is_read_only_on_linux(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    device = _usb_device(tmp_path, _HOST1, "usb3", "3-1", "1")
    node = _node(tmp_path, "input", "event1", device / "3-1:1.0" / "input" / "input5")
    monkeypatch.setattr(ci, "_SYSFS", tmp_path)
    monkeypatch.setattr(ci.sys, "platform", "linux")
    assert resolve_key(node) == f"usb:{_HOST1}:1"
    monkeypatch.setattr(ci.sys, "platform", "darwin")
    assert resolve_key(node) is None


def test_host_controllers_are_numbered_in_a_stable_order(tmp_path: Path) -> None:
    _root_hubs(tmp_path)
    assert usb_host_paths(tmp_path) == (_HOST0, _HOST1)


def test_host_controllers_of_this_machine_are_scanned_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _root_hubs(tmp_path)
    monkeypatch.setattr(ci, "_SYSFS", tmp_path)
    monkeypatch.setattr(ci.sys, "platform", "linux")
    ci._system_usb_host_paths.cache_clear()
    try:
        assert usb_host_paths() == (_HOST0, _HOST1)
        (tmp_path / "bus" / "usb" / "devices" / "usb1").unlink()
        assert usb_host_paths() == (_HOST0, _HOST1)
    finally:
        ci._system_usb_host_paths.cache_clear()
    monkeypatch.setattr(ci.sys, "platform", "darwin")
    assert usb_host_paths() == ()


def test_no_usb_bus_numbers_nothing(tmp_path: Path) -> None:
    assert usb_host_paths(tmp_path) == ()


@pytest.mark.parametrize(
    ("key", "label"),
    [
        (f"usb:{_HOST1}:1", "USB 2 · port 1"),
        (f"usb:{_HOST0}:1.4", "USB 1 · port 1.4"),
        ("usb:platform/unknown-host:2", "USB · port 2"),
        ("bt:aa:bb:cc:dd:ee:ff", "Bluetooth"),
        (None, "no stable port"),
        ("mystery:1", "no stable port"),
    ],
)
def test_port_labels(key: str | None, label: str) -> None:
    assert port_label(key, (_HOST0, _HOST1)) == label


def test_ports_sort_numerically() -> None:
    keys = [f"usb:{_HOST0}:1.10", f"usb:{_HOST1}:1", f"usb:{_HOST0}:1.2", f"usb:{_HOST0}:2"]
    assert sorted(keys, key=natural_sort_key) == [
        f"usb:{_HOST0}:1.2",
        f"usb:{_HOST0}:1.10",
        f"usb:{_HOST0}:2",
        f"usb:{_HOST1}:1",
    ]
