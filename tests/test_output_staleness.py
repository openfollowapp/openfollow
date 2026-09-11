# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""What each output protocol puts on the wire once the frame loop stops writing.

PSN, OTP, RTTrPM, and the OSC transmitter each run their own send thread, so a
frozen frame loop leaves all four broadcasting the last known position at full
rate - a stream that looks healthy to a console while its coordinates never
move. They share one freshness rule (``openfollow.psn.marker.is_marker_stale``)
and map it onto whatever their protocol offers: PSN has a validity field, OTP
has a per-point sampled timestamp, RTTrPM has only absence.

Everything here reads the emitted bytes rather than the encoder's arguments,
because the bytes are what a receiver sees. The OSC transmitter's own idiom
lives with its harness in ``test_osc_transmitter.py``.
"""

from __future__ import annotations

import logging
import struct

import pypsn
import pytest
from conftest import PsnStepClock as _StepClock

import openfollow.osc.transmitter as osc_transmitter
import openfollow.otp.server as otp_server
import openfollow.psn.server as psn_server
import openfollow.rttrpm.server as rttrpm_server
from openfollow.otp.server import encode_otp_transform_packet
from openfollow.psn import marker as marker_module
from openfollow.psn.marker import Marker
from openfollow.psn.server import PsnServer
from openfollow.rttrpm.server import RttrpmServer

pytestmark = pytest.mark.unit

# Transform Message layout, verified against the offsets in ``test_otp.py``.
_OTP_TRANSFORM_TS = slice(84, 92)
_OTP_FIRST_POINT_SAMPLED_TS = slice(108, 116)


_NOW_US = 4_000_000_000


def _aged_marker(marker_id: int, age_s: float, *, now_us: int = _NOW_US) -> Marker:
    """A locally-driven marker written ``age_s`` ago, on its own clock.

    A marker's age is measured against the clock it was built with, so the test
    drives that clock rather than patching a module function.
    """
    clock = _StepClock(now_us - int(age_s * 1_000_000))
    marker = Marker(marker_id, f"T{marker_id}", clock=clock)
    marker.set_pos(float(marker_id), 5.0, 6.0)
    clock.now = now_us  # time moves on; the stamp does not
    return marker


# --------------------------------------------------------------------------- #
# PSN - tracker validity
# --------------------------------------------------------------------------- #


class TestPsnStatus:
    def _emit(self, monkeypatch: pytest.MonkeyPatch, age_s: float) -> pypsn.PsnTracker:
        clock = _StepClock(_NOW_US - int(age_s * 1_000_000))
        server = PsnServer(mcast_ip=None, clock=clock)
        marker = server.add_marker(1, "T1")
        marker.set_pos(4.0, 5.0, 6.0)
        clock.now = _NOW_US
        sent: list[bytes] = []
        monkeypatch.setattr(server, "_send", lambda data, stop_event=None: sent.append(data))
        server._send_data_packet()
        return pypsn.parse_psn_packet(sent[0]).trackers[0]

    def test_a_live_marker_ships_a_valid_tracker(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert self._emit(monkeypatch, 0.0).status == 1.0

    def test_a_frozen_marker_ships_an_invalid_tracker(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert self._emit(monkeypatch, 5.0).status == 0.0

    def test_the_position_still_ships_while_stale(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Withholding the tracker would read as a disconnect. The console keeps
        its last position; the validity field is what says not to trust it."""
        tracker = self._emit(monkeypatch, 5.0)
        assert (tracker.pos.x, tracker.pos.y, tracker.pos.z) == (4.0, 5.0, 6.0)


# --------------------------------------------------------------------------- #
# OTP - per-point sampled timestamp (E1.59 Section 9.6)
# --------------------------------------------------------------------------- #


class TestOtpSampledTimestamp:
    def _encode(self, marker: Marker, *, timestamp_us: int = 3_600_000_000) -> tuple[int, int]:
        packet = encode_otp_transform_packet(
            cid=b"\x11" * 16,
            component_name="X",
            folio=1,
            system_number=1,
            timestamp_us=timestamp_us,
            markers=[marker],
            priority=100,
        )
        return (
            struct.unpack("!Q", packet[_OTP_TRANSFORM_TS])[0],
            struct.unpack("!Q", packet[_OTP_FIRST_POINT_SAMPLED_TS])[0],
        )

    def test_a_just_written_point_samples_at_the_packet_time(self) -> None:
        transform_ts, sampled_ts = self._encode(_aged_marker(1, 0.0))
        assert sampled_ts == transform_ts

    @pytest.mark.parametrize("age_s", [0.25, 2.0, 30.0])
    def test_the_sampled_timestamp_trails_the_packet_by_the_marker_age(self, age_s: float) -> None:
        transform_ts, sampled_ts = self._encode(_aged_marker(1, age_s))
        assert transform_ts - sampled_ts == int(age_s * 1_000_000)

    def test_a_frozen_point_stops_ageing_while_the_packet_clock_runs_on(self) -> None:
        """The whole point of the field: two packets a second apart from a
        wedged station carry the same sampled timestamp, so a Consumer can see
        the point is not being re-read even though transforms keep arriving."""
        clock = _StepClock(_NOW_US - 4_000_000)
        marker = Marker(1, "T1", clock=clock)
        marker.set_pos(1.0, 2.0, 3.0)
        clock.now = _NOW_US
        _, first = self._encode(marker, timestamp_us=10_000_000)
        clock.now = _NOW_US + 1_000_000  # a second later, still unwritten
        _, second = self._encode(marker, timestamp_us=11_000_000)
        assert first == second

    def test_an_unaged_marker_clamps_instead_of_wrapping_the_unsigned_field(self) -> None:
        # A never-written marker has an unknowable age; the field is unsigned,
        # so underflowing it would ship a timestamp far in the future.
        _, sampled_ts = self._encode(Marker(1, "T1"))
        assert sampled_ts == 0

    def test_a_write_landing_mid_build_cannot_push_the_sample_past_the_packet(self) -> None:
        """A negative age (the marker written after the packet's clock read)
        would make ``transform - sampled`` underflow the unsigned field a
        Consumer reads, reporting a point ~584,000 years old."""
        clock = _StepClock(_NOW_US)
        marker = Marker(1, "T1", clock=clock)
        marker.set_pos(1.0, 2.0, 3.0)
        clock.now = _NOW_US - 500_000  # clock behind the stamp -> negative age
        transform_ts, sampled_ts = self._encode(marker)
        assert sampled_ts <= transform_ts

    def test_an_explicit_sampled_timestamp_overrides_the_derivation(self) -> None:
        marker = _aged_marker(1, 9.0)
        packet = encode_otp_transform_packet(
            cid=b"\x11" * 16,
            component_name="X",
            folio=1,
            system_number=1,
            timestamp_us=3_600_000_000,
            markers=[marker],
            priority=100,
            sampled_timestamp_us=1_234_567,
        )
        assert struct.unpack("!Q", packet[_OTP_FIRST_POINT_SAMPLED_TS])[0] == 1_234_567


# --------------------------------------------------------------------------- #
# RTTrPM - absence is the only idiom available
# --------------------------------------------------------------------------- #


class TestRttrpmWithholding:
    def _emit(self, monkeypatch: pytest.MonkeyPatch, ages_s: list[float]) -> list[bytes]:
        """Register one trackable per entry in *ages_s* and emit one packet."""
        server = RttrpmServer(host="127.0.0.1", port=24_002)
        for index, age_s in enumerate(ages_s, start=1):
            server.register_marker(_aged_marker(index, age_s))
        sent: list[bytes] = []
        monkeypatch.setattr(server, "_socket", object())
        monkeypatch.setattr(server, "_send", lambda data, stop_event=None: sent.append(data))
        server._send_packet()
        return sent

    def test_a_live_trackable_is_sent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert len(self._emit(monkeypatch, [0.0])) == 1

    def test_an_all_stale_set_sends_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Streaming a frozen centroid at 60 Hz is worse than silence: the
        receiver's own trackable timeout is the protocol's way to say gone."""
        assert self._emit(monkeypatch, [5.0]) == []

    def test_withholding_is_logged_once_per_episode(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Unlike the other three protocols this one removes the trackable
        entirely, so without a log line a marker vanishes from the console with
        no evidence anywhere. Once per episode, not once per 60 Hz packet."""
        server = RttrpmServer(host="127.0.0.1", port=24_002)
        server.register_marker(_aged_marker(1, 0.0))
        stale = _aged_marker(2, 5.0)
        server.register_marker(stale)
        monkeypatch.setattr(server, "_socket", object())
        monkeypatch.setattr(server, "_send", lambda data, stop_event=None: None)
        with caplog.at_level(logging.WARNING, logger="openfollow.rttrpm.server"):
            for _ in range(10):
                server._send_packet()
        assert len(caplog.records) == 1
        assert "withholding 1 stale trackable" in caplog.records[0].getMessage()

    def test_withhold_warning_rearms_after_recovery(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        server = RttrpmServer(host="127.0.0.1", port=24_002)
        server.register_marker(_aged_marker(1, 5.0))
        monkeypatch.setattr(server, "_socket", object())
        monkeypatch.setattr(server, "_send", lambda data, stop_event=None: None)
        with caplog.at_level(logging.WARNING, logger="openfollow.rttrpm.server"):
            server._send_packet()
            server._markers.clear()
            server.register_marker(_aged_marker(1, 0.0))  # loop writing again
            server._send_packet()
            server._markers.clear()
            server.register_marker(_aged_marker(1, 5.0))  # and stalls again
            server._send_packet()
        assert len(caplog.records) == 2

    def test_a_stale_trackable_is_dropped_without_taking_the_packet_with_it(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        sent = self._emit(monkeypatch, [0.0, 5.0])
        assert len(sent) == 1
        # numModules is the uint8 at offset 17 (see ``_warn_if_capped``).
        assert sent[0][17] == 1


# --------------------------------------------------------------------------- #
# The rule is shared, not reimplemented
# --------------------------------------------------------------------------- #


def test_every_output_reads_the_same_freshness_rule() -> None:
    """Four protocols disagreeing on when a position stops counting would be a
    quiet, per-protocol bug. Each imports the shared helper rather than rolling
    its own timestamp comparison, so the threshold can only move in one place."""
    assert psn_server.is_marker_stale is marker_module.is_marker_stale
    assert rttrpm_server.is_marker_stale is marker_module.is_marker_stale
    assert osc_transmitter.is_marker_stale is marker_module.is_marker_stale
    # OTP needs the age itself, not just the verdict, to fill in Section 9.6's
    # per-point sampled timestamp.
    assert otp_server.marker_age_s is marker_module.marker_age_s
