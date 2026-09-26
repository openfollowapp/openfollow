# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Controller slots through the InputManager: routing, missing slots, Forget, Identify."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import openfollow.input.input_manager as input_manager_module
from openfollow.configuration import AppConfig
from openfollow.input.gamepad import GamepadUpdate
from openfollow.input.input_manager import InputManager
from openfollow.input.mouse3d import Mouse3DDeviceInfo, Mouse3DUpdate
from openfollow.operator_messages import OperatorMessageStore
from openfollow.psn import Marker

pytestmark = pytest.mark.unit

_HOST = "platform/xhci-hcd.1"


class _Keyboard:
    def __init__(self, app: Any, *, event_bus: Any = None) -> None:
        pass

    def update(self, _dt: float) -> None:
        return None

    def is_connected(self) -> bool:
        return True


class _Pads:
    """Gamepads by SDL instance id: ``pads[id] = (port key, name)``."""

    def __init__(self, app: Any, **_kwargs: Any) -> None:
        self.pads: dict[int, tuple[str | None, str]] = dict(_Pads.initial)
        self.inputs: dict[int, float] = {}
        self.rumbles_ok: dict[int, bool] = {}
        self.identified: list[int] = []

    initial: dict[int, tuple[str | None, str]] = {}

    @property
    def joysticks(self) -> dict[int, object]:
        return {idx: object() for idx in self.pads}

    def update(self, _dt: float) -> GamepadUpdate:
        return GamepadUpdate()

    def device_identities(self) -> dict[int, tuple[str | None, str]]:
        return dict(self.pads)

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

    def __init__(self, config: Any, *, backend: Any = None) -> None:
        self.devices: dict[int, Mouse3DDeviceInfo] = {}
        self.settled = True
        self.inputs: dict[int, float] = {}
        self.leds_ok = True
        self.identified: list[int] = []

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
    assert (info["name"], info["connected"], info["port_label"]) == ("First", False, "USB 1 · port 1")


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


def test_switching_gamepads_off_rebuilds_the_slots(station) -> None:
    manager, app = station({0: (_key("1"), "First"), 1: (_key("2"), "Second")}, mouse3d__enabled=True)
    manager.mouse3d_manager.devices[0] = Mouse3DDeviceInfo(path="/dev/hidraw0", port_key=_key("3"))
    manager.update(0.016)
    app._config.controller.enabled = False
    manager.update(0.016)
    assert [(c["kind"], c["state"]) for c in manager.get_controller_info()] == [("mouse3d", "connected")]
    app._config.controller.enabled = True
    manager.update(0.016)
    assert [c["kind"] for c in manager.get_controller_info()] == ["gamepad", "gamepad", "mouse3d"]


def test_slots_wait_for_the_3d_mouse_scan_before_freezing(station) -> None:
    manager, _ = station({0: (_key("2"), "Pad")}, mouse3d__enabled=True)
    manager.mouse3d_manager.settled = False
    manager._slot_table.rebuild()
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
    assert manager.identify_flash_marker() == 11
    clock.now += 2.9
    assert manager.identify_flash_marker() == 11
    clock.now += 0.1
    assert manager.identify_flash_marker() is None
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
    assert "Controller slots: C1 GameSir (USB 1 · port 1), C2 Wireless (no stable port)" in caplog.text
    caplog.clear()
    with caplog.at_level("INFO", logger=input_manager_module.__name__):
        manager.update(0.016)
    assert "Controller slots" not in caplog.text
