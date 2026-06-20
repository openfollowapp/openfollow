# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Unit tests for the input event bus.

Pure pub/sub component; no I/O, no threads beyond what the bus itself
synthesises. Covers:

- Subscribe / emit / fan-out to multiple handlers.
- Idempotent unsubscribe via the returned callable.
- Handler errors are isolated – one raising callback doesn't sink the
  rest of the dispatch.
- Subscribers added or removed mid-emit don't see torn state because
  ``emit_*`` snapshots the handler list under the lock first.
"""

from __future__ import annotations

import logging

import pytest

from openfollow.input.events import (
    ButtonEvent,
    InputEventBus,
    KeyEvent,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Event dataclasses
# ---------------------------------------------------------------------------


class TestKeyEvent:
    def test_default_modifiers_is_empty_frozenset(self) -> None:
        ev = KeyEvent(key="F1")
        assert ev.modifiers == frozenset()

    def test_default_edge_is_press(self) -> None:
        assert KeyEvent(key="F1").edge == "press"

    def test_modifiers_carry_through(self) -> None:
        ev = KeyEvent(key="F1", modifiers=frozenset({"ctrl", "shift"}))
        assert ev.modifiers == frozenset({"ctrl", "shift"})

    def test_equal_when_same_fields(self) -> None:
        a = KeyEvent(key="F1", modifiers=frozenset({"ctrl"}), edge="press")
        b = KeyEvent(key="F1", modifiers=frozenset({"ctrl"}), edge="press")
        assert a == b

    def test_modifiers_normalised_to_lower_case(self) -> None:
        """Modifiers normalized to lower-case for consistent config matching."""
        ev = KeyEvent(key="F1", modifiers=frozenset({"Shift", "CTRL"}))
        assert ev.modifiers == frozenset({"shift", "ctrl"})

    def test_modifiers_strip_surrounding_whitespace(self) -> None:
        ev = KeyEvent(
            key="F1",
            modifiers=frozenset({"  shift  ", "ctrl"}),
        )
        assert ev.modifiers == frozenset({"shift", "ctrl"})

    def test_modifiers_filter_unknown_names(self) -> None:
        """Unknown modifier names ('hyper', etc.) drop out – same
        robustness contract the trigger config layer already uses."""
        ev = KeyEvent(key="F1", modifiers=frozenset({"ctrl", "hyper"}))
        assert ev.modifiers == frozenset({"ctrl"})

    def test_modifiers_accepts_list_or_set_input(self) -> None:
        """Emit-site callers can pass any iterable of strings, not
        just frozenset – eases future hardware-emit code. The
        constructor parameter is typed ``Iterable[str]`` so list /
        set / generator inputs are type-correct without a
        ``# type: ignore``."""
        ev = KeyEvent(key="F1", modifiers=["ctrl", "shift"])
        assert ev.modifiers == frozenset({"ctrl", "shift"})
        ev2 = KeyEvent(key="F1", modifiers={"ctrl", "shift"})
        assert ev2.modifiers == frozenset({"ctrl", "shift"})
        ev3 = KeyEvent(key="F1", modifiers=(m for m in ("ctrl", "shift")))
        assert ev3.modifiers == frozenset({"ctrl", "shift"})

    def test_modifiers_non_iterable_collapses_to_empty(self) -> None:
        """Defensive: malformed input (None, an int) collapses to
        empty rather than raising."""
        ev = KeyEvent(key="F1", modifiers=None)  # type: ignore[arg-type]
        assert ev.modifiers == frozenset()
        ev2 = KeyEvent(key="F1", modifiers=42)  # type: ignore[arg-type]
        assert ev2.modifiers == frozenset()

    def test_modifiers_non_string_entries_dropped(self) -> None:
        """A mixed iterable with a non-string entry skips that entry
        rather than raising on ``.lower()``."""
        ev = KeyEvent(
            key="F1",
            modifiers=frozenset({"ctrl", 42}),  # type: ignore[arg-type]
        )
        assert ev.modifiers == frozenset({"ctrl"})


class TestButtonEvent:
    def test_default_controller_index_is_zero(self) -> None:
        assert ButtonEvent(button="A").controller_index == 0

    def test_default_edge_is_press(self) -> None:
        assert ButtonEvent(button="A").edge == "press"


# ---------------------------------------------------------------------------
# InputEventBus pub/sub
# ---------------------------------------------------------------------------


class TestSubscribeEmit:
    def test_subscribe_key_receives_emitted_event(self) -> None:
        bus = InputEventBus()
        seen: list[KeyEvent] = []
        bus.subscribe_key(seen.append)
        bus.emit_key(KeyEvent(key="F1"))
        assert seen == [KeyEvent(key="F1")]

    def test_subscribe_button_receives_emitted_event(self) -> None:
        bus = InputEventBus()
        seen: list[ButtonEvent] = []
        bus.subscribe_button(seen.append)
        bus.emit_button(ButtonEvent(button="A"))
        assert seen == [ButtonEvent(button="A")]

    def test_multiple_key_subscribers_each_receive_events(self) -> None:
        """Multiple OSC bindings on the same key fire independently –
        the bus fans out to every subscriber."""
        bus = InputEventBus()
        seen_a: list[KeyEvent] = []
        seen_b: list[KeyEvent] = []
        bus.subscribe_key(seen_a.append)
        bus.subscribe_key(seen_b.append)
        bus.emit_key(KeyEvent(key="F1"))
        assert seen_a == seen_b == [KeyEvent(key="F1")]

    def test_no_subscribers_emit_is_noop(self) -> None:
        bus = InputEventBus()
        # No handlers; emit must not raise.
        bus.emit_key(KeyEvent(key="F1"))
        bus.emit_button(ButtonEvent(button="A"))

    def test_key_handlers_are_independent_of_button_handlers(self) -> None:
        bus = InputEventBus()
        keys: list[KeyEvent] = []
        buttons: list[ButtonEvent] = []
        bus.subscribe_key(keys.append)
        bus.subscribe_button(buttons.append)
        bus.emit_key(KeyEvent(key="F1"))
        bus.emit_button(ButtonEvent(button="A"))
        assert len(keys) == 1 and len(buttons) == 1


class TestUnsubscribe:
    def test_unsubscribe_callable_removes_key_handler(self) -> None:
        bus = InputEventBus()
        seen: list[KeyEvent] = []
        unsub = bus.subscribe_key(seen.append)
        bus.emit_key(KeyEvent(key="F1"))
        unsub()
        bus.emit_key(KeyEvent(key="F2"))
        assert seen == [KeyEvent(key="F1")]  # F2 not delivered

    def test_unsubscribe_callable_removes_button_handler(self) -> None:
        bus = InputEventBus()
        seen: list[ButtonEvent] = []
        unsub = bus.subscribe_button(seen.append)
        bus.emit_button(ButtonEvent(button="A"))
        unsub()
        bus.emit_button(ButtonEvent(button="B"))
        assert seen == [ButtonEvent(button="A")]

    def test_unsubscribe_is_idempotent(self) -> None:
        bus = InputEventBus()
        unsub_key = bus.subscribe_key(lambda _: None)
        unsub_btn = bus.subscribe_button(lambda _: None)
        unsub_key()
        unsub_key()  # must not raise
        unsub_btn()
        unsub_btn()  # must not raise

    def test_unsubscribe_one_of_many_leaves_others_alone(self) -> None:
        bus = InputEventBus()
        seen_a: list[KeyEvent] = []
        seen_b: list[KeyEvent] = []
        unsub_a = bus.subscribe_key(seen_a.append)
        bus.subscribe_key(seen_b.append)
        unsub_a()
        bus.emit_key(KeyEvent(key="F1"))
        assert seen_a == []
        assert seen_b == [KeyEvent(key="F1")]


class TestErrorIsolation:
    def test_raising_key_handler_does_not_sink_dispatch(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        bus = InputEventBus()
        seen: list[KeyEvent] = []

        def raising(_event: KeyEvent) -> None:
            raise RuntimeError("boom")

        bus.subscribe_key(raising)
        bus.subscribe_key(seen.append)
        with caplog.at_level(logging.ERROR, logger="openfollow.input.events"):
            bus.emit_key(KeyEvent(key="F1"))
        assert seen == [KeyEvent(key="F1")]
        assert any("key handler raised" in r.message for r in caplog.records)

    def test_raising_button_handler_does_not_sink_dispatch(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        bus = InputEventBus()
        seen: list[ButtonEvent] = []

        def raising(_event: ButtonEvent) -> None:
            raise RuntimeError("boom")

        bus.subscribe_button(raising)
        bus.subscribe_button(seen.append)
        with caplog.at_level(logging.ERROR, logger="openfollow.input.events"):
            bus.emit_button(ButtonEvent(button="A"))
        assert seen == [ButtonEvent(button="A")]
        assert any("button handler raised" in r.message for r in caplog.records)


class TestEmitSnapshotIsolation:
    def test_handler_unsubscribing_during_emit_does_not_skip_others(
        self,
    ) -> None:
        """``emit_*`` snapshots the handler list before iterating, so
        a callback that unsubscribes itself mid-dispatch doesn't
        accidentally skip the next handler in the list (which would
        happen if we iterated the live list while mutating it)."""
        bus = InputEventBus()
        log: list[str] = []
        unsubs: dict[str, object] = {}

        def first(_event: KeyEvent) -> None:
            log.append("first")
            # Unsubscribe ourselves mid-dispatch – the second handler
            # must still see the event because the snapshot was taken
            # before iteration started.
            unsubs["first"]()  # type: ignore[operator]

        def second(_event: KeyEvent) -> None:
            log.append("second")

        unsubs["first"] = bus.subscribe_key(first)
        bus.subscribe_key(second)
        bus.emit_key(KeyEvent(key="F1"))
        assert log == ["first", "second"]
