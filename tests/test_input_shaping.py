# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Shared analog-axis shaping used by the gamepad sticks and the 3D mouse."""

from __future__ import annotations

import math

import pytest

from openfollow.input.shaping import apply_curve, shape_axis

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "curve,value,expected",
    [
        ("linear", 0.5, 0.5),
        ("logarithmic", 1.0, 1.0),
        ("logarithmic", 0.0, 0.0),
        ("quadratic", 0.5, 0.25),
        ("s-law", 0.5, 0.5),  # 3*.25 - 2*.125 = .5
        ("s-law", 1.0, 1.0),
        ("unknown-name", 0.42, 0.42),  # falls through to linear
    ],
)
def test_apply_curve(curve: str, value: float, expected: float) -> None:
    assert apply_curve(value, curve) == pytest.approx(expected, abs=1e-9)


def test_apply_curve_logarithmic_is_finer_near_centre() -> None:
    # log curve lifts small inputs above linear (finer control near centre).
    assert apply_curve(0.2, "logarithmic") > 0.2


def test_shape_axis_inside_deadzone_is_zero() -> None:
    assert shape_axis(0.1, 0.2, "linear") == 0.0
    assert shape_axis(-0.19, 0.2, "linear") == 0.0


def test_shape_axis_rescales_above_deadzone() -> None:
    # 0.6 with a 0.2 band -> (0.6-0.2)/(1-0.2) = 0.5, linear -> 0.5.
    assert shape_axis(0.6, 0.2, "linear") == pytest.approx(0.5)


def test_shape_axis_preserves_sign_and_full_deflection() -> None:
    assert shape_axis(-1.0, 0.15, "linear") == pytest.approx(-1.0)
    assert shape_axis(1.0, 0.15, "linear") == pytest.approx(1.0)


@pytest.mark.parametrize("value", [0.0, 0.5, 1.0, -1.0])
def test_shape_axis_full_deadzone_is_inert_no_divide_by_zero(value: float) -> None:
    # deadzone >= 1 makes the axis inert and guards the (1 - deadzone) divisor;
    # value == 1.0 would otherwise hit 0/0.
    out = shape_axis(value, 1.0, "linear")
    assert out == 0.0
    assert math.isfinite(out)
