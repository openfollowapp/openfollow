#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Michel Honold and Paul Hermann
"""Time the Operator Screen's drawing on the bench scenes, or compare two checkouts.

Each scene (``tests/_hud_scenes.py``) is a run of frames with moving markers,
drawn by ``CairoOverlayRenderer.draw`` into a real 1920x1080 picture, cleared
before each frame as the HUD render thread does. The result is CPU time per
picture (``time.thread_time``), the median over rounds; the first round of each
scene only warms the caches::

    # every scene, this checkout
    poetry run python scripts/hw_validation/hud_bench.py

    # where the time goes, per drawing pass
    poetry run python scripts/hw_validation/hud_bench.py --scenes full --split

    # this checkout against another one (e.g. ``git archive <base> openfollow
    # tests/_hud_scenes.py`` unpacked into /tmp/of-base); rounds alternate between the two
    poetry run python scripts/hw_validation/hud_bench.py --compare /tmp/of-base

Each side runs in a worker process of its own, with the renderer and the scenes
of its own checkout, so a change to the scenes' data never hands a renderer data
it cannot read. A checkout without ``tests/_hud_scenes.py`` draws this one's, less
any scene field it lacks (the report names those). The report says when the two
checkouts' scenes differ, and a scene only this checkout has is timed here alone.

Exit ``0`` = done (with ``--compare``: no scene more than ``--max-slowdown``
slower), ``1`` = a scene is slower than that, ``2`` = a side could not run or
its renderer failed to draw a frame.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import logging
import statistics
import subprocess
import sys
import time
import traceback
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
SCENES_FILE = REPO / "tests" / "_hud_scenes.py"

# Drawing passes timed by --split: (module, attribute or Class.method, label).
SPLIT_PASSES = (
    ("openfollow.video.overlay", "draw_paths_pass", "paths"),
    ("openfollow.video.overlay", "draw_marker_pass", "markers"),
    ("openfollow.video.overlay", "draw_zones_pass", "zones"),
    ("openfollow.video.overlay", "draw_operator_messages_pass", "operator messages"),
    ("openfollow.video.overlay", "draw_settings_overlay_pass", "settings menu"),
    ("openfollow.runtime.overlay_draw_hud", "draw_bottom_left_info_panel", "info panel"),
    ("openfollow.runtime.overlay_draw_hud", "_draw_corner_help", "help"),
    ("openfollow.runtime.overlay_draw_hud", "draw_system_stats", "system stats"),
    ("openfollow.runtime.overlay_draw_hud", "draw_marker_card", "marker cards"),
    ("openfollow.runtime.overlay_draw_hud", "draw_status_badge", "status badge"),
    ("openfollow.runtime.overlay_draw_scene", "GridCache.draw", "grid"),
)


# --------------------------------------------------------------------------- #
# Timing (the worker side)
# --------------------------------------------------------------------------- #


def time_round(
    draw: Callable[[Any, int, int, Any], None],
    surface: Any,
    frames: Sequence[Any],
    clock: Callable[[], float],
    cairo: Any,
) -> float:
    """CPU ms per picture for one pass over ``frames``, each drawn onto ``surface`` after clearing it."""
    total = 0.0
    for width, height, state in frames:
        start = clock()
        clear = cairo.Context(surface)
        clear.set_operator(cairo.OPERATOR_CLEAR)
        clear.paint()
        draw(cairo.Context(surface), width, height, state)
        total += clock() - start
    return 1000.0 * total / max(1, len(frames))


class Split:
    """Wraps the drawing passes and adds up the CPU time spent in each."""

    def __init__(self, clock: Callable[[], float]) -> None:
        self.clock = clock
        self.ms: dict[str, float] = defaultdict(float)
        self._undo: list[tuple[Any, str, Any]] = []

    def install(self) -> None:
        for module_name, path, label in SPLIT_PASSES:
            owner: Any = importlib.import_module(module_name)
            *outer, attr = path.split(".")
            for name in outer:
                owner = getattr(owner, name, None)
            original = getattr(owner, attr, None) if owner is not None else None
            if original is None:
                continue
            setattr(owner, attr, self._wrap(original, label))
            self._undo.append((owner, attr, original))

    def remove(self) -> None:
        for module, attr, original in reversed(self._undo):
            setattr(module, attr, original)
        self._undo.clear()

    def _wrap(self, func: Callable[..., Any], label: str) -> Callable[..., Any]:
        def timed(*args: Any, **kwargs: Any) -> Any:
            start = self.clock()
            try:
                return func(*args, **kwargs)
            finally:
                self.ms[label] += 1000.0 * (self.clock() - start)

        return timed


class _DrawErrors(logging.Handler):
    """Collects the warnings the renderer logs for a frame it could not draw."""

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def worker(
    package_root: Path, stdin: Any = sys.stdin, stdout: Any = sys.stdout, clock: Callable[[], float] = time.thread_time
) -> int:
    """Answer ``{"scene", "frames", "split"}`` lines with ``{"ms", "split", "errors", "dropped"}`` lines.

    The renderer and the scenes come from ``package_root``; a scene it lacks is answered ``{"missing": true}``.
    """
    sys.path.insert(0, str(package_root))
    import cairo

    from openfollow.video.overlay import CairoOverlayRenderer

    scenes = load_scenes(package_root)
    # The renderer logs a failed frame and carries on; a failed frame is fast, so it must not be timed.
    errors = _DrawErrors()
    overlay_logger = logging.getLogger("openfollow.video.overlay")
    overlay_logger.addHandler(errors)
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, scenes.WIDTH, scenes.HEIGHT)
    renderers: dict[str, Any] = {}
    built: dict[tuple[str, int], tuple[Any, list[str]]] = {}
    try:
        for line in stdin:
            request = json.loads(line)
            if request["scene"] not in scenes.SCENES:
                stdout.write(json.dumps({"missing": True}) + "\n")
                stdout.flush()
                continue
            key = (request["scene"], int(request["frames"]))
            if key not in built:
                # DROPPED collects across builds; each scene reports only its own.
                scenes.DROPPED.clear()
                built[key] = (scenes.frames(*key), sorted(scenes.DROPPED))
            scene_frames, dropped = built[key]
            if request["scene"] not in renderers:
                renderers[request["scene"]] = CairoOverlayRenderer()
            errors.messages.clear()
            split = Split(clock) if request.get("split") else None
            if split is not None:
                split.install()
            try:
                ms = time_round(renderers[request["scene"]].draw, surface, scene_frames, clock, cairo)
            finally:
                if split is not None:
                    split.remove()
            frames = len(scene_frames)
            reply = {
                "ms": ms,
                "split": {k: v / frames for k, v in split.ms.items()} if split else {},
                "errors": errors.messages[:1],
                "dropped": dropped,
            }
            stdout.write(json.dumps(reply) + "\n")
            stdout.flush()
    finally:
        overlay_logger.removeHandler(errors)
    return 0


# --------------------------------------------------------------------------- #
# Driving the workers and judging (the parent side)
# --------------------------------------------------------------------------- #


class Worker:
    """A worker process drawing with the renderer of one checkout."""

    def __init__(self, package_root: Path, python: str = sys.executable) -> None:
        self.root = package_root
        self.proc = subprocess.Popen(  # noqa: S603 - our own script, fixed arguments
            [python, str(Path(__file__).resolve()), "--worker", str(package_root)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )

    def run(self, scene: str, frames: int, split: bool = False) -> dict[str, Any]:
        assert self.proc.stdin is not None and self.proc.stdout is not None
        try:
            self.proc.stdin.write(json.dumps({"scene": scene, "frames": frames, "split": split}) + "\n")
            self.proc.stdin.flush()
            line = self.proc.stdout.readline()
        except OSError as exc:
            raise RuntimeError(f"the worker for {self.root} stopped") from exc
        if not line:
            raise RuntimeError(f"the worker for {self.root} stopped")
        try:
            reply = json.loads(line)
            if reply == {"missing": True}:
                return dict(reply)
            float(reply["ms"])
        except (ValueError, TypeError, KeyError) as exc:
            raise RuntimeError(f"the worker for {self.root} answered {line.strip()!r}") from exc
        return dict(reply)

    def close(self) -> None:
        with contextlib.suppress(OSError):
            if self.proc.stdin is not None:
                self.proc.stdin.close()
        try:
            self.proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()


@dataclass
class SceneResult:
    scene: str
    head_ms: list[float] = field(default_factory=list)
    base_ms: list[float] = field(default_factory=list)
    split: dict[str, float] = field(default_factory=dict)
    # Scene fields the other checkout lacks, so its frames hold less than this one's.
    base_lacks: list[str] = field(default_factory=list)
    # The other checkout's scenes have no scene of this name: timed here only.
    base_missing: bool = False

    @property
    def head(self) -> float:
        return statistics.median(self.head_ms)

    @property
    def base(self) -> float | None:
        return statistics.median(self.base_ms) if self.base_ms else None

    @property
    def slowdown(self) -> float | None:
        """How much slower this checkout is than the other one (0.15 = 15 % slower)."""
        base = self.base
        return None if not base else self.head / base - 1.0


def _run(side: Any, scene: str, frames: int, split: bool = False) -> dict[str, Any]:
    """One round on ``side``; a frame the renderer failed to draw stops the run."""
    reply: dict[str, Any] = side.run(scene, frames, split)
    if reply.get("errors"):
        raise RuntimeError(f"{scene}: the renderer failed to draw a frame: {reply['errors'][0]}")
    return reply


def measure(
    scenes: Sequence[str], frames: int, rounds: int, head: Any, base: Any | None, split: bool
) -> list[SceneResult]:
    """Warm each scene up once per side, then time ``rounds`` rounds, alternating the sides."""
    results = []
    for scene in scenes:
        result = SceneResult(scene)
        sides = [(head, result.head_ms)] + ([(base, result.base_ms)] if base is not None else [])
        for side, _ in list(sides):
            warm = _run(side, scene, frames)
            if side is base and warm.get("missing"):
                result.base_missing = True
                sides = sides[:1]
            elif side is base:
                result.base_lacks = list(warm.get("dropped", []))
        for r in range(rounds):
            # Alternate which side goes first, so a load change hits both alike.
            for side, out in sides if r % 2 == 0 else sides[::-1]:
                out.append(float(_run(side, scene, frames)["ms"]))
        if split:
            result.split = dict(_run(head, scene, frames, split=True)["split"])
        results.append(result)
    return results


def verdict(results: Sequence[SceneResult], max_slowdown: float) -> int:
    """``1`` when any compared scene is more than ``max_slowdown`` slower, else ``0``."""
    slower = [r for r in results if r.slowdown is not None and r.slowdown > max_slowdown]
    return 1 if slower else 0


def report(results: Sequence[SceneResult], max_slowdown: float) -> list[str]:
    lines = []
    for r in results:
        if r.base_missing:
            lines.append(f"{r.scene:14} {r.head:7.2f} ms/picture  base has no such scene")
        elif r.slowdown is None:
            lines.append(f"{r.scene:14} {r.head:7.2f} ms/picture")
        else:
            flag = "  SLOWER" if r.slowdown > max_slowdown else ""
            lines.append(f"{r.scene:14} {r.head:7.2f} ms/picture  base {r.base:7.2f}  {r.slowdown:+6.1%}{flag}")
        if r.base_lacks:
            lines.append(f"    base lacks {', '.join(r.base_lacks)}")
        for label, ms in sorted(r.split.items(), key=lambda kv: -kv[1]):
            lines.append(f"    {label:18} {ms:6.2f} ms")
    return lines


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenes", default="", help="comma-separated scene names (default: all)")
    parser.add_argument("--frames", type=int, default=60, help="frames per scene")
    parser.add_argument("--rounds", type=int, default=5, help="timed rounds per scene and side")
    parser.add_argument("--split", action="store_true", help="also time each drawing pass (this checkout)")
    parser.add_argument("--compare", type=Path, help="another checkout's root (holding openfollow/) to compare with")
    parser.add_argument("--max-slowdown", type=float, default=0.15, help="slowdown that fails --compare (0.15 = 15 %%)")
    parser.add_argument("--json", action="store_true", help="one JSON line per scene instead of the table")
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    return parser


def scenes_file(root: Path) -> Path:
    """The scenes a checkout draws: its own ``tests/_hud_scenes.py``, else this checkout's."""
    own = root / "tests" / "_hud_scenes.py"
    return own if own.is_file() else SCENES_FILE


def scenes_note(base: Path) -> str:
    """What the report says about the base's scenes, or nothing when they are this checkout's."""
    own = scenes_file(base)
    if own == SCENES_FILE:
        return "the base has no scenes of its own and draws this checkout's"
    if own.read_bytes() != SCENES_FILE.read_bytes():
        return "the base draws its own scenes, which differ from this checkout's"
    return ""


def load_scenes(root: Path = REPO) -> Any:
    """``root``'s scenes (``scenes_file``) as a module, with ``openfollow`` from whatever is first on ``sys.path``."""
    spec = importlib.util.spec_from_file_location("_hud_scenes", scenes_file(root))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["_hud_scenes"] = module
    spec.loader.exec_module(module)
    return module


def main(argv: Sequence[str] | None = None, make_worker: Callable[[Path], Any] = Worker) -> int:
    args = build_parser().parse_args(argv)
    if args.worker is not None:
        return worker(args.worker)
    sys.path.insert(0, str(REPO))
    try:
        known = list(load_scenes().SCENES)
    except ImportError as exc:
        print(f"the scenes do not load in this checkout: {exc}", file=sys.stderr)
        return 2
    scenes = [s for s in args.scenes.split(",") if s] or known
    unknown = [s for s in scenes if s not in known]
    if unknown:
        print(f"unknown scene(s): {', '.join(unknown)}; known: {', '.join(known)}", file=sys.stderr)
        return 2
    if args.frames < 1 or args.rounds < 1:
        print("--frames and --rounds must be 1 or more", file=sys.stderr)
        return 2
    # Written so NaN is refused too: no slowdown is ever greater than NaN.
    if not args.max_slowdown >= 0.0:
        print("--max-slowdown must be 0 or more", file=sys.stderr)
        return 2
    if args.compare is not None and not (args.compare / "openfollow").is_dir():
        print(f"{args.compare} holds no openfollow package", file=sys.stderr)
        return 2
    head = base = None
    try:
        head = make_worker(REPO)
        if args.compare is not None:
            base = make_worker(args.compare)
        results = measure(scenes, args.frames, args.rounds, head, base, args.split)
    except (RuntimeError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    finally:
        for side in (head, base):
            if side is not None:
                side.close()
    if args.json:
        for r in results:
            print(
                json.dumps(
                    {
                        "scene": r.scene,
                        "ms": r.head,
                        "base_ms": r.base,
                        "slowdown": r.slowdown,
                        "split": r.split,
                        "base_lacks": r.base_lacks,
                    }
                )
            )
    else:
        print("\n".join(report(results, args.max_slowdown)))
        note = scenes_note(args.compare) if args.compare is not None else ""
        if note:
            print(note)
    return verdict(results, args.max_slowdown)


def run(argv: Sequence[str] | None = None) -> int:
    """``main``, an unexpected error read as a side that could not run (2), never as a slower scene (1)."""
    try:
        return main(argv)
    except Exception:
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    sys.exit(run())
