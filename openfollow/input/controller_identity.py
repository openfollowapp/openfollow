# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Where a controller is plugged in: a key that is the same every start.

The key names the socket, not the device or the order it was probed in, and it
is the same for every kind of controller: a gamepad's ``/dev/input/event*`` and
a 3D mouse's ``/dev/hidraw*`` walk through sysfs to the same USB device.
"""

from __future__ import annotations

import functools
import re
import sys
from collections.abc import Sequence
from pathlib import Path

_SYSFS = Path("/sys")
_ROOT_HUB_RE = re.compile(r"usb\d+")
_NATURAL_CHUNK_RE = re.compile(r"(\d+)")

USB_KEY_PREFIX = "usb:"
BT_KEY_PREFIX = "bt:"


def resolve_key(node: str | None, *, sysfs_root: Path | None = None) -> str | None:
    """Key of the socket ``node`` sits in, or ``None`` when it has none.

    USB: ``usb:<host controller>:<devpath>``. ``devpath`` is the port chain
    without the bus number, so a renumbered bus keeps the key, and the USB 2
    and USB 3 root hubs of one controller give a socket the same key.
    Bluetooth: ``bt:<uniq>``. Never raises.
    """
    if sysfs_root is None:
        if not sys.platform.startswith("linux"):
            return None
        sysfs_root = _SYSFS
    if not node:
        return None
    try:
        device = _class_device(Path(node).name, sysfs_root)
        if device is None:
            return None
        return _usb_key(device, sysfs_root) or _bluetooth_key(device)
    except OSError:
        return None


def _class_device(name: str, sysfs_root: Path) -> Path | None:
    for cls in ("input", "hidraw"):
        link = sysfs_root / "class" / cls / name / "device"
        if link.exists():
            return link.resolve(strict=True)
    return None


def _usb_key(device: Path, sysfs_root: Path) -> str | None:
    devices = (sysfs_root / "devices").resolve()
    for node in (device, *device.parents):
        if node == devices or devices not in node.parents:
            return None
        if (node / "devpath").is_file() and (node / "busnum").is_file():
            devpath = (node / "devpath").read_text(encoding="ascii").strip()
            root_hub = next((p for p in node.parents if _ROOT_HUB_RE.fullmatch(p.name)), None)
            if root_hub is None or not devpath:
                return None
            return f"{USB_KEY_PREFIX}{root_hub.parent.relative_to(devices).as_posix()}:{devpath}"
    return None


def _bluetooth_key(device: Path) -> str | None:
    if "bluetooth" not in device.parts:
        return None
    for node in (device, *device.parents):
        uniq = node / "uniq"
        if uniq.is_file():
            value = uniq.read_text(encoding="ascii", errors="replace").strip().lower()
            return f"{BT_KEY_PREFIX}{value}" if value else None
        uevent = node / "uevent"
        if uevent.is_file():
            for line in uevent.read_text(encoding="ascii", errors="replace").splitlines():
                if line.startswith("HID_UNIQ="):
                    value = line.partition("=")[2].strip().lower()
                    return f"{BT_KEY_PREFIX}{value}" if value else None
    return None


def usb_host_paths(sysfs_root: Path | None = None) -> tuple[str, ...]:
    """Host controllers on this machine, in the order their labels number them."""
    if sysfs_root is None:
        if not sys.platform.startswith("linux"):
            return ()
        return _system_usb_host_paths()
    return _scan_usb_host_paths(sysfs_root)


@functools.cache
def _system_usb_host_paths() -> tuple[str, ...]:
    # Host controllers are fixed hardware, so one scan serves the process.
    return _scan_usb_host_paths(_SYSFS)


def _scan_usb_host_paths(sysfs_root: Path) -> tuple[str, ...]:
    try:
        devices = (sysfs_root / "devices").resolve()
        hosts = {
            hub.resolve().parent.relative_to(devices).as_posix()
            for hub in (sysfs_root / "bus" / "usb" / "devices").iterdir()
            if _ROOT_HUB_RE.fullmatch(hub.name)
        }
    except (OSError, ValueError):
        return ()
    return tuple(sorted(hosts, key=natural_sort_key))


def port_label(key: str | None, hosts: Sequence[str] = ()) -> str:
    """Short, readable name of a socket: ``USB 2 · port 1.4``."""
    if key is None:
        return "no stable port"
    if key.startswith(BT_KEY_PREFIX):
        return "Bluetooth"
    if key.startswith(USB_KEY_PREFIX):
        host, _sep, devpath = key[len(USB_KEY_PREFIX) :].rpartition(":")
        if host in hosts:
            return f"USB {hosts.index(host) + 1} · port {devpath}"
        return f"USB · port {devpath}"
    return "no stable port"


def natural_sort_key(value: str) -> tuple[tuple[int, int | str], ...]:
    """Sort key that orders embedded numbers numerically (``1.10`` after ``1.2``)."""
    return tuple((0, int(chunk)) if chunk.isdigit() else (1, chunk) for chunk in _NATURAL_CHUNK_RE.split(value))
