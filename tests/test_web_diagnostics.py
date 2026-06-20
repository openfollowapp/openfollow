# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for ``openfollow.web.diagnostics``.

Each section's collector is exercised independently against fakes.
Bundle-assembly test wires everything end-to-end.
"""

from __future__ import annotations

import logging
import platform
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from openfollow.logging_setup import RingBufferLogHandler
from openfollow.web import diagnostics as diag

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fmt_record(text: str) -> logging.LogRecord:
    return logging.LogRecord(
        name="t",
        level=logging.INFO,
        pathname="x",
        lineno=1,
        msg=text,
        args=None,
        exc_info=None,
    )


def _seed_ring(*lines: str) -> RingBufferLogHandler:
    ring = RingBufferLogHandler(capacity=64)
    ring.setFormatter(logging.Formatter("%(message)s"))
    for line in lines:
        ring.emit(_fmt_record(line))
    return ring


# ---------------------------------------------------------------------------
# Subprocess helper
# ---------------------------------------------------------------------------


def test_run_missing_binary_returns_unavailable_sentinel(monkeypatch) -> None:
    monkeypatch.setattr(diag.shutil, "which", lambda _name: None)
    rc, out = diag._run(["fakecmd"])
    assert rc == -1
    assert out.startswith("[unavailable: fakecmd not found]")


def test_run_timeout_returns_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(diag.shutil, "which", lambda _name: "/bin/fake")

    def boom(*_a, **_kw):
        raise subprocess.TimeoutExpired(cmd="fake", timeout=1)

    monkeypatch.setattr(diag.subprocess, "run", boom)
    rc, out = diag._run(["fake"])
    assert rc == -1
    assert "timed out" in out


def test_run_oserror_returns_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(diag.shutil, "which", lambda _n: "/bin/fake")

    def boom(*_a, **_kw):
        raise OSError("ENOENT", "boom")

    monkeypatch.setattr(diag.subprocess, "run", boom)
    rc, out = diag._run(["fake"])
    assert rc == -1
    assert "[unavailable: fake:" in out


def test_run_stdout_on_error_keeps_stdout_despite_nonzero_exit(monkeypatch) -> None:
    # ``du`` totals a tree but exits 1 when one sub-dir is unreadable (apt's
    # root-only, empty ``archives/partial``). With ``stdout_on_error`` the
    # valid stdout total must survive rather than be replaced by the stderr
    # warning.
    monkeypatch.setattr(diag.shutil, "which", lambda _n: "/usr/bin/du")
    monkeypatch.setattr(
        diag.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(
            returncode=1,
            stdout="208\t/var/cache/apt/archives\n",
            stderr="du: cannot read directory '.../partial': Permission denied\n",
        ),
    )
    rc, out = diag._run(["du", "-s", "/x"], stdout_on_error=True)
    assert rc == 1
    assert out == "208\t/var/cache/apt/archives"


def test_run_stdout_on_error_falls_back_to_stderr_when_no_stdout(monkeypatch) -> None:
    # A non-zero exit with no stdout still yields the stderr message even when
    # ``stdout_on_error`` is set.
    monkeypatch.setattr(diag.shutil, "which", lambda _n: "/usr/bin/du")
    monkeypatch.setattr(
        diag.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(returncode=1, stdout="", stderr="boom\n"),
    )
    rc, out = diag._run(["du"], stdout_on_error=True)
    assert rc == 1
    assert out == "boom"


# ---------------------------------------------------------------------------
# Section A – service / port
# ---------------------------------------------------------------------------


def test_collect_service_no_providers() -> None:
    rows = diag.collect_service(diag.DiagnosticsProviders())
    assert any("not applicable" in r for r in rows)


def test_collect_service_with_providers() -> None:
    p = diag.DiagnosticsProviders(
        web_port_configured=lambda: 80,
        web_port_display=lambda: 8080,
        process_uptime_s=lambda: "1h 12m",
        process_pid=lambda: 4242,
        restart_count=lambda: 0,
    )
    rows = diag.collect_service(p)
    joined = "\n".join(rows)
    assert "Configured web_port:        80" in joined
    assert "Actual display_port:        8080" in joined
    assert "1h 12m" in joined
    assert "4242" in joined


def test_safely_renders_unavailable_on_exception() -> None:
    def boom() -> int:
        raise RuntimeError("nope")

    out = diag._safely(boom, "x")
    assert out.startswith("[unavailable: x:")


def test_safely_value_returns_default_and_sentinel_on_exception() -> None:
    """Companion to ``_safely`` for providers that return structured
    data – callers iterate the returned value, so we hand back the
    default plus the rendered sentinel string."""

    def boom() -> dict[str, int]:
        raise RuntimeError("nope")

    value, err = diag._safely_value(boom, "x", {"k": 0})
    assert value == {"k": 0}
    assert err is not None
    assert err.startswith("[unavailable: x:")
    # Success path leaves the sentinel as ``None``.
    value2, err2 = diag._safely_value(lambda: {"a": 1}, "x", {})
    assert value2 == {"a": 1}
    assert err2 is None


# ---------------------------------------------------------------------------
# Section A2 – OSC multicast group status
# ---------------------------------------------------------------------------


def test_collect_osc_multicast_no_provider() -> None:
    rows = diag.collect_osc_multicast(diag.DiagnosticsProviders())
    assert any("not applicable: OSC service not wired" in r for r in rows)


def test_collect_osc_multicast_provider_raises() -> None:
    def boom() -> dict[str, object]:
        raise RuntimeError("nope")

    rows = diag.collect_osc_multicast(diag.DiagnosticsProviders(osc_multicast_status=boom))
    assert any("[unavailable: osc_multicast_status:" in r for r in rows)


def test_collect_osc_multicast_joined_group_with_allowlist() -> None:
    p = diag.DiagnosticsProviders(
        osc_multicast_status=lambda: {
            "port": 8765,
            "multicast_group": "239.20.20.20",
            "multicast_joined": True,
            "allowed_sender_ips": ["192.168.1.5", "192.168.1.6"],
        }
    )
    joined = "\n".join(diag.collect_osc_multicast(p))
    assert "Listener port:              8765" in joined
    assert "239.20.20.20 (joined)" in joined
    assert "192.168.1.5, 192.168.1.6" in joined


def test_collect_osc_multicast_join_failed_open_allowlist() -> None:
    p = diag.DiagnosticsProviders(
        osc_multicast_status=lambda: {
            "port": 8765,
            "multicast_group": "239.1.2.3",
            "multicast_joined": False,
            "allowed_sender_ips": [],
        }
    )
    joined = "\n".join(diag.collect_osc_multicast(p))
    assert "239.1.2.3 (JOIN FAILED)" in joined
    assert "[open – any LAN device]" in joined


def test_collect_osc_multicast_no_group_unbound_port() -> None:
    rows = diag.collect_osc_multicast(diag.DiagnosticsProviders(osc_multicast_status=lambda: {}))
    joined = "\n".join(rows)
    assert "[not bound]" in joined
    assert "[none – unicast/broadcast only]" in joined


# ---------------------------------------------------------------------------
# Section B – discovery
# ---------------------------------------------------------------------------


def test_collect_discovery_renders_provider_dicts() -> None:
    p = diag.DiagnosticsProviders(
        beacon_sender_health=lambda: {"alive": True, "consecutive_errors": 0, "last_send_age_s": 1.4},
        beacon_receiver_health=lambda: {"alive": True, "packets_received": 12, "last_recv_age_s": 0.4},
        known_peers=lambda: [
            {"name": "pi-stage", "ip": "192.168.80.20", "web_port": 80, "last_seen_age_s": 0.4},
        ],
        iface_ip=lambda: "192.168.80.110",
    )
    rows = diag.collect_discovery(p)
    joined = "\n".join(rows)
    assert "alive" in joined
    assert "pi-stage" in joined
    assert "Multicast iface_ip" in joined
    # The host's own NIC enumeration runs unconditionally.
    assert "Local IPv4 addresses" in joined


def test_collect_discovery_renders_unavailable_when_providers_raise() -> None:
    """Failing providers are wrapped and rendered as unavailable rows."""

    def boom() -> Any:
        raise RuntimeError("boom")

    p = diag.DiagnosticsProviders(
        beacon_sender_health=boom,
        beacon_receiver_health=boom,
        known_peers=boom,
    )
    rows = diag.collect_discovery(p)
    joined = "\n".join(rows)
    assert "[unavailable: beacon_sender_health:" in joined
    assert "[unavailable: beacon_receiver_health:" in joined
    assert "[unavailable: known_peers:" in joined
    assert "Local IPv4 addresses" in joined  # rest of bundle still renders


def test_collect_discovery_no_providers_still_lists_local_ips() -> None:
    rows = diag.collect_discovery(diag.DiagnosticsProviders())
    joined = "\n".join(rows)
    assert "[not applicable: discovery not wired]" in joined
    assert "Local IPv4 addresses" in joined


# ---------------------------------------------------------------------------
# Section C – config + redaction
# ---------------------------------------------------------------------------


def test_redact_web_pin_replaces_set_value() -> None:
    out = diag.redact_web_pin('web_pin = "secret"\nsystem = "rig"')
    assert 'web_pin = "***"' in out
    assert "secret" not in out
    assert 'system = "rig"' in out  # other lines untouched


def test_redact_web_pin_marks_empty_explicitly() -> None:
    assert 'web_pin = "(empty)"' in diag.redact_web_pin('web_pin = ""')
    assert 'web_pin = "(empty)"' in diag.redact_web_pin("web_pin = ''")


def test_redact_web_pin_does_not_match_sibling_keys() -> None:
    """Regex anchors to exact key followed by = to avoid sibling key matches."""
    text = 'web_pin = "real-secret"\nweb_pin_hint = "do not redact me"\nweb_pinger = "also untouched"'
    out = diag.redact_web_pin(text)
    assert 'web_pin = "***"' in out
    assert 'web_pin_hint = "do not redact me"' in out
    assert 'web_pinger = "also untouched"' in out


def test_redact_web_pin_handles_indented_line() -> None:
    text = '  web_pin = "leaked"'
    out = diag.redact_web_pin(text)
    assert out == '  web_pin = "***"'


def test_collect_config_streams_provider_text() -> None:
    p = diag.DiagnosticsProviders(
        config_redacted_toml=lambda: 'web_pin = "***"\nsystem = "rig"',
        config_diff_from_defaults=lambda: ["web_port: 80 (default 8080)"],
    )
    rows = diag.collect_config(p)
    joined = "\n".join(rows)
    assert "begin effective config" in joined
    assert 'web_pin = "***"' in joined
    assert "Diff vs defaults:" in joined
    assert "web_port: 80" in joined


def test_collect_config_renders_unavailable_when_diff_provider_raises() -> None:

    def boom() -> Any:
        raise RuntimeError("nope")

    p = diag.DiagnosticsProviders(
        config_redacted_toml=lambda: 'system = "rig"',
        config_diff_from_defaults=boom,
    )
    rows = diag.collect_config(p)
    joined = "\n".join(rows)
    assert "[unavailable: config_diff_from_defaults:" in joined
    # Effective-config block is unaffected.
    assert "begin effective config" in joined


# ---------------------------------------------------------------------------
# Section D – log tail + signature redaction
# ---------------------------------------------------------------------------


def test_redact_signatures_strips_hex_payload() -> None:
    line = "INFO peer_auth: outgoing X-Auth-Signature: 0123456789abcdef other text"
    out = diag.redact_signatures(line)
    assert "0123456789abcdef" not in out
    assert "X-Auth-Signature: ***" in out
    assert "other text" in out


def test_redact_signatures_is_case_insensitive_and_multiline() -> None:
    text = "row1 X-AUTH-SIGNATURE: ff\nrow2 untouched\nrow3 x-auth-signature: 11"
    out = diag.redact_signatures(text)
    assert "ff" not in out
    assert " 11" not in out
    assert "untouched" in out


@pytest.fixture(autouse=True)
def _clear_probe_log_source_cache():
    """Clear probe cache between tests to prevent monkeypatch leakage."""
    diag._probe_log_source_cache.clear()
    yield
    diag._probe_log_source_cache.clear()


def test_probe_log_source_returns_journalctl_when_reachable(monkeypatch) -> None:
    """``probe_log_source`` is the variant ``_build_diagnostics_cards``
    uses on every 5 s poll – the journalctl probe is TTL-cached
    so we don't spawn a subprocess on every poll.    on."""
    monkeypatch.setattr(
        diag.shutil,
        "which",
        lambda name: "/usr/bin/journalctl" if name == "journalctl" else None,
    )
    monkeypatch.setattr(diag, "_run", lambda *a, **kw: (0, "ok"))
    assert diag.probe_log_source("openfollow") == "journalctl"


def test_probe_log_source_no_service_name_short_circuits() -> None:
    """An empty / ``None`` service name skips the journalctl probe
    entirely – same short-circuit ``collect_log_tail`` does. Pass
    a non-None ring so the "no log source at all" branch doesn't
    fire."""
    ring = RingBufferLogHandler(capacity=4)
    assert "no journald service name" in diag.probe_log_source(None, ring=ring)
    assert "no journald service name" in diag.probe_log_source("", ring=ring)


def test_probe_log_source_falls_back_when_journalctl_missing(monkeypatch) -> None:
    """Service name set but journalctl absent → fallback label.
    Ring is present (typical dev / macOS config)."""
    ring = RingBufferLogHandler(capacity=4)
    monkeypatch.setattr(diag.shutil, "which", lambda _name: None)
    assert "journalctl unavailable" in diag.probe_log_source("openfollow", ring=ring)


def test_probe_log_source_falls_back_when_journalctl_unusable(monkeypatch) -> None:
    ring = RingBufferLogHandler(capacity=4)
    monkeypatch.setattr(
        diag.shutil,
        "which",
        lambda name: "/usr/bin/journalctl" if name == "journalctl" else None,
    )
    monkeypatch.setattr(
        diag,
        "_run",
        lambda *a, **kw: (1, "Failed to add match: Operation not permitted"),
    )
    assert "journalctl unavailable" in diag.probe_log_source("openfollow", ring=ring)


def test_probe_log_source_caches_journalctl_probe(monkeypatch) -> None:
    """Journalctl probe is TTL-cached to avoid spawning subprocess on every HTMX poll."""
    monkeypatch.setattr(
        diag.shutil,
        "which",
        lambda name: "/usr/bin/journalctl" if name == "journalctl" else None,
    )
    calls: list[None] = []

    def counting_run(*a, **kw):  # noqa: ARG001
        calls.append(None)
        return (0, "ok")

    monkeypatch.setattr(diag, "_run", counting_run)
    diag.probe_log_source("openfollow")
    diag.probe_log_source("openfollow")
    diag.probe_log_source("openfollow")
    assert len(calls) == 1


def test_probe_log_source_no_log_at_all_when_ring_missing(monkeypatch) -> None:
    """Surface missing log sources instead of promising unavailable fallback."""
    monkeypatch.setattr(diag.shutil, "which", lambda _name: None)
    # No service name, no ring.
    assert "no log source available" in diag.probe_log_source(None, ring=None)
    # Service name set, journalctl missing, no ring.
    assert "no log source available" in diag.probe_log_source("openfollow", ring=None)
    # Service name set, journalctl on PATH and reachable, no ring →
    # still "journalctl" because the primary path is fine. Clear
    # the TTL cache so the previous "unreachable" result doesn't
    # short-circuit this re-probe.
    diag._probe_log_source_cache.clear()
    monkeypatch.setattr(
        diag.shutil,
        "which",
        lambda name: "/usr/bin/journalctl" if name == "journalctl" else None,
    )
    monkeypatch.setattr(diag, "_run", lambda *a, **kw: (0, "ok"))
    assert diag.probe_log_source("openfollow", ring=None) == "journalctl"


def test_collect_log_tail_uses_journalctl_when_available(monkeypatch) -> None:
    def fake_run(cmd, *, timeout_s=5.0):  # noqa: ARG001
        assert cmd[:2] == ["journalctl", "-u"]
        return 0, "line one\nline two"

    monkeypatch.setattr(diag, "_run", fake_run)
    src, lines = diag.collect_log_tail(_seed_ring("ringline"), update_service_name="openfollow")
    assert src == "journalctl"
    assert lines == ["line one", "line two"]


def test_collect_log_tail_falls_back_to_ring_on_journalctl_failure(monkeypatch) -> None:
    monkeypatch.setattr(diag, "_run", lambda *a, **kw: (1, "[unavailable: journalctl not found]"))
    src, lines = diag.collect_log_tail(_seed_ring("a", "b"), update_service_name="openfollow")
    assert "in-memory ring buffer" in src
    assert lines == ["a", "b"]


def test_collect_log_tail_no_service_name_skips_journalctl_attempt() -> None:
    src, lines = diag.collect_log_tail(_seed_ring("only-ring"), update_service_name=None)
    assert "no journald service name" in src
    assert lines == ["only-ring"]


def test_collect_log_tail_handles_no_ring() -> None:
    """Unavailable log source renders consistently across diagnostics."""
    src, lines = diag.collect_log_tail(None, update_service_name=None)
    assert lines == ["[unavailable: ring buffer not initialised]"]
    assert "no log source available" in src


def test_collect_recent_failures_renders_unavailable_when_providers_raise() -> None:

    def boom() -> Any:
        raise RuntimeError("nope")

    p = diag.DiagnosticsProviders(
        worker_thread_tracebacks=boom,
        detection_install_state=boom,
    )
    rows = diag.collect_recent_failures(p, lambda: ("test", []))
    joined = "\n".join(rows)
    assert "[unavailable: worker_thread_tracebacks:" in joined
    assert "[unavailable: detection_install_state:" in joined


def test_collect_recent_failures_renders_empty_traceback_dict() -> None:
    """Empty ``worker_thread_tracebacks`` dict (no recorded events)
    renders the documented sentinel rather than a blank section."""
    p = diag.DiagnosticsProviders(worker_thread_tracebacks=lambda: {})
    rows = diag.collect_recent_failures(p, lambda: ("test", []))
    assert any("[none recorded since process start]" in r for r in rows)


def test_collect_recent_failures_handles_log_collector_exception() -> None:
    """Buggy log_collector surfaces as sentinel row rather than aborting."""

    def boom() -> tuple[str, list[str]]:
        raise RuntimeError("collector exploded")

    rows = diag.collect_recent_failures(diag.DiagnosticsProviders(), boom)
    joined = "\n".join(rows)
    assert "[unavailable: log_collector:" in joined
    # The framing rows still land so the bundle reader's parser
    # (or a human eye) doesn't lose its place.
    assert "begin log tail" in joined
    assert "end log tail" in joined


def test_collect_recent_failures_redacts_signatures_in_log_tail() -> None:
    p = diag.DiagnosticsProviders(
        worker_thread_tracebacks=lambda: {"BeaconSender": "Traceback (most recent...)"},
        request_semaphore_rejections=lambda: 3,
        detection_install_state=lambda: {"phase": "idle"},
    )

    def fake_log_collector():
        return "test", ["INFO X-Auth-Signature: deadbeef ok"]

    rows = diag.collect_recent_failures(p, fake_log_collector)
    joined = "\n".join(rows)
    assert "deadbeef" not in joined
    assert "X-Auth-Signature: ***" in joined
    assert "Last worker-thread tracebacks" in joined
    assert "BeaconSender" in joined
    assert "503s): 3" in joined
    assert "phase             idle" in joined


def test_collect_recent_failures_redacts_signatures_in_worker_traceback() -> None:
    """Worker-thread traceback lines go through ``redact_signatures`` too,
    so a signature captured in a frame can't leak past the always-on
    stripping applied to every other log surface."""
    p = diag.DiagnosticsProviders(
        worker_thread_tracebacks=lambda: {
            "BeaconSender": "File a.py, line 1\n  X-Auth-Signature: deadbeef\n  raise RuntimeError",
        },
    )
    rows = diag.collect_recent_failures(p, lambda: ("test", []))
    joined = "\n".join(rows)
    assert "deadbeef" not in joined
    assert "X-Auth-Signature: ***" in joined


# -- Failure-aware diagnostics Section D with 2000-line tail -------------------------


def test_is_failure_line_matches_warning_and_worse() -> None:
    assert diag._is_failure_line("ts [WARNING] x: y")
    assert diag._is_failure_line("ts [ERROR] x: y")
    assert diag._is_failure_line("ts [CRITICAL] x: y")
    assert not diag._is_failure_line("ts [INFO] x: y")
    assert not diag._is_failure_line("ts [DEBUG] x: y")
    assert not diag._is_failure_line("a plain line with no level token")


def test_is_failure_line_excludes_benign_wlroots_egl_probe() -> None:
    """The wlroots EGL device-enumeration probe carries a ``[ERROR]`` token but
    is benign noise on every clean boot – it must not pollute the extract."""
    egl = (
        "openfollow[1132]: 00:00:00.115 [ERROR] [EGL] command: "
        "eglQueryDeviceStringEXT, error: EGL_BAD_PARAMETER (0x300c), "
        'message: "eglQueryDeviceStringEXT"'
    )
    assert not diag._is_failure_line(egl)


def test_is_failure_line_keeps_other_egl_errors() -> None:
    """The benign denylist is tight: a different EGL call / error still surfaces."""
    # Different EGL command.
    assert diag._is_failure_line("0.1 [ERROR] [EGL] command: eglCreateContext, error: EGL_BAD_ALLOC")
    # Same command, different (non-benign) error code.
    assert diag._is_failure_line("0.1 [ERROR] [EGL] command: eglQueryDeviceStringEXT, error: EGL_NOT_INITIALIZED")


def test_collect_failure_extract_journalctl_filters_to_warning_plus(monkeypatch) -> None:
    """Journald path passes server-side --grep + 24h window. Python
    re-filter keeps only WARNING/ERROR/CRITICAL lines."""
    captured: dict[str, Any] = {}

    def fake_run(cmd, *, timeout_s=5.0):  # noqa: ARG001
        captured["cmd"] = cmd
        return 0, "\n".join(
            [
                "2026 [INFO] x: noise",
                "2026 [WARNING] x: a warning",
                "2026 [ERROR] x: an error",
                "2026 [INFO] y: more noise",
                "2026 [CRITICAL] z: boom",
            ]
        )

    monkeypatch.setattr(diag, "_run", fake_run)
    src, lines = diag.collect_failure_extract(_seed_ring("r"), update_service_name="openfollow")
    assert src == "journalctl"
    assert lines == [
        "2026 [WARNING] x: a warning",
        "2026 [ERROR] x: an error",
        "2026 [CRITICAL] z: boom",
    ]
    # Server-side grep + longer window are requested.
    assert "--grep" in captured["cmd"]
    assert "--since" in captured["cmd"]


def test_collect_failure_extract_ring_fallback_filters_levels() -> None:
    ring = _seed_ring(
        "2026 [INFO] a: noise",
        "2026 [WARNING] b: warn",
        "2026 [INFO] c: noise",
        "2026 [ERROR] d: err",
    )
    src, lines = diag.collect_failure_extract(ring, update_service_name=None)
    assert "in-memory ring buffer" in src
    assert lines == ["2026 [WARNING] b: warn", "2026 [ERROR] d: err"]


def test_collect_failure_extract_drops_benign_egl_probe() -> None:
    """End-to-end: the wlroots EGL probe line is dropped from the extract even
    though it carries a ``[ERROR]`` token, while real failures are kept."""
    ring = _seed_ring(
        "00:00:00.115 [ERROR] [EGL] command: eglQueryDeviceStringEXT, error: EGL_BAD_PARAMETER (0x300c)",
        "2026 [ERROR] svc: a real failure",
    )
    src, lines = diag.collect_failure_extract(ring, update_service_name=None)
    assert lines == ["2026 [ERROR] svc: a real failure"]


def test_collect_failure_extract_journalctl_empty_window_is_not_a_fallback(monkeypatch) -> None:
    """A clean run with no failures in the window is a valid journalctl
    result (empty), NOT a reason to fall back to the ring."""
    monkeypatch.setattr(diag, "_run", lambda *a, **kw: (0, ""))
    src, lines = diag.collect_failure_extract(_seed_ring("[ERROR] ring-only"), update_service_name="openfollow")
    assert src == "journalctl"
    assert lines == []


def test_collect_failure_extract_no_ring_no_journalctl(monkeypatch) -> None:
    monkeypatch.setattr(diag, "_run", lambda *a, **kw: (1, "[unavailable: journalctl not found]"))
    src, lines = diag.collect_failure_extract(None, update_service_name="openfollow")
    assert "no log source available" in src
    assert lines == []


def test_collect_failure_extract_uses_larger_timeout_and_truncated_label(monkeypatch) -> None:
    """The 24h scan requests the larger explicit timeout, and a timeout
    falls back to the ring with a label that says the window was truncated
    (distinct from the generic 'journalctl unavailable')."""
    captured: dict[str, Any] = {}

    def fake_run(cmd, *, timeout_s=5.0):  # noqa: ARG001
        captured["timeout_s"] = timeout_s
        return -1, f"[unavailable: journalctl timed out after {timeout_s}s]"

    monkeypatch.setattr(diag, "_run", fake_run)
    src, lines = diag.collect_failure_extract(
        _seed_ring("2026 [ERROR] ring: e"),
        update_service_name="openfollow",
    )
    assert captured["timeout_s"] == diag._FAILURE_EXTRACT_TIMEOUT_S
    assert "timed out" in src and "truncated" in src
    assert lines == ["2026 [ERROR] ring: e"]


def test_collect_failure_extract_caps_to_last_n() -> None:
    ring = _seed_ring(*[f"2026 [ERROR] x: e{i}" for i in range(10)])
    src, lines = diag.collect_failure_extract(ring, update_service_name=None, last_n=3)
    assert "in-memory ring buffer" in src
    assert lines == ["2026 [ERROR] x: e7", "2026 [ERROR] x: e8", "2026 [ERROR] x: e9"]


def test_collect_recent_failures_renders_failure_extract_with_redaction() -> None:
    def log_collector():
        return "journalctl", ["[INFO] boot ok"]

    def failure_collector():
        return "journalctl", ["[ERROR] boom X-Auth-Signature: deadbeef end"]

    rows = diag.collect_recent_failures(diag.DiagnosticsProviders(), log_collector, failure_collector)
    joined = "\n".join(rows)
    assert "Failure extract (WARNING+, last 24h, source: journalctl)" in joined
    assert "begin failure extract" in joined
    assert "end failure extract" in joined
    assert "[ERROR] boom" in joined
    # Redaction applies to the extract block too.
    assert "deadbeef" not in joined
    assert "X-Auth-Signature: ***" in joined


def test_collect_recent_failures_extract_window_label_is_source_aware() -> None:
    rows = diag.collect_recent_failures(
        diag.DiagnosticsProviders(),
        lambda: ("journalctl", ["[INFO] ok"]),
        lambda: ("in-memory ring buffer (journalctl unavailable)", ["[ERROR] boom"]),
    )
    joined = "\n".join(rows)
    assert "last 24h" not in joined
    assert "this process (ring buffer)" in joined
    assert "source: in-memory ring buffer (journalctl unavailable)" in joined


def test_collect_recent_failures_failure_extract_empty_window() -> None:
    rows = diag.collect_recent_failures(
        diag.DiagnosticsProviders(),
        lambda: ("journalctl", []),
        lambda: ("journalctl", []),
    )
    assert any("[no WARNING/ERROR/CRITICAL lines in window]" in r for r in rows)


def test_collect_recent_failures_handles_failure_collector_exception() -> None:
    def boom() -> tuple[str, list[str]]:
        raise RuntimeError("extract exploded")

    rows = diag.collect_recent_failures(
        diag.DiagnosticsProviders(),
        lambda: ("journalctl", ["[INFO] ok"]),
        boom,
    )
    joined = "\n".join(rows)
    assert "[unavailable: failure_collector:" in joined
    # The raw-tail framing still lands so the section stays parseable.
    assert "begin log tail" in joined


def test_collect_bundle_requests_2000_line_tail_and_wires_failure_extract(monkeypatch) -> None:
    """Bundle requests 2000 lines (matching ring capacity) and wires
    failure extract into Section D."""
    seen: dict[str, Any] = {}

    def fake_log_tail(ring, *, update_service_name=None, last_n=500):  # noqa: ARG001
        seen["last_n"] = last_n
        return "journalctl", ["[INFO] x"]

    def fake_failure(ring, *, update_service_name=None, since="-24h", last_n=1000):  # noqa: ARG001
        seen["failure_called"] = True
        return "journalctl", ["[ERROR] boom"]

    monkeypatch.setattr(diag, "collect_log_tail", fake_log_tail)
    monkeypatch.setattr(diag, "collect_failure_extract", fake_failure)
    bundle = diag.collect_bundle(
        diag.DiagnosticsProviders(),
        log_ring=_seed_ring("r"),
        update_service_name="openfollow",
    )
    assert seen["last_n"] == 2000
    assert seen.get("failure_called") is True
    joined = "\n".join(bundle.d_failures)
    assert "begin failure extract" in joined
    assert "[ERROR] boom" in joined


# ---------------------------------------------------------------------------
# Section E – environment
# ---------------------------------------------------------------------------


def test_collect_runtime_versions_returns_strings(tmp_path) -> None:
    rows = diag.collect_runtime_versions(repo_root=tmp_path)
    joined = "\n".join(rows)
    assert "Python" in joined
    # ``tmp_path`` is not a git repo; the collector must degrade
    # gracefully rather than raise.
    assert "[unavailable]" in joined or "Branch" in joined
    assert any("bottle" in r for r in rows)


def test_collect_runtime_versions_reports_gtk3_row() -> None:
    rows = diag.collect_runtime_versions()
    gtk_rows = [r for r in rows if "GTK 3" in r]
    assert len(gtk_rows) == 1
    # The value after the label is never empty.
    assert gtk_rows[0].split("GTK 3", 1)[1].strip()


def test_gtk3_version_never_raises() -> None:
    val = diag._gtk3_version()
    assert isinstance(val, str) and val


def test_gtk3_version_falls_back_to_pkgconfig_when_runtime_probe_fails(monkeypatch) -> None:
    """When the runtime ``gi`` probe can't resolve GTK 3 (PyGObject
    absent or typelib missing), fall back to pkg-config metadata – and
    finally to ``[unavailable]`` if that's missing too."""
    import gi

    def _raise(*_a, **_k) -> None:
        raise ValueError("no GTK 3 typelib")

    monkeypatch.setattr(gi, "require_version", _raise)
    # pkg-config present → its version is reported.
    monkeypatch.setattr(diag, "_run", lambda *_a, **_k: (0, "3.24.99"))
    assert diag._gtk3_version() == "3.24.99"
    # pkg-config also absent → graceful sentinel, never a raise.
    monkeypatch.setattr(diag, "_run", lambda *_a, **_k: (1, ""))
    assert diag._gtk3_version() == "[unavailable]"


def test_render_usb_table_generic_token_does_not_steal_name() -> None:
    """A device whose product string is only the generic word
    ``"Controller"`` must not be attributed the *first* gamepad's name
    just because ``controller`` is a substring of it. The distinctively
    named pad matches by its model token; the generic device falls to an
    honest 'no subsystem claim' row instead of borrowing a wrong name."""
    devices = [
        diag.UsbDevice(
            vid="3537",
            pid="1024",
            name="GameSir-G7 SE",
            manufacturer="GameSir",
        ),
        diag.UsbDevice(
            vid="045e",
            pid="0b12",
            name="Controller",
            manufacturer="Microsoft",
        ),
    ]
    out = diag.render_usb_table(
        devices,
        midi_ports=[],
        gamepads=[
            "GameSir-G7 SE Controller for Xbox",
            "Xbox Series X Controller",
        ],
        cameras=[],
    )
    joined = "\n".join(out)
    # The GameSir device matches its own entry via the distinctive
    # "gamesir" token – exactly once.
    assert joined.count("gamepad: GameSir-G7 SE Controller for Xbox") == 1
    # Bare "Controller" must not claim GameSir name without distinctive overlap.
    assert "?  endpoint device, no subsystem claim" in joined


def test_usb_match_score_does_not_double_count_repeated_tokens() -> None:
    once = diag._usb_match_score("gamesir-g7 se", "gamesir mat")
    twice = diag._usb_match_score("gamesir-g7 se | gamesir", "gamesir mat")
    # 'gamesir' (len 7) scores the same whether or not it is duplicated
    # across the name|manufacturer fields.
    assert once == twice == 7


def test_git_failure_detail_renders_first_line_of_stderr() -> None:
    """_git_failure_detail forwards detailed sentinel or first stderr line."""
    assert diag._git_failure_detail("") == "[unavailable]"
    assert diag._git_failure_detail("[unavailable: git not found]") == ("[unavailable: git not found]")
    # Multi-line stderr – only the first line lands so the bundle
    # row stays single-line.
    assert diag._git_failure_detail("fatal: not a git repo\nstray text") == ("fatal: not a git repo")


def test_collect_detection_stack_lists_known_extras() -> None:
    rows = diag.collect_detection_stack()
    joined = "\n".join(rows)
    # We don't assert on which extras are installed (host-dependent),
    # but every distribution name from the catalogue must appear.
    for dist, _mod in diag._DETECTION_DISTRIBUTIONS:
        assert dist in joined


def test_collect_os_renders_platform_basics() -> None:
    rows = diag.collect_os()
    joined = "\n".join(rows)
    assert platform.system() in joined
    assert platform.release() in joined


def test_collect_cpu_returns_lines() -> None:
    rows = diag.collect_cpu()
    joined = "\n".join(rows)
    assert "logical cores" in joined
    assert "frequency" in joined or "[unavailable]" in joined


def test_collect_memory_disk_includes_root() -> None:
    rows = diag.collect_memory_disk(extra_paths=[Path("/")])
    joined = "\n".join(rows)
    assert "virtual mem total" in joined
    assert "disk /" in joined


def test_collect_system_health_renders_all_subsections() -> None:
    rows = diag.collect_system_health()
    joined = "\n".join(rows)
    # Sensor sections render either an "[unavailable]" header line
    # (macOS / no sensors) or one or more ``temp[...]`` / ``fans[...]``
    # data rows when psutil exposes readings (typical on Linux). The
    # test must accept both shapes so the unit suite stays green on
    # every CI runner.
    assert "boot time" in joined
    assert "uptime" in joined
    assert "temperatures" in joined or "temp[" in joined
    assert "fans" in joined
    assert "battery" in joined


def test_collect_network_interfaces_returns_per_nic_lines() -> None:
    rows = diag.collect_network_interfaces()
    # At least the loopback interface exists on every host.
    assert any("lo" in r for r in rows)


# ---------------------------------------------------------------------------
# Section E8 – USB
# ---------------------------------------------------------------------------


def test_hex4_normalises_vid_pid() -> None:
    assert diag._hex4("0x0BDA") == "0bda"
    assert diag._hex4("0bda") == "0bda"
    assert diag._hex4("") == ""


def test_hex4_passes_apple_vendor_id_through() -> None:
    assert diag._hex4("apple_vendor_id") == "apple_vendor_id"


def test_walk_macos_collects_nested_devices() -> None:
    """The system_profiler tree nests endpoints under hubs under
    buses; the walker has to descend recursively."""
    sample = {
        "_name": "USB 3.1 Bus",
        "_items": [
            {
                "_name": "MIDI Mix",
                "vendor_id": "0x09e8",
                "product_id": "0x0031",
                "manufacturer": "AKAI Pro",
                "device_speed": "full_speed",
                "_items": [],
            },
            {
                "_name": "USB Hub",
                "vendor_id": "0x8087",
                "product_id": "0x0b40",
                "device_speed": "super_speed_plus",
                "_items": [
                    {
                        "_name": "Webcam",
                        "vendor_id": "0x046d",
                        "product_id": "0x0892",
                        "manufacturer": "Logitech",
                        "device_speed": "high_speed",
                    },
                ],
            },
        ],
    }
    out: list[diag.UsbDevice] = []
    diag._walk_macos(sample, out)
    names = [d.name for d in out]
    assert "MIDI Mix" in names
    assert "USB Hub" in names
    assert "Webcam" in names
    speeds = [d.speed for d in out]
    # Speed enum is mapped to human strings.
    assert "12 Mb/s" in speeds
    assert "10 Gb/s" in speeds
    assert "480 Mb/s" in speeds
    # Hub detection by name substring.
    hub = next(d for d in out if d.name == "USB Hub")
    assert hub.is_hub is True


def test_collect_usb_macos_handles_missing_binary(monkeypatch) -> None:
    monkeypatch.setattr(diag, "_run", lambda *a, **kw: (-1, "[unavailable: system_profiler not found]"))
    assert diag.collect_usb_devices_macos() == []


def test_collect_usb_macos_handles_garbage_json(monkeypatch) -> None:
    monkeypatch.setattr(diag, "_run", lambda *a, **kw: (0, "<not json>"))
    assert diag.collect_usb_devices_macos() == []


def test_collect_usb_linux_reads_sysfs(tmp_path) -> None:
    bus = tmp_path / "1-1"
    bus.mkdir()
    (bus / "idVendor").write_text("09e8")
    (bus / "idProduct").write_text("0031")
    (bus / "product").write_text("MIDI Mix")
    (bus / "manufacturer").write_text("AKAI Pro")
    (bus / "speed").write_text("12")
    (bus / "bDeviceClass").write_text("00")

    iface = tmp_path / "1-1:1.0"  # interface entry, must be skipped
    iface.mkdir()

    hub = tmp_path / "1-2"
    hub.mkdir()
    (hub / "idVendor").write_text("8087")
    (hub / "idProduct").write_text("0b40")
    (hub / "bDeviceClass").write_text("09")

    devices = diag.collect_usb_devices_linux(sysfs_root=tmp_path)
    names_by_pid = {d.pid: d for d in devices}
    assert names_by_pid["0031"].name == "MIDI Mix"
    assert names_by_pid["0031"].speed == "12 Mb/s"
    assert names_by_pid["0031"].is_hub is False
    assert names_by_pid["0b40"].is_hub is True


def test_collect_usb_linux_handles_unreadable_sysfs(tmp_path, monkeypatch) -> None:
    """Sysfs permission errors return empty list; caller surfaces sentinel."""

    def boom(self):  # noqa: ARG001
        raise PermissionError("sysfs read denied")

    monkeypatch.setattr(diag.Path, "iterdir", boom)
    devices = diag.collect_usb_devices_linux(sysfs_root=tmp_path)
    assert devices == []


def test_collect_usb_linux_handles_missing_sysfs() -> None:
    assert diag.collect_usb_devices_linux(sysfs_root=Path("/nope/does-not-exist")) == []


def test_render_usb_table_visibility_matches_midi() -> None:
    devices = [
        diag.UsbDevice(vid="8087", pid="0b40", name="USB Hub", is_hub=True),
        diag.UsbDevice(
            vid="09e8",
            pid="0031",
            speed="12 Mb/s",
            name="MIDI Mix",
            manufacturer="AKAI Pro",
        ),
        diag.UsbDevice(
            vid="045e",
            pid="028e",
            speed="12 Mb/s",
            name="Xbox 360 Controller",
            manufacturer="Microsoft",
        ),
        diag.UsbDevice(
            vid="2bd9",
            pid="0011",
            speed="480 Mb/s",
            name="Mystery Device",
            manufacturer="Unknown Co",
        ),
    ]
    out = diag.render_usb_table(
        devices,
        midi_ports=["MIDI Mix"],
        gamepads=["Xbox 360 Controller"],
        cameras=[],
    )
    joined = "\n".join(out)
    assert "MIDI: MIDI Mix" in joined
    assert "gamepad: Xbox 360 Controller" in joined
    assert "(hub)" in joined
    # The unclaimed endpoint is the row a maintainer skips to.
    assert "?  endpoint device" in joined
    # Footer counts.
    assert "1 hubs" in joined
    assert "1 MIDI" in joined
    assert "1 gamepad" in joined


def test_render_usb_table_degrades_when_subsystems_missing() -> None:
    devices = [diag.UsbDevice(vid="045e", pid="028e", name="Xbox", is_hub=False)]
    out = diag.render_usb_table(
        devices,
        midi_ports=None,
        gamepads=None,
        cameras=None,
    )
    joined = "\n".join(out)
    # Every visibility cell falls back to "–" because no index
    # exists, and a footer note records why.
    assert "–" in joined
    assert "MIDI subsystem not available" in joined
    assert "gamepad subsystem not available" in joined
    assert "camera subsystem not available" in joined


# ---------------------------------------------------------------------------
# Section F – recent I/O activity
# ---------------------------------------------------------------------------


def test_collect_recent_io_renders_not_wired_when_no_providers() -> None:
    rows = diag.collect_recent_io(diag.DiagnosticsProviders())
    joined = "\n".join(rows)
    assert "[not wired]" in joined  # OSC + MIDI providers both unwired
    assert "no OSC input path" in joined
    assert "no MIDI output path" in joined


def test_collect_recent_io_lists_provider_entries() -> None:
    p = diag.DiagnosticsProviders(
        recent_osc_sends=lambda: [
            {"age_s": 0.12, "status": "sent", "address": "/track/1/x", "args": (1.0, 2.0)},
        ],
        recent_midi_events=lambda: [
            {"age_s": 0.04, "patch_id": 3, "type": "control_change", "channel": 1, "number": 7, "value": 64},
        ],
    )
    rows = diag.collect_recent_io(p)
    joined = "\n".join(rows)
    assert "/track/1/x" in joined
    assert "patch=3" in joined
    assert "ch=1" in joined


# ---------------------------------------------------------------------------
# Bundle assembly + format
# ---------------------------------------------------------------------------


def test_collect_bundle_runs_with_no_providers() -> None:
    bundle = diag.collect_bundle()
    assert bundle.generated_at  # ISO timestamp
    assert bundle.host_label
    # All section lists are populated (each section's collector ran
    # to completion even without any provider wired).
    for _label, attr in diag._BUNDLE_SECTIONS:
        assert getattr(bundle, attr), f"section {attr} unexpectedly empty"


def test_format_bundle_emits_section_headers() -> None:
    bundle = diag.collect_bundle()
    text = diag.format_bundle(bundle)
    for label, _attr in diag._BUNDLE_SECTIONS:
        assert f"=== {label} ===" in text
    # UTF-8 round-trips cleanly.
    text.encode("utf-8")


# ---------------------------------------------------------------------------
# On-disk writer + retention
# ---------------------------------------------------------------------------


def test_write_bundle_to_disk_round_trip(tmp_path) -> None:
    path = diag.write_bundle_to_disk(
        "hello",
        system_name="Rig One",
        root=tmp_path,
        retention=10,
    )
    assert path is not None
    assert path.exists()
    assert path.read_text() == "hello"
    # Filename: openfollow-diagnostics-<sanitised>-<ts>.txt
    assert path.name.startswith("openfollow-diagnostics-Rig_One-")
    assert path.name.endswith(".txt")


def test_write_bundle_to_disk_retention_prunes(tmp_path) -> None:
    base_ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    # Pre-populate the dir with five "old" bundles for one system.
    for i in range(5):
        f = tmp_path / diag.bundle_filename(
            "rig",
            base_ts.replace(hour=i),
        )
        f.write_text(f"old{i}")
    # Write a new one at the latest timestamp; retention=3 should
    # leave the three most recent (2 old + 1 new) and unlink the rest.
    new = diag.write_bundle_to_disk(
        "new",
        system_name="rig",
        root=tmp_path,
        retention=3,
    )
    assert new is not None and new.exists()
    remaining = sorted(p.name for p in tmp_path.iterdir())
    assert len(remaining) == 3
    assert new.name in remaining


def test_write_bundle_to_disk_per_system_retention(tmp_path) -> None:
    """Two systems sharing the same dir don't evict each other.
    The prune is filename-prefix-scoped, not directory-scoped."""
    base_ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(4):
        (tmp_path / diag.bundle_filename("alpha", base_ts.replace(hour=i))).write_text("alpha")
    for i in range(4):
        (tmp_path / diag.bundle_filename("beta", base_ts.replace(hour=i))).write_text("beta")
    diag.write_bundle_to_disk("new-alpha", system_name="alpha", root=tmp_path, retention=2)
    alpha_files = sorted(p.name for p in tmp_path.iterdir() if "alpha" in p.name)
    beta_files = sorted(p.name for p in tmp_path.iterdir() if "beta" in p.name)
    assert len(alpha_files) == 2  # pruned to retention=2
    assert len(beta_files) == 4  # untouched


def test_write_bundle_to_disk_failure_returns_none(monkeypatch, tmp_path) -> None:
    def boom(*_a, **_kw):
        raise OSError("readonly")

    monkeypatch.setattr(Path, "write_text", boom)
    out = diag.write_bundle_to_disk("x", system_name="rig", root=tmp_path)
    assert out is None


def test_sanitise_name_replaces_unsafe_chars() -> None:
    assert diag._sanitise_name("rig 1/2") == "rig_1_2"
    # Empty / all-stripped → fallback.
    assert diag._sanitise_name("///") == "openfollow"


def test_default_disk_root_falls_back_to_home(monkeypatch, tmp_path) -> None:
    # /var/log/openfollow doesn't exist or isn't writable on dev hosts;
    # default_disk_root should pick the per-user fallback.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: Path(str(tmp_path)))
    monkeypatch.setattr(diag.os, "access", lambda *a, **kw: False)
    root = diag.default_disk_root()
    assert root == Path(str(tmp_path)) / ".openfollow" / "diagnostics"


def test_bundle_filename_shape() -> None:
    ts = datetime(2026, 5, 7, 12, 34, 56, tzinfo=timezone.utc)
    name = diag.bundle_filename("Rig One", ts)
    assert name == "openfollow-diagnostics-Rig_One-20260507T123456Z.txt"


# ---------------------------------------------------------------------------
# Branch coverage – fallback / edge paths the happy-path tests above don't
# exercise. Each test below targets one specific code branch.
# ---------------------------------------------------------------------------


def test_collect_discovery_renders_empty_ip_list_when_no_non_loopback(monkeypatch) -> None:
    """The local-IP enumeration fallback path: every interface is
    127.x. The bundle still renders rather than emitting nothing."""
    import psutil

    class _Loop:
        family = diag.socket.AF_INET
        address = "127.0.0.1"

    monkeypatch.setattr(psutil, "net_if_addrs", lambda: {"lo0": [_Loop()]})
    rows = diag.collect_discovery(diag.DiagnosticsProviders())
    joined = "\n".join(rows)
    assert "[unavailable: no non-loopback IPv4 found]" in joined


def test_collect_discovery_recovers_from_psutil_exception(monkeypatch) -> None:
    import psutil

    def boom():
        raise OSError("no nics")

    monkeypatch.setattr(psutil, "net_if_addrs", boom)
    rows = diag.collect_discovery(diag.DiagnosticsProviders())
    joined = "\n".join(rows)
    assert "[unavailable:" in joined


def test_collect_config_handles_provider_exception() -> None:
    def boom() -> str:
        raise RuntimeError("config gone")

    p = diag.DiagnosticsProviders(config_redacted_toml=boom)
    rows = diag.collect_config(p)
    assert any("[unavailable:" in r for r in rows)


def test_collect_recent_failures_empty_tracebacks_dict() -> None:
    p = diag.DiagnosticsProviders(worker_thread_tracebacks=lambda: {})
    rows = diag.collect_recent_failures(p, lambda: ("test", []))
    assert any("none recorded since process start" in r for r in rows)


def test_safe_version_renders_not_installed_for_missing_distribution() -> None:
    # ``not-a-real-distribution-name-xyz`` is reliably absent.
    assert diag._safe_version("not-a-real-distribution-name-xyz") == "[not installed]"


def test_collect_runtime_versions_marks_dirty_working_tree(tmp_path, monkeypatch) -> None:
    # First call returns HEAD sha, second returns branch, third returns
    # non-empty status (= dirty). Sequencing the responses by command.
    seen: list[list[str]] = []

    def fake_run(cmd, *, timeout_s=5.0):  # noqa: ARG001
        seen.append(cmd)
        if "rev-parse" in cmd and "HEAD" in cmd:
            return 0, "abcdef0"
        if "abbrev-ref" in cmd:
            return 0, "feat/x"
        if "status" in cmd:
            return 0, " M openfollow/foo.py"
        if "gst-launch-1.0" in cmd[0]:
            return 0, "gst-launch-1.0 version 1.28.0"
        if "pkg-config" in cmd[0]:
            return 0, "3.24.50"
        return -1, "[unavailable]"

    monkeypatch.setattr(diag, "_run", fake_run)
    rows = diag.collect_runtime_versions(repo_root=tmp_path)
    joined = "\n".join(rows)
    assert "Working tree                 dirty" in joined


def test_collect_runtime_versions_marks_clean_working_tree(tmp_path, monkeypatch) -> None:
    def fake_run(cmd, *, timeout_s=5.0):  # noqa: ARG001
        if "status" in cmd:
            return 0, ""
        return 0, "ok"

    monkeypatch.setattr(diag, "_run", fake_run)
    rows = diag.collect_runtime_versions(repo_root=tmp_path)
    assert any("Working tree                 clean" in r for r in rows)


def test_collect_detection_stack_handles_onnx_provider_failure(monkeypatch) -> None:
    """If onnxruntime imports but ``get_available_providers`` raises
    (older release / broken install), the section reports the failure
    without dragging the rest of the bundle down."""
    if not diag.importlib.util.find_spec("onnxruntime"):
        pytest.skip("onnxruntime not installed in this environment")

    real_import = diag.importlib.import_module

    def fake_import(name):
        mod = real_import(name)
        if name == "onnxruntime":

            class _Stub:
                @staticmethod
                def get_available_providers():
                    raise RuntimeError("ort broken")

            return _Stub
        return mod

    monkeypatch.setattr(diag.importlib, "import_module", fake_import)
    rows = diag.collect_detection_stack()
    joined = "\n".join(rows)
    assert "onnxruntime providers        [unavailable:" in joined


def test_collect_os_renders_linux_branch(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(diag.platform, "system", lambda: "Linux")
    fake_release = tmp_path / "os-release"
    fake_release.write_text(
        '# distro file\nNAME="Test Linux"\nVERSION="1.0"\n',
    )

    real_read = Path.read_text

    def patched_read(self, *a, **kw):
        if str(self) == "/etc/os-release":
            return fake_release.read_text(*a, **kw)
        return real_read(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", patched_read)
    rows = diag.collect_os()
    joined = "\n".join(rows)
    assert 'NAME="Test Linux"' in joined


def test_collect_os_handles_linux_missing_os_release(monkeypatch) -> None:
    monkeypatch.setattr(diag.platform, "system", lambda: "Linux")

    def boom(*a, **kw):
        raise FileNotFoundError(2, "no such file")

    monkeypatch.setattr(Path, "read_text", boom)
    rows = diag.collect_os()
    assert any("/etc/os-release" in r for r in rows)


def test_collect_os_handles_macos_sw_vers_failure(monkeypatch) -> None:
    monkeypatch.setattr(diag.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(diag, "_run", lambda *a, **kw: (1, "[unavailable]"))
    rows = diag.collect_os()
    assert any("sw_vers" in r and "[unavailable]" in r for r in rows)


def test_collect_os_renders_macos_sw_vers_success(monkeypatch) -> None:
    """``sw_vers`` succeeds – the per-line indented-append branch
    runs (covers the for/append loop above the failure fallthrough).
    Linux CI hits the ``elif sysname == "Darwin"`` arm via this
    monkeypatch even though the host itself isn't Darwin."""
    monkeypatch.setattr(diag.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        diag,
        "_run",
        lambda *a, **kw: (0, "ProductName:\tmacOS\nProductVersion:\t15.7.4"),
    )
    rows = diag.collect_os()
    joined = "\n".join(rows)
    assert "ProductName:\tmacOS" in joined
    assert "ProductVersion:\t15.7.4" in joined


def test_collect_usb_devices_macos_parses_json(monkeypatch) -> None:
    """``system_profiler -json`` succeeds with a real-shaped tree –
    the recursive walker collects each leaf into a ``UsbDevice``."""
    fake_json = (
        '{"SPUSBDataType": [{"_name": "USB 3.1 Bus", "_items": ['
        '{"_name": "MIDI Mix", "vendor_id": "0x09e8", "product_id": "0x0031",'
        ' "manufacturer": "AKAI Pro", "device_speed": "full_speed"}]}]}'
    )
    monkeypatch.setattr(diag, "_run", lambda *a, **kw: (0, fake_json))
    devices = diag.collect_usb_devices_macos()
    assert len(devices) == 1
    assert devices[0].name == "MIDI Mix"
    assert devices[0].vid == "09e8"
    assert devices[0].pid == "0031"
    assert devices[0].speed == "12 Mb/s"


def test_collect_usb_dispatches_to_macos_branch(monkeypatch) -> None:
    """The dispatch line ``devices = collect_usb_devices_macos()``
    runs only when ``platform.system() == "Darwin"``. CI hosts may
    be Linux; force Darwin and stub the macOS collector to confirm
    the branch resolves."""
    monkeypatch.setattr(diag.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        diag,
        "collect_usb_devices_macos",
        lambda: [diag.UsbDevice(vid="abcd", pid="1234", name="MIDI Mix")],
    )
    p = diag.DiagnosticsProviders(
        midi_port_names=lambda: ["MIDI Mix"],
        gamepad_names=lambda: [],
        camera_names=lambda: [],
    )
    rows = diag.collect_usb(p)
    joined = "\n".join(rows)
    assert "MIDI: MIDI Mix" in joined


def test_cpu_brand_linux_proc_cpuinfo(monkeypatch) -> None:
    monkeypatch.setattr(diag.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda self, *a, **kw: (
            "processor\t: 0\nmodel name\t: Test CPU @ 1 GHz\n" if str(self) == "/proc/cpuinfo" else ""
        ),
    )
    assert diag._cpu_brand() == "Test CPU @ 1 GHz"


def test_cpu_brand_linux_no_model_name_field(monkeypatch) -> None:
    monkeypatch.setattr(diag.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda self, *a, **kw: "processor\t: 0\n" if str(self) == "/proc/cpuinfo" else "",
    )
    assert diag._cpu_brand() == "[unavailable]"


def test_cpu_brand_linux_oserror(monkeypatch) -> None:
    monkeypatch.setattr(diag.platform, "system", lambda: "Linux")

    def boom(self, *a, **kw):
        raise PermissionError("cpuinfo")

    monkeypatch.setattr(Path, "read_text", boom)
    assert diag._cpu_brand() == "[unavailable]"


def test_cpu_brand_unsupported_platform(monkeypatch) -> None:
    monkeypatch.setattr(diag.platform, "system", lambda: "OS/2")
    assert diag._cpu_brand() == "[unavailable: unsupported platform]"


def test_cpu_brand_macos_sysctl_failure(monkeypatch) -> None:
    monkeypatch.setattr(diag.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(diag, "_run", lambda *a, **kw: (1, "boom"))
    assert diag._cpu_brand() == "[unavailable]"


def test_collect_cpu_handles_no_freq(monkeypatch) -> None:
    import psutil

    monkeypatch.setattr(psutil, "cpu_freq", lambda: None)
    rows = diag.collect_cpu()
    assert any("frequency                    [unavailable]" in r for r in rows)


def test_collect_cpu_handles_freq_raises(monkeypatch) -> None:
    import psutil

    def boom():
        raise NotImplementedError("no cpu_freq on this kernel")

    monkeypatch.setattr(psutil, "cpu_freq", boom)
    rows = diag.collect_cpu()
    assert any("frequency                    [unavailable]" in r for r in rows)


def test_collect_cpu_handles_percpu_raises(monkeypatch) -> None:
    import psutil

    def boom(*a, **kw):
        raise OSError("perf counter")

    monkeypatch.setattr(psutil, "cpu_percent", boom)
    rows = diag.collect_cpu()
    assert any("per-core" in r and "[unavailable" in r for r in rows)


def test_collect_memory_disk_handles_disk_oserror(monkeypatch) -> None:
    import psutil

    real = psutil.disk_usage

    def selective(path):
        if path == "/nope":
            raise OSError(2, "no such fs")
        return real(path)

    monkeypatch.setattr(psutil, "disk_usage", selective)
    rows = diag.collect_memory_disk(extra_paths=[Path("/nope")])
    assert any("/nope" in r and "[unavailable" in r for r in rows)


def test_collect_memory_disk_appends_inode_row(monkeypatch) -> None:
    # Stub the inode helper so this integration test is deterministic and
    # doesn't depend on the host root filesystem reporting inodes – the
    # helper's own behaviour is covered by the dedicated unit tests below.
    monkeypatch.setattr(diag, "_inode_usage_row", lambda p: f"  inodes {p} STUB")
    rows = diag.collect_memory_disk(extra_paths=[Path("/")])
    joined = "\n".join(rows)
    assert "inodes / STUB" in joined


def test_inode_usage_row_formats_from_statvfs(monkeypatch) -> None:
    # Stub statvfs to a small fake struct so the formatting is fully
    # deterministic (and the test runs even where ``os.statvfs`` is absent).
    import os

    class _Stat:
        f_files = 1000
        f_ffree = 400  # 600 allocated
        f_favail = 300  # available to a non-root process

    monkeypatch.setattr(os, "statvfs", lambda _p: _Stat(), raising=False)
    row = diag._inode_usage_row("/data")
    assert row is not None
    # used = f_files - f_ffree = 600; avail = f_favail = 300;
    # pct = (f_files - f_favail) / f_files = 70%
    assert "inodes /data" in row
    assert "used=600" in row
    assert "avail=300" in row
    assert "(70%)" in row


def test_inode_usage_row_none_when_statvfs_missing(monkeypatch) -> None:
    # On a platform without ``os.statvfs`` (Windows) the helper returns
    # ``None`` rather than raising. Patched on the helper in isolation so
    # psutil.disk_usage (which shares os.statvfs) isn't disturbed.
    import os

    monkeypatch.delattr(os, "statvfs", raising=False)
    assert diag._inode_usage_row("/") is None


def test_inode_usage_row_none_when_statvfs_raises(monkeypatch) -> None:
    import os

    def _boom(_p):
        raise OSError("statvfs failed")

    monkeypatch.setattr(os, "statvfs", _boom, raising=False)
    assert diag._inode_usage_row("/") is None


def test_inode_usage_row_none_when_no_inodes(monkeypatch) -> None:
    # Some virtual / network filesystems report ``f_files == 0``; the
    # helper suppresses the row rather than dividing by zero.
    import os

    class _FakeStat:
        f_files = 0
        f_ffree = 0

    monkeypatch.setattr(os, "statvfs", lambda _p: _FakeStat(), raising=False)
    assert diag._inode_usage_row("/") is None


def test_collect_memory_disk_omits_inode_row_when_none(monkeypatch) -> None:
    # When the inode helper yields ``None``, the disk line still renders
    # and no inode line is appended.
    monkeypatch.setattr(diag, "_inode_usage_row", lambda _p: None)
    rows = diag.collect_memory_disk(extra_paths=[Path("/")])
    joined = "\n".join(rows)
    assert "disk /" in joined
    assert "inodes /" not in joined


# ---------------------------------------------------------------------------
# E5b. Storage breakdown
# ---------------------------------------------------------------------------


def test_collect_storage_breakdown_lists_mounts(monkeypatch) -> None:
    # Stub du so the mount-table + header rendering is exercised without
    # sizing the host's caches (slow / non-deterministic). The real du path
    # is covered by ``test_du_kib_sizes_real_directory``.
    monkeypatch.setattr(diag, "_du_kib", lambda *a, **kw: 4096)
    rows = diag.collect_storage_breakdown()
    joined = "\n".join(rows)
    assert "Mounted filesystems:" in joined
    assert "Largest known directories" in joined
    # The root filesystem is mounted on every host the bundle runs on.
    assert any("/" in r for r in rows)


def test_collect_storage_breakdown_sizes_extra_path(monkeypatch, tmp_path) -> None:
    # An existing candidate path is rendered with a GB figure + its path,
    # sorted into the "largest known directories" view. ``_du_kib`` is
    # stubbed so the test is deterministic and doesn't size host caches.
    monkeypatch.setattr(diag, "_du_kib", lambda *a, **kw: 1_500_000)
    rows = diag.collect_storage_breakdown(extra_paths=[tmp_path])
    joined = "\n".join(rows)
    assert str(tmp_path) in joined
    assert "GB" in joined


def test_collect_storage_breakdown_includes_repo_root(monkeypatch, tmp_path) -> None:
    # ``repo_root`` adds the checkout to the sized candidates.
    monkeypatch.setattr(diag, "_du_kib", lambda *a, **kw: 4096)
    rows = diag.collect_storage_breakdown(repo_root=tmp_path)
    joined = "\n".join(rows)
    assert "OpenFollow checkout" in joined


def test_collect_storage_breakdown_skips_when_du_missing(monkeypatch, tmp_path) -> None:
    # When ``du`` can't run, the present-but-unsizable path degrades to a
    # "[skipped: …]" line rather than crashing or being silently dropped.
    monkeypatch.setattr(diag.shutil, "which", lambda _name: None)
    rows = diag.collect_storage_breakdown(extra_paths=[tmp_path])
    joined = "\n".join(rows)
    assert str(tmp_path) in joined
    assert "[skipped:" in joined


def test_collect_storage_breakdown_caps_total_du_time(monkeypatch, tmp_path) -> None:
    """A shared deadline bounds the *total* du time: once it passes, the
    remaining candidates are listed as budget-exhausted (du not invoked)
    rather than each adding another bounded du to the synchronous bundle."""
    calls = {"n": 0}

    def fake_monotonic() -> float:
        calls["n"] += 1
        # First call sets the deadline; every later check is already past it.
        return 1000.0 if calls["n"] == 1 else 1000.0 + diag._STORAGE_SECTION_BUDGET_S + 1.0

    monkeypatch.setattr(diag.time, "monotonic", fake_monotonic)
    # du must never run once the budget is blown.
    monkeypatch.setattr(diag, "_du_kib", lambda *a, **kw: pytest.fail("du called after budget exhausted"))
    rows = diag.collect_storage_breakdown(extra_paths=[tmp_path])
    joined = "\n".join(rows)
    assert str(tmp_path) in joined
    assert "storage-section time budget" in joined
    assert "exhausted" in joined


def test_collect_storage_breakdown_handles_partitions_error(monkeypatch) -> None:
    import psutil

    def _boom(all=False):
        raise RuntimeError("boom")

    monkeypatch.setattr(psutil, "disk_partitions", _boom)
    monkeypatch.setattr(diag, "_du_kib", lambda *a, **kw: 4096)
    rows = diag.collect_storage_breakdown()
    joined = "\n".join(rows)
    # Never raises – partition failure renders as an unavailable line and
    # the directory-sizing half of the section still runs.
    assert "[unavailable: disk_partitions" in joined
    assert "Largest known directories" in joined


def test_collect_storage_breakdown_partition_usage_error(monkeypatch) -> None:
    # A partition whose usage probe fails renders an "[unavailable]" cell
    # in the mount table rather than taking the section down.
    import collections

    import psutil

    Part = collections.namedtuple("Part", "device mountpoint fstype opts")
    fake = Part("/dev/fake", "/fakemnt", "ext4", "")
    monkeypatch.setattr(psutil, "disk_partitions", lambda all=False: [fake])
    real_usage = psutil.disk_usage

    def _usage(path):
        if path == "/fakemnt":
            raise OSError(2, "gone")
        return real_usage(path)

    monkeypatch.setattr(psutil, "disk_usage", _usage)
    monkeypatch.setattr(diag, "_du_kib", lambda *a, **kw: 4096)
    rows = diag.collect_storage_breakdown()
    joined = "\n".join(rows)
    assert "/fakemnt" in joined
    assert "[unavailable:" in joined


def test_du_kib_sizes_real_directory(tmp_path) -> None:
    # The one test that exercises the real ``du`` subprocess + KiB parse,
    # scoped to a single tiny directory so it stays fast/deterministic –
    # the collect_storage_breakdown tests stub ``_du_kib`` to avoid sizing
    # the host's caches.
    (tmp_path / "blob.bin").write_bytes(b"\0" * 8192)
    kib = diag._du_kib(tmp_path, timeout_s=10.0)
    assert kib is not None
    assert kib > 0


def test_du_kib_returns_none_on_unparseable_output(monkeypatch) -> None:
    # ``du`` succeeding but emitting a non-numeric first token (locale /
    # busybox quirk) yields ``None`` rather than crashing the section.
    monkeypatch.setattr(diag, "_run", lambda *a, **kw: (0, "notanumber\t/x"))
    assert diag._du_kib(Path("/x"), timeout_s=1.0) is None


def test_du_kib_parses_total_despite_nonzero_exit(monkeypatch) -> None:
    # ``du`` exits non-zero because an empty sub-dir was unreadable (apt's
    # ``archives/partial``) but still printed a valid total to stdout – the
    # section must report the size, not ``[skipped]``.
    monkeypatch.setattr(
        diag,
        "_run",
        lambda *a, **kw: (1, "208\t/var/cache/apt/archives"),
    )
    assert diag._du_kib(Path("/var/cache/apt/archives"), timeout_s=1.0) == 208


def test_collect_storage_breakdown_handles_exists_oserror(monkeypatch) -> None:
    # A candidate whose ``.exists()`` raises a *fast* OSError (e.g. permission
    # denied on a parent) is treated as absent and skipped – distinct from a
    # true hang. The section still renders and does NOT mislabel it as a timeout.
    def _boom(self) -> bool:
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "exists", _boom)
    rows = diag.collect_storage_breakdown(extra_paths=[Path("/x")])
    joined = "\n".join(rows)
    assert "Largest known directories" in joined
    assert "stat timed out" not in joined


def test_collect_storage_breakdown_exists_timeout_is_surfaced(monkeypatch) -> None:
    # #614 review: a true exists() hang (None from the bounded probe) IS surfaced
    # as a stat-timeout – distinct from the fast-OSError "absent" case above.
    monkeypatch.setattr(diag, "_bounded_probe", lambda fn, t, default: default)
    rows = diag.collect_storage_breakdown(extra_paths=[Path("/hung")])
    assert "stat timed out (stale mount?)" in "\n".join(rows)


def test_bounded_probe_fails_fast_when_inflight_cap_reached(monkeypatch) -> None:
    # #614 review: once the in-flight cap is hit (probes orphaned on a hung
    # mount), a new probe fails fast without spawning another thread.
    monkeypatch.setattr(diag, "_stat_probe_sem", threading.BoundedSemaphore(1))
    diag._stat_probe_sem.acquire()  # simulate one orphaned probe holding the cap
    ran: list[int] = []
    assert diag._bounded_probe(lambda: ran.append(1) or "x", 1.0, "default") == "default"
    assert ran == []  # fn never ran – no thread spawned


def test_bounded_probe_releases_permit_on_thread_start_failure(monkeypatch) -> None:
    # #614 review: a thread that can't start must release its permit so the cap
    # isn't permanently reduced.
    sem = threading.BoundedSemaphore(1)
    monkeypatch.setattr(diag, "_stat_probe_sem", sem)

    class _BadThread:
        def __init__(self, *a, **kw) -> None:
            pass

        def start(self) -> None:
            raise RuntimeError("cannot start thread")

    monkeypatch.setattr(diag.threading, "Thread", _BadThread)
    assert diag._bounded_probe(lambda: "x", 1.0, "default") == "default"
    # Permit was released, not leaked: it's available again.
    assert sem.acquire(blocking=False) is True


def test_bounded_probe_returns_fn_result() -> None:
    assert diag._bounded_probe(lambda: "ok", 1.0, "default") == "ok"


def test_bounded_probe_swallows_exception_returns_default() -> None:
    def _raise() -> str:
        raise RuntimeError("boom")

    assert diag._bounded_probe(_raise, 1.0, "default") == "default"


def test_bounded_probe_returns_timeout_value_on_hang() -> None:
    # #559: a probe that blocks (like os.stat on a stale mount) is abandoned and
    # the timeout value returned – the request thread doesn't wedge.
    ev = threading.Event()
    try:
        assert diag._bounded_probe(ev.wait, 0.05, "TIMED_OUT") == "TIMED_OUT"
    finally:
        ev.set()  # release the abandoned daemon thread


def test_collect_memory_disk_skips_inode_when_disk_probe_times_out(monkeypatch) -> None:
    # #559: a hung disk_usage degrades to the timeout sentinel, and the inode
    # statvfs on the same stale mount is skipped (not re-hung).
    monkeypatch.setattr(diag, "_bounded_probe", lambda fn, t, default: default)
    rows = diag.collect_memory_disk(extra_paths=[Path("/stale")])
    joined = "\n".join(rows)
    assert "stat timed out (stale mount?)" in joined
    assert "inodes" not in joined


def test_collect_storage_breakdown_budget_exhausted_after_exists(monkeypatch) -> None:
    # #559: exists() succeeds but the budget is gone before sizing – the
    # candidate is reported as budget-exhausted, du is not invoked.
    monkeypatch.setattr(diag, "_bounded_probe", lambda fn, t, default: True)  # exists() succeeds
    calls = {"n": 0}

    def fake_monotonic() -> float:
        calls["n"] += 1
        # deadline + first budget check see 1000 (remaining > 0); the post-exists
        # check sees a time past the deadline.
        return 1000.0 if calls["n"] <= 2 else 1000.0 + diag._STORAGE_SECTION_BUDGET_S + 1.0

    monkeypatch.setattr(diag.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(diag, "_du_kib", lambda *a, **kw: pytest.fail("du must not run when budget is gone"))
    rows = diag.collect_storage_breakdown(extra_paths=[Path("/exists")])
    assert "exhausted" in "\n".join(rows)


def test_collect_system_health_with_temperatures(monkeypatch) -> None:
    """When ``sensors_temperatures`` actually returns values (Linux
    Pi case), the rows render per-zone."""
    from collections import namedtuple

    import psutil

    Temp = namedtuple("Temp", ["label", "current", "high", "critical"])
    monkeypatch.setattr(
        psutil,
        "sensors_temperatures",
        lambda: {"cpu_thermal": [Temp(label="cpu", current=42.5, high=80, critical=90)]},
        raising=False,
    )
    rows = diag.collect_system_health()
    joined = "\n".join(rows)
    assert "cpu=42.5°C" in joined


def test_collect_system_health_temperatures_empty(monkeypatch) -> None:
    """``sensors_temperatures()`` returning an empty dict (Linux
    container / VM with no sensor exposure) and the macOS no-attr
    path both funnel through the same ``[unavailable: not exposed
    by this OS]`` row. Force the empty arm explicitly so CI on both
    platforms covers it (without the mock, Linux runners with real
    sensors hit the populated branch and leave the empty arm cold)."""
    import psutil

    monkeypatch.setattr(psutil, "sensors_temperatures", lambda: {}, raising=False)
    rows = diag.collect_system_health()
    assert any("temperatures" in r and "not exposed by this OS" in r for r in rows)


def test_collect_system_health_temperatures_raises(monkeypatch) -> None:
    import psutil

    def boom():
        raise OSError("perm denied")

    monkeypatch.setattr(psutil, "sensors_temperatures", boom, raising=False)
    rows = diag.collect_system_health()
    assert any("temperatures" in r and "[unavailable:" in r for r in rows)


def test_collect_system_health_with_fans(monkeypatch) -> None:
    from collections import namedtuple

    import psutil

    Fan = namedtuple("Fan", ["label", "current"])
    monkeypatch.setattr(
        psutil,
        "sensors_fans",
        lambda: {"acpi": [Fan(label="cpu_fan", current=2400)]},
        raising=False,
    )
    rows = diag.collect_system_health()
    assert any("cpu_fan=2400 rpm" in r for r in rows)


def test_collect_system_health_fans_empty(monkeypatch) -> None:
    """``sensors_fans()`` returning an empty dict (host with no fan
    sensors exposed) funnels through the ``[unavailable: not exposed]``
    row. Force the empty arm explicitly so a host *with* real fans
    (e.g. a Pi) still covers it – without the mock those runners hit the
    populated branch and leave the empty arm cold."""
    import psutil

    monkeypatch.setattr(psutil, "sensors_fans", lambda: {}, raising=False)
    rows = diag.collect_system_health()
    assert any(r.startswith("  fans") and "not exposed" in r for r in rows)


def test_collect_system_health_fans_raises(monkeypatch) -> None:
    import psutil

    def boom():
        raise OSError()

    monkeypatch.setattr(psutil, "sensors_fans", boom, raising=False)
    rows = diag.collect_system_health()
    assert any(r.startswith("  fans") and "[unavailable:" in r for r in rows)


def test_collect_system_health_with_battery(monkeypatch) -> None:
    from collections import namedtuple

    import psutil

    Bat = namedtuple("Bat", ["percent", "secsleft", "power_plugged"])
    monkeypatch.setattr(
        psutil,
        "sensors_battery",
        lambda: Bat(percent=42, secsleft=3600, power_plugged=False),
        raising=False,
    )
    rows = diag.collect_system_health()
    assert any("42% on battery" in r for r in rows)


def test_collect_system_health_battery_raises(monkeypatch) -> None:
    import psutil

    def boom():
        raise OSError()

    monkeypatch.setattr(psutil, "sensors_battery", boom, raising=False)
    rows = diag.collect_system_health()
    assert any(r.startswith("  battery") and "[unavailable:" in r for r in rows)


def test_collect_network_interfaces_handles_stats_exception(monkeypatch) -> None:
    import psutil

    def boom():
        raise OSError("no nic stats")

    monkeypatch.setattr(psutil, "net_if_stats", boom)
    rows = diag.collect_network_interfaces()
    assert any("[unavailable: net_if_stats" in r for r in rows)


def test_collect_usb_unsupported_platform(monkeypatch) -> None:
    monkeypatch.setattr(diag.platform, "system", lambda: "OS/2")
    rows = diag.collect_usb(diag.DiagnosticsProviders())
    assert any("USB enumeration not implemented for OS/2" in r for r in rows)


def test_collect_usb_no_devices_returns_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(diag.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        diag,
        "collect_usb_devices_linux",
        lambda **kw: [],
    )
    rows = diag.collect_usb(diag.DiagnosticsProviders())
    joined = "\n".join(rows)
    assert "no USB devices enumerated" in joined
    # Linux-specific hint mentions sysfs.
    assert "/sys/bus/usb/devices" in joined


def test_collect_usb_no_devices_macos_mentions_system_profiler(monkeypatch) -> None:
    """Same empty-result sentinel on macOS, but the troubleshooting
    hint mentions ``system_profiler`` instead of sysfs."""
    monkeypatch.setattr(diag.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        diag,
        "collect_usb_devices_macos",
        lambda: [],
    )
    rows = diag.collect_usb(diag.DiagnosticsProviders())
    joined = "\n".join(rows)
    assert "no USB devices enumerated" in joined
    assert "system_profiler" in joined


def test_collect_usb_with_subsystem_providers(monkeypatch) -> None:
    monkeypatch.setattr(diag.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        diag,
        "collect_usb_devices_linux",
        lambda **kw: [diag.UsbDevice(vid="abcd", pid="1234", name="MIDI Mix")],
    )
    p = diag.DiagnosticsProviders(
        midi_port_names=lambda: ["MIDI Mix"],
        gamepad_names=lambda: [],
        camera_names=lambda: [],
    )
    rows = diag.collect_usb(p)
    joined = "\n".join(rows)
    assert "MIDI: MIDI Mix" in joined


def test_collect_usb_provider_failures_degrade_visibility(monkeypatch) -> None:
    monkeypatch.setattr(diag.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        diag,
        "collect_usb_devices_linux",
        lambda **kw: [diag.UsbDevice(vid="abcd", pid="1234", name="MIDI Mix")],
    )

    def boom() -> Any:
        raise RuntimeError("backend gone")

    p = diag.DiagnosticsProviders(
        midi_port_names=boom,
        gamepad_names=boom,
        camera_names=boom,
    )
    rows = diag.collect_usb(p)
    joined = "\n".join(rows)
    # Visibility column degrades to ``–`` and the footer notes the
    # missing subsystems (the existing render_usb_table behaviour).
    assert "MIDI subsystem not available" in joined
    assert "gamepad subsystem not available" in joined
    # Provider-error sentinel is preserved alongside the footer so
    # the operator can tell a crashing backend apart from "feature
    # not configured" – the previous version discarded ``err``.
    assert "[unavailable: midi_port_names:" in joined
    assert "[unavailable: gamepad_names:" in joined
    assert "[unavailable: camera_names:" in joined


def test_collect_usb_with_partial_subsystem_providers(monkeypatch) -> None:
    monkeypatch.setattr(diag.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        diag,
        "collect_usb_devices_linux",
        lambda **kw: [diag.UsbDevice(vid="abcd", pid="1234", name="(unknown)")],
    )
    # Each provider's own ``is not None`` branch needs coverage on
    # both True and False sides; iterate so the False side fires
    # for each provider in turn (single set / others unset).
    for kwargs in (
        {"midi_port_names": lambda: []},
        {"gamepad_names": lambda: []},
        {"camera_names": lambda: []},
    ):
        p = diag.DiagnosticsProviders(**kwargs)
        rows = diag.collect_usb(p)
        joined = "\n".join(rows)
        # render_usb_table footer mentions the unwired subsystems –
        # exact list depends on which one is set, so just check the
        # general "visibility column degraded" marker is present.
        assert "visibility column degraded" in joined


def test_render_usb_table_camera_match() -> None:
    devices = [
        diag.UsbDevice(
            vid="046d",
            pid="0892",
            name="Logitech HD Pro Webcam C920",
            manufacturer="Logitech",
        ),
    ]
    out = diag.render_usb_table(
        devices,
        midi_ports=[],
        gamepads=[],
        cameras=["Logitech HD Pro Webcam C920"],
    )
    assert any("camera: Logitech" in r for r in out)


def test_collect_usb_devices_linux_skips_interface_entries(tmp_path) -> None:
    iface = tmp_path / "1-1:1.0"  # interface, must skip
    iface.mkdir()
    (iface / "idVendor").write_text("dead")
    (iface / "idProduct").write_text("beef")
    devices = diag.collect_usb_devices_linux(sysfs_root=tmp_path)
    assert devices == []


def test_collect_usb_devices_linux_skips_devices_with_no_id(tmp_path) -> None:
    bus = tmp_path / "1-1"
    bus.mkdir()
    # No idVendor, no idProduct files
    devices = diag.collect_usb_devices_linux(sysfs_root=tmp_path)
    assert devices == []


def test_collect_usb_devices_linux_handles_non_numeric_speed(tmp_path) -> None:
    bus = tmp_path / "1-1"
    bus.mkdir()
    (bus / "idVendor").write_text("dead")
    (bus / "idProduct").write_text("beef")
    (bus / "speed").write_text("super-speed")  # not a number
    devices = diag.collect_usb_devices_linux(sysfs_root=tmp_path)
    assert devices[0].speed == "super-speed"


def test_collect_usb_devices_linux_speed_missing(tmp_path) -> None:
    bus = tmp_path / "1-1"
    bus.mkdir()
    (bus / "idVendor").write_text("dead")
    (bus / "idProduct").write_text("beef")
    devices = diag.collect_usb_devices_linux(sysfs_root=tmp_path)
    assert devices[0].speed == "?"


def test_read_sysfs_oserror_returns_empty(tmp_path) -> None:
    # The directory exists but the attribute file doesn't.
    assert diag._read_sysfs(tmp_path, "no_such_attr") == ""


def test_collect_recent_io_provider_raises_osc(monkeypatch) -> None:
    def boom():
        raise RuntimeError("ring corrupt")

    p = diag.DiagnosticsProviders(recent_osc_sends=boom)
    rows = diag.collect_recent_io(p)
    joined = "\n".join(rows)
    assert "[unavailable" in joined


def test_collect_recent_io_provider_raises_midi(monkeypatch) -> None:
    def boom():
        raise RuntimeError("ring corrupt")

    p = diag.DiagnosticsProviders(recent_midi_events=boom)
    rows = diag.collect_recent_io(p)
    joined = "\n".join(rows)
    assert "[unavailable" in joined


def test_collect_recent_io_empty_records_branch() -> None:
    p = diag.DiagnosticsProviders(
        recent_osc_sends=lambda: [],
        recent_midi_events=lambda: [],
    )
    rows = diag.collect_recent_io(p)
    joined = "\n".join(rows)
    assert "no events recorded" in joined


def test_collect_log_tail_journalctl_returns_empty(monkeypatch) -> None:
    monkeypatch.setattr(diag, "_run", lambda *a, **kw: (0, ""))
    src, lines = diag.collect_log_tail(
        _seed_ring("ringline"),
        update_service_name="openfollow",
    )
    assert "in-memory ring buffer (journalctl unavailable)" in src
    assert lines == ["ringline"]


def test_default_disk_root_uses_var_log_when_writable(monkeypatch) -> None:
    monkeypatch.setattr(Path, "exists", lambda self: str(self) == "/var/log/openfollow")
    monkeypatch.setattr(diag.os, "access", lambda path, mode: True)
    assert diag.default_disk_root() == Path("/var/log/openfollow")


def test_write_bundle_to_disk_handles_mkdir_failure(monkeypatch, tmp_path) -> None:
    def boom(self, *a, **kw):
        raise PermissionError("readonly")

    monkeypatch.setattr(Path, "mkdir", boom)
    out = diag.write_bundle_to_disk(
        "x",
        system_name="rig",
        root=tmp_path / "nope",
    )
    assert out is None


def test_prune_old_bundles_handles_iterdir_oserror(monkeypatch, tmp_path) -> None:
    def boom(self):
        raise OSError("denied")

    monkeypatch.setattr(Path, "iterdir", boom)
    diag._prune_old_bundles(tmp_path, "rig", 3)  # must not raise


def test_prune_old_bundles_handles_unlink_oserror(monkeypatch, tmp_path) -> None:
    base_ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(3):
        (tmp_path / diag.bundle_filename("rig", base_ts.replace(hour=i))).write_text("x")

    def boom(self, *a, **kw):
        raise PermissionError("readonly")

    monkeypatch.setattr(Path, "unlink", boom)
    # Must not raise even though every unlink fails.
    diag._prune_old_bundles(tmp_path, "rig", 0)
    assert len(list(tmp_path.iterdir())) == 3  # unlinks all swallowed


def test_prune_old_bundles_negative_retention_clamped_to_zero(tmp_path) -> None:
    base_ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(2):
        (tmp_path / diag.bundle_filename("rig", base_ts.replace(hour=i))).write_text("x")
    diag._prune_old_bundles(tmp_path, "rig", -5)
    assert list(tmp_path.iterdir()) == []  # retention < 0 clamped to 0


# --- Branch-coverage filler tests --------------------------------------------


def test_collect_service_minimal_providers_skips_optional_rows() -> None:
    p = diag.DiagnosticsProviders(
        web_port_configured=lambda: 80,
        web_port_display=lambda: 80,
    )
    rows = diag.collect_service(p)
    joined = "\n".join(rows)
    assert "Process uptime" not in joined
    assert "Process PID" not in joined
    assert "Restarts" not in joined


def test_collect_service_no_display_provider_skips_display_row() -> None:
    p = diag.DiagnosticsProviders(web_port_configured=lambda: 80)
    rows = diag.collect_service(p)
    joined = "\n".join(rows)
    assert "Configured web_port:        80" in joined
    assert "Actual display_port" not in joined


def test_collect_discovery_only_sender_provider() -> None:
    p = diag.DiagnosticsProviders(
        beacon_sender_health=lambda: {"alive": True},
    )
    rows = diag.collect_discovery(p)
    joined = "\n".join(rows)
    assert "Beacon sender" in joined
    assert "Beacon receiver" not in joined


def test_collect_discovery_only_receiver_provider() -> None:
    p = diag.DiagnosticsProviders(
        beacon_receiver_health=lambda: {"alive": True},
    )
    rows = diag.collect_discovery(p)
    joined = "\n".join(rows)
    assert "Beacon receiver" in joined
    assert "Beacon sender" not in joined


def test_collect_discovery_no_peer_or_iface_providers() -> None:
    p = diag.DiagnosticsProviders(
        beacon_sender_health=lambda: {"alive": True},
        beacon_receiver_health=lambda: {"alive": True},
    )
    rows = diag.collect_discovery(p)
    joined = "\n".join(rows)
    assert "Known peers" not in joined
    assert "Multicast iface_ip" not in joined


def test_collect_config_no_diff_provider_skips_diff_section() -> None:
    p = diag.DiagnosticsProviders(
        config_redacted_toml=lambda: 'web_pin = "***"',
    )
    rows = diag.collect_config(p)
    joined = "\n".join(rows)
    assert "Diff vs defaults" not in joined


def test_collect_detection_stack_no_onnxruntime(monkeypatch) -> None:
    """When onnxruntime isn't importable the providers list block
    must not run."""
    real_find_spec = diag.importlib.util.find_spec

    def selective(name):
        if name == "onnxruntime":
            return None
        return real_find_spec(name)

    monkeypatch.setattr(diag.importlib.util, "find_spec", selective)
    rows = diag.collect_detection_stack()
    joined = "\n".join(rows)
    assert "onnxruntime providers" not in joined


def test_collect_detection_stack_missing_distribution_reports_not_installed(monkeypatch) -> None:
    """PackageNotFoundError reports [not installed] even if import resolves via sibling."""
    monkeypatch.setattr(
        diag.importlib.metadata,
        "version",
        lambda _name: (_ for _ in ()).throw(diag.importlib.metadata.PackageNotFoundError()),
    )
    monkeypatch.setattr(diag.importlib.util, "find_spec", lambda _n: object())
    rows = diag.collect_detection_stack()
    assert any("[not installed]" in r for r in rows)
    assert not any("installed (version unknown)" in r for r in rows)


def test_collect_os_unsupported_platform(monkeypatch) -> None:
    """Neither Linux nor Darwin – the function still returns the
    base platform.* rows without crashing."""
    monkeypatch.setattr(diag.platform, "system", lambda: "OS/2")
    rows = diag.collect_os()
    joined = "\n".join(rows)
    assert "system                       OS/2" in joined
    # No distro-specific rows added on the unsupported branch.
    assert "/etc/os-release" not in joined
    assert "sw_vers" not in joined


def test_collect_system_health_battery_none(monkeypatch) -> None:
    """Hosts without a battery (Pi, desktop) report
    ``[not applicable: no battery]`` – covers the
    ``bat is None`` arm."""
    import psutil

    monkeypatch.setattr(psutil, "sensors_battery", lambda: None, raising=False)
    rows = diag.collect_system_health()
    assert any("[not applicable: no battery]" in r for r in rows)


def test_render_usb_table_endpoint_with_no_subsystems_at_all() -> None:
    """When *every* subsystem index is missing (None), endpoint
    devices should not be flagged as "?  endpoint device" – that
    indicator is only meaningful when at least one subsystem
    actually claimed something. Covers the ``any_index`` False
    branch."""
    devices = [
        diag.UsbDevice(vid="1234", pid="5678", name="Some Device", is_hub=False),
    ]
    out = diag.render_usb_table(
        devices,
        midi_ports=None,
        gamepads=None,
        cameras=None,
    )
    joined = "\n".join(out)
    assert "?  endpoint device" not in joined
    assert "–" in joined


def test_render_usb_table_handles_partial_subsystems() -> None:
    devices = [
        diag.UsbDevice(
            vid="1234",
            pid="5678",
            name="Mystery Device",
            is_hub=False,
        ),
    ]
    out = diag.render_usb_table(
        devices,
        midi_ports=["MIDI Mix"],
        gamepads=None,
        cameras=None,
    )
    joined = "\n".join(out)
    assert "?  endpoint device" in joined


def test_render_usb_table_camera_loop_skips_non_matching_first(monkeypatch) -> None:
    """The visibility cross-reference iterates ``cameras`` until it
    finds a match. Covers the "non-matching first entry, matching
    second" branch that the simpler camera test never exercises."""
    devices = [
        diag.UsbDevice(
            vid="046d",
            pid="0892",
            name="Logitech HD Pro Webcam C920",
            manufacturer="Logitech",
        ),
    ]
    out = diag.render_usb_table(
        devices,
        midi_ports=[],
        gamepads=[],
        cameras=["NotThisCamera", "Logitech HD Pro Webcam C920"],
    )
    assert any("camera: Logitech HD Pro Webcam C920" in r for r in out)


# ---------------------------------------------------------------------------
# Device permissions
# ---------------------------------------------------------------------------


def test_collect_device_permissions_no_provider_says_not_applicable() -> None:
    rows = diag.collect_device_permissions(diag.DiagnosticsProviders())
    assert rows == ["  [not applicable: privilege broker not wired]"]


def test_collect_device_permissions_empty_states_branch() -> None:
    """Provider returns an empty dict – no capabilities reported.
    Surfaces a clear sentinel so the bundle reader doesn't see a
    blank section."""
    p = diag.DiagnosticsProviders(privilege_states=lambda: {})
    rows = diag.collect_device_permissions(p)
    assert rows == ["  [no capabilities reported]"]


def test_collect_device_permissions_renders_summary_and_sorted_rows() -> None:
    """Happy path: mixed states render the summary line + one row per
    capability sorted alphabetically (deterministic diff in the
    bundle), with the longest name driving column alignment."""
    p = diag.DiagnosticsProviders(
        privilege_states=lambda: {
            "z.last": "needs_password",
            "a.first": "passwordless",
            "m.middle": "unavailable",
        },
    )
    rows = diag.collect_device_permissions(p)
    joined = "\n".join(rows)
    # Summary line counts: 1 passwordless, 1 needs_password, 1 unavailable.
    assert "  summary:           1 passwordless, 1 needs password, 1 unavailable" in joined
    # Sorted order: a.first first, m.middle second, z.last last.
    a_idx = next(i for i, r in enumerate(rows) if "a.first" in r)
    m_idx = next(i for i, r in enumerate(rows) if "m.middle" in r)
    z_idx = next(i for i, r in enumerate(rows) if "z.last" in r)
    assert a_idx < m_idx < z_idx


def test_collect_device_permissions_provider_exception_surfaces_sentinel() -> None:
    """Misbehaving provider folds into the same ``[unavailable: …]``
    sentinel format every other diagnostics collector uses, so the
    bundle stays readable even with a partially-broken runtime."""

    def boom() -> dict[str, str]:
        raise RuntimeError("broker offline")

    p = diag.DiagnosticsProviders(privilege_states=boom)
    rows = diag.collect_device_permissions(p)
    assert len(rows) == 1
    assert "[unavailable:" in rows[0]
    assert "privilege_states" in rows[0]


def test_collect_device_permissions_ignores_unknown_state_strings() -> None:
    p = diag.DiagnosticsProviders(
        privilege_states=lambda: {
            "service.restart": "passwordless",
            "future.cap": "experimental",  # unknown to the counts dict
        },
    )
    rows = diag.collect_device_permissions(p)
    joined = "\n".join(rows)
    # The "experimental" value is reported as-is on its row.
    assert "future.cap" in joined
    assert "experimental" in joined
    # Counts ignored the unknown value (only the passwordless one
    # counted toward the summary line).
    assert "1 passwordless" in joined
    assert "0 needs password" in joined
    assert "0 unavailable" in joined


def test_diagnostics_bundle_includes_g_permissions_section() -> None:
    """End-to-end: ``collect_bundle`` packs the new ``g_permissions``
    field, and ``format_bundle`` renders it with the new section
    heading so support-ticket consumers can search for the
    capability state without knowing the field name."""
    p = diag.DiagnosticsProviders(
        privilege_states=lambda: {"service.restart": "passwordless"},
    )
    bundle = diag.collect_bundle(providers=p)
    assert bundle.g_permissions
    text = diag.format_bundle(bundle)
    assert "G. Device permissions" in text
    assert "service.restart" in text


# --------------------------------------------------------------------------- #
# Section E9 – gamepad controllers (live SDL view)
# --------------------------------------------------------------------------- #


def _pad(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "index": 0,
        "name": "GameSir-G7 SE",
        "guid": "g7se-guid",
        "backend": "sdl2_controller",
        "num_axes": 6,
        "num_buttons": 15,
        "num_hats": 1,
        "is_game_controller": True,
        "matches_calibration": True,
        "calibration_stored": True,
    }
    base.update(overrides)
    return base


def test_collect_gamepad_runtime_not_wired() -> None:
    rows = diag.collect_gamepad_runtime(diag.DiagnosticsProviders())
    assert rows == ["  [not applicable: gamepad provider not wired]"]


def test_collect_gamepad_runtime_no_controllers() -> None:
    p = diag.DiagnosticsProviders(gamepad_runtime=lambda: [])
    assert diag.collect_gamepad_runtime(p) == ["  [no controllers connected]"]


def test_collect_gamepad_runtime_provider_raises() -> None:
    def boom() -> list[dict[str, Any]]:
        raise RuntimeError("kaboom")

    rows = diag.collect_gamepad_runtime(diag.DiagnosticsProviders(gamepad_runtime=boom))
    assert len(rows) == 1
    assert "unavailable: gamepad_runtime" in rows[0]
    assert "kaboom" in rows[0]


def test_collect_gamepad_runtime_xinput_match() -> None:
    p = diag.DiagnosticsProviders(gamepad_runtime=lambda: [_pad()])
    joined = "\n".join(diag.collect_gamepad_runtime(p))
    assert "[0] GameSir-G7 SE" in joined
    assert "guid:    g7se-guid" in joined
    assert "X-input (SDL game controller – preferred)" in joined
    assert "axes=6 buttons=15 hats=1" in joined
    assert "calibration: matches saved mapping" in joined


def test_collect_gamepad_runtime_raw_backend_and_mismatch() -> None:
    pad = _pad(
        backend="joystick",
        is_game_controller=False,
        matches_calibration=False,
    )
    joined = "\n".join(
        diag.collect_gamepad_runtime(
            diag.DiagnosticsProviders(
                gamepad_runtime=lambda: [pad],
            )
        )
    )
    assert "raw-joystick fallback – NOT recognised as X-input" in joined
    assert "*** MISMATCH" in joined
    assert "re-run the" in joined


def test_collect_gamepad_runtime_no_calibration_and_unnamed() -> None:
    pad = _pad(name="", guid="", calibration_stored=False, matches_calibration=True)
    joined = "\n".join(
        diag.collect_gamepad_runtime(
            diag.DiagnosticsProviders(
                gamepad_runtime=lambda: [pad],
            )
        )
    )
    assert "(unnamed)" in joined
    assert "guid:    (none)" in joined
    assert "calibration: none saved (using SDL defaults)" in joined


def test_diagnostics_bundle_includes_e9_gamepad_section() -> None:
    """End-to-end: ``collect_bundle`` packs ``e9_gamepad`` and
    ``format_bundle`` renders the new heading."""
    p = diag.DiagnosticsProviders(gamepad_runtime=lambda: [_pad()])
    bundle = diag.collect_bundle(providers=p)
    assert bundle.e9_gamepad
    text = diag.format_bundle(bundle)
    assert "E9. Gamepad controllers" in text
    assert "GameSir-G7 SE" in text


# ---------------------------------------------------------------------------
# Route-layer wiring: _build_diagnostics_providers
# ---------------------------------------------------------------------------


def _io_server(**overrides: Any) -> Any:
    """Minimal fake ``ConfigWebServer`` exposing only what
    ``_build_diagnostics_providers`` reads at construction time."""
    base = {
        "gamepad_runtime_provider": lambda: [{"name": "8BitDo Pro 2"}, {"name": "nanoKONTROL2"}],
        "recent_osc_sends_provider": lambda: [{"address": "/x"}],
        "osc_listener_status_provider": lambda: {
            "port": 8765,
            "multicast_group": "",
            "multicast_joined": False,
            "allowed_sender_ips": [],
        },
        "recent_midi_events_provider": lambda: [{"type": "note_on"}],
        "midi_port_names_provider": lambda: ["nanoKONTROL2"],
        "camera_names_provider": lambda: ["USB Capture HDMI"],
        "get_privilege_capability_states": lambda: {},
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_build_diagnostics_providers_wires_io_fields() -> None:
    from openfollow.web.routes import _build_diagnostics_providers

    server = _io_server(
        gamepad_runtime_provider=lambda: [
            {"name": "8BitDo Pro 2"},
            {"name": "  "},  # whitespace-only → filtered out
            {"name": "nanoKONTROL2"},
        ]
    )
    providers = _build_diagnostics_providers(server, SimpleNamespace(web_port=8080))
    # The direct hooks pass straight through.
    assert providers.recent_osc_sends is server.recent_osc_sends_provider
    assert providers.osc_multicast_status is server.osc_listener_status_provider
    assert providers.recent_midi_events is server.recent_midi_events_provider
    assert providers.midi_port_names is server.midi_port_names_provider
    assert providers.camera_names is server.camera_names_provider
    # gamepad_names is derived from the gamepad runtime snapshot's names,
    # dropping empty / whitespace-only entries.
    assert providers.gamepad_names is not None
    assert providers.gamepad_names() == ["8BitDo Pro 2", "nanoKONTROL2"]


def test_build_diagnostics_providers_gamepad_names_none_when_unwired() -> None:
    from openfollow.web.routes import _build_diagnostics_providers

    server = _io_server(gamepad_runtime_provider=None)
    providers = _build_diagnostics_providers(server, SimpleNamespace(web_port=8080))
    # No runtime provider → no derived names hook (keeps render_usb_table's
    # "subsystem not available" footer note meaningful).
    assert providers.gamepad_names is None
    assert providers.gamepad_runtime is None
