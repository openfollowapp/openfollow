#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Prove a pinned sender leaves on its interface, or not at all.

Runs **on the device, as root**, with the station's own interpreter so it
exercises the deployed ``openfollow.net_egress.pin_socket_egress``:

    sudo /opt/openfollow/venv/bin/python egress_pin_probe.py \\
        --iface eth0.13 --other-dest 192.0.2.50

``--other-dest`` must be an address the routing table sends out of a
*different* interface than ``--iface``, and one that answers ARP there (a host
on that network, or anything beyond its gateway): an unanswered address puts
only an ARP request on the wire. Every leak check first sends the same
traffic unpinned as a control. A control that never takes a path a leak
would, leaves the check **inconclusive** rather than passed: that capture
could not have seen a leak either.

A VLAN shares its parent's cable, so the parent is watched too. There a frame
tagged with the pinned VLAN is its own traffic; an untagged one is a leak.
Pinned to a parent, anything on one of its VLANs is a leak.

Checks:

- the pin is accepted from the unprivileged service user (``--user``);
- unicast, limited broadcast and multicast from a pinned socket never appear
  on another interface;
- a pinned TCP connect puts no SYN on another interface;
- what a pinned socket does with 127.0.0.1 (reported, not asserted).

Frames are read from AF_PACKET sockets, since stations carry no tcpdump.
Exit code 0 when every check held, 1 on a leak or a refused pin, 2 when nothing
leaked but some check was inconclusive.
"""

from __future__ import annotations

import argparse
import os
import pwd
import secrets
import socket
import struct
import subprocess
import sys
import time

ETH_P_ALL = 0x0003
_VLAN_TPIDS = (0x8100, 0x88A8)
SETTLE_S = 1.5
# The kernel lifts an 802.1Q tag out of a received frame into socket metadata;
# PACKET_AUXDATA hands it back. CPython exports neither constant.
_SOL_PACKET = 263
_PACKET_AUXDATA = 8
_TP_STATUS_VLAN_VALID = 1 << 4
_AUXDATA_FMT = "IIIHHHH"
_AUXDATA_LEN = struct.calcsize(_AUXDATA_FMT)

PASS, LEAK, INCONCLUSIVE = "ok", "FAIL", "????"


def interface_addresses() -> dict[str, str]:
    """Every interface carrying an IPv4 address, excluding loopback."""
    out = subprocess.run(["ip", "-4", "-brief", "address"], capture_output=True, text=True, check=True).stdout
    return parse_brief_addresses(out)


def parse_brief_addresses(listing: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in listing.splitlines():
        parts = line.split()
        # A VLAN child is listed as ``eth0.13@eth0``.
        name = parts[0].split("@", 1)[0] if parts else ""
        if len(parts) >= 3 and name != "lo":
            found[name] = parts[2].split("/")[0]
    return found


def watched_interfaces(pinned: str, addresses: dict[str, str]) -> list[str]:
    """Every addressed interface but the pinned one, plus a pinned VLAN's parent."""
    watched = [iface for iface in addresses if iface != pinned]
    parent = pinned.split(".", 1)[0]
    if parent != pinned and parent not in watched:
        watched.append(parent)
    return watched


def own_traffic(capture_iface: str, vlan: int | None, pinned: str) -> bool:
    """Whether a frame seen on *capture_iface* is the pinned interface's own."""
    parent, _, vid = pinned.partition(".")
    return capture_iface == parent and vid.isdigit() and vlan == int(vid)


def frame_vlan(frame: bytes, ancillary: list[tuple[int, int, bytes]]) -> int | None:
    """The frame's VLAN id, read in-band (egress) or from the stripped-tag metadata."""
    if len(frame) >= 16 and struct.unpack_from("!H", frame, 12)[0] in _VLAN_TPIDS:
        return struct.unpack_from("!H", frame, 14)[0] & 0x0FFF
    for level, cmsg_type, data in ancillary:
        if level == _SOL_PACKET and cmsg_type == _PACKET_AUXDATA and len(data) >= _AUXDATA_LEN:
            status, _len, _snap, _mac, _net, tci, _tpid = struct.unpack(_AUXDATA_FMT, data[:_AUXDATA_LEN])
            if status & _TP_STATUS_VLAN_VALID:
                return tci & 0x0FFF
    return None


def ip_payload_offset(frame: bytes) -> int | None:
    """Offset of the IPv4 header in an Ethernet frame, or None when it is not IPv4."""
    offset = 12
    ethertype = struct.unpack_from("!H", frame, offset)[0] if len(frame) >= 14 else 0
    while ethertype in _VLAN_TPIDS and len(frame) >= offset + 6:
        offset += 4
        ethertype = struct.unpack_from("!H", frame, offset)[0]
    return offset + 2 if ethertype == 0x0800 else None


def is_tcp_syn_to(frame: bytes, dest: str, port: int) -> bool:
    start = ip_payload_offset(frame)
    if start is None or len(frame) < start + 20 or frame[start + 9] != socket.IPPROTO_TCP:
        return False
    if socket.inet_ntoa(frame[start + 16 : start + 20]) != dest:
        return False
    tcp = start + (frame[start] & 0x0F) * 4
    if len(frame) < tcp + 14:
        return False
    dport = struct.unpack_from("!H", frame, tcp + 2)[0]
    flags = frame[tcp + 13]
    return dport == port and bool(flags & 0x02) and not flags & 0x10


class Capture:
    """Raw frames, with their VLAN, seen on each interface while it is open."""

    def __init__(self, ifaces: list[str]) -> None:
        self._socks: dict[str, socket.socket] = {}
        for iface in ifaces:
            sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_ALL))  # type: ignore[attr-defined]
            sock.bind((iface, 0))
            sock.setsockopt(_SOL_PACKET, _PACKET_AUXDATA, 1)
            sock.setblocking(False)
            self._socks[iface] = sock

    def drain(self) -> dict[str, list[tuple[bytes, int | None]]]:
        frames: dict[str, list[tuple[bytes, int | None]]] = {iface: [] for iface in self._socks}
        deadline = time.monotonic() + SETTLE_S
        while time.monotonic() < deadline:
            for iface, sock in self._socks.items():
                try:
                    while True:
                        frame, ancillary, _flags, _addr = sock.recvmsg(65535, socket.CMSG_SPACE(_AUXDATA_LEN))
                        frames[iface].append((frame, frame_vlan(frame, ancillary)))
                except BlockingIOError:
                    pass
            time.sleep(0.02)
        return frames

    def close(self) -> None:
        for sock in self._socks.values():
            sock.close()


def pinned_socket(kind: int, egress: object, *, multicast: bool = False) -> socket.socket:
    from openfollow.net_egress import pin_socket_egress

    sock = socket.socket(socket.AF_INET, kind)
    pin_socket_egress(sock, egress, multicast=multicast)  # type: ignore[arg-type]
    return sock


def udp_send(sock: socket.socket, dest: str, token: bytes, *, broadcast: bool = False) -> str:
    try:
        if broadcast:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for _ in range(3):
            sock.sendto(token, (dest, 9))
            time.sleep(0.05)
        return "sent"
    except OSError as exc:
        return f"refused ({exc.strerror})"
    finally:
        sock.close()


def leaks(frames: dict[str, list[tuple[bytes, int | None]]], pinned: str, match: object) -> list[str]:
    """Interfaces where a matching frame was not the pinned interface's own."""
    return [
        iface
        for iface, seen in frames.items()
        if any(match(frame) and not own_traffic(iface, vlan, pinned) for frame, vlan in seen)  # type: ignore[operator]
    ]


def leak_check(label: str, pinned: str, watched: list[str], send: object, control: object) -> tuple[str, str]:
    """Run *control* unpinned, then *send* pinned; the control must take a leak's path."""
    capture = Capture(watched)
    try:
        control_token = secrets.token_bytes(12)
        control(control_token)  # type: ignore[operator]
        control_seen = leaks(capture.drain(), pinned, lambda frame: control_token in frame)
        token = secrets.token_bytes(12)
        outcome = send(token)  # type: ignore[operator]
        leaked = leaks(capture.drain(), pinned, lambda frame: token in frame)
    finally:
        capture.close()
    return verdict(label, leaked, control_seen, outcome)


def verdict(label: str, leaked: list[str], control_seen: list[str], outcome: str) -> tuple[str, str]:
    if leaked:
        return LEAK, f"{label}: pinned traffic appeared on {', '.join(leaked)} ({outcome})"
    if not control_seen:
        return INCONCLUSIVE, f"{label}: the unpinned control took no path a leak would, so nothing to compare"
    return PASS, f"{label}: control left on {', '.join(control_seen)}; pinned {outcome}, nothing leaked"


def unprivileged_pin(user: str, iface: str, address: str) -> tuple[str, str]:
    """Apply the pin from the service user in a forked child."""
    from openfollow.net_egress import Egress

    entry = pwd.getpwnam(user)
    pid = os.fork()
    if pid == 0:  # pragma: no cover - runs in the child
        try:
            os.setgid(entry.pw_gid)
            os.setuid(entry.pw_uid)
            pinned_socket(socket.SOCK_DGRAM, Egress(iface, address)).close()
            os._exit(0)
        except OSError:
            os._exit(1)
    _pid, status = os.waitpid(pid, 0)
    ok = os.waitstatus_to_exitcode(status) == 0
    return (PASS if ok else LEAK), f"pin as {user}: {'accepted' if ok else 'refused'}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--iface", required=True, help="interface to pin to")
    parser.add_argument("--other-dest", required=True, help="unicast address routed out of another interface")
    parser.add_argument("--group", default="239.20.20.20", help="multicast group for the multicast check")
    parser.add_argument("--user", default="openfollow", help="unprivileged user the station runs as")
    args = parser.parse_args()

    if not sys.platform.startswith("linux") or os.geteuid() != 0:
        print("FAIL: run on the station, as root (AF_PACKET capture needs it)")
        return 1
    from openfollow.net_egress import Egress

    addresses = interface_addresses()
    if args.iface not in addresses:
        print(f"FAIL: {args.iface} has no IPv4 address; interfaces: {', '.join(addresses) or 'none'}")
        return 1
    egress = Egress(args.iface, addresses[args.iface])
    watched = watched_interfaces(egress.iface, addresses)
    print(f"pinned to {egress.iface} ({egress.address}); watching {', '.join(watched) or 'nothing'}\n")
    if not watched:
        print("FAIL: no other interface to catch a leak on")
        return 1

    def _plain(kind: int = socket.SOCK_DGRAM) -> socket.socket:
        return socket.socket(socket.AF_INET, kind)

    results = [unprivileged_pin(args.user, egress.iface, egress.address)]
    for label, dest, multicast, broadcast in (
        ("unicast", args.other_dest, False, False),
        ("limited broadcast", "255.255.255.255", False, True),
        ("multicast", args.group, True, False),
    ):
        results.append(
            leak_check(
                label,
                egress.iface,
                watched,
                lambda token, d=dest, m=multicast, b=broadcast: udp_send(
                    pinned_socket(socket.SOCK_DGRAM, egress, multicast=m), d, token, broadcast=b
                ),
                lambda token, d=dest, b=broadcast: udp_send(_plain(), d, token, broadcast=b),
            )
        )
    results.append(tcp_check(egress, watched, args.other_dest))

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
        receiver.bind(("127.0.0.1", 0))
        receiver.settimeout(0.5)
        loop_outcome = udp_send(pinned_socket(socket.SOCK_DGRAM, egress), "127.0.0.1", b"loopback")
        try:
            receiver.recv(64)
            loopback = "delivered"
        except OSError:
            loopback = "not delivered"
    print(f"  info  loopback while pinned: {loop_outcome}, {loopback}")

    for status, line in results:
        print(f"  {status}  {line}")
    statuses = {status for status, _ in results}
    if LEAK in statuses:
        print("\nFAIL")
        return 1
    if INCONCLUSIVE in statuses:
        print("\nNO LEAK, but some checks were inconclusive")
        return 2
    print("\nPASS")
    return 0


def tcp_connect(sock: socket.socket, dest: str, port: int) -> str:
    try:
        sock.settimeout(1.0)
        sock.connect((dest, port))
        return "connected"
    except OSError as exc:
        return f"refused ({exc.strerror or exc})"
    finally:
        sock.close()


def tcp_check(egress: object, watched: list[str], dest: str) -> tuple[str, str]:
    """A pinned connect must put no SYN where a leak would go; an unpinned one shows the path."""
    pinned_iface = egress.iface  # type: ignore[attr-defined]
    capture = Capture(watched)
    try:
        control_port = 40000 + secrets.randbelow(10000)
        tcp_connect(socket.socket(socket.AF_INET, socket.SOCK_STREAM), dest, control_port)
        control_seen = leaks(capture.drain(), pinned_iface, lambda frame: is_tcp_syn_to(frame, dest, control_port))
        port = 50000 + secrets.randbelow(10000)
        outcome = tcp_connect(pinned_socket(socket.SOCK_STREAM, egress), dest, port)
        leaked = leaks(capture.drain(), pinned_iface, lambda frame: is_tcp_syn_to(frame, dest, port))
    finally:
        capture.close()
    return verdict("tcp connect", leaked, control_seen, outcome)


if __name__ == "__main__":
    raise SystemExit(main())
