# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Ground-grid geometry: the main grid's lines clipped to a polygon, and the floor under a point."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import numpy as np
import numpy.typing as npt


def _crossings(vertices: Sequence[Sequence[float]], axis: int, value: float) -> list[float]:
    """Where the line ``coord[axis] == value`` crosses the polygon's edges, sorted.

    Half-open edge test (``lo <= value < hi``), so a line through a vertex
    counts it once and the crossings pair up even-odd.
    """
    other = 1 - axis
    out: list[float] = []
    n = len(vertices)
    for i in range(n):
        a, b = vertices[i], vertices[(i + 1) % n]
        a0, b0 = float(a[axis]), float(b[axis])
        if (a0 <= value < b0) or (b0 <= value < a0):
            t = (value - a0) / (b0 - a0)
            out.append(float(a[other]) + t * (float(b[other]) - float(a[other])))
    out.sort()
    return out


def main_grid_steps(width: float, depth: float, spacing: float, cap: int) -> tuple[float, float]:
    """The gaps between the main grid's lines as the Operator Screen draws them, across X and along Y.

    It spreads its lines evenly from edge to edge, at most *cap* per axis, so a gap is the spacing
    only when the size is a whole multiple of it. ``(0, 0)`` when it draws no lines.
    """
    if not spacing > 0:
        return 0.0, 0.0
    n_x = min(max(int(width / spacing) + 1, 2), cap)
    n_y = min(max(int(depth / spacing) + 1, 2), cap)
    return width / (n_x - 1), depth / (n_y - 1)


def _line_values(lo: float, hi: float, origin: float, step: float, cap: int) -> list[float]:
    """Positions ``origin + k * step`` inside ``[lo, hi]``: every one, or an even share of them past *cap*."""
    if not step > 0:
        return []
    a, b = (lo - origin) / step, (hi - origin) / step
    if not (math.isfinite(a) and math.isfinite(b)):
        return []
    first, last = math.ceil(a), math.floor(b)
    count = last - first + 1
    if count <= 0:
        return []
    stride = -(-count // cap)
    return [origin + k * step for k in range(first, last + 1, stride)]


def grid_segments_in_polygon(
    vertices: Sequence[Sequence[float]],
    origin_x: float,
    origin_y: float,
    step_x: float,
    step_y: float,
    z: float,
    max_lines_per_axis: int,
) -> npt.NDArray[np.float64]:
    """World segments ``(N, 2, 3)`` of the grid through ``(origin_x, origin_y)``, its lines *step_x*
    apart across X and *step_y* along Y, inside the polygon (even-odd), at height *z*."""
    if len(vertices) < 3:
        return np.empty((0, 2, 3))
    xs = [float(v[0]) for v in vertices]
    ys = [float(v[1]) for v in vertices]
    segs: list[tuple[tuple[float, float, float], tuple[float, float, float]]] = []
    for y in _line_values(min(ys), max(ys), origin_y, step_y, max_lines_per_axis):
        hits = _crossings(vertices, 1, y)
        for x0, x1 in zip(hits[::2], hits[1::2], strict=False):
            if x1 > x0:
                segs.append(((x0, y, z), (x1, y, z)))
    for x in _line_values(min(xs), max(xs), origin_x, step_x, max_lines_per_axis):
        hits = _crossings(vertices, 0, x)
        for y0, y1 in zip(hits[::2], hits[1::2], strict=False):
            if y1 > y0:
                segs.append(((x, y0, z), (x, y1, z)))
    if not segs:
        return np.empty((0, 2, 3))
    return np.asarray(segs, dtype=np.float64)


def outline_segments(vertices: Sequence[Sequence[float]], z: float) -> npt.NDArray[np.float64]:
    """The closed polygon outline as world segments ``(N, 2, 3)`` at height *z*."""
    n = len(vertices)
    if n < 3:
        return np.empty((0, 2, 3))
    out = np.empty((n, 2, 3))
    for i in range(n):
        a, b = vertices[i], vertices[(i + 1) % n]
        out[i, 0] = (float(a[0]), float(a[1]), z)
        out[i, 1] = (float(b[0]), float(b[1]), z)
    return out


def floor_height(cfg: Any, x: float, y: float) -> float:
    """The floor under world ``(x, y)`` from config: the ground grid's height inside its polygon when it
    drops markers (Ground grid feature on, three corners, Ground grid on), else the main grid's."""
    from openfollow.configuration import feature_on

    gg = getattr(cfg, "ground_grid", None)
    if gg is not None and feature_on(cfg, "ground_grid") and gg.enabled and len(gg.vertices) >= 3:
        from openfollow.zones.geometry import point_in_polygon

        if point_in_polygon(x, y, gg.vertices):
            return float(gg.z_offset)
    return float(cfg.grid.z_offset)
