#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Assert the discovery beacon and marker-catalog sync leave only where pinned.

Runs **on the device**, as root, with no dependencies - an OpenFollow station
ships no ``tcpdump`` and the offline-runtime contract rules out installing one.

Both streams are multicast and both follow the station interface. When that
interface has no address they must go silent, because an unbound multicast
socket does not reach "all interfaces" - it follows the routing table onto one
interface nobody chose, carrying this station's name, version and **web port**
to a network the operator excluded.

Watches **every** interface at once, which is the point: a leak that moved to a
second adapter reads as containment on the one interface you thought to watch.

    # nothing should be leaving anywhere
    sudo /opt/openfollow/venv/bin/python discovery_containment_probe.py \\
        --seconds 12 --expect silent

    # traffic should be leaving eth0 and nowhere else
    sudo /opt/openfollow/venv/bin/python discovery_containment_probe.py \\
        --seconds 12 --expect only --on eth0

Exit code is 0 when the expectation held and 1 when it did not, so a shell can
assert either direction. ``--seconds`` must comfortably exceed the slower
stream's period (beacon 2 s, catalog heartbeat 5 s); the default samples
several of each, because a window shorter than the interval reads an idle gap
as silence.
"""

from __future__ import annotations

import argparse
import json
import os
import select
import socket
import struct
import sys
import time

_ETH_P_ALL = 0x0003
_ETH_P_IP = 0x0800
_ETH_P_8021Q = 0x8100
_IPPROTO_UDP = 17

BEACON_PORT = 50505
CATALOG_PORT = 50506
_STREAMS = {BEACON_PORT: "discovery-beacon", CATALOG_PORT: "marker-catalog-sync"}


def _parse_udp(frame: bytes) -> tuple[str, str, int] | None:
    """Decode one ethernet frame into ``(src, dst, dport)``.

    Returns ``None`` for anything that is not IPv4/UDP - ARP, IPv6, and the
    truncated reads a raw socket can hand back.
    """
    if len(frame) < 14:
        return None
    ethertype = struct.unpack("!H", frame[12:14])[0]
    offset = 14
    if ethertype == _ETH_P_8021Q:
        if len(frame) < 18:
            return None
        ethertype = struct.unpack("!H", frame[16:18])[0]
        offset = 18
    if ethertype != _ETH_P_IP or len(frame) < offset + 20:
        return None
    ihl = (frame[offset] & 0x0F) * 4
    if frame[offset + 9] != _IPPROTO_UDP or len(frame) < offset + ihl + 8:
        return None
    src = socket.inet_ntoa(frame[offset + 12 : offset + 16])
    dst = socket.inet_ntoa(frame[offset + 16 : offset + 20])
    _sport, dport = struct.unpack("!HH", frame[offset + ihl : offset + ihl + 4])
    return src, dst, dport


def _interfaces() -> list[str]:
    """Every interface the kernel currently has, loopback included.

    Loopback is watched deliberately: it is somewhere traffic can go that is
    neither the pin nor a leak, and reporting it keeps a zero elsewhere
    honest.
    """
    return sorted(name for _idx, name in socket.if_nameindex())


def capture(ifaces: list[str], seconds: float, ports: set[int]) -> dict[str, dict[str, int]]:
    """Tally matching datagrams per interface, per stream, over one window."""
    socks: dict[int, tuple[socket.socket, str]] = {}
    tally: dict[str, dict[str, int]] = {i: {} for i in ifaces}
    try:
        for iface in ifaces:
            s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(_ETH_P_ALL))
            s.bind((iface, 0))
            s.setblocking(False)
            socks[s.fileno()] = (s, iface)

        by_fd = dict(socks)
        end = time.monotonic() + seconds
        while True:
            remaining = end - time.monotonic()
            if remaining <= 0:
                break
            ready, _, _ = select.select(list(by_fd), [], [], min(remaining, 0.5))
            for fd in ready:
                sock, iface = by_fd[fd]
                try:
                    frame = sock.recv(65535)
                except BlockingIOError:
                    continue
                parsed = _parse_udp(frame)
                if parsed is None:
                    continue
                src, _dst, dport = parsed
                if dport not in ports:
                    continue
                key = f"{_STREAMS[dport]} from {src}"
                tally[iface][key] = tally[iface].get(key, 0) + 1
    finally:
        for sock, _iface in socks.values():
            sock.close()
    return tally


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seconds", type=float, default=12.0, help="capture window (default 12)")
    ap.add_argument(
        "--expect",
        choices=("silent", "only", "report"),
        default="report",
        help="silent: no station traffic anywhere. only: traffic on --on and nowhere else. report: just print.",
    )
    ap.add_argument("--on", default="", help="the interface traffic is pinned to, for --expect only")
    ap.add_argument(
        "--from-ip",
        default="",
        help="count only datagrams with this source address; without it every sender on the LAN counts, "
        "including peer stations legitimately sending their own beacons",
    )
    ap.add_argument("--iface", action="append", default=[], help="interface to watch (repeatable; default all)")
    ap.add_argument("--json", action="store_true", help="emit the tally as JSON")
    args = ap.parse_args()

    if os.geteuid() != 0:
        print("must run as root (AF_PACKET)", file=sys.stderr)
        return 2
    if args.expect == "only" and not args.on:
        print("--expect only requires --on <iface>", file=sys.stderr)
        return 2

    ifaces = args.iface or _interfaces()
    print(f"watching {', '.join(ifaces)} for {args.seconds:.0f}s", flush=True)
    tally = capture(ifaces, args.seconds, set(_STREAMS))

    if args.from_ip:
        suffix = f" from {args.from_ip}"
        tally = {i: {k: v for k, v in rows.items() if k.endswith(suffix)} for i, rows in tally.items()}

    if args.json:
        print(json.dumps(tally, indent=2))
    else:
        for iface in ifaces:
            rows = tally[iface]
            if not rows:
                print(f"  {iface:20s} (nothing)")
                continue
            for key, count in sorted(rows.items()):
                print(f"  {iface:20s} {count:5d}  {key}")

    totals = {i: sum(rows.values()) for i, rows in tally.items()}
    if args.expect == "report":
        return 0
    if args.expect == "silent":
        noisy = {i: n for i, n in totals.items() if n}
        if noisy:
            print(f"FAIL: expected silence, saw {noisy}")
            return 1
        print("PASS: silent on every interface")
        return 0

    elsewhere = {i: n for i, n in totals.items() if n and i not in (args.on, "lo")}
    if elsewhere:
        print(f"FAIL: traffic left interfaces other than {args.on}: {elsewhere}")
        return 1
    if not totals.get(args.on):
        print(f"FAIL: expected traffic on {args.on}, saw none - the control is not proven")
        return 1
    print(f"PASS: {totals[args.on]} datagrams on {args.on}, none elsewhere")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
