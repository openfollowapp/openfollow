# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Paul Hermann and Michel Honold
"""Output prediction: the X/Y lead a controlled marker's outputs send ahead of it.

A slow fixture aimed at the real position trails a walking performer. The lead
is the marker's broadcast velocity times its horizon, so every output sends
where the marker will be that far ahead. Z is never predicted.
"""

from __future__ import annotations

import math

from openfollow.runtime.marker_velocity import MarkerVelocityState

# Ceiling on the speed a lead is built from; a performer stays well under it.
PREDICTION_MAX_SPEED_MPS = 5.0
# No lead this long after a repositioning (reset, OSC snap): the velocity it
# leaves behind decays over several frames and would throw the point past it.
PREDICTION_JUMP_HOLD_S = 0.5
_NO_LEAD: tuple[float, float] = (0.0, 0.0)


def prediction_lead(state: MarkerVelocityState, horizon_ms: int) -> tuple[float, float]:
    """The X/Y shift *horizon_ms* ahead at the velocity *state* last estimated."""
    if horizon_ms <= 0 or state.since_jump_s < PREDICTION_JUMP_HOLD_S:
        return _NO_LEAD
    vx, vy = state.velocity[0], state.velocity[1]
    speed = math.hypot(vx, vy)
    if speed == 0.0:
        return _NO_LEAD
    scale = min(speed, PREDICTION_MAX_SPEED_MPS) / speed * (horizon_ms / 1000.0)
    return (vx * scale, vy * scale)
