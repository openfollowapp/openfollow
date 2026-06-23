# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for :mod:`openfollow.runtime.overlay_draw_scene`.

Covers the PSN→screen projection of the marker ball vs. the origin glyph:
a marker reported at PSN (0, 0, 0) must render at the world origin (the
Reference Point), not at the grid center, regardless of the grid's
``x_offset`` / ``y_offset`` / ``z_offset``.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from openfollow.runtime.overlay_draw_scene import (
    _MAX_GRID_LINES_PER_AXIS,
    draw_grid,
    draw_marker,
    draw_origin,
    project,
)
from openfollow.runtime.overlay_state import MarkerOverlayData, OverlayState
from tests._fake_cairo import FakeCairo, FakeRenderer

pytestmark = pytest.mark.unit


def _camera_params() -> np.ndarray:
    # Looking down-stage from a typical FOH position.
    return np.array([0.0, -6.0, 2.5, -15.0, 0.0, 0.0, 60.0], dtype=np.float64)


def _state_with_grid_offset(x_off: float, y_off: float, z_off: float) -> OverlayState:
    state = OverlayState()
    state.camera_params = _camera_params()
    state.grid_config = (10.0, 6.0, 1.0, x_off, y_off, z_off)
    state.show_ball = True
    state.show_crosshair = False
    state.show_drop_line = False
    state.show_ground_circle = False
    state.show_origin = True
    state.origin_length = 1.0
    state.origin_thickness = 3
    return state


class TestMarkerAtOriginMatchesOriginGlyph:
    """PSN (0,0,0) must project to the same point as the origin axes."""

    @pytest.mark.parametrize(
        ("x_off", "y_off", "z_off"),
        [
            (0.0, 0.0, 0.0),  # baseline: no offset
            (0.0, 3.0, 0.0),  # default: grid 3m upstage of REF
            (1.5, 3.0, 0.5),  # non-zero on all three axes
            (-2.0, 4.0, 0.0),  # REF right of grid center
        ],
    )
    def test_ball_center_equals_origin_axis_start(self, x_off: float, y_off: float, z_off: float) -> None:
        state = _state_with_grid_offset(x_off, y_off, z_off)
        marker = MarkerOverlayData(marker_id=1, x=0.0, y=0.0, z=0.0, color="#ff3333")
        w, h = 1920, 1080

        origin_cr = FakeCairo()
        draw_origin(origin_cr, state, w, h)
        # draw_origin issues one move_to per axis at the shared (0,0,0) start.
        assert origin_cr.move_tos, "origin axes should have been drawn"
        origin_x, origin_y = origin_cr.move_tos[0]

        marker_cr = FakeCairo()
        draw_marker(marker_cr, state, marker, w, h)
        assert marker_cr.arcs, "marker ball should have been drawn"
        ball_x, ball_y, _ = marker_cr.arcs[0]

        assert math.isclose(ball_x, origin_x, abs_tol=1e-6)
        assert math.isclose(ball_y, origin_y, abs_tol=1e-6)

    def test_ball_does_not_track_grid_center(self) -> None:
        """Sanity: changing the grid offset must not move a PSN(0,0,0) marker."""
        marker = MarkerOverlayData(marker_id=1, x=0.0, y=0.0, z=0.0, color="#ff3333")
        w, h = 1920, 1080

        cr_a = FakeCairo()
        draw_marker(cr_a, _state_with_grid_offset(0.0, 0.0, 0.0), marker, w, h)
        cr_b = FakeCairo()
        draw_marker(cr_b, _state_with_grid_offset(2.5, 4.0, 0.5), marker, w, h)

        ax, ay, _ = cr_a.arcs[0]
        bx, by, _ = cr_b.arcs[0]
        assert math.isclose(ax, bx, abs_tol=1e-6)
        assert math.isclose(ay, by, abs_tol=1e-6)


class TestMarkerProjectsAtItsPsnCoords:
    """A marker at arbitrary PSN coords projects exactly to those coords."""

    def test_ball_center_matches_direct_projection(self) -> None:
        state = _state_with_grid_offset(1.5, 3.0, 0.5)
        marker = MarkerOverlayData(marker_id=1, x=2.0, y=1.0, z=1.7, color="#3399ff")
        w, h = 1920, 1080

        cr = FakeCairo()
        draw_marker(cr, state, marker, w, h)
        assert cr.arcs, "marker ball should have been drawn"
        ball_x, ball_y, _ = cr.arcs[0]

        expected = project(state.camera_params, [(2.0, 1.0, 1.7)], w, h)[0]
        assert math.isclose(ball_x, float(expected[0]), abs_tol=1e-6)
        assert math.isclose(ball_y, float(expected[1]), abs_tol=1e-6)


class TestGridLineCountIsBounded:
    """A degenerate width/depth ÷ spacing must not explode the grid render."""

    def _state(self, gw: float, gd: float, gs: float) -> OverlayState:
        state = OverlayState()
        state.camera_params = _camera_params()
        state.grid_config = (gw, gd, gs, 0.0, 0.0, 0.0)
        return state

    def test_degenerate_grid_caps_lines_and_buffer(self) -> None:
        # width=depth=5000, spacing=0.05 would compute ~200k points/axis with
        # no clamp – a multi-MB buffer and hundreds of thousands of Cairo calls.
        renderer = FakeRenderer()
        baseline_buf = renderer._grid_pts_buf.shape[0]
        cr = FakeCairo()

        draw_grid(renderer, cr, self._state(5000.0, 5000.0, 0.05), 1920, 1080)

        # Each line is one move_to + one line_to; total lines stay bounded.
        assert len(cr.move_tos) <= 2 * _MAX_GRID_LINES_PER_AXIS
        assert len(cr.line_tos) == len(cr.move_tos)
        # Buffer stays bounded too (default is reused or grown by a small fixed cap).
        max_pts = (2 * _MAX_GRID_LINES_PER_AXIS) * 2
        assert renderer._grid_pts_buf.shape[0] <= max(baseline_buf, max_pts)

    def test_normal_grid_draws_every_line(self) -> None:
        # A reasonable grid stays well under the cap, so no clamping occurs.
        renderer = FakeRenderer()
        cr = FakeCairo()

        draw_grid(renderer, cr, self._state(10.0, 6.0, 1.0), 1920, 1080)

        # n_z = 6/1+1 = 7 horizontal lines, n_x = 10/1+1 = 11 vertical lines.
        assert len(cr.move_tos) == 7 + 11
        assert len(cr.line_tos) == len(cr.move_tos)

    def test_hidden_grid_draws_nothing(self) -> None:
        # grid_visible=False short-circuits before any line is emitted.
        renderer = FakeRenderer()
        cr = FakeCairo()
        state = self._state(10.0, 6.0, 1.0)
        state.grid_visible = False

        draw_grid(renderer, cr, state, 1920, 1080)

        assert cr.move_tos == []
        assert cr.line_tos == []


class TestAssistGhost:
    """The assist-mode AI-output ghost renders as a dim crosshair + ground ring,
    never a filled ball, and carries no HUD card (filtered elsewhere)."""

    def test_ghost_draws_no_filled_ball(self) -> None:
        state = _state_with_grid_offset(0.0, 0.0, 0.0)
        ghost = MarkerOverlayData(marker_id=1, x=1.0, y=1.0, z=0.0, color="#33ff99", is_assist_ghost=True)
        cr = FakeCairo()
        draw_marker(cr, state, ghost, 1920, 1080)
        # No filled ball and no ball arc – the ghost is outline-only.
        assert cr.fills == 0
        assert cr.arcs == []
        # It still renders (crosshair + ground ring strokes).
        assert cr.strokes >= 1

    def test_normal_marker_draws_filled_ball(self) -> None:
        state = _state_with_grid_offset(0.0, 0.0, 0.0)
        marker = MarkerOverlayData(marker_id=1, x=1.0, y=1.0, z=0.0, color="#33ff99")
        cr = FakeCairo()
        draw_marker(cr, state, marker, 1920, 1080)
        # A normal solid marker keeps its filled ball.
        assert cr.fills >= 1
        assert cr.arcs

    def test_ghost_skips_ground_ring_without_grid_config(self) -> None:
        state = _state_with_grid_offset(0.0, 0.0, 0.0)
        state.grid_config = None  # no grid -> the ground ring is skipped
        ghost = MarkerOverlayData(marker_id=1, x=1.0, y=1.0, z=0.0, color="#33ff99", is_assist_ghost=True)
        cr = FakeCairo()
        draw_marker(cr, state, ghost, 1920, 1080)
        # Crosshair still renders; no crash from the missing grid.
        assert cr.strokes >= 1

    def test_ghost_handles_non_finite_projection(self, monkeypatch) -> None:
        import openfollow.runtime.overlay_draw_scene as mod

        state = _state_with_grid_offset(0.0, 0.0, 0.0)
        ghost = MarkerOverlayData(marker_id=1, x=1.0, y=1.0, z=0.0, color="#33ff99", is_assist_ghost=True)

        def _fake_project(_cam, pts, _w, _h):
            # The marker's own position (2 points) stays finite so draw_marker
            # reaches the ghost path; the crosshair (6) and ground ring (24)
            # project to non-finite, so their segments are skipped and the ring
            # is degenerate (<3 finite points).
            if len(pts) == 2:
                return np.array([[100.0, 100.0], [110.0, 100.0]])
            return np.full((len(pts), 2), np.nan)

        monkeypatch.setattr(mod, "project", _fake_project)
        cr = FakeCairo()
        draw_marker(cr, state, ghost, 1920, 1080)
        # Reached the ghost path, but nothing fillable was drawn.
        assert cr.fills == 0
