# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""#85: PSN timestamps and tracker validity, from the clock to the wire.

The spec puts the packet header's timestamp in microseconds since the server
started, and the reference implementation stamps trackers off the same clock, so
these are checked together: a per-tracker timestamp is only meaningful if a
receiver can compare it against the header it arrived in.
"""

from __future__ import annotations

import time

import pypsn
import pytest

from openfollow.psn import clock as clock_module
from openfollow.psn.marker import Marker
from openfollow.psn.server import PsnServer

pytestmark = pytest.mark.unit


class _StepClock:
    def __init__(self, now: int = 0) -> None:
        self.now = now

    def __call__(self) -> int:
        return self.now


def _decode(packet: pypsn.PsnDataPacket) -> pypsn.PsnDataPacket:
    """Round-trip a packet through the real encoder and parser."""
    parsed = pypsn.parse_psn_packet(pypsn.prepare_psn_data_packet_bytes(packet))
    assert isinstance(parsed, pypsn.PsnDataPacket)
    return parsed


class TestMonotonicClock:
    def test_reports_microseconds(self) -> None:
        before = clock_module.psn_timestamp_usec()
        time.sleep(0.02)
        elapsed = clock_module.psn_timestamp_usec() - before
        # 20 ms is 20_000 us; a wide upper bound keeps this off the flake list.
        assert 10_000 < elapsed < 2_000_000

    def test_starts_near_zero_not_at_the_unix_epoch(self) -> None:
        """The spec counts from server start. Wall-clock microseconds would be
        ~1.7e15 here, which is what the pre-fix header sent (in milliseconds)."""
        assert clock_module.psn_timestamp_usec() < 60 * 60 * 1_000_000

    def test_does_not_move_when_the_wall_clock_is_stepped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The Pis have no RTC and sync time shortly after boot. A wall-clock
        timestamp would jump backwards mid-stream; a monotonic one cannot."""
        monkeypatch.setattr(time, "time", lambda: 0.0)
        stepped = clock_module.psn_timestamp_usec()
        monkeypatch.setattr(time, "time", lambda: 10_000_000.0)
        assert clock_module.psn_timestamp_usec() >= stepped


class TestTrackerFieldsReachTheWire:
    """The chunks were always emitted; they just always read zero."""

    def test_timestamp_and_status_survive_encode_and_parse(self) -> None:
        marker = Marker(301, "Marker 301", clock=_StepClock(123_456_789))
        marker.set_pos(1.474, 2.672, 1.6)
        packet = pypsn.PsnDataPacket(
            info=pypsn.PsnInfo(
                timestamp=123_456_789,
                version_high=2,
                version_low=0,
                frame_id=0,
                packet_count=1,
            ),
            trackers=[marker.to_psn_marker()],
        )
        tracker = _decode(packet).trackers[0]
        assert tracker.tracker_id == 301
        assert tracker.timestamp == 123_456_789
        assert tracker.status == pytest.approx(1.0)

    def test_a_never_updated_marker_stays_invalid_on_the_wire(self) -> None:
        """Registered but never written: honestly reported as invalid rather
        than claiming validity it does not have."""
        packet = pypsn.PsnDataPacket(
            info=pypsn.PsnInfo(timestamp=1, version_high=2, version_low=0, frame_id=0, packet_count=1),
            trackers=[Marker(7, "T7").to_psn_marker()],
        )
        tracker = _decode(packet).trackers[0]
        assert tracker.timestamp == 0
        assert tracker.status == pytest.approx(0.0)


class TestHeaderSharesTheTrackerClock:
    def test_header_reads_the_injected_clock(self) -> None:
        clock = _StepClock(555_000)
        server = PsnServer(clock=clock)
        assert server._make_psn_info().timestamp == 555_000

    def test_header_and_tracker_stamps_share_one_time_base(self) -> None:
        """A receiver compares the two to judge tracker freshness, so they must
        come from the same clock - not one wall and one monotonic."""
        clock = _StepClock(1_000)
        server = PsnServer(clock=clock)
        marker = server.add_marker(301, "Marker 301")
        marker.set_pos(1.0, 2.0, 3.0)
        clock.now = 4_000
        info = server._make_psn_info()
        assert marker.to_psn_marker().timestamp == 1_000
        assert info.timestamp == 4_000
        assert info.timestamp - marker.timestamp == 3_000

    def test_header_advances_between_packets(self) -> None:
        server = PsnServer()
        first = server._make_psn_info().timestamp
        time.sleep(0.01)
        assert server._make_psn_info().timestamp > first
