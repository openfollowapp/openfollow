# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Reusable runtime metrics and pooling helpers.

``FrameMetrics`` tracks per-frame timing (lifetime + recent-window FPS,
slow-frame counts); ``OverlayStatePool`` recycles ``OverlayState``
objects to reduce GC pressure on the frame loop.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field

from openfollow.runtime.overlay_state import OverlayState

logger = logging.getLogger(__name__)

_FRAME_METRICS_WINDOW = 300


@dataclass
class FrameMetrics:
    """Collected per-frame timing metrics."""

    frame_count: int = 0
    slow_frames: int = 0
    total_frame_time: float = 0.0
    slow_frame_threshold: float = 0.020  # 20ms threshold
    recent_frame_times: deque[float] = field(default_factory=lambda: deque(maxlen=_FRAME_METRICS_WINDOW))
    last_frame_time: float = 0.0

    def add_frame(self, frame_time: float) -> None:
        """Record a frame's execution time."""
        self.frame_count += 1
        self.total_frame_time += frame_time
        self.last_frame_time = frame_time
        self.recent_frame_times.append(frame_time)
        if frame_time > self.slow_frame_threshold:
            self.slow_frames += 1

    def avg_frame_time(self) -> float:
        """Return lifetime average frame time in milliseconds."""
        if self.frame_count == 0:
            return 0.0
        return (self.total_frame_time / self.frame_count) * 1000.0

    def slow_frame_percent(self) -> float:
        """Return lifetime percentage of slow frames."""
        if self.frame_count == 0:
            return 0.0
        return (self.slow_frames / self.frame_count) * 100.0

    def effective_fps(self) -> float:
        """Return lifetime effective FPS from measured frame times."""
        if self.total_frame_time <= 0.0:
            return 0.0
        return self.frame_count / self.total_frame_time

    def recent_avg_frame_time(self) -> float:
        """Return average frame time for the recent window in milliseconds."""
        if not self.recent_frame_times:
            return 0.0
        return (sum(self.recent_frame_times) / len(self.recent_frame_times)) * 1000.0

    def recent_slow_frame_percent(self) -> float:
        """Return recent-window percentage of slow frames."""
        if not self.recent_frame_times:
            return 0.0
        slow_recent = sum(1 for frame_time in self.recent_frame_times if frame_time > self.slow_frame_threshold)
        return (slow_recent / len(self.recent_frame_times)) * 100.0

    def recent_effective_fps(self) -> float:
        """Return recent-window effective FPS."""
        if not self.recent_frame_times:
            return 0.0
        window_time = sum(self.recent_frame_times)
        if window_time <= 0.0:
            return 0.0
        return len(self.recent_frame_times) / window_time

    def snapshot(self) -> dict[str, float | int]:
        """Return a web-friendly playback performance snapshot."""
        return {
            "frame_count_total": int(self.frame_count),
            "slow_frames_total": int(self.slow_frames),
            "avg_frame_ms": float(self.avg_frame_time()),
            "slow_frame_percent": float(self.slow_frame_percent()),
            "effective_fps": float(self.effective_fps()),
            "recent_avg_frame_ms": float(self.recent_avg_frame_time()),
            "recent_slow_frame_percent": float(self.recent_slow_frame_percent()),
            "recent_effective_fps": float(self.recent_effective_fps()),
            "last_frame_ms": float(self.last_frame_time * 1000.0),
            "slow_frame_threshold_ms": float(self.slow_frame_threshold * 1000.0),
        }


class OverlayStatePool:
    """Object pool for OverlayState to reduce garbage collection pressure."""

    def __init__(self, pool_size: int = 3) -> None:
        self._pool_size = pool_size
        self._pool: deque[OverlayState] = deque([OverlayState() for _ in range(pool_size)])
        self._allocated_count = 0
        self._fallback_count = 0

    def acquire(self) -> OverlayState:
        """Get a state from the pool, or allocate a new one if pool is empty."""
        if self._pool:
            state = self._pool.popleft()
            state.reset()
            self._allocated_count += 1
            return state
        # Allocate if pool exhausted.
        self._fallback_count += 1
        logger.debug("OverlayStatePool exhausted (fallback allocations: %d)", self._fallback_count)
        return OverlayState()

    def release(self, state: OverlayState) -> None:
        """Return a state to the pool for reuse."""
        if len(self._pool) < self._pool_size:
            self._pool.append(state)
