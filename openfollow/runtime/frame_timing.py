# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The animate-clock step, shared by every per-frame filter.

The filters that run on the frame loop (detection pinning, the assist glide,
the broadcast velocity estimate) are tuned for the ~60fps animate tick.
Re-deriving each per-frame factor for the real elapsed dt keeps the time
constants stable whether animate runs at 60fps (Mac) or slower (Pi) and across
stalls. This is the animate-cadence clock; the tracker's Kalman runs on its own
detection-cadence clock (see ``video/detection``) - two deliberately separate
clocks for two loops at different rates.
"""

from __future__ import annotations

NOMINAL_FRAME_DT = 1.0 / 60.0
# Bounds on the step an EMA is re-derived for: the floor keeps two ticks in the
# same instant from collapsing the alpha to zero, the ceiling keeps a long stall
# from jumping every filter straight to its target.
_MIN_SMOOTH_DT = 1.0 / 1000.0
_MAX_SMOOTH_DT = 0.2


def dt_steps(dt: float) -> float:
    """Elapsed time as a multiple of the nominal animate frame, clamped.

    The clamp is an EMA-stability bound, so this is not a measure of real time:
    anything deriving a rate needs the elapsed seconds themselves.
    """
    return min(max(dt, _MIN_SMOOTH_DT), _MAX_SMOOTH_DT) / NOMINAL_FRAME_DT


def ema_factor(per_frame_alpha: float, steps: float) -> float:
    """Re-derive a per-nominal-frame EMA alpha for ``steps`` frames elapsed.

    At ``steps == 1`` it returns the alpha unchanged (steady 60fps); otherwise it
    compounds the retention so the time constant is frame-rate-independent.
    """
    if steps == 1.0:
        return per_frame_alpha
    base = 1.0 - per_frame_alpha
    if base <= 0.0:
        return 1.0
    # ``base`` is > 0 here, so the power is real; float() keeps mypy from widening
    # ``float ** float`` (which can be complex) to Any.
    return 1.0 - float(base**steps)
