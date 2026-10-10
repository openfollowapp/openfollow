#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Simulate the straight-line lens fit over coverage scenarios and lens strengths.

This is where the coverage rating thresholds (``RATING_THRESHOLDS_PX``) and the
k2 gate come from: synthetic lines are placed on the distorted image the way an
operator clicks them, warped with the real overlay functions, jittered by the
given placement noise, and fitted. The table lists, per scenario, how far k1 /
k2 land from the truth, the predicted corner uncertainty the rating is read
from, and whether k2 was fitted.

    poetry run python scripts/lens_fit_simulation.py [--noise 1 3] [--trials 5]
"""

from __future__ import annotations

import argparse

import numpy as np

from openfollow.scene.lens_fit import fit_lens_from_lines
from openfollow.scene.solver import apply_overlay_distortion, invert_overlay_distortion

W, H = 1920.0, 1080.0
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
LENSES = [(0.0, 0.0), (-0.15, 0.0), (-0.3, 0.05), (-0.45, 0.20), (-0.47, 0.25), (0.2, 0.0)]


def make_line(p0, p1, k1: float, k2: float, noise: float, rng: np.random.Generator) -> list[list[float]]:
    ends = invert_overlay_distortion(np.array([p0, p1], dtype=np.float64), W, H, k1, k2)
    t = np.linspace(0.0, 1.0, 5)[:, None]
    seen = apply_overlay_distortion(ends[0] + t * (ends[1] - ends[0]), W, H, k1, k2)
    return (seen + rng.normal(0.0, noise, seen.shape)).tolist()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--noise", type=float, nargs="+", default=[1.0, 3.0], help="placement noise in px")
    parser.add_argument("--trials", type=int, default=5)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    rng = np.random.default_rng(args.seed)
    for k1, k2 in LENSES:
        print(f"=== lens k1={k1:+.2f} k2={k2:+.2f}")
        for name, keys in SCENARIOS.items():
            for noise in args.noise:
                rows = []
                for _ in range(args.trials):
                    lines = [make_line(*SEGMENTS[key], k1, k2, noise, rng) for key in keys]
                    r = fit_lens_from_lines(lines, W, H)
                    rows.append((abs(r.k1 - k1), abs(r.k2 - k2), r.uncertainty_px, r.k2_fitted, r.rating))
                dk1 = max(x[0] for x in rows)
                dk2 = max(x[1] for x in rows)
                unc = float(np.median([x[2] for x in rows]))
                fitted = sum(1 for x in rows if x[3])
                ratings = ",".join(sorted({x[4] for x in rows}))
                print(
                    f"  {name:20s} noise {noise:3.1f} px | max dk1 {dk1:6.3f} max dk2 {dk2:6.3f} | "
                    f"corner sigma median {unc:8.1f} px | k2 fitted {fitted}/{args.trials} | {ratings}"
                )


if __name__ == "__main__":
    main()
