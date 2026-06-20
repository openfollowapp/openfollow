# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Web-driven MIDI Learn broker for OSC binding capture."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openfollow.input.midi import MidiEvent, MidiSubsystem


_DEFAULT_TIMEOUT_S = 10.0  # default capture window


class MidiCaptureBroker:
    """Single-slot MIDI Learn broker for the OSC binding web form.

    Lifecycle:

    1. ``arm()`` cancels any previous subscription and installs a
       fresh one. The slot is empty and the timer starts.
    2. The next :class:`MidiEvent` lands the broker into the
       ``captured`` state – the slot now holds the event details
       and the subscription is dropped.
    3. If no event arrives before the timeout, the next ``poll()``
       returns ``timeout`` and clears state.
    4. A subsequent ``arm()`` resets everything for the next round.

    All states are observed via :meth:`poll`, called every 250 ms by the
    web polling endpoint. State does not survive a server restart.
    """

    def __init__(
        self,
        midi_subsystem: MidiSubsystem,
        *,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        self._midi = midi_subsystem
        self._timeout_s = timeout_s
        self._lock = threading.Lock()
        self._captured: MidiEvent | None = None
        self._armed_at: float | None = None
        self._armed_by: str | None = None
        self._unsubscribe: Callable[[], None] | None = None

    def arm(self, row_id: str = "") -> None:
        """Begin a fresh capture window, cancelling any previous arm.

        The slot is cleared so the next poll never returns a stale event.
        ``row_id`` scopes the arm: a row that armed first sees ``cancelled``
        once a different row arms, rather than consuming that row's event.
        Default ``""`` keeps the single-caller contract for callers without a row.
        """
        with self._lock:
            self._cancel_locked()
            self._captured = None
            self._armed_at = time.monotonic()
            self._armed_by = row_id
            self._unsubscribe = self._midi.subscribe(self._on_event)

    def poll(self, row_id: str = "") -> dict[str, Any]:
        """Return the broker's current state as a JSON-serialisable
        dict. Five shapes:

        - ``{"status": "idle"}`` – operator hasn't armed (or arm
          timed out without re-arm).
        - ``{"status": "waiting", "elapsed_s": <float>}`` – armed
          but no event yet.
        - ``{"status": "captured", "patch_id": ..., "type": ...,
          "channel": <int>, "number": <int|null>, "value": <int>}``
          – the next event landed; the slot is now drained and the
          broker has returned to idle.
        - ``{"status": "timeout"}`` – armed, no event before the
          timeout window elapsed; the slot is cleared and the
          broker is back to idle.
        - ``{"status": "cancelled"}`` – this row's arm was
          superseded by another row arming (the broker is single-
          slot, so concurrent arms cancel earlier ones). The
          original row sees this on its next poll instead of
          silently consuming the new row's event.

        Returning a dict (not the :class:`MidiEvent` directly) means
        the web layer can serialise the result as JSON without
        importing the substrate's dataclass; the field names match
        :class:`MidiMessageTrigger`'s field names so the form's
        capture path can pre-fill inputs by key without translation.
        """
        with self._lock:
            if self._armed_at is None:
                return {"status": "idle"}
            # Row mismatch: this poll is for a different row than
            # the one that's currently armed → tell the caller their
            # session was cancelled so the form can re-enable its
            # Capture button without consuming someone else's event.
            # An empty poll row_id must also mismatch a concrete armed
            # row: otherwise the legacy ``""`` JSON endpoints could
            # drain a fader/OSC row's captured event. ``_armed_at`` is
            # not None here, so ``_armed_by`` is the (possibly empty)
            # row_id passed to ``arm`` – matching empties still fall
            # through, preserving the legacy single-caller contract.
            if row_id != self._armed_by:
                return {"status": "cancelled"}
            if self._captured is not None:
                event = self._captured
                # Clear the slot before ``_cancel_locked`` so poll's
                # "slot is now drained" promise holds.
                self._captured = None
                self._cancel_locked()
                return {"status": "captured", **event.as_dict()}
            elapsed = time.monotonic() - self._armed_at
            if elapsed >= self._timeout_s:
                self._cancel_locked()
                return {"status": "timeout"}
            return {"status": "waiting", "elapsed_s": elapsed}

    def cancel(self) -> None:
        """Drop any pending arm. Used when the operator navigates
        away mid-capture or closes the form – the next poll on a
        cancelled session sees ``idle`` rather than a stale
        ``waiting``."""
        with self._lock:
            self._cancel_locked()

    def _on_event(self, event: MidiEvent) -> None:
        """Subscriber callback – runs on the rtmidi listener thread.

        Writes the event into the slot and drops the subscription so
        a single arm produces exactly one capture even if the
        operator's wiggle produces a burst of CC values. The first
        message wins; subsequent messages from the same session
        return early (no slot to write into because the unsubscribe
        already removed us – this guard is defence-in-depth for the
        gap between the slot read and the unsubscribe call).
        """
        with self._lock:
            if self._armed_at is None:
                return
            if self._captured is not None:
                return
            self._captured = event
            # Capture the unsubscribe to run outside the lock – the
            # substrate's unsubscribe path takes its own lock and
            # we don't want to hold both. ``_armed_at`` and
            # ``_unsubscribe`` are set / cleared together (under the
            # lock), so reaching this line guarantees ``unsub`` is
            # callable; no defensive ``if unsub is not None`` needed.
            unsub = self._unsubscribe
            self._unsubscribe = None
        # The state-machine invariant above guarantees ``unsub`` is
        # callable here; the assert pins that down for the type
        # checker (cleaner than ``# type: ignore`` and louder if a
        # future change ever splits the invariant – :class:`AssertionError`
        # on the listener thread is a much clearer failure mode than a
        # silent ``TypeError`` from calling None).
        assert unsub is not None
        unsub()

    def _cancel_locked(self) -> None:
        """Internal: drop the subscription + clear the timer slot
        without touching ``_captured`` (so a successful capture's
        result survives until ``poll`` reads it)."""
        if self._unsubscribe is not None:
            unsub = self._unsubscribe
            self._unsubscribe = None
            # Releasing the lock around ``unsub`` is unnecessary
            # here – the substrate's unsubscribe takes its own
            # lock briefly and doesn't call back into the broker.
            try:
                unsub()
            except Exception:  # pragma: no cover - defensive
                pass
        self._armed_at = None
        self._armed_by = None
