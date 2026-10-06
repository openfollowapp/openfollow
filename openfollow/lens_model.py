# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The radial lens model's one validity rule, shared by config, web and solver.

The overlay correction bows the HUD with ``f(r) = 1 + k1*r^2 + k2*r^4`` about
the image centre, ``r`` normalised to the image half-diagonal (``r == 1`` at a
corner). A pair of coefficients is usable only while that warp does not fold
anywhere out to the corner: ``r*f(r)`` must keep growing on ``[0, 1]``, which is
``g(s) = 1 + 3*k1*s + 5*k2*s^2 > 0`` for ``s = r^2`` in ``[0, 1]``. A fold would
map two pinhole points onto one screen point, so a click there has no single
preimage.

This module is stdlib-only because ``configuration.py`` reads it.
"""

from __future__ import annotations

import math


def lens_fold_radius(k1: float, k2: float) -> float:
    """Normalised radius where the warp first folds, ``inf`` when it never does.

    The smallest positive root of ``g(s) = 5*k2*s^2 + 3*k1*s + 1`` as a radius.
    """
    if k2 == 0.0:
        return math.sqrt(-1.0 / (3.0 * k1)) if k1 < 0.0 else math.inf
    disc = 9.0 * k1 * k1 - 20.0 * k2
    if disc < 0.0:
        return math.inf
    # The q form keeps both roots exact when k2 is tiny against k1.
    q = -0.5 * (3.0 * k1 + math.copysign(math.sqrt(disc), 3.0 * k1 if k1 != 0.0 else 1.0))
    roots = (q / (5.0 * k2), 1.0 / q if q != 0.0 else math.inf)
    positive = [s for s in roots if s > 0.0]
    return math.sqrt(min(positive)) if positive else math.inf


def lens_warp_is_valid(k1: object, k2: object) -> bool:
    """True when the pair is finite and the warp does not fold out to the corner."""
    try:
        a = float(k1)  # type: ignore[arg-type]
        b = float(k2)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return False
    if not (math.isfinite(a) and math.isfinite(b)):
        return False
    return lens_fold_radius(a, b) > 1.0
