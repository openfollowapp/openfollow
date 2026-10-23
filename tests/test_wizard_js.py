# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The Setup Wizard's script maths, run in node and held against the Python it mirrors."""

from __future__ import annotations

import json
import math
import re
from html.parser import HTMLParser

import numpy as np
import pytest

from openfollow.configuration import CameraConfig
from openfollow.lens_model import C_BOUND, lens_limit_radius, lens_warp_is_valid
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
from openfollow.scene.solver import (
    apply_overlay_distortion,
    distort_normalised_radius,
    invert_overlay_distortion,
    project_points,
)
from openfollow.units import format_length
from tests._lens_band import scaled_luma
from tests._wizard_js import (
    WIZARD_TEMPLATE,
    length_page,
    needs_node,
    run_wizard_js,
    wizard_block,
    wizard_function,
    wizard_var,
    wizard_window_function,
)

pytestmark = [pytest.mark.unit, needs_node]

_LENS_MODEL = ("wizLensRoot", "wizLensLimitRadius", "wizLensIsValid")
_LENS_FUNCTIONS = (*_LENS_MODEL, "wizHalfDiag", "wizDistortRadius", "wizApplyDistortion", "wizInvertDistortion")
_W, _H = 1920, 1080
_D = 0.5 * math.hypot(_W, _H)

# Lenses across the valid region: pinhole, barrel, a 100 degree lens, a 126 degree one, a corner-heavy one,
# pincushion, mixed signs; with the centre at the picture's centre and off it.
_PAIRS = [(0.0, 0.0), (-0.1, 0.0), (-0.333, -0.025), (-0.527, -0.069), (0.0, -0.6), (0.2, 0.0), (0.1, -0.05)]
_CENTRES = [(0.0, 0.0), (0.06, -0.04), (-0.12, 0.09)]
_LENSES = [(a, b, x, y) for a, b in _PAIRS for x, y in _CENTRES if lens_warp_is_valid(a, b, x, y)]


# ---------------------------------------------------------------------------
# Lens warp
# ---------------------------------------------------------------------------


def test_limit_radius_and_validity_are_the_servers() -> None:
    k1s = np.round(np.arange(-1.2, 1.21, 0.06), 2).tolist()
    k2s = np.round(np.arange(-0.9, 0.51, 0.05), 2).tolist()
    centres = [[0.0, 0.0], [0.1, -0.05], [-0.2, 0.15], [C_BOUND, -C_BOUND], [C_BOUND + 1e-9, 0.0], [0.0, -0.3]]
    lenses = [[a, b, *c] for a in k1s for b in k2s for c in centres] + [[-1e200, 1e200, 0, 0], [2e3, 0.0, 0, 0]]
    out = run_wizard_js(
        "lenses.map(function(l) { return [wizLensLimitRadius(l[0], l[1]), wizLensIsValid(l[0], l[1], l[2], l[3])]; })",
        functions=_LENS_MODEL,
        LENS_C_BOUND=C_BOUND,
        lenses=lenses,
    )
    for (radius, valid), (a, b, x, y) in zip(out, lenses, strict=True):
        expected = lens_limit_radius(a, b)
        assert (radius is None) == math.isinf(expected), (a, b)
        if radius is not None:
            assert radius == pytest.approx(expected, rel=1e-9), (a, b)
        assert valid == lens_warp_is_valid(a, b, x, y), (a, b, x, y)
    # Both answers occur, with the centre deciding some.
    flags = [v for _, v in out]
    assert any(flags) and not all(flags)
    assert any(v != lens_warp_is_valid(a, b, 0.0, 0.0) for v, (a, b, _, _) in zip(flags, lenses, strict=True)), (
        "no lens whose centre decides"
    )


@pytest.mark.parametrize(("k1", "k2"), [(-0.33, 1e-15), (-0.33, -1e-15), (-0.3, 1e-12), (0.4, 1e-14)])
def test_limit_radius_is_the_servers_when_the_edge_term_is_tiny(k1: float, k2: float) -> None:
    radius, valid = run_wizard_js(
        "[wizLensLimitRadius(k1, k2), wizLensIsValid(k1, k2, 0.05, 0)]",
        functions=_LENS_MODEL,
        LENS_C_BOUND=C_BOUND,
        k1=k1,
        k2=k2,
    )
    expected = lens_limit_radius(k1, k2)
    assert (radius is None) == math.isinf(expected)
    if radius is not None:
        assert radius == pytest.approx(expected, rel=1e-9)
    assert valid == lens_warp_is_valid(k1, k2, 0.05, 0.0)


@pytest.mark.parametrize(("k1", "k2"), _PAIRS[1:] + [(-0.951, -0.251), (0.0, 0.3)])
def test_distorted_radius_is_the_servers(k1: float, k2: float) -> None:
    # Out to three corners, and far past the frame: under barrel every pinhole radius has one, past a fold none.
    radii = [*np.linspace(0.0, 3.0, 61).tolist(), 50.0, 1000.0]
    out = run_wizard_js(
        "radii.map(function(r) { return wizDistortRadius(r, k1, k2); })",
        functions=("wizLensRoot", "wizLensLimitRadius", "wizDistortRadius"),
        radii=radii,
        k1=k1,
        k2=k2,
    )
    expected = distort_normalised_radius(np.array(radii), k1, k2)
    # At a fold the root is double, so both solves stop within about the square root of their tolerance there.
    at_fold = expected > lens_limit_radius(k1, k2) * (1.0 - 1e-6)
    np.testing.assert_allclose(np.array(out)[~at_fold], expected[~at_fold], rtol=0, atol=1e-12)
    np.testing.assert_allclose(np.array(out)[at_fold], expected[at_fold], rtol=0, atol=1e-8)


@pytest.mark.parametrize(("k1", "k2", "cx", "cy"), _LENSES)
def test_bowing_and_unbowing_a_point_are_the_servers(k1: float, k2: float, cx: float, cy: float) -> None:
    points = [[0.0, 0.0], [960.0, 540.0], [1920.0, 1080.0], [0.0, 1080.0], [300.0, 900.0], [1700.0, 100.0]]
    bowed, unbowed = run_wizard_js(
        "[points.map(function(p) { return wizApplyDistortion(p, k1, k2, cx, cy); }),"
        " points.map(function(p) { return wizInvertDistortion(p, k1, k2, cx, cy); })]",
        functions=_LENS_FUNCTIONS,
        imageWidth=_W,
        imageHeight=_H,
        points=points,
        k1=k1,
        k2=k2,
        cx=cx,
        cy=cy,
    )
    pts = np.array(points)
    np.testing.assert_allclose(bowed, apply_overlay_distortion(pts, _W, _H, k1, k2, cx, cy), rtol=0, atol=1e-9)
    np.testing.assert_allclose(unbowed, invert_overlay_distortion(pts, _W, _H, k1, k2, cx, cy), rtol=0, atol=1e-9)


def test_the_lens_centre_moves_the_picture_by_its_own_offset() -> None:
    # k = (-0.2, 0), centre 0.1 half-diagonals right: the lens centre's own pixel is the picture centre's pinhole
    # point, and 0.5 half-diagonals right of it lands 0.5 / 0.95 right of the picture centre.
    centre_px = [960.0 + 0.1 * _D, 540.0]
    out = run_wizard_js(
        "[wizInvertDistortion(a, -0.2, 0, 0.1, 0), wizInvertDistortion(b, -0.2, 0, 0.1, 0),"
        " wizApplyDistortion([960, 540], 0, 0, 0, -0.1)]",
        functions=_LENS_FUNCTIONS,
        imageWidth=_W,
        imageHeight=_H,
        a=centre_px,
        b=[centre_px[0] + 0.5 * _D, 540.0],
    )
    assert out[0] == pytest.approx([960.0, 540.0], abs=1e-9)
    assert out[1] == pytest.approx([960.0 + 0.5 / 0.95 * _D, 540.0], abs=1e-9)
    # A centre above the picture's (cy negative, y down) moves the picture up with it.
    assert out[2] == pytest.approx([960.0, 540.0 - 0.1 * _D], abs=1e-9)


def test_a_point_past_the_limit_has_no_pinhole_point() -> None:
    # (-0.8, -0.4) reaches its pole at 1.0 half-diagonals: the frame corner has no pinhole point, the server's NaN.
    corner = [[1920.0, 1080.0]]
    out = run_wizard_js(
        "wizInvertDistortion(p, -0.8, -0.4, 0, 0)",
        functions=_LENS_FUNCTIONS,
        imageWidth=_W,
        imageHeight=_H,
        p=corner[0],
    )
    assert out == [None, None]
    assert np.isnan(invert_overlay_distortion(np.array(corner), _W, _H, -0.8, -0.4, 0.0, 0.0)).all()


@pytest.mark.parametrize(
    ("lens", "shown"),
    [
        ((-0.3, 0.02, 0.0, 0.0), ["-0.3000", "0.0200"]),
        # Four decimals would put the pair past the limit with this centre, though not without one.
        ((-0.5, -0.26979, 0.1, 0.0), ["-0.5", "-0.26979"]),
    ],
)
def test_a_fitted_lens_is_shown_as_a_valid_lens(lens: tuple[float, ...], shown: list[str]) -> None:
    k1, k2, cx, cy = lens
    assert lens_warp_is_valid(k1, k2, cx, cy)
    out = run_wizard_js(
        "wizLensShownPair(k1, k2, cx, cy)",
        functions=(*_LENS_MODEL, "wizLensShownPair"),
        LENS_C_BOUND=C_BOUND,
        k1=k1,
        k2=k2,
        cx=cx,
        cy=cy,
    )
    assert out == shown
    assert lens_warp_is_valid(float(out[0]), float(out[1]), cx, cy)


_FIT_LENS = ("lensFitApplies", "lensFitMisfit", "lensSame", "lensIsTheFits")


@pytest.mark.parametrize(
    ("centre", "lens", "same"),
    [
        ([0.0, 0.0], [-0.3, 0.02, 0.0, 0.0], True),
        ([0.03, -0.01], [-0.3, 0.02, 0.03, -0.01], True),
        ([0.03, -0.01], [-0.3, 0.02, 0.0, 0.0], False),
        ([0.0, 0.0], [-0.3, 0.02, 0.0, 0.001], False),
        ([0.0, 0.0], [-0.3, 0.021, 0.0, 0.0], False),
        ([0.1, 0.0], [-0.5, -0.26979, 0.1, 0], True),
    ],
    ids=["pinhole centre", "a centre", "centre moved after", "centre set after", "pair moved after", "unrounded"],
)
def test_the_lens_a_fit_wrote_reads_as_the_fits(centre: list, lens: list[float], same: bool) -> None:
    k1, k2 = (-0.5, -0.26979) if centre == [0.1, 0.0] else (-0.3, 0.02)
    fit = {"k1": k1, "k2": k2, "rating": "good", "centre": centre}
    out = run_wizard_js(
        "lensIsTheFits(lens)",
        functions=(*_LENS_MODEL, "wizLensShownPair", *_FIT_LENS),
        prelude=f"var lensFit = {json.dumps(fit)};",
        LENS_C_BOUND=C_BOUND,
        lens=lens,
    )
    # The fit's pair holds about the centre it was sent; a centre changed since is not the fit's lens.
    assert out is same


_ANSWER_FETCH = (
    "function settled(v) { return { then: function(f) { var r = f(v); return r && r.then ? r : settled(r); },"
    " catch: function() { return this; } }; }"
    " function fetch(url, opts) { sent = JSON.parse(opts.body); return settled({ ok: true,"
    " json: function() { return settled({ k1: -0.3123, k2: -0.0123, k2_fitted: true, rating: 'good', hint: '',"
    " lines: [{ curve: [], rms_px: 0.4, misfit: false }, { curve: [], rms_px: 0.4, misfit: false }] }); } }); }"
)


def test_a_fit_measures_the_pair_about_the_lens_centre_in_use_and_keeps_it() -> None:
    out = run_wizard_js(
        "(function() { runLensFit(); return [sent.cx, sent.cy, written, S.lensCentre, lensFit.centre]; })()",
        functions=(*_LENS_MODEL, "wizLensShownPair", "lensFitApplies", "lensFitMisfit", "runLensFit"),
        prelude=_ANSWER_FETCH + _FIT_PAGE + " var sent = null, written = {}, S = { lensCentre: [0.0254, -0.0145] };"
        " function wizWriteLensCoeff(id, v) { written[id] = v; } function wizShowLensLimit() {}"
        " function onLensCoeffChanged() {}",
        LENS_C_BOUND=C_BOUND,
    )
    assert out == [0.0254, -0.0145, {"wiz_lens_k1": "-0.3123", "wiz_lens_k2": "-0.0123"}, [0.0254, -0.0145],
                   [0.0254, -0.0145]]  # fmt: skip


@pytest.mark.parametrize(
    ("answer", "taken"),
    [("k1: -0.3123, k2: -0.0123", True), ("k1: -0.6, k2: -0.1", False)],
    ids=["fits", "still short"],
)
def test_a_fit_takes_a_centre_the_last_pair_could_not(answer: str, taken: bool) -> None:
    fetch = _ANSWER_FETCH.replace("k1: -0.3123, k2: -0.0123", answer)
    out = run_wizard_js(
        "(function() { $('wiz_lens_k1').value = '-0.6'; $('wiz_lens_k2').value = '-0.1';"
        " $('wiz_lens_cx').value = '220'; $('wiz_lens_cy').value = '220'; runLensFit();"
        " return [S.lensCentre, S.lensCentreBad, fits]; })()",
        functions=(
            *_LENS_MODEL,
            *_CENTRE_PX,
            "wizReadLensCoeff",
            "wizLens",
            "wizLensShownPair",
            "lensFitApplies",
            "lensFitMisfit",
            "wizWriteLensCoeff",
            "wizSyncLensRange",
            "wizTakeLensCentre",
            "runLensFit",
        ),
        prelude=_LIMIT_PAGE
        + fetch
        + _FIT_PAGE
        + " var sent = null, fits = 0, S = { lensCentre: [0.01, 0.02], lensCentreBad: true };"
        " function wizShowLensLimit() {} function onLensCoeffChanged() {} function scheduleLensFit() { fits++; }",
        LENS_C_BOUND=C_BOUND,
    )
    # Taken, the lines are measured again about it; still short, the last centre stays, flagged.
    if taken:
        assert out == [pytest.approx([220 / _D, -220 / _D]), False, 1]
    else:
        assert out == [[0.01, 0.02], True, 0]


def test_a_fit_that_leaves_the_corner_correction_open_applies_its_estimate() -> None:
    # The answer's k2 is the pair's, drawn toward no lens; a point the fit set aside changes nothing on the page.
    fetch = _ANSWER_FETCH.replace("k2: -0.0123, k2_fitted: true", "k2: -0.1834, k2_fitted: false").replace(
        "misfit: false }", "misfit: false, dropped: [2] }"
    )
    assert fetch != _ANSWER_FETCH
    out = run_wizard_js(
        "(function() { runLensFit(); return [written, lensLines[0].rms, lensFit.k2_fitted]; })()",
        functions=(*_LENS_MODEL, "wizLensShownPair", "lensFitApplies", "lensFitMisfit", "runLensFit"),
        prelude=fetch + _FIT_PAGE + " var sent = null, written = {}, S = { lensCentre: [0, 0] };"
        " function wizWriteLensCoeff(id, v) { written[id] = v; } function wizShowLensLimit() {}"
        " function onLensCoeffChanged() {}",
        LENS_C_BOUND=C_BOUND,
    )
    assert out == [{"wiz_lens_k1": "-0.3123", "wiz_lens_k2": "-0.1834"}, 0.4, False]


@pytest.mark.parametrize(("lines", "refit"), [(True, True), (False, False)], ids=["traced lines", "no lines"])
def test_a_new_lens_centre_measures_the_traced_lines_again(lines: bool, refit: bool) -> None:
    out = run_wizard_js(
        "(function() { $('wiz_lens_cx').value = '13'; $('wiz_lens_cy').value = '9';"
        " window.onWizardLensCentreInput(); return fits; })()",
        functions=(*_LENS_MODEL, *_CENTRE_PX, "wizReadLensCoeff", "wizLens", "wizTakeLensCentre"),
        prelude=_LIMIT_PAGE + f"var window = {{}}, fits = 0, lensLines = [{{}}].slice(0, {int(lines)}),"
        " S = { lensCentre: [0, 0], lensCentreBad: false };"
        " function wizLensEdited() {} function lensLineCounts() { return true; }"
        " function scheduleLensFit() { fits++; }\n" + wizard_window_function("onWizardLensCentreInput"),
        LENS_C_BOUND=C_BOUND,
    )
    assert out == int(refit)


# ---------------------------------------------------------------------------
# The lens centre in pixels of the snapshot
# ---------------------------------------------------------------------------

_CENTRE_PX = (
    "wizHalfDiag",
    "wizLensCentrePxMax",
    "wizLensCentrePx",
    "wizLensCentreText",
    "wizLensCentreField",
    "wizLensCentreFromPx",
)


@pytest.mark.parametrize(("w", "h", "bound"), [(1920, 1080, 275), (3840, 2160, 550), (1280, 720, 183)])
def test_the_centre_fields_span_the_stations_bound_in_pixels(w: int, h: int, bound: int) -> None:
    out = run_wizard_js(
        "[wizLensCentrePxMax(w, h), wizLensCentreFromPx(String(b), String(-b), w, h),"
        " wizLensCentreFromPx(String(b + 1), '0', w, h), wizLensCentreFromPx('0', String(-b - 1), w, h)]",
        functions=_CENTRE_PX,
        LENS_C_BOUND=C_BOUND,
        w=w,
        h=h,
        b=bound,
    )
    d = 0.5 * math.hypot(w, h)
    assert out[0] == bound == math.floor(C_BOUND * d)
    assert out[1] == pytest.approx([bound / d, bound / d]) and max(map(abs, out[1])) <= C_BOUND
    assert out[2] is None and out[3] is None


def test_a_centre_42_px_right_and_18_px_up_is_stored_in_half_diagonals() -> None:
    out = run_wizard_js(
        "[wizLensCentreFromPx('42', '18', 1920, 1080), wizLensCentreFromPx(' -42 ', '-18', 1920, 1080)]",
        functions=_CENTRE_PX,
        LENS_C_BOUND=C_BOUND,
    )
    # Up is positive on the field and negative in image axes (y down).
    assert out == [pytest.approx([42 / _D, -18 / _D]), pytest.approx([-42 / _D, 18 / _D])]
    # The station reads them back to the same pixels.
    assert CameraConfig(lens_cx=out[0][0], lens_cy=out[0][1]).lens_cx * _D == pytest.approx(42.0)


@pytest.mark.parametrize("text", ["", "  ", "abc", "1e400", "NaN", "Infinity"])
def test_a_centre_field_without_a_number_gives_no_centre(text: str) -> None:
    out = run_wizard_js(
        "[wizLensCentreFromPx(t, '0', 1920, 1080), wizLensCentreFromPx('0', t, 1920, 1080)]",
        functions=_CENTRE_PX,
        LENS_C_BOUND=C_BOUND,
        t=text,
    )
    assert out == [None, None]


@pytest.mark.parametrize(
    ("centre_px", "text"),
    [
        ((42, 18), "42 px right, 18 px up"),
        ((-13, 9), "13 px left, 9 px up"),
        ((0, -7), "7 px down"),
        ((5, 0), "5 px right"),
        ((0.4, -0.4), ""),
        ((0, 0), ""),
    ],
)
def test_the_centre_is_named_in_whole_pixels_and_an_axis_at_0_is_left_out(centre_px: tuple, text: str) -> None:
    c = [centre_px[0] / _D, -centre_px[1] / _D]
    out = run_wizard_js(
        "[wizLensCentreText(c, 1920, 1080), wizLensCentrePx(c, 1920, 1080), wizLensCentreText(c, 0, 0)]",
        functions=_CENTRE_PX,
        LENS_C_BOUND=C_BOUND,
        c=c,
    )
    assert out[0] == text
    assert out[1] == [round(centre_px[0]), round(centre_px[1])]
    # Without a snapshot there are no pixels to name.
    assert out[2] == ""


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
        " var S = { lensCentre: [0, 0] };"
        " var lensFit = { k1: -0.2, k2: 0, rating: 'good' };"
        " var lensLines = [{ points: 5 }, { points: 5 }];"
        " function lensLineCounts() { return true; } function lensActivePoints() { return []; }"
        " function lensShowStatus(text, ok) { status = [text, ok]; }"
        " function renderLensResult() { shown = lensFit; } function renderLens() {} function saveToSession() {}",
    )
    assert out == [None, None, ["At most 32 lines.", False]]


_FIT_PAGE = (
    " var lensFitSeq = 0, imageWidth = 1920, imageHeight = 1080, drawn = 0, saved = 0, S = { lensCentre: [0, 0] };"
    " var lensFit = { k1: -0.2, k2: 0, rating: 'good' };"
    " var lensLines = [{ points: 5 }, { points: 5 }];"
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
        " function wizWriteLensCoeff() {}"
        " function lensRecomputeCurves() {} function renderLens() {} function renderLensResult() {}"
        " function onLensCoeffChanged() {} function wizShowLensLimit() {}\n"
        + wizard_function("wizLensEdited")
        + wizard_window_function("onWizardLensInput")
        + "\nvar onWizardLensInput = window.onWizardLensInput;",
    )
    # A station's saved pair counts as measured; a pinhole one only once it is edited.
    assert out == [before, True]


# A number field as a browser keeps it: the text as typed, its value empty while the text is no number yet.
_TYPED_FIELDS = r"""
function numberField(text) {
  return { text: text, writes: 0,
    get value() { return /^-?(\d+(\.\d+)?|\.\d+)([eE][-+]?\d+)?$/.test(this.text) ? this.text : ''; },
    set value(v) { this.writes++; this.text = String(v); } };
}
var fields = { wiz_lens_k1: numberField('0'), wiz_lens_k2: numberField('0'),
  wiz_lens_k1_range: { value: '0', min: '-1', max: '0.5' },
  wiz_lens_k2_range: { value: '0', min: '-0.8', max: '0.3' } };
var document = { getElementById: function(id) { return fields[id] || null; } };
var window = {}, S = { lensCentreBad: false };
function wizLensEdited() {} function wizTakeLensCentre() {}
"""


@pytest.mark.parametrize(
    ("which", "typed", "span"),
    [("k1", "-0.25", [-1, 0.5]), ("k2", "-0.125", [-0.8, 0.3]), ("k1", "-1.5", [-1.5, 0.5]),
     ("k2", "0.4", [-0.8, 0.4])],
)  # fmt: skip
def test_a_value_typed_key_by_key_stays_as_typed_and_the_slider_follows(which: str, typed: str, span: list) -> None:
    out = run_wizard_js(
        "(function() { var id = 'wiz_lens_' + which, seen = [];"
        " for (var i = 1; i <= typed.length; i++) { fields[id].text = typed.slice(0, i);"
        " window.onWizardLensInput(which, 'number');"
        " seen.push([fields[id].text, Number(fields[id + '_range'].value)]); }"
        " return [seen, fields[id].writes, Number(fields[id + '_range'].min), Number(fields[id + '_range'].max)]; })()",
        functions=("wizSyncLensRange",),
        prelude=_TYPED_FIELDS + wizard_window_function("onWizardLensInput"),
        which=which,
        typed=typed,
    )
    seen, writes, low, high = out
    # Every key leaves the text as typed: a part-typed "-" or "-0." is never written back as empty.
    assert [text for text, _ in seen] == [typed[:i] for i in range(1, len(typed) + 1)] and writes == 0
    # The slider follows each prefix that reads as a number and keeps its place while one does not.
    slider, expected = [v for _, v in seen], []
    for i in range(1, len(typed) + 1):
        prefix = typed[:i]
        number = re.fullmatch(r"-?(\d+(\.\d+)?|\.\d+)", prefix)
        expected.append(float(prefix) if number else (expected[-1] if expected else 0.0))
    assert slider == pytest.approx(expected) and slider[-1] == pytest.approx(float(typed))
    assert [low, high] == pytest.approx(span)


def test_a_slider_drag_writes_the_number_field() -> None:
    out = run_wizard_js(
        "(function() { fields.wiz_lens_k2_range.value = '-0.245'; window.onWizardLensInput('k2', 'range');"
        " return [fields.wiz_lens_k2.text, fields.wiz_lens_k2.writes]; })()",
        functions=("wizSyncLensRange",),
        prelude=_TYPED_FIELDS + wizard_window_function("onWizardLensInput"),
    )
    assert out == ["-0.245", 1]


# ---------------------------------------------------------------------------
# Lens values from an exported settings file
# ---------------------------------------------------------------------------

_LENS_FILE_FUNCTIONS = (*_LENS_MODEL, "lensFromSettings")
_NOT_SETTINGS = "This is not an OpenFollow settings file."
_NO_LENS = "This settings file has no lens calibration."
_LIMIT = "With the lens values of this file, the overlay cannot follow the picture out to its corners."
LIMIT_CENTRE = (
    "With this lens centre, the overlay cannot follow the picture out to its corners. Set the lens centre closer to 0."
)


def _settings(k1: object, k2: object, **centre: object) -> str:
    camera = {"fov": 60.0, "lens_k1": k1, "lens_k2": k2, **{f"lens_{k}": v for k, v in centre.items()}}
    return json.dumps({"psn_system_name": "Stage", "camera": camera})


def _lens_from_settings(text: str) -> dict:
    return run_wizard_js("lensFromSettings(text)", functions=_LENS_FILE_FUNCTIONS, LENS_C_BOUND=C_BOUND, text=text)


def test_a_settings_file_gives_its_lens() -> None:
    assert _lens_from_settings(_settings(-0.21, 0.03, cx=0.02, cy=-0.01)) == {
        "k1": -0.21,
        "k2": 0.03,
        "cx": 0.02,
        "cy": -0.01,
    }


def test_a_settings_file_without_a_centre_reads_it_as_0_as_the_station_does() -> None:
    assert _lens_from_settings(_settings(-0.21, 0.03)) == {"k1": -0.21, "k2": 0.03, "cx": 0, "cy": 0}
    assert (CameraConfig().lens_cx, CameraConfig().lens_cy) == (0.0, 0.0)


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
        (_settings(-1.0, 0.0), _LIMIT),
        (_settings(-0.2, 0.0, cx=True), _NO_LENS),
        (_settings(-0.2, 0.0, cy="0.1"), _NO_LENS),
        (_settings(-0.2, 0.0, cx=None), _NO_LENS),
        ('{"camera": {"lens_k1": -0.2, "lens_k2": 0, "lens_cy": -1e400}}', _NO_LENS),
        (_settings(-0.2, 0.0, cx=C_BOUND + 1e-9), _LIMIT),
        (_settings(-0.2, 0.0, cy=-0.26), _LIMIT),
        (_settings(-0.6, -0.1, cx=0.2, cy=0.2), _LIMIT),
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
        "a pair past the limit",
        "a bool centre",
        "a string centre",
        "a null centre",
        "an infinite centre",
        "a centre past its bound",
        "a centre far past its bound",
        "a centre that takes the lens past the limit",
    ],
)
def test_a_settings_file_without_a_usable_lens_says_why(text: str, error: str) -> None:
    assert _lens_from_settings(text) == {"error": error}


def test_a_settings_file_lens_is_taken_exactly_when_the_station_keeps_it() -> None:
    lenses = [
        (float(a), float(b), x, y)
        for a in np.round(np.arange(-1.0, 0.51, 0.1), 2)
        for b in np.round(np.arange(-0.8, 0.31, 0.1), 2)
        for x, y in ((0.0, 0.0), (0.1, -0.08), (-0.24, 0.2), (0.3, 0.0))
    ]
    out = run_wizard_js(
        "texts.map(function(t) { return !lensFromSettings(t).error; })",
        functions=_LENS_FILE_FUNCTIONS,
        LENS_C_BOUND=C_BOUND,
        texts=[_settings(a, b, cx=x, cy=y) for a, b, x, y in lenses],
    )

    def kept(lens: tuple[float, float, float, float]) -> bool:
        cam = CameraConfig(lens_k1=lens[0], lens_k2=lens[1], lens_cx=lens[2], lens_cy=lens[3])
        return (cam.lens_k1, cam.lens_k2, cam.lens_cx, cam.lens_cy) == lens

    assert out == [kept(lens) for lens in lenses]
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
    "rectSized",
    "rectProblem",
    "cornerXYZ",
    "currentMarks",
    "orderMarks",
    "marksIncomplete",
    "incompleteLines",
)
_MARK_VARS = ("exactLen", "CORNERS", "PIN_PROBLEM", "DISTANCE_SIGMA")
# What the hints read of a partly filled camera.
_CAM_HINT_FUNCTIONS = ("camFields", "camPartialNamed")
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
var imageWidth = 0, imageHeight = 0, CAM_PRESETS = [], LENS_ENABLED = true, FS = null;
function overlayUnit() { return 1; } function calibGrid() { return { w: 1, d: 1, spacing: 1, cx: 0, cy: 0, z: 0 }; }
function gridLines() { return []; } function poly() { return ''; } function originMarkup() { return ''; }
function originPixel() { return null; } function projectShown() { return null; }
function solvedParamsHtml() { return ''; }
function wizReadLensCoeff() { return 0; } function wizLens() { return [0, 0, 0, 0]; }
function wizLensCentreText() { return ''; } function fmt() { return ''; } function reviewLensCoverage() { return ''; }
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
    "setLive",
    "showLive",
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
        functions=("esc", "pinBehind", "pinFlagged", "pinLabel", "renderCalibOverlay"),
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
        "(function() { renderCalibResult(); return $('cal-status').innerHTML; })()",
        functions=("esc", "setLive", "showLive", "renderHeightGroup", "renderCalibResult"),
        variables=("heightAsked",),
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
function lensMeasured() { return true; } function wizLensValid() { return true; }
function wizLens() { return [-0.1, -0.1, 0, 0]; } function wizLensCentreText() { return ''; }
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
        functions=(*_MARK_FUNCTIONS, *_CAM_HINT_FUNCTIONS, "esc", "lenSuffix", "renderCamGroup", "renderReq"),
        variables=(*_MARK_VARS, "camNamed"),
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


def test_the_lens_tab_key_goes_round_the_points_of_its_line() -> None:
    out = run_wizard_js(
        "(function() { var seen = [], stops = 0;"
        " function press(shift) { var e = { key: 'Tab', shiftKey: shift, preventDefault: function() { stops++; },"
        " stopPropagation: function() {} }; lensTabKey(e); seen.push(lensSelected.point); }"
        " [false, false, false, true, true, true].forEach(press);"
        " lensHold = false; press(false); return [seen, stops]; })()",
        functions=("lensTabKey",),
        prelude="var lensHold = true, lensSelected = { line: 1, point: 3 }, currentStep = 1, WIZ = { lens: 1 };"
        " var lensLines = [{}, {}]; function renderLens() {}",
    )
    # After the last point comes the first, before the first the last; without a held point, Tab is the browser's.
    assert out == [[4, 0, 1, 0, 4, 3, 3], 6]


# Two traced lines between a control before the picture and a suggestion bubble after it, drawn by ``lensHandle``.
# ``key`` runs the Lens step's Tab and Enter handlers, then the browser's own Tab: the next element in page order
# whose tabindex is 0. Each focus runs the step's focus handlers; one that moves is logged with the hold it left.
_TAB_PAGE = r"""
var currentStep = 1, WIZ = { lens: 1 }, lensSelected = null, lensHold = false, lensRefocus = false, lensDrag = null;
var lensLines = [0, 1].map(function() {
  return { mids: [{ on: true }, { on: true }, { on: true }], snapped: [true, true, true, true, true] };
});
var page = [], log = [], keydowns = [];
function lensPointPos() { return [0, 0]; }
function showLoupe() {} function hideLoupe() {} function renderLensHint() {} function renderFsBar() {}
function el(name, point) {
  return { name: name, attrs: {}, dataset: {}, isConnected: true,
    setAttribute: function(k, v) { this.attrs[k] = String(v); }, appendChild: function() {},
    closest: function() { return this.attrs['class'] && this.attrs['class'].indexOf('lens-point') >= 0 ? this : null; },
    focus: function() { focusOn(this); } };
}
function svgEl(tag, attrs) {
  var e = el(tag);
  Object.keys(attrs || {}).forEach(function(k) { e.setAttribute(k, attrs[k]); });
  return e;
}
var before = el('before'), bubble = el('bubble');
before.attrs.tabindex = bubble.attrs.tabindex = '0';
function name(e) { return e && e.dataset.line != null ? 'L' + e.dataset.line + 'P' + e.dataset.point : e && e.name; }
function build() {
  page = [before];
  lensLines.forEach(function(line, i) { for (var j = 0; j < 5; j++) page.push(lensHandle(line, i, j, 1)); });
  page.push(bubble);
}
var document = {
  activeElement: null,
  addEventListener: function(t, f) { if (t === 'keydown') keydowns.push(f); },
  querySelector: function(sel) {
    var m = /data-line="(\d+)"\]\[data-point="(\d+)"/.exec(sel);
    return page.filter(function(e) { return e.dataset.line == m[1] && e.dataset.point == m[2]; })[0] || null;
  }
};
function focusOn(e) {
  var moved = name(e) !== name(document.activeElement);
  document.activeElement = e;
  lensPointFocusIn({ target: e }); lensFocusElsewhere({ target: e });
  if (moved) log.push([name(e), lensHold]);
}
function renderLens() {
  var had = document.activeElement && document.activeElement.dataset.line != null;
  build();
  if (had) lensFocusSelected();
  if (lensHold) holdLensPoint();
}
function key(k, shift) {
  log = [];
  var e = { key: k, shiftKey: !!shift, target: document.activeElement, defaultPrevented: false,
    preventDefault: function() { this.defaultPrevented = true; }, stopPropagation: function() {} };
  lensTabKey(e);
  keydowns.forEach(function(f) { f(e); });
  if (k === 'Tab' && !e.defaultPrevented) {
    var d = shift ? -1 : 1;
    for (var i = page.indexOf(document.activeElement) + d; i >= 0 && i < page.length; i += d) {
      if (page[i].attrs.tabindex === '0') { focusOn(page[i]); break; }
    }
  }
  var sel = lensSelected || {};
  return [name(document.activeElement), sel.line, sel.point, lensHold, log];
}
build();
"""
_TAB_FUNCTIONS = (
    "pointHandle",
    "lensPointIsOn",
    "lensHandle",
    "lensTabKey",
    "lensEndHold",
    "lensPointFocusIn",
    "lensFocusElsewhere",
    "holdLensPoint",
    "dropLensPoint",
    "lensFocusSelected",
    "lensFocusEl",
)


_ENTER_ON_A_POINT = "document.addEventListener('keydown', function(e) {\n      if (e.key !== 'Enter'"


def _tab_through(keys: list) -> list:
    return run_wizard_js(
        "(function() { document.activeElement = before; return ks.map(function(k) { return key(k[0], k[1]); }); })()",
        functions=_TAB_FUNCTIONS,
        prelude=_TAB_PAGE + wizard_block(_ENTER_ON_A_POINT),
        ks=keys,
    )


def test_each_traced_line_is_one_tab_stop_its_first_point() -> None:
    out = run_wizard_js(
        "page.map(function(e) { return [name(e), e.attrs.tabindex]; })", functions=_TAB_FUNCTIONS, prelude=_TAB_PAGE
    )
    assert out[1:-1] == [[f"L{i}P{j}", "0" if j == 0 else "-1"] for i in (0, 1) for j in range(5)]


def test_tab_reaches_a_lines_first_point_and_holds_it() -> None:
    out = _tab_through([["Tab", False]])
    assert out == [["L0P0", 0, 0, True, [["L0P0", True]]]]


def test_a_held_point_tabs_round_its_line_both_ways() -> None:
    out = _tab_through([["Tab", False]] + [["Tab", False]] * 5 + [["Tab", True]] * 2)
    # The focus follows the selection round the line and never leaves it while the point is held.
    assert [o[:4] for o in out[1:]] == [
        ["L0P1", 0, 1, True],
        ["L0P2", 0, 2, True],
        ["L0P3", 0, 3, True],
        ["L0P4", 0, 4, True],
        ["L0P0", 0, 0, True],
        ["L0P4", 0, 4, True],
        ["L0P3", 0, 3, True],
    ]


def test_enter_ends_the_hold_and_tab_then_reaches_the_next_lines_first_point() -> None:
    out = _tab_through([["Tab", False], ["Tab", False], ["Tab", False], ["Enter", False], ["Tab", False]])
    enter, tab = out[3], out[4]
    # The focus stays on the point.
    assert enter == ["L0P2", 0, 2, False, []]
    assert tab == ["L1P0", 1, 0, True, [["L1P0", True]]]


def test_shift_tab_after_enter_moves_back_past_the_line_without_holding_its_first_point() -> None:
    tab, enter, back = ["Tab", False], ["Enter", False], ["Tab", True]
    out = _tab_through([tab, enter, tab, tab, tab, enter, back])
    # The line's own Tab stop takes the focus unheld, and the browser's Shift+Tab goes on from it.
    assert out[-1] == ["L0P0", 0, 0, True, [["L1P0", False], ["L0P0", True]]]


@pytest.mark.parametrize(
    ("keys", "result"),
    [
        ([["Tab", False], ["Enter", False], ["Tab", True]], ["before", 0, 0, False, [["before", False]]]),
        (
            [["Tab", False], ["Enter", False], ["Tab", False], ["Enter", False], ["Tab", False]],
            ["bubble", 1, 0, False, [["bubble", False]]],
        ),
        (
            [["Tab", False], ["Enter", False], ["Tab", False], ["Enter", False], ["Tab", False], ["Tab", True]],
            ["L1P0", 1, 0, True, [["L1P0", True]]],
        ),
    ],
    ids=["Shift+Tab from a first point", "Tab past the last line", "Shift+Tab from a bubble"],
)
def test_the_browser_moves_the_focus_off_and_onto_the_lines(keys: list, result: list) -> None:
    assert _tab_through(keys)[-1] == result


@pytest.mark.parametrize("shift", [False, True], ids=["Tab", "Shift+Tab"])
def test_the_lens_tab_key_leaves_the_bubbles_to_the_browser(shift: bool) -> None:
    out = run_wizard_js(
        "(function() { lensSelected = { line: 1, point: 2 }; document.activeElement = bubble;"
        " var e = { key: 'Tab', shiftKey: shift, target: bubble, defaultPrevented: false,"
        " preventDefault: function() { this.defaultPrevented = true; }, stopPropagation: function() {} };"
        " lensTabKey(e); return [e.defaultPrevented, name(document.activeElement), lensSelected, log]; })()",
        functions=_TAB_FUNCTIONS,
        prelude=_TAB_PAGE,
        shift=shift,
    )
    assert out == [False, "bubble", {"line": 1, "point": 2}, []]


def test_enter_or_esc_ends_the_hold_and_leaves_the_focus_on_the_point() -> None:
    out = run_wizard_js(
        "(function() { lensEndHold(); return [lensHold, lensSelected, drops, renders, blurs]; })()",
        functions=("lensEndHold",),
        prelude="var lensHold = true, lensSelected = { line: 1, point: 3 }, drops = 0, renders = 0, blurs = 0;"
        " var document = { activeElement: { blur: function() { blurs++; } } };"
        " function dropLensPoint() { drops++; lensHold = false; } function renderLens() { renders++; }",
    )
    # Still selected, so the redraw gives the point its focus back and the next Tab goes on from it.
    assert out == [False, {"line": 1, "point": 3}, 1, 1, 0]


_POINT_FOCUS_PAGE = """
var els = {};
function $(id) { if (!els[id]) els[id] = { innerHTML: '', style: {} }; return els[id]; }
var lensHintText = '', lensHintHtml = '', lensPress = false, S = { lensLoaded: null };
var lensPending = null, lensFit = null;
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
        functions=(
            "esc",
            "setLive",
            "lensMiddleOnly",
            "lensShownRating",
            "renderLensHint",
            "lensPointFocusIn",
            "lensTabKey",
        ),
        prelude="var lensSelected = null, lensHold = false, lensRefocus = false, lensDrag = null;" + _POINT_FOCUS_PAGE,
    )
    hint = "Move the point exactly onto the edge." if held else ""
    # Held as one clicked: the loupe and the hint are on it, and Tab goes round its line.
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
function $(id) { if (!els[id]) els[id] = { innerHTML: '', style: {} }; return els[id]; }
var lensHintText = '', lensHintHtml = '', lensPress = false;
function lensFitApplies(fit) { return fit.rating !== 'low'; }
function lensOpenCandidates() { return state.open; }
var S = { lensLoaded: state.loaded }, lensPending = state.pending, lensHold = state.hold, lensSelected = state.sel;
var lensLines = state.lines, lensFit = state.fit;
"""
_LINE = {"misfit": False}
_MIDDLE_HINT = "The result may already be good in the middle."
_MIDDLE_SUB = "If possible, add lines near the edges of the picture to measure them too. Or press Next."
_AGREE = "The result may already be good. If possible, add a line near the top edge to confirm it."
# The station's coverage hint below Good (lens_fit.coverage_hint).
_TOP_EDGE = "If possible, add a line near the top edge."


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
            "Confirm each of the lines that is straight in reality by pressing ✔. Reject the others, such as a curtain,"
            " a cloth or a cable, by pressing ✖."
            " 1 left to check. Or load a saved lens calibration.",
        ),
        (
            _hint_state(open=[1, 4, 7]),
            "Check the suggested lines.",
            "Confirm each of the lines that is straight in reality by pressing ✔. Reject the others, such as a curtain,"
            " a cloth or a cable, by pressing ✖."
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
            _hint_state(
                lines=[_LINE] * 4,
                fit={
                    "rating": "excellent",
                    "hint": "If possible, add lines near the top and left edges to confirm it.",
                    "edges_measured": False,
                },
            ),
            _MIDDLE_HINT,
            _MIDDLE_SUB,
        ),
        (
            _hint_state(lines=[_LINE] * 4, fit={"rating": "good", "hint": "", "edges_measured": False}),
            _MIDDLE_HINT,
            _MIDDLE_SUB,
        ),
        (
            _hint_state(lines=[_LINE] * 4, fit={"rating": "good", "hint": "", "edges_measured": True}),
            "The lens is measured.",
            "Press Next.",
        ),
        (
            _hint_state(lines=[_LINE] * 3, fit={"rating": "okay", "hint": _TOP_EDGE, "edges_measured": False}),
            "Improve the lines, or add more.",
            "Select a point and move it exactly onto the edge. Lines near the edges and corners of the picture help"
            " the most. If possible, add a line near the top edge.",
        ),
        (
            _hint_state(lines=[_LINE] * 4, fit={"rating": "okay", "hint": _AGREE}),
            "The result may already be good.",
            "If possible, add a line near the top edge to confirm it. Or press Next.",
        ),
        (
            _hint_state(lines=[_LINE] * 3, fit={"rating": "okay", "hint": _TOP_EDGE}),
            "Improve the lines, or add more.",
            "Select a point and move it exactly onto the edge. Lines near the edges and corners of the picture help"
            " the most. If possible, add a line near the top edge.",
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
        "only the middle, excellent",
        "only the middle, good",
        "the edges measured",
        "only the middle, fair",
        "lines agree",
        "applied",
        "not usable",
    ],  # fmt: skip
)
def test_the_lens_hint_names_the_one_next_thing_to_do(state: dict, heading: str, sub: str) -> None:
    out = run_wizard_js(
        "(function() { renderLensHint(); return [$('lens-hint').innerHTML, lensHintText]; })()",
        functions=("esc", "setLive", "lensMiddleOnly", "lensShownRating", "renderLensHint"),
        variables=("LENS_MIDDLE",),
        prelude="var state = " + json.dumps(state) + ";" + _HINT_PAGE,
    )
    assert out == [f'{heading}<div class="notice-sub">{sub}</div>', heading]


# The full-screen toolbar: markup written into it becomes new buttons, and a focused button that is replaced or
# switched off leaves the focus on the body, as the browser does.
_FS_TOOLBAR = r"""
function fsToolbar(doc) {
  var bar = { dataset: {}, buttons: [], on: {} };
  bar.addEventListener = function(t, f) { bar.on[t] = f; };
  bar.querySelectorAll = function() { return bar.buttons; };
  Object.defineProperty(bar, 'innerHTML', { set: function(html) {
    if (bar.buttons.indexOf(doc.activeElement) >= 0) doc.activeElement = doc.body;
    bar.buttons = [];
    html.replace(/<button[^>]*data-fs-act="([^"]+)"([^>]*)>([^<]*)<\/button>/g, function(m, act, rest, label) {
      var off = / disabled/.test(rest), b = { dataset: { fsAct: act }, textContent: label };
      b.closest = function() { return b; };
      b.focus = function() { doc.activeElement = b; };
      Object.defineProperty(b, 'disabled', { get: function() { return off; },
        set: function(v) { off = !!v; if (off && doc.activeElement === b) doc.activeElement = doc.body; } });
      bar.buttons.push(b);
    });
  } });
  return bar;
}
"""

# The hint box and the full-screen bar, on a page that counts every write to their words.
_LIVE_PAGE = (
    _FS_TOOLBAR
    + """
var els = {}, writes = {}, document = { activeElement: null, body: {} };
function $(id) {
  if (id === 'fs-actions') return els[id] = els[id] || fsToolbar(document);
  if (!els[id]) {
    var words = { innerHTML: '', textContent: '' };
    els[id] = { classList: { toggle: function() {} }, style: {} };
    ['innerHTML', 'textContent'].forEach(function(k) {
      Object.defineProperty(els[id], k, { get: function() { return words[k]; },
        set: function(v) { words[k] = v; writes[id] = (writes[id] || 0) + 1; } });
    });
  }
  return els[id];
}
var lensHintText = '', lensHintHtml = '', lensPress = false, FS = { key: 'lens' }, loupe = 'shown';
function lensFitApplies(fit) { return fit.rating !== 'low'; }
function lensOpenCandidates() { return []; }
function hideLoupe() { loupe = 'hidden'; }
var S = { lensLoaded: null }, lensPending = null, lensHold = true, lensSelected = { line: 0, point: 2 };
var lensLines = [{ misfit: false }], lensFit = null;
"""
)
_LIVE = (
    "esc",
    "setLive",
    "lensMiddleOnly",
    "lensShownRating",
    "renderLensHint",
    "renderFsBar",
    "fsActions",
    "dropLensPoint",
)


@pytest.mark.parametrize("full_screen", [True, False], ids=["full screen", "on the page"])
def test_ending_a_hold_moves_the_hint_and_the_full_screen_bar_on(full_screen: bool) -> None:
    out = run_wizard_js(
        "(function() { if (!fs) FS = null; renderLensHint(); renderFsBar(); var before = $('fs-hint').innerHTML;"
        " dropLensPoint(); return [before, $('lens-hint').innerHTML, $('fs-hint').innerHTML, loupe]; })()",
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


@pytest.mark.parametrize(("edges", "heading"), [(False, _MIDDLE_HINT), (None, "The lens is measured.")])
def test_the_full_screen_bar_says_when_only_the_middle_is_measured(edges: bool | None, heading: str) -> None:
    out = run_wizard_js(
        "(function() { lensHold = false; lensSelected = null; lensFit = { rating: 'good', hint: '' };"
        " if (edges !== null) lensFit.edges_measured = edges; renderLensHint(); renderFsBar();"
        " return [$('fs-hint').innerHTML, $('lens-hint').innerHTML]; })()",
        functions=_LIVE,
        variables=("LENS_MIDDLE",),
        prelude=_LIVE_PAGE,
        edges=edges,
    )
    assert out[0] == heading and out[1].startswith(heading + "<div")


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


# A full-screen Lens session: the page's press listeners, the toolbar's, and its buttons' actions.
_FS_LENS_PAGE = (
    _FS_TOOLBAR
    + """
var window = globalThis, docOn = [];
var overlay = { focus: function() { document.activeElement = overlay; } };
var document = { activeElement: null, body: {}, getElementById: function(id) { return $(id); },
  addEventListener: function(t, f, capture) { docOn.push([t, f, !!capture]); } };
var els = { 'fs-actions': fsToolbar(document), 'lens-overlay': overlay,
  'lens-lines': { contains: function(el) { return false; } } };
function $(id) {
  return els[id] = els[id] || { classList: { toggle: function() {} }, style: {}, textContent: '', innerHTML: '' };
}
var FS = { key: 'lens' }, lensHintText = 'Check the line.', hintText = '', lensFit = null;
function renderLensHint() {} function hideLoupe() {} function lensReleaseCandidate() {}
function lensMeasured() { return false; } function wizLensValid() { return true; }
function lensChanged() { renderLensToolbar(); renderFsBar(); }
function line() { return { misfit: false, mids: [{ on: true }, { on: true }, { on: true }] }; }
var lensLines = [line(), line()], lensSelected = null, lensHold = false;
function fire(type, target) {
  var e = { type: type, target: target, pointerType: 'mouse' };
  docOn.filter(function(l) { return l[0] === type && l[2]; }).forEach(function(l) { l[1](e); });
  if (els['fs-actions'].on[type]) els['fs-actions'].on[type](e);
  docOn.filter(function(l) { return l[0] === type && !l[2]; }).forEach(function(l) { l[1](e); });
}
function button(act) { return els['fs-actions'].buttons.filter(function(b) { return b.dataset.fsAct === act; })[0]; }
// A mouse press focuses the button; its click comes only if the button it pressed is still there on release.
function mouseClick(b) {
  fire('pointerdown', b); document.activeElement = b; fire('pointerup', b);
  if (els['fs-actions'].buttons.indexOf(b) >= 0) fire('click', b);
}
// Enter clicks the focused button, if a button has the focus.
function enter() { var t = document.activeElement; if (t && t.dataset) fire('click', t); }
function select(sel, held) { lensSelected = sel; lensHold = held; renderLensToolbar(); renderFsBar(); }
function focusName() {
  var b = els['fs-actions'].buttons.indexOf(document.activeElement);
  return b >= 0 ? document.activeElement.dataset.fsAct : document.activeElement === overlay ? 'picture' : 'body';
}
"""
)


def _fs_lens(expression: str, **inputs) -> list:
    listeners = "\n".join(
        wizard_block(start)
        for start in (
            "document.addEventListener('pointerdown', function() { clearTimeout(lensPressTimer)",
            "document.addEventListener('pointerup', function(e) {",
            "['click', 'pointercancel', 'contextmenu'].forEach(",
            "document.addEventListener('pointerdown', function(e) { if (lensHold",
            "$('fs-actions').addEventListener('click', function(e) {",
        )
    )
    actions = "\n".join(wizard_window_function(f) for f in ("lensToggleSelectedPoint", "lensDeleteSelectedLine"))
    return run_wizard_js(
        f"(function() {{ {listeners}\n{actions}\n{expression} }})()",
        functions=("esc", "setLive", "renderFsBar", "fsActions", "renderLensToolbar", "dropLensPoint", "lensPressEnd"),
        variables=("lensPress",),
        prelude=_FS_LENS_PAGE,
        **inputs,
    )


@pytest.mark.parametrize("held", [False, True], ids=["line selected", "point held"])
def test_a_mouse_click_on_delete_line_in_full_screen_deletes_the_line(held: bool) -> None:
    out = _fs_lens(
        "select({ line: 0, point: held ? 2 : null }, held); var b = button('delete-line'); mouseClick(b);"
        " return [button('delete-line') === b, lensLines.length, lensSelected, lensHold, b.disabled];",
        held=held,
    )
    # The press and its release redraw the toolbar: the pressed button stays, so its click still lands.
    assert out == [True, 1, None, False, True]


@pytest.mark.parametrize("held", [False, True], ids=["point selected", "point held"])
def test_a_mouse_click_on_point_off_in_full_screen_switches_the_point_off(held: bool) -> None:
    out = _fs_lens(
        "select({ line: 0, point: 2 }, held); var b = button('point-off'), seen = [b.textContent]; mouseClick(b);"
        " seen.push(button('point-off') === b, lensLines[0].mids[1].on, b.textContent, b.disabled); mouseClick(b);"
        " seen.push(lensLines[0].mids[1].on, b.textContent); return seen;",
        held=held,
    )
    assert out == ["Point off", True, False, "Point on", False, True, "Point off"]


def test_enter_on_point_off_in_full_screen_keeps_the_focus_on_the_button() -> None:
    out = _fs_lens(
        "select({ line: 0, point: 2 }, false); var b = button('point-off'); b.focus(); enter();"
        " var seen = [focusName(), lensLines[0].mids[1].on]; enter();"
        " return seen.concat([focusName(), lensLines[0].mids[1].on, document.activeElement === b]);"
    )
    # Enter again switches it back on: the keys stay on the toolbar.
    assert out == ["point-off", False, "point-off", True, True]


@pytest.mark.parametrize("press", ["enter", "mouse"])
def test_a_focused_full_screen_button_switched_off_hands_the_focus_to_the_picture(press: str) -> None:
    out = _fs_lens(
        "select({ line: 1, point: 2 }, false); var b = button('delete-line');"
        " if (press === 'enter') { b.focus(); enter(); } else mouseClick(b);"
        " return [focusName(), lensLines.length, button('point-off').disabled, b.disabled];",
        press=press,
    )
    # Delete line leaves nothing selected: both buttons are off, and the arrow keys and Tab start from the picture.
    assert out == ["picture", 1, True, True]


def test_a_full_screen_button_keeps_the_focus_when_it_stays_on() -> None:
    out = _fs_lens(
        "select({ line: 0, point: 2 }, false); button('delete-line').focus();"
        " select({ line: 0, point: null }, false); return [focusName(), button('point-off').disabled];"
    )
    # Only the switched-off button gives the focus away.
    assert out == ["delete-line", True]


@pytest.mark.parametrize("key", ["cal", "review"])
def test_the_full_screen_bar_has_no_buttons_outside_the_lens_step(key: str) -> None:
    out = _fs_lens(
        "select({ line: 0, point: 2 }, false); FS = { key: key }; S = { mode: 'simple', solve: null };"
        " renderFsBar(); var n = els['fs-actions'].buttons.length; FS = { key: 'lens' }; renderFsBar();"
        " return [n, els['fs-actions'].buttons.map(function(b) { return b.dataset.fsAct; })];",
        key=key,
    )
    assert out == [0, ["point-off", "delete-line"]]


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
        ({**_EMPTY, "w": "1200", "d": "600"}, "Width and depth must be between 0 and 200 m."),
        ({**_EMPTY, "w": "0", "d": "6"}, "Width and depth must be between 0 and 200 m."),
        ({**_EMPTY, "w": "12", "d": "600", "origin": "custom"}, "Width and depth must be between 0 and 200 m."),
    ],
    ids=[
        "size first",
        "then the origin",
        "then a custom centre",
        "complete",
        "too large, no origin",
        "zero, no origin",
        "too deep, custom centre missing",
    ],
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
        functions=(*_MARK_FUNCTIONS, *_CAM_HINT_FUNCTIONS, "esc", "lenText", "fmt", "renderSimpleReq"),
        variables=(*_MARK_VARS, "CORNER_TITLES", "camNamed"),
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
        (
            {**_EMPTY, "w": "1200", "d": "600"},
            "back",
            ["rect", "Width and depth must be between 0 and 200 m.",
             "Only the corners need a mark, such as a tape cross. The Setup Wizard then draws the rectangle on the"
             " picture."],
        ),
        (
            {**_EMPTY, "w": "12", "d": "600", "origin": "custom"},
            "back",
            ["rect", "Width and depth must be between 0 and 200 m.",
             "Only the corners need a mark, such as a tape cross. The Setup Wizard then draws the rectangle on the"
             " picture."],
        ),
    ],
    ids=["nothing", "the size", "the origin", "the corners", "a size out of range", "out of range, custom"],
)  # fmt: skip
def test_the_simple_hint_sits_above_the_step_it_is_about(rect: dict, loc: str, shown: list) -> None:
    assert _simple_hint(rect, loc) == shown


_WIZ_HINT_PAGE = """
var writes = [], renders = 0;
var hint = { parentNode: null };
Object.defineProperty(hint, 'innerHTML', { set: function(v) { writes.push(v); },
  get: function() { return writes[writes.length - 1]; } });
function slot() { return { appendChild: function(el) { el.parentNode = this; } }; }
var slots = { 'hint-slot-rect': slot(), 'hint-slot-image': slot() };
function $(id) { return id === 'wiz-hint' ? hint : slots[id]; }
function renderFsBar() { renders++; }
"""


def test_the_hint_box_is_rewritten_only_when_its_words_or_its_place_change() -> None:
    out = run_wizard_js(
        "(function() { showHint('rect', 'A', 'a'); showHint('rect', 'A', 'a'); showHint('rect', 'B', 'a');"
        " showHint('image', 'B', 'a'); showHint('image', 'B', 'a'); return [writes.length, hintText, renders]; })()",
        functions=("esc", "setLive", "showHint"),
        variables=("hintText",),
        prelude=_WIZ_HINT_PAGE,
    )
    # A polite live region: a redraw with the same words is not read out again; a new slot is.
    assert out == [3, "B", 5]


_CORNER_ENTER_PAGE = """
var S = { selCorner: 'DSR', cornerMoved: { DSL: true, DSR: false, USR: false, USL: false } };
var solves = 0, blurs = 0, prevented = 0;
var document = { activeElement: { blur: function() { blurs++; } }, querySelector: function() { return null; } };
function hideLoupe() {} function solveSoon() { solves++; } function saveToSession() {}
"""


@pytest.mark.parametrize(
    ("key", "selected", "result"),
    [
        ("Enter", "DSR", [None, True, 1, 1]),
        ("Enter", "DSL", [None, False, 0, 1]),
        ("Enter", None, [None, False, 0, 0]),
        ("Tab", "DSR", ["DSR", False, 0, 0]),
    ],
    ids=["places an unplaced corner", "a placed corner", "nothing selected", "another key"],
)
def test_enter_on_a_corner_places_it_and_leaves_it_the_focus(key: str, selected, result: list) -> None:
    out = run_wizard_js(
        "(function() { S.selCorner = selected;"
        " cornerEnterKey({ key: key, preventDefault: function() { prevented++; } });"
        " return [S.selCorner, S.cornerMoved.DSR, solves, prevented, blurs]; })()",
        functions=("dropCorner", "cornerEnterKey"),
        prelude=_CORNER_ENTER_PAGE,
        key=key,
        selected=selected,
    )
    # The focus stays on the corner, so the next Tab moves on to the next one, not back to DSL.
    assert out == [*result, 0]


_REDRAW_FOCUS_PAGE = """
var made = {}, focusinSelects = 0, FS = null;
function handle(mark) {
  return { mark: mark, focused: 0, getAttribute: function() { return mark; },
    closest: function(sel) { return sel === '#cal-pins .pin-handle' ? this : null; },
    focus: function() {
      this.focused++; if (!calRefocus && S.selCorner !== mark) focusinSelects++; document.activeElement = this; } };
}
var document = { activeElement: null, querySelector: function(sel) {
  var m = /data-mark="([^"]+)"/.exec(sel); if (!m) return null; return made[m[1]] || (made[m[1]] = handle(m[1])); },
  querySelectorAll: function() { return []; } };
var els = {};
function $(id) {
  if (!els[id]) els[id] = { innerHTML: '', setAttribute: function() {}, classList: { toggle: function() {} },
    getBoundingClientRect: function() { return { width: 0 }; } };
  return els[id];
}
var imageWidth = 1920, imageHeight = 1080, held = 0;
function holdCorner() { held++; } function overlayUnit() { return 1; } function pairsFor() { return []; }
function originPixel() { return null; } function originMarkup() { return ''; } function pinFlagged() { return false; }
function currentMarks() { return [{ id: 1, name: 'DSL' }, { id: 2, name: 'DSR' }]; }
var S = { mode: 'simple', solve: null, selPin: null, selCorner: null, pinMenu: null, pair: 0,
  cornerPins: { DSL: [10, 10], DSR: [50, 10] }, cornerMoved: { DSL: true, DSR: true } };
"""


@pytest.mark.parametrize(
    ("focus", "selected", "result"),
    [
        ("DSR", None, [1, 0, 0]),
        (None, None, [0, 0, 0]),
        ("DSR", "DSR", [0, 0, 1]),
    ],
    ids=["a focused corner", "nothing focused", "a held corner"],
)
def test_a_redraw_gives_a_focused_pin_its_focus_back_without_selecting_it(focus, selected, result: list) -> None:
    out = run_wizard_js(
        "(function() { if (focus) document.activeElement = handle(focus); S.selCorner = selected; renderCalibOverlay();"
        " return [made.DSR ? made.DSR.focused : 0, focusinSelects, held]; })()",
        functions=("esc", "pinLabel", "renderCalibOverlay"),
        variables=("calRefocus", "CORNERS"),
        prelude=_REDRAW_FOCUS_PAGE,
        focus=focus,
        selected=selected,
    )
    # The new handle takes the focus while calRefocus is up, so the focus listener selects nothing.
    assert out == result


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
            "lensBandHalf",
            "lensPointWindow",
            "lensNearFits",
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
# Lens: a used suggestion is snapped onto its own edge
# ---------------------------------------------------------------------------

_SUGGESTION = [[100.0, 100.0], [300.0, 108.0], [500.0, 112.0], [700.0, 114.0], [900.0, 115.0]]
_SNAP_PAGE = """
var imageWidth = 1920, imageHeight = 1080, lensLines = [], lensPending = null, lensSelected = null, lensCanvas = {};
var bodies = [], changed = 0;
function renderLens() {} function lensShowNotice() {} function lensHotCand() {} function lensChanged() { changed++; }
function lensBand() { return { step: 4, half: 192, cols: 2, rows: 385, data: '' }; }
function fetch(url, opts) {
  bodies.push(JSON.parse(opts.body));
  return { then: function() { return this; }, catch: function() { return this; } };
}
"""
_SNAP_FUNCTIONS = (
    "lensFrame",
    "lensClamp",
    "lensSetMidsFromPositions",
    "lensSetMidFromPos",
    "lensMovePoint",
    "lensBandHalf",
    "lensPointWindow",
    "lensNearFits",
    "lensSnapLine",
    "lensTraceClick",
    "lensAddCandidate",
)


@pytest.mark.parametrize(
    ("steps", "near"),
    [
        ("lensAddCandidate(0);", {"points": _SUGGESTION, "polarity": -1}),
        ("lensTraceClick([100, 500]); lensTraceClick([900, 520]);", None),
        ("lensAddCandidate(0); lensMovePoint(lensLines[0], 4, [950, 130]); lensSnapLine(lensLines[0]);", None),
        ("lensAddCandidate(0); lensMovePoint(lensLines[0], 2, [500, 120]); lensSnapLine(lensLines[0]);", None),
    ],
    ids=["a used suggestion", "a drawn line", "an end moved since", "a middle point moved since"],
)
def test_only_a_suggestion_just_used_is_snapped_near_its_own_edge(steps: str, near) -> None:
    out = run_wizard_js(
        "(function() { " + steps + " return bodies[bodies.length - 1]; })()",
        functions=_SNAP_FUNCTIONS,
        variables=("LENS_T", "LENS_MIN_LINE_PX"),
        prelude=_SNAP_PAGE + f"var lensCandidates = [{{ samples: {json.dumps(_SUGGESTION)}, polarity: -1, used: false,"
        " rejected: false }];",
    )
    assert out.get("near") == near
    if near is not None:
        # The chord runs between the suggestion's own ends, the band the station searches around it.
        assert (out["p0"], out["p1"]) == (_SUGGESTION[0], _SUGGESTION[4])


def test_a_suggestion_bowed_past_its_band_keeps_its_samples_unsnapped() -> None:
    bowed = [[100.0, 100.0], [300.0, 260.0], [500.0, 300.0], [700.0, 260.0], [900.0, 100.0]]
    out = run_wizard_js(
        "(function() { lensAddCandidate(0); return [bodies.length, changed, [0, 1, 2, 3, 4].map(function(j) {"
        " return lensPointPos(lensLines[0], j).map(Math.round); })]; })()",
        functions=(*_SNAP_FUNCTIONS, "lensPointPos"),
        variables=("LENS_T", "LENS_MIN_LINE_PX"),
        prelude=_SNAP_PAGE
        + f"var lensCandidates = [{{ samples: {json.dumps(bowed)}, polarity: 1, used: false, rejected: false }}];",
    )
    # No request the station would refuse: the line stays where the suggestion was, as after a refusal.
    assert out == [0, 1, bowed]


@pytest.mark.parametrize("width", [640, 1920, 2160, 3840])
def test_the_page_sends_near_only_when_the_station_takes_it(width: int) -> None:
    from openfollow.scene.edge_snap import NearEdge, point_window, snap_line_to_edges
    from tests._lens_band import cut_band

    height = width * 9 // 16
    limit = band_half_size(width) - point_window(width) - 1
    a, b = [0.1 * width, 0.5 * height], [0.9 * width, 0.5 * height]
    span = b[0] - a[0]
    cases = [
        [a, [a[0] + 0.25 * span, a[1] - off], [a[0] + 0.5 * span, a[1] + sag], [a[0] + 0.75 * span, a[1]], b]
        for sag in (limit - 1.0, limit - 0.25, limit + 0.25, limit + 1.0)
        for off in (0.0, sag)
    ] + [
        [a, [a[0] + f * span, a[1]], [a[0] + 0.5 * span, a[1]], [a[0] + 0.75 * span, a[1]], b]
        for f in (-0.02, -0.005, 1.005, 1.02)
    ]
    sent = run_wizard_js(
        "cases.map(lensNearFits)",
        functions=("lensBandHalf", "lensPointWindow", "lensNearFits"),
        prelude=f"var imageWidth = {width};",
        cases=cases,
    )
    blank = np.zeros((height, width))
    taken = []
    for samples in cases:
        try:
            snap_line_to_edges(a, b, cut_band(blank, a, b), width, height, near=NearEdge(tuple(map(tuple, samples)), 0))
            taken.append(True)
        except ValueError as exc:
            assert "near points must lie along the line" in str(exc)
            taken.append(False)
    # Never a body the station refuses; the page rounds the point window up where the station rounds half to even.
    assert not any(s and not t for s, t in zip(sent, taken, strict=True))
    assert any(sent) and not all(taken)
    if width != 2160:
        assert sent == taken


def test_the_suggestions_keep_their_contrast_side() -> None:
    answer = {
        "candidates": [
            {"points": [], "samples": _SUGGESTION, "polarity": 1},
            {"points": [], "samples": _SUGGESTION, "polarity": -1},
            {"points": [], "samples": _SUGGESTION, "polarity": 0},
            {"points": [], "samples": _SUGGESTION, "polarity": "1"},
            {"points": [], "samples": _SUGGESTION},
        ]
    }
    out = run_wizard_js(
        "(function() { lensRequestEdges(); return lensCandidates.map(function(c) { return c.polarity; }); })()",
        functions=("lensEdgeScale", "lensEdgeSize", "lensScaledLuma", "lensBase64", "lensRequestEdges", "lensSameEnds"),
        prelude="var LENS_DEV = false, imageWidth = 8, imageHeight = 4, lensCandidates = [], lensRejected = [];"
        " var lensLines = [], lensCanvas = { getContext: function() { return { getImageData: function() {"
        " return { data: new Uint8Array(8 * 4 * 4) }; } }; } };"
        " function renderLens() {} function lensPaintEdges() {} function lensCandidateOf() {}"
        " function settled(v) { return { then: function(f) { var r = f(v); return r && r.then ? r : settled(r); },"
        " catch: function() { return this; } }; }"
        " function fetch() { return settled({ ok: true, json: function() {"
        f" return settled({json.dumps(answer)}); }} }}); }}",
    )
    # Only a known side is sent back; anything else is a thin line's, 0.
    assert out == [1, -1, 0, 0, 0]


# ---------------------------------------------------------------------------
# Camera height: asked while the camera looks almost straight down
# ---------------------------------------------------------------------------

_HEIGHT_RESULT = (
    "(function() { renderCalibResult(); return [$('cal-height').hidden, $('cal-height-ask').hidden,"
    " $('cal-height-note').hidden, $('cal-height-note').innerHTML]; })()"
)


@pytest.mark.parametrize(
    ("solve", "typed", "height", "shown"),
    [
        ({"cam": {"fov": 60}, "steep": False}, "", None, [True, True, True, ""]),
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
        ({"cam": {"fov": 60}, "steep": False}, "9.", None, [False, True, True, ""]),
        ({"error": "No camera fits.", "steep": True}, "", None, [True, True, True, ""]),
    ],
    ids=["not steep", "steep", "steep, entered", "no longer steep", "no solve", "half typed", "no camera"],
)
def test_the_height_is_asked_while_steep_and_noted_once_entered(solve: dict, typed: str, height, shown: list) -> None:
    out = run_wizard_js(
        _HEIGHT_RESULT,
        functions=("esc", "setLive", "showLive", "renderHeightGroup", "renderCalibResult", "lenText", "fmt"),
        variables=("heightAsked",),
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
        functions=("esc", "setLive", "showLive", "renderHeightGroup", "renderCalibResult", "lenText", "fmt"),
        variables=("heightAsked",),
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
        functions=("len", "lenRough", "refreshEchoes", "readHeight"),
        variables=("exactLen", "DISTANCE_SIGMA"),
        prelude=length_page(system, {}) + _DOM + "var S = { height: 'before' };",
    )
    assert out == (None if height is None else pytest.approx(height))


@pytest.mark.parametrize(
    ("system", "typed", "error"),
    [
        ("metric", "9.8", ""),
        ("metric", "", ""),
        ("metric", "abc", "Enter the height of the camera's lens."),
        ("metric", "9..8", "Enter the height of the camera's lens."),
        ("metric", "10000.5", "Enter a height within 10000.0 m of the point of origin."),
        ("metric", "-10000.5", "Enter a height within 10000.0 m of the point of origin."),
        ("imperial", "32809 ft", "Enter a height within 32808.4 ft of the point of origin."),
        ("imperial", "32 ft 2 in", ""),
        ("metric", None, "Enter the height of the camera's lens."),
    ],
    ids=[
        "valid",
        "empty",
        "unreadable",
        "two points",
        "too far up",
        "too far down",
        "too far in feet",
        "feet",
        "a number field it cannot read",
    ],
)
def test_a_height_that_cannot_be_used_says_why_under_the_field(system: str, typed: str | None, error: str) -> None:
    # A number field with text it cannot read reports an empty value and badInput.
    fill = (
        "$('cam_height').validity = { badInput: true };"
        if typed is None
        else f"$('cam_height').value = {json.dumps(typed)};"
    )
    out = run_wizard_js(
        f"(function() {{ {fill} readHeight();"
        " var e = $('cam-height-error');"
        " return [e.textContent, e.hidden, $('cam_height').invalid, S.height === null]; })()",
        functions=("len", "lenRough", "refreshEchoes", "readHeight"),
        variables=("exactLen", "DISTANCE_SIGMA"),
        prelude=length_page(system, {}) + _DOM + "var S = { height: 'before' };"
        " $('cam_height').setAttribute = function(k, v) { if (k === 'aria-invalid') this.invalid = v; };",
    )
    # An unusable height is no height: the camera is calculated without one, and the field says why.
    assert out == [error, not error, "true" if error else "false", bool(error) or not typed]


@pytest.mark.parametrize("height", [9.8, None])
def test_the_solve_carries_the_height_or_null(height) -> None:
    out = run_wizard_js(
        "solveBody([], 7)",
        functions=("solveBody",),
        prelude=f"var S = {{ mode: 'simple', height: {json.dumps(height)}, lensCentre: [0.02, -0.01] }};"
        " var SAVED = { fov: 60 }, imageWidth = 1920, imageHeight = 1080, WIZ_SOLVE_PAGE = 'p';"
        " function hintPos() { return null; }"
        " function wizReadLensCoeff(id) { return id === 'wiz_lens_k1' ? -0.2 : 0.01; }",
    )
    assert "camera_height" in out and out["camera_height"] == height
    # The pins are undistorted with the whole lens, its centre included.
    assert out["camera"] == {"lens_k1": -0.2, "lens_k2": 0.01, "lens_cx": 0.02, "lens_cy": -0.01}


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
        functions=("len", "lenField", "camFields", "camUnusable", "readCam", "readHeight", "setUnitSystem"),
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


# ---------------------------------------------------------------------------
# Focus: leaving full screen, the height field, the pin menu, Reset Corners
# ---------------------------------------------------------------------------

_CLOSE_FULL_PAGE = """
var focused = [], lensSelected = null, lensPending = null;
function toggleFsHelp() {} function closePinMenu() {} function dropCorner() {} function dropLensPoint() {}
function fsRender() {}
var shell = { hidden: false };
function $() { return shell; }
var btn = { focus: function() { focused.push(document.fullscreenElement ? 'behind full screen' : 'on the page'); } };
var FS = { key: 'cal', cont: { style: {}, querySelector: function() { return btn; } },
  ph: { parentNode: { insertBefore: function() {}, removeChild: function() {} } }, x: 0, y: 0 };
var window = { scrollTo: function() {} };
var document = { body: { style: {} }, fullscreenElement: null,
  exitFullscreen: function() { return new Promise(function(ok, no) {
    setTimeout(function() { document.fullscreenElement = null; if (refused) no(); else ok(); }, 0); }); } };
"""


@pytest.mark.parametrize(
    ("full", "refused", "at_once"),
    [(True, False, []), (True, True, []), (False, False, ["on the page"])],
    ids=["full screen", "exit refused", "window-filling shell"],
)
def test_leaving_full_screen_gives_the_focus_to_the_steps_full_screen_button(
    full: bool, refused: bool, at_once
) -> None:
    out = run_wizard_js(
        "(function() { document.fullscreenElement = full ? shell : null; closeFull(); var now = focused.slice();"
        " return new Promise(function(r) { setTimeout(function() { r([now, focused]); }, 5); }); })()",
        functions=("closeFull",),
        prelude=_CLOSE_FULL_PAGE,
        full=full,
        refused=refused,
    )
    # The page behind the full-screen element takes no focus: the button is focused once the browser is out.
    assert out == [at_once, ["on the page"]]


_ASKED = {"cam": {"fov": 60}, "steep": True, "withHeight": False}
_NO_FIT = {"error": "No camera fits at this camera height.", "withHeight": True}
_LEVEL = {"cam": {"fov": 60}, "steep": False, "withHeight": False}
_LEVEL_AT_HEIGHT = {"cam": {"fov": 60}, "steep": False, "withHeight": True}


@pytest.mark.parametrize(
    ("steps", "shown"),
    [
        ([(_ASKED, ""), (_NO_FIT, "0"), (_NO_FIT, "")], [True, True, True]),
        ([(_ASKED, ""), (_LEVEL_AT_HEIGHT, "12"), (_LEVEL_AT_HEIGHT, "")], [True, True, True]),
        ([(_ASKED, ""), (_LEVEL, "")], [True, False]),
        ([(_LEVEL, ""), (_NO_FIT, "")], [False, False]),
    ],
    ids=["no camera at the typed height", "level at the typed height", "level without a height", "never asked"],
)
def test_the_height_stays_asked_until_a_camera_without_a_height_is_not_steep(steps: list, shown: list) -> None:
    out = run_wizard_js(
        "(function() { return steps.map(function(st) {"
        " S.solve = Object.assign({ worst: 0.4, pinned: 4, reasons: [], marks: [] }, st[0]);"
        " $('cam_height').value = st[1]; renderCalibResult(); return !$('cal-height').hidden; }); })()",
        functions=("esc", "setLive", "showLive", "renderHeightGroup", "renderCalibResult", "lenText", "fmt"),
        variables=("heightAsked",),
        prelude=length_page("metric", {})
        + _DOM
        + _REVIEW_STUBS
        + "function lensMeasured() { return true; } var S = { mode: 'simple', pair: 0, height: null };",
        steps=steps,
    )
    # Clearing a height no camera fits never hides the field being typed in.
    assert out == shown


@pytest.mark.parametrize(
    ("focus", "bad", "shown"),
    [(True, False, True), (False, True, True), (False, False, False)],
    ids=["focused", "unreadable text", "neither"],
)
def test_an_empty_height_field_stays_while_it_has_the_focus_or_unreadable_text(
    focus: bool, bad: bool, shown: bool
) -> None:
    out = run_wizard_js(
        "(function() { var f = $('cam_height'); f.validity = { badInput: bad }; if (focus) document.activeElement = f;"
        " renderCalibResult(); return !$('cal-height').hidden; })()",
        functions=("esc", "setLive", "showLive", "renderHeightGroup", "renderCalibResult", "lenText", "fmt"),
        variables=("heightAsked",),
        prelude=length_page("metric", {})
        + _DOM
        + _REVIEW_STUBS
        + "function lensMeasured() { return true; } var S = { mode: 'simple', pair: 0, height: null };"
        " S.solve = { cam: { fov: 60 }, steep: false, worst: 0.4, pinned: 4, reasons: [], marks: [] };",
        focus=focus,
        bad=bad,
    )
    assert out is shown


@pytest.mark.parametrize("height", [9.8, None])
def test_a_solve_remembers_whether_it_was_sent_with_a_height(height) -> None:
    out = run_wizard_js(
        "(async function() { runSolve();"
        " answer({ status: 200, json: function() { return Promise.resolve({ camera: {} }); } });"
        " await new Promise(function(r) { setImmediate(r); }); return S.solve.withHeight; })()",
        functions=("runSolve",),
        prelude=_DOM + "var S = { solve: null }, solveSeq = 0, solveTimer = null, solvePending = false;"
        " var imageWidth = 1920;"
        " var answer; function fetch() { return new Promise(function(r) { answer = r; }); }"
        " function clearTimeout() {} function solveState() { return { marks: [], usable: [{ id: 1 }] }; }"
        " function solveFromAnswer() { return {}; } function afterSolve() {}"
        f" function solveBody() {{ return {{ camera_height: {json.dumps(height)} }}; }}",
    )
    assert out is (height is not None)


# A page whose elements record every focus, and every listener the wizard adds.
_MENU_PAGE = """
var listeners = [], focused = null, drawn = {}, renders = 0;
function fakeEl(id) {
  return { id: id, hidden: false, style: {}, focus: function() { focused = id; }, setAttribute: function() {},
    addEventListener: function(t, f, capture) { listeners.push([id, t, f, !!capture]); },
    contains: function(x) { return !!x && x.inside === id; } };
}
var els = { 'modal-root': Object.assign(fakeEl('modal-root'), { hidden: true }) };
function $(id) { return els[id] || (els[id] = fakeEl(id)); }
var document = { activeElement: null, body: { classList: { contains: function() { return false; } } },
  addEventListener: function(t, f, capture) { listeners.push(['document', t, f, !!capture]); },
  querySelectorAll: function() { return []; },
  querySelector: function(sel) {
    var m = /#cal-pins \\[data-mark="(\\w+)"\\]/.exec(sel);
    return m && drawn[m[1]] ? { focus: function() { focused = 'pin ' + m[1]; } } : null;
  } };
function fire(id, type, ev) {
  listeners.filter(function(l) { return l[0] === id && l[1] === type; }).forEach(function(l) { l[2](ev); });
}
function renderCalibOverlay() {
  renders++; drawn = {}; S.points.forEach(function(p) { if (p.pin) drawn[p.id] = true; });
}
function renderSimpleLoc() {} function bindDrag() {} function hideLoupe() {} function renderAdvRows() {}
function solveSoon() {} function saveToSession() {} function dropCorner() {}
function camChanged() {} function cornerEnterKey() {} function pinMenuCheck() {}
var results = 0, reqs = 0; function renderHeightGroup() { results++; } function renderReq() { reqs++; }
var FS = null, lensHold = false, lensPending = null, currentStep = 2, WIZ = { lens: 1 };
var S = { mode: 'advanced', selPin: null, selCorner: null, pinMenu: null,
  points: [{ id: 2, name: 'DSR', pin: [5, 5] }, { id: 3, name: 'USR', pin: [6, 6] }] };
drawn = { 2: true, 3: true };
""" + wizard_var("gestureSeq")
_MENU_FUNCTIONS = ("closePinMenu", "focusCalPin", "setPin", "deletePoint", "camFields", "calibInit")


def _menu_click(act: str) -> str:
    return (
        f"fire('pin-menu', 'click', {{ target: {{ closest: function(sel) {{"
        f" return sel === '[data-pm]' ? {{ dataset: {{ pm: {json.dumps(act)} }} }} : null; }} }} }})"
    )


@pytest.mark.parametrize(
    ("menu_pin", "act", "in_menu", "focus"),
    [
        (2, "close", True, "pin 2"),
        (None, "close", True, "cal-overlay"),
        (2, "unpin", True, "cal-overlay"),
        (2, "delete", True, "cal-overlay"),
        (2, "close", False, None),
    ],
    ids=["x on a pin's menu", "x on a spot's menu", "remove pin", "delete point", "focus already elsewhere"],
)
def test_closing_the_pin_menu_gives_its_focus_to_the_pin_or_the_picture(
    menu_pin, act: str, in_menu: bool, focus
) -> None:
    out = run_wizard_js(
        "(function() { calibInit(); S.pinMenu = { p: [1, 1], form: null, pin: menu_pin };"
        " document.activeElement = in_menu ? { inside: 'pin-menu' } : null; "
        + _menu_click(act)
        + "; return [focused, S.pinMenu]; })()",
        functions=_MENU_FUNCTIONS,
        prelude=_MENU_PAGE,
        menu_pin=menu_pin,
        in_menu=in_menu,
    )
    assert out == [focus, None]


@pytest.mark.parametrize(("menu_pin", "focus"), [(2, "pin 2"), (None, "cal-overlay")], ids=["pin", "spot"])
def test_esc_in_the_pin_menu_gives_its_focus_back(menu_pin, focus: str) -> None:
    out = run_wizard_js(
        "(function() { eval(escKey); S.pinMenu = { p: [1, 1], form: null, pin: menu_pin };"
        " document.activeElement = { inside: 'pin-menu' }; fire('document', 'keydown', { key: 'Escape' });"
        " return [focused, S.pinMenu]; })()",
        functions=_MENU_FUNCTIONS,
        prelude=_MENU_PAGE,
        menu_pin=menu_pin,
        escKey=wizard_block("document.addEventListener('keydown', function(e) {\n    if (e.key !== 'Escape') return;"),
    )
    assert out == [focus, None]


@pytest.mark.parametrize(
    ("place", "focus"), [("3", "pin 3"), ("new", "pin 4")], ids=["an existing point", "a new point"]
)
def test_a_point_placed_from_the_menu_takes_the_focus(place: str, focus: str) -> None:
    out = run_wizard_js(
        "(function() { S.points[1].pin = null; delete drawn[3]; S.nextId = 4;"
        " S.pinMenu = { p: [9, 9], form: place === 'new' ? 'known' : null, pin: null };"
        " document.activeElement = { inside: 'pin-menu' };"
        " if (place === 'new') placeNewFromMenu(); else placeFromMenu(place); return [focused, S.selPin]; })()",
        functions=(*_MENU_FUNCTIONS, "placeFromMenu", "placeNewFromMenu"),
        prelude=_MENU_PAGE + "function pinMenuCheck() { return true; }"
        " function pinMenuVals() { return { name: 'Truss', x: '1', y: '2', z: '0' }; }",
        place=place,
    )
    assert out == [focus, int(focus.split()[1])]


@pytest.mark.parametrize(
    ("where", "kept"), [("pin", True), ("menu", True), ("elsewhere", False)], ids=["a pin", "its menu", "elsewhere"]
)
def test_a_pin_shows_as_selected_only_while_the_focus_is_on_the_pins(where: str, kept: bool) -> None:
    out = run_wizard_js(
        "(function() { calibInit(); S.selPin = 'USL'; renders = 0;"
        " fire('document', 'focusin', { target: { isConnected: true, closest: function(sel) {"
        " return (where === 'pin' && sel.indexOf('.pin-handle') >= 0)"
        " || (where === 'menu' && sel.indexOf('#pin-menu') >= 0) ? {} : null; } } });"
        " return [S.selPin, renders]; })()",
        functions=_MENU_FUNCTIONS,
        prelude=_MENU_PAGE,
        where=where,
    )
    # The last corner a Tab leaves is drawn as any other: its selection would look like the focus.
    assert out == (["USL", 0] if kept else [None, 1])


@pytest.mark.parametrize(
    ("mode", "on_button", "focus"),
    [("simple", True, "cal-overlay"), ("simple", False, None), ("advanced", True, None)],
    ids=["simple, from the button", "simple, focus elsewhere", "advanced keeps its button"],
)
def test_reset_corners_gives_the_focus_of_its_hidden_button_to_the_picture(mode: str, on_button: bool, focus) -> None:
    out = run_wizard_js(
        "(function() { S.mode = mode; document.activeElement = on_button ? $('cal-reset') : null;"
        " window.calibResetPins(); return focused; })()",
        prelude="var window = {};"
        + _MENU_PAGE
        + "function clearCorners() {}\n"
        + wizard_window_function("calibResetPins"),
        mode=mode,
        on_button=on_button,
    )
    assert out == focus


# The rows under the picture: each row's first control and its Delete button record the focus they take.
_ROWS_PAGE = """
els['adv-rows'] = Object.assign(fakeEl('adv-rows'), { querySelector: function(sel) {
  var m = /^\\[data-(del|row)="(\\d+)"\\]$/.exec(sel);
  if (!m || !S.points.some(function(p) { return p.id === +m[2]; })) return null;
  if (m[1] === 'del') return { focus: function() { focused = 'delete ' + m[2]; } };
  return { querySelector: function(s) {
    return s === 'button, input, select' ? { focus: function() { focused = 'row ' + m[2]; } } : null; } };
} });
function rowClick(attr, id) {
  fire('adv-rows', 'click', { target: { closest: function(sel) {
    var data = {}; data[attr] = String(id);
    return sel === '[data-' + attr + ']' ? { dataset: data } : null;
  } } });
}
"""


@pytest.mark.parametrize(
    ("ids", "act", "focus"),
    [
        ([2, 3, 4], ("unpin", 3), "delete 3"),
        ([2, 3, 4], ("del", 3), "row 4"),
        ([2, 3, 4], ("del", 4), "row 3"),
        ([2], ("del", 2), "adv-add-known"),
    ],
    ids=["remove pin", "delete, a row after", "delete the last row", "delete the only row"],
)
def test_a_row_button_hands_its_focus_on(ids: list, act: list, focus: str) -> None:
    out = run_wizard_js(
        "(function() { S.points = ids.map(function(id) { return { id: id, name: 'P' + id, pin: [id, id] }; });"
        " calibInit(); rowClick(act[0], act[1]);"
        " return [focused, S.points.map(function(p) { return [p.id, p.pin]; })]; })()",
        functions=_MENU_FUNCTIONS,
        prelude=_MENU_PAGE + _ROWS_PAGE,
        ids=ids,
        act=list(act),
    )
    kind, gone = act
    left = [[i, None if kind == "unpin" and i == gone else [i, i]] for i in ids if kind == "unpin" or i != gone]
    assert out == [focus, left]


@pytest.mark.parametrize(
    ("where", "selected", "result"),
    [
        ("elsewhere", "USL", [None, 1, 1]),
        ("elsewhere", None, [None, 0, 1]),
        ("a pin", "USL", ["USL", 0, 0]),
        ("its menu", 2, [2, 0, 0]),
    ],
    ids=["a selected pin", "nothing selected", "a pin", "the pin menu"],
)
def test_a_press_off_the_pins_drops_the_selection(where: str, selected, result: list) -> None:
    out = run_wizard_js(
        "(function() { calibInit(); S.selPin = selected; renders = 0;"
        " fire('document', 'pointerdown', { pointerType: 'mouse', target: { closest: function(sel) {"
        " return (where === 'a pin' && sel.indexOf('.pin-handle') >= 0)"
        " || (where === 'its menu' && sel.indexOf('#pin-menu') >= 0) ? {} : null; } } });"
        " return [S.selPin, renders, dropped]; })()",
        functions=_MENU_FUNCTIONS,
        prelude=_MENU_PAGE + "var dropped = 0; function dropCorner() { dropped++; }",
        where=where,
        selected=selected,
    )
    # A press on nothing focusable moves the focus to no element, so no focusin clears the selection.
    assert out == result


@pytest.mark.parametrize(
    ("moved", "focus"), [(True, ["pin 3", "pin 3"]), (False, [None, "menu 3"])], ids=["dragged", "clicked"]
)
def test_a_dragged_pin_takes_the_focus(moved: bool, focus: list) -> None:
    out = run_wizard_js(
        "(function() { calibInit(); var d = drags['cal-overlay'].start({ dataset: { mark: '3' } }, [6, 6], 3);"
        " if (moved) d.move([60, 60]); var click = d.end(true), released = focused; if (click) click();"
        " return [released, focused, S.selPin]; })()",
        functions=_MENU_FUNCTIONS,
        prelude=_MENU_PAGE + "var drags = {}; function bindDrag(id, c, hit, o) { drags[id] = o; }"
        " function openPointMenu(id) { focused = 'menu ' + id; } var imageWidth = 1920, imageHeight = 1080;"
        " els['cal-container'] = Object.assign(fakeEl('cal-container'),"
        " { getBoundingClientRect: function() { return { width: 960 }; } });",
        moved=moved,
    )
    # Selected, it has the focus: the arrow keys move it. A click opens the menu on its click, not on the release.
    assert out == [*focus, 3]


def test_the_height_field_is_looked_at_again_once_left() -> None:
    out = run_wizard_js(
        "(function() { calibInit(); results = 0; fire('document', 'focusout', { target: $('cam_height') });"
        " var left = results; fire('document', 'focusout', { target: $('cam_pos_x') }); return [left, results]; })()",
        functions=_MENU_FUNCTIONS,
        prelude=_MENU_PAGE,
    )
    assert out == [1, 1]


@pytest.mark.parametrize(
    ("asked", "typed", "focus", "shown"),
    [
        (False, "", True, [True, False]),
        (False, "9.", False, [True, False]),
        (True, "", False, [False, False]),
        (True, "", True, [False, False]),
    ],
    ids=["level, focused", "level, half typed", "steep", "steep, focused"],
)
def test_the_steep_camera_notice_shows_only_while_the_height_is_asked(
    asked: bool, typed: str, focus: bool, shown: list
) -> None:
    out = run_wizard_js(
        "(function() { var f = $('cam_height'); f.value = typed; if (focus) document.activeElement = f;"
        " renderCalibResult(); return [$('cal-height-ask').hidden, $('cal-height').hidden]; })()",
        functions=("esc", "setLive", "showLive", "renderHeightGroup", "renderCalibResult", "lenText", "fmt"),
        variables=("heightAsked",),
        prelude=length_page("metric", {})
        + _DOM
        + _REVIEW_STUBS
        + "function lensMeasured() { return true; } var S = { mode: 'simple', pair: 0, height: null };"
        + f" S.solve = {{ cam: {{ fov: 60 }}, steep: {json.dumps(asked)}, worst: 0.4, pinned: 4,"
        " reasons: [], marks: [] };",
        typed=typed,
        focus=focus,
    )
    assert out == shown


@pytest.mark.parametrize(
    ("step", "focus"),
    [
        ("inStep('lens')", "calib heading"),
        ("document.body", "calib heading"),
        ("null", "calib heading"),
        ("{ step: null, closest: function() { return null; } }", None),
        ("inStep('calib')", None),
    ],
    ids=["from the step left", "from the body", "from no element", "from the step list", "from the step entered"],
)
def test_a_step_change_gives_a_lost_focus_to_the_new_steps_heading(step: str, focus) -> None:
    out = run_wizard_js(
        f"(function() {{ document.activeElement = {step}; window.wizardGo(2); return focused; }})()",
        prelude=_STEPS_PAGE + wizard_window_function("wizardGo"),
    )
    assert out == focus


def test_opening_the_wizard_leaves_the_focus_where_it_is() -> None:
    out = run_wizard_js(
        "(function() { document.activeElement = document.body; window.wizardGo(1, true); return focused; })()",
        prelude=_STEPS_PAGE + wizard_window_function("wizardGo"),
    )
    assert out is None


def test_each_step_heading_can_take_the_focus() -> None:
    src = WIZARD_TEMPLATE.read_text(encoding="utf-8")
    for key in ("video", "lens", "calib", "review"):
        step = src[src.index(f'id="wizard-step-{key}"') :]
        assert re.search(r"<h2[^>]*>", step).group(0) == '<h2 tabindex="-1">', key


def test_the_focusable_pictures_can_take_the_focus() -> None:
    src = WIZARD_TEMPLATE.read_text(encoding="utf-8")
    for el_id in ("lens-overlay", "cal-overlay"):
        assert 'tabindex="-1"' in re.search(rf'<svg id="{el_id}"[^>]*>', src).group(0), el_id


def test_the_focus_ring_is_drawn_inside_the_pictures_rounded_frame() -> None:
    src = WIZARD_TEMPLATE.read_text(encoding="utf-8")
    rule = re.search(r"\.wizard-overlay:focus-visible \{([^}]*)\}", src).group(1)
    # The container clips with its radius: the ring sits inside the edge and follows the same corners.
    assert "outline: 2px solid var(--accent)" in rule and "outline-offset: -2px" in rule
    assert "border-radius: inherit" in rule
    container = re.search(r"\n  \.wizard-preview-container \{([^}]*)\}", src).group(1)
    assert "border-radius: 0.75rem" in container and "overflow: hidden" in container


def test_the_pin_menu_scrolls_inside_the_picture_it_cannot_outgrow() -> None:
    src = WIZARD_TEMPLATE.read_text(encoding="utf-8")
    rule = re.search(r"\n  \.pin-menu \{([^}]*)\}", src).group(1)
    assert "max-height: calc(100% - 16px)" in rule and "overflow-y: auto" in rule


@pytest.mark.parametrize("size", [(260, 120), (288, 184)], ids=["the list", "a form at its tallest"])
def test_the_pin_menu_is_placed_inside_the_smallest_picture_that_keeps_it(size: tuple) -> None:
    # A 16:9 picture keeps the popup from 640 x 360 CSS px; the menu is at most 16 px less high.
    out = run_wizard_js(
        "(function() { var out = []; for (var x = 0; x <= 1920; x += 160) for (var y = 0; y <= 1080; y += 90) {"
        " S.pinMenu = { p: [x, y] }; positionPinMenu();"
        " out.push([parseFloat(m.style.left), parseFloat(m.style.top)]); }"
        " return out; })()",
        functions=("pinMenuAsPanel", "positionPinMenu"),
        prelude="var imageWidth = 1920, S = {}, FS = null; function menuLoupe() {} function renderCalibOverlay() {}"
        " var box = { getBoundingClientRect: function() { return { width: 640, height: 360 }; } };"
        f" var m = {{ offsetWidth: {size[0]}, offsetHeight: {size[1]}, style: {{}}, parentNode: box,"
        " classList: { toggle: function() {} } };"
        " function $(id) { return id === 'pin-menu' ? m : box; }",
    )
    w, h = size
    for left, top in out:
        assert left >= 8 and left + w <= 640 - 8, (left, top)
        assert top >= 8 and top + h <= 360 - 8, (left, top)


# ---------------------------------------------------------------------------
# Live regions: rewritten only when their words change
# ---------------------------------------------------------------------------

# The fake DOM counts every write of an element's words.
_COUNTING_DOM = """
var els = {}, writes = {};
function $(id) {
  if (!els[id]) {
    var el = { id: id, hidden: false, disabled: false, className: '', value: '', style: {}, open: false,
      setAttribute: function() {}, getBoundingClientRect: function() { return { width: 0 }; },
      classList: { toggle: function() {} }, querySelector: function() { return { style: {} }; },
      querySelectorAll: function() { return []; } };
    ['innerHTML', 'textContent'].forEach(function(k) {
      var v = '';
      Object.defineProperty(el, k, { get: function() { return v; },
        set: function(x) { v = x; writes[id] = (writes[id] || 0) + 1; } });
    });
    els[id] = el;
  }
  return els[id];
}
var document = { getElementById: $, querySelector: function() { return null; },
  querySelectorAll: function() { return []; } };
"""


def _live_writes(draw: str, ids: list[str], **kw) -> dict:
    """``draw`` three times: the writes each live region in ``ids`` took."""
    return run_wizard_js(
        f"(function() {{ for (var i = 0; i < 3; i++) {{ {draw} }} var out = {{}};"
        f" {json.dumps(ids)}.forEach(function(id) {{ out[id] = writes[id] || 0; }}); return out; }})()",
        **kw,
    )


def test_set_live_writes_only_new_words_unless_forced() -> None:
    out = run_wizard_js(
        "(function() { var el = $('r'); setLive(el, 'a'); setLive(el, 'a'); setLive(el, 'b'); setLive(el, 'b', true);"
        " return [writes.r, el.innerHTML]; })()",
        functions=("setLive",),
        prelude=_COUNTING_DOM,
    )
    assert out == [3, "b"]


@pytest.mark.parametrize(
    ("mode", "solve", "height", "regions"),
    [
        ("advanced", {"need": ["2 more pinned points"]}, None, ["adv-need"]),
        (
            "simple",
            {"cam": {"fov": 60}, "steep": True, "worst": 0.4, "pinned": 4, "reasons": [{"kind": "fov"}]},
            9.8,
            ["cal-status", "cal-caution", "cal-height-note"],
        ),
        ("simple", {"error": "No camera fits these corners."}, None, ["cal-status"]),
        ("simple", {"waiting": "Drag each corner onto its tape mark."}, None, ["cal-status"]),
    ],
    ids=["advanced needs points", "solved", "error", "waiting"],
)
def test_the_calibration_live_regions_are_written_once_for_the_same_words(mode, solve, height, regions) -> None:
    out = _live_writes(
        "renderCalibResult();",
        regions,
        functions=("esc", "setLive", "showLive", "renderHeightGroup", "renderCalibResult", "lenText", "fmt"),
        variables=("heightAsked",),
        prelude=length_page("metric", {})
        + _COUNTING_DOM
        + _REVIEW_STUBS
        + "function lensMeasured() { return true; }"
        + f" var S = {{ mode: {json.dumps(mode)}, pair: 0, height: {json.dumps(height)} }};"
        + f" S.solve = Object.assign({{ marks: [] }}, {json.dumps(solve)});",
    )
    assert out == dict.fromkeys(regions, 1)


@pytest.mark.parametrize(
    ("solve", "regions"),
    [
        (
            {"marks": [{"id": 5, "name": "P5", "problem": "Pin it.", "need": "a pin"}]},
            ["review-status", "review-incomplete"],
        ),
        (
            {"cam": {"fov": 60}, "worst": 0.4, "reasons": [{"kind": "fov"}], "positions": {}, "marks": []},
            ["review-caution"],
        ),
    ],
    ids=["not calculated", "something might be wrong"],
)
def test_the_review_live_regions_are_written_once_for_the_same_words(solve: dict, regions: list) -> None:
    out = _live_writes(
        "renderReview();",
        regions,
        functions=_REVIEW_FUNCTIONS,
        variables=("APPLY_MAX_PX",),
        prelude=_COUNTING_DOM + _REVIEW_STUBS,
        S={"mode": "advanced", "cam": None, "solve": solve, "height": None},
        solvePending=False,
    )
    assert out == dict.fromkeys(regions, 1)


_LENS_RESULT_PAGE = """
function fmt(v, d) { return Number(v).toFixed(d); } function wizLens() { return [-0.12, 0, 0, 0]; }
function renderLensMeter() {} function lensIsTheFits() { return true; } function lensSame() { return true; }
function lensCentreSuffix() { return ''; }
"""


@pytest.mark.parametrize(
    ("state", "regions"),
    [
        (
            "lensFit = { rating: 'okay', k1: -0.12, k2: 0, k2_fitted: false,"
            " hint: 'If possible, add a line near the top edge.' };",
            ["lens-result-text", "lens-result-hint"],
        ),
        (
            "S.lensLoaded = { name: 'Wide', k1: -0.12, k2: 0, cx: 0, cy: 0, src: 'Saved on this station' };",
            ["lens-result-text", "lens-result-hint"],
        ),
    ],
    ids=["measured", "loaded"],
)
def test_the_lens_result_is_written_once_for_the_same_words(state: str, regions: list) -> None:
    out = _live_writes(
        "renderLensResult();",
        regions,
        functions=(
            "esc",
            "setLive",
            "lensFitApplies",
            "lensFitMisfit",
            "lensMiddleOnly",
            "lensShownRating",
            "lensHandTuned",
            "renderLensResult",
        ),
        variables=("LENS_RATING_LEVEL", "LENS_RATING_LABEL"),
        prelude=_COUNTING_DOM
        + _LENS_RESULT_PAGE
        + "var S = { lensLoaded: null }, lensFit = null, lensLines = [];"
        + state,
    )
    assert out == dict.fromkeys(regions, 1)


def test_the_lens_status_and_notice_are_written_once_for_the_same_words() -> None:
    out = _live_writes(
        "lensShowStatus('The lens could not be measured.', false); lensShowNotice('The lines were removed.');",
        ["lens-status", "lens-notice"],
        functions=("esc", "setLive", "lensShowStatus", "lensShowNotice"),
        prelude=_COUNTING_DOM,
    )
    assert out == {"lens-status": 1, "lens-notice": 1}


def test_a_hidden_live_region_forgets_its_words() -> None:
    out = run_wizard_js(
        "(function() { var el = $('r'), seen = []; [true, true, false, true].forEach(function(on) {"
        " showLive(el, 'a', on); seen.push(el.hidden); }); return [writes.r, seen, el.innerHTML]; })()",
        functions=("setLive", "showLive"),
        prelude=_COUNTING_DOM,
    )
    # Shown again with the same words, it is written again, so a screen reader reads it.
    assert out == [2, [False, False, True, False], "a"]


_SOLVED = {"cam": {"fov": 60}, "steep": False, "worst": 0.4, "pinned": 4, "reasons": [{"kind": "fov"}], "marks": []}
_SOLVED_CLEAN = {**_SOLVED, "reasons": []}


@pytest.mark.parametrize(
    ("mode", "shown", "hidden", "region"),
    [
        ("advanced", {"need": ["2 more pinned points"], "marks": []}, _SOLVED_CLEAN, "adv-need"),
        ("simple", _SOLVED, _SOLVED_CLEAN, "cal-caution"),
        ("simple", _SOLVED_CLEAN, None, "cal-status"),
    ],
    ids=["what Advanced needs", "something might be wrong", "the solve's status"],
)
def test_a_calibration_region_shown_again_is_written_again(mode: str, shown: dict, hidden, region: str) -> None:
    out = run_wizard_js(
        "(function() { var seen = []; [shown, hidden, shown].forEach(function(s) { S.solve = s; renderCalibResult();"
        " seen.push($(region).hidden); }); return [writes[region] || 0, seen]; })()",
        functions=("esc", "setLive", "showLive", "renderHeightGroup", "renderCalibResult", "lenText", "fmt"),
        variables=("heightAsked",),
        prelude=length_page("metric", {})
        + _COUNTING_DOM
        + _REVIEW_STUBS
        + "function lensMeasured() { return true; }"
        + f" var S = {{ mode: {json.dumps(mode)}, pair: 0, height: null }};",
        shown=shown,
        hidden=hidden,
        region=region,
    )
    assert out == [2, [False, True, False]]


_REVIEW_SOLVED = {"cam": {"fov": 60}, "worst": 0.4, "reasons": [], "positions": {}, "marks": []}


@pytest.mark.parametrize(
    ("shown", "hidden", "region"),
    [
        ({**_REVIEW_SOLVED, "reasons": [{"kind": "fov"}]}, _REVIEW_SOLVED, "review-caution"),
        ({"error": "No camera fits.", "marks": []}, _REVIEW_SOLVED, "review-status"),
        (
            {**_REVIEW_SOLVED, "marks": [{"id": 5, "name": "P5", "problem": "Pin it.", "need": "a pin"}]},
            _REVIEW_SOLVED,
            "review-incomplete",
        ),
    ],
    ids=["something might be wrong", "not calculated", "incomplete points"],
)
def test_a_review_region_shown_again_is_written_again(shown: dict, hidden: dict, region: str) -> None:
    out = run_wizard_js(
        "(function() { var seen = []; [shown, hidden, shown].forEach(function(s) { S.solve = s; renderReview();"
        " seen.push($(region).hidden); }); return [writes[region] || 0, seen]; })()",
        functions=_REVIEW_FUNCTIONS,
        variables=("APPLY_MAX_PX",),
        prelude=_COUNTING_DOM + _REVIEW_STUBS,
        S={"mode": "advanced", "cam": None, "solve": None, "height": None},
        solvePending=False,
        shown=shown,
        hidden=hidden,
        region=region,
    )
    assert out == [2, [False, True, False]]


def test_the_lens_result_shown_again_is_written_again() -> None:
    out = run_wizard_js(
        "(function() { var fit = { rating: 'okay', k1: -0.12, k2: 0, k2_fitted: false,"
        " hint: 'If possible, add a line near the top edge.' };"
        " [fit, null, fit].forEach(function(f) { lensFit = f; renderLensResult(); });"
        " return [writes['lens-result-text'], writes['lens-result-hint']]; })()",
        functions=(
            "esc",
            "setLive",
            "lensFitApplies",
            "lensFitMisfit",
            "lensMiddleOnly",
            "lensShownRating",
            "lensHandTuned",
            "renderLensResult",
        ),
        variables=("LENS_RATING_LEVEL", "LENS_RATING_LABEL"),
        prelude=_COUNTING_DOM + _LENS_RESULT_PAGE + "var S = { lensLoaded: null }, lensFit = null, lensLines = [];",
    )
    assert out == [2, 2]


@pytest.mark.parametrize(("visible", "kept"), [(True, "words"), (False, None)], ids=["picture", "no picture"])
def test_the_lens_hint_hidden_with_the_picture_forgets_its_words(visible: bool, kept) -> None:
    out = run_wizard_js(
        "(function() { $('lens-hint').liveHtml = 'words'; setPreviewVisibility(visible);"
        " return [$('lens-hint').liveHtml, $('lens-hint').hidden]; })()",
        functions=("setPreviewVisibility",),
        prelude=_COUNTING_DOM,
        visible=visible,
    )
    assert out == [kept, not visible]


def test_leaving_full_screen_forgets_the_full_screen_hint() -> None:
    out = run_wizard_js(
        "(function() { closeFull(); return hint.liveHtml; })()",
        functions=("closeFull",),
        prelude=_CLOSE_FULL_PAGE + "var refused = false, hint = { liveHtml: 'words' };"
        " function $(id) { return id === 'fs-hint' ? hint : shell; }",
    )
    assert out is None


# Four steps, each with two live regions holding words, a heading, and a focus that is recorded.
_STEPS_PAGE = """
var focused = null, steps = {}, WIZ_STEPS = ['video', 'lens', 'calib', 'review'], currentStep = 0;
WIZ_STEPS.forEach(function(key) {
  steps['wizard-step-' + key] = { key: key, regions: [{ liveHtml: 'words' }, { liveHtml: 'words' }],
    classList: { toggle: function() {} },
    querySelectorAll: function(sel) {
      return ['[role="status"]', '[role="alert"]', '[aria-live]'].every(function(s) { return sel.indexOf(s) >= 0; })
        ? this.regions : [];
    },
    querySelector: function(sel) {
      return sel === 'h2' ? { focus: function() { focused = key + ' heading'; } } : null; },
    contains: function(x) { return !!x && x.step === key; } };
});
function $(id) { return steps[id] || null; }
var window = { scrollTo: function() {} };
var document = { body: { step: null }, activeElement: null,
  querySelectorAll: function(sel) { return sel === '.wizard-content' ? Object.values(steps) : []; } };
function inStep(key) { return { step: key, closest: function(sel) { return sel === '.wizard-content' ? {} : null; } }; }
function closePinMenu() {} function dropCorner() {} function dropLensPoint() {} function saveToSession() {}
function loadSnapshot() {} function placeStartQuad() {} function renderCalibPanels() {} function runSolve() {}
function renderReview() {}
var S = { mode: 'advanced' };
"""


def test_a_step_entered_reads_out_its_live_regions_again() -> None:
    out = run_wizard_js(
        "(function() { window.wizardGo(2); var out = {}; WIZ_STEPS.forEach(function(k) {"
        " out[k] = steps['wizard-step-' + k].regions.map(function(r) { return r.liveHtml; }); }); return out; })()",
        prelude=_STEPS_PAGE + wizard_window_function("wizardGo"),
    )
    kept = ["words", "words"]
    assert out == {"video": kept, "lens": kept, "calib": [None, None], "review": kept}


# ---------------------------------------------------------------------------
# Names for assistive technology
# ---------------------------------------------------------------------------


class _Rows(HTMLParser):
    """Each ``role="group"`` row: the words of its label, and its buttons as (name, visible words)."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[dict] = []
        self.ids: dict[str, str] = {}
        self._open: list[tuple[str, str | None]] = []
        self._button: dict | None = None

    def handle_starttag(self, tag: str, attrs: list) -> None:
        a = dict(attrs)
        if a.get("role") == "group":
            self.rows.append({"label": a.get("aria-labelledby"), "buttons": []})
        if tag == "button":
            self._button = {"name": a.get("aria-label"), "words": ""}
            self.rows[-1]["buttons"].append(self._button)
        self._open.append((tag, a.get("id")))

    def handle_endtag(self, tag: str) -> None:
        if tag == "button":
            self._button = None
        while self._open and self._open.pop()[0] != tag:
            pass

    def handle_data(self, data: str) -> None:
        if self._button is not None:
            self._button["words"] += data
        for _, el_id in self._open:
            if el_id:
                self.ids[el_id] = self.ids.get(el_id, "") + data


def test_each_point_row_is_a_group_named_by_its_point_and_names_its_buttons() -> None:
    html = run_wizard_js(
        "(function() { renderAdvRows(); return $('adv-rows').innerHTML; })()",
        functions=("esc", "renderAdvRows"),
        variables=("AXIS_NOTE",),
        prelude=_DOM
        + "function currentMarks() { return []; } function triPlaced() { return {}; } function chipFor() { return ''; }"
        " function triXYZ() { return ''; } function lenInput() { return ''; } function lenSuffix() { return 'm'; }"
        " function pointOptions() { return ''; } function renderReq() {} var SAVED = { max_marks: 32 };"
        " var S = { solve: null, points: [{ id: 1, name: 'DSL', kind: 'known', pin: [1, 1] },"
        " { id: 2, name: '<b>\"Truss\"', kind: 'triangulated', pin: null },"
        " { id: 3, name: '', kind: 'known', pin: [3, 3] }] };",
    )
    parsed = _Rows()
    parsed.feed(html)
    named = [(parsed.ids[r["label"]], [(b["name"], b["words"]) for b in r["buttons"]]) for r in parsed.rows]
    # The buttons keep their visible words; their names start with them, then say which point.
    assert named == [
        ("DSL", [("Remove pin of DSL", "Remove pin"), ("Delete DSL", "Delete")]),
        ('<b>"Truss"', [('Delete <b>"Truss"', "Delete")]),
        ("P3", [("Remove pin of P3", "Remove pin"), ("Delete P3", "Delete")]),
    ]


@pytest.mark.parametrize(
    ("on", "labels"),
    [
        (
            [True, True, True],
            [
                "Line 2, start",
                "Line 2, middle point 1",
                "Line 2, middle point 2",
                "Line 2, middle point 3",
                "Line 2, end",
            ],
        ),
        (
            [True, False, True],
            [
                "Line 2, start",
                "Line 2, middle point 1",
                "Line 2, middle point 2 (off)",
                "Line 2, middle point 3",
                "Line 2, end",
            ],
        ),
    ],
    ids=["all on", "one switched off"],
)
def test_a_switched_off_middle_point_says_so_in_its_name(on: list, labels: list) -> None:
    out = run_wizard_js(
        "[0, 1, 2, 3, 4].map(function(j) { return lensHandle(line, 1, j, 1).attrs['aria-label']; })",
        functions=("lensPointIsOn", "lensHandle"),
        prelude="var lensSelected = null; function lensPointPos() { return [0, 0]; }"
        " function pointHandle() {"
        " return { attrs: {}, dataset: {}, setAttribute: function(k, v) { this.attrs[k] = v; } }; }",
        line={"mids": [{"on": v} for v in on], "snapped": [True] * 5},
    )
    assert out == labels


# ---------------------------------------------------------------------------
# A press on the picture acts on its click
# ---------------------------------------------------------------------------

# One picture whose listeners are kept; a press and a click as a mouse or a finger sends them.
_DRAG_PAGE = """
var on = {}, taps = [], starts = 0;
var svg = { addEventListener: function(t, f) { on[t] = f; }, setPointerCapture: function() {} };
function $() { return svg; }
function svgPoint(s, e) { return [e.clientX * 2, e.clientY * 2]; }
function showLoupe() {} function hideLoupe() {}
function target(kind) {
  return { closest: function(sel) {
      return (kind === 'pin' && sel === '.pin-handle') || (kind === 'hit' && sel === '#cal-hit') ? {} : null; },
    matches: function() { return false; } };
}
function press(kind, type, x, y) {
  on.pointerdown({ target: target(kind), pointerType: type, pointerId: 1, clientX: x, clientY: y,
    preventDefault: function() {} });
}
function move(type, x, y) { on.pointermove({ pointerType: type, pointerId: 1, clientX: x, clientY: y }); }
function click(x, y) { on.click({ target: target('hit'), clientX: x, clientY: y }); }
"""


@pytest.mark.parametrize(
    ("pressed", "path", "taps"),
    [
        (("hit", "touch"), [[10, 10]], [[20, 20]]),
        (("hit", "mouse"), [[10, 10]], [[20, 20]]),
        (("hit", "pen"), [[10, 10]], [[20, 20]]),
        (("hit", "touch"), [[17, 10]], [[20, 20]]),
        (("hit", "touch"), [[18, 10]], []),
        (("hit", "touch"), [[30, 10], [10, 10]], []),
        (("hit", "mouse"), [[12, 10]], [[20, 20]]),
        (("hit", "mouse"), [[13, 10]], []),
        (("hit", "pen"), [[17, 10]], [[20, 20]]),
        (("hit", "pen"), [[20, 10]], []),
        (("pin", "mouse"), [[10, 10]], []),
        (("elsewhere", "mouse"), [[10, 10]], []),
        (None, [[10, 10]], []),
    ],
    ids=[
        "a tap",
        "a mouse click",
        "a pen tap",
        "a finger within the slop",
        "a finger past the slop",
        "a finger that came back",
        "a mouse within the slop",
        "a mouse that moved",
        "a pen within the slop",
        "a pen drag",
        "a pin",
        "off the hit area",
        "no press",
    ],
)
def test_a_press_on_the_picture_acts_once_on_its_click(pressed, path: list, taps: list) -> None:
    out = run_wizard_js(
        "(function() { bindDrag('cal-overlay', 'cal-container', '#cal-hit',"
        " { start: function() { starts++; return null; }, tap: function(p) { taps.push(p); } });"
        " var type = pressed ? pressed[1] : 'mouse'; if (pressed) press(pressed[0], type, 10, 10);"
        " path.forEach(function(q) { move(type, q[0], q[1]); }); var early = taps.length;"
        " var at = path[path.length - 1]; click(at[0], at[1]); click(at[0], at[1]); return [early, taps]; })()",
        functions=("bindDrag", "tapSlop"),
        prelude=_DRAG_PAGE,
        pressed=list(pressed) if pressed else None,
        path=path,
    )
    # Nothing opens on the press: under a finger, a menu opened then would take the same tap's click.
    # A mouse may move 3 CSS px, a finger or a pen 8, and stay a tap.
    assert out == [0, taps]


@pytest.mark.parametrize(
    ("ends", "opened"),
    [(["pointerup", "click"], [1, 1]), (["pointercancel", "click"], [0, 0]), (["pointerup"], [0, 1])],
    ids=["a click", "a cancelled press", "the release alone"],
)
def test_a_press_on_a_pin_acts_on_its_click_not_its_release(ends: list, opened: list) -> None:
    out = run_wizard_js(
        "(function() { var opens = 0;"
        " bindDrag('cal-overlay', 'cal-container', '#cal-hit', { tap: function() { taps.push(1); }, start: function() {"
        " return { pos: function() { return [0, 0]; }, move: function() {},"
        " end: function() { return function() { opens++; }; } }; } });"
        " press('pin', 'touch', 10, 10);"
        " ends.forEach(function(t) { if (t === 'click') click(10, 10);"
        " else on[t]({ type: t, pointerId: 1, clientX: 10, clientY: 10 }); });"
        " var after = opens; click(10, 10); return [after, opens, taps.length]; })()",
        functions=("bindDrag", "tapSlop"),
        prelude=_DRAG_PAGE,
        ends=ends,
    )
    # The next click acts once at most, and never as a tap on the picture.
    assert out == [*opened, 0]


# ---------------------------------------------------------------------------
# Distances to check: choosing a row keeps the focus on it
# ---------------------------------------------------------------------------

_PAIRS_PAGE = """
var rebuilt = 0, prevented = 0; function renderCalibResult() { rebuilt++; }
var rows = [0, 1, 2].map(function(i) {
  return { dataset: { pair: String(i) }, on: i === 0, attrs: { 'aria-current': String(i === 0) } };
});
rows.forEach(function(r) {
  r.classList = { toggle: function(c, v) { if (c === 'on') r.on = v; } };
  r.setAttribute = function(k, v) { r.attrs[k] = v; };
});
els['cal-dist-table'] = Object.assign(fakeEl('cal-dist-table'), { innerHTML: 'the table',
  querySelectorAll: function(sel) { return sel === '[data-pair]' ? rows : []; } });
function rowEvent(type, i, key) {
  fire('cal-dist-table', type, { key: key, preventDefault: function() { prevented++; },
    target: { closest: function(sel) { return sel === '[data-pair]' ? rows[i] : null; } } });
}
"""


@pytest.mark.parametrize(
    ("event", "key", "chosen"),
    [("click", None, 2), ("keydown", "Enter", 1), ("keydown", " ", 2), ("keydown", "Tab", None)],
    ids=["a click", "Enter", "Space", "another key"],
)
def test_choosing_a_distance_moves_only_the_highlight(event: str, key, chosen) -> None:
    out = run_wizard_js(
        "(function() { S.pair = 0; calibInit(); renders = 0; rowEvent(event, chosen == null ? 1 : chosen, key);"
        " return [S.pair, rows.map(function(r) { return r.on; }),"
        " rows.map(function(r) { return r.attrs['aria-current']; }),"
        " renders, rebuilt, $('cal-dist-table').innerHTML, focused, prevented]; })()",
        functions=_MENU_FUNCTIONS,
        prelude=_MENU_PAGE + _PAIRS_PAGE,
        event=event,
        key=key,
        chosen=chosen,
    )
    pair = 0 if chosen is None else chosen
    # The table is not rebuilt, so the row keeps the focus; only the picture draws the new distance. The chosen row
    # is the current one; a key that chose it does not also scroll the page.
    assert out == [
        pair,
        [i == pair for i in range(3)],
        [str(i == pair).lower() for i in range(3)],
        0 if chosen is None else 1,
        0,
        "the table",
        None,
        1 if event == "keydown" and chosen is not None else 0,
    ]


@pytest.mark.parametrize(("interactive", "current"), [(True, ["false", "true", "false"]), (False, [])])
def test_the_chosen_distance_row_is_the_current_one(interactive: bool, current: list) -> None:
    html = run_wizard_js(
        "distTable([0, 1, 2].map(function(i) { return { label: 'P' + i, A: [0, 0, 0], B: [i, 0, 0] }; }), interactive)",
        functions=("esc", "dist3", "distTable"),
        prelude="var S = { pair: 1 }; function lenText(m) { return m + ' m'; }",
        interactive=interactive,
    )
    # Only the Camera Calibration table is chosen from; the Review table has no current row.
    assert re.findall(r'<tr class="[^"]*"[^>]*?aria-current="(\w+)"', html) == current
    assert html.count("<tr") == 4


# ---------------------------------------------------------------------------
# A partly filled camera position
# ---------------------------------------------------------------------------

_CAM_PAGE = """
var focused = null, nexted = 0, scrolled = 0;
function field(id) {
  return { id: id, value: '', attrs: {}, setAttribute: function(k, v) { this.attrs[k] = v; },
    removeAttribute: function(k) { delete this.attrs[k]; }, focus: function(o) { focused = id; } };
}
var els = { 'cal-next': { disabled: false }, 'cam-error': { hidden: true }, 'simple-cam-box': { open: false },
  'cal-camera': { scrollIntoView: function() { scrolled++; } } };
['x', 'y', 'z'].forEach(function(k) { els['cam_pos_' + k] = field('cam_pos_' + k); });
$ = function(id) { return els[id]; };
function wizardNext() { nexted++; }
function placeStartQuad() {} function renderReq() {} function solveSoon() {} function saveToSession() {}
function fill(v) { ['x', 'y', 'z'].forEach(function(k, i) { els['cam_pos_' + k].value = v[i]; }); }
function attrsOf() { return ['x', 'y', 'z'].map(function(k) { return Object.assign({}, els['cam_pos_' + k].attrs); }); }
"""
_CAM_FUNCTIONS = ("len", "camFields", "camUnusable", "readCam", "showCamError", "camChanged")


def _cam_js(expression: str, **inputs) -> list:
    return run_wizard_js(
        expression,
        functions=_CAM_FUNCTIONS,
        variables=("exactLen", "DISTANCE_SIGMA"),
        prelude=length_page("metric", {}) + _CAM_PAGE + wizard_window_function("calibNext"),
        **inputs,
    )


_DESCRIBED = {"aria-describedby": "cam-error", "aria-invalid": "false"}
_INVALID = {"aria-describedby": "cam-error", "aria-invalid": "true"}
_PLAIN = {"aria-invalid": "false"}


@pytest.mark.parametrize(
    ("values", "mode", "result"),
    [
        (["0", "", ""], "advanced", ["cam_pos_y", False, [_DESCRIBED, _INVALID, _INVALID], 0, False]),
        (["", "-20", "8"], "simple", ["cam_pos_x", False, [_INVALID, _DESCRIBED, _DESCRIBED], 0, True]),
        (["0", "-20", "20000"], "advanced", ["cam_pos_z", False, [_DESCRIBED, _DESCRIBED, _INVALID], 0, False]),
        (["0", "abc", "8"], "advanced", ["cam_pos_y", False, [_DESCRIBED, _INVALID, _DESCRIBED], 0, False]),
        (["0", "-20", "8"], "advanced", [None, True, [_PLAIN, _PLAIN, _PLAIN], 1, False]),
        (["", "", ""], "simple", [None, True, [_PLAIN, _PLAIN, _PLAIN], 1, False]),
    ],
    ids=["two empty", "one empty, Simple", "out of reach", "unreadable", "complete", "empty, Simple"],
)
def test_next_with_a_partly_filled_camera_points_at_the_first_missing_value(
    values: list, mode: str, result: list
) -> None:
    out = _cam_js(
        "(function() { S.mode = mode; fill(values); window.calibNext();"
        " return [focused, $('cam-error').hidden, attrsOf(), nexted, $('simple-cam-box').open]; })()",
        values=values,
        mode=mode,
        S={"mode": "advanced"},
    )
    assert out == result


def test_the_camera_error_follows_the_values_typed_after_it() -> None:
    out = _cam_js(
        "(function() { var seen = []; fill(['0', '', '']); window.calibNext();"
        " fill(['0', '1', '']); camChanged(); seen.push([$('cam-error').hidden, attrsOf()]);"
        " fill(['0', '1', '2']); camChanged(); seen.push([$('cam-error').hidden, attrsOf()]);"
        " fill(['0', '', '']); camChanged(); seen.push([$('cam-error').hidden, attrsOf()]); return seen; })()",
        S={"mode": "advanced"},
    )
    # Completed, it goes; a value typed again before the next Next shows no error yet.
    assert out == [
        [False, [_DESCRIBED, _DESCRIBED, _INVALID]],
        [True, [_PLAIN, _PLAIN, _PLAIN]],
        [True, [_PLAIN, _PLAIN, _PLAIN]],
    ]


def test_the_camera_error_is_an_alert() -> None:
    src = WIZARD_TEMPLATE.read_text(encoding="utf-8")
    assert re.search(r'<div id="cam-error"[^>]*>', src).group(0) == (
        '<div id="cam-error" class="wizard-field-error" role="alert" hidden>'
    )


@pytest.mark.parametrize(
    ("partial", "heading"),
    [(True, "Enter all three values of the camera position."), (False, "Enter the rough camera position first.")],
    ids=["partly filled", "empty"],
)
def test_the_advanced_hint_names_a_partly_filled_camera(partial: bool, heading: str) -> None:
    out = run_wizard_js(
        "(function() { renderReq(); return shown; })()",
        functions=(*_MARK_FUNCTIONS, *_CAM_HINT_FUNCTIONS, "esc", "renderReq"),
        variables=(*_MARK_VARS, "camNamed"),
        prelude=length_page("metric", {})
        + _DOM
        + f"var S = {{ mode: 'advanced', cam: null, camPartial: {json.dumps(partial)}, points: [], rect: {{}} }};"
        " var SAVED = { max_marks: 12 }, shown = null;"
        " function showHint(area, h, sub) { shown = [area, h, sub]; } function lockSimpleSteps() {}",
    )
    assert out == ["cam", heading, "An estimate within about 2.0 m per axis is good enough."]


@pytest.mark.parametrize(
    ("loc", "rect", "shown"),
    [
        (
            "back",
            _READY,
            [
                "sloc",
                "Enter all three values of the camera position, or leave all three empty.",
                "An estimate within about 1.0 m is good enough.",
            ],
        ),
        (
            "back",
            _EMPTY,
            [
                "sloc",
                "Enter all three values of the camera position, or leave all three empty.",
                "An estimate within about 1.0 m is good enough.",
            ],
        ),
        (
            "",
            _EMPTY,
            [
                "sloc",
                "Choose where your camera is located.",
                "The location decides on which side of the picture each corner of the rectangle starts.",
            ],
        ),
    ],
    ids=["corners to place", "no rectangle yet", "no location yet"],
)
def test_the_simple_hint_names_a_partly_filled_camera(loc: str, rect: dict, shown: list) -> None:
    out = run_wizard_js(
        "(function() { S.camPartial = true; renderSimpleReq(); return shown; })()",
        functions=(*_MARK_FUNCTIONS, *_CAM_HINT_FUNCTIONS, "esc", "lenText", "fmt", "renderSimpleReq"),
        variables=(*_MARK_VARS, "CORNER_TITLES", "camNamed"),
        prelude=length_page("metric", {})
        + _DOM
        + "var shown = null; function showHint(area, h, sub) { shown = [area, h, sub]; }"
        f" var S = {{ simpleLoc: '{loc}', rect: {json.dumps(rect)}, solve: null,"
        " cornerMoved: { DSL: false, DSR: false, USR: false, USL: false } };",
    )
    assert out == shown


# ---------------------------------------------------------------------------
# The Stored echo of a field with an error
# ---------------------------------------------------------------------------

_ECHO_PAGE = """
var echo = { dataset: { echo: 'cam_height' }, hidden: true, textContent: '' };
var height = { value: '', attrs: {}, setAttribute: function(k, v) { this.attrs[k] = v; },
  getAttribute: function(k) { return k in this.attrs ? this.attrs[k] : null; } };
var els = { cam_height: height };
function $(id) {
  return els[id] || (els[id] = { hidden: false, textContent: '', setAttribute: function() {} });
}
var document = { querySelectorAll: function(sel) { return sel === '#wizard-step-calib [data-echo]' ? [echo] : []; } };
var S = { height: null };
"""


@pytest.mark.parametrize(
    ("typed", "echo"),
    [("40000 ft", None), ("40000", None), ("abc", None), ("30 ft", "Stored: 9.144 m"), ("", None)],
    ids=["too far up", "too far up in bare feet", "unreadable", "valid", "empty"],
)
def test_a_height_with_an_error_echoes_no_stored_value(typed: str, echo) -> None:
    out = run_wizard_js(
        "(function() { height.value = typed; readHeight(); return echo.hidden ? null : echo.textContent; })()",
        functions=("len", "lenRough", "refreshEchoes", "readHeight"),
        variables=("exactLen", "DISTANCE_SIGMA"),
        prelude=length_page("imperial", {}) + _ECHO_PAGE,
        typed=typed,
    )
    assert out == echo


def test_a_later_echo_refresh_keeps_the_error_fields_echo_hidden() -> None:
    out = run_wizard_js(
        "(function() { height.value = '40000 ft'; readHeight(); refreshEchoes(); var kept = echo.hidden;"
        " height.value = '30 ft'; readHeight(); return [kept, echo.hidden]; })()",
        functions=("len", "lenRough", "refreshEchoes", "readHeight"),
        variables=("exactLen", "DISTANCE_SIGMA"),
        prelude=length_page("imperial", {}) + _ECHO_PAGE,
    )
    assert out == [True, False]


# ---------------------------------------------------------------------------
# New snapshot loaded: read out each time it shows
# ---------------------------------------------------------------------------


def test_the_new_snapshot_notice_is_written_each_time_it_shows() -> None:
    out = run_wizard_js(
        "(function() { var el = $('cal-refreshed'), seen = []; el.hidden = true;"
        " showRefreshed(); seen.push([el.hidden, writes['cal-refreshed']]);"
        " showRefreshed(); seen.push([el.hidden, writes['cal-refreshed']]);"
        " timer(); seen.push([el.hidden, writes['cal-refreshed']]);"
        " showRefreshed(); seen.push([el.hidden, writes['cal-refreshed']]); return [seen, cleared, el.innerHTML]; })()",
        functions=("setLive", "showLive", "showRefreshed"),
        variables=("refreshedTimer",),
        prelude=_COUNTING_DOM
        + "var timer = null, cleared = 0; function setTimeout(f, ms) { timer = ms === 4000 ? f : null; return 1; }"
        " function clearTimeout() { cleared++; } $('cal-refreshed').innerHTML = 'New snapshot loaded.'; writes = {};",
    )
    # A second snapshot while the notice still shows is a new event: written again, and its 4 s start over.
    assert out == [[[False, 1], [False, 2], [True, 2], [False, 3]], 3, "New snapshot loaded."]


# ---------------------------------------------------------------------------
# Each picture is named once; a pin's name stays clear of the full-screen button
# ---------------------------------------------------------------------------


class _PictureNames(HTMLParser):
    """Within each picture's container, the elements that name it for a screen reader."""

    def __init__(self) -> None:
        super().__init__()
        self.names: dict[str, list] = {}
        self._in: str | None = None

    def handle_starttag(self, tag: str, attrs: list) -> None:
        a = dict(attrs)
        if a.get("id") in ("lens-container", "cal-container", "review-container"):
            self._in = a["id"]
            self.names[self._in] = []
        elif self._in and tag == "img":
            self.names[self._in].append(("img", a.get("alt")))
        elif self._in and tag == "svg" and a.get("aria-hidden") != "true":
            self.names[self._in].append(("svg", a.get("role"), a.get("aria-label")))
        elif self._in and tag == "button" and a.get("class") == "fs-btn":
            self._in = None


def test_each_picture_is_named_once() -> None:
    parser = _PictureNames()
    parser.feed(WIZARD_TEMPLATE.read_text(encoding="utf-8"))
    # The snapshot is decorative; the drawing over it carries the name, so an empty drawing is no second image.
    assert parser.names == {
        "lens-container": [("img", ""), ("svg", None, "Camera snapshot")],
        "cal-container": [("img", ""), ("svg", None, "Camera snapshot")],
        "review-container": [("img", ""), ("svg", "img", "Camera snapshot")],
    }


@pytest.mark.parametrize(
    ("pin", "name", "u", "fs", "left"),
    [
        ([1880, 40], "DSR", 1, False, True),
        ([1780, 30], "Truss left", 1, False, True),
        ([1780, 30], "DSR", 1, False, False),
        ([1880, 80], "DSR", 1, False, False),
        ([40, 40], "DSL", 1, False, False),
        ([1700, 150], "DSR", 5.36, False, True),
        ([1700, 400], "DSR", 5.36, False, False),
        ([1890, 400], "DSR", 1, False, True),
        ([1850, 600], "Truss left", 1, False, True),
        ([1750, 600], "DSR", 5.36, False, True),
        ([1880, 40], "DSR", 1, True, False),
        ([1700, 150], "DSR", 5.36, True, False),
        ([1890, 40], "DSR", 1, True, True),
    ],
    ids=[
        "top right",
        "a long name reaching it",
        "a short name clear of it",
        "below it",
        "top left",
        "a phone",
        "below it on a phone",
        "at the right edge",
        "a long name past the right edge",
        "past the right edge on a phone",
        "full screen, where the button was",
        "full screen on a phone",
        "full screen at the right edge",
    ],
)
def test_a_pins_name_moves_left_of_the_full_screen_button_and_the_edge(
    pin: list, name: str, u: float, fs: bool, left: bool
) -> None:
    out = run_wizard_js(
        "(function() { var t = pinLabel(pin, name, [13, 12], u);"
        ' return [/text-anchor="end"/.test(t), +/ x="([^"]+)"/.exec(t)[1], +/ y="([^"]+)"/.exec(t)[1]]; })()',
        functions=("esc", "pinLabel"),
        prelude="var imageWidth = 1920;",
        pin=pin,
        name=name,
        u=u,
        FS={"key": "cal"} if fs else None,
    )
    # Full screen hides the button: only the picture's edge sends a name left there.
    x = pin[0] - 13 * u if left else pin[0] + 13 * u
    assert out == [left, pytest.approx(x), pytest.approx(pin[1] - 12 * u)]


@pytest.mark.parametrize("u", [1, 5.36], ids=["desktop", "phone"])
@pytest.mark.parametrize("y", [0, 5, 22.9, 23, 40, 540, 1060, 1079], ids=lambda y: f"y={y}")
def test_a_pins_name_stays_inside_the_top_and_bottom_of_the_picture(y: float, u: float) -> None:
    out = run_wizard_js(
        "(function() { return [[13, 12], [9, 9]].map(function(off) {"
        ' return +/ y="([^"]+)"/.exec(pinLabel([960, y], "DSR", off, u))[1]; }); })()',
        functions=("esc", "pinLabel"),
        prelude="var imageWidth = 1920, imageHeight = 1080, FS = null;",
        y=y,
        u=u,
    )
    # A 14 px name rises 11 px above its baseline and drops 4 below it. Above its pin unless that leaves the top.
    for baseline, off in zip(out, (12, 9), strict=True):
        assert baseline - 11 * u >= 0 and baseline + 4 * u <= 1080, (baseline, off)
        above = y - (off + 11) * u >= 0
        assert baseline == pytest.approx(y - off * u if above else y + (off + 11) * u)


def test_a_name_below_a_pin_in_the_top_right_still_moves_left_of_the_full_screen_button() -> None:
    out = run_wizard_js(
        "(function() { var t = pinLabel([1880, 10], 'DSR', [13, 12], 1);"
        ' return [/text-anchor="end"/.test(t), +/ y="([^"]+)"/.exec(t)[1]]; })()',
        functions=("esc", "pinLabel"),
        prelude="var imageWidth = 1920, imageHeight = 1080, FS = null;",
    )
    assert out == [True, 33]


def test_both_pictures_place_their_names_clear_of_the_full_screen_button() -> None:
    out = run_wizard_js(
        "(function() { renderCalibOverlay(); renderReview();"
        " return [$('cal-pins').innerHTML, $('review-pins').innerHTML]; })()",
        functions=(*_REVIEW_FUNCTIONS, "pinBehind", "pinFlagged", "pinLabel", "renderCalibOverlay"),
        variables=("DISTANCE_SIGMA", "APPLY_MAX_PX"),
        prelude=_DOM
        + _REVIEW_STUBS
        + "imageWidth = 1920; imageHeight = 1080; var solvePending = false; function projectShown(c, P) { return P; }"
        " var S = { mode: 'advanced', pair: 0, selPin: null, selCorner: null, pinMenu: null };"
        " S.solve = { cam: { fov: 60 }, res: {}, reasons: [], worst: 0.4, positions: { 1: [1880, 40], 2: [40, 40] },"
        " marks: [{ id: 1, name: 'DSR', pin: [1880, 40] }, { id: 2, name: 'DSL', pin: [40, 40] }] };"
        " function currentMarks() { return S.solve.marks; }",
    )
    for html in out:
        labels = re.findall(r'<text class="pin-label"[^>]*>(\w+)</text>', html)
        assert labels == ["DSR", "DSL"], html
        assert html.count('text-anchor="end"') == 1 and re.search(r'text-anchor="end"[^>]*>DSR<', html), html


# ---------------------------------------------------------------------------
# A tap on a pin opens its menu on its click; the menu ignores the clicks of the gesture that opened it
# ---------------------------------------------------------------------------

_PIN_TAP_PAGE = """
var imageWidth = 1920, imageHeight = 1080, SAVED = { max_marks: 12 };
els['cal-container'] = Object.assign(fakeEl('cal-container'),
  { getBoundingClientRect: function() { return { width: 960 }; } });
els['cal-overlay'] = Object.assign(fakeEl('cal-overlay'), { setPointerCapture: function() {} });
els['pin-menu'] = Object.assign(fakeEl('pin-menu'), { hidden: true,
  classList: { add: function() {}, remove: function() {}, contains: function() { return false; } },
  querySelector: function() { return { focus: function() {} }; } });
function svgPoint(s, e) { return [e.clientX * 2, e.clientY * 2]; }
function showLoupe() {} function positionPinMenu() {} function currentMarks() { return []; }
// What a press, a key or a click lands on: a pin, the bare picture, or a menu item ({ pm } or { place }).
function pin(id) {
  return { isConnected: true, closest: function(sel) {
    if (sel.indexOf('.pin-handle') >= 0) return { dataset: { mark: String(id) } };
    return sel === '#cal-overlay' ? {} : null; }, matches: function() { return false; } };
}
var picture = { isConnected: true,
  closest: function(sel) { return sel === '#cal-hit' || sel === '#cal-overlay' ? {} : null; },
  matches: function(sel) { return sel === '#cal-hit'; } };
function item(data) {
  return { isConnected: true, closest: function(sel) {
    if (sel === '[data-pm]') return data.pm ? { dataset: { pm: data.pm } } : null;
    if (sel === '[data-place]') return data.place ? { dataset: { place: data.place }, disabled: false } : null;
    return sel.indexOf('#pin-menu') >= 0 ? {} : null; } };
}
// Document capture listeners, the element's own, then document bubble listeners, as the browser runs them.
function dispatch(on, type, target, extra) {
  var e = Object.assign({ type: type, target: target, pointerId: 1, pointerType: 'touch', clientX: 10, clientY: 10,
    key: '', repeat: false, preventDefault: function() {} }, extra || {});
  function run(id, capture) {
    listeners.filter(function(l) {
      return l[0] === id && l[1] === type && (capture === undefined || l[3] === capture);
    }).forEach(function(l) { l[2](e); });
  }
  run('document', true); run(on); run('document', false);
}
// A click lands on what is under the pointer: the menu once it covers the spot, else the picture.
function clickAt(target, menuItem) {
  dispatch(S.pinMenu ? 'pin-menu' : 'cal-overlay', 'click', S.pinMenu ? menuItem : target);
}
function state() {
  return [S.pinMenu && S.pinMenu.pin, S.points.map(function(p) { return p.id + ':' + (p.pin ? 'pinned' : '-'); })];
}
"""
_PIN_TAP_FUNCTIONS = (
    *_MENU_FUNCTIONS,
    "bindDrag",
    "tapSlop",
    "getPin",
    "esc",
    "pinMenuHead",
    "openPinMenu",
    "openPointMenu",
    "placeFromMenu",
)
_BOTH_PINNED = ["2:pinned", "3:pinned"]


def _pin_tap(expression: str, **inputs) -> list:
    return run_wizard_js(
        "(function() { calibInit();" + expression + " })()",
        functions=_PIN_TAP_FUNCTIONS,
        prelude=_MENU_PAGE + _PIN_TAP_PAGE,
        **inputs,
    )


@pytest.mark.parametrize(
    ("kind", "dx", "released", "clicked"),
    [
        ("touch", 0, [None, _BOTH_PINNED], [2, _BOTH_PINNED]),
        ("touch", 7, [None, _BOTH_PINNED], [2, _BOTH_PINNED]),
        ("mouse", 0, [None, _BOTH_PINNED], [2, _BOTH_PINNED]),
        ("mouse", 3, [None, _BOTH_PINNED], [None, _BOTH_PINNED]),
        ("pen", 0, [None, _BOTH_PINNED], [2, _BOTH_PINNED]),
        ("pen", 10, [None, _BOTH_PINNED], [None, _BOTH_PINNED]),
    ],
    ids=["a tap", "a finger within the slop", "a mouse click", "a mouse drag", "a pen tap", "a pen drag"],
)
def test_a_pin_opens_its_menu_on_the_click_of_a_press_that_did_not_move(
    kind: str, dx: int, released: list, clicked: list
) -> None:
    out = _pin_tap(
        "dispatch('cal-overlay', 'pointerdown', pin(2), { pointerType: kind });"
        " if (dx) dispatch('cal-overlay', 'pointermove', pin(2), { pointerType: kind, clientX: 10 + dx });"
        " dispatch('cal-overlay', 'pointerup', pin(2), { pointerType: kind, clientX: 10 + dx });"
        " var r = state(); clickAt(pin(2), item({ pm: 'delete' })); return [r, state()];",
        kind=kind,
        dx=dx,
    )
    # The release opens nothing, so the tap's click lands on the pin, not on a menu item over it.
    assert out == [released, clicked]


def test_a_tap_on_a_pin_whose_menu_covers_it_deletes_nothing_and_the_next_tap_acts() -> None:
    out = _pin_tap(
        "var seen = [];"
        " dispatch('cal-overlay', 'pointerdown', pin(2)); dispatch('cal-overlay', 'pointerup', pin(2));"
        " clickAt(pin(2), item({ pm: 'delete' })); seen.push(state());"
        " clickAt(pin(2), item({ pm: 'delete' })); seen.push(state());"
        " dispatch('pin-menu', 'pointerdown', item({ pm: 'delete' })); clickAt(pin(2), item({ pm: 'delete' }));"
        " seen.push(state()); return seen;"
    )
    # A click with no press of its own (assistive technology) is a gesture too, once the opening click is over.
    assert out == [[2, _BOTH_PINNED], [None, ["3:pinned"]], [None, ["3:pinned"]]]


@pytest.mark.parametrize(
    ("opening", "then", "result"),
    [
        ("press", "click", [2, _BOTH_PINNED]),
        ("press", "press and click", [None, ["3:pinned"]]),
        ("press", "click after a click", [None, ["3:pinned"]]),
        ("Enter", "held Enter", [2, _BOTH_PINNED]),
        ("Enter", "Enter released, Enter", [None, ["3:pinned"]]),
        ("Enter", "press and click", [None, ["3:pinned"]]),
        ("Enter", "click once the key is up", [None, ["3:pinned"]]),
    ],
    ids=[
        "the opening press's click",
        "the next press",
        "a click after the opening click",
        "a held Enter's repeat",
        "the next Enter",
        "a press after Enter",
        "a click after Enter was released",
    ],
)
def test_the_pin_menu_ignores_the_clicks_of_the_gesture_that_opened_it(opening: str, then: str, result: list) -> None:
    out = _pin_tap(
        "var del = item({ pm: 'delete' });"
        " if (opening === 'press') { dispatch('document', 'pointerdown', pin(2)); openPointMenu(2); }"
        " else dispatch('cal-overlay', 'keydown', pin(2), { key: 'Enter' });"
        " var opened = S.pinMenu && S.pinMenu.pin;"
        " if (then === 'press and click') dispatch('pin-menu', 'pointerdown', del);"
        " if (then === 'click after a click') dispatch('pin-menu', 'click', del);"
        " if (then === 'held Enter') dispatch('pin-menu', 'keydown', del, { key: 'Enter', repeat: true });"
        " if (then === 'click once the key is up') dispatch('cal-overlay', 'keyup', pin(2), { key: 'Enter' });"
        " if (then === 'Enter released, Enter') { dispatch('cal-overlay', 'keyup', pin(2), { key: 'Enter' });"
        " dispatch('pin-menu', 'keydown', del, { key: 'Enter' }); }"
        " dispatch('pin-menu', 'click', del); return [opened, state()];",
        opening=opening,
        then=then,
    )
    # Whatever opened the menu, a click of that same press or key never chooses an item; the next gesture does.
    assert out == [2, result]


def test_a_tap_on_the_picture_opens_the_menu_and_its_item_takes_the_next_tap() -> None:
    out = _pin_tap(
        "S.points[1].pin = null; var seen = [];"
        " dispatch('cal-overlay', 'pointerdown', picture); seen.push(!!S.pinMenu);"
        " clickAt(picture, item({ place: '3' })); seen.push(S.pinMenu && S.pinMenu.p);"
        " dispatch('pin-menu', 'pointerdown', item({ place: '3' })); clickAt(picture, item({ place: '3' }));"
        " seen.push(S.pinMenu, S.points[1].pin); return seen;"
    )
    # Opened on the click at [20, 20]; the next tap pins USR there.
    assert out == [False, [20, 20], None, [20, 20]]


@pytest.mark.parametrize(
    ("form", "detail", "spot"),
    [(False, 2, [20, 20]), (False, 1, [20, 92]), (True, 2, [20, 20]), (True, 1, [20, 92])],
    ids=["double-click", "a second click", "double-click on a form", "a second click on a form"],
)
def test_a_double_clicks_second_click_on_the_picture_repeats_nothing(form: bool, detail: int, spot: list) -> None:
    out = _pin_tap(
        "dispatch('cal-overlay', 'pointerdown', picture); dispatch('cal-overlay', 'click', picture);"
        " if (form) S.pinMenu.form = 'known';"
        # The menu scrolled the page: the second press lands 36 CSS px lower on the picture.
        " var low = { clientY: 46, detail: detail };"
        " dispatch('cal-overlay', 'pointerdown', picture, low); dispatch('cal-overlay', 'click', picture, low);"
        " return S.pinMenu.p;",
        form=form,
        detail=detail,
    )
    # A double-click leaves the menu (or the new point's form) where its first click put it.
    assert out == spot


_LENS_TAP_PAGE = """
var on = {}, traced = 0, moves = 0;
var overlay = { addEventListener: function(t, f) { on[t] = f; }, setPointerCapture: function() {},
  releasePointerCapture: function() {} };
var document = { getElementById: function(id) { return id === 'lens-overlay' ? overlay : null; },
  addEventListener: function() {} };
function $() { return { addEventListener: function() {} }; }
var lensDrag = null, lensHold = false, lensSelected = null, lensPending = null, lensLines = [{}];
function lensSvgPoint(x, y) { return [x, y]; } function lensTraceClick() { traced++; }
function lensMovePoint() { moves++; } function lensClamp(p) { return p; } function lensUpdateLineGeometry() {}
function lensPointPos() { return [0, 0]; } function showLoupe() {} function hideLoupe() {} function renderLens() {}
function lensFocusSelected() {} function lensChanged() {} function lensPointFocusIn() {}
function lensTabKey() {} function lensFocusElsewhere() {} function renderLensBubbles() {} function renderLensResult() {}
function lensPressEnd() {} function wizShowLensCentre() {} function wizShowLensLimit() {}
var hit = { id: 'lens-hit', closest: function() { return null; } };
var handle = { id: '', closest: function(sel) {
  return sel === '.lens-point' ? { dataset: { line: '0', point: '1' } } : null; } };
"""


@pytest.mark.parametrize(
    ("kind", "path", "result"),
    [
        ("hit", ("mouse", [12]), [1, 0]),
        ("hit", ("mouse", [13]), [0, 0]),
        ("hit", ("touch", [17]), [1, 0]),
        ("hit", ("touch", [18]), [0, 0]),
        ("hit", ("pen", [30, 10]), [0, 0]),
        ("point", ("mouse", [12]), [0, 0]),
        ("point", ("mouse", [13]), [0, 1]),
        ("point", ("touch", [17]), [0, 0]),
        ("point", ("touch", [18]), [0, 1]),
    ],
    ids=[
        "a mouse click starts a line",
        "a mouse drag starts none",
        "a finger tap starts a line",
        "a finger drag starts none",
        "a pen that came back starts none",
        "a mouse click moves no point",
        "a mouse drag moves the point",
        "a finger tap moves no point",
        "a finger drag moves the point",
    ],
)
def test_the_lens_picture_takes_the_same_tap_slop(kind: str, path: tuple, result: list) -> None:
    out = run_wizard_js(
        "(function() { lensInit(); var type = path[0], x = 10, target = kind === 'hit' ? hit : handle;"
        " function ev(t) { return { type: t, target: target, pointerType: type, pointerId: 1, clientX: x, clientY: 10,"
        " preventDefault: function() {} }; }"
        " on.pointerdown(ev('pointerdown'));"
        " path[1].forEach(function(q) { x = q; on.pointermove(ev('pointermove')); });"
        " on.pointerup(ev('pointerup')); var released = traced; on.click(ev('click'));"
        " return [released, traced, moves > 0 ? 1 : 0]; })()",
        functions=("tapSlop", "lensInit"),
        prelude=_LENS_TAP_PAGE,
        kind=kind,
        path=list(path),
    )
    # One slop on every picture: 3 CSS px for a mouse, 8 for a finger or a pen. A line starts on the click.
    assert out == [0, *result]


def test_every_button_class_in_the_wizard_has_a_style() -> None:
    src = WIZARD_TEMPLATE.read_text(encoding="utf-8")
    base = (WIZARD_TEMPLATE.parent / "base.tpl").read_text(encoding="utf-8")
    styles = "".join(re.findall(r"<style[^>]*>(.*?)</style>", base + src, re.S))
    classes = {c for attr in re.findall(r'<button[^>]*\bclass="([\w -]+)"', src) for c in attr.split()}
    # A button styled by a class no stylesheet defines falls back to the browser's own look.
    assert "btn-primary" in classes and "small" in classes
    assert {c for c in classes if not re.search(rf"\.{re.escape(c)}(?![\w-])", styles)} == set()


# ---------------------------------------------------------------------------
# The hint names a partly filled camera only once Next showed the error or the focus left the fields
# ---------------------------------------------------------------------------

# Each step: [camera partly filled, the field with the focus, the error shown].
_CAM_STEPS = [
    [True, "cam_pos_x", False],
    [True, "rect_w", False],
    [True, "cam_pos_y", False],
    [False, "cam_pos_z", False],
    [True, "cam_pos_z", False],
    [True, "cam_pos_z", True],
]
_CAM_STEP_IDS = "typing, focus left, back in a field, completed, typed again, Next"
_CAM_STEP_JS = (
    "steps.map(function(st) { S.camPartial = st[0]; document.activeElement = $(st[1]); $('cam-error').hidden = !st[2];"
    " draw(); return shown; })"
)


def test_the_simple_hint_names_a_partly_filled_camera_only_once_the_typing_is_done() -> None:
    out = run_wizard_js(
        "(function() { function draw() { renderSimpleReq(); } return " + _CAM_STEP_JS + "; })()",
        functions=(*_MARK_FUNCTIONS, *_CAM_HINT_FUNCTIONS, "esc", "lenText", "fmt", "renderSimpleReq"),
        variables=(*_MARK_VARS, "CORNER_TITLES", "camNamed"),
        prelude=length_page("metric", {}) + _DOM + "var shown = null; function showHint(area) { shown = area; }"
        f" var S = {{ simpleLoc: 'back', rect: {json.dumps(_READY)}, solve: null,"
        " cornerMoved: { DSL: false, DSR: false, USR: false, USL: false } };",
        steps=_CAM_STEPS,
    )
    # Above the location only once named, then until the camera is complete: typing never moves the hint.
    assert dict(zip(_CAM_STEP_IDS.split(", "), out, strict=True)) == {
        "typing": "image",
        "focus left": "sloc",
        "back in a field": "sloc",
        "completed": "image",
        "typed again": "image",
        "Next": "sloc",
    }


_LIMIT_PAGE = """
var els = {};
function $(id) {
  return els[id] = els[id] || { style: {}, textContent: '', setAttribute: function(k, v) { this[k] = v; } };
}
var document = { getElementById: $ };
var imageWidth = 1920, imageHeight = 1080;
"""


@pytest.mark.parametrize(
    ("lens", "bad_field", "was_open", "pair", "centre"),
    [
        ((-1.0, 0.0, 0.0, 0.0), False, False, "block", ""),
        # The centre is not named while the pair alone already fails.
        ((-1.0, 0.0, 0.2, 0.2), True, False, "block", ""),
        ((-0.6, -0.1, 0.2, 0.2), False, False, "none", LIMIT_CENTRE),
        ((-0.2, 0.0, 0.0, 0.0), True, False, "none", "Enter a whole number from \u2212275 to 275."),
        ((-0.2, 0.0, 0.2, 0.2), False, False, "none", ""),
        ((-0.2, 0.0, 0.2, 0.2), False, True, "none", ""),
    ],
    ids=["the pair", "the pair, a centre field too", "the centre", "a centre field", "valid", "valid, opened by hand"],
)
def test_a_lens_past_its_limit_says_which_value_and_opens_adjust_by_hand(
    lens: tuple[float, ...], bad_field: bool, was_open: bool, pair: str, centre: str
) -> None:
    out = run_wizard_js(
        "(function() { $('wiz_lens_k1').value = String(lens[0]); $('wiz_lens_k2').value = String(lens[1]);"
        " $('lens-adjust').open = was_open; wizShowLensLimit();"
        " return [$('wiz-lens-error').style.display, $('wiz-lens-centre-error').style.display,"
        " $('wiz-lens-centre-error').textContent, $('wiz_lens_cx')['aria-invalid'], $('lens-adjust').open]; })()",
        functions=(
            *_LENS_MODEL,
            "wizHalfDiag",
            "wizLensCentrePxMax",
            "wizLensCentreField",
            "wizReadLensCoeff",
            "wizLens",
            "wizLensCentreRangeError",
            "wizLensProblem",
            "wizShowLensLimit",
        ),
        variables=("LENS_CENTRE_LIMIT_ERROR", "wizLensCentreTyping"),
        prelude=_LIMIT_PAGE
        + f"var S = {{ lensCentre: [{lens[2]}, {lens[3]}], lensCentreBad: {json.dumps(bad_field)} }};",
        LENS_C_BOUND=C_BOUND,
        lens=list(lens),
        was_open=was_open,
    )
    shown = pair == "block" or bool(centre)
    # An error is never folded away; a valid lens leaves the section as the operator left it.
    assert out == [pair, "block" if centre else "none", centre, "true" if centre else "false", shown or was_open]
    assert lens_warp_is_valid(lens[0], lens[1], 0.0, 0.0) is (pair == "none")


@pytest.mark.parametrize(
    ("right", "above", "centre", "bad"),
    [("42", "18", [42 / _D, -18 / _D], False), ("-275", "275", [-275 / _D, -275 / _D], False), ("276", "0", None, True),
     ("", "3", None, True), ("3", "abc", None, True), ("12.5", "3", None, True), ("3", "-0.5", None, True),
     ("12.0", "3", [12 / _D, -3 / _D], False)],
    ids=["in range", "at the bound", "past the bound", "empty", "not a number", "half a pixel", "half a pixel below",
         "whole pixels written with a point"],
)  # fmt: skip
def test_a_centre_field_edit_takes_the_centre_only_while_both_fields_hold_one(
    right: str, above: str, centre: list | None, bad: bool
) -> None:
    out = run_wizard_js(
        "(function() { $('wiz_lens_cx').value = right; $('wiz_lens_cy').value = above;"
        " window.onWizardLensCentreInput(); return [S.lensCentre, S.lensCentreBad, edited]; })()",
        functions=(*_LENS_MODEL, *_CENTRE_PX, "wizReadLensCoeff", "wizLens", "wizTakeLensCentre"),
        prelude=_LIMIT_PAGE
        + "var window = {}, edited = 0, lensLines = [], S = { lensCentre: [0.01, 0.02], lensCentreBad: false };"
        + " function wizLensEdited() { edited++; } function lensLineCounts() { return true; }\n"
        + wizard_window_function("onWizardLensCentreInput"),
        LENS_C_BOUND=C_BOUND,
        right=right,
        above=above,
    )
    # A field without a number in range keeps the last centre, flagged so nothing saves it as if shown.
    assert out == [pytest.approx(centre) if centre else [0.01, 0.02], bad, 1]


_RANGE_CENTRE = "Enter a whole number from \u2212275 to 275."
_SHOW_LIMIT = (
    *_LENS_MODEL,
    *_CENTRE_PX,
    "wizReadLensCoeff",
    "wizLens",
    "wizLensValid",
    "wizLensCentreRangeError",
    "wizLensProblem",
    "wizShowLensLimit",
)


@pytest.mark.parametrize(
    ("pair", "applied", "bad", "fields", "text", "invalid"),
    [
        ((-0.2, 0.0), (0.0, 0.0), True, ("12", "300"), _RANGE_CENTRE, ["false", "true"]),
        ((-0.2, 0.0), (0.0, 0.0), True, ("12.5", "3"), _RANGE_CENTRE, ["true", "false"]),
        ((-0.2, 0.0), (0.0, 0.0), True, ("abc", "-276"), _RANGE_CENTRE, ["true", "true"]),
        ((-0.6, -0.1), (0.0, 0.0), True, ("220", "220"), LIMIT_CENTRE, ["true", "true"]),
        ((-0.6, -0.1), (0.2, -0.2), False, ("220", "220"), LIMIT_CENTRE, ["true", "true"]),
    ],
    ids=["right in range, above past it", "half a pixel right", "both off", "a typed centre not taken",
         "the centre in use, after a pair edit"],
)  # fmt: skip
def test_a_range_error_marks_only_its_field_and_the_limit_error_marks_both(
    pair: tuple, applied: tuple, bad: bool, fields: tuple, text: str, invalid: list
) -> None:
    out = run_wizard_js(
        "(function() { $('wiz_lens_k1').value = String(pair[0]); $('wiz_lens_k2').value = String(pair[1]);"
        " $('wiz_lens_cx').value = fields[0]; $('wiz_lens_cy').value = fields[1]; wizShowLensLimit();"
        " return [$('wiz-lens-centre-error').style.display, $('wiz-lens-centre-error').textContent,"
        " $('wiz_lens_cx')['aria-invalid'], $('wiz_lens_cy')['aria-invalid']]; })()",
        functions=_SHOW_LIMIT,
        variables=("LENS_CENTRE_LIMIT_ERROR", "wizLensCentreTyping"),
        prelude=_LIMIT_PAGE + f"var S = {{ lensCentre: {list(applied)}, lensCentreBad: {json.dumps(bad)} }};",
        LENS_C_BOUND=C_BOUND,
        pair=list(pair),
        fields=list(fields),
    )
    assert out == ["block", text, *invalid]


def test_the_centre_error_is_written_again_only_when_its_words_change() -> None:
    out = run_wizard_js(
        "(function() { $('wiz_lens_k1').value = '-0.2'; $('wiz_lens_cx').value = '300'; $('wiz_lens_cy').value = '0';"
        " wizShowLensLimit(); wizShowLensLimit(); $('wiz_lens_cx').value = '301'; wizShowLensLimit();"
        " var once = writes; S.lensCentreBad = false; wizShowLensLimit(); return [once, writes, err.textContent]; })()",
        functions=_SHOW_LIMIT,
        variables=("LENS_CENTRE_LIMIT_ERROR", "wizLensCentreTyping"),
        prelude=_LIMIT_PAGE
        + "var writes = 0, S = { lensCentre: [0, 0], lensCentreBad: true };"
        + " var err = els['wiz-lens-centre-error'] = { style: {}, words: '',"
        + " get textContent() { return this.words; }, set textContent(v) { writes++; this.words = v; } };",
        LENS_C_BOUND=C_BOUND,
    )
    # An alert rewritten with the same words is read out again on every keystroke.
    assert out == [1, 2, ""]


_TAKE_PAGE = (
    _LIMIT_PAGE
    + "var window = {}, edited = 0, fits = 0, lensLines = [{}], S = { lensCentre: [0.01, 0.02], lensCentreBad: false };"
    + " function wizLensEdited() { edited++; wizShowLensLimit(); } function lensLineCounts() { return true; }"
    + " function scheduleLensFit() { fits++; }\n"
    + wizard_window_function("onWizardLensCentreInput")
    + wizard_window_function("onWizardLensInput")
)
_TAKE = (*_SHOW_LIMIT, "wizWriteLensCoeff", "wizSyncLensRange", "wizTakeLensCentre")


def test_a_centre_that_stops_the_lens_short_keeps_the_last_one_until_a_pair_can_take_it() -> None:
    out = run_wizard_js(
        "(function() { $('wiz_lens_k1').value = '-0.6'; $('wiz_lens_k2').value = '-0.1';"
        " $('wiz_lens_cx').value = '220'; $('wiz_lens_cy').value = '220'; window.onWizardLensCentreInput();"
        " var held = [S.lensCentre.slice(), S.lensCentreBad, wizLensValid(), fits, edited,"
        " $('wiz-lens-centre-error').textContent, $('wiz_lens_cx')['aria-invalid'], $('wiz_lens_cy')['aria-invalid']];"
        " $('wiz_lens_k1').value = '-0.2'; $('wiz_lens_k2').value = '0'; window.onWizardLensInput('k1', 'number');"
        " return [held, [S.lensCentre, S.lensCentreBad, wizLensValid(), fits, $('wiz-lens-centre-error').style.display,"
        " $('wiz_lens_cx')['aria-invalid']]]; })()",
        functions=_TAKE,
        variables=("LENS_CENTRE_LIMIT_ERROR", "wizLensCentreTyping"),
        prelude=_TAKE_PAGE,
        LENS_C_BOUND=C_BOUND,
    )
    # Not taken: the last centre stays, nothing is fitted about the typed one, and nothing saves.
    assert out[0] == [[0.01, 0.02], True, False, 0, 1, LIMIT_CENTRE, "true", "true"]
    # A pair that follows the picture with the typed centre takes it, as typed by hand: no refit.
    assert out[1] == [pytest.approx([220 / _D, -220 / _D]), False, True, 0, "none", "false"]


def test_a_centre_typed_while_the_pair_alone_fails_is_taken() -> None:
    out = run_wizard_js(
        "(function() { $('wiz_lens_k1').value = '-1.0'; $('wiz_lens_k2').value = '0';"
        " $('wiz_lens_cx').value = '42'; $('wiz_lens_cy').value = '18'; window.onWizardLensCentreInput();"
        " return [S.lensCentre, S.lensCentreBad, fits, $('wiz-lens-error').style.display,"
        " $('wiz-lens-centre-error').style.display]; })()",
        functions=_TAKE,
        variables=("LENS_CENTRE_LIMIT_ERROR", "wizLensCentreTyping"),
        prelude=_TAKE_PAGE,
        LENS_C_BOUND=C_BOUND,
    )
    # The centre does not make the lens fail, so it is the one in use; the pair's error is the one shown.
    assert out == [pytest.approx([42 / _D, -18 / _D]), False, 1, "block", "none"]


@pytest.mark.parametrize(("w", "h"), [(1920, 1080), (1280, 720), (3840, 2160)])
def test_a_centre_at_the_bound_shows_within_its_fields_bound(w: int, h: int) -> None:
    out = run_wizard_js(
        "(function() { var max = wizLensCentrePxMax(w, h), a = wizLensCentrePx([b, -b], w, h),"
        " z = wizLensCentrePx([-b, b], w, h);"
        " return [max, a, z, wizLensCentreFromPx(String(a[0]), String(a[1]), w, h) !== null,"
        " wizLensCentreFromPx(String(z[0]), String(z[1]), w, h) !== null]; })()",
        functions=_CENTRE_PX,
        LENS_C_BOUND=C_BOUND,
        w=w,
        h=h,
        b=C_BOUND,
    )
    # Rounded alone, the bound lands half a pixel past the fields' bound at 720p and 2160p.
    assert out == [out[0], [out[0], out[0]], [-out[0], -out[0]], True, True]
    assert out[0] == math.floor(C_BOUND * 0.5 * math.hypot(w, h))


def _type(field: str, text: str) -> str:
    """Keystrokes into a centre field: each prefix of ``text`` with its input event, the error after each."""
    return (
        f"(function() {{ var el = $('{field}'), seen = []; el.id = '{field}';"
        f" for (var i = 0; i <= {len(text)}; i++) {{ el.value = {json.dumps(text)}.slice(0, i);"
        " window.onWizardLensCentreInput(el); var e = $('wiz-lens-centre-error');"
        " seen.push([e.style.display, e.textContent]); } return seen; })()"
    )


@pytest.mark.parametrize("text", ["-12", "300", "-0.5"], ids=["a number on its way", "past the bound", "a decimal"])
def test_a_centre_range_error_waits_until_its_field_is_left(text: str) -> None:
    out = run_wizard_js(
        "(function() { $('wiz_lens_k1').value = '-0.2'; $('wiz_lens_k2').value = '0';"
        " $('wiz_lens_cy').value = '0'; var typing = " + _type("wiz_lens_cx", text) + ";"
        " var cleared = " + _type("wiz_lens_cx", "") + ";"
        " window.onWizardLensCentreSettled(); var e = $('wiz-lens-centre-error');"
        " return [typing, cleared, e.style.display, e.textContent, $('wiz_lens_cx')['aria-invalid']]; })()",
        functions=_TAKE,
        variables=("LENS_CENTRE_LIMIT_ERROR", "wizLensCentreTyping"),
        prelude=_TAKE_PAGE + wizard_window_function("onWizardLensCentreSettled"),
        LENS_C_BOUND=C_BOUND,
    )
    typing, cleared, display, words, invalid = out
    # Nothing is announced while the field is typed in, an emptied field included.
    assert all(seen == ["none", ""] for seen in typing + cleared)
    # Left empty, the field says what it needs.
    assert [display, words, invalid] == ["block", _RANGE_CENTRE, "true"]


def test_the_lens_centre_limit_is_checked_while_typing() -> None:
    out = run_wizard_js(
        "(function() { $('wiz_lens_k1').value = '-0.6'; $('wiz_lens_k2').value = '-0.1';"
        " $('wiz_lens_cy').value = '100';"
        " return " + _type("wiz_lens_cx", "220") + "; })()",
        functions=_TAKE,
        variables=("LENS_CENTRE_LIMIT_ERROR", "wizLensCentreTyping"),
        prelude=_TAKE_PAGE,
        LENS_C_BOUND=C_BOUND,
    )
    # "2" and "22" still follow the picture with the pair; "220" does not, and says so at once.
    assert out[1:] == [["none", ""], ["none", ""], ["block", LIMIT_CENTRE]]


_SNAPSHOT_PAGE = (
    "var snapshotCanvas = {}, lensCanvas = null, lensFitSeq = 0, lensFit = null, lensSelected = null,"
    " lensPending = null, lensRejected = [], lensCandidates = [], currentStep = 0, WIZ = { lens: 1 }, notices = [];"
    " function lensShowNotice(t) { notices.push(t); } function renderLens() {} function renderLensResult() {}"
    " function saveToSession() {} function lensRequestEdges() {}\n"
)


@pytest.mark.parametrize(
    ("w", "h", "typed", "taken"), [(1920, 1080, "300", 30), (1280, 720, "208", 20), (1920, 1080, "275", 27)]
)
def test_a_centre_field_left_invalid_survives_a_new_snapshot_and_keeps_the_lens_from_being_saved(
    w: int, h: int, typed: str, taken: int
) -> None:
    pair = "'-0.6', '-0.1'" if typed == "275" else "'-0.2', '0'"
    out = run_wizard_js(
        "(function() { imageWidth = w; imageHeight = h; lensImageSize = [w, h]; wizShowLensCentre();"
        f" var p = [{pair}]; $('wiz_lens_k1').value = p[0]; $('wiz_lens_k2').value = p[1];"
        " $('wiz_lens_cy').value = '0';"
        " $('wiz_lens_cx').id = 'wiz_lens_cx';"
        " for (var i = 1; i <= typed.length; i++) { $('wiz_lens_cx').value = typed.slice(0, i);"
        " window.onWizardLensCentreInput($('wiz_lens_cx')); }"
        " window.onWizardLensCentreSettled(); lensOnSnapshot();"
        " return [$('wiz_lens_cx').value, S.lensCentre[0], S.lensCentreBad, wizLensValid(),"
        " $('wiz-lens-centre-error').textContent, wizLensSaveError(wizLensProblem()).error]; })()",
        functions=(*_TAKE, "wizShowLensCentre", "wizLensSaveError", "lensOnSnapshot"),
        variables=("LENS_CENTRE_LIMIT_ERROR", "wizLensCentreTyping"),
        prelude=_TAKE_PAGE
        + _SNAPSHOT_PAGE
        + "var lensImageSize = null;"
        + wizard_window_function("onWizardLensCentreSettled"),
        LENS_C_BOUND=C_BOUND,
        w=w,
        h=h,
        typed=typed,
    )
    bound = math.floor(C_BOUND * 0.5 * math.hypot(w, h))
    field, centre, bad, valid, words, refusal = out
    # The field keeps what was typed and its flag; the centre in use is the last one taken, which nothing saves.
    assert [field, bad, valid] == [typed, True, False]
    assert centre == pytest.approx(taken / (0.5 * math.hypot(w, h)))
    if typed == "275":
        assert words == LIMIT_CENTRE
        assert refusal == "With this lens centre, the overlay cannot follow the picture out to its corners."
    else:
        assert words == f"Enter a whole number from \u2212{bound} to {bound}."
        assert refusal == f"A lens centre field does not hold a whole number from \u2212{bound} to {bound}."


def test_a_snapshot_of_another_size_shows_the_centre_in_use_again() -> None:
    out = run_wizard_js(
        "(function() { lensImageSize = [1280, 720]; $('wiz_lens_cx').value = '300'; $('wiz_lens_cy').value = '0';"
        " S.lensCentreBad = true; lensOnSnapshot(); return [$('wiz_lens_cx').value, S.lensCentreBad]; })()",
        functions=(*_TAKE, "wizShowLensCentre", "lensOnSnapshot"),
        variables=("LENS_CENTRE_LIMIT_ERROR", "wizLensCentreTyping"),
        prelude=_TAKE_PAGE + _SNAPSHOT_PAGE + "var lensImageSize = null;",
        LENS_C_BOUND=C_BOUND,
    )
    # The typed pixels counted in the old picture's size: the fields show the centre in use, in the new one's.
    assert out == [round(0.01 * _D), False]


_NO_SOLVE = {"marks": [], "error": "No camera fits."}


@pytest.mark.parametrize(
    ("solve", "shown"),
    [
        (
            {**_NO_SOLVE, "withHeight": True},
            [
                "image",
                "The camera cannot be calculated at this camera height.",
                "Check the camera height, the size of the rectangle, and that each corner is on its tape mark.",
            ],
        ),
        (
            {**_NO_SOLVE, "withHeight": False},
            [
                "image",
                "The camera cannot be calculated from these corners.",
                "Check the size of the rectangle, and check that each corner is on its tape mark.",
            ],
        ),
    ],
    ids=["at an entered camera height", "without one"],
)
def test_the_simple_hint_names_the_camera_height_when_no_camera_fits_at_it(solve: dict, shown: list) -> None:
    out = run_wizard_js(
        "(function() { renderSimpleReq(); return shown; })()",
        functions=(*_MARK_FUNCTIONS, *_CAM_HINT_FUNCTIONS, "esc", "lenText", "fmt", "renderSimpleReq"),
        variables=(*_MARK_VARS, "CORNER_TITLES", "camNamed"),
        prelude=length_page("metric", {})
        + _DOM
        + "var shown = null; function showHint(area, h, sub) { shown = [area, h, sub]; }"
        f" var S = {{ simpleLoc: 'back', rect: {json.dumps(_READY)}, solve: {json.dumps(solve)},"
        " cornerMoved: { DSL: true, DSR: true, USR: true, USL: true } };",
    )
    # The hint and the status line under the picture name the same cause.
    assert out == shown


@pytest.mark.parametrize(
    ("solve", "shown"),
    [
        (
            {**_NO_SOLVE, "withHeight": True},
            [
                "The camera cannot be calculated at this camera height.",
                "Check the camera height, the pins and the measurements.",
            ],
        ),
        (
            {**_NO_SOLVE, "withHeight": False},
            ["The camera cannot be calculated from these points.", "Check the pins and the measurements."],
        ),
        (
            {"marks": [], "cam": {"fov": 60}, "withHeight": True},
            [
                "Check the result.",
                "Check the grid on the picture and the Distances to check. Then press Next. More known points or"
                " distance points give a better result.",
            ],
        ),
    ],
    ids=["at an entered camera height", "without one", "solved at a height"],
)
def test_the_advanced_hint_names_the_camera_height_when_no_camera_fits_at_it(solve: dict, shown: list) -> None:
    out = run_wizard_js(
        "(function() { currentMarks = function() { return [1, 2, 3, 4].map(function(i) {"
        " return { id: i, kind: 'known', name: 'P' + i, pin: [i, i] }; }); };"
        " renderReq(); return shown; })()",
        functions=(*_MARK_FUNCTIONS, *_CAM_HINT_FUNCTIONS, "esc", "renderReq"),
        variables=(*_MARK_VARS, "camNamed"),
        prelude=length_page("metric", {})
        + _DOM
        + "var S = { mode: 'advanced', cam: { x: 0, y: 10, z: 8 }, camPartial: false, points: [], rect: {},"
        f" solve: {json.dumps(solve)} }};"
        " var SAVED = { max_marks: 12 }, shown = null;"
        " function showHint(area, h, sub) { shown = [h, sub]; } function lockSimpleSteps() {}",
    )
    assert out == shown


def test_the_advanced_hint_names_a_partly_filled_camera_only_once_the_typing_is_done() -> None:
    out = run_wizard_js(
        "(function() { function draw() { renderReq(); } return " + _CAM_STEP_JS + "; })()",
        functions=(*_MARK_FUNCTIONS, *_CAM_HINT_FUNCTIONS, "esc", "renderReq"),
        variables=(*_MARK_VARS, "camNamed"),
        prelude=length_page("metric", {})
        + _DOM
        + "var S = { mode: 'advanced', cam: null, camPartial: false, points: [], rect: {} };"
        " var SAVED = { max_marks: 12 }, shown = null;"
        " function showHint(area, h) { shown = h; } function lockSimpleSteps() {}",
        steps=_CAM_STEPS,
    )
    named, first = "Enter all three values of the camera position.", "Enter the rough camera position first."
    # The heading is not rewritten (and read out again) while the first value is typed.
    assert out == [first, named, named, first, first, named]


@pytest.mark.parametrize(
    ("left", "to", "drawn"),
    [("cam_pos_x", "cam_pos_y", 0), ("cam_pos_z", "rect_w", 1), ("cam_pos_x", None, 1), ("rect_w", "cam_pos_x", 0)],
    ids=["to the next camera field", "out of the fields", "to nothing", "into the fields"],
)
def test_leaving_the_camera_fields_draws_the_hint_again(left: str, to, drawn: int) -> None:
    out = run_wizard_js(
        "(function() { calibInit(); reqs = 0;"
        " fire('document', 'focusout', { target: $(left), relatedTarget: to && $(to) }); return reqs; })()",
        functions=_MENU_FUNCTIONS,
        prelude=_MENU_PAGE,
        left=left,
        to=to,
    )
    assert out == drawn


@pytest.mark.parametrize(("values", "drawn"), [(["1", "", ""], 1), (["1", "2", "3"], 0)], ids=["partly", "complete"])
def test_next_with_a_partly_filled_camera_draws_the_hint_with_its_error(values: list, drawn: int) -> None:
    out = run_wizard_js(
        "(function() { S.mode = 'simple'; fill(values); window.calibNext(); return [reqs, $('cam-error').hidden]; })()",
        functions=_CAM_FUNCTIONS,
        variables=("exactLen", "DISTANCE_SIGMA"),
        prelude=length_page("metric", {})
        + _CAM_PAGE
        + "var reqs = 0; function renderReq() { reqs++; }"
        + wizard_window_function("calibNext"),
        values=values,
        S={"mode": "simple"},
    )
    # The hint follows the error the same moment: it names the camera above the location.
    assert out == [drawn, not drawn]


def test_a_menu_opened_at_a_spot_ignores_the_click_of_its_press() -> None:
    out = _pin_tap(
        "S.points[1].pin = null; var seen = [], usr = item({ place: '3' });"
        " dispatch('cal-overlay', 'pointerdown', picture); openPinMenu([7, 7]);"
        " dispatch('pin-menu', 'click', usr); seen.push(state());"
        " dispatch('pin-menu', 'pointerdown', usr); dispatch('pin-menu', 'click', usr); seen.push(state());"
        " return seen;"
    )
    assert out == [[None, ["2:pinned", "3:-"]], [None, _BOTH_PINNED]]


@pytest.mark.parametrize(("type", "at", "taps"), [("mouse", 13, 0), ("pen", 18, 0), ("touch", 17, 1)])
def test_a_click_away_from_its_press_is_no_tap_even_without_a_reported_move(type: str, at: int, taps: int) -> None:
    out = run_wizard_js(
        "(function() { bindDrag('cal-overlay', 'cal-container', '#cal-hit',"
        " { start: function() { return null; }, tap: function(p) { taps.push(p); } });"
        " press('hit', type, 10, 10); click(at, 10); return taps.length; })()",
        functions=("bindDrag", "tapSlop"),
        prelude=_DRAG_PAGE,
        type=type,
        at=at,
    )
    assert out == taps


# ---------------------------------------------------------------------------
# On a picture under 480 x 360 CSS px the Advanced menu is a panel under the picture, in full screen a sheet
# ---------------------------------------------------------------------------

_PANEL_PAGE = """
// The picture's CSS size decides where the one menu goes; every element keeps its parent, so a move shows.
var view = { w: 960, h: 540 }, scrolls = [];
function setTimeout(f) { f(); return 0; } function clearTimeout() {}
var window = { addEventListener: function(t, f) { listeners.push(['window', t, f, false]); } };
var resizeTimer = null; function renderLens() {} function renderReview() {} function renderFsBar() {}
function classes() {
  var on = {};
  return { add: function(c) { on[c] = true; }, remove: function(c) { delete on[c]; },
    toggle: function(c, f) { if (f) on[c] = true; else delete on[c]; }, contains: function(c) { return !!on[c]; },
    list: function() { return Object.keys(on).sort(); } };
}
function adopt(parent, child) {
  if (child.parentNode) child.parentNode.kids.splice(child.parentNode.kids.indexOf(child), 1);
  child.parentNode = parent; parent.kids.push(child);
}
function box(id, extra) {
  els[id] = Object.assign(fakeEl(id), { kids: [], appendChild: function(c) { adopt(this, c); },
    insertBefore: function(c, ref) { adopt(this, c); this.before = ref; }, querySelector: function() { return null; } },
    extra || {});
}
var fsBtn = { id: 'fs-btn' };
box('pin-menu-slot'); box('fs-shell');
box('fs-stage', { getBoundingClientRect: function() { return { width: 844, height: 348 }; } });
box('cal-container', { getBoundingClientRect: function() { return { width: view.w, height: view.h }; },
  querySelector: function(sel) { return sel === '.fs-btn' ? fsBtn : null; } });
els['pin-menu'] = Object.assign(fakeEl('pin-menu'), { hidden: true, classList: classes(), offsetWidth: 260,
  offsetHeight: 180, scrollIntoView: function(o) { scrolls.push(o.block); },
  querySelector: function() { return { focus: function() { focused = 'first item'; } }; } });
els['cal-container'].appendChild(els['pin-menu']);
function where() {
  var m = $('pin-menu');
  return [m.parentNode.id, m.classList.list(), !!m.style.left];
}
var away = { isConnected: true, nodeType: 1, closest: function() { return null; },
  matches: function() { return false; } };
var AXIS_NOTE = { x: '', y: '', z: '' }; function lenSuffix() { return 'm'; } function lenInput() { return ''; }
function lenField() { return '0'; } function pointOptions() { return ''; } function refreshEchoes() {}
els['pm-point'] = Object.assign(fakeEl('pm-point'), { select: function() {} });
"""
_PANEL_FUNCTIONS = (
    *_PIN_TAP_FUNCTIONS,
    "pinMenuAsPanel",
    "positionPinMenu",
    "menuLoupe",
    "fsLayout",
    "fsRender",
    "nudgeMenuSpot",
    "pinMenuForm",
)


def _panel(expression: str, **inputs) -> list:
    return run_wizard_js(
        "(function() { calibInit();" + expression + " })()",
        functions=_PANEL_FUNCTIONS,
        prelude=_MENU_PAGE + _PIN_TAP_PAGE + _PANEL_PAGE,
        **inputs,
    )


@pytest.mark.parametrize(
    ("size", "panel"),
    [
        ((358, 201.4), True),
        ((620, 348.8), True),
        ((479.9, 400), True),
        ((480, 359.9), True),
        ((480, 360), False),
        ((990, 557), False),
        ((1200, 675), False),
    ],
    ids=[
        "portrait phone",
        "landscape phone",
        "just under 480 wide",
        "just under 360 high",
        "480 x 360",
        "1024 x 768 tablet",
        "desktop",
    ],
)
def test_the_menu_is_a_panel_on_a_picture_under_480_by_360(size: tuple, panel: bool) -> None:
    out = _panel("view = { w: size[0], h: size[1] }; openPinMenu([960, 540]); return where();", size=size)
    # A panel sits in the slot under the picture, full width; the popup stays in the picture, before its button.
    assert out == (["pin-menu-slot", ["as-panel"], False] if panel else ["cal-container", [], True])


def test_a_panel_opens_under_the_picture_and_scrolls_into_view() -> None:
    out = _panel(
        "view = { w: 358, h: 201 }; openPinMenu([960, 540]); var opened = [where(), scrolls.slice(), focused];"
        " S.pinMenu.p = [10, 10]; positionPinMenu(); var moved = scrolls.length;"
        " placeFromMenu('new-known'); var formed = scrolls.length;"
        " closePinMenu(); openPinMenu([5, 5]); closePinMenu(); openPointMenu(2);"
        " return [opened, moved, formed, scrolls.length];"
    )
    # It opens out of the clipping picture and into view; moving its spot does not scroll, a new form and
    # each opening in the slot it already sits in do.
    assert out == [[["pin-menu-slot", ["as-panel"], False], ["nearest"], "first item"], 1, 2, 4]


def test_the_popup_scrolls_into_view_as_it_opens_or_changes() -> None:
    out = _panel(
        "openPinMenu([960, 540]); var opened = scrolls.length; S.pinMenu.p = [10, 10]; positionPinMenu();"
        " var moved = scrolls.length; placeFromMenu('new-known'); var formed = scrolls.length;"
        " closePinMenu(); openPointMenu(2); var pinned = scrolls.length;"
        " closePinMenu(); FS = { key: 'cal', pad: 0 }; openPinMenu([5, 5]);"
        " return [opened, moved, formed, pinned, scrolls];"
    )
    # A menu opened from the keyboard near the bottom of a tall window is brought on screen; moving its spot
    # does not scroll, and full screen never does.
    assert out == [1, 1, 2, 3, ["nearest", "nearest", "nearest"]]


@pytest.mark.parametrize(("panel", "cross"), [(False, False), (True, True)], ids=["popup", "panel"])
def test_a_panels_spot_is_marked_with_a_crosshair(panel: bool, cross: bool) -> None:
    out = run_wizard_js(
        "(function() { var shown = []; $('pin-menu').classList = { contains: function(c) {"
        " return panel && c === 'as-panel'; } };"
        " [{ p: [100, 50], pin: null }, { p: [10, 10], pin: 1 }, null].forEach(function(m) {"
        " S.pinMenu = m; renderCalibOverlay(); var h = $('cal-pins').innerHTML;"
        " function n(c) { return (h.match(new RegExp('class=\"' + c + '\"', 'g')) || []).length; }"
        " shown.push([n('pin-pending'), n('pin-pending-cross')]); });"
        " return shown; })()",
        functions=("esc", "pinBehind", "pinFlagged", "pinLabel", "pendingMark", "renderCalibOverlay"),
        variables=("DISTANCE_SIGMA",),
        prelude=_DOM + _REVIEW_STUBS + "imageWidth = 1920; imageHeight = 1080;"
        " var S = { mode: 'advanced', pair: 0, selPin: null, selCorner: null, pinMenu: null, solve: null };"
        " function currentMarks() { return [{ id: 1, name: 'DSL', pin: [10, 10] }]; }",
        panel=panel,
    )
    # A spot's menu marks the spot (the panel adds a crosshair, as no menu points at it); a pin's menu has its pin.
    assert out == [[1, 1 if cross else 0], [0, 0], [0, 0]]


def test_a_pins_menu_shows_its_pin_selected() -> None:
    out = _panel("view = { w: 358, h: 201 }; openPointMenu(3); return [S.selPin, where()[0]];")
    assert out == [3, "pin-menu-slot"]


@pytest.mark.parametrize(
    ("layout", "gesture", "result"),
    [
        ("popup", "press elsewhere", ["closed", None, None]),
        ("panel", "press elsewhere", [2, 2, None]),
        ("panel", "click elsewhere", ["closed", None, None]),
        ("panel", "x", ["closed", 2, "pin 2"]),
        ("panel", "x, focus elsewhere", ["closed", None, None]),
        ("panel", "tap on the picture", [None, None, "first item"]),
        ("panel", "press on another pin", ["closed", 3, None]),
        ("popup", "press on another pin", ["closed", 3, None]),
    ],
    ids=[
        "popup: a press elsewhere closes it",
        "panel: the press of a swipe",
        "panel: a click elsewhere closes it",
        "panel: x gives the pin the focus",
        "panel: x with the focus elsewhere",
        "panel: a tap on the picture opens a spot's menu",
        "panel: a press on another pin",
        "popup: a press on another pin",
    ],
)
def test_a_pin_stays_selected_while_its_menu_is_open_and_focused(layout: str, gesture: str, result: list) -> None:
    out = _panel(
        "view = layout === 'panel' ? { w: 358, h: 201 } : { w: 960, h: 540 }; openPointMenu(2);"
        " document.activeElement = gesture === 'x, focus elsewhere' ? null : { inside: 'pin-menu' }; focused = null;"
        " if (gesture.indexOf('elsewhere') >= 0 && gesture[0] !== 'x') dispatch('page', 'pointerdown', away);"
        " if (gesture === 'click elsewhere') dispatch('page', 'click', away);"
        " if (gesture[0] === 'x') { dispatch('pin-menu', 'pointerdown', item({ pm: 'close' }));"
        " dispatch('pin-menu', 'click', item({ pm: 'close' })); }"
        " if (gesture === 'tap on the picture') { dispatch('cal-overlay', 'pointerdown', picture);"
        " dispatch('cal-overlay', 'click', picture); }"
        " if (gesture === 'press on another pin') dispatch('cal-overlay', 'pointerdown', pin(3));"
        " return [S.pinMenu ? S.pinMenu.pin : 'closed', S.selPin, focused];",
        layout=layout,
        gesture=gesture,
    )
    # A pin is selected only while its menu is open or it has the focus; a swipe keeps a panel and its pin.
    assert out == result


_KEYS_AND_FOCUS = (
    "view = layout === 'panel' ? { w: 358, h: 201 } : { w: 960, h: 540 }; eval(escKey); var seen = [];"
    " function key(k) { dispatch('page', 'keydown', away, { key: k, nodeType: 1 }); }"
    " openPinMenu([100, 100]); document.activeElement = { inside: 'pin-menu' }; seen.push(focused);"
    " key('ArrowRight'); key('ArrowDown'); seen.push(S.pinMenu.p);"
    " key('Escape'); seen.push(focused, S.pinMenu);"
    " openPointMenu(2); document.activeElement = { inside: 'pin-menu' }; seen.push(focused);"
    " dispatch('pin-menu', 'pointerdown', item({ pm: 'close' })); dispatch('pin-menu', 'click', item({ pm: 'close' }));"
    " seen.push(focused, S.pinMenu);"
    " openPinMenu([5, 5]); fire('document', 'focusin', { target: away }); seen.push(S.pinMenu);"
    " return seen;"
)


@pytest.mark.parametrize("layout", ["popup", "panel"])
def test_a_panel_takes_the_same_keys_and_focus_as_the_popup(layout: str) -> None:
    esc_key = wizard_block("document.addEventListener('keydown', function(e) {\n    if (e.key !== 'Escape') return;")
    out = _panel(_KEYS_AND_FOCUS, layout=layout, escKey=esc_key)
    # Its first item takes the focus, the arrows move the spot, Esc and x give the focus back, Tab out closes it.
    assert out == ["first item", [101, 101], "cal-overlay", None, "first item", "pin 2", None, None]


@pytest.mark.parametrize("layout", ["popup", "panel"])
def test_a_panel_ignores_the_clicks_of_the_gesture_that_opened_it(layout: str) -> None:
    out = _panel(
        "view = layout === 'panel' ? { w: 358, h: 201 } : { w: 960, h: 540 };"
        " var del = item({ pm: 'delete' }), seen = [];"
        " dispatch('cal-overlay', 'pointerdown', pin(2)); dispatch('cal-overlay', 'pointerup', pin(2));"
        " clickAt(pin(2), del); seen.push(state(), where()[0]);"
        " dispatch('pin-menu', 'pointerdown', del); dispatch('pin-menu', 'click', del);"
        " seen.push(state()); return seen;",
        layout=layout,
    )
    home = "pin-menu-slot" if layout == "panel" else "cal-container"
    assert out == [[2, _BOTH_PINNED], home, [None, ["3:pinned"]]]


@pytest.mark.parametrize(
    ("layout", "event", "target", "closed"),
    [
        ("popup", "pointerdown", "away", True),
        ("panel", "pointerdown", "away", False),
        ("panel", "click", "away", True),
        ("panel", "click", "rebuilt", False),
        ("panel", "click", "menu", False),
        ("panel", "click", "picture", False),
    ],
    ids=[
        "popup: a press elsewhere",
        "panel: the press of a swipe",
        "panel: a click elsewhere",
        "panel: a rebuilt menu's item",
        "panel: inside the panel",
        "panel: on the picture",
    ],
)
def test_a_panel_closes_on_a_click_elsewhere_not_on_a_press(layout: str, event: str, target: str, closed: bool) -> None:
    out = _panel(
        "view = layout === 'panel' ? { w: 358, h: 201 } : { w: 960, h: 540 }; openPinMenu([100, 100]);"
        " var t = { away: away, rebuilt: item({ pm: 'none' }), menu: item({ pm: 'none' }), picture: picture }[target];"
        " if (target === 'rebuilt') listeners.push(['page', 'click', function() {"
        " t.isConnected = false; t.closest = function() { return null; }; }, false]);"
        " dispatch('page', event, t); return S.pinMenu === null;",
        layout=layout,
        event=event,
        target=target,
    )
    # A swipe that scrolls the page down to the panel starts with a press, and makes no click.
    assert out is closed


def test_full_screen_shows_the_menu_as_a_sheet_with_the_picture_above_it() -> None:
    out = _panel(
        "FS = { key: 'cal', cont: { style: {} }, pad: 0 }; view = { w: 390, h: 219 };"
        " openPinMenu([960, 540]);"
        " var open = [where(), $('fs-stage').style.paddingBottom, FS.cont.style.width, scrolls];"
        " placeFromMenu('new-known'); open.push(scrolls.length);"
        " closePinMenu(); return [open, $('fs-stage').style.paddingBottom, FS.cont.style.width];"
    )
    # The sheet takes the shell's bottom 180 px: the picture fits the 168 px above it, and is whole again once closed.
    # A sheet never scrolls the page behind the shell, not even for a form.
    assert out == [[["fs-shell", ["as-panel", "as-sheet"], False], "180px", "298px", [], 0], "", "618px"]


def test_full_screen_on_a_tablet_keeps_the_popup() -> None:
    out = _panel(
        "FS = { key: 'cal', cont: { style: {} }, pad: 0 }; view = { w: 990, h: 557 };"
        " openPinMenu([960, 540]); return [where()[0], where()[1], $('fs-stage').style.paddingBottom || ''];"
    )
    assert out == ["cal-container", [], ""]


@pytest.mark.parametrize(
    ("first", "then", "home"),
    [("popup", "panel", "pin-menu-slot"), ("panel", "popup", "cal-container")],
    ids=["turned to portrait", "turned to landscape"],
)
def test_a_resize_moves_an_open_menu_and_keeps_its_focus(first: str, then: str, home: str) -> None:
    out = _panel(
        "var sizes = { popup: { w: 990, h: 557 }, panel: { w: 358, h: 201 } }; view = sizes[first]; eval(onResize);"
        " openPinMenu([960, 540]); var base = renderCalibOverlay, drawnAs = null;"
        " renderCalibOverlay = function() { base(); drawnAs = $('pin-menu').classList.contains('as-panel'); };"
        " document.activeElement = { inside: 'pin-menu', focus: function() { focused = 'kept'; } };"
        " view = sizes[then]; fire('window', 'resize', {});"
        " return [where(), $(home).before === fsBtn, focused, drawnAs, S.pinMenu.p];",
        first=first,
        then=then,
        home=home,
        onResize=wizard_block("window.addEventListener('resize'"),
    )
    # Moving the element drops its focus: it is given back. The spot's marker is drawn for the new layout.
    # The popup goes back before the picture's full-screen button, its place in the Tab order.
    placed = [home, ["as-panel"], False] if then == "panel" else [home, [], True]
    assert out == [placed, then == "popup", "kept", then == "panel", [960, 540]]


def test_a_panel_is_full_width_and_never_scrolls_inside() -> None:
    src = WIZARD_TEMPLATE.read_text(encoding="utf-8")
    panel = re.search(r"\n  \.pin-menu\.as-panel \{([^}]*)\}", src).group(1)
    for decl in ("position: static", "width: auto", "max-width: none", "max-height: none", "overflow: visible"):
        assert decl in panel, decl
    sheet = re.search(r"\n  \.pin-menu\.as-sheet \{([^}]*)\}", src).group(1)
    for decl in ("position: absolute", "left: 0", "right: 0", "bottom: 0", "max-height: 50%", "overflow-y: auto"):
        assert decl in sheet, decl
    # The layout classes come after the popup's own width for a form, so they win.
    assert src.index(".pin-menu.as-panel {") > src.index(".pin-menu.form {")


def test_the_panel_slot_is_under_the_picture_outside_its_clipping_frame() -> None:
    src = WIZARD_TEMPLATE.read_text(encoding="utf-8")
    start, slot = src.index('<div id="cal-container"'), src.index('<div id="pin-menu-slot"></div>')
    between = src[start:slot]
    # The picture's container is closed before the slot, and the Refresh Image row follows it.
    assert between.count("<div") == between.count("</div>")
    assert re.match(r'\s*<div id="cal-no-feed"', src[slot + len('<div id="pin-menu-slot"></div>') :])
    assert slot < src.index('id="cal-refresh"')
    assert src.count('id="pin-menu"') == 1


@pytest.mark.parametrize(("size", "avoided"), [((358, 201), False), ((960, 540), True)], ids=["panel", "popup"])
def test_the_loupe_keeps_clear_only_of_a_menu_on_the_picture(size: tuple, avoided: bool) -> None:
    out = _panel(
        "var avoid = 'none'; showLoupe = function(c, o, p, a) { avoid = a; };"
        " view = { w: size[0], h: size[1] }; openPinMenu([960, 540]); return avoid === $('pin-menu');",
        size=size,
    )
    # A panel under the picture covers none of it; its offsets are not the picture's.
    assert out is avoided


# ---------------------------------------------------------------------------
# A press never moves what it pressed before its click
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("layout", ["popup", "panel"])
@pytest.mark.parametrize(
    ("kind", "gesture", "result"),
    [
        ("mouse", "tap", [3, [6, 6]]),
        ("touch", "tap", [3, [6, 6]]),
        ("pen", "tap", [3, [6, 6]]),
        ("mouse", "drag", [None, [80, 20]]),
        ("touch", "drag", [None, [80, 20]]),
        ("touch", "cancel", [None, [6, 6]]),
    ],
    ids=["a mouse click", "a tap", "a pen tap", "a mouse drag", "a finger drag", "a cancelled press"],
)
def test_a_press_on_another_pin_with_a_menu_open_acts_as_on_a_closed_one(
    layout: str, kind: str, gesture: str, result: list
) -> None:
    out = _panel(
        "view = layout === 'panel' ? { w: 358, h: 201 } : { w: 960, h: 540 }; openPinMenu([100, 100]);"
        " var base = renderCalibOverlay, gen = 0, ring = true;"
        " renderCalibOverlay = function() { gen++; ring = !!(S.pinMenu && !S.pinMenu.pin); base(); };"
        " var pressed = pin(3), at = gen, x = 10;"
        " function send(type) { dispatch('cal-overlay', type, pressed, { pointerType: kind, clientX: x }); }"
        " send('pointerdown'); var during = [ring, gen === at];"
        " if (gesture === 'drag') { x = 40; send('pointermove'); }"
        " send(gesture === 'cancel' ? 'pointercancel' : 'pointerup');"
        # A mouse's click goes to the pressed element, and none comes once it was rebuilt; a tap's is aimed anew.
        " if (gesture === 'tap' && (kind !== 'mouse' || gen === at)) send('click');"
        " return [during, S.pinMenu && S.pinMenu.pin, ring, S.points[1].pin];",
        layout=layout,
        kind=kind,
        gesture=gesture,
    )
    # The press closes the menu and leaves the picture as drawn: the closed menu's ring goes once the press ends.
    menu, pinned = result
    assert out == [[True, True], menu, False, pinned]


# A page whose full-screen shell takes the picture; scroll anchoring moves the page as the picture leaves it.
_FULL_SCROLL_PAGE = """
var seen = [], scroll = 745, FS = null, lensSelected = null, lensPending = null;
var window = { scrollX: 0, scrollTo: function(x, y) { scroll = y; } };
Object.defineProperty(window, 'scrollY', { get: function() { return scroll; } });
var placeholder = { style: {} };
var page = { insertBefore: function(n) { n.parentNode = page; }, removeChild: function() {} };
var btn = { focus: function() { seen.push(scroll); } };
var cont = { style: {}, parentNode: page, getBoundingClientRect: function() { return { height: 435 }; },
  querySelector: function() { return btn; } };
function getComputedStyle() { return { margin: '0px 0px 16px' }; }
var shell = { hidden: true, requestFullscreen: function() {
  if (full) document.fullscreenElement = shell; return Promise.resolve(); } };
var els = { 'fs-stage': { appendChild: function(c) { c.parentNode = this; scroll = 1182; } }, 'fs-shell': shell,
  'fs-exit': { focus: function() {} }, 'fs-hint': {} };
function $(id) { return els[id]; } function fsContainer() { return cont; }
var document = { body: { style: {} }, fullscreenElement: null, createElement: function() { return placeholder; },
  exitFullscreen: function() {
    return Promise.resolve().then(function() { document.fullscreenElement = null; scroll = 300; }); } };
function closeHelp() {} function dropCorner() {} function dropLensPoint() {} function fsLayout() {}
function toggleFsHelp() {} function closePinMenu() {} function fsRender() {}
"""


@pytest.mark.parametrize("full", [True, False], ids=["full screen", "window-filling shell"])
def test_leaving_full_screen_brings_the_page_back_where_it_was(full: bool) -> None:
    out = run_wizard_js(
        "(async function() { openFull('cal'); var held = [placeholder.style.height, placeholder.style.margin];"
        " closeFull(); await new Promise(function(r) { setTimeout(r, 5); }); return [held, seen]; })()",
        functions=("openFull", "closeFull"),
        prelude=_FULL_SCROLL_PAGE,
        full=full,
    )
    # The placeholder keeps the picture's room; the page is back at its scroll before the button takes the focus.
    assert out == [["435px", "0px 0px 16px"], [745]]


# The Lens picture's listeners and the page's, with the hint box above the picture.
_LENS_PRESS_PAGE = (
    _LENS_TAP_PAGE
    + """
var docOn = [], timers = [], box = { style: {}, offsetHeight: 126, innerHTML: '' };
document.addEventListener = function(t, f, capture) { docOn.push([t, f, !!capture]); };
function $(id) { return id === 'lens-hint' ? box : { addEventListener: function() {} }; }
function setTimeout(f) { timers.push(f); return timers.length; }
function clearTimeout(id) { if (id) timers[id - 1] = null; }
function lensFitApplies() { return false; } function lensOpenCandidates() { return []; } function renderFsBar() {}
var lensFit = null, lensHintText = '', S = { lensLoaded: null };
renderLens = lensChanged = function() { renderLensHint(); };
lensTraceClick = function(p) {
  traced++; if (lensPending) { lensPending = null; lensLines.push({}); } else lensPending = p; renderLensHint(); };
// The page's capture listeners, the picture's own, then the page's bubble listeners, as the browser runs them.
function send(type, target, kind, x) {
  var e = { type: type, target: target, pointerType: kind, pointerId: 1, clientX: x, clientY: 10,
    preventDefault: function() {} };
  docOn.filter(function(l) { return l[0] === type && l[2]; }).forEach(function(l) { l[1](e); });
  if (on[type]) on[type](e);
  docOn.filter(function(l) { return l[0] === type && !l[2]; }).forEach(function(l) { l[1](e); });
}
function words() { return lensHintText; }
"""
)
_LENS_PRESS_FUNCTIONS = (
    "tapSlop",
    "lensInit",
    "esc",
    "setLive",
    "lensMiddleOnly",
    "lensShownRating",
    "renderLensHint",
    "lensPressEnd",
)
_DRAW_MORE = "Draw more straight lines."
_OTHER_END = "Click the other end of the straight edge."
_MOVE_POINT = "Move the point exactly onto the edge."


@pytest.mark.parametrize(
    ("kind", "pressed", "path", "result"),
    [
        ("touch", "hit", [10], [_DRAW_MORE, _DRAW_MORE, _OTHER_END]),
        ("mouse", "hit", [10], [_DRAW_MORE, _DRAW_MORE, _OTHER_END]),
        ("touch", "handle", [10], [_DRAW_MORE, _DRAW_MORE, _MOVE_POINT]),
        ("pen", "handle", [10], [_DRAW_MORE, _DRAW_MORE, _MOVE_POINT]),
        ("mouse", "handle", [10], [_DRAW_MORE, _MOVE_POINT, _MOVE_POINT]),
        ("touch", "handle", [40], [_DRAW_MORE, _DRAW_MORE, _MOVE_POINT]),
    ],
    ids=[
        "a tap on the picture",
        "a mouse click on it",
        "a tap on a point",
        "a pen tap on a point",
        "a mouse click on a point",
        "a finger drag of a point",
    ],  # fmt: skip
)
def test_the_lens_hint_keeps_its_words_until_a_tap_has_clicked(
    kind: str, pressed: str, path: list, result: list
) -> None:
    out = run_wizard_js(
        "(function() { lensInit(); renderLensHint(); var t = pressed === 'hit' ? hit : handle, seen = [], x = 10;"
        " send('pointerdown', t, kind, x); path.forEach(function(q) { x = q; send('pointermove', t, kind, x); });"
        " seen.push(words()); send('pointerup', t, kind, x); seen.push(words());"
        # A finger that moved makes no click: the hint follows shortly after its release.
        " if (x === 10) send('click', t, kind, x); else timers.forEach(function(f) { if (f) f(); });"
        " seen.push(words()); return seen; })()",
        functions=_LENS_PRESS_FUNCTIONS,
        variables=("lensPress",),
        prelude=_LENS_PRESS_PAGE,
        kind=kind,
        pressed=pressed,
        path=path,
    )
    # A box changing height before a tap's click moves the picture, and the click lands on what is then there.
    assert out == result


def test_a_started_line_keeps_the_lens_hint_box_height_until_it_ends() -> None:
    out = run_wizard_js(
        "(function() { lensInit(); renderLensHint(); var seen = [];"
        " function tap(x) { send('pointerdown', hit, 'touch', x); send('pointerup', hit, 'touch', x);"
        " send('click', hit, 'touch', x); box.offsetHeight = 65; seen.push([words(), box.style.minHeight, traced]); }"
        " tap(10); tap(200); return seen; })()",
        functions=_LENS_PRESS_FUNCTIONS,
        variables=("lensPress",),
        prelude=_LENS_PRESS_PAGE,
    )
    # The shorter words of a started line sit in the box's earlier height, so its second tap hits the same picture.
    assert out == [[_OTHER_END, "126px", 1], [_DRAW_MORE, "", 2]]


@pytest.mark.parametrize(
    ("layout", "target", "result"),
    [
        ("panel", "field", ["open", "open", "closed"]),
        ("panel", "rebuilt", ["open", "open", "closed"]),
        ("popup", "field", ["closed", "closed", "closed"]),
    ],
    ids=["panel: a field below it", "panel: Delete, which rebuilds its row", "popup: a field below the picture"],
)
def test_a_tap_below_a_panel_acts_before_the_panel_closes(layout: str, target: str, result: list) -> None:
    out = _panel(
        "view = layout === 'panel' ? { w: 358, h: 201 } : { w: 960, h: 540 }; openPointMenu(2); var seen = [];"
        " function state() { seen.push(S.pinMenu ? 'open' : 'closed'); }"
        " var t = Object.assign({}, away);"
        " listeners.push(['page', 'click', function() { state();"
        " if (target === 'rebuilt') t.isConnected = false; }, false]);"
        # The press focuses the control, then its click acts.
        " dispatch('page', 'pointerdown', t); fire('document', 'focusin', { target: t }); state();"
        " dispatch('page', 'click', t); state(); return seen;",
        layout=layout,
        target=target,
    )
    # Closed by the focus, a panel moves the control up from under the finger, and the tap's click is lost.
    assert out == result


def test_tab_out_of_a_panel_still_closes_it() -> None:
    out = _panel(
        "view = { w: 358, h: 201 }; openPointMenu(2); dispatch('pin-menu', 'pointerdown', item({ pm: 'none' }));"
        " dispatch('pin-menu', 'click', item({ pm: 'none' })); var open = !!S.pinMenu;"
        " dispatch('page', 'keydown', away, { key: 'Tab' }); fire('document', 'focusin', { target: away });"
        " return [open, S.pinMenu];"
    )
    assert out == [True, None]


def test_a_change_after_the_menu_closed_is_ignored() -> None:
    out = run_wizard_js(
        "(function() { var e = { target: { id: 'pm-x' } }, seen = [];"
        " S.pinMenu = { form: 'known' }; seen.push(pinMenuCheck(e)); S.pinMenu = null; seen.push(pinMenuCheck(e));"
        " return seen; })()",
        functions=("pinMenuCheck", "pinMenuVals"),
        prelude="var S = {}, els = { 'pm-ok': {} }; function $(id) { return els[id] || { value: '1' }; }"
        " function len(t) { return +t; } function refreshEchoes() {} function pointOptions() { return ''; }",
    )
    # A field's late change event finds no menu: nothing to check, and no error.
    assert out == [True, False]


@pytest.mark.parametrize(
    ("key", "result"),
    [("Escape", [None, None, ["pin-handle"], True, False, 0]), ("Enter", [None, None, ["pin-handle"], True, True, 1])],
    ids=["Esc", "Enter"],
)
def test_ending_a_corners_hold_ends_its_selection_and_keeps_its_focus(key: str, result: list) -> None:
    esc_key = wizard_block("document.addEventListener('keydown', function(e) {\n    if (e.key !== 'Escape') return;")
    out = run_wizard_js(
        "(function() { eval(escKey); var e = { key: key, preventDefault: function() {} };"
        " if (key === 'Escape') onKey(e); cornerEnterKey(e); cornerEnterKey(e);"
        " return [S.selCorner, S.selPin, handle.classes, document.activeElement === handle, S.cornerMoved.DSR, solves];"
        " })()",
        functions=("dropCorner", "cornerEnterKey"),
        prelude="var onKey, solves = 0;"
        " var handle = { classes: ['pin-handle', 'active'], classList: { remove: function(c) {"
        " handle.classes = handle.classes.filter(function(x) { return x !== c; }); } } };"
        " var document = { activeElement: handle, body: { classList: { contains: function() { return false; } } },"
        " addEventListener: function(t, f) { onKey = f; },"
        " querySelector: function(sel) { return sel === '#cal-pins [data-mark=\"DSR\"]' ? handle : null; } };"
        " function $() { return { hidden: true }; }"
        " var FS = null, lensHold = false, lensPending = null, currentStep = 2, WIZ = { lens: 1 };"
        " var S = { mode: 'simple', pinMenu: null, selCorner: 'DSR', selPin: 'DSR', cornerMoved: { DSR: false } };"
        " function hideLoupe() {} function solveSoon() { solves++; } function saveToSession() {}",
        key=key,
        escKey=esc_key,
    )
    # No longer drawn as selected once Enter would not place it; Esc places nothing, Enter places it once.
    assert out == result


# ---------------------------------------------------------------------------
# The lens centre through the Lens step: the result line, Load, Save, Review and the session
# ---------------------------------------------------------------------------

# A centre 42 px right and 18 px up on 1920 x 1080, in image axes.
_C42 = [42 / _D, -18 / _D]
_LINE_FUNCTIONS = ("esc", "fmt", "setLive", "lensFitApplies", "lensFitMisfit", "lensSame", "lensCentreSuffix")
_GOOD = "Good \u2013 barrel / fisheye \u22120.198, corner correction \u22120.071"
_RESULT_PAGE = """
var imageWidth = 1920, imageHeight = 1080, lensLines = [];
function renderLensMeter() {} function lensIsTheFits() { return true; } function wizLens() { return [0, 0, 0, 0]; }
"""


@pytest.mark.parametrize(
    ("centre", "text"),
    [
        (_C42, _GOOD + ", lens centre 42 px right, 18 px up"),
        ([0.0, 0.0], _GOOD),
        ([0.4 / _D, _C42[1]], _GOOD + ", lens centre 18 px up"),
    ],
    ids=["a centre set", "no centre", "an axis under half a pixel"],
)  # fmt: skip
def test_the_result_line_names_the_lens_centre_the_fit_held(centre: list, text: str) -> None:
    out = run_wizard_js(
        "(function() { renderLensResult(); return $('lens-result-text').innerHTML; })()",
        functions=(
            *_LINE_FUNCTIONS,
            *_CENTRE_PX,
            "lensMiddleOnly",
            "lensShownRating",
            "lensHandTuned",
            "renderLensResult",
        ),
        variables=("LENS_RATING_LEVEL", "LENS_RATING_LABEL"),
        prelude=_DOM
        + _RESULT_PAGE
        + "document.getElementById = function(id) { var el = $(id); el.querySelector = function() { return $('m'); };"
        " return el; };"
        + "var S = { lensLoaded: null }, lensFit = "
        + json.dumps({"rating": "good", "k1": -0.198, "k2": -0.071, "k2_fitted": True, "hint": "", "centre": centre})
        + ";",
        LENS_C_BOUND=C_BOUND,
    )
    assert out == text


@pytest.mark.parametrize(
    ("k2_fitted", "text"),
    [
        (True, "Good \u2013 barrel / fisheye \u22120.198, corner correction \u22120.183"),
        (False, "Good \u2013 barrel / fisheye \u22120.198, corner correction \u22120.183 (estimated)"),
    ],
    ids=["measured", "left open by the lines"],
)
def test_a_corner_correction_the_lines_left_open_reads_as_estimated(k2_fitted: bool, text: str) -> None:
    out = run_wizard_js(
        "(function() { renderLensResult(); return $('lens-result-text').innerHTML; })()",
        functions=(
            *_LINE_FUNCTIONS,
            *_CENTRE_PX,
            "lensMiddleOnly",
            "lensShownRating",
            "lensHandTuned",
            "renderLensResult",
        ),
        variables=("LENS_RATING_LEVEL", "LENS_RATING_LABEL"),
        prelude=_DOM
        + _RESULT_PAGE
        + "document.getElementById = function(id) { var el = $(id); el.querySelector = function() { return $('m'); };"
        " return el; };"
        + "var S = { lensLoaded: null }, lensFit = "
        + json.dumps(
            {"rating": "good", "k1": -0.198, "k2": -0.183, "k2_fitted": k2_fitted, "hint": "", "centre": [0.0, 0.0]}
        )
        + ";",
        LENS_C_BOUND=C_BOUND,
    )
    assert out == text


_MIDDLE_RESULT = (
    "The lines stay near the middle of the picture, so its edges are not measured. That is fine if you only need the"
    " middle. To measure the full image, add lines near the edges."
)
_MORE_LINES = "More lines near the edges give a better rating."
_PAIR_TEXT = " \u2013 barrel / fisheye \u22120.310, corner correction \u22120.020"
_TOP_HINT = "If possible, add a line near the top edge to confirm it."


@pytest.mark.parametrize(
    ("rating", "edges", "text", "hint", "shown", "box"),
    [
        ("good", False, "Fair, but might already be good in the middle" + _PAIR_TEXT, _MIDDLE_RESULT, "okay", "notice"),
        ("excellent", False, "Fair, but might already be good in the middle" + _PAIR_TEXT, _MIDDLE_RESULT, "okay",
         "notice"),
        ("good", True, "Good" + _PAIR_TEXT, "", "good", "notice success"),
        ("good", None, "Good" + _PAIR_TEXT, "", "good", "notice success"),
        ("okay", False, "Fair" + _PAIR_TEXT, "", "okay", "notice"),
        ("low", False, "Not usable" + _PAIR_TEXT + " \u2013 not applied", _MORE_LINES, "low", "notice warning"),
    ],
    ids=["good, only the middle", "excellent, only the middle", "the edges measured", "an answer without the field",
         "fair, only the middle", "not usable, only the middle"],
)  # fmt: skip
def test_a_good_fit_whose_lines_stay_in_the_middle_is_shown_as_fair(
    rating: str, edges: bool | None, text: str, hint: str, shown: str, box: str
) -> None:
    fit = {"rating": rating, "k1": -0.31, "k2": -0.02, "k2_fitted": True, "centre": [0.0, 0.0], "hint": _TOP_HINT}
    if edges is not None:
        fit["edges_measured"] = edges
    out = run_wizard_js(
        "(function() { renderLensResult(); return [$('lens-result-text').innerHTML, $('lens-result-hint').innerHTML,"
        " meter, $('lens-result').className]; })()",
        functions=(
            *_LINE_FUNCTIONS,
            *_CENTRE_PX,
            "lensMiddleOnly",
            "lensShownRating",
            "lensHandTuned",
            "renderLensResult",
        ),
        variables=("LENS_RATING_LEVEL", "LENS_RATING_LABEL", "LENS_MIDDLE"),
        prelude=_DOM
        + _RESULT_PAGE
        + "var meter = null; renderLensMeter = function(m, r) { meter = r; };"
        + "document.getElementById = function(id) { var el = $(id); el.querySelector = function() { return $('m'); };"
        " return el; };" + f"var S = {{ lensLoaded: null }}, lensFit = {json.dumps(fit)};",
        LENS_C_BOUND=C_BOUND,
    )
    # The saved lens is the fit's whatever it is shown as: only the words, the meter and the colour change.
    assert out == [text, hint, shown, box]


def test_one_line_asks_for_a_second_instead_of_a_rating() -> None:
    out = run_wizard_js(
        "(function() { renderLensResult(); return [$('lens-result').style.display, $('lens-result').className,"
        " $('lens-result-text').innerHTML, $('lens-result-hint').innerHTML, meter, $('m').dataset.level]; })()",
        functions=(
            *_LINE_FUNCTIONS,
            *_CENTRE_PX,
            "lensMiddleOnly",
            "lensShownRating",
            "lensHandTuned",
            "renderLensResult",
        ),
        variables=("LENS_RATING_LEVEL", "LENS_RATING_LABEL", "LENS_MIDDLE"),
        prelude=_DOM
        + _RESULT_PAGE
        + "var meter = null; renderLensMeter = function(m, r) { meter = r; }; lensLines = [{ misfit: false }];"
        + " $('m').dataset = { level: 'success' };"
        + "document.getElementById = function(id) { var el = $(id); el.querySelector = function() { return $('m'); };"
        " return el; }; var S = { lensLoaded: null }, lensFit = null;",
        LENS_C_BOUND=C_BOUND,
    )
    assert out == ["", "notice", "Draw at least two lines.", "", "", ""]


@pytest.mark.parametrize(("lines", "fitted"), [(1, False), (2, True)])
def test_the_lens_is_fitted_from_two_lines(lines: int, fitted: bool) -> None:
    out = run_wizard_js(
        "(function() { runLensFit(); return [sent !== null, lensFit]; })()",
        functions=(*_LENS_MODEL, "wizLensShownPair", "lensFitApplies", "lensFitMisfit", "runLensFit"),
        prelude=_ANSWER_FETCH + _FIT_PAGE + " var sent = null;"
        " function wizWriteLensCoeff() {} function wizShowLensLimit() {} function onLensCoeffChanged() {}"
        f" lensLines = lensLines.slice(0, {lines});",
        LENS_C_BOUND=C_BOUND,
    )
    assert out[0] is fitted
    assert (out[1] is not None) is fitted


def test_the_loaded_line_names_the_centre_in_use() -> None:
    out = run_wizard_js(
        "(function() { renderLensResult();"
        " return [$('lens-result-text').innerHTML, $('lens-result-hint').innerHTML]; })()",
        functions=(
            *_LINE_FUNCTIONS,
            *_CENTRE_PX,
            "lensMiddleOnly",
            "lensShownRating",
            "lensHandTuned",
            "renderLensResult",
        ),
        variables=("LENS_RATING_LEVEL", "LENS_RATING_LABEL"),
        prelude=_DOM
        + _RESULT_PAGE
        + "document.getElementById = function(id) { var el = $(id); el.querySelector = function() { return $('m'); };"
        " return el; };"
        + f"var lensFit = null, S = {{ lensLoaded: {{ name: 'Wide', src: 'Saved on this station on 2026-10-14',"
        f" k1: -0.2, k2: -0.07, cx: {_C42[0]}, cy: {_C42[1]} }} }};"
        + f" wizLens = function() {{ return [-0.2, -0.07, {_C42[0]}, 5 / {_D}]; }};",
        LENS_C_BOUND=C_BOUND,
    )
    # The centre moved by hand after loading: the line names the one in use and says it was adjusted.
    assert out == [
        "Loaded “Wide” – barrel / fisheye −0.200, corner correction −0.070, lens centre 42 px right, 5 px down",
        "Adjusted by hand after loading. Saved on this station on 2026-10-14.",
    ]


_LOAD_PAGE = """
var imageWidth = 1920, imageHeight = 1080, lensLines = [], lensSelected = null, lensPending = null, lensFit = null;
var lensFitSeq = 0, written = {};
var S = { lensCentre: [0, 0], lensCentreBad: true, lensTouched: false, lensLoaded: null };
function lensReleaseCandidate() {} function dropLensPoint() {} function lensShowNotice() {} function renderLens() {}
function renderLensResult() {} function onLensCoeffChanged() {} function wizShowLensLimit() {}
function wizWriteLensCoeff(id, v) { written[id] = v; }
"""


def test_loading_a_calibration_sets_its_centre_and_shows_it_in_pixels() -> None:
    out = run_wizard_js(
        "(function() { lensApplyLoaded(c, 'Saved on this station on 2026-10-14');"
        " return [written, S.lensCentre, S.lensCentreBad, S.lensLoaded, $('wiz_lens_cx').value, $('wiz_lens_cy').value,"
        " $('wiz_lens_cx').max, $('wiz_lens_cy').min]; })()",
        functions=(
            *_LENS_MODEL,
            "wizLensShownPair",
            *_CENTRE_PX,
            "wizShowLensCentre",
            "wizSetLensCentre",
            "lensApplyLoaded",
        ),
        prelude=_DOM + _LOAD_PAGE,
        LENS_C_BOUND=C_BOUND,
        c={"name": "Wide", "k1": -0.2, "k2": -0.07, "cx": _C42[0], "cy": _C42[1]},
    )
    assert out[0] == {"wiz_lens_k1": "-0.2000", "wiz_lens_k2": "-0.0700"}
    assert out[1] == pytest.approx(_C42) and out[2] is False
    assert out[3] == {"name": "Wide", "src": "Saved on this station on 2026-10-14", "k1": -0.2, "k2": -0.07,
                      "cx": pytest.approx(_C42[0]), "cy": pytest.approx(_C42[1])}  # fmt: skip
    assert out[4:] == [42, 18, 275, -275]


def test_without_a_snapshot_the_centre_fields_are_off_and_say_so() -> None:
    out = run_wizard_js(
        "(function() { wizSetLensCentre(c); return [$('wiz_lens_cx').disabled, $('wiz_lens_cx').placeholder,"
        " $('wiz_lens_cy').value, S.lensCentre]; })()",
        functions=(*_CENTRE_PX, "wizShowLensCentre", "wizSetLensCentre"),
        prelude=_DOM + "var imageWidth = 0, imageHeight = 0, S = { lensCentre: [0, 0], lensCentreBad: false };",
        LENS_C_BOUND=C_BOUND,
        c=_C42,
    )
    # The centre is kept in half-diagonals, so a snapshot arriving later shows it in its own pixels.
    assert out[:3] == [True, "No video", ""] and out[3] == pytest.approx(_C42)


def test_the_load_list_names_a_saved_lens_centre() -> None:
    saved = [
        {"name": "Wide", "k1": -0.2, "k2": -0.07, "cx": _C42[0], "cy": -_C42[1], "saved": "2026-10-14",
         "width": 1920, "height": 1080},
        {"name": "Tele", "k1": -0.05, "k2": 0.0, "cx": 0.0, "cy": 0.0, "saved": "2026-10-02",
         "width": 1920, "height": 1080},
    ]  # fmt: skip
    out = run_wizard_js(
        "(async function() { lensLoadDialog(); await new Promise(function(r) { setImmediate(r); }); return body; })()",
        functions=(*_DIALOG_FUNCTIONS, *_CENTRE_PX),
        prelude=_SAVE_ERROR
        + _DOM
        + _DIALOG_PAGE
        + "function fetch() { return Promise.resolve({ ok: true, json: function() {"
        + f" return Promise.resolve({{ calibrations: {json.dumps(saved)} }}); }} }}); }}\n"
        + wizard_window_function("lensLoadDialog")
        + "\nvar lensLoadDialog = window.lensLoadDialog;",
        LENS_C_BOUND=C_BOUND,
    )
    assert (
        "<small>Barrel / fisheye −0.200 · Corner correction −0.070 · Lens centre 42 px right, 18 px down"
        " · 2026-10-14 · 1920 × 1080</small>" in out
    )
    assert "<small>Barrel / fisheye −0.050 · Corner correction 0.000 · 2026-10-02 · 1920 × 1080</small>" in out


def test_save_sends_the_lens_centre_with_the_pair() -> None:
    out = run_wizard_js(
        "(async function() { lensSaveDialog(); await new Promise(function(r) { setImmediate(r); });"
        " $('ls-name').value = 'Wide'; buttons[1].onClick({ disabled: false });"
        " await new Promise(function(r) { setImmediate(r); }); return [body, posted]; })()",
        functions=(*_DIALOG_FUNCTIONS, *_CENTRE_PX),
        prelude=_SAVE_ERROR
        + _DOM
        + _DIALOG_PAGE
        + f"function wizLens() {{ return [-0.2, -0.07, {_C42[0]}, {_C42[1]}]; }}"
        + " var buttons = null, posted = null;"
        + " openModal = function(o) { body = o.bodyHTML; buttons = o.footerButtons; }; function closeModal() {}"
        + " function fetch(url, opts) {"
        + " if (opts) { posted = JSON.parse(opts.body); return Promise.resolve({ ok: true }); }"
        + " return Promise.resolve({ ok: true, json: function() {"
        + " return Promise.resolve({ calibrations: [] }); } }); }\n"
        + wizard_window_function("lensSaveDialog")
        + "\nvar lensSaveDialog = window.lensSaveDialog;",
        LENS_C_BOUND=C_BOUND,
    )
    assert "Barrel / fisheye −0.200 · Corner correction −0.070 · Lens centre 42 px right, 18 px up" in out[0]
    assert out[1] == {"name": "Wide", "k1": -0.2, "k2": -0.07, "cx": pytest.approx(_C42[0]),
                      "cy": pytest.approx(_C42[1]), "width": 1920, "height": 1080}  # fmt: skip


@pytest.mark.parametrize(("centre", "row"), [(_C42, ["Lens centre", "42 px right, 18 px up"]), ([0.0, 0.0], None)])
def test_review_names_the_lens_centre_in_use(centre: list, row: list | None) -> None:
    out = run_wizard_js(
        "(function() { renderReview(); return $('review-lens').innerHTML; })()",
        functions=(*_REVIEW_FUNCTIONS, *_CENTRE_PX),
        variables=("APPLY_MAX_PX",),
        prelude=_COUNTING_DOM
        + _REVIEW_STUBS
        + " imageWidth = 1920; imageHeight = 1080;"
        + f" function wizLens() {{ return [-0.2, -0.07, {centre[0]}, {centre[1]}]; }}"
        " fmt = function(v, d) { return Number(v).toFixed(d); };",
        LENS_C_BOUND=C_BOUND,
        S={"mode": "advanced", "cam": None, "solve": _REVIEW_SOLVED, "height": None},
        solvePending=False,
    )
    rows = re.findall(r'<span class="param-label">(.*?)</span><span class="param-value">(.*?)</span>', out)
    expected = [("Barrel / fisheye", "-0.200"), ("Corner correction", "-0.070")]
    assert rows == [*expected, *([tuple(row)] if row else []), ("Lens rating", "")]


def test_the_session_keeps_the_lens_centre() -> None:
    out = run_wizard_js(
        "(function() { S.lensCentre = c; saveToSession(); raw = JSON.stringify(stored); S.lensCentre = [0, 0];"
        " LENS_ENABLED = true; restoreFromSession(); return [stored.lensCentre, S.lensCentre]; })()",
        functions=("pairOk", "fracPinOk", "saveToSession", "restoreFromSession"),
        variables=("CORNERS",),
        prelude=length_page("metric", {}) + _DOM + _SESSION_PAGE + "var raw = null;"
        " var S = { mode: 'simple', simpleLoc: '', rect: { origin: 'dsc' }, pinSize: null, nextId: 1,"
        " cornerPins: { DSL: null, DSR: null, USR: null, USL: null },"
        " cornerMoved: { DSL: false, DSR: false, USR: false, USL: false }, points: [],"
        " lensTouched: true, lensLoaded: null };",
        c=_C42,
    )
    assert out == [pytest.approx(_C42), pytest.approx(_C42)]


@pytest.mark.parametrize(
    ("rating", "edges", "label", "level", "filled"),
    [
        ("good", False, "Fair", "info", 3),
        ("excellent", False, "Fair", "info", 3),
        ("good", True, "Good", "success", 4),
        ("medium", False, "Poor", "caution", 2),
    ],
)
def test_review_names_a_good_fit_on_middle_lines_fair(
    rating: str, edges: bool, label: str, level: str, filled: int
) -> None:
    out = run_wizard_js(
        "reviewLensCoverage([0, 0, 0, 0])",
        functions=(
            "lensMiddleOnly",
            "lensShownRating",
            "lensFitApplies",
            "lensFitMisfit",
            "lensHandTuned",
            "reviewLensCoverage",
        ),
        variables=("LENS_RATING_LEVEL", "LENS_RATING_SEGMENTS", "LENS_RATING_LABEL"),
        prelude="function lensIsTheFits() { return true; }"
        f" var lensFit = {{ rating: {json.dumps(rating)}, edges_measured: {json.dumps(edges)} }};",
    )
    assert out.endswith("</span> " + label)
    assert f'data-level="{level}"' in out
    assert out.count("wizard-meter-seg filled") == filled


_MISFIT_LINES = [{"misfit": False}, {"misfit": True}, {"misfit": False}]


@pytest.mark.parametrize(
    ("rating", "lines", "text", "hint", "level"),
    [
        ("good", _MISFIT_LINES, "Good" + _PAIR_TEXT + " \u2013 not applied",
         "It is applied once the line that does not fit is corrected or deleted.", "caution"),
        ("okay", _MISFIT_LINES, "Fair" + _PAIR_TEXT + " \u2013 not applied",
         "It is applied once the line that does not fit is corrected or deleted.", "caution"),
        ("low", _MISFIT_LINES, "Not usable" + _PAIR_TEXT + " \u2013 not applied",
         "It is applied once the line that does not fit is corrected or deleted.", "caution"),
        ("good", [{"misfit": False}] * 3, "Good" + _PAIR_TEXT, "", "success"),
    ],
    ids=["good, a line does not fit", "fair, a line does not fit", "not usable, a line does not fit",
         "every line fits"],
)  # fmt: skip
def test_a_fit_with_a_line_that_does_not_fit_is_not_applied(
    rating: str, lines: list, text: str, hint: str, level: str
) -> None:
    fit = {"rating": rating, "k1": -0.31, "k2": -0.02, "k2_fitted": True, "centre": [0.0, 0.0], "hint": _TOP_HINT,
           "lines": lines}  # fmt: skip
    out = run_wizard_js(
        "(function() { renderLensResult(); return [$('lens-result-text').innerHTML, $('lens-result-hint').innerHTML,"
        " lensFitApplies(lensFit), meterLevel, $('lens-result').className]; })()",
        functions=(
            *_LINE_FUNCTIONS,
            "lensFitMisfit",
            *_CENTRE_PX,
            "lensMiddleOnly",
            "lensShownRating",
            "lensHandTuned",
            "renderLensResult",
        ),  # fmt: skip
        variables=("LENS_RATING_LEVEL", "LENS_RATING_LABEL", "LENS_MIDDLE"),
        prelude=_DOM
        + _RESULT_PAGE
        + "var meterLevel = null; renderLensMeter = function(m, r, level) { meterLevel = level; };"
        + "document.getElementById = function(id) { var el = $(id); el.querySelector = function() { return $('m'); };"
        " return el; };" + f"var S = {{ lensLoaded: null }}, lensFit = {json.dumps(fit)};",
        LENS_C_BOUND=C_BOUND,
    )
    # The coverage hint is the hint box's: the result's own line never repeats it. Not applied is a caution.
    box = {"caution": "notice warning", "success": "notice success"}[level]
    assert out == [text, hint, hint == "", level, box]


@pytest.mark.parametrize("misfit", [True, False], ids=["a line does not fit", "every line fits"])
def test_a_fit_with_a_line_that_does_not_fit_keeps_the_lens_as_it_was(misfit: bool) -> None:
    out = run_wizard_js(
        "(function() { runLensFit(); return [written, lensFit.kept || null]; })()",
        functions=(*_LENS_MODEL, "wizLensShownPair", "lensFitApplies", "lensFitMisfit", "runLensFit"),
        prelude=_ANSWER_FETCH.replace("misfit: false }]", f"misfit: {json.dumps(misfit)} }}]")
        + _FIT_PAGE
        + " var written = {}; function wizWriteLensCoeff(id, v) { written[id] = v; } function wizShowLensLimit() {}"
        " function onLensCoeffChanged() {} function wizLens() { return [-0.2, 0, 0, 0]; }",
        LENS_C_BOUND=C_BOUND,
    )
    assert out == ([{}, [-0.2, 0, 0, 0]] if misfit else [{"wiz_lens_k1": "-0.3123", "wiz_lens_k2": "-0.0123"}, None])


@pytest.mark.parametrize(
    ("fit", "lens", "touched", "label"),
    [
        ({"rating": "good", "lines": _MISFIT_LINES, "kept": [-0.2, 0, 0, 0]}, [-0.2, 0, 0, 0], False,
         "Good, not applied"),
        ({"rating": "low", "lines": [], "kept": [-0.2, 0, 0, 0]}, [-0.2, 0, 0, 0], False, "Not usable, not applied"),
        # Edited by hand after a fit that was not applied: the lens is the operator's.
        ({"rating": "good", "lines": _MISFIT_LINES, "kept": [-0.2, 0, 0, 0]}, [-0.25, 0, 0, 0], True,
         "Adjusted by hand"),
    ],
    ids=["a line does not fit", "not usable", "edited after"],
)  # fmt: skip
def test_review_names_a_fit_that_was_not_applied(fit: dict, lens: list, touched: bool, label: str) -> None:
    out = run_wizard_js(
        f"reviewLensCoverage({json.dumps(lens)})",
        functions=(
            "lensMiddleOnly",
            "lensShownRating",
            "lensFitApplies",
            "lensFitMisfit",
            "lensSame",
            "lensHandTuned",
            "reviewLensCoverage",
        ),  # fmt: skip
        variables=("LENS_RATING_LEVEL", "LENS_RATING_SEGMENTS", "LENS_RATING_LABEL"),
        prelude="function lensIsTheFits() { return false; } function esc(t) { return t; }"
        f" function wizLens() {{ return {json.dumps(lens)}; }} function lensMeasured() {{ return true; }}"
        f" var S = {{ lensLoaded: null, lensTouched: {json.dumps(touched)} }}, lensFit = {json.dumps(fit)};",
    )
    assert out.endswith(label) and ("wizard-meter" in out) is (label != "Adjusted by hand")
    # A fit that is not applied is a caution in Review too, whatever its rating.
    if label != "Adjusted by hand":
        assert 'data-level="caution"' in out
