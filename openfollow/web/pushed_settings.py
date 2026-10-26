# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Settings other stations pushed here, held until someone removes the warning.

In memory only: a restart clears it.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections import deque
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
        self._pushes: deque[Push] = deque(maxlen=SHOWN)
        # The lowest id still held. A removal drops a prefix, so the held ids are
        # consecutive from here to the newest, and the unlisted ones are only counted.
        self._floor = 1
        self._next_id = 1
        # Ids start at 1 on every run; a page names the run it saw, so a removal it
        # sends after a restart can't drop pushes it never showed.
        self.run = secrets.token_hex(8)

    def record(self, station: str, address: str, what: str, backup_error: str = "") -> Push:
        with self._lock:
            push = Push(self._next_id, self._clock(), station, address, what, backup_error)
            self._next_id += 1
            self._pushes.append(push)
            return push

    @property
    def received(self) -> int:
        """Pushes recorded this run, removed ones included."""
        with self._lock:
            return self._next_id - 1

    def snapshot(self) -> tuple[tuple[Push, ...], int]:
        """The listed pushes newest first, and how many earlier ones there were."""
        with self._lock:
            earlier = self._pushes[0].id - self._floor if self._pushes else 0
            return tuple(reversed(self._pushes)), earlier

    def remove(self, upto: int) -> None:
        """Drop every push up to ``upto``; a push that arrived since stays."""
        with self._lock:
            self._floor = max(self._floor, min(upto, self._next_id - 1) + 1)
            self._pushes = deque((push for push in self._pushes if push.id > upto), maxlen=SHOWN)
