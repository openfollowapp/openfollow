# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for :mod:`openfollow.runtime.marker_velocity`.

The estimator behind the PSN speed field: a per-frame position delta in m/s,
EMA-smoothed with a frame-rate-independent alpha, zero on the seeding frame
and after a jump.
"""

from __future__ import annotations

import pytest

from openfollow.runtime.marker_velocity import (
    _TELEPORT_SPEED_MPS,
    _VELOCITY_ALPHA,
    MarkerVelocityState,
    estimate_marker_velocity,
)
from openfollow.runtime.services_detection_pin import _NOMINAL_FRAME_DT, _dt_steps, _ema_factor

pytestmark = pytest.mark.unit

_DT_60 = 1.0 / 60.0
_DT_30 = 1.0 / 30.0


def _drive(
    state: MarkerVelocityState,
    velocity: tuple[float, float, float],
    *,
    dt: float,
    frames: int,
    start: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> tuple[float, float, float]:
    """Seed at *start*, then move at *velocity* for *frames* steps of *dt*."""
    x, y, z = start
    out = estimate_marker_velocity(state, (x, y, z), dt)
    for _ in range(frames):
        x += velocity[0] * dt
        y += velocity[1] * dt
        z += velocity[2] * dt
        out = estimate_marker_velocity(state, (x, y, z), dt)
    return out


def test_first_frame_seeds_and_reports_zero() -> None:
    state = MarkerVelocityState()
    assert estimate_marker_velocity(state, (5.0, 5.0, 1.0), _DT_60) == (0.0, 0.0, 0.0)
    assert state.prev_pos == (5.0, 5.0, 1.0)


def test_stationary_marker_stays_exactly_zero() -> None:
    """A marker that does not move reports no velocity, not its speed setting."""
    state = MarkerVelocityState()
    for _ in range(30):
        assert estimate_marker_velocity(state, (2.0, -1.0, 1.6), _DT_60) == (0.0, 0.0, 0.0)


def test_one_frame_of_motion_applies_the_per_frame_alpha() -> None:
    """At the nominal frame the rate is in m/s and the blend is exactly the alpha."""
    state = MarkerVelocityState()
    out = _drive(state, (1.0, 0.0, 0.0), dt=_NOMINAL_FRAME_DT, frames=1)
    assert out == pytest.approx((_VELOCITY_ALPHA, 0.0, 0.0))


@pytest.mark.parametrize("axis", [0, 1, 2])
@pytest.mark.parametrize("sign", [1.0, -1.0])
def test_motion_along_one_axis_reports_that_axis_with_sign(axis: int, sign: float) -> None:
    velocity = [0.0, 0.0, 0.0]
    velocity[axis] = sign * 1.5
    state = MarkerVelocityState()
    out = _drive(state, (velocity[0], velocity[1], velocity[2]), dt=_DT_60, frames=60)
    assert out[axis] == pytest.approx(sign * 1.5, abs=1e-6)
    for other in range(3):
        if other != axis:
            assert out[other] == 0.0


def test_same_physical_motion_converges_identically_at_60_and_30_fps() -> None:
    """Half a second at 1 m/s lands on the same estimate whether it was sampled
    30 or 15 times, and neither has fully converged yet: the alpha is
    rescaled per frame, not just the steady state."""
    fast = _drive(MarkerVelocityState(), (1.0, 0.0, 0.0), dt=_DT_60, frames=30)
    slow = _drive(MarkerVelocityState(), (1.0, 0.0, 0.0), dt=_DT_30, frames=15)
    assert fast == pytest.approx(slow, abs=1e-9)
    assert 0.0 < fast[0] < 1.0


def test_teleport_resets_velocity_and_reseeds() -> None:
    state = MarkerVelocityState()
    _drive(state, (1.0, 0.0, 0.0), dt=_DT_60, frames=60)
    assert state.prev_pos is not None
    x, y, z = state.prev_pos
    assert estimate_marker_velocity(state, (x + 5.0, y, z), _DT_60) == (0.0, 0.0, 0.0)
    # The next normal step restarts from the new position, not from the jump.
    out = estimate_marker_velocity(state, (x + 5.0 + 1.0 * _DT_60, y, z), _DT_60)
    assert out == pytest.approx((_VELOCITY_ALPHA, 0.0, 0.0))


def test_teleport_threshold_is_exclusive() -> None:
    at_cap = MarkerVelocityState()
    estimate_marker_velocity(at_cap, (0.0, 0.0, 0.0), _NOMINAL_FRAME_DT)
    out = estimate_marker_velocity(at_cap, (_TELEPORT_SPEED_MPS * _NOMINAL_FRAME_DT, 0.0, 0.0), _NOMINAL_FRAME_DT)
    assert out[0] == pytest.approx(_VELOCITY_ALPHA * _TELEPORT_SPEED_MPS)

    past_cap = MarkerVelocityState()
    estimate_marker_velocity(past_cap, (0.0, 0.0, 0.0), _NOMINAL_FRAME_DT)
    step = (_TELEPORT_SPEED_MPS + 1e-3) * _NOMINAL_FRAME_DT
    assert estimate_marker_velocity(past_cap, (step, 0.0, 0.0), _NOMINAL_FRAME_DT) == (0.0, 0.0, 0.0)


def test_zero_dt_uses_the_clamped_step() -> None:
    """Two ticks in the same instant must not divide by zero; the rate is
    taken over the clamp floor instead."""
    state = MarkerVelocityState()
    estimate_marker_velocity(state, (0.0, 0.0, 0.0), 0.0)
    out = estimate_marker_velocity(state, (0.001, 0.0, 0.0), 0.0)
    steps = _dt_steps(0.0)
    expected = _ema_factor(_VELOCITY_ALPHA, steps) * (0.001 / (steps * _NOMINAL_FRAME_DT))
    assert expected > 0.0
    assert out == pytest.approx((expected, 0.0, 0.0))


def test_returned_vector_matches_state() -> None:
    state = MarkerVelocityState()
    out = _drive(state, (0.0, 2.0, 0.0), dt=_DT_60, frames=5)
    assert out == state.velocity
