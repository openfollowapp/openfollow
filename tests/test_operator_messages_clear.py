# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for the operator-message clear binding.

Covers the ``ControllerConfig`` bindings, the keyboard and gamepad dispatch
in ``app_modes``, and the ``_clear_operator_messages`` handler.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openfollow.configuration import (
    _BUTTON_MAPPING_FIELDS,
    _KEYBOARD_ACTION_FIELDS,
    AppConfig,
    ControllerConfig,
)
from openfollow.input.gamepad import GamepadUpdate
from openfollow.operator_messages import OperatorMessageStore
from openfollow.runtime import app_modes

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# Config surface
# --------------------------------------------------------------------------- #


def test_clear_message_bindings_default_unbound() -> None:
    cc = ControllerConfig()
    assert cc.key_clear_messages == ""
    assert cc.btn_clear_messages == ""


def test_clear_message_bindings_registered() -> None:
    # Registry membership drives __post_init__ validation and the bindings form.
    assert "btn_clear_messages" in _BUTTON_MAPPING_FIELDS
    assert "key_clear_messages" in _KEYBOARD_ACTION_FIELDS


# --------------------------------------------------------------------------- #
# App handler
# --------------------------------------------------------------------------- #


def test_clear_operator_messages_empties_store() -> None:
    from openfollow.app import OpenFollowApp

    store = OperatorMessageStore(clock=lambda: 0.0)
    store.add("a", marker_id=0)
    store.add("b", marker_id=3)
    app = SimpleNamespace(_runtime_services=SimpleNamespace(_operator_message_store=store))

    # Call the unbound method against the stand-in app.
    OpenFollowApp._clear_operator_messages(app)

    assert store.snapshot() == []


# --------------------------------------------------------------------------- #
# Gamepad dispatch (process_input)
# --------------------------------------------------------------------------- #


def test_process_input_dispatches_clear_messages() -> None:
    class _InputManager:
        keyboard_handler = SimpleNamespace(keys=set())

        def update(self, dt):  # noqa: ANN001, ANN201
            return GamepadUpdate(clear_messages_pressed=True)

    cleared: list[bool] = []
    app = SimpleNamespace(
        _input_manager=_InputManager(),
        _button_detection=None,
        _settings_menu_active=False,
        _video_receiver=None,
        _iface_selection_active=False,
        _source_type_selection_active=False,
        _url_editor_active=False,
        _field_choice_active=False,
        _browser_active=False,
        _config=AppConfig(),
        _settings_key_pressed=False,
        _show_hud_help=False,
        _controlled_ids=[],
        _selected_id=None,
        _clear_operator_messages=lambda: cleared.append(True),
    )

    app_modes.process_input(app, 0.01)

    assert cleared == [True]


# --------------------------------------------------------------------------- #
# Keyboard dispatch (handle_key_press)
# --------------------------------------------------------------------------- #


def _key_app(cleared: list[bool]) -> SimpleNamespace:
    app = SimpleNamespace(
        _button_detection=None,
        _settings_menu_active=False,
        _video_receiver=None,
        _iface_selection_active=False,
        _available_interfaces=[],
        _selected_iface_index=0,
        _url_editor_active=False,
        _browser_active=False,
        _source_type_selection_active=False,
        _field_choice_active=False,
        _config=AppConfig(),
        _selected_id=None,
        _server=None,
        _clear_operator_messages=lambda: cleared.append(True),
    )
    app._config.controller.key_clear_messages = "c"
    return app


def test_handle_key_press_dispatches_clear_messages() -> None:
    cleared: list[bool] = []
    app = _key_app(cleared)
    app_modes.handle_key_press(app, "c")
    assert cleared == [True]


def test_handle_key_press_other_key_does_not_clear() -> None:
    cleared: list[bool] = []
    app = _key_app(cleared)
    app_modes.handle_key_press(app, "p")  # unrelated key
    assert cleared == []
