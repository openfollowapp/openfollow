# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Reading OpenCV lens calibrations and fitting the lens model to them."""

from __future__ import annotations

import math

import numpy as np
import numpy.typing as npt
import pytest

from openfollow.lens_model import C_BOUND, lens_warp_is_valid, pull_back_to_valid
from openfollow.scene import opencv_lens
from openfollow.scene.opencv_lens import (
    MAX_FILE_BYTES,
    MIN_COVERAGE,
    OpenCvLensError,
    OpenCvLensFit,
    OpenCvSizeUnknown,
    fit_opencv_lens,
    parse_opencv_calibration,
)
from openfollow.scene.solver import apply_overlay_distortion
from tests._opencv_files import opencv_matrix, opencv_xml

pytestmark = pytest.mark.unit

Floats = npt.NDArray[np.floating]
_W, _H = 1920, 1080


# OpenCV's two models, written out from its documentation, apart from the module's own.
def _opencv_distort(x: Floats, y: Floats, dist: list[float], fisheye: bool) -> tuple[Floats, Floats]:
    r = np.hypot(x, y)
    if fisheye:
        k1, k2, k3, k4 = dist
        th = np.arctan(r)
        thd = th * (1 + k1 * th**2 + k2 * th**4 + k3 * th**6 + k4 * th**8)
        s = np.where(r > 0, thd / np.where(r > 0, r, 1), 1.0)
        return x * s, y * s
    k1, k2, p1, p2, k3, k4, k5, k6, s1, s2, s3, s4 = (list(dist) + [0.0] * 12)[:12]
    r2 = r * r
    radial = (1 + k1 * r2 + k2 * r2**2 + k3 * r2**3) / (1 + k4 * r2 + k5 * r2**2 + k6 * r2**3)
    return (
        x * radial + 2 * p1 * x * y + p2 * (r2 + 2 * x * x) + s1 * r2 + s2 * r2**2,
        y * radial + p1 * (r2 + 2 * y * y) + 2 * p2 * x * y + s3 * r2 + s4 * r2**2,
    )


def _screen_error(text: str, fit: OpenCvLensFit, width: float = _W, height: float = _H) -> tuple[float, float]:
    """RMS and largest screen distance (px) between the file's lens and the fitted one over the picture.

    Pinhole points are mapped by the file's model; the fitted lens's pinhole scale is free, as the
    camera solve owns it, and is taken where it fits best.
    """
    cal = parse_opencv_calibration(text)
    size = (cal.width, cal.height) if cal.width else (_W, _H)
    k = width / size[0]
    fx, fy, ox, oy = cal.fx * k, cal.fy * k, (cal.cx + 0.5) * k, (cal.cy + 0.5) * k
    g = np.linspace(-2.0, 2.0, 161)
    x, y = (a.ravel() for a in np.meshgrid(g, g))
    xd, yd = _opencv_distort(x, y, list(cal.coeffs), cal.fisheye)
    seen = np.column_stack([fx * xd + ox, fy * yd + oy])
    # Only where the model still rises along the ray: past that it describes no screen point.
    rr = np.linspace(1e-6, 2.9, 3000)
    rd, _ = _opencv_distort(rr, 0 * rr, list(cal.coeffs), cal.fisheye)
    fold = rr[np.argmax(np.diff(rd) <= 0)] if np.any(np.diff(rd) <= 0) else math.inf
    keep = (
        (seen[:, 0] >= 0) & (seen[:, 0] <= width) & (seen[:, 1] >= 0) & (seen[:, 1] <= height) & (np.hypot(x, y) < fold)
    )
    pinhole = np.column_stack([fx * x[keep], fy * y[keep]])
    # Each sample weighs the screen area its pinhole cell covers, so the RMS is over the picture, not the grid.
    eps = 1e-6
    ax, ay = _opencv_distort(x[keep] + eps, y[keep], list(cal.coeffs), cal.fisheye)
    bx, by = _opencv_distort(x[keep], y[keep] + eps, list(cal.coeffs), cal.fisheye)
    weight = np.abs((ax - xd[keep]) * (by - yd[keep]) - (bx - xd[keep]) * (ay - yd[keep])) / eps**2
    best = (math.inf, math.inf)
    for s in np.linspace(0.85, 1.15, 301):
        ours = apply_overlay_distortion(
            np.array([width / 2, height / 2]) + s * pinhole, width, height, fit.k1, fit.k2, fit.cx, fit.cy
        )
        err = np.hypot(*(ours - seen[keep]).T)
        rms = float(np.sqrt(np.sum(weight * err**2) / np.sum(weight)))
        if rms < best[0]:
            best = (rms, float(err.max()))
    return best


# ---------------------------------------------------------------------------
# Reading the file
# ---------------------------------------------------------------------------


def test_a_calibration_file_reads_its_matrix_distortion_and_size() -> None:
    dist = [-0.415, 0.163, 0.001, -0.002, -0.029]
    cal = parse_opencv_calibration(opencv_xml(1134.3, (969.45, 560.05), dist, fy=1135.0))
    assert (cal.fx, cal.fy, cal.cx, cal.cy) == (1134.3, 1135.0, 969.45, 560.05)
    assert cal.coeffs == (-0.415, 0.163, 0.001, -0.002, -0.029)
    assert (cal.fisheye, cal.width, cal.height) == (False, 1920, 1080)


@pytest.mark.parametrize(("flag", "fisheye"), [("1", True), ("0", False), (None, False)])
def test_the_fisheye_flag_picks_the_model(flag: str | None, fisheye: bool) -> None:
    dist = [-0.05, 0.01, -0.006, 0.001] if fisheye else [-0.1, 0.02, 0.0, 0.0, 0.01]
    assert parse_opencv_calibration(opencv_xml(dist=dist, fisheye=flag)).fisheye is fisheye


def test_the_fisheye_flag_reads_under_the_name_opencv_s_sample_program_writes() -> None:
    text = opencv_xml(dist=[-0.05, 0.01, -0.006, 0.001], extra="<fisheye_model>1</fisheye_model>")
    assert parse_opencv_calibration(text).fisheye is True


def test_four_values_marked_not_fisheye_are_the_standard_model() -> None:
    cal = parse_opencv_calibration(opencv_xml(dist=[-0.1, 0.02, 0.001, 0.0], fisheye="0"))
    assert (cal.fisheye, cal.coeffs) == (False, (-0.1, 0.02, 0.001, 0.0))


def test_the_lens_centre_offset_is_added_to_the_principal_point() -> None:
    cal = parse_opencv_calibration(opencv_xml(c=(961.52, 541.52), offset=[-102.4, 88.6]))
    assert (cal.cx, cal.cy) == pytest.approx((961.52 - 102.4, 541.52 + 88.6))


@pytest.mark.parametrize(
    ("matrix_key", "dist_key"),
    [("camera_matrix", "distortion_coefficients"), ("cameraMatrix", "distCoeffs")],
)
def test_the_other_key_names_of_calibration_tools_are_read(matrix_key: str, dist_key: str) -> None:
    cal = parse_opencv_calibration(opencv_xml(1200.0, matrix_key=matrix_key, dist_key=dist_key))
    assert (cal.fx, cal.coeffs) == (1200.0, (-0.1, 0.02, 0.0, 0.0, 0.0))


def test_the_size_reads_from_image_width_and_height_too() -> None:
    cal = parse_opencv_calibration(opencv_xml(size=(3840, 2160), size_keys="image"))
    assert (cal.width, cal.height) == (3840, 2160)


def test_a_file_without_a_size_says_so() -> None:
    cal = parse_opencv_calibration(opencv_xml(size=None))
    assert (cal.width, cal.height) == (None, None)


@pytest.mark.parametrize("count", [5, 8, 12, 14])
def test_every_standard_model_size_reads(count: int) -> None:
    dist = [-0.1, 0.02] + [0.0] * (count - 2)
    assert len(parse_opencv_calibration(opencv_xml(dist=dist)).coeffs) == count


_MATRIX = opencv_matrix("cameraMatrix", 3, 3, [1500, 0, 959.5, 0, 1500, 539.5, 0, 0, 1])
_DIST = opencv_matrix("dist_coeffs", 1, 5, [-0.1, 0.02, 0, 0, 0])


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("%YAML:1.0\n---\ncameraMatrix: !!opencv-matrix\n", "Only XML calibration files can be read."),
        ('{"camera": {}}', "Only XML calibration files can be read."),
        ("", "Only XML calibration files can be read."),
        ('<!DOCTYPE x [<!ENTITY a "b">]><opencv_storage/>', "declares a document type"),
        ("<opencv_storage><!ENTITY a 'b'></opencv_storage>", "declares a document type"),
        ("<opencv_storage><cameraMatrix>", "not a readable XML file"),
        (f"<calibration>{_MATRIX}{_DIST}</calibration>", "not an OpenCV calibration"),
        (f"<opencv_storage>{_DIST}</opencv_storage>", "has no camera matrix"),
        (f"<opencv_storage>{_MATRIX}</opencv_storage>", "has no distortion values"),
    ],
    ids=["yaml", "json", "empty", "doctype", "entity", "broken", "other root", "no matrix", "matrix only"],
)
def test_a_file_that_is_no_xml_calibration_is_refused(text: str, reason: str) -> None:
    with pytest.raises(OpenCvLensError, match=reason):
        parse_opencv_calibration(text)


def test_a_file_with_a_lone_surrogate_is_no_readable_file() -> None:
    with pytest.raises(OpenCvLensError, match="not a readable XML file"):
        parse_opencv_calibration(opencv_xml(extra="<note>\ud800</note>"))


def test_a_size_written_as_a_matrix_reads() -> None:
    text = opencv_xml(size=None, extra=opencv_matrix("cameraResolution", 1, 2, [3840, 2160]))
    assert (parse_opencv_calibration(text).width, parse_opencv_calibration(text).height) == (3840, 2160)


@pytest.mark.parametrize("tilt", [(0.05, 0.0), (0.0, -0.05)], ids=["tau x", "tau y"])
def test_a_tilted_sensor_is_refused(tilt: tuple[float, float]) -> None:
    dist = [-0.1, 0.02] + [0.0] * 10 + list(tilt)
    with pytest.raises(OpenCvLensError, match="tilted sensor"):
        parse_opencv_calibration(opencv_xml(dist=dist))


@pytest.mark.parametrize("lead", ["\n", "  \r\n", "\ufeff"], ids=["newline", "spaces", "byte order mark"])
def test_a_file_with_something_before_its_declaration_still_reads(lead: str) -> None:
    assert parse_opencv_calibration(lead + opencv_xml()).fx == 1500.0


def test_a_file_carrying_its_detected_points_reads() -> None:
    # Calibration tools may also write every view's detected points: tens to hundreds of kB.
    points = opencv_matrix("image_points", 60 * 54, 2, [i * 0.7071 % 1900 for i in range(60 * 54 * 2)])
    text = opencv_xml(extra=points)
    assert len(text.encode()) > 64 * 1024
    assert parse_opencv_calibration(text).fx == 1500.0


def test_a_file_past_the_size_cap_is_refused() -> None:
    with pytest.raises(OpenCvLensError, match="too large"):
        parse_opencv_calibration(opencv_xml(extra="<!-- " + "x" * MAX_FILE_BYTES + " -->"))


@pytest.mark.parametrize(
    ("matrix", "reason"),
    [
        (opencv_matrix("cameraMatrix", 2, 2, [1, 0, 0, 1]), "not 3 x 3"),
        (opencv_matrix("cameraMatrix", 3, 3, [1500, 0, 959.5, 0, 1500, 539.5, 0, 0]), "does not hold 3 x 3 values"),
        ("<cameraMatrix><rows>x</rows><cols>3</cols><data>1</data></cameraMatrix>", "has no valid size"),
        (opencv_matrix("cameraMatrix", 3, 3, [0, 0, 959.5, 0, 1500, 539.5, 0, 0, 1]), "not a camera's"),
        (opencv_matrix("cameraMatrix", 3, 3, [1500, 0, 959.5, 0, -1500, 539.5, 0, 0, 1]), "not a camera's"),
        (opencv_matrix("cameraMatrix", 3, 3, [1500, 2, 959.5, 0, 1500, 539.5, 0, 0, 1]), "not a camera's"),
        (opencv_matrix("cameraMatrix", 3, 3, [1500, 0, 959.5, 0, 1500, 539.5, 0, 1, 1]), "not a camera's"),
        (
            "<cameraMatrix><rows>3</rows><cols>3</cols><data>1500 0 abc 0 1500 539 0 0 1</data></cameraMatrix>",
            "not a number",
        ),
        (
            "<cameraMatrix><rows>3</rows><cols>3</cols><data>1500 0 nan 0 1500 539 0 0 1</data></cameraMatrix>",
            "not a finite number",
        ),
        (
            "<cameraMatrix><rows>3</rows><cols>3</cols><data>inf 0 959 0 1500 539 0 0 1</data></cameraMatrix>",
            "not a finite number",
        ),
    ],
    ids=[
        "2x2",
        "short",
        "rows not a number",
        "zero focal",
        "negative focal",
        "skew",
        "bottom row",
        "text",
        "nan",
        "inf",
    ],
)
def test_a_camera_matrix_that_is_no_camera_is_refused(matrix: str, reason: str) -> None:
    with pytest.raises(OpenCvLensError, match=reason):
        parse_opencv_calibration(f"<opencv_storage>{matrix}{_DIST}</opencv_storage>")


@pytest.mark.parametrize(
    ("dist", "flag", "reason"),
    [
        ([-0.1, 0.02, 0.0, 0.0], None, "does not say whether it is a fisheye calibration"),
        ([-0.1, 0.02, 0.0, 0.0, 0.0], "1", "A fisheye calibration has 4 distortion values, this one 5"),
        ([-0.1, 0.02, 0.0], None, "has 3 distortion values, which is no OpenCV model"),
        ([-0.1] * 6, "0", "has 6 distortion values"),
        ([-0.1, 0.02, 0.0, 0.0, 0.0], "2", "neither 0 nor 1"),
        ([-0.1, 0.02, 0.0, 0.0, 0.0], "yes", "not a number"),
    ],
    ids=["four bare", "fisheye five", "three", "six", "flag two", "flag text"],
)
def test_distortion_values_of_no_model_are_refused(dist: list[float], flag: str | None, reason: str) -> None:
    with pytest.raises(OpenCvLensError, match=reason):
        parse_opencv_calibration(opencv_xml(dist=dist, fisheye=flag))


@pytest.mark.parametrize(
    ("extra", "reason"),
    [
        (opencv_matrix("cxcy_offset", 1, 3, [1, 2, 3]), "lens centre offset is not two values"),
        ("<cameraResolution>1920 1080 3</cameraResolution>", "image size is not two whole numbers"),
        ("<cameraResolution>1920.5 1080</cameraResolution>", "image size is not two whole numbers"),
        ("<cameraResolution>0 1080</cameraResolution>", "image size is not two whole numbers"),
        ("<cameraResolution>99999 1080</cameraResolution>", "image size is not two whole numbers"),
    ],
    ids=["offset three", "size three", "size fraction", "size zero", "size huge"],
)
def test_a_malformed_offset_or_size_is_refused(extra: str, reason: str) -> None:
    with pytest.raises(OpenCvLensError, match=reason):
        parse_opencv_calibration(opencv_xml(size=None, extra=extra))


# ---------------------------------------------------------------------------
# Fitting the lens model
# ---------------------------------------------------------------------------


def test_a_calibration_without_distortion_gives_no_lens_about_its_centre() -> None:
    fit = fit_opencv_lens(parse_opencv_calibration(opencv_xml(c=(989.5, 519.5), dist=[0.0] * 5)), _W, _H)
    half_diag = 0.5 * math.hypot(_W, _H)
    assert (fit.k1, fit.k2) == pytest.approx((0.0, 0.0), abs=1e-6)
    assert (fit.cx, fit.cy) == pytest.approx((30.0 / half_diag, -20.0 / half_diag))
    assert (fit.rms_px, fit.corner_px, fit.coverage) == pytest.approx((0.0, 0.0, 1.0), abs=1e-3)


_LENSES = {
    "mild barrel": opencv_xml(1500.0, (959.5, 539.5), [-0.12, 0.03, 0.0, 0.0, 0.0]),
    "wide, off centre": opencv_xml(1134.0, (979.5, 559.5), [-0.36, 0.12, 0.0, 0.0, -0.01]),
    "tangential": opencv_xml(1500.0, (959.5, 539.5), [-0.12, 0.03, 0.004, -0.003, 0.0]),
    "fisheye": opencv_xml(1300.0, (950.5, 545.5), [-0.04, 0.008, -0.004, 0.0005], fisheye="1"),
    "4K": opencv_xml(3000.0, (1919.5, 1079.5), [-0.12, 0.03, 0.0, 0.0, 0.0], size=(3840, 2160)),
    "rational": opencv_xml(1500.0, (959.5, 539.5), [-0.1, 0.02, 0.0, 0.0, 0.0, 0.2, -0.05, 0.01]),
    "thin prism": opencv_xml(
        1500.0, (959.5, 539.5), [-0.12, 0.03, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.003, -0.001, -0.002, 0.0005]
    ),
}


@pytest.mark.parametrize("name", list(_LENSES))
def test_the_error_reported_is_the_one_the_fitted_lens_has(name: str) -> None:
    text = _LENSES[name]
    fit = fit_opencv_lens(parse_opencv_calibration(text), _W, _H)
    assert fit.rms_px == pytest.approx(_screen_error(text, fit)[0], rel=0.15, abs=0.15)


@pytest.mark.parametrize("name", ["mild barrel", "fisheye", "4K"])
def test_a_lens_the_model_can_follow_comes_out_within_a_fraction_of_a_pixel(name: str) -> None:
    text = _LENSES[name]
    rms, worst = _screen_error(text, fit_opencv_lens(parse_opencv_calibration(text), _W, _H))
    assert rms < 0.3 and worst < 1.0


@pytest.mark.parametrize("name", list(_LENSES))
def test_the_fitted_lens_is_valid_without_a_pull_back(name: str) -> None:
    fit = fit_opencv_lens(parse_opencv_calibration(_LENSES[name]), _W, _H)
    assert lens_warp_is_valid(fit.k1, fit.k2, fit.cx, fit.cy)
    assert pull_back_to_valid(fit.k1, fit.k2, fit.cx, fit.cy) == (fit.k1, fit.k2, fit.cx, fit.cy)


def test_tangential_terms_are_part_of_what_the_fit_follows() -> None:
    plain = fit_opencv_lens(parse_opencv_calibration(_LENSES["mild barrel"]), _W, _H)
    tangential = fit_opencv_lens(parse_opencv_calibration(_LENSES["tangential"]), _W, _H)
    assert tangential.rms_px > plain.rms_px + 0.5


def test_a_calibration_at_another_resolution_of_the_shape_gives_the_same_lens() -> None:
    full = fit_opencv_lens(parse_opencv_calibration(_LENSES["4K"]), _W, _H)
    half = fit_opencv_lens(parse_opencv_calibration(_LENSES["mild barrel"]), _W, _H)
    assert (full.k1, full.k2, full.cx, full.cy) == pytest.approx((half.k1, half.k2, half.cx, half.cy), abs=2e-3)
    assert (full.calibration_width, full.calibration_height, full.size_from_file) == (3840, 2160, True)


def test_a_lens_fitted_for_a_smaller_snapshot_reports_errors_in_its_pixels() -> None:
    cal = parse_opencv_calibration(_LENSES["wide, off centre"])
    full, small = fit_opencv_lens(cal, _W, _H), fit_opencv_lens(cal, _W / 2, _H / 2)
    assert (small.k1, small.k2) == pytest.approx((full.k1, full.k2), abs=2e-3)
    assert small.rms_px == pytest.approx(full.rms_px / 2, rel=0.1)


def test_a_wide_polynomial_that_folds_inside_the_picture_is_fitted_where_it_rises() -> None:
    # A wide lens whose calibration pattern did not reach the corners: its polynomial turns back before them.
    text = opencv_xml(1100.0, (959.5, 539.5), [-0.42, 0.16, 0.0, 0.0, -0.03])
    fit = fit_opencv_lens(parse_opencv_calibration(text), _W, _H)
    assert MIN_COVERAGE < fit.coverage < 0.97
    assert fit.corner_px is None
    assert lens_warp_is_valid(fit.k1, fit.k2, fit.cx, fit.cy)
    assert _screen_error(text, fit)[0] < 2.0


def test_a_fisheye_is_fitted_no_further_than_80_degrees_off_its_axis() -> None:
    # The corners of this picture lie between 80 and 90 degrees off the lens's axis.
    text = opencv_xml(850.0, (959.5, 539.5), [-0.05, 0.011, -0.011, 0.002], fisheye="1")
    fit = fit_opencv_lens(parse_opencv_calibration(text), _W, _H)
    assert 0.9 < fit.coverage < 1.0 and fit.corner_px is None
    assert lens_warp_is_valid(fit.k1, fit.k2, fit.cx, fit.cy)


def test_the_corner_error_waits_for_every_corner() -> None:
    # Off centre, the polynomial reaches the two near corners and turns back before the far ones.
    text = opencv_xml(1250.0, (1159.5, 539.5), [-0.42, 0.16, 0.0, 0.0, -0.03])
    fit = fit_opencv_lens(parse_opencv_calibration(text), _W, _H)
    assert fit.coverage < 1.0 and fit.corner_px is None


def test_a_camera_matrix_that_overflows_once_scaled_is_refused() -> None:
    cal = parse_opencv_calibration(opencv_xml(1.7e308, (479.5, 269.5), size=(960, 540)))
    with pytest.raises(OpenCvLensError, match="camera matrix is out of range"):
        fit_opencv_lens(cal, _W, _H)


def test_a_calibration_that_describes_little_of_the_picture_is_refused() -> None:
    text = opencv_xml(500.0, (959.5, 539.5), [-0.9, 0.3, 0.0, 0.0, 0.0])
    with pytest.raises(OpenCvLensError, match="describes too little of this picture"):
        fit_opencv_lens(parse_opencv_calibration(text), _W, _H)


def test_a_calibration_without_a_size_needs_one() -> None:
    cal = parse_opencv_calibration(opencv_xml(size=None))
    with pytest.raises(OpenCvSizeUnknown, match="does not say which image size"):
        fit_opencv_lens(cal, _W, _H)
    fit = fit_opencv_lens(cal, _W, _H, (1920, 1080))
    assert (fit.calibration_width, fit.calibration_height, fit.size_from_file) == (1920, 1080, False)


def test_the_size_in_the_file_wins_over_one_given() -> None:
    fit = fit_opencv_lens(parse_opencv_calibration(_LENSES["4K"]), _W, _H, (1920, 1080))
    assert (fit.calibration_width, fit.calibration_height) == (3840, 2160)


@pytest.mark.parametrize("size", [(1440, 1080), (1920, 1200)], ids=["4:3", "16:10"])
def test_a_calibration_of_another_picture_shape_is_refused_naming_both(size: tuple[int, int]) -> None:
    cal = parse_opencv_calibration(opencv_xml(size=size, c=(size[0] / 2 - 0.5, size[1] / 2 - 0.5)))
    with pytest.raises(
        OpenCvLensError, match=f"calibrated on a {size[0]} x {size[1]} picture, this one is 1920 x 1080"
    ):
        fit_opencv_lens(cal, _W, _H)


@pytest.mark.parametrize("axis", ["x", "y"])
def test_a_lens_centre_beyond_the_bound_is_refused(axis: str) -> None:
    half_diag = 0.5 * math.hypot(_W, _H)
    off = (C_BOUND + 0.01) * half_diag
    c = (959.5 + off, 539.5) if axis == "x" else (959.5, 539.5 - off)
    with pytest.raises(OpenCvLensError, match="lens centre is too far"):
        fit_opencv_lens(parse_opencv_calibration(opencv_xml(c=c)), _W, _H)


def test_the_corner_error_is_given_when_the_file_reaches_every_corner() -> None:
    fit = fit_opencv_lens(parse_opencv_calibration(_LENSES["wide, off centre"]), _W, _H)
    assert fit.coverage == 1.0 and fit.corner_px is not None and fit.corner_px >= fit.rms_px


def test_distortion_values_written_as_a_plain_list_read() -> None:
    text = f"<opencv_storage>{_MATRIX}<dist_coeffs>-0.1 0.02 0 0 0.01</dist_coeffs></opencv_storage>"
    assert parse_opencv_calibration(text).coeffs == (-0.1, 0.02, 0.0, 0.0, 0.01)


def test_a_fit_cut_short_by_its_step_budget_still_gives_a_valid_lens(monkeypatch: pytest.MonkeyPatch) -> None:
    cal = parse_opencv_calibration(_LENSES["wide, off centre"])
    full = fit_opencv_lens(cal, _W, _H)
    monkeypatch.setattr(opencv_lens, "_LM_ITERS", 1)
    cut = fit_opencv_lens(cal, _W, _H)
    assert lens_warp_is_valid(cut.k1, cut.k2, cut.cx, cut.cy)
    assert full.rms_px <= cut.rms_px < math.inf
