# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The lens model's one validity rule, shared by config, web and solver.

The overlay follows the lens with the two-term division model about a lens
centre. For a screen point ``p``, frame centre ``F`` and half-diagonal ``D``::

    v   = (p - F) / D - c          c = (cx, cy): the lens centre's offset, image axes (x right, y down)
    r_d = |v|,  s = r_d^2
    r_u = r_d / (1 + k1 s + k2 s^2)
    q   = F + D * v * r_u / r_d    the pinhole point

``r_d`` is the screen radius and ``r_u`` the pinhole radius, both about the lens
centre and in half-diagonals, so ``r_d == 1`` at a corner when ``c == 0`` and a
lens fits any resolution of the same picture shape. The lens centre is the
pinhole principal point: with ``k1 == k2 == 0`` the warp only shifts the
picture by ``c``. Barrel is negative: to first order
``r_d = r_u (1 + k1 r_u^2 + k2 r_u^4)``, and the slope at the centre is 1.

The map is defined and rising out to the screen radius where either
``D(s) = 1 + k1 s + k2 s^2`` (the pole, a ray 90 degrees off the axis) or
``P(s) = 1 - k1 s - 3 k2 s^2`` (the fold, ``dr_u/dr_d = P / D^2``) first reaches
0. A lens is valid when that limit lies beyond every frame corner; the corner
farthest from the lens centre is at most ``1 + |c|`` half-diagonals from it, for
any picture shape.

This module is stdlib-only because ``configuration.py`` reads it.
"""

from __future__ import annotations

import math

# No lens comes near this; it guards ``k1 * k1`` against overflow.
_MAX_COEFFICIENT = 1e3
# A pulled-back pair stops this fraction short of the limit, so the map's slope at the far corner stays clear of 0.
PULL_BACK_MARGIN = 0.01
# Farthest the lens centre may sit from the frame centre, per axis, in half-diagonals.
C_BOUND = 0.25


def _first_positive_root(a: float, b: float) -> float:
    """Smallest positive root of ``1 + b s + a s^2``, ``inf`` when there is none."""
    if a == 0.0:
        return -1.0 / b if b < 0.0 else math.inf
    disc = b * b - 4.0 * a
    if disc < 0.0:
        return math.inf
    # The q form keeps both roots exact when ``a`` is tiny against ``b``.
    q = -0.5 * (b + math.copysign(math.sqrt(disc), b))
    positive = [s for s in (q / a, 1.0 / q) if s > 0.0]
    return min(positive) if positive else math.inf


def lens_limit_radius(k1: float, k2: float) -> float:
    """Screen radius (half-diagonals, about the lens centre) where the map first stops rising, ``inf`` when never."""
    return math.sqrt(min(_first_positive_root(k2, k1), _first_positive_root(-3.0 * k2, -k1)))


def _finite(*values: object) -> tuple[float, ...] | None:
    try:
        out = tuple(float(v) for v in values)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return out if all(math.isfinite(v) for v in out) else None


def lens_warp_is_valid(k1: object, k2: object, cx: object, cy: object) -> bool:
    """True when the four are finite numbers, the centre in bounds, and the map rises out to every frame corner."""
    lens = _finite(k1, k2, cx, cy)
    if lens is None:
        return False
    a, b, x, y = lens
    if max(abs(a), abs(b)) > _MAX_COEFFICIENT or abs(x) > C_BOUND or abs(y) > C_BOUND:
        return False
    return lens_limit_radius(a, b) > 1.0 + math.hypot(x, y)


def _clamped_centre(v: float) -> float:
    return min(max(v, -C_BOUND), C_BOUND) if math.isfinite(v) else 0.0


def _peak(alpha: float, beta: float, s_max: float) -> float:
    """Largest value of ``alpha s + beta s^2`` on ``[0, s_max]``."""
    best = max(0.0, alpha * s_max + beta * s_max * s_max)
    if beta < 0.0 and 0.0 < -alpha / (2.0 * beta) < s_max:
        best = max(best, -alpha * alpha / (4.0 * beta))
    return best


def pull_back_to_valid(k1: float, k2: float, cx: float, cy: float) -> tuple[float, float, float, float]:
    """The lens with its centre clamped into bounds and, when it is not valid, its pair scaled toward 0 until it is.

    A non-finite centre axis reads as 0, a non-finite pair as ``(0, 0)``. The
    centre is never moved to make a pair valid. The valid pairs along a ray from
    ``(0, 0)`` form one segment for a fixed centre, ending at ``t* = 1 / m``
    with ``m`` the largest of ``-(k1 s + k2 s^2)`` and ``k1 s + 3 k2 s^2`` over
    ``s`` out to the far corner; the pair returned stops ``PULL_BACK_MARGIN``
    short of it.
    """
    cx, cy = _clamped_centre(cx), _clamped_centre(cy)
    if not (math.isfinite(k1) and math.isfinite(k2)):
        return 0.0, 0.0, cx, cy
    if lens_warp_is_valid(k1, k2, cx, cy):
        return k1, k2, cx, cy
    # Scaled to a unit pair first, so no product overflows however large the pair.
    scale = max(abs(k1), abs(k2))
    a, b = k1 / scale, k2 / scale
    s_max = (1.0 + math.hypot(cx, cy)) ** 2
    m = max(_peak(-a, -b, s_max), _peak(a, 3.0 * b, s_max))
    # An invalid lens has m * scale >= 1; the max only absorbs rounding at the edge.
    t = (1.0 - PULL_BACK_MARGIN) / max(m, 1.0 / scale)
    return t * a, t * b, cx, cy
