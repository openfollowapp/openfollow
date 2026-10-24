# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The lens's one validity rule: the map must stay defined and rising out to every frame corner."""

from __future__ import annotations

import math

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from openfollow.lens_model import (
    C_BOUND,
    PULL_BACK_MARGIN,
    lens_limit_radius,
    lens_warp_is_valid,
    pull_back_to_valid,
)

pytestmark = pytest.mark.unit

_HAIR = 1e-9


@pytest.mark.parametrize(
    "k1,k2,expected",
    [
        (0.0, 0.0, math.inf),
        (-1.0, 0.0, 1.0),  # D = 1 - s: the pole on the corner
        (-0.25, 0.0, 2.0),
        (1.0, 0.0, 1.0),  # P = 1 - s: the fold on the corner
        (0.25, 0.0, 2.0),
        (0.0, -1.0, 1.0),  # D = 1 - s^2
        (0.0, 1.0 / 3.0, 1.0),  # P = 1 - s^2
        (0.0, 1.0, 3.0**-0.25),  # P = 1 - 3 s^2, D never 0
        (-3.0, 2.0, math.sqrt(0.5)),  # D = (1 - s)(1 - 2 s): the nearer of two roots
        (4.0, -1.0, math.sqrt(1.0 / 3.0)),  # P = (1 - s)(1 - 3 s)
    ],
)
def test_limit_radius_closed_form(k1: float, k2: float, expected: float) -> None:
    assert lens_limit_radius(k1, k2) == pytest.approx(expected, rel=1e-12)


def test_limit_radius_stays_exact_when_the_corner_term_is_tiny() -> None:
    # The pole of 1 - 0.5 s + 1e-12 s^2 is 2 + 8e-12 + ...; the textbook root formula loses it to cancellation.
    s = 2.0 + 8e-12
    assert lens_limit_radius(-0.5, 1e-12) == pytest.approx(math.sqrt(s), rel=1e-14)


@pytest.mark.parametrize("k1", [-1.0 + _HAIR, -0.5, 0.0, 0.5, 1.0 - _HAIR])
def test_without_a_corner_term_the_pair_is_valid_inside_minus_one_to_one(k1: float) -> None:
    assert lens_warp_is_valid(k1, 0.0, 0.0, 0.0)


@pytest.mark.parametrize("k1", [-1.0, 1.0, -1.5, 2.0])
def test_without_a_corner_term_the_pair_is_invalid_from_minus_one_and_one(k1: float) -> None:
    assert not lens_warp_is_valid(k1, 0.0, 0.0, 0.0)


@pytest.mark.parametrize(
    "k1,k2",
    [
        (-0.6, -0.4),  # k1 + k2 = -1: the pole on the corner
        (0.4, 0.2),  # k1 + 3 k2 = 1: the fold on the corner
        (0.0, -1.0),
        (0.0, 1.0 / 3.0),
    ],
)
def test_a_limit_on_the_corner_is_invalid_and_a_hair_short_of_it_valid(k1: float, k2: float) -> None:
    assert not lens_warp_is_valid(k1, k2, 0.0, 0.0)
    assert lens_warp_is_valid(k1 * (1.0 - _HAIR), k2 * (1.0 - _HAIR), 0.0, 0.0)


@pytest.mark.parametrize(
    "k1,k2",
    [
        (-3.0, 2.0),  # D dips below 0 at its vertex inside the frame and is back above it at the corner
        (4.0, -1.0),  # P likewise
        (-0.951, -0.251),  # an equidistant 170 degree fisheye: its corners look past 90 degrees
    ],
)
def test_a_limit_inside_the_frame_is_invalid_whatever_the_corner_says(k1: float, k2: float) -> None:
    assert not lens_warp_is_valid(k1, k2, 0.0, 0.0)


@pytest.mark.parametrize(
    "k1,k2",
    [
        (-0.527, -0.069),  # an equidistant 2.3 mm lens
        (-0.74, -0.152),  # an equidistant 150 degree fisheye
        (0.0, -0.8),  # the Corner correction slider's end
        (12.9, -13.88),  # the far tip of the valid region
        (-1.98, 0.985),
    ],
)
def test_strong_lenses_are_valid(k1: float, k2: float) -> None:
    assert lens_warp_is_valid(k1, k2, 0.0, 0.0)


@pytest.mark.parametrize("k1,k2", [(12.95, -13.95), (-2.0, 0.99), (0.0, 1.0), (-1.0, -0.01), (13.0, -14.0)])
def test_the_valid_region_is_bounded(k1: float, k2: float) -> None:
    assert not lens_warp_is_valid(k1, k2, 0.0, 0.0)


@pytest.mark.parametrize("cx,cy", [(0.1, 0.0), (0.0, -0.1), (0.06, 0.08), (-0.08, -0.06)])
def test_the_lens_centre_moves_the_far_corner_out_to_one_plus_its_offset(cx: float, cy: float) -> None:
    # A pole at radius 1.1 is valid for a centre just under 0.1 off and invalid just over.
    k1 = -1.0 / 1.1**2
    assert lens_warp_is_valid(k1, 0.0, cx * (1.0 - _HAIR), cy * (1.0 - _HAIR))
    assert not lens_warp_is_valid(k1, 0.0, cx * (1.0 + _HAIR), cy * (1.0 + _HAIR))
    assert not lens_warp_is_valid(k1, 0.0, cx, cy)


@pytest.mark.parametrize("axis", [0, 1])
@pytest.mark.parametrize("sign", [-1.0, 1.0])
def test_each_centre_axis_is_valid_to_its_bound_and_not_a_hair_past(axis: int, sign: float) -> None:
    at = [0.0, 0.0]
    at[axis] = sign * C_BOUND
    assert lens_warp_is_valid(0.0, 0.0, *at)
    at[axis] = sign * (C_BOUND + 1e-12)
    assert not lens_warp_is_valid(0.0, 0.0, *at)


@pytest.mark.parametrize("position", range(4))
@pytest.mark.parametrize("bad", ["abc", None, [0.1], float("nan"), float("inf"), float("-inf"), 10**400])
def test_a_value_that_is_no_finite_number_is_invalid(position: int, bad: object) -> None:
    lens: list[object] = [0.0, 0.0, 0.0, 0.0]
    lens[position] = bad
    assert not lens_warp_is_valid(*lens)


def test_numeric_strings_are_accepted() -> None:
    assert lens_warp_is_valid("-0.2", "0.05", "0.01", "-0.02")


@pytest.mark.parametrize("k1,k2", [(-1e200, 1e200), (2e3, 0.0), (0.0, -2e3)])
def test_a_coefficient_far_past_any_lens_is_invalid(k1: float, k2: float) -> None:
    assert not lens_warp_is_valid(k1, k2, 0.0, 0.0)


def _least_on(c0: float, c1: float, s_max: float) -> float:
    """Least of ``1 + c0 s + c1 s^2`` on ``[0, s_max]``: at an end, or at the vertex of an upward parabola."""
    candidates = [0.0, s_max]
    if c1 > 0.0 and 0.0 < -c0 / (2.0 * c1) < s_max:
        candidates.append(-c0 / (2.0 * c1))
    return min(1.0 + c0 * s + c1 * s * s for s in candidates)


_K = st.floats(min_value=-3.0, max_value=3.0)
_C = st.floats(min_value=-C_BOUND, max_value=C_BOUND)


@given(k1=_K, k2=_K, cx=_C, cy=_C)
def test_valid_means_neither_limit_falls_inside_the_far_corner(k1: float, k2: float, cx: float, cy: float) -> None:
    s_max = (1.0 + math.hypot(cx, cy)) ** 2
    least = min(_least_on(k1, k2, s_max), _least_on(-k1, -3.0 * k2, s_max))
    # On the boundary a parabola only touches zero, and rounding decides either way.
    assume(abs(least) > 1e-9)
    assert lens_warp_is_valid(k1, k2, cx, cy) == (least > 0.0)


@pytest.mark.parametrize(
    "lens,expected",
    [
        # 1 - 1.2 t s reaches 0 at s = 1: t* = 1 / 1.2.
        ((-1.2, 0.0, 0.0, 0.0), (-0.99, 0.0, 0.0, 0.0)),
        # With a centre 0.1 off, out to s = 1.21: t* = 1 / (1.2 * 1.21).
        ((-1.2, 0.0, 0.1, 0.0), (-0.99 / 1.21, 0.0, 0.1, 0.0)),
        # P = 1 - 1.5 t s^2: t* = 2 / 3.
        ((0.0, 0.5, 0.0, 0.0), (0.0, 0.33, 0.0, 0.0)),
        # P = 1 - t (4 s - 3 s^2) peaks at its vertex s = 2/3: t* = 3 / 4.
        ((4.0, -1.0, 0.0, 0.0), (2.97, -0.7425, 0.0, 0.0)),
        # P's vertex lies past the far corner (s = 1.25), so the corner decides: t* = 1 / 1.44.
        ((2.4, -0.32, 0.0, 0.0), (1.65, -0.22, 0.0, 0.0)),
        # The corner term the larger: P = 1 - t (13 s - 40.5 s^2) peaks at s = 13/81, t* = 162 / 169.
        ((13.0, -13.5, 0.0, 0.0), (13.0 * 0.99 * 162 / 169, -13.5 * 0.99 * 162 / 169, 0.0, 0.0)),
    ],
)
def test_an_invalid_pair_is_pulled_back_to_the_closed_form_point(
    lens: tuple[float, float, float, float], expected: tuple[float, float, float, float]
) -> None:
    assert pull_back_to_valid(*lens) == pytest.approx(expected, rel=1e-12, abs=1e-15)


def test_a_valid_lens_is_kept() -> None:
    assert pull_back_to_valid(-0.527, -0.069, 0.04, -0.03) == (-0.527, -0.069, 0.04, -0.03)


@pytest.mark.parametrize(
    "centre,clamped",
    [
        ((0.3, -0.4), (C_BOUND, -C_BOUND)),
        ((float("nan"), float("inf")), (0.0, 0.0)),
        ((float("-inf"), 0.1), (0.0, 0.1)),
    ],
)
def test_the_centre_is_clamped_and_a_centre_that_is_no_number_reads_as_0(
    centre: tuple[float, float], clamped: tuple[float, float]
) -> None:
    assert pull_back_to_valid(0.0, 0.0, *centre) == (0.0, 0.0, *clamped)


def test_the_centre_is_never_moved_to_make_a_pair_valid() -> None:
    # Valid about the frame centre, not with the centre off: the pair shrinks, the centre stays.
    k1, k2, cx, cy = pull_back_to_valid(-0.95, 0.0, C_BOUND, C_BOUND)
    assert (cx, cy) == (C_BOUND, C_BOUND)
    assert -0.95 < k1 < 0.0
    assert k2 == 0.0


@pytest.mark.parametrize("k1,k2", [(math.nan, -0.4), (-0.4, math.inf), (-math.inf, 0.0)])
def test_a_pair_that_is_no_number_pulls_back_to_pinhole_keeping_the_centre(k1: float, k2: float) -> None:
    assert pull_back_to_valid(k1, k2, 0.1, -0.2) == (0.0, 0.0, 0.1, -0.2)


def test_a_pair_far_past_any_lens_pulls_back_without_overflowing() -> None:
    k1, k2, _, _ = pull_back_to_valid(1e308, -1e308, 0.0, 0.0)
    assert lens_warp_is_valid(k1, k2, 0.0, 0.0)
    assert k1 == pytest.approx(-k2) and k1 > 0.0


def _bisected(k1: float, k2: float, cx: float, cy: float) -> float:
    lo, hi = 0.0, 1.0
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if lens_warp_is_valid(mid * k1, mid * k2, cx, cy):
            lo = mid
        else:
            hi = mid
    return lo


_GRID_LENSES = [
    (k1, k2, centre)
    for k1 in (-2.5, -1.2, -0.3, 0.0, 0.6, 2.0, 9.0)
    for k2 in (-3.0, -0.5, 0.0, 0.4, 1.5)
    for centre in ((0.0, 0.0), (0.2, -0.1), (-C_BOUND, C_BOUND))
]


@pytest.mark.parametrize(("k1", "k2", "centre"), [g for g in _GRID_LENSES if lens_warp_is_valid(g[0], g[1], *g[2])])
def test_a_valid_lens_on_the_grid_is_kept(k1: float, k2: float, centre: tuple[float, float]) -> None:
    assert pull_back_to_valid(k1, k2, *centre) == (k1, k2, *centre)


@pytest.mark.parametrize(("k1", "k2", "centre"), [g for g in _GRID_LENSES if not lens_warp_is_valid(g[0], g[1], *g[2])])
def test_the_closed_form_pull_back_ends_where_bisection_does(k1: float, k2: float, centre: tuple[float, float]) -> None:
    pulled = pull_back_to_valid(k1, k2, *centre)
    t = (pulled[0] / k1 if abs(k1) >= abs(k2) else pulled[1] / k2) / (1.0 - PULL_BACK_MARGIN)
    assert t == pytest.approx(_bisected(k1, k2, *centre), abs=1e-9)


@given(
    k1=st.floats(min_value=-1e4, max_value=1e4),
    k2=st.floats(min_value=-1e4, max_value=1e4),
    cx=_C,
    cy=_C,
)
def test_a_lens_is_pulled_back_along_its_ray_to_just_short_of_the_limit(
    k1: float, k2: float, cx: float, cy: float
) -> None:
    pulled = pull_back_to_valid(k1, k2, cx, cy)
    if lens_warp_is_valid(k1, k2, cx, cy):
        assert pulled == (k1, k2, cx, cy)
        return
    assert lens_warp_is_valid(*pulled)
    assert pulled[2:] == (cx, cy)
    t = pulled[0] / k1 if abs(k1) >= abs(k2) else pulled[1] / k2
    assert 0.0 < t < 1.0
    assert pulled[:2] == pytest.approx((t * k1, t * k2))
    # The margin is all that stands between it and the limit.
    grown = t / (1.0 - 2.0 * PULL_BACK_MARGIN)
    assert not lens_warp_is_valid(grown * k1, grown * k2, cx, cy)
