# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The Setup Wizard's script maths, run in node and held against the Python it mirrors."""

from __future__ import annotations

import json
import math
import re

import numpy as np
import pytest

from openfollow.configuration import CameraConfig
from openfollow.lens_model import lens_fold_radius, lens_warp_is_valid
from openfollow.scene.calibration_points import (
    DISTANCE_SIGMA,
    FLAG_PX,
    MEET_TOLERANCE,
    REASON_DIFFER,
    REASON_HEIGHT,
    REASON_SAME_PLACE,
    REASON_SHORT,
    MarkFit,
    look_at,
    plausibility,
    trilaterate,
)
from openfollow.scene.edge_chains import edge_map_scale
from openfollow.scene.edge_snap import band_half_size, band_step
from openfollow.scene.lens_fit import fit_lens_from_lines
from openfollow.scene.solver import apply_overlay_distortion, invert_normalised_radius, project_points
from openfollow.units import format_length
from tests._lens_band import scaled_luma
from tests._wizard_js import length_page, needs_node, run_wizard_js, wizard_window_function

pytestmark = [pytest.mark.unit, needs_node]

_LENS_FUNCTIONS = (
    "wizLensFoldRadius",
    "wizLensIsValid",
    "wizApplyDistortion",
    "wizInvertRadius",
    "wizInvertDistortion",
)
_LENS_VARS = ("DISTORTION_INVERT_ITERS", "DISTORTION_INVERT_SETTLED", "DISTORTION_INVERT_R_CAP")
_W, _H = 1920, 1080

# Pairs across the valid region: pinhole, barrel, a 100 degree lens, pincushion, mixed signs.
_PAIRS = [(0.0, 0.0), (-0.1, 0.0), (-0.26, 0.05), (-0.47, 0.25), (0.2, 0.0), (0.1, -0.05), (-0.3, 0.12)]


# ---------------------------------------------------------------------------
# Lens warp
# ---------------------------------------------------------------------------


def test_fold_radius_and_validity_are_the_servers() -> None:
    k1s = np.round(np.arange(-0.8, 0.81, 0.04), 2).tolist()
    k2s = np.round(np.arange(-0.4, 0.41, 0.04), 2).tolist()
    pairs = [(a, b) for a in k1s for b in k2s] + [(-1e200, 1e200), (2e3, 0.0)]
    out = run_wizard_js(
        "pairs.map(function(p) { return [wizLensFoldRadius(p[0], p[1]), wizLensIsValid(p[0], p[1])]; })",
        functions=("wizLensFoldRadius", "wizLensIsValid"),
        pairs=[list(p) for p in pairs],
    )
    for (radius, valid), (a, b) in zip(out, pairs, strict=True):
        expected = lens_fold_radius(a, b)
        assert (radius is None) == math.isinf(expected), (a, b)
        if radius is not None:
            assert radius == pytest.approx(expected, rel=1e-9), (a, b)
        assert valid == lens_warp_is_valid(a, b), (a, b)


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


# ---------------------------------------------------------------------------
# Lens step: band, edge map, lines and the fit
# ---------------------------------------------------------------------------


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


@pytest.mark.parametrize(("saved", "before"), [([0, 0], False), ([-0.2, 0.03], True)], ids=["pinhole", "saved pair"])
def test_a_slider_edit_counts_as_a_measured_lens(saved: list[float], before: bool) -> None:
    out = run_wizard_js(
        "(function() { var was = lensMeasured(); onWizardLensInput('k1', 'range'); return [was, lensMeasured()]; })()",
        functions=("lensMeasured",),
        prelude="var window = {}; var S = { lensTouched: false };"
        f" var SAVED = {{ lens: {json.dumps(saved)} }};"
        " var fields = { wiz_lens_k1: { value: '-0.2' }, wiz_lens_k1_range: { value: '-0.2' } };"
        " var document = { getElementById: function(id) { return fields[id] || null; } };"
        " function wizWriteLensCoeff() {} function wizLensPairValid() { return true; }"
        " function lensRecomputeCurves() {} function renderLens() {} function renderLensResult() {}"
        " function onLensCoeffChanged() {}\n"
        + wizard_window_function("onWizardLensInput")
        + "\nvar onWizardLensInput = window.onWizardLensInput;",
    )
    # A station's saved pair counts as measured; a pinhole one only once it is edited.
    assert out == [before, True]


# ---------------------------------------------------------------------------
# Lens values from an exported settings file
# ---------------------------------------------------------------------------

_LENS_FILE_FUNCTIONS = ("wizLensFoldRadius", "wizLensIsValid", "lensFromSettings")
_NOT_SETTINGS = "This is not an OpenFollow settings file."
_NO_LENS = "This settings file has no lens calibration."
_FOLDS = "With the lens values of this file, the overlay folds over itself inside the picture."


def _settings(k1: object, k2: object) -> str:
    return json.dumps({"psn_system_name": "Stage", "camera": {"fov": 60.0, "lens_k1": k1, "lens_k2": k2}})


def test_a_settings_file_gives_its_lens_pair() -> None:
    out = run_wizard_js("lensFromSettings(text)", functions=_LENS_FILE_FUNCTIONS, text=_settings(-0.21, 0.03))
    assert out == {"k1": -0.21, "k2": 0.03}


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("camera: lens_k1", _NOT_SETTINGS),
        ("", _NOT_SETTINGS),
        ("null", _NOT_SETTINGS),
        ("5", _NOT_SETTINGS),
        ("[1, 2]", _NOT_SETTINGS),
        ("{}", _NOT_SETTINGS),
        ('{"camera": 3}', _NOT_SETTINGS),
        ('{"camera": null}', _NOT_SETTINGS),
        (json.dumps({"camera": {"lens_k1": -0.2}}), _NO_LENS),
        (_settings(True, 0.0), _NO_LENS),
        (_settings("-0.2", 0.0), _NO_LENS),
        (_settings(None, 0.0), _NO_LENS),
        ('{"camera": {"lens_k1": 1e400, "lens_k2": 0}}', _NO_LENS),
        (_settings(-0.6, 0.0), _FOLDS),
    ],
    ids=[
        "not json",
        "empty",
        "null",
        "number",
        "list",
        "no camera",
        "camera a number",
        "camera null",
        "edge fit missing",
        "a bool",
        "a string",
        "a null value",
        "infinite",
        "a folding pair",
    ],
)
def test_a_settings_file_without_a_usable_pair_says_why(text: str, error: str) -> None:
    assert run_wizard_js("lensFromSettings(text)", functions=_LENS_FILE_FUNCTIONS, text=text) == {"error": error}


def test_a_settings_file_pair_is_taken_exactly_when_the_station_would_take_it() -> None:
    pairs = [
        (float(a), float(b))
        for a in np.round(np.arange(-0.8, 0.81, 0.05), 2)
        for b in np.round(np.arange(-0.4, 0.41, 0.05), 2)
    ]
    out = run_wizard_js(
        "texts.map(function(t) { return !lensFromSettings(t).error; })",
        functions=_LENS_FILE_FUNCTIONS,
        texts=[_settings(a, b) for a, b in pairs],
    )
    assert out == [lens_warp_is_valid(a, b) for a, b in pairs]
    assert any(out) and not all(out)


# ---------------------------------------------------------------------------
# Lengths in the active unit system
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("system", ["imperial", "metric"])
@pytest.mark.parametrize("meters", [0.5, 7.5, 0.3048, -2.25, 11.0, 0.123456])
def test_a_written_length_reads_back_the_metres_it_was_written_from(system: str, meters: float) -> None:
    shown, read = run_wizard_js(
        "(function() { var t = lenField(m); return [t, len(t)]; })()",
        functions=("len", "lenField"),
        variables=("exactLen",),
        prelude=length_page(system, {}),
        m=meters,
    )
    if system == "imperial":
        # Read back from its ft / in text, 0.5 m was 0.500126 m: one grid line more than the HUD draws.
        assert shown == format_length(meters, "imperial")
        assert read == meters
    else:
        assert read == float(shown) and abs(read - meters) <= 5e-4


def test_an_edited_imperial_length_reads_what_it_shows() -> None:
    read = run_wizard_js(
        "(function() { lenField(0.5); return len('2 ft'); })()",
        functions=("len", "lenField"),
        variables=("exactLen",),
        prelude=length_page("imperial", {}),
    )
    assert read == pytest.approx(0.6096, abs=1e-12)


# ---------------------------------------------------------------------------
# Projection: the page draws what the station solved
# ---------------------------------------------------------------------------

_CAMERAS = [
    [0.0, -12.0, 7.0, -25.0, 0.0, 0.0, 60.0],
    [0.0, 18.0, 7.0, -20.0, 180.0, 0.0, 70.0],
    [3.0, -9.0, 10.0, -48.0, 15.0, 5.0, 40.0],
    [-4.0, -6.0, 5.0, -35.0, -20.0, -3.0, 120.0],
    [-10.0, 4.0, 6.0, -30.0, 90.0, 0.0, 70.0],
]
_CAMERA_IDS = ["front of house", "yaw 180", "pitch -48 rolled fov 40", "fov 120 rolled", "stage right"]


def _forward(cam: list[float]) -> np.ndarray:
    pitch, yaw = math.radians(cam[3]), math.radians(cam[4])
    return np.array([math.sin(yaw) * math.cos(pitch), math.cos(yaw) * math.cos(pitch), math.sin(pitch)])


@pytest.mark.parametrize("size", [(1920, 1080), (1280, 960)])
@pytest.mark.parametrize("cam", _CAMERAS, ids=_CAMERA_IDS)
def test_a_point_lands_where_the_station_projects_it(cam: list[float], size: tuple[int, int]) -> None:
    w, h = size
    points = [[x, y, z] for x in (-6.0, -2.0, 3.0, 7.0) for y in (-3.0, 2.0, 8.0, 14.0) for z in (0.0, 1.8)]
    behind = np.array(cam[:3]) - 4.0 * _forward(cam)
    points.append(behind.tolist())
    out = run_wizard_js(
        "points.map(function(P) { return wizProject(cam, P, w, h); })",
        functions=("wizRotation", "wizProject"),
        variables=("RAD",),
        cam=cam,
        points=points,
        w=w,
        h=h,
    )
    expected = project_points(np.array(cam), np.array(points), float(w), float(h))
    seen = ~np.isnan(expected).any(axis=1)
    # Behind the camera: the station's NaN is the page's null.
    assert [q is None for q in out] == (~seen).tolist()
    assert out[-1] is None and seen.sum() >= 8
    np.testing.assert_allclose([q for q in out if q is not None], expected[seen], atol=1e-6)


@pytest.mark.parametrize("fov", [0.5, 179.5])
def test_a_field_of_view_the_station_refuses_draws_nothing(fov: float) -> None:
    cam = [0.0, -12.0, 7.0, -25.0, 0.0, 0.0, fov]
    out = run_wizard_js(
        "wizProject(cam, [0, 3, 0], 1920, 1080)", functions=("wizRotation", "wizProject"), variables=("RAD",), cam=cam
    )
    assert out is None
    with pytest.raises(ValueError, match="fov"):
        project_points(np.array(cam), np.array([[0.0, 3.0, 0.0]]), 1920.0, 1080.0)


_LOOKS = [
    ([0.0, -12.0, 7.0], [0.0, 3.0, 0.0]),
    ([10.0, 4.0, 6.0], [0.0, 3.0, 0.0]),
    ([-3.0, 20.0, 8.0], [1.0, 2.0, 0.5]),
    ([2.0, -5.0, 1.0], [2.0, 10.0, 3.0]),
    ([-8.0, -8.0, 4.0], [0.0, 0.0, 0.0]),
]


@pytest.mark.parametrize(("pos", "target"), _LOOKS)
def test_looking_at_a_point_is_the_stations_look_at_and_centres_it(pos: list[float], target: list[float]) -> None:
    look, centre = run_wizard_js(
        "(function() { var la = wizLookAt(pos, target);"
        " return [la, wizProject([pos[0], pos[1], pos[2], la.pitch, la.yaw, 0, 60], target, 1920, 1080)]; })()",
        functions=("wizLookAt", "wizRotation", "wizProject"),
        variables=("RAD",),
        pos=pos,
        target=target,
    )
    pitch, yaw = look_at(pos, target)
    assert (look["pitch"], look["yaw"]) == pytest.approx((pitch, yaw), abs=1e-12)
    assert centre == pytest.approx([960.0, 540.0], abs=1e-6)


@pytest.mark.parametrize("cam", _CAMERAS, ids=_CAMERA_IDS)
def test_the_origin_before_the_solve_is_where_the_camera_sees_it(cam: list[float]) -> None:
    # Simple's corners, DSL DSR USR USL, of a rectangle whose centre is off the origin.
    cx, cy, hw, hd = 1.5, 4.0, 5.0, 3.0
    plane = [[cx + hw, cy - hd], [cx - hw, cy - hd], [cx - hw, cy + hd], [cx + hw, cy + hd]]
    screen = project_points(np.array(cam), np.array([[x, y, 0.0] for x, y in plane]), 1920.0, 1080.0)
    origin = project_points(np.array(cam), np.zeros((1, 3)), 1920.0, 1080.0)[0]
    assert not np.isnan(screen).any() and not np.isnan(origin).any()
    out = run_wizard_js(
        "wizHomographyPoint(plane, screen, [0, 0])",
        functions=("solveLinear", "wizHomographyPoint"),
        plane=plane,
        screen=screen.tolist(),
    )
    assert out == pytest.approx(origin.tolist(), abs=1e-6)


# ---------------------------------------------------------------------------
# Simple: the starting rectangle
# ---------------------------------------------------------------------------


def _box(points: np.ndarray) -> tuple[float, float, float, float]:
    return points[:, 0].min(), points[:, 1].min(), points[:, 0].max(), points[:, 1].max()


@pytest.mark.parametrize(
    ("quad", "scaled"),
    [
        ([[-500.0, 1200.0], [2400.0, 1300.0], [1800.0, 300.0], [100.0, 200.0]], "down"),
        ([[-500.0, 1900.0], [100.0, 1950.0], [80.0, 1700.0], [-480.0, 1650.0]], "kept"),
        ([[910.0, 560.0], [1010.0, 565.0], [1000.0, 520.0], [920.0, 515.0]], "up"),
        ([[600.0, 700.0], [1350.0, 720.0], [1250.0, 380.0], [700.0, 360.0]], "kept"),
    ],
    ids=["larger than the frame", "off the frame", "small", "inside"],
)
def test_the_starting_rectangle_fits_the_frame_with_its_shape(quad: list[list[float]], scaled: str) -> None:
    w, h = 1920.0, 1080.0
    out = np.array(run_wizard_js("wizFitQuad(q, w, h)", functions=("wizFitQuad",), q=quad, w=w, h=h))
    q = np.array(quad)
    x0, y0, x1, y1 = _box(out)
    qx0, qy0, qx1, qy1 = _box(q)
    # Never more than half the picture, at least 5 % inside its edges.
    assert x1 - x0 <= w / 2 + 1e-9 and y1 - y0 <= h / 2 + 1e-9
    assert x0 >= 0.05 * w - 1e-9 and x1 <= 0.95 * w + 1e-9 and y0 >= 0.05 * h - 1e-9 and y1 <= 0.95 * h + 1e-9
    # One scale about the box centre: each corner keeps its side of the picture.
    k = (x1 - x0) / (qx1 - qx0)
    centre, q_centre = np.array([x0 + x1, y0 + y1]) / 2, np.array([qx0 + qx1, qy0 + qy1]) / 2
    np.testing.assert_allclose(out - centre, k * (q - q_centre), atol=1e-9)
    if scaled == "kept":
        assert k == pytest.approx(1.0)
    elif scaled == "up":
        # A small one is drawn as large as half the picture allows.
        assert max((x1 - x0) / (w / 2), (y1 - y0) / (h / 2)) == pytest.approx(1.0)
    else:
        assert k < 1.0
    if scaled == "kept" and qx0 >= 0.05 * w and qy1 <= 0.95 * h:
        np.testing.assert_allclose(out, q, atol=1e-9)


# ---------------------------------------------------------------------------
# The loupe beside the point
# ---------------------------------------------------------------------------

_LOUPE = 120
_PREVIEW = (800.0, 450.0)


def _loupe(sx: float, sy: float, gap: float, avoid: dict | None = None, preview=_PREVIEW) -> list[float]:
    return run_wizard_js(
        "wizLoupeSpot(sx, sy, size, gap, width, height, avoid)",
        functions=("wizLoupeSpot",),
        sx=sx,
        sy=sy,
        size=_LOUPE,
        gap=gap,
        width=preview[0],
        height=preview[1],
        avoid=avoid,
    )


def _side(at: list[float], sx: float, sy: float) -> tuple[int, int]:
    cx, cy = at[0] + _LOUPE / 2, at[1] + _LOUPE / 2
    return (1 if cx > sx else -1), (1 if cy > sy else -1)


@pytest.mark.parametrize("gap", [12, 30], ids=["mouse", "touch"])
def test_the_loupe_edge_is_the_gap_from_the_point_up_and_right(gap: int) -> None:
    at = _loupe(400.0, 225.0, gap)
    assert _side(at, 400.0, 225.0) == (1, -1)
    edge = math.hypot(at[0] + _LOUPE / 2 - 400.0, at[1] + _LOUPE / 2 - 225.0) - _LOUPE / 2
    assert edge == pytest.approx(gap, abs=1e-9)


@pytest.mark.parametrize(
    ("point", "side"),
    [
        ((790.0, 225.0), (-1, -1)),
        ((400.0, 10.0), (-1, 1)),
        ((10.0, 10.0), (1, 1)),
        ((790.0, 10.0), (-1, 1)),
        ((790.0, 440.0), (-1, -1)),
        ((10.0, 440.0), (1, -1)),
    ],
    ids=["right edge", "top edge", "top left", "top right", "bottom right", "bottom left"],
)
def test_the_loupe_takes_the_next_corner_that_stays_in_the_picture(point: tuple, side: tuple) -> None:
    at = _loupe(*point, 12)
    assert _side(at, *point) == side
    assert 0 <= at[0] <= _PREVIEW[0] - _LOUPE and 0 <= at[1] <= _PREVIEW[1] - _LOUPE
    edge = math.hypot(at[0] + _LOUPE / 2 - point[0], at[1] + _LOUPE / 2 - point[1]) - _LOUPE / 2
    assert edge == pytest.approx(12.0, abs=1e-9)


def test_the_loupe_keeps_clear_of_the_menu() -> None:
    menu = {"l": 420.0, "t": 50.0, "r": 700.0, "b": 220.0}
    at = _loupe(400.0, 225.0, 12, menu)
    assert _side(at, 400.0, 225.0) == (-1, -1)
    assert at[0] + _LOUPE <= menu["l"] or at[0] >= menu["r"] or at[1] + _LOUPE <= menu["t"] or at[1] >= menu["b"]


def test_a_loupe_with_no_clear_spot_still_stays_in_the_picture() -> None:
    covered = _loupe(400.0, 225.0, 12, {"l": 0.0, "t": 0.0, "r": 800.0, "b": 450.0})
    assert _side(covered, 400.0, 225.0) == (1, -1)
    cramped = _loupe(75.0, 75.0, 30, preview=(150.0, 150.0))
    assert 0 <= cramped[0] <= 30 and 0 <= cramped[1] <= 30


# ---------------------------------------------------------------------------
# Triangulated points placed from their measurements, before the solve
# ---------------------------------------------------------------------------


def test_the_page_shares_the_stations_tolerances() -> None:
    out = run_wizard_js("[DISTANCE_SIGMA, MEET_TOL, FLAG_PX]", variables=("DISTANCE_SIGMA",))
    assert out == pytest.approx([DISTANCE_SIGMA, MEET_TOLERANCE, FLAG_PX], abs=1e-15)


def _trilaterate_js(cases: list[list]) -> list[dict]:
    return run_wizard_js(
        "cases.map(function(c) { return wizTrilaterate(c[0], c[1], c[2], c[3], c[4]); })",
        functions=("wizTrilaterate",),
        variables=("DISTANCE_SIGMA",),
        cases=cases,
    )


def _cross(a, b, p) -> float:
    return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])


@pytest.mark.parametrize(
    ("a", "b"),
    [([0.0, 0.0, 0.0], [0.0, 10.0, 0.0]), ([5.0, 2.0, 0.0], [-5.0, 2.0, 0.0]), ([-3.0, 7.0, 0.5], [4.0, -1.0, 0.0])],
)
def test_the_first_place_is_right_of_the_line_seen_from_above(a: list[float], b: list[float]) -> None:
    # A place 3 m to the right of the middle of A -> B, with X stage left and Y upstage seen from above.
    d = np.subtract(b[:2], a[:2])
    right = np.array([d[1], -d[0]]) / np.linalg.norm(d)
    place = (np.add(a[:2], b[:2]) / 2 + 3.0 * right).tolist() + [0.0]
    da, db = math.dist(place, a), math.dist(place, b)
    (out,) = _trilaterate_js([[a, da, b, db, 0.0]])
    assert out["pos"] == pytest.approx(place, abs=1e-9)
    assert _cross(a, b, out["pos"]) < 0 < _cross(a, b, out["alt"])


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ([[0, 0, 0], 1.0, [10, 0, 0], 10.0, 2.0], REASON_HEIGHT),
        ([[0, 0, 0], 5.0, [0.01, 0.02, 0], 5.0, 0.0], REASON_SAME_PLACE),
        ([[0, 0, 0], 10.0, [4, 0, 0], 2.0, 0.0], REASON_DIFFER),
        ([[0, 0, 0], 3.0, [10, 0, 0], 3.0, 0.0], REASON_SHORT),
    ],
    ids=["shorter than the height", "same place", "differ", "short"],
)
def test_a_measurement_that_places_nothing_says_what_the_station_says(case: list, reason: str) -> None:
    assert _trilaterate_js([case]) == [{"reason": reason}]
    assert trilaterate(case[0], case[2], case[1], case[3], case[4]) == reason


def _missing_by(kind: str, miss: float) -> list:
    """Two circles 10 m apart whose edges miss each other by ``miss``."""
    a, b = [0.0, 0.0, 0.0], [10.0, 0.0, 0.0]
    ra, rb = {"apart": (4.0, 6.0 - miss), "b inside a": (13.0 + miss, 3.0), "a inside b": (3.0, 13.0 + miss)}[kind]
    return [a, ra, b, rb, 0.0]


@pytest.mark.parametrize("kind", ["apart", "b inside a", "a inside b"])
@pytest.mark.parametrize("share", [0.9, 1.1], ids=["within", "beyond"])
def test_circles_that_just_miss_meet_within_the_tolerance(kind: str, share: float) -> None:
    case = _missing_by(kind, share * MEET_TOLERANCE)
    (out,) = _trilaterate_js([case])
    expected = trilaterate(case[0], case[2], case[1], case[3], case[4])
    if share > 1:
        assert out == {"reason": REASON_SHORT if kind == "apart" else REASON_DIFFER} and out["reason"] == expected
    else:
        # One place on the line between them, where the two edges come closest.
        assert out["pos"] == out["alt"]
        assert out["pos"] == pytest.approx(expected[0].tolist(), abs=1e-12)


def test_both_places_are_the_stations_two_places() -> None:
    rng = np.random.default_rng(7)
    cases = []
    for _ in range(300):
        a = [*rng.uniform(-10, 10, 2), rng.uniform(-1, 2)]
        b = [*rng.uniform(-10, 10, 2), rng.uniform(-1, 2)]
        p = [*rng.uniform(-12, 12, 2), rng.uniform(0, 2)]
        noise = rng.choice([0.0, 0.05, 0.3]) * rng.standard_normal(2)
        cases.append([a, math.dist(p, a) + noise[0], b, math.dist(p, b) + noise[1], p[2]])
    out = _trilaterate_js(
        [[list(map(float, c[0])), float(c[1]), list(map(float, c[2])), float(c[3]), float(c[4])] for c in cases]
    )
    placed = 0
    for case, got in zip(cases, out, strict=True):
        expected = trilaterate(case[0], case[2], case[1], case[3], case[4])
        if isinstance(expected, str):
            assert got == {"reason": expected}
            continue
        placed += 1
        pair = sorted([got["pos"], got["alt"]])
        np.testing.assert_allclose(pair, sorted([e.tolist() for e in expected]), atol=1e-9)
    assert 100 < placed < len(cases)


_TRI_FUNCTIONS = ("orderMarks", "wizTrilaterate", "triPlaced")
_A, _B = (-4.0, 2.0, 0.0), (4.0, 2.0, 0.0)
_SIDE_CAMERAS = {
    "front of house": [0.0, -14.0, 7.0, -22.0, 0.0, 0.0, 70.0],
    "back truss": [0.0, 20.0, 8.0, -25.0, 180.0, 0.0, 70.0],
    "stage left": [12.0, 3.0, 6.0, -25.0, -90.0, 0.0, 80.0],
    "overhead": [0.0, 2.0, 15.0, -75.0, 0.0, 0.0, 90.0],
}


def _pin(cam: list[float], place) -> list[float]:
    pin = project_points(np.array(cam), np.array([place], dtype=float), 1920.0, 1080.0)[0]
    assert not np.isnan(pin).any()
    return pin.tolist()


def _marks(cam: list[float], truth: list[float], *, unpinned: str = "") -> list[dict]:
    da, db = math.dist(truth, _A), math.dist(truth, _B)
    pins = {name: _pin(cam, place) for name, place in (("a", _A), ("b", _B), ("point", truth))}
    pins[unpinned] = None
    return [
        {"id": 1, "name": "A", "kind": "known", "pos": list(_A), "pin": pins["a"], "problem": ""},
        {"id": 2, "name": "B", "kind": "known", "pos": list(_B), "pin": pins["b"], "problem": ""},
        {"id": 3, "name": "T", "kind": "triangulated", "a": 1, "b": 2, "da": da, "db": db, "z": truth[2],
         "pin": pins["point"], "problem": ""},
    ]  # fmt: skip


@pytest.mark.parametrize(
    "truth", [[1.0, 8.0, 0.0], [1.0, -4.0, 0.0], [-2.5, 5.0, 0.5]], ids=["upstage", "downstage", "raised"]
)
@pytest.mark.parametrize("camera", list(_SIDE_CAMERAS), ids=list(_SIDE_CAMERAS))
def test_the_pin_picks_the_true_place_of_the_two(camera: str, truth: list[float]) -> None:
    cam = _SIDE_CAMERAS[camera]
    places = trilaterate(_A, _B, math.dist(truth, _A), math.dist(truth, _B), truth[2])
    assert not isinstance(places, str)
    assert min(np.linalg.norm(p - truth) for p in places) < 1e-9 and np.linalg.norm(places[0] - places[1]) > 1.0
    out = run_wizard_js(
        "triPlaced(marks)", functions=_TRI_FUNCTIONS, variables=("DISTANCE_SIGMA",), marks=_marks(cam, truth)
    )
    assert out["3"] == pytest.approx(truth, abs=1e-9)
    assert (out["1"], out["2"]) == (list(_A), list(_B))


@pytest.mark.parametrize("unpinned", ["point", "a", "b"])
def test_no_place_until_the_point_and_both_of_its_points_are_pinned(unpinned: str) -> None:
    marks = _marks(_SIDE_CAMERAS["front of house"], [1.0, 8.0, 0.0], unpinned=unpinned)
    out = run_wizard_js("triPlaced(marks)", functions=_TRI_FUNCTIONS, variables=("DISTANCE_SIGMA",), marks=marks)
    assert "3" not in out and set(out) == {"1", "2"}


def test_a_point_measured_from_a_triangulated_point_is_placed_after_it() -> None:
    cam = _SIDE_CAMERAS["back truss"]
    first, second = [1.0, 8.0, 0.0], [-3.0, 11.0, 0.0]
    marks = _marks(cam, first)
    chained = {
        "id": 4, "name": "U", "kind": "triangulated", "a": 3, "b": 1, "da": math.dist(second, first),
        "db": math.dist(second, _A), "z": 0.0, "pin": _pin(cam, second), "problem": "",
    }  # fmt: skip
    out = run_wizard_js(
        "triPlaced(marks)", functions=_TRI_FUNCTIONS, variables=("DISTANCE_SIGMA",), marks=[chained, *marks]
    )
    assert out["3"] == pytest.approx(first, abs=1e-9)
    assert out["4"] == pytest.approx(second, abs=1e-9)


# ---------------------------------------------------------------------------
# The station's reasons in the page's words
# ---------------------------------------------------------------------------

_REASON_FUNCTIONS = ("lenRough", "lenSmall", "lensMeasured", "offsetReason", "wordReasons")


def _reason_page(system: str = "metric", *, mode: str = "advanced", origin: str = "dsc", lens: bool = True,
                 touched: bool = False, saved: tuple = (0.0, 0.0)) -> str:  # fmt: skip
    return length_page(system, {}) + (
        f"\nvar S = {{ mode: {json.dumps(mode)}, rect: {{ origin: {json.dumps(origin)} }},"
        f" lensTouched: {json.dumps(touched)} }};"
        f" var LENS_ENABLED = {json.dumps(lens)}; var SAVED = {{ lens: {json.dumps(list(saved))} }};"
    )


@pytest.mark.parametrize(
    ("delta", "allowed", "per_axis", "said"),
    [
        ([0.0, -3.0, 0.0], 2.0, False, "3.0 m downstage of the camera position you entered (limit 2.0 m)."),
        (
            [2.0, -3.0, 0.5],
            2.5,
            True,
            "2.0 m stage left and 3.0 m downstage of the camera position you entered"
            " (3.6 m in total, limit 2.5 m per axis).",
        ),
        (
            [-1.0, 0.0, 2.5],
            1.0,
            False,
            "1.0 m stage right and 2.5 m above the camera position you entered (2.7 m in total, limit 1.0 m).",
        ),
        ([0.2, 4.0, -0.3], 1.0, False, "4.0 m upstage of the camera position you entered (limit 1.0 m)."),
    ],
    ids=["one axis", "two axes", "ending on height", "one axis carries it"],
)
def test_a_camera_off_its_entered_position_names_the_axes(delta, allowed, per_axis, said) -> None:  # noqa: ANN001
    out = run_wizard_js(
        "offsetReason(d, off, tol, perAxis)",
        functions=_REASON_FUNCTIONS,
        prelude=_reason_page(),
        d=delta,
        off=math.hypot(*delta),
        tol=allowed,
        perAxis=per_axis,
    )
    assert out == "The calculated camera is " + said


def test_the_offset_is_worded_in_feet_on_an_imperial_station() -> None:
    out = run_wizard_js(
        "offsetReason([0, -3, 0], 3, 2, false)", functions=_REASON_FUNCTIONS, prelude=_reason_page("imperial")
    )
    assert out == "The calculated camera is 9.8 ft downstage of the camera position you entered (limit 6.6 ft)."


@pytest.mark.parametrize(
    ("mode", "origin", "asked"), [("simple", "custom", True), ("simple", "dsc", False), ("advanced", "custom", False)]
)
def test_a_custom_origin_asks_whether_the_camera_was_measured_from_it(mode: str, origin: str, asked: bool) -> None:
    out = run_wizard_js(
        "offsetReason([0, -3, 0], 3, 2, false)",
        functions=_REASON_FUNCTIONS,
        prelude=_reason_page(mode=mode, origin=origin),
    )
    assert out.endswith(" Was the camera measured from the same point of origin as the rectangle's centre?") is asked


def test_every_reason_the_station_sends_is_worded() -> None:
    camera = CameraConfig(pos_x=0.0, pos_y=-10.0, pos_z=-0.5, pitch=5.0, yaw=0.0, roll=0.0, fov=150.0)
    fits = [MarkFit(1, (0.0, 0.0, 0.0)), MarkFit(2, (4.0, 0.0, 0.0)), MarkFit(3, (2.0, 5.0, 0.0), distance_error=0.3)]
    reasons = plausibility(camera, fits, {1: 7.5, 2: 0.4}, (0.0, -20.0, 8.0), True)
    assert {r["kind"] for r in reasons} == {"offset", "fov", "below", "up", "pixel", "distance"}
    out = run_wizard_js(
        "wordReasons(reasons, marks)",
        functions=_REASON_FUNCTIONS,
        prelude=_reason_page(),
        reasons=reasons,
        marks=[{"id": 1, "name": "DSL"}, {"id": 2, "name": "DSR"}, {"id": 3, "name": "P3"}],
    )
    assert out == [
        "The calculated camera is 10.0 m upstage and 8.5 m below the camera position you entered"
        " (13.1 m in total, limit 2.5 m per axis).",
        "The calculated field of view, 150.0°, is outside the usual range for cameras.",
        "The calculated camera is below the floor.",
        "The calculated camera points upwards.",
        "DSL disagrees with the other points by 7.5 px.",
        "P3's distances disagree with the picture by 30 cm.",
    ]


_UNMEASURED = (
    "The lens was not measured. Four corners fit with any lens, so a missing lens measurement does not show in their"
    " errors. Measure the lens in the Lens step, unless straight lines in the picture already look straight."
)


@pytest.mark.parametrize(
    ("page", "named"),
    [
        ({"mode": "simple"}, True),
        ({"mode": "advanced"}, False),
        ({"mode": "simple", "lens": False}, False),
        ({"mode": "simple", "touched": True}, False),
        ({"mode": "simple", "saved": (-0.2, 0.0)}, False),
    ],
    ids=["simple", "advanced", "lens step off", "measured here", "saved pair"],
)
def test_simple_names_a_lens_never_measured(page: dict, named: bool) -> None:
    out = run_wizard_js("wordReasons([], [])", functions=_REASON_FUNCTIONS, prelude=_reason_page(**page))
    assert out == ([_UNMEASURED] if named else [])


@pytest.mark.parametrize(
    ("page", "noted", "cautioned"),
    [
        ({"mode": "simple", "saved": (-0.1, 0.02)}, True, False),
        ({"mode": "simple", "saved": (0.0, 0.0)}, False, True),
        ({"mode": "simple", "saved": (-0.1, 0.02), "touched": True}, False, False),
        ({"mode": "simple", "saved": (-0.1, 0.02), "lens": False}, False, False),
        ({"mode": "advanced", "saved": (-0.1, 0.02)}, False, False),
    ],
    ids=["station's pair", "no pair", "measured here", "lens step off", "advanced"],
)
def test_simple_notes_the_stations_lens_kept_without_measuring(page: dict, noted: bool, cautioned: bool) -> None:
    out = run_wizard_js(
        "[stationLensOnly(), wordReasons([], [])]",
        functions=(*_REASON_FUNCTIONS, "stationLensOnly"),
        prelude=_reason_page(**page),
    )
    # The station's pair is a measured lens: a note, never the caution.
    assert out == [noted, [_UNMEASURED] if cautioned else []]


@pytest.mark.parametrize(("worst", "blocked"), [(20.0, False), (20.1, True), (60.0, True)])
@pytest.mark.parametrize(
    ("mode", "again"), [("simple", "place the corners again."), ("advanced", "pin the points again.")]
)
def test_a_pin_over_twenty_pixels_off_keeps_apply_off(worst: float, blocked: bool, mode: str, again: str) -> None:
    out = run_wizard_js(
        "[applyBlocked(s), cautionHtml(s)]",
        functions=("esc", "applyBlocked", "cautionHtml"),
        variables=("APPLY_MAX_PX",),
        prelude=f"var S = {{ mode: '{mode}' }};",
        s={"cam": {"fov": 60}, "worst": worst, "reasons": ["P2 disagrees with the other points by 9.0 px."]},
    )
    assert out[0] is blocked
    closing = (
        "Save and finish stays off while a point is more than 20 px off or behind the camera."
        if blocked
        else "You can still save the camera."
    )
    assert out[1].endswith(closing + " Check the measurements, then " + again + "</div>")


def test_no_solve_never_blocks_apply_by_its_error() -> None:
    assert run_wizard_js("[applyBlocked(null), applyBlocked({ worst: 99 })]", functions=("applyBlocked",),
                         variables=("APPLY_MAX_PX",)) == [False, False]  # fmt: skip


@pytest.mark.parametrize(
    ("saved", "here", "warned"),
    [
        ((1920, 1080), (1280, 720), False),
        ((1920, 1080), (1920, 1088), False),
        ((1920, 1080), (1280, 1024), True),
        ((1920, 1080), (1080, 1920), True),
        ((None, None), (1280, 1024), False),
        ((1920, 1080), (0, 0), False),
    ],
    ids=["same shape", "within 1 %", "other shape", "portrait", "no size saved", "no snapshot yet"],
)
def test_a_saved_lens_from_another_picture_shape_is_flagged(saved: tuple, here: tuple, warned: bool) -> None:
    out = run_wizard_js(
        "lensShapeWarning(c)",
        functions=("lensSize", "lensSized", "lensShapeWarning"),
        prelude=f"var imageWidth = {here[0]}, imageHeight = {here[1]};",
        c={"width": saved[0], "height": saved[1]},
    )
    if warned:
        assert out == (
            f"Measured on a {saved[0]} × {saved[1]} picture, this one is {here[0]} × {here[1]}."
            " The values only carry over at the same picture shape."
        )
    else:
        assert out == ""


def test_a_pin_behind_the_solved_camera_keeps_apply_off() -> None:
    out = run_wizard_js(
        "[applyBlocked(s), wordReasons([{ kind: 'behind', id: 2 }], [{ id: 2, name: 'P2' }])]",
        functions=(*_REASON_FUNCTIONS, "applyBlocked"),
        variables=("APPLY_MAX_PX",),
        prelude=_reason_page(),
        s={"cam": {"fov": 60}, "worst": 0.4, "behind": 1},
    )
    assert out == [True, ["P2 lies behind the calculated camera."]]


# ---------------------------------------------------------------------------
# Points with a problem: what each needs, what Review lists, and Apply waiting for them
# ---------------------------------------------------------------------------

# Every element the page asks for, created on first use; ``els`` holds what the page wrote.
_DOM = """
var els = {};
function $(id) {
  if (!els[id]) els[id] = { id: id, hidden: false, disabled: false, innerHTML: '', textContent: '', className: '',
    value: '', style: {}, open: false, lastChild: {}, appendChild: function() {}, setAttribute: function() {},
    getBoundingClientRect: function() { return { width: 0 }; }, classList: { toggle: function() {} } };
  return els[id];
}
var document = { getElementById: $, querySelector: function() { return null; },
  querySelectorAll: function() { return []; } };
"""
_MARK_FUNCTIONS = (
    "len",
    "lenField",
    "lenRough",
    "lenLimit",
    "andList",
    "rectVals",
    "rectProblem",
    "cornerXYZ",
    "currentMarks",
    "orderMarks",
    "marksIncomplete",
    "incompleteLines",
)
_MARK_VARS = ("exactLen", "CORNERS", "PIN_PROBLEM", "DISTANCE_SIGMA")
_RECT = {"w": "12", "d": "6", "origin": "dsc", "cx": "", "cy": "", "z": ""}


def _known(pid: int, x: str, y: str, z: str, pin: list | None = None) -> dict:
    return {"id": pid, "name": f"P{pid}", "kind": "known", "x": x, "y": y, "z": z, "pin": pin}


def _tri(pid: int, a: object, b: object, da: str = "7", db: str = "7", z: str = "", pin: list | None = None) -> dict:
    return {
        "id": pid,
        "name": f"P{pid}",
        "kind": "triangulated",
        "a": a,
        "b": b,
        "da": da,
        "db": db,
        "z": z,
        "pin": pin,
    }


_BASE_POINTS = [_known(1, "6", "0", "0", [100, 900]), _known(2, "-6", "0", "0", [1800, 900])]


def _incomplete(points: list, system: str = "metric") -> list:
    return run_wizard_js(
        "(function() { var s = { marks: currentMarks() }; orderMarks(s.marks);"
        " return [incompleteLines(s), marksIncomplete(s)]; })()",
        functions=_MARK_FUNCTIONS,
        variables=_MARK_VARS,
        prelude=length_page(system, {})
        + f"\nvar S = {json.dumps({'mode': 'advanced', 'points': points, 'rect': _RECT})};",
    )


@pytest.mark.parametrize(
    ("point", "line"),
    [
        (_known(3, "", "4", "0"), "P3 needs its X."),
        (_known(3, "1", "", ""), "P3 needs its Y and Z."),
        (_known(3, "", "", ""), "P3 needs its X, Y and Z."),
        (_known(3, "20000", "0", "0"), "P3 needs X, Y and Z within 10000.0 m of the point of origin."),
        (_tri(3, "", ""), "P3 needs the two points its distances are measured from."),
        (_tri(3, 1, 1), "P3 needs two different points to measure from."),
        (_tri(3, 1, 2, da=""), "P3 needs both distances, between 0 and 200 m."),
        (_tri(3, 1, 2, da="250"), "P3 needs both distances, between 0 and 200 m."),
        (_tri(3, 1, 2, z="abc", pin=[900, 500]), "P3 needs its Z."),
        (_tri(3, 1, 2), "P3 needs a pin."),
        (_tri(3, 1, 9, pin=[900, 500]), "P3: One of its points was deleted."),
    ],
    ids=[
        "X",
        "Y and Z",
        "all three",
        "out of reach",
        "no From",
        "same From",
        "no distance",
        "distance too long",
        "Z",
        "pin",
        "From deleted",
    ],
)
def test_review_names_each_incomplete_point_and_what_it_needs(point: dict, line: str) -> None:
    assert _incomplete([*_BASE_POINTS, point]) == [[line], True]


def test_complete_points_leave_nothing_to_name() -> None:
    points = [*_BASE_POINTS, _known(3, "0", "8", "1.2"), _tri(4, 1, 2, pin=[900, 500])]
    assert _incomplete(points) == [[], False]


def test_what_a_point_needs_is_worded_in_the_active_unit() -> None:
    assert _incomplete([*_BASE_POINTS, _tri(3, 1, 2, da="")], "imperial")[0] == [
        "P3 needs both distances, between 0 and 656 ft."
    ]


def test_simples_rectangle_problem_keeps_apply_off_without_a_line() -> None:
    # The no-solve notice already names what the rectangle needs.
    out = run_wizard_js(
        "(function() { var s = { marks: currentMarks() }; return [incompleteLines(s), marksIncomplete(s)]; })()",
        functions=_MARK_FUNCTIONS,
        variables=_MARK_VARS,
        prelude=length_page("metric", {}) + "\nvar S = { mode: 'simple', rect: { w: '12', d: '', origin: 'dsc' },"
        " cornerMoved: { DSL: true, DSR: true, USR: true, USL: true },"
        " cornerPins: { DSL: [1, 1], DSR: [2, 2], USR: [3, 3], USL: [4, 4] } };",
    )
    assert out == [[], True]


def test_the_stations_reason_for_a_point_is_listed_by_name() -> None:
    out = run_wizard_js(
        "(function() { var s = solveFromAnswer(marks, 200, data); return incompleteLines(s); })()",
        functions=(*_REASON_FUNCTIONS, "solveFromAnswer", "incompleteLines"),
        prelude=_reason_page(),
        marks=[
            {"id": 1, "name": "DSL", "pin": [1, 1], "problem": ""},
            {"id": 5, "name": "P5", "pin": [5, 5], "problem": ""},
        ],
        data={
            "camera": {"fov": 60},
            "largest_error": 0.4,
            "marks": [
                {"id": 1, "error": 0.4},
                {"id": 5, "reason": "A distance is shorter than the height difference between the points."},
            ],
        },
    )
    assert out == ["P5: A distance is shorter than the height difference between the points."]


_REVIEW_STUBS = """
var imageWidth = 0, imageHeight = 0, CAM_PRESETS = [], LENS_ENABLED = true;
function overlayUnit() { return 1; } function calibGrid() { return { w: 1, d: 1, spacing: 1, cx: 0, cy: 0, z: 0 }; }
function gridLines() { return []; } function poly() { return ''; } function originMarkup() { return ''; }
function originPixel() { return null; } function projectShown() { return null; }
function solvedParamsHtml() { return ''; }
function wizReadLensCoeff() { return 0; } function fmt() { return ''; } function reviewLensCoverage() { return ''; }
function pairsFor() { return []; } function distTable() { return ''; } function lenText() { return ''; }
function renderFsBar() {} function stationLensOnly() { return false; } function cautionHtml() { return 'caution'; }
"""
_REVIEW_FUNCTIONS = (
    "esc",
    "paramRows",
    "applyBlocked",
    "marksIncomplete",
    "incompleteLines",
    "incompleteHtml",
    "renderReview",
)
_COMPLETE = [
    {"id": 1, "name": "DSL", "kind": "known", "pin": [1, 1], "problem": ""},
    {"id": 2, "name": "DSR", "kind": "known", "pin": [2, 2], "problem": ""},
    {"id": 3, "name": "Truss leg", "kind": "known", "pin": None, "problem": ""},
]


def _review(marks: list, *, pending: bool = False) -> dict:
    solve = {"cam": {"fov": 60}, "worst": 0.4, "reasons": [], "positions": {}, "marks": marks}
    return run_wizard_js(
        "(function() { renderReview(); return { apply: $('btn-apply-finish').disabled,"
        " hidden: $('review-incomplete').hidden,"
        " incomplete: $('review-incomplete').innerHTML, calib: $('review-calib').innerHTML }; })()",
        functions=_REVIEW_FUNCTIONS,
        variables=("APPLY_MAX_PX",),
        prelude=_DOM + _REVIEW_STUBS,
        S={"mode": "advanced", "cam": None, "solve": solve},
        solvePending=pending,
    )


def test_review_turns_apply_on_for_a_finished_solve_of_complete_points() -> None:
    out = _review(_COMPLETE)
    assert (out["apply"], out["hidden"]) == (False, True)


def test_review_keeps_apply_off_while_a_solve_is_still_running() -> None:
    assert _review(_COMPLETE, pending=True)["apply"] is True


def test_review_lists_the_incomplete_points_and_keeps_apply_off() -> None:
    marks = [
        *_COMPLETE,
        {"id": 5, "name": "P5", "kind": "triangulated", "pin": None, "problem": "Pin it.", "need": "a pin"},
        {"id": 6, "name": "P6", "kind": "known", "pin": [6, 6], "problem": "Enter its X, Y and Z.", "need": "its X"},
    ]
    out = _review(marks)
    assert (out["apply"], out["hidden"]) == (True, False)
    assert out["incomplete"] == (
        'Some points are not complete.<div class="notice-sub"><ul class="caution-list"><li>P5 needs a pin.</li>'
        "<li>P6 needs its X.</li></ul>Complete or delete them in Camera Calibration.</div>"
    )
    # Points and Pinned count what Apply writes: every point, once each is complete.
    assert '<span class="param-label">Points</span><span class="param-value">5</span>' in out["calib"]
    assert '<span class="param-label">Pinned</span><span class="param-value">3</span>' in out["calib"]


# ---------------------------------------------------------------------------
# A pin behind the solved camera, and the pinned count the status line names
# ---------------------------------------------------------------------------

_ANSWER_MARKS = [
    {"id": 1, "name": "DSL", "pin": [1, 1], "problem": ""},
    {"id": 2, "name": "DSR", "pin": [2, 2], "problem": ""},
    {"id": 3, "name": "P3", "pin": [3, 3], "problem": ""},
    {"id": 4, "name": "P4", "pin": [4, 4], "problem": ""},
    {"id": 5, "name": "P5", "pin": [5, 5], "problem": ""},
    {"id": 6, "name": "P6", "pin": None, "problem": ""},
    {"id": 7, "name": "P7", "pin": [7, 7], "problem": "Enter its Z."},
]
_ANSWER = {
    "camera": {"fov": 60},
    "largest_error": 0.6,
    "count": 4,
    "marks": [{"id": i, "error": 0.5, "seen": [i, i]} for i in (1, 2, 3, 4)] + [{"id": 5, "error": None}, {"id": 6}],
}


def test_the_solve_counts_the_pins_the_page_sent_and_names_the_one_behind() -> None:
    out = run_wizard_js(
        "(function() { var s = solveFromAnswer(marks, 200, data);"
        " return [s.pinned, s.behind, s.behindIds, s.reasons]; })()",
        functions=(*_REASON_FUNCTIONS, "solveFromAnswer"),
        prelude=_reason_page(),
        marks=_ANSWER_MARKS,
        data=_ANSWER,
    )
    # P7 was not sent; P5 was, and the camera puts it behind itself: Review's Pinned counts it too.
    assert out == [5, 1, {"5": True}, ["P5 lies behind the calculated camera."]]


_CHIP_FUNCTIONS = ("lenSmall", "pinBehind", "pinFlagged", "chipFor")


@pytest.mark.parametrize(
    ("mark_id", "chip", "flagged"),
    [
        (5, '<span class="stat-chip warn">Behind the camera</span>', True),
        (1, '<span class="stat-chip ok">0.5 px</span>', False),
        (2, '<span class="stat-chip warn">6.0 px</span>', True),
    ],
    ids=["behind", "on its pin", "off its pin"],
)
def test_a_pin_behind_the_camera_is_chipped_and_drawn_as_off(mark_id: int, chip: str, flagged: bool) -> None:
    solve = {
        "cam": {"fov": 60},
        "res": {"1": {"px": 0.5}, "2": {"px": 6.0}, "5": {"px": None}},
        "behindIds": {"5": True},
    }
    out = run_wizard_js(
        "[chipFor({ id: id, kind: 'known', pin: [1, 1], problem: '' }, solve), pinFlagged(solve, id)]",
        functions=_CHIP_FUNCTIONS,
        variables=("DISTANCE_SIGMA",),
        prelude=length_page("metric", {}),
        solve=solve,
        id=mark_id,
    )
    assert out == [chip, flagged]


def test_a_pin_behind_the_camera_is_drawn_flagged_on_the_picture() -> None:
    out = run_wizard_js(
        '(function() { renderCalibOverlay(); return $(\'cal-pins\').innerHTML.match(/<g class="[^"]*"/g); })()',
        functions=("esc", "pinBehind", "pinFlagged", "renderCalibOverlay"),
        variables=("DISTANCE_SIGMA",),
        prelude=_DOM + _REVIEW_STUBS + "imageWidth = 1920; imageHeight = 1080;"
        " var S = { mode: 'advanced', pair: 0, selPin: null, selCorner: null, pinMenu: null };"
        " S.solve = { cam: { fov: 60 }, res: { 1: { px: 0.5 }, 5: { px: null } }, behindIds: { 5: true } };"
        " function currentMarks() {"
        " return [{ id: 1, name: 'DSL', pin: [10, 10] }, { id: 5, name: 'P5', pin: [50, 50] }]; }",
    )
    assert out == ['<g class="pin-handle"', '<g class="pin-handle flag"']


def test_the_status_line_names_the_pins_the_page_sent() -> None:
    out = run_wizard_js(
        "(function() { renderCalibResult(); return $('cal-status').textContent; })()",
        functions=("renderCalibResult",),
        prelude=_DOM + _REVIEW_STUBS + "var S = { mode: 'advanced', pair: 0 }; function lensMeasured() { return true; }"
        " S.solve = { cam: { fov: 60 }, worst: 0.6, pinned: 5, count: 4, reasons: [], marks: [] };",
    )
    assert out == "Calculated from 5 pinned points, largest error 0.6 px."


# ---------------------------------------------------------------------------
# Solving: an answer to the state before an edit is dropped
# ---------------------------------------------------------------------------


def test_an_edit_drops_the_answer_to_a_solve_already_in_flight() -> None:
    out = run_wizard_js(
        "(async function() { runSolve(); solveSoon();"
        " answer({ status: 200, json: function() { return Promise.resolve({ camera: {} }); } });"
        " await new Promise(function(r) { setImmediate(r); }); return [S.solve, solvePending]; })()",
        functions=("solveSoon", "runSolve"),
        prelude=_DOM + "var S = { solve: 'before the edit' }, solveSeq = 0, solveTimer = null, solvePending = false;"
        " var imageWidth = 1920;"
        " var answer; function fetch() { return new Promise(function(r) { answer = r; }); }"
        " function setTimeout() { return 1; } function clearTimeout() {} function renderCalibOverlay() {}"
        " function solveState() { return { marks: [], usable: [{ id: 1 }] }; } function solveBody() { return {}; }"
        " function solveFromAnswer() { return 'for the old state'; } function afterSolve() {}",
    )
    # The debounced solve for the edit is still to run.
    assert out == ["before the edit", True]


# ---------------------------------------------------------------------------
# Session: pins are stored as fractions of the snapshot they are in
# ---------------------------------------------------------------------------

_SESSION_PAGE = """
var stored = null;
var sessionStorage = { setItem: function(k, v) { stored = JSON.parse(v); }, getItem: function() { return raw; } };
var STORAGE_KEY = 'openfollow:wizard', WIZ_STEPS = ['video', 'lens', 'calib', 'review'];
var WIZ = { video: 0, lens: 1, calib: 2, review: 3 };
var currentStep = 2, lensLines = [], lensImageSize = null, lensFit = null, lensRejected = [], LENS_ENABLED = false;
var CAM_PRESETS = [];
function wizReadLensCoeff() { return 0; } function wizWriteLensCoeff() {}
function lensRestoredLine() { return true; } function lensRestoredPair() { return false; }
"""


def test_a_snapshot_of_another_size_stores_the_pins_as_the_same_fractions() -> None:
    out = run_wizard_js(
        "(function() { imageWidth = 1280; imageHeight = 720;"
        " showSnapshotOnCurrentStep('blob:x', {}); return stored; })()",
        functions=("pinsToSnapshot", "saveToSession", "showSnapshotOnCurrentStep"),
        variables=("CORNERS",),
        prelude=length_page("metric", {})
        + _DOM
        + _SESSION_PAGE
        + "var imageWidth = 1920, imageHeight = 1080, snapshotCanvas = null, FS = false;"
        " document.createElement = function() {"
        " return { getContext: function() { return { drawImage: function() {} }; } }; };"
        " function setPreviewVisibility() {} function placeStartQuad() {} function renderCalibOverlay() {}"
        " function runSolve() {}"
        # The Lens step stores the session the moment it has the new snapshot.
        " function lensOnSnapshot() { saveToSession(); }"
        " var S = { mode: 'advanced', simpleLoc: '', rect: {}, pinSize: [1920, 1080], nextId: 2,"
        " cornerPins: { DSL: null, DSR: null, USR: null, USL: null },"
        " cornerMoved: { DSL: false, DSR: false, USR: false, USL: false },"
        " points: [{ id: 1, name: 'P1', kind: 'known', x: '0', y: '0', z: '0', pin: [960, 270] }],"
        " lensTouched: false, lensLoaded: null };",
    )
    assert out["points"][0]["pin"] == [0.5, 0.25]


def test_pins_are_stored_as_fractions_of_the_snapshot_they_are_in() -> None:
    # Between a new snapshot's size arriving and the pins following it, they are still the old snapshot's pixels.
    out = run_wizard_js(
        "(function() { saveToSession(); return [stored.points[0].pin, stored.cornerPins.DSL]; })()",
        functions=("saveToSession",),
        variables=("CORNERS",),
        prelude=length_page("metric", {}) + _DOM + _SESSION_PAGE + "var imageWidth = 1280, imageHeight = 720;"
        " var S = { mode: 'advanced', simpleLoc: '', rect: {}, pinSize: [1920, 1080], nextId: 2,"
        " cornerPins: { DSL: [480, 540], DSR: null, USR: null, USL: null },"
        " cornerMoved: { DSL: true, DSR: false, USR: false, USL: false },"
        " points: [{ id: 1, name: 'P1', kind: 'known', x: '0', y: '0', z: '0', pin: [960, 270] }],"
        " lensTouched: false, lensLoaded: null };",
    )
    assert out == [[0.5, 0.25], [0.25, 0.5]]


@pytest.mark.parametrize(
    ("pin", "kept"),
    [([0.5, 0.25], True), ([0.0, 1.0], True), ([1.2, 0.5], False), ([0.5, -0.01], False), ([960, 540], False)],
    ids=["inside", "on the edge", "right of it", "above it", "pixels"],
)
def test_a_restored_pin_must_lie_on_the_snapshot(pin: list, kept: bool) -> None:
    raw = {
        "units": "metric",
        "mode": "advanced",
        "stepKey": "calib",
        "rect": {},
        "nextId": 3,
        "cornerPins": {"DSL": pin},
        "cornerMoved": {"DSL": True},
        "points": [{"id": 1, "name": "P1", "kind": "known", "x": "0", "y": "0", "z": "0", "pin": pin}],
    }
    out = run_wizard_js(
        "(function() { restoreFromSession(); return [S.points[0].pin, S.cornerPins.DSL, S.cornerMoved.DSL]; })()",
        functions=("pairOk", "fracPinOk", "restoreFromSession"),
        variables=("CORNERS",),
        prelude=length_page("metric", {}) + _DOM + _SESSION_PAGE + f"var raw = {json.dumps(json.dumps(raw))};"
        " var S = { mode: 'simple', rect: { origin: 'dsc' }, cornerPins: {}, cornerMoved: {}, points: [], nextId: 1 };",
    )
    assert out == ([pin, pin, True] if kept else [None, None, False])


# ---------------------------------------------------------------------------
# The saved lens list: a list that could not be read says so
# ---------------------------------------------------------------------------

_SAVE_ERROR = """
var window = { OpenFollow: { saveError: { UNREACHABLE: { error: 'The station did not answer.' },
  fromResponse: function(r) { return Promise.resolve({ error: 'Refused with ' + r.status + '.' }); } } } };
"""


def _fetch_answering(answer: str) -> str:
    return {
        "list": "function fetch() { return Promise.resolve({ ok: true, json: function() {"
        " return Promise.resolve({ calibrations: [{ name: 'Main', k1: -0.1, k2: 0.02, saved: '2026-09-12' }] });"
        " } }); }",
        "empty": "function fetch() { return Promise.resolve({ ok: true, json: function() {"
        " return Promise.resolve({ calibrations: [] }); } }); }",
        "refused": "function fetch() { return Promise.resolve({ ok: false, status: 500 }); }",
        "not json": "function fetch() { return Promise.resolve({ ok: true, json: function() {"
        " return Promise.reject(new SyntaxError('x')); } }); }",
        "no list": "function fetch() { return Promise.resolve({ ok: true, json: function() {"
        " return Promise.resolve({ error: 'x' }); } }); }",
        "down": "function fetch() { return Promise.reject(new TypeError('Failed to fetch')); }",
    }[answer]


@pytest.mark.parametrize(
    ("answer", "got"),
    [
        ("list", {"list": [{"name": "Main", "k1": -0.1, "k2": 0.02, "saved": "2026-09-12"}]}),
        ("empty", {"list": []}),
        ("refused", {"error": "Refused with 500."}),
        ("not json", {"error": ""}),
        ("no list", {"error": ""}),
        ("down", {"error": "The station did not answer."}),
    ],
)
def test_the_saved_list_or_why_it_could_not_be_read(answer: str, got: dict) -> None:
    out = run_wizard_js("lensSavedList()", functions=("lensSavedList",), prelude=_SAVE_ERROR + _fetch_answering(answer))
    assert out == got


_DIALOG_PAGE = """
var body = null, lensLines = [], imageWidth = 1920, imageHeight = 1080;
function openModal(o) { body = o.bodyHTML; }
function lensMeasured() { return true; } function wizLensPairValid() { return true; }
function wizReadLensCoeff() { return -0.1; }
"""
_DIALOG_FUNCTIONS = (
    "esc",
    "fmt",
    "lensVals",
    "lensSize",
    "lensSized",
    "lensShapeWarning",
    "lensOption",
    "lensSavedList",
    "lensListFailed",
)
_UNREAD = "The saved lens calibrations could not be read."


@pytest.mark.parametrize(("answer", "failed"), [("empty", False), ("refused", True), ("down", True)])
def test_the_load_dialog_says_when_the_saved_list_could_not_be_read(answer: str, failed: bool) -> None:
    out = run_wizard_js(
        "(async function() { lensLoadDialog(); await new Promise(function(r) { setImmediate(r); }); return body; })()",
        functions=_DIALOG_FUNCTIONS,
        prelude=_SAVE_ERROR
        + _DOM
        + _DIALOG_PAGE
        + _fetch_answering(answer)
        + "\n"
        + wizard_window_function("lensLoadDialog")
        + "\nvar lensLoadDialog = window.lensLoadDialog;",
    )
    assert (_UNREAD in out) is failed
    assert ("No lens calibrations are saved on this station." in out) is not failed


@pytest.mark.parametrize(("answer", "failed"), [("list", False), ("refused", True), ("down", True)])
def test_the_save_dialog_warns_that_it_may_replace_when_the_list_could_not_be_read(answer: str, failed: bool) -> None:
    out = run_wizard_js(
        "(async function() { lensSaveDialog(); await new Promise(function(r) { setImmediate(r); }); return body; })()",
        functions=_DIALOG_FUNCTIONS,
        prelude=_SAVE_ERROR
        + _DOM
        + _DIALOG_PAGE
        + _fetch_answering(answer)
        + "\n"
        + wizard_window_function("lensSaveDialog")
        + "\nvar lensSaveDialog = window.lensSaveDialog;",
    )
    warning = (
        '<div class="notice error" id="lens-list-error" role="alert">'
        + _UNREAD
        + '<div class="notice-sub">'
        + {"refused": "Refused with 500.", "down": "The station did not answer.", "list": ""}[answer]
        + " If a calibration with this name is saved on this station, saving replaces it.</div></div>"
    )
    assert (warning in out) is failed


# ---------------------------------------------------------------------------
# Rough amounts in the active unit
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("system", "simple", "advanced", "hint"),
    [
        (
            "metric",
            "An estimate within about 1.0 m is good enough.",
            "An estimate within about 2.0 m per axis is good enough.",
            "An estimate within about 2.0 m per axis is good enough.",
        ),
        (
            "imperial",
            "An estimate within about 3.3 ft is good enough.",
            "An estimate within about 6.6 ft per axis is good enough.",
            "An estimate within about 6.6 ft per axis is good enough.",
        ),
    ],
)
def test_the_camera_estimate_is_worded_in_the_active_unit(system: str, simple: str, advanced: str, hint: str) -> None:
    out = run_wizard_js(
        "(function() { var said = [];"
        " S.mode = 'simple'; renderCamGroup(); said.push($('cam-intro').innerHTML);"
        " S.mode = 'advanced'; renderCamGroup(); said.push($('cam-intro').innerHTML);"
        " renderReq(); said.push(shown); return said; })()",
        functions=(*_MARK_FUNCTIONS, "esc", "lenSuffix", "renderCamGroup", "renderReq"),
        variables=_MARK_VARS,
        prelude=length_page(system, {})
        + _DOM
        + "var S = { mode: 'simple', cam: null, camPartial: false, points: [], rect: {} };"
        " var SAVED = { max_marks: 12 }, shown = null;"
        " function showHint(area, h, sub) { shown = sub; } function lockSimpleSteps() {}",
    )
    assert simple in out[0] and advanced in out[1] and out[2] == hint


# ---------------------------------------------------------------------------
# Simple to Advanced: every corner becomes a known point
# ---------------------------------------------------------------------------


def _to_advanced(rect: dict) -> list:
    return run_wizard_js(
        "(function() { switchMode('advanced'); var s = { marks: currentMarks() };"
        " return [S.points.map(function(p) { return [p.name, p.x, p.y, p.z, p.pin]; }), incompleteLines(s)]; })()",
        functions=(*_MARK_FUNCTIONS, "switchMode"),
        variables=_MARK_VARS,
        prelude=length_page("metric", {})
        + _DOM
        + "function renderCalibPanels() {} function solveSoon() {} function saveToSession() {}"
        f" var S = {{ mode: 'simple', rect: {json.dumps(rect)}, points: [], nextId: 1,"
        " cornerMoved: { DSL: true, DSR: false, USR: false, USL: false },"
        " cornerPins: { DSL: [100, 900], DSR: [1800, 900], USR: [1500, 400], USL: [400, 400] } };",
    )


def test_a_rectangle_that_reads_gives_every_corner_its_position() -> None:
    points, lines = _to_advanced(_RECT)
    assert points == [
        ["DSL", "6", "0", "0", [100, 900]],
        ["DSR", "-6", "0", "0", None],
        ["USR", "-6", "6", "0", None],
        ["USL", "6", "6", "0", None],
    ]
    assert lines == []


@pytest.mark.parametrize(
    ("rect", "xyz", "need"),
    [
        ({**_RECT, "d": ""}, [["6", "", "0"], ["-6", "", "0"]], "its Y"),
        ({**_RECT, "w": "", "d": ""}, [["", "", "0"], ["", "", "0"]], "its X and Y"),
        ({**_RECT, "w": "300"}, [["", "0", "0"], ["", "0", "0"]], "its X"),
    ],
    ids=["no depth", "no size", "width out of range"],
)
def test_a_rectangle_that_does_not_read_leaves_the_corners_to_complete(rect: dict, xyz: list, need: str) -> None:
    points, lines = _to_advanced(rect)
    assert [p[0] for p in points] == ["DSL", "DSR", "USR", "USL"]
    assert [p[1:4] for p in points[:2]] == xyz
    # Placed corners keep their pins; the others are points to pin.
    assert [p[4] for p in points] == [[100, 900], None, None, None]
    assert lines == [f"{c} needs {need}." for c in ("DSL", "DSR", "USR", "USL")]


# ---------------------------------------------------------------------------
# Lens: the use / reject bubble beside each suggestion
# ---------------------------------------------------------------------------

_BUBBLE = ("lensPolyAt", "lensPlaceBubbles")
_BW, _BH, _GAP = 72, 38, 18
# A picture shown 1080 x 608 CSS px, as on a laptop.
_SW, _SH = 1080.0, 608.0


def _segment(a: tuple[float, float], b: tuple[float, float], n: int = 6) -> list[list[float]]:
    return [[a[0] + (b[0] - a[0]) * t / (n - 1), a[1] + (b[1] - a[1]) * t / (n - 1)] for t in range(n)]


_FLAT = _segment((300.0, 300.0), (800.0, 310.0))
_STEEP = _segment((540.0, 120.0), (560.0, 520.0))
_TOP = _segment((300.0, 22.0), (800.0, 24.0))
_LEFT = _segment((18.0, 120.0), (20.0, 500.0))
_FAN = [_segment((200.0, 380.0 + 14 * i), (880.0, 380.0 + 14 * i)) for i in range(4)]
# Bends up just past its anchor, like a curtain's edge: a bubble straight above would cover it.
_BENT = [[300.0, 300.0], [420.0, 300.0], [440.0, 160.0]]
# Short and against the right edge: a centred bubble would leave the picture, so it slides along its side.
_EDGE_SHORT = _segment((1040.0, 300.0), (1078.0, 300.0))


def _place(lines: list) -> list[dict]:
    return run_wizard_js(
        "lensPlaceBubbles(lines, lines.map(function(_, i) { return i; }), w, h)",
        functions=_BUBBLE,
        variables=("LENS_BUBBLE",),
        lines=lines,
        w=_SW,
        h=_SH,
    )


def _dist_to_line(p: tuple[float, float], line: list) -> float:
    return _distance_to_polyline(np.array(p, dtype=float), np.array(line, dtype=float))


def _distance_to_polyline(point: np.ndarray, polyline: np.ndarray) -> float:
    a, b = polyline[:-1], polyline[1:]
    ab = b - a
    t = np.clip(np.einsum("ij,ij->i", point - a, ab) / np.einsum("ij,ij->i", ab, ab), 0.0, 1.0)
    return float(np.min(np.hypot(*(a + t[:, None] * ab - point).T)))


def _anchor(b: dict) -> tuple[float, float]:
    """The point the pointer aims at: one pointer height and 1 px off the bubble's side."""
    edge = _GAP + 1
    return {
        "top": (b["at"], b["y"] + _BH + edge),
        "bottom": (b["at"], b["y"] - edge),
        "left": (b["x"] + _BW + edge, b["at"]),
        "right": (b["x"] - edge, b["at"]),
    }[b["side"]]


@pytest.mark.parametrize(
    "lines",
    [[_FLAT], [_STEEP], [_TOP], [_LEFT], [_BENT], [_EDGE_SHORT], _FAN, [_FLAT, _STEEP, _TOP, _LEFT]],
    ids=["flat", "steep", "at the top", "at the left", "bent", "short at the edge", "a fan", "four"],
)
def test_a_bubble_points_at_its_line_from_inside_the_picture_and_never_covers_it(lines: list) -> None:
    placed = _place(lines)
    assert sorted(b["i"] for b in placed) == list(range(len(lines)))
    for b in placed:
        line = lines[b["i"]]
        assert b["x"] >= 6 and b["y"] >= 6 and b["x"] + _BW <= _SW - 6 and b["y"] + _BH <= _SH - 6, b
        # The anchor sits on the line, so the rounded tip stops 4 px short of it.
        assert _dist_to_line(_anchor(b), line) < 0.5, b
        # Nothing of its own line under the bubble, nor within 4 px of it.
        grown = np.array([b["x"] - 4, b["y"] - 4, b["x"] + _BW + 4, b["y"] + _BH + 4])
        pts = np.array(_segment(tuple(line[0]), tuple(line[-1]), 400))
        inside = (pts[:, 0] > grown[0]) & (pts[:, 0] < grown[2]) & (pts[:, 1] > grown[1]) & (pts[:, 1] < grown[3])
        assert not inside.any(), b
        # The pointer leaves from the flat part of the side, clear of the rounded corners.
        lo, hi = (b["x"], b["x"] + _BW) if b["side"] in ("top", "bottom") else (b["y"], b["y"] + _BH)
        half = _GAP / math.sqrt(3)
        assert lo + 8 + half - 1e-6 <= b["at"] <= hi - 8 - half + 1e-6, b


def test_bubbles_never_overlap_each_other() -> None:
    placed = _place(_FAN + [_FLAT, _STEEP])
    boxes = [(b["x"], b["y"], b["x"] + _BW, b["y"] + _BH) for b in placed]
    for i, a in enumerate(boxes):
        for b in boxes[i + 1 :]:
            assert min(a[2], b[2]) - max(a[0], b[0]) <= 0 or min(a[3], b[3]) - max(a[1], b[1]) <= 0, (a, b)


@pytest.mark.parametrize(
    ("line", "sides"),
    [
        (_FLAT, {"top", "bottom"}),
        # Too short to reach a bubble beside it: the side still follows the line's direction.
        (_segment((500.0, 300.0), (540.0, 300.0)), {"top", "bottom"}),
        (_STEEP, {"left", "right"}),
        (_TOP, {"bottom"}),
        (_LEFT, {"right"}),
    ],
    ids=[
        "flat above or below",
        "short and flat above or below",
        "steep beside",
        "flat at the top flips below",
        "steep at the left flips right",
    ],
)
def test_a_bubble_takes_the_side_its_line_leaves_room_on(line: list, sides: set) -> None:
    assert _place([line])[0]["side"] in sides


def test_a_bubble_anchors_off_the_middle_of_its_line() -> None:
    b = _place([_FLAT])[0]
    along = (_anchor(b)[0] - _FLAT[0][0]) / (_FLAT[-1][0] - _FLAT[0][0])
    # Where the line's centre point sits once used stays visible.
    assert along == pytest.approx(0.38, abs=0.01)


def _outline_numbers(d: str) -> list[float]:
    return [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", d)]


@pytest.mark.parametrize("side", ["top", "bottom", "left", "right"])
def test_the_pointer_is_equilateral_with_a_rounded_tip_four_pixels_short(side: str) -> None:
    x, y, at = 100.0, 200.0, 136.0 if side in ("top", "bottom") else 219.0
    d = run_wizard_js(
        "lensBubbleOutline(x, y, side, at)",
        functions=("lensBubbleOutline",),
        variables=("LENS_BUBBLE",),
        x=x,
        y=y,
        side=side,
        at=at,
    )
    # One closed path: the bubble's four 8 px corners and the pointer's one 3 px tip.
    assert d.startswith("M") and d.endswith(" Z")
    assert d.count("A8 8 0 0 1") == 4 and d.count("A3 3 0 0 1") == 1
    tip = re.search(r"L(-?[\d.]+) (-?[\d.]+) A3 3 0 0 1 (-?[\d.]+) (-?[\d.]+)", d)
    assert tip
    p1, p2 = np.array(_outline_numbers(tip.group(0))[:2]), np.array(_outline_numbers(tip.group(0))[-2:])
    # An equilateral pointer 18 px high: its apex 18 px off the side, the rounding cut 3 * sqrt(3) px back
    # along each flank. The arc then reaches 3 px short of the apex: 4 px short of the line at 19 px.
    normal = {"top": (0, 1), "bottom": (0, -1), "left": (1, 0), "right": (-1, 0)}[side]
    edge = {"top": (at, y + _BH), "bottom": (at, y), "left": (x + _BW, at), "right": (x, at)}[side]
    apex = np.array(edge) + _GAP * np.array(normal)
    cut = 3 * math.sqrt(3)
    for p in (p1, p2):
        assert np.hypot(*(p - apex)) == pytest.approx(cut, abs=0.11)
        assert float(np.dot(apex - p, normal)) == pytest.approx(cut * math.cos(math.radians(30)), abs=0.11)
    centre_to_apex = 3 / math.sin(math.radians(30))
    assert _GAP - (centre_to_apex - 3) + 4 == pytest.approx(_GAP + 1)


@pytest.mark.parametrize(
    ("open_ids", "keep", "focused"),
    [
        ([0, 2, 5], {"i": 1, "cls": "lb-use"}, 2),
        ([0, 2, 5], {"i": 5, "cls": "lb-reject"}, 0),
        ([], {"i": 3, "cls": "lb-use"}, None),
        ([0, 2, 5], {"same": 2, "cls": "lb-use"}, 2),
        ([0, 5], {"same": 2, "cls": "lb-use"}, None),
        ([0, 2], None, None),
    ],
    ids=["next", "round to the first", "none left", "the same after a redraw", "the same is gone", "no choice"],
)
def test_after_a_choice_by_keyboard_the_next_bubble_takes_the_focus(open_ids: list, keep: dict, focused) -> None:  # noqa: ANN001
    assert run_wizard_js("lensFocusTarget(o, k)", functions=("lensFocusTarget",), o=open_ids, k=keep) == focused


# The bubbles drawn on a page that records the order they enter it and what takes the focus.
_BUBBLE_PAGE = r"""
var imageWidth = 1080, imageHeight = 608, lensHot = -1, lensFocusAfter = null, lensRefocus = false;
var lensCandidates = [], lensSelected = null, focused = null, refocusing = null, bubbles = [];
function lensOpenCandidates() { return open; }
function el(name) { return { focus: function() { focused = name; refocusing = lensRefocus; } }; }
var box = { innerHTML: '', contains: function() { return false; },
  insertAdjacentHTML: function(where, html) { bubbles.push(+/data-candidate="(\d+)"/.exec(html)[1]); },
  querySelector: function(sel) {
    var m = /data-candidate="(\d+)"\] \.(lb-\w+)/.exec(sel);
    return m && bubbles.indexOf(+m[1]) >= 0 ? el('bubble ' + m[1] + ' ' + m[2]) : null;
  } };
var shapes = { innerHTML: '', setAttribute: function() {}, classList: { toggle: function() {} },
  insertAdjacentHTML: function() {} };
var els = { 'lens-container': { getBoundingClientRect: function() { return { width: 1080, height: 608 }; } },
  'lens-bubbles': box, 'lens-tails': shapes, 'lens-overlay': el('the picture') };
function $(id) { return els[id]; }
var document = { activeElement: null, querySelector: function(sel) {
  var m = /\[data-line="(\d+)"\]\[data-point="(\d+)"\]/.exec(sel);
  return m && +m[1] < traced ? el('line ' + m[1] + ' point ' + m[2]) : null;
} };
"""
_BUBBLE_DOM = (
    "renderLensBubbles",
    "lensFocusTarget",
    "lensFocusLineOrPicture",
    "lensFocusEl",
    "lensPlaceBubbles",
    "lensPolyAt",
    "lensBubbleOutline",
)
# Numbered short, long, middling: placed longest first.
_THREE = [_segment((100.0, 500.0), (260.0, 500.0)), _FLAT, _segment((150.0, 120.0), (500.0, 130.0))]


def _bubbles(open_ids: list, keep: dict | None = None, selected: dict | None = None, traced: int = 0) -> dict:
    return run_wizard_js(
        "(function() { lensFocusAfter = keep; lensSelected = selected;"
        " lensCandidates = cands.map(function(c) { return { points: c }; });"
        " renderLensBubbles(); return { bubbles: bubbles, focused: focused, refocusing: refocusing,"
        " placed: lensPlaceBubbles(cands.map(function(c) { return c; }), open, 1080, 608).map(function(b) {"
        " return b.i; }), refocus: lensRefocus }; })()",
        functions=_BUBBLE_DOM,
        variables=("LENS_BUBBLE", "LENS_CHECK_SVG", "LENS_CROSS_SVG"),
        prelude=f"var traced = {traced};" + _BUBBLE_PAGE,
        cands=_THREE,
        open=open_ids,
        keep=keep,
        selected=selected,
    )


def test_bubbles_enter_the_page_in_suggestion_order_while_the_longest_line_is_placed_first() -> None:
    out = _bubbles([0, 1, 2])
    assert out["placed"] == [1, 2, 0]
    # Tab walks the bubbles by number, as the focus does after a choice.
    assert out["bubbles"] == [0, 1, 2]


@pytest.mark.parametrize(
    ("open_ids", "keep", "selected", "focused"),
    [
        ([0, 2], {"i": 0, "cls": "lb-use"}, {"line": 0, "point": None}, "bubble 2 lb-use"),
        ([], {"i": 1, "cls": "lb-use"}, {"line": 0, "point": None}, "line 0 point 0"),
        ([], {"i": 1, "cls": "lb-reject"}, {"line": 0, "point": None}, "the picture"),
        ([], {"i": 1, "cls": "lb-reject"}, None, "the picture"),
        ([], None, {"line": 0, "point": None}, None),
    ],
    ids=["the next bubble", "the last used", "the last rejected", "the last rejected, nothing selected", "no choice"],
)
def test_the_last_suggestion_decided_by_keyboard_leaves_the_focus_on_the_page(
    open_ids: list, keep: dict | None, selected: dict | None, focused: str | None
) -> None:
    out = _bubbles(open_ids, keep, selected, traced=1)
    assert out["focused"] == focused
    # Giving the focus to a point is not a new selection of it; the flag never stays up.
    assert out["refocusing"] is (None if focused is None else not focused.startswith("bubble"))
    assert out["refocus"] is False


_REJECT_PAGE = """
var lensCandidates = [], lensRejected = [], lensHot = 3, saved = 0, rendered = 0, loaded = 0;
function renderLens() { rendered++; } function saveToSession() { saved++; } function loadSnapshot() { loaded++; }
function lensHotCand(i) { lensHot = i; } var window = {};
"""
_CANDS = [
    {"samples": [[10, 10], [20, 10], [30, 10], [40, 10], [50, 10]], "used": False, "rejected": False},
    {"samples": [[10, 90], [20, 90], [30, 90], [40, 90], [50, 90]], "used": True, "rejected": False},
    {"samples": [[90, 10], [90, 20], [90, 30], [90, 40], [90, 50]], "used": False, "rejected": False},
]


def test_a_rejected_suggestion_leaves_the_picture_until_refresh_image() -> None:
    out = run_wizard_js(
        "(function() { lensCandidates = cands; var before = lensOpenCandidates();"
        " lensRejectCandidate(0); lensRejectCandidate(1);"
        " var after = lensOpenCandidates(), ends = JSON.parse(JSON.stringify(lensRejected)); window.lensRefreshImage();"
        " return [before, after, ends, lensHot, saved, lensOpenCandidates(), lensRejected, loaded]; })()",
        functions=("lensOpenCandidates", "lensRejectCandidate"),
        prelude=_REJECT_PAGE + wizard_window_function("lensRefreshImage"),
        cands=_CANDS,
    )
    before, after, ends, hot, saved, refreshed, kept, loaded = out
    assert before == [0, 2] and after == [2]
    # A used line is not rejected; the rejected one is kept by its ends for the next snapshot.
    assert ends == [[[10, 10], [50, 10]]]
    assert (hot, saved) == (-1, 2)
    assert (refreshed, kept, loaded) == ([0, 2], [], 1)


@pytest.mark.parametrize(
    ("samples", "same"),
    [
        ([[12, 8], [0, 0], [0, 0], [0, 0], [48, 14]], True),
        ([[48, 14], [0, 0], [0, 0], [0, 0], [12, 8]], True),
        ([[35, 10], [0, 0], [0, 0], [0, 0], [50, 10]], False),
    ],
    ids=["same ends", "reversed", "one end moved"],
)
def test_a_new_snapshots_suggestion_is_matched_to_a_rejection_by_its_ends(samples: list, same: bool) -> None:
    out = run_wizard_js("lensSameEnds(s, [10, 10], [50, 10])", functions=("lensSameEnds",), s=samples)
    assert out is same


def test_the_lens_tab_key_steps_through_the_points_of_its_line() -> None:
    out = run_wizard_js(
        "(function() { var seen = [], stops = 0;"
        " function press(shift) { var e = { key: 'Tab', shiftKey: shift, preventDefault: function() { stops++; },"
        " stopPropagation: function() {} }; lensTabKey(e); seen.push(lensSelected.point); }"
        " [false, false, false, true, true, true, true].forEach(press);"
        " lensHold = false; press(false); return [seen, stops]; })()",
        functions=("lensTabKey",),
        prelude="var lensHold = true, lensSelected = { line: 1, point: 2 }, currentStep = 1, WIZ = { lens: 1 };"
        " var lensLines = [{}, {}]; function renderLens() {}",
    )
    # Round from the last point to the first and back; without a held point Tab is the browser's.
    assert out == [[3, 4, 0, 4, 3, 2, 1, 1], 7]


_POINT_FOCUS_PAGE = """
var els = {};
function $(id) { if (!els[id]) els[id] = { innerHTML: '' }; return els[id]; }
var lensHintText = '', lensHintHtml = '', S = { lensLoaded: null }, lensPending = null, lensFit = null;
var lensLines = [{}, {}];
function lensFitApplies() { return true; } function lensOpenCandidates() { return []; }
var currentStep = 1, WIZ = { lens: 1 }, renders = 0;
function renderLens() { renders++; renderLensHint(); }
var handle = { dataset: { line: '1', point: '2' } };
var focusEvent = { target: { closest: function() { return handle; } } };
"""


@pytest.mark.parametrize(
    ("before", "selected", "held", "renders", "steps"),
    [
        ("", {"line": 1, "point": 2}, True, 1, [3, 4, 0, 4]),
        ("lensSelected = { line: 1, point: 2 };", {"line": 1, "point": 2}, True, 1, [3, 4, 0, 4]),
        ("lensSelected = { line: 1, point: 2 }; lensHold = true;", {"line": 1, "point": 2}, True, 0, [3, 4, 0, 4]),
        ("lensSelected = { line: 1, point: null }; lensRefocus = true;", {"line": 1, "point": None}, False, 0, []),
        ("lensDrag = {};", None, False, 0, []),
    ],
    ids=["reached with Tab", "its hold ended", "already held", "a redraw giving the focus back", "a press"],
)
def test_a_point_reached_with_tab_is_selected_and_held(
    before: str, selected: dict | None, held: bool, renders: int, steps: list
) -> None:
    out = run_wizard_js(
        "(function() { " + before + " lensPointFocusIn(focusEvent); lensRefocus = false;"
        " var got = [lensSelected, lensHold, renders, lensHintText], seq = [];"
        " if (lensHold) [false, false, false, true].forEach(function(shift) {"
        " lensTabKey({ key: 'Tab', shiftKey: shift, preventDefault: function() {}, stopPropagation: function() {} });"
        " seq.push(lensSelected.point); });"
        " return got.concat([seq]); })()",
        functions=("esc", "renderLensHint", "lensPointFocusIn", "lensTabKey"),
        prelude="var lensSelected = null, lensHold = false, lensRefocus = false, lensDrag = null;" + _POINT_FOCUS_PAGE,
    )
    hint = "Move the point exactly onto the edge." if held else ""
    # Held as one clicked: the loupe and the hint are on it, and Tab steps along its line, round to the start.
    assert out == [selected, held, renders, hint if renders else "", steps]


@pytest.mark.parametrize(
    ("held", "connected", "on_point", "dropped"),
    [
        (True, True, False, 1),
        (True, False, False, 0),
        (True, True, True, 0),
        (False, True, False, 0),
    ],
    ids=["focus elsewhere", "the old handle of a redraw", "another point", "nothing held"],
)
def test_a_held_point_is_dropped_by_a_focus_elsewhere_only(
    held: bool, connected: bool, on_point: bool, dropped: int
) -> None:
    out = run_wizard_js(
        "(function() { lensFocusElsewhere({ target: { isConnected: connected,"
        " closest: function() { return onPoint ? {} : null; } } }); return drops; })()",
        functions=("lensFocusElsewhere",),
        prelude=f"var lensHold = {json.dumps(held)}, drops = 0; function dropLensPoint() {{ drops++; }}",
        connected=connected,
        onPoint=on_point,
    )
    assert out == dropped


_REFOCUS_PAGE = r"""
var document = { querySelector: function(sel) {
  var m = /data-line="(\d+)"\]\[data-point="(\d+)"/.exec(sel);
  return m && { focus: function() { focused = 'line ' + m[1] + ' point ' + m[2]; refocusing = lensRefocus; } };
} };
"""


@pytest.mark.parametrize(
    ("selected", "focused"),
    [({"line": 1, "point": 3}, "line 1 point 3"), ({"line": 1, "point": None}, "line 1 point 0"), (None, None)],
    ids=["a point", "a whole line", "nothing"],
)
def test_a_redraw_gives_the_focus_back_to_the_selection_without_selecting_anew(
    selected: dict | None, focused: str | None
) -> None:
    out = run_wizard_js(
        "(function() { lensSelected = selected; lensFocusSelected(); return [focused, refocusing, lensRefocus]; })()",
        functions=("lensFocusSelected", "lensFocusEl"),
        prelude="var lensSelected = null, lensRefocus = false, focused = null, refocusing = null;" + _REFOCUS_PAGE,
        selected=selected,
    )
    assert out == [focused, None if focused is None else True, False]


# ---------------------------------------------------------------------------
# Lens: the hint box
# ---------------------------------------------------------------------------

_HINT_PAGE = """
var els = {};
function $(id) { if (!els[id]) els[id] = { innerHTML: '' }; return els[id]; }
var lensHintText = '', lensHintHtml = '';
function lensFitApplies(fit) { return fit.rating !== 'low'; }
function lensOpenCandidates() { return state.open; }
var S = { lensLoaded: state.loaded }, lensPending = state.pending, lensHold = state.hold, lensSelected = state.sel;
var lensLines = state.lines, lensFit = state.fit;
"""
_LINE = {"misfit": False}
_AGREE = "The result may already be good. If possible, add a line near the top edge to confirm it."


def _hint_state(**kw: object) -> dict:
    return {"open": [], "loaded": None, "pending": None, "hold": False, "sel": None, "lines": [], "fit": None, **kw}


@pytest.mark.parametrize(
    ("state", "heading", "sub"),
    [
        (_hint_state(pending=[1, 2], open=[1]), "Click the other end of the straight edge.", "Esc cancels the line."),
        (
            _hint_state(hold=True, sel={"line": 0, "point": 2}, lines=[_LINE], open=[1]),
            "Move the point exactly onto the edge.",
            "Drag it, or use the arrow keys while the loupe shows the edge. Press Enter when it is in the right place.",
        ),
        (
            _hint_state(sel={"line": 0, "point": 2}, lines=[_LINE], open=[1]),
            "Check the suggested lines.",
            "Use each one that is straight in reality. Reject the others, such as a curtain, a cloth or a cable."
            " 1 left to check. Or load a saved lens calibration.",
        ),
        (
            _hint_state(open=[1, 4, 7]),
            "Check the suggested lines.",
            "Use each one that is straight in reality. Reject the others, such as a curtain, a cloth or a cable."
            " 3 left to check. Or load a saved lens calibration.",
        ),
        (
            _hint_state(loaded={"name": "FOH"}),
            "A saved lens calibration is loaded.",
            "Press Next, or draw straight lines to measure the lens again.",
        ),
        (
            _hint_state(),
            "Draw straight lines on the picture.",
            "Click the start and the end of an edge that is straight in reality, such as a stage edge, a tape line"
            " or a truss. Then move its points exactly onto the edge. Or load a saved lens calibration.",
        ),
        (
            _hint_state(lines=[_LINE, {"misfit": True}], fit={"rating": "good", "hint": ""}),
            "Check the line that does not fit.",
            "It is drawn in the warning colour. Move its points onto the edge, or delete it if it is not straight"
            " in reality.",
        ),
        (
            _hint_state(lines=[_LINE] * 4, fit={"rating": "excellent", "hint": ""}),
            "The lens is measured.",
            "Press Next.",
        ),
        (
            _hint_state(
                lines=[_LINE] * 4,
                fit={"rating": "good", "hint": "If possible, add lines near the top and left edges to confirm it."},
            ),
            "The lens is measured.",
            "Press Next. If possible, add lines near the top and left edges to confirm it.",
        ),
        (
            _hint_state(lines=[_LINE] * 4, fit={"rating": "okay", "hint": _AGREE}),
            "The result may already be good.",
            "If possible, add a line near the top edge to confirm it. Or press Next.",
        ),
        (
            _hint_state(lines=[_LINE] * 3, fit={"rating": "okay", "hint": "Add a line near the top edge."}),
            "Improve the lines, or add more.",
            "Select a point and move it exactly onto the edge. Lines near the edges and corners of the picture help"
            " the most. Add a line near the top edge.",
        ),
        (
            _hint_state(
                lines=[_LINE], fit={"rating": "low", "hint": "Add a second line in another part of the picture."}
            ),
            "Draw more straight lines.",
            "Click the start and the end of a straight edge. Then move its points exactly onto the edge. Add a second"
            " line in another part of the picture.",
        ),
    ],
    ids=[
        "line started",
        "point selected",
        "point focused, not held",
        "suggestions left",
        "loaded",
        "no lines",
        "misfit",
        "measured",
        "measured with weak edges",
        "lines agree",
        "applied",
        "not usable",
    ],  # fmt: skip
)
def test_the_lens_hint_names_the_one_next_thing_to_do(state: dict, heading: str, sub: str) -> None:
    out = run_wizard_js(
        "(function() { renderLensHint(); return [$('lens-hint').innerHTML, lensHintText]; })()",
        functions=("esc", "renderLensHint"),
        prelude="var state = " + json.dumps(state) + ";" + _HINT_PAGE,
    )
    assert out == [f'{heading}<div class="notice-sub">{sub}</div>', heading]


# The hint box and the full-screen bar, on a page that counts every write to their words.
_LIVE_PAGE = """
var els = {}, writes = {};
function $(id) {
  if (!els[id]) {
    var words = { innerHTML: '', textContent: '' };
    els[id] = { classList: { toggle: function() {} } };
    ['innerHTML', 'textContent'].forEach(function(k) {
      Object.defineProperty(els[id], k, { get: function() { return words[k]; },
        set: function(v) { words[k] = v; writes[id] = (writes[id] || 0) + 1; } });
    });
  }
  return els[id];
}
var lensHintText = '', lensHintHtml = '', FS = { key: 'lens' }, loupe = 'shown';
function lensFitApplies(fit) { return fit.rating !== 'low'; }
function lensOpenCandidates() { return []; }
function hideLoupe() { loupe = 'hidden'; }
var S = { lensLoaded: null }, lensPending = null, lensHold = true, lensSelected = { line: 0, point: 2 };
var lensLines = [{ misfit: false }], lensFit = null;
"""
_LIVE = ("esc", "renderLensHint", "renderFsBar", "dropLensPoint")


@pytest.mark.parametrize("full_screen", [True, False], ids=["full screen", "on the page"])
def test_ending_a_hold_moves_the_hint_and_the_full_screen_bar_on(full_screen: bool) -> None:
    out = run_wizard_js(
        "(function() { if (!fs) FS = null; renderLensHint(); renderFsBar(); var before = $('fs-hint').textContent;"
        " dropLensPoint(); return [before, $('lens-hint').innerHTML, $('fs-hint').textContent, loupe]; })()",
        functions=_LIVE,
        prelude=_LIVE_PAGE,
        fs=full_screen,
    )
    before, hint, bar, loupe = out
    # A click or a focus elsewhere ends the hold without a redraw of the lines: the words still follow it.
    assert before == ("Move the point exactly onto the edge." if full_screen else "")
    assert hint.startswith("Draw more straight lines.<div")
    assert bar == ("Draw more straight lines." if full_screen else "")
    assert loupe == "hidden"


def test_the_hint_and_the_full_screen_bar_are_only_rewritten_when_their_words_change() -> None:
    out = run_wizard_js(
        "(function() { var seen = [];"
        " function draw() { renderLensHint(); renderFsBar(); seen.push([writes['lens-hint'], writes['fs-hint']]); }"
        " draw(); draw(); lensHold = false; draw(); draw(); return seen; })()",
        functions=_LIVE,
        prelude=_LIVE_PAGE,
    )
    # Both are polite live regions: a redraw with the same words is not read out again.
    assert out == [[1, 1], [1, 1], [2, 2], [2, 2]]


@pytest.mark.parametrize(
    ("rating", "label"),
    [("excellent", "Excellent"), ("good", "Good"), ("okay", "Fair"), ("medium", "Poor"), ("low", "Not usable")],
)
def test_the_lens_ratings_are_named_for_the_operator(rating: str, label: str) -> None:
    assert run_wizard_js(f"LENS_RATING_LABEL['{rating}']", variables=("LENS_RATING_LABEL",)) == label


# ---------------------------------------------------------------------------
# Simple: the four steps open in order, the point of origin is chosen
# ---------------------------------------------------------------------------

_LOCK_PAGE = """
var els = {};
function $(id) {
  if (!els[id]) els[id] = { hidden: false, disabled: false, inert: false, cls: {},
    classList: { toggle: function(c, on) { els[id].cls[c] = !!on; } } };
  return els[id];
}
var tips = [$('tip-simple')];
var document = { querySelectorAll: function() { return tips; } };
"""
_LOCKED = ["sstep-2", "sstep-3", "corners-q", "cal-container", "cal-simple-corners"]


def _locks(mode: str, rect: dict, loc: str = "", moved: tuple[bool, ...] = (False,) * 4) -> dict:
    corners = dict(zip(("DSL", "DSR", "USR", "USL"), moved, strict=True))
    return run_wizard_js(
        "(function() { lockSimpleSteps(); var ids = " + json.dumps(_LOCKED) + ";"
        " return { locked: ids.filter(function(id) { return els[id].cls['step-locked'] && els[id].inert; }),"
        " open: ids.filter(function(id) { return !els[id].cls['step-locked'] && !els[id].inert; }),"
        " tip: !!els['tip-simple'].cls['step-locked'], refresh: $('cal-refresh').disabled,"
        " reset: $('cal-reset').hidden, next: $('cal-next').disabled }; })()",
        functions=(*_MARK_FUNCTIONS, "lockSimpleSteps"),
        variables=_MARK_VARS,
        prelude=length_page("metric", {})
        + _LOCK_PAGE
        + f"var S = {{ mode: '{mode}', simpleLoc: '{loc}', rect: {json.dumps(rect)},"
        f" cornerMoved: {json.dumps(corners)} }};",
    )


_EMPTY = {"w": "", "d": "", "origin": "", "cx": "", "cy": "", "z": ""}


@pytest.mark.parametrize(
    ("rect", "loc", "open_upto"),
    [
        (_EMPTY, "", 0),
        (_EMPTY, "back", 1),
        ({**_EMPTY, "w": "12"}, "", 1),
        ({**_EMPTY, "w": "12", "d": "6"}, "", 2),
        ({**_EMPTY, "w": "12", "d": "300"}, "back", 1),
        ({**_EMPTY, "w": "12", "d": "6", "origin": "dsc"}, "", 5),
        ({**_EMPTY, "w": "12", "d": "6", "origin": "custom", "cx": "0", "cy": "3"}, "", 2),
        ({**_EMPTY, "w": "12", "d": "6", "origin": "custom", "cx": "0", "cy": "3", "z": "0"}, "", 5),
    ],
    ids=[
        "nothing",
        "a location",
        "a rectangle begun",
        "a size",
        "a size out of range",
        "the default origin",
        "a custom centre without Z",
        "a custom centre",
    ],  # fmt: skip
)
def test_simples_steps_open_in_order(rect: dict, loc: str, open_upto: int) -> None:
    out = _locks("simple", rect, loc)
    # Step 2 opens with step 1 done, step 3 with a valid size, step 4 (heading, picture, corners) with an origin.
    assert out["open"] == _LOCKED[:open_upto] and out["locked"] == _LOCKED[open_upto:]
    picture = open_upto == len(_LOCKED)
    assert (out["tip"], out["refresh"]) == (not picture, not picture)


_READY = {**_EMPTY, "w": "12", "d": "6", "origin": "dsc"}


@pytest.mark.parametrize(
    ("rect", "moved", "reset_shown", "next_on"),
    [
        (_READY, (False,) * 4, False, False),
        (_READY, (True, False, False, False), True, False),
        (_READY, (True,) * 4, True, True),
        ({**_READY, "origin": ""}, (True,) * 4, False, False),
        ({**_READY, "d": "300"}, (True,) * 4, False, False),
        ({**_READY, "w": ""}, (True, False, False, False), False, False),
    ],
    ids=["none placed", "one placed", "all placed", "no origin again", "a size out of range again", "no size again"],
)
def test_reset_corners_and_next_wait_for_the_corners(
    rect: dict, moved: tuple, reset_shown: bool, next_on: bool
) -> None:
    out = _locks("simple", rect, loc="back", moved=moved)
    assert (out["reset"], out["next"]) == (not reset_shown, not next_on)


def test_advanced_locks_nothing_and_always_shows_reset_pins() -> None:
    out = _locks("advanced", _EMPTY)
    assert out == {"locked": [], "open": _LOCKED, "tip": False, "refresh": False, "reset": False, "next": False}


@pytest.mark.parametrize(
    ("rect", "problem"),
    [
        ({**_EMPTY, "w": "12"}, "Enter the rectangle's depth."),
        ({**_EMPTY, "w": "12", "d": "6"}, "Choose the point of origin."),
        (
            {**_EMPTY, "w": "12", "d": "6", "origin": "custom", "cx": "1"},
            "Enter the Y and Z of the rectangle's centre, measured from the point of origin.",
        ),
        ({**_EMPTY, "w": "12", "d": "6", "origin": "dsc"}, ""),
    ],
    ids=["size first", "then the origin", "then a custom centre", "complete"],
)
def test_the_point_of_origin_is_an_active_choice(rect: dict, problem: str) -> None:
    out = run_wizard_js(
        "[rectProblem(), rectVals()]",
        functions=_MARK_FUNCTIONS,
        variables=_MARK_VARS,
        prelude=length_page("metric", {}) + f"var S = {{ rect: {json.dumps(rect)} }};",
    )
    assert out[0] == problem
    if not rect["origin"]:
        # Without a chosen origin the rectangle has no centre, so no corner has a position yet.
        assert (out[1]["cx"], out[1]["cy"], out[1]["z"]) == (None, None, None)


@pytest.mark.parametrize(
    ("marks", "grid", "origin"),
    [
        ([], {"width": 12, "depth": 6, "x_offset": 0, "y_offset": 3, "z_offset": 0}, ""),
        ([{"id": 1, "pin": [0.2, 0.4]}], {"width": 12, "depth": 6, "x_offset": 0, "y_offset": 3, "z_offset": 0}, "dsc"),
        (
            [{"id": 1, "pin": [0.2, 0.4]}],
            {"width": 12, "depth": 6, "x_offset": 1, "y_offset": 3, "z_offset": 0},
            "custom",
        ),
    ],
    ids=["no calibration: choose", "a saved default origin", "a saved custom origin"],
)
def test_a_station_keeps_the_origin_its_saved_rectangle_gives(marks: list, grid: dict, origin: str) -> None:
    out = run_wizard_js(
        "(function() { loadSaved(); return S.rect.origin; })()",
        functions=("len", "lenField", "loadSaved"),
        variables=("exactLen", "CORNERS"),
        prelude=length_page("metric", {})
        + "var S = { mode: 'simple', rect: { w: '', d: '', origin: '', cx: '', cy: '', z: '' },"
        " cornerPins: {}, cornerMoved: {}, points: [] };"
        f" var SAVED = {{ mode: 'simple', marks: {json.dumps(marks)}, grid: {json.dumps(grid)} }};",
    )
    assert out == origin


def _simple_hint(rect: dict, loc: str = "") -> list:
    return run_wizard_js(
        "(function() { renderSimpleReq(); return shown; })()",
        functions=(*_MARK_FUNCTIONS, "esc", "lenText", "fmt", "renderSimpleReq"),
        variables=(*_MARK_VARS, "CORNER_TITLES"),
        prelude=length_page("metric", {})
        + _DOM
        + "var shown = null; function showHint(area, h, sub) { shown = [area, h, sub]; }"
        f" var S = {{ simpleLoc: '{loc}', rect: {json.dumps(rect)}, solve: null,"
        " cornerMoved: { DSL: false, DSR: false, USR: false, USL: false } };",
    )


@pytest.mark.parametrize(
    ("rect", "loc", "shown"),
    [
        (
            _EMPTY,
            "",
            ["sloc", "Choose where your camera is located.",
             "The location decides on which side of the picture each corner of the rectangle starts."],
        ),
        (
            _EMPTY,
            "back",
            ["rect", "Enter the size of the rectangle you marked on stage.",
             "Only the corners need a mark, such as a tape cross. The Setup Wizard then draws the rectangle on the"
             " picture."],
        ),
        (
            {**_EMPTY, "w": "12", "d": "6"},
            "back",
            ["origin-q", "Choose the point of origin.",
             "All position data that the station sends is measured from this point (0/0/0). It must match the point"
             " of origin of your console or tracking system."],
        ),
        (
            {**_EMPTY, "w": "12", "d": "6", "origin": "dsc"},
            "",
            ["image", "Drag DSL onto its tape mark.",
             "Downstage left. 0 of 4 corners are on their marks. Click a corner to select it and move it with the"
             " arrow keys. Press Enter when the crosshair is in the right place."],
        ),
    ],
    ids=["nothing", "the size", "the origin", "the corners"],
)  # fmt: skip
def test_the_simple_hint_sits_above_the_step_it_is_about(rect: dict, loc: str, shown: list) -> None:
    assert _simple_hint(rect, loc) == shown


# ---------------------------------------------------------------------------
# Lens: a new line is selected as a whole line, so Delete takes it back
# ---------------------------------------------------------------------------

_NEW_LINE_PAGE = """
var imageWidth = 1920, imageHeight = 1080, lensLines = [], lensPending = null, lensSelected = null, lensHold = false;
var lensCandidates = [{ samples: [[100, 100], [300, 105], [500, 110], [700, 115], [900, 120]], used: false,
  rejected: false }];
var snapped = 0, changed = 0, window = {};
function renderLens() {} function lensShowNotice() {} function lensSnapLine() { snapped++; } function lensHotCand() {}
function dropLensPoint() { lensHold = false; } function lensChanged() { changed++; }
var focused = { contains: function() { return false; }, focus: function() {} };
var document = { getElementById: function() { return focused; } };
function lensFrame(line) {
  var dx = line.p1[0] - line.p0[0], dy = line.p1[1] - line.p0[1], len = Math.sqrt(dx * dx + dy * dy) || 1;
  return { dx: dx, dy: dy, len: len, nx: -dy / len, ny: dx / len };
}
"""


@pytest.mark.parametrize(
    "add",
    ["lensTraceClick([100, 500]); lensTraceClick([900, 520]);", "lensAddCandidate(0);"],
    ids=["a drawn line", "a used suggestion"],
)
def test_a_new_line_is_selected_whole_and_delete_takes_it_back(add: str) -> None:
    out = run_wizard_js(
        "(function() { " + add + " var after = [lensSelected, lensHold, lensLines.length, snapped];"
        " window.lensDeleteSelectedLine(); window.lensToggleSelectedPoint();"
        " return [after, lensLines.length, lensSelected]; })()",
        functions=(
            "lensClamp",
            "lensTraceClick",
            "lensSetMidsFromPositions",
            "lensAddCandidate",
            "lensReleaseCandidate",
        ),
        variables=("LENS_T", "LENS_MIN_LINE_PX"),
        prelude=_NEW_LINE_PAGE
        + wizard_window_function("lensDeleteSelectedLine")
        + wizard_window_function("lensToggleSelectedPoint"),
    )
    after, left, selected = out
    # No point held and no loupe: the hint moves on, and Delete or Backspace removes the line just added.
    assert after == [{"line": 0, "point": None}, False, 1, 1]
    assert (left, selected) == (0, None)


def test_switching_a_point_off_needs_a_middle_point_selected() -> None:
    out = run_wizard_js(
        "(function() { lensSelected = { line: 0, point: null }; window.lensToggleSelectedPoint();"
        " lensSelected = { line: 0, point: 2 }; window.lensToggleSelectedPoint(); return lensLines[0].mids[1].on; })()",
        prelude=_NEW_LINE_PAGE
        + "lensLines = [{ mids: [{ on: true }, { on: true }, { on: true }] }];"
        + wizard_window_function("lensToggleSelectedPoint"),
    )
    assert out is False


# ---------------------------------------------------------------------------
# Camera height: asked while the camera looks almost straight down
# ---------------------------------------------------------------------------

_HEIGHT_RESULT = (
    "(function() { renderCalibResult(); return [$('cal-height').hidden, $('cal-height-ask').hidden,"
    " $('cal-height-note').hidden, $('cal-height-note').textContent]; })()"
)


@pytest.mark.parametrize(
    ("solve", "typed", "height", "shown"),
    [
        ({"cam": {"fov": 60}, "steep": False}, "", None, [True, False, True, ""]),
        ({"cam": {"fov": 60}, "steep": True}, "", None, [False, False, True, ""]),
        (
            {"cam": {"fov": 60}, "steep": True},
            "9.8",
            9.8,
            [False, True, False, "The camera height is fixed at 9.80 m."],
        ),
        (
            {"cam": {"fov": 60}, "steep": False},
            "9.8",
            9.8,
            [False, True, False, "The camera height is fixed at 9.80 m."],
        ),
        ({"error": "No camera fits."}, "9.8", 9.8, [False, True, False, "The camera height is fixed at 9.80 m."]),
        ({"cam": {"fov": 60}, "steep": False}, "9.", None, [False, False, True, ""]),
        ({"error": "No camera fits.", "steep": True}, "", None, [True, False, True, ""]),
    ],
    ids=["not steep", "steep", "steep, entered", "no longer steep", "no solve", "half typed", "no camera"],
)
def test_the_height_is_asked_while_steep_and_noted_once_entered(solve: dict, typed: str, height, shown: list) -> None:
    out = run_wizard_js(
        _HEIGHT_RESULT,
        functions=("renderCalibResult", "lenText", "fmt"),
        prelude=length_page("metric", {})
        + _DOM
        + _REVIEW_STUBS
        + "function lensMeasured() { return true; }"
        + f" var S = {{ mode: 'simple', pair: 0, height: {json.dumps(height)} }};"
        + f" S.solve = Object.assign({{ worst: 0.4, pinned: 4, reasons: [], marks: [] }}, {json.dumps(solve)});"
        + f" $('cam_height').value = {json.dumps(typed)};",
    )
    assert out == shown


def test_the_note_is_in_the_active_unit() -> None:
    out = run_wizard_js(
        _HEIGHT_RESULT,
        functions=("renderCalibResult", "lenText", "fmt"),
        prelude=length_page("imperial", {})
        + _DOM
        + _REVIEW_STUBS
        + "function lensMeasured() { return true; }"
        + " var S = { mode: 'simple', pair: 0, height: 9.8 };"
        + " S.solve = { cam: { fov: 60 }, steep: true, worst: 0.4, pinned: 4, reasons: [], marks: [] };"
        + " $('cam_height').value = 'x';",
    )
    assert out[3] == f"The camera height is fixed at {format_length(9.8, 'imperial')}."


@pytest.mark.parametrize(
    ("system", "typed", "height"),
    [
        ("metric", "9.8", 9.8),
        ("metric", "-1.5", -1.5),
        ("metric", "", None),
        ("metric", "abc", None),
        ("metric", "10000", 10000.0),
        ("metric", "10000.5", None),
        ("imperial", "32 ft 2 in", 32 * 0.3048 + 2 * 0.0254),
        ("imperial", "32", 32 * 0.3048),
    ],
)
def test_the_field_reads_as_metres_or_none(system: str, typed: str, height) -> None:
    out = run_wizard_js(
        f"(function() {{ $('cam_height').value = {json.dumps(typed)}; readHeight(); return S.height; }})()",
        functions=("len", "readHeight"),
        variables=("exactLen", "DISTANCE_SIGMA"),
        prelude=length_page(system, {}) + _DOM + "var S = { height: 'before' };",
    )
    assert out == (None if height is None else pytest.approx(height))


@pytest.mark.parametrize("height", [9.8, None])
def test_the_solve_carries_the_height_or_null(height) -> None:
    out = run_wizard_js(
        "solveBody([], 7)",
        functions=("solveBody",),
        prelude=f"var S = {{ mode: 'simple', height: {json.dumps(height)} }}, SAVED = {{ fov: 60 }};"
        " var imageWidth = 1920, imageHeight = 1080, WIZ_SOLVE_PAGE = 'p';"
        " function hintPos() { return null; } function wizReadLensCoeff() { return 0; }",
    )
    assert "camera_height" in out and out["camera_height"] == height


@pytest.mark.parametrize(("answered", "steep"), [({"steep": True}, True), ({"steep": False}, False), ({}, False)])
def test_the_solve_takes_the_stations_word_on_whether_the_camera_is_steep(answered: dict, steep: bool) -> None:
    out = run_wizard_js(
        "solveFromAnswer(marks, 200, data).steep",
        functions=(*_REASON_FUNCTIONS, "solveFromAnswer"),
        prelude=_reason_page(),
        marks=_ANSWER_MARKS[:4],
        data={**_ANSWER, "marks": _ANSWER["marks"][:4], **answered},
    )
    assert out is steep


def _review_height(height, steep: bool, *, ok: bool = True, system: str = "metric") -> str:
    solve = {"worst": 0.4, "reasons": [], "positions": {}, "marks": _COMPLETE, "steep": steep}
    if ok:
        solve["cam"] = {"fov": 60}
    return run_wizard_js(
        "(function() { renderReview(); return $('review-calib').innerHTML; })()",
        functions=(*_REVIEW_FUNCTIONS, "lenText", "fmt"),
        variables=("APPLY_MAX_PX",),
        prelude=length_page(system, {}) + _DOM + _REVIEW_STUBS,
        S={"mode": "advanced", "cam": None, "solve": solve, "height": height},
        solvePending=False,
    )


_HEIGHT_ROW = '<span class="param-label">Camera height</span><span class="param-value">{}</span>'


@pytest.mark.parametrize(
    ("height", "steep", "row"),
    [
        (None, True, "Not entered"),
        (9.8, True, "Fixed at 9.80 m"),
        (9.8, False, "Fixed at 9.80 m"),
        (None, False, None),
    ],
    ids=["steep, not entered", "steep, entered", "entered", "neither"],
)
def test_review_lists_the_camera_height_when_steep_or_entered(height, steep: bool, row) -> None:
    calib = _review_height(height, steep)
    assert (_HEIGHT_ROW.format(row) in calib) if row else ("Camera height" not in calib)


def test_review_lists_no_camera_height_for_a_steep_answer_without_a_camera() -> None:
    assert "Camera height" not in _review_height(None, True, ok=False)


def test_review_lists_the_camera_height_in_the_active_unit() -> None:
    calib = _review_height(9.8, True, system="imperial")
    assert _HEIGHT_ROW.format("Fixed at " + format_length(9.8, "imperial").replace('"', "&quot;")) in calib


def test_a_unit_switch_keeps_the_height_it_converts() -> None:
    out = run_wizard_js(
        "(function() { $('cam_height').value = '9.8'; readHeight(); setUnitSystem('imperial');"
        " var shown = $('cam_height').value; setUnitSystem('metric');"
        " return [shown, $('cam_height').value, S.height]; })()",
        functions=("len", "lenField", "readCam", "readHeight", "setUnitSystem"),
        variables=("exactLen", "DISTANCE_SIGMA"),
        prelude=length_page("metric", {}) + _DOM + "var S = { rect: {}, points: [], height: null };"
        " function applyUnitMarkup() {} function refreshEchoes() {} function renderCalibPanels() {}"
        " function solveSoon() {} function saveToSession() {}",
    )
    assert out[0] == format_length(9.8, "imperial")
    assert out[1:] == ["9.8", pytest.approx(9.8)]


@pytest.mark.parametrize(("units", "kept"), [("metric", "9.8"), ("imperial", "")], ids=["same units", "other units"])
def test_the_session_keeps_the_typed_height_in_its_units(units: str, kept: str) -> None:
    raw = {"units": units, "mode": "simple", "stepKey": "calib", "rect": {}, "height": "9.8"}
    out = run_wizard_js(
        "(function() { saveToSession(); var saved = stored.height; restoreFromSession();"
        " return [saved, $('cam_height').value]; })()",
        functions=("pairOk", "fracPinOk", "saveToSession", "restoreFromSession"),
        variables=("CORNERS",),
        prelude=length_page("metric", {}) + _DOM + _SESSION_PAGE + f"var raw = {json.dumps(json.dumps(raw))};"
        " var S = { mode: 'simple', simpleLoc: '', rect: { origin: '' }, pinSize: null, nextId: 1, points: [],"
        " cornerPins: { DSL: null, DSR: null, USR: null, USL: null },"
        " cornerMoved: { DSL: false, DSR: false, USR: false, USL: false }, lensTouched: false, lensLoaded: null };"
        " $('cam_height').value = 'typed';",
    )
    assert out == ["typed", kept or "typed"]


@pytest.mark.parametrize(
    ("system", "marks", "height", "field"),
    [
        ("metric", [{"id": 1, "pin": [0.2, 0.4]}], 9.8, "9.8"),
        ("imperial", [{"id": 1, "pin": [0.2, 0.4]}], 9.8, format_length(9.8, "imperial")),
        ("metric", [{"id": 1, "pin": [0.2, 0.4]}], None, ""),
        ("metric", [], 9.8, ""),
    ],
    ids=["metric", "imperial", "not entered", "no calibration"],
)
def test_a_reopened_wizard_fills_in_the_saved_height(system: str, marks: list, height, field: str) -> None:
    out = run_wizard_js(
        "(function() { loadSaved(); return $('cam_height').value; })()",
        functions=("len", "lenField", "loadSaved"),
        variables=("exactLen", "CORNERS"),
        prelude=length_page(system, {})
        + _DOM
        + "var S = { mode: 'simple', rect: { w: '', d: '', origin: '', cx: '', cy: '', z: '' },"
        " cornerPins: {}, cornerMoved: {}, points: [] };"
        + f" var SAVED = {{ mode: 'simple', marks: {json.dumps(marks)}, height: {json.dumps(height)},"
        " grid: { width: 12, depth: 6, x_offset: 0, y_offset: 3, z_offset: 0 } };",
    )
    assert out == field
