#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Check what a station sends its DHCP server: its FQDN (option 81) or its hostname (option 12).

Runs **on the companion, as root**, on an interface that shares a network with
the station, and watches for the station's DHCP requests by MAC address:

    sudo python3 dhcp_fqdn_probe.py --iface eth0 --mac 02:00:5e:00:53:01 \\
        --expect-fqdn of-1.stage.example.com

    sudo python3 dhcp_fqdn_probe.py --iface eth0 --mac 02:00:5e:00:53:01 \\
        --expect-hostname openfollow-noble-bear

Start it, then save the Station FQDN (or press Remove FQDN): the save reconnects
every interface, and a reconnecting client broadcasts its request, so it reaches
the companion. A renewal is unicast to the server and is not seen here, so a
quiet window is inconclusive, never a pass.

With an FQDN, every request must carry option 81 naming it, with the S flag
(the server updates the A record) and the E flag (the name in DNS wire format),
and no option 12: RFC 4702 s3.1 forbids a client sending both. Without one,
every request must carry option 12 naming the hostname, and no option 81.

Frames are read from an AF_PACKET socket, since stations carry no tcpdump.
Exit code 0 when every request matched, 1 when one did not, 2 when none came.
"""

from __future__ import annotations

import argparse
import socket
import struct
import sys
import time
from dataclasses import dataclass

ETH_P_ALL = 0x0003
_ETH_P_IP = 0x0800
_UDP = 17
_DHCP_PORTS = {67, 68}
_MAGIC_COOKIE = b"\x63\x82\x53\x63"
# The client messages that name the client; RELEASE and DECLINE carry no name to judge.
_CLIENT_TYPES = {1: "DISCOVER", 3: "REQUEST"}
_FLAG_S = 0x01
_FLAG_E = 0x04

PASS, FAIL, INCONCLUSIVE = 0, 1, 2


@dataclass(frozen=True)
class ClientMessage:
    """One DHCP client message: its type, sender and naming options."""

    kind: str
    mac: str
    hostname: str | None
    fqdn: str | None
    fqdn_flags: int | None


def _dns_wire_name(data: bytes) -> str:
    labels: list[str] = []
    i = 0
    while i < len(data) and data[i]:
        length = data[i]
        labels.append(data[i + 1 : i + 1 + length].decode("ascii", errors="replace"))
        i += 1 + length
    return ".".join(labels)


def parse_client_message(frame: bytes) -> ClientMessage | None:
    """The DHCP client message in an Ethernet frame, or None for anything else."""
    if len(frame) < 34 or struct.unpack("!H", frame[12:14])[0] != _ETH_P_IP or frame[23] != _UDP:
        return None
    udp = 14 + (frame[14] & 0x0F) * 4
    if len(frame) < udp + 8:
        return None
    sport, dport = struct.unpack("!HH", frame[udp : udp + 4])
    if {sport, dport} != _DHCP_PORTS:
        return None
    bootp = frame[udp + 8 :]
    if len(bootp) < 240 or bootp[0] != 1 or bootp[236:240] != _MAGIC_COOKIE:
        return None
    options: dict[int, bytes] = {}
    data, i = bootp[240:], 0
    while i < len(data) and data[i] != 255:
        if data[i] == 0:
            i += 1
            continue
        if i + 1 >= len(data):
            break
        code, length = data[i], data[i + 1]
        options[code] = data[i + 2 : i + 2 + length]
        i += 2 + length
    kind = _CLIENT_TYPES.get((options.get(53) or b"\x00")[0])
    if kind is None:
        return None
    fqdn = fqdn_flags = None
    if 81 in options and len(options[81]) >= 3:
        fqdn_flags = options[81][0]
        name = options[81][3:]
        fqdn = _dns_wire_name(name) if fqdn_flags & _FLAG_E else name.decode("ascii", errors="replace")
    hostname = options[12].decode("ascii", errors="replace") if 12 in options else None
    mac = ":".join(f"{b:02x}" for b in bootp[28:34])
    return ClientMessage(kind=kind, mac=mac, hostname=hostname, fqdn=fqdn, fqdn_flags=fqdn_flags)


def problems(message: ClientMessage, *, expect_fqdn: str = "", expect_hostname: str = "") -> list[str]:
    """What is wrong with ``message`` for the expectation; empty when it matches."""
    found: list[str] = []
    if expect_fqdn:
        if message.fqdn is None:
            found.append("no option 81")
        else:
            if message.fqdn.lower().rstrip(".") != expect_fqdn.lower().rstrip("."):
                found.append(f"option 81 names {message.fqdn!r}")
            flags = message.fqdn_flags or 0
            if not flags & _FLAG_S:
                found.append("option 81 does not ask the server to update DNS (S flag clear)")
            if not flags & _FLAG_E:
                found.append("option 81 name is not in DNS wire format (E flag clear)")
        if message.hostname is not None:
            found.append(f"option 12 sent alongside it ({message.hostname!r})")
    else:
        if message.hostname != expect_hostname:
            found.append("no option 12" if message.hostname is None else f"option 12 names {message.hostname!r}")
        if message.fqdn is not None:
            found.append(f"option 81 still sent ({message.fqdn!r})")
    return found


def verdict(
    messages: list[ClientMessage], *, expect_fqdn: str = "", expect_hostname: str = ""
) -> tuple[int, list[str]]:
    """The exit code and one line per message for what was seen."""
    if not messages:
        return INCONCLUSIVE, ["no DHCP request from the station in the window"]
    code = PASS
    lines: list[str] = []
    for message in messages:
        found = problems(message, expect_fqdn=expect_fqdn, expect_hostname=expect_hostname)
        if found:
            code = FAIL
        lines.append(
            f"{'FAIL' if found else 'ok  '} {message.kind} from {message.mac}: " + ("; ".join(found) or "as expected")
        )
    return code, lines


def capture(iface: str, macs: set[str], seconds: float) -> list[ClientMessage]:
    sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.ntohs(ETH_P_ALL))
    sock.bind((iface, 0))
    sock.settimeout(0.5)
    end = time.monotonic() + seconds
    seen: list[ClientMessage] = []
    while time.monotonic() < end:
        try:
            frame = sock.recv(65535)
        except TimeoutError:
            continue
        message = parse_client_message(frame)
        if message is not None and message.mac in macs:
            seen.append(message)
            print(f"seen {message.kind} from {message.mac}", flush=True)
    return seen


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--iface", required=True, help="companion interface on the station's network")
    parser.add_argument("--mac", action="append", required=True, help="station MAC to watch (repeatable)")
    expect = parser.add_mutually_exclusive_group(required=True)
    expect.add_argument("--expect-fqdn", default="")
    expect.add_argument("--expect-hostname", default="")
    parser.add_argument("--seconds", type=float, default=60.0)
    args = parser.parse_args(argv)
    messages = capture(args.iface, {mac.lower() for mac in args.mac}, args.seconds)
    code, lines = verdict(messages, expect_fqdn=args.expect_fqdn, expect_hostname=args.expect_hostname)
    print("\n".join(lines))
    print({PASS: "PASS", FAIL: "FAIL", INCONCLUSIVE: "INCONCLUSIVE"}[code])
    return code


if __name__ == "__main__":
    sys.exit(main())
