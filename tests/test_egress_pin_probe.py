# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Checks for scripts/hw_validation/egress_pin_probe.py: the frame and listing
parsing a leak verdict rests on."""

from __future__ import annotations

import importlib.util
import inspect
import socket
import struct
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.unit


def _load() -> ModuleType:
    source = inspect.getsourcefile(_load)
    assert source, "Could not resolve current test source path"
    script = Path(source).resolve().parents[1] / "scripts" / "hw_validation" / "egress_pin_probe.py"
    spec = importlib.util.spec_from_file_location("egress_pin_probe", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


probe = _load()


def _frame(
    *,
    vlan: bool = False,
    proto: int = socket.IPPROTO_TCP,
    dest: str = "192.0.2.50",
    port: int = 41000,
    flags: int = 0x02,
) -> bytes:
    eth = b"\x00" * 12 + (struct.pack("!HH", 0x8100, 13) if vlan else b"") + struct.pack("!H", 0x0800)
    ip = struct.pack(
        "!BBHHHBBH4s4s", 0x45, 0, 40, 0, 0, 64, proto, 0, socket.inet_aton("198.51.100.10"), socket.inet_aton(dest)
    )
    tcp = struct.pack("!HHIIBBHHH", 50000, port, 0, 0, 0x50, flags, 0, 0, 0)
    return eth + ip + tcp


def test_brief_listing_names_a_vlan_child_as_config_does() -> None:
    listing = "lo UNKNOWN 127.0.0.1/8\neth0 UP 192.0.2.10/24\neth0.13@eth0 UP 198.51.100.10/24\nwlan0 DOWN\n"
    assert probe.parse_brief_addresses(listing) == {"eth0": "192.0.2.10", "eth0.13": "198.51.100.10"}


@pytest.mark.parametrize(
    ("a", "b", "same"),
    [("eth0.13", "eth0", True), ("eth0", "eth0.13", True), ("eth0", "eth1", False), ("eth0.13", "eth1.13", False)],
)
def test_a_vlan_and_its_parent_count_as_one_link(a: str, b: str, same: bool) -> None:
    assert probe.same_link(a, b) is same


@pytest.mark.parametrize("vlan", [False, True], ids=["untagged", "tagged"])
def test_a_syn_is_found_through_a_vlan_tag(vlan: bool) -> None:
    assert probe.is_tcp_syn_to(_frame(vlan=vlan), "192.0.2.50", 41000) is True


@pytest.mark.parametrize(
    "frame",
    [
        _frame(dest="192.0.2.51"),
        _frame(port=41001),
        _frame(flags=0x12),
        _frame(proto=socket.IPPROTO_UDP),
        b"\x00" * 12 + struct.pack("!H", 0x0806) + b"\x00" * 28,
        b"\x00" * 8,
    ],
    ids=["other-host", "other-port", "syn-ack", "udp", "arp", "runt"],
)
def test_only_a_syn_to_the_probed_port_counts(frame: bytes) -> None:
    assert probe.is_tcp_syn_to(frame, "192.0.2.50", 41000) is False
