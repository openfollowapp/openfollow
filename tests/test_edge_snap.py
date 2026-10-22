# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Edge snap on synthetic frames: a bowed edge is followed as a whole, one edge and one polarity per line."""

from __future__ import annotations

import numpy as np
import pytest

from openfollow.scene.edge_snap import (
    LINE_SAMPLE_FRACTIONS,
    MAX_BAND_HALF,
    MAX_BAND_STEP,
    MIN_BAND_HALF,
    LumaBand,
    NearEdge,
    band_columns,
    band_half_size,
    band_step,
    end_search_radius,
    line_sample_points,
    point_window,
    snap_line_to_edges,
)
from openfollow.scene.solver import apply_overlay_distortion, invert_overlay_distortion
from tests._lens_band import cut_band

pytestmark = pytest.mark.unit

W, H = 1920, 1080
DARK, BRIGHT = 40.0, 200.0
# A strong lens with a large corner term.
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
    undistorted = invert_overlay_distortion(pixels, W, H, k1, k2, 0.0, 0.0)
    ends = invert_overlay_distortion(np.array([p0, p1], dtype=np.float64), W, H, k1, k2, 0.0, 0.0)
    d = ends[1] - ends[0]
    d /= np.hypot(*d)
    rel = undistorted - ends[0]
    distance = np.abs(rel[:, 0] * d[1] - rel[:, 1] * d[0]).reshape(H, W)
    img = np.where(distance <= half_width, bright, dark)
    return np.clip(img + rng.normal(0.0, noise, img.shape), 0, 255)


def distance_to_strip_centre(points, p0, p1, lens) -> np.ndarray:
    """Signed distance of picture points from the strip's centre line, measured where the strip is straight."""
    k1, k2 = lens
    ends = invert_overlay_distortion(np.array([p0, p1], dtype=np.float64), W, H, k1, k2, 0.0, 0.0)
    d = ends[1] - ends[0]
    d /= np.hypot(*d)
    rel = invert_overlay_distortion(np.asarray(points, dtype=np.float64), W, H, k1, k2, 0.0, 0.0) - ends[0]
    return rel[:, 0] * d[1] - rel[:, 1] * d[0]


def chord_sag(p0, p1, lens) -> float:
    """How far the strip's centre bows away from the straight chord, at most."""
    k1, k2 = lens
    ends = invert_overlay_distortion(np.array([p0, p1], dtype=np.float64), W, H, k1, k2, 0.0, 0.0)
    s = np.linspace(0.0, 1.0, 400)[:, None]
    seen = apply_overlay_distortion(ends[0] + s * (ends[1] - ends[0]), W, H, k1, k2, 0.0, 0.0)
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


@pytest.mark.parametrize("lens", [(-0.9, 0.0), (-0.74, -0.152)], ids=["barrel-slider-end", "150-degree"])
def test_an_edge_bowed_by_a_lens_near_the_slider_end_is_followed(lens) -> None:
    """Below the centre such a lens bows a line by some 160-180 px: the corridor still holds it."""
    p0, p1 = (90.0, 700.0), (1830.0, 690.0)
    assert chord_sag(p0, p1, lens) > 150.0
    pts = snap(strip_frame(p0, p1, lens), p0, p1)
    assert all(p.snapped for p in pts)
    np.testing.assert_allclose(np.abs(edge_distances(pts, p0, p1, lens)), 7.0, atol=1.5)


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
    """A brighter strip further off than the corridor must not pull the line over to it."""
    p0, p1 = (200.0, 500.0), (1700.0, 540.0)
    img = strip_frame(p0, p1, STRAIGHT, bright=110.0)
    img = np.maximum(img, strip_frame((p0[0], p0[1] + 60.0), (p1[0], p1[1] + 60.0), STRAIGHT, bright=240.0))
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
    # The strip's middle runs below the picture's bottom edge, its quarter points inside it.
    p0, p1 = (90.0, 1025.0), (1830.0, 1015.0)
    pts = snap(strip_frame(p0, p1, BARREL), p0, p1)
    assert [p.snapped for p in pts] == [True, True, False, True, True]
    # The unsnapped point stays where the page can show it and drag it: on the picture's edge.
    assert pts[2].y == H - 1


def test_an_unsnapped_end_stays_inside_the_picture() -> None:
    # The strip leaves the picture above its ends; the curve through its middle runs on out there.
    img = strip_frame((90.0, -12.0), (1830.0, -12.0), PINCUSHION, half_width=4.0)
    pts = snap(img, (90.0, 1.0), (1830.0, 1.0))
    assert [p.snapped for p in pts] == [False, True, True, True, False]
    assert all(0.0 <= p.x <= W - 1 and 0.0 <= p.y <= H - 1 for p in pts)


# --------------------------------------------------------------------------- #
# One edge: never the parallel one
# --------------------------------------------------------------------------- #

yy, xx = np.mgrid[0:H, 0:W]


def chord_y(x, y0: float = 500.0):
    """A line across the picture, gently tilted."""
    return y0 + 0.02 * x


def noisy(img: np.ndarray, sigma: float = 3.0) -> np.ndarray:
    return np.clip(img + np.random.default_rng(0).normal(0.0, sigma, img.shape), 0, 255)


def offsets(pts) -> list[float]:
    """Each point's offset below the line ``chord_y``."""
    return [p.y - chord_y(p.x) for p in pts]


def stepped_frame() -> np.ndarray:
    """Two edges of one polarity 16 px apart, the upper one weak: a truss's two chords."""
    img = np.full((H, W), 30.0)
    img[yy >= chord_y(xx)] = 70.0
    img[yy >= chord_y(xx) + 16.0] = 230.0
    return noisy(img)


@pytest.mark.parametrize("dy", [-2.0, 2.0])
def test_of_two_parallel_edges_the_clicked_one_is_kept(dy: float) -> None:
    pts = snap(stepped_frame(), (200.0, chord_y(200.0) + dy), (1700.0, chord_y(1700.0) - dy))
    assert all(p.snapped for p in pts)
    np.testing.assert_allclose(offsets(pts), 0.0, atol=1.0)


def test_the_stronger_parallel_edge_is_taken_when_it_is_clicked() -> None:
    pts = snap(stepped_frame(), (200.0, chord_y(200.0) + 15.0), (1700.0, chord_y(1700.0) + 17.0))
    assert all(p.snapped for p in pts)
    np.testing.assert_allclose(offsets(pts), 16.0, atol=1.0)


def tape_frame() -> np.ndarray:
    """White tape 8 px wide on a floor that is darker below it, so its lower edge is the stronger."""
    img = np.full((H, W), 90.0)
    img[np.abs(yy - chord_y(xx)) <= 4.0] = 200.0
    img[yy > chord_y(xx) + 4.0] = 10.0
    return noisy(img)


def test_a_tape_keeps_the_side_its_ends_were_clicked_on() -> None:
    pts = snap(tape_frame(), (200.0, chord_y(200.0) - 5.0), (1700.0, chord_y(1700.0) - 3.0))
    assert all(p.snapped for p in pts)
    np.testing.assert_allclose(offsets(pts), -4.0, atol=1.0)


def test_clutter_beside_a_weak_edge_does_not_pull_the_line_off_it() -> None:
    # Bright boxes 6 px below the edge, over 40 % of its length: far more gradient, but no line.
    img = np.full((H, W), 60.0)
    img[yy >= chord_y(xx)] = 85.0
    for x0 in range(250, 1700, 250):
        img[(xx >= x0) & (xx < x0 + 100) & (yy >= chord_y(xx) + 6.0) & (yy < chord_y(xx) + 30.0)] = 230.0
    pts = snap(noisy(img), (200.0, chord_y(200.0) + 1.0), (1700.0, chord_y(1700.0) - 1.0))
    assert all(p.snapped for p in pts)
    np.testing.assert_allclose(offsets(pts), 0.0, atol=1.0)


# --------------------------------------------------------------------------- #
# A used suggestion keeps its own edge
# --------------------------------------------------------------------------- #


def bright_tape_frame() -> np.ndarray:
    """Tape 6 px wide, black above and grey below: its upper edge is the stronger."""
    img = np.full((H, W), 20.0)
    img[np.abs(yy - chord_y(xx)) <= 3.0] = 200.0
    img[yy < chord_y(xx) - 3.0] = 0.0
    img[yy > chord_y(xx) + 3.0] = 120.0
    return noisy(img, 2.0)


def snap_near(img: np.ndarray, samples, polarity: int):
    samples = tuple((float(x), float(y)) for x, y in samples)
    band = cut_band(img, samples[0], samples[-1])
    return snap_line_to_edges(samples[0], samples[-1], band, W, H, near=NearEdge(samples, polarity))


def lower_tape_edge_samples(shift: float = 0.0):
    return line_sample_points((200.0, chord_y(200.0) + 3.5 + shift), (1700.0, chord_y(1700.0) + 3.5 + shift))


def test_a_suggestion_on_the_weaker_side_of_a_tape_stays_there() -> None:
    # Bright to grey downwards: luma falls along the chord's normal, which points down here.
    pts = snap_near(bright_tape_frame(), lower_tape_edge_samples(), -1)
    assert all(p.snapped for p in pts)
    np.testing.assert_allclose(offsets(pts), 3.0, atol=1.0)


def test_a_suggestion_s_own_contrast_side_is_never_swapped_for_the_other() -> None:
    # Told the other polarity, the point finds no edge of it within its window: none snaps.
    pts = snap_near(bright_tape_frame(), lower_tape_edge_samples(), 1)
    assert not any(p.snapped for p in pts)
    np.testing.assert_allclose([[p.x, p.y] for p in pts], lower_tape_edge_samples())


def test_a_suggestion_without_a_polarity_takes_the_one_at_its_samples() -> None:
    pts = snap_near(bright_tape_frame(), lower_tape_edge_samples(), 0)
    assert all(p.snapped for p in pts)
    np.testing.assert_allclose(offsets(pts), 3.0, atol=1.0)


def test_a_suggestion_s_samples_settle_onto_the_edge_beside_them() -> None:
    # The suggestions come from a map at half size: their samples sit a pixel or two off.
    pts = snap_near(bright_tape_frame(), lower_tape_edge_samples(shift=2.0), -1)
    assert all(p.snapped for p in pts)
    np.testing.assert_allclose(offsets(pts), 3.0, atol=1.0)


def test_a_suggestion_on_the_weak_chord_never_takes_the_strong_one() -> None:
    samples = line_sample_points((200.0, chord_y(200.0) + 0.5), (1700.0, chord_y(1700.0) + 0.5))
    pts = snap_near(stepped_frame(), samples, 1)
    assert all(p.snapped for p in pts)
    np.testing.assert_allclose(offsets(pts), 0.0, atol=1.0)


@pytest.mark.parametrize(
    ("samples", "polarity", "match"),
    [
        (line_sample_points((200.0, 600.0), (1700.0, 630.0))[:4], 1, "5 finite points"),
        (np.vstack([line_sample_points((200.0, 600.0), (1700.0, 630.0))[:4], [[np.nan, 600.0]]]), 1, "finite"),
        (line_sample_points((200.0, 600.0), (1700.0, 630.0)), 2, "polarity"),
        (line_sample_points((200.0, 600.0), (1700.0, 630.0)) + [0.0, 300.0], 1, "along the line"),
        (np.vstack([line_sample_points((200.0, 600.0), (1700.0, 630.0))[:4], [[2500.0, 640.0]]]), 1, "along"),
    ],
    ids=["four-points", "nan-point", "polarity-two", "beyond-the-band", "beyond-the-end"],
)
def test_a_suggestion_that_does_not_lie_along_its_line_is_refused(samples, polarity: int, match: str) -> None:
    img = np.full((H, W), DARK)
    band = cut_band(img, (200.0, 600.0), (1700.0, 630.0))
    near = NearEdge(tuple((float(x), float(y)) for x, y in samples), polarity)
    with pytest.raises(ValueError, match=match):
        snap_line_to_edges((200.0, 600.0), (1700.0, 630.0), band, W, H, near=near)


def test_the_point_window_scales_with_the_snapshot() -> None:
    assert [point_window(w) for w in (640, 1920, 2160, 3840)] == [4, 4, 4, 8]


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
    # Plain bools and floats: the snap route answers them as JSON.
    assert all(type(p.snapped) is bool and type(p.x) is float for p in pts)
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
