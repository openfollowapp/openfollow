# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Parked markers: controlled markers that keep transmitting while no relative input moves them.

The keyboard, gamepads and 3D mice skip a parked marker; the 2D mouse and OSC still move it.
``app._parked_ids`` is the live set and :func:`is_parked` the one predicate every routing site reads.
"""

from __future__ import annotations

import logging
from typing import Any

from openfollow.configuration import feature_on

logger = logging.getLogger(__name__)


def is_parked(app: Any, marker_id: int | None) -> bool:
    """Whether ``marker_id`` is parked, so relative input must leave it alone."""
    parked = getattr(app, "_parked_ids", None)
    return marker_id is not None and parked is not None and marker_id in parked


def select(app: Any, marker_id: int) -> None:
    """The operator selects ``marker_id``: a pin that follows the selection may drive it again."""
    app._selected_id = marker_id
    app._pin_waits_for_selection = False


def _pin_follows_selection(app: Any) -> bool:
    """Whether Fully Automatic detection runs with its pin following the selected marker."""
    cfg = getattr(app, "_config", None)
    if cfg is None:
        return False
    det = cfg.detection
    return bool(det.enabled) and det.pin_mode == "replace" and det.pin_marker_id < 0 and feature_on(cfg, "detection")


def rehome_selection(app: Any) -> None:
    """Keep the selection on an unparked controlled marker: the next one after it, else none.

    Moving off a marker that parked is no choice of the operator's, so a running Fully Automatic
    pin that follows the selection waits for one (``_pin_waits_for_selection``).
    """
    ids = list(app._controlled_ids)
    selected = app._selected_id
    if selected in ids and not is_parked(app, selected):
        return
    if selected in ids and _pin_follows_selection(app):
        app._pin_waits_for_selection = True
    start = ids.index(selected) if selected in ids else -1
    rotated = ids[start + 1 :] + ids[: start + 1]
    app._selected_id = next((mid for mid in rotated if not is_parked(app, mid)), None)


def set_parked(app: Any, parked: list[int]) -> None:
    """Make ``parked`` (a subset of the controlled ids) the live parked set.

    A mouse grab on the selected marker stays on it when it parks; the selection moves on.
    """
    new = frozenset(parked)
    old: frozenset[int] = getattr(app, "_parked_ids", frozenset())
    if new != old:
        input_manager = getattr(app, "_input_manager", None)
        if input_manager is not None and app._selected_id in new - old:
            input_manager.mouse_handler.keep_grab(app._selected_id)
        app._parked_ids = new
        for marker_id in sorted(new - old):
            logger.info("Marker %d parked", marker_id)
        for marker_id in sorted(old - new):
            logger.info("Marker %d unparked", marker_id)
    rehome_selection(app)
