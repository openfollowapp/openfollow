# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Velocity estimate behind the speed each controlled marker broadcasts.

The PSN speed field is a velocity vector in the same frame as the position,
so a controlled marker publishes how it actually moves: the per-frame position
delta, lightly smoothed so writers that update slower than the frame clock (an
OSC sender at 30 Hz, a stepped glide) don't alternate between zero and double
speed on the wire.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from openfollow.psn.marker import Vec3
from openfollow.runtime.services_detection_pin import _NOMINAL_FRAME_DT, _dt_steps, _ema_factor

# Per nominal animate frame; ``_ema_factor`` rescales it for the real ``dt``.
_VELOCITY_ALPHA = 0.3
# Faster than this over one frame is a jump (reset, OSC snap, default-position
# change), not motion: the estimate restarts from the new position instead of
# putting a spike on the wire. Same reference as the HUD speed ramp.
_TELEPORT_SPEED_MPS = 20.0
_ZERO: Vec3 = (0.0, 0.0, 0.0)


@dataclass
class MarkerVelocityState:
    """Per-marker estimator state: the last sampled position and the smoothed velocity."""

    prev_pos: Vec3 | None = None
    velocity: Vec3 = _ZERO


def estimate_marker_velocity(state: MarkerVelocityState, pos: Vec3, dt: float) -> Vec3:
    """Advance *state* with this frame's position and return the velocity in m/s.

    Call once per frame per marker with the marker's current PSN-absolute
    position. The first call only seeds the reference and reports zero; so
    does any jump faster than ``_TELEPORT_SPEED_MPS``.
    """
    prev = state.prev_pos
    state.prev_pos = pos
    if prev is None:
        state.velocity = _ZERO
        return _ZERO
    steps = _dt_steps(dt)
    # The clamped step, so a zero ``dt`` can't divide by zero. The rate is m/s,
    # not the per-frame displacement the detection pin tracks.
    seconds = steps * _NOMINAL_FRAME_DT
    rate_x = (pos[0] - prev[0]) / seconds
    rate_y = (pos[1] - prev[1]) / seconds
    rate_z = (pos[2] - prev[2]) / seconds
    if math.hypot(rate_x, rate_y, rate_z) > _TELEPORT_SPEED_MPS:
        state.velocity = _ZERO
        return _ZERO
    alpha = _ema_factor(_VELOCITY_ALPHA, steps)
    vx, vy, vz = state.velocity
    state.velocity = (
        vx + alpha * (rate_x - vx),
        vy + alpha * (rate_y - vy),
        vz + alpha * (rate_z - vz),
    )
    return state.velocity
