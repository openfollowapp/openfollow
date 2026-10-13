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
from openfollow.scene.lens_fit import fit_lens_from_lines
from openfollow.scene.solver import apply_overlay_distortion, invert_normalised_radius
from openfollow.units import format_length
from tests._lens_band import scaled_luma
from tests._wizard_js import length_page, needs_node, run_wizard_js

pytestmark = [pytest.mark.unit, needs_node]

_LENS_FUNCTIONS = (
    "wizLensFoldRadius",
    "wizLensIsValid",
    "wizApplyDistortion",
    "wizInvertRadius",
    "wizInvertDistortion",
)
_LENS_VARS = (
    "DISTORTION_SUBDIVISIONS",
    "DISTORTION_INVERT_ITERS",
    "DISTORTION_INVERT_SETTLED",
    "DISTORTION_INVERT_R_CAP",
)
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
        pairs=[[a, b] for a in k1s for b in k2s] + [[-1e200, 1e200], [2e3, 0.0]],
    )
    for (radius, valid), (a, b) in zip(
        out, [(a, b) for a in k1s for b in k2s] + [(-1e200, 1e200), (2e3, 0.0)], strict=True
    ):
        expected = lens_fold_radius(a, b)
        assert (radius is None) == math.isinf(expected), (a, b)
        if radius is not None:
            assert radius == pytest.approx(expected, rel=1e-9), (a, b)
        assert valid == lens_warp_is_valid(a, b), (a, b)


# Just above k2 = 9 k1^2 / 20, where Newton creeps.
_SLOW_PAIRS = [(k1, 9.0 * k1 * k1 / 20.0 * (1.0 + 1e-9)) for k1 in (-0.8, -0.47, -0.38, -0.19)]


@pytest.mark.parametrize(("k1", "k2"), _PAIRS + _SLOW_PAIRS)
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


# 1200, 2160, 3120 and 1285 sit exactly on a half, where Python's round() goes to even.
@pytest.mark.parametrize("width", [320, 640, 1200, 1280, 1285, 1920, 2160, 2560, 3120, 3840, 7680])
def test_band_and_edge_map_geometry_is_the_servers(width: int) -> None:
    half, step, scale = run_wizard_js(
        "[lensBandHalf(), lensBandStep(), lensEdgeScale()]",
        functions=("lensBandHalf", "lensBandStep", "lensEdgeScale"),
        imageWidth=width,
    )
    assert (half, step, scale) == (band_half_size(width), band_step(width), edge_map_scale(width))


def test_a_restored_line_must_have_the_shape_the_page_draws() -> None:
    good = {"p0": [1, 2], "p1": [300, 4], "mids": [{}, {}, {}], "snapped": [True] * 5}
    lines = [
        good,
        None,
        {**good, "p0": [1]},
        {**good, "p1": ["a", 2]},
        {**good, "mids": [{}]},
        {key: value for key, value in good.items() if key != "snapped"},
    ]
    kept = run_wizard_js(
        "lines.map(function(line) { return lensRestoredLine(line); })",
        functions=("lensRestoredPair", "lensRestoredLine"),
        lines=lines,
    )
    assert kept == [True, False, False, False, False, False]


@pytest.mark.parametrize("scale", [1, 2, 3, 4])
def test_the_edge_map_is_the_block_means_the_server_is_tested_with(scale: int) -> None:
    rng = np.random.default_rng(scale)
    h, w = 37, 53
    rgb = rng.integers(0, 256, (h, w, 3))
    rgb[:, 20] = 255  # a one-pixel seam, which a sampled downscale loses between its samples
    rgba = np.concatenate([rgb, np.full((h, w, 1), 255)], axis=2)
    luma = run_wizard_js(
        "Array.from(lensScaledLuma(rgba, w, h, scale))",
        functions=("lensScaledLuma",),
        rgba=rgba.ravel().tolist(),
        w=w,
        h=h,
        scale=scale,
    )
    expected = scaled_luma((rgb[..., 0] * 299 + rgb[..., 1] * 587 + rgb[..., 2] * 114) / 1000.0, scale)
    diff = np.abs(np.array(luma).reshape(expected.shape) - expected)
    # Only a mean exactly on a half may differ: Math.round goes up, numpy to even.
    assert diff.max() <= 1 and np.mean(diff == 0) > 0.95


@pytest.mark.parametrize("span", [9.5, 10.0, 10.5, 40.0])
def test_a_line_counts_exactly_when_the_fit_takes_it(span: float) -> None:
    line = {
        "p0": [100.0, 200.0],
        "p1": [100.0 + span, 200.0],
        "mids": [{"t": t, "off": 0.0, "on": True} for t in (0.25, 0.5, 0.75)],
    }
    counts = run_wizard_js(
        "lensLineCounts(line)",
        functions=("lensFrame", "lensPointPos", "lensPointIsOn", "lensActivePoints", "lensLineCounts"),
        variables=("LENS_MIN_SPAN_PX",),
        line=line,
    )
    points = [[100.0 + span * t, 200.0] for t in (0.0, 0.25, 0.5, 0.75, 1.0)]
    try:
        fit_lens_from_lines([points], 1920.0, 1080.0)
        taken = True
    except ValueError:
        taken = False
    assert counts == taken


@pytest.mark.parametrize(("width", "height"), [(324, 182), (390, 219), (1079, 608), (1400, 788)])
def test_the_loupe_stays_inside_the_preview_and_beside_the_finger(width: int, height: int) -> None:
    size = 120
    fingers = [[x, y] for x in np.linspace(0, width, 27).tolist() for y in np.linspace(0, height, 15).tolist()]
    places = run_wizard_js(
        "fingers.map(function(f) { return loupePlace(f[0], f[1], size, width, height); })",
        functions=("loupePlace",),
        fingers=fingers,
        size=size,
        width=width,
        height=height,
    )
    for (sx, sy), (left, top) in zip(fingers, places, strict=True):
        # A phone's preview is shorter than the loupe above and below the finger together.
        assert 0 <= left <= width - size and 0 <= top <= height - size, (sx, sy)
        # Every preview here is wider than two loupes with their offsets, so the finger stays in sight.
        assert left >= sx + 30 or left + size <= sx - 30, (sx, sy)


_LENGTH_FUNCTIONS = ("wizReadLen", "wizUpdateEcho", "wizWriteLen")


@pytest.mark.parametrize("system", ["imperial", "metric"])
@pytest.mark.parametrize("meters", [0.5, 7.5, 0.3048, -2.25, 11.0, 0.123456])
def test_a_written_length_reads_back_the_metres_it_was_written_from(system: str, meters: float) -> None:
    shown, read = run_wizard_js(
        "(function() { wizWriteLen('f', m); return [fields.f.value, wizReadLen('f')]; })()",
        functions=_LENGTH_FUNCTIONS,
        prelude=length_page(system, {"f": {"value": "", "dataset": {}}}),
        m=meters,
    )
    # Read back from its ft / in text, 0.5 m was 0.500126 m: one grid line more than the HUD draws.
    assert read == meters
    if system == "imperial":
        assert shown == format_length(meters, "imperial")


def test_an_edited_imperial_length_reads_what_it_shows() -> None:
    read = run_wizard_js(
        "(function() { wizWriteLen('f', 0.5); fields.f.value = '2 ft'; return wizReadLen('f'); })()",
        functions=_LENGTH_FUNCTIONS,
        prelude=length_page("imperial", {"f": {"value": "", "dataset": {}}}),
    )
    assert read == pytest.approx(0.6096, abs=1e-12)


@pytest.mark.parametrize(
    ("k1", "k2", "shown"),
    [
        (-0.3, 0.02, ["-0.3000", "0.0200"]),
        # Four decimals would put the pair on the fold.
        (-0.5, 0.10001, ["-0.5", "0.10001"]),
    ],
)
def test_a_fitted_pair_is_shown_as_a_valid_pair(k1: float, k2: float, shown: list[str]) -> None:
    out = run_wizard_js(
        "wizLensShownPair(k1, k2)",
        functions=("wizLensFoldRadius", "wizLensIsValid", "wizLensShownPair"),
        k1=k1,
        k2=k2,
    )
    assert out == shown
    assert lens_warp_is_valid(float(out[0]), float(out[1]))


def _zoom_boxes() -> list[dict]:
    """Every element the template gives the ``fine-zoom-box`` class, with its classes and data attributes."""
    import re

    from tests._wizard_js import WIZARD_TEMPLATE

    boxes = []
    for tag in re.findall(r"<div\b[^>]*\bclass=\"[^\"]*\bfine-zoom-box\b[^\"]*\"[^>]*>", WIZARD_TEMPLATE.read_text()):
        attrs = dict(re.findall(r'([\w-]+)="([^"]*)"', tag))
        dataset = {k[len("data-") :]: v for k, v in attrs.items() if k.startswith("data-")}
        boxes.append({"classes": attrs["class"].split(), "attrs": attrs, "dataset": dataset})
    return boxes


def test_the_corner_drag_is_wired_to_the_corner_boxes_only() -> None:
    boxes = _zoom_boxes()
    # The Reference Mapping crop shares the look of a corner box but drags the Reference Point.
    assert any("corner" not in box["dataset"] for box in boxes)
    wired = run_wizard_js(
        "(function() { setupFineZoomDragging(); return wired; })()",
        functions=("setupFineZoomDragging",),
        prelude="""
var wired = [];
function _attachFineZoomBoxHandlers(box) { wired.push(box.dataset.corner === undefined ? null : box.dataset.corner); }
var document = { querySelectorAll: function(sel) {
  var m = /^\\.([\\w-]+)((?:\\[[\\w-]+\\])*)$/.exec(sel);
  if (!m) throw new Error('selector the test cannot read: ' + sel);
  var needs = m[2].match(/[\\w-]+/g) || [];
  return BOXES.filter(function(b) {
    return b.classes.indexOf(m[1]) !== -1 && needs.every(function(a) { return a in b.attrs; });
  });
} };
""",
        BOXES=boxes,
    )
    assert sorted(wired, key=str) == ["DSL", "DSR", "USL", "USR"]


@pytest.mark.parametrize(("k1", "k2"), [(-0.33, 1e-15), (-0.33, -1e-15), (-0.3, 1e-12), (0.4, 1e-14)])
def test_fold_radius_is_the_servers_when_the_edge_term_is_tiny(k1: float, k2: float) -> None:
    radius, valid = run_wizard_js(
        "[wizLensFoldRadius(k1, k2), wizLensIsValid(k1, k2)]",
        functions=("wizLensFoldRadius", "wizLensIsValid"),
        k1=k1,
        k2=k2,
    )
    expected = lens_fold_radius(k1, k2)
    assert (radius is None) == math.isinf(expected)
    if radius is not None:
        assert radius == pytest.approx(expected, rel=1e-9)
    assert valid == lens_warp_is_valid(k1, k2)


@pytest.mark.parametrize(("k1", "k2"), [(-0.3, 0.02), (-0.5, 0.10001)])
def test_the_pair_a_fit_wrote_reads_as_the_fits(k1: float, k2: float) -> None:
    out = run_wizard_js(
        "(function() { var s = wizLensShownPair(k1, k2); return lensPairIsTheFits(Number(s[0]), Number(s[1])); })()",
        functions=("wizLensFoldRadius", "wizLensIsValid", "wizLensShownPair", "lensFitApplies", "lensPairIsTheFits"),
        prelude=f"var lensFit = {{ k1: {k1!r}, k2: {k2!r}, rating: 'good' }};",
        k1=k1,
        k2=k2,
    )
    assert out is True


def test_an_edit_drops_the_answer_to_a_fit_already_in_flight() -> None:
    out = run_wizard_js(
        "(function() { var before = lensFitSeq; scheduleLensFit(); return lensFitSeq !== before; })()",
        functions=("scheduleLensFit",),
        prelude="var lensFitSeq = 3, lensFitTimer = null, LENS_FIT_DEBOUNCE_MS = 300;"
        " function setTimeout() { return 1; } function clearTimeout() {} function runLensFit() {}",
    )
    assert out is True


# A thenable that settles at once, so a fetch chain runs before the expression is read.
_SYNC_FETCH = (
    "function settled(v) { return { then: function(f) { var r = f(v); return r && r.then ? r : settled(r); },"
    " catch: function() { return this; } }; }"
    " function fetch() { return settled({ ok: false,"
    " json: function() { return settled({ error: 'At most 32 lines.' }); } }); }"
)


def test_a_failed_fit_leaves_no_earlier_fit_standing() -> None:
    out = run_wizard_js(
        "(function() { runLensFit(); return [lensFit, shown, status]; })()",
        functions=("runLensFit",),
        prelude=_SYNC_FETCH
        + " var lensFitSeq = 0, imageWidth = 1920, imageHeight = 1080, shown = 'unset', status = null;"
        " var lensFit = { k1: -0.2, k2: 0, rating: 'good' };"
        " var lensLines = [{ points: 5 }];"
        " function lensLineCounts() { return true; } function lensActivePoints() { return []; }"
        " function lensShowStatus(text, ok) { status = [text, ok]; }"
        " function renderLensResult() { shown = lensFit; } function renderLens() {} function saveToSession() {}",
    )
    assert out == [None, None, ["At most 32 lines.", False]]


def test_the_corners_a_solve_records_are_the_ones_it_sent() -> None:
    out = run_wizard_js(
        "(function() { var sent = wizSolveCorners(pos); pos.DSL[0] -= 10; return sent; })()",
        functions=("wizSolveCorners",),
        variables=("CORNER_NAMES",),
        pos={"DSL": [1, 2], "DSR": [3, 4], "USR": [5, 6], "USL": [7, 8]},
    )
    assert out == [[1, 2], [3, 4], [5, 6], [7, 8]]


def test_a_corner_nudge_drops_the_answer_to_a_solve_in_flight() -> None:
    out = run_wizard_js(
        "(function() { setupCornerDragging(container); var before = poseSeq;"
        " listeners.keydown({ key: 'ArrowLeft', shiftKey: false, target: handle, preventDefault: function() {} });"
        " return [poseSeq !== before, cornerPositions.DSL]; })()",
        functions=("setupCornerDragging",),
        prelude="var poseSeq = 7, arrowDebounceTimer = null, cornerPositions = { DSL: [100, 50] };"
        " var listeners = {}; var container = { addEventListener: function(n, f) { listeners[n] = f; } };"
        " var handle = { dataset: { corner: 'DSL' }, setAttribute: function() {},"
        " closest: function() { return handle; } };"
        " var document = { getElementById: function() { return {}; } };"
        " var window = { addEventListener: function() {} };"
        " function setTimeout() { return 1; } function clearTimeout() {}"
        " function updateFineQuad() {} function refreshFineZoomNeighbourhood() {} function solveFromCorners() {}",
    )
    assert out == [True, [99, 50]]


@pytest.mark.parametrize(
    ("key", "shows"),
    [
        ("prep", False),
        ("grid", False),
        ("video", False),
        ("lens", True),
        ("camera", False),
        ("ref", True),
        ("corners", True),
        ("review", True),
    ],
)
def test_only_a_step_that_shows_the_snapshot_loads_it(key: str, shows: bool) -> None:
    steps = ["prep", "grid", "video", "lens", "camera", "ref", "corners", "review"]
    out = run_wizard_js(
        "wizStepNeedsSnapshot(key, WIZ)",
        functions=("wizStepNeedsSnapshot",),
        key=key,
        WIZ={k: i for i, k in enumerate(steps)},
    )
    assert out is shows
