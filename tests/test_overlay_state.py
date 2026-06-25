# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for :class:`openfollow.runtime.overlay_state.OverlayState`: default
values mirror ``GridConfig`` and ``reset()`` restores the pooled state."""

from __future__ import annotations

import numpy as np
import pytest

from openfollow.configuration import GridConfig
from openfollow.runtime.overlay_state import MarkerOverlayData, OverlayState
from openfollow.units import UnitSystem

pytestmark = pytest.mark.unit


def test_overlay_state_grid_defaults_match_grid_config() -> None:
    # Don't hardcode color/thickness/transparency literals in two places – keep them in sync.
    state = OverlayState()
    grid_defaults = GridConfig()
    assert state.grid_color == grid_defaults.color
    assert state.grid_thickness == grid_defaults.thickness
    assert state.grid_transparency == grid_defaults.transparency


def test_overlay_state_reset_grid_defaults_match_grid_config() -> None:
    # Same contract for ``reset()`` – the pooled OverlayState reused across
    # frames must re-initialise to GridConfig's declared defaults.
    state = OverlayState()
    state.grid_color = "#aaaaaa"
    state.grid_thickness = 5
    state.grid_transparency = 0.1
    state.reset()
    grid_defaults = GridConfig()
    assert state.grid_color == grid_defaults.color
    assert state.grid_thickness == grid_defaults.thickness
    assert state.grid_transparency == grid_defaults.transparency


def test_overlay_state_reset_restores_defaults() -> None:
    from openfollow.runtime.overlay_state import VirtualFaderDisplayData

    state = OverlayState()
    state.markers.append(MarkerOverlayData(marker_id=1, x=1.0, y=2.0, z=3.0, color="#ffffff"))
    state.selected_id = 1
    state.camera_params = np.array([1.0, 2.0, 3.0], dtype=np.float64)
    state.lens_k1 = -0.15
    state.lens_k2 = 0.04
    state.grid_config = (1.0, 2.0, 3.0, 4.0, 5.0, 6.0)
    state.show_ball = False
    state.show_hud_help = False
    state.video_source_type = "srt"
    state.source_label = "My Source"
    state.discovered_sources.extend(["A", "B"])
    state.available_interfaces.extend(["10.0.0.2"])
    state.detection_show_boxes = True
    state.detection_box_color = "#123456"
    # virtual_faders_display must clear on reset to avoid stale entries.
    state.virtual_faders_display.append(
        VirtualFaderDisplayData(
            index=1,
            name="Master",
            value=0.5,
            picked_up=True,
        )
    )
    # status_flags clears on reset to avoid stale frame warnings.
    state.status_flags.append(("midi_patch_missing", "Stale warning"))
    state.unit_system = UnitSystem.IMPERIAL

    state.reset()

    assert state.markers == []
    assert state.selected_id is None
    assert state.camera_params is None
    assert state.lens_k1 == 0.0
    assert state.lens_k2 == 0.0
    assert state.grid_config is None
    assert state.show_ball is True
    assert state.show_hud_help is True
    assert state.video_source_type == "ndi"
    assert state.source_label == ""
    assert state.discovered_sources == []
    assert state.available_interfaces == []
    assert state.detection_show_boxes is False
    assert state.detection_box_color == "#808080"
    assert state.unit_system is UnitSystem.METRIC
    assert state.source_selection_title == "SELECT SOURCE"
    assert len(state._marker_pool) == 16
    assert state.virtual_faders_display == []
    assert state.status_flags == []
