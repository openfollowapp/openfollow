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
    ("capture_iface", "vlan", "pinned", "own"),
    [
        ("eth0", 13, "eth0.13", True),
        ("eth0", None, "eth0.13", False),
        ("eth0", 47, "eth0.13", False),
        ("eth0.13", None, "eth0", False),
        ("eth1", None, "eth0", False),
        ("eth1", 13, "eth0.13", False),
    ],
    ids=[
        "tagged-on-parent",
        "untagged-on-parent",
        "other-vlan",
        "on-pinned-parents-vlan",
        "other-nic",
        "other-nic-tag",
    ],
)
def test_only_the_pinned_vlans_tagged_frames_on_its_parent_are_its_own(
    capture_iface: str, vlan: int | None, pinned: str, own: bool
) -> None:
    assert probe.own_traffic(capture_iface, vlan, pinned) is own


def test_a_pinned_vlans_parent_is_watched() -> None:
    assert probe.watched_interfaces("eth0.13", {"eth0": "a", "eth0.13": "b", "eth1": "c"}) == ["eth0", "eth1"]
    assert probe.watched_interfaces("eth0.13", {"eth0.13": "b"}) == ["eth0"]
    assert probe.watched_interfaces("eth0", {"eth0": "a", "eth0.13": "b"}) == ["eth0.13"]


def test_a_tag_is_read_in_band_or_from_the_stripped_metadata() -> None:
    assert probe.frame_vlan(_frame(vlan=True), []) == 13
    aux = struct.pack(probe._AUXDATA_FMT, probe._TP_STATUS_VLAN_VALID, 0, 0, 0, 0, 0x2000 | 47, 0x8100)
    assert probe.frame_vlan(_frame(), [(probe._SOL_PACKET, probe._PACKET_AUXDATA, aux)]) == 47
    no_tag = struct.pack(probe._AUXDATA_FMT, 0, 0, 0, 0, 0, 47, 0)
    assert probe.frame_vlan(_frame(), [(probe._SOL_PACKET, probe._PACKET_AUXDATA, no_tag)]) is None


def test_leaks_skip_the_pinned_interfaces_own_frames() -> None:
    frames = {"eth0": [(b"..token..", 13), (b"..token..", None)], "eth1": [(b"other", None)]}
    assert probe.leaks(frames, "eth0.13", lambda frame: b"token" in frame) == ["eth0"]
    assert probe.leaks({"eth0": [(b"..token..", 13)]}, "eth0.13", lambda frame: b"token" in frame) == []


@pytest.mark.parametrize(
    ("leaked", "control_seen", "status"),
    [(["eth0"], ["eth0"], "FAIL"), ([], [], "????"), ([], ["eth0"], "ok"), (["eth0"], [], "FAIL")],
    ids=["leak", "blind-control", "pass", "leak-beats-blind"],
)
def test_a_blind_control_is_inconclusive_not_a_pass(leaked: list[str], control_seen: list[str], status: str) -> None:
    assert probe.verdict("unicast", leaked, control_seen, "sent")[0] == status


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
