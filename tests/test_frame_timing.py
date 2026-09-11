# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for :mod:`openfollow.runtime.frame_timing`.

The animate-clock step shared by every per-frame filter: the detection pin, the
assist glide, and the broadcast velocity estimate all re-derive their per-frame
constants through these two functions.
"""

from __future__ import annotations

import pytest

from openfollow.runtime.frame_timing import NOMINAL_FRAME_DT, dt_steps, ema_factor

pytestmark = pytest.mark.unit


def test_ema_factor_is_exact_at_nominal_and_compounds() -> None:
    # At one nominal frame the alpha is returned unchanged (no float drift).
    assert ema_factor(0.3, 1.0) == 0.3
    assert ema_factor(0.15, 1.0) == 0.15
    # Two frames compound the retention: 1 - (1-a)^2.
    assert ema_factor(0.2, 2.0) == pytest.approx(1.0 - 0.8**2)
    # A full-snap alpha stays full at any step count.
    assert ema_factor(1.0, 3.0) == 1.0


def test_dt_steps_clamps_and_normalises() -> None:
    assert dt_steps(NOMINAL_FRAME_DT) == pytest.approx(1.0)
    assert dt_steps(2 * NOMINAL_FRAME_DT) == pytest.approx(2.0)
    # A huge stall is clamped so the filter can't take an unbounded step.
    assert dt_steps(100.0) == dt_steps(10.0)
    # And two ticks in the same instant still leave a positive step.
    assert dt_steps(0.0) > 0.0
