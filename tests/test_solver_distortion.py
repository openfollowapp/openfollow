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
from hypothesis import given
from hypothesis import strategies as st

from openfollow.scene.solver import apply_overlay_distortion, invert_overlay_distortion

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


# Mirror the CameraConfig clamps.
_K1 = st.floats(min_value=-0.4, max_value=0.4)
_K2 = st.floats(min_value=-0.2, max_value=0.2)
# Undistorted-input disk: where the forward map is a clean bijection for every
# coeff in range, so apply -> invert recovers the original. Even at k1=-0.4,
# k2=-0.2 the map stays monotonic well past r=0.6, so a pinhole point this far
# out has a valid distorted image to invert back.
_R_EDGE = 0.6  # normalised working-disk radius (1.0 == frame corner)
_R = _R_EDGE * _HALF_DIAG
_OFFSET = st.floats(min_value=-_R / math.sqrt(2), max_value=_R / math.sqrt(2))


@st.composite
def _reachable_distorted(draw: st.DrawFn) -> tuple[float, float, float, float]:
    """An ``(ox, oy, k1, k2)`` whose distorted point is reachable for that pair.

    The invert -> apply round-trip is identity only for *reachable* distorted
    points: a strong barrel folds the forward map at r_u~0.75, and beyond the
    fold there is no preimage (invert floors the point – bounded, not identity;
    covered by the out-of-domain test below). The forward image of the working
    disk, ``apply(_R_EDGE)``, is the reachable cap for each coefficient pair –
    it shrinks under barrel (~0.50) and grows under pincushion (~0.70), and stays
    below the fold (its preimage r_u <= _R_EDGE), so the inverse is well
    conditioned. Drawing the distorted radius *freely* within that per-coeff cap
    (not as the forward image of a fixed pinhole point) exercises
    ``apply o invert == id`` over the whole reachable range for every coefficient,
    independently of the inverse-then-forward direction.
    """
    k1 = draw(_K1)
    k2 = draw(_K2)
    r_d_max = _R_EDGE * (1.0 + k1 * _R_EDGE**2 + k2 * _R_EDGE**4)
    frac = draw(st.floats(min_value=0.0, max_value=1.0))
    theta = draw(st.floats(min_value=0.0, max_value=2.0 * math.pi))
    r = frac * r_d_max * _HALF_DIAG
    return r * math.cos(theta), r * math.sin(theta), k1, k2


@given(ox=_OFFSET, oy=_OFFSET, k1=_K1, k2=_K2)
def test_inverse_round_trips_forward(ox: float, oy: float, k1: float, k2: float) -> None:
    pt = np.array([[_CX + ox, _CY + oy]])
    back = invert_overlay_distortion(apply_overlay_distortion(pt, _W, _H, k1, k2), _W, _H, k1, k2)
    # Sub-pixel agreement on a 1080p frame across the whole clamped range.
    np.testing.assert_allclose(back, pt, atol=0.5)


@given(params=_reachable_distorted())
def test_forward_round_trips_inverse(params: tuple[float, float, float, float]) -> None:
    ox, oy, k1, k2 = params
    pt = np.array([[_CX + ox, _CY + oy]])
    fwd = apply_overlay_distortion(invert_overlay_distortion(pt, _W, _H, k1, k2), _W, _H, k1, k2)
    np.testing.assert_allclose(fwd, pt, atol=0.5)


def test_inverse_stays_bounded_for_out_of_domain_corner() -> None:
    # Under strong barrel a frame-corner click has no undistorted preimage; the
    # floored iteration must return a finite, sane point (no divergence to inf).
    corner = np.array([[_W, _H]])
    out = invert_overlay_distortion(corner, _W, _H, -0.4, -0.2)
    assert np.all(np.isfinite(out))
    r_out = math.hypot(out[0, 0] - _CX, out[0, 1] - _CY)
    # Bounded by the factor floor (1/0.2 = 5x), not runaway.
    assert r_out < 6.0 * _HALF_DIAG
