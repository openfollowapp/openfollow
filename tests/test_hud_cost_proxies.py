# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""What an Operator Screen picture costs, counted rather than timed, over the bench scenes.

Each cache's draw function is counted on purpose, as the cache tests count ``_path_shapes``
and ``_cone_geometry``: a cache's only observable is the work it saves.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable

import cairo
import pytest

from openfollow.runtime import overlay_draw_hud, overlay_draw_paths, overlay_draw_scene
from openfollow.runtime.overlay_draw_scene import ConeCache, GridCache
from openfollow.runtime.overlay_draw_zones import ZoneCache
from openfollow.runtime.overlay_state import OverlayState
from openfollow.video.overlay import CairoOverlayRenderer
from tests import _hud_scenes
from tests._fake_cairo import FakeCairo, FakeRenderer
from tests._hud_cost import CountingContext, area, count_calls
from tests._hud_scenes import SCENES, frames

pytestmark = pytest.mark.unit

FRAMES = 12


def _draw(name: str, strip: Callable[[OverlayState], OverlayState] = lambda state: state) -> cairo.ImageSurface:
    """Every frame of scene ``name``, after ``strip``, through one renderer, as the HUD render thread draws them."""
    renderer = CairoOverlayRenderer()
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1920, 1080)
    for width, height, state in frames(name, FRAMES):
        state = strip(state)
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


def test_a_scene_field_the_checkout_lacks_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_hud_scenes, "OverlayState", _hud_scenes.overlay_state_without("show_paths"))
    monkeypatch.setattr(_hud_scenes, "DROPPED", set())
    ((_, _, state),) = frames("full", 1)
    assert not hasattr(state, "show_paths")
    assert sorted(_hud_scenes.DROPPED) == ["OverlayState.show_paths"]


@pytest.mark.parametrize("name", sorted(SCENES))
def test_every_scene_draws_without_an_error_and_more_than_the_hud_alone(
    name: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="openfollow.video.overlay"):
        surface = _draw(name)
    assert not [r for r in caplog.records if "draw error" in r.getMessage()]
    # With no camera and no menu only the HUD is drawn; a camera looking away from the stage matches it.
    hud_alone = _draw(name, lambda state: dataclasses.replace(state, camera_params=None, settings_menu_active=False))
    assert bytes(surface.get_data()) != bytes(hud_alone.get_data())


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
        tuple(occupied for _verts, _color, occupied, _height in state.zone_polygons)
        for _w, _h, state in frames("full", FRAMES)
    ]
    changes = sum(a != b for a, b in zip(occupancy, occupancy[1:], strict=False))
    # The scene has to move markers across zone edges for the count to mean anything.
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


def test_a_viewer_only_card_s_group_covers_the_card_not_the_canvas() -> None:
    renderer = CairoOverlayRenderer()
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1920, 1080)
    groups: list[tuple[float, float, float, float]] = []
    for width, height, state in frames("eight_markers", 2):
        cr = CountingContext(surface)
        renderer.draw(cr, width, height, state)
        groups += cr.groups
    viewers = sum(not m.is_controlled for m in frames("eight_markers", 1)[0][2].markers)
    assert len(groups) == 2 * viewers
    # A card is 180 x 67 px; its border and antialiasing reach a few pixels past it.
    assert max(area(g) for g in groups) <= (180 + 8) * (67 + 8)


def test_the_hud_panels_are_drawn_once_while_they_do_not_change() -> None:
    with (
        count_calls(overlay_draw_hud, "draw_bottom_left_info_panel") as info,
        count_calls(overlay_draw_hud, "draw_system_stats") as stats,
        count_calls(overlay_draw_hud, "_draw_corner_help") as help_panel,
    ):
        _draw("light")
    # Each is measured, then drawn into its picture, once.
    assert (info[0], stats[0], help_panel[0]) == (2, 2, 2)


def test_a_crosshair_marker_is_projected_once() -> None:
    ((width, height, state),) = frames("eight_markers", 1)
    state = dataclasses.replace(state, show_crosshair=True, show_z_line=True, show_ground_circle=True)
    cr = cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height))
    with count_calls(overlay_draw_scene, "project") as projected:
        for marker in state.markers:
            overlay_draw_scene.draw_marker(cr, state, marker, width, height)
    assert projected[0] == len(state.markers)


def test_the_grid_is_pasted_only_where_its_lines_run() -> None:
    ((width, height, state),) = frames("full", 1)
    cr = FakeCairo()
    GridCache().draw(FakeRenderer(), cr, state, width, height)
    covered = sum(w * h for _x, _y, w, h in cr.rects)
    assert 0 < covered < 0.5 * width * height
