#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Render the Media Gallery's Concert default: a concert stage seen from a camera on the back truss.

The camera hangs on the upstage back truss behind the band riser and looks steeply down over the
stage into the crowd: dark brown 1 x 2 m stage decks under a white wash, running on, darker, into
the wings; the band riser with black treads and a white safety edge, and a higher deck with moving
heads on each side of it; white tape along the lip, the mic stand, pedalboards and setlists at the
stage edge; two sheer white legs tied back on each side, at the downstage corner and mid-stage;
sidefills, amp racks, a monitor desk, a guitar world and cases in the wings; front fills in the pit
before the crowd barrier, and the crowd. The picture is bowed by a barrel lens through the overlay's
own model, so a station set to the printed camera and lens draws its grid on the deck seams. Tape
marks the grid's corners and the Reference Point.

Needs ``rsvg-convert`` (librsvg) and Pillow. Prints the camera, the lens pair, the grid and the
corners' pixels.

    poetry run python scripts/gen_concert_default.py
"""

from __future__ import annotations

import io
import math
import random
import subprocess
import sys
from itertools import pairwise
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
from gallery_calibration import camera_from, write_calibration  # noqa: E402
from PIL import Image, ImageFilter  # noqa: E402

from openfollow.lens_model import pull_back_to_valid  # noqa: E402
from openfollow.scene.solver import apply_overlay_distortion, project_points  # noqa: E402

ASSET = ROOT / "openfollow" / "video" / "inputs" / "assets" / "concert_default.jpg"
W, H = 1920.0, 1080.0
# Back truss upstage of the riser: pos_x, pos_y, pos_z, pitch, yaw, roll, horizontal fov.
CAMERA = np.array([0.0, 13.0, 12.0, -48.0, 180.0, 0.0, 70.0])
K1, K2 = -0.2, -0.07  # barrel / fisheye, corner correction
# Pinhole geometry is cut to this box before the lens bows it, which keeps the SVG small.
CLIP = (-0.25 * W, -0.25 * H, 1.25 * W, 1.25 * H)
STEP_PX = 6.0  # edges are split this finely, so a straight edge bows like the lens bows it

STAGE_X, LIP_Y, STAGE_BACK = 7.0, 0.0, 11.0  # the deck: 14 m across, from the lip back past the riser
WING_X, OFF_X = STAGE_X + 5.0, 20.0  # the deck runs on, darker, into the wings; dark floor beyond
DECK_W, DECK_D = 1.0, 2.0  # 1 m across the front, 2 m running back
FLOOR_Z = -1.8  # house floor below the deck
PIT_Y = LIP_Y - 2.7  # barricade
LEG_TOP, LEG_KNOT = 11.0, 1.5  # leg height and tie-back height
# Two tied-back legs per side: plane (y), outer and inner edge at the top, the tie (x, this side), and
# whether the cloth falls straight from the top and curves only toward the tie (the downstage pair).
LEGS = (
    (LIP_Y + 0.3, STAGE_X + 2.5, STAGE_X - 1.2, STAGE_X + 0.5, True),
    (4.6, STAGE_X + 4.5, STAGE_X - 0.4, STAGE_X + 0.3, False),
)
GRID_W, GRID_D, GRID_SPACING = 12.0, 6.0, 2.0  # grid lines on every second deck seam, lip to riser
CORNERS = {
    "DSL": (GRID_W / 2, 0.0, 0.0),
    "DSR": (-GRID_W / 2, 0.0, 0.0),
    "USR": (-GRID_W / 2, GRID_D, 0.0),
    "USL": (GRID_W / 2, GRID_D, 0.0),
}
RISER = (-5.0, 5.0, 6.0, 10.0, 0.6)  # x0, x1, y0 (front), y1, height
SIDE_RISER = (5.3, 9.3, 8.4, 9.4, 2.0)  # each side of the riser: inner x, outer x, y0, y1, height

C_BG, C_HOUSE, C_DECK, C_SEAM = "#050507", "#0d0b10", "#3a332d", "#c9c0b5"
C_OFF, C_WING_DECK, C_RISER_TOP, C_RISER_FACE = "#0c0c0f", "#1f1b18", "#070708", "#0e0f12"
C_DRAPE, C_DRAPE_DARK, C_BARR = "#e4e2df", "#9a9692", "#b8bcc4"
GAMMA = 1.55  # darkens the scene and keeps the tape white
TAPE = "#ffffff"


def pinhole(pts) -> np.ndarray:
    return project_points(CAMERA, np.asarray(pts, dtype=float), W, H)


def lens(px: np.ndarray) -> np.ndarray:
    return apply_overlay_distortion(px, W, H, K1, K2, 0.0, 0.0)


_PLANES = [(0, CLIP[0], 1), (0, CLIP[2], -1), (1, CLIP[1], 1), (1, CLIP[3], -1)]


def _clip_polygon(px: np.ndarray) -> np.ndarray | None:
    pts = list(px)
    for axis, bound, sign in _PLANES:
        out = []
        for a, b in zip(pts, pts[1:] + pts[:1], strict=True):
            a_in, b_in = sign * (a[axis] - bound) >= 0, sign * (b[axis] - bound) >= 0
            if a_in:
                out.append(a)
            if a_in != b_in:
                out.append(a + (bound - a[axis]) / (b[axis] - a[axis]) * (b - a))
        pts = out
        if len(pts) < 3:
            return None
    return np.array(pts)


def _clip_segment(a: np.ndarray, b: np.ndarray) -> np.ndarray | None:
    t0, t1, d = 0.0, 1.0, b - a
    for axis, bound, sign in _PLANES:
        p, q = -sign * d[axis], sign * (a[axis] - bound)
        if p == 0:
            if q < 0:
                return None
            continue
        t = q / p
        if p < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
        if t0 > t1:
            return None
    return np.array([a + t0 * d, a + t1 * d])


def _bowed(px: np.ndarray, closed: bool) -> np.ndarray:
    ring = np.vstack([px, px[:1]]) if closed else px
    dense = [ring[0]]
    for a, b in zip(ring[:-1], ring[1:], strict=True):
        n = max(1, int(np.ceil(np.linalg.norm(b - a) / STEP_PX)))
        dense.extend(a + (b - a) * (k / n) for k in range(1, n + 1))
    return lens(np.array(dense[:-1] if closed else dense))


def _points(px: np.ndarray) -> str:
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in px)


def poly(pts, fill: str, stroke: str = "", width: float = 0.0, opacity: float = 1.0) -> str:
    px = pinhole(pts)
    if not np.isfinite(px).all():
        return ""
    kept = _clip_polygon(px)
    if kept is None:
        return ""
    edge = f' stroke="{stroke}" stroke-width="{width}"' if stroke else ""
    alpha = f' fill-opacity="{opacity}"' if opacity < 1.0 else ""
    return f'<polygon points="{_points(_bowed(kept, True))}" fill="{fill}"{alpha}{edge}/>'


def line(a, b, color: str, width: float, opacity: float = 1.0) -> str:
    px = pinhole([a, b])
    if not np.isfinite(px).all():
        return ""
    kept = _clip_segment(px[0], px[1])
    if kept is None:
        return ""
    alpha = f' stroke-opacity="{opacity}"' if opacity < 1.0 else ""
    return (
        f'<polyline points="{_points(_bowed(kept, False))}" fill="none" stroke="{color}" stroke-width="{width}"'
        f'{alpha} stroke-linecap="round"/>'
    )


def box(out: list[str], x0, x1, y0, y1, z0, z1, top: str, face: str) -> None:
    """An axis-aligned box, drawn as the faces that point at the camera."""
    if CAMERA[1] > y1:
        out.append(poly([(x0, y1, z0), (x1, y1, z0), (x1, y1, z1), (x0, y1, z1)], face))
    elif CAMERA[1] < y0:
        out.append(poly([(x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1)], face))
    if x1 < CAMERA[0]:
        out.append(poly([(x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)], face))
    if x0 > CAMERA[0]:
        out.append(poly([(x0, y0, z0), (x0, y1, z0), (x0, y1, z1), (x0, y0, z1)], face))
    out.append(poly([(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)], top))


def disc(cx, cy, z, r, n=28):
    return [(cx + r * math.cos(t), cy + r * math.sin(t), z) for t in np.linspace(0, 2 * math.pi, n, endpoint=False)]


def house(out: list[str], rng: random.Random) -> None:
    out.append(poly([(-40, PIT_Y, FLOOR_Z), (40, PIT_Y, FLOOR_Z), (40, -70, FLOOR_Z), (-40, -70, FLOOR_Z)], C_HOUSE))
    people = []
    y = -60.0
    while y < PIT_Y - 0.35:
        x = -30.0 + rng.uniform(0, 0.5)
        while x < 30.0:
            if rng.random() < 0.88:
                people.append((x + rng.uniform(-0.15, 0.15), y + rng.uniform(-0.15, 0.15)))
            x += rng.uniform(0.48, 0.62)
        y += rng.uniform(0.5, 0.62)
    shirts = ["#1a1a1f", "#26222a", "#3a1d24", "#202a38", "#2f2f33", "#4a2630", "#15151a", "#5a5050"]
    heads = ["#3a2a22", "#5b4334", "#1c1714", "#7a5a45", "#2b2420"]
    for x, y in sorted(people, key=lambda p: p[1]):
        z = FLOOR_Z + rng.uniform(1.55, 1.75)
        out.append(poly(disc(x, y, z - 0.2, 0.22, 10), rng.choice(shirts)))
        out.append(poly(disc(x, y + 0.03, z, 0.11, 8), rng.choice(heads)))
        if rng.random() < 0.05:  # a raised phone
            out.append(poly(disc(x + 0.15, y + 0.1, z + 0.35, 0.06, 6), "#e8f0ff"))


def crew_member(out: list[str], x: float, y: float) -> None:
    """A crew member in black standing on the pit floor, facing the crowd: legs, torso, arms, neck and head."""
    z = FLOOR_Z
    for dx in (-0.15, 0.03):  # legs
        box(out, x + dx, x + dx + 0.12, y - 0.07, y + 0.07, z, z + 0.85, "#121216", "#0a0a0d")
    box(out, x - 0.21, x + 0.21, y - 0.12, y + 0.12, z + 0.8, z + 1.42, "#141418", "#0d0d11")  # torso
    for x0 in (x - 0.3, x + 0.21):  # arms down at the sides
        box(out, x0, x0 + 0.09, y - 0.06, y + 0.06, z + 0.72, z + 1.38, "#121216", "#0a0a0d")
    out.append(poly(disc(x, y, z + 1.45, 0.24, 10), "#101014"))  # shoulders
    box(out, x - 0.05, x + 0.05, y - 0.05, y + 0.05, z + 1.45, z + 1.55, "#3e3028", "#33271f")  # neck
    box(out, x - 0.09, x + 0.09, y - 0.11, y + 0.07, z + 1.52, z + 1.7, "#4a3a30", "#2e241d")  # head, hair at the back
    out.append(poly(disc(x, y - 0.02, z + 1.72, 0.11, 8), "#4a3a30"))


def pit(out: list[str], rng: random.Random) -> None:
    out.append(
        poly(
            [(-OFF_X, LIP_Y, FLOOR_Z), (OFF_X, LIP_Y, FLOOR_Z), (OFF_X, PIT_Y, FLOOR_Z), (-OFF_X, PIT_Y, FLOOR_Z)],
            "#2b2a30",
        )
    )
    for k in range(9):  # crowd-barrier frames and a working light on the pit floor
        x0 = -9.0 + k * 2.0
        box(out, x0 + 0.05, x0 + 1.95, PIT_Y - 0.5, PIT_Y, FLOOR_Z, FLOOR_Z + 0.08, "#3a3b40", "#202125")
        out.append(line((x0 + 0.1, PIT_Y, FLOOR_Z + 1.2), (x0 + 1.9, PIT_Y, FLOOR_Z + 1.2), C_BARR, 3))
        for xb in (x0 + 0.1, x0 + 1.9):
            out.append(line((xb, PIT_Y, FLOOR_Z), (xb, PIT_Y, FLOOR_Z + 1.2), C_BARR, 2.5))
        out.append(
            poly(
                [
                    (x0 + 0.1, PIT_Y, FLOOR_Z + 0.2),
                    (x0 + 1.9, PIT_Y, FLOOR_Z + 0.2),
                    (x0 + 1.9, PIT_Y, FLOOR_Z + 1.2),
                    (x0 + 0.1, PIT_Y, FLOOR_Z + 1.2),
                ],
                "#e6e8ee",
                opacity=0.18,
            )
        )
    for x in (-7.5, -4.0, -0.8, 2.6, 6.2):  # security and camera ops in the pit
        crew_member(out, x, PIT_Y + 1.0 + rng.uniform(-0.3, 0.3))
    for x0 in np.arange(-6.6, 6.6, 2.2):  # front fills on the pit floor, along the stage front
        box(out, x0 + 0.1, x0 + 1.0, LIP_Y - 1.4, LIP_Y - 0.8, FLOOR_Z, FLOOR_Z + 0.5, "#16161a", "#0b0b0e")


def offstage(out: list[str], rng: random.Random) -> None:
    """The deck running on, darker, into the wings, dark floor beyond, and road cases; no walls."""
    for sgn in (-1, 1):
        x0, x1 = sorted((sgn * STAGE_X, sgn * OFF_X))
        out.append(poly([(x0, LIP_Y, 0.0), (x1, LIP_Y, 0.0), (x1, 16.0, 0.0), (x0, 16.0, 0.0)], C_OFF))
        w0, w1 = sorted((sgn * STAGE_X, sgn * WING_X))
        out.append(poly([(w0, LIP_Y, 0.0), (w1, LIP_Y, 0.0), (w1, 16.0, 0.0), (w0, 16.0, 0.0)], C_WING_DECK))
        for x in np.arange(w0, w1 + 1e-6, DECK_W):
            out.append(line((x, LIP_Y, 0.01), (x, 16.0, 0.01), C_SEAM, 1.6, 0.22))
        for y in np.arange(LIP_Y, 16.0 + 1e-6, DECK_D):
            out.append(line((w0, y, 0.01), (w1, y, 0.01), C_SEAM, 1.6, 0.22))
        for y0 in (1.0, 3.2, 7.5, 9.8):  # road cases
            xa = sgn * (STAGE_X + 2.2 + rng.uniform(0.0, 0.8))
            xs = sorted((xa, xa + sgn * 1.2))
            box(out, xs[0], xs[1], y0, y0 + 0.8, 0.0, 0.9, "#25262b", "#141418")


def wing_gear(out: list[str], rng: random.Random) -> None:
    """What stands in the wings: sidefills, amp racks, a monitor desk, a guitar world, a tech table, cases."""
    for sgn in (-1, 1):

        def xs(a: float, b: float, sgn: int = sgn) -> tuple[float, float]:
            """Offstage of the deck edge by ``a`` to ``b`` metres, on this side."""
            return tuple(sorted((sgn * (STAGE_X + a), sgn * (STAGE_X + b))))  # type: ignore[return-value]

        x0, x1 = xs(1.8, 2.8)  # sidefill stack at the downstage wing
        y0, y1 = LIP_Y + 0.3, LIP_Y + 1.1
        box(out, x0, x1, y0, y1, 0.0, 1.7, "#1a1a1e", "#101013")
        face = x0 if sgn > 0 else x1
        for z in (0.45, 0.9, 1.35):
            out.append(line((face, y0 + 0.05, z), (face, y1 - 0.05, z), "#2c2d33", 2.0))
        x0, x1 = xs(2.0, 2.8)  # amp racks with status lights
        for y0 in (5.2, 6.2):
            box(out, x0, x1, y0, y0 + 0.8, 0.0, 1.6, "#202126", "#121316")
            for k, colour in enumerate(("#5dff9a", "#4aa3ff", "#5dff9a")):
                out.append(poly(disc((x0 + x1) / 2, y0 + 0.2 + 0.2 * k, 1.61, 0.04, 8), colour))
        x0, x1 = xs(4.0, 5.2)  # tech table with a work light
        box(out, x0, x1, 7.0, 7.8, 0.0, 0.9, "#3a3b40", "#1d1e22")
        for k in np.linspace(1.0, 0.2, 6):
            out.append(poly(disc((x0 + x1) / 2, 7.4, 0.91, 0.7 * k, 20), "#ffd9a0", opacity=0.07))
        for ya, yb, h in ((2.0, 3.2, 0.9), (2.0, 3.2, 1.8), (9.2, 10.4, 0.9), (11.0, 12.0, 1.1)):  # stacked cases
            x0, x1 = xs(5.6, 7.0)
            box(out, x0, x1, ya, yb, h - 0.9 if h > 1.2 else 0.0, h, "#2a2b30", "#16171a")
            out.append(line((x0, ya, h + 0.001), (x1, ya, h + 0.001), "#8d9199", 1.6, 0.6))
        for ya, yb in ((1.4, 1.5), (4.6, 4.7), (8.0, 8.1)):  # cable runs from the stage into the wing
            x0, x1 = xs(0.0, 4.5 + rng.uniform(0.0, 1.0))
            out.append(poly([(x0, ya, 0.012), (x1, ya, 0.012), (x1, yb, 0.012), (x0, yb, 0.012)], "#0a0a0c"))
    x0, x1 = sorted((-(STAGE_X + 3.0), -(STAGE_X + 4.6)))  # monitor desk with a lit screen
    box(out, x0, x1, 1.6, 2.4, 0.0, 1.0, "#2a2b30", "#16171a")
    out.append(
        poly([(x0 + 0.2, 2.3, 1.0), (x1 - 0.2, 2.3, 1.0), (x1 - 0.2, 2.35, 1.45), (x0 + 0.2, 2.35, 1.45)], "#3d6fb0")
    )
    for k in range(8):
        out.append(line((x0 + 0.2 + 0.15 * k, 1.75, 1.01), (x0 + 0.2 + 0.15 * k, 2.0, 1.01), "#cfd3da", 1.4, 0.7))
    gx = STAGE_X + 3.0  # a guitar world: a rack and three guitars on stands
    box(out, gx, gx + 1.4, 3.4, 4.0, 0.0, 0.4, "#24252a", "#141518")
    for k, colour in enumerate(("#8a4a1f", "#101012", "#e9e4dc")):
        x = gx + 0.3 + 0.4 * k
        body = [(x + 0.17 * math.cos(t), 3.7, 0.65 + 0.24 * math.sin(t)) for t in np.linspace(0, 2 * math.pi, 16)]
        out.append(poly(body, colour, "#3a3a3e", 1.0))
        out.append(line((x, 3.7, 0.85), (x, 3.7, 1.45), "#6a4a2a", 3.0))


def deck(out: list[str]) -> None:
    out.append(
        poly(
            [(-STAGE_X, LIP_Y, 0.0), (STAGE_X, LIP_Y, 0.0), (STAGE_X, STAGE_BACK, 0.0), (-STAGE_X, STAGE_BACK, 0.0)],
            C_DECK,
        )
    )
    for k in np.linspace(1.0, 0.2, 20):  # the white wash, brightest centre stage
        ring = [(6.5 * k * math.cos(t), 3.0 + 3.6 * k * math.sin(t), 0.005) for t in np.linspace(0, 2 * math.pi, 48)]
        out.append(poly(ring, "#f6f1ea", opacity=0.024))
    x = -STAGE_X
    while x <= STAGE_X + 1e-6:
        out.append(line((x, LIP_Y, 0.01), (x, STAGE_BACK, 0.01), C_SEAM, 2.0, 0.75))
        x += DECK_W
    y = LIP_Y
    while y <= STAGE_BACK + 1e-6:
        out.append(line((-STAGE_X, y, 0.01), (STAGE_X, y, 0.01), C_SEAM, 2.0, 0.75))
        y += DECK_D


def stage_edge(out: list[str]) -> None:
    """White tape along the lip, the centre mic stand, pedalboards and setlists."""
    e = 0.05
    out.append(
        poly(
            [(-STAGE_X, LIP_Y, 0.0), (STAGE_X, LIP_Y, 0.0), (STAGE_X, LIP_Y + e, 0.0), (-STAGE_X, LIP_Y + e, 0.0)], TAPE
        )
    )
    mic = 0.5  # off centre, clear of the Reference Point tape
    out.append(poly(disc(mic, LIP_Y + 0.7, 0.0, 0.16, 12), "#3d4048"))
    out.append(line((mic, LIP_Y + 0.7, 0.0), (mic, LIP_Y + 0.6, 1.5), "#c9ccd2", 2.5))
    for x0, leds in ((-5.2, ("#4aa3ff", "#5dff9a", "#ff5d5d")), (3.9, ("#4aa3ff", "#ffd35d"))):  # pedalboards
        box(out, x0, x0 + 0.95, LIP_Y + 0.3, LIP_Y + 0.68, 0.0, 0.08, "#2f3238", "#1d1f23")
        for k, colour in enumerate(leds):
            out.append(poly(disc(x0 + 0.2 + 0.25 * k, LIP_Y + 0.42, 0.081, 0.03, 8), colour))
    for x, y, turn in ((-6.1, 0.45, 0.2), (-2.8, 0.4, -0.1), (2.7, 0.45, 0.15), (5.6, 0.5, -0.2)):  # setlists
        c, s_ = math.cos(turn), math.sin(turn)
        sheet = [
            (x + c * dx - s_ * dy, y + s_ * dx + c * dy, 0.012) for dx, dy in ((0, 0), (0.21, 0), (0.21, 0.3), (0, 0.3))
        ]
        out.append(poly(sheet, "#f4f4f2"))


def riser(out: list[str]) -> None:
    x0, x1, y0, y1, h = RISER
    for xs in (-3.9, 2.3):  # treads up the riser front, hidden where the riser stands in front of them
        for k in range(3):
            z = h * (k + 1) / 3
            yy = y0 - 0.9 + k * 0.3
            box(out, xs, xs + 1.6, yy, y0, 0.0, z, "#0b0b0d", "#060607")
            for a, b in (((xs, yy), (xs + 1.6, yy)), ((xs, yy), (xs, y0)), ((xs + 1.6, yy), (xs + 1.6, y0))):
                out.append(line((*a, z), (*b, z), "#f4f4f2", 1.4))
    box(out, x0, x1, y0, y1, 0.0, h, C_RISER_TOP, C_RISER_FACE)
    x = x0
    while x <= x1 + 1e-6:  # riser built from the same 1 x 2 m decks
        out.append(line((x, y0, h), (x, y1, h), C_SEAM, 1.6, 0.85))
        x += DECK_W
    y = y0
    while y <= y1 + 1e-6:
        out.append(line((x0, y, h), (x1, y, h), C_SEAM, 1.6, 0.85))
        y += DECK_D
    e = 0.1  # white safety edge along the open sides
    for strip in (
        [(x0, y0, h), (x1, y0, h), (x1, y0 + e, h), (x0, y0 + e, h)],
        [(x0, y0, h), (x0 + e, y0, h), (x0 + e, y1, h), (x0, y1, h)],
        [(x1 - e, y0, h), (x1, y0, h), (x1, y1, h), (x1 - e, y1, h)],
    ):
        out.append(poly(strip, "#f4f4f2"))
    for xa in (-4.3, -2.4, 2.4, 4.3):  # backline amps
        box(out, xa - 0.4, xa + 0.4, y1 - 0.8, y1 - 0.2, h, h + 1.2, "#3e4148", "#2b2d32")
    drum = (0.0, y0 + 2.2)
    for dx, dy, r, z in (
        (0.0, 0.2, 0.28, 0.55),
        (-0.3, -0.2, 0.12, 0.75),
        (0.3, -0.22, 0.13, 0.75),
        (0.55, 0.2, 0.2, 0.6),
        (-0.55, 0.1, 0.17, 0.7),
    ):
        out.append(poly(disc(drum[0] + dx, drum[1] + dy, h + z, r, 20), "#e9e4dc", "#7a756c", 1.2))
    for dx, dy, r, z in (
        (-0.75, -0.45, 0.23, 1.3),
        (0.8, -0.4, 0.26, 1.35),
        (0.25, -0.6, 0.2, 1.15),
        (-0.35, 0.55, 0.24, 1.45),
    ):
        out.append(poly(disc(drum[0] + dx, drum[1] + dy, h + z, r, 24), "#b98a3e", "#6a4a1a", 1.0))
        out.append(poly(disc(drum[0] + dx, drum[1] + dy, h + z + 0.001, r * 0.35, 12), "#d6aa5a"))


def side_risers(out: list[str]) -> None:
    """A higher deck on each side of the band riser, into the wings, with three moving heads each."""
    xi, xo, y0, y1, h = SIDE_RISER
    for sgn in (-1, 1):
        x0, x1 = sorted((sgn * xi, sgn * xo))
        box(out, x0, x1, y0, y1, 0.0, h, C_RISER_TOP, C_RISER_FACE)
        x = x0
        while x <= x1 + 1e-6:  # built from the same 1 x 2 m decks
            out.append(line((x, y0, h), (x, y1, h), C_SEAM, 1.4, 0.5))
            x += DECK_W
        y = y0
        while y <= y1 + 1e-6:
            out.append(line((x0, y, h), (x1, y, h), C_SEAM, 1.4, 0.5))
            y += DECK_D
        e, inner = 0.1, sgn * xi  # white safety edge along the front and the side facing the riser
        out.append(poly([(x0, y0, h), (x1, y0, h), (x1, y0 + e, h), (x0, y0 + e, h)], "#f4f4f2"))
        ie = sorted((inner, inner + sgn * e))
        out.append(poly([(ie[0], y0, h), (ie[1], y0, h), (ie[1], y1, h), (ie[0], y1, h)], "#f4f4f2"))
        for xm in (sgn * (xi + 0.7), sgn * (xi + 2.0), sgn * (xi + 3.3)):  # moving heads: base, head, lens
            ym = (y0 + y1) / 2
            box(out, xm - 0.22, xm + 0.22, ym - 0.22, ym + 0.22, h, h + 0.15, "#1c1d21", "#111215")
            out.append(poly(disc(xm, ym, h + 0.55, 0.24, 18), "#202126", "#3a3c42", 1.5))
            out.append(poly(disc(xm, ym - 0.05, h + 0.56, 0.13, 14), "#5a5e66"))


def leg(out: list[str], sgn: int, y: float, x_out: float, x_in: float, x_tie: float, falls: bool) -> None:
    """A sheer white leg hung above the frame and tied back: drawn as fold strips, lit and shaded in
    turn, that hang from the top, bunch into the tie along a sagging sweep and fall below it in a bell."""
    folds, steps = 18, 40
    zs = np.linspace(LEG_TOP, 0.0, steps)

    def at(u: float, z: float) -> float:
        """Where the fold ``u`` (0 outer edge, 1 inner edge at the top) hangs at height ``z``."""
        x_top = x_out + (x_in - x_out) * u
        x_knot = x_tie + (u - 0.5) * 0.35  # folds bunched across the tie
        if z >= LEG_KNOT:
            t = (z - LEG_KNOT) / (LEG_TOP - LEG_KNOT)
            if falls:  # straight down from the top, curving in only toward the tie
                pull, sag = 1.0 - (1.0 - t) ** 2.2, 0.15 * u * math.sin(math.pi * t)
            else:  # stays near its top position, then sweeps in; the inner folds sag outward on the way
                pull, sag = t**0.55, 0.45 * u * math.sin(math.pi * t)
            return x_knot + (x_top - x_knot) * pull + sag * (x_out - x_in) / abs(x_out - x_in)
        flare = (1.0 - z / LEG_KNOT) ** 1.4  # below the tie the cloth falls open into a bell
        return x_knot + (u - 0.5) * 1.1 * flare

    def hem(u: float) -> float:
        return 0.04 + 0.04 * math.sin(7.0 * u * math.pi)

    # Folds of uneven width, the same for each leg on every run.
    rng = random.Random(round(1000 * (y + x_out)))
    widths = [rng.uniform(0.4, 1.8) for _ in range(folds)]
    us = np.concatenate([[0.0], np.cumsum(widths) / sum(widths)])
    lines = [[(sgn * at(u, z), y, max(z, hem(u))) for z in zs] for u in us]
    tones = ("#f1efeb", "#cfc9c2", "#e2ded8", "#b3ada6")  # lit, half, lit, deep
    for k, (a, b) in enumerate(pairwise(lines)):  # each strip between two folds
        out.append(poly(a + b[::-1], tones[k % 4], opacity=0.78))
    for k, fold in enumerate(lines[1:-1]):
        if k % 2 == 0:  # a highlight along every other fold
            for p0, p1 in zip(fold[::3], fold[3::3], strict=False):
                out.append(line(p0, p1, "#fbfaf8", 1.4, 0.55))


def side_lights(out: list[str]) -> None:
    """A small white fixture at each side of the stage, with its glow on the deck."""
    for sgn in (-1, 1):
        x, y = sgn * (STAGE_X - 0.15), 2.6
        for k in np.linspace(1.0, 0.2, 8):
            out.append(poly(disc(x, y, 0.02, 1.1 * k, 24), "#ffffff", opacity=0.06))
        box(out, x - 0.12, x + 0.12, y - 0.25, y + 0.25, 0.0, 0.18, "#e9ecf2", "#5a5d64")


def tape(out: list[str]) -> None:
    arm = 0.35
    for name, (x, y, z) in CORNERS.items():
        sx = -1.0 if x > 0 else 1.0
        sy = 1.0 if name.startswith("DS") else -1.0
        out.append(line((x, y, z), (x + sx * arm, y, z), TAPE, 3))
        out.append(line((x, y, z), (x, y + sy * arm, z), TAPE, 3))
    out.append(line((-0.25, 0.0, 0.0), (0.25, 0.0, 0.0), TAPE, 3))
    out.append(line((0.0, 0.0, 0.0), (0.0, 0.25, 0.0), TAPE, 3))


def build_svg() -> str:
    rng = random.Random(7)
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W:.0f} {H:.0f}" width="{W:.0f}" height="{H:.0f}">',
        f'<rect x="0" y="0" width="{W:.0f}" height="{H:.0f}" fill="{C_BG}"/>',
    ]
    house(out, rng)
    pit(out, rng)
    offstage(out, rng)
    wing_gear(out, rng)
    deck(out)
    tape(out)
    stage_edge(out)
    side_lights(out)
    riser(out)
    side_risers(out)
    for y, x_out, x_in, x_tie, falls in LEGS:  # downstage pair first, the nearer pair over it
        for sgn in (-1, 1):
            leg(out, sgn, y, x_out, x_in, x_tie, falls)
    out.append("</svg>\n")
    return "\n".join(part for part in out if part)


def picture() -> Image.Image:
    """The scene rendered, hazed and darkened by a gamma curve."""
    png = subprocess.run(["rsvg-convert", "-f", "png"], input=build_svg().encode(), capture_output=True, check=True)
    img = Image.open(io.BytesIO(png.stdout)).convert("RGB")
    img = Image.blend(img, img.filter(ImageFilter.GaussianBlur(18)), 0.12)
    return img.point([round(255 * (v / 255) ** GAMMA) for v in range(256)] * 3)


def main() -> None:
    assert pull_back_to_valid(K1, K2, 0.0, 0.0) == (K1, K2, 0.0, 0.0), "lens pair cannot reach the corners"
    picture().save(ASSET, quality=90, optimize=True)
    pos = ", ".join(f"{v:g}" for v in CAMERA[:3])
    print(f"Camera: pos=({pos}) pitch={CAMERA[3]:g} yaw={CAMERA[4]:g} roll={CAMERA[5]:g} hfov={CAMERA[6]:g}")
    print(f"Lens:   barrel / fisheye {K1:g}, corner correction {K2:g}")
    print(f"Grid:   {GRID_W:g} x {GRID_D:g} m, x_offset 0, y_offset {GRID_D / 2:g}, z_offset 0")
    for name, corner in CORNERS.items():
        x, y = lens(pinhole([corner]))[0]
        print(f"  {name}: ({x:.1f}, {y:.1f})")
    print(f"Wrote: {ASSET.relative_to(ROOT)} ({ASSET.stat().st_size // 1024} kB)")
    grid = {"width": GRID_W, "depth": GRID_D, "spacing": GRID_SPACING}
    grid |= {"x_offset": 0.0, "y_offset": GRID_D / 2, "z_offset": 0.0}
    cal = write_calibration(ASSET, "scripts/gen_concert_default.py", camera_from(CAMERA, K1, K2), grid)
    print(f"Wrote: {cal.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
