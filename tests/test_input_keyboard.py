# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for keyboard discrete-key edge detection, movement layouts, and
the keyboard-to-InputEventBus emission path."""

from __future__ import annotations

import math

import pytest

import openfollow.input.keyboard as keyboard_module
from openfollow.configuration import AppConfig
from openfollow.input.keyboard import FallbackKeyboardPoller, KeyboardHandler

pytestmark = pytest.mark.unit


class _FakePoller:
    def __init__(self) -> None:
        self.pressed: set[str] = set()

    def is_key_pressed(self, key: str) -> bool:
        return key in self.pressed

    def get_pressed_keys(self, keys: list[str]) -> set[str]:
        return {key for key in keys if key in self.pressed}

    def is_available(self) -> bool:
        return True


class _DummyApp:
    def __init__(self) -> None:
        self._config = AppConfig()
        self._selected_id: int | None = 0

    def get_marker_move_speed(self, marker_id: int | None) -> float:
        """Mirror ``OpenFollowApp.get_marker_move_speed``.

        Keyboard movement now scales by the per-marker speed for the
        selected marker; the fake keeps the same fallback semantics so
        the existing velocity assertions continue to read the global
        ``MarkerConfig.move_speed`` default.
        """
        if marker_id is None:
            return self._config.marker.move_speed
        return self._config.marker_move_speeds.get(
            marker_id,
            self._config.marker.move_speed,
        )


def test_discrete_polled_keys_caches_and_invalidates_on_controller_swap(
    monkeypatch,
) -> None:
    """The discrete-key set is memoised by controller identity:
    a repeat call with the same controller returns the cached frozenset;
    a hot-reload that reassigns ``.controller`` (new object) recomputes."""
    import dataclasses

    poller = _FakePoller()
    monkeypatch.setattr(keyboard_module, "create_keyboard_poller", lambda: poller)
    app = _DummyApp()
    handler = KeyboardHandler(app)

    first = handler._discrete_polled_keys()
    # Same controller object → cache hit → identical frozenset returned.
    assert handler._discrete_polled_keys() is first

    # apply_runtime_config_changes reassigns .controller to a new object
    # only when it actually changes; here we rebind a discrete action.
    app._config.controller = dataclasses.replace(app._config.controller, key_reset="p")
    refreshed = handler._discrete_polled_keys()
    assert refreshed is not first
    assert "p" in refreshed


def test_discrete_key_polling_detects_edges(monkeypatch) -> None:
    poller = _FakePoller()
    monkeypatch.setattr(keyboard_module, "create_keyboard_poller", lambda: poller)

    handler = KeyboardHandler(_DummyApp())

    poller.pressed = {"r"}
    handler.poll_discrete_keys()
    assert handler.consume_key_presses() == ["r"]

    handler.poll_discrete_keys()
    assert handler.consume_key_presses() == []

    poller.pressed = set()
    handler.poll_discrete_keys()
    assert handler.consume_key_presses() == []

    poller.pressed = {"r"}
    handler.poll_discrete_keys()
    assert handler.consume_key_presses() == ["r"]


def test_keyboard_update_returns_marker_velocity(monkeypatch) -> None:
    poller = _FakePoller()
    monkeypatch.setattr(keyboard_module, "create_keyboard_poller", lambda: poller)

    app = _DummyApp()
    handler = KeyboardHandler(app)

    poller.pressed = {"w", "d"}
    velocity = handler.update(0.016)

    normalized = app._config.marker.move_speed / math.sqrt(2)
    assert velocity == pytest.approx((normalized, normalized, 0.0))


@pytest.mark.parametrize(
    "layout,forward,back,left,right",
    [
        ("wasd", "w", "s", "a", "d"),
        ("ijkl", "i", "k", "j", "l"),
        ("numpad", "Numpad8", "Numpad2", "Numpad4", "Numpad6"),
    ],
)
def test_keyboard_update_respects_movement_layout(
    monkeypatch,
    layout,
    forward,
    back,
    left,
    right,
) -> None:
    poller = _FakePoller()
    monkeypatch.setattr(keyboard_module, "create_keyboard_poller", lambda: poller)

    app = _DummyApp()
    app._config.controller.key_move_layout = layout
    handler = KeyboardHandler(app)
    move_speed = app._config.marker.move_speed

    poller.pressed = {forward, right}
    normalized = move_speed / math.sqrt(2)
    assert handler.update(0.016) == pytest.approx((normalized, normalized, 0.0))

    poller.pressed = {back, left}
    assert handler.update(0.016) == pytest.approx((-normalized, -normalized, 0.0))


# ---------------------------------------------------------------------------
# Hardware emission: keyboard to InputEventBus
# ---------------------------------------------------------------------------

from openfollow.input.events import InputEventBus, KeyEvent  # noqa: E402


def test_event_bus_press_event_emits_on_polled_key(monkeypatch) -> None:
    """Pressing any key in ``POLLED_KEYS`` emits a press ``KeyEvent``
    on the bus the next ``poll_discrete_keys`` runs. Letters that
    aren't movement keys (e.g. 'q') exercise the broadened
    polling – the existing legacy ``_pending_presses`` only
    captures the discrete-action subset, but the bus sees every
    polled key."""
    poller = _FakePoller()
    monkeypatch.setattr(keyboard_module, "create_keyboard_poller", lambda: poller)
    bus = InputEventBus()
    seen: list[KeyEvent] = []
    bus.subscribe_key(seen.append)

    handler = KeyboardHandler(_DummyApp(), event_bus=bus)
    poller.pressed = {"q"}
    handler.poll_discrete_keys()

    assert seen == [
        KeyEvent(key="q", modifiers=frozenset(), edge="press"),
    ]


def test_event_bus_release_event_emits_on_polled_key(monkeypatch) -> None:
    """Holding then releasing a key emits press, then release, on
    the next two polls – one event per edge transition."""
    poller = _FakePoller()
    monkeypatch.setattr(keyboard_module, "create_keyboard_poller", lambda: poller)
    bus = InputEventBus()
    seen: list[KeyEvent] = []
    bus.subscribe_key(seen.append)

    handler = KeyboardHandler(_DummyApp(), event_bus=bus)
    poller.pressed = {"q"}
    handler.poll_discrete_keys()
    poller.pressed = set()
    handler.poll_discrete_keys()

    assert seen == [
        KeyEvent(key="q", modifiers=frozenset(), edge="press"),
        KeyEvent(key="q", modifiers=frozenset(), edge="release"),
    ]


def test_event_bus_held_key_fires_on_press_exactly_once(monkeypatch) -> None:
    """Held key fires on press exactly once; polling emits only initial event."""
    poller = _FakePoller()
    monkeypatch.setattr(keyboard_module, "create_keyboard_poller", lambda: poller)
    bus = InputEventBus()
    seen: list[KeyEvent] = []
    bus.subscribe_key(seen.append)

    handler = KeyboardHandler(_DummyApp(), event_bus=bus)
    poller.pressed = {"q"}
    for _ in range(10):
        handler.poll_discrete_keys()

    assert seen == [
        KeyEvent(key="q", modifiers=frozenset(), edge="press"),
    ]


def test_event_bus_carries_modifier_state(monkeypatch) -> None:
    """``KeyEvent.modifiers`` reflects the held modifier keys at
    the moment the edge is detected. Operators binding ``Shift+F1``
    rely on this stamp matching their ``HotkeyTrigger.modifiers``."""
    poller = _FakePoller()
    monkeypatch.setattr(keyboard_module, "create_keyboard_poller", lambda: poller)
    bus = InputEventBus()
    seen: list[KeyEvent] = []
    bus.subscribe_key(seen.append)

    handler = KeyboardHandler(_DummyApp(), event_bus=bus)
    # Shift held, then 'q' pressed alongside it.
    poller.pressed = {"Shift", "q"}
    handler.poll_discrete_keys()

    # Both Shift and q press events fire this frame; the modifier
    # snapshot includes ``"shift"`` because Shift is held at poll
    # time. (The Shift event itself also carries ``"shift"`` in its
    # modifier set; that's an acceptable quirk – operators don't
    # bind modifier keys themselves as hotkeys in practice.)
    q_events = [e for e in seen if e.key == "q"]
    assert q_events == [
        KeyEvent(key="q", modifiers=frozenset({"shift"}), edge="press"),
    ]


def test_event_bus_no_emission_when_bus_is_none(monkeypatch) -> None:
    """``event_bus=None`` (the default) is the headless / test-harness
    code path – the keyboard handler still drives ``_pending_presses``
    for the legacy app-orchestration consumer but emits no bus
    events. Crucially, ``_current_modifiers`` is not even called
    (saves four ``is_key_pressed`` syscalls per frame)."""
    poller = _FakePoller()
    monkeypatch.setattr(keyboard_module, "create_keyboard_poller", lambda: poller)
    handler = KeyboardHandler(_DummyApp(), event_bus=None)
    poller.pressed = {"q"}
    handler.poll_discrete_keys()
    # No bus → no observable emission. The discrete-action queue
    # still works for keys mapped in controller config (the existing
    # legacy contract), but 'q' isn't a discrete action by default,
    # so nothing lands there either.
    assert handler.consume_key_presses() == []


def test_event_bus_modifier_snapshot_includes_all_canonical_names(monkeypatch) -> None:
    """The keyboard handler's modifier snapshot maps every physical
    modifier key to its canonical lower-case name (``shift`` / ``ctrl``
    / ``alt`` / ``cmd``) – these are the exact names
    ``HotkeyTrigger.modifiers`` expects, so a hotkey row configured for
    e.g. ``ctrl+shift+r`` matches when the operator holds those
    modifiers + the trigger key."""
    poller = _FakePoller()
    monkeypatch.setattr(keyboard_module, "create_keyboard_poller", lambda: poller)
    bus = InputEventBus()
    seen: list[KeyEvent] = []
    bus.subscribe_key(seen.append)

    handler = KeyboardHandler(_DummyApp(), event_bus=bus)
    # All four modifiers held at once.
    poller.pressed = {"Shift", "Control", "Alt", "Meta", "q"}
    handler.poll_discrete_keys()

    q_events = [e for e in seen if e.key == "q"]
    assert q_events == [
        KeyEvent(
            key="q",
            modifiers=frozenset({"shift", "ctrl", "alt", "cmd"}),
            edge="press",
        ),
    ]


def test_event_bus_unmapped_modifier_key_does_not_appear_in_snapshot(monkeypatch) -> None:
    """A held physical key that isn't in ``_MODIFIER_KEY_TO_NAME`` (for
    example, the regular trigger key itself, or any non-modifier
    letter held alongside the trigger) does **not** leak into the
    ``KeyEvent.modifiers`` snapshot. The handler only consults the
    map; ``KeyEvent.__post_init__`` then filters by the canonical
    set in ``VALID_TRIGGER_MODIFIERS`` as a defensive second pass."""
    poller = _FakePoller()
    monkeypatch.setattr(keyboard_module, "create_keyboard_poller", lambda: poller)
    bus = InputEventBus()
    seen: list[KeyEvent] = []
    bus.subscribe_key(seen.append)

    handler = KeyboardHandler(_DummyApp(), event_bus=bus)
    # Hold ``a`` + ``b`` (both letters in POLLED_KEYS, neither a
    # modifier) plus the trigger key ``q``. The press for ``a`` and
    # ``b`` themselves arrive as their own events; what we're pinning
    # here is that ``q``'s modifier-snapshot doesn't include ``a`` or
    # ``b`` even though they're held when ``q`` was pressed.
    poller.pressed = {"a", "b", "q"}
    handler.poll_discrete_keys()

    q_events = [e for e in seen if e.key == "q"]
    assert len(q_events) == 1
    # The modifier set is empty: no canonical modifier was held, and
    # the unrelated letters never enter the snapshot.
    assert q_events[0].modifiers == frozenset()


def test_keyboard_handler_clear_resets_poller_and_edge_state(monkeypatch) -> None:
    """clear() drops the fallback poller's held keys AND the handler's edge
    state together, so the next poll sees no stale ``was_pressed`` (a held key
    can't keep driving the marker after control moves elsewhere)."""
    poller = FallbackKeyboardPoller()
    monkeypatch.setattr(keyboard_module, "create_keyboard_poller", lambda: poller)
    handler = KeyboardHandler(_DummyApp())

    poller.on_key_down("w")
    handler._prev_key_state["w"] = True
    assert poller.is_key_pressed("w") is True

    handler.clear()

    assert poller.is_key_pressed("w") is False
    assert handler._prev_key_state == {}


def test_keyboard_handler_clear_tolerates_poller_without_clear(monkeypatch) -> None:
    """The macOS poller has no clear() (hardware-polled); clear() must still
    reset the handler edge state without raising."""
    poller = _FakePoller()  # no clear() method
    monkeypatch.setattr(keyboard_module, "create_keyboard_poller", lambda: poller)
    handler = KeyboardHandler(_DummyApp())
    handler._prev_key_state["w"] = True

    handler.clear()  # must not raise

    assert handler._prev_key_state == {}


def test_poll_discrete_prunes_edge_state_for_unpolled_keys(monkeypatch) -> None:
    """A key that leaves the polled set (discrete-set shrink on a rebind, or
    the bus-less narrowing) must not strand a True ``was_pressed`` – its edge
    state is pruned so a later re-add re-sees the press edge."""
    poller = _FakePoller()
    monkeypatch.setattr(keyboard_module, "create_keyboard_poller", lambda: poller)
    handler = KeyboardHandler(_DummyApp(), event_bus=None)  # bus None → narrowed poll set

    # Seed edge state for a key that is not in the (narrowed) polled set.
    handler._prev_key_state["unbound_key"] = True
    handler.poll_discrete_keys()

    assert "unbound_key" not in handler._prev_key_state
