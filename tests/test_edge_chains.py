# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Edge suggestions on synthetic frames: long smooth edges are offered, rims, corners and repeats are not."""

from __future__ import annotations

import numpy as np
import pytest

from openfollow.scene.edge_chains import (
    EDGE_MAP_WIDTH,
    MAX_CANDIDATES,
    MAX_EDGE_SCALE,
    candidate_sample_points,
    edge_map_scale,
    edge_map_size,
    find_edge_candidates,
)
from openfollow.scene.edge_snap import LINE_SAMPLE_FRACTIONS, end_search_radius, snap_line_to_edges
from tests._lens_band import cut_band, scaled_luma
from tests.test_edge_snap import BARREL, BRIGHT, DARK, STRAIGHT, H, W, distance_to_strip_centre, strip_frame

pytestmark = pytest.mark.unit

SCALE = edge_map_scale(W)


def find(img, **kwargs):
    return find_edge_candidates(scaled_luma(img, SCALE), SCALE, W, H, **kwargs).candidates


def straight_strip(p0, p1, *, half_width: float = 7.0, bright: float = BRIGHT, base=None) -> np.ndarray:
    """A bright strip between two points on a dark floor, or added to ``base``; it ends at the points."""
    yy, xx = np.mgrid[0:H, 0:W]
    a, b = np.asarray(p0, dtype=np.float64), np.asarray(p1, dtype=np.float64)
    d = b - a
    t = np.clip(((xx - a[0]) * d[0] + (yy - a[1]) * d[1]) / float(d @ d), 0.0, 1.0)
    distance = np.hypot(xx - a[0] - t * d[0], yy - a[1] - t * d[1])
    img = np.where(distance <= half_width, bright, DARK).astype(np.float64)
    return img if base is None else np.maximum(base, img)


def noisy(img: np.ndarray, sigma: float = 4.0, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return np.clip(img + rng.normal(0.0, sigma, img.shape), 0, 255)


def ends_of(candidate) -> tuple[np.ndarray, np.ndarray]:
    return np.asarray(candidate.points[0]), np.asarray(candidate.points[-1])


# --------------------------------------------------------------------------- #
# Geometry
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("width", "scale"), [(320, 1), (1280, 1), (1281, 2), (1920, 2), (2560, 2), (3840, 3), (99999, MAX_EDGE_SCALE)]
)
def test_the_scale_brings_the_snapshot_under_the_map_width(width: int, scale: int) -> None:
    assert edge_map_scale(width) == scale
    assert width / scale <= EDGE_MAP_WIDTH or scale == MAX_EDGE_SCALE


@pytest.mark.parametrize(("w", "h", "scale", "size"), [(1920, 1080, 2, (960, 540)), (1279, 719, 2, (640, 360))])
def test_the_scaled_size_rounds_up(w: int, h: int, scale: int, size: tuple[int, int]) -> None:
    assert edge_map_size(w, h, scale) == size


def test_sample_points_sit_at_fractions_of_the_arc_length() -> None:
    pts = candidate_sample_points([(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)], (0.0, 0.5, 0.75, 1.0))
    assert pts == [(0.0, 0.0), (10.0, 0.0), (10.0, 5.0), (10.0, 10.0)]


def test_sample_points_of_a_point_stay_on_it() -> None:
    assert candidate_sample_points([(3.0, 4.0), (3.0, 4.0)], LINE_SAMPLE_FRACTIONS) == [(3.0, 4.0)] * 5


# --------------------------------------------------------------------------- #
# What is offered
# --------------------------------------------------------------------------- #


def test_a_bowed_edge_is_offered_with_its_points_on_it() -> None:
    p0, p1 = (90.0, 1010.0), (1830.0, 990.0)
    found = find(noisy(strip_frame(p0, p1, BARREL, noise=0.0)))
    assert found, "the strip's edge was not offered"
    best = found[0]
    assert best.length > 0.8 * np.hypot(p1[0] - p0[0], p1[1] - p0[1])
    samples = candidate_sample_points(best.points, LINE_SAMPLE_FRACTIONS)
    distance = distance_to_strip_centre(samples, p0, p1, BARREL)
    # All five on one edge of the strip, each within the scaled pixel the map resolves.
    assert np.all(np.sign(distance) == np.sign(distance[0]))
    np.testing.assert_allclose(np.abs(distance), 7.0, atol=SCALE + 1.0)


def test_an_offered_edge_snaps_at_full_resolution() -> None:
    """Tapping a suggestion traces a line from its ends, which the band snap then refines."""
    p0, p1 = (90.0, 1010.0), (1830.0, 990.0)
    img = noisy(strip_frame(p0, p1, BARREL, noise=0.0))
    samples = candidate_sample_points(find(img)[0].points, LINE_SAMPLE_FRACTIONS)
    pts = snap_line_to_edges(samples[0], samples[4], cut_band(img, samples[0], samples[4]), W, H)
    assert all(p.snapped for p in pts)
    np.testing.assert_allclose(
        np.abs(distance_to_strip_centre([[p.x, p.y] for p in pts], p0, p1, BARREL)), 7.0, atol=1.0
    )
    for p, click in ((pts[0], samples[0]), (pts[4], samples[4])):
        assert np.hypot(p.x - click[0], p.y - click[1]) <= end_search_radius(W)


def test_the_two_edges_of_a_strip_are_one_suggestion() -> None:
    assert len(find(straight_strip((200.0, 300.0), (1700.0, 330.0)))) == 1


def test_a_long_faint_line_is_offered_before_a_short_bright_one() -> None:
    """A line's worth to the fit grows with its length; brightness beyond clear adds nothing."""
    img = straight_strip((300.0, 300.0), (1500.0, 300.0), bright=DARK + 40.0)
    img = straight_strip((800.0, 780.0), (1100.0, 780.0), base=img, bright=BRIGHT)
    found = find(img)
    assert len(found) == 2
    assert found[0].length > 1000.0


def test_a_line_far_from_the_centre_is_offered_first() -> None:
    img = straight_strip((400.0, 540.0), (1500.0, 540.0))
    img = straight_strip((400.0, 60.0), (1500.0, 60.0), base=img)
    found = find(img)
    assert len(found) == 2
    assert abs(ends_of(found[0])[0][1] - 60.0) < 12.0


def test_a_crossing_offers_both_lines_whole() -> None:
    """Each line's edge is interrupted where the other strip covers it, and is joined across the gap."""
    img = straight_strip((300.0, 540.0), (1600.0, 540.0))
    img = straight_strip((960.0, 100.0), (960.0, 1000.0), base=img)
    found = find(img)
    assert len(found) == 2
    spans = sorted(
        (abs(float(ends_of(c)[1][0] - ends_of(c)[0][0])), abs(float(ends_of(c)[1][1] - ends_of(c)[0][1])))
        for c in found
    )
    assert spans[0][0] < 12.0 and spans[0][1] > 850.0
    assert spans[1][0] > 1250.0 and spans[1][1] < 12.0


def test_a_tape_grid_offers_its_lines_whole() -> None:
    img = np.full((H, W), DARK)
    for y in (300.0, 500.0, 700.0):
        img = straight_strip((200.0, y), (1700.0, y), base=img, half_width=3.0)
    for x in (400.0, 700.0, 1000.0, 1300.0):
        img = straight_strip((x, 150.0), (x, 850.0), base=img, half_width=3.0)
    found = find(img)
    assert len(found) == 7
    assert all(c.length > 650.0 for c in found)


def test_a_line_crossed_twice_is_joined_whatever_the_order_of_its_gaps() -> None:
    """The wider strip leaves the larger gap, so the right-hand gap closes first and the left joins a run."""
    img = straight_strip((200.0, 500.0), (1700.0, 500.0))
    img = straight_strip((600.0, 150.0), (600.0, 850.0), base=img, half_width=9.0)
    img = straight_strip((1300.0, 150.0), (1300.0, 850.0), base=img, half_width=3.0)
    found = find(img)
    assert len(found) == 3
    lengths = sorted(c.length for c in found)
    assert lengths[0] > 600.0 and lengths[1] > 600.0 and lengths[2] > 1400.0


def test_pieces_meeting_at_an_angle_are_not_joined() -> None:
    """A small gap between two straight strips that meet at 15 degrees: aligned enough to try, but no one line."""
    img = straight_strip((200.0, 500.0), (900.0, 500.0), half_width=3.0)
    img = straight_strip((910.0, 500.0), (1586.0, 681.0), base=img, half_width=3.0)
    found = find(img)
    assert len(found) == 2
    assert all(c.length < 800.0 for c in found)


def test_pieces_offset_sideways_are_not_joined() -> None:
    """Two collinear-looking strips a few pixels apart across the line are not one edge."""
    img = straight_strip((200.0, 500.0), (900.0, 500.0), half_width=3.0)
    img = straight_strip((930.0, 516.0), (1700.0, 516.0), base=img, half_width=3.0)
    found = find(img)
    assert len(found) == 2
    assert all(c.length < 800.0 for c in found)


def test_a_u_shape_is_cut_into_its_three_straight_sides() -> None:
    img = straight_strip((300.0, 200.0), (1600.0, 200.0))
    img = straight_strip((300.0, 200.0), (300.0, 900.0), base=img)
    img = straight_strip((1600.0, 200.0), (1600.0, 900.0), base=img)
    found = find(img)
    assert len(found) == 3
    for c in found:
        a, b = ends_of(c)
        # Each side runs along one axis only: no suggestion turns the corner.
        assert min(abs(a[0] - b[0]), abs(a[1] - b[1])) < 12.0


def test_edges_close_together_are_thinned_out_but_not_those_far_apart() -> None:
    treads = np.full((H, W), DARK)
    for k in range(5):
        treads = straight_strip((700.0, 700.0 + 40.0 * k), (1300.0, 700.0 + 40.0 * k), base=treads)
    assert 1 <= len(find(treads)) <= 2
    apart = np.full((H, W), DARK)
    for y in (150.0, 500.0, 850.0):
        apart = straight_strip((300.0, y), (1600.0, y), base=apart)
    assert len(find(apart)) == 3


def test_the_limit_caps_the_suggestions() -> None:
    img = np.full((H, W), DARK)
    for y in (150.0, 500.0, 850.0):
        img = straight_strip((300.0, y), (1600.0, y), base=img)
    assert len(find(img, limit=2)) == 2
    assert MAX_CANDIDATES >= 3


# --------------------------------------------------------------------------- #
# Thin lines, at the size a soft 720p camera is analysed at
# --------------------------------------------------------------------------- #

W720, H720 = 1280, 720


def one_pixel_lines(contrast: float, *, floor: float = 120.0) -> np.ndarray:
    """A 720p floor with a one-pixel seam across and one down, ``contrast`` above or below the floor."""
    yy, xx = np.mgrid[0:H720, 0:W720]
    img = np.full((H720, W720), floor)
    img[np.abs(yy - (400.0 + 0.03 * xx)) < 0.5] += contrast
    img[(np.abs(xx - (700.0 + 0.02 * yy)) < 0.5) & (yy > 100) & (yy < 650)] += contrast
    rng = np.random.default_rng(2)
    return np.clip(img + rng.normal(0.0, 1.5, img.shape), 0, 255)


def find_720(img: np.ndarray):
    assert edge_map_scale(W720) == 1
    return find_edge_candidates(img, 1, W720, H720).candidates


@pytest.mark.parametrize("contrast", [40.0, -40.0], ids=["bright-seam", "dark-seam"])
def test_a_one_pixel_seam_is_offered_whole(contrast: float) -> None:
    found = find_720(one_pixel_lines(contrast))
    assert len(found) == 2
    spans = sorted(
        (abs(float(ends_of(c)[1][0] - ends_of(c)[0][0])), abs(float(ends_of(c)[1][1] - ends_of(c)[0][1])))
        for c in found
    )
    assert spans[0][1] > 500.0 and spans[1][0] > 1200.0


def test_a_seam_under_the_weak_level_is_not_offered() -> None:
    assert find_720(one_pixel_lines(10.0)) == []


def test_a_step_edge_has_no_line_beside_it() -> None:
    """The flanks of a step curve too, but carry the gradient: only the step itself is offered."""
    yy, xx = np.mgrid[0:H720, 0:W720]
    halves = np.where(yy < 360 + 0.05 * xx, 60.0, 180.0)
    result = find_edge_candidates(halves, 1, W720, H720)
    assert len(result.candidates) == 1
    ys, xs = np.nonzero(result.edge_map)
    assert np.all(np.abs(ys - (360.0 + 0.05 * xs)) <= 2.0)


# --------------------------------------------------------------------------- #
# What is not offered
# --------------------------------------------------------------------------- #


def test_a_spotlight_rim_is_not_offered() -> None:
    yy, xx = np.mgrid[0:H, 0:W]
    disc = np.where(np.hypot(xx - 900.0, yy - 500.0) <= 150.0, BRIGHT, DARK)
    assert find(disc) == []


def test_a_wavy_edge_is_not_offered() -> None:
    yy, xx = np.mgrid[0:H, 0:W]
    wave = np.where(yy > 500.0 + 8.0 * np.sin(2.0 * np.pi * xx / 600.0), BRIGHT, DARK)
    assert find(wave) == []


def test_a_short_edge_is_not_offered() -> None:
    assert find(straight_strip((900.0, 300.0), (1050.0, 300.0))) == []
    assert len(find(straight_strip((800.0, 300.0), (1100.0, 300.0)))) == 1


@pytest.mark.parametrize("contrast", [12.0, 20.0], ids=["below-the-weak-level", "weak-but-never-strong"])
def test_a_faint_edge_is_not_offered(contrast: float) -> None:
    assert find(straight_strip((200.0, 300.0), (1700.0, 330.0), bright=DARK + contrast)) == []


def test_noise_alone_offers_nothing() -> None:
    assert find(noisy(np.full((H, W), DARK), sigma=6.0)) == []


def test_the_picture_border_is_no_edge() -> None:
    yy, xx = np.mgrid[0:H, 0:W]
    halves = np.where(xx < 960, BRIGHT, DARK)
    found = find(halves)
    assert len(found) == 1
    a, b = ends_of(found[0])
    assert abs(a[0] - 960.0) < 6.0 and abs(b[0] - 960.0) < 6.0


def test_the_edge_map_marks_the_strip_only() -> None:
    p0, p1 = (200.0, 300.0), (1700.0, 330.0)
    result = find_edge_candidates(scaled_luma(straight_strip(p0, p1), SCALE), SCALE, W, H)
    ys, xs = np.nonzero(result.edge_map)
    assert len(ys) > 500
    marked = np.column_stack([xs, ys]) * SCALE + (SCALE - 1) / 2.0
    # Away from the strip's rounded ends, every marked cell lies on one of its two edges.
    along = (marked[:, 0] - p0[0]) / (p1[0] - p0[0])
    between = (along > 0.02) & (along < 0.98)
    assert between.sum() > 400
    distance = distance_to_strip_centre(marked[between], p0, p1, STRAIGHT)
    assert np.all(np.abs(np.abs(distance) - 7.0) <= 2.0 * SCALE)
    assert result.edge_map.dtype == np.uint8
    flat = find_edge_candidates(scaled_luma(np.full((H, W), DARK), SCALE), SCALE, W, H)
    assert not flat.edge_map.any() and flat.candidates == []


# --------------------------------------------------------------------------- #
# Input rules
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("scale", [0, MAX_EDGE_SCALE + 1])
def test_a_scale_outside_the_range_is_refused(scale: int) -> None:
    with pytest.raises(ValueError, match="scale"):
        find_edge_candidates(np.zeros((540, 960)), scale, W, H)


@pytest.mark.parametrize("shape", [(540, 961), (539, 960), (540, 960, 3), (960,)])
def test_a_luma_that_is_not_the_scaled_snapshot_is_refused(shape: tuple[int, ...]) -> None:
    with pytest.raises(ValueError, match="scaled"):
        find_edge_candidates(np.zeros(shape), 2, W, H)
