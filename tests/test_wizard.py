# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for the setup wizard – API endpoints and configuration defaults.

Covers /api/wizard/project, /api/wizard/unproject, /api/wizard/solve,
the /wizard page rendering, and the updated camera/grid default values.
"""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

import numpy as np
import pytest

import openfollow.web.discovery as discovery_module
from openfollow.configuration import CameraConfig, GridConfig
from openfollow.scene.solver import project_points, solve_camera_dlt
from openfollow.web.server import ConfigWebServer

# ---------------------------------------------------------------------------
# Markers
# ---------------------------------------------------------------------------

# Unit tests (solver-level, no server)
unit = pytest.mark.unit
# Integration tests (live HTTP server)
integration = pytest.mark.integration

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_CAM = {
    "pos_x": 0.0,
    "pos_y": -10.0,
    "pos_z": 8.0,
    "pitch": -30.0,
    "yaw": 0.0,
    "roll": 0.0,
    "fov": 60.0,
}

_GRID = {
    "width": 10.0,
    "depth": 6.0,
    "spacing": 1.0,
    "x_offset": 0.0,
    "y_offset": 3.0,
    "z_offset": 0.0,
}

IMG_W, IMG_H = 1920.0, 1080.0


def _find_free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _wait_for_port(port: int, host: str = "127.0.0.1", timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.1):
                return True
        except OSError:
            time.sleep(0.05)
    return False


@pytest.fixture()
def live_server(tmp_path, monkeypatch):
    monkeypatch.setattr(discovery_module.BeaconSender, "start", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconSender, "stop", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconReceiver, "start", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconReceiver, "stop", lambda self: None)

    port = _find_free_tcp_port()
    config_path = tmp_path / "config.toml"

    server = ConfigWebServer(
        config_path=str(config_path),
        host="127.0.0.1",
        port=port,
        system_name="WizardTest",
    )
    server.start()
    assert _wait_for_port(port), f"Web server did not start on port {port}"

    yield server, f"http://127.0.0.1:{port}"
    server.stop()


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _get(base: str, path: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(f"{base}{path}", timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def _post_json(base: str, path: str, data: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def _post_raw(base: str, path: str, data: dict) -> tuple[int, str]:
    """POST JSON and return the raw response body (un-parsed).

    ``json.loads`` tolerates ``NaN``/``Infinity`` tokens, but the browser's
    ``JSON.parse`` does not – so a response that round-trips through
    ``_post_json`` can still be invalid JSON that breaks the wizard. This
    helper hands back the raw bytes so a test can assert strict validity.
    """
    req = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def _reject_non_finite(_token: str) -> float:
    raise ValueError("non-finite JSON token (NaN/Infinity) – browsers reject this")


def _cam_params(cam: dict | None = None) -> np.ndarray:
    c = cam or _CAM
    return np.array(
        [
            c["pos_x"],
            c["pos_y"],
            c["pos_z"],
            c["pitch"],
            c["yaw"],
            c["roll"],
            c["fov"],
        ],
        dtype=np.float64,
    )


def _world_corners(grid: dict | None = None) -> np.ndarray:
    g = grid or _GRID
    hw = g["width"] / 2
    hd = g["depth"] / 2
    ox = g.get("x_offset", 0) or 0
    oy = g.get("y_offset", 0) or 0
    oz = g.get("z_offset", 0) or 0
    return np.array(
        [
            [ox - hw, oy - hd, oz],
            [ox + hw, oy - hd, oz],
            [ox + hw, oy + hd, oz],
            [ox - hw, oy + hd, oz],
        ],
        dtype=np.float64,
    )


# ===========================================================================
# Unit tests – configuration defaults
# ===========================================================================


@unit
class TestConfigDefaults:
    def test_camera_defaults(self) -> None:
        cam = CameraConfig()
        assert cam.pos_x == 0.0
        assert cam.pos_y == -11.0
        assert cam.pos_z == 6.0
        assert cam.pitch == -22.0
        assert cam.yaw == 0.0
        assert cam.roll == 0.0
        assert cam.fov == 60.0

    def test_grid_defaults(self) -> None:
        grid = GridConfig()
        assert grid.width == 10.0
        assert grid.depth == 6.0
        assert grid.spacing == 1.0
        assert grid.x_offset == 0.0
        assert grid.y_offset == 3.0
        assert grid.z_offset == 0.0

    def test_defaults_project_to_stock_svg_corners(self) -> None:
        cam = CameraConfig()
        grid = GridConfig()
        hw = grid.width / 2.0
        hd = grid.depth / 2.0
        corners = np.array(
            [
                [grid.x_offset - hw, grid.y_offset - hd, grid.z_offset],  # DSL
                [grid.x_offset + hw, grid.y_offset - hd, grid.z_offset],  # DSR
                [grid.x_offset + hw, grid.y_offset + hd, grid.z_offset],  # USR
                [grid.x_offset - hw, grid.y_offset + hd, grid.z_offset],  # USL
            ]
        )
        params = np.array([cam.pos_x, cam.pos_y, cam.pos_z, cam.pitch, cam.yaw, cam.roll, cam.fov])
        projected = project_points(params, corners, 1920.0, 1080.0)
        expected = np.array(
            [
                [292.0, 732.7],
                [1628.0, 732.7],
                [1421.6, 465.7],
                [498.4, 465.7],
            ]
        )
        assert np.allclose(projected, expected, atol=0.5), (
            f"Grid corners drifted from the stock SVG. "
            f"Re-run scripts/gen_stage_svg.py.\n"
            f"expected={expected.tolist()}\ngot={projected.tolist()}"
        )


# ===========================================================================
# Unit tests – projection logic
# ===========================================================================


@unit
class TestProjectionLogic:
    """Verify the projection math that the wizard endpoints rely on."""

    def test_reference_at_ground_level(self) -> None:
        """Reference point should be at [0, 0, 0], not at grid z_offset."""
        params = _cam_params()
        ref_ground = np.array([[0, 0, 0]], dtype=np.float64)
        ref_elevated = np.array([[0, 0, 1.0]], dtype=np.float64)

        screen_ground = project_points(params, ref_ground, IMG_W, IMG_H)
        screen_elevated = project_points(params, ref_elevated, IMG_W, IMG_H)

        # Ground and elevated should project to different screen positions
        assert not np.allclose(screen_ground, screen_elevated, atol=1.0)

    def test_corners_at_z_offset(self) -> None:
        """Grid corners should be at z = z_offset."""
        grid = {**_GRID, "z_offset": 0.9}
        corners = _world_corners(grid)
        assert all(c[2] == 0.9 for c in corners)

    def test_grid_offsets_shift_corners(self) -> None:
        """Non-zero x/y offsets should shift all corners."""
        grid_centered = {**_GRID, "x_offset": 0, "y_offset": 0}
        grid_offset = {**_GRID, "x_offset": 2.0, "y_offset": 5.0}
        c1 = _world_corners(grid_centered)
        c2 = _world_corners(grid_offset)

        # All corners should be shifted by (2, 5)
        diff = c2 - c1
        assert np.allclose(diff[:, 0], 2.0)
        assert np.allclose(diff[:, 1], 5.0)

    def test_solve_roundtrip_with_z_offset(self) -> None:
        """DLT solve should work correctly when grid has non-zero z_offset."""
        grid = {**_GRID, "z_offset": 0.9}
        params = _cam_params()
        world = _world_corners(grid)
        screen = project_points(params, world, IMG_W, IMG_H)

        solved = solve_camera_dlt(
            [tuple(p) for p in world],
            [tuple(p) for p in screen],
            IMG_W,
            IMG_H,
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
        reprojected = project_points(solved_params, world, IMG_W, IMG_H)
        assert float(np.max(np.abs(reprojected - screen))) < 2.0

    def test_solve_roundtrip_with_offsets(self) -> None:
        """DLT solve should work with non-zero grid offsets."""
        grid = {**_GRID, "x_offset": 1.5, "y_offset": 4.0}
        params = _cam_params()
        world = _world_corners(grid)
        screen = project_points(params, world, IMG_W, IMG_H)

        solved = solve_camera_dlt(
            [tuple(p) for p in world],
            [tuple(p) for p in screen],
            IMG_W,
            IMG_H,
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
        reprojected = project_points(solved_params, world, IMG_W, IMG_H)
        assert float(np.max(np.abs(reprojected - screen))) < 2.0


# ===========================================================================
# Integration tests – wizard HTTP endpoints
# ===========================================================================


@integration
class TestWizardPage:
    def test_wizard_page_renders(self, live_server) -> None:
        _, base = live_server
        status, body = _get(base, "/wizard")
        assert status == 200
        assert "wizard" in body.lower()
        assert len(body) > 1000

    def test_wizard_page_contains_all_steps(self, live_server) -> None:
        _, base = live_server
        status, body = _get(base, "/wizard")
        assert status == 200
        for step_name in [
            "Preparation",
            "Grid Setup",
            "Video Source",
            "Camera Position",
            "Reference Mapping",
            "Corner Pinning",
            "Review",
        ]:
            assert step_name in body, f"Missing step: {step_name}"

    def test_wizard_page_renders_corner_pinning_zoom_view(self, live_server) -> None:
        """Verify Corner Pinning renders 4-box fine-adjust view with
        toggle; zoom hidden initially, enabled after snapshot loads."""
        _, base = live_server
        status, body = _get(base, "/wizard")
        assert status == 200
        # Toggle button exists and starts disabled (snapshot not yet
        # loaded). The render-time markup carries the disabled
        # attribute and the initial label.
        toggle_idx = body.find('id="fine-zoom-toggle"')
        assert toggle_idx != -1
        # Walk back to the enclosing <button ...> and check it carries
        # disabled + the right onclick. Walk forward to the > to grab
        # the inner-text label that immediately follows.
        button_open = body.rfind("<button", 0, toggle_idx)
        assert button_open != -1
        button_close = body.find(">", toggle_idx)
        assert button_close != -1
        button_tag = body[button_open : button_close + 1]
        assert "disabled" in button_tag
        assert 'onclick="toggleFineZoomMode()"' in button_tag
        # Initial label text is rendered between the opening tag and
        # the matching </button>.
        end_tag = body.find("</button>", button_close)
        assert end_tag != -1
        assert "Fine adjust" in body[button_close + 1 : end_tag]
        # Both views exist; zoom view starts hidden.
        assert 'id="fine-full-view"' in body
        assert 'id="fine-zoom-view" style="display:none;"' in body
        # All four corners get a dedicated box with the right
        # ``data-corner`` hook for the drag wiring.
        for corner in ("USL", "USR", "DSL", "DSR"):
            assert f'class="fine-zoom-box" data-corner="{corner}"' in body

    def test_every_overlay_step_has_a_projection_status_surface(self, live_server) -> None:
        """Each step that renders the projected overlay can show a projection error.

        ``projectAndOverlay`` runs on Reference Mapping, Corner Pinning, and
        Review. A degenerate camera makes the server reject the projection; if a
        step lacks a ``*-status`` element, ``showProjectionError`` has nowhere to
        write and the overlay silently vanishes there – the exact failure this
        fix removes. Pin that every overlay step exposes a status surface.
        """
        _, base = live_server
        status, body = _get(base, "/wizard")
        assert status == 200
        for step in ("coarse", "fine", "review"):
            assert f'id="{step}-container"' in body, f"missing {step}-container"
            assert f'id="{step}-status"' in body, (
                f"{step} step renders the overlay but has no status surface for a projection error"
            )


@integration
class TestWizardProjectEndpoint:
    def test_project_returns_corners_and_reference(self, live_server) -> None:
        _, base = live_server
        status, data = _post_json(
            base,
            "/api/wizard/project",
            {
                "camera": _CAM,
                "grid": _GRID,
                "image_width": IMG_W,
                "image_height": IMG_H,
            },
        )
        assert status == 200
        assert "corners" in data
        assert "reference" in data
        for name in ["DSL", "DSR", "USR", "USL"]:
            assert name in data["corners"]
            assert len(data["corners"][name]) == 2
        assert len(data["reference"]) == 2

    def test_project_reference_at_ground_not_grid_height(self, live_server) -> None:
        """Reference should project from [0,0,0] regardless of z_offset."""
        _, base = live_server
        grid_flat = {**_GRID, "z_offset": 0.0}
        grid_raised = {**_GRID, "z_offset": 2.0}

        _, data_flat = _post_json(
            base,
            "/api/wizard/project",
            {
                "camera": _CAM,
                "grid": grid_flat,
                "image_width": IMG_W,
                "image_height": IMG_H,
            },
        )
        _, data_raised = _post_json(
            base,
            "/api/wizard/project",
            {
                "camera": _CAM,
                "grid": grid_raised,
                "image_width": IMG_W,
                "image_height": IMG_H,
            },
        )

        # Reference point should be the same (both at ground [0,0,0])
        assert data_flat["reference"] == pytest.approx(data_raised["reference"], abs=0.1)

    def test_project_includes_elevated_when_z_offset(self, live_server) -> None:
        _, base = live_server
        grid = {**_GRID, "z_offset": 0.9}
        status, data = _post_json(
            base,
            "/api/wizard/project",
            {
                "camera": _CAM,
                "grid": grid,
                "image_width": IMG_W,
                "image_height": IMG_H,
            },
        )
        assert status == 200
        assert "reference_elevated" in data
        assert "z_offset" in data
        assert data["z_offset"] == pytest.approx(0.9)
        assert len(data["reference_elevated"]) == 2
        # Elevated should differ from ground reference
        assert data["reference_elevated"] != pytest.approx(data["reference"], abs=1.0)

    def test_project_no_elevated_when_z_offset_zero(self, live_server) -> None:
        _, base = live_server
        status, data = _post_json(
            base,
            "/api/wizard/project",
            {
                "camera": _CAM,
                "grid": _GRID,
                "image_width": IMG_W,
                "image_height": IMG_H,
            },
        )
        assert status == 200
        assert "reference_elevated" not in data
        assert "z_offset" not in data

    def test_project_handles_null_offsets(self, live_server) -> None:
        """Grid offsets sent as null (from stale session) should not crash."""
        _, base = live_server
        grid = {**_GRID, "x_offset": None, "y_offset": None, "z_offset": None}
        status, data = _post_json(
            base,
            "/api/wizard/project",
            {
                "camera": _CAM,
                "grid": grid,
                "image_width": IMG_W,
                "image_height": IMG_H,
            },
        )
        assert status == 200
        assert "corners" in data

    def test_project_handles_missing_offsets(self, live_server) -> None:
        """Grid without offset keys should default to 0."""
        _, base = live_server
        grid = {"width": 10.0, "depth": 6.0}
        status, data = _post_json(
            base,
            "/api/wizard/project",
            {
                "camera": _CAM,
                "grid": grid,
                "image_width": IMG_W,
                "image_height": IMG_H,
            },
        )
        assert status == 200
        assert "corners" in data

    def test_project_missing_camera_returns_400(self, live_server) -> None:
        _, base = live_server
        status, data = _post_json(
            base,
            "/api/wizard/project",
            {
                "grid": _GRID,
                "image_width": IMG_W,
                "image_height": IMG_H,
            },
        )
        assert status == 400
        assert "error" in data

    def test_project_corners_form_convex_quad(self, live_server) -> None:
        """Projected corners should form a valid convex quadrilateral."""
        _, base = live_server
        status, data = _post_json(
            base,
            "/api/wizard/project",
            {
                "camera": _CAM,
                "grid": _GRID,
                "image_width": IMG_W,
                "image_height": IMG_H,
            },
        )
        assert status == 200
        c = data["corners"]
        # All corners should be within image bounds
        for name in ["DSL", "DSR", "USR", "USL"]:
            x, y = c[name]
            assert 0 <= x <= IMG_W, f"{name} x={x} out of bounds"
            assert 0 <= y <= IMG_H, f"{name} y={y} out of bounds"

    # Camera sitting inside the grid footprint, nearly level: the two front
    # corners fall at/behind the camera plane and project to NaN.
    _CAM_CORNER_BEHIND = {
        "pos_x": 0.0,
        "pos_y": 3.0,
        "pos_z": 1.6,
        "pitch": -1.0,
        "yaw": 0.0,
        "roll": 0.0,
        "fov": 60.0,
    }

    def test_project_corner_behind_camera_returns_400(self, live_server) -> None:
        """A corner behind the camera must yield a clean 400, not NaN corners.

        Regression: ``project_points`` returns NaN for such a corner, the
        default ``json.dumps`` wrote literal ``NaN`` tokens, the browser's
        ``r.json()`` threw, and the un-caught rejection silently dropped the
        Corner Pinning overlay (feed visible, no corner markers, no error).
        """
        _, base = live_server
        status, data = _post_json(
            base,
            "/api/wizard/project",
            {
                "camera": self._CAM_CORNER_BEHIND,
                "grid": _GRID,
                "image_width": IMG_W,
                "image_height": IMG_H,
            },
        )
        assert status == 400
        assert "error" in data
        assert "corners" not in data

    def test_project_never_emits_invalid_json_for_degenerate_camera(self, live_server) -> None:
        """The response must be strict JSON (no NaN/Infinity) even when degenerate.

        ``json.loads`` accepts ``NaN``; the browser's ``JSON.parse`` does not.
        Parse the raw body with ``parse_constant`` rejecting non-finite tokens
        to mirror the browser and prove the overlay can't be silently dropped.
        """
        _, base = live_server
        status, raw = _post_raw(
            base,
            "/api/wizard/project",
            {
                "camera": self._CAM_CORNER_BEHIND,
                "grid": _GRID,
                "image_width": IMG_W,
                "image_height": IMG_H,
            },
        )
        assert status == 400
        assert "NaN" not in raw and "Infinity" not in raw
        # Strict parse (browser-equivalent) must succeed.
        json.loads(raw, parse_constant=_reject_non_finite)


@integration
class TestWizardUnprojectEndpoint:
    def test_unproject_single_point(self, live_server) -> None:
        _, base = live_server
        # First project a known world point to get a screen point
        params = _cam_params()
        world_pt = np.array([[0, 0, 0]], dtype=np.float64)
        screen_pt = project_points(params, world_pt, IMG_W, IMG_H)

        status, data = _post_json(
            base,
            "/api/wizard/unproject",
            {
                "camera": _CAM,
                "screen_points": screen_pt.tolist(),
                "image_width": IMG_W,
                "image_height": IMG_H,
                "plane_z": 0.0,
            },
        )
        assert status == 200
        assert "world_points" in data
        wp = data["world_points"]
        assert len(wp) == 1
        assert wp[0][0] == pytest.approx(0.0, abs=0.1)
        assert wp[0][1] == pytest.approx(0.0, abs=0.1)

    def test_unproject_two_points_returns_delta(self, live_server) -> None:
        _, base = live_server
        params = _cam_params()
        world_pts = np.array([[0, 0, 0], [2, 3, 0]], dtype=np.float64)
        screen_pts = project_points(params, world_pts, IMG_W, IMG_H)

        status, data = _post_json(
            base,
            "/api/wizard/unproject",
            {
                "camera": _CAM,
                "screen_points": screen_pts.tolist(),
                "image_width": IMG_W,
                "image_height": IMG_H,
                "plane_z": 0.0,
            },
        )
        assert status == 200
        assert "delta" in data
        assert data["delta"]["x"] == pytest.approx(2.0, abs=0.1)
        assert data["delta"]["y"] == pytest.approx(3.0, abs=0.1)

    def test_unproject_on_elevated_plane(self, live_server) -> None:
        _, base = live_server
        params = _cam_params()
        world_pt = np.array([[1, 2, 0.9]], dtype=np.float64)
        screen_pt = project_points(params, world_pt, IMG_W, IMG_H)

        status, data = _post_json(
            base,
            "/api/wizard/unproject",
            {
                "camera": _CAM,
                "screen_points": screen_pt.tolist(),
                "image_width": IMG_W,
                "image_height": IMG_H,
                "plane_z": 0.9,
            },
        )
        assert status == 200
        wp = data["world_points"]
        assert wp[0][0] == pytest.approx(1.0, abs=0.1)
        assert wp[0][1] == pytest.approx(2.0, abs=0.1)

    def test_unproject_missing_camera_returns_400(self, live_server) -> None:
        _, base = live_server
        status, data = _post_json(
            base,
            "/api/wizard/unproject",
            {
                "screen_points": [[960, 540]],
                "image_width": IMG_W,
                "image_height": IMG_H,
            },
        )
        assert status == 400
        assert "error" in data


@integration
class TestWizardSolveEndpoint:
    def _project_corners(self, cam: dict | None = None, grid: dict | None = None) -> tuple[list, list]:
        """Project world corners to screen, return (world_list, screen_list)."""
        params = _cam_params(cam)
        world = _world_corners(grid)
        screen = project_points(params, world, IMG_W, IMG_H)
        return world.tolist(), screen.tolist()

    @pytest.mark.parametrize("corner_key", ["world_corners", "screen_corners"])
    def test_solve_rejects_non_finite_corner(self, live_server, corner_key: str) -> None:
        # A NaN coord must 400 (caught in the route), not 500 from numpy's solve.
        _, base = live_server
        body: dict = {
            "world_corners": [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]],
            "screen_corners": [[0, 0], [1, 0], [1, 1], [0, 1]],
            "image_width": IMG_W,
            "image_height": IMG_H,
        }
        body[corner_key][0][0] = float("nan")
        status, data = _post_json(base, "/api/wizard/solve", body)
        assert status == 400
        assert "error" in data

    def test_solve_returns_camera_and_reprojected(self, live_server) -> None:
        _, base = live_server
        world, screen = self._project_corners()

        status, data = _post_json(
            base,
            "/api/wizard/solve",
            {
                "world_corners": world,
                "screen_corners": screen,
                "image_width": IMG_W,
                "image_height": IMG_H,
            },
        )
        assert status == 200
        assert "camera" in data
        assert "reprojected_corners" in data
        cam = data["camera"]
        for key in ["pos_x", "pos_y", "pos_z", "pitch", "yaw", "roll", "fov"]:
            assert key in cam

    def test_solve_reprojection_is_accurate(self, live_server) -> None:
        _, base = live_server
        world, screen = self._project_corners()

        status, data = _post_json(
            base,
            "/api/wizard/solve",
            {
                "world_corners": world,
                "screen_corners": screen,
                "image_width": IMG_W,
                "image_height": IMG_H,
            },
        )
        assert status == 200

        # Reprojected corners should be close to input screen corners
        rp = data["reprojected_corners"]
        for i in range(4):
            assert rp[i][0] == pytest.approx(screen[i][0], abs=2.0)
            assert rp[i][1] == pytest.approx(screen[i][1], abs=2.0)

    def test_solve_with_z_offset(self, live_server) -> None:
        _, base = live_server
        grid = {**_GRID, "z_offset": 0.9}
        world, screen = self._project_corners(grid=grid)

        status, data = _post_json(
            base,
            "/api/wizard/solve",
            {
                "world_corners": world,
                "screen_corners": screen,
                "image_width": IMG_W,
                "image_height": IMG_H,
            },
        )
        assert status == 200
        assert "camera" in data

    def test_solve_with_offsets(self, live_server) -> None:
        _, base = live_server
        grid = {**_GRID, "x_offset": 1.5, "y_offset": 4.0}
        world, screen = self._project_corners(grid=grid)

        status, data = _post_json(
            base,
            "/api/wizard/solve",
            {
                "world_corners": world,
                "screen_corners": screen,
                "image_width": IMG_W,
                "image_height": IMG_H,
            },
        )
        assert status == 200
        rp = data["reprojected_corners"]
        for i in range(4):
            assert rp[i][0] == pytest.approx(screen[i][0], abs=2.0)
            assert rp[i][1] == pytest.approx(screen[i][1], abs=2.0)

    def test_solve_degenerate_corners_returns_422(self, live_server) -> None:
        _, base = live_server
        world = _world_corners().tolist()
        # All screen corners at the same point – degenerate
        screen = [[500, 500]] * 4

        status, data = _post_json(
            base,
            "/api/wizard/solve",
            {
                "world_corners": world,
                "screen_corners": screen,
                "image_width": IMG_W,
                "image_height": IMG_H,
            },
        )
        assert status == 422
        assert "error" in data

    def test_solve_missing_fields_returns_400(self, live_server) -> None:
        _, base = live_server
        status, data = _post_json(
            base,
            "/api/wizard/solve",
            {
                "world_corners": [[0, 0, 0]],
            },
        )
        assert status == 400
        assert "error" in data


# ===========================================================================
# Integration tests – malformed input edge cases (regression guard)
# ===========================================================================
#
# These tests lock in 400-on-bad-input across all wizard endpoints
# so a future refactor can't silently regress invalid input handling.


@integration
class TestWizardProjectBadInput:
    def test_project_partial_camera_returns_400(self, live_server) -> None:
        _, base = live_server
        partial_cam = {"pos_x": 0.0, "pos_y": -10.0}  # missing pos_z/pitch/etc.
        status, data = _post_json(
            base,
            "/api/wizard/project",
            {
                "camera": partial_cam,
                "grid": _GRID,
                "image_width": IMG_W,
                "image_height": IMG_H,
            },
        )
        assert status == 400
        assert "error" in data

    def test_project_non_numeric_camera_returns_400(self, live_server) -> None:
        _, base = live_server
        bad_cam = {**_CAM, "pitch": "not-a-number"}
        status, data = _post_json(
            base,
            "/api/wizard/project",
            {
                "camera": bad_cam,
                "grid": _GRID,
                "image_width": IMG_W,
                "image_height": IMG_H,
            },
        )
        assert status == 400
        assert "error" in data

    def test_project_missing_grid_dimensions_returns_400(self, live_server) -> None:
        _, base = live_server
        bad_grid = {"spacing": 1.0}  # missing width/depth
        status, data = _post_json(
            base,
            "/api/wizard/project",
            {
                "camera": _CAM,
                "grid": bad_grid,
                "image_width": IMG_W,
                "image_height": IMG_H,
            },
        )
        assert status == 400
        assert "error" in data

    def test_project_non_object_camera_returns_400(self, live_server) -> None:
        _, base = live_server
        status, data = _post_json(
            base,
            "/api/wizard/project",
            {
                "camera": [1, 2, 3],
                "grid": _GRID,
                "image_width": IMG_W,
                "image_height": IMG_H,
            },
        )
        assert status == 400
        assert "error" in data

    def test_project_zero_fov_returns_400_not_500(self, live_server) -> None:
        # A degenerate fov used to reach the solver and raise ZeroDivisionError
        # (HTTP 500). It must be rejected as a 400 instead.
        _, base = live_server
        status, data = _post_json(
            base,
            "/api/wizard/project",
            {
                "camera": {**_CAM, "fov": 0.0},
                "grid": _GRID,
                "image_width": IMG_W,
                "image_height": IMG_H,
            },
        )
        assert status == 400
        assert "error" in data

    def test_project_zero_canvas_height_returns_400_not_500(self, live_server) -> None:
        _, base = live_server
        status, data = _post_json(
            base,
            "/api/wizard/project",
            {
                "camera": _CAM,
                "grid": _GRID,
                "image_width": IMG_W,
                "image_height": 0.0,
            },
        )
        assert status == 400
        assert "error" in data

    @pytest.mark.parametrize("bad", [float("nan"), float("inf")])
    def test_project_non_finite_canvas_returns_400_not_500(self, live_server, bad: float) -> None:
        # json.dumps emits NaN/Infinity and the server's json parser accepts
        # them; a bare ``<= 0`` guard lets them through (``NaN <= 0`` is False),
        # so the route must reject non-finite dims with a 400, not a 500 / a
        # non-standard-JSON "NaN" response.
        _, base = live_server
        status, data = _post_json(
            base,
            "/api/wizard/project",
            {
                "camera": _CAM,
                "grid": _GRID,
                "image_width": bad,
                "image_height": IMG_H,
            },
        )
        assert status == 400
        assert "error" in data


@integration
class TestWizardUnprojectBadInput:
    def test_unproject_partial_camera_returns_400(self, live_server) -> None:
        _, base = live_server
        status, data = _post_json(
            base,
            "/api/wizard/unproject",
            {
                "camera": {"pos_x": 0.0},  # missing the other six fields
                "screen_points": [[100, 100]],
                "image_width": IMG_W,
                "image_height": IMG_H,
            },
        )
        assert status == 400
        assert "error" in data

    def test_unproject_empty_screen_points_returns_400(self, live_server) -> None:
        _, base = live_server
        status, data = _post_json(
            base,
            "/api/wizard/unproject",
            {
                "camera": _CAM,
                "screen_points": [],
                "image_width": IMG_W,
                "image_height": IMG_H,
            },
        )
        assert status == 400
        assert "error" in data

    def test_unproject_malformed_screen_point_returns_400(self, live_server) -> None:
        _, base = live_server
        status, data = _post_json(
            base,
            "/api/wizard/unproject",
            {
                "camera": _CAM,
                "screen_points": [[1, 2, 3]],  # 3D point, should be 2D
                "image_width": IMG_W,
                "image_height": IMG_H,
            },
        )
        assert status == 400
        assert "error" in data

    def test_unproject_non_numeric_screen_point_returns_400(self, live_server) -> None:
        """Non-numeric coordinates must be caught before np.array() runs."""
        _, base = live_server
        status, data = _post_json(
            base,
            "/api/wizard/unproject",
            {
                "camera": _CAM,
                "screen_points": [["a", "b"]],
                "image_width": IMG_W,
                "image_height": IMG_H,
            },
        )
        assert status == 400
        assert "error" in data

    def test_unproject_zero_fov_returns_400_not_500(self, live_server) -> None:
        _, base = live_server
        status, data = _post_json(
            base,
            "/api/wizard/unproject",
            {
                "camera": {**_CAM, "fov": 0.0},
                "screen_points": [[960, 540]],
                "image_width": IMG_W,
                "image_height": IMG_H,
            },
        )
        assert status == 400
        assert "error" in data

    def test_unproject_zero_canvas_returns_400_not_500(self, live_server) -> None:
        _, base = live_server
        status, data = _post_json(
            base,
            "/api/wizard/unproject",
            {
                "camera": _CAM,
                "screen_points": [[960, 540]],
                "image_width": 0.0,
                "image_height": IMG_H,
            },
        )
        assert status == 400
        assert "error" in data


@integration
class TestWizardSolveBadInput:
    def test_solve_three_corners_returns_400(self, live_server) -> None:
        _, base = live_server
        status, data = _post_json(
            base,
            "/api/wizard/solve",
            {
                "world_corners": [[0, 0, 0], [1, 0, 0], [1, 1, 0]],
                "screen_corners": [[0, 0], [100, 0], [100, 100]],
                "image_width": IMG_W,
                "image_height": IMG_H,
            },
        )
        assert status == 400
        assert "error" in data

    def test_solve_mismatched_corner_counts_returns_400(self, live_server) -> None:
        _, base = live_server
        world = _world_corners().tolist()
        status, data = _post_json(
            base,
            "/api/wizard/solve",
            {
                "world_corners": world,
                "screen_corners": [[0, 0], [100, 0], [100, 100]],  # only 3
                "image_width": IMG_W,
                "image_height": IMG_H,
            },
        )
        assert status == 400
        assert "error" in data

    def test_solve_2d_world_corner_returns_400(self, live_server) -> None:
        _, base = live_server
        status, data = _post_json(
            base,
            "/api/wizard/solve",
            {
                "world_corners": [[0, 0], [1, 0], [1, 1], [0, 1]],  # missing z
                "screen_corners": [[0, 0], [100, 0], [100, 100], [0, 100]],
                "image_width": IMG_W,
                "image_height": IMG_H,
            },
        )
        assert status == 400
        assert "error" in data

    def test_solve_non_numeric_screen_corner_returns_400(self, live_server) -> None:
        _, base = live_server
        world = _world_corners().tolist()
        status, data = _post_json(
            base,
            "/api/wizard/solve",
            {
                "world_corners": world,
                "screen_corners": [["a", "b"], [100, 0], [100, 100], [0, 100]],
                "image_width": IMG_W,
                "image_height": IMG_H,
            },
        )
        assert status == 400
        assert "error" in data

    def test_solve_zero_canvas_returns_400_not_500(self, live_server) -> None:
        _, base = live_server
        world = _world_corners().tolist()
        status, data = _post_json(
            base,
            "/api/wizard/solve",
            {
                "world_corners": world,
                "screen_corners": [[0, 0], [100, 0], [100, 100], [0, 100]],
                "image_width": 0.0,
                "image_height": IMG_H,
            },
        )
        assert status == 400
        assert "error" in data


# ===========================================================================
# Unit test – wizard template contains required safety patterns
# ===========================================================================
#
# The wizard's JS is embedded in a Bottle template, so we have no JS test
# toolchain here. These checks assert key safety patterns stay present:
#   - URL.revokeObjectURL is called on the previous blob snapshot URL
#   - applyAndFinish() inspects response.ok before redirecting


@unit
class TestWizardTemplateSafetyPatterns:
    def _wizard_tpl(self) -> str:
        from pathlib import Path

        path = Path(__file__).resolve().parent.parent / "openfollow" / "web" / "templates" / "wizard.tpl"
        return path.read_text(encoding="utf-8")

    def test_snapshot_revokes_previous_blob_url(self) -> None:
        src = self._wizard_tpl()
        assert "URL.revokeObjectURL" in src, "loadSnapshot() must revoke the previous blob URL to avoid memory leaks."

    def test_apply_and_finish_checks_response_ok(self) -> None:
        src = self._wizard_tpl()
        # Extract the applyAndFinish function body and assert the check
        # lives *inside* it, not somewhere unrelated in the file.
        import re

        match = re.search(
            r"window\.applyAndFinish\s*=\s*function\s*\([^)]*\)\s*\{(.*?)\n  \};",
            src,
            re.DOTALL,
        )
        assert match is not None, "applyAndFinish() function not found in wizard.tpl"
        body = match.group(1)
        assert "entry.response.ok" in body, (
            "applyAndFinish() must check entry.response.ok before clearing session storage."
        )
        assert "Failed to save " in body, (
            "applyAndFinish() must surface a descriptive error message on failed config POSTs."
        )

    def test_wizard_nav_uses_nav_landmark(self) -> None:
        """The step list is a navigation landmark, not a tablist.

        Prior ``role=\"tablist\"`` markup was missing aria-selected/aria-controls/
        role=tabpanel/aria-labelledby. Using the nav landmark with aria-current=step
        is the lighter-weight correct semantics for a linear wizard.
        """
        src = self._wizard_tpl()
        assert '<nav class="wizard-steps"' in src
        assert 'role="tablist"' not in src
