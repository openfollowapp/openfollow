# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Place a traced line's points on the brightness edge the operator meant.

The wizard sends a small luma patch around each of the five sample positions
of a line (its ends and 25 / 50 / 75 %), cut straight from the full-resolution
snapshot. Along each point's perpendicular the luma is averaged over a few
pixels of the line's direction and differentiated; the strongest gradient
within the search radius is the edge. Every point of one line snaps to an edge
of the same polarity (the same dark-to-light direction), otherwise a strip of
gaffa tape would put one point on its top edge and the next on its bottom
edge, and the tape's width would read as curvature. A point with no clear edge
stays on the straight line and is reported as not snapped.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

# Where along a line its five points sit.
LINE_SAMPLE_FRACTIONS: tuple[float, ...] = (0.0, 0.25, 0.5, 0.75, 1.0)
# Pixels averaged along the line's direction at every perpendicular offset.
_TANGENT_HALF_WIDTH = 3
# Luma levels per pixel below which a gradient is not an edge.
_MIN_EDGE_STRENGTH = 6.0
# A candidate at the search radius counts this much of one at the click.
_NEAR_WEIGHT_AT_RADIUS = 0.5
# Patch side limits: a half size under this finds nothing, over it costs bandwidth.
MIN_PATCH_HALF = 16
MAX_PATCH_HALF = 64


@dataclass(frozen=True)
class SnapPatch:
    """A luma crop whose top-left pixel sits at ``(x, y)`` of the snapshot."""

    x: int
    y: int
    luma: npt.NDArray[np.float64]


@dataclass(frozen=True)
class SnappedPoint:
    x: float
    y: float
    snapped: bool


def patch_half_size(canvas_w: float) -> int:
    """Half side of the patch the wizard cuts, scaled with the snapshot width."""
    return max(MIN_PATCH_HALF, min(MAX_PATCH_HALF, round(canvas_w / 60.0)))


def search_radius(half: int) -> int:
    """How far along the perpendicular the edge may lie, for a patch of ``half``."""
    return max(4, half - _TANGENT_HALF_WIDTH - 2)


def line_sample_points(p0: npt.ArrayLike, p1: npt.ArrayLike) -> npt.NDArray[np.float64]:
    """The five nominal positions along the straight line from ``p0`` to ``p1``."""
    a = np.asarray(p0, dtype=np.float64)
    b = np.asarray(p1, dtype=np.float64)
    t = np.asarray(LINE_SAMPLE_FRACTIONS)[:, None]
    return a[None, :] + t * (b - a)[None, :]


def _bilinear(patch: SnapPatch, x: npt.NDArray[np.float64], y: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
    """Luma at snapshot coordinates, NaN outside the patch."""
    img = patch.luma
    rows, cols = img.shape
    px = x - patch.x
    py = y - patch.y
    out = np.full(px.shape, np.nan)
    inside = (px >= 0.0) & (px <= cols - 1) & (py >= 0.0) & (py <= rows - 1)
    if not inside.any():
        return out
    sx = px[inside]
    sy = py[inside]
    x0 = np.floor(sx).astype(int)
    y0 = np.floor(sy).astype(int)
    x1 = np.minimum(x0 + 1, cols - 1)
    y1 = np.minimum(y0 + 1, rows - 1)
    fx = sx - x0
    fy = sy - y0
    out[inside] = (
        img[y0, x0] * (1.0 - fx) * (1.0 - fy)
        + img[y0, x1] * fx * (1.0 - fy)
        + img[y1, x0] * (1.0 - fx) * fy
        + img[y1, x1] * fx * fy
    )
    return out


def _gradient_profile(
    patch: SnapPatch,
    centre: npt.NDArray[np.float64],
    normal: npt.NDArray[np.float64],
    tangent: npt.NDArray[np.float64],
    radius: int,
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]]:
    """Luma gradient across the line at integer offsets ``-radius..radius``."""
    offsets = np.arange(-radius - 1, radius + 2, dtype=np.float64)
    along = np.arange(-_TANGENT_HALF_WIDTH, _TANGENT_HALF_WIDTH + 1, dtype=np.float64)
    xs = centre[0] + offsets[:, None] * normal[0] + along[None, :] * tangent[0]
    ys = centre[1] + offsets[:, None] * normal[1] + along[None, :] * tangent[1]
    luma = _bilinear(patch, xs, ys)
    # Mean over the samples inside the patch; an offset with none is NaN.
    valid = np.isfinite(luma)
    counts = valid.sum(axis=1)
    sums = np.where(valid, luma, 0.0).sum(axis=1)
    profile = np.where(counts > 0, sums / np.maximum(counts, 1), np.nan)
    gradient = (profile[2:] - profile[:-2]) / 2.0
    return offsets[1:-1], gradient


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


def snap_line_to_edges(
    p0: Sequence[float],
    p1: Sequence[float],
    patches: Sequence[SnapPatch],
    *,
    radius: int,
) -> list[SnappedPoint]:
    """Snap the five points of the line ``p0`` to ``p1`` to one edge polarity.

    ``patches`` holds one patch per sample position, in :data:`LINE_SAMPLE_FRACTIONS`
    order. Raises ``ValueError`` on a degenerate line or patch count.
    """
    if len(patches) != len(LINE_SAMPLE_FRACTIONS):
        raise ValueError(f"expected {len(LINE_SAMPLE_FRACTIONS)} patches, got {len(patches)}")
    if radius < 1:
        raise ValueError("radius must be at least 1")
    a = np.asarray(p0, dtype=np.float64)
    b = np.asarray(p1, dtype=np.float64)
    tangent = b - a
    length = float(np.hypot(*tangent))
    if not np.isfinite(length) or length < 1.0:
        raise ValueError("line is too short")
    tangent = tangent / length
    normal = np.array([-tangent[1], tangent[0]])
    samples = line_sample_points(a, b)
    profiles = [_gradient_profile(patch, samples[i], normal, tangent, radius) for i, patch in enumerate(patches)]

    best_score = -np.inf
    best_picks: list[tuple[float, bool]] = []
    for polarity in (1.0, -1.0):
        picks: list[tuple[float, bool]] = []
        score = 0.0
        for offsets, gradient in profiles:
            signed = polarity * gradient
            weight = 1.0 - (1.0 - _NEAR_WEIGHT_AT_RADIUS) * np.abs(offsets) / radius
            weighted = np.where(np.isfinite(signed), signed * weight, -np.inf)
            i = int(np.argmax(weighted))
            strength = float(signed[i]) if np.isfinite(signed[i]) else 0.0
            if strength >= _MIN_EDGE_STRENGTH:
                picks.append((float(offsets[i]) + _subpixel_peak(signed, i), True))
                score += float(weighted[i])
            else:
                picks.append((0.0, False))
        if score > best_score:
            best_score = score
            best_picks = picks

    return [
        SnappedPoint(float(samples[i, 0] + off * normal[0]), float(samples[i, 1] + off * normal[1]), ok)
        for i, (off, ok) in enumerate(best_picks)
    ]
