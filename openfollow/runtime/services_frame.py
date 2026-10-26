# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Per-frame runtime helper functions."""

from __future__ import annotations

import logging
from typing import Any


def update_video(app: Any, logger: logging.Logger) -> None:
    """Log the source geometry once per receiver."""
    try:
        w, h = app._video_receiver.resolution
        if w <= 0 or h <= 0:
            return
        if not app._video_logged:
            logger.info("Native sink: %dx%d", w, h)
            app._video_logged = True
    except Exception as e:
        logger.debug("Video update error: %s", e)
