# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Video frames shown on a cadence locked to the display refresh.

A frame is due at its timestamp plus one offset, so frames keep the spacing
they were captured with. The offset puts due times halfway between two
refreshes, where neither arrival jitter nor refresh jitter can move a frame to
the neighbouring refresh.
"""

from __future__ import annotations

import threading
from collections import Counter, deque
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")

# More queued frames than this means the timestamps no longer match the clock.
MAX_QUEUED = 6
# A timestamp step larger than this starts a new stream.
MAX_PTS_STEP_US = 1_000_000
# The offset drops by a refresh when this many frames in a row arrived a refresh early.
SETTLE_FRAMES = 300


@dataclass(frozen=True)
class CadenceReport:
    """What the cadence did since the last report."""

    refreshes: dict[int, int]
    skipped: int
    late: int
    wait_ms_p50: float
    wait_ms_p99: float

    @property
    def shown(self) -> int:
        return sum(self.refreshes.values())

    def share(self, refreshes: int) -> float:
        """The share of shown frames that stayed on screen for exactly ``refreshes`` refreshes."""
        return self.refreshes.get(refreshes, 0) / self.shown if self.shown else 0.0


def _percentile(values: list[int], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))] / 1000.0


class Cadence(Generic[T]):
    """Frames in from the streaming thread, one pick per display refresh on the GTK main thread.

    Unlocked, each refresh shows the newest frame that arrived.
    """

    def __init__(self, locked: bool = True) -> None:
        self._locked = locked
        self._lock = threading.Lock()
        self._queue: deque[tuple[T, int | None, int]] = deque()
        self._offset: int | None = None
        self._last_pts: int | None = None
        self._interval = 0
        self._early_run = 0
        self._on_screen = 0
        self._refreshes: Counter[int] = Counter()
        self._skipped = 0
        self._late = 0
        self._waits: list[int] = []

    def push(self, frame: T, pts_us: int | None, arrival_us: int) -> None:
        with self._lock:
            if pts_us is not None:
                if self._last_pts is not None and abs(pts_us - self._last_pts) > MAX_PTS_STEP_US:
                    self._offset = None
                self._last_pts = pts_us
            self._queue.append((frame, pts_us, arrival_us))
            if len(self._queue) > MAX_QUEUED:
                self._queue.popleft()
                self._skipped += 1
                self._offset = None
            if self._offset is not None and pts_us is not None:
                self._track_slack(self._offset, pts_us + self._offset - arrival_us)

    def _track_slack(self, offset: int, slack: int) -> None:
        interval = self._interval
        if slack < 0:
            # Late: move every due time back by whole refreshes, keeping the phase.
            self._late += 1
            self._offset = offset - (slack // interval) * interval
            self._early_run = 0
        elif slack >= interval:
            self._early_run += 1
            if self._early_run >= SETTLE_FRAMES:
                self._offset = offset - interval
                self._early_run = 0
        else:
            self._early_run = 0

    def pick(self, refresh_us: int, interval_us: int) -> T | None:
        """The frame to show at the refresh at ``refresh_us``; None keeps the frame on screen."""
        with self._lock:
            self._interval = interval_us
            frame = self._pick(refresh_us, interval_us)
            if frame is None:
                if self._on_screen:
                    self._on_screen += 1
                return None
            if self._on_screen:
                self._refreshes[self._on_screen] += 1
            self._on_screen = 1
            return frame

    def _pick(self, refresh_us: int, interval_us: int) -> T | None:
        queue = self._queue
        if not queue:
            return None
        newest_pts = queue[-1][1]
        if not self._locked or newest_pts is None:
            return self._take(len(queue), refresh_us)
        if self._offset is None:
            # Due half a refresh after this one, so the newest frame shows at the next.
            self._offset = refresh_us + interval_us // 2 - newest_pts
            self._early_run = 0
        due = sum(1 for _, pts, _ in queue if pts is not None and pts + self._offset <= refresh_us)
        return self._take(due, refresh_us) if due else None

    def _take(self, count: int, refresh_us: int) -> T:
        for _ in range(count - 1):
            self._queue.popleft()
        self._skipped += count - 1
        frame, _, arrival = self._queue.popleft()
        self._waits.append(refresh_us - arrival)
        return frame

    def report(self) -> CadenceReport:
        """The counts since the last report; starts the next period."""
        with self._lock:
            waits = self._waits
            report = CadenceReport(
                refreshes=dict(self._refreshes),
                skipped=self._skipped,
                late=self._late,
                wait_ms_p50=_percentile(waits, 0.5),
                wait_ms_p99=_percentile(waits, 0.99),
            )
            self._refreshes = Counter()
            self._skipped = self._late = 0
            self._waits = []
            return report
