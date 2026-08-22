# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Shared time base for PSN packet headers and per-tracker timestamps.

The PSN spec defines the header timestamp as the microseconds elapsed since the
server started, and the reference implementation stamps trackers off that same
clock (``set_timestamp( uint64_t timestamp_usec )``), so a receiver can compare
the two. Monotonic rather than wall clock: the Pis have no RTC and sync their
system time shortly after boot, which would otherwise step every timestamp we
have already sent.
"""

from __future__ import annotations

import time

# Fixed at import, not at ``PsnServer.start()``: rebind() is a stop/start cycle,
# and restarting the epoch there would send a receiver's clock backwards.
_EPOCH = time.monotonic()


def psn_timestamp_usec() -> int:
    """Microseconds elapsed since this PSN server started."""
    return int((time.monotonic() - _EPOCH) * 1_000_000)
