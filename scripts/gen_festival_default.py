#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Michel Honold and Paul Hermann
"""Render the Media Gallery's Festival default: a festival stage seen from a camera on the front truss.

The camera hangs on the front truss over the barrier and looks steeply down onto the stage under
flat white work light: dark 2 x 1 m stage decks with dark seams, wings to each side, a black
skirt on the front, a drum riser with a kit upstage centre, a keys riser stage left, backline,
mic stands and wedges downstage, an LED wall across the back, the roof's four truss towers.
In front of the stage: a line of subs with front fills on top, the photo pit, a mojo-style crowd
barrier on its feet and ground protection panels on the floor.

The picture is bowed by a 2.8 mm M12 lens through the overlay's own model, about a lens centre off
the frame centre, so a station set to the printed camera and lens draws its grid on the tape. Tape
marks the grid's corners and the Reference Point.

Needs ``rsvg-convert`` (librsvg) and Pillow. Prints the camera, the lens, the grid and the corners'
pixels.

    poetry run python scripts/gen_festival_default.py
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

from openfollow.lens_model import pull_back_to_valid  # noqa: E402
from openfollow.scene.solver import apply_overlay_distortion, invert_overlay_distortion, project_points  # noqa: E402

ASSET = ROOT / "openfollow" / "video" / "inputs" / "assets" / "festival_default.jpg"
W, H = 1920.0, 1080.0
HALF_DIAG = 0.5 * math.hypot(W, H)

# A 2.8 mm M12 lens, 100° horizontal angle of view. The centre in whole pixels (x right, y down),
# stored in half-diagonals.
K1, K2 = -0.31, -0.01
CENTRE_PX = (28.0, -16.0)
CX, CY = CENTRE_PX[0] / HALF_DIAG, CENTRE_PX[1] / HALF_DIAG
H_AOV = 100.0  # horizontal angle of view across the lens centre's row, edge to edge

FLOOR_Z = -1.7  # house floor below the deck
CAM_POS = (0.0, -4.0, 9.3)  # front truss over the barrier: 4 m before the lip, 11 m above the floor
PITCH, YAW, ROLL = -42.0, 0.0, 0.0

STAGE_X, WING_X, STAGE_BACK = 8.0, 12.0, 11.0
DECK_W, DECK_D = 2.0, 1.0  # 2 m across, 1 m deep
SUB_Y0, SUB_Y1 = -1.1, -0.1
BARR_Y = -2.6  # barrier's front face (audience side)
GRID_W, GRID_D, GRID_SPACING = 12.0, 6.0, 2.0
CORNERS = {
    "DSL": (GRID_W / 2, 0.0, 0.0),
    "DSR": (-GRID_W / 2, 0.0, 0.0),
    "USR": (-GRID_W / 2, GRID_D, 0.0),
    "USL": (GRID_W / 2, GRID_D, 0.0),
}
DRUM_RISER = (-1.5, 1.5, 7.0, 9.0, 0.5)
KEYS_RISER = (4.4, 6.4, 6.8, 8.8, 0.4)
LED = (-7.0, 7.0, 10.4, 0.6, 5.1)  # x0, x1, y (front face), z0, z1
LED_PANEL = 0.5
TOWER_X = 8.6  # roof towers, just offstage of the main deck; the wings stand outside the roof
TOWER_Y_FRONT, TOWER_Y_BACK = -0.8, 11.8  # on the ground, clear of the deck's front and back edges
TOWER_PLATE = 0.6  # base plate, square
TOWER_CLEAR = 0.5  # nothing within this of a base plate
SUB_W, SUB_GAP = 1.2, 0.12

STEP_PX = 5.0
NEAR = 0.05
TAPE = "#ffffff"
C_BG = "#060608"
C_DECK, C_WING_DECK, C_SEAM = "#6e6862", "#4f4b47", "#141210"  # worn dark deck under work light, dark gaps
C_RISER_TOP = "#4a4642"


# --- lens -----------------------------------------------------------------------------------


def lens(px: np.ndarray) -> np.ndarray:
    return apply_overlay_distortion(px, W, H, K1, K2, CX, CY)


def unlens(px) -> np.ndarray:
    return invert_overlay_distortion(np.atleast_2d(np.asarray(px, dtype=float)), W, H, K1, K2, CX, CY)


def paraxial_hfov() -> float:
    """The paraxial horizontal fov that makes the frame's lens-centre row span H_AOV degrees."""
    row = H / 2 + CY * HALF_DIAG
    q = unlens([(0.0, row), (W, row)])
    dl, dr = abs(q[0, 0] - W / 2), abs(q[1, 0] - W / 2)
    lo, hi = 100.0, 5000.0
    for _ in range(100):
        f = 0.5 * (lo + hi)
        ang = math.degrees(math.atan(dl / f) + math.atan(dr / f))
        lo, hi = (f, hi) if ang > H_AOV else (lo, f)
    return 2.0 * math.degrees(math.atan((W / 2) / (0.5 * (lo + hi))))


FOV = round(paraxial_hfov(), 1)
CAMERA = np.array([*CAM_POS, PITCH, YAW, ROLL, FOV])


# --- pinhole camera (the overlay's project_points, split at the near plane) ---------------


def _rotation(pitch: float, yaw: float, roll: float) -> np.ndarray:
    a, b, c = math.radians(pitch), -math.radians(yaw), math.radians(roll)
    ca, sa, cb, sb, cc, sc = math.cos(a), math.sin(a), math.cos(b), math.sin(b), math.cos(c), math.sin(c)
    return np.array(
        [
            [cb * cc + sb * sa * sc, -cb * sc + sb * sa * cc, sb * ca],
            [ca * sc, ca * cc, -sa],
            [-sb * cc + cb * sa * sc, sb * sc + cb * sa * cc, cb * ca],
        ]
    )


_R = _rotation(PITCH, YAW, ROLL)
_CAM = np.array([CAM_POS[0], CAM_POS[2], -CAM_POS[1]])
_ASPECT = W / H
_F_NDC = 1.0 / math.tan(math.atan(math.tan(math.radians(FOV) / 2) / _ASPECT))


def to_view(pts) -> np.ndarray:
    p = np.asarray(pts, dtype=float).reshape(-1, 3)
    g = np.column_stack([p[:, 0], p[:, 2], -p[:, 1]])
    return (_R.T @ (g - _CAM).T).T


def view_to_px(v: np.ndarray) -> np.ndarray:
    z = -v[:, 2]
    sx = ((_F_NDC / _ASPECT) * v[:, 0] / z + 1.0) / 2.0 * W
    sy = (1.0 - _F_NDC * v[:, 1] / z) / 2.0 * H
    return np.column_stack([sx, sy])


def pinhole(pts) -> np.ndarray:
    return project_points(CAMERA, np.asarray(pts, dtype=float), W, H)


# --- drawing --------------------------------------------------------------------------------


def _clip_box() -> tuple[float, float, float, float]:
    """The screen border in pinhole pixels with a margin; geometry is cut to it, which keeps the SVG small."""
    t = np.linspace(0, 1, 200)
    border = np.vstack(
        [
            np.column_stack([t * W, 0 * t]),
            np.column_stack([t * W, H + 0 * t]),
            np.column_stack([0 * t, t * H]),
            np.column_stack([W + 0 * t, t * H]),
        ]
    )
    q = unlens(border)
    x0, y0 = q.min(axis=0)
    x1, y1 = q.max(axis=0)
    mx, my = 0.15 * (x1 - x0), 0.15 * (y1 - y0)
    return x0 - mx, y0 - my, x1 + mx, y1 + my


CLIP = _clip_box()


def _planes():
    return [(0, CLIP[0], 1), (0, CLIP[2], -1), (1, CLIP[1], 1), (1, CLIP[3], -1)]


def _clip_near_poly(v: np.ndarray) -> np.ndarray | None:
    pts, out = list(v), []
    for a, b in zip(pts, pts[1:] + pts[:1], strict=True):
        a_in, b_in = a[2] <= -NEAR, b[2] <= -NEAR
        if a_in:
            out.append(a)
        if a_in != b_in:
            t = (-NEAR - a[2]) / (b[2] - a[2])
            out.append(a + t * (b - a))
    return np.array(out) if len(out) >= 3 else None


def _clip_polygon(px: np.ndarray) -> np.ndarray | None:
    pts = list(px)
    for axis, bound, sign in _planes():
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
    for axis, bound, sign in _planes():
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
    v = _clip_near_poly(to_view(pts))
    if v is None:
        return ""
    kept = _clip_polygon(view_to_px(v))
    if kept is None:
        return ""
    edge = f' stroke="{stroke}" stroke-width="{width}" stroke-linejoin="round"' if stroke else ""
    alpha = f' fill-opacity="{opacity}"' if opacity < 1.0 else ""
    return f'<polygon points="{_points(_bowed(kept, True))}" fill="{fill}"{alpha}{edge}/>'


def line(a, b, color: str, width: float, opacity: float = 1.0) -> str:
    va, vb = to_view([a, b])
    if va[2] > -NEAR and vb[2] > -NEAR:
        return ""
    if va[2] > -NEAR:
        va = vb + (-NEAR - vb[2]) / (va[2] - vb[2]) * (va - vb)
    elif vb[2] > -NEAR:
        vb = va + (-NEAR - va[2]) / (vb[2] - va[2]) * (vb - va)
    px = view_to_px(np.array([va, vb]))
    kept = _clip_segment(px[0], px[1])
    if kept is None:
        return ""
    alpha = f' stroke-opacity="{opacity}"' if opacity < 1.0 else ""
    return (
        f'<polyline points="{_points(_bowed(kept, False))}" fill="none" stroke="{color}" stroke-width="{width}"'
        f'{alpha} stroke-linecap="round" stroke-linejoin="round"/>'
    )


def box(out: list[str], x0, x1, y0, y1, z0, z1, top: str, face: str, side: str | None = None) -> None:
    """An axis-aligned box, drawn as the faces that point at the camera."""
    side = side or face
    cx, cy, cz = CAM_POS
    if cy < y0:
        out.append(poly([(x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1)], face))
    elif cy > y1:
        out.append(poly([(x0, y1, z0), (x1, y1, z0), (x1, y1, z1), (x0, y1, z1)], face))
    if x1 < cx:
        out.append(poly([(x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)], side))
    if x0 > cx:
        out.append(poly([(x0, y0, z0), (x0, y1, z0), (x0, y1, z1), (x0, y0, z1)], side))
    if cz > z1:
        out.append(poly([(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)], top))


def disc(cx, cy, z, r, n=28):
    return [(cx + r * math.cos(t), cy + r * math.sin(t), z) for t in np.linspace(0, 2 * math.pi, n, endpoint=False)]


def vdisc(cx, y, cz, r, n=28):
    """A disc in a vertical plane facing the audience."""
    return [(cx + r * math.cos(t), y, cz + r * math.sin(t)) for t in np.linspace(0, 2 * math.pi, n, endpoint=False)]


# --- scene ----------------------------------------------------------------------------------


def floor(out: list[str]) -> None:
    """Dark ground around the stage; ground protection panels, 1 x 1 m, in front of it."""
    x0, x1, y0, y1 = -34.0, 34.0, -7.9, 24.0
    out.append(poly([(x0, y0, FLOOR_Z), (x1, y0, FLOOR_Z), (x1, y1, FLOOR_Z), (x0, y1, FLOOR_Z)], "#17181b"))
    yt = 0.0
    out.append(poly([(x0, y0, FLOOR_Z), (x1, y0, FLOOR_Z), (x1, yt, FLOOR_Z), (x0, yt, FLOOR_Z)], "#2b2c30"))
    z = FLOOR_Z + 0.005
    for x in np.arange(x0, x1 + 1e-6, 1.0):
        out.append(line((x, y0, z), (x, yt, z), "#121316", 1.3, 0.8))
    for y in np.arange(math.ceil(y0), yt + 1e-6, 1.0):
        out.append(line((x0, y, z), (x1, y, z), "#121316", 1.3, 0.8))


def back_structure(out: list[str]) -> None:
    """Backstage: a black drape behind the wall and the roof's back towers."""
    out.append(poly([(-16, 12.9, FLOOR_Z), (16, 12.9, FLOOR_Z), (16, 12.9, 12), (-16, 12.9, 12)], "#0b0b0e"))
    for sgn in (-1, 1):
        tower(out, sgn * TOWER_X, TOWER_Y_BACK, FLOOR_Z, 12.0)


def front_towers(out: list[str]) -> None:
    """The roof's front towers, on the floor just off the stage's downstage corners."""
    for sgn in (-1, 1):
        tower(out, sgn * TOWER_X, TOWER_Y_FRONT, FLOOR_Z, 12.0)


def tower(out: list[str], x: float, y: float, z0: float, z1: float, s: float = 0.4) -> None:
    """A ground-support box-truss tower: four chords, lacing on the faces toward the camera."""
    h = s / 2
    p = TOWER_PLATE / 2
    box(out, x - p, x + p, y - p, y + p, z0, z0 + 0.06, "#3c3e44", "#2a2b30")
    xs = x - h if x > CAM_POS[0] else x + h  # the side face toward the camera

    def lacing(p0, p1, colour: str, width: float) -> None:
        z, k = z0, 0
        while z < z1 - 1e-6:
            zn = min(z1, z + s)
            a, b = (p0, p1) if k % 2 == 0 else (p1, p0)
            out.append(line((*a, z), (*b, zn), colour, width))
            z, k = zn, k + 1

    out.append(line((2 * x - xs, y + h, z0), (2 * x - xs, y + h, z1), "#6f727a", 2.0))  # far back chord
    out.append(line((xs, y + h, z0), (xs, y + h, z1), "#80838a", 2.2))
    lacing((xs, y - h), (xs, y + h), "#7d8087", 1.2)
    lacing((x - h, y - h), (x + h, y - h), "#9a9da4", 1.3)
    for dx in (-h, h):
        out.append(line((x + dx, y - h, z0), (x + dx, y - h, z1), "#b9bcc3", 3.0))


def led_wall(out: list[str], rng: random.Random) -> None:
    x0, x1, y, z0, z1 = LED
    # Support frame and a black skirt under the wall
    box(out, x0 - 0.15, x1 + 0.15, y, y + 0.6, 0.0, z0, "#111114", "#0d0d10")
    nx, nz = round((x1 - x0) / LED_PANEL), round((z1 - z0) / LED_PANEL)
    for i in range(nx):
        for j in range(nz):
            xa, za = x0 + i * LED_PANEL, z0 + j * LED_PANEL
            u, w = (i + 0.5) / nx, (j + 0.5) / nz
            # dim content: a slate gradient with a soft cool band, and the panels' own spread
            band = math.exp(-((w - 0.55 - 0.15 * math.sin(4.0 * u)) ** 2) / 0.02)
            base = 30 + 26 * w + 34 * band + rng.uniform(-3, 3)
            r, g, b = base * 0.78, base * 0.9, base * 1.15
            out.append(
                poly(
                    [
                        (xa, y, za),
                        (xa + LED_PANEL, y, za),
                        (xa + LED_PANEL, y, za + LED_PANEL),
                        (xa, y, za + LED_PANEL),
                    ],
                    f"#{int(r):02x}{int(g):02x}{min(255, int(b)):02x}",
                )
            )
    ys = y - 0.002
    for i in range(nx + 1):
        out.append(line((x0 + i * LED_PANEL, ys, z0), (x0 + i * LED_PANEL, ys, z1), "#050507", 1.3, 0.9))
    for j in range(nz + 1):
        out.append(line((x0, ys, z0 + j * LED_PANEL), (x1, ys, z0 + j * LED_PANEL), "#050507", 1.3, 0.9))
    # The frame: a black border with a bright aluminium edge
    e = 0.12
    for strip in (
        [(x0 - e, ys, z1), (x1 + e, ys, z1), (x1 + e, ys, z1 + e), (x0 - e, ys, z1 + e)],
        [(x0 - e, ys, z0 - e), (x1 + e, ys, z0 - e), (x1 + e, ys, z0), (x0 - e, ys, z0)],
        [(x0 - e, ys, z0 - e), (x0, ys, z0 - e), (x0, ys, z1 + e), (x0 - e, ys, z1 + e)],
        [(x1, ys, z0 - e), (x1 + e, ys, z0 - e), (x1 + e, ys, z1 + e), (x1, ys, z1 + e)],
    ):
        out.append(poly(strip, "#16171b"))
    for a, b in (
        ((x0 - e, z1 + e), (x1 + e, z1 + e)),
        ((x0 - e, z0 - e), (x1 + e, z0 - e)),
        ((x0 - e, z0 - e), (x0 - e, z1 + e)),
        ((x1 + e, z0 - e), (x1 + e, z1 + e)),
    ):
        out.append(line((a[0], ys - 0.01, a[1]), (b[0], ys - 0.01, b[1]), "#8e9299", 2.0))


def deck(out: list[str]) -> None:
    """Main stage and wings at deck height, the decks' seams, white tape along the lip."""
    out.append(poly([(-WING_X, 0, 0), (WING_X, 0, 0), (WING_X, STAGE_BACK, 0), (-WING_X, STAGE_BACK, 0)], C_WING_DECK))
    out.append(poly([(-STAGE_X, 0, 0), (STAGE_X, 0, 0), (STAGE_X, STAGE_BACK, 0), (-STAGE_X, STAGE_BACK, 0)], C_DECK))
    z = 0.004
    for x in np.arange(-WING_X, WING_X + 1e-6, DECK_W):
        main = abs(x) <= STAGE_X + 1e-6
        out.append(line((x, 0, z), (x, STAGE_BACK, z), C_SEAM, 2.0 if main else 1.6, 0.9 if main else 0.7))
    for y in np.arange(0.0, STAGE_BACK + 1e-6, DECK_D):
        out.append(line((-STAGE_X, y, z), (STAGE_X, y, z), C_SEAM, 2.0, 0.9))
        for sgn in (-1, 1):
            out.append(line((sgn * STAGE_X, y, z), (sgn * WING_X, y, z), C_SEAM, 1.6, 0.7))
    e = 0.05
    out.append(poly([(-WING_X, 0, 0.006), (WING_X, 0, 0.006), (WING_X, e, 0.006), (-WING_X, e, 0.006)], TAPE))


def skirt(out: list[str]) -> None:
    """Black skirt on the stage front, with its pleats."""
    y = -0.01
    out.append(poly([(-WING_X, y, FLOOR_Z), (WING_X, y, FLOOR_Z), (WING_X, y, 0), (-WING_X, y, 0)], "#0c0c0e"))
    for x in np.arange(-WING_X + 0.15, WING_X, 0.3):
        out.append(line((x, y - 0.005, FLOOR_Z), (x, y - 0.005, -0.04), "#1c1c21", 2.0, 0.9))
    out.append(line((-WING_X, y - 0.01, -0.03), (WING_X, y - 0.01, -0.03), "#202024", 3.0))


def tape(out: list[str]) -> None:
    arm = 0.35
    for name, (x, y, z) in CORNERS.items():
        sx = -1.0 if x > 0 else 1.0
        sy = 1.0 if name.startswith("DS") else -1.0
        out.append(line((x, y, z + 0.008), (x + sx * arm, y, z + 0.008), TAPE, 3.2))
        out.append(line((x, y, z + 0.008), (x, y + sy * arm, z + 0.008), TAPE, 3.2))
    out.append(line((-0.25, 0.0, 0.008), (0.25, 0.0, 0.008), TAPE, 3.2))
    out.append(line((0.0, 0.0, 0.008), (0.0, 0.25, 0.008), TAPE, 3.2))


def riser_box(out: list[str], r, top: str, seams_x: float, seams_y: float) -> None:
    x0, x1, y0, y1, h = r
    box(out, x0, x1, y0, y1, 0.0, h, top, "#0a0a0c", "#08080a")
    for x in np.arange(x0 + seams_x, x1 - 1e-6, seams_x):
        out.append(line((x, y0, h + 0.003), (x, y1, h + 0.003), C_SEAM, 1.5, 0.9))
    for y in np.arange(y0 + seams_y, y1 - 1e-6, seams_y):
        out.append(line((x0, y, h + 0.003), (x1, y, h + 0.003), C_SEAM, 1.5, 0.9))
    for x in np.arange(x0, x1 + 1e-6, 0.25):  # pleats of the riser skirt
        out.append(line((x, y0 - 0.003, 0.02), (x, y0 - 0.003, h - 0.03), "#18181c", 1.4))


def amp(out: list[str], x0, y0, w, d, hgt, z0=0.0, front="#202126") -> None:
    box(out, x0, x0 + w, y0, y0 + d, z0, z0 + hgt, "#2e3036", front)
    out.append(
        poly(
            [
                (x0 + 0.05, y0 - 0.002, z0 + 0.06),
                (x0 + w - 0.05, y0 - 0.002, z0 + 0.06),
                (x0 + w - 0.05, y0 - 0.002, z0 + hgt - 0.08),
                (x0 + 0.05, y0 - 0.002, z0 + hgt - 0.08),
            ],
            "#3a3b40",
        )
    )


def backline(out: list[str]) -> None:
    amp(out, -4.6, 7.6, 0.75, 0.36, 0.75, 0.0)  # guitar 4x12 with head
    amp(out, -4.6, 7.62, 0.75, 0.3, 0.27, 0.75, "#16171a")
    amp(out, 2.2, 8.2, 0.6, 0.45, 1.25, 0.0)  # bass 8x10 with head
    amp(out, 2.2, 8.22, 0.6, 0.35, 0.22, 1.25, "#16171a")
    for x0 in (-7.0, 6.2):  # road case side tables
        box(out, x0, x0 + 0.8, 9.2, 9.8, 0.0, 0.9, "#2a2b30", "#17181b")


def drum(out: list[str], x: float, y: float, z0: float, z1: float, r: float, shell: str, head: str) -> None:
    """A shell from z0 up to its head at z1, built from stacked rings so its side reads from above."""
    for z in np.linspace(z0, z1, max(2, int((z1 - z0) / 0.02) + 1)):
        out.append(poly(disc(x, y, z, r, 20), shell))
    out.append(poly(disc(x, y, z1 + 0.001, r, 20), head, "#7a756c", 1.2))


def drum_kit(out: list[str]) -> None:
    x0, x1, y0, y1, h = DRUM_RISER
    riser_box(out, DRUM_RISER, C_RISER_TOP, 1.0, 2.0)
    out.append(
        poly(
            [
                (x0 + 0.3, y0 + 0.2, h + 0.005),
                (x1 - 0.3, y0 + 0.2, h + 0.005),
                (x1 - 0.3, y1 - 0.2, h + 0.005),
                (x0 + 0.3, y1 - 0.2, h + 0.005),
            ],
            "#3a2c2a",
        )
    )  # drum rug
    cx, cy = 0.0, 8.0
    stand, shell, head = "#9a9ea6", "#5a2a22", "#e9e4dc"
    out.append(line((cx, cy + 0.65, h), (cx, cy + 0.65, h + 0.5), stand, 2.4))  # throne on its post
    drum(out, cx, cy + 0.65, h + 0.5, h + 0.56, 0.2, "#0e0e10", "#1a1a1d")
    for dx, dy, top in ((-0.75, -0.35, 1.25), (0.85, -0.3, 1.35), (0.6, 0.35, 1.0), (-0.5, 0.45, 1.4)):
        out.append(line((cx + dx, cy + dy, h), (cx + dx, cy + dy, h + top), stand, 2.0))  # cymbal stands
    ft = (cx + 0.55, cy + 0.35)  # floor tom on three legs
    for t in (0.5, 2.6, 4.7):
        out.append(
            line(
                (ft[0] + 0.3 * math.cos(t), ft[1] + 0.3 * math.sin(t), h),
                (ft[0] + 0.22 * math.cos(t), ft[1] + 0.22 * math.sin(t), h + 0.2),
                stand,
                1.6,
            )
        )
    drum(out, *ft, h + 0.2, h + 0.62, 0.22, shell, head)
    sn = (cx - 0.42, cy + 0.2)  # snare on its stand
    out.append(line((*sn, h), (*sn, h + 0.48), stand, 2.0))
    drum(out, *sn, h + 0.48, h + 0.62, 0.17, shell, head)
    box(out, cx - 0.3, cx + 0.3, cy - 0.25, cy + 0.25, h, h + 0.6, "#3a1c18", "#2a1512")  # bass drum
    out.append(poly(vdisc(cx, cy - 0.252, h + 0.3, 0.29, 28), "#151517", "#8a8d94", 2.0))
    out.append(poly(vdisc(cx, cy - 0.254, h + 0.3, 0.1, 16), "#2c2d32"))
    for dx in (-0.16, 0.16):  # rack toms mounted on the bass drum
        drum(out, cx + dx, cy - 0.02, h + 0.6, h + 0.8, 0.13, shell, head)
    out.append(poly(disc(cx + 0.6, cy + 0.35, h + 1.0, 0.18, 20), "#c69a4c", "#6a4a1a", 1.0))  # hi-hat
    for dx, dy, r, z in ((-0.5, 0.45, 0.24, 1.4), (-0.75, -0.35, 0.23, 1.25), (0.85, -0.3, 0.26, 1.35)):
        out.append(poly(disc(cx + dx, cy + dy, h + z, r, 24), "#b98a3e", "#6a4a1a", 1.0))
        out.append(poly(disc(cx + dx, cy + dy, h + z + 0.001, r * 0.3, 12), "#d6aa5a"))


def keys(out: list[str]) -> None:
    x0, x1, y0, y1, h = KEYS_RISER
    riser_box(out, KEYS_RISER, C_RISER_TOP, 2.0, 1.0)
    kx, ky = (x0 + x1) / 2, y0 + 0.9
    out.append(line((kx, ky + 0.7, h), (kx, ky + 0.7, h + 0.5), "#9a9ea6", 2.4))  # bench on its post, behind the stand
    drum(out, kx, ky + 0.7, h + 0.5, h + 0.56, 0.18, "#0e0e10", "#121214")
    top = h + 0.85  # the X stand's top, where the lower keyboard rests
    for dx in (-0.45, 0.45):  # X stand, with the second tier's arms rising back from its top
        out.append(line((kx + dx - 0.25, ky, h), (kx + dx + 0.25, ky, top), "#2c2e33", 3.0))
        out.append(line((kx + dx + 0.25, ky, h), (kx + dx - 0.25, ky, top), "#2c2e33", 3.0))
        out.append(line((kx + dx, ky, top), (kx + dx, ky + 0.2, top + 0.35), "#2c2e33", 2.4))
    for z0, y_front, depth, kw in ((top, ky - 0.17, 0.34, 1.36), (top + 0.35, ky + 0.05, 0.3, 1.0)):  # two keyboards
        z = z0 + 0.1
        box(out, kx - kw / 2, kx + kw / 2, y_front, y_front + depth, z0, z, "#18191c", "#101113")
        k0, k1 = kx - kw / 2 + 0.08, kx + kw / 2 - 0.08
        keys_y0 = y_front + depth - 0.16
        out.append(
            poly(
                [
                    (k0, keys_y0, z + 0.002),
                    (k1, keys_y0, z + 0.002),
                    (k1, keys_y0 + 0.14, z + 0.002),
                    (k0, keys_y0 + 0.14, z + 0.002),
                ],
                "#ecebe6",
            )
        )
        for xk in np.arange(k0 + 0.023, k1, 0.0235):
            if int(round((xk - k0) / 0.0235)) % 7 in (0, 3):
                continue
            out.append(line((xk, keys_y0, z + 0.004), (xk, keys_y0 + 0.08, z + 0.004), "#141416", 1.6))


def mic_stand(out: list[str], x: float, y: float) -> None:
    """A straight stand on a round base, the mic in its clip at the top, tipped toward the singer."""
    out.append(poly(disc(x, y, 0.004, 0.16, 28), "#2a2c31", "#4a4d54", 1.4))
    out.append(poly(disc(x, y, 0.03, 0.04, 12), "#3a3d43"))
    top = 1.5
    out.append(line((x, y, 0.03), (x, y, top), "#aeb2b9", 2.6))
    out.append(line((x, y, top), (x, y + 0.14, top + 0.06), "#1c1d20", 5.0))
    out.append(poly(disc(x, y + 0.17, top + 0.075, 0.03, 12), "#8e9299"))


def wedge(out: list[str], x: float, y: float, w: float = 0.6, d: float = 0.5) -> None:
    """A floor monitor: high at the back (downstage), its face sloped up toward the performer."""
    x0, x1, hb, hf = x - w / 2, x + w / 2, 0.42, 0.1
    out.append(poly([(x0, y, 0), (x1, y, 0), (x1, y, hb), (x0, y, hb)], "#16171a"))  # back panel
    out.append(poly([(x0, y, hb), (x1, y, hb), (x1, y + 0.12, hb), (x0, y + 0.12, hb)], "#25262b"))  # top
    out.append(poly([(x0, y + 0.12, hb), (x1, y + 0.12, hb), (x1, y + d, hf), (x0, y + d, hf)], "#1d1e22"))
    for xs in (x0, x1):
        out.append(poly([(xs, y, 0), (xs, y + d, 0), (xs, y + d, hf), (xs, y + 0.12, hb), (xs, y, hb)], "#101113"))
    out.append(line((x0 + 0.08, y - 0.002, hb - 0.08), (x1 - 0.08, y - 0.002, hb - 0.08), "#3a3c42", 2.0))


def downstage(out: list[str]) -> None:
    for x, y in ((-4.0, 2.0), (0.0, 1.7), (4.0, 2.0)):
        mic_stand(out, x, y)
    for x in (-4.4, -3.6, -0.6, 0.6, 3.6, 4.4):
        wedge(out, x, 0.55 if abs(x) < 1 else 0.85)
    for x0, leds in ((-3.6, ("#5dff9a", "#4aa3ff")), (2.9, ("#4aa3ff", "#ffd35d", "#5dff9a"))):  # pedalboards
        box(out, x0, x0 + 0.8, 1.55, 1.9, 0.0, 0.07, "#2f3238", "#1d1f23")
        for k, c in enumerate(leds):
            out.append(poly(disc(x0 + 0.15 + 0.22 * k, 1.65, 0.072, 0.025, 8), c))
    for x, y, turn in ((-2.2, 0.35, 0.2), (2.4, 0.4, -0.15), (-4.0, 1.3, 0.1)):  # setlists taped down
        c, s_ = math.cos(turn), math.sin(turn)
        sheet = [
            (x + c * dx - s_ * dy, y + s_ * dx + c * dy, 0.006) for dx, dy in ((0, 0), (0.21, 0), (0.21, 0.3), (0, 0.3))
        ]
        out.append(poly(sheet, "#f4f4f2"))
    for ya, yb, xa, xb in ((4.4, 4.48, -8.0, -1.2), (4.7, 4.78, 1.0, 8.0)):  # cable runs to the wings
        out.append(poly([(xa, ya, 0.005), (xb, ya, 0.005), (xb, yb, 0.005), (xa, yb, 0.005)], "#101012"))


def wings(out: list[str]) -> None:
    """Cases, amp racks, a monitor desk and side fills in the wings, drawn from the back forward."""
    for sgn in (-1, 1):

        def xs(a: float, b: float, sgn: int = sgn) -> tuple[float, float]:
            return tuple(sorted((sgn * a, sgn * b)))  # type: ignore[return-value]

        x0, x1 = xs(9.0, 10.3)  # a single case, then a stack of two: the upper one squarely on the lower
        box(out, x0, x1, 8.8, 9.6, 0.0, 0.9, "#2a2b30", "#16171a", "#1b1c20")
        out.append(line((x0, 8.8, 0.902), (x1, 8.8, 0.902), "#8d9199", 1.6, 0.6))
        box(out, x0, x1, 7.4, 8.2, 0.0, 0.8, "#2a2b30", "#16171a", "#1b1c20")
        out.append(line((x0, 7.4, 0.802), (x1, 7.4, 0.802), "#8d9199", 1.6, 0.6))
        u0, u1 = xs(9.1, 10.2)
        box(out, u0, u1, 7.5, 8.1, 0.8, 1.4, "#2e2f35", "#1a1b1f", "#1f2024")
        out.append(line((u0, 7.5, 1.402), (u1, 7.5, 1.402), "#8d9199", 1.6, 0.6))
        x0, x1 = xs(10.4, 11.2)
        for y0 in (5.0, 4.0):  # amp racks with status lights
            box(out, x0, x1, y0, y0 + 0.8, 0.0, 1.6, "#202126", "#121316", "#17181b")
            for k, c in enumerate(("#5dff9a", "#4aa3ff", "#5dff9a")):
                out.append(poly(vdisc(x0 + 0.15 + 0.2 * k, y0 - 0.003, 1.45, 0.025, 8), c))
        if sgn < 0:  # monitor desk with a lit screen standing on it
            d0, d1 = -10.8, -9.0
            box(out, d0, d1, 2.0, 2.8, 0.0, 0.95, "#2a2b30", "#16171a", "#1b1c20")
            out.append(
                poly(
                    [(d0 + 0.2, 2.75, 0.95), (d1 - 0.2, 2.75, 0.95), (d1 - 0.2, 2.8, 1.4), (d0 + 0.2, 2.8, 1.4)],
                    "#3d6fb0",
                )
            )
            for k in range(10):
                out.append(
                    line((d0 + 0.2 + 0.14 * k, 2.15, 0.955), (d0 + 0.2 + 0.14 * k, 2.4, 0.955), "#cfd3da", 1.3, 0.7)
                )
        x0, x1 = xs(8.6, 9.6)
        box(out, x0, x1, 0.6, 1.4, 0.0, 1.8, "#1a1a1e", "#101013", "#141418")  # side fill
        for z in (0.5, 1.0, 1.5):
            out.append(line((x0 + 0.05, 0.598, z), (x1 - 0.05, 0.598, z), "#2c2d33", 2.0))


def sub_positions() -> list[tuple[float, bool]]:
    """Each sub's left edge and whether a front fill stands on it: five a side between the front
    towers, two a side further offstage, all clear of the towers' base plates by TOWER_CLEAR."""
    out = []
    for sgn in (-1, 1):
        for k in range(5):  # from the centre gap out toward the tower
            x = 0.7 + k * (SUB_W + SUB_GAP)
            out.append((x if sgn > 0 else -x - SUB_W, k in (1, 3)))
        for k in range(2):  # outside the tower
            x = TOWER_X + TOWER_PLATE / 2 + TOWER_CLEAR + 0.5 + k * (SUB_W + SUB_GAP)
            out.append((x if sgn > 0 else -x - SUB_W, False))
    return out


def subs(out: list[str]) -> None:
    """Subs on the floor along the stage front, front fills on four of them."""
    w, hgt = SUB_W, 0.6
    for x0, fill in sorted(sub_positions(), key=lambda p: -abs(p[0] + w / 2)):
        z0 = FLOOR_Z
        box(out, x0, x0 + w, SUB_Y0, SUB_Y1, z0, z0 + hgt, "#232428", "#141518", "#18191c")
        out.append(
            poly(
                [
                    (x0 + 0.06, SUB_Y0 - 0.002, z0 + 0.06),
                    (x0 + w - 0.06, SUB_Y0 - 0.002, z0 + 0.06),
                    (x0 + w - 0.06, SUB_Y0 - 0.002, z0 + hgt - 0.06),
                    (x0 + 0.06, SUB_Y0 - 0.002, z0 + hgt - 0.06),
                ],
                "#1d1e22",
            )
        )
        if fill:
            zt = z0 + hgt
            box(out, x0 + 0.3, x0 + 0.9, SUB_Y0 + 0.1, SUB_Y0 + 0.5, zt, zt + 0.32, "#2c2d33", "#18191c", "#1e1f23")
            out.append(
                poly(
                    [
                        (x0 + 0.34, SUB_Y0 + 0.098, zt + 0.04),
                        (x0 + 0.86, SUB_Y0 + 0.098, zt + 0.04),
                        (x0 + 0.86, SUB_Y0 + 0.098, zt + 0.28),
                        (x0 + 0.34, SUB_Y0 + 0.098, zt + 0.28),
                    ],
                    "#3a3b41",
                )
            )


def barrier(out: list[str]) -> None:
    """A straight run of mojo-style barrier: foot plates toward the audience, a brace and step toward the pit."""
    x0, x1, sec = -22.0, 22.0, 1.0
    y, hgt, foot = BARR_Y, 1.2, 1.0
    zf = FLOOR_Z
    al, al_dk = "#c3c6cc", "#7d8188"
    for xa in np.arange(x0, x1, sec):  # pit-side braces and steps, drawn first: behind the front face
        out.append(line((xa + 0.05, y, zf + hgt - 0.05), (xa + 0.05, y + 0.75, zf), al_dk, 2.2))
        out.append(
            poly(
                [
                    (xa + 0.05, y + 0.1, zf + 0.42),
                    (xa + sec - 0.05, y + 0.1, zf + 0.42),
                    (xa + sec - 0.05, y + 0.45, zf + 0.42),
                    (xa + 0.05, y + 0.45, zf + 0.42),
                ],
                "#5c6067",
            )
        )
    for xa in np.arange(x0, x1, sec):  # foot plates
        out.append(
            poly(
                [
                    (xa + 0.03, y - foot, zf + 0.01),
                    (xa + sec - 0.03, y - foot, zf + 0.01),
                    (xa + sec - 0.03, y, zf + 0.01),
                    (xa + 0.03, y, zf + 0.01),
                ],
                "#45484f",
                "#2e3036",
                1.0,
            )
        )
        out.append(line((xa + 0.03, y - foot, zf + 0.012), (xa + sec - 0.03, y - foot, zf + 0.012), "#7d8188", 1.4))
        out.append(
            poly(
                [(xa, y, zf + 0.05), (xa + sec, y, zf + 0.05), (xa + sec, y, zf + hgt), (xa, y, zf + hgt)],
                "#a9adb4",
                opacity=0.2,
            )
        )
    out.append(line((x0, y, zf + 0.05), (x1, y, zf + 0.05), al_dk, 2.4))
    out.append(line((x0, y, zf + 0.6), (x1, y, zf + 0.6), al_dk, 1.6))
    for xa in np.arange(x0, x1 + 1e-6, sec):
        out.append(line((xa, y, zf), (xa, y, zf + hgt), al, 2.6))
    out.append(line((x0, y, zf + hgt), (x1, y, zf + hgt), "#e4e6ea", 5.0))  # rounded top rail


# --- assembly -------------------------------------------------------------------------------


def build_svg() -> str:
    rng = random.Random(11)
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W:.0f} {H:.0f}" width="{W:.0f}" height="{H:.0f}">',
        f'<rect x="0" y="0" width="{W:.0f}" height="{H:.0f}" fill="{C_BG}"/>',
    ]
    floor(out)
    back_structure(out)
    led_wall(out, rng)
    deck(out)
    tape(out)
    backline(out)
    drum_kit(out)
    keys(out)
    wings(out)
    downstage(out)
    skirt(out)
    subs(out)
    barrier(out)
    front_towers(out)
    out.append("</svg>\n")
    return "\n".join(p for p in out if p)


def picture() -> Image.Image:
    """The scene rendered and softened under a cool work light, darker toward the corners about the lens centre."""
    png = subprocess.run(["rsvg-convert", "-f", "png"], input=build_svg().encode(), capture_output=True, check=True)
    img = Image.open(io.BytesIO(png.stdout)).convert("RGB")
    img = Image.blend(img, img.filter(ImageFilter.GaussianBlur(14)), 0.10)
    arr = np.asarray(img).astype(np.float32) / 255.0
    arr = arr**1.12
    arr *= np.array([0.97, 1.0, 1.05], dtype=np.float32)
    yy, xx = np.mgrid[0 : int(H), 0 : int(W)].astype(np.float32)
    r = np.hypot((xx - W / 2) / HALF_DIAG - CX, (yy - H / 2) / HALF_DIAG - CY)
    arr *= (1.0 - 0.22 * r**2.2)[..., None]
    return Image.fromarray(np.clip(arr * 255.0 + 0.5, 0, 255).astype(np.uint8))


def main() -> None:
    assert pull_back_to_valid(K1, K2, CX, CY) == (K1, K2, CX, CY), "lens cannot reach the corners"
    corners = np.array(list(CORNERS.values()))
    assert np.allclose(view_to_px(to_view(corners)), pinhole(corners)), "near-plane split differs from the overlay"
    picture().save(ASSET, quality=90, optimize=True)
    pos = ", ".join(f"{v:g}" for v in CAMERA[:3])
    print(f"Camera: pos=({pos}) pitch={PITCH:g} yaw={YAW:g} roll={ROLL:g} hfov={FOV:g}")
    print(f"Lens:   barrel / fisheye {K1:g}, corner correction {K2:g}")
    print(f"Centre: {CENTRE_PX[0]:g} px right, {-CENTRE_PX[1]:g} px up")
    print(f"Grid:   {GRID_W:g} x {GRID_D:g} m, x_offset 0, y_offset {GRID_D / 2:g}, z_offset 0")
    for name, corner in {**CORNERS, "REF": (0.0, 0.0, 0.0)}.items():
        x, y = lens(pinhole([corner]))[0]
        print(f"  {name}: ({x:.1f}, {y:.1f})")
    print(f"Wrote: {ASSET.relative_to(ROOT)} ({ASSET.stat().st_size // 1024} kB)")
    grid = {"width": GRID_W, "depth": GRID_D, "spacing": GRID_SPACING}
    grid |= {"x_offset": 0.0, "y_offset": GRID_D / 2, "z_offset": 0.0}
    cal = write_calibration(ASSET, "scripts/gen_festival_default.py", camera_from(CAMERA, K1, K2, CX, CY), grid)
    print(f"Wrote: {cal.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
