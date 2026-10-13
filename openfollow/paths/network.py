# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The active paths as one network: where they cross, and how a locked marker moves through it.

Two paths cross where they meet in plan at the same height, and a path whose
end lies on another joins it there too. A path crossing itself is not a
junction. A locked marker moves along its path by the part of the operator's
push that points along it; at a junction it takes whichever direction matches
the push best, but leaves its own path only when the other is clearly better.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import numpy as np

from openfollow.paths.geometry import FloatArray, PathCurve

# How close two paths must come, in plan and in height, to meet.
JUNCTION_REACH_M = 0.1
JUNCTION_HEIGHT_M = 0.1
# A marker this close to a junction may steer into the other path.
_CAPTURE_M = 0.05
# The other path wins only when its direction is this much closer to the push.
_SWITCH_MARGIN_RAD = math.radians(15.0)
_SAME_JUNCTION_M = 0.05

# Segment starts, vectors, squared lengths, ``s`` at each start, and the path each belongs to.
_SegmentIndex = tuple[FloatArray, FloatArray, FloatArray, FloatArray, FloatArray]
_PARALLEL = 1e-12
_EPS = 1e-6
_MAX_LEGS = 16
_INTERSECT_CHUNK = 256


@dataclass(frozen=True)
class Place:
    """A spot on the network: a path, by index, and the arc length along it."""

    path: int
    s: float


@dataclass(frozen=True)
class Junction:
    a: int
    s_a: float
    b: int
    s_b: float


@dataclass(frozen=True)
class Step:
    """Where a walk ended, and whether it went off an end that releases the marker."""

    place: Place
    released: bool = False


@dataclass(frozen=True)
class _Way:
    place: Place
    sign: int
    dx: float
    dy: float


class PathNetwork:
    """The active paths' curves, the junctions between them, and movement through them."""

    def __init__(self, curves: Sequence[PathCurve], releases: Sequence[tuple[bool, bool]] | None = None) -> None:
        self.curves = list(curves)
        self.releases = list(releases) if releases is not None else [(False, False)] * len(self.curves)
        self.junctions = find_junctions(self.curves)
        self._stations: dict[int, list[tuple[float, Place]]] = {i: [] for i in range(len(self.curves))}
        for j in self.junctions:
            self._stations[j.a].append((j.s_a, Place(j.b, j.s_b)))
            self._stations[j.b].append((j.s_b, Place(j.a, j.s_a)))
        self._groups = self._find_groups()
        self._indexes: dict[frozenset[int], _SegmentIndex | None] = {}

    def point(self, place: Place) -> tuple[float, float, float]:
        return self.curves[place.path].point(place.s)

    def nearest(self, x: float, y: float, among: Iterable[int] | None = None) -> Place | None:
        """The place nearest ``(x, y)`` on the given paths (all of them by default); a tie goes to the lower path."""
        index = self._segment_index(range(len(self.curves)) if among is None else among)
        if index is None:
            return None
        start, vec, len2, s0, owner = index
        rel = np.array([x, y]) - start
        t = np.clip(np.einsum("ij,ij->i", rel, vec) / len2, 0.0, 1.0)
        gap = rel - t[:, None] * vec
        i = int(np.argmin(np.einsum("ij,ij->i", gap, gap)))
        return Place(int(owner[i]), float(s0[i] + t[i] * math.sqrt(len2[i])))

    def _segment_index(self, paths: Iterable[int]) -> _SegmentIndex | None:
        """Every segment of ``paths`` in one set of arrays, lower paths first, kept per set of paths."""
        key = frozenset(paths)
        if key not in self._indexes:
            parts = [(i, *self.curves[i].segments()) for i in sorted(key)]
            self._indexes[key] = (
                (
                    np.concatenate([p[1] for p in parts]),
                    np.concatenate([p[2] for p in parts]),
                    np.concatenate([p[3] for p in parts]),
                    np.concatenate([p[4] for p in parts]),
                    np.concatenate([np.full(len(p[3]), p[0]) for p in parts]),
                )
                if parts
                else None
            )
        return self._indexes[key]

    def connected(self, path: int) -> frozenset[int]:
        """Every path reachable from ``path`` through junctions, ``path`` included."""
        return self._groups[path]

    def _find_groups(self) -> list[frozenset[int]]:
        groups = []
        for start in range(len(self.curves)):
            seen = {start}
            todo = [start]
            while todo:
                for _, other in self._stations[todo.pop()]:
                    if other.path not in seen:
                        seen.add(other.path)
                        todo.append(other.path)
            groups.append(frozenset(seen))
        return groups

    def walk(self, place: Place, vx: float, vy: float, dt: float) -> Step:
        """Move a locked marker for ``dt`` seconds under the push ``(vx, vy)`` in m/s."""
        speed = math.hypot(vx, vy)
        if speed <= 0.0 or dt <= 0.0:
            return Step(place)
        wx, wy = vx / speed, vy / speed
        time_left = dt
        for _ in range(_MAX_LEGS):
            way = self._choose(place, wx, wy)
            if way is None:
                break
            curve = self.curves[way.place.path]
            rate = speed * (wx * way.dx + wy * way.dy)
            budget = rate * time_left
            ahead = self._next_junction(way.place, way.sign)
            leg = budget if ahead is None else min(budget, ahead)
            s, end = curve.advance(way.place.s, way.sign * leg)
            place = Place(way.place.path, s)
            if end != 0:
                if self.releases[place.path][0 if end < 0 else 1]:
                    return Step(place, released=True)
                break
            if leg >= budget:
                break
            time_left -= leg / rate
        return Step(place)

    def _ways(self, place: Place) -> list[_Way]:
        curve = self.curves[place.path]
        tx, ty = curve.tangent(place.s)
        release_start, release_end = self.releases[place.path]
        ways = []
        if curve.closed or place.s < curve.length - _EPS or release_end:
            ways.append(_Way(place, 1, tx, ty))
        if curve.closed or place.s > _EPS or release_start:
            ways.append(_Way(place, -1, -tx, -ty))
        return ways

    def _choose(self, place: Place, wx: float, wy: float) -> _Way | None:
        stay = self._ways(place)
        other: list[_Way] = []
        curve = self.curves[place.path]
        for s, partner in self._stations[place.path]:
            if abs(curve.offset(place.s, s)) <= _CAPTURE_M:
                other.extend(self._ways(partner))

        def angle(way: _Way) -> float:
            return math.acos(max(-1.0, min(1.0, wx * way.dx + wy * way.dy)))

        best = min(stay, key=angle, default=None)
        challenger = min(other, key=angle, default=None)
        if challenger is not None and (best is None or angle(challenger) + _SWITCH_MARGIN_RAD < angle(best)):
            best = challenger
        if best is None or wx * best.dx + wy * best.dy <= 0.0:
            return None
        return best

    def _next_junction(self, place: Place, sign: int) -> float | None:
        curve = self.curves[place.path]
        ahead = []
        for s, _ in self._stations[place.path]:
            d = sign * (s - place.s)
            if curve.closed:
                d %= curve.length
            if d > _CAPTURE_M:
                ahead.append(d)
        return min(ahead, default=None)


def find_junctions(curves: Sequence[PathCurve]) -> list[Junction]:
    """Where each pair of curves crosses or touches, at the same height."""
    found: list[Junction] = []
    for a in range(len(curves)):
        for b in range(a + 1, len(curves)):
            pair = [Junction(a, sa, b, sb) for sa, sb in _crossings(curves[a], curves[b])]
            pair += [Junction(a, sa, b, sb) for sa, sb in _touches(curves[a], curves[b])]
            pair += [Junction(a, sa, b, sb) for sb, sa in _touches(curves[b], curves[a])]
            for j in pair:
                if _same_height(curves[a], j.s_a, curves[b], j.s_b) and not any(
                    _same_junction(curves, j, k) for k in found
                ):
                    found.append(j)
    return found


def _crossings(ca: PathCurve, cb: PathCurve) -> list[tuple[float, float]]:
    pa, ra = ca.xy[:-1], np.diff(ca.xy, axis=0)
    pb, rb = cb.xy[:-1], np.diff(cb.xy, axis=0)
    out: list[tuple[float, float]] = []
    for lo in range(0, len(pa), _INTERSECT_CHUNK):
        p, r = pa[lo : lo + _INTERSECT_CHUNK, None, :], ra[lo : lo + _INTERSECT_CHUNK, None, :]
        qp = pb[None, :, :] - p
        denom = _cross(r, rb[None, :, :])
        with np.errstate(divide="ignore", invalid="ignore"):
            t = _cross(qp, rb[None, :, :]) / denom
            u = _cross(qp, r) / denom
        hit = (np.abs(denom) > _PARALLEL) & (t >= 0.0) & (t <= 1.0) & (u >= 0.0) & (u <= 1.0)
        for i, k in zip(*np.nonzero(hit), strict=True):
            sa = ca.s[lo + i] + t[i, k] * (ca.s[lo + i + 1] - ca.s[lo + i])
            sb = cb.s[k] + u[i, k] * (cb.s[k + 1] - cb.s[k])
            out.append((float(sa), float(sb)))
    return out


def _cross(a: FloatArray, b: FloatArray) -> FloatArray:
    result: FloatArray = a[..., 0] * b[..., 1] - a[..., 1] * b[..., 0]
    return result


def _touches(end_of: PathCurve, on: PathCurve) -> list[tuple[float, float]]:
    """Ends of open ``end_of`` lying on ``on``: (s on ``end_of``, s on ``on``)."""
    if end_of.closed:
        return []
    out = []
    for s_end in (0.0, end_of.length):
        x, y, _ = end_of.point(s_end)
        s_on, dist = on.nearest(x, y)
        if dist <= JUNCTION_REACH_M:
            out.append((s_end, s_on))
    return out


def _same_height(ca: PathCurve, sa: float, cb: PathCurve, sb: float) -> bool:
    return abs(ca.point(sa)[2] - cb.point(sb)[2]) <= JUNCTION_HEIGHT_M


def _same_junction(curves: Sequence[PathCurve], j: Junction, k: Junction) -> bool:
    return (
        (j.a, j.b) == (k.a, k.b)
        and abs(curves[j.a].offset(j.s_a, k.s_a)) <= _SAME_JUNCTION_M
        and abs(curves[j.b].offset(j.s_b, k.s_b)) <= _SAME_JUNCTION_M
    )
