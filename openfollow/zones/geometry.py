# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Pure-function polygon geometry helpers for the zone trigger engine."""

from __future__ import annotations

from collections.abc import Sequence

Vertex = tuple[float, float]
Polygon = Sequence[Vertex]


def point_in_polygon(px: float, py: float, vertices: Polygon) -> bool:
    """Test whether (px, py) lies inside the polygon via ray casting."""
    n = len(vertices)
    if n < 3:
        return False

    inside = False
    x1, y1 = vertices[n - 1]
    for i in range(n):
        x2, y2 = vertices[i]
        if (y1 > py) != (y2 > py):
            x_intersect = x1 + (py - y1) * (x2 - x1) / (y2 - y1)
            if px < x_intersect:
                inside = not inside
        x1, y1 = x2, y2
    return inside


def _polygon_signed_area(vertices: Polygon) -> float:
    """Shoelace signed area (positive = counter-clockwise)."""
    n = len(vertices)
    total = 0.0
    x1, y1 = vertices[n - 1]
    for i in range(n):
        x2, y2 = vertices[i]
        total += (x1 * y2) - (x2 * y1)
        x1, y1 = x2, y2
    return total * 0.5


def shrink_polygon(vertices: Polygon, amount: float) -> list[Vertex]:
    """Offset each vertex inward by amount along the angle-bisector normal for hysteresis."""
    n = len(vertices)
    if n < 3 or amount <= 0.0:
        return [(float(x), float(y)) for x, y in vertices]

    # Reject degenerate polygons (coincident consecutive vertices)
    _eps = 1e-9
    for i in range(n):
        ax, ay = vertices[i]
        bx, by = vertices[(i + 1) % n]
        if (bx - ax) * (bx - ax) + (by - ay) * (by - ay) < _eps:
            return [(float(x), float(y)) for x, y in vertices]

    # Determine winding: push vertices toward interior
    orig_area = _polygon_signed_area(vertices)
    ccw = orig_area > 0.0
    sign = 1.0 if ccw else -1.0

    result: list[Vertex] = []
    for i in range(n):
        px, py = vertices[(i - 1) % n]
        cx, cy = vertices[i]
        nx, ny = vertices[(i + 1) % n]

        # Unit edge normals (pointing inward for CCW polygons)
        e1x, e1y = cx - px, cy - py
        e2x, e2y = nx - cx, ny - cy
        l1 = (e1x * e1x + e1y * e1y) ** 0.5
        l2 = (e2x * e2x + e2y * e2y) ** 0.5
        n1x, n1y = sign * (-e1y / l1), sign * (e1x / l1)
        n2x, n2y = sign * (-e2y / l2), sign * (e2x / l2)

        # Angle bisector direction; fallback for degenerate/hairpin vertices
        bx, by = n1x + n2x, n1y + n2y
        blen = (bx * bx + by * by) ** 0.5
        if blen < 1e-9:
            bx, by = n1x, n1y
            blen = 1.0

        # Offset distance along bisector
        bx /= blen
        by /= blen
        cos_half = n1x * bx + n1y * by
        cos_half = max(cos_half, 0.2)  # clamp to avoid runaway miter
        offset = amount / cos_half
        # Never let a single vertex travel more than half its shorter adjacent
        # edge – bounds the miter so a large hysteresis can't push a vertex
        # clear across a narrow polygon and expand/flip it.
        offset = min(offset, 0.5 * min(l1, l2))

        result.append((cx + bx * offset, cy + by * offset))

    # A large offset relative to the polygon's own width pushes vertices past
    # the opposite edge: the offset polygon self-intersects, flipping its
    # winding (sign) or collapsing its area. Either way the result is a
    # misplaced hysteresis polygon – fall back to no shrink for this zone.
    new_area = _polygon_signed_area(result)
    same_sign = (new_area >= 0.0) == (orig_area >= 0.0)
    if not same_sign or abs(new_area) < 0.05 * abs(orig_area):
        return [(float(x), float(y)) for x, y in vertices]
    return result
