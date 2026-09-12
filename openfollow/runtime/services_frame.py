# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Per-frame runtime helper functions."""

from __future__ import annotations

import logging
from typing import Any

from openfollow.runtime.overlay_state import OverlayState
from openfollow.runtime_metrics import OverlayStatePool


def update_video(app: Any, logger: logging.Logger) -> None:
    """Update video resolution-dependent runtime state."""
    try:
        w, h = app._video_receiver.resolution
        if w <= 0 or h <= 0:
            return
        if not app._video_logged:
            logger.info("Native sink: %dx%d", w, h)
            app._video_logged = True
        # The hint tracks the live source for the whole session rather than
        # latching its first figure: the HUD projects across the canvas while
        # calibration is solved against the input, so a window left at the
        # previous source's aspect ratio slides the overlay off the video.
        if app._video_aspect != (w, h):
            canvas = app._canvas
            if hasattr(canvas, "set_aspect_ratio"):
                canvas.set_aspect_ratio(w, h)
                app._video_aspect = (w, h)
    except Exception as e:
        logger.debug("Video update error: %s", e)


def prepare_overlay_state_swap(
    overlay_state_pool: OverlayStatePool,
    old_overlay_state: OverlayState | None,
    new_overlay_state: OverlayState,
) -> OverlayState:
    """Release previous pooled overlay state before storing the next one."""
    if old_overlay_state is not None:
        overlay_state_pool.release(old_overlay_state)
    return new_overlay_state
