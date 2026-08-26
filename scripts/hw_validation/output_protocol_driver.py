#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Drive N moving markers out of the deployed PSN / OTP / RTTrPM servers.

A DUT-side traffic generator for validating the outputs against real consumer
software (PSNView, OTPAnalyzer, OTPView, a BlackTrax receiver) rather than
against our own decoders. It starts the deployed server classes - not a
re-implementation - so what leaves the NIC is what the app would send::

    # 40 markers: every stream splits (PSN data 4 packets, OTP transform 2
    # pages, OTP name advertisement 2, RTTrPM 2)
    poetry run python scripts/hw_validation/output_protocol_driver.py --markers 40

    # 13 markers: the control - every stream fits one datagram, nothing splits
    poetry run python scripts/hw_validation/output_protocol_driver.py --markers 13

    # RTTrPM is unicast, so it needs the consumer's address
    poetry run python scripts/hw_validation/output_protocol_driver.py \\
        --markers 40 --rttrpm-host 192.168.178.20

Markers orbit a shared centre so a consumer can be judged on motion rather than
a single static write, and every marker is rewritten every frame: a marker the
loop stops writing goes stale, which PSN publishes as an invalid tracker and
RTTrPM answers by withholding the trackable entirely.

Datagrams are classified by their own header bytes on the way out, so the
per-stream counts reported are what a consumer has to reassemble. Runs until
``--duration`` elapses or Ctrl-C.
"""

from __future__ import annotations

import argparse
import math
import signal
import struct
import sys
import threading
import time
from types import FrameType

import pypsn

from openfollow.otp.server import (
    OTP_PACKET_IDENTIFIER,
    VECTOR_OTP_ADVERTISEMENT_MESSAGE,
    VECTOR_OTP_ADVERTISEMENT_NAME,
    VECTOR_OTP_TRANSFORM_MESSAGE,
    OtpServer,
)
from openfollow.packet_chunking import MAX_DATAGRAM_BYTES
from openfollow.psn.marker import Marker
from openfollow.psn.server import PsnServer
from openfollow.rttrpm.server import RttrpmServer

FRAME_HZ = 60.0
STATUS_INTERVAL_S = 5.0

# Where each stream starts needing more than one datagram, at the operator
# label lengths this driver generates.
SPLIT_THRESHOLDS = {
    "PSN data": 14,
    "PSN info": 85,
    "OTP transform": 32,
    "OTP advertisement": 36,
    "RTTrPM": 34,
}


def _classify(data: bytes) -> str | None:
    """Name the stream a datagram belongs to, from its own header."""
    if data.startswith(OTP_PACKET_IDENTIFIER):
        vector = struct.unpack("!H", data[12:14])[0]
        if vector == VECTOR_OTP_TRANSFORM_MESSAGE:
            return "OTP transform"
        if vector == VECTOR_OTP_ADVERTISEMENT_MESSAGE:
            # Only the name advertisement grows with the marker count.
            inner = struct.unpack("!H", data[79:81])[0]
            return "OTP advertisement" if inner == VECTOR_OTP_ADVERTISEMENT_NAME else None
        return None
    if len(data) >= 2:
        chunk = struct.unpack("<H", data[:2])[0]
        if chunk == pypsn.PsnV2Chunck.PSN_DATA_PACKET:
            return "PSN data"
        if chunk == pypsn.PsnV2Chunck.PSN_INFO_PACKET:
            return "PSN info"
    return "RTTrPM"


class Wire:
    """Per-stream datagram tally, written from the servers' send threads."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.streams: dict[str, dict[str, int]] = {}

    def record(self, data: bytes) -> None:
        stream = _classify(data)
        if stream is None:
            return
        with self.lock:
            row = self.streams.setdefault(stream, {"datagrams": 0, "octets": 0, "largest": 0})
            row["datagrams"] += 1
            row["octets"] += len(data)
            row["largest"] = max(row["largest"], len(data))

    def snapshot(self) -> dict[str, dict[str, int]]:
        with self.lock:
            return {name: dict(row) for name, row in self.streams.items()}


def _count_sends(server: object, wire: Wire, dest_arg: bool) -> None:
    """Tally every datagram a server hands its socket, then send it as usual."""
    original = server._send  # type: ignore[attr-defined]

    if dest_arg:

        def wrapped(data: bytes, dest_ip: str, stop_event: object = None) -> None:
            wire.record(data)
            original(data, dest_ip, stop_event)
    else:

        def wrapped(data: bytes, stop_event: object = None) -> None:  # type: ignore[misc]
            wire.record(data)
            original(data, stop_event)

    server._send = wrapped  # type: ignore[attr-defined]


def _positions(count: int, elapsed: float, radius: float, period_s: float) -> list[tuple[float, float, float]]:
    """Markers evenly spaced around one slow orbit, at staggered heights."""
    out = []
    for index in range(count):
        phase = index / count
        angle = (elapsed / period_s + phase) * 2.0 * math.pi
        height = 1.2 + 0.8 * math.sin((elapsed / 7.0 + phase) * 2.0 * math.pi)
        out.append((radius * math.cos(angle), radius * math.sin(angle), height))
    return out


def _report(wire: Wire, markers: int, elapsed: float) -> bool:
    """Print the per-stream tally; return True if anything exceeded the MTU."""
    over = False
    for stream, row in sorted(wire.snapshot().items()):
        threshold = SPLIT_THRESHOLDS.get(stream, 0)
        state = "split" if markers >= threshold else "single"
        rate = row["datagrams"] / elapsed if elapsed > 0 else 0.0
        flag = ""
        if row["largest"] > MAX_DATAGRAM_BYTES:
            over = True
            flag = "  <- OVER MTU"
        print(
            f"    {stream:<18} {state:>6}  largest={row['largest']:>5} B  "
            f"{row['datagrams']:>7} dgram  {rate:6.1f}/s{flag}",
            flush=True,
        )
    return over


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--markers", type=int, default=40, help="marker count (13 = nothing splits, 40 = all split)")
    parser.add_argument("--duration", type=float, default=0.0, help="seconds to run; 0 = until Ctrl-C")
    parser.add_argument("--iface-ip", default="", help="source interface IPv4 to bind the multicast senders to")
    parser.add_argument("--psn-mcast", default="236.10.10.10", help="PSN multicast group")
    parser.add_argument("--otp-system", type=int, default=1, help="OTP System Number (transform group 239.159.1.N)")
    parser.add_argument("--rttrpm-host", default="", help="RTTrPM unicast target; omitted = RTTrPM not started")
    parser.add_argument("--rttrpm-port", type=int, default=36700, help="RTTrPM target port")
    parser.add_argument("--radius", type=float, default=3.0, help="orbit radius in metres")
    parser.add_argument("--period", type=float, default=12.0, help="seconds per orbit")
    parser.add_argument("--no-psn", action="store_true", help="do not start the PSN server")
    parser.add_argument("--no-otp", action="store_true", help="do not start the OTP server")
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    if args.markers < 1:
        parser.error("--markers must be at least 1")

    wire = Wire()
    servers: list[object] = []
    # OTP and RTTrPM read shared Marker objects; PSN owns its own registrations.
    shared = [Marker(index, f"Followspot Operator Position {index:02d}") for index in range(1, args.markers + 1)]
    for marker in shared:
        marker.set_pos(0.0, 0.0, 1.2)

    psn = None
    if not args.no_psn:
        psn = PsnServer(mcast_ip=args.psn_mcast, source_ip=args.iface_ip)
        for marker in shared:
            psn.add_marker(marker.marker_id, marker.name)
        _count_sends(psn, wire, dest_arg=False)
        servers.append(psn)
    if not args.no_otp:
        otp = OtpServer(system_number=args.otp_system, source_ip=args.iface_ip)
        for marker in shared:
            otp.register_marker(marker)
        _count_sends(otp, wire, dest_arg=True)
        servers.append(otp)
    if args.rttrpm_host:
        rttrpm = RttrpmServer(host=args.rttrpm_host, port=args.rttrpm_port)
        for marker in shared:
            rttrpm.register_marker(marker)
        _count_sends(rttrpm, wire, dest_arg=False)
        servers.append(rttrpm)

    if not servers:
        parser.error("every output disabled; nothing to drive")

    print(f"Driving {args.markers} markers at {FRAME_HZ:.0f} Hz.\n", flush=True)
    print("  stream             at this marker count", flush=True)
    for stream, threshold in SPLIT_THRESHOLDS.items():
        if stream == "RTTrPM" and not args.rttrpm_host:
            continue
        if stream.startswith("PSN") and args.no_psn:
            continue
        if stream.startswith("OTP") and args.no_otp:
            continue
        note = "SPLITS across datagrams" if args.markers >= threshold else f"one datagram (splits at {threshold})"
        print(f"  {stream:<18} {note}", flush=True)
    print(flush=True)
    if psn is not None:
        print(f"  PSN    -> {args.psn_mcast}:56565 multicast", flush=True)
    if not args.no_otp:
        print(
            f"  OTP    -> 239.159.1.{args.otp_system}:5568 transform, 239.159.2.1:5568 advertisement",
            flush=True,
        )
    if args.rttrpm_host:
        print(f"  RTTrPM -> {args.rttrpm_host}:{args.rttrpm_port} unicast", flush=True)
    print("\nCtrl-C to stop.\n", flush=True)

    for server in servers:
        server.start()  # type: ignore[attr-defined]

    stopping = threading.Event()

    def _stop(signum: int, frame: FrameType | None) -> None:
        stopping.set()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    start = time.monotonic()
    next_status = start + STATUS_INTERVAL_S
    speed = 2.0 * math.pi * args.radius / args.period
    try:
        while not stopping.is_set():
            now = time.monotonic()
            elapsed = now - start
            if args.duration and elapsed >= args.duration:
                break
            # Rewrite every marker every frame: one the loop stops writing ages
            # out and drops off the wire on all three protocols.
            for marker, position in zip(
                shared, _positions(args.markers, elapsed, args.radius, args.period), strict=True
            ):
                marker.set_pos(*position)
                marker.set_speed(speed, 0.0, 0.0)
                if psn is not None:
                    live = psn.get_marker(marker.marker_id)
                    if live is not None:
                        live.set_pos(*position)
                        live.set_speed(speed, 0.0, 0.0)
            if now >= next_status:
                next_status = now + STATUS_INTERVAL_S
                print(f"[{elapsed:6.1f}s]", flush=True)
                _report(wire, args.markers, elapsed)
                print(flush=True)
            stopping.wait(1.0 / FRAME_HZ)
    finally:
        for server in servers:
            server.stop()  # type: ignore[attr-defined]

    elapsed = time.monotonic() - start
    print(f"\nStopped after {elapsed:.1f}s.", flush=True)
    over = _report(wire, args.markers, elapsed)
    if over:
        print(f"\nFAIL: a stream exceeded {MAX_DATAGRAM_BYTES} B.", flush=True)
        return 1
    print(f"\nPASS: every stream stayed inside {MAX_DATAGRAM_BYTES} B.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
