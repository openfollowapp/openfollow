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
from openfollow.runtime.latency_probe import monotonic_us
from openfollow.runtime.overlay_layers import LayerContext, Layers
from openfollow.runtime.overlay_state import OverlayState
from openfollow.video.hud_metrics import HudMetrics

if TYPE_CHECKING:
    from openfollow.video.overlay import CairoOverlayRenderer

logger = logging.getLogger(__name__)

# Canvas size in units and its device scale.
_Canvas = tuple[int, int, tuple[float, float]]

# A run of pictures spanning this many refreshes, each within this share of a shorter interval, takes the least such.
_STEADY_REFRESHES = 120
_STEP_DOWN_SHARE = 0.7
# This many misses among the latest max(this many, ceil(_MISS_REFRESHES / interval)) pictures lengthen the interval.
_MISSES_TO_LENGTHEN = 3
_MISS_REFRESHES = 60
_PERIOD_SAMPLES = 16
# Text on a layered canvas is laid out as on the canvas-sized picture it replaces.
_IMAGE_FONT_OPTIONS = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1).get_font_options()


class _Pacing:
    """How many refreshes every picture is held for, so each reaches the screen equally late."""

    refreshes: int
    _floor: int
    _took: deque[float]
    _held: deque[int]

    def __init__(self) -> None:
        self._ticks: deque[float] = deque(maxlen=_PERIOD_SAMPLES + 1)
        self._floor = 1
        self._set(1)

    def refreshed(self, now: float) -> None:
        self._ticks.append(now)

    def shown(self, held: int, took_s: float) -> None:
        """A picture went on screen ``held`` refreshes after it started; it took ``took_s`` to finish."""
        self._held.append(held)
        self._took.append(took_s)
        missed = [h for h in self._held if h > self.refreshes]
        if len(missed) >= _MISSES_TO_LENGTHEN:
            # No step down back to the interval that failed until a steady window at the one above it.
            self._floor = self.refreshes + 1
            self._set(min(missed))
        elif self.refreshes > 1 and len(self._took) == self._took.maxlen:
            if self.refreshes == self._floor:
                self._floor = 1
            period = statistics.median(b - a for a, b in pairwise(self._ticks))
            longest = max(self._took)
            # Straight to the least that fits, so the long interval after a stall recovers in two windows.
            fits = (n for n in range(self._floor, self.refreshes) if longest <= _STEP_DOWN_SHARE * n * period)
            if (shorter := next(fits, None)) is not None:
                self._set(shorter)

    def _set(self, refreshes: int) -> None:
        self.refreshes = refreshes
        # Every picture is held at least ``refreshes`` refreshes, so this many span each window.
        self._took = deque(maxlen=math.ceil(_STEADY_REFRESHES / refreshes))
        self._held = deque(maxlen=max(_MISSES_TO_LENGTHEN, math.ceil(_MISS_REFRESHES / refreshes)))


class HudRenderThread:
    """Draws the HUD into a picture on a thread of its own; the GTK draw only pastes the shown one.

    The display refresh paces it (``tick``). At a refresh the thread, when idle,
    starts a picture of the state current at that refresh; the refresh a set number
    of refreshes later puts it on screen and asks for a redraw, so every position
    reaches the screen equally late, whatever else repaints the HUD in between. A
    lone picture that misses its refresh is shown late; repeated misses lengthen the
    interval to what they needed, and a run of pictures with room to spare shortens
    it to the least interval they all fit, kept above the interval that last failed
    until a steady run just above it. With no display there are no refreshes, and
    nothing is drawn.

    ``tick`` and ``paint`` run on the GTK main thread; only the render thread calls
    the renderer's ``draw``. Two pictures take turns: a new one starts only at a
    refresh, after the finished one went on screen, so it is drawn into the one
    shown before.
    """

    def __init__(
        self,
        renderer: CairoOverlayRenderer,
        clock: Callable[[], float] = time.monotonic,
        cpu_clock: Callable[[], float] = time.thread_time,
        *,
        layered: bool = False,
        refresh_us: Callable[[], int] = monotonic_us,
    ) -> None:
        self._renderer = renderer
        self._clock = clock
        # The latency probe's clock, in µs, for the refresh that shows a picture.
        self._refresh_us = refresh_us
        # Told on the Cairo Operator Screen when a picture carrying a latency flash goes on screen.
        self.on_flash_shown: Callable[[float, int], None] | None = None
        # CPU time of the calling thread, for what a picture and a repaint cost.
        self._cpu_clock = cpu_clock
        self.metrics = HudMetrics(clock())
        self._thread_id: int | None = None
        # Layered: each picture is a ``Layers`` list for a compositor instead of one canvas-sized picture.
        self._layered = layered
        self._cond = threading.Condition()
        # The canvas the last paint showed; the picture asked for; asked for or being drawn.
        self._target: _Canvas | None = None
        self._job: tuple[_Canvas, OverlayState] | None = None
        self._busy = False
        # Finished and waiting for the refresh that shows it; on screen; free to draw into.
        self._ready: cairo.ImageSurface | Layers | None = None
        # The latency flash the ready picture carries.
        self._ready_flash: float | None = None
        # Only the GTK main thread (tick and paint) touches the shown picture.
        self._shown: cairo.ImageSurface | Layers | None = None
        # Counts pictures put on screen, so a reader can tell a new one in a reused surface.
        self._shown_serial = 0
        self._spare: cairo.ImageSurface | Layers | None = None
        # Pictures shown and those shown after their refresh, since the last pacing report.
        self._report = [0, 0]
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
            flash = None
            if ready is not None:
                # The GL display reports its own flash, drawn over everything at its render.
                flash = None if self._layered else self._ready_flash
                self._ready = None
                self._spare, self._shown = self._shown, ready
                self._shown_serial += 1
                self._report[0] += 1
                self._report[1] += self._held > self._pacing.refreshes
                self._pacing.shown(self._held, self._finished_at - self._started_at)
            self.metrics.refresh(ready is not None)
            if not self._busy and self._ready is None and self._target is not None:
                self._job = (self._target, self._renderer.state)
                self._busy = True
                self._held = 0
                self._started_at = now
                self._cond.notify()
        if flash is not None and (shown := self.on_flash_shown) is not None:
            shown(flash, self._refresh_us())
        return ready is not None

    def paint(self, cr: Any, width: int, height: int) -> None:
        """GTK draw callback: paste the shown picture, and remember the canvas for the next one."""
        start = self._cpu_clock()
        if isinstance(self._shown, cairo.ImageSurface):
            cr.set_source_surface(self._shown, 0, 0)
            cr.paint()
        self.metrics.painted(self._cpu_clock() - start)
        self.set_canvas(width, height, cr.get_target().get_device_scale())

    def set_canvas(self, width: int, height: int, scale: tuple[float, float]) -> None:
        """The canvas the next picture is drawn for."""
        with self._cond:
            self._target = (width, height, scale)

    def shown_picture(self) -> tuple[cairo.ImageSurface | Layers | None, int]:
        """The picture on screen and its serial; GTK main thread only, like ``tick``."""
        return self._shown, self._shown_serial

    def set_layered(self, layered: bool) -> None:
        """Draw the next pictures as ``Layers`` for a compositor, or as one canvas-sized picture to paste."""
        with self._cond:
            self._layered = layered

    def pacing_report(self) -> tuple[int, int, int]:
        """Pictures shown and how many of them late since the last report, and the refreshes each is held.

        GTK main thread only, like ``tick``.
        """
        shown, late = self._report
        self._report = [0, 0]
        return shown, late, self._pacing.refreshes

    def gl_rendered(self, cpu_s: float) -> None:
        """The GL display rendered a refresh (video and HUD uploads, compositing) at this GTK main thread CPU cost."""
        self.metrics.gl_rendered(cpu_s)

    def video_frame(self) -> None:
        """A video frame reached the display sink (its streaming thread)."""
        self.metrics.video_frame()

    def stats(self) -> dict[str, Any] | None:
        """``HudMetrics.summary`` with the pacing interval and the render thread's id; None before the first window."""
        summary = self.metrics.summary(self._clock(), self._pacing.refreshes)
        if summary is None:
            return None
        summary["render_thread_id"] = self._thread_id
        return summary

    def _run(self) -> None:
        self._thread_id = threading.get_native_id()
        while (taken := self._take()) is not None:
            (canvas, state), picture = taken
            try:
                with self._cond:
                    layered = self._layered
                ready: cairo.ImageSurface | Layers | None = (
                    self._render_layers(canvas, state) if layered else self._render(picture, canvas, state)
                )
            except Exception:
                self._err_log.log()
                ready = None
            with self._cond:
                self._busy = False
                self._ready = ready
                self._ready_flash = state.latency_flash
                self._finished_at = self._clock()

    def _take(self) -> tuple[tuple[_Canvas, OverlayState], cairo.ImageSurface | Layers | None] | None:
        """Wait for the picture asked for, with the spare to draw it into; None once stopped."""
        with self._cond:
            while not self._stopped:
                if (job := self._job) is not None:
                    self._job = None
                    return job, self._spare
                self._cond.wait()
            return None

    def _render_layers(self, canvas: _Canvas, state: OverlayState) -> Layers:
        width, height, scale = canvas
        start = self._cpu_clock()
        cr = LayerContext(width, height, scale, _IMAGE_FONT_OPTIONS)
        self._renderer.draw(cr, width, height, state)
        layers = cr.finish()
        # Layers start empty: nothing to clear.
        self.metrics.picture(None, self._cpu_clock() - start)
        return layers

    def _render(
        self, picture: cairo.ImageSurface | Layers | None, canvas: _Canvas, state: OverlayState
    ) -> cairo.ImageSurface:
        width, height, (sx, sy) = canvas
        size = (math.ceil(width * sx), math.ceil(height * sy))
        start = self._cpu_clock()
        if isinstance(picture, cairo.ImageSurface) and _fits(picture, size, (sx, sy)):
            clear = cairo.Context(picture)
            clear.set_operator(cairo.OPERATOR_CLEAR)
            clear.paint()
        else:
            # Drawn at the screen's device scale, so a HiDPI screen gets a sharp HUD.
            picture = cairo.ImageSurface(cairo.FORMAT_ARGB32, *size)
            picture.set_device_scale(sx, sy)
        drawing = self._cpu_clock()
        self._renderer.draw(cairo.Context(picture), width, height, state)
        self.metrics.picture(drawing - start, self._cpu_clock() - drawing)
        return picture


def _fits(picture: cairo.ImageSurface, size: tuple[int, int], scale: tuple[float, float]) -> bool:
    return (picture.get_width(), picture.get_height()) == size and picture.get_device_scale() == scale
