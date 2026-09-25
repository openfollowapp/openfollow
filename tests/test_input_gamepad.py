# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for the gamepad input handler.

Covers the logical surface of :mod:`openfollow.input.gamepad` without
touching real hardware: pygame's joystick / controller / event surfaces
are monkey-patched so the suite stays hermetic while the branching
logic (deadzones, response curves, trigger/bumper routing, button
remapping, hat sentinels, startup-retry, source-selection /
settings-menu input shapes, cleanup and stop) is exercised end-to-end.
"""

from __future__ import annotations

import ctypes
import logging
import math
import os
import re

import pygame
import pytest

import openfollow.input.gamepad as gp
from openfollow.configuration import AppConfig, ControllerConfig, MarkerConfig
from openfollow.input.gamepad import (
    BUMPER_LEFT_BUTTON_INDICES,
    BUMPER_RIGHT_BUTTON_INDICES,
    BUTTON_ID_LT,
    BUTTON_ID_RT,
    BUTTON_ID_UNBOUND,
    CONTROLLER_AXIS_LEFTX,
    CONTROLLER_AXIS_LEFTY,
    CONTROLLER_AXIS_RIGHTX,
    CONTROLLER_AXIS_TRIGGERLEFT,
    CONTROLLER_AXIS_TRIGGERRIGHT,
    CONTROLLER_BUTTON_A,
    CONTROLLER_BUTTON_B,
    CONTROLLER_BUTTON_BACK,
    CONTROLLER_BUTTON_DPAD_DOWN,
    CONTROLLER_BUTTON_DPAD_LEFT,
    CONTROLLER_BUTTON_DPAD_RIGHT,
    CONTROLLER_BUTTON_DPAD_UP,
    CONTROLLER_BUTTON_LEFTSHOULDER,
    CONTROLLER_BUTTON_RIGHTSHOULDER,
    CONTROLLER_BUTTON_START,
    CONTROLLER_BUTTON_X,
    CONTROLLER_BUTTON_Y,
    HAT_DOWN,
    HAT_LEFT,
    HAT_RIGHT,
    HAT_UP,
    ControllerCapabilities,
    ControllerRuntimeInfo,
    GamepadHandler,
    GamepadUpdate,
    SettingsMenuInput,
    SourceSelectionInput,
)

pytestmark = pytest.mark.unit

# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


class FakeJoystick:
    """Minimal pygame.joystick.Joystick replacement."""

    def __init__(
        self,
        *,
        num_buttons: int = 12,
        num_hats: int = 1,
        num_axes: int = 6,
        name: str = "FakePad",
        guid: str = "0000",
        instance_id: int | None = None,
    ) -> None:
        self._buttons = [False] * num_buttons
        self._hats = [(0, 0)] * num_hats
        self._axes = [0.0] * num_axes
        self._name = name
        self._guid = guid
        # ``None`` = let the stubbed ``Joystick(idx)`` factory stamp the index,
        # which is what SDL hands out on a fresh start.
        self.instance_id = instance_id
        self.quit_called = False

    # Surface read by the handler
    def quit(self) -> None:
        self.quit_called = True

    def get_numbuttons(self) -> int:
        return len(self._buttons)

    def get_button(self, idx: int) -> int:
        return 1 if self._buttons[idx] else 0

    def get_numhats(self) -> int:
        return len(self._hats)

    def get_hat(self, idx: int) -> tuple[int, int]:
        return self._hats[idx]

    def get_numaxes(self) -> int:
        return len(self._axes)

    def get_axis(self, idx: int) -> float:
        return self._axes[idx]

    def get_name(self) -> str:
        return self._name

    def get_guid(self) -> str:
        return self._guid

    def get_instance_id(self) -> int:
        return 0 if self.instance_id is None else self.instance_id

    # Test helpers
    def press(self, btn: int) -> None:
        self._buttons[btn] = True

    def release(self, btn: int) -> None:
        self._buttons[btn] = False

    def set_hat(self, direction: tuple[int, int], idx: int = 0) -> None:
        self._hats[idx] = direction

    def set_axis(self, idx: int, value: float) -> None:
        self._axes[idx] = value


class FakeController:
    """Minimal SDL2 Controller replacement."""

    def __init__(self) -> None:
        self._axes: dict[int, int] = {}
        self._buttons: dict[int, bool] = {}
        self.quit_called = False

    def quit(self) -> None:
        self.quit_called = True

    def get_axis(self, axis: int) -> int:
        return self._axes.get(axis, 0)

    def get_button(self, btn: int) -> int:
        return 1 if self._buttons.get(btn, False) else 0

    def set_axis(self, axis: int, value: int) -> None:
        self._axes[axis] = value

    def press(self, btn: int) -> None:
        self._buttons[btn] = True

    def release(self, btn: int) -> None:
        self._buttons[btn] = False


class FakeApp:
    """Minimal app stub exposing the surface GamepadHandler reads."""

    def __init__(self, controller_cfg: ControllerConfig | None = None) -> None:
        self._config = AppConfig()
        if controller_cfg is not None:
            self._config.controller = controller_cfg
        self._config.marker = MarkerConfig(move_speed=4.0)
        # Tuple ``(delta, marker_id)`` mirrors the signature
        # extension: ``adjust_move_speed`` now takes an optional marker_id
        # routed by the gamepad handler's resolver.
        self.move_speed_calls: list[tuple[int, int | None]] = []

    def adjust_move_speed(self, delta: int, marker_id: int | None = None) -> None:
        self.move_speed_calls.append((delta, marker_id))

    def get_marker_move_speed(self, marker_id: int | None) -> float:
        """Mirror ``OpenFollowApp.get_marker_move_speed`` for handler reads."""
        if marker_id is None:
            return self._config.marker.move_speed
        return self._config.marker_move_speeds.get(
            marker_id,
            self._config.marker.move_speed,
        )


# --------------------------------------------------------------------------- #
# Pygame / sdl2 stubbing fixture
# --------------------------------------------------------------------------- #


@pytest.fixture
def stubbed_pygame(monkeypatch):
    """Neutralise pygame calls that the handler makes at construct time.

    Tests can override the joystick count or the event stream before or
    after instantiation.  Returns a small namespace for the test to
    manipulate.
    """

    events: list[object] = []
    posted: list[object] = []
    state = {"count": 0, "factories": {}, "instance_ids": {}, "lookup": False, "pucks": set()}

    monkeypatch.setattr(pygame, "get_init", lambda: True)
    monkeypatch.setattr(pygame, "init", lambda: None)
    monkeypatch.setattr(pygame.joystick, "get_init", lambda: True)
    monkeypatch.setattr(pygame.joystick, "init", lambda: None)
    monkeypatch.setattr(pygame.joystick, "quit", lambda: None)
    monkeypatch.setattr(pygame.joystick, "get_count", lambda: state["count"])

    def _joystick_factory(idx: int):
        factory = state["factories"].get(idx)
        joy = FakeJoystick() if factory is None else factory(idx)
        if getattr(joy, "instance_id", 0) is None:
            joy.instance_id = idx
        return joy

    monkeypatch.setattr(pygame.joystick, "Joystick", _joystick_factory)
    # SDL's instance-id lookup answers from ``state``; ``lookup`` False stands
    # for a pygame build where it can't be reached.
    monkeypatch.setattr(gp, "_device_instance_id", lambda idx: state["instance_ids"].get(idx))
    monkeypatch.setattr(gp, "_instance_id_lookup_available", lambda: state["lookup"])
    monkeypatch.setattr(gp, "_is_spacemouse", lambda idx: idx in state["pucks"])
    monkeypatch.setattr(pygame.event, "get", lambda: list(events))
    monkeypatch.setattr(pygame.event, "post", lambda e: posted.append(e))

    # Disable the sdl2 controller backend in the module (easier than
    # emulating SDL2 init/is_controller surface).  Tests that want to
    # exercise the SDL2 path will re-enable it locally.
    monkeypatch.setattr(gp, "sdl2_controller", None)

    return {
        "events": events,
        "posted": posted,
        "state": state,
    }


def make_handler(
    stubbed_pygame,
    *,
    config: ControllerConfig | None = None,
    app: FakeApp | None = None,
) -> tuple[GamepadHandler, FakeApp]:
    """Construct a GamepadHandler with zero attached devices."""
    if app is None:
        app = FakeApp(controller_cfg=config)
    elif config is not None:
        app._config.controller = config
    handler = GamepadHandler(app)
    return handler, app


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #


class TestNormalizeAxis:
    def test_regular_axis_clamps_to_unit_range(self) -> None:
        assert GamepadHandler._normalize_controller_axis(0) == 0.0
        assert GamepadHandler._normalize_controller_axis(32768) == pytest.approx(1.0)
        assert GamepadHandler._normalize_controller_axis(-32768) == pytest.approx(-1.0)
        assert GamepadHandler._normalize_controller_axis(100000) == pytest.approx(1.0)
        assert GamepadHandler._normalize_controller_axis(-100000) == pytest.approx(-1.0)

    def test_trigger_axis_clamps_to_zero_one(self) -> None:
        assert GamepadHandler._normalize_controller_axis(0, trigger=True) == 0.0
        assert GamepadHandler._normalize_controller_axis(32768, trigger=True) == pytest.approx(1.0)
        assert GamepadHandler._normalize_controller_axis(-32768, trigger=True) == 0.0


class TestReadHatDirection:
    @pytest.mark.parametrize(
        "direction,sentinel",
        [
            ((0, 1), HAT_UP),
            ((0, -1), HAT_DOWN),
            ((-1, 0), HAT_LEFT),
            ((1, 0), HAT_RIGHT),
        ],
    )
    def test_each_cardinal_direction(self, direction: tuple[int, int], sentinel: int) -> None:
        joy = FakeJoystick(num_hats=1)
        joy.set_hat(direction)
        assert GamepadHandler._read_hat_direction(joy, sentinel) is True

    def test_returns_false_when_hat_idle(self) -> None:
        joy = FakeJoystick(num_hats=1)
        for sentinel in (HAT_UP, HAT_DOWN, HAT_LEFT, HAT_RIGHT):
            assert GamepadHandler._read_hat_direction(joy, sentinel) is False

    def test_returns_false_when_no_hats(self) -> None:
        joy = FakeJoystick(num_hats=0)
        assert GamepadHandler._read_hat_direction(joy, HAT_UP) is False


class TestReadButtonState:
    def test_out_of_range_index_returns_false(self) -> None:
        joy = FakeJoystick(num_buttons=4)
        assert GamepadHandler._read_button_state(joy, 99) is False
        assert GamepadHandler._read_button_state(joy, -1) is False

    def test_returns_true_when_pressed(self) -> None:
        joy = FakeJoystick(num_buttons=4)
        joy.press(2)
        assert GamepadHandler._read_button_state(joy, 2) is True
        assert GamepadHandler._read_button_state(joy, 1) is False


# --------------------------------------------------------------------------- #
# Deadzone + response curves
# --------------------------------------------------------------------------- #


class TestApplyDeadzone:
    def test_inside_deadzone_returns_zero(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)
        handler.deadzone = 0.2
        handler.curve = "linear"
        assert handler._apply_deadzone(0.1) == 0.0
        assert handler._apply_deadzone(-0.15) == 0.0

    def test_outside_deadzone_scales_linearly(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)
        handler.deadzone = 0.2
        handler.curve = "linear"
        # Magnitude just above the deadzone scales to near 0
        assert handler._apply_deadzone(0.21) == pytest.approx(0.0125, abs=1e-4)
        # Negative sign preserved
        assert handler._apply_deadzone(-1.0) == pytest.approx(-1.0)

    @pytest.mark.parametrize(
        "curve,inp,expected",
        [
            ("linear", 0.5, 0.5),
            ("quadratic", 0.5, 0.25),
            ("logarithmic", 1.0, 1.0),
            ("s-law", 0.5, 0.5),
            ("unknown", 0.5, 0.5),  # unknown falls back to linear
        ],
    )
    def test_curve_shapes(self, stubbed_pygame, curve: str, inp: float, expected: float) -> None:
        handler, _ = make_handler(stubbed_pygame)
        handler.curve = curve
        assert handler._apply_curve(inp) == pytest.approx(expected, abs=1e-6)

    def test_logarithmic_monotonic(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)
        handler.curve = "logarithmic"
        # y = log10(1+9x) – strictly increasing on [0,1]
        prev = -math.inf
        for x in (0.0, 0.1, 0.3, 0.7, 1.0):
            y = handler._apply_curve(x)
            assert y >= prev
            prev = y


# --------------------------------------------------------------------------- #
# Axis reading
# --------------------------------------------------------------------------- #


class TestReadAxes:
    def test_joystick_left_stick_invert_y_default_flips_sign(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)
        joy = FakeJoystick(num_axes=4)
        joy.set_axis(0, 0.8)  # x
        joy.set_axis(1, 0.8)  # y
        dx, dy, dz = handler._read_axes_from_joystick(joy)
        assert dx > 0
        assert dy < 0  # invert_y=False means y is negated
        assert dz == 0.0

    def test_joystick_invert_y_true_preserves_sign(self, stubbed_pygame) -> None:
        cfg = ControllerConfig(invert_y=True)
        handler, _ = make_handler(stubbed_pygame, config=cfg)
        joy = FakeJoystick(num_axes=4)
        joy.set_axis(1, 0.8)
        _, dy, _ = handler._read_axes_from_joystick(joy)
        assert dy > 0  # invert_y=True means sign preserved

    def test_joystick_right_stick_uses_axes_2_3(self, stubbed_pygame) -> None:
        cfg = ControllerConfig(move_xy_stick="right")
        handler, _ = make_handler(stubbed_pygame, config=cfg)
        joy = FakeJoystick(num_axes=4)
        joy.set_axis(0, 0.5)  # not read (left stick)
        joy.set_axis(2, 0.5)  # right-X
        dx, _, _ = handler._read_axes_from_joystick(joy)
        assert dx > 0

    def test_joystick_too_few_axes_returns_zero(self, stubbed_pygame) -> None:
        cfg = ControllerConfig(move_xy_stick="right")
        handler, _ = make_handler(stubbed_pygame, config=cfg)
        joy = FakeJoystick(num_axes=2)  # right stick needs axis 3
        assert handler._read_axes_from_joystick(joy) == (0.0, 0.0, 0.0)

    def test_controller_backend_left_stick(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)
        ctrl = FakeController()
        ctrl.set_axis(CONTROLLER_AXIS_LEFTX, 32768)
        ctrl.set_axis(CONTROLLER_AXIS_LEFTY, -32768)
        dx, dy, _ = handler._read_axes_from_controller(ctrl)
        assert dx == pytest.approx(1.0)
        # invert_y=False → LEFTY negated
        assert dy == pytest.approx(1.0)

    def test_controller_backend_invert_y(self, stubbed_pygame) -> None:
        cfg = ControllerConfig(invert_y=True)
        handler, _ = make_handler(stubbed_pygame, config=cfg)
        ctrl = FakeController()
        ctrl.set_axis(CONTROLLER_AXIS_LEFTY, -32768)
        _, dy, _ = handler._read_axes_from_controller(ctrl)
        assert dy == pytest.approx(-1.0)

    def test_controller_backend_right_stick(self, stubbed_pygame) -> None:
        cfg = ControllerConfig(move_xy_stick="right")
        handler, _ = make_handler(stubbed_pygame, config=cfg)
        ctrl = FakeController()
        ctrl.set_axis(CONTROLLER_AXIS_RIGHTX, 32768)
        dx, _, _ = handler._read_axes_from_controller(ctrl)
        assert dx == pytest.approx(1.0)

    def test_read_axes_prefers_controller_when_present(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)
        ctrl = FakeController()
        ctrl.set_axis(CONTROLLER_AXIS_LEFTX, 32768)
        joy = FakeJoystick(num_axes=4)
        joy.set_axis(0, -1.0)  # should be ignored
        handler.controllers[0] = ctrl
        handler.joysticks[0] = joy
        dx, _, _ = handler._read_axes(0, joy)
        assert dx == pytest.approx(1.0)  # controller value wins

    def test_read_axes_falls_back_to_joystick_without_controller(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)
        joy = FakeJoystick(num_axes=4)
        joy.set_axis(0, 0.8)
        handler.joysticks[0] = joy
        dx, _, _ = handler._read_axes(0, joy)
        assert dx > 0

    def test_read_axes_swallows_pygame_error(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)

        class ExplodingJoystick(FakeJoystick):
            def get_axis(self, idx: int) -> float:
                raise pygame.error("dead")

        joy = ExplodingJoystick(num_axes=4)
        assert handler._read_axes(0, joy) == (0.0, 0.0, 0.0)


# --------------------------------------------------------------------------- #
# Trigger axis and Z-input
# --------------------------------------------------------------------------- #


class TestTriggerAxisValue:
    def test_baseline_seeded_on_first_read(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)
        joy = FakeJoystick(num_axes=8)
        joy.set_axis(4, -0.5)  # a typical "trigger at rest" baseline
        handler._shoulder_axis_baselines[0] = {}
        # First read seeds the baseline and returns 0.0 for that axis
        result = handler._read_trigger_axis_value(0, joy, (4,))
        assert result == 0.0
        # baseline for axis 4 should now be stored
        assert handler._shoulder_axis_baselines[0][4] == -0.5

    def test_delta_above_baseline_is_returned(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)
        joy = FakeJoystick(num_axes=8)
        joy.set_axis(4, 0.0)  # at rest
        handler._shoulder_axis_baselines[0] = {}
        handler._read_trigger_axis_value(0, joy, (4,))  # seed
        joy.set_axis(4, 0.7)  # deflect
        assert handler._read_trigger_axis_value(0, joy, (4,)) == pytest.approx(0.7)

    def test_best_of_multiple_axes_returned(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)
        joy = FakeJoystick(num_axes=8)
        # Pre-seed baselines so no seeding happens mid-test
        handler._shoulder_axis_baselines[0] = {4: 0.0, 6: 0.0}
        joy.set_axis(4, 0.3)
        joy.set_axis(6, 0.8)
        assert handler._read_trigger_axis_value(0, joy, (4, 6)) == pytest.approx(0.8)

    def test_out_of_range_axes_skipped(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)
        joy = FakeJoystick(num_axes=2)
        handler._shoulder_axis_baselines[0] = {}
        assert handler._read_trigger_axis_value(0, joy, (4, 6)) == 0.0


class TestReadZInput:
    def test_digital_button_returns_one_or_zero(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)
        joy = FakeJoystick(num_buttons=8)
        handler.joysticks[0] = joy
        # No controller; button ID is a plain SDL2 button (not LT/RT sentinel)
        joy.press(CONTROLLER_BUTTON_A)
        assert handler._read_z_input(0, joy, CONTROLLER_BUTTON_A, is_lt=False) == 1.0
        joy.release(CONTROLLER_BUTTON_A)
        assert handler._read_z_input(0, joy, CONTROLLER_BUTTON_A, is_lt=False) == 0.0

    def test_lt_sentinel_controller_path_analog(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)
        joy = FakeJoystick(num_axes=6)  # > 4 axes → analog path
        ctrl = FakeController()
        ctrl.set_axis(CONTROLLER_AXIS_TRIGGERLEFT, 16384)
        handler.joysticks[0] = joy
        handler.controllers[0] = ctrl
        assert handler._read_z_input(0, joy, BUTTON_ID_LT, is_lt=True) == pytest.approx(0.5, abs=1e-3)

    def test_lt_sentinel_controller_path_digital_fallback(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)
        joy = FakeJoystick(num_buttons=10, num_axes=4)  # <=4 axes → digital path
        ctrl = FakeController()
        handler.joysticks[0] = joy
        handler.controllers[0] = ctrl
        # LT_DIGITAL_BUTTON_INDICES = (6, 8)
        joy.press(6)
        assert handler._read_z_input(0, joy, BUTTON_ID_LT, is_lt=True) == 1.0
        joy.release(6)
        assert handler._read_z_input(0, joy, BUTTON_ID_LT, is_lt=True) == 0.0

    def test_rt_sentinel_joystick_path(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)
        joy = FakeJoystick(num_axes=8)
        handler.joysticks[0] = joy
        handler._shoulder_axis_baselines[0] = {5: 0.0, 7: 0.0}
        joy.set_axis(5, 0.6)
        assert handler._read_z_input(0, joy, BUTTON_ID_RT, is_lt=False) == pytest.approx(0.6)

    def test_swap_triggers_flips_lt_rt(self, stubbed_pygame) -> None:
        cfg = ControllerConfig(swap_triggers=True)
        handler, _ = make_handler(stubbed_pygame, config=cfg)
        joy = FakeJoystick(num_axes=6)
        ctrl = FakeController()
        ctrl.set_axis(CONTROLLER_AXIS_TRIGGERRIGHT, 32767)  # physical RT pulled
        handler.joysticks[0] = joy
        handler.controllers[0] = ctrl
        # With swap, asking for LT returns the physical RT value
        assert handler._read_z_input(0, joy, BUTTON_ID_LT, is_lt=True) == pytest.approx(1.0, abs=1e-3)


class TestReadTriggerHeight:
    def test_deadzone_suppresses_tiny_deflections(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)
        joy = FakeJoystick(num_axes=6)
        ctrl = FakeController()
        # Below TRIGGER_DEADZONE (0.05)
        ctrl.set_axis(CONTROLLER_AXIS_TRIGGERLEFT, 100)
        ctrl.set_axis(CONTROLLER_AXIS_TRIGGERRIGHT, 100)
        handler.joysticks[0] = joy
        handler.controllers[0] = ctrl
        assert handler._read_trigger_height(0, joy) == 0.0

    def test_up_minus_down_returned(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)
        joy = FakeJoystick(num_axes=6)
        ctrl = FakeController()
        ctrl.set_axis(CONTROLLER_AXIS_TRIGGERLEFT, 16384)  # z_down ~0.5
        ctrl.set_axis(CONTROLLER_AXIS_TRIGGERRIGHT, 32767)  # z_up ~1.0
        handler.joysticks[0] = joy
        handler.controllers[0] = ctrl
        assert handler._read_trigger_height(0, joy) == pytest.approx(0.5, abs=1e-3)


# --------------------------------------------------------------------------- #
# Button routing
# --------------------------------------------------------------------------- #


class TestGetButton:
    def test_unbound_sentinel_always_released(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)
        assert handler._get_button(0, BUTTON_ID_UNBOUND) is False
        # Legacy -1 toggle_zones sentinel also reads as released
        assert handler._get_button(0, -1) is False

    def test_lt_sentinel_past_deadzone_returns_true(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)
        joy = FakeJoystick(num_axes=6)
        ctrl = FakeController()
        ctrl.set_axis(CONTROLLER_AXIS_TRIGGERLEFT, 16384)
        handler.joysticks[0] = joy
        handler.controllers[0] = ctrl
        assert handler._get_button(0, BUTTON_ID_LT) is True

    def test_lt_sentinel_under_deadzone_returns_false(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)
        joy = FakeJoystick(num_axes=6)
        ctrl = FakeController()
        ctrl.set_axis(CONTROLLER_AXIS_TRIGGERLEFT, 0)
        handler.joysticks[0] = joy
        handler.controllers[0] = ctrl
        assert handler._get_button(0, BUTTON_ID_LT) is False

    def test_hat_sentinel_dispatches_to_hat(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)
        joy = FakeJoystick(num_hats=1)
        joy.set_hat((0, 1))
        handler.joysticks[0] = joy
        # Remap sends DPAD_UP → HAT_UP sentinel
        handler._button_remap[CONTROLLER_BUTTON_DPAD_UP] = HAT_UP
        assert handler._get_button(0, CONTROLLER_BUTTON_DPAD_UP) is True

    def test_hat_sentinel_without_joystick_false(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)
        handler._button_remap[CONTROLLER_BUTTON_DPAD_UP] = HAT_UP
        # No joystick registered
        assert handler._get_button(0, CONTROLLER_BUTTON_DPAD_UP) is False

    def test_hat_sentinel_via_sdl_controller_path(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)
        ctrl = FakeController()
        handler.controllers[0] = ctrl
        joy = FakeJoystick(num_hats=1)
        joy.set_hat((0, 1))
        handler.joysticks[0] = joy
        handler._button_remap[CONTROLLER_BUTTON_DPAD_UP] = HAT_UP
        assert handler._get_button(0, CONTROLLER_BUTTON_DPAD_UP) is True

    def test_hat_sentinel_via_sdl_controller_path_no_joystick(
        self,
        stubbed_pygame,
    ) -> None:
        handler, _ = make_handler(stubbed_pygame)
        ctrl = FakeController()
        handler.controllers[0] = ctrl
        # No joystick at idx 0
        handler._button_remap[CONTROLLER_BUTTON_DPAD_UP] = HAT_UP
        assert handler._get_button(0, CONTROLLER_BUTTON_DPAD_UP) is False

    def test_controller_backend_consumed_directly(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)
        ctrl = FakeController()
        ctrl.press(CONTROLLER_BUTTON_A)
        handler.controllers[0] = ctrl
        assert handler._get_button(0, CONTROLLER_BUTTON_A) is True

    def test_joystick_fallback_when_no_controller(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)
        joy = FakeJoystick(num_buttons=4)
        joy.press(2)
        handler.joysticks[0] = joy
        # Remap SDL2 CONTROLLER_BUTTON_X (default 2) to raw index 2
        assert handler._get_button(0, CONTROLLER_BUTTON_X) is True

    def test_remap_redirects_id(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)
        joy = FakeJoystick(num_buttons=8)
        handler.joysticks[0] = joy
        # Logical Y detected on raw idx 5
        handler._button_remap[CONTROLLER_BUTTON_Y] = 5
        joy.press(5)
        assert handler._get_button(0, CONTROLLER_BUTTON_Y) is True
        # Pressing the raw index the logical Y was mapped AWAY from does
        # not fire – the logical id now only reads through the remap.
        joy.release(5)
        joy.press(CONTROLLER_BUTTON_Y)
        assert handler._get_button(0, CONTROLLER_BUTTON_Y) is False


class TestDetectButtonEdge:
    def test_rising_edge_fires_once(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)
        joy = FakeJoystick()
        handler.joysticks[0] = joy
        joy.press(CONTROLLER_BUTTON_A)
        assert handler._detect_button_edge(0, CONTROLLER_BUTTON_A) is True
        # Still held → no rising edge
        assert handler._detect_button_edge(0, CONTROLLER_BUTTON_A) is False
        joy.release(CONTROLLER_BUTTON_A)
        assert handler._detect_button_edge(0, CONTROLLER_BUTTON_A) is False
        joy.press(CONTROLLER_BUTTON_A)
        assert handler._detect_button_edge(0, CONTROLLER_BUTTON_A) is True


# --------------------------------------------------------------------------- #
# Speed bumpers
# --------------------------------------------------------------------------- #


class TestSpeedFromBumpers:
    def test_rising_edge_adjusts_move_speed(self, stubbed_pygame) -> None:
        handler, app = make_handler(stubbed_pygame)
        joy = FakeJoystick(num_buttons=12)
        handler.joysticks[0] = joy
        joy.press(CONTROLLER_BUTTON_LEFTSHOULDER)
        handler._update_speed_from_bumpers(0, joy)
        assert app.move_speed_calls == [(-1, None)]
        # Same press again – no new edge
        handler._update_speed_from_bumpers(0, joy)
        assert app.move_speed_calls == [(-1, None)]

    def test_rising_edge_passes_resolved_marker_id(self, stubbed_pygame) -> None:
        """With a resolver wired, bumper handler routes speed adjust to
        that marker_id instead of the global default.
        """
        handler, app = make_handler(stubbed_pygame)
        handler._marker_resolver = lambda idx: 5
        joy = FakeJoystick(num_buttons=12)
        handler.joysticks[0] = joy
        joy.press(CONTROLLER_BUTTON_LEFTSHOULDER)
        handler._update_speed_from_bumpers(0, joy)
        assert app.move_speed_calls == [(-1, 5)]

    def test_resolver_returning_none_is_hard_noop(self, stubbed_pygame) -> None:
        handler, app = make_handler(stubbed_pygame)
        handler._marker_resolver = lambda idx: None
        joy = FakeJoystick(num_buttons=12)
        handler.joysticks[0] = joy
        joy.press(CONTROLLER_BUTTON_LEFTSHOULDER)
        handler._update_speed_from_bumpers(0, joy)
        assert app.move_speed_calls == []
        # Edge still latched so the press isn't replayed on the next frame
        assert handler._bumper_state[0] == (True, False)
        # Releasing then re-pressing after rebinding still no-ops while
        # unbound – resolver result, not edge state, gates the call.
        joy.release(CONTROLLER_BUTTON_LEFTSHOULDER)
        handler._update_speed_from_bumpers(0, joy)
        joy.press(CONTROLLER_BUTTON_LEFTSHOULDER)
        handler._update_speed_from_bumpers(0, joy)
        assert app.move_speed_calls == []

    def test_rb_rising_edge_adjusts_move_speed_up(self, stubbed_pygame) -> None:
        handler, app = make_handler(stubbed_pygame)
        joy = FakeJoystick(num_buttons=12)
        handler.joysticks[0] = joy
        joy.press(CONTROLLER_BUTTON_RIGHTSHOULDER)
        handler._update_speed_from_bumpers(0, joy)
        assert app.move_speed_calls == [(+1, None)]

    def test_raw_joystick_fallback_uses_alt_indices(self, stubbed_pygame) -> None:
        handler, app = make_handler(stubbed_pygame)
        joy = FakeJoystick(num_buttons=12)
        handler.joysticks[0] = joy
        # CONTROLLER_BUTTON_LEFTSHOULDER defaults to 9; raw LB sits on 4
        joy.press(BUMPER_LEFT_BUTTON_INDICES[0])
        handler._update_speed_from_bumpers(0, joy)
        # No remap set → raw fallback activates
        assert app.move_speed_calls == [(-1, None)]
        # And RB via raw index
        joy.press(BUMPER_RIGHT_BUTTON_INDICES[0])
        handler._update_speed_from_bumpers(0, joy)
        assert app.move_speed_calls[-1] == (+1, None)

    def test_fallback_skipped_when_remap_set(self, stubbed_pygame) -> None:
        handler, app = make_handler(stubbed_pygame)
        joy = FakeJoystick(num_buttons=12)
        handler.joysticks[0] = joy
        # Detection wizard explicitly mapped LB to a different index;
        # fallback must not trigger on the alt-indices.
        handler._button_remap[CONTROLLER_BUTTON_LEFTSHOULDER] = 9
        joy.press(BUMPER_LEFT_BUTTON_INDICES[0])  # raw 4, not the mapped one
        handler._update_speed_from_bumpers(0, joy)
        assert app.move_speed_calls == []

    def test_rb_fallback_skipped_when_remap_set(self, stubbed_pygame) -> None:
        from openfollow.input.gamepad import (
            BUMPER_RIGHT_BUTTON_INDICES,
            CONTROLLER_BUTTON_RIGHTSHOULDER,
        )

        handler, app = make_handler(stubbed_pygame)
        joy = FakeJoystick(num_buttons=12)
        handler.joysticks[0] = joy
        handler._button_remap[CONTROLLER_BUTTON_RIGHTSHOULDER] = 8
        joy.press(BUMPER_RIGHT_BUTTON_INDICES[0])
        handler._update_speed_from_bumpers(0, joy)
        assert app.move_speed_calls == []


# --------------------------------------------------------------------------- #
# Cleanup
# --------------------------------------------------------------------------- #


class TestCleanupFailed:
    def test_pops_every_tracked_structure(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)
        joy = FakeJoystick()
        ctrl = FakeController()
        handler.joysticks[0] = joy
        handler.controllers[0] = ctrl
        handler.capabilities[0] = ControllerCapabilities(backend="joystick")
        handler._bumper_state[0] = (True, False)
        handler._shoulder_axis_baselines[0] = {4: 0.0}
        handler._button_prev[0] = {1: True}

        handler._cleanup_failed([0, 999])  # 999 is unknown; must not raise

        # A pad that failed a read is released in SDL, not just forgotten.
        assert joy.quit_called is True
        assert ctrl.quit_called is True

        assert 0 not in handler.joysticks
        assert 0 not in handler.controllers
        assert 0 not in handler.capabilities
        assert 0 not in handler._bumper_state
        assert 0 not in handler._shoulder_axis_baselines
        assert 0 not in handler._button_prev


# --------------------------------------------------------------------------- #
# update() – disabled + hotplug + dispatch
# --------------------------------------------------------------------------- #


class TestUpdate:
    def test_disabled_returns_empty_update(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)
        handler.enabled = False
        result = handler.update(0.016)
        assert isinstance(result, GamepadUpdate)
        assert result.movements == {}
        assert result.resets == set()

    def test_stick_movements_are_scaled_by_move_speed(self, stubbed_pygame) -> None:
        handler, app = make_handler(stubbed_pygame)
        joy = FakeJoystick(num_buttons=12, num_axes=6)
        joy.set_axis(0, 0.9)  # left stick X strongly deflected
        handler.joysticks[0] = joy
        handler.capabilities[0] = ControllerCapabilities(backend="joystick")
        handler._bumper_state[0] = (False, False)
        handler._shoulder_axis_baselines[0] = {4: 0.0, 5: 0.0, 6: 0.0, 7: 0.0}
        result = handler.update(0.016)
        assert 0 in result.movements
        dx, dy, dz = result.movements[0]
        move_speed = app._config.marker.move_speed
        assert dx == pytest.approx(move_speed * handler._apply_deadzone(0.9))

    def test_diagonal_stick_input_is_normalized(self, stubbed_pygame) -> None:
        handler, app = make_handler(stubbed_pygame)
        joy = FakeJoystick(num_buttons=12, num_axes=6)
        joy.set_axis(0, 1.0)  # left stick X full deflection
        joy.set_axis(1, 1.0)  # left stick Y full deflection
        handler.joysticks[0] = joy
        handler.capabilities[0] = ControllerCapabilities(backend="joystick")
        handler._bumper_state[0] = (False, False)
        handler._shoulder_axis_baselines[0] = {4: 0.0, 5: 0.0, 6: 0.0, 7: 0.0}
        result = handler.update(0.016)
        assert 0 in result.movements
        dx, dy, dz = result.movements[0]
        move_speed = app._config.marker.move_speed
        # Both axes at 1.0 after deadzone → mag_xy = sqrt(2) > 1.0 → normalized to ~0.707 each
        expected = move_speed / math.sqrt(2)
        assert dx == pytest.approx(expected)
        assert dy == pytest.approx(-expected)
        assert dz == 0.0

    def test_reset_button_adds_controller_index_to_resets(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)
        joy = FakeJoystick(num_buttons=12)
        joy.press(CONTROLLER_BUTTON_X)  # default btn_reset = "X"
        handler.joysticks[0] = joy
        handler.capabilities[0] = ControllerCapabilities(backend="joystick")
        handler._bumper_state[0] = (False, False)
        handler._shoulder_axis_baselines[0] = {}
        result = handler.update(0.016)
        assert 0 in result.resets

    def test_dispatch_edges_for_configured_buttons(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)
        joy = FakeJoystick(num_buttons=16)
        joy.press(CONTROLLER_BUTTON_Y)  # toggle help
        joy.press(CONTROLLER_BUTTON_B)  # toggle zones (btn_toggle_zones default)
        joy.press(CONTROLLER_BUTTON_DPAD_RIGHT)  # next marker
        joy.press(CONTROLLER_BUTTON_DPAD_LEFT)  # prev marker
        joy.press(CONTROLLER_BUTTON_BACK)  # settings
        handler.joysticks[0] = joy
        handler.capabilities[0] = ControllerCapabilities(backend="joystick")
        handler._bumper_state[0] = (False, False)
        handler._shoulder_axis_baselines[0] = {}
        result = handler.update(0.016)
        assert result.toggle_help_pressed is True
        assert result.toggle_zones_pressed is True
        assert result.next_marker_pressed is True
        assert result.prev_marker_pressed is True
        assert result.settings_open_pressed is True

    def test_clear_messages_button_edge_fires_when_bound(self, stubbed_pygame) -> None:
        # btn_clear_messages bound → edge sets the flag.
        handler, _ = make_handler(stubbed_pygame, config=ControllerConfig(btn_clear_messages="START"))
        joy = FakeJoystick(num_buttons=16)
        joy.press(CONTROLLER_BUTTON_START)
        handler.joysticks[0] = joy
        handler.capabilities[0] = ControllerCapabilities(backend="joystick")
        handler._bumper_state[0] = (False, False)
        handler._shoulder_axis_baselines[0] = {}
        result = handler.update(0.016)
        assert result.clear_messages_pressed is True

    def test_clear_messages_unbound_does_not_fire(self, stubbed_pygame) -> None:
        # Default btn_clear_messages = "" → unbound → pressing START is a no-op.
        handler, _ = make_handler(stubbed_pygame)
        joy = FakeJoystick(num_buttons=16)
        joy.press(CONTROLLER_BUTTON_START)
        handler.joysticks[0] = joy
        handler.capabilities[0] = ControllerCapabilities(backend="joystick")
        handler._bumper_state[0] = (False, False)
        handler._shoulder_axis_baselines[0] = {}
        result = handler.update(0.016)
        assert result.clear_messages_pressed is False

    def test_clear_messages_trigger_binding_fires(self, stubbed_pygame) -> None:
        # Bound to a trigger (LT) → deflection past the deadzone fires the edge.
        # The negative LT id used to be blocked by a >= 0 dispatch guard.
        handler, _ = make_handler(stubbed_pygame, config=ControllerConfig(btn_clear_messages="LT"))
        joy = FakeJoystick(num_buttons=16, num_axes=8)
        joy.set_axis(4, 0.9)  # LT raw axis, past TRIGGER_DEADZONE
        handler.joysticks[0] = joy
        handler.capabilities[0] = ControllerCapabilities(backend="joystick")
        handler._bumper_state[0] = (False, False)
        handler._shoulder_axis_baselines[0] = {4: 0.0, 6: 0.0}  # seed so the delta reads as a press
        result = handler.update(0.016)
        assert result.clear_messages_pressed is True

    def test_pygame_error_during_read_triggers_cleanup(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)

        class ExplodingJoystick(FakeJoystick):
            def get_axis(self, idx: int) -> float:  # type: ignore[override]
                raise pygame.error("disconnected")

        joy = ExplodingJoystick(num_buttons=12, num_axes=6)
        handler.joysticks[0] = joy
        handler.capabilities[0] = ControllerCapabilities(backend="joystick")
        handler._bumper_state[0] = (False, False)
        handler._shoulder_axis_baselines[0] = {}
        handler._axes_logged.add(0)  # skip the dump branch so the raise goes through _read_axes
        result = handler.update(0.016)
        assert result.movements == {}
        # Erroring controller was cleaned up
        assert 0 not in handler.joysticks


# --------------------------------------------------------------------------- #
# Multi-gamepad DPAD next/prev gate + per-marker effective-speed resolver
# --------------------------------------------------------------------------- #


class TestMultiGamepadDpadGate:
    """With ≥2 controllers connected, DPAD next/prev presses from any pad
    must NOT propagate as a ``GamepadUpdate.next_marker_pressed`` /
    ``prev_marker_pressed`` flag – they'd otherwise rotate the shared
    ``app._selected_id`` and surprise other operators. Edge state is still
    advanced for ``_button_prev`` so a later single-pad disconnect leaves
    no stuck "pressed" carryover.
    """

    def test_multi_gamepad_dpad_does_not_set_next_prev_flags(
        self,
        stubbed_pygame,
    ) -> None:
        handler, _ = make_handler(stubbed_pygame)
        joy_a = FakeJoystick(num_buttons=16)
        joy_b = FakeJoystick(num_buttons=16)
        joy_a.press(CONTROLLER_BUTTON_DPAD_RIGHT)
        joy_a.press(CONTROLLER_BUTTON_DPAD_LEFT)
        handler.joysticks[0] = joy_a
        handler.joysticks[1] = joy_b
        for idx in (0, 1):
            handler.capabilities[idx] = ControllerCapabilities(backend="joystick")
            handler._bumper_state[idx] = (False, False)
            handler._shoulder_axis_baselines[idx] = {}

        result = handler.update(0.016)
        assert result.next_marker_pressed is False
        assert result.prev_marker_pressed is False

    def test_multi_gamepad_dpad_still_advances_button_prev(
        self,
        stubbed_pygame,
    ) -> None:
        """Edge state is recorded even when the flag is suppressed so a
        subsequent release+press cycle is still edge-detected once the
        operator drops back to single-pad mode."""
        handler, _ = make_handler(stubbed_pygame)
        joy_a = FakeJoystick(num_buttons=16)
        joy_b = FakeJoystick(num_buttons=16)
        joy_a.press(CONTROLLER_BUTTON_DPAD_RIGHT)
        handler.joysticks[0] = joy_a
        handler.joysticks[1] = joy_b
        for idx in (0, 1):
            handler.capabilities[idx] = ControllerCapabilities(backend="joystick")
            handler._bumper_state[idx] = (False, False)
            handler._shoulder_axis_baselines[idx] = {}

        handler.update(0.016)
        # ``_btn_next_marker_id`` defaults to CONTROLLER_BUTTON_DPAD_RIGHT;
        # whichever id resolution lands on, the edge was tracked.
        assert handler._button_prev[0][handler._btn_next_marker_id] is True

    def test_single_gamepad_dpad_still_sets_flag(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)
        joy = FakeJoystick(num_buttons=16)
        joy.press(CONTROLLER_BUTTON_DPAD_RIGHT)
        handler.joysticks[0] = joy
        handler.capabilities[0] = ControllerCapabilities(backend="joystick")
        handler._bumper_state[0] = (False, False)
        handler._shoulder_axis_baselines[0] = {}

        result = handler.update(0.016)
        assert result.next_marker_pressed is True


class TestGetEffectiveSpeedResolver:
    """``get_effective_speed`` reads the controller's routed marker via
    the injected ``marker_resolver`` and looks up that marker's per-marker
    override. Without a resolver, falls back to the global default.
    """

    def test_uses_resolver_to_pick_per_marker_value(
        self,
        stubbed_pygame,
    ) -> None:
        handler, app = make_handler(stubbed_pygame)
        handler._marker_resolver = lambda idx: 3
        app._config.marker_move_speeds = {3: 4.2}
        assert handler.get_effective_speed(0) == pytest.approx(4.2)

    def test_falls_back_to_marker_move_speed_when_no_override(
        self,
        stubbed_pygame,
    ) -> None:
        handler, app = make_handler(stubbed_pygame)
        handler._marker_resolver = lambda idx: 3
        # No entry for marker 3 in the dict → falls back to the global default.
        assert handler.get_effective_speed(0) == pytest.approx(
            app._config.marker.move_speed,
        )

    def test_no_resolver_uses_global_default(self, stubbed_pygame) -> None:
        handler, app = make_handler(stubbed_pygame)
        # Default-constructed handler has ``_marker_resolver=None``.
        assert handler.get_effective_speed(0) == pytest.approx(
            app._config.marker.move_speed,
        )


# --------------------------------------------------------------------------- #
# Mode-specific input readers
# --------------------------------------------------------------------------- #


class TestSourceSelectionInput:
    def test_empty_when_no_joysticks(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)
        inp = handler.read_source_selection_input()
        assert isinstance(inp, SourceSelectionInput)
        assert inp.confirm_pressed is False

    def test_dpad_and_confirm_edges(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)
        joy = FakeJoystick(num_buttons=16)
        joy.press(CONTROLLER_BUTTON_DPAD_UP)
        joy.press(CONTROLLER_BUTTON_DPAD_DOWN)
        joy.press(CONTROLLER_BUTTON_A)
        joy.press(CONTROLLER_BUTTON_B)
        handler.joysticks[0] = joy
        inp = handler.read_source_selection_input()
        assert inp.up_pressed is True
        assert inp.down_pressed is True
        assert inp.confirm_pressed is True
        assert inp.cancel_pressed is True


class TestSettingsMenuInput:
    def test_empty_when_no_joysticks(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)
        inp = handler.read_settings_menu_input()
        assert isinstance(inp, SettingsMenuInput)
        assert inp.up_pressed is False

    def test_dpad_and_confirm(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)
        joy = FakeJoystick(num_buttons=16)
        joy.press(CONTROLLER_BUTTON_DPAD_UP)
        joy.press(CONTROLLER_BUTTON_A)
        handler.joysticks[0] = joy
        inp = handler.read_settings_menu_input()
        assert inp.up_pressed is True
        assert inp.confirm_pressed is True

    def test_sync_refreshes_normal_mode_prev_state(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)
        joy = FakeJoystick(num_buttons=16)
        joy.press(CONTROLLER_BUTTON_X)  # held through the menu
        handler.joysticks[0] = joy
        handler.read_settings_menu_input()
        # First post-menu edge check should NOT see a rising edge
        assert handler._detect_button_edge(0, CONTROLLER_BUTTON_X) is False


# --------------------------------------------------------------------------- #
# Speed accessors and info
# --------------------------------------------------------------------------- #


class TestSpeedAccessors:
    def test_effective_speed_comes_from_config(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)
        handler.joysticks[0] = FakeJoystick()
        assert handler.get_effective_speed(0) == pytest.approx(4.0)  # from FakeApp

    def test_controller_effective_speeds_maps_all_attached(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)
        handler.joysticks[0] = FakeJoystick()
        handler.joysticks[2] = FakeJoystick()
        speeds = handler.get_controller_effective_speeds()
        assert set(speeds.keys()) == {0, 2}
        assert all(v == pytest.approx(4.0) for v in speeds.values())


class TestControllerInfo:
    def test_shape_includes_all_required_keys(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)
        joy = FakeJoystick(name="TestPad")
        handler.joysticks[3] = joy
        handler.capabilities[3] = ControllerCapabilities(backend="sdl2_controller")
        info = handler.get_controller_info()
        assert len(info) == 1
        entry = info[0]
        assert entry["controller_index"] == 3
        assert entry["name"] == "TestPad"
        assert entry["connected"] is True
        assert entry["marker_id"] is None
        assert entry["backend"] == "sdl2_controller"
        assert entry["effective_speed"] == pytest.approx(4.0)

    def test_dead_joystick_name_falls_back_to_unknown(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)

        class DeadJoystick(FakeJoystick):
            def get_name(self) -> str:  # type: ignore[override]
                raise pygame.error("dead")

        handler.joysticks[1] = DeadJoystick()
        info = handler.get_controller_info()
        assert info[0]["name"] == "Unknown"
        assert info[0]["connected"] is False


# --------------------------------------------------------------------------- #
# apply_config
# --------------------------------------------------------------------------- #


class TestApplyConfig:
    def test_empty_binding_string_maps_to_unbound(self, stubbed_pygame) -> None:
        cfg = ControllerConfig(btn_toggle_zones="")
        handler, _ = make_handler(stubbed_pygame, config=cfg)
        assert handler._btn_toggle_zones_id == BUTTON_ID_UNBOUND

    def test_raw_indices_seed_remap(self, stubbed_pygame) -> None:
        cfg = ControllerConfig(button_raw_indices={"A": 2, "B": 3})
        handler, _ = make_handler(stubbed_pygame, config=cfg)
        # A (SDL2 id 0) → raw idx 2; B (id 1) → raw idx 3
        assert handler._raw_button_remap[CONTROLLER_BUTTON_A] == 2
        assert handler._raw_button_remap[CONTROLLER_BUTTON_B] == 3
        # SDL2 path remap stays clean – raw indices must not leak here.
        assert CONTROLLER_BUTTON_A not in handler._button_remap
        assert CONTROLLER_BUTTON_B not in handler._button_remap

    def test_map_field_remap_for_swapped_a_b(self, stubbed_pygame) -> None:
        # If physical A reports as B, the remap should redirect logical A
        # to the B id.
        cfg = ControllerConfig(map_a="B")
        handler, _ = make_handler(stubbed_pygame, config=cfg)
        assert handler._button_remap.get(CONTROLLER_BUTTON_A) == CONTROLLER_BUTTON_B

    def test_map_field_no_change_skips_remap_entry(self, stubbed_pygame) -> None:
        cfg = ControllerConfig()  # defaults: each map_* matches its logical id
        handler, _ = make_handler(stubbed_pygame, config=cfg)
        assert handler._button_remap == {}

    def test_apply_config_clears_button_prev(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)
        handler._button_prev[0] = {1: True}
        handler.apply_config()
        assert handler._button_prev == {}


# --------------------------------------------------------------------------- #
# stop()
# --------------------------------------------------------------------------- #


class TestStop:
    def test_stop_clears_all_state_and_disables(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)
        handler.joysticks[0] = FakeJoystick()
        handler.controllers[0] = FakeController()
        handler.capabilities[0] = ControllerCapabilities(backend="joystick")
        handler.stop()
        assert handler.enabled is False
        assert handler.joysticks == {}
        assert handler.controllers == {}
        assert handler.capabilities == {}


# --------------------------------------------------------------------------- #
# pump_events + hotplug
# --------------------------------------------------------------------------- #


class TestPumpEvents:
    def test_non_joystick_events_are_reposted(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)
        # pygame.USEREVENT is not in the joystick/controller set – must be reposted
        evt = pygame.event.Event(pygame.USEREVENT, {})
        stubbed_pygame["events"].append(evt)
        handler._pump_events()
        assert evt in stubbed_pygame["posted"]

    def test_joystick_events_are_consumed(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)
        evt = pygame.event.Event(pygame.JOYAXISMOTION, {})
        stubbed_pygame["events"].append(evt)
        handler._pump_events()
        assert evt not in stubbed_pygame["posted"]


class TestHotplugTouchesOnlyTheChangedDevice:
    """A device event opens or closes the one device it names, never the rest."""

    @staticmethod
    def _attach(
        stubbed_pygame,
        *joys: FakeJoystick,
        opened: list[int] | None = None,
        lookup: bool = True,
    ) -> None:
        """Make ``joys`` the attached devices, in SDL device-index order.

        ``lookup=False`` makes SDL's pre-open instance-id lookup unavailable.
        """

        def _factory(idx: int, joy: FakeJoystick):
            if opened is not None:
                opened.append(idx)
            return joy

        stubbed_pygame["state"]["count"] = len(joys)
        stubbed_pygame["state"]["lookup"] = lookup
        stubbed_pygame["state"]["instance_ids"] = (
            {i: joy.get_instance_id() for i, joy in enumerate(joys)} if lookup else {}
        )
        stubbed_pygame["state"]["factories"] = {
            i: (lambda idx, joy=joy: _factory(idx, joy)) for i, joy in enumerate(joys)
        }

    @staticmethod
    def _deliver(stubbed_pygame, *events: tuple[int, dict[str, int]]) -> None:
        stubbed_pygame["events"][:] = [pygame.event.Event(kind, fields) for kind, fields in events]

    def test_connect_log_names_the_sdl_instance_not_a_slot(self, stubbed_pygame, caplog) -> None:
        # A replugged pad comes back under a fresh, higher instance id, so the
        # number stops matching its slot or the C-badge; the log must say which.
        first = FakeJoystick(instance_id=0)
        self._attach(stubbed_pygame, first)
        handler, _ = make_handler(stubbed_pygame)

        self._attach(stubbed_pygame, first, FakeJoystick(instance_id=7))
        self._deliver(stubbed_pygame, (pygame.JOYDEVICEADDED, {"device_index": 1}))
        with caplog.at_level(logging.INFO, logger="openfollow.input.gamepad"):
            handler._pump_events()

        assert "Gamepad instance 7 connected via" in caplog.text

    def test_startup_announcement_reopens_nothing(self, stubbed_pygame) -> None:
        opened: list[int] = []
        pad = FakeJoystick(instance_id=0)
        self._attach(stubbed_pygame, pad, opened=opened)
        handler, _ = make_handler(stubbed_pygame)

        # SDL announces every device already attached when its joystick
        # subsystem came up, including the one just opened.
        self._deliver(stubbed_pygame, (pygame.JOYDEVICEADDED, {"device_index": 0}))
        handler._pump_events()

        assert opened == [0]
        assert pad.quit_called is False
        assert handler.joysticks == {0: pad}

    def test_unplugging_one_pad_leaves_the_other_open(self, stubbed_pygame) -> None:
        pad_a = FakeJoystick(instance_id=0)
        pad_b = FakeJoystick(instance_id=1)
        self._attach(stubbed_pygame, pad_a, pad_b)
        handler, _ = make_handler(stubbed_pygame)

        self._attach(stubbed_pygame, pad_b)
        self._deliver(stubbed_pygame, (pygame.JOYDEVICEREMOVED, {"instance_id": 0}))
        handler._pump_events()

        assert pad_a.quit_called is True
        assert pad_b.quit_called is False
        assert handler.joysticks == {1: pad_b}
        assert set(handler.capabilities) == {1}

    def test_unplugged_pad_that_fails_to_close_is_still_dropped(self, stubbed_pygame) -> None:
        class _VanishedPad(FakeJoystick):
            def quit(self) -> None:
                raise pygame.error("device already gone")

        pad = _VanishedPad(instance_id=0)
        self._attach(stubbed_pygame, pad)
        handler, _ = make_handler(stubbed_pygame)

        self._attach(stubbed_pygame)
        self._deliver(stubbed_pygame, (pygame.JOYDEVICEREMOVED, {"instance_id": 0}))
        handler._pump_events()

        assert handler.joysticks == {}
        assert handler.capabilities == {}

    def test_held_button_does_not_fire_again_when_another_pad_is_unplugged(self, stubbed_pygame) -> None:
        pad_a = FakeJoystick(instance_id=0)
        pad_b = FakeJoystick(instance_id=1)
        self._attach(stubbed_pygame, pad_a, pad_b)
        handler, _ = make_handler(stubbed_pygame)
        pad_b.press(CONTROLLER_BUTTON_X)  # default btn_reset = "X"
        assert handler.update(0.016).resets == {1}
        assert handler.update(0.016).resets == set()

        # pad_a goes; SDL renumbers pad_b to device index 0.
        self._attach(stubbed_pygame, pad_b)
        self._deliver(stubbed_pygame, (pygame.JOYDEVICEREMOVED, {"instance_id": 0}))
        result = handler.update(0.016)

        # Still the same held press on pad_b: no second reset.
        assert result.resets == set()

    def test_plugging_in_opens_only_the_new_pad(self, stubbed_pygame) -> None:
        pad_a = FakeJoystick(instance_id=0)
        self._attach(stubbed_pygame, pad_a)
        handler, _ = make_handler(stubbed_pygame)

        pad_b = FakeJoystick(instance_id=7)
        self._attach(stubbed_pygame, pad_a, pad_b)
        self._deliver(stubbed_pygame, (pygame.JOYDEVICEADDED, {"device_index": 1}))
        handler._pump_events()

        assert pad_a.quit_called is False
        assert handler.joysticks == {0: pad_a, 7: pad_b}

    def test_replug_within_one_frame_swaps_just_that_device(self, stubbed_pygame) -> None:
        pad = FakeJoystick(instance_id=0)
        self._attach(stubbed_pygame, pad)
        handler, _ = make_handler(stubbed_pygame)

        replugged = FakeJoystick(instance_id=3)
        self._attach(stubbed_pygame, replugged)
        self._deliver(
            stubbed_pygame,
            (pygame.JOYDEVICEREMOVED, {"instance_id": 0}),
            (pygame.JOYDEVICEADDED, {"device_index": 0}),
        )
        handler._pump_events()

        assert pad.quit_called is True
        assert handler.joysticks == {3: replugged}

    def test_game_controller_hotplug_events_are_consumed_not_acted_on(self, stubbed_pygame) -> None:
        opened: list[int] = []
        pad = FakeJoystick(instance_id=0)
        self._attach(stubbed_pygame, pad, opened=opened)
        handler, _ = make_handler(stubbed_pygame)

        self._deliver(
            stubbed_pygame,
            (pygame.CONTROLLERDEVICEADDED, {"device_index": 0}),
            (pygame.CONTROLLERDEVICEREMOVED, {"instance_id": 0}),
        )
        handler._pump_events()

        assert opened == [0]
        assert pad.quit_called is False
        assert stubbed_pygame["posted"] == []

    def test_removal_of_a_device_that_is_not_open_changes_nothing(self, stubbed_pygame) -> None:
        pad = FakeJoystick(instance_id=0)
        self._attach(stubbed_pygame, pad)
        handler, _ = make_handler(stubbed_pygame)

        self._deliver(stubbed_pygame, (pygame.JOYDEVICEREMOVED, {"instance_id": 42}))
        handler._pump_events()

        assert pad.quit_called is False
        assert handler.joysticks == {0: pad}

    @pytest.mark.parametrize("stale_index", [0, 5], ids=["already-open", "out-of-range"])
    def test_add_with_a_stale_index_still_opens_the_new_pad(self, stubbed_pygame, stale_index) -> None:
        opened: list[int] = []
        pad_a = FakeJoystick(instance_id=0)
        self._attach(stubbed_pygame, pad_a, opened=opened)
        handler, _ = make_handler(stubbed_pygame)

        # The event names an index that does not lead to the new pad; the
        # handler matches SDL's current list, not the event.
        pad_b = FakeJoystick(instance_id=1)
        self._attach(stubbed_pygame, pad_a, pad_b, opened=opened)
        self._deliver(stubbed_pygame, (pygame.JOYDEVICEADDED, {"device_index": stale_index}))
        handler._pump_events()

        assert opened == [0, 1]
        assert pad_a.quit_called is False
        assert handler.joysticks == {0: pad_a, 1: pad_b}

    def test_a_burst_of_adds_opens_each_new_pad_once(self, stubbed_pygame) -> None:
        opened: list[int] = []
        pad_a = FakeJoystick(instance_id=0)
        self._attach(stubbed_pygame, pad_a, opened=opened)
        handler, _ = make_handler(stubbed_pygame)

        pad_b = FakeJoystick(instance_id=1)
        self._attach(stubbed_pygame, pad_a, pad_b, opened=opened)
        self._deliver(
            stubbed_pygame,
            (pygame.JOYDEVICEADDED, {"device_index": 1}),
            (pygame.JOYDEVICEADDED, {"device_index": 0}),
        )
        handler._pump_events()

        assert opened == [0, 1]
        assert handler.joysticks == {0: pad_a, 1: pad_b}

    @pytest.mark.parametrize(
        ("lookup", "expected_opens", "reopened"),
        [(True, [0, 1], False), (False, [0, 0, 1], True)],
        ids=["matched-by-instance-id", "lookup-unavailable-rescans"],
    )
    def test_add_naming_an_open_pad_leaves_its_state_alone(
        self, stubbed_pygame, lookup, expected_opens, reopened
    ) -> None:
        opened: list[int] = []
        pad_a = FakeJoystick(instance_id=0)
        self._attach(stubbed_pygame, pad_a, opened=opened, lookup=lookup)
        handler, _ = make_handler(stubbed_pygame)
        pad_a.press(CONTROLLER_BUTTON_X)
        assert handler.update(0.016).resets == {0}

        # A new pad arrives in the same frame as an add that names pad_a's
        # index. With the lookup, pad_a is matched and left alone; without it,
        # the rescan reopens pad_a but carries its state by instance id.
        pad_b = FakeJoystick(instance_id=1)
        self._attach(stubbed_pygame, pad_a, pad_b, opened=opened, lookup=lookup)
        self._deliver(
            stubbed_pygame,
            (pygame.JOYDEVICEADDED, {"device_index": 0}),
            (pygame.JOYDEVICEADDED, {"device_index": 1}),
        )
        result = handler.update(0.016)

        assert opened == expected_opens
        assert pad_a.quit_called is reopened
        assert handler.joysticks == {0: pad_a, 1: pad_b}
        assert result.resets == set()

    def test_failed_open_releases_what_it_opened(self, stubbed_pygame, monkeypatch) -> None:
        class _UnreadablePad(FakeJoystick):
            def get_axis(self, idx: int) -> float:
                raise pygame.error("axis read failed")

        class _Sdl2:
            @staticmethod
            def get_init() -> bool:
                return True

            @staticmethod
            def is_controller(_idx: int) -> bool:
                return True

            @staticmethod
            def Controller(_idx: int) -> FakeController:  # noqa: N802 - mirrors pygame's API
                return ctrl

        class _GoneController(FakeController):
            def quit(self) -> None:
                super().quit()
                raise pygame.error("controller already gone")

        ctrl = _GoneController()
        pad = _UnreadablePad(instance_id=4)
        monkeypatch.setattr(gp, "sdl2_controller", _Sdl2)
        self._attach(stubbed_pygame, pad)
        handler, _ = make_handler(stubbed_pygame)

        assert handler.joysticks == {}
        assert pad.quit_called is True
        assert ctrl.quit_called is True

    def test_failed_open_never_closes_a_registered_pad(self, stubbed_pygame) -> None:
        class _FlakyIdPad(FakeJoystick):
            """Reports its id once, as a registered pad does, then fails."""

            def __init__(self) -> None:
                super().__init__(instance_id=0)
                self.id_reads = 0

            def get_instance_id(self) -> int:
                self.id_reads += 1
                if self.id_reads > 1:
                    raise pygame.error("device busy")
                return 0

        pad = _FlakyIdPad()
        self._attach(stubbed_pygame, pad, lookup=False)
        handler, _ = make_handler(stubbed_pygame)

        # A stale lookup lists a new id at an index whose object pygame hands
        # back is the registered pad, and that object then fails its id read.
        state = stubbed_pygame["state"]
        state["lookup"] = True
        state["count"] = 2
        state["instance_ids"] = {0: 0, 1: 9}
        state["factories"] = {0: lambda idx: pad, 1: lambda idx: pad}
        self._deliver(stubbed_pygame, (pygame.JOYDEVICEADDED, {"device_index": 1}))
        handler._pump_events()

        assert pad.quit_called is False
        assert handler.joysticks == {0: pad}

    def test_stale_lookup_never_registers_a_pad_twice(self, stubbed_pygame) -> None:
        pad = FakeJoystick(instance_id=0)
        self._attach(stubbed_pygame, pad)
        handler, _ = make_handler(stubbed_pygame)

        # The lookup lists a new id at an index whose object pygame hands back
        # is the pad already registered.
        state = stubbed_pygame["state"]
        state["count"] = 2
        state["instance_ids"] = {0: 0, 1: 9}
        state["factories"] = {0: lambda idx: pad, 1: lambda idx: pad}
        self._deliver(stubbed_pygame, (pygame.JOYDEVICEADDED, {"device_index": 1}))
        handler._pump_events()

        assert pad.quit_called is False
        assert handler.joysticks == {0: pad}

    def test_a_pad_that_never_opens_does_not_churn_the_others(self, stubbed_pygame) -> None:
        def _open_fails(_idx: int):
            raise pygame.error("cannot open")

        pad_a = FakeJoystick(instance_id=0)
        dead = FakeJoystick(instance_id=5)
        self._attach(stubbed_pygame, pad_a, dead)
        stubbed_pygame["state"]["factories"][1] = _open_fails
        handler, _ = make_handler(stubbed_pygame)
        assert handler.joysticks == {0: pad_a}

        # SDL keeps counting the dead device; another pad's arrival must not
        # close and reopen the pad that works.
        pad_c = FakeJoystick(instance_id=6)
        self._attach(stubbed_pygame, pad_a, dead, pad_c)
        stubbed_pygame["state"]["factories"][1] = _open_fails
        self._deliver(stubbed_pygame, (pygame.JOYDEVICEADDED, {"device_index": 2}))
        handler._pump_events()

        assert pad_a.quit_called is False
        assert handler.joysticks == {0: pad_a, 6: pad_c}

    def test_rescan_never_shows_other_threads_a_partial_set(self, stubbed_pygame) -> None:
        pad_a = FakeJoystick(instance_id=0)
        pad_b = FakeJoystick(instance_id=1)
        self._attach(stubbed_pygame, pad_a, pad_b, lookup=False)
        handler, _ = make_handler(stubbed_pygame)
        seen_mid_rescan: list[tuple[set[int], set[int]]] = []

        def _open_b(idx: int) -> FakeJoystick:
            # What the web and OSC threads would read while the rescan runs.
            seen_mid_rescan.append((set(handler.joysticks), set(handler.capabilities)))
            return pad_b

        stubbed_pygame["state"]["factories"][1] = _open_b
        handler._detect_controllers(force=True)

        assert seen_mid_rescan == [({0, 1}, {0, 1})]
        assert set(handler.joysticks) == set(handler.capabilities) == {0, 1}

    def test_hotplug_error_is_logged_not_raised(self, stubbed_pygame, monkeypatch, caplog) -> None:
        pad = FakeJoystick(instance_id=0)
        self._attach(stubbed_pygame, pad)
        handler, _ = make_handler(stubbed_pygame)

        def _boom() -> int:
            raise pygame.error("device list unavailable")

        monkeypatch.setattr(pygame.joystick, "get_count", _boom)
        self._deliver(stubbed_pygame, (pygame.JOYDEVICEADDED, {"device_index": 0}))
        with caplog.at_level(logging.ERROR, logger="openfollow.input.gamepad"):
            handler._pump_events()

        assert "device list unavailable" in caplog.text
        assert handler.joysticks == {0: pad}


def _sdl_vidpid_pairs(hint: str) -> set[tuple[int, int]]:
    """Read a VID/PID list the way SDL does: consecutive ``0x`` numbers, in pairs."""
    numbers = [int(token, 16) for token in re.findall(r"0x([0-9a-fA-F]+)", hint)]
    return set(zip(numbers[0::2], numbers[1::2], strict=False))


class TestSpacemouseBlacklist:
    """The SDL blacklist that keeps 3Dconnexion pucks from binding as gamepads."""

    # The profiles pyspacemouse 2.1.0 ships: every puck the 3D Mouse subsystem
    # can drive must also be kept away from SDL.
    DRIVABLE = {
        (0x046D, 0xC625), (0x046D, 0xC626), (0x046D, 0xC627), (0x046D, 0xC629), (0x046D, 0xC62B),
        (0x256F, 0xC62E), (0x256F, 0xC632), (0x256F, 0xC633), (0x256F, 0xC635), (0x256F, 0xC638),
        (0x256F, 0xC63A), (0x256F, 0xC641), (0x256F, 0xC652),
    }  # fmt: skip
    # 3Dconnexion products that are not 6DOF pucks (CadMouse, Keyboard Pro,
    # Numpad Pro). Blocking them would do nothing, so they stay off the list.
    NOT_PUCKS = {
        (0x256F, 0xC650), (0x256F, 0xC651), (0x256F, 0xC654), (0x256F, 0xC655), (0x256F, 0xC656),
        (0x256F, 0xC657), (0x256F, 0xC664), (0x256F, 0xC665), (0x256F, 0xC668),
    }  # fmt: skip

    def test_every_drivable_puck_is_blocked(self) -> None:
        assert _sdl_vidpid_pairs(gp._spacemouse_blacklist()) >= self.DRIVABLE

    def test_logitech_entries_stay_inside_the_puck_range(self) -> None:
        # Logitech's own gamepads share vendor 0x046d; only its 3Dconnexion
        # range may be blocked.
        for vid, pid in _sdl_vidpid_pairs(gp._spacemouse_blacklist()):
            assert vid in (0x046D, 0x256F)
            if vid == 0x046D:
                assert 0xC600 <= pid <= 0xC6FF

    def test_non_puck_products_are_not_listed(self) -> None:
        assert not self.NOT_PUCKS & _sdl_vidpid_pairs(gp._spacemouse_blacklist())

    def test_every_entry_is_a_pair_sdl_can_read(self) -> None:
        for entry in gp._spacemouse_blacklist().split(","):
            assert re.fullmatch(r"0x[0-9a-f]{4}/0x[0-9a-f]{4}", entry)


class TestSpacemouseFallback:
    """Pucks stay off the gamepad path even where SDL ignores its blacklist."""

    @pytest.fixture(autouse=True)
    def _fresh_lookup(self):
        lookup = gp._sdl_device_fn  # a test may swap it; clear the real cache
        lookup.cache_clear()
        yield
        lookup.cache_clear()

    def test_listed_puck_is_never_opened(self, stubbed_pygame) -> None:
        opened: list[int] = []
        pad = FakeJoystick(instance_id=0)
        puck = FakeJoystick(instance_id=1, name="SpaceNavigator")
        stubbed_pygame["state"].update(
            count=2,
            lookup=True,
            instance_ids={0: 0, 1: 1},
            pucks={1},
            factories={i: (lambda idx, j=j: (opened.append(idx), j)[1]) for i, j in enumerate((pad, puck))},
        )
        handler, _ = make_handler(stubbed_pygame)

        # A later hotplug must not open it either.
        stubbed_pygame["events"][:] = [pygame.event.Event(pygame.JOYDEVICEADDED, {"device_index": 1})]
        handler._pump_events()

        assert opened == [0]
        assert handler.joysticks == {0: pad}

    def test_reads_vendor_and_product_through_pygames_sdl(self) -> None:
        # The fallback only holds if both queries resolve in the loaded SDL.
        assert gp._sdl_device_fn("SDL_JoystickGetDeviceVendor", ctypes.c_uint16) is not None
        assert gp._sdl_device_fn("SDL_JoystickGetDeviceProduct", ctypes.c_uint16) is not None

    def test_index_with_no_device_is_not_a_puck(self) -> None:
        # Real call into the SDL pygame loaded; SDL reports 0/0 for no device.
        assert gp._is_spacemouse(10_000) is False

    @pytest.mark.parametrize(
        ("ids", "expected"),
        [((0x046D, 0xC626), True), ((0x045E, 0x028E), False)],
        ids=["spacenavigator", "xbox-360-pad"],
    )
    def test_matches_the_puck_list(self, monkeypatch, ids, expected) -> None:
        vendor, product = ids
        answers = {"SDL_JoystickGetDeviceVendor": vendor, "SDL_JoystickGetDeviceProduct": product}
        monkeypatch.setattr(gp, "_sdl_device_fn", lambda symbol, _restype: lambda _idx: answers[symbol])
        assert gp._is_spacemouse(0) is expected

    def test_unreachable_sdl_treats_nothing_as_a_puck(self, monkeypatch) -> None:
        monkeypatch.setattr(gp, "_sdl_device_fn", lambda _symbol, _restype: None)
        assert gp._is_spacemouse(0) is False


class TestDeviceInstanceIdLookup:
    """SDL's instance id for a device index, read without opening the device."""

    @pytest.fixture(autouse=True)
    def _fresh_lookup(self):
        lookup = gp._sdl_device_fn  # a test may swap it; clear the real cache
        lookup.cache_clear()
        yield
        lookup.cache_clear()

    def test_lookup_resolves_in_the_sdl_pygame_loaded(self) -> None:
        # The contract the whole hotplug path rests on: if packaging ever makes
        # the symbol unreachable, this fails instead of hotplug quietly falling
        # back to a full rescan.
        assert gp._sdl_device_instance_id_fn() is not None
        assert gp._instance_id_lookup_available() is True

    def test_lookup_unavailable_is_reported(self, monkeypatch) -> None:
        monkeypatch.setattr(gp, "_sdl_device_instance_id_fn", lambda: None)
        assert gp._instance_id_lookup_available() is False
        assert gp._device_instance_id(0) is None

    def test_index_with_no_device_reads_as_unknown(self) -> None:
        # Real call into the SDL pygame loaded; no joystick has this index.
        assert gp._device_instance_id(10_000) is None

    def test_reports_the_id_sdl_assigned(self, monkeypatch) -> None:
        monkeypatch.setattr(gp, "_sdl_device_instance_id_fn", lambda: lambda idx: idx + 40)
        assert gp._device_instance_id(2) == 42

    @pytest.mark.parametrize("failure", [OSError("no such image"), AttributeError("no such symbol")])
    def test_unreachable_sdl_reads_as_unknown(self, monkeypatch, failure) -> None:
        def _cdll(_path: str):
            raise failure

        monkeypatch.setattr(gp.ctypes, "CDLL", _cdll)
        assert gp._device_instance_id(0) is None

    def test_joystick_module_without_a_file_reads_as_unknown(self, monkeypatch) -> None:
        monkeypatch.setattr(pygame.joystick, "__file__", None, raising=False)
        assert gp._device_instance_id(0) is None


# --------------------------------------------------------------------------- #
# Construction branches
# --------------------------------------------------------------------------- #


class TestConstruction:
    def test_startup_retry_enabled_when_no_devices(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)
        assert handler._startup_retry_remaining > 0

    def test_startup_retry_skipped_when_controllers_present(self, stubbed_pygame) -> None:
        # Make pygame report one controller at construct time.
        def _factory(i: int) -> FakeJoystick:
            return FakeJoystick(num_buttons=12)

        stubbed_pygame["state"]["count"] = 1
        stubbed_pygame["state"]["factories"][0] = _factory
        app = FakeApp()
        handler = GamepadHandler(app)
        assert handler._startup_retry_remaining == 0
        assert 0 in handler.joysticks


# --------------------------------------------------------------------------- #
# Pygame subsystem initialization + SDL2 fallback paths
# --------------------------------------------------------------------------- #


class TestPygameSubsystemInitOnConstruct:
    def test_calls_pygame_init_when_not_already_initialized(self, monkeypatch) -> None:
        called = {"pygame": False, "joystick": False}

        monkeypatch.setattr(pygame, "get_init", lambda: False)
        monkeypatch.setattr(pygame, "init", lambda: called.__setitem__("pygame", True))
        monkeypatch.setattr(pygame.joystick, "get_init", lambda: False)
        monkeypatch.setattr(
            pygame.joystick,
            "init",
            lambda: called.__setitem__("joystick", True),
        )
        monkeypatch.setattr(pygame.joystick, "quit", lambda: None)
        monkeypatch.setattr(pygame.joystick, "get_count", lambda: 0)
        monkeypatch.setattr(pygame.joystick, "Joystick", lambda i: FakeJoystick())
        monkeypatch.setattr(pygame.event, "get", lambda: [])
        monkeypatch.setattr(pygame.event, "post", lambda e: None)
        monkeypatch.setattr(gp, "sdl2_controller", None)

        GamepadHandler(FakeApp())
        assert called["pygame"] is True
        assert called["joystick"] is True

    @pytest.mark.parametrize(("is_ce", "warns"), [(True, False), (False, True)], ids=["pygame-ce", "classic"])
    def test_warns_when_classic_pygame_loaded(self, stubbed_pygame, monkeypatch, caplog, is_ce, warns) -> None:
        monkeypatch.setattr(pygame, "IS_CE", is_ce, raising=False)
        with caplog.at_level(logging.WARNING, logger="openfollow.input.gamepad"):
            make_handler(stubbed_pygame)
        assert any("classic pygame" in r.message for r in caplog.records) is warns

    def test_keeps_3dconnexion_pucks_off_the_joystick_api_before_init(self, monkeypatch) -> None:
        monkeypatch.delenv("SDL_JOYSTICK_BLACKLIST_DEVICES", raising=False)
        seen: dict[str, str | None] = {}

        monkeypatch.setattr(pygame, "get_init", lambda: False)
        monkeypatch.setattr(
            pygame,
            "init",
            lambda: seen.__setitem__("blacklist", os.environ.get("SDL_JOYSTICK_BLACKLIST_DEVICES")),
        )
        monkeypatch.setattr(pygame.joystick, "get_init", lambda: False)
        monkeypatch.setattr(pygame.joystick, "init", lambda: None)
        monkeypatch.setattr(pygame.joystick, "quit", lambda: None)
        monkeypatch.setattr(pygame.joystick, "get_count", lambda: 0)
        monkeypatch.setattr(pygame.event, "get", lambda: [])
        monkeypatch.setattr(pygame.event, "post", lambda e: None)
        monkeypatch.setattr(gp, "sdl2_controller", None)

        GamepadHandler(FakeApp())

        # SDL takes the list when its joystick subsystem comes up.
        blocked = _sdl_vidpid_pairs(seen["blacklist"] or "")
        assert {(0x046D, 0xC626), (0x256F, 0xC62F), (0x256F, 0xC631)} <= blocked

    def test_keeps_an_explicit_blacklist(self, monkeypatch) -> None:
        monkeypatch.setenv("SDL_JOYSTICK_BLACKLIST_DEVICES", "0x1234/0x5678")

        monkeypatch.setattr(pygame, "get_init", lambda: False)
        monkeypatch.setattr(pygame, "init", lambda: None)
        monkeypatch.setattr(pygame.joystick, "get_init", lambda: False)
        monkeypatch.setattr(pygame.joystick, "init", lambda: None)
        monkeypatch.setattr(pygame.joystick, "quit", lambda: None)
        monkeypatch.setattr(pygame.joystick, "get_count", lambda: 0)
        monkeypatch.setattr(pygame.event, "get", lambda: [])
        monkeypatch.setattr(pygame.event, "post", lambda e: None)
        monkeypatch.setattr(gp, "sdl2_controller", None)

        GamepadHandler(FakeApp())
        assert os.environ["SDL_JOYSTICK_BLACKLIST_DEVICES"] == "0x1234/0x5678"

    def test_points_sdl_audio_at_dummy_driver_before_init(self, monkeypatch) -> None:
        monkeypatch.delenv("SDL_AUDIODRIVER", raising=False)
        seen: dict[str, str | None] = {}

        monkeypatch.setattr(pygame, "get_init", lambda: False)
        monkeypatch.setattr(
            pygame,
            "init",
            lambda: seen.__setitem__("driver", os.environ.get("SDL_AUDIODRIVER")),
        )
        monkeypatch.setattr(pygame.joystick, "get_init", lambda: False)
        monkeypatch.setattr(pygame.joystick, "init", lambda: None)
        monkeypatch.setattr(pygame.joystick, "quit", lambda: None)
        monkeypatch.setattr(pygame.joystick, "get_count", lambda: 0)
        monkeypatch.setattr(pygame.joystick, "Joystick", lambda i: FakeJoystick())
        monkeypatch.setattr(pygame.event, "get", lambda: [])
        monkeypatch.setattr(pygame.event, "post", lambda e: None)
        monkeypatch.setattr(gp, "sdl2_controller", None)

        GamepadHandler(FakeApp())
        assert seen["driver"] == "dummy"

    def test_keeps_explicit_audio_driver_override(self, monkeypatch) -> None:
        monkeypatch.setenv("SDL_AUDIODRIVER", "alsa")

        monkeypatch.setattr(pygame, "get_init", lambda: False)
        monkeypatch.setattr(pygame, "init", lambda: None)
        monkeypatch.setattr(pygame.joystick, "get_init", lambda: False)
        monkeypatch.setattr(pygame.joystick, "init", lambda: None)
        monkeypatch.setattr(pygame.joystick, "quit", lambda: None)
        monkeypatch.setattr(pygame.joystick, "get_count", lambda: 0)
        monkeypatch.setattr(pygame.joystick, "Joystick", lambda i: FakeJoystick())
        monkeypatch.setattr(pygame.event, "get", lambda: [])
        monkeypatch.setattr(pygame.event, "post", lambda e: None)
        monkeypatch.setattr(gp, "sdl2_controller", None)

        GamepadHandler(FakeApp())
        assert os.environ["SDL_AUDIODRIVER"] == "alsa"

    def test_points_sdl_video_at_dummy_driver_before_init(self, monkeypatch) -> None:
        monkeypatch.delenv("SDL_VIDEODRIVER", raising=False)
        seen: dict[str, str | None] = {}

        monkeypatch.setattr(pygame, "get_init", lambda: False)
        monkeypatch.setattr(
            pygame,
            "init",
            lambda: seen.__setitem__("driver", os.environ.get("SDL_VIDEODRIVER")),
        )
        monkeypatch.setattr(pygame.joystick, "get_init", lambda: False)
        monkeypatch.setattr(pygame.joystick, "init", lambda: None)
        monkeypatch.setattr(pygame.joystick, "quit", lambda: None)
        monkeypatch.setattr(pygame.joystick, "get_count", lambda: 0)
        monkeypatch.setattr(pygame.joystick, "Joystick", lambda i: FakeJoystick())
        monkeypatch.setattr(pygame.event, "get", lambda: [])
        monkeypatch.setattr(pygame.event, "post", lambda e: None)
        monkeypatch.setattr(gp, "sdl2_controller", None)

        GamepadHandler(FakeApp())
        # Read at init time, not after: SDL takes the driver when the subsystem
        # comes up, so a setdefault below pygame.init() would be a silent no-op.
        assert seen["driver"] == "dummy"

    def test_keeps_explicit_video_driver_override(self, monkeypatch) -> None:
        monkeypatch.setenv("SDL_VIDEODRIVER", "x11")

        monkeypatch.setattr(pygame, "get_init", lambda: False)
        monkeypatch.setattr(pygame, "init", lambda: None)
        monkeypatch.setattr(pygame.joystick, "get_init", lambda: False)
        monkeypatch.setattr(pygame.joystick, "init", lambda: None)
        monkeypatch.setattr(pygame.joystick, "quit", lambda: None)
        monkeypatch.setattr(pygame.joystick, "get_count", lambda: 0)
        monkeypatch.setattr(pygame.joystick, "Joystick", lambda i: FakeJoystick())
        monkeypatch.setattr(pygame.event, "get", lambda: [])
        monkeypatch.setattr(pygame.event, "post", lambda e: None)
        monkeypatch.setattr(gp, "sdl2_controller", None)

        GamepadHandler(FakeApp())
        assert os.environ["SDL_VIDEODRIVER"] == "x11"

    @pytest.mark.parametrize(("platform", "expected"), [("darwin", "0"), ("linux", None)])
    def test_disables_sdl_hidapi_joysticks_on_macos_before_init(self, monkeypatch, platform, expected) -> None:
        monkeypatch.delenv("SDL_JOYSTICK_HIDAPI", raising=False)
        monkeypatch.setattr("sys.platform", platform)
        seen: dict[str, str | None] = {}

        monkeypatch.setattr(pygame, "get_init", lambda: False)
        monkeypatch.setattr(
            pygame,
            "init",
            lambda: seen.__setitem__("hidapi", os.environ.get("SDL_JOYSTICK_HIDAPI")),
        )
        monkeypatch.setattr(pygame.joystick, "get_init", lambda: False)
        monkeypatch.setattr(pygame.joystick, "init", lambda: None)
        monkeypatch.setattr(pygame.joystick, "quit", lambda: None)
        monkeypatch.setattr(pygame.joystick, "get_count", lambda: 0)
        monkeypatch.setattr(pygame.joystick, "Joystick", lambda i: FakeJoystick())
        monkeypatch.setattr(pygame.event, "get", lambda: [])
        monkeypatch.setattr(pygame.event, "post", lambda e: None)
        monkeypatch.setattr(gp, "sdl2_controller", None)

        GamepadHandler(FakeApp())
        assert seen["hidapi"] == expected

    def test_keeps_explicit_sdl_hidapi_override_on_macos(self, monkeypatch) -> None:
        monkeypatch.setenv("SDL_JOYSTICK_HIDAPI", "1")
        monkeypatch.setattr("sys.platform", "darwin")

        monkeypatch.setattr(pygame, "get_init", lambda: False)
        monkeypatch.setattr(pygame, "init", lambda: None)
        monkeypatch.setattr(pygame.joystick, "get_init", lambda: False)
        monkeypatch.setattr(pygame.joystick, "init", lambda: None)
        monkeypatch.setattr(pygame.joystick, "quit", lambda: None)
        monkeypatch.setattr(pygame.joystick, "get_count", lambda: 0)
        monkeypatch.setattr(pygame.joystick, "Joystick", lambda i: FakeJoystick())
        monkeypatch.setattr(pygame.event, "get", lambda: [])
        monkeypatch.setattr(pygame.event, "post", lambda e: None)
        monkeypatch.setattr(gp, "sdl2_controller", None)

        GamepadHandler(FakeApp())
        assert os.environ["SDL_JOYSTICK_HIDAPI"] == "1"

    def test_calls_sdl2_controller_init_when_available_and_uninit(self, monkeypatch) -> None:
        sdl2_calls = {"init": False}

        class _FakeSdl2:
            @staticmethod
            def get_init():
                return False

            @staticmethod
            def init():
                sdl2_calls["init"] = True

            @staticmethod
            def quit():
                sdl2_calls["init"] = False

            @staticmethod
            def is_controller(_idx):
                return False

        monkeypatch.setattr(pygame, "get_init", lambda: True)
        monkeypatch.setattr(pygame.joystick, "get_init", lambda: True)
        monkeypatch.setattr(pygame.joystick, "quit", lambda: None)
        monkeypatch.setattr(pygame.joystick, "get_count", lambda: 0)
        monkeypatch.setattr(pygame.joystick, "Joystick", lambda i: FakeJoystick())
        monkeypatch.setattr(pygame.event, "get", lambda: [])
        monkeypatch.setattr(pygame.event, "post", lambda e: None)
        monkeypatch.setattr(gp, "sdl2_controller", _FakeSdl2)

        GamepadHandler(FakeApp())
        assert sdl2_calls["init"] is True


class TestSdl2ControllerOpenSuccess:
    def test_sdl2_open_succeeds_records_controller_and_sdl2_backend(
        self,
        monkeypatch,
    ) -> None:
        """Happy SDL2 path: when is_controller(i) is True and Controller(i)
        succeeds, the handler stores the SDL2 controller object AND tags
        the backend as `sdl2_controller`. This is the path that unlocks
        labelled axis access (LEFTX/LEFTY/etc.) instead of raw indices."""
        joy = FakeJoystick(num_buttons=12)
        opened: list[int] = []

        class _Sdl2Ctrl:
            def __init__(self, idx):
                opened.append(idx)

        class _FakeSdl2:
            @staticmethod
            def get_init():
                return True

            @staticmethod
            def init():
                pass

            @staticmethod
            def quit():
                pass

            @staticmethod
            def is_controller(_idx):
                return True

            Controller = _Sdl2Ctrl

        monkeypatch.setattr(pygame, "get_init", lambda: True)
        monkeypatch.setattr(pygame.joystick, "get_init", lambda: True)
        monkeypatch.setattr(pygame.joystick, "quit", lambda: None)
        monkeypatch.setattr(pygame.joystick, "get_count", lambda: 1)
        monkeypatch.setattr(pygame.joystick, "Joystick", lambda i: joy)
        monkeypatch.setattr(pygame.event, "get", lambda: [])
        monkeypatch.setattr(pygame.event, "post", lambda e: None)
        monkeypatch.setattr(gp, "sdl2_controller", _FakeSdl2)

        handler = GamepadHandler(FakeApp())
        assert opened == [0]
        assert isinstance(handler.controllers.get(0), _Sdl2Ctrl)
        assert handler.capabilities[0].backend == "sdl2_controller"


class TestSdl2ControllerOpenFailure:
    def test_failed_sdl2_open_falls_back_to_joystick_backend(self, monkeypatch) -> None:
        joy = FakeJoystick(num_buttons=12)

        class _FakeSdl2:
            @staticmethod
            def get_init():
                return True

            @staticmethod
            def init():
                pass

            @staticmethod
            def quit():
                pass

            @staticmethod
            def is_controller(_idx):
                return True

            @staticmethod
            def Controller(_idx):
                raise pygame.error("sdl2 open failed")

        monkeypatch.setattr(pygame, "get_init", lambda: True)
        monkeypatch.setattr(pygame.joystick, "get_init", lambda: True)
        monkeypatch.setattr(pygame.joystick, "quit", lambda: None)
        monkeypatch.setattr(pygame.joystick, "get_count", lambda: 1)
        monkeypatch.setattr(pygame.joystick, "Joystick", lambda i: joy)
        monkeypatch.setattr(pygame.event, "get", lambda: [])
        monkeypatch.setattr(pygame.event, "post", lambda e: None)
        monkeypatch.setattr(gp, "sdl2_controller", _FakeSdl2)

        handler = GamepadHandler(FakeApp())
        assert 0 in handler.joysticks
        assert 0 not in handler.controllers
        assert handler.capabilities[0].backend == "joystick"


class TestDetectControllersErrorPaths:
    def test_old_controllers_quit_pygame_error_is_swallowed(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)

        class _ExplodingCtrl:
            def quit(self):
                raise pygame.error("ctrl quit boom")

        handler.joysticks[0] = FakeJoystick()
        handler.controllers[0] = _ExplodingCtrl()
        stubbed_pygame["state"]["count"] = 0  # all devices gone
        handler._detect_controllers()
        assert handler.controllers == {}

    def test_old_joysticks_quit_pygame_error_is_swallowed(self, stubbed_pygame) -> None:
        """Mirror of the controller-quit guard, on the raw-joystick side."""
        handler, _ = make_handler(stubbed_pygame)

        class _ExplodingJoy:
            def quit(self):
                raise pygame.error("joy quit boom")

        handler.joysticks[0] = _ExplodingJoy()
        stubbed_pygame["state"]["count"] = 0
        handler._detect_controllers()
        assert handler.joysticks == {}

    def test_joystick_init_pygame_error_is_logged_and_skipped(self, stubbed_pygame, caplog) -> None:
        import logging as _logging

        good_joy = FakeJoystick(num_buttons=12)

        def _open_fails(_i: int):
            raise pygame.error("device gone")

        stubbed_pygame["state"]["count"] = 2
        stubbed_pygame["state"]["factories"][0] = _open_fails
        stubbed_pygame["state"]["factories"][1] = lambda i: good_joy

        handler, _ = make_handler(stubbed_pygame)
        with caplog.at_level(_logging.ERROR, logger="openfollow.input.gamepad"):
            handler._detect_controllers(force=True)

        assert 0 not in handler.joysticks
        assert 1 in handler.joysticks
        assert any("Failed to open gamepad at SDL device index 0" in r.message for r in caplog.records)

    def test_pygame_error_on_get_count_is_swallowed(self, stubbed_pygame, caplog, monkeypatch) -> None:
        import logging as _logging

        handler, _ = make_handler(stubbed_pygame)

        def _raise():
            raise pygame.error("count failed")

        monkeypatch.setattr(pygame.joystick, "get_count", _raise)
        with caplog.at_level(_logging.ERROR, logger="openfollow.input.gamepad"):
            handler._detect_controllers(force=True)
        assert any("Error detecting controllers" in r.message for r in caplog.records)

    def test_force_logs_refreshed_message_when_count_unchanged(
        self,
        stubbed_pygame,
        caplog,
    ) -> None:
        """force=True with count==old must hit the "map refreshed" log
        branch (distinct from the connect/disconnect arms)."""
        import logging as _logging

        handler, _ = make_handler(stubbed_pygame)
        stubbed_pygame["state"]["count"] = 0
        with caplog.at_level(_logging.INFO, logger="openfollow.input.gamepad"):
            handler._detect_controllers(force=True)
        assert any("Controller map refreshed" in r.message for r in caplog.records)


# --------------------------------------------------------------------------- #
# Apply config: raw_indices remap + curve fall-through
# --------------------------------------------------------------------------- #


class TestApplyConfigRawIndicesRemap:
    def test_raw_indices_install_remap_for_mismatched_indices(self, stubbed_pygame) -> None:
        """When the wizard has run, raw button indices that differ from the
        SDL2 logical IDs install entries in ``_raw_button_remap`` –
        that's how oddly-laid-out raw joysticks get corrected. Indices
        that already match the logical ID are skipped to keep the
        remap minimal."""
        cfg = ControllerConfig()
        cfg.button_raw_indices = {"A": 99, "B": CONTROLLER_BUTTON_B}
        handler, _ = make_handler(stubbed_pygame, config=cfg)
        # A's raw idx (99) != logical CONTROLLER_BUTTON_A → entry installed.
        assert handler._raw_button_remap.get(CONTROLLER_BUTTON_A) == 99
        # B's raw idx == logical CONTROLLER_BUTTON_B → no remap entry.
        assert CONTROLLER_BUTTON_B not in handler._raw_button_remap

    def test_raw_indices_unknown_label_skipped(self, stubbed_pygame) -> None:
        """An unknown label in the raw_indices dict (e.g. typo "XX") must
        not install a remap entry – and must not crash with KeyError."""
        cfg = ControllerConfig()
        cfg.button_raw_indices = {"XX": 5}
        handler, _ = make_handler(stubbed_pygame, config=cfg)
        # Empty remap; nothing matched the unknown label.
        assert 5 not in handler._raw_button_remap.values()

    def test_raw_indices_remap_isolated_from_sdl_controller_path(
        self,
        stubbed_pygame,
    ) -> None:
        """Regression for the GameSir G7 SE bug: with both
        ``raw_indices`` (hardware) and ``map_*`` (SDL2-logical) set,
        the SDL2 controller path must read via the SDL2-logical remap
        only – feeding a raw hardware index to ``controller.get_button``
        would silently mis-read (raw 4 ≠ SDL2 logical BACK)."""
        cfg = ControllerConfig()
        cfg.button_raw_indices = {"LB": 4, "RB": 5}
        cfg.map_lb = "BACK"  # SDL2 maps physical LB → logical BACK
        cfg.map_rb = "RB"  # SDL2 maps physical RB → logical RB
        handler, _ = make_handler(stubbed_pygame, config=cfg)

        # Press SDL2 logical BACK on the controller – that's what the
        # SDL2 driver reports when the operator presses physical LB.
        ctrl = FakeController()
        ctrl.press(CONTROLLER_BUTTON_BACK)
        handler.controllers[0] = ctrl

        # Asking for LB (logical LEFTSHOULDER=9) must route through
        # the SDL2-logical remap (LEFTSHOULDER → BACK=4), NOT through
        # the raw remap (LEFTSHOULDER → raw 4).
        assert handler._get_button(0, CONTROLLER_BUTTON_LEFTSHOULDER) is True

        # And RB: ``map_rb = "RB"`` means SDL2 mapping is correct,
        # no remap needed, so pressing SDL2 logical RIGHTSHOULDER
        # reads true.
        ctrl.release(CONTROLLER_BUTTON_BACK)
        ctrl.press(CONTROLLER_BUTTON_RIGHTSHOULDER)
        assert handler._get_button(0, CONTROLLER_BUTTON_RIGHTSHOULDER) is True

    def test_raw_indices_drives_joystick_path(self, stubbed_pygame) -> None:
        """On the raw joystick path (no SDL2 controller), the raw-index
        remap is what reaches the joystick – the same wizard data that
        was previously misapplied to SDL2 now correctly drives the
        joystick fallback."""
        cfg = ControllerConfig()
        cfg.button_raw_indices = {"LB": 4, "RB": 5}
        handler, _ = make_handler(stubbed_pygame, config=cfg)

        joy = FakeJoystick(num_buttons=12)
        joy.press(4)  # physical LB at raw 4
        handler.joysticks[0] = joy

        # Asking for SDL2 logical LEFTSHOULDER (9) on the joystick
        # path must route through _raw_button_remap to raw 4.
        assert handler._get_button(0, CONTROLLER_BUTTON_LEFTSHOULDER) is True


class TestApplyCurveFallthrough:
    def test_unknown_curve_returns_value_verbatim(self, stubbed_pygame) -> None:
        cfg = ControllerConfig()
        cfg.curve = "no-such-curve"
        handler, _ = make_handler(stubbed_pygame, config=cfg)
        assert handler._apply_curve(0.5) == 0.5
        assert handler._apply_curve(1.0) == 1.0


# --------------------------------------------------------------------------- #
# _read_axes: pygame.error swallowing
# --------------------------------------------------------------------------- #


class TestReadAxesErrorSwallow:
    def test_pygame_error_during_read_returns_zero_vector(self, stubbed_pygame, caplog) -> None:
        import logging as _logging

        class _ExplodingJoy(FakeJoystick):
            def get_axis(self, _idx):
                raise pygame.error("controller gone")

        joy = _ExplodingJoy(num_axes=4)
        handler, _ = make_handler(stubbed_pygame)
        with caplog.at_level(_logging.WARNING, logger="openfollow.input.gamepad"):
            result = handler._read_axes(0, joy)
        assert result == (0.0, 0.0, 0.0)
        assert any("Error reading axes" in r.message for r in caplog.records)


# --------------------------------------------------------------------------- #
# _get_button + _get_trigger_as_button: missing-device guards
# --------------------------------------------------------------------------- #


class TestGetButtonMissingDevice:
    def test_get_button_returns_false_when_no_joystick_or_controller(
        self,
        stubbed_pygame,
    ) -> None:
        """No device wired at the requested index → returns False rather
        than raising KeyError (which would crash the per-frame button
        scan)."""
        handler, _ = make_handler(stubbed_pygame)
        assert handler._get_button(99, CONTROLLER_BUTTON_A) is False

    def test_get_trigger_as_button_returns_false_when_no_joystick(
        self,
        stubbed_pygame,
    ) -> None:
        """Trigger-as-button needs a joystick to read the axis from –
        missing-index path returns False."""
        handler, _ = make_handler(stubbed_pygame)
        assert handler._get_trigger_as_button(99, BUTTON_ID_LT) is False

    def test_get_trigger_as_button_swap_swaps_axis_via_controller(
        self,
        stubbed_pygame,
    ) -> None:
        """With swap_triggers=True the LT button reads the *right* trigger
        axis on an SDL2 controller (and vice versa) – needed when the
        physical controller has the trigger cables crossed."""
        cfg = ControllerConfig()
        cfg.swap_triggers = True
        handler, _ = make_handler(stubbed_pygame, config=cfg)
        joy = FakeJoystick(num_axes=8)
        ctrl = FakeController()
        # Press the right-trigger axis past the deadzone; LT button query
        # must read this axis because of the swap.
        ctrl.set_axis(CONTROLLER_AXIS_TRIGGERRIGHT, 32000)
        handler.joysticks[0] = joy
        handler.controllers[0] = ctrl
        assert handler._get_trigger_as_button(0, BUTTON_ID_LT) is True
        # And the RT button (with swap) reads the left axis (idle).
        assert handler._get_trigger_as_button(0, BUTTON_ID_RT) is False

    def test_get_trigger_as_button_swap_swaps_axis_via_raw_joystick(
        self,
        stubbed_pygame,
    ) -> None:
        """Same swap semantics on a raw joystick (no SDL2 controller bound).
        Raw triggers report a delta from baseline, so prime the baseline at
        rest before deflecting."""
        cfg = ControllerConfig()
        cfg.swap_triggers = True
        handler, _ = make_handler(stubbed_pygame, config=cfg)
        joy = FakeJoystick(num_axes=8)
        handler.joysticks[0] = joy

        # Baseline pass at rest: every trigger axis reports 0.0.
        baselines = dict.fromkeys((*gp.LT_AXIS_INDICES, *gp.RT_AXIS_INDICES), 0.0)
        handler._shoulder_axis_baselines[0] = dict(baselines)

        # Now deflect every RT axis past the deadzone.
        for idx in gp.RT_AXIS_INDICES:
            if idx < 8:
                joy.set_axis(idx, 0.95)
        # LT button query (with swap) reads RT axis → past threshold.
        assert handler._get_trigger_as_button(0, BUTTON_ID_LT) is True


# --------------------------------------------------------------------------- #
# update(): startup-retry tick
# --------------------------------------------------------------------------- #


class TestUpdateStartupRetry:
    def test_retry_tick_calls_redetect_when_interval_elapsed(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)
        # Confirm baseline: no controllers, retry active.
        assert handler._startup_retry_remaining > 0

        redetect_calls = {"force": 0}
        original = handler._detect_controllers

        def _spy(force=False):
            redetect_calls["force"] += int(bool(force))
            return original(force=force)

        handler._detect_controllers = _spy
        # Tick past the interval (default 2.0s).
        handler.update(GamepadHandler._STARTUP_RETRY_INTERVAL + 0.01)
        assert redetect_calls["force"] >= 1

    def test_retry_tick_below_interval_does_not_redetect(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)
        assert handler._startup_retry_remaining > 0

        redetect_calls = {"force": 0}

        def _spy(force=False):
            redetect_calls["force"] += int(bool(force))

        handler._detect_controllers = _spy
        # Tick a fraction of the interval – timer accumulates but
        # doesn't reach the threshold.
        handler.update(GamepadHandler._STARTUP_RETRY_INTERVAL * 0.1)
        assert redetect_calls["force"] == 0

    def test_retry_stops_when_controller_appears(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)
        # Replace _detect_controllers so a "device" appears on the next call.
        joy = FakeJoystick(num_buttons=12)

        def _appears(force=False):
            handler.joysticks[0] = joy

        handler._detect_controllers = _appears
        handler.update(GamepadHandler._STARTUP_RETRY_INTERVAL + 0.01)
        assert handler._startup_retry_remaining == 0
        assert 0 in handler.joysticks

    def test_retry_reinitialises_sdl2_controller_subsystem(
        self,
        monkeypatch,
    ) -> None:
        sdl2_calls = {"quit": 0, "init": 0}

        class _FakeSdl2:
            @staticmethod
            def get_init():
                return True

            @staticmethod
            def init():
                sdl2_calls["init"] += 1

            @staticmethod
            def quit():
                sdl2_calls["quit"] += 1
                # Force the except-arm on this very call so the
                # error-swallow path also gets exercised.
                raise pygame.error("sdl2 quit failed")

            @staticmethod
            def is_controller(_idx):
                return False

        monkeypatch.setattr(pygame, "get_init", lambda: True)
        monkeypatch.setattr(pygame, "init", lambda: None)
        monkeypatch.setattr(pygame.joystick, "get_init", lambda: True)
        monkeypatch.setattr(pygame.joystick, "init", lambda: None)
        monkeypatch.setattr(pygame.joystick, "quit", lambda: None)
        monkeypatch.setattr(pygame.joystick, "get_count", lambda: 0)
        monkeypatch.setattr(pygame.joystick, "Joystick", lambda i: FakeJoystick())
        monkeypatch.setattr(pygame.event, "get", lambda: [])
        monkeypatch.setattr(pygame.event, "post", lambda e: None)
        monkeypatch.setattr(gp, "sdl2_controller", _FakeSdl2)

        handler = GamepadHandler(FakeApp())
        # Retry must be active after the empty-startup detection.
        assert handler._startup_retry_remaining > 0

        handler.update(GamepadHandler._STARTUP_RETRY_INTERVAL + 0.01)
        # SDL2 quit was attempted (raised), exception was swallowed, init
        # was NOT called (because quit raised before reaching init).
        assert sdl2_calls["quit"] == 1

    def test_retry_warns_when_window_expires_with_no_devices(
        self,
        stubbed_pygame,
        caplog,
    ) -> None:
        """When the retry budget runs out without any controller appearing,
        the user gets a warning so they know hotplug is the only path now."""
        import logging as _logging

        handler, _ = make_handler(stubbed_pygame)
        # Compress the retry window so a single tick exhausts it.
        handler._startup_retry_remaining = GamepadHandler._STARTUP_RETRY_INTERVAL
        handler._detect_controllers = lambda force=False: None
        with caplog.at_level(_logging.WARNING, logger="openfollow.input.gamepad"):
            handler.update(GamepadHandler._STARTUP_RETRY_INTERVAL + 0.01)
        assert any("No controllers found after startup" in r.message for r in caplog.records)


# --------------------------------------------------------------------------- #
# update(): axis dump branch - sdl2 axis read with errors
# --------------------------------------------------------------------------- #


class TestUpdateAxisDump:
    def test_axis_dump_includes_sdl2_axes_when_controller_present(
        self,
        stubbed_pygame,
        caplog,
    ) -> None:
        """The first-frame axis dump for a controller-backed device pulls
        each SDL2 logical axis. Failures on individual axis reads must
        be tagged "err" rather than blow up the dump."""
        import logging as _logging

        joy = FakeJoystick(num_buttons=12, num_axes=6)

        class _PartialController(FakeController):
            def get_axis(self, axis):
                if axis == CONTROLLER_AXIS_LEFTX:
                    raise pygame.error("axis read failed")
                return super().get_axis(axis)

        ctrl = _PartialController()

        handler, _ = make_handler(stubbed_pygame)
        handler.joysticks[0] = joy
        handler.controllers[0] = ctrl

        with caplog.at_level(_logging.INFO, logger="openfollow.input.gamepad"):
            handler.update(0.01)
        # Dump must have run exactly once and recorded an "err" for the
        # failing axis without crashing.
        assert 0 in handler._axes_logged
        assert any("axis dump" in r.message for r in caplog.records)


# --------------------------------------------------------------------------- #
# read_source_selection_input + read_settings_menu_input: error handling
# --------------------------------------------------------------------------- #


class TestSourceSelectionInputErrors:
    def test_pygame_error_on_read_marks_for_cleanup(
        self,
        stubbed_pygame,
        caplog,
    ) -> None:
        import logging as _logging

        handler, _ = make_handler(stubbed_pygame)

        # Inject a joystick whose button-prev access raises mid-read by
        # routing through _detect_button_edge → _get_button → joystick.
        class _ExplodingJoy(FakeJoystick):
            def get_button(self, _idx):
                raise pygame.error("dead")

        handler.joysticks[0] = _ExplodingJoy(num_buttons=12)

        with caplog.at_level(_logging.WARNING, logger="openfollow.input.gamepad"):
            handler.read_source_selection_input()
        assert 0 not in handler.joysticks


class TestSettingsMenuInputErrors:
    def test_pygame_error_on_read_marks_for_cleanup(
        self,
        stubbed_pygame,
        caplog,
    ) -> None:
        import logging as _logging

        handler, _ = make_handler(stubbed_pygame)

        class _ExplodingJoy(FakeJoystick):
            def get_button(self, _idx):
                raise pygame.error("dead")

        handler.joysticks[0] = _ExplodingJoy(num_buttons=12)

        with caplog.at_level(_logging.WARNING, logger="openfollow.input.gamepad"):
            handler.read_settings_menu_input()
        assert 0 not in handler.joysticks


# --------------------------------------------------------------------------- #
# stop(): error swallowing on quit + sdl2/pygame teardown
# --------------------------------------------------------------------------- #


class TestStopErrorSwallowing:
    def test_controller_quit_pygame_error_swallowed_during_stop(
        self,
        stubbed_pygame,
    ) -> None:
        joy = FakeJoystick()

        class _ExplodingCtrl:
            def quit(self):
                raise pygame.error("ctrl bad")

        handler, _ = make_handler(stubbed_pygame)
        handler.controllers[0] = _ExplodingCtrl()
        handler.joysticks[0] = joy
        handler.stop()
        assert handler.enabled is False
        assert handler.joysticks == {}
        assert handler.controllers == {}

    def test_joystick_quit_pygame_error_swallowed_during_stop(
        self,
        stubbed_pygame,
    ) -> None:
        """Mirror of the controller-quit guard for raw joysticks."""

        class _ExplodingJoy:
            def quit(self):
                raise pygame.error("joy bad")

        handler, _ = make_handler(stubbed_pygame)
        handler.joysticks[0] = _ExplodingJoy()
        handler.stop()
        assert handler.joysticks == {}

    def test_stop_quits_sdl2_controller_and_pygame_subsystems(
        self,
        monkeypatch,
    ) -> None:
        called = {"sdl2_quit": False, "joy_quit": False, "pyg_quit": False}

        class _FakeSdl2:
            inited = True

            @staticmethod
            def get_init():
                return _FakeSdl2.inited

            @staticmethod
            def init():
                _FakeSdl2.inited = True

            @staticmethod
            def quit():
                called["sdl2_quit"] = True
                _FakeSdl2.inited = False

            @staticmethod
            def is_controller(_idx):
                return False

        # Construct phase: pretend everything is already initialised so
        # the handler doesn't toggle our flags.
        monkeypatch.setattr(pygame, "get_init", lambda: True)
        monkeypatch.setattr(pygame, "init", lambda: None)
        monkeypatch.setattr(
            pygame,
            "quit",
            lambda: called.__setitem__("pyg_quit", True),
        )
        monkeypatch.setattr(pygame.joystick, "get_init", lambda: True)
        monkeypatch.setattr(pygame.joystick, "init", lambda: None)
        monkeypatch.setattr(
            pygame.joystick,
            "quit",
            lambda: called.__setitem__("joy_quit", True),
        )
        monkeypatch.setattr(pygame.joystick, "get_count", lambda: 0)
        monkeypatch.setattr(pygame.joystick, "Joystick", lambda i: FakeJoystick())
        monkeypatch.setattr(pygame.event, "get", lambda: [])
        monkeypatch.setattr(pygame.event, "post", lambda e: None)
        monkeypatch.setattr(gp, "sdl2_controller", _FakeSdl2)

        handler = GamepadHandler(FakeApp())
        handler.stop()
        assert called["sdl2_quit"] is True
        assert called["joy_quit"] is True
        assert called["pyg_quit"] is True


# --------------------------------------------------------------------------- #
# Hardware event emission: gamepad → InputEventBus
# --------------------------------------------------------------------------- #

from openfollow.input.events import ButtonEvent, InputEventBus  # noqa: E402


def _attach_fake_joystick(
    handler: GamepadHandler,
    idx: int = 0,
) -> FakeJoystick:
    """Common setup: attach a FakeJoystick with the capability /
    bumper-state / shoulder-axis-baseline state ``update()`` expects."""
    joy = FakeJoystick(num_buttons=16)
    handler.joysticks[idx] = joy
    handler.capabilities[idx] = ControllerCapabilities(backend="joystick")
    handler._bumper_state[idx] = (False, False)
    handler._shoulder_axis_baselines[idx] = {}
    return joy


class TestEventBusEmission:
    """Hardware-emission path: every controller button transition
    emits a :class:`ButtonEvent` on the bus, parallel to (not
    duplicating) the existing edge-detection consumers like
    settings-button / next-marker / etc."""

    def test_button_press_emits_event(self, stubbed_pygame) -> None:
        bus = InputEventBus()
        seen: list[ButtonEvent] = []
        bus.subscribe_button(seen.append)
        app = FakeApp()
        handler = GamepadHandler(app, event_bus=bus)
        joy = _attach_fake_joystick(handler)
        joy.press(CONTROLLER_BUTTON_A)

        handler.update(0.016)

        a_events = [e for e in seen if e.button == "A"]
        assert a_events == [
            ButtonEvent(
                button="A",
                controller_index=0,
                edge="press",
            ),
        ]

    def test_button_release_emits_event(self, stubbed_pygame) -> None:
        bus = InputEventBus()
        seen: list[ButtonEvent] = []
        bus.subscribe_button(seen.append)
        app = FakeApp()
        handler = GamepadHandler(app, event_bus=bus)
        joy = _attach_fake_joystick(handler)
        joy.press(CONTROLLER_BUTTON_A)
        handler.update(0.016)
        joy.release(CONTROLLER_BUTTON_A)
        handler.update(0.016)

        a_events = [e for e in seen if e.button == "A"]
        assert a_events == [
            ButtonEvent(button="A", controller_index=0, edge="press"),
            ButtonEvent(button="A", controller_index=0, edge="release"),
        ]

    def test_held_button_fires_press_exactly_once(
        self,
        stubbed_pygame,
    ) -> None:
        """Held key/button fires on press exactly once. Multiple update()
        calls emit only the initial press event."""
        bus = InputEventBus()
        seen: list[ButtonEvent] = []
        bus.subscribe_button(seen.append)
        app = FakeApp()
        handler = GamepadHandler(app, event_bus=bus)
        joy = _attach_fake_joystick(handler)
        joy.press(CONTROLLER_BUTTON_A)
        for _ in range(5):
            handler.update(0.016)

        a_events = [e for e in seen if e.button == "A"]
        assert a_events == [
            ButtonEvent(button="A", controller_index=0, edge="press"),
        ]

    def test_no_emission_when_bus_is_none(self, stubbed_pygame) -> None:
        """``event_bus=None`` is the headless / test-harness code path
        – handler still computes movements + edge dispatch for
        existing consumers but emits no bus events."""
        app = FakeApp()
        handler = GamepadHandler(app, event_bus=None)
        joy = _attach_fake_joystick(handler)
        joy.press(CONTROLLER_BUTTON_A)
        # No bus → nothing to assert except that this doesn't raise.
        handler.update(0.016)

    def test_event_carries_controller_index(self, stubbed_pygame) -> None:
        """Multi-controller setups stamp ``controller_index`` so a
        future per-controller binding can route correctly. Today's
        match contract ignores it, but the wire is in place."""
        bus = InputEventBus()
        seen: list[ButtonEvent] = []
        bus.subscribe_button(seen.append)
        app = FakeApp()
        handler = GamepadHandler(app, event_bus=bus)
        joy_a = _attach_fake_joystick(handler, idx=0)
        joy_b = _attach_fake_joystick(handler, idx=1)
        joy_a.press(CONTROLLER_BUTTON_A)
        joy_b.press(CONTROLLER_BUTTON_B)
        handler.update(0.016)

        events_by_index = {(e.controller_index, e.button): e for e in seen}
        assert (0, "A") in events_by_index
        assert events_by_index[(0, "A")].edge == "press"
        assert (1, "B") in events_by_index
        assert events_by_index[(1, "B")].edge == "press"

    def test_dpad_button_emits_event(self, stubbed_pygame) -> None:
        """DPAD buttons are part of the same emission set –
        operators can bind ``DPAD_UP`` etc. as a ControllerButton
        trigger key."""
        bus = InputEventBus()
        seen: list[ButtonEvent] = []
        bus.subscribe_button(seen.append)
        app = FakeApp()
        handler = GamepadHandler(app, event_bus=bus)
        joy = _attach_fake_joystick(handler)
        joy.press(CONTROLLER_BUTTON_DPAD_UP)
        handler.update(0.016)

        dpad_events = [e for e in seen if e.button == "DPAD_UP"]
        assert dpad_events == [
            ButtonEvent(
                button="DPAD_UP",
                controller_index=0,
                edge="press",
            ),
        ]

    def test_cleanup_clears_bus_state(self, stubbed_pygame) -> None:
        """When a controller errors mid-poll and gets cleaned up, its
        bus-emission state is also dropped – a re-attached controller
        with the same index doesn't see ghost ``release`` events for
        buttons the previous controller had pressed."""
        bus = InputEventBus()
        seen: list[ButtonEvent] = []
        bus.subscribe_button(seen.append)
        app = FakeApp()
        handler = GamepadHandler(app, event_bus=bus)

        class ExplodingJoy(FakeJoystick):
            def get_axis(self, idx: int) -> float:  # type: ignore[override]
                raise pygame.error("disconnected")

        joy = ExplodingJoy(num_buttons=16, num_axes=6)
        handler.joysticks[0] = joy
        handler.capabilities[0] = ControllerCapabilities(backend="joystick")
        handler._bumper_state[0] = (False, False)
        handler._shoulder_axis_baselines[0] = {}
        handler._axes_logged.add(0)
        # Pre-populate bus state so we can assert it gets cleared.
        handler._button_bus_prev[0] = {CONTROLLER_BUTTON_A: True}

        handler.update(0.016)

        assert 0 not in handler._button_bus_prev

    def test_pygame_error_during_emit_routes_through_cleanup(
        self,
        stubbed_pygame,
    ) -> None:
        """A controller that disconnects between the ``update()`` poll
        and the bus-emission walk raises ``pygame.error`` from
        ``_get_button``. The new try/except inside
        ``_emit_button_events`` catches it, marks the controller for
        cleanup, and routes it through ``_cleanup_failed`` – the input
        loop keeps running for the surviving controllers instead of
        propagating the exception out of ``update()``.


        The fake raises *only* for ``CONTROLLER_BUTTON_A`` so the
        bumper handler in ``update()`` (which reads LB/RB) and the
        axis reads succeed cleanly. That way the main ``update()``
        loop's try/except doesn't fire, the controller stays in
        ``self.joysticks`` past ``_cleanup_failed``, and the
        ``pygame.error`` actually surfaces inside
        ``_emit_button_events`` – exercising the new emission-time
        cleanup path.
        """
        bus = InputEventBus()
        seen: list[ButtonEvent] = []
        bus.subscribe_button(seen.append)
        app = FakeApp()
        handler = GamepadHandler(app, event_bus=bus)

        class ExplodingOnA(FakeJoystick):
            """Selective failure: raises pygame.error when ``get_button``
            is called for button A, which is the first id
            ``_emit_button_events`` walks. Bumper / axis reads use
            different ids so the main ``update()`` body doesn't tear
            the controller down before emission runs."""

            def get_button(self, idx: int) -> int:  # type: ignore[override]
                if idx == CONTROLLER_BUTTON_A:
                    raise pygame.error("button-A read failed mid-emit")
                return super().get_button(idx)

        joy = ExplodingOnA(num_buttons=16, num_axes=6)
        handler.joysticks[0] = joy
        handler.capabilities[0] = ControllerCapabilities(backend="joystick")
        handler._bumper_state[0] = (False, False)
        handler._shoulder_axis_baselines[0] = {}
        handler._axes_logged.add(0)

        # Must not raise – that's the contract being pinned.
        handler.update(0.016)

        # Cleanup ran via the emit-side cleanup path.
        assert 0 not in handler.joysticks
        assert 0 not in handler._button_bus_prev


class TestApplyConfigClearsBusPrev:
    """``apply_config`` must clear both ``_button_prev`` (legacy edge detector) and
    ``_button_bus_prev`` (bus-edge detector) to prevent ghost press/release events
    after a button-remap change at runtime."""

    def test_apply_config_clears_button_bus_prev(self, stubbed_pygame) -> None:
        bus = InputEventBus()
        app = FakeApp()
        handler = GamepadHandler(app, event_bus=bus)
        # Pre-populate state both edge detectors would use.
        handler._button_prev[0] = {CONTROLLER_BUTTON_A: True}
        handler._button_bus_prev[0] = {CONTROLLER_BUTTON_A: True}

        handler.apply_config()

        # Both detectors must restart from a clean slate so a remap
        # cannot fabricate a phantom edge on the next update.
        assert handler._button_prev == {}
        assert handler._button_bus_prev == {}


class TestDetectControllersRebuildsBusPrev:
    """On hotplug, ``_detect_controllers`` must rebuild both ``_button_prev`` and
    ``_button_bus_prev`` to prevent stale state from causing ghost release/press events
    when a new controller reuses a previously-connected index."""

    def test_disconnect_drops_bus_prev_for_gone_index(self, stubbed_pygame) -> None:
        bus = InputEventBus()
        app = FakeApp()
        handler = GamepadHandler(app, event_bus=bus)
        # Simulate one connected controller with bus state.
        joy = FakeJoystick()
        handler.joysticks[0] = joy
        handler._button_bus_prev[0] = {CONTROLLER_BUTTON_A: True}

        # Hotplug: device disappears silently (no pygame.error).
        stubbed_pygame["state"]["count"] = 0
        handler._detect_controllers()

        assert 0 not in handler._button_bus_prev

    def test_reconnect_at_same_index_starts_with_clean_bus_prev(
        self,
        stubbed_pygame,
    ) -> None:
        bus = InputEventBus()
        app = FakeApp()
        handler = GamepadHandler(app, event_bus=bus)
        handler.joysticks[0] = FakeJoystick()
        handler._button_bus_prev[0] = {CONTROLLER_BUTTON_A: True}

        # Disconnect.
        stubbed_pygame["state"]["count"] = 0
        handler._detect_controllers()
        assert 0 not in handler._button_bus_prev

        # Reconnect at the same index – fresh device, no carry-over.
        stubbed_pygame["state"]["count"] = 1
        handler._detect_controllers()
        assert handler._button_bus_prev.get(0) == {}

    def test_surviving_index_keeps_its_bus_prev(self, stubbed_pygame) -> None:
        bus = InputEventBus()
        app = FakeApp()
        handler = GamepadHandler(app, event_bus=bus)
        handler.joysticks[0] = FakeJoystick()
        handler._button_bus_prev[0] = {CONTROLLER_BUTTON_A: True}

        # force=True with an unchanged count rebuilds the map.
        stubbed_pygame["state"]["count"] = 1
        handler._detect_controllers(force=True)

        assert handler._button_bus_prev[0] == {CONTROLLER_BUTTON_A: True}


# --------------------------------------------------------------------------- #
# Marker-fader integrator (gamepad stick → the controlled marker's fader)
# --------------------------------------------------------------------------- #


class _FakeFaderBus:
    """Records every ``set_marker_fader_from_velocity_delta`` call as
    ``(marker_id, delta)`` for assertions."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, float]] = []

    def set_marker_fader_from_velocity_delta(
        self,
        marker_id: int,
        delta: float,
    ) -> None:
        self.calls.append((marker_id, delta))


# Resolver that maps every controller index to one fixed marker id, so a
# test that drives the stick sees the call land on a known marker.
def _resolve_to(marker_id: int | None):  # noqa: ANN202 - test helper
    return lambda _controller_idx: marker_id


class TestMarkerFaderIntegrator:
    def test_no_op_when_stick_unset(self, stubbed_pygame) -> None:
        # Default ``marker_fader_stick = ""`` – integrator is dormant even with
        # a deflected stick. Operator opted out, no fader motion.
        bus = _FakeFaderBus()
        app = FakeApp()
        handler = GamepadHandler(
            app,
            virtual_faders=bus,
            marker_resolver=_resolve_to(1),
        )
        joy = FakeJoystick(num_buttons=12, num_axes=6)
        joy.set_axis(1, -0.8)  # Y deflected; would-be stick-up
        handler.joysticks[0] = joy
        handler.capabilities[0] = ControllerCapabilities(backend="joystick")
        handler._bumper_state[0] = (False, False)
        handler._shoulder_axis_baselines[0] = {}
        handler.update(0.016)
        assert bus.calls == []

    def test_no_op_when_bus_absent(self, stubbed_pygame) -> None:
        # Handler constructed without a bus (legacy path). Even with
        # ``marker_fader_stick`` set, integrator must not crash on the missing
        # reference.
        cfg = ControllerConfig(marker_fader_stick="left_y")
        app = FakeApp(controller_cfg=cfg)
        handler = GamepadHandler(app)  # no virtual_faders kwarg
        joy = FakeJoystick(num_buttons=12, num_axes=6)
        joy.set_axis(1, -0.8)
        handler.joysticks[0] = joy
        handler.capabilities[0] = ControllerCapabilities(backend="joystick")
        handler._bumper_state[0] = (False, False)
        handler._shoulder_axis_baselines[0] = {}
        handler.update(0.016)  # must not raise

    def test_no_op_when_marker_resolver_absent(self, stubbed_pygame) -> None:
        # Bus + stick set but no marker_resolver wired (isolated handler
        # construction): the integrator can't know which marker to drive,
        # so it must no-op rather than guess.
        cfg = ControllerConfig(
            marker_fader_stick="left_y",
            marker_fader_max_speed_s=1.0,
        )
        bus = _FakeFaderBus()
        app = FakeApp(controller_cfg=cfg)
        handler = GamepadHandler(app, virtual_faders=bus)  # no resolver
        joy = FakeJoystick(num_buttons=12, num_axes=6)
        joy.set_axis(1, -0.8)
        handler.joysticks[0] = joy
        handler.capabilities[0] = ControllerCapabilities(backend="joystick")
        handler._bumper_state[0] = (False, False)
        handler._shoulder_axis_baselines[0] = {}
        handler.update(0.016)
        assert bus.calls == []

    def test_no_op_when_resolver_returns_none(self, stubbed_pygame) -> None:
        # Orphan / unbound pad: the resolver returns None (no marker for
        # this controller). The deflection is read but there's no marker
        # fader to drive, so nothing is enqueued.
        cfg = ControllerConfig(
            marker_fader_stick="left_y",
            marker_fader_max_speed_s=1.0,
        )
        bus = _FakeFaderBus()
        app = FakeApp(controller_cfg=cfg)
        handler = GamepadHandler(
            app,
            virtual_faders=bus,
            marker_resolver=_resolve_to(None),
        )
        joy = FakeJoystick(num_buttons=12, num_axes=6)
        joy.set_axis(1, -0.8)
        handler.joysticks[0] = joy
        handler.capabilities[0] = ControllerCapabilities(backend="joystick")
        handler._bumper_state[0] = (False, False)
        handler._shoulder_axis_baselines[0] = {}
        handler.update(0.016)
        assert bus.calls == []

    def test_left_y_stick_drives_resolved_marker_fader(
        self,
        stubbed_pygame,
    ) -> None:
        cfg = ControllerConfig(
            marker_fader_stick="left_y",
            marker_fader_max_speed_s=1.0,
        )
        bus = _FakeFaderBus()
        app = FakeApp(controller_cfg=cfg)
        handler = GamepadHandler(
            app,
            virtual_faders=bus,
            marker_resolver=_resolve_to(7),
        )
        joy = FakeJoystick(num_buttons=12, num_axes=6)
        # Stick Y is conventionally +1 = down; -0.8 = pushed up.
        joy.set_axis(1, -0.8)
        handler.joysticks[0] = joy
        handler.capabilities[0] = ControllerCapabilities(backend="joystick")
        handler._bumper_state[0] = (False, False)
        handler._shoulder_axis_baselines[0] = {}
        handler.update(0.016)
        assert len(bus.calls) == 1
        marker_id, delta = bus.calls[0]
        # Routed to the marker the resolver returned (not a fixed index).
        assert marker_id == 7
        # Stick up → positive delta (fader rises). Magnitude depends on
        # post-deadzone-post-curve; just assert sign + non-zero here.
        assert delta > 0

    def test_right_y_stick_drives_fader_one(self, stubbed_pygame) -> None:
        # On raw joysticks, right stick Y is axis index 3.
        cfg = ControllerConfig(marker_fader_stick="right_y", marker_fader_max_speed_s=1.0)
        bus = _FakeFaderBus()
        app = FakeApp(controller_cfg=cfg)
        handler = GamepadHandler(
            app,
            virtual_faders=bus,
            marker_resolver=_resolve_to(1),
        )
        joy = FakeJoystick(num_buttons=12, num_axes=6)
        joy.set_axis(3, 0.8)  # pushed down → negative delta
        handler.joysticks[0] = joy
        handler.capabilities[0] = ControllerCapabilities(backend="joystick")
        handler._bumper_state[0] = (False, False)
        handler._shoulder_axis_baselines[0] = {}
        handler.update(0.016)
        assert len(bus.calls) == 1
        _, delta = bus.calls[0]
        assert delta < 0

    def test_centered_stick_emits_no_fader_call(self, stubbed_pygame) -> None:
        # Inside deadzone → ``_apply_deadzone`` returns 0 → no fader
        # call (saves a no-op subscriber fan-out on the hot path).
        cfg = ControllerConfig(marker_fader_stick="left_y", marker_fader_max_speed_s=1.0)
        bus = _FakeFaderBus()
        app = FakeApp(controller_cfg=cfg)
        handler = GamepadHandler(
            app,
            virtual_faders=bus,
            marker_resolver=_resolve_to(1),
        )
        joy = FakeJoystick(num_buttons=12, num_axes=6)
        # Default 0.0 axis value sits squarely in the deadzone.
        handler.joysticks[0] = joy
        handler.capabilities[0] = ControllerCapabilities(backend="joystick")
        handler._bumper_state[0] = (False, False)
        handler._shoulder_axis_baselines[0] = {}
        handler.update(0.016)
        assert bus.calls == []

    def test_max_speed_scales_delta(self, stubbed_pygame) -> None:
        # ``marker_fader_max_speed_s = 0.5`` halves the time for full travel,
        # so the per-tick delta doubles compared to the 1.0 s baseline.
        cfg_slow = ControllerConfig(marker_fader_stick="left_y", marker_fader_max_speed_s=1.0)
        cfg_fast = ControllerConfig(marker_fader_stick="left_y", marker_fader_max_speed_s=0.5)
        slow_bus, fast_bus = _FakeFaderBus(), _FakeFaderBus()
        slow_handler = GamepadHandler(
            FakeApp(controller_cfg=cfg_slow),
            virtual_faders=slow_bus,
            marker_resolver=_resolve_to(1),
        )
        fast_handler = GamepadHandler(
            FakeApp(controller_cfg=cfg_fast),
            virtual_faders=fast_bus,
            marker_resolver=_resolve_to(1),
        )
        for handler in (slow_handler, fast_handler):
            joy = FakeJoystick(num_buttons=12, num_axes=6)
            joy.set_axis(1, -1.0)
            handler.joysticks[0] = joy
            handler.capabilities[0] = ControllerCapabilities(backend="joystick")
            handler._bumper_state[0] = (False, False)
            handler._shoulder_axis_baselines[0] = {}
            handler.update(0.016)
        slow_delta = slow_bus.calls[0][1]
        fast_delta = fast_bus.calls[0][1]
        assert fast_delta == pytest.approx(slow_delta * 2.0)

    def test_sdl2_controller_path_reads_left_y_axis(
        self,
        stubbed_pygame,
    ) -> None:
        # SDL2 controller path uses CONTROLLER_AXIS_LEFTY (= 1) at
        # 16-bit ints. Pushing up = -32768; bus delta should be > 0.
        cfg = ControllerConfig(marker_fader_stick="left_y", marker_fader_max_speed_s=1.0)
        bus = _FakeFaderBus()
        app = FakeApp(controller_cfg=cfg)
        handler = GamepadHandler(
            app,
            virtual_faders=bus,
            marker_resolver=_resolve_to(1),
        )
        joy = FakeJoystick(num_buttons=12, num_axes=6)
        controller = FakeController()
        controller.set_axis(CONTROLLER_AXIS_LEFTY, -26214)  # ~-0.8
        handler.joysticks[0] = joy
        handler.controllers[0] = controller
        handler.capabilities[0] = ControllerCapabilities(backend="controller")
        handler._bumper_state[0] = (False, False)
        handler._shoulder_axis_baselines[0] = {}
        handler.update(0.016)
        assert len(bus.calls) == 1
        assert bus.calls[0][1] > 0

    def test_joystick_with_too_few_axes_short_circuits(
        self,
        stubbed_pygame,
    ) -> None:
        # Cheap controllers may report fewer than 4 axes; right_y at
        # index 3 isn't present. Read must return 0.0 (no call) rather
        # than crash on an out-of-range axis read.
        cfg = ControllerConfig(marker_fader_stick="right_y", marker_fader_max_speed_s=1.0)
        bus = _FakeFaderBus()
        app = FakeApp(controller_cfg=cfg)
        handler = GamepadHandler(
            app,
            virtual_faders=bus,
            marker_resolver=_resolve_to(1),
        )
        joy = FakeJoystick(num_buttons=12, num_axes=2)  # only X, Y
        handler.joysticks[0] = joy
        handler.capabilities[0] = ControllerCapabilities(backend="joystick")
        handler._bumper_state[0] = (False, False)
        handler._shoulder_axis_baselines[0] = {}
        handler.update(0.016)
        assert bus.calls == []

    def test_pygame_error_during_axis_read_returns_zero(
        self,
        stubbed_pygame,
    ) -> None:
        cfg = ControllerConfig(marker_fader_stick="left_y", marker_fader_max_speed_s=1.0)
        bus = _FakeFaderBus()
        app = FakeApp(controller_cfg=cfg)
        handler = GamepadHandler(
            app,
            virtual_faders=bus,
            marker_resolver=_resolve_to(1),
        )

        class ExplodingController:
            quit_called = False

            def quit(self) -> None:
                pass

            def get_axis(self, axis: int) -> int:
                raise pygame.error("disconnected")

            def get_button(self, btn: int) -> int:
                return 0

        joy = FakeJoystick(num_buttons=12, num_axes=6)
        handler.joysticks[0] = joy
        # Cast through Any to bypass the structural protocol check
        # (we want this fake to satisfy the runtime read but not the
        # full protocol surface).
        handler.controllers[0] = ExplodingController()  # type: ignore[assignment]
        handler.capabilities[0] = ControllerCapabilities(backend="controller")
        handler._bumper_state[0] = (False, False)
        handler._shoulder_axis_baselines[0] = {}
        handler._axes_logged.add(0)  # skip axis-dump path
        # Movement read also raises (same controller); _read_axes will
        # log the warning and clean up. We still want the integrator
        # to not crash – _read_marker_fader_deflection has its own try/except.
        handler.update(0.016)
        assert bus.calls == []

    def test_apply_config_refreshes_cached_settings(
        self,
        stubbed_pygame,
    ) -> None:
        # Hot-reload path: operator changes ``marker_fader_stick`` mid-session.
        # The runtime ``apply_config`` re-caches so the next ``update``
        # uses the new value without restarting.
        cfg_off = ControllerConfig()
        bus = _FakeFaderBus()
        app = FakeApp(controller_cfg=cfg_off)
        handler = GamepadHandler(
            app,
            virtual_faders=bus,
            marker_resolver=_resolve_to(1),
        )
        # Operator picks a stick.
        app._config.controller = ControllerConfig(
            marker_fader_stick="left_y",
            marker_fader_max_speed_s=2.0,
        )
        handler.apply_config()
        assert handler._marker_fader_stick == "left_y"
        assert handler._marker_fader_max_speed_s == 2.0

    # The two tests below call ``_read_marker_fader_deflection`` directly
    # rather than going through ``update`` because the public path
    # shields the helper:
    #
    # - The "stick unset" early-return is unreachable from ``update``
    #   (the outer caller already guards on ``_marker_fader_stick`` before
    #   calling the helper).
    # - The pygame.error catch is unreachable because an exploding
    #   controller would raise inside ``_read_axes`` / ``_read_trigger_height``
    #   first, get caught by the outer try/except, and the controller
    #   would be removed before we ever reach the integrator.
    #
    # The defensive code stays in the helper so a future direct caller
    # (diagnostic probe, hot-reload sampler) gets the same fail-soft
    # semantics.

    def test_read_marker_fader_deflection_returns_zero_when_stick_unset(
        self,
        stubbed_pygame,
    ) -> None:
        handler, _ = make_handler(stubbed_pygame)
        # Default cfg leaves marker_fader_stick = "" – the helper bails early.
        joy = FakeJoystick(num_buttons=12, num_axes=6)
        joy.set_axis(1, -0.8)
        assert handler._read_marker_fader_deflection(0, joy) == 0.0

    def test_read_marker_fader_deflection_returns_zero_on_pygame_error(
        self,
        stubbed_pygame,
        caplog,
    ) -> None:
        cfg = ControllerConfig(marker_fader_stick="left_y", marker_fader_max_speed_s=1.0)
        bus = _FakeFaderBus()
        app = FakeApp(controller_cfg=cfg)
        handler = GamepadHandler(
            app,
            virtual_faders=bus,
            marker_resolver=_resolve_to(1),
        )

        class ExplodingController:
            def quit(self) -> None:
                pass

            def get_axis(self, axis: int) -> int:
                raise pygame.error("disconnected")

            def get_button(self, btn: int) -> int:
                return 0

        joy = FakeJoystick(num_buttons=12, num_axes=6)
        handler.controllers[0] = ExplodingController()  # type: ignore[assignment]
        with caplog.at_level("WARNING", logger="openfollow.input.gamepad"):
            result = handler._read_marker_fader_deflection(0, joy)
        assert result == 0.0
        assert any("Error reading marker-fader axis" in r.message for r in caplog.records)


# Runtime snapshot + calibration-mismatch (gamepad diagnostics)
# --------------------------------------------------------------------------- #


class TestRuntimeSnapshot:
    def test_empty_when_no_controllers(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)
        assert handler.runtime_snapshot() == []

    def test_reports_backend_layout_and_xinput(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)
        handler.capabilities[0] = ControllerCapabilities(
            backend="sdl2_controller",
            name="Xbox Wireless Controller",
            guid="030000005e040000",
            num_axes=6,
            num_buttons=11,
            num_hats=1,
        )
        snap = handler.runtime_snapshot()
        assert len(snap) == 1
        info = snap[0]
        assert isinstance(info, ControllerRuntimeInfo)
        assert info.index == 0
        assert info.backend == "sdl2_controller"
        assert info.is_game_controller is True
        assert (info.name, info.guid) == ("Xbox Wireless Controller", "030000005e040000")
        assert (info.num_axes, info.num_buttons, info.num_hats) == (6, 11, 1)
        # Default config has no calibration on file.
        assert info.calibration_stored is False
        assert info.matches_calibration is True

    def test_raw_joystick_backend_is_not_game_controller(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)
        handler.capabilities[0] = ControllerCapabilities(backend="joystick")
        assert handler.runtime_snapshot()[0].is_game_controller is False

    def test_ordered_by_index(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)
        handler.capabilities[2] = ControllerCapabilities(backend="joystick")
        handler.capabilities[0] = ControllerCapabilities(backend="joystick")
        assert [i.index for i in handler.runtime_snapshot()] == [0, 2]

    def test_matches_calibration_by_guid(self, stubbed_pygame) -> None:
        cfg = ControllerConfig()
        cfg.mapped_controller_guid = "abc123"
        handler, _ = make_handler(stubbed_pygame, config=cfg)
        handler.capabilities[0] = ControllerCapabilities(backend="joystick", guid="abc123")
        handler.capabilities[1] = ControllerCapabilities(backend="joystick", guid="zzz999")
        snap = {i.index: i for i in handler.runtime_snapshot()}
        assert snap[0].calibration_stored is True
        assert snap[0].matches_calibration is True
        assert snap[1].matches_calibration is False

    def test_matches_calibration_by_name_when_no_guid(self, stubbed_pygame) -> None:
        cfg = ControllerConfig()
        cfg.mapped_controller_name = "GameSir-G7 SE"
        handler, _ = make_handler(stubbed_pygame, config=cfg)
        handler.capabilities[0] = ControllerCapabilities(backend="joystick", name="GameSir-G7 SE")
        handler.capabilities[1] = ControllerCapabilities(backend="joystick", name="Other Pad")
        snap = {i.index: i for i in handler.runtime_snapshot()}
        assert snap[0].matches_calibration is True
        assert snap[1].matches_calibration is False

    def test_calibrated_by_raw_indices_only_cannot_mismatch(self, stubbed_pygame) -> None:
        cfg = ControllerConfig()
        cfg.button_raw_indices = {"A": 0, "RB": 7}
        handler, _ = make_handler(stubbed_pygame, config=cfg)
        handler.capabilities[0] = ControllerCapabilities(backend="joystick", guid="whatever")
        info = handler.runtime_snapshot()[0]
        assert info.calibration_stored is True
        # No identity recorded → can't tell, so don't claim a mismatch.
        assert info.matches_calibration is True

    def test_capabilities_populated_on_connect(self, stubbed_pygame) -> None:
        handler, _ = make_handler(stubbed_pygame)
        stubbed_pygame["state"]["factories"][0] = lambda idx: FakeJoystick(
            name="GameSir-G7 SE",
            guid="g7se-guid",
            num_axes=6,
            num_buttons=15,
            num_hats=1,
        )
        stubbed_pygame["state"]["count"] = 1
        handler._detect_controllers()
        cap = handler.capabilities[0]
        assert cap.name == "GameSir-G7 SE"
        assert cap.guid == "g7se-guid"
        assert cap.num_buttons == 15
        assert cap.num_hats == 1
        assert cap.backend == "joystick"

    def test_connect_warns_once_per_guid_on_mismatch(self, stubbed_pygame, caplog) -> None:
        import logging as _logging

        cfg = ControllerConfig()
        cfg.mapped_controller_guid = "calibrated-guid"
        handler, _ = make_handler(stubbed_pygame, config=cfg)
        stubbed_pygame["state"]["factories"][0] = lambda idx: FakeJoystick(
            name="Some Other Pad",
            guid="connected-guid",
        )
        stubbed_pygame["state"]["count"] = 1
        with caplog.at_level(_logging.WARNING, logger="openfollow.input.gamepad"):
            handler._detect_controllers()
            first = [r for r in caplog.records if "does not match the saved" in r.message]
            assert len(first) == 1
            # A hotplug re-detect of the same GUID must not re-warn.
            handler._detect_controllers(force=True)
            again = [r for r in caplog.records if "does not match the saved" in r.message]
            assert len(again) == 1

    def test_guidless_pad_warns_once_across_reconnects(self, stubbed_pygame, caplog) -> None:
        import logging as _logging

        cfg = ControllerConfig()
        cfg.mapped_controller_guid = "calibrated-guid"
        handler, _ = make_handler(stubbed_pygame, config=cfg)
        state = stubbed_pygame["state"]
        with caplog.at_level(_logging.WARNING, logger="openfollow.input.gamepad"):
            # The same GUID-less pad, reconnected: SDL gives it a new instance id each time.
            for instance_id in (0, 1, 2):
                state["factories"][0] = lambda idx, i=instance_id: FakeJoystick(
                    name="No-GUID Pad", guid="", instance_id=i
                )
                state["count"] = 1
                handler._detect_controllers()
                state["count"] = 0
                handler._detect_controllers()
        warnings = [r for r in caplog.records if "does not match the saved" in r.message]
        assert len(warnings) == 1

    def test_no_warn_when_identity_matches(self, stubbed_pygame, caplog) -> None:
        import logging as _logging

        cfg = ControllerConfig()
        cfg.mapped_controller_guid = "same-guid"
        handler, _ = make_handler(stubbed_pygame, config=cfg)
        stubbed_pygame["state"]["factories"][0] = lambda idx: FakeJoystick(guid="same-guid")
        stubbed_pygame["state"]["count"] = 1
        with caplog.at_level(_logging.WARNING, logger="openfollow.input.gamepad"):
            handler._detect_controllers()
        assert not [r for r in caplog.records if "does not match the saved" in r.message]

    def test_warn_helper_no_op_for_missing_index(self, stubbed_pygame) -> None:
        cfg = ControllerConfig()
        cfg.mapped_controller_guid = "x"
        handler, _ = make_handler(stubbed_pygame, config=cfg)
        # No capabilities entry for index 5 → must not raise.
        handler._warn_if_calibration_mismatch(5)
        assert handler._calibration_warned == set()

    def test_capability_getters_degrade_when_one_raises(self, stubbed_pygame) -> None:

        class BadGuidJoystick(FakeJoystick):
            def get_guid(self) -> str:
                raise RuntimeError("driver hiccup")

        handler, _ = make_handler(stubbed_pygame)
        stubbed_pygame["state"]["factories"][0] = lambda idx: BadGuidJoystick(
            name="Flaky Pad",
            num_buttons=11,
            num_axes=6,
            num_hats=1,
        )
        stubbed_pygame["state"]["count"] = 1
        handler._detect_controllers()
        cap = handler.capabilities[0]
        assert cap.name == "Flaky Pad"  # other getters still read
        assert cap.guid == ""  # bad getter degraded, didn't escape
        assert cap.num_buttons == 11

    def test_runtime_snapshot_is_an_independent_copy(self, stubbed_pygame) -> None:
        """The snapshot copies capabilities under the lock, so a concurrent
        mutation can't change size mid-iteration or alter the returned list."""
        handler, _ = make_handler(stubbed_pygame)
        handler.capabilities[0] = ControllerCapabilities(backend="joystick")
        snap = handler.runtime_snapshot()
        handler.capabilities[1] = ControllerCapabilities(backend="joystick")
        assert len(snap) == 1  # snapshot unaffected by the later mutation

    def test_apply_config_clears_warned_on_identity_change(self, stubbed_pygame) -> None:
        cfg = ControllerConfig()
        cfg.mapped_controller_guid = "cal-A"
        handler, app = make_handler(stubbed_pygame, config=cfg)
        handler.capabilities[0] = ControllerCapabilities(
            backend="joystick",
            guid="connected",
            name="Pad",
        )
        handler._warn_if_calibration_mismatch(0)
        assert handler._calibration_warned == {"connected"}

        app._config.controller.mapped_controller_guid = "cal-B"
        handler.apply_config()
        assert handler._calibration_warned == set()

    def test_apply_config_keeps_warned_when_identity_unchanged(self, stubbed_pygame) -> None:
        cfg = ControllerConfig()
        cfg.mapped_controller_guid = "cal-A"
        handler, _ = make_handler(stubbed_pygame, config=cfg)
        handler._calibration_warned.add("connected")
        handler.apply_config()  # identity unchanged
        assert handler._calibration_warned == {"connected"}

    def test_warn_dedup_falls_back_to_name_when_guid_empty(
        self,
        stubbed_pygame,
        caplog,
    ) -> None:
        import logging as _logging

        cfg = ControllerConfig()
        cfg.mapped_controller_name = "Calibrated Pad"
        handler, _ = make_handler(stubbed_pygame, config=cfg)
        handler.capabilities[0] = ControllerCapabilities(backend="joystick", guid="", name="Pad A")
        handler.capabilities[1] = ControllerCapabilities(backend="joystick", guid="", name="Pad B")
        with caplog.at_level(_logging.WARNING, logger="openfollow.input.gamepad"):
            handler._warn_if_calibration_mismatch(0)
            handler._warn_if_calibration_mismatch(1)
        warns = [r for r in caplog.records if "does not match the saved" in r.message]
        assert len(warns) == 2
        assert handler._calibration_warned == {"name:Pad A", "name:Pad B"}


class TestFrameButtonSnapshot:
    """Per-frame button-state snapshot. Each button read once per update()
    and shared between action edge-detectors and bus emitter."""

    def test_button_read_once_per_frame(self, stubbed_pygame) -> None:
        """``btn_reset`` (default X) is both an action binding *and* a button
        the bus emitter walks. With the frame snapshot, ``get_button`` is
        called exactly once for it per frame; without the snapshot the action
        edge-detector and the emitter would each read it (twice)."""

        class CountingController(FakeController):
            def __init__(self) -> None:
                super().__init__()
                self.read_counts: dict[int, int] = {}

            def get_button(self, btn: int) -> int:
                self.read_counts[btn] = self.read_counts.get(btn, 0) + 1
                return super().get_button(btn)

        bus = InputEventBus()
        seen: list[ButtonEvent] = []
        bus.subscribe_button(seen.append)
        app = FakeApp()
        handler = GamepadHandler(app, event_bus=bus)
        _attach_fake_joystick(handler)
        ctrl = CountingController()
        handler.controllers[0] = ctrl
        handler.capabilities[0] = ControllerCapabilities(
            backend="sdl2_controller",
        )
        ctrl.press(CONTROLLER_BUTTON_X)  # default btn_reset

        result = handler.update(0.016)

        # Dedup: the reset action and the bus emitter share the single read
        # of X, and no button is read from hardware more than once per frame.
        assert ctrl.read_counts[CONTROLLER_BUTTON_X] == 1
        assert max(ctrl.read_counts.values()) == 1
        # The single shared read still drives both consumers correctly.
        assert 0 in result.resets
        assert any(e.button == "X" and e.edge == "press" for e in seen)

    def test_snapshot_reset_after_update(self, stubbed_pygame) -> None:
        """The snapshot is torn down when the frame ends, so later direct
        reads (menu pollers, tests) see live hardware, not a stale cache."""
        app = FakeApp()
        handler = GamepadHandler(app, event_bus=InputEventBus())
        joy = _attach_fake_joystick(handler)
        handler.update(0.016)
        assert handler._frame_button_states is None
        # Direct reads outside a frame stay live across state changes.
        joy.press(CONTROLLER_BUTTON_X)
        assert handler._get_button(0, CONTROLLER_BUTTON_X) is True
        joy.release(CONTROLLER_BUTTON_X)
        assert handler._get_button(0, CONTROLLER_BUTTON_X) is False


# --------------------------------------------------------------------------- #
# Stick priming after detection (restart phantom guard)
# --------------------------------------------------------------------------- #


class TestStickPrimingAfterDetection:
    """A pad freshly opened after a service restart can report a stale axis
    value before the OS delivers its first centering event. That phantom must
    not slide the controlled marker into a corner: stick deflection is discarded
    until the stick reads at rest once, the same defence the trigger axes have.
    """

    @staticmethod
    def _detect(stubbed_pygame, joy, *, app=None, **kwargs) -> GamepadHandler:
        """Construct a handler that detects ``joy`` at index 0, so the pad goes
        through ``_detect_controllers`` and is marked unprimed (unlike the
        direct-injection helper used elsewhere, which defaults to primed)."""
        stubbed_pygame["state"]["count"] = 1
        stubbed_pygame["state"]["factories"][0] = lambda _idx: joy
        return GamepadHandler(app if app is not None else FakeApp(), **kwargs)

    def test_phantom_stick_does_not_move_marker_on_first_frame(self, stubbed_pygame) -> None:
        joy = FakeJoystick(num_buttons=12, num_axes=6)
        joy.set_axis(0, -0.9)  # phantom left-stick X reported at startup
        handler = self._detect(stubbed_pygame, joy)
        assert 0 in handler._stick_unprimed
        result = handler.update(0.016)
        assert result.movements == {}  # phantom discarded, marker stays put

    def test_stick_primes_on_rest_then_moves(self, stubbed_pygame) -> None:
        joy = FakeJoystick(num_buttons=12, num_axes=6)
        joy.set_axis(0, -0.9)
        app = FakeApp()
        handler = self._detect(stubbed_pygame, joy, app=app)
        assert handler.update(0.016).movements == {}  # phantom frame: nothing
        joy.set_axis(0, 0.0)  # stick returns to rest -> primes
        assert handler.update(0.016).movements == {}
        assert 0 not in handler._stick_unprimed
        joy.set_axis(0, -0.9)  # real deflection now honoured
        result = handler.update(0.016)
        assert 0 in result.movements
        dx, _dy, _dz = result.movements[0]
        assert dx == pytest.approx(app._config.marker.move_speed * handler._apply_deadzone(-0.9))

    def test_phantom_does_not_drive_marker_fader(self, stubbed_pygame) -> None:
        bus = _FakeFaderBus()
        cfg = ControllerConfig(marker_fader_stick="left_y", marker_fader_max_speed_s=1.0)
        app = FakeApp(controller_cfg=cfg)
        joy = FakeJoystick(num_buttons=12, num_axes=6)
        joy.set_axis(1, -0.8)  # phantom Y deflection
        handler = self._detect(
            stubbed_pygame,
            joy,
            app=app,
            virtual_faders=bus,
            marker_resolver=_resolve_to(1),
        )
        handler.update(0.016)
        assert bus.calls == []  # fader untouched until primed
        joy.set_axis(1, 0.0)  # center -> primes
        handler.update(0.016)
        joy.set_axis(1, -0.8)  # real deflection drives the fader
        handler.update(0.016)
        assert bus.calls and bus.calls[-1][0] == 1

    def test_directly_injected_pad_is_primed(self, stubbed_pygame) -> None:
        # Pads attached without going through detection (the existing test
        # harness path) are treated as primed, so first-frame movement works.
        handler, app = make_handler(stubbed_pygame)
        joy = FakeJoystick(num_buttons=12, num_axes=6)
        joy.set_axis(0, 0.9)
        handler.joysticks[0] = joy
        handler.capabilities[0] = ControllerCapabilities(backend="joystick")
        handler._bumper_state[0] = (False, False)
        handler._shoulder_axis_baselines[0] = {}
        assert 0 not in handler._stick_unprimed
        result = handler.update(0.016)
        assert 0 in result.movements

    def test_disconnect_drops_priming_state(self, stubbed_pygame) -> None:
        joy = FakeJoystick(num_buttons=12, num_axes=6)
        handler = self._detect(stubbed_pygame, joy)
        assert 0 in handler._stick_unprimed
        handler._cleanup_failed([0])
        assert 0 not in handler._stick_unprimed
