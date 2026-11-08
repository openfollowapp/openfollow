# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Michel Honold and Paul Hermann
"""The Operator Screen's "latency test about to start" card, held up by Measure dialogs showing their warnings.

Shaped like the web remote's sessions (``input/touch_remote.py``): a dialog's warnings renew a
session under a token the page was given; the web thread stamps, the frame loop reads, and a
session not heard from for ``STALE_S`` is forgotten, so an abandoned browser never leaves the card
up. A closed token never comes back, so a renewal still on its way cannot raise the card again.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any

STALE_S = 3.0
MAX_SESSIONS = 32
# Closed tokens remembered, so a late renewal of one is ignored.
MAX_CLOSED = 64
HEADLINE = "WARNING"
MESSAGE = "Latency test about to start"
INFO = "The Operator Screen will flash black and white. Look away if flashing light affects you."


class LatencyWarning:
    """Thread-safe registry of the dialogs showing the latency test's warnings."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._seen_at: dict[str, float] = {}
        self._closed: deque[str] = deque(maxlen=MAX_CLOSED)

    @staticmethod
    def token() -> str:
        """A token for a dialog to renew a session under; nothing shows until it does."""
        return secrets.token_urlsafe(16)

    def renew(self, token: str) -> None:
        """The dialog shows its warnings: start or keep its session, one gone stale (a hidden tab) included.

        A closed token is ignored.
        """
        with self._lock:
            if token in self._closed:
                return
            while token not in self._seen_at and len(self._seen_at) >= MAX_SESSIONS:
                del self._seen_at[min(self._seen_at, key=self._seen_at.__getitem__)]
            self._seen_at[token] = self._clock()

    def close(self, token: str) -> None:
        with self._lock:
            self._seen_at.pop(token, None)
            self._closed.append(token)

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
