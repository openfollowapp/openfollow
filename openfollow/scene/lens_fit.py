# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Measure the lens coefficients from lines that are straight in reality.

The plumb-line method: the operator traces features that are straight on
stage (a stage edge, a gaffa line, truss). Under the right ``k1`` / ``k2`` the
traced points become collinear once undistorted, so the fit minimises each
line's perpendicular residuals about its own total-least-squares line.

A coarse grid over both coefficients seeds a damped Gauss-Newton refinement,
because a start at zero diverges on strong barrel distortion. ``k2`` is kept
at zero unless the lines determine it: its predicted uncertainty has to fall
under :data:`K2_SIGMA_GATE`, which in practice needs lines in the outer
corners. The rating is the predicted uncertainty of the warp at the frame
corner in pixels, one number that captures how many lines there are, how much
of the frame they cover and how precisely their points were placed.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt

from openfollow.lens_model import lens_warp_is_valid
from openfollow.scene.solver import _fold_radius, apply_overlay_distortion, invert_normalised_radius

FloatArray = npt.NDArray[np.float64]

# Seed grid. Wide enough for a 100 degree lens on 16:9 (k1 about -0.5). The
# cost valley is a thin curved ridge, narrow in k2 (0.05 moves a corner by
# 55 px), so k2 is scanned finely and the refinement starts from several of
# the best cells rather than one.
_K1_SCAN = np.arange(-0.8, 0.8001, 0.05)
_K2_SCAN = np.arange(-0.5, 0.5001, 0.02)
_SEEDS = 4
# Beyond this the model describes no real lens; keeps a one-line fit bounded.
_K_BOUND = 1.0
_LM_ITERS = 40
_JACOBIAN_EPS = 1e-4
# Placement precision nobody beats; keeps a lucky fit from reporting zero uncertainty.
_SIGMA_FLOOR_PX = 1.0
# k2 is fitted only when its predicted uncertainty is below this.
K2_SIGMA_GATE = 0.05
# A line does not fit the others when, with k1 refitted without it, its RMS is
# above both an absolute floor and a multiple of the other lines' RMS.
_MISFIT_MIN_PX = 3.0
_MISFIT_RATIO = 3.0
# Smallest eigenvalue (px^2 per unit k^2) of the information matrix below which
# the lines constrain nothing; a real line sits many orders above it.
_SINGULAR_INFORMATION = 1e-9
_MIN_POINTS_PER_LINE = 3
_MIN_LINES_FOR_MISFIT = 3
_MIN_LINE_SPAN_PX = 10.0
_CURVE_SAMPLES = 24
# Outer band of the frame, as a fraction of the half extent, for the coverage hint.
_EDGE_BAND = 0.7
# Points an outer band needs before the hint stops asking for a line there.
_EDGE_BAND_POINTS = 5

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
    """One traced line's share of the result."""

    rms_px: float
    misfit: bool
    curve: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class LensFitResult:
    k1: float
    k2: float
    k2_fitted: bool
    rms_px: float
    uncertainty_px: float
    rating: str
    hint: str
    lines: tuple[LensLineFit, ...]


def rate_uncertainty(uncertainty_px: float) -> str:
    """Five-step rating from the predicted corner uncertainty."""
    if not math.isfinite(uncertainty_px):
        return RATING_LOW
    for limit, rating in RATING_THRESHOLDS_PX:
        if uncertainty_px < limit:
            return rating
    return RATING_LOW


class _Lines:
    """The traced points as flat arrays, normalised to the half-diagonal."""

    def __init__(self, pts: FloatArray, idx: npt.NDArray[np.int64], canvas_w: float, canvas_h: float) -> None:
        self.pts = pts
        self.idx = idx
        self.n = pts.shape[0]
        self.count = int(idx.max()) + 1 if self.n else 0
        self.half_diag = 0.5 * math.hypot(canvas_w, canvas_h)
        self.cx = canvas_w / 2.0
        self.cy = canvas_h / 2.0
        self.dx = (pts[:, 0] - self.cx) / self.half_diag
        self.dy = (pts[:, 1] - self.cy) / self.half_diag
        self.rd = np.hypot(self.dx, self.dy)
        self.members = [np.flatnonzero(idx == i) for i in range(self.count)]

    @classmethod
    def from_lines(cls, lines: Sequence[Sequence[Sequence[float]]], canvas_w: float, canvas_h: float) -> _Lines:
        if not lines:
            raise ValueError("at least one line is needed")
        rows: list[FloatArray] = []
        idx: list[int] = []
        for i, line in enumerate(lines):
            arr = np.asarray(line, dtype=np.float64)
            if arr.ndim != 2 or arr.shape[1] != 2 or arr.shape[0] < _MIN_POINTS_PER_LINE:
                raise ValueError(f"line {i + 1} needs at least {_MIN_POINTS_PER_LINE} points of [x, y]")
            if not np.isfinite(arr).all():
                raise ValueError(f"line {i + 1} has a non-finite point")
            if float(np.max(np.ptp(arr, axis=0))) < _MIN_LINE_SPAN_PX:
                raise ValueError(f"line {i + 1} is shorter than {_MIN_LINE_SPAN_PX:g} px")
            rows.append(arr)
            idx.extend([i] * arr.shape[0])
        return cls(np.vstack(rows), np.asarray(idx, dtype=np.int64), canvas_w, canvas_h)

    def without(self, line: int) -> _Lines:
        """The same points minus one line, for the leave-one-out check."""
        keep = self.idx != line
        idx = self.idx[keep]
        idx = np.where(idx > line, idx - 1, idx)
        return _Lines(self.pts[keep], idx, 2.0 * self.cx, 2.0 * self.cy)

    def undistort(self, k1: npt.ArrayLike, k2: npt.ArrayLike) -> tuple[FloatArray, FloatArray]:
        """Pinhole positions (normalised) of every point; a grid of ``k`` broadcasts on a trailing axis."""
        a = np.asarray(k1, dtype=np.float64)
        b = np.asarray(k2, dtype=np.float64)
        rd: FloatArray = self.rd
        dx: FloatArray = self.dx
        dy: FloatArray = self.dy
        if a.ndim:
            rd, dx, dy = rd[:, None], dx[:, None], dy[:, None]
            a, b = a[None, :], b[None, :]
        ru = invert_normalised_radius(rd, a, b)
        scale = np.where(rd > 0.0, ru / np.where(rd > 0.0, rd, 1.0), 1.0)
        return dx * scale, dy * scale

    def grid_cost(self, k1: FloatArray, k2: FloatArray) -> FloatArray:
        """Sum over lines of the squared perpendicular residuals, for every grid cell."""
        ux, uy = self.undistort(k1, k2)
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
        self, ux: FloatArray, uy: FloatArray, rows: npt.NDArray[np.int64]
    ) -> tuple[FloatArray, FloatArray, FloatArray]:
        """Centroid, direction and centred points of one line's TLS fit."""
        centre = np.array([ux[rows].mean(), uy[rows].mean()])
        q = np.column_stack([ux[rows], uy[rows]]) - centre
        _, _, vt = np.linalg.svd(q, full_matrices=False)
        return centre, np.asarray(vt[0], dtype=np.float64), q

    def residuals(self, k: FloatArray) -> FloatArray:
        """Perpendicular residual of every point about its line's TLS line, in pixels."""
        ux, uy = self.undistort(float(k[0]), float(k[1]))
        out = np.empty(self.n)
        for rows in self.members:
            _, direction, q = self._tls(ux, uy, rows)
            out[rows] = q @ np.array([-direction[1], direction[0]])
        return out * self.half_diag

    def line_rms(self, k: FloatArray) -> list[float]:
        res = self.residuals(k)
        return [float(np.sqrt(np.mean(res[rows] ** 2))) for rows in self.members]

    def curve(self, k: FloatArray, line: int) -> tuple[tuple[float, float], ...]:
        """The curve the lens predicts for a line: its TLS line, re-distorted."""
        ux, uy = self.undistort(float(k[0]), float(k[1]))
        centre, direction, q = self._tls(ux, uy, self.members[line])
        t = q @ direction
        ts = np.linspace(t.min(), t.max(), _CURVE_SAMPLES)
        straight = centre[None, :] + ts[:, None] * direction[None, :]
        straight_px = np.column_stack(
            [self.cx + straight[:, 0] * self.half_diag, self.cy + straight[:, 1] * self.half_diag]
        )
        bowed = apply_overlay_distortion(straight_px, 2.0 * self.cx, 2.0 * self.cy, float(k[0]), float(k[1]))
        return tuple((float(x), float(y)) for x, y in bowed)


def _in_bounds(k: FloatArray) -> bool:
    return bool(abs(k[0]) <= _K_BOUND and abs(k[1]) <= _K_BOUND and lens_warp_is_valid(k[0], k[1]))


def _jacobian(lines: _Lines, k: FloatArray) -> FloatArray:
    cols = []
    for i in (0, 1):
        kp = k.copy()
        km = k.copy()
        kp[i] += _JACOBIAN_EPS
        km[i] -= _JACOBIAN_EPS
        cols.append((lines.residuals(kp) - lines.residuals(km)) / (2.0 * _JACOBIAN_EPS))
    return np.column_stack(cols)


def _refine(lines: _Lines, k0: FloatArray, fit_k2: bool) -> FloatArray:
    """Damped Gauss-Newton (Levenberg-Marquardt) from ``k0``, kept inside the valid region."""
    k: FloatArray = k0.copy()
    res = lines.residuals(k)
    cost = float(res @ res)
    lam = 1e-3
    free = [0, 1] if fit_k2 else [0]
    for _ in range(_LM_ITERS):
        jac = _jacobian(lines, k)[:, free]
        normal = jac.T @ jac
        grad = jac.T @ res
        delta: FloatArray = np.zeros(len(free))
        improved = False
        for _attempt in range(12):
            # The damping term keeps this invertible however flat the residuals are.
            damped = normal + lam * np.diag(np.diag(normal) + 1e-12)
            delta = np.asarray(-np.linalg.solve(damped, grad), dtype=np.float64)
            cand: FloatArray = k.copy()
            cand[free] += delta
            if not _in_bounds(cand):
                lam *= 10.0
                continue
            cand_res = lines.residuals(cand)
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


def _covariance(lines: _Lines, k: FloatArray, n_params: int) -> FloatArray:
    """Covariance of ``(k1, k2)`` at ``k`` from the residual scatter, floored at one pixel."""
    res = lines.residuals(k)
    dof = max(lines.n - n_params - 2 * lines.count, 1)
    sigma2 = max(float(res @ res) / dof, _SIGMA_FLOOR_PX**2)
    jac = _jacobian(lines, k)
    normal = jac.T @ jac
    # A line through the centre moves along itself under the warp, so no
    # coefficient changes a residual: the pair is then unbounded, not merely loose.
    if float(np.linalg.eigvalsh(normal).min()) <= _SINGULAR_INFORMATION:
        return np.full((2, 2), np.inf)
    return np.asarray(sigma2 * np.linalg.inv(normal), dtype=np.float64)


def _corner_uncertainty_px(cov: FloatArray, half_diag: float) -> float:
    """Predicted sigma of the warp's radial displacement at a frame corner (``r == 1``)."""
    var = float(cov[0, 0] + cov[1, 1] + 2.0 * cov[0, 1])
    if not math.isfinite(var):
        return math.inf
    return half_diag * math.sqrt(max(var, 0.0))


def _seeds(lines: _Lines, k2_zero: bool) -> list[FloatArray]:
    """The lowest-cost grid cells, at most one per k1 column so the starts differ."""
    if k2_zero:
        k1: FloatArray = np.asarray(_K1_SCAN, dtype=np.float64)
        k2: FloatArray = np.zeros_like(k1)
    else:
        g1, g2 = np.meshgrid(_K1_SCAN, _K2_SCAN, indexing="ij")
        k1 = np.asarray(g1.ravel(), dtype=np.float64)
        k2 = np.asarray(g2.ravel(), dtype=np.float64)
    valid = _fold_radius(k1, k2) > 1.0
    k1, k2 = k1[valid], k2[valid]
    cost = lines.grid_cost(k1, k2)
    best_per_column: dict[float, int] = {}
    for i in np.argsort(cost):
        best_per_column.setdefault(float(k1[i]), int(i))
    return [np.array([k1[i], k2[i]]) for i in list(best_per_column.values())[:_SEEDS]]


def _fit(lines: _Lines, fit_k2: bool) -> FloatArray:
    """Refine from every seed and keep the lowest cost."""
    best: FloatArray | None = None
    best_cost = math.inf
    for seed in _seeds(lines, k2_zero=not fit_k2):
        k = _refine(lines, seed, fit_k2=fit_k2)
        res = lines.residuals(k)
        cost = float(res @ res)
        if cost < best_cost:
            best, best_cost = k, cost
    assert best is not None
    return best


def _solve(lines: _Lines) -> tuple[FloatArray, bool, FloatArray]:
    """Fit, deciding whether the lines determine ``k2``; returns ``(k, k2_fitted, cov)``."""
    k = _fit(lines, fit_k2=True)
    cov = _covariance(lines, k, 2)
    if math.sqrt(max(float(cov[1, 1]), 0.0)) < K2_SIGMA_GATE:
        return k, True, cov
    k = _fit(lines, fit_k2=False)
    return k, False, _covariance(lines, k, 1)


def _k1_variance(lines: _Lines, k: FloatArray) -> float:
    """Variance of ``k1`` fitted alone at ``k``, from the residual scatter floored at one pixel."""
    res = lines.residuals(k)
    dof = max(lines.n - 1 - 2 * lines.count, 1)
    sigma2 = max(float(res @ res) / dof, _SIGMA_FLOOR_PX**2)
    column = _jacobian(lines, k)[:, 0]
    information = float(column @ column)
    if information <= _SINGULAR_INFORMATION:
        return math.inf
    return sigma2 / information


def _misfits(lines: _Lines, k: FloatArray) -> list[bool]:
    """Leave-one-out: a line that only fits while it is pulling the fit its way.

    ``k1`` is refitted without the line from the full solution, ``k2`` held, so
    two remaining lines cannot be overfitted into agreeing with anything. The
    line's residual is judged against what the refit's own uncertainty predicts
    at that line: the longest, most curved line moves the most when it is left
    out, and that is the rest's ignorance, not the line's fault.
    """
    if lines.count < _MIN_LINES_FOR_MISFIT:
        return [False] * lines.count
    flags = []
    for i in range(lines.count):
        rest = lines.without(i)
        k_rest = _refine(rest, k, fit_k2=False)
        variance = _k1_variance(rest, k_rest)
        if not math.isfinite(variance):
            flags.append(False)
            continue
        rms_rest = float(np.sqrt(np.mean(rest.residuals(k_rest) ** 2)))
        rms_line = lines.line_rms(k_rest)[i]
        sensitivity = _jacobian(lines, k_rest)[lines.idx == i, 0]
        predicted = float(np.sqrt(variance * np.mean(sensitivity**2)))
        flags.append(rms_line > max(_MISFIT_MIN_PX, _MISFIT_RATIO * rms_rest, _MISFIT_RATIO * predicted))
    return flags


def coverage_hint(
    lines: Sequence[Sequence[Sequence[float]]], canvas_w: float, canvas_h: float, *, rating: str, k2_fitted: bool
) -> str:
    """One sentence naming the weakest area, empty when nothing is missing."""
    if rating == RATINGS[-1]:
        return ""
    if len(lines) < 2:
        return "Add a second line in another part of the image."
    if not k2_fitted:
        return "k2 needs a line near a corner."
    pts = np.asarray([p for line in lines for p in line], dtype=np.float64)
    cx, cy = canvas_w / 2.0, canvas_h / 2.0
    bands = {
        "top": int(np.sum(pts[:, 1] < cy - _EDGE_BAND * cy)),
        "bottom": int(np.sum(pts[:, 1] > cy + _EDGE_BAND * cy)),
        "left": int(np.sum(pts[:, 0] < cx - _EDGE_BAND * cx)),
        "right": int(np.sum(pts[:, 0] > cx + _EDGE_BAND * cx)),
    }
    weakest = min(bands, key=lambda name: (bands[name], name))
    if bands[weakest] < _EDGE_BAND_POINTS:
        return f"Add a line near the {weakest} edge."
    return "Add more lines near the edges, or place the points more precisely."


def fit_lens_from_lines(lines: Sequence[Sequence[Sequence[float]]], canvas_w: float, canvas_h: float) -> LensFitResult:
    """Fit ``k1`` / ``k2`` to the traced lines (active points only, image pixels).

    Raises ``ValueError`` when the input cannot be fitted: no line, a line with
    fewer than three points, a non-finite point, or a line too short to carry
    a direction.
    """
    if not (math.isfinite(canvas_w) and math.isfinite(canvas_h) and canvas_w > 0.0 and canvas_h > 0.0):
        raise ValueError("canvas must be finite and positive")
    data = _Lines.from_lines(lines, canvas_w, canvas_h)
    k, k2_fitted, cov = _solve(data)
    rms = data.line_rms(k)
    res = data.residuals(k)
    uncertainty = _corner_uncertainty_px(cov, data.half_diag)
    rating = rate_uncertainty(uncertainty)
    flags = _misfits(data, k)
    return LensFitResult(
        k1=float(k[0]),
        k2=float(k[1]),
        k2_fitted=k2_fitted,
        rms_px=float(np.sqrt(np.mean(res**2))),
        uncertainty_px=uncertainty,
        rating=rating,
        hint=coverage_hint(lines, canvas_w, canvas_h, rating=rating, k2_fitted=k2_fitted),
        lines=tuple(LensLineFit(rms_px=rms[i], misfit=flags[i], curve=data.curve(k, i)) for i in range(data.count)),
    )


def fit_result_to_dict(result: LensFitResult) -> dict[str, Any]:
    """The JSON shape the wizard reads."""
    return {
        "k1": result.k1,
        "k2": result.k2,
        "k2_fitted": result.k2_fitted,
        "rms_px": result.rms_px,
        "uncertainty_px": result.uncertainty_px if math.isfinite(result.uncertainty_px) else None,
        "rating": result.rating,
        "hint": result.hint,
        "lines": [
            {"rms_px": line.rms_px, "misfit": line.misfit, "curve": [list(p) for p in line.curve]}
            for line in result.lines
        ],
    }
