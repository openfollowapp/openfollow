# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Cairo draw pass for trigger-zone polygons on the video overlay."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import cairo
import numpy as np
import numpy.typing as npt

from openfollow.runtime.overlay_draw_scene import _DISTORTION_SUBDIVISIONS, _project_segments, project
from openfollow.runtime.overlay_draw_style import parse_hex
from openfollow.runtime.overlay_layers import keep
from openfollow.runtime.overlay_picture import ink_picture, target_font_options
from openfollow.runtime.overlay_state import OverlayState

_LABEL_SIZE = 13
_LABEL_PAD = 4.0


@dataclass(frozen=True)
class _Zone:
    outline: npt.NDArray[np.float64]
    color: str
    name: str
    is_occupied: bool
    count: int


def draw_zones(renderer: Any, cr: Any, state: OverlayState, w: int, h: int, cache: ZoneCache | None = None) -> None:
    """Render all configured zones as filled+stroked polygons with labels."""
    if not state.show_zones or not state.zone_polygons or state.camera_params is None:
        if cache is not None:
            cache.clear()
        return
    if cache is None:
        _paint_zones(renderer, cr, _zone_outlines(state, w, h))
    else:
        cache.draw(renderer, cr, state, w, h)


def _zone_outlines(state: OverlayState, w: int, h: int) -> list[_Zone]:
    """Each zone's outline on screen; a zone with a corner behind the camera is left out whole."""
    cam = state.camera_params
    z = state.zone_z_offset
    zones = []
    for vertices_xy, color_hex, name, is_occupied, count in state.zone_polygons:
        if len(vertices_xy) < 3:
            continue

        pts = [(vx, vy, z) for vx, vy in vertices_xy]
        # Under lens distortion each straight edge bows, so build the outline
        # from subdivided edges; otherwise the vertex projection is the outline.
        n = _DISTORTION_SUBDIVISIONS if (state.lens_k1 or state.lens_k2) else 1
        if n == 1:
            outline = project(cam, pts, w, h, *state.lens, state.pixel_aspect)
        else:
            loop = pts + [pts[0]]
            edges = [(loop[i], loop[i + 1]) for i in range(len(pts))]
            polys = _project_segments(cam, edges, w, h, *state.lens, n, state.pixel_aspect)
            # Drop each edge's last point – it repeats the next edge's first.
            outline = np.vstack([poly[:-1] for poly in polys])

        # A zone is one closed path: dropping individual non-finite vertices
        # (e.g. a corner behind the camera) would reconnect non-adjacent
        # neighbours and warp the outline. Skip the whole zone instead.
        if not np.all(np.isfinite(outline)):
            continue
        zones.append(_Zone(outline, color_hex, name, is_occupied, count))
    return zones


def _label(zone: _Zone) -> str:
    return f"{zone.name} ({zone.count})" if zone.is_occupied else zone.name


def _paint_zones(renderer: Any, cr: Any, zones: list[_Zone]) -> None:
    cr.save()
    # Grayscale labels: subpixel coverage cannot survive the cached picture's one alpha channel.
    options = cr.get_font_options()
    options.set_antialias(cairo.ANTIALIAS_GRAY)
    cr.set_font_options(options)
    for zone in zones:
        outline = zone.outline
        r, g, b = parse_hex(zone.color)
        fill_alpha = 0.35 if zone.is_occupied else 0.15
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

        # Label at polygon centroid. Without distortion ``outline`` is the corner
        # projection; with it, the mean of the bowed boundary points.
        if zone.name:
            cx = float(np.mean(outline[:, 0]))
            cy = float(np.mean(outline[:, 1]))
            label = _label(zone)
            renderer._set_ui_font(cr, _LABEL_SIZE, bold=zone.is_occupied)
            ext = cr.text_extents(label)
            rect_w = ext.width + _LABEL_PAD * 2
            rect_h = ext.height + _LABEL_PAD * 2
            rect_x = cx - rect_w / 2.0
            rect_y = cy - rect_h / 2.0
            cr.set_source_rgba(0.0, 0.0, 0.0, 0.55)
            cr.rectangle(rect_x, rect_y, rect_w, rect_h)
            cr.fill()
            cr.set_source_rgba(r, g, b, 1.0)
            cr.move_to(rect_x + _LABEL_PAD, rect_y + _LABEL_PAD + ext.height)
            cr.show_text(label)
    cr.restore()


class ZoneCache:
    """Every zone as one picture, drawn again only when a zone, its occupancy or the view changed.

    The Operator Screen draws a picture at most once per display refresh; zones that did not
    change are then one paint instead of each zone's projection, fill, outline and label.
    """

    def __init__(self) -> None:
        self._entry: tuple[tuple[Any, ...], cairo.ImageSurface | None, int, int] | None = None

    def clear(self) -> None:
        """Let go of the picture, which can run to a hundred megabytes on a HiDPI 4K screen."""
        self._entry = None

    def draw(self, renderer: Any, cr: Any, state: OverlayState, w: int, h: int) -> None:
        cam = state.camera_params
        if cam is None:
            return
        scale = cr.get_target().get_device_scale()
        options = target_font_options(cr)
        # The labels are always gray (``_paint_zones``), so the screen's antialias does not change the picture.
        options.set_antialias(cairo.ANTIALIAS_GRAY)
        # Every input a zone is projected or drawn from; one missing here freezes on screen.
        key = (
            tuple(
                (tuple(tuple(v) for v in vertices), color, name, is_occupied, count)
                for vertices, color, name, is_occupied, count in state.zone_polygons
            ),
            state.zone_z_offset,
            tuple(float(v) for v in cam),
            state.lens,
            state.pixel_aspect,
            w,
            h,
            scale,
            options,
        )
        if self._entry is None or self._entry[0] != key:
            self._entry = (key, *self._render(renderer, _zone_outlines(state, w, h), w, h, scale, options))
        _, picture, x0, y0 = self._entry
        if picture is not None and not keep(cr, picture, x0, y0):
            cr.set_source_surface(picture, x0, y0)
            cr.paint()

    @staticmethod
    def _render(
        renderer: Any, zones: list[_Zone], w: int, h: int, scale: tuple[float, float], options: cairo.FontOptions
    ) -> tuple[cairo.ImageSurface | None, int, int]:
        if not zones:
            return None, 0, 0
        return ink_picture(lambda ctx: _paint_zones(renderer, ctx, zones), w, h, scale, options)
