# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""NDI connection status tracking with reconnection state and listener callbacks.

``NdiStatusMarker`` publishes the four status fields as one immutable
``_StatusSnapshot`` via a GIL-atomic assignment so readers never see a mixed
state; writers serialize on a lock, reads are lock-free.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from enum import Enum, auto
from typing import NamedTuple

logger = logging.getLogger(__name__)

# Signature for status-change listeners.
StatusCallback = Callable[
    ["ConnectionStatus", str, int, str],  # status, source_name, attempt, error
    None,
]


class ConnectionStatus(Enum):
    """NDI video connection state."""

    DISCONNECTED = auto()
    CONNECTING = auto()
    CONNECTED = auto()
    RECONNECTING = auto()


class _StatusSnapshot(NamedTuple):
    """Immutable view of the four status fields, published as a unit."""

    status: ConnectionStatus
    source_name: str
    reconnect_attempt: int
    error_message: str

    @property
    def is_connected(self) -> bool:
        return self.status == ConnectionStatus.CONNECTED


class NdiStatusMarker:
    """Tracks NDI connection status and notifies listeners of changes.

    The four status fields are kept in a single immutable ``_StatusSnapshot``
    and published with one GIL-atomic attribute assignment, so a reader on the
    ``update_marker_visuals`` tick always sees a self-consistent set – never a
    mix where ``is_connected`` is from the new state while ``error_message`` is
    still the old one. Writers (GStreamer bus thread, GLib reconnect/timeout
    timers, ``stop()``) serialize on ``_lock`` so the change-detection,
    carry-forward read, and publish stay consistent; reads are lock-free. A
    second ``_notify_lock`` orders each transition's publish + callback
    dispatch, so concurrent writers can't deliver callbacks out of state order.
    """

    def __init__(self) -> None:
        self._state = _StatusSnapshot(ConnectionStatus.DISCONNECTED, "", 0, "")
        self._callbacks: list[StatusCallback] = []
        self._lock = threading.Lock()
        # Serializes the publish + callback dispatch of one transition so two
        # concurrent writers can't interleave and deliver callbacks out of
        # publish order. Held across the dispatch, so it must not be acquired by
        # a callback.
        self._notify_lock = threading.Lock()

    @property
    def status(self) -> ConnectionStatus:
        return self._state.status

    @property
    def source_name(self) -> str:
        return self._state.source_name

    @property
    def reconnect_attempt(self) -> int:
        return self._state.reconnect_attempt

    @property
    def error_message(self) -> str:
        return self._state.error_message

    @property
    def is_connected(self) -> bool:
        return self._state.is_connected

    def snapshot(self) -> _StatusSnapshot:
        """Return all four status fields as one consistent unit.

        Readers that need more than one field (e.g. the marker-visuals tick
        reading ``is_connected`` + ``reconnect_attempt`` + ``error_message``)
        MUST use this rather than separate property reads, which could each see
        a different ``_update`` generation and produce a mixed HUD line.
        """
        return self._state

    def add_callback(self, callback: StatusCallback) -> None:
        """Register a callback for status changes."""
        with self._lock:
            self._callbacks.append(callback)

    def remove_callback(self, callback: StatusCallback) -> None:
        """Unregister a status change callback."""
        with self._lock:
            try:
                self._callbacks.remove(callback)
            except ValueError:
                pass

    def set_connecting(self, source_name: str) -> None:
        """Transition to CONNECTING state.

        A pending error is kept only while *actively reconnecting* – a fresh
        connect (prior DISCONNECTED/CONNECTED) starts with a clean message so
        the HUD doesn't show "connecting…" next to a leftover failure string
        from a previous attempt.
        """

        def derive(prior: _StatusSnapshot) -> _StatusSnapshot:
            error = prior.error_message if prior.status == ConnectionStatus.RECONNECTING else ""
            return _StatusSnapshot(ConnectionStatus.CONNECTING, source_name, 0, error)

        self._update(derive)

    def set_connected(self, source_name: str) -> None:
        """Transition to CONNECTED state."""
        self._update(lambda _prior: _StatusSnapshot(ConnectionStatus.CONNECTED, source_name, 0, ""))

    def set_disconnected(self, error_message: str = "") -> None:
        """Transition to DISCONNECTED state."""
        self._update(lambda prior: _StatusSnapshot(ConnectionStatus.DISCONNECTED, prior.source_name, 0, error_message))

    def set_reconnecting(self, attempt: int, error_message: str = "") -> None:
        """Transition to RECONNECTING state with attempt counter."""
        self._update(
            lambda prior: _StatusSnapshot(ConnectionStatus.RECONNECTING, prior.source_name, attempt, error_message)
        )

    def _update(self, derive: Callable[[_StatusSnapshot], _StatusSnapshot]) -> None:
        """Publish the derived state as a unit and notify callbacks on change.

        ``derive`` builds the new snapshot from the prior one (carrying fields
        forward) and runs **inside** ``_lock`` so the read-modify-write is
        atomic – a concurrent transition can't slip a stale carried field into
        the publish. ``_notify_lock`` is held across the whole transition so two
        writers publish-and-dispatch in order; a late callback for an older
        state can't land after a newer state has already been delivered.
        """
        with self._notify_lock:
            with self._lock:
                prior = self._state
                new_state = derive(prior)
                changed = new_state != prior
                self._state = new_state  # GIL-atomic publish – readers see all four together
                callbacks = list(self._callbacks)

            if not changed:
                return
            # Fire outside _lock so a slow / re-entrant callback can't stall
            # readers (lock-free) or block another writer from publishing.
            status, source_name, attempt, error = new_state
            for callback in callbacks:
                try:
                    callback(status, source_name, attempt, error)
                except Exception:
                    logger.debug("Status callback error", exc_info=True)
