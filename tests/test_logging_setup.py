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
import threading
from collections.abc import Iterator

import pytest

from openfollow.logging_setup import (
    DEFAULT_RING_CAPACITY,
    RingBufferLogHandler,
    ThrottledExceptionLogger,
    _JournalPriorityFormatter,
    _sd_priority,
    _under_systemd,
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
