# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Suggest the edges of a snapshot a Lens line could be traced along.

The wizard sends the snapshot's luma scaled down by ``edge_map_scale``. Thin
edges are found in it (blurred gradient, non-maximum suppression), followed
into chains pixel by pixel along the local edge direction, split where a chain
turns a corner, and kept where a chain is long, bows no more than a straight
line can through a lens, and reaches a clear edge somewhere. A chain that runs
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

# The scaled luma is at most this wide; the scale is what brings the snapshot under it.
EDGE_MAP_WIDTH = 960
MAX_EDGE_SCALE = 16
MAX_CANDIDATES = 10
# Luma per scaled pixel: a chain must reach the strong level somewhere and keep the weak one everywhere.
_STRONG_EDGE = 8.0
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
# Scaled pixels a chain may stray from the quartic through it (a parabola misses the r^4 wave).
_SMOOTH_RMS = 1.0
# Collinear pieces (directions within the angle, the gap along them) this close, as a share of
# the scaled width, are one edge interrupted by a crossing or a cable: the tape grid's lines
# would otherwise be cut at every crossing into pieces too short to offer.
_JOIN_GAP_FRACTION = 0.03
_JOIN_COSINE = math.cos(math.radians(20.0))
_JOIN_MIN_POINTS = 8
# The sharpest bend a straight line takes through a lens, as curvature times the scaled width:
# about 2 for a 100 degree lens, while a spotlight's rim is 20 and more.
_MAX_CURVATURE_WIDTH = 4.0
# A chain with this share of its points within the distance (snapshot pixels) of an accepted one repeats it.
_DUPLICATE_DISTANCE = 16.0
_DUPLICATE_FRACTION = 0.5
# Of lines running alike closer than this share of the scaled width, one is offered: the stair treads
# under a stage edge all say the same about the lens, and crowd out the lines elsewhere.
_CROWD_DISTANCE_FRACTION = 0.1
_CROWD_COSINE = math.cos(math.radians(15.0))
_OUTPUT_SPACING = 8
_BLUR = np.array([1.0, 4.0, 6.0, 4.0, 1.0]) / 16.0
_NEIGHBOURS = tuple((dx, dy) for dy in (-1, 0, 1) for dx in (-1, 0, 1) if dx or dy)


@dataclass(frozen=True)
class EdgeCandidate:
    """One suggested edge: its polyline in snapshot pixels, its length there, its mean gradient."""

    points: list[tuple[float, float]]
    length: float
    strength: float


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


def _blur(img: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
    h, w = img.shape
    padded = np.pad(img, 2, mode="edge")
    rows = np.zeros((h + 4, w))
    out = np.zeros((h, w))
    for k in range(5):
        rows += _BLUR[k] * padded[:, k : k + w]
    for k in range(5):
        out += _BLUR[k] * rows[k : k + h, :]
    return out


def _thin_edges(
    luma: npt.NDArray[np.float64],
) -> tuple[npt.NDArray[np.bool_], npt.NDArray[np.float64], npt.NDArray[np.float64], npt.NDArray[np.float64]]:
    """Edge pixels at a gradient maximum across the edge, with the magnitude and the gradient."""
    smooth = _blur(luma)
    gx = np.zeros_like(smooth)
    gy = np.zeros_like(smooth)
    gx[:, 1:-1] = (smooth[:, 2:] - smooth[:, :-2]) / 2.0
    gy[1:-1, :] = (smooth[2:, :] - smooth[:-2, :]) / 2.0
    mag = np.hypot(gx, gy)
    # The gradient direction, quantised to the neighbour pair it runs through.
    angle = np.mod(np.arctan2(gy, gx), np.pi)
    sector = np.floor((angle + np.pi / 8.0) / (np.pi / 4.0)).astype(int) % 4
    padded = np.pad(mag, 1)
    h, w = mag.shape
    shifted = {
        (dx, dy): padded[1 + dy : 1 + dy + h, 1 + dx : 1 + dx + w] for dx, dy in ((1, 0), (1, 1), (0, 1), (-1, 1))
    }
    before = np.zeros_like(mag)
    after = np.zeros_like(mag)
    for k, (dx, dy) in enumerate(((1, 0), (1, 1), (0, 1), (-1, 1))):
        mask = sector == k
        before[mask] = shifted[(dx, dy)][mask]
        after[mask] = padded[1 - dy : 1 - dy + h, 1 - dx : 1 - dx + w][mask]
    edges = (mag >= _WEAK_EDGE) & (mag >= before) & (mag > after)
    edges[0, :] = edges[-1, :] = edges[:, 0] = edges[:, -1] = False
    return edges, mag, gx, gy


def _trace_chains(
    edges: npt.NDArray[np.bool_], mag: npt.NDArray[np.float64], gx: npt.NDArray[np.float64], gy: npt.NDArray[np.float64]
) -> list[npt.NDArray[np.float64]]:
    """Follow every edge pixel into a chain, strongest first, each pixel once."""
    visited = ~edges
    safe = np.where(mag > 0.0, mag, 1.0)
    tx, ty = -gy / safe, gx / safe

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

    ys, xs = np.nonzero(edges)
    chains = []
    for i in np.argsort(-mag[ys, xs], kind="stable"):
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


def _bows_like_a_line(chain: npt.NDArray[np.float64], max_curvature: float) -> bool:
    """Whether the chain follows one quartic, bent no more than a lens bends a straight line."""
    centre = chain.mean(axis=0)
    rel = chain - centre
    _values, vectors = np.linalg.eigh(rel.T @ rel)
    axis = vectors[:, -1]
    u = rel @ axis
    v = rel @ np.array([-axis[1], axis[0]])
    design = np.column_stack([u**k for k in range(5)])
    coefficients, *_ = np.linalg.lstsq(design, v, rcond=None)
    residual = v - design @ coefficients
    rms = float(np.sqrt(np.mean(residual * residual)))
    return rms <= _SMOOTH_RMS and 2.0 * abs(float(coefficients[2])) <= max_curvature


def _join_across_gaps(
    pieces: list[npt.NDArray[np.float64]], gap: float, max_curvature: float
) -> list[npt.NDArray[np.float64]]:
    """Pieces joined end to end where one continues another across a short gap.

    Two ends are linked when they lie within ``gap``, each piece runs on toward the
    other, and the two together still bow like a line. Nearest links first, each end
    linked once, never into a ring.
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
    delta = pts[None, :, :] - pts[:, None, :]
    distance = np.linalg.norm(delta, axis=2)
    with np.errstate(invalid="ignore", divide="ignore"):
        unit = delta / distance[:, :, None]
    towards = np.sum(dirs[:, None, :] * unit, axis=2)
    back = -np.sum(dirs[None, :, :] * unit, axis=2)
    own = np.arange(2 * n) // 2
    linkable = (distance <= gap) & (towards >= _JOIN_COSINE) & (back >= _JOIN_COSINE) & (own[:, None] != own[None, :])
    order = np.argsort(distance, axis=None, kind="stable")
    group = list(range(n))

    def root(i: int) -> int:
        while group[i] != i:
            i = group[i]
        return i

    link: dict[int, int] = {}
    for flat in order:
        a, b = divmod(int(flat), 2 * n)
        pa, pb = own[a], own[b]
        if a >= b or not linkable[a, b] or a in link or b in link or root(pa) == root(pb):
            continue
        # Oriented so each piece runs into the gap: a's piece ends at a, b's piece starts at b.
        first = pieces[pa] if a % 2 == 1 else pieces[pa][::-1]
        second = pieces[pb] if b % 2 == 0 else pieces[pb][::-1]
        if not _bows_like_a_line(np.concatenate([first, second]), max_curvature):
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
    edges, mag, gx, gy = _thin_edges(luma)
    edge_map = np.where(edges, np.minimum(mag * 4.0, 255.0), 0.0).astype(np.uint8)
    min_length = _MIN_LENGTH_FRACTION * w
    max_curvature = _MAX_CURVATURE_WIDTH / w
    duplicate = _DUPLICATE_DISTANCE / scale
    crowd = _CROWD_DISTANCE_FRACTION * w
    centre = np.array([(w - 1) / 2.0, (h - 1) / 2.0])
    half_diagonal = float(np.hypot(*centre))

    pieces = []
    for chain in _trace_chains(edges, mag, gx, gy):
        for piece in _split_at_corners(chain):
            piece = piece[_END_TRIM : len(piece) - _END_TRIM]
            if len(piece) >= _JOIN_MIN_POINTS and _bows_like_a_line(piece, max_curvature):
                pieces.append(piece)
    scored: list[tuple[float, npt.NDArray[np.float64], float, float]] = []
    for piece in _join_across_gaps(pieces, _JOIN_GAP_FRACTION * w, max_curvature):
        length = _arc_length(piece)
        if length < min_length:
            continue
        strength = mag[piece[:, 1].astype(int), piece[:, 0].astype(int)]
        if float(strength.max()) < _STRONG_EDGE:
            continue
        mean = float(strength.mean())
        # A line far from the centre shows the lens most, so it is offered first.
        radius = float(np.linalg.norm(piece.mean(axis=0) - centre)) / half_diagonal
        scored.append((length * (1.0 + 2.0 * radius * radius) * math.sqrt(mean), piece, length, mean))
    scored.sort(key=lambda item: -item[0])

    kept: list[npt.NDArray[np.float64]] = []
    candidates: list[EdgeCandidate] = []
    for _score, piece, length, mean in scored:
        if len(candidates) >= limit:
            break
        if any(
            np.mean(_distance_to_polyline(piece, other) <= duplicate) >= _DUPLICATE_FRACTION
            or _runs_alike(piece, other, crowd)
            for other in kept
        ):
            continue
        kept.append(piece)
        index = np.unique(np.append(np.arange(0, len(piece), _OUTPUT_SPACING), len(piece) - 1))
        full = (piece[index] + 0.5) * scale - 0.5
        points = [(float(x), float(y)) for x, y in full]
        candidates.append(EdgeCandidate(points, length * scale, mean))
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
