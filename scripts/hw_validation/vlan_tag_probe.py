#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Report which 802.1Q tag (if any) a station's traffic leaves an interface with.

Runs **on the device**, as root, with no dependencies – an OpenFollow station
ships neither ``tcpdump`` nor a pip toolchain, and the offline-runtime contract
means a validation step cannot assume an uplink to install one.

Tagging is done by the **host**, not the switch: sending via ``eth0.10`` makes
the kernel add the VLAN header before the frame reaches ``eth0``. So capturing
on the parent proves the pin is honoured even on an access port, where the
switch would drop the frame downstream. Only end-to-end delivery to another
station needs a trunk.

Exit code is 0 when at least one matching frame was seen, 1 when none were –
so "PSN stopped" and "PSN is flowing" are both assertable from a shell.

    sudo /opt/openfollow/venv/bin/python vlan_tag_probe.py --iface eth0 \\
        --dst 236.10.10.10 --seconds 6
"""

from __future__ import annotations

import argparse
import json
import socket
import struct
import sys
import time

_ETH_P_ALL = 0x0003
_ETH_P_IP = 0x0800
_ETH_P_8021Q = 0x8100
_IPPROTO_UDP = 17


def _parse(frame: bytes) -> dict[str, object] | None:
    """Decode one ethernet frame into ``{vlan, src, dst, sport, dport}``.

    Returns ``None`` for anything that is not IPv4/UDP – including ARP, IPv6
    and the truncated reads a raw socket can hand back.
    """
    if len(frame) < 14:
        return None
    ethertype = struct.unpack("!H", frame[12:14])[0]
    vlan: int | None = None
    offset = 14
    if ethertype == _ETH_P_8021Q:
        if len(frame) < 18:
            return None
        # The low 12 bits of the TCI are the VLAN ID; the top 4 are priority
        # and the drop-eligible bit, which are not part of the identity.
        vlan = struct.unpack("!H", frame[14:16])[0] & 0x0FFF
        ethertype = struct.unpack("!H", frame[16:18])[0]
        offset = 18
    if ethertype != _ETH_P_IP or len(frame) < offset + 20:
        return None
    ihl = (frame[offset] & 0x0F) * 4
    if frame[offset + 9] != _IPPROTO_UDP or len(frame) < offset + ihl + 8:
        return None
    src = socket.inet_ntoa(frame[offset + 12 : offset + 16])
    dst = socket.inet_ntoa(frame[offset + 16 : offset + 20])
    sport, dport = struct.unpack("!HH", frame[offset + ihl : offset + ihl + 4])
    return {"vlan": vlan, "src": src, "dst": dst, "sport": sport, "dport": dport}


def capture(iface: str, dst: str, seconds: float, limit: int) -> list[dict[str, object]]:
    sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(_ETH_P_ALL))
    try:
        sock.bind((iface, 0))
        sock.settimeout(0.5)
        deadline = time.monotonic() + seconds
        seen: list[dict[str, object]] = []
        while time.monotonic() < deadline and len(seen) < limit:
            try:
                frame = sock.recv(65535)
            except TimeoutError:
                continue
            parsed = _parse(frame)
            if parsed is not None and (not dst or parsed["dst"] == dst):
                seen.append(parsed)
        return seen
    finally:
        sock.close()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--iface", required=True, help="interface to capture on (the PARENT, not the VLAN)")
    p.add_argument("--dst", default="", help="only report frames to this IPv4 destination")
    p.add_argument("--seconds", type=float, default=6.0)
    p.add_argument("--limit", type=int, default=40)
    p.add_argument("--json", action="store_true", help="emit the frame list as JSON")
    args = p.parse_args()

    try:
        seen = capture(args.iface, args.dst, args.seconds, args.limit)
    except PermissionError:
        print("Raw capture needs root.", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"Capture on {args.iface} failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(seen))
    else:
        tags = sorted({("untagged" if f["vlan"] is None else f"vlan {f['vlan']}") for f in seen})
        print(f"frames={len(seen)} tags={', '.join(tags) if tags else '(none)'}")
        for f in seen[:5]:
            tag = "untagged" if f["vlan"] is None else f"vlan {f['vlan']}"
            print(f"  {tag:<10} {f['src']}:{f['sport']} -> {f['dst']}:{f['dport']}")
    return 0 if seen else 1


if __name__ == "__main__":
    sys.exit(main())
