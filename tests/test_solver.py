# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Example-based tests for the camera solver (``openfollow.scene.solver``):
project/unproject round-trips, DLT reconstruction, horizontal-FOV semantics,
behind-camera NaN clipping, and degenerate-fov/canvas rejection."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from openfollow.scene.solver import (
    compute_homography,
    decompose_homography,
    ground_circle_world_ring,
    hfov_to_vfov,
    project_points,
    ring_silhouette_edges,
    solve_camera_dlt,
    unproject_to_plane,
    vfov_to_hfov,
)

pytestmark = pytest.mark.unit


def test_ground_circle_world_ring_geometry() -> None:
    ring = ground_circle_world_ring(2.0, -1.0, 0.5, 0.3, segments=8)
    assert len(ring) == 8
    # Every point sits at the requested height and exactly the radius away
    # from the centre in the XY plane.
    for x, y, z in ring:
        assert z == 0.5
        assert math.hypot(x - 2.0, y - (-1.0)) == pytest.approx(0.3)
    # First point is at angle 0 → offset purely along +X.
    assert ring[0] == pytest.approx((2.3, -1.0, 0.5))


def test_ground_circle_world_ring_default_segment_count() -> None:
    assert len(ground_circle_world_ring(0.0, 0.0, 0.0, 1.0)) == 24


def _ellipse(cx: float, cy: float, rx: float, ry: float, n: int = 24) -> np.ndarray:
    ang = np.linspace(0.0, 2 * math.pi, n, endpoint=False)
    return np.column_stack([cx + rx * np.cos(ang), cy + ry * np.sin(ang)])


def _rotated_ellipse(cx: float, cy: float, rx: float, ry: float, tilt_deg: float, n: int = 24) -> np.ndarray:
    ang = np.linspace(0.0, 2 * math.pi, n, endpoint=False)
    x, y = rx * np.cos(ang), ry * np.sin(ang)
    c, s_ = math.cos(math.radians(tilt_deg)), math.sin(math.radians(tilt_deg))
    return np.column_stack([cx + x * c - y * s_, cy + x * s_ + y * c])


def _supports(edge: tuple[tuple[float, float], tuple[float, float]], pts: np.ndarray) -> bool:
    """True when every point of ``pts`` lies on one side of the edge's line."""
    (x0, y0), (x1, y1) = edge
    cross = (x1 - x0) * (pts[:, 1] - y0) - (y1 - y0) * (pts[:, 0] - x0)
    return bool(np.all(cross >= -1e-6) or np.all(cross <= 1e-6))


class TestRingSilhouetteEdges:
    """The two edges are the common tangents of the projected rings: each is a
    supporting line of both rings, one per side, from a floor point to a top
    point."""

    def _check(self, floor: np.ndarray, top: np.ndarray, fc: tuple[float, float], tc: tuple[float, float]) -> list:
        edges = ring_silhouette_edges(floor, top, fc, tc)
        assert len(edges) == 2
        both = np.vstack([floor, top])
        for f, t in edges:
            assert any(np.allclose(f, pt) for pt in floor)
            assert any(np.allclose(t, pt) for pt in top)
            assert _supports((f, t), both)
        return edges

    def test_front_view_joins_the_outer_points(self) -> None:
        # Seen from the front: a wide base ellipse low on screen, a narrower
        # top ellipse above it. The tangents leave from the outer flanks.
        floor = _ellipse(400.0, 600.0, 50.0, 10.0)
        top = _ellipse(400.0, 300.0, 30.0, 6.0)
        edges = self._check(floor, top, (400.0, 600.0), (400.0, 300.0))
        left, right = sorted(edges, key=lambda e: e[0][0])
        assert left[0][0] < 400.0 and left[1][0] < 400.0
        assert right[0][0] > 400.0 and right[1][0] > 400.0

    def test_off_axis_tilted_ellipses_get_true_tangents(self) -> None:
        # A marker far off the optical axis under a steep pitch: perspective
        # tilts the two ellipses differently, so the outermost point along
        # the perpendicular to the axis is not the tangent point. The edge
        # must still support both rings instead of cutting into one.
        floor = _rotated_ellipse(900.0, 700.0, 80.0, 22.0, 28.0)
        top = _rotated_ellipse(820.0, 380.0, 45.0, 11.0, 18.0)
        self._check(floor, top, (900.0, 700.0), (820.0, 380.0))

    def test_sides_follow_a_tilted_axis(self) -> None:
        # Roll the camera 90 degrees: the axis runs along screen X.
        floor = _ellipse(600.0, 400.0, 10.0, 50.0)
        top = _ellipse(300.0, 400.0, 6.0, 30.0)
        edges = self._check(floor, top, (600.0, 400.0), (300.0, 400.0))
        ys = sorted(f[1] for f, _ in edges)
        assert ys[0] < 400.0 < ys[1]

    def test_apex_gets_the_two_tangents_from_the_base(self) -> None:
        # A top radius of zero: both edges end at the apex and support the base.
        floor = _ellipse(400.0, 600.0, 50.0, 10.0)
        apex = np.array([[430.0, 250.0]])
        edges = self._check(floor, apex, (400.0, 600.0), (430.0, 250.0))
        assert all(t == (430.0, 250.0) for _, t in edges)

    def test_top_ring_inside_the_base_falls_back_to_the_axis_split(self) -> None:
        # Seen from above the top ring sits inside the base ring: the hull
        # has no bridge, so the outermost point per side is joined instead.
        floor = _ellipse(400.0, 400.0, 50.0, 50.0)
        top = _ellipse(410.0, 395.0, 10.0, 10.0)
        edges = ring_silhouette_edges(floor, top, (400.0, 400.0), (410.0, 395.0))
        assert len(edges) == 2
        for f, t in edges:
            assert any(np.allclose(f, pt) for pt in floor)
            assert any(np.allclose(t, pt) for pt in top)

    def test_identical_rings_still_return_two_edges(self) -> None:
        # Both centres coincide and both rings are the same: the fallback
        # split is horizontal so the call stays total.
        ring = _ellipse(400.0, 400.0, 20.0, 20.0)
        edges = ring_silhouette_edges(ring, ring, (400.0, 400.0), (400.0, 400.0))
        assert len(edges) == 2
        xs = sorted(f[0] for f, _ in edges)
        assert xs == [pytest.approx(380.0), pytest.approx(420.0)]

    @pytest.mark.parametrize("empty_floor", [True, False])
    def test_an_empty_ring_yields_no_edges(self, empty_floor: bool) -> None:
        ring = _ellipse(400.0, 400.0, 20.0, 20.0)
        none = np.zeros((0, 2))
        floor, top = (none, ring) if empty_floor else (ring, none)
        assert ring_silhouette_edges(floor, top, (400.0, 400.0), (400.0, 300.0)) == []

    def test_partial_rings_use_the_points_that_remain(self) -> None:
        # Half the floor ring went behind the camera; the edges come from what
        # is left rather than vanishing with the ring.
        floor = _ellipse(400.0, 600.0, 50.0, 10.0)[:12]
        top = _ellipse(400.0, 300.0, 30.0, 6.0)
        self._check(floor, top, (400.0, 600.0), (400.0, 300.0))


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


def test_solve_camera_dlt_rejects_mirror_solution_that_reprojects_to_nan() -> None:
    """A solve that places the calibration points behind the camera is rejected.

    The homography decomposition has a twofold sign ambiguity. For some
    well-framed inputs it lands on the mirror branch – a finite camera below
    the floor (pos_z < 0, roll ~= 180 deg) whose own world points fall behind
    the lens, so ``project_points`` returns all-NaN. The reprojection gate must
    reject this: ``NaN > 20.0`` is ``False``, so a bare threshold check lets the
    bad camera through. The contract is reconstruct-or-None, never a camera that
    can't reproject its own input.

    Inputs are the exact regime that ``test_solver_properties`` falsified:
    on-screen corners unprojected onto z=0, then solved.
    """
    params = np.array([-12.0, -5.0, 4.0, -68.0, -25.0, 0.0, 40.0], dtype=np.float64)
    w, h = 1920.0, 1080.0
    screen = np.array(
        [[0.25 * w, 0.25 * h], [0.75 * w, 0.25 * h], [0.75 * w, 0.75 * h], [0.25 * w, 0.75 * h]],
        dtype=np.float64,
    )
    world = unproject_to_plane(params, screen, w, h, plane_z_psn=0.0)
    assert np.all(np.isfinite(world))

    solved = solve_camera_dlt([tuple(p) for p in world], [tuple(p) for p in screen], w, h)

    # Either the solver rejected the mirror branch, or it returned a camera
    # that genuinely reproduces the input (finite reprojection) – never a
    # camera whose own points reproject to NaN.
    if solved is not None:
        solved_params = np.array(
            [solved.pos_x, solved.pos_y, solved.pos_z, solved.pitch, solved.yaw, solved.roll, solved.fov],
            dtype=np.float64,
        )
        reproj = project_points(solved_params, world, w, h)
        assert np.all(np.isfinite(reproj)), "solver returned a camera that reprojects its own input to NaN"


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


# --------------------------------------------------------------------------- #
# Camera orientation convention
#
# The round-trip tests above (project ∘ unproject, project ∘ solve ∘ project)
# all read the same rotation, so they hold for *any* self-consistent pose
# convention. The tests below make absolute claims instead: where a given
# pitch/yaw actually aims the camera in the world.
# --------------------------------------------------------------------------- #


def _centre_floor_hit(params: np.ndarray, canvas_w: float, canvas_h: float) -> np.ndarray:
    """World point under the centre of frame, i.e. where the camera is aimed.

    The screen centre unprojects along the optical axis, so this is the
    observable form of "which way is the camera pointing".
    """
    centre = np.array([[canvas_w / 2.0, canvas_h / 2.0]], dtype=np.float64)
    return unproject_to_plane(params, centre, canvas_w, canvas_h, plane_z_psn=0.0)[0]


class TestCameraOrientationConvention:
    """Pitch tilts about the camera's own right axis, after yaw has panned it.

    So ``pitch`` is the depression below the horizon and ``yaw`` is the ground
    bearing, independently of each other, at every heading.
    """

    def test_upstage_camera_sees_the_grid(self) -> None:
        """A camera upstage of the grid, looking downstage and down at it.

        Reported rig: 6.9 m up and 5.25 m upstage of a 6.1 x 3.64 m grid that
        spans y = 0…3.64, tilted 65° down and panned 180° to face downstage.
        Every corner is plainly in shot, so every corner must project.
        """
        params = np.array([0.5, 5.25, 6.9, -65.0, 180.0, 0.0, 69.09], dtype=np.float64)
        half_w, half_d = 3.05, 1.82
        corners = np.array(
            [
                (half_w, 0.0, 0.0),
                (-half_w, 0.0, 0.0),
                (-half_w, 2 * half_d, 0.0),
                (half_w, 2 * half_d, 0.0),
                (0.0, half_d, 0.0),  # grid centre / reference point
            ],
            dtype=np.float64,
        )

        screen = project_points(params, corners, 1280.0, 720.0)

        assert np.all(np.isfinite(screen)), f"corners fell behind the lens: {screen}"
        assert np.all(screen[:, 0] >= 0.0) and np.all(screen[:, 0] <= 1280.0)
        assert np.all(screen[:, 1] >= 0.0) and np.all(screen[:, 1] <= 720.0)

    @pytest.mark.parametrize("yaw", list(range(0, 360, 10)))
    def test_negative_pitch_looks_down_at_every_yaw(self, yaw: int) -> None:
        """Panning the camera must not change how far below the horizon it aims.

        A camera 8 m up tilted 65° down reaches the floor 8/tan(65°) away no
        matter which way it faces.
        """
        pitch = -65.0
        params = np.array([0.0, 0.0, 8.0, pitch, float(yaw), 0.0, 60.0], dtype=np.float64)

        hit = _centre_floor_hit(params, 1920.0, 1080.0)

        assert np.all(np.isfinite(hit)), f"optical axis never reaches the floor at yaw={yaw}"
        assert hit[2] == pytest.approx(0.0, abs=1e-9)
        reach = math.hypot(hit[0] - 0.0, hit[1] - 0.0)
        assert reach == pytest.approx(8.0 / math.tan(math.radians(65.0)), abs=1e-6)

    @pytest.mark.parametrize("yaw", [-135.0, -90.0, -45.0, 0.0, 45.0, 90.0, 135.0, 180.0])
    def test_yaw_is_the_ground_bearing(self, yaw: float) -> None:
        """Yaw pans the aim point around the camera by exactly that angle.

        Bearing is measured from upstage (+Y) toward stage left (+X), matching
        the ``Yaw (left −)`` label on the camera form.
        """
        params = np.array([2.0, -3.0, 7.0, -50.0, yaw, 0.0, 60.0], dtype=np.float64)

        hit = _centre_floor_hit(params, 1920.0, 1080.0)

        assert np.all(np.isfinite(hit))
        bearing = math.degrees(math.atan2(hit[0] - 2.0, hit[1] - (-3.0)))
        assert bearing == pytest.approx(yaw, abs=1e-6)

    @pytest.mark.parametrize("yaw", [-90.0, 90.0])
    def test_side_on_camera_still_tilts_down(self, yaw: float) -> None:
        """A box-boom camera looks across the stage *and* down into it.

        Facing along the X axis must not pin the optical axis to the horizon.
        """
        params = np.array([-9.0, 1.0, 6.0, -35.0, yaw, 0.0, 60.0], dtype=np.float64)

        hit = _centre_floor_hit(params, 1920.0, 1080.0)

        assert np.all(np.isfinite(hit)), "side-on camera aims at the horizon, never the floor"
        # Aims across the stage along X, at the depression angle pitch asks for.
        assert hit[1] == pytest.approx(1.0, abs=1e-6)
        reach = abs(hit[0] - (-9.0))
        assert reach == pytest.approx(6.0 / math.tan(math.radians(35.0)), abs=1e-6)

    @pytest.mark.parametrize(
        ("roll", "expected"),
        [
            (
                0.0,
                [
                    (117.6381, 906.6958),
                    (1465.4171, 906.6958),
                    (1224.7765, 411.5542),
                    (518.7058, 411.5542),
                    (835.2268, 384.8844),
                ],
            ),
            (
                -12.0,
                [
                    (212.2861, 1073.8195),
                    (1530.6129, 793.6005),
                    (1192.2851, 359.3109),
                    (501.6437, 506.1112),
                    (805.7031, 414.2158),
                ],
            ),
            (
                25.0,
                [
                    (41.5885, 516.3417),
                    (1263.0911, 1085.9377),
                    (1254.2526, 535.4879),
                    (614.3352, 237.0895),
                    (912.4718, 346.6861),
                ],
            ),
        ],
    )
    def test_head_on_camera_projects_to_pinned_pixels(self, roll: float, expected: list[tuple[float, float]]) -> None:
        """Golden pixels for a yaw-0 camera, the overwhelmingly common rig.

        A camera facing straight upstage is unaffected by how pitch and yaw
        compose, so these coordinates are the guarantee that a head-on
        installation keeps projecting exactly where it always did.
        """
        params = np.array([1.0, -9.0, 5.5, -27.0, 0.0, roll, 72.0], dtype=np.float64)
        world = np.array(
            [
                (-4.0, -3.0, 0.0),
                (4.0, -3.0, 0.0),
                (4.0, 5.0, 0.0),
                (-4.0, 5.0, 0.0),
                (0.0, 1.0, 1.8),
            ],
            dtype=np.float64,
        )

        screen = project_points(params, world, 1920.0, 1080.0)

        assert screen == pytest.approx(np.array(expected), abs=1e-4)


class TestWizardIllustrationAgreement:
    """The Setup Wizard's camera-position preview must match what gets drawn.

    Step 4 sketches the ground footprint of the frustum client-side, from its
    own copy of the camera model (``updateCamIllustration`` in ``wizard.tpl``).
    Nothing links the two but this test, and a preview that disagrees with the
    projection sends the operator chasing an overlay that never lines up.
    """

    # The formulas ``_tpl_footprint`` below mirrors. Pinning the template
    # source is what makes the mirror trustworthy: without it, editing the JS
    # leaves this suite green and the preview drifts off the projection again.
    _TPL_CAMERA_MODEL = (
        "var lookX = Math.cos(pitchR) * Math.sin(yawR);",
        "var lookY = Math.cos(pitchR) * Math.cos(yawR);",
        "var lookZ = Math.sin(pitchR);",
        "var Rv = [Math.cos(yawR), -Math.sin(yawR), 0];",
        "var Uv = [-Math.sin(yawR) * Math.sin(pitchR), -Math.cos(yawR) * Math.sin(pitchR), Math.cos(pitchR)];",
        "var vfov = (2 * Math.atan(Math.tan((fov / 2) * rad) * 9 / 16)) / rad;",
    )

    @staticmethod
    def _wizard_tpl() -> str:
        path = Path(__file__).resolve().parent.parent / "openfollow" / "web" / "templates" / "wizard.tpl"
        return path.read_text(encoding="utf-8")

    def test_template_still_uses_the_mirrored_camera_model(self) -> None:
        """The preview's own camera model is what the rest of this class mirrors.

        Change one of these lines in ``wizard.tpl`` and the numeric agreement
        test below silently stops describing the template, so pin them here and
        update both together.
        """
        src = self._wizard_tpl()
        for line in self._TPL_CAMERA_MODEL:
            assert line in src, f"wizard.tpl no longer contains {line!r} – update _tpl_footprint to match"

    @staticmethod
    def _tpl_footprint(
        pos: tuple[float, float, float],
        pitch: float,
        yaw: float,
        hfov: float,
    ) -> list[tuple[float, float]]:
        """Ground footprint the way ``wizard.tpl`` computes it.

        Mirrors the look vector, camera basis and 16:9 vertical FOV the
        template derives, then intersects the four frustum corners with the
        floor. Corner order is top-left, top-right, bottom-right, bottom-left.
        """
        cx, cy, cz = pos
        p, y = math.radians(pitch), math.radians(yaw)
        look = (math.cos(p) * math.sin(y), math.cos(p) * math.cos(y), math.sin(p))
        right = (math.cos(y), -math.sin(y), 0.0)
        up = (-math.sin(y) * math.sin(p), -math.cos(y) * math.sin(p), math.cos(p))
        th = math.tan(math.radians(hfov) / 2.0)
        tv = math.tan(math.atan(th * 9 / 16))

        hits = []
        for sx, sy in ((-1, 1), (1, 1), (1, -1), (-1, -1)):
            d = tuple(look[i] + sx * th * right[i] + sy * tv * up[i] for i in range(3))
            t = -cz / d[2]
            hits.append((cx + d[0] * t, cy + d[1] * t))
        return hits

    @pytest.mark.parametrize(
        ("pitch", "yaw"),
        [(-45.0, 0.0), (-45.0, 180.0), (-50.0, 90.0), (-40.0, -60.0), (-65.0, 137.0)],
    )
    def test_footprint_matches_unprojected_frame_corners(self, pitch: float, yaw: float) -> None:
        canvas_w, canvas_h = 1920.0, 1080.0
        pos = (1.5, -2.0, 7.0)
        hfov = 70.0
        params = np.array([*pos, pitch, yaw, 0.0, hfov], dtype=np.float64)
        frame_corners = np.array(
            [(0.0, 0.0), (canvas_w, 0.0), (canvas_w, canvas_h), (0.0, canvas_h)],
            dtype=np.float64,
        )

        world = unproject_to_plane(params, frame_corners, canvas_w, canvas_h, plane_z_psn=0.0)

        assert np.all(np.isfinite(world))
        assert world[:, :2] == pytest.approx(np.array(self._tpl_footprint(pos, pitch, yaw, hfov)), abs=1e-6)


class TestSolveAtNonZeroYaw:
    """``solve_camera_dlt`` has to recover the pose it was handed.

    The decomposition inverts the rotation composition by hand, so a solve is
    the only thing that catches the two drifting apart.
    """

    _GRID = [(-5.0, -5.0, 0.0), (5.0, -5.0, 0.0), (5.0, 5.0, 0.0), (-5.0, 5.0, 0.0)]

    @pytest.mark.parametrize(
        ("pos", "pitch", "yaw", "roll", "fov"),
        [
            ((0.0, -11.0, 6.0), -22.0, 0.0, 0.0, 60.0),  # front of house
            ((0.0, 11.0, 6.0), -30.0, 180.0, 0.0, 60.0),  # upstage, facing downstage
            ((11.0, 0.0, 6.0), -30.0, -90.0, 0.0, 60.0),  # box boom, stage left
            ((-11.0, 0.0, 6.0), -30.0, 90.0, 0.0, 60.0),  # box boom, stage right
            ((-9.0, -9.0, 7.0), -28.0, 45.0, 3.0, 70.0),  # oblique, canted
            ((8.0, -8.0, 5.0), -25.0, -38.0, -6.0, 55.0),
        ],
    )
    def test_solve_recovers_the_projected_pose(
        self,
        pos: tuple[float, float, float],
        pitch: float,
        yaw: float,
        roll: float,
        fov: float,
    ) -> None:
        canvas_w, canvas_h = 1920.0, 1080.0
        params = np.array([*pos, pitch, yaw, roll, fov], dtype=np.float64)
        world = np.array(self._GRID, dtype=np.float64)
        screen = project_points(params, world, canvas_w, canvas_h)
        assert np.all(np.isfinite(screen)), "fixture camera cannot see its own grid"

        solved = solve_camera_dlt(self._GRID, [tuple(pt) for pt in screen], canvas_w, canvas_h)

        assert solved is not None
        assert (solved.pos_x, solved.pos_y, solved.pos_z) == pytest.approx(pos, abs=0.01)
        assert solved.pitch == pytest.approx(pitch, abs=0.05)
        assert solved.roll == pytest.approx(roll, abs=0.05)
        assert solved.fov == pytest.approx(fov, abs=0.05)
        # Yaw is an angle: ±180 name the same heading.
        assert math.cos(math.radians(solved.yaw - yaw)) == pytest.approx(1.0, abs=1e-5)

    @pytest.mark.parametrize("pitch", [-90.0, -89.96])
    @pytest.mark.parametrize("roll", [0.0, 30.0, -45.0])
    def test_overhead_camera_keeps_its_roll(self, pitch: float, roll: float) -> None:
        """Straight down, bearing and roll stop being separable.

        Aimed at the floor there is no heading left to measure – panning the
        head and rolling the lens do the same thing – so the pan is pinned and
        the operator's lens rotation is what survives. Without that the two
        freedoms split arbitrarily and a plumb overhead camera comes back
        canted. Both a bit-exact −90 and a camera merely near the pole must
        land here, so the behaviour is a neighbourhood and not one float.
        """
        canvas_w, canvas_h = 1920.0, 1080.0
        params = np.array([0.0, 0.0, 12.0, pitch, 0.0, roll, 60.0], dtype=np.float64)
        world = np.array(self._GRID, dtype=np.float64)
        screen = project_points(params, world, canvas_w, canvas_h)

        solved = decompose_homography(
            compute_homography(world[:, :2], screen),
            canvas_w,
            canvas_h,
            0.0,
        )

        assert solved is not None
        assert solved.pitch < -88.9  # aimed at the floor
        assert solved.roll == pytest.approx(roll, abs=0.05)
        assert solved.yaw == pytest.approx(0.0, abs=1e-9)
        # Signed zero reaches config.toml and the form field verbatim.
        assert math.copysign(1.0, solved.yaw) > 0.0, "yaw came back as negative zero"

    @pytest.mark.parametrize("pitch", [-90.0, -89.6])
    def test_plumb_overhead_rig_is_refused_rather_than_approximated(self, pitch: float) -> None:
        """A camera hung dead overhead is rejected, not silently tilted.

        The solved pitch is clamped one degree off vertical, which shifts the
        corners further than the reprojection gate allows, so the wizard
        answers "Invalid perspective" instead of accepting a pose that does not
        reproduce what the operator pinned. Rigs from −89.5° up solve normally.
        """
        canvas_w, canvas_h = 1920.0, 1080.0
        params = np.array([0.0, 0.0, 12.0, pitch, 0.0, 0.0, 60.0], dtype=np.float64)
        world = np.array(self._GRID, dtype=np.float64)
        screen = project_points(params, world, canvas_w, canvas_h)

        assert solve_camera_dlt(self._GRID, [tuple(pt) for pt in screen], canvas_w, canvas_h) is None
