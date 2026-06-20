# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Unit tests for the small diagnostics helper functions in
``openfollow.web.routes``. The integration tests
(``tests/test_web_diagnostics_routes.py``) exercise the routes
end-to-end; this file covers the pure helpers – uptime / age
formatters, the alive-chip mapping, and the peer-probe wrappers
– directly so each branch is named in a failure rather than
hidden behind an HTTP probe."""

from __future__ import annotations

import time
import urllib.error

import pytest

from openfollow.web import routes as routes_module

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# _monotonic_age
# ---------------------------------------------------------------------------


def test_monotonic_age_renders_never_for_unused_sentinel() -> None:
    """The beacon-health properties seed ``last_send_ts`` to 0.0
    until the first event lands; the renderer maps that to
    ``"never"`` so the bundle reader doesn't see a confusing
    "infinitely long ago" age."""
    assert routes_module._monotonic_age(0.0) == "never"
    # Negative is unreachable in practice, but treated the same.
    assert routes_module._monotonic_age(-1.0) == "never"


def test_monotonic_age_renders_seconds_for_recent_event() -> None:
    """A real (recent) monotonic timestamp formats as ``N.Ns``.
    Asserts the suffix shape rather than the numeric value because
    ``time.monotonic`` advances during test execution."""
    out = routes_module._monotonic_age(time.monotonic() - 0.05)
    assert out.endswith("s")
    assert out != "never"


# ---------------------------------------------------------------------------
# _alive_chip
# ---------------------------------------------------------------------------


def test_alive_chip_returns_off_when_thread_dead() -> None:
    """Thread alive flag is False (post-``stop()``) – the chip class
    must read ``off`` regardless of error count, because reporting
    "warn / 3 errors" on a stopped thread would mislead the operator
    into thinking it might recover on its own."""
    assert routes_module._alive_chip(is_alive=False, errors=0) == "off"
    assert routes_module._alive_chip(is_alive=False, errors=5) == "off"


def test_alive_chip_returns_warn_when_alive_with_errors() -> None:
    """Alive thread + non-zero ``consecutive_errors`` → ``warn``.
    Tells the operator the thread is running but currently failing
    its work – distinguishable from the ``off`` case where it isn't
    running at all."""
    assert routes_module._alive_chip(is_alive=True, errors=1) == "warn"
    assert routes_module._alive_chip(is_alive=True, errors=99) == "warn"


def test_alive_chip_returns_ok_when_alive_clean() -> None:
    assert routes_module._alive_chip(is_alive=True, errors=0) == "ok"


# ---------------------------------------------------------------------------
# _format_uptime
# ---------------------------------------------------------------------------


def test_format_uptime_under_a_minute() -> None:
    """Sub-minute uptime renders as integer seconds – covers boot
    + the first minute of operation."""
    assert routes_module._format_uptime(0.0) == "0s"
    assert routes_module._format_uptime(45.7) == "46s"
    assert routes_module._format_uptime(59.4) == "59s"


def test_format_uptime_minutes_only() -> None:
    """Sub-hour uptime: ``Mm`` form, no padding to ``0h``."""
    assert routes_module._format_uptime(60) == "1m"
    assert routes_module._format_uptime(125) == "2m"
    assert routes_module._format_uptime(3599) == "59m"


def test_format_uptime_hours_and_minutes() -> None:
    """Hours-plus uptime: ``Hh Mm`` form. Picked so a multi-day
    rig still reads naturally on the diagnostics card."""
    assert routes_module._format_uptime(3600) == "1h 0m"
    assert routes_module._format_uptime(3660) == "1h 1m"
    assert routes_module._format_uptime(86_460) == "24h 1m"  # 1 day + 1m


# ---------------------------------------------------------------------------
# _probe_peer (HEAD probe wrapper)
# ---------------------------------------------------------------------------


def test_probe_peer_success_records_status_and_timing(monkeypatch) -> None:
    class _FakeResp:
        status = 200

        def __enter__(self) -> _FakeResp:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr(
        routes_module.urllib.request,
        "urlopen",
        lambda req, timeout: _FakeResp(),
    )
    out = routes_module._probe_peer("10.0.0.1", 80, "rig-a", expected_port=80)
    assert out["ok"] is True
    assert out["status"] == 200
    assert out["error"] == ""
    assert out["ms"] >= 0.0
    assert out["name"] == "rig-a"


def test_probe_peer_4xx_is_still_ok_for_diagnostics(monkeypatch) -> None:
    """A peer responding with 401 (PIN required) is still
    reachable – the bundle's "test peer connectivity" probe cares
    about the network path, not the auth state. Map any
    ``HTTPError`` with code < 500 to ``ok=True`` so the chip reads
    as success."""
    err = urllib.error.HTTPError(
        url="http://10.0.0.1/",
        code=401,
        msg="Unauthorized",
        hdrs=None,  # type: ignore[arg-type]
        fp=None,
    )

    def boom(req, timeout):  # noqa: ARG001
        raise err

    monkeypatch.setattr(
        routes_module.urllib.request,
        "urlopen",
        boom,
    )
    out = routes_module._probe_peer("10.0.0.1", 80, "rig-a", expected_port=80)
    assert out["ok"] is True
    assert out["status"] == 401


def test_probe_peer_5xx_marks_not_ok(monkeypatch) -> None:
    err = urllib.error.HTTPError(
        url="http://10.0.0.1/",
        code=502,
        msg="Bad Gateway",
        hdrs=None,  # type: ignore[arg-type]
        fp=None,
    )
    monkeypatch.setattr(
        routes_module.urllib.request,
        "urlopen",
        lambda req, timeout: (_ for _ in ()).throw(err),
    )
    out = routes_module._probe_peer("10.0.0.1", 80, "rig-a", expected_port=80)
    assert out["ok"] is False
    assert out["status"] == 502


def test_probe_peer_handles_connection_refused(monkeypatch) -> None:
    """``URLError`` (no route / refused / DNS-fail) lands in the
    catch-all branch, which records the error string."""

    def boom(req, timeout):  # noqa: ARG001
        raise urllib.error.URLError(
            ConnectionRefusedError(61, "Connection refused"),
        )

    monkeypatch.setattr(
        routes_module.urllib.request,
        "urlopen",
        boom,
    )
    out = routes_module._probe_peer("10.0.0.1", 80, "rig-a", expected_port=80)
    assert out["ok"] is False
    assert out["status"] == 0
    assert "refused" in out["error"].lower()


def test_probe_peer_handles_timeout(monkeypatch) -> None:
    def boom(req, timeout):  # noqa: ARG001
        raise TimeoutError("timed out")

    monkeypatch.setattr(
        routes_module.urllib.request,
        "urlopen",
        boom,
    )
    out = routes_module._probe_peer("10.0.0.1", 80, "rig-a", expected_port=80)
    assert out["ok"] is False
    assert out["status"] == 0
    assert "timed out" in out["error"]


# ---------------------------------------------------------------------------
# _short_probe_error
# ---------------------------------------------------------------------------


def test_short_probe_error_strips_errno_prefix() -> None:
    """The stdlib renders ``OSError`` as ``"[Errno 61] Connection
    refused"``; the operator-facing card just wants
    ``"Connection refused"``."""
    err = OSError(61, "Connection refused")
    assert routes_module._short_probe_error(err) == "Connection refused"


def test_short_probe_error_keeps_first_line_only() -> None:
    """A multi-line message gets collapsed to the first line so the
    inline cell stays one row tall."""
    err = RuntimeError("first line\nsecond line should not appear")
    out = routes_module._short_probe_error(err)
    assert out == "first line"


def test_short_probe_error_caps_length() -> None:
    """Pathological exception messages are clipped at 120 chars
    so the table cell can't blow out the layout."""
    err = RuntimeError("x" * 500)
    out = routes_module._short_probe_error(err)
    assert len(out) == 120


def test_short_probe_error_falls_back_to_class_name() -> None:
    """A bare exception with empty ``__str__`` (the rare wrapper
    error from a subprocess that lost stdout) reports the class
    name so the operator at least sees what kind of failure
    happened."""

    class _Bare(Exception):
        def __str__(self) -> str:
            return ""

    assert routes_module._short_probe_error(_Bare()) == "_Bare"
