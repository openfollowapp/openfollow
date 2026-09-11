# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""PSN timestamps and tracker validity, from the clock to the wire.

The spec puts the packet header's timestamp in microseconds since the server
started, and the reference implementation stamps trackers off the same clock, so
these are checked together: a per-tracker timestamp is only meaningful if a
receiver can compare it against the header it arrived in.
"""

from __future__ import annotations

import time

import pypsn
import pytest
from conftest import PsnStepClock as _StepClock

from openfollow.psn import clock as clock_module
from openfollow.psn.marker import Marker
from openfollow.psn.server import PsnServer

pytestmark = pytest.mark.unit


def _decode(packet: pypsn.PsnDataPacket) -> pypsn.PsnDataPacket:
    """Round-trip a packet through the real encoder and parser."""
    parsed = pypsn.parse_psn_packet(pypsn.prepare_psn_data_packet_bytes(packet))
    assert isinstance(parsed, pypsn.PsnDataPacket)
    return parsed


class TestMonotonicClock:
    """The clock is pinned by driving ``time.monotonic`` - the function the
    implementation actually reads - so a wall-clock implementation fails these
    rather than passing them by coincidence.

    The epoch is pinned too, and elapsed times are binary fractions: reading the
    real epoch would leave the assertion at the mercy of how a host's uptime
    rounds, and ``(epoch + 0.02) - epoch`` truncates to 19_999 us on some.
    """

    @pytest.fixture(autouse=True)
    def _pinned_epoch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(clock_module, "_EPOCH", 1_000.0)

    @pytest.mark.parametrize(
        ("elapsed_s", "expected_usec"),
        [(0.25, 250_000), (1.5, 1_500_000), (0.0, 0)],
    )
    def test_reports_microseconds_elapsed(
        self, monkeypatch: pytest.MonkeyPatch, elapsed_s: float, expected_usec: int
    ) -> None:
        monkeypatch.setattr(time, "monotonic", lambda: 1_000.0 + elapsed_s)
        assert clock_module.psn_timestamp_usec() == expected_usec

    def test_starts_at_zero_not_at_the_unix_epoch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The spec counts from server start. Wall-clock microseconds would be
        ~1.7e15 here, which is what the pre-fix header sent (in milliseconds)."""
        monkeypatch.setattr(time, "monotonic", lambda: 1_000.0)
        assert clock_module.psn_timestamp_usec() == 0

    @pytest.mark.parametrize("stepped_to", [10_000_000.0, -5_000.0])
    def test_does_not_move_when_the_wall_clock_is_stepped(
        self, monkeypatch: pytest.MonkeyPatch, stepped_to: float
    ) -> None:
        """The Pis have no RTC and sync time shortly after boot, stepping the
        wall clock in either direction mid-stream. Holding ``time.monotonic``
        still means a correct implementation cannot move at all."""
        monkeypatch.setattr(time, "monotonic", lambda: 1_001.0)
        monkeypatch.setattr(time, "time", lambda: 0.0)
        before = clock_module.psn_timestamp_usec()
        monkeypatch.setattr(time, "time", lambda: stepped_to)
        assert clock_module.psn_timestamp_usec() == before == 1_000_000


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
    """Read off the wire rather than off the header builder: what a receiver
    compares is what arrives, and the builder is free to be restructured."""

    @staticmethod
    def _sent_data_packets(server: PsnServer, monkeypatch: pytest.MonkeyPatch) -> list[bytes]:
        sent: list[bytes] = []
        monkeypatch.setattr(server, "_send", lambda data, stop_event=None: sent.append(data))
        return sent

    def _one_packet(self, server: PsnServer, monkeypatch: pytest.MonkeyPatch) -> pypsn.PsnDataPacket:
        sent = self._sent_data_packets(server, monkeypatch)
        server._send_data_packet()
        packet = pypsn.parse_psn_packet(sent[0])
        assert isinstance(packet, pypsn.PsnDataPacket)
        return packet

    def test_header_reads_the_injected_clock(self, monkeypatch: pytest.MonkeyPatch) -> None:
        clock = _StepClock(555_000)
        server = PsnServer(clock=clock, mcast_ip=None)
        server.add_marker(301, "Marker 301").set_pos(1.0, 2.0, 3.0)
        assert self._one_packet(server, monkeypatch).info.timestamp == 555_000

    def test_header_and_tracker_stamps_share_one_time_base(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A receiver compares the two to judge tracker freshness, so they must
        come from the same clock - not one wall and one monotonic."""
        clock = _StepClock(1_000)
        server = PsnServer(clock=clock, mcast_ip=None)
        server.add_marker(301, "Marker 301").set_pos(1.0, 2.0, 3.0)
        clock.now = 4_000
        packet = self._one_packet(server, monkeypatch)
        assert packet.trackers[0].timestamp == 1_000
        assert packet.info.timestamp == 4_000

    def test_a_write_during_the_snapshot_cannot_outrun_the_header(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The header and the trackers are read at different instants while the
        GTK thread writes markers at 60-120 Hz. A tracker timestamp ahead of the
        header it ships in underflows a receiver computing age as unsigned, so
        the trackers must be snapshotted first."""
        clock = _StepClock(1_000)
        server = PsnServer(clock=clock, mcast_ip=None)
        marker = server.add_marker(301, "Marker 301")
        marker.set_pos(1.0, 2.0, 3.0)

        sent = self._sent_data_packets(server, monkeypatch)
        real_next_id = server._next_data_frame_id

        def _take_id_then_write() -> int:
            frame_id = real_next_id()
            clock.now += 5_000
            marker.set_pos(9.0, 9.0, 9.0)  # a write lands between the two reads
            return frame_id

        monkeypatch.setattr(server, "_next_data_frame_id", _take_id_then_write)
        server._send_data_packet()

        packet = pypsn.parse_psn_packet(sent[0])
        assert isinstance(packet, pypsn.PsnDataPacket)
        assert packet.trackers[0].timestamp <= packet.info.timestamp

    def test_header_advances_between_packets(self, monkeypatch: pytest.MonkeyPatch) -> None:
        clock = _StepClock(1_000)
        server = PsnServer(clock=clock, mcast_ip=None)
        server.add_marker(301, "Marker 301").set_pos(1.0, 2.0, 3.0)
        sent = self._sent_data_packets(server, monkeypatch)
        server._send_data_packet()
        clock.now = 17_667
        server._send_data_packet()
        stamps = [pypsn.parse_psn_packet(datagram).info.timestamp for datagram in sent]
        assert stamps[1] > stamps[0]
