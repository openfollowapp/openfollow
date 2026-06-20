# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Diagnostics bundle data and collectors for system-state snapshots.

``collect_bundle`` runs every section collector and packs the result into a
``DiagnosticsBundle``; ``format_bundle`` renders it as the operator-facing text
and ``write_bundle_to_disk`` persists it with bounded retention. Live runtime
state is pulled through the optional ``DiagnosticsProviders`` callables so the
module is testable without a running ``ConfigWebServer``; ``web_pin`` and
``X-Auth-Signature`` values are redacted from config and log output.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import json
import logging
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any, TypeVar

from openfollow.logging_setup import RingBufferLogHandler

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

# Cap each stat-style probe (os.stat / os.statvfs / psutil.disk_usage). These
# block uninterruptibly in the kernel on a hung NFS/CIFS/USB mount, and
# try/except cannot catch a D-state hang.
_STAT_PROBE_TIMEOUT_S = 2.0
# A truly hung mount never lets its probe thread finish, so each timed-out probe
# leaks one daemon thread. Cap how many can be in flight / orphaned at once so
# repeated diagnostics requests against a stale mount can't grow threads without
# bound; beyond the cap, probes fail fast to the timeout value.
_STAT_PROBE_MAX_INFLIGHT = 16
_stat_probe_sem = threading.BoundedSemaphore(_STAT_PROBE_MAX_INFLIGHT)


def _bounded_probe(fn: Callable[[], _T], timeout_s: float, timeout_value: _T) -> _T:
    """Run ``fn()`` on a daemon thread; return its result, or ``timeout_value``
    if it hasn't returned within ``timeout_s``.

    The diagnostics bundle is assembled synchronously in the WSGI worker with no
    outer deadline, so a single stat that hangs on a stale mount would wedge the
    worker permanently and, under repeated requests, 503 the whole web UI. The
    request thread abandons the orphaned probe (a daemon thread that unblocks if
    the mount ever recovers) instead of blocking. ``fn`` handles its own
    exceptions and returns a value; an unexpected raise folds into ``timeout_value``."""
    if not _stat_probe_sem.acquire(blocking=False):
        # Too many probes already orphaned on a hung mount – fail fast instead
        # of leaking yet another thread.
        return timeout_value
    box: list[_T] = [timeout_value]
    done = threading.Event()

    def _run() -> None:
        try:
            box[0] = fn()
        except Exception:  # noqa: BLE001 - fn already formats its own errors
            box[0] = timeout_value
        finally:
            _stat_probe_sem.release()
            done.set()

    try:
        threading.Thread(target=_run, daemon=True, name="diag-stat-probe").start()
    except RuntimeError:
        # Couldn't spawn the probe thread – release the permit we took so the
        # cap isn't permanently reduced, and degrade to the timeout value.
        _stat_probe_sem.release()
        return timeout_value
    done.wait(timeout_s)
    return box[0]


# Default per-subprocess timeout. The bundle is assembled synchronously
# in the request handler with no outer deadline, so the rough worst-case
# wall-clock is the sum of the section caps: this default ×(quick probes)
# + ``_PROFILER_TIMEOUT_S`` (macOS only) + ``_FAILURE_EXTRACT_TIMEOUT_S``
# (the 24h journald --grep scan) + ``_STORAGE_SECTION_BUDGET_S`` (a shared
# deadline across all ``du`` calls, not per-path × count). On the Pi
# (no system_profiler) that lands around 25–30 s in the pathological case
# and well under it normally. Individual callers override as needed.
_DEFAULT_SUBPROCESS_TIMEOUT_S = 5.0

# system_profiler is slow; use a longer timeout to allow it to complete.
_PROFILER_TIMEOUT_S = 8.0

# The 24h ``journalctl --grep`` failure extract is a full scan of the day's
# journal – the slowest journald call in the bundle. Sized well above the
# 5 s default so a busy host's history isn't truncated to the in-process
# ring (the whole point of the extract is the longer window).
_FAILURE_EXTRACT_TIMEOUT_S = 12.0

# Back-compat alias so any external caller importing the old name
# keeps working. Same value, same semantics – default cap.
_SUBPROCESS_TIMEOUT_S = _DEFAULT_SUBPROCESS_TIMEOUT_S

# Pre-compiled regex – strips the value off any ``X-Auth-Signature:``
# header line in the log tail. Hex-only payload to avoid eating
# adjacent text. Case-insensitive on the header name.
_SIGNATURE_REDACT_RE = re.compile(
    r"(?im)^(.*X-Auth-Signature:\s*)[0-9a-f]+",
)


# ---------------------------------------------------------------------------
# Provider hooks – minimal contract so this module is callable from a test
# without a live ``ConfigWebServer``.
# ---------------------------------------------------------------------------


@dataclass
class DiagnosticsProviders:
    """Callable hooks for live runtime state. Every field defaults
    to ``None``; the matching collector renders ``[not applicable:
    service not running]`` when its provider is missing.

    Pulling state through callables keeps this module independent of
    the web layer, allowing the test suite to drive the bundle with fakes.
    """

    web_port_configured: Callable[[], int] | None = None
    web_port_display: Callable[[], int] | None = None
    process_uptime_s: Callable[[], str] | None = None
    process_pid: Callable[[], int] | None = None
    restart_count: Callable[[], int] | None = None

    beacon_sender_health: Callable[[], dict[str, Any]] | None = None
    beacon_receiver_health: Callable[[], dict[str, Any]] | None = None
    known_peers: Callable[[], list[dict[str, Any]]] | None = None
    iface_ip: Callable[[], str] | None = None

    config_redacted_toml: Callable[[], str] | None = None
    config_diff_from_defaults: Callable[[], list[str]] | None = None

    request_semaphore_rejections: Callable[[], int] | None = None
    detection_install_state: Callable[[], dict[str, Any]] | None = None
    worker_thread_tracebacks: Callable[[], dict[str, str]] | None = None

    recent_osc_sends: Callable[[], list[dict[str, Any]]] | None = None
    # Live OSC inbound-listener status: ``{"port", "multicast_group",
    # "multicast_joined", "allowed_sender_ips"}`` from ``OscService``.
    # ``None`` → section renders ``[not applicable: OSC service not wired]``.
    osc_multicast_status: Callable[[], dict[str, Any]] | None = None
    recent_midi_events: Callable[[], list[dict[str, Any]]] | None = None

    # Subsystem indices for the USB visibility column. Each returns
    # a list of strings (port names / joystick names / camera
    # labels). ``None`` → corresponding visibility cell stays "–"
    # for every device, with a footer note explaining why.
    midi_port_names: Callable[[], list[str]] | None = None
    gamepad_names: Callable[[], list[str]] | None = None
    camera_names: Callable[[], list[str]] | None = None

    # Live per-controller snapshot for the dedicated gamepad section.
    # Each dict carries: index, name, guid, backend, num_axes, num_buttons,
    # num_hats, is_game_controller, matches_calibration, calibration_stored
    # (the shape of ``GamepadHandler.runtime_snapshot`` items, as dicts).
    # ``None`` → section renders ``[not applicable: gamepad provider not
    # wired]``. Surfaces the SDL view (X-input vs raw-joystick backend) and
    # any calibration mismatch a support ticket about "a button won't bind"
    # needs, which the effective-config dump alone can't show.
    gamepad_runtime: Callable[[], list[dict[str, Any]]] | None = None

    # Per-capability privilege state (passwordless /
    # needs_password / unavailable). Returns ``{name: state}`` –
    # exactly the shape ``ConfigWebServer.get_privilege_capability_states``
    # produces. ``None`` → the diagnostics section renders ``[not
    # applicable: privilege broker not wired]``. Surfaces in the
    # bundle so a support ticket about "network apply prompts every
    # time" includes the diagnosis (which capabilities are
    # ``needs_password`` vs ``passwordless``).
    privilege_states: Callable[[], dict[str, str]] | None = None


# ---------------------------------------------------------------------------
# Subprocess helper – used by every shell-touching collector.
# ---------------------------------------------------------------------------


def _run(
    cmd: list[str],
    *,
    timeout_s: float = _DEFAULT_SUBPROCESS_TIMEOUT_S,
    stdout_on_error: bool = False,
) -> tuple[int, str]:
    """Run ``cmd``; return ``(returncode, output)``.

    ``output`` is stdout if the process succeeded, stderr otherwise
    (trimmed). Missing binary / timeout / launch-error are folded
    into a sentinel ``returncode=-1`` with a ``[unavailable: …]``
    message in ``output`` so callers can pattern-match without
    wrapping the call in their own try/except.

    ``stdout_on_error`` returns stdout even on a non-zero exit *when the
    process still wrote some* – for tools like ``du`` that emit a valid total
    to stdout yet exit non-zero because one sub-directory was unreadable (e.g.
    apt's root-only, always-empty ``archives/partial``). The stderr warning is
    discarded in that case.
    """
    if not cmd or not shutil.which(cmd[0]):
        return -1, f"[unavailable: {cmd[0] if cmd else '(empty)'} not found]"
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return -1, f"[unavailable: {cmd[0]} timed out after {timeout_s}s]"
    except OSError as exc:
        return -1, f"[unavailable: {cmd[0]}: {exc.strerror or exc!s}]"
    if result.returncode == 0 or (stdout_on_error and result.stdout):
        out = result.stdout or ""
    else:
        out = result.stderr or ""
    return result.returncode, out.rstrip()


# ---------------------------------------------------------------------------
# Section A – service / port
# ---------------------------------------------------------------------------


def collect_service(p: DiagnosticsProviders) -> list[str]:
    """Service-state lines. All values come through the providers –
    none of them can be derived from the host alone (port binding
    state, process uptime, etc. live on the running
    ``ConfigWebServer`` instance)."""
    rows: list[str] = []
    if p.web_port_configured is None:
        rows.append("  [not applicable: ConfigWebServer not wired]")
        return rows
    cfg = _safely(p.web_port_configured, "web_port_configured")
    rows.append(f"  Configured web_port:        {cfg}")
    if p.web_port_display is not None:
        disp = _safely(p.web_port_display, "web_port_display")
        rows.append(f"  Actual display_port:        {disp}")
    if p.process_uptime_s is not None:
        up = _safely(p.process_uptime_s, "process_uptime_s")
        rows.append(f"  Process uptime:             {up}")
    if p.process_pid is not None:
        rows.append(f"  Process PID:                {_safely(p.process_pid, 'process_pid')}")
    if p.restart_count is not None:
        rows.append(f"  Restarts (window):          {_safely(p.restart_count, 'restart_count')}")
    return rows


def collect_osc_multicast(p: DiagnosticsProviders) -> list[str]:
    """OSC inbound-listener multicast status from the live ``OscService``.

    The effective-config dump (Section C) shows the *requested* group; this
    shows the *live* one and whether the kernel join actually succeeded – a
    failed ``IP_ADD_MEMBERSHIP`` is non-fatal, so the two can disagree."""
    rows: list[str] = []
    if p.osc_multicast_status is None:
        rows.append("  [not applicable: OSC service not wired]")
        return rows
    status, err = _safely_value(p.osc_multicast_status, "osc_multicast_status", {})
    if err is not None:
        rows.append(f"  {err}")
        return rows
    port = status.get("port")
    rows.append(f"  Listener port:              {port if port is not None else '[not bound]'}")
    group = str(status.get("multicast_group") or "")
    if group:
        joined = "joined" if status.get("multicast_joined") else "JOIN FAILED"
        rows.append(f"  Multicast group:            {group} ({joined})")
    else:
        rows.append("  Multicast group:            [none – unicast/broadcast only]")
    allow = status.get("allowed_sender_ips") or []
    if allow:
        rows.append(f"  Sender allowlist:           {', '.join(allow)}")
    else:
        rows.append("  Sender allowlist:           [open – any LAN device]")
    return rows


def _safely(fn: Callable[[], Any], label: str) -> str:
    """Invoke a provider callable; render ``[unavailable]`` instead
    of propagating any exception. Boundary helper so each section
    doesn't repeat the try/except shape."""
    try:
        return str(fn())
    except Exception as exc:  # noqa: BLE001
        return f"[unavailable: {label}: {exc!r}]"


def _safely_value(
    fn: Callable[[], Any],
    label: str,
    default: Any,
) -> tuple[Any, str | None]:
    """Like :func:`_safely` but for providers that return structured
    data (dict / list). Returns ``(value, None)`` on success or
    ``(default, "[unavailable: …]")`` on failure so callers can both
    iterate the value and surface the failure as a sentinel row.
    Diagnostics' "Never raise" contract – a single bad provider can't
    take down the whole bundle when the operator needs it most."""
    try:
        return fn(), None
    except Exception as exc:  # noqa: BLE001
        return default, f"[unavailable: {label}: {exc!r}]"


# ---------------------------------------------------------------------------
# Section B – discovery / peers
# ---------------------------------------------------------------------------


def collect_discovery(p: DiagnosticsProviders) -> list[str]:
    rows: list[str] = []
    if p.beacon_sender_health is None and p.beacon_receiver_health is None:
        rows.append("  [not applicable: discovery not wired]")
    else:
        if p.beacon_sender_health is not None:
            rows.append("  Beacon sender:")
            health, err = _safely_value(p.beacon_sender_health, "beacon_sender_health", {})
            if err is not None:
                rows.append(f"    {err}")
            else:
                for k, v in (health or {}).items():
                    rows.append(f"    {k:<22}{v}")
        if p.beacon_receiver_health is not None:
            rows.append("  Beacon receiver:")
            health, err = _safely_value(p.beacon_receiver_health, "beacon_receiver_health", {})
            if err is not None:
                rows.append(f"    {err}")
            else:
                for k, v in (health or {}).items():
                    rows.append(f"    {k:<22}{v}")
        if p.known_peers is not None:
            peers, err = _safely_value(p.known_peers, "known_peers", [])
            if err is not None:
                rows.append(f"  Known peers: {err}")
            else:
                peers = peers or []
                rows.append(f"  Known peers ({len(peers)}):")
                for peer in peers:
                    rows.append(
                        f"    {peer.get('name', '?'):<24} "
                        f"{peer.get('ip', '?')}:{peer.get('web_port', '?')}  "
                        f"last_seen={peer.get('last_seen_age_s', '?')}s"
                    )
        if p.iface_ip is not None:
            rows.append(f"  Multicast iface_ip:        {_safely(p.iface_ip, 'iface_ip')}")
    rows.append("  Local IPv4 addresses (host enumeration):")
    try:
        import psutil  # noqa: PLC0415

        seen = False
        for nic, addrs in psutil.net_if_addrs().items():
            for a in addrs:
                if a.family == socket.AF_INET and not a.address.startswith("127."):
                    rows.append(f"    {nic:<10}  {a.address}")
                    seen = True
        if not seen:
            rows.append("    [unavailable: no non-loopback IPv4 found]")
    except Exception as exc:  # noqa: BLE001
        rows.append(f"    [unavailable: {exc!r}]")
    rows.append(f"  SO_REUSEPORT available:    {hasattr(socket, 'SO_REUSEPORT')}")
    return rows


# ---------------------------------------------------------------------------
# Section C – effective config
# ---------------------------------------------------------------------------


_WEB_PIN_LINE_RE = re.compile(r"^(\s*)web_pin\s*=")


def redact_web_pin(toml_text: str) -> str:
    """Replace the ``web_pin`` value with ``"***"`` or ``"(empty)"`` in TOML text.

    The match is anchored to the exact ``web_pin`` key to avoid
    rewriting similar keys like ``web_pin_hint``.
    """
    out: list[str] = []
    for line in toml_text.splitlines():
        m = _WEB_PIN_LINE_RE.match(line)
        if m is not None:
            # Detect empty-string assignment so the bundle reader
            # can distinguish "operator never set a PIN" from "PIN
            # is set but redacted"; the security signal is meaningful.
            value_part = line.split("=", 1)[1].strip() if "=" in line else ""
            replacement = '"***"' if value_part not in ('""', "''", "") else '"(empty)"'
            indent = m.group(1)
            out.append(f"{indent}web_pin = {replacement}")
        else:
            out.append(line)
    return "\n".join(out)


def collect_config(p: DiagnosticsProviders) -> list[str]:
    rows: list[str] = []
    if p.config_redacted_toml is None:
        rows.append("  [not applicable: config provider not wired]")
        return rows
    try:
        text = p.config_redacted_toml() or ""
    except Exception as exc:  # noqa: BLE001
        rows.append(f"  [unavailable: {exc!r}]")
        return rows
    rows.append("  ----- begin effective config (web_pin redacted) -----")
    for line in text.splitlines():
        rows.append(f"  {line}")
    rows.append("  ----- end effective config -----")
    if p.config_diff_from_defaults is not None:
        rows.append("")
        rows.append("  Diff vs defaults:")
        deltas, err = _safely_value(
            p.config_diff_from_defaults,
            "config_diff_from_defaults",
            [],
        )
        if err is not None:
            rows.append(f"    {err}")
        else:
            for delta in deltas or []:
                rows.append(f"    {delta}")
    return rows


# ---------------------------------------------------------------------------
# Section D – recent failures
# ---------------------------------------------------------------------------


def redact_signatures(line: str) -> str:
    """Strip ``X-Auth-Signature: <hex>`` payload from a log line.
    Always-on (no toggle). Pre-compiled regex so the log-tail path
    stays cheap even when the ring is full."""
    return _SIGNATURE_REDACT_RE.sub(r"\1***", line)


# Log capture sizing – bounded to match the in-memory ring capacity.
_BUNDLE_LOG_TAIL_LINES = 2000

# Failure extract: severity-filtered view of WARNING/ERROR lines.
_FAILURE_EXTRACT_SINCE = "-24h"
_FAILURE_EXTRACT_LINES = 1000

# Match the ``[LEVELNAME]`` token written by ``logging_setup.DEFAULT_FORMAT``
# (``"%(asctime)s [%(levelname)s] %(name)s: %(message)s"``). We filter on
# this TEXT token rather than ``journalctl -p warning`` on purpose: the app
# logs to stderr, which journald tags at a single uniform priority, so
# ``-p warning`` misses Python-level WARNING/ERROR lines entirely (it only
# catches GLib/GStreamer messages routed through sd_journal). Text-matching
# the level token works identically over journald output and the ring.
_FAILURE_LEVEL_RE = re.compile(r"\[(?:WARNING|ERROR|CRITICAL)\]")

# Server-side equivalent for ``journalctl --grep`` (PCRE2). Kept in lockstep
# with ``_FAILURE_LEVEL_RE`` so the journald and ring paths select the same
# lines. journalctl's smart-case makes an all-uppercase pattern case-sensitive.
_FAILURE_LEVEL_GREP = r"\[(WARNING|ERROR|CRITICAL)\]"

# journald captures foreign stderr from the graphics stack (cage / wlroots /
# Mesa / Xwayland) under our unit, and some of it carries a ``[ERROR]`` token
# that is NOT one of our log lines. These specific lines are benign and noisy on
# every clean boot; left unfiltered they pad the failure extract and can mask a
# real error. Each pattern is kept tight so a genuine error in the same
# subsystem (a different EGL call / error code) still surfaces.
_BENIGN_FAILURE_RES: tuple[re.Pattern[str], ...] = (
    # wlroots' EGL device-enumeration probe on GPUs without EGL_EXT_device_query
    # (e.g. the Raspberry Pi VC4/V3D): logged at ERROR, then wlroots falls back.
    # Rendering is unaffected.
    re.compile(r"\[EGL\] command: eglQueryDeviceStringEXT, error: EGL_BAD_PARAMETER"),
)


def _is_failure_line(line: str) -> bool:
    """``True`` when a formatted log line is WARNING level or worse.

    Known-benign foreign stderr (``_BENIGN_FAILURE_RES``) is excluded even
    though it carries a level token – it isn't an app failure and would
    otherwise mask real ones in the extract.
    """
    if _FAILURE_LEVEL_RE.search(line) is None:
        return False
    return not any(rx.search(line) for rx in _BENIGN_FAILURE_RES)


def collect_recent_failures(
    p: DiagnosticsProviders,
    log_collector: Callable[[], tuple[str, list[str]]],
    failure_collector: Callable[[], tuple[str, list[str]]] | None = None,
) -> list[str]:
    """Collect recent log lines and optionally a severity-filtered extract.

    ``log_collector`` returns ``(source_label, lines)`` and is injectable
    for test isolation. ``failure_collector`` provides a severity-filtered,
    longer-window view for WARNING/ERROR lines.
    """
    rows: list[str] = []
    try:
        src, log_lines = log_collector()
    except Exception as exc:  # noqa: BLE001
        rows.append(f"  Log source: [unavailable: log_collector: {exc!r}]")
        rows.append("  ----- begin log tail -----")
        rows.append("  ----- end log tail -----")
    else:
        rows.append(f"  Log source: {src}")
        rows.append("  ----- begin log tail -----")
        for line in log_lines:
            rows.append(f"  {redact_signatures(line)}")
        rows.append("  ----- end log tail -----")
    if failure_collector is not None:
        rows.append("")
        # Same never-abort contract as ``log_collector`` above.
        try:
            fsrc, failure_lines = failure_collector()
        except Exception as exc:  # noqa: BLE001
            rows.append(f"  Failure extract: [unavailable: failure_collector: {exc!r}]")
        else:
            # The 24h window only applies to journald; the ring fallback
            # covers the current process lifetime / ring capacity.
            window = "last 24h" if fsrc == "journalctl" else "this process (ring buffer)"
            rows.append(f"  Failure extract (WARNING+, {window}, source: {fsrc}):")
            rows.append("  ----- begin failure extract -----")
            if failure_lines:
                for line in failure_lines:
                    rows.append(f"  {redact_signatures(line)}")
            else:
                rows.append("  [no WARNING/ERROR/CRITICAL lines in window]")
            rows.append("  ----- end failure extract -----")
    if p.worker_thread_tracebacks is not None:
        rows.append("")
        rows.append("  Last worker-thread tracebacks:")
        tbs, err = _safely_value(p.worker_thread_tracebacks, "worker_thread_tracebacks", {})
        if err is not None:
            rows.append(f"    {err}")
        else:
            tbs = tbs or {}
            if not tbs:
                rows.append("    [none recorded since process start]")
            else:
                for thread, tb in tbs.items():
                    rows.append(f"    --- {thread} ---")
                    for tb_line in tb.splitlines():
                        # Redact for parity with the log tail / failure extract:
                        # standard tracebacks carry no locals, but a future
                        # change (or a captured header string) shouldn't leak
                        # an HMAC signature past the always-on stripping.
                        rows.append(f"    {redact_signatures(tb_line)}")
    if p.request_semaphore_rejections is not None:
        rows.append(
            f"  Request semaphore rejections (503s): "
            f"{_safely(p.request_semaphore_rejections, 'request_semaphore_rejections')}"
        )
    if p.detection_install_state is not None:
        rows.append("  Detection install job:")
        state, err = _safely_value(p.detection_install_state, "detection_install_state", {})
        if err is not None:
            rows.append(f"    {err}")
        else:
            for k, v in (state or {}).items():
                rows.append(f"    {k:<18}{v}")
    return rows


# TTL cache for ``probe_log_source``'s journalctl reachability
# probe. Keyed by service name so a config edit that changes the
# unit invalidates the previous answer on the next poll.
_PROBE_LOG_SOURCE_TTL_S = 60.0
_probe_log_source_cache: dict[str, tuple[float, bool]] = {}
# The diagnostics card polls every 5 s on a threaded WSGI server, so guard
# the cache. Held across the probe (not just the dict ops) so concurrent
# cold-cache pollers collapse onto one ``journalctl`` spawn instead of each
# racing the TTL the cache exists to enforce – a poller waits at most one
# probe duration, then reads the freshly cached answer.
_probe_log_source_lock = threading.Lock()


def _journalctl_reachable(service_name: str) -> bool:
    """Check if journalctl is available and can reach the service.

    Result is cached to avoid re-spawning the probe on every poll.
    """
    now = time.monotonic()
    with _probe_log_source_lock:
        cached = _probe_log_source_cache.get(service_name)
        if cached is not None and now - cached[0] < _PROBE_LOG_SOURCE_TTL_S:
            return cached[1]
        if shutil.which("journalctl") is None:
            ok = False
        else:
            rc, out = _run(["journalctl", "-u", service_name, "-n", "0"])
            ok = rc == 0 and not out.startswith("[unavailable:")
        _probe_log_source_cache[service_name] = (now, ok)
        return ok


def probe_log_source(
    update_service_name: str | None,
    *,
    ring: RingBufferLogHandler | None = None,
) -> str:
    """Return the log source label without spawning journalctl.

    Cached probing for the diagnostics UI poll. Consults the ring
    on the fallback path so the UI doesn't promise a source that doesn't exist.
    """
    if not update_service_name:
        if ring is None:
            return "no log source available (ring not initialised)"
        return "in-memory ring buffer (no journald service name configured)"
    if not _journalctl_reachable(update_service_name):
        if ring is None:
            return "no log source available (ring not initialised)"
        return "in-memory ring buffer (journalctl unavailable)"
    return "journalctl"


def collect_log_tail(
    ring: RingBufferLogHandler | None,
    *,
    update_service_name: str | None = None,
    last_n: int = 500,
) -> tuple[str, list[str]]:
    """Read up to ``last_n`` log lines, preferring journalctl and falling back to the ring.

    Returns ``(source_label, lines)`` where the label describes the source.
    """
    if update_service_name:
        rc, out = _run(["journalctl", "-u", update_service_name, "-n", str(last_n)])
        if rc == 0 and out and not out.startswith("[unavailable:"):
            return "journalctl", out.splitlines()
        # Fall through to ring on non-zero exit / missing binary.
        fallback_label = "in-memory ring buffer (journalctl unavailable)"
    else:
        fallback_label = "in-memory ring buffer (no journald service name configured)"
    if ring is None:
        # Match ``probe_log_source``'s wording so the cards and the
        # bundle / log-tail download don't disagree on what the
        # operator's log source actually is.
        return (
            "no log source available (ring not initialised)",
            ["[unavailable: ring buffer not initialised]"],
        )
    return fallback_label, ring.snapshot(last_n=last_n)


def collect_failure_extract(
    ring: RingBufferLogHandler | None,
    *,
    update_service_name: str | None = None,
    since: str = _FAILURE_EXTRACT_SINCE,
    last_n: int = _FAILURE_EXTRACT_LINES,
) -> tuple[str, list[str]]:
    """Severity-filtered log extract; WARNING/ERROR lines only.

    Over a longer window than the raw tail to prevent WARNING/ERROR lines
    from being evicted during an INFO flood. Returns ``(source_label, lines)``.
    """
    if update_service_name:
        rc, out = _run(
            [
                "journalctl",
                "-u",
                update_service_name,
                "--since",
                since,
                "--grep",
                _FAILURE_LEVEL_GREP,
                "-n",
                str(last_n),
            ],
            timeout_s=_FAILURE_EXTRACT_TIMEOUT_S,
        )
        # An empty result with rc 0 is legitimate: journalctl ran and the
        # window simply held no failures. Only fall through to the ring on
        # an actual error sentinel / non-zero exit.
        if rc == 0 and not out.startswith("[unavailable:"):
            return "journalctl", [line for line in out.splitlines() if _is_failure_line(line)]
        if "timed out" in out:
            # Distinct from "journalctl unavailable" so the operator knows the
            # 24h window was truncated to this process's lifetime, not that
            # journald is missing – the ring only covers the current process.
            fallback_label = "in-memory ring buffer (journalctl 24h scan timed out – window truncated)"
        else:
            fallback_label = "in-memory ring buffer (journalctl unavailable)"
    else:
        fallback_label = "in-memory ring buffer (no journald service name configured)"
    if ring is None:
        return (
            "no log source available (ring not initialised)",
            [],
        )
    failures = [line for line in ring.snapshot() if _is_failure_line(line)]
    return fallback_label, failures[-last_n:] if last_n > 0 else []


# ---------------------------------------------------------------------------
# Section E – environment
# ---------------------------------------------------------------------------


# E1. Runtime / versions ----------------------------------------------------


def _safe_version(dist: str) -> str:
    try:
        return importlib.metadata.version(dist)
    except importlib.metadata.PackageNotFoundError:
        return "[not installed]"


def _git_failure_detail(out: str) -> str:
    """Render a non-zero git result as an actionable sentinel."""
    if not out:
        return "[unavailable]"
    return out.splitlines()[0]


def _gtk3_version() -> str:
    """Report the GTK 3 version the app actually uses at runtime.

    Probes the import path first (no dev package required), then falls
    back to pkg-config if the runtime probe is inconclusive.
    """
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk

        ver = f"{Gtk.get_major_version()}.{Gtk.get_minor_version()}.{Gtk.get_micro_version()}"
        return f"{ver} (gi {getattr(gi, '__version__', '?')})"
    except Exception:
        # Runtime probe failed (PyGObject absent – e.g. macOS dev / CI –
        # or the GTK 3 typelib is missing). Fall back to the dev-package
        # metadata so we still report a version where it exists.
        rc, out = _run(["pkg-config", "--modversion", "gtk+-3.0"])
        return out if rc == 0 and out else "[unavailable]"


def collect_runtime_versions(repo_root: Path | None = None) -> list[str]:
    rows = [f"  Python                       {sys.version.split(chr(32), 1)[0]}"]
    if repo_root is not None:
        rc, out = _run(["git", "-C", str(repo_root), "rev-parse", "HEAD"])
        rows.append(f"  OpenFollow git rev           {out if rc == 0 else _git_failure_detail(out)}")
        rc, out = _run(["git", "-C", str(repo_root), "rev-parse", "--abbrev-ref", "HEAD"])
        rows.append(f"  Branch                       {out if rc == 0 else _git_failure_detail(out)}")
        rc, out = _run(["git", "-C", str(repo_root), "status", "--porcelain"])
        if rc == 0:
            rows.append(f"  Working tree                 {'dirty' if out.strip() else 'clean'}")
        else:
            rows.append(f"  Working tree                 {_git_failure_detail(out)}")
    for dist in ("bottle", "pygame", "psutil"):
        rows.append(f"  {dist:<29}{_safe_version(dist)}")
    rc, out = _run(["gst-launch-1.0", "--version"])
    rows.append(f"  GStreamer                    {out.splitlines()[0] if rc == 0 and out else '[unavailable]'}")
    rows.append(f"  GTK 3                        {_gtk3_version()}")
    ndi = "present" if importlib.util.find_spec("NDIlib") else "[not present]"
    rows.append(f"  libndi                       {ndi}")
    return rows


# E2. Person detection stack ------------------------------------------------


# Distribution name → (importable module, friendly label). Some
# distributions ship multiple modules (opencv-python(-headless)
# both ship ``cv2``); we list each separately because the
# distribution name is what ``importlib.metadata.version`` keys on.
_DETECTION_DISTRIBUTIONS: tuple[tuple[str, str], ...] = (
    ("onnxruntime", "onnxruntime"),
    ("mediapipe", "mediapipe"),
    ("opencv-python", "cv2"),
    ("opencv-python-headless", "cv2"),
)


def collect_detection_stack() -> list[str]:
    rows: list[str] = []
    for dist, _mod in _DETECTION_DISTRIBUTIONS:
        try:
            v = importlib.metadata.version(dist)
        except importlib.metadata.PackageNotFoundError:
            rows.append(f"  {dist:<29}[not installed]")
            continue
        rows.append(f"  {dist:<29}installed ({v})")
    if importlib.util.find_spec("onnxruntime") is not None:
        try:
            ort = importlib.import_module("onnxruntime")
            providers = ort.get_available_providers()
            rows.append(f"  onnxruntime providers        {', '.join(providers)}")
        except Exception as exc:  # noqa: BLE001
            rows.append(f"  onnxruntime providers        [unavailable: {exc!r}]")
    return rows


# E3. Operating system ------------------------------------------------------


def collect_os() -> list[str]:
    rows = [
        f"  system                       {platform.system()}",
        f"  release                      {platform.release()}",
        f"  version                      {platform.version()}",
        f"  machine                      {platform.machine()}",
        f"  processor                    {platform.processor() or '(empty)'}",
        f"  platform                     {platform.platform()}",
    ]
    sysname = platform.system()
    if sysname == "Linux":
        try:
            for line in Path("/etc/os-release").read_text().splitlines():
                if line and not line.startswith("#"):
                    rows.append(f"  {line}")
        except OSError as exc:
            rows.append(f"  /etc/os-release              [unavailable: {exc.strerror or exc!s}]")
    elif sysname == "Darwin":
        rc, out = _run(["sw_vers"])
        if rc == 0:
            for line in out.splitlines():
                rows.append(f"  {line}")
        else:
            rows.append(f"  sw_vers                      {out}")
    return rows


# E4. CPU --------------------------------------------------------------------


def _cpu_brand() -> str:
    sysname = platform.system()
    if sysname == "Linux":
        try:
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
        except OSError:
            pass
        return "[unavailable]"
    if sysname == "Darwin":
        rc, out = _run(["sysctl", "-n", "machdep.cpu.brand_string"])
        return out if rc == 0 else "[unavailable]"
    return "[unavailable: unsupported platform]"


def collect_cpu() -> list[str]:
    # Local imports here keep the module's top-level imports cheap;
    # psutil is a hard project dep so each is guaranteed to succeed.
    import psutil  # noqa: PLC0415

    rows = [
        f"  logical cores                {psutil.cpu_count()}",
        f"  physical cores               {psutil.cpu_count(logical=False) or '(unavailable)'}",
        f"  brand                        {_cpu_brand()}",
    ]
    try:
        freq = psutil.cpu_freq()
    except Exception:  # noqa: BLE001 – some platforms raise NotImplementedError
        freq = None
    if freq is None:
        rows.append("  frequency                    [unavailable]")
    else:
        rows.append(
            f"  frequency MHz                current={freq.current:.0f} min={freq.min or 0:.0f} max={freq.max or 0:.0f}"
        )
    try:
        sample = psutil.cpu_percent(interval=0.2, percpu=True)
        rows.append("  per-core %% (200 ms)         " + " ".join(f"{p:>5.1f}" for p in sample))
    except Exception as exc:  # noqa: BLE001
        rows.append(f"  per-core %%                  [unavailable: {exc!r}]")
    return rows


# E5. Memory + disk ---------------------------------------------------------


def _inode_usage_row(path: str) -> str | None:
    """Render an inode-usage row for ``path``, or ``None`` when inode
    info isn't available.

    A filesystem can be byte-free yet inode-exhausted (lots of tiny
    files), which fails writes while the df-style byte usage still looks
    healthy – so this is worth surfacing alongside bytes. ``statvfs`` is
    Unix-only; returns ``None`` where it's unavailable, errors, or the
    filesystem reports no inodes (some virtual / network mounts).

    ``avail`` reports ``f_favail`` (inodes available to a non-privileged
    process), not ``f_ffree`` (which also counts root-reserved inodes).
    The service runs as the non-root ``openfollow`` user, so ``f_favail``
    is what actually bounds whether it can still create files – and the
    percentage is the share *unavailable to us*, the triage-relevant
    figure. ``used`` stays the true allocated count (from ``f_ffree``).

    Split out from :func:`collect_memory_disk` so it's testable in
    isolation: ``psutil.disk_usage`` shares ``os.statvfs`` under the
    hood on POSIX, so patching ``statvfs`` to exercise the inode paths
    can't be done against the byte-usage call without breaking it too."""
    if not hasattr(os, "statvfs"):
        return None
    try:
        st = os.statvfs(path)
    except OSError:
        return None
    if not st.f_files:
        return None
    iused = st.f_files - st.f_ffree
    return (
        f"  inodes {path:<21} used={iused} avail={st.f_favail} ({100.0 * (st.f_files - st.f_favail) / st.f_files:.0f}%)"
    )


def _disk_usage_row(p: Path) -> str:
    """Format a ``psutil.disk_usage`` row for ``p`` (handles OSError)."""
    import psutil  # noqa: PLC0415

    try:
        d = psutil.disk_usage(str(p))
    except OSError as exc:
        return f"  disk {str(p):<23} [unavailable: {exc.strerror or exc!s}]"
    return f"  disk {str(p):<23} total={d.total / 1e9:.1f} GB free={d.free / 1e9:.1f} GB used={d.percent:.0f}%"


def _partition_usage(mountpoint: str) -> str:
    """Format the used/total/percent string for a mount (handles OSError)."""
    import psutil  # noqa: PLC0415

    try:
        u = psutil.disk_usage(mountpoint)
    except OSError as exc:
        return f"[unavailable: {exc.strerror or exc!s}]"
    return f"{u.used / 1e9:.1f}/{u.total / 1e9:.1f} GB ({u.percent:.0f}%)"


def _path_exists(path: Path) -> bool:
    """``Path.exists`` that folds a fast OSError (e.g. permission denied on a
    parent) to ``False`` – so only a true kernel hang surfaces as the
    ``_bounded_probe`` timeout sentinel, not a stat that errored quickly."""
    try:
        return path.exists()
    except OSError:
        return False


def collect_memory_disk(extra_paths: list[Path] | None = None) -> list[str]:
    import psutil  # noqa: PLC0415

    vm = psutil.virtual_memory()
    sm = psutil.swap_memory()
    rows = [
        f"  virtual mem total            {vm.total / 1e9:.1f} GB",
        f"  virtual mem available        {vm.available / 1e9:.1f} GB",
        f"  virtual mem used %           {vm.percent:.0f}%",
        f"  swap total                   {sm.total / 1e9:.1f} GB",
        f"  swap used                    {sm.used / 1e9:.1f} GB",
    ]
    paths_to_check = [Path("/")]
    if extra_paths:
        paths_to_check.extend(extra_paths)
    for p in paths_to_check:
        # An operator-configured detection.storage_path can be a stale NFS/CIFS/
        # USB mount; bound disk_usage + statvfs so neither hangs the bundle.
        timeout_row = f"  disk {str(p):<23} [unavailable: stat timed out (stale mount?)]"
        row = _bounded_probe(partial(_disk_usage_row, p), _STAT_PROBE_TIMEOUT_S, timeout_row)
        rows.append(row)
        if row is timeout_row:
            continue  # don't re-hang on statvfs for the same stale mount
        inode_row = _bounded_probe(partial(_inode_usage_row, str(p)), _STAT_PROBE_TIMEOUT_S, None)
        if inode_row is not None:
            rows.append(inode_row)
    return rows


# E5b. Storage breakdown ----------------------------------------------------
#
# Answers the one question ``df`` can't: *where did the space go?* A 16 GB
# Pi SD card filling to 90%+ is almost always one of a handful of trees –
# the apt download cache (never auto-cleaned), the pip / Poetry caches,
# the journal, the OpenFollow checkout, or detection models that landed on
# the SD card because the NVMe drive wasn't mounted. This section sizes
# those directly and shows the mount table so "NVMe not mounted" is
# obvious at a glance.

# Per-path ``du`` is bounded so one pathological tree (e.g. a multi-GB
# Poetry venv) can't stall the whole bundle. A shared
# ``_STORAGE_SECTION_BUDGET_S`` deadline caps the *total* du time across all
# candidates too, so the worst case is the budget – not the unbounded
# ``len(candidates) * _STORAGE_DU_TIMEOUT_S`` (~48 s on a near-full disk,
# exactly the case this section exists to diagnose, where a browser /
# reverse-proxy read timeout could fire before the bundle returns).
# Candidates reached after the deadline are listed as skipped, not silently
# dropped.
_STORAGE_DU_TIMEOUT_S = 6.0
_STORAGE_SECTION_BUDGET_S = 12.0


def _storage_candidate_paths(
    repo_root: Path | None,
    extra_paths: list[Path] | None,
) -> list[tuple[str, Path]]:
    """Curated (label, path) pairs most likely to explain a near-full
    OpenFollow disk. Home-relative entries resolve against the running
    user's home – the systemd unit runs as ``openfollow``, so these land
    on ``/home/openfollow``. Non-existent paths are filtered by the
    caller, so listing one that may be absent (the NVMe model root) is
    harmless."""
    home = Path.home()
    candidates: list[tuple[str, Path]] = [
        ("apt package cache", Path("/var/cache/apt/archives")),
        ("systemd journal", Path("/var/log/journal")),
        ("pip cache", home / ".cache" / "pip"),
        ("Poetry cache + venvs", home / ".cache" / "pypoetry"),
        ("OpenFollow data", home / ".openfollow"),
        # The installer's default NVMe detection-model root. Sized even
        # when the operator never set ``detection.storage_path`` so models
        # that fell back onto the SD card (NVMe absent / unmounted) still
        # show up.
        ("NVMe detection models", Path("/mnt/nvme/openfollow")),
    ]
    if repo_root is not None:
        candidates.append(("OpenFollow checkout", repo_root))
    for p in extra_paths or []:
        candidates.append((f"configured storage ({p})", p))
    return candidates


def _du_kib(path: Path, *, timeout_s: float) -> int | None:
    """On-disk size of ``path`` in KiB via ``du -s -k -x`` (portable
    across GNU and BSD ``du``), or ``None`` if ``du`` is missing / timed
    out / produced no usable total. ``-x`` keeps the walk on one filesystem
    so a mount nested under ``path`` isn't folded into the parent's total.
    ``--`` terminates options so an operator-configured path beginning with
    ``-`` is treated as a path, not a (failing) du flag.

    ``du`` exits non-zero when a *sub*-directory is unreadable (e.g. apt's
    root-only, always-empty ``archives/partial``) but still prints the
    accessible total to stdout – so we read stdout regardless of exit code
    (``stdout_on_error``) and only give up when there's no parseable total
    (missing/timed-out du → ``[unavailable: …]`` sentinel)."""
    _, out = _run(
        ["du", "-s", "-k", "-x", "--", str(path)],
        timeout_s=timeout_s,
        stdout_on_error=True,
    )
    if not out or out.startswith("[unavailable:"):
        return None
    first = out.splitlines()[0].split(None, 1)
    try:
        return int(first[0])
    except (ValueError, IndexError):
        return None


def collect_storage_breakdown(
    *,
    repo_root: Path | None = None,
    extra_paths: list[Path] | None = None,
) -> list[str]:
    import psutil  # noqa: PLC0415

    rows: list[str] = []

    # 1. Mount table – reveals whether the NVMe drive is actually
    #    mounted (vs. detection models silently filling the SD card) and
    #    the per-filesystem fill level in one view.
    rows.append("  Mounted filesystems:")
    try:
        parts = psutil.disk_partitions(all=False)
    except Exception as exc:  # noqa: BLE001
        parts = []
        rows.append(f"    [unavailable: disk_partitions: {exc!r}]")
    for part in parts:
        # Bound per-mount disk_usage: a stale mount in the partition table hangs
        # the stat uninterruptibly otherwise.
        usage = _bounded_probe(
            partial(_partition_usage, part.mountpoint),
            _STAT_PROBE_TIMEOUT_S,
            "[unavailable: stat timed out (stale mount?)]",
        )
        rows.append(f"    {part.mountpoint:<18} {usage:<26} {part.fstype or '?':<8} {part.device}")

    # 2. Largest known directories – the "where did the space go" view.
    #    Sized cheapest-isolated (per-path ``du`` with its own timeout) so
    #    a single huge tree degrades to a "[skipped]" line rather than
    #    taking the section down.
    rows.append("")
    rows.append("  Largest known directories (du -skx, bounded):")
    sized: list[tuple[int, str, Path]] = []
    skipped: list[str] = []
    over_budget: list[str] = []
    stat_timed_out: list[str] = []
    deadline = time.monotonic() + _STORAGE_SECTION_BUDGET_S
    for label, path in _storage_candidate_paths(repo_root, extra_paths):
        # Check the budget BEFORE any stat – a candidate reached after the
        # budget is skipped without touching it. The hardcoded /mnt/nvme and the
        # operator-configured extra paths can be stale mounts.
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            over_budget.append(f"{label} ({path})")
            continue
        # Bound exists() too: os.stat on a stale NFS/CIFS/USB mount blocks
        # uninterruptibly and try/except can't catch a kernel hang.
        exists = _bounded_probe(partial(_path_exists, path), min(_STAT_PROBE_TIMEOUT_S, remaining), None)
        if exists is None:
            stat_timed_out.append(f"{label} ({path})")
            continue
        if not exists:
            continue
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            over_budget.append(f"{label} ({path})")
            continue
        kib = _du_kib(path, timeout_s=min(_STORAGE_DU_TIMEOUT_S, remaining))
        if kib is None:
            skipped.append(f"{label} ({path})")
            continue
        sized.append((kib, label, path))
    if sized:
        for kib, label, path in sorted(sized, reverse=True):
            rows.append(f"    {kib * 1024 / 1e9:>7.2f} GB  {label:<22} {path}")
    else:
        rows.append("    [unavailable: no candidate directories sized (du missing, or none present)]")
    for entry in skipped:
        # ``_du_kib`` returns None for any of: du missing, timed out, launch
        # error, or output that couldn't be parsed (locale / busybox quirk) –
        # so the reason text covers all of them rather than implying only the
        # missing/timeout cases.
        rows.append(f"    [skipped: {entry} – du unavailable, timed out, or output unparseable]")
    for entry in stat_timed_out:
        rows.append(f"    [skipped: {entry} – stat timed out (stale mount?)]")
    for entry in over_budget:
        rows.append(
            f"    [skipped: {entry} – storage-section time budget ({_STORAGE_SECTION_BUDGET_S:.0f}s) exhausted]"
        )
    return rows


# E6. System health ---------------------------------------------------------


def collect_system_health() -> list[str]:
    import psutil  # noqa: PLC0415

    rows: list[str] = []
    bt = psutil.boot_time()
    rows.append(
        f"  boot time (UTC)              {datetime.fromtimestamp(bt, tz=timezone.utc).isoformat(timespec='seconds')}"
    )
    rows.append(f"  uptime                       {(time.time() - bt) / 3600:.1f} h")
    # ``sensors_temperatures`` doesn't exist on macOS at all
    # (``AttributeError``); on Linux it can be empty / Permission
    # Denied. Treat all three as the same "unavailable" case.
    try:
        temps = psutil.sensors_temperatures() if hasattr(psutil, "sensors_temperatures") else {}
        if not temps:
            rows.append("  temperatures                 [unavailable: not exposed by this OS]")
        else:
            for label, temp_items in temps.items():
                vals = ", ".join(f"{i.label or 'n/a'}={i.current:.1f}°C" for i in temp_items)
                rows.append(f"  temp[{label:<20}] {vals}")
    except Exception as exc:  # noqa: BLE001
        rows.append(f"  temperatures                 [unavailable: {exc!r}]")
    try:
        fans = psutil.sensors_fans() if hasattr(psutil, "sensors_fans") else {}
        if not fans:
            rows.append("  fans                         [unavailable: not exposed]")
        else:
            for label, fan_items in fans.items():
                vals = ", ".join(f"{i.label or 'n/a'}={i.current} rpm" for i in fan_items)
                rows.append(f"  fans[{label:<20}] {vals}")
    except Exception as exc:  # noqa: BLE001
        rows.append(f"  fans                         [unavailable: {exc!r}]")
    try:
        _battery = getattr(psutil, "sensors_battery", None)
        bat = _battery() if _battery is not None else None
        if bat is None:
            rows.append("  battery                      [not applicable: no battery]")
        else:
            rows.append(
                f"  battery                      {bat.percent:.0f}% {'plugged' if bat.power_plugged else 'on battery'}"
            )
    except Exception as exc:  # noqa: BLE001
        rows.append(f"  battery                      [unavailable: {exc!r}]")
    return rows


# E7. Network interfaces ----------------------------------------------------


def collect_network_interfaces() -> list[str]:
    import psutil  # noqa: PLC0415

    rows: list[str] = []
    duplex_label = {0: "unknown", 1: "half", 2: "full"}
    try:
        stats = psutil.net_if_stats()
    except Exception as exc:  # noqa: BLE001
        return [f"  [unavailable: net_if_stats: {exc!r}]"]
    for nic, st in stats.items():
        rows.append(
            f"  {nic:<14}isup={st.isup} speed={st.speed}Mb mtu={st.mtu} "
            f"duplex={duplex_label.get(int(st.duplex), str(st.duplex))}"
        )
    return rows


# E8. USB devices -----------------------------------------------------------


@dataclass
class UsbDevice:
    """Single USB endpoint discovered during enumeration. Both
    platform branches (sysfs on Linux, ``system_profiler -json`` on
    macOS) shape their output into this struct so the formatter
    doesn't have to special-case."""

    bus_label: str = "USB"
    vid: str = ""
    pid: str = ""
    speed: str = ""  # human "10 Gb/s" / "480 Mb/s" / "12 Mb/s" / "1.5 Mb/s"
    name: str = ""
    manufacturer: str = ""
    serial: str = ""
    is_hub: bool = False


# macOS speed enum → human; covers what ``system_profiler`` emits.
_MACOS_SPEED_LABEL = {
    "low_speed": "1.5 Mb/s",
    "full_speed": "12 Mb/s",
    "high_speed": "480 Mb/s",
    "super_speed": "5 Gb/s",
    "super_speed_plus": "10 Gb/s",
}


def _hex4(v: str) -> str:
    """Normalise a vendor / product id to lowercase hex without
    ``0x`` prefix. Apple reports its own vendor IDs as
    ``apple_vendor_id`` (ASCII), which we pass through verbatim
    so the operator can tell at a glance the device is Apple."""
    if not v:
        return ""
    if v.startswith("apple_"):
        return v
    s = v.lower().split()[0]
    # ``lstrip("0x")`` would strip *every* leading 0 and x – we only
    # want the prefix. ``removeprefix`` (Python 3.9+) does that.
    s = s.removeprefix("0x")
    return s[:4]


def _walk_macos(node: dict[str, Any], out: list[UsbDevice]) -> None:
    name = (node.get("_name") or "").strip()
    is_hub = "hub" in name.lower()
    vid = _hex4(str(node.get("vendor_id", "")))
    pid = _hex4(str(node.get("product_id", "")))
    speed_raw = str(node.get("device_speed") or node.get("speed", "")).strip()
    if vid or pid:
        out.append(
            UsbDevice(
                vid=vid,
                pid=pid,
                speed=_MACOS_SPEED_LABEL.get(speed_raw, speed_raw or "?"),
                name=name,
                manufacturer=(node.get("manufacturer") or "").strip(),
                serial=(node.get("serial_num") or "").strip(),
                is_hub=is_hub,
            )
        )
    for child in node.get("_items") or []:
        _walk_macos(child, out)


def collect_usb_devices_macos() -> list[UsbDevice]:
    rc, out = _run(
        ["system_profiler", "SPUSBDataType", "-json"],
        timeout_s=_PROFILER_TIMEOUT_S,
    )
    if rc != 0 or not out or out.startswith("[unavailable:"):
        return []
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return []
    devices: list[UsbDevice] = []
    for bus in data.get("SPUSBDataType", []):
        _walk_macos(bus, devices)
    return devices


def collect_usb_devices_linux(sysfs_root: Path | None = None) -> list[UsbDevice]:
    """Read ``/sys/bus/usb/devices/*/{idVendor,idProduct,product,
    manufacturer,serial,speed,bDeviceClass}`` directly. No
    subprocess, no parser fragility – the kernel's exposing
    structured fields and we're just reading them."""
    root = sysfs_root or Path("/sys/bus/usb/devices")
    if not root.exists():
        return []
    devices: list[UsbDevice] = []
    # Guard against ``OSError`` / ``PermissionError`` from
    # ``iterdir()`` – sysfs can exist but be unreadable on
    # Constrained/containerised hosts (e.g. unprivileged container with /sys masked) may not allow access.
    # Permission errors must not abort bundle generation.
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return []
    for entry in entries:
        # Skip non-device entries (interfaces, configurations).
        if ":" in entry.name:
            continue
        idv = _read_sysfs(entry, "idVendor")
        idp = _read_sysfs(entry, "idProduct")
        if not idv and not idp:
            continue
        speed_raw = _read_sysfs(entry, "speed")
        speed = f"{speed_raw} Mb/s" if speed_raw and speed_raw.isdigit() else (speed_raw or "?")
        dclass = _read_sysfs(entry, "bDeviceClass")
        # USB device class 09 = hub. Match name fallback so
        # devices that describe themselves through their interface
        # class still get tagged (rare).
        name = _read_sysfs(entry, "product") or ""
        is_hub = dclass == "09" or "hub" in name.lower()
        devices.append(
            UsbDevice(
                vid=idv.lower(),
                pid=idp.lower(),
                speed=speed,
                name=name,
                manufacturer=_read_sysfs(entry, "manufacturer") or "",
                serial=_read_sysfs(entry, "serial") or "",
                is_hub=is_hub,
            )
        )
    return devices


def _read_sysfs(node: Path, attr: str) -> str:
    try:
        return (node / attr).read_text().strip()
    except OSError:
        return ""


# Generic tokens ignored as match drivers to avoid spurious attribution.
_USB_GENERIC_TOKENS = frozenset(
    {
        "controller",
        "gamepad",
        "joystick",
        "game",
        "pad",
        "device",
        "usb",
        "audio",
        "composite",
        "interface",
        "midi",
        "keyboard",
        "mouse",
        "camera",
        "webcam",
        "video",
        "input",
        "hid",
        "wireless",
        "adapter",
        "receiver",
        "for",
        "and",
        "the",
        "with",
    }
)

_USB_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _usb_match_score(haystack: str, needle: str) -> int:
    """Score how strongly a USB device string identifies a subsystem.

    Verbatim embeds score highest; otherwise sum lengths of distinctive
    tokens (excluding generic words and very short tokens). Tokens are
    de-duplicated to avoid skewing by field repetition.
    """
    if needle and needle in haystack:
        return len(needle) + 1000
    score = 0
    for tok in set(_USB_TOKEN_RE.findall(haystack)):
        if len(tok) >= 3 and tok not in _USB_GENERIC_TOKENS and tok in needle:
            score += len(tok)
    return score


def _best_usb_match(haystack: str, raws: list[str]) -> str | None:
    """Pick the subsystem entry that best identifies this device, or
    ``None`` when nothing overlaps distinctively. Best = highest score;
    ties resolve to the earliest index entry so the choice is stable.
    """
    best_raw: str | None = None
    best_score = 0
    for raw in raws:
        score = _usb_match_score(haystack, raw.lower())
        if score > best_score:
            best_score = score
            best_raw = raw
    return best_raw


def render_usb_table(
    devices: list[UsbDevice],
    *,
    midi_ports: list[str] | None,
    gamepads: list[str] | None,
    cameras: list[str] | None,
) -> list[str]:
    """Render the USB section as a fixed-width table. Each non-hub
    device gets a "visibility" cell that cross-references the
    device's product / manufacturer string against each input
    subsystem's enumeration. Operator question this answers in one
    glance: "is the kernel seeing this device, and is OpenFollow
    picking it up?"
    """
    # Header rendered as a tuple of column titles + separators so
    # the line-length lint stays happy without sacrificing the
    # visual grid the operator sees.
    _h = f"  {'Bus':<5}  {'VID:PID':<9}  {'Speed':<10}  {'Name':<34}  {'Manufacturer':<18}  {'Serial':<17}  OpenFollow"
    _sep = f"  {'-' * 5}  {'-' * 9}  {'-' * 10}  {'-' * 34}  {'-' * 18}  {'-' * 17}  {'-' * 24}"
    rows: list[str] = [_h, _sep]
    counts = {"hub": 0, "midi": 0, "gamepad": 0, "camera": 0, "other": 0, "unclaimed": 0}
    for d in devices:
        if d.is_hub:
            vis = "(hub)"
            counts["hub"] += 1
        else:
            haystack = " | ".join(s for s in (d.name.lower(), d.manufacturer.lower()) if s)
            vis = ""
            raw = _best_usb_match(haystack, midi_ports or [])
            if raw:
                vis = f"MIDI: {raw}"
                counts["midi"] += 1
            if not vis:
                raw = _best_usb_match(haystack, gamepads or [])
                if raw:
                    vis = f"gamepad: {raw}"
                    counts["gamepad"] += 1
            if not vis:
                raw = _best_usb_match(haystack, cameras or [])
                if raw:
                    vis = f"camera: {raw}"
                    counts["camera"] += 1
            if not vis:
                any_index = any(x is not None for x in (midi_ports, gamepads, cameras))
                if any_index:
                    vis = "?  endpoint device, no subsystem claim"
                    counts["unclaimed"] += 1
                else:
                    vis = "–"
                    counts["other"] += 1
        vidpid = f"{d.vid or '----'}:{d.pid or '----'}"
        rows.append(
            f"  {d.bus_label:<5}  {vidpid:<9}  {d.speed:<10}  "
            f"{d.name[:34]:<34}  {d.manufacturer[:18]:<18}  "
            f"{(d.serial or '-')[:17]:<17}  {vis}"
        )
    rows.append("")
    rows.append(
        f"  Total: {len(devices)} devices ("
        f"{counts['hub']} hubs, "
        f"{counts['midi']} MIDI, "
        f"{counts['gamepad']} gamepad, "
        f"{counts['camera']} camera, "
        f"{counts['other'] + counts['unclaimed']} other endpoint)"
    )
    notes = []
    if midi_ports is None:
        notes.append("MIDI subsystem not available")
    if gamepads is None:
        notes.append("gamepad subsystem not available")
    if cameras is None:
        notes.append("camera subsystem not available")
    if notes:
        rows.append("  [note: visibility column degraded – " + ", ".join(notes) + "]")
    return rows


def collect_usb(p: DiagnosticsProviders) -> list[str]:
    sysname = platform.system()
    if sysname == "Linux":
        devices = collect_usb_devices_linux()
    elif sysname == "Darwin":
        devices = collect_usb_devices_macos()
    else:
        return [f"  [unavailable: USB enumeration not implemented for {sysname}]"]
    if not devices:
        if sysname == "Linux":
            return ["  [unavailable: no USB devices enumerated (/sys/bus/usb/devices missing or unreadable)]"]
        return ["  [unavailable: no USB devices enumerated (system_profiler unavailable or returned no SPUSBDataType)]"]
    provider_errors: list[str] = []
    midi: list[str] | None = None
    gp: list[str] | None = None
    cam: list[str] | None = None
    if p.midi_port_names is not None:
        midi, err = _safely_value(p.midi_port_names, "midi_port_names", None)
        if err is not None:
            provider_errors.append(err)
    if p.gamepad_names is not None:
        gp, err = _safely_value(p.gamepad_names, "gamepad_names", None)
        if err is not None:
            provider_errors.append(err)
    if p.camera_names is not None:
        cam, err = _safely_value(p.camera_names, "camera_names", None)
        if err is not None:
            provider_errors.append(err)
    rows = render_usb_table(devices, midi_ports=midi, gamepads=gp, cameras=cam)
    for sentinel in provider_errors:
        rows.append(f"  {sentinel}")
    return rows


# ---------------------------------------------------------------------------
# Section F – recent I/O activity
# ---------------------------------------------------------------------------


def collect_recent_io(p: DiagnosticsProviders) -> list[str]:
    rows: list[str] = []
    if p.recent_osc_sends is not None:
        osc_unavailable = False
        try:
            entries = p.recent_osc_sends() or []
        except Exception as exc:  # noqa: BLE001
            entries = []
            rows.append(f"  OSC sends:        [unavailable: {exc!r}]")
            osc_unavailable = True
        if entries:
            rows.append(f"  OSC sends ({len(entries)} most recent):")
            for e in entries:
                rows.append(
                    f"    +{e.get('age_s', 0):.2f}s  {e.get('status', '?'):<8} "
                    f"{e.get('address', '?')} {e.get('args', ())}"
                )
        elif not osc_unavailable:
            rows.append("  OSC sends:        (no events recorded since process start)")
    else:
        rows.append("  OSC sends:        [not wired]")
    rows.append("  OSC receives:     [not applicable: OpenFollow has no OSC input path]")
    if p.recent_midi_events is not None:
        midi_unavailable = False
        try:
            events = p.recent_midi_events() or []
        except Exception as exc:  # noqa: BLE001
            events = []
            rows.append(f"  MIDI received:    [unavailable: {exc!r}]")
            midi_unavailable = True
        if events:
            rows.append(f"  MIDI received ({len(events)} most recent):")
            for ev in events:
                # ``number`` is None for program_change / channel_pressure;
                # render it as "-" (``dict.get`` default doesn't fire – the
                # key is present with a None value).
                num = ev.get("number")
                rows.append(
                    f"    +{ev.get('age_s', 0):.2f}s  patch={ev.get('patch_id', '?')} "
                    f"type={ev.get('type', '?')} ch={ev.get('channel', '?')} "
                    f"num={'-' if num is None else num} val={ev.get('value', '?')}"
                )
        elif not midi_unavailable:
            rows.append("  MIDI received:    (no events recorded since process start)")
    else:
        rows.append("  MIDI received:    [not wired]")
    rows.append("  MIDI sent:        [not applicable: OpenFollow has no MIDI output path today]")
    return rows


# ---------------------------------------------------------------------------
# Section E9 – gamepad controllers (live SDL view)
# ---------------------------------------------------------------------------


def collect_gamepad_runtime(p: DiagnosticsProviders) -> list[str]:
    if p.gamepad_runtime is None:
        return ["  [not applicable: gamepad provider not wired]"]
    pads, err = _safely_value(p.gamepad_runtime, "gamepad_runtime", [])
    if err is not None:
        return [f"  {err}"]
    if not pads:
        return ["  [no controllers connected]"]
    rows: list[str] = []
    for pad in pads:
        rows.append(f"  [{pad.get('index')}] {pad.get('name', '') or '(unnamed)'}")
        rows.append(f"      guid:    {pad.get('guid', '') or '(none)'}")
        if pad.get("is_game_controller"):
            mode = "X-input (SDL game controller – preferred)"
        else:
            mode = "raw-joystick fallback – NOT recognised as X-input"
        rows.append(f"      backend: {pad.get('backend', '')} ({mode})")
        rows.append(
            f"      layout:  axes={pad.get('num_axes')} buttons={pad.get('num_buttons')} hats={pad.get('num_hats')}"
        )
        if not pad.get("calibration_stored"):
            rows.append("      calibration: none saved (using SDL defaults)")
        elif pad.get("matches_calibration"):
            rows.append("      calibration: matches saved mapping")
        else:
            rows.append(
                "      calibration: *** MISMATCH – saved mapping was captured "
                "on a different controller or hardware mode; re-run the "
                "button-detection wizard ***"
            )
    return rows


# ---------------------------------------------------------------------------
# Section G – device permissions / privilege broker state
# ---------------------------------------------------------------------------


def collect_device_permissions(p: DiagnosticsProviders) -> list[str]:
    if p.privilege_states is None:
        return ["  [not applicable: privilege broker not wired]"]
    states, err = _safely_value(p.privilege_states, "privilege_states", {})
    if err is not None:
        return [f"  {err}"]
    if not states:
        return ["  [no capabilities reported]"]
    rows: list[str] = []
    counts = {"passwordless": 0, "needs_password": 0, "unavailable": 0}
    for value in states.values():
        if value in counts:
            counts[value] += 1
    rows.append(
        f"  summary:           {counts['passwordless']} passwordless, "
        f"{counts['needs_password']} needs password, "
        f"{counts['unavailable']} unavailable"
    )
    rows.append("")
    # Sorted for deterministic output; long capability names align
    # cleanly to the longest name + 2 spaces.
    name_width = max(len(name) for name in states) + 2
    for name in sorted(states):
        rows.append(f"  {name:<{name_width}}{states[name]}")
    return rows


# ---------------------------------------------------------------------------
# Bundle assembly
# ---------------------------------------------------------------------------


@dataclass
class DiagnosticsBundle:
    """Sectioned bundle ready for text rendering.

    Each field holds the output of one section's collector – a list of
    pre-formatted lines kept separate so the formatter can insert
    headers and blank lines uniformly.
    """

    generated_at: str = ""
    host_label: str = ""
    service_status: str = ""
    redactions_applied: str = ""
    a_service: list[str] = field(default_factory=list)
    a2_osc_multicast: list[str] = field(default_factory=list)
    b_discovery: list[str] = field(default_factory=list)
    c_config: list[str] = field(default_factory=list)
    d_failures: list[str] = field(default_factory=list)
    e1_runtime: list[str] = field(default_factory=list)
    e2_detection: list[str] = field(default_factory=list)
    e3_os: list[str] = field(default_factory=list)
    e4_cpu: list[str] = field(default_factory=list)
    e5_memdisk: list[str] = field(default_factory=list)
    e5b_storage: list[str] = field(default_factory=list)
    e6_health: list[str] = field(default_factory=list)
    e7_net: list[str] = field(default_factory=list)
    e8_usb: list[str] = field(default_factory=list)
    e9_gamepad: list[str] = field(default_factory=list)
    f_io: list[str] = field(default_factory=list)
    g_permissions: list[str] = field(default_factory=list)


def collect_bundle(
    providers: DiagnosticsProviders | None = None,
    *,
    log_ring: RingBufferLogHandler | None = None,
    update_service_name: str | None = None,
    repo_root: Path | None = None,
    extra_storage_paths: list[Path] | None = None,
) -> DiagnosticsBundle:
    """Run every collector and pack the result into a
    :class:`DiagnosticsBundle`. ``providers`` may be ``None`` for
    test contexts that only want the host-side environment
    sections; the runtime sections then degrade to ``[not
    applicable: …]`` rather than crashing.

    ``extra_storage_paths`` are extra directories / mount points to
    size in the storage breakdown and report disk usage for – the
    route layer passes the operator's configured detection
    ``storage_path`` so its footprint shows up alongside the SD card."""
    p = providers or DiagnosticsProviders()

    def log_collector_fn() -> tuple[str, list[str]]:
        return collect_log_tail(
            log_ring,
            update_service_name=update_service_name,
            last_n=_BUNDLE_LOG_TAIL_LINES,
        )

    def failure_collector_fn() -> tuple[str, list[str]]:
        return collect_failure_extract(log_ring, update_service_name=update_service_name)

    return DiagnosticsBundle(
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        host_label=f"{platform.node()} ({platform.platform()})",
        service_status=("running" if p.web_port_configured is not None else "NOT RUNNING (sample)"),
        redactions_applied="web_pin=***, X-Auth-Signature stripped",
        a_service=collect_service(p),
        a2_osc_multicast=collect_osc_multicast(p),
        b_discovery=collect_discovery(p),
        c_config=collect_config(p),
        d_failures=collect_recent_failures(p, log_collector_fn, failure_collector_fn),
        e1_runtime=collect_runtime_versions(repo_root),
        e2_detection=collect_detection_stack(),
        e3_os=collect_os(),
        e4_cpu=collect_cpu(),
        e5_memdisk=collect_memory_disk(extra_paths=extra_storage_paths),
        e5b_storage=collect_storage_breakdown(
            repo_root=repo_root,
            extra_paths=extra_storage_paths,
        ),
        e6_health=collect_system_health(),
        e7_net=collect_network_interfaces(),
        e8_usb=collect_usb(p),
        e9_gamepad=collect_gamepad_runtime(p),
        f_io=collect_recent_io(p),
        g_permissions=collect_device_permissions(p),
    )


# Section header rendering – table laid out so :func:`format_bundle`
# can iterate uniformly. Each entry is ``(header, attribute name)``.
_BUNDLE_SECTIONS: tuple[tuple[str, str], ...] = (
    ("A. Service / port", "a_service"),
    ("A2. OSC multicast group status", "a2_osc_multicast"),
    ("B. Discovery / peers", "b_discovery"),
    ("C. Effective config", "c_config"),
    ("D. Recent failures", "d_failures"),
    ("E1. Runtime / versions", "e1_runtime"),
    ("E2. Person detection stack", "e2_detection"),
    ("E3. Operating system", "e3_os"),
    ("E4. CPU", "e4_cpu"),
    ("E5. Memory + disk", "e5_memdisk"),
    ("E5b. Storage breakdown", "e5b_storage"),
    ("E6. System health", "e6_health"),
    ("E7. Network interfaces", "e7_net"),
    ("E8. USB devices", "e8_usb"),
    ("E9. Gamepad controllers", "e9_gamepad"),
    ("F. Recent I/O activity", "f_io"),
    ("G. Device permissions", "g_permissions"),
)


def format_bundle(bundle: DiagnosticsBundle) -> str:
    """Render a :class:`DiagnosticsBundle` as the bundle text the
    operator pastes / attaches. UTF-8 by construction (every input
    is a Python ``str``)."""
    out: list[str] = [
        "openfollow diagnostics bundle",
        f"generated: {bundle.generated_at}",
        f"host: {bundle.host_label}",
        f"service status: {bundle.service_status}",
        f"redactions: {bundle.redactions_applied}",
    ]
    for header, attr in _BUNDLE_SECTIONS:
        out.append("")
        out.append(f"=== {header} ===")
        out.extend(getattr(bundle, attr) or ["  [empty]"])
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# On-disk writer with bounded retention
# ---------------------------------------------------------------------------


# Filename shape: ``openfollow-diagnostics-<sanitised-name>-
# <utc-timestamp>.txt``. Underscores in the system name go through
# verbatim; everything outside [A-Za-z0-9._-] is replaced with ``_``
# so a name with spaces / slashes lands cleanly.
_NAME_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]")


def _sanitise_name(name: str) -> str:
    cleaned = _NAME_SAFE_RE.sub("_", name).strip("._-")
    return cleaned or "openfollow"


def bundle_filename(system_name: str, ts: datetime) -> str:
    """Return the canonical filename for a diagnostics bundle.

    ``system_name`` is sanitised to ``[A-Za-z0-9._-]`` to prevent
    breaking header quoting or the on-disk filename.
    """
    name = _sanitise_name(system_name)
    return f"openfollow-diagnostics-{name}-{ts.strftime('%Y%m%dT%H%M%SZ')}.txt"


def default_disk_root() -> Path:
    """Return the preferred on-disk bundle directory.

    Tries ``/var/log/openfollow`` first (systemd / production
    install), falls back to ``~/.openfollow/diagnostics`` (dev /
    macOS / per-user install). The fallback is silent – most
    operators won't have ``/var/log/openfollow`` writable, and the
    bundle is a nice-to-have second copy, not a hard requirement.
    """
    primary = Path("/var/log/openfollow")
    if primary.exists() and os.access(primary, os.W_OK):
        return primary
    return Path.home() / ".openfollow" / "diagnostics"


def write_bundle_to_disk(
    text: str,
    *,
    system_name: str = "openfollow",
    root: Path | None = None,
    retention: int = 10,
) -> Path | None:
    """Write the bundle to disk and prune older copies to retention limit.

    Returns the written path on success, None on failure (write errors are
    logged but never raised). Retention prune is per-system to support
    multi-system installs with different hostnames.
    """
    root = root or default_disk_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("diagnostics: cannot create %s (%s)", root, exc)
        return None
    fname = bundle_filename(system_name, datetime.now(timezone.utc))
    path = root / fname
    try:
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        logger.warning("diagnostics: cannot write %s (%s)", path, exc)
        return None
    _prune_old_bundles(root, system_name, retention)
    return path


def _prune_old_bundles(root: Path, system_name: str, retention: int) -> None:
    prefix = f"openfollow-diagnostics-{_sanitise_name(system_name)}-"
    try:
        candidates = sorted(
            (p for p in root.iterdir() if p.name.startswith(prefix) and p.suffix == ".txt"),
            key=lambda p: p.name,  # filename includes UTC timestamp; sort is monotone
        )
    except OSError:
        return
    if retention < 0:
        retention = 0
    excess = len(candidates) - retention
    for p in candidates[: max(0, excess)]:
        try:
            p.unlink()
        except OSError:
            # Best-effort – a stale read-only bundle shouldn't
            # turn the next bundle's write into a failure.
            pass
