# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""A path's curve: rounded corners, arc-length addressing, nearest point, travel."""

from __future__ import annotations

import math

import numpy as np
import pytest

from openfollow.paths.geometry import PathCurve, PathPoint, build_curve

pytestmark = pytest.mark.unit


def _curve(points: list[tuple[float, ...]], closed: bool = False) -> PathCurve:
    curve = build_curve([PathPoint(*p) for p in points], closed)
    assert curve is not None
    return curve


def _close(a: tuple[float, ...], b: tuple[float, ...], tol: float = 1e-6) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b, strict=True))


class TestStraight:
    def test_a_two_point_path_runs_straight_between_them(self) -> None:
        c = _curve([(0, 0, 0), (3, 4, 2)])
        assert c.length == pytest.approx(5.0)
        assert _close(c.point(2.5), (1.5, 2.0, 1.0))
        assert _close(c.tangent(1.0), (0.6, 0.8))

    def test_a_point_on_the_line_adds_no_corner(self) -> None:
        c = _curve([(0, 0, 0, 0), (2, 0, 0, 1.0), (5, 0, 0, 0)])
        assert c.length == pytest.approx(5.0)
        assert _close(c.point(2.0), (2.0, 0.0, 0.0))

    @pytest.mark.parametrize(
        "points",
        [[], [(1, 1, 0)], [(1, 1, 0), (1, 1, 2)], [(1, 1, 0), (1, 1 + 1e-9, 0)]],
    )
    def test_fewer_than_two_distinct_points_make_no_curve(self, points: list[tuple[float, ...]]) -> None:
        assert build_curve([PathPoint(*p) for p in points], False) is None

    def test_a_repeated_point_is_dropped(self) -> None:
        c = _curve([(0, 0, 0, 0), (2, 0, 0, 0), (2, 0, 0, 0), (2, 2, 0, 0)])
        assert c.length == pytest.approx(4.0)


class TestCorner:
    def test_an_arc_replaces_the_corner_with_the_point_s_radius(self) -> None:
        c = _curve([(0, 0, 0, 0), (4, 0, 0, 1.0), (4, 4, 0, 0)])
        assert c.length == pytest.approx(3 + 3 + math.pi / 2, abs=1e-3)
        mid = c.point(3 + math.pi / 4)
        assert _close(mid[:2], (3 + math.sin(math.pi / 4), 1 - math.cos(math.pi / 4)), tol=1e-3)

    def test_the_arc_stays_within_a_millimetre_of_the_circle(self) -> None:
        c = _curve([(0, 0, 0, 0), (4, 0, 0, 2.0), (4, 4, 0, 0)])
        centre = np.array([2.0, 2.0])
        on_arc = c.xy[(c.xy[:, 0] > 2.0 + 1e-9) & (c.xy[:, 1] < 2.0 - 1e-9)]
        assert len(on_arc) > 2
        assert np.allclose(np.hypot(*(on_arc - centre).T), 2.0, atol=1e-9)
        # Every chord's midpoint sags inside the circle by at most a millimetre.
        mids = (on_arc[:-1] + on_arc[1:]) / 2
        assert np.all(2.0 - np.hypot(*(mids - centre).T) <= 0.001)

    def test_a_right_turn_bulges_the_other_way(self) -> None:
        c = _curve([(0, 0, 0, 0), (4, 0, 0, 1.0), (4, -4, 0, 0)])
        mid = c.point(3 + math.pi / 4)
        assert _close(mid[:2], (3 + math.sin(math.pi / 4), -(1 - math.cos(math.pi / 4))), tol=1e-3)

    def test_a_zero_radius_keeps_the_corner_sharp(self) -> None:
        c = _curve([(0, 0, 0, 0), (4, 0, 0, 0), (4, 4, 0, 0)])
        assert c.length == pytest.approx(8.0)
        assert _close(c.point(4.0), (4.0, 0.0, 0.0))

    def test_a_radius_too_big_takes_half_the_shorter_segment(self) -> None:
        c = _curve([(0, 0, 0, 0), (2, 0, 0, 50.0), (2, 6, 0, 0)])
        # Half of the 2 m leg on each side: the arc runs from (1, 0) to (2, 1) with radius 1.
        assert c.length == pytest.approx(1 + math.pi / 2 + 5, abs=1e-3)
        assert _close(c.point(1.0)[:2], (1.0, 0.0), tol=1e-9)
        assert _close(c.point(1 + math.pi / 2)[:2], (2.0, 1.0), tol=1e-3)

    def test_two_corners_meeting_mid_segment_leave_no_empty_step(self) -> None:
        # Each corner takes half the 2 m middle segment, so one arc ends where the next begins.
        c = _curve([(0, 0, 0, 0), (2, 0, 0, 50.0), (2, 2, 0, 50.0), (0, 2, 0, 0)])
        # The direction of travel turns smoothly everywhere, also exactly where one arc meets the next.
        for s_k in c.s[:-1]:
            ahead = c.tangent(float(s_k) + 1e-3)
            assert np.dot(c.tangent(float(s_k)), ahead) > 0.99
        s, dist = c.nearest(3.0, 1.0)
        assert dist == pytest.approx(1.0, abs=1e-3)
        assert c.point(s)[:2] == pytest.approx((2.0, 1.0), abs=1e-3)

    def test_turning_straight_back_keeps_the_corner_sharp(self) -> None:
        c = _curve([(0, 0, 0, 0), (4, 0, 0, 1.0), (1, 0, 0, 0)])
        assert c.length == pytest.approx(7.0)
        assert _close(c.point(4.0), (4.0, 0.0, 0.0))


class TestDirections:
    CORNER = [(0, 0, 0, 0), (2, 0, 0, 0), (2, 2, 0, 0)]

    def test_on_a_sharp_corner_arriving_and_leaving_differ(self) -> None:
        c = _curve(self.CORNER)
        assert c.directions(2.0) == ((1.0, 0.0), (0.0, 1.0))
        assert c.tangent(2.0) == (0.0, 1.0)

    @pytest.mark.parametrize("s", [float(np.nextafter(2.0, 0.0)), float(np.nextafter(2.0, 3.0)), 2.0 - 1e-10])
    def test_a_hair_off_a_sharp_corner_is_on_it(self, s: float) -> None:
        assert _curve(self.CORNER).directions(s) == ((1.0, 0.0), (0.0, 1.0))

    @pytest.mark.parametrize(("s", "want"), [(1.0, (1.0, 0.0)), (3.0, (0.0, 1.0)), (2.0 - 1e-6, (1.0, 0.0))])
    def test_between_corners_both_follow_the_segment(self, s: float, want: tuple[float, float]) -> None:
        assert _curve(self.CORNER).directions(s) == (want, want)

    def test_an_open_end_has_one_direction(self) -> None:
        c = _curve(self.CORNER)
        assert c.directions(0.0) == ((1.0, 0.0), (1.0, 0.0))
        assert c.directions(c.length) == ((0.0, 1.0), (0.0, 1.0))

    @pytest.mark.parametrize("s", [0.0, 8.0, float(np.nextafter(8.0, 0.0)), -1e-12])
    def test_a_closed_path_arrives_at_its_start_along_its_last_segment(self, s: float) -> None:
        c = _curve([(0, 0, 0, 0), (2, 0, 0, 0), (2, 2, 0, 0), (0, 2, 0, 0)], closed=True)
        assert c.directions(s) == ((0.0, -1.0), (1.0, 0.0))


class TestHeight:
    def test_height_follows_the_straight_parts_linearly(self) -> None:
        c = _curve([(0, 0, 0, 0), (4, 0, 2, 1.0), (4, 4, 0, 0)])
        assert c.point(0.0)[2] == pytest.approx(0.0)
        assert c.point(1.5)[2] == pytest.approx(0.75)
        assert c.point(c.length)[2] == pytest.approx(0.0)

    def test_a_bend_in_height_alone_is_rounded_too(self) -> None:
        # Straight in plan, level for 4 m, then climbing 1 m in 2 m: the bend is only in height.
        c = _curve([(0, 0, 0, 0), (4, 0, 0, 2.0), (6, 0, 1, 0)])
        # A sharp bend would sit at height 0 at x = 4; rounded, the path is already rising there.
        s4, _ = c.nearest(4.0, 0.0)
        assert c.point(s4)[2] > 0.01
        # Level well before the bend, on the slope after it, and no step anywhere along the way.
        s2, _ = c.nearest(2.0, 0.0)
        assert c.point(s2)[2] == pytest.approx(0.0)
        assert c.point(c.length)[2] == pytest.approx(1.0)
        rise = np.diff(c.z) / np.diff(c.s)
        assert np.all(np.diff(rise) >= -1e-9)
        assert rise.max() == pytest.approx(0.5, abs=1e-3)

    def test_a_rounded_height_bend_stays_within_a_millimetre_of_its_circle(self) -> None:
        c = _curve([(0, 0, 0, 0), (4, 0, 0, 2.0), (6, 0, 1, 0)])
        on_arc = np.column_stack((c.xy, c.z))[(c.xy[:, 0] > 2.5) & (c.xy[:, 0] < 5.5)]
        # The arc's centre sits above the bend, square to the level stretch: x where it starts, z = radius.
        turn = math.atan2(1, 2)
        reach = 2.0 * math.tan(turn / 2)
        centre = np.array([4 - reach, 0.0, 2.0])
        arc = on_arc[(on_arc[:, 0] >= 4 - reach - 1e-9)]
        assert len(arc) > 2
        assert np.allclose(np.linalg.norm(arc - centre, axis=1), 2.0, atol=1e-6)


class TestClosed:
    SQUARE = [(0, 0, 0, 0), (2, 0, 0, 0), (2, 2, 0, 0), (0, 2, 0, 0)]

    def test_a_closed_path_joins_its_last_point_to_its_first(self) -> None:
        c = _curve(self.SQUARE, closed=True)
        assert c.closed
        assert c.length == pytest.approx(8.0)
        assert _close(c.point(7.0), (0.0, 1.0, 0.0))

    def test_its_first_point_is_rounded_too(self) -> None:
        c = _curve([(x, y, z, 0.5) for x, y, z, _ in self.SQUARE], closed=True)
        assert c.length == pytest.approx(4 * 1.0 + 2 * math.pi * 0.5, abs=1e-3)
        assert math.hypot(*c.point(0.0)[:2]) > 0.4

    def test_repeating_the_first_point_at_the_end_closes_nothing_twice(self) -> None:
        c = _curve([*self.SQUARE, (0, 0, 0, 0)], closed=True)
        assert c.length == pytest.approx(8.0)

    def test_two_points_cannot_close(self) -> None:
        c = _curve([(0, 0, 0), (2, 0, 0)], closed=True)
        assert not c.closed
        assert c.length == pytest.approx(2.0)

    def test_travel_and_positions_wrap_round(self) -> None:
        c = _curve(self.SQUARE, closed=True)
        assert c.advance(7.5, 1.0) == (pytest.approx(0.5), 0)
        assert c.advance(0.5, -1.0) == (pytest.approx(7.5), 0)
        assert _close(c.point(9.0), c.point(1.0))

    def test_the_offset_between_two_spots_goes_the_shorter_way(self) -> None:
        c = _curve(self.SQUARE, closed=True)
        assert c.offset(0.5, 7.5) == pytest.approx(-1.0)
        assert c.offset(7.5, 0.5) == pytest.approx(1.0)
        assert c.offset(1.0, 3.0) == pytest.approx(2.0)


class TestOpenTravel:
    def test_an_open_path_stops_travel_at_its_ends_and_says_which(self) -> None:
        c = _curve([(0, 0, 0), (5, 0, 0)])
        assert c.advance(4.0, 0.5) == (4.5, 0)
        assert c.advance(4.0, 3.0) == (5.0, 1)
        assert c.advance(1.0, -3.0) == (0.0, -1)

    def test_positions_beyond_the_ends_clamp(self) -> None:
        c = _curve([(0, 0, 0), (5, 0, 0)])
        assert c.wrap(9.0) == 5.0
        assert c.wrap(-1.0) == 0.0
        assert c.offset(1.0, 4.0) == 3.0


class TestNearest:
    def test_the_nearest_spot_and_its_distance(self) -> None:
        c = _curve([(0, 0, 0, 0), (4, 0, 0, 0), (4, 4, 0, 0)])
        s, dist = c.nearest(2.0, 1.0)
        assert s == pytest.approx(2.0)
        assert dist == pytest.approx(1.0)
        s, dist = c.nearest(5.0, 3.0)
        assert s == pytest.approx(7.0)
        assert dist == pytest.approx(1.0)

    def test_beyond_an_end_the_end_is_nearest(self) -> None:
        c = _curve([(0, 0, 0), (4, 0, 0)])
        s, dist = c.nearest(-3.0, 4.0)
        assert s == 0.0
        assert dist == pytest.approx(5.0)
