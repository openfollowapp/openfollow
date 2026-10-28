# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Ground grid: clip geometry, the per-marker floor height and the HUD pass."""

from __future__ import annotations

import numpy as np
import pytest

from openfollow.runtime.overlay_draw_scene import (
    draw_ground_grid,
    draw_marker,
    floor_z_at,
    project,
)
from openfollow.runtime.overlay_state import MarkerOverlayData, OverlayState
from openfollow.scene.ground_grid import floor_height, grid_segments_in_polygon, main_grid_steps, outline_segments
from openfollow.scene.solver import ground_circle_world_ring
from tests._fake_cairo import FakeCairo

pytestmark = pytest.mark.unit

_SQUARE = [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0)]
# An L: the notch (1..2, 1..2) is outside.
_L_SHAPE = [(0.0, 0.0), (2.0, 0.0), (2.0, 1.0), (1.0, 1.0), (1.0, 2.0), (0.0, 2.0)]
_W, _H = 1920, 1080


def _sorted_segments(segs: np.ndarray) -> list[tuple[float, ...]]:
    return sorted(tuple(round(float(v), 6) for v in seg.ravel()) for seg in segs)


# --------------------------------------------------------------------------- #
# Geometry
# --------------------------------------------------------------------------- #


class TestGridSegmentsInPolygon:
    def test_square_gets_lines_at_the_grid_spacing(self) -> None:
        segs = grid_segments_in_polygon(_SQUARE, 0.0, 0.0, 1.0, 1.0, -0.5, 200)
        # Lines at y = 0, 1 and x = 0, 1 (the top / right edges are half-open).
        assert _sorted_segments(segs) == _sorted_segments(
            np.array(
                [
                    [(0, 0, -0.5), (2, 0, -0.5)],
                    [(0, 1, -0.5), (2, 1, -0.5)],
                    [(0, 0, -0.5), (0, 2, -0.5)],
                    [(1, 0, -0.5), (1, 2, -0.5)],
                ],
                dtype=float,
            )
        )

    def test_lines_follow_the_main_grid_origin_not_the_polygon(self) -> None:
        segs = grid_segments_in_polygon(_SQUARE, 0.25, 0.5, 1.0, 1.0, 0.0, 200)
        ys = {round(float(s[0, 1]), 6) for s in segs if s[0, 1] == s[1, 1]}
        xs = {round(float(s[0, 0]), 6) for s in segs if s[0, 0] == s[1, 0]}
        assert ys == {0.5, 1.5}
        assert xs == {0.25, 1.25}

    def test_concave_polygon_splits_a_line_at_the_notch(self) -> None:
        segs = grid_segments_in_polygon(_L_SHAPE, 0.0, 0.0, 0.5, 0.5, 0.0, 200)
        at_y = [s for s in segs if s[0, 1] == s[1, 1] == 1.5]
        assert [(float(s[0, 0]), float(s[1, 0])) for s in at_y] == [(0.0, 1.0)]

    def test_self_intersecting_polygon_is_even_odd(self) -> None:
        bowtie = [(0.0, 0.0), (2.0, 2.0), (2.0, 0.0), (0.0, 2.0)]
        segs = grid_segments_in_polygon(bowtie, 0.0, 0.0, 0.5, 0.5, 0.0, 200)
        at_y = sorted((float(s[0, 0]), float(s[1, 0])) for s in segs if s[0, 1] == s[1, 1] == 0.5)
        assert at_y == [(0.0, 0.5), (1.5, 2.0)]

    def test_past_the_cap_an_even_share_of_the_lines_is_drawn(self) -> None:
        segs = grid_segments_in_polygon(_SQUARE, 0.0, 0.0, 0.001, 0.001, 0.0, 10)
        assert len(segs) == 20
        xs = sorted(float(seg[0, 0]) for seg in segs if seg[0, 0] == seg[1, 0])
        assert xs[0] == 0.0 and xs[-1] > 1.8
        # Every one still on a line of the grid.
        assert all(abs(x / 0.001 - round(x / 0.001)) < 1e-6 for x in xs)

    def test_a_corner_too_far_out_to_count_lines_to_draws_none(self) -> None:
        huge = [(0.0, 0.0), (1.7e308, 0.0), (0.0, 1.0)]
        segs = grid_segments_in_polygon(huge, 0.0, 0.0, 0.5, 0.5, 0.0, 200)
        assert not any(seg[0, 0] == seg[1, 0] for seg in segs)

    @pytest.mark.parametrize(
        ("verts", "spacing"),
        [(_SQUARE[:2], 1.0), ([], 1.0), (_SQUARE, 0.0), (_SQUARE, -1.0), (_SQUARE, float("nan"))],
    )
    def test_degenerate_input_gives_no_lines(self, verts: list, spacing: float) -> None:
        assert grid_segments_in_polygon(verts, 0.0, 0.0, spacing, spacing, 0.0, 200).shape == (0, 2, 3)

    def test_a_polygon_between_grid_lines_gets_no_lines(self) -> None:
        sliver = [(0.2, 0.2), (0.8, 0.2), (0.8, 0.8)]
        assert grid_segments_in_polygon(sliver, 0.0, 0.0, 1.0, 1.0, 0.0, 200).shape == (0, 2, 3)

    def test_outline_closes_the_polygon_at_z(self) -> None:
        segs = outline_segments(_SQUARE, 1.5)
        assert segs.shape == (4, 2, 3)
        assert np.all(segs[:, :, 2] == 1.5)
        assert tuple(segs[-1, 1, :2]) == _SQUARE[0]

    def test_outline_needs_three_corners(self) -> None:
        assert outline_segments(_SQUARE[:2], 0.0).shape == (0, 2, 3)


# --------------------------------------------------------------------------- #
# Floor height
# --------------------------------------------------------------------------- #


def _state(**overrides: object) -> OverlayState:
    state = OverlayState()
    state.camera_params = np.array([0.0, -6.0, 2.5, -15.0, 0.0, 0.0, 60.0])
    state.grid_config = (10.0, 6.0, 1.0, 0.0, 0.0, 0.2)
    state.ground_grid_active = True
    state.ground_grid_visible = True
    state.ground_grid_vertices = list(_SQUARE)
    state.ground_grid_z = -0.8
    state.show_ball = False
    state.show_crosshair = False
    state.show_z_line = False
    state.show_ground_circle = False
    for k, v in overrides.items():
        setattr(state, k, v)
    return state


class TestFloorZAt:
    def test_inside_the_polygon_is_the_ground_grid(self) -> None:
        assert floor_z_at(_state(), 1.0, 1.0) == -0.8

    def test_outside_the_polygon_is_the_main_grid(self) -> None:
        assert floor_z_at(_state(), 3.0, 1.0) == 0.2

    def test_inside_the_notch_of_a_concave_polygon_is_the_main_grid(self) -> None:
        assert floor_z_at(_state(ground_grid_vertices=list(_L_SHAPE)), 1.5, 1.5) == 0.2

    def test_inactive_ground_grid_is_ignored(self) -> None:
        assert floor_z_at(_state(ground_grid_active=False), 1.0, 1.0) == 0.2

    def test_a_hidden_ground_grid_still_sets_the_floor(self) -> None:
        assert floor_z_at(_state(ground_grid_visible=False), 1.0, 1.0) == -0.8

    def test_no_grid_and_outside_is_zero(self) -> None:
        assert floor_z_at(_state(grid_config=None), 5.0, 5.0) == 0.0

    def test_main_grid_offset_never_moves_the_polygon(self) -> None:
        shifted = _state(grid_config=(10.0, 6.0, 1.0, 4.0, 3.0, 0.2))
        assert floor_z_at(shifted, 1.0, 1.0) == -0.8
        assert floor_z_at(shifted, 5.0, 4.0) == 0.2


# --------------------------------------------------------------------------- #
# HUD
# --------------------------------------------------------------------------- #


def _marker(x: float = 1.0, y: float = 1.0, z: float = 1.5) -> MarkerOverlayData:
    return MarkerOverlayData(marker_id=1, x=x, y=y, z=z, color="#ff3333")


def _expected(state: OverlayState, pts: list) -> np.ndarray:
    return project(state.camera_params, pts, _W, _H)


class TestMarkerFloorElements:
    @pytest.mark.parametrize(("x", "floor"), [(1.0, -0.8), (3.0, 0.2)])
    def test_z_line_ends_on_the_floor_under_the_marker(self, x: float, floor: float) -> None:
        state = _state(show_z_line=True)
        cr = FakeCairo()
        draw_marker(cr, state, _marker(x=x), _W, _H)
        start, end = _expected(state, [(x, 1.0, 1.5), (x, 1.0, floor)])
        assert cr.move_tos == [pytest.approx(tuple(start))]
        assert cr.line_tos == [pytest.approx(tuple(end))]

    def test_z_line_drawn_with_the_crosshair_still_ends_on_the_floor(self) -> None:
        state = _state(show_z_line=True, show_crosshair=True, crosshair_size=0.3)
        cr = FakeCairo()
        draw_marker(cr, state, _marker(), _W, _H)
        floor = _expected(state, [(1.0, 1.0, -0.8)])[0]
        assert any(end == pytest.approx(tuple(floor)) for end in cr.line_tos)

    def test_z_line_reaches_up_to_a_ground_grid_above_the_marker(self) -> None:
        state = _state(show_z_line=True, ground_grid_z=2.0)
        cr = FakeCairo()
        draw_marker(cr, state, _marker(z=1.5), _W, _H)
        assert cr.line_tos == [pytest.approx(tuple(_expected(state, [(1.0, 1.0, 2.0)])[0]))]

    def test_ground_circle_sits_on_the_ground_grid(self) -> None:
        state = _state(show_ground_circle=True, ground_circle_filled=False, ground_circle_size=0.4)
        cr = FakeCairo()
        draw_marker(cr, state, _marker(), _W, _H)
        ring = _expected(state, ground_circle_world_ring(1.0, 1.0, -0.8, 0.4))
        assert cr.move_tos[0] == pytest.approx(tuple(ring[0]))

    def test_cone_inside_draws_as_on_a_main_grid_at_the_ground_grid_height(self) -> None:
        on_ground = FakeCairo()
        draw_marker(on_ground, _state(marker_style="cone"), _marker(), _W, _H)
        on_main = FakeCairo()
        draw_marker(
            on_main,
            _state(marker_style="cone", ground_grid_active=False, grid_config=(10.0, 6.0, 1.0, 0.0, 0.0, -0.8)),
            _marker(),
            _W,
            _H,
        )
        untouched = FakeCairo()
        draw_marker(untouched, _state(marker_style="cone", ground_grid_active=False), _marker(), _W, _H)
        assert on_ground.calls == on_main.calls
        assert on_ground.calls != untouched.calls

    def test_assist_ghost_ring_sits_on_the_ground_grid(self) -> None:
        state = _state()
        ghost = MarkerOverlayData(marker_id=1, x=1.0, y=1.0, z=1.5, color="#ff3333", is_assist_ghost=True)
        cr = FakeCairo()
        draw_marker(cr, state, ghost, _W, _H)
        ring = _expected(state, ground_circle_world_ring(1.0, 1.0, -0.8, state.ground_circle_size))
        assert pytest.approx(tuple(ring[0])) in cr.move_tos


class TestDrawGroundGrid:
    def test_visible_ground_grid_draws_lines_and_outline_in_one_stroke(self) -> None:
        state = _state(grid_color="#112233", grid_thickness=3, grid_transparency=0.4)
        cr = FakeCairo()
        draw_ground_grid(cr, state, _W, _H)
        # 4 clipped lines + 4 outline edges.
        assert len(cr.move_tos) == 8
        assert cr.strokes == 1
        assert ("line_width", 3.0) in cr.calls
        assert ("rgba", 0x11 / 255, 0x22 / 255, 0x33 / 255, 0.4) in cr.calls

    def test_lines_are_drawn_at_the_ground_grid_height(self) -> None:
        state = _state()
        cr = FakeCairo()
        draw_ground_grid(cr, state, _W, _H)
        assert pytest.approx(tuple(_expected(state, [(0.0, 0.0, -0.8)])[0])) in cr.move_tos

    @pytest.mark.parametrize("override", [{"ground_grid_visible": False}, {"grid_config": None}])
    def test_hidden_or_gridless_draws_nothing(self, override: dict) -> None:
        cr = FakeCairo()
        draw_ground_grid(cr, _state(**override), _W, _H)
        assert cr.calls == []

    def test_a_ground_grid_behind_the_camera_strokes_nothing(self) -> None:
        cr = FakeCairo()
        behind = [(-1.0, -40.0), (1.0, -40.0), (1.0, -42.0), (-1.0, -42.0)]
        draw_ground_grid(cr, _state(ground_grid_vertices=behind), _W, _H)
        assert cr.strokes == 0
        assert cr.move_tos == []

    def test_main_grid_hidden_still_draws_the_ground_grid(self) -> None:
        cr = FakeCairo()
        draw_ground_grid(cr, _state(grid_visible=False), _W, _H)
        assert cr.strokes == 1


class TestMainGridSteps:
    @pytest.mark.parametrize(
        ("size", "spacing", "want"),
        [
            ((10.0, 6.0), 3.0, (10.0 / 3.0, 3.0)),
            ((4.0, 4.0), 1.0, (1.0, 1.0)),
            ((10.0, 10.0), 0.01, (10 / 199, 10 / 199)),
        ],
        ids=["not-a-multiple", "multiple", "capped"],
    )
    def test_the_gaps_are_the_main_grid_s_as_drawn(self, size: tuple, spacing: float, want: tuple) -> None:
        assert main_grid_steps(*size, spacing, 200) == pytest.approx(want)

    @pytest.mark.parametrize("spacing", [0.0, -1.0, float("nan")])
    def test_no_spacing_no_lines(self, spacing: float) -> None:
        assert main_grid_steps(10.0, 6.0, spacing, 200) == (0.0, 0.0)

    def test_ground_grid_lines_lie_on_the_main_grid_s_lines(self) -> None:
        # Width 10 at spacing 3: the main grid draws x = -5, -1.67, 1.67, 5.
        cover = [(-6.0, -4.0), (6.0, -4.0), (6.0, 4.0), (-6.0, 4.0)]
        step_x, step_y = main_grid_steps(10.0, 6.0, 3.0, 200)
        segs = grid_segments_in_polygon(cover, -5.0, -3.0, step_x, step_y, 0.0, 200)
        xs = sorted({round(float(seg[0, 0]), 6) for seg in segs if seg[0, 0] == seg[1, 0]})
        assert [x for x in xs if -5.0 <= x <= 5.0] == [round(v, 6) for v in np.linspace(-5.0, 5.0, 4)]


class TestFloorHeight:
    @staticmethod
    def _cfg(*, experimental: bool = True, enabled: bool = True, vertices: list | None = None):
        from openfollow.configuration import AppConfig, GroundGridConfig

        cfg = AppConfig()
        cfg.ui.shown_features = ["ground_grid"] if experimental else []
        cfg.grid.z_offset = 0.2
        cfg.ground_grid = GroundGridConfig(
            enabled=enabled, z_offset=-1.5, vertices=vertices or [list(v) for v in _SQUARE]
        )
        return cfg

    def test_inside_a_dropping_ground_grid_the_floor_is_its_height(self) -> None:
        assert floor_height(self._cfg(), 1.0, 1.0) == -1.5
        assert floor_height(self._cfg(), 3.0, 1.0) == 0.2

    @pytest.mark.parametrize(
        "change",
        [{"experimental": False}, {"enabled": False}, {"vertices": [[0.0, 0.0], [2.0, 0.0]]}],
        ids=["experimental-off", "ground-grid-off", "two-corners"],
    )
    def test_otherwise_the_floor_is_the_main_grid_s(self, change: dict) -> None:
        assert floor_height(self._cfg(**change), 1.0, 1.0) == 0.2
