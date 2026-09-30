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
traffic unpinned as a control: if the control does not show up on another
interface either, the capture is blind and the check is reported as such
rather than as a pass.

Checks:

- the pin is accepted from the unprivileged service user (``--user``);
- unicast, limited broadcast and multicast from a pinned socket never appear
  on another interface;
- a pinned TCP connect puts no SYN on another interface;
- what a pinned socket does with 127.0.0.1 (reported, not asserted).

Frames are read from AF_PACKET sockets, since stations carry no tcpdump.
Exit code 0 when every check held, 1 otherwise.
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


def same_link(a: str, b: str) -> bool:
    """A VLAN child's frames also cross its parent, tagged; that is not a leak."""
    return a.split(".", 1)[0] == b.split(".", 1)[0]


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
    """Raw frames seen on each interface while it is open."""

    def __init__(self, ifaces: list[str]) -> None:
        self._socks: dict[str, socket.socket] = {}
        for iface in ifaces:
            sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_ALL))  # type: ignore[attr-defined]
            sock.bind((iface, 0))
            sock.setblocking(False)
            self._socks[iface] = sock

    def drain(self) -> dict[str, list[bytes]]:
        frames: dict[str, list[bytes]] = {iface: [] for iface in self._socks}
        deadline = time.monotonic() + SETTLE_S
        while time.monotonic() < deadline:
            for iface, sock in self._socks.items():
                try:
                    while True:
                        frames[iface].append(sock.recv(65535))
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


def leak_check(label: str, others: list[str], send: object, control: object) -> tuple[bool, str]:
    """Run *control* unpinned and *send* pinned; a pass needs the control to be seen."""
    capture = Capture(others)
    try:
        control_token = secrets.token_bytes(12)
        control(control_token)  # type: ignore[operator]
        control_seen = [iface for iface, frames in capture.drain().items() if any(control_token in f for f in frames)]
        token = secrets.token_bytes(12)
        outcome = send(token)  # type: ignore[operator]
        leaked = [iface for iface, frames in capture.drain().items() if any(token in f for f in frames)]
    finally:
        capture.close()
    if leaked:
        return False, f"{label}: pinned traffic appeared on {', '.join(leaked)} ({outcome})"
    if not control_seen:
        return False, f"{label}: control never reached another interface, so the capture proves nothing"
    return True, f"{label}: control seen on {', '.join(control_seen)}; pinned {outcome}, nothing leaked"


def unprivileged_pin(user: str, iface: str, address: str) -> tuple[bool, str]:
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
    return ok, f"pin as {user}: {'accepted' if ok else 'refused'}"


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
    others = [iface for iface in addresses if not same_link(iface, args.iface)]
    print(f"pinned to {egress.iface} ({egress.address}); watching {', '.join(others) or 'nothing'}\n")
    if not others:
        print("FAIL: no other interface to catch a leak on")
        return 1

    def _plain(kind: int = socket.SOCK_DGRAM) -> socket.socket:
        return socket.socket(socket.AF_INET, kind)

    results = [unprivileged_pin(args.user, egress.iface, egress.address)]
    results.append(
        leak_check(
            "unicast",
            others,
            lambda token: udp_send(pinned_socket(socket.SOCK_DGRAM, egress), args.other_dest, token),
            lambda token: udp_send(_plain(), args.other_dest, token),
        )
    )
    results.append(
        leak_check(
            "limited broadcast",
            others,
            lambda token: udp_send(pinned_socket(socket.SOCK_DGRAM, egress), "255.255.255.255", token, broadcast=True),
            lambda token: udp_send(_plain(), "255.255.255.255", token, broadcast=True),
        )
    )
    results.append(
        leak_check(
            "multicast",
            others,
            lambda token: udp_send(pinned_socket(socket.SOCK_DGRAM, egress, multicast=True), args.group, token),
            lambda token: udp_send(_plain(), args.group, token),
        )
    )

    port = 40000 + secrets.randbelow(20000)
    capture = Capture(others)
    try:
        sock = pinned_socket(socket.SOCK_STREAM, egress)
        sock.settimeout(1.0)
        try:
            sock.connect((args.other_dest, port))
            tcp_outcome = "connected"
        except OSError as exc:
            tcp_outcome = f"refused ({exc.strerror or exc})"
        finally:
            sock.close()
        syns = [
            i for i, frames in capture.drain().items() if any(is_tcp_syn_to(f, args.other_dest, port) for f in frames)
        ]
    finally:
        capture.close()
    results.append((not syns, f"tcp connect: {tcp_outcome}; SYN on {', '.join(syns) or 'no other interface'}"))

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

    for ok, line in results:
        print(f"  {'ok  ' if ok else 'FAIL'}  {line}")
    passed = all(ok for ok, _ in results)
    print("\nPASS" if passed else "\nFAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
