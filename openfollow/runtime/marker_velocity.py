# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Paul Hermann and Michel Honold
"""Velocity estimate behind the speed each controlled marker broadcasts.

The PSN speed field is a velocity vector in the same frame as the position,
so a controlled marker publishes how it actually moves. The rate is taken per
new position sample, not per frame: a writer slower than the frame clock (an
OSC sender at 10-30 Hz, a stepped glide) leaves the position unchanged between
its samples, and a per-frame rate would alternate between zero and a multiple of
its real speed. The rate is then lightly smoothed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from openfollow.psn.marker import Vec3
from openfollow.runtime.frame_timing import dt_steps, ema_factor

# Per nominal animate frame; ``ema_factor`` rescales it for the real ``dt``.
_VELOCITY_ALPHA = 0.3
# Divide by no less than this so two samples in the same instant can't divide by
# zero. Well under one frame, so it never shortens a real interval.
_MIN_SAMPLE_DT = 1.0 / 1000.0
# Ceiling on the rate that reaches the wire. A consumer dead-reckons a marker
# forward by at most one packet interval, so beyond this the extrapolation is
# further off than simply holding the last position - and a repositioning (reset,
# OSC snap, default-position change) covers metres in one frame, which as a rate
# is unbounded. Clamping keeps the direction and a usable magnitude for a fast
# drag, where zeroing would report a visibly moving marker as standing still.
_MAX_REPORTED_SPEED_MPS = 20.0
# Below this the marker is at rest: snap the decaying tail of the EMA to exactly
# zero so a marker that stops reads as stopped rather than asymptotically slow.
_STILL_SPEED_MPS = 0.005
# An unchanged position is a gap between samples while it lasts no longer than
# this multiple of the last sample interval, and never longer than the ceiling;
# past that the marker has stopped and the velocity decays.
_SAMPLE_GAP_FACTOR = 1.5
_MAX_SAMPLE_GAP_S = 0.25
# A sample whose X/Y rate passes this is a repositioning (reset, OSC snap), not
# motion: twice the fastest speed a prediction is built from.
REPOSITION_SPEED_MPS = 10.0
_ZERO: Vec3 = (0.0, 0.0, 0.0)


@dataclass
class MarkerVelocityState:
    """Per-marker estimator state: the last position sample and the smoothed velocity."""

    prev_pos: Vec3 | None = None
    velocity: Vec3 = _ZERO
    # Seconds since ``prev_pos`` was sampled, and the interval that sample closed.
    since_sample_s: float = 0.0
    sample_interval_s: float = 0.0
    # Seconds since a sample's X/Y rate passed ``REPOSITION_SPEED_MPS``.
    since_jump_s: float = math.inf
    # The X/Y prediction last sent (``marker_prediction``), which grows from frame to frame.
    prediction: tuple[float, float] = (0.0, 0.0)


def estimate_marker_velocity(state: MarkerVelocityState, pos: Vec3, dt: float) -> Vec3:
    """Advance *state* with this frame's position and return the velocity in m/s.

    Call once per frame per marker with the marker's current PSN-absolute
    position and the real seconds since the previous frame - a clamped step
    would divide a real displacement by a shorter interval and inflate the rate.
    The first call only seeds the reference and reports zero. Between a slow
    writer's samples the last velocity holds.
    """
    prev = state.prev_pos
    if prev is None:
        state.prev_pos = pos
        state.velocity = _ZERO
        return _ZERO
    seconds = max(dt, _MIN_SAMPLE_DT)
    elapsed = state.since_sample_s + seconds
    state.since_jump_s += seconds
    gap = min(_SAMPLE_GAP_FACTOR * state.sample_interval_s, _MAX_SAMPLE_GAP_S)
    if pos == prev:
        state.since_sample_s = elapsed
        if elapsed <= gap:
            return state.velocity
        rate_x = rate_y = rate_z = 0.0
        alpha = ema_factor(_VELOCITY_ALPHA, dt_steps(dt))
    else:
        # After a pause the interval is the expected gap, not the whole rest.
        interval = min(elapsed, max(seconds, gap))
        state.prev_pos = pos
        state.since_sample_s = 0.0
        state.sample_interval_s = interval
        rate_x = (pos[0] - prev[0]) / interval
        rate_y = (pos[1] - prev[1]) / interval
        rate_z = (pos[2] - prev[2]) / interval
        if math.hypot(rate_x, rate_y) > REPOSITION_SPEED_MPS:
            state.since_jump_s = 0.0
        magnitude = math.hypot(rate_x, rate_y, rate_z)
        if magnitude > _MAX_REPORTED_SPEED_MPS:
            scale = _MAX_REPORTED_SPEED_MPS / magnitude
            rate_x *= scale
            rate_y *= scale
            rate_z *= scale
        # The EMA step is the clamped one: it is a smoothing time constant, not a rate.
        alpha = ema_factor(_VELOCITY_ALPHA, dt_steps(interval))
    vx, vy, vz = state.velocity
    vx += alpha * (rate_x - vx)
    vy += alpha * (rate_y - vy)
    vz += alpha * (rate_z - vz)
    state.velocity = _ZERO if math.hypot(vx, vy, vz) < _STILL_SPEED_MPS else (vx, vy, vz)
    return state.velocity
