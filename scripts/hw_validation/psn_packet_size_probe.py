#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""PSN receive-buffer / datagram-truncation probe – DUT-local, no companion.

A PSN V2 data packet is ~104 B/tracker plus header, so a single datagram crosses
1500 B at ~15 trackers. If the receive buffer is sized to 1500, the kernel
silently truncates the tail – markers beyond ~15 vanish *every frame* on a
multi-marker install, with no error (the truncated buffer still parses, just
short). This probe builds real PSN datagrams with the deployed encoder
(``Marker.to_psn_marker`` + ``pypsn.prepare_psn_data_packet_bytes``), round-trips
them through a real loopback UDP socket at 1500 vs 65535, and asserts the
deployed receiver's ``recvfrom`` buffer is large enough for a realistic packet.

Run on the DUT (deployed build), from the repo root::

    poetry run python scripts/hw_validation/psn_packet_size_probe.py

Exit 0 = PASS (deployed buffer recovers every tracker), 1 = FAIL. Guards the
``recvfrom`` sizing in ``openfollow/psn/receiver.py``.
"""

from __future__ import annotations

import inspect
import re
import socket
import sys
import time

import pypsn

from openfollow.psn import receiver as receiver_module
from openfollow.psn.marker import Marker

# Tracker counts to characterise: 14 fits in 1500, the rest don't.
TRACKER_COUNTS = (14, 16, 20, 40)


def _build_packet(n: int) -> bytes:
    """Encode an n-tracker PSN data packet exactly as the app's PsnServer does."""
    markers = []
    for i in range(1, n + 1):
        m = Marker(i, f"Marker {i}")
        m.set_pos(float(i), float(i) * 2.0, float(i) * 3.0)
        markers.append(m)
    info = pypsn.PsnInfo(
        timestamp=int(time.time() * 1000),
        version_high=2,
        version_low=0,
        frame_id=0,
        packet_count=1,
    )
    pkt = pypsn.PsnDataPacket(info=info, trackers=[m.to_psn_marker() for m in markers])
    return pypsn.prepare_psn_data_packet_bytes(pkt)


def _trackers_received(rx: socket.socket, tx: socket.socket, port: int, data: bytes, bufsize: int) -> int:
    """Send ``data`` to the loopback receiver and parse it back at ``bufsize``.

    Returns the tracker count parsed, or -1 if the (truncated) buffer fails to
    parse at all.
    """
    tx.sendto(data, ("127.0.0.1", port))
    raw, _ = rx.recvfrom(bufsize)
    try:
        return len(pypsn.parse_psn_packet(raw).trackers)
    except Exception:  # noqa: BLE001 – any parse failure on a truncated buffer
        return -1


def _deployed_recv_bufsize() -> int:
    """The recvfrom buffer size the deployed receiver actually uses."""
    src = inspect.getsource(receiver_module._RobustReceiver.run)
    match = re.search(r"recvfrom\((\d+)\)", src)
    return int(match.group(1)) if match else 0


def main() -> int:
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("127.0.0.1", 0))
    port = rx.getsockname()[1]
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    largest = 0
    print("Datagram size vs receive-buffer truncation (loopback):", flush=True)
    for n in TRACKER_COUNTS:
        data = _build_packet(n)
        largest = max(largest, len(data))
        at_1500 = _trackers_received(rx, tx, port, data, 1500)
        at_full = _trackers_received(rx, tx, port, data, 65535)
        note = "" if at_1500 == n else f"  <- recvfrom(1500) loses {n - at_1500} tracker(s)"
        print(
            f"  N={n:3d}  encoded={len(data):5d}B  recvfrom(1500)={at_1500:>4}  recvfrom(65535)={at_full:>4}{note}",
            flush=True,
        )

    app_bufsize = _deployed_recv_bufsize()
    ok = app_bufsize >= largest
    print(
        f"\nDeployed receiver recvfrom buffer = {app_bufsize} B; largest tested packet = {largest} B.",
        flush=True,
    )
    print(
        f"{'PASS' if ok else 'FAIL'}: deployed buffer "
        f"{'covers' if ok else 'TRUNCATES'} a realistic multi-marker packet.",
        flush=True,
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
