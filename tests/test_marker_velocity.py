# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for :mod:`openfollow.runtime.marker_velocity`.

The estimator behind the PSN speed field: a per-frame position delta in m/s,
EMA-smoothed with a frame-rate-independent alpha, zero on the seeding frame,
clamped to the wire ceiling, and settling to exactly zero at rest.
"""

from __future__ import annotations

import math

import pytest

from openfollow.runtime.frame_timing import NOMINAL_FRAME_DT, dt_steps, ema_factor
from openfollow.runtime.marker_velocity import (
    _MAX_REPORTED_SPEED_MPS,
    _MIN_SAMPLE_DT,
    _STILL_SPEED_MPS,
    _VELOCITY_ALPHA,
    MarkerVelocityState,
    estimate_marker_velocity,
)

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
    """The seed frame has no reference position, so it reports zero rather than
    reading the marker's own coordinates as a displacement from the origin. The
    start is one slow frame's travel from the origin, so a reference that
    silently defaulted there would publish an ordinary 0.6 m/s - not a spike the
    wire ceiling would have flattened anyway."""
    state = MarkerVelocityState()
    start = (0.6 * _DT_60, 0.0, 0.0)
    assert estimate_marker_velocity(state, start, _DT_60) == (0.0, 0.0, 0.0)
    assert state.prev_pos == start


def test_stationary_marker_stays_exactly_zero() -> None:
    """A marker that does not move reports no velocity, not its speed setting."""
    state = MarkerVelocityState()
    for _ in range(30):
        assert estimate_marker_velocity(state, (2.0, -1.0, 1.6), _DT_60) == (0.0, 0.0, 0.0)


def test_one_frame_of_motion_applies_the_per_frame_alpha() -> None:
    """At the nominal frame the rate is in m/s and the blend is exactly the alpha."""
    state = MarkerVelocityState()
    out = _drive(state, (1.0, 0.0, 0.0), dt=NOMINAL_FRAME_DT, frames=1)
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


def test_a_drag_faster_than_the_ceiling_still_reports_motion() -> None:
    """A grabbed marker follows the cursor with no glide by default, so an
    ordinary flick crosses the stage far faster than the wire ceiling. It must
    read as fast motion in the right direction - reporting zero would say the
    marker is standing still while it visibly moves."""
    state = MarkerVelocityState()
    out = _drive(state, (-40.0, 0.0, 0.0), dt=_DT_60, frames=60)
    assert out[0] == pytest.approx(-_MAX_REPORTED_SPEED_MPS, abs=1e-6)
    assert out[1] == 0.0
    assert out[2] == 0.0


def test_the_ceiling_keeps_direction_across_axes() -> None:
    """Clamping scales the whole vector, so a diagonal move stays diagonal."""
    state = MarkerVelocityState()
    out = _drive(state, (30.0, -40.0, 0.0), dt=_DT_60, frames=60)
    assert math.hypot(*out) == pytest.approx(_MAX_REPORTED_SPEED_MPS, abs=1e-6)
    assert out[0] / out[1] == pytest.approx(30.0 / -40.0)


def test_a_reposition_is_bounded_not_extrapolated() -> None:
    """A reset / OSC snap moves the marker metres in one frame. As a rate that
    is hundreds of m/s, which a dead-reckoning console would extrapolate; the
    wire never sees more than the ceiling."""
    state = MarkerVelocityState()
    estimate_marker_velocity(state, (0.0, 0.0, 0.0), _DT_60)
    out = estimate_marker_velocity(state, (12.0, 0.0, 0.0), _DT_60)
    assert out[0] == pytest.approx(_VELOCITY_ALPHA * _MAX_REPORTED_SPEED_MPS)


def test_a_marker_that_stops_settles_to_exactly_zero() -> None:
    """The EMA only decays toward zero, so without the rest deadband a marker
    that stopped would keep a shrinking speed on the wire indefinitely."""
    state = MarkerVelocityState()
    moving = _drive(state, (1.5, 0.0, 0.0), dt=_DT_60, frames=60)
    assert moving[0] == pytest.approx(1.5, abs=1e-6)

    resting = state.prev_pos
    assert resting is not None
    settled = None
    for frame in range(120):
        out = estimate_marker_velocity(state, resting, _DT_60)
        if out == (0.0, 0.0, 0.0):
            settled = frame
            break
    assert settled is not None, "a stopped marker never reached exactly zero"
    # Within a second of standing still, not a slow crawl toward it.
    assert settled < 60
    assert estimate_marker_velocity(state, resting, _DT_60) == (0.0, 0.0, 0.0)


def test_motion_below_the_rest_deadband_reads_as_still() -> None:
    """The deadband is well under any stage motion, so the only thing it can
    swallow is drift."""
    state = MarkerVelocityState()
    creep = _STILL_SPEED_MPS / 2.0
    assert _drive(state, (creep, 0.0, 0.0), dt=_DT_60, frames=200) == (0.0, 0.0, 0.0)


def test_a_stalled_frame_reports_the_rate_it_actually_travelled() -> None:
    """The velocity divides by real elapsed time. A frame that took 0.4 s moving
    1 m is 2.5 m/s - clamping the divisor to the motion step would call it 10."""
    state = MarkerVelocityState()
    estimate_marker_velocity(state, (0.0, 0.0, 0.0), 0.4)
    out = estimate_marker_velocity(state, (1.0, 0.0, 0.0), 0.4)
    alpha = ema_factor(_VELOCITY_ALPHA, dt_steps(0.4))
    assert out[0] == pytest.approx(alpha * 2.5)


def test_zero_dt_divides_by_the_floor_instead() -> None:
    """Two ticks in the same instant must not divide by zero."""
    state = MarkerVelocityState()
    estimate_marker_velocity(state, (0.0, 0.0, 0.0), 0.0)
    out = estimate_marker_velocity(state, (0.001, 0.0, 0.0), 0.0)
    alpha = ema_factor(_VELOCITY_ALPHA, dt_steps(0.0))
    assert out == pytest.approx((alpha * (0.001 / _MIN_SAMPLE_DT), 0.0, 0.0))


def test_returned_vector_matches_state() -> None:
    state = MarkerVelocityState()
    out = _drive(state, (0.0, 2.0, 0.0), dt=_DT_60, frames=5)
    assert out == state.velocity
