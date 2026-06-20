# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Input handling for game controllers, keyboard, and mouse.

Marker-position OSC input flows through the unified :class:`openfollow.osc.OscService`.
"""

from openfollow.input.gamepad import GamepadHandler, SourceSelectionInput
from openfollow.input.input_manager import InputManager
from openfollow.input.keyboard import POLLED_KEYS, KeyboardHandler, KeyboardPoller, create_keyboard_poller
from openfollow.input.mouse import MouseHandler

__all__ = [
    "InputManager",
    "GamepadHandler",
    "SourceSelectionInput",
    "KeyboardHandler",
    "KeyboardPoller",
    "create_keyboard_poller",
    "POLLED_KEYS",
    "MouseHandler",
]
