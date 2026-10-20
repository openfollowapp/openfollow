# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Cairo draw pass for paths on the video overlay."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import cairo
import numpy as np
import numpy.typing as npt

from openfollow.runtime.overlay_draw_scene import _stroke_polyline, project
from openfollow.runtime.overlay_draw_style import (
    COLOR_PATH,
    COLOR_PATH_LOCKED,
    PATH_ARROW_LENGTH,
    PATH_ARROW_WIDTH,
    PATH_LINE_WIDTH,
    PATH_LOCKED_LINE_WIDTH,
    PATH_POINT_RADIUS,
)
from openfollow.runtime.overlay_layers import keep
from openfollow.runtime.overlay_state import OverlayState


@dataclass(frozen=True)
class _Path:
    line: npt.NDArray[np.float64]
    # Each end on screen and a point just inside the path from it; NaN behind the camera.
    ends: list[tuple[float, float, float, float, bool]]
    locked: bool


def draw_paths(renderer: Any, cr: Any, state: OverlayState, w: int, h: int, cache: PathCache | None = None) -> None:
    """Each path as a line through its curve, white while a marker is on it.

    An end that holds the marker carries a dot; one that releases it, an arrowhead pointing out.
    """
    if not state.show_paths or not state.path_lines or state.camera_params is None:
        if cache is not None:
            cache.clear()
        return
    if cache is None:
        shapes = _path_shapes(state, w, h, state.path_lines)
        _paint_paths(cr, shapes, locked=False)
        _paint_paths(cr, [shape for shape in shapes if shape.locked], locked=True)
    else:
        cache.draw(cr, state, w, h)
    _paint_switch_arrows(cr, state, w, h)


def _paint_switch_arrows(cr: Any, state: OverlayState, w: int, h: int) -> None:
    """A small white arrow along each way a locked marker can turn into; drawn every frame, as markers move."""
    if not state.path_arrows:
        return
    cr.set_source_rgba(*COLOR_PATH_LOCKED)
    cr.set_line_width(PATH_LINE_WIDTH)
    for tail, tip in state.path_arrows:
        (tx, ty), (px, py) = project(state.camera_params, [tail, tip], w, h, state.lens_k1, state.lens_k2)
        length = math.hypot(px - tx, py - ty)
        if not (math.isfinite(length) and length > 0.0):
            continue
        cr.move_to(tx, ty)
        cr.line_to(px, py)
        cr.stroke()
        _arrowhead(cr, px, py, (px - tx) / length, (py - ty) / length)
        cr.fill()


def _path_shapes(state: OverlayState, w: int, h: int, lines: list[Any]) -> list[_Path]:
    cam = state.camera_params
    shapes = []
    for curve, ends, locked in lines:
        # The curve is drawn finely enough that projecting its points bows it with the lens.
        line = project(cam, curve, w, h, state.lens_k1, state.lens_k2)
        on_screen = []
        for at, inside, releases in ends:
            (ex, ey), (ix, iy) = project(cam, [at, inside], w, h, state.lens_k1, state.lens_k2)
            on_screen.append((float(ex), float(ey), float(ix), float(iy), releases))
        shapes.append(_Path(line, on_screen, locked))
    return shapes


def _paint_paths(cr: Any, shapes: list[_Path], *, locked: bool) -> None:
    """Every path in one style: a path with a marker on it is drawn grey, then white over it."""
    for shape in shapes:
        cr.set_source_rgba(*(COLOR_PATH_LOCKED if locked else COLOR_PATH))
        if _stroke_polyline(cr, shape.line):
            cr.set_line_width(PATH_LOCKED_LINE_WIDTH if locked else PATH_LINE_WIDTH)
            cr.stroke()
        for ex, ey, ix, iy, releases in shape.ends:
            if not all(math.isfinite(v) for v in (ex, ey)):
                continue
            length = math.hypot(ex - ix, ey - iy)
            if releases and math.isfinite(length) and length > 0.0:
                _arrowhead(cr, ex, ey, (ex - ix) / length, (ey - iy) / length)
            else:
                cr.arc(ex, ey, PATH_POINT_RADIUS, 0.0, 2.0 * math.pi)
            cr.fill()


def _arrowhead(cr: Any, x: float, y: float, ux: float, uy: float) -> None:
    """A triangle centred on ``(x, y)``, its tip pointing along ``(ux, uy)``."""
    half = PATH_ARROW_LENGTH / 2.0
    side = PATH_ARROW_WIDTH / 2.0
    cr.move_to(x + ux * half, y + uy * half)
    cr.line_to(x - ux * half - uy * side, y - uy * half + ux * side)
    cr.line_to(x - ux * half + uy * side, y - uy * half - ux * side)
    cr.close_path()


class PathCache:
    """Every path as one picture, and the paths with a marker on them as a second picture over it.

    The Operator Screen draws a picture at most once per display refresh. The paths change only with
    the paths or the view; which ones carry a marker changes whenever a marker crosses
    a junction, and then only the second, smaller picture is drawn again.
    """

    def __init__(self) -> None:
        self._layers: dict[bool, tuple[tuple[Any, ...], cairo.ImageSurface | None, int, int]] = {}

    def clear(self) -> None:
        """Let go of both pictures, which can run to a hundred megabytes each on a HiDPI 4K screen."""
        self._layers.clear()

    def draw(self, cr: Any, state: OverlayState, w: int, h: int) -> None:
        cam = state.camera_params
        if cam is None:
            return
        view = (tuple(float(v) for v in cam), state.lens_k1, state.lens_k2, w, h, cr.get_target().get_device_scale())
        self._paste(cr, state, w, h, view, state.path_lines, locked=False)
        on = [line for line in state.path_lines if line[2]]
        if on:
            self._paste(cr, state, w, h, view, on, locked=True)
        else:
            self._layers.pop(True, None)

    def _paste(
        self, cr: Any, state: OverlayState, w: int, h: int, view: tuple[Any, ...], lines: list[Any], *, locked: bool
    ) -> None:
        # A path's curve is the engine's own list until the paths reload, so comparing it is cheap.
        key = (tuple((curve, tuple(ends)) for curve, ends, _ in lines), *view)
        entry = self._layers.get(locked)
        if entry is None or entry[0] != key:
            entry = (key, *_render(state, lines, w, h, view[-1], locked=locked))
            self._layers[locked] = entry
        _, picture, x0, y0 = entry
        if picture is not None and not keep(cr, picture, x0, y0):
            cr.set_source_surface(picture, x0, y0)
            cr.paint()


def _render(
    state: OverlayState, lines: list[Any], w: int, h: int, scale: tuple[float, float], *, locked: bool
) -> tuple[cairo.ImageSurface | None, int, int]:
    shapes = _path_shapes(state, w, h, lines)
    points = [shape.line for shape in shapes] + [np.array([(ex, ey)]) for shape in shapes for ex, ey, *_ in shape.ends]
    bounds = np.vstack(points) if points else np.empty((0, 2))
    bounds = bounds[np.all(np.isfinite(bounds), axis=1)]
    if len(bounds) == 0:
        return None, 0, 0
    # An arrowhead reaches its length past the end; one more pixel for anti-aliasing.
    pad = int(math.ceil(max(PATH_ARROW_LENGTH, PATH_POINT_RADIUS, PATH_LOCKED_LINE_WIDTH))) + 1
    x0 = max(0, int(math.floor(bounds[:, 0].min())) - pad)
    y0 = max(0, int(math.floor(bounds[:, 1].min())) - pad)
    x1 = min(w, int(math.ceil(bounds[:, 0].max())) + pad)
    y1 = min(h, int(math.ceil(bounds[:, 1].max())) + pad)
    if x1 <= x0 or y1 <= y0:
        return None, 0, 0
    sx, sy = scale
    # Drawn at the target's device scale, so a HiDPI screen gets sharp paths.
    picture = cairo.ImageSurface(cairo.FORMAT_ARGB32, math.ceil((x1 - x0) * sx), math.ceil((y1 - y0) * sy))
    picture.set_device_scale(sx, sy)
    ctx = cairo.Context(picture)
    ctx.translate(-x0, -y0)
    _paint_paths(ctx, shapes, locked=locked)
    return picture, x0, y0
