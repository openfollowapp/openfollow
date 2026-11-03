# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Michel Honold and Paul Hermann
"""Diagnostics bundle section E11: GPUs, connectors, the compositor's layout and kanshi.

Three readings, so a support thread can tell an output Cage dropped from one
kanshi left extended, or from a desktop holding the display: what the kernel
sees (``/sys/class/drm``), what Cage holds (wlr-output-management) and what the
session logged. Each part says when it could not be read.
"""

from __future__ import annotations

import os
import re
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path

from openfollow import wayland_outputs
from openfollow.wayland_outputs import OutputHead, OutputMode, WaylandError
from openfollow.web import diagnostics

_DRM_ROOT = Path("/sys/class/drm")
_CARD_RE = re.compile(r"card\d+")
_CONNECTOR_RE = re.compile(r"(card\d+)-(.+)")
_MODES_SHOWN = 6

_LAYOUT_TIMEOUT_S = 2.0
_SYSTEMCTL_TIMEOUT_S = 2.0

# kanshi writes these to stderr, which the unit's journal holds. The wlroots /
# Cage lines are told apart by the ``[file.c:line]`` tag wlr_log puts on every line.
_KANSHI_RE = re.compile(
    r"applying profile|configuration for profile|no profile matched|doesn't support mode"
    r"|wlr-output-management|failed to connect to display"
)
_WLR_TAG_RE = re.compile(r"\[[\w./-]+\.c:\d+\]")
_WLR_TOPIC_RE = re.compile(r"output|swapchain|mode|connector|crtc|commit|drm", re.IGNORECASE)
_JOURNAL_GREP = _KANSHI_RE.pattern + r"|\[[\w./-]+\.c:\d+\]"
_JOURNAL_MAX_LINES = 200
_SESSION_LOG_MAX_LINES = 40
_JOURNAL_STAMP_RE = re.compile(r"^[A-Z][a-z]{2} [ \d]\d \d{2}:\d{2}:\d{2}")
_KANSHI_PROFILE_RE = re.compile(r"profile '([^']*)'")


def _row(label: str, value: str) -> str:
    return f"  {label:<29}{value}"


def _mode_text(mode: OutputMode) -> str:
    return f"{mode.width}x{mode.height}@{mode.refresh_mhz / 1000:.2f} Hz"


def _card_rows(card: Path) -> str:
    device = card / "device"
    driver = os.path.basename(os.path.realpath(device / "driver")) if (device / "driver").exists() else "no driver"
    vendor = diagnostics._read_sysfs(device, "vendor").removeprefix("0x")
    product = diagnostics._read_sysfs(device, "device").removeprefix("0x")
    where = f"PCI {vendor}:{product}" if vendor and product else os.path.basename(os.path.realpath(device))
    boot = ", boot display" if diagnostics._read_sysfs(device, "boot_vga") == "1" else ""
    return f"    {card.name:<10}{driver} ({where}{boot})"


def _connector_row(connector: Path, card: str, name: str) -> str:
    status = diagnostics._read_sysfs(connector, "status") or "status unreadable"
    enabled = diagnostics._read_sysfs(connector, "enabled") or "?"
    dpms = diagnostics._read_sysfs(connector, "dpms") or "?"
    modes = list(dict.fromkeys(diagnostics._read_sysfs(connector, "modes").split()))
    shown = ", ".join(modes[:_MODES_SHOWN]) if modes else "none"
    if len(modes) > _MODES_SHOWN:
        shown += f" (+{len(modes) - _MODES_SHOWN} more)"
    return f"    {name:<12}{card:<7}{status}, {enabled}, dpms {dpms}; modes {shown}"


def collect_drm(drm_root: Path | None = None) -> tuple[list[str], list[str]]:
    """GPU and connector rows from sysfs, plus the names of the connected connectors."""
    drm_root = drm_root or _DRM_ROOT
    try:
        entries = sorted(drm_root.iterdir(), key=lambda p: p.name)
    except OSError as exc:
        return [_row("GPUs", f"[unavailable: {drm_root}: {exc.strerror or exc}]")], []
    cards = [e for e in entries if _CARD_RE.fullmatch(e.name)]
    rows = ["  GPUs:"]
    rows += [_card_rows(card) for card in cards] or ["    [none: the kernel lists no DRM device]"]
    rows += ["", "  Connectors (kernel):"]
    connected: list[str] = []
    connector_rows: list[str] = []
    for entry in entries:
        match = _CONNECTOR_RE.fullmatch(entry.name)
        if match is None:
            continue
        card, name = match.groups()
        connector_rows.append(_connector_row(entry, card, name))
        if diagnostics._read_sysfs(entry, "status") == "connected":
            connected.append(name)
    rows += connector_rows or ["    [none]"]
    return rows, connected


def _head_row(head: OutputHead) -> str:
    who = " ".join(part for part in (head.make, head.model) if part) or head.description
    label = f"{head.name or '(unnamed)'}"
    if not head.enabled:
        preferred = next((m for m in head.modes if m.preferred), None)
        offers = f"preferred {_mode_text(preferred)}" if preferred else f"{len(head.modes)} modes"
        return f"    {label:<12}disabled, offers {offers}" + (f"  [{who}]" if who else "")
    mode = _mode_text(head.current_mode) if head.current_mode else "no current mode"
    parts = [f"enabled, {mode}", f"at {head.position[0]},{head.position[1]}", f"scale {head.scale:.2f}"]
    if head.transform:
        parts.append(f"transform {head.transform}")
    return f"    {label:<12}" + ", ".join(parts) + (f"  [{who}]" if who else "")


def _arrangement(enabled: list[OutputHead]) -> str:
    if not enabled:
        return "no output enabled: nothing is on screen"
    if len(enabled) == 1:
        return "one output"
    if len({head.position for head in enabled}) == 1:
        return f"{len(enabled)} outputs mirrored (all at one position)"
    return f"{len(enabled)} outputs extended (side by side: each shows part of the window)"


def collect_layout(
    connected: list[str],
    *,
    environ: Mapping[str, str] = os.environ,
    timeout_s: float = _LAYOUT_TIMEOUT_S,
    reader: Callable[[Path, float], list[OutputHead]] = wayland_outputs.read_heads,
) -> list[str]:
    """What the compositor holds, and which connected displays it left out."""
    rows = ["", "  Compositor layout (wlr-output-management):"]
    path = wayland_outputs.socket_path(environ)
    if path is None:
        return [*rows, "    [not read: not running in a Wayland session]"]
    try:
        heads = reader(path, timeout_s)
    except WaylandError as exc:
        return [*rows, f"    [unavailable: {exc}]"]
    rows += [_head_row(head) for head in heads] or ["    [none: the compositor lists no output]"]
    enabled = [head for head in heads if head.enabled]
    rows.append(_row("  Arrangement", _arrangement(enabled)))
    held = {head.name for head in enabled}
    left_out = [name for name in connected if name not in held]
    if left_out:
        rows.append(_row("  Connected, not shown", ", ".join(left_out)))
    return rows


def read_display_journal(unit: str | None, timeout_s: float) -> list[str] | str:
    """This boot's kanshi and wlroots / Cage lines from the unit's journal, or why they are missing."""
    if not unit:
        return "[not read: no journald service name configured]"
    rc, out = diagnostics._run(
        [
            "journalctl",
            "-u",
            unit,
            "-b",
            "--no-pager",
            "-o",
            "short",
            "--grep",
            _JOURNAL_GREP,
            "-n",
            str(_JOURNAL_MAX_LINES),
        ],
        timeout_s=timeout_s,
    )
    if rc != 0 and out.strip():
        return out if out.startswith("[unavailable") else f"[unavailable: {out}]"
    # ``--grep`` exits non-zero with no output when nothing matched.
    return out.splitlines() if rc == 0 else []


def _is_session_line(line: str) -> bool:
    if _KANSHI_RE.search(line):
        return True
    tag = _WLR_TAG_RE.search(line)
    return tag is not None and _WLR_TOPIC_RE.search(line, tag.start()) is not None


def _stamp(line: str) -> str:
    match = _JOURNAL_STAMP_RE.match(line)
    return f" ({match.group(0)})" if match else ""


def kanshi_rows(lines: list[str]) -> list[str]:
    """kanshi's last verdict this boot, with the modes it could not set on the way there."""
    kanshi = [line for line in lines if _KANSHI_RE.search(line)]
    last = next(
        (line for line in reversed(kanshi) if "applying profile output" not in line and "support mode" not in line),
        None,
    )
    if last is None:
        return [_row("kanshi", "nothing logged this boot")]
    profile = _KANSHI_PROFILE_RE.search(last)
    name = f"'{profile.group(1)}'" if profile else ""
    if "no profile matched" in last:
        verdict = "no profile matched: the compositor arranges the outputs itself"
    elif "failed to apply" in last:
        verdict = f"profile {name} failed to apply"
    elif " applied" in last:
        verdict = f"profile {name} applied"
    elif "cancelled" in last:
        verdict = f"profile {name} cancelled, retrying"
    elif "applying profile" in last:
        verdict = f"applying profile {name}, no result logged"
    else:
        verdict = last.split(": ", 1)[-1]
    rows = [_row("kanshi", verdict + _stamp(last))]
    start = max((i for i, line in enumerate(kanshi) if "applying profile '" in line), default=-1)
    for line in kanshi[start + 1 :]:
        if "doesn't support mode" in line:
            rows.append(f"    {diagnostics.redact_log_line(line.split(': ', 1)[-1])}")
    return rows


def session_log_rows(lines: list[str]) -> list[str]:
    rows = ["", "  Session log (this boot: Cage / wlroots / kanshi, outputs and modes):"]
    matched = diagnostics.collapse_repeated_blocks([line for line in lines if _is_session_line(line)])
    if not matched:
        return [*rows, "    [none]"]
    dropped = len(matched) - _SESSION_LOG_MAX_LINES
    if dropped > 0:
        rows.append(f"    [... {dropped} earlier line(s) not shown]")
    return rows + [f"    {diagnostics.redact_log_line(line)}" for line in matched[-_SESSION_LOG_MAX_LINES:]]


def collect_display_manager(timeout_s: float = _SYSTEMCTL_TIMEOUT_S) -> list[str]:
    """The boot target and the display manager, the check the package's installer makes."""
    rc, out = diagnostics._run(["systemctl", "get-default"], timeout_s=timeout_s)
    target = out.strip() if rc == 0 else (out if out.startswith("[unavailable") else f"[unavailable: {out}]")
    rows = ["", _row("Default boot target", target)]
    rc, out = diagnostics._run(
        ["systemctl", "show", "-p", "Id", "-p", "LoadState", "-p", "ActiveState", "display-manager.service"],
        timeout_s=timeout_s,
    )
    if rc != 0:
        return [*rows, _row("Display manager", out if out.startswith("[unavailable") else f"[unavailable: {out}]")]
    props = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    if props.get("LoadState") != "loaded":
        return [*rows, _row("Display manager", "none enabled")]
    unit = props.get("Id", "display-manager.service")
    state = props.get("ActiveState", "unknown")
    if state == "active":
        return [*rows, _row("Display manager", f"{unit}, running: a desktop holds the display")]
    return [*rows, _row("Display manager", f"{unit}, enabled, {state}")]


def collect_displays(
    *,
    unit: str | None,
    budget_s: float,
    drm_root: Path | None = None,
    environ: Mapping[str, str] = os.environ,
    platform_name: str | None = None,
    layout_reader: Callable[[Path, float], list[OutputHead]] = wayland_outputs.read_heads,
) -> list[str]:
    """Section E11, Linux only."""
    if not (platform_name or sys.platform).startswith("linux"):
        return ["  [not applicable: displays are read on Linux only]"]
    deadline = time.monotonic() + budget_s

    def cap(limit: float) -> float:
        return max(0.0, min(limit, deadline - time.monotonic()))

    rows, connected = collect_drm(drm_root)
    rows += collect_layout(connected, environ=environ, timeout_s=cap(_LAYOUT_TIMEOUT_S), reader=layout_reader)
    rows.append("")
    journal = read_display_journal(unit, cap(diagnostics._DEFAULT_SUBPROCESS_TIMEOUT_S))
    if isinstance(journal, str):
        rows += [_row("kanshi", journal), "", "  Session log:", f"    {journal}"]
    else:
        rows += kanshi_rows(journal) + session_log_rows(journal)
    rows += collect_display_manager(cap(_SYSTEMCTL_TIMEOUT_S))
    return rows
