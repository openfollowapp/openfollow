# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Unit tests for the MIDI Learn web broker.

The broker bridges the OSC binding form's "Capture" button to the
running :class:`MidiSubsystem`. Tests drive a fake substrate without
needing a real rtmidi backend; the fake's ``subscribe`` records
callbacks the test can pump events through directly.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

import pytest

from openfollow.input.midi import MidiEvent
from openfollow.web.midi_capture import MidiCaptureBroker

pytestmark = pytest.mark.unit


class _FakeMidiSubsystem:
    """Minimal stand-in for :class:`MidiSubsystem`. Only ``subscribe``
    is exercised by the broker – the test pumps events by invoking
    every recorded callback directly (matches the real substrate's
    listener-thread fan-out shape)."""

    def __init__(self) -> None:
        self._subscribers: list[Callable[[MidiEvent], None]] = []
        self.subscribe_calls: int = 0

    def subscribe(
        self,
        callback: Callable[[MidiEvent], None],
    ) -> Callable[[], None]:
        self.subscribe_calls += 1
        self._subscribers.append(callback)

        def _unsubscribe() -> None:
            try:
                self._subscribers.remove(callback)
            except ValueError:
                pass

        return _unsubscribe

    def emit(self, event: MidiEvent) -> None:
        # Iterate a snapshot so a callback that unsubscribes itself
        # doesn't trip "list modified during iteration" – the real
        # substrate does the same.
        for sub in list(self._subscribers):
            sub(event)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


def _event(**overrides: Any) -> MidiEvent:
    """Build a default-shaped :class:`MidiEvent`. Tests override only
    the fields they're asserting on."""
    defaults: dict[str, Any] = {
        "type": "control_change",
        "channel": 1,
        "number": 7,
        "value": 64,
        "patch_id": 1,
        "timestamp": 0.0,
    }
    defaults.update(overrides)
    return MidiEvent(**defaults)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TestIdleState:
    def test_default_poll_is_idle(self) -> None:
        broker = MidiCaptureBroker(_FakeMidiSubsystem())  # type: ignore[arg-type]
        assert broker.poll() == {"status": "idle"}

    def test_idle_does_not_subscribe(self) -> None:
        midi = _FakeMidiSubsystem()
        MidiCaptureBroker(midi)  # type: ignore[arg-type]
        assert midi.subscribe_calls == 0


class TestArmAndCapture:
    def test_arm_subscribes_to_substrate(self) -> None:
        midi = _FakeMidiSubsystem()
        broker = MidiCaptureBroker(midi)  # type: ignore[arg-type]
        broker.arm()
        assert midi.subscribe_calls == 1
        assert midi.subscriber_count == 1

    def test_arm_then_poll_reports_waiting(self) -> None:
        midi = _FakeMidiSubsystem()
        broker = MidiCaptureBroker(midi)  # type: ignore[arg-type]
        broker.arm()
        result = broker.poll()
        assert result["status"] == "waiting"
        assert "elapsed_s" in result
        assert result["elapsed_s"] >= 0.0

    def test_event_after_arm_lands_in_slot(self) -> None:
        midi = _FakeMidiSubsystem()
        broker = MidiCaptureBroker(midi)  # type: ignore[arg-type]
        broker.arm()
        midi.emit(_event(value=42))
        result = broker.poll()
        assert result == {
            "status": "captured",
            "patch_id": 1,
            "type": "control_change",
            "channel": 1,
            "number": 7,
            "value": 42,
        }

    def test_capture_drops_subscription(self) -> None:
        """First event consumes the arm – the substrate's
        subscriber list is back to empty so subsequent events
        don't queue into the broker."""
        midi = _FakeMidiSubsystem()
        broker = MidiCaptureBroker(midi)  # type: ignore[arg-type]
        broker.arm()
        midi.emit(_event())
        assert midi.subscriber_count == 0

    def test_capture_returns_to_idle_after_poll_consumes_slot(
        self,
    ) -> None:
        """Poll drains the captured slot – a second poll on the
        same arm sees ``idle``, not the stale capture."""
        midi = _FakeMidiSubsystem()
        broker = MidiCaptureBroker(midi)  # type: ignore[arg-type]
        broker.arm()
        midi.emit(_event())
        broker.poll()  # consume
        assert broker.poll() == {"status": "idle"}

    def test_captured_slot_actually_cleared_after_poll(self) -> None:
        """Poll drains the slot once consumed – assert the internal
        field is ``None`` so the broker doesn't retain the event
        indefinitely."""
        midi = _FakeMidiSubsystem()
        broker = MidiCaptureBroker(midi)  # type: ignore[arg-type]
        broker.arm()
        midi.emit(_event())
        result = broker.poll()
        assert result["status"] == "captured"
        assert broker._captured is None

    def test_first_event_wins_on_burst(self) -> None:
        """A real fader sweep produces dozens of CC values in a
        single second. The first one captures; the rest are
        dropped because the unsubscribe ran. This keeps the
        operator's "Capture" feel deterministic – they wiggle the
        control once and the first edge wins."""
        midi = _FakeMidiSubsystem()
        broker = MidiCaptureBroker(midi)  # type: ignore[arg-type]
        broker.arm()
        midi.emit(_event(value=10))
        midi.emit(_event(value=42))
        midi.emit(_event(value=120))
        result = broker.poll()
        assert result["value"] == 10

    def test_program_change_captures_with_none_number(self) -> None:
        """The substrate emits ``number=None`` for program_change /
        channel_pressure. The broker passes that through verbatim
        so the form's number input lands as empty (matches the
        ``Optional[int]`` shape on :class:`MidiMessageTrigger`)."""
        midi = _FakeMidiSubsystem()
        broker = MidiCaptureBroker(midi)  # type: ignore[arg-type]
        broker.arm()
        midi.emit(_event(type="program_change", number=None, value=5))
        result = broker.poll()
        assert result["status"] == "captured"
        assert result["type"] == "program_change"
        assert result["number"] is None
        assert result["value"] == 5


class TestArmIsIdempotent:
    def test_second_arm_replaces_first(self) -> None:
        """Operator clicked Capture twice. Second arm cancels the
        first subscription so a stale event from the first window
        can't land in the second one's slot."""
        midi = _FakeMidiSubsystem()
        broker = MidiCaptureBroker(midi)  # type: ignore[arg-type]
        broker.arm()
        broker.arm()
        # Exactly one subscription is live (the second arm dropped
        # the first one before installing the new one).
        assert midi.subscriber_count == 1

    def test_second_arm_clears_pending_capture(self) -> None:
        midi = _FakeMidiSubsystem()
        broker = MidiCaptureBroker(midi)  # type: ignore[arg-type]
        broker.arm()
        midi.emit(_event(value=42))
        # Drained-but-not-polled is the same situation; re-arm and
        # confirm the slot is empty regardless.
        broker.arm()
        result = broker.poll()
        assert result["status"] == "waiting"


class TestTimeout:
    def test_poll_returns_timeout_after_window(self) -> None:
        """A short timeout makes the test deterministic without a
        long sleep. The broker's per-instance timeout exists for
        exactly this case – the production timeout is 10 seconds."""
        midi = _FakeMidiSubsystem()
        broker = MidiCaptureBroker(midi, timeout_s=0.05)  # type: ignore[arg-type]
        broker.arm()
        time.sleep(0.06)
        result = broker.poll()
        assert result == {"status": "timeout"}

    def test_timeout_drops_subscription(self) -> None:
        midi = _FakeMidiSubsystem()
        broker = MidiCaptureBroker(midi, timeout_s=0.05)  # type: ignore[arg-type]
        broker.arm()
        time.sleep(0.06)
        broker.poll()  # surfaces the timeout + cleans up
        assert midi.subscriber_count == 0

    def test_timeout_returns_to_idle(self) -> None:
        midi = _FakeMidiSubsystem()
        broker = MidiCaptureBroker(midi, timeout_s=0.05)  # type: ignore[arg-type]
        broker.arm()
        time.sleep(0.06)
        broker.poll()
        assert broker.poll() == {"status": "idle"}


class TestCancel:
    def test_cancel_drops_subscription(self) -> None:
        midi = _FakeMidiSubsystem()
        broker = MidiCaptureBroker(midi)  # type: ignore[arg-type]
        broker.arm()
        broker.cancel()
        assert midi.subscriber_count == 0
        assert broker.poll() == {"status": "idle"}

    def test_cancel_when_idle_is_noop(self) -> None:
        midi = _FakeMidiSubsystem()
        broker = MidiCaptureBroker(midi)  # type: ignore[arg-type]
        broker.cancel()  # must not raise
        assert broker.poll() == {"status": "idle"}


class TestRowIdScope:
    """Concurrent rows must not steal each other's captured events."""

    def test_poll_with_different_row_id_returns_cancelled(self) -> None:
        """Row A armed; Row B polls → cancelled signal so Row B
        doesn't consume Row A's pending capture."""
        midi = _FakeMidiSubsystem()
        broker = MidiCaptureBroker(midi)  # type: ignore[arg-type]
        broker.arm("row-a")
        assert broker.poll("row-b") == {"status": "cancelled"}
        # Row A's poll still reports waiting (its arm is intact).
        assert broker.poll("row-a")["status"] == "waiting"

    def test_re_arming_with_new_row_supersedes_previous(self) -> None:
        """Row A armed → Row B armed (cancels A's subscription) →
        Row A's poll sees cancelled, Row B's poll sees waiting."""
        midi = _FakeMidiSubsystem()
        broker = MidiCaptureBroker(midi)  # type: ignore[arg-type]
        broker.arm("row-a")
        broker.arm("row-b")
        assert broker.poll("row-a") == {"status": "cancelled"}
        assert broker.poll("row-b")["status"] == "waiting"

    def test_empty_row_id_legacy_caller_receives_state(self) -> None:
        midi = _FakeMidiSubsystem()
        broker = MidiCaptureBroker(midi)  # type: ignore[arg-type]
        broker.arm("")
        assert broker.poll("")["status"] == "waiting"

    def test_empty_poll_does_not_drain_concrete_row_capture(self) -> None:
        """A concrete row armed and captured an event; a legacy
        empty-row_id poll must not steal it. The empty poll sees
        ``cancelled``; the concrete row still receives its capture."""
        midi = _FakeMidiSubsystem()
        broker = MidiCaptureBroker(midi)  # type: ignore[arg-type]
        broker.arm("fader:1")
        midi.emit(_event(value=42))
        # Legacy empty-row_id poll must not consume the concrete
        # row's pending capture.
        assert broker.poll("") == {"status": "cancelled"}
        # The concrete row's capture survived for its own poll.
        result = broker.poll("fader:1")
        assert result["status"] == "captured"
        assert result["value"] == 42

    def test_concrete_poll_does_not_drain_empty_row_capture(self) -> None:
        """Mirror case: the legacy empty row armed and captured;
        a concrete-row poll must not steal it."""
        midi = _FakeMidiSubsystem()
        broker = MidiCaptureBroker(midi)  # type: ignore[arg-type]
        broker.arm("")
        midi.emit(_event(value=7))
        assert broker.poll("fader:1") == {"status": "cancelled"}
        result = broker.poll("")
        assert result["status"] == "captured"
        assert result["value"] == 7


class TestDefensiveCallbackGuards:
    """``_on_event`` short-circuits when the broker's state would
    otherwise be incoherent. These guards exist for two scenarios:

    - **Snapshotted callback fires after cancel**: the substrate
      snapshots subscribers before iterating; an event that landed
      during a cancel + re-arm window could call our previous
      callback. The ``_armed_at is None`` check covers that.
    - **Two events in tight succession**: a real fader sweep
      produces dozens of CC values per second. The first event sets
      ``_captured`` + drops the subscription; the second event,
      already snapshotted, races into the callback before the
      unsubscribe takes effect. The ``_captured is not None`` check
      covers that.

    Driven by direct ``_on_event`` calls so the guard paths are
    deterministically exercised rather than relying on a flake-prone
    real-thread race."""

    def test_event_without_arm_returns_early(self) -> None:
        midi = _FakeMidiSubsystem()
        broker = MidiCaptureBroker(midi)  # type: ignore[arg-type]
        # Fire an event before any arm – defensive guard kicks in,
        # nothing lands in the slot.
        broker._on_event(_event(value=42))
        assert broker.poll() == {"status": "idle"}

    def test_second_event_during_same_arm_returns_early(self) -> None:
        midi = _FakeMidiSubsystem()
        broker = MidiCaptureBroker(midi)  # type: ignore[arg-type]
        broker.arm()
        # First event captures.
        broker._on_event(_event(value=10))
        # Second event hits the ``_captured is not None`` guard
        # without overwriting the first one – first-event-wins is
        # the documented contract.
        broker._on_event(_event(value=99))
        result = broker.poll()
        assert result["status"] == "captured"
        assert result["value"] == 10


class TestThreadSafety:
    def test_concurrent_emit_and_poll_does_not_race(self) -> None:
        midi = _FakeMidiSubsystem()
        broker = MidiCaptureBroker(midi)  # type: ignore[arg-type]

        # Each round arms, emits, and polls; the broker must always
        # report a fully-populated capture or a clean idle/waiting.
        for _ in range(50):
            broker.arm()
            emit_thread = threading.Thread(
                target=lambda: midi.emit(_event(value=64)),
            )
            poll_thread = threading.Thread(target=broker.poll)
            emit_thread.start()
            poll_thread.start()
            emit_thread.join()
            poll_thread.join()
            # Whatever raced, the slot is either captured (with all
            # fields) or about-to-be-captured. Drain to clean state.
            final = broker.poll()
            if final["status"] == "captured":
                # All required fields present.
                for key in ("patch_id", "type", "channel", "number", "value"):
                    assert key in final
