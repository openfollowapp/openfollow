# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Edge snap on synthetic images: one polarity per line, no-edge points marked."""

from __future__ import annotations

import numpy as np
import pytest

from openfollow.scene.edge_snap import (
    LINE_SAMPLE_FRACTIONS,
    MAX_PATCH_HALF,
    MIN_PATCH_HALF,
    SnapPatch,
    line_sample_points,
    patch_half_size,
    search_radius,
    snap_line_to_edges,
)

pytestmark = pytest.mark.unit

W, H = 1920, 1080
HALF = patch_half_size(W)
RADIUS = search_radius(HALF)
STRIPE_HALF_WIDTH = 7
STRIPE_SLOPE = 0.02


def stripe_centre(x: float) -> float:
    return 600.0 + STRIPE_SLOPE * x


@pytest.fixture(scope="module")
def stripe() -> np.ndarray:
    """A dark floor with a bright strip of tape across it, slightly tilted, with sensor noise."""
    rng = np.random.default_rng(0)
    img = np.full((H, W), 40.0)
    yy, xx = np.mgrid[0:H, 0:W]
    img[np.abs(yy - stripe_centre(xx)) <= STRIPE_HALF_WIDTH] = 200.0
    return img + rng.normal(0.0, 4.0, img.shape)


def patches_for(img: np.ndarray, p0, p1, half: int = HALF) -> list[SnapPatch]:
    """Cut the patches the wizard would send: axis-aligned crops around the sample points."""
    out = []
    for q in line_sample_points(p0, p1):
        x0 = int(round(q[0])) - half
        y0 = int(round(q[1])) - half
        xa, ya = max(x0, 0), max(y0, 0)
        xb, yb = min(x0 + 2 * half + 1, img.shape[1]), min(y0 + 2 * half + 1, img.shape[0])
        out.append(SnapPatch(xa, ya, img[ya:yb, xa:xb].astype(np.float64)))
    return out


def test_sample_points_sit_at_the_five_fractions() -> None:
    pts = line_sample_points((100.0, 200.0), (500.0, 600.0))
    assert pts.shape == (5, 2)
    assert LINE_SAMPLE_FRACTIONS == (0.0, 0.25, 0.5, 0.75, 1.0)
    np.testing.assert_allclose(pts[2], [300.0, 400.0])


@pytest.mark.parametrize(
    "width,half", [(640, MIN_PATCH_HALF), (1920, 32), (3840, MAX_PATCH_HALF), (9999, MAX_PATCH_HALF)]
)
def test_patch_size_scales_with_the_snapshot(width: float, half: int) -> None:
    assert patch_half_size(width) == half
    assert 4 <= search_radius(patch_half_size(width)) < patch_half_size(width)


def test_points_land_on_the_edge_the_clicks_were_near(stripe: np.ndarray) -> None:
    top = -STRIPE_HALF_WIDTH
    p0 = (200.0, stripe_centre(200.0) + top + 5.0)
    p1 = (1700.0, stripe_centre(1700.0) + top - 4.0)
    pts = snap_line_to_edges(p0, p1, patches_for(stripe, p0, p1), radius=RADIUS)
    assert len(pts) == 5
    for p in pts:
        assert p.snapped
        assert abs(p.y - (stripe_centre(p.x) + top)) < 0.5


def test_one_polarity_per_line_even_when_the_clicks_straddle_the_tape(stripe: np.ndarray) -> None:
    # Start nearer the top edge, end nearer the bottom edge: the tape's width must not read as curvature.
    p0 = (200.0, stripe_centre(200.0) - 5.0)
    p1 = (1700.0, stripe_centre(1700.0) + 5.0)
    pts = snap_line_to_edges(p0, p1, patches_for(stripe, p0, p1), radius=RADIUS)
    offsets = [p.y - stripe_centre(p.x) for p in pts]
    on_top = [abs(o + STRIPE_HALF_WIDTH) < 0.5 for o in offsets]
    on_bottom = [abs(o - STRIPE_HALF_WIDTH) < 0.5 for o in offsets]
    assert all(on_top) or all(on_bottom)


def test_a_point_without_a_clear_edge_stays_on_the_line_and_is_marked(stripe: np.ndarray) -> None:
    rng = np.random.default_rng(1)
    img = stripe.copy()
    img[:, 900:1000] = 40.0 + rng.normal(0.0, 4.0, (H, 100))  # the tape is covered at the middle point
    p0 = (200.0, stripe_centre(200.0) - STRIPE_HALF_WIDTH)
    p1 = (1700.0, stripe_centre(1700.0) - STRIPE_HALF_WIDTH)
    pts = snap_line_to_edges(p0, p1, patches_for(img, p0, p1), radius=RADIUS)
    assert [p.snapped for p in pts] == [True, True, False, True, True]
    nominal = line_sample_points(p0, p1)[2]
    assert (pts[2].x, pts[2].y) == pytest.approx(tuple(nominal))


def test_noise_alone_snaps_nothing() -> None:
    rng = np.random.default_rng(2)
    img = 100.0 + rng.normal(0.0, 4.0, (H, W))
    p0, p1 = (200.0, 600.0), (1700.0, 640.0)
    pts = snap_line_to_edges(p0, p1, patches_for(img, p0, p1), radius=RADIUS)
    assert not any(p.snapped for p in pts)
    np.testing.assert_allclose([[p.x, p.y] for p in pts], line_sample_points(p0, p1))


def test_a_vertical_edge_is_found_to_subpixel_precision() -> None:
    rng = np.random.default_rng(3)
    img = np.full((H, W), 60.0)
    img[:, 700:] = 180.0
    img += rng.normal(0.0, 3.0, img.shape)
    p0, p1 = (704.0, 100.0), (697.0, 900.0)
    pts = snap_line_to_edges(p0, p1, patches_for(img, p0, p1), radius=RADIUS)
    assert all(p.snapped for p in pts)
    assert all(abs(p.x - 699.5) < 0.3 for p in pts)


def test_a_patch_cut_short_by_the_image_border_still_snaps(stripe: np.ndarray) -> None:
    p0, p1 = (3.0, stripe_centre(3.0) - STRIPE_HALF_WIDTH + 4.0), (1500.0, stripe_centre(1500.0) - STRIPE_HALF_WIDTH)
    pts = snap_line_to_edges(p0, p1, patches_for(stripe, p0, p1), radius=RADIUS)
    assert pts[0].snapped
    assert abs(pts[0].y - (stripe_centre(pts[0].x) - STRIPE_HALF_WIDTH)) < 0.5


def test_an_edge_outside_the_search_radius_is_not_taken(stripe: np.ndarray) -> None:
    far = RADIUS + 8
    p0 = (200.0, stripe_centre(200.0) - STRIPE_HALF_WIDTH - far)
    p1 = (1700.0, stripe_centre(1700.0) - STRIPE_HALF_WIDTH - far)
    pts = snap_line_to_edges(p0, p1, patches_for(stripe, p0, p1, half=HALF + 8), radius=RADIUS)
    assert not any(p.snapped for p in pts)


@pytest.mark.parametrize("count", [0, 4, 6])
def test_wrong_patch_count_is_an_error(stripe: np.ndarray, count: int) -> None:
    p0, p1 = (200.0, 600.0), (1700.0, 640.0)
    patches = (
        patches_for(stripe, p0, p1)[:count]
        if count < 5
        else patches_for(stripe, p0, p1) + [SnapPatch(0, 0, stripe[:5, :5])]
    )
    with pytest.raises(ValueError, match="patches"):
        snap_line_to_edges(p0, p1, patches, radius=RADIUS)


def test_a_zero_length_line_is_an_error(stripe: np.ndarray) -> None:
    patches = patches_for(stripe, (200.0, 600.0), (200.0, 600.0))
    with pytest.raises(ValueError, match="too short"):
        snap_line_to_edges((200.0, 600.0), (200.0, 600.0), patches, radius=RADIUS)


def test_a_zero_radius_is_an_error(stripe: np.ndarray) -> None:
    p0, p1 = (200.0, 600.0), (1700.0, 640.0)
    with pytest.raises(ValueError, match="radius"):
        snap_line_to_edges(p0, p1, patches_for(stripe, p0, p1), radius=0)


def test_a_patch_that_misses_its_point_finds_no_edge(stripe: np.ndarray) -> None:
    # The wizard cuts each patch around its own sample point; one cut elsewhere holds nothing to read.
    p0, p1 = (200.0, stripe_centre(200.0) - STRIPE_HALF_WIDTH), (1700.0, stripe_centre(1700.0) - STRIPE_HALF_WIDTH)
    patches = patches_for(stripe, p0, p1)
    patches[2] = SnapPatch(0, 0, stripe[:40, :40].astype(np.float64))
    pts = snap_line_to_edges(p0, p1, patches, radius=RADIUS)
    assert [p.snapped for p in pts] == [True, True, False, True, True]


def test_an_edge_at_the_search_radius_is_taken_without_refinement() -> None:
    # The strongest gradient sits on the last offset, where no neighbour exists for the parabola.
    img = np.full((H, W), 60.0)
    edge_x = 800
    img[:, edge_x:] = 180.0
    p0, p1 = (edge_x - 0.5 - RADIUS, 100.0), (edge_x - 0.5 - RADIUS, 900.0)
    pts = snap_line_to_edges(p0, p1, patches_for(img, p0, p1, half=HALF + 4), radius=RADIUS)
    assert all(p.snapped for p in pts)
    assert all(abs(abs(p.x - p0[0]) - RADIUS) < 1e-9 for p in pts)


def test_a_patch_cut_short_beside_the_edge_snaps_without_refinement() -> None:
    # The gradient next to the peak falls outside the patch, so the parabola has no left neighbour.
    img = np.full((H, W), 60.0)
    edge_x = 800
    img[:, edge_x:] = 180.0
    p0, p1 = (edge_x - 0.5, 100.0), (edge_x - 0.5, 900.0)
    patches = [
        SnapPatch(
            edge_x - 2, int(round(q[1])) - 4, img[int(round(q[1])) - 4 : int(round(q[1])) + 5, edge_x - 2 : edge_x + 12]
        )
        for q in line_sample_points(p0, p1)
    ]
    pts = snap_line_to_edges(p0, p1, patches, radius=RADIUS)
    assert all(p.snapped for p in pts)
    assert all(abs(p.x - (edge_x - 0.5)) < 1.0 for p in pts)


def test_a_soft_ramp_edge_snaps_onto_the_ramp() -> None:
    # A linear ramp has a flat gradient: the parabola through three equal values is no peak to refine.
    img = np.full((H, W), 60.0)
    ramp_x = 800
    for i in range(8):
        img[:, ramp_x + i] = 60.0 + 15.0 * i
    img[:, ramp_x + 8 :] = 180.0
    p0, p1 = (ramp_x + 3.0, 100.0), (ramp_x + 3.0, 900.0)
    pts = snap_line_to_edges(p0, p1, patches_for(img, p0, p1), radius=RADIUS)
    assert all(p.snapped for p in pts)
    assert all(ramp_x <= p.x <= ramp_x + 8 for p in pts)
