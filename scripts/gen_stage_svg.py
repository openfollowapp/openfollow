#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Regenerate stock stage SVG from 3D world geometry using CameraConfig/GridConfig defaults.

Writes stage_default_perspective.svg (with grid overlay) and stage_default.svg (no overlay).
Run after changing defaults in openfollow/configuration.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from openfollow.configuration import CameraConfig, GridConfig  # noqa: E402
from openfollow.scene.solver import project_points  # noqa: E402

CANVAS_W = 1920.0
CANVAS_H = 1080.0

# Scene dimensions (metres, PSN coords).
STAGE_X_HALF = 7.0  # stage is 14 m wide
PROSC_X_HALF = 6.0  # proscenium opening is 12 m wide (2 m narrower than stage)
PROSC_DEPTH = 0.4  # proscenium wall thickness upstage of the front face
STAGE_Y_FRONT = -0.5  # apron slightly in front of grid DS edge (y=0)
STAGE_Y_BACK = 7.5  # back wall slightly beyond grid US edge (y=6)
STAGE_H = 5.5  # cyc/side-leg height
APRON_DROP = 1.0  # stage sits 1 m above auditorium floor
PROSC_OPENING_H = 5.0  # top of proscenium opening (header hangs below cyc)
PROSC_TOP_H = 3.0  # dark header height above the opening

# Wizard-marker styling – keep in sync with openfollow/web/templates/wizard.tpl
# (corner handle + reference-point crosshair). Used in the docs overlay.
MARKER_COLOR = "#ffbc00"
MARKER_FILL = "rgba(255,188,0,0.8)"
MARKER_LABEL = "rgba(247,245,233,0.8)"

# Stage-tape markers baked into the testpattern asset: white spike tape on
# the physical stage, in world coords so they foreshorten with perspective.
TAPE_COLOR = "#ffffff"
TAPE_BRACKET_LEN = 0.35  # bracket arm length, metres (inside the grid)
TAPE_CROSS_HALF = 0.25  # origin cross half-arm, metres

# Seat rows – y (distance from stage), tier z (below stage level). All between
# the stage apron and the camera, so projection never crosses the camera plane.
SEAT_ROWS = [
    # (y, z, visible)
    (-1.5, -0.9),
    (-3.0, -0.65),
    (-4.5, -0.4),
    (-6.5, -0.05),
    (-9.0, 0.35),
]
SEAT_W = 0.56
SEAT_GAP = 0.08
SEAT_BACK_H = 0.55
SEAT_BACK_TILT = 0.12  # top of seat-back leans away from stage


def cam_params(cam: CameraConfig) -> np.ndarray:
    return np.array([cam.pos_x, cam.pos_y, cam.pos_z, cam.pitch, cam.yaw, cam.roll, cam.fov], dtype=float)


def proj(params: np.ndarray, pts) -> np.ndarray:
    return project_points(params, np.asarray(pts, dtype=float), CANVAS_W, CANVAS_H)


def poly_str(pts_2d) -> str:
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in pts_2d)


def grid_corners(grid: GridConfig) -> list[tuple[float, float, float]]:
    """Return DSL, DSR, USR, USL grid corners in PSN coords.

    PSN +X is stage left, so the stage-left corners (DSL, USL) sit at +hw and
    the stage-right corners (DSR, USR) at -hw.
    """
    hw = grid.width / 2.0
    hd = grid.depth / 2.0
    cx = grid.x_offset
    cy = grid.y_offset
    z = grid.z_offset
    return [
        (cx + hw, cy - hd, z),  # DSL (downstage stage-left)
        (cx - hw, cy - hd, z),  # DSR (downstage stage-right)
        (cx - hw, cy + hd, z),  # USR (upstage stage-right)
        (cx + hw, cy + hd, z),  # USL (upstage stage-left)
    ]


def build_svg(cam: CameraConfig, grid: GridConfig, *, include_grid_overlay: bool) -> str:
    p = cam_params(cam)
    lines: list[str] = []
    push = lines.append

    push(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1920 1080" '
        'width="100%" preserveAspectRatio="xMidYMid meet" '
        'font-family="system-ui, -apple-system, sans-serif">'
    )
    push("<defs>")
    push(
        '<linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0%" stop-color="#1a1820"/>'
        '<stop offset="100%" stop-color="#0a0a10"/>'
        "</linearGradient>"
    )
    push(
        '<linearGradient id="cyc" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0%" stop-color="#3a3050"/>'
        '<stop offset="100%" stop-color="#1a1428"/>'
        "</linearGradient>"
    )
    push(
        '<linearGradient id="floor" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0%" stop-color="#2a2218"/>'
        '<stop offset="100%" stop-color="#181208"/>'
        "</linearGradient>"
    )
    push("</defs>")
    push('<rect x="0" y="0" width="1920" height="1080" fill="url(#bg)"/>')

    _draw_cyc_and_wash(lines, p)
    _draw_side_legs(lines, p)
    _draw_stage_floor(lines, p)
    _draw_apron_face(lines, p)
    _draw_proscenium_surround(lines, p)
    _draw_auditorium_floor(lines, p)
    _draw_seats(lines, p)

    if include_grid_overlay:
        _draw_grid_overlay(lines, p, grid)
        _draw_origin_marker(lines, p, grid)
        _draw_corner_markers(lines, p, grid)
    else:
        _draw_tape_corners(lines, p, grid)
        _draw_tape_origin(lines, p, grid)

    push('<rect x="0.5" y="0.5" width="1919" height="1079" fill="none" stroke="#000" stroke-width="1"/>')
    push("</svg>")
    push("")
    return "\n".join(lines)


def _draw_cyc_and_wash(out, p):
    cyc = [
        (-STAGE_X_HALF, STAGE_Y_BACK, 0),
        (STAGE_X_HALF, STAGE_Y_BACK, 0),
        (STAGE_X_HALF, STAGE_Y_BACK, STAGE_H),
        (-STAGE_X_HALF, STAGE_Y_BACK, STAGE_H),
    ]
    cyc_px = proj(p, cyc)
    out.append(f'<polygon points="{poly_str(cyc_px)}" fill="url(#cyc)" stroke="#0a0612" stroke-width="1"/>')


def _draw_side_legs(out, p):
    left = [
        (-STAGE_X_HALF, STAGE_Y_FRONT, 0),
        (-STAGE_X_HALF, STAGE_Y_BACK, 0),
        (-STAGE_X_HALF, STAGE_Y_BACK, STAGE_H),
        (-STAGE_X_HALF, STAGE_Y_FRONT, STAGE_H),
    ]
    right = [
        (STAGE_X_HALF, STAGE_Y_FRONT, 0),
        (STAGE_X_HALF, STAGE_Y_BACK, 0),
        (STAGE_X_HALF, STAGE_Y_BACK, STAGE_H),
        (STAGE_X_HALF, STAGE_Y_FRONT, STAGE_H),
    ]
    out.append(f'<polygon points="{poly_str(proj(p, left))}"  fill="#0c0810" stroke="#000" stroke-width="1"/>')
    out.append(f'<polygon points="{poly_str(proj(p, right))}" fill="#0c0810" stroke="#000" stroke-width="1"/>')


def _draw_stage_floor(out, p):
    floor = [
        (-STAGE_X_HALF, STAGE_Y_FRONT, 0),
        (STAGE_X_HALF, STAGE_Y_FRONT, 0),
        (STAGE_X_HALF, STAGE_Y_BACK, 0),
        (-STAGE_X_HALF, STAGE_Y_BACK, 0),
    ]
    out.append(f'<polygon points="{poly_str(proj(p, floor))}" fill="url(#floor)" stroke="#0a0805" stroke-width="1"/>')


def _draw_apron_face(out, p):
    apron = [
        (-STAGE_X_HALF, STAGE_Y_FRONT, 0),
        (STAGE_X_HALF, STAGE_Y_FRONT, 0),
        (STAGE_X_HALF, STAGE_Y_FRONT, -APRON_DROP),
        (-STAGE_X_HALF, STAGE_Y_FRONT, -APRON_DROP),
    ]
    out.append(f'<polygon points="{poly_str(proj(p, apron))}" fill="#1a1208" stroke="#000" stroke-width="1"/>')


def _draw_proscenium_surround(out, p):
    """Dark walls/ceiling framing the stage opening, extruded 0.4 m upstage
    so the inner reveals of the opening show the wall thickness.
    """
    # Extends far beyond frame so edges always clip outside viewBox.
    X_OUT = 30.0
    Z_TOP = PROSC_OPENING_H + PROSC_TOP_H
    Y_F = STAGE_Y_FRONT
    Y_B = STAGE_Y_FRONT + PROSC_DEPTH

    # Inner returns (reveals inside the opening) – drawn first so the downstage
    # front faces paint over their leading edge. Darker shade to read as shadow.
    left_return = [
        (-PROSC_X_HALF, Y_F, 0.0),
        (-PROSC_X_HALF, Y_B, 0.0),
        (-PROSC_X_HALF, Y_B, PROSC_OPENING_H),
        (-PROSC_X_HALF, Y_F, PROSC_OPENING_H),
    ]
    right_return = [
        (PROSC_X_HALF, Y_F, 0.0),
        (PROSC_X_HALF, Y_F, PROSC_OPENING_H),
        (PROSC_X_HALF, Y_B, PROSC_OPENING_H),
        (PROSC_X_HALF, Y_B, 0.0),
    ]
    top_soffit = [
        (-PROSC_X_HALF, Y_F, PROSC_OPENING_H),
        (PROSC_X_HALF, Y_F, PROSC_OPENING_H),
        (PROSC_X_HALF, Y_B, PROSC_OPENING_H),
        (-PROSC_X_HALF, Y_B, PROSC_OPENING_H),
    ]
    out.append(f'<polygon points="{poly_str(proj(p, left_return))}"  fill="#0c0810" stroke="#000" stroke-width="1"/>')
    out.append(f'<polygon points="{poly_str(proj(p, right_return))}" fill="#0c0810" stroke="#000" stroke-width="1"/>')
    out.append(f'<polygon points="{poly_str(proj(p, top_soffit))}"   fill="#0c0810" stroke="#000" stroke-width="1"/>')

    # Front faces (downstage side, visible to the audience).
    left_wall = [
        (-X_OUT, Y_F, -APRON_DROP),
        (-PROSC_X_HALF, Y_F, -APRON_DROP),
        (-PROSC_X_HALF, Y_F, Z_TOP),
        (-X_OUT, Y_F, Z_TOP),
    ]
    right_wall = [
        (PROSC_X_HALF, Y_F, -APRON_DROP),
        (X_OUT, Y_F, -APRON_DROP),
        (X_OUT, Y_F, Z_TOP),
        (PROSC_X_HALF, Y_F, Z_TOP),
    ]
    top_wall = [
        (-X_OUT, Y_F, PROSC_OPENING_H),
        (X_OUT, Y_F, PROSC_OPENING_H),
        (X_OUT, Y_F, Z_TOP),
        (-X_OUT, Y_F, Z_TOP),
    ]
    out.append(f'<polygon points="{poly_str(proj(p, left_wall))}"  fill="#1a1418" stroke="#000" stroke-width="1"/>')
    out.append(f'<polygon points="{poly_str(proj(p, right_wall))}" fill="#1a1418" stroke="#000" stroke-width="1"/>')
    out.append(f'<polygon points="{poly_str(proj(p, top_wall))}"   fill="#1a1418" stroke="#000" stroke-width="1"/>')


def _draw_auditorium_floor(out, p):
    # Extends to just in front of the camera. pos_y is where the camera is;
    # anything closer than ~1 m from camera produces huge projected polygons.
    cam_y = p[1]
    y_back_of_house = cam_y + 1.0
    floor = [
        (-30.0, y_back_of_house, -APRON_DROP),
        (30.0, y_back_of_house, -APRON_DROP),
        (30.0, STAGE_Y_FRONT, -APRON_DROP),
        (-30.0, STAGE_Y_FRONT, -APRON_DROP),
    ]
    out.append(f'<polygon points="{poly_str(proj(p, floor))}" fill="#15100c" stroke="#000" stroke-width="1"/>')


def _draw_seats(out, p):
    cam_y = p[1]
    for row_y, tier_z in SEAT_ROWS:
        # Skip rows that are behind (or too close to) the camera.
        if row_y <= cam_y + 0.5:
            continue
        x = -11.0
        x_end = 11.0
        while x < x_end - 0.01:
            x2 = x + SEAT_W
            seat = [
                (x, row_y, tier_z),
                (x2, row_y, tier_z),
                (x2, row_y - SEAT_BACK_TILT, tier_z + SEAT_BACK_H),
                (x, row_y - SEAT_BACK_TILT, tier_z + SEAT_BACK_H),
            ]
            out.append(
                f'<polygon points="{poly_str(proj(p, seat))}" fill="#5a1212" stroke="#1a0606" stroke-width="0.6"/>'
            )
            x += SEAT_W + SEAT_GAP


def _draw_grid_overlay(out, p, grid: GridConfig):
    corners_world = grid_corners(grid)
    corners_px = proj(p, corners_world)
    out.append(
        f'<polygon points="{poly_str(corners_px)}" '
        f'fill="#c9a14a" fill-opacity="0.10" stroke="#c9a14a" '
        f'stroke-opacity="0.55" stroke-width="2"/>'
    )
    # Grid lines (along x and along y) inside the quad
    hw = grid.width / 2.0
    hd = grid.depth / 2.0
    cx = grid.x_offset
    cy = grid.y_offset
    z = grid.z_offset
    step = grid.spacing
    x = -hw
    while x <= hw + 1e-6:
        wx = cx + x
        pts = proj(p, [(wx, cy - hd, z), (wx, cy + hd, z)])
        out.append(
            f'<line x1="{pts[0][0]:.1f}" y1="{pts[0][1]:.1f}" '
            f'x2="{pts[1][0]:.1f}" y2="{pts[1][1]:.1f}" '
            f'stroke="#c9a14a" stroke-opacity="0.30" stroke-width="1"/>'
        )
        x += step
    y = -hd
    while y <= hd + 1e-6:
        wy = cy + y
        pts = proj(p, [(-hw + cx, wy, z), (hw + cx, wy, z)])
        out.append(
            f'<line x1="{pts[0][0]:.1f}" y1="{pts[0][1]:.1f}" '
            f'x2="{pts[1][0]:.1f}" y2="{pts[1][1]:.1f}" '
            f'stroke="#c9a14a" stroke-opacity="0.30" stroke-width="1"/>'
        )
        y += step


def _draw_corner_markers(out, p, grid: GridConfig):
    """Yellow circle + text label at each grid corner – same styling as
    the wizard's corner-pinning step (openfollow/web/templates/wizard.tpl).
    """
    corners_px = proj(p, grid_corners(grid))
    labels = ["DSL", "DSR", "USR", "USL"]
    for (x, y), label in zip(corners_px, labels, strict=True):
        out.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="7" fill="{MARKER_FILL}" stroke="{MARKER_COLOR}" stroke-width="1.5"/>'
        )
        out.append(
            f'<text x="{x + 12:.1f}" y="{y + 4:.1f}" '
            f'fill="{MARKER_LABEL}" font-size="11" font-weight="600">{label}</text>'
        )


def _draw_origin_marker(out, p, grid: GridConfig):
    """Draw crosshair + ring at PSN origin (0/0/0)."""
    origin_px = proj(p, [(0.0, 0.0, grid.z_offset)])[0]
    x, y = origin_px
    out.append(
        f'<line x1="{x - 10:.1f}" y1="{y:.1f}" x2="{x + 10:.1f}" y2="{y:.1f}" '
        f'stroke="{MARKER_COLOR}" stroke-width="2"/>'
    )
    out.append(
        f'<line x1="{x:.1f}" y1="{y - 10:.1f}" x2="{x:.1f}" y2="{y + 10:.1f}" '
        f'stroke="{MARKER_COLOR}" stroke-width="2"/>'
    )
    out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="8" fill="none" stroke="{MARKER_COLOR}" stroke-width="1.5"/>')


def _draw_tape_corners(out, p, grid: GridConfig):
    """Draw L-brackets at grid corners like stage-manager spike tape."""
    hw = grid.width / 2.0
    hd = grid.depth / 2.0
    cx = grid.x_offset
    cy = grid.y_offset
    z = grid.z_offset
    L = TAPE_BRACKET_LEN
    # (corner, inward_x_sign, inward_y_sign). +X is stage left, so the -hw
    # corners are stage right (DSR/USR) and the +hw corners stage left (DSL/USL).
    specs = [
        ((cx - hw, cy - hd, z), +1, +1),  # DSR (downstage stage-right)
        ((cx + hw, cy - hd, z), -1, +1),  # DSL (downstage stage-left)
        ((cx + hw, cy + hd, z), -1, -1),  # USL (upstage stage-left)
        ((cx - hw, cy + hd, z), +1, -1),  # USR (upstage stage-right)
    ]
    for (x0, y0, z0), sx, sy in specs:
        x_arm_end = (x0 + sx * L, y0, z0)
        y_arm_end = (x0, y0 + sy * L, z0)
        pts = proj(p, [(x0, y0, z0), x_arm_end, y_arm_end])
        (cx_px, cy_px), (ax_px, ay_px), (bx_px, by_px) = pts
        out.append(
            f'<line x1="{cx_px:.1f}" y1="{cy_px:.1f}" '
            f'x2="{ax_px:.1f}" y2="{ay_px:.1f}" '
            f'stroke="{TAPE_COLOR}" stroke-width="3" stroke-linecap="square"/>'
        )
        out.append(
            f'<line x1="{cx_px:.1f}" y1="{cy_px:.1f}" '
            f'x2="{bx_px:.1f}" y2="{by_px:.1f}" '
            f'stroke="{TAPE_COLOR}" stroke-width="3" stroke-linecap="square"/>'
        )


def _draw_tape_origin(out, p, grid: GridConfig):
    """Draw cross at PSN (0, 0, z_offset) – the reference point to pin."""
    z = grid.z_offset
    L = TAPE_CROSS_HALF
    pts = proj(
        p,
        [
            (-L, 0.0, z),
            (L, 0.0, z),
            (0.0, -L, z),
            (0.0, L, z),
        ],
    )
    out.append(
        f'<line x1="{pts[0][0]:.1f}" y1="{pts[0][1]:.1f}" '
        f'x2="{pts[1][0]:.1f}" y2="{pts[1][1]:.1f}" '
        f'stroke="{TAPE_COLOR}" stroke-width="3" stroke-linecap="square"/>'
    )
    out.append(
        f'<line x1="{pts[2][0]:.1f}" y1="{pts[2][1]:.1f}" '
        f'x2="{pts[3][0]:.1f}" y2="{pts[3][1]:.1f}" '
        f'stroke="{TAPE_COLOR}" stroke-width="3" stroke-linecap="square"/>'
    )


def main() -> None:
    cam = CameraConfig()
    grid = GridConfig()

    docs_path = ROOT / "docs" / "stage_default_perspective.svg"
    asset_path = ROOT / "openfollow" / "video" / "inputs" / "assets" / "stage_default.svg"

    docs_path.write_text(build_svg(cam, grid, include_grid_overlay=True))
    asset_path.write_text(build_svg(cam, grid, include_grid_overlay=False))

    print(
        f"Camera: pos=({cam.pos_x}, {cam.pos_y}, {cam.pos_z}) "
        f"pitch={cam.pitch} yaw={cam.yaw} roll={cam.roll} hfov={cam.fov}"
    )
    print(f"Grid:   {grid.width}x{grid.depth} m, y_offset={grid.y_offset}, spacing={grid.spacing}")
    corners_px = proj(cam_params(cam), grid_corners(grid))
    for label, (x, y) in zip(["DSL", "DSR", "USR", "USL"], corners_px, strict=True):
        print(f"  {label}: ({x:.1f}, {y:.1f})")
    print(f"Wrote: {docs_path.relative_to(ROOT)}")
    print(f"Wrote: {asset_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
