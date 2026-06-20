# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for ConnectionStatus enum and NdiStatusMarker state transitions."""

from __future__ import annotations

import pytest

from openfollow.video.connection_status import ConnectionStatus, NdiStatusMarker

pytestmark = pytest.mark.unit


class TestConnectionStatus:
    def test_enum_values_are_distinct(self) -> None:
        values = [s.value for s in ConnectionStatus]
        assert len(values) == len(set(values))

    def test_all_states_present(self) -> None:
        names = {s.name for s in ConnectionStatus}
        assert names == {"DISCONNECTED", "CONNECTING", "CONNECTED", "RECONNECTING"}


class TestNdiStatusMarker:
    def test_initial_state_is_disconnected(self) -> None:
        marker = NdiStatusMarker()
        assert marker.status == ConnectionStatus.DISCONNECTED
        assert marker.source_name == ""
        assert marker.reconnect_attempt == 0
        assert marker.error_message == ""
        assert marker.is_connected is False

    def test_set_connecting(self) -> None:
        marker = NdiStatusMarker()
        marker.set_connecting("Camera 1")
        assert marker.status == ConnectionStatus.CONNECTING
        assert marker.source_name == "Camera 1"
        assert marker.is_connected is False

    def test_set_connected(self) -> None:
        marker = NdiStatusMarker()
        marker.set_connected("Camera 1")
        assert marker.status == ConnectionStatus.CONNECTED
        assert marker.source_name == "Camera 1"
        assert marker.is_connected is True

    def test_set_disconnected(self) -> None:
        marker = NdiStatusMarker()
        marker.set_connected("Camera 1")
        marker.set_disconnected("Signal lost")
        assert marker.status == ConnectionStatus.DISCONNECTED
        assert marker.source_name == "Camera 1"  # preserved
        assert marker.error_message == "Signal lost"
        assert marker.is_connected is False

    def test_set_reconnecting(self) -> None:
        marker = NdiStatusMarker()
        marker.set_connected("Camera 1")
        marker.set_reconnecting(3, "Timeout")
        assert marker.status == ConnectionStatus.RECONNECTING
        assert marker.reconnect_attempt == 3
        assert marker.error_message == "Timeout"

    def test_connecting_preserves_pending_error_during_reconnect_episode(self) -> None:
        """Reconnect cycle alternates set_reconnecting(error) → set_connecting();
        set_connecting must NOT wipe the error to "",
        otherwise the panel (which reddens on a non-empty error_message)
        flashes green on every attempt. The error persists until a real
        connect clears it."""
        marker = NdiStatusMarker()
        marker.set_reconnecting(1, "No video received")
        marker.set_connecting("Cam")  # next attempt – error must survive
        assert marker.status == ConnectionStatus.CONNECTING
        assert marker.error_message == "No video received"
        marker.set_connected("Cam")  # real frame flow → error cleared
        assert marker.error_message == ""

    def test_first_connecting_has_no_error(self) -> None:
        """A fresh connect (no pending error) still shows a clean CONNECTING."""
        marker = NdiStatusMarker()
        marker.set_connecting("Cam")
        assert marker.error_message == ""

    def test_connecting_after_disconnect_error_starts_clean(self) -> None:
        """A disconnect error must NOT bleed into the next fresh connect – only
        an active RECONNECTING episode keeps the pending error."""
        marker = NdiStatusMarker()
        marker.set_disconnected("Signal lost")
        assert marker.error_message == "Signal lost"
        marker.set_connecting("Cam")  # fresh connect (prior was DISCONNECTED)
        assert marker.status == ConnectionStatus.CONNECTING
        assert marker.error_message == ""

    def test_snapshot_returns_all_fields_as_a_unit(self) -> None:
        marker = NdiStatusMarker()
        marker.set_reconnecting(3, "Timeout")
        snap = marker.snapshot()
        assert snap.status == ConnectionStatus.RECONNECTING
        assert snap.reconnect_attempt == 3
        assert snap.error_message == "Timeout"
        assert snap.is_connected is False

    def test_snapshot_is_always_a_consistent_generation_under_concurrency(self) -> None:
        """A reader using snapshot() always sees one full _update generation,
        never a mix of fields from two states."""
        import threading

        marker = NdiStatusMarker()
        marker.set_connected("camA")  # prime to state A
        state_a = (ConnectionStatus.CONNECTED, "camA", 0, "")
        state_b = (ConnectionStatus.DISCONNECTED, "camA", 0, "boom")
        valid = {state_a, state_b}

        stop = threading.Event()

        def _writer() -> None:
            toggle = False
            while not stop.is_set():
                if toggle:
                    marker.set_connected("camA")
                else:
                    marker.set_disconnected("boom")
                toggle = not toggle

        writer = threading.Thread(target=_writer, daemon=True)
        writer.start()
        try:
            for _ in range(20_000):
                s = marker.snapshot()
                assert (s.status, s.source_name, s.reconnect_attempt, s.error_message) in valid
        finally:
            stop.set()
            writer.join(timeout=2.0)

    def test_callback_fires_on_state_change(self) -> None:
        marker = NdiStatusMarker()
        events: list[tuple] = []
        marker.add_callback(lambda s, n, a, e: events.append((s, n, a, e)))

        marker.set_connecting("Cam")
        assert len(events) == 1
        assert events[0] == (ConnectionStatus.CONNECTING, "Cam", 0, "")

    def test_callback_does_not_fire_when_state_unchanged(self) -> None:
        marker = NdiStatusMarker()
        events: list[tuple] = []
        marker.add_callback(lambda s, n, a, e: events.append((s, n, a, e)))

        marker.set_disconnected("")  # already disconnected with empty error
        assert len(events) == 0

    def test_multiple_callbacks(self) -> None:
        marker = NdiStatusMarker()
        calls_a: list[ConnectionStatus] = []
        calls_b: list[ConnectionStatus] = []
        marker.add_callback(lambda s, n, a, e: calls_a.append(s))
        marker.add_callback(lambda s, n, a, e: calls_b.append(s))

        marker.set_connected("Cam")
        assert len(calls_a) == 1
        assert len(calls_b) == 1

    def test_remove_callback(self) -> None:
        marker = NdiStatusMarker()
        events: list[tuple] = []
        cb = lambda s, n, a, e: events.append((s, n, a, e))  # noqa: E731
        marker.add_callback(cb)
        marker.set_connecting("Cam")
        assert len(events) == 1

        marker.remove_callback(cb)
        marker.set_connected("Cam")
        assert len(events) == 1  # no new event

    def test_remove_nonexistent_callback_is_noop(self) -> None:
        marker = NdiStatusMarker()
        marker.remove_callback(lambda s, n, a, e: None)  # should not raise

    def test_callback_exception_does_not_propagate(self) -> None:
        marker = NdiStatusMarker()
        marker.add_callback(lambda s, n, a, e: 1 / 0)
        # Should not raise despite ZeroDivisionError in callback
        marker.set_connected("Cam")
        assert marker.is_connected is True

    def test_connecting_carry_forward_reads_committed_prior_state(self) -> None:
        """set_connecting carries the pending error forward only when the prior
        state – at publish time – is RECONNECTING. If another writer commits
        CONNECTED (clearing the error) before set_connecting publishes, the
        carried error must be re-read as cleared, not the stale pre-read value.

        Pre-fix, set_connecting snapshots self._state OUTSIDE the lock, so an
        interleaved set_connected can't be observed and the stale error is
        republished onto CONNECTING.
        """
        import threading

        marker = NdiStatusMarker()
        marker.set_reconnecting(1, "No video received")

        real_lock = marker._lock
        gate = threading.Event()
        released = threading.Event()
        interposing = {"armed": False}

        class _GatedLock:
            def acquire(self, *a: object, **k: object) -> bool:
                # Block the connecting writer's publish-lock until the
                # interposing set_connected has committed CONNECTED.
                if interposing["armed"]:
                    interposing["armed"] = False
                    gate.set()
                    released.wait(2.0)
                return real_lock.acquire(*a, **k)

            def release(self) -> None:
                real_lock.release()

            def __enter__(self) -> object:
                self.acquire()
                return self

            def __exit__(self, *exc: object) -> None:
                self.release()

        marker._lock = _GatedLock()  # type: ignore[assignment]

        def _connect_when_gated() -> None:
            gate.wait(2.0)
            marker.set_connected("Cam")  # commit CONNECTED, clearing the error
            released.set()

        other = threading.Thread(target=_connect_when_gated, daemon=True)
        other.start()
        interposing["armed"] = True
        marker.set_connecting("Cam")
        other.join(timeout=2.0)

        # CONNECTED was the last committed state; the carried error is cleared.
        assert marker.error_message == ""

    def test_callbacks_delivered_in_published_state_order(self) -> None:
        """The last callback delivered must match the last published snapshot.

        Forces the out-of-order window: writer A publishes CONNECTED then pauses
        before firing its callback; writer B publishes DISCONNECTED and fires.
        Pre-fix, A's callback dispatch happens after _lock is released, so B's
        DISCONNECTED callback lands first and A's stale CONNECTED lands last –
        leaving seen[-1] disagreeing with the published state. The fix holds the
        whole publish+dispatch under _notify_lock, so B can't publish until A
        has finished dispatching.
        """
        import threading

        marker = NdiStatusMarker()
        seen: list[ConnectionStatus] = []
        seen_lock = threading.Lock()

        def _record(status: ConnectionStatus, *_: object) -> None:
            with seen_lock:
                seen.append(status)

        marker.add_callback(_record)

        real_lock = marker._lock
        a_published = threading.Event()
        b_done = threading.Event()
        gate = {"armed": False}

        class _GatedLock:
            def acquire(self, *a: object, **k: object) -> bool:
                return real_lock.acquire(*a, **k)

            def release(self) -> None:
                real_lock.release()
                # After A publishes (and releases _lock) but BEFORE it fires its
                # callback, let B run a full transition.
                if gate["armed"]:
                    gate["armed"] = False
                    a_published.set()
                    b_done.wait(2.0)

            def __enter__(self) -> object:
                self.acquire()
                return self

            def __exit__(self, *exc: object) -> None:
                self.release()

        marker._lock = _GatedLock()  # type: ignore[assignment]

        def _writer_b() -> None:
            a_published.wait(2.0)
            marker.set_disconnected("boom")  # publishes + dispatches DISCONNECTED
            b_done.set()

        b = threading.Thread(target=_writer_b, daemon=True)
        b.start()
        gate["armed"] = True
        marker.set_connected("Cam")  # writer A
        b.join(timeout=3.0)

        # Terminal published state is whatever B/A settled on; the last callback
        # must agree with it rather than carry a stale earlier transition.
        with seen_lock:
            last_seen = seen[-1]
        assert last_seen == marker.status

    def test_full_lifecycle(self) -> None:
        marker = NdiStatusMarker()
        events: list[ConnectionStatus] = []
        marker.add_callback(lambda s, n, a, e: events.append(s))

        marker.set_connecting("Cam")
        marker.set_connected("Cam")
        marker.set_reconnecting(1, "Lost signal")
        marker.set_reconnecting(2, "Lost signal")
        marker.set_connected("Cam")
        marker.set_disconnected("Shutdown")

        assert events == [
            ConnectionStatus.CONNECTING,
            ConnectionStatus.CONNECTED,
            ConnectionStatus.RECONNECTING,
            ConnectionStatus.RECONNECTING,  # attempt changed
            ConnectionStatus.CONNECTED,
            ConnectionStatus.DISCONNECTED,
        ]
