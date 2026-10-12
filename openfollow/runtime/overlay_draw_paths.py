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
    if not state.show_paths or state.camera_params is None:
        return
    if cache is None:
        _paint_paths(cr, _path_shapes(state, w, h))
    else:
        cache.draw(cr, state, w, h)


def _path_shapes(state: OverlayState, w: int, h: int) -> list[_Path]:
    cam = state.camera_params
    shapes = []
    for curve, ends, locked in state.path_lines:
        # The curve is drawn finely enough that projecting its points bows it with the lens.
        line = project(cam, curve, w, h, state.lens_k1, state.lens_k2)
        on_screen = []
        for at, inside, releases in ends:
            (ex, ey), (ix, iy) = project(cam, [at, inside], w, h, state.lens_k1, state.lens_k2)
            on_screen.append((float(ex), float(ey), float(ix), float(iy), releases))
        shapes.append(_Path(line, on_screen, locked))
    return shapes


def _paint_paths(cr: Any, shapes: list[_Path]) -> None:
    for shape in shapes:
        cr.set_source_rgba(*(COLOR_PATH_LOCKED if shape.locked else COLOR_PATH))
        if _stroke_polyline(cr, shape.line):
            cr.set_line_width(PATH_LOCKED_LINE_WIDTH if shape.locked else PATH_LINE_WIDTH)
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
    """Every path as one picture, drawn again only when a path, a lock or the view changed.

    The Operator Screen redraws on every display refresh; paths that did not change
    are then one paint instead of projecting and stroking every curve.
    """

    def __init__(self) -> None:
        self._entry: tuple[tuple[Any, ...], cairo.ImageSurface | None, int, int] | None = None

    def draw(self, cr: Any, state: OverlayState, w: int, h: int) -> None:
        cam = state.camera_params
        if cam is None:
            return
        scale = cr.get_target().get_device_scale()
        # A path's curve is the engine's own list until the paths reload, so comparing it is cheap.
        key = (
            tuple((curve, tuple(ends), locked) for curve, ends, locked in state.path_lines),
            tuple(float(v) for v in cam),
            state.lens_k1,
            state.lens_k2,
            w,
            h,
            scale,
        )
        if self._entry is None or self._entry[0] != key:
            self._entry = (key, *self._render(state, w, h, scale))
        _, picture, x0, y0 = self._entry
        if picture is not None:
            cr.set_source_surface(picture, x0, y0)
            cr.paint()

    @staticmethod
    def _render(
        state: OverlayState, w: int, h: int, scale: tuple[float, float]
    ) -> tuple[cairo.ImageSurface | None, int, int]:
        shapes = _path_shapes(state, w, h)
        points = [shape.line for shape in shapes] + [
            np.array([(ex, ey)]) for shape in shapes for ex, ey, *_ in shape.ends
        ]
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
        _paint_paths(ctx, shapes)
        return picture, x0, y0
