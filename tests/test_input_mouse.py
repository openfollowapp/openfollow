# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for the mouse input handler."""

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


class _DummyVideoReceiver:
    def __init__(self, w: int, h: int) -> None:
        self._res = (w, h)

    @property
    def resolution(self) -> tuple[int, int]:
        return self._res


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
        self._camera = _DummyCamera(self._config.camera)
        self._video_receiver = _DummyVideoReceiver(1280, 720)
        self._canvas = None

        self._server = _DummyServer()
        self._server.add_marker(1).set_pos(0.0, 0.0, 0.0)

        self._controlled_ids = [1]
        self._selected_id = 1
        self._assist_manual: dict[int, Marker] = {}


class TestMouseActivation:
    """Left-click activates, right-click deactivates."""

    def test_starts_inactive(self) -> None:
        handler = MouseHandler(_DummyApp())
        assert handler.active is False

    def test_left_click_activates(self) -> None:
        handler = MouseHandler(_DummyApp())
        consumed = handler.on_pointer_down(640, 360, 1)
        assert consumed is True
        assert handler.active is True

    def test_right_click_deactivates(self) -> None:
        handler = MouseHandler(_DummyApp())
        handler.on_pointer_down(640, 360, 1)
        consumed = handler.on_pointer_down(640, 360, 3)
        assert consumed is True
        assert handler.active is False

    def test_right_click_when_inactive_not_consumed(self) -> None:
        handler = MouseHandler(_DummyApp())
        consumed = handler.on_pointer_down(640, 360, 3)
        assert consumed is False

    def test_move_ignored_when_inactive(self) -> None:
        handler = MouseHandler(_DummyApp())
        consumed = handler.on_pointer_move(640, 360)
        assert consumed is False

    def test_deactivate_disarms_so_next_move_is_ignored(self) -> None:
        # Disarming (mouse_enabled toggled off) must prevent the next move from
        # repositioning until a fresh left-click re-arms.
        app = _DummyApp()
        handler = MouseHandler(app)
        handler.on_pointer_down(640, 360, 1)
        assert handler.active is True

        handler.deactivate()
        assert handler.active is False
        assert handler.on_pointer_move(100, 100) is False

    def test_deactivate_is_noop_when_already_inactive(self) -> None:
        handler = MouseHandler(_DummyApp())
        handler.deactivate()  # must not raise
        assert handler.active is False


class TestMousePosition:
    """Position is applied via unprojection."""

    def test_left_click_moves_marker(self) -> None:
        app = _DummyApp()
        handler = MouseHandler(app)
        handler.on_pointer_down(640, 360, 1)

        marker = app._server.get_marker(1)
        x, y, z = marker.pos
        # Should have been unprojected to a finite stage coord
        assert math.isfinite(x)
        assert math.isfinite(y)
        # Z should be unchanged (0.0)
        assert z == 0.0

    def test_move_updates_marker(self) -> None:
        app = _DummyApp()
        handler = MouseHandler(app)
        handler.on_pointer_down(640, 360, 1)

        handler.on_pointer_move(700, 400)
        marker = app._server.get_marker(1)
        x2, y2, _ = marker.pos
        assert math.isfinite(x2)
        assert math.isfinite(y2)

    def test_uses_canvas_size_when_available(self) -> None:
        app = _DummyApp()
        app._canvas = _DummyCanvas(1280, 720)
        handler = MouseHandler(app)
        handler.on_pointer_down(640, 360, 1)

        marker = app._server.get_marker(1)
        x, y, _ = marker.pos
        assert app._canvas.calls > 0
        assert math.isfinite(x)
        assert math.isfinite(y)


class TestMouseAssistRedirect:
    """In detection assist mode the mouse steers the manual ghost anchor, not
    the registered (AI-corrected, broadcast) marker."""

    @staticmethod
    def _assist_app() -> _DummyApp:
        app = _DummyApp()  # _controlled_ids = [1], _selected_id = 1
        app._config.detection.enabled = True
        app._config.detection.pin_mode = "assist"
        return app

    def test_left_click_moves_ghost_not_registered_marker(self) -> None:
        app = self._assist_app()
        handler = MouseHandler(app)
        handler.on_pointer_down(700, 400, 1)

        # The registered marker (broadcast + zones, owned by the pin) is untouched.
        assert app._server.get_marker(1).pos == (0.0, 0.0, 0.0)
        # The operator's click moved the manual ghost instead.
        ghost = app._assist_manual[1]
        gx, gy, _ = ghost.pos
        assert math.isfinite(gx)
        assert math.isfinite(gy)
        assert (gx, gy) != (0.0, 0.0)

    def test_wheel_adjusts_ghost_z_not_registered_marker(self) -> None:
        app = self._assist_app()
        handler = MouseHandler(app)
        handler.on_pointer_down(640, 360, 1)  # seeds + positions the ghost
        ghost = app._assist_manual[1]
        ghost.set_pos(ghost.pos[0], ghost.pos[1], 3.0)

        handler.on_wheel(1.0)
        assert ghost.pos[2] == pytest.approx(3.1)
        # Registered marker Z never moved.
        assert app._server.get_marker(1).pos[2] == 0.0

    def test_replace_mode_steers_registered_marker(self) -> None:
        # Replace mode (with detection enabled) drives the registered marker
        # directly and never creates a ghost.
        app = _DummyApp()
        app._config.detection.enabled = True
        app._config.detection.pin_mode = "replace"
        handler = MouseHandler(app)
        handler.on_pointer_down(700, 400, 1)

        assert app._server.get_marker(1).pos != (0.0, 0.0, 0.0)
        assert app._assist_manual == {}

    def test_detection_disabled_steers_registered_marker(self) -> None:
        # Detection off: even with pin_mode "assist" left over, assist_active is
        # False, so the mouse drives the registered marker and creates no ghost.
        app = _DummyApp()
        app._config.detection.enabled = False
        app._config.detection.pin_mode = "assist"
        handler = MouseHandler(app)
        handler.on_pointer_down(700, 400, 1)

        assert app._server.get_marker(1).pos != (0.0, 0.0, 0.0)
        assert app._assist_manual == {}

    def test_assist_redirect_only_for_selected_controlled_id(self) -> None:
        # The mouse only ever steers the selected marker, and only redirects to
        # the ghost when that selected id is itself assist-controlled. A
        # selected id outside controlled_marker_ids drives the registered
        # marker even while assist is active.
        app = self._assist_app()
        app._server.add_marker(2).set_pos(0.0, 0.0, 0.0)
        app._selected_id = 2  # selected but NOT in _controlled_ids ([1])
        handler = MouseHandler(app)
        handler.on_pointer_down(700, 400, 1)

        assert app._server.get_marker(2).pos != (0.0, 0.0, 0.0)
        assert app._assist_manual == {}


class TestMouseWheel:
    """Scroll wheel adjusts Z height."""

    def test_wheel_ignored_when_inactive(self) -> None:
        handler = MouseHandler(_DummyApp())
        consumed = handler.on_wheel(1.0)
        assert consumed is False

    def test_wheel_adjusts_z(self) -> None:
        app = _DummyApp()
        app._server.get_marker(1).set_pos(1.0, 2.0, 3.0)
        handler = MouseHandler(app)
        handler.on_pointer_down(640, 360, 1)

        consumed = handler.on_wheel(1.0)
        assert consumed is True
        _, _, z = app._server.get_marker(1).pos
        assert z == pytest.approx(3.1)  # 3.0 + 1.0 * 0.1

    def test_wheel_negative(self) -> None:
        app = _DummyApp()
        app._server.get_marker(1).set_pos(1.0, 2.0, 3.0)
        handler = MouseHandler(app)
        handler.on_pointer_down(640, 360, 1)

        handler.on_wheel(-2.0)
        _, _, z = app._server.get_marker(1).pos
        assert z == pytest.approx(2.8)  # 3.0 + (-2.0) * 0.1


class TestMouseNoMarker:
    """Graceful handling when no marker is selected."""

    def test_no_selected_id(self) -> None:
        app = _DummyApp()
        app._selected_id = None
        handler = MouseHandler(app)
        # Should not raise
        handler.on_pointer_down(640, 360, 1)
        handler.on_pointer_move(700, 400)
        handler.on_wheel(1.0)


class TestMouseEdgeArms:
    """The defensive guards that the main coverage tests don't reach
    yet: middle-button no-op, on_pointer_up no-op, server=None,
    degenerate canvas, non-finite unprojection."""

    def test_middle_button_press_returns_false(self) -> None:
        """Buttons other than 1 (left) / 3 (right) are not consumed."""
        handler = MouseHandler(_DummyApp())
        assert handler.on_pointer_down(640, 360, 2) is False  # middle button
        assert handler.active is False

    def test_pointer_up_is_a_no_op(self) -> None:
        """``on_pointer_up`` is unconditional."""
        handler = MouseHandler(_DummyApp())
        handler.on_pointer_down(640, 360, 1)
        # Release does nothing; activation persists, return False.
        assert handler.on_pointer_up(640, 360, 1) is False
        assert handler.active is True

    def test_apply_position_with_server_none(self) -> None:
        """When ``app._server`` is None, ``_get_selected_marker``
        returns None and the position update is a no-op."""
        app = _DummyApp()
        app._server = None
        handler = MouseHandler(app)
        # Must not raise.
        consumed = handler.on_pointer_down(640, 360, 1)
        assert consumed is True  # left-click activates regardless

    def test_apply_position_with_degenerate_canvas_returns(self) -> None:
        """A canvas reporting ``(0, 0)`` size triggers the early-return
        so ``unproject_to_plane`` is never called with garbage."""
        app = _DummyApp()
        app._canvas = _DummyCanvas(0, 0)
        handler = MouseHandler(app)
        before = app._server.get_marker(1).pos
        handler.on_pointer_down(640, 360, 1)
        # Marker position unchanged because the canvas was degenerate.
        assert app._server.get_marker(1).pos == before

    def test_apply_position_skips_when_unproject_yields_nan(self, monkeypatch) -> None:
        from openfollow.input import mouse as mouse_module

        def _nan_unproject(*args, **kwargs):
            return np.full((1, 3), np.nan)

        monkeypatch.setattr(mouse_module, "unproject_to_plane", _nan_unproject)
        app = _DummyApp()
        handler = MouseHandler(app)
        before = app._server.get_marker(1).pos
        handler.on_pointer_down(640, 360, 1)
        assert app._server.get_marker(1).pos == before
