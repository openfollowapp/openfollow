# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Camera round-trip latency: flash the Operator Screen black and white, time each change in the decoded video.

Three threads touch a run. ``feed`` is called from the display sink's streaming thread and only
appends to a locked queue; ``step`` runs on the frame loop and owns the state machine; ``presented``
comes from the display's draw (GTK main thread). Every time is on one monotonic clock in µs
(``monotonic_us``): the refresh that put a level on screen and the arrival of each decoded frame.
"""

from __future__ import annotations

import random
import statistics
import threading
import time
from collections import deque
from collections.abc import Callable, Hashable
from dataclasses import dataclass
from enum import Enum
from typing import Any

BLACK = 0.0
WHITE = 1.0
SETTLE_US = 1_000_000
HOLD_US = 150_000
GAP_US = (300_000, 900_000)
FLASHES = 5
SAMPLES = 2 * FLASHES
TIMEOUT_US = 3_000_000
# An edge is a jump against the darkest (rise) or brightest (fall) of this many earlier frames.
HISTORY = 3
FIRST_THRESHOLD = 0.07
CONTRAST_SHARE = 0.35
MIN_THRESHOLD = 0.04
SUCCESS_BELOW_MS = 120.0
CAUTION_BELOW_MS = 250.0
# Frames queued between two frame-loop steps; a stalled loop drops the oldest.
_QUEUE = 512
# The Media Gallery's input id: it plays stored pictures, not a camera.
MEDIA_GALLERY = "testpattern"


def monotonic_us() -> int:
    """GLib's monotonic clock in µs, the clock GTK's frame clock runs on."""
    try:
        from gi.repository import GLib

        return int(GLib.get_monotonic_time())
    except ImportError:
        return time.monotonic_ns() // 1000


class Phase(Enum):
    IDLE = "idle"
    SETTLE = "settle"
    GAP = "gap"
    WAIT_RISE = "wait_rise"
    HOLD = "hold"
    WAIT_FALL = "wait_fall"
    DONE = "done"


class FailureReason(Enum):
    NO_RISE = "no_rise"
    NO_FALL = "no_fall"
    SOURCE_CHANGED = "source_changed"
    NO_VIDEO = "no_video"
    NO_DISPLAY = "no_display"
    MEDIA_GALLERY = "media_gallery"
    UNSUPPORTED_FORMAT = "unsupported_format"


# What the station observed, and one next step.
_FAILURE_TEXT: dict[FailureReason, tuple[str, str]] = {
    FailureReason.NO_RISE: (
        "No flash seen in the video within 3 s.",
        "Point the camera at the Operator Screen.",
    ),
    FailureReason.NO_FALL: (
        "The video did not go dark again within 3 s of the flash.",
        "Point the camera at the Operator Screen only.",
    ),
    FailureReason.SOURCE_CHANGED: (
        "The video was rebuilt or changed during the measurement.",
        "Measure again once the video shows.",
    ),
    FailureReason.NO_VIDEO: ("No video is arriving.", "Measure when the camera's picture is on the Operator Screen."),
    FailureReason.NO_DISPLAY: (
        "No Operator Screen is showing on this station.",
        "Connect a screen to the station to measure.",
    ),
    FailureReason.MEDIA_GALLERY: ("The Media Gallery plays stored pictures, not a camera.", "Choose a camera source."),
    FailureReason.UNSUPPORTED_FORMAT: (
        "The station cannot read the brightness of this video format.",
        "Export a diagnostics file for support.",
    ),
}


def result_level(median_ms: float) -> str:
    """The status level of a round trip: success, caution or error."""
    if median_ms < SUCCESS_BELOW_MS:
        return "success"
    if median_ms < CAUTION_BELOW_MS:
        return "caution"
    return "error"


@dataclass(frozen=True)
class LatencyResult:
    median_ms: float
    min_ms: float
    max_ms: float
    samples: int
    # Wall-clock time for people, monotonic µs for the age.
    finished_at: float
    finished_us: int
    source_type: str

    @property
    def level(self) -> str:
        return result_level(self.median_ms)

    def to_dict(self, now_us: int | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {
            "median_ms": round(self.median_ms, 1),
            "min_ms": round(self.min_ms, 1),
            "max_ms": round(self.max_ms, 1),
            "samples": self.samples,
            "finished_at": self.finished_at,
            "source_type": self.source_type,
            "level": self.level,
        }
        if now_us is not None:
            out["age_s"] = max(0.0, (now_us - self.finished_us) / 1e6)
        return out


@dataclass(frozen=True)
class LatencyFailure:
    reason: FailureReason

    @property
    def text(self) -> str:
        return _FAILURE_TEXT[self.reason][0]

    @property
    def action(self) -> str:
        return _FAILURE_TEXT[self.reason][1]

    def to_dict(self) -> dict[str, str]:
        return {"reason": self.reason.value, "text": self.text, "action": self.action}


class LatencyProbe:
    """One measurement run at a time; idle, it asks for no screen level and the receiver hands it no frames."""

    def __init__(
        self,
        *,
        wall_clock: Callable[[], float] = time.time,
        rng: random.Random | None = None,
    ) -> None:
        self._wall_clock = wall_clock
        self._rng = rng or random.Random()  # nosec B311 - flash timing, not security
        self._lock = threading.Lock()
        self._frames: deque[tuple[int, float | None]] = deque(maxlen=_QUEUE)
        self._phase = Phase.IDLE
        self._level = BLACK
        self._feed: Hashable = None
        self._source_type = ""
        self._requested_us = 0
        self._shown_us: int | None = None
        self._until_us = 0
        self._history: deque[float] = deque(maxlen=HISTORY)
        self._threshold = FIRST_THRESHOLD
        self._baseline = 0.0
        self._peak = 0.0
        self._delays_us: list[int] = []
        self._flashes = 0
        self.result: LatencyResult | None = None
        self.failure: LatencyFailure | None = None
        self.last_success: LatencyResult | None = None

    @property
    def active(self) -> bool:
        return self._phase not in (Phase.IDLE, Phase.DONE)

    @property
    def phase(self) -> Phase:
        return self._phase

    def screen_level(self) -> float | None:
        """The level the Operator Screen must show over video and HUD, None while idle."""
        return self._level if self.active else None

    def start(self, now_us: int, feed: Hashable, source_type: str) -> None:
        """Start a run on the feed ``feed`` names; None means no video is decoded."""
        if feed is None:
            self.refuse(FailureReason.NO_VIDEO)
            return
        self.result = None
        self.failure = None
        self._delays_us = []
        with self._lock:
            self._frames.clear()
        self._phase = Phase.SETTLE
        self._feed = feed
        self._source_type = source_type
        self._until_us = now_us + SETTLE_US
        self._request(BLACK, now_us)
        self._history.clear()
        self._threshold = FIRST_THRESHOLD
        self._flashes = 0

    def refuse(self, reason: FailureReason) -> None:
        """End before starting, for a reason the caller found."""
        if not self.active:
            self.result = None
            self._delays_us = []
            self._fail(reason)

    def feed(self, arrival_us: int, mean_luma: float | None) -> None:
        """Streaming thread: a decoded frame's arrival and mean luma (0 to 1), None when unreadable."""
        with self._lock:
            self._frames.append((arrival_us, mean_luma))

    def presented(self, level: float, refresh_us: int) -> None:
        """The display put ``level`` on screen at the refresh at ``refresh_us``; the first report counts.

        A vsync-aligned frame time can precede a request made in the same refresh, whose render drew it:
        the edge is then timed from the request.
        """
        if self.active and self._shown_us is None and level == self._level:
            self._shown_us = max(refresh_us, self._requested_us)

    def step(self, now_us: int, feed: Hashable) -> None:
        """Frame loop: take the frames that arrived, look for the edge, and move the run on."""
        if not self.active:
            return
        if feed != self._feed:
            self._fail(FailureReason.SOURCE_CHANGED)
            return
        with self._lock:
            frames = list(self._frames)
            self._frames.clear()
        for arrival_us, luma in frames:
            if luma is None:
                self._fail(FailureReason.UNSUPPORTED_FORMAT)
                return
            self._observe(arrival_us, luma)
            if not self.active:
                return
        self._advance(now_us)

    def status(self, now_us: int | None = None) -> dict[str, Any]:
        """The run for the web UI: ``state`` idle, running, done or failed, with the progress or the outcome."""
        if self.active:
            state = "running"
        elif self.failure is not None:
            state = "failed"
        elif self.result is not None:
            state = "done"
        else:
            state = "idle"
        return {
            "state": state,
            "measured": len(self._delays_us),
            "total": SAMPLES,
            "result": self.result.to_dict(now_us) if self.result is not None else None,
            "failure": self.failure.to_dict() if self.failure is not None else None,
        }

    # -- internals (frame loop) ------------------------------------------

    def _request(self, level: float, now_us: int) -> None:
        self._level = level
        self._requested_us = now_us
        self._shown_us = None

    def _observe(self, arrival_us: int, luma: float) -> None:
        history = self._history
        seen = self._shown_us is not None and arrival_us > self._shown_us and len(history) > 0
        if self._phase is Phase.WAIT_RISE and seen and luma - min(history) >= self._threshold:
            self._baseline = min(history)
            self._peak = luma
            self._edge(arrival_us)
            self._phase = Phase.HOLD
            self._until_us = arrival_us + HOLD_US
        elif self._phase is Phase.WAIT_FALL and seen and max(history) - luma >= self._threshold:
            self._edge(arrival_us)
            self._flashes += 1
            if self._flashes >= FLASHES:
                self._finish(arrival_us)
                return
            self._phase = Phase.GAP
            self._until_us = arrival_us + self._rng.randint(*GAP_US)
        elif self._phase is Phase.HOLD:
            self._peak = max(self._peak, luma)
        history.append(luma)

    def _edge(self, arrival_us: int) -> None:
        assert self._shown_us is not None
        self._delays_us.append(arrival_us - self._shown_us)

    def _advance(self, now_us: int) -> None:
        phase = self._phase
        if phase in (Phase.SETTLE, Phase.GAP) and now_us >= self._until_us:
            self._request(WHITE, now_us)
            self._phase = Phase.WAIT_RISE
        elif phase is Phase.HOLD and now_us >= self._until_us:
            if self._flashes == 0:
                self._threshold = max(MIN_THRESHOLD, CONTRAST_SHARE * (self._peak - self._baseline))
            self._request(BLACK, now_us)
            self._phase = Phase.WAIT_FALL
        elif phase is Phase.WAIT_RISE and now_us - self._requested_us >= TIMEOUT_US:
            self._fail(FailureReason.NO_RISE)
        elif phase is Phase.WAIT_FALL and now_us - self._requested_us >= TIMEOUT_US:
            self._fail(FailureReason.NO_FALL)

    def _finish(self, arrival_us: int) -> None:
        delays_ms = [d / 1000.0 for d in self._delays_us]
        self.result = LatencyResult(
            median_ms=statistics.median(delays_ms),
            min_ms=min(delays_ms),
            max_ms=max(delays_ms),
            samples=len(delays_ms),
            finished_at=self._wall_clock(),
            finished_us=arrival_us,
            source_type=self._source_type,
        )
        self.last_success = self.result
        self._end()

    def _fail(self, reason: FailureReason) -> None:
        self.failure = LatencyFailure(reason)
        self._end()

    def _end(self) -> None:
        self._phase = Phase.DONE
        with self._lock:
            self._frames.clear()
