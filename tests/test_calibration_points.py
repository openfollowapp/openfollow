# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Calibration points: the trilateration seed, the joint adjustment and its plausibility rules,
on the rig they exist for (an audience camera on the back truss that sees a 10 x 1.5 m stage strip)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from openfollow.scene import calibration_points as cp
from openfollow.scene.calibration_points import CalibrationPoint, solve_with_points, trilaterate
from openfollow.scene.solver import project_points, solve_camera_dlt, unproject_to_plane

pytestmark = pytest.mark.unit

W, H = 1920.0, 1080.0
# Back truss, 10 m upstage of the strip's downstage edge, 8 m up, looking downstage into the audience.
TRUE = np.array([0.4, 10.0, 8.0, -24.0, 180.0, 0.0, 55.0])
CORNERS = np.array([[-5.0, 0.0, 0.0], [5.0, 0.0, 0.0], [5.0, 1.5, 0.0], [-5.0, 1.5, 0.0]])  # DSL DSR USR USL
DSL, DSR, USR, USL = (tuple(c) for c in CORNERS)
AUDIENCE = np.array([[x, y, 0.0] for x in (-6.0, 0.0, 6.0) for y in (-5.0, -10.0, -20.0, -30.0, -40.0)])


def _pins(points: np.ndarray) -> np.ndarray:
    return project_points(TRUE, points, W, H)


def _point(where, a=DSL, b=DSR, *, height=0.0, err_a=0.0, err_b=0.0, pin_err=(0.0, 0.0)) -> CalibrationPoint:
    place = np.array([where[0], where[1], height])
    pin = _pins(place[None])[0] + np.array(pin_err)
    return CalibrationPoint(
        corner_a=a,
        corner_b=b,
        distance_a=float(np.linalg.norm(place - np.array(a))) + err_a,
        distance_b=float(np.linalg.norm(place - np.array(b))) + err_b,
        height=height,
        pin=(float(pin[0]), float(pin[1])),
    )


def _params(cam) -> np.ndarray:
    return np.array([cam.pos_x, cam.pos_y, cam.pos_z, cam.pitch, cam.yaw, cam.roll, cam.fov])


def _far_error(params: np.ndarray) -> np.ndarray:
    """Metres between each audience spot and where ``params`` puts its true pixel on the floor."""
    back = unproject_to_plane(params, _pins(AUDIENCE), W, H, 0.0)
    return np.linalg.norm(back[:, :2] - AUDIENCE[:, :2], axis=1)


class TestTrilaterate:
    def test_the_two_places_are_mirror_images_across_the_corners(self) -> None:
        first, second = trilaterate(DSL, DSR, math.hypot(1.0, 3.0), math.hypot(9.0, 3.0), 0.0)
        assert sorted([tuple(np.round(first, 9)), tuple(np.round(second, 9))]) == [(-4.0, -3.0, 0.0), (-4.0, 3.0, 0.0)]

    def test_a_point_above_stage_level_takes_its_height(self) -> None:
        places = trilaterate(DSL, DSR, math.sqrt(1 + 9 + 4), math.sqrt(81 + 9 + 4), 2.0)
        assert {tuple(np.round(p, 9)) for p in places} == {(-4.0, -3.0, 2.0), (-4.0, 3.0, 2.0)}

    @pytest.mark.parametrize(
        ("distance_a", "distance_b", "height", "reason"),
        [
            (1.0, 30.0, 0.0, cp.REASON_DIFFER),
            (3.0, 3.0, 0.0, cp.REASON_SHORT),
            (1.0, 12.0, 1.5, cp.REASON_HEIGHT),
        ],
    )
    def test_distances_that_place_it_nowhere_say_why(self, distance_a, distance_b, height, reason) -> None:
        assert trilaterate(DSL, DSR, distance_a, distance_b, height) == reason

    @pytest.mark.parametrize(
        ("distance_a", "distance_b", "x"),
        [
            (15.1, 5.0, 10.05),  # b's circle inside a's, beyond b
            (5.0, 15.1, -10.05),  # a's circle inside b's, beyond a
            (4.95, 4.95, 0.0),  # apart, between the corners
        ],
    )
    def test_circles_that_just_miss_meet_midway_across_the_gap(self, distance_a, distance_b, x) -> None:
        """A point on the line through both corners: noise makes the circles miss by a few cm."""
        first, second = trilaterate(DSL, DSR, distance_a, distance_b, 0.0)
        assert first == pytest.approx((x, 0.0, 0.0))
        assert second == pytest.approx((x, 0.0, 0.0))


class TestTheJointAdjustment:
    def test_exact_measurements_give_back_the_camera_and_the_points(self) -> None:
        points = [_point((-4.0, -20.0)), _point((5.0, -35.0))]
        solved = solve_with_points(CORNERS, _pins(CORNERS), points, W, H)
        assert solved is not None
        assert np.allclose(_params(solved.camera), TRUE, atol=1e-6)
        assert [p.status for p in solved.points] == ["ok", "ok"]
        assert np.allclose([p.position for p in solved.points], [(-4.0, -20.0, 0.0), (5.0, -35.0, 0.0)], atol=1e-6)

    def test_a_point_above_stage_level_is_fitted_at_its_height(self) -> None:
        points = [_point((-4.0, -20.0), height=1.2), _point((5.0, -35.0), height=-0.8)]
        solved = solve_with_points(CORNERS, _pins(CORNERS), points, W, H)
        assert solved is not None
        assert np.allclose(_params(solved.camera), TRUE, atol=1e-6)
        assert np.allclose([p.position for p in solved.points], [(-4.0, -20.0, 1.2), (5.0, -35.0, -0.8)], atol=1e-6)

    @pytest.mark.parametrize("where", [(3.0, -25.0), (-13.0, -25.0)])
    def test_the_pin_decides_between_the_mirror_images(self, where) -> None:
        """Measured from DSL and USL the two places are mirror images across x = -5; only the pin tells."""
        solved = solve_with_points(CORNERS, _pins(CORNERS), [_point(where, DSL, USL)], W, H)
        assert solved is not None
        assert solved.points[0].position == pytest.approx((*where, 0.0), abs=1e-6)

    def test_two_points_cut_the_far_field_error_from_noisy_pins(self) -> None:
        rng = np.random.default_rng(7)
        corners_only, with_points = [], []
        for _ in range(30):
            pins = _pins(CORNERS) + rng.normal(0.0, 1.0, (4, 2))
            seed = solve_camera_dlt([tuple(c) for c in CORNERS], [tuple(p) for p in pins], W, H)
            points = [
                _point(
                    where,
                    err_a=rng.normal(0.0, 0.04),
                    err_b=rng.normal(0.0, 0.04),
                    pin_err=rng.normal(0.0, 1.0, 2),
                )
                for where in ((-4.0, -20.0), (5.0, -35.0))
            ]
            solved = solve_with_points(CORNERS, pins, points, W, H)
            assert seed is not None and solved is not None
            corners_only.append(np.median(_far_error(_params(seed))))
            with_points.append(np.median(_far_error(_params(solved.camera))))
        assert np.median(with_points) < np.median(corners_only) / 3.0
        assert np.median(with_points) < 0.15

    def test_a_single_point_cannot_be_cross_checked(self) -> None:
        solved = solve_with_points(CORNERS, _pins(CORNERS), [_point((-4.0, -20.0))], W, H)
        assert solved is not None
        assert solved.points[0].status == "unverified"

    def test_without_points_it_is_the_corner_solve(self) -> None:
        solved = solve_with_points(CORNERS, _pins(CORNERS), [], W, H)
        assert solved is not None
        assert solved.points == ()
        assert np.allclose(_params(solved.camera), TRUE, atol=1e-6)


class TestPlausibility:
    def test_a_wrong_distance_is_flagged_when_another_point_cross_checks_it(self) -> None:
        points = [_point((-4.0, -20.0), err_a=2.0, err_b=2.0), _point((5.0, -35.0)), _point((0.0, -28.0))]
        solved = solve_with_points(CORNERS, _pins(CORNERS), points, W, H)
        assert solved is not None
        assert [p.status for p in solved.points] == ["flagged", "ok", "ok"]
        assert solved.points[0].reason == cp.REASON_DISAGREES
        assert np.allclose(_params(solved.camera), TRUE, atol=1e-6)

    def test_of_three_the_one_whose_removal_fits_best_is_left_out(self) -> None:
        """In a row 10 m out with the left one 1 m off, the joint fit's worst residual lands on the good
        centre point; leaving out the left one is what makes the other two fit."""
        points = [_point((-6.0, -10.0), err_a=1.0, err_b=1.0), _point((0.0, -10.0)), _point((6.0, -10.0))]
        solved = solve_with_points(CORNERS, _pins(CORNERS), points, W, H)
        assert solved is not None
        assert [p.status for p in solved.points] == ["flagged", "ok", "ok"]
        assert np.allclose(_params(solved.camera), TRUE, atol=1e-6)

    def test_two_points_that_disagree_are_both_left_out(self) -> None:
        """From a narrow strip neither can show the other wrong, and guessing is worse than the corners."""
        points = [_point((-4.0, -20.0), err_a=2.0, err_b=2.0), _point((5.0, -35.0))]
        solved = solve_with_points(CORNERS, _pins(CORNERS), points, W, H)
        assert solved is not None
        assert [(p.status, p.reason) for p in solved.points] == [("flagged", cp.REASON_PAIR)] * 2
        assert np.allclose(_params(solved.camera), TRUE, atol=1e-6)

    def test_a_lone_point_that_disagrees_with_the_corners_is_left_out(self) -> None:
        """Pinned on the wrong mark: no camera puts the measured place there and the corners where they are."""
        solved = solve_with_points(CORNERS, _pins(CORNERS), [_point((-4.0, -20.0), pin_err=(0.0, 160.0))], W, H)
        assert solved is not None
        assert (solved.points[0].status, solved.points[0].reason) == ("flagged", cp.REASON_DISAGREES_CORNERS)
        assert np.allclose(_params(solved.camera), TRUE, atol=1e-6)

    def test_a_lone_point_that_bends_the_camera_off_the_corners_is_left_out(self) -> None:
        """Pinned 200 px low 30 m out, the fit meets the pin and the distances by moving the camera off
        the corner pins: the point's own residuals stay small, the corners' do not."""
        solved = solve_with_points(CORNERS, _pins(CORNERS), [_point((0.0, -30.0), pin_err=(0.0, 200.0))], W, H)
        assert solved is not None
        assert (solved.points[0].status, solved.points[0].reason) == ("flagged", cp.REASON_DISAGREES_CORNERS)
        assert solved.points[0].residual < cp.FLAG_SIGMA
        assert np.allclose(_params(solved.camera), TRUE, atol=1e-6)

    def test_a_perfect_lone_point_is_used_beside_corners_pinned_to_their_own_accuracy(self) -> None:
        """Corner marks on a stage floor are pinned to about 3 px; weighed like a 1 px pin, corners that far
        off read as the lone point bending the camera, and the measurement was thrown away. The floor guard
        lets it through too."""
        pins = _pins(CORNERS) + np.array([[0.6, -1.6], [-1.2, -7.3], [5.4, 3.4], [-1.0, 2.3]])  # 3 px noise
        solved = solve_with_points(CORNERS, pins, [_point((0.0, -30.0))], W, H)
        corners_only = solve_camera_dlt([tuple(c) for c in CORNERS], [tuple(p) for p in pins], W, H)
        assert solved is not None and corners_only is not None
        assert solved.points[0].status == "unverified"
        assert solved.corner_residual < cp.FLAG_SIGMA
        assert np.median(_far_error(_params(solved.camera))) < np.median(_far_error(_params(corners_only))) / 3.0

    def test_two_points_are_used_beside_corner_pins_well_off(self) -> None:
        """They check each other; the corners' residual would only throw away what rescues the far field."""
        pins = _pins(CORNERS) + np.array([[16.3, -20.4], [3.3, -4.5], [-3.6, -1.7], [-16.2, -1.9]])
        points = [_point((-4.0, -20.0)), _point((5.0, -35.0))]
        solved = solve_with_points(CORNERS, pins, points, W, H)
        corners_only = solve_camera_dlt([tuple(c) for c in CORNERS], [tuple(p) for p in pins], W, H)
        assert solved is not None and corners_only is not None
        assert [p.status for p in solved.points] == ["ok", "ok"]
        assert solved.corner_residual > cp.FLAG_SIGMA
        assert np.median(_far_error(_params(solved.camera))) < np.median(_far_error(_params(corners_only))) / 3.0

    def test_a_lone_point_that_moves_the_far_floor_is_left_out(self) -> None:
        """Pinned 100 px low 30 m out, it fits within its sigmas, but only by putting the floor across the
        picture more than twice as far as the corners do: the far field would land metres off."""
        point = _point((0.0, -30.0), pin_err=(0.0, 100.0))
        solved = solve_with_points(CORNERS, _pins(CORNERS), [point], W, H)
        assert solved is not None
        assert (solved.points[0].status, solved.points[0].reason) == ("flagged", cp.REASON_FLOOR)
        assert solved.points[0].residual < cp.FLAG_SIGMA and solved.corner_residual < cp.FLAG_SIGMA
        assert np.allclose(_params(solved.camera), TRUE, atol=1e-6)

    def test_a_correct_lone_point_passes_the_floor_check_near_the_horizon(self) -> None:
        """Tilted up, the picture's top reaches floor far beyond the points, which the corners alone put
        anywhere: only floor within reach of the points is asked about, or good points would be left out."""
        true = TRUE.copy()
        true[3] = -17.0
        pins = project_points(true, CORNERS, W, H) + np.array([[0.4, -0.4], [1.9, 0.3], [-1.6, 1.1], [3.9, 2.8]])
        place = np.array([0.0, -30.0, 0.0])
        pin = project_points(true, place[None], W, H)[0]
        point = CalibrationPoint(
            DSL, DSR, *(float(np.linalg.norm(place - np.array(c))) for c in (DSL, DSR)), 0.0, (pin[0], pin[1])
        )
        solved = solve_with_points(CORNERS, pins, [point], W, H)
        assert solved is not None and solved.points[0].status == "unverified"

    @pytest.mark.parametrize("n", [2, 3])
    def test_points_that_agree_and_move_the_far_floor_are_left_out(self, n: int) -> None:
        """Pinned 120 px low the same way, they check each other fine: only the floor shows the bend."""
        spots = [(0.0, -40.0), (6.0, -40.0), (-6.0, -40.0)][:n]
        solved = solve_with_points(CORNERS, _pins(CORNERS), [_point(w, pin_err=(0.0, 120.0)) for w in spots], W, H)
        assert solved is not None
        assert [(p.status, p.reason) for p in solved.points] == [("flagged", cp.REASON_FLOOR)] * n
        assert np.allclose(_params(solved.camera), TRUE, atol=1e-6)

    @pytest.mark.parametrize("where", [(3.0, -25.0), (-13.0, -25.0)])
    def test_a_flagged_point_shows_where_its_distances_put_it_on_the_pinned_side(self, where) -> None:
        """Measured from DSL and USL its distances allow two mirror places; the one shown is the pinned side."""
        wrong = _point(where, DSL, USL, err_a=2.5, err_b=2.5)
        solved = solve_with_points(CORNERS, _pins(CORNERS), [wrong, _point((5.0, -35.0)), _point((0.0, -28.0))], W, H)
        assert solved is not None
        assert solved.points[0].status == "flagged"
        assert solved.points[0].position is not None
        assert np.sign(solved.points[0].position[0] + 5.0) == np.sign(where[0] + 5.0)

    @pytest.mark.parametrize(
        "point",
        [
            CalibrationPoint(DSL, DSR, 1.0, 30.0, 0.0, (900.0, 700.0)),  # distances that cannot meet
            _point((0.0, -30.0), pin_err=(0.0, 200.0)),  # disagrees with the corners
            _point((0.0, -30.0), pin_err=(0.0, 100.0)),  # moves the far floor
        ],
    )
    def test_a_point_left_out_does_not_move_the_pose(self, point) -> None:
        """'Left out' means not used: the camera is the one the corners give without it, to the digit."""
        pins = _pins(CORNERS) + np.random.default_rng(3).normal(0.0, 1.0, (4, 2))
        solved = solve_with_points(CORNERS, pins, [point], W, H)
        corners_only = solve_camera_dlt([tuple(c) for c in CORNERS], [tuple(p) for p in pins], W, H)
        assert solved is not None and solved.points[0].status == "flagged"
        assert solved.camera == corners_only
        reprojected = project_points(_params(corners_only), CORNERS, W, H)
        assert solved.corner_residual == pytest.approx(float(np.abs(reprojected - pins).max()) / cp.CORNER_SIGMA)

    def test_distances_that_place_a_point_nowhere_leave_it_out(self) -> None:
        points = [
            CalibrationPoint(DSL, DSR, 1.0, 30.0, 0.0, (900.0, 700.0)),
            _point((5.0, -35.0)),
            _point((0.0, -28.0)),
        ]
        solved = solve_with_points(CORNERS, _pins(CORNERS), points, W, H)
        assert solved is not None
        assert (solved.points[0].status, solved.points[0].reason, solved.points[0].position) == (
            "flagged",
            cp.REASON_DIFFER,
            None,
        )
        assert [p.status for p in solved.points[1:]] == ["ok", "ok"]
        assert np.allclose(_params(solved.camera), TRUE, atol=1e-6)

    def test_a_point_neither_mirror_puts_in_front_of_the_camera(self) -> None:
        """Measured along the strip's side to a place behind the camera, both mirrors are behind it."""
        behind = _point((0.0, 14.0), DSL, USL)
        flagged = CalibrationPoint(
            behind.corner_a, behind.corner_b, behind.distance_a, behind.distance_b, 0.0, (960.0, 540.0)
        )
        solved = solve_with_points(CORNERS, _pins(CORNERS), [flagged, _point((5.0, -35.0))], W, H)
        assert solved is not None
        assert (solved.points[0].status, solved.points[0].reason) == ("flagged", cp.REASON_BEHIND)
        assert solved.points[1].status == "unverified"

    def test_corners_that_give_no_camera_give_no_solve(self) -> None:
        line = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [2.0, 0.0, 0.0], [3.0, 0.0, 0.0]])
        assert solve_with_points(line, _pins(CORNERS), [_point((5.0, -35.0))], W, H) is None


def _angle_gap(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


@pytest.mark.parametrize("roll", [0.0, 180.0])  # hung upright, and upside down
def test_the_solved_angles_are_in_the_corner_solves_range(roll: float) -> None:
    """Facing straight downstage the fit's yaw crosses +/-180 freely, an inverted camera's roll too."""
    true = TRUE.copy()
    true[5] = roll
    rng = np.random.default_rng(7)
    angles = []
    for _ in range(20):
        pins = project_points(true, CORNERS, W, H) + rng.normal(0.0, 1.0, (4, 2))
        points = []
        for where in ((-4.0, -20.0), (5.0, -35.0)):
            place = np.array([where[0], where[1], 0.0])
            pin = project_points(true, place[None], W, H)[0] + rng.normal(0.0, 1.0, 2)
            to_a, to_b = (float(np.linalg.norm(place - np.array(c))) + rng.normal(0.0, 0.04) for c in (DSL, DSR))
            points.append(CalibrationPoint(DSL, DSR, to_a, to_b, 0.0, (float(pin[0]), float(pin[1]))))
        solved = solve_with_points(CORNERS, pins, points, W, H)
        assert solved is not None
        angles.append((solved.camera.yaw, solved.camera.roll))
    assert all(-180.0 < a <= 180.0 for pair in angles for a in pair)
    assert max(max(_angle_gap(yaw, 180.0), _angle_gap(r, roll)) for yaw, r in angles) < 1.0


class TestTheFloorCheck:
    """Cameras the solve cannot produce from good data, to hold each bound of the check."""

    def _holds(self, camera: np.ndarray) -> bool:
        return cp._floor_holds(TRUE, camera, 0.0, np.array([[0.0, -30.0, 0.0]]), W, H)

    def test_the_corners_own_camera_holds(self) -> None:
        assert self._holds(TRUE.copy())

    @pytest.mark.parametrize(
        ("pitch", "why"),
        [(-50.0, "the floor comes more than twice as near"), (-12.0, "the far floor goes more than twice as far")],
    )
    def test_a_camera_that_moves_the_floor_too_far_fails(self, pitch: float, why: str) -> None:
        camera = TRUE.copy()
        camera[3] = pitch
        assert not self._holds(camera), why

    def test_a_camera_that_takes_the_far_floor_off_the_picture_fails(self) -> None:
        """Tilted 3.5 degrees up, the top row goes past the horizon while the rows below move less than 2x."""
        reference, camera = TRUE.copy(), TRUE.copy()
        reference[3], camera[3] = -19.5, -16.0
        place = np.array([[0.0, -80.0, 0.0]])
        assert cp._floor_holds(reference, reference, 0.0, place, W, H)
        assert not cp._floor_holds(reference, camera, 0.0, place, W, H)


class TestTheFitsGuards:
    """What the public solve never reaches: its seed is valid by construction."""

    def _problem(self) -> cp._Problem:
        return cp._Problem(CORNERS, _pins(CORNERS), [], W, H)

    def test_a_camera_off_the_valid_range_has_no_residuals_and_no_fit(self) -> None:
        x0 = TRUE.copy()
        x0[6] = 200.0
        assert self._problem().residuals(x0) is None
        assert cp._fit(self._problem(), x0) is None

    def test_a_derivative_probe_off_the_valid_range_ends_the_fit(self) -> None:
        x0 = TRUE.copy()
        x0[6] = 179.0 - 1e-6
        assert self._problem().residuals(x0) is not None
        assert cp._fit(self._problem(), x0) is None

    def test_the_iteration_cap_returns_the_best_fit_so_far(self) -> None:
        x0 = TRUE.copy()
        x0[3] += 2.0
        start = self._problem().residuals(x0)
        capped = cp._fit(self._problem(), x0, iterations=1)
        full = cp._fit(self._problem(), x0)
        assert start is not None and capped is not None and full is not None
        assert float(full[1] @ full[1]) < float(capped[1] @ capped[1]) < float(start @ start)

    def test_points_no_fit_can_take_are_left_out_and_the_corners_solve(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fit = cp._fit
        monkeypatch.setattr(cp, "_fit", lambda problem, x0: None if problem.n else fit(problem, x0))
        solved = solve_with_points(CORNERS, _pins(CORNERS), [_point((0.0, -20.0)), _point((5.0, -35.0))], W, H)
        assert solved is not None
        assert [(p.status, p.reason) for p in solved.points] == [("flagged", cp.REASON_DISAGREES_CORNERS)] * 2
        assert all(math.isnan(p.residual) for p in solved.points)
        assert solved.points[0].position == pytest.approx((0.0, -20.0, 0.0))
        assert solved.camera == solve_camera_dlt([tuple(c) for c in CORNERS], [tuple(p) for p in _pins(CORNERS)], W, H)
