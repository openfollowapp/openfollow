# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Integration tests for InputManager: keyboard/gamepad routing, single- vs
multi-gamepad marker resolution, button-signal forwarding, MIDI hotplug, and
per-subsystem failure isolation."""

from __future__ import annotations

import logging
import math
from types import SimpleNamespace

import numpy as np
import pytest

import openfollow.input.input_manager as input_manager_module
from openfollow.configuration import AppConfig, CameraConfig
from openfollow.input.gamepad import GamepadUpdate
from openfollow.input.input_manager import InputManager
from openfollow.operator_messages import OperatorMessageStore
from openfollow.psn.marker import Marker
from openfollow.scene.solver import project_points

pytestmark = pytest.mark.integration


class _DummyServer:
    def __init__(self) -> None:
        self.markers: dict[int, Marker] = {}

    def add_marker(self, marker_id: int) -> Marker:
        marker = Marker(marker_id, f"Marker {marker_id}")
        self.markers[marker_id] = marker
        return marker

    def get_marker(self, marker_id: int) -> Marker | None:
        return self.markers.get(marker_id)


class _FakeKeyboardHandler:
    next_velocity: tuple[float, float, float] | None = None

    def __init__(self, app, *, event_bus=None) -> None:  # noqa: ANN001
        # Real KeyboardHandler accepts event_bus kwarg for hardware emission.
        # Fake mirrors signature; emission covered by test_input_keyboard.py.
        self.app = app
        self.event_bus = event_bus

    @property
    def keys(self) -> set[str]:
        return set()

    def update(self, _dt: float) -> tuple[float, float, float] | None:
        return self.next_velocity

    def is_connected(self) -> bool:
        return True


class _FakeGamepadHandler:
    next_update = GamepadUpdate()
    joystick_indices: tuple[int, ...] = (0, 1, 2)
    controller_info: list[dict] = []
    effective_speeds: dict[int, float] = {0: 2.0, 1: 3.0, 2: 4.0}
    last_marker_resolver = None  # captured for assertion in tests

    def __init__(  # noqa: ANN001
        self,
        app,
        *,
        event_bus=None,
        virtual_faders=None,
        marker_resolver=None,
    ) -> None:
        # Mirror the real GamepadHandler signature with virtual_faders and marker_resolver.
        # Both are captured so tests can assert on the wiring.
        self.app = app
        self.event_bus = event_bus
        self.virtual_faders = virtual_faders
        self.marker_resolver = marker_resolver
        _FakeGamepadHandler.last_marker_resolver = marker_resolver
        self.joysticks = {idx: object() for idx in self.joystick_indices}

    def update(self, _dt: float) -> GamepadUpdate:
        return self.next_update

    def get_controller_info(self) -> list[dict]:
        return [dict(item) for item in self.controller_info]

    def get_controller_effective_speeds(self) -> dict[int, float]:
        return dict(self.effective_speeds)

    def device_identities(self) -> dict[int, tuple[str | None, str, tuple[str, ...]]]:
        names = {int(item["controller_index"]): item.get("name", "") for item in self.controller_info}
        return {idx: (None, names.get(idx, f"Pad {idx}"), ()) for idx in self.joysticks}

    def last_input(self) -> dict[int, float]:
        return {}

    def identify(self, _instance_id: int) -> bool:
        return True

    def stop(self) -> None:
        pass


class _DummyApp:
    def __init__(self) -> None:
        self._config = AppConfig()
        self._config.osc.enabled = False
        self._config.marker.default_pos_x = 5.0
        self._config.marker.default_pos_y = 6.0
        self._config.marker.default_pos_z = 7.0

        self._controlled_ids = [10, 11]
        self._selected_id = 10

        self._server = _DummyServer()
        self._server.add_marker(10).set_pos(0.0, 0.0, 0.0)
        self._server.add_marker(11).set_pos(0.0, 0.0, 0.0)
        self._assist_manual: dict[int, Marker] = {}
        # InputManager reads _virtual_faders to pass to GamepadHandler.
        # Fake ignores the value, but read must not raise.
        self._runtime_services = SimpleNamespace(
            _virtual_faders=None,
            # Cleared by _rebuild_operator_message_handler on the no-listener path.
            _operator_message_store=OperatorMessageStore(),
        )

    def _get_default_marker_position(self) -> tuple[float, float, float]:
        cfg = self._config.marker
        return (cfg.default_pos_x, cfg.default_pos_y, cfg.default_pos_z)


@pytest.fixture(autouse=True)
def _reset_fake_handlers() -> None:
    _FakeKeyboardHandler.next_velocity = None
    _FakeGamepadHandler.next_update = GamepadUpdate()
    _FakeGamepadHandler.joystick_indices = (0, 1, 2)
    _FakeGamepadHandler.controller_info = []
    _FakeGamepadHandler.effective_speeds = {0: 2.0, 1: 3.0, 2: 4.0}
    _FakeGamepadHandler.last_marker_resolver = None


def test_input_manager_applies_keyboard_gamepad_and_reset(monkeypatch) -> None:
    monkeypatch.setattr(input_manager_module, "KeyboardHandler", _FakeKeyboardHandler)
    monkeypatch.setattr(input_manager_module, "GamepadHandler", _FakeGamepadHandler)

    _FakeKeyboardHandler.next_velocity = (0.5, 0.0, 0.0)
    _FakeGamepadHandler.next_update = GamepadUpdate(
        movements={1: (1.0, 0.0, 0.0)},
        resets={0},
    )

    app = _DummyApp()
    manager = InputManager(app)
    manager.update(2.0)

    assert app._server.get_marker(10).pos == pytest.approx((5.0, 6.0, 7.0))
    assert app._server.get_marker(11).pos == pytest.approx((2.0, 0.0, 0.0))


def test_get_marker_redirects_every_controlled_id_to_ghost_in_assist_mode(monkeypatch) -> None:
    monkeypatch.setattr(input_manager_module, "KeyboardHandler", _FakeKeyboardHandler)
    monkeypatch.setattr(input_manager_module, "GamepadHandler", _FakeGamepadHandler)

    app = _DummyApp()  # _controlled_ids = [10, 11], _selected_id = 10
    app._config.detection.enabled = True
    app._config.detection.pin_mode = "assist"
    manager = InputManager(app)

    # Assist refines every controlled marker, so each controlled id – not just
    # the selected one – redirects to its own manual ghost, leaving every
    # registered (AI-corrected, broadcast) marker for the detection pin.
    for marker_id in (10, 11):
        registered = app._server.get_marker(marker_id)
        resolved = manager._get_marker(marker_id)
        assert resolved is app._assist_manual[marker_id]
        assert resolved is not registered
    # The two controlled ids get distinct ghosts.
    assert app._assist_manual[10] is not app._assist_manual[11]


def test_get_marker_returns_registered_marker_outside_assist(monkeypatch) -> None:
    monkeypatch.setattr(input_manager_module, "KeyboardHandler", _FakeKeyboardHandler)
    monkeypatch.setattr(input_manager_module, "GamepadHandler", _FakeGamepadHandler)

    # Detection off, and replace mode: no ghost is created, input drives the
    # registered marker directly.
    app = _DummyApp()
    app._config.detection.enabled = False
    app._config.detection.pin_mode = "replace"
    manager = InputManager(app)
    assert manager._get_marker(10) is app._server.get_marker(10)
    assert app._assist_manual == {}

    # Replace mode while detection is enabled still drives the registered marker.
    app._config.detection.enabled = True
    assert manager._get_marker(10) is app._server.get_marker(10)
    assert app._assist_manual == {}


def test_keyboard_movement_steers_ghost_in_assist_mode(monkeypatch) -> None:
    monkeypatch.setattr(input_manager_module, "KeyboardHandler", _FakeKeyboardHandler)
    monkeypatch.setattr(input_manager_module, "GamepadHandler", _FakeGamepadHandler)

    _FakeKeyboardHandler.next_velocity = (0.5, 0.0, 0.0)
    app = _DummyApp()
    app._config.detection.enabled = True
    app._config.detection.pin_mode = "assist"
    manager = InputManager(app)
    manager.update(2.0)

    # Operator input moves the manual ghost (0.5·2.0 = 1.0 m); the registered
    # marker is left for the detection pin to drive.
    assert app._server.get_marker(10).pos == pytest.approx((0.0, 0.0, 0.0))
    assert app._assist_manual[10].pos == pytest.approx((1.0, 0.0, 0.0))


def test_input_manager_maps_controller_speeds_to_marker_ids(monkeypatch) -> None:
    monkeypatch.setattr(input_manager_module, "KeyboardHandler", _FakeKeyboardHandler)
    monkeypatch.setattr(input_manager_module, "GamepadHandler", _FakeGamepadHandler)

    app = _DummyApp()
    manager = InputManager(app)

    assert manager.get_marker_gamepad_speeds() == {10: 2.0, 11: 3.0}
    assert manager.is_keyboard_connected() is True


def test_input_manager_passes_marker_resolver_to_gamepad(monkeypatch) -> None:
    """Gamepad handler maps controller_idx to marker_id for speed paths.
    InputManager passes _gamepad_marker_id at construction."""
    monkeypatch.setattr(input_manager_module, "KeyboardHandler", _FakeKeyboardHandler)
    monkeypatch.setattr(input_manager_module, "GamepadHandler", _FakeGamepadHandler)
    _FakeGamepadHandler.joystick_indices = (0, 1)

    app = _DummyApp()
    InputManager(app)
    resolver = _FakeGamepadHandler.last_marker_resolver
    assert resolver is not None
    # Multi-pad mode: controller_idx maps to fixed slot in app._controlled_ids.
    assert resolver(0) == app._controlled_ids[0]
    assert resolver(1) == app._controlled_ids[1]


def test_controller_marker_id_delegates_to_gamepad_marker_id(monkeypatch) -> None:
    """The public ``:cN`` seam mirrors the internal ``_gamepad_marker_id``
    routing – multi-pad mode maps controller_idx to a fixed slot, and an
    out-of-range index returns ``None``."""
    monkeypatch.setattr(input_manager_module, "KeyboardHandler", _FakeKeyboardHandler)
    monkeypatch.setattr(input_manager_module, "GamepadHandler", _FakeGamepadHandler)
    _FakeGamepadHandler.joystick_indices = (0, 1)  # two pads → multi-pad mode

    app = _DummyApp()
    manager = InputManager(app)
    assert manager.controller_marker_id(0) == manager._gamepad_marker_id(0)
    assert manager.controller_marker_id(0) == app._controlled_ids[0]
    assert manager.controller_marker_id(1) == app._controlled_ids[1]
    assert manager.controller_marker_id(5) is None  # out of range


def test_controller_marker_id_single_gamepad_follows_selected(monkeypatch) -> None:
    """In single-gamepad mode the seam follows the selected marker, so a
    ``[markerid:c1]`` row tracks what the operator is actually moving."""
    monkeypatch.setattr(input_manager_module, "KeyboardHandler", _FakeKeyboardHandler)
    monkeypatch.setattr(input_manager_module, "GamepadHandler", _FakeGamepadHandler)
    _FakeGamepadHandler.joystick_indices = (0,)  # exactly one pad

    app = _DummyApp()  # _selected_id = 10
    manager = InputManager(app)
    assert manager.controller_marker_id(0) == app._selected_id
    # :c2 has no controller 2 connected → skip, not the selected marker.
    assert manager.controller_marker_id(1) is None


def test_controller_marker_id_skips_disconnected_controller(monkeypatch) -> None:
    """:cN resolves only for a connected controller. An index beyond the
    connected pads skips even when the controlled-marker slot exists, so a
    row never emits a marker no controller drives."""
    monkeypatch.setattr(input_manager_module, "KeyboardHandler", _FakeKeyboardHandler)
    monkeypatch.setattr(input_manager_module, "GamepadHandler", _FakeGamepadHandler)
    _FakeGamepadHandler.joystick_indices = (0, 1)  # two pads connected

    app = _DummyApp()
    app._controlled_ids = [10, 11, 12, 13, 14]  # more controlled markers than pads
    manager = InputManager(app)
    # connected controllers resolve to their fixed slot
    assert manager.controller_marker_id(0) == 10
    assert manager.controller_marker_id(1) == 11
    # controllers 3 and 5 are not connected → skip despite the slot existing
    assert manager.controller_marker_id(2) is None
    assert manager.controller_marker_id(4) is None


def test_input_manager_propagates_gamepad_button_signals(monkeypatch) -> None:
    monkeypatch.setattr(input_manager_module, "KeyboardHandler", _FakeKeyboardHandler)
    monkeypatch.setattr(input_manager_module, "GamepadHandler", _FakeGamepadHandler)

    # Single controller: next/prev cycling is allowed (suppressed only when more
    # than one controller is present).
    _FakeGamepadHandler.joystick_indices = (0,)
    _FakeKeyboardHandler.next_velocity = None
    _FakeGamepadHandler.next_update = GamepadUpdate(
        next_marker_pressed=True,
        prev_marker_pressed=True,
        settings_open_pressed=True,
        toggle_help_pressed=True,
    )

    app = _DummyApp()
    manager = InputManager(app)
    result = manager.update(0.016)

    assert result.next_marker_pressed is True
    assert result.prev_marker_pressed is True
    assert result.settings_open_pressed is True
    assert result.toggle_help_pressed is True


def test_input_manager_returns_gamepad_signals_even_without_controlled_markers(
    monkeypatch,
) -> None:
    """Settings menu must still be openable when no markers are controlled."""
    monkeypatch.setattr(input_manager_module, "KeyboardHandler", _FakeKeyboardHandler)
    monkeypatch.setattr(input_manager_module, "GamepadHandler", _FakeGamepadHandler)

    _FakeGamepadHandler.next_update = GamepadUpdate(settings_open_pressed=True)

    app = _DummyApp()
    app._controlled_ids = []
    app._selected_id = None
    manager = InputManager(app)
    result = manager.update(0.016)

    assert result.settings_open_pressed is True


def test_single_gamepad_routes_movement_to_selected_marker(monkeypatch) -> None:
    """Regression for the multi-marker DPAD bug: with exactly one gamepad
    connected, movement from controller_idx=0 must apply to app._selected_id
    rather than always to controlled_ids[0]. Without this, DPAD_LEFT/RIGHT
    updates the UI selection but leaves gamepad control glued to the first
    marker.
    """
    monkeypatch.setattr(input_manager_module, "KeyboardHandler", _FakeKeyboardHandler)
    monkeypatch.setattr(input_manager_module, "GamepadHandler", _FakeGamepadHandler)

    _FakeGamepadHandler.joystick_indices = (0,)
    _FakeGamepadHandler.next_update = GamepadUpdate(movements={0: (1.0, 0.0, 0.0)})

    app = _DummyApp()
    app._selected_id = 11
    manager = InputManager(app)
    manager.update(1.0)

    assert app._server.get_marker(10).pos == pytest.approx((0.0, 0.0, 0.0))
    assert app._server.get_marker(11).pos == pytest.approx((1.0, 0.0, 0.0))


def test_single_gamepad_routes_reset_to_selected_marker(monkeypatch) -> None:
    monkeypatch.setattr(input_manager_module, "KeyboardHandler", _FakeKeyboardHandler)
    monkeypatch.setattr(input_manager_module, "GamepadHandler", _FakeGamepadHandler)

    _FakeGamepadHandler.joystick_indices = (0,)
    _FakeGamepadHandler.next_update = GamepadUpdate(resets={0})

    app = _DummyApp()
    app._selected_id = 11
    app._server.get_marker(10).set_pos(1.0, 1.0, 1.0)
    app._server.get_marker(11).set_pos(2.0, 2.0, 2.0)
    manager = InputManager(app)
    manager.update(0.016)

    assert app._server.get_marker(10).pos == pytest.approx((1.0, 1.0, 1.0))
    assert app._server.get_marker(11).pos == pytest.approx((5.0, 6.0, 7.0))


def test_multi_gamepad_preserves_fixed_slot_assignment(monkeypatch) -> None:
    """With 2+ gamepads connected, each controller keeps its dedicated
    marker slot (controlled_ids[controller_idx]) regardless of the shared
    _selected_id. This preserves multi-operator setups where each physical
    gamepad belongs to a specific marker."""
    monkeypatch.setattr(input_manager_module, "KeyboardHandler", _FakeKeyboardHandler)
    monkeypatch.setattr(input_manager_module, "GamepadHandler", _FakeGamepadHandler)

    _FakeGamepadHandler.joystick_indices = (0, 1)
    _FakeGamepadHandler.next_update = GamepadUpdate(
        movements={0: (1.0, 0.0, 0.0), 1: (0.0, 2.0, 0.0)},
        resets=set(),
    )

    app = _DummyApp()
    app._selected_id = 11
    manager = InputManager(app)
    manager.update(1.0)

    assert app._server.get_marker(10).pos == pytest.approx((1.0, 0.0, 0.0))
    assert app._server.get_marker(11).pos == pytest.approx((0.0, 2.0, 0.0))


def test_single_gamepad_without_selection_falls_back_to_slot_mapping(monkeypatch) -> None:
    """When _selected_id is None (startup edge case before first cycle),
    single-gamepad routing must not crash – fall back to slot 0."""
    monkeypatch.setattr(input_manager_module, "KeyboardHandler", _FakeKeyboardHandler)
    monkeypatch.setattr(input_manager_module, "GamepadHandler", _FakeGamepadHandler)

    _FakeGamepadHandler.joystick_indices = (0,)
    _FakeGamepadHandler.next_update = GamepadUpdate(movements={0: (1.0, 0.0, 0.0)})

    app = _DummyApp()
    app._selected_id = None
    manager = InputManager(app)
    manager.update(1.0)

    assert app._server.get_marker(10).pos == pytest.approx((1.0, 0.0, 0.0))
    assert app._server.get_marker(11).pos == pytest.approx((0.0, 0.0, 0.0))


def test_single_gamepad_speed_maps_to_selected_marker(monkeypatch) -> None:
    monkeypatch.setattr(input_manager_module, "KeyboardHandler", _FakeKeyboardHandler)
    monkeypatch.setattr(input_manager_module, "GamepadHandler", _FakeGamepadHandler)

    _FakeGamepadHandler.joystick_indices = (0,)
    _FakeGamepadHandler.effective_speeds = {0: 2.5}

    app = _DummyApp()
    app._selected_id = 11
    manager = InputManager(app)

    assert manager.get_marker_gamepad_speeds() == {11: 2.5}


def test_single_gamepad_controller_info_reports_selected_marker(monkeypatch) -> None:
    monkeypatch.setattr(input_manager_module, "KeyboardHandler", _FakeKeyboardHandler)
    monkeypatch.setattr(input_manager_module, "GamepadHandler", _FakeGamepadHandler)

    _FakeGamepadHandler.joystick_indices = (0,)
    _FakeGamepadHandler.controller_info = [
        {"controller_index": 0, "name": "Solo", "connected": True},
    ]

    app = _DummyApp()
    app._selected_id = 11
    manager = InputManager(app)

    info = manager.get_controller_info()
    # ``effective_speed`` / ``backend`` are always present (defaulted when the
    # gamepad info dict is missing them) so the stats consumer can read them
    # unconditionally.
    assert info == [
        {
            "controller_index": 0,
            "name": "Solo",
            "kind": "gamepad",
            "state": "connected",
            "connected": True,
            "marker_id": 11,
            "effective_speed": 0.0,
            "backend": "",
            "port_key": None,
            "port_label": "no stable port",
            "slot_ref": "gamepad||Solo|connected",
            "seconds_since_input": None,
            "notes": [],
        },
    ]


def test_multi_gamepad_controller_info_uses_slot_mapping(monkeypatch) -> None:
    """With 2+ gamepads, controller_info keeps the slot-based marker_id."""
    monkeypatch.setattr(input_manager_module, "KeyboardHandler", _FakeKeyboardHandler)
    monkeypatch.setattr(input_manager_module, "GamepadHandler", _FakeGamepadHandler)

    _FakeGamepadHandler.joystick_indices = (0, 1)
    _FakeGamepadHandler.controller_info = [
        {"controller_index": 0, "name": "Left", "connected": True},
        {"controller_index": 1, "name": "Right", "connected": True},
    ]

    app = _DummyApp()
    app._selected_id = 11
    manager = InputManager(app)

    info = manager.get_controller_info()
    marker_ids = [item["marker_id"] for item in info]
    assert marker_ids == [10, 11]


def test_input_manager_polls_midi_hotplug(monkeypatch) -> None:
    """Each input tick drives MIDI hotplug detection (sibling to the gamepad
    hotplug) so an unplug / replug updates the patch-missing badge without a
    config save. The MIDI subsystem is reached via
    ``app._runtime_services._midi``; ``poll_hotplug`` itself is throttled."""
    monkeypatch.setattr(input_manager_module, "KeyboardHandler", _FakeKeyboardHandler)
    monkeypatch.setattr(input_manager_module, "GamepadHandler", _FakeGamepadHandler)

    class _FakeMidi:
        def __init__(self) -> None:
            self.poll_count = 0

        def poll_hotplug(self) -> None:
            self.poll_count += 1

    app = _DummyApp()
    fake_midi = _FakeMidi()
    app._runtime_services._midi = fake_midi
    manager = InputManager(app)

    manager.update(0.016)

    assert fake_midi.poll_count == 1


def test_update_isolates_midi_hotplug_failure(monkeypatch) -> None:
    """A raising MIDI hotplug poll must not drop the gamepad result."""
    monkeypatch.setattr(input_manager_module, "KeyboardHandler", _FakeKeyboardHandler)
    monkeypatch.setattr(input_manager_module, "GamepadHandler", _FakeGamepadHandler)
    _FakeGamepadHandler.next_update = GamepadUpdate(settings_open_pressed=True)

    class _BoomMidi:
        def poll_hotplug(self) -> None:
            raise RuntimeError("midi boom")

    app = _DummyApp()
    app._runtime_services._midi = _BoomMidi()
    manager = InputManager(app)

    result = manager.update(0.016)

    assert result.settings_open_pressed is True


def test_update_isolates_keyboard_failure(monkeypatch) -> None:
    """A raising keyboard update must not skip gamepad movement application."""
    monkeypatch.setattr(input_manager_module, "KeyboardHandler", _FakeKeyboardHandler)
    monkeypatch.setattr(input_manager_module, "GamepadHandler", _FakeGamepadHandler)
    _FakeGamepadHandler.joystick_indices = (0,)
    _FakeGamepadHandler.next_update = GamepadUpdate(movements={0: (1.0, 0.0, 0.0)})

    app = _DummyApp()
    app._selected_id = 10
    manager = InputManager(app)

    def _boom(_dt: float) -> None:
        raise RuntimeError("kbd boom")

    manager.keyboard_handler.update = _boom

    manager.update(1.0)

    assert app._server.get_marker(10).pos == pytest.approx((1.0, 0.0, 0.0))


def test_update_isolates_osc_flush_failure(monkeypatch) -> None:
    """A raising OSC flush must not drop the gamepad result."""
    monkeypatch.setattr(input_manager_module, "KeyboardHandler", _FakeKeyboardHandler)
    monkeypatch.setattr(input_manager_module, "GamepadHandler", _FakeGamepadHandler)
    _FakeGamepadHandler.next_update = GamepadUpdate(settings_open_pressed=True)

    app = _DummyApp()
    manager = InputManager(app)

    class _BoomOsc:
        def flush_updates(self) -> dict:
            raise RuntimeError("osc boom")

    manager.osc_handler = _BoomOsc()

    result = manager.update(0.016)

    assert result.settings_open_pressed is True


def test_update_drives_mouse_when_enabled(monkeypatch) -> None:
    """The per-frame mouse glide runs once per update when mouse_enabled."""
    monkeypatch.setattr(input_manager_module, "KeyboardHandler", _FakeKeyboardHandler)
    monkeypatch.setattr(input_manager_module, "GamepadHandler", _FakeGamepadHandler)

    app = _DummyApp()
    app._config.controller.mouse_enabled = True
    manager = InputManager(app)
    calls: list[int] = []
    manager.mouse_handler = SimpleNamespace(update=lambda: calls.append(1))

    manager.update(1.0)

    assert calls == [1]


def test_update_skips_mouse_when_disabled(monkeypatch) -> None:
    """With mouse disabled the per-frame glide must not run."""
    monkeypatch.setattr(input_manager_module, "KeyboardHandler", _FakeKeyboardHandler)
    monkeypatch.setattr(input_manager_module, "GamepadHandler", _FakeGamepadHandler)

    app = _DummyApp()
    app._config.controller.mouse_enabled = False
    manager = InputManager(app)
    calls: list[int] = []
    manager.mouse_handler = SimpleNamespace(update=lambda: calls.append(1))

    manager.update(1.0)

    assert calls == []


def test_update_isolates_mouse_failure(monkeypatch) -> None:
    """A raising mouse update must not drop the gamepad result."""
    monkeypatch.setattr(input_manager_module, "KeyboardHandler", _FakeKeyboardHandler)
    monkeypatch.setattr(input_manager_module, "GamepadHandler", _FakeGamepadHandler)
    _FakeGamepadHandler.next_update = GamepadUpdate(settings_open_pressed=True)

    app = _DummyApp()
    app._config.controller.mouse_enabled = True
    manager = InputManager(app)

    class _BoomMouse:
        def update(self) -> None:
            raise RuntimeError("mouse boom")

    manager.mouse_handler = _BoomMouse()

    result = manager.update(0.016)

    assert result.settings_open_pressed is True


# --------------------------------------------------------------------------- #
# Control frame – which stage directions relative input steers in
# --------------------------------------------------------------------------- #


def _with_camera(app: _DummyApp, **pose: float) -> _DummyApp:
    """Give the dummy app a camera with ``pose`` and a 1920x1080 Operator Screen."""
    app._camera = SimpleNamespace(to_config=lambda: CameraConfig(**pose))
    app._canvas = SimpleNamespace(get_canvas_size=lambda: (1920, 1080))
    return app


def _keyboard_step(monkeypatch, app: _DummyApp, velocity: tuple[float, float, float]) -> InputManager:  # noqa: ANN001
    monkeypatch.setattr(input_manager_module, "KeyboardHandler", _FakeKeyboardHandler)
    monkeypatch.setattr(input_manager_module, "GamepadHandler", _FakeGamepadHandler)
    _FakeKeyboardHandler.next_velocity = velocity
    manager = InputManager(app)
    manager.update(1.0)
    return manager


def test_stage_frame_is_the_default_and_moves_in_stage_axes(monkeypatch) -> None:
    """An existing install must not have its controls turned by the upgrade."""
    app = _DummyApp()
    assert app._config.marker.control_frame == "stage"
    _keyboard_step(monkeypatch, app, (1.0, 2.0, 3.0))
    assert app._server.get_marker(10).pos == pytest.approx((1.0, 2.0, 3.0))


@pytest.mark.parametrize(
    ("heading", "expected"),
    [
        (0.0, (1.0, 2.0)),
        (180.0, (-1.0, -2.0)),
        # A camera with yaw 90 faces stage left: up is +X, right is downstage.
        (90.0, (2.0, -1.0)),
        (270.0, (-2.0, 1.0)),
    ],
)
def test_heading_frame_turns_xy_to_the_heading_and_leaves_z(monkeypatch, heading, expected) -> None:  # noqa: ANN001
    app = _DummyApp()
    app._config.marker.control_frame = "heading"
    app._config.marker.control_heading = heading
    _keyboard_step(monkeypatch, app, (1.0, 2.0, 3.0))
    assert app._server.get_marker(10).pos == pytest.approx((*expected, 3.0))


@pytest.mark.parametrize("yaw", [0.0, 90.0, 180.0, -90.0, 33.0])
def test_camera_frame_matches_the_heading_of_the_camera_yaw(monkeypatch, yaw) -> None:  # noqa: ANN001
    by_camera = _with_camera(_DummyApp(), yaw=yaw)
    by_camera._config.marker.control_frame = "camera"
    _keyboard_step(monkeypatch, by_camera, (1.0, 2.0, 3.0))

    by_heading = _DummyApp()
    by_heading._config.marker.control_frame = "heading"
    by_heading._config.marker.control_heading = yaw
    _keyboard_step(monkeypatch, by_heading, (1.0, 2.0, 3.0))

    assert by_camera._server.get_marker(10).pos == pytest.approx(by_heading._server.get_marker(10).pos)


def test_camera_frame_without_a_camera_moves_in_stage_axes(monkeypatch) -> None:
    app = _DummyApp()
    app._camera = None
    app._config.marker.control_frame = "camera"
    _keyboard_step(monkeypatch, app, (1.0, 2.0, 0.0))
    assert app._server.get_marker(10).pos == pytest.approx((1.0, 2.0, 0.0))


def test_screen_frame_moves_the_ball_up_the_picture(monkeypatch) -> None:
    """Stick up keeps the ball on its picture column; the speed stays the stick's."""
    app = _with_camera(_DummyApp())
    app._config.marker.control_frame = "screen"
    marker = app._server.get_marker(10)
    marker.set_pos(4.0, 0.0, 1.6)
    params = np.array([0.0, -11.0, 6.0, -22.0, 0.0, 0.0, 60.0])
    before = project_points(params, np.array([marker.pos]), 1920, 1080)[0]

    _keyboard_step(monkeypatch, app, (0.0, 0.5, 0.0))

    after = project_points(params, np.array([marker.pos]), 1920, 1080)[0]
    assert after[0] == pytest.approx(before[0], abs=1e-6)
    assert after[1] < before[1]
    assert math.dist(marker.pos[:2], (4.0, 0.0)) == pytest.approx(0.5)
    assert marker.pos[2] == pytest.approx(1.6)


def test_screen_frame_off_the_picture_uses_the_camera_heading(monkeypatch) -> None:
    app = _with_camera(_DummyApp(), yaw=90.0, pos_x=-12.0, pos_y=0.0)
    app._config.marker.control_frame = "screen"
    # Behind a camera that faces stage left.
    app._server.get_marker(10).set_pos(-20.0, 0.0, 0.0)
    _keyboard_step(monkeypatch, app, (0.0, 1.0, 0.0))
    assert app._server.get_marker(10).pos == pytest.approx((-19.0, 0.0, 0.0))


def test_screen_frame_with_no_canvas_size_uses_the_camera_heading(monkeypatch) -> None:
    app = _with_camera(_DummyApp(), yaw=90.0, pos_x=-12.0, pos_y=0.0)
    app._canvas = None
    app._config.window_width = 0
    app._config.marker.control_frame = "screen"
    _keyboard_step(monkeypatch, app, (0.0, 1.0, 0.0))
    assert app._server.get_marker(10).pos == pytest.approx((1.0, 0.0, 0.0))


def test_control_frame_turns_every_gamepad(monkeypatch) -> None:
    """The frame belongs to the shared camera view, so every pad turns, not just the selected one."""
    monkeypatch.setattr(input_manager_module, "KeyboardHandler", _FakeKeyboardHandler)
    monkeypatch.setattr(input_manager_module, "GamepadHandler", _FakeGamepadHandler)
    _FakeGamepadHandler.joystick_indices = (0, 1)
    _FakeGamepadHandler.next_update = GamepadUpdate(movements={0: (1.0, 0.0, 3.0), 1: (0.0, 2.0, 0.0)})

    app = _DummyApp()
    app._config.marker.control_frame = "heading"
    app._config.marker.control_heading = 180.0
    InputManager(app).update(1.0)

    assert app._server.get_marker(10).pos == pytest.approx((-1.0, 0.0, 3.0))
    assert app._server.get_marker(11).pos == pytest.approx((0.0, -2.0, 0.0))


def test_control_frame_leaves_gamepad_reset_absolute(monkeypatch) -> None:
    monkeypatch.setattr(input_manager_module, "KeyboardHandler", _FakeKeyboardHandler)
    monkeypatch.setattr(input_manager_module, "GamepadHandler", _FakeGamepadHandler)
    _FakeGamepadHandler.joystick_indices = (0,)
    _FakeGamepadHandler.next_update = GamepadUpdate(resets={0})

    app = _DummyApp()
    app._selected_id = 11
    app._config.marker.control_frame = "heading"
    app._config.marker.control_heading = 180.0
    InputManager(app).update(0.016)

    assert app._server.get_marker(11).pos == pytest.approx((5.0, 6.0, 7.0))


def test_control_frame_leaves_osc_positions_absolute(monkeypatch) -> None:
    """A console commanding x=3 lands the marker at x=3 whichever way the camera faces."""
    monkeypatch.setattr(input_manager_module, "KeyboardHandler", _FakeKeyboardHandler)
    monkeypatch.setattr(input_manager_module, "GamepadHandler", _FakeGamepadHandler)

    app = _DummyApp()
    app._config.marker.control_frame = "heading"
    app._config.marker.control_heading = 180.0
    manager = InputManager(app)
    manager.osc_handler = SimpleNamespace(flush_updates=lambda: {10: {"x": 3.0, "y": 4.0, "z": 5.0}})
    manager.update(1.0)

    assert app._server.get_marker(10).pos == pytest.approx((3.0, 4.0, 5.0))


def test_a_held_direction_survives_a_frame_change_until_the_input_returns_to_zero(monkeypatch) -> None:
    app = _DummyApp()
    manager = _keyboard_step(monkeypatch, app, (1.0, 0.0, 0.0))
    assert app._server.get_marker(10).pos == pytest.approx((1.0, 0.0, 0.0))

    app._config.marker.control_frame = "heading"
    app._config.marker.control_heading = 180.0
    manager.update(1.0)
    assert app._server.get_marker(10).pos == pytest.approx((2.0, 0.0, 0.0))

    _FakeKeyboardHandler.next_velocity = (0.0, 0.0, 0.0)
    manager.update(1.0)
    _FakeKeyboardHandler.next_velocity = (1.0, 0.0, 0.0)
    manager.update(1.0)
    assert app._server.get_marker(10).pos == pytest.approx((1.0, 0.0, 0.0))


def test_a_source_that_stops_reporting_drops_its_held_direction(monkeypatch) -> None:
    app = _DummyApp()
    manager = _keyboard_step(monkeypatch, app, (1.0, 0.0, 0.0))

    app._config.marker.control_frame = "heading"
    app._config.marker.control_heading = 180.0
    _FakeKeyboardHandler.next_velocity = None
    manager.update(1.0)
    manager.update(1.0)
    _FakeKeyboardHandler.next_velocity = (1.0, 0.0, 0.0)
    manager.update(1.0)
    assert app._server.get_marker(10).pos == pytest.approx((0.0, 0.0, 0.0))


def test_each_source_latches_its_own_direction(monkeypatch) -> None:
    """A pad starting a move after a frame change takes the new frame; a pad already moving keeps its own."""
    monkeypatch.setattr(input_manager_module, "KeyboardHandler", _FakeKeyboardHandler)
    monkeypatch.setattr(input_manager_module, "GamepadHandler", _FakeGamepadHandler)
    _FakeGamepadHandler.joystick_indices = (0, 1)
    _FakeGamepadHandler.next_update = GamepadUpdate(movements={0: (1.0, 0.0, 0.0)})
    app = _DummyApp()
    manager = InputManager(app)
    manager.update(1.0)

    app._config.marker.control_frame = "heading"
    app._config.marker.control_heading = 180.0
    _FakeGamepadHandler.next_update = GamepadUpdate(movements={0: (1.0, 0.0, 0.0), 1: (1.0, 0.0, 0.0)})
    manager.update(1.0)

    assert app._server.get_marker(10).pos == pytest.approx((2.0, 0.0, 0.0))
    assert app._server.get_marker(11).pos == pytest.approx((-1.0, 0.0, 0.0))


# --------------------------------------------------------------------------- #
# Paths: every input writes a locked marker through the path engine
# --------------------------------------------------------------------------- #


def _cross_engine():  # noqa: ANN202
    from openfollow.configuration import PathConfig, PathPointConfig, PathsConfig
    from openfollow.paths.engine import PathEngine

    engine = PathEngine()
    cross = PathConfig(name="cross", active=True, points=[PathPointConfig(-5, 0, 0, 0), PathPointConfig(5, 0, 0, 0)])
    engine.reload(PathsConfig(slide_time=0.0, paths=[cross]), enabled=True)
    return engine


@pytest.fixture
def path_app(monkeypatch):  # noqa: ANN001, ANN201
    monkeypatch.setattr(input_manager_module, "KeyboardHandler", _FakeKeyboardHandler)
    monkeypatch.setattr(input_manager_module, "GamepadHandler", _FakeGamepadHandler)
    app = _DummyApp()
    app._paths = _cross_engine()
    return app


def test_a_locked_marker_lands_then_moves_along_its_path_at_its_height(path_app) -> None:  # noqa: ANN001
    app = path_app
    app._server.get_marker(10).set_pos(0.0, 2.0, 0.0)
    app._paths.press(10, (0.0, 2.0, 0.0))
    # Up and across, and Z up: only the part along the path moves it, and Z is the path's.
    _FakeKeyboardHandler.next_velocity = (0.5, 0.5, 1.0)
    InputManager(app).update(2.0)
    assert app._server.get_marker(10).pos == pytest.approx((1.0, 0.0, 0.0))


def test_a_pad_s_pathfinder_press_locks_the_marker_that_pad_drives(path_app) -> None:  # noqa: ANN001
    app = path_app
    app._server.get_marker(11).set_pos(2.0, 3.0, 0.0)
    _FakeGamepadHandler.next_update = GamepadUpdate(pathfinder_presses={1})
    InputManager(app).update(0.02)
    assert app._paths.locked_path(11) == "cross"
    assert not app._paths.is_locked(10)


def test_a_gamepad_push_moves_a_locked_marker_along_its_path(path_app) -> None:  # noqa: ANN001
    app = path_app
    app._paths.press(11, (0.0, 0.0, 0.0))
    app._paths.tick(0.0)
    _FakeGamepadHandler.next_update = GamepadUpdate(movements={1: (0.0, 1.0, 1.0)})
    InputManager(app).update(1.0)
    assert app._server.get_marker(11).pos == pytest.approx((0.0, 0.0, 0.0))


def test_a_reset_releases_the_marker_from_its_path(path_app) -> None:  # noqa: ANN001
    app = path_app
    app._paths.press(10, (0.0, 1.0, 0.0))
    _FakeGamepadHandler.next_update = GamepadUpdate(resets={0})
    InputManager(app).update(0.02)
    assert not app._paths.is_locked(10)
    assert app._server.get_marker(10).pos == pytest.approx((5.0, 6.0, 7.0))


def test_an_osc_position_for_a_locked_marker_lands_on_its_path(path_app) -> None:  # noqa: ANN001
    app = path_app
    app._paths.press(10, (0.0, 0.0, 0.0))
    app._paths.tick(0.0)
    manager = InputManager(app)
    manager.osc_handler = SimpleNamespace(flush_updates=lambda: {10: {"x": 3.0, "y": 4.0, "z": 9.0}})
    manager.update(0.02)
    assert app._server.get_marker(10).pos == pytest.approx((3.0, 0.0, 0.0))


@pytest.mark.parametrize(
    ("axes", "pulled", "landed"),
    [
        # Only Z: nothing moves, and the pull carries on from where it was.
        ({"z": 0.0}, (0.0, 1.0, 0.0), (0.0, 0.0, 0.0)),
        # Only X: the free position moves along X and keeps its Y.
        ({"x": 3.0}, (3.0, 1.0, 0.0), (3.0, 0.0, 0.0)),
    ],
)
def test_a_per_axis_osc_position_during_the_pull_keeps_the_axes_it_leaves_out(  # noqa: ANN001
    path_app, axes, pulled, landed
) -> None:
    from openfollow.configuration import PathConfig, PathPointConfig, PathsConfig

    app = path_app
    cross = PathConfig(name="cross", active=True, points=[PathPointConfig(-5, 0, 0, 0), PathPointConfig(5, 0, 0, 0)])
    app._paths.reload(PathsConfig(slide_time=1.0, paths=[cross]), enabled=True)
    marker = app._server.get_marker(10)
    marker.set_pos(0.0, 2.0, 0.0)
    app._paths.press(10, marker.pos)
    manager = InputManager(app)
    manager.update_paths(0.5)
    manager.osc_handler = SimpleNamespace(flush_updates=lambda: {10: axes})
    manager.update(0.0)
    assert marker.pos == pytest.approx(pulled)
    manager.update_paths(0.5)
    assert marker.pos == pytest.approx(landed)


def test_a_marker_no_longer_controlled_is_released(path_app) -> None:  # noqa: ANN001
    app = path_app
    app._paths.press(99, (0.0, 1.0, 0.0))
    InputManager(app).update_paths(0.02)
    assert not app._paths.is_locked(99)


def test_with_no_marker_controlled_every_lock_is_released(path_app) -> None:  # noqa: ANN001
    app = path_app
    app._paths.press(10, (0.0, 1.0, 0.0))
    app._controlled_ids = []
    InputManager(app).update_paths(0.02)
    assert not app._paths.is_locked(10)


def test_path_commands_from_osc_reach_the_engine_and_the_marker_lands_that_frame(path_app) -> None:  # noqa: ANN001
    from openfollow.osc.paths import MarkerPath

    app = path_app
    app._server.get_marker(10).set_pos(2.0, 1.0, 0.0)
    manager = InputManager(app)
    manager.path_osc_handler = SimpleNamespace(take=lambda: [MarkerPath(10, "cross")])
    manager.update_paths(0.02)
    assert app._paths.locked_path(10) == "cross"
    assert app._server.get_marker(10).pos == pytest.approx((2.0, 0.0, 0.0))


def test_a_station_controlling_no_marker_still_switches_paths(path_app, tmp_path) -> None:  # noqa: ANN001
    from openfollow.configuration import PathConfig, PathPointConfig, PathsConfig
    from openfollow.osc.paths import PathActive

    app = path_app
    app._controlled_ids = []
    app._config.ui.show_experimental_features = True
    app._config.paths = PathsConfig(
        slide_time=0.0,
        paths=[PathConfig(name="cross", active=True, points=[PathPointConfig(-5, 0, 0), PathPointConfig(5, 0, 0)])],
    )
    app._config_path = str(tmp_path / "config.toml")
    app._get_config_mtime = lambda: 1.0
    manager = InputManager(app)
    manager.path_osc_handler = SimpleNamespace(take=lambda: [PathActive("cross", False)])
    manager.update_paths(0.02)
    assert app._config.paths.paths[0].active is False
    assert app._paths.press(10, (0.0, 1.0, 0.0)).value == "none"


def test_without_paths_path_commands_are_still_taken(monkeypatch) -> None:  # noqa: ANN001
    from openfollow.osc.paths import PathActive

    monkeypatch.setattr(input_manager_module, "KeyboardHandler", _FakeKeyboardHandler)
    monkeypatch.setattr(input_manager_module, "GamepadHandler", _FakeGamepadHandler)
    queued = [PathActive("cross", False)]
    manager = InputManager(_DummyApp())
    manager.path_osc_handler = SimpleNamespace(take=lambda: [queued.pop()] if queued else [])
    manager.update_paths(0.02)
    assert queued == []


def test_a_slide_carries_on_while_a_menu_is_open(path_app) -> None:  # noqa: ANN001
    from openfollow.configuration import PathConfig, PathPointConfig, PathsConfig
    from openfollow.runtime.app_modes import process_input

    app = path_app
    cross = PathConfig(name="cross", active=True, points=[PathPointConfig(-5, 0, 0, 0), PathPointConfig(5, 0, 0, 0)])
    app._paths.reload(PathsConfig(slide_time=1.0, paths=[cross]), enabled=True)
    app._server.get_marker(10).set_pos(0.0, 2.0, 0.0)
    app._paths.press(10, (0.0, 2.0, 0.0))
    manager = InputManager(app)
    app._input_manager = manager
    manager.gamepad_handler.read_settings_toggle = lambda: False
    app._button_detection = None
    app._settings_menu_active = True
    app._process_settings_menu_input = lambda: None
    # One frame as the frame loop runs it: the engine, then input, which the open menu keeps for itself.
    manager.update_paths(0.5)
    process_input(app, 0.5)
    assert app._server.get_marker(10).pos == pytest.approx((0.0, 1.0, 0.0))


def test_the_keyboard_pathfinder_press_locks_the_selected_marker(path_app) -> None:  # noqa: ANN001
    app = path_app
    manager = InputManager(app)
    manager.pathfinder(10)
    assert app._paths.locked_path(10) == "cross"
    manager.pathfinder(None)
    assert app._paths.is_locked(10)


def test_without_paths_a_pathfinder_press_does_nothing(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(input_manager_module, "KeyboardHandler", _FakeKeyboardHandler)
    monkeypatch.setattr(input_manager_module, "GamepadHandler", _FakeGamepadHandler)
    app = _DummyApp()
    InputManager(app).pathfinder(10)
    assert app._server.get_marker(10).pos == pytest.approx((0.0, 0.0, 0.0))


def test_a_sliding_marker_the_server_does_not_have_is_skipped(path_app) -> None:  # noqa: ANN001
    app = path_app
    app._controlled_ids = [10, 11, 12]
    app._paths.press(12, (0.0, 1.0, 0.0))
    app._paths.press(10, (2.0, 1.0, 0.0))
    InputManager(app).update_paths(0.02)
    assert app._server.get_marker(10).pos == pytest.approx((2.0, 0.0, 0.0))


@pytest.mark.parametrize("off", ["tracking off", "fully automatic"])
def test_when_assist_stops_input_moves_a_locked_marker_on_from_its_output(path_app, off: str) -> None:  # noqa: ANN001
    import numpy as np

    from openfollow.runtime.services_detection_pin import apply_detection_pin, get_or_create_manual_marker

    app = path_app
    app._config.detection.enabled = True
    app._config.detection.pin_mode = "assist"
    app._detection_pin_states = {}
    app._detection_pin_mode = "assist"
    app._video_receiver = SimpleNamespace(resolution=(0, 0))
    # The output stands at x = 3 on the path, the anchor input steers at x = 2, locked there.
    app._server.get_marker(10).set_pos(3.0, 0.0, 0.0)
    anchor = get_or_create_manual_marker(app, 10)
    anchor.set_pos(2.0, 0.0, 0.0)
    app._paths.press(10, anchor.pos)
    app._paths.tick(0.0)
    manager = InputManager(app)
    # Assist stops between frames, as a config reload does.
    if off == "tracking off":
        app._config.detection.enabled = False
    else:
        app._config.detection.pin_mode = "replace"
    _FakeKeyboardHandler.next_velocity = (0.5, 0.0, 0.0)
    # One frame in the frame loop's order: the engine, input, then the detection pin.
    manager.update_paths(0.02)
    manager.update(0.02)
    apply_detection_pin(
        app,
        person_detector=None,
        unproject_cam_buffer=np.zeros(7),
        screen_point_buffer=np.zeros((1, 2)),
        dt=0.02,
    )
    assert app._server.get_marker(10).pos == pytest.approx((3.01, 0.0, 0.0))


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _osc_station(monkeypatch, pending: dict[int, dict[str, float]]):  # noqa: ANN202
    """InputManager on a fake clock whose OSC handler hands out ``pending`` every frame."""
    monkeypatch.setattr(input_manager_module, "KeyboardHandler", _FakeKeyboardHandler)
    monkeypatch.setattr(input_manager_module, "GamepadHandler", _FakeGamepadHandler)
    app = _DummyApp()
    app._runtime_services._osc_service = None
    manager = InputManager(app)
    clock = _Clock()
    manager._clock = clock
    adapter = SimpleNamespace(start=lambda: None, stop=lambda: None, flush_updates=lambda: dict(pending))
    monkeypatch.setattr(input_manager_module, "OscMarkerAdapter", lambda *_a, **_kw: adapter)
    paths = SimpleNamespace(start=lambda: None, stop=lambda: None, take=lambda: [])
    monkeypatch.setattr(input_manager_module, "PathOscAdapter", lambda *_a, **_kw: paths)
    manager.osc_handler = adapter
    return app, manager, clock


def _osc_info_lines(caplog) -> list[str]:  # noqa: ANN001
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.INFO]


def _stream(manager, clock, seconds: float, rate_hz: float = 30.0) -> None:  # noqa: ANN001
    for _ in range(round(seconds * rate_hz)):
        manager.update(1 / rate_hz)
        clock.now += 1 / rate_hz


def test_an_osc_position_stream_logs_one_info_line_when_it_starts(monkeypatch, caplog) -> None:
    app, manager, clock = _osc_station(monkeypatch, {10: {"x": 3.0, "y": 4.0, "z": 5.0}})

    with caplog.at_level(logging.INFO, logger=input_manager_module.logger.name):
        _stream(manager, clock, seconds=10.0)

    assert app._server.get_marker(10).pos == pytest.approx((3.0, 4.0, 5.0))
    assert _osc_info_lines(caplog) == ["OSC positions arriving for marker 10, first at (3.000, 4.000, 5.000)"]


def test_an_osc_stream_resuming_after_a_silence_logs_again(monkeypatch, caplog) -> None:
    pending = {10: {"x": 3.0, "y": 4.0, "z": 5.0}}
    _app, manager, clock = _osc_station(monkeypatch, pending)

    with caplog.at_level(logging.INFO, logger=input_manager_module.logger.name):
        manager.update(1 / 30)
        start = clock.now
        # A pause just short of the gap is the same stream; binary fractions keep the sums exact.
        clock.now = start + input_manager_module._OSC_STREAM_GAP_S - 0.125
        manager.update(1 / 30)
        pending[10] = {"x": 1.0}
        clock.now += input_manager_module._OSC_STREAM_GAP_S
        _stream(manager, clock, seconds=2.0)

    assert _osc_info_lines(caplog) == [
        "OSC positions arriving for marker 10, first at (3.000, 4.000, 5.000)",
        "OSC positions arriving for marker 10, first at (1.000, 4.000, 5.000)",
    ]


def test_each_marker_logs_the_start_of_its_own_osc_stream(monkeypatch, caplog) -> None:
    pending = {10: {"x": 3.0, "y": 4.0, "z": 5.0}}
    _app, manager, clock = _osc_station(monkeypatch, pending)

    with caplog.at_level(logging.INFO, logger=input_manager_module.logger.name):
        _stream(manager, clock, seconds=1.0)
        pending[11] = {"x": -1.0, "y": 2.0, "z": 0.5}
        _stream(manager, clock, seconds=1.0)

    assert _osc_info_lines(caplog) == [
        "OSC positions arriving for marker 10, first at (3.000, 4.000, 5.000)",
        "OSC positions arriving for marker 11, first at (-1.000, 2.000, 0.500)",
    ]


def test_every_osc_position_write_is_traced_at_debug(monkeypatch, caplog) -> None:
    _app, manager, clock = _osc_station(monkeypatch, {10: {"x": 3.0, "y": 4.0, "z": 5.0}})

    with caplog.at_level(logging.DEBUG, logger=input_manager_module.logger.name):
        _stream(manager, clock, seconds=0.1)

    assert [r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG] == [
        "OSC jump: marker 10 → (3.000, 4.000, 5.000)"
    ] * 3


def test_an_osc_stream_on_a_restarted_listener_logs_again(monkeypatch, caplog) -> None:
    pending = {10: {"x": 3.0, "y": 4.0, "z": 5.0}}
    _app, manager, clock = _osc_station(monkeypatch, pending)

    with caplog.at_level(logging.INFO, logger=input_manager_module.logger.name):
        _stream(manager, clock, seconds=1.0)
        manager.restart_osc(enabled=True, port=9001)
        _stream(manager, clock, seconds=1.0)

    assert _osc_info_lines(caplog) == ["OSC positions arriving for marker 10, first at (3.000, 4.000, 5.000)"] * 2
