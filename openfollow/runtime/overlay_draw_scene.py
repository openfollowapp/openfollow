# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""3D overlay draw passes (grid/origin/markers/detections)."""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any, NamedTuple

import cairo
import numpy as np
import numpy.typing as npt

from openfollow.runtime.overlay_draw_style import parse_hex
from openfollow.runtime.overlay_state import MarkerOverlayData, OverlayState
from openfollow.scene.solver import (
    apply_overlay_distortion,
    cone_ring_angles,
    cone_silhouette_angles,
    ground_circle_world_ring,
    project_points,
    ring_silhouette_indices,
)
from openfollow.zones.geometry import polygon_signed_area

# Cap grid lines per axis. A degenerate width/depth ÷ spacing (e.g. from a
# hand-edited config.toml or peer broadcast – neither has an upper clamp) would
# otherwise allocate a multi-MB point buffer and issue hundreds of thousands of
# Cairo calls per frame, freezing the overlay.
_MAX_GRID_LINES_PER_AXIS = 200

# Chords per straight world line when lens distortion is active. A straight
# world line stays straight under the pinhole projection; only the radial warp
# bows it, so we subdivide and warp each chord endpoint to approximate the
# curve. 1 (no subdivision) when distortion is off – the pinhole path is then
# byte-for-byte unchanged.
_DISTORTION_SUBDIVISIONS = 12


def project(
    cam: npt.NDArray[Any] | None,
    pts_psn: list[Any] | npt.NDArray[Any],
    w: int,
    h: int,
    k1: float = 0.0,
    k2: float = 0.0,
) -> npt.NDArray[Any]:
    # Return NaN when camera missing; downstream np.isfinite filters handle it.
    if cam is None:  # pragma: no cover
        return np.full((np.asarray(pts_psn, dtype=np.float64).reshape(-1, 3).shape[0], 2), np.nan)
    arr = np.asarray(pts_psn, dtype=np.float64).reshape(-1, 3)
    scr = project_points(cam, arr, float(w), float(h))
    # Identity when k1 == k2 == 0, so the pinhole overlay path is unchanged.
    return apply_overlay_distortion(scr, float(w), float(h), k1, k2)


def _project_segments(
    cam: npt.NDArray[Any] | None,
    segments: npt.NDArray[Any] | list[Any],
    w: int,
    h: int,
    k1: float,
    k2: float,
    n: int,
) -> npt.NDArray[Any]:
    """Project + warp ``segments`` (M, 2, 3) as ``n``-chord polylines.

    Returns an (M, n+1, 2) array. Subdivision is in world space so each chord
    endpoint is warped individually – that is what bows a straight world line
    on screen to match the lens.
    """
    segs = np.asarray(segments, dtype=np.float64).reshape(-1, 2, 3)
    ts = np.linspace(0.0, 1.0, n + 1)
    p0 = segs[:, 0:1, :]
    p1 = segs[:, 1:2, :]
    world = p0 + ts[None, :, None] * (p1 - p0)
    scr = project(cam, world.reshape(-1, 3), w, h, k1, k2)
    return scr.reshape(segs.shape[0], ts.shape[0], 2)


def _stroke_polyline(cr: Any, poly: npt.NDArray[Any]) -> bool:
    """Emit move_to/line_to for one polyline; return whether anything was drawn.

    Connects consecutive on-screen points, breaking the path across points
    behind the camera (NaN) so a partially-visible line never spans the
    singularity. A lone finite point (both neighbours behind the camera) draws
    nothing – matching the straight-segment passes, where a segment renders only
    when both endpoints are finite. The caller sets colour / width and strokes.
    """
    pen_down = False
    drew = False
    run_start: tuple[float, float] | None = None
    for x, y in poly:
        if math.isfinite(x) and math.isfinite(y):
            if pen_down:
                cr.line_to(x, y)
                drew = True
            elif run_start is None:
                run_start = (x, y)
            else:
                cr.move_to(run_start[0], run_start[1])
                cr.line_to(x, y)
                pen_down = True
                drew = True
        else:
            pen_down = False
            run_start = None
    return drew


def _draw_world_lines(
    cr: Any,
    cam: npt.NDArray[Any] | None,
    segments: npt.NDArray[Any] | list[Any],
    w: int,
    h: int,
    k1: float,
    k2: float,
) -> bool:
    """Path straight world segments, bowing them to match the lens when
    distortion is active. Returns whether any line was drawn so the caller can
    skip an empty stroke. The caller sets colour / width before and strokes
    after, so several segment groups can share one stroke.
    """
    n = _DISTORTION_SUBDIVISIONS if (k1 or k2) else 1
    polys = _project_segments(cam, segments, w, h, k1, k2, n)
    drew = False
    for poly in polys:
        if _stroke_polyline(cr, poly):
            drew = True
    return drew


def draw_grid(renderer: Any, cr: Any, state: OverlayState, w: int, h: int) -> None:
    if not state.grid_visible:
        return
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

    segments = buf[:idx].reshape(-1, 2, 3)
    _draw_world_lines(cr, cam, segments, w, h, state.lens_k1, state.lens_k2)
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
        cr.set_source_rgba(r, g, b, 1.0)
        if _draw_world_lines(cr, cam, [(p_start, p_end)], w, h, state.lens_k1, state.lens_k2):
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
# Alpha of a cone's wireframe, matching the ground circle's outline.
_CONE_ALPHA = 0.8
# Line-width factor marking the selected marker in cone style, the
# counterpart of the ball's radius bump.
_CONE_SELECTED_SCALE = 1.5
# Shaded side: how far the lit flank tints toward white and the far flank
# toward black. The light sits to the screen left.
_SHADE_LIGHT = 0.35
_SHADE_DARK = 0.45


def _side_gradient(
    edges: list[tuple[tuple[float, float], tuple[float, float]]],
    rgb: tuple[float, float, float],
    alpha: float,
) -> cairo.LinearGradient:
    """Linear gradient across the side, from the left edge's midpoint (lit) to
    the right edge's midpoint (shadow)."""
    mids = sorted(((f[0] + t[0]) / 2.0, (f[1] + t[1]) / 2.0) for f, t in edges)
    (x0, y0), (x1, y1) = mids
    grad = cairo.LinearGradient(x0, y0, x1, y1)
    r, g, b = rgb
    lit = [c + (1.0 - c) * _SHADE_LIGHT for c in rgb]
    shadow = [c * (1.0 - _SHADE_DARK) for c in rgb]
    grad.add_color_stop_rgba(0.0, lit[0], lit[1], lit[2], alpha)
    grad.add_color_stop_rgba(0.5, r, g, b, alpha)
    grad.add_color_stop_rgba(1.0, shadow[0], shadow[1], shadow[2], alpha)
    return grad


def _path_ring(cr: Any, ring_scr: npt.NDArray[Any]) -> bool:
    """Path a projected ring as a closed polygon; return whether it was pathed.

    Keeps only finite points so one segment crossing behind the camera doesn't
    erase the whole ring. The caller strokes or fills.
    """
    if len(ring_scr) < 3:
        return False
    cr.move_to(ring_scr[0, 0], ring_scr[0, 1])
    for i in range(1, len(ring_scr)):
        cr.line_to(ring_scr[i, 0], ring_scr[i, 1])
    cr.close_path()
    return True


def _wound_same_way(poly: npt.NDArray[Any]) -> npt.NDArray[Any]:
    """Return ``poly`` with a non-negative signed area, reversing it if needed.

    Cairo's default fill rule is non-zero winding: sub-paths wound the same
    way union into one evenly covered region, while an opposite winding
    would punch a hole where they overlap.
    """
    if len(poly) < 3:  # nothing to wind; _path_ring skips it anyway
        return poly
    return poly[::-1] if polygon_signed_area(poly.tolist()) < 0 else poly


class _ConeScreen(NamedTuple):
    """A marker's cone on screen: finite ring points, projected centres and
    the silhouette edges as ``(base index, top index)`` pairs."""

    base_ring: npt.NDArray[Any]
    top_ring: npt.NDArray[Any]
    base_center: npt.NDArray[Any]
    top_center: npt.NDArray[Any]
    edges: list[tuple[int, int]]


def _cone_geometry(state: OverlayState, t: MarkerOverlayData, w: int, h: int) -> _ConeScreen:
    """Project a marker's cone: the base ring on the stage plane, the top ring
    at the marker's Z and the silhouette edges joining them.

    The top ring follows Z wherever it is, below the stage plane included: the
    cone is not symmetric, so a marker under the stage reads as pointing down.
    """
    tx, ty, tz = t.x, t.y, t.z
    z_off = state.grid_config[5] if state.grid_config else 0.0
    r_base, r_top = state.cone_base_diameter / 2.0, state.cone_top_diameter / 2.0
    silhouette = cone_silhouette_angles(state.camera_params, (tx, ty), z_off, tz, r_base, r_top)
    angles, at = cone_ring_angles(silhouette)
    cos_a, sin_a = np.cos(angles), np.sin(angles)
    n = len(angles)

    # One projection per marker: both centres and both rings, split after.
    world = np.empty((2 * n + 2, 3))
    world[0] = (tx, ty, z_off)
    world[1 : n + 1] = np.column_stack([tx + r_base * cos_a, ty + r_base * sin_a, np.full(n, z_off)])
    world[n + 1] = (tx, ty, tz)
    world[n + 2 :] = np.column_stack([tx + r_top * cos_a, ty + r_top * sin_a, np.full(n, tz)])
    scr = project(state.camera_params, world, w, h, state.lens_k1, state.lens_k2)
    base_center, base_ring = scr[0], scr[1 : n + 1]
    top_center, top_ring = scr[n + 1], scr[n + 2 :]
    if at and np.all(np.isfinite(scr)):
        # The rings share their angles, so each silhouette edge joins equal indices.
        return _ConeScreen(base_ring, top_ring, base_center, top_center, [(i, i) for i in at])

    # No silhouette (seen from inside its view) or part of the cone behind the camera.
    base_ring = base_ring[np.all(np.isfinite(base_ring), axis=1)]
    top_ring = top_ring[np.all(np.isfinite(top_ring), axis=1)]
    edges: list[tuple[int, int]] = []
    if np.all(np.isfinite(base_center)) and np.all(np.isfinite(top_center)):
        edges = ring_silhouette_indices(
            base_ring,
            top_ring,
            (float(base_center[0]), float(base_center[1])),
            (float(top_center[0]), float(top_center[1])),
        )
    return _ConeScreen(base_ring, top_ring, base_center, top_center, edges)


def _ring_arc(ring: npt.NDArray[Any], start: int, end: int, toward: npt.NDArray[Any]) -> npt.NDArray[Any]:
    """Points of ``ring`` from index ``start`` to ``end``, along whichever of
    the two arcs lies on the ``toward`` side of the chord between them."""
    n = len(ring)
    forward = [(start + k) % n for k in range((end - start) % n + 1)]
    backward = [(start - k) % n for k in range((start - end) % n + 1)]
    cx, cy = ring[end] - ring[start]

    def side(xs: npt.NDArray[Any], ys: npt.NDArray[Any]) -> float:
        return float(np.sum(cx * (ys - ring[start, 1]) - cy * (xs - ring[start, 0])))

    want = side(np.asarray([toward[0]]), np.asarray([toward[1]]))
    fwd = ring[forward]
    arc = forward if (side(fwd[:, 0], fwd[:, 1]) >= 0) == (want >= 0) else backward
    return ring[arc]


def _cone_side(g: _ConeScreen) -> npt.NDArray[Any] | None:
    """The side of the cone as the region between the rings: from one tangent
    point along the base ring's arc on the top's side to the other, up that
    edge, back along the top ring's arc on the base's side, and down again.

    Bounded by the ring arcs rather than their chords, it shares no area with
    either disc while the discs are apart, so a translucent fill never doubles up and the only seams
    fall on the ring outlines the wireframe strokes over.
    """
    if len(g.edges) != 2:
        return None
    (ia, ja), (ib, jb) = g.edges
    base_arc = _ring_arc(g.base_ring, ia, ib, g.top_center)
    top_arc = _ring_arc(g.top_ring, jb, ja, g.base_center)
    return np.vstack([base_arc, top_arc])


def _draw_cone(
    cr: Any,
    state: OverlayState,
    t: MarkerOverlayData,
    w: int,
    h: int,
    rgb: tuple[float, float, float],
    alpha: float,
    line_width: float,
    fill_alpha: float = 0.0,
    shaded: bool = False,
    geometry: _ConeScreen | None = None,
) -> None:
    """Draw a truncated cone between the stage plane and the marker's Z.

    A base ring at the marker's XY on the stage plane, a top ring at its Z and
    the two silhouette edges joining them. A positive ``fill_alpha`` first
    fills the silhouette (both discs and the side between them) as one evenly
    covered region, or, when ``shaded``, the side under a left-lit gradient
    between the two flat discs; the wireframe is stroked on top.
    """
    if not state.grid_config:
        return
    g = geometry if geometry is not None else _cone_geometry(state, t, w, h)
    edges = [
        ((float(g.base_ring[i, 0]), float(g.base_ring[i, 1])), (float(g.top_ring[j, 0]), float(g.top_ring[j, 1])))
        for i, j in g.edges
    ]
    side = _cone_side(g)
    r, g_, b = rgb

    if fill_alpha > 0.0:
        if shaded and side is not None:
            # Base disc, lit side, lid: three passes so the side can carry
            # its own gradient while the discs stay flat. Where the discs
            # overlap, seen from above, the passes stack. A ring too
            # degenerate to path leaves an empty path, and the fill is a no-op.
            _path_ring(cr, g.base_ring)
            cr.set_source_rgba(r, g_, b, fill_alpha)
            cr.fill()
            _path_ring(cr, side)
            cr.set_source(_side_gradient(edges, rgb, fill_alpha))
            cr.fill()
            _path_ring(cr, g.top_ring)
            cr.set_source_rgba(r, g_, b, fill_alpha)
            cr.fill()
        else:
            regions = [g.base_ring, g.top_ring] + ([side] if side is not None else [])
            filled = [_path_ring(cr, _wound_same_way(region)) for region in regions]
            if any(filled):
                cr.set_source_rgba(r, g_, b, fill_alpha)
                cr.fill()

    cr.set_source_rgba(r, g_, b, alpha)
    cr.set_line_width(line_width)
    drew = _path_ring(cr, g.base_ring)
    if _path_ring(cr, g.top_ring):
        drew = True
    for (x0, y0), (x1, y1) in edges:
        cr.move_to(x0, y0)
        cr.line_to(x1, y1)
        drew = True
    if drew:
        cr.stroke()


def _draw_assist_ghost(
    cr: Any, state: OverlayState, t: MarkerOverlayData, w: int, h: int, rgb: tuple[float, float, float]
) -> None:
    """Draw the assist AI-corrected output: dim crosshair + ground ring, marker colour.

    No filled ball, no Z line, no speed bar – it's a secondary indicator of
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
    axis_segments = [
        ((tx - cs, ty, tz), (tx + cs, ty, tz)),
        ((tx, ty - cs, tz), (tx, ty + cs, tz)),
        ((tx, ty, tz - cs), (tx, ty, tz + cs)),
    ]
    _draw_world_lines(cr, cam, axis_segments, w, h, state.lens_k1, state.lens_k2)
    cr.stroke()

    if state.grid_config:
        z_off = state.grid_config[5]
        gc_pts = ground_circle_world_ring(tx, ty, z_off, state.ground_circle_size)
        gc_scr = project(cam, gc_pts, w, h, state.lens_k1, state.lens_k2)
        if _path_ring(cr, gc_scr[np.all(np.isfinite(gc_scr), axis=1)]):
            cr.set_source_rgba(r, g, b, _GHOST_ALPHA)
            cr.set_line_width(1.5)
            cr.stroke()


class ConeCache:
    """Each marker's cone as a picture, drawn again only when something it depends on changed.

    The Operator Screen redraws on every display refresh; a cone that has not
    moved is then one paint instead of its geometry and three fills.
    """

    def __init__(self) -> None:
        self._entries: dict[int, tuple[tuple[Any, ...], cairo.ImageSurface, int, int]] = {}

    def retain(self, marker_ids: Iterable[int]) -> None:
        """Forget the cones of markers no longer drawn."""
        keep = set(marker_ids)
        for marker_id in [m for m in self._entries if m not in keep]:
            del self._entries[marker_id]

    def draw(
        self,
        cr: Any,
        state: OverlayState,
        t: MarkerOverlayData,
        w: int,
        h: int,
        rgb: tuple[float, float, float],
        alpha: float,
        line_width: float,
        fill_alpha: float = 0.0,
        shaded: bool = False,
    ) -> None:
        if not state.grid_config:
            return
        cam = state.camera_params
        scale = cr.get_target().get_device_scale()
        key = (
            t.x,
            t.y,
            t.z,
            None if cam is None else tuple(float(v) for v in cam),
            state.lens_k1,
            state.lens_k2,
            tuple(state.grid_config),
            state.cone_base_diameter,
            state.cone_top_diameter,
            rgb,
            alpha,
            line_width,
            fill_alpha,
            shaded,
            w,
            h,
            scale,
        )
        entry = self._entries.get(t.marker_id)
        if entry is None or entry[0] != key:
            entry = self._render(key, state, t, w, h, rgb, alpha, line_width, fill_alpha, shaded, scale)
            if entry is None:
                self._entries.pop(t.marker_id, None)
                return
            self._entries[t.marker_id] = entry
        _, picture, x0, y0 = entry
        cr.set_source_surface(picture, x0, y0)
        cr.paint()

    @staticmethod
    def _render(
        key: tuple[Any, ...],
        state: OverlayState,
        t: MarkerOverlayData,
        w: int,
        h: int,
        rgb: tuple[float, float, float],
        alpha: float,
        line_width: float,
        fill_alpha: float,
        shaded: bool,
        scale: tuple[float, float],
    ) -> tuple[tuple[Any, ...], cairo.ImageSurface, int, int] | None:
        g = _cone_geometry(state, t, w, h)
        points = np.vstack([g.base_ring, g.top_ring])
        if len(points) == 0:
            return None
        # The stroke reaches half its width past the rings, anti-aliasing one pixel more;
        # nothing beyond the frame is ever shown.
        pad = int(math.ceil(line_width / 2.0)) + 2
        x0 = max(0, int(math.floor(points[:, 0].min())) - pad)
        y0 = max(0, int(math.floor(points[:, 1].min())) - pad)
        x1 = min(w, int(math.ceil(points[:, 0].max())) + pad)
        y1 = min(h, int(math.ceil(points[:, 1].max())) + pad)
        if x1 <= x0 or y1 <= y0:
            return None
        sx, sy = scale
        # Drawn at the target's device scale, so a HiDPI screen gets a sharp cone.
        picture = cairo.ImageSurface(cairo.FORMAT_ARGB32, math.ceil((x1 - x0) * sx), math.ceil((y1 - y0) * sy))
        picture.set_device_scale(sx, sy)
        ctx = cairo.Context(picture)
        ctx.translate(-x0, -y0)
        _draw_cone(ctx, state, t, w, h, rgb, alpha, line_width, fill_alpha, shaded, geometry=g)
        return key, picture, x0, y0


def draw_marker(
    cr: Any,
    state: OverlayState,
    t: MarkerOverlayData,
    w: int,
    h: int,
    cone_cache: ConeCache | None = None,
) -> None:
    cam = state.camera_params
    tx, ty, tz = t.x, t.y, t.z
    is_sel = t.marker_id == state.selected_id
    r, g, b = parse_hex(t.color)

    # The cone survives any one point projecting behind the camera, so it is
    # not gated on the marker's own point the way the ball is.
    if state.marker_style == "cone":
        draw = _draw_cone if cone_cache is None else cone_cache.draw
        if t.is_assist_ghost:
            draw(cr, state, t, w, h, (r, g, b), _GHOST_ALPHA, max(1.0, state.cone_thickness))
            return
        lw = float(state.cone_thickness)
        if is_sel:
            lw *= _CONE_SELECTED_SCALE
        fill_alpha = state.cone_opacity if state.cone_filled else 0.0
        draw(cr, state, t, w, h, (r, g, b), _CONE_ALPHA, lw, fill_alpha, state.cone_shaded)
        return

    pts = [
        (tx, ty, tz),
        (tx + t.radius, ty, tz),
    ]
    scr = project(cam, pts, w, h, state.lens_k1, state.lens_k2)
    if not np.all(np.isfinite(scr[0])):
        return

    sx, sy = float(scr[0, 0]), float(scr[0, 1])

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
        axis_segments = [
            ((tx - cs, ty, tz), (tx + cs, ty, tz)),
            ((tx, ty - cs, tz), (tx, ty + cs, tz)),
            ((tx, ty, tz - cs), (tx, ty, tz + cs)),
        ]
        _draw_world_lines(cr, cam, axis_segments, w, h, state.lens_k1, state.lens_k2)
        cr.stroke()

    if state.show_z_line and state.grid_config:
        z_off = state.grid_config[5]
        cr.set_source_rgba(r, g, b, 0.8)
        cr.set_line_width(state.z_line_thickness)
        if _draw_world_lines(cr, cam, [((tx, ty, tz), (tx, ty, z_off))], w, h, state.lens_k1, state.lens_k2):
            cr.stroke()

    if state.show_ground_circle and state.grid_config:
        z_off = state.grid_config[5]
        gc_pts = ground_circle_world_ring(tx, ty, z_off, state.ground_circle_size)
        gc_scr = project(cam, gc_pts, w, h, state.lens_k1, state.lens_k2)
        if _path_ring(cr, gc_scr[np.all(np.isfinite(gc_scr), axis=1)]):
            if state.ground_circle_filled:
                cr.set_source_rgba(r, g, b, 0.4)
                cr.fill()
            else:
                cr.set_source_rgba(r, g, b, 0.8)
                cr.set_line_width(2.0)
                cr.stroke()
