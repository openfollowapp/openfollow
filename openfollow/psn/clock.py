# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Shared time base for PSN packet headers and per-tracker timestamps.

Both fields are microseconds elapsed since this process started, off one clock,
so a receiver can compare them. Process start is the spec's "server start": the
epoch spans every ``PsnServer`` this process builds, not one instance's uptime.
"""

from __future__ import annotations

import time

# Import time, not ``PsnServer.start()``: rebind() is a stop/start cycle, and
# restarting the epoch there would send a receiver's clock backwards.
_EPOCH = time.monotonic()


def psn_timestamp_usec() -> int:
    """Microseconds elapsed since this process started."""
    return int((time.monotonic() - _EPOCH) * 1_000_000)
