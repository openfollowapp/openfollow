# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The Setup Wizard's camera solve from marks: trilateration, the joint fit and its plausibility,
against a known camera so every pose assert is absolute."""

from __future__ import annotations

import math

import numpy as np
import pytest

from openfollow.configuration import CameraConfig
from openfollow.scene import calibration_points as cp
from openfollow.scene.calibration_points import (
    Mark,
    MarkFit,
    SolveSuperseded,
    look_at,
    plausibility,
    solve_marks,
    trilaterate,
)
from openfollow.scene.solver import project_points

pytestmark = pytest.mark.unit

W, H = 1920.0, 1080.0
# Upstage on a truss, 12 m up, looking downstage at a 12 x 6 m rectangle.
TRUE = np.array([0.0, 13.0, 12.0, -48.0, 180.0, 0.0, 70.0])
DSL, DSR, USR, USL = (6.0, 0.0, 0.0), (-6.0, 0.0, 0.0), (-6.0, 6.0, 0.0), (6.0, 6.0, 0.0)


def _pin(place, camera=TRUE) -> tuple[float, float]:
    x, y = project_points(camera, np.array([place], dtype=np.float64), W, H)[0]
    return float(x), float(y)


def _known(mark_id: int, place, *, pinned: bool = True, typed=None) -> Mark:
    """A known mark at ``place``, typed as ``typed`` when that differs (a typing slip)."""
    x, y, z = typed if typed is not None else place
    return Mark(mark_id, "known", z, (x, y), pin=_pin(place) if pinned else None)


def _triangulated(mark_id: int, place, a: tuple[int, tuple], b: tuple[int, tuple], err_a: float = 0.0) -> Mark:
    (from_a, at_a), (from_b, at_b) = a, b
    return Mark(
        mark_id,
        "triangulated",
        place[2],
        from_a=from_a,
        from_b=from_b,
        distance_a=math.dist(place, at_a) + err_a,
        distance_b=math.dist(place, at_b),
        pin=_pin(place),
    )


def _pose(camera: CameraConfig) -> np.ndarray:
    return np.array([camera.pos_x, camera.pos_y, camera.pos_z, camera.pitch, camera.yaw, camera.roll, camera.fov])


def _assert_true_camera(camera: CameraConfig | None, tol: float = 1e-4) -> None:
    assert camera is not None
    np.testing.assert_allclose(_pose(camera), TRUE, atol=tol)


def _simple(*, off_px: tuple[float, float] = (0.0, 0.0)) -> list[Mark]:
    marks = [_known(i, place) for i, place in enumerate((DSL, DSR, USR, USL), start=1)]
    pin = marks[0].pin
    assert pin is not None
    marks[0] = Mark(1, "known", 0.0, (DSL[0], DSL[1]), pin=(pin[0] + off_px[0], pin[1] + off_px[1]))
    return marks


# Advanced: a reference point and DSL known, two points measured from them, the second from the first.
REF = (0.0, 0.0, 0.0)
P3, P4 = (2.0, 4.0, 0.0), (-3.0, 5.0, 0.5)


def _advanced(err_a: float = 0.0) -> list[Mark]:
    return [
        _known(1, REF),
        _known(2, DSL),
        _triangulated(3, P3, (1, REF), (2, DSL)),
        _triangulated(4, P4, (3, P3), (1, REF), err_a=err_a),
    ]


class TestTrilaterate:
    def test_two_circles_meet_at_mirror_images_across_the_line_between_their_points(self) -> None:
        first, second = trilaterate(REF, DSL, 5.0, 5.0, 0.0)
        assert {tuple(np.round(p, 9)) for p in (first, second)} == {(3.0, 4.0, 0.0), (3.0, -4.0, 0.0)}

    def test_a_point_above_its_two_points_takes_its_height(self) -> None:
        places = trilaterate(REF, DSL, math.sqrt(25 + 4), math.sqrt(25 + 4), 2.0)
        assert not isinstance(places, str)
        assert {tuple(np.round(p, 9)) for p in places} == {(3.0, 4.0, 2.0), (3.0, -4.0, 2.0)}

    @pytest.mark.parametrize(
        ("distance_a", "distance_b", "x"),
        [
            (2.95, 2.95, 3.0),  # 10 cm apart: midway between the facing edges
            (9.1, 3.0, 9.05),  # b's circle 10 cm inside a's: midway between the far edges
            (3.0, 9.1, -3.05),  # a's circle inside b's
        ],
    )
    def test_circles_that_miss_by_the_tolerance_meet_on_the_line(self, distance_a, distance_b, x) -> None:
        places = trilaterate(REF, DSL, distance_a, distance_b, 0.0)
        assert not isinstance(places, str)
        for place in places:
            assert tuple(np.round(place, 9)) == (x, 0.0, 0.0)

    @pytest.mark.parametrize(
        ("a", "b", "distance_a", "distance_b", "z", "reason"),
        [
            (REF, DSL, 1.0, 6.0, 2.0, cp.REASON_HEIGHT),
            (REF, DSL, 6.0, 1.0, -2.0, cp.REASON_HEIGHT),
            (REF, (0.01, 0.0, 0.0), 5.0, 5.0, 0.0, cp.REASON_SAME_PLACE),
            (REF, DSL, 10.0, 3.0, 0.0, cp.REASON_DIFFER),
            (REF, DSL, 2.9, 2.9, 0.0, cp.REASON_SHORT),
        ],
    )
    def test_distances_that_place_it_nowhere_say_why(self, a, b, distance_a, distance_b, z, reason) -> None:
        assert trilaterate(a, b, distance_a, distance_b, z) == reason

    def test_the_tolerance_is_the_noise_of_two_distances(self) -> None:
        assert math.isclose(cp.MEET_TOLERANCE, 3.0 * math.sqrt(2.0) * 0.04)
        gap = cp.MEET_TOLERANCE
        assert not isinstance(trilaterate(REF, DSL, 3.0 - gap / 2 + 1e-6, 3.0 - gap / 2 + 1e-6, 0.0), str)
        assert trilaterate(REF, DSL, 3.0 - gap / 2 - 1e-3, 3.0 - gap / 2 - 1e-3, 0.0) == cp.REASON_SHORT


class TestLookAt:
    def test_a_camera_upstage_looking_at_the_origin_faces_downstage_and_down(self) -> None:
        pitch, yaw = look_at((0.0, 13.0, 12.0), (0.0, 0.0, 0.0))
        assert yaw == pytest.approx(180.0)
        assert pitch == pytest.approx(-math.degrees(math.atan2(12.0, 13.0)))
        assert pitch < 0.0

    def test_a_target_stage_left_turns_the_camera_to_a_positive_yaw(self) -> None:
        pitch, yaw = look_at((0.0, -10.0, 5.0), (5.0, 0.0, 0.0))
        assert yaw == pytest.approx(math.degrees(math.atan2(5.0, 10.0)))
        assert yaw > 0.0

    @pytest.mark.parametrize(
        ("position", "target"),
        [
            ((0.0, 13.0, 12.0), (0.0, 0.0, 0.0)),
            ((4.0, -12.0, 6.0), (-2.0, 3.0, 1.0)),
            ((-8.0, 2.0, 9.0), (3.0, 1.0, 0.0)),
        ],
    )
    def test_the_target_lands_in_the_middle_of_the_picture(self, position, target) -> None:
        pitch, yaw = look_at(position, target)
        assert _pin(target, np.array([*position, pitch, yaw, 0.0, 60.0])) == pytest.approx((W / 2, H / 2))


class TestSimple:
    def test_four_level_corners_without_a_camera_position_give_the_camera(self) -> None:
        solved = solve_marks(_simple(), W, H)
        _assert_true_camera(solved.camera)
        assert solved.error == ""

    @pytest.mark.parametrize("hint", [(0.0, 13.0, 12.0), (5.0, 9.0, 9.0), (0.0, -13.0, 12.0)])
    def test_a_camera_position_good_or_far_off_still_gives_the_camera(self, hint) -> None:
        _assert_true_camera(solve_marks(_simple(), W, H, hint=hint).camera)

    def test_each_corner_is_seen_where_it_is_pinned(self) -> None:
        marks = _simple()
        solved = solve_marks(marks, W, H)
        for mark, fit in zip(marks, solved.marks, strict=True):
            assert fit.id == mark.id
            assert fit.seen == pytest.approx(mark.pin, abs=1e-3)
            assert fit.distance_error is None
            assert fit.reason == ""

    def test_a_corner_pinned_40_px_off_still_solves_without_a_camera_position(self) -> None:
        marks = _simple(off_px=(40.0, 0.0))
        solved = solve_marks(marks, W, H)
        assert solved.camera is not None
        assert solved.camera.pos_z > 0.0 and solved.camera.pitch < 0.0
        np.testing.assert_allclose(_pose(solved.camera)[:3], TRUE[:3], atol=2.0)


class TestAdvanced:
    def test_two_known_and_chained_triangulated_marks_give_the_camera_and_the_places(self) -> None:
        solved = solve_marks(_advanced(), W, H, hint=(0.0, 13.0, 12.0))
        _assert_true_camera(solved.camera)
        by_id = {f.id: f for f in solved.marks}
        assert by_id[3].position == pytest.approx(P3, abs=1e-6)
        assert by_id[4].position == pytest.approx(P4, abs=1e-6)
        assert by_id[3].distance_error == pytest.approx(0.0, abs=1e-6)
        assert by_id[1].distance_error is None

    def test_a_known_mark_outside_the_picture_needs_no_pin(self) -> None:
        behind = (0.0, 12.0, 0.0)
        assert _pin(behind)[1] > H  # below the picture
        marks = [
            _known(1, behind, pinned=False),
            _known(2, DSL),
            _known(3, DSR),
            _triangulated(4, P3, (1, behind), (2, DSL)),
            _triangulated(5, P4, (1, behind), (3, DSR)),
        ]
        solved = solve_marks(marks, W, H, hint=(0.0, 13.0, 12.0))
        _assert_true_camera(solved.camera)
        outside = solved.marks[0]
        assert outside.position == pytest.approx(behind)
        assert outside.seen == pytest.approx(_pin(behind), abs=1e-3)

    def test_a_camera_position_6_m_off_still_puts_the_marks_on_their_side(self) -> None:
        """From there the pins pick the mirror places; the other side is fitted as well and wins."""
        solved = solve_marks(_advanced(), W, H, hint=(6.0, 7.0, 12.0))
        _assert_true_camera(solved.camera)
        assert solved.camera is not None and solved.camera.pos_z > 0.0
        assert {f.id: f.position for f in solved.marks}[3] == pytest.approx(P3, abs=1e-6)

    def test_a_mistyped_distance_shows_in_the_distance_error_and_the_caution(self) -> None:
        """Four pinned corners hold the camera, so a distance 30 cm long cannot hide in it."""
        marks = [*_simple(), _triangulated(5, (1.0, 3.0, 1.0), (1, DSL), (2, DSR), err_a=0.3)]
        solved = solve_marks(marks, W, H, hint=(0.0, 13.0, 12.0))
        _assert_true_camera(solved.camera, tol=0.5)
        assert solved.camera is not None
        fit = {f.id: f for f in solved.marks}[5]
        assert fit.distance_error == pytest.approx(0.26, abs=0.02)
        reasons = plausibility(solved.camera, solved.marks, {}, (0.0, 13.0, 12.0), per_axis=True)
        assert [r["id"] for r in reasons if r["kind"] == "distance"] == [5]

    def test_a_mistyped_known_coordinate_moves_the_camera_off_its_position(self) -> None:
        hint = (0.0, 13.0, 12.0)
        marks = [
            _known(1, REF, typed=(0.5, 0.0, 0.0)),
            _known(2, DSL),
            _triangulated(3, P3, (1, REF), (2, DSL)),
            _triangulated(4, P4, (3, P3), (1, REF)),
        ]
        solved = solve_marks(marks, W, H, hint=hint)
        assert solved.camera is not None
        (offset,) = [
            r for r in plausibility(solved.camera, solved.marks, {}, hint, per_axis=True) if r["kind"] == "offset"
        ]
        assert offset["per_axis"] is True
        assert max(abs(v) for v in offset["delta"]) > offset["allowed"] >= 2.5

    def test_a_mark_its_distances_place_nowhere_carries_the_reason_and_the_rest_solve(self) -> None:
        nowhere = Mark(5, "triangulated", 0.0, from_a=1, from_b=2, distance_a=1.0, distance_b=1.0, pin=(900.0, 500.0))
        marks = [*_advanced(), _known(6, DSR), nowhere]
        solved = solve_marks(marks, W, H, hint=(0.0, 13.0, 12.0))
        _assert_true_camera(solved.camera)
        fit = {f.id: f for f in solved.marks}[5]
        assert (fit.position, fit.reason, fit.seen) == (None, cp.REASON_SHORT, None)

    def test_a_mark_measured_from_one_placed_nowhere_says_so(self) -> None:
        nowhere = Mark(5, "triangulated", 0.0, from_a=1, from_b=2, distance_a=1.0, distance_b=1.0, pin=(900.0, 500.0))
        after = Mark(7, "triangulated", 0.0, from_a=5, from_b=1, distance_a=3.0, distance_b=3.0, pin=(800.0, 500.0))
        marks = [*_advanced(), _known(6, DSR), nowhere, after]
        fit = {f.id: f for f in solve_marks(marks, W, H, hint=(0.0, 13.0, 12.0)).marks}[7]
        assert (fit.position, fit.reason) == (None, cp.REASON_UNPLACED_FROM)


class TestNoCamera:
    def test_without_a_camera_position_or_four_level_known_marks_nothing_seeds_the_fit(self) -> None:
        solved = solve_marks(_advanced(), W, H)
        assert (solved.camera, solved.error) == (None, cp.NO_FIT)
        assert [(f.id, f.position) for f in solved.marks] == [(1, None), (2, None), (3, None), (4, None)]

    def test_four_known_marks_at_different_heights_seed_nothing_without_a_camera_position(self) -> None:
        """Only a level four seeds through a homography; a tilted one would seed a wrong camera."""
        marks = [_known(1, DSL), _known(2, DSR), _known(3, USR), _known(4, (6.0, 6.0, 1.0))]
        assert solve_marks(marks, W, H).error == cp.NO_FIT
        _assert_true_camera(solve_marks(marks, W, H, hint=(0.0, 13.0, 12.0)).camera)

    def test_fewer_than_four_pins_fit_no_camera(self) -> None:
        marks = [*_simple()[:3], _known(4, USL, pinned=False)]
        solved = solve_marks(marks, W, H, hint=(0.0, 13.0, 12.0))
        assert (solved.camera, solved.error) == (None, cp.NO_FIT)

    def test_a_mark_placed_nowhere_counts_no_pin(self) -> None:
        nowhere = Mark(4, "triangulated", 0.0, from_a=1, from_b=2, distance_a=1.0, distance_b=1.0, pin=(900.0, 500.0))
        solved = solve_marks([*_simple()[:3], nowhere], W, H, hint=(0.0, 13.0, 12.0))
        assert (solved.camera, solved.error) == (None, cp.NO_FIT)
        assert {f.id: f.reason for f in solved.marks}[4] == cp.REASON_SHORT

    def test_a_newer_solve_supersedes_this_one(self) -> None:
        with pytest.raises(SolveSuperseded):
            solve_marks(_simple(), W, H, stale=lambda: True)

    def test_a_solve_nobody_superseded_asks_and_finishes(self) -> None:
        asked = []
        solved = solve_marks(_simple(), W, H, stale=lambda: asked.append(1) is not None)
        _assert_true_camera(solved.camera)
        assert asked


# One mark at 0/0/0: the camera's distance to the marks is its distance to the origin.
_AT_ORIGIN = (MarkFit(1, (0.0, 0.0, 0.0)),)


def _camera(**changes: float) -> CameraConfig:
    pose = dict(zip(("pos_x", "pos_y", "pos_z", "pitch", "yaw", "roll", "fov"), TRUE.tolist(), strict=True))
    return CameraConfig(**{**pose, **changes})


def _kinds(reasons: list[dict]) -> list[str]:
    return [r["kind"] for r in reasons]


def test_a_camera_that_matches_everything_raises_nothing() -> None:
    assert plausibility(_camera(), _AT_ORIGIN, {1: 0.5}, (0.0, 13.0, 12.0), per_axis=True) == []


@pytest.mark.parametrize(
    ("off", "fires"),
    [((2.4, 0.0, 0.0), False), ((2.6, 0.0, 0.0), True), ((0.0, 0.0, -2.6), True), ((2.0, 2.0, 2.0), False)],
)
def test_advanced_checks_each_axis_against_2_5_m_near_the_marks(off, fires) -> None:
    hint = (TRUE[0] - off[0], TRUE[1] - off[1], TRUE[2] - off[2])
    reasons = plausibility(_camera(), _AT_ORIGIN, {}, hint, per_axis=True)
    assert ("offset" in _kinds(reasons)) is fires
    if fires:
        (offset,) = reasons
        assert offset["delta"] == pytest.approx(list(off))
        assert offset["allowed"] == 2.5
        assert offset["per_axis"] is True


@pytest.mark.parametrize(("off", "fires"), [(4.9, False), (5.1, True)])
def test_advanced_allows_a_tenth_of_the_distance_on_a_far_camera(off, fires) -> None:
    far = _camera(pos_x=0.0, pos_y=40.0, pos_z=30.0)  # 50 m from the marks
    reasons = plausibility(far, _AT_ORIGIN, {}, (off, 40.0, 30.0), per_axis=True)
    assert ("offset" in _kinds(reasons)) is fires
    if fires:
        assert reasons[0]["allowed"] == pytest.approx(5.0)


@pytest.mark.parametrize(("off", "fires"), [((0.5, 0.5, 0.5), False), ((0.6, 0.6, 0.6), True)])
def test_simple_checks_the_whole_gap_against_1_m_near_the_marks(off, fires) -> None:
    near = _camera(pos_x=0.0, pos_y=3.0, pos_z=5.0)  # under 10 m from the marks
    hint = (-off[0], 3.0 - off[1], 5.0 - off[2])
    reasons = plausibility(near, _AT_ORIGIN, {}, hint, per_axis=False)
    assert ("offset" in _kinds(reasons)) is fires
    if fires:
        assert reasons[0]["off"] == pytest.approx(math.sqrt(3 * 0.36))
        assert (reasons[0]["allowed"], reasons[0]["per_axis"]) == (1.0, False)
    assert "offset" not in _kinds(plausibility(near, _AT_ORIGIN, {}, hint, per_axis=True))


@pytest.mark.parametrize(("off", "fires"), [(1.7, False), (1.8, True)])
def test_simple_allows_a_tenth_of_the_distance_on_a_far_camera(off, fires) -> None:
    span = math.hypot(13.0, 12.0)
    reasons = plausibility(_camera(), _AT_ORIGIN, {}, (off, 13.0, 12.0), per_axis=False)
    assert ("offset" in _kinds(reasons)) is fires
    if fires:
        assert reasons[0]["allowed"] == pytest.approx(0.1 * span)


def test_without_a_camera_position_the_offset_is_not_checked() -> None:
    assert plausibility(_camera(pos_x=40.0), _AT_ORIGIN, {}, None, per_axis=True) == []


@pytest.mark.parametrize(("fov", "fires"), [(19.9, True), (20.0, False), (120.0, False), (120.1, True)])
def test_a_field_of_view_outside_20_to_120_degrees_is_named(fov, fires) -> None:
    reasons = plausibility(_camera(fov=fov), _AT_ORIGIN, {}, None, per_axis=True)
    assert reasons == ([{"kind": "fov", "fov": fov}] if fires else [])


@pytest.mark.parametrize(("pos_z", "fires"), [(-0.01, True), (0.0, False)])
def test_a_camera_below_the_lowest_mark_is_named(pos_z, fires) -> None:
    reasons = plausibility(_camera(pos_z=pos_z), _AT_ORIGIN, {}, None, per_axis=True)
    assert ("below" in _kinds(reasons)) is fires


@pytest.mark.parametrize(("pitch", "fires"), [(0.5, True), (0.0, False)])
def test_a_camera_looking_up_is_named(pitch, fires) -> None:
    reasons = plausibility(_camera(pitch=pitch), _AT_ORIGIN, {}, None, per_axis=True)
    assert ("up" in _kinds(reasons)) is fires


@pytest.mark.parametrize(("px", "fires"), [(5.0, False), (5.01, True)])
def test_a_pin_more_than_5_px_off_is_named(px, fires) -> None:
    reasons = plausibility(_camera(), _AT_ORIGIN, {1: px}, None, per_axis=True)
    assert reasons == ([{"kind": "pixel", "id": 1, "px": px}] if fires else [])


@pytest.mark.parametrize(("error", "fires"), [(0.119, False), (0.121, True)])
def test_distances_more_than_12_cm_off_are_named(error, fires) -> None:
    fits = (MarkFit(3, (1.0, 2.0, 0.0), distance_error=error),)
    reasons = plausibility(_camera(), fits, {}, None, per_axis=True)
    assert reasons == ([{"kind": "distance", "id": 3, "m": error}] if fires else [])
