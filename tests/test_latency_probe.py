# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The camera latency probe on a fake clock: a display that shows each level at its next refresh,
a camera that sees the screen a set time late, and synthetic luma traces."""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, field

import pytest

from openfollow.runtime import latency_probe as lp
from openfollow.runtime.latency_probe import FailureReason, LatencyProbe, Phase

pytestmark = pytest.mark.unit

FEED = ("rtsp", 1)
MS = 1000
LO, HI = 0.1, 0.8


def _probe() -> LatencyProbe:
    return LatencyProbe(wall_clock=lambda: 1_700_000_000.0, rng=random.Random(7))


@dataclass
class _Bench:
    """A station on a fake clock: frame loop, display refreshes and camera frames, each on its own period."""

    probe: LatencyProbe
    # The time the camera's picture of transition ``k`` (0 the first white) arrives after it shows.
    delay_us: Callable[[int], int]
    frame_us: int = 33_333
    refresh_us: int = 16_667
    step_us: int = 16_000
    # Added to every frame's luma by the arrival time: auto exposure, stage light.
    drift: Callable[[int], float] = lambda _t: 0.0
    # The camera sees the screen at all.
    sees: bool = True
    feed: object = FEED
    shown: list[tuple[int, float]] = field(default_factory=list)

    def luma_at(self, arrival_us: int) -> float:
        level = lp.BLACK
        if self.sees:
            # The settle's black needs no delay: the screen starts dark.
            for k, (refresh, new) in enumerate(self.shown[1:]):
                if arrival_us >= refresh + self.delay_us(k):
                    level = new
        return LO + (HI - LO) * level + self.drift(arrival_us)

    def run(self, until_us: int = 60_000_000) -> None:
        self.probe.start(0, self.feed, "rtsp")
        refresh, frame, step = self.refresh_us, 7 * MS, 3 * MS
        while self.probe.active and min(refresh, frame, step) < until_us:
            now = min(refresh, frame, step)
            if now == refresh:
                level = self.probe.screen_level()
                if level is not None:
                    if not self.shown or self.shown[-1][1] != level:
                        self.shown.append((now, level))
                    self.probe.presented(level, now)
                refresh += self.refresh_us
            elif now == frame:
                self.probe.feed(now, self.luma_at(now))
                frame += self.frame_us
            else:
                self.probe.step(now, self.feed)
                step += self.step_us


def test_a_clean_run_measures_the_cameras_delay_within_one_frame() -> None:
    bench = _Bench(_probe(), delay_us=lambda _k: 180 * MS)
    bench.run()
    result = bench.probe.result
    assert result is not None and bench.probe.failure is None
    assert result.samples == lp.SAMPLES
    assert abs(result.median_ms - 180) <= bench.frame_us / MS
    assert bench.probe.status()["state"] == "done"


def test_the_median_and_spread_cover_every_edge_timed() -> None:
    delays = [40, 50, 60, 70, 80, 90, 100, 110, 120, 130]
    # A frame every millisecond, so each edge lands within one of its delay.
    bench = _Bench(_probe(), delay_us=lambda k: delays[k] * MS, frame_us=MS)
    bench.run()
    result = bench.probe.result
    assert result is not None
    assert result.samples == 10
    assert result.median_ms == pytest.approx(85, abs=1)
    assert result.min_ms == pytest.approx(40, abs=1)
    assert result.max_ms == pytest.approx(130, abs=1)


def test_both_edges_of_five_flashes_are_timed() -> None:
    bench = _Bench(_probe(), delay_us=lambda _k: 60 * MS)
    bench.run()
    assert [level for _t, level in bench.shown] == [lp.BLACK] + [lp.WHITE, lp.BLACK] * lp.FLASHES


def test_slow_auto_exposure_drift_never_reads_as_a_flash() -> None:
    # The camera does not see the screen; its exposure climbs a quarter of full scale over the run.
    bench = _Bench(_probe(), delay_us=lambda _k: 0, sees=False, drift=lambda t: 0.25 * t / 4_000_000)
    bench.run()
    assert bench.probe.failure is not None and bench.probe.failure.reason is FailureReason.NO_RISE
    assert bench.probe.status()["measured"] == 0


def test_drift_between_flashes_leaves_the_measurement_as_it_was() -> None:
    def swing(t: int) -> float:
        # Up and down by 6 % of full scale, a few seconds a cycle.
        return 0.06 * ((t // 1_000_000) % 4) / 3

    bench = _Bench(_probe(), delay_us=lambda _k: 120 * MS, drift=swing)
    bench.run()
    result = bench.probe.result
    assert result is not None
    assert abs(result.median_ms - 120) <= bench.frame_us / MS
    assert result.min_ms >= 120


def _drive_to_wait_rise(probe: LatencyProbe, t: int = 0) -> int:
    """Start, feed dark frames through the settle, and step until the probe asks for white."""
    probe.start(t, FEED, "rtsp")
    while probe.phase is not Phase.WAIT_RISE:
        t += 10 * MS
        probe.feed(t, LO)
        probe.step(t, FEED)
    return t


@pytest.mark.parametrize("trace", [(0.16, 0.22), (0.13, 0.19)], ids=["half-and-half", "slow-start"])
def test_a_rise_spread_over_two_frames_still_detects(trace: tuple[float, float]) -> None:
    # Each step alone is under the threshold; against the darkest of the last three frames it is not.
    probe = _probe()
    t = _drive_to_wait_rise(probe)
    probe.presented(lp.WHITE, t + MS)
    for luma in (LO, *trace):
        t += 33 * MS
        probe.feed(t, luma)
    probe.step(t, FEED)
    assert probe.phase is Phase.HOLD
    assert probe.status()["measured"] == 1


def test_a_fall_spread_over_two_frames_still_detects() -> None:
    probe = _probe()
    t = _drive_to_wait_rise(probe)
    probe.presented(lp.WHITE, t + MS)
    t += 33 * MS
    probe.feed(t, 0.18)
    probe.step(t, FEED)
    while probe.phase is not Phase.WAIT_FALL:
        t += 10 * MS
        probe.feed(t, 0.18)
        probe.step(t, FEED)
    probe.presented(lp.BLACK, t + MS)
    # A first flash 0.08 bright makes the threshold the 4 % floor; the fall comes in two 3 % steps.
    for luma in (0.15, 0.12):
        t += 33 * MS
        probe.feed(t, luma)
    probe.step(t, FEED)
    assert probe.phase is Phase.GAP


def test_no_rise_within_3_s_fails_with_its_reason_at_the_timeout() -> None:
    probe = _probe()
    t = _drive_to_wait_rise(probe)
    probe.presented(lp.WHITE, t + MS)
    requested = t
    while t + 10 * MS < requested + lp.TIMEOUT_US:
        t += 10 * MS
        probe.feed(t, LO)
        probe.step(t, FEED)
    assert probe.active
    probe.step(requested + lp.TIMEOUT_US, FEED)
    assert probe.failure is not None and probe.failure.reason is FailureReason.NO_RISE
    assert probe.screen_level() is None
    assert probe.status()["failure"] == {
        "reason": "no_rise",
        "text": "No flash seen in the video within 3 s.",
        "action": "Point the camera at the Operator Screen.",
    }


def test_no_fall_within_3_s_fails_with_its_reason() -> None:
    # The camera's picture turns white with the first flash and stays white.
    bench = _Bench(_probe(), delay_us=lambda k: 50 * MS if k == 0 else 10_000_000)
    bench.run()
    assert bench.probe.failure is not None and bench.probe.failure.reason is FailureReason.NO_FALL
    assert bench.probe.status()["state"] == "failed"


def test_a_display_that_never_shows_the_flash_fails_as_no_rise() -> None:
    probe = _probe()
    t = _drive_to_wait_rise(probe)
    for _ in range(400):
        t += 10 * MS
        probe.feed(t, HI)
        probe.step(t, FEED)
    assert probe.failure is not None and probe.failure.reason is FailureReason.NO_RISE


def test_stopping_mid_run_ends_it_and_takes_no_more_frames() -> None:
    probe = _probe()
    t = _drive_to_wait_rise(probe)
    probe.stop()
    assert probe.failure is not None and probe.failure.reason is FailureReason.STOPPED
    assert probe.screen_level() is None
    probe.feed(t + MS, HI)
    assert probe._frames == type(probe._frames)()
    assert probe.status()["state"] == "failed"


def test_a_source_change_mid_run_ends_it() -> None:
    probe = _probe()
    t = _drive_to_wait_rise(probe)
    probe.step(t + MS, ("rtsp", 2))
    assert probe.failure is not None and probe.failure.reason is FailureReason.SOURCE_CHANGED


def test_a_feed_that_stops_decoding_mid_run_ends_it() -> None:
    probe = _probe()
    t = _drive_to_wait_rise(probe)
    probe.step(t + MS, None)
    assert probe.failure is not None and probe.failure.reason is FailureReason.SOURCE_CHANGED


def test_an_edge_before_the_flash_was_shown_is_never_paired_with_it() -> None:
    probe = _probe()
    t = _drive_to_wait_rise(probe)
    # Bright after the request, before any refresh showed the white: stage light, not the flash.
    probe.feed(t + 5 * MS, HI)
    probe.step(t + 5 * MS, FEED)
    shown = t + 20 * MS
    # Drained only after the report, but it arrived before the white was on screen.
    probe.feed(shown - MS, HI)
    probe.presented(lp.WHITE, shown)
    probe.step(shown, FEED)
    assert probe.phase is Phase.WAIT_RISE and probe.status()["measured"] == 0
    for after in (10, 20, 30):
        probe.feed(shown + after * MS, LO)
    probe.feed(shown + 100 * MS, HI)
    probe.step(shown + 100 * MS, FEED)
    assert probe.phase is Phase.HOLD
    assert probe._delays_us == [100 * MS]


def test_a_refresh_from_before_the_request_does_not_count_as_shown() -> None:
    probe = _probe()
    t = _drive_to_wait_rise(probe)
    probe.presented(lp.WHITE, t - MS)
    probe.presented(lp.BLACK, t + MS)
    assert probe._shown_us is None
    probe.presented(lp.WHITE, t + 2 * MS)
    probe.presented(lp.WHITE, t + 20 * MS)
    assert probe._shown_us == t + 2 * MS


def test_an_unreadable_frame_fails_the_run_instead_of_waiting_out_the_timeout() -> None:
    probe = _probe()
    probe.start(0, FEED, "rtsp")
    probe.feed(MS, None)
    probe.step(2 * MS, FEED)
    assert probe.failure is not None and probe.failure.reason is FailureReason.UNSUPPORTED_FORMAT


def test_idle_asks_for_no_level_and_takes_no_frames() -> None:
    probe = _probe()
    assert probe.screen_level() is None
    probe.feed(MS, HI)
    probe.step(2 * MS, FEED)
    assert not probe._frames
    assert probe.status() == {"state": "idle", "measured": 0, "total": 10, "result": None, "failure": None}


def test_a_run_with_no_decoded_video_ends_at_once() -> None:
    probe = _probe()
    probe.start(0, None, "rtsp")
    assert not probe.active
    assert probe.failure is not None and probe.failure.reason is FailureReason.NO_VIDEO


def test_a_failed_run_keeps_the_last_success() -> None:
    probe = _probe()
    bench = _Bench(probe, delay_us=lambda _k: 90 * MS)
    bench.run()
    success = probe.last_success
    assert success is not None
    _drive_to_wait_rise(probe, t=100_000_000)
    probe.stop()
    assert probe.result is None
    assert probe.last_success is success


def test_a_result_reads_with_its_level_age_and_source() -> None:
    probe = _probe()
    _Bench(probe, delay_us=lambda _k: 90 * MS).run()
    result = probe.result
    assert result is not None
    out = result.to_dict(result.finished_us + 5_000_000)
    assert out["level"] == "success"
    assert out["age_s"] == pytest.approx(5.0)
    assert out["source_type"] == "rtsp"
    assert out["finished_at"] == 1_700_000_000.0
    assert out["samples"] == 10


@pytest.mark.parametrize(
    ("median_ms", "level"),
    [(0.0, "success"), (119.9, "success"), (120.0, "caution"), (249.9, "caution"), (250.0, "error"), (900.0, "error")],
)
def test_the_level_of_a_round_trip(median_ms: float, level: str) -> None:
    assert lp.result_level(median_ms) == level


@pytest.mark.parametrize("reason", list(FailureReason))
def test_every_failure_says_what_was_seen(reason: FailureReason) -> None:
    failure = lp.LatencyFailure(reason)
    assert failure.text.endswith(".")
    assert "—" not in failure.text + failure.action
