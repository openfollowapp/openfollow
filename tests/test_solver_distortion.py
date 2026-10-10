# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for the overlay radial-distortion warp + its inverse.

The forward warp bows projected HUD points to match a fisheye / wide-angle
lens; the inverse maps a click / detection point on the distorted video back to
the pinhole frame before unprojection. Both must be identity when the
coefficients are zero (so the pinhole overlay path is untouched), purely radial,
and true inverses of each other.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from openfollow.lens_model import lens_fold_radius, lens_warp_is_valid
from openfollow.scene.solver import apply_overlay_distortion, invert_normalised_radius, invert_overlay_distortion

pytestmark = pytest.mark.unit

_W = 1920.0
_H = 1080.0
_HALF_DIAG = 0.5 * math.hypot(_W, _H)
_CX, _CY = _W / 2.0, _H / 2.0


def test_forward_identity_when_coeffs_zero() -> None:
    pts = np.array([[10.0, 20.0], [1900.0, 5.0], [_CX, _CY]])
    out = apply_overlay_distortion(pts, _W, _H, 0.0, 0.0)
    np.testing.assert_array_equal(out, pts)


def test_inverse_identity_when_coeffs_zero() -> None:
    pts = np.array([[10.0, 20.0], [1900.0, 5.0], [_CX, _CY]])
    out = invert_overlay_distortion(pts, _W, _H, 0.0, 0.0)
    np.testing.assert_array_equal(out, pts)


def test_center_is_fixed_point() -> None:
    # The image centre sits at r=0, so any coefficient leaves it untouched.
    centre = np.array([[_CX, _CY]])
    fwd = apply_overlay_distortion(centre, _W, _H, -0.3, 0.1)
    inv = invert_overlay_distortion(centre, _W, _H, -0.3, 0.1)
    np.testing.assert_allclose(fwd, centre)
    np.testing.assert_allclose(inv, centre)


def test_forward_matches_hand_computed_factor() -> None:
    # A point one half-diagonal to the right of centre has r=1, so f = 1 + k1.
    k1 = -0.2
    pt = np.array([[_CX + _HALF_DIAG, _CY]])
    out = apply_overlay_distortion(pt, _W, _H, k1, 0.0)
    expected_x = _CX + _HALF_DIAG * (1.0 + k1)
    np.testing.assert_allclose(out, [[expected_x, _CY]])


def test_negative_k1_pulls_edges_inward() -> None:
    # Barrel / fisheye: a near-corner point moves toward the centre.
    pt = np.array([[_W - 1.0, _H - 1.0]])
    out = apply_overlay_distortion(pt, _W, _H, -0.2, 0.0)
    r_in = math.hypot(pt[0, 0] - _CX, pt[0, 1] - _CY)
    r_out = math.hypot(out[0, 0] - _CX, out[0, 1] - _CY)
    assert r_out < r_in


def test_positive_k1_pushes_edges_outward() -> None:
    # Pincushion: a near-corner point moves away from the centre.
    pt = np.array([[_W - 1.0, _H - 1.0]])
    out = apply_overlay_distortion(pt, _W, _H, 0.2, 0.0)
    r_in = math.hypot(pt[0, 0] - _CX, pt[0, 1] - _CY)
    r_out = math.hypot(out[0, 0] - _CX, out[0, 1] - _CY)
    assert r_out > r_in


def test_displacement_is_purely_radial() -> None:
    # The warped point stays on the ray from the centre through the input.
    pt = np.array([[1700.0, 300.0]])
    out = apply_overlay_distortion(pt, _W, _H, -0.2, 0.05)
    v_in = pt[0] - np.array([_CX, _CY])
    v_out = out[0] - np.array([_CX, _CY])
    cross = v_in[0] * v_out[1] - v_in[1] * v_out[0]
    assert abs(cross) < 1e-6


def test_k2_adds_higher_order_edge_correction() -> None:
    # At a corner (r=1) k2 contributes on top of k1; the warped radius differs
    # from a k1-only warp, proving the r^4 term is wired in.
    pt = np.array([[_CX + _HALF_DIAG, _CY]])
    k1_only = apply_overlay_distortion(pt, _W, _H, -0.2, 0.0)
    with_k2 = apply_overlay_distortion(pt, _W, _H, -0.2, 0.05)
    assert with_k2[0, 0] > k1_only[0, 0]


def test_nan_rows_pass_through() -> None:
    pts = np.array([[np.nan, np.nan], [1700.0, 300.0]])
    out = apply_overlay_distortion(pts, _W, _H, -0.2, 0.0)
    assert not np.all(np.isfinite(out[0]))
    assert np.all(np.isfinite(out[1]))


# Any pair that does not fold inside the frame is a valid lens: the forward map
# is one-to-one over every pinhole point of the frame, and the Newton inverse
# recovers each exactly, strong barrel and pincushion alike. A screen point past
# what the warp reaches has no preimage and lands on the fold ring.
_K1 = st.floats(min_value=-0.8, max_value=0.8)
_K2 = st.floats(min_value=-0.5, max_value=0.5)
_X = st.floats(min_value=0.0, max_value=_W)
_Y = st.floats(min_value=0.0, max_value=_H)


@given(x=_X, y=_Y, k1=_K1, k2=_K2)
def test_inverse_round_trips_forward_over_the_whole_frame(x: float, y: float, k1: float, k2: float) -> None:
    assume(lens_warp_is_valid(k1, k2))
    pt = np.array([[x, y]])
    back = invert_overlay_distortion(apply_overlay_distortion(pt, _W, _H, k1, k2), _W, _H, k1, k2)
    np.testing.assert_allclose(back, pt, atol=1e-6)


@given(x=_X, y=_Y, k1=_K1, k2=_K2)
def test_forward_round_trips_inverse_for_reachable_points(x: float, y: float, k1: float, k2: float) -> None:
    # A screen point the warp can reach (one inside the forward image of the
    # frame) comes back exactly; the frame corner maps to r = f(1), so every
    # point within that radius qualifies.
    assume(lens_warp_is_valid(k1, k2))
    pt = np.array([[x, y]])
    r = math.hypot(x - _CX, y - _CY) / _HALF_DIAG
    assume(r <= 1.0 + k1 + k2 - 1e-6)
    fwd = apply_overlay_distortion(invert_overlay_distortion(pt, _W, _H, k1, k2), _W, _H, k1, k2)
    np.testing.assert_allclose(fwd, pt, atol=1e-6)


@pytest.mark.parametrize("k1,k2", [(-0.3, 0.0), (-0.45, 0.2), (-0.47, 0.25), (0.6, 0.4), (5.0, 0.0)])
def test_strong_lenses_invert_to_the_pixel_everywhere(k1: float, k2: float) -> None:
    rng = np.random.default_rng(0)
    pts = np.column_stack([rng.uniform(0.0, _W, 500), rng.uniform(0.0, _H, 500)])
    back = invert_overlay_distortion(apply_overlay_distortion(pts, _W, _H, k1, k2), _W, _H, k1, k2)
    assert np.max(np.hypot(*(back - pts).T)) < 1e-6


@pytest.mark.parametrize("k1", [-0.8, -0.7, -0.6, -0.47, -0.38, -0.3, -0.19])
@pytest.mark.parametrize("excess", [1e-12, 1e-9, 1e-4, 1e-2])
def test_the_inverse_converges_where_the_warp_barely_climbs(k1: float, excess: float) -> None:
    # Just above k2 = 9 k1^2 / 20 the warp's slope nearly touches zero past the frame
    # without folding, and Newton creeps there: twelve steps missed by up to 108 px.
    k2 = 9.0 * k1 * k1 / 20.0 * (1.0 + excess)
    assert lens_warp_is_valid(k1, k2)
    rd = np.linspace(0.0, 1.0, 2001)
    ru = invert_normalised_radius(rd, k1, k2)
    assert np.max(np.abs(ru * (1.0 + k1 * ru**2 + k2 * ru**4) - rd)) * _HALF_DIAG < 0.01


def test_inverse_stays_bounded_for_out_of_domain_corner() -> None:
    # Under strong barrel a frame-corner click has no undistorted preimage; the
    # inverse lands on the fold radius, a finite point just past where the warp
    # stops growing, never a runaway.
    corner = np.array([[_W, _H]])
    k1, k2 = -0.3, 0.0
    out = invert_overlay_distortion(corner, _W, _H, k1, k2)
    assert np.all(np.isfinite(out))
    r_out = math.hypot(out[0, 0] - _CX, out[0, 1] - _CY) / _HALF_DIAG
    # Bisection closes on the fold radius at one bit per step, so a few 1e-5 remain.
    assert r_out == pytest.approx(lens_fold_radius(k1, k2), abs=1e-3)


def test_a_folding_pair_still_inverts_inside_its_fold() -> None:
    # The function accepts any pair; a reachable point under a pair the config
    # would refuse still comes back, so an old hand-edited file cannot crash the input path.
    pt = np.array([[_CX + 0.3 * _HALF_DIAG, _CY]])
    k1, k2 = -0.4, -0.2
    back = invert_overlay_distortion(apply_overlay_distortion(pt, _W, _H, k1, k2), _W, _H, k1, k2)
    np.testing.assert_allclose(back, pt, atol=1e-6)


def test_normalised_radius_broadcasts_a_grid_of_coefficients() -> None:
    # The lens fit scans a grid of pairs in one call: radii on one axis, pairs on the other.
    r_d = np.array([0.0, 0.3, 0.6])[:, None]
    k1 = np.array([0.0, -0.2, 0.3])[None, :]
    k2 = np.array([0.0, 0.05, 0.0])[None, :]
    r_u = invert_normalised_radius(r_d, k1, k2)
    assert r_u.shape == (3, 3)
    np.testing.assert_allclose(r_u[:, 0], r_d[:, 0])
    np.testing.assert_allclose(r_u * (1.0 + k1 * r_u**2 + k2 * r_u**4), np.broadcast_to(r_d, (3, 3)), atol=1e-12)


def test_nan_passes_through_the_inverse() -> None:
    pts = np.array([[np.nan, np.nan], [1700.0, 300.0]])
    out = invert_overlay_distortion(pts, _W, _H, -0.3, 0.05)
    assert not np.all(np.isfinite(out[0]))
    assert np.all(np.isfinite(out[1]))
