# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for ``FrameMetrics`` and ``OverlayStatePool`` in ``runtime_metrics``."""

from __future__ import annotations

import pytest

from openfollow.runtime_metrics import FrameMetrics, OverlayStatePool

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# FrameMetrics
# ---------------------------------------------------------------------------


class TestFrameMetrics:
    def test_initial_state_is_zero(self) -> None:
        m = FrameMetrics()
        assert m.frame_count == 0
        assert m.avg_frame_time() == 0.0
        assert m.slow_frame_percent() == 0.0
        assert m.effective_fps() == 0.0
        assert m.recent_avg_frame_time() == 0.0
        assert m.recent_slow_frame_percent() == 0.0
        assert m.recent_effective_fps() == 0.0

    def test_add_frame_updates_counters(self) -> None:
        m = FrameMetrics()
        m.add_frame(0.010)
        assert m.frame_count == 1
        assert m.total_frame_time == pytest.approx(0.010)
        assert m.last_frame_time == pytest.approx(0.010)
        assert len(m.recent_frame_times) == 1

    def test_avg_frame_time_in_milliseconds(self) -> None:
        m = FrameMetrics()
        m.add_frame(0.010)
        m.add_frame(0.020)
        assert m.avg_frame_time() == pytest.approx(15.0)

    def test_slow_frame_counting(self) -> None:
        m = FrameMetrics()
        m.add_frame(0.010)  # fast
        m.add_frame(0.015)  # fast
        m.add_frame(0.025)  # slow (>20ms threshold)
        m.add_frame(0.030)  # slow
        assert m.slow_frames == 2
        assert m.slow_frame_percent() == pytest.approx(50.0)

    def test_effective_fps(self) -> None:
        m = FrameMetrics()
        for _ in range(100):
            m.add_frame(0.01)  # 10ms per frame = 100 FPS
        assert m.effective_fps() == pytest.approx(100.0)

    def test_recent_window_metrics(self) -> None:
        m = FrameMetrics()
        # Fill beyond the window (300 frames)
        for _ in range(300):
            m.add_frame(0.010)  # fast
        # Now add some slow frames
        for _ in range(10):
            m.add_frame(0.030)  # slow
        # Recent window should contain 300 fast + 10 slow = last 300 of the 310 total
        assert m.recent_slow_frame_percent() > 0.0
        assert m.recent_effective_fps() > 0.0
        assert m.recent_avg_frame_time() > 0.0

    def test_recent_effective_fps_handles_zero_time(self) -> None:
        m = FrameMetrics()
        # recent_frame_times is empty
        assert m.recent_effective_fps() == 0.0

    def test_recent_effective_fps_returns_zero_when_window_time_nonpositive(self) -> None:
        """When recent_frame_times is non-empty but the window sums to 0
        (or negative – defensive against future bugs), return 0.0
        rather than raising ZeroDivisionError. Covers line 77."""
        m = FrameMetrics()
        # A single zero-duration frame: recent_frame_times non-empty
        # but window_time == 0.0 → guard arm fires.
        m.recent_frame_times.append(0.0)
        assert m.recent_effective_fps() == 0.0

    def test_snapshot_returns_all_keys(self) -> None:
        m = FrameMetrics()
        m.add_frame(0.015)
        snap = m.snapshot()
        expected_keys = {
            "frame_count_total",
            "slow_frames_total",
            "avg_frame_ms",
            "slow_frame_percent",
            "effective_fps",
            "recent_avg_frame_ms",
            "recent_slow_frame_percent",
            "recent_effective_fps",
            "last_frame_ms",
            "slow_frame_threshold_ms",
        }
        assert set(snap.keys()) == expected_keys
        assert snap["frame_count_total"] == 1
        assert snap["last_frame_ms"] == pytest.approx(15.0)
        assert snap["slow_frame_threshold_ms"] == pytest.approx(20.0)

    def test_custom_slow_threshold(self) -> None:
        m = FrameMetrics(slow_frame_threshold=0.050)
        m.add_frame(0.030)  # under 50ms – fast
        m.add_frame(0.060)  # over 50ms – slow
        assert m.slow_frames == 1


# ---------------------------------------------------------------------------
# OverlayStatePool
# ---------------------------------------------------------------------------


class TestOverlayStatePool:
    def test_acquire_returns_reset_state(self) -> None:
        pool = OverlayStatePool(pool_size=2)
        state = pool.acquire()
        assert state.markers == []
        assert state.selected_id is None

    def test_pool_exhaustion_creates_new_instance(self) -> None:
        pool = OverlayStatePool(pool_size=1)
        s1 = pool.acquire()
        s2 = pool.acquire()  # pool exhausted, fallback allocation
        assert s1 is not s2
        assert pool._fallback_count == 1

    def test_release_and_reuse(self) -> None:
        pool = OverlayStatePool(pool_size=1)
        s1 = pool.acquire()
        pool.release(s1)
        s2 = pool.acquire()
        assert s2 is s1  # reused from pool

    def test_release_caps_at_pool_size(self) -> None:
        # release() caps at the configured pool_size, not a hardcoded 3.
        pool = OverlayStatePool(pool_size=2)
        states = [pool.acquire() for _ in range(2)]
        extra = pool.acquire()  # fallback (pool empty)
        for s in states:
            pool.release(s)
        pool.release(extra)
        # Only pool_size (2) fit; the extra release is dropped.
        assert len(pool._pool) == 2

    def test_release_honours_larger_pool_size(self) -> None:
        """A pool_size > 3 keeps up to pool_size states (the old hardcoded 3
        cap silently shrank larger pools)."""
        pool = OverlayStatePool(pool_size=4)
        states = [pool.acquire() for _ in range(4)]
        for s in states:
            pool.release(s)
        # All four refit (previously the 4th was dropped at the hardcoded 3).
        assert len(pool._pool) == 4
