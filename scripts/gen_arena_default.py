#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Render the Media Gallery's Arena default: an arena seen from a truss above the stage.

The camera hangs above the stage and looks downstage over a strip of it into an arena: floor
seating either side of a centre aisle with a cross aisle, a raked end tier with a centre stair,
side tiers broken by vomitories, corner doorways. The picture is bowed by a barrel lens through the
overlay's own model, so the Setup Wizard's Lens step measures the pair back from edges that are
straight in the hall. The stage carries spike tape at the grid's corners and the Reference Point
cross; four mic stands in the aisles carry a tape mark at stage level, for calibration points.

Needs ``rsvg-convert`` (librsvg) and Pillow. Prints the camera, the lens pair, the grid, the corners' pixels
and each mark's distances from the two downstage corners.

    poetry run python scripts/gen_arena_default.py
"""

from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from openfollow.scene.solver import apply_overlay_distortion, project_points  # noqa: E402

ASSET = ROOT / "openfollow" / "video" / "inputs" / "assets" / "arena_default.jpg"
W, H = 1920.0, 1080.0
# Truss above the stage: pos_x, pos_y, pos_z, pitch, yaw, roll, horizontal fov.
CAMERA = np.array([0.0, 9.8, 7.6, -21.5, 180.0, 0.0, 65.8])
K1, K2 = -0.15, 0.02  # barrel / fisheye, edge fit
# Pinhole geometry is cut to this box before the lens bows it; far outside it the warp folds back.
CLIP = (-0.25 * W, -0.25 * H, 1.25 * W, 1.25 * H)
STEP_PX = 6.0  # edges are split this finely, so a straight edge bows like the lens bows it

LIP_Y = -0.3  # stage front, 0.3 m downstage of the grid
GRID_W, GRID_D = 10.0, 1.0
CORNERS = {
    "DSL": (GRID_W / 2, 0.0, 0.0),
    "DSR": (-GRID_W / 2, 0.0, 0.0),
    "USR": (-GRID_W / 2, GRID_D, 0.0),
    "USL": (GRID_W / 2, GRID_D, 0.0),
}
FLOOR_Z = -1.3  # stage height above the arena floor
STAGE_X_HALF = 9.0
DECK = 1.0

# From a venue plan, in metres downstage of the stage front (+x is stage left).
END_Y = LIP_Y - 39.1  # end tier front
END_FRONT_HALF, END_BACK_HALF, END_DEPTH, END_ROWS = 10.45, 14.25, 8.3, 10
SIDE_X, SIDE_DEPTH, SIDE_ROWS = 12.4, 8.3, 10
SIDE_BLOCKS = [(END_Y + 2.4, END_Y + 11.1), (END_Y + 14.2, END_Y + 30.9), (END_Y + 33.9, LIP_Y - 1.0)]
CORNER_BACK_Y = END_Y - 1.8  # the corner block's angled end, at the back of the tier
WALL_Y, WALL_X = END_Y - 8.5, 20.7
ROW_RISE = 0.45

AISLE_HALF, BLOCK_X_OUT = 1.15, 11.6
FRONT_ROWS = (LIP_Y - 2.5, LIP_Y - 23.9)
BACK_ROWS = (LIP_Y - 24.9, LIP_Y - 35.0)
ROW_PITCH, SEAT = 0.9, 0.53

TARGETS = [  # mic stands with a tape mark at stage level: x, y, where
    (0.0, LIP_Y - 10.0, "centre aisle, front section"),
    (7.0, LIP_Y - 24.4, "cross aisle, stage left"),
    (-7.0, LIP_Y - 24.4, "cross aisle, stage right"),
    (0.0, LIP_Y - 31.0, "centre aisle, back section"),
]

C_FLOOR, C_STAGE, C_LIP, C_SEAM = "#3b3c41", "#1d2735", "#3cc8e6", "#0b0f16"
C_CHAIR, C_CHAIR_DARK, C_FRAME = "#3f4fae", "#2a3678", "#c9a35a"
C_TIER_SEAT, C_TIER_EDGE, C_RISER, C_TREAD = "#d4b47a", "#8a6d3a", "#4b4943", "#5e5b53"
C_STAIR, C_WALL, C_WALL_LOW, C_DOOR = "#4f6cff", "#9c937f", "#6e675a", "#55524b"
TAPE, MARK = "#ffffff", "#ff3fa4"


def pinhole(pts) -> np.ndarray:
    return project_points(CAMERA, np.asarray(pts, dtype=float), W, H)


def lens(px: np.ndarray) -> np.ndarray:
    return apply_overlay_distortion(px, W, H, K1, K2)


_PLANES = [(0, CLIP[0], 1), (0, CLIP[2], -1), (1, CLIP[1], 1), (1, CLIP[3], -1)]


def _clip_polygon(px: np.ndarray) -> np.ndarray | None:
    """Sutherland-Hodgman against CLIP."""
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
    """Liang-Barsky against CLIP."""
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


def poly(pts, fill: str, stroke: str = "", width: float = 0.0) -> str:
    px = pinhole(pts)
    if not np.isfinite(px).all():
        return ""
    kept = _clip_polygon(px)
    if kept is None:
        return ""
    edge = f' stroke="{stroke}" stroke-width="{width}"' if stroke else ""
    return f'<polygon points="{_points(_bowed(kept, True))}" fill="{fill}"{edge}/>'


def line(a, b, color: str, width: float) -> str:
    px = pinhole([a, b])
    if not np.isfinite(px).all():
        return ""
    kept = _clip_segment(px[0], px[1])
    if kept is None:
        return ""
    return (
        f'<polyline points="{_points(_bowed(kept, False))}" fill="none" stroke="{color}" stroke-width="{width}" '
        'stroke-linecap="square"/>'
    )


def walls(out: list[str]) -> None:
    top = FLOOR_Z + 12.0
    back = [(-WALL_X, WALL_Y, FLOOR_Z), (WALL_X, WALL_Y, FLOOR_Z), (WALL_X, WALL_Y, top), (-WALL_X, WALL_Y, top)]
    out.append(poly(back, C_WALL))
    for s in (-1, 1):
        x = s * WALL_X
        out.append(poly([(x, WALL_Y, FLOOR_Z), (x, 2.0, FLOOR_Z), (x, 2.0, top), (x, WALL_Y, top)], C_WALL_LOW))
    for s in (-1, 1):  # corner walls between the end tier and the side tiers, a doorway in each
        a = np.array((s * (END_BACK_HALF + 1.5), WALL_Y, FLOOR_Z))
        b = np.array((s * WALL_X, END_Y - 3.5, FLOOR_Z))
        up = np.array((0.0, 0.0, 4.5))
        out.append(poly([a, b, b + up, a + up], C_WALL))
        mid, along = (a + b) / 2, (b - a) / np.linalg.norm(b - a) * 1.6
        door = np.array((0.0, 0.0, 2.4))
        out.append(poly([mid - along, mid + along, mid + along + door, mid - along + door], C_DOOR))


def seat_row(out: list[str], a, b, z: float, facing, color: str, edge: str) -> None:
    """Seat backs from a to b on a tread at height z; ``facing`` points from the seats into the arena."""
    a, b, back = np.array(a, float), np.array(b, float), -np.array(facing, float) * 0.25
    length = float(np.linalg.norm(b - a))
    u = (b - a) / length
    k = 0.0
    while k + SEAT <= length + 1e-6:
        p0, p1 = a + u * (k + 0.04) + back, a + u * (k + SEAT - 0.04) + back
        out.append(poly([(*p0, z + 0.42), (*p1, z + 0.42), (*p1, z + 0.85), (*p0, z + 0.85)], color, edge, 0.5))
        k += SEAT


def end_tier(out: list[str]) -> None:
    for k in reversed(range(END_ROWS)):
        t0, t1 = k / END_ROWS, (k + 1) / END_ROWS
        y0, y1 = END_Y - END_DEPTH * t0, END_Y - END_DEPTH * t1
        h0 = END_FRONT_HALF + (END_BACK_HALF - END_FRONT_HALF) * t0
        h1 = END_FRONT_HALF + (END_BACK_HALF - END_FRONT_HALF) * t1
        z0, z1 = FLOOR_Z + k * ROW_RISE, FLOOR_Z + (k + 1) * ROW_RISE
        out.append(poly([(-h0, y0, z0), (h0, y0, z0), (h0, y0, z1), (-h0, y0, z1)], C_RISER))
        out.append(poly([(-h0, y0, z1), (h0, y0, z1), (h1, y1, z1), (-h1, y1, z1)], C_TREAD))
        for s in (-1, 1):
            seat_row(out, (s * 0.7, y1 + 0.3), (s * (h1 - 0.9), y1 + 0.3), z1, (0, 1), C_TIER_SEAT, C_TIER_EDGE)
            out.append(line((s * h0, y0, z1), (s * (h0 - 0.6), y0, z1), C_STAIR, 2.5))
        out.append(line((-0.6, y0, z1), (0.6, y0, z1), C_STAIR, 2.5))


def side_tiers(out: list[str]) -> None:
    for s in (-1, 1):
        for n, (y_near, y_far) in enumerate(SIDE_BLOCKS):
            for k in reversed(range(SIDE_ROWS)):
                x0 = s * (SIDE_X + k * SIDE_DEPTH / SIDE_ROWS)
                x1 = s * (SIDE_X + (k + 1) * SIDE_DEPTH / SIDE_ROWS)
                z0, z1 = FLOOR_Z + k * ROW_RISE, FLOOR_Z + (k + 1) * ROW_RISE
                far = y_near + (CORNER_BACK_Y - y_near) * (k + 1) / SIDE_ROWS if n == 0 else y_near
                out.append(poly([(x0, y_far, z0), (x0, far, z0), (x0, far, z1), (x0, y_far, z1)], C_RISER))
                out.append(poly([(x0, y_far, z1), (x0, far, z1), (x1, far, z1), (x1, y_far, z1)], C_TREAD))
                xs = x1 - s * 0.3
                seat_row(out, (xs, y_far - 0.3), (xs, far + 0.3), z1, (-s, 0), C_TIER_SEAT, C_TIER_EDGE)
                out.append(line((x0, y_far, z1), (x1, y_far, z1), C_STAIR, 2.5))
                out.append(line((x0, far, z1), (x1, far, z1), C_STAIR, 2.5))


def floor(out: list[str]) -> None:
    out.append(
        poly(
            [
                (-WALL_X, LIP_Y, FLOOR_Z),
                (WALL_X, LIP_Y, FLOOR_Z),
                (WALL_X, WALL_Y, FLOOR_Z),
                (-WALL_X, WALL_Y, FLOOR_Z),
            ],
            C_FLOOR,
        )
    )


def chair(out: list[str], x: float, y: float) -> None:
    w, z = SEAT - 0.06, FLOOR_Z
    out.append(
        poly(
            [(x, y, z + 0.45), (x + w, y, z + 0.45), (x + w, y - 0.42, z + 0.45), (x, y - 0.42, z + 0.45)], C_CHAIR_DARK
        )
    )
    out.append(
        poly(
            [
                (x, y - 0.42, z + 0.45),
                (x + w, y - 0.42, z + 0.45),
                (x + w, y - 0.47, z + 0.92),
                (x, y - 0.47, z + 0.92),
            ],
            C_CHAIR,
            C_FRAME,
            1.0,
        )
    )


def floor_seats(out: list[str]) -> None:
    rows = []
    for first, last in (FRONT_ROWS, BACK_ROWS):
        y = first
        while y >= last - 1e-6:
            rows.append(y)
            y -= ROW_PITCH
    for y in reversed(rows):
        for x0, x1 in ((-BLOCK_X_OUT, -AISLE_HALF), (AISLE_HALF, BLOCK_X_OUT)):
            x = x0
            while x + SEAT <= x1 + 1e-6:
                chair(out, x, y)
                x += SEAT
    for s in (-1, 1):  # front-of-house desks at the back of the centre aisle
        x0, x1, y0, y1, z = s * 1.4, s * 4.4, LIP_Y - 35.8, LIP_Y - 36.8, FLOOR_Z + 1.0
        out.append(poly([(x0, y0, FLOOR_Z), (x1, y0, FLOOR_Z), (x1, y0, z), (x0, y0, z)], "#1e1f24"))
        out.append(poly([(x0, y0, z), (x1, y0, z), (x1, y1, z), (x0, y1, z)], "#2c2d33"))


def mic_stand(out: list[str], x: float, y: float) -> None:
    base = [
        (x - 0.22, y + 0.22, FLOOR_Z),
        (x + 0.22, y + 0.22, FLOOR_Z),
        (x + 0.22, y - 0.22, FLOOR_Z),
        (x - 0.22, y - 0.22, FLOOR_Z),
    ]
    out.append(poly(base, "#141414"))
    out.append(line((x, y, FLOOR_Z), (x, y, 0.2), "#1a1a1a", 3))
    out.append(line((x - 0.12, y, 0.0), (x + 0.12, y, 0.0), MARK, 4))


def stage(out: list[str]) -> None:
    near = CAMERA[1] - 1.0
    out.append(
        poly(
            [
                (-STAGE_X_HALF, LIP_Y, 0.0),
                (STAGE_X_HALF, LIP_Y, 0.0),
                (STAGE_X_HALF, near, 0.0),
                (-STAGE_X_HALF, near, 0.0),
            ],
            C_STAGE,
        )
    )
    x = -STAGE_X_HALF + DECK
    while x < STAGE_X_HALF - 1e-6:
        out.append(line((x, LIP_Y, 0.0), (x, near, 0.0), C_SEAM, 1.5))
        x += DECK
    y = LIP_Y + 2.0
    while y < near:
        out.append(line((-STAGE_X_HALF, y, 0.0), (STAGE_X_HALF, y, 0.0), C_SEAM, 1.5))
        y += 2.0
    out.append(line((-STAGE_X_HALF, LIP_Y, 0.0), (STAGE_X_HALF, LIP_Y, 0.0), C_LIP, 4))


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
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W:.0f} {H:.0f}" width="{W:.0f}" height="{H:.0f}">',
        f'<rect x="0" y="0" width="{W:.0f}" height="{H:.0f}" fill="#0c0c10"/>',
    ]
    walls(out)
    floor(out)
    end_tier(out)
    side_tiers(out)
    floor_seats(out)
    for x, y, _where in TARGETS:
        mic_stand(out, x, y)
    stage(out)
    tape(out)
    out.append("</svg>\n")
    return "\n".join(part for part in out if part)


def main() -> None:
    png = subprocess.run(["rsvg-convert", "-f", "png"], input=build_svg().encode(), capture_output=True, check=True)
    Image.open(io.BytesIO(png.stdout)).convert("RGB").save(ASSET, quality=90, optimize=True)
    pos = ", ".join(f"{v:g}" for v in CAMERA[:3])
    print(f"Camera: pos=({pos}) pitch={CAMERA[3]:g} yaw={CAMERA[4]:g} roll={CAMERA[5]:g} hfov={CAMERA[6]:g}")
    print(f"Lens:   barrel / fisheye {K1:g}, edge fit {K2:g}")
    print(f"Grid:   {GRID_W:g} x {GRID_D:g} m, x_offset 0, y_offset {GRID_D / 2:g}, z_offset 0")
    print(f"Stage front at y {LIP_Y:g}, {-FLOOR_Z:g} m above the arena floor")
    for name, corner in CORNERS.items():
        x, y = lens(pinhole([corner]))[0]
        print(f"  {name}: ({x:.1f}, {y:.1f})")
    print("Calibration points (height 0), from DSL and DSR:")
    for n, (x, y, where) in enumerate(TARGETS, 1):
        mark = np.array([x, y, 0.0])
        da = float(np.linalg.norm(mark - np.array(CORNERS["DSL"])))
        db = float(np.linalg.norm(mark - np.array(CORNERS["DSR"])))
        px, py = lens(pinhole([mark]))[0]
        print(f"  {n}: {where:28s} DSL {da:7.3f} m  DSR {db:7.3f} m  pixel ({px:.0f}, {py:.0f})")
    print(f"Wrote: {ASSET.relative_to(ROOT)} ({ASSET.stat().st_size // 1024} kB)")


if __name__ == "__main__":
    main()
