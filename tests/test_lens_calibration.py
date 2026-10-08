# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The Lens step end to end on a synthetic stage: its suggestions, snapped and fitted, give back the lens."""

from __future__ import annotations

from functools import cache

import numpy as np
import pytest

from openfollow.scene.solver import apply_overlay_distortion, invert_overlay_distortion
from tests._lens_scene import FLOOR, H, Scene, Strip, W, calibrate, render, stage_scene

pytestmark = pytest.mark.unit

LENSES = {
    "pinhole": (0.0, 0.0),
    "mild-barrel": (-0.15, 0.0),
    "stage-camera": (-0.26, 0.05),
    "wide-lens": (-0.47, 0.25),
    "pincushion": (0.2, 0.0),
    "barrel-with-edge-fit": (-0.3, 0.1),
}
# How far either recovered coefficient may sit from the lens the picture was made with.
K_TOLERANCE = 0.01


@cache
def picture(
    lens: tuple[float, float], *, grid: bool = True, distractors: bool = True, noise: float = 3.0, seed: int = 0
):
    return render(stage_scene(grid=grid, distractors=distractors), lens, noise=noise, seed=seed)


def assert_recovered(fit, lens: tuple[float, float]) -> None:
    assert fit.k1 == pytest.approx(lens[0], abs=K_TOLERANCE), (fit.k1, fit.k2)
    assert fit.k2 == pytest.approx(lens[1], abs=K_TOLERANCE), (fit.k1, fit.k2)
    assert fit.k2_fitted
    assert fit.rating in ("okay", "good", "excellent")
    assert not any(line.misfit for line in fit.lines)


def on_a_strip(points, scene: Scene, lens: tuple[float, float]) -> bool:
    """Whether every point, undistorted with the true lens, lies within a few pixels of one of the scene's strips."""
    pinhole = invert_overlay_distortion(np.asarray(points, dtype=np.float64), W, H, *lens)
    for p in pinhole:
        nearest = min(
            float(np.linalg.norm(p - a - np.clip(((p - a) @ d) / float(d @ d), 0.0, 1.0) * d)) - strip.half_width
            for strip in scene.strips
            for a, d in [(np.asarray(strip.p0), np.asarray(strip.p1) - np.asarray(strip.p0))]
        )
        if nearest > 3.0:
            return False
    return True


# --------------------------------------------------------------------------- #
# The picture
# --------------------------------------------------------------------------- #


def test_a_strip_lies_in_the_picture_where_the_overlay_bows_it() -> None:
    """The picture is made with the inverse; the fit relies on the forward warp. The two must agree."""
    lens = LENSES["wide-lens"]
    img = picture(lens)
    strip = stage_scene().strips[0]
    s = np.linspace(0.05, 0.95, 30)[:, None]
    along = np.asarray(strip.p0) + s * (np.asarray(strip.p1) - np.asarray(strip.p0))
    on = apply_overlay_distortion(along, W, H, *lens)
    beside = apply_overlay_distortion(along + np.array([0.0, 14.0]), W, H, *lens)
    assert np.all(img[on[:, 1].round().astype(int), on[:, 0].round().astype(int)] > 150.0)
    assert np.all(img[beside[:, 1].round().astype(int), beside[:, 0].round().astype(int)] < FLOOR + 25.0)


def test_the_lines_bow_before_the_fit_straightens_them() -> None:
    """Straight lines in the picture would let any pair pass: through the wide lens they sag by tens of pixels."""
    lens = LENSES["wide-lens"]
    cal = calibrate(picture(lens))
    sags = []
    for line in cal.lines:
        a, b = np.asarray(line[0]), np.asarray(line[-1])
        d = b - a
        normal = np.array([-d[1], d[0]]) / np.hypot(*d)
        sags.append(max(abs(float((np.asarray(p) - a) @ normal)) for p in line))
    assert max(sags) > 40.0
    # Undistorted with the fitted pair the lines are straight: to a fraction of a pixel for most,
    # and none near the misfit threshold, though a point at a tape crossing can sit a pixel or two off.
    residuals = [line.rms_px for line in cal.fit.lines]
    assert float(np.median(residuals)) < 0.5
    assert max(residuals) < 3.0


# --------------------------------------------------------------------------- #
# Recovery
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("lens", list(LENSES.values()), ids=list(LENSES))
def test_the_pair_is_recovered_from_the_suggested_lines(lens: tuple[float, float]) -> None:
    cal = calibrate(picture(lens))
    assert len(cal.lines) >= 6
    assert_recovered(cal.fit, lens)


@pytest.mark.parametrize("lens", [LENSES["stage-camera"], LENSES["wide-lens"]], ids=["stage-camera", "wide-lens"])
def test_the_four_stage_edges_alone_pin_the_pair(lens: tuple[float, float]) -> None:
    cal = calibrate(picture(lens, grid=False))
    assert len(cal.lines) == 4
    assert_recovered(cal.fit, lens)


@pytest.mark.parametrize("lens", [LENSES["stage-camera"], LENSES["wide-lens"]], ids=["stage-camera", "wide-lens"])
def test_heavier_noise_costs_little(lens: tuple[float, float]) -> None:
    cal = calibrate(picture(lens, noise=8.0, seed=3))
    assert_recovered(cal.fit, lens)


@pytest.mark.parametrize("lens", [LENSES["pinhole"], LENSES["wide-lens"]], ids=["pinhole", "wide-lens"])
def test_every_line_used_is_a_straight_strip_of_the_scene(lens: tuple[float, float]) -> None:
    """Spotlights and a round table are in the picture; none of their rims is taken for a line."""
    scene = stage_scene()
    cal = calibrate(picture(lens))
    assert cal.lines
    assert all(on_a_strip(line, scene, lens) for line in cal.lines)


def test_a_single_bowed_edge_already_tells_the_pair() -> None:
    lens = LENSES["stage-camera"]
    front = Scene([Strip((100.0, 960.0), (1820.0, 960.0), 6.0, 210.0)])
    cal = calibrate(render(front, lens))
    assert len(cal.lines) == 1
    assert cal.fit.k1 == pytest.approx(lens[0], abs=K_TOLERANCE)
    assert cal.fit.k2 == pytest.approx(lens[1], abs=K_TOLERANCE)
