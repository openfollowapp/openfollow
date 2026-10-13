# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The active paths as one network: where they cross, and how a locked marker moves through it.

Two paths cross where they meet in plan at the same height, and a path whose
end lies on another joins it there too, carried on to the curve first when it
ends on a corner point the curve rounds away. A path crossing itself is not a
junction. A locked marker moves along its path by the part of the operator's
push that points along it; at a junction it takes whichever direction matches
the push best, but leaves its own path only when the other is clearly better.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import cached_property
from typing import Any

import numpy as np
import numpy.typing as npt

from openfollow.paths.geometry import FloatArray, PathCurve

# How close a path's end must come to another path, and how close their heights must be, to meet.
JUNCTION_REACH_M = 0.2
JUNCTION_HEIGHT_M = 0.2
# The switch area: a marker this close to a junction, along its path, may steer into the other path;
# at speed, as far as it travels in SWITCH_LOOKAHEAD_S.
SWITCH_AREA_M = 0.5
SWITCH_LOOKAHEAD_S = 0.5
# A position may switch paths this much further from a junction than that: about one sample's step.
_POSITION_STEP_M = 0.15
# The other path wins only when its direction is more than this much closer to the push.
_SWITCH_MARGIN_RAD = math.radians(15.0)
_SAME_JUNCTION_M = 0.05

# Segment starts, vectors, squared lengths, ``s`` at each start, and the path each belongs to.
_SegmentIndex = tuple[FloatArray, FloatArray, FloatArray, FloatArray, FloatArray]
_PARALLEL = 1e-12
# How far past a segment's end, as a fraction of it, a crossing still counts: one through a vertex sits on both.
_ON_SEGMENT = 1e-9
_EPS = 1e-6
_MAX_LEGS = 16
# Segments per bounding box when looking for crossings, and pairs of boxes tested in one pass.
_BOX_SEGMENTS = 16
_BOX_PAIRS_PER_PASS = 512


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
        self.curves = extend_to_corners(curves)
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
        area = max(SWITCH_AREA_M, speed * SWITCH_LOOKAHEAD_S)
        time_left = dt
        for _ in range(_MAX_LEGS):
            way, to_junction = self._choose(place, wx, wy, area)
            if way is None:
                break
            if abs(to_junction) > _EPS:
                # A turn taken short of its junction runs there first, at the push's speed.
                own = self.curves[place.path]
                leg = min(speed * time_left, abs(to_junction))
                s, _ = own.advance(place.s, math.copysign(leg, to_junction))
                if leg < abs(to_junction):
                    return Step(Place(place.path, s))
                place = way.place
                time_left -= leg / speed
                continue
            curve = self.curves[way.place.path]
            rate = speed * (wx * way.dx + wy * way.dy)
            budget = rate * time_left
            ahead = self._next_junction(way.place, way.sign, area)
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
        # On a sharp corner, back is along the segment that arrived there.
        (ax, ay), (lx, ly) = curve.directions(place.s)
        release_start, release_end = self.releases[place.path]
        ways = []
        if curve.closed or place.s < curve.length - _EPS or release_end:
            ways.append(_Way(place, 1, lx, ly))
        if curve.closed or place.s > _EPS or release_start:
            ways.append(_Way(place, -1, -ax, -ay))
        return ways

    def _choose(self, place: Place, wx: float, wy: float, area: float) -> tuple[_Way | None, float]:
        """The way to go, and how far along this path its junction is when it leaves this path (else 0)."""
        stay = self._ways(place)
        other: list[tuple[_Way, float]] = []
        curve = self.curves[place.path]
        for s, partner in self._stations[place.path]:
            off = curve.offset(place.s, s)
            if abs(off) <= area:
                other.extend((way, off) for way in self._ways(partner))

        def angle(way: _Way) -> float:
            return math.acos(max(-1.0, min(1.0, wx * way.dx + wy * way.dy)))

        best = min(stay, key=angle, default=None)
        to_junction = 0.0
        challenger = min(other, key=lambda pair: angle(pair[0]), default=None)
        if challenger is not None and (best is None or angle(challenger[0]) + _SWITCH_MARGIN_RAD < angle(best)):
            best, to_junction = challenger
        if best is None or wx * best.dx + wy * best.dy <= 0.0:
            return None, 0.0
        return best, to_junction

    def _next_junction(self, place: Place, sign: int, area: float) -> float | None:
        """How far ahead the walk must ask again: where the next switch area starts, or, inside one, its junction."""
        curve = self.curves[place.path]
        ahead = []
        for s, _ in self._stations[place.path]:
            d = sign * (s - place.s)
            if curve.closed:
                d %= curve.length
            if d > area:
                ahead.append(d - area)
            elif d > _EPS:
                ahead.append(d)
        return min(ahead, default=None)

    @cached_property
    def _junction_points(self) -> dict[int, list[tuple[float, float]]]:
        """Each path's junctions in plan, in the order of its stations."""
        return {i: [self.curves[i].point(s)[:2] for s, _ in stations] for i, stations in self._stations.items()}

    def follow(self, place: Place, x: float, y: float) -> Place:
        """Where a position ``(x, y)`` puts a marker at ``place``: along its path, onto another only at a junction.

        Another path counts where it meets this one at a junction that ``(x, y)`` lies close to, and that path's
        point nearest it close along that path; the nearest point wins, and a tie stays on this path.
        """
        s, best = self.curves[place.path].nearest(x, y)
        found = Place(place.path, s)
        reach = SWITCH_AREA_M + _POSITION_STEP_M
        for (_, there), (jx, jy) in zip(self._stations[place.path], self._junction_points[place.path], strict=True):
            if math.hypot(x - jx, y - jy) > reach:
                continue
            other = self.curves[there.path]
            s_other, gap = other.nearest(x, y)
            # Measured along the other path: one passing back over the junction higher up is not near it.
            if gap < best and abs(other.offset(there.s, s_other)) <= reach:
                found, best = Place(there.path, s_other), gap
        return found


def extend_to_corners(curves: Sequence[PathCurve]) -> list[PathCurve]:
    """``curves``, each open end that lies on another path's corner point carried on to that corner's arc.

    An arc passes its corner point by, so an end within reach of the point may be out of reach of the
    curve. Such an end runs on straight to where the corner's curve passes nearest the point, at the
    same height, and so touches it; the old end becomes a sharp inner point. An end lying on another
    curve's side joins it there and stays, as running on would leave that curve. Another path's end
    touching ours does not stop it, as it still touches our curve afterwards, unless that end runs on
    to a different corner, which would part the two: then both stay.
    """
    targets = {}
    for i, curve in enumerate(curves):
        for side in () if curve.closed else (0, 1):
            target = _corner_target(curves, i, side)
            if target is not None:
                targets[(i, side)] = target
    touched = {end: _touched(curves, *end) for end in targets}
    free = {end for end in targets if all(side >= 0 for _, side in touched[end])}
    carried = {end for end in free if not any(t in free and targets[t][0] != targets[end][0] for t in touched[end])}
    out = []
    for i, curve in enumerate(curves):
        xy, z = curve.xy, curve.z
        if (i, 0) in carried:
            (tx, ty, tz) = targets[(i, 0)][1]
            xy, z = np.vstack(([(tx, ty)], xy)), np.concatenate(([tz], z))
        if (i, 1) in carried:
            (tx, ty, tz) = targets[(i, 1)][1]
            xy, z = np.vstack((xy, [(tx, ty)])), np.concatenate((z, [tz]))
        out.append(curve if xy is curve.xy else PathCurve(xy, z, False, curve.corners))
    return out


def _corner_target(
    curves: Sequence[PathCurve], i: int, side: int
) -> tuple[tuple[int, int], tuple[float, float, float]] | None:
    """The nearest corner point within reach of an end of curve ``i`` (0 start, 1 end), and where it runs on to."""
    x, y, z = curves[i].point(side * curves[i].length)
    targets = []
    for k, other in enumerate(curves):
        if k == i:
            continue
        gaps = np.hypot(*(other.corners - (x, y)).T)
        for c in np.flatnonzero(gaps <= JUNCTION_REACH_M):
            place = other.point(other.nearest(*other.corners[c])[0])
            if abs(place[2] - z) <= JUNCTION_HEIGHT_M:
                targets.append((float(gaps[c]), (k, int(c)), place))
    if not targets:
        return None
    _, corner, place = min(targets)
    return corner, place


def _touched(curves: Sequence[PathCurve], i: int, side: int) -> list[tuple[int, int]]:
    """What an end of curve ``i`` touches at its height: an open curve's end (0 / 1), or its side (-1)."""
    x, y, z = curves[i].point(side * curves[i].length)
    out = []
    for k, other in enumerate(curves):
        s, dist = other.nearest(x, y)
        if k == i or dist > JUNCTION_REACH_M or abs(other.point(s)[2] - z) > JUNCTION_HEIGHT_M:
            continue
        at_end = not other.closed and (s <= _EPS or s >= other.length - _EPS)
        out.append((k, (0 if s <= _EPS else 1) if at_end else -1))
    return out


def find_junctions(curves: Sequence[PathCurve]) -> list[Junction]:
    """Where each pair of curves crosses or touches, at the same height."""
    boxes = [_boxes(curve) for curve in curves]
    whole = [np.concatenate((b[:, :2].min(axis=0), b[:, 2:].max(axis=0))) for b in boxes]
    found: list[Junction] = []
    for a in range(len(curves)):
        for b in range(a + 1, len(curves)):
            ca, cb = curves[a], curves[b]
            if not _overlapping(whole[a][None, :], whole[b][None, :])[0, 0]:
                continue
            pair = [Junction(a, sa, b, sb) for sa, sb in _crossings(ca, cb, boxes[a], boxes[b])]
            pair += [Junction(a, sa, b, sb) for sa, sb in _touches(ca, cb)]
            pair += [Junction(a, sa, b, sb) for sb, sa in _touches(cb, ca)]
            found += _distinct_junctions(ca, cb, pair)
    return found


def _distinct_junctions(ca: PathCurve, cb: PathCurve, pair: list[Junction]) -> list[Junction]:
    """``pair`` at the same height, less each that repeats one kept before it."""
    kept: list[Junction] = []
    # Kept junctions by ``s_a`` in steps of _SAME_JUNCTION_M: a repeat lies within two steps.
    near: dict[int, list[Junction]] = {}
    for j in pair:
        if not _same_height(ca, j.s_a, cb, j.s_b):
            continue
        spans = (j.s_a - ca.length, j.s_a, j.s_a + ca.length) if ca.closed else (j.s_a,)
        steps = {_step(s) + d for s in spans for d in (-2, -1, 0, 1, 2)}
        if any(_same_junction(ca, cb, j, k) for step in steps for k in near.get(step, ())):
            continue
        kept.append(j)
        near.setdefault(_step(j.s_a), []).append(j)
    return kept


def _step(s: float) -> int:
    return math.floor(s / _SAME_JUNCTION_M)


def _boxes(curve: PathCurve) -> FloatArray:
    """The plan bounding box of each run of ``_BOX_SEGMENTS`` segments: min x, min y, max x, max y."""
    n = len(curve.xy) - 1
    starts = np.arange(0, n, _BOX_SEGMENTS)
    ends = curve.xy[np.minimum(starts + _BOX_SEGMENTS, n)]
    lo = np.minimum(np.minimum.reduceat(curve.xy[:-1], starts, axis=0), ends)
    hi = np.maximum(np.maximum.reduceat(curve.xy[:-1], starts, axis=0), ends)
    result: FloatArray = np.hstack((lo, hi))
    return result


def _overlapping(a: FloatArray, b: FloatArray) -> npt.NDArray[np.bool_]:
    """Which boxes of ``a`` come within reach of which of ``b``."""
    m = JUNCTION_REACH_M
    return (
        (a[:, None, 0] <= b[None, :, 2] + m)
        & (b[None, :, 0] <= a[:, None, 2] + m)
        & (a[:, None, 1] <= b[None, :, 3] + m)
        & (b[None, :, 1] <= a[:, None, 3] + m)
    )


def _crossings(ca: PathCurve, cb: PathCurve, boxes_a: FloatArray, boxes_b: FloatArray) -> list[tuple[float, float]]:
    """Where ``ca`` crosses ``cb`` in plan, in segment order along ``ca`` then ``cb``: (s on ``ca``, s on ``cb``)."""
    run_a, run_b = np.nonzero(_overlapping(boxes_a, boxes_b))
    offsets = np.arange(_BOX_SEGMENTS)
    pa, ra, _, _ = ca.segments()
    pb, rb, _, _ = cb.segments()
    hits: list[tuple[npt.NDArray[Any], ...]] = [(np.empty(0, dtype=np.intp),) * 2 + (np.empty(0),) * 2]
    for lo in range(0, len(run_a), _BOX_PAIRS_PER_PASS):
        ia, ib = np.broadcast_arrays(
            run_a[lo : lo + _BOX_PAIRS_PER_PASS, None, None] * _BOX_SEGMENTS + offsets[None, :, None],
            run_b[lo : lo + _BOX_PAIRS_PER_PASS, None, None] * _BOX_SEGMENTS + offsets[None, None, :],
        )
        valid = (ia < len(pa)) & (ib < len(pb))
        ia, ib = ia[valid], ib[valid]
        r, w = ra[ia], rb[ib]
        qp = pb[ib] - pa[ia]
        denom = _cross(r, w)
        with np.errstate(divide="ignore", invalid="ignore"):
            t = _cross(qp, w) / denom
            u = _cross(qp, r) / denom
        lo_t, hi_t = -_ON_SEGMENT, 1.0 + _ON_SEGMENT
        hit = (np.abs(denom) > _PARALLEL) & (t >= lo_t) & (t <= hi_t) & (u >= lo_t) & (u <= hi_t)
        hits.append((ia[hit], ib[hit], t[hit], u[hit]))
    ia, ib, t, u = (np.concatenate(part) for part in zip(*hits, strict=True))
    order = np.lexsort((ib, ia))
    # A crossing just past an end of the whole curve is at that end.
    sa = np.clip(ca.s[ia] + t * (ca.s[ia + 1] - ca.s[ia]), 0.0, ca.length)
    sb = np.clip(cb.s[ib] + u * (cb.s[ib + 1] - cb.s[ib]), 0.0, cb.length)
    return [(float(sa[k]), float(sb[k])) for k in order]


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


def _same_junction(ca: PathCurve, cb: PathCurve, j: Junction, k: Junction) -> bool:
    return abs(ca.offset(j.s_a, k.s_a)) <= _SAME_JUNCTION_M and abs(cb.offset(j.s_b, k.s_b)) <= _SAME_JUNCTION_M
