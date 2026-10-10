# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for the setup wizard – API endpoints and configuration defaults.

Covers /api/wizard/project, /api/wizard/unproject, /api/wizard/solve,
the /wizard page rendering, and the updated camera/grid default values.
"""

from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.parse
import urllib.request

import numpy as np
import pytest

import openfollow.web.discovery as discovery_module
from openfollow.configuration import CameraConfig, GridConfig
from openfollow.scene.solver import apply_overlay_distortion, project_points, solve_camera_dlt
from openfollow.web.server import ConfigWebServer
from tests._ports import live_on_free_port

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


@pytest.fixture()
def live_server(tmp_path, monkeypatch):
    monkeypatch.setattr(discovery_module.BeaconSender, "start", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconSender, "stop", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconReceiver, "start", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconReceiver, "stop", lambda self: None)

    config_path = tmp_path / "config.toml"
    with live_on_free_port(
        lambda port: ConfigWebServer(
            config_path=str(config_path),
            host="127.0.0.1",
            port=port,
            system_name="WizardTest",
        )
    ) as (server, base):
        yield server, base


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


def _wizard_step(body: str, key: str) -> str:
    """One step's markup, up to whichever step follows it (Lens is optional)."""
    start = body.index(f'id="wizard-step-{key}"')
    end = body.find('<div class="wizard-content', start)
    return body[start : end if end != -1 else len(body)]


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
        # Stage convention: +X is stage left, so DSL/USL are at +hw and DSR/USR
        # at -hw (matching openfollow/web/routes.py and scripts/gen_stage_svg.py).
        corners = np.array(
            [
                [grid.x_offset + hw, grid.y_offset - hd, grid.z_offset],  # DSL (stage left)
                [grid.x_offset - hw, grid.y_offset - hd, grid.z_offset],  # DSR (stage right)
                [grid.x_offset - hw, grid.y_offset + hd, grid.z_offset],  # USR (stage right)
                [grid.x_offset + hw, grid.y_offset + hd, grid.z_offset],  # USL (stage left)
            ]
        )
        params = np.array([cam.pos_x, cam.pos_y, cam.pos_z, cam.pitch, cam.yaw, cam.roll, cam.fov])
        projected = project_points(params, corners, 1920.0, 1080.0)
        expected = np.array(
            [
                [1628.0, 732.7],  # DSL (stage left → image right)
                [292.0, 732.7],  # DSR (stage right → image left)
                [498.4, 465.7],  # USR
                [1421.6, 465.7],  # USL
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
        assert '<div id="wizard-video-saved" class="notice success" role="status" style="display:none">' in body

    def test_save_and_next_advances_only_after_save_resolves(self, live_server) -> None:
        # Save & Next must not advance while the old source is still active: the
        # onclick chains wizardGo(3) onto the save promise's .then, and
        # saveWizardVideoSource returns the fetch so the chain awaits it.
        _, base = live_server
        status, body = _get(base, "/wizard")
        assert status == 200
        assert "saveWizardVideoSource().then(function(){ wizardNext(); })" in body
        assert "saveWizardVideoSource(); wizardNext()" not in body  # fire-and-forget pattern gone
        assert "return fetch('/section/video_source'" in body

    def test_video_source_step_reports_the_last_failure(self, live_server, monkeypatch) -> None:
        server, base = live_server
        video = {
            "failure": "unreachable",
            "failure_text": "Nothing answered at 192.0.2.10:554.",
            "failure_action": "Check the camera is powered.",
            "error_message": "Could not open resource.",
        }
        monkeypatch.setattr(server, "get_runtime_stats", lambda: {"video": video})
        status, body = _get(base, "/wizard")
        assert status == 200

        step = _wizard_step(body, "video")
        assert "Nothing answered at 192.0.2.10:554." in step
        assert "Check the camera is powered." in step
        assert "Could not open resource." not in step
        assert step.index('<div class="notice error"') < step.index('id="wizard-video-source-type"')

    def test_video_source_step_polls_for_a_failure_while_healthy(self, live_server) -> None:
        _, base = live_server
        status, body = _get(base, "/wizard")
        assert status == 200

        step = _wizard_step(body, "video")
        assert '<div class="notice error"' not in step
        poll = re.search(r'<div id="video-source-failure"[^>]*>', step)
        assert poll
        assert 'hx-get="/section/video_source/failure"' in poll.group(0)

    def test_reference_mapping_offers_fine_adjust_once_there_is_a_snapshot(self, live_server) -> None:
        """Like Corner Pinning: off until the snapshot and the projection are in,
        and the page opens on the full image."""
        _, base = live_server
        status, body = _get(base, "/wizard")
        assert status == 200

        step = _wizard_step(body, "ref")
        toggle = re.search(r'<button[^>]*id="coarse-zoom-toggle"[^>]*>([^<]*)</button>', step)
        assert toggle
        assert toggle.group(1) == "Fine adjust"
        assert "disabled" in toggle.group(0)
        assert 'onclick="toggleCoarseZoomMode()"' in toggle.group(0)
        assert re.search(r'<div id="coarse-zoom-view"[^>]*style="display:none;"', step)
        assert re.search(r'<svg id="coarse-zoom-svg"[^>]*tabindex="0"', step)

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

    def test_fine_zoom_view_has_bowed_edge_wiring(self, live_server) -> None:
        """The fine-adjust zoom view must curve its edges to match the lens
        (same bowed boundary the full overlay / HUD draws), not draw straight
        chords between corners."""
        _, base = live_server
        status, body = _get(base, "/wizard")
        assert status == 200
        # The bow is computed client-side from the pin positions via the
        # distortion mirror of scene/solver.py, so it never needs a server round
        # trip and the helpers must be present.
        assert "fineBowedEdges" in body
        assert "function buildBowedEdges(" in body
        assert "function wizApplyDistortion(" in body
        assert "function wizInvertDistortion(" in body
        assert "function wizBowEdge(" in body

    def test_manual_corner_move_keeps_distortion_bow(self, live_server) -> None:
        """Dragging a corner must NOT snap the preview back to straight edges.

        ``updateFineQuad`` runs on every manual corner move (drag, zoom-drag,
        arrow nudge). It must rebuild the bowed edges from the moved pins, never
        null them out – the regression where a corner move dropped the curve.
        """
        _, base = live_server
        status, body = _get(base, "/wizard")
        assert status == 200
        # Isolate the updateFineQuad body so the assertion is about the move path.
        start = body.index("function updateFineQuad(")
        end = body.index("\n  }", start)
        fn = body[start:end]
        assert "buildBowedEdges()" in fn  # rebuilds from the moved pins
        assert "fineBowedEdges = null" not in fn  # never drops the curve on move

    def test_review_step_does_not_double_project_overlay(self, live_server) -> None:
        """Review must not flash a straight, lens-free grid before the bow lands.

        The review overlay is drawn once by loadSnapshot -> projectAndOverlay ->
        updateAllOverlays (live form values + the server's bowed outline).
        ``populateReview`` must only fill the text summary; a second projection
        there raced that path and briefly painted a straight quad – the flash the
        operator saw on entering Review.
        """
        _, base = live_server
        status, body = _get(base, "/wizard")
        assert status == 200
        start = body.index("function populateReview(")
        end = body.index("\n  }", start)
        fn = body[start:end]
        assert "/api/wizard/project" not in fn  # no second projection fetch
        assert "review-quad" not in fn  # overlay drawing is delegated

    def test_overlay_bow_gated_on_server_outline(self, live_server) -> None:
        """The fine-zoom bow must follow the server's bowed outline.

        The server omits ``outline`` when a grid edge projects behind the camera,
        and the main quad falls back to a straight 4-corner quad. ``updateAllOverlays``
        must gate ``fineBowedEdges`` on that same ``data.outline`` signal, otherwise
        the corner zoom boxes curve while the full quad stays straight – the preview
        disagreeing with itself in the behind-camera fallback.
        """
        _, base = live_server
        status, body = _get(base, "/wizard")
        assert status == 200
        start = body.index("function updateAllOverlays(")
        end = body.index("\n  }", start)
        fn = body[start:end]
        assert "fineBowedEdges = (data.outline" in fn  # built only with a server outline
        assert "fineBowedEdges = buildBowedEdges();" not in fn  # not unconditional

    def test_manual_corner_move_does_not_re_enable_dropped_bow(self, live_server) -> None:
        """A drag must refresh an active bow, never re-enable a dropped one.

        When the behind-camera fallback leaves ``fineBowedEdges`` null,
        ``updateFineQuad`` must not rebuild it from the pins – that would curve the
        fine view while the full quad is straight. The rebuild is guarded on
        ``fineBowedEdges`` already being set.
        """
        _, base = live_server
        status, body = _get(base, "/wizard")
        assert status == 200
        start = body.index("function updateFineQuad(")
        end = body.index("\n  }", start)
        fn = body[start:end]
        assert "if (fineBowedEdges) fineBowedEdges = buildBowedEdges()" in fn

    def test_corner_pinning_and_review_show_the_grid_lines(self, live_server) -> None:
        """Corner Pinning and Review draw the grid's own lines inside the quad, under the corners."""
        _, base = live_server
        status, body = _get(base, "/wizard")
        assert status == 200
        fine = body.index('id="fine-quad"')
        assert fine < body.index('<g id="fine-grid"></g>') < body.index('<g id="fine-corners"></g>')
        review = body.index('id="review-quad"')
        assert review < body.index('<g id="review-grid"></g>') < body.index('<g id="review-corners"></g>')
        start = body.index("function updateAllOverlays(")
        fn = body[start : body.index("\n  }", start)]
        assert "renderGridLines('fine-grid', c);" in fn
        assert "renderGridLines('review-grid', c);" in fn

    def test_the_grid_follows_a_moved_corner(self, live_server) -> None:
        """A drag, a nudge and the snap after a solve all run updateFineQuad, which redraws the grid from the pins."""
        _, base = live_server
        status, body = _get(base, "/wizard")
        assert status == 200
        start = body.index("function updateFineQuad(")
        fn = body[start : body.index("\n  }", start)]
        assert "renderGridLines('fine-grid', cornerPositions);" in fn

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

    def test_stage_left_corners_project_to_image_right(self, live_server) -> None:
        """Stage-left corners (DSL/USL) land on the right of a front-of-house image.

        PSN ``+X`` is stage left, and a centred front-of-house camera shows the
        audience's view, where stage left is on the right (audience right). So
        the stage-left corners must project to a larger image-x than their
        stage-right counterparts. This pins the convention the wizard labels and
        DLT solve depend on (stage left / audience right), not the projection math.
        """
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
        assert c["DSL"][0] > c["DSR"][0], "DSL (stage left) must be right of DSR (stage right)"
        assert c["USL"][0] > c["USR"][0], "USL (stage left) must be right of USR (stage right)"
        # Downstage corners sit lower in the image (larger y) than upstage ones.
        assert c["DSL"][1] > c["USL"][1], "DSL (downstage) must be below USL (upstage)"
        assert c["DSR"][1] > c["USR"][1], "DSR (downstage) must be below USR (upstage)"

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

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), 1e170, 20_000.0])
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
        assert "saveError.show(reviewBox, await saveError.fromResponse(entry.response)" in body, (
            "applyAndFinish() must show why a config POST failed on the Review step."
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

    def test_camera_step_draws_vertical_fov_footprint(self) -> None:
        """The Camera Position step draws the vertical FOV as a ground footprint.

        The earlier illustration only drew the horizontal FOV (a single width
        line at the centre sight distance), so the operator could not tell
        whether the camera covered the full depth of the grid. The footprint is
        the view frustum intersected with the grid plane: a shaded quad plus the
        four frustum edges, with the vertical FOV derived from the horizontal
        one on a 16:9 frame.
        """
        src = self._wizard_tpl()
        # Footprint quad + four frustum edges.
        assert 'id="cp-fov-area"' in src
        for edge in ("cp-fov-bl", "cp-fov-br", "cp-fov-tl", "cp-fov-tr"):
            assert f'id="{edge}"' in src, f"missing frustum edge {edge}"
        # The horizontal-only markup must be gone so it can't silently return.
        for old in ("cp-fov-left", "cp-fov-right", "cp-fov-line"):
            assert old not in src, f"stale horizontal-only FOV element {old}"
        # VFOV is derived from HFOV on a 16:9 frame, not read independently.
        assert "9 / 16" in src, "vertical FOV must follow from the 16:9 aspect ratio"
        assert "var vfov" in src
        # The FOV readout reports both axes (degree sign + H and + V).
        assert "\\u00b0H" in src and "\\u00b0V" in src


_CAM_LENS = {**_CAM, "lens_k1": -0.2, "lens_k2": 0.02}


@integration
class TestWizardLensDistortion:
    """The corner-pinning preview bows to match the lens, and the solve
    undistorts the pinned corners before the pinhole DLT."""

    def test_project_warps_corners_when_lens_set(self, live_server) -> None:
        _, base = live_server
        body = {"grid": _GRID, "image_width": IMG_W, "image_height": IMG_H}
        _, plain = _post_json(base, "/api/wizard/project", {**body, "camera": _CAM})
        _, warped = _post_json(base, "/api/wizard/project", {**body, "camera": _CAM_LENS})
        # A non-zero k bows the projected overlay, so the corners shift.
        moved = any(
            abs(plain["corners"][n][0] - warped["corners"][n][0]) > 1.0
            or abs(plain["corners"][n][1] - warped["corners"][n][1]) > 1.0
            for n in ("DSL", "DSR", "USR", "USL")
        )
        assert moved

    def test_project_zero_lens_matches_no_lens_key(self, live_server) -> None:
        _, base = live_server
        body = {"grid": _GRID, "image_width": IMG_W, "image_height": IMG_H}
        _, no_key = _post_json(base, "/api/wizard/project", {**body, "camera": _CAM})
        _, zero = _post_json(base, "/api/wizard/project", {**body, "camera": {**_CAM, "lens_k1": 0.0, "lens_k2": 0.0}})
        for n in ("DSL", "DSR", "USR", "USL"):
            assert zero["corners"][n] == pytest.approx(no_key["corners"][n], abs=1e-6)

    def test_project_returns_bowed_outline_when_lens_set(self, live_server) -> None:
        # The wizard quad must bow like the HUD grid: the boundary is returned
        # subdivided (many points) and distorted, not just the 4 corners.
        _, base = live_server
        body = {"grid": _GRID, "image_width": IMG_W, "image_height": IMG_H}
        _, warped = _post_json(base, "/api/wizard/project", {**body, "camera": _CAM_LENS})
        assert "outline" in warped
        assert len(warped["outline"]) > 4  # subdivided edges, not a 4-corner quad
        # The outline starts at the first (DSL) corner.
        assert warped["outline"][0] == pytest.approx(warped["corners"]["DSL"], abs=1e-6)
        # A mid point on the first edge bows off the straight DSL->DSR chord.
        dsl, dsr = warped["corners"]["DSL"], warped["corners"]["DSR"]
        mid = warped["outline"][len(warped["outline"]) // 16]
        cross = (mid[0] - dsl[0]) * (dsr[1] - dsl[1]) - (mid[1] - dsl[1]) * (dsr[0] - dsl[0])
        assert abs(cross) > 1.0

    def test_project_outline_is_four_corners_when_lens_zero(self, live_server) -> None:
        _, base = live_server
        body = {"grid": _GRID, "image_width": IMG_W, "image_height": IMG_H}
        _, plain = _post_json(base, "/api/wizard/project", {**body, "camera": _CAM})
        # No distortion -> boundary is just the four corners (a straight quad).
        assert len(plain["outline"]) == 4

    def test_unproject_undistorts_input_when_lens_set(self, live_server) -> None:
        _, base = live_server
        params = _cam_params()
        screen = project_points(params, np.array([[2.0, 3.0, 0.0]]), IMG_W, IMG_H)
        body = {"screen_points": screen.tolist(), "image_width": IMG_W, "image_height": IMG_H, "plane_z": 0.0}
        _, plain = _post_json(base, "/api/wizard/unproject", {**body, "camera": _CAM})
        _, warped = _post_json(base, "/api/wizard/unproject", {**body, "camera": _CAM_LENS})
        # Undistorting the same screen point before unprojecting moves the world hit.
        assert plain["world_points"][0] != pytest.approx(warped["world_points"][0], abs=1e-3)

    def test_solve_undistorts_pinned_corners_round_trip(self, live_server) -> None:
        # Pin corners on a *distorted* video: project pinhole, then warp forward.
        _, base = live_server
        params = _cam_params()
        world = _world_corners()
        pinhole_screen = project_points(params, world, IMG_W, IMG_H)
        k1, k2 = _CAM_LENS["lens_k1"], _CAM_LENS["lens_k2"]
        distorted_screen = apply_overlay_distortion(pinhole_screen, IMG_W, IMG_H, k1, k2)

        status, data = _post_json(
            base,
            "/api/wizard/solve",
            {
                "world_corners": world.tolist(),
                "screen_corners": distorted_screen.tolist(),
                "image_width": IMG_W,
                "image_height": IMG_H,
                "camera": {"lens_k1": k1, "lens_k2": k2},
            },
        )
        assert status == 200
        # Undistort-then-solve recovers the true pinhole camera.
        cam = data["camera"]
        assert cam["pos_y"] == pytest.approx(_CAM["pos_y"], abs=0.5)
        assert cam["pos_z"] == pytest.approx(_CAM["pos_z"], abs=0.5)
        assert cam["fov"] == pytest.approx(_CAM["fov"], abs=2.0)
        # Reprojected corners are re-distorted, so they land on the pinned positions.
        rp = data["reprojected_corners"]
        for i in range(4):
            assert rp[i][0] == pytest.approx(distorted_screen[i][0], abs=2.0)
            assert rp[i][1] == pytest.approx(distorted_screen[i][1], abs=2.0)


@unit
class TestWizardLensCoeffs:
    """Direct coverage of the lens-coefficient extraction helper's edge cases."""

    def test_non_dict_camera_yields_zero(self) -> None:
        from openfollow.web.routes import _wizard_lens_coeffs

        assert _wizard_lens_coeffs(None) == (0.0, 0.0)
        assert _wizard_lens_coeffs("not a dict") == (0.0, 0.0)

    def test_non_numeric_and_non_finite_fall_back_to_zero(self) -> None:
        from openfollow.web.routes import _wizard_lens_coeffs

        assert _wizard_lens_coeffs({"lens_k1": "abc"}) == (0.0, 0.0)
        assert _wizard_lens_coeffs({"lens_k1": float("inf"), "lens_k2": float("nan")}) == (0.0, 0.0)

    def test_a_folding_pair_falls_back_to_pinhole(self) -> None:
        from openfollow.web.routes import _wizard_lens_coeffs

        assert _wizard_lens_coeffs({"lens_k1": 5.0, "lens_k2": -5.0}) == (0.0, 0.0)
        assert _wizard_lens_coeffs({"lens_k1": -0.4, "lens_k2": -0.2}) == (0.0, 0.0)
        assert _wizard_lens_coeffs({"lens_k1": -0.1, "lens_k2": 0.02}) == pytest.approx((-0.1, 0.02))
        # A wide lens outside the old +-0.4 / +-0.2 box passes through unclamped.
        assert _wizard_lens_coeffs({"lens_k1": -0.47, "lens_k2": 0.25}) == pytest.approx((-0.47, 0.25))


# ---------------------------------------------------------------------------
# Lens step: lines that are straight in reality -> k1 / k2
# ---------------------------------------------------------------------------


def _lens_station(tmp_path, monkeypatch, *, developer_mode: bool = False):
    """A station with experimental features on, so the wizard renders the Lens step."""
    from openfollow.configuration import AppConfig, save_config

    monkeypatch.setattr(discovery_module.BeaconSender, "start", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconSender, "stop", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconReceiver, "start", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconReceiver, "stop", lambda self: None)

    # Its own file, so a test holding several fixtures keeps the plain station plain.
    config_path = tmp_path / ("config_lens_dev.toml" if developer_mode else "config_lens.toml")
    cfg = AppConfig()
    cfg.ui.show_experimental_features = True
    cfg.ui.developer_mode = developer_mode
    cfg.camera.lens_k1 = -0.21
    cfg.camera.lens_k2 = 0.03
    save_config(cfg, str(config_path))
    return live_on_free_port(
        lambda port: ConfigWebServer(
            config_path=str(config_path),
            host="127.0.0.1",
            port=port,
            system_name="WizardLensTest",
        )
    )


@pytest.fixture()
def live_server_lens(tmp_path, monkeypatch):
    with _lens_station(tmp_path, monkeypatch) as (server, base):
        yield server, base


@pytest.fixture()
def live_server_lens_dev(tmp_path, monkeypatch):
    """The Lens station in developer mode, which adds the edge view."""
    with _lens_station(tmp_path, monkeypatch, developer_mode=True) as (server, base):
        yield server, base


def _stripe_image(w: int = 1920, h: int = 1080) -> np.ndarray:
    rng = np.random.default_rng(0)
    img = np.full((h, w), 40.0)
    yy, xx = np.mgrid[0:h, 0:w]
    img[np.abs(yy - (600.0 + 0.02 * xx)) <= 7] = 200.0
    return np.clip(img + rng.normal(0.0, 4.0, img.shape), 0, 255)


def _snap_band(img: np.ndarray, p0, p1) -> dict:
    """The band the wizard sends: the luma along the chord, rectified."""
    from tests._lens_band import band_payload, cut_band

    return band_payload(cut_band(img, p0, p1))


def _warped_line(p0, p1, k1: float, k2: float, n: int = 5) -> list[list[float]]:
    from openfollow.scene.solver import invert_overlay_distortion

    ends = invert_overlay_distortion(np.array([p0, p1], dtype=np.float64), IMG_W, IMG_H, k1, k2)
    t = np.linspace(0.0, 1.0, n)[:, None]
    seen = apply_overlay_distortion(ends[0] + t * (ends[1] - ends[0]), IMG_W, IMG_H, k1, k2)
    return seen.tolist()


_LENS_EDGE_LINES = [
    ((52.0, 12.0), (1868.0, 20.0)),
    ((52.0, 1068.0), (1868.0, 1062.0)),
    ((12.0, 52.0), (18.0, 1028.0)),
    ((1908.0, 52.0), (1900.0, 1028.0)),
    ((120.0, 100.0), (1800.0, 1000.0)),
]


@integration
class TestWizardLensSnapEndpoint:
    def test_snaps_the_five_points_onto_the_edge(self, live_server) -> None:
        _, base = live_server
        img = _stripe_image()
        top = -7.0
        p0 = (200.0, 600.0 + 0.02 * 200.0 + top + 5.0)
        p1 = (1700.0, 600.0 + 0.02 * 1700.0 + top - 4.0)
        status, data = _post_json(
            base,
            "/api/wizard/lens/snap",
            {"image_width": 1920, "image_height": 1080, "p0": p0, "p1": p1, "band": _snap_band(img, p0, p1)},
        )
        assert status == 200
        assert len(data["points"]) == 5
        for p in data["points"]:
            assert p["snapped"] is True
            assert abs(p["y"] - (600.0 + 0.02 * p["x"] + top)) < 0.75

    def test_a_covered_point_is_reported_unsnapped(self, live_server) -> None:
        _, base = live_server
        img = _stripe_image()
        img[:, 900:1000] = 40.0
        p0 = (200.0, 600.0 + 0.02 * 200.0 - 7.0)
        p1 = (1700.0, 600.0 + 0.02 * 1700.0 - 7.0)
        _, data = _post_json(
            base,
            "/api/wizard/lens/snap",
            {"image_width": 1920, "image_height": 1080, "p0": p0, "p1": p1, "band": _snap_band(img, p0, p1)},
        )
        assert [p["snapped"] for p in data["points"]] == [True, True, False, True, True]

    def _good_body(self) -> dict:
        img = _stripe_image()
        p0, p1 = (200.0, 596.0), (1700.0, 627.0)
        return {"image_width": 1920, "image_height": 1080, "p0": p0, "p1": p1, "band": _snap_band(img, p0, p1)}

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda b: b.pop("band"),
            lambda b: b.__setitem__("band", "wide"),
            lambda b: b["band"].update(data="not base64!"),
            lambda b: b["band"].update(rows=b["band"]["rows"] + 2, half=b["band"]["half"] + 1),
            lambda b: b["band"].update(rows=b["band"]["rows"] + 1),
            lambda b: b["band"].update(step=0),
            lambda b: b["band"].update(step=99),
            lambda b: b["band"].update(half=8, rows=17),
            lambda b: b["band"].update(half=10_000, rows=20_001),
            lambda b: b["band"].update(cols=1),
            lambda b: b["band"].update(cols=100_000),
            lambda b: b["band"].update(cols=b["band"]["cols"] + 1),
            lambda b: b["band"].update(data=12345),
            lambda b: b["band"].pop("step"),
            lambda b: b.update(p1=[b["p1"][0] - 400.0, b["p1"][1]]),
            lambda b: b.update(p0=[1.0]),
            lambda b: b.update(p0=["a", "b"]),
            lambda b: b.update(p0=[float("nan"), 1.0]),
            lambda b: b.update(p1=b["p0"]),
            lambda b: b.update(image_width=0),
            lambda b: b.update(image_height="tall"),
            lambda b: b.update(image_width=1e170, image_height=1e170),
            lambda b: b.update(image_width=20_000),
            lambda b: b.update(image_width=True),
            lambda b: b["band"].update(step=float("inf")),
            lambda b: b["band"].update(half=float("-inf")),
            lambda b: b["band"].update(step=True),
            lambda b: b["band"].update(step=1.5),
            lambda b: b.update(p0=["200", "596"]),
        ],
        ids=[
            "no-band",
            "band-not-an-object",
            "bad-base64",
            "size-mismatch",
            "rows-not-two-half-plus-one",
            "zero-step",
            "huge-step",
            "half-too-small",
            "half-too-large",
            "one-column",
            "too-many-columns",
            "columns-do-not-match-data",
            "data-not-a-string",
            "missing-step",
            "band-cut-for-another-line",
            "p0-not-a-pair",
            "p0-non-numeric",
            "p0-nan",
            "zero-length-line",
            "zero-canvas",
            "string-canvas",
            "astronomical-canvas",
            "canvas-too-wide",
            "boolean-canvas",
            "infinite-step",
            "negative-infinite-half",
            "boolean-step",
            "fractional-step",
            "string-coordinates",
        ],
    )
    def test_malformed_body_returns_400(self, live_server, mutate) -> None:
        _, base = live_server
        body = self._good_body()
        mutate(body)
        status, data = _post_json(base, "/api/wizard/lens/snap", body)
        assert status == 400, data
        assert "error" in data

    def test_non_object_body_returns_400(self, live_server) -> None:
        _, base = live_server
        status, _ = _post_json(base, "/api/wizard/lens/snap", [1, 2, 3])  # type: ignore[arg-type]
        assert status == 400

    def test_invalid_json_returns_400(self, live_server) -> None:
        _, base = live_server
        req = urllib.request.Request(
            f"{base}/api/wizard/lens/snap",
            data=b"{not json",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req, timeout=5)
        assert exc.value.code == 400


@integration
class TestWizardLensEdgesEndpoint:
    def _body(self, with_map: bool = False) -> dict:
        from openfollow.scene.edge_chains import edge_map_scale
        from tests._lens_band import edges_payload, scaled_luma

        scale = edge_map_scale(1920)
        return edges_payload(scaled_luma(_stripe_image(), scale), scale, 1920, 1080, with_map=with_map)

    def test_suggests_the_stripe_with_five_sample_points(self, live_server) -> None:
        _, base = live_server
        status, data = _post_json(base, "/api/wizard/lens/edges", self._body())
        assert status == 200
        assert data["edges"] is None
        assert len(data["candidates"]) >= 1
        best = data["candidates"][0]
        assert len(best["samples"]) == 5 and len(best["points"]) >= 2
        for x, y in best["samples"]:
            assert abs(abs(y - (600.0 + 0.02 * x)) - 7.0) < 3.0
        assert best["length"] > 1500.0 and best["strength"] > 8.0

    def test_the_developer_map_is_returned_on_request(self, live_server) -> None:
        _, base = live_server
        status, data = _post_json(base, "/api/wizard/lens/edges", self._body(with_map=True))
        assert status == 200
        edges = data["edges"]
        assert (edges["width"], edges["height"]) == (960, 540)
        assert len(base64.b64decode(edges["data"])) == 960 * 540

    @pytest.mark.parametrize("with_map", ["false", 1, "yes"])
    def test_only_true_asks_for_the_developer_map(self, live_server, with_map) -> None:
        _, base = live_server
        status, data = _post_json(base, "/api/wizard/lens/edges", {**self._body(), "with_map": with_map})
        assert status == 200
        assert data["edges"] is None

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda b: b.pop("data"),
            lambda b: b.update(data=123),
            lambda b: b.update(data="not base64!"),
            lambda b: b.update(data=b["data"][:-8]),
            lambda b: b.update(scale=0),
            lambda b: b.update(scale=99),
            lambda b: b.update(scale=1),
            lambda b: b.update(width=b["width"] + 1),
            lambda b: b.update(height=b["height"] - 1),
            lambda b: b.update(scale=1, width=2000, height=2000, image_width=2000, image_height=2000),
            lambda b: b.update(image_width=0),
            lambda b: b.update(image_height="tall"),
            lambda b: b.pop("scale"),
            lambda b: b.update(scale=float("inf")),
            lambda b: b.update(scale=2.5),
            lambda b: b.update(scale=1, width=1920, height=1080, data=base64.b64encode(bytes(1920 * 1080)).decode()),
        ],
        ids=[
            "no-data",
            "data-not-a-string",
            "bad-base64",
            "data-too-short",
            "zero-scale",
            "huge-scale",
            "scale-does-not-match-size",
            "width-off-by-one",
            "height-off-by-one",
            "too-large",
            "zero-canvas",
            "string-canvas",
            "missing-scale",
            "infinite-scale",
            "fractional-scale",
            "unscaled-snapshot",
        ],
    )
    def test_malformed_body_returns_400(self, live_server, mutate) -> None:
        _, base = live_server
        body = self._body()
        mutate(body)
        status, data = _post_json(base, "/api/wizard/lens/edges", body)
        assert status == 400, data
        assert "error" in data

    def test_non_object_body_returns_400(self, live_server) -> None:
        _, base = live_server
        status, _ = _post_json(base, "/api/wizard/lens/edges", [1, 2, 3])  # type: ignore[arg-type]
        assert status == 400

    def test_invalid_json_returns_400(self, live_server) -> None:
        _, base = live_server
        req = urllib.request.Request(
            f"{base}/api/wizard/lens/edges",
            data=b"{not json",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req, timeout=5)
        assert exc.value.code == 400


@integration
class TestWizardLensFitEndpoint:
    def test_fit_recovers_the_pair_and_describes_each_line(self, live_server) -> None:
        _, base = live_server
        k1, k2 = -0.3, 0.05
        lines = [{"points": _warped_line(a, b, k1, k2)} for a, b in _LENS_EDGE_LINES]
        status, data = _post_json(
            base, "/api/wizard/lens/fit", {"image_width": IMG_W, "image_height": IMG_H, "lines": lines}
        )
        assert status == 200
        assert data["k1"] == pytest.approx(k1, abs=5e-3)
        assert data["k2"] == pytest.approx(k2, abs=5e-3)
        assert data["k2_fitted"] is True
        assert data["rating"] in ("low", "medium", "okay", "good", "excellent")
        assert isinstance(data["hint"], str)
        assert data["uncertainty_px"] is not None
        assert len(data["lines"]) == len(lines)
        for line in data["lines"]:
            assert line["misfit"] is False
            assert line["rms_px"] < 0.1
            assert len(line["curve"]) == 24

    def test_bare_point_lists_are_accepted(self, live_server) -> None:
        _, base = live_server
        lines = [_warped_line(a, b, -0.2, 0.0) for a, b in _LENS_EDGE_LINES[:3]]
        status, data = _post_json(
            base, "/api/wizard/lens/fit", {"image_width": IMG_W, "image_height": IMG_H, "lines": lines}
        )
        assert status == 200
        assert data["k1"] == pytest.approx(-0.2, abs=0.02)

    def test_zero_coefficients_fit_straight_lines_as_pinhole(self, live_server) -> None:
        _, base = live_server
        lines = [_warped_line(a, b, 0.0, 0.0) for a, b in _LENS_EDGE_LINES]
        _, data = _post_json(
            base, "/api/wizard/lens/fit", {"image_width": IMG_W, "image_height": IMG_H, "lines": lines}
        )
        assert data["k1"] == pytest.approx(0.0, abs=2e-3)
        assert data["k2"] == pytest.approx(0.0, abs=2e-3)
        # The predicted curves of a pinhole fit are the straight lines themselves.
        for line, (a, b) in zip(data["lines"], _LENS_EDGE_LINES, strict=True):
            first, last = line["curve"][0], line["curve"][-1]
            assert first == pytest.approx(list(a), abs=0.05)
            assert last == pytest.approx(list(b), abs=0.05)

    @pytest.mark.parametrize(
        "body",
        [
            {"image_width": IMG_W, "image_height": IMG_H, "lines": []},
            {"image_width": IMG_W, "image_height": IMG_H, "lines": "none"},
            {"image_width": IMG_W, "image_height": IMG_H},
            {"image_width": IMG_W, "image_height": IMG_H, "lines": [{"points": [[0, 0], [100, 0]]}]},
            {"image_width": IMG_W, "image_height": IMG_H, "lines": [{"points": [[0, 0], ["a", 0], [100, 0]]}]},
            {"image_width": IMG_W, "image_height": IMG_H, "lines": [{"points": [[0, 0], [1], [100, 0]]}]},
            {"image_width": IMG_W, "image_height": IMG_H, "lines": [{"points": "abc"}]},
            {"image_width": IMG_W, "image_height": IMG_H, "lines": [{"points": [[0, 0], [1, 1], [2, 2]]}]},
            {"image_width": 0, "image_height": IMG_H, "lines": [{"points": [[0, 0], [50, 0], [100, 0]]}]},
            {"image_width": IMG_W, "image_height": float("nan"), "lines": [{"points": [[0, 0], [50, 0], [100, 0]]}]},
            {
                "image_width": IMG_W,
                "image_height": IMG_H,
                "lines": [{"points": [[0, 0], [50, float("inf")], [100, 0]]}],
            },
            {"image_width": IMG_W, "image_height": IMG_H, "lines": [{"points": [[0, 2], [50, 0], [100, 0]]}] * 33},
            {"image_width": IMG_W, "image_height": IMG_H, "lines": [{"points": [[x, 0] for x in range(0, 170, 10)]}]},
            {"image_width": IMG_W, "image_height": IMG_H, "lines": [{"points": ["12", [50, 0], [100, 0]]}]},
            {"image_width": IMG_W, "image_height": IMG_H, "lines": [{"points": [[0, 0, 0], [50, 0, 0], [100, 0, 0]]}]},
            {"image_width": IMG_W, "image_height": IMG_H, "lines": [{"points": [[True, 0], [50, 0], [100, 0]]}]},
            {"image_width": 1e170, "image_height": 1e170, "lines": [{"points": [[0, 0], [50, 0], [100, 0]]}]},
            {"image_width": IMG_W, "image_height": IMG_H, "lines": [{"points": [[10**400, 0], [50, 0], [100, 0]]}]},
        ],
        ids=[
            "no-lines",
            "lines-not-a-list",
            "lines-missing",
            "two-points",
            "non-numeric-point",
            "short-point",
            "points-not-a-list",
            "degenerate-line",
            "zero-canvas",
            "nan-canvas",
            "non-finite-point",
            "too-many-lines",
            "too-many-points",
            "string-point",
            "three-coordinates",
            "boolean-coordinate",
            "astronomical-canvas",
            "integer-past-float-range",
        ],
    )
    def test_malformed_body_returns_400(self, live_server, body: dict) -> None:
        _, base = live_server
        status, data = _post_json(base, "/api/wizard/lens/fit", body)
        assert status == 400, data
        assert "error" in data

    def test_non_object_body_returns_400(self, live_server) -> None:
        _, base = live_server
        status, _ = _post_json(base, "/api/wizard/lens/fit", [[0, 0]])  # type: ignore[arg-type]
        assert status == 400

    @pytest.mark.parametrize(
        "raw",
        [b"[not json", b'{"image_width": 1' + b"0" * 5000 + b"}", b"[" * 100_000 + b"]" * 100_000],
        ids=["not-json", "integer-past-the-digit-limit", "nested-past-the-recursion-limit"],
    )
    def test_invalid_json_returns_400(self, live_server, raw: bytes) -> None:
        _, base = live_server
        req = urllib.request.Request(
            f"{base}/api/wizard/lens/fit",
            data=raw,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req, timeout=5)
        assert exc.value.code == 400


@integration
class TestLensChangeAfterPinning:
    """The pins are undistorted with whatever pair is current, so a new pair solves again from them."""

    def test_solving_again_with_the_measured_pair_recovers_the_camera(self, live_server) -> None:
        _, base = live_server
        k1, k2 = -0.3, 0.05
        world = _world_corners()
        pins = apply_overlay_distortion(project_points(_cam_params(), world, IMG_W, IMG_H), IMG_W, IMG_H, k1, k2)
        body = {
            "world_corners": world.tolist(),
            "screen_corners": pins.tolist(),
            "image_width": IMG_W,
            "image_height": IMG_H,
        }
        _, before = _post_json(base, "/api/wizard/solve", {**body, "camera": {"lens_k1": 0.0, "lens_k2": 0.0}})
        _, after = _post_json(base, "/api/wizard/solve", {**body, "camera": {"lens_k1": k1, "lens_k2": k2}})
        # Without the lens the pins solve to the wrong pose; with it they come back to the truth.
        assert after["camera"]["fov"] == pytest.approx(_CAM["fov"], abs=1.0)
        assert after["camera"]["pos_z"] == pytest.approx(_CAM["pos_z"], abs=0.3)
        assert abs(before["camera"]["fov"] - after["camera"]["fov"]) > 1.0

    def test_a_wide_lens_pair_outside_the_old_box_is_honoured(self, live_server) -> None:
        _, base = live_server
        k1, k2 = -0.47, 0.25
        world = _world_corners()
        pins = apply_overlay_distortion(project_points(_cam_params(), world, IMG_W, IMG_H), IMG_W, IMG_H, k1, k2)
        body = {
            "world_corners": world.tolist(),
            "screen_corners": pins.tolist(),
            "image_width": IMG_W,
            "image_height": IMG_H,
        }
        status, data = _post_json(base, "/api/wizard/solve", {**body, "camera": {"lens_k1": k1, "lens_k2": k2}})
        assert status == 200
        assert data["camera"]["fov"] == pytest.approx(_CAM["fov"], abs=1.0)


@integration
class TestWizardLensStepPage:
    def test_without_the_toggle_the_wizard_keeps_seven_steps(self, live_server) -> None:
        _, base = live_server
        _, body = live_server and _get(base, "/wizard")
        assert body.count('class="wizard-step-btn') == 7
        assert 'id="wizard-step-lens"' not in body
        assert "4. Camera Position" in body
        # The stored pair rides along in hidden inputs so Apply keeps it.
        assert '<input type="hidden" id="wiz_lens_k1"' in body
        assert '<input type="hidden" id="wiz_lens_k2"' in body
        assert "lens: " not in body.split("window.WIZ = {")[1].split("};")[0]

    def test_with_the_toggle_the_lens_step_sits_after_video_source(self, live_server_lens) -> None:
        _, base = live_server_lens
        status, body = _get(base, "/wizard")
        assert status == 200
        assert body.count('class="wizard-step-btn') == 8
        assert "3. Video Source" in body and "4. Lens" in body and "5. Camera Position" in body
        assert "8. Review" in body
        step_map = body.split("window.WIZ = {")[1].split("};")[0]
        assert "lens: 3" in step_map and "camera: 4" in step_map and "review: 7" in step_map
        assert 'id="wizard-step-lens"' in body
        assert 'id="wiz_lens_k1"' in body and 'type="hidden" id="wiz_lens_k1"' not in body
        # The sliders carry the stored pair and sit under the result as Fine-tune.
        assert 'id="wiz_lens_k1" step="0.005"' in body
        assert 'value="-0.21"' in body and 'value="0.03"' in body
        assert "Fine-tune" in body
        # No step is referred to by number anywhere, since the Lens step shifts the numbering.
        assert re.search(r"Step \d", body) is None

    def test_review_repeats_the_rating_and_the_values_only_with_the_step(self, live_server, live_server_lens) -> None:
        _, plain = _get(live_server[1], "/wizard")
        _, lens = _get(live_server_lens[1], "/wizard")
        assert 'id="review-lens-rating"' in lens and 'id="review-lens-caution"' in lens
        assert 'id="review-lens-rating"' not in plain and 'id="review-lens-caution"' not in plain

    def test_the_operator_never_sees_a_coefficient_name(self, live_server_lens) -> None:
        """The pair is Barrel / fisheye and Edge fit everywhere an operator reads: never k1 or k2."""
        import re

        _, base = live_server_lens
        _, page = _get(base, "/wizard")
        # The script keeps the coefficient names; what the page shows and says must not.
        wizard = re.sub(r"<script.*?</script>", "", page, flags=re.S)
        assert "Barrel / fisheye" in wizard and "Edge fit" in wizard
        for leak in ("(k1)", "(k2)", "Lens k1", "Lens k2", "k1 or k2", "k2 not"):
            assert leak not in wizard, leak
        for text in (
            "' · Barrel / fisheye '",
            "' · Edge fit '",
            "'not measured'",
            "Bring either lens value closer to 0",
        ):
            assert text in page, text
        _, camera = _get(base, "/section/camera")
        assert "<label>Barrel / fisheye</label>" in camera and "<label>Edge fit</label>" in camera
        assert "(k1)" not in camera and "(k2)" not in camera

    def test_the_edge_view_is_a_developer_control(self, live_server_lens, live_server_lens_dev) -> None:
        _, plain = _get(live_server_lens[1], "/wizard")
        _, dev = _get(live_server_lens_dev[1], "/wizard")
        assert 'id="lens-show-edges"' in dev and "Show edges" in dev and "var LENS_DEV = true;" in dev
        assert 'id="lens-edges"' in dev
        assert 'id="lens-show-edges"' not in plain and "var LENS_DEV = false;" in plain


@unit
class TestWizardLensTemplate:
    def _src(self) -> str:
        from pathlib import Path

        return (Path(__file__).resolve().parent.parent / "openfollow" / "web" / "templates" / "wizard.tpl").read_text(
            encoding="utf-8"
        )

    def test_no_script_or_button_carries_a_step_number(self) -> None:
        import re

        src = self._src()
        assert "window.WIZ = {" in src and "window.WIZ_STEPS" in src
        assert not re.search(r"wizard-step-\d", src)
        # Only the generated nav buttons call wizardGo with an index; every other button steps relatively.
        assert re.findall(r'onclick="wizardGo\((\d+|\{\{_i\}\})\)"', src) == ["{{_i}}"]
        assert "wizardNext()" in src and "wizardPrev()" in src
        assert "_stepKey" in src

    def test_band_columns_mirror_the_server(self) -> None:
        # One column per step pixels of the chord, both ends included: edge_snap.band_columns.
        assert "Math.ceil(len / step) + 1" in self._src()

    def test_suggestions_are_requested_for_the_lens_step_and_tapped_into_lines(self) -> None:
        import re

        src = self._src()
        assert "'/api/wizard/lens/edges'" in src
        assert "if (currentStep === WIZ.lens) lensRequestEdges();" in src
        assert 'id="lens-candidates"' in src
        down = re.search(r"overlay\.addEventListener\('pointerdown', function\(e\) \{(.*?)\n    \}\);", src, re.S)
        assert down is not None and "lensAddCandidate(+candidate.dataset.candidate)" in down.group(1)
        # A deleted or cleared line gives its suggestion back.
        for name in ("lensDeleteSelectedLine", "lensClearLines"):
            body = re.search(r"window\." + name + r" = function\(\) \{(.*?)\n  \};\n", src, re.S)
            assert body is not None and "lensReleaseCandidate" in body.group(1), name

    def test_a_middle_point_without_an_edge_starts_switched_off(self) -> None:
        src = self._src()
        assert "for (var j = 1; j <= 3; j++) line.mids[j - 1].on = line.snapped[j];" in src
        assert "starts switched off" in src

    def test_lines_persist_in_the_session_and_clear_on_a_resolution_change(self) -> None:
        src = self._src()
        assert "state._lensLines = lensLines;" in src
        assert "Array.isArray(state._lensLines)" in src
        assert "lensImageSize[0] !== imageWidth || lensImageSize[1] !== imageHeight" in src
        assert "resolution changed" in src

    def test_a_click_on_a_point_keeps_the_keys_on_it(self) -> None:
        """Rebuilding the handles on pointer-up must not drop the focus the keys need."""
        import re

        src = self._src()
        render = re.search(r"function renderLens\(\) \{(.*?)\n  \}\n", src, re.S)
        assert render is not None
        body = render.group(1)
        assert body.index("g.contains(document.activeElement)") < body.index("g.innerHTML = ''")
        assert body.rstrip().endswith("if (hadFocus) lensFocusSelected();")
        focus = re.search(r"function lensFocusSelected\(\) \{(.*?)\n  \}\n", src, re.S)
        assert focus is not None and "focus({ preventScroll: true })" in focus.group(1)
        # The handler that selects a point by pointer relies on the same helper.
        down = re.search(r"overlay\.addEventListener\('pointerdown', function\(e\) \{(.*?)\n    \}\);", src, re.S)
        assert down is not None and "lensFocusSelected();" in down.group(1)

    def test_a_lens_change_solves_again_from_the_pins(self) -> None:
        import re

        src = self._src()
        solve = re.search(r"function solveFromCorners\(\) \{(.*?)\n  \}\n", src, re.S)
        coarse = re.search(r"function applyCoarseOffset\(.*?\) \{(.*?)\n  \}\n", src, re.S)
        assert solve and "notePinnedCorners(screenCorners)" in solve.group(1)
        assert coarse and "notePinnedCorners(screenCorners)" in coarse.group(1)
        changed = re.search(r"function onLensCoeffChanged\(\) \{(.*?)\n  \}\n", src, re.S)
        assert changed and "solveFromPinnedCorners" in changed.group(1)
        assert "if (!pinnedCorners" in changed.group(1)
        # Both the fit and the sliders go through it.
        fit = re.search(r"function runLensFit\(\) \{(.*?)\n  \}\n", src, re.S)
        assert fit and "onLensCoeffChanged()" in fit.group(1)
        slider = re.search(r"window\.onWizardLensInput = function\(.*?\) \{(.*?)\n  \};\n", src, re.S)
        assert slider and "onLensCoeffChanged()" in slider.group(1)
        # Reset drops the pins, and Review's caution follows the pins.
        reset = re.search(r"window\.resetCornerPinning = function\(\) \{(.*?)\n  \};\n", src, re.S)
        assert reset and "pinnedCorners = null" in reset.group(1)
        assert "return !pinnedCorners && (" in src
        assert "'review-lens-caution').style.display = lensChangedSinceSolve()" in src

    def test_middle_points_move_across_the_line_only(self) -> None:
        src = self._src()
        # Dragging a middle point sets its perpendicular offset; the keyboard keeps only the nudge's perpendicular part.
        assert "function lensSetMidFromPos(line, j, pos)" in src
        assert "line.mids[j - 1].off += dx * f.nx + dy * f.ny;" in src
        for key in ("ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Delete", "Escape"):
            assert f"'{key}'" in src
        assert "e.key === ' '" in src

    def test_fit_runs_debounced_after_a_release_and_writes_the_sliders(self) -> None:
        src = self._src()
        assert "setTimeout(runLensFit, LENS_FIT_DEBOUNCE_MS)" in src
        assert "'/api/wizard/lens/fit'" in src and "'/api/wizard/lens/snap'" in src
        assert "wizWriteLensCoeff('wiz_lens_k1', Number(res.data.k1).toFixed(4))" in src
        assert "Point off" in src and "Delete line" in src and "Clear lines" in src

    def test_a_folding_pair_blocks_apply_with_a_reason(self) -> None:
        import re

        src = self._src()
        body = re.search(r"window\.applyAndFinish\s*=\s*function\s*\([^)]*\)\s*\{(.*?)\n  \};", src, re.S)
        assert body and "if (!wizLensPairValid())" in body.group(1)
        assert "folds the overlay" in body.group(1)

    def test_rating_levels_follow_the_status_language(self) -> None:
        src = self._src()
        levels = "{ low: 'caution', medium: 'caution', okay: 'info', good: 'success', excellent: 'success' }"
        assert "LENS_RATING_LEVEL = " + levels in src
        assert "LENS_RATING_SEGMENTS = { low: 1, medium: 2, okay: 3, good: 4, excellent: 5 }" in src
        assert "This line doesn't fit the others – is it really straight?" in src
