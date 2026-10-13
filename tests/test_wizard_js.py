# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The setup wizard's script maths, run in node and held against the Python it mirrors."""

from __future__ import annotations

import json
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
from tests._wizard_js import length_page, needs_node, run_wizard_js, wizard_function, wizard_window_function

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


_POINT = {"corner_a": "DSR", "corner_b": "DSL", "distance_a": 18.5, "distance_b": 19.25, "height": 0.0}


@pytest.mark.parametrize(
    "change",
    [
        {},
        {"corner_b": "DSR"},
        {"corner_a": None},
        {"corner_b": ""},
        {"distance_a": 0.0},
        {"distance_b": -1.0},
        {"distance_a": 200.0},
        {"distance_b": 200.01},
        {"distance_a": None},
        {"height": 10.0},
        {"height": -10.0},
        {"height": 10.01},
        {"height": -10.5},
        {"height": None},
    ],
)
def test_a_point_the_page_sends_is_one_the_server_takes(change: dict) -> None:
    from openfollow.configuration import CalibrationPointConfig

    point = {**_POINT, **change}
    complete = run_wizard_js(
        "calibComplete(point)",
        functions=(
            "calibNumber",
            "calibComplete",
        ),
        point=point,
    )
    # The page's points always carry a pin; the server needs one too.
    server = CalibrationPointConfig(**{k: (v if v is not None else "") for k, v in point.items()}, pin_x=0.5, pin_y=0.5)
    assert complete is (server.problem() == "")


def test_each_answer_lands_on_the_point_it_answers() -> None:
    # The incomplete second point is not sent, so the server's answers skip it.
    points = [
        {**_POINT, "pin": [0.25, 0.5]},
        {**_POINT, "distance_b": None, "pin": [0.5, 0.5]},
        {**_POINT, "corner_a": "USR", "pin": [0.75, 0.25]},
    ]
    sent, answers = run_wizard_js(
        "(function() { var sent = calibPayload(); calibTakeAnswer(sent.indices, ['first', 'third']);"
        " return [sent, calibAnswers]; })()",
        functions=("calibNumber", "calibComplete", "calibPinPx", "calibPayload", "calibTakeAnswer"),
        prelude="function renderCalibStatus() {} function renderCalibOverlay() {}",
        calibPoints=points,
        calibAnswers=[None, None, None],
        imageWidth=1920,
        imageHeight=1080,
    )
    assert sent["indices"] == [0, 2]
    assert [p["pin"] for p in sent["points"]] == [[480, 540], [1440, 270]]
    assert answers == ["first", None, "third"]


def test_a_saved_point_is_restored_only_in_the_shape_the_page_keeps() -> None:
    good = {**_POINT, "pin": [0.4, 0.2], "pinned": True, "position": [2.5, -14.0, 0.0]}
    restored = run_wizard_js(
        "points.map(calibRestored)",
        functions=("calibNumber", "calibRestored"),
        variables=("CORNER_NAMES",),
        points=[
            good,
            None,
            {**good, "pin": [0.4]},
            {**good, "pin": [1.5, 0.2]},
            {**good, "pin": ["a", 0.2]},
            {**good, "corner_a": "XYZ"},
            {**good, "distance_a": "18"},
            {**good, "height": None},
            {**good, "position": []},
            {**good, "position": [2.5, -14.0]},
            {**good, "position": [2.5, "x", 0.0]},
            {**good, "position": "2.5, -14, 0"},
            {**good, "pinned": False},
            {k: v for k, v in good.items() if k != "pinned"},
        ],
    )
    assert restored[0] == good
    assert restored[1:5] == [None, None, None, None]
    assert restored[5]["corner_a"] is None
    assert restored[6]["distance_a"] is None
    assert restored[7]["height"] is None
    assert [r["position"] for r in restored[8:12]] == [None] * 4
    # A dot never moved stays unplaced across a reload; a saved point (no flag) was placed.
    assert [r["pinned"] for r in restored[12:]] == [False, True]


_FAKE_SVG = """
function fakeEl(tag) {
  return { tag: tag, attrs: {}, children: [], textContent: '',
           setAttribute: function(k, v) { this.attrs[k] = String(v); },
           appendChild: function(c) { this.children.push(c); } };
}
var reviewGroup = fakeEl('g');
Object.defineProperty(reviewGroup, 'innerHTML', { set: function() { this.children = []; } });
var document = {
  createElementNS: function(_ns, tag) { return fakeEl(tag); },
  getElementById: function(id) { return id === 'review-calib-points' ? reviewGroup : null; },
};
"""


def test_review_draws_where_the_solved_camera_sees_each_point() -> None:
    drawn = run_wizard_js(
        "(function() { renderReviewCalib(2); renderReviewCalib(2); return reviewGroup.children.map(function(c) {"
        " return [c.tag, c.attrs.class, c.attrs.transform || null, c.attrs.cx || null, c.attrs.cy || null,"
        " c.children.map(function(k) { return k.textContent; }).join('')]; }); })()",
        functions=("svgEl", "pointMarker", "renderReviewCalib"),
        prelude=_FAKE_SVG,
        calibAnswers=[
            {"status": "ok", "seen": [100, 200]},
            None,
            {"status": "flagged", "seen": [300, 400]},
            {"status": "flagged", "seen": None},
            {"status": "unverified", "seen": [500, 600]},
        ],
    )
    assert drawn == [
        ["g", "wizard-point", "translate(100,200)", None, None, "1"],
        ["circle", "calib-miss", None, "300", "400", ""],
        ["g", "wizard-point", "translate(500,600)", None, None, "5"],
    ]


def test_apply_saves_each_complete_point_with_where_the_solve_of_this_pose_put_it() -> None:
    points = [
        {**_POINT, "pin": [0.25, 0.5], "position": [2.5, 9.0, 0.0]},
        {**_POINT, "distance_b": None, "pin": [0.5, 0.5], "position": [1, 1, 1]},
        {**_POINT, "corner_a": "USR", "height": 1.2, "pin": [0.75, 0.25], "position": None},
    ]
    saved = run_wizard_js("calibSaved()", functions=("calibNumber", "calibComplete", "calibSaved"), calibPoints=points)
    common = {"corner_b": "DSL", "distance_a": 18.5, "distance_b": 19.25}
    assert saved == [
        {**common, "corner_a": "DSR", "height": 0.0, "pin_x": 0.25, "pin_y": 0.5, "position": [2.5, 9.0, 0.0]},
        {**common, "corner_a": "USR", "height": 1.2, "pin_x": 0.75, "pin_y": 0.25, "position": []},
    ]


_NO_CALIB_DRAWING = "function renderCalibStatus() {} function renderCalibOverlay() {}"


def test_a_solve_gives_each_point_it_took_its_place_and_the_rest_none() -> None:
    """A left-out point's place is where its distances put it, not where the solve did: it keeps none."""
    points = [
        {**_POINT, "pin": [0.25, 0.5], "position": [9, 9, 9]},
        {**_POINT, "distance_b": None, "pin": [0.5, 0.5], "position": [8, 8, 8]},
        {**_POINT, "corner_a": "USR", "pin": [0.75, 0.25], "position": [7, 7, 7]},
    ]
    positions = run_wizard_js(
        "(function() { calibTakeAnswer([0, 2], [{status: 'ok', position: [1, 2, 0]}, {status: 'flagged',"
        " position: [5, 5, 0]}]); return calibPoints.map(function(p) { return p.position; }); })()",
        functions=("calibTakeAnswer",),
        prelude=_NO_CALIB_DRAWING,
        calibPoints=points,
        calibAnswers=[None, None, None],
    )
    assert positions == [[1, 2, 0], None, None]


_POSE_PAGE = (
    _NO_CALIB_DRAWING
    + """
var poseSeq = 0, lensSolvedWith = null, pinnedCorners = null, currentStep = 0, WIZ = { review: 7 }, CAM_LEN_IDS = [];
var calibHeldPins = { size: [1920, 1080], world: [], corners: [] };
function wizardApplySolvedCamera() {} function saveToSession() {} function showSolvedParams() {}
function populateReview() {} function updateCamIllustration() {} function refreshLengthEchoes() {}
var els = { 'fine-solved-params': { style: { display: 'none' } }, cam_pitch: { value: '-22' },
            'cam-pitch-error': { style: {} } };
var document = { getElementById: function(id) { return els[id] || null; } };
"""
)


@pytest.mark.parametrize(
    "change",
    [
        "restorePose({ camera: {}, lensSolvedWith: { k1: 0, k2: 0 }, pins: null })",
        "applySolvedPose({}, [[0, 0], [1, 0], [1, 1], [0, 1]], { image_width: 1920, image_height: 1080,"
        " camera: { lens_k1: 0, lens_k2: 0 } }, undefined)",
        "onCamInputChanged()",
    ],
    ids=["a reset", "a solve without the points", "a camera field edit"],
)
def test_a_pose_the_points_were_not_solved_with_leaves_them_no_answer_and_no_place(change: str) -> None:
    answers, positions, held = run_wizard_js(
        f"(function() {{ {change}; return [calibAnswers, calibPoints.map(function(p) {{ return p.position; }}),"
        " calibHeldPins]; })()",
        functions=(
            "calibTakeAnswer",
            "calibForgetPose",
            "restorePose",
            "applySolvedPose",
            "onCamInputChanged",
        ),
        prelude=_POSE_PAGE,
        calibPoints=[{**_POINT, "pin": [0.25, 0.5], "position": [2.5, 9.0, 0.0]}],
        calibAnswers=[{"status": "ok", "position": [2.5, 9.0, 0.0]}],
    )
    assert (answers, positions, held) == ([None], [None], None)


def test_a_reset_brings_back_the_corners_its_pose_was_solved_from() -> None:
    held = run_wizard_js(
        "(function() { restorePose({ camera: {}, lensSolvedWith: null, pins: null, held: snap });"
        " return calibHeldPins; })()",
        functions=("calibTakeAnswer", "calibForgetPose", "restorePose"),
        prelude=_POSE_PAGE,
        calibPoints=[],
        calibAnswers=[],
        snap={"size": [1920, 1080], "world": [], "corners": [[1, 2]]},
    )
    assert held == {"size": [1920, 1080], "world": [], "corners": [[1, 2]]}


def test_a_solve_while_points_exist_holds_the_corners_it_was_solved_from() -> None:
    held = run_wizard_js(
        "(function() { var body = { image_width: 1920, image_height: 1080, world_corners: world,"
        " camera: { lens_k1: 0, lens_k2: 0 } }; Object.defineProperty(body, 'calibHold', { value: true });"
        " applySolvedPose({}, corners, body, []); return calibHeldPins; })()",
        functions=("calibTakeAnswer", "applySolvedPose"),
        prelude=_POSE_PAGE,
        calibPoints=[],
        calibAnswers=[],
        corners=[[1, 2], [3, 4], [5, 6], [7, 8]],
        world=[[5, 0, 0], [-5, 0, 0], [-5, 6, 0], [5, 6, 0]],
    )
    # Fractions of the snapshot the pins were placed on, so a snapshot of another resolution maps too.
    assert held == {
        "world": [[5, 0, 0], [-5, 0, 0], [-5, 6, 0], [5, 6, 0]],
        "corners": [[k / 1920, (k + 1) / 1080] for k in (1, 3, 5, 7)],
    }


_SOLVE_PAGE = """
function answered(value) { return { then: function(f) { return answered(f(value)); }, catch: function() {} }; }
var sent = 0, landed = 0, poseSeq = 0, preCornerPinningPose = null;
function fetch(_url, _opts) { sent++; return answered({ json: function() { return reply; } }); }
function invalidLengthFields() { return []; } function snapshotPose() { return {}; }
function showSolveStatus() {} function showSolvedParams() {} function updateFineQuad() {}
function renderAllFineZoomBoxes() {} function applySolvedPose(c, s, b, answers) { landed++; handed = answers; }
var handed = null;
var document = { getElementById: function() { return { querySelectorAll: function() { return []; } }; } };
"""
_CORNERS_PINNED = {"DSL": [1500, 900], "DSR": [400, 900], "USR": [600, 500], "USL": [1300, 500]}


@pytest.mark.parametrize("hold", [False, True])
def test_the_corners_snap_to_a_solve_only_while_there_are_no_points(hold: bool) -> None:
    """A fit with calibration points is least squares: snapped, its corners would be the next solve's pins.
    A point made incomplete for a moment leaves a corners-only solve, which must not snap them either."""
    reprojected = [[1501, 901], [401, 899], [601, 501], [1299, 499]]
    snapped = not hold
    corners, landed, handed = run_wizard_js(
        "(function() { solveFromCorners(); return [cornerPositions, landed, handed]; })()",
        functions=("isConvex", "quadArea", "wizSolveCorners", "solveFromCorners"),
        variables=("CORNER_NAMES",),
        prelude=_SOLVE_PAGE
        + "function wizardSolveBody(sc) { var b = { screen_corners: sc };"
        + f" Object.defineProperty(b, 'calibHold', {{ value: {json.dumps(hold)} }}); return b; }}",
        cornerPositions=_CORNERS_PINNED,
        reply={"camera": {}, "reprojected_corners": reprojected, "calibration_points": ["answer"]},
    )
    assert (landed, handed) == (1, ["answer"])
    expected = dict(zip(("DSL", "DSR", "USR", "USL"), reprojected, strict=True)) if snapped else _CORNERS_PINNED
    assert corners == expected


def test_a_point_asking_for_a_solve_before_the_corners_are_placed_sends_nothing() -> None:
    sent = run_wizard_js(
        "(function() { solveFromCorners(); return sent; })()",
        functions=("isConvex", "quadArea", "wizSolveCorners", "solveFromCorners"),
        variables=("CORNER_NAMES",),
        prelude=_SOLVE_PAGE + "function wizardSolveBody(sc) { return { screen_corners: sc }; }",
        cornerPositions={"DSL": None, "DSR": None, "USR": None, "USL": None},
        reply={},
    )
    assert sent == 0


_DRAG_PAGE = """
function fakeEl(tag) {
  var el = { tag: tag, attrs: {}, children: [], parent: null, dataset: {}, textContent: '', listeners: {},
    setAttribute: function(k, v) { this.attrs[k] = String(v); },
    appendChild: function(c) { c.parent = this; this.children.push(c); },
    addEventListener: function(type, fn) { this.listeners[type] = fn; },
    contains: function() { return false; },
    focus: function() {},
    closest: function(sel) {
      for (var e = this; e; e = e.parent) if ((e.attrs.class || '').split(' ').indexOf(sel.slice(1)) !== -1) return e;
      return null;
    } };
  Object.defineProperty(el, 'innerHTML', { set: function() {
    this.children.forEach(function(c) { c.parent = null; });
    this.children = [];
  } });
  return el;
}
var calibGroup = fakeEl('g'), overlay = fakeEl('svg');
overlay.classList = { toggle: function() {} };
overlay.createSVGPoint = function() {
  return { x: 0, y: 0, matrixTransform: function() { return { x: this.x, y: this.y }; } };
};
overlay.getScreenCTM = function() { return { inverse: function() { return null; } }; };
var document = { activeElement: null, createElementNS: function(_ns, tag) { return fakeEl(tag); },
  getElementById: function(id) { return { 'fine-calib': calibGroup, 'fine-overlay': overlay }[id] || null; } };
var windowListeners = {};
var window = { addEventListener: function(type, fn) { windowListeners[type] = fn; } };
var poseSeq = 0, calibAwaiting = null, calibDrawn = [], calibDrag = null, CALIB_ENABLED = true;
var imageWidth = 1920, imageHeight = 1080, calibSolveTimer = 'pending', cleared = [], solves = 0, listed = 0;
function clearTimeout(t) { cleared.push(t); }
function showLoupe() {} function hideLoupe() {} function overlayPx() { return 1; } function renderReviewCalib() {}
function saveToSession() {} function renderCalibStatus() {} function renderCalibList() { listed++; }
function solveFromCorners() { solves++; }
function calibScheduleSolve() {} function wizReadLen(id) { return lengths[id]; }
function touch(target, x, y) {
  return { target: target, touches: [{ clientX: x, clientY: y }], preventDefault: function() {} };
}
"""
_DRAG_FUNCTIONS = (
    "calibPicking",
    "svgEl",
    "pointHandle",
    "calibPinPx",
    "calibSetPin",
    "calibEdited",
    "calibPlaced",
    "calibTakeAnswer",
    "renderCalibOverlay",
    "calibMoveDrawn",
    "setupCalibDragging",
    "calibOnField",
)
_DRAG_INPUTS = {
    "calibPoints": [{**_POINT, "pin": [0.25, 0.5], "position": [2.5, 9.0, 0.0]}],
    "calibAnswers": [{"status": "flagged", "seen": [700, 600]}],
    "cornerPositions": _CORNERS_PINNED,
    "lengths": {},
}


def test_a_touch_drag_keeps_the_point_it_began_on_on_the_page() -> None:
    """A touch goes on to the element it began on; once that element is gone it reaches nothing else.
    A solve answer landing mid-drag must not draw the points again either."""
    attached, transform, lines, cleared = run_wizard_js(
        """(function() {
          renderCalibOverlay();
          setupCalibDragging();
          var handle = calibDrawn[0].handle, dot = handle.children[2], attached = [];
          function touch(x, y) { return { target: dot, touches: [{ clientX: x, clientY: y }],
                                          preventDefault: function() {} }; }
          calibGroup.listeners.touchstart(touch(480, 540));
          [[490, 545], [500, 550]].forEach(function(at) {
            if (handle.parent === calibGroup) windowListeners.touchmove(touch(at[0], at[1]));
            calibTakeAnswer([0], [{ status: 'ok', position: [1, 2, 0], seen: [480, 540] }]);
            attached.push(handle.parent === calibGroup);
          });
          return [attached, handle.attrs.transform, calibDrawn[0].lines.map(function(l) {
            return [l.attrs.class, l.attrs.x1, l.attrs.y1]; }), cleared];
        })()""",
        functions=_DRAG_FUNCTIONS,
        prelude=_DRAG_PAGE,
        **_DRAG_INPUTS,
    )
    assert attached == [True, True]
    assert [float(v) for v in transform[len("translate(") : -1].split(",")] == pytest.approx([500, 550])
    assert [cls for cls, _x, _y in lines] == ["calib-guide", "calib-guide", "calib-miss"]
    assert [float(v) for _cls, x, y in lines for v in (x, y)] == pytest.approx([500, 550] * 3)
    assert cleared == ["pending"]  # a solve still to come would read the pin half-dragged


def test_a_field_edit_takes_the_left_out_ring_off_the_picture() -> None:
    drawn = run_wizard_js(
        """(function() {
          renderCalibOverlay();
          var before = calibGroup.children.map(function(c) { return c.attrs.class; });
          calibOnField(0);
          return [before, calibGroup.children.map(function(c) { return c.attrs.class; })];
        })()""",
        functions=_DRAG_FUNCTIONS,
        prelude=_DRAG_PAGE,
        **{**_DRAG_INPUTS, "lengths": {"calib-a-0": 18.0, "calib-b-0": 19.25, "calib-h-0": 0.0}},
    )
    assert drawn == [
        ["calib-guide", "calib-guide", "calib-miss", "calib-miss", "handle wizard-point"],
        ["calib-guide", "calib-guide", "handle wizard-point"],
    ]


def test_a_keyboard_nudge_drops_what_the_last_solve_said_of_the_point() -> None:
    answer, position, seq, pin = run_wizard_js(
        """(function() {
          renderCalibOverlay();
          setupCalibDragging();
          var handle = calibDrawn[0].handle;
          calibGroup.listeners.keydown({ target: handle, key: 'ArrowRight', shiftKey: true,
                                         preventDefault: function() {} });
          return [calibAnswers[0], calibPoints[0].position, poseSeq, calibPinPx(calibPoints[0])];
        })()""",
        functions=_DRAG_FUNCTIONS,
        prelude=_DRAG_PAGE,
        **_DRAG_INPUTS,
    )
    assert (answer, position, seq) == (None, None, 1)
    assert pin == pytest.approx([490, 540])


def test_the_points_are_drawn_to_the_corners_the_projection_just_placed_and_not_solved() -> None:
    """A projection is no measurement: solving from it would move the pose towards the points on every visit."""
    drawn, scheduled = run_wizard_js(
        "(function() { updateAllOverlays({ corners: corners, reference: [0, 0] }); return [drawn, scheduled]; })()",
        functions=("updateAllOverlays", "calibHeldFor", "calibHeldCorners"),
        variables=("CORNER_NAMES",),
        prelude="""
var drawn = [], scheduled = 0, imageWidth = 1920, imageHeight = 1080, fineBowedEdges = null, calibHeldPins = null;
function wizardWorldCorners() { return []; } function updateFineQuad() {}
var CALIB_ENABLED = true, currentStep = 5, WIZ = { corners: 5 }, calibPoints = [{}], calibAnswers = [null];
var stub = { setAttribute: function() {}, set innerHTML(_v) {} };
var document = { getElementById: function() { return stub; } };
function record() { drawn.push(JSON.parse(JSON.stringify(cornerPositions))); }
function renderCalibOverlay() { record(); } function calibScheduleSolve() { scheduled++; }
function calibComplete() { return true; } function renderCalibStatus() {}
function renderGridLines() {} function renderCornerMarkers() {} function renderRefMarker() {}
function renderZOffsetLine() {} function buildBowedEdges() {} function renderAllFineZoomBoxes() {}
function updateFineZoomToggleEnabled() {} function recenterCoarseZoomIfNeeded() {} function renderCoarseZoom() {}
function updateCoarseZoomToggleEnabled() {}
""",
        cornerPositions={"DSL": None, "DSR": None, "USR": None, "USL": None},
        corners=_CORNERS_PINNED,
    )
    assert (drawn, scheduled) == ([_CORNERS_PINNED], 0)


@pytest.mark.parametrize(
    ("clicks", "taps", "expected", "solves"),
    [
        (1, ["DSL", "DSR"], ("DSL", 19.25, "DSR", 18.5), 1),
        (1, ["DSR", "DSL"], ("DSR", 18.5, "DSL", 19.25), 1),
        (1, ["USR", "DSL"], ("USR", None, "DSL", 19.25), 0),
        (2, ["DSL", "DSR"], ("DSL", 19.25, "DSR", 18.5), 1),  # Choose corners clicked twice
    ],
)
def test_each_distance_stays_with_its_corner_when_the_corners_are_chosen_again(
    clicks: int, taps: list[str], expected: tuple, solves: int
) -> None:
    during, point, solved, kept = run_wizard_js(
        """(function() {
          for (var k = 0; k < clicks; k++) calibChooseCorners(0);
          var during = [calibPoints[0].distance_a, calibPoints[0].distance_b, Object.keys(calibPoints[0])];
          taps.forEach(calibPickCorner);
          return [during, calibPoints[0], solved, Object.keys(calibPoints[0])];
        })()""",
        functions=("calibNumber", "calibComplete", "calibEdited", "calibChooseCorners", "calibPickCorner"),
        prelude="""
var solved = 0, poseSeq = 0, calibAwaiting = null, calibSolveTimer = null;
function saveToSession() {} function renderCalibList() {} function renderCalibOverlay() {} function setFineZoomMode() {}
function calibScheduleSolve() {} function solveFromCorners() { solved++; }
""",
        calibPoints=[{**_POINT, "pin": [0.25, 0.5], "position": [2.5, 9.0, 0.0]}],
        calibAnswers=[{"status": "ok"}],
        clicks=clicks,
        taps=taps,
    )
    # What remembers the distances is never saved with the point.
    fields = ["corner_a", "corner_b", "distance_a", "distance_b", "height", "pin", "position"]
    assert during == [None, None, fields] and kept == fields
    assert (point["corner_a"], point["distance_a"], point["corner_b"], point["distance_b"]) == expected
    assert solved == solves


def test_the_reference_mapping_solve_leaves_the_points_out() -> None:
    """Its corners are a rigid shift, no perspective image: the points would be judged against that error."""
    sent = run_wizard_js(
        "(function() { applyCoarseOffset([0, 0], [10, 5], corners); return sent; })()",
        functions=(
            "isConvex",
            "quadArea",
            "calibNumber",
            "calibComplete",
            "calibPinPx",
            "calibPayload",
            "wizSolveCorners",
            "wizardSolveBody",
            "applyCoarseOffset",
        ),
        variables=("CORNER_NAMES",),
        prelude="""
var sent = [], poseSeq = 0, CALIB_ENABLED = true, imageWidth = 1920, imageHeight = 1080, WIZ_SOLVE_PAGE = 'p';
var cornerPositions = { DSL: null, DSR: null, USR: null, USL: null };
function wizardWorldCorners() { return []; } function wizReadLensCoeff() { return 0; }
function fetch(_url, opts) { sent.push(JSON.parse(opts.body)); return { then: function() { return this; } }; }
""",
        corners=_CORNERS_PINNED,
        calibPoints=[{**_POINT, "pin": [0.25, 0.5]}],
    )
    assert len(sent) == 1 and "calibration_points" not in sent[0]
    assert sent[0]["screen_corners"][0] == [1510, 905]


def test_a_complete_point_without_an_answer_reads_not_solved() -> None:
    chips = run_wizard_js(
        "(function() { renderCalibStatus(); return [els['calib-chip-0'].textContent, els['calib-chip-1'].textContent,"
        " els['calib-fit-0'].textContent]; })()",
        functions=("calibNumber", "calibComplete", "calibPositionText", "renderCalibStatus"),
        prelude=length_page("metric", {})
        + """
var els = {}; ['calib-list', 'calib-add', 'calib-chip-0', 'calib-fit-0', 'calib-reason-0', 'calib-chip-1',
  'calib-fit-1', 'calib-reason-1'].forEach(function(id) { els[id] = { style: {}, textContent: '' }; });
els['calib-notice'] = { style: {} };
els['calib-notice-step'] = { style: {} };
document.getElementById = function(id) { return els[id] || null; };
var CALIB_MAX = 6, imageWidth = 1920, CALIB_STATES = {};
""",
        calibPoints=[{**_POINT, "pin": [0.25, 0.5], "position": [2.5, 9.0, 0.0]}, {**_POINT, "distance_a": None}],
        calibAnswers=[None, None],
    )
    assert chips == ["Not solved", "Incomplete", "X 2.500 m  Y 9.000 m  Z 0.000 m"]


_HELD_PAGE = """
var imageWidth = 1920, imageHeight = 1080, fineBowedEdges = null, CALIB_ENABLED = true, currentStep = 5;
var WIZ = { corners: 5 }, calibPoints = [], calibAnswers = [], fine = [], quads = 0;
var stub = { setAttribute: function() {}, set innerHTML(_v) {} };
var document = { getElementById: function() { return stub; } };
function wizardWorldCorners() { return worldNow; }
function renderCornerMarkers(id, corners) { if (id === 'fine-corners') fine.push(JSON.parse(JSON.stringify(corners))); }
function updateFineQuad() { quads++; }
function renderCalibOverlay() {} function renderCalibStatus() {} function calibScheduleSolve() {}
function renderGridLines() {} function renderRefMarker() {} function renderZOffsetLine() {}
function buildBowedEdges() {} function renderAllFineZoomBoxes() {} function updateFineZoomToggleEnabled() {}
function recenterCoarseZoomIfNeeded() {} function renderCoarseZoom() {} function updateCoarseZoomToggleEnabled() {}
"""
_WORLD = [[5, 0, 0], [-5, 0, 0], [-5, 6, 0], [5, 6, 0]]
_PINS = [[1500, 900], [400, 900], [600, 500], [1300, 500]]
_FRACTIONS = [[x / 1920, y / 1080] for x, y in _PINS]
_PROJECTED = {"DSL": [1502, 901], "DSR": [398, 899], "USR": [601, 502], "USL": [1299, 498]}


@pytest.mark.parametrize(
    ("held", "world_now", "scale", "enabled", "on_pins"),
    [
        ({"world": _WORLD, "corners": _FRACTIONS}, _WORLD, 1, True, True),
        ({"world": _WORLD, "corners": _FRACTIONS}, _WORLD, 2, True, True),  # a 4K snapshot of the same camera
        ({"world": _WORLD, "corners": _FRACTIONS}, [[6, 0, 0], *_WORLD[1:]], 1, True, False),  # grid edited
        ({"world": _WORLD, "corners": _FRACTIONS}, _WORLD, 1, False, False),  # the section hidden
        (None, _WORLD, 1, True, False),
    ],
)
def test_a_projection_keeps_the_corner_handles_on_the_pins_a_fit_with_points_used(
    held, world_now, scale, enabled, on_pins
) -> None:
    """Its projection is least squares: taken as the corners, the next solve would read it as the pins."""
    corners, fine, quads = run_wizard_js(
        "(function() { updateAllOverlays({ corners: projected, reference: [0, 0] });"
        " return [cornerPositions, fine, quads]; })()",
        functions=("updateAllOverlays", "calibHeldFor", "calibHeldCorners"),
        variables=("CORNER_NAMES",),
        prelude=_HELD_PAGE
        + f"imageWidth = {1920 * scale}; imageHeight = {1080 * scale}; CALIB_ENABLED = {json.dumps(enabled)};",
        cornerPositions={"DSL": None, "DSR": None, "USR": None, "USL": None},
        projected=_PROJECTED,
        calibHeldPins=held,
        worldNow=world_now,
    )
    pins = {k: [v * scale for v in p] for k, p in zip(("DSL", "DSR", "USR", "USL"), _PINS, strict=True)}
    assert corners == (pins if on_pins else _PROJECTED)
    assert fine[-1] == corners and quads == (1 if on_pins else 0)


@pytest.mark.parametrize(("open_", "zoom", "picked"), [(True, False, ["DSR"]), (False, False, []), (True, True, [])])
def test_corners_are_picked_for_a_point_only_while_its_section_is_open(open_: bool, zoom: bool, picked: list) -> None:
    """Closed, the prompt that asks for them is hidden, so the corners drag as usual; in Fine adjust the
    boxes drag them too."""
    got, stopped = run_wizard_js(
        """(function() {
          setupCalibCornerPicking();
          var handle = { dataset: { corner: 'DSR' } };
          var e = { target: { closest: function() { return handle; } }, preventDefault: function() {},
                    stopImmediatePropagation: function() { this.stopped = true; } };
          group.listeners.mousedown(e);
          return [picked, !!e.stopped];
        })()""",
        functions=("calibPicking", "setupCalibCornerPicking"),
        prelude=f"""
var picked = [], calibAwaiting = 0, fineZoomMode = {json.dumps(zoom)};
function listening() {{ return {{ listeners: {{}}, addEventListener: function(t, f) {{ this.listeners[t] = f; }} }}; }}
var group = listening(), section = listening();
section.open = {json.dumps(open_)};
var byId = {{ 'fine-corners': group, 'calib-section': section }};
var document = {{ getElementById: function(id) {{ return byId[id]; }} }};
function calibPickCorner(name) {{ picked.push(name); }} function renderCalibOverlay() {{}}
""",
    )
    assert (got, stopped) == (picked, bool(picked))


@pytest.mark.parametrize("start", ["add", "choose"])
def test_waiting_for_corners_shows_the_full_view_where_they_are_tapped(start: str) -> None:
    zoom = run_wizard_js(
        "(function() { if (start === 'add') window.calibAdd(); else calibChooseCorners(0); return zoom; })()",
        functions=("calibEdited", "calibChooseCorners"),
        prelude="""
var zoom = [], poseSeq = 0, calibAwaiting = null, CALIB_MAX = 6, imageWidth = 1920, window = {};
function setFineZoomMode(on) { zoom.push(on); }
function saveToSession() {} function renderCalibList() {} function renderCalibOverlay() {}
function calibScheduleSolve() {}
"""
        + wizard_function("calibAdd", prefix="window."),
        calibPoints=[{**_POINT, "pin": [0.25, 0.5], "position": None}],
        calibAnswers=[None],
        start=start,
    )
    assert zoom == [False]


@pytest.mark.parametrize(("size", "seen"), [((1920, 1080), [700, 600]), ((3840, 2160), None)])
def test_a_snapshot_of_another_size_drops_where_the_last_solve_saw_each_point(size: tuple, seen: list | None) -> None:
    """``seen`` is in the pixels of the snapshot it was solved on; drawn on another it would be off by the scale."""
    answers = run_wizard_js(
        "(function() { window.loadSnapshot(); return calibAnswers; })()",
        functions=("calibDropSeen",),
        prelude=f"""
function answered(value) {{ return {{ then: function(f) {{ return answered(f(value)); }}, catch: function() {{}} }}; }}
function fetch() {{ return answered({{ ok: true, blob: function() {{ return 'blob'; }} }}); }}
var URL = {{ createObjectURL: function() {{ return 'url'; }}, revokeObjectURL: function() {{}} }};
function Image() {{}}
Object.defineProperty(Image.prototype, 'src', {{ set: function() {{
  this.naturalWidth = {size[0]}; this.naturalHeight = {size[1]}; this.onload(); }} }});
var window = {{}}, imageWidth = 1920, imageHeight = 1080, snapshotUrl = null;
function showSnapshotOnCurrentStep() {{}} function projectAndOverlay() {{}} function revokeSnapshotUrl() {{}}
function showNoFeed() {{}} function setTimeout() {{}}
"""
        + wizard_function("loadSnapshot", prefix="window."),
        calibAnswers=[{"status": "flagged", "seen": [700, 600]}, None],
    )
    assert answers == [{"status": "flagged", "seen": seen}, None]


_GOOD_HELD = {"world": _WORLD, "corners": _FRACTIONS}


@pytest.mark.parametrize(
    "stored",
    [
        None,
        "pins",
        {**_GOOD_HELD, "corners": _FRACTIONS[:3]},
        {**_GOOD_HELD, "world": _WORLD[:3]},
        {**_GOOD_HELD, "world": [[5, 0], *_WORLD[1:]]},
        {**_GOOD_HELD, "corners": [*_PINS[:3], [1, "x"]]},
        {k: v for k, v in _GOOD_HELD.items() if k != "corners"},
    ],
)
def test_held_corners_come_back_from_the_session_only_in_the_shape_the_page_keeps(stored) -> None:
    restored = run_wizard_js(
        "[calibHeldRestored(good), calibHeldRestored(stored)]",
        functions=("calibNumber", "calibHeldRestored"),
        good=_GOOD_HELD,
        stored=stored,
    )
    assert restored == [_GOOD_HELD, None]


def test_reference_mapping_shifts_the_projection_not_the_held_corners() -> None:
    corners = run_wizard_js(
        "(function() { updateAllOverlays({ corners: projected, reference: [0, 0] }); return cornerPositions; })()",
        functions=("updateAllOverlays", "calibHeldFor", "calibHeldCorners"),
        variables=("CORNER_NAMES",),
        prelude=_HELD_PAGE + "WIZ.ref = 4; currentStep = 4;",
        cornerPositions={"DSL": None, "DSR": None, "USR": None, "USL": None},
        projected=_PROJECTED,
        calibHeldPins=_GOOD_HELD,
        worldNow=_WORLD,
    )
    assert corners == _PROJECTED


def test_held_corners_survive_a_reload_of_the_page() -> None:
    held = run_wizard_js(
        "(function() { calibHeldPins = good; saveToSession(); calibHeldPins = null; restoreFromSession();"
        " return calibHeldPins; })()",
        functions=(
            "lensRestoredPair",
            "calibNumber",
            "calibRestored",
            "calibLoad",
            "calibHeldRestored",
            "saveToSession",
            "restoreFromSession",
        ),
        variables=("STORAGE_KEY", "CORNER_NAMES"),
        prelude="""
var CALIB_ENABLED = true;
var calibHeldPins = null, calibPoints = [], calibAnswers = [], calibAwaiting = null, CALIB_MAX = 6;
var WIZ = {}, WIZ_STEPS = [], currentStep = 0, lensLines = null, lensImageSize = null, lensFit = null;
var pinnedCorners = null, lensSolvedWith = null, originalFov = 60;
function getState() { return {}; }
var store = {};
var sessionStorage = { getItem: function(k) { return store[k] || null; }, setItem: function(k, v) { store[k] = v; } };
""",
        good=_GOOD_HELD,
    )
    assert held == _GOOD_HELD


@pytest.mark.parametrize(("enabled", "expected"), [(True, 30.0), (False, 18.5)], ids=["shown", "hidden"])
def test_a_reload_with_the_points_hidden_keeps_the_saved_ones(enabled: bool, expected: float) -> None:
    """Apply writes the saved points while the section is hidden, never edits the operator cannot see."""
    distance = run_wizard_js(
        "(function() { calibPoints = [edited]; saveToSession(); calibPoints = [saved]; restoreFromSession();"
        " return calibPoints[0].distance_a; })()",
        functions=(
            "lensRestoredPair",
            "calibNumber",
            "calibRestored",
            "calibLoad",
            "calibHeldRestored",
            "saveToSession",
            "restoreFromSession",
        ),
        variables=("STORAGE_KEY", "CORNER_NAMES"),
        prelude=f"var CALIB_ENABLED = {json.dumps(enabled)};"
        + """
var calibHeldPins = null, calibPoints = [], calibAnswers = [], calibAwaiting = null, CALIB_MAX = 6;
var WIZ = {}, WIZ_STEPS = [], currentStep = 0, lensLines = null, lensImageSize = null, lensFit = null;
var pinnedCorners = null, lensSolvedWith = null, originalFov = 60;
function getState() { return {}; }
var store = {};
var sessionStorage = { getItem: function(k) { return store[k] || null; }, setItem: function(k, v) { store[k] = v; } };
""",
        saved={**_POINT, "pin": [0.25, 0.5]},
        edited={**_POINT, "distance_a": 30.0, "pin": [0.25, 0.5]},
    )
    assert distance == expected


def test_a_snapshot_of_the_pose_carries_the_corners_it_was_solved_from() -> None:
    held = run_wizard_js(
        "(function() { calibHeldPins = good; var snap = snapshotPose(); calibHeldPins = null; restorePose(snap);"
        " return calibHeldPins; })()",
        functions=("calibTakeAnswer", "calibForgetPose", "snapshotPose", "restorePose"),
        prelude=_POSE_PAGE + "function getState() { return { camera: {} }; }",
        calibPoints=[],
        calibAnswers=[],
        good=_GOOD_HELD,
    )
    assert held == _GOOD_HELD


def test_an_unreadable_height_leaves_the_point_incomplete() -> None:
    """Read as 0 m it would be solved at stage level, wherever the spot is."""
    height = run_wizard_js(
        "(function() { renderCalibOverlay(); calibOnField(0); return calibPoints[0].height; })()",
        functions=_DRAG_FUNCTIONS,
        prelude=_DRAG_PAGE,
        **{**_DRAG_INPUTS, "lengths": {"calib-a-0": 18.5, "calib-b-0": 19.25, "calib-h-0": float("nan")}},
    )
    assert height is None


@pytest.mark.parametrize(
    ("timer", "moves", "solves", "kept"),
    [
        (None, [], 0, True),  # a press that moved nothing changes nothing
        ("pending", [], 1, True),  # ... but runs the solve it held back
        (None, [[500, 550]], 1, False),
    ],
)
def test_a_point_press_solves_only_what_it_changed_or_held_back(timer, moves, solves, kept) -> None:
    result = run_wizard_js(
        """(function() {
          calibSolveTimer = timer;
          renderCalibOverlay();
          setupCalibDragging();
          var dot = calibDrawn[0].handle.children[2];
          calibGroup.listeners.touchstart(touch(dot, 480, 540));
          moves.forEach(function(at) { windowListeners.touchmove(touch(dot, at[0], at[1])); });
          windowListeners.touchend({});
          return [solves, calibAnswers[0] !== null, calibDrag];
        })()""",
        functions=_DRAG_FUNCTIONS,
        prelude=_DRAG_PAGE,
        **{**_DRAG_INPUTS, "timer": timer, "moves": moves},
    )
    assert result == [solves, kept, None]


def test_a_cancelled_touch_ends_the_point_drag() -> None:
    """Left set, nothing would draw the points again, and the next touch anywhere would move this one."""
    dragging = run_wizard_js(
        """(function() {
          renderCalibOverlay();
          setupCalibDragging();
          calibGroup.listeners.touchstart(touch(calibDrawn[0].handle.children[2], 480, 540));
          var during = calibDrag !== null;
          windowListeners.touchcancel({});
          return [during, calibDrag];
        })()""",
        functions=_DRAG_FUNCTIONS,
        prelude=_DRAG_PAGE,
        **_DRAG_INPUTS,
    )
    assert dragging == [True, None]


def test_a_corner_move_takes_the_guides_along_and_leaves_the_points_drawn() -> None:
    moved = run_wizard_js(
        """(function() {
          renderCalibOverlay();
          var handle = calibDrawn[0].handle;
          cornerPositions.DSR = [420, 905];
          updateFineQuad();
          var guides = calibDrawn[0].guides;
          return [handle.parent === calibGroup, guides.DSR.attrs.x2, guides.DSR.attrs.y2, guides.DSL.attrs.x2];
        })()""",
        functions=(*_DRAG_FUNCTIONS, "calibMoveGuides", "updateFineQuad"),
        prelude=_DRAG_PAGE
        + """
var fineBowedEdges = null, quadStub = { setAttribute: function() {} };
var pageById = document.getElementById;
document.getElementById = function(id) { return id === 'fine-quad' ? quadStub : pageById(id); };
function renderGridLines() {}
""",
        **_DRAG_INPUTS,
    )
    assert moved == [True, "420", "905", "1500"]


def test_a_corner_drag_drops_a_point_solve_still_to_come() -> None:
    """It would read the corner half-dragged; the drag's own end solves."""
    cleared, timer = run_wizard_js(
        """(function() {
          setupCornerDragging(container);
          container.listeners.mousedown({ target: { closest: function() { return handle; } }, clientX: 10, clientY: 10,
                                          preventDefault: function() {} });
          return [cleared, calibSolveTimer];
        })()""",
        functions=("setupCornerDragging",),
        prelude="""
var calibSolveTimer = 'pending', cleared = [], cornerPositions = { DSL: [1, 2] }, poseSeq = 0;
function clearTimeout(t) { cleared.push(t); } function showLoupe() {}
var handle = { dataset: { corner: 'DSL' }, style: {} };
var container = { listeners: {}, addEventListener: function(t, f) { this.listeners[t] = f; } };
var svg = { createSVGPoint: function() { return { matrixTransform: function() { return { x: 0, y: 0 }; } }; },
            getScreenCTM: function() { return { inverse: function() {} }; } };
var document = { getElementById: function() { return svg; } };
var window = { addEventListener: function() {} };
""",
    )
    assert (cleared, timer) == (["pending"], None)


def test_a_reset_brings_back_the_places_of_points_unchanged_since() -> None:
    """The restored pose is the one those places were solved with; an edited point's place is not."""
    places = run_wizard_js(
        """(function() {
          var snap = snapshotPose();
          calibForgetPose();
          calibPoints[1].distance_a = 20.0;
          restorePose(snap);
          return calibPoints.map(function(p) { return p.position; });
        })()""",
        functions=("calibMeasured", "calibTakeAnswer", "calibForgetPose", "snapshotPose", "restorePose"),
        prelude=_POSE_PAGE + "function getState() { return { camera: {} }; }",
        calibPoints=[
            {**_POINT, "pin": [0.25, 0.5], "position": [2.5, 9.0, 0.0]},
            {**_POINT, "pin": [0.75, 0.5], "position": [-3.0, 12.0, 0.0]},
        ],
        calibAnswers=[None, None],
    )
    assert places == [[2.5, 9.0, 0.0], None]


def test_review_lists_a_point_apply_will_not_save() -> None:
    rows = run_wizard_js(
        """(function() {
          populateReviewCalib();
          return [box.style.display, list.children.map(function(r) {
            return r.children.map(function(c) { return c.textContent; }); })];
        })()""",
        functions=("calibNumber", "calibComplete", "calibPositionText", "populateReviewCalib"),
        prelude=length_page("metric", {})
        + """
function el() { return { children: [], style: {}, textContent: '', appendChild: function(c) { this.children.push(c); },
                         set innerHTML(_v) { this.children = []; } }; }
var box = el(), list = el(), CALIB_STATES = { ok: ['stat-chip ok', 'Used'] };
document.getElementById = function(id) { return { 'review-calib': box, 'review-calib-list': list }[id] || null; };
document.createElement = function() { return el(); };
""",
        calibPoints=[{**_POINT, "pin": [0.25, 0.5], "position": [2.5, 9.0, 0.0]}, {**_POINT, "distance_b": None}],
        calibAnswers=[{"status": "ok", "position": [2.5, 9.0, 0.0]}, None],
    )
    assert rows == ["", [["Point 1", "Used", "X 2.500 m  Y 9.000 m  Z 0.000 m"], ["Point 2", "Incomplete", ""]]]


@pytest.mark.parametrize(("count", "step"), [(2, ""), (6, "none")])
def test_the_one_point_notice_offers_a_second_only_while_one_can_be_added(count: int, step: str) -> None:
    shown = run_wizard_js(
        "(function() { renderCalibStatus(); return [els['calib-notice'].style.display,"
        " els['calib-notice-step'].style.display]; })()",
        functions=("calibNumber", "calibComplete", "calibPositionText", "renderCalibStatus"),
        prelude=length_page("metric", {})
        + """
var els = {}; ['calib-list', 'calib-add', 'calib-notice', 'calib-notice-step'].forEach(function(id) {
  els[id] = { style: {}, textContent: '' }; });
document.getElementById = function(id) { return els[id] || null; };
var CALIB_MAX = 6, imageWidth = 1920, CALIB_STATES = { unverified: ['stat-chip warn', 'Unverified'] };
""",
        calibPoints=[{**_POINT, "distance_a": None}] * (count - 1) + [{**_POINT, "pin": [0.2, 0.5]}],
        calibAnswers=[None] * (count - 1) + [{"status": "unverified", "position": [1, 2, 0]}],
    )
    assert shown == ["", step]


def test_a_new_point_counts_once_its_dot_is_moved_onto_the_spot() -> None:
    """Its dot starts at the centre of the snapshot: solved or saved there, it would be an invented pin."""
    states = run_wizard_js(
        """(function() {
          var states = [calibComplete(calibPoints[0])];
          renderCalibOverlay();
          setupCalibDragging();
          var dot = calibDrawn[0].handle.children[2];
          calibGroup.listeners.touchstart(touch(dot, 960, 540));
          windowListeners.touchend({});
          states.push(calibComplete(calibPoints[0]), solves);
          calibGroup.listeners.touchstart(touch(calibDrawn[0].handle.children[2], 960, 540));
          windowListeners.touchmove(touch(dot, 980, 560));
          windowListeners.touchend({});
          states.push(calibComplete(calibPoints[0]), solves, listed);
          return states;
        })()""",
        functions=(*_DRAG_FUNCTIONS, "calibNumber", "calibComplete"),
        prelude=_DRAG_PAGE + "calibSolveTimer = null;",
        **{**_DRAG_INPUTS, "calibPoints": [{**_POINT, "pin": [0.5, 0.5], "pinned": False, "position": None}]},
    )
    # Unplaced: incomplete; a press that moves nothing places nothing; the first move places it.
    assert states == [False, False, 0, True, 1, 1]


def _list_page(system: str = "metric") -> str:
    return (
        length_page(system, {})
        + """
function el(tag) {
  return { tag: tag, children: [], attrs: {}, style: {}, textContent: '', className: '',
           appendChild: function(c) { this.children.push(c); }, setAttribute: function(k, v) { this.attrs[k] = v; },
           set innerHTML(_v) { this.children = []; } };
}
var list = el('div');
document.getElementById = function(id) { return id === 'calib-list' ? list : null; };
document.createElement = el;
var calibAwaiting = null, CALIB_LEN_UNIT = 'm';
function wizWriteLen() {} function renderCalibStatus() {}
function texts(node) { return [node.textContent].concat.apply([node.textContent], node.children.map(texts)); }
"""
    )


@pytest.mark.parametrize(("pinned", "asked"), [(False, True), (True, False)])
def test_a_point_whose_dot_was_never_moved_asks_for_it(pinned: bool, asked: bool) -> None:
    shown = run_wizard_js(
        "(function() { renderCalibList(); return texts(list).filter(function(t) { return t; }); })()",
        functions=("calibField", "renderCalibList"),
        prelude=_list_page(),
        calibPoints=[{**_POINT, "pin": [0.5, 0.5], "pinned": pinned, "position": None}],
    )
    assert ("Drag dot 1 onto the measured spot." in shown) is asked


@pytest.mark.parametrize(("system", "kind", "step"), [("metric", "number", "0.001"), ("imperial", "text", None)])
def test_a_distance_field_takes_millimetres_in_metric(system: str, kind: str, step: str | None) -> None:
    """A laser distance meter reads to the millimetre."""
    field = run_wizard_js(
        "(function() { var f = calibField('calib-a-0', 'From DSL', 18.5, 0); var input = f.el.children[1];"
        " return [input.type, input.step === undefined ? null : input.step]; })()",
        functions=("calibField",),
        prelude=_list_page(system),
    )
    assert field == [kind, step]


@pytest.mark.parametrize(
    ("enabled", "with_points", "points", "sent", "hold"),
    [
        (True, None, [{**_POINT, "pin": [0.25, 0.5]}], True, True),
        (True, None, [{**_POINT, "distance_a": None}], False, True),  # only incomplete points: still held
        (True, None, [], False, False),
        (True, False, [{**_POINT, "pin": [0.25, 0.5]}], False, False),  # Reference Mapping's rigid shift
        (False, None, [{**_POINT, "pin": [0.25, 0.5]}], False, False),  # the section hidden
    ],
)
def test_a_solve_body_carries_the_points_only_with_the_section(enabled, with_points, points, sent, hold) -> None:
    body = run_wizard_js(
        "(function() { var b = wizardSolveBody([[0, 0]], withPoints); return [JSON.parse(JSON.stringify(b)),"
        " !!b.calibHold, Array.isArray(b.calibIndices)]; })()",
        functions=("calibNumber", "calibComplete", "calibPinPx", "calibPayload", "wizardSolveBody"),
        prelude=f"""
var CALIB_ENABLED = {json.dumps(enabled)}, imageWidth = 1920, imageHeight = 1080, poseSeq = 3, WIZ_SOLVE_PAGE = 'p';
function wizardWorldCorners() {{ return []; }} function wizReadLensCoeff() {{ return 0; }}
""",
        calibPoints=points,
        withPoints=with_points,
    )
    sent_body, held, indexed = body
    assert ("calibration_points" in sent_body) is sent
    assert "calibIndices" not in sent_body and "calibHold" not in sent_body  # never on the wire
    assert (held, indexed) == (hold, enabled and with_points is None)


def test_a_solve_body_names_its_page_and_its_place_in_the_pose_sequence() -> None:
    """The server drops a joint fit once this page has asked for a newer one."""
    sent = run_wizard_js(
        "JSON.parse(JSON.stringify(wizardSolveBody([[0, 0]])))",
        functions=("calibNumber", "calibComplete", "calibPinPx", "calibPayload", "wizardSolveBody"),
        prelude="""
var CALIB_ENABLED = true, imageWidth = 1920, imageHeight = 1080, poseSeq = 12, WIZ_SOLVE_PAGE = 'page-a';
function wizardWorldCorners() { return []; } function wizReadLensCoeff() { return 0; }
""",
        calibPoints=[],
    )
    assert (sent["solve_page"], sent["solve_seq"]) == ("page-a", 12)


def test_the_solve_page_is_a_name_the_server_takes() -> None:
    page = run_wizard_js("WIZ_SOLVE_PAGE", variables=("WIZ_SOLVE_PAGE",))
    assert isinstance(page, str) and 0 < len(page) <= 64


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize(
    ("held", "world", "pins"),
    [(True, _WORLD, _FRACTIONS), (True, [[6, 0, 0], *_WORLD[1:]], []), (False, _WORLD, [])],
    ids=["held", "grid changed since", "none held"],
)
def test_apply_saves_the_points_with_the_pose_with_or_without_the_section(enabled, held, world, pins) -> None:
    """Without the section they are the saved points, each place kept only with the pose it came from; the
    corner pins go with them while they belong to this pose and grid, and none otherwise."""
    payload = run_wizard_js(
        "(function() { window.applyAndFinish(); return sent[0]; })()",
        functions=("calibNumber", "calibComplete", "calibSaved", "calibHeldFor"),
        prelude=f"""
var window = {{ OpenFollow: {{ saveError: {{ show: function() {{}} }} }} }}, CALIB_ENABLED = {json.dumps(enabled)};
var calibHeldPins = {json.dumps(_GOOD_HELD if held else None)};
function wizardWorldCorners() {{ return {json.dumps(world)}; }}
var sent = [], SENSOR_SIZES = [];
function invalidLengthFields() {{ return []; }} function wizLensPairValid() {{ return true; }}
function getState() {{ return {{ camera: {{ pos_x: 1, lens_k1: 0, lens_k2: 0 }}, grid: {{}}, lens: {{}} }}; }}
var document = {{ querySelector: function() {{ return null; }} }};
function fetch(_url, opts) {{ sent.push(JSON.parse(opts.body)); return {{ then: function() {{ return this; }} }}; }}
"""
        + wizard_function("applyAndFinish", prefix="window."),
        calibPoints=[{**_POINT, "pin": [0.25, 0.5], "position": [2.5, 9.0, 0.0]}, {**_POINT, "distance_a": None}],
    )
    common = {"corner_a": "DSR", "corner_b": "DSL", "distance_a": 18.5, "distance_b": 19.25, "height": 0.0}
    assert payload["calibration_points"] == [{**common, "pin_x": 0.25, "pin_y": 0.5, "position": [2.5, 9.0, 0.0]}]
    assert payload["corner_pins"] == pins


_CHANGE_PAGE = """
var poseSeq = 4, solved = 0, scheduled = 0, calibAwaiting = null, calibSolveTimer = null, window = {};
function saveToSession() {} function renderCalibList() {} function renderCalibOverlay() {}
function renderCalibStatus() {} function setFineZoomMode() {} function calibTakeAnswer() {}
function solveFromCorners() { solved++; } function calibScheduleSolve() { scheduled++; }
function wizReadLen(id) { return { 'calib-a-0': 20.0, 'calib-b-0': 19.25, 'calib-h-0': 0.0 }[id]; }
"""


@pytest.mark.parametrize("change", ["window.calibDelete(0)", "calibChooseCorners(0)", "calibOnField(0)"])
def test_a_change_to_the_points_drops_older_answers_and_solves_again(change: str) -> None:
    after = run_wizard_js(
        f"(function() {{ {change}; return [poseSeq, solved + scheduled]; }})()",
        functions=("calibNumber", "calibComplete", "calibEdited", "calibChooseCorners", "calibOnField"),
        prelude=_CHANGE_PAGE + wizard_function("calibDelete", prefix="window."),
        calibPoints=[{**_POINT, "pin": [0.25, 0.5]}, {**_POINT, "pin": [0.75, 0.5]}],
        calibAnswers=[{"status": "ok"}, {"status": "ok"}],
    )
    assert after == [5, 1]


@pytest.mark.parametrize(
    ("deleted", "deleted_answer", "forgotten"),
    [
        ({}, {"status": "ok"}, True),
        ({}, {"status": "unverified"}, True),
        ({"position": [3, 4, 0]}, None, True),  # loaded from the config: in the saved pose's fit, no answer yet
        ({}, None, True),  # complete, just edited: the pose may hold its old measurement
        ({}, {"status": "flagged"}, False),  # left out of the fit: the pose is the rest's own
        ({"distance_a": None}, None, False),  # incomplete: never in a fit
    ],
    ids=["used", "the only one", "loaded", "edited", "left out", "incomplete"],
)
def test_a_delete_forgets_a_pose_the_deleted_point_may_have_been_fitted_with(
    deleted, deleted_answer, forgotten
) -> None:
    """Forgotten before the session is saved and the solve sent, so neither a reload nor a failed or
    superseded solve leaves it standing; the solve's answer brings the rest back. The held corner pins
    are the operator's, never the deleted point's: they stay."""
    answers, positions, held, timer, solved, stored = run_wizard_js(
        "(function() { window.calibDelete(1); return [calibAnswers, calibPoints.map(function(p) {"
        " return p.position; }), calibHeldPins, calibSolveTimer, solved, stored]; })()",
        functions=("calibNumber", "calibComplete", "calibTakeAnswer", "calibForgetPose"),
        prelude=_NO_CALIB_DRAWING
        + """
var poseSeq = 4, calibAwaiting = null, window = {}, calibHeldPins = { world: [], corners: [] }, solved = 0;
var calibSolveTimer = setTimeout(function() {}, 0);
var stored = null;
function saveToSession() { stored = calibPoints.map(function(p) { return p.position; }); }
function renderCalibList() {}
function solveFromCorners() { solved++; }
"""
        + wizard_function("calibDelete", prefix="window."),
        calibPoints=[{**_POINT, "pin": [0.25, 0.5], "position": [1, 2, 0]}, {**_POINT, "pin": [0.75, 0.5], **deleted}],
        calibAnswers=[{"status": "ok", "position": [1, 2, 0]}, deleted_answer],
    )
    assert (timer, solved) == (None, 1)  # one solve, the delete's own; a point solve still to come would repeat it
    assert held == {"world": [], "corners": []}
    assert stored == positions
    if forgotten:
        assert (answers, positions) == ([None], [None])
    else:
        assert (answers, positions) == ([{"status": "ok", "position": [1, 2, 0]}], [[1, 2, 0]])


@pytest.mark.parametrize(("added", "snapped"), [(False, True), (True, False)], ids=["none added", "point added"])
def test_a_corners_only_answer_landing_after_a_point_was_added_holds_the_pins(added: bool, snapped: bool) -> None:
    """Whether the pins are held is decided when the answer lands: a point added meanwhile counts."""
    corners, held = run_wizard_js(
        "(function() { solveFromCorners(); return [cornerPositions, heldBody]; })()",
        functions=(
            "isConvex",
            "quadArea",
            "wizSolveCorners",
            "calibNumber",
            "calibComplete",
            "calibPinPx",
            "calibPayload",
            "wizardSolveBody",
            "solveFromCorners",
        ),
        variables=("CORNER_NAMES",),
        prelude=f"""
function answered(value) {{ return {{ then: function(f) {{ return answered(f(value)); }}, catch: function() {{}} }}; }}
var poseSeq = 0, preCornerPinningPose = null, CALIB_ENABLED = true, WIZ_SOLVE_PAGE = 'p';
var imageWidth = 1920, imageHeight = 1080, heldBody = null;
function fetch() {{
  if ({json.dumps(added)}) calibPoints.push({{ pin: [0.5, 0.5] }});
  return answered({{ json: function() {{ return reply; }} }});
}}
function invalidLengthFields() {{ return []; }} function snapshotPose() {{ return {{}}; }}
function showSolveStatus() {{}} function showSolvedParams() {{}} function updateFineQuad() {{}}
function renderAllFineZoomBoxes() {{}} function applySolvedPose(c, s, b) {{ heldBody = !!b.calibHold; }}
function wizardWorldCorners() {{ return []; }} function wizReadLensCoeff() {{ return 0; }}
var document = {{ getElementById: function() {{ return {{ querySelectorAll: function() {{ return []; }} }}; }} }};
""",
        cornerPositions=_CORNERS_PINNED,
        calibPoints=[],
        reply={"camera": {}, "reprojected_corners": [[1501, 901], [401, 899], [601, 501], [1299, 499]]},
    )
    assert (corners != _CORNERS_PINNED) is snapped
    assert held is not snapped


def test_the_lens_resolve_hands_its_point_answers_on() -> None:
    handed = run_wizard_js(
        "(function() { solveFromPinnedCorners(); return handed; })()",
        functions=("solveFromPinnedCorners",),
        prelude="""
function answered(value) { return { then: function(f) { return answered(f(value)); }, catch: function() {} }; }
var handed = null, poseSeq = 0, imageWidth = 1920, imageHeight = 1080, currentStep = 1, WIZ = { lens: 1 };
var pinnedCorners = { size: [1920, 1080], corners: [[1, 2], [3, 4], [5, 6], [7, 8]] };
function wizardSolveBody(sc) { return { screen_corners: sc }; }
function fetch() { return answered({ json: function() { return { camera: {}, calibration_points: ['answer'] }; } }); }
function applySolvedPose(camera, corners, body, answers) { handed = answers; }
""",
    )
    assert handed == ["answer"]


@pytest.mark.parametrize(("points", "expected"), [(False, [False]), (True, [True])], ids=["rigid shift", "corners"])
def test_the_lens_resolve_leaves_the_points_out_where_the_pins_came_without_them(points: bool, expected) -> None:
    """Reference Mapping's rigid shift is no perspective image: solved again for a new lens pair, its
    pins must not judge the points either, nor be held as the corners the points were solved with."""
    asked = run_wizard_js(
        "(function() { var body = { image_width: 1920, image_height: 1080, camera: { lens_k1: 0, lens_k2: 0 } };"
        " if (points) Object.defineProperty(body, 'calibIndices', { value: [] });"
        " applySolvedPose({}, [[1, 2], [3, 4], [5, 6], [7, 8]], body, []);"
        " solveFromPinnedCorners(); return asked; })()",
        functions=("calibTakeAnswer", "applySolvedPose", "solveFromPinnedCorners"),
        prelude=_POSE_PAGE
        + """
var asked = [], imageWidth = 1920, imageHeight = 1080;
function wizardSolveBody(sc, withPoints) { asked.push(withPoints !== false); return { screen_corners: sc }; }
function fetch() { return { then: function() { return { then: function() { return { catch: function() {} }; } }; } }; }
""",
        calibPoints=[],
        calibAnswers=[],
        points=points,
    )
    assert asked == expected


@pytest.mark.parametrize(
    ("taps", "expected"),
    [
        (["DSR", "DSL"], (["pending"], None, 1)),  # complete again: solved now, once
        (["USR", "DSL"], ([], "pending", 0)),  # USR has no distance: the scheduled solve takes it out of the pose
    ],
)
def test_the_second_corner_solves_once_not_again_from_a_timer(taps: list[str], expected: tuple) -> None:
    cleared, timer, solved = run_wizard_js(
        """(function() {
          calibChooseCorners(0);
          calibSolveTimer = 'pending';
          taps.forEach(calibPickCorner);
          return [cleared, calibSolveTimer, solved];
        })()""",
        functions=("calibNumber", "calibComplete", "calibEdited", "calibChooseCorners", "calibPickCorner"),
        prelude="""
var solved = 0, poseSeq = 0, calibAwaiting = null, calibSolveTimer = null, cleared = [];
function clearTimeout(t) { cleared.push(t); }
function saveToSession() {} function renderCalibList() {} function renderCalibOverlay() {} function setFineZoomMode() {}
function calibScheduleSolve() {} function solveFromCorners() { solved++; }
""",
        calibPoints=[{**_POINT, "pin": [0.25, 0.5]}],
        calibAnswers=[None],
        taps=taps,
    )
    assert (cleared, timer, solved) == expected


def test_an_added_point_is_not_complete_until_its_dot_is_moved() -> None:
    complete = run_wizard_js(
        """(function() {
          window.calibAdd();
          var p = calibPoints[0];
          p.corner_a = 'DSR'; p.corner_b = 'DSL'; p.distance_a = 18.5; p.distance_b = 19.25;
          return [calibComplete(p), JSON.parse(JSON.stringify(p)).pin];
        })()""",
        functions=("calibNumber", "calibComplete"),
        prelude="""
var poseSeq = 0, calibAwaiting = null, CALIB_MAX = 6, imageWidth = 1920, window = {};
function setFineZoomMode() {} function saveToSession() {} function renderCalibList() {} function renderCalibOverlay() {}
"""
        + wizard_function("calibAdd", prefix="window."),
        calibPoints=[],
        calibAnswers=[],
    )
    assert complete == [False, [0.5, 0.5]]


@pytest.mark.parametrize(
    ("saved", "expected"),
    [
        (_FRACTIONS, {"world": _WORLD, "corners": _FRACTIONS}),
        ([], None),
        (_FRACTIONS[:3], None),
    ],
)
def test_a_fresh_run_holds_the_corner_pins_the_saved_pose_was_solved_from(saved, expected) -> None:
    """Started from the saved pose's reprojection instead, a run that only edits a point would drift it."""
    held = run_wizard_js(
        "(function() { calibHoldSavedPins(); return calibHeldPins; })()",
        functions=("calibNumber", "calibHeldRestored", "calibHoldSavedPins"),
        prelude=f"var calibHeldPins = null; function wizardWorldCorners() {{ return {json.dumps(_WORLD)}; }}",
        CALIB_SAVED_CORNERS=saved,
    )
    assert held == expected


@pytest.mark.parametrize("pinning_pose", [None, {"camera": {}, "held": _GOOD_HELD}])
def test_reset_corners_goes_back_to_where_the_pose_projects_them(pinning_pose) -> None:
    """Not to the pins the pose was solved from: Reset is how the operator lets go of them."""
    held = run_wizard_js(
        "(function() { window.resetCornerPinning(); return [calibHeldPins, projected]; })()",
        functions=(),
        prelude="""
var window = {}, calibHeldPins = held, projected = 0, fineZoomViewBoxes = {};
var els = {}; ['fine-status', 'fine-solved-params', 'fine-container'].forEach(function(id) {
  els[id] = { style: {}, classList: { remove: function() {} } }; });
var document = { getElementById: function(id) { return els[id]; } };
function restorePose(pose) { calibHeldPins = pose.held; } function saveToSession() {}
function projectAndOverlay() { projected++; }
"""
        + wizard_function("resetCornerPinning", prefix="window."),
        held=_GOOD_HELD,
        preCornerPinningPose=pinning_pose,
    )
    assert held == [None, 1]


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
        functions=("runLensFit", "lensFitFailed"),
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


_ZOOM_BOX = (
    "var poseSeq = 7, arrowDebounceTimer = null, cornerPositions = { DSL: [100, 50] }, FINE_NEIGHBOURS = { DSL: [] };"
    " var listeners = {};"
    " var svg = { addEventListener: function(n, f) { listeners[n] = f; }, setAttribute: function() {},"
    " setPointerCapture: function() {}, getScreenCTM: function() { return { inverse: function() {} }; },"
    " createSVGPoint: function() { return { matrixTransform: function() { return { x: 120, y: 60 }; } }; } };"
    " var box = { dataset: { corner: 'DSL' }, querySelector: function() { return svg; } };"
    " var document = { querySelector: function() { return null; } };"
    " function setTimeout() { return 1; } function clearTimeout() {} function fineZoomReady() { return true; }"
    " function updateFineQuad() {} function refreshFineZoomNeighbourhood() {} function renderFineZoomBox() {}"
    " function recenterFineZoomBoxIfNeeded() { return false; } function solveFromCorners() {}"
)


@pytest.mark.parametrize(
    ("event", "payload"),
    [
        ("keydown", "{ key: 'ArrowLeft', shiftKey: false, preventDefault: function() {} }"),
        ("pointerdown", "{ pointerId: 1, clientX: 0, clientY: 0, preventDefault: function() {} }"),
    ],
)
def test_a_zoom_box_move_drops_the_answer_to_a_solve_in_flight(event: str, payload: str) -> None:
    out = run_wizard_js(
        f"(function() {{ _attachFineZoomBoxHandlers(box); var before = poseSeq; listeners.{event}({payload});"
        " return poseSeq !== before; })()",
        functions=("_attachFineZoomBoxHandlers",),
        prelude=_ZOOM_BOX,
    )
    assert out is True


_FIT_PAGE = (
    " var lensFitSeq = 0, imageWidth = 1920, imageHeight = 1080, drawn = 0, saved = 0;"
    " var lensFit = { k1: -0.2, k2: 0, rating: 'good' };"
    " var lensLines = [{ points: 5 }];"
    " function lensLineCounts() { return true; } function lensActivePoints() { return []; }"
    " function lensShowStatus() {} function renderLensResult() {}"
    " function renderLens() { drawn++; } function saveToSession() { saved++; }"
)
# A fetch whose request never reaches the server: the chain goes straight to its catch.
_FAILED_FETCH = (
    "function rejected() { return { then: function() { return this; }, catch: function(f) { f(new Error('x')); } }; }"
    " function fetch() { return rejected(); }"
)


@pytest.mark.parametrize("fetch_stub", [_SYNC_FETCH, _FAILED_FETCH], ids=["refused", "unreachable"])
def test_a_failed_fit_redraws_and_stores_no_fit(fetch_stub: str) -> None:
    out = run_wizard_js(
        "(function() { runLensFit(); return [lensFit, drawn, saved]; })()",
        functions=("runLensFit", "lensFitFailed"),
        prelude=fetch_stub + _FIT_PAGE,
    )
    assert out[0] is None
    assert out[1] >= 1 and out[2] >= 1


def test_a_lens_slider_on_the_lens_step_projects_nothing() -> None:
    out = run_wizard_js(
        "(function() { onWizardLensInput('k1', 'range'); return projected; })()",
        prelude="var window = {}; var projected = 0, currentStep = 3, WIZ = { lens: 3 };"
        " var fields = { wiz_lens_k1: { value: '-0.2' }, wiz_lens_k1_range: { value: '-0.2' } };"
        " var document = { getElementById: function(id) { return fields[id] || null; } };"
        " function wizWriteLensCoeff() {} function wizLensPairValid() { return true; }"
        " function lensRecomputeCurves() {} function renderLens() {} function onLensCoeffChanged() {}"
        " function projectAndOverlay() { projected++; }\n"
        + wizard_window_function("onWizardLensInput")
        + "\nvar onWizardLensInput = window.onWizardLensInput;",
    )
    assert out == 0
