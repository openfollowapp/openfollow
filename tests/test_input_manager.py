# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Integration tests for InputManager: keyboard/gamepad routing, single- vs
multi-gamepad marker resolution, button-signal forwarding, MIDI hotplug, and
per-subsystem failure isolation."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import openfollow.input.input_manager as input_manager_module
from openfollow.configuration import AppConfig
from openfollow.input.gamepad import GamepadUpdate
from openfollow.input.input_manager import InputManager
from openfollow.operator_messages import OperatorMessageStore
from openfollow.psn.marker import Marker

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
    gamepad belongs to a specific tracker."""
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
            "connected": True,
            "marker_id": 11,
            "effective_speed": 0.0,
            "backend": "",
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
