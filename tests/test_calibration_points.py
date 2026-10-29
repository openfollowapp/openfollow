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
    pin_errors,
    plausibility,
    solve_marks,
    trilaterate,
)
from openfollow.scene.solver import apply_overlay_distortion, project_points

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


def _triangulated(
    mark_id: int,
    place,
    a: tuple[int, tuple],
    b: tuple[int, tuple],
    err_a: float = 0.0,
    err_b: float = 0.0,
    camera=TRUE,
) -> Mark:
    (from_a, at_a), (from_b, at_b) = a, b
    return Mark(
        mark_id,
        "triangulated",
        place[2],
        from_a=from_a,
        from_b=from_b,
        distance_a=math.dist(place, at_a) + err_a,
        distance_b=math.dist(place, at_b) + err_b,
        pin=_pin(place, camera),
    )


def _pose(camera: CameraConfig) -> np.ndarray:
    return np.array([camera.pos_x, camera.pos_y, camera.pos_z, camera.pitch, camera.yaw, camera.roll, camera.fov])


def _assert_true_camera(camera: CameraConfig | None, tol: float = 1e-4) -> None:
    """Angles compared a whole turn apart or not: yaw 180 and -179.99999 are one bearing."""
    assert camera is not None
    off = _pose(camera) - TRUE
    off[3:6] = (off[3:6] + 180.0) % 360.0 - 180.0
    np.testing.assert_allclose(off, np.zeros(7), atol=tol)


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

    # The last has the upstage corners behind it: its seeds project nothing and fit nothing.
    @pytest.mark.parametrize("hint", [(0.0, 13.0, 12.0), (5.0, 9.0, 9.0), (0.0, -13.0, 12.0), (0.0, 3.0, 1.0)])
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

    @pytest.mark.parametrize("z", [0.0, 0.5])
    def test_known_corners_at_different_heights_give_the_camera_without_a_camera_position(self, z) -> None:
        """Seeded from above the marks when no four of them are level."""
        marks = [*_simple()[:3], _known(4, (USL[0], USL[1], z))]
        _assert_true_camera(solve_marks(marks, W, H).camera)

    @pytest.mark.parametrize(("height", "error"), [(None, cp.NO_FIT), (75.0, cp.NO_FIT_AT_HEIGHT)])
    def test_a_camera_too_narrow_to_be_one_fits_nothing(self, height, error) -> None:
        tele = np.array([0.0, 100.0, 75.0, -36.9, 180.0, 0.0, 7.0])
        marks = [Mark(i, "known", 0.0, (p[0], p[1]), pin=_pin(p, tele)) for i, p in enumerate((DSL, DSR, USR, USL), 1)]
        solved = solve_marks(marks, W, H, hint=tuple(tele[:3]), height=height)
        assert (solved.camera, solved.error) == (None, error)

    @pytest.mark.parametrize("full_fit", ["fails", "sinks below the floor"])
    def test_the_brief_fit_answers_when_the_full_fit_ranks_lower(
        self, full_fit: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fit = cp._fit

        def full_fit_goes_wrong(problem, x0, stale, iterations):
            result = fit(problem, x0, stale, iterations)
            if iterations < cp.FIT_ITERATIONS:
                return result
            if full_fit == "fails" or result is None:
                return None
            below = result[0].copy()
            below[2] = -below[2]
            return below, result[1]

        monkeypatch.setattr(cp, "_fit", full_fit_goes_wrong)
        _assert_true_camera(solve_marks(_simple(), W, H).camera, tol=1e-3)

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

    def test_the_saved_field_of_view_seeds_the_fit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cp, "SEED_FOVS", ())
        _assert_true_camera(solve_marks(_advanced(), W, H, hint=(0.0, 13.0, 12.0), saved_fov=70.0).camera)

    def test_a_side_that_places_fewer_marks_loses_to_one_that_places_them_all(self) -> None:
        """On the flipped side T5 leaves T6 nowhere, and a fit without T6 costs less."""
        t5, t6 = (1.0, 3.0, 0.0), (3.5, 4.4, 0.0)
        marks = [*_simple(), _triangulated(5, t5, (1, DSL), (2, DSR)), _triangulated(6, t6, (5, t5), (4, USL))]
        solved = solve_marks(marks, W, H, hint=(0.0, 13.0, 12.0))
        by_id = {f.id: f for f in solved.marks}
        assert by_id[5].position == pytest.approx(t5, abs=1e-6)
        assert by_id[6].position == pytest.approx(t6, abs=1e-6)

    def test_a_side_that_drops_a_mark_loses_though_its_fit_costs_less(self) -> None:
        """T6's circles miss by 20 cm from T5's true place, and meet from where T5's distances put it."""
        t5, t6, miss = (1.0, 3.0, 0.0), (3.5, 4.5, 0.0), 0.2
        marks = [
            *_simple(),
            _triangulated(5, t5, (1, DSL), (2, DSR), err_a=0.04, err_b=0.04),
            _triangulated(6, t6, (5, t5), (4, USL), err_a=-miss / 2, err_b=-miss / 2),
        ]
        fit = {f.id: f for f in solve_marks(marks, W, H, hint=(0.0, 13.0, 12.0)).marks}[6]
        assert fit.reason == ""
        assert fit.position == pytest.approx(t6, abs=0.05)

    def test_a_chain_its_seed_places_too_far_off_is_placed_from_the_fit(self) -> None:
        """2 cm per distance moves 8 enough that 9's circles miss; from the fitted 8 they meet, and 10, measured
        from 9, lands on the side its pin picks."""
        p7, p8, p9, p10 = (4.6, 1.9, 0.0), (-0.2, 6.4, 0.0), (-0.8, 4.5, 0.0), (-4.0, 3.0, 0.0)
        marks = [
            _known(1, REF),
            _known(2, DSL),
            _known(3, DSR),
            _triangulated(7, p7, (1, REF), (2, DSL), err_a=0.02, err_b=-0.02),
            _triangulated(8, p8, (7, p7), (2, DSL), err_a=-0.02, err_b=0.02),
            _triangulated(9, p9, (8, p8), (1, REF), err_a=-0.02, err_b=0.02),
            _triangulated(10, p10, (9, p9), (3, DSR)),
        ]
        solved = solve_marks(marks, W, H, hint=(0.0, 13.0, 12.0))
        assert solved.camera is not None
        np.testing.assert_allclose(_pose(solved.camera)[:3], TRUE[:3], atol=0.5)
        by_id = {f.id: f for f in solved.marks}
        for mark_id, place in ((9, p9), (10, p10)):
            assert by_id[mark_id].reason == ""
            assert by_id[mark_id].position == pytest.approx(place, abs=0.05)

    @pytest.mark.parametrize("hint", [(0.0, 0.0, 10.0), (0.0, -13.0, 10.0)], ids=["above", "above the pinning camera"])
    def test_an_upright_camera_beats_a_cheaper_one_below_the_floor(self, hint: tuple[float, float, float]) -> None:
        """Pinned from under the stage looking up, the same pins fit a camera above it as well; from straight above
        the pinning camera the fit reaches both."""
        below = np.array([0.0, -13.0, -12.0, 48.0, 0.0, 180.0, 70.0])
        p3, p4 = (2.0, -4.0, 0.0), (-3.0, -5.0, 0.4)
        marks = [
            Mark(1, "known", 0.0, (0.0, 0.0), pin=_pin(REF, below)),
            Mark(2, "known", 0.0, (DSL[0], DSL[1]), pin=_pin(DSL, below)),
            _triangulated(3, p3, (1, REF), (2, DSL), camera=below),
            _triangulated(4, p4, (3, p3), (1, REF), camera=below),
        ]
        camera = solve_marks(marks, W, H, hint=hint).camera
        assert camera is not None
        assert camera.pos_z >= 0.0 and camera.pitch <= 0.0

    def test_an_upright_camera_that_places_fewer_marks_beats_one_far_under_the_floor(self) -> None:
        """From where the seed puts 4, 5's circles cannot meet; the other side places every mark, but only for a
        camera some 30 m under the floor looking up. From the upright fit 5 meets."""
        truth = (-5.33, 8.36, 3.17)
        marks = [
            Mark(1, "known", 0.0, (0.0, 0.0), pin=(1267.1, 458.5)),
            Mark(2, "known", 0.0, (1.6, 0.5), pin=(1115.4, 436.2)),
            Mark(3, "triangulated", 0.3, from_a=2, from_b=1, distance_a=6.27, distance_b=6.53, pin=(664.6, 635.2)),
            Mark(4, "triangulated", 0.3, from_a=2, from_b=3, distance_a=8.19, distance_b=6.05, pin=(315.2, 447.9)),
            Mark(5, "triangulated", 0.0, from_a=1, from_b=4, distance_a=3.31, distance_b=11.28, pin=(1572.3, 586.1)),
            Mark(6, "triangulated", 0.2, from_a=3, from_b=1, distance_a=2.55, distance_b=7.13, pin=(810.1, 898.1)),
        ]
        solved = solve_marks(marks, W, H, hint=(-3.8, 7.5, 2.7))
        assert solved.camera is not None
        np.testing.assert_allclose(_pose(solved.camera)[:3], truth, atol=0.1)
        assert all(f.position is not None for f in solved.marks)

    @pytest.mark.parametrize("turns", [cp.STEEP_TURNS, (0.0,)], ids=["quarter turns", "one seed"])
    def test_a_fit_turned_past_straight_down_answers_the_same_view_upright(
        self, turns: tuple[float, ...], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """From straight above the origin, seeded once, the fit passes -90° of pitch: -132° is -48° turned half
        round."""
        monkeypatch.setattr(cp, "STEEP_TURNS", turns)
        marks = [
            _known(1, REF),
            _known(2, DSL),
            _triangulated(3, P3, (1, REF), (2, DSL)),
            _triangulated(4, (-3.0, 5.0, 0.0), (3, P3), (1, REF)),
        ]
        _assert_true_camera(solve_marks(marks, W, H, hint=(0.0, 0.0, 10.0)).camera)

    @pytest.mark.parametrize("turns", [cp.STEEP_TURNS, (0.0,)], ids=["quarter turns", "one seed"])
    def test_a_fit_just_past_straight_down_answers_the_same_view_upright(
        self, turns: tuple[float, ...], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """From above the origin, seeded once, this camera fits at -98° of pitch: -82° turned half round."""
        monkeypatch.setattr(cp, "STEEP_TURNS", turns)
        steep = np.array([0.0, 2.0, 12.0, -82.0, 180.0, 0.0, 70.0])
        p3, p4 = (2.0, 4.0, 0.0), (-3.0, 2.0, 0.0)
        marks = [
            Mark(1, "known", 0.0, (0.0, 0.0), pin=_pin(REF, steep)),
            Mark(2, "known", 0.0, (DSL[0], DSL[1]), pin=_pin(DSL, steep)),
            _triangulated(3, p3, (1, REF), (2, DSL), camera=steep),
            _triangulated(4, p4, (3, p3), (1, REF), camera=steep),
        ]
        camera = solve_marks(marks, W, H, hint=(0.0, 0.0, 12.0)).camera
        assert camera is not None
        pose = _pose(camera)
        pose[4] %= 360.0
        np.testing.assert_allclose(pose, steep, atol=1e-4)

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

    def test_a_mark_measured_from_a_mark_that_is_not_there_says_so(self) -> None:
        orphan = Mark(5, "triangulated", 0.0, from_a=1, from_b=9, distance_a=3.0, distance_b=3.0, pin=(800.0, 500.0))
        solved = solve_marks([*_advanced(), orphan], W, H, hint=(0.0, 13.0, 12.0))
        _assert_true_camera(solved.camera)
        fit = {f.id: f for f in solved.marks}[5]
        assert (fit.position, fit.reason) == (None, cp.REASON_UNPLACED_FROM)

    def test_a_triangulated_mark_without_a_pin_is_placed_by_its_distances_alone(self) -> None:
        place = (1.0, 2.0, 0.0)
        to_ref, to_dsl = math.dist(place, REF), math.dist(place, DSL)
        unpinned = Mark(5, "triangulated", 0.0, from_a=1, from_b=2, distance_a=to_ref, distance_b=to_dsl)
        solved = solve_marks([*_advanced(), unpinned], W, H, hint=(0.0, 13.0, 12.0))
        _assert_true_camera(solved.camera)
        fit = {f.id: f for f in solved.marks}[5]
        assert fit.distance_error == pytest.approx(0.0, abs=1e-6)
        assert fit.position is not None
        assert abs(fit.position[1]) == pytest.approx(2.0, abs=1e-6)  # either side of the line from REF to DSL
        assert (fit.position[0], fit.position[2]) == pytest.approx((1.0, 0.0), abs=1e-6)

    def test_a_mark_measured_0_m_from_a_mark_sits_on_it(self) -> None:
        solved = solve_marks([*_advanced(), _triangulated(5, REF, (1, REF), (2, DSL))], W, H, hint=(0.0, 13.0, 12.0))
        _assert_true_camera(solved.camera)
        fit = {f.id: f for f in solved.marks}[5]
        assert fit.position == pytest.approx(REF, abs=1e-6)
        assert fit.distance_error == pytest.approx(0.0, abs=1e-6)


def _turned(place: tuple[float, float, float], turn: float) -> tuple[float, float, float]:
    """``place`` turned about 0/0/0 so that its bearing turns by ``turn`` degrees."""
    c, s = math.cos(math.radians(turn)), math.sin(math.radians(turn))
    return (c * place[0] + s * place[1], -s * place[0] + c * place[1], place[2])


# Raked: USL stands 50 cm up, so no four corners seed through a homography.
_STEEP_CORNERS = ((4.0, -3.0, 0.0), (-4.0, -3.0, 0.0), (-4.0, 2.0, 0.0), (4.0, 2.0, 0.5))
_STEEP_POINTS = ((0.0, 0.0, 0.0), (-3.0, 1.5, 0.0), (2.5, 1.0, 0.0), (-1.0, -2.5, 0.3), (2.0, -1.5, 0.8))


class TestSteepCamera:
    """1 m upstage of 0/0/0, 12 m up, 5° off straight down; the camera position 30 cm past 0/0/0, so looking
    at 0/0/0 from there faces the opposite way. The whole stage is turned with the camera's yaw."""

    @pytest.mark.parametrize("yaw", [180.0, 0.0, 90.0, -90.0])
    @pytest.mark.parametrize("mode", ["simple", "advanced"])
    def test_a_camera_position_above_the_marks_gives_the_camera_at_any_bearing(self, yaw: float, mode: str) -> None:
        turn = yaw - 180.0
        steep = np.array([*_turned((0.0, 1.0, 12.0), turn), -85.0, yaw, 0.0, 60.0])
        if mode == "simple":
            places = [_turned(p, turn) for p in _STEEP_CORNERS]
            marks = [Mark(i, "known", p[2], (p[0], p[1]), pin=_pin(p, steep)) for i, p in enumerate(places, 1)]
        else:
            ref, k2, p3, p4, p5 = (_turned(p, turn) for p in _STEEP_POINTS)
            marks = [
                Mark(1, "known", 0.0, (ref[0], ref[1]), pin=_pin(ref, steep)),
                Mark(2, "known", 0.0, (k2[0], k2[1]), pin=_pin(k2, steep)),
                _triangulated(3, p3, (1, ref), (2, k2), camera=steep),
                _triangulated(4, p4, (3, p3), (1, ref), camera=steep),
                _triangulated(5, p5, (4, p4), (1, ref), camera=steep),
            ]
        assert all(0.0 < m.pin[0] < W and 0.0 < m.pin[1] < H for m in marks if m.pin)
        solved = solve_marks(marks, W, H, hint=_turned((0.0, -0.3, 10.0), turn))
        assert solved.camera is not None
        pose = _pose(solved.camera)
        np.testing.assert_allclose(pose[:3], steep[:3], atol=0.01)
        assert pose[3] == pytest.approx(-85.0, abs=0.1)
        assert (pose[4] - yaw + 180.0) % 360.0 - 180.0 == pytest.approx(0.0, abs=1.0)
        for mark, fit in zip(marks, solved.marks, strict=True):
            assert fit.seen == pytest.approx(mark.pin, abs=0.1)


# 12 m up, 2° off straight down: four level corners whose pins are each about half a pixel off.
_STRAIGHT_DOWN = np.array([0.0, 1.0, 12.0, -88.0, 180.0, 0.0, 70.0])
_UNDER_IT = ((6.0, -1.0, 0.0), (-6.0, -1.0, 0.0), (-6.0, 3.0, 0.0), (6.0, 3.0, 0.0))
_HALF_PX = ((0.6, -0.4), (-0.5, 0.5), (0.4, 0.6), (-0.6, -0.3))


def _straight_down_corners() -> list[Mark]:
    pins = [_pin(p, _STRAIGHT_DOWN) for p in _UNDER_IT]
    return [
        Mark(i, "known", p[2], (p[0], p[1]), pin=(pin[0] + dx, pin[1] + dy))
        for i, (p, pin, (dx, dy)) in enumerate(zip(_UNDER_IT, pins, _HALF_PX, strict=True), start=1)
    ]


class TestCameraHeight:
    @pytest.mark.parametrize("hint", [None, (0.0, 2.0, 10.0)], ids=["no position", "position 2 m off"])
    def test_straight_down_half_a_pixel_moves_the_camera_metres_without_the_height(self, hint) -> None:
        camera = solve_marks(_straight_down_corners(), W, H, hint=hint).camera
        assert camera is not None
        assert abs(camera.pos_z - 12.0) > 1.0

    @pytest.mark.parametrize("hint", [None, (0.0, 2.0, 10.0)], ids=["no position", "position 2 m off"])
    def test_straight_down_the_entered_height_holds_the_camera_within_centimetres(self, hint) -> None:
        camera = solve_marks(_straight_down_corners(), W, H, hint=hint, height=12.0).camera
        assert camera is not None
        np.testing.assert_allclose(_pose(camera)[:3], _STRAIGHT_DOWN[:3], atol=0.08)
        assert camera.pos_z == pytest.approx(12.0, abs=0.01)
        assert camera.fov == pytest.approx(70.0, abs=0.5)

    def test_the_entered_height_is_weighed_at_a_centimetre(self) -> None:
        """Four exact corners hold the camera's height loosely: one entered 20 cm higher moves it nearly all the way."""
        camera = solve_marks(_simple(), W, H, hint=(0.0, 13.0, 12.0), height=12.2).camera
        assert camera is not None
        assert camera.pos_z == pytest.approx(12.2, abs=0.01)

    @pytest.mark.parametrize(("hint", "level"), [((0.0, 2.0, 10.0), True), ((0.0, 2.0, 10.0), False), (None, True)])
    def test_every_seed_starts_at_the_entered_height(self, monkeypatch: pytest.MonkeyPatch, hint, level) -> None:
        starts: list[np.ndarray] = []
        fit = cp._fit
        monkeypatch.setattr(cp, "_fit", lambda problem, x0, *a: starts.append(x0.copy()) or fit(problem, x0, *a))
        marks = _straight_down_corners()
        if not level:
            marks[3] = Mark(4, "known", 0.5, marks[3].position, pin=marks[3].pin)
        solve_marks(marks, W, H, hint=hint, height=11.0)
        assert len(starts) > 1
        # The full fit starts where the best brief fit ended.
        assert {float(x0[2]) for x0 in starts[:-1]} == {11.0}
        if hint is not None:
            # From the position at that height, looking at 0/0/0.
            assert starts[0][3] == pytest.approx(look_at((hint[0], hint[1], 11.0), (0.0, 0.0, 0.0))[0])

    @pytest.mark.parametrize(("pitch", "steep"), [(-88.0, True), (-83.0, True), (-82.0, False), (-48.0, False)])
    def test_the_solve_says_whether_the_camera_looks_almost_straight_down(self, pitch: float, steep: bool) -> None:
        camera = np.array([0.0, 1.0, 12.0, pitch, 180.0, 0.0, 70.0])
        marks = [Mark(i, "known", 0.0, (p[0], p[1]), pin=_pin(p, camera)) for i, p in enumerate(_UNDER_IT, 1)]
        solved = solve_marks(marks, W, H, hint=(0.0, 1.0, 12.0))
        assert solved.camera is not None and solved.camera.pitch == pytest.approx(pitch, abs=1e-3)
        assert solved.steep is steep

    def test_the_steep_line_is_82_and_a_half_degrees(self) -> None:
        assert (cp.STEEP_PITCH, cp.HEIGHT_SIGMA) == (-82.5, 0.01)

    @pytest.mark.parametrize("height", [None, 11.5])
    def test_the_jacobian_is_the_residuals_derivative(self, height: float | None) -> None:
        marks = _advanced()
        known = {m.id: np.array([*m.position, m.z]) for m in marks if m.position is not None}
        live = [m for m in marks if m.kind == "triangulated"]
        places = [np.array(P3), np.array(P4)]
        problem = cp._Problem(known, live, marks, (W, H), height)
        x = np.concatenate([TRUE + np.array([0.3, -0.2, 0.4, 1.0, -1.5, 0.5, 2.0]), *[p[:2] + 0.1 for p in places]])
        r = problem.residuals(x)
        assert r is not None
        jac = problem.jacobian(x, r.size)
        assert jac is not None
        numeric = np.zeros_like(jac)
        for k in range(x.size):
            step = 1e-5 * (1.0 + abs(x[k]))
            up, down = x.copy(), x.copy()
            up[k] += step
            down[k] -= step
            numeric[:, k] = (problem.residuals(up) - problem.residuals(down)) / (2.0 * step)
        np.testing.assert_allclose(jac, numeric, rtol=1e-4, atol=1e-3)
        rows = 2 * len(marks) + 2 * len(live) + (height is not None)
        assert jac.shape == (rows, 7 + 2 * len(live))
        if height is not None:
            expected = np.zeros(jac.shape[1])
            expected[2] = 1.0 / cp.HEIGHT_SIGMA
            np.testing.assert_allclose(jac[-1], expected, atol=1e-6)


# 13 m up over an 8 x 5 m rectangle; each corner pinned under half a pixel off.
_BELOW = ((4.0, -2.0, 0.0), (-4.0, -2.0, 0.0), (-4.0, 3.0, 0.0), (4.0, 3.0, 0.0))
_UNDER_HALF_PX = ((0.4, -0.3), (-0.5, 0.2), (0.3, 0.4), (-0.2, -0.4))


def _corners_below(pitch: float, yaw: float, off=_UNDER_HALF_PX) -> tuple[np.ndarray, list[Mark]]:
    camera = np.array([0.3, 0.7, 13.0, pitch, yaw, 0.0, 75.0])
    pins = [_pin(p, camera) for p in _BELOW]
    marks = [
        Mark(i, "known", p[2], (p[0], p[1]), pin=(pin[0] + dx, pin[1] + dy))
        for i, (p, pin, (dx, dy)) in enumerate(zip(_BELOW, pins, off, strict=True), start=1)
    ]
    assert all(0.0 < m.pin[0] < W and 0.0 < m.pin[1] < H for m in marks if m.pin)
    return camera, marks


class TestStraightDown:
    """Simple without a camera position, its four level corners' homography no seed: tried from above."""

    @pytest.mark.parametrize("pitch", [-90.0, -89.9])
    @pytest.mark.parametrize("height", [None, 13.0], ids=["no height", "height"])
    def test_simple_without_a_camera_position_gives_a_camera(self, pitch: float, height: float | None) -> None:
        down, marks = _corners_below(pitch, 30.0)
        solved = solve_marks(marks, W, H, height=height)
        assert solved.camera is not None and solved.error == ""
        assert solved.steep is True
        for mark, fit in zip(marks, solved.marks, strict=True):
            assert fit.seen == pytest.approx(mark.pin, abs=2.0)
        if height is not None:
            assert solved.camera.pos_z == pytest.approx(13.0, abs=0.01)
            np.testing.assert_allclose(_pose(solved.camera)[:2], down[:2], atol=0.15)
            assert solved.camera.fov == pytest.approx(75.0, abs=0.5)

    def test_corners_whose_steep_homography_fits_nothing_are_tried_from_above_as_well(self) -> None:
        _, marks = _corners_below(-88.0, 90.0, off=_HALF_PX)
        solved = solve_marks(marks, W, H)
        assert solved.camera is not None and solved.steep is True


# Four level corners pinned a few pixels off, three more known marks pinned exactly, the height entered.
_SPREAD = ((-1.0, 1.0), (1.0, 1.0), (1.0, -1.0), (-1.0, -1.0))
_MORE_KNOWN = ((0.0, 1.0, 1.5), (2.0, 0.0, 0.0), (-3.0, 2.0, 0.8))


def _steep_with_off_corners(pitch: float, off: tuple[tuple[float, float], ...]) -> tuple[np.ndarray, list[Mark]]:
    camera = np.array([0.0, 1.0, 12.0, pitch, 180.0, 0.0, 70.0])
    pins = [_pin(p, camera) for p in _UNDER_IT]
    corners = [
        Mark(i, "known", p[2], (p[0], p[1]), pin=(pin[0] + dx, pin[1] + dy))
        for i, (p, pin, (dx, dy)) in enumerate(zip(_UNDER_IT, pins, off, strict=True), start=1)
    ]
    more = [Mark(i, "known", p[2], (p[0], p[1]), pin=_pin(p, camera)) for i, p in enumerate(_MORE_KNOWN, start=5)]
    return camera, corners + more


class TestFloorReach:
    """A mark typed at the wrong height bends the camera; four level corners alone put the floor elsewhere."""

    @pytest.mark.parametrize(
        ("place", "typed_z", "off"),
        [
            ((0.0, 6.0, 2.0), 2.0, False),
            ((0.0, 6.0, 2.0), 3.0, False),  # the near floor at 0.56 of the corners'
            ((0.0, 6.0, 2.0), 3.3, True),  # at 0.42
            ((0.0, 7.0, 4.0), 0.0, True),  # the far floor at 2.5 times
        ],
    )
    def test_a_fit_that_moves_the_floor_twice_as_far_or_near_as_the_level_corners_is_named(
        self, place, typed_z, off
    ) -> None:
        marks = [*_simple(), _known(5, place, typed=(place[0], place[1], typed_z))]
        solved = solve_marks(marks, W, H, hint=(0.0, 13.0, 12.0))
        assert solved.camera is not None
        assert solved.floor_off is off
        reasons = plausibility(solved.camera, solved.marks, {}, None, per_axis=True, floor_off=solved.floor_off)
        assert ("reach" in _kinds(reasons)) is off

    @pytest.mark.parametrize(
        ("pitch", "off"),
        [
            (-89.0, _HALF_PX),  # both steep
            (-86.0, tuple((4.0 * x, 4.0 * y) for x, y in _SPREAD)),  # the corners' camera at -77°
            (-80.0, tuple((-4.0 * x, -4.0 * y) for x, y in _SPREAD)),  # the corners' camera at -89°
        ],
        ids=["both steep", "fit steep", "corners steep"],
    )
    def test_a_correct_fit_is_not_compared_with_the_corners_when_either_is_steep(self, pitch, off) -> None:
        """Steeper than STEEP_PITCH a pixel moves the corners' camera metres; the entered height holds the fit."""
        camera, marks = _steep_with_off_corners(pitch, off)
        solved = solve_marks(marks, W, H, hint=(0.0, 2.0, 10.0), height=12.0)
        assert solved.camera is not None
        np.testing.assert_allclose(_pose(solved.camera)[:3], camera[:3], atol=0.05)
        assert solved.floor_off is False
        reasons = plausibility(solved.camera, solved.marks, {}, None, per_axis=True, floor_off=solved.floor_off)
        assert "reach" not in _kinds(reasons)

    @pytest.mark.parametrize(("z", "compared"), [(0.009, True), (0.011, False)])
    def test_only_corners_within_1_cm_of_one_height_are_compared(self, z, compared) -> None:
        mistyped = _known(5, (0.0, 7.0, 4.0), typed=(0.0, 7.0, 0.0))
        marks = [*_simple()[:3], _known(4, (USL[0], USL[1], z)), mistyped]
        assert solve_marks(marks, W, H, hint=(0.0, 13.0, 12.0)).floor_off is compared

    @pytest.mark.parametrize(("more", "compared"), [((), False), ((USR,), True)])
    def test_level_marks_with_three_in_a_line_are_not_compared(self, more, compared) -> None:
        """REF, DSL and DSR lie on one line: with USL they make no four to compare, with USR as well they do."""
        level = [_known(i, place) for i, place in enumerate((REF, DSL, DSR, USL, *more), start=1)]
        marks = [*level, _known(9, (0.0, 6.0, 2.0), typed=(0.0, 6.0, 4.0))]
        assert solve_marks(marks, W, H, hint=(0.0, 13.0, 12.0)).floor_off is compared

    def test_without_four_level_known_marks_nothing_is_compared(self) -> None:
        marks = [*_simple()[:3], _known(4, (USL[0], USL[1], 0.5)), _known(5, (0.0, 6.0, 2.0), typed=(0.0, 6.0, 4.0))]
        solved = solve_marks(marks, W, H, hint=(0.0, 13.0, 12.0))
        assert solved.camera is not None and solved.camera.pos_y < 10.0  # bent as far as with level corners
        assert solved.floor_off is False

    def test_four_level_corners_alone_are_not_compared_with_themselves(self) -> None:
        solved = solve_marks(_simple(off_px=(40.0, 0.0)), W, H)
        assert solved.camera is not None
        assert solved.floor_off is False


class TestNoCamera:
    def test_without_a_camera_position_or_two_pinned_known_marks_nothing_seeds_the_fit(self) -> None:
        marks = [
            _known(1, REF),
            _known(2, DSL, pinned=False),
            _triangulated(3, P3, (1, REF), (2, DSL)),
            _triangulated(4, P4, (3, P3), (1, REF)),
            _triangulated(5, (1.0, 2.0, 0.0), (4, P4), (1, REF)),
        ]
        solved = solve_marks(marks, W, H)
        assert (solved.camera, solved.error) == (None, cp.NO_FIT)
        assert [f.position for f in solved.marks] == [None] * 5
        _assert_true_camera(solve_marks(marks, W, H, hint=(0.0, 13.0, 12.0)).camera)

    def test_two_pinned_known_marks_seed_from_above_without_a_camera_position(self) -> None:
        _assert_true_camera(solve_marks(_advanced(), W, H).camera)

    @pytest.mark.parametrize("height", [0.0, -3.0, -0.01])
    def test_a_camera_height_not_above_the_lowest_pinned_mark_names_the_height(self, height: float) -> None:
        solved = solve_marks(_simple(), W, H, hint=(0.0, 13.0, 12.0), height=height)
        assert (solved.camera, solved.error) == (None, cp.NO_FIT_AT_HEIGHT)
        assert [f.position for f in solved.marks] == [None] * 4

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


def test_the_allowance_is_a_tenth_of_the_distance_to_the_marks_centre_not_the_origin() -> None:
    """At 0/6/0 the mark is 13.9 m from the camera, which is 17.7 m from the origin."""
    fits = (MarkFit(1, (0.0, 6.0, 0.0)),)
    reasons = plausibility(_camera(), fits, {}, (1.5, 13.0, 12.0), per_axis=False)
    assert reasons[0]["allowed"] == pytest.approx(0.1 * math.hypot(7.0, 12.0))
    assert _kinds(reasons) == ["offset"]


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


def test_a_camera_between_the_lowest_and_the_highest_mark_is_not_below() -> None:
    fits = (MarkFit(1, (0.0, 0.0, 0.0)), MarkFit(2, (0.0, 3.0, 2.0)))
    assert "below" not in _kinds(plausibility(_camera(pos_z=1.0), fits, {}, None, per_axis=True))


@pytest.mark.parametrize(("floor_off", "fires"), [(True, True), (False, False)])
def test_a_floor_off_the_level_marks_is_named(floor_off, fires) -> None:
    reasons = plausibility(_camera(), _AT_ORIGIN, {}, None, per_axis=True, floor_off=floor_off)
    assert reasons == ([{"kind": "reach"}] if fires else [])


@pytest.mark.parametrize(("pitch", "fires"), [(0.5, True), (0.0, False)])
def test_a_camera_looking_up_is_named(pitch, fires) -> None:
    reasons = plausibility(_camera(pitch=pitch), _AT_ORIGIN, {}, None, per_axis=True)
    assert ("up" in _kinds(reasons)) is fires


@pytest.mark.parametrize(("px", "fires"), [(5.0, False), (5.01, True)])
def test_a_pin_more_than_5_px_off_is_named(px, fires) -> None:
    reasons = plausibility(_camera(), _AT_ORIGIN, {1: px}, None, per_axis=True)
    assert reasons == ([{"kind": "pixel", "id": 1, "px": px}] if fires else [])


@pytest.mark.parametrize(("error", "fires"), [(0.119, False), (cp.FLAG_DISTANCE_M, False), (0.121, True)])
def test_distances_more_than_12_cm_off_are_named(error, fires) -> None:
    fits = (MarkFit(3, (1.0, 2.0, 0.0), distance_error=error),)
    reasons = plausibility(_camera(), fits, {}, None, per_axis=True)
    assert reasons == ([{"kind": "distance", "id": 3, "m": error}] if fires else [])


# pin_errors: how far each saved pin is from where the saved camera sees its mark

_LENS = (-0.2, 0.03, 0.01, -0.02)


def _saved_camera(marks: list[dict], lens=_LENS) -> CameraConfig:
    pose = dict(zip(("pos_x", "pos_y", "pos_z", "pitch", "yaw", "roll", "fov"), TRUE.tolist(), strict=True))
    k1, k2, cx, cy = lens
    return CameraConfig(
        **pose, lens_k1=k1, lens_k2=k2, lens_cx=cx, lens_cy=cy, calibration_mode="advanced", calibration_marks=marks
    )  # type: ignore[arg-type]


def _seen(place, lens=_LENS) -> tuple[float, float]:
    """Where TRUE sees ``place`` on the Operator Screen, its lens bowing the pinhole picture."""
    x, y = apply_overlay_distortion(np.array([_pin(place)]), W, H, *lens)[0]
    return float(x), float(y)


def _pinned_at(xy, dx: float = 0.0, dy: float = 0.0) -> list[float]:
    return [(xy[0] + dx) / W, (xy[1] + dy) / H]


def test_a_pin_where_the_camera_sees_its_mark_through_the_lens_has_no_error() -> None:
    marks = [
        {"id": i, "kind": "known", "x": p[0], "y": p[1], "z": p[2], "pin": _pinned_at(_seen(p))}
        for i, p in enumerate((DSL, DSR), 1)
    ]
    errors = pin_errors(_saved_camera(marks), W, H)
    assert errors.keys() == {1, 2} and max(errors.values()) == pytest.approx(0.0, abs=1e-6)


def test_a_pin_off_its_mark_counts_the_distance_in_pixels() -> None:
    marks = [
        {"id": 1, "kind": "known", "x": DSL[0], "y": DSL[1], "z": DSL[2], "pin": _pinned_at(_seen(DSL), 3.0, -4.0)}
    ]
    assert pin_errors(_saved_camera(marks), W, H)[1] == pytest.approx(5.0, abs=1e-6)


def test_the_error_counts_the_lens() -> None:
    # Pinned where a pinhole camera sees the mark: off by as far as this lens bows that point.
    marks = [{"id": 1, "kind": "known", "x": USR[0], "y": USR[1], "z": USR[2], "pin": _pinned_at(_pin(USR))}]
    bow = math.dist(_pin(USR), _seen(USR))
    assert bow > 1.0
    assert pin_errors(_saved_camera(marks), W, H)[1] == pytest.approx(bow, abs=1e-6)


def test_a_distance_point_counts_at_the_position_its_solve_placed_it() -> None:
    at = (0.0, 3.0, 0.0)
    base = [{"id": i, "kind": "known", "x": p[0], "y": p[1], "z": p[2], "pin": []} for i, p in enumerate((DSL, DSR), 1)]
    measured = {
        "id": 3,
        "kind": "triangulated",
        "from_a": 1,
        "from_b": 2,
        "distance_a": 6.7,
        "distance_b": 6.7,
        "z": 0.0,
    }
    placed = {**measured, "pin": _pinned_at(_seen(at), 0.0, 2.0), "position": list(at)}
    unplaced = {**measured, "id": 4, "pin": _pinned_at(_seen(at))}
    assert pin_errors(_saved_camera([*base, placed, unplaced]), W, H) == {3: pytest.approx(2.0, abs=1e-6)}


def test_unpinned_marks_and_marks_behind_the_camera_have_no_error() -> None:
    behind = (0.0, 30.0, 0.0)  # upstage of the camera, which looks downstage
    marks = [
        {"id": 1, "kind": "known", "x": DSL[0], "y": DSL[1], "z": DSL[2], "pin": []},
        {"id": 2, "kind": "known", "x": behind[0], "y": behind[1], "z": behind[2], "pin": [0.5, 0.5]},
    ]
    assert pin_errors(_saved_camera(marks), W, H) == {}


def _at(*heights: float) -> list[Mark]:
    return [Mark(n, "known", z, (float(n), 0.0)) for n, z in enumerate(heights, start=1)]


@pytest.mark.parametrize(
    ("heights", "floor"),
    [
        ((1.2, 1.2, 1.2, 1.8), (1.2, (1, 2, 3))),
        ((0.0, 0.6, 0.0, 0.6, 0.0), (0.0, (1, 3, 5))),
        ((1.18, 1.22, 1.23, 3.0), (1.21, (1, 2, 3))),
        ((1.2, 1.25), (1.225, (1, 2))),
        ((-1.5, -1.5, 0.0), (-1.5, (1, 2))),
        ((1.0, 1.03, 1.03, 1.06), (1.02, (1, 2, 3))),
        ((0.0, 0.04, 0.08), (0.02, (1, 2))),
        ((0.0, 0.04, 0.05, 0.06), (0.05, (2, 3, 4))),
    ],
    ids=[
        "raised-deck",
        "floor-and-risers",
        "within-5-cm",
        "exactly-5-cm",
        "a-pit",
        "one-loose-deck",
        "a-chain",
        "tightest",
    ],
)
def test_the_floor_is_the_mean_of_the_largest_group_within_5_cm(heights, floor) -> None:
    assert cp.floor_height(_at(*heights)) == floor


@pytest.mark.parametrize(
    "heights",
    [(), (1.2,), (0.0, 0.051), (0.0, 0.0, 0.6, 0.6), (0.0, 0.03, 0.6, 0.63), (0.0, 0.5, 1.0, 1.5)],
    ids=["none", "one", "just-over-5-cm", "two-groups-tie", "two-loose-groups-tie", "all-apart"],
)
def test_no_floor_without_one_largest_group_of_two(heights) -> None:
    assert cp.floor_height(_at(*heights)) is None


def test_a_triangulated_point_counts_by_its_typed_height() -> None:
    marks = [
        Mark(1, "known", 1.2, (6.0, 0.0)),
        Mark(2, "triangulated", 1.2, from_a=1, from_b=3),
        Mark(3, "known", 0.0, (0.0, 0.0)),
    ]
    assert cp.floor_height(marks) == (1.2, (1, 2))
