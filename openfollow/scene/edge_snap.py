# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Place a traced line's points on the brightness edge the operator meant.

The wizard sends a luma band along the traced chord, cut from the
full-resolution snapshot and rectified so the chord runs along its middle row:
one column every ``step`` pixels of the chord, one row per pixel across it,
``half`` rows to either side. A straight edge seen through a lens is bowed, by
far more than any search around the chord's own points could cover, and with a
strong lens the bow is not even one arc (the r^4 term pushes the ends of a
line near the frame edge out while the r^2 term pulls its middle in). So the
band is searched for the highest-scoring smooth path through all its columns,
the path is smoothed by the shape a straight line can take through a radial
lens (the bows of the r^2 and r^4 terms plus a shift of either end, fitted by
least squares), and each point then takes the edge peak nearest that curve.
Every point of a line snaps to one edge polarity (the same dark-to-light
direction), otherwise a strip of gaffa tape would put one point on its top edge
and the next on its bottom edge and the tape's width would read as curvature.
The ends stay within a few pixels of the clicks: the operator placed them, and
of two parallel edges the click says which one is meant. A point with no clear
edge where the curve runs stays on the curve and is reported as not snapped.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from openfollow.scene.solver import apply_overlay_distortion, invert_overlay_distortion

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
# One column's contribution is capped here, so a single bright crossing cannot carry a path.
_GRADIENT_CLIP = 60.0
# The path may climb this many rows per chord pixel.
_PATH_SLOPE = 0.5
# Luma per row of climb, so the path runs straight where there is nothing to follow.
_PATH_BEND_PENALTY = 1.0
# Luma per row an end sits from its click: of two edges alike, the one the operator clicked nearer wins.
_END_PULL = 1.0
# The lenses the two reference bows are drawn with. Pincushion, because a barrel
# reference folds before it reaches the corners and a line ending there could not
# be undistorted; the fitted factors carry the sign.
_REFERENCE_K1 = 0.2
_REFERENCE_K2 = 0.2
# Keeps the bow factors at zero for a line the lens barely bends.
_BOW_RIDGE = 1e-3
# The path may leave the chord by the clicks' reach plus this many reference bows: the
# bow of any lens the fit accepts, so a parallel edge further off is never taken.
_CORRIDOR_K1 = 3.0
_CORRIDOR_K2 = 2.0
_CORRIDOR_MARGIN = 2.0
# How far a clicked end may move, as a fraction of the snapshot width: the click
# says which of two parallel edges is meant, so an end only settles onto the nearest.
_END_RADIUS_DIVISOR = 120
_MIN_END_RADIUS = 4
# Pixels around the fitted curve where a point's own edge peak is looked for.
_POINT_WINDOW = 6


@dataclass(frozen=True)
class LumaBand:
    """Luma along a chord: ``luma[half, i]`` is the chord at ``i * step`` pixels from ``p0``."""

    step: int
    half: int
    luma: npt.NDArray[np.float64]


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


def line_sample_points(p0: npt.ArrayLike, p1: npt.ArrayLike) -> npt.NDArray[np.float64]:
    """The five nominal positions along the straight line from ``p0`` to ``p1``."""
    a = np.asarray(p0, dtype=np.float64)
    b = np.asarray(p1, dtype=np.float64)
    t = np.asarray(LINE_SAMPLE_FRACTIONS)[:, None]
    return a[None, :] + t * (b - a)[None, :]


def band_cell_positions(
    p0: Sequence[float], p1: Sequence[float], step: int, half: int, cols: int
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]]:
    """Snapshot coordinates of every band cell, as ``(xs, ys)`` of shape ``(2 * half + 1, cols)``."""
    a = np.asarray(p0, dtype=np.float64)
    tangent, normal, _length = _frame(a, np.asarray(p1, dtype=np.float64))
    u = np.arange(cols, dtype=np.float64) * step
    v = np.arange(2 * half + 1, dtype=np.float64) - half
    xs = a[0] + u[None, :] * tangent[0] + v[:, None] * normal[0]
    ys = a[1] + u[None, :] * tangent[1] + v[:, None] * normal[1]
    return xs, ys


def _frame(
    a: npt.NDArray[np.float64], b: npt.NDArray[np.float64]
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64], float]:
    tangent = b - a
    length = float(np.hypot(*tangent))
    if not np.isfinite(length) or length < 1.0:
        raise ValueError("line is too short")
    tangent = tangent / length
    return tangent, np.array([-tangent[1], tangent[0]]), length


def _subpixel_peak(signed: npt.NDArray[np.float64], i: int) -> float:
    """Parabolic refinement of the peak at index ``i`` by its two neighbours."""
    if i <= 0 or i >= len(signed) - 1:
        return 0.0
    left, mid, right = signed[i - 1], signed[i], signed[i + 1]
    if not (np.isfinite(left) and np.isfinite(right)):
        return 0.0
    denom = left - 2.0 * mid + right
    if denom >= 0.0:
        return 0.0
    return float(np.clip(0.5 * (left - right) / denom, -0.5, 0.5))


def _reference_bow(
    a: npt.NDArray[np.float64],
    b: npt.NDArray[np.float64],
    tangent: npt.NDArray[np.float64],
    normal: npt.NDArray[np.float64],
    length: float,
    canvas_w: float,
    canvas_h: float,
    samples: int,
    k1: float,
    k2: float,
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]]:
    """``(fractions, offsets)`` of the straight line through the ends, seen through the lens ``(k1, k2)``."""
    ends = invert_overlay_distortion(np.array([a, b]), canvas_w, canvas_h, k1, k2)
    s = np.linspace(0.0, 1.0, samples)[:, None]
    straight = ends[0][None, :] * (1.0 - s) + ends[1][None, :] * s
    rel = apply_overlay_distortion(straight, canvas_w, canvas_h, k1, k2) - a[None, :]
    fractions = rel @ tangent / length
    offsets = rel @ normal
    order = np.argsort(fractions)
    return fractions[order], offsets[order]


def _best_path(
    score: npt.NDArray[np.float64], allowed_end: npt.NDArray[np.bool_], max_step: int
) -> tuple[float, npt.NDArray[np.int64]]:
    """The path through every column with the highest score, climbing at most ``max_step`` rows per column.

    Both ends must lie on an ``allowed_end`` row. Returns the score and the row per column.
    """
    rows, cols = score.shape
    steps = np.arange(-max_step, max_step + 1)
    back = np.zeros((cols, rows), dtype=np.int64)
    pull = _END_PULL * np.abs(np.arange(rows) - (rows - 1) / 2.0)
    value = np.where(allowed_end, score[:, 0] - pull, -np.inf)
    for i in range(1, cols):
        # reached[k, r] is the value of arriving at row r from row r - steps[k].
        reached = np.full((len(steps), rows), -np.inf)
        for k, d in enumerate(steps):
            if d >= 0:
                reached[k, d:] = value[: rows - d] - _PATH_BEND_PENALTY * d
            else:
                reached[k, : rows + d] = value[-d:] + _PATH_BEND_PENALTY * d
        best = np.argmax(reached, axis=0)
        value = reached[best, np.arange(rows)] + score[:, i]
        back[i] = steps[best]
    value = np.where(allowed_end, value - pull, -np.inf)
    r = int(np.argmax(value))
    total = float(value[r])
    path = np.empty(cols, dtype=np.int64)
    path[-1] = r
    for i in range(cols - 1, 0, -1):
        r -= int(back[i, r])
        path[i - 1] = r
    return total, path


def snap_line_to_edges(
    p0: Sequence[float],
    p1: Sequence[float],
    band: LumaBand,
    canvas_w: float,
    canvas_h: float,
) -> list[SnappedPoint]:
    """Snap the five points of the line ``p0`` to ``p1`` onto one bowed edge found in ``band``.

    Raises ``ValueError`` on a degenerate line or a band that does not fit it.
    """
    a = np.asarray(p0, dtype=np.float64)
    b = np.asarray(p1, dtype=np.float64)
    tangent, normal, length = _frame(a, b)
    if band.step < 1 or band.half < 1:
        raise ValueError("band step and half must be positive")
    rows, cols = band.luma.shape
    if rows != 2 * band.half + 1:
        raise ValueError("band rows must be 2 * half + 1")
    if cols < 2 or abs(cols - band_columns(length, band.step)) > 1:
        raise ValueError("band columns do not match the line")

    # Cells outside the snapshot carry no evidence.
    xs, ys = band_cell_positions(p0, p1, band.step, band.half, cols)
    inside = (xs >= 0.0) & (xs <= canvas_w - 1.0) & (ys >= 0.0) & (ys <= canvas_h - 1.0)
    luma = np.where(inside, band.luma, np.nan)
    grad = np.full_like(luma, np.nan)
    grad[1:-1] = (luma[2:] - luma[:-2]) / 2.0
    known = np.nan_to_num(grad, nan=0.0)
    half = float(band.half)
    # A peak counts as an edge where it stands out from the band's own grain.
    finite = grad[np.isfinite(grad)]
    noise = 1.4826 * float(np.median(np.abs(finite))) if finite.size else 0.0
    min_strength = max(_MIN_EDGE_STRENGTH, _NOISE_FACTOR * noise)

    # The shape a straight line can take through a radial lens: the two reference
    # bows, which bound the search and are fitted to the path afterwards.
    t = np.minimum(np.arange(cols, dtype=np.float64) * band.step / length, 1.0)
    bows = []
    for k1, k2 in ((_REFERENCE_K1, 0.0), (0.0, _REFERENCE_K2)):
        fractions, bow = _reference_bow(a, b, tangent, normal, length, canvas_w, canvas_h, max(cols, 64), k1, k2)
        bows.append(np.interp(t, fractions, bow))
    reach = end_search_radius(canvas_w)
    distance = np.abs(np.arange(rows, dtype=np.float64)[:, None] - half)
    corridor = reach + _CORRIDOR_MARGIN + _CORRIDOR_K1 * np.abs(bows[0]) + _CORRIDOR_K2 * np.abs(bows[1])
    inside_corridor = distance <= corridor[None, :]
    allowed_end = distance[:, 0] <= reach
    clipped = np.clip(known, -_GRADIENT_CLIP, _GRADIENT_CLIP)
    max_step = max(1, int(np.ceil(_PATH_SLOPE * band.step)))
    best_score, polarity = -np.inf, 1.0
    path: npt.NDArray[np.int64] = np.full(cols, band.half, dtype=np.int64)
    for sign in (1.0, -1.0):
        total, found = _best_path(np.where(inside_corridor, sign * clipped, -np.inf), allowed_end, max_step)
        if total > best_score:
            best_score, polarity, path = total, sign, found

    design = np.column_stack([bows[0], bows[1], 1.0 - t, t])
    evidence = np.clip(polarity * known[path, np.arange(cols)], 0.0, _GRADIENT_CLIP) + 1e-3
    ridge = np.diag([_BOW_RIDGE, _BOW_RIDGE, 0.0, 0.0]) * float(evidence.sum())
    lhs = design.T @ (design * evidence[:, None]) + ridge
    rhs = design.T @ (evidence * (path - half))
    coefficients = np.linalg.lstsq(lhs, rhs, rcond=None)[0]
    # The clicks say where the line ends: the fitted ends stay within their reach.
    coefficients[2:] = np.clip(coefficients[2:], -reach, reach)
    curve = design @ coefficients
    if best_score <= 0.0:
        curve = np.zeros(cols)

    points = []
    for fraction in LINE_SAMPLE_FRACTIONS:
        offset = float(np.interp(fraction, t, curve))
        col = min(max(int(round(fraction * length / band.step)), 0), cols - 1)
        profile = polarity * grad[:, col]
        centre = int(round(offset + half))
        lo, hi = max(1, centre - _POINT_WINDOW), min(rows - 2, centre + _POINT_WINDOW)
        if fraction in (0.0, 1.0):
            # An end stays within its reach of the click, whatever edge the curve found beside it.
            lo, hi = max(lo, band.half - reach), min(hi, band.half + reach)
        window = profile[lo : hi + 1]
        peak = float(np.nanmax(window)) if np.isfinite(window).any() else -np.inf
        snapped = best_score > 0.0 and peak >= min_strength
        if snapped:
            j = lo + int(np.nanargmax(window))
            offset = j - half + _subpixel_peak(np.where(np.isfinite(profile), profile, -np.inf), j)
        position = a + fraction * (b - a) + offset * normal
        points.append(SnappedPoint(float(position[0]), float(position[1]), snapped))
    return points
