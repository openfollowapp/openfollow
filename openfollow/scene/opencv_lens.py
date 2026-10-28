# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Read an OpenCV lens calibration and fit the lens model to it.

An OpenCV ``FileStorage`` XML file carries a camera matrix and distortion
coefficients in pixels of the image it was calibrated on: the standard model
(``k1, k2, p1, p2, k3`` and the rational and thin-prism terms after them, never a tilted sensor) or,
with ``isFisheye``, the equidistant fisheye model (``k1..k4``). Neither maps
one to one onto :mod:`openfollow.lens_model`, so the lens centre is taken from
the file and ``k1`` / ``k2`` are fitted to the file's whole mapping: screen
points over the snapshot are undistorted by OpenCV's model, and the valid pair
(with a free pinhole scale, which the camera solve owns) that lands them back
nearest, by RMS, is kept. The field of view in the camera matrix is not used.

A calibration describes the picture only as far as its pattern reached: past
the radius where its polynomial stops rising, and past 90 degrees off the axis
for a fisheye, it maps no screen point. The fit uses the part it describes, at
most :data:`MAX_OFF_AXIS_DEG` off the axis, and the lens model carries on
past it.
"""

from __future__ import annotations

import math
import re

# The parse is size-capped and never sees a DOCTYPE or entity (parse_opencv_calibration).
import xml.etree.ElementTree as ET  # nosec B405
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from openfollow.lens_model import C_BOUND, PULL_BACK_MARGIN, lens_warp_is_valid
from openfollow.scene.solver import apply_overlay_distortion

FloatArray = npt.NDArray[np.float64]
BoolArray = npt.NDArray[np.bool_]

# Far larger than any calibration file (about 1 kB); bounds the parse.
MAX_FILE_BYTES = 64 * 1024
# The largest calibrated image side a file may state.
MAX_SIDE = 65536
_STANDARD_COUNTS = (4, 5, 8, 12, 14)
_FISHEYE_COUNT = 4
_MATRIX_KEYS = ("cameraMatrix", "camera_matrix")
_DIST_KEYS = ("dist_coeffs", "distCoeffs", "distortion_coefficients")
# Same picture shape: the sides' ratios within this fraction, as the Lens step's saved list judges it.
SHAPE_TOLERANCE = 0.01
# Toward 90 degrees the pinhole picture runs off to infinity; a stage overlay ends well before.
MAX_OFF_AXIS_DEG = 80.0
# The share of the snapshot a calibration must describe to give a lens.
MIN_COVERAGE = 0.25
# Screen points the fit samples along the snapshot's long side; the short side in proportion.
_GRID_LONG = 48
_INVERT_ITERS = 30
# Normalised distance below which an undistorted point maps back onto its screen point.
_SETTLED = 1e-9
_LM_ITERS = 60
_JACOBIAN_EPS = 1e-6
_K_SEEDS = ((0.0, 0.0), (-0.3, 0.0), (-0.6, -0.3), (0.0, -0.4))


class OpenCvLensError(ValueError):
    """The file gives no lens for this picture; the message says why, for the operator."""


class OpenCvSizeUnknown(OpenCvLensError):
    """The file does not carry its calibrated image's size and none was given."""


@dataclass(frozen=True)
class OpenCvCalibration:
    fx: float
    fy: float
    # The lens centre in pixels of the calibrated image, OpenCV's pixel-centre convention, ``cxcy_offset`` added.
    cx: float
    cy: float
    coeffs: tuple[float, ...]
    fisheye: bool
    # The calibrated image's size; None when the file does not carry it.
    width: int | None
    height: int | None


@dataclass(frozen=True)
class OpenCvLensFit:
    k1: float
    k2: float
    # The lens centre, half-diagonals from the frame centre (openfollow.lens_model).
    cx: float
    cy: float
    # Screen error against the file, in snapshot pixels, over the part it describes; the corners'
    # largest only when it describes all four, else None.
    rms_px: float
    corner_px: float | None
    # The share of the snapshot the file describes and the fit used.
    coverage: float
    # The calibrated image's size, and whether the file carried it.
    calibration_width: int
    calibration_height: int
    size_from_file: bool


def _numbers(text: str | None) -> list[float]:
    try:
        values = [float(v) for v in (text or "").split()]
    except ValueError as exc:
        raise OpenCvLensError("The calibration file holds a value that is not a number.") from exc
    if not all(math.isfinite(v) for v in values):
        raise OpenCvLensError("The calibration file holds a value that is not a finite number.")
    return values


def _matrix(node: ET.Element, name: str) -> list[float]:
    """An ``opencv-matrix`` node's data, checked against its rows and cols; a plain list of numbers as is."""
    data = node.find("data")
    if data is None:
        return _numbers(node.text)
    values = _numbers(data.text)
    try:
        rows, cols = int(node.findtext("rows") or ""), int(node.findtext("cols") or "")
    except ValueError as exc:
        raise OpenCvLensError(f"The calibration file's {name} has no valid size.") from exc
    if rows * cols != len(values):
        raise OpenCvLensError(f"The calibration file's {name} does not hold {rows} x {cols} values.")
    return values


def _first(root: ET.Element, keys: tuple[str, ...]) -> ET.Element | None:
    return next((node for key in keys if (node := root.find(key)) is not None), None)


def _size(root: ET.Element) -> tuple[int | None, int | None]:
    node = root.find("cameraResolution")
    if node is not None:
        values = _matrix(node, "image size")
    else:
        values = _numbers(root.findtext("image_width")) + _numbers(root.findtext("image_height"))
    if not values:
        return None, None
    if len(values) != 2 or not all(v.is_integer() and 0 < v <= MAX_SIDE for v in values):
        raise OpenCvLensError("The calibration file's image size is not two whole numbers.")
    return int(values[0]), int(values[1])


def parse_opencv_calibration(text: str) -> OpenCvCalibration:
    """The calibration in an OpenCV ``FileStorage`` XML file; anything that gives no lens raises with the reason."""
    try:
        size = len(text.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise OpenCvLensError("This is not a readable XML file.") from exc
    if size > MAX_FILE_BYTES:
        raise OpenCvLensError("This file is too large for a lens calibration.")
    head = text.lstrip("﻿ \t\r\n")
    if not head.startswith("<"):
        raise OpenCvLensError("Only XML calibration files can be read. Save the calibration as XML.")
    if re.search(r"<!(DOCTYPE|ENTITY)", text, re.IGNORECASE):
        raise OpenCvLensError("This XML file declares a document type, which a calibration file never does.")
    try:
        root = ET.fromstring(text)  # nosec B314
    except ET.ParseError as exc:
        raise OpenCvLensError("This is not a readable XML file.") from exc
    if root.tag != "opencv_storage":
        raise OpenCvLensError("This XML file is not an OpenCV calibration.")
    matrix_node = _first(root, _MATRIX_KEYS)
    if matrix_node is None:
        raise OpenCvLensError("The calibration file has no camera matrix.")
    matrix = _matrix(matrix_node, "camera matrix")
    if len(matrix) != 9:
        raise OpenCvLensError("The calibration file's camera matrix is not 3 x 3.")
    fx, skew, cx, _, fy, cy, *bottom = matrix
    if fx <= 0.0 or fy <= 0.0 or skew != 0.0 or bottom != [0.0, 0.0, 1.0]:
        raise OpenCvLensError("The calibration file's camera matrix is not a camera's.")
    dist_node = _first(root, _DIST_KEYS)
    if dist_node is None:
        raise OpenCvLensError("The calibration file has no distortion values, so it gives no lens.")
    coeffs = tuple(_matrix(dist_node, "distortion values"))
    flag = _numbers(root.findtext("isFisheye")) if root.find("isFisheye") is not None else []
    if flag not in ([], [0.0], [1.0]):
        raise OpenCvLensError("The calibration file's fisheye flag is neither 0 nor 1.")
    fisheye = flag == [1.0]
    if fisheye and len(coeffs) != _FISHEYE_COUNT:
        raise OpenCvLensError(f"A fisheye calibration has {_FISHEYE_COUNT} distortion values, this one {len(coeffs)}.")
    if not fisheye and len(coeffs) not in _STANDARD_COUNTS:
        raise OpenCvLensError(f"The calibration file has {len(coeffs)} distortion values, which is no OpenCV model.")
    if not flag and len(coeffs) == _FISHEYE_COUNT:
        raise OpenCvLensError(
            "The calibration file has 4 distortion values and does not say whether it is a fisheye calibration."
        )
    if len(coeffs) == 14 and (coeffs[12] or coeffs[13]):
        raise OpenCvLensError("The calibration models a tilted sensor, which the overlay cannot follow.")
    offset_node = root.find("cxcy_offset")
    offset = _matrix(offset_node, "lens centre offset") if offset_node is not None else [0.0, 0.0]
    if len(offset) != 2:
        raise OpenCvLensError("The calibration file's lens centre offset is not two values.")
    width, height = _size(root)
    return OpenCvCalibration(fx, fy, cx + offset[0], cy + offset[1], coeffs, fisheye, width, height)


def _padded(cal: OpenCvCalibration) -> tuple[float, ...]:
    return (*cal.coeffs, *(0.0,) * (14 - len(cal.coeffs)))


def _radial_scale(cal: OpenCvCalibration, r: FloatArray) -> FloatArray:
    """Distorted over pinhole radius (normalised) along a ray, tangential and prism terms aside."""
    if cal.fisheye:
        k1, k2, k3, k4 = cal.coeffs
        theta = np.arctan(r)
        t2 = theta * theta
        out = np.divide(theta * (1 + t2 * (k1 + t2 * (k2 + t2 * (k3 + t2 * k4)))), r, out=np.ones_like(r), where=r > 0)
        return np.asarray(out, dtype=np.float64)
    c = _padded(cal)
    s = r * r
    return np.asarray((1 + s * (c[0] + s * (c[1] + s * c[4]))) / (1 + s * (c[5] + s * (c[6] + s * c[7]))))


def distort_normalised(cal: OpenCvCalibration, x: FloatArray, y: FloatArray) -> tuple[FloatArray, FloatArray]:
    """OpenCV's model: normalised pinhole points to normalised distorted ones."""
    rad = _radial_scale(cal, np.hypot(x, y))
    if cal.fisheye:
        return x * rad, y * rad
    c = _padded(cal)
    s = x * x + y * y
    p1, p2, s1, s2, s3, s4 = c[2], c[3], c[8], c[9], c[10], c[11]
    xd = x * rad + 2 * p1 * x * y + p2 * (s + 2 * x * x) + s1 * s + s2 * s * s
    yd = y * rad + p1 * (s + 2 * y * y) + 2 * p2 * x * y + s3 * s + s4 * s * s
    return xd, yd


def _radial_table(cal: OpenCvCalibration) -> tuple[FloatArray, FloatArray]:
    """Pinhole and distorted radii along a ray, out to the cap or to where the distorted radius stops rising."""
    r = np.tan(np.radians(np.linspace(0.0, MAX_OFF_AXIS_DEG, 20001)))
    rd = r * _radial_scale(cal, r)
    falls = np.flatnonzero(~(np.diff(rd) > 0.0))
    end = int(falls[0]) + 1 if falls.size else r.size
    return r[:end], rd[:end]


def _undistort(cal: OpenCvCalibration, xd: FloatArray, yd: FloatArray) -> tuple[FloatArray, FloatArray, BoolArray]:
    """Normalised pinhole points of distorted ones, and which of them the file describes.

    The radius comes from the radial table; Newton then settles it, on the ray angle for a fisheye and
    on both axes for the standard model's tangential and prism terms.
    """
    table_r, table_rd = _radial_table(cal)
    rd = np.hypot(xd, yd)
    inside = rd < table_rd[-1]
    r = np.interp(rd, table_rd, table_r)
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        if cal.fisheye:
            k1, k2, k3, k4 = cal.coeffs
            theta = np.arctan(r)
            for _ in range(_INVERT_ITERS):
                t2 = theta * theta
                value = theta * (1 + t2 * (k1 + t2 * (k2 + t2 * (k3 + t2 * k4)))) - rd
                slope = 1 + t2 * (3 * k1 + t2 * (5 * k2 + t2 * (7 * k3 + t2 * 9 * k4)))
                theta = np.clip(theta - value / slope, 0.0, 0.5 * math.pi - 1e-9)
            r = np.tan(theta)
        scale = np.divide(r, rd, out=np.ones_like(rd), where=rd > 0.0)
        x, y = xd * scale, yd * scale
        if not cal.fisheye:
            for _ in range(_INVERT_ITERS):
                fx_, fy_ = distort_normalised(cal, x, y)
                ex, ey = fx_ - xd, fy_ - yd
                ax, ay = distort_normalised(cal, x + _JACOBIAN_EPS, y)
                bx, by = distort_normalised(cal, x, y + _JACOBIAN_EPS)
                j11, j21 = (ax - fx_) / _JACOBIAN_EPS, (ay - fy_) / _JACOBIAN_EPS
                j12, j22 = (bx - fx_) / _JACOBIAN_EPS, (by - fy_) / _JACOBIAN_EPS
                det = j11 * j22 - j12 * j21
                x = x - (j22 * ex - j12 * ey) / det
                y = y - (j11 * ey - j21 * ex) / det
        back_x, back_y = distort_normalised(cal, x, y)
        settled = np.hypot(back_x - xd, back_y - yd) < _SETTLED
        described = inside & settled & (np.hypot(x, y) <= table_r[-1])
    return x, y, described


def calibrated_size(
    cal: OpenCvCalibration, width: float, height: float, size: tuple[int, int] | None = None
) -> tuple[int, int]:
    """The calibrated image's size, from the file or else ``size``; raises when unknown or of another shape."""
    got = (cal.width, cal.height) if cal.width and cal.height else size
    if got is None:
        raise OpenCvSizeUnknown("The calibration file does not say which image size it was calibrated on.")
    cal_w, cal_h = got
    here = width / height
    if abs(cal_w / cal_h - here) > SHAPE_TOLERANCE * here:
        raise OpenCvLensError(
            f"The lens was calibrated on a {cal_w} x {cal_h} picture, this one is {width:g} x {height:g}. "
            "A calibration only fits a picture of the same shape."
        )
    return cal_w, cal_h


def _in_bounds(k1: float, k2: float, cx: float, cy: float) -> bool:
    """Valid with the pull-back's margin, so the slope at the far corner stays clear of 0."""
    return lens_warp_is_valid(k1 / (1.0 - PULL_BACK_MARGIN), k2 / (1.0 - PULL_BACK_MARGIN), cx, cy)


def fit_opencv_lens(
    cal: OpenCvCalibration, width: float, height: float, size: tuple[int, int] | None = None
) -> OpenCvLensFit:
    """Fit the lens model to ``cal`` for a ``width`` x ``height`` snapshot.

    ``size`` is the calibrated image's size when the file does not carry it. Raises when the size is
    unknown, the picture's shape differs, the lens centre is out of bounds or the file describes too
    little of the picture.
    """
    cal_w, cal_h = calibrated_size(cal, width, height, size)
    # The width sets the scale: within the shape tolerance, sides differ by a crop, not a resample.
    scale = width / cal_w
    half_diag = 0.5 * math.hypot(width, height)
    # OpenCV puts pixel centres on whole numbers, the snapshot puts pixel edges there.
    ox, oy = (cal.cx + 0.5) * scale, (cal.cy + 0.5) * scale
    fx, fy = cal.fx * scale, cal.fy * scale
    if not all(math.isfinite(v) for v in (ox, oy, fx, fy)):
        raise OpenCvLensError("The calibration file's camera matrix is out of range.")
    cx, cy = (ox - width / 2.0) / half_diag, (oy - height / 2.0) / half_diag
    if abs(cx) > C_BOUND or abs(cy) > C_BOUND:
        raise OpenCvLensError("The calibration's lens centre is too far from the centre of the picture.")
    long_side = _GRID_LONG
    short_side = max(2, round(_GRID_LONG * min(width, height) / max(width, height)))
    gw, gh = (long_side, short_side) if width >= height else (short_side, long_side)
    gx, gy = np.meshgrid(np.linspace(0.0, width, gw), np.linspace(0.0, height, gh))
    screen = np.column_stack([gx.ravel(), gy.ravel()])
    x, y, described = _undistort(cal, (screen[:, 0] - ox) / fx, (screen[:, 1] - oy) / fy)
    coverage = float(described.mean())
    if coverage < MIN_COVERAGE:
        raise OpenCvLensError("The calibration describes too little of this picture to give a lens.")
    is_corner = np.isin(screen[:, 0], (0.0, width)) & np.isin(screen[:, 1], (0.0, height))
    all_corners = bool(described[is_corner].all())
    screen, is_corner = screen[described], is_corner[described]
    pinhole = np.column_stack([fx * x[described], fy * y[described]])
    centre = np.array([width / 2.0, height / 2.0])

    def residuals(p: FloatArray) -> FloatArray:
        seen = apply_overlay_distortion(centre + p[2] * pinhole, width, height, float(p[0]), float(p[1]), cx, cy)
        return np.asarray((seen - screen).ravel(), dtype=np.float64)

    def refine(p: FloatArray) -> tuple[FloatArray, float]:
        """Damped Gauss-Newton (Levenberg-Marquardt) from ``p``, inside the valid region."""
        res = residuals(p)
        cost, lam = float(res @ res), 1e-3
        for _ in range(_LM_ITERS):
            jac = np.column_stack(
                [(residuals(p + d) - residuals(p - d)) / (2.0 * _JACOBIAN_EPS) for d in np.eye(3) * _JACOBIAN_EPS]
            )
            normal, grad = jac.T @ jac, jac.T @ res
            step: FloatArray = np.zeros(3)
            improved = False
            for _attempt in range(12):
                # The damping term keeps this invertible however flat the residuals are.
                step = np.asarray(-np.linalg.solve(normal + lam * np.diag(np.diag(normal) + 1e-12), grad))
                cand = p + step
                if not _in_bounds(float(cand[0]), float(cand[1]), cx, cy):
                    lam *= 10.0
                    continue
                cand_res = residuals(cand)
                cand_cost = float(cand_res @ cand_res)
                if cand_cost < cost:
                    p, res, cost, lam, improved = cand, cand_res, cand_cost, max(lam / 10.0, 1e-12), True
                    break
                lam *= 10.0
            if not improved or float(np.max(np.abs(step))) < 1e-10:
                break
        return p, cost

    starts = [np.array([a, b, 1.0]) for a, b in _K_SEEDS if _in_bounds(a, b, cx, cy)]
    best, _cost = min((refine(s) for s in starts), key=lambda found: found[1])
    err = np.hypot(*residuals(best).reshape(-1, 2).T)
    return OpenCvLensFit(
        k1=float(best[0]),
        k2=float(best[1]),
        cx=cx,
        cy=cy,
        rms_px=float(np.sqrt(np.mean(err * err))),
        corner_px=float(err[is_corner].max()) if all_corners else None,
        coverage=coverage,
        calibration_width=cal_w,
        calibration_height=cal_h,
        size_from_file=bool(cal.width and cal.height),
    )
