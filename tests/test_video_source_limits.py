# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The source limits: the one rule the Media Gallery and the live feed share,
and the meter that holds arriving frames against it without flapping."""

from __future__ import annotations

import math
import random

import pytest

from openfollow.runtime.services_marker_visuals import CONTROL_CONFLICT_ROW_MAX_CHARS
from openfollow.video import source_limits as sl
from openfollow.video.failure import SourceKind
from openfollow.video.source_limits import (
    FrameMeter,
    OverLimit,
    SourceReading,
    limits_action,
    limits_row,
    limits_sentence,
    limits_snapshot,
    over_limits,
)

pytestmark = pytest.mark.unit

RES = OverLimit.RESOLUTION
FPS = OverLimit.FRAME_RATE


# --------------------------------------------------------------------------- #
# The shared rule
# --------------------------------------------------------------------------- #


class TestOverLimits:
    @pytest.mark.parametrize(
        ("width", "height"),
        [
            (1920, 1080),
            (1280, 720),
            (1080, 1920),  # portrait Full HD
            (1080, 1080),
            (1920, 1),
            (640, 480),
        ],
    )
    def test_full_hd_or_less_in_either_orientation_is_within(self, width: int, height: int) -> None:
        assert over_limits(width, height, 30) == ()

    @pytest.mark.parametrize(
        ("width", "height"),
        [
            (3840, 2160),
            (2560, 1440),
            (2560, 1080),  # ultrawide: the longer side is over
            (1080, 2560),
            (1921, 1080),  # one past the longer side
            (1920, 1081),  # one past the shorter side
            (1081, 1920),  # portrait one past the shorter side
            (1440, 1440),  # square past the shorter side
            (1200, 1920),
        ],
    )
    def test_past_full_hd_is_over(self, width: int, height: int) -> None:
        assert over_limits(width, height, 30) == (RES,)

    @pytest.mark.parametrize("fps", [0.0, 1.0, 25.0, 29.97, 30.0, 30.5])
    def test_a_rate_up_to_the_tolerance_is_within(self, fps: float) -> None:
        assert over_limits(1920, 1080, fps) == ()

    @pytest.mark.parametrize("fps", [30.51, 31.0, 48.0, 50.0, 59.94, 60.0, 120.0, math.inf])
    def test_a_rate_past_the_tolerance_is_over(self, fps: float) -> None:
        assert over_limits(1920, 1080, fps) == (FPS,)

    def test_both_causes_are_named_resolution_first(self) -> None:
        assert over_limits(3840, 2160, 60) == (RES, FPS)

    @pytest.mark.parametrize(
        ("width", "height", "fps"),
        [
            (0, 0, 0.0),  # nothing measured yet
            (0, 4000, 0.0),  # one side unknown
            (4000, 0, 0.0),
            (-3840, -2160, -60.0),
            (math.nan, 2160, math.nan),
        ],
    )
    def test_an_unknown_value_exceeds_nothing(self, width: float, height: float, fps: float) -> None:
        assert over_limits(width, height, fps) == ()

    @pytest.mark.parametrize("bad", [None, "abc", "1920", True, False, [1920], object()])
    @pytest.mark.parametrize("position", [0, 1, 2])
    def test_a_value_that_is_not_a_number_is_refused(self, bad: object, position: int) -> None:
        args: list[object] = [1920, 1080, 30.0]
        args[position] = bad
        with pytest.raises(TypeError):
            over_limits(*args)  # type: ignore[arg-type]

    def test_the_tolerance_absorbs_a_declared_ntsc_rate(self) -> None:
        assert over_limits(1920, 1080, 30000 / 1001) == ()


# --------------------------------------------------------------------------- #
# The meter
# --------------------------------------------------------------------------- #


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _feed(meter: FrameMeter, clock: _Clock, times: list[float]) -> list[SourceReading | None]:
    """Deliver a frame at each absolute time; the reading after each frame."""
    readings = []
    for t in times:
        clock.now = t
        meter.note_frame()
        readings.append(meter.reading)
    return readings


def _steady(fps: float, seconds: float, start: float = 1000.0) -> list[float]:
    return [start + i / fps for i in range(int(seconds * fps))]


def _meter(width: int = 1920, height: int = 1080) -> tuple[FrameMeter, _Clock]:
    clock = _Clock()
    meter = FrameMeter(clock=clock)
    meter.note_caps(width, height)
    return meter, clock


class TestTheMeterWaitsForAWindow:
    def test_nothing_is_read_before_the_first_frame(self) -> None:
        meter, _clock = _meter()
        assert meter.reading is None

    def test_until_the_window_spans_no_rate_is_judged(self) -> None:
        meter, clock = _meter()
        readings = _feed(meter, clock, _steady(120.0, sl.WINDOW_SLOTS * sl.SLOT_S - 0.1))
        assert all(r is not None and r.fps is None and FPS not in r.over for r in readings)

    def test_a_source_over_the_rate_is_named_only_once_confirmed(self) -> None:
        meter, clock = _meter()
        times = _steady(60.0, 20.0)
        readings = _feed(meter, clock, times)
        first_over = next(i for i, r in enumerate(readings) if r is not None and FPS in r.over)
        # A 5 s window, then the third reading in a row that agrees.
        assert 7.0 <= times[first_over] - times[0] < 7.1
        assert readings[-1] is not None
        assert readings[-1].fps == pytest.approx(60.0, abs=0.01)


class TestASteadyThirtyNeverReadsOver:
    @pytest.mark.parametrize("fps", [25.0, 29.97, 30.0, 30000 / 1001])
    def test_a_steady_feed_at_or_under_the_limit(self, fps: float) -> None:
        meter, clock = _meter()
        readings = _feed(meter, clock, _steady(fps, 120.0))
        assert all(FPS not in r.over for r in readings if r is not None)
        final = readings[-1]
        assert final is not None and final.fps == pytest.approx(fps, abs=0.01)
        assert f"{final.fps:.0f}" in ("25", "30")

    @pytest.mark.parametrize("seed", range(5))
    def test_arrival_jitter_does_not_make_thirty_read_over(self, seed: int) -> None:
        """Each frame arrives up to 60 ms late, as a busy network and decoder deliver it."""
        rng = random.Random(seed)
        times = sorted(t + rng.uniform(0.0, 0.06) for t in _steady(30.0, 300.0))
        meter, clock = _meter()
        readings = _feed(meter, clock, times)
        assert all(FPS not in r.over for r in readings if r is not None)
        assert all(r.fps is None or r.fps < 30.5 for r in readings if r is not None)

    def test_one_reading_over_does_not_raise_the_row(self) -> None:
        """A burst after a stall makes one window read high; it is not a fault."""
        times = _steady(30.0, 12.0)
        # 2 s with nothing, then those frames all at once.
        burst_at = times[-1] + 2.0
        times += [burst_at + i * 0.001 for i in range(60)]
        times += _steady(30.0, 30.0, start=burst_at + 0.1)
        meter, clock = _meter()
        readings = _feed(meter, clock, times)
        assert all(FPS not in r.over for r in readings if r is not None)


class TestTheVerdictDoesNotFlap:
    def test_it_clears_only_once_confirmed(self) -> None:
        meter, clock = _meter()
        over = _steady(60.0, 15.0)
        _feed(meter, clock, over)
        assert meter.reading is not None and FPS in meter.reading.over
        start = over[-1] + 1 / 60
        under = _steady(30.0, 30.0, start=start)
        readings = _feed(meter, clock, under)
        first_clear = next(i for i, r in enumerate(readings) if r is not None and FPS not in r.over)
        # The old frames leave the window, then the readings must agree in a row.
        assert under[first_clear] - start >= sl.CONFIRM_READINGS * sl.SLOT_S
        assert all(FPS not in r.over for r in readings[first_clear:] if r is not None)
        assert readings[-1] is not None and readings[-1].fps == pytest.approx(30.0, abs=0.01)

    def test_the_rate_shown_is_the_one_behind_the_verdict(self) -> None:
        """While the readings disagree with an over verdict, the row must not say ``30 fps > 30 fps``."""
        meter, clock = _meter()
        over = _steady(60.0, 15.0)
        _feed(meter, clock, over)
        under = _steady(30.0, 30.0, start=over[-1] + 1 / 60)
        for r in _feed(meter, clock, under):
            assert r is not None
            if FPS in r.over:
                assert r.fps is not None and r.fps > 30.5

    def test_a_wandering_over_limit_rate_keeps_the_text(self) -> None:
        """A decoder-limited feed drifting between 44 and 46 fps: the row and the box
        keep the rate the verdict was reached at, so the box is not redrawn and re-announced."""
        per_second = [(44.0, 45.0, 46.0, 45.0)[(second // 4) % 4] for second in range(60)]
        # The 5 s average does cross whole numbers: 44.6 reads 45, 45.4 reads 45, 44.4 reads 44 ...
        assert len({f"{sum(per_second[s - 5 : s]) / 5:.0f}" for s in range(5, 60)}) > 1
        meter, clock = _meter(3840, 2160)
        times: list[float] = []
        for second, fps in enumerate(per_second):
            times += [1000.0 + second + k / fps for k in range(int(fps))]
        slots: list[SourceReading] = []
        for r in _feed(meter, clock, times):
            if r is not None and FPS in r.over and (not slots or r is not slots[-1]):
                slots.append(r)
        # Read at the flip and once a window later, when no frame from before it is left; then held.
        settled = slots[sl.WINDOW_SLOTS :]
        assert len(settled) > 30
        assert {limits_row(r) for r in settled} == {limits_row(settled[0])}
        assert {limits_sentence(r) for r in settled} == {limits_sentence(settled[0])}

    def test_the_shown_rate_is_taken_again_when_the_verdict_changes(self) -> None:
        meter, clock = _meter()
        first = _steady(60.0, 15.0)
        _feed(meter, clock, first)
        assert limits_row(meter.reading) == "Video 60 fps > 30 fps"
        within = _steady(30.0, 20.0, start=first[-1] + 1 / 60)
        _feed(meter, clock, within)
        assert meter.reading is not None and meter.reading.over == ()
        _feed(meter, clock, _steady(50.0, 15.0, start=within[-1] + 1 / 30))
        assert limits_row(meter.reading) == "Video 50 fps > 30 fps"


class TestAVariableRateFeed:
    def test_it_is_judged_on_what_arrives_on_average(self) -> None:
        """Bursts of 45 and 15 fps average 30: within."""
        times: list[float] = []
        t = 1000.0
        for i in range(60):
            fps = 45.0 if i % 2 == 0 else 15.0
            times += [t + k / fps for k in range(int(fps))]
            t += 1.0
        meter, clock = _meter()
        readings = _feed(meter, clock, times)
        assert all(FPS not in r.over for r in readings if r is not None)

    def test_a_variable_feed_averaging_over_the_limit_is_over(self) -> None:
        times: list[float] = []
        t = 1000.0
        for i in range(30):
            fps = 70.0 if i % 2 == 0 else 30.0
            times += [t + k / fps for k in range(int(fps))]
            t += 1.0
        meter, clock = _meter()
        _feed(meter, clock, times)
        assert meter.reading is not None and FPS in meter.reading.over


class TestTheMeterReadsTheFrameSize:
    def test_an_oversized_frame_is_named_at_once(self) -> None:
        meter, clock = _meter(3840, 2160)
        _feed(meter, clock, [1000.0])
        assert meter.reading == SourceReading(3840, 2160, None, (RES,))

    def test_a_portrait_full_hd_frame_is_within(self) -> None:
        meter, clock = _meter(1080, 1920)
        _feed(meter, clock, [1000.0])
        assert meter.reading is not None and meter.reading.over == ()

    def test_frames_with_no_size_yet_exceed_nothing(self) -> None:
        clock = _Clock()
        meter = FrameMeter(clock=clock)
        _feed(meter, clock, [1000.0])
        assert meter.reading == SourceReading(0, 0, None, ())

    def test_a_size_change_mid_stream_is_read_at_the_next_slot(self) -> None:
        meter, clock = _meter()
        _feed(meter, clock, _steady(30.0, 2.0))
        meter.note_caps(3840, 2160)
        _feed(meter, clock, _steady(30.0, 2.0, start=1002.0))
        assert meter.reading is not None and meter.reading.over == (RES,)
        assert (meter.reading.width, meter.reading.height) == (3840, 2160)

    def test_both_at_once(self) -> None:
        meter, clock = _meter(3840, 2160)
        _feed(meter, clock, _steady(60.0, 15.0))
        assert meter.reading is not None and meter.reading.over == (RES, FPS)


# --------------------------------------------------------------------------- #
# Text
# --------------------------------------------------------------------------- #

_RES_ONLY = SourceReading(3840, 2160, 30.0, (RES,))
_FPS_ONLY = SourceReading(1920, 1080, 59.94, (FPS,))
_BOTH = SourceReading(3840, 2160, 60.0, (RES, FPS))
_WITHIN = SourceReading(1920, 1080, 30.0, ())


class TestTheOperatorScreenRow:
    @pytest.mark.parametrize(
        ("reading", "row"),
        [
            (_RES_ONLY, "Video 3840×2160 > Full HD"),
            (_FPS_ONLY, "Video 60 fps > 30 fps"),
            (_BOTH, "Video 3840×2160 > Full HD, 60 fps > 30 fps"),
            (_WITHIN, ""),
            (None, ""),
        ],
    )
    def test_the_row_names_what_is_over(self, reading: SourceReading | None, row: str) -> None:
        assert limits_row(reading) == row

    def test_the_row_fits_a_badge_row(self) -> None:
        assert len(limits_row(_BOTH)) <= CONTROL_CONFLICT_ROW_MAX_CHARS


class TestTheWebSentence:
    @pytest.mark.parametrize(
        ("reading", "sentence"),
        [
            (_RES_ONLY, "Video arrives at 3840×2160, over Full HD (1920×1080)."),
            (_FPS_ONLY, "Video arrives at 60 fps, over 30 fps."),
            (_BOTH, "Video arrives at 3840×2160 and 60 fps, over Full HD (1920×1080) and 30 fps."),
            (_WITHIN, ""),
            (None, ""),
        ],
    )
    def test_the_observed_value_sits_beside_the_limit(self, reading: SourceReading | None, sentence: str) -> None:
        assert limits_sentence(reading) == sentence

    @pytest.mark.parametrize("kind", [SourceKind.REMOTE, SourceKind.LISTENER, SourceKind.NAMED])
    def test_a_sender_is_told_to_set_the_camera_or_encoder(self, kind: SourceKind) -> None:
        assert limits_action(kind) == "Set the camera or encoder to 1920×1080 at 30 fps or less."

    def test_a_local_camera_is_pointed_at_the_camera_and_the_station_s_settings(self) -> None:
        """A USB camera can be read past its settings (a capture card at its incoming
        signal), so the step names both, and no control by a label it may not carry."""
        assert limits_action(SourceKind.LOCAL) == (
            "Set the camera, or the signal into the capture card, to 1920×1080 at 30 fps or less, "
            "or lower the resolution and frame rate under Video Source."
        )


class TestTheStatsSnapshot:
    def test_nothing_measured(self) -> None:
        assert limits_snapshot(None) == {
            "exceeded": [],
            "width": 0,
            "height": 0,
            "fps": None,
            "max_long_side": 1920,
            "max_short_side": 1080,
            "max_fps": 30,
            "text": "",
            "action": "",
        }

    def test_over_carries_stable_ids_and_the_box_text(self) -> None:
        snap = limits_snapshot(_BOTH)
        assert snap["exceeded"] == ["resolution_over_limit", "frame_rate_over_limit"]
        assert (snap["width"], snap["height"], snap["fps"]) == (3840, 2160, 60.0)
        assert snap["text"] == limits_sentence(_BOTH)
        assert snap["action"] == limits_action(SourceKind.REMOTE)

    def test_the_step_follows_the_source_kind(self) -> None:
        assert limits_snapshot(_BOTH, SourceKind.LOCAL)["action"] == limits_action(SourceKind.LOCAL)
        assert limits_snapshot(_WITHIN, SourceKind.LOCAL)["action"] == ""

    def test_within_carries_the_measurement_and_no_box(self) -> None:
        snap = limits_snapshot(_WITHIN)
        assert snap["exceeded"] == []
        assert snap["fps"] == 30.0
        assert snap["text"] == "" and snap["action"] == ""

    def test_the_ids_are_a_wire_interface(self) -> None:
        assert {cause.value for cause in OverLimit} == {"resolution_over_limit", "frame_rate_over_limit"}
