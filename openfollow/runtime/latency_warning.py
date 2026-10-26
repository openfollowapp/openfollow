# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The Operator Screen's "latency test about to start" card, held up by Measure dialogs showing their warnings.

Shaped like the web remote's sessions (``input/touch_remote.py``): a dialog opens a session and
renews it while its warnings show; the web thread stamps, the frame loop reads, and a session
not heard from for ``STALE_S`` is forgotten, so a closed or abandoned browser never leaves the card up.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable
from typing import Any

STALE_S = 3.0
MAX_SESSIONS = 32
HEADLINE = "WARNING"
MESSAGE = "Latency test about to start"
INFO = "The Operator Screen will flash black and white. Look away if flashing light affects you."


class LatencyWarning:
    """Thread-safe registry of the dialogs showing the latency test's warnings."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._seen_at: dict[str, float] = {}

    def open(self) -> str:
        """Start a session and return its token."""
        token = secrets.token_urlsafe(16)
        self.renew(token)
        return token

    def renew(self, token: str) -> None:
        """The dialog still shows its warnings; one gone stale meanwhile (a hidden browser tab) joins again."""
        with self._lock:
            while token not in self._seen_at and len(self._seen_at) >= MAX_SESSIONS:
                del self._seen_at[min(self._seen_at, key=self._seen_at.__getitem__)]
            self._seen_at[token] = self._clock()

    def close(self, token: str) -> None:
        with self._lock:
            self._seen_at.pop(token, None)

    def showing(self) -> bool:
        """Frame loop: whether a dialog shows its warnings; forgets sessions gone quiet."""
        now = self._clock()
        with self._lock:
            for token in [t for t, seen in self._seen_at.items() if now - seen > STALE_S]:
                del self._seen_at[token]
            return bool(self._seen_at)


def card_showing(app: Any) -> bool:
    """Whether the Operator Screen shows the latency test's warning card."""
    web_commands = getattr(app, "_web_commands", None)
    return web_commands is not None and web_commands.latency_warning.showing()
