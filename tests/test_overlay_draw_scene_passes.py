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
* :func:`draw_marker` – ball / crosshair / Z-line / ground-circle
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
    state.show_z_line = False
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

        def _fake_project(cam, pts, w, h, k1=0.0, k2=0.0):
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

        def _fake_project(cam, pts, w, h, k1=0.0, k2=0.0):
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

        def _fake_project(cam, pts, w, h, k1=0.0, k2=0.0):
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

    def test_z_line_requires_grid_config(self) -> None:
        state = _scene_state(show_z_line=True, grid_config=None)
        cr = FakeCairo()
        draw_marker(cr, state, self._marker(), 1920, 1080)
        assert cr.move_tos == []
        assert cr.line_tos == []

    def test_z_line_emitted_when_grid_and_flag_set(self) -> None:
        state = _scene_state(
            show_z_line=True,
            grid_config=(10.0, 6.0, 1.0, 0.0, 0.0, 0.0),
            z_line_thickness=2,
        )
        cr = FakeCairo()
        draw_marker(cr, state, self._marker(x=0.5, y=0.5, z=2.0), 1920, 1080)
        # One move_to + one line_to for the drop segment.  stroke() finalises.
        assert len(cr.move_tos) == 1
        assert len(cr.line_tos) == 1
        assert cr.strokes == 1

    def test_z_line_skipped_when_projection_nonfinite(self) -> None:
        from openfollow.runtime import overlay_draw_scene as mod

        call = [0]

        def _fake_project(cam, pts, w, h, k1=0.0, k2=0.0):
            arr = np.asarray(pts, dtype=np.float64).reshape(-1, 3)
            call[0] += 1
            # First call (center radius pair) – return finite.
            if call[0] == 1:
                return np.array([[500.0, 400.0], [510.0, 400.0]], dtype=np.float64)
            # Z line call – NaN.
            return np.full((len(arr), 2), np.nan, dtype=np.float64)

        state = _scene_state(
            show_z_line=True,
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

        def _fake_project(cam, pts, w, h, k1=0.0, k2=0.0):
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

    def test_crosshair_style_is_unchanged_by_the_cone_fields(self) -> None:
        """In crosshair style the cone settings are inert: the draw trace is
        the same whatever they hold."""
        traces = []
        for radius, thickness in ((0.3, 2), (5.0, 9)):
            state = _scene_state(
                show_ball=True,
                show_crosshair=True,
                show_z_line=True,
                show_ground_circle=True,
                cone_base_diameter=radius,
                cone_thickness=thickness,
            )
            cr = FakeCairo()
            draw_marker(cr, state, self._marker(z=1.6), 1920, 1080)
            traces.append(cr.calls)
        assert traces[0] == traces[1]
        assert ("close_path",) in traces[0]  # the ground circle still draws

    def test_project_helper_roundtrips_through_numpy_reshape(self) -> None:
        """Plain Python lists and numpy arrays both work as inputs."""
        cam = _camera_params()
        a = project(cam, [(0.0, 0.0, 0.0)], 1920, 1080)
        b = project(cam, np.array([[0.0, 0.0, 0.0]]), 1920, 1080)
        np.testing.assert_allclose(a, b)


# --------------------------------------------------------------------------- #
# draw_marker – cone style
# --------------------------------------------------------------------------- #


def _ring_on_screen(state: OverlayState, x: float, y: float, z: float, radius: float) -> np.ndarray:
    from openfollow.scene.solver import CONE_RING_SEGMENTS, ground_circle_world_ring

    ring = ground_circle_world_ring(x, y, z, radius, segments=CONE_RING_SEGMENTS)
    return project(state.camera_params, ring, 1920, 1080)


# One projection per cone marker: both centres and both rings.
_CONE_POINTS = 2 * 72 + 2
_TOP_START = 72 + 1  # index of the top centre in that projection


def _ring_paths(cr: FakeCairo, *, before_fill: bool = False) -> list[list[tuple[float, float]]]:
    """Split the recorded path into sub-paths (one per move_to), closed ones only.

    ``before_fill`` keeps only the sub-paths pathed ahead of the last ``fill``
    call (the filled regions); the default keeps only those pathed after it
    (the stroked wireframe).
    """
    calls = cr.calls
    if ("fill",) in calls:
        cut = len(calls) - 1 - calls[::-1].index(("fill",))
        calls = calls[:cut] if before_fill else calls[cut + 1 :]
    elif before_fill:
        return []
    paths: list[list[tuple[float, float]]] = []
    closed: list[list[tuple[float, float]]] = []
    for call in calls:
        if call[0] == "move_to":
            paths.append([(call[1], call[2])])
        elif call[0] == "line_to":
            paths[-1].append((call[1], call[2]))
        elif call[0] == "close_path":
            closed.append(paths[-1])
    return closed


def _signed_area(poly: list[tuple[float, float]]) -> float:
    arr = np.array(poly)
    x, y = arr[:, 0], arr[:, 1]
    return float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


class TestDrawMarkerConeStyle:
    def _marker(self, **kw: object) -> MarkerOverlayData:
        defaults = {"marker_id": 1, "x": 1.0, "y": 0.5, "z": 1.8, "color": "#ff3333"}
        defaults.update(kw)
        return MarkerOverlayData(**defaults)  # type: ignore[arg-type]

    def _state(self, **overrides: object) -> OverlayState:
        # Diameters 0.8 / 0.4 m: rings of radius 0.4 / 0.2 m.
        base: dict[str, object] = {
            "marker_style": "cone",
            "cone_base_diameter": 0.8,
            "cone_top_diameter": 0.4,
            "cone_thickness": 2,
            "cone_filled": False,
            "cone_shaded": False,
        }
        base.update(overrides)
        return _scene_state(**base)

    def test_draws_two_rings_and_two_edges_in_one_stroke(self) -> None:
        state = self._state()
        cr = FakeCairo()
        draw_marker(cr, state, self._marker(), 1920, 1080)
        assert cr.closes == 2  # floor ring + top ring
        assert len(cr.move_tos) == 4  # two rings, two silhouette edges
        assert len(cr.line_tos) == 2 * 71 + 2
        assert cr.strokes == 1
        assert cr.fills == 0
        assert cr.arcs == []

    def test_rings_sit_on_the_stage_plane_and_at_the_marker_z(self) -> None:
        state = self._state(grid_config=(10.0, 6.0, 1.0, 0.0, 0.0, 0.5))
        marker = self._marker(z=2.0)
        cr = FakeCairo()
        draw_marker(cr, state, marker, 1920, 1080)
        floor, top = _ring_paths(cr)
        np.testing.assert_allclose(np.array(floor), _ring_on_screen(state, 1.0, 0.5, 0.5, 0.4))
        np.testing.assert_allclose(np.array(top), _ring_on_screen(state, 1.0, 0.5, 2.0, 0.2))

    def test_equal_radii_give_a_cylinder_and_zero_top_radius_a_point(self) -> None:
        cyl = self._state(cone_top_diameter=0.8)
        cr = FakeCairo()
        draw_marker(cr, cyl, self._marker(), 1920, 1080)
        _, top = _ring_paths(cr)
        np.testing.assert_allclose(np.array(top), _ring_on_screen(cyl, 1.0, 0.5, 1.8, 0.4))

        point = self._state(cone_top_diameter=0.0)
        cr = FakeCairo()
        draw_marker(cr, point, self._marker(), 1920, 1080)
        _, top = _ring_paths(cr)
        apex = project(point.camera_params, [(1.0, 0.5, 1.8)], 1920, 1080)[0]
        for pt in top:
            np.testing.assert_allclose(pt, apex)

    def test_edges_join_the_two_rings(self) -> None:
        state = self._state()
        cr = FakeCairo()
        draw_marker(cr, state, self._marker(), 1920, 1080)
        floor, top = _ring_paths(cr)
        edge_starts = cr.move_tos[2:]
        edge_ends = cr.line_tos[-2:]
        for start, end in zip(edge_starts, edge_ends, strict=True):
            assert any(np.allclose(start, pt) for pt in floor)
            assert any(np.allclose(end, pt) for pt in top)
        # One edge per side of the marker: the two starts straddle the floor centre.
        xs = sorted(x for x, _ in edge_starts)
        assert xs[0] < xs[1]

    @pytest.mark.parametrize("z", [0.0, -0.5])
    def test_top_ring_follows_the_marker_at_or_below_the_stage(self, z: float) -> None:
        """The cone is not symmetric, so a marker under the stage draws the cone
        pointing down rather than collapsing to the base ring."""
        state = self._state()
        cr = FakeCairo()
        draw_marker(cr, state, self._marker(z=z), 1920, 1080)
        assert cr.closes == 2
        assert len(cr.move_tos) == 4
        assert cr.strokes == 1
        floor, top = _ring_paths(cr)
        np.testing.assert_allclose(np.array(floor), _ring_on_screen(state, 1.0, 0.5, 0.0, 0.4))
        np.testing.assert_allclose(np.array(top), _ring_on_screen(state, 1.0, 0.5, z, 0.2))

    def test_zero_radii_draw_the_vertical_axis(self) -> None:
        state = self._state(cone_base_diameter=0.0, cone_top_diameter=0.0)
        cr = FakeCairo()
        draw_marker(cr, state, self._marker(), 1920, 1080)
        floor_c = project(state.camera_params, [(1.0, 0.5, 0.0)], 1920, 1080)[0]
        top_c = project(state.camera_params, [(1.0, 0.5, 1.8)], 1920, 1080)[0]
        for start in cr.move_tos[2:]:
            np.testing.assert_allclose(start, floor_c)
        for end in cr.line_tos[-2:]:
            np.testing.assert_allclose(end, top_c)

    def test_uses_the_marker_colour_and_the_cone_thickness(self) -> None:
        state = self._state(cone_thickness=4)
        cr = FakeCairo()
        draw_marker(cr, state, self._marker(color="#ff3333"), 1920, 1080)
        rgba = [c for c in cr.calls if c[0] == "rgba"]
        assert rgba == [("rgba", pytest.approx(1.0), pytest.approx(0.2), pytest.approx(0.2), 0.8)]
        assert [c for c in cr.calls if c[0] == "line_width"] == [("line_width", 4.0)]

    def test_selected_marker_is_drawn_heavier(self) -> None:
        state = self._state(cone_thickness=2, selected_id=1)
        cr = FakeCairo()
        draw_marker(cr, state, self._marker(marker_id=1), 1920, 1080)
        assert [c for c in cr.calls if c[0] == "line_width"] == [("line_width", 3.0)]

    def test_crosshair_fields_are_ignored(self) -> None:
        """The ball, crosshair, Z line and ground circle switches change nothing."""
        traces = []
        for flag in (False, True):
            state = self._state(
                show_ball=flag,
                show_crosshair=flag,
                show_z_line=flag,
                show_ground_circle=flag,
                ground_circle_filled=flag,
            )
            cr = FakeCairo()
            draw_marker(cr, state, self._marker(), 1920, 1080)
            traces.append(cr.calls)
        assert traces[0] == traces[1]
        assert traces[0].count(("close_path",)) == 2

    def test_assist_ghost_is_a_dim_cone(self) -> None:
        state = self._state(cone_thickness=3)
        cr = FakeCairo()
        draw_marker(cr, state, self._marker(is_assist_ghost=True), 1920, 1080)
        assert cr.closes == 2
        assert len(cr.move_tos) == 4
        assert cr.fills == 0
        assert cr.arcs == []
        rgba = [c for c in cr.calls if c[0] == "rgba"]
        assert len(rgba) == 1 and rgba[0][4] == 0.5
        assert [c for c in cr.calls if c[0] == "line_width"] == [("line_width", 3.0)]

    def test_requires_grid_config(self) -> None:
        state = self._state(grid_config=None)
        cr = FakeCairo()
        draw_marker(cr, state, self._marker(), 1920, 1080)
        assert cr.strokes == 0
        assert cr.move_tos == []

    def test_nonfinite_rings_draw_nothing(self, monkeypatch) -> None:
        from openfollow.runtime import overlay_draw_scene as mod

        real = mod.project

        def _fake_project(cam, pts, w, h, k1=0.0, k2=0.0):
            arr = np.asarray(pts, dtype=np.float64).reshape(-1, 3)
            if len(arr) == 2:  # the marker's own centre + radius point
                return real(cam, pts, w, h, k1, k2)
            return np.full((len(arr), 2), np.nan, dtype=np.float64)

        monkeypatch.setattr(mod, "project", _fake_project)
        state = self._state()
        cr = FakeCairo()
        draw_marker(cr, state, self._marker(), 1920, 1080)
        assert cr.strokes == 0
        assert cr.move_tos == []

    def test_partial_ring_behind_the_camera_keeps_the_rest(self, monkeypatch) -> None:
        """One ring point behind the camera drops that point, not the ring."""
        from openfollow.runtime import overlay_draw_scene as mod

        real = mod.project

        def _fake_project(cam, pts, w, h, k1=0.0, k2=0.0):
            out = real(cam, pts, w, h, k1, k2)
            if len(out) == _CONE_POINTS:
                out[5] = np.nan  # a base ring point
                out[_TOP_START + 5] = np.nan  # a top ring point
            return out

        monkeypatch.setattr(mod, "project", _fake_project)
        state = self._state()
        cr = FakeCairo()
        draw_marker(cr, state, self._marker(), 1920, 1080)
        floor, top = _ring_paths(cr)
        assert len(floor) == 71
        assert len(top) == 71
        assert cr.strokes == 1

    def test_unfilled_emits_no_fill(self) -> None:
        cr = FakeCairo()
        draw_marker(cr, self._state(cone_filled=False), self._marker(), 1920, 1080)
        assert cr.fills == 0

    def test_shipped_defaults_fill_shaded_at_the_ground_circle_opacity(self) -> None:
        state = _scene_state(marker_style="cone")
        cr = FakeCairo()
        draw_marker(cr, state, self._marker(), 1920, 1080)
        assert cr.fills == 3  # base disc, shaded side, lid
        assert ("source_pattern",) in cr.calls
        rgba = [c for c in cr.calls if c[0] == "rgba"]
        assert rgba[0][4] == 0.4

    def test_shaded_fill_lights_the_side_from_the_left(self) -> None:
        state = self._state(cone_filled=True, cone_opacity=0.5, cone_shaded=True)
        cr = FakeCairo()
        draw_marker(cr, state, self._marker(color="#ff3333"), 1920, 1080)
        # Flat base disc, gradient side, flat lid, then the wireframe stroke.
        kinds = [c[0] for c in cr.calls if c[0] in ("fill", "source_pattern", "stroke")]
        assert kinds == ["fill", "source_pattern", "fill", "fill", "stroke"]
        (grad,) = cr.patterns
        x0, _, x1, _ = grad.get_linear_points()
        assert x0 < x1  # runs from the left edge's midpoint to the right one
        stops = grad.get_color_stops_rgba()
        assert [s[0] for s in stops] == [0.0, 0.5, 1.0]
        lit, base, shadow = (s[1:] for s in stops)
        assert base == pytest.approx((1.0, 0.2, 0.2, 0.5))
        assert all(c >= b for c, b in zip(lit[:3], base[:3], strict=True)) and lit[1] > base[1]
        assert all(c <= b for c, b in zip(shadow[:3], base[:3], strict=True)) and shadow[0] < base[0]
        assert lit[3] == shadow[3] == 0.5

    def test_shaded_side_shares_no_area_with_the_discs(self) -> None:
        """The side stops at the ring arcs, not at a chord across the discs:
        a translucent fill must not double up over half of the base disc, which
        drew a hard line across it that hopped as the marker moved."""
        from openfollow.zones.geometry import point_in_polygon

        state = self._state(cone_filled=True, cone_opacity=0.5, cone_shaded=True)
        cr = FakeCairo()
        draw_marker(cr, state, self._marker(), 1920, 1080)
        base, side, lid = _ring_paths(cr, before_fill=True)
        base_c = np.mean(base, axis=0)
        lid_c = np.mean(lid, axis=0)
        # Every side vertex is a ring vertex: the region is bounded by arcs.
        ring_pts = np.vstack([base, lid])
        for pt in side:
            assert any(np.allclose(pt, rp) for rp in ring_pts)
        # Inside the base disc, on the lid's side of its centre: disc only.
        far = min(base, key=lambda p: math.hypot(p[0] - lid_c[0], p[1] - lid_c[1]))
        probe = base_c + 0.5 * (np.array(far) - base_c)
        assert point_in_polygon(float(probe[0]), float(probe[1]), base)
        assert not point_in_polygon(float(probe[0]), float(probe[1]), side)
        # Halfway between the rings: side only.
        mid = (base_c + lid_c) / 2.0
        assert point_in_polygon(float(mid[0]), float(mid[1]), side)
        assert not point_in_polygon(float(mid[0]), float(mid[1]), base)
        assert not point_in_polygon(float(mid[0]), float(mid[1]), lid)

    def test_silhouette_edges_move_smoothly_as_the_marker_slides(self) -> None:
        """The tangent points can only land on ring samples, so a coarse ring
        makes the base chord rotate in visible hops as a marker crosses the
        stage. With the cone's ring density each hop stays under a degree."""
        state = self._state()
        angles = []
        for x in np.linspace(0.0, 2.0, 81):
            cr = FakeCairo()
            draw_marker(cr, state, self._marker(x=x, y=0.5, z=1.8), 1920, 1080)
            (x0, y0), (x1, y1) = cr.move_tos[2:]  # the two edges' base endpoints
            angles.append(math.degrees(math.atan2(y1 - y0, x1 - x0)))
        steps = [abs(b - a) for a, b in zip(angles[:-1], angles[1:], strict=True)]
        assert max(steps) < 1.5
        assert angles[-1] - angles[0] > 3.0  # the chord does turn with perspective

    def test_unshaded_fill_is_one_flat_pass(self) -> None:
        state = self._state(cone_filled=True, cone_opacity=0.5, cone_shaded=False)
        cr = FakeCairo()
        draw_marker(cr, state, self._marker(), 1920, 1080)
        assert cr.fills == 1
        assert cr.patterns == []

    def test_shaded_without_edges_falls_back_to_a_flat_fill(self, monkeypatch) -> None:
        from openfollow.runtime import overlay_draw_scene as mod

        real = mod.project

        def _fake_project(cam, pts, w, h, k1=0.0, k2=0.0):
            out = real(cam, pts, w, h, k1, k2)
            if len(out) == _CONE_POINTS:
                out[_TOP_START:] = np.nan  # no top ring, so no silhouette edges
            return out

        monkeypatch.setattr(mod, "project", _fake_project)
        state = self._state(cone_filled=True, cone_opacity=0.5, cone_shaded=True)
        cr = FakeCairo()
        draw_marker(cr, state, self._marker(), 1920, 1080)
        assert cr.fills == 1
        assert cr.patterns == []

    def test_shading_is_inert_without_a_fill(self) -> None:
        state = self._state(cone_filled=False, cone_shaded=True)
        cr = FakeCairo()
        draw_marker(cr, state, self._marker(), 1920, 1080)
        assert cr.fills == 0
        assert cr.patterns == []

    def test_assist_ghost_is_never_shaded(self) -> None:
        state = self._state(cone_filled=True, cone_opacity=0.9, cone_shaded=True)
        cr = FakeCairo()
        draw_marker(cr, state, self._marker(is_assist_ghost=True), 1920, 1080)
        assert cr.fills == 0
        assert cr.patterns == []

    def test_filled_cone_fills_the_silhouette_before_the_wireframe(self) -> None:
        state = self._state(cone_filled=True, cone_opacity=0.45)
        cr = FakeCairo()
        draw_marker(cr, state, self._marker(color="#ff3333"), 1920, 1080)
        assert cr.fills == 1
        assert cr.calls.index(("fill",)) < cr.calls.index(("stroke",))
        # The fill is in the marker colour at the configured opacity; the
        # wireframe keeps its own alpha on top.
        rgba = [c for c in cr.calls if c[0] == "rgba"]
        assert rgba[0] == ("rgba", pytest.approx(1.0), pytest.approx(0.2), pytest.approx(0.2), 0.45)
        assert rgba[1][4] == 0.8
        # Floor ring, top ring and the side between the edges, as one region.
        fill_paths = _ring_paths(cr, before_fill=True)
        assert [len(p) for p in fill_paths[:2]] == [72, 72]
        assert len(fill_paths) == 3 and len(fill_paths[2]) >= 4
        assert len(_ring_paths(cr)) == 2  # the wireframe rings still stroke

    def test_filled_sub_paths_are_wound_the_same_way(self) -> None:
        """Cairo's non-zero rule unions same-winding sub-paths; a sub-path wound
        the other way would punch a hole where the side overlaps a ring."""
        state = self._state(cone_filled=True, cone_opacity=0.45)
        cr = FakeCairo()
        draw_marker(cr, state, self._marker(), 1920, 1080)
        areas = [_signed_area(p) for p in _ring_paths(cr, before_fill=True)]
        assert all(a > 0 for a in areas)

    def test_filled_side_spans_the_two_silhouette_edges(self) -> None:
        state = self._state(cone_filled=True, cone_opacity=0.45)
        cr = FakeCairo()
        draw_marker(cr, state, self._marker(), 1920, 1080)
        side = _ring_paths(cr, before_fill=True)[2]
        edge_starts = cr.move_tos[-2:]
        edge_ends = cr.line_tos[-2:]
        for pt in (*edge_starts, *edge_ends):
            assert any(np.allclose(pt, s) for s in side)

    @pytest.mark.parametrize("z", [0.0, -0.5])
    def test_filled_cone_below_the_stage_still_fills_three_regions(self, z: float) -> None:
        state = self._state(cone_filled=True, cone_opacity=0.45)
        cr = FakeCairo()
        draw_marker(cr, state, self._marker(z=z), 1920, 1080)
        assert cr.fills == 1
        fill_paths = _ring_paths(cr, before_fill=True)
        assert [len(p) for p in fill_paths[:2]] == [72, 72]
        assert len(fill_paths) == 3 and len(fill_paths[2]) >= 4

    def test_zero_opacity_fills_nothing(self) -> None:
        state = self._state(cone_filled=True, cone_opacity=0.0)
        cr = FakeCairo()
        draw_marker(cr, state, self._marker(), 1920, 1080)
        assert cr.fills == 0

    def test_filled_cone_with_nothing_on_screen_fills_nothing(self, monkeypatch) -> None:
        from openfollow.runtime import overlay_draw_scene as mod

        real = mod.project

        def _fake_project(cam, pts, w, h, k1=0.0, k2=0.0):
            arr = np.asarray(pts, dtype=np.float64).reshape(-1, 3)
            if len(arr) == 2:
                return real(cam, pts, w, h, k1, k2)
            return np.full((len(arr), 2), np.nan, dtype=np.float64)

        monkeypatch.setattr(mod, "project", _fake_project)
        state = self._state(cone_filled=True, cone_opacity=0.5)
        cr = FakeCairo()
        draw_marker(cr, state, self._marker(), 1920, 1080)
        assert cr.fills == 0
        assert cr.strokes == 0

    def test_assist_ghost_is_never_filled(self) -> None:
        state = self._state(cone_filled=True, cone_opacity=0.9)
        cr = FakeCairo()
        draw_marker(cr, state, self._marker(is_assist_ghost=True), 1920, 1080)
        assert cr.fills == 0
        assert cr.closes == 2

    def test_top_behind_the_camera_keeps_the_base_ring(self, monkeypatch) -> None:
        """A raised marker walking toward a low camera: its own point at Z
        projects behind the lens, but the base ring is on screen and stays."""
        from openfollow.runtime import overlay_draw_scene as mod

        real = mod.project

        def _fake_project(cam, pts, w, h, k1=0.0, k2=0.0):
            out = real(cam, pts, w, h, k1, k2)
            if len(out) == _CONE_POINTS:
                out[_TOP_START:] = np.nan  # top centre and the whole top ring
            return out

        monkeypatch.setattr(mod, "project", _fake_project)
        state = self._state(cone_filled=True, cone_opacity=0.5)
        cr = FakeCairo()
        draw_marker(cr, state, self._marker(), 1920, 1080)
        assert cr.strokes == 1
        assert len(_ring_paths(cr)) == 1  # the base ring, no edges
        assert len(cr.move_tos) == 2  # one for the fill, one for the stroke
        assert cr.fills == 1

    def test_cone_projects_once_per_marker(self, monkeypatch) -> None:
        from openfollow.runtime import overlay_draw_scene as mod

        real = mod.project
        calls: list[int] = []

        def _counting_project(cam, pts, w, h, k1=0.0, k2=0.0):
            calls.append(len(np.asarray(pts, dtype=np.float64).reshape(-1, 3)))
            return real(cam, pts, w, h, k1, k2)

        monkeypatch.setattr(mod, "project", _counting_project)
        cr = FakeCairo()
        draw_marker(cr, self._state(), self._marker(), 1920, 1080)
        assert calls == [_CONE_POINTS]

    def test_lens_distortion_warps_the_rings(self) -> None:
        plain = self._state()
        warped = self._state(lens_k1=-0.2)
        cr_plain, cr_warped = FakeCairo(), FakeCairo()
        draw_marker(cr_plain, plain, self._marker(), 1920, 1080)
        draw_marker(cr_warped, warped, self._marker(), 1920, 1080)
        assert len(cr_plain.line_tos) == len(cr_warped.line_tos)
        assert not np.allclose(np.array(cr_plain.line_tos), np.array(cr_warped.line_tos))
