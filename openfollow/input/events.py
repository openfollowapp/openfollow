# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Pub/sub event bus for edge-triggered keyboard and controller-button events.

Bridges the ``InputManager`` (which polls hardware state per frame) to consumers
wanting **edge-triggered** notifications (key pressed, button released).

Key properties:

- **Edge-only:** One event per transition, not per polling tick. Handlers don't
  need to track state to enforce "held key fires once" semantics.
- **Snapshot-then-call:** Subscribe / unsubscribe from other threads without
  blocking slow handlers; re-entrant subscribes from within a handler are safe.
- **Error isolation:** Exceptions in one handler are logged and skipped; the bus
  continues fanning out to remaining subscribers.
- **Functional unsubscribe:** Returns an idempotent callable for cleanup without
  bookkeeping handler references.

Emission is synchronous: callbacks run on the caller's thread before ``emit_*``
returns. Subscribers should be fast (quick locks, append to queue, schedule work).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Literal

from openfollow.configuration import VALID_TRIGGER_MODIFIERS

logger = logging.getLogger(__name__)


_Edge = Literal["press", "release"]


@dataclass(frozen=True)
class KeyEvent:
    """One keyboard edge event.

    ``key`` matches the form used elsewhere in the project – single
    lower-case letters (``"a"``), arrow / function names (``"F1"``,
    ``"ArrowUp"``, ``"Escape"``), numpad names (``"Numpad7"``).
    ``modifiers`` is the set of held modifier keys at the moment of
    the edge.

    The constructor accepts any iterable of strings (set, list,
    frozenset, tuple, generator), so production emit-site code that
    builds modifiers from polled state doesn't need a frozenset
    coercion. ``__post_init__`` then normalises to a lower-case
    ``frozenset[str]`` filtered against
    :data:`openfollow.configuration.VALID_TRIGGER_MODIFIERS`, so by
    the time the event leaves construction ``ev.modifiers`` is
    byte-comparable against a config row's
    :class:`HotkeyTrigger.modifiers`. (The static type stays
    ``Iterable[str]`` because a frozen dataclass field type doubles
    as the constructor parameter type; downstream readers treat the
    field as the post-init invariant ``frozenset[str]``.)
    """

    key: str
    modifiers: Iterable[str] = field(default_factory=frozenset)
    edge: _Edge = "press"

    def __post_init__(self) -> None:
        # Normalise to the same lower-case canonical form
        # ``HotkeyTrigger.__post_init__`` produces, so a config row
        # ``modifiers=("ctrl", "shift")`` matches a hardware-emitted
        # event regardless of the emit-site casing.
        if isinstance(self.modifiers, Iterable) and not isinstance(
            self.modifiers,
            (str, bytes),
        ):
            normalised = frozenset(
                m.strip().lower()
                for m in self.modifiers
                if isinstance(m, str) and m.strip().lower() in VALID_TRIGGER_MODIFIERS
            )
        else:
            normalised = frozenset()
        # Frozen dataclass workaround.
        object.__setattr__(self, "modifiers", normalised)


@dataclass(frozen=True)
class ButtonEvent:
    """One controller-button edge event.

    ``button`` is a name from
    :data:`openfollow.configuration.VALID_BUTTON_NAMES` (``"A"``,
    ``"DPAD_UP"``, etc.). ``controller_index`` is the SDL joystick
    index, kept for future per-controller routing – today's
    Hotkey-trigger and ControllerButton-trigger matchers ignore it.
    """

    button: str
    controller_index: int = 0
    edge: _Edge = "press"


_KeyHandler = Callable[[KeyEvent], None]
_ButtonHandler = Callable[[ButtonEvent], None]


class InputEventBus:
    """Pub/sub for keyboard + controller-button edge events.

    One instance per :class:`openfollow.input.input_manager.InputManager`.
    Producers (the keyboard / gamepad handlers) call ``emit_key`` /
    ``emit_button`` on the input poll thread; consumers (initially:
    the OSC transmitter manager) ``subscribe_*`` at construction and
    ``unsubscribe`` at shutdown via the returned callable.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._key_handlers: list[_KeyHandler] = []
        self._button_handlers: list[_ButtonHandler] = []

    # ------------------------------------------------------------------
    # Subscribe / unsubscribe
    # ------------------------------------------------------------------

    def subscribe_key(self, handler: _KeyHandler) -> Callable[[], None]:
        """Register a key-event subscriber. Returns an idempotent
        unsubscribe callable."""
        with self._lock:
            self._key_handlers.append(handler)

        def _unsubscribe() -> None:
            with self._lock:
                try:
                    self._key_handlers.remove(handler)
                except ValueError:
                    # Idempotent: already removed.
                    pass

        return _unsubscribe

    def subscribe_button(self, handler: _ButtonHandler) -> Callable[[], None]:
        """Register a button-event subscriber. Returns an idempotent
        unsubscribe callable."""
        with self._lock:
            self._button_handlers.append(handler)

        def _unsubscribe() -> None:
            with self._lock:
                try:
                    self._button_handlers.remove(handler)
                except ValueError:
                    # Idempotent: already removed.
                    pass

        return _unsubscribe

    # ------------------------------------------------------------------
    # Emission
    # ------------------------------------------------------------------

    def emit_key(self, event: KeyEvent) -> None:
        """Fan out a ``KeyEvent`` to every key subscriber. A handler
        that raises is logged and skipped – bus dispatch continues."""
        with self._lock:
            handlers = list(self._key_handlers)
        for handler in handlers:
            try:
                handler(event)
            except Exception:
                logger.exception(
                    "InputEventBus: key handler raised on %r; continuing dispatch to remaining handlers.",
                    event,
                )

    def emit_button(self, event: ButtonEvent) -> None:
        """Fan out a ``ButtonEvent`` to every button subscriber. A
        handler that raises is logged and skipped."""
        with self._lock:
            handlers = list(self._button_handlers)
        for handler in handlers:
            try:
                handler(event)
            except Exception:
                logger.exception(
                    "InputEventBus: button handler raised on %r; continuing dispatch to remaining handlers.",
                    event,
                )
