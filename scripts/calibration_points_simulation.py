#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Simulate far-field calibration from a narrow stage strip, with and without calibration points.

The rig: an audience camera on the back truss, 8 m up, 55 degree lens, that sees a 10 x 1.5 m strip
of the stage and an auditorium about 45 m deep. Each trial pins the four corners with the given
noise (3 px by default), measures each calibration point from the two downstage corners with the
given laser noise, pins it with 1 px, and solves with the shipped solver. The error is the distance
on the floor between an audience spot and where the solved camera puts that spot's true pixel.

Columns: a typical audience spot (the median of 15) on a typical trial (p50) and on a bad one (p95);
the worst spot on a bad trial; the worst spot on the strip on a bad trial; and how often a point
was flagged.

    poetry run python scripts/calibration_points_simulation.py [--trials 400] [--laser 0.04 0.08 0.12]
        [--corner-px 3] [--corner-sigma PX] [--no-floor-guard]

``--corner-sigma`` and ``--no-floor-guard`` replace the solver's corner weighting and switch its floor
guard off, to compare against what ships.
"""

from __future__ import annotations

import argparse

import numpy as np

from openfollow.scene import calibration_points
from openfollow.scene.calibration_points import CalibrationPoint, solve_with_points
from openfollow.scene.solver import project_points, solve_camera_dlt, unproject_to_plane

W, H = 1920.0, 1080.0
TRUE = np.array([0.4, 10.0, 8.0, -24.0, 180.0, 0.0, 55.0])
CORNERS = np.array([[-5.0, 0.0, 0.0], [5.0, 0.0, 0.0], [5.0, 1.5, 0.0], [-5.0, 1.5, 0.0]])
STRIP = np.array([[x, y, 0.0] for x in np.linspace(-5, 5, 9) for y in np.linspace(0, 1.5, 4)])
AUDIENCE = np.array([[x, y, 0.0] for x in (-6.0, 0.0, 6.0) for y in (-5.0, -10.0, -20.0, -30.0, -40.0)])


def _params(cam) -> np.ndarray:
    return np.array([cam.pos_x, cam.pos_y, cam.pos_z, cam.pitch, cam.yaw, cam.roll, cam.fov])


def _error(params: np.ndarray, spots: np.ndarray) -> np.ndarray:
    """Metres off on the floor; a spot the solved camera sees above its horizon is off without bound."""
    back = unproject_to_plane(params, project_points(TRUE, spots, W, H), W, H, 0.0)
    off = np.linalg.norm(back[:, :2] - spots[:, :2], axis=1)
    return np.where(np.isfinite(off), off, np.inf)


def _measure(rng, spot, laser: float, gross: float, pin_px: float = 0.0) -> CalibrationPoint:
    a, b = CORNERS[0], CORNERS[1]
    pin = project_points(TRUE, spot[None], W, H)[0] + rng.normal(0.0, 1.0, 2) + (0.0, pin_px)
    return CalibrationPoint(
        corner_a=tuple(a),
        corner_b=tuple(b),
        distance_a=float(np.linalg.norm(spot - a) + rng.normal(0.0, laser) + gross),
        distance_b=float(np.linalg.norm(spot - b) + rng.normal(0.0, laser) + gross),
        height=0.0,
        pin=(float(pin[0]), float(pin[1])),
    )


def _row(
    rng, trials: int, n_points: int, laser: float, gross: tuple[float, float], kind: str, corner_px: float
) -> tuple[np.ndarray, float]:
    rows, flagged = [], 0
    for _ in range(trials):
        pins = project_points(TRUE, CORNERS, W, H) + rng.normal(0.0, corner_px, (4, 2))
        if kind == "dlt":
            cam = solve_camera_dlt([tuple(c) for c in CORNERS], [tuple(p) for p in pins], W, H)
        else:
            spots = AUDIENCE[rng.choice(len(AUDIENCE), size=n_points, replace=False)]
            points = [_measure(rng, s, laser, *(gross if i == 0 else (0.0, 0.0))) for i, s in enumerate(spots)]
            solved = solve_with_points(CORNERS, pins, points, W, H)
            cam = None if solved is None else solved.camera
            flagged += bool(solved and any(p.status == "flagged" for p in solved.points))
        if cam is None:
            continue
        far = _error(_params(cam), AUDIENCE)
        rows.append((np.median(far), far.max(), _error(_params(cam), STRIP).max()))
    return np.array(rows), flagged / trials


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--trials", type=int, default=400)
    parser.add_argument("--laser", type=float, nargs="+", default=[0.04, 0.08, 0.12], help="laser sigma, m")
    parser.add_argument("--corner-px", type=float, default=3.0, help="corner pin noise, px")
    parser.add_argument("--corner-sigma", type=float, help="weigh corner pins as this accurate, px")
    parser.add_argument("--no-floor-guard", action="store_true", help="switch the floor guard off")
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()
    rng = np.random.default_rng(args.seed)
    if args.corner_sigma is not None:
        calibration_points.CORNER_SIGMA = args.corner_sigma
    if args.no_floor_guard:
        calibration_points._floor_holds = lambda *_args: True

    cases = [("4 corners", 0, 0.0, (0.0, 0.0), "dlt")]
    for laser in args.laser:
        cm = round(laser * 100)
        cases += [
            (f"+ 1 laser point (+/- {cm} cm)", 1, laser, (0.0, 0.0), "fit"),
            (f"+ 2 laser points (+/- {cm} cm)", 2, laser, (0.0, 0.0), "fit"),
        ]
    for gross in (0.5, 2.0):
        cases += [
            (f"+ 1 point, both distances {gross:g} m too long", 1, args.laser[0], (gross, 0.0), "fit"),
            (f"+ 2 points, one {gross:g} m too long", 2, args.laser[0], (gross, 0.0), "fit"),
        ]
    cases += [
        ("+ 1 point pinned 100 px low", 1, args.laser[0], (0.0, 100.0), "fit"),
        ("+ 2 points, one pinned 100 px low", 2, args.laser[0], (0.0, 100.0), "fit"),
    ]

    print(f"{'':44s} {'far p50':>8s} {'far p95':>8s} {'worst seat p95':>15s} {'strip p95':>10s} {'flagged':>8s}")
    for label, n, laser, gross, kind in cases:
        v, flagged = _row(rng, args.trials, n, laser, gross, kind, args.corner_px)
        p50, p95 = np.percentile(v[:, 0], [50, 95])
        print(
            f"{label:44s} {p50:7.2f}m {p95:7.2f}m {np.percentile(v[:, 1], 95):14.2f}m "
            f"{np.percentile(v[:, 2], 95):9.3f}m {flagged:7.0%}"
        )


if __name__ == "__main__":
    main()
