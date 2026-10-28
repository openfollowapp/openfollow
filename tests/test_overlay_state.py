# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for :class:`openfollow.runtime.overlay_state.OverlayState`: default
values mirror ``GridConfig``."""

from __future__ import annotations

import pytest

from openfollow.configuration import GridConfig
from openfollow.runtime.overlay_state import OverlayState

pytestmark = pytest.mark.unit


def test_overlay_state_grid_defaults_match_grid_config() -> None:
    # Don't hardcode color/thickness/transparency literals in two places – keep them in sync.
    state = OverlayState()
    grid_defaults = GridConfig()
    assert state.grid_color == grid_defaults.color
    assert state.grid_thickness == grid_defaults.thickness
    assert state.grid_transparency == grid_defaults.transparency
