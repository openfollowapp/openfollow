# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Paths on the video overlay: a line through each curve, a dot on each point, white while locked."""

from __future__ import annotations

import numpy as np
import pytest

from openfollow.runtime.overlay_draw_paths import draw_paths
from openfollow.runtime.overlay_draw_style import (
    COLOR_PATH,
    COLOR_PATH_LOCKED,
    PATH_ARROW_LENGTH,
    PATH_LINE_WIDTH,
    PATH_LOCKED_LINE_WIDTH,
    PATH_POINT_RADIUS,
)
from openfollow.runtime.overlay_state import OverlayState
from tests._fake_cairo import FakeCairo, FakeRenderer

pytestmark = pytest.mark.unit

LINE = [(-1.0, 2.0, 0.0), (0.0, 2.0, 0.0), (1.0, 2.0, 0.0)]
ENDS = [((-1.0, 2.0, 0.0), (-0.5, 2.0, 0.0), False), ((1.0, 2.0, 0.0), (0.5, 2.0, 0.0), False)]


def _state(*, locked: bool = False, show: bool = True, camera: bool = True) -> OverlayState:
    state = OverlayState()
    if camera:
        state.camera_params = np.array([0.0, -6.0, 2.5, -15.0, 0.0, 0.0, 60.0], dtype=np.float64)
    state.show_paths = show
    state.path_lines = [(LINE, ENDS, locked)]
    return state


def _rgba_and_widths(cr: FakeCairo) -> tuple[list[tuple[float, ...]], list[float]]:
    colours = [call[1:] for call in cr.calls if call[0] == "rgba"]
    widths = [call[1] for call in cr.calls if call[0] == "line_width"]
    return colours, widths


def test_a_free_path_is_a_grey_line_with_a_dot_on_each_end_that_holds() -> None:
    cr = FakeCairo()
    draw_paths(FakeRenderer(), cr, _state(), 1920, 1080)
    colours, widths = _rgba_and_widths(cr)
    assert colours == [COLOR_PATH]
    assert widths == [PATH_LINE_WIDTH]
    assert cr.strokes == 1
    assert len(cr.line_tos) == len(LINE) - 1
    assert [r for _x, _y, r in cr.arcs] == [PATH_POINT_RADIUS, PATH_POINT_RADIUS]
    assert cr.fills == 2


def test_a_path_with_a_marker_on_it_is_white_and_heavier() -> None:
    cr = FakeCairo()
    draw_paths(FakeRenderer(), cr, _state(locked=True), 1920, 1080)
    colours, widths = _rgba_and_widths(cr)
    assert colours == [COLOR_PATH_LOCKED]
    assert widths == [PATH_LOCKED_LINE_WIDTH]


@pytest.mark.parametrize(("show", "camera"), [(False, True), (True, False)])
def test_hidden_paths_or_no_camera_draw_nothing(show: bool, camera: bool) -> None:
    cr = FakeCairo()
    draw_paths(FakeRenderer(), cr, _state(show=show, camera=camera), 1920, 1080)
    assert cr.calls == []


def test_a_path_behind_the_camera_draws_no_line_and_no_dots() -> None:
    state = _state()
    behind = [(0.0, -20.0, 0.0), (1.0, -20.0, 0.0)]
    state.path_lines = [(behind, [(behind[0], behind[1], True), (behind[1], behind[0], False)], False)]
    cr = FakeCairo()
    draw_paths(FakeRenderer(), cr, state, 1920, 1080)
    assert cr.strokes == 0
    assert cr.arcs == []


def test_an_end_that_releases_points_an_arrowhead_out_of_the_path() -> None:
    state = _state()
    state.path_lines = [(LINE, [ENDS[0], (ENDS[1][0], ENDS[1][1], True)], False)]
    cr = FakeCairo()
    draw_paths(FakeRenderer(), cr, state, 1920, 1080)
    # The holding end keeps its dot; the releasing end is a filled triangle instead.
    assert len(cr.arcs) == 1
    assert cr.closes == 1
    assert cr.fills == 2
    tip = cr.move_tos[-1]
    corners = cr.line_tos[-2:]
    centre = np.mean(np.array([tip, *corners]), axis=0)
    # Out of the path is to the right (+x on screen) for this end: the tip lies beyond the base.
    assert tip[0] > max(c[0] for c in corners)
    assert np.hypot(*(np.array(tip) - np.mean(np.array(corners), axis=0))) == pytest.approx(PATH_ARROW_LENGTH)
    assert centre[0] > 0


def test_an_end_seen_end_on_falls_back_to_a_dot() -> None:
    state = _state()
    end = (1.0, 2.0, 0.0)
    state.path_lines = [(LINE, [(end, end, True)], False)]
    cr = FakeCairo()
    draw_paths(FakeRenderer(), cr, state, 1920, 1080)
    assert len(cr.arcs) == 1
    assert cr.closes == 0
