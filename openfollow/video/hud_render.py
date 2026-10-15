# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The Operator Screen drawn on its own thread, so the frame loop never waits for a whole paint."""

from __future__ import annotations

import logging
import math
import statistics
import threading
import time
from collections import deque
from collections.abc import Callable
from itertools import pairwise
from typing import TYPE_CHECKING, Any

import cairo

from openfollow.logging_setup import ThrottledExceptionLogger
from openfollow.runtime.overlay_state import OverlayState

if TYPE_CHECKING:
    from openfollow.video.overlay import CairoOverlayRenderer

logger = logging.getLogger(__name__)

# Canvas size in units and its device scale.
_Canvas = tuple[int, int, tuple[float, float]]

# A run of pictures spanning this many refreshes, each within this share of a shorter interval, takes the least such.
_STEADY_REFRESHES = 120
_STEP_DOWN_SHARE = 0.7
# Misses within this many refreshes that lengthen the interval; fewer are shown late and change nothing.
_MISSES_TO_LENGTHEN = 3
_MISS_REFRESHES = 60
_PERIOD_SAMPLES = 16


class _Pacing:
    """How many refreshes every picture is held for, so each reaches the screen equally late."""

    refreshes: int
    _took: deque[float]
    _held: deque[int]

    def __init__(self) -> None:
        self._ticks: deque[float] = deque(maxlen=_PERIOD_SAMPLES + 1)
        self._set(1)

    def refreshed(self, now: float) -> None:
        self._ticks.append(now)

    def shown(self, held: int, took_s: float) -> None:
        """A picture went on screen ``held`` refreshes after it started; it took ``took_s`` to finish."""
        self._held.append(held)
        self._took.append(took_s)
        missed = [h for h in self._held if h > self.refreshes]
        if len(missed) >= _MISSES_TO_LENGTHEN:
            self._set(min(missed))
        elif self.refreshes > 1 and len(self._took) == self._took.maxlen:
            period = statistics.median(b - a for a, b in pairwise(self._ticks))
            longest = max(self._took)
            # Straight to the least that fits, so the long interval after a stall recovers in one window.
            fits = (n for n in range(1, self.refreshes) if longest <= _STEP_DOWN_SHARE * n * period)
            if (shorter := next(fits, None)) is not None:
                self._set(shorter)

    def _set(self, refreshes: int) -> None:
        self.refreshes = refreshes
        # Every picture is held at least ``refreshes`` refreshes, so this many span each window.
        self._took = deque(maxlen=math.ceil(_STEADY_REFRESHES / refreshes))
        self._held = deque(maxlen=math.ceil(_MISS_REFRESHES / refreshes))


class HudRenderThread:
    """Draws the HUD into a picture on a thread of its own; the GTK draw only pastes the shown one.

    The display refresh paces it (``tick``). At a refresh the thread, when idle,
    starts a picture of the state current at that refresh; the refresh a set number
    of refreshes later puts it on screen and asks for a redraw, so every position
    reaches the screen equally late, whatever else repaints the HUD in between. A
    lone picture that misses its refresh is shown late; repeated misses lengthen the
    interval to what they needed, and a run of pictures with room to spare shortens
    it to the least interval they all fit. With no display there are no refreshes,
    and nothing is drawn.

    ``tick`` and ``paint`` run on the GTK main thread; only the render thread calls
    the renderer's ``draw``. Two pictures take turns: a new one starts only at a
    refresh, after the finished one went on screen, so it is drawn into the one
    shown before.
    """

    def __init__(self, renderer: CairoOverlayRenderer, clock: Callable[[], float] = time.monotonic) -> None:
        self._renderer = renderer
        self._clock = clock
        self._cond = threading.Condition()
        # The canvas the last paint showed; the picture asked for; asked for or being drawn.
        self._target: _Canvas | None = None
        self._job: tuple[_Canvas, OverlayState] | None = None
        self._busy = False
        # Finished and waiting for the refresh that shows it; on screen; free to draw into.
        self._ready: cairo.ImageSurface | None = None
        # Only the GTK main thread (tick and paint) touches the shown picture.
        self._shown: cairo.ImageSurface | None = None
        self._spare: cairo.ImageSurface | None = None
        # Refreshes since the current picture started, when it started and when it finished.
        self._pacing = _Pacing()
        self._held = 0
        self._started_at = 0.0
        self._finished_at = 0.0
        self._stopped = False
        self._thread: threading.Thread | None = None
        self._err_log = ThrottledExceptionLogger(logger, "Unhandled exception in HUD render")

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="HudRender", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        with self._cond:
            self._stopped = True
            self._cond.notify()
        if self._thread is not None:
            self._thread.join(timeout)

    def tick(self) -> bool:
        """Display refresh: show a finished picture (True: redraw) and, when idle, start the next."""
        with self._cond:
            now = self._clock()
            self._pacing.refreshed(now)
            self._held += 1
            ready = self._ready if self._held >= self._pacing.refreshes else None
            if ready is not None:
                self._ready = None
                self._spare, self._shown = self._shown, ready
                self._pacing.shown(self._held, self._finished_at - self._started_at)
            if not self._busy and self._ready is None and self._target is not None:
                self._job = (self._target, self._renderer.state)
                self._busy = True
                self._held = 0
                self._started_at = now
                self._cond.notify()
        return ready is not None

    def paint(self, cr: Any, width: int, height: int) -> None:
        """GTK draw callback: paste the shown picture, and remember the canvas for the next one."""
        if self._shown is not None:
            cr.set_source_surface(self._shown, 0, 0)
            cr.paint()
        scale = cr.get_target().get_device_scale()
        with self._cond:
            self._target = (width, height, scale)

    def _run(self) -> None:
        while (taken := self._take()) is not None:
            (canvas, state), picture = taken
            try:
                ready: cairo.ImageSurface | None = self._render(picture, canvas, state)
            except Exception:
                self._err_log.log()
                ready = None
            with self._cond:
                self._busy = False
                self._ready = ready
                self._finished_at = self._clock()

    def _take(self) -> tuple[tuple[_Canvas, OverlayState], cairo.ImageSurface | None] | None:
        """Wait for the picture asked for, with the spare to draw it into; None once stopped."""
        with self._cond:
            while not self._stopped:
                if (job := self._job) is not None:
                    self._job = None
                    return job, self._spare
                self._cond.wait()
            return None

    def _render(self, picture: cairo.ImageSurface | None, canvas: _Canvas, state: OverlayState) -> cairo.ImageSurface:
        width, height, (sx, sy) = canvas
        size = (math.ceil(width * sx), math.ceil(height * sy))
        if picture is not None and _fits(picture, size, (sx, sy)):
            clear = cairo.Context(picture)
            clear.set_operator(cairo.OPERATOR_CLEAR)
            clear.paint()
        else:
            # Drawn at the screen's device scale, so a HiDPI screen gets a sharp HUD.
            picture = cairo.ImageSurface(cairo.FORMAT_ARGB32, *size)
            picture.set_device_scale(sx, sy)
        self._renderer.draw(cairo.Context(picture), width, height, state)
        return picture


def _fits(picture: cairo.ImageSurface, size: tuple[int, int], scale: tuple[float, float]) -> bool:
    return (picture.get_width(), picture.get_height()) == size and picture.get_device_scale() == scale
