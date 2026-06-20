#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Capture a reproducible runtime baseline from OpenFollow's /api/stats endpoint."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

DEFAULT_STATS_URL = "http://127.0.0.1:8080/api/stats"


@dataclass(frozen=True)
class StatsSample:
    """Single sampled /api/stats payload with capture timestamp."""

    captured_at_monotonic: float
    payload: dict[str, Any]


def _non_negative_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid number: {value}") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError(f"Value must be >= 0: {value}")
    return parsed


def _positive_float(value: str) -> float:
    parsed = _non_negative_float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError(f"Value must be > 0: {value}")
    return parsed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default=DEFAULT_STATS_URL,
        help=f"/api/stats endpoint URL (default: {DEFAULT_STATS_URL})",
    )
    parser.add_argument(
        "--duration",
        type=_non_negative_float,
        default=30.0,
        help="Capture duration in seconds (default: 30)",
    )
    parser.add_argument(
        "--interval",
        type=_positive_float,
        default=1.0,
        help="Sampling interval in seconds (default: 1)",
    )
    parser.add_argument(
        "--timeout",
        type=_positive_float,
        default=2.0,
        help="HTTP timeout per request in seconds (default: 2)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print JSON summary instead of human-readable text",
    )
    return parser.parse_args()


def _nested_number(payload: dict[str, Any], *path: str) -> float | None:
    node: Any = payload
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    if isinstance(node, (int, float)):
        return float(node)
    return None


def _collect_series(samples: list[StatsSample], *path: str) -> list[float]:
    values: list[float] = []
    for sample in samples:
        value = _nested_number(sample.payload, *path)
        if value is not None:
            values.append(value)
    return values


def _mean_or_none(values: list[float]) -> float | None:
    if not values:
        return None
    return float(statistics.fmean(values))


def _max_or_none(values: list[float]) -> float | None:
    if not values:
        return None
    return float(max(values))


def fetch_stats(url: str, timeout_s: float) -> dict[str, Any]:
    """Fetch one runtime stats snapshot from OpenFollow."""
    try:
        with urlopen(url, timeout=timeout_s) as response:
            status = getattr(response, "status", 200)
            if status != 200:
                raise RuntimeError(f"unexpected HTTP status {status}")
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, RuntimeError) as exc:
        raise RuntimeError(f"failed to fetch {url}: {exc}") from exc

    if not isinstance(payload, dict):
        raise RuntimeError("expected JSON object from /api/stats")
    return payload


def capture_samples(
    url: str,
    *,
    duration_s: float,
    interval_s: float,
    timeout_s: float,
) -> tuple[list[StatsSample], int]:
    """Collect periodic /api/stats samples."""
    deadline = time.monotonic() + duration_s
    samples: list[StatsSample] = []
    failures = 0

    while True:
        now = time.monotonic()
        if samples and now > deadline:
            break

        try:
            payload = fetch_stats(url, timeout_s)
            samples.append(StatsSample(captured_at_monotonic=now, payload=payload))
        except RuntimeError as exc:
            failures += 1
            print(f"warning: {exc}", file=sys.stderr)

        if now >= deadline:
            break

        remaining = max(0.0, deadline - time.monotonic())
        sleep_for = min(interval_s, remaining)
        if sleep_for > 0.0:
            time.sleep(sleep_for)

    return samples, failures


def build_summary(
    samples: list[StatsSample],
    *,
    duration_s: float,
    interval_s: float,
    failures: int,
    url: str,
) -> dict[str, Any]:
    """Build aggregate metrics for reproducible before/after comparisons."""
    first = samples[0]
    last = samples[-1]
    elapsed_s = max(0.0, last.captured_at_monotonic - first.captured_at_monotonic)

    frame_first = _nested_number(first.payload, "playback", "frame_count_total")
    frame_last = _nested_number(last.payload, "playback", "frame_count_total")
    slow_first = _nested_number(first.payload, "playback", "slow_frames_total")
    slow_last = _nested_number(last.payload, "playback", "slow_frames_total")

    frame_delta: int | None = None
    if frame_first is not None and frame_last is not None and frame_last >= frame_first:
        frame_delta = int(frame_last - frame_first)

    slow_delta: int | None = None
    if slow_first is not None and slow_last is not None and slow_last >= slow_first:
        slow_delta = int(slow_last - slow_first)

    window_fps: float | None = None
    window_avg_frame_ms: float | None = None
    window_slow_frame_percent: float | None = None
    if frame_delta is not None and frame_delta > 0 and elapsed_s > 0.0:
        window_fps = frame_delta / elapsed_s
        window_avg_frame_ms = (elapsed_s / frame_delta) * 1000.0
        if slow_delta is not None:
            window_slow_frame_percent = (slow_delta / frame_delta) * 100.0

    cpu_values = _collect_series(samples, "system", "cpu_percent")
    ram_values = _collect_series(samples, "system", "ram_percent")
    temp_values = _collect_series(samples, "system", "temperature_c")

    playback_latest = {
        "avg_frame_ms": _nested_number(last.payload, "playback", "avg_frame_ms"),
        "recent_avg_frame_ms": _nested_number(last.payload, "playback", "recent_avg_frame_ms"),
        "effective_fps": _nested_number(last.payload, "playback", "effective_fps"),
        "recent_effective_fps": _nested_number(last.payload, "playback", "recent_effective_fps"),
        "slow_frame_percent": _nested_number(last.payload, "playback", "slow_frame_percent"),
        "recent_slow_frame_percent": _nested_number(last.payload, "playback", "recent_slow_frame_percent"),
        "slow_frame_threshold_ms": _nested_number(last.payload, "playback", "slow_frame_threshold_ms"),
        "last_frame_ms": _nested_number(last.payload, "playback", "last_frame_ms"),
    }

    playback_sample_avg = {
        "avg_frame_ms": _mean_or_none(_collect_series(samples, "playback", "avg_frame_ms")),
        "recent_avg_frame_ms": _mean_or_none(_collect_series(samples, "playback", "recent_avg_frame_ms")),
        "effective_fps": _mean_or_none(_collect_series(samples, "playback", "effective_fps")),
        "recent_effective_fps": _mean_or_none(_collect_series(samples, "playback", "recent_effective_fps")),
        "slow_frame_percent": _mean_or_none(_collect_series(samples, "playback", "slow_frame_percent")),
        "recent_slow_frame_percent": _mean_or_none(_collect_series(samples, "playback", "recent_slow_frame_percent")),
    }

    return {
        "capture": {
            "url": url,
            "duration_requested_s": duration_s,
            "interval_s": interval_s,
            "samples_collected": len(samples),
            "request_failures": failures,
            "elapsed_s": elapsed_s,
        },
        "playback": {
            "latest": playback_latest,
            "sample_average": playback_sample_avg,
            "capture_window": {
                "frame_delta": frame_delta,
                "slow_frame_delta": slow_delta,
                "window_fps": window_fps,
                "window_avg_frame_ms": window_avg_frame_ms,
                "window_slow_frame_percent": window_slow_frame_percent,
            },
        },
        "system": {
            "cpu_percent_avg": _mean_or_none(cpu_values),
            "cpu_percent_max": _max_or_none(cpu_values),
            "ram_percent_avg": _mean_or_none(ram_values),
            "ram_percent_max": _max_or_none(ram_values),
            "temperature_c_avg": _mean_or_none(temp_values),
            "temperature_c_max": _max_or_none(temp_values),
        },
    }


def _fmt_num(value: Any, *, decimals: int = 2, suffix: str = "") -> str:
    if value is None:
        return "n/a"
    if isinstance(value, (int, float)):
        return f"{value:.{decimals}f}{suffix}"
    return str(value)


def print_text_summary(summary: dict[str, Any]) -> None:
    capture = summary["capture"]
    playback = summary["playback"]
    latest = playback["latest"]
    sample_average = playback["sample_average"]
    window = playback["capture_window"]
    system = summary["system"]

    print("Runtime baseline summary")
    print(
        "Capture:"
        f" samples={capture['samples_collected']}"
        f", failures={capture['request_failures']}"
        f", elapsed={_fmt_num(capture['elapsed_s'], suffix='s')}"
    )
    print(
        "Playback (latest):"
        f" avg_frame={_fmt_num(latest['avg_frame_ms'], suffix='ms')},"
        f" recent_avg={_fmt_num(latest['recent_avg_frame_ms'], suffix='ms')},"
        f" fps={_fmt_num(latest['effective_fps'])},"
        f" recent_fps={_fmt_num(latest['recent_effective_fps'])}"
    )
    print(
        "Playback (slow frames):"
        f" lifetime={_fmt_num(latest['slow_frame_percent'], suffix='%')},"
        f" recent={_fmt_num(latest['recent_slow_frame_percent'], suffix='%')},"
        f" threshold={_fmt_num(latest['slow_frame_threshold_ms'], suffix='ms')}"
    )
    print(
        "Playback (capture window):"
        f" fps={_fmt_num(window['window_fps'])},"
        f" avg_frame={_fmt_num(window['window_avg_frame_ms'], suffix='ms')},"
        f" slow={_fmt_num(window['window_slow_frame_percent'], suffix='%')},"
        f" frames={window['frame_delta'] if window['frame_delta'] is not None else 'n/a'}"
    )
    print(
        "Playback (sample average):"
        f" avg_frame={_fmt_num(sample_average['avg_frame_ms'], suffix='ms')},"
        f" recent_avg={_fmt_num(sample_average['recent_avg_frame_ms'], suffix='ms')},"
        f" fps={_fmt_num(sample_average['effective_fps'])},"
        f" recent_fps={_fmt_num(sample_average['recent_effective_fps'])}"
    )
    print(
        "System:"
        f" cpu_avg/max={_fmt_num(system['cpu_percent_avg'], suffix='%')}"
        f"/{_fmt_num(system['cpu_percent_max'], suffix='%')},"
        f" ram_avg/max={_fmt_num(system['ram_percent_avg'], suffix='%')}"
        f"/{_fmt_num(system['ram_percent_max'], suffix='%')},"
        f" temp_avg/max={_fmt_num(system['temperature_c_avg'], suffix='C')}"
        f"/{_fmt_num(system['temperature_c_max'], suffix='C')}"
    )


def main() -> int:
    args = parse_args()
    samples, failures = capture_samples(
        args.url,
        duration_s=args.duration,
        interval_s=args.interval,
        timeout_s=args.timeout,
    )
    if not samples:
        print("error: no /api/stats samples collected. Is OpenFollow running?", file=sys.stderr)
        return 1

    summary = build_summary(
        samples,
        duration_s=args.duration,
        interval_s=args.interval,
        failures=failures,
        url=args.url,
    )
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print_text_summary(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
