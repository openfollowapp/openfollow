#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Drive several markers along predefined paths over OSC.

A companion-side traffic generator for anything that consumes marker
positions - PSN / OTP / RTTrPM output, trigger zones, the overlay, a console
downstream. Markers move continuously and predictably, so a receiver can be
checked against motion rather than a single static write::

    # four markers orbiting a shared centre, 90 degrees apart, 30 Hz
    python3 scripts/hw_validation/osc_marker_paths.py --host 10.0.0.5 \\
        --markers 301-304 --path circle --size 3 --period 8 --duration 60

    # per-marker paths, and a vertical bob between 0.5 m and 2.5 m
    python3 scripts/hw_validation/osc_marker_paths.py --host 10.0.0.5 \\
        --markers 301:circle,302:figure8,303:square --z-range 0.5,2.5

    # see the samples without touching the network
    python3 scripts/hw_validation/osc_marker_paths.py --markers 1-2 --dry-run

Coordinates are PSN-absolute metres (X stage-left, Y upstage, Z up) - the same
frame ``/marker/<id>`` writes land in. Stdlib only, so it runs on a bare show
Pi with no venv. Exits ``0`` once the run completes (or on Ctrl-C).
"""

from __future__ import annotations

import argparse
import math
import random
import socket
import struct
import sys
import time
from dataclasses import dataclass

DEFAULT_PORT = 8765
AXES: tuple[str, ...] = ("x", "y", "z")


# --------------------------------------------------------------------------- #
# OSC encoding (stdlib only)
# --------------------------------------------------------------------------- #


def _osc_pad(raw: bytes) -> bytes:
    """Pad to the next 4-byte boundary, adding nothing when already aligned."""
    remainder = len(raw) % 4
    return raw if remainder == 0 else raw + b"\x00" * (4 - remainder)


def osc_message(address: str, *values: float) -> bytes:
    """Encode one OSC message with an all-float argument list.

    The null terminator is part of the string *before* padding: an address
    whose length is already a multiple of 4 still gets exactly one null plus
    three pad bytes, never a spurious extra word - a receiver reads the word
    after the terminator as the type tag.
    """
    out = _osc_pad(address.encode("ascii") + b"\x00")
    out += _osc_pad(("," + "f" * len(values)).encode("ascii") + b"\x00")
    for value in values:
        out += struct.pack(">f", value)
    return out


# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PathParams:
    """Geometry shared by every path in a run."""

    centre_x: float = 0.0
    centre_y: float = 0.0
    size: float = 3.0
    period_s: float = 8.0
    z_low: float = 1.6
    z_high: float = 1.6
    z_period_s: float = 5.0

    def height(self, t: float, phase: float) -> float:
        """Z at time ``t``; constant unless a z-range was given."""
        if self.z_low == self.z_high:
            return self.z_low
        mid = (self.z_low + self.z_high) / 2.0
        swing = (self.z_high - self.z_low) / 2.0
        turns = (t / self.z_period_s + phase) * 2.0 * math.pi
        return mid + swing * math.sin(turns)


class Path:
    """A marker trajectory sampled at an absolute time.

    ``phase`` is a 0-1 fraction of the period, used to spread markers that
    share a shape so they don't stack on one point.
    """

    name = "path"

    def __init__(self, params: PathParams, phase: float = 0.0) -> None:
        self.params = params
        self.phase = phase

    def _turns(self, t: float) -> float:
        return (t / self.params.period_s + self.phase) * 2.0 * math.pi

    def _lap(self, t: float) -> float:
        """Position within the lap as a 0-1 fraction."""
        return (t / self.params.period_s + self.phase) % 1.0

    def plane(self, t: float) -> tuple[float, float]:
        raise NotImplementedError

    def sample(self, t: float) -> tuple[float, float, float]:
        x, y = self.plane(t)
        return x, y, self.params.height(t, self.phase)


class CirclePath(Path):
    name = "circle"

    def plane(self, t: float) -> tuple[float, float]:
        p = self.params
        angle = self._turns(t)
        return p.centre_x + p.size * math.cos(angle), p.centre_y + p.size * math.sin(angle)


class Figure8Path(Path):
    """Lemniscate of Gerono - crosses its own centre once per half lap."""

    name = "figure8"

    def plane(self, t: float) -> tuple[float, float]:
        p = self.params
        angle = self._turns(t)
        return p.centre_x + p.size * math.sin(angle), p.centre_y + p.size * math.sin(angle) * math.cos(angle)


class LinePath(Path):
    """Straight sweep across X, reversing at each end (triangle wave)."""

    name = "line"

    def plane(self, t: float) -> tuple[float, float]:
        p = self.params
        lap = self._lap(t)
        offset = 4.0 * lap - 1.0 if lap < 0.5 else 3.0 - 4.0 * lap
        return p.centre_x + p.size * offset, p.centre_y


class SquarePath(Path):
    """Perimeter of a square, a quarter lap per side."""

    name = "square"

    def plane(self, t: float) -> tuple[float, float]:
        p = self.params
        side, within = divmod(self._lap(t) * 4.0, 1.0)
        span = p.size * (2.0 * within - 1.0)
        if side == 0:
            return p.centre_x + span, p.centre_y - p.size
        if side == 1:
            return p.centre_x + p.size, p.centre_y + span
        if side == 2:
            return p.centre_x - span, p.centre_y + p.size
        return p.centre_x - p.size, p.centre_y - span


class SpiralPath(Path):
    """Outward spiral that snaps back to the centre each lap."""

    name = "spiral"

    _TURNS_PER_LAP = 3.0

    def plane(self, t: float) -> tuple[float, float]:
        p = self.params
        lap = self._lap(t)
        radius = p.size * lap
        angle = lap * self._TURNS_PER_LAP * 2.0 * math.pi
        return p.centre_x + radius * math.cos(angle), p.centre_y + radius * math.sin(angle)


class RandomWalkPath(Path):
    """Bounded drunkard's walk - the awkward case for smoothing and zones."""

    name = "random"

    def __init__(self, params: PathParams, phase: float = 0.0, rng: random.Random | None = None) -> None:
        super().__init__(params, phase)
        self._rng = rng or random.Random()
        self._x = params.centre_x
        self._y = params.centre_y
        self._last_t: float | None = None

    def plane(self, t: float) -> tuple[float, float]:
        p = self.params
        elapsed = 0.0 if self._last_t is None else max(0.0, t - self._last_t)
        self._last_t = t
        # One size-worth of drift per period, so --size / --period steer it
        # the same way they steer every other path.
        step = p.size / p.period_s * elapsed
        self._x = _clamp(self._x + self._rng.uniform(-step, step), p.centre_x - p.size, p.centre_x + p.size)
        self._y = _clamp(self._y + self._rng.uniform(-step, step), p.centre_y - p.size, p.centre_y + p.size)
        return self._x, self._y


class StaticPath(Path):
    """Parks the marker at the centre - a control for 'is anything moving?'."""

    name = "static"

    def plane(self, t: float) -> tuple[float, float]:
        return self.params.centre_x, self.params.centre_y


PATHS: dict[str, type[Path]] = {
    cls.name: cls for cls in (CirclePath, Figure8Path, LinePath, SquarePath, SpiralPath, RandomWalkPath, StaticPath)
}


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def build_path(name: str, params: PathParams, phase: float, rng: random.Random | None = None) -> Path:
    """Instantiate a path by name. Raises ``KeyError`` for an unknown name."""
    cls = PATHS[name]
    if cls is RandomWalkPath:
        return RandomWalkPath(params, phase, rng)
    return cls(params, phase)


# --------------------------------------------------------------------------- #
# Marker spec
# --------------------------------------------------------------------------- #


def parse_marker_spec(spec: str, default_path: str) -> list[tuple[int, str]]:
    """Parse ``301,302:circle,400-403:square`` into ``[(id, path), ...]``.

    A group without ``:path`` uses ``default_path``. Ranges are inclusive.
    Raises ``ValueError`` with the offending token on bad input.
    """
    assignments: list[tuple[int, str]] = []
    for token in (part.strip() for part in spec.split(",")):
        if not token:
            continue
        ids_text, _, path_name = token.partition(":")
        path_name = path_name.strip() or default_path
        if path_name not in PATHS:
            raise ValueError(f"unknown path {path_name!r} in {token!r}; choose from {', '.join(sorted(PATHS))}")
        low_text, dash, high_text = ids_text.strip().partition("-")
        try:
            low = int(low_text)
            high = int(high_text) if dash else low
        except ValueError:
            raise ValueError(f"marker id must be an integer: {token!r}") from None
        if high < low:
            raise ValueError(f"range runs backwards: {token!r}")
        assignments.extend((marker_id, path_name) for marker_id in range(low, high + 1))
    if not assignments:
        raise ValueError("no markers given")
    return assignments


def assign_phases(assignments: list[tuple[int, str]], *, spread: bool = True) -> list[tuple[int, str, float]]:
    """Spread markers sharing a path evenly around its period."""
    if not spread:
        return [(marker_id, path, 0.0) for marker_id, path in assignments]
    per_path: dict[str, list[int]] = {}
    for marker_id, path in assignments:
        per_path.setdefault(path, []).append(marker_id)
    phases = {
        (path, marker_id): index / len(ids) for path, ids in per_path.items() for index, marker_id in enumerate(ids)
    }
    return [(marker_id, path, phases[(path, marker_id)]) for marker_id, path in assignments]


# --------------------------------------------------------------------------- #
# Driving
# --------------------------------------------------------------------------- #


def messages_for(marker_id: int, position: tuple[float, float, float], *, axis_mode: str) -> list[bytes]:
    """Encode one marker update as a triple, or as three per-axis writes."""
    if axis_mode == "axis":
        return [osc_message(f"/marker/{marker_id}/{axis}", value) for axis, value in zip(AXES, position, strict=True)]
    return [osc_message(f"/marker/{marker_id}", *position)]


def run(args: argparse.Namespace) -> int:
    params = PathParams(
        centre_x=args.centre[0],
        centre_y=args.centre[1],
        size=args.size,
        period_s=args.period,
        z_low=args.z_range[0],
        z_high=args.z_range[1],
        z_period_s=args.z_period,
    )
    rng = random.Random(args.seed)
    try:
        assignments = assign_phases(parse_marker_spec(args.markers, args.path), spread=not args.no_phase_spread)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    drivers = [(marker_id, build_path(path, params, phase, rng)) for marker_id, path, phase in assignments]
    interval = 1.0 / args.rate

    print(
        f"{'DRY RUN, no packets' if args.dry_run else f'sending to {args.host}:{args.port}'} - "
        f"{len(drivers)} marker(s) @ {args.rate:g} Hz, "
        f"{'unbounded' if args.duration <= 0 else f'{args.duration:g}s'}, "
        f"mode={args.axis_mode}",
        flush=True,
    )
    for marker_id, path in drivers:
        print(f"  marker {marker_id}: {path.name} (phase {path.phase:.2f})", flush=True)

    sock = None if args.dry_run else socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    started = time.monotonic()
    sent = 0
    ticks = 0
    try:
        while True:
            now = time.monotonic()
            elapsed = now - started
            if args.duration > 0 and elapsed >= args.duration:
                break
            for marker_id, path in drivers:
                position = path.sample(elapsed)
                if sock is None:
                    if args.print_every and ticks % args.print_every == 0:
                        x, y, z = position
                        print(f"  t={elapsed:6.2f}s marker {marker_id}: ({x:7.3f}, {y:7.3f}, {z:6.3f})")
                else:
                    for payload in messages_for(marker_id, position, axis_mode=args.axis_mode):
                        sock.sendto(payload, (args.host, args.port))
                        sent += 1
            ticks += 1
            # Absolute deadline, so a slow tick doesn't accumulate drift.
            time.sleep(max(0.0, started + ticks * interval - time.monotonic()))
    except KeyboardInterrupt:
        print("\ninterrupted", flush=True)
    finally:
        if sock is not None and args.park:
            for marker_id, _path in drivers:
                park = (params.centre_x, params.centre_y, params.z_low)
                for payload in messages_for(marker_id, park, axis_mode=args.axis_mode):
                    sock.sendto(payload, (args.host, args.port))
            print(f"parked {len(drivers)} marker(s) at the centre", flush=True)
        if sock is not None:
            sock.close()

    print(f"done: {ticks} ticks, {sent} message(s) in {time.monotonic() - started:.1f}s", flush=True)
    return 0


def _pair(text: str) -> tuple[float, float]:
    parts = text.split(",")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(f"expected two comma-separated numbers, got {text!r}")
    try:
        return float(parts[0]), float(parts[1])
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected numbers, got {text!r}") from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Drive markers along predefined paths over OSC.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--host", default="127.0.0.1", help="OSC listener address of the station under test")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="OSC listener port")
    parser.add_argument("--markers", default="1", help="ids and optional paths, e.g. '301-304' or '1:circle,2:line'")
    parser.add_argument("--path", default="circle", choices=sorted(PATHS), help="path for ids given without one")
    parser.add_argument("--rate", type=float, default=30.0, help="updates per second per marker")
    parser.add_argument("--duration", type=float, default=30.0, help="seconds to run; <=0 runs until Ctrl-C")
    parser.add_argument("--centre", type=_pair, default=(0.0, 0.0), metavar="X,Y", help="path centre in metres")
    parser.add_argument("--size", type=float, default=3.0, help="radius / half-extent in metres")
    parser.add_argument("--period", type=float, default=8.0, help="seconds per lap")
    parser.add_argument(
        "--z-range",
        type=_pair,
        default=(1.6, 1.6),
        metavar="LO,HI",
        help="height in metres; equal values hold a constant Z",
    )
    parser.add_argument("--z-period", type=float, default=5.0, help="seconds per vertical bob")
    parser.add_argument(
        "--axis-mode",
        choices=("triple", "axis"),
        default="triple",
        help="'triple' sends /marker/<id> x y z; 'axis' sends three /marker/<id>/<axis> writes",
    )
    parser.add_argument("--no-phase-spread", action="store_true", help="start every marker at the same point")
    parser.add_argument("--seed", type=int, default=0, help="seed for the random path")
    parser.add_argument("--park", action="store_true", help="send a final centre position on exit")
    parser.add_argument("--dry-run", action="store_true", help="print samples instead of sending")
    parser.add_argument("--print-every", type=int, default=15, help="dry-run: print every Nth tick (0 to silence)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.rate <= 0:
        print("error: --rate must be positive", file=sys.stderr)
        return 2
    if args.period <= 0 or args.z_period <= 0:
        print("error: --period and --z-period must be positive", file=sys.stderr)
        return 2
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
