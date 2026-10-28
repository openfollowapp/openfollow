# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Measure the lens from lines that are straight in reality.

The plumb-line method: the operator traces features that are straight on
stage (a stage edge, a gaffa line, truss). Under the right lens the traced
points become collinear once undistorted, so the fit minimises each line's
perpendicular residuals about its own total-least-squares line, mapped back to
screen pixels, where the placement noise lives.

The lens centre is never measured here: it is given (typed or imported, 0 by
default) and ``k1`` / ``k2`` are fitted about it. A coarse grid over
``k1`` / ``k2`` seeds a damped Gauss-Newton refinement, because a start at zero
diverges on strong barrel distortion. A gentle prior (:data:`K1_PRIOR`,
:data:`K2_PRIOR`) draws the pair toward no lens where the lines leave it open.
``k2`` counts as measured where the lines say more about it than its prior
does; otherwise it is mostly the prior's, and the rating says how loose it is.
A point that left its line (a snap onto another
edge) is set aside before the lens is fitted, and a line that does not fit the
others is flagged. The rating is the predicted uncertainty of where
the pinhole frame's corners land on screen, in pixels, one number that
captures how many lines there are, how much of the frame they cover and how
precisely their points were placed. Another lens that fits the lines about as
well widens it to how far the two warps disagree, since the local uncertainty
sees only the minimum it sits in. Lines whose points do not reach out toward the
corners on two sides of the lens centre leave the edges of the picture unmeasured;
the result says so (``edges_measured``) and keeps its rating for the middle.
"""

from __future__ import annotations

import functools
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt

from openfollow.lens_model import C_BOUND, lens_warp_is_valid
from openfollow.scene.solver import _limit_radius, apply_overlay_distortion, invert_overlay_distortion

FloatArray = npt.NDArray[np.float64]

# Seed grid, over the pairs the sliders reach and a margin. The cost valley is a thin
# curved ridge, narrow in k2 (0.05 moves a corner by up to 55 px), so k2 is scanned
# finely and the refinement starts from several of the best cells rather than one.
_K1_SCAN = np.arange(-1.2, 0.6001, 0.05)
_K2_SCAN = np.arange(-1.0, 0.4001, 0.02)
_SEEDS = 4
# Beyond this the model describes no real lens; keeps a one-line fit bounded.
_K_BOUND = 2.0
_LM_ITERS = 40
_JACOBIAN_EPS = 1e-4
# Placement precision nobody beats; keeps a lucky fit from reporting zero uncertainty.
_SIGMA_FLOOR_PX = 1.0
# A gentle prior toward no lens, the sigma of each coefficient: real lenses keep k2 within
# a few tenths, so where the lines say little about a coefficient it is drawn toward 0
# rather than left to the noise or dropped, which trades the corners' bend into k1.
K1_PRIOR = 0.25
K2_PRIOR = 0.2
_PRIOR = (K1_PRIOR, K2_PRIOR)
# The least scatter the prior is weighed against: exact lines still feel it, barely.
_PRIOR_FLOOR_PX = 0.1
# Another minimum the lines cannot rule out: its cost within this many residual
# variances of the best, the 95 % region of a two-parameter fit.
_RIVAL_MARGIN = 6.0
# Fractions of the frame where two lenses' screen points are compared (see :func:`_rated_points`).
_RIVAL_GRID = np.linspace(0.0, 1.0, 9, dtype=np.float64)
# A line does not fit the others when, with the lens refitted without it, its RMS is above
# what placement noise gives a straight line once in a thousand, above _MISFIT_RATIO sigmas
# of the refit's own uncertainty at the line, and above the floor.
_MISFIT_TAIL = 1e-3
_MISFIT_MIN_PX = 2.0
_MISFIT_RATIO = 3.0
# A point is set aside when it misses the line through its line's other points by more
# than this many sigmas of that miss (the placement noise floored at a pixel); at most one
# a line, of a line of at least the points, and only while those others lie on a line under the
# lens the other lines agree on (an F test at this tail), so a line that bends throughout is flagged instead.
_STRAY_SIGMAS = 4.0
_STRAY_MIN_POINTS = 5
_STRAY_STRAIGHT_TAIL = 0.05
# Smallest eigenvalue (px^2 per unit parameter^2) of the information matrix below
# which the lines constrain nothing; a real line sits many orders above it.
_SINGULAR_INFORMATION = 1e-9
_MIN_POINTS_PER_LINE = 3
# The wizard sends at most five points a line; the seeding grid costs memory per point.
MAX_LINES = 32
MAX_POINTS_PER_LINE = 16
_MIN_LINES_FOR_MISFIT = 3
_MIN_LINE_SPAN_PX = 10.0
_CURVE_SAMPLES = 24
# Outer band of the frame, as a fraction of the half extent, for the coverage hint: the outer 15 % of each side.
_EDGE_BAND = 0.7
# Points an outer band needs before the hint stops asking for a line there.
_EDGE_BAND_POINTS = 5
# A point this far out (normalised to the half-diagonal) is near a corner of the picture.
_CORNER_RADIUS = 0.8
# Lines that already agree: at least this many, none misfit, their points this close (RMS px) to the fit.
_AGREE_MIN_LINES = 4
_AGREE_RMS_PX = 1.5
# The edges are measured once points reach beyond this radius (half-diagonals) from the lens
# centre on two different of its four sides (right, up, left, down); one line may reach both.
_REACH_RADIUS = 0.8
_REACH_SIDES = 2

# Free parameters of the lens ``(k1, k2, cx, cy)`` in each model; the centre is given.
_PAIR: tuple[int, ...] = (0, 1)

# Predicted corner uncertainty (px) below which each rating applies, best first.
RATING_THRESHOLDS_PX: tuple[tuple[float, str], ...] = (
    (2.5, "excellent"),
    (5.0, "good"),
    (10.0, "okay"),
    (20.0, "medium"),
)
RATING_LOW = "low"
RATINGS: tuple[str, ...] = (RATING_LOW, "medium", "okay", "good", "excellent")


@dataclass(frozen=True)
class LensLineFit:
    """One traced line's share of the result: ``dropped`` are the points set aside, by index."""

    rms_px: float
    misfit: bool
    curve: tuple[tuple[float, float], ...]
    dropped: tuple[int, ...] = ()


@dataclass(frozen=True)
class LensFitResult:
    k1: float
    k2: float
    k2_fitted: bool
    # The lens centre the pair was fitted about, in half-diagonals, as given.
    cx: float
    cy: float
    rms_px: float
    uncertainty_px: float
    rating: str
    hint: str
    lines: tuple[LensLineFit, ...]
    # Whether the lines reach out far enough to measure the picture's edges, not only its middle.
    edges_measured: bool


def rate_uncertainty(uncertainty_px: float) -> str:
    """Five-step rating from the predicted corner uncertainty."""
    if not math.isfinite(uncertainty_px):
        return RATING_LOW
    for limit, rating in RATING_THRESHOLDS_PX:
        if uncertainty_px < limit:
            return rating
    return RATING_LOW


class _Lines:
    """The traced points as flat arrays, normalised to the half-diagonal about the frame centre."""

    def __init__(self, pts: FloatArray, idx: npt.NDArray[np.int64], canvas_w: float, canvas_h: float) -> None:
        self.pts = pts
        self.idx = idx
        self.n = pts.shape[0]
        self.count = int(idx.max()) + 1 if self.n else 0
        self.width = canvas_w
        self.height = canvas_h
        self.half_diag = 0.5 * math.hypot(canvas_w, canvas_h)
        # Pixels of scatter the prior is weighed against (see :func:`_solve`).
        self.prior_px = _SIGMA_FLOOR_PX
        self.fx = canvas_w / 2.0
        self.fy = canvas_h / 2.0
        self.dx = (pts[:, 0] - self.fx) / self.half_diag
        self.dy = (pts[:, 1] - self.fy) / self.half_diag
        self.members = [np.flatnonzero(idx == i) for i in range(self.count)]
        self.sizes = np.bincount(idx, minlength=self.count).astype(np.float64)
        self.first = np.array([rows[0] for rows in self.members], dtype=np.int64)
        self.last = np.array([rows[-1] for rows in self.members], dtype=np.int64)

    @classmethod
    def from_lines(cls, lines: Sequence[Sequence[Sequence[float]]], canvas_w: float, canvas_h: float) -> _Lines:
        if not lines:
            raise ValueError("at least one line is needed")
        if len(lines) > MAX_LINES:
            raise ValueError(f"At most {MAX_LINES} lines can be fitted.")
        rows: list[FloatArray] = []
        idx: list[int] = []
        for i, line in enumerate(lines):
            arr = np.asarray(line, dtype=np.float64)
            if arr.ndim != 2 or arr.shape[1] != 2 or arr.shape[0] < _MIN_POINTS_PER_LINE:
                raise ValueError(f"line {i + 1} needs at least {_MIN_POINTS_PER_LINE} points of [x, y]")
            if arr.shape[0] > MAX_POINTS_PER_LINE:
                raise ValueError(f"line {i + 1} has more than {MAX_POINTS_PER_LINE} points")
            if not np.isfinite(arr).all():
                raise ValueError(f"line {i + 1} has a non-finite point")
            if float(np.max(np.ptp(arr, axis=0))) < _MIN_LINE_SPAN_PX:
                raise ValueError(f"line {i + 1} is shorter than {_MIN_LINE_SPAN_PX:g} px")
            rows.append(arr)
            idx.extend([i] * arr.shape[0])
        return cls(np.vstack(rows), np.asarray(idx, dtype=np.int64), canvas_w, canvas_h)

    def subset(self, keep: Sequence[int]) -> _Lines:
        """The same points, of the lines ``keep`` only, numbered in that order."""
        number = np.full(self.count, -1, dtype=np.int64)
        number[list(keep)] = np.arange(len(keep))
        rows = number[self.idx] >= 0
        out = _Lines(self.pts[rows], number[self.idx][rows], self.width, self.height)
        out.prior_px = self.prior_px
        return out

    def undistort(
        self, k1: npt.ArrayLike, k2: npt.ArrayLike, cx: float = 0.0, cy: float = 0.0
    ) -> tuple[FloatArray, FloatArray]:
        """Pinhole positions (normalised) of every point; a grid of pairs broadcasts on a trailing axis."""
        a = np.asarray(k1, dtype=np.float64)
        b = np.asarray(k2, dtype=np.float64)
        vx: FloatArray = self.dx - cx
        vy: FloatArray = self.dy - cy
        s: FloatArray = vx * vx + vy * vy
        if a.ndim:
            s, vx, vy = s[:, None], vx[:, None], vy[:, None]
            a, b = a[None, :], b[None, :]
        # The lens model's closed-form screen-to-pinhole direction.
        scale = 1.0 / (1.0 + a * s + b * s * s)
        return vx * scale, vy * scale

    def grid_cost(self, k1: FloatArray, k2: FloatArray, cx: float = 0.0, cy: float = 0.0) -> FloatArray:
        """Sum over lines of the squared perpendicular residuals in the pinhole picture, for every grid cell."""
        ux, uy = self.undistort(k1, k2, cx, cy)
        cost = np.zeros(k1.shape)
        for rows in self.members:
            x = ux[rows] - ux[rows].mean(axis=0)
            y = uy[rows] - uy[rows].mean(axis=0)
            sxx = np.sum(x * x, axis=0)
            syy = np.sum(y * y, axis=0)
            sxy = np.sum(x * y, axis=0)
            trace = sxx + syy
            det = sxx * syy - sxy * sxy
            cost += 0.5 * (trace - np.sqrt(np.maximum(trace * trace - 4.0 * det, 0.0)))
        return cost

    def _tls(
        self, ux: FloatArray, uy: FloatArray, weight: FloatArray
    ) -> tuple[FloatArray, FloatArray, FloatArray, FloatArray]:
        """Every point less its line's weighted centroid, and each line's weighted TLS direction.

        The direction runs from the line's first point toward its last. Its sign
        is otherwise free, and a sign that flips between two nearby lenses flips
        every residual, which the Jacobian's central difference reads as a slope
        thousands of times too steep.
        """
        idx = self.idx
        total = np.bincount(idx, weight, self.count)
        x = ux - (np.bincount(idx, weight * ux, self.count) / total)[idx]
        y = uy - (np.bincount(idx, weight * uy, self.count) / total)[idx]
        sxx = np.bincount(idx, weight * x * x, self.count)
        syy = np.bincount(idx, weight * y * y, self.count)
        sxy = np.bincount(idx, weight * x * y, self.count)
        theta = 0.5 * np.arctan2(2.0 * sxy, sxx - syy)
        tx, ty = np.cos(theta), np.sin(theta)
        flip = tx * (x[self.last] - x[self.first]) + ty * (y[self.last] - y[self.first]) < 0.0
        return x, y, np.where(flip, -tx, tx), np.where(flip, -ty, ty)

    def _screen_lines(self, k: FloatArray) -> tuple[FloatArray, FloatArray, FloatArray, FloatArray, FloatArray]:
        """Each line's TLS line in the pinhole picture, weighted to screen pixels, and each point's gain.

        A point's distance from its line in the pinhole picture is its screen
        distance times ``|J^T n|``, ``J`` the undistortion's Jacobian at the point
        and ``n`` the line's normal. The line is fitted once unweighted, then
        again with each point weighted by ``1 / |J^T n|^2``, so it is the line
        the screen distances themselves favour.
        """
        k1, k2, cx, cy = (float(v) for v in k)
        ux, uy = self.undistort(k1, k2, cx, cy)
        vx, vy = self.dx - cx, self.dy - cy
        s = vx * vx + vy * vy
        g = 1.0 / (1.0 + k1 * s + k2 * s * s)
        # J = g I + 2 g'(s) v v^T with g' = dg/ds, symmetric: J^T n = g n + 2 g' (v . n) v.
        slope = -2.0 * (k1 + 2.0 * k2 * s) * g * g
        weight = np.ones(self.n)
        for _ in range(2):
            x, y, tx, ty = self._tls(ux, uy, weight)
            nx, ny = -ty[self.idx], tx[self.idx]
            along = slope * (vx * nx + vy * ny)
            gain = np.hypot(g * nx + along * vx, g * ny + along * vy)
            weight = 1.0 / (gain * gain)
        return x, y, tx, ty, gain

    def residuals(self, k: FloatArray) -> FloatArray:
        """Perpendicular distance of every point from its line, in screen pixels."""
        x, y, tx, ty, gain = self._screen_lines(k)
        return np.asarray((y * tx[self.idx] - x * ty[self.idx]) / gain * self.half_diag, dtype=np.float64)

    def line_rms(self, k: FloatArray) -> list[float]:
        res = self.residuals(k)
        return [float(np.sqrt(np.mean(res[rows] ** 2))) for rows in self.members]

    def curve(self, k: FloatArray, line: int) -> tuple[tuple[float, float], ...]:
        """The curve the lens predicts for a line: its TLS line, re-distorted."""
        k1, k2, cx, cy = (float(v) for v in k)
        ux, uy = self.undistort(k1, k2, cx, cy)
        x, y, tx, ty, _gain = self._screen_lines(k)
        rows = self.members[line]
        # Any point less its offset from the centroid is the centroid.
        centre = np.array([ux[rows[0]] - x[rows[0]], uy[rows[0]] - y[rows[0]]])
        direction = np.array([tx[line], ty[line]])
        t = x[rows] * direction[0] + y[rows] * direction[1]
        ts = np.linspace(t.min(), t.max(), _CURVE_SAMPLES)
        straight = centre[None, :] + ts[:, None] * direction[None, :]
        straight_px = np.column_stack(
            [self.fx + straight[:, 0] * self.half_diag, self.fy + straight[:, 1] * self.half_diag]
        )
        bowed = apply_overlay_distortion(straight_px, self.width, self.height, k1, k2, cx, cy)
        return tuple((float(x), float(y)) for x, y in bowed)


def _in_bounds(k: FloatArray) -> bool:
    return bool(abs(k[0]) <= _K_BOUND and abs(k[1]) <= _K_BOUND and lens_warp_is_valid(*(float(v) for v in k)))


def _residuals(lines: _Lines, k: FloatArray, free: Sequence[int]) -> FloatArray:
    """The points' residuals, and the prior's on each free coefficient, in pixels of placement noise."""
    prior = [lines.prior_px * float(k[i]) / _PRIOR[i] for i in free]
    return np.append(lines.residuals(k), prior)


def _jacobian(lines: _Lines, k: FloatArray, free: Sequence[int], *, prior: bool = True) -> FloatArray:
    cols = []
    for i in free:
        kp = k.copy()
        km = k.copy()
        kp[i] += _JACOBIAN_EPS
        km[i] -= _JACOBIAN_EPS
        if prior:
            cols.append((_residuals(lines, kp, free) - _residuals(lines, km, free)) / (2.0 * _JACOBIAN_EPS))
        else:
            cols.append((lines.residuals(kp) - lines.residuals(km)) / (2.0 * _JACOBIAN_EPS))
    return np.column_stack(cols)


def _refine(lines: _Lines, k0: FloatArray, free: Sequence[int]) -> FloatArray:
    """Damped Gauss-Newton (Levenberg-Marquardt) on the ``free`` parameters from ``k0``, inside the valid region."""
    k: FloatArray = k0.copy()
    res = _residuals(lines, k, free)
    cost = float(res @ res)
    lam = 1e-3
    index = list(free)
    for _ in range(_LM_ITERS):
        jac = _jacobian(lines, k, index)
        normal = jac.T @ jac
        grad = jac.T @ res
        delta: FloatArray = np.zeros(len(index))
        improved = False
        for _attempt in range(12):
            # The damping term keeps this invertible however flat the residuals are.
            damped = normal + lam * np.diag(np.diag(normal) + 1e-12)
            delta = np.asarray(-np.linalg.solve(damped, grad), dtype=np.float64)
            cand: FloatArray = k.copy()
            cand[index] += delta
            if not _in_bounds(cand):
                lam *= 10.0
                continue
            cand_res = _residuals(lines, cand, free)
            cand_cost = float(cand_res @ cand_res)
            if cand_cost < cost:
                k, res, cost = cand, cand_res, cand_cost
                lam = max(lam / 10.0, 1e-9)
                improved = True
                break
            lam *= 10.0
        if not improved or float(np.max(np.abs(delta))) < 1e-7:
            break
    return k


def _cost(lines: _Lines, k: FloatArray, free: Sequence[int] = ()) -> float:
    """The points' squared residuals, plus the prior's on each coefficient in ``free``."""
    res = _residuals(lines, k, free)
    return float(res @ res)


def _residual_dof(lines: _Lines, n_params: int) -> int:
    """Degrees of freedom of the residuals: the points less each line's own two and the lens's."""
    return max(lines.n - n_params - 2 * lines.count, 1)


def _residual_variance(lines: _Lines, k: FloatArray, n_params: int) -> float:
    """Variance of one point's residual at ``k``, floored at one pixel."""
    res = lines.residuals(k)
    return max(float(res @ res) / _residual_dof(lines, n_params), _SIGMA_FLOOR_PX**2)


def _beta_fraction(a: float, b: float, x: float) -> float:
    """The continued fraction of the regularised incomplete beta function (modified Lentz).

    Called only for ``x <= (a + 1) / (a + b + 2)``, where it converges within a few dozen terms.
    """
    tiny = 1e-300
    c, d = 1.0, 1.0 - (a + b) * x / (a + 1.0)
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    # Capped, and a NaN ends it too: the comparison is False for it.
    m, step = 0, 0.0
    while m < 400 and abs(step - 1.0) >= 1e-14:
        m += 1
        for num in (
            m * (b - m) * x / ((a + 2 * m - 1) * (a + 2 * m)),
            -(a + m) * (a + b + m) * x / ((a + 2 * m) * (a + 2 * m + 1)),
        ):
            d = 1.0 + num * d
            d = 1.0 / (d if abs(d) > tiny else tiny)
            c = 1.0 + num / c
            c = c if abs(c) > tiny else tiny
            h *= d * c
        step = d * c
    return h


def _beta_regularised(a: float, b: float, x: float) -> float:
    """The regularised incomplete beta function ``I_x(a, b)``, for ``0 < x < 1``."""
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _beta_fraction(a, b, x) / a
    return 1.0 - front * _beta_fraction(b, a, 1.0 - x) / b


@functools.lru_cache(maxsize=1024)
def _f_upper(d1: int, d2: int, tail: float) -> float:
    """The point of the F distribution with ``d1`` / ``d2`` degrees of freedom that ``tail`` of it lies above."""
    lo, hi = 0.0, 1e7
    for _ in range(80):
        f = math.sqrt(lo * hi) if lo > 0.0 else hi / 1e6
        above = 1.0 - _beta_regularised(d1 / 2.0, d2 / 2.0, d1 * f / (d1 * f + d2))
        lo, hi = (f, hi) if above > tail else (lo, f)
    return hi


def _covariance(lines: _Lines, k: FloatArray, free: Sequence[int], *, prior: bool = True) -> FloatArray:
    """Covariance of the ``free`` parameters at ``k`` from the residual scatter, floored at one pixel.

    With ``prior`` it is the posterior's, the prior on ``k2`` included; without, the lines' own.
    """
    sigma2 = _residual_variance(lines, k, len(free))
    jac = _jacobian(lines, k, free, prior=False)
    normal = jac.T @ jac
    # A line through the centre moves along itself under the warp, so no
    # parameter changes a residual: the lens is then unbounded, not merely loose.
    if float(np.linalg.eigvalsh(normal).min()) <= _SINGULAR_INFORMATION:
        return np.full((len(free), len(free)), np.inf)
    information = normal / sigma2
    if prior:
        information += np.diag([1.0 / _PRIOR[i] ** 2 for i in free])
    return np.asarray(np.linalg.inv(information), dtype=np.float64)


def _rated_points(lines: _Lines, k: FloatArray, fractions: FloatArray) -> FloatArray:
    """Pinhole points (px) where the rating measures the lens, on a grid of ``fractions`` of the frame.

    The pinhole frame's own points, where the rating thresholds were set; one that
    lands outside the picture (a pincushion lens) is taken where the picture ends
    instead, since no line can reach past it.
    """
    gx, gy = np.meshgrid(fractions * lines.width, fractions * lines.height)
    grid = np.column_stack([gx.ravel(), gy.ravel()])
    k1, k2, cx, cy = (float(v) for v in k)
    seen = apply_overlay_distortion(grid, lines.width, lines.height, k1, k2, cx, cy)
    beyond = np.any((seen < 0.0) | (seen > np.array([lines.width, lines.height])), axis=1)
    if not beyond.any():
        return grid
    edge = invert_overlay_distortion(grid[beyond], lines.width, lines.height, k1, k2, cx, cy)
    grid[beyond] = edge
    return grid


def _seen(lines: _Lines, pinhole: FloatArray, k: FloatArray) -> FloatArray:
    """Where pinhole points land on screen under the lens ``k``."""
    k1, k2, cx, cy = (float(v) for v in k)
    return np.asarray(apply_overlay_distortion(pinhole, lines.width, lines.height, k1, k2, cx, cy), dtype=np.float64)


def _corner_uncertainty_px(lines: _Lines, k: FloatArray, cov: FloatArray, free: Sequence[int]) -> float:
    """Predicted sigma of where the pinhole frame's corners land on screen, the largest of the four.

    The corners are rated points (:func:`_rated_points`); the covariance is that
    of the ``free`` parameters only. With the centre at 0 and the pair free, a
    corner's screen radius ``r`` moves by ``(r s, r s^2) / P(s)`` per unit of
    ``(k1, k2)``, ``s = r^2``, ``P`` the lens model's slope term.
    """
    corners = _rated_points(lines, k, np.array([0.0, 1.0]))
    cols = []
    for i in free:
        kp = k.copy()
        km = k.copy()
        kp[i] += _JACOBIAN_EPS
        km[i] -= _JACOBIAN_EPS
        cols.append((_seen(lines, corners, kp) - _seen(lines, corners, km)) / (2 * _JACOBIAN_EPS))
    jac = np.stack(cols, axis=-1)
    with np.errstate(invalid="ignore", over="ignore"):
        var = np.einsum("caf,fg,cag->c", jac, cov, jac)
    if not np.all(np.isfinite(var)):
        return math.inf
    return math.sqrt(max(float(var.max()), 0.0))


def _seeds(lines: _Lines, cx: float, cy: float) -> list[FloatArray]:
    """The lowest-cost grid cells about a fixed centre, at most one per k1 column so the starts differ."""
    g1, g2 = np.meshgrid(_K1_SCAN, _K2_SCAN, indexing="ij")
    k1: FloatArray = np.asarray(g1.ravel(), dtype=np.float64)
    k2: FloatArray = np.asarray(g2.ravel(), dtype=np.float64)
    valid = _limit_radius(k1, k2) > 1.0 + math.hypot(cx, cy)
    k1, k2 = k1[valid], k2[valid]
    cost = lines.grid_cost(k1, k2, cx, cy)
    best_per_column: dict[float, int] = {}
    for i in np.argsort(cost):
        best_per_column.setdefault(float(k1[i]), int(i))
    return [np.array([k1[i], k2[i], cx, cy]) for i in list(best_per_column.values())[:_SEEDS]]


Minima = tuple[tuple[FloatArray, float], ...]


@dataclass(frozen=True)
class _Solution:
    k: FloatArray
    free: tuple[int, ...]
    cov: FloatArray
    # Every minimum this model's starts reached, the best included, with its cost.
    minima: Minima
    # Whether the lines, not the prior, set k2.
    k2_fitted: bool


def _fit(lines: _Lines, starts: Sequence[FloatArray], free: Sequence[int]) -> tuple[FloatArray, Minima]:
    """Refine from every start; the lowest-cost lens and every minimum reached."""
    minima = []
    for start in starts:
        k = _refine(lines, start, free)
        minima.append((k, _cost(lines, k, free)))
    return min(minima, key=lambda m: m[1])[0], tuple(minima)


def _solve(lines: _Lines, cx: float, cy: float) -> _Solution:
    """Fit the pair about the given centre, drawn toward no lens by its prior.

    The prior is a belief about lenses, the residuals are pixels: it weighs against the
    points' own scatter, so lines placed to a tenth of a pixel are barely drawn toward 0.
    A loose k2 is kept as drawn, never dropped: dropped, the corners' bend goes into k1,
    and the middle of the picture bends wrong.
    """
    k, minima = _fit(lines, _seeds(lines, cx, cy), _PAIR)
    res = lines.residuals(k)
    lines.prior_px = max(math.sqrt(float(res @ res) / _residual_dof(lines, len(_PAIR))), _PRIOR_FLOOR_PX)
    k, minima = _fit(lines, [m for m, _cost in minima], _PAIR)
    told = _covariance(lines, k, _PAIR, prior=False)
    measured = math.sqrt(max(float(told[1, 1]), 0.0)) < K2_PRIOR
    return _Solution(k, _PAIR, _covariance(lines, k, _PAIR), minima, measured)


def _refit_variance(lines: _Lines, k_rest: FloatArray, cov: FloatArray, rows: npt.NDArray[np.int64]) -> float:
    """Mean variance (px^2) of the residuals at ``rows`` that the uncertainty ``cov`` of a refit ``k_rest`` gives."""
    sensitivity = _jacobian(lines, k_rest, _PAIR, prior=False)[rows]
    return float(np.mean(np.einsum("pf,fg,pg->p", sensitivity, cov, sensitivity)))


def _through(
    x: FloatArray, y: FloatArray, gain: FloatArray, rows: npt.NDArray[np.int64]
) -> tuple[float, float, float, float, FloatArray, FloatArray]:
    """The line through ``rows`` that their screen distances favour: centroid, direction, and their offsets."""
    weight = 1.0 / (gain[rows] * gain[rows])
    mx, my = float(np.average(x[rows], weights=weight)), float(np.average(y[rows], weights=weight))
    ox, oy = x[rows] - mx, y[rows] - my
    theta = 0.5 * math.atan2(2.0 * float(np.sum(weight * ox * oy)), float(np.sum(weight * (ox * ox - oy * oy))))
    return mx, my, math.cos(theta), math.sin(theta), ox, oy


def _line_runs_straight(lines: _Lines, k: FloatArray, row: int) -> bool:
    """Whether the other points of ``row``'s line lie on a line, under the lens the other lines agree on.

    The test is an F test of their bend against the others' scatter (floored at
    :data:`_PRIOR_FLOOR_PX`) plus the refit's own uncertainty at those points.
    Judged under the lens the line itself pulled its way, a bowed line's other
    points run straight, and a straight line elsewhere takes the blame.
    """
    i = int(lines.idx[row])
    rows = lines.members[i]
    others = rows[rows != row]
    # A line alone is judged under its own fit; lines that pin the lens nowhere add no spread.
    rest, k_rest, spread = lines, k, 0.0
    if lines.count >= 2:
        rest = lines.subset([j for j in range(lines.count) if j != i])
        k_rest = _refine(rest, k, _PAIR)
        cov = _covariance(rest, k_rest, _PAIR)
        if np.all(np.isfinite(cov)):
            spread = _refit_variance(lines, k_rest, cov, others)
    x, y, _tx, _ty, gain = lines._screen_lines(k_rest)
    _mx, _my, tx, ty, ox, oy = _through(x, y, gain, others)
    bend = float(np.sum(((oy * tx - ox * ty) / gain[others] * lines.half_diag) ** 2))
    res = rest.residuals(k_rest)
    dof = max(float(np.sum(rest.sizes - 2.0)), 1.0)
    scatter = 1.4826 * float(np.median(np.abs(res))) * math.sqrt(rest.n / dof)
    free = len(others) - 2
    return bend / free <= _f_upper(free, round(dof), _STRAY_STRAIGHT_TAIL) * (
        max(scatter, _PRIOR_FLOOR_PX) ** 2 + spread
    )


def _strays(lines: _Lines, k: FloatArray) -> list[int]:
    """Rows of the points that left their line under the lens ``k``, the worst of each line at most.

    Each point is measured against the line through its line's other points in the
    pinhole picture, in screen pixels, and judged against the noise that miss would
    have from placement alone: the points' own scatter (a robust sigma, so the strays
    do not hide themselves) inflated by how far the others' line is extrapolated.
    Where the others bend too, the line bends throughout: setting its end aside would
    straighten it, so nothing is (:func:`_line_runs_straight`).
    """
    x, y, _tx, _ty, gain = lines._screen_lines(k)
    res = lines.residuals(k)
    dof = max(float(np.sum(lines.sizes - 2.0)), 1.0)
    sigma = max(_SIGMA_FLOOR_PX, 1.4826 * float(np.median(np.abs(res))) * math.sqrt(lines.n / dof))
    found = []
    for rows in lines.members:
        if len(rows) < _STRAY_MIN_POINTS:
            continue
        worst, worst_z = -1, _STRAY_SIGMAS
        for row in rows:
            mx, my, tx, ty, ox, oy = _through(x, y, gain, rows[rows != row])
            miss = abs((y[row] - my) * tx - (x[row] - mx) * ty) / gain[row] * lines.half_diag
            along = ox * tx + oy * ty
            at = (x[row] - mx) * tx + (y[row] - my) * ty
            leverage = 1.0 / (len(rows) - 1) + at * at / max(float(np.sum(along * along)), 1e-18)
            z = miss / (sigma * math.sqrt(1.0 + leverage))
            if z > worst_z:
                worst, worst_z = int(row), z
        if worst >= 0 and _line_runs_straight(lines, k, worst):
            found.append(worst)
    return found


def _rival_spread_px(lines: _Lines, solution: _Solution) -> float:
    """How far apart the fit and a lens fitting the lines about as well put a point of the pinhole frame.

    With a strong lens traced only along the top and bottom edges, the cost
    valley has two minima a pixel of placement noise cannot tell apart, tens of
    pixels apart at the frame's corners, and each is sharp enough to rate
    excellent.
    """
    margin = _RIVAL_MARGIN * _residual_variance(lines, solution.k, len(solution.free))
    best = min(cost for _, cost in solution.minima)
    pinhole = _rated_points(lines, solution.k, _RIVAL_GRID)
    seen = _seen(lines, pinhole, solution.k)
    spread = 0.0
    for other, cost in solution.minima:
        if cost - best <= margin:
            gap = _seen(lines, pinhole, other) - seen
            spread = max(spread, float(np.max(np.hypot(gap[:, 0], gap[:, 1]))))
    return spread


def _misfit_excess(lines: _Lines, k: FloatArray) -> list[tuple[float, float, int]]:
    """Per line, how far it misses the lens the others agree on, and how much those others scatter.

    Each entry is the line's RMS under the others' fit over what would still count as
    fitting (above 1 it does not fit), and the others' residual variance under that fit
    with its degrees of freedom. Placement noise is judged by the line's own degrees of
    freedom (its points less its own line's two), an F test against the others' scatter.
    The floor sits a little above what the test asks where the scatter is at the 1 px
    placement floor.
    """
    excess = []
    for i in range(lines.count):
        rest = lines.subset([j for j in range(lines.count) if j != i])
        k_rest = _refine(rest, k, _PAIR)
        cov = _covariance(rest, k_rest, _PAIR)
        if not np.all(np.isfinite(cov)):
            excess.append((0.0, math.inf, 1))
            continue
        rms_line = lines.line_rms(k_rest)[i]
        predicted = math.sqrt(_refit_variance(lines, k_rest, cov, lines.members[i]))
        size = int(lines.sizes[i])
        free = size - 2
        res = rest.residuals(k_rest)
        dof = _residual_dof(rest, len(_PAIR))
        variance = float(res @ res) / dof
        noise = math.sqrt(free / size * _f_upper(free, dof, _MISFIT_TAIL) * variance)
        excess.append((rms_line / max(_MISFIT_MIN_PX, noise, _MISFIT_RATIO * predicted), variance, dof))
    return excess


def _misfits(lines: _Lines, k: FloatArray) -> list[bool]:
    """Leave-one-out: a line that only fits while it is pulling the fit its way.

    The pair is refitted without the line from the full solution, its prior keeping
    two remaining lines from being overfitted into agreeing with anything; a line
    alone in its part of the picture sets k2 there, and only a refit of both
    coefficients sees it bent. The line's residual is judged against what the
    refit's own uncertainty predicts at that line: the longest, most curved line
    moves the most when it is left out, and that is the rest's ignorance, not the
    line's fault. A bent line drags the others off too, so one line is set aside and
    the rest judged again without it, until every line left fits: of
    the lines that do not fit, the one that misses most, among those without which
    the others agree about as well as without any (an F test of their scatters).
    Left out, a line a bent one leans on can miss more than the bent one, but the
    others then scatter.
    """
    kept = list(range(lines.count))
    flags = [False] * lines.count
    while len(kept) >= _MIN_LINES_FOR_MISFIT:
        subset = lines.subset(kept)
        judged = _misfit_excess(subset, k)
        over = [j for j, (excess, _variance, _dof) in enumerate(judged) if excess > 1.0]
        if not over:
            break
        _excess, best, best_dof = min((judged[j] for j in over), key=lambda judgement: judgement[1])
        agree = [j for j in over if judged[j][1] <= _f_upper(judged[j][2], best_dof, _MISFIT_TAIL) * best]
        flags[kept.pop(max(agree, key=lambda j: judged[j][0]))] = True
    return flags


def _reaches_out(lines: _Lines, cx: float, cy: float) -> bool:
    """Whether points reach beyond :data:`_REACH_RADIUS` of the lens centre on two different of its sides."""
    vx, vy = lines.dx - cx, lines.dy - cy
    far = np.hypot(vx, vy) > _REACH_RADIUS
    sides = np.floor((np.degrees(np.arctan2(-vy[far], vx[far])) + 45.0) / 90.0) % 4
    return len(set(sides.astype(int).tolist())) >= _REACH_SIDES


def _weak_edges(pts: FloatArray, canvas_w: float, canvas_h: float) -> list[str]:
    """Every edge whose outer band holds too few points, weakest first."""
    cx, cy = canvas_w / 2.0, canvas_h / 2.0
    bands = {
        "top": int(np.sum(pts[:, 1] < cy - _EDGE_BAND * cy)),
        "bottom": int(np.sum(pts[:, 1] > cy + _EDGE_BAND * cy)),
        "left": int(np.sum(pts[:, 0] < cx - _EDGE_BAND * cx)),
        "right": int(np.sum(pts[:, 0] > cx + _EDGE_BAND * cx)),
    }
    order = list(bands)
    weak = [name for name in order if bands[name] < _EDGE_BAND_POINTS]
    return sorted(weak, key=lambda name: (bands[name], order.index(name)))


def _edges_phrase(weak: Sequence[str]) -> str:
    if len(weak) == 4:
        return "lines near all edges of the picture"
    if len(weak) == 1:
        return f"a line near the {weak[0]} edge"
    return f"lines near the {', '.join(weak[:-1])} and {weak[-1]} edges"


def coverage_hint(
    lines: Sequence[Sequence[Sequence[float]]],
    canvas_w: float,
    canvas_h: float,
    *,
    rating: str,
    k2_fitted: bool,
    rms_px: float,
    misfit: bool,
) -> str:
    """One sentence naming what would improve or confirm the fit, empty when nothing is missing.

    A line near an edge is asked for only if possible at every rating: a picture may
    show nothing straight there (a crowd, rows of seats). Rated good or better it
    only confirms the fit. Below that, lines that already agree may be right; a
    corner line is required for that, because lines that never reach a corner
    agree with a wrong lens just as well.
    """
    if len(lines) < 2:
        return "Add a second line in another part of the picture."
    pts = np.asarray([p for line in lines for p in line], dtype=np.float64)
    radius = np.hypot(pts[:, 0] - canvas_w / 2.0, pts[:, 1] - canvas_h / 2.0) / (0.5 * math.hypot(canvas_w, canvas_h))
    if not k2_fitted and not bool(np.any(radius > _CORNER_RADIUS)):
        return "Corner correction needs a line that reaches a corner of the picture."
    weak = _weak_edges(pts, canvas_w, canvas_h)
    if not weak:
        return ""
    where = _edges_phrase(weak)
    rank = RATINGS.index(rating) if rating in RATINGS else 0
    if rank >= RATINGS.index("good"):
        return f"If possible, add {where} to confirm it."
    agree = (
        len(lines) >= _AGREE_MIN_LINES
        and not misfit
        and rms_px <= _AGREE_RMS_PX
        and k2_fitted
        and rank >= RATINGS.index("okay")
    )
    if agree:
        return f"The result may already be good. If possible, add {where} to confirm it."
    return f"If possible, add {where}."


def fit_lens_from_lines(
    lines: Sequence[Sequence[Sequence[float]]],
    canvas_w: float,
    canvas_h: float,
    *,
    cx: float = 0.0,
    cy: float = 0.0,
) -> LensFitResult:
    """Fit ``k1`` / ``k2`` to the traced lines (active points only, image pixels) about the lens centre ``cx`` /
    ``cy`` (half-diagonals), which the fit takes as given.

    Raises ``ValueError`` when the input cannot be fitted: no line, more than
    ``MAX_LINES``, a line with fewer than three points or more than
    ``MAX_POINTS_PER_LINE``, a non-finite point, a line too short to carry
    a direction, or a centre beyond ``C_BOUND``.
    """
    if not (math.isfinite(canvas_w) and math.isfinite(canvas_h) and canvas_w > 0.0 and canvas_h > 0.0):
        raise ValueError("canvas must be finite and positive")
    if not (math.isfinite(cx) and math.isfinite(cy) and max(abs(cx), abs(cy)) <= C_BOUND):
        raise ValueError(f"the lens centre must be within -{C_BOUND:g}..{C_BOUND:g}")
    data = _Lines.from_lines(lines, canvas_w, canvas_h)
    solution = _solve(data, cx, cy)
    # A point that left its line is set aside and the lens fitted again, once per line at most.
    kept = np.ones(data.n, dtype=bool)
    for _ in range(2):
        rows = np.flatnonzero(kept)
        fitted = _Lines(data.pts[rows], data.idx[rows], canvas_w, canvas_h)
        strays = [rows[r] for r in _strays(fitted, solution.k) if np.all(kept[data.members[data.idx[rows[r]]]])]
        if not strays:
            break
        kept[strays] = False
        solution = _solve(_Lines(data.pts[kept], data.idx[kept], canvas_w, canvas_h), cx, cy)
    data_kept = _Lines(data.pts[kept], data.idx[kept], canvas_w, canvas_h)
    k = solution.k
    res = data_kept.residuals(k)
    rms = [float(np.sqrt(np.mean(res[rows] ** 2))) for rows in data_kept.members]
    uncertainty = max(
        _corner_uncertainty_px(data_kept, k, solution.cov, solution.free), _rival_spread_px(data_kept, solution)
    )
    rating = rate_uncertainty(uncertainty)
    flags = _misfits(data_kept, k)
    rms_all = float(np.sqrt(np.mean(res**2)))
    dropped = [tuple(int(j) for j in np.flatnonzero(~kept[rows])) for rows in data.members]
    return LensFitResult(
        k1=float(k[0]),
        k2=float(k[1]),
        k2_fitted=solution.k2_fitted,
        cx=float(k[2]),
        cy=float(k[3]),
        rms_px=rms_all,
        uncertainty_px=uncertainty,
        rating=rating,
        hint=coverage_hint(
            lines,
            canvas_w,
            canvas_h,
            rating=rating,
            k2_fitted=solution.k2_fitted,
            rms_px=rms_all,
            misfit=any(flags),
        ),
        lines=tuple(
            LensLineFit(rms_px=rms[i], misfit=flags[i], curve=data_kept.curve(k, i), dropped=dropped[i])
            for i in range(data.count)
        ),
        edges_measured=_reaches_out(data_kept, cx, cy),
    )


def fit_result_to_dict(result: LensFitResult) -> dict[str, Any]:
    """The JSON shape the wizard reads."""
    return {
        "k1": result.k1,
        "k2": result.k2,
        "k2_fitted": result.k2_fitted,
        "cx": result.cx,
        "cy": result.cy,
        "rms_px": result.rms_px,
        "uncertainty_px": result.uncertainty_px if math.isfinite(result.uncertainty_px) else None,
        "rating": result.rating,
        "hint": result.hint,
        "edges_measured": result.edges_measured,
        "lines": [
            {
                "rms_px": line.rms_px,
                "misfit": line.misfit,
                "curve": [list(p) for p in line.curve],
                "dropped": list(line.dropped),
            }
            for line in result.lines
        ],
    }
