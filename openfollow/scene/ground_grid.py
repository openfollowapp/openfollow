# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Ground-grid geometry: main-grid-aligned lines clipped to a polygon."""

from __future__ import annotations

import math
from collections.abc import Sequence

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


def _line_values(lo: float, hi: float, origin: float, spacing: float, cap: int) -> list[float]:
    """Grid-line positions ``origin + k * spacing`` inside ``[lo, hi]``, at most *cap*."""
    first = math.ceil((lo - origin) / spacing)
    last = math.floor((hi - origin) / spacing)
    count = min(max(last - first + 1, 0), cap)
    return [origin + (first + k) * spacing for k in range(count)]


def grid_segments_in_polygon(
    vertices: Sequence[Sequence[float]],
    origin_x: float,
    origin_y: float,
    spacing: float,
    z: float,
    max_lines_per_axis: int,
) -> npt.NDArray[np.float64]:
    """World segments ``(N, 2, 3)`` of the grid through ``(origin_x, origin_y)``
    at *spacing*, inside the polygon (even-odd), at height *z*."""
    if len(vertices) < 3 or not spacing > 0:
        return np.empty((0, 2, 3))
    xs = [float(v[0]) for v in vertices]
    ys = [float(v[1]) for v in vertices]
    segs: list[tuple[tuple[float, float, float], tuple[float, float, float]]] = []
    for y in _line_values(min(ys), max(ys), origin_y, spacing, max_lines_per_axis):
        hits = _crossings(vertices, 1, y)
        for x0, x1 in zip(hits[::2], hits[1::2], strict=False):
            if x1 > x0:
                segs.append(((x0, y, z), (x1, y, z)))
    for x in _line_values(min(xs), max(xs), origin_x, spacing, max_lines_per_axis):
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
