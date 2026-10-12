# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""One path as a curve: straight between its points, its corners rounded, walked by arc length.

Each interior corner (every corner of a closed path) is replaced by a circular
arc of that point's radius, shrunk where it would take more than half of either
neighbouring segment. The curve is held as a dense polyline whose chords stay
within a millimetre of the arcs, so walking it by arc length is exact enough to
show no facets. Positions are PSN-absolute metres; the walk is in X/Y and Z
follows along it.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]

_ARC_TOLERANCE_M = 0.001
_MAX_ARC_STEP_RAD = math.radians(5.0)
# Below this two points are one, and a turn this close to none or to a full reversal gets no arc.
_SAME_POINT_M = 1e-6
_STRAIGHT_RAD = 1e-6


@dataclass(frozen=True)
class PathPoint:
    x: float
    y: float
    z: float
    radius: float = 0.0


class PathCurve:
    """A path's curve, addressed by arc length ``s`` from its start."""

    def __init__(self, xy: FloatArray, z: FloatArray, closed: bool) -> None:
        self.xy = xy
        self.z = z
        self.closed = closed
        steps = np.hypot(*np.diff(xy, axis=0).T)
        self.s: FloatArray = np.concatenate(([0.0], np.cumsum(steps)))
        self._seg_start = xy[:-1]
        self._seg_vec = np.diff(xy, axis=0)
        self._seg_len2 = steps**2

    @property
    def length(self) -> float:
        return float(self.s[-1])

    def wrap(self, s: float) -> float:
        """``s`` folded into the curve: around a closed one, clamped to an open one."""
        if self.closed:
            return float(s % self.length)
        return min(max(s, 0.0), self.length)

    def _segment(self, s: float) -> int:
        i = int(np.searchsorted(self.s, s, side="right")) - 1
        return min(max(i, 0), len(self.s) - 2)

    def point(self, s: float) -> tuple[float, float, float]:
        """The position at ``s``."""
        s = self.wrap(s)
        i = self._segment(s)
        span = self.s[i + 1] - self.s[i]
        t = (s - self.s[i]) / span if span > 0 else 0.0
        x, y = self.xy[i] + t * self._seg_vec[i]
        z = self.z[i] + t * (self.z[i + 1] - self.z[i])
        return float(x), float(y), float(z)

    def tangent(self, s: float) -> tuple[float, float]:
        """The unit direction of travel at ``s``, towards growing ``s``."""
        dx, dy = self._seg_vec[self._segment(self.wrap(s))]
        norm = math.hypot(dx, dy)
        return float(dx / norm), float(dy / norm)

    def nearest(self, x: float, y: float) -> tuple[float, float]:
        """The ``s`` of the curve's point nearest ``(x, y)``, and its distance."""
        rel = np.array([x, y]) - self._seg_start
        t = np.clip(np.einsum("ij,ij->i", rel, self._seg_vec) / self._seg_len2, 0.0, 1.0)
        gap = rel - t[:, None] * self._seg_vec
        dist2 = np.einsum("ij,ij->i", gap, gap)
        i = int(np.argmin(dist2))
        return float(self.s[i] + t[i] * math.sqrt(self._seg_len2[i])), math.sqrt(float(dist2[i]))

    def offset(self, s_from: float, s_to: float) -> float:
        """Signed travel from ``s_from`` to ``s_to`` along the curve, the shorter way round a loop."""
        delta = s_to - s_from
        if self.closed:
            half = self.length / 2.0
            delta = (delta + half) % self.length - half
        return delta

    def advance(self, s: float, ds: float) -> tuple[float, int]:
        """Travel ``ds`` from ``s``: the new ``s``, and -1 / +1 when an open curve's start / end stopped it."""
        if self.closed:
            return self.wrap(s + ds), 0
        target = s + ds
        if target < 0.0:
            return 0.0, -1
        if target > self.length:
            return self.length, 1
        return target, 0


def build_curve(points: Sequence[PathPoint], closed: bool) -> PathCurve | None:
    """The curve through ``points``, or ``None`` when fewer than two distinct points remain.

    A closed path needs three: two points make an open one.
    """
    pts = _distinct(points, closed)
    if len(pts) < 2:
        return None
    closed = closed and len(pts) >= 3
    n = len(pts)
    corners = range(n) if closed else range(1, n - 1)
    rounded = {i: _corner(pts[(i - 1) % n], pts[i], pts[(i + 1) % n]) for i in corners}
    samples: list[tuple[float, float, float]] = []
    if not closed:
        samples.append((pts[0].x, pts[0].y, pts[0].z))
    for i in corners:
        samples.extend(rounded[i])
    if closed:
        samples.append(samples[0])
    else:
        samples.append((pts[-1].x, pts[-1].y, pts[-1].z))
    arr = np.array(samples, dtype=np.float64)
    # Drop the repeats a corner meeting its neighbour's corner leaves behind.
    keep = np.concatenate(([True], np.hypot(*np.diff(arr[:, :2], axis=0).T) > _SAME_POINT_M))
    arr = arr[keep]
    return PathCurve(arr[:, :2].copy(), arr[:, 2].copy(), closed)


def _distinct(points: Sequence[PathPoint], closed: bool) -> list[PathPoint]:
    out: list[PathPoint] = []
    for p in points:
        if not out or math.hypot(p.x - out[-1].x, p.y - out[-1].y) > _SAME_POINT_M:
            out.append(p)
    if closed and len(out) > 1 and math.hypot(out[0].x - out[-1].x, out[0].y - out[-1].y) <= _SAME_POINT_M:
        out.pop()
    return out


def _corner(prev: PathPoint, at: PathPoint, nxt: PathPoint) -> list[tuple[float, float, float]]:
    """The samples replacing corner ``at``: the point itself, or the arc rounding it."""
    len_in = math.hypot(at.x - prev.x, at.y - prev.y)
    len_out = math.hypot(nxt.x - at.x, nxt.y - at.y)
    ux, uy = (at.x - prev.x) / len_in, (at.y - prev.y) / len_in
    vx, vy = (nxt.x - at.x) / len_out, (nxt.y - at.y) / len_out
    turn = math.acos(max(-1.0, min(1.0, ux * vx + uy * vy)))
    if at.radius <= 0.0 or turn < _STRAIGHT_RAD or turn > math.pi - _STRAIGHT_RAD:
        return [(at.x, at.y, at.z)]
    half_tan = math.tan(turn / 2.0)
    reach = min(at.radius * half_tan, 0.5 * min(len_in, len_out))
    radius = reach / half_tan
    ax, ay = at.x - ux * reach, at.y - uy * reach
    left = ux * vy - uy * vx > 0.0
    side = 1.0 if left else -1.0
    cx, cy = ax - uy * radius * side, ay + ux * radius * side
    start = math.atan2(ay - cy, ax - cx)
    sweep = turn * side
    step = min(_MAX_ARC_STEP_RAD, 2.0 * math.acos(max(-1.0, 1.0 - _ARC_TOLERANCE_M / radius)))
    count = max(1, math.ceil(turn / step))
    z_in = at.z + (prev.z - at.z) * reach / len_in
    z_out = at.z + (nxt.z - at.z) * reach / len_out
    return [
        (
            cx + radius * math.cos(start + sweep * k / count),
            cy + radius * math.sin(start + sweep * k / count),
            z_in + (z_out - z_in) * k / count,
        )
        for k in range(count + 1)
    ]
