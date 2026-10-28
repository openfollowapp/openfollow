# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""When video frames meet the display refresh, and what that did to them.

Unlocked, each refresh shows the newest frame that arrived. Locked, a frame is
due at its timestamp plus one offset, so frames keep the spacing they were
captured with. The offset puts due times halfway between two refreshes, where
neither arrival jitter nor refresh jitter can move a frame to the neighbouring
refresh, and moves by whole refreshes to cover how late the recent frames
arrived, all but the latest few. Either way the cadence reports how long frames
stayed on screen, waited and arrived apart.
"""

from __future__ import annotations

import math
import threading
from collections import Counter, deque
from dataclasses import dataclass

# More queued frames than this means the timestamps no longer match the clock.
MAX_QUEUED = 6
# A timestamp step larger than this starts a new stream.
MAX_PTS_STEP_US = 1_000_000
# The recent frames the offset covers, all but the latest share of them.
WINDOW_FRAMES = 150
COVER = 0.98
# The offset drops only after this many frames in a row would have allowed it.
LOWER_AFTER_FRAMES = 60
# The arrival spread covers at most this many latest frames, so a display that stops rendering holds no more.
SPREAD_FRAMES = 3600


@dataclass(frozen=True)
class CadenceReport:
    """What the cadence did since the last report."""

    refreshes: dict[int, int]
    skipped: int
    late: int
    wait_ms_p50: float
    wait_ms_p99: float
    # How much later than the earliest frame of the period the median and the 98th percentile arrived.
    spread_ms_p50: float = 0.0
    spread_ms_p98: float = 0.0

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


class Durations:
    """How long something took over a report period, in µs; reports its count, median and 99th percentile in ms."""

    def __init__(self, limit: int = 3600) -> None:
        self._values: deque[int] = deque(maxlen=limit)

    def add(self, us: int) -> None:
        self._values.append(us)

    def take(self) -> tuple[int, float, float]:
        """The count, median and 99th percentile since the last take; starts the next period."""
        values = list(self._values)
        self._values.clear()
        return len(values), _percentile(values, 0.5), _percentile(values, 0.99)


class Cadence[T]:
    """Frames in from the streaming thread, one pick per display refresh on the GTK main thread.

    Unlocked, each refresh shows the newest frame that arrived, and only the newest is held.
    """

    def __init__(self, locked: bool = True, max_hold_refreshes: int | None = None) -> None:
        self._locked = locked
        # Capped: a frame is held at most this many refreshes past the median arrival; later ones show late.
        self._max_hold = max_hold_refreshes
        self._lock = threading.Lock()
        self._queue: deque[tuple[T, int | None, int]] = deque()
        self._offset: int | None = None
        self._last_pts: int | None = None
        self._interval = 0
        self._recent: deque[int] = deque(maxlen=WINDOW_FRAMES)
        self._lower_run = 0
        self._lateness: deque[int] = deque(maxlen=SPREAD_FRAMES)
        self._last_refresh: int | None = None
        self._shown_at: int | None = None
        self._refreshes: Counter[int] = Counter()
        self._skipped = 0
        self._late = 0
        self._waits: list[int] = []
        self._closed = False

    def push(self, frame: T, pts_us: int | None, arrival_us: int, max_queued: int = MAX_QUEUED) -> None:
        """Queue a frame; ``max_queued`` lower for frames that hold a decoder's own buffers, which it has few of."""
        with self._lock:
            if self._closed:
                return
            if pts_us is not None:
                if self._last_pts is not None and abs(pts_us - self._last_pts) > MAX_PTS_STEP_US:
                    # The queued frames belong to the old timeline.
                    self._skipped += len(self._queue)
                    self._queue.clear()
                    self._offset = None
                    self._recent.clear()
                    self._lateness.clear()
                self._last_pts = pts_us
                self._recent.append(arrival_us - pts_us)
                self._lateness.append(arrival_us - pts_us)
            self._queue.append((frame, pts_us, arrival_us))
            # Unlocked, only the newest can show, so the ones before it go back to the decoder at once.
            if not self._locked:
                max_queued = 1
            if len(self._queue) > max_queued:
                while len(self._queue) > max_queued:
                    self._queue.popleft()
                    self._skipped += 1
                self._offset = None
                # The lateness that overfilled the queue would raise the new offset past what it holds again.
                self._recent.clear()
            if self._offset is not None and pts_us is not None:
                self._late += pts_us + self._offset < arrival_us
                self._steer(self._offset)

    def _steer(self, offset: int) -> None:
        """Move the offset by whole refreshes to the least covering the recent frames: up at once, down after a run."""
        recent = sorted(self._recent)
        need = recent[min(len(recent) - 1, int(COVER * len(recent)))]
        interval = self._interval
        target = offset + math.ceil((need - offset) / interval) * interval
        if self._max_hold is not None:
            bound = recent[len(recent) // 2] + self._max_hold * interval
            target = min(target, offset + math.floor((bound - offset) / interval) * interval)
        if target < offset:
            self._lower_run += 1
            if self._lower_run < LOWER_AFTER_FRAMES:
                return
        self._offset = target
        self._lower_run = 0

    def close(self) -> None:
        """Drop the queued frames and refuse new ones until ``open``: their pipeline is stopping."""
        with self._lock:
            self._closed = True
            self._queue.clear()
            self._offset = None
            self._recent.clear()
            self._lateness.clear()

    def open(self) -> None:
        """Take frames again, from a new stream; the first anchors anew."""
        with self._lock:
            self._closed = False

    def pick(self, refresh_us: int, interval_us: int) -> T | None:
        """The frame to show at the refresh at ``refresh_us``; None keeps the frame on screen.

        Time on screen counts the refreshes elapsed, a missed one included; a repeated refresh picks nothing.
        """
        with self._lock:
            if refresh_us == self._last_refresh:
                return None
            self._last_refresh = refresh_us
            self._interval = interval_us
            frame = self._pick(refresh_us, interval_us)
            if frame is None:
                return None
            if self._shown_at is not None:
                self._refreshes[max(1, round((refresh_us - self._shown_at) / interval_us))] += 1
            self._shown_at = refresh_us
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
            self._lower_run = 0
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
            earliest = min(self._lateness, default=0)
            spread = [v - earliest for v in self._lateness]
            report = CadenceReport(
                refreshes=dict(self._refreshes),
                skipped=self._skipped,
                late=self._late,
                wait_ms_p50=_percentile(waits, 0.5),
                wait_ms_p99=_percentile(waits, 0.99),
                spread_ms_p50=_percentile(spread, 0.5),
                spread_ms_p98=_percentile(spread, 0.98),
            )
            self._refreshes = Counter()
            self._skipped = self._late = 0
            self._waits = []
            self._lateness.clear()
            return report
