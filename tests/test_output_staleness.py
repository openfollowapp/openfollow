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


def _age(monkeypatch: pytest.MonkeyPatch, marker: Marker, seconds: float) -> None:
    """Make *marker* read as written ``seconds`` ago.

    ``Marker`` binds its stamping clock at construction, so moving the process
    clock ages the marker without disturbing the stamp already on it. Both
    readers of that clock are moved together: the OTP encoder samples it once
    per packet so all its points share one instant.
    """
    frozen = marker.timestamp + int(seconds * 1_000_000)
    monkeypatch.setattr(marker_module, "psn_timestamp_usec", lambda: frozen)
    monkeypatch.setattr(otp_server, "psn_timestamp_usec", lambda: frozen)


# --------------------------------------------------------------------------- #
# PSN - tracker validity
# --------------------------------------------------------------------------- #


class TestPsnStatus:
    def _emit(self, monkeypatch: pytest.MonkeyPatch, age_s: float) -> pypsn.PsnTracker:
        server = PsnServer(mcast_ip=None)
        marker = server.add_marker(1, "T1")
        marker.set_pos(4.0, 5.0, 6.0)
        sent: list[bytes] = []
        monkeypatch.setattr(server, "_send", lambda data, stop_event=None: sent.append(data))
        _age(monkeypatch, marker, age_s)
        server._send_data_packet()
        packet = pypsn.parse_psn_packet(sent[0])
        return packet.trackers[0]

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

    def _marker(self) -> Marker:
        marker = Marker(1, "T1")
        marker.set_pos(1.0, 2.0, 3.0)
        return marker

    def test_a_just_written_point_samples_at_the_packet_time(self, monkeypatch: pytest.MonkeyPatch) -> None:
        marker = self._marker()
        _age(monkeypatch, marker, 0.0)
        transform_ts, sampled_ts = self._encode(marker)
        assert sampled_ts == transform_ts

    @pytest.mark.parametrize("age_s", [0.25, 2.0, 30.0])
    def test_the_sampled_timestamp_trails_the_packet_by_the_marker_age(
        self,
        monkeypatch: pytest.MonkeyPatch,
        age_s: float,
    ) -> None:
        marker = self._marker()
        _age(monkeypatch, marker, age_s)
        transform_ts, sampled_ts = self._encode(marker)
        assert transform_ts - sampled_ts == int(age_s * 1_000_000)

    def test_a_frozen_point_stops_ageing_while_the_packet_clock_runs_on(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The whole point of the field: two packets a second apart from a
        wedged station carry the same sampled timestamp, so a Consumer can see
        the point is not being re-read even though transforms keep arriving."""
        marker = self._marker()
        _age(monkeypatch, marker, 4.0)
        _, first = self._encode(marker, timestamp_us=10_000_000)
        _age(monkeypatch, marker, 5.0)
        _, second = self._encode(marker, timestamp_us=11_000_000)
        assert first == second

    def test_an_unaged_marker_clamps_instead_of_wrapping_the_unsigned_field(self) -> None:
        # A never-written marker has an unknowable age; the field is unsigned,
        # so underflowing it would ship a timestamp far in the future.
        _, sampled_ts = self._encode(Marker(1, "T1"))
        assert sampled_ts == 0

    def test_an_explicit_sampled_timestamp_overrides_the_derivation(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        marker = self._marker()
        _age(monkeypatch, marker, 9.0)
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


_NOW_US = 100_000_000


class TestRttrpmWithholding:
    def _emit(self, monkeypatch: pytest.MonkeyPatch, ages_s: list[float]) -> list[bytes]:
        """Register one trackable per entry in *ages_s* and emit one packet."""
        monkeypatch.setattr(marker_module, "psn_timestamp_usec", lambda: _NOW_US)
        server = RttrpmServer(host="127.0.0.1", port=24_002)
        for index, age_s in enumerate(ages_s, start=1):
            stamp = _StepClock(_NOW_US - int(age_s * 1_000_000))
            marker = Marker(index, f"T{index}", clock=stamp)
            marker.set_pos(float(index), 0.0, 0.0)
            server.register_marker(marker)
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
