# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Michel Honold and Paul Hermann
"""HudMetrics: what the Operator Screen costs and how evenly pictures and video frames reach the screen."""

from __future__ import annotations

import pytest

from openfollow.video.hud_metrics import HudMetrics

pytestmark = pytest.mark.unit

START = 100.0


def _closed(metrics: HudMetrics, after_s: float = 2.0) -> dict:
    summary = metrics.summary(START + after_s)
    assert summary is not None
    return summary


def test_nothing_is_reported_before_the_first_window_closes() -> None:
    metrics = HudMetrics(START)
    metrics.picture(0.001, 0.004)
    assert metrics.summary(START + 1.9) is None


def test_a_window_reports_what_pictures_and_repaints_cost_on_average() -> None:
    metrics = HudMetrics(START)
    metrics.picture(0.001, 0.004)
    metrics.picture(0.003, 0.008)
    metrics.painted(0.002)
    metrics.painted(0.004)
    metrics.painted(0.006)
    metrics.refresh(False)
    summary = _closed(metrics, 2.0)
    assert summary["clear_ms"] == pytest.approx(2.0)
    assert summary["draw_ms"] == pytest.approx(6.0)
    assert summary["picture_ms"] == pytest.approx(8.0)
    assert summary["paint_ms"] == pytest.approx(4.0)
    assert summary["repaints_per_s"] == pytest.approx(1.5)
    assert summary["window_s"] == pytest.approx(2.0)
    assert (summary["gl_render_ms"], summary["gl_renders_per_s"]) == (None, None)


def test_a_window_reports_what_gl_renders_cost_on_average() -> None:
    metrics = HudMetrics(START)
    metrics.gl_rendered(0.002)
    metrics.gl_rendered(0.004)
    metrics.refresh(False)
    summary = _closed(metrics, 2.0)
    assert summary["gl_render_ms"] == pytest.approx(3.0)
    assert summary["gl_renders_per_s"] == pytest.approx(1.0)
    assert (summary["paint_ms"], summary["repaints_per_s"]) == (None, None)


def test_a_picture_with_nothing_to_clear_leaves_the_clear_cost_to_those_that_had() -> None:
    metrics = HudMetrics(START)
    metrics.picture(None, 0.004)
    metrics.refresh(False)
    assert _closed(metrics, 2.0)["clear_ms"] is None
    metrics.picture(0.002, 0.004)
    metrics.picture(None, 0.008)
    metrics.refresh(False)
    summary = _closed(metrics, 4.0)
    assert summary is not None
    assert summary["clear_ms"] == pytest.approx(2.0)
    assert summary["draw_ms"] == pytest.approx(6.0)
    assert summary["picture_ms"] == pytest.approx(7.0)


def test_a_window_with_nothing_drawn_reports_no_cost_rather_than_zero() -> None:
    metrics = HudMetrics(START)
    metrics.refresh(False)
    summary = _closed(metrics)
    costs = ("picture_ms", "clear_ms", "draw_ms", "paint_ms", "repaints_per_s", "gl_render_ms", "gl_renders_per_s")
    assert [summary[k] for k in costs] == [None] * len(costs)
    assert summary["pictures_per_s"] == 0.0


def test_a_window_without_a_refresh_reports_nothing_as_no_display_drew() -> None:
    metrics = HudMetrics(START)
    metrics.video_frame()
    assert metrics.summary(START + 2.0) is None
    # The display going away mid-run replaces the window it last drew in.
    metrics.refresh(False)
    assert _closed(metrics, 4.0) is not None
    metrics.video_frame()
    assert metrics.summary(START + 6.0) is None


def test_each_shown_picture_counts_the_refreshes_since_the_one_before() -> None:
    metrics = HudMetrics(START)
    # The first picture shown has nothing before it to count from.
    for shown in (True, False, True, True, False, False, True):
        metrics.refresh(shown)
    summary = _closed(metrics, 2.0)
    assert summary["picture_refreshes"] == {"1": 1, "2": 1, "3": 1}
    assert summary["pictures_per_s"] == pytest.approx(2.0)
    assert summary["refreshes_per_s"] == pytest.approx(3.5)


def test_video_frames_count_at_the_refresh_that_shows_them_and_extra_ones_are_replaced() -> None:
    metrics = HudMetrics(START)
    metrics.video_frame()
    metrics.refresh(False)
    metrics.refresh(False)
    metrics.video_frame()
    metrics.video_frame()  # two before one refresh: only the newer one reaches the screen
    metrics.refresh(False)
    metrics.video_frame()
    metrics.refresh(False)
    summary = _closed(metrics, 2.0)
    assert summary["video_refreshes"] == {"2": 1, "1": 1}
    assert summary["video_replaced"] == 1
    assert summary["video_frames_per_s"] == pytest.approx(2.0)


def test_the_closed_window_is_reported_until_the_next_one_closes_and_counting_starts_fresh() -> None:
    metrics = HudMetrics(START)
    metrics.picture(0.001, 0.001)
    metrics.refresh(False)
    first = _closed(metrics, 2.0)
    metrics.picture(0.005, 0.005)
    assert metrics.summary(START + 3.0) == first
    metrics.refresh(False)
    second = _closed(metrics, 4.0)
    assert second is not None
    assert second["picture_ms"] == pytest.approx(10.0)


def test_the_cadence_carries_across_windows() -> None:
    metrics = HudMetrics(START)
    metrics.refresh(True)
    _closed(metrics, 2.0)
    metrics.refresh(False)
    metrics.refresh(True)
    summary = metrics.summary(START + 4.0)
    assert summary is not None
    assert summary["picture_refreshes"] == {"2": 1}


def test_a_summary_is_a_copy() -> None:
    metrics = HudMetrics(START)
    metrics.refresh(False)
    summary = _closed(metrics)
    summary["picture_ms"] = 99.0
    assert metrics.summary(START + 2.5)["picture_ms"] is None


def test_the_pacing_interval_is_the_one_the_window_closed_with() -> None:
    metrics = HudMetrics(START)
    metrics.refresh(False)
    first = metrics.summary(START + 2.0, 2)
    assert first is not None
    assert first["interval_refreshes"] == 2
    # The interval changing later never alters a window already closed.
    metrics.refresh(False)
    assert metrics.summary(START + 2.5, 3) == first
    second = metrics.summary(START + 4.0, 3)
    assert second is not None
    assert second["interval_refreshes"] == 3
