#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Raw-UDP reachability probe – isolates network delivery from the OSC layer.

Run BEFORE the operator-message harness when a run records zero packets, to
tell a network problem apart from an application one. Pure ``socket`` (no
deps), so it runs under the system ``python3``.

On the DUT::      python3 scripts/hw_validation/raw_udp_probe.py listen --port 8765
On the sender::   python3 scripts/hw_validation/raw_udp_probe.py send --host 192.168.178.59 --port 8765

If ``listen`` records nothing while ``send`` reports success, packets are being
dropped below the application (switch/AP client isolation, port-specific
behaviour, etc.), not by the OSC code. We hit exactly this on an arbitrary
port (8790) while the configured OSC port (8765) worked.
"""

from __future__ import annotations

import argparse
import socket
import time


def listen(port: int, window: float) -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("", port))
    s.settimeout(1.0)
    print(f"listening on {port}", flush=True)
    end = time.time() + window
    n = 0
    while time.time() < end:
        try:
            data, addr = s.recvfrom(2048)
            n += 1
            print(f"  recv from {addr}: {data[:48]!r}", flush=True)
        except TimeoutError:
            continue
    print(f"DONE: {n} datagrams", flush=True)


def send(host: str, port: int, count: int) -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    for i in range(count):
        s.sendto(f"probe-{i}".encode(), (host, port))
        time.sleep(0.1)
    print(f"sent {count} datagrams to {host}:{port}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Raw-UDP reachability probe.")
    sub = ap.add_subparsers(dest="mode", required=True)
    lp = sub.add_parser("listen")
    lp.add_argument("--port", type=int, default=8790)
    lp.add_argument("--window", type=float, default=12.0)
    sp = sub.add_parser("send")
    sp.add_argument("--host", required=True)
    sp.add_argument("--port", type=int, default=8790)
    sp.add_argument("--count", type=int, default=5)
    args = ap.parse_args()

    if args.mode == "listen":
        listen(args.port, args.window)
    else:
        send(args.host, args.port, args.count)


if __name__ == "__main__":
    main()
