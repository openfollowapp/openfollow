# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Paul Hermann and Michel Honold
"""Suggest the edges of a snapshot a Lens line could be traced along.

The wizard sends the snapshot's luma scaled down by ``edge_map_scale``. Thin
edges are found in it (blurred gradient, non-maximum suppression) and so are
thin lines (the ridges of a lightly blurred image, which a step edge's own
flanks never are), both followed into chains pixel by pixel along the local
direction, split where a chain turns a corner, and kept where a chain is long,
bows no more than a straight line can through a lens, and reaches a clear edge
somewhere. A chain that runs
along a stronger one (the two edges of a strip of tape, the top and the bottom
of a stage front) is dropped. The best few are returned as polylines in
snapshot pixels; whether such an edge is straight in reality is for the
operator to say.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from openfollow.lens_model import C_BOUND, pull_back_to_valid
from openfollow.scene.solver import distort_normalised_radius

# The scaled luma is at most this wide; the scale is what brings the snapshot under it. A
# soft 720p camera keeps its one-pixel deck seams only at full size.
EDGE_MAP_WIDTH = 1280
MAX_EDGE_SCALE = 16
MAX_CANDIDATES = 12
# Luma per scaled pixel: a chain must reach the strong level somewhere and keep the weak one everywhere.
_STRONG_EDGE = 8.0
# A line stronger than this is not a better line, only a brighter one.
_CLEAR_EDGE = 20.0
_WEAK_EDGE = 4.0
# A step along the chain must point this far along the local edge direction (cosine).
_FOLLOW_COSINE = 0.3
# Chord pixels to either side of a chain point that decide whether it turns a corner there.
_CORNER_WINDOW = 8
_CORNER_ANGLE = math.radians(30.0)
# Scaled pixels dropped at either end: where an edge fades or hooks into a crossing, which
# the corner cut cannot see within its window of the end.
_END_TRIM = _CORNER_WINDOW
_MIN_LENGTH_FRACTION = 0.08
# Scaled pixels a chain may stray from the quartic through it (a parabola misses the wave a corner term puts in a line).
_SMOOTH_RMS = 1.0
# Collinear pieces (directions within the angle, the gap along them) this close, as a share of
# the scaled width, are one edge interrupted by a crossing or a cable: the tape grid's lines
# would otherwise be cut at every crossing into pieces too short to offer.
_JOIN_GAP_FRACTION = 0.03
_JOIN_COSINE = math.cos(math.radians(20.0))
_JOIN_MIN_POINTS = 8
# Scaled pixels a piece may step aside where it continues another: a thin line's crest and flanks lie
# within it, a riser's seam continuing a deck's seam lies beyond it.
_JOIN_OFFSET = 1.75
# The sharpest bend a straight line takes through a lens, as curvature times the scaled width:
# about 2 for a 100 degree lens, while a spotlight's rim is 20 and more.
_MAX_CURVATURE_WIDTH = 4.0
# How far a straight line strays from its own chord depends on where it runs: little near the
# centre, up to a quarter of the chord along the edge of a wide lens. The bound is what the
# slider ends (k1 = -1.0 or 0.5, k2 = -0.8 or 0.3) bend that chord, with the lens centre at the
# frame centre or on the edge of its bound, each pulled back to a valid lens, times the margin.
# A wide arc (a round truss, a curved stage front) away from the frame edge strays further.
_SAG_LENSES = np.array(
    [
        pull_back_to_valid(k1, k2, C_BOUND * x, C_BOUND * y)
        for k1, k2 in ((-1.0, 0.0), (0.0, -0.8), (0.5, 0.0), (0.0, 0.3))
        for x in (-1.0, 0.0, 1.0)
        for y in (-1.0, 0.0, 1.0)
    ]
)
_SAG_MARGIN = 1.25
_SAG_SAMPLES = 17
# A chain with this share of its points within the distance (snapshot pixels) of an accepted one repeats it.
_DUPLICATE_DISTANCE = 16.0
_DUPLICATE_FRACTION = 0.5
# Of lines running alike closer than this share of the scaled width, one is offered: the stair treads
# under a stage edge all say the same about the lens, and crowd out the lines elsewhere.
_CROWD_DISTANCE_FRACTION = 0.1
_CROWD_COSINE = math.cos(math.radians(15.0))
_OUTPUT_SPACING = 8
_BLUR = np.array([1.0, 4.0, 6.0, 4.0, 1.0]) / 16.0
# The lighter blur the ridges are found in: the edge blur flattens a one-pixel line away.
_RIDGE_BLUR = np.array([1.0, 2.0, 1.0]) / 4.0
# A chain is a step edge, with a contrast side, when its gradient across the chord averages
# at least this share of its magnitude; a thin line's crest carries no gradient.
_STEP_SHARE = 0.5
# Puts a thin line's ridge strength beside a step edge's gradient: a line and an edge of
# the same contrast then score alike.
_RIDGE_TO_EDGE = 0.6
_SECTORS = ((1, 0), (1, 1), (0, 1), (-1, 1))
_NEIGHBOURS = tuple((dx, dy) for dy in (-1, 0, 1) for dx in (-1, 0, 1) if dx or dy)


@dataclass(frozen=True)
class EdgeCandidate:
    """One suggested edge: its polyline in snapshot pixels, its length there, its mean gradient.

    ``polarity`` is +1 where luma rises along the normal ``(-ty, tx)`` of the unit chord
    ``t`` from the first point to the last, -1 where it falls, and 0 for a thin line,
    which is brighter or darker than both its sides.
    """

    points: list[tuple[float, float]]
    length: float
    strength: float
    polarity: int


@dataclass(frozen=True)
class EdgeCandidates:
    candidates: list[EdgeCandidate]
    # Every thin edge found, scaled luma per pixel clipped to a byte, for the developer view.
    edge_map: npt.NDArray[np.uint8]


def edge_map_scale(canvas_w: float) -> int:
    """Snapshot pixels per scaled pixel; mirrored by the wizard."""
    return max(1, min(MAX_EDGE_SCALE, int(math.ceil(canvas_w / EDGE_MAP_WIDTH))))


def edge_map_size(canvas_w: float, canvas_h: float, scale: int) -> tuple[int, int]:
    """``(width, height)`` of the scaled luma the wizard sends."""
    return int(math.ceil(canvas_w / scale)), int(math.ceil(canvas_h / scale))


def _blur(img: npt.NDArray[np.float64], kernel: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
    h, w = img.shape
    n = len(kernel)
    pad = n // 2
    padded = np.pad(img, pad, mode="edge")
    rows = np.zeros((h + 2 * pad, w))
    out = np.zeros((h, w))
    for k in range(n):
        rows += kernel[k] * padded[:, k : k + w]
    for k in range(n):
        out += kernel[k] * rows[k : k + h, :]
    return out


def _suppress_across(
    strength: npt.NDArray[np.float64], nx: npt.NDArray[np.float64], ny: npt.NDArray[np.float64]
) -> npt.NDArray[np.bool_]:
    """Pixels whose strength is a maximum along their normal ``(nx, ny)``, quantised to a neighbour pair."""
    angle = np.mod(np.arctan2(ny, nx), np.pi)
    sector = np.floor((angle + np.pi / 8.0) / (np.pi / 4.0)).astype(int) % 4
    padded = np.pad(strength, 1)
    h, w = strength.shape
    before = np.zeros_like(strength)
    after = np.zeros_like(strength)
    for k, (dx, dy) in enumerate(_SECTORS):
        mask = sector == k
        before[mask] = padded[1 + dy : 1 + dy + h, 1 + dx : 1 + dx + w][mask]
        after[mask] = padded[1 - dy : 1 - dy + h, 1 - dx : 1 - dx + w][mask]
    kept = (strength >= _WEAK_EDGE) & (strength >= before) & (strength > after)
    kept[0, :] = kept[-1, :] = kept[:, 0] = kept[:, -1] = False
    return kept


Channel = tuple[npt.NDArray[np.bool_], npt.NDArray[np.float64], npt.NDArray[np.float64], npt.NDArray[np.float64]]


def _thin_edges(luma: npt.NDArray[np.float64]) -> Channel:
    """Edge pixels at a gradient maximum across the edge: mask, gradient magnitude, unit tangent."""
    smooth = _blur(luma, _BLUR)
    gx = np.zeros_like(smooth)
    gy = np.zeros_like(smooth)
    gx[:, 1:-1] = (smooth[:, 2:] - smooth[:, :-2]) / 2.0
    gy[1:-1, :] = (smooth[2:, :] - smooth[:-2, :]) / 2.0
    mag = np.hypot(gx, gy)
    safe = np.where(mag > 0.0, mag, 1.0)
    return _suppress_across(mag, gx, gy), mag, -gy / safe, gx / safe


def _polarity(
    piece: npt.NDArray[np.float64],
    mag: npt.NDArray[np.float64],
    tx: npt.NDArray[np.float64],
    ty: npt.NDArray[np.float64],
) -> int:
    """Which way luma steps across the chord of ``piece``, from the gradient ``mag * (ty, -tx)`` along it."""
    chord = piece[-1] - piece[0]
    nx, ny = -chord[1], chord[0]
    x, y = piece[:, 0].astype(int), piece[:, 1].astype(int)
    across = mag[y, x] * (ty[y, x] * nx - tx[y, x] * ny) / max(float(np.hypot(*chord)), 1e-9)
    if abs(float(across.mean())) < _STEP_SHARE * float(mag[y, x].mean()):
        return 0
    return 1 if across.mean() > 0.0 else -1


def _thin_ridges(luma: npt.NDArray[np.float64], gradient: npt.NDArray[np.float64]) -> Channel:
    """Thin line pixels, bright or dark, at a ridge maximum across the line: mask, strength, unit tangent.

    The ridge is the stronger principal curvature less the weaker, so a blob scores
    nothing, and it must beat the gradient there, which the flanks of a step edge
    never do while a line's own crest carries no gradient at all.
    """
    smooth = _blur(luma, _RIDGE_BLUR)
    fxx = np.zeros_like(smooth)
    fyy = np.zeros_like(smooth)
    fxy = np.zeros_like(smooth)
    fxx[:, 1:-1] = smooth[:, 2:] - 2.0 * smooth[:, 1:-1] + smooth[:, :-2]
    fyy[1:-1, :] = smooth[2:, :] - 2.0 * smooth[1:-1, :] + smooth[:-2, :]
    fxy[1:-1, 1:-1] = (smooth[2:, 2:] - smooth[2:, :-2] - smooth[:-2, 2:] + smooth[:-2, :-2]) / 4.0
    mean = (fxx + fyy) / 2.0
    spread = np.hypot((fxx - fyy) / 2.0, fxy)
    low, high = mean - spread, mean + spread
    bright = -low - np.abs(high)
    dark = high - np.abs(low)
    strength = np.maximum(np.maximum(bright, dark), 0.0) * _RIDGE_TO_EDGE
    # The normal is the eigenvector of the curvature that carries the line: the larger one
    # for a dark line, the other (at a right angle) for a bright one.
    theta = 0.5 * np.arctan2(2.0 * fxy, fxx - fyy) + np.where(bright >= dark, np.pi / 2.0, 0.0)
    nx, ny = np.cos(theta), np.sin(theta)
    ridges = _suppress_across(np.where(strength > gradient, strength, 0.0), nx, ny)
    return ridges, strength, -ny, nx


def _trace_chains(
    mask: npt.NDArray[np.bool_],
    strength: npt.NDArray[np.float64],
    tx: npt.NDArray[np.float64],
    ty: npt.NDArray[np.float64],
) -> list[npt.NDArray[np.float64]]:
    """Follow every marked pixel into a chain along the tangent ``(tx, ty)``, strongest first, each pixel once."""
    visited = ~mask

    def walk(x: int, y: int, sign: float) -> list[tuple[int, int]]:
        dx_dir, dy_dir = sign * tx[y, x], sign * ty[y, x]
        path: list[tuple[int, int]] = []
        while True:
            best, best_dot = None, _FOLLOW_COSINE
            # Border pixels are never edges, so every neighbour of an edge pixel is inside.
            for dx, dy in _NEIGHBOURS:
                nx, ny = x + dx, y + dy
                if visited[ny, nx]:
                    continue
                norm = math.hypot(dx, dy)
                dot = (dx * dx_dir + dy * dy_dir) / norm
                if dot > best_dot:
                    best, best_dot = (nx, ny, dx / norm, dy / norm), dot
            if best is None:
                return path
            x, y, sx, sy = best
            visited[y, x] = True
            path.append((x, y))
            # The edge direction here, pointed the way the chain runs, steadied by the step taken.
            lx, ly = tx[y, x], ty[y, x]
            if lx * sx + ly * sy < 0.0:
                lx, ly = -lx, -ly
            dx_dir, dy_dir = lx + sx, ly + sy
            norm = math.hypot(dx_dir, dy_dir)
            dx_dir, dy_dir = dx_dir / norm, dy_dir / norm

    ys, xs = np.nonzero(mask)
    chains = []
    for i in np.argsort(-strength[ys, xs], kind="stable"):
        x0, y0 = int(xs[i]), int(ys[i])
        if visited[y0, x0]:
            continue
        visited[y0, x0] = True
        back = walk(x0, y0, -1.0)
        back.reverse()
        chains.append(np.array(back + [(x0, y0)] + walk(x0, y0, 1.0), dtype=np.float64))
    return chains


def _split_at_corners(chain: npt.NDArray[np.float64]) -> list[npt.NDArray[np.float64]]:
    """The chain cut wherever the direction before a point and after it differ by a corner's angle."""
    n, win = len(chain), _CORNER_WINDOW
    if n < 2 * win + 1:
        return [chain]
    before = chain[win:-win] - chain[: n - 2 * win]
    after = chain[2 * win :] - chain[win:-win]
    cosine = np.sum(before * after, axis=1) / (np.linalg.norm(before, axis=1) * np.linalg.norm(after, axis=1))
    turn = np.arccos(np.clip(cosine, -1.0, 1.0))
    sharp = turn > _CORNER_ANGLE
    cuts = []
    i = 0
    while i < len(turn):
        if not sharp[i]:
            i += 1
            continue
        j = i
        while j < len(turn) and sharp[j]:
            j += 1
        cuts.append(win + i + int(np.argmax(turn[i:j])))
        i = j
    pieces, start = [], 0
    for cut in cuts:
        pieces.append(chain[start:cut])
        start = cut + 1
    pieces.append(chain[start:])
    return pieces


def _arc_length(chain: npt.NDArray[np.float64]) -> float:
    return float(np.sum(np.linalg.norm(np.diff(chain, axis=0), axis=1))) if len(chain) > 1 else 0.0


def _along_across(chain: npt.NDArray[np.float64]) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]]:
    """The chain's points along its main axis and across it."""
    rel = chain - chain.mean(axis=0)
    _values, vectors = np.linalg.eigh(rel.T @ rel)
    axis = vectors[:, -1]
    return rel @ axis, rel @ np.array([-axis[1], axis[0]])


@dataclass(frozen=True)
class _Picture:
    """The snapshot the scaled luma comes from: its scale and size in snapshot pixels."""

    scale: int
    width: float
    height: float

    def straight_sag(self, a: npt.NDArray[np.float64], b: npt.NDArray[np.float64]) -> float:
        """Scaled pixels a straight line seen from ``a`` to ``b`` (scaled) may stray from its chord, at most."""
        ends = (np.array([a, b]) + 0.5) * self.scale - 0.5
        half_diag = 0.5 * math.hypot(self.width, self.height)
        k1, k2, cx, cy = (_SAG_LENSES[:, i, None] for i in range(4))
        # The lens model's closed-form undistortion of both ends, then the straight line between them.
        vx = (ends[:, 0] - self.width / 2.0) / half_diag - cx
        vy = (ends[:, 1] - self.height / 2.0) / half_diag - cy
        s = vx * vx + vy * vy
        g = 1.0 / (1.0 + k1 * s + k2 * s * s)
        t = np.linspace(0.0, 1.0, _SAG_SAMPLES)
        qx = (vx * g)[:, :1] + t * np.diff(vx * g, axis=1)
        qy = (vy * g)[:, :1] + t * np.diff(vy * g, axis=1)
        r_u = np.hypot(qx, qy)
        scale = np.divide(distort_normalised_radius(r_u, k1, k2), r_u, out=np.ones_like(r_u), where=r_u > 0.0)
        px = (cx + qx * scale) * half_diag + self.width / 2.0 - ends[0, 0]
        py = (cy + qy * scale) * half_diag + self.height / 2.0 - ends[0, 1]
        chord = ends[1] - ends[0]
        across = (px * chord[1] - py * chord[0]) / max(float(np.hypot(*chord)), 1e-9)
        return _SAG_MARGIN * float(np.max(np.abs(across))) / self.scale


def _bows_like_a_line(chain: npt.NDArray[np.float64], max_curvature: float, picture: _Picture) -> bool:
    """Whether the chain follows one quartic, bent and bowed no more than a lens bends a straight line there."""
    u, v = _along_across(chain)
    design = np.column_stack([u**k for k in range(5)])
    coefficients, *_ = np.linalg.lstsq(design, v, rcond=None)
    fitted = design @ coefficients
    residual = v - fitted
    rms = float(np.sqrt(np.mean(residual * residual)))
    if rms > _SMOOTH_RMS or 2.0 * abs(float(coefficients[2])) > max_curvature:
        return False
    # The curvature at the middle passes a wide arc too; its sag from the chord does not.
    first, last = int(np.argmin(u)), int(np.argmax(u))
    span = float(u[last] - u[first])
    chord = fitted[first] + (u - u[first]) * (fitted[last] - fitted[first]) / span
    sag = float(np.max(np.abs(fitted - chord)))
    return sag <= _SMOOTH_RMS or sag <= picture.straight_sag(chain[first], chain[last]) + _SMOOTH_RMS


def _join_offset(chain: npt.NDArray[np.float64], joint: int) -> float:
    """How far the chain steps aside at point ``joint``: the step one quartic through it needs there."""
    u, v = _along_across(chain)
    design = np.column_stack([u**k for k in range(5)] + [(np.arange(len(chain)) >= joint).astype(np.float64)])
    coefficients, *_ = np.linalg.lstsq(design, v, rcond=None)
    return abs(float(coefficients[5]))


def _close_pairs(pts: npt.NDArray[np.float64], radius: float) -> tuple[npt.NDArray[np.int64], npt.NDArray[np.int64]]:
    """Index pairs ``a < b`` of points that may lie within ``radius``: those in the same or a touching cell.

    A textured picture has thousands of pieces, so each end meets only the ends in
    its own and the touching cells of a grid ``radius`` wide.
    """
    keys = np.floor(pts / radius).astype(np.int64)
    members: dict[tuple[int, int], list[int]] = {}
    for i, (cx, cy) in enumerate(keys.tolist()):
        members.setdefault((cx, cy), []).append(i)
    cells = {key: np.array(found, dtype=np.int64) for key, found in members.items()}
    firsts, seconds = [], []
    for (cx, cy), here in cells.items():
        # Half the neighbourhood, so each pair of cells is met once.
        for dx, dy in ((0, 0), (1, -1), (1, 0), (1, 1), (0, 1)):
            there = cells.get((cx + dx, cy + dy))
            if there is None:
                continue
            a, b = (grid.ravel() for grid in np.meshgrid(here, there, indexing="ij"))
            keep = a < b if dx == 0 and dy == 0 else np.ones(a.shape, dtype=bool)
            firsts.append(np.minimum(a[keep], b[keep]))
            seconds.append(np.maximum(a[keep], b[keep]))
    # Every point meets its own cell, so neither list is empty.
    return np.concatenate(firsts), np.concatenate(seconds)


def _join_across_gaps(
    pieces: list[npt.NDArray[np.float64]], gap: float, max_curvature: float, picture: _Picture
) -> list[npt.NDArray[np.float64]]:
    """Pieces joined end to end where one continues another across a short gap.

    Two ends are linked when they lie within ``gap``, each piece runs on toward the
    other, and the two together bow like one line that does not step aside where they
    meet. Nearest links first, each end linked once, never into a ring.
    """
    pieces = [piece for piece in pieces if len(piece) >= _JOIN_MIN_POINTS]
    n = len(pieces)
    if n < 2:
        return pieces
    # Each piece's two ends, with the direction it runs out of that end.
    ends = np.array([[piece[0], piece[-1]] for piece in pieces])
    out = np.array([[piece[0] - piece[_JOIN_MIN_POINTS - 1], piece[-1] - piece[-_JOIN_MIN_POINTS]] for piece in pieces])
    out /= np.linalg.norm(out, axis=2, keepdims=True)
    pts = ends.reshape(-1, 2)
    dirs = out.reshape(-1, 2)
    own = np.arange(2 * n) // 2
    end_a, end_b = _close_pairs(pts, gap)
    delta = pts[end_b] - pts[end_a]
    distance = np.linalg.norm(delta, axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        unit = delta / distance[:, None]
    towards = np.sum(dirs[end_a] * unit, axis=1)
    back = -np.sum(dirs[end_b] * unit, axis=1)
    linkable = (distance <= gap) & (towards >= _JOIN_COSINE) & (back >= _JOIN_COSINE) & (own[end_a] != own[end_b])
    end_a, end_b, distance = end_a[linkable], end_b[linkable], distance[linkable]
    group = list(range(n))

    def root(i: int) -> int:
        while group[i] != i:
            i = group[i]
        return i

    link: dict[int, int] = {}
    # Nearest first; ties in the order of the ends.
    for k in np.lexsort((end_b, end_a, distance)):
        a, b = int(end_a[k]), int(end_b[k])
        pa, pb = own[a], own[b]
        if a in link or b in link or root(pa) == root(pb):
            continue
        # Oriented so each piece runs into the gap: a's piece ends at a, b's piece starts at b.
        first = pieces[pa] if a % 2 == 1 else pieces[pa][::-1]
        second = pieces[pb] if b % 2 == 0 else pieces[pb][::-1]
        both = np.concatenate([first, second])
        if _join_offset(both, len(first)) > _JOIN_OFFSET or not _bows_like_a_line(both, max_curvature, picture):
            continue
        link[a] = b
        link[b] = a
        group[root(pa)] = root(pb)

    joined = []
    done = [False] * n
    for start in range(n):
        if done[start]:
            continue
        # Walk back to the free end of this run, then forward along the links.
        piece, end = start, 2 * start
        while end in link:
            other = link[end]
            piece, end = own[other], other ^ 1
        run = []
        while True:
            done[piece] = True
            run.append(pieces[piece] if end % 2 == 0 else pieces[piece][::-1])
            far = end ^ 1
            if far not in link:
                break
            other = link[far]
            piece, end = own[other], other
        joined.append(np.concatenate(run))
    return joined


def _runs_alike(chain: npt.NDArray[np.float64], other: npt.NDArray[np.float64], distance: float) -> bool:
    """Whether the chains' middles lie within ``distance`` of each other and their directions agree."""
    if np.linalg.norm(chain.mean(axis=0) - other.mean(axis=0)) > distance:
        return False
    a, b = chain[-1] - chain[0], other[-1] - other[0]
    return abs(float(a @ b)) >= _CROWD_COSINE * float(np.linalg.norm(a) * np.linalg.norm(b))


def _repeats(
    chain: npt.NDArray[np.float64], other: npt.NDArray[np.float64], box: npt.NDArray[np.float64], distance: float
) -> bool:
    """Whether most of the chain lies within ``distance`` of ``other``, whose box grown by it is ``box``.

    A point outside the box is farther than ``distance`` from all of ``other``, so
    only the points inside it are measured.
    """
    near = np.all((chain >= box[0]) & (chain <= box[1]), axis=1)
    if np.mean(near) < _DUPLICATE_FRACTION:
        return False
    within = np.count_nonzero(_distance_to_polyline(chain[near], other) <= distance)
    return within / len(chain) >= _DUPLICATE_FRACTION


def _distance_to_polyline(
    points: npt.NDArray[np.float64], polyline: npt.NDArray[np.float64]
) -> npt.NDArray[np.float64]:
    """Distance of each point from the nearest segment of the polyline."""
    a = polyline[:-1][None, :, :]
    d = (polyline[1:] - polyline[:-1])[None, :, :]
    rel = points[:, None, :] - a
    length2 = np.maximum(np.sum(d * d, axis=2), 1e-12)
    t = np.clip(np.sum(rel * d, axis=2) / length2, 0.0, 1.0)
    nearest = a + t[:, :, None] * d
    return np.asarray(np.min(np.linalg.norm(points[:, None, :] - nearest, axis=2), axis=1), dtype=np.float64)


def find_edge_candidates(
    luma: npt.NDArray[np.float64], scale: int, canvas_w: float, canvas_h: float, *, limit: int = MAX_CANDIDATES
) -> EdgeCandidates:
    """The best edges of the scaled luma to trace a Lens line along, in snapshot pixels.

    Raises ``ValueError`` when the luma is not the snapshot scaled by ``scale``.
    """
    if scale < 1 or scale > MAX_EDGE_SCALE:
        raise ValueError(f"scale must be within 1..{MAX_EDGE_SCALE}")
    if luma.ndim != 2 or luma.shape[::-1] != edge_map_size(canvas_w, canvas_h, scale):
        raise ValueError("luma does not match the snapshot scaled by scale")
    h, w = luma.shape
    edges, mag, etx, ety = _thin_edges(luma)
    ridges, ridge, rtx, rty = _thin_ridges(luma, mag)
    strength = np.maximum(np.where(edges, mag, 0.0), np.where(ridges, ridge, 0.0))
    edge_map = np.minimum(strength * 4.0, 255.0).astype(np.uint8)
    min_length = _MIN_LENGTH_FRACTION * w
    max_curvature = _MAX_CURVATURE_WIDTH / w
    duplicate = _DUPLICATE_DISTANCE / scale
    crowd = _CROWD_DISTANCE_FRACTION * w
    centre = np.array([(w - 1) / 2.0, (h - 1) / 2.0])
    half_diagonal = float(np.hypot(*centre))
    picture = _Picture(scale, canvas_w, canvas_h)

    pieces = []
    for channel in ((edges, mag, etx, ety), (ridges, ridge, rtx, rty)):
        for chain in _trace_chains(*channel):
            for piece in _split_at_corners(chain):
                piece = piece[_END_TRIM : len(piece) - _END_TRIM]
                if len(piece) >= _JOIN_MIN_POINTS and _bows_like_a_line(piece, max_curvature, picture):
                    pieces.append(piece)
    scored: list[tuple[float, npt.NDArray[np.float64], float, float]] = []
    for piece in _join_across_gaps(pieces, _JOIN_GAP_FRACTION * w, max_curvature, picture):
        length = _arc_length(piece)
        if length < min_length:
            continue
        along = strength[piece[:, 1].astype(int), piece[:, 0].astype(int)]
        if float(along.max()) < _STRONG_EDGE:
            continue
        mean = float(along.mean())
        # A long line far from the centre shows the lens most, so it is offered first: its bow
        # grows with the square of its length, while brightness beyond clear counts for nothing.
        radius = float(np.linalg.norm(piece.mean(axis=0) - centre)) / half_diagonal
        score = length * length * (1.0 + 2.0 * radius * radius) * math.sqrt(min(mean, _CLEAR_EDGE))
        scored.append((score, piece, length, mean))
    scored.sort(key=lambda item: -item[0])

    kept: list[tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]]] = []
    candidates: list[EdgeCandidate] = []
    for _score, piece, length, mean in scored:
        if len(candidates) >= limit:
            break
        if any(_runs_alike(piece, other, crowd) or _repeats(piece, other, box, duplicate) for other, box in kept):
            continue
        kept.append((piece, np.array([piece.min(axis=0) - duplicate, piece.max(axis=0) + duplicate])))
        index = np.unique(np.append(np.arange(0, len(piece), _OUTPUT_SPACING), len(piece) - 1))
        full = (piece[index] + 0.5) * scale - 0.5
        points = [(float(x), float(y)) for x, y in full]
        candidates.append(EdgeCandidate(points, length * scale, mean, _polarity(piece, mag, etx, ety)))
    return EdgeCandidates(candidates, edge_map)


def candidate_sample_points(
    points: Sequence[tuple[float, float]], fractions: Sequence[float]
) -> list[tuple[float, float]]:
    """Points at the given fractions of the polyline's arc length."""
    pts = np.asarray(points, dtype=np.float64)
    steps = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    cumulative = np.concatenate([[0.0], np.cumsum(steps)])
    total = cumulative[-1] if cumulative[-1] > 0.0 else 1.0
    out = []
    for fraction in fractions:
        x = float(np.interp(fraction * total, cumulative, pts[:, 0]))
        y = float(np.interp(fraction * total, cumulative, pts[:, 1]))
        out.append((x, y))
    return out
