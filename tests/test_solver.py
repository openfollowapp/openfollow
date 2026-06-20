# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Example-based tests for the camera solver (``openfollow.scene.solver``):
project/unproject round-trips, DLT reconstruction, horizontal-FOV semantics,
behind-camera NaN clipping, and degenerate-fov/canvas rejection."""

from __future__ import annotations

import math

import numpy as np
import pytest

from openfollow.scene.solver import (
    hfov_to_vfov,
    project_points,
    solve_camera_dlt,
    unproject_to_plane,
    vfov_to_hfov,
)

pytestmark = pytest.mark.unit


def _world_corners() -> np.ndarray:
    return np.array(
        [
            (-5.0, -5.0, 0.0),
            (5.0, -5.0, 0.0),
            (5.0, 5.0, 0.0),
            (-5.0, 5.0, 0.0),
        ],
        dtype=np.float64,
    )


def test_project_and_unproject_roundtrip_on_plane() -> None:
    params = np.array([0.0, -8.0, 4.0, -25.0, 5.0, 0.0, 60.0], dtype=np.float64)
    world = _world_corners()

    projected = project_points(params, world, 1920.0, 1080.0)
    unprojected = unproject_to_plane(
        params,
        projected,
        1920.0,
        1080.0,
        plane_z_psn=0.0,
    )

    assert np.allclose(unprojected, world, atol=1e-5)


def test_solve_camera_dlt_reconstructs_known_projection() -> None:
    params = np.array([0.0, -10.0, 5.0, -28.0, 8.0, 1.0, 65.0], dtype=np.float64)
    world = _world_corners()
    screen = project_points(params, world, 1280.0, 720.0)

    solved = solve_camera_dlt(
        [tuple(p) for p in world],
        [tuple(p) for p in screen],
        1280.0,
        720.0,
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
    reprojection = project_points(solved_params, world, 1280.0, 720.0)
    assert float(np.max(np.abs(reprojection - screen))) < 2.0


def test_solve_camera_dlt_rejects_degenerate_input() -> None:
    world = _world_corners()
    bad_screen = [(100.0, 100.0)] * 4

    solved = solve_camera_dlt([tuple(p) for p in world], bad_screen, 1280.0, 720.0)

    assert solved is None


# --- Horizontal-FOV semantics ------------------------------------------------


def test_hfov_vfov_roundtrip() -> None:
    for hfov in (30.0, 60.0, 90.0, 120.0):
        for aspect in (4 / 3, 16 / 9, 21 / 9):
            v = hfov_to_vfov(hfov, aspect)
            h = vfov_to_hfov(v, aspect)
            assert math.isclose(h, hfov, abs_tol=1e-9)


def test_hfov_to_vfov_known_value() -> None:
    # 90° horizontal on a 16:9 canvas → ~58.7155° vertical.
    assert math.isclose(hfov_to_vfov(90.0, 16 / 9), 58.7155, abs_tol=1e-3)


def test_project_points_respects_horizontal_fov_edge() -> None:
    canvas_w, canvas_h = 1920.0, 1080.0
    # Camera at origin, looking along +Y in PSN (yaw=0, pitch=0, roll=0).
    params = np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 90.0], dtype=np.float64)
    # PSN forward = +Y, right = +X. A point 1 m right and 1 m ahead:
    world = np.array([[1.0, 1.0, 0.0]], dtype=np.float64)

    screen = project_points(params, world, canvas_w, canvas_h)
    # Right edge of the frame is x = canvas_w.
    assert math.isclose(float(screen[0, 0]), canvas_w, abs_tol=1e-6)


def test_project_points_respects_horizontal_fov_aspect_invariance() -> None:
    """HFOV=60° means the ±30° horizontal extent is the frame edge, regardless of aspect."""
    params = np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 60.0], dtype=np.float64)
    half_angle = math.radians(30.0)
    x = math.tan(half_angle)  # 1 m ahead, x m right → 30° off axis
    world = np.array([[x, 1.0, 0.0]], dtype=np.float64)

    for canvas_w, canvas_h in [(1920.0, 1080.0), (1280.0, 720.0), (1024.0, 768.0)]:
        screen = project_points(params, world, canvas_w, canvas_h)
        assert math.isclose(float(screen[0, 0]), canvas_w, abs_tol=1e-6)


def test_decompose_returns_horizontal_fov() -> None:
    """Round-trip: synthesize corners with known HFOV, solve, recover the same HFOV."""
    canvas_w, canvas_h = 1280.0, 720.0
    known_hfov = 72.5
    params = np.array(
        [0.0, -10.0, 5.0, -28.0, 8.0, 1.0, known_hfov],
        dtype=np.float64,
    )
    world = _world_corners()
    screen = project_points(params, world, canvas_w, canvas_h)

    solved = solve_camera_dlt(
        [tuple(p) for p in world],
        [tuple(p) for p in screen],
        canvas_w,
        canvas_h,
    )

    assert solved is not None
    assert math.isclose(solved.fov, known_hfov, abs_tol=0.1)


# --- Behind-camera clipping --------------------------------------------------


def test_points_behind_camera_project_to_nan() -> None:
    # Camera downstage looking upstage (+Y); a point well behind it must not
    # project to a finite, mirrored screen coordinate that downstream
    # isfinite filters would fail to discard.
    params = np.array([0.0, -10.0, 5.0, -20.0, 0.0, 0.0, 60.0], dtype=np.float64)
    behind = np.array([[0.0, -30.0, 0.0]], dtype=np.float64)
    out = project_points(params, behind, 1920.0, 1080.0)
    assert np.all(np.isnan(out[0]))


def test_points_in_front_still_project_finite() -> None:
    params = np.array([0.0, -10.0, 5.0, -20.0, 0.0, 0.0, 60.0], dtype=np.float64)
    front = np.array([[0.0, 10.0, 0.0]], dtype=np.float64)
    out = project_points(params, front, 1920.0, 1080.0)
    assert np.all(np.isfinite(out[0]))


# --- Degenerate fov / canvas are rejected (ValueError, not ZeroDivisionError) --


@pytest.mark.parametrize("bad_fov", [0.0, 0.5, 180.0, 200.0, float("nan")])
def test_project_points_rejects_degenerate_fov(bad_fov: float) -> None:
    params = np.array([0.0, -10.0, 5.0, -20.0, 0.0, 0.0, bad_fov], dtype=np.float64)
    world = _world_corners()
    with pytest.raises(ValueError):
        project_points(params, world, 1920.0, 1080.0)


@pytest.mark.parametrize(
    ("cw", "ch"),
    [
        (0.0, 1080.0),
        (1920.0, 0.0),
        (-1.0, 1080.0),
        # Non-finite slips past a bare ``<= 0`` (``NaN <= 0`` is False) and
        # json.loads accepts NaN/Infinity, so these must be rejected too.
        (float("nan"), 1080.0),
        (1920.0, float("nan")),
        (float("inf"), 1080.0),
        (1920.0, float("inf")),
    ],
)
def test_project_points_rejects_degenerate_canvas(cw: float, ch: float) -> None:
    params = np.array([0.0, -10.0, 5.0, -20.0, 0.0, 0.0, 60.0], dtype=np.float64)
    world = _world_corners()
    with pytest.raises(ValueError):
        project_points(params, world, cw, ch)


@pytest.mark.parametrize("bad_fov", [0.0, 180.0])
def test_unproject_rejects_degenerate_fov(bad_fov: float) -> None:
    params = np.array([0.0, -10.0, 5.0, -20.0, 0.0, 0.0, bad_fov], dtype=np.float64)
    screen = np.array([[960.0, 540.0]], dtype=np.float64)
    with pytest.raises(ValueError):
        unproject_to_plane(params, screen, 1920.0, 1080.0)


@pytest.mark.parametrize(
    ("cw", "ch"),
    [(0.0, 1080.0), (1920.0, 0.0), (float("nan"), 1080.0), (1920.0, float("inf"))],
)
def test_unproject_rejects_degenerate_canvas(cw: float, ch: float) -> None:
    params = np.array([0.0, -10.0, 5.0, -20.0, 0.0, 0.0, 60.0], dtype=np.float64)
    screen = np.array([[960.0, 540.0]], dtype=np.float64)
    with pytest.raises(ValueError):
        unproject_to_plane(params, screen, cw, ch)


def test_unproject_at_camera_plane_returns_nan() -> None:
    # Camera height (pos_z) equal to the target plane → the ray reaches the
    # plane at t == 0 (the camera origin). Reject it rather than returning a
    # self-referential "hit" at the camera position.
    params = np.array([0.0, -10.0, 0.0, -20.0, 0.0, 0.0, 60.0], dtype=np.float64)
    screen = np.array([[960.0, 540.0]], dtype=np.float64)
    out = unproject_to_plane(params, screen, 1920.0, 1080.0, plane_z_psn=0.0)
    assert np.all(np.isnan(out[0]))
