# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Cairo draw pass for trigger-zone polygons on the video overlay: outline and fill."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import cairo
import numpy as np
import numpy.typing as npt

from openfollow.runtime.overlay_draw_scene import (
    _DISTORTION_SUBDIVISIONS,
    _project_segments,
    _stroke_polyline,
    _subdivided,
    project,
)
from openfollow.runtime.overlay_draw_style import parse_hex
from openfollow.runtime.overlay_picture import ink_picture, paste
from openfollow.runtime.overlay_state import OverlayState
from openfollow.zones.geometry import hidden_by_zone

_FLOOR_ALPHA = 0.7
_BEHIND_ALPHA = 0.18
_POST_DASH = (2.0, 4.0)


@dataclass(frozen=True)
class _FloorRun:
    """A stretch of a raised zone's floor outline or of a corner line, all in front of or all behind the zone."""

    line: npt.NDArray[np.float64]
    behind: bool
    post: bool


@dataclass(frozen=True)
class _Zone:
    outline: npt.NDArray[np.float64]
    color: str
    is_occupied: bool
    floor: tuple[_FloorRun, ...] = ()


def draw_zones(cr: Any, state: OverlayState, w: int, h: int, cache: ZoneCache | None = None) -> None:
    """Render all configured zones as filled and stroked polygons."""
    if not state.show_zones or not state.zone_polygons or state.camera_params is None:
        if cache is not None:
            cache.clear()
        return
    if cache is None:
        _paint_zones(cr, _zone_outlines(state, w, h))
    else:
        cache.draw(cr, state, w, h)


def _zone_outlines(state: OverlayState, w: int, h: int) -> list[_Zone]:
    """Each zone's outline on screen; a zone with a corner behind the camera is left out whole."""
    cam = state.camera_params
    zones = []
    for vertices_xy, color_hex, is_occupied, height in state.zone_polygons:
        if len(vertices_xy) < 3:
            continue
        z = state.zone_z_offset + height

        pts = [(vx, vy, z) for vx, vy in vertices_xy]
        # Under lens distortion each straight edge bows, so build the outline
        # from subdivided edges; otherwise the vertex projection is the outline.
        n = _DISTORTION_SUBDIVISIONS if (state.lens_k1 or state.lens_k2) else 1
        if n == 1:
            outline = project(cam, pts, w, h, *state.lens)
        else:
            loop = pts + [pts[0]]
            edges = [(loop[i], loop[i + 1]) for i in range(len(pts))]
            polys = _project_segments(cam, edges, w, h, *state.lens, n)
            # Drop each edge's last point – it repeats the next edge's first.
            outline = np.vstack([poly[:-1] for poly in polys])

        # A zone is one closed path: dropping individual non-finite vertices
        # (e.g. a corner behind the camera) would reconnect non-adjacent
        # neighbours and warp the outline. Skip the whole zone instead.
        if not np.all(np.isfinite(outline)):
            continue
        floor: tuple[_FloorRun, ...] = ()
        if height > 0:
            floor = _floor_runs(
                tuple((float(x), float(y)) for x, y in vertices_xy),
                float(state.zone_z_offset),
                float(z),
                tuple(np.asarray(cam, dtype=np.float64).tolist()),
                tuple(state.lens),
                w,
                h,
            )
        zones.append(_Zone(outline, color_hex, is_occupied, floor))
    return zones


@lru_cache(maxsize=64)
def _floor_runs(
    vertices: tuple[tuple[float, float], ...],
    bottom: float,
    top: float,
    cam: tuple[float, ...],
    lens: tuple[float, ...],
    w: int,
    h: int,
) -> tuple[_FloorRun, ...]:
    """A raised zone's floor outline and corner lines on screen, cut where they pass behind the zone."""
    eye = (cam[0], cam[1], cam[2])
    n = len(vertices)
    segments = [((*vertices[i], bottom), (*vertices[(i + 1) % n], bottom)) for i in range(n)]
    segments += [((x, y, bottom), (x, y, top)) for x, y in vertices]
    world = _subdivided(segments, _DISTORTION_SUBDIVISIONS)
    screen = project(np.asarray(cam), world.reshape(-1, 3), w, h, *lens).reshape(world.shape[0], -1, 2)
    runs = []
    for index, (points, line) in enumerate(zip(world, screen, strict=True)):
        mids = ((points[:-1] + points[1:]) / 2).tolist()
        behind = [hidden_by_zone((m[0], m[1], m[2]), eye, vertices, bottom, top) for m in mids]
        start = 0
        for k in range(1, len(behind) + 1):
            if k == len(behind) or behind[k] != behind[start]:
                runs.append(_FloorRun(line[start : k + 1], behind[start], index >= n))
                start = k
    return tuple(runs)


def _paint_zones(cr: Any, zones: list[_Zone]) -> None:
    cr.save()
    for zone in zones:
        outline = zone.outline
        r, g, b = parse_hex(zone.color)
        fill_alpha = 0.35 if zone.is_occupied else 0.15
        if zone.floor:
            _paint_floor(cr, zone.floor, (r, g, b))
        stroke_alpha = 1.0 if zone.is_occupied else 0.7

        cr.set_source_rgba(r, g, b, fill_alpha)
        cr.move_to(outline[0, 0], outline[0, 1])
        for i in range(1, len(outline)):
            cr.line_to(outline[i, 0], outline[i, 1])
        cr.close_path()
        cr.fill_preserve()
        cr.set_source_rgba(r, g, b, stroke_alpha)
        cr.set_line_width(2.0 if zone.is_occupied else 1.5)
        cr.stroke()
    cr.restore()


def _paint_floor(cr: Any, runs: tuple[_FloorRun, ...], rgb: tuple[float, float, float]) -> None:
    """The floor outline solid and the corner lines dotted, each fainter where it is behind the zone."""
    cr.save()
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    for post in (False, True):
        cr.set_dash(_POST_DASH if post else ())
        cr.set_line_width(2.0 if post else 1.5)
        for behind in (True, False):
            drew = False
            for run in runs:
                if run.post is post and run.behind is behind:
                    drew = _stroke_polyline(cr, run.line) or drew
            if drew:
                cr.set_source_rgba(*rgb, _BEHIND_ALPHA if behind else _FLOOR_ALPHA)
                cr.stroke()
    cr.restore()


class ZoneCache:
    """Every zone as one picture, drawn again only when a zone, its occupancy or the view changed.

    The Operator Screen draws a picture at most once per display refresh; zones that did not
    change are then one paint instead of each zone's projection, fill and outline.
    """

    def __init__(self) -> None:
        self._entry: tuple[tuple[Any, ...], cairo.ImageSurface | None, int, int] | None = None

    def clear(self) -> None:
        """Let go of the picture, which can run to a hundred megabytes on a HiDPI 4K screen."""
        self._entry = None

    def draw(self, cr: Any, state: OverlayState, w: int, h: int) -> None:
        cam = state.camera_params
        if cam is None:
            return
        scale = cr.get_target().get_device_scale()
        # Every input a zone is projected or drawn from; one missing here freezes on screen.
        key = (
            tuple(
                (tuple(tuple(v) for v in vertices), color, is_occupied, height)
                for vertices, color, is_occupied, height in state.zone_polygons
            ),
            state.zone_z_offset,
            tuple(float(v) for v in cam),
            state.lens,
            w,
            h,
            scale,
        )
        if self._entry is None or self._entry[0] != key:
            self._entry = (key, *self._render(_zone_outlines(state, w, h), w, h, scale))
        _, picture, x0, y0 = self._entry
        paste(cr, picture, x0, y0)

    @staticmethod
    def _render(
        zones: list[_Zone], w: int, h: int, scale: tuple[float, float]
    ) -> tuple[cairo.ImageSurface | None, int, int]:
        if not zones:
            return None, 0, 0
        return ink_picture(lambda ctx: _paint_zones(ctx, zones), w, h, scale, cairo.FontOptions())
