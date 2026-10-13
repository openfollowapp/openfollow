# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The path network: junctions, walking a locked marker, steering at crossings, gliding."""

from __future__ import annotations

import math
import random

import pytest

from openfollow.paths.geometry import PathCurve, PathPoint, build_curve
from openfollow.paths.network import Junction, PathNetwork, Place, find_junctions

pytestmark = pytest.mark.unit


def _curve(points: list[tuple[float, ...]], closed: bool = False) -> PathCurve:
    curve = build_curve([PathPoint(*p) for p in points], closed)
    assert curve is not None
    return curve


# A cross: H runs x -5..5 along y = 0, V runs y -5..5 along x = 0; they meet at s = 5 on both.
H = _curve([(-5, 0, 0), (5, 0, 0)])
V = _curve([(0, -5, 0), (0, 5, 0)])


def _at(net: PathNetwork, place: Place) -> tuple[float, float]:
    x, y, _ = net.point(place)
    return round(x, 6), round(y, 6)


def _push(degrees: float, speed: float = 1.0) -> tuple[float, float]:
    return speed * math.cos(math.radians(degrees)), speed * math.sin(math.radians(degrees))


class TestJunctions:
    def test_two_crossing_paths_meet_where_they_cross(self) -> None:
        assert find_junctions([H, V]) == [Junction(0, pytest.approx(5.0), 1, pytest.approx(5.0))]

    def test_paths_crossing_at_different_heights_do_not_meet(self) -> None:
        bridge = _curve([(0, -5, 0.5), (0, 5, 0.5)])
        assert find_junctions([H, bridge]) == []

    def test_a_small_height_difference_still_meets(self) -> None:
        near = _curve([(0, -5, 0.08), (0, 5, 0.08)])
        assert len(find_junctions([H, near])) == 1

    def test_a_path_ending_on_another_joins_it_there(self) -> None:
        branch = _curve([(2, 0.05, 0), (2, 4, 0)])
        assert find_junctions([H, branch]) == [Junction(0, pytest.approx(7.0), 1, 0.0)]

    def test_a_path_ending_short_of_another_does_not(self) -> None:
        branch = _curve([(2, 0.3, 0), (2, 4, 0)])
        assert find_junctions([H, branch]) == []

    def test_a_crossing_on_a_corner_point_counts_once(self) -> None:
        # The crossing sits exactly on V2's middle point, the end of one segment and the start of the next.
        v2 = _curve([(0, -5, 0, 0), (0, 0, 0, 0), (0, 5, 0, 0)])
        assert len(find_junctions([H, v2])) == 1

    def test_a_path_crossing_itself_has_no_junction(self) -> None:
        loop = _curve([(0, 0, 0), (4, 0, 0), (4, 2, 0), (2, 2, 0), (2, -2, 0)])
        assert PathNetwork([loop]).junctions == []

    def test_a_closed_path_coming_close_without_crossing_does_not_meet(self) -> None:
        # The ring's lowest point, its first, stops 5 cm above H: a loop has no end to join by.
        ring = _curve([(0, 0.05, 0), (2, 2, 0), (-2, 2, 0)], closed=True)
        assert find_junctions([H, ring]) == []

    def test_paths_running_side_by_side_do_not_meet(self) -> None:
        assert find_junctions([H, _curve([(-5, 0.3, 0), (5, 0.3, 0)])]) == []

    def test_a_closed_path_meets_an_open_one_twice(self) -> None:
        ring = _curve([(-2, -2, 0), (2, -2, 0), (2, 2, 0), (-2, 2, 0)], closed=True)
        assert len(find_junctions([H, ring])) == 2


class TestWalk:
    def test_the_marker_moves_by_the_part_of_the_push_along_the_path(self) -> None:
        net = PathNetwork([H])
        step = net.walk(Place(0, 1.0), *_push(60.0, speed=2.0), dt=1.0)
        assert step.place.s == pytest.approx(2.0)

    def test_a_push_across_the_path_moves_nothing(self) -> None:
        net = PathNetwork([H])
        assert net.walk(Place(0, 1.0), 0.0, 1.0, 1.0).place == Place(0, 1.0)

    def test_a_push_backwards_moves_back(self) -> None:
        net = PathNetwork([H])
        assert net.walk(Place(0, 3.0), -1.0, 0.0, 1.0).place.s == pytest.approx(2.0)

    @pytest.mark.parametrize(("vx", "vy", "dt"), [(0.0, 0.0, 1.0), (1.0, 0.0, 0.0)])
    def test_no_push_or_no_time_moves_nothing(self, vx: float, vy: float, dt: float) -> None:
        assert PathNetwork([H]).walk(Place(0, 3.0), vx, vy, dt).place == Place(0, 3.0)

    def test_the_marker_follows_the_path_round_a_corner(self) -> None:
        corner = _curve([(0, 0, 0, 0), (4, 0, 0, 1.0), (4, 4, 0, 0)])
        net = PathNetwork([corner])
        # Pushing up and to the right keeps a positive share along the whole bend.
        step = net.walk(Place(0, 2.0), *_push(45.0), dt=10.0)
        assert step.place.s == pytest.approx(corner.length)

    def test_an_open_end_stops_the_marker(self) -> None:
        step = PathNetwork([H]).walk(Place(0, 9.5), 1.0, 0.0, 1.0)
        assert step == Step_(Place(0, 10.0), released=False)

    def test_an_end_that_releases_lets_go(self) -> None:
        step = PathNetwork([H], [(False, True)]).walk(Place(0, 9.5), 1.0, 0.0, 1.0)
        assert step.released
        assert step.place.s == pytest.approx(10.0)

    def test_the_start_releases_on_its_own_setting(self) -> None:
        net = PathNetwork([H], [(True, False)])
        assert net.walk(Place(0, 0.5), -1.0, 0.0, 1.0).released
        assert not net.walk(Place(0, 9.5), 1.0, 0.0, 1.0).released

    def test_at_a_stopping_end_a_push_outward_does_nothing(self) -> None:
        net = PathNetwork([H])
        assert net.walk(Place(0, 10.0), 1.0, 0.0, 1.0) == Step_(Place(0, 10.0))

    def test_a_closed_path_carries_on_round(self) -> None:
        ring = _curve([(0, 0, 0), (2, 0, 0), (2, 2, 0), (0, 2, 0)], closed=True)
        step = PathNetwork([ring]).walk(Place(0, 7.5), 0.0, -1.0, 1.0)
        assert step.place.s == pytest.approx(0.5)


class TestLegs:
    def test_one_step_crosses_a_bounded_number_of_junctions(self) -> None:
        # Twenty short paths cross H at x = -4.5, -4.0, ...; one huge step stops at the sixteenth.
        comb = [_curve([(-4.5 + 0.5 * i, -1, 0), (-4.5 + 0.5 * i, 1, 0)]) for i in range(20)]
        net = PathNetwork([H, *comb])
        step = net.walk(Place(0, 0.0), 1.0, 0.0, 100.0)
        assert _at(net, step.place) == (-4.5 + 0.5 * 15, 0.0)


class TestSteering:
    def test_pushing_straight_on_keeps_the_path_through_a_crossing(self) -> None:
        net = PathNetwork([H, V])
        step = net.walk(Place(0, 4.0), 1.0, 0.0, 3.0)
        assert _at(net, step.place) == (2.0, 0.0)

    def test_pushing_towards_the_other_path_takes_it_at_the_crossing(self) -> None:
        net = PathNetwork([H, V])
        # 60 degrees: 30 off V, 60 off H. Half speed along H reaches the crossing in 2 s, then 0.87 m/s up V.
        step = net.walk(Place(0, 4.0), *_push(60.0), dt=3.0)
        assert step.place.path == 1
        assert _at(net, step.place) == pytest.approx((0.0, math.cos(math.radians(30.0))), abs=1e-6)

    def test_a_push_only_a_little_closer_to_the_other_path_stays(self) -> None:
        net = PathNetwork([H, V])
        # 50 degrees: 40 off V, 50 off H - closer to V, but not by the switching margin.
        step = net.walk(Place(0, 4.0), *_push(50.0), dt=4.0)
        assert step.place.path == 0

    def test_standing_on_a_crossing_a_push_along_the_other_path_takes_it(self) -> None:
        net = PathNetwork([H, V])
        step = net.walk(Place(0, 5.0), 0.0, -1.0, 1.0)
        assert _at(net, step.place) == (0.0, -1.0)

    def test_a_marker_next_to_a_crossing_can_still_take_it(self) -> None:
        net = PathNetwork([H, V])
        step = net.walk(Place(0, 4.97), 0.0, 1.0, 1.0)
        assert step.place.path == 1

    def test_a_branch_is_entered_only_the_way_it_leads(self) -> None:
        branch = _curve([(2, 0, 0), (2, 4, 0)])
        net = PathNetwork([H, branch])
        assert net.walk(Place(0, 7.0), 0.0, 1.0, 1.0).place == Place(1, pytest.approx(1.0))
        # Down, there is no branch to take; the marker stays put on H.
        assert net.walk(Place(0, 7.0), 0.0, -1.0, 1.0).place == Place(0, 7.0)

    def test_at_the_end_of_a_branch_a_diagonal_push_carries_on_along_the_path_it_joins(self) -> None:
        # The branch comes down from (2, 4) and ends on H at (2, 0).
        branch = _curve([(2, 4, 0), (2, 0, 0)])
        net = PathNetwork([H, branch])
        step = net.walk(Place(1, 4.0), *_push(-45.0), dt=1.0)
        assert step.place.path == 0
        assert _at(net, step.place) == pytest.approx((2.0 + math.cos(math.radians(45.0)), 0.0), abs=1e-6)

    def test_a_junction_just_past_a_closed_path_s_seam_is_still_taken(self) -> None:
        # The ring starts at (-2, 0.5) going down, so it meets H at (-2, 0) half a metre after its seam.
        ring = _curve([(-2, 0.5, 0), (-2, -2, 0), (2, -2, 0), (2, 2, 0), (-2, 2, 0)], closed=True)
        net = PathNetwork([H, ring])
        # Coming down the ring's last stretch, 0.8 m before the crossing; mostly right, a little down.
        start = Place(1, ring.length - 0.3)
        step = net.walk(start, *_push(-30.0), dt=1.6 + 1.0)
        assert step.place.path == 0
        assert _at(net, step.place) == pytest.approx((-2.0 + math.cos(math.radians(30.0)), 0.0), abs=1e-6)

    def test_coming_back_the_marker_can_leave_the_branch_again(self) -> None:
        branch = _curve([(2, 0, 0), (2, 4, 0)])
        net = PathNetwork([H, branch])
        step = net.walk(Place(1, 1.0), *_push(-60.0), dt=4.0)
        assert step.place.path == 0


class TestNearest:
    def test_the_nearest_place_over_all_paths(self) -> None:
        net = PathNetwork([H, V])
        assert net.nearest(0.3, 3.0) == Place(1, pytest.approx(8.0))

    def test_the_first_path_wins_when_it_is_nearest(self) -> None:
        net = PathNetwork([H, V])
        assert net.nearest(3.0, 0.2) == Place(0, pytest.approx(8.0))

    def test_the_nearest_place_among_some_paths(self) -> None:
        net = PathNetwork([H, V])
        assert net.nearest(0.3, 3.0, among=[0]) == Place(0, pytest.approx(5.3))
        assert net.nearest(0.3, 3.0, among=[]) is None

    def test_a_tie_between_paths_goes_to_the_lower_one(self) -> None:
        # (0, 0) is on both H and V, at the crossing.
        net = PathNetwork([V, H])
        assert net.nearest(0.0, 0.0).path == 0
        assert net.nearest(0.0, 0.0, among={1, 0}).path == 0
        assert net.nearest(0.0, 0.0, among=[1]).path == 1

    @pytest.mark.parametrize("seed", range(6))
    def test_the_nearest_place_matches_measuring_every_path(self, seed: int) -> None:
        rng = random.Random(seed)
        curves = []
        while len(curves) < 24:
            count = rng.randint(2, 6)
            points = [(rng.uniform(-6, 6), rng.uniform(-9, 1), 0.0) for _ in range(count)]
            curves.append(_curve(points, closed=count >= 3 and rng.random() < 0.3))
        net = PathNetwork(curves)
        groups = {net.connected(i) for i in range(len(curves))}
        for _ in range(150):
            x, y = rng.uniform(-8, 8), rng.uniform(-11, 3)
            for among in (None, *groups, [rng.randrange(len(curves))]):
                paths = range(len(curves)) if among is None else among
                dist, path, s = min((curves[i].nearest(x, y)[1], i, curves[i].nearest(x, y)[0]) for i in paths)
                place = net.nearest(x, y, among=among)
                assert place.path == path
                assert place.s == pytest.approx(s, abs=1e-9)

    def test_paths_joined_through_junctions_are_connected(self) -> None:
        apart = _curve([(20, 0, 0), (30, 0, 0)])
        branch = _curve([(0, 2, 0), (8, 2, 0)])
        net = PathNetwork([H, apart, V, branch])
        assert net.connected(0) == {0, 2, 3}
        assert net.connected(1) == {1}


def Step_(place: Place, released: bool = False) -> object:  # noqa: N802 - reads like the dataclass
    from openfollow.paths.network import Step

    return Step(place, released)
