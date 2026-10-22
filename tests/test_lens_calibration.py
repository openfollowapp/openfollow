# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The Lens step end to end on a synthetic stage: its suggestions, snapped and fitted, give back the lens."""

from __future__ import annotations

import math
from functools import cache

import numpy as np
import pytest

from openfollow.scene.edge_chains import find_edge_candidates
from openfollow.scene.solver import apply_overlay_distortion, invert_overlay_distortion
from tests._lens_band import scaled_luma
from tests._lens_scene import FLOOR, H, Scene, Strip, W, calibrate, deck_scene, render, riser_scene, stage_scene
from tests.test_lens_fit import frame_error_px

pytestmark = pytest.mark.unit

LENSES = {
    "pinhole": (0.0, 0.0),
    "mild-barrel": (-0.15, 0.0),
    "stage-camera": (-0.2, -0.03),
    # A 126 degree lens (2.3 mm on 1920 x 1080).
    "wide-lens": (-0.527, -0.069),
    "corner-heavy": (-0.3, -0.3),
    "pincushion": (0.2, 0.0),
    "barrel-with-edge-fit": (-0.3, 0.1),
}
# How far either recovered coefficient may sit from the lens the picture was made with.
K_TOLERANCE = 0.01
HALF_DIAG = 0.5 * math.hypot(W, H)


@cache
def picture(
    lens: tuple[float, float], *, grid: bool = True, distractors: bool = True, noise: float = 3.0, seed: int = 0
):
    return render(stage_scene(grid=grid, distractors=distractors), lens, noise=noise, seed=seed)


def assert_recovered(fit, lens: tuple[float, float]) -> None:
    assert fit.k1 == pytest.approx(lens[0], abs=K_TOLERANCE), (fit.k1, fit.k2)
    assert fit.k2 == pytest.approx(lens[1], abs=K_TOLERANCE), (fit.k1, fit.k2)
    assert fit.k2_fitted
    assert (fit.cx, fit.cy) == (0.0, 0.0)
    assert fit.rating in ("okay", "good", "excellent")
    assert not any(line.misfit for line in fit.lines)


def on_a_strip(points, scene: Scene, lens: tuple[float, float]) -> bool:
    """Whether every point, undistorted with the true lens, lies within a few pixels of one of the scene's strips."""
    pinhole = invert_overlay_distortion(np.asarray(points, dtype=np.float64), W, H, *lens, 0.0, 0.0)
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
    on = apply_overlay_distortion(along, W, H, *lens, 0.0, 0.0)
    beside = apply_overlay_distortion(along + np.array([0.0, 14.0]), W, H, *lens, 0.0, 0.0)
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


@pytest.mark.parametrize("lens", [LENSES["stage-camera"], LENSES["wide-lens"]], ids=["stage-camera", "wide-lens"])
def test_one_pixel_deck_seams_at_720p_pin_the_pair(lens: tuple[float, float]) -> None:
    """A soft 720p camera shows the seams between stage decks as one-pixel lines; they are offered and used."""
    img = render(deck_scene(), lens, size=(1280, 720), floor=120.0, noise=2.0)
    cal = calibrate(img)
    assert len(cal.lines) >= 8
    assert_recovered(cal.fit, lens)
    # At least two of the lines used are seams across the stage, flat in the pinhole frame.
    seams = 0
    for line in cal.lines:
        pts = invert_overlay_distortion(np.asarray(line, dtype=np.float64), 1280, 720, *lens, 0.0, 0.0)
        if np.ptp(pts[:, 1]) < 6.0 and min(abs(float(np.mean(pts[:, 1])) - y) for y in (280.0, 410.0, 540.0)) < 6.0:
            seams += 1
    assert seams >= 2


@pytest.mark.parametrize(("offset", "lens"), [((80.0, 50.0), "wide-lens"), ((-40.0, 25.0), "corner-heavy")])
def test_a_lens_centre_off_the_picture_centre_is_fitted_about_when_given(
    offset: tuple[float, float], lens: str
) -> None:
    """The lens bends the stage about a point off the picture's centre; given it, the suggested lines give the pair."""
    k1, k2 = LENSES[lens]
    centre = (offset[0] / HALF_DIAG, offset[1] / HALF_DIAG)
    fit = calibrate(render(stage_scene(), (k1, k2, *centre), noise=3.0), centre).fit
    assert (fit.cx, fit.cy) == centre
    assert fit.k1 == pytest.approx(k1, abs=K_TOLERANCE)
    assert fit.k2 == pytest.approx(k2, abs=K_TOLERANCE)
    assert fit.rating in ("okay", "good", "excellent")


def test_a_seam_running_on_up_a_riser_is_not_offered_as_one_line() -> None:
    """The deck seam and the riser seam beside it are two lines: no suggestion steps from one to the other."""
    lens = LENSES["stage-camera"]
    img = render(riser_scene(), lens, size=(960, 540), noise=2.0)
    found = find_edge_candidates(scaled_luma(img, 1), 1, 960, 540).candidates
    for candidate in found:
        pinhole = invert_overlay_distortion(np.asarray(candidate.points), 960, 540, *lens, 0.0, 0.0)
        rel = pinhole - pinhole.mean(axis=0)
        normal = np.linalg.svd(rel)[2][1]
        assert float(np.max(np.abs(rel @ normal))) < 1.5, candidate.points
    assert_recovered(calibrate(img).fit, lens)


def test_a_single_bowed_edge_measures_barrel_and_asks_for_a_corner() -> None:
    """One edge across the picture shows the barrel; the corner correction needs a line into a corner."""
    lens = LENSES["wide-lens"]
    front = Scene([Strip((100.0, 960.0), (1820.0, 960.0), 6.0, 210.0)])
    cal = calibrate(render(front, lens))
    assert len(cal.lines) == 1
    assert cal.fit.k1 < -0.4
    assert cal.fit.lines[0].rms_px < 0.5
    assert cal.fit.hint == "Add a second line in another part of the picture."


def _gallery_fit(picture):
    cv2 = pytest.importorskip("cv2", reason="decoding the picture needs the detection extra")
    return calibrate(cv2.imread(str(picture), cv2.IMREAD_GRAYSCALE).astype(np.float64)).fit


def test_the_arena_default_gives_back_the_lens_it_was_drawn_with() -> None:
    """The Media Gallery's Arena demonstrates the Lens step; the help drawer names the pair it was drawn with."""
    from openfollow.video import media_store

    fit = _gallery_fit(media_store.ARENA_ASSET_JPG)
    # The arena's lines trade the two values along a valley; the bend they measure is the drawn one.
    assert frame_error_px((fit.k1, fit.k2, fit.cx, fit.cy), (-0.15, -0.03, 0.0, 0.0)) < 5.0
    assert fit.k2_fitted
    assert not fit.centre_fitted
    assert fit.rating in ("good", "excellent")
    assert not any(line.misfit for line in fit.lines)
