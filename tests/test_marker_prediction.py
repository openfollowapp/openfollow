# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Paul Hermann and Michel Honold
"""Output prediction: the lead itself, and every output sending it while the marker stays real."""

from __future__ import annotations

import math

import pytest
from conftest import PsnStepClock as _StepClock

from openfollow.otp.server import encode_otp_transform_packet
from openfollow.psn.marker import Marker, is_marker_stale
from openfollow.rttrpm.server import RttrpmServer
from openfollow.runtime.marker_prediction import (
    PREDICTION_JUMP_HOLD_S,
    PREDICTION_MAX_SPEED_MPS,
    prediction_lead,
)
from openfollow.runtime.marker_velocity import MarkerVelocityState, estimate_marker_velocity

pytestmark = pytest.mark.unit

_NOW_US = 4_000_000_000


def _state(vx: float, vy: float, vz: float = 0.0, *, since_jump_s: float = math.inf) -> MarkerVelocityState:
    return MarkerVelocityState(velocity=(vx, vy, vz), since_jump_s=since_jump_s)


class TestPredictionLead:
    @pytest.mark.parametrize("horizon_ms", [0, -5])
    def test_no_horizon_is_no_lead(self, horizon_ms: int) -> None:
        assert prediction_lead(_state(1.0, 2.0), horizon_ms) == (0.0, 0.0)

    def test_a_still_marker_has_no_lead(self) -> None:
        assert prediction_lead(_state(0.0, 0.0), 1000) == (0.0, 0.0)

    def test_the_lead_is_velocity_times_horizon(self) -> None:
        assert prediction_lead(_state(1.5, -0.5), 500) == pytest.approx((0.75, -0.25))

    @pytest.mark.parametrize("horizon_ms", [100, 400, 1000])
    def test_the_lead_scales_linearly_with_the_horizon(self, horizon_ms: int) -> None:
        assert prediction_lead(_state(2.0, 0.0), horizon_ms) == pytest.approx((2.0 * horizon_ms / 1000.0, 0.0))

    def test_z_velocity_never_leads(self) -> None:
        assert prediction_lead(_state(0.0, 0.0, 3.0), 1000) == (0.0, 0.0)

    def test_a_speed_above_the_ceiling_is_capped_and_keeps_its_direction(self) -> None:
        dx, dy = prediction_lead(_state(12.0, 16.0), 1000)
        assert math.hypot(dx, dy) == pytest.approx(PREDICTION_MAX_SPEED_MPS)
        assert dy / dx == pytest.approx(16.0 / 12.0)

    def test_the_ceiling_ignores_z(self) -> None:
        """A marker rising fast does not shrink the X/Y lead."""
        assert prediction_lead(_state(1.0, 0.0, 19.0), 1000) == pytest.approx((1.0, 0.0))

    @pytest.mark.parametrize("since_jump_s", [0.0, PREDICTION_JUMP_HOLD_S / 2, PREDICTION_JUMP_HOLD_S - 1e-6])
    def test_no_lead_while_a_repositioning_settles(self, since_jump_s: float) -> None:
        assert prediction_lead(_state(4.0, 0.0, since_jump_s=since_jump_s), 1000) == (0.0, 0.0)

    def test_the_lead_returns_once_the_hold_is_over(self) -> None:
        assert prediction_lead(_state(1.0, 0.0, since_jump_s=PREDICTION_JUMP_HOLD_S), 1000) == pytest.approx((1.0, 0.0))


class TestMarkerLead:
    def test_the_lead_rides_on_what_is_sent_never_on_pos(self) -> None:
        marker = Marker(1, "T1")
        marker.set_pos(1.0, 2.0, 1.5)
        marker.set_lift(0.25)
        marker.set_lead(0.5, -0.5)
        assert marker.pos == (1.0, 2.0, 1.5)
        assert marker.lifted_pos == (1.0, 2.0, 1.75)
        assert marker.sent_pos == (1.5, 1.5, 1.75)
        assert marker.lead == (0.5, -0.5)

    def test_psn_sends_the_led_position(self) -> None:
        marker = Marker(1, "T1")
        marker.set_pos(1.0, 2.0, 1.5)
        marker.set_lift(0.25)
        marker.set_lead(0.5, -0.5)
        tracker = marker.to_psn_marker()
        assert (tracker.pos.x, tracker.pos.y, tracker.pos.z) == (1.5, 1.5, 1.75)

    def test_psn_sends_the_real_speed(self) -> None:
        marker = Marker(1, "T1")
        marker.set_speed(1.0, 0.0, 0.0)
        marker.set_lead(0.8, 0.0)
        tracker = marker.to_psn_marker()
        assert (tracker.speed.x, tracker.speed.y, tracker.speed.z) == (1.0, 0.0, 0.0)

    def test_setting_the_lead_is_not_a_data_write(self) -> None:
        clock = _StepClock(_NOW_US - 5_000_000)
        marker = Marker(1, "T1", clock=clock)
        marker.set_pos(1.0, 2.0, 0.0)
        stamp = marker.timestamp
        clock.now = _NOW_US
        marker.set_lead(0.3, 0.3)
        assert marker.timestamp == stamp
        assert is_marker_stale(marker)

    def test_an_untouched_marker_stays_invalid_when_led(self) -> None:
        marker = Marker(1, "T1")
        marker.set_lead(0.3, 0.3)
        assert marker.status == 0.0


def _frozen(marker_id: int, pos: tuple[float, float, float], *, age_s: float = 0.0) -> Marker:
    clock = _StepClock(_NOW_US - int(age_s * 1_000_000))
    marker = Marker(marker_id, f"T{marker_id}", clock=clock)
    marker.set_pos(*pos)
    clock.now = _NOW_US
    return marker


class TestEveryOutputSendsTheLead:
    def _otp(self, marker: Marker) -> bytes:
        return encode_otp_transform_packet(
            cid=b"\x11" * 16,
            component_name="X",
            folio=1,
            system_number=1,
            timestamp_us=_NOW_US,
            markers=[marker],
            priority=100,
        )

    def _rttrpm(self, monkeypatch: pytest.MonkeyPatch, marker: Marker) -> list[bytes]:
        server = RttrpmServer(host="127.0.0.1", port=24_002)
        server.register_marker(marker)
        sent: list[bytes] = []
        monkeypatch.setattr(server, "_socket", object())
        monkeypatch.setattr(server, "_send", lambda data, stop_event=None: sent.append(data))
        monkeypatch.setattr(server, "_next_pkt_id", lambda: 1)
        server._send_packet()
        return sent

    def test_otp_sends_the_led_point(self) -> None:
        led = _frozen(1, (1.0, 2.0, 0.5))
        led.set_lead(0.4, -0.2)
        assert self._otp(led) == self._otp(_frozen(1, (1.4, 1.8, 0.5)))

    def test_rttrpm_sends_the_led_centroid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        led = _frozen(1, (1.0, 2.0, 0.5))
        led.set_lead(0.4, -0.2)
        sent = self._rttrpm(monkeypatch, led)
        assert sent and sent == self._rttrpm(monkeypatch, _frozen(1, (1.4, 1.8, 0.5)))

    def test_a_frozen_led_marker_is_still_withheld_by_rttrpm(self, monkeypatch: pytest.MonkeyPatch) -> None:
        frozen = _frozen(1, (1.0, 2.0, 0.5), age_s=5.0)
        frozen.set_lead(0.4, 0.0)
        assert self._rttrpm(monkeypatch, frozen) == []

    def test_a_frozen_led_marker_ships_an_invalid_psn_tracker(self) -> None:
        frozen = _frozen(1, (1.0, 2.0, 0.5), age_s=5.0)
        frozen.set_lead(0.4, 0.0)
        assert frozen.to_psn_marker(stale=is_marker_stale(frozen)).status == 0.0


class TestJumpDetection:
    """The velocity estimate marks a repositioning so the lead can sit it out."""

    def test_a_jump_resets_the_clock_and_motion_advances_it(self) -> None:
        state = MarkerVelocityState()
        estimate_marker_velocity(state, (0.0, 0.0, 0.0), 1 / 60)
        assert state.since_jump_s == math.inf
        estimate_marker_velocity(state, (10.0, 0.0, 0.0), 1 / 60)
        assert state.since_jump_s == 0.0
        estimate_marker_velocity(state, (10.02, 0.0, 0.0), 1 / 60)
        assert state.since_jump_s == pytest.approx(1 / 60)

    @pytest.mark.parametrize("snap_m", [0.2, 0.3, 1.0])
    def test_a_short_snap_at_rest_is_a_jump(self, snap_m: float) -> None:
        state = MarkerVelocityState()
        for _ in range(30):
            estimate_marker_velocity(state, (0.0, 0.0, 0.0), 1 / 60)
        estimate_marker_velocity(state, (snap_m, 0.0, 0.0), 1 / 60)
        assert state.since_jump_s == 0.0

    def test_a_z_step_is_not_a_jump(self) -> None:
        """Z is never led, so an instant lift must not switch the X/Y lead off."""
        state = MarkerVelocityState()
        for frame in range(60):
            estimate_marker_velocity(state, (1.5 * frame / 60, 0.0, 0.0), 1 / 60)
        estimate_marker_velocity(state, (1.5, 0.0, 2.0), 1 / 60)
        assert state.since_jump_s == math.inf

    @pytest.mark.parametrize("writer_hz", [10, 30])
    def test_a_slow_writer_walking_is_not_a_jump(self, writer_hz: int) -> None:
        state = MarkerVelocityState()
        for frame in range(120):
            x = 1.5 * math.floor(frame / 60 * writer_hz + 1e-9) / writer_hz
            estimate_marker_velocity(state, (x, 0.0, 0.0), 1 / 60)
        assert state.since_jump_s == math.inf

    def test_a_fast_walk_is_not_a_jump(self) -> None:
        state = MarkerVelocityState()
        for frame in range(60):
            estimate_marker_velocity(state, (3.0 * frame / 60, 0.0, 0.0), 1 / 60)
        assert state.since_jump_s == math.inf
