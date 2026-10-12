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
    PATH_LINE_WIDTH,
    PATH_LOCKED_LINE_WIDTH,
    PATH_POINT_RADIUS,
)
from openfollow.runtime.overlay_state import OverlayState


def draw_paths(renderer: Any, cr: Any, state: OverlayState, w: int, h: int) -> None:
    """Each path as a line through its curve with a dot on every point, white while a marker is on it."""
    cam = state.camera_params
    if not state.show_paths or cam is None:
        return
    for curve, points, locked in state.path_lines:
        cr.set_source_rgba(*(COLOR_PATH_LOCKED if locked else COLOR_PATH))
        # The curve is drawn finely enough that projecting its points bows it with the lens.
        if _stroke_polyline(cr, project(cam, curve, w, h, state.lens_k1, state.lens_k2)):
            cr.set_line_width(PATH_LOCKED_LINE_WIDTH if locked else PATH_LINE_WIDTH)
            cr.stroke()
        for x, y in project(cam, points, w, h, state.lens_k1, state.lens_k2):
            if math.isfinite(x) and math.isfinite(y):
                cr.arc(x, y, PATH_POINT_RADIUS, 0.0, 2.0 * math.pi)
                cr.fill()
