# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Controller slots through the InputManager: routing, missing slots, Forget, Identify, pins and Pin here."""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any

import pytest

import openfollow.input.input_manager as input_manager_module
from openfollow.configuration import AppConfig, SlotMarkerConfig, SlotPinConfig
from openfollow.input.controller_slots import NOTE_BUTTON_MAP_OTHER_MODEL, NOTE_CANNOT_IDENTIFY
from openfollow.input.gamepad import GamepadUpdate
from openfollow.input.input_manager import InputManager
from openfollow.input.mouse3d import Mouse3DDeviceInfo, Mouse3DUpdate
from openfollow.input.slot_pin_capture import CAPTURE_TIMEOUT_S, RELEASE_TIMEOUT_S, CaptureResult
from openfollow.operator_messages import OperatorMessageStore
from openfollow.psn import Marker
from openfollow.runtime import app_commands, app_modes
from openfollow.services import WebCommandQueue

pytestmark = pytest.mark.unit

_HOST = "platform/xhci-hcd.1"


class _Keyboard:
    def __init__(self, app: Any, *, event_bus: Any = None) -> None:
        self.keys: set[str] = set()
        self.cleared = 0

    def clear(self) -> None:
        self.cleared += 1

    def update(self, _dt: float) -> None:
        return None

    def is_connected(self) -> bool:
        return True


class _Pads:
    """Gamepads by SDL instance id: ``pads[id] = (port key, name)``, ``notes[id]`` what each can't do."""

    def __init__(self, app: Any, **_kwargs: Any) -> None:
        self.pads: dict[int, tuple[str | None, str]] = dict(_Pads.initial)
        self.notes: dict[int, tuple[str, ...]] = {}
        self.inputs: dict[int, float] = {}
        self.rumbles_ok: dict[int, bool] = {}
        self.identified: list[int] = []
        self.held: dict[int, frozenset[int]] = {}
        self.resyncs = 0

    initial: dict[int, tuple[str | None, str]] = {}

    def held_inputs(self) -> dict[int, frozenset[int]]:
        return {idx: self.held.get(idx, frozenset()) for idx in self.pads}

    def resync_edges(self) -> None:
        self.resyncs += 1

    @property
    def joysticks(self) -> dict[int, object]:
        return {idx: object() for idx in self.pads}

    def update(self, _dt: float) -> GamepadUpdate:
        return GamepadUpdate()

    def device_identities(self) -> dict[int, tuple[str | None, str, tuple[str, ...]]]:
        return {idx: (key, name, self.notes.get(idx, ())) for idx, (key, name) in self.pads.items()}

    def last_input(self) -> dict[int, float]:
        return dict(self.inputs)

    def identify(self, instance_id: int) -> bool:
        self.identified.append(instance_id)
        return self.rumbles_ok.get(instance_id, True)

    def get_controller_info(self) -> list[dict]:
        return [
            {"controller_index": idx, "name": name, "connected": True, "effective_speed": 1.5, "backend": "joystick"}
            for idx, (_key, name) in self.pads.items()
        ]

    def get_controller_effective_speeds(self) -> dict[int, float]:
        return dict.fromkeys(self.pads, 1.5)

    def stop(self) -> None:
        pass


class _Pucks:
    """3D mice by instance id."""

    settled_at_start = True
    initial: dict[int, Mouse3DDeviceInfo] = {}

    def __init__(self, config: Any, *, backend: Any = None) -> None:
        self.devices: dict[int, Mouse3DDeviceInfo] = dict(self.initial)
        self.settled = self.settled_at_start
        self.inputs: dict[int, float] = {}
        self.leds_ok = True
        self.without_led: set[int] = set()
        self.identified: list[int] = []
        self.presses: dict[int, set[int]] = {}
        self.held: dict[int, frozenset[int]] = {}

    def take_presses(self) -> dict[int, set[int]]:
        presses = {idx: set(self.presses.get(idx, ())) for idx in self.devices}
        self.presses = {}
        return presses

    def held_buttons(self) -> dict[int, frozenset[int]]:
        return {idx: self.held.get(idx, frozenset()) for idx in self.devices}

    def start(self) -> None:
        pass

    def stop(self, *, wait: bool = False) -> None:
        pass

    def reload_config(self, config: Any) -> None:
        pass

    def connected_devices(self) -> dict[int, Mouse3DDeviceInfo]:
        return dict(self.devices)

    def initial_scan_settled(self) -> bool:
        return self.settled

    def led_ids(self) -> set[int]:
        return set(self.devices) - self.without_led

    def last_input(self) -> dict[int, float]:
        return dict(self.inputs)

    def identify(self, instance_id: int) -> bool:
        self.identified.append(instance_id)
        return self.leds_ok

    def update(self, _dt: float) -> dict[int, Mouse3DUpdate]:
        return {}


class _Server:
    def __init__(self) -> None:
        self.markers: dict[int, Marker] = {}

    def add_marker(self, marker_id: int) -> Marker:
        marker = Marker(marker_id, f"M{marker_id}")
        self.markers[marker_id] = marker
        return marker

    def get_marker(self, marker_id: int) -> Marker | None:
        return self.markers.get(marker_id)


class _App:
    def __init__(self) -> None:
        self._config = AppConfig()
        self._config.osc.enabled = False
        self._controlled_ids = [10, 11, 12]
        self._selected_id: int | None = 10
        self._server = _Server()
        for marker_id in self._controlled_ids:
            self._server.add_marker(marker_id)
        self._assist_manual: dict[int, Marker] = {}
        self._runtime_services = SimpleNamespace(_virtual_faders=None, _operator_message_store=OperatorMessageStore())

    def get_marker_move_speed(self, _marker_id: int) -> float:
        return 2.0

    def _get_default_marker_position(self) -> tuple[float, float, float]:
        return (0.0, 0.0, 0.0)


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def station(monkeypatch):
    monkeypatch.setattr(input_manager_module, "KeyboardHandler", _Keyboard)
    monkeypatch.setattr(input_manager_module, "GamepadHandler", _Pads)
    monkeypatch.setattr(input_manager_module, "Mouse3DManager", _Pucks)
    monkeypatch.setattr(input_manager_module, "usb_host_paths", lambda: (_HOST,))
    _Pads.initial = {}

    def build(pads: dict[int, tuple[str | None, str]] | None = None, **config: Any) -> tuple[InputManager, _App]:
        _Pads.initial = dict(pads or {})
        app = _App()
        for field, value in config.items():
            section, _, name = field.partition("__")
            setattr(getattr(app._config, section), name, value)
        return InputManager(app), app

    return build


def _key(port: str) -> str:
    return f"usb:{_HOST}:{port}"


def _states(manager: InputManager) -> list[tuple[str, int | None]]:
    return [(c["state"], c["marker_id"]) for c in manager.get_controller_info()]


def test_startup_order_follows_the_sockets(station) -> None:
    manager, _ = station({0: (_key("2"), "Right"), 1: (_key("1"), "Left")})
    assert [c["name"] for c in manager.get_controller_info()] == ["Left", "Right"]
    assert manager.controller_marker_id(0) == 10
    assert manager.controller_marker_id(1) == 11


def test_a_departed_pad_leaves_its_slot_missing_and_nobody_moves(station) -> None:
    manager, _ = station({0: (_key("1"), "First"), 1: (_key("2"), "Second")})
    del manager.gamepad_handler.pads[0]
    manager.update(0.016)
    assert _states(manager) == [("missing", 10), ("connected", 11)]
    # The missing slot still maps its marker, so OSC ``c1`` keeps sending it.
    assert manager.controller_marker_id(0) == 10
    # One pad left but two slots: it keeps its fixed marker and cannot cycle.
    assert manager._gamepad_marker_id(1) == 11
    assert manager.marker_cycle_active() is False
    info = manager.get_controller_info()[0]
    assert (info["name"], info["connected"], info["port_label"]) == ("First", False, "USB 1, port 1")


def test_a_replugged_pad_reclaims_its_slot(station) -> None:
    manager, _ = station({0: (_key("1"), "First"), 1: (_key("2"), "Second")})
    del manager.gamepad_handler.pads[0]
    manager.update(0.016)
    manager.gamepad_handler.pads[7] = (_key("1"), "First")
    manager.update(0.016)
    assert _states(manager) == [("connected", 10), ("connected", 11)]
    assert manager._gamepad_marker_id(7) == 10


def test_forget_silences_a_missing_slot_and_releases_its_marker(station, caplog) -> None:
    manager, _ = station({0: (_key("1"), "First"), 1: (_key("2"), "Second")})
    del manager.gamepad_handler.pads[0]
    manager.update(0.016)
    with caplog.at_level("INFO", logger=input_manager_module.__name__):
        assert manager.forget_slot(0) is True
    assert "C1 forgotten" in caplog.text
    assert _states(manager) == [("reserved", None), ("connected", 11)]
    assert manager.controller_marker_id(0) is None
    # Still a slot: the pad behind it keeps its marker.
    assert manager._gamepad_marker_id(1) == 11
    manager.gamepad_handler.pads[9] = (_key("5"), "Spare")
    manager.update(0.016)
    assert _states(manager) == [("connected", 10), ("connected", 11)]


def test_forget_refuses_a_connected_slot(station) -> None:
    manager, _ = station({0: (_key("1"), "First")})
    assert manager.forget_slot(0) is False
    assert _states(manager) == [("connected", 10)]


def test_on_a_single_slot_station_a_puck_takes_over_from_a_pad(station) -> None:
    manager, app = station({0: (_key("1"), "Pad")}, mouse3d__enabled=True)
    del manager.gamepad_handler.pads[0]
    manager.update(0.016)
    assert _states(manager) == [("missing", app._selected_id)]
    manager.mouse3d_manager.devices[0] = Mouse3DDeviceInfo(path="/dev/hidraw0", product_name="SpaceNavigator")
    manager.update(0.016)
    info = manager.get_controller_info()
    assert [(c["kind"], c["state"]) for c in info] == [("mouse3d", "connected")]


def _slots(manager: InputManager) -> list[tuple[str, str, int | None]]:
    return [(c["kind"], c["state"], c["marker_id"]) for c in manager.get_controller_info()]


def test_switching_gamepads_off_leaves_their_slots_missing_and_nobody_moves(station) -> None:
    manager, app = station({0: (_key("1"), "First"), 1: (_key("2"), "Second")}, mouse3d__enabled=True)
    manager.mouse3d_manager.devices[0] = Mouse3DDeviceInfo(path="/dev/hidraw0", port_key=_key("3"))
    manager.update(0.016)
    app._config.controller.enabled = False
    manager.update(0.016)
    assert _slots(manager) == [("gamepad", "missing", 10), ("gamepad", "missing", 11), ("mouse3d", "connected", 12)]
    app._config.controller.enabled = True
    manager.update(0.016)
    assert _slots(manager) == [
        ("gamepad", "connected", 10),
        ("gamepad", "connected", 11),
        ("mouse3d", "connected", 12),
    ]


def test_switching_the_3d_mouse_off_keeps_a_missing_pad_and_the_pad_behind_it(station, monkeypatch) -> None:
    monkeypatch.setattr(_Pucks, "initial", {0: Mouse3DDeviceInfo(path="/dev/hidraw0", port_key=_key("1"))})
    manager, app = station({0: (_key("2"), "First"), 1: (_key("3"), "Second")}, mouse3d__enabled=True)
    assert [c["kind"] for c in manager.get_controller_info()] == ["mouse3d", "gamepad", "gamepad"]
    del manager.gamepad_handler.pads[0]
    manager.update(0.016)
    app._config.mouse3d.enabled = False
    manager.update(0.016)
    assert _slots(manager) == [("mouse3d", "missing", 10), ("gamepad", "missing", 11), ("gamepad", "connected", 12)]
    app._config.mouse3d.enabled = True
    manager.update(0.016)
    assert _slots(manager) == [("mouse3d", "connected", 10), ("gamepad", "missing", 11), ("gamepad", "connected", 12)]


def test_slots_wait_for_the_3d_mouse_scan_before_freezing(station, monkeypatch) -> None:
    monkeypatch.setattr(_Pucks, "settled_at_start", False)
    manager, _ = station({0: (_key("2"), "Pad")}, mouse3d__enabled=True)
    manager.update(0.016)
    manager.mouse3d_manager.devices[4] = Mouse3DDeviceInfo(path="/dev/hidraw0", port_key=_key("1"))
    manager.mouse3d_manager.settled = True
    manager.update(0.016)
    # The puck is in the lower socket, so it seeds first.
    assert [c["kind"] for c in manager.get_controller_info()] == ["mouse3d", "gamepad"]


def test_identify_pulses_the_device_and_flashes_its_card(station) -> None:
    manager, _ = station({0: (_key("1"), "First"), 1: (_key("2"), "Second")})
    clock = _Clock()
    manager._clock = clock
    manager.gamepad_handler.rumbles_ok[1] = False
    assert manager.identify_slot(1) is False
    assert manager.gamepad_handler.identified == [1]
    # Lit and dark in quarter-second turns, for three seconds.
    lit = []
    for step in range(13):
        clock.now = 1000.0 + step * 0.25
        lit.append(manager.identify_flash_marker())
    assert lit == [11, None] * 6 + [None]
    assert manager.identify_slot(0) is True


def test_identify_blinks_a_puck(station) -> None:
    manager, _ = station({}, mouse3d__enabled=True)
    manager.mouse3d_manager.devices[3] = Mouse3DDeviceInfo(path="/dev/hidraw0")
    manager.update(0.016)
    assert manager.identify_slot(0) is True
    assert manager.mouse3d_manager.identified == [3]


def test_identify_on_a_missing_slot_only_flashes_the_card(station) -> None:
    manager, _ = station({0: (_key("1"), "First"), 1: (_key("2"), "Second")})
    del manager.gamepad_handler.pads[0]
    manager.update(0.016)
    assert manager.identify_slot(0) is False
    assert manager.gamepad_handler.identified == []
    assert manager.identify_flash_marker() == 10


def test_each_slot_reports_what_its_pad_cannot_do(station) -> None:
    manager, _ = station({0: (_key("1"), "First"), 1: (_key("2"), "Second")})
    manager.gamepad_handler.notes[0] = (NOTE_CANNOT_IDENTIFY,)
    manager.update(0.016)
    assert [c["notes"] for c in manager.get_controller_info()] == [[NOTE_CANNOT_IDENTIFY], []]
    manager.gamepad_handler.notes[1] = (NOTE_BUTTON_MAP_OTHER_MODEL,)
    manager.update(0.016)
    notes = [c["notes"] for c in manager.get_controller_info()]
    assert notes == [[NOTE_CANNOT_IDENTIFY], [NOTE_BUTTON_MAP_OTHER_MODEL]]


def test_a_puck_without_an_led_cannot_be_identified(station) -> None:
    manager, _ = station({}, mouse3d__enabled=True)
    manager.mouse3d_manager.devices = {
        2: Mouse3DDeviceInfo(path="/dev/hidraw0"),
        3: Mouse3DDeviceInfo(path="/dev/hidraw1"),
    }
    manager.mouse3d_manager.without_led = {3}
    manager.update(0.016)
    assert [c["notes"] for c in manager.get_controller_info()] == [[], [NOTE_CANNOT_IDENTIFY]]


def test_a_missing_slot_keeps_its_notes_and_a_reserved_one_raises_none(station) -> None:
    manager, _ = station({0: (_key("1"), "First"), 1: (_key("2"), "Second")})
    manager.gamepad_handler.notes[0] = (NOTE_CANNOT_IDENTIFY,)
    manager.update(0.016)
    del manager.gamepad_handler.pads[0]
    manager.update(0.016)
    first = manager.get_controller_info()[0]
    assert (first["state"], first["notes"]) == ("missing", [NOTE_CANNOT_IDENTIFY])
    assert manager.forget_slot(0)
    first = manager.get_controller_info()[0]
    assert (first["state"], first["notes"]) == ("reserved", [])


def test_a_slot_ref_names_what_the_slot_holds_now(station) -> None:
    manager, _ = station({0: (_key("1"), "First"), 1: (_key("2"), "Second")})
    before = manager.slot_ref(0)
    assert before == manager.get_controller_info()[0]["slot_ref"]
    assert before != manager.slot_ref(1)
    del manager.gamepad_handler.pads[0]
    manager.update(0.016)
    assert manager.slot_ref(0) != before
    assert manager.slot_ref(0) == manager.get_controller_info()[0]["slot_ref"]


@pytest.mark.parametrize("index", [-1, 2])
def test_no_slot_has_no_ref(station, index: int) -> None:
    manager, _ = station({0: (_key("1"), "First"), 1: (_key("2"), "Second")})
    assert manager.slot_ref(index) is None


@pytest.mark.parametrize("index", [-1, 5])
def test_identify_on_no_slot_does_nothing(station, index: int) -> None:
    manager, _ = station({0: (_key("1"), "First")})
    assert manager.identify_slot(index) is False
    assert manager.identify_flash_marker() is None


def test_controller_info_reports_recent_use(station) -> None:
    manager, _ = station({0: (_key("1"), "First"), 1: (_key("2"), "Second")}, mouse3d__enabled=True)
    manager.mouse3d_manager.devices[2] = Mouse3DDeviceInfo(
        path="/dev/hidraw0", product_name="SpaceNavigator", serial="S1"
    )
    manager.update(0.016)
    clock = _Clock()
    manager._clock = clock
    manager.gamepad_handler.inputs[0] = clock.now - 0.4
    manager.mouse3d_manager.inputs[2] = clock.now - 5.0
    info = manager.get_controller_info()
    assert [c["seconds_since_input"] for c in info] == [pytest.approx(0.4), None, pytest.approx(5.0)]
    assert (info[2]["product_name"], info[2]["serial"], info[2]["effective_speed"]) == ("SpaceNavigator", "S1", 2.0)
    assert (info[0]["backend"], info[0]["effective_speed"]) == ("joystick", 1.5)


def test_slot_changes_are_logged_with_their_sockets(station, caplog) -> None:
    with caplog.at_level("INFO", logger=input_manager_module.__name__):
        manager, _ = station({0: (_key("1"), "GameSir"), 1: (None, "Wireless")})
    assert "Controller slots: C1 GameSir (USB 1, port 1), C2 Wireless (no stable port)" in caplog.text
    caplog.clear()
    with caplog.at_level("INFO", logger=input_manager_module.__name__):
        manager.update(0.016)
    assert "Controller slots" not in caplog.text


# -- pins ----------------------------------------------------------------------


def _pin(slot: int, port: str, label: str = "Pad") -> SlotPinConfig:
    return SlotPinConfig(slot=slot, key=_key(port), kind="gamepad", label=label)


def _names(manager: InputManager) -> list[tuple[str, bool]]:
    return [(c["name"], c["pinned"]) for c in manager.get_controller_info()]


def test_a_pinned_socket_holds_its_slot_and_its_marker_from_startup(station) -> None:
    manager, _ = station(
        {0: (_key("1"), "Left"), 1: (_key("2"), "Right")}, controller__slot_pins=[_pin(1, "2", "Right")]
    )
    assert _names(manager) == [("Right", True), ("Left", False)]
    assert manager._gamepad_marker_id(1) == 10
    assert manager.slot_pins() == [_pin(1, "2", "Right")]


def test_pinning_moves_the_pad_and_its_marker(station, caplog) -> None:
    manager, _ = station({0: (_key("1"), "Left"), 1: (_key("2"), "Right")})
    with caplog.at_level("INFO", logger=input_manager_module.__name__):
        assert manager.pin_slot(0, "gamepad", 1) is True
    assert "C1 pinned to USB 1, port 2" in caplog.text
    assert _names(manager) == [("Right", True), ("Left", False)]
    assert manager._gamepad_marker_id(1) == 10
    assert manager._gamepad_marker_id(0) == 11
    assert manager.slot_pins() == [_pin(1, "2", "Right")]


def test_a_pad_with_no_stable_port_cannot_be_pinned(station) -> None:
    manager, _ = station({0: (None, "Pad")})
    assert manager.pin_slot(0, "gamepad", 0) is False
    assert manager.slot_pins() == []


def test_unpinning_keeps_every_pad_where_it_is(station, caplog) -> None:
    manager, _ = station(
        {0: (_key("1"), "Left"), 1: (_key("2"), "Right")}, controller__slot_pins=[_pin(1, "2", "Right")]
    )
    with caplog.at_level("INFO", logger=input_manager_module.__name__):
        assert manager.unpin_slot(0) is True
    assert "C1 unpinned" in caplog.text
    assert _names(manager) == [("Right", False), ("Left", False)]
    assert manager.slot_pins() == []
    assert manager.unpin_slot(0) is False


def test_a_lone_pad_pinned_above_the_first_slot_still_follows_the_selection(station) -> None:
    manager, app = station({0: (_key("1"), "Solo")}, controller__slot_pins=[_pin(3, "1", "Solo")])
    assert _states(manager) == [("empty", None), ("empty", None), ("connected", app._selected_id)]
    assert [c["all_markers"] for c in manager.get_controller_info()] == [False, False, True]
    assert manager.controller_marker_id(0) is None
    assert manager._gamepad_marker_id(0) == app._selected_id
    assert manager.marker_cycle_active() is True


def test_hand_edited_pins_move_their_pads(station, caplog) -> None:
    manager, _ = station({0: (_key("1"), "Left"), 1: (_key("2"), "Right")})
    with caplog.at_level("INFO", logger=input_manager_module.__name__):
        manager.apply_slot_pins([_pin(1, "2", "Right")])
    assert "pins changed" in caplog.text
    assert _names(manager) == [("Right", True), ("Left", False)]
    caplog.clear()
    with caplog.at_level("INFO", logger=input_manager_module.__name__):
        manager.apply_slot_pins([_pin(1, "2", "Right")])
    assert "pins changed" not in caplog.text


def _armed(station, pads, slot: int = 0, **config: Any) -> tuple[InputManager, _App, _Clock]:
    manager, app = station(pads, **config)
    clock = _Clock()
    manager._clock = clock
    assert manager.start_pin_capture(slot) is True
    return manager, app, clock


def test_the_first_press_pins_its_socket_once_released(station) -> None:
    manager, _, clock = _armed(station, {0: (_key("1"), "Left"), 1: (_key("2"), "Right")})
    assert manager.pin_capture_active is True
    manager.gamepad_handler.held = {1: frozenset({3})}
    assert manager.poll_pin_capture() is None
    # Nothing moves until the press is let go, so it reaches no other consumer.
    assert _names(manager) == [("Left", False), ("Right", False)]
    manager.gamepad_handler.held = {}
    clock.now += 0.1
    assert manager.poll_pin_capture() == CaptureResult(0, "pinned", "USB 1, port 2")
    assert manager.pin_capture_active is False
    assert _names(manager) == [("Right", True), ("Left", False)]
    assert manager.slot_pins() == [_pin(1, "2", "Right")]
    assert manager.gamepad_handler.resyncs == 1


def test_a_press_held_past_the_release_window_pins_anyway(station) -> None:
    manager, _, clock = _armed(station, {0: (_key("1"), "Left"), 1: (_key("2"), "Right")})
    manager.gamepad_handler.held = {1: frozenset({3})}
    assert manager.poll_pin_capture() is None
    clock.now += RELEASE_TIMEOUT_S
    assert manager.poll_pin_capture() == CaptureResult(0, "pinned", "USB 1, port 2")


def test_a_button_held_when_the_capture_starts_is_not_the_press(station) -> None:
    manager, _ = station({0: (_key("1"), "Left"), 1: (_key("2"), "Right")})
    manager._clock = _Clock()
    manager.gamepad_handler.held = {1: frozenset({3})}
    manager.start_pin_capture(0)
    assert manager.poll_pin_capture() is None
    # Let go and pressed again, it counts.
    manager.gamepad_handler.held = {}
    assert manager.poll_pin_capture() is None
    manager.gamepad_handler.held = {1: frozenset({3})}
    assert manager.poll_pin_capture() is None
    manager.gamepad_handler.held = {}
    assert manager.poll_pin_capture() == CaptureResult(0, "pinned", "USB 1, port 2")


def test_a_puck_press_pins_the_puck(station) -> None:
    _Pucks.initial = {4: Mouse3DDeviceInfo(path="/dev/hidraw0", port_key=_key("3"), product_name="Puck")}
    try:
        manager, _, _ = _armed(station, {0: (_key("1"), "Pad")}, slot=0, mouse3d__enabled=True)
    finally:
        _Pucks.initial = {}
    manager.mouse3d_manager.presses = {4: {0}}
    assert manager.poll_pin_capture() == CaptureResult(0, "pinned", "USB 1, port 3")
    assert [(c["kind"], c["name"]) for c in manager.get_controller_info()] == [("mouse3d", "Puck"), ("gamepad", "Pad")]


def test_a_puck_press_latched_before_the_capture_is_not_the_press(station) -> None:
    _Pucks.initial = {4: Mouse3DDeviceInfo(path="/dev/hidraw0", port_key=_key("3"), product_name="Puck")}
    try:
        manager, _ = station({0: (_key("1"), "Pad")}, mouse3d__enabled=True)
    finally:
        _Pucks.initial = {}
    manager.mouse3d_manager.presses = {4: {0}}
    manager.start_pin_capture(1)
    assert manager.poll_pin_capture() is None


def test_a_puck_press_waits_for_its_release(station) -> None:
    _Pucks.initial = {4: Mouse3DDeviceInfo(path="/dev/hidraw0", port_key=_key("3"), product_name="Puck")}
    try:
        manager, _, _ = _armed(station, {}, slot=0, mouse3d__enabled=True)
    finally:
        _Pucks.initial = {}
    manager.mouse3d_manager.presses = {4: {1}}
    manager.mouse3d_manager.held = {4: frozenset({1})}
    assert manager.poll_pin_capture() is None
    manager.mouse3d_manager.held = {}
    assert manager.poll_pin_capture() == CaptureResult(0, "pinned", "USB 1, port 3")


def test_a_controller_with_no_stable_port_is_refused(station) -> None:
    manager, _, _ = _armed(station, {0: (_key("1"), "Left"), 1: (None, "Keyless")})
    manager.gamepad_handler.held = {1: frozenset({0})}
    manager.poll_pin_capture()
    manager.gamepad_handler.held = {}
    assert manager.poll_pin_capture() == CaptureResult(0, "no_port", "Keyless")
    assert manager.slot_pins() == []
    assert _names(manager) == [("Left", False), ("Keyless", False)]


def test_a_controller_in_no_slot_is_refused(station, monkeypatch) -> None:
    manager, _, _ = _armed(station, {0: (_key("1"), "Left")})
    # Read while waiting, but holding no slot: its kind's slots were never built.
    monkeypatch.setattr(manager, "_refresh_slots", lambda: manager._slot_table.slots)
    manager.gamepad_handler.pads[5] = (_key("4"), "Stray")
    manager.poll_pin_capture()
    manager.gamepad_handler.held = {5: frozenset({0})}
    manager.poll_pin_capture()
    manager.gamepad_handler.held = {}
    assert manager.poll_pin_capture() == CaptureResult(0, "no_slot", "")


def test_a_pad_plugged_in_while_waiting_can_be_pinned(station) -> None:
    manager, _, _ = _armed(station, {0: (_key("1"), "Left")})
    manager.gamepad_handler.pads[5] = (_key("4"), "New")
    assert manager.poll_pin_capture() is None
    manager.gamepad_handler.held = {5: frozenset({2})}
    manager.poll_pin_capture()
    manager.gamepad_handler.held = {}
    assert manager.poll_pin_capture() == CaptureResult(0, "pinned", "USB 1, port 4")
    assert _names(manager) == [("New", True), ("Left", False)]


def test_a_pad_plugged_in_with_a_button_down_is_not_the_press(station) -> None:
    manager, _, _ = _armed(station, {0: (_key("1"), "Left")})
    manager.gamepad_handler.pads[5] = (_key("4"), "New")
    manager.gamepad_handler.held = {5: frozenset({2})}
    assert manager.poll_pin_capture() is None
    manager.gamepad_handler.held = {}
    assert manager.poll_pin_capture() is None
    assert manager.pin_capture_active is True


def test_a_capture_with_no_press_times_out_and_pins_nothing(station) -> None:
    manager, _, clock = _armed(station, {0: (_key("1"), "Left")})
    clock.now += CAPTURE_TIMEOUT_S - 0.5
    assert manager.poll_pin_capture() is None
    assert manager.pin_capture_status()["seconds_left"] == pytest.approx(0.5)
    clock.now += 0.5
    assert manager.poll_pin_capture() == CaptureResult(0, "timeout", "")
    assert manager.pin_capture_active is False
    assert manager.slot_pins() == []
    assert manager.gamepad_handler.resyncs == 1


def test_cancel_pins_nothing(station) -> None:
    manager, _, _ = _armed(station, {0: (_key("1"), "Left")})
    manager.gamepad_handler.held = {0: frozenset({1})}
    assert manager.cancel_pin_capture() == CaptureResult(0, "cancelled", "")
    assert manager.slot_pins() == []
    assert manager.cancel_pin_capture() is None
    assert manager.poll_pin_capture() is None


def test_a_switched_off_kind_cannot_answer(station) -> None:
    manager, _, _ = _armed(station, {0: (_key("1"), "Left")}, mouse3d__enabled=True)
    manager.app._config.controller.enabled = False
    manager.gamepad_handler.held = {0: frozenset({1})}
    assert manager.poll_pin_capture() is None
    manager.gamepad_handler.held = {}
    assert manager.poll_pin_capture() is None


@pytest.mark.parametrize("slot", [-1, 1])
def test_a_capture_needs_a_slot(station, slot: int) -> None:
    manager, _ = station({0: (_key("1"), "Left")})
    assert manager.start_pin_capture(slot) is False
    assert manager.pin_capture_active is False


def test_only_one_capture_runs_at_a_time(station) -> None:
    manager, _, _ = _armed(station, {0: (_key("1"), "Left"), 1: (_key("2"), "Right")})
    assert manager.start_pin_capture(1) is False
    assert manager.pin_capture_status()["slot"] == 0


def test_the_status_reports_the_armed_slot_then_the_result_for_a_while(station) -> None:
    manager, _ = station({0: (_key("1"), "Left")})
    clock = _Clock()
    manager._clock = clock
    assert manager.pin_capture_status() == {"slot": None, "seconds_left": 0.0, "result": None}
    manager.start_pin_capture(0)
    clock.now += 2.0
    assert manager.pin_capture_status() == {"slot": 0, "seconds_left": CAPTURE_TIMEOUT_S - 2.0, "result": None}
    manager.cancel_pin_capture()
    assert manager.pin_capture_status()["result"] == {"slot": 0, "outcome": "cancelled", "detail": ""}
    clock.now += 9.9
    assert manager.pin_capture_status()["result"] is not None
    clock.now += 0.1
    assert manager.pin_capture_status()["result"] is None
    # A new capture clears the last one's result.
    manager.cancel_pin_capture()
    manager.start_pin_capture(0)
    assert manager.pin_capture_status()["result"] is None


def test_an_empty_slot_reports_no_kind_of_backend(station) -> None:
    manager, _ = station({0: (_key("1"), "Solo")}, controller__slot_pins=[_pin(2, "1", "Solo")])
    empty = manager.get_controller_info()[0]
    assert (empty["state"], empty["backend"], empty["effective_speed"]) == ("empty", "", 0.0)


@pytest.fixture
def running(station, tmp_path):
    """A station wired the way the main loop drives Pin here: web clicks, the HUD prompt, config.toml."""

    def build(pads: dict[int, tuple[str | None, str]], **config: Any) -> tuple[InputManager, _App, _Clock]:
        manager, app = station(pads, **config)
        clock = _Clock()
        manager._clock = clock
        app._input_manager = manager
        app._web_commands = WebCommandQueue()
        app._config_path = str(tmp_path / "config.toml")
        app._slot_pin_prompt = None
        return manager, app, clock

    return build


def _click(app: _App, action: str, index: int) -> None:
    ref = app._input_manager.slot_ref(index) or ""
    app._web_commands.request_slot_action(action, index, ref)
    app_commands.check_controller_slot_actions(app)


def _prompts(app: _App) -> list[tuple[str, bool]]:
    return [(m.message, m.from_app) for m in app._runtime_services._operator_message_store.snapshot()]


def _saved_pins(app: _App) -> list[SlotPinConfig]:
    from openfollow.configuration import load_config

    return load_config(app._config_path).controller.slot_pins


def test_pin_here_prompts_on_the_operator_screen_and_saves_the_pin(running) -> None:
    manager, app, _ = running({0: (_key("1"), "Left"), 1: (_key("2"), "Right")})
    _click(app, "pin", 0)
    assert manager.pin_capture_active is True
    assert _prompts(app) == [("Press a button on the controller for C1", True)]
    manager.gamepad_handler.held = {1: frozenset({0})}
    app_modes.process_input(app, 0.016)
    manager.gamepad_handler.held = {}
    app_modes.process_input(app, 0.016)
    assert manager.pin_capture_active is False
    assert _prompts(app) == []
    assert app._config.controller.slot_pins == [_pin(1, "2", "Right")]
    assert _saved_pins(app) == [_pin(1, "2", "Right")]


def test_while_waiting_no_input_reaches_a_marker(running, monkeypatch) -> None:
    manager, app, _ = running({0: (_key("1"), "Left")})
    _click(app, "pin", 0)
    monkeypatch.setattr(manager, "update", lambda _dt: pytest.fail("input ran while waiting for the press"))
    app_modes.process_input(app, 0.016)
    assert app._marker_control_suspended is True
    # The key and pointer handlers stand down too.
    assert app_modes._exclusive_mode_active(app) is True


@pytest.mark.parametrize("how", ["escape", "web", "timeout"])
def test_a_capture_that_ends_without_a_press_saves_nothing(running, how: str) -> None:
    manager, app, clock = running({0: (_key("1"), "Left")})
    _click(app, "pin", 0)
    if how == "escape":
        manager.keyboard_handler.keys = {"Escape"}
        app_modes.process_input(app, 0.016)
    elif how == "web":
        app._web_commands.request_slot_action("cancel-pin", 0, "")
        app_commands.check_controller_slot_actions(app)
    else:
        clock.now += CAPTURE_TIMEOUT_S
        app_modes.process_input(app, 0.016)
    assert manager.pin_capture_active is False
    assert _prompts(app) == []
    assert app._config.controller.slot_pins == []
    assert not os.path.exists(app._config_path)


def test_a_refused_press_retracts_the_prompt_and_saves_nothing(running) -> None:
    manager, app, _ = running({0: (None, "Keyless")})
    _click(app, "pin", 0)
    manager.gamepad_handler.held = {0: frozenset({0})}
    app_modes.process_input(app, 0.016)
    manager.gamepad_handler.held = {}
    app_modes.process_input(app, 0.016)
    assert manager.pin_capture_status()["result"]["outcome"] == "no_port"
    assert _prompts(app) == []
    assert app._config.controller.slot_pins == []


def test_the_prompt_shows_with_operator_messages_switched_off(running) -> None:
    _, app, _ = running({0: (_key("1"), "Left")}, operator_messages__enabled=False)
    _click(app, "pin", 0)
    assert _prompts(app) == [("Press a button on the controller for C1", True)]


def test_pin_here_waits_while_a_screen_owns_the_input(running, caplog) -> None:
    manager, app, _ = running({0: (_key("1"), "Left")})
    app._settings_menu_active = True
    with caplog.at_level("WARNING", logger=app_modes.__name__):
        _click(app, "pin", 0)
    assert manager.pin_capture_active is False
    assert _prompts(app) == []
    assert "Pin here for C1 ignored" in caplog.text


def test_pin_here_on_no_slot_raises_no_prompt(running) -> None:
    _, app, _ = running({0: (_key("1"), "Left")})
    assert app_modes.enter_slot_pin_capture(app, 3) is False
    assert _prompts(app) == []


def test_unpin_from_the_web_saves_the_pins_left(running) -> None:
    manager, app, _ = running(
        {0: (_key("1"), "Left"), 1: (_key("2"), "Right")},
        controller__slot_pins=[_pin(1, "2", "Right"), _pin(2, "1", "Left")],
    )
    _click(app, "unpin", 0)
    assert app._config.controller.slot_pins == [_pin(2, "1", "Left")]
    assert _saved_pins(app) == [_pin(2, "1", "Left")]


def test_unpin_on_a_slot_without_a_pin_writes_nothing(running) -> None:
    _, app, _ = running({0: (_key("1"), "Left")})
    _click(app, "unpin", 0)
    assert not os.path.exists(app._config_path)


def test_web_cancel_with_nothing_armed_does_nothing(running) -> None:
    manager, app, _ = running({0: (_key("1"), "Left")})
    app._web_commands.request_slot_action("cancel-pin", 0, "")
    app_commands.check_controller_slot_actions(app)
    assert manager.pin_capture_status()["result"] is None


def test_pins_are_not_saved_without_an_input_manager() -> None:
    app = SimpleNamespace(_input_manager=None)
    assert app_modes.save_slot_pins(app) is False


def test_a_capture_armed_without_a_prompt_ends_cleanly(running) -> None:
    manager, app, _ = running({0: (_key("1"), "Left")})
    app._runtime_services._operator_message_store.add("from OSC")
    manager.start_pin_capture(0)
    app_modes.exit_slot_pin_capture(app)
    assert manager.pin_capture_active is False
    assert _prompts(app) == [("from OSC", False)]


def _markers(manager: InputManager) -> list[int | None]:
    return [c["marker_id"] for c in manager.get_controller_info()]


def _pick(slot: int, marker: int) -> SlotMarkerConfig:
    return SlotMarkerConfig(slot=slot, marker=marker)


def test_a_picked_marker_routes_its_slot_everywhere(station) -> None:
    manager, _ = station({0: (_key("1"), "Left"), 1: (_key("2"), "Right")}, controller__slot_markers=[_pick(1, 12)])
    assert _markers(manager) == [12, 10]
    assert manager._gamepad_marker_id(0) == 12
    assert manager.controller_marker_id(0) == 12


def test_picking_a_marker_swaps_it_with_the_slot_that_drove_it(station, caplog) -> None:
    manager, app = station({0: (_key("1"), "Left"), 1: (_key("2"), "Right")})
    with caplog.at_level("INFO", logger=input_manager_module.__name__):
        assert manager.pick_slot_marker(0, 11) is True
    assert "C1 drives marker 11; C1 11, C2 10" in caplog.text
    assert _markers(manager) == [11, 10]
    assert app._config.controller.slot_markers == [_pick(1, 11), _pick(2, 10)]


def test_a_missing_slot_can_be_given_a_marker(station) -> None:
    manager, _ = station({0: (_key("1"), "Left"), 1: (_key("2"), "Right")})
    del manager.gamepad_handler.pads[0]
    manager.update(0.016)
    assert manager.pick_slot_marker(0, 12) is True
    assert _states(manager) == [("missing", 12), ("connected", 11)]


def test_picking_for_a_slot_without_a_marker_leaves_the_other_without_one(station) -> None:
    manager, app = station({0: (_key("1"), "A"), 1: (_key("2"), "B"), 2: (_key("3"), "C"), 3: (_key("4"), "D")})
    assert _markers(manager) == [10, 11, 12, None]
    assert manager.pick_slot_marker(3, 10) is True
    assert _markers(manager) == [None, 11, 12, 10]
    # A newly controlled marker goes nowhere it wasn't picked: the emptied slot stays empty.
    app._controlled_ids.append(13)
    assert _markers(manager) == [None, 11, 12, 10]


def test_an_unassigned_slot_takes_a_newly_controlled_marker(station) -> None:
    manager, app = station({0: (_key("1"), "A"), 1: (_key("2"), "B"), 2: (_key("3"), "C"), 3: (_key("4"), "D")})
    manager.pick_slot_marker(0, 11)
    app._controlled_ids.append(13)
    assert _markers(manager) == [11, 10, 12, 13]


def test_a_marker_that_stops_being_controlled_lets_its_slot_fall_back(station) -> None:
    manager, app = station({0: (_key("1"), "Left"), 1: (_key("2"), "Right")}, controller__slot_markers=[_pick(1, 12)])
    app._controlled_ids.remove(12)
    assert _markers(manager) == [10, 11]


def test_hand_edited_picks_apply_at_once(station) -> None:
    manager, app = station({0: (_key("1"), "Left"), 1: (_key("2"), "Right")})
    app._config.controller.slot_markers = [_pick(2, 10), _pick(1, 0)]
    assert _markers(manager) == [None, 10]


@pytest.mark.parametrize("marker", [99, 0])
def test_only_a_controlled_marker_can_be_picked(station, marker: int) -> None:
    manager, app = station({0: (_key("1"), "Left"), 1: (_key("2"), "Right")})
    assert manager.pick_slot_marker(0, marker) is False
    assert app._config.controller.slot_markers == []


def test_a_reserved_or_empty_slot_has_no_marker_to_pick(station) -> None:
    manager, app = station(
        {0: (_key("1"), "Left"), 1: (_key("2"), "Right")}, controller__slot_pins=[_pin(4, "2", "Right")]
    )
    del manager.gamepad_handler.pads[0]
    manager.update(0.016)
    manager.forget_slot(0)
    assert [c["state"] for c in manager.get_controller_info()] == ["reserved", "empty", "empty", "connected"]
    assert manager.pick_slot_marker(0, 11) is False
    assert manager.pick_slot_marker(1, 11) is False
    assert manager.pick_slot_marker(9, 11) is False
    assert app._config.controller.slot_markers == []


def test_a_lone_slot_drives_all_markers_and_has_no_pick(station) -> None:
    manager, app = station({0: (_key("1"), "Solo")}, controller__slot_markers=[_pick(1, 12), _pick(2, 10)])
    info = manager.get_controller_info()[0]
    assert (info["all_markers"], info["marker_id"]) == (True, app._selected_id)
    assert manager.pick_slot_marker(0, 11) is False
    # The picks wait for a second slot.
    manager.gamepad_handler.pads[1] = (_key("2"), "Second")
    manager.update(0.016)
    assert _markers(manager) == [12, 10]
    assert [c["all_markers"] for c in manager.get_controller_info()] == [False, False]


def _pick_click(app: _App, index: int, marker: int, ref: str | None = None) -> None:
    app._web_commands.request_slot_marker(index, app._input_manager.slot_ref(index) if ref is None else ref, marker)
    app_commands.check_controller_slot_actions(app)


def _saved_picks(app: _App) -> list[SlotMarkerConfig]:
    from openfollow.configuration import load_config

    return load_config(app._config_path).controller.slot_markers


def test_a_marker_picked_on_the_web_is_saved(running) -> None:
    manager, app, _ = running({0: (_key("1"), "Left"), 1: (_key("2"), "Right")})
    app._config.controlled_marker_ids = [10, 11, 12]
    _pick_click(app, 1, 12)
    assert _markers(manager) == [10, 12]
    assert _saved_picks(app) == [_pick(1, 10), _pick(2, 12)]


def test_a_pick_for_a_slot_that_changed_since_is_left_alone(running, caplog) -> None:
    manager, app, _ = running({0: (_key("1"), "Left"), 1: (_key("2"), "Right")})
    with caplog.at_level("INFO", logger=app_commands.__name__):
        _pick_click(app, 1, 12, ref="stale")
    assert "C2 changed since its marker was picked" in caplog.text
    assert _markers(manager) == [10, 11]
    assert not os.path.exists(app._config_path)


def test_a_refused_pick_writes_nothing(running) -> None:
    _, app, _ = running({0: (_key("1"), "Left"), 1: (_key("2"), "Right")})
    _pick_click(app, 0, 99)
    assert not os.path.exists(app._config_path)


def test_picks_without_input_are_dropped() -> None:
    queue = WebCommandQueue()
    queue.request_slot_marker(0, "r", 10)
    app_commands.check_controller_slot_actions(SimpleNamespace(_input_manager=None, _web_commands=queue))
    assert queue.consume_slot_marker_picks() == []
