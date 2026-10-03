# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Which physical adapter an interface name is, in words an operator recognises.

A name says little about the hardware behind it: two USB adapters named after
their MACs differ only in their last few characters. This reads where each one
is plugged in and what it is, and pairs a name with the label the operator gave
it, so every surface can say "Lighting (enx9c69d3ac16ab)" instead.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# A label shares one Operator Screen row with the kernel name.
LABEL_MAX_LEN = 20
_IFNAME_MAX = 15

_SYSFS = Path("/sys")
_ZERO_MAC = "00:00:00:00:00:00"
# Strings a USB device reports about itself are untrusted and unbounded.
_DESCRIPTOR_MAX_LEN = 48
_CONTROL_RE = re.compile("[\x00-\x1f\x7f\u200e-\u200f\u202a-\u202e]")
_CACHE_MAX = 64
_IP_TIMEOUT_S = 1.0
_NETWORKSETUP_TIMEOUT_S = 2.0
# How often macOS's hardware-port list is re-read for a name it did not list.
_HARDWARE_PORTS_REFRESH_S = 10.0
_HARDWARE_PORT_SUFFIX_RE = re.compile(r"\s*\([a-z]+\d+\)$")

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def display_name(name: str, labels: Mapping[str, str]) -> str:
    """``Lighting (enx9c69d3ac16ab)`` for a labelled interface, the bare name otherwise."""
    label = labels.get(name, "") if name else ""
    return f"{label} ({name})" if label else name


def label_conflict(labels: Mapping[str, str], iface: str, label: str) -> str | None:
    """Why *label* cannot go on *iface*, or None. Labels are unique, ignoring case."""
    wanted = label.strip().casefold()
    if not wanted:
        return None
    for other, taken in sorted(labels.items()):
        if other != iface and taken.casefold() == wanted:
            return f"'{taken}' is already the label of {other}."
    return None


def clean_label(raw: object) -> str:
    """A stored label: text only, no control or direction characters, at most LABEL_MAX_LEN."""
    if not isinstance(raw, str):
        return ""
    return _CONTROL_RE.sub("", raw).strip()[:LABEL_MAX_LEN].strip()


def normalize_labels(raw: object) -> dict[str, str]:
    """Labels as stored: valid names only, cleaned and non-empty, and of two equal labels the
    one on the name that sorts first."""
    if not isinstance(raw, dict):
        return {}
    named = {key.strip(): value for key, value in raw.items() if isinstance(key, str)}
    out: dict[str, str] = {}
    taken: set[str] = set()
    for name in sorted(key for key in named if valid_iface_name(key)):
        label = clean_label(named[name])
        if label and label.casefold() not in taken:
            taken.add(label.casefold())
            out[name] = label
    return out


def valid_iface_name(name: object) -> bool:
    """Whether *name* could be a kernel interface name, and is safe in a sysfs path."""
    return (
        isinstance(name, str)
        and 0 < len(name) <= _IFNAME_MAX
        and not name.startswith(".")
        and not any(ch.isspace() or ch in "/:" for ch in name)
    )


@dataclass(frozen=True)
class Adapter:
    """What sits behind one interface name. An empty field is unknown."""

    name: str
    port: str = ""
    model: str = ""
    mac: str = ""
    vlan_parent: str = ""
    vlan_id: int | None = None

    def vlan_text(self, labels: Mapping[str, str]) -> str:
        """``13 on Production (eth0)``; empty for anything but a VLAN."""
        if not self.vlan_parent:
            return ""
        tag = f"{self.vlan_id} on " if self.vlan_id is not None else "on "
        return tag + display_name(self.vlan_parent, labels)

    def where(self, labels: Mapping[str, str]) -> str:
        """The short form a picker shows: the port, or the VLAN and its parent."""
        if self.vlan_parent:
            return f"VLAN {self.vlan_text(labels)}"
        return self.port

    def summary(self, labels: Mapping[str, str]) -> str:
        """One line: ``USB 2, port 2 · ASIX AX88179B``, or ``VLAN 13 on Production (eth0)``."""
        if self.vlan_parent:
            return self.where(labels)
        return " · ".join(part for part in (self.port, self.model) if part)


class AdapterReader:
    """Reads adapters from sysfs (Linux) or macOS's hardware-port list. Never raises.

    Linux answers are cached per name and interface index, so a replugged
    adapter that comes back under the same name is read again.
    """

    def __init__(
        self,
        *,
        sysfs_root: Path | None = None,
        platform: str = sys.platform,
        run: Runner = subprocess.run,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._root = sysfs_root or _SYSFS
        # The system's USB host list is cached for the process; a test tree is scanned each time.
        self._hosts_root = sysfs_root
        self._platform = platform
        self._run = run
        self._clock = clock
        self._lock = threading.Lock()
        self._cache: dict[tuple[str, str], Adapter] = {}
        self._hardware_ports: dict[str, tuple[str, str]] = {}
        self._hardware_ports_read_at: float | None = None

    def read(self, name: str) -> Adapter:
        if not valid_iface_name(name):
            return Adapter(name)
        if self._platform.startswith("linux"):
            return self._read_linux(name)
        if self._platform == "darwin":
            port, mac = self._macos_ports(name).get(name, ("", ""))
            return Adapter(name, port=port, mac=mac)
        return Adapter(name)

    def _read_linux(self, name: str) -> Adapter:
        base = self._root / "class" / "net" / name
        ifindex = _read(base / "ifindex")
        if not ifindex:
            return Adapter(name)
        key = (name, ifindex)
        with self._lock:
            hit = self._cache.get(key)
        if hit is not None:
            return hit
        adapter = self._describe_linux(name, base)
        with self._lock:
            if len(self._cache) >= _CACHE_MAX:
                self._cache.clear()
            self._cache[key] = adapter
        return adapter

    def _describe_linux(self, name: str, base: Path) -> Adapter:
        if _uevent(base).get("DEVTYPE") == "vlan":
            parent = next((link.name.removeprefix("lower_") for link in sorted(base.glob("lower_*"))), "")
            return Adapter(name, vlan_parent=parent, vlan_id=self._vlan_id(name))
        mac = _read(base / "address").lower()
        mac = "" if mac == _ZERO_MAC else mac
        device = base / "device"
        if not device.exists():
            return Adapter(name, mac=mac)
        # Deferred: the input package's own imports reach back into configuration, which imports this.
        from openfollow.input.controller_identity import port_label, resolve_net_key, usb_host_paths

        usb_key = resolve_net_key(name, sysfs_root=self._root)
        if usb_key is not None:
            port = port_label(usb_key, usb_host_paths(self._hosts_root))
            return Adapter(name, port=port, model=_usb_model(device, self._root), mac=mac)
        wireless = (base / "wireless").exists() or (base / "phy80211").exists()
        return Adapter(name, port="Built-in Wi-Fi" if wireless else "Built-in Ethernet", mac=mac)

    def _vlan_id(self, name: str) -> int | None:
        try:
            done = self._run(
                ["ip", "-j", "-d", "link", "show", "dev", name],
                capture_output=True,
                text=True,
                timeout=_IP_TIMEOUT_S,
                check=False,
            )
            found = json.loads(done.stdout)[0]["linkinfo"]["info_data"]["id"]
            if isinstance(found, int) and not isinstance(found, bool):
                return found
        except (OSError, subprocess.SubprocessError, ValueError, LookupError, TypeError):
            pass
        # Every VLAN this station creates is named <parent>.<id>.
        tail = name.rpartition(".")[2]
        return int(tail) if name.count(".") and tail.isdigit() else None

    def _macos_ports(self, name: str) -> dict[str, tuple[str, str]]:
        with self._lock:
            read_at = self._hardware_ports_read_at
            recent = read_at is not None and self._clock() - read_at < _HARDWARE_PORTS_REFRESH_S
            if name in self._hardware_ports or recent:
                return self._hardware_ports
        ports = self._list_hardware_ports()
        with self._lock:
            self._hardware_ports = ports
            self._hardware_ports_read_at = self._clock()
            return ports

    def _list_hardware_ports(self) -> dict[str, tuple[str, str]]:
        try:
            done = self._run(
                ["networksetup", "-listallhardwareports"],
                capture_output=True,
                text=True,
                timeout=_NETWORKSETUP_TIMEOUT_S,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return {}
        return parse_hardware_ports(done.stdout or "")


def parse_hardware_ports(text: str) -> dict[str, tuple[str, str]]:
    """``networksetup -listallhardwareports`` as ``{device: (port, mac)}``."""
    ports: dict[str, tuple[str, str]] = {}
    port = device = ""
    for line in text.splitlines():
        key, _sep, value = line.partition(":")
        value = value.strip()
        if key == "Hardware Port":
            port, device = _clean(_HARDWARE_PORT_SUFFIX_RE.sub("", value)), ""
        elif key == "Device" and port and value:
            device = value
            ports[device] = (port, "")
        elif key == "Ethernet Address" and device and value.upper() != "N/A":
            ports[device] = (ports[device][0], value.lower())
    return ports


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="ascii", errors="replace").strip()
    except OSError:
        return ""


def _uevent(base: Path) -> dict[str, str]:
    lines = _read(base / "uevent").splitlines()
    return dict(line.partition("=")[::2] for line in lines if "=" in line)


def _clean(text: str) -> str:
    return _CONTROL_RE.sub("", text).strip()[:_DESCRIPTOR_MAX_LEN].strip()


def _usb_model(device: Path, sysfs_root: Path) -> str:
    """``ASIX AX88179B``: the strings the USB device reports about itself."""
    try:
        node = device.resolve(strict=True)
        devices = (sysfs_root / "devices").resolve()
    except OSError:
        return ""
    while devices in node.parents:
        if (node / "idVendor").is_file():
            maker = _clean(_read(node / "manufacturer"))
            product = _clean(_read(node / "product"))
            if maker and product.casefold().startswith(maker.casefold()):
                return product
            return " ".join(part for part in (maker, product) if part)
        node = node.parent
    return ""


_reader = AdapterReader()


def reader() -> AdapterReader:
    """The process-wide reader."""
    return _reader


def set_reader(new: AdapterReader) -> AdapterReader:
    """Swap the process-wide reader; returns the previous one. For tests."""
    global _reader
    previous, _reader = _reader, new
    return previous


def describe(name: str) -> Adapter:
    """The adapter behind *name*, from the process-wide reader."""
    return _reader.read(name)


def adapter_fields(name: str, labels: Mapping[str, str]) -> dict[str, Any]:
    """The per-interface fields a web row or the Operator Screen shows."""
    adapter = describe(name)
    return {
        "label": labels.get(name, ""),
        "description": adapter.summary(labels),
        "port": adapter.port,
        "model": adapter.model,
        "mac": adapter.mac,
        "vlan_on": adapter.vlan_text(labels),
    }
