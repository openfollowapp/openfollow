# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Per-source-IP brute-force throttle for PIN authentication with exponential backoff."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

_MAX_LOCKOUT_S = 30.0  # exponential backoff cap
_RESET_AFTER_S = 600.0  # idle timeout for garbage collection
# Short provisional lockout armed atomically when a guess starts, so
# concurrent requests for one IP serialize to a single in-flight guess. A real
# failure overwrites it with the backoff; a success clears it; a dropped
# request lets it expire (self-healing – no permanent lock).
_PROVISIONAL_LOCKOUT_S = 1.0


@dataclass
class _Entry:
    failures: int = 0
    lockout_until: float = 0.0
    last_failure_at: float = 0.0


class LoginThrottle:
    """Per-IP exponential-backoff lockout for PIN-style authentication."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        max_lockout_s: float = _MAX_LOCKOUT_S,
        reset_after_s: float = _RESET_AFTER_S,
        provisional_lockout_s: float = _PROVISIONAL_LOCKOUT_S,
    ) -> None:
        self._clock = clock
        self._max_lockout_s = max_lockout_s
        self._reset_after_s = reset_after_s
        self._provisional_lockout_s = provisional_lockout_s
        self._lock = threading.Lock()
        self._entries: dict[str, _Entry] = {}
        self._last_sweep_at = 0.0  # timestamp of last full sweep for GC

    @staticmethod
    def _is_stale(entry: _Entry, now: float, reset_after_s: float) -> bool:
        """Check if entry is stale: idle threshold passed and lockout expired."""
        return now - entry.last_failure_at >= reset_after_s and now >= entry.lockout_until

    def remaining_lockout(self, ip: str) -> float:
        """Return seconds until ip is allowed to retry (0.0 if not locked)."""
        with self._lock:
            now = self._clock()
            entry = self._entries.get(ip)
            if entry is None:
                return 0.0
            if self._is_stale(entry, now, self._reset_after_s):
                del self._entries[ip]
                return 0.0
            return max(0.0, entry.lockout_until - now)

    def begin_attempt(self, ip: str) -> float:
        """Atomically reserve a single in-flight guess for ``ip``.

        Returns ``0.0`` when the caller may proceed – and arms a short
        provisional lockout so concurrent guesses for the same IP serialize to
        one per window – or the seconds remaining when ``ip`` is already locked
        out or another guess is in flight. The provisional window self-heals if
        the caller never records a result, so a dropped request can't lock an
        IP permanently. A ``0.0`` return MUST be followed by exactly one
        ``record_success`` / ``record_failure``.
        """
        with self._lock:
            now = self._clock()
            if now - self._last_sweep_at >= self._reset_after_s:
                self._sweep_locked(now)
                self._last_sweep_at = now

            entry = self._entries.get(ip)
            if entry is not None and not self._is_stale(entry, now, self._reset_after_s):
                if now < entry.lockout_until:
                    return entry.lockout_until - now
            else:
                entry = _Entry()
                self._entries[ip] = entry
            entry.lockout_until = now + self._provisional_lockout_s
            entry.last_failure_at = now
            return 0.0

    def record_failure(self, ip: str) -> None:
        """Increment ip's failure count and arm the next lockout window."""
        with self._lock:
            now = self._clock()
            # Periodic full sweep to prevent unbounded dict growth
            if now - self._last_sweep_at >= self._reset_after_s:
                self._sweep_locked(now)
                self._last_sweep_at = now

            entry = self._entries.get(ip)
            if entry is None:
                entry = _Entry()
                self._entries[ip] = entry
            elif self._is_stale(entry, now, self._reset_after_s):
                entry.failures = 0
            entry.failures += 1
            # Saturate exponent to prevent overflow on persistent attacks
            exponent = min(entry.failures - 1, 60)
            delay = min(self._max_lockout_s, 2.0**exponent)
            entry.lockout_until = now + delay
            entry.last_failure_at = now

    def _sweep_locked(self, now: float) -> None:
        """Drop entries idle past ``reset_after_s``. Caller must hold the lock."""
        stale = [ip for ip, e in self._entries.items() if self._is_stale(e, now, self._reset_after_s)]
        for ip in stale:
            del self._entries[ip]

    def record_success(self, ip: str) -> None:
        """Clear ``ip``'s failure history after a successful check."""
        with self._lock:
            self._entries.pop(ip, None)
