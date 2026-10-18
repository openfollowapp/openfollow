# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""What an Operator Screen picture costs, counted rather than timed, over the bench scenes."""

from __future__ import annotations

import logging

import cairo
import numpy as np
import pytest

from openfollow.runtime import overlay_draw_paths, overlay_draw_scene
from openfollow.runtime.overlay_draw_scene import ConeCache
from openfollow.runtime.overlay_draw_zones import ZoneCache
from openfollow.video.overlay import CairoOverlayRenderer
from tests import _hud_scenes
from tests._hud_cost import count_calls
from tests._hud_scenes import SCENES, frames

pytestmark = pytest.mark.unit

FRAMES = 12


def _draw(name: str) -> cairo.ImageSurface:
    """Every frame of scene ``name`` through one renderer, as the HUD render thread draws them."""
    renderer = CairoOverlayRenderer()
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1920, 1080)
    for width, height, state in frames(name, FRAMES):
        clear = cairo.Context(surface)
        clear.set_operator(cairo.OPERATOR_CLEAR)
        clear.paint()
        renderer.draw(cairo.Context(surface), width, height, state)
    surface.flush()
    return surface


def test_this_checkout_has_every_field_the_scenes_set() -> None:
    for name in SCENES:
        frames(name, 1)
    assert not _hud_scenes.DROPPED


@pytest.mark.parametrize("name", sorted(SCENES))
def test_every_scene_draws_without_an_error_and_puts_something_on_screen(
    name: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="openfollow.video.overlay"):
        surface = _draw(name)
    assert not [r for r in caplog.records if "draw error" in r.getMessage()]
    alpha = np.frombuffer(surface.get_data(), dtype=np.uint8)[3::4]
    assert alpha.any()


def test_the_grid_is_drawn_once_while_it_does_not_change() -> None:
    with count_calls(overlay_draw_scene, "draw_grid") as drawn:
        _draw("full")
    assert drawn[0] == 1


def test_paths_are_drawn_once_while_they_do_not_change() -> None:
    with count_calls(overlay_draw_paths, "_render") as drawn:
        _draw("full")
    assert drawn[0] == 1


def test_zones_are_drawn_again_only_when_a_zone_s_occupancy_changes() -> None:
    occupancy = [
        tuple((occupied, count) for _verts, _color, _name, occupied, count in state.zone_polygons)
        for _w, _h, state in frames("full", FRAMES)
    ]
    changes = sum(a != b for a, b in zip(occupancy, occupancy[1:], strict=False))
    # The scene has to move markers across zones for the count to mean anything.
    assert 0 < changes < FRAMES - 1
    with count_calls(ZoneCache, "_render") as drawn:
        _draw("full")
    assert drawn[0] == 1 + changes


def test_a_cone_is_drawn_again_only_when_its_marker_moves() -> None:
    states = [state for _w, _h, state in frames("cones", FRAMES)]
    moving = sum(
        (a.x, a.y, a.z) != (b.x, b.y, b.z)
        for first, second in zip(states, states[1:], strict=False)
        for a, b in zip(first.markers, second.markers, strict=True)
    )
    with count_calls(ConeCache, "_render") as drawn:
        _draw("cones")
    assert drawn[0] == len(states[0].markers) + moving
