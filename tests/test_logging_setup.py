# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for ``openfollow.logging_setup``.

The handler is small and pure-Python; tests focus on:
    - ring eviction order under load
    - thread safety (writers + readers)
    - ``snapshot(last_n=...)`` honouring the cap
    - ``setup_logging`` being idempotent across re-entry
"""

from __future__ import annotations

import logging
import os
import re
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from openfollow.logging_setup import (
    DEFAULT_RING_CAPACITY,
    LOG_FILE_BACKUPS,
    LogFileHandler,
    RingBufferLogHandler,
    ThrottledExceptionLogger,
    _JournalPriorityFormatter,
    _sd_priority,
    _under_systemd,
    active_log_file,
    read_log_file,
    setup_logging,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_root_logger() -> Iterator[None]:
    """Snapshot the root logger's handlers + level before each test
    and restore both afterwards. Without this, tests that call
    ``setup_logging(level=DEBUG)`` would leak that level into
    sibling tests (``test_video_receiver`` does caplog-based
    assertions that rely on the default WARNING root)."""
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    yield
    root.setLevel(original_level)
    for h in list(root.handlers):
        if h not in original_handlers:
            root.removeHandler(h)
    for h in original_handlers:
        if h not in root.handlers:
            root.addHandler(h)


def _formatter() -> logging.Formatter:
    return logging.Formatter(fmt="%(message)s")


def test_ring_evicts_oldest_when_capacity_exceeded() -> None:
    h = RingBufferLogHandler(capacity=3)
    h.setFormatter(_formatter())
    for i in range(5):
        h.emit(_record(f"msg{i}"))
    snap = h.snapshot()
    # The deque is bounded so the first two writes get evicted.
    assert snap == ["msg2", "msg3", "msg4"]


def test_snapshot_returns_a_defensive_copy() -> None:
    h = RingBufferLogHandler(capacity=8)
    h.setFormatter(_formatter())
    h.emit(_record("first"))
    snap = h.snapshot()
    snap.append("tampered")
    # The handler's internal state is unaffected.
    assert h.snapshot() == ["first"]


def test_snapshot_last_n_caps_response() -> None:
    h = RingBufferLogHandler(capacity=10)
    h.setFormatter(_formatter())
    for i in range(8):
        h.emit(_record(f"m{i}"))
    assert h.snapshot(last_n=3) == ["m5", "m6", "m7"]
    # ``last_n`` larger than the ring just returns everything.
    assert h.snapshot(last_n=99) == [f"m{i}" for i in range(8)]
    # ``None`` (the default) returns everything.
    assert h.snapshot(last_n=None) == [f"m{i}" for i in range(8)]
    # ``last_n=0`` honours the cap explicitly; entries[-0:] would return the full buffer without it.
    assert h.snapshot(last_n=0) == []
    # Negative values are treated the same as zero rather than
    # silently slicing from the tail.
    assert h.snapshot(last_n=-5) == []


def test_capacity_property_matches_constructor() -> None:
    h = RingBufferLogHandler(capacity=42)
    assert h.capacity == 42
    # Default value is the spec's published cap.
    assert RingBufferLogHandler().capacity == DEFAULT_RING_CAPACITY


def test_concurrent_emit_does_not_lose_records() -> None:
    h = RingBufferLogHandler(capacity=10_000)
    h.setFormatter(_formatter())
    n_threads = 8
    per_thread = 500
    barrier = threading.Barrier(n_threads)

    def worker(tid: int) -> None:
        barrier.wait()
        for i in range(per_thread):
            h.emit(_record(f"t{tid}-i{i}"))

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    snap = h.snapshot()
    assert len(snap) == n_threads * per_thread


def test_setup_logging_attaches_ring_to_root() -> None:
    ring = setup_logging(level=logging.DEBUG, ring_capacity=64)
    logger = logging.getLogger("openfollow.test.logging")
    logger.info("hello world")
    snap = ring.snapshot()
    assert any("hello world" in line for line in snap)
    # Format matches the spec's default – module name is in the
    # rendered line, as the operator sees in journalctl.
    assert any("openfollow.test.logging" in line for line in snap)


def test_setup_logging_is_idempotent() -> None:
    """Re-entering ``main`` (the restart-loop) calls ``setup_logging``
    a second time. Without idempotence the ring would double-attach
    and every line would land twice."""
    setup_logging(ring_capacity=8)
    second_ring = setup_logging(ring_capacity=8)
    managed = [h for h in logging.getLogger().handlers if getattr(h, "_openfollow_managed", False)]
    # Exactly one stream + one ring after the second call.
    assert len(managed) == 2
    # Logging once produces exactly one ring entry, not two.
    logging.getLogger("openfollow.test.idempotent").info("once")
    assert sum(1 for line in second_ring.snapshot() if "once" in line) == 1


def test_setup_logging_closes_handlers_on_re_entry() -> None:
    closed: list[str] = []

    class _CloseTracker(logging.Handler):
        def __init__(self) -> None:
            super().__init__()
            self._tag = "tracker"

        def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover
            pass

        def close(self) -> None:
            closed.append(self._tag)
            super().close()

    tracker = _CloseTracker()
    setup_logging(ring_capacity=8, extra_handlers=[tracker])
    # Second call drops the tracker – close() must have fired.
    setup_logging(ring_capacity=8)
    assert "tracker" in closed


def test_setup_logging_close_failure_is_swallowed() -> None:

    class _BadCloseHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover
            pass

        def close(self) -> None:
            raise RuntimeError("close failed")

    bad = _BadCloseHandler()
    setup_logging(ring_capacity=8, extra_handlers=[bad])
    # Should not raise.
    setup_logging(ring_capacity=8)


def test_setup_logging_extra_handlers_round_trip() -> None:
    custom = RingBufferLogHandler(capacity=4)
    custom.setFormatter(logging.Formatter("%(message)s"))
    setup_logging(ring_capacity=8, extra_handlers=[custom])
    logging.getLogger("openfollow.test.extra").warning("payload")
    # The custom handler was wired into root and saw the record.
    assert any("payload" in line for line in custom.snapshot())
    # And it carries the managed flag – so the next setup call
    # would clean it up alongside the built-in handlers.
    assert getattr(custom, "_openfollow_managed", False) is True


# ---------------------------------------------------------------------------
# Log file (the macOS app)
# ---------------------------------------------------------------------------

_FILE_LINE_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \[(?P<level>[A-Z]+)\] (?P<name>[\w.]+): (?P<msg>.*)$")


@pytest.fixture
def std_fds(tmp_path: Path) -> Iterator[tuple[int, int]]:
    """Stand-ins for stdout and stderr, so no test redirects the worker's own.

    Closes every log file handler a test left on the root logger first: the
    root-logger fixture removes handlers but does not close them."""
    fds = (
        os.open(tmp_path / "stdout.stand-in", os.O_WRONLY | os.O_CREAT, 0o600),
        os.open(tmp_path / "stderr.stand-in", os.O_WRONLY | os.O_CREAT, 0o600),
    )
    yield fds
    for h in logging.getLogger().handlers:
        if isinstance(h, LogFileHandler):
            h.close()
    for fd in fds:
        os.close(fd)


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def test_setup_logging_writes_dated_records_to_the_log_file(tmp_path: Path, std_fds: tuple[int, int]) -> None:
    log_file = tmp_path / "Logs" / "OpenFollow" / "openfollow.log"
    ring = setup_logging(level=logging.INFO, log_file=log_file, std_fds=std_fds)
    logging.getLogger("openfollow.test.file").warning("disk nearly full")
    # The file spans days and restarts, so each line carries its date as well as its time.
    matches = [m for m in map(_FILE_LINE_RE.match, _lines(log_file)) if m]
    assert ("WARNING", "openfollow.test.file", "disk nearly full") in [
        (m["level"], m["name"], m["msg"]) for m in matches
    ]
    assert any("disk nearly full" in line for line in ring.snapshot())


def test_setup_logging_marks_where_each_process_starts_in_the_log_file(
    tmp_path: Path, std_fds: tuple[int, int]
) -> None:
    log_file = tmp_path / "openfollow.log"
    setup_logging(log_file=log_file, std_fds=std_fds)
    setup_logging(log_file=log_file, std_fds=std_fds)
    starts = [line for line in _lines(log_file) if f"Process {os.getpid()} logging to {log_file}" in line]
    assert len(starts) == 2


def test_setup_logging_with_a_log_file_leaves_stderr_to_the_file(
    tmp_path: Path, std_fds: tuple[int, int], capsys: pytest.CaptureFixture[str]
) -> None:
    """The file takes stderr over, so a stderr handler on top would write each record twice."""
    setup_logging(log_file=tmp_path / "openfollow.log", std_fds=std_fds)
    logging.getLogger("openfollow.test.file").warning("written once")
    assert "written once" not in capsys.readouterr().err


def test_the_log_file_takes_what_the_process_writes_to_stdout_and_stderr(
    tmp_path: Path, std_fds: tuple[int, int]
) -> None:
    """GLib and GStreamer warnings, uncaught thread exceptions and faulthandler
    write straight to the standard streams, past the logging module."""
    log_file = tmp_path / "openfollow.log"
    setup_logging(log_file=log_file, std_fds=std_fds)
    os.write(std_fds[0], b"printed to stdout\n")
    os.write(std_fds[1], b"(OpenFollow:42): GStreamer-CRITICAL **: native warning\n")
    lines = _lines(log_file)
    assert "printed to stdout" in lines
    assert "(OpenFollow:42): GStreamer-CRITICAL **: native warning" in lines


def _file_handler(path: Path, std_fds: tuple[int, int], *, max_bytes: int = 200) -> LogFileHandler:
    handler = LogFileHandler(path, max_bytes=max_bytes, backup_count=2, std_fds=std_fds)
    handler.setFormatter(_formatter())
    return handler


def test_the_log_file_rotates_by_size_and_keeps_its_copies_bounded(tmp_path: Path, std_fds: tuple[int, int]) -> None:
    log_file = tmp_path / "openfollow.log"
    handler = _file_handler(log_file, std_fds)
    try:
        for i in range(40):
            handler.emit(_record(f"record {i:02d} " + "x" * 40))
    finally:
        handler.close()
    assert sorted(p.name for p in tmp_path.glob("openfollow.log*")) == [
        "openfollow.log",
        "openfollow.log.1",
        "openfollow.log.2",
    ]
    for path in tmp_path.glob("openfollow.log*"):
        # One record past the cap at most: a rotation happens before the record that would cross it.
        assert path.stat().st_size < 200 + 60
    assert _lines(log_file)[-1].startswith("record 39")


def test_the_process_output_follows_the_log_file_through_a_rotation(tmp_path: Path, std_fds: tuple[int, int]) -> None:
    log_file = tmp_path / "openfollow.log"
    handler = _file_handler(log_file, std_fds)
    try:
        while not (tmp_path / "openfollow.log.1").exists():
            handler.emit(_record("filler " + "x" * 40))
        os.write(std_fds[1], b"after the rotation\n")
    finally:
        handler.close()
    assert "after the rotation" in _lines(log_file)
    assert "after the rotation" not in _lines(tmp_path / "openfollow.log.1")


def test_the_log_file_counts_process_output_towards_its_size(tmp_path: Path, std_fds: tuple[int, int]) -> None:
    log_file = tmp_path / "openfollow.log"
    handler = _file_handler(log_file, std_fds)
    try:
        handler.emit(_record("first"))
        os.write(std_fds[1], b"n" * 300 + b"\n")
        handler.emit(_record("second"))
    finally:
        handler.close()
    assert _lines(log_file) == ["second"]
    assert _lines(tmp_path / "openfollow.log.1")[0] == "first"


def test_the_log_file_rotates_on_process_output_alone(tmp_path: Path, std_fds: tuple[int, int]) -> None:
    """A GLib warning repeated every frame fills the file with no record to trigger a size check."""
    log_file = tmp_path / "openfollow.log"
    handler = _file_handler(log_file, std_fds)
    try:
        os.write(std_fds[1], b"n" * 100 + b"\n")
        handler.roll_over_if_full()
        assert not (tmp_path / "openfollow.log.1").exists()
        os.write(std_fds[1], b"n" * 100 + b"\n")
        handler.roll_over_if_full()
    finally:
        handler.close()
    assert (tmp_path / "openfollow.log.1").stat().st_size == 202
    assert log_file.stat().st_size == 0


class _SignallingLogFileHandler(LogFileHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.rolled = threading.Event()
        super().__init__(*args, **kwargs)

    def doRollover(self) -> None:
        super().doRollover()
        self.rolled.set()


def test_the_log_file_checks_its_size_in_the_background(tmp_path: Path, std_fds: tuple[int, int]) -> None:
    handler = _SignallingLogFileHandler(
        tmp_path / "openfollow.log", max_bytes=200, backup_count=2, std_fds=std_fds, check_every_s=0.01
    )
    try:
        os.write(std_fds[1], b"n" * 300 + b"\n")
        assert handler.rolled.wait(timeout=10)
    finally:
        handler.close()


def test_closing_the_log_file_stops_its_size_checks(tmp_path: Path, std_fds: tuple[int, int]) -> None:
    before = set(threading.enumerate())
    handler = _file_handler(tmp_path / "openfollow.log", std_fds)
    (watcher,) = set(threading.enumerate()) - before
    handler.close()
    watcher.join(timeout=10)
    assert not watcher.is_alive()


def test_logging_shutdown_closes_the_log_file_while_a_size_check_waits(
    tmp_path: Path, std_fds: tuple[int, int]
) -> None:
    """``logging.shutdown`` holds the handler lock across ``close``: a size check
    waiting for that lock must neither hold ``close`` up nor fail once it runs."""
    # A zero interval keeps the background check queued on the lock as often as not.
    handler = LogFileHandler(
        tmp_path / "openfollow.log", max_bytes=200, backup_count=2, std_fds=std_fds, check_every_s=0.0
    )
    errors: list[BaseException] = []

    def check() -> None:
        try:
            handler.roll_over_if_full()
        except BaseException as exc:  # noqa: BLE001 - reported by the assertion below
            errors.append(exc)

    checking = threading.Thread(target=check, daemon=True)

    def shut_down() -> None:
        handler.acquire()
        try:
            checking.start()
            handler.close()
        finally:
            handler.release()

    closer = threading.Thread(target=shut_down, daemon=True)
    closer.start()
    closer.join(timeout=10)
    checking.join(timeout=10)
    assert not closer.is_alive()
    assert not checking.is_alive()
    assert errors == []


def test_setup_logging_falls_back_to_stderr_when_the_log_file_cannot_open(
    tmp_path: Path, std_fds: tuple[int, int], capsys: pytest.CaptureFixture[str]
) -> None:
    blocker = tmp_path / "Logs"
    blocker.write_text("a file where the log directory belongs", encoding="utf-8")
    ring = setup_logging(log_file=blocker / "OpenFollow" / "openfollow.log", std_fds=std_fds)
    logging.getLogger("openfollow.test.file").warning("still reported")
    assert "still reported" in capsys.readouterr().err
    assert any("[WARNING]" in line and "Cannot write the log file" in line for line in ring.snapshot())
    assert active_log_file() is None


def test_active_log_file_names_the_file_setup_logging_installed(tmp_path: Path, std_fds: tuple[int, int]) -> None:
    setup_logging()
    assert active_log_file() is None
    log_file = tmp_path / "openfollow.log"
    setup_logging(log_file=log_file, std_fds=std_fds)
    assert active_log_file() == log_file
    # Re-entry without one closes the file handler with the other managed handlers.
    setup_logging()
    assert active_log_file() is None


def _write_kept_files(log_file: Path, *contents: str) -> None:
    """``contents`` oldest first: the last one is the current file, the rest its rotated copies."""
    names = [f"{log_file.name}.{i}" for i in range(len(contents) - 1, 0, -1)] + [log_file.name]
    for name, text in zip(names, contents, strict=True):
        (log_file.parent / name).write_text(text, encoding="utf-8")


def test_read_log_file_joins_the_rotated_copies_oldest_first(tmp_path: Path) -> None:
    log_file = tmp_path / "openfollow.log"
    _write_kept_files(log_file, "a1\na2\n", "b1\nb2\n", "c1\n")
    assert read_log_file(log_file) == ["a1", "a2", "b1", "b2", "c1"]


@pytest.mark.parametrize(
    ("last_n", "expected"),
    [
        (1, ["c1"]),
        (2, ["b2", "c1"]),
        (4, ["a2", "b1", "b2", "c1"]),
        (99, ["a1", "a2", "b1", "b2", "c1"]),
        (0, []),
    ],
)
def test_read_log_file_tail_spans_the_rotated_copies(tmp_path: Path, last_n: int, expected: list[str]) -> None:
    log_file = tmp_path / "openfollow.log"
    _write_kept_files(log_file, "a1\na2\n", "b1\nb2\n", "c1\n")
    assert read_log_file(log_file, last_n=last_n) == expected


def test_read_log_file_reads_only_the_copies_rotation_keeps(tmp_path: Path) -> None:
    log_file = tmp_path / "openfollow.log"
    # The oldest is one copy more than rotation keeps.
    _write_kept_files(log_file, *(f"file {i}\n" for i in range(LOG_FILE_BACKUPS + 2)))
    assert read_log_file(log_file) == [f"file {i}" for i in range(1, LOG_FILE_BACKUPS + 2)]


def test_read_log_file_reads_no_copy_past_a_missing_one(tmp_path: Path) -> None:
    """Rotation fills ``.1``, ``.2``, … in order, so a gap ends the kept set."""
    log_file = tmp_path / "openfollow.log"
    _write_kept_files(log_file, "stale\n", "missing\n", "current\n")
    (tmp_path / "openfollow.log.1").unlink()
    assert read_log_file(log_file) == ["current"]


def test_read_log_file_raises_without_the_current_file(tmp_path: Path) -> None:
    (tmp_path / "openfollow.log.1").write_text("orphaned copy\n", encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        read_log_file(tmp_path / "openfollow.log")


def test_read_log_file_replaces_bytes_that_are_not_utf8(tmp_path: Path) -> None:
    """Native output is not guaranteed to be UTF-8."""
    log_file = tmp_path / "openfollow.log"
    log_file.write_bytes(b"ok\nbad \xff byte\n")
    assert read_log_file(log_file) == ["ok", "bad \ufffd byte"]


# ---------------------------------------------------------------------------
# journald priority prefix (sd-daemon "<N>")
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        (logging.CRITICAL, 2),
        (logging.ERROR, 3),
        (logging.WARNING, 4),
        (logging.INFO, 6),
        (logging.DEBUG, 7),
        (logging.DEBUG - 5, 7),  # below DEBUG still maps to the debug priority
    ],
)
def test_sd_priority_maps_level_to_syslog_priority(level: int, expected: int) -> None:
    assert _sd_priority(level) == expected


def test_journal_priority_formatter_prepends_token() -> None:
    fmt = _JournalPriorityFormatter(fmt="%(message)s")
    assert fmt.format(_record_at(logging.WARNING, "danger")) == "<4>danger"


def test_journal_priority_formatter_prefixes_every_line() -> None:
    # A multi-line record (e.g. an exception traceback) must carry the priority
    # token on every line – journald reads stderr line-by-line, so an unprefixed
    # continuation line lands at default priority and `journalctl -p` misses it.
    fmt = _JournalPriorityFormatter(fmt="%(message)s")
    out = fmt.format(_record_at(logging.ERROR, "boom\nTraceback line 1\nline 2"))
    assert out == "<3>boom\n<3>Traceback line 1\n<3>line 2"


def test_under_systemd_follows_journal_stream_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JOURNAL_STREAM", raising=False)
    assert _under_systemd() is False
    monkeypatch.setenv("JOURNAL_STREAM", "8:123456")
    assert _under_systemd() is True


# ---------------------------------------------------------------------------
# ThrottledExceptionLogger
# ---------------------------------------------------------------------------


class _Clock:
    """Controllable monotonic clock."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def _raise_then_log(throttle: ThrottledExceptionLogger) -> None:
    """Call ``throttle.log()`` from inside an ``except`` so exc_info is live."""
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        throttle.log()


def test_throttled_logger_emits_first_and_suppresses_within_interval(caplog: pytest.LogCaptureFixture) -> None:
    clock = _Clock()
    logger = logging.getLogger("openfollow.test.throttle.a")
    throttle = ThrottledExceptionLogger(logger, "tick failed", interval=5.0, clock=clock)

    with caplog.at_level(logging.ERROR, logger=logger.name):
        for step in range(4):  # t = 0, 1, 2, 3 – all within the 5 s window
            clock.t = float(step)
            _raise_then_log(throttle)

    records = [r for r in caplog.records if r.name == logger.name]
    assert len(records) == 1  # only the first emitted; the rest were suppressed
    assert records[0].getMessage() == "tick failed"
    # logger.exception captured the active traceback.
    assert records[0].exc_info is not None


def test_throttled_logger_reports_suppressed_count_after_interval(caplog: pytest.LogCaptureFixture) -> None:
    clock = _Clock()
    logger = logging.getLogger("openfollow.test.throttle.b")
    throttle = ThrottledExceptionLogger(logger, "tick failed", interval=5.0, clock=clock)

    with caplog.at_level(logging.ERROR, logger=logger.name):
        clock.t = 0.0
        _raise_then_log(throttle)  # emit
        clock.t = 1.0
        _raise_then_log(throttle)  # suppressed (1)
        clock.t = 2.0
        _raise_then_log(throttle)  # suppressed (2)
        clock.t = 6.0
        _raise_then_log(throttle)  # interval elapsed -> emit with the count

    messages = [r.getMessage() for r in caplog.records if r.name == logger.name]
    assert messages[0] == "tick failed"
    assert messages[1] == "tick failed (2 more suppressed in the last 5s)"


def test_throttled_logger_resets_suppressed_count_after_emit(caplog: pytest.LogCaptureFixture) -> None:
    clock = _Clock()
    logger = logging.getLogger("openfollow.test.throttle.c")
    throttle = ThrottledExceptionLogger(logger, "tick failed", interval=5.0, clock=clock)

    with caplog.at_level(logging.ERROR, logger=logger.name):
        clock.t = 0.0
        _raise_then_log(throttle)  # emit
        clock.t = 1.0
        _raise_then_log(throttle)  # suppressed (1)
        clock.t = 6.0
        _raise_then_log(throttle)  # emit "(1 more suppressed...)"
        clock.t = 12.0
        _raise_then_log(throttle)  # emit, count reset -> no suppressed suffix

    messages = [r.getMessage() for r in caplog.records if r.name == logger.name]
    assert messages == [
        "tick failed",
        "tick failed (1 more suppressed in the last 5s)",
        "tick failed",
    ]


def _managed_stream() -> logging.StreamHandler:
    return next(
        h
        for h in logging.getLogger().handlers
        if isinstance(h, logging.StreamHandler) and getattr(h, "_openfollow_managed", False)
    )


def test_setup_logging_prefixes_stderr_priority_under_systemd(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOURNAL_STREAM", "8:123456")
    setup_logging(level=logging.INFO)
    line = _managed_stream().format(_record_at(logging.WARNING, "boom"))
    assert line.startswith("<4>")  # journald parses the token to set priority


def test_setup_logging_leaves_stderr_clean_off_systemd(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JOURNAL_STREAM", raising=False)
    setup_logging(level=logging.INFO)
    line = _managed_stream().format(_record_at(logging.WARNING, "boom"))
    assert not line.startswith("<")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _record(message: str) -> logging.LogRecord:
    return _record_at(logging.INFO, message)


def _record_at(level: int, message: str) -> logging.LogRecord:
    return logging.LogRecord(
        name="t",
        level=level,
        pathname="test",
        lineno=1,
        msg=message,
        args=None,
        exc_info=None,
    )
