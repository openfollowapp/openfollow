#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Paul Hermann and Michel Honold
"""Simulate the straight-line lens fit over coverage scenarios, lens strengths and lens centres.

This is where the coverage rating thresholds (``RATING_THRESHOLDS_PX``) and the
priors on k1 / k2 come from: synthetic lines are placed on the distorted image
the way an operator clicks them, warped with the real overlay functions,
jittered by the given placement noise, and fitted about a given lens centre.
A lens whose centre sits off the frame centre is fitted twice: with its centre
given, and with the centre given as 0, which shows what a missing centre costs.
The table lists, per scenario, how far k1 / k2 land from the truth, the
predicted corner uncertainty the rating is read from, how often k2 was
measured, and the largest screen error over the frame once the camera solve has
absorbed what it can (a free homography of the pinhole picture).

    poetry run python scripts/lens_fit_simulation.py [--noise 1 3] [--trials 12] [--only 2.3mm]
"""

from __future__ import annotations

import argparse
import math
from collections import Counter

import numpy as np

from openfollow.scene.lens_fit import RATING_THRESHOLDS_PX, RATINGS, fit_lens_from_lines
from openfollow.scene.solver import apply_overlay_distortion, invert_overlay_distortion

W, H = 1920.0, 1080.0
HALF_DIAG = 0.5 * math.hypot(W, H)
_M = 12.0
# Endpoints on the distorted image: along its edges, a diagonal, and the middle.
SEGMENTS = {
    "top": ((_M + 40, _M), (W - _M - 40, _M + 8)),
    "bottom": ((_M + 40, H - _M), (W - _M - 40, H - _M - 6)),
    "left": ((_M, _M + 40), (_M + 6, H - _M - 40)),
    "right": ((W - _M, _M + 40), (W - _M - 8, H - _M - 40)),
    "diagonal": ((120.0, 100.0), (1800.0, 1000.0)),
    "mid_h": ((300.0, 540.0), (1600.0, 520.0)),
    "mid_v": ((960.0, 100.0), (980.0, 980.0)),
    "half_top": ((_M + 40, _M), (W / 2, _M + 8)),
}
SCENARIOS = {
    "1 centre line": ("mid_h",),
    "2 centre lines": ("mid_h", "mid_v"),
    "top only": ("top",),
    "top + bottom": ("top", "bottom"),
    "half top + bottom": ("half_top", "bottom"),
    "top + bottom + left": ("top", "bottom", "left"),
    "4 edges": ("top", "bottom", "left", "right"),
    "4 edges + diagonal": ("top", "bottom", "left", "right", "diagonal"),
    "4 edges + 2 middle": ("top", "bottom", "left", "right", "mid_h", "mid_v"),
}
# Idealised lenses fitted with the division model (name, k1, k2), about the frame centre.
CENTRED = [
    ("pinhole", 0.0, 0.0),
    ("equidistant 6 mm", -0.097, -0.002),
    ("equidistant 4.0 mm", -0.203, -0.009),
    ("equidistant 2.8 mm", -0.333, -0.025),
    ("equidistant 2.3 mm", -0.527, -0.069),
    ("equidistant 2.1 mm", -0.647, -0.111),
    ("stereographic 2.3 mm", -0.494, 0.0),
    ("equisolid 2.3 mm", -0.527, -0.118),
    ("fisheye 150", -0.740, -0.152),
    ("pincushion", 0.2, 0.0),
]
# Lenses whose centre sits off the frame centre, offsets in pixels of the 1920 x 1080 picture (x right, y down).
OFFSETS = [(20.0, -12.0), (40.0, -25.0), (80.0, 50.0), (120.0, 80.0)]
DECENTRED = [("weak", -0.10, 0.0), ("corner-heavy", -0.27, -0.09), ("2.3 mm", -0.53, -0.07), ("2.3 mm", -0.53, -0.118)]
# Screen points over the frame where the fitted lens is compared with the truth.
_GRID = np.column_stack([g.ravel() for g in np.meshgrid(np.linspace(0.0, W, 17), np.linspace(0.0, H, 10))])


def lenses() -> list[tuple[str, tuple[float, float, float, float], tuple[float, float]]]:
    """``(name, lens, centre the fit is given)`` for every case."""
    out = [(name, (k1, k2, 0.0, 0.0), (0.0, 0.0)) for name, k1, k2 in CENTRED]
    for name, k1, k2 in DECENTRED:
        for ox, oy in OFFSETS:
            lens = (k1, k2, ox / HALF_DIAG, oy / HALF_DIAG)
            out.append((f"{name} at ({ox:+.0f}, {oy:+.0f}) px, centre given", lens, lens[2:]))
            out.append((f"{name} at ({ox:+.0f}, {oy:+.0f}) px, centre given as 0", lens, (0.0, 0.0)))
    return out


def make_line(p0, p1, lens, noise: float, rng: np.random.Generator) -> list[list[float]]:
    ends = invert_overlay_distortion(np.array([p0, p1], dtype=np.float64), W, H, *lens)
    t = np.linspace(0.0, 1.0, 5)[:, None]
    seen = apply_overlay_distortion(ends[0] + t * (ends[1] - ends[0]), W, H, *lens)
    return (seen + rng.normal(0.0, noise, seen.shape)).tolist()


def _homography(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """Least-squares homography taking ``src`` to ``dst`` (normalised DLT)."""

    def norm(p: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        c = p.mean(axis=0)
        s = math.sqrt(2.0) / float(np.mean(np.hypot(*(p - c).T)))
        t = np.array([[s, 0.0, -s * c[0]], [0.0, s, -s * c[1]], [0.0, 0.0, 1.0]])
        return (p - c) * s, t

    a, ta = norm(src)
    b, tb = norm(dst)
    rows = []
    for (x, y), (u, v) in zip(a, b, strict=True):
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y, -u])
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y, -v])
    h = np.linalg.svd(np.asarray(rows))[2][-1].reshape(3, 3)
    return np.linalg.solve(tb, h @ ta)


def frame_error_px(fitted, truth) -> float:
    """Largest screen error over the frame after the free homography the camera solve absorbs."""
    q_true = invert_overlay_distortion(_GRID, W, H, *truth)
    q_fit = invert_overlay_distortion(_GRID, W, H, *fitted)
    hom = _homography(q_fit, q_true)
    mapped = np.column_stack([q_fit, np.ones(len(q_fit))]) @ hom.T
    seen = apply_overlay_distortion(mapped[:, :2] / mapped[:, 2:], W, H, *truth)
    return float(np.max(np.hypot(*(seen - _GRID).T)))


def overclaimed(rating: str, error: float) -> bool:
    """Whether the frame error is more than twice what the rating promises."""
    bound = {name: limit for limit, name in RATING_THRESHOLDS_PX}.get(rating)
    return bound is not None and error > 2.0 * bound


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--noise", type=float, nargs="+", default=[1.0, 3.0], help="placement noise in px")
    parser.add_argument("--trials", type=int, default=12)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--only", default="", help="run the lenses whose name contains this")
    parser.add_argument("--scenarios", nargs="+", default=list(SCENARIOS), help="scenario names to run")
    args = parser.parse_args()
    rng = np.random.default_rng(args.seed)
    for name, lens, given in lenses():
        if args.only not in name:
            continue
        k1, k2, cx, cy = lens
        print(f"=== {name}: k1={k1:+.3f} k2={k2:+.3f} centre ({cx * HALF_DIAG:+.0f}, {cy * HALF_DIAG:+.0f}) px")
        for scenario in args.scenarios:
            for noise in args.noise:
                rows = []
                for _ in range(args.trials):
                    lines = [make_line(*SEGMENTS[key], lens, noise, rng) for key in SCENARIOS[scenario]]
                    r = fit_lens_from_lines(lines, W, H, cx=given[0], cy=given[1])
                    error = frame_error_px((r.k1, r.k2, r.cx, r.cy), lens)
                    rows.append((abs(r.k1 - k1), abs(r.k2 - k2), r.uncertainty_px, r.k2_fitted, r.rating, error))
                errors = [x[5] for x in rows]
                counts = Counter(x[4] for x in rows)
                ratings = " ".join(f"{rating} {counts[rating]}" for rating in reversed(RATINGS) if counts[rating])
                sigma = float(np.median([x[2] for x in rows]))
                print(
                    f"  {scenario:20s} noise {noise:3.1f} px | max dk1 {max(x[0] for x in rows):6.3f} "
                    f"max dk2 {max(x[1] for x in rows):6.3f} | corner sigma median {sigma:8.1f} px | "
                    f"k2 {sum(x[3] for x in rows):2d}/{args.trials} | "
                    f"frame error {float(np.median(errors)):6.1f} / {max(errors):6.1f} px | "
                    f"overclaimed {sum(overclaimed(x[-2], x[-1]) for x in rows):2d} | {ratings}"
                )


if __name__ == "__main__":
    main()
