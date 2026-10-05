# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Settings other stations pushed here, held until someone removes the warning.

In memory only: a restart clears it.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

# Pushes listed one by one; older ones are only counted.
SHOWN = 10


@dataclass(frozen=True)
class Push:
    """One push: who sent it, from where, what it replaced, and the backup's failure if any."""

    id: int
    at: float
    station: str
    address: str
    what: str
    backup_error: str


class PushedSettings:
    """Thread-safe: web request threads and the main loop's housekeeping share it."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._pushes: list[Push] = []
        # Ids of pushes past the listed ones, oldest first, so a removal counts them right.
        self._earlier: list[int] = []
        self._next_id = 1

    def record(self, station: str, address: str, what: str, backup_error: str = "") -> Push:
        with self._lock:
            push = Push(self._next_id, self._clock(), station, address, what, backup_error)
            self._next_id += 1
            self._pushes.append(push)
            while len(self._pushes) > SHOWN:
                self._earlier.append(self._pushes.pop(0).id)
            return push

    def snapshot(self) -> tuple[tuple[Push, ...], int]:
        """The listed pushes newest first, and how many earlier ones there were."""
        with self._lock:
            return tuple(reversed(self._pushes)), len(self._earlier)

    def remove(self, upto: int) -> None:
        """Drop every push up to ``upto``; a push that arrived since stays."""
        with self._lock:
            self._pushes = [push for push in self._pushes if push.id > upto]
            self._earlier = [push_id for push_id in self._earlier if push_id > upto]
