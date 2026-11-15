#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Paul Hermann and Michel Honold
"""Edge suggestion cost on the DUT – how long the Lens step's edge search takes.

``POST /api/wizard/lens/edges`` runs ``find_edge_candidates`` on a web thread,
and its chain tracing is pure Python, so while it runs it competes with the
frame loop for the GIL. This times it the way the route runs it, on synthetic
stages and on real snapshots, and measures the longest gap a 60 Hz ticker
thread sees meanwhile: the stall the frame loop would take.

Run on the DUT, from the repo root::

    poetry run python scripts/hw_validation/lens_edge_benchmark.py
    poetry run python scripts/hw_validation/lens_edge_benchmark.py --image snap1.jpg --image snap2.jpg

A real snapshot is a JPEG saved from ``/api/video/snapshot/full`` of a station
pointed at the scene; it is decoded with GStreamer, so no image library is
needed. Exit 0 = PASS (every scene under ``--budget`` seconds), 1 = FAIL.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from openfollow.scene.edge_chains import edge_map_scale, find_edge_candidates  # noqa: E402
from tests._lens_band import scaled_luma  # noqa: E402
from tests._lens_scene import Disc, Scene, Strip, deck_scene, render, stage_scene  # noqa: E402

# A 100 degree lens on 16:9, the strongest the wizard is built for.
WIDE_LENS = (-0.47, 0.25)
TICK_S = 1.0 / 60.0


@dataclass(frozen=True)
class Result:
    name: str
    size: tuple[int, int]
    runs_s: list[float]
    worst_tick_gap_s: float
    candidates: int


def _busy_scene(width: int, height: int) -> Scene:
    """An LED wall and a perforated panel: thousands of short edges, nearly none of them lines."""
    discs = [
        Disc((float(x), float(y)), 3.0, 200.0) for x in range(40, width - 40, 22) for y in range(40, height // 2, 22)
    ]
    strips = [
        Strip((float(x), height * 0.55), (float(x) + 9.0, height * 0.95), 1.0, 160.0) for x in range(30, width - 30, 14)
    ]
    return Scene(strips, discs)


def _synthetic() -> list[tuple[str, np.ndarray]]:
    return [
        ("stage 1080p, wide lens", render(stage_scene(), WIDE_LENS)),
        ("stage 1080p, no lens", render(stage_scene(), (0.0, 0.0))),
        ("deck seams 720p", render(deck_scene(), (-0.2, 0.05), size=(1280, 720))),
        ("busy 1080p (LED wall, slats)", render(_busy_scene(1920, 1080), WIDE_LENS, noise=6.0)),
        ("noise only 1080p", render(Scene(), (0.0, 0.0), noise=12.0)),
    ]


def _decode_jpeg(path: Path) -> np.ndarray:
    import gi

    gi.require_version("Gst", "1.0")
    from gi.repository import Gst

    Gst.init(None)
    pipeline = Gst.parse_launch(
        "filesrc name=src ! jpegdec ! videoconvert ! video/x-raw,format=GRAY8 ! appsink name=sink sync=false"
    )
    pipeline.get_by_name("src").set_property("location", str(path))
    sink = pipeline.get_by_name("sink")
    pipeline.set_state(Gst.State.PLAYING)
    try:
        sample = sink.emit("try-pull-sample", 10 * Gst.SECOND)
        if sample is None:
            raise RuntimeError(f"{path}: no frame decoded")
        structure = sample.get_caps().get_structure(0)
        width, height = structure.get_value("width"), structure.get_value("height")
        buffer = sample.get_buffer()
        ok, info = buffer.map(Gst.MapFlags.READ)
        if not ok:
            raise RuntimeError(f"{path}: buffer not readable")
        try:
            stride = len(info.data) // height
            rows = np.frombuffer(info.data, dtype=np.uint8).reshape(height, stride)
            return rows[:, :width].astype(np.float64)
        finally:
            buffer.unmap(info)
    finally:
        pipeline.set_state(Gst.State.NULL)


def _measure(name: str, img: np.ndarray, repeats: int) -> Result:
    height, width = img.shape
    scale = edge_map_scale(width)
    luma = scaled_luma(img, scale)
    runs: list[float] = []
    worst_gap = 0.0
    count = 0
    for _ in range(repeats):
        stop = threading.Event()
        gaps: list[float] = []

        def _tick(stop: threading.Event = stop, gaps: list[float] = gaps) -> None:
            last = time.perf_counter()
            while not stop.is_set():
                time.sleep(TICK_S)
                now = time.perf_counter()
                gaps.append(now - last)
                last = now

        ticker = threading.Thread(target=_tick, daemon=True)
        ticker.start()
        time.sleep(3 * TICK_S)
        start = time.perf_counter()
        found = find_edge_candidates(luma, scale, width, height)
        runs.append(time.perf_counter() - start)
        stop.set()
        ticker.join()
        worst_gap = max(worst_gap, max(gaps, default=0.0))
        count = len(found.candidates)
    return Result(name, (width, height), runs, worst_gap, count)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--image", action="append", type=Path, default=[], help="a snapshot JPEG to time")
    parser.add_argument("--repeats", type=int, default=3, help="runs per scene (default 3)")
    parser.add_argument("--budget", type=float, default=1.0, help="seconds a scene may take (default 1.0)")
    parser.add_argument("--skip-synthetic", action="store_true", help="time only the --image snapshots")
    args = parser.parse_args(argv)

    scenes = [] if args.skip_synthetic else _synthetic()
    scenes += [(f"snapshot {path.name}", _decode_jpeg(path)) for path in args.image]
    if not scenes:
        parser.error("nothing to time")

    print(f"{'scene':38} {'size':>10} {'median s':>9} {'max s':>7} {'worst tick gap s':>17} {'lines':>6}")
    failed = False
    for name, img in scenes:
        result = _measure(name, img, args.repeats)
        slowest = max(result.runs_s)
        failed |= slowest > args.budget
        size = f"{result.size[0]}x{result.size[1]}"
        over = "  OVER BUDGET" if slowest > args.budget else ""
        print(
            f"{result.name:38} {size:>10} {statistics.median(result.runs_s):9.3f} {slowest:7.3f}"
            f" {result.worst_tick_gap_s:17.3f} {result.candidates:6d}{over}"
        )
    print("FAIL" if failed else "PASS", f"(budget {args.budget:.1f} s per scene)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
