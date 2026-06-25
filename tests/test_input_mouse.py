# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for the mouse input handler.

Control is grabbed by left-clicking a marker's ground circle and released
with a right-click; movement is applied each frame via ``update()`` so the
test pattern is: grab → move → ``update()`` → assert.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from openfollow.configuration import (
    AppConfig,
    CameraConfig,
    GridConfig,
)
from openfollow.input.mouse import MouseHandler
from openfollow.psn.marker import Marker
from openfollow.scene.solver import project_points, unproject_to_plane

pytestmark = pytest.mark.unit


class _DummyServer:
    def __init__(self) -> None:
        self.markers: dict[int, Marker] = {}

    def add_marker(self, tid: int) -> Marker:
        t = Marker(tid, f"Marker {tid}")
        self.markers[tid] = t
        return t

    def get_marker(self, tid: int) -> Marker | None:
        return self.markers.get(tid)


class _DummyCamera:
    """Minimal stand-in for scene.camera.Camera."""

    def __init__(self, cfg: CameraConfig) -> None:
        self._cfg = cfg

    def to_config(self) -> CameraConfig:
        return self._cfg


class _DummyCanvas:
    def __init__(self, w: int, h: int) -> None:
        self._size = (w, h)
        self.calls = 0

    def get_canvas_size(self) -> tuple[int, int]:
        self.calls += 1
        return self._size


class _DummyApp:
    def __init__(self) -> None:
        self._config = AppConfig()
        self._config.grid = GridConfig(
            width=20.0,
            depth=15.0,
            x_offset=0.0,
            y_offset=0.0,
            z_offset=0.0,
        )
        self._config.camera = CameraConfig(
            pos_x=0.0,
            pos_y=-10.0,
            pos_z=5.0,
            pitch=-20.0,
            yaw=0.0,
            roll=0.0,
            fov=60.0,
        )
        self._config.controller.mouse_enabled = True
        self._camera = _DummyCamera(self._config.camera)
        self._canvas = None

        self._server = _DummyServer()
        self._server.add_marker(1).set_pos(0.0, 0.0, 0.0)

        self._controlled_ids = [1]
        self._selected_id = 1
        self._assist_manual: dict[int, Marker] = {}

    def _get_default_marker_position(self) -> tuple[float, float, float]:
        return (7.0, -3.0, 1.6)


def _cam_buffer(app: _DummyApp) -> np.ndarray:
    c = app._config.camera
    return np.array(
        [c.pos_x, c.pos_y, c.pos_z, c.pitch, c.yaw, c.roll, c.fov],
        dtype=np.float64,
    )


def _canvas_size(app: _DummyApp) -> tuple[int, int]:
    if app._canvas is not None:
        return app._canvas.get_canvas_size()
    return app._config.window_width, app._config.window_height


def _ground_center(app: _DummyApp, tid: int) -> tuple[float, float]:
    """Screen pixel where marker *tid*'s ground-circle centre projects."""
    m = app._server.get_marker(tid)
    z_off = app._config.grid.z_offset
    w, h = _canvas_size(app)
    scr = project_points(
        _cam_buffer(app),
        np.array([[m.pos[0], m.pos[1], z_off]], dtype=np.float64),
        float(w),
        float(h),
    )
    return float(scr[0, 0]), float(scr[0, 1])


def _world_at(app: _DummyApp, x: float, y: float) -> tuple[float, float]:
    """Stage-plane world point a screen pixel unprojects to."""
    w, h = _canvas_size(app)
    out = unproject_to_plane(
        _cam_buffer(app),
        np.array([[x, y]], dtype=np.float64),
        float(w),
        float(h),
        app._config.grid.z_offset,
    )
    return float(out[0, 0]), float(out[0, 1])


def _grab(handler: MouseHandler, app: _DummyApp, tid: int = 1) -> bool:
    cx, cy = _ground_center(app, tid)
    return handler.on_pointer_down(cx, cy, 1)


# ---------------------------------------------------------------------------


class TestGrab:
    """Left-click on a ground circle grabs; right-click releases."""

    def test_starts_inactive(self) -> None:
        handler = MouseHandler(_DummyApp())
        assert handler.active is False

    def test_click_ground_circle_grabs(self) -> None:
        app = _DummyApp()
        handler = MouseHandler(app)
        assert _grab(handler, app) is True
        assert handler.active is True
        assert app._selected_id == 1

    def test_click_empty_space_does_not_grab(self) -> None:
        app = _DummyApp()
        handler = MouseHandler(app)
        # Far corner – nowhere near the marker's projected ground circle.
        consumed = handler.on_pointer_down(5, 5, 1)
        assert consumed is False
        assert handler.active is False

    def test_grab_does_not_move_marker(self) -> None:
        app = _DummyApp()
        handler = MouseHandler(app)
        before = app._server.get_marker(1).pos
        _grab(handler, app)
        handler.update()  # even after a frame tick, no move without a cursor move
        assert app._server.get_marker(1).pos == before

    def test_grab_selects_clicked_marker(self) -> None:
        app = _DummyApp()
        app._server.add_marker(2).set_pos(4.0, 0.0, 0.0)
        app._controlled_ids = [1, 2]
        app._selected_id = 1
        handler = MouseHandler(app)
        assert _grab(handler, app, tid=2) is True
        assert app._selected_id == 2

    def test_grab_with_no_prior_selection(self) -> None:
        # Grab works even when nothing was selected yet – it selects the marker.
        app = _DummyApp()
        app._selected_id = None
        handler = MouseHandler(app)
        assert _grab(handler, app) is True
        assert app._selected_id == 1

    def test_right_click_releases(self) -> None:
        app = _DummyApp()
        handler = MouseHandler(app)
        _grab(handler, app)
        consumed = handler.on_pointer_down(640, 360, 3)
        assert consumed is True
        assert handler.active is False

    def test_right_click_when_inactive_not_consumed(self) -> None:
        handler = MouseHandler(_DummyApp())
        assert handler.on_pointer_down(640, 360, 3) is False

    def test_move_ignored_when_inactive(self) -> None:
        handler = MouseHandler(_DummyApp())
        assert handler.on_pointer_move(640, 360) is False

    def test_ground_circle_off_uses_pixel_fallback(self) -> None:
        app = _DummyApp()
        app._config.marker.ground_circle = False
        handler = MouseHandler(app)
        cx, cy = _ground_center(app, 1)
        # Exact centre is within the touch radius.
        assert handler.on_pointer_down(cx, cy, 1) is True

    def test_ground_circle_off_misses_beyond_touch_radius(self) -> None:
        app = _DummyApp()
        app._config.marker.ground_circle = False
        handler = MouseHandler(app)
        cx, cy = _ground_center(app, 1)
        # 40 px away, outside the ~14 px touch radius and no polygon to fall on.
        assert handler.on_pointer_down(cx + 40, cy, 1) is False

    def test_deactivate_disarms(self) -> None:
        app = _DummyApp()
        handler = MouseHandler(app)
        _grab(handler, app)
        assert handler.active is True
        handler.deactivate()
        assert handler.active is False
        assert handler.on_pointer_move(100, 100) is False

    def test_deactivate_is_noop_when_already_inactive(self) -> None:
        handler = MouseHandler(_DummyApp())
        handler.deactivate()  # must not raise
        assert handler.active is False


class TestPosition:
    """Movement is applied via ``update`` after a cursor move."""

    def test_move_then_update_moves_marker(self) -> None:
        app = _DummyApp()
        handler = MouseHandler(app)
        cx, cy = _ground_center(app, 1)
        handler.on_pointer_down(cx, cy, 1)
        handler.on_pointer_move(cx + 80, cy + 40)
        handler.update()
        x, y, z = app._server.get_marker(1).pos
        assert math.isfinite(x) and math.isfinite(y)
        assert (round(x, 6), round(y, 6)) != (0.0, 0.0)
        assert z == 0.0  # wheel owns Z

    def test_default_smoothing_is_instant(self) -> None:
        app = _DummyApp()
        handler = MouseHandler(app)
        cx, cy = _ground_center(app, 1)
        handler.on_pointer_down(cx, cy, 1)
        tx, ty = cx + 90, cy - 30
        handler.on_pointer_move(tx, ty)
        handler.update()
        wx, wy = _world_at(app, tx, ty)
        x, y, _ = app._server.get_marker(1).pos
        assert x == pytest.approx(wx)
        assert y == pytest.approx(wy)

    def test_smoothing_glides_partway(self) -> None:
        app = _DummyApp()
        app._config.controller.mouse_smoothing = 0.5
        handler = MouseHandler(app)
        cx, cy = _ground_center(app, 1)
        handler.on_pointer_down(cx, cy, 1)
        tx, ty = cx + 120, cy + 60
        handler.on_pointer_move(tx, ty)
        wx, wy = _world_at(app, tx, ty)

        handler.update()  # seed=(0,0); alpha 0.5 → halfway
        x1, y1, _ = app._server.get_marker(1).pos
        assert x1 == pytest.approx(0.5 * wx)
        assert y1 == pytest.approx(0.5 * wy)

        handler.update()  # 0.75 of the way
        x2, y2, _ = app._server.get_marker(1).pos
        assert x2 == pytest.approx(0.75 * wx)
        assert abs(x2 - wx) < abs(x1 - wx)  # monotonic approach

    def test_higher_smoothing_glides_more_slowly(self) -> None:
        # The field reads "0 = instant, higher = smoother": a high value yields
        # a small glide alpha (= 1 - smoothing), so the first frame barely moves.
        app = _DummyApp()
        app._config.controller.mouse_smoothing = 0.75  # alpha 0.25
        handler = MouseHandler(app)
        cx, cy = _ground_center(app, 1)
        handler.on_pointer_down(cx, cy, 1)
        tx, ty = cx + 120, cy + 60
        handler.on_pointer_move(tx, ty)
        wx, wy = _world_at(app, tx, ty)

        handler.update()  # seed=(0,0); alpha 0.25 → a quarter of the way
        x1, y1, _ = app._server.get_marker(1).pos
        assert x1 == pytest.approx(0.25 * wx)
        assert y1 == pytest.approx(0.25 * wy)

    def test_max_smoothing_creeps_but_never_freezes(self) -> None:
        # smoothing 1.0 would invert to alpha 0; the floor keeps it converging
        # (very slowly) instead of freezing short of the target.
        app = _DummyApp()
        app._config.controller.mouse_smoothing = 1.0  # alpha floored to 0.01
        handler = MouseHandler(app)
        cx, cy = _ground_center(app, 1)
        handler.on_pointer_down(cx, cy, 1)
        tx, ty = cx + 120, cy + 60
        handler.on_pointer_move(tx, ty)
        wx, wy = _world_at(app, tx, ty)

        handler.update()  # seed=(0,0); alpha 0.01 → 1% of the way, not frozen
        x1, _y1, _ = app._server.get_marker(1).pos
        assert x1 == pytest.approx(0.01 * wx)
        assert 0.0 < abs(x1) < abs(wx)  # moved toward the target, not stuck at 0

        handler.update()  # keeps creeping closer
        x2, _y2, _ = app._server.get_marker(1).pos
        assert abs(x2 - wx) < abs(x1 - wx)

    def test_uses_canvas_size_when_available(self) -> None:
        app = _DummyApp()
        app._canvas = _DummyCanvas(1280, 720)
        handler = MouseHandler(app)
        _grab(handler, app)
        assert app._canvas.calls > 0

    def test_second_update_is_noop_once_settled(self) -> None:
        app = _DummyApp()
        handler = MouseHandler(app)
        cx, cy = _ground_center(app, 1)
        handler.on_pointer_down(cx, cy, 1)
        handler.on_pointer_move(cx + 80, cy + 40)
        handler.update()  # snaps to target
        settled = app._server.get_marker(1).pos
        handler.update()  # already at target → no further write
        assert app._server.get_marker(1).pos == settled


class TestHysteresis:
    """The pixel deadband suppresses sub-threshold cursor jitter."""

    def test_below_threshold_holds(self) -> None:
        app = _DummyApp()
        app._config.controller.mouse_hysteresis_px = 50
        handler = MouseHandler(app)
        cx, cy = _ground_center(app, 1)
        handler.on_pointer_down(cx, cy, 1)
        before = app._server.get_marker(1).pos
        # 20 px move – inside the 50 px deadband, consumed but no target update.
        assert handler.on_pointer_move(cx + 20, cy) is True
        handler.update()
        assert app._server.get_marker(1).pos == before

    def test_above_threshold_moves(self) -> None:
        app = _DummyApp()
        app._config.controller.mouse_hysteresis_px = 10
        handler = MouseHandler(app)
        cx, cy = _ground_center(app, 1)
        handler.on_pointer_down(cx, cy, 1)
        handler.on_pointer_move(cx + 60, cy + 30)
        handler.update()
        x, y, _ = app._server.get_marker(1).pos
        assert (round(x, 6), round(y, 6)) != (0.0, 0.0)

    def test_subthreshold_jitter_never_accumulates(self) -> None:
        app = _DummyApp()
        app._config.controller.mouse_hysteresis_px = 50
        handler = MouseHandler(app)
        cx, cy = _ground_center(app, 1)
        handler.on_pointer_down(cx, cy, 1)
        before = app._server.get_marker(1).pos
        # Oscillate within the deadband; the anchor never commits, so nothing
        # accumulates into a move.
        for off in (10, -10, 25, -25, 40, -40):
            handler.on_pointer_move(cx + off, cy)
            handler.update()
        assert app._server.get_marker(1).pos == before

    def test_zero_threshold_applies_every_move(self) -> None:
        app = _DummyApp()
        app._config.controller.mouse_hysteresis_px = 0
        handler = MouseHandler(app)
        cx, cy = _ground_center(app, 1)
        handler.on_pointer_down(cx, cy, 1)
        handler.on_pointer_move(cx + 5, cy + 5)
        handler.update()
        x, y, _ = app._server.get_marker(1).pos
        assert (round(x, 6), round(y, 6)) != (0.0, 0.0)


def _screen_for_world(app: _DummyApp, wx: float, wy: float) -> tuple[float, float]:
    """Screen pixel that a stage-plane world point projects to."""
    z_off = app._config.grid.z_offset
    w, h = _canvas_size(app)
    scr = project_points(
        _cam_buffer(app),
        np.array([[wx, wy, z_off]], dtype=np.float64),
        float(w),
        float(h),
    )
    return float(scr[0, 0]), float(scr[0, 1])


class TestMaxY:
    """Targets past the upstage (Y+) cap are ignored; downstage is unaffected."""

    def test_beyond_max_y_is_ignored(self) -> None:
        app = _DummyApp()
        app._config.controller.mouse_max_y = 3.0
        handler = MouseHandler(app)
        cx, cy = _ground_center(app, 1)
        handler.on_pointer_down(cx, cy, 1)
        before = app._server.get_marker(1).pos
        handler.on_pointer_move(*_screen_for_world(app, 0.0, 6.0))  # 6 m upstage > 3 m cap
        handler.update()
        assert app._server.get_marker(1).pos == before

    def test_within_max_y_applies(self) -> None:
        app = _DummyApp()
        app._config.controller.mouse_max_y = 10.0
        handler = MouseHandler(app)
        cx, cy = _ground_center(app, 1)
        handler.on_pointer_down(cx, cy, 1)
        handler.on_pointer_move(*_screen_for_world(app, 0.0, 6.0))
        handler.update()
        _, y, _ = app._server.get_marker(1).pos
        assert y == pytest.approx(6.0, abs=1e-3)

    def test_zero_max_y_is_unlimited(self) -> None:
        app = _DummyApp()
        app._config.controller.mouse_max_y = 0.0
        handler = MouseHandler(app)
        cx, cy = _ground_center(app, 1)
        handler.on_pointer_down(cx, cy, 1)
        handler.on_pointer_move(*_screen_for_world(app, 0.0, 6.0))
        handler.update()
        _, y, _ = app._server.get_marker(1).pos
        assert y == pytest.approx(6.0, abs=1e-3)

    def test_downstage_target_not_capped(self) -> None:
        # The cap is on Y+ (upstage) only; a downstage target (Y below the cap)
        # always applies, even with a low cap.
        app = _DummyApp()
        app._config.controller.mouse_max_y = 1.0
        handler = MouseHandler(app)
        cx, cy = _ground_center(app, 1)
        handler.on_pointer_down(cx, cy, 1)
        handler.on_pointer_move(*_screen_for_world(app, 0.0, -2.0))  # 2 m downstage
        handler.update()
        _, y, _ = app._server.get_marker(1).pos
        assert y == pytest.approx(-2.0, abs=1e-3)

    def test_capped_move_does_not_strand_hysteresis_anchor(self) -> None:
        # A move rejected by the Y+ cap must NOT advance the hysteresis anchor,
        # otherwise a later small correction is wrongly measured from the
        # rejected point and swallowed by the deadband.
        app = _DummyApp()
        app._config.controller.mouse_hysteresis_px = 60
        app._config.controller.mouse_max_y = 3.0
        handler = MouseHandler(app)
        cx, cy = _ground_center(app, 1)
        handler.on_pointer_down(cx, cy, 1)
        handler.on_pointer_move(cx + 100, cy)  # commits (100 px > 60 px deadband)
        handler.update()
        committed = app._server.get_marker(1).pos
        # Far upstage move – rejected by the cap; must not move the anchor.
        handler.on_pointer_move(*_screen_for_world(app, 0.0, 10.0))
        # A 20 px jitter from the *committed* point stays inside the deadband; if
        # the rejected move had stranded the anchor this would commit + move.
        handler.on_pointer_move(cx + 120, cy)
        handler.update()
        assert app._server.get_marker(1).pos == committed


class TestWheel:
    """Scroll wheel adjusts Z, gated by config."""

    def test_wheel_ignored_when_inactive(self) -> None:
        handler = MouseHandler(_DummyApp())
        assert handler.on_wheel(1.0) is False

    def test_wheel_adjusts_z(self) -> None:
        app = _DummyApp()
        app._server.get_marker(1).set_pos(1.0, 2.0, 3.0)
        handler = MouseHandler(app)
        _grab(handler, app)
        assert handler.on_wheel(1.0) is True
        assert app._server.get_marker(1).pos[2] == pytest.approx(3.1)

    def test_wheel_negative(self) -> None:
        app = _DummyApp()
        app._server.get_marker(1).set_pos(1.0, 2.0, 3.0)
        handler = MouseHandler(app)
        _grab(handler, app)
        handler.on_wheel(-2.0)
        assert app._server.get_marker(1).pos[2] == pytest.approx(2.8)

    def test_wheel_disabled(self) -> None:
        app = _DummyApp()
        app._config.controller.mouse_wheel_z_enabled = False
        app._server.get_marker(1).set_pos(1.0, 2.0, 3.0)
        handler = MouseHandler(app)
        _grab(handler, app)
        assert handler.on_wheel(1.0) is False
        assert app._server.get_marker(1).pos[2] == 3.0

    def test_wheel_inverted(self) -> None:
        app = _DummyApp()
        app._config.controller.mouse_wheel_invert = True
        app._server.get_marker(1).set_pos(1.0, 2.0, 3.0)
        handler = MouseHandler(app)
        _grab(handler, app)
        handler.on_wheel(1.0)
        assert app._server.get_marker(1).pos[2] == pytest.approx(2.9)

    def test_wheel_custom_step(self) -> None:
        app = _DummyApp()
        app._config.controller.mouse_wheel_z_step = 0.5
        app._server.get_marker(1).set_pos(1.0, 2.0, 3.0)
        handler = MouseHandler(app)
        _grab(handler, app)
        handler.on_wheel(2.0)
        assert app._server.get_marker(1).pos[2] == pytest.approx(4.0)


class _FakeClock:
    """Controllable monotonic clock for double-click timing."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


_DEFAULT_POS = (7.0, -3.0, 1.6)  # _DummyApp._get_default_marker_position


class TestDoubleClickReset:
    """Double right-clicking resets the controlled marker to default and
    releases it (right-click is the release button)."""

    def _handler_with_clock(self, app: _DummyApp) -> tuple[MouseHandler, _FakeClock]:
        handler = MouseHandler(app)
        clock = _FakeClock()
        handler._clock = clock
        return handler, clock

    def _grab_then(self, handler: MouseHandler, app: _DummyApp) -> tuple[float, float]:
        cx, cy = _ground_center(app, 1)
        handler.on_pointer_down(cx, cy, 1)  # grab marker 1
        return cx, cy

    def test_double_right_click_resets_to_default(self) -> None:
        app = _DummyApp()
        handler, clock = self._handler_with_clock(app)
        cx, cy = self._grab_then(handler, app)
        clock.t = 0.1
        handler.on_pointer_down(cx, cy, 3)  # right-click #1 (release)
        clock.t = 0.2
        handler.on_pointer_down(cx, cy, 3)  # right-click #2 → reset
        assert app._server.get_marker(1).pos == _DEFAULT_POS

    def test_double_right_click_releases_control(self) -> None:
        app = _DummyApp()
        handler, clock = self._handler_with_clock(app)
        cx, cy = self._grab_then(handler, app)
        clock.t = 0.1
        handler.on_pointer_down(cx, cy, 3)
        clock.t = 0.2
        handler.on_pointer_down(cx, cy, 3)  # reset
        assert handler.active is False
        assert handler.on_pointer_move(cx + 200, cy + 100) is False
        handler.update()
        assert app._server.get_marker(1).pos == _DEFAULT_POS

    def test_single_right_click_does_not_reset(self) -> None:
        app = _DummyApp()
        handler, _clock = self._handler_with_clock(app)
        cx, cy = self._grab_then(handler, app)
        handler.on_pointer_down(cx, cy, 3)  # one right-click → release only
        assert handler.active is False
        assert app._server.get_marker(1).pos == (0.0, 0.0, 0.0)

    def test_slow_second_right_click_does_not_reset(self) -> None:
        app = _DummyApp()
        handler, clock = self._handler_with_clock(app)
        cx, cy = self._grab_then(handler, app)
        clock.t = 0.1
        handler.on_pointer_down(cx, cy, 3)
        clock.t = 1.0  # outside the 0.4 s window
        handler.on_pointer_down(cx, cy, 3)
        assert app._server.get_marker(1).pos == (0.0, 0.0, 0.0)

    def test_far_second_right_click_does_not_reset(self) -> None:
        app = _DummyApp()
        handler, clock = self._handler_with_clock(app)
        cx, cy = self._grab_then(handler, app)
        clock.t = 0.1
        handler.on_pointer_down(cx, cy, 3)
        clock.t = 0.2
        handler.on_pointer_down(cx + 25, cy, 3)  # > 8 px from the first right-click
        assert app._server.get_marker(1).pos == (0.0, 0.0, 0.0)

    def test_disabled_does_not_reset(self) -> None:
        app = _DummyApp()
        app._config.controller.mouse_double_click_reset = False
        handler, clock = self._handler_with_clock(app)
        cx, cy = self._grab_then(handler, app)
        clock.t = 0.1
        handler.on_pointer_down(cx, cy, 3)
        clock.t = 0.2
        handler.on_pointer_down(cx, cy, 3)
        assert app._server.get_marker(1).pos == (0.0, 0.0, 0.0)

    def test_double_right_click_without_grab_does_not_reset(self) -> None:
        # Reset only arms when the first right-click released an active mouse
        # grab. Two stray right-clicks with no grab (e.g. while steering by
        # keyboard) must not reset the selected marker.
        app = _DummyApp()
        handler, clock = self._handler_with_clock(app)
        cx, cy = _ground_center(app, 1)
        handler.on_pointer_down(cx, cy, 3)  # right-click #1 – nothing to release
        clock.t = 0.1
        handler.on_pointer_down(cx, cy, 3)  # right-click #2 within window
        assert app._server.get_marker(1).pos == (0.0, 0.0, 0.0)

    def test_new_grab_between_right_clicks_does_not_reset(self) -> None:
        # The double-click window must not straddle two separate grab/release
        # cycles: grab -> release(#1) -> grab -> release(#2) within the window is
        # two intentional releases, not a double-click reset. A fresh grab
        # disarms the pending reset.
        app = _DummyApp()
        handler, clock = self._handler_with_clock(app)
        cx, cy = self._grab_then(handler, app)  # grab A
        clock.t = 0.05
        handler.on_pointer_down(cx, cy, 3)  # release A (#1)
        clock.t = 0.10
        handler.on_pointer_down(cx, cy, 1)  # grab B (re-grab, disarms)
        clock.t = 0.15
        handler.on_pointer_down(cx, cy, 3)  # release B (#2) – must NOT reset
        assert app._server.get_marker(1).pos == (0.0, 0.0, 0.0)
        assert handler.active is False

    def test_left_double_click_no_longer_resets(self) -> None:
        # Reset lives on the right button now; two left-clicks just grab.
        app = _DummyApp()
        handler, clock = self._handler_with_clock(app)
        cx, cy = _ground_center(app, 1)
        handler.on_pointer_down(cx, cy, 1)
        clock.t = 0.1
        handler.on_pointer_down(cx, cy, 1)
        assert app._server.get_marker(1).pos == (0.0, 0.0, 0.0)
        assert handler.active is True

    def test_reset_is_a_noop_without_a_selected_marker(self) -> None:
        # Defensive guard: the reset must not crash or move anything when there
        # is no selection.
        app = _DummyApp()
        app._selected_id = None
        handler = MouseHandler(app)
        handler._reset_to_default()
        assert app._server.get_marker(1).pos == (0.0, 0.0, 0.0)

    def test_double_right_click_resets_assist_ghost_not_registered(self) -> None:
        app = _DummyApp()
        app._config.detection.enabled = True
        app._config.detection.pin_marker = True
        app._config.detection.pin_mode = "assist"
        handler, clock = self._handler_with_clock(app)
        cx, cy = self._grab_then(handler, app)
        clock.t = 0.1
        handler.on_pointer_down(cx, cy, 3)
        clock.t = 0.2
        handler.on_pointer_down(cx, cy, 3)
        # The manual ghost was reset; the registered (broadcast) marker is untouched.
        assert app._assist_manual[1].pos == _DEFAULT_POS
        assert app._server.get_marker(1).pos == (0.0, 0.0, 0.0)


class TestAssistRedirect:
    """In detection assist mode the mouse steers the manual ghost anchor, not
    the registered (AI-corrected, broadcast) marker."""

    @staticmethod
    def _assist_app() -> _DummyApp:
        app = _DummyApp()
        app._config.detection.enabled = True
        app._config.detection.pin_marker = True
        app._config.detection.pin_mode = "assist"
        return app

    def test_move_steers_ghost_not_registered_marker(self) -> None:
        app = self._assist_app()
        handler = MouseHandler(app)
        cx, cy = _ground_center(app, 1)
        handler.on_pointer_down(cx, cy, 1)
        handler.on_pointer_move(cx + 80, cy + 40)
        handler.update()

        # The registered marker (broadcast + zones, owned by the pin) is untouched.
        assert app._server.get_marker(1).pos == (0.0, 0.0, 0.0)
        # The operator's drag moved the manual ghost instead.
        ghost = app._assist_manual[1]
        gx, gy, _ = ghost.pos
        assert math.isfinite(gx) and math.isfinite(gy)
        assert (round(gx, 6), round(gy, 6)) != (0.0, 0.0)

    def test_wheel_adjusts_ghost_z_not_registered_marker(self) -> None:
        app = self._assist_app()
        handler = MouseHandler(app)
        _grab(handler, app)
        # Wheel lazily creates the ghost (seeded at the registered pos) and
        # raises its Z; the registered marker's Z is untouched.
        handler.on_wheel(1.0)
        ghost = app._assist_manual[1]
        assert ghost.pos[2] == pytest.approx(0.1)
        assert app._server.get_marker(1).pos[2] == 0.0

    def test_replace_mode_steers_registered_marker(self) -> None:
        # Back-compat: with assist off, the mouse drives the registered marker
        # directly and never creates a ghost.
        app = _DummyApp()  # default detection: pin_marker False / replace
        handler = MouseHandler(app)
        cx, cy = _ground_center(app, 1)
        handler.on_pointer_down(cx, cy, 1)
        handler.on_pointer_move(cx + 80, cy + 40)
        handler.update()

        assert app._server.get_marker(1).pos != (0.0, 0.0, 0.0)
        assert app._assist_manual == {}


class TestEdges:
    """Defensive guards: empty roster, missing/behind-camera markers, server
    None, degenerate canvas, off-plane unprojection."""

    def test_middle_button_is_not_consumed(self) -> None:
        handler = MouseHandler(_DummyApp())
        assert handler.on_pointer_down(640, 360, 2) is False
        assert handler.active is False

    def test_pointer_up_is_a_noop(self) -> None:
        app = _DummyApp()
        handler = MouseHandler(app)
        _grab(handler, app)
        assert handler.on_pointer_up(640, 360, 1) is False
        assert handler.active is True

    def test_no_controlled_markers(self) -> None:
        app = _DummyApp()
        app._controlled_ids = []
        handler = MouseHandler(app)
        # Nothing to grab anywhere on screen.
        assert handler.on_pointer_down(640, 360, 1) is False
        assert handler.active is False

    def test_skips_marker_missing_from_server(self) -> None:
        app = _DummyApp()
        app._controlled_ids = [1, 99]  # 99 not registered
        handler = MouseHandler(app)
        assert _grab(handler, app, tid=1) is True
        assert app._selected_id == 1

    def test_skips_marker_behind_camera(self) -> None:
        app = _DummyApp()
        # Marker 2 sits behind the camera (downstage of pos_y=-10) → projects
        # to NaN and must be skipped during the hit-test.
        app._server.add_marker(2).set_pos(0.0, -50.0, 0.0)
        app._controlled_ids = [1, 2]
        handler = MouseHandler(app)
        assert _grab(handler, app, tid=1) is True
        assert app._selected_id == 1

    def test_grab_with_server_none(self) -> None:
        app = _DummyApp()
        app._server = None
        handler = MouseHandler(app)
        assert handler.on_pointer_down(640, 360, 1) is False
        assert handler.active is False

    def test_degenerate_canvas_blocks_grab(self) -> None:
        app = _DummyApp()
        app._canvas = _DummyCanvas(0, 0)
        handler = MouseHandler(app)
        assert handler.on_pointer_down(640, 360, 1) is False

    def test_move_off_plane_holds_target(self, monkeypatch) -> None:
        from openfollow.input import mouse as mouse_module

        app = _DummyApp()
        handler = MouseHandler(app)
        cx, cy = _ground_center(app, 1)
        handler.on_pointer_down(cx, cy, 1)
        before = app._server.get_marker(1).pos

        def _nan_unproject(*args, **kwargs):
            return np.full((1, 3), np.nan)

        monkeypatch.setattr(mouse_module, "unproject_to_plane", _nan_unproject)
        assert handler.on_pointer_move(cx + 80, cy + 40) is True
        handler.update()
        assert app._server.get_marker(1).pos == before

    def test_update_noop_when_inactive(self) -> None:
        app = _DummyApp()
        handler = MouseHandler(app)
        before = app._server.get_marker(1).pos
        handler.update()  # not active – must not move anything
        assert app._server.get_marker(1).pos == before

    def test_update_noop_when_mouse_disabled(self) -> None:
        app = _DummyApp()
        handler = MouseHandler(app)
        cx, cy = _ground_center(app, 1)
        handler.on_pointer_down(cx, cy, 1)
        handler.on_pointer_move(cx + 80, cy + 40)
        app._config.controller.mouse_enabled = False
        before = app._server.get_marker(1).pos
        handler.update()
        assert app._server.get_marker(1).pos == before

    def test_wheel_noop_when_selection_cleared(self) -> None:
        app = _DummyApp()
        handler = MouseHandler(app)
        _grab(handler, app)
        app._selected_id = None
        assert handler.on_wheel(1.0) is False

    def test_wheel_noop_when_server_cleared(self) -> None:
        app = _DummyApp()
        handler = MouseHandler(app)
        _grab(handler, app)
        app._server = None
        assert handler.on_wheel(1.0) is False

    def test_move_off_canvas_after_grab_holds(self) -> None:
        app = _DummyApp()
        handler = MouseHandler(app)
        _grab(handler, app)
        before = app._server.get_marker(1).pos
        app._canvas = _DummyCanvas(0, 0)  # degenerate – unprojection is skipped
        assert handler.on_pointer_move(800, 300) is True
        handler.update()
        assert app._server.get_marker(1).pos == before

    def test_grab_falls_back_to_pixel_radius_when_ring_degenerate(self, monkeypatch) -> None:
        # If the ground ring projects to < 3 finite points, the hit-test falls
        # back to a pixel radius around the projected centre.
        from openfollow.input import mouse as mouse_module

        app = _DummyApp()
        cx, cy = _ground_center(app, 1)  # real centre, before patching
        real_project = mouse_module.project_points

        def _proj(buf, pts, w, h):
            arr = np.asarray(pts, dtype=np.float64)
            if arr.shape[0] == 1:  # the centre point stays real
                return real_project(buf, arr, w, h)
            return np.full((arr.shape[0], 2), np.nan)  # ring → all NaN

        monkeypatch.setattr(mouse_module, "project_points", _proj)
        handler = MouseHandler(app)
        assert handler.on_pointer_down(cx, cy, 1) is True
