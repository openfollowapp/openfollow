#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Output datagram sizes vs the MTU – DUT-local, no companion.

A UDP datagram larger than 1472 B (a 1500 B Ethernet MTU less the 20 B IPv4 and
8 B UDP headers) is fragmented by IP. A quiet bench LAN reassembles the
fragments and everything looks fine; a loaded show network drops one and the
whole datagram is lost, so the failure only appears where it cannot be
debugged. Each output protocol spreads a large marker set over several
datagrams instead: PSN over the packets of one frame, OTP over the pages of one
folio, RTTrPM over independent packets.

This drives the *deployed* send paths (``PsnServer._send_data_packet``,
``OtpServer._send_transform_packet``, ``RttrpmServer._send_packet`` and the
advertisement/info streams) with the send seam captured, so what is measured is
what the unit under test would put on the wire. Run on the DUT, from the repo
root::

    poetry run python scripts/hw_validation/output_datagram_size_probe.py

Exit 0 = PASS (no stream exceeds the MTU at any tested marker count), 1 = FAIL.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

from openfollow.otp.server import OtpServer
from openfollow.packet_chunking import MAX_DATAGRAM_BYTES
from openfollow.psn.marker import Marker
from openfollow.psn.server import PsnServer
from openfollow.rttrpm.server import RttrpmServer

# Spans every stream's historical crossing point: PSN data at 14, OTP transform
# at 32, RTTrPM at 35, OTP name advertisement at 36, PSN info at 85.
MARKER_COUNTS = (4, 14, 32, 36, 64, 100)

# Operator labels are what push the name-carrying streams over first.
NAME_TEMPLATE = "Followspot Operator Position {:02d}"


def _markers(count: int) -> list[Marker]:
    markers = []
    for index in range(1, count + 1):
        marker = Marker(index, NAME_TEMPLATE.format(index))
        marker.set_pos(float(index), float(index) * -2.0, float(index) * 0.5)
        markers.append(marker)
    return markers


def _psn(count: int) -> dict[str, list[bytes]]:
    server = PsnServer(mcast_ip=None)
    sent: list[bytes] = []
    server._send = lambda data, stop_event=None: sent.append(data)
    for marker in _markers(count):
        server._markers[marker.marker_id] = marker
    server._send_data_packet()
    data = list(sent)
    sent.clear()
    server._send_info_packet()
    return {"PSN data": data, "PSN info": list(sent)}


def _otp(count: int) -> dict[str, list[bytes]]:
    server = OtpServer(system_number=1)
    sent: list[bytes] = []
    server._send = lambda data, dest_ip, stop_event=None: sent.append(data)
    for marker in _markers(count):
        server.register_marker(marker)
    server._send_transform_packet()
    transform = list(sent)
    sent.clear()
    server._send_advertisement_packets()
    return {"OTP transform": transform, "OTP advertisement": list(sent)}


def _rttrpm(count: int) -> dict[str, list[bytes]]:
    server = RttrpmServer(host="127.0.0.1")
    server._socket = MagicMock()
    sent: list[bytes] = []
    server._send = lambda data, stop_event=None: sent.append(data)
    for marker in _markers(count):
        server.register_marker(marker)
    server._send_packet()
    return {"RTTrPM": sent}


def _streams(count: int) -> dict[str, list[bytes]]:
    return {**_psn(count), **_otp(count), **_rttrpm(count)}


def main() -> int:
    print(f"Output datagram sizes against the {MAX_DATAGRAM_BYTES} B MTU budget:\n", flush=True)
    failures = []
    for count in MARKER_COUNTS:
        print(f"  {count:>4} markers", flush=True)
        for stream, datagrams in _streams(count).items():
            if not datagrams:
                failures.append(f"{stream} sent nothing at {count} markers")
                print(f"      {stream:<20} NOTHING SENT", flush=True)
                continue
            largest = max(len(datagram) for datagram in datagrams)
            over = largest > MAX_DATAGRAM_BYTES
            if over:
                failures.append(f"{stream} emitted {largest} B at {count} markers")
            note = "  <- FRAGMENTS" if over else ""
            print(
                f"      {stream:<20} {len(datagrams):>2} datagram(s)  largest={largest:>5} B{note}",
                flush=True,
            )

    if failures:
        print("\nFAIL: a stream exceeded the MTU budget:", flush=True)
        for failure in failures:
            print(f"  - {failure}", flush=True)
        return 1
    print("\nPASS: every stream stays inside the MTU at every tested marker count.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
