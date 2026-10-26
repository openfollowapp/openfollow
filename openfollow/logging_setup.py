# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Process-wide logging configuration with bounded in-memory ring buffer fallback.

Records go to stderr (the journal on a Pi), or to a size-rotated log file (the
macOS app). The ring buffer is used when neither the journal nor a log file can
be read. Sized at 2000 lines (~160 KB).
"""

from __future__ import annotations

import io
import logging
import os
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from logging.handlers import RotatingFileHandler
from pathlib import Path

logger = logging.getLogger(__name__)

# Default cap for the in-memory ring.
# Public so tests can drive a smaller cap without monkeypatching.
DEFAULT_RING_CAPACITY = 2000

# Default format string for log records.
DEFAULT_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
DEFAULT_DATEFMT = "%H:%M:%S"

# A log file spans days and restarts, so its lines carry the date too.
LOG_FILE_DATEFMT = "%Y-%m-%d %H:%M:%S"
# 5 MB a file, the current one and four rotated copies: 25 MB at most.
LOG_FILE_MAX_BYTES = 5 * 1024 * 1024
LOG_FILE_BACKUPS = 4
# How often a log file checks its size between records.
LOG_FILE_CHECK_S = 10.0
# stdout and stderr, which a log file takes over.
STD_FDS = (1, 2)


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


class LogFileHandler(RotatingFileHandler):
    """Size-rotated log file that also takes the process's stdout and stderr.

    Every file it opens is duplicated onto ``std_fds``, so what the process
    writes there past the logging module (GLib and GStreamer warnings, uncaught
    thread exceptions, faulthandler's crash tracebacks) lands in the same file
    and follows each rotation. The rotation size counts those writes too: the
    duplicated descriptors share the stream's file offset. A background check
    every ``check_every_s`` rotates a file that output alone has filled.
    """

    def __init__(
        self,
        path: Path,
        *,
        max_bytes: int = LOG_FILE_MAX_BYTES,
        backup_count: int = LOG_FILE_BACKUPS,
        std_fds: tuple[int, ...] = STD_FDS,
        check_every_s: float = LOG_FILE_CHECK_S,
    ) -> None:
        self._std_fds = std_fds
        path.parent.mkdir(parents=True, exist_ok=True)
        super().__init__(path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8")
        self._closing = threading.Event()
        self._watcher = threading.Thread(
            target=self._watch, args=(check_every_s,), daemon=True, name="log-file-rotation"
        )
        self._watcher.start()

    def _open(self) -> io.TextIOWrapper:
        stream = super()._open()
        for fd in self._std_fds:
            os.dup2(stream.fileno(), fd)
        return stream

    def roll_over_if_full(self) -> None:
        """Rotate once the file has reached its size, whoever wrote to it."""
        self.acquire()
        try:
            if self.stream is not None and self.stream.tell() >= self.maxBytes:
                self.doRollover()
        finally:
            self.release()

    def _watch(self, every_s: float) -> None:
        while not self._closing.wait(every_s):
            self.roll_over_if_full()

    def close(self) -> None:
        # No join: ``logging.shutdown`` calls this holding the lock a check may be waiting for.
        self._closing.set()
        super().close()


def read_log_file(path: Path, *, last_n: int | None = None) -> list[str]:
    """Lines of ``path`` and its rotated copies, oldest first; the last ``last_n`` when given.

    Raises ``OSError`` when ``path`` itself cannot be read.
    """
    chunks: list[list[str]] = []
    count = 0
    for index in range(LOG_FILE_BACKUPS + 1):
        file = path if index == 0 else path.with_name(f"{path.name}.{index}")
        try:
            text = file.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            if index == 0:
                raise
            # Rotation fills .1, .2, ... in order, so a gap ends the kept set.
            break
        chunks.append(text.splitlines())
        count += len(chunks[-1])
        if last_n is not None and count >= last_n:
            break
    lines = [line for chunk in reversed(chunks) for line in chunk]
    return lines if last_n is None else lines[max(0, len(lines) - last_n) :]


def active_log_file() -> Path | None:
    """The log file ``setup_logging`` installed, ``None`` when records go to stderr."""
    for handler in logging.getLogger().handlers:
        if isinstance(handler, LogFileHandler):
            return Path(handler.baseFilename)
    return None


def setup_logging(
    *,
    level: int = logging.INFO,
    ring_capacity: int = DEFAULT_RING_CAPACITY,
    extra_handlers: Iterable[logging.Handler] = (),
    log_file: Path | None = None,
    std_fds: tuple[int, ...] = STD_FDS,
) -> RingBufferLogHandler:
    """Install the output handler + in-memory ring. Idempotent (safe to call multiple times).

    Records go to stderr, or to ``log_file``, which then takes ``std_fds`` over
    too. A log file that cannot be opened leaves the records on stderr.
    """
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

    output: logging.Handler | None = None
    file_error: OSError | None = None
    if log_file is not None:
        try:
            output = LogFileHandler(log_file, std_fds=std_fds)
        except OSError as exc:
            file_error = exc
        else:
            output.setFormatter(logging.Formatter(fmt=DEFAULT_FORMAT, datefmt=LOG_FILE_DATEFMT))
    if output is None:
        output = logging.StreamHandler()
        # Under systemd, prefix stderr lines with the sd-daemon priority token so
        # journald assigns the right per-line priority; off systemd keep clean lines.
        output.setFormatter(
            _JournalPriorityFormatter(fmt=DEFAULT_FORMAT, datefmt=DEFAULT_DATEFMT) if _under_systemd() else formatter
        )
    output._openfollow_managed = True  # type: ignore[attr-defined]
    root.addHandler(output)

    ring = RingBufferLogHandler(capacity=ring_capacity)
    ring.setFormatter(formatter)
    ring._openfollow_managed = True  # type: ignore[attr-defined]
    root.addHandler(ring)

    for h in extra_handlers:
        h._openfollow_managed = True  # type: ignore[attr-defined]
        root.addHandler(h)

    if file_error is not None:
        logger.warning("Cannot write the log file %s, logging to stderr: %s", log_file, file_error)
    elif log_file is not None:
        # The file spans restarts: this line marks where each process starts.
        logger.info("Process %d logging to %s", os.getpid(), log_file)
    return ring
