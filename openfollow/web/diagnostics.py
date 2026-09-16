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
import ipaddress
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

import openfollow
from openfollow.logging_setup import RingBufferLogHandler
from openfollow.uri_redaction import redact_uri, redact_uris_in_text

logger = logging.getLogger(__name__)

# The kernel's IPv4 route table. Read directly rather than asked of the
# network backend: the backend reports what is *configured*, and "can this
# station reach that address" is a question about what the kernel will do
# with the packet.
_PROC_NET_ROUTE = Path("/proc/net/route")

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

    The bundle's outer deadline is checked between sections, so it cannot unwedge
    a stat that hangs *inside* one: on a stale mount that would block the WSGI
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


# Default per-subprocess timeout. Individual callers override as needed;
# ``_BUNDLE_BUDGET_S`` bounds what their sum can cost one request.
_DEFAULT_SUBPROCESS_TIMEOUT_S = 5.0

# system_profiler is slow; use a longer timeout to allow it to complete.
_PROFILER_TIMEOUT_S = 8.0

# The 24h ``journalctl --grep`` failure extract is a full scan of the day's
# journal – the slowest journald call in the bundle. Sized well above the
# 5 s default so a busy host's history isn't truncated to the in-process
# ring (the whole point of the extract is the longer window).
_FAILURE_EXTRACT_TIMEOUT_S = 12.0

# E1 runs up to five probes on a source checkout (three ``git`` calls,
# ``gst-launch-1.0``, ``pkg-config``), so it carries a section deadline of its
# own. Without one its overshoot past any outer budget would be five times the
# default cap rather than one probe's.
_RUNTIME_SECTION_BUDGET_S = 10.0

# Whole-assembly wall-clock budget. The caps above bound each probe, but an
# operator's browser (or a reverse proxy in front of it) waits on their sum,
# which no single cap constrains. ``collect_bundle`` checks this deadline
# between sections and lists the ones it never reaches as skipped. Every
# section that runs more than one probe – D, E1, E5b – takes the remaining
# budget explicitly, so the section running when the deadline passes overshoots
# by one probe's cap (+8 s where ``system_profiler`` runs, +5 s elsewhere) and
# not by the sum of its probes.
_BUNDLE_BUDGET_S = 20.0

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

    # Live runtime telemetry - the same snapshot ``/api/stats`` and the
    # Statistics page read. The bundle carried host, config and logs but
    # nothing about whether the app was *working*, so triage needed a second
    # artefact nobody thinks to attach.
    runtime_stats: Callable[[], dict[str, Any]] | None = None

    # Passive uplink observation: what the online-sync worker's own NTP and
    # GitHub attempts already learned. Reports, never probes - so it adds no
    # outbound path of its own.
    online_sync_status: Callable[[], dict[str, Any]] | None = None

    # The remote host the active video input dials, as
    # ``{"host", "port", "connection_oriented", "source_type"}`` - or ``None``
    # when this input dials nothing (a local camera, a listener, discovery).
    source_endpoint: Callable[[], dict[str, Any] | None] | None = None

    # Absolute paths of the files the station's configuration is read from.
    config_file_paths: Callable[[], list[str]] | None = None
    # The resolved ``<storage>/models`` directory detection loads from.
    detection_models_dir: Callable[[], str] | None = None

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


# Elements no single input plugin owns, each named with what stops working
# when it is missing. Universally present ones (queue, videoconvert) are left
# out: listing what cannot plausibly be absent buries what can.
_SHARED_GST_ELEMENTS: tuple[tuple[str, str], ...] = (
    ("gtksink", "video output"),
    ("appsink", "detection, preview and wizard snapshots"),
    ("valve", "snapshot gating"),
    ("decodebin", "SRT / RTSP / RTP decode"),
    ("rtpjitterbuffer", "RTP input"),
    ("jpegenc", "wizard snapshots and the web preview"),
    ("imagefreeze", "still images in the Media Gallery"),
    ("webpdec", "WebP media in the Media Gallery"),
    ("videotestsrc", "the No Signal placeholder"),
)

# Decoders, in the order the pipeline prefers them. Absence is not a fault -
# which one is present is the answer to "why is this Pi dropping frames".
_DECODER_ELEMENTS: tuple[str, ...] = ("v4l2h264dec", "v4l2h265dec", "avdec_h264", "openh264dec")

# ``vcgencmd get_throttled`` bit meanings. The low bits are live, the high
# ones latch since boot - an operator chasing an intermittent freeze needs the
# latched ones, which is the whole reason to read this.
_THROTTLE_BITS: tuple[tuple[int, str], ...] = (
    (0, "under-voltage NOW"),
    (1, "ARM frequency capped NOW"),
    (2, "currently throttled"),
    (3, "soft temperature limit active"),
    (16, "under-voltage has occurred since boot"),
    (17, "ARM frequency capping has occurred since boot"),
    (18, "throttling has occurred since boot"),
    (19, "soft temperature limit has occurred since boot"),
)


def describe_file(path: Path) -> str:
    """``<size> B, modified <when>`` for a file, or why it cannot be read."""
    try:
        stat = path.stat()
    except OSError as exc:
        return f"[unavailable: {exc.strerror or exc}]"
    when = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(timespec="seconds")
    return f"{stat.st_size} B, modified {when}"


def decode_throttled(raw: str) -> str:
    """Render a ``vcgencmd get_throttled`` reading as the flags it stands for.

    ``throttled=0x50005`` is not something an operator reads, and it is the
    answer to the freezes and dropped USB devices that an undervolted supply
    causes.
    """
    _, _, value = raw.strip().partition("=")
    try:
        bits = int(value, 16)
    except ValueError:
        return f"[unavailable: unrecognised reading {raw.strip()!r}]"
    if bits == 0:
        return "none (no under-voltage or throttling, now or since boot)"
    flags = [label for bit, label in _THROTTLE_BITS if bits & (1 << bit)]
    return f"{value} - {'; '.join(flags)}" if flags else f"{value} - no known flag set"


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


def _fmt_resolution(res: Any) -> str:
    """``WxH``, or a dash when nothing has been negotiated yet."""
    if not isinstance(res, dict):
        return "-"
    width, height = int(res.get("width", 0) or 0), int(res.get("height", 0) or 0)
    return f"{width}x{height}" if width > 0 and height > 0 else "- (no source frames yet)"


def collect_runtime_state(p: DiagnosticsProviders) -> list[str]:
    """Video, frame loop, detection and controllers, from the live snapshot.

    Every value here is read through ``.get`` with a default: this is a live
    dict from a running provider, not a fixed schema, and a bundle that aborts
    because one key moved is worse than one reporting a blank field.
    """
    if p.runtime_stats is None:
        return ["  [not applicable: runtime stats provider not wired]"]
    stats, err = _safely_value(p.runtime_stats, "runtime_stats", {})
    if err is not None:
        return [f"  {err}"]
    stats = stats or {}
    rows: list[str] = []

    video = stats.get("video") or {}
    # Defence in depth: plugin labels and GStreamer error text are expected to
    # arrive redacted, but this is the artefact operators attach to public
    # issues, so a plugin that forgets must not be the leak.
    label = redact_uris_in_text(str(video.get("source_label") or video.get("source_type") or "-"))
    rows.append("  Video:")
    rows.append(f"    source                {video.get('source_type', '?')} ({label})")
    attempt = int(video.get("reconnect_attempt", 0) or 0)
    signal = f"{video.get('pipeline_state', '?')} (connected={bool(video.get('connected'))})"
    rows.append(f"    signal                {signal}{f', reconnect attempt {attempt}' if attempt else ''}")
    error = redact_uris_in_text(str(video.get("error_message") or ""))
    rows.append(f"    last error            {error or '(none)'}")
    rows.append(f"    input resolution      {_fmt_resolution(video.get('resolution'))}")
    rows.append(f"    source framerate      {float(video.get('source_fps', 0.0) or 0.0):.1f} fps")

    playback = stats.get("playback") or {}
    stale_after = float(playback.get("stale_after_s", 0.0) or 0.0)
    age = playback.get("seconds_since_last_frame")
    if playback.get("stalled") or (isinstance(age, (int, float)) and stale_after and age >= stale_after):
        clock = f"STALLED for {float(age):.1f}s" if isinstance(age, (int, float)) else "STALLED"
    elif age is None:
        clock = "starting (no frame completed yet)"
    else:
        clock = f"running (last frame {float(age):.2f}s ago)"
    rows.append("  Frame loop:")
    rows.append(f"    state                 {clock}")
    rows.append(f"    frames total          {playback.get('frame_count_total', 0)}")
    rows.append(
        f"    effective fps         {float(playback.get('effective_fps', 0.0) or 0.0):.1f} "
        f"(recent {float(playback.get('recent_effective_fps', 0.0) or 0.0):.1f}), "
        f"slow {float(playback.get('recent_slow_frame_percent', 0.0) or 0.0):.1f}%"
    )

    tracking = stats.get("tracking") or {}
    rows.append("  Person detection:")
    rows.append(
        f"    state                 enabled={bool(tracking.get('enabled'))} "
        f"available={bool(tracking.get('available'))} running={bool(tracking.get('running'))} "
        f"model={tracking.get('model', '?')}"
    )
    rows.append(
        f"    inference             {tracking.get('inference_count', 0)} runs at "
        f"{float(tracking.get('inference_hz', 0.0) or 0.0):.1f} Hz, "
        f"avg {float(tracking.get('inference_avg_ms', 0.0) or 0.0):.1f} ms, "
        f"errors {tracking.get('inference_errors', 0)}"
    )
    missing = tracking.get("missing_deps") or []
    rows.append(f"    missing deps          {', '.join(missing) if missing else '(none)'}")

    controllers = stats.get("controllers") or {}
    rows.append(
        f"  Controllers:            {controllers.get('connected_count', 0)} connected, "
        f"{controllers.get('mapped_count', 0)} mapped to a marker"
    )
    system = stats.get("system") or {}
    out_res = system.get("output_resolution")
    rows.append(f"  Output resolution:      {_fmt_resolution(out_res) if out_res else '- (no canvas)'}")
    return rows


def _fmt_age(age: Any) -> str:
    """``42 s ago`` for a float age, ``never`` for ``None``."""
    if not isinstance(age, (int, float)):
        return "never"
    return f"{float(age):.0f} s ago"


def _uplink_target_rows(label: str, target: dict[str, Any]) -> list[str]:
    enabled = "enabled" if target.get("enabled") else "disabled"
    name = str(target.get("target") or "-")
    detail = str(target.get("detail") or "")
    outcome = str(target.get("outcome") or "never attempted")
    age = _fmt_age(target.get("age_s"))
    second = f"{outcome} {age}" if target.get("age_s") is not None else outcome
    return [
        f"  {label:<20}{enabled}, {name}",
        f"  {'':<20}{second}{f' - {detail}' if detail else ''}",
    ]


def collect_uplink(p: DiagnosticsProviders) -> list[str]:
    """Whether this station has internet, from attempts it already made.

    Reuses the online-sync worker's own NTP and GitHub release checks rather
    than probing: those run on startup, on IP change and on a timer anyway, so
    the answer costs nothing and the bundle stays free of outbound traffic.

    Staleness is self-limiting in the direction that matters. A station that
    has never reached the network retries every few minutes, so its reading is
    always fresh; only a station that *did* have an uplink can show an old
    "reached", and the age is printed beside it either way.
    """
    if p.online_sync_status is None:
        return ["  [not applicable: online-sync worker not wired]"]
    status, err = _safely_value(p.online_sync_status, "online_sync_status", {})
    if err is not None:
        return [f"  {err}"]
    status = status or {}
    if not status:
        return ["  [not applicable: online-sync worker not running]"]

    time_sync = status.get("time_sync") or {}
    update_check = status.get("update_check") or {}
    attempted = any((t.get("age_s") is not None) for t in (time_sync, update_check))
    if not attempted:
        disabled = not (time_sync.get("enabled") or update_check.get("enabled"))
        # No observation is not the same as no uplink, and saying "offline"
        # here would be an outright guess about a network nobody asked about.
        verdict = "not observed (both checks disabled)" if disabled else "not observed yet"
    elif status.get("online"):
        verdict = "internet reachable"
    else:
        verdict = "no uplink observed"

    cadence = float(status.get("cadence_s", 0.0) or 0.0)
    if status.get("online"):
        cadence_note = "periodic backstop"
    elif not status.get("cycles"):
        # Distinct from a failed attempt: during the startup delay the worker
        # has simply not run yet, and saying it failed to reach anything would
        # be an accusation the record does not support.
        cadence_note = "retry - no cycle has run yet"
    else:
        cadence_note = "retry - no cycle has reached the network"
    reason = str(status.get("last_reason") or "-")
    rows = [
        f"  Verdict:            {verdict}",
        f"  Cadence:            every {cadence:.0f} s ({cadence_note})",
        f"  Last cycle:         {_fmt_age(status.get('last_cycle_age_s'))} "
        f"(reason: {reason}, cycle {status.get('cycles', 0)})",
    ]
    rows.extend(_uplink_target_rows("Time sync:", time_sync))
    rows.extend(_uplink_target_rows("Update check:", update_check))
    return rows


# Bounded because both run inside a bundle download. ``getaddrinfo`` takes no
# timeout argument, so a LAN with no resolver hangs it indefinitely - it goes on
# a daemon thread we stop waiting for. Together they cap the section at ~2.5 s.
_DNS_TIMEOUT_S = 1.0
_CONNECT_TIMEOUT_S = 1.5
# Resolver workers that may still be running after we stopped waiting. Two is
# enough that a bundle download never queues behind itself, and small enough
# that a resolver-less LAN cannot accumulate threads across downloads.
_MAX_INFLIGHT_DNS = 2
_dns_slots = threading.BoundedSemaphore(_MAX_INFLIGHT_DNS)


def resolve_host_bounded(host: str, timeout_s: float = _DNS_TIMEOUT_S) -> tuple[str | None, str]:
    """``(address, note)`` for a host, without ever blocking indefinitely."""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return host, ""

    # Giving up on a lookup does not stop it: the thread runs on until the
    # resolver answers or the process exits. On the very LAN this bounding
    # exists for - one with no reachable resolver - repeated bundle downloads
    # would otherwise pile up a thread apiece. A slot is held by the worker,
    # not by us, and released when it finally returns.
    if not _dns_slots.acquire(blocking=False):
        return None, "DNS lookup skipped (an earlier lookup has not returned)"

    resolved: list[str] = []

    def _lookup() -> None:
        try:
            # Both families: a camera on an AAAA-only name resolves, and the
            # analysis below answers for either.
            infos = socket.getaddrinfo(host, None)
            if infos:
                resolved.append(str(infos[0][4][0]))
        except OSError:
            pass
        finally:
            _dns_slots.release()

    worker = threading.Thread(target=_lookup, daemon=True, name="diag-dns")
    worker.start()
    worker.join(timeout_s)
    if worker.is_alive():
        return None, f"DNS lookup timed out after {timeout_s:.1f} s"
    if not resolved:
        return None, "DNS lookup failed (name does not resolve here)"
    return resolved[0], f"resolves to {resolved[0]}"


def _on_link_interfaces(target: ipaddress.IPv4Address | ipaddress.IPv6Address) -> list[str] | None:
    """Interfaces whose own subnet contains ``target``; ``None`` if unreadable.

    Matched within the target's own address family - an IPv4 target against a
    v6 interface is not merely a non-match, it is a different question.
    """
    import psutil  # noqa: PLC0415

    family = socket.AF_INET if target.version == 4 else socket.AF_INET6
    try:
        addrs = psutil.net_if_addrs()
    except Exception:  # noqa: BLE001
        return None
    found: list[str] = []
    for nic, entries in addrs.items():
        for entry in entries:
            if entry.family != family or not entry.netmask:
                continue
            prefix = netmask_prefix_len(entry.netmask)
            if prefix is None:
                continue
            # A link-local v6 address carries a %scope suffix that is not part
            # of the address.
            address = entry.address.split("%", 1)[0]
            try:
                network = ipaddress.ip_network(f"{address}/{prefix}", strict=False)
            except ValueError:
                continue
            if target in network:
                found.append(f"{nic} {address}/{prefix}")
    return found


def describe_address_reachability(address: str, route_path: Path | None = None) -> list[str]:
    """Whether a packet to ``address`` has anywhere to go from this station.

    Pure local computation - interface addresses and the kernel route table.
    This is the part that answers the question a bundle could not: an address
    on no local subnet, with no route that covers it, is unreachable no matter
    what the camera is doing.
    """
    indent = f"  {'':<20}"
    try:
        target = ipaddress.ip_address(address)
    except ValueError:
        return [f"{indent}[unavailable: {address!r} is not an IPv4/IPv6 address]"]

    on_link = _on_link_interfaces(target)
    if on_link is None:
        return [f"{indent}[unavailable: interface addresses could not be read]"]
    if on_link:
        return [f"{indent}on-link via {', '.join(on_link)}"]

    if target.version == 6:
        # The kernel's v6 table lives elsewhere and in another format. Saying
        # so is the honest answer; running the v4 analysis over a v6 target
        # would print a verdict about an unrelated table.
        return [f"{indent}not on any local IPv6 subnet; IPv6 routing is not analysed"]

    routes = read_routes(route_path)
    if routes is None:
        return [f"{indent}not on any local subnet; routing unknown (kernel route table unreadable)"]
    # Longest prefix wins, exactly as the kernel picks: a station can hold a
    # route to the camera's network and no default route at all, and reporting
    # only the default would call that unreachable.
    matches = [route for route in routes if target in route[1]]
    if not matches:
        return [f"{indent}NOT on any local subnet, and no route covers it"]
    # The kernel picks the longest prefix, then the lowest metric. Ignoring
    # the metric names whichever route the table happened to list first, which
    # on a multi-homed station is the wrong interface as often as not.
    iface, network, gateway, metric = min(matches, key=lambda route: (-route[1].prefixlen, route[3]))
    if gateway == "0.0.0.0":  # noqa: S104 - comparison, not a bind
        return [f"{indent}not on this station's own subnet, but {network} is directly connected on {iface}"]
    return [f"{indent}not on any local subnet; routed via {gateway} on {iface} (route {network}, metric {metric})"]


def probe_tcp_connect(address: str, port: int, timeout_s: float = _CONNECT_TIMEOUT_S) -> str:
    """One bounded TCP connect. Never raises; the outcome is the return value."""
    try:
        with socket.create_connection((address, port), timeout=timeout_s):
            return f"connected in under {timeout_s:.1f} s"
    except TimeoutError:
        return f"no response within {timeout_s:.1f} s (unreachable or filtered)"
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"[:160]


def collect_source_reachability(p: DiagnosticsProviders) -> list[str]:
    """Can this station reach the host its video input is pointed at?

    The question behind most "it works in VLC" reports, and the one a bundle
    could not answer: VLC runs on a laptop that sits on the camera's network,
    and the station often does not.
    """
    if p.source_endpoint is None:
        return ["  [not applicable: video input provider not wired]"]
    endpoint, err = _safely_value(p.source_endpoint, "source_endpoint", None)
    if err is not None:
        return [f"  {err}"]
    if not endpoint:
        return ["  [not applicable: this video input dials no remote host]"]

    host = str(endpoint.get("host", ""))
    port = int(endpoint.get("port", 0) or 0)
    source_type = str(endpoint.get("source_type", "?"))
    problem = str(endpoint.get("problem", ""))
    if problem:
        # The configured source cannot be dialled as written. Probing a
        # substituted default would report on an endpoint the pipeline never
        # uses, which is the failure this whole section exists to prevent.
        return [f"  Configured source:  {source_type} -> {host or '(none)'}", f"  {'':<20}UNUSABLE: {problem}"]
    rows = [f"  Configured source:  {source_type} -> {host}:{port}"]

    address, note = resolve_host_bounded(host)
    if note:
        rows.append(f"  {'':<20}{note}")
    if address is None:
        return rows

    rows.extend(describe_address_reachability(address))

    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:  # pragma: no cover - resolve_host_bounded only returns parseable addresses
        return rows
    if not parsed.is_private:
        rows.append(f"  {'':<20}WARNING: {address} is a public internet address. The probe below")
        rows.append(f"  {'':<20}         leaves the show LAN. No stream data is sent or received.")

    if not endpoint.get("connection_oriented", True):
        rows.append(f"  {'':<20}not probed: this transport rides UDP, where a connect proves nothing")
        return rows
    outcome = probe_tcp_connect(address, port)
    rows.append(f"  {'TCP probe:':<20}{address}:{port} - {outcome}")
    return rows


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


_CONFIG_KEY_LINE_RE = re.compile(r"^(\s*)([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")

# Keys whose value is a credential. Whether one is *set* is the diagnostic –
# it separates "the operator never entered a login" from "the login is wrong" –
# and it is the whole of the useful content, so the value never appears.
_SECRET_CONFIG_KEYS = frozenset({"web_pin", "rtsp_user", "rtsp_password", "srt_passphrase"})

# Keys holding a media URI, which can carry a credential inline: RTSP userinfo
# (``rtsp://user:pass@host/s``) and the SRT ``?passphrase=`` query.
_URI_CONFIG_KEYS = frozenset({"rtsp_url", "srt_host"})


def _redact_toml_uri(raw_value: str) -> str:
    """Redact the URI inside a quoted TOML scalar, keeping its quoting.

    A value in any other shape collapses to ``"***"``. The dump is what
    operators attach to public issue reports, so a form we could not parse has
    to fail closed – printing whatever it holds is the one outcome this
    function exists to prevent.
    """
    text = raw_value.strip()
    for quote in ('"', "'"):
        if len(text) >= 2 and text.startswith(quote) and text.endswith(quote):
            return f"{quote}{redact_uri(text[1:-1])}{quote}"
    return '"***"'


def redact_config_value(key: str, rendered: str) -> str:
    """Redact one already-rendered scalar, keyed by its config field name.

    The line-based :func:`redact_config_secrets` cannot be reused where a
    value is not alone on a TOML line (the defaults diff pairs two of them),
    so both share these key sets and this policy instead of growing a second,
    driftable one.
    """
    if key in _SECRET_CONFIG_KEYS:
        return "(empty)" if rendered.strip() in ('""', "''", "") else "***"
    if key in _URI_CONFIG_KEYS:
        return _redact_toml_uri(rendered)
    return rendered


def redact_config_secrets(toml_text: str) -> str:
    """Strip credentials from a TOML config dump.

    Credential keys collapse to ``"***"`` or ``"(empty)"`` so the bundle still
    answers "is a login configured?"; URI-valued keys keep everything except
    the credential fused into them. Matching is anchored to a whole key so a
    similarly-named one (``web_pin_hint``) is left alone.
    """
    out: list[str] = []
    for line in toml_text.splitlines():
        m = _CONFIG_KEY_LINE_RE.match(line)
        if m is None:
            out.append(line)
            continue
        indent, key, value = m.groups()
        if key in _SECRET_CONFIG_KEYS:
            replacement = '"(empty)"' if value.strip() in ('""', "''", "") else '"***"'
            out.append(f"{indent}{key} = {replacement}")
        elif key in _URI_CONFIG_KEYS:
            out.append(f"{indent}{key} = {_redact_toml_uri(value)}")
        else:
            out.append(line)
    return "\n".join(out)


def _collect_config_provenance(p: DiagnosticsProviders) -> list[str]:
    """Which files this configuration came from, and when they last changed.

    Answers two questions the dump itself cannot: whether a save actually
    landed, and whether the station is still running the image defaults.
    """
    if p.config_file_paths is None:
        return []
    paths, err = _safely_value(p.config_file_paths, "config_file_paths", [])
    if err is not None:
        return ["", f"  Config files: {err}"]
    rows = ["", "  Config files:"]
    for raw in paths or []:
        path = Path(str(raw))
        rows.append(f"    {path.name:<18}{path}")
        rows.append(f"    {'':<18}{describe_file(path)}")
    return rows


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
    rows.append("  ----- begin effective config (credentials redacted) -----")
    for line in text.splitlines():
        rows.append(f"  {line}")
    rows.append("  ----- end effective config -----")
    rows.extend(_collect_config_provenance(p))
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


def redact_log_line(line: str) -> str:
    """Strip HMAC signatures and stream credentials from one log line."""
    return redact_uris_in_text(redact_signatures(line))


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


# Journal noise stripped before two log lines are compared for repetition:
# the ``MMM DD HH:MM:SS`` prefix journalctl adds, the ``[pid]`` of the emitting
# process, and the application's own ``HH:MM:SS``. Everything else - including
# any number that distinguishes one attempt from the next - is significant and
# is left alone, so "attempt 1" never collapses into "attempt 2".
_LOG_DATE_PREFIX_RE = re.compile(r"^[A-Z][a-z]{2} +\d{1,2} +\d{2}:\d{2}:\d{2} +")
_LOG_CLOCK_RE = re.compile(r"\b\d{2}:\d{2}:\d{2}(?:\.\d+)?\b")
_LOG_PID_RE = re.compile(r"\[\d+\]")
_MONTHS = {
    m: i
    for i, m in enumerate(("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), start=1)
}
# Days before the 1st of each month in a non-leap year. journalctl's short
# format carries no year, so a leap day cannot be resolved: Feb 29 and Mar 1
# both land on the same ordinal, which under-reports one gap by a day and can
# never invent one.
_DAYS_BEFORE_MONTH = (0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334)
# Forward step that earns a marker. A station logs continuously, so anything
# past a few hours inside one boot is either a clock correction or a real gap
# in the journal - both worth pointing at, neither worth guessing between.
_CLOCK_JUMP_FORWARD_S = 6 * 3600
# Slack for lines sharing a second, or arriving a beat out of order.
_CLOCK_STEP_BACK_S = 2
# A backwards step this large is the calendar wrapping into a new year, which
# the year-less timestamp format cannot distinguish from a correction. It reads
# forwards to a human (Dec 31 -> Jan 1), so it gets no marker.
_YEAR_WRAP_S = 300 * 86400
# journalctl's own boot separator, e.g. "-- Boot 9b9ec076... --".
_BOOT_MARKER_RE = re.compile(r"^-{2,}\s*Boot\b", re.IGNORECASE)
# Longest repeating cycle the collapser will look for. A reconnect loop is a
# handful of lines; searching further costs more than it saves and risks
# folding together two genuinely different stretches that happen to rhyme.
_MAX_REPEAT_PERIOD = 24


def normalise_log_line(line: str) -> str:
    """A log line reduced to what makes it the *same message* as another."""
    stripped = _LOG_DATE_PREFIX_RE.sub("", line.strip())
    stripped = _LOG_PID_RE.sub("[]", stripped)
    return _LOG_CLOCK_RE.sub("", stripped)


def collapse_repeated_blocks(lines: list[str], *, max_period: int = _MAX_REPEAT_PERIOD) -> list[str]:
    """Fold a repeating run of log lines down to one instance plus a count.

    A failing network source does not repeat one line, it repeats a *cycle* -
    connect, wait, time out, schedule, reconnect - so collapsing only adjacent
    identical lines leaves the tail almost untouched. This finds the shortest
    period that repeats from the current position and keeps the first pass
    verbatim, timestamps included.

    Deliberately no time span in the summary: the window this most often folds
    is the one where the station's clock was corrected mid-log, so any duration
    computed across it would be fiction. The kept pass and the line after the
    fold both carry their real timestamps.
    """
    keys = [normalise_log_line(line) for line in lines]
    out: list[str] = []
    i = 0
    total = len(lines)
    while i < total:
        best_period = 0
        best_repeats = 0
        for period in range(1, min(max_period, (total - i) // 2) + 1):
            repeats = 0
            while (
                i + (repeats + 2) * period <= total
                and keys[i + repeats * period : i + (repeats + 1) * period]
                == keys[i + (repeats + 1) * period : i + (repeats + 2) * period]
            ):
                repeats += 1
            # Prefer the shortest period that repeats at all: a 2-line cycle
            # seen 20 times would also match as a 4-line cycle seen 10 times,
            # and the shorter one is the honest description.
            if repeats > 0:
                best_period, best_repeats = period, repeats
                break
        if best_period == 0:
            out.append(lines[i])
            i += 1
            continue
        out.extend(lines[i : i + best_period])
        noun = "line" if best_period == 1 else f"{best_period}-line block"
        out.append(f"  [... the {noun} above repeated {best_repeats} more time(s), folded]")
        i += best_period * (best_repeats + 1)
    return out


def _log_line_stamp(line: str) -> tuple[int, int, int] | None:
    """``(month, day, seconds-of-day)`` from a journal prefix, else ``None``."""
    m = _LOG_DATE_PREFIX_RE.match(line.strip())
    if m is None:
        return None
    parts = m.group(0).split()
    month = _MONTHS.get(parts[0])
    if month is None:
        return None
    # The regex already pinned day and time to digit groups, so no conversion
    # here can fail - an abbreviation that is not a month name is the only way
    # a matching prefix can still be unreadable.
    day = int(parts[1])
    hh, mm, ss = (int(x) for x in parts[2].split(":"))
    return month, day, hh * 3600 + mm * 60 + ss


def _stamp_seconds(stamp: tuple[int, int, int]) -> int:
    """Seconds since the start of a notional non-leap year."""
    month, day, seconds = stamp
    return (_DAYS_BEFORE_MONTH[month - 1] + day - 1) * 86400 + seconds


def _discontinuity_note(previous: tuple[int, int, int], current: tuple[int, int, int]) -> str | None:
    """The marker for a step between two timestamps, or ``None`` if ordinary."""
    delta = _stamp_seconds(current) - _stamp_seconds(previous)
    if delta <= -_YEAR_WRAP_S:
        return None
    if delta < -_CLOCK_STEP_BACK_S:
        return "  [... timestamps step backwards here - the clock moved, or the log source changed]"
    if delta < _CLOCK_JUMP_FORWARD_S:
        return None
    size = f"~{delta // 86400} day(s)" if delta >= 86400 else f"~{delta // 3600} hour(s)"
    return f"  [... timestamps jump {size} forward here - a clock correction, or a gap in the journal]"


def annotate_log_discontinuities(lines: list[str]) -> list[str]:
    """Mark where the log's own timestamps stop running forwards.

    A station with no RTC boots at whatever the filesystem last recorded and is
    corrected minutes later, so a single boot's journal can jump weeks forward
    in the middle. Without a marker that reads as an out-of-order log and the
    reader silently distrusts the whole section.

    A marker that fired on every midnight would do the same damage in reverse,
    so the step is measured on real day ordinals and has to clear
    ``_CLOCK_JUMP_FORWARD_S``. A reboot is skipped outright: the journal marks
    it, and a station powered off overnight resumes hours later with nothing
    having gone wrong.
    """
    out: list[str] = []
    previous: tuple[int, int, int] | None = None
    across_boot = False
    for line in lines:
        if _BOOT_MARKER_RE.match(line.strip()):
            across_boot = True
        stamp = _log_line_stamp(line)
        if stamp is not None:
            if previous is not None and not across_boot:
                note = _discontinuity_note(previous, stamp)
                if note is not None:
                    out.append(note)
            previous = stamp
            across_boot = False
        out.append(line)
    return out


# Kernel messages worth surfacing beside the application log. The unit's own
# journal cannot show them, so a failing supply, a USB device dropping off the
# bus or the OOM killer reads as an unexplained application fault.
_KERNEL_PATTERNS: tuple[str, ...] = ("Under-voltage", "over-current", "USB disconnect", "Out of memory", "oom-kill")
# Handed to ``journalctl --grep`` so the match happens in the journal rather
# than by buffering a day of kernel messages through this process - a browning
# out Pi logs continuously, and the unfiltered read timed out having produced
# nothing while spending the section's whole budget.
_KERNEL_GREP = "|".join(_KERNEL_PATTERNS)
_KERNEL_EXTRACT_TIMEOUT_S = 6.0
_KERNEL_EXTRACT_MAX_LINES = 40


def collect_kernel_extract(timeout_s: float = _KERNEL_EXTRACT_TIMEOUT_S) -> list[str]:
    """Hardware-level events from the kernel log, filtered and bounded."""
    rc, out = _run(
        [
            "journalctl",
            "-k",
            "--since",
            "-24h",
            "--no-pager",
            "-o",
            "short",
            "--grep",
            _KERNEL_GREP,
            "-n",
            str(_KERNEL_EXTRACT_MAX_LINES * 4),
        ],
        timeout_s=timeout_s,
    )
    if rc != 0:
        return ["", f"  Kernel log (last 24h): {out if out.startswith('[unavailable') else f'[unavailable: {out}]'}"]
    matched = [line for line in out.splitlines() if any(pattern in line for pattern in _KERNEL_PATTERNS)]
    rows = ["", "  Kernel log (last 24h, power / USB / OOM only):"]
    if not matched:
        rows.append("    [none]")
        return rows
    dropped = len(matched) - _KERNEL_EXTRACT_MAX_LINES
    for line in matched[-_KERNEL_EXTRACT_MAX_LINES:]:
        rows.append(f"    {redact_log_line(line)}")
    if dropped > 0:
        rows.append(f"    [... {dropped} earlier matching line(s) not shown]")
    return rows


def collect_recent_failures(
    p: DiagnosticsProviders,
    log_collector: Callable[[], tuple[str, list[str]]],
    failure_collector: Callable[[], tuple[str, list[str]]] | None = None,
    kernel_collector: Callable[[], list[str]] | None = None,
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
        rendered = [f"  {redact_log_line(line)}" for line in log_lines]
        rows.extend(collapse_repeated_blocks(annotate_log_discontinuities(rendered)))
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
                rendered = [f"  {redact_log_line(line)}" for line in failure_lines]
                rows.extend(collapse_repeated_blocks(rendered))
            else:
                rows.append("  [no WARNING/ERROR/CRITICAL lines in window]")
            rows.append("  ----- end failure extract -----")
    rows.extend((kernel_collector or collect_kernel_extract)())
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
                        rows.append(f"    {redact_log_line(tb_line)}")
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
    timeout_s: float = _DEFAULT_SUBPROCESS_TIMEOUT_S,
) -> tuple[str, list[str]]:
    """Read up to ``last_n`` log lines, preferring journalctl and falling back to the ring.

    Returns ``(source_label, lines)`` where the label describes the source.
    """
    if update_service_name:
        rc, out = _run(["journalctl", "-u", update_service_name, "-n", str(last_n)], timeout_s=timeout_s)
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
    timeout_s: float = _FAILURE_EXTRACT_TIMEOUT_S,
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
            timeout_s=timeout_s,
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


def _gtk3_version(timeout_s: float = _DEFAULT_SUBPROCESS_TIMEOUT_S) -> str:
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
        rc, out = _run(["pkg-config", "--modversion", "gtk+-3.0"], timeout_s=timeout_s)
        return out if rc == 0 and out else "[unavailable]"


def collect_runtime_versions(
    repo_root: Path | None = None,
    *,
    budget_s: float = _RUNTIME_SECTION_BUDGET_S,
) -> list[str]:
    deadline = time.monotonic() + budget_s

    def cap() -> float:
        """Per-probe cap clamped to what's left of this section's budget."""
        return max(0.0, min(_DEFAULT_SUBPROCESS_TIMEOUT_S, deadline - time.monotonic()))

    rows = [f"  Python                       {sys.version.split(chr(32), 1)[0]}"]
    if repo_root is not None:
        rc, out = _run(["git", "-C", str(repo_root), "rev-parse", "HEAD"], timeout_s=cap())
        rows.append(f"  OpenFollow git rev           {out if rc == 0 else _git_failure_detail(out)}")
        rc, out = _run(["git", "-C", str(repo_root), "rev-parse", "--abbrev-ref", "HEAD"], timeout_s=cap())
        rows.append(f"  Branch                       {out if rc == 0 else _git_failure_detail(out)}")
        rc, out = _run(["git", "-C", str(repo_root), "status", "--porcelain"], timeout_s=cap())
        if rc == 0:
            rows.append(f"  Working tree                 {'dirty' if out.strip() else 'clean'}")
        else:
            rows.append(f"  Working tree                 {_git_failure_detail(out)}")
    for dist in ("bottle", "pygame", "psutil"):
        rows.append(f"  {dist:<29}{_safe_version(dist)}")
    rc, out = _run(["gst-launch-1.0", "--version"], timeout_s=cap())
    rows.append(f"  GStreamer                    {out.splitlines()[0] if rc == 0 and out else '[unavailable]'}")
    rows.append(f"  GTK 3                        {_gtk3_version(cap())}")
    ndi = "present" if importlib.util.find_spec("NDIlib") else "[not present]"
    rows.append(f"  libndi                       {ndi}")
    rows.append(f"  openfollow package           {_installed_package_version(cap())}")
    return rows


def _normalise_package_version(version: str) -> str:
    """A Debian version reduced to what compares against a PEP 440 one.

    ``build-deb.sh`` rewrites a pre-release for Debian's sort order - ``rc``
    becomes ``~rc``, so the wheel's ``0.4.2rc3`` ships as ``0.4.2~rc3`` - and a
    raw equality then reports every rc build as a mismatch. An epoch is
    Debian's alone and never appears upstream.
    """
    _, _, without_epoch = version.strip().rpartition(":")
    return (without_epoch or version.strip()).replace("~", "").lower()


def _installed_package_version(timeout_s: float) -> str:
    """The installed ``.deb`` version against the code actually running.

    They part company the moment an update installs and the service is not
    restarted, and every other line in the bundle then describes the old
    build while the operator reads the new version number off the release
    notes.
    """
    running = openfollow.__version__
    if shutil.which("dpkg-query") is None:
        return f"running {running} (not a .deb install)"
    rc, out = _run(["dpkg-query", "-W", "-f=${Version}", "openfollow"], timeout_s=timeout_s)
    if rc != 0:
        # ``_run`` folds a missing binary, a timeout and a launch error into
        # one sentinel, and dpkg-query exits non-zero for an unknown package
        # too. Only the last of those means "not a .deb install", so the rest
        # report what went wrong rather than asserting a packaging state.
        if "no packages found" in out.lower():
            return f"running {running} (openfollow is not installed as a .deb)"
        return f"running {running} (installed version unavailable: {out})"
    installed = out.strip()
    if _normalise_package_version(installed) == _normalise_package_version(running):
        return f"{installed} installed, and running"
    return (
        f"MISMATCH: {installed} installed, {running} running - "
        "an update that installed without a service restart looks like this"
    )


def collect_video_capability() -> list[str]:
    """Which video inputs this station can actually offer, and why not.

    The picker hides a backend whose element is missing, so "NDI is not in the
    list" and "NDI is broken" look identical from a screenshot. Each plugin
    already answers this for itself through ``is_available`` - this reports
    what it says rather than keeping a second list that can drift.
    """
    rows: list[str] = []
    try:
        from openfollow.video.inputs import get_registry  # noqa: PLC0415

        registry = get_registry()
    except Exception as exc:  # noqa: BLE001
        return [f"  [unavailable: input registry: {exc!r}]"]

    rows.append("  Video inputs:")
    for input_id, plugin in sorted(registry.items()):
        try:
            available, reason = plugin.is_available()
        except Exception as exc:  # noqa: BLE001
            available, reason = False, f"is_available raised: {exc!r}"
        state = "available" if available else f"unavailable - {reason}"
        rows.append(f"    {input_id:<14}{state}")

    try:
        import gi  # noqa: PLC0415

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst  # noqa: PLC0415

        Gst.init(None)
    except Exception as exc:  # noqa: BLE001
        rows.append(f"  GStreamer elements: [unavailable: {exc!r}]")
        return rows

    rows.append("  Shared GStreamer elements:")
    for name, used_for in _SHARED_GST_ELEMENTS:
        present = "present" if Gst.ElementFactory.find(name) else "MISSING"
        rows.append(f"    {name:<18}{present:<9}({used_for})")
    found = [name for name in _DECODER_ELEMENTS if Gst.ElementFactory.find(name)]
    rows.append(f"  Video decoders:       {', '.join(found) if found else 'none of the expected decoders'}")
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


def collect_detection_stack(p: DiagnosticsProviders | None = None) -> list[str]:
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
    rows.extend(_collect_detection_models(p))
    return rows


def _list_model_files(directory: Path) -> tuple[list[Path], str | None]:
    """``(models, error)`` for a storage directory, never raising."""
    try:
        return sorted(directory.glob("*.onnx")), None
    except OSError as exc:
        return [], str(exc.strerror or exc)


def _collect_detection_models(p: DiagnosticsProviders | None) -> list[str]:
    """The model files actually present in the storage directory.

    The storage breakdown reports that directory's *size*; "detection will not
    start" is a question about which model is in it and whether the configured
    one is among them.
    """
    if p is None or p.detection_models_dir is None:
        return ["  models                       [not applicable: storage provider not wired]"]
    raw, err = _safely_value(p.detection_models_dir, "detection_models_dir", "")
    if err is not None:
        return [f"  models                       {err}"]
    directory = Path(str(raw or ""))
    rows = [f"  models directory             {directory}"]
    # Same hazard the storage section bounds for the same path: an
    # operator-configured storage_path can be a stale NFS/CIFS/USB mount,
    # where a glob blocks the WSGI worker in D-state and repeated downloads
    # take the web UI down with them.
    listed = _bounded_probe(partial(_list_model_files, directory), _STAT_PROBE_TIMEOUT_S, None)
    if listed is None:
        rows.append("  models                       [unavailable: listing timed out (stale mount?)]")
        return rows
    entries, error = listed
    if error is not None:
        rows.append(f"  models                       [unavailable: {error}]")
        return rows
    if not entries:
        rows.append("  models                       [none present]")
        return rows
    for entry in entries:
        rows.append(f"    {entry.name:<27}{describe_file(entry)}")
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
# Poetry venv) can't stall the whole bundle. A shared deadline caps the
# *total* du time across all candidates too, so the worst case is the budget
# – not the unbounded ``len(candidates) * _STORAGE_DU_TIMEOUT_S`` (~48 s on
# a near-full disk, exactly the case this section exists to diagnose).
# ``collect_bundle`` passes a smaller ``budget_s`` when its own deadline
# leaves less than this. Candidates reached after the deadline are listed as
# skipped, not silently dropped.
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
    budget_s: float = _STORAGE_SECTION_BUDGET_S,
) -> list[str]:
    import psutil  # noqa: PLC0415

    rows: list[str] = []
    # Seeded before the mount table, not just the ``du`` walk: a stale mount in
    # the partition table is exactly what this budget exists to survive, and its
    # stat probe blocks for the full cap.
    deadline = time.monotonic() + budget_s

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
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            usage = f"[skipped: storage-section time budget ({budget_s:.0f}s) exhausted]"
        else:
            usage = _bounded_probe(
                partial(_partition_usage, part.mountpoint),
                min(_STAT_PROBE_TIMEOUT_S, remaining),
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
        rows.append(f"    [skipped: {entry} – storage-section time budget ({budget_s:.0f}s) exhausted]")
    return rows


# E6. System health ---------------------------------------------------------


def _collect_throttle_state() -> str:
    """Raspberry Pi under-voltage and throttling flags.

    Temperature and fan speed are already here, and neither shows an
    inadequate supply - which presents as random freezes and USB devices
    dropping out, not as heat.
    """
    rc, out = _run(["vcgencmd", "get_throttled"], timeout_s=2.0)
    if rc != 0:
        return out if out.startswith("[unavailable") else f"[unavailable: {out}]"
    return decode_throttled(out)


def collect_system_health() -> list[str]:
    import psutil  # noqa: PLC0415

    rows: list[str] = []
    bt = psutil.boot_time()
    rows.append(
        f"  boot time (UTC)              {datetime.fromtimestamp(bt, tz=timezone.utc).isoformat(timespec='seconds')}"
    )
    rows.append(f"  uptime                       {(time.time() - bt) / 3600:.1f} h")
    rows.append(f"  throttling                   {_collect_throttle_state()}")
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


def netmask_prefix_len(netmask: str) -> int | None:
    """Prefix length for a netmask of either family, ``None`` if unusable.

    Computed from the packed bytes rather than handed to ``ip_network``:
    that accepts a dotted-quad mask for IPv4 but rejects the colon form
    ``psutil`` reports for IPv6, and reachability has to answer for both.
    A non-contiguous mask is rejected rather than guessed at.
    """
    try:
        packed = ipaddress.ip_address(netmask).packed
    except ValueError:
        return None
    width = len(packed) * 8
    bits = int.from_bytes(packed, "big")
    prefix = bin(bits).count("1")
    if bits != (((1 << prefix) - 1) << (width - prefix)):
        return None
    return prefix


def read_routes(route_path: Path | None = None) -> list[tuple[str, ipaddress.IPv4Network, str, int]] | None:
    """Every IPv4 route as ``(interface, destination network, gateway, metric)``.

    ``None`` means the table could not be read (no ``/proc`` on macOS), which
    the caller reports as unknown - distinct from ``[]``, a host that really
    has nowhere to send a packet. A gateway of ``0.0.0.0`` marks a directly
    connected route.

    Resolved here rather than as a default argument: a default binds the
    module attribute at import, which silently ignores a test (or a future
    caller) that points the module at another table.
    """
    try:
        text = (route_path or _PROC_NET_ROUTE).read_text()
    except OSError:
        return None
    routes: list[tuple[str, ipaddress.IPv4Network, str, int]] = []
    for line in text.splitlines()[1:]:
        fields = line.split()
        if len(fields) < 8:
            continue
        iface, dest_hex, gw_hex, metric_raw, mask_hex = fields[0], fields[1], fields[2], fields[6], fields[7]
        try:
            destination = socket.inet_ntoa(int(dest_hex, 16).to_bytes(4, "little"))
            gateway = socket.inet_ntoa(int(gw_hex, 16).to_bytes(4, "little"))
            mask = socket.inet_ntoa(int(mask_hex, 16).to_bytes(4, "little"))
            network = ipaddress.IPv4Network(f"{destination}/{mask}", strict=False)
            metric = int(metric_raw)
        except (ValueError, OverflowError):
            continue
        routes.append((iface, network, gateway, metric))
    return routes


def read_default_routes(route_path: Path | None = None) -> list[tuple[str, str, int]] | None:
    """``(interface, gateway, metric)`` per IPv4 default route, best first.

    Sorted by metric, the order the kernel would try them: a multi-homed
    station has several and they are not a set of equals.
    """
    routes = read_routes(route_path)
    if routes is None:
        return None
    defaults = [r for r in routes if r[1].prefixlen == 0 and r[2] != "0.0.0.0"]
    return [(iface, gateway, metric) for iface, _net, gateway, metric in sorted(defaults, key=lambda r: r[3])]


def collect_network_interfaces(route_path: Path | None = None) -> list[str]:
    """Interface table plus the addressing a reachability question needs.

    The link fields alone ("eth0 is up at 1000Mb") cannot answer the most
    common support question - why a station does not reach a camera, a console
    or a peer. That needs the prefix, which says whether the target is on-link,
    and the default route, which says whether anything would carry a packet off
    this subnet. Both were absent, so a bundle from a station addressed
    addressed on one subnet, with the camera on another, read as healthy.
    """
    import psutil  # noqa: PLC0415

    rows: list[str] = []
    duplex_label = {0: "unknown", 1: "half", 2: "full"}
    try:
        stats = psutil.net_if_stats()
    except Exception as exc:  # noqa: BLE001
        return [f"  [unavailable: net_if_stats: {exc!r}]"]
    try:
        addrs = psutil.net_if_addrs()
    except Exception as exc:  # noqa: BLE001
        addrs = {}
        rows.append(f"  [addresses unavailable: net_if_addrs: {exc!r}]")
    for nic, st in stats.items():
        rows.append(
            f"  {nic:<14}isup={st.isup} speed={st.speed}Mb mtu={st.mtu} "
            f"duplex={duplex_label.get(int(st.duplex), str(st.duplex))}"
        )
        for addr in addrs.get(nic, ()):
            if addr.family != socket.AF_INET:
                continue
            prefix = netmask_prefix_len(addr.netmask) if addr.netmask else None
            suffix = f"/{prefix}" if prefix is not None else f" netmask={addr.netmask}"
            rows.append(f"  {'':<14}ipv4 {addr.address}{suffix}")
    routes = read_default_routes(route_path)
    if routes is None:
        rows.append("  Default route:  [unavailable: kernel route table not readable]")
    elif not routes:
        rows.append("  Default route:  none (nothing routes off-subnet)")
    else:
        for iface, gateway, metric in routes:
            rows.append(f"  Default route:  via {gateway} on {iface} (metric {metric})")
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
# Bundle identity – release version + platform
# ---------------------------------------------------------------------------


def _platform_arch() -> str:
    """Debian-style architecture label for this host (``arm64`` / ``amd64`` /
    ``armhf`` / ``i386``), falling back to the raw ``platform.machine()``.

    Shares the updater's mapping so a bundle's platform matches the release
    artifact (``openfollow_<version>_<arch>.ofupdate``) the host installs.
    Imported lazily to keep this module's top-level import surface cheap."""
    from openfollow.runtime.deb_update import _deb_arch  # noqa: PLC0415

    return _deb_arch()


def _platform_label() -> str:
    """Architecture label for the bundle header, carrying the raw
    ``platform.machine()`` alongside it when the two differ – so the mapping
    stays lossless and an unmapped machine reads as itself, not twice."""
    arch = _platform_arch()
    machine = platform.machine()
    return f"{arch} ({machine})" if arch != machine else arch


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
    app_version: str = ""
    platform_label: str = ""
    host_label: str = ""
    service_status: str = ""
    redactions_applied: str = ""
    a_service: list[str] = field(default_factory=list)
    a2_osc_multicast: list[str] = field(default_factory=list)
    a3_runtime_state: list[str] = field(default_factory=list)
    a4_uplink: list[str] = field(default_factory=list)
    a5_source_reach: list[str] = field(default_factory=list)
    b_discovery: list[str] = field(default_factory=list)
    c_config: list[str] = field(default_factory=list)
    d_failures: list[str] = field(default_factory=list)
    e1_runtime: list[str] = field(default_factory=list)
    e1b_video: list[str] = field(default_factory=list)
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


# Section table – the one order both :func:`collect_bundle` (assembly, and
# therefore which sections an exhausted budget drops) and :func:`format_bundle`
# (rendering) walk. Each entry is ``(header, attribute name)``.
_BUNDLE_SECTIONS: tuple[tuple[str, str], ...] = (
    ("A. Service / port", "a_service"),
    ("A2. OSC multicast group status", "a2_osc_multicast"),
    ("A3. Runtime state", "a3_runtime_state"),
    ("A4. Uplink status", "a4_uplink"),
    ("A5. Video source reachability", "a5_source_reach"),
    ("B. Discovery / peers", "b_discovery"),
    ("C. Effective config", "c_config"),
    ("D. Recent failures", "d_failures"),
    ("E1. Runtime / versions", "e1_runtime"),
    ("E1b. Video capability", "e1b_video"),
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


def collect_bundle(
    providers: DiagnosticsProviders | None = None,
    *,
    log_ring: RingBufferLogHandler | None = None,
    update_service_name: str | None = None,
    repo_root: Path | None = None,
    extra_storage_paths: list[Path] | None = None,
    budget_s: float | None = None,
) -> DiagnosticsBundle:
    """Run every collector and pack the result into a
    :class:`DiagnosticsBundle`. ``providers`` may be ``None`` for
    test contexts that only want the host-side environment
    sections; the runtime sections then degrade to ``[not
    applicable: …]`` rather than crashing.

    ``extra_storage_paths`` are extra directories / mount points to
    size in the storage breakdown and report disk usage for – the
    route layer passes the operator's configured detection
    ``storage_path`` so its footprint shows up alongside the SD card.

    ``budget_s`` is the whole-assembly wall-clock budget; sections not reached
    before it expires are listed as skipped rather than run. ``None`` reads
    ``_BUNDLE_BUDGET_S`` here rather than binding it as a default argument, so
    the constant stays patchable."""
    p = providers or DiagnosticsProviders()
    budget = _BUNDLE_BUDGET_S if budget_s is None else budget_s
    deadline = time.monotonic() + budget

    def remaining(cap: float) -> float:
        """Section cap clamped to what's left of the bundle budget, floored at
        zero – the deadline can lapse *inside* a section, between the check
        that admitted it and this call (section D's log tail runs first), and a
        negative cap would reach the wire as ``timed out after -2.1s``."""
        return max(0.0, min(cap, deadline - time.monotonic()))

    def log_collector_fn() -> tuple[str, list[str]]:
        return collect_log_tail(
            log_ring,
            update_service_name=update_service_name,
            last_n=_BUNDLE_LOG_TAIL_LINES,
            timeout_s=remaining(_DEFAULT_SUBPROCESS_TIMEOUT_S),
        )

    def failure_collector_fn() -> tuple[str, list[str]]:
        return collect_failure_extract(
            log_ring,
            update_service_name=update_service_name,
            timeout_s=remaining(_FAILURE_EXTRACT_TIMEOUT_S),
        )

    def kernel_collector_fn() -> list[str]:
        # Clamped to what is left of the bundle's budget, like the failure
        # extract beside it: a fixed timeout here pushes the later sections
        # into "[skipped]" on exactly the struggling station whose kernel log
        # is worth reading.
        return collect_kernel_extract(timeout_s=remaining(_KERNEL_EXTRACT_TIMEOUT_S))

    # Every section that runs more than one probe takes the remaining budget
    # explicitly; the single-probe ones are bound by the deadline check alone.
    collectors: dict[str, Callable[[], list[str]]] = {
        "a_service": lambda: collect_service(p),
        "a2_osc_multicast": lambda: collect_osc_multicast(p),
        "a3_runtime_state": lambda: collect_runtime_state(p),
        "a4_uplink": lambda: collect_uplink(p),
        "a5_source_reach": lambda: collect_source_reachability(p),
        "b_discovery": lambda: collect_discovery(p),
        "c_config": lambda: collect_config(p),
        "d_failures": lambda: collect_recent_failures(
            p,
            log_collector_fn,
            failure_collector_fn,
            kernel_collector_fn,
        ),
        "e1_runtime": lambda: collect_runtime_versions(repo_root, budget_s=remaining(_RUNTIME_SECTION_BUDGET_S)),
        "e1b_video": collect_video_capability,
        "e2_detection": lambda: collect_detection_stack(p),
        "e3_os": collect_os,
        "e4_cpu": collect_cpu,
        "e5_memdisk": lambda: collect_memory_disk(extra_paths=extra_storage_paths),
        "e5b_storage": lambda: collect_storage_breakdown(
            repo_root=repo_root,
            extra_paths=extra_storage_paths,
            budget_s=remaining(_STORAGE_SECTION_BUDGET_S),
        ),
        "e6_health": collect_system_health,
        "e7_net": collect_network_interfaces,
        "e8_usb": lambda: collect_usb(p),
        "e9_gamepad": lambda: collect_gamepad_runtime(p),
        "f_io": lambda: collect_recent_io(p),
        "g_permissions": lambda: collect_device_permissions(p),
    }
    bundle = DiagnosticsBundle(
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        app_version=openfollow.__version__,
        platform_label=_platform_label(),
        host_label=f"{platform.node()} ({platform.platform()})",
        service_status=("running" if p.web_port_configured is not None else "NOT RUNNING (sample)"),
        redactions_applied="web_pin=***, X-Auth-Signature stripped",
    )
    # Walked in display order, so an exhausted budget drops the trailing
    # sections – a truncated bundle still reads correctly from the top.
    for header, attr in _BUNDLE_SECTIONS:
        if time.monotonic() >= deadline:
            setattr(bundle, attr, [f"  [skipped: {header} – bundle time budget ({budget:.0f}s) exhausted]"])
            continue
        setattr(bundle, attr, collectors[attr]())
    return bundle


def format_bundle(bundle: DiagnosticsBundle) -> str:
    """Render a :class:`DiagnosticsBundle` as the bundle text the
    operator pastes / attaches. UTF-8 by construction (every input
    is a Python ``str``)."""
    out: list[str] = [
        "openfollow diagnostics bundle",
        # Version + platform lead the header so a bundle is self-identifying
        # in its first lines. Both come from the running package, not the
        # checkout, so a .deb / image install reports them just as a source
        # tree does (E1's git rev needs a ``.git`` and is blank there).
        f"version: {bundle.app_version}",
        f"platform: {bundle.platform_label}",
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
# <utc-timestamp>-<version>-<arch>.txt``. Underscores in the system name go
# through verbatim; everything outside [A-Za-z0-9._-] is replaced with ``_``
# so a name with spaces / slashes lands cleanly.
_NAME_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]")


def _sanitise_name(name: str) -> str:
    cleaned = _NAME_SAFE_RE.sub("_", name).strip("._-")
    return cleaned or "openfollow"


def bundle_filename(system_name: str, ts: datetime) -> str:
    """Return the canonical filename for a diagnostics bundle.

    ``system_name``, the release version, and the architecture label are each
    sanitised to ``[A-Za-z0-9._-]`` to prevent breaking header quoting or the
    on-disk filename (a local version like ``0.0.0+unknown`` carries a ``+``).

    Version and architecture trail the timestamp rather than preceding it:
    :func:`_prune_old_bundles` sorts candidates by filename and relies on that
    sort being chronological, which a leading version string would break
    (``0.10.0`` sorts before ``0.4.0``, so retention would evict the newer
    bundle first).
    """
    name = _sanitise_name(system_name)
    version = _sanitise_name(openfollow.__version__)
    arch = _sanitise_name(_platform_arch())
    return f"openfollow-diagnostics-{name}-{ts.strftime('%Y%m%dT%H%M%SZ')}-{version}-{arch}.txt"


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
