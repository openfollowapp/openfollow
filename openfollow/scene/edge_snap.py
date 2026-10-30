# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Paul Hermann and Michel Honold
"""Place a traced line's points on the brightness edge the operator meant.

The wizard sends a luma band along the traced chord, cut from the
full-resolution snapshot and rectified so the chord runs along its middle row:
one column every ``step`` pixels of the chord, one row per pixel across it,
``half`` rows to either side.

A straight edge seen through a lens is a smooth curve: its offset from the
chord is a shift of either end plus the bows of the two lens terms. So the
snap only ever follows such a curve, never a path that may step from one edge
to its neighbour.

- **A hand-traced line** is anchored where it was clicked: each end takes an
  edge peak within its reach of the click, and every curve through two such
  ends, bowed no further than the strongest plausible lens bows that chord, is
  scored by the edge it runs along. Of the curves that carry an edge about as
  well as the best (:data:`_CREDIBLE`), the one whose ends lie nearest the
  clicks wins: of two parallel edges, the click says which one is meant.
- **A used suggestion** already lies on an edge: each point is looked for only
  within a few pixels of the suggestion's own sample, on the suggestion's own
  side of the contrast (:class:`NearEdge`).

Either way the curve is then fitted to the edge peaks found along it, and each
point takes the peak within a few pixels of that curve. Every point of a line
keeps one polarity (the same dark-to-light direction), otherwise a strip of
gaffa tape would put one point on its top edge and the next on its bottom edge
and the tape's width would read as curvature. A point with no clear edge there
stays on the curve, inside the picture, and is reported as not snapped.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from openfollow.scene.solver import apply_overlay_distortion, invert_overlay_distortion

FloatArray = npt.NDArray[np.float64]

# Where along a line its five points sit.
LINE_SAMPLE_FRACTIONS: tuple[float, ...] = (0.0, 0.25, 0.5, 0.75, 1.0)
# Band limits: rows to either side of the chord, and chord pixels per column.
MIN_BAND_HALF = 48
MAX_BAND_HALF = 256
MAX_BAND_STEP = 8
# Luma levels per pixel below which a gradient is not an edge, and the band's own
# noise times this factor, whichever is higher.
_MIN_EDGE_STRENGTH = 6.0
_NOISE_FACTOR = 3.5
# One column's contribution is capped here, so a single bright crossing cannot carry a curve.
_GRADIENT_CLIP = 60.0
# The lenses the two reference bows are drawn with, about the frame centre; the fitted
# factors carry the sign.
_REFERENCE_K1 = 0.2
_REFERENCE_K2 = 0.2
# Keeps the bow factors at zero for a line the lens barely bends.
_BOW_RIDGE = 1e-3
# The strongest lenses a camera on a followspot plausibly has, barrel (a 150 degree
# fisheye) and pincushion: a hand-traced curve bows no further than these bow its
# chord, times the margin, plus the slack.
_PLAUSIBLE_LENSES = ((-0.74, -0.152), (0.3, 0.0))
_BOW_MARGIN = 1.25
_BOW_SLACK = 2.0
# Pixels of bow per step of the grid the curves are scored on; the fit to the edge refines it.
_BOW_STEP = 2.0
# A curve is scored by the share of its columns with an edge peak within this many rows:
# a straight edge runs on along the curve, clutter only crosses it. The strength of the
# peaks only breaks a tie, scaled so it never outweighs one column more.
_SCORE_SLACK = 1
_STRENGTH_TIE = 1e-3
# Columns scored per curve, at most.
_SCORE_COLUMNS = 128
# End peaks per end and polarity a hand-traced line is anchored on, nearest first.
_END_CANDIDATES = 2
# An end with no peak in its reach is tried at this many offsets across it; the curve's
# fit to the edge then sets the end where the edge runs.
_FREE_END_OFFSETS = 5
# A curve whose score is at least this share of the best carries its edge: of those, the
# one anchored nearest the clicks wins.
_CREDIBLE = 0.5
# How far a clicked end may move, as a fraction of the snapshot width: the click
# says which of two parallel edges is meant, so an end only settles onto the nearest.
_END_RADIUS_DIVISOR = 120
_MIN_END_RADIUS = 4
# Pixels around the curve (and around a suggestion's sample) where a point's edge is
# looked for, as a fraction of the snapshot width: another edge further off is another line.
_WINDOW_DIVISOR = 480
_MIN_WINDOW = 4
# A peak this far (px) off the fitted curve is another edge and does not shape it; a
# point takes its peak within the point tolerance of the curve, or stays unsnapped (the
# reference bows draw the widest lenses to about 3 px).
_TRACK_TOLERANCE = 1.5
_POINT_TOLERANCE = 2.5


@dataclass(frozen=True)
class LumaBand:
    """Luma along a chord: ``luma[half, i]`` is the chord at ``i * step`` pixels from ``p0``."""

    step: int
    half: int
    luma: npt.NDArray[np.float64]


@dataclass(frozen=True)
class NearEdge:
    """The edge a used suggestion lies on: its five samples and its contrast side.

    ``polarity`` is +1 where luma rises along the chord's normal ``(-ty, tx)``, ``t``
    the unit chord from ``p0`` to ``p1``, -1 where it falls, 0 when unknown (a thin line).
    """

    points: tuple[tuple[float, float], ...]
    polarity: int


@dataclass(frozen=True)
class SnappedPoint:
    x: float
    y: float
    snapped: bool


def band_half_size(canvas_w: float) -> int:
    """Rows to either side of the chord the wizard cuts, scaled with the snapshot width.

    A half rounds up, as the wizard's ``Math.round`` does.
    """
    return max(MIN_BAND_HALF, min(MAX_BAND_HALF, math.floor(canvas_w / 10.0 + 0.5)))


def band_step(canvas_w: float) -> int:
    """Chord pixels per band column, scaled with the snapshot width; a half rounds up."""
    return max(1, min(MAX_BAND_STEP, math.floor(canvas_w / 480.0 + 0.5)))


def band_columns(length: float, step: int) -> int:
    """Columns a band of ``step`` needs to cover a chord of ``length`` pixels."""
    return int(np.ceil(length / step)) + 1


def end_search_radius(canvas_w: float) -> int:
    """How far from the click a line's end may be moved onto the edge."""
    return max(_MIN_END_RADIUS, round(canvas_w / _END_RADIUS_DIVISOR))


def point_window(canvas_w: float) -> int:
    """How far from its curve, or from its suggestion's sample, a point's edge is looked for."""
    return max(_MIN_WINDOW, round(canvas_w / _WINDOW_DIVISOR))


def line_sample_points(p0: npt.ArrayLike, p1: npt.ArrayLike) -> FloatArray:
    """The five nominal positions along the straight line from ``p0`` to ``p1``."""
    a = np.asarray(p0, dtype=np.float64)
    b = np.asarray(p1, dtype=np.float64)
    t = np.asarray(LINE_SAMPLE_FRACTIONS)[:, None]
    return np.asarray(a[None, :] + t * (b - a)[None, :], dtype=np.float64)


def band_cell_positions(
    p0: npt.ArrayLike, p1: npt.ArrayLike, step: int, half: int, cols: int
) -> tuple[FloatArray, FloatArray]:
    """Snapshot coordinates of every band cell, as ``(xs, ys)`` of shape ``(2 * half + 1, cols)``."""
    a = np.asarray(p0, dtype=np.float64)
    tangent, normal, _length = _frame(a, np.asarray(p1, dtype=np.float64))
    u = np.arange(cols, dtype=np.float64) * step
    v = np.arange(2 * half + 1, dtype=np.float64) - half
    xs = a[0] + u[None, :] * tangent[0] + v[:, None] * normal[0]
    ys = a[1] + u[None, :] * tangent[1] + v[:, None] * normal[1]
    return xs, ys


def _frame(a: FloatArray, b: FloatArray) -> tuple[FloatArray, FloatArray, float]:
    tangent = b - a
    length = float(np.hypot(*tangent))
    if not np.isfinite(length) or length < 1.0:
        raise ValueError("line is too short")
    tangent = tangent / length
    return tangent, np.array([-tangent[1], tangent[0]]), length


def _subpixel_peak(signed: FloatArray, i: int) -> float:
    """Parabolic refinement of the peak at index ``i`` (which has both neighbours) by its two neighbours."""
    left, mid, right = signed[i - 1], signed[i], signed[i + 1]
    if not (np.isfinite(left) and np.isfinite(right)):
        return 0.0
    denom = left - 2.0 * mid + right
    if denom >= 0.0:
        return 0.0
    return float(np.clip(0.5 * (left - right) / denom, -0.5, 0.5))


def _lens_bow(
    a: FloatArray,
    b: FloatArray,
    tangent: FloatArray,
    normal: FloatArray,
    length: float,
    canvas_w: float,
    canvas_h: float,
    t: FloatArray,
    k1: float,
    k2: float,
) -> FloatArray:
    """Offsets from the chord, at the fractions ``t``, of the straight line through the ends seen through
    ``(k1, k2)`` about the frame centre."""
    ends = invert_overlay_distortion(np.array([a, b]), canvas_w, canvas_h, k1, k2, 0.0, 0.0)
    s = np.linspace(0.0, 1.0, max(len(t), 64))[:, None]
    straight = ends[0][None, :] * (1.0 - s) + ends[1][None, :] * s
    rel = apply_overlay_distortion(straight, canvas_w, canvas_h, k1, k2, 0.0, 0.0) - a[None, :]
    fractions = rel @ tangent / length
    order = np.argsort(fractions)
    return np.asarray(np.interp(t, fractions[order], (rel @ normal)[order]), dtype=np.float64)


@dataclass(frozen=True)
class _Band:
    """The band's evidence and the curve basis along it."""

    grad: FloatArray  # signed gradient across the band, NaN where nothing is known
    known: FloatArray  # the same, 0 where nothing is known
    half: int
    min_strength: float
    t: FloatArray  # each column's fraction of the chord
    design: FloatArray  # columns x (bow 1, bow 2, end 0, end 1)
    step: int
    length: float

    def column(self, fraction: float) -> int:
        cols = self.grad.shape[1]
        return min(max(int(round(fraction * self.length / self.step)), 0), cols - 1)

    def peak(self, profile: FloatArray, lo: int, hi: int) -> float | None:
        """Sub-pixel row offset of the strongest peak of ``profile`` within ``lo..hi`` that stands as an edge.

        The window's own end on the flank of a peak beyond it is no peak.
        """
        rows = len(profile)
        lo, hi = max(1, lo), min(rows - 2, hi)
        if lo > hi:
            return None
        known = np.where(np.isfinite(profile), profile, -np.inf)
        rows_in = np.arange(lo, hi + 1)
        here = known[lo : hi + 1]
        crest = (here >= self.min_strength) & (here >= known[lo - 1 : hi]) & (here >= known[lo + 1 : hi + 2])
        if not crest.any():
            return None
        j = int(rows_in[crest][np.argmax(here[crest])])
        return j - self.half + _subpixel_peak(known, j)

    def fit(self, offsets: FloatArray, weights: FloatArray, reach: float) -> FloatArray:
        """The curve (offset per column) of the basis fitted to ``offsets`` by ``weights``; the ends within reach."""
        d = self.design
        ridge = np.diag([_BOW_RIDGE, _BOW_RIDGE, 0.0, 0.0]) * float(weights.sum())
        lhs = d.T @ (d * weights[:, None]) + ridge
        rhs = d.T @ (weights * offsets)
        coefficients = np.linalg.lstsq(lhs, rhs, rcond=None)[0]
        coefficients[2:] = np.clip(coefficients[2:], -reach, reach)
        return np.asarray(d @ coefficients, dtype=np.float64)

    def track(self, curve: FloatArray, polarity: float, window: int, reach: float) -> FloatArray | None:
        """The curve refitted to the edge peaks of ``polarity`` within ``window`` of ``curve``, column by column.

        A stretch where another edge runs beside this one pulls a least-squares curve
        halfway over, so the curve is fitted again to the half of the peaks it fits best,
        and then to every peak within :data:`_TRACK_TOLERANCE` of that.
        """
        profile = polarity * self.grad
        rows, cols = profile.shape
        found = np.full(cols, np.nan)
        weight = np.zeros(cols)
        for col in range(cols):
            centre = int(round(curve[col])) + self.half
            offset = self.peak(profile[:, col], centre - window, centre + window)
            if offset is not None:
                found[col] = offset
                weight[col] = min(float(profile[int(round(offset)) + self.half, col]), _GRADIENT_CLIP)
        seen = np.isfinite(found)
        if np.count_nonzero(seen) < 4:
            return None
        offsets = np.where(seen, found, 0.0)
        miss = np.abs(offsets - self.fit(offsets, weight, reach))
        best = np.where(seen & (miss <= np.median(miss[seen])), weight, 0.0)
        miss = np.abs(offsets - self.fit(offsets, best, reach))
        kept = np.where(seen & (miss <= _TRACK_TOLERANCE), weight, 0.0)
        if np.count_nonzero(kept) < 4:
            return None
        return self.fit(offsets, kept, reach)


def _prepare(
    a: FloatArray, b: FloatArray, band: LumaBand, canvas_w: float, canvas_h: float
) -> tuple[_Band, FloatArray, FloatArray, float]:
    tangent, normal, length = _frame(a, b)
    if band.step < 1 or band.half < 1:
        raise ValueError("band step and half must be positive")
    rows, cols = band.luma.shape
    if rows != 2 * band.half + 1:
        raise ValueError("band rows must be 2 * half + 1")
    if cols < 2 or abs(cols - band_columns(length, band.step)) > 1:
        raise ValueError("band columns do not match the line")
    # Cells outside the snapshot carry no evidence.
    xs, ys = band_cell_positions(a, b, band.step, band.half, cols)
    inside = (xs >= 0.0) & (xs <= canvas_w - 1.0) & (ys >= 0.0) & (ys <= canvas_h - 1.0)
    luma = np.where(inside, band.luma, np.nan)
    grad = np.full_like(luma, np.nan)
    grad[1:-1] = (luma[2:] - luma[:-2]) / 2.0
    # A peak counts as an edge where it stands out from the band's own grain.
    finite = grad[np.isfinite(grad)]
    noise = 1.4826 * float(np.median(np.abs(finite))) if finite.size else 0.0
    t = np.minimum(np.arange(cols, dtype=np.float64) * band.step / length, 1.0)
    bows = [
        _lens_bow(a, b, tangent, normal, length, canvas_w, canvas_h, t, k1, k2)
        for k1, k2 in ((_REFERENCE_K1, 0.0), (0.0, _REFERENCE_K2))
    ]
    data = _Band(
        grad=grad,
        known=np.nan_to_num(grad, nan=0.0),
        half=band.half,
        min_strength=max(_MIN_EDGE_STRENGTH, _NOISE_FACTOR * noise),
        t=t,
        design=np.column_stack([bows[0], bows[1], 1.0 - t, t]),
        step=band.step,
        length=length,
    )
    return data, tangent, normal, length


def _bow_grid(
    data: _Band, a: FloatArray, b: FloatArray, tangent: FloatArray, normal: FloatArray, canvas_w: float, canvas_h: float
) -> FloatArray:
    """Every bow (offset per column, ends at 0) a plausible lens can give this chord, on a grid of pixels."""
    t = data.t
    one = data.design[:, 0]
    scale1 = float(np.max(np.abs(one)))
    if scale1 < 1e-9:
        return np.zeros((1, len(t)))
    unit1 = one / scale1
    two = data.design[:, 1] - (data.design[:, 1] @ unit1) / (unit1 @ unit1) * unit1
    scale2 = float(np.max(np.abs(two)))
    unit2 = two / scale2 if scale2 > 1e-9 else np.zeros_like(two)
    basis = np.column_stack([unit1, unit2])
    factors: list[FloatArray] = [np.zeros(2)]
    for k1, k2 in _PLAUSIBLE_LENSES:
        bow = _lens_bow(a, b, tangent, normal, data.length, canvas_w, canvas_h, t, k1, k2)
        factors.append(np.asarray(np.linalg.lstsq(basis, bow, rcond=None)[0], dtype=np.float64))
    lo = np.min(factors, axis=0) * _BOW_MARGIN - _BOW_SLACK
    hi = np.max(factors, axis=0) * _BOW_MARGIN + _BOW_SLACK
    # A bow beyond the band leaves it; the second bow is a few pixels at most.
    lo[0], hi[0] = max(lo[0], -data.half), min(hi[0], data.half)
    grids = [np.arange(lo[i], hi[i] + 1e-9, _BOW_STEP) if scale2 > 1e-9 or i == 0 else np.zeros(1) for i in (0, 1)]
    f1, f2 = np.meshgrid(grids[0], grids[1], indexing="ij")
    return np.asarray(f1.ravel()[:, None] * unit1[None, :] + f2.ravel()[:, None] * unit2[None, :], dtype=np.float64)


def _end_candidates(data: _Band, polarity: float, end: int, reach: int) -> list[tuple[float, float]]:
    """``(offset, distance from the click)`` of the edge peaks an end may settle on, nearest first.

    The profile is the mean of the end's own column and the next, so one pixel of grain
    does not make an end. Without a peak the end is free: offsets across its reach, each
    counted as further than any peak.
    """
    cols = data.grad.shape[1]
    pair = [0, 1] if end == 0 else [cols - 1, cols - 2]
    profile = polarity * np.mean(data.grad[:, pair], axis=1)
    peaks = []
    for row in range(data.half - reach, data.half + reach + 1):
        offset = data.peak(profile, row, row)
        if offset is not None:
            peaks.append((offset, abs(offset)))
    peaks.sort(key=lambda p: p[1])
    if peaks:
        return peaks[:_END_CANDIDATES]
    return [(float(o), float(reach + 1)) for o in np.linspace(-reach, reach, _FREE_END_OFFSETS)]


def _anchored_curve(
    data: _Band, a: FloatArray, b: FloatArray, tangent: FloatArray, normal: FloatArray, canvas_w: float, canvas_h: float
) -> tuple[FloatArray, float] | None:
    """The curve a hand-traced line follows, and its polarity; None where no curve carries an edge."""
    reach = end_search_radius(canvas_w)
    bows = _bow_grid(data, a, b, tangent, normal, canvas_w, canvas_h)
    rows, cols = data.grad.shape
    sampled = np.unique(np.linspace(0, cols - 1, min(cols, _SCORE_COLUMNS)).round().astype(np.int64))
    t = data.t[sampled]
    scored: list[tuple[float, float, float, FloatArray]] = []
    for polarity in (1.0, -1.0):
        signed = np.where(np.isfinite(data.grad), polarity * data.grad, -np.inf)
        evidence = np.zeros((rows, cols))
        crest = (signed[1:-1] >= data.min_strength) & (signed[1:-1] >= signed[:-2]) & (signed[1:-1] >= signed[2:])
        evidence[1:-1] = np.where(crest, 1.0 + _STRENGTH_TIE * np.minimum(signed[1:-1], _GRADIENT_CLIP), 0.0)
        padded = np.pad(evidence, ((_SCORE_SLACK, _SCORE_SLACK), (0, 0)))
        near = np.max([padded[i : i + rows] for i in range(2 * _SCORE_SLACK + 1)], axis=0)[:, sampled]
        for c0, d0 in _end_candidates(data, polarity, 0, reach):
            for c1, d1 in _end_candidates(data, polarity, 1, reach):
                curves = bows[:, sampled] + (c0 * (1.0 - t) + c1 * t)[None, :]
                index = np.round(curves).astype(np.int64) + data.half
                valid = (index >= 0) & (index < rows)
                score = np.where(valid, near[np.clip(index, 0, rows - 1), np.arange(len(sampled))], 0.0).mean(axis=1)
                best = int(np.argmax(score))
                scored.append((float(score[best]), d0 + d1, polarity, bows[best] + c0 * (1.0 - data.t) + c1 * data.t))
    top = max(s[0] for s in scored)
    if top <= 0.0:
        return None
    credible = [s for s in scored if s[0] >= _CREDIBLE * top]
    _score, _distance, polarity, curve = min(credible, key=lambda s: (s[1], -s[0]))
    return curve, polarity


def _near_curve(
    data: _Band, offsets: FloatArray, fractions: FloatArray, polarity: int, window: int
) -> tuple[FloatArray, float]:
    """The curve through a suggestion's samples, and its polarity: the one given, or the stronger at the samples."""
    weights = np.zeros(len(data.t))
    target = np.zeros(len(data.t))
    for offset, fraction in zip(offsets, fractions, strict=True):
        col = data.column(float(fraction))
        target[col], weights[col] = offset, 1.0
    curve = data.fit(target, weights, math.inf)
    if polarity:
        return curve, float(polarity)
    strength = {}
    for sign in (1.0, -1.0):
        total = 0.0
        for offset, fraction in zip(offsets, fractions, strict=True):
            col = data.column(float(fraction))
            row = int(round(offset)) + data.half
            column = sign * data.known[max(0, row - window) : row + window + 1, col]
            total += float(np.clip(column, 0.0, _GRADIENT_CLIP).max(initial=0.0))
        strength[sign] = total
    return curve, (1.0 if strength[1.0] >= strength[-1.0] else -1.0)


def _inside(point: FloatArray, canvas_w: float, canvas_h: float) -> bool:
    return bool(0.0 <= point[0] <= canvas_w - 1.0 and 0.0 <= point[1] <= canvas_h - 1.0)


def snap_line_to_edges(
    p0: Sequence[float],
    p1: Sequence[float],
    band: LumaBand,
    canvas_w: float,
    canvas_h: float,
    *,
    near: NearEdge | None = None,
) -> list[SnappedPoint]:
    """Snap the five points of the line ``p0`` to ``p1`` onto one edge found in ``band``.

    With ``near`` the line is a used suggestion and its points stay on that suggestion's edge.
    Raises ``ValueError`` on a degenerate line, a band that does not fit it, or samples
    that do not lie along it.
    """
    a = np.asarray(p0, dtype=np.float64)
    b = np.asarray(p1, dtype=np.float64)
    data, tangent, normal, length = _prepare(a, b, band, canvas_w, canvas_h)
    reach = end_search_radius(canvas_w)
    window = point_window(canvas_w)

    if near is None:
        fractions = np.asarray(LINE_SAMPLE_FRACTIONS)
        anchors = None
        anchored = _anchored_curve(data, a, b, tangent, normal, canvas_w, canvas_h)
        fallback = line_sample_points(p0, p1)
    else:
        samples = np.asarray(near.points, dtype=np.float64)
        if samples.shape != (len(LINE_SAMPLE_FRACTIONS), 2) or not np.isfinite(samples).all():
            raise ValueError(f"near needs {len(LINE_SAMPLE_FRACTIONS)} finite points")
        if near.polarity not in (-1, 0, 1):
            raise ValueError("near polarity must be -1, 0 or 1")
        fractions = (samples - a) @ tangent / length
        anchors = (samples - a) @ normal
        if np.any(fractions < -0.01) or np.any(fractions > 1.01) or np.any(np.abs(anchors) > band.half - window - 1):
            raise ValueError("near points must lie along the line, inside its band")
        anchored = _near_curve(data, anchors, fractions, near.polarity, window)
        fallback = samples
    if anchored is None:
        return [SnappedPoint(float(x), float(y), False) for x, y in fallback]
    curve, polarity = anchored
    # Twice: the first fit draws the curve onto the edge, the second fits it to the edge. The
    # curve is already on its edge, so a parallel one at the points' window is out of reach.
    for _ in range(2):
        tracked = data.track(curve, polarity, window - 1, reach)
        if tracked is None:
            break
        curve = tracked

    profile = polarity * data.grad
    points = []
    for i, fraction in enumerate(fractions):
        col = data.column(float(fraction))
        centre = float(np.interp(fraction, data.t, curve))
        # The strongest peak close enough to the curve to be its edge; another further off is another edge.
        lo, hi = math.ceil(centre - _POINT_TOLERANCE - 1e-9), math.floor(centre + _POINT_TOLERANCE + 1e-9)
        if anchors is not None:
            # A suggestion's point stays on the suggestion's own edge.
            sample = round(float(anchors[i]))
            lo, hi = max(lo, sample - window), min(hi, sample + window)
        elif fraction in (0.0, 1.0):
            # An end stays within its reach of the click, whatever edge the curve found beside it.
            lo, hi = max(lo, -reach), min(hi, reach)
        offset = data.peak(profile[:, col], lo + data.half, hi + data.half)
        snapped = offset is not None
        if offset is None:
            # On the curve, within the window the point was looked for in.
            offset = float(np.clip(centre, lo, hi))
        position = a + fraction * (b - a) + offset * normal
        if not _inside(position, canvas_w, canvas_h):
            snapped = False
            position = np.clip(position, 0.0, [canvas_w - 1.0, canvas_h - 1.0])
        points.append(SnappedPoint(float(position[0]), float(position[1]), snapped))
    if sum(p.snapped for p in points) < 2:
        # Nothing along the curve stands out as an edge: the best curve over grain is
        # still a curve, so the line stays where it was placed.
        return [SnappedPoint(float(x), float(y), False) for x, y in fallback]
    return points
