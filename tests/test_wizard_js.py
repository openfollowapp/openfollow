# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The setup wizard's script maths, run in node and held against the Python it mirrors."""

from __future__ import annotations

import math

import numpy as np
import pytest

from openfollow.lens_model import lens_fold_radius, lens_warp_is_valid
from openfollow.runtime.overlay_draw_scene import grid_line_count, project
from openfollow.scene.edge_chains import edge_map_scale
from openfollow.scene.edge_snap import band_half_size, band_step
from openfollow.scene.solver import apply_overlay_distortion, invert_normalised_radius
from tests._wizard_js import needs_node, run_wizard_js

pytestmark = [pytest.mark.unit, needs_node]

_LENS_FUNCTIONS = (
    "wizLensFoldRadius",
    "wizLensIsValid",
    "wizApplyDistortion",
    "wizInvertRadius",
    "wizInvertDistortion",
)
_LENS_VARS = ("DISTORTION_SUBDIVISIONS", "DISTORTION_INVERT_ITERS", "DISTORTION_INVERT_R_CAP")
_W, _H = 1920, 1080

# Pairs across the valid region: pinhole, barrel, a 100 degree lens, pincushion, mixed signs.
_PAIRS = [(0.0, 0.0), (-0.1, 0.0), (-0.26, 0.05), (-0.47, 0.25), (0.2, 0.0), (0.1, -0.05), (-0.3, 0.12)]


def _hud_inner_lines(cam: np.ndarray, grid: dict, k1: float, k2: float) -> np.ndarray:
    """The HUD's inner grid lines: world segments cut into chords, projected and bowed."""
    hw, hd = grid["width"] / 2, grid["depth"] / 2
    ox, oy, oz = grid["x_offset"], grid["y_offset"], grid["z_offset"]
    n_z, n_x = grid_line_count(grid["depth"], grid["spacing"]), grid_line_count(grid["width"], grid["spacing"])
    segments = [((ox - hw, y, oz), (ox + hw, y, oz)) for y in np.linspace(oy - hd, oy + hd, n_z)[1:-1]]
    segments += [((x, oy - hd, oz), (x, oy + hd, oz)) for x in np.linspace(ox - hw, ox + hw, n_x)[1:-1]]
    chords = 12 if (k1 or k2) else 1
    ts = np.linspace(0.0, 1.0, chords + 1)[None, :, None]
    seg = np.array(segments, dtype=np.float64)
    world = seg[:, 0:1, :] + ts * (seg[:, 1:2, :] - seg[:, 0:1, :])
    return project(cam, world.reshape(-1, 3), _W, _H, k1, k2).reshape(len(segments), chords + 1, 2)


@pytest.mark.parametrize(
    ("cam", "grid", "lens"),
    [
        ([0.0, -9.0, 4.0, -18.0, 0.0, 0.0, 70.0], (10.0, 6.0, 1.0, 0.0, 3.0, 0.0), (0.0, 0.0)),
        ([1.5, -7.0, 3.0, -22.0, 6.0, 2.0, 90.0], (12.0, 8.0, 0.75, 0.5, 4.0, 0.3), (-0.26, 0.05)),
        ([-2.0, -6.0, 5.0, -35.0, -10.0, -1.0, 100.0], (9.5, 6.3, 1.0, 0.0, 3.2, 0.0), (-0.47, 0.25)),
        ([0.0, -10.0, 2.0, -10.0, 0.0, 0.0, 50.0], (8.0, 5.0, 0.5, 0.0, 2.5, 0.0), (0.2, 0.0)),
    ],
)
def test_grid_lines_are_the_huds_inner_lines(cam: list[float], grid: tuple, lens: tuple[float, float]) -> None:
    width, depth, spacing, ox, oy, oz = grid
    g = {"width": width, "depth": depth, "spacing": spacing, "x_offset": ox, "y_offset": oy, "z_offset": oz}
    k1, k2 = lens
    camera = np.array(cam)
    hw, hd = width / 2, depth / 2
    # DSL / USL sit at +X: stage left.
    world = np.array([[ox + hw, oy - hd, oz], [ox - hw, oy - hd, oz], [ox - hw, oy + hd, oz], [ox + hw, oy + hd, oz]])
    corners = dict(zip(("DSL", "DSR", "USR", "USL"), project(camera, world, _W, _H, k1, k2).tolist(), strict=True))
    lines = run_wizard_js(
        "wizGridLines(corners, grid, k1, k2)",
        functions=(*_LENS_FUNCTIONS, "isConvex", "wizGridLineCount", "wizSquareToQuad", "wizGridLines"),
        variables=(*_LENS_VARS, "GRID_MAX_LINES_PER_AXIS", "CORNER_NAMES"),
        imageWidth=_W,
        imageHeight=_H,
        corners=corners,
        grid=g,
        k1=k1,
        k2=k2,
    )
    np.testing.assert_allclose(np.array(lines), _hud_inner_lines(camera, g, k1, k2), atol=1e-6)


@pytest.mark.parametrize(("length", "spacing"), [(10.0, 1.0), (9.5, 1.0), (0.1, 1.0), (7.0, 0.7), (5e3, 0.05)])
def test_line_count_is_the_huds(length: float, spacing: float) -> None:
    count = run_wizard_js(
        "wizGridLineCount(length, spacing)",
        functions=("wizGridLineCount",),
        variables=("GRID_MAX_LINES_PER_AXIS",),
        length=length,
        spacing=spacing,
    )
    assert count == grid_line_count(length, spacing)


def test_fold_radius_and_validity_are_the_servers() -> None:
    k1s = np.round(np.arange(-0.8, 0.81, 0.04), 2).tolist()
    k2s = np.round(np.arange(-0.4, 0.41, 0.04), 2).tolist()
    out = run_wizard_js(
        "pairs.map(function(p) { return [wizLensFoldRadius(p[0], p[1]), wizLensIsValid(p[0], p[1])]; })",
        functions=("wizLensFoldRadius", "wizLensIsValid"),
        pairs=[[a, b] for a in k1s for b in k2s],
    )
    for (radius, valid), (a, b) in zip(out, [(a, b) for a in k1s for b in k2s], strict=True):
        expected = lens_fold_radius(a, b)
        assert (radius is None) == math.isinf(expected), (a, b)
        if radius is not None:
            assert radius == pytest.approx(expected, rel=1e-9), (a, b)
        assert valid == lens_warp_is_valid(a, b), (a, b)


@pytest.mark.parametrize(("k1", "k2"), _PAIRS)
def test_inverse_radius_is_the_servers(k1: float, k2: float) -> None:
    radii = np.linspace(0.0, 1.0, 41).tolist()
    out = run_wizard_js(
        "radii.map(function(r) { return wizInvertRadius(r, k1, k2); })",
        functions=("wizLensFoldRadius", "wizInvertRadius"),
        variables=_LENS_VARS,
        radii=radii,
        k1=k1,
        k2=k2,
    )
    np.testing.assert_allclose(out, invert_normalised_radius(np.array(radii), k1, k2), atol=1e-12)


@pytest.mark.parametrize(("k1", "k2"), _PAIRS)
def test_bowing_and_unbowing_a_point_are_the_servers(k1: float, k2: float) -> None:
    points = [[0.0, 0.0], [960.0, 540.0], [1919.0, 1079.0], [300.0, 900.0], [1700.0, 100.0]]
    bowed, unbowed = run_wizard_js(
        "[points.map(function(p) { return wizApplyDistortion(p, k1, k2); }),"
        " points.map(function(p) { return wizInvertDistortion(p, k1, k2); })]",
        functions=_LENS_FUNCTIONS,
        variables=_LENS_VARS,
        imageWidth=_W,
        imageHeight=_H,
        points=points,
        k1=k1,
        k2=k2,
    )
    np.testing.assert_allclose(bowed, apply_overlay_distortion(np.array(points), _W, _H, k1, k2), atol=1e-9)
    # The inverse bows back onto the click wherever the warp reaches it.
    np.testing.assert_allclose(apply_overlay_distortion(np.array(unbowed), _W, _H, k1, k2), points, atol=1e-6)


@pytest.mark.parametrize("width", [320, 640, 1280, 1920, 2560, 3840, 7680])
def test_band_and_edge_map_geometry_is_the_servers(width: int) -> None:
    half, step, scale = run_wizard_js(
        "[lensBandHalf(), lensBandStep(), lensEdgeScale()]",
        functions=("lensBandHalf", "lensBandStep", "lensEdgeScale"),
        imageWidth=width,
    )
    assert (half, step, scale) == (band_half_size(width), band_step(width), edge_map_scale(width))
