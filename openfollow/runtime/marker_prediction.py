# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Paul Hermann and Michel Honold
"""Output prediction: the X/Y prediction a controlled marker's outputs send ahead of it.

A slow fixture aimed at the real position trails a walking performer. The prediction
is the marker's broadcast velocity times its horizon, so every output sends
where the marker will be that far ahead. Z is never predicted.

The prediction grows towards that target with a time constant and shrinks to it at once:
a wobbling stick or a short nudge never builds a full prediction, and slowing down takes
it back as fast as the marker slows. Below a walking pace it fades out, so a marker at
rest with a noisy source keeps still.
"""

from __future__ import annotations

import math

from openfollow.runtime.marker_velocity import MarkerVelocityState

# Ceiling on the speed a prediction is built from; a performer stays well under it.
PREDICTION_MAX_SPEED_MPS = 5.0
# No prediction this long after a repositioning (reset, OSC snap): the velocity it
# leaves behind decays over several frames and would throw the point past it.
PREDICTION_JUMP_HOLD_S = 0.5
# Time constant of a growing prediction.
PREDICTION_RISE_S = 0.25
# No prediction below the first speed, the full one from the second, linear between.
PREDICTION_FADE_MPS = (0.2, 0.5)
_NO_PREDICTION: tuple[float, float] = (0.0, 0.0)


def prediction_offset(state: MarkerVelocityState, horizon_ms: int, dt: float) -> tuple[float, float]:
    """Advance and return the X/Y shift *horizon_ms* ahead, *dt* seconds after the last call."""
    if horizon_ms <= 0 or state.since_jump_s < PREDICTION_JUMP_HOLD_S:
        state.prediction = _NO_PREDICTION
        return _NO_PREDICTION
    vx, vy = state.velocity[0], state.velocity[1]
    speed = math.hypot(vx, vy)
    low, full = PREDICTION_FADE_MPS
    fade = min(max((speed - low) / (full - low), 0.0), 1.0)
    scale = fade * min(speed, PREDICTION_MAX_SPEED_MPS) / speed * (horizon_ms / 1000.0) if fade else 0.0
    tx, ty = vx * scale, vy * scale
    px, py = state.prediction
    if math.hypot(tx, ty) <= math.hypot(px, py):
        state.prediction = (tx, ty)
    else:
        k = 1.0 - math.exp(-max(dt, 0.0) / PREDICTION_RISE_S)
        state.prediction = (px + (tx - px) * k, py + (ty - py) * k)
    return state.prediction
