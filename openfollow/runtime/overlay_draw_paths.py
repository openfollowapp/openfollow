# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Cairo draw pass for paths on the video overlay."""

from __future__ import annotations

import math
from typing import Any

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


def draw_paths(renderer: Any, cr: Any, state: OverlayState, w: int, h: int) -> None:
    """Each path as a line through its curve, white while a marker is on it.

    An end that holds the marker carries a dot; one that releases it, an arrowhead pointing out.
    """
    cam = state.camera_params
    if not state.show_paths or cam is None:
        return
    for curve, ends, locked in state.path_lines:
        cr.set_source_rgba(*(COLOR_PATH_LOCKED if locked else COLOR_PATH))
        # The curve is drawn finely enough that projecting its points bows it with the lens.
        if _stroke_polyline(cr, project(cam, curve, w, h, state.lens_k1, state.lens_k2)):
            cr.set_line_width(PATH_LOCKED_LINE_WIDTH if locked else PATH_LINE_WIDTH)
            cr.stroke()
        for at, inside, releases in ends:
            (ex, ey), (ix, iy) = project(cam, [at, inside], w, h, state.lens_k1, state.lens_k2)
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
