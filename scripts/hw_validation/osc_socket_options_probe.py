#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""OSC broadcast/multicast socket-options probe – DUT-local, no companion.

pythonosc's ``SimpleUDPClient`` defaults to ``allow_broadcast=False``, so a send
to a broadcast address raises ``EACCES`` on *every* datagram (the row silently
never works), and multicast sends go out with implicit TTL/loopback. The
deployed ``OscService._make_client`` classifies the destination and sets the
right socket options. This probe builds clients via the deployed ``_make_client``
on real sockets and asserts:

- **broadcast** (255.255.255.255) → ``SO_BROADCAST`` set and a send succeeds
  (a plain ``SimpleUDPClient`` is shown raising ``EACCES`` for contrast);
- **multicast** (224.0.0.0/4) → ``IP_MULTICAST_TTL`` / ``IP_MULTICAST_LOOP`` set;
- **unicast** → no broadcast option.

Run on the DUT (deployed build), from the repo root::

    poetry run python scripts/hw_validation/osc_socket_options_probe.py

Exit 0 = PASS, 1 = FAIL. Guards ``openfollow/osc/service.py:_make_client``. The
broadcast ``SO_BROADCAST`` check is the regression signal; multicast TTL/LOOP are
reported for inspection (the Linux defaults happen to match, so they're a sanity
check rather than a strict pass/fail).
"""

from __future__ import annotations

import socket
import sys

from pythonosc.udp_client import SimpleUDPClient

from openfollow.osc import service as svc


def main() -> int:
    ok = True

    # Contrast: a plain client must fail on a broadcast address (the bug guarded).
    try:
        SimpleUDPClient("255.255.255.255", 9999).send_message("/probe", [1.0])
        print("plain SimpleUDPClient -> 255.255.255.255: SENT (no EACCES?!)", flush=True)
    except OSError as exc:
        print(f"plain SimpleUDPClient -> 255.255.255.255: {type(exc).__name__} (expected)", flush=True)

    # Broadcast via the deployed _make_client: SO_BROADCAST set + send succeeds.
    bcast = svc._make_client("255.255.255.255", 9999, "udp", "slip")
    so_broadcast = bcast._sock.getsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST)
    try:
        bcast.send_message("/probe", [1.0])
        bcast_sent = True
    except OSError:
        bcast_sent = False
    bcast_ok = bool(so_broadcast) and bcast_sent
    ok = ok and bcast_ok
    print(
        f"_make_client broadcast: SO_BROADCAST={so_broadcast} sent={bcast_sent}  {'PASS' if bcast_ok else 'FAIL'}",
        flush=True,
    )

    # Multicast via the deployed _make_client: TTL + LOOP set (reported).
    mcast = svc._make_client("239.20.20.21", 9999, "udp", "slip")
    ttl = mcast._sock.getsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL)
    loop = mcast._sock.getsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP)
    mcast.send_message("/probe", [1.0])
    print(f"_make_client multicast: IP_MULTICAST_TTL={ttl} IP_MULTICAST_LOOP={loop}  (sent ok)", flush=True)

    # Unicast: no broadcast option.
    uni = svc._make_client("127.0.0.1", 9999, "udp", "slip")
    uni_broadcast = uni._sock.getsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST)
    uni_ok = not uni_broadcast
    ok = ok and uni_ok
    print(f"_make_client unicast: SO_BROADCAST={uni_broadcast}  {'PASS' if uni_ok else 'FAIL'}", flush=True)

    print(f"\n{'PASS' if ok else 'FAIL'}", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
