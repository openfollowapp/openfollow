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

REACH = network.JUNCTION_REACH_M

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
        bridge = _curve([(0, -5, 0.25), (0, 5, 0.25)])
        assert find_junctions([H, bridge]) == []

    def test_a_small_height_difference_still_meets(self) -> None:
        near = _curve([(0, -5, 0.18), (0, 5, 0.18)])
        assert len(find_junctions([H, near])) == 1

    def test_a_path_ending_on_another_joins_it_there(self) -> None:
        branch = _curve([(2, 0.18, 0), (2, 4, 0)])
        assert find_junctions([H, branch]) == [Junction(0, pytest.approx(7.0), 1, 0.0)]

    def test_a_path_ending_short_of_another_does_not(self) -> None:
        branch = _curve([(2, 0.25, 0), (2, 4, 0)])
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

    @pytest.mark.parametrize(
        ("vee", "line"),
        [
            ([(-3.5, 0.5), (-3.3, -1.2), (-2.6, -2.9)], [(-5.0, -1.4), (-1.6, -1.0)]),
            ([(-2.1, 1.2), (-1.9, -0.5), (-1.2, -2.2)], [(-3.6, -0.7), (-0.2, -0.3)]),
            ([(-2.5, -3.0), (-1.2, -3.3), (0.6, -3.6)], [(-1.5, -4.6), (-0.9, -2.0)]),
        ],
    )
    def test_a_path_through_another_s_sharp_corner_point_meets_it_once(
        self, vee: list[tuple[float, float]], line: list[tuple[float, float]]
    ) -> None:
        # Typed to one decimal, the crossing falls a rounding error outside both segments meeting there.
        corner = _curve([(x, y, 0, 0) for x, y in vee])
        assert len(find_junctions([corner, _curve([(x, y, 0) for x, y in line])])) == 1

    def test_a_crossing_found_a_rounding_error_past_a_path_s_end_is_at_that_end(self) -> None:
        # The branch ends on the line; the intersection lands a rounding error beyond its end.
        branch = _curve([(-4.9, -3.7, 0), (-4.0, -4.0, 0)])
        line = _curve([(-4.3, -4.9, 0), (-3.7, -3.1, 0)])
        assert [j.s_a for j in find_junctions([branch, line])] == [branch.length]
        assert [j.s_b for j in find_junctions([line, branch])] == [branch.length]
        # Turned round, it lands a rounding error before its start.
        back = _curve([(-4.0, -4.0, 0), (-4.9, -3.7, 0)])
        assert [j.s_a for j in find_junctions([back, line])] == [0.0]

    def test_a_corner_stopping_a_micrometre_short_of_another_path_does_not_meet_it(self) -> None:
        vee = _curve([(-1, 2, 0, 0), (0, 1e-6, 0, 0), (1, 2, 0, 0)])
        assert find_junctions([vee, H]) == []

    def test_a_crossing_on_a_closed_path_s_first_point_counts_once(self) -> None:
        # The crossing at (0, 0) ends the diamond's last segment and starts its first.
        diamond = _curve([(0, 0, 0, 0), (1, -1, 0, 0), (2, 0, 0, 0), (1, 1, 0, 0)], closed=True)
        junctions = find_junctions([diamond, _curve([(-1, 0, 0), (3, 0, 0)])])
        assert len(junctions) == 2

    def test_a_junction_found_twice_across_a_closed_path_s_seam_counts_once(self) -> None:
        # B crosses the diamond's last segment 2.8 cm before the seam, and ends 1.8 cm after it.
        diamond = _curve([(0, 0, 0, 0), (1, -1, 0, 0), (2, 0, 0, 0), (1, 1, 0, 0)], closed=True)
        junctions = find_junctions([diamond, _curve([(0, 1, 0), (0.02, -0.005, 0)])])
        assert len(junctions) == 1

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
        return [(s, on.nearest(*end_of.point(s)[:2])[0]) for s in ends if on.nearest(*end_of.point(s)[:2])[1] <= REACH]

    for a, ca in enumerate(curves):
        for b in range(a + 1, len(curves)):
            cb = curves[b]
            r, w = np.diff(ca.xy, axis=0)[:, None, :], np.diff(cb.xy, axis=0)[None, :, :]
            qp = cb.xy[None, :-1, :] - ca.xy[:-1, None, :]
            denom = r[..., 0] * w[..., 1] - r[..., 1] * w[..., 0]
            with np.errstate(divide="ignore", invalid="ignore"):
                t = (qp[..., 0] * w[..., 1] - qp[..., 1] * w[..., 0]) / denom
                u = (qp[..., 0] * r[..., 1] - qp[..., 1] * r[..., 0]) / denom
            on = 1e-9
            hit = (np.abs(denom) > 1e-12) & (t >= -on) & (t <= 1 + on) & (u >= -on) & (u <= 1 + on)
            pair = [
                Junction(
                    a,
                    float(np.clip(ca.s[i] + t[i, k] * (ca.s[i + 1] - ca.s[i]), 0.0, ca.length)),
                    b,
                    float(np.clip(cb.s[k] + u[i, k] * (cb.s[k + 1] - cb.s[k]), 0.0, cb.length)),
                )
                for i, k in zip(*np.nonzero(hit), strict=True)
            ]
            pair += [Junction(a, sa, b, sb) for sa, sb in touches(ca, cb)]
            pair += [Junction(a, sa, b, sb) for sb, sa in touches(cb, ca)]
            for j in pair:
                level = abs(ca.point(j.s_a)[2] - cb.point(j.s_b)[2]) <= network.JUNCTION_HEIGHT_M
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
        for gap in (0.0, REACH / 2, REACH - 0.001, REACH + 0.001, 3 * REACH):
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


def _comb(teeth: int, along_x: bool) -> PathCurve:
    """A zigzag of sharp teeth 0.2 m apart across a 6 m square."""
    points = [(-3 + 0.2 * i, 3.0 if i % 2 else -3.0, 0, 0) for i in range(teeth)]
    return _curve(points if along_x else [(y, x, z, r) for x, y, z, r in points])


class TestJunctionSearchCost:
    def test_a_repeat_is_looked_for_only_among_nearby_junctions(self, monkeypatch: pytest.MonkeyPatch) -> None:
        compared = []
        same = network._same_junction
        monkeypatch.setattr(network, "_same_junction", lambda *args: compared.append(1) or same(*args))
        junctions = find_junctions([_comb(30, along_x=True), _comb(30, along_x=False)])
        # The crossings lie about 20 cm apart: most have no junction within reach to compare with.
        assert len(junctions) > 800
        assert len(compared) < len(junctions) / 2

    def test_a_dense_pair_is_tested_in_bounded_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rng = random.Random(3)
        scribbles = [_curve([(rng.uniform(0, 2), rng.uniform(0, 2), 0, 0.5) for _ in range(40)]) for _ in range(2)]
        sizes = []
        cross = network._cross
        monkeypatch.setattr(network, "_cross", lambda a, b: sizes.append(a.size // 2) or cross(a, b))
        assert find_junctions(scribbles) == _reference_junctions(scribbles)
        # Three products per pass, and more than one pass.
        assert len(sizes) > 3
        assert max(sizes) <= network._BOX_PAIRS_PER_PASS * network._BOX_SEGMENTS**2


class TestCornerExtension:
    # B turns up at (2, 0) with the default radius, so its curve passes 0.21 m inside the point.
    ROUNDED = _curve([(0, 0, 0, 0), (2, 0, 0, 0.5), (2, 2, 0, 0)])
    VERTEX = ROUNDED.point(ROUNDED.nearest(2.0, 0.0)[0])

    @pytest.mark.parametrize("gap", [0.0, REACH - 0.01])
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
        branch = _curve([(2, -2, 0), (2, -(REACH + 0.01), 0)])
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
        # The end touches H, and B's corner point is in reach of it while B itself is not.
        branch = _curve([(4, 2, 0), (2, 0.5 * REACH, 0)])
        beside = _curve([(-2, 1.4 * REACH, 0, 0), (2, 1.4 * REACH, 0, 0.5), (2, 3, 0, 0)])
        net = PathNetwork([branch, H, beside])
        assert net.curves[0] is branch
        assert net.junctions == [Junction(0, branch.length, 1, pytest.approx(7.0))]

    def test_an_end_under_a_path_at_another_height_still_runs_on(self) -> None:
        bridge = _curve([(1, -1, 2.0), (3, 1, 2.0)])
        net = PathNetwork([_curve([(2, -2, 0), (2, 0, 0)]), self.ROUNDED, bridge])
        assert net.curves[0].point(net.curves[0].length) == pytest.approx(self.VERTEX)
        assert [(j.a, j.b) for j in net.junctions] == [(0, 1)]

    SPURS = (_curve([(2, -2, 0), (2, 0, 0)]), _curve([(4, -1, 0), (2, 0, 0)]), _curve([(4, 0.3, 0), (2, 0, 0)]))

    @pytest.mark.parametrize("count", [2, 3])
    @pytest.mark.parametrize("main_first", [False, True])
    def test_spurs_ending_on_one_corner_point_all_run_on_and_join_the_corner_path(
        self, count: int, main_first: bool
    ) -> None:
        spurs = list(self.SPURS[:count])
        curves = [self.ROUNDED, *spurs] if main_first else [*reversed(spurs), self.ROUNDED]
        net = PathNetwork(curves)
        main = curves.index(self.ROUNDED)
        for i, curve in enumerate(net.curves):
            if i != main:
                assert curve.point(curve.length) == pytest.approx(self.VERTEX)
                with_main = [j for j in net.junctions if {j.a, j.b} == {i, main}]
                assert len(with_main) == 1

    def test_a_spur_drawn_away_from_the_corner_point_runs_on_with_the_other(self) -> None:
        away = _curve([(2, 0, 0), (4, -1, 0)])
        net = PathNetwork([self.SPURS[0], away, self.ROUNDED])
        assert net.curves[0].point(net.curves[0].length) == pytest.approx(self.VERTEX)
        assert net.curves[1].point(0.0) == pytest.approx(self.VERTEX)

    def test_a_spur_on_a_corner_point_beside_another_spur_s_side_stays(self) -> None:
        # The first ends 5 cm beside the second, which runs on up to the corner point itself.
        beside = _curve([(0, -1, 0), (1.95, -0.06, 0)])
        net = PathNetwork([beside, self.SPURS[0], self.ROUNDED])
        assert net.curves[0] is beside

    # The path turns down at (2, 0) and, later, up at (2.12, 0).
    ZIGZAG = _curve([(0, 2, 0, 0), (2, 0, 0, 0.5), (4, 2, 0, 0), (4, -2, 0, 0), (2.12, 0, 0, 0.5), (0, -2, 0, 0)])

    @pytest.mark.parametrize("right_ends_there", [True, False])
    def test_spurs_on_two_corner_points_of_one_path_touching_each_other_stay(self, right_ends_there: bool) -> None:
        # The spurs end, or the right one starts, 8 cm apart, one on each corner point: running on would part them.
        left = _curve([(0, 0, 0), (2, 0, 0)])
        right = _curve([(4.5, 0, 0), (2.08, 0, 0)] if right_ends_there else [(2.08, 0, 0), (4.5, 0, 0)])
        net = PathNetwork([left, right, self.ZIGZAG])
        assert net.curves[:2] == [left, right]

    def test_a_spur_runs_on_past_a_spur_end_on_another_corner_point_that_stays(self, monkeypatch) -> None:  # noqa: ANN001
        # As above, but the right spur's end also lies on the side of a third path, so it stays.
        monkeypatch.setattr(network, "JUNCTION_REACH_M", 0.1)  # the layout's centimetres assume a 10 cm reach
        left, right = _curve([(0, 0, 0), (2, 0, 0)]), _curve([(4.5, 0, 0), (2.08, 0, 0)])
        beside = _curve([(2.16, -1, 0), (2.16, 1, 0)])
        net = PathNetwork([left, right, self.ZIGZAG, beside])
        assert net.curves[0] is not left
        assert net.curves[1] is right

    def test_a_marker_on_one_of_two_spurs_walks_on_to_the_corner_path(self) -> None:
        net = PathNetwork([*self.SPURS[:2], self.ROUNDED])
        place = Place(0, 2.0)
        for _ in range(3):  # a moving marker stops at each junction it reaches, then decides there
            place = net.walk(place, -1.0, 0.0, 1.0).place
            if place.path != 0:
                break
        assert place.path == 2
        assert net.point(place)[0] < self.VERTEX[0]

    def test_of_two_spurs_ending_at_each_other_the_one_on_the_corner_point_runs_on_and_they_still_meet(
        self,
    ) -> None:
        # The first ends 8 cm from the corner point, the second 15 cm from it and 8.5 cm from the first's end.
        on = _curve([(2, -2, 0), (2, -0.08, 0)])
        off = _curve([(4, -1, 0), (2.06, -0.14, 0)])
        net = PathNetwork([on, off, self.ROUNDED])
        assert net.curves[0].point(net.curves[0].length) == pytest.approx(self.VERTEX)
        assert net.curves[1] is off
        assert sorted((j.a, j.b) for j in net.junctions) == [(0, 1), (0, 2)]
        spurs = next(j for j in net.junctions if j.b == 1)
        assert (spurs.s_a, spurs.s_b) == (pytest.approx(on.nearest(2.06, -0.14)[0]), off.length)

    def test_a_spur_on_a_corner_point_runs_on_past_a_spur_end_that_stays(self, monkeypatch) -> None:  # noqa: ANN001
        # Both ends lie on the corner point and touch each other; the second also lies on the side of a third path.
        monkeypatch.setattr(network, "JUNCTION_REACH_M", 0.1)  # the layout's centimetres assume a 10 cm reach
        first = _curve([(1.96, -2, 0), (1.96, 0, 0)])
        second = _curve([(4, -1, 0), (2.05, 0, 0)])
        beside = _curve([(2.13, -1, 0), (2.13, 1, 0)])
        net = PathNetwork([first, second, self.ROUNDED, beside])
        assert net.curves[0].point(net.curves[0].length) == pytest.approx(self.VERTEX)
        assert net.curves[1] is second
        assert {(0, 1), (0, 2), (1, 3)} <= {(j.a, j.b) for j in net.junctions}

    def test_an_end_on_a_closed_path_s_first_point_stays_though_another_corner_point_is_near(self) -> None:
        # The ring's sharp first point lies on its curve, 6 cm from B's corner point.
        ring = _curve([(2.05, -0.03, 0, 0), (3, -1, 0, 0), (3.5, 0.5, 0, 0)], closed=True)
        spur = _curve([(2.05, -2, 0), (2.05, -0.03, 0)])
        net = PathNetwork([spur, ring, self.ROUNDED])
        assert net.curves[0] is spur

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

    def test_an_end_on_its_own_path_s_corner_point_stays(self) -> None:
        hooked = _curve([(0, 0, 0, 0), (2, 0, 0, 0.5), (2, 2, 0, 0), (2.05, 0.05, 0, 0)])
        assert PathNetwork([hooked]).curves[0] is hooked

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

    def test_a_leg_ending_where_a_switch_area_starts_still_moves_on(self) -> None:
        # At 1.3 m/s the area reaches 0.65 m: from 4.35 the leg to it is a rounding error long.
        net = PathNetwork([H, V])
        assert net.walk(Place(0, 4.35), 1.3, 0.0, 1.0).place == Place(0, pytest.approx(5.65))

    def test_a_steady_push_carries_the_marker_through_a_crossing_every_frame(self) -> None:
        net = PathNetwork([H, V])
        place = Place(0, 1.652)
        for _ in range(600):
            step = net.walk(place, 1.3, 0.0, 1 / 60)
            assert step.place.path == 0
            assert step.place.s == pytest.approx(min(place.s + 1.3 / 60, H.length))
            place = step.place
        assert place.s == pytest.approx(H.length)

    @pytest.mark.parametrize("seed", range(4))
    def test_every_frame_of_a_push_along_the_path_moves_the_marker(self, seed: int) -> None:
        rng = random.Random(seed)
        net = PathNetwork([H, V])
        for _ in range(40):
            speed = rng.uniform(0.05, 4.0) * rng.choice((-1.0, 1.0))
            place = Place(0, rng.uniform(0.0, H.length))
            for _ in range(120):
                step = net.walk(place, speed, 0.0, 1 / 60)
                assert step.place.path == 0
                assert step.place.s == pytest.approx(min(max(place.s + speed / 60, 0.0), H.length))
                place = step.place


class TestSteering:
    def test_pushing_straight_on_keeps_the_path_through_a_crossing(self) -> None:
        net = PathNetwork([H, V])
        step = net.walk(Place(0, 4.0), 1.0, 0.0, 3.0)
        assert _at(net, step.place) == (2.0, 0.0)

    def test_pushing_towards_the_other_path_takes_it_at_the_crossing(self) -> None:
        net = PathNetwork([H, V])
        # 60 degrees: 30 off V, 60 off H. Half speed along H reaches the 0.5 m window in 1 s, the push carries the
        # marker on to the crossing in 0.5 s, then 1.5 s up V at 0.87 m/s.
        step = net.walk(Place(0, 4.0), *_push(60.0), dt=3.0)
        assert step.place.path == 1
        assert _at(net, step.place) == pytest.approx((0.0, 1.5 * math.cos(math.radians(30.0))), abs=1e-6)

    def test_a_push_only_a_little_closer_to_the_other_path_stays(self) -> None:
        net = PathNetwork([H, V])
        # 50 degrees: 40 off V, 50 off H - closer to V, but not by the switching margin.
        step = net.walk(Place(0, 4.0), *_push(50.0), dt=4.0)
        assert step.place.path == 0

    def test_standing_on_a_crossing_a_push_along_the_other_path_takes_it(self) -> None:
        net = PathNetwork([H, V])
        step = net.walk(Place(0, 5.0), 0.0, -1.0, 1.0)
        assert _at(net, step.place) == (0.0, -1.0)

    def test_a_marker_in_the_switch_area_runs_on_to_the_crossing_and_takes_it(self) -> None:
        net = PathNetwork([H, V])
        step = net.walk(Place(0, 4.75), 0.0, 1.0, 1.0)
        assert step.place.path == 1
        assert _at(net, step.place) == (0.0, 0.75)

    def test_a_turn_taken_short_of_the_crossing_does_not_jump_there(self) -> None:
        net = PathNetwork([H, V])
        step = net.walk(Place(0, 4.6), 0.0, 1.0, 0.1)
        assert step.place == Place(0, pytest.approx(4.7))

    def test_a_marker_short_of_the_switch_area_cannot(self) -> None:
        net = PathNetwork([H, V])
        # At 1 m/s the area is its 0.5 m floor, also 0.5 s of travel.
        assert net.walk(Place(0, 4.45), 0.0, 1.0, 1.0).place == Place(0, 4.45)

    @pytest.mark.parametrize(
        ("speed", "start", "turns"), [(2.0, 4.05, True), (2.0, 3.95, False), (0.2, 4.55, True), (0.2, 4.45, False)]
    )
    def test_the_switch_area_grows_with_speed_and_never_shrinks_below_its_floor(
        self, speed: float, start: float, turns: bool
    ) -> None:
        net = PathNetwork([H, V])
        step = net.walk(Place(0, start), 0.0, speed, 0.01)
        assert (step.place.path == 1 or step.place.s > start) is turns

    def test_just_past_a_crossing_the_marker_can_still_turn(self) -> None:
        net = PathNetwork([H, V])
        assert net.walk(Place(0, 5.25), 0.0, -1.0, 1.0).place.path == 1

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
        # 0.6 s down the ring to the 0.5 m window, 0.5 s on to the crossing, then 1.5 s along H at 0.87 m/s.
        assert _at(net, step.place) == pytest.approx((-2.0 + 1.5 * math.cos(math.radians(30.0)), 0.0), abs=1e-6)

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


def _tips(arrows: list) -> list[tuple[float, float]]:
    return sorted((round(tip[0], 6), round(tip[1], 6)) for _tail, tip in arrows)


class TestSwitchArrows:
    def test_approaching_a_crossing_shows_every_way_on_but_back(self) -> None:
        net = PathNetwork([H, V])
        arrows = net.switch_arrows(Place(0, 4.8), 1.0)
        assert _tips(arrows) == [(0.0, -0.55), (0.0, 0.55), (0.55, 0.0)]
        tails = sorted((round(t[0], 6), round(t[1], 6)) for t, _tip in arrows)
        assert tails == [(0.0, -0.25), (0.0, 0.25), (0.25, 0.0)]

    def test_standing_on_the_crossing_every_way_shows(self) -> None:
        net = PathNetwork([H, V])
        assert _tips(net.switch_arrows(Place(0, 5.0), 0.0)) == [(-0.55, 0.0), (0.0, -0.55), (0.0, 0.55), (0.55, 0.0)]

    def test_past_the_crossing_the_way_back_to_the_marker_is_left_out(self) -> None:
        net = PathNetwork([H, V])
        assert _tips(net.switch_arrows(Place(0, 5.2), 0.0)) == [(-0.55, 0.0), (0.0, -0.55), (0.0, 0.55)]

    @pytest.mark.parametrize(("start", "speed", "shown"), [(4.4, 1.0, False), (4.1, 2.0, True), (4.75, 0.0, True)])
    def test_the_arrows_show_exactly_where_a_turn_can_be_taken(self, start: float, speed: float, shown: bool) -> None:
        net = PathNetwork([H, V])
        assert bool(net.switch_arrows(Place(0, start), speed)) is shown

    def test_a_branch_shows_straight_on_and_the_way_into_it(self) -> None:
        branch = _curve([(2, 0, 0), (2, 4, 0)])
        net = PathNetwork([H, branch])
        assert _tips(net.switch_arrows(Place(0, 6.8), 1.0)) == [(2.0, 0.55), (2.55, 0.0)]

    def test_an_end_too_close_to_the_junction_draws_no_arrow(self) -> None:
        short = _curve([(-5, 0, 0), (0.1, 0, 0)])
        net = PathNetwork([short, V])
        assert _tips(net.switch_arrows(Place(0, 4.9), 1.0)) == [(0.0, -0.55), (0.0, 0.55)]

    def test_a_lone_path_has_none(self) -> None:
        assert PathNetwork([H]).switch_arrows(Place(0, 5.0), 1.0) == []
