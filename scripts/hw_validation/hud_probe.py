#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Measure what the Operator Screen costs on a running station.

Runs on the station, next to the app. Over each window it reads the CPU time of
the OpenFollow process and its threads from ``/proc`` (the main thread, the HUD
render thread named by ``/api/stats``, the GStreamer threads) and of the
compositor, and averages the ``hud`` section of ``/api/stats``: CPU per picture
and per repaint, pictures and repaints a second, and how many refreshes each
picture and each video frame stayed on screen::

    /opt/openfollow/venv/bin/python scripts/hw_validation/hud_probe.py --pin 0000
    /opt/openfollow/venv/bin/python scripts/hw_validation/hud_probe.py --runs 3 --window 20 --label after

One JSON line per run on stdout (``--out`` appends them to a file too).
Exit ``0`` = measured, ``2`` = no OpenFollow process, or the Operator Screen
reported nothing (a station without a display draws no HUD).
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

APP_PATTERN = "python -m openfollow.main"
PROC = Path("/proc")


# --------------------------------------------------------------------------- #
# /proc
# --------------------------------------------------------------------------- #


def cpu_ticks(stat_line: str) -> int:
    """utime + stime from a ``/proc/<pid>/stat`` line; the name in brackets may hold spaces."""
    fields = stat_line.rsplit(")", 1)[1].split()
    return int(fields[11]) + int(fields[12])


def read_ticks(path: Path) -> int | None:
    try:
        return cpu_ticks(path.read_text())
    except (OSError, IndexError, ValueError):
        return None


def thread_ticks(pid: int, proc: Path = PROC) -> dict[int, tuple[str, int]]:
    """Every thread of ``pid``: its name and CPU ticks so far."""
    out: dict[int, tuple[str, int]] = {}
    try:
        tids = os.listdir(proc / str(pid) / "task")
    except OSError:
        return out
    for tid in tids:
        task = proc / str(pid) / "task" / tid
        ticks = read_ticks(task / "stat")
        if ticks is None:
            continue
        try:
            name = (task / "comm").read_text().strip()
        except OSError:
            name = "?"
        out[int(tid)] = (name, ticks)
    return out


def thread_shares(
    before: dict[int, tuple[str, int]],
    after: dict[int, tuple[str, int]],
    seconds: float,
    hz: int,
    *,
    main: int,
    render: int | None,
) -> dict[str, float]:
    """% of one core per thread over the window; ``main`` and ``render`` by role, the rest by name."""
    shares: dict[str, float] = {}
    for tid, (name, ticks) in after.items():
        spent = ticks - before.get(tid, (name, 0))[1]
        key = "main" if tid == main else "hud_render" if tid == render else name
        shares[key] = shares.get(key, 0.0) + 100.0 * spent / hz / seconds
    return {k: round(v, 1) for k, v in sorted(shares.items(), key=lambda kv: -kv[1])}


def find_pid(pattern: str = APP_PATTERN, proc: Path = PROC) -> int | None:
    """The Python process running OpenFollow; the session's compositor carries the same command in its own."""
    found = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True, check=False).stdout.split()
    for pid in found:
        try:
            name = (proc / pid / "comm").read_text().strip()
        except OSError:
            continue
        if name.startswith("python"):
            return int(pid)
    return None


def find_compositor() -> int | None:
    found = subprocess.run(["pgrep", "-x", "cage"], capture_output=True, text=True, check=False).stdout.split()
    return int(found[0]) if found else None


# --------------------------------------------------------------------------- #
# /api/stats
# --------------------------------------------------------------------------- #


def stats_reader(base_url: str, pin: str) -> Callable[[], dict[str, Any]]:
    """A function returning ``/api/stats``, logged in with ``pin`` when one is set."""
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    if pin:
        body = urllib.parse.urlencode({"pin": pin}).encode()
        login = urllib.request.Request(f"{base_url}/login", data=body, headers={"Origin": base_url})
        opener.open(login, timeout=10).read()  # noqa: S310 - the station's own web UI

    def read() -> dict[str, Any]:
        with opener.open(f"{base_url}/api/stats", timeout=10) as reply:  # noqa: S310 - the station's own web UI
            return dict(json.loads(reply.read()))

    return read


def average_hud(samples: Sequence[dict[str, Any] | None]) -> dict[str, Any] | None:
    """Numbers averaged, refresh counts added up, over the distinct windows among ``samples``."""
    windows: list[dict[str, Any]] = []
    for sample in samples:
        if sample and sample not in windows:
            windows.append(sample)
    if not windows:
        return None
    out: dict[str, Any] = {"windows": len(windows)}
    for key in windows[-1]:
        values = [w[key] for w in windows if key in w]
        if key == "render_thread_id":
            out[key] = values[-1]
        elif all(isinstance(v, dict) for v in values):
            counts: dict[str, int] = {}
            for histogram in values:
                for refreshes, n in histogram.items():
                    counts[refreshes] = counts.get(refreshes, 0) + int(n)
            out[key] = dict(sorted(counts.items(), key=lambda kv: int(kv[0])))
        elif all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
            out[key] = round(sum(values) / len(values), 3)
    return out


def on_time(counts: dict[str, int], refreshes: int) -> float | None:
    """Share of entries that stayed exactly ``refreshes`` refreshes on screen."""
    total = sum(counts.values())
    return round(counts.get(str(refreshes), 0) / total, 3) if total else None


# --------------------------------------------------------------------------- #
# A run
# --------------------------------------------------------------------------- #


def measure(
    pid: int,
    compositor: int | None,
    read_stats: Callable[[], dict[str, Any]],
    window: float,
    *,
    hz: int,
    proc: Path = PROC,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
    poll: float = 0.5,
) -> dict[str, Any]:
    """One window: CPU per thread and of the compositor, and the averaged ``hud`` figures."""
    before_run = read_stats().get("hud")
    proc_before = read_ticks(proc / str(pid) / "stat") or 0
    comp_before = read_ticks(proc / str(compositor) / "stat") if compositor else None
    threads_before = thread_ticks(pid, proc)
    start = now()
    samples: list[dict[str, Any] | None] = []
    while now() - start < window:
        sleep(poll)
        samples.append(read_stats().get("hud"))
    seconds = max(now() - start, 1e-9)
    proc_after = read_ticks(proc / str(pid) / "stat") or proc_before
    comp_after = read_ticks(proc / str(compositor) / "stat") if compositor else None
    # The window closed before the run counts only when none closed during it.
    hud = average_hud([s for s in samples if s != before_run]) or average_hud([before_run])
    render = (hud or {}).get("render_thread_id")
    result: dict[str, Any] = {
        "seconds": round(seconds, 1),
        "process_pct": round(100.0 * (proc_after - proc_before) / hz / seconds, 1),
        "compositor_pct": (
            round(100.0 * (comp_after - comp_before) / hz / seconds, 1)
            if comp_before is not None and comp_after is not None
            else None
        ),
        "threads": thread_shares(threads_before, thread_ticks(pid, proc), seconds, hz, main=pid, render=render),
        "hud": hud,
    }
    if hud:
        interval = int(round(hud.get("interval_refreshes", 1)))
        result["pictures_on_time"] = on_time(hud.get("picture_refreshes", {}), interval)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pin", default="", help="the station's web PIN, if it has one")
    parser.add_argument("--web-url", default="", help="the web UI (default http://127.0.0.1)")
    parser.add_argument("--window", type=float, default=20.0, help="seconds per run")
    parser.add_argument("--runs", type=int, default=1, help="how many runs")
    parser.add_argument("--label", default="", help="a name for the runs, copied into each line")
    parser.add_argument("--pid", type=int, default=0, help="the OpenFollow process (default: found by name)")
    parser.add_argument("--out", type=Path, help="also append each line to this file")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    reader: Callable[[str, str], Callable[[], dict[str, Any]]] = stats_reader,
    run: Callable[..., dict[str, Any]] = measure,
) -> int:
    args = build_parser().parse_args(argv)
    if args.window <= 0 or args.runs < 1:
        print("--window must be positive and --runs 1 or more", file=sys.stderr)
        return 2
    pid = args.pid or find_pid()
    if not pid:
        print("no OpenFollow process found", file=sys.stderr)
        return 2
    read_stats = reader(args.web_url or "http://127.0.0.1", args.pin)
    hz = os.sysconf("SC_CLK_TCK")
    reported = False
    for _ in range(args.runs):
        result = {"label": args.label, **run(pid, find_compositor(), read_stats, args.window, hz=hz)}
        reported = reported or result["hud"] is not None
        line = json.dumps(result)
        print(line, flush=True)
        if args.out is not None:
            with args.out.open("a") as f:
                f.write(line + "\n")
    if not reported:
        print("the Operator Screen reported nothing: no display, or not drawing yet", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
