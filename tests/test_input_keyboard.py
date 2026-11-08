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


def test_clear_messages_key_is_queued_on_press(monkeypatch) -> None:
    poller = _FakePoller()
    monkeypatch.setattr(keyboard_module, "create_keyboard_poller", lambda: poller)
    app = _DummyApp()
    app._config.controller.key_clear_messages = "c"
    handler = KeyboardHandler(app)

    poller.pressed = {"c"}
    handler.poll_discrete_keys()
    assert handler.consume_key_presses() == ["c"]


@pytest.mark.parametrize(("field", "key"), [("key_pathfinder", "p"), ("key_toggle_paths", "o")])
def test_path_keys_are_queued_on_press(monkeypatch, field: str, key: str) -> None:
    poller = _FakePoller()
    monkeypatch.setattr(keyboard_module, "create_keyboard_poller", lambda: poller)
    app = _DummyApp()
    setattr(app._config.controller, field, key)
    handler = KeyboardHandler(app)

    poller.pressed = {key}
    handler.poll_discrete_keys()
    assert handler.consume_key_presses() == [key]


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


# ---------------------------------------------------------------------------
# macOS: the poll reads the whole system's keyboard, so it acts only on presses
# that started while the Operator Screen owned it. ``_FakePoller`` stands in
# for Quartz; ``FallbackKeyboardPoller`` is the Linux path, which GTK feeds
# with this window's own key events only.
# ---------------------------------------------------------------------------


def _handler_with_bus(monkeypatch, poller=None):
    poller = poller if poller is not None else _FakePoller()
    monkeypatch.setattr(keyboard_module, "create_keyboard_poller", lambda: poller)
    bus = InputEventBus()
    seen: list[KeyEvent] = []
    bus.subscribe_key(seen.append)
    return KeyboardHandler(_DummyApp(), event_bus=bus), poller, seen


def test_keys_typed_while_another_window_owns_the_keyboard_act_nowhere(monkeypatch) -> None:
    """Typing into Spotlight over the Operator Screen: the poll still reads the
    keys, but no action, bus event, held key or movement comes of them."""
    handler, poller, seen = _handler_with_bus(monkeypatch)

    poller.pressed = {"x", "h", "w"}
    handler.poll_discrete_keys(owned=False)

    assert handler.consume_key_presses() == []
    assert seen == []
    assert handler.keys == set()
    assert handler.update(0.016) is None


def test_press_from_another_window_stays_ignored_until_released(monkeypatch) -> None:
    handler, poller, seen = _handler_with_bus(monkeypatch)

    poller.pressed = {"x", "w"}
    handler.poll_discrete_keys(owned=False)
    handler.poll_discrete_keys(owned=True)

    assert handler.consume_key_presses() == []
    assert handler.keys == set()
    assert handler.update(0.016) is None

    poller.pressed = set()
    handler.poll_discrete_keys(owned=True)
    poller.pressed = {"x"}
    handler.poll_discrete_keys(owned=True)
    assert handler.consume_key_presses() == ["x"]
    assert [e.edge for e in seen] == ["press"]


def test_press_seen_as_the_keyboard_comes_back_is_ignored(monkeypatch) -> None:
    """The Escape that closes Spotlight can be down before the poll next runs,
    on the same tick that finds the Operator Screen owning the keyboard again."""
    handler, poller, seen = _handler_with_bus(monkeypatch)

    handler.poll_discrete_keys(owned=False)
    poller.pressed = {"Escape"}
    handler.poll_discrete_keys(owned=True)

    assert handler.consume_key_presses() == []
    assert seen == []

    poller.pressed = set()
    handler.poll_discrete_keys(owned=True)
    poller.pressed = {"Escape"}
    handler.poll_discrete_keys(owned=True)
    assert handler.consume_key_presses() == ["Escape"]


def test_bus_sees_a_release_only_when_it_saw_the_press(monkeypatch) -> None:
    handler, poller, seen = _handler_with_bus(monkeypatch)

    poller.pressed = {"q"}
    handler.poll_discrete_keys(owned=False)
    poller.pressed = set()
    handler.poll_discrete_keys(owned=True)
    assert seen == []

    poller.pressed = {"q"}
    handler.poll_discrete_keys(owned=True)
    poller.pressed = set()
    handler.poll_discrete_keys(owned=False)
    assert [(e.key, e.edge) for e in seen] == [("q", "press"), ("q", "release")]


def test_clear_keeps_a_press_from_another_window_ignored(monkeypatch) -> None:
    """``clear()`` (focus-out, leaving a menu) resets edge state, so the poll
    sees every held key afresh; one pressed elsewhere must not then act."""
    handler, poller, _seen = _handler_with_bus(monkeypatch)

    poller.pressed = {"x"}
    handler.poll_discrete_keys(owned=False)
    handler.clear()
    handler.poll_discrete_keys(owned=True)

    assert handler.consume_key_presses() == []


@pytest.mark.parametrize(
    ("modifier", "canonical"),
    [("Meta", "cmd"), ("Control", "ctrl"), ("Alt", "alt")],
)
def test_shortcut_modifier_keeps_the_press_from_acting(monkeypatch, modifier, canonical) -> None:
    """Cmd+H must not toggle help. The bus still gets the press with its
    modifiers, so an OSC hotkey bound to that combination keeps firing."""
    handler, poller, seen = _handler_with_bus(monkeypatch)

    poller.pressed = {modifier, "h"}
    handler.poll_discrete_keys()

    assert handler.consume_key_presses() == []
    assert KeyEvent(key="h", modifiers=frozenset({canonical}), edge="press") in seen


def test_modifier_bound_to_an_action_is_not_a_shortcut_modifier(monkeypatch) -> None:
    """Control on Z-: holding it alongside W drives both, whichever came first."""
    app = _DummyApp()
    app._config.controller.key_move_z_down = "Control"
    poller = _FakePoller()
    monkeypatch.setattr(keyboard_module, "create_keyboard_poller", lambda: poller)
    handler = KeyboardHandler(app, event_bus=InputEventBus())
    speed = app._config.marker.move_speed

    poller.pressed = {"w"}
    handler.poll_discrete_keys()
    poller.pressed = {"w", "Control"}
    handler.poll_discrete_keys()
    assert handler.update(0.016) == pytest.approx((0.0, speed, -speed))

    poller.pressed = {"Control"}
    handler.poll_discrete_keys()
    poller.pressed = {"Control", "w"}
    handler.poll_discrete_keys()
    poller.pressed = {"w"}
    handler.poll_discrete_keys()
    assert handler.update(0.016) == pytest.approx((0.0, speed, 0.0))


def test_alt_bound_to_an_action_leaves_other_action_keys_working(monkeypatch) -> None:
    app = _DummyApp()
    app._config.controller.key_speed_up = "Alt"
    poller = _FakePoller()
    monkeypatch.setattr(keyboard_module, "create_keyboard_poller", lambda: poller)
    handler = KeyboardHandler(app, event_bus=InputEventBus())

    poller.pressed = {"Alt"}
    handler.poll_discrete_keys()
    poller.pressed = {"Alt", "h"}
    handler.poll_discrete_keys()

    assert handler.consume_key_presses() == ["Alt", "h"]


def test_command_stays_a_shortcut_modifier_beside_a_bound_control(monkeypatch) -> None:
    app = _DummyApp()
    app._config.controller.key_move_z_down = "Control"
    poller = _FakePoller()
    monkeypatch.setattr(keyboard_module, "create_keyboard_poller", lambda: poller)
    handler = KeyboardHandler(app, event_bus=InputEventBus())

    poller.pressed = {"Meta", "h"}
    handler.poll_discrete_keys()

    assert handler.consume_key_presses() == []


def test_rebinding_a_modifier_updates_the_shortcut_modifiers(monkeypatch) -> None:
    import dataclasses

    app = _DummyApp()
    poller = _FakePoller()
    monkeypatch.setattr(keyboard_module, "create_keyboard_poller", lambda: poller)
    handler = KeyboardHandler(app, event_bus=InputEventBus())
    poller.pressed = {"Control", "h"}
    handler.poll_discrete_keys()
    assert handler.consume_key_presses() == []

    app._config.controller = dataclasses.replace(app._config.controller, key_move_z_down="Control")
    poller.pressed = set()
    handler.poll_discrete_keys()
    poller.pressed = {"Control", "h"}
    handler.poll_discrete_keys()
    assert handler.consume_key_presses() == ["h"]


def test_bus_gets_the_release_of_a_press_it_saw_before_focus_out(monkeypatch) -> None:
    """Cmd+Space opens Spotlight: the bus saw both presses, so it gets both
    releases even though focus-out reset the edge state in between."""
    handler, poller, seen = _handler_with_bus(monkeypatch)

    poller.pressed = {"Meta", "Space"}
    handler.poll_discrete_keys(owned=True)
    handler.clear()
    handler.poll_discrete_keys(owned=False)
    poller.pressed = set()
    handler.poll_discrete_keys(owned=False)

    assert sorted((e.key, e.edge) for e in seen) == [
        ("Meta", "press"),
        ("Meta", "release"),
        ("Space", "press"),
        ("Space", "release"),
    ]


def test_bus_gets_a_release_that_happens_while_edge_state_is_reset(monkeypatch) -> None:
    handler, poller, seen = _handler_with_bus(monkeypatch)

    poller.pressed = {"q"}
    handler.poll_discrete_keys(owned=True)
    handler.clear()
    poller.pressed = set()
    handler.poll_discrete_keys(owned=False)

    assert [(e.key, e.edge) for e in seen] == [("q", "press"), ("q", "release")]


def test_shift_is_not_a_shortcut_modifier(monkeypatch) -> None:
    handler, poller, _seen = _handler_with_bus(monkeypatch)

    poller.pressed = {"Shift", "x"}
    handler.poll_discrete_keys()

    assert handler.consume_key_presses() == ["x"]
    assert handler.keys == {"Shift", "x"}


def test_shortcut_key_stays_ignored_after_the_modifier_is_released(monkeypatch) -> None:
    """Cmd+M, letting go of Cmd first: M must not open the Settings menu,
    which reads the held set rather than a press edge."""
    handler, poller, _seen = _handler_with_bus(monkeypatch)

    poller.pressed = {"Meta", "m"}
    handler.poll_discrete_keys()
    assert handler.keys == {"Meta"}

    poller.pressed = {"m"}
    handler.poll_discrete_keys()
    assert handler.keys == set()

    poller.pressed = set()
    handler.poll_discrete_keys()
    poller.pressed = {"m"}
    handler.poll_discrete_keys()
    assert handler.keys == {"m"}


def test_movement_pauses_while_a_shortcut_modifier_is_held(monkeypatch) -> None:
    handler, poller, _seen = _handler_with_bus(monkeypatch)

    poller.pressed = {"w"}
    handler.poll_discrete_keys()
    assert handler.update(0.016) is not None

    poller.pressed = {"w", "Meta"}
    handler.poll_discrete_keys()
    assert handler.update(0.016) is None

    poller.pressed = {"w"}
    handler.poll_discrete_keys()
    assert handler.update(0.016) is not None


def test_key_pressed_for_a_shortcut_never_moves_the_marker(monkeypatch) -> None:
    """Cmd+S, letting go of Cmd first: S must not then drive the marker."""
    handler, poller, _seen = _handler_with_bus(monkeypatch)

    poller.pressed = {"Meta", "s"}
    handler.poll_discrete_keys()
    assert handler.update(0.016) is None

    poller.pressed = {"s"}
    handler.poll_discrete_keys()
    assert handler.update(0.016) is None


def test_text_field_drops_the_gtk_copy_of_a_polled_press(monkeypatch) -> None:
    """The Enter that opens the URL editor reaches it again as a GTK key-down,
    plus its auto-repeats; none of them may confirm the editor."""
    handler, poller, _seen = _handler_with_bus(monkeypatch)

    poller.pressed = {"Enter"}
    handler.poll_discrete_keys(owned=True)
    assert handler.consume_key_presses() == ["Enter"]

    assert handler.take_text_key("Enter") is False
    handler.poll_discrete_keys(owned=False)
    assert handler.take_text_key("Enter") is False

    poller.pressed = set()
    handler.poll_discrete_keys(owned=False)
    poller.pressed = {"Enter"}
    handler.poll_discrete_keys(owned=False)
    assert handler.take_text_key("Enter") is True
    assert handler.take_text_key("a") is True


def test_press_a_text_field_took_first_never_reaches_the_poll(monkeypatch) -> None:
    """GTK can deliver Escape before the poll sees it: the editor closes onto
    the Settings menu, which must not then take the same Escape too."""
    handler, poller, seen = _handler_with_bus(monkeypatch)

    assert handler.take_text_key("Escape") is True
    poller.pressed = {"Escape"}
    handler.poll_discrete_keys(owned=True)

    assert handler.consume_key_presses() == []
    assert seen == []


def test_quick_tap_a_text_field_took_does_not_swallow_the_next_press(monkeypatch) -> None:
    """A tap shorter than one poll is never seen held; the next press counts."""
    handler, poller, _seen = _handler_with_bus(monkeypatch)

    assert handler.take_text_key("Enter") is True
    handler.poll_discrete_keys(owned=True)
    poller.pressed = {"Enter"}
    handler.poll_discrete_keys(owned=True)

    assert handler.consume_key_presses() == ["Enter"]


def test_late_gtk_copy_of_a_short_polled_tap_is_still_dropped(monkeypatch) -> None:
    """A quick Enter on RTSP: the swap blocks the main loop, the key is already
    up when the poll next runs, and only then does GTK deliver its key-down."""
    handler, poller, _seen = _handler_with_bus(monkeypatch)
    now = [100.0]
    handler._clock = lambda: now[0]

    poller.pressed = {"Enter"}
    handler.poll_discrete_keys(owned=True)
    poller.pressed = set()
    now[0] += 0.016
    handler.poll_discrete_keys(owned=False)
    now[0] += 0.2
    assert handler.take_text_key("Enter") is False

    now[0] += 1.0
    handler.poll_discrete_keys(owned=False)
    assert handler.take_text_key("Enter") is True


def test_second_press_after_the_gtk_copy_arrived_goes_to_the_text_field(monkeypatch) -> None:
    """Once GTK has delivered the copy of a polled press, nothing more of that
    press is due: a deliberate second Enter right after it is the editor's,
    even when GTK delivers it before the poll sees it."""
    handler, poller, _seen = _handler_with_bus(monkeypatch)
    now = [100.0]
    handler._clock = lambda: now[0]

    poller.pressed = {"Enter"}
    handler.poll_discrete_keys(owned=True)
    assert handler.take_text_key("Enter") is False
    poller.pressed = set()
    handler.poll_discrete_keys(owned=False)
    now[0] += 0.2

    assert handler.take_text_key("Enter") is True


def test_gtk_copy_delivered_before_the_poll_claims_counts_too(monkeypatch) -> None:
    """GTK can deliver the Enter that picks RTSP to the source list before the
    poll sees it; the editor the poll then opens owes that press nothing."""
    handler, poller, _seen = _handler_with_bus(monkeypatch)
    now = [100.0]
    handler._clock = lambda: now[0]

    handler.on_key_down({"key": "Enter"})
    poller.pressed = {"Enter"}
    handler.poll_discrete_keys(owned=True)
    assert handler.consume_key_presses() == ["Enter"]
    poller.pressed = set()
    handler.poll_discrete_keys(owned=False)
    now[0] += 0.2

    assert handler.take_text_key("Enter") is True


def test_gtk_copy_delivered_to_a_menu_after_the_poll_claims_counts(monkeypatch) -> None:
    handler, poller, _seen = _handler_with_bus(monkeypatch)
    now = [100.0]
    handler._clock = lambda: now[0]

    poller.pressed = {"Enter"}
    handler.poll_discrete_keys(owned=True)
    handler.on_key_down({"key": "Enter"})
    poller.pressed = set()
    handler.poll_discrete_keys(owned=True)
    now[0] += 0.2

    assert handler.take_text_key("Enter") is True


def test_gtk_key_down_the_poll_never_sees_held_is_forgotten(monkeypatch) -> None:
    """A tap shorter than one poll leaves no GTK mark for the next press, whose
    own copy is then still due after its release."""
    handler, poller, _seen = _handler_with_bus(monkeypatch)
    now = [100.0]
    handler._clock = lambda: now[0]

    handler.on_key_down({"key": "Enter"})
    handler.poll_discrete_keys(owned=True)
    poller.pressed = {"Enter"}
    handler.poll_discrete_keys(owned=True)
    poller.pressed = set()
    handler.poll_discrete_keys(owned=False)
    now[0] += 0.2

    assert handler.take_text_key("Enter") is False


def test_next_press_the_poll_sees_belongs_to_the_text_field(monkeypatch) -> None:
    handler, poller, _seen = _handler_with_bus(monkeypatch)
    now = [100.0]
    handler._clock = lambda: now[0]

    poller.pressed = {"Enter"}
    handler.poll_discrete_keys(owned=True)
    poller.pressed = set()
    handler.poll_discrete_keys(owned=False)
    poller.pressed = {"Enter"}
    handler.poll_discrete_keys(owned=False)

    assert handler.take_text_key("Enter") is True


def test_linux_keyboard_ignores_ownership_and_shortcut_modifiers(monkeypatch) -> None:
    """GTK feeds the fallback poller from this window's own key events, so the
    macOS rules leave it exactly as it was."""
    poller = FallbackKeyboardPoller()
    handler, _poller, seen = _handler_with_bus(monkeypatch, poller)

    poller.on_key_down("Control")
    poller.on_key_down("x")
    handler.poll_discrete_keys(owned=False)
    assert handler.consume_key_presses() == ["x"]
    assert handler.keys == {"Control", "x"}
    assert KeyEvent(key="x", modifiers=frozenset({"ctrl"}), edge="press") in seen

    poller.on_key_down("w")
    assert handler.update(0.016) is not None

    assert handler.take_text_key("x") is True
    assert handler.take_text_key("Enter") is True
    poller.on_key_down("Enter")
    handler.poll_discrete_keys(owned=True)
    assert handler.consume_key_presses() == ["Enter"]
