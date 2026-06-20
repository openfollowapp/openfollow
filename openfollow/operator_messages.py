# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Thread-safe in-memory store for OSC operator messages.

The OSC adapter writes to it; the per-frame overlay builder reads from it.
Pure data structure – no I/O or rendering.

- Keyed messages (``marker_id >= 1``) replace any existing card for that
  marker.
- Broadcast messages (``marker_id == 0``) stack, each with its own countdown.
- ``duration_s == 0`` lives until cleared; otherwise it expires ``duration_s``
  seconds after ingest.
- A hard cap bounds memory; the display cap is applied by the overlay builder.

The clock is injectable (``time.monotonic`` by default); monotonic so lifetime
is unaffected by wall-clock steps.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# Upper bound on stored messages; oldest evicted past the cap.
_HARD_CAP = 50

# Cap on stored text length, truncated at ingest.
_MAX_TEXT_LEN = 200


@dataclass(frozen=True)
class OperatorMessage:
    """One operator message held in the store.

    ``marker_id == 0`` means broadcast / no marker. ``duration_s == 0``
    means "forever". ``created_monotonic`` is the ``time.monotonic()`` value
    at ingest; ``seq`` is a per-store monotonic counter giving a stable
    newest-first ordering and a unique identity for stacked broadcasts.
    """

    message: str
    info: str
    marker_id: int
    created_monotonic: float
    duration_s: float
    seq: int

    def is_expired(self, now: float) -> bool:
        """True when a timed message has passed its display window."""
        return self.duration_s > 0.0 and now >= self.created_monotonic + self.duration_s

    def remaining_s(self, now: float) -> float:
        """Seconds left before expiry; ``0.0`` for a forever message."""
        if self.duration_s <= 0.0:
            return 0.0
        return max(0.0, self.created_monotonic + self.duration_s - now)

    def remaining_fraction(self, now: float) -> float:
        """Remaining fraction in ``[0, 1]`` for the countdown bar; ``1.0``
        for a forever message (no bar shrinkage)."""
        if self.duration_s <= 0.0:
            return 1.0
        return max(0.0, min(1.0, self.remaining_s(now) / self.duration_s))


class OperatorMessageStore:
    """Thread-safe collection of live :class:`OperatorMessage` cards.

    A single :class:`threading.Lock` guards all access: writes via
    :meth:`add` / :meth:`clear_all` / :meth:`clear_marker`, reads via
    :meth:`snapshot`.
    """

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._lock = threading.Lock()
        self._messages: list[OperatorMessage] = []
        self._seq = 0
        self._clock = clock

    def add(
        self,
        message: str,
        info: str = "",
        marker_id: int = 0,
        duration_s: float = 0.0,
    ) -> None:
        """Add a message, applying replace-by-marker / broadcast-stack rules.

        Inputs are coerced: ``message`` / ``info`` to ``str``, ``marker_id`` to
        a non-negative ``int``, ``duration_s`` to a non-negative ``float``.
        Keyed messages (``marker_id >= 1``) evict the prior card for that marker.
        """
        now = self._clock()
        entry = OperatorMessage(
            message=str(message)[:_MAX_TEXT_LEN],
            info=str(info)[:_MAX_TEXT_LEN],
            marker_id=max(0, _as_int(marker_id)),
            created_monotonic=now,
            duration_s=max(0.0, _as_float(duration_s)),
            seq=0,  # replaced under the lock so seq stays monotonic
        )
        with self._lock:
            self._seq += 1
            entry = OperatorMessage(
                message=entry.message,
                info=entry.info,
                marker_id=entry.marker_id,
                created_monotonic=entry.created_monotonic,
                duration_s=entry.duration_s,
                seq=self._seq,
            )
            if entry.marker_id >= 1:
                # Keyed: latest cue per marker wins – drop the prior card.
                self._messages = [m for m in self._messages if m.marker_id != entry.marker_id]
            self._messages.append(entry)
            self._prune_locked(now)

    def snapshot(self, now: float | None = None) -> list[OperatorMessage]:
        """Return non-expired messages newest-first as a copy. ``now`` defaults
        to the store clock; callers may pass a sampled frame clock.
        """
        when = self._clock() if now is None else now
        with self._lock:
            live = [m for m in self._messages if not m.is_expired(when)]
        live.sort(key=lambda m: m.seq, reverse=True)
        return live

    def clear_all(self) -> None:
        """Remove every message."""
        with self._lock:
            self._messages.clear()

    def clear_marker(self, marker_id: int) -> None:
        """Remove messages tied to ``marker_id`` (``0`` clears broadcasts)."""
        target = max(0, _as_int(marker_id))
        with self._lock:
            self._messages = [m for m in self._messages if m.marker_id != target]

    def _prune_locked(self, now: float) -> None:
        """Drop expired messages, then evict the oldest past the hard cap.
        Caller holds the lock."""
        live = [m for m in self._messages if not m.is_expired(now)]
        if len(live) > _HARD_CAP:
            live.sort(key=lambda m: m.seq)
            live = live[-_HARD_CAP:]
        self._messages = live


def _as_int(value: Any) -> int:
    """Best-effort int coercion (``bool`` rejected); falls back to ``0``."""
    if isinstance(value, bool):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return 0


def _as_float(value: Any) -> float:
    """Best-effort finite float coercion; falls back to ``0.0`` (forever).

    Non-finite results (``NaN`` / ``inf``) also fall back to ``0.0`` so a
    timed card can never be pinned forever or shrink to a ``NaN``-width bar.
    """
    if isinstance(value, bool):
        return 0.0
    try:
        out = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return out if math.isfinite(out) else 0.0
