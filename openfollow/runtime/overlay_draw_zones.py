# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Cairo draw pass for trigger-zone polygons on the video overlay."""

from __future__ import annotations

from typing import Any

import numpy as np

from openfollow.runtime.overlay_draw_scene import _DISTORTION_SUBDIVISIONS, _project_segments, project
from openfollow.runtime.overlay_draw_style import parse_hex
from openfollow.runtime.overlay_state import OverlayState


def draw_zones(renderer: Any, cr: Any, state: OverlayState, w: int, h: int) -> None:
    """Render all configured zones as filled+stroked polygons with labels."""
    if not state.show_zones or not state.zone_polygons:
        return
    cam = state.camera_params
    if cam is None:
        return

    z = state.zone_z_offset

    for vertices_xy, color_hex, name, is_occupied, count in state.zone_polygons:
        if len(vertices_xy) < 3:
            continue

        pts = [(vx, vy, z) for vx, vy in vertices_xy]
        scr = project(cam, pts, w, h, state.lens_k1, state.lens_k2)
        # A zone is one closed path: dropping individual non-finite vertices
        # (e.g. a corner behind the camera) would reconnect non-adjacent
        # neighbours and warp the outline. Skip the whole zone instead.
        if not np.all(np.isfinite(scr)):
            continue

        # Under lens distortion each straight edge bows, so build the outline
        # from subdivided edges; otherwise the vertex projection is the outline.
        n = _DISTORTION_SUBDIVISIONS if (state.lens_k1 or state.lens_k2) else 1
        if n == 1:
            outline = scr
        else:
            loop = pts + [pts[0]]
            edges = [(loop[i], loop[i + 1]) for i in range(len(pts))]
            polys = _project_segments(cam, edges, w, h, state.lens_k1, state.lens_k2, n)
            # Drop each edge's last point – it repeats the next edge's first.
            outline = np.vstack([poly[:-1] for poly in polys])
            if not np.all(np.isfinite(outline)):
                continue

        r, g, b = parse_hex(color_hex)
        fill_alpha = 0.35 if is_occupied else 0.15
        stroke_alpha = 1.0 if is_occupied else 0.7

        cr.set_source_rgba(r, g, b, fill_alpha)
        cr.move_to(outline[0, 0], outline[0, 1])
        for i in range(1, len(outline)):
            cr.line_to(outline[i, 0], outline[i, 1])
        cr.close_path()
        cr.fill_preserve()
        cr.set_source_rgba(r, g, b, stroke_alpha)
        cr.set_line_width(2.0 if is_occupied else 1.5)
        cr.stroke()

        # Label at polygon centroid
        if name:
            cx = float(np.mean(scr[:, 0]))
            cy = float(np.mean(scr[:, 1]))
            label = f"{name} ({count})" if is_occupied else name
            renderer._set_ui_font(cr, 13, bold=is_occupied)
            ext = cr.text_extents(label)
            pad = 4.0
            rect_w = ext.width + pad * 2
            rect_h = ext.height + pad * 2
            rect_x = cx - rect_w / 2.0
            rect_y = cy - rect_h / 2.0
            cr.set_source_rgba(0.0, 0.0, 0.0, 0.55)
            cr.rectangle(rect_x, rect_y, rect_w, rect_h)
            cr.fill()
            cr.set_source_rgba(r, g, b, 1.0)
            cr.move_to(rect_x + pad, rect_y + pad + ext.height)
            cr.show_text(label)
