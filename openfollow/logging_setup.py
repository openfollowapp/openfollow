# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Process-wide logging configuration with bounded in-memory ring buffer fallback.

The ring buffer is used when ``journalctl`` is unavailable (non-systemd hosts or
unprivileged users). Sized at 2000 lines (~160 KB).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable

# Default cap for the in-memory ring.
# Public so tests can drive a smaller cap without monkeypatching.
DEFAULT_RING_CAPACITY = 2000

# Default format string for log records.
DEFAULT_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
DEFAULT_DATEFMT = "%H:%M:%S"


class ThrottledExceptionLogger:
    """Rate-limit ``logger.exception`` for a failure on a per-frame path.

    A failing per-tick path (the 60–120 Hz display loop) would otherwise flood
    the log with the same traceback every frame. :meth:`log` emits at most once
    per ``interval`` seconds; calls in between are counted and the suppressed
    total is reported with the next emitted line. Call it from inside an
    ``except`` block so ``logger.exception`` captures the active exception.

    The clock is injectable so the throttle is deterministically testable.
    """

    def __init__(
        self,
        logger: logging.Logger,
        message: str,
        *,
        interval: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._logger = logger
        self._message = message
        self._interval = interval
        self._clock = clock
        self._last_emit: float | None = None
        self._suppressed = 0

    def log(self) -> None:
        now = self._clock()
        if self._last_emit is not None and now - self._last_emit < self._interval:
            self._suppressed += 1
            return
        if self._suppressed:
            self._logger.exception(
                "%s (%d more suppressed in the last %.0fs)",
                self._message,
                self._suppressed,
                self._interval,
            )
        else:
            self._logger.exception(self._message)
        self._last_emit = now
        self._suppressed = 0


def _sd_priority(levelno: int) -> int:
    """Map a Python log level to a syslog priority (0–7) for the sd-daemon prefix."""
    if levelno >= logging.CRITICAL:
        return 2  # crit
    if levelno >= logging.ERROR:
        return 3  # err
    if levelno >= logging.WARNING:
        return 4  # warning
    if levelno >= logging.INFO:
        return 6  # info
    return 7  # debug


class _JournalPriorityFormatter(logging.Formatter):
    """Prefix each line with the sd-daemon ``<N>`` priority token.

    Under systemd ``StandardError=journal`` the leading ``<N>`` is parsed (and
    stripped) by journald to set the record's priority, so ``journalctl -p
    warning`` filters by real level instead of treating every Python record as
    the same priority. Used for stderr only; the ring buffer keeps clean lines.
    """

    def format(self, record: logging.LogRecord) -> str:
        # Prefix EVERY line: journald reads stderr line-by-line, so a
        # multi-line record (e.g. an exception traceback) would otherwise leave
        # all but the first line at the default priority – and ``journalctl -p
        # warning`` would miss the traceback that belongs to the warning.
        prefix = f"<{_sd_priority(record.levelno)}>"
        formatted = super().format(record)
        return "\n".join(f"{prefix}{line}" for line in formatted.split("\n"))


def _under_systemd() -> bool:
    """True when stderr is wired to the journal (systemd sets JOURNAL_STREAM)."""
    return "JOURNAL_STREAM" in os.environ


class RingBufferLogHandler(logging.Handler):
    """Bounded FIFO of formatted log lines for the diagnostics bundle.

    Thread-safe. Stores formatted strings (not raw LogRecords) for memory efficiency.
    """

    def __init__(self, capacity: int = DEFAULT_RING_CAPACITY) -> None:
        super().__init__()
        self._capacity = capacity
        self._lock = threading.Lock()
        self._entries: deque[str] = deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord) -> None:
        # Format matches what setup_logging installed.
        try:
            line = self.format(record)
        except Exception:  # pragma: no cover – defensive
            self.handleError(record)
            return
        with self._lock:
            self._entries.append(line)

    def snapshot(self, *, last_n: int | None = None) -> list[str]:
        """Defensive copy of current entries. last_n caps to most recent N lines."""
        with self._lock:
            entries = list(self._entries)
        if last_n is None:
            return entries
        if last_n <= 0:
            # Explicit empty return for ?n=0 query.
            return []
        return entries[-last_n:]

    @property
    def capacity(self) -> int:
        """Configured max entries. Exposed for diagnostics log header."""
        return self._capacity


def setup_logging(
    *,
    level: int = logging.INFO,
    ring_capacity: int = DEFAULT_RING_CAPACITY,
    extra_handlers: Iterable[logging.Handler] = (),
) -> RingBufferLogHandler:
    """Install stderr stream handler + in-memory ring. Idempotent (safe to call multiple times)."""
    root = logging.getLogger()
    root.setLevel(level)
    formatter = logging.Formatter(fmt=DEFAULT_FORMAT, datefmt=DEFAULT_DATEFMT)

    # Clean up prior handlers to prevent double-logging on re-entry.
    for h in list(root.handlers):
        if getattr(h, "_openfollow_managed", False):
            root.removeHandler(h)
            try:
                h.close()
            except Exception:  # pragma: no cover – defensive
                pass

    stream = logging.StreamHandler()
    # Under systemd, prefix stderr lines with the sd-daemon priority token so
    # journald assigns the right per-line priority; off systemd keep clean lines.
    stream.setFormatter(
        _JournalPriorityFormatter(fmt=DEFAULT_FORMAT, datefmt=DEFAULT_DATEFMT) if _under_systemd() else formatter
    )
    stream._openfollow_managed = True  # type: ignore[attr-defined]
    root.addHandler(stream)

    ring = RingBufferLogHandler(capacity=ring_capacity)
    ring.setFormatter(formatter)
    ring._openfollow_managed = True  # type: ignore[attr-defined]
    root.addHandler(ring)

    for h in extra_handlers:
        h._openfollow_managed = True  # type: ignore[attr-defined]
        root.addHandler(h)

    return ring
