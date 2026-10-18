# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""What the Operator Screen costs and how evenly pictures and video frames reach the screen.

Fed by the HUD render thread (a picture drawn), the GTK draw (a window repaint),
the display tick (a refresh, and whether it showed a new picture) and the
display sink's streaming thread (a video frame arrived). Read as a summary of
the last closed window of about two seconds.
"""

from __future__ import annotations

import threading
from typing import Any

WINDOW_S = 2.0


class _Window:
    def __init__(self, start: float) -> None:
        self.start = start
        self.pictures = 0
        self.clear_s = 0.0
        self.draw_s = 0.0
        self.paints = 0
        self.paint_s = 0.0
        self.refreshes = 0
        self.shown = 0
        self.held: dict[int, int] = {}
        self.video_frames = 0
        self.video_replaced = 0
        self.video_held: dict[int, int] = {}


def _ms(total_s: float, count: int) -> float:
    return 1000.0 * total_s / count if count else 0.0


def _histogram(counts: dict[int, int]) -> dict[str, int]:
    return {str(k): counts[k] for k in sorted(counts)}


class HudMetrics:
    """Counters for one Operator Screen; every method is safe from any thread."""

    def __init__(self, now: float, window_s: float = WINDOW_S) -> None:
        self._lock = threading.Lock()
        self._window_s = window_s
        self._current = _Window(now)
        self._last: dict[str, Any] | None = None
        # Refreshes since the last picture and the last video frame went on screen.
        self._since_picture: int | None = None
        self._since_video: int | None = None
        self._video_waiting = 0

    def picture(self, clear_s: float, draw_s: float) -> None:
        """The render thread finished a picture: CPU spent clearing and drawing it."""
        with self._lock:
            w = self._current
            w.pictures += 1
            w.clear_s += clear_s
            w.draw_s += draw_s

    def painted(self, cpu_s: float) -> None:
        """The window repainted the HUD (pasting the picture on screen) at this CPU cost."""
        with self._lock:
            self._current.paints += 1
            self._current.paint_s += cpu_s

    def video_frame(self) -> None:
        """A video frame reached the display sink; the next refresh shows it."""
        with self._lock:
            self._current.video_frames += 1
            self._video_waiting += 1

    def refresh(self, shown: bool) -> None:
        """A display refresh; ``shown`` when it put a new picture on screen."""
        with self._lock:
            w = self._current
            w.refreshes += 1
            self._since_picture = None if self._since_picture is None else self._since_picture + 1
            self._since_video = None if self._since_video is None else self._since_video + 1
            if shown:
                w.shown += 1
                if self._since_picture is not None:
                    w.held[self._since_picture] = w.held.get(self._since_picture, 0) + 1
                self._since_picture = 0
            if self._video_waiting:
                # Frames that arrived since the last refresh; only the newest reaches the screen.
                w.video_replaced += self._video_waiting - 1
                self._video_waiting = 0
                if self._since_video is not None:
                    w.video_held[self._since_video] = w.video_held.get(self._since_video, 0) + 1
                self._since_video = 0

    def summary(self, now: float) -> dict[str, Any] | None:
        """The last closed window, closing the current one once it is ``window_s`` old; None before the first."""
        with self._lock:
            w = self._current
            span = now - w.start
            if span >= self._window_s:
                self._last = {
                    "window_s": span,
                    "pictures_per_s": w.shown / span,
                    "picture_ms": _ms(w.clear_s + w.draw_s, w.pictures),
                    "draw_ms": _ms(w.draw_s, w.pictures),
                    "clear_ms": _ms(w.clear_s, w.pictures),
                    "paint_ms": _ms(w.paint_s, w.paints),
                    "repaints_per_s": w.paints / span,
                    "refreshes_per_s": w.refreshes / span,
                    "picture_refreshes": _histogram(w.held),
                    "video_frames_per_s": w.video_frames / span,
                    "video_refreshes": _histogram(w.video_held),
                    "video_replaced": w.video_replaced,
                }
                self._current = _Window(now)
            return None if self._last is None else dict(self._last)
