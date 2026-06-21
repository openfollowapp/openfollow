# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""3D overlay draw passes (grid/origin/markers/detections)."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import numpy.typing as npt

from openfollow.runtime.overlay_draw_style import parse_hex
from openfollow.runtime.overlay_state import MarkerOverlayData, OverlayState
from openfollow.scene.solver import project_points

# Cap grid lines per axis. A degenerate width/depth ÷ spacing (e.g. from a
# hand-edited config.toml or peer broadcast – neither has an upper clamp) would
# otherwise allocate a multi-MB point buffer and issue hundreds of thousands of
# Cairo calls per frame, freezing the overlay.
_MAX_GRID_LINES_PER_AXIS = 200


def project(
    cam: npt.NDArray[Any] | None,
    pts_psn: list[Any] | npt.NDArray[Any],
    w: int,
    h: int,
) -> npt.NDArray[Any]:
    # Return NaN when camera missing; downstream np.isfinite filters handle it.
    if cam is None:  # pragma: no cover
        return np.full((np.asarray(pts_psn, dtype=np.float64).reshape(-1, 3).shape[0], 2), np.nan)
    arr = np.asarray(pts_psn, dtype=np.float64).reshape(-1, 3)
    return project_points(cam, arr, float(w), float(h))


def draw_grid(renderer: Any, cr: Any, state: OverlayState, w: int, h: int) -> None:
    gc = state.grid_config
    if gc is None:
        return
    gw, gd, gs, x_off, y_off, z_off = gc
    if gs <= 0:
        return
    cam = state.camera_params
    hw, hd = gw / 2.0, gd / 2.0

    r, g, b = parse_hex(state.grid_color)
    cr.set_source_rgba(r, g, b, state.grid_transparency)
    cr.set_line_width(float(state.grid_thickness))

    n_x = min(max(int(gw / gs) + 1, 2), _MAX_GRID_LINES_PER_AXIS)
    n_z = min(max(int(gd / gs) + 1, 2), _MAX_GRID_LINES_PER_AXIS)
    n_pts = (n_x + n_z) * 2

    buf = renderer._grid_pts_buf
    if n_pts > buf.shape[0]:
        buf = np.zeros((n_pts, 3), dtype=np.float64)
        renderer._grid_pts_buf = buf

    idx = 0
    for y in np.linspace(-hd + y_off, hd + y_off, n_z):
        buf[idx] = (-hw + x_off, y, z_off)
        buf[idx + 1] = (hw + x_off, y, z_off)
        idx += 2
    for x in np.linspace(-hw + x_off, hw + x_off, n_x):
        buf[idx] = (x, -hd + y_off, z_off)
        buf[idx + 1] = (x, hd + y_off, z_off)
        idx += 2

    scr = project(cam, buf[:idx], w, h)
    for i in range(0, len(scr), 2):
        p0, p1 = scr[i], scr[i + 1]
        if np.all(np.isfinite(p0)) and np.all(np.isfinite(p1)):
            cr.move_to(p0[0], p0[1])
            cr.line_to(p1[0], p1[1])
    cr.stroke()


def draw_origin(cr: Any, state: OverlayState, w: int, h: int) -> None:
    if not state.show_origin:
        return
    cam = state.camera_params
    length = state.origin_length
    thickness = state.origin_thickness

    axes = [
        ((0, 0, 0), (length, 0, 0), (1.0, 0.0, 0.0)),
        ((0, 0, 0), (0, length, 0), (0.0, 1.0, 0.0)),
        ((0, 0, 0), (0, 0, length), (0.0, 0.4, 1.0)),
    ]

    cr.set_line_width(thickness)
    for p_start, p_end, (r, g, b) in axes:
        scr = project(cam, [p_start, p_end], w, h)
        if np.all(np.isfinite(scr)):
            cr.set_source_rgba(r, g, b, 1.0)
            cr.move_to(scr[0, 0], scr[0, 1])
            cr.line_to(scr[1, 0], scr[1, 1])
            cr.stroke()


def draw_detections(renderer: Any, cr: Any, state: OverlayState, w: int, h: int) -> None:
    default_rgb = parse_hex(state.detection_box_color)
    attached_colors = state.detection_attached_colors
    cr.set_line_width(state.detection_box_thickness)

    for det in state.detections:
        x1 = det.x1 * w
        y1 = det.y1 * h
        x2 = det.x2 * w
        y2 = det.y2 * h

        # A box attached to a marker is drawn in that marker's colour so the
        # operator can tell which detection is driving each followspot.
        attached_hex = attached_colors.get(det.track_id)
        if attached_hex:
            r, g, b = parse_hex(attached_hex)
        else:
            r, g, b = default_rgb

        cr.set_source_rgba(r, g, b, 0.8)
        cr.rectangle(x1, y1, x2 - x1, y2 - y1)
        cr.stroke()

        if state.detection_show_labels:
            label = f"{det.confidence:.0%}"
            renderer._set_ui_font(cr, 13)
            ext = cr.text_extents(label)
            cr.set_source_rgba(r, g, b, 0.6)
            cr.rectangle(x1, y1 - ext.height - 6, ext.width + 8, ext.height + 6)
            cr.fill()
            cr.set_source_rgba(0, 0, 0, 1.0)
            cr.move_to(x1 + 4, y1 - 3)
            cr.show_text(label)


# Alpha for the assist-mode ghost – dim so the AI-corrected PSN output reads as
# secondary to the solid marker the operator steers.
_GHOST_ALPHA = 0.5


def _draw_assist_ghost(
    cr: Any, state: OverlayState, t: MarkerOverlayData, w: int, h: int, rgb: tuple[float, float, float]
) -> None:
    """Draw the assist AI-corrected output: dim crosshair + ground ring, marker colour.

    No filled ball, no drop line, no speed bar – it's a secondary indicator of
    where the broadcast output sits, not the marker the operator steers.
    Crosshair and ring always render (not gated on the marker's show-flags) so
    the output footprint is always visible.
    """
    cam = state.camera_params
    tx, ty, tz = t.x, t.y, t.z
    r, g, b = rgb

    cs = state.crosshair_size
    cr.set_source_rgba(r, g, b, _GHOST_ALPHA)
    cr.set_line_width(max(1.0, state.crosshair_thickness))
    axis_pts = [
        (tx - cs, ty, tz),
        (tx + cs, ty, tz),
        (tx, ty - cs, tz),
        (tx, ty + cs, tz),
        (tx, ty, tz - cs),
        (tx, ty, tz + cs),
    ]
    axis_scr = project(cam, axis_pts, w, h)
    for i in range(0, 6, 2):
        p0, p1 = axis_scr[i], axis_scr[i + 1]
        if np.all(np.isfinite(p0)) and np.all(np.isfinite(p1)):
            cr.move_to(p0[0], p0[1])
            cr.line_to(p1[0], p1[1])
    cr.stroke()

    if state.grid_config:
        z_off = state.grid_config[5]
        gc_radius = state.ground_circle_size
        n_segs = 24
        gc_pts = [
            (
                tx + gc_radius * math.cos(2 * math.pi * i / n_segs),
                ty + gc_radius * math.sin(2 * math.pi * i / n_segs),
                z_off,
            )
            for i in range(n_segs)
        ]
        gc_scr = project(cam, gc_pts, w, h)
        gc_scr = gc_scr[np.all(np.isfinite(gc_scr), axis=1)]
        if len(gc_scr) >= 3:
            cr.move_to(gc_scr[0, 0], gc_scr[0, 1])
            for i in range(1, len(gc_scr)):
                cr.line_to(gc_scr[i, 0], gc_scr[i, 1])
            cr.close_path()
            cr.set_source_rgba(r, g, b, _GHOST_ALPHA)
            cr.set_line_width(1.5)
            cr.stroke()


def draw_marker(cr: Any, state: OverlayState, t: MarkerOverlayData, w: int, h: int) -> None:
    cam = state.camera_params
    tx, ty, tz = t.x, t.y, t.z

    pts = [
        (tx, ty, tz),
        (tx + t.radius, ty, tz),
    ]
    scr = project(cam, pts, w, h)
    if not np.all(np.isfinite(scr[0])):
        return

    sx, sy = float(scr[0, 0]), float(scr[0, 1])
    is_sel = t.marker_id == state.selected_id
    r, g, b = parse_hex(t.color)

    if t.is_assist_ghost:
        _draw_assist_ghost(cr, state, t, w, h, (r, g, b))
        return

    if np.all(np.isfinite(scr[1])):
        # Full 2D distance to the projected radius endpoint, not the X delta
        # alone – at camera angles where world-X maps near-vertically on screen
        # the X delta collapses the ball to the 3px floor.
        sr = max(3.0, math.hypot(scr[1, 0] - sx, scr[1, 1] - sy))
    else:
        sr = 10.0
    if is_sel:
        sr *= 1.15

    if state.show_ball:
        cr.set_source_rgba(r, g, b, state.transparency)
        cr.arc(sx, sy, sr, 0, 2 * math.pi)
        cr.fill()
        cr.set_source_rgba(r, g, b, 1.0)
        cr.set_line_width(2.0)
        cr.arc(sx, sy, sr, 0, 2 * math.pi)
        cr.stroke()

    if state.show_crosshair:
        cs = state.crosshair_size
        ch_r, ch_g, ch_b = parse_hex(state.crosshair_color)
        cr.set_source_rgba(ch_r, ch_g, ch_b, 1.0)
        cr.set_line_width(state.crosshair_thickness)
        axis_pts = [
            (tx - cs, ty, tz),
            (tx + cs, ty, tz),
            (tx, ty - cs, tz),
            (tx, ty + cs, tz),
            (tx, ty, tz - cs),
            (tx, ty, tz + cs),
        ]
        axis_scr = project(cam, axis_pts, w, h)
        for i in range(0, 6, 2):
            p0, p1 = axis_scr[i], axis_scr[i + 1]
            if np.all(np.isfinite(p0)) and np.all(np.isfinite(p1)):
                cr.move_to(p0[0], p0[1])
                cr.line_to(p1[0], p1[1])
        cr.stroke()

    if state.show_drop_line and state.grid_config:
        z_off = state.grid_config[5]
        dl_pts = [(tx, ty, tz), (tx, ty, z_off)]
        dl_scr = project(cam, dl_pts, w, h)
        if np.all(np.isfinite(dl_scr)):
            cr.set_source_rgba(r, g, b, 0.8)
            cr.set_line_width(state.drop_line_thickness)
            cr.move_to(dl_scr[0, 0], dl_scr[0, 1])
            cr.line_to(dl_scr[1, 0], dl_scr[1, 1])
            cr.stroke()

    if state.show_ground_circle and state.grid_config:
        z_off = state.grid_config[5]
        gc_radius = state.ground_circle_size
        n_segs = 24
        gc_pts = [
            (
                tx + gc_radius * math.cos(2 * math.pi * i / n_segs),
                ty + gc_radius * math.sin(2 * math.pi * i / n_segs),
                z_off,
            )
            for i in range(n_segs)
        ]
        gc_scr = project(cam, gc_pts, w, h)
        # Keep only finite points so one segment crossing behind the camera
        # doesn't erase the whole ground circle (matches the zone treatment).
        gc_scr = gc_scr[np.all(np.isfinite(gc_scr), axis=1)]
        if len(gc_scr) >= 3:
            cr.move_to(gc_scr[0, 0], gc_scr[0, 1])
            for i in range(1, len(gc_scr)):
                cr.line_to(gc_scr[i, 0], gc_scr[i, 1])
            cr.close_path()
            if state.ground_circle_filled:
                cr.set_source_rgba(r, g, b, 0.4)
                cr.fill()
            else:
                cr.set_source_rgba(r, g, b, 0.8)
                cr.set_line_width(2.0)
                cr.stroke()
