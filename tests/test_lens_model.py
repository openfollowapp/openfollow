# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The lens pair's one validity rule: the warp must not fold inside the frame."""

from __future__ import annotations

import math

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from openfollow.lens_model import lens_fold_radius, lens_warp_is_valid
from openfollow.scene.solver import invert_normalised_radius

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "k1,k2",
    [
        (0.0, 0.0),
        (-0.15, 0.0),
        (-0.3, 0.05),
        (-0.45, 0.20),  # strong barrel, well past the old +-0.4 box
        (-0.47, 0.25),  # about 100 degrees horizontal on 16:9
        (0.2, 0.0),
        (0.6, 0.4),
        (5.0, 0.0),  # pincushion never folds
        (-0.3333, 0.0),  # g(1) is just above zero
    ],
)
def test_pairs_that_keep_growing_to_the_corner_are_valid(k1: float, k2: float) -> None:
    assert lens_warp_is_valid(k1, k2)


@pytest.mark.parametrize(
    "k1,k2",
    [
        (-0.4, -0.2),  # the old box's far corner folds at r ~ 0.9
        (-1 / 3, 0.0),  # g(1) == 0 exactly: the fold sits on the corner
        (-0.5, 0.0),
        (-0.25, -0.1),
        (-0.6, 0.1),  # k2 > 0 but the parabola dips below zero inside [0, 1]
        (-1.0, 0.3),  # two real roots, the first at s ~ 0.21
        (-1e200, 1e200),  # 9 * k1 * k1 overflows: no lens is that strong
        (2e3, 0.0),
    ],
)
def test_pairs_that_fold_inside_the_frame_are_invalid(k1: float, k2: float) -> None:
    assert not lens_warp_is_valid(k1, k2)


@pytest.mark.parametrize(
    "k1,k2",
    [
        ("abc", 0.0),
        (None, 0.0),
        (0.0, "0.1x"),
        (float("nan"), 0.0),
        (0.0, float("inf")),
        (float("-inf"), 0.1),
        ([0.1], 0.0),
    ],
)
def test_wrong_type_or_non_finite_is_invalid(k1: object, k2: object) -> None:
    assert not lens_warp_is_valid(k1, k2)


def test_numeric_strings_are_accepted() -> None:
    assert lens_warp_is_valid("-0.2", "0.05")


@pytest.mark.parametrize(
    "k1,k2,expected",
    [
        (0.0, 0.0, math.inf),
        (0.3, 0.0, math.inf),
        (-0.3, 0.0, math.sqrt(1 / 0.9)),
        (-0.25, -0.05, 1.0),  # g(1) = 1 - 0.75 - 0.25 = 0
        (0.1, 0.4, math.inf),  # no real root
    ],
)
def test_fold_radius_closed_form(k1: float, k2: float, expected: float) -> None:
    assert lens_fold_radius(k1, k2) == pytest.approx(expected)


_K = st.floats(min_value=-1.0, max_value=1.0)


@given(k1=_K, k2=_K)
def test_valid_means_the_warp_keeps_growing_on_the_frame(k1: float, k2: float) -> None:
    # The closed form must agree with the least of g(s) = 1 + 3 k1 s + 5 k2 s^2 on [0, 1]:
    # at an end, or at the vertex of an upward parabola.
    candidates = [0.0, 1.0]
    if k2 > 0.0 and 0.0 < -3.0 * k1 / (10.0 * k2) < 1.0:
        candidates.append(-3.0 * k1 / (10.0 * k2))
    least = min(1.0 + 3.0 * k1 * s + 5.0 * k2 * s * s for s in candidates)
    # On the boundary the parabola only touches zero, and rounding decides either way.
    assume(abs(least) > 1e-9)
    assert lens_warp_is_valid(k1, k2) == (least > 0.0)


@given(k1=_K, k2=_K)
def test_a_radius_past_the_warp_inverts_to_the_fold_radius(k1: float, k2: float) -> None:
    # Further than any warp of a lens this size reaches, so the inverse stops at the
    # fold radius, or at its cap of four half-diagonals for a warp that never folds.
    stopped = float(invert_normalised_radius(1e6, k1, k2))
    assert stopped == pytest.approx(min(lens_fold_radius(k1, k2), 4.0), rel=1e-9)
