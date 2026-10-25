# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The backoff a failed network operation is retried on: 2 s, doubling to a minute."""

from __future__ import annotations

import threading
from collections.abc import Callable

RETRY_BACKOFF_S = 2.0
RETRY_BACKOFF_MAX_S = 60.0


def retry_with_backoff(attempt: Callable[[int], bool], stop: threading.Event, first: int = 1) -> int | None:
    """Call ``attempt(number)`` after each backoff wait until it returns True or *stop* is set.

    Numbers count up from *first*. Returns the number that succeeded, or None once stopped.
    """
    delay = RETRY_BACKOFF_S
    number = first
    while not stop.wait(delay):
        if attempt(number):
            return number
        number += 1
        delay = min(delay * 2, RETRY_BACKOFF_MAX_S)
    return None
