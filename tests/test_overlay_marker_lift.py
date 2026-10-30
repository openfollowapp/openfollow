# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""A zone's lift on the Operator Screen: the marker at its sent Z, its floor elements on the raised floor."""

from __future__ import annotations

import numpy as np
import pytest

from openfollow.runtime.overlay_draw_scene import ConeCache, draw_marker, marker_floor_z, project
from openfollow.runtime.overlay_state import MarkerOverlayData, OverlayState
from openfollow.scene.solver import ground_circle_world_ring
from tests._fake_cairo import FakeCairo

pytestmark = pytest.mark.unit

_W, _H = 1920, 1080
_STAGE = 0.2


def _state(**overrides: object) -> OverlayState:
    state = OverlayState()
    state.camera_params = np.array([0.0, -6.0, 2.5, -15.0, 0.0, 0.0, 60.0])
    state.grid_config = (10.0, 6.0, 1.0, 0.0, 0.0, _STAGE)
    state.show_ball = False
    state.show_crosshair = False
    state.show_z_line = False
    state.show_ground_circle = False
    for k, v in overrides.items():
        setattr(state, k, v)
    return state


def _marker(lift: float = 0.0, *, z: float = 1.5, ghost: bool = False) -> MarkerOverlayData:
    # ``z`` is the sent Z: the operator's own plus the lift, as the overlay state builds it.
    return MarkerOverlayData(marker_id=1, x=1.0, y=1.0, z=z, color="#ff3333", lift=lift, is_assist_ghost=ghost)


def _seen(state: OverlayState, pts: list) -> np.ndarray:
    return project(state.camera_params, pts, _W, _H)


class TestMarkerFloorZ:
    def test_the_floor_is_raised_by_the_lift(self) -> None:
        assert marker_floor_z(_state(), _marker(0.5)) == pytest.approx(_STAGE + 0.5)

    def test_without_a_lift_it_is_the_stage(self) -> None:
        assert marker_floor_z(_state(), _marker()) == _STAGE


class TestALiftedMarker:
    def test_the_z_line_runs_from_the_sent_z_to_the_raised_floor(self) -> None:
        state = _state(show_z_line=True)
        cr = FakeCairo()
        draw_marker(cr, state, _marker(0.5, z=2.0), _W, _H)
        top, floor = _seen(state, [(1.0, 1.0, 2.0), (1.0, 1.0, _STAGE + 0.5)])
        assert cr.move_tos == [pytest.approx(tuple(top))]
        assert cr.line_tos == [pytest.approx(tuple(floor))]

    def test_the_ground_circle_stands_on_the_raised_floor(self) -> None:
        state = _state(show_ground_circle=True, ground_circle_filled=False, ground_circle_size=0.4)
        cr = FakeCairo()
        draw_marker(cr, state, _marker(0.5), _W, _H)
        ring = _seen(state, ground_circle_world_ring(1.0, 1.0, _STAGE + 0.5, 0.4))
        assert cr.move_tos[0] == pytest.approx(tuple(ring[0]))

    def test_a_cone_is_drawn_as_on_a_stage_raised_by_the_lift(self) -> None:
        lifted = FakeCairo()
        draw_marker(lifted, _state(marker_style="cone"), _marker(0.5, z=2.0), _W, _H)
        raised_stage = FakeCairo()
        draw_marker(
            raised_stage,
            _state(marker_style="cone", grid_config=(10.0, 6.0, 1.0, 0.0, 0.0, _STAGE + 0.5)),
            _marker(z=2.0),
            _W,
            _H,
        )
        unlifted = FakeCairo()
        draw_marker(unlifted, _state(marker_style="cone"), _marker(z=2.0), _W, _H)
        assert lifted.calls == raised_stage.calls
        assert lifted.calls != unlifted.calls

    def test_the_assist_ghost_ring_stands_on_the_raised_floor(self) -> None:
        state = _state()
        cr = FakeCairo()
        draw_marker(cr, state, _marker(0.5, ghost=True), _W, _H)
        ring = _seen(state, ground_circle_world_ring(1.0, 1.0, _STAGE + 0.5, state.ground_circle_size))
        assert pytest.approx(tuple(ring[0])) in cr.move_tos

    def test_a_cached_cone_is_drawn_again_when_the_lift_changes(self) -> None:
        cache = ConeCache()
        state = _state(marker_style="cone")
        first, second = FakeCairo(), FakeCairo()
        cache.draw(first, state, _marker(0.0, z=2.0), _W, _H, (1.0, 0.2, 0.2), 0.8, 2.0)
        cache.draw(second, state, _marker(0.5, z=2.0), _W, _H, (1.0, 0.2, 0.2), 0.8, 2.0)
        assert len(second.surfaces) == 1 and second.surfaces[0] is not first.surfaces[0]
