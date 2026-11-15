# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Michel Honold and Paul Hermann
"""A path's curve: rounded corners, arc-length addressing, nearest point, travel."""

from __future__ import annotations

import math

import numpy as np
import pytest

from openfollow.paths.geometry import PathCurve, PathPoint, build_curve
from openfollow.paths.network import PathNetwork, Place

pytestmark = pytest.mark.unit


def _curve(points: list[tuple[float, ...]], closed: bool = False) -> PathCurve:
    curve = build_curve([PathPoint(*p) for p in points], closed)
    assert curve is not None
    return curve


def _close(a: tuple[float, ...], b: tuple[float, ...], tol: float = 1e-6) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b, strict=True))


def _cos(degrees: float) -> float:
    return math.cos(math.radians(degrees))


def _sin(degrees: float) -> float:
    return math.sin(math.radians(degrees))


def _gap(a: PathCurve, b: PathCurve) -> float:
    """How far apart two curves get: the farthest any sample of one lies from the other."""
    return max(
        max(b.nearest(float(x), float(y))[1] for x, y in a.xy),
        max(a.nearest(float(x), float(y))[1] for x, y in b.xy),
    )


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


class TestTinyPaths:
    """Points a few micrometres apart, whose arcs take sub-micrometre steps."""

    CASES = [
        ([(0, 0, 0, 0), (1.5e-6, 0, 0, 0.5), (3e-6, 5e-7, 0, 0)], False),
        ([(0, 0, 0, 0.5), (2e-6, 0, 0, 0.5), (1e-6, 2e-6, 0, 0.5)], True),
        ([(0, 0, 0, 0.5), (2e-6, 0, 0, 0.5), (1e-6, 2e-6, 0, 0.5)], False),
    ]

    @pytest.mark.parametrize(("points", "closed"), CASES)
    def test_a_curve_built_from_them_has_distinct_steps(self, points: list[tuple[float, ...]], closed: bool) -> None:
        c = build_curve([PathPoint(*p) for p in points], closed)
        assert c is not None
        assert len(c.xy) >= (4 if c.closed else 2)
        assert np.all(np.diff(c.s) > 1e-6)
        assert c.length > 0.0
        if c.closed:
            assert tuple(c.xy[0]) == tuple(c.xy[-1])

    @pytest.mark.parametrize(("points", "closed"), CASES)
    def test_it_can_be_walked_and_projected_onto(self, points: list[tuple[float, ...]], closed: bool) -> None:
        c = build_curve([PathPoint(*p) for p in points], closed)
        assert c is not None
        net = PathNetwork([c])
        place = net.nearest(1e-6, 1e-6)
        assert place is not None
        assert c.tangent(place.s) is not None
        assert net.walk(Place(0, 0.0), 1.0, 0.0, 1.0).place.path == 0

    def test_a_closed_one_left_with_two_distinct_samples_is_open(self) -> None:
        c = build_curve(
            [PathPoint(0, 0, 0, 0.5), PathPoint(2.5e-6, 0, 0, 0.5), PathPoint(1.25e-6, 1.5e-6, 0, 0.5)], True
        )
        assert c is not None
        assert not c.closed
        assert len(c.xy) == 2

    def test_one_left_with_a_single_distinct_sample_is_no_curve(self) -> None:
        points = [PathPoint(0, 0, 0, 0.5), PathPoint(1.01e-6, 0, 0, 0.5), PathPoint(5e-7, 1.01e-6, 0, 0.5)]
        assert build_curve(points, True) is None


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

    @pytest.mark.parametrize("turn", [30.0, 90.0, 120.0])
    def test_a_turn_up_to_120_degrees_keeps_the_point_s_radius(self, turn: float) -> None:
        c = _curve([(0, 0, 0, 0), (4, 0, 0, 0.5), (4 + 4 * _cos(turn), 4 * _sin(turn), 0, 0)])
        reach = 0.5 * math.tan(math.radians(turn) / 2)
        assert c.length == pytest.approx(8 - 2 * reach + 0.5 * math.radians(turn), abs=1e-3)

    @pytest.mark.parametrize("turn", [121.0, 150.0, 170.0, 179.0])
    def test_a_sharper_turn_meets_its_segments_three_radii_over_tan_half_the_turn_from_the_point(
        self, turn: float
    ) -> None:
        c = _curve([(0, 0, 0, 0), (4, 0, 0, 0.5), (4 + 4 * _cos(turn), 4 * _sin(turn), 0, 0)])
        half_tan = math.tan(math.radians(turn) / 2)
        reach = 3 * 0.5 / half_tan
        radius = reach / half_tan
        assert c.length == pytest.approx(8 - 2 * reach + radius * math.radians(turn), abs=1e-3)
        middle = radius * (1 / math.cos(math.radians(turn) / 2) - 1)
        assert c.nearest(4.0, 0.0)[1] == pytest.approx(middle, abs=1e-3)

    def test_a_150_degree_turn_passes_its_point_closer_than_a_right_angle_does(self) -> None:
        c = _curve([(0, 0, 0, 0), (4, 0, 0, 0.5), (4 + 4 * _cos(150), 4 * _sin(150), 0, 0)])
        assert c.nearest(4.0, 0.0)[1] == pytest.approx(0.308, abs=2e-3)

    def test_an_out_and_back_runs_out_to_its_far_point(self) -> None:
        c = _curve([(0, 0, 0, 0), (4, 0, 0, 0.5), (0, 0.01, 0, 0)])
        assert c.nearest(4.0, 0.0)[1] <= 0.01
        assert c.length == pytest.approx(8.0, abs=0.01)

    @pytest.mark.parametrize("back_y", [1e-9, -1e-9, 1e-6, -1e-6, 1e-3, -1e-3])
    def test_a_turn_close_to_straight_back_changes_the_curve_as_little(self, back_y: float) -> None:
        straight_back = _curve([(0, 0, 0, 0), (4, 0, 0, 0.5), (0, 0, 0, 0)])
        near = _curve([(0, 0, 0, 0), (4, 0, 0, 0.5), (0, back_y, 0, 0)])
        assert _gap(near, straight_back) <= abs(back_y) * 1.01

    @pytest.mark.parametrize("radius", [0.0, 1.0])
    def test_turning_straight_back_reaches_the_point(self, radius: float) -> None:
        c = _curve([(0, 0, 0, 0), (4, 0, 0, radius), (1, 0, 0, 0)])
        assert c.length == pytest.approx(7.0)
        assert _close(c.point(4.0), (4.0, 0.0, 0.0))

    def test_a_large_radius_nearly_straight_back_passes_close_to_the_point(self) -> None:
        # 179.97 degrees at 20 m: the arc meets each segment 16 mm short of the point.
        turn = 179.97
        c = _curve([(0, 0, 0, 0), (100, 0, 0, 20.0), (100 + 100 * _cos(turn), 100 * _sin(turn), 0, 0)])
        reach = 3 * 20.0 / math.tan(math.radians(turn) / 2)
        assert c.nearest(100.0, 0.0)[1] == pytest.approx(reach, abs=1e-4)

    def test_a_sharper_turn_still_takes_at_most_half_the_shorter_segment(self) -> None:
        c = _curve([(0, 0, 0, 0), (1, 0, 0, 50.0), (1 + 4 * _cos(150), 4 * _sin(150), 0, 0)])
        radius = 0.5 / math.tan(math.radians(75))
        assert _close(c.point(0.5), (0.5, 0.0, 0.0), tol=1e-9)
        assert c.length == pytest.approx(0.5 + radius * math.radians(150) + 3.5, abs=1e-3)


class TestCorners:
    def test_an_open_path_s_corners_are_its_inner_points(self) -> None:
        c = _curve([(0, 0, 0, 0.5), (2, 0, 1, 0.5), (2, 2, 0, 0), (0, 3, 0, 0.5)])
        assert c.corners.tolist() == [[2.0, 0.0], [2.0, 2.0]]

    def test_every_point_of_a_closed_path_is_a_corner(self) -> None:
        c = _curve([(0, 0, 0), (2, 0, 0), (2, 2, 0)], closed=True)
        assert c.corners.tolist() == [[0.0, 0.0], [2.0, 0.0], [2.0, 2.0]]

    def test_a_two_point_path_has_no_corners(self) -> None:
        assert _curve([(0, 0, 0), (2, 0, 0)]).corners.shape == (0, 2)


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


class TestToVertex:
    CORNER = [(0, 0, 0, 0), (2, 0, 0, 0), (2, 2, 0, 0)]
    SQUARE = [(0, 0, 0, 0), (2, 0, 0, 0), (2, 2, 0, 0), (0, 2, 0, 0)]

    @pytest.mark.parametrize(("s", "sign", "want"), [(0.5, 1, 1.5), (0.5, -1, 0.5), (3.0, -1, 1.0), (3.0, 1, 1.0)])
    def test_the_distance_to_the_next_vertex_either_way(self, s: float, sign: int, want: float) -> None:
        assert _curve(self.CORNER).to_vertex(s, sign) == pytest.approx(want)

    @pytest.mark.parametrize("s", [2.0, float(np.nextafter(2.0, 0.0)), float(np.nextafter(2.0, 3.0))])
    @pytest.mark.parametrize("sign", [1, -1])
    def test_on_a_vertex_the_next_one_is_beyond_it(self, s: float, sign: int) -> None:
        assert _curve(self.CORNER).to_vertex(s, sign) == pytest.approx(2.0)

    @pytest.mark.parametrize(("s", "sign"), [(4.0, 1), (0.0, -1)])
    def test_past_an_open_end_there_is_none(self, s: float, sign: int) -> None:
        assert _curve(self.CORNER).to_vertex(s, sign) == math.inf

    @pytest.mark.parametrize(("s", "sign", "want"), [(7.5, 1, 0.5), (0.0, 1, 2.0), (0.0, -1, 2.0), (0.5, -1, 0.5)])
    def test_a_closed_curve_wraps_round_its_first_point(self, s: float, sign: int, want: float) -> None:
        assert _curve(self.SQUARE, closed=True).to_vertex(s, sign) == pytest.approx(want)

    def test_every_vertex_of_an_arc_counts(self) -> None:
        c = _curve([(0, 0, 0, 0), (4, 0, 0, 1.0), (4, 4, 0, 0)])
        s_in = float(c.s[1])
        assert c.to_vertex(s_in, 1) == pytest.approx(float(c.s[2] - c.s[1]))


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
