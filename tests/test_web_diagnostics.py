# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for ``openfollow.web.diagnostics``.

Each section's collector is exercised independently against fakes.
Bundle-assembly test wires everything end-to-end.
"""

from __future__ import annotations

import logging
import platform
import socket
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import openfollow
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


def test_redact_config_secrets_replaces_set_value() -> None:
    out = diag.redact_config_secrets('web_pin = "secret"\nsystem = "rig"')
    assert 'web_pin = "***"' in out
    assert "secret" not in out
    assert 'system = "rig"' in out  # other lines untouched


def test_redact_config_secrets_marks_empty_explicitly() -> None:
    assert 'web_pin = "(empty)"' in diag.redact_config_secrets('web_pin = ""')
    assert 'web_pin = "(empty)"' in diag.redact_config_secrets("web_pin = ''")


def test_redact_config_secrets_does_not_match_sibling_keys() -> None:
    """Matching anchors to the whole key so a similar one is left alone."""
    text = 'web_pin = "real-secret"\nweb_pin_hint = "do not redact me"\nweb_pinger = "also untouched"'
    out = diag.redact_config_secrets(text)
    assert 'web_pin = "***"' in out
    assert 'web_pin_hint = "do not redact me"' in out
    assert 'web_pinger = "also untouched"' in out


def test_redact_config_secrets_handles_indented_line() -> None:
    text = '  web_pin = "leaked"'
    out = diag.redact_config_secrets(text)
    assert out == '  web_pin = "***"'


@pytest.mark.parametrize(
    "key, secret",
    [
        ("rtsp_password", "hunter2"),
        ("rtsp_user", "operator"),
        ("srt_passphrase", "0123456789abcdef"),
    ],
)
def test_redact_config_secrets_hides_stream_credentials(key: str, secret: str) -> None:
    """Whether a login is set is the diagnostic; the value never is.

    It separates "the operator never entered a login" from "the login is
    wrong", which is the whole of the useful content.
    """
    out = diag.redact_config_secrets(f'{key} = "{secret}"')
    assert out == f'{key} = "***"'
    assert secret not in out
    assert diag.redact_config_secrets(f'{key} = ""') == f'{key} = "(empty)"'


def test_redact_config_secrets_strips_credentials_fused_into_a_url() -> None:
    """An operator told to put the password in the URL must not then have it
    printed back in a bundle we ask them to attach to a public issue."""
    out = diag.redact_config_secrets(
        'rtsp_url = "rtsp://operator:hunter2@192.168.0.182:554/profile2/media.smp"\n'
        'srt_host = "srt://10.0.0.5:5000?passphrase=topsecret&latency=125"'
    )
    assert "hunter2" not in out
    assert "topsecret" not in out
    # Everything that is not the credential survives, or the dump stops being
    # useful for diagnosing the connection it describes.
    assert "192.168.0.182:554/profile2/media.smp" in out
    assert "latency=125" in out


def test_redact_config_secrets_leaves_a_credential_free_url_intact() -> None:
    text = 'rtsp_url = "rtsp://192.168.0.182:554/stream1"'
    assert diag.redact_config_secrets(text) == text


@pytest.mark.parametrize(
    "line, key",
    [
        ("rtsp_url = rtsp://admin:hunter2@cam:554/s", "rtsp_url"),  # unquoted
        ("srt_host = [", "srt_host"),  # start of a multi-line array
        ('rtsp_url = "unterminated', "rtsp_url"),
    ],
)
def test_redact_config_secrets_fails_closed_on_an_unparsed_uri_value(line: str, key: str) -> None:
    """A URI value in a shape we cannot parse collapses to ``"***"``.

    The dump is what operators attach to public issue reports, so passing an
    unrecognised form through would print whatever it holds - the one outcome
    this redaction exists to prevent. ``"***"`` also keeps the block valid
    TOML, which a raw pass-through of a broken value would not.
    """
    assert diag.redact_config_secrets(line) == f'{key} = "***"'
    assert "hunter2" not in diag.redact_config_secrets(line)


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


# A real ``rtspsrc`` failure: GStreamer's debug string carries the full
# ``location``, userinfo and all. An auth failure is both the condition that
# puts it in the log and the condition that makes an operator send us a bundle.
_GST_AUTH_ERROR = (
    "2026-09-12 10:04:11 [ERROR] openfollow.runtime.receiver_bus: GStreamer error: "
    "Unauthorized (gstrtspsrc.c(7469): gst_rtspsrc_send (): "
    "/GstPipeline:rtsp-sink/GstRTSPSrc:rtspsrc: Could not open resource for reading "
    "rtsp://operator:hunter2@192.168.0.182:554/profile2/media.smp)"
)


def test_redact_log_line_strips_a_credential_from_a_gstreamer_error() -> None:
    out = diag.redact_log_line(_GST_AUTH_ERROR)
    assert "hunter2" not in out
    assert "operator:" not in out
    # The line has to stay diagnosable: host, path and reason all survive.
    assert "rtsp://192.168.0.182:554/profile2/media.smp" in out
    assert "Unauthorized" in out


def test_redact_log_line_masks_an_srt_passphrase() -> None:
    out = diag.redact_log_line("INFO srt: connecting srt://10.0.0.5:5000?passphrase=topsecret&latency=125")
    assert "topsecret" not in out
    assert "latency=125" in out


def test_redact_log_line_keeps_trailing_punctuation_outside_the_uri() -> None:
    out = diag.redact_log_line("WARNING video: gave up on rtsp://u:p@cam.local:554/s.")
    assert out.endswith("rtsp://cam.local:554/s.")


def test_redact_log_line_still_strips_signatures() -> None:
    out = diag.redact_log_line("INFO X-Auth-Signature: deadbeef ok")
    assert "deadbeef" not in out
    assert "X-Auth-Signature: ***" in out


def test_redact_log_line_leaves_a_credential_free_line_alone() -> None:
    line = "INFO video: RTSP source: rtsp://192.168.0.182:554/stream1 (latency=0)"
    assert diag.redact_log_line(line) == line


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


def test_collect_recent_failures_redacts_credentials_across_every_log_surface() -> None:
    """Log tail, failure extract, and worker tracebacks share one redactor.

    A credential surfacing in only one of the three would still reach a public
    issue report, so the guarantee is pinned across all of them at once.
    """
    p = diag.DiagnosticsProviders(
        worker_thread_tracebacks=lambda: {
            "VideoReceiver": f"File a.py, line 1\n  {_GST_AUTH_ERROR}\n  raise RuntimeError",
        },
    )
    rows = diag.collect_recent_failures(
        p,
        lambda: ("test", [_GST_AUTH_ERROR]),
        lambda: ("test", ["[ERROR] srt://10.0.0.5:5000?passphrase=topsecret failed"]),
    )
    joined = "\n".join(rows)
    assert "hunter2" not in joined
    assert "topsecret" not in joined
    assert joined.count("192.168.0.182:554/profile2/media.smp") == 2


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

    def fake_log_tail(ring, *, update_service_name=None, last_n=500, timeout_s=0.0):  # noqa: ARG001
        seen["last_n"] = last_n
        return "journalctl", ["[INFO] x"]

    def fake_failure(ring, *, update_service_name=None, since="-24h", last_n=1000, timeout_s=0.0):  # noqa: ARG001
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


@pytest.mark.parametrize(
    ("machine", "expected_arch", "expected_label"),
    [
        ("aarch64", "arm64", "arm64 (aarch64)"),
        ("x86_64", "amd64", "amd64 (x86_64)"),
        ("armv7l", "armhf", "armhf (armv7l)"),
        # macOS already reports the Debian label – no redundant parenthetical.
        ("arm64", "arm64", "arm64"),
        # Unmapped machine passes through verbatim, not "riscv64 (riscv64)".
        ("riscv64", "riscv64", "riscv64"),
    ],
)
def test_platform_label_maps_machine(monkeypatch, machine, expected_arch, expected_label) -> None:
    monkeypatch.setattr(platform, "machine", lambda: machine)
    assert diag._platform_arch() == expected_arch
    assert diag._platform_label() == expected_label


def test_collect_bundle_runs_with_no_providers() -> None:
    bundle = diag.collect_bundle()
    assert bundle.generated_at  # ISO timestamp
    assert bundle.host_label
    assert bundle.app_version == openfollow.__version__
    assert bundle.platform_label
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


def test_format_bundle_header_identifies_version_and_platform(monkeypatch) -> None:
    """A bundle must name the release it came from and the architecture it
    ran on, from the package metadata – not from a git checkout, which a
    ``.deb`` / image install doesn't have."""
    monkeypatch.setattr(openfollow, "__version__", "9.9.9")
    monkeypatch.setattr(platform, "machine", lambda: "aarch64")
    text = diag.format_bundle(diag.collect_bundle())
    header = text.splitlines()[:3]
    assert header[0] == "openfollow diagnostics bundle"
    assert header[1] == "version: 9.9.9"
    assert header[2] == "platform: arm64 (aarch64)"


# ---------------------------------------------------------------------------
# Bundle assembly – outer wall-clock budget
# ---------------------------------------------------------------------------


class _FrozenClock:
    """Stand-in for the module's ``time`` whose monotonic only moves when a
    test moves it, so a budget assertion measures the budget and not how long
    the real collectors happened to take on the runner.

    Patched onto ``diag.time``, which rebinds only this module's reference –
    patching ``time.monotonic`` itself would reach every other module in the
    process."""

    def __init__(self) -> None:
        self.now = 1000.0

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def monotonic(self) -> float:
        return self.now

    def __getattr__(self, name: str) -> Any:
        return getattr(time, name)


@pytest.fixture()
def no_host_probes(monkeypatch):
    """Keep the real host probes out of the budget tests.

    They assert deadline arithmetic, so a collector's actual output buys
    nothing - while a bundle that shells out for real adds wall-clock and, on a
    probe that hits its cap, seconds of it. ``_run`` is the module's single
    subprocess boundary, the same seam ``tests/test_web_diagnostics_routes.py``
    stubs for the same reason."""
    monkeypatch.setattr(
        diag,
        "_run",
        lambda cmd, **_kw: (-1, f"[unavailable: {cmd[0] if cmd else '(empty)'} not found]"),
    )


def test_collect_bundle_skips_sections_reached_after_the_budget(monkeypatch, no_host_probes) -> None:
    """One section eating the budget truncates the bundle rather than
    stalling the download: the sections behind it are named as skipped."""
    clock = _FrozenClock()
    monkeypatch.setattr(diag, "time", clock)

    def slow_service(_providers):
        clock.advance(60.0)
        return ["  ran"]

    monkeypatch.setattr(diag, "collect_service", slow_service)
    bundle = diag.collect_bundle(budget_s=20.0)
    assert bundle.a_service == ["  ran"]
    for header, attr in diag._BUNDLE_SECTIONS[1:]:
        assert getattr(bundle, attr) == [f"  [skipped: {header} – bundle time budget (20s) exhausted]"]


def test_collect_bundle_spent_budget_still_renders_a_whole_bundle(no_host_probes) -> None:
    """A budget already gone when assembly starts yields a complete,
    readable bundle – header, every section heading, all skipped."""
    bundle = diag.collect_bundle(budget_s=0.0)
    text = diag.format_bundle(bundle)
    for header, attr in diag._BUNDLE_SECTIONS:
        assert getattr(bundle, attr) == [f"  [skipped: {header} – bundle time budget (0s) exhausted]"]
        assert f"=== {header} ===" in text
    assert f"version: {openfollow.__version__}" in text


def test_collect_bundle_reads_the_module_budget_at_call_time(monkeypatch, no_host_probes) -> None:
    """``budget_s=None`` resolves ``_BUNDLE_BUDGET_S`` in the body. Bound as a
    default argument it would freeze at import and ignore this patch."""
    monkeypatch.setattr(diag, "_BUNDLE_BUDGET_S", 0.0)
    bundle = diag.collect_bundle()
    assert bundle.a_service == ["  [skipped: A. Service / port – bundle time budget (0s) exhausted]"]


def test_collect_bundle_within_budget_skips_nothing(monkeypatch, no_host_probes) -> None:
    """A budget the assembly never spends leaves every section intact. The
    clock is frozen, so this asserts the deadline arithmetic rather than how
    fast the host's collectors ran."""
    monkeypatch.setattr(diag, "time", _FrozenClock())
    bundle = diag.collect_bundle()
    for _header, attr in diag._BUNDLE_SECTIONS:
        assert not any("bundle time budget" in line for line in getattr(bundle, attr))


def test_collect_bundle_clamps_the_log_tail_to_the_remaining_budget(monkeypatch, no_host_probes) -> None:
    """Section D's tail runs before its extract, so leaving it on the 5 s
    default would spend budget the deadline had already promised away."""
    clock = _FrozenClock()
    monkeypatch.setattr(diag, "time", clock)
    seen: dict[str, float] = {}

    def eat_budget(_providers):
        clock.advance(17.0)
        return ["  ran"]

    def fake_tail(ring, *, update_service_name=None, last_n=500, timeout_s=0.0):  # noqa: ARG001
        seen["timeout_s"] = timeout_s
        return "journalctl", []

    monkeypatch.setattr(diag, "collect_service", eat_budget)
    monkeypatch.setattr(diag, "collect_log_tail", fake_tail)
    diag.collect_bundle(log_ring=_seed_ring("r"), update_service_name="openfollow", budget_s=20.0)
    assert seen["timeout_s"] == 3.0


def test_collect_bundle_clamps_the_failure_extract_to_the_remaining_budget(monkeypatch, no_host_probes) -> None:
    """Section D's 24h journalctl scan has a 12 s cap of its own, long
    enough to overshoot a deadline with less than that left."""
    clock = _FrozenClock()
    monkeypatch.setattr(diag, "time", clock)
    seen: dict[str, float] = {}

    def eat_budget(_providers):
        clock.advance(10.0)
        return ["  ran"]

    def fake_failure(ring, *, update_service_name=None, since="-24h", last_n=1000, timeout_s=0.0):  # noqa: ARG001
        seen["timeout_s"] = timeout_s
        return "journalctl", []

    monkeypatch.setattr(diag, "collect_service", eat_budget)
    monkeypatch.setattr(diag, "collect_failure_extract", fake_failure)
    diag.collect_bundle(log_ring=_seed_ring("r"), update_service_name="openfollow", budget_s=20.0)
    assert seen["timeout_s"] == 10.0 < diag._FAILURE_EXTRACT_TIMEOUT_S


def test_collect_bundle_never_hands_a_section_a_negative_cap(monkeypatch, no_host_probes) -> None:
    """Section D's log tail runs before its failure extract, so the deadline
    can lapse between the check that admitted the section and the clamp. A
    negative cap would reach the operator as ``timed out after -2.1s``."""
    clock = _FrozenClock()
    monkeypatch.setattr(diag, "time", clock)
    seen: dict[str, float] = {}

    def slow_tail(ring, *, update_service_name=None, last_n=500, timeout_s=0.0):  # noqa: ARG001
        clock.advance(60.0)
        return "journalctl", []

    def fake_failure(ring, *, update_service_name=None, since="-24h", last_n=1000, timeout_s=-1.0):  # noqa: ARG001
        seen["timeout_s"] = timeout_s
        return "journalctl", []

    monkeypatch.setattr(diag, "collect_log_tail", slow_tail)
    monkeypatch.setattr(diag, "collect_failure_extract", fake_failure)
    diag.collect_bundle(log_ring=_seed_ring("r"), update_service_name="openfollow", budget_s=20.0)
    assert seen["timeout_s"] == 0.0


def test_collect_bundle_clamps_the_runtime_section_to_the_remaining_budget(monkeypatch, no_host_probes) -> None:
    """E1 issues up to five probes, so without a section budget its overshoot
    past the deadline would be five caps rather than one."""
    clock = _FrozenClock()
    monkeypatch.setattr(diag, "time", clock)
    seen: dict[str, float] = {}

    def eat_budget(_providers):
        clock.advance(14.0)
        return ["  ran"]

    def fake_runtime(repo_root=None, *, budget_s=0.0):  # noqa: ARG001
        seen["budget_s"] = budget_s
        return ["  x"]

    monkeypatch.setattr(diag, "collect_service", eat_budget)
    monkeypatch.setattr(diag, "collect_runtime_versions", fake_runtime)
    diag.collect_bundle(budget_s=20.0)
    assert seen["budget_s"] == 6.0 < diag._RUNTIME_SECTION_BUDGET_S


def test_collect_bundle_clamps_the_storage_section_to_the_remaining_budget(monkeypatch, no_host_probes) -> None:
    clock = _FrozenClock()
    monkeypatch.setattr(diag, "time", clock)
    seen: dict[str, float] = {}

    def eat_budget(_providers):
        clock.advance(12.0)
        return ["  ran"]

    def fake_storage(*, repo_root=None, extra_paths=None, budget_s=0.0):  # noqa: ARG001
        seen["budget_s"] = budget_s
        return ["  x"]

    monkeypatch.setattr(diag, "collect_service", eat_budget)
    monkeypatch.setattr(diag, "collect_storage_breakdown", fake_storage)
    diag.collect_bundle(budget_s=20.0)
    assert seen["budget_s"] == 8.0 < diag._STORAGE_SECTION_BUDGET_S


def test_collect_storage_breakdown_reports_the_budget_it_ran_under(monkeypatch) -> None:
    """The skipped-candidate line names the caller's budget, not the
    module default – otherwise a clamped run misreports its own limit."""
    monkeypatch.setattr(diag, "_storage_candidate_paths", lambda *_a: [("repo", Path("/tmp"))])
    rows = diag.collect_storage_breakdown(budget_s=0.0)
    assert any("storage-section time budget (0s) exhausted" in row for row in rows)


def test_collect_storage_breakdown_budgets_the_mount_table_too(monkeypatch) -> None:
    """A stale mount in the partition table is what this budget exists to
    survive, and its stat probe blocks for the full cap – so the mount walk
    has to sit inside the section deadline, not ahead of it."""
    import psutil

    monkeypatch.setattr(
        psutil,
        "disk_partitions",
        lambda all=False: [SimpleNamespace(mountpoint="/stale", fstype="nfs", device="srv:/x")],  # noqa: A002
    )
    called: list[str] = []
    monkeypatch.setattr(diag, "_partition_usage", lambda mp: called.append(mp) or "used")
    rows = diag.collect_storage_breakdown(budget_s=0.0)
    assert any("/stale" in row and "storage-section time budget (0s) exhausted" in row for row in rows)
    assert called == []


def test_collect_runtime_versions_stops_probing_when_its_budget_is_gone(monkeypatch) -> None:
    """E1 fires up to five probes; a spent budget caps each at zero rather
    than letting the section overshoot by five times the default."""
    seen: list[float] = []

    def fake_run(cmd, *, timeout_s=5.0, **_kw):  # noqa: ARG001
        seen.append(timeout_s)
        return -1, "[unavailable: x not found]"

    monkeypatch.setattr(diag, "_run", fake_run)
    diag.collect_runtime_versions(Path("/repo"), budget_s=0.0)
    assert seen and all(cap == 0.0 for cap in seen)


def test_collect_runtime_versions_default_budget_allows_the_full_cap(monkeypatch) -> None:
    seen: list[float] = []

    def fake_run(cmd, *, timeout_s=5.0, **_kw):  # noqa: ARG001
        seen.append(timeout_s)
        return 0, "ok"

    monkeypatch.setattr(diag, "_run", fake_run)
    diag.collect_runtime_versions(Path("/repo"))
    assert seen and all(cap == diag._DEFAULT_SUBPROCESS_TIMEOUT_S for cap in seen)


def test_collect_failure_extract_honours_a_timeout_override(monkeypatch) -> None:
    seen: dict[str, float] = {}

    def fake_run(cmd, *, timeout_s=5.0, **_kw):  # noqa: ARG001
        seen["timeout_s"] = timeout_s
        return 0, ""

    monkeypatch.setattr(diag, "_run", fake_run)
    diag.collect_failure_extract(None, update_service_name="openfollow", timeout_s=1.5)
    assert seen["timeout_s"] == 1.5


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
    # Filename: openfollow-diagnostics-<sanitised>-<ts>-<version>-<arch>.txt
    assert path.name.startswith("openfollow-diagnostics-Rig_One-")
    assert path.name.endswith(f"-{diag._sanitise_name(openfollow.__version__)}-{diag._platform_arch()}.txt")


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


def test_bundle_filename_shape(monkeypatch) -> None:
    monkeypatch.setattr(openfollow, "__version__", "0.4.0")
    monkeypatch.setattr(diag, "_platform_arch", lambda: "arm64")
    ts = datetime(2026, 5, 7, 12, 34, 56, tzinfo=timezone.utc)
    name = diag.bundle_filename("Rig One", ts)
    assert name == "openfollow-diagnostics-Rig_One-20260507T123456Z-0.4.0-arm64.txt"


def test_bundle_filename_sanitises_local_version(monkeypatch) -> None:
    """A PEP 440 local version (``0.0.0+unknown``, what a non-checkout
    install without metadata reports) carries a ``+`` – illegal in the
    ``Content-Disposition`` filename token and awkward on disk."""
    monkeypatch.setattr(openfollow, "__version__", "0.0.0+unknown")
    monkeypatch.setattr(diag, "_platform_arch", lambda: "amd64")
    ts = datetime(2026, 5, 7, 12, 34, 56, tzinfo=timezone.utc)
    assert diag.bundle_filename("rig", ts) == "openfollow-diagnostics-rig-20260507T123456Z-0.0.0_unknown-amd64.txt"


def test_bundle_filename_sorts_chronologically_across_a_version_bump(monkeypatch) -> None:
    """Retention prunes by filename sort, so the timestamp must dominate
    the version. ``0.10.0`` sorts *before* ``0.4.0`` lexicographically – a
    version ahead of the timestamp would make retention evict the newer
    bundle first."""
    monkeypatch.setattr(diag, "_platform_arch", lambda: "arm64")
    ts = datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(openfollow, "__version__", "0.4.0")
    older = diag.bundle_filename("rig", ts)
    monkeypatch.setattr(openfollow, "__version__", "0.10.0")
    newer = diag.bundle_filename("rig", ts.replace(hour=13))
    assert older < newer


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
    """A shared deadline bounds the *total* du time: once one candidate has
    spent it, the remaining ones are listed as budget-exhausted rather than
    each adding another bounded du to the synchronous bundle."""
    import psutil

    clock = _FrozenClock()
    monkeypatch.setattr(diag, "time", clock)
    monkeypatch.setattr(psutil, "disk_partitions", lambda all=False: [])  # noqa: A002
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    monkeypatch.setattr(diag, "_storage_candidate_paths", lambda *_a: [("first", first), ("second", second)])
    sized: list[Path] = []

    def slow_du(path, *, timeout_s):  # noqa: ARG001
        sized.append(path)
        clock.advance(diag._STORAGE_SECTION_BUDGET_S + 1.0)
        return 4096

    monkeypatch.setattr(diag, "_du_kib", slow_du)
    joined = "\n".join(diag.collect_storage_breakdown())
    assert sized == [first]
    assert "second" in joined
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
    """exists() succeeds but a stale mount ate the budget during that very
    probe – the candidate is reported as budget-exhausted and du never runs."""
    import psutil

    clock = _FrozenClock()
    monkeypatch.setattr(diag, "time", clock)
    monkeypatch.setattr(psutil, "disk_partitions", lambda all=False: [])  # noqa: A002

    def exists_but_spends_the_budget(fn, timeout_s, default):  # noqa: ARG001
        clock.advance(diag._STORAGE_SECTION_BUDGET_S + 1.0)
        return True

    monkeypatch.setattr(diag, "_bounded_probe", exists_but_spends_the_budget)
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
        "crash_restarts_provider": lambda: 0,
        "online_sync_status_provider": lambda: {},
        "get_runtime_stats": lambda: {},
        "get_detection_install_status": lambda: {},
    }
    base.update(overrides)
    return SimpleNamespace(**base)


# ``worker_thread_tracebacks`` has no recorder behind it: nothing in the
# runtime captures a thread's last traceback, so the builder leaves it unwired
# and the collector renders its "[not applicable]" sentinel. Every other
# declared provider must be passed.
_PROVIDERS_UNWIRED_BY_DESIGN = {"worker_thread_tracebacks"}


def test_build_diagnostics_providers_passes_every_declared_provider() -> None:
    """A provider the builder forgets is dark code: the collector for it ships,
    renders nothing, and no test notices. Four shipped that way (restart count,
    config diff, detection install job, runtime stats), so the wiring is pinned
    as a set rather than one assertion per field, which only covers the ones
    somebody remembered to assert."""
    import dataclasses

    from openfollow.web.routes import _build_diagnostics_providers

    providers = _build_diagnostics_providers(_io_server(), SimpleNamespace(web_port=8080))
    unwired = {f.name for f in dataclasses.fields(providers) if getattr(providers, f.name) is None}
    assert unwired == _PROVIDERS_UNWIRED_BY_DESIGN


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


# ---------------------------------------------------------------------------
# E7 – interface addressing and the default route
# ---------------------------------------------------------------------------


_ROUTE_HEADER = "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"


def _route_file(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "route"
    path.write_text(_ROUTE_HEADER + body)
    return path


@pytest.mark.parametrize(
    ("netmask", "expected"),
    [("255.255.255.0", 24), ("255.255.0.0", 16), ("255.255.255.255", 32), ("0.0.0.0", 0)],
)
def test_ipv4_prefix_len_converts_dotted_quad(netmask: str, expected: int) -> None:
    assert diag.ipv4_prefix_len(netmask) == expected


@pytest.mark.parametrize("netmask", ["nonsense", "", "255.255.255", "256.0.0.0"])
def test_ipv4_prefix_len_rejects_unparseable_mask(netmask: str) -> None:
    """A mask we can't read must not fabricate a prefix – the caller prints the
    raw netmask instead, which is still true."""
    assert diag.ipv4_prefix_len(netmask) is None


def test_read_default_routes_returns_gateway_and_interface(tmp_path: Path) -> None:
    """Little-endian hex, as the kernel writes it: 0101A8C0 is 192.168.1.1."""
    path = _route_file(
        tmp_path,
        "eth0\t00000000\t0101A8C0\t0003\t0\t0\t100\t00000000\t0\t0\t0\n"
        "eth0\t0001A8C0\t00000000\t0001\t0\t0\t100\t00FFFFFF\t0\t0\t0\n",
    )
    assert diag.read_default_routes(path) == [("eth0", "192.168.1.1")]


def test_read_default_routes_distinguishes_absent_table_from_no_route(tmp_path: Path) -> None:
    """``None`` (can't read the table, e.g. macOS) must not read as ``[]``
    (host genuinely has no way off its subnet) – the bundle says different
    things about a camera on another network in those two cases."""
    assert diag.read_default_routes(tmp_path / "missing") is None
    assert diag.read_default_routes(_route_file(tmp_path, "")) == []


def test_read_default_routes_skips_rows_that_are_not_usable_defaults(tmp_path: Path) -> None:
    path = _route_file(
        tmp_path,
        "short\tline\n"  # fewer than 4 fields
        "eth0\t0001A8C0\t0101A8C0\t0003\t0\t0\t0\t0\t0\t0\t0\n"  # not the default route
        "eth0\t00000000\t0101A8C0\t0001\t0\t0\t0\t0\t0\t0\t0\n"  # RTF_GATEWAY clear
        "eth0\t00000000\tZZZZ\t0003\t0\t0\t0\t0\t0\t0\t0\n"  # unparseable gateway
        "eth0\t00000000\tFFFFFFFFFF\t0003\t0\t0\t0\t0\t0\t0\t0\n",  # wider than four octets
    )
    assert diag.read_default_routes(path) == []


def test_collect_network_interfaces_reports_prefix_and_default_route(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The bundle that prompted this reported ``eth0 192.168.3.5`` and nothing
    else, so a camera on 192.168.1.100 read as a normal address on a healthy
    interface. Prefix plus default route is what makes it off-subnet."""
    import psutil

    monkeypatch.setattr(
        psutil,
        "net_if_stats",
        lambda: {"eth0": SimpleNamespace(isup=True, speed=1000, mtu=1500, duplex=2)},
    )
    monkeypatch.setattr(
        psutil,
        "net_if_addrs",
        lambda: {
            "eth0": [
                SimpleNamespace(family=socket.AF_INET, address="192.168.3.5", netmask="255.255.255.0"),
                SimpleNamespace(family=socket.AF_INET6, address="fe80::1", netmask=None),
            ]
        },
    )
    rows = diag.collect_network_interfaces(_route_file(tmp_path, ""))
    joined = "\n".join(rows)
    assert "ipv4 192.168.3.5/24" in joined
    assert "fe80::1" not in joined
    assert "Default route:  none" in joined


def test_collect_network_interfaces_falls_back_to_raw_netmask(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import psutil

    monkeypatch.setattr(
        psutil, "net_if_stats", lambda: {"eth0": SimpleNamespace(isup=True, speed=0, mtu=1500, duplex=0)}
    )
    monkeypatch.setattr(
        psutil,
        "net_if_addrs",
        lambda: {"eth0": [SimpleNamespace(family=socket.AF_INET, address="10.0.0.2", netmask="not-a-mask")]},
    )
    rows = diag.collect_network_interfaces(_route_file(tmp_path, ""))
    assert "ipv4 10.0.0.2 netmask=not-a-mask" in "\n".join(rows)


def test_collect_network_interfaces_renders_each_default_route(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import psutil

    monkeypatch.setattr(
        psutil, "net_if_stats", lambda: {"eth0": SimpleNamespace(isup=True, speed=0, mtu=1500, duplex=1)}
    )
    monkeypatch.setattr(psutil, "net_if_addrs", lambda: {})
    path = _route_file(tmp_path, "eth0\t00000000\t0101A8C0\t0003\t0\t0\t100\t00000000\t0\t0\t0\n")
    assert "  Default route:  via 192.168.1.1 on eth0" in diag.collect_network_interfaces(path)


def test_collect_network_interfaces_survives_unreadable_addresses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Losing addresses must not lose the link table with it."""
    import psutil

    monkeypatch.setattr(
        psutil, "net_if_stats", lambda: {"eth0": SimpleNamespace(isup=True, speed=0, mtu=1500, duplex=2)}
    )

    def _boom() -> dict[str, Any]:
        raise OSError("no permission")

    monkeypatch.setattr(psutil, "net_if_addrs", _boom)
    joined = "\n".join(diag.collect_network_interfaces(_route_file(tmp_path, "")))
    assert "addresses unavailable" in joined
    assert "eth0" in joined


def test_collect_network_interfaces_reports_unreadable_stats(monkeypatch: pytest.MonkeyPatch) -> None:
    import psutil

    def _boom() -> dict[str, Any]:
        raise OSError("nope")

    monkeypatch.setattr(psutil, "net_if_stats", _boom)
    assert "unavailable" in diag.collect_network_interfaces()[0]


def test_collect_network_interfaces_marks_route_table_unreadable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import psutil

    monkeypatch.setattr(psutil, "net_if_stats", lambda: {})
    monkeypatch.setattr(psutil, "net_if_addrs", lambda: {})
    rows = diag.collect_network_interfaces(tmp_path / "absent")
    assert "[unavailable: kernel route table not readable]" in rows[0]


# ---------------------------------------------------------------------------
# A3 – runtime state
# ---------------------------------------------------------------------------


def _stats(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "video": {
            "source_type": "rtsp",
            "source_label": "rtsp://cam.local:554/s",
            "pipeline_state": "connected",
            "connected": True,
            "reconnect_attempt": 0,
            "error_message": "",
            "resolution": {"width": 1920, "height": 1080},
            "source_fps": 25.0,
        },
        "playback": {
            "frame_count_total": 100,
            "effective_fps": 59.9,
            "recent_effective_fps": 59.8,
            "recent_slow_frame_percent": 0.1,
            "seconds_since_last_frame": 0.01,
            "stale_after_s": 1.0,
            "stalled": False,
        },
        "tracking": {
            "enabled": False,
            "available": False,
            "running": False,
            "model": "yolo26n.onnx",
            "inference_count": 0,
            "inference_hz": 0.0,
            "inference_avg_ms": 0.0,
            "inference_errors": 0,
            "missing_deps": [],
        },
        "controllers": {"connected_count": 1, "mapped_count": 1},
        "system": {"output_resolution": {"width": 1920, "height": 1080}},
    }
    base.update(overrides)
    return base


def test_collect_runtime_state_reports_not_wired() -> None:
    assert "not wired" in diag.collect_runtime_state(diag.DiagnosticsProviders())[0]


def test_collect_runtime_state_survives_a_raising_provider() -> None:
    """The snapshot is a live dict from a running provider. A bundle that
    aborts because telemetry hiccuped is worse than one without telemetry."""

    def _boom() -> dict[str, Any]:
        raise RuntimeError("stats exploded")

    rows = diag.collect_runtime_state(diag.DiagnosticsProviders(runtime_stats=_boom))
    assert "stats exploded" in "\n".join(rows)


def test_collect_runtime_state_renders_an_empty_snapshot() -> None:
    """Every field read through ``.get`` with a default: a snapshot missing a
    key renders a blank field, it does not raise."""
    rows = diag.collect_runtime_state(diag.DiagnosticsProviders(runtime_stats=dict))
    joined = "\n".join(rows)
    assert "Video:" in joined
    assert "- (no canvas)" in joined


def test_collect_runtime_state_reports_a_healthy_feed() -> None:
    rows = diag.collect_runtime_state(diag.DiagnosticsProviders(runtime_stats=_stats))
    joined = "\n".join(rows)
    assert "1920x1080" in joined
    assert "25.0 fps" in joined
    assert "last error            (none)" in joined
    assert "running (last frame 0.01s ago)" in joined
    assert "1 connected, 1 mapped to a marker" in joined


def test_collect_runtime_state_reports_the_reconnect_loop() -> None:
    """The ticket's signature: no frames, an error, a retry count."""
    stats = _stats(
        video={
            "source_type": "rtsp",
            "source_label": "rtsp://cam.local:554/s",
            "pipeline_state": "reconnecting",
            "connected": False,
            "reconnect_attempt": 2,
            "error_message": "No video received (connection timeout)",
            "resolution": {"width": 0, "height": 0},
            "source_fps": 0.0,
        }
    )
    joined = "\n".join(diag.collect_runtime_state(diag.DiagnosticsProviders(runtime_stats=lambda: stats)))
    assert "reconnect attempt 2" in joined
    assert "No video received (connection timeout)" in joined
    assert "- (no source frames yet)" in joined


def test_collect_runtime_state_redacts_a_credential_in_the_source_label() -> None:
    """Plugins are expected to redact their own labels, but this is the file
    operators attach to public issues – a plugin that forgets must not be the
    leak."""
    stats = _stats(video=dict(_stats()["video"], source_label="rtsp://admin:hunter2@cam/s"))
    joined = "\n".join(diag.collect_runtime_state(diag.DiagnosticsProviders(runtime_stats=lambda: stats)))
    assert "hunter2" not in joined
    assert "rtsp://cam/s" in joined


def test_collect_runtime_state_redacts_a_credential_in_the_error_message() -> None:
    stats = _stats(video=dict(_stats()["video"], error_message="failed: rtsp://admin:hunter2@cam/s"))
    joined = "\n".join(diag.collect_runtime_state(diag.DiagnosticsProviders(runtime_stats=lambda: stats)))
    assert "hunter2" not in joined


@pytest.mark.parametrize(
    ("playback", "expected"),
    [
        ({"stalled": True, "seconds_since_last_frame": 12.4, "stale_after_s": 1.0}, "STALLED for 12.4s"),
        ({"stalled": True, "seconds_since_last_frame": None, "stale_after_s": 1.0}, "STALLED"),
        ({"stalled": False, "seconds_since_last_frame": 4.0, "stale_after_s": 1.0}, "STALLED for 4.0s"),
        ({"stalled": False, "seconds_since_last_frame": None, "stale_after_s": 1.0}, "starting"),
    ],
)
def test_collect_runtime_state_describes_the_frame_clock(playback: dict[str, Any], expected: str) -> None:
    """A headless station's outputs keep transmitting a frozen position, and
    nothing else in the bundle distinguishes that from a healthy one. The age
    decides, not the flag: the watchdog shares the loop it watches, so a block
    inside one callback stops both and leaves the flag False."""
    stats = _stats(playback=playback)
    joined = "\n".join(diag.collect_runtime_state(diag.DiagnosticsProviders(runtime_stats=lambda: stats)))
    assert expected in joined


def test_collect_runtime_state_lists_missing_detection_dependencies() -> None:
    stats = _stats(tracking=dict(_stats()["tracking"], missing_deps=["onnxruntime"]))
    joined = "\n".join(diag.collect_runtime_state(diag.DiagnosticsProviders(runtime_stats=lambda: stats)))
    assert "missing deps          onnxruntime" in joined


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "-"),
        ({"width": 0, "height": 0}, "- (no source frames yet)"),
        ({"width": 1280, "height": 720}, "1280x720"),
    ],
)
def test_fmt_resolution(value: Any, expected: str) -> None:
    assert diag._fmt_resolution(value) == expected


# ---------------------------------------------------------------------------
# A4 – uplink status
# ---------------------------------------------------------------------------


def _uplink(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "online": False,
        "cadence_s": 300.0,
        "last_reason": "retry",
        "last_cycle_age_s": 42.0,
        "cycles": 17,
        "time_sync": {
            "enabled": True,
            "target": "ptbtime1.ptb.de",
            "outcome": "unreachable",
            "detail": "TimeoutError: timed out",
            "age_s": 42.0,
        },
        "update_check": {
            "enabled": True,
            "target": "openfollowapp/openfollow",
            "outcome": "unreachable",
            "detail": "URLError: Network is unreachable",
            "age_s": 42.0,
        },
    }
    base.update(overrides)
    return base


def test_collect_uplink_reports_not_wired() -> None:
    assert "not wired" in diag.collect_uplink(diag.DiagnosticsProviders())[0]


def test_collect_uplink_reports_worker_not_running() -> None:
    """``{}`` is what the lazy provider returns before the worker exists."""
    assert "not running" in diag.collect_uplink(diag.DiagnosticsProviders(online_sync_status=dict))[0]


def test_collect_uplink_survives_a_raising_provider() -> None:
    def _boom() -> dict[str, Any]:
        raise RuntimeError("worker exploded")

    assert "worker exploded" in "\n".join(diag.collect_uplink(diag.DiagnosticsProviders(online_sync_status=_boom)))


def test_collect_uplink_reports_no_uplink_with_the_retry_cadence() -> None:
    """``retry`` cadence is the single most diagnostic field: it means no cycle
    has ever reached the network this session, so the reading is fresh."""
    joined = "\n".join(diag.collect_uplink(diag.DiagnosticsProviders(online_sync_status=_uplink)))
    assert "Verdict:            no uplink observed" in joined
    assert "every 300 s (retry - no cycle has reached the network)" in joined
    assert "unreachable 42 s ago - TimeoutError: timed out" in joined


def test_collect_uplink_reports_a_reachable_uplink() -> None:
    status = _uplink(
        online=True,
        cadence_s=86400.0,
        last_reason="periodic",
        time_sync={
            "enabled": True,
            "target": "ptbtime1.ptb.de",
            "outcome": "reached",
            "detail": "clock set (drift was 1339228.0s)",
            "age_s": 3.0,
        },
        update_check={
            "enabled": True,
            "target": "openfollowapp/openfollow",
            "outcome": "reached",
            "detail": "up to date (running 0.4.2)",
            "age_s": 3.0,
        },
    )
    joined = "\n".join(diag.collect_uplink(diag.DiagnosticsProviders(online_sync_status=lambda: status)))
    assert "Verdict:            internet reachable" in joined
    assert "periodic backstop" in joined


def test_collect_uplink_separates_no_observation_from_no_uplink() -> None:
    """With both checks off nothing was ever asked, and reporting that as
    offline would be a guess about a network nobody queried."""
    status = _uplink(
        time_sync={
            "enabled": False,
            "target": "ptbtime1.ptb.de",
            "outcome": "not attempted",
            "detail": "",
            "age_s": None,
        },
        update_check={"enabled": False, "target": "o/r", "outcome": "not attempted", "detail": "", "age_s": None},
        last_cycle_age_s=None,
    )
    joined = "\n".join(diag.collect_uplink(diag.DiagnosticsProviders(online_sync_status=lambda: status)))
    assert "not observed (both checks disabled)" in joined
    assert "never" in joined


def test_collect_uplink_reports_enabled_but_not_yet_attempted() -> None:
    """Enabled and skipped is still no observation: a host that cannot set its
    clock never sends an NTP packet, so it has learned nothing about the LAN."""
    status = _uplink(
        time_sync={
            "enabled": True,
            "target": "ptbtime1.ptb.de",
            "outcome": "not attempted",
            "detail": "clock-set unavailable on this host",
            "age_s": None,
        },
        update_check={
            "enabled": False,
            "target": "o/r",
            "outcome": "not attempted",
            "detail": "auto_update_check is off",
            "age_s": None,
        },
    )
    joined = "\n".join(diag.collect_uplink(diag.DiagnosticsProviders(online_sync_status=lambda: status)))
    assert "not observed yet" in joined


@pytest.mark.parametrize(("age", "expected"), [(None, "never"), ("x", "never"), (41.6, "42 s ago")])
def test_fmt_age(age: Any, expected: str) -> None:
    assert diag._fmt_age(age) == expected


# ---------------------------------------------------------------------------
# Section D – folding repeated log blocks, marking clock discontinuities
# ---------------------------------------------------------------------------


def _journal(stamp: str, text: str) -> str:
    return f"Sep 14 {stamp} host openfollow[2052]: {stamp[-8:]} {text}"


def test_normalise_log_line_ignores_timestamp_and_pid() -> None:
    a = "Sep 14 21:20:09 host openfollow[2052]: 21:20:09 [WARNING] x: no video"
    b = "Sep 14 21:28:24 host openfollow[9999]: 21:28:24 [WARNING] x: no video"
    assert diag.normalise_log_line(a) == diag.normalise_log_line(b)


def test_normalise_log_line_keeps_numbers_that_distinguish_messages() -> None:
    """Only the journal's own noise is stripped. An attempt counter is the
    message, and folding attempt 1 into attempt 2 would erase the loop's shape."""
    a = "Sep 14 21:20:09 host of[1]: Reconnecting pipeline (attempt 1)"
    b = "Sep 14 21:20:19 host of[1]: Reconnecting pipeline (attempt 2)"
    assert diag.normalise_log_line(a) != diag.normalise_log_line(b)


def test_collapse_repeated_blocks_folds_a_reconnect_cycle() -> None:
    """A failing source repeats a *cycle*, not a line: the bundle that prompted
    this was 381 kB of the same three-line loop. Collapsing only adjacent
    identical lines would have left it untouched."""
    lines: list[str] = []
    for minute in range(20, 26):
        lines += [
            _journal(f"21:{minute}:00", "Reconnecting pipeline (attempt 0)..."),
            _journal(f"21:{minute}:01", "RTSP pipeline started"),
            _journal(f"21:{minute}:09", "no video received after 8s"),
        ]
    tail = _journal("21:30:00", "Placeholder pipeline started")
    lines.append(tail)

    folded = diag.collapse_repeated_blocks(lines)

    assert len(folded) == 5
    assert folded[:3] == lines[:3]  # first pass kept verbatim, timestamps intact
    assert "3-line block above repeated 5 more time(s)" in folded[3]
    assert folded[4] == tail


def test_collapse_repeated_blocks_folds_a_single_repeating_line() -> None:
    lines = [_journal(f"21:00:0{i}", "no video received after 8s") for i in range(5)]
    folded = diag.collapse_repeated_blocks(lines)
    assert len(folded) == 2
    assert "the line above repeated 4 more time(s)" in folded[1]


def test_collapse_repeated_blocks_leaves_a_varied_tail_alone() -> None:
    lines = [_journal(f"21:00:0{i}", f"event {i}") for i in range(6)]
    assert diag.collapse_repeated_blocks(lines) == lines


def test_collapse_repeated_blocks_prefers_the_shortest_period() -> None:
    """A 1-line cycle also matches as a 2-line one at half the count; the
    shorter period is the honest description of what repeated."""
    lines = [_journal(f"21:00:0{i}", "same") for i in range(4)]
    folded = diag.collapse_repeated_blocks(lines)
    assert "the line above repeated 3 more time(s)" in folded[1]


def test_collapse_repeated_blocks_respects_the_period_cap() -> None:
    """Past the cap a long stretch is left intact rather than folded against a
    period we never searched for."""
    block = [_journal(f"21:00:0{i}", f"step {i}") for i in range(4)]
    lines = block + block
    assert diag.collapse_repeated_blocks(lines, max_period=3) == lines
    assert len(diag.collapse_repeated_blocks(lines, max_period=4)) == 5


def test_collapse_repeated_blocks_handles_no_lines() -> None:
    assert diag.collapse_repeated_blocks([]) == []


def test_annotate_log_discontinuities_marks_a_forward_clock_jump() -> None:
    """A station with no RTC boots at whatever was last recorded and is
    corrected minutes later, so one boot's journal can jump weeks mid-file."""
    lines = [
        "Aug 29 22:58:13 host of[1]: starting",
        "Aug 29 22:58:26 host of[1]: sudo date -s",
        "Sep 14 21:18:42 host of[1]: System clock set from trusted time source",
    ]
    annotated = diag.annotate_log_discontinuities(lines)
    assert len(annotated) == 4
    assert "timestamps jump ~16 day(s) forward here" in annotated[2]


def test_annotate_log_discontinuities_marks_a_backwards_step() -> None:
    lines = [
        "Sep 14 21:18:42 host of[1]: after sync",
        "Aug 29 22:58:13 host of[1]: an older boot's tail",
    ]
    annotated = diag.annotate_log_discontinuities(lines)
    assert "timestamps step backwards here" in annotated[1]


def test_annotate_log_discontinuities_leaves_a_monotonic_log_alone() -> None:
    lines = [
        "Sep 14 21:18:42 host of[1]: one",
        "Sep 14 21:18:43 host of[1]: two",
        "Sep 14 21:19:00 host of[1]: three",
    ]
    assert diag.annotate_log_discontinuities(lines) == lines


def test_annotate_log_discontinuities_ignores_lines_without_a_stamp() -> None:
    """The ring-buffer fallback has no journal prefix, and journalctl's own
    ``-- Boot ... --`` markers have none either."""
    lines = [
        "Sep 14 21:18:42 host of[1]: one",
        "-- Boot 9b9ec0760a244035813f842a6a988798 --",
        "Sep 14 21:18:43 host of[1]: two",
    ]
    assert diag.annotate_log_discontinuities(lines) == lines


@pytest.mark.parametrize(
    "line",
    [
        "no prefix at all",
        "Xxx 14 21:18:42 host of[1]: bad month",
        "Sep 14 21:18:6a host of[1]: bad seconds",  # not a digit group, so no prefix matches
    ],
)
def test_log_line_stamp_rejects_unparseable_prefixes(line: str) -> None:
    assert diag._log_line_stamp(line) is None
