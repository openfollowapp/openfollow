# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""3D overlay draw passes (grid/origin/markers/detections)."""

from __future__ import annotations

import functools
import math
from collections.abc import Iterable
from typing import Any, NamedTuple

import cairo
import numpy as np
import numpy.typing as npt

from openfollow.runtime.overlay_draw_style import parse_hex
from openfollow.runtime.overlay_layers import LayerContext, cut, keep
from openfollow.runtime.overlay_picture import box_picture, paste
from openfollow.runtime.overlay_state import MarkerOverlayData, OverlayState
from openfollow.scene.ground_grid import grid_segments_in_polygon, main_grid_steps, outline_segments
from openfollow.scene.solver import (
    apply_overlay_distortion,
    cone_ring_angles,
    cone_silhouette_angles,
    ground_circle_world_ring,
    project_points,
    ring_silhouette_indices,
)
from openfollow.zones.geometry import point_in_polygon, polygon_signed_area

# Cap grid lines per axis. A degenerate width/depth ÷ spacing (e.g. from a
# hand-edited config.toml or peer broadcast – neither has an upper clamp) would
# otherwise allocate a multi-MB point buffer and issue hundreds of thousands of
# Cairo calls per frame, freezing the overlay.
_MAX_GRID_LINES_PER_AXIS = 200


def grid_line_count(length: float, spacing: float) -> int:
    """Lines across one axis of the grid, both edges included, spread evenly
    over ``length``. The setup wizard mirrors this rule (``wizGridLineCount``)."""
    return min(max(int(length / spacing) + 1, 2), _MAX_GRID_LINES_PER_AXIS)


# Chords per straight world line when lens distortion is active. A straight
# world line stays straight under the pinhole projection; only the radial warp
# bows it, so we subdivide and warp each chord endpoint to approximate the
# curve: 24 keep a line along the frame edge of a 126 degree lens within about
# a pixel of its curve. 1 (no subdivision) when the lens pair is 0: the lens
# centre alone only shifts a line, and the pinhole path is byte-for-byte unchanged.
_DISTORTION_SUBDIVISIONS = 24


def project(
    cam: npt.NDArray[Any] | None,
    pts_psn: list[Any] | npt.NDArray[Any],
    w: int,
    h: int,
    k1: float = 0.0,
    k2: float = 0.0,
    cx: float = 0.0,
    cy: float = 0.0,
) -> npt.NDArray[Any]:
    # Return NaN when camera missing; downstream np.isfinite filters handle it.
    if cam is None:  # pragma: no cover
        return np.full((np.asarray(pts_psn, dtype=np.float64).reshape(-1, 3).shape[0], 2), np.nan)
    arr = np.asarray(pts_psn, dtype=np.float64).reshape(-1, 3)
    scr = project_points(cam, arr, float(w), float(h))
    # Identity without a lens, so the pinhole overlay path is unchanged.
    return apply_overlay_distortion(scr, float(w), float(h), k1, k2, cx, cy)


def _project_segments(
    cam: npt.NDArray[Any] | None,
    segments: npt.NDArray[Any] | list[Any],
    w: int,
    h: int,
    k1: float,
    k2: float,
    cx: float,
    cy: float,
    n: int,
) -> npt.NDArray[Any]:
    """Project + warp ``segments`` (M, 2, 3) as ``n``-chord polylines.

    Returns an (M, n+1, 2) array. Subdivision is in world space so each chord
    endpoint is warped individually – that is what bows a straight world line
    on screen to match the lens.
    """
    world = _subdivided(segments, n)
    scr = project(cam, world.reshape(-1, 3), w, h, k1, k2, cx, cy)
    return scr.reshape(world.shape[0], n + 1, 2)


def _subdivided(segments: npt.NDArray[Any] | list[Any], n: int) -> npt.NDArray[Any]:
    """``segments`` (M, 2, 3) as ``n``-chord world polylines, (M, n+1, 3)."""
    segs = np.asarray(segments, dtype=np.float64).reshape(-1, 2, 3)
    ts = np.linspace(0.0, 1.0, n + 1)
    p0 = segs[:, 0:1, :]
    p1 = segs[:, 1:2, :]
    return p0 + ts[None, :, None] * (p1 - p0)


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
    # Python floats: walking numpy rows costs a scalar object per coordinate.
    for x, y in poly.tolist():
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
    cx: float,
    cy: float,
) -> bool:
    """Path straight world segments, bowing them to match the lens when
    distortion is active. Returns whether any line was drawn so the caller can
    skip an empty stroke. The caller sets colour / width before and strokes
    after, so several segment groups can share one stroke.
    """
    n = _DISTORTION_SUBDIVISIONS if (k1 or k2) else 1
    polys = _project_segments(cam, segments, w, h, k1, k2, cx, cy, n)
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

    n_x = grid_line_count(gw, gs)
    n_z = grid_line_count(gd, gs)
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
    _draw_world_lines(cr, cam, segments, w, h, *state.lens)
    cr.stroke()


def floor_z_at(state: OverlayState, x: float, y: float) -> float:
    """The floor under world ``(x, y)``: the ground grid's Z inside its polygon,
    else the main grid's. The one place a marker's floor height is decided."""
    if state.ground_grid_active and point_in_polygon(x, y, state.ground_grid_vertices):
        return state.ground_grid_z
    return state.grid_config[5] if state.grid_config else 0.0


def marker_floor_z(state: OverlayState, t: MarkerOverlayData) -> float:
    """Where a marker's floor elements stand: the floor under it, raised by its zone lift."""
    return floor_z_at(state, t.x, t.y) + t.lift


@functools.lru_cache(maxsize=4)
def _ground_grid_segments(
    verts: tuple[tuple[float, float], ...], gc: tuple[float, ...], z: float
) -> npt.NDArray[np.float64]:
    """The ground grid's lines and outline in world space; kept, as the polygon changes only on a save."""
    gw, gd, gs, x_off, y_off, _ = gc
    step_x, step_y = main_grid_steps(gw, gd, gs, _MAX_GRID_LINES_PER_AXIS)
    lines = grid_segments_in_polygon(
        verts, x_off - gw / 2.0, y_off - gd / 2.0, step_x, step_y, z, _MAX_GRID_LINES_PER_AXIS
    )
    segments = np.concatenate([lines, outline_segments(verts, z)])
    segments.setflags(write=False)
    return segments


def draw_ground_grid(cr: Any, state: OverlayState, w: int, h: int) -> None:
    """Main-grid-aligned lines clipped to the ground-grid polygon, plus its outline, in the main grid's style."""
    gc = state.grid_config
    if not state.ground_grid_visible or gc is None:
        return
    segments = _ground_grid_segments(tuple(map(tuple, state.ground_grid_vertices)), tuple(gc), state.ground_grid_z)
    r, g, b = parse_hex(state.grid_color)
    cr.set_source_rgba(r, g, b, state.grid_transparency)
    cr.set_line_width(float(state.grid_thickness))
    if _draw_world_lines(cr, state.camera_params, segments, w, h, *state.lens):
        cr.stroke()


class GridCache:
    """The main grid and the ground grid as one picture, drawn again only when either grid or the view changed.

    The Operator Screen draws a picture at most once per display refresh; an unchanged grid is then
    one paste instead of projecting and stroking every line. The picture is the whole canvas pasted
    at its origin, so every line covers the same pixels as one drawn directly; the paste covers only
    the tiles its lines run through, as the rest of the picture is clear.
    """

    def __init__(self) -> None:
        self._entry: tuple[tuple[Any, ...], cairo.ImageSurface, list[tuple[float, float, float, float]]] | None = None

    def clear(self) -> None:
        """Let go of the picture, which can run to a hundred megabytes on a HiDPI 4K screen."""
        self._entry = None

    def draw(self, renderer: Any, cr: Any, state: OverlayState, w: int, h: int) -> None:
        cam = state.camera_params
        gc = state.grid_config
        if cam is None or gc is None or not (state.grid_visible or state.ground_grid_visible):
            self._entry = None
            return
        scale = cr.get_target().get_device_scale()
        # Every input either grid is projected or drawn from; one missing here freezes on screen.
        key = (
            state.grid_visible,
            state.ground_grid_visible,
            tuple(gc),
            tuple(map(tuple, state.ground_grid_vertices)),
            state.ground_grid_z,
            state.grid_color,
            state.grid_transparency,
            state.grid_thickness,
            tuple(float(v) for v in cam),
            state.lens,
            w,
            h,
            scale,
        )
        if self._entry is None or self._entry[0] != key:
            # A layered canvas may still be showing the kept picture, so it gets a new one.
            reuse = self._entry[1] if self._entry is not None and not isinstance(cr, LayerContext) else None
            picture = _blank_canvas(reuse, w, h, scale)
            # Filed again only once drawn, so a failed draw leaves no picture under the old key.
            self._entry = None
            ctx = cairo.Context(picture)
            draw_grid(renderer, ctx, state, w, h)
            draw_ground_grid(ctx, state, w, h)
            self._entry = (key, picture, _inked_tiles(picture))
        _, picture, tiles = self._entry
        if keep(cr, picture, 0, 0):
            return
        cr.set_source_surface(picture, 0, 0)
        for rect in tiles:
            cr.rectangle(*rect)
        cr.fill()


_TILE = 32


def _inked_tiles(picture: cairo.ImageSurface) -> list[tuple[float, float, float, float]]:
    """The ``_TILE``-pixel tiles of ``picture`` holding any ink, as rectangles in its user space.

    Neighbouring inked tiles in a row join into one rectangle; edges fall on whole device pixels,
    so filling them with the picture is pixel for pixel a paint of it.
    """
    picture.flush()
    pw, ph = picture.get_width(), picture.get_height()
    pixels: npt.NDArray[np.uint32] = np.ndarray(
        (ph, picture.get_stride() // 4), dtype=np.uint32, buffer=picture.get_data()
    )[:, :pw]
    rows, cols = -(-ph // _TILE), -(-pw // _TILE)
    # Scanned one band of rows at a time: no canvas-sized copy.
    bands = np.zeros((rows, cols * _TILE), dtype=np.bool_)
    for r in range(rows):
        bands[r, :pw] = pixels[r * _TILE : (r + 1) * _TILE].any(axis=0)
    inked: npt.NDArray[np.bool_] = np.any(bands.reshape(rows, cols, _TILE), axis=2)
    sx, sy = picture.get_device_scale()
    tiles = []
    for r, row in enumerate(inked.tolist()):
        c = 0
        while c < cols:
            if not row[c]:
                c += 1
                continue
            start = c
            while c < cols and row[c]:
                c += 1
            x0, y0 = start * _TILE, r * _TILE
            x1, y1 = min(c * _TILE, pw), min(y0 + _TILE, ph)
            tiles.append((x0 / sx, y0 / sy, (x1 - x0) / sx, (y1 - y0) / sy))
    return tiles


def _blank_canvas(kept: cairo.ImageSurface | None, w: int, h: int, scale: tuple[float, float]) -> cairo.ImageSurface:
    """An empty picture of the canvas at ``scale``: ``kept`` cleared when it already is one, else a new one."""
    sx, sy = scale
    size = (math.ceil(w * sx), math.ceil(h * sy))
    if kept is not None and (kept.get_width(), kept.get_height(), kept.get_device_scale()) == (*size, scale):
        clear = cairo.Context(kept)
        clear.set_operator(cairo.OPERATOR_CLEAR)
        clear.paint()
        return kept
    picture = cairo.ImageSurface(cairo.FORMAT_ARGB32, *size)
    picture.set_device_scale(sx, sy)
    return picture


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
        if _draw_world_lines(cr, cam, [(p_start, p_end)], w, h, *state.lens):
            cr.stroke()


def draw_detections(renderer: Any, cr: Any, state: OverlayState, w: int, h: int) -> None:
    default_rgb = parse_hex(state.detection_box_color)
    attached_colors = state.detection_attached_colors
    for det in state.detections:
        cr.set_line_width(state.detection_box_thickness)
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
        cut(cr)


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
    """A marker's cone on screen: finite ring points, projected centres, the
    silhouette edges as ``(base index, top index)`` pairs, and each edge as a
    polyline from its base point to its top point, bowed to match the lens."""

    base_ring: npt.NDArray[Any]
    top_ring: npt.NDArray[Any]
    base_center: npt.NDArray[Any]
    top_center: npt.NDArray[Any]
    edges: list[tuple[int, int]]
    edge_lines: list[npt.NDArray[Any]]


def _cone_geometry(state: OverlayState, t: MarkerOverlayData, w: int, h: int) -> _ConeScreen:
    """Project a marker's cone: the base ring on the stage plane, the top ring
    at the marker's Z and the silhouette edges joining them.

    The top ring follows Z wherever it is, below the stage plane included: the
    cone is not symmetric, so a marker under the stage reads as pointing down.
    """
    tx, ty, tz = t.x, t.y, t.z
    z_off = marker_floor_z(state, t)
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
    scr = project(state.camera_params, world, w, h, *state.lens)
    base_center, base_ring = scr[0], scr[1 : n + 1]
    top_center, top_ring = scr[n + 1], scr[n + 2 :]
    base_world, top_world = world[1 : n + 1], world[n + 2 :]
    edges: list[tuple[int, int]] = []
    if at and np.all(np.isfinite(scr)):
        # The rings share their angles, so each silhouette edge joins equal indices.
        edges = [(i, i) for i in at]
    else:
        # No silhouette (seen from inside its view) or part of the cone behind the camera.
        base_ok = np.all(np.isfinite(base_ring), axis=1)
        top_ok = np.all(np.isfinite(top_ring), axis=1)
        base_ring, base_world = base_ring[base_ok], base_world[base_ok]
        top_ring, top_world = top_ring[top_ok], top_world[top_ok]
        if np.all(np.isfinite(base_center)) and np.all(np.isfinite(top_center)):
            edges = ring_silhouette_indices(
                base_ring,
                top_ring,
                (float(base_center[0]), float(base_center[1])),
                (float(top_center[0]), float(top_center[1])),
            )
    lines = [np.vstack([base_ring[i], top_ring[j]]) for i, j in edges]
    if edges and (state.lens_k1 or state.lens_k2):
        segments = [(base_world[i], top_world[j]) for i, j in edges]
        bowed = _project_segments(state.camera_params, segments, w, h, *state.lens, _DISTORTION_SUBDIVISIONS)
        lines = [line[np.all(np.isfinite(line), axis=1)] for line in bowed]
    return _ConeScreen(base_ring, top_ring, base_center, top_center, edges, lines)


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
    up, down = g.edge_lines[1][1:-1], g.edge_lines[0][-2:0:-1]
    base_arc = _ring_arc(g.base_ring, ia, ib, g.top_center)
    top_arc = _ring_arc(g.top_ring, jb, ja, g.base_center)
    return np.vstack([base_arc, up, top_arc, down])


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
    edges = [((float(ln[0, 0]), float(ln[0, 1])), (float(ln[-1, 0]), float(ln[-1, 1]))) for ln in g.edge_lines]
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

    cr.save()
    cr.set_source_rgba(r, g_, b, alpha)
    cr.set_line_width(line_width)
    # A miter where a ring seen almost edge-on turns sharply spikes past the ring.
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    drew = _path_ring(cr, g.base_ring)
    if _path_ring(cr, g.top_ring):
        drew = True
    for line in g.edge_lines:
        cr.move_to(line[0, 0], line[0, 1])
        for x, y in line[1:]:
            cr.line_to(x, y)
        drew = True
    if drew:
        cr.stroke()
    cr.restore()


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
    _draw_world_lines(cr, cam, axis_segments, w, h, *state.lens)
    cr.stroke()

    if state.grid_config:
        z_off = marker_floor_z(state, t)
        gc_pts = ground_circle_world_ring(tx, ty, z_off, state.ground_circle_size)
        gc_scr = project(cam, gc_pts, w, h, *state.lens)
        if _path_ring(cr, gc_scr[np.all(np.isfinite(gc_scr), axis=1)]):
            cr.set_source_rgba(r, g, b, _GHOST_ALPHA)
            cr.set_line_width(1.5)
            cr.stroke()


class ConeCache:
    """Each marker's cone as a picture, drawn again only when something it depends on changed.

    The Operator Screen draws a picture at most once per display refresh; a cone
    that has not moved is then one paint instead of its geometry and three fills.
    """

    def __init__(self) -> None:
        self._entries: dict[tuple[int, bool], tuple[tuple[Any, ...], cairo.ImageSurface, int, int]] = {}

    @staticmethod
    def _slot(t: MarkerOverlayData) -> tuple[int, bool]:
        # An assist marker draws its anchor and its ghost under one marker id.
        return t.marker_id, t.is_assist_ghost

    def retain(self, markers: Iterable[MarkerOverlayData]) -> None:
        """Forget the cones of markers no longer drawn."""
        keep = {self._slot(t) for t in markers}
        for slot in [s for s in self._entries if s not in keep]:
            del self._entries[slot]

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
            state.lens,
            tuple(state.grid_config),
            marker_floor_z(state, t),
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
        slot = self._slot(t)
        entry = self._entries.get(slot)
        if entry is None or entry[0] != key:
            entry = self._render(key, state, t, w, h, rgb, alpha, line_width, fill_alpha, shaded, scale)
            if entry is None:
                self._entries.pop(slot, None)
                return
            self._entries[slot] = entry
        _, picture, x0, y0 = entry
        paste(cr, picture, x0, y0)

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
        points = np.vstack([g.base_ring, g.top_ring, *g.edge_lines])
        if len(points) == 0:
            return None
        # With round joins the stroke reaches half its width past the rings and edges,
        # anti-aliasing one pixel more; nothing beyond the frame is ever shown.
        pad = int(math.ceil(line_width / 2.0)) + 2
        x0 = max(0, int(math.floor(points[:, 0].min())) - pad)
        y0 = max(0, int(math.floor(points[:, 1].min())) - pad)
        x1 = min(w, int(math.ceil(points[:, 0].max())) + pad)
        y1 = min(h, int(math.ceil(points[:, 1].max())) + pad)
        if x1 <= x0 or y1 <= y0:
            return None

        def paint(ctx: cairo.Context[Any]) -> None:
            _draw_cone(ctx, state, t, w, h, rgb, alpha, line_width, fill_alpha, shaded, geometry=g)

        return key, box_picture(paint, x0, y0, x1, y1, scale, None), x0, y0


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

    lens = state.lens
    if t.is_assist_ghost:
        if np.all(np.isfinite(project(cam, [(tx, ty, tz)], w, h, *lens)[0])):
            _draw_assist_ghost(cr, state, t, w, h, (r, g, b))
        return

    # Every point the marker is drawn from goes through one projection.
    z_line = bool(state.show_z_line and state.grid_config)
    ground_circle = bool(state.show_ground_circle and state.grid_config)
    floor = marker_floor_z(state, t) if z_line or ground_circle else 0.0
    lines = []
    if state.show_crosshair:
        cs = state.crosshair_size
        lines += [
            ((tx - cs, ty, tz), (tx + cs, ty, tz)),
            ((tx, ty - cs, tz), (tx, ty + cs, tz)),
            ((tx, ty, tz - cs), (tx, ty, tz + cs)),
        ]
    if z_line:
        lines.append(((tx, ty, tz), (tx, ty, floor)))
    n = _DISTORTION_SUBDIVISIONS if (lens[0] or lens[1]) else 1
    world: list[npt.ArrayLike] = [np.array([(tx, ty, tz), (tx + t.radius, ty, tz)], dtype=np.float64)]
    if lines:
        world.append(_subdivided(lines, n).reshape(-1, 3))
    if ground_circle:
        world.append(ground_circle_world_ring(tx, ty, floor, state.ground_circle_size))
    scr = project(cam, np.concatenate(world), w, h, *lens)
    if not np.all(np.isfinite(scr[0])):
        return
    polys = scr[2 : 2 + len(lines) * (n + 1)].reshape(len(lines), n + 1, 2)
    ring = scr[2 + len(lines) * (n + 1) :]

    sx, sy = float(scr[0, 0]), float(scr[0, 1])
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
        ch_r, ch_g, ch_b = parse_hex(state.crosshair_color)
        cr.set_source_rgba(ch_r, ch_g, ch_b, 1.0)
        cr.set_line_width(state.crosshair_thickness)
        for poly in polys[:3]:
            _stroke_polyline(cr, poly)
        cr.stroke()

    if z_line:
        cr.set_source_rgba(r, g, b, 0.8)
        cr.set_line_width(state.z_line_thickness)
        if _stroke_polyline(cr, polys[-1]):
            cr.stroke()

    if ground_circle:
        if _path_ring(cr, ring[np.all(np.isfinite(ring), axis=1)]):
            if state.ground_circle_filled:
                cr.set_source_rgba(r, g, b, 0.4)
                cr.fill()
            else:
                cr.set_source_rgba(r, g, b, 0.8)
                cr.set_line_width(2.0)
                cr.stroke()
