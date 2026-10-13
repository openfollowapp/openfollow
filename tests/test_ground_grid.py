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
from openfollow.scene.ground_grid import grid_segments_in_polygon, outline_segments
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
        segs = grid_segments_in_polygon(_SQUARE, 0.0, 0.0, 1.0, -0.5, 200)
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
        segs = grid_segments_in_polygon(_SQUARE, 0.25, 0.5, 1.0, 0.0, 200)
        ys = {round(float(s[0, 1]), 6) for s in segs if s[0, 1] == s[1, 1]}
        xs = {round(float(s[0, 0]), 6) for s in segs if s[0, 0] == s[1, 0]}
        assert ys == {0.5, 1.5}
        assert xs == {0.25, 1.25}

    def test_concave_polygon_splits_a_line_at_the_notch(self) -> None:
        segs = grid_segments_in_polygon(_L_SHAPE, 0.0, 0.0, 0.5, 0.0, 200)
        at_y = [s for s in segs if s[0, 1] == s[1, 1] == 1.5]
        assert [(float(s[0, 0]), float(s[1, 0])) for s in at_y] == [(0.0, 1.0)]

    def test_self_intersecting_polygon_is_even_odd(self) -> None:
        bowtie = [(0.0, 0.0), (2.0, 2.0), (2.0, 0.0), (0.0, 2.0)]
        segs = grid_segments_in_polygon(bowtie, 0.0, 0.0, 0.5, 0.0, 200)
        at_y = sorted((float(s[0, 0]), float(s[1, 0])) for s in segs if s[0, 1] == s[1, 1] == 0.5)
        assert at_y == [(0.0, 0.5), (1.5, 2.0)]

    def test_line_count_is_capped_per_axis(self) -> None:
        segs = grid_segments_in_polygon(_SQUARE, 0.0, 0.0, 0.001, 0.0, 10)
        assert len(segs) == 20

    @pytest.mark.parametrize(
        ("verts", "spacing"),
        [(_SQUARE[:2], 1.0), ([], 1.0), (_SQUARE, 0.0), (_SQUARE, -1.0), (_SQUARE, float("nan"))],
    )
    def test_degenerate_input_gives_no_lines(self, verts: list, spacing: float) -> None:
        assert grid_segments_in_polygon(verts, 0.0, 0.0, spacing, 0.0, 200).shape == (0, 2, 3)

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
    def test_dropline_ends_on_the_floor_under_the_marker(self, x: float, floor: float) -> None:
        state = _state(show_z_line=True)
        cr = FakeCairo()
        draw_marker(cr, state, _marker(x=x), _W, _H)
        start, end = _expected(state, [(x, 1.0, 1.5), (x, 1.0, floor)])
        assert cr.move_tos == [pytest.approx(tuple(start))]
        assert cr.line_tos == [pytest.approx(tuple(end))]

    def test_dropline_reaches_up_to_a_ground_grid_above_the_marker(self) -> None:
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

    def test_main_grid_hidden_still_draws_the_ground_grid(self) -> None:
        cr = FakeCairo()
        draw_ground_grid(cr, _state(grid_visible=False), _W, _H)
        assert cr.strokes == 1
