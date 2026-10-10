# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Edge snap on synthetic frames: a bowed edge is followed as a whole, one polarity per line."""

from __future__ import annotations

import numpy as np
import pytest

from openfollow.scene.edge_snap import (
    LINE_SAMPLE_FRACTIONS,
    MAX_BAND_HALF,
    MAX_BAND_STEP,
    MIN_BAND_HALF,
    LumaBand,
    band_columns,
    band_half_size,
    band_step,
    end_search_radius,
    line_sample_points,
    snap_line_to_edges,
)
from openfollow.scene.solver import apply_overlay_distortion, invert_overlay_distortion
from tests._lens_band import cut_band

pytestmark = pytest.mark.unit

W, H = 1920, 1080
DARK, BRIGHT = 40.0, 200.0
# A wide lens whose warp reaches the frame corners (k1 alone at -0.3 folds inside the picture).
BARREL = (-0.47, 0.25)
PINCUSHION = (0.3, 0.0)
STRAIGHT = (0.0, 0.0)


def strip_frame(
    p0,
    p1,
    lens,
    *,
    half_width: float = 7.0,
    noise: float = 4.0,
    seed: int = 0,
    dark: float = DARK,
    bright: float = BRIGHT,
):
    """A dark floor with a bright strip that is straight in reality, seen through ``lens``.

    ``p0`` and ``p1`` are where the strip's centre line passes in the picture.
    """
    k1, k2 = lens
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:H, 0:W]
    pixels = np.column_stack([xx.ravel(), yy.ravel()]).astype(np.float64)
    undistorted = invert_overlay_distortion(pixels, W, H, k1, k2)
    ends = invert_overlay_distortion(np.array([p0, p1], dtype=np.float64), W, H, k1, k2)
    d = ends[1] - ends[0]
    d /= np.hypot(*d)
    rel = undistorted - ends[0]
    distance = np.abs(rel[:, 0] * d[1] - rel[:, 1] * d[0]).reshape(H, W)
    img = np.where(distance <= half_width, bright, dark)
    return np.clip(img + rng.normal(0.0, noise, img.shape), 0, 255)


def distance_to_strip_centre(points, p0, p1, lens) -> np.ndarray:
    """Signed distance of picture points from the strip's centre line, measured where the strip is straight."""
    k1, k2 = lens
    ends = invert_overlay_distortion(np.array([p0, p1], dtype=np.float64), W, H, k1, k2)
    d = ends[1] - ends[0]
    d /= np.hypot(*d)
    rel = invert_overlay_distortion(np.asarray(points, dtype=np.float64), W, H, k1, k2) - ends[0]
    return rel[:, 0] * d[1] - rel[:, 1] * d[0]


def chord_sag(p0, p1, lens) -> float:
    """How far the strip's centre bows away from the straight chord, at most."""
    k1, k2 = lens
    ends = invert_overlay_distortion(np.array([p0, p1], dtype=np.float64), W, H, k1, k2)
    s = np.linspace(0.0, 1.0, 400)[:, None]
    seen = apply_overlay_distortion(ends[0] + s * (ends[1] - ends[0]), W, H, k1, k2)
    a, b = np.asarray(p0, float), np.asarray(p1, float)
    t = (b - a) / np.hypot(*(b - a))
    return float(np.max(np.abs((seen - a) @ np.array([-t[1], t[0]]))))


def snap(img, p0, p1):
    return snap_line_to_edges(p0, p1, cut_band(img, p0, p1), W, H)


def edge_distances(pts, p0, p1, lens) -> np.ndarray:
    return distance_to_strip_centre([[p.x, p.y] for p in pts], p0, p1, lens)


# --------------------------------------------------------------------------- #
# Geometry
# --------------------------------------------------------------------------- #


def test_sample_points_sit_at_the_five_fractions() -> None:
    pts = line_sample_points((100.0, 200.0), (500.0, 600.0))
    np.testing.assert_allclose(pts[:, 0], [100.0 + 400.0 * f for f in LINE_SAMPLE_FRACTIONS])
    np.testing.assert_allclose(pts[:, 1], [200.0 + 400.0 * f for f in LINE_SAMPLE_FRACTIONS])


@pytest.mark.parametrize(
    ("width", "half", "step", "reach"),
    [
        (320, MIN_BAND_HALF, 1, 4),
        (960, 96, 2, 8),
        (1920, 192, 4, 16),
        (3840, MAX_BAND_HALF, MAX_BAND_STEP, 32),
        (9999, MAX_BAND_HALF, MAX_BAND_STEP, 83),
        # A half rounds up, as the wizard's Math.round does.
        (1200, 120, 3, 10),
        (1285, 129, 3, 11),
        (2160, 216, 5, 18),
    ],
)
def test_band_geometry_scales_with_the_snapshot(width: int, half: int, step: int, reach: int) -> None:
    assert band_half_size(width) == half
    assert band_step(width) == step
    assert end_search_radius(width) == reach


@pytest.mark.parametrize(
    ("length", "step", "cols"), [(1.0, 4, 2), (4.0, 4, 2), (5.0, 4, 3), (1900.0, 4, 476), (10.0, 1, 11)]
)
def test_band_columns_cover_the_chord(length: float, step: int, cols: int) -> None:
    assert band_columns(length, step) == cols


# --------------------------------------------------------------------------- #
# Following a bowed edge
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("lens", [BARREL, PINCUSHION], ids=["barrel", "pincushion"])
def test_a_strongly_bowed_edge_is_followed_along_its_length(lens) -> None:
    """The chord misses the edge by far more than a search around the chord's points covers."""
    p0, p1 = (90.0, 1010.0), (1830.0, 990.0)
    sag = chord_sag(p0, p1, lens)
    assert sag > 40.0
    pts = snap(strip_frame(p0, p1, lens), p0, p1)
    assert all(p.snapped for p in pts)
    distance = edge_distances(pts, p0, p1, lens)
    # Every point on the same edge of the strip, each within a pixel of it.
    assert np.all(np.sign(distance) == np.sign(distance[0]))
    np.testing.assert_allclose(np.abs(distance), 7.0, atol=1.0)


def test_a_line_off_the_image_centre_keeps_the_bow_asymmetry() -> None:
    p0, p1 = (150.0, 200.0), (260.0, 1000.0)
    pts = snap(strip_frame(p0, p1, BARREL), p0, p1)
    assert all(p.snapped for p in pts)
    np.testing.assert_allclose(np.abs(edge_distances(pts, p0, p1, BARREL)), 7.0, atol=1.0)


def test_a_straight_edge_stays_straight_to_the_sub_pixel() -> None:
    p0, p1 = (200.0, 300.3), (1700.0, 330.3)
    pts = snap(strip_frame(p0, p1, STRAIGHT, noise=1.0), p0, p1)
    assert all(p.snapped for p in pts)
    distance = np.abs(edge_distances(pts, p0, p1, STRAIGHT))
    spread = float(np.max(np.abs(distance - np.mean(distance))))
    assert spread < 0.6


def test_a_thin_tape_line_is_followed() -> None:
    p0, p1 = (90.0, 1010.0), (1830.0, 990.0)
    pts = snap(strip_frame(p0, p1, BARREL, half_width=2.0), p0, p1)
    assert all(p.snapped for p in pts)
    np.testing.assert_allclose(np.abs(edge_distances(pts, p0, p1, BARREL)), 2.0, atol=1.5)


def test_the_clicked_ends_are_moved_onto_the_edge() -> None:
    """Clicks a few pixels off the lower edge settle onto it, with the rest of the line."""
    p0, p1 = (90.0, 1010.0), (1830.0, 990.0)
    img = strip_frame(p0, p1, BARREL)
    pts = snap(img, (p0[0], p0[1] + 12.0), (p1[0], p1[1] + 3.0))
    assert all(p.snapped for p in pts)
    np.testing.assert_allclose(np.abs(edge_distances(pts, p0, p1, BARREL)), 7.0, atol=1.0)


def test_a_chord_tilted_against_its_edge_is_straightened() -> None:
    """Both ends off to opposite sides: only moving them together finds the edge."""
    p0, p1 = (200.0, 600.0), (1700.0, 630.0)
    img = strip_frame(p0, p1, STRAIGHT)
    pts = snap(img, (p0[0], p0[1] - 2.0), (p1[0], p1[1] - 12.0))
    assert all(p.snapped for p in pts)
    np.testing.assert_allclose(np.abs(edge_distances(pts, p0, p1, STRAIGHT)), 7.0, atol=1.0)


def test_an_edge_beyond_the_ends_reach_is_not_taken_at_the_ends() -> None:
    """The clicks say where the line starts and ends; an edge too far from them stays unclaimed there."""
    p0, p1 = (200.0, 600.0), (1700.0, 630.0)
    img = strip_frame(p0, p1, STRAIGHT)
    shift = end_search_radius(W) + 30.0
    clicks = (p0[0], p0[1] + shift), (p1[0], p1[1] + shift)
    pts = snap(img, *clicks)
    assert not pts[0].snapped and not pts[4].snapped
    for p, click in ((pts[0], clicks[0]), (pts[4], clicks[1])):
        assert np.hypot(p.x - click[0], p.y - click[1]) <= end_search_radius(W) + 0.5


def test_an_end_stays_within_its_reach_of_the_click_beside_a_stronger_edge() -> None:
    """A brighter strip a little further off must not pull the clicked ends over to it."""
    p0, p1 = (200.0, 500.0), (1700.0, 540.0)
    img = strip_frame(p0, p1, STRAIGHT, bright=110.0)
    img = np.maximum(img, strip_frame((p0[0], p0[1] + 30.0), (p1[0], p1[1] + 30.0), STRAIGHT, bright=240.0))
    clicks = (p0[0], p0[1] - 7.0 + 2.0), (p1[0], p1[1] - 7.0 - 2.0)
    pts = snap(img, *clicks)
    reach = end_search_radius(W)
    for p, click in ((pts[0], clicks[0]), (pts[4], clicks[1])):
        assert np.hypot(p.x - click[0], p.y - click[1]) <= reach + 0.5
    # The whole line stays on the strip the clicks named, its upper edge.
    assert all(p.snapped for p in pts)
    np.testing.assert_allclose(edge_distances(pts, p0, p1, STRAIGHT), 7.0, atol=1.0)


# --------------------------------------------------------------------------- #
# What is not an edge
# --------------------------------------------------------------------------- #


def test_a_covered_stretch_leaves_its_point_on_the_curve_unsnapped() -> None:
    p0, p1 = (90.0, 1010.0), (1830.0, 990.0)
    img = strip_frame(p0, p1, BARREL)
    img[:, 860:1060] = DARK
    pts = snap(img, p0, p1)
    assert [p.snapped for p in pts] == [True, True, False, True, True]
    # The hidden point still sits about where the edge runs, not on the straight chord.
    assert abs(edge_distances(pts[2:3], p0, p1, BARREL)[0]) < 12.0
    chord_mid = line_sample_points(p0, p1)[2]
    assert np.hypot(pts[2].x - chord_mid[0], pts[2].y - chord_mid[1]) > 30.0


@pytest.mark.parametrize("sigma", [1.5, 3.0, 6.0])
def test_noise_alone_snaps_nothing_and_leaves_the_line_where_it_was(sigma: float) -> None:
    # The best path through grain is still a path: followed, it moved points 16 px.
    rng = np.random.default_rng(1)
    img = np.clip(DARK + rng.normal(0.0, sigma, (H, W)), 0, 255)
    p0, p1 = (200.0, 600.0), (1700.0, 630.0)
    pts = snap(img, p0, p1)
    assert not any(p.snapped for p in pts)
    np.testing.assert_allclose([[p.x, p.y] for p in pts], line_sample_points(p0, p1))


def test_an_end_beside_an_edge_beyond_its_reach_is_not_snapped_onto_its_flank() -> None:
    p0, p1 = (200.0, 600.0), (1700.0, 630.0)
    img = strip_frame(p0, p1, STRAIGHT, noise=1.0)
    # The strip's upper edge sits 7 px above its centre line, and one pixel past the ends' reach.
    shift = 7.0 + end_search_radius(W) + 1.0
    clicks = (p0[0], p0[1] - shift), (p1[0], p1[1] - shift)
    pts = snap_line_to_edges(*clicks, cut_band(img, *clicks), W, H)
    assert [p.snapped for p in pts] == [False, True, True, True, False]


def test_a_flat_frame_keeps_the_chord_exactly() -> None:
    img = np.full((H, W), DARK)
    p0, p1 = (200.0, 600.0), (1700.0, 630.0)
    pts = snap(img, p0, p1)
    assert not any(p.snapped for p in pts)
    np.testing.assert_allclose([[p.x, p.y] for p in pts], line_sample_points(p0, p1))


def test_all_points_take_one_edge_of_a_strip_even_when_the_other_is_stronger_locally() -> None:
    p0, p1 = (200.0, 600.0), (1700.0, 630.0)
    img = strip_frame(p0, p1, STRAIGHT)
    # Darken the floor below the strip's middle, so its bottom edge is the stronger one there.
    yy, xx = np.mgrid[0:H, 0:W]
    below = (yy > 600.0 + 0.02 * xx + 7.0) & (xx > 800) & (xx < 1100)
    img[below] = 5.0
    pts = snap(img, p0, p1)
    assert all(p.snapped for p in pts)
    distance = edge_distances(pts, p0, p1, STRAIGHT)
    assert np.all(np.sign(distance) == np.sign(distance[0]))


def test_the_image_border_is_no_edge() -> None:
    """A band leaving the snapshot sees nothing there, not a step from the picture to black.

    The faint strip runs 28 px from the left border; the picture's own step down to the
    nothing beyond it would be the stronger edge if the band were read as painted.
    """
    p0, p1 = (28.0, 200.0), (28.0, 900.0)
    pts = snap(strip_frame(p0, p1, STRAIGHT, bright=70.0, noise=1.0), p0, p1)
    assert all(p.snapped for p in pts)
    np.testing.assert_allclose(np.abs(edge_distances(pts, p0, p1, STRAIGHT)), 7.0, atol=1.0)


def test_an_edge_that_bows_out_of_the_picture_is_unsnapped_there() -> None:
    p0, p1 = (90.0, 1060.0), (1830.0, 1040.0)
    pts = snap(strip_frame(p0, p1, BARREL), p0, p1)
    assert [p.snapped for p in pts] == [True, True, False, True, True]
    assert pts[2].y > H - 1


# --------------------------------------------------------------------------- #
# Input rules
# --------------------------------------------------------------------------- #


def test_a_band_with_the_wrong_row_count_is_refused() -> None:
    img = np.full((H, W), DARK)
    p0, p1 = (200.0, 600.0), (1700.0, 630.0)
    band = cut_band(img, p0, p1)
    with pytest.raises(ValueError, match="rows"):
        snap_line_to_edges(p0, p1, LumaBand(band.step, band.half, band.luma[1:]), W, H)


def test_a_band_cut_for_another_line_is_refused() -> None:
    img = np.full((H, W), DARK)
    band = cut_band(img, (200.0, 600.0), (1700.0, 630.0))
    with pytest.raises(ValueError, match="columns"):
        snap_line_to_edges((200.0, 600.0), (900.0, 630.0), band, W, H)


@pytest.mark.parametrize(("step", "half"), [(0, 192), (4, 0)])
def test_a_band_without_a_size_is_refused(step: int, half: int) -> None:
    band = LumaBand(step, half, np.zeros((2 * max(half, 1) + 1, 10)))
    with pytest.raises(ValueError, match="positive"):
        snap_line_to_edges((0.0, 0.0), (36.0, 0.0), band, W, H)


def test_a_degenerate_line_is_refused() -> None:
    band = LumaBand(4, 192, np.zeros((385, 2)))
    with pytest.raises(ValueError, match="short"):
        snap_line_to_edges((200.0, 600.0), (200.0, 600.0), band, W, H)


def step_frame(edge_x: float, *, ramp: float = 1.0) -> np.ndarray:
    """Dark left of ``edge_x``, bright right of it, the step spread linearly over ``ramp`` pixels."""
    xx = np.arange(W, dtype=np.float64)[None, :] * np.ones((H, 1))
    return DARK + (BRIGHT - DARK) * np.clip((xx - edge_x) / ramp + 0.5, 0.0, 1.0)


@pytest.mark.parametrize("fraction", [0.0, 0.25, 0.5, 0.75])
def test_an_edge_between_pixels_is_found_to_a_tenth_of_one(fraction: float) -> None:
    edge = 1000.0 + fraction
    pts = snap(step_frame(edge, ramp=2.0), (edge + 5.0, 100.0), (edge + 5.0, 900.0))
    assert all(p.snapped for p in pts)
    assert max(abs(p.x - edge) for p in pts) < 0.1


def test_a_very_soft_edge_snaps_onto_its_ramp() -> None:
    # Spread over twenty pixels, the gradient has a flat top: no parabola to refine.
    pts = snap(step_frame(1000.0, ramp=20.0), (1000.0, 100.0), (1000.0, 900.0))
    assert all(p.snapped for p in pts)
    assert all(990.0 <= p.x <= 1010.0 for p in pts)


def test_an_edge_beside_the_frame_border_snaps_to_its_pixel() -> None:
    # The cells outside the snapshot carry no gradient, so the peak beside them has one
    # neighbour to refine it by, and stays on its pixel.
    pts = snap(step_frame(1.0), (12.0, 100.0), (12.0, 900.0))
    assert all(p.snapped for p in pts)
    np.testing.assert_allclose([p.x for p in pts], 1.0)
