# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Shared analog-axis shaping: per-axis deadzone + response curve.

The gamepad sticks and the 3D mouse axes shape raw deflection the same way, so
the math lives here once. Both feed a raw value in ``[-1, 1]`` through a centre
deadzone (rescaled to the full range above the band) and a response curve.
"""

from __future__ import annotations

import math


def apply_curve(value: float, curve: str) -> float:
    """Response curve for a normalised magnitude in ``[0, 1]``.

    - ``linear``:      ``y = x``
    - ``logarithmic``: ``y = log(1 + 9x) / log(10)`` (fine control near centre)
    - ``quadratic``:   ``y = x^2``
    - ``s-law``:       ``y = 3x^2 - 2x^3`` (smooth ease-in / ease-out)

    An unknown curve name falls through to linear.
    """
    if curve == "logarithmic":
        return math.log1p(9.0 * value) / math.log(10.0)
    if curve == "quadratic":
        return value * value
    if curve == "s-law":
        return 3.0 * value * value - 2.0 * value * value * value
    return value


def shape_axis(value: float, deadzone: float, curve: str) -> float:
    """Deadzone + response curve for a raw axis value in ``[-1, 1]``.

    Deflection inside the deadzone reads as zero; above it the magnitude is
    rescaled so the band's edge maps to 0 and full deflection to 1, then shaped
    by ``curve``. ``deadzone >= 1`` makes the whole axis inert (and guards the
    ``1 - deadzone`` divisor against a divide-by-zero).
    """
    if deadzone >= 1.0 or abs(value) < deadzone:
        return 0.0
    sign = 1.0 if value > 0 else -1.0
    scaled = (abs(value) - deadzone) / (1.0 - deadzone)
    return sign * apply_curve(scaled, curve)
