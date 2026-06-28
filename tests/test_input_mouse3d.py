# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for the 3D Mouse (6DOF) input handler and its InputManager wiring.

The device is mocked at the boundary – a fake reader returning scripted state
snapshots – so the suite never imports real ``pyspacemouse`` or touches HID.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

import openfollow.input.input_manager as input_manager_module
from openfollow.configuration import AppConfig, MarkerConfig, Mouse3DConfig
from openfollow.input.gamepad import GamepadUpdate
from openfollow.input.input_manager import InputManager
from openfollow.input.mouse3d import Mouse3DHandler, Mouse3DUpdate
from openfollow.operator_messages import OperatorMessageStore
from openfollow.psn.marker import Marker

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


def _state(*, x=0.0, y=0.0, z=0.0, roll=0.0, pitch=0.0, yaw=0.0, buttons=()):  # noqa: ANN001, ANN201
    return SimpleNamespace(x=x, y=y, z=z, roll=roll, pitch=pitch, yaw=yaw, buttons=list(buttons))


class FakeDevice:
    """Minimal pyspacemouse device: replays scripted states, then ``None``."""

    def __init__(self, states: list[object] | None = None) -> None:
        self._states = list(states or [])
        self.closed = False

    def read(self) -> object | None:
        if self._states:
            return self._states.pop(0)
        return None

    def close(self) -> None:
        self.closed = True


def _handler(cfg: Mouse3DConfig | None = None, *, snapshot=None) -> Mouse3DHandler:  # noqa: ANN001
    """Handler with no thread; optionally seed the consumer snapshot directly."""
    h = Mouse3DHandler(cfg or Mouse3DConfig(enabled=True), device_factory=lambda: None)
    if snapshot is not None:
        h._snapshot = snapshot
    return h


# --------------------------------------------------------------------------- #
# Axis -> velocity mapping
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "axis_kwargs,expected",
    [
        ({"x": 1.0}, (1.0, 0.0, 0.0)),  # pan_x -> x
        ({"y": 1.0}, (0.0, 1.0, 0.0)),  # pan_y -> y
        ({"z": 1.0}, (0.0, 0.0, 1.0)),  # lift -> z
    ],
)
def test_default_axis_targets_drive_xyz(axis_kwargs, expected) -> None:  # noqa: ANN001
    h = _handler(snapshot=_state(**axis_kwargs))
    assert h.update(0.016).velocity == pytest.approx(expected)


def test_rotation_axes_default_to_none() -> None:
    # pitch/yaw/roll default to "none" -> no velocity contribution.
    h = _handler(snapshot=_state(pitch=1.0, yaw=1.0, roll=1.0))
    assert h.update(0.016).velocity == pytest.approx((0.0, 0.0, 0.0))


def test_invert_flips_axis_sign() -> None:
    h = _handler(Mouse3DConfig(invert_pan_x=True), snapshot=_state(x=1.0))
    assert h.update(0.016).velocity[0] == pytest.approx(-1.0)


def test_sensitivity_scales_velocity() -> None:
    h = _handler(Mouse3DConfig(sens_pan_x=2.5), snapshot=_state(x=1.0))
    assert h.update(0.016).velocity[0] == pytest.approx(2.5)


def test_remapped_axis_target() -> None:
    # pan_x re-pointed at z instead of x.
    h = _handler(Mouse3DConfig(map_pan_x="z"), snapshot=_state(x=1.0))
    assert h.update(0.016).velocity == pytest.approx((0.0, 0.0, 1.0))


def test_two_axes_summing_into_same_target() -> None:
    cfg = Mouse3DConfig(map_pan_x="x", map_pan_y="x")
    h = _handler(cfg, snapshot=_state(x=1.0, y=1.0))
    assert h.update(0.016).velocity[0] == pytest.approx(2.0)


# --------------------------------------------------------------------------- #
# Deadzone + curve shaping
# --------------------------------------------------------------------------- #


def test_deadzone_suppresses_small_deflection() -> None:
    h = _handler(Mouse3DConfig(deadzone=0.2), snapshot=_state(x=0.1))
    assert h.update(0.016).velocity[0] == 0.0


def test_deadzone_rescales_above_threshold() -> None:
    # At deadzone 0.5, an input of 0.75 maps to (0.75-0.5)/(1-0.5) = 0.5 (linear).
    h = _handler(Mouse3DConfig(deadzone=0.5), snapshot=_state(x=0.75))
    assert h.update(0.016).velocity[0] == pytest.approx(0.5)


def test_quadratic_curve_shapes_magnitude() -> None:
    # quadratic, no deadzone: 0.5 -> 0.25.
    h = _handler(Mouse3DConfig(deadzone=0.0, curve="quadratic"), snapshot=_state(x=0.5))
    assert h.update(0.016).velocity[0] == pytest.approx(0.25)


def test_full_deadzone_is_inert() -> None:
    # deadzone 1.0 must not divide by zero, even at full deflection.
    h = _handler(Mouse3DConfig(deadzone=1.0), snapshot=_state(x=1.0))
    assert h.update(0.016).velocity[0] == 0.0


# --------------------------------------------------------------------------- #
# speed-mapped axis
# --------------------------------------------------------------------------- #


def test_speed_axis_accumulates_into_steps() -> None:
    # pitch mapped to speed, full deflection. Rate is 6 steps/s; one big frame
    # (dt=1s) yields 6 steps; velocity stays zero (speed isn't movement).
    h = _handler(Mouse3DConfig(map_pitch="speed"), snapshot=_state(pitch=1.0))
    out = h.update(1.0)
    assert out.velocity == pytest.approx((0.0, 0.0, 0.0))
    assert out.speed_steps == 6


def test_speed_axis_negative_direction() -> None:
    h = _handler(Mouse3DConfig(map_pitch="speed"), snapshot=_state(pitch=-1.0))
    assert h.update(1.0).speed_steps == -6


def test_speed_axis_resets_accumulator_when_centered() -> None:
    h = _handler(Mouse3DConfig(map_pitch="speed"), snapshot=_state(pitch=0.1))
    # Below default deadzone 0.1 -> shaped 0 -> no steps, accumulator cleared.
    assert h.update(1.0).speed_steps == 0


# --------------------------------------------------------------------------- #
# Buttons + edge detection
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "cfg_kwargs,index,attr",
    [
        ({"btn_reset": 0}, 0, "reset"),
        ({"btn_next_marker": 1}, 1, "next_marker"),
        ({"btn_prev_marker": 2}, 2, "prev_marker"),
        ({"btn_toggle_help": 3}, 3, "toggle_help"),
        ({"btn_toggle_zones": 4}, 4, "toggle_zones"),
        ({"btn_settings": 5}, 5, "settings"),
    ],
)
def test_button_edge_fires_bound_action(cfg_kwargs, index, attr) -> None:  # noqa: ANN001
    buttons = [0] * 6
    buttons[index] = 1
    h = _handler(Mouse3DConfig(**cfg_kwargs), snapshot=_state(buttons=buttons))
    assert getattr(h.update(0.016), attr) is True


def test_speed_buttons_step_up_and_down() -> None:
    h = _handler(Mouse3DConfig(btn_speed_up=0, btn_speed_down=1))
    h._snapshot = _state(buttons=[1, 0])
    assert h.update(0.016).speed_steps == 1
    # Re-press the down button (release up first so it doesn't re-fire).
    h._snapshot = _state(buttons=[0, 1])
    assert h.update(0.016).speed_steps == -1


def test_held_button_does_not_refire_until_released() -> None:
    h = _handler(Mouse3DConfig(btn_reset=0))
    h._snapshot = _state(buttons=[1])
    assert h.update(0.016).reset is True  # rising edge
    assert h.update(0.016).reset is False  # still held -> no edge
    h._snapshot = _state(buttons=[0])
    h.update(0.016)
    h._snapshot = _state(buttons=[1])
    assert h.update(0.016).reset is True  # re-pressed -> edge again


def test_unbound_button_index_never_fires() -> None:
    # btn_prev_marker defaults to -1 (unbound); no button index can trigger it.
    h = _handler(Mouse3DConfig(), snapshot=_state(buttons=[1, 1, 1, 1]))
    assert h.update(0.016).prev_marker is False


def test_no_snapshot_yields_empty_update() -> None:
    h = Mouse3DHandler(Mouse3DConfig(enabled=True), device_factory=lambda: None)
    assert h.update(0.016) == Mouse3DUpdate()


# --------------------------------------------------------------------------- #
# Config reload + status
# --------------------------------------------------------------------------- #


def test_reload_config_swaps_mapping() -> None:
    h = _handler(Mouse3DConfig(map_pan_x="x"), snapshot=_state(x=1.0))
    assert h.update(0.016).velocity == pytest.approx((1.0, 0.0, 0.0))
    h.reload_config(Mouse3DConfig(map_pan_x="y"))
    assert h.update(0.016).velocity == pytest.approx((0.0, 1.0, 0.0))


def test_latest_button_returns_pressed_index() -> None:
    h = _handler(snapshot=_state(buttons=[0, 0, 1]))
    assert h.latest_button() == 2


def test_latest_button_none_without_press() -> None:
    h = _handler(snapshot=_state(buttons=[0, 0, 0]))
    assert h.latest_button() is None
    assert _handler().latest_button() is None  # no snapshot


def test_detect_pressed_button_one_shot_when_not_running() -> None:
    # Feature disabled / thread not started -> one-shot device open finds a press
    # so buttons can be bound while configuring.
    dev = FakeDevice([_state(buttons=[0, 1])])
    h = Mouse3DHandler(Mouse3DConfig(enabled=False), device_factory=lambda: dev)
    assert h._thread is None
    assert h.detect_pressed_button(timeout=0.5) == 1
    assert dev.closed is True  # one-shot probe opens then closes


def test_detect_pressed_button_times_out_to_none() -> None:
    dev = FakeDevice([_state(buttons=[0, 0])])  # no press, then read() -> None
    h = Mouse3DHandler(Mouse3DConfig(enabled=False), device_factory=lambda: dev)
    assert h.detect_pressed_button(timeout=0.05) is None


def test_detect_pressed_button_uses_snapshot_when_running() -> None:
    dev = FakeDevice([_state(buttons=[1, 0])])
    h = Mouse3DHandler(Mouse3DConfig(enabled=True), device_factory=lambda: dev)
    h.start()
    try:
        assert _wait_until(lambda: h._snapshot is not None)
        assert h.detect_pressed_button(timeout=1.0) == 0
    finally:
        h.stop()


# --------------------------------------------------------------------------- #
# Worker thread (fake device)
# --------------------------------------------------------------------------- #


def _wait_until(predicate, timeout=2.0) -> bool:  # noqa: ANN001
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def test_worker_publishes_snapshot_from_device() -> None:
    dev = FakeDevice([_state(x=0.5, buttons=[1, 0])])
    # deadzone 0 -> identity mapping so the published 0.5 reads back as 0.5.
    h = Mouse3DHandler(Mouse3DConfig(enabled=True, deadzone=0.0), device_factory=lambda: dev)
    h.start()
    try:
        assert _wait_until(lambda: h.connected and h._snapshot is not None)
        assert h.available is True
        assert h.latest_button() == 0
        # The published snapshot maps through update().
        assert _wait_until(lambda: h.update(0.016).velocity[0] == pytest.approx(0.5))
    finally:
        h.stop()
    assert dev.closed is True


def test_worker_marks_disconnected_when_no_device() -> None:
    h = Mouse3DHandler(Mouse3DConfig(enabled=True), device_factory=lambda: None)
    h.start()
    try:
        # Factory provided -> deps considered available; but no device opens.
        assert _wait_until(lambda: h.available and not h.connected)
    finally:
        h.stop()


def test_start_is_noop_when_disabled() -> None:
    # Default-disabled config: the device is only read while in use.
    h = Mouse3DHandler(Mouse3DConfig(enabled=False), device_factory=lambda: FakeDevice())
    h.start()
    try:
        assert h._thread is None
    finally:
        h.stop()


def test_start_is_noop_without_pyspacemouse(monkeypatch) -> None:
    # Enabled but pyspacemouse missing -> no thread (deterministic regardless of
    # whether the package happens to be installed in the test env).
    import openfollow.input.mouse3d as m3d_mod

    monkeypatch.setattr(m3d_mod, "check_mouse3d_dependencies", lambda: ["pyspacemouse"])
    h = Mouse3DHandler(Mouse3DConfig(enabled=True))
    h.start()
    try:
        assert h._thread is None
        assert h.available is False
    finally:
        h.stop()


def test_run_marks_unavailable_when_import_fails(monkeypatch) -> None:
    h = Mouse3DHandler(Mouse3DConfig(enabled=True))

    def _boom():
        raise OSError("libhidapi not found")

    monkeypatch.setattr(h, "_resolve_factory", _boom)
    h._run()  # synchronous; returns immediately on the import failure
    assert h.available is False
    assert h._import_error is not None


# --------------------------------------------------------------------------- #
# InputManager wiring
# --------------------------------------------------------------------------- #


class _FakeKeyboardHandler:
    def __init__(self, app, *, event_bus=None) -> None:  # noqa: ANN001
        self.app = app

    @property
    def keys(self) -> set[str]:
        return set()

    def update(self, _dt: float):  # noqa: ANN201
        return None

    def is_connected(self) -> bool:
        return True


class _FakeGamepadHandler:
    def __init__(self, app, *, event_bus=None, virtual_faders=None, marker_resolver=None) -> None:  # noqa: ANN001
        self.app = app
        self.joysticks = {0: object()}

    def update(self, _dt: float) -> GamepadUpdate:
        return GamepadUpdate()

    def get_controller_info(self) -> list[dict]:
        return []

    def stop(self) -> None:
        pass


class _FakeMouse3DHandler:
    """Returns a scripted Mouse3DUpdate; records lifecycle calls."""

    def __init__(self, config, *, device_factory=None) -> None:  # noqa: ANN001
        self.config = config
        self.next_update = Mouse3DUpdate()
        self.update_calls = 0
        self.started = False
        self.stopped = False
        self.reloaded_with = None

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def reload_config(self, config) -> None:  # noqa: ANN001
        self.reloaded_with = config

    def update(self, _dt: float) -> Mouse3DUpdate:
        self.update_calls += 1
        return self.next_update


class _DummyServer:
    def __init__(self) -> None:
        self.markers: dict[int, Marker] = {}

    def add_marker(self, marker_id: int) -> Marker:
        marker = Marker(marker_id, f"Marker {marker_id}")
        self.markers[marker_id] = marker
        return marker

    def get_marker(self, marker_id: int) -> Marker | None:
        return self.markers.get(marker_id)


class _DummyApp:
    def __init__(self) -> None:
        self._config = AppConfig()
        self._config.osc.enabled = False
        self._config.marker = MarkerConfig(move_speed=2.0, default_pos_x=5.0, default_pos_y=6.0, default_pos_z=7.0)
        self._controlled_ids = [10, 11]
        self._selected_id = 10
        self._server = _DummyServer()
        self._server.add_marker(10).set_pos(0.0, 0.0, 0.0)
        self._server.add_marker(11).set_pos(0.0, 0.0, 0.0)
        self._assist_manual: dict[int, Marker] = {}
        self.move_speed_calls: list[tuple[int, int | None]] = []
        self._runtime_services = SimpleNamespace(
            _virtual_faders=None,
            _operator_message_store=OperatorMessageStore(),
        )

    def _get_default_marker_position(self) -> tuple[float, float, float]:
        cfg = self._config.marker
        return (cfg.default_pos_x, cfg.default_pos_y, cfg.default_pos_z)

    def get_marker_move_speed(self, marker_id: int | None) -> float:
        return self._config.marker.move_speed

    def adjust_move_speed(self, direction: int, marker_id: int | None = None) -> None:
        self.move_speed_calls.append((direction, marker_id))


@pytest.fixture
def wired(monkeypatch):  # noqa: ANN001, ANN201
    monkeypatch.setattr(input_manager_module, "KeyboardHandler", _FakeKeyboardHandler)
    monkeypatch.setattr(input_manager_module, "GamepadHandler", _FakeGamepadHandler)
    monkeypatch.setattr(input_manager_module, "Mouse3DHandler", _FakeMouse3DHandler)
    app = _DummyApp()
    manager = InputManager(app)
    return manager, app


def test_inputmanager_starts_mouse3d(wired) -> None:  # noqa: ANN001
    manager, _ = wired
    assert manager.mouse3d_handler.started is True


def test_disabled_mouse3d_does_not_move_or_consume(wired) -> None:  # noqa: ANN001
    manager, app = wired
    app._config.mouse3d.enabled = False
    manager.mouse3d_handler.next_update = Mouse3DUpdate(velocity=(1.0, 0.0, 0.0))
    manager.update(1.0)
    assert manager.mouse3d_handler.update_calls == 0
    assert app._server.get_marker(10).pos == pytest.approx((0.0, 0.0, 0.0))


def test_enabled_mouse3d_moves_selected_marker_scaled_by_speed(wired) -> None:  # noqa: ANN001
    manager, app = wired
    app._config.mouse3d.enabled = True
    manager.mouse3d_handler.next_update = Mouse3DUpdate(velocity=(1.0, 0.0, 0.0))
    manager.update(1.0)  # dt=1, move_speed=2.0 -> +2.0 in x on the selected marker
    assert app._server.get_marker(10).pos == pytest.approx((2.0, 0.0, 0.0))
    assert app._server.get_marker(11).pos == pytest.approx((0.0, 0.0, 0.0))


def test_enabled_mouse3d_reset_and_speed(wired) -> None:  # noqa: ANN001
    manager, app = wired
    app._config.mouse3d.enabled = True
    manager.mouse3d_handler.next_update = Mouse3DUpdate(reset=True, speed_steps=2)
    manager.update(0.016)
    assert app._server.get_marker(10).pos == pytest.approx((5.0, 6.0, 7.0))  # default pos
    assert app.move_speed_calls == [(1, 10), (1, 10)]


def test_mouse3d_buttons_fold_into_gamepad_result(wired) -> None:  # noqa: ANN001
    manager, app = wired
    app._config.mouse3d.enabled = True
    manager.mouse3d_handler.next_update = Mouse3DUpdate(next_marker=True, settings=True, toggle_help=True)
    result = manager.update(0.016)
    assert result.next_marker_pressed is True
    assert result.settings_open_pressed is True
    assert result.toggle_help_pressed is True


def test_restart_mouse3d_enabled_reloads_and_starts(wired) -> None:  # noqa: ANN001
    manager, _ = wired
    new_cfg = Mouse3DConfig(enabled=True, sens_pan_x=3.0)
    manager.restart_mouse3d(new_cfg)
    assert manager.mouse3d_handler.reloaded_with is new_cfg
    assert manager.mouse3d_handler.started is True


def test_restart_mouse3d_disabled_stops(wired) -> None:  # noqa: ANN001
    manager, _ = wired
    manager.restart_mouse3d(Mouse3DConfig(enabled=False))
    assert manager.mouse3d_handler.stopped is True
