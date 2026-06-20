# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for zone geometry: point-in-polygon and miter-clamped polygon shrink."""

from __future__ import annotations

import math

import pytest

from openfollow.zones.geometry import point_in_polygon, shrink_polygon

pytestmark = pytest.mark.unit

SQUARE = [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0)]
# Concave L-shape
L_SHAPE = [
    (0.0, 0.0),
    (4.0, 0.0),
    (4.0, 2.0),
    (2.0, 2.0),
    (2.0, 4.0),
    (0.0, 4.0),
]


class TestPointInPolygon:
    def test_center_is_inside(self) -> None:
        assert point_in_polygon(2.0, 2.0, SQUARE) is True

    def test_far_point_is_outside(self) -> None:
        assert point_in_polygon(10.0, 10.0, SQUARE) is False

    def test_negative_point_is_outside(self) -> None:
        assert point_in_polygon(-1.0, 2.0, SQUARE) is False

    def test_concave_notch_is_outside(self) -> None:
        # Point in the "missing" corner of the L-shape
        assert point_in_polygon(3.0, 3.0, L_SHAPE) is False

    def test_concave_arms_are_inside(self) -> None:
        assert point_in_polygon(1.0, 3.0, L_SHAPE) is True
        assert point_in_polygon(3.0, 1.0, L_SHAPE) is True

    def test_empty_polygon_is_outside(self) -> None:
        assert point_in_polygon(0.0, 0.0, []) is False

    def test_degenerate_line_is_outside(self) -> None:
        assert point_in_polygon(0.5, 0.0, [(0.0, 0.0), (1.0, 0.0)]) is False

    def test_counterclockwise_and_clockwise_winding_give_same_result(self) -> None:
        ccw = SQUARE
        cw = list(reversed(SQUARE))
        for x in (0.5, 1.5, 3.5):
            for y in (0.5, 2.0, 3.5):
                assert point_in_polygon(x, y, ccw) == point_in_polygon(x, y, cw)


class TestShrinkPolygon:
    def test_shrink_zero_returns_copy(self) -> None:
        result = shrink_polygon(SQUARE, 0.0)
        assert result == SQUARE
        assert result is not SQUARE

    def test_shrink_negative_is_noop(self) -> None:
        result = shrink_polygon(SQUARE, -0.5)
        assert result == SQUARE

    def test_shrink_square_moves_each_vertex_inward(self) -> None:
        amount = 0.5
        shrunken = shrink_polygon(SQUARE, amount)
        expected = [(0.5, 0.5), (3.5, 0.5), (3.5, 3.5), (0.5, 3.5)]
        assert len(shrunken) == 4
        for (sx, sy), (ex, ey) in zip(shrunken, expected, strict=False):
            assert math.isclose(sx, ex, abs_tol=1e-6)
            assert math.isclose(sy, ey, abs_tol=1e-6)

    def test_shrunken_square_excludes_formerly_interior_points(self) -> None:
        shrunken = shrink_polygon(SQUARE, 0.5)
        # Point near the original corner is strictly inside the square, but
        # the 0.5 inward shrink pushes the new edges past it.
        assert point_in_polygon(0.1, 0.1, SQUARE) is True
        assert point_in_polygon(0.1, 0.1, shrunken) is False
        # Center remains inside
        assert point_in_polygon(2.0, 2.0, shrunken) is True

    def test_shrink_clockwise_polygon_also_moves_inward(self) -> None:
        cw = list(reversed(SQUARE))
        shrunken = shrink_polygon(cw, 0.5)
        # Original center should still classify as inside shrunken CW polygon
        assert point_in_polygon(2.0, 2.0, shrunken) is True
        # Near the original corner should be excluded
        assert point_in_polygon(0.1, 0.1, shrunken) is False

    def test_shrink_degenerate_polygon_returns_copy(self) -> None:
        line = [(0.0, 0.0), (1.0, 0.0)]
        assert shrink_polygon(line, 0.1) == line

    def test_shrink_polygon_with_coincident_consecutive_vertices_is_noop(self) -> None:
        """A polygon with two coincident consecutive vertices is
        degenerate – ``shrink_polygon`` returns a copy rather than
        producing arbitrarily-offset garbage. Covers line 77."""
        from openfollow.zones.geometry import shrink_polygon

        coincident = [(0.0, 0.0), (1.0, 0.0), (1.0, 0.0), (0.0, 1.0)]
        assert shrink_polygon(coincident, 0.1) == coincident

    def test_shrink_polygon_at_hairpin_vertex_uses_n1_fallback(self) -> None:
        from openfollow.zones.geometry import shrink_polygon

        # At vertex B=(10, 0), the polygon reverses direction:
        # A=(0,0) → B=(10,0) → C=(-10,0) → D=(0,10) → A.
        # The inward normals at B are (0, -1) and (0, 1), summing to
        # zero – bisector length < 1e-9, fallback fires.
        hairpin = [(0.0, 0.0), (10.0, 0.0), (-10.0, 0.0), (0.0, 10.0)]
        shrunken = shrink_polygon(hairpin, 0.5)
        assert len(shrunken) == 4
        # All output coords are finite (the fallback prevented NaN).
        import math as _math

        for x, y in shrunken:
            assert _math.isfinite(x) and _math.isfinite(y)

    def test_shrink_bounds_offset_at_very_acute_vertex(self) -> None:
        # Narrow isoceles triangle – apex at (10, 0) has a very small angle.
        narrow = [(0.0, -0.1), (0.0, 0.1), (10.0, 0.0)]
        amount = 0.05
        shrunken = shrink_polygon(narrow, amount)
        assert len(shrunken) == 3
        # All coordinates must remain finite.
        for x, y in shrunken:
            assert math.isfinite(x) and math.isfinite(y)
        # Clamp is cos_half >= 0.2, so max miter offset is amount / 0.2 = 5 *
        # amount. No vertex can move further than that from its original.
        max_offset = amount / 0.2 + 1e-9
        for (sx, sy), (ox, oy) in zip(shrunken, narrow, strict=False):
            dx, dy = sx - ox, sy - oy
            assert (dx * dx + dy * dy) ** 0.5 <= max_offset
        # A point clearly inside the original base should be inside the
        # shrunken polygon – sharpness must not destroy the valid interior.
        assert point_in_polygon(1.0, 0.0, shrunken) is True

    def test_shrink_narrow_strip_does_not_misplace_hysteresis_polygon(self) -> None:
        # A thin tripwire strip with a hysteresis larger than half its width
        # would, without a bound, offset vertices past the opposite edge and
        # invert – points clearly OUTSIDE the real zone then test as inside.
        strip = [(0.0, 0.0), (5.0, 0.0), (5.0, 0.2), (0.0, 0.2)]
        shrunken = shrink_polygon(strip, 0.3)
        # Points outside the real strip must never be inside the exit polygon.
        assert point_in_polygon(2.5, 0.25, shrunken) is False
        assert point_in_polygon(2.5, -0.05, shrunken) is False
        # The strip's own centre line stays inside (hysteresis keeps the core).
        assert point_in_polygon(2.5, 0.1, shrunken) is True

    def test_shrink_falls_back_when_offset_would_invert_winding(self) -> None:
        # A flat needle triangle with a huge hysteresis: the apex offsets past
        # the base, flipping the signed-area sign. Guard returns the original.
        needle = [(0.0, 0.0), (1.0, 0.0), (0.5, 0.05)]
        assert shrink_polygon(needle, 10.0) == needle

    def test_shrink_falls_back_when_offset_collapses_area(self) -> None:
        # A small equilateral triangle with a huge hysteresis: vertices
        # converge toward the incentre, collapsing the area below the keep
        # threshold while keeping the winding sign. Guard returns the original.
        equilateral = [(0.0, 0.0), (1.0, 0.0), (0.5, math.sqrt(3) / 2)]
        assert shrink_polygon(equilateral, 10.0) == equilateral
