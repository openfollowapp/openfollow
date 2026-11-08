# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for :class:`openfollow.runtime.overlay_state.OverlayState`: default
values mirror ``GridConfig``."""

from __future__ import annotations

import pytest

from openfollow.configuration import GridConfig
from openfollow.runtime.overlay_layout import scene_view
from openfollow.runtime.overlay_state import OverlayState

pytestmark = pytest.mark.unit


def test_overlay_state_grid_defaults_match_grid_config() -> None:
    # Don't hardcode color/thickness/transparency literals in two places – keep them in sync.
    state = OverlayState()
    grid_defaults = GridConfig()
    assert state.grid_color == grid_defaults.color
    assert state.grid_thickness == grid_defaults.thickness
    assert state.grid_transparency == grid_defaults.transparency


def test_overlay_state_draws_the_scene_on_the_whole_canvas_by_default() -> None:
    """A fresh state carries no scene view: the scene fills the canvas at square pixels."""
    state = OverlayState()
    assert state.scene_view is None
    assert state.pixel_aspect == 1.0


def test_overlay_state_pixel_aspect_is_the_scene_views_stretch() -> None:
    state = OverlayState(scene_view=scene_view(1000, 1000, 704, 576, (12, 11)))
    assert state.pixel_aspect == pytest.approx(12 / 11)
