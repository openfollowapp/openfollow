# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The overlay's lens warp, both directions, against hand-computed values.

``invert_overlay_distortion`` takes a screen point to the pinhole frame in
closed form; ``apply_overlay_distortion`` bows a pinhole point onto the
lens-distorted picture by inverting it. Both are identity without a lens and
purely radial about the lens centre, which only translates the picture.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from openfollow.lens_model import C_BOUND, lens_limit_radius, lens_warp_is_valid
from openfollow.scene import solver
from openfollow.scene.solver import apply_overlay_distortion, distort_normalised_radius, invert_overlay_distortion

pytestmark = pytest.mark.unit

_W = 1920.0
_H = 1080.0
_D = 0.5 * math.hypot(_W, _H)
_F = np.array([_W / 2.0, _H / 2.0])
# A direction off both axes, so a swapped x and y would show.
_DIR = np.array([math.cos(0.6), math.sin(0.6)])


def _at(radius: float, centre: tuple[float, float] = (0.0, 0.0)) -> np.ndarray:
    """The pixel ``radius`` half-diagonals from ``centre`` (half-diagonals from the frame centre) along ``_DIR``."""
    return (_F + _D * (np.asarray(centre) + radius * _DIR))[None, :]


def _invert(pts: np.ndarray, *lens: float) -> np.ndarray:
    return invert_overlay_distortion(pts, _W, _H, *lens)


def _apply(pts: np.ndarray, *lens: float) -> np.ndarray:
    return apply_overlay_distortion(pts, _W, _H, *lens)


# --- Without a lens ---------------------------------------------------------


@pytest.mark.parametrize("warp", [apply_overlay_distortion, invert_overlay_distortion])
def test_both_directions_are_identity_without_a_lens(warp) -> None:  # noqa: ANN001
    pts = np.array([[10.0, 20.0], [1900.0, 5.0], [_W / 2.0, _H / 2.0], [np.nan, np.nan]])
    np.testing.assert_array_equal(warp(pts, _W, _H, 0.0, 0.0, 0.0, 0.0), pts)


# --- Screen to pinhole: closed form -----------------------------------------


def test_a_screen_radius_maps_to_its_pinhole_radius_by_the_division_model() -> None:
    # k = (-0.2, 0): screen radius 0.5 is pinhole radius 0.5 / (1 - 0.2 * 0.25) = 0.5 / 0.95.
    out = _invert(_at(0.5), -0.2, 0.0, 0.0, 0.0)
    np.testing.assert_allclose(out, _at(0.5 / 0.95), rtol=0, atol=1e-9)
    # 0.5 / 0.95 half-diagonals along _DIR, in pixels.
    np.testing.assert_allclose(out, [[960.0 + 579.71241 * math.cos(0.6), 540.0 + 579.71241 * math.sin(0.6)]], atol=1e-4)


def test_the_corner_term_enters_with_the_fourth_power_of_the_screen_radius() -> None:
    # k = (-0.2, -0.1) at screen radius 0.8: D = 1 - 0.2 * 0.64 - 0.1 * 0.4096.
    out = _invert(_at(0.8), -0.2, -0.1, 0.0, 0.0)
    np.testing.assert_allclose(out, _at(0.8 / (1.0 - 0.128 - 0.04096)), atol=1e-9)


def test_barrel_moves_a_screen_point_outward_in_the_pinhole_frame_and_pincushion_inward() -> None:
    barrel = _invert(_at(1.0), -0.3, 0.0, 0.0, 0.0)
    pincushion = _invert(_at(1.0), 0.3, 0.0, 0.0, 0.0)
    np.testing.assert_allclose(barrel, _at(1.0 / 0.7), atol=1e-9)
    np.testing.assert_allclose(pincushion, _at(1.0 / 1.3), atol=1e-9)


# --- Pinhole to screen: the inverse -----------------------------------------


def test_a_pinhole_radius_maps_back_to_its_screen_radius() -> None:
    out = _apply(_at(0.5 / 0.95), -0.2, 0.0, 0.0, 0.0)
    np.testing.assert_allclose(out, _at(0.5), atol=1e-9)


@pytest.mark.parametrize("k1", [-0.6, -0.2, -1e-9, 1e-9, 0.2, 0.9])
def test_without_a_corner_term_the_inverse_is_its_closed_form(k1: float) -> None:
    r_u = np.linspace(0.0, 2.0, 41)
    reach = 1.0 / (2.0 * math.sqrt(k1)) if k1 > 0.0 else math.inf
    r_c = np.minimum(r_u, reach)
    expected = 2.0 * r_c / (1.0 + np.sqrt(np.maximum(1.0 - 4.0 * k1 * r_c * r_c, 0.0)))
    np.testing.assert_allclose(distort_normalised_radius(r_u, k1, 0.0), expected, rtol=1e-14, atol=0)


def test_barrel_pulls_a_pinhole_point_inward_and_pincushion_pushes_it_outward_by_the_stated_amount() -> None:
    # Pinhole radius 1 with k1 = -0.2 lands at 2 / (1 + sqrt(1.8)); with k1 = 0.2 at 2 / (1 + sqrt(0.2)).
    barrel = _apply(_at(1.0), -0.2, 0.0, 0.0, 0.0)
    pincushion = _apply(_at(1.0), 0.2, 0.0, 0.0, 0.0)
    np.testing.assert_allclose(barrel, _at(0.8541019662496845), atol=1e-9)
    np.testing.assert_allclose(pincushion, _at(1.381966011250105), atol=1e-9)
    # About 161 px inward and 421 px outward on 1080p.
    assert math.dist(barrel[0], _at(1.0)[0]) == pytest.approx(160.700, abs=1e-3)
    assert math.dist(pincushion[0], _at(1.0)[0]) == pytest.approx(420.718, abs=1e-3)


@pytest.mark.parametrize("theta_deg", [5.0, 20.0, 40.0, 60.0, 72.0])
def test_a_stereographic_lens_is_the_k1_only_model_exactly(theta_deg: float) -> None:
    # A ray theta off the axis lands at 2 f tan(theta / 2) through the lens and at f tan(theta) through a
    # pinhole of the same paraxial focal length, f in pixels; k1 = -(D / 2 f)^2.
    f = 900.0
    k1 = -((_D / (2.0 * f)) ** 2)
    theta = math.radians(theta_deg)
    screen = _at(2.0 * f * math.tan(theta / 2.0) / _D)
    pinhole = _at(f * math.tan(theta) / _D)
    np.testing.assert_allclose(_invert(screen, k1, 0.0, 0.0, 0.0), pinhole, rtol=1e-12)
    np.testing.assert_allclose(_apply(pinhole, k1, 0.0, 0.0, 0.0), screen, rtol=1e-12)


def test_an_equidistant_126_degree_lens_is_followed_to_within_a_pixel() -> None:
    # 2.3 mm on 1920x1080: f theta on screen, f tan(theta) in the pinhole frame, out to the corner.
    f = 960.0 / math.radians(63.0)
    theta = np.linspace(0.0, _D / f, 400)
    pinhole = _F + (f * np.tan(theta))[:, None] * _DIR
    seen = _apply(pinhole, -0.527, -0.069, 0.0, 0.0)
    assert np.max(np.abs(np.hypot(*(seen - _F).T) - f * theta)) < 0.7


# --- The lens centre --------------------------------------------------------


def test_a_centre_alone_translates_the_picture() -> None:
    # c = (0.1, 0): the pinhole point is 0.1 D to the left of the screen point, and back.
    pts = np.array([[100.0, 200.0], [1500.0, 900.0]])
    shift = np.array([0.1 * _D, 0.0])
    np.testing.assert_allclose(_invert(pts, 0.0, 0.0, 0.1, 0.0), pts - shift, atol=1e-9)
    np.testing.assert_allclose(_apply(pts, 0.0, 0.0, 0.1, 0.0), pts + shift, atol=1e-9)


def test_the_lens_centre_itself_maps_to_the_frame_centre() -> None:
    centre = (0.1, -0.05)
    np.testing.assert_allclose(_invert(_at(0.0, centre), -0.2, 0.0, *centre), [_F], atol=1e-9)
    np.testing.assert_allclose(_apply(np.array([_F]), -0.2, 0.0, *centre), _at(0.0, centre), atol=1e-9)


def test_the_warp_bends_about_the_lens_centre_and_lands_about_the_frame_centre() -> None:
    # 0.5 half-diagonals right of the lens centre is 0.5 / 0.95 right of the frame centre.
    centre = (0.1, -0.05)
    right = _F + _D * (np.array(centre) + [0.5, 0.0])
    out = _invert(right[None, :], -0.2, 0.0, *centre)
    np.testing.assert_allclose(out, [_F + [_D * 0.5 / 0.95, 0.0]], atol=1e-9)
    np.testing.assert_allclose(_apply(out, -0.2, 0.0, *centre), [right], atol=1e-9)


def test_a_positive_cy_puts_the_lens_centre_below_the_frame_centre() -> None:
    # Image axes: y down. A lens centre 0.1 half-diagonals down sits at pixel row 540 + 110.1.
    below = np.array([[960.0, 650.14536]])
    np.testing.assert_allclose(_invert(below, -0.3, 0.0, 0.0, 0.1), [[960.0, 540.0]], atol=1e-4)


# --- Reach, limits and NaN --------------------------------------------------


@pytest.mark.parametrize("k1,k2", [(-0.53, -0.07), (-0.3, 0.0), (-0.2, -0.5)])
@pytest.mark.parametrize("r_u", [1e3, 1e6])
def test_under_barrel_a_pinhole_point_far_past_the_corner_lands_just_inside_the_pole(
    k1: float, k2: float, r_u: float
) -> None:
    r_d = float(distort_normalised_radius(r_u, k1, k2))
    assert r_d < lens_limit_radius(k1, k2)
    assert r_d / (1.0 + k1 * r_d**2 + k2 * r_d**4) == pytest.approx(r_u, rel=1e-6)
    # A grid of pairs takes the bracketed path and lands on the same radius.
    assert float(distort_normalised_radius([r_u], [k1], [k2])[0]) == pytest.approx(r_d, rel=1e-12)


@pytest.mark.parametrize("k1,k2", [(0.5, 0.0), (0.2, 0.05), (0.0, 0.3)])
def test_past_a_fold_a_pinhole_point_lands_on_the_fold_radius(k1: float, k2: float) -> None:
    assert float(distort_normalised_radius(5.0, k1, k2)) == pytest.approx(lens_limit_radius(k1, k2), rel=1e-6)
    assert float(distort_normalised_radius([5.0], [k1], [k2])[0]) == pytest.approx(lens_limit_radius(k1, k2), rel=1e-6)


def test_a_screen_point_past_the_limit_has_no_pinhole_point() -> None:
    # Not a lens the config keeps (its pole is inside the frame): the far corner has no pinhole point.
    k1 = -1.5
    corner = np.array([[_W, _H], [_W / 2.0 + 10.0, _H / 2.0]])
    out = _invert(corner, k1, 0.0, 0.0, 0.0)
    assert np.isnan(out[0]).all()
    assert np.isfinite(out[1]).all()


@pytest.mark.parametrize("warp", [apply_overlay_distortion, invert_overlay_distortion])
@pytest.mark.parametrize("lens", [(-0.3, 0.0, 0.0, 0.0), (-0.3, 0.05, 0.02, 0.01), (0.0, 0.0, 0.05, 0.0)])
def test_nan_rows_pass_through(warp, lens) -> None:  # noqa: ANN001
    out = warp(np.array([[np.nan, np.nan], [1700.0, 300.0]]), _W, _H, *lens)
    assert np.isnan(out[0]).all()
    assert np.isfinite(out[1]).all()


def test_nan_radii_pass_through_every_path_of_the_inverse() -> None:
    r_u = np.array([np.nan, 0.5])
    for k1, k2 in [(-0.3, 0.0), (-0.3, 0.05)]:
        out = distort_normalised_radius(r_u, k1, k2)
        assert np.isnan(out[0]) and np.isfinite(out[1])
        out = distort_normalised_radius(r_u, np.full(2, k1), np.full(2, k2))
        assert np.isnan(out[0]) and np.isfinite(out[1])


# --- The inverse's solver ---------------------------------------------------


def test_the_inverse_broadcasts_a_grid_of_pairs() -> None:
    r_u = np.array([0.0, 0.3, 0.9, 1.1])[:, None]
    k1 = np.array([0.0, -0.2, 0.1, -0.53])[None, :]
    k2 = np.array([0.0, 0.0, 0.02, -0.07])[None, :]
    r_d = distort_normalised_radius(r_u, k1, k2)
    assert r_d.shape == (4, 4)
    np.testing.assert_allclose(r_d[:, 0], r_u[:, 0])
    s = r_d * r_d
    np.testing.assert_allclose(r_d / (1.0 + k1 * s + k2 * s * s), np.broadcast_to(r_u, (4, 4)), rtol=1e-12)
    for j in range(4):
        np.testing.assert_allclose(r_d[:, j], distort_normalised_radius(r_u[:, 0], k1[0, j], k2[0, j]), rtol=1e-12)


def test_one_pair_against_many_radii_keeps_their_shape() -> None:
    r_u = np.linspace(0.0, 2.0, 12).reshape(4, 3)
    out = distort_normalised_radius(r_u, -0.53, -0.07)
    assert out.shape == (4, 3)
    np.testing.assert_allclose(out.ravel(), distort_normalised_radius(r_u.ravel(), -0.53, -0.07), rtol=0)


@pytest.mark.parametrize(("k1", "k2"), [(np.array([-0.53, 0.2, 0.0]), -0.07), (-0.53, np.array([-0.07, 0.0, 0.05]))])
def test_one_coefficient_as_an_array_broadcasts_the_other(k1, k2) -> None:  # noqa: ANN001
    r_u = np.array([0.4, 0.9, 1.6])
    out = distort_normalised_radius(r_u, k1, k2)
    for i, (a, b) in enumerate(np.broadcast(k1, k2)):
        assert out[i] == pytest.approx(float(distort_normalised_radius(r_u[i], a, b)), rel=1e-12)


@pytest.mark.parametrize(
    "k1,k2", [(-0.097, -0.002), (-0.203, -0.009), (-0.333, -0.025), (-0.527, -0.069), (-0.74, -0.152)]
)
def test_the_table_seed_alone_settles_every_frame_radius(k1: float, k2: float, monkeypatch: pytest.MonkeyPatch) -> None:
    # Two Newton steps from the table suffice on screen; the bracketed solve is only a fallback.
    def refuse(*_args: object) -> None:
        raise AssertionError("the polish left a frame radius unsettled")

    monkeypatch.setattr(solver, "_bracketed_radius", refuse)
    r_d = np.linspace(0.0, 1.0, 501)
    s = r_d * r_d
    np.testing.assert_allclose(distort_normalised_radius(r_d / (1.0 + k1 * s + k2 * s * s), k1, k2), r_d, atol=1e-13)


@pytest.mark.parametrize("k1,k2", [(-0.53, -0.07), (-0.2, -0.5), (0.2, 0.05), (-0.74, -0.152)])
def test_the_bracketed_solve_converges_in_a_few_newton_steps(
    k1: float, k2: float, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Bisection alone would need some 45 halvings to 1e-13; Newton inside the bracket needs a handful.
    monkeypatch.setattr(solver, "_DISTORT_MAX_STEPS", 12)
    r_lim = lens_limit_radius(k1, k2)
    r_d = np.linspace(0.0, 0.95 * r_lim, 200)
    s = r_d * r_d
    r_u = r_d / (1.0 + k1 * s + k2 * s * s)
    out = solver._bracketed_radius(
        r_u, k1, k2, r_lim, np.clip(2.0 * r_u / (1.0 + np.sqrt(np.abs(1.0 - 4.0 * k1 * r_u * r_u))), 0.0, r_lim)
    )
    np.testing.assert_allclose(out, r_d, rtol=0, atol=1e-12)


@pytest.mark.parametrize("k1,k2", [(-0.53, -0.07), (-0.2, -0.5), (0.2, 0.05), (-1.98, 0.985), (12.9, -13.88)])
def test_the_table_seeded_inverse_agrees_with_the_bracketed_solve(k1: float, k2: float) -> None:
    r_lim = lens_limit_radius(k1, k2)
    r_d = np.linspace(0.0, r_lim, 2001)[:-1]
    s = r_d * r_d
    r_u = r_d / (1.0 + k1 * s + k2 * s * s)
    seeded = distort_normalised_radius(r_u, k1, k2)
    bracketed = solver._bracketed_radius(r_u, k1, k2, r_lim, np.zeros_like(r_u))
    np.testing.assert_allclose(seeded, bracketed, rtol=0, atol=1e-12)
    np.testing.assert_allclose(seeded, r_d, rtol=0, atol=1e-11)


def test_a_radius_the_polish_leaves_unsettled_is_solved_in_the_bracket() -> None:
    # Right at a fold the slope vanishes and two Newton steps from the table fall short.
    k1, k2 = 0.2, 0.05
    r_lim = lens_limit_radius(k1, k2)
    r_d = np.array([0.5, r_lim * (1.0 - 1e-7)])
    s = r_d * r_d
    r_u = r_d / (1.0 + k1 * s + k2 * s * s)
    out = distort_normalised_radius(r_u, k1, k2)
    np.testing.assert_allclose(out, r_d, rtol=0, atol=1e-7)
    assert out[1] <= r_lim


@given(
    x=st.floats(min_value=0.0, max_value=_W),
    y=st.floats(min_value=0.0, max_value=_H),
    k1=st.floats(min_value=-2.0, max_value=2.0),
    k2=st.floats(min_value=-2.0, max_value=1.0),
    cx=st.floats(min_value=-C_BOUND, max_value=C_BOUND),
    cy=st.floats(min_value=-C_BOUND, max_value=C_BOUND),
)
def test_a_valid_lens_maps_every_frame_point_there_and_back(
    x: float, y: float, k1: float, k2: float, cx: float, cy: float
) -> None:
    assume(lens_warp_is_valid(k1, k2, cx, cy))
    pt = np.array([[x, y]])
    pinhole = _invert(pt, k1, k2, cx, cy)
    assert np.isfinite(pinhole).all()
    np.testing.assert_allclose(_apply(pinhole, k1, k2, cx, cy), pt, atol=1e-6)


def test_the_vectorised_limit_radius_is_the_rules() -> None:
    k1 = np.array([0.0, -1.0, 1.0, 0.0, 0.0, -3.0, 4.0, -0.5, -0.53, 12.9])
    k2 = np.array([0.0, 0.0, 0.0, -1.0, 1.0, 2.0, -1.0, 1e-12, -0.07, -13.88])
    expected = [lens_limit_radius(a, b) for a, b in zip(k1, k2, strict=True)]
    np.testing.assert_allclose(solver._limit_radius(k1, k2), expected, rtol=1e-14)


@pytest.mark.parametrize("warp", [apply_overlay_distortion, invert_overlay_distortion])
@pytest.mark.parametrize("lens", [(-0.3, 0.0, 0.0, 0.0), (-0.3, -0.05, 0.02, 0.0), (0.0, 0.0, 0.02, 0.0)])
def test_no_points_give_no_points(warp, lens) -> None:  # noqa: ANN001
    assert warp(np.empty((0, 2)), _W, _H, *lens).shape == (0, 2)
