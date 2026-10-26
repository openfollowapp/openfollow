# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""A hardware keyboard poller is gated while a text-entry screen owns input.

evdev and Quartz read the kernel, so they see every key typed into the URL
editor, the Pi-network field editor or the browser overlay. None of those keys
may act on the app: not the Settings key, not a press edge on the screen
underneath once the typing screen closes, not an OSC hotkey.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import openfollow.input.keyboard as keyboard_module
from openfollow.configuration import AppConfig
from openfollow.input.events import InputEventBus, KeyEvent
from openfollow.input.keyboard import FallbackKeyboardPoller, KeyboardHandler, KeyboardPoller
from openfollow.runtime import app_modes, app_modes_network
from openfollow.runtime.app_modes import process_input

pytestmark = pytest.mark.unit


class _HardwarePoller(KeyboardPoller):
    """Reads held keys the way evdev and Quartz do, whatever has focus."""

    kind = "evdev"

    def __init__(self) -> None:
        self.held: set[str] = set()

    def is_available(self) -> bool:
        return True

    def is_key_pressed(self, key: str) -> bool:
        return key in self.held


class _Gamepad:
    def read_settings_menu_input(self) -> SimpleNamespace:
        return SimpleNamespace(cancel_pressed=False)

    def read_settings_toggle(self) -> bool:
        return False


def _app(**state: object) -> SimpleNamespace:
    app = SimpleNamespace(
        _config=AppConfig(),
        _button_detection=None,
        _settings_menu_active=False,
        _video_receiver=None,
        _source_type_selection_active=False,
        _field_choice_active=False,
        _url_editor_active=False,
        _url_editor_field_name="rtsp_url",
        _url_editor_field_label="",
        _url_editor_value="rtsp://192.0.2.1/strea",
        _url_editor_banner="",
        _url_editor_source_type="",
        _browser_active=False,
        _browser_closed=False,
        _settings_key_pressed=False,
        _pi_network_active=False,
        _pi_network_field_edit_active=False,
    )
    vars(app).update(state)
    return app


@pytest.fixture
def poller(monkeypatch: pytest.MonkeyPatch) -> _HardwarePoller:
    hardware = _HardwarePoller()
    monkeypatch.setattr(keyboard_module, "create_keyboard_poller", lambda: hardware)
    return hardware


def _handler(app: SimpleNamespace, bus: InputEventBus | None = None) -> KeyboardHandler:
    handler = KeyboardHandler(app, event_bus=bus)  # type: ignore[arg-type]
    app._input_manager = SimpleNamespace(
        keyboard_handler=handler,
        gamepad_handler=_Gamepad(),
        control_frame=SimpleNamespace(release_all=lambda: None),
    )
    return handler


def _recording_bus() -> tuple[InputEventBus, list[KeyEvent]]:
    bus = InputEventBus()
    events: list[KeyEvent] = []
    bus.subscribe_key(events.append)
    return bus, events


def test_the_settings_key_typed_into_the_url_editor_keeps_the_editor(poller: _HardwarePoller) -> None:
    app = _app(_url_editor_active=True)
    _handler(app)
    assert app._config.controller.key_settings == "m"
    poller.held = {"m"}  # the m of "stream"
    process_input(app, 0.016)
    assert app._url_editor_active is True
    assert app._url_editor_value == "rtsp://192.0.2.1/strea"


def test_the_settings_key_typed_into_the_browser_overlay_keeps_it_open(poller: _HardwarePoller) -> None:
    app = _app(_browser_active=True)
    app._process_browser_input = lambda: None
    _handler(app)
    poller.held = {"m"}
    process_input(app, 0.016)
    assert app._browser_active is True


def test_escape_that_closed_the_url_editor_does_not_press_on_settings(poller: _HardwarePoller) -> None:
    app = _app(_url_editor_active=True)
    handler = _handler(app)
    handler.poll_discrete_keys()
    poller.held = {"Escape"}
    # GTK delivered the Escape: the editor closed back to Settings before the next frame.
    app._url_editor_active = False
    app._settings_menu_active = True
    handler.poll_discrete_keys()
    assert handler.consume_key_presses() == []
    assert "Escape" not in handler.keys


def test_enter_that_closed_the_field_editor_does_not_press_on_the_network_screen(poller: _HardwarePoller) -> None:
    app = _app(_pi_network_active=True, _pi_network_field_edit_active=True)
    handler = _handler(app)
    poller.held = {"Enter"}
    handler.poll_discrete_keys()
    app._pi_network_field_edit_active = False
    for _ in range(3):
        handler.poll_discrete_keys()
        assert handler.consume_key_presses() == []


def test_a_key_held_through_the_close_counts_again_once_pressed_again(poller: _HardwarePoller) -> None:
    app = _app(_url_editor_active=True)
    handler = _handler(app)
    poller.held = {"Escape"}
    handler.poll_discrete_keys()
    app._url_editor_active = False
    handler.poll_discrete_keys()
    poller.held = set()
    handler.poll_discrete_keys()
    poller.held = {"Escape"}
    handler.poll_discrete_keys()
    assert handler.consume_key_presses() == ["Escape"]
    assert "Escape" in handler.keys


@pytest.mark.parametrize("screen", ["_url_editor_active", "_pi_network_field_edit_active", "_browser_active"])
def test_keys_typed_into_a_text_screen_emit_no_key_events(screen: str, poller: _HardwarePoller) -> None:
    app = _app(**{screen: True})
    bus, events = _recording_bus()
    handler = _handler(app, bus)
    poller.held = {"a", "Shift"}
    handler.poll_discrete_keys()
    assert handler.keys == set()
    setattr(app, screen, False)
    handler.poll_discrete_keys()
    assert events == []


def test_a_key_held_before_the_screen_opened_is_released_on_the_bus(poller: _HardwarePoller) -> None:
    app = _app()
    bus, events = _recording_bus()
    handler = _handler(app, bus)
    poller.held = {"Enter"}
    handler.poll_discrete_keys()
    app._url_editor_active = True
    handler.poll_discrete_keys()
    assert [(event.key, event.edge) for event in events] == [("Enter", "press"), ("Enter", "release")]


def test_the_field_choice_picker_still_takes_polled_arrows(poller: _HardwarePoller) -> None:
    app = _app(_field_choice_active=True)
    handler = _handler(app)
    poller.held = {"ArrowDown"}
    handler.poll_discrete_keys()
    assert handler.consume_key_presses() == ["ArrowDown"]


def test_marker_movement_ignores_a_key_held_through_the_close(poller: _HardwarePoller) -> None:
    app = _app(_url_editor_active=True, _selected_id=1)
    app.get_marker_move_speed = lambda marker_id: 1.0
    handler = _handler(app)
    poller.held = {"w"}
    handler.poll_discrete_keys()
    app._url_editor_active = False
    handler.poll_discrete_keys()
    assert handler.update(0.016) is None


def test_the_fallback_is_not_gated(monkeypatch: pytest.MonkeyPatch) -> None:
    fallback = FallbackKeyboardPoller()
    monkeypatch.setattr(keyboard_module, "create_keyboard_poller", lambda: fallback)
    app = _app(_url_editor_active=True)
    handler = _handler(app)
    fallback.on_key_down("w")
    assert handler.keys == {"w"}


# --------------------------------------------------------------------------- #
# The press that opened a text screen reaches GTK after it is open
# --------------------------------------------------------------------------- #


def _opened_by_a_polled_enter(poller: _HardwarePoller, **screen: object) -> SimpleNamespace:
    """The frame's poll saw Enter, and the press it produced opened a text screen."""
    app = _app()
    app._normalize_key = app_modes.normalize_key
    handler = _handler(app)
    poller.held = {"Enter"}
    handler.poll_discrete_keys()
    assert handler.consume_key_presses() == ["Enter"]
    vars(app).update(screen)
    return app


def test_the_late_gtk_enter_does_not_confirm_the_url_editor_it_opened(poller: _HardwarePoller) -> None:
    confirmed: list[bool] = []
    app = _opened_by_a_polled_enter(poller, _url_editor_active=True)
    app._confirm_url_editor = lambda: confirmed.append(True)
    app_modes.on_key_down(app, {"key": "Enter"})
    assert (app._url_editor_active, app._url_editor_value, confirmed) == (True, "rtsp://192.0.2.1/strea", [])
    # Released (GTK key_up) and pressed again: that Enter is the operator's.
    poller.held = set()
    app_modes.on_key_up(app, {"key": "Enter"})
    poller.held = {"Enter"}
    app_modes.on_key_down(app, {"key": "Enter"})
    assert confirmed == [True]


def test_a_release_the_poller_saw_ends_the_echo_too(poller: _HardwarePoller) -> None:
    confirmed: list[bool] = []
    app = _opened_by_a_polled_enter(poller, _url_editor_active=True)
    app._confirm_url_editor = lambda: confirmed.append(True)
    poller.held = set()
    app._input_manager.keyboard_handler.poll_discrete_keys()
    poller.held = {"Enter"}
    app_modes.on_key_down(app, {"key": "Enter"})
    assert confirmed == [True]


def test_the_late_gtk_enter_does_not_commit_the_field_editor_it_opened(
    poller: _HardwarePoller, monkeypatch: pytest.MonkeyPatch
) -> None:
    committed: list[bool] = []
    monkeypatch.setattr(app_modes_network, "confirm_pi_network_field_edit", lambda app: committed.append(True))
    app = _opened_by_a_polled_enter(
        poller, _pi_network_active=True, _pi_network_field_edit_active=True, _pi_network_field_value="192.0.2.10"
    )
    app_modes.on_key_down(app, {"key": "Enter"})
    assert (app._pi_network_field_edit_active, committed) == (True, [])
    # Other keys still type.
    app_modes.on_key_down(app, {"key": "1"})
    assert app._pi_network_field_value == "192.0.2.101"
    poller.held = set()
    app_modes.on_key_up(app, {"key": "Enter"})
    app_modes.on_key_down(app, {"key": "Enter"})
    assert committed == [True]


def test_keys_pressed_after_the_screen_opened_still_type(poller: _HardwarePoller) -> None:
    app = _opened_by_a_polled_enter(poller, _url_editor_active=True)
    handler = app._input_manager.keyboard_handler
    poller.held = set()
    handler.poll_discrete_keys()
    poller.held = {"b"}
    handler.poll_discrete_keys()
    app_modes.on_key_down(app, {"key": "b"})
    assert app._url_editor_value == "rtsp://192.0.2.1/streab"


def test_the_echo_keys_auto_repeat_never_confirms_the_url_editor(poller: _HardwarePoller) -> None:
    confirmed: list[bool] = []
    app = _opened_by_a_polled_enter(poller, _url_editor_active=True)
    app._confirm_url_editor = lambda: confirmed.append(True)
    for _ in range(4):
        app._input_manager.keyboard_handler.poll_discrete_keys()
        app_modes.on_key_down(app, {"key": "Enter"})
    assert (app._url_editor_active, confirmed) == (True, [])


def test_the_echo_keys_auto_repeat_never_commits_the_field_editor(
    poller: _HardwarePoller, monkeypatch: pytest.MonkeyPatch
) -> None:
    committed: list[bool] = []
    monkeypatch.setattr(app_modes_network, "confirm_pi_network_field_edit", lambda app: committed.append(True))
    app = _opened_by_a_polled_enter(poller, _pi_network_active=True, _pi_network_field_edit_active=True)
    for _ in range(4):
        app._input_manager.keyboard_handler.poll_discrete_keys()
        app_modes.on_key_down(app, {"key": "Enter"})
    assert committed == []


def test_the_late_gtk_enter_is_marked_for_the_window_to_keep_from_the_webview(poller: _HardwarePoller) -> None:
    app = _opened_by_a_polled_enter(poller, _browser_active=True)
    late = {"key": "Enter"}
    app_modes.on_key_down(app, late)
    assert late.get("late_echo") is True
    typed = {"key": "a"}
    app_modes.on_key_down(app, typed)
    assert "late_echo" not in typed


def test_the_fallback_has_no_late_echo(monkeypatch: pytest.MonkeyPatch) -> None:
    fallback = FallbackKeyboardPoller()
    monkeypatch.setattr(keyboard_module, "create_keyboard_poller", lambda: fallback)
    confirmed: list[bool] = []
    app = _app()
    app._normalize_key = app_modes.normalize_key
    handler = _handler(app)
    fallback.on_key_down("Enter")
    handler.poll_discrete_keys()
    app._url_editor_active = True
    app._confirm_url_editor = lambda: confirmed.append(True)
    app_modes.on_key_down(app, {"key": "Enter"})
    assert confirmed == [True]


# --------------------------------------------------------------------------- #
# Modifiers on the bus
# --------------------------------------------------------------------------- #


def test_a_shift_held_through_a_close_still_stamps_the_next_key(poller: _HardwarePoller) -> None:
    app = _app(_url_editor_active=True)
    bus, events = _recording_bus()
    handler = _handler(app, bus)
    poller.held = {"Shift"}
    handler.poll_discrete_keys()
    app._url_editor_active = False
    handler.poll_discrete_keys()
    poller.held = {"Shift", "a"}
    handler.poll_discrete_keys()
    poller.held = {"Shift"}
    handler.poll_discrete_keys()
    assert [(event.key, event.edge, event.modifiers) for event in events] == [
        ("a", "press", frozenset({"shift"})),
        ("a", "release", frozenset({"shift"})),
    ]
