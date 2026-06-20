# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Property-based tests for the camera solver.

100% line coverage proves the solver's lines *run*; these hypothesis tests
prove the algebraic invariants hold across the whole valid pose space –
project ∘ unproject round-trips on a plane, the rotation matrix stays
special-orthogonal, hfov ↔ vfov is a true inverse, and ``solve_camera_dlt``
either reconstructs the camera or returns ``None`` (never a NaN-laden or
wildly-wrong result).

Tolerances and the camera/point conventions mirror the example-based tests
in ``test_solver.py`` / ``test_solver_mutation.py``. Strategies generate a
realistic tracker-calibration regime (an elevated camera downstage of the
origin, tilted down at the stage) so the geometry stays well-conditioned and
``assume()`` discards only the rare draw that projects behind the camera.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from openfollow.configuration import CameraConfig
from openfollow.scene.solver import (
    _rotation_matrix,
    hfov_to_vfov,
    project_points,
    solve_camera_dlt,
    unproject_to_plane,
    vfov_to_hfov,
)

pytestmark = pytest.mark.unit


def _floats(lo: float, hi: float) -> st.SearchStrategy[float]:
    return st.floats(min_value=lo, max_value=hi, allow_nan=False, allow_infinity=False)


# Realistic calibration regime: camera elevated and downstage of the origin,
# tilted down at the stage. ``pitch`` stays clear of the horizon so the
# projective round-trips stay numerically tight (a grazing camera amplifies
# unprojection error without any real-world relevance).
@st.composite
def _camera_params(draw: st.DrawFn) -> np.ndarray:
    pos_x = draw(_floats(-15.0, 15.0))
    pos_y = draw(_floats(-30.0, -5.0))
    pos_z = draw(_floats(4.0, 20.0))
    pitch = draw(_floats(-75.0, -20.0))
    yaw = draw(_floats(-25.0, 25.0))
    roll = draw(_floats(-15.0, 15.0))
    fov = draw(_floats(40.0, 100.0))
    return np.array([pos_x, pos_y, pos_z, pitch, yaw, roll, fov], dtype=np.float64)


# A well-spread, non-degenerate coplanar quad at z=0 (the calibration grid
# plane), the input shape ``solve_camera_dlt`` is designed for.
@st.composite
def _quad(draw: st.DrawFn) -> np.ndarray:
    half_w = draw(_floats(3.0, 10.0))
    half_d = draw(_floats(3.0, 10.0))
    cx = draw(_floats(-3.0, 3.0))
    cy = draw(_floats(-3.0, 3.0))
    return np.array(
        [
            (cx - half_w, cy - half_d, 0.0),
            (cx + half_w, cy - half_d, 0.0),
            (cx + half_w, cy + half_d, 0.0),
            (cx - half_w, cy + half_d, 0.0),
        ],
        dtype=np.float64,
    )


_CANVASES = st.sampled_from(
    [
        (1920.0, 1080.0),
        (1280.0, 720.0),
        (1024.0, 768.0),
        (3840.0, 2160.0),
        (2560.0, 1080.0),
    ]
)

_XY = st.tuples(_floats(-8.0, 8.0), _floats(-8.0, 8.0))


def _solved_params(solved: CameraConfig) -> np.ndarray:
    return np.array(
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


# --- project ∘ unproject round-trip ------------------------------------------


@given(
    params=_camera_params(),
    canvas=_CANVASES,
    plane_z=_floats(0.0, 5.0),
    xy=st.lists(_XY, min_size=1, max_size=6),
)
def test_project_then_unproject_is_identity_on_plane(
    params: np.ndarray,
    canvas: tuple[float, float],
    plane_z: float,
    xy: list[tuple[float, float]],
) -> None:
    """Projecting plane points and unprojecting back recovers them (atol 1e-4)."""
    w, h = canvas
    world = np.array([(x, y, plane_z) for x, y in xy], dtype=np.float64)
    screen = project_points(params, world, w, h)
    assume(np.all(np.isfinite(screen)))
    back = unproject_to_plane(params, screen, w, h, plane_z_psn=plane_z)
    assume(np.all(np.isfinite(back)))
    assert np.allclose(back, world, atol=1e-4)


# --- hfov ↔ vfov inverse -----------------------------------------------------


@given(hfov=_floats(5.0, 170.0), aspect=_floats(0.5, 3.0))
def test_hfov_vfov_roundtrip(hfov: float, aspect: float) -> None:
    recovered = vfov_to_hfov(hfov_to_vfov(hfov, aspect), aspect)
    assert math.isclose(recovered, hfov, rel_tol=1e-9, abs_tol=1e-7)


@given(vfov=_floats(5.0, 170.0), aspect=_floats(0.5, 3.0))
def test_vfov_hfov_roundtrip(vfov: float, aspect: float) -> None:
    recovered = hfov_to_vfov(vfov_to_hfov(vfov, aspect), aspect)
    assert math.isclose(recovered, vfov, rel_tol=1e-9, abs_tol=1e-7)


# --- rotation matrix is special-orthogonal -----------------------------------


@given(
    pitch=_floats(-180.0, 180.0),
    yaw=_floats(-180.0, 180.0),
    roll=_floats(-180.0, 180.0),
)
def test_rotation_matrix_is_orthonormal(pitch: float, yaw: float, roll: float) -> None:
    R = _rotation_matrix(math.radians(pitch), math.radians(yaw), math.radians(roll))
    assert np.allclose(R @ R.T, np.eye(3), atol=1e-10)
    assert np.allclose(np.linalg.norm(R, axis=0), 1.0, atol=1e-10)


@given(
    pitch=_floats(-180.0, 180.0),
    yaw=_floats(-180.0, 180.0),
    roll=_floats(-180.0, 180.0),
)
def test_rotation_matrix_determinant_is_one(pitch: float, yaw: float, roll: float) -> None:
    R = _rotation_matrix(math.radians(pitch), math.radians(yaw), math.radians(roll))
    assert math.isclose(float(np.linalg.det(R)), 1.0, abs_tol=1e-10)


# --- solve_camera_dlt --------------------------------------------------------


@given(params=_camera_params(), canvas=_CANVASES, corners=_quad())
def test_solve_dlt_recovers_horizontal_fov(
    params: np.ndarray,
    canvas: tuple[float, float],
    corners: np.ndarray,
) -> None:
    """Solving a synthesised projection recovers the camera's horizontal FOV.

    The headline calibration correctness claim: project a known camera's view
    of a coplanar quad, solve, and recover the same FOV. Empirically the error
    stays under 0.005° across the whole regime (the focal length comes from the
    homography columns, which are well-conditioned even when extrinsic pixel
    error grows for off-screen corners); 0.05° is a 10× margin.
    """
    w, h = canvas
    screen = project_points(params, corners, w, h)
    assume(np.all(np.isfinite(screen)))
    solved = solve_camera_dlt(
        [tuple(p) for p in corners],
        [tuple(p) for p in screen],
        w,
        h,
    )
    assume(solved is not None)
    assert math.isclose(solved.fov, float(params[6]), abs_tol=0.05)


@given(
    params=_camera_params(),
    canvas=_CANVASES,
    fx0=_floats(0.1, 0.4),
    fy0=_floats(0.1, 0.4),
    fx1=_floats(0.6, 0.9),
    fy1=_floats(0.6, 0.9),
)
def test_solve_dlt_reprojects_on_screen_corners_within_2px(
    params: np.ndarray,
    canvas: tuple[float, float],
    fx0: float,
    fy0: float,
    fx1: float,
    fy1: float,
) -> None:
    """End-to-end calibration accuracy on the realistic input: 4 corners the
    operator clicks *within* the frame round-trip through solve to < 2 px.

    The corners are built by unprojecting on-screen pixels onto the ground
    plane, so the correspondence is always well-framed – the off-screen-corner
    geometry that makes a naive reprojection bound flaky (residuals up to ~14 px,
    still under the solver's 20 px gate) cannot arise here. Measured max ≈ 0.8 px
    across seeds, so the 2 px bound has comfortable headroom.
    """
    w, h = canvas
    screen = np.array(
        [[fx0 * w, fy0 * h], [fx1 * w, fy0 * h], [fx1 * w, fy1 * h], [fx0 * w, fy1 * h]],
        dtype=np.float64,
    )
    world = unproject_to_plane(params, screen, w, h, plane_z_psn=0.0)
    assume(np.all(np.isfinite(world)))
    solved = solve_camera_dlt(
        [tuple(p) for p in world],
        [tuple(p) for p in screen],
        w,
        h,
    )
    assume(solved is not None)
    reproj = project_points(_solved_params(solved), world, w, h)
    assert float(np.max(np.abs(reproj - screen))) < 2.0


@given(params=_camera_params(), canvas=_CANVASES, corners=_quad())
def test_solve_dlt_never_returns_a_non_finite_camera(
    params: np.ndarray,
    canvas: tuple[float, float],
    corners: np.ndarray,
) -> None:
    w, h = canvas
    screen = project_points(params, corners, w, h)
    assume(np.all(np.isfinite(screen)))
    solved = solve_camera_dlt(
        [tuple(p) for p in corners],
        [tuple(p) for p in screen],
        w,
        h,
    )
    if solved is None:
        return
    assert np.all(np.isfinite(_solved_params(solved)))


@given(
    corners=_quad(),
    screen=st.lists(
        st.tuples(_floats(-3000.0, 3000.0), _floats(-3000.0, 3000.0)),
        min_size=4,
        max_size=4,
    ),
    canvas=_CANVASES,
)
def test_solve_dlt_handles_arbitrary_screen_without_crash_or_nan(
    corners: np.ndarray,
    screen: list[tuple[float, float]],
    canvas: tuple[float, float],
) -> None:
    """Arbitrary 4-point screen input (incl. degenerate/collinear/collapsed)
    never raises and never yields a non-finite camera – it solves to a valid
    ``CameraConfig`` or ``None``. (A collapsed screen is *not* always rejected:
    a camera that maps the quad near that point reprojects with tiny error and
    passes the solver's residual gate, so the only invariant is finite-or-None.)

    This is primarily a *no-crash* guard: a random ±3000 px quad vs a 3–10 m
    world quad is almost always rejected, so the ``solved is not None`` branch
    rarely fires (≈0 under the derandomized ``ci`` profile). The
    non-finite-camera guarantee for realistic inputs is exercised by
    ``test_solve_dlt_never_returns_a_non_finite_camera`` above.
    """
    w, h = canvas
    solved = solve_camera_dlt([tuple(p) for p in corners], screen, w, h)
    if solved is not None:
        assert np.all(np.isfinite(_solved_params(solved)))


# --- unproject robustness ----------------------------------------------------


@given(
    params=_camera_params(),
    canvas=_CANVASES,
    plane_z=_floats(-5.0, 5.0),
    screen=st.lists(
        st.tuples(_floats(-5000.0, 5000.0), _floats(-5000.0, 5000.0)),
        min_size=1,
        max_size=6,
    ),
)
def test_unproject_rows_are_all_finite_or_all_nan(
    params: np.ndarray,
    canvas: tuple[float, float],
    plane_z: float,
    screen: list[tuple[float, float]],
) -> None:
    w, h = canvas
    out = unproject_to_plane(
        params,
        np.array(screen, dtype=np.float64),
        w,
        h,
        plane_z_psn=plane_z,
    )
    assert out.shape == (len(screen), 3)
    for row in out:
        assert np.all(np.isfinite(row)) or np.all(np.isnan(row))
