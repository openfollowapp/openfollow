# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Mutation-audit kills for :mod:`openfollow.scene.solver`.

Each test targets a specific mutation survivor, with the rationale documented
inline.

The existing :mod:`tests.test_solver` suite drove round-trip behaviour
against symmetric 10×10 world corners centred on the origin and small
non-zero yaw/pitch/roll (``-28°/8°/1°``).  Those inputs happen to hide
a cluster of mutants because:

* centroid of symmetric corners is (0, 0) → ``pts - centroid ==
  pts + centroid``, so the sign-flip in ``_normalize_points`` is
  invisible.
* ``roll ≈ 1°`` → ``cos(roll) ≈ 0.9998`` → ``cb * cc ≈ cb / cc``,
  so multiplication-vs-division swaps in ``_rotation_matrix`` slip
  under the 2-pixel reprojection tolerance.
* ``unproject_to_plane(plane_z_psn=…)`` is always called with an
  explicit keyword, so the default-value mutation ``0.0 → 1.0`` never
  takes effect.

The tests below break each of those free parameters so the mutation
survives only when the replacement genuinely has the same observable
effect – i.e. when it is a *true* equivalent mutant.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from openfollow.scene.solver import (
    _normalize_points,
    _rotation_matrix,
    project_points,
    solve_camera_dlt,
    unproject_to_plane,
)

pytestmark = pytest.mark.unit

# --------------------------------------------------------------------------- #
# _rotation_matrix – kill cb*cc → cb/cc, -sa*sb*sc → +sa*sb*sc, etc.
# --------------------------------------------------------------------------- #


class TestRotationMatrixSubstantialAngles:
    """Kill multiplication-vs-division mutants in ``_rotation_matrix``.

    Under a roll of 30° (`cos ≈ 0.866`), any ``cc`` factor swapped to
    a ``/ cc`` division produces a numerically different matrix element
    – the existing round-trip tests use roll=1° where `cos ≈ 0.9998`
    and the difference collapses into the tolerance.

    Assert on the orthogonality of the matrix directly rather than on
    a projection round-trip: rotation matrices are orthogonal
    (`R @ R.T == I`), and the mul-vs-div mutants break that invariant
    in exactly one column pair at non-trivial rolls.
    """

    def test_rotation_matrix_is_orthogonal_at_substantial_roll(self) -> None:
        # pitch = -35°, yaw = 20°, roll = 30° – no column is near-unity.
        R = _rotation_matrix(
            math.radians(-35.0),
            math.radians(20.0),
            math.radians(30.0),
        )
        # Orthogonal: R @ R.T is the identity matrix.
        assert np.allclose(R @ R.T, np.eye(3), atol=1e-10)

    def test_rotation_matrix_determinant_is_plus_one_at_substantial_roll(self) -> None:
        """A proper rotation has determinant +1 (right-handed).  Sign
        flips on any ``-sa`` / ``+sa`` term would flip the determinant
        sign – kills the unary-negation mutants.
        """
        R = _rotation_matrix(
            math.radians(-45.0),
            math.radians(30.0),
            math.radians(25.0),
        )
        assert math.isclose(float(np.linalg.det(R)), 1.0, abs_tol=1e-10)

    @pytest.mark.parametrize(
        "pitch, yaw, roll",
        [
            (0.0, 0.0, 30.0),  # isolates roll column interactions
            (45.0, 0.0, 0.0),
            (0.0, 45.0, 0.0),
            (15.0, 25.0, 35.0),
        ],
    )
    def test_rotation_matrix_columns_are_unit_length(self, pitch: float, yaw: float, roll: float) -> None:
        """Each column of a rotation matrix has unit length.  The
        ``* cc`` → ``/ cc`` mutants make one column's norm drift above
        1.0 at non-trivial roll.
        """
        R = _rotation_matrix(math.radians(pitch), math.radians(yaw), math.radians(roll))
        for col in range(3):
            norm = float(np.linalg.norm(R[:, col]))
            assert math.isclose(norm, 1.0, abs_tol=1e-10), (
                f"column {col} norm = {norm} at pitch={pitch}° yaw={yaw}° roll={roll}°"
            )


# --------------------------------------------------------------------------- #
# _normalize_points – kill pts - centroid → pts + centroid and similar.
# --------------------------------------------------------------------------- #


class TestNormalizePointsAsymmetricInput:
    """Kill sign-flip mutants in ``_normalize_points``.

    The existing suite's world corners are symmetric around the origin
    (``centroid == (0, 0)``) → ``pts - centroid == pts + centroid``,
    hiding the sign-flip.  Pass points whose centroid is *not* at the
    origin so ``shifted`` actually depends on the subtraction.
    """

    def test_centroid_subtraction_centres_non_symmetric_points(self) -> None:
        # Corners offset +100 on both axes so centroid ≠ 0.
        pts = np.array(
            [[100.0, 100.0], [105.0, 100.0], [105.0, 105.0], [100.0, 105.0]],
            dtype=np.float64,
        )
        normalized, _T = _normalize_points(pts)
        mean = normalized.mean(axis=0)
        assert abs(float(mean[0])) < 1e-10
        assert abs(float(mean[1])) < 1e-10

    def test_average_distance_after_normalisation_is_sqrt_two(self) -> None:
        """The scale factor is ``sqrt(2) / avg_dist``.  Mutants that
        replace ``sqrt(2)`` with ``sqrt(3)`` or invert the division
        (``sqrt(2) * avg_dist``) produce a different post-normalisation
        average distance.  The contract is: normalised average
        distance == sqrt(2).
        """
        # Asymmetric corners so avg_dist isn't 1.0 by coincidence.
        pts = np.array(
            [[100.0, 100.0], [250.0, 140.0], [320.0, 480.0], [80.0, 420.0]],
            dtype=np.float64,
        )
        normalized, _T = _normalize_points(pts)
        avg_dist = float(np.mean(np.linalg.norm(normalized, axis=1)))
        assert math.isclose(avg_dist, math.sqrt(2.0), abs_tol=1e-10)

    def test_transform_matrix_recovers_original_points(self) -> None:
        pts = np.array(
            [[250.0, 80.0], [310.0, 90.0], [320.0, 260.0], [245.0, 255.0]],
            dtype=np.float64,
        )
        normalized, T = _normalize_points(pts)
        homo = np.column_stack([normalized, np.ones(len(normalized))])
        reconstructed = (np.linalg.inv(T) @ homo.T).T[:, :2]
        assert np.allclose(reconstructed, pts, atol=1e-10)


# --------------------------------------------------------------------------- #
# unproject_to_plane – kill plane_z_psn default (0.0 → 1.0) + plane at z != 0.
# --------------------------------------------------------------------------- #


class TestUnprojectDefaultAndNonZeroPlane:
    def test_default_plane_z_is_the_ground_plane(self) -> None:
        """Kill mutant ``plane_z_psn: float = 0.0`` → ``1.0``.  The
        existing suite always passes ``plane_z_psn=0.0`` explicitly;
        this one relies on the default.
        """
        params = np.array([0.0, -8.0, 4.0, -25.0, 5.0, 0.0, 60.0], dtype=np.float64)
        world_at_z0 = np.array(
            [
                [-3.0, -3.0, 0.0],
                [3.0, -3.0, 0.0],
                [3.0, 3.0, 0.0],
                [-3.0, 3.0, 0.0],
            ],
            dtype=np.float64,
        )
        screen = project_points(params, world_at_z0, 1920.0, 1080.0)
        # No ``plane_z_psn`` kwarg – relies on the default.
        unprojected = unproject_to_plane(params, screen, 1920.0, 1080.0)
        assert np.allclose(unprojected, world_at_z0, atol=1e-5)

    def test_non_zero_plane_z_roundtrip(self) -> None:
        """Project corners on a raised platform and unproject back to
        the same plane Z.  Guards against any mutation that dropped
        the ``plane_y = plane_z_psn`` linkage (the variable that
        determines the ray-plane intersection).
        """
        params = np.array([0.0, -8.0, 6.0, -25.0, 0.0, 0.0, 60.0], dtype=np.float64)
        world_at_z1 = np.array(
            [
                [-2.0, -2.0, 1.5],
                [2.0, -2.0, 1.5],
                [2.0, 2.0, 1.5],
                [-2.0, 2.0, 1.5],
            ],
            dtype=np.float64,
        )
        screen = project_points(params, world_at_z1, 1920.0, 1080.0)
        unprojected = unproject_to_plane(params, screen, 1920.0, 1080.0, plane_z_psn=1.5)
        assert np.allclose(unprojected, world_at_z1, atol=1e-5)

    def test_rays_pointing_away_from_plane_return_nan(self) -> None:
        # Camera at z = -2 (below floor), looking down (pitch very negative)
        # → ray goes further below, never hits z = 0 in front.
        params = np.array([0.0, 0.0, -2.0, -80.0, 0.0, 0.0, 60.0], dtype=np.float64)
        screen = np.array([[960.0, 540.0]], dtype=np.float64)  # centre pixel
        result = unproject_to_plane(params, screen, 1920.0, 1080.0, plane_z_psn=0.0)
        assert result.shape == (1, 3)
        assert np.all(np.isnan(result[0]))


# --------------------------------------------------------------------------- #
# solve_camera_dlt – additional regression guards
# --------------------------------------------------------------------------- #


class TestSolveDltAsymmetricCorners:
    """The existing ``test_solve_camera_dlt_reconstructs_known_projection``
    uses corners centred on the origin.  Repeat the solve with
    off-origin corners so any DLT normalisation bug that only fires
    under non-zero centroid is caught.
    """

    def test_solves_with_non_centred_world_corners(self) -> None:
        canvas_w, canvas_h = 1280.0, 720.0
        params = np.array([1.0, -12.0, 5.0, -28.0, 8.0, 1.0, 65.0], dtype=np.float64)
        # Rectangle shifted +5 on x so centroid is (5, 0) not (0, 0).
        world = np.array(
            [
                [0.0, -5.0, 0.0],
                [10.0, -5.0, 0.0],
                [10.0, 5.0, 0.0],
                [0.0, 5.0, 0.0],
            ],
            dtype=np.float64,
        )
        screen = project_points(params, world, canvas_w, canvas_h)

        solved = solve_camera_dlt(
            [tuple(p) for p in world],
            [tuple(p) for p in screen],
            canvas_w,
            canvas_h,
        )

        assert solved is not None
        solved_params = np.array(
            [
                solved.pos_x,
                solved.pos_y,
                solved.pos_z,
                solved.pitch,
                solved.yaw,
                solved.roll,
                solved.fov,
            ],
            dtype=np.float64,
        )
        reprojection = project_points(solved_params, world, canvas_w, canvas_h)
        assert float(np.max(np.abs(reprojection - screen))) < 2.0
