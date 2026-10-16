#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Render the Media Gallery's Concert default: a concert stage seen from a camera on the back truss.

The camera hangs on the upstage back truss and looks down and downstage over the whole stage into
the crowd: black 1 x 2 m stage decks with silver frames from wall to wall, a band riser built from the
same decks with a white safety edge, monitor wedges and pedalboards along the stage edge, three pairs
of truss towers each masked by a tied-back white leg, a pit with a crowd barrier, and the crowd. The
picture is bowed by a barrel lens through the overlay's own model, so a station set to the printed
camera and lens draws its grid on the deck seams. Tape marks the grid's corners and the Reference
Point.

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
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
from gallery_calibration import camera_from, write_calibration  # noqa: E402
from PIL import Image, ImageFilter  # noqa: E402

from openfollow.lens_model import pull_back_to_fold  # noqa: E402
from openfollow.scene.solver import apply_overlay_distortion, project_points  # noqa: E402

ASSET = ROOT / "openfollow" / "video" / "inputs" / "assets" / "concert_default.jpg"
W, H = 1920.0, 1080.0
# Back truss upstage of the deck: pos_x, pos_y, pos_z, pitch, yaw, roll, horizontal fov.
CAMERA = np.array([0.0, 15.0, 12.5, -44.0, 180.0, 0.0, 78.0])
K1, K2 = -0.25, 0.04
# Pinhole geometry is cut to this box before the lens bows it.
CLIP = (-0.25 * W, -0.25 * H, 1.25 * W, 1.25 * H)
STEP_PX = 6.0  # edges are split this finely, so a straight edge bows like the lens bows it

TOWER_X, WALL_X, STAGE_BACK, LIP_Y = 8.0, 11.0, 14.0, -2.0  # deck runs wall to wall
DECK_W, DECK_D = 1.0, 2.0  # 1 m across the front, 2 m running back
FLOOR_Z = -1.8  # house floor below the deck
PIT_Y = LIP_Y - 2.7  # barricade
TOWER_ROWS = (LIP_Y + 0.6, 4.0, 9.0)  # the front pair at the stage edge
LEG_TOP, LEG_KNOT = 9.5, 3.4  # leg height and tie-back height
GRID_W, GRID_D, GRID_SPACING = 12.0, 8.0, 2.0  # grid lines on every second deck seam
CORNERS = {
    "DSL": (GRID_W / 2, 0.0, 0.0),
    "DSR": (-GRID_W / 2, 0.0, 0.0),
    "USR": (-GRID_W / 2, GRID_D, 0.0),
    "USL": (GRID_W / 2, GRID_D, 0.0),
}
RISER = (-5.0, 5.0, 8.0, 12.0, 0.6)  # x0, x1, y0 (front), y1, height

C_BG, C_HOUSE, C_DECK, C_SEAM = "#050507", "#0d0b10", "#070708", "#a9adb4"
C_WALL, C_RISER_FACE, C_LIP = "#0c0c0f", "#0e0f12", "#f2f4ff"
C_TRUSS, C_DRAPE, C_DRAPE_DARK, C_BARR = "#9aa0a8", "#d9d6d2", "#7f7b77", "#b8bcc4"
TAPE = "#ffffff"


def pinhole(pts) -> np.ndarray:
    return project_points(CAMERA, np.asarray(pts, dtype=float), W, H)


def lens(px: np.ndarray) -> np.ndarray:
    return apply_overlay_distortion(px, W, H, K1, K2)


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


def pit(out: list[str], rng: random.Random) -> None:
    out.append(
        poly(
            [(-WALL_X, LIP_Y, FLOOR_Z), (WALL_X, LIP_Y, FLOOR_Z), (WALL_X, PIT_Y, FLOOR_Z), (-WALL_X, PIT_Y, FLOOR_Z)],
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
        y = PIT_Y + 1.0 + rng.uniform(-0.3, 0.3)
        out.append(poly(disc(x, y, FLOOR_Z + 1.45, 0.24, 10), "#101014"))
        out.append(poly(disc(x, y - 0.02, FLOOR_Z + 1.68, 0.11, 8), "#4a3a30"))
    for x0 in (-8.6, -5.4, -1.0, 1.9, 5.6):  # flight cases
        box(out, x0, x0 + 1.2, LIP_Y - 1.2, LIP_Y - 0.6, FLOOR_Z, FLOOR_Z + 0.9, "#cfd2d8", "#5d6068")


def deck(out: list[str]) -> None:
    out.append(
        poly(
            [(-WALL_X, LIP_Y, 0.0), (WALL_X, LIP_Y, 0.0), (WALL_X, STAGE_BACK, 0.0), (-WALL_X, STAGE_BACK, 0.0)], C_DECK
        )
    )
    x = -WALL_X
    while x <= WALL_X + 1e-6:
        out.append(line((x, LIP_Y, 0.0), (x, STAGE_BACK, 0.0), C_SEAM, 1.6, 0.85))
        x += DECK_W
    y = LIP_Y
    while y <= STAGE_BACK + 1e-6:
        out.append(line((-WALL_X, y, 0.0), (WALL_X, y, 0.0), C_SEAM, 1.6, 0.85))
        y += DECK_D
    out.append(line((-WALL_X, LIP_Y, 0.02), (WALL_X, LIP_Y, 0.02), C_LIP, 7))
    out.append(line((-WALL_X, LIP_Y + 0.05, 0.02), (WALL_X, LIP_Y + 0.05, 0.02), "#ff9ad6", 3, 0.7))


def pools(out: list[str]) -> None:
    for cx, cy, r in ((-3.5, 3.2, 1.7), (3.8, 5.0, 1.6), (0.0, 7.0, 1.3)):
        for k in np.linspace(1.0, 0.3, 8):
            out.append(poly(disc(cx, cy, 0.01, r * k, 40), "#fffbe8", opacity=0.06))


def wedges(out: list[str]) -> None:
    for x in (-4.5, -1.8, 1.8, 4.5):  # downstage monitor wedges and a centre mic stand
        box(out, x - 0.35, x + 0.35, LIP_Y + 0.15, LIP_Y + 0.75, 0.0, 0.45, "#4c5059", "#33363d")
    out.append(poly(disc(0.0, LIP_Y + 1.4, 0.0, 0.18, 12), "#3d4048"))
    out.append(line((0.0, LIP_Y + 1.4, 0.0), (0.0, LIP_Y + 1.2, 1.5), "#c9ccd2", 2.5))
    for x0, y0 in ((-5.8, LIP_Y + 1.1), (4.8, LIP_Y + 1.3)):  # pedalboards, just upstage of the wedges
        box(out, x0, x0 + 1.0, y0, y0 + 0.45, 0.0, 0.08, "#5d626d", "#3d4048")


def riser(out: list[str]) -> None:
    x0, x1, y0, y1, h = RISER
    for xs in (-3.9, 2.3):  # treads up the riser front, hidden where the riser stands in front of them
        for k in range(3):
            z = h * (k + 1) / 3
            yy = y0 - 0.9 + k * 0.3
            box(out, xs, xs + 1.6, yy, y0, 0.0, z, "#d8dade", "#9a9da4")
    box(out, x0, x1, y0, y1, 0.0, h, C_DECK, C_RISER_FACE)
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
    drum = (0.0, 10.2)
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


def truss_tower(out: list[str], x: float, y: float, top: float) -> None:
    s = 0.2
    legs = [(x - s, y - s), (x + s, y - s), (x + s, y + s), (x - s, y + s)]
    for lx, ly in legs:
        out.append(line((lx, ly, 0.0), (lx, ly, top), C_TRUSS, 2.2))
    z = 0.0
    k = 0
    while z < top:
        z1 = min(z + 0.5, top)
        a, b = legs[k % 2], legs[(k % 2) + 1]
        out.append(line((a[0], a[1], z), (b[0], b[1], z1), C_TRUSS, 1.2, 0.8))
        z, k = z1, k + 1
    on = -1.0 if x > 0 else 1.0  # towards centre stage
    for dz in (top - 1.0, top - 2.6, top - 4.2, top - 5.8):  # heads hung on the onstage face
        xa, xb = sorted((x + on * s, x + on * (s + 0.3)))
        box(out, xa, xb, y - 0.15, y + 0.15, dz - 0.3, dz, "#1a1b1f", "#0c0c0f")
        out.append(poly(disc(x + on * (s + 0.31), y, dz - 0.15, 0.09, 10), "#f5d8ff"))


def beams(out: list[str]) -> None:
    for sx, sy, sz, tx, ty, r in (
        (-8.0, 4.0, 7.0, -3.5, 3.2, 1.5),
        (8.0, 4.0, 6.0, 3.8, 5.0, 1.4),
        (-8.0, 9.0, 5.0, -12.0, -20.0, 2.5),
        (8.0, -1.4, 7.0, 14.0, -24.0, 2.5),
        (0.0, 14.5, 9.5, 0.0, 7.0, 1.2),
    ):
        src = np.array((sx, sy, sz))
        tgt = np.array((tx, ty, 0.0 if ty > LIP_Y else FLOOR_Z))
        d = tgt - src
        side = np.cross(d, (0.0, 0.0, 1.0))
        side = side / (np.linalg.norm(side) or 1.0) * r
        for k, a in ((1.0, 0.05), (0.55, 0.06), (0.25, 0.08)):
            out.append(poly([src, tgt + side * k, tgt - side * k], "#f4f0ff", opacity=a))


def walls(out: list[str]) -> None:
    for sgn in (-1, 1):
        x = sgn * WALL_X
        out.append(poly([(x, LIP_Y, 0.0), (x, STAGE_BACK, 0.0), (x, STAGE_BACK, 11.0), (x, LIP_Y, 11.0)], C_WALL))
        for k in range(1, 8):  # wall panels
            yy = LIP_Y + k * (STAGE_BACK - LIP_Y) / 8
            out.append(line((x, yy, 0.0), (x, yy, 11.0), "#1b1b20", 2))


def leg(out: list[str], y: float) -> None:
    """A pair of white legs just downstage of a tower row, masking it: straight on the truss side,
    swept in toward centre stage at the top, gathered back to a tie, falling to the deck."""
    for sgn in (-1, 1):
        xo = sgn * (TOWER_X + 0.35)

        def inner(z: float, sgn: int = sgn) -> float:
            if z >= LEG_KNOT:  # rod to tie: a near-straight diagonal with a slight belly toward centre
                t = (z - LEG_KNOT) / (LEG_TOP - LEG_KNOT)
                return sgn * (TOWER_X - 0.55 - 2.2 * t - 0.3 * math.sin(math.pi * t))
            return sgn * (TOWER_X - 0.55 - 0.7 * math.sqrt(1.0 - z / LEG_KNOT))  # bell from the tie to the deck

        top, knot = LEG_TOP, LEG_KNOT
        zs = list(np.linspace(top, 0.0, 40))
        ring = [(xo, y, top), (xo, y, 0.0)] + [(inner(z), y, z) for z in reversed(zs)]
        out.append(poly(ring, C_DRAPE))
        for k in range(1, 8):  # pleats, gathered at the tie
            t = k / 8
            a = (xo + (inner(top) - xo) * t, y, top)
            b = (xo + (inner(knot) - xo) * t, y, knot)
            c = (xo + (inner(0.0) - xo) * t, y, 0.0)
            out.append(line(a, b, C_DRAPE_DARK, 2.5, 0.6))
            out.append(line(b, c, C_DRAPE_DARK, 2.5, 0.6))
        out.append(line((xo, y, knot), (inner(knot) - sgn * 0.05, y, knot), "#55524e", 4))


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
    deck(out)
    pools(out)
    tape(out)
    wedges(out)
    riser(out)
    walls(out)
    for y in TOWER_ROWS:  # far to near: each leg, then the tower behind it
        leg(out, y - 0.45)
        for sgn in (-1, 1):
            truss_tower(out, sgn * TOWER_X, y, 9.0)
    beams(out)
    out.append("</svg>\n")
    return "\n".join(part for part in out if part)


def main() -> None:
    assert pull_back_to_fold(K1, K2) == (K1, K2), "lens pair folds inside the frame"
    png = subprocess.run(["rsvg-convert", "-f", "png"], input=build_svg().encode(), capture_output=True, check=True)
    img = Image.open(io.BytesIO(png.stdout)).convert("RGB")
    haze = img.filter(ImageFilter.GaussianBlur(18))
    img = Image.blend(img, haze, 0.18)
    img.save(ASSET, quality=90, optimize=True)
    pos = ", ".join(f"{v:g}" for v in CAMERA[:3])
    print(f"Camera: pos=({pos}) pitch={CAMERA[3]:g} yaw={CAMERA[4]:g} roll={CAMERA[5]:g} hfov={CAMERA[6]:g}")
    print(f"Lens:   barrel / fisheye {K1:g}, edge fit {K2:g}")
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
