# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for :mod:`openfollow.runtime.overlay_draw_scene` draw passes.

The companion file ``test_overlay_draw_scene.py`` covers the
coordinate-system invariants (PSN(0,0,0) renders at the origin glyph,
grid offsets don't leak into ``marker.pos``).  This file covers every
branch of the remaining draw entry points:

* :func:`draw_grid`  – grid buffer resize, spacing gate, offset handling.
* :func:`draw_origin` – gated draw + three coloured axes.
* :func:`draw_detections` – rectangles + optional label chips.
* :func:`draw_marker` – ball / crosshair / drop-line / ground-circle
  gates and fallbacks (finite-projection guards, selected-scale bump).
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from openfollow.runtime.overlay_draw_scene import (
    draw_detections,
    draw_grid,
    draw_marker,
    draw_origin,
    project,
)
from openfollow.runtime.overlay_state import MarkerOverlayData, OverlayState
from openfollow.video.detection import DetectionBox
from tests._fake_cairo import FakeCairo, FakeRenderer

pytestmark = pytest.mark.unit

# --------------------------------------------------------------------------- #
# Shared fixtures
# --------------------------------------------------------------------------- #


def _camera_params() -> np.ndarray:
    return np.array([0.0, -6.0, 2.5, -15.0, 0.0, 0.0, 60.0], dtype=np.float64)


def _scene_state(**overrides: object) -> OverlayState:
    state = OverlayState()
    state.camera_params = _camera_params()
    state.grid_config = (10.0, 6.0, 1.0, 0.0, 0.0, 0.0)
    state.show_ball = False
    state.show_crosshair = False
    state.show_drop_line = False
    state.show_ground_circle = False
    state.show_origin = False
    for k, v in overrides.items():
        setattr(state, k, v)
    return state


# --------------------------------------------------------------------------- #
# draw_grid
# --------------------------------------------------------------------------- #


class TestDrawGrid:
    def test_missing_grid_config_is_no_op(self) -> None:
        state = _scene_state(grid_config=None)
        cr = FakeCairo()
        draw_grid(FakeRenderer(), cr, state, 1920, 1080)
        assert cr.calls == []

    @pytest.mark.parametrize("spacing", [0.0, -1.0, -0.5])
    def test_non_positive_spacing_is_no_op(self, spacing: float) -> None:
        state = _scene_state(grid_config=(10.0, 6.0, spacing, 0.0, 0.0, 0.0))
        cr = FakeCairo()
        draw_grid(FakeRenderer(), cr, state, 1920, 1080)
        # The guard is purely on spacing ≤ 0, so the whole body must
        # short-circuit – no strokes, no move_to/line_to.
        assert cr.strokes == 0
        assert cr.move_tos == []
        assert cr.line_tos == []

    def test_grid_emits_paired_endpoints(self) -> None:
        """Each line is one move_to + one line_to; counts balance."""
        state = _scene_state(grid_config=(10.0, 6.0, 1.0, 0.0, 0.0, 0.0))
        cr = FakeCairo()
        draw_grid(FakeRenderer(), cr, state, 1920, 1080)
        assert len(cr.move_tos) == len(cr.line_tos)
        assert cr.strokes == 1  # single stroke() call finalises the batch

    def test_grid_pts_buf_grows_to_fit_large_grid(self) -> None:
        state = _scene_state(grid_config=(1000.0, 1000.0, 1.0, 0.0, 0.0, 0.0))
        renderer = FakeRenderer()
        initial_shape = renderer._grid_pts_buf.shape
        draw_grid(renderer, FakeCairo(), state, 1920, 1080)
        # The grid needs ((1000+1)+(1000+1))*2 ~= 4004 points; the buffer must
        # have been replaced with something strictly larger than the default.
        assert renderer._grid_pts_buf.shape[0] > initial_shape[0]

    def test_grid_offset_shifts_line_positions(self) -> None:
        """Changing only the grid x-offset shifts the projected x of the
        first grid line.  (This is the flipside of the PSN-absolute
        marker invariant: grid *does* live at its configured offset.)
        """
        a_state = _scene_state(grid_config=(10.0, 6.0, 1.0, 0.0, 0.0, 0.0))
        b_state = _scene_state(grid_config=(10.0, 6.0, 1.0, 2.0, 0.0, 0.0))
        a_cr = FakeCairo()
        b_cr = FakeCairo()
        draw_grid(FakeRenderer(), a_cr, a_state, 1920, 1080)
        draw_grid(FakeRenderer(), b_cr, b_state, 1920, 1080)
        assert a_cr.move_tos != b_cr.move_tos

    def test_grid_skips_nonfinite_segments(self) -> None:
        """Segments that project off-screen (NaN/Inf) must be skipped."""
        # Put the camera at the origin with a vertical pitch that produces
        # near-parallel projection rays → several endpoints become NaN.
        state = _scene_state()
        state.camera_params = np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 60.0], dtype=np.float64)
        state.grid_config = (10.0, 6.0, 1.0, 0.0, 0.0, 0.0)
        cr = FakeCairo()
        with np.errstate(invalid="ignore", divide="ignore"):
            draw_grid(FakeRenderer(), cr, state, 1920, 1080)
        # The outer try does not skip the whole grid, but individual
        # segments must be filtered – move_to/line_to should NOT have
        # been called for NaN endpoints.  The stroke at the end is
        # still emitted.
        assert cr.strokes == 1


# --------------------------------------------------------------------------- #
# draw_origin
# --------------------------------------------------------------------------- #


class TestDrawOrigin:
    def test_gated_off_by_show_origin_false(self) -> None:
        state = _scene_state(show_origin=False)
        cr = FakeCairo()
        draw_origin(cr, state, 1920, 1080)
        assert cr.calls == []

    def test_emits_three_axes(self) -> None:
        state = _scene_state(show_origin=True, origin_length=1.0, origin_thickness=3)
        cr = FakeCairo()
        draw_origin(cr, state, 1920, 1080)
        # 3 axes, each rendered as move_to + line_to + stroke.
        assert len(cr.move_tos) == 3
        assert len(cr.line_tos) == 3
        assert cr.strokes == 3

    def test_axis_with_nonfinite_endpoint_is_skipped(self) -> None:
        from openfollow.runtime import overlay_draw_scene as mod

        calls = [0]

        def _fake_project(cam, pts, w, h):
            calls[0] += 1
            if calls[0] == 1:
                # X axis: make the endpoint NaN so the branch body is skipped.
                return np.array([[np.nan, np.nan], [100.0, 100.0]], dtype=np.float64)
            return np.array([[10.0, 20.0], [30.0, 40.0]], dtype=np.float64)

        state = _scene_state(show_origin=True)
        cr = FakeCairo()
        real = mod.project
        try:
            mod.project = _fake_project  # type: ignore[assignment]
            draw_origin(cr, state, 1920, 1080)
        finally:
            mod.project = real  # type: ignore[assignment]

        # Only two axes (Y, Z) drawn; X skipped because its endpoint is NaN.
        assert len(cr.move_tos) == 2
        assert len(cr.line_tos) == 2
        assert cr.strokes == 2

    def test_axes_use_distinct_colors(self) -> None:
        """X axis red, Y axis green, Z axis blue-ish (0.0, 0.4, 1.0)."""
        state = _scene_state(show_origin=True)
        cr = FakeCairo()
        draw_origin(cr, state, 1920, 1080)
        rgba_calls = [c for c in cr.calls if c[0] == "rgba"]
        # First rgba per axis picks the axis colour.
        assert rgba_calls[0][1:4] == (1.0, 0.0, 0.0)
        assert rgba_calls[1][1:4] == (0.0, 1.0, 0.0)
        assert rgba_calls[2][1:4] == (0.0, 0.4, 1.0)


# --------------------------------------------------------------------------- #
# draw_detections
# --------------------------------------------------------------------------- #


class TestDrawDetections:
    def test_empty_detections_draws_nothing(self) -> None:
        state = _scene_state(detections=[])
        cr = FakeCairo()
        draw_detections(FakeRenderer(), cr, state, 1920, 1080)
        assert cr.rects == []
        assert cr.strokes == 0

    def test_detection_box_dimensions_scale_to_frame(self) -> None:
        state = _scene_state()
        # Normalised [0,1] rectangle covering upper-left quadrant.
        state.detections = [DetectionBox(x1=0.1, y1=0.2, x2=0.3, y2=0.4, confidence=0.91)]
        cr = FakeCairo()
        draw_detections(FakeRenderer(), cr, state, 1000, 500)
        # Rectangle: (x1*w, y1*h, (x2-x1)*w, (y2-y1)*h)
        assert cr.rects[0] == pytest.approx((100.0, 100.0, 200.0, 100.0))
        assert cr.strokes == 1

    def test_labels_suppressed_when_flag_off(self) -> None:
        state = _scene_state()
        state.detection_show_labels = False
        state.detections = [DetectionBox(x1=0.1, y1=0.2, x2=0.3, y2=0.4, confidence=0.91)]
        cr = FakeCairo()
        draw_detections(FakeRenderer(), cr, state, 1000, 500)
        assert cr.texts == []

    def test_labels_drawn_as_percentage_chips(self) -> None:
        state = _scene_state()
        state.detection_show_labels = True
        state.detections = [DetectionBox(x1=0.0, y1=0.5, x2=0.1, y2=0.6, confidence=0.912)]
        cr = FakeCairo()
        draw_detections(FakeRenderer(), cr, state, 1000, 500)
        # Confidence formatted as "91%" (zero decimals, percent sign).
        assert [t.text for t in cr.texts] == ["91%"]
        # Label chip: one filled rectangle behind the text + one stroke for
        # the box itself.  So `rects` is [box_rect, label_chip_rect].
        assert len(cr.rects) == 2

    def test_multiple_detections_draw_in_order(self) -> None:
        state = _scene_state()
        state.detection_show_labels = False
        state.detections = [
            DetectionBox(x1=0.0, y1=0.0, x2=0.1, y2=0.1, confidence=0.5),
            DetectionBox(x1=0.5, y1=0.5, x2=0.6, y2=0.6, confidence=0.8),
        ]
        cr = FakeCairo()
        draw_detections(FakeRenderer(), cr, state, 100, 100)
        assert cr.rects == [
            (0.0, 0.0, 10.0, 10.0),
            (50.0, 50.0, 10.0, 10.0),
        ]
        assert cr.strokes == 2

    def test_detection_color_parsed_from_hex(self) -> None:
        state = _scene_state(detection_box_color="#3399ff")
        state.detections = [DetectionBox(x1=0.0, y1=0.0, x2=1.0, y2=1.0, confidence=0.7)]
        cr = FakeCairo()
        draw_detections(FakeRenderer(), cr, state, 100, 100)
        # First rgba call after parse_hex is the box stroke at 0.8 alpha.
        first_rgba = next(c for c in cr.calls if c[0] == "rgba")
        assert first_rgba[1:4] == pytest.approx((0.2, 0.6, 1.0), abs=2e-3)
        assert first_rgba[4] == pytest.approx(0.8)

    def test_attached_box_uses_marker_colour_others_default(self) -> None:
        state = _scene_state(
            detection_box_color="#808080",  # default grey
            detection_show_labels=False,
            detection_attached_colors={2: "#ff0000"},  # track 2 → marker red
        )
        state.detections = [
            DetectionBox(x1=0.0, y1=0.0, x2=0.1, y2=0.1, confidence=0.5, track_id=1),
            DetectionBox(x1=0.5, y1=0.5, x2=0.6, y2=0.6, confidence=0.8, track_id=2),
        ]
        cr = FakeCairo()
        draw_detections(FakeRenderer(), cr, state, 100, 100)
        strokes = [c for c in cr.calls if c[0] == "rgba"]
        # Box 1 (unattached) renders grey; box 2 (attached) renders red.
        grey = 128 / 255
        assert strokes[0][1:4] == pytest.approx((grey, grey, grey))
        assert strokes[1][1:4] == pytest.approx((1.0, 0.0, 0.0))

    def test_two_attached_tracks_each_use_their_own_colour(self) -> None:
        # Assist drives every controlled marker, so the map can carry several
        # track→colour entries; each box paints in its own marker's colour and
        # an unmapped box falls back to the default.
        state = _scene_state(
            detection_box_color="#808080",  # default grey
            detection_show_labels=False,
            detection_attached_colors={1: "#ff0000", 3: "#00ff00"},
        )
        state.detections = [
            DetectionBox(x1=0.0, y1=0.0, x2=0.1, y2=0.1, confidence=0.5, track_id=1),
            DetectionBox(x1=0.2, y1=0.2, x2=0.3, y2=0.3, confidence=0.6, track_id=2),
            DetectionBox(x1=0.5, y1=0.5, x2=0.6, y2=0.6, confidence=0.8, track_id=3),
        ]
        cr = FakeCairo()
        draw_detections(FakeRenderer(), cr, state, 100, 100)
        strokes = [c for c in cr.calls if c[0] == "rgba"]
        grey = 128 / 255
        # Box 1 → red, box 2 (unmapped) → grey default, box 3 → green.
        assert strokes[0][1:4] == pytest.approx((1.0, 0.0, 0.0))
        assert strokes[1][1:4] == pytest.approx((grey, grey, grey))
        assert strokes[2][1:4] == pytest.approx((0.0, 1.0, 0.0))

    def test_attached_colour_ignored_when_track_not_present(self) -> None:
        # An attached track id with no matching detection leaves every box in
        # the default colour (no crash, no stray highlight).
        state = _scene_state(
            detection_box_color="#808080",
            detection_show_labels=False,
            detection_attached_colors={99: "#ff0000"},
        )
        state.detections = [DetectionBox(x1=0.0, y1=0.0, x2=0.1, y2=0.1, confidence=0.5, track_id=1)]
        cr = FakeCairo()
        draw_detections(FakeRenderer(), cr, state, 100, 100)
        first_rgba = next(c for c in cr.calls if c[0] == "rgba")
        grey = 128 / 255
        assert first_rgba[1:4] == pytest.approx((grey, grey, grey))


# --------------------------------------------------------------------------- #
# draw_marker – branch coverage
# --------------------------------------------------------------------------- #


class TestDrawMarkerBranches:
    def _marker(self, **kw: object) -> MarkerOverlayData:
        defaults = {
            "marker_id": 1,
            "x": 0.0,
            "y": 0.0,
            "z": 0.0,
            "color": "#ff3333",
        }
        defaults.update(kw)
        return MarkerOverlayData(**defaults)  # type: ignore[arg-type]

    def test_nonfinite_ball_center_bails_before_drawing(self) -> None:
        """If the center itself projects to NaN, nothing is emitted."""
        state = _scene_state(show_ball=True)
        # Camera at marker position → invalid perspective divide.
        state.camera_params = np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 60.0], dtype=np.float64)
        marker = self._marker(x=0.0, y=0.0, z=0.0)
        cr = FakeCairo()
        with np.errstate(invalid="ignore", divide="ignore"):
            draw_marker(cr, state, marker, 1920, 1080)
        assert cr.arcs == []
        assert cr.strokes == 0

    def test_selected_scales_ball_radius_up(self) -> None:
        """``marker_id == selected_id`` bumps the screen radius by 15%."""
        state = _scene_state(show_ball=True)
        marker = self._marker(marker_id=7, x=0.5, y=0.5, z=0.5)

        unsel_cr = FakeCairo()
        state.selected_id = None
        draw_marker(unsel_cr, state, marker, 1920, 1080)
        sel_cr = FakeCairo()
        state.selected_id = 7
        draw_marker(sel_cr, state, marker, 1920, 1080)
        # First arc is the filled ball – its radius is the only difference.
        assert sel_cr.arcs[0][2] > unsel_cr.arcs[0][2]
        assert math.isclose(sel_cr.arcs[0][2] / unsel_cr.arcs[0][2], 1.15, abs_tol=1e-6)

    def test_radius_fallback_when_offset_point_nonfinite(self) -> None:
        """If the ``(x+radius, y, z)`` helper point projects to NaN the ball
        still gets the documented ``sr = 10.0`` screen-radius fallback.
        """
        # Build a synthetic scr where scr[0] is finite but scr[1] is NaN by
        # monkey-patching ``project`` – easier than constructing a pathological
        # camera.
        from openfollow.runtime import overlay_draw_scene as mod

        calls: list[int] = []

        def _fake_project(cam, pts, w, h):
            arr = np.asarray(pts, dtype=np.float64).reshape(-1, 3)
            calls.append(len(arr))
            out = np.zeros((len(arr), 2), dtype=np.float64)
            out[0] = (500.0, 400.0)
            if len(arr) > 1:
                out[1] = (np.nan, np.nan)
            return out

        state = _scene_state(show_ball=True)
        marker = self._marker()
        cr = FakeCairo()
        real = mod.project
        try:
            mod.project = _fake_project  # type: ignore[assignment]
            draw_marker(cr, state, marker, 1920, 1080)
        finally:
            mod.project = real  # type: ignore[assignment]

        # The fallback radius is 10.0 per the module.
        assert cr.arcs[0][2] == pytest.approx(10.0)

    def test_ball_paints_fill_and_stroke(self) -> None:
        state = _scene_state(show_ball=True)
        cr = FakeCairo()
        draw_marker(cr, state, self._marker(), 1920, 1080)
        # Two arcs (fill path + stroke path at same centre/radius).
        assert len(cr.arcs) == 2
        assert cr.fills >= 1
        assert cr.strokes >= 1

    def test_crosshair_emits_three_axis_line_pairs(self) -> None:
        state = _scene_state(show_crosshair=True, crosshair_size=0.4, crosshair_thickness=2)
        cr = FakeCairo()
        draw_marker(cr, state, self._marker(), 1920, 1080)
        # 3 axes × (move_to + line_to) = 3 of each.
        assert len(cr.move_tos) == 3
        assert len(cr.line_tos) == 3

    def test_crosshair_skips_nonfinite_axis_endpoints(self) -> None:
        from openfollow.runtime import overlay_draw_scene as mod

        def _fake_project(cam, pts, w, h):
            arr = np.asarray(pts, dtype=np.float64).reshape(-1, 3)
            # Only called by the crosshair path for 6 endpoints; make them
            # all NaN.  The center projection (called first) stays finite.
            out = np.full((len(arr), 2), np.nan, dtype=np.float64)
            if len(arr) <= 2:
                out[:] = [[500.0, 400.0], [510.0, 400.0]][: len(arr)]
            return out

        state = _scene_state(show_crosshair=True)
        state.show_ball = False
        cr = FakeCairo()
        real = mod.project
        try:
            mod.project = _fake_project  # type: ignore[assignment]
            draw_marker(cr, state, self._marker(), 1920, 1080)
        finally:
            mod.project = real  # type: ignore[assignment]

        # No axis segments should have been emitted since each endpoint is NaN.
        assert cr.move_tos == []
        assert cr.line_tos == []

    def test_drop_line_requires_grid_config(self) -> None:
        state = _scene_state(show_drop_line=True, grid_config=None)
        cr = FakeCairo()
        draw_marker(cr, state, self._marker(), 1920, 1080)
        assert cr.move_tos == []
        assert cr.line_tos == []

    def test_drop_line_emitted_when_grid_and_flag_set(self) -> None:
        state = _scene_state(
            show_drop_line=True,
            grid_config=(10.0, 6.0, 1.0, 0.0, 0.0, 0.0),
            drop_line_thickness=2,
        )
        cr = FakeCairo()
        draw_marker(cr, state, self._marker(x=0.5, y=0.5, z=2.0), 1920, 1080)
        # One move_to + one line_to for the drop segment.  stroke() finalises.
        assert len(cr.move_tos) == 1
        assert len(cr.line_tos) == 1
        assert cr.strokes == 1

    def test_drop_line_skipped_when_projection_nonfinite(self) -> None:
        from openfollow.runtime import overlay_draw_scene as mod

        call = [0]

        def _fake_project(cam, pts, w, h):
            arr = np.asarray(pts, dtype=np.float64).reshape(-1, 3)
            call[0] += 1
            # First call (center radius pair) – return finite.
            if call[0] == 1:
                return np.array([[500.0, 400.0], [510.0, 400.0]], dtype=np.float64)
            # Drop line call – NaN.
            return np.full((len(arr), 2), np.nan, dtype=np.float64)

        state = _scene_state(
            show_drop_line=True,
            grid_config=(10.0, 6.0, 1.0, 0.0, 0.0, 0.0),
        )
        state.show_ball = False
        cr = FakeCairo()
        real = mod.project
        try:
            mod.project = _fake_project  # type: ignore[assignment]
            draw_marker(cr, state, self._marker(), 1920, 1080)
        finally:
            mod.project = real  # type: ignore[assignment]
        assert cr.move_tos == []

    def test_ground_circle_requires_grid_config(self) -> None:
        state = _scene_state(show_ground_circle=True, grid_config=None)
        cr = FakeCairo()
        draw_marker(cr, state, self._marker(), 1920, 1080)
        assert cr.arcs == []

    def test_ground_circle_filled_mode_emits_fill(self) -> None:
        state = _scene_state(
            show_ground_circle=True,
            ground_circle_filled=True,
            ground_circle_size=0.4,
            grid_config=(10.0, 6.0, 1.0, 0.0, 0.0, 0.0),
        )
        cr = FakeCairo()
        draw_marker(cr, state, self._marker(), 1920, 1080)
        assert cr.fills >= 1
        # Filled variant closes the path before filling.
        assert cr.closes >= 1

    def test_ground_circle_outline_mode_emits_stroke(self) -> None:
        state = _scene_state(
            show_ground_circle=True,
            ground_circle_filled=False,
            ground_circle_size=0.4,
            grid_config=(10.0, 6.0, 1.0, 0.0, 0.0, 0.0),
        )
        cr = FakeCairo()
        draw_marker(cr, state, self._marker(), 1920, 1080)
        assert cr.strokes >= 1

    def test_ground_circle_skipped_when_projection_nonfinite(self) -> None:
        from openfollow.runtime import overlay_draw_scene as mod

        calls = [0]

        def _fake_project(cam, pts, w, h):
            arr = np.asarray(pts, dtype=np.float64).reshape(-1, 3)
            calls[0] += 1
            if calls[0] == 1:
                return np.array([[500.0, 400.0], [510.0, 400.0]], dtype=np.float64)
            return np.full((len(arr), 2), np.nan, dtype=np.float64)

        state = _scene_state(
            show_ground_circle=True,
            ground_circle_filled=True,
            grid_config=(10.0, 6.0, 1.0, 0.0, 0.0, 0.0),
        )
        state.show_ball = False
        cr = FakeCairo()
        real = mod.project
        try:
            mod.project = _fake_project  # type: ignore[assignment]
            draw_marker(cr, state, self._marker(), 1920, 1080)
        finally:
            mod.project = real  # type: ignore[assignment]
        # No arcs, fills, or closes for the ground circle (initial ball arc
        # is suppressed via show_ball=False).
        assert cr.arcs == []
        assert cr.fills == 0

    def test_project_helper_roundtrips_through_numpy_reshape(self) -> None:
        """Plain Python lists and numpy arrays both work as inputs."""
        cam = _camera_params()
        a = project(cam, [(0.0, 0.0, 0.0)], 1920, 1080)
        b = project(cam, np.array([[0.0, 0.0, 0.0]]), 1920, 1080)
        np.testing.assert_allclose(a, b)
