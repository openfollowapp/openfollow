# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The largest video a station is sized for, and the meter that holds a live feed against it.

One rule serves the Media Gallery's upload check and the live feed, so the two
cannot disagree: the longer side at most 1920, the shorter at most 1080, at
most 30 fps.
"""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from openfollow.video.failure import SourceKind

MAX_LONG_SIDE = 1920
MAX_SHORT_SIDE = 1080
MAX_FPS = 30
# Absorbs a rate a hair over the limit: a declared 30000/1001, or a measured 30 a frame's jitter high.
FPS_TOLERANCE = 0.5

# The meter publishes a reading once per slot; its rate spans the last WINDOW_SLOTS slots.
SLOT_S = 1.0
WINDOW_SLOTS = 5
# Readings in a row that must disagree with the frame-rate verdict before it changes.
CONFIRM_READINGS = 3

_REMOTE_ACTION = f"Set the camera or encoder to {MAX_LONG_SIDE}×{MAX_SHORT_SIDE} at {MAX_FPS} fps or less."
# A local camera is read at the size and rate the station's own settings ask for.
_LOCAL_ACTION = (
    f"Set the resolution to {MAX_LONG_SIDE}×{MAX_SHORT_SIDE} or less and FPS to {MAX_FPS} or less under Video Source."
)


def limits_action(kind: SourceKind) -> str:
    """The one next step for a source over the limits, in the words its kind's settings use."""
    return _LOCAL_ACTION if kind is SourceKind.LOCAL else _REMOTE_ACTION


class OverLimit(str, Enum):
    """What a source exceeds. The values are a wire interface: ``/api/stats`` and the diagnostics bundle."""

    RESOLUTION = "resolution_over_limit"
    FRAME_RATE = "frame_rate_over_limit"


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number, not {type(value).__name__}")
    return float(value)


def over_limits(width: float, height: float, fps: float) -> tuple[OverLimit, ...]:
    """What ``width`` x ``height`` at ``fps`` exceeds, in either orientation; empty when within.

    A zero, negative or NaN value is unknown and exceeds nothing: a variable-rate
    clip declares 0 fps, and a feed not yet measured has no rate.
    """
    w, h, rate = _number(width, "width"), _number(height, "height"), _number(fps, "fps")
    over: list[OverLimit] = []
    if w > 0 and h > 0 and (max(w, h) > MAX_LONG_SIDE or min(w, h) > MAX_SHORT_SIDE):
        over.append(OverLimit.RESOLUTION)
    if rate > MAX_FPS + FPS_TOLERANCE:
        over.append(OverLimit.FRAME_RATE)
    return tuple(over)


@dataclass(frozen=True)
class SourceReading:
    """What one attempt's arriving frames measured."""

    width: int
    height: int
    # The arrival rate behind the verdict, held while readings disagree with it; None until the window spans.
    fps: float | None
    over: tuple[OverLimit, ...]


class FrameMeter:
    """Counts one pipeline attempt's frames as they arrive, ahead of any queue that drops them.

    ``note_caps`` and ``note_frame`` run on the streaming thread that pushes into
    the metered pad, and only there; ``reading`` is replaced whole, so any
    thread may read it. Per frame: a counter, a clock read and a compare.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._width = 0
        self._height = 0
        self._frames = 0
        self._next_mark = -math.inf
        # (frames counted, arrival time of the frame that closed the slot)
        self._marks: deque[tuple[int, float]] = deque(maxlen=WINDOW_SLOTS + 1)
        self._rate_over = False
        self._rate_fps: float | None = None
        self._disagreeing = 0
        self.reading: SourceReading | None = None

    def note_caps(self, width: int, height: int) -> None:
        self._width = width
        self._height = height

    def note_frame(self) -> None:
        self._frames += 1
        now = self._clock()
        if now >= self._next_mark:
            self._close_slot(now)

    def _close_slot(self, now: float) -> None:
        self._next_mark = now + SLOT_S
        marks = self._marks
        marks.append((self._frames, now))
        if len(marks) == marks.maxlen:
            # Frame to frame, so the reading carries only the two end frames' jitter, never the slot timing.
            first_frames, first_at = marks[0]
            self._judge_rate((self._frames - first_frames) / (now - first_at))
        over = over_limits(self._width, self._height, 0.0)
        if self._rate_over:
            over += (OverLimit.FRAME_RATE,)
        self.reading = SourceReading(self._width, self._height, self._rate_fps, over)

    def _judge_rate(self, fps: float) -> None:
        if (OverLimit.FRAME_RATE in over_limits(0, 0, fps)) != self._rate_over:
            self._disagreeing += 1
            if self._disagreeing < CONFIRM_READINGS:
                return
            self._rate_over = not self._rate_over
        self._disagreeing = 0
        self._rate_fps = fps


def _causes(reading: SourceReading | None) -> list[tuple[str, str, str]]:
    """(observed, limit, limit spelled out) for each cause the reading is over."""
    if reading is None:
        return []
    causes = []
    if OverLimit.RESOLUTION in reading.over:
        causes.append((f"{reading.width}×{reading.height}", "Full HD", f"Full HD ({MAX_LONG_SIDE}×{MAX_SHORT_SIDE})"))
    if OverLimit.FRAME_RATE in reading.over and reading.fps is not None:
        causes.append((f"{reading.fps:.0f} fps", f"{MAX_FPS} fps", f"{MAX_FPS} fps"))
    return causes


def limits_row(reading: SourceReading | None) -> str:
    """The Operator Screen's status row, ``Video 3840×2160 > Full HD, 60 fps > 30 fps``; "" when within."""
    causes = _causes(reading)
    return "Video " + ", ".join(f"{seen} > {limit}" for seen, limit, _ in causes) if causes else ""


def limits_sentence(reading: SourceReading | None) -> str:
    """What the station observed beside the limit, for the web UI and the bundle; "" when within."""
    causes = _causes(reading)
    if not causes:
        return ""
    seen = " and ".join(cause[0] for cause in causes)
    limits = " and ".join(cause[2] for cause in causes)
    return f"Video arrives at {seen}, over {limits}."


def limits_snapshot(reading: SourceReading | None, kind: SourceKind = SourceKind.REMOTE) -> dict[str, Any]:
    """``/api/stats``'s ``video.limits``: the verdict as stable ids, what was measured, the limits, the box text."""
    exceeded = [cause.value for cause in reading.over] if reading is not None else []
    return {
        "exceeded": exceeded,
        "width": reading.width if reading is not None else 0,
        "height": reading.height if reading is not None else 0,
        "fps": reading.fps if reading is not None else None,
        "max_long_side": MAX_LONG_SIDE,
        "max_short_side": MAX_SHORT_SIDE,
        "max_fps": MAX_FPS,
        "text": limits_sentence(reading),
        "action": limits_action(kind) if exceeded else "",
    }
