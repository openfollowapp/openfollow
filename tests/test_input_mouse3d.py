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
import openfollow.input.mouse3d as mouse3d_module
from openfollow.configuration import MOUSE3D_AXES, AppConfig, MarkerConfig, Mouse3DConfig
from openfollow.input.gamepad import GamepadUpdate
from openfollow.input.input_manager import InputManager
from openfollow.input.mouse3d import (
    Mouse3DDeviceInfo,
    Mouse3DHandler,
    Mouse3DManager,
    Mouse3DUpdate,
    check_mouse3d_dependencies,
)
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


def _cfg(**overrides) -> Mouse3DConfig:  # noqa: ANN003
    """Mouse3DConfig with neutral shaping (linear curve, sens 1.0, deadzone 0)
    so handler-logic tests isolate the behaviour they set from the tuned
    product defaults. Axis targets keep their defaults unless overridden."""
    params: dict[str, object] = {"enabled": True, "curve": "linear"}
    for axis in MOUSE3D_AXES:
        params[f"sens_{axis}"] = 1.0
        params[f"deadzone_{axis}"] = 0.0
    params.update(overrides)
    return Mouse3DConfig(**params)  # type: ignore[arg-type]


def _handler(cfg: Mouse3DConfig | None = None, *, snapshot=None) -> Mouse3DHandler:  # noqa: ANN001
    """Handler with no thread; optionally seed the consumer snapshot directly."""
    h = Mouse3DHandler(cfg if cfg is not None else _cfg(), device_factory=lambda: None)
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


def test_pitch_and_roll_default_to_none() -> None:
    # pitch/roll default to "none" -> no velocity contribution.
    h = _handler(snapshot=_state(pitch=1.0, roll=1.0))
    assert h.update(0.016).velocity == pytest.approx((0.0, 0.0, 0.0))


def test_yaw_defaults_to_speed() -> None:
    # yaw maps to "speed" by default: no velocity, but it ramps move-speed.
    h = _handler(snapshot=_state(yaw=1.0))
    out = h.update(1.0)
    assert out.velocity == pytest.approx((0.0, 0.0, 0.0))
    assert out.speed_steps != 0


def test_invert_flips_axis_sign() -> None:
    h = _handler(_cfg(invert_pan_x=True), snapshot=_state(x=1.0))
    assert h.update(0.016).velocity[0] == pytest.approx(-1.0)


def test_sensitivity_scales_velocity() -> None:
    h = _handler(_cfg(sens_pan_x=2.5), snapshot=_state(x=1.0))
    assert h.update(0.016).velocity[0] == pytest.approx(2.5)


def test_remapped_axis_target() -> None:
    # pan_x re-pointed at z instead of x.
    h = _handler(_cfg(map_pan_x="z"), snapshot=_state(x=1.0))
    assert h.update(0.016).velocity == pytest.approx((0.0, 0.0, 1.0))


def test_two_axes_summing_into_same_target() -> None:
    h = _handler(_cfg(map_pan_x="x", map_pan_y="x"), snapshot=_state(x=1.0, y=1.0))
    assert h.update(0.016).velocity[0] == pytest.approx(2.0)


# --------------------------------------------------------------------------- #
# Deadzone + curve shaping
# --------------------------------------------------------------------------- #


def test_deadzone_suppresses_small_deflection() -> None:
    h = _handler(_cfg(deadzone_pan_x=0.2), snapshot=_state(x=0.1))
    assert h.update(0.016).velocity[0] == 0.0


def test_deadzone_rescales_above_threshold() -> None:
    # At deadzone 0.5, an input of 0.75 maps to (0.75-0.5)/(1-0.5) = 0.5 (linear).
    h = _handler(_cfg(deadzone_pan_x=0.5), snapshot=_state(x=0.75))
    assert h.update(0.016).velocity[0] == pytest.approx(0.5)


def test_deadzone_is_per_axis() -> None:
    # A big deadzone on pan_x suppresses x; pan_y (its own deadzone) still moves.
    h = _handler(_cfg(deadzone_pan_x=0.9, deadzone_pan_y=0.0), snapshot=_state(x=0.5, y=0.5))
    vx, vy, _ = h.update(0.016).velocity
    assert vx == 0.0
    assert vy == pytest.approx(0.5)


def test_quadratic_curve_shapes_magnitude() -> None:
    # quadratic, no deadzone: 0.5 -> 0.25.
    h = _handler(_cfg(curve="quadratic"), snapshot=_state(x=0.5))
    assert h.update(0.016).velocity[0] == pytest.approx(0.25)


def test_s_law_curve_shapes_magnitude() -> None:
    # s-law, no deadzone: 0.25 -> 3*0.0625 - 2*0.015625 = 0.15625.
    h = _handler(_cfg(curve="s-law"), snapshot=_state(x=0.25))
    assert h.update(0.016).velocity[0] == pytest.approx(0.15625)


def test_full_deadzone_is_inert() -> None:
    # deadzone 1.0 must not divide by zero, even at full deflection.
    h = _handler(_cfg(deadzone_pan_x=1.0), snapshot=_state(x=1.0))
    assert h.update(0.016).velocity[0] == 0.0


# --------------------------------------------------------------------------- #
# speed-mapped axis
# --------------------------------------------------------------------------- #


def test_speed_axis_accumulates_into_steps() -> None:
    # pitch mapped to speed, full deflection. Rate is 6 steps/s; one big frame
    # (dt=1s) yields 6 steps; velocity stays zero (speed isn't movement).
    h = _handler(_cfg(map_pitch="speed"), snapshot=_state(pitch=1.0))
    out = h.update(1.0)
    assert out.velocity == pytest.approx((0.0, 0.0, 0.0))
    assert out.speed_steps == 6


def test_speed_axis_negative_direction() -> None:
    h = _handler(_cfg(map_pitch="speed"), snapshot=_state(pitch=-1.0))
    assert h.update(1.0).speed_steps == -6


def test_speed_axis_resets_accumulator_when_centered() -> None:
    # Below the axis deadzone -> shaped 0 -> no steps, accumulator cleared.
    h = _handler(_cfg(map_pitch="speed", deadzone_pitch=0.2), snapshot=_state(pitch=0.1))
    assert h.update(1.0).speed_steps == 0


def test_fader_axis_accumulates_into_signal() -> None:
    # A "fader"-mapped axis produces a unit fader_signal, no velocity.
    h = _handler(_cfg(map_pitch="fader"), snapshot=_state(pitch=1.0))
    out = h.update(0.016)
    assert out.velocity == pytest.approx((0.0, 0.0, 0.0))
    assert out.fader_signal == pytest.approx(1.0)
    assert out.speed_steps == 0


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


def test_button_tap_between_frames_still_fires() -> None:
    # The worker samples far faster than the frame rate; a press+release that
    # lands entirely between two frames must still fire (the worker latches the
    # rising edge) instead of being lost to level detection.
    h = _handler(_cfg(btn_next_marker=0))

    class _TapThenStop:
        def __init__(self, stop: object) -> None:
            self._stop = stop
            self.n = 0

        def read(self) -> object | None:
            self.n += 1
            if self.n == 1:
                return _state(buttons=[1])  # press
            if self.n == 2:
                return _state(buttons=[0])  # release – still before any frame samples
            self._stop.set()
            return None

        def close(self) -> None:
            pass

    h._pump(_TapThenStop(h._stop), h._stop)
    # The snapshot now reads released, but the tap was latched during the burst.
    assert h.update(0.016).next_marker is True
    # Draining is one-shot: a later frame with no new press doesn't re-fire.
    assert h.update(0.016).next_marker is False


def test_unbound_button_index_never_fires() -> None:
    # An explicitly unbound (-1) action never fires, whatever is pressed.
    h = _handler(_cfg(btn_toggle_zones=-1), snapshot=_state(buttons=[1, 1, 1, 1]))
    assert h.update(0.016).toggle_zones is False


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
    # neutral shaping (linear, deadzone 0) so the published 0.5 reads back as 0.5.
    h = Mouse3DHandler(_cfg(), device_factory=lambda: dev)
    h.start()
    try:
        assert _wait_until(lambda: h.connected and h._snapshot is not None)
        assert h.available is True
        assert h.latest_button() == 0
        # The published snapshot maps through update().
        assert _wait_until(lambda: h.update(0.016).velocity[0] == pytest.approx(0.5))
    finally:
        h.stop()
    # stop() is non-blocking (no join), so the worker closes the device
    # asynchronously as it exits.
    assert _wait_until(lambda: dev.closed)


def test_worker_marks_disconnected_when_no_device() -> None:
    h = Mouse3DHandler(Mouse3DConfig(enabled=True), device_factory=lambda: None)
    h.start()
    try:
        # Factory provided -> deps considered available; but no device opens.
        assert _wait_until(lambda: h.available and not h.connected)
    finally:
        h.stop()


def test_start_idempotent_while_running() -> None:
    h = Mouse3DHandler(Mouse3DConfig(enabled=True), device_factory=lambda: FakeDevice())
    h.start()
    try:
        first = h._thread
        h.start()  # thread already alive -> no-op
        assert h._thread is first
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
    h._run(h._stop)  # synchronous; returns immediately on the import failure
    assert h.available is False
    assert h._import_error is not None


def test_stop_clears_snapshot() -> None:
    # A stale deflection must not survive a stop()->start() (disable/enable),
    # otherwise re-enabling re-applies the last reading before a fresh one.
    h = _handler(snapshot=_state(x=0.8))
    assert h._snapshot is not None
    h.stop()
    assert h._snapshot is None


class _SpyThread:
    """Stand-in thread that records whether ``join`` was called."""

    def __init__(self) -> None:
        self.joined = False

    def is_alive(self) -> bool:
        return True

    def join(self, timeout: float | None = None) -> None:
        self.joined = True


def test_stop_is_nonblocking_by_default() -> None:
    # A live disable runs on the GTK main loop; stop() must not join the worker
    # (a blocked HID read would otherwise stall the HUD up to the join timeout).
    h = _handler()
    spy = _SpyThread()
    h._thread = spy  # type: ignore[assignment]
    h.stop()
    assert spy.joined is False
    assert h._thread is None
    assert h._stop.is_set()


def test_stop_wait_joins_for_shutdown() -> None:
    # App shutdown passes wait=True so the device is released before teardown.
    h = _handler()
    spy = _SpyThread()
    h._thread = spy  # type: ignore[assignment]
    h.stop(wait=True)
    assert spy.joined is True
    assert h._thread is None


def test_pump_does_not_publish_after_stop_requested() -> None:
    # If a stop arrives mid-read, the stopping worker must not write the reading
    # back (which would undo a non-blocking stop()'s snapshot-clear).
    h = _handler()

    class _StopDuringRead:
        def read(self):  # noqa: ANN202
            h._stop.set()  # stop requested during this read
            return _state(x=0.9)

        def close(self) -> None:
            pass

    assert h._pump(_StopDuringRead(), h._stop) is True  # it did read
    assert h._snapshot is None  # but did not publish the post-stop reading


def test_reconnect_does_not_replay_stale_deflection() -> None:
    # Device delivers one deflection then drops; on reconnect the worker reports
    # connected but, until a fresh HID report arrives, must serve NO stale input.
    class _OneReadThenDrop:
        def __init__(self) -> None:
            self.n = 0
            self.closed = False

        def read(self) -> object:
            self.n += 1
            if self.n == 1:
                return _state(x=0.8)
            raise OSError("unplugged")

        def close(self) -> None:
            self.closed = True

    class _Idle:
        """Connected but silent – a real puck only reports on change."""

        def __init__(self) -> None:
            self.closed = False

        def read(self) -> object | None:
            return None

        def close(self) -> None:
            self.closed = True

    seq: list[object] = [_OneReadThenDrop(), _Idle()]
    h = Mouse3DHandler(_cfg(), device_factory=lambda: seq.pop(0) if seq else _Idle())
    h.start()
    try:
        # Both devices opened: the flaky one dropped, the idle one reconnected.
        assert _wait_until(lambda: h.connected and not seq)
        # The idle device never publishes, so the stale 0.8 must not return.
        assert _wait_until(lambda: h._snapshot is None)
        assert h.update(0.016).velocity == (0.0, 0.0, 0.0)
    finally:
        h.stop()


def test_pump_reports_no_read_on_immediate_failure() -> None:
    # An open that errors on the first read returns read_any=False so the caller
    # backs off instead of tight-looping open->read-fail->reopen.
    class _Dead:
        def read(self) -> object:
            raise OSError("nope")

        def close(self) -> None:
            pass

    h = _handler()
    assert h._pump(_Dead(), h._stop) is False


def test_pump_reports_read_after_delivering_state() -> None:
    # A connection that delivers at least one reading returns read_any=True so
    # the caller resets the backoff for a prompt reconnect.
    h = _handler()

    class _OneThenStop:
        def __init__(self, stop: object) -> None:
            self._stop = stop
            self.n = 0

        def read(self) -> object | None:
            self.n += 1
            if self.n == 1:
                return _state(x=0.3)
            self._stop.set()  # end the pump loop after the first reading
            return None

        def close(self) -> None:
            pass

    assert h._pump(_OneThenStop(h._stop), h._stop) is True


def test_pump_recenters_latched_deflection_when_device_goes_silent(monkeypatch) -> None:  # noqa: ANN001
    # A 3D mouse reports only while deflected; if the return-to-center report is
    # dropped and the device falls silent, the worker must re-zero the latched
    # deflection so the marker can't glide with hands off the puck. Buttons are
    # kept (they're edge-triggered on the consumer side).
    monkeypatch.setattr(mouse3d_module, "_RECENTER_AFTER_IDLE_S", mouse3d_module._IDLE_POLL_S)
    h = _handler()

    class _DeflectThenSilent:
        def __init__(self, stop: object) -> None:
            self._stop = stop
            self.n = 0

        def read(self) -> object | None:
            self.n += 1
            if self.n == 1:
                return _state(x=0.9, buttons=[1])  # full +X deflection, button held
            if self.n >= 4:
                self._stop.set()  # end the loop after a few silent reads
            return None  # device silent: no return-to-center report arrived

        def close(self) -> None:
            pass

    h._pump(_DeflectThenSilent(h._stop), h._stop)
    assert h._snapshot is not None
    assert h._snapshot.is_centered()  # latched deflection re-zeroed
    assert h._snapshot.buttons == (1,)  # button state preserved


def test_concurrent_detect_is_serialized() -> None:
    # While one detect holds the lock, a second concurrent detect no-ops instead
    # of opening the singleton HID device a second time.
    h = Mouse3DHandler(
        Mouse3DConfig(enabled=False),
        device_factory=lambda: FakeDevice([_state(buttons=[1])]),
    )
    h._detect_lock.acquire()  # simulate a detect already in progress
    try:
        assert h.detect_pressed_button(timeout=0.05) is None
    finally:
        h._detect_lock.release()


def test_each_worker_gets_a_fresh_stop_event() -> None:
    # stop() sets the current worker's event; a later start() creates a new one,
    # leaving the old set so a straggler worker can't resume on a shared event.
    h = Mouse3DHandler(Mouse3DConfig(enabled=True), device_factory=lambda: FakeDevice())
    h.start()
    first_stop = h._stop
    h.stop()
    assert first_stop.is_set()
    h.start()
    try:
        assert h._stop is not first_stop
        assert first_stop.is_set()
        assert not h._stop.is_set()
    finally:
        h.stop()


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
        # No gamepads by default, so a connected 3D mouse is the *sole* controller
        # (single-controller mode -> follows the selected marker). Tests that want
        # a gamepad alongside set ``joysticks`` after construction.
        self.joysticks: dict = {}
        self.next_update = GamepadUpdate()

    def update(self, _dt: float) -> GamepadUpdate:
        return self.next_update

    def get_controller_info(self) -> list[dict]:
        return [
            {"controller_index": idx, "name": "Pad", "connected": True, "effective_speed": 0.0, "backend": "joystick"}
            for idx in sorted(self.joysticks)
        ]

    def stop(self) -> None:
        pass


class _FakeMouse3DManager:
    """Manager-boundary mock: N connected pucks, each returning a scripted update.

    ``connected`` toggles device presence (``False`` -> no live pucks). By default
    one puck is present; ``devices`` may be replaced to model several. ``update``
    fans the scripted ``next_update`` across every connected puck, mirroring the
    real per-device dict.
    """

    def __init__(self, config, *, backend=None) -> None:  # noqa: ANN001
        self.config = config
        self.next_update = Mouse3DUpdate()
        self.update_calls = 0
        self.started = False
        self.stopped = False
        self.reloaded_with = None
        self.connected = True  # at least one puck present
        self.devices = [Mouse3DDeviceInfo(path="/dev/hidraw0", product_name="SpaceNavigator")]

    def start(self) -> None:
        self.started = True

    def stop(self, *, wait: bool = False) -> None:
        self.stopped = True

    def reload_config(self, config) -> None:  # noqa: ANN001
        self.reloaded_with = config

    def connected_indices(self) -> list[int]:
        return list(range(len(self.devices))) if self.connected else []

    def connected_devices(self) -> list[Mouse3DDeviceInfo]:
        return list(self.devices) if self.connected else []

    def detect_pressed_button(self, timeout: float = 2.0) -> int | None:
        return None

    def update(self, _dt: float) -> dict[int, Mouse3DUpdate]:
        self.update_calls += 1
        return dict.fromkeys(self.connected_indices(), self.next_update)


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
    monkeypatch.setattr(input_manager_module, "Mouse3DManager", _FakeMouse3DManager)
    app = _DummyApp()
    manager = InputManager(app)
    return manager, app


def test_inputmanager_starts_mouse3d(wired) -> None:  # noqa: ANN001
    manager, _ = wired
    assert manager.mouse3d_manager.started is True


def test_disabled_mouse3d_does_not_move_or_consume(wired) -> None:  # noqa: ANN001
    manager, app = wired
    app._config.mouse3d.enabled = False
    manager.mouse3d_manager.next_update = Mouse3DUpdate(velocity=(1.0, 0.0, 0.0))
    manager.update(1.0)
    assert manager.mouse3d_manager.update_calls == 0
    assert app._server.get_marker(10).pos == pytest.approx((0.0, 0.0, 0.0))


def test_enabled_mouse3d_moves_selected_marker_scaled_by_speed(wired) -> None:  # noqa: ANN001
    manager, app = wired
    app._config.mouse3d.enabled = True
    manager.mouse3d_manager.next_update = Mouse3DUpdate(velocity=(1.0, 0.0, 0.0))
    manager.update(1.0)  # dt=1, move_speed=2.0 -> +2.0 in x on the selected marker
    assert app._server.get_marker(10).pos == pytest.approx((2.0, 0.0, 0.0))
    assert app._server.get_marker(11).pos == pytest.approx((0.0, 0.0, 0.0))


def test_enabled_mouse3d_reset_and_speed(wired) -> None:  # noqa: ANN001
    manager, app = wired
    app._config.mouse3d.enabled = True
    manager.mouse3d_manager.next_update = Mouse3DUpdate(reset=True, speed_steps=2)
    manager.update(0.016)
    assert app._server.get_marker(10).pos == pytest.approx((5.0, 6.0, 7.0))  # default pos
    assert app.move_speed_calls == [(1, 10), (1, 10)]


def test_mouse3d_buttons_fold_into_gamepad_result(wired) -> None:  # noqa: ANN001
    manager, app = wired
    app._config.mouse3d.enabled = True
    manager.mouse3d_manager.next_update = Mouse3DUpdate(next_marker=True, toggle_zones=True, toggle_help=True)
    result = manager.update(0.016)
    assert result.next_marker_pressed is True
    assert result.toggle_zones_pressed is True
    assert result.toggle_help_pressed is True


def test_get_controller_info_defaults_missing_gamepad_fields(wired) -> None:  # noqa: ANN001
    # ``slots`` and the gamepad info list are separate snapshots; a pad added to
    # ``joysticks`` between them appears in ``slots`` with no matching info dict.
    # The fallback must still carry every field the stats consumer reads
    # unconditionally, or the 4Hz stats tick KeyErrors.
    manager, _ = wired
    manager.gamepad_handler.joysticks = {0: object()}  # pad present in slots
    manager.gamepad_handler.get_controller_info = lambda: []  # ...but info missing
    info = manager.get_controller_info()
    assert len(info) == 1
    item = info[0]
    assert item["name"] == ""
    assert item["connected"] is False
    assert item["effective_speed"] == 0.0
    assert item["backend"] == ""
    # Consumable exactly as publish_runtime_stats does it (must not raise).
    float(item["effective_speed"])
    str(item["backend"])


def test_restart_mouse3d_enabled_reloads_and_starts(wired) -> None:  # noqa: ANN001
    manager, _ = wired
    new_cfg = Mouse3DConfig(enabled=True, sens_pan_x=3.0)
    manager.restart_mouse3d(new_cfg)
    assert manager.mouse3d_manager.reloaded_with is new_cfg
    assert manager.mouse3d_manager.started is True


def test_restart_mouse3d_disabled_stops(wired) -> None:  # noqa: ANN001
    manager, _ = wired
    manager.restart_mouse3d(Mouse3DConfig(enabled=False))
    assert manager.mouse3d_manager.stopped is True


# --------------------------------------------------------------------------- #
# Unified controller-id space (3D mouse counts like a gamepad)
# --------------------------------------------------------------------------- #


def test_mouse_and_gamepad_unified_ordering(wired) -> None:  # noqa: ANN001
    manager, app = wired
    app._config.mouse3d.enabled = True
    manager.gamepad_handler.joysticks = {0: object()}  # one gamepad beside the mouse
    # Mice first: mouse = unified 0 (c1) -> slot 0; gamepad = unified 1 (c2) -> slot 1.
    assert manager.controller_marker_id(0) == app._controlled_ids[0]  # mouse -> 10
    assert manager.controller_marker_id(1) == app._controlled_ids[1]  # gamepad -> 11
    info = manager.get_controller_info()
    assert info[0]["name"] == "3D Mouse"
    assert info[0]["controller_index"] == 0
    assert info[0]["marker_id"] == app._controlled_ids[0]
    assert info[1]["controller_index"] == 1
    assert info[1]["marker_id"] == app._controlled_ids[1]


def test_mouse_routes_to_its_slot_not_selected_in_multi_mode(wired) -> None:  # noqa: ANN001
    manager, app = wired
    app._config.mouse3d.enabled = True
    app._selected_id = 11  # selection differs from the mouse's slot 0 (marker 10)
    manager.gamepad_handler.joysticks = {0: object()}  # 2 controllers -> slot mode
    manager.mouse3d_manager.next_update = Mouse3DUpdate(velocity=(1.0, 0.0, 0.0))
    manager.update(1.0)
    assert app._server.get_marker(10).pos == pytest.approx((2.0, 0.0, 0.0))  # mouse's slot 0
    assert app._server.get_marker(11).pos == pytest.approx((0.0, 0.0, 0.0))  # selected, untouched


def test_mouse3d_fader_signal_drives_marker_fader(wired) -> None:  # noqa: ANN001
    manager, app = wired
    app._config.mouse3d.enabled = True  # sole controller -> selected marker (10)
    app._config.controller.marker_fader_max_speed_s = 2.0
    calls: list[tuple[int, float]] = []
    app._runtime_services._virtual_faders = SimpleNamespace(
        set_marker_fader_from_velocity_delta=lambda mid, delta: calls.append((mid, delta)),
    )
    manager.mouse3d_manager.next_update = Mouse3DUpdate(fader_signal=1.0)
    manager.update(1.0)  # delta = signal(1.0) * dt(1.0) / max_speed_s(2.0) = 0.5
    assert calls == [(10, pytest.approx(0.5))]


def test_mouse3d_fader_no_op_without_bus(wired) -> None:  # noqa: ANN001
    manager, app = wired
    app._config.mouse3d.enabled = True
    app._runtime_services._virtual_faders = None  # no fader bus wired
    manager.mouse3d_manager.next_update = Mouse3DUpdate(fader_signal=1.0)
    manager.update(1.0)  # no crash, nothing to drive


def test_mouse_cycling_suppressed_with_second_controller(wired) -> None:  # noqa: ANN001
    manager, app = wired
    app._config.mouse3d.enabled = True
    manager.gamepad_handler.joysticks = {0: object()}  # 2 controllers
    manager.mouse3d_manager.next_update = Mouse3DUpdate(next_marker=True, toggle_help=True)
    result = manager.update(0.016)
    assert result.next_marker_pressed is False  # cycling is a single-controller affordance
    assert result.toggle_help_pressed is True  # global toggles still fold


def test_disconnected_mouse_is_not_a_controller(wired) -> None:  # noqa: ANN001
    manager, app = wired
    app._config.mouse3d.enabled = True
    manager.mouse3d_manager.connected = False  # enabled but no device
    manager.gamepad_handler.joysticks = {0: object()}
    # Only the gamepad counts -> it takes unified index 0 (c1).
    info = manager.get_controller_info()
    assert [c["name"] for c in info] == ["Pad"]
    assert manager.controller_marker_id(0) == app._controlled_ids[0]


def test_controller_slots_runtime_error_falls_back(wired) -> None:  # noqa: ANN001
    manager, _ = wired

    class _Boom(dict):
        def __iter__(self):  # noqa: ANN204
            raise RuntimeError("joysticks rebuilt mid-iteration")

    manager.gamepad_handler.joysticks = _Boom()
    # sorted() raises -> fall back to no gamepads, no crash.
    assert manager._controller_slots() == []


def test_controller_marker_id_none_index(wired) -> None:  # noqa: ANN001
    manager, _ = wired
    assert manager._controller_marker_id(None) is None


def test_gamepad_unified_idx_absent_returns_none(wired) -> None:  # noqa: ANN001
    manager, _ = wired
    # No gamepad with pygame index 9 -> _gamepad_unified_idx returns None.
    assert manager._gamepad_marker_id(9) is None


def test_mouse3d_skips_when_marker_unregistered(wired) -> None:  # noqa: ANN001
    manager, app = wired
    app._config.mouse3d.enabled = True  # sole controller -> follows selected
    app._selected_id = 999  # not registered in the server
    manager.mouse3d_manager.next_update = Mouse3DUpdate(velocity=(1.0, 0.0, 0.0))
    manager.update(1.0)  # _get_marker(999) is None -> early return, no crash
    assert app._server.get_marker(10).pos == pytest.approx((0.0, 0.0, 0.0))


def test_mouse3d_prev_and_zones_fold_when_sole_controller(wired) -> None:  # noqa: ANN001
    manager, app = wired
    app._config.mouse3d.enabled = True  # sole controller (no gamepad)
    manager.mouse3d_manager.next_update = Mouse3DUpdate(prev_marker=True, toggle_zones=True)
    result = manager.update(0.016)
    assert result.prev_marker_pressed is True
    assert result.toggle_zones_pressed is True


def test_apply_mouse3d_swallows_handler_error(wired) -> None:  # noqa: ANN001
    manager, app = wired
    app._config.mouse3d.enabled = True

    def _boom(_dt):  # noqa: ANN001, ANN202
        raise RuntimeError("handler blew up")

    manager.mouse3d_manager.update = _boom
    manager.update(0.016)  # logged, not raised


def test_mouse3d_no_movement_when_disconnected(wired) -> None:  # noqa: ANN001
    manager, app = wired
    app._config.mouse3d.enabled = True
    manager.mouse3d_manager.connected = False  # not a live controller
    manager.gamepad_handler.joysticks = {0: object()}
    manager.mouse3d_manager.next_update = Mouse3DUpdate(velocity=(1.0, 0.0, 0.0))
    manager.update(1.0)  # _mouse3d_unified_idx is None -> no movement
    assert app._server.get_marker(10).pos == pytest.approx((0.0, 0.0, 0.0))


def test_marker_cycle_active_and_unified_idx_use_live_snapshot(wired) -> None:  # noqa: ANN001
    # The shared predicate + unified-index helpers default to a fresh
    # controller snapshot when called without one (the OSC seam / status paths).
    manager, app = wired
    app._config.mouse3d.enabled = True
    manager.mouse3d_manager.connected = True
    # Lone 3D mouse: one unified controller -> cycling active, mouse is slot 0.
    manager.gamepad_handler.joysticks = {}
    assert manager.marker_cycle_active() is True
    assert manager._mouse3d_unified_idx(0) == 0
    # Add a gamepad: two controllers -> cycling suppressed, gamepad is slot 1.
    manager.gamepad_handler.joysticks = {0: object()}
    assert manager.marker_cycle_active() is False
    assert manager._gamepad_unified_idx(0) == 1


# --------------------------------------------------------------------------- #
# Handler internals (dependency probe, axis coercion, device helpers, worker)
# --------------------------------------------------------------------------- #


def test_check_mouse3d_dependencies_present(monkeypatch) -> None:  # noqa: ANN001
    # Deterministic regardless of whether pyspacemouse is installed in the env.
    import openfollow.input.mouse3d as m3d_mod

    monkeypatch.setattr(m3d_mod.importlib.util, "find_spec", lambda _name: object())
    assert check_mouse3d_dependencies() == []


def test_check_mouse3d_dependencies_missing(monkeypatch) -> None:  # noqa: ANN001
    import openfollow.input.mouse3d as m3d_mod

    monkeypatch.setattr(m3d_mod.importlib.util, "find_spec", lambda _name: None)
    assert check_mouse3d_dependencies() == ["pyspacemouse"]


@pytest.mark.parametrize(
    "raw,expected",
    [(0.5, 0.5), (2.0, 1.0), (-2.0, -1.0), ("abc", 0.0), (float("inf"), 0.0), (float("nan"), 0.0), (None, 0.0)],
)
def test_finite_axis(raw, expected) -> None:  # noqa: ANN001
    from openfollow.input.mouse3d import _finite_axis

    assert _finite_axis(raw) == expected


def test_open_device_oserror_returns_none() -> None:
    def _boom():  # noqa: ANN202
        raise OSError("permission denied")

    assert Mouse3DHandler._open_device(_boom) is None


def test_open_device_falsey_returns_none() -> None:
    assert Mouse3DHandler._open_device(lambda: None) is None
    assert Mouse3DHandler._open_device(lambda: False) is None


def test_open_device_returns_device() -> None:
    dev = object()
    assert Mouse3DHandler._open_device(lambda: dev) is dev


def test_resolve_factory_returns_injected() -> None:
    def _factory():  # noqa: ANN202
        return None

    h = Mouse3DHandler(Mouse3DConfig(), device_factory=_factory)
    assert h._resolve_factory() is _factory


def test_resolve_factory_imports_pyspacemouse(monkeypatch) -> None:  # noqa: ANN001
    import sys
    import types

    fake = types.ModuleType("pyspacemouse")
    fake.open = lambda: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pyspacemouse", fake)
    h = Mouse3DHandler(Mouse3DConfig())  # no injected factory -> lazy import
    assert h._resolve_factory() is fake.open


def test_safe_close_no_close_attr() -> None:
    from openfollow.input.mouse3d import _safe_close

    _safe_close(object())  # no close attr -> no-op


def test_safe_close_swallows_error() -> None:
    from openfollow.input.mouse3d import _safe_close

    class _Dev:
        def close(self) -> None:
            raise RuntimeError("close blew up")

    _safe_close(_Dev())  # best-effort; swallowed


def test_pump_publishes_snapshot_then_exits() -> None:
    h = Mouse3DHandler(Mouse3DConfig(enabled=True), device_factory=lambda: None)

    class _Dev:
        def __init__(self) -> None:
            self.n = 0

        def read(self):  # noqa: ANN202
            self.n += 1
            if self.n == 1:
                return _state(x=0.5, buttons=[1])
            h._stop.set()  # end the loop on the idle pass
            return None

    assert h._pump(_Dev(), h._stop) is True  # synchronous; delivered a reading
    assert h._snapshot is not None
    assert h._snapshot.x == 0.5


def test_pump_returns_on_read_oserror() -> None:
    h = Mouse3DHandler(Mouse3DConfig(enabled=True), device_factory=lambda: None)

    class _Dev:
        def read(self):  # noqa: ANN202
            raise OSError("unplugged")

    assert h._pump(_Dev(), h._stop) is False  # returns without raising, no read
    assert h._snapshot is None


def test_pump_exits_immediately_when_already_stopped() -> None:
    h = Mouse3DHandler(Mouse3DConfig(enabled=True), device_factory=lambda: None)
    h._stop.set()  # already stopping -> the while condition is False on entry

    class _Dev:
        def read(self):  # noqa: ANN202
            raise AssertionError("read must not be called when already stopped")

    assert h._pump(_Dev(), h._stop) is False  # returns without reading
    assert h._snapshot is None


def test_run_reconnects_when_device_absent(monkeypatch) -> None:  # noqa: ANN001
    # Run the worker loop synchronously: open returns no device, so it backs off
    # and retries; stop after the second attempt exercises the reconnect branch.
    import openfollow.input.mouse3d as m3d_mod

    monkeypatch.setattr(m3d_mod, "_RECONNECT_MIN_S", 0.001)
    h = Mouse3DHandler(Mouse3DConfig(enabled=True))
    calls: list[int] = []

    def _factory():  # noqa: ANN202
        calls.append(1)
        if len(calls) >= 2:
            h._stop.set()  # end the loop after the 2nd open attempt
        return None  # no device present

    h._device_factory = _factory
    h._run(h._stop)
    assert len(calls) >= 2
    assert h.connected is False
    assert h.available is True


def test_run_opens_pumps_and_closes(monkeypatch) -> None:  # noqa: ANN001
    import openfollow.input.mouse3d as m3d_mod

    monkeypatch.setattr(m3d_mod, "_RECONNECT_MIN_S", 0.001)
    h = Mouse3DHandler(Mouse3DConfig(enabled=True))
    closed: list[int] = []

    class _Dev:
        def __init__(self) -> None:
            self.n = 0

        def read(self):  # noqa: ANN202
            self.n += 1
            if self.n == 1:
                return _state(x=0.5)
            h._stop.set()  # end the pump (and the run loop) on the idle pass
            return None

        def close(self) -> None:
            closed.append(1)

    h._device_factory = lambda: _Dev()
    h._run(h._stop)
    assert closed == [1]  # the finally block closed the device
    assert h.available is True
    # The connection ended, so the published reading is dropped – a reconnect
    # must not replay the last deflection.
    assert h._snapshot is None


def test_run_backs_off_when_open_succeeds_but_read_fails(monkeypatch) -> None:  # noqa: ANN001
    # An open that succeeds but whose read fails immediately must back off like a
    # failed open (read_any=False), not tight-loop open->read-fail->reopen.
    import openfollow.input.mouse3d as m3d_mod

    monkeypatch.setattr(m3d_mod, "_RECONNECT_MIN_S", 0.001)
    h = Mouse3DHandler(Mouse3DConfig(enabled=True))
    opens: list[int] = []

    class _DeadDev:
        def read(self):  # noqa: ANN202
            raise OSError("read fails immediately")

        def close(self) -> None:
            pass

    def _factory():  # noqa: ANN202
        opens.append(1)
        if len(opens) >= 2:
            h._stop.set()  # end the loop after the 2nd open attempt
        return _DeadDev()

    h._device_factory = _factory
    h._run(h._stop)
    assert len(opens) >= 2  # it retried (backed off) rather than giving up
    assert h.connected is False


def test_detect_snapshot_poll_times_out() -> None:
    dev = FakeDevice([_state(buttons=[0, 0])])  # connects, but no press
    h = Mouse3DHandler(Mouse3DConfig(enabled=True), device_factory=lambda: dev)
    h.start()
    try:
        assert _wait_until(lambda: h._snapshot is not None)
        assert h.detect_pressed_button(timeout=0.05) is None
    finally:
        h.stop()


def test_detect_device_none_returns_none() -> None:
    h = Mouse3DHandler(Mouse3DConfig(enabled=True), device_factory=lambda: None)
    assert h.detect_pressed_button(timeout=0.05) is None


def test_detect_read_oserror_returns_none() -> None:
    class _Dev:
        def read(self):  # noqa: ANN202
            raise OSError("unplugged mid-detect")

        def close(self) -> None:
            pass

    h = Mouse3DHandler(Mouse3DConfig(enabled=True), device_factory=lambda: _Dev())
    assert h.detect_pressed_button(timeout=0.5) is None


def test_detect_resolve_raises_returns_none(monkeypatch) -> None:  # noqa: ANN001
    h = Mouse3DHandler(Mouse3DConfig(enabled=True))

    def _boom():  # noqa: ANN202
        raise OSError("no libhidapi")

    monkeypatch.setattr(h, "_resolve_factory", _boom)
    assert h.detect_pressed_button(timeout=0.05) is None


# --------------------------------------------------------------------------- #
# Mouse3DManager: multiple pucks enumerated + read like gamepads
# --------------------------------------------------------------------------- #


class _FakeBackend:
    """Backend stub: ``enumerate`` returns a live (mutable) info list; ``open``
    resolves a per-path device factory and records the opened paths."""

    def __init__(self, infos, openers) -> None:  # noqa: ANN001
        self._infos = infos  # list[Mouse3DDeviceInfo]; tests mutate for hotplug
        self._openers = openers  # dict[path -> callable() -> device]
        self.opened: list[str] = []

    def enumerate(self):  # noqa: ANN201
        return list(self._infos)

    def open(self, path: str):  # noqa: ANN201
        self.opened.append(path)
        opener = self._openers.get(path)
        if opener is None:
            raise RuntimeError("no such device")
        return opener()


def _fast_manager(monkeypatch):  # noqa: ANN001, ANN201
    """Shrink the enumerate/reconnect cadences so manager tests settle quickly."""
    import openfollow.input.mouse3d as m3d_mod

    monkeypatch.setattr(m3d_mod, "_ENUMERATE_INTERVAL_S", 0.005)
    monkeypatch.setattr(m3d_mod, "_RECONNECT_MIN_S", 0.001)


def test_manager_enumerates_and_reads_two_pucks(monkeypatch) -> None:  # noqa: ANN001
    _fast_manager(monkeypatch)
    infos = [
        Mouse3DDeviceInfo(path="/dev/hidraw2", product_name="SpaceNavigator"),
        Mouse3DDeviceInfo(path="/dev/hidraw3", product_name="SpaceExplorer"),
    ]
    backend = _FakeBackend(
        infos,
        {
            "/dev/hidraw2": lambda: FakeDevice([_state(x=1.0)]),  # persists x -> pan_x
            "/dev/hidraw3": lambda: FakeDevice([_state(y=1.0)]),  # persists y -> pan_y
        },
    )
    mgr = Mouse3DManager(_cfg(enabled=True), backend=backend)
    mgr.start()
    try:
        assert _wait_until(lambda: mgr.connected_indices() == [0, 1])
        assert [d.product_name for d in mgr.connected_devices()] == ["SpaceNavigator", "SpaceExplorer"]
        # Wait until both handlers have published their first snapshot.
        assert _wait_until(lambda: all(u.velocity != (0.0, 0.0, 0.0) for u in mgr.update(1.0).values()))
        updates = mgr.update(1.0)
        assert set(updates) == {0, 1}
        assert updates[0].velocity[0] == pytest.approx(1.0)  # hidraw2 -> x axis
        assert updates[1].velocity[1] == pytest.approx(1.0)  # hidraw3 -> y axis
    finally:
        mgr.stop(wait=True)


def test_manager_dedups_by_path(monkeypatch) -> None:  # noqa: ANN001
    _fast_manager(monkeypatch)
    # easyhid reports the same puck once per HID collection: three entries, one node.
    infos = [Mouse3DDeviceInfo(path="/dev/hidraw2", product_name="SpaceNavigator")] * 3
    backend = _FakeBackend(infos, {"/dev/hidraw2": lambda: FakeDevice([_state(x=0.5)])})
    mgr = Mouse3DManager(_cfg(enabled=True), backend=backend)
    mgr.start()
    try:
        assert _wait_until(lambda: mgr.connected_indices() == [0])  # collapsed to one puck
        assert backend.opened.count("/dev/hidraw2") == 1  # opened exactly once
    finally:
        mgr.stop(wait=True)


def test_manager_hotplug_add_then_remove(monkeypatch) -> None:  # noqa: ANN001
    _fast_manager(monkeypatch)
    info2 = Mouse3DDeviceInfo(path="/dev/hidraw2", product_name="SpaceNavigator")
    info3 = Mouse3DDeviceInfo(path="/dev/hidraw3", product_name="SpaceExplorer")
    backend = _FakeBackend(
        [info2],
        {
            "/dev/hidraw2": lambda: FakeDevice([_state(x=0.5)]),
            "/dev/hidraw3": lambda: FakeDevice([_state(y=0.5)]),
        },
    )
    mgr = Mouse3DManager(_cfg(enabled=True), backend=backend)
    mgr.start()
    try:
        assert _wait_until(lambda: mgr.connected_indices() == [0])
        backend._infos.append(info3)  # plug a second puck
        assert _wait_until(lambda: mgr.connected_indices() == [0, 1])
        backend._infos[:] = [info3]  # unplug the first
        assert _wait_until(lambda: [d.path for d in mgr.connected_devices()] == ["/dev/hidraw3"])
    finally:
        mgr.stop(wait=True)


def test_manager_open_runtimeerror_does_not_kill_reconnect(monkeypatch) -> None:  # noqa: ANN001
    # pyspacemouse 2.x raises RuntimeError when a device isn't ready; the handler
    # must treat it as retryable (not crash the reconnect loop).
    _fast_manager(monkeypatch)
    opens = {"n": 0}

    def _opener():  # noqa: ANN202
        opens["n"] += 1
        if opens["n"] < 3:
            raise RuntimeError("No connected or supported devices found.")
        return FakeDevice([_state(x=0.5)])

    backend = _FakeBackend([Mouse3DDeviceInfo(path="/dev/hidraw2")], {"/dev/hidraw2": _opener})
    mgr = Mouse3DManager(_cfg(enabled=True), backend=backend)
    mgr.start()
    try:
        assert _wait_until(lambda: mgr.connected_indices() == [0], timeout=3.0)
        assert opens["n"] >= 3  # retried past the RuntimeErrors
    finally:
        mgr.stop(wait=True)


def test_manager_disabled_does_not_start(monkeypatch) -> None:  # noqa: ANN001
    _fast_manager(monkeypatch)
    backend = _FakeBackend([Mouse3DDeviceInfo(path="/dev/hidraw2")], {"/dev/hidraw2": lambda: FakeDevice()})
    mgr = Mouse3DManager(_cfg(enabled=False), backend=backend)
    mgr.start()  # no-op while disabled
    try:
        assert mgr.connected_indices() == []
        assert mgr.update(1.0) == {}
        assert backend.opened == []
    finally:
        mgr.stop(wait=True)


def test_reconcile_skips_start_when_stop_in_flight() -> None:
    # stop() sets _stop before taking the lock; a reconcile racing behind it must
    # not register or start handlers, or their workers would be orphaned (stop
    # already snapshotted the handler set and would never reap them).
    info = Mouse3DDeviceInfo(path="/dev/hidraw2", product_name="SpaceNavigator")
    backend = _FakeBackend([info], {"/dev/hidraw2": lambda: FakeDevice([_state(x=0.5)])})
    mgr = Mouse3DManager(_cfg(enabled=True), backend=backend)
    mgr._stop.set()  # a stop() is in flight
    mgr._reconcile(backend, [info])
    assert mgr._handlers == {}  # nothing registered, so nothing to orphan


def test_two_pucks_route_to_fixed_slots(wired) -> None:  # noqa: ANN001
    manager, app = wired
    app._config.mouse3d.enabled = True
    manager.mouse3d_manager.devices = [
        Mouse3DDeviceInfo(path="/dev/hidraw2", product_name="SpaceNavigator"),
        Mouse3DDeviceInfo(path="/dev/hidraw3", product_name="SpaceExplorer"),
    ]
    manager.mouse3d_manager.next_update = Mouse3DUpdate(velocity=(1.0, 0.0, 0.0))
    manager.update(1.0)
    # 2 pucks -> unified slots [mouse0, mouse1] -> controlled_ids[0]/[1] both move +2.
    assert app._server.get_marker(10).pos == pytest.approx((2.0, 0.0, 0.0))
    assert app._server.get_marker(11).pos == pytest.approx((2.0, 0.0, 0.0))


def test_two_pucks_controller_info_surfaces_each_device(wired) -> None:  # noqa: ANN001
    manager, app = wired
    app._config.mouse3d.enabled = True
    manager.mouse3d_manager.devices = [
        Mouse3DDeviceInfo(path="/dev/hidraw2", product_name="SpaceNavigator", serial="SN1"),
        Mouse3DDeviceInfo(path="/dev/hidraw3", product_name="SpaceExplorer", serial="SN2"),
    ]
    info = manager.get_controller_info()
    assert [c["name"] for c in info] == ["3D Mouse", "3D Mouse"]
    assert [c["controller_index"] for c in info] == [0, 1]
    assert [c["marker_id"] for c in info] == [10, 11]
    assert [c["product_name"] for c in info] == ["SpaceNavigator", "SpaceExplorer"]
    assert [c["serial"] for c in info] == ["SN1", "SN2"]


def test_two_pucks_suppress_cycling(wired) -> None:  # noqa: ANN001
    manager, app = wired
    app._config.mouse3d.enabled = True
    manager.mouse3d_manager.devices = [
        Mouse3DDeviceInfo(path="/dev/hidraw2"),
        Mouse3DDeviceInfo(path="/dev/hidraw3"),
    ]
    manager.mouse3d_manager.next_update = Mouse3DUpdate(next_marker=True, toggle_help=True)
    result = manager.update(0.016)
    assert result.next_marker_pressed is False  # 2 controllers -> cycling suppressed
    assert result.toggle_help_pressed is True  # global toggles still fold


def test_two_pucks_plus_gamepad_unified_order(wired) -> None:  # noqa: ANN001
    manager, app = wired
    app._config.mouse3d.enabled = True
    app._controlled_ids = [10, 11, 12]
    app._server.add_marker(12).set_pos(0.0, 0.0, 0.0)
    manager.mouse3d_manager.devices = [
        Mouse3DDeviceInfo(path="/dev/hidraw2"),
        Mouse3DDeviceInfo(path="/dev/hidraw3"),
    ]
    manager.gamepad_handler.joysticks = {0: object()}
    # Mice first: mouse0=c1->10, mouse1=c2->11, gamepad0=c3->12.
    assert manager.controller_marker_id(0) == 10
    assert manager.controller_marker_id(1) == 11
    assert manager.controller_marker_id(2) == 12
    assert [c["backend"] for c in manager.get_controller_info()] == ["mouse3d", "mouse3d", "joystick"]
