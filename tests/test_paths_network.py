# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The path network: junctions, walking a locked marker, steering at crossings, nearest places."""

from __future__ import annotations

import math
import random

import numpy as np
import pytest

from openfollow.paths import network
from openfollow.paths.geometry import PathCurve, PathPoint, build_curve
from openfollow.paths.network import Junction, PathNetwork, Place, Step, extend_to_corners, find_junctions

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

    def test_a_path_passing_the_same_spot_of_another_twice_meets_it_twice(self) -> None:
        # Up through (0, 0), round, and diagonally back through (0, 0).
        twice = _curve([(0, -2, 0, 0), (0, 2, 0, 0), (2, 2, 0, 0), (-2, -2, 0, 0)])
        junctions = find_junctions([H, twice])
        assert [(j.s_a, j.s_b) for j in junctions] == [
            (pytest.approx(5.0), pytest.approx(2.0)),
            (pytest.approx(5.0), pytest.approx(6.0 + 2 * math.sqrt(2))),
        ]

    def test_a_closed_path_meets_an_open_one_twice(self) -> None:
        ring = _curve([(-2, -2, 0), (2, -2, 0), (2, 2, 0), (-2, 2, 0)], closed=True)
        assert len(find_junctions([H, ring])) == 2


def _reference_junctions(curves: list[PathCurve]) -> list[Junction]:
    """Every segment of every pair of curves tested against every other, the junctions in the order found."""
    found: list[Junction] = []

    def same(j: Junction, k: Junction) -> bool:
        return (
            (j.a, j.b) == (k.a, k.b)
            and abs(curves[j.a].offset(j.s_a, k.s_a)) <= 0.05
            and abs(curves[j.b].offset(j.s_b, k.s_b)) <= 0.05
        )

    def touches(end_of: PathCurve, on: PathCurve) -> list[tuple[float, float]]:
        ends = [] if end_of.closed else [0.0, end_of.length]
        return [(s, on.nearest(*end_of.point(s)[:2])[0]) for s in ends if on.nearest(*end_of.point(s)[:2])[1] <= 0.1]

    for a, ca in enumerate(curves):
        for b in range(a + 1, len(curves)):
            cb = curves[b]
            r, w = np.diff(ca.xy, axis=0)[:, None, :], np.diff(cb.xy, axis=0)[None, :, :]
            qp = cb.xy[None, :-1, :] - ca.xy[:-1, None, :]
            denom = r[..., 0] * w[..., 1] - r[..., 1] * w[..., 0]
            with np.errstate(divide="ignore", invalid="ignore"):
                t = (qp[..., 0] * w[..., 1] - qp[..., 1] * w[..., 0]) / denom
                u = (qp[..., 0] * r[..., 1] - qp[..., 1] * r[..., 0]) / denom
            hit = (np.abs(denom) > 1e-12) & (t >= 0.0) & (t <= 1.0) & (u >= 0.0) & (u <= 1.0)
            pair = [
                Junction(
                    a,
                    float(ca.s[i] + t[i, k] * (ca.s[i + 1] - ca.s[i])),
                    b,
                    float(cb.s[k] + u[i, k] * (cb.s[k + 1] - cb.s[k])),
                )
                for i, k in zip(*np.nonzero(hit), strict=True)
            ]
            pair += [Junction(a, sa, b, sb) for sa, sb in touches(ca, cb)]
            pair += [Junction(a, sa, b, sb) for sb, sa in touches(cb, ca)]
            for j in pair:
                level = abs(ca.point(j.s_a)[2] - cb.point(j.s_b)[2]) <= 0.1
                if level and not any(same(j, k) for k in found):
                    found.append(j)
    return found


def _wander(rng: random.Random, count: int) -> list[tuple[float, ...]]:
    x, y, heading = rng.uniform(-6, 6), rng.uniform(-5, 5), rng.uniform(0, 2 * math.pi)
    z, radius = rng.choice([0.0, 0.05, 0.5]), rng.choice([0.0, 0.2, 0.5, 2.0])
    points = []
    for _ in range(count):
        points.append((round(x, 2), round(y, 2), z, radius))
        heading += rng.uniform(-1.5, 1.5)
        step = rng.uniform(0.2, 1.5)
        x, y = min(8, max(-8, x + step * math.cos(heading))), min(6, max(-6, y + step * math.sin(heading)))
    return points


class TestJunctionSearch:
    @pytest.mark.parametrize("seed", range(10))
    def test_the_junctions_are_those_of_testing_every_pair_of_segments(self, seed: int) -> None:
        rng = random.Random(seed)
        curves = []
        for _ in range(rng.randint(3, 7)):
            count = rng.randint(2, 30)
            curves.append(_curve(_wander(rng, count), closed=count >= 3 and rng.random() < 0.3))
        # Branches ending on, near, and just out of reach of a path.
        for gap in (0.0, 0.05, 0.099, 0.101, 0.3):
            on = rng.choice(curves)
            x, y, z = on.point(rng.uniform(0, on.length))
            curves.append(_curve([(x + gap, y, z, 0), (x + 3, y + rng.uniform(-3, 3), z, 0)]))
        assert find_junctions(curves) == _reference_junctions(curves)

    def test_paths_far_apart_are_never_compared(self, monkeypatch: pytest.MonkeyPatch) -> None:
        compared = []
        monkeypatch.setattr(network, "_crossings", lambda *args: compared.append(args) or [])
        monkeypatch.setattr(network, "_touches", lambda *args: compared.append(args) or [])
        assert find_junctions([H, _curve([(20, -5, 0), (20, 5, 0)])]) == []
        assert compared == []

    def test_long_paths_are_compared_segment_by_segment_only_where_they_come_near(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A zigzags along x in 0 <= y <= 1, B along y in 0 <= x <= 1: they meet only near the origin.
        along_x = _curve([(-50 + 0.5 * i, i % 2, 0, 0) for i in range(201)])
        along_y = _curve([(i % 2, -50 + 0.5 * i, 0, 0) for i in range(201)])
        tested = []
        cross = network._cross
        monkeypatch.setattr(network, "_cross", lambda a, b: tested.append(a.size // 2) or cross(a, b))
        junctions = find_junctions([along_x, along_y])
        assert junctions == _reference_junctions([along_x, along_y])
        assert len(junctions) >= 1
        assert sum(tested) / 3 < 0.05 * 200 * 200


class TestCornerExtension:
    # B turns up at (2, 0) with the default radius, so its curve passes 0.21 m inside the point.
    ROUNDED = _curve([(0, 0, 0, 0), (2, 0, 0, 0.5), (2, 2, 0, 0)])
    VERTEX = ROUNDED.point(ROUNDED.nearest(2.0, 0.0)[0])

    @pytest.mark.parametrize("gap", [0.0, 0.09])
    def test_an_end_on_a_rounded_corner_point_runs_on_to_the_arc_and_joins_there(self, gap: float) -> None:
        branch = _curve([(2, -2, 0), (2, -gap, 0)])
        net = PathNetwork([branch, self.ROUNDED])
        extended = net.curves[0]
        assert extended.point(extended.length) == pytest.approx(self.VERTEX)
        assert extended.length == pytest.approx(branch.length + math.dist((2, -gap, 0), self.VERTEX))
        assert len(net.junctions) == 1
        j = net.junctions[0]
        assert j.s_a == pytest.approx(extended.length)
        assert net.point(Place(0, j.s_a)) == pytest.approx(net.point(Place(1, j.s_b)), abs=1e-9)

    def test_on_a_right_angle_at_the_default_radius_the_end_runs_on_0_207_m(self) -> None:
        net = PathNetwork([_curve([(2, -2, 0), (2, 0, 0)]), self.ROUNDED])
        assert net.curves[0].length == pytest.approx(2.0 + 0.5 * (math.sqrt(2) - 1))

    def test_the_old_end_becomes_a_sharp_inner_point(self) -> None:
        net = PathNetwork([_curve([(2, -2, 0), (2, 0, 0)]), self.ROUNDED])
        arriving, leaving = net.curves[0].directions(2.0)
        assert arriving == pytest.approx((0.0, 1.0))
        assert leaving == pytest.approx((-math.sqrt(0.5), math.sqrt(0.5)))

    def test_a_start_on_a_corner_point_runs_on_backwards(self) -> None:
        branch = _curve([(2, 0, 0), (2, -2, 0)])
        net = PathNetwork([branch, self.ROUNDED])
        assert net.curves[0].point(0.0) == pytest.approx(self.VERTEX)
        assert net.curves[0].point(net.curves[0].length) == pytest.approx((2.0, -2.0, 0.0))
        assert [j.s_a for j in net.junctions] == [pytest.approx(0.0)]

    def test_an_end_beyond_reach_of_the_corner_point_stays_and_does_not_join(self) -> None:
        branch = _curve([(2, -2, 0), (2, -0.11, 0)])
        net = PathNetwork([branch, self.ROUNDED])
        assert net.curves[0] is branch
        assert net.junctions == []

    def test_an_end_on_the_curve_beside_a_corner_point_stays_and_joins_where_it_lies(self) -> None:
        # A gentle bend: the curve passes 2 cm from the point, and the end lies on it 9 cm along.
        gentle = _curve([(0, 0, 0, 0), (2, 0, 0, 0.5), (4, 1, 0, 0)])
        branch = _curve([(1.91, -2, 0), (1.91, 0.0, 0)])
        net = PathNetwork([branch, gentle])
        assert net.curves[0] is branch
        assert net.junctions == [Junction(0, branch.length, 1, pytest.approx(gentle.nearest(1.91, 0.0)[0]))]

    def test_an_end_touching_another_path_stays_though_a_corner_point_is_near(self) -> None:
        # The end lies 5 cm above H and 9 cm below the corner of B, which runs 14 cm above H.
        branch = _curve([(4, 2, 0), (2, 0.05, 0)])
        beside = _curve([(-2, 0.14, 0, 0), (2, 0.14, 0, 0.5), (2, 3, 0, 0)])
        net = PathNetwork([branch, H, beside])
        assert net.curves[0] is branch
        assert net.junctions == [Junction(0, branch.length, 1, pytest.approx(7.0))]

    def test_an_end_runs_on_to_the_nearest_of_several_corner_points(self) -> None:
        hook = _curve([(0, 0, 0, 0), (2, 0, 0, 0.5), (2, 2, 0, 0.5), (0, 2, 0, 0)])
        net = PathNetwork([_curve([(2, 4, 0), (2, 2.05, 0)]), hook])
        end = net.curves[0].point(net.curves[0].length)
        assert end == pytest.approx(hook.point(hook.nearest(2.0, 2.0)[0]))

    def test_an_end_runs_on_to_the_nearer_of_two_paths_corner_points(self) -> None:
        # A peak at (0.05, 0) and a valley at (-0.08, 0), both rounded 0.21 m away from the end at (0, 0).
        peak = _curve([(-1.95, -2, 0, 0), (0.05, 0, 0, 0.5), (2.05, -2, 0, 0)])
        valley = _curve([(-2.08, 2, 0, 0), (-0.08, 0, 0, 0.5), (1.92, 2, 0, 0)])
        net = PathNetwork([_curve([(-3, 0, 0), (0, 0, 0)]), valley, peak])
        end = net.curves[0].point(net.curves[0].length)
        assert end == pytest.approx(peak.point(peak.nearest(0.05, 0.0)[0]))

    def test_a_carried_on_curve_keeps_its_corner_points(self) -> None:
        bent = _curve([(0, -2, 0, 0), (2, -2, 0, 0.5), (2, 0, 0, 0)])
        net = PathNetwork([bent, self.ROUNDED])
        assert net.curves[0].length > bent.length
        assert net.curves[0].corners.tolist() == [[2.0, -2.0]]

    def test_the_run_on_climbs_linearly_to_the_corner_s_height(self) -> None:
        raised = _curve([(0, 0, 0.08, 0), (2, 0, 0.08, 0.5), (2, 2, 0.08, 0)])
        net = PathNetwork([_curve([(2, -2, 0), (2, 0, 0)]), raised])
        extended = net.curves[0]
        assert extended.point(2.0)[2] == pytest.approx(0.0)
        assert extended.point((2.0 + extended.length) / 2)[2] == pytest.approx(0.04)
        assert extended.point(extended.length)[2] == pytest.approx(0.08)
        assert len(net.junctions) == 1

    def test_an_end_below_a_corner_point_at_another_height_stays(self) -> None:
        bridge = _curve([(0, 0, 0.5, 0), (2, 0, 0.5, 0.5), (2, 2, 0.5, 0)])
        branch = _curve([(2, -2, 0), (2, 0, 0)])
        net = PathNetwork([branch, bridge])
        assert net.curves[0] is branch
        assert net.junctions == []

    def test_paths_turning_at_the_same_point_with_curves_apart_do_not_join(self) -> None:
        # A peak and a valley at (0, 0): rounded on opposite sides, their curves stay 0.41 m apart.
        peak = _curve([(-2, -2, 0, 0.5), (0, 0, 0, 0.5), (2, -2, 0, 0.5)])
        valley = _curve([(-2, 2, 0, 0.5), (0, 0, 0, 0.5), (2, 2, 0, 0.5)])
        net = PathNetwork([peak, valley])
        assert net.curves == [peak, valley]
        assert net.junctions == []

    def test_a_closed_path_has_no_end_to_run_on(self) -> None:
        ring = _curve([(2, 0, 0, 0), (3, -1, 0, 0), (3, 1, 0, 0)], closed=True)
        net = PathNetwork([ring, self.ROUNDED])
        assert net.curves[0] is ring

    def test_the_curves_alone_can_be_carried_on_without_a_network(self) -> None:
        curves = extend_to_corners([_curve([(2, -2, 0), (2, 0, 0)]), self.ROUNDED])
        assert curves[0].point(curves[0].length) == pytest.approx(self.VERTEX)
        assert curves[1] is self.ROUNDED


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
        assert step == Step(Place(0, 10.0), released=False)

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
        assert net.walk(Place(0, 10.0), 1.0, 0.0, 1.0) == Step(Place(0, 10.0))

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

    # B turns sharply up at (2, 0), where the branch A coming up from (2, -2) ends.
    SHARP = _curve([(0, 0, 0, 0), (2, 0, 0, 0), (2, 2, 0, 0)])

    @pytest.mark.parametrize(("push", "want"), [((-1.0, 0.0), (1.5, 0.0)), ((0.0, 1.0), (2.0, 0.5))])
    def test_from_a_branch_ending_on_a_sharp_corner_either_way_can_be_taken(
        self, push: tuple[float, float], want: tuple[float, float]
    ) -> None:
        branch = _curve([(2, -2, 0), (2, 0, 0)])
        net = PathNetwork([branch, self.SHARP])
        step = net.walk(Place(0, branch.length), *push, dt=0.5)
        assert step.place.path == 1
        assert _at(net, step.place) == want

    @pytest.mark.parametrize("s", [2.0, float(np.nextafter(2.0, 0.0)), float(np.nextafter(2.0, 3.0))])
    @pytest.mark.parametrize(("push", "want"), [((-1.0, 0.0), (1.0, 0.0)), ((0.0, 1.0), (2.0, 1.0))])
    def test_on_a_sharp_corner_the_marker_leaves_by_either_segment(
        self, s: float, push: tuple[float, float], want: tuple[float, float]
    ) -> None:
        net = PathNetwork([self.SHARP])
        assert _at(net, net.walk(Place(0, s), *push, dt=1.0).place) == want

    @pytest.mark.parametrize(("push", "want"), [((0.0, 1.0), 7.0), ((1.0, 0.0), 1.0)])
    def test_at_a_closed_path_s_sharp_first_point_the_marker_leaves_by_either_segment(
        self, push: tuple[float, float], want: float
    ) -> None:
        square = _curve([(0, 0, 0, 0), (2, 0, 0, 0), (2, 2, 0, 0), (0, 2, 0, 0)], closed=True)
        assert PathNetwork([square]).walk(Place(0, 0.0), *push, dt=1.0).place.s == pytest.approx(want)

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
