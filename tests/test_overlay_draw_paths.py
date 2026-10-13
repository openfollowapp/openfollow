# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Paths on the video overlay: a line through each curve, its ends marked, white while locked."""

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
    # Drawn grey with every path, then white over it.
    assert colours[-1] == COLOR_PATH_LOCKED
    assert widths[-1] == PATH_LOCKED_LINE_WIDTH
    assert colours == [COLOR_PATH, COLOR_PATH_LOCKED]


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


# --------------------------------------------------------------------------- #
# PathCache – every path as one picture, drawn again only on a change
# --------------------------------------------------------------------------- #

CURVE = [(-2.0 + 0.1 * i, 2.0 + 0.04 * i * i, 0.0) for i in range(30)]
RELEASE_ENDS = [((-2.0, 2.0, 0.0), (-1.9, 2.0, 0.0), True), (CURVE[-1], CURVE[-2], False)]


def _scene(**overrides: object) -> OverlayState:
    state = _state()
    state.path_lines = [(LINE, ENDS, False), (CURVE, RELEASE_ENDS, True)]
    for name, value in overrides.items():
        setattr(state, name, value)
    return state


def _pixels(surface: object) -> np.ndarray:
    surface.flush()
    h, stride = surface.get_height(), surface.get_stride()
    return np.ndarray((h, stride // 4, 4), np.uint8, surface.get_data())[:, : surface.get_width()].astype(int)


@pytest.fixture
def shape_calls(monkeypatch) -> list[tuple[bool, ...]]:
    """Each projection, as the locked flags of the paths it projected."""
    from openfollow.runtime import overlay_draw_paths as mod

    calls: list[tuple[bool, ...]] = []
    real = mod._path_shapes

    def _counting(state, w, h, lines):
        calls.append(tuple(locked for _curve, _ends, locked in lines))
        return real(state, w, h, lines)

    monkeypatch.setattr(mod, "_path_shapes", _counting)
    return calls


@pytest.mark.parametrize(
    ("scale", "overrides"),
    [(1.0, {}), (2.0, {}), (1.0, {"lens_k1": -0.15, "lens_k2": 0.02})],
    ids=["screen", "hidpi", "lens"],
)
def test_cached_paths_look_like_paths_drawn_directly(scale: float, overrides: dict) -> None:
    import cairo

    from openfollow.runtime.overlay_draw_paths import PathCache

    state = _scene(**overrides)
    surfaces = []
    for cache in (None, PathCache()):
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, int(1920 * scale), int(1080 * scale))
        surface.set_device_scale(scale, scale)
        draw_paths(FakeRenderer(), cairo.Context(surface), state, 1920, 1080, cache)
        surfaces.append(_pixels(surface))
    direct, cached = surfaces
    assert direct[..., 3].any()
    assert np.abs(direct - cached).max() <= 1


def test_paths_that_did_not_change_are_not_drawn_again(shape_calls) -> None:
    from openfollow.runtime.overlay_draw_paths import PathCache

    cache, state = PathCache(), _scene()
    for _ in range(3):
        cr = FakeCairo()
        draw_paths(FakeRenderer(), cr, state, 1920, 1080, cache)
        assert ("paint",) in cr.calls
    assert shape_calls == [(False, True), (True,)]


BOTH = [(False, True), (True,), (False, True), (True,)]


@pytest.mark.parametrize(
    ("change", "calls"),
    [
        ({"line": (list(LINE[:2]), ENDS, False)}, [(False, True), (True,), (False, True)]),
        (
            {"line": (LINE, [ENDS[0], ((1.0, 2.0, 0.0), (0.5, 2.0, 0.0), True)], False)},
            [(False, True), (True,), (False, True)],
        ),
        ({"line": (LINE, ENDS, True)}, [(False, True), (True,), (True, True)]),
        ({"state": {"camera_params": np.array([0.2, -6.0, 2.5, -15.0, 0.0, 0.0, 60.0])}}, BOTH),
        ({"state": {"lens_k1": 0.05}}, BOTH),
        ({"state": {"lens_k2": 0.02}}, BOTH),
        ({"size": (1280, 720)}, BOTH),
        ({"scale": 2.0}, BOTH),
    ],
    ids=["curve", "end", "locked", "camera", "k1", "k2", "frame", "scale"],
)
def test_anything_a_path_is_drawn_from_draws_its_picture_again(shape_calls, change: dict, calls: list) -> None:
    from types import SimpleNamespace

    from openfollow.runtime.overlay_draw_paths import PathCache

    cache = PathCache()
    draw_paths(FakeRenderer(), FakeCairo(), _scene(), 1920, 1080, cache)
    state = _scene(**change.get("state", {}))
    if "line" in change:
        state.path_lines = [change["line"], *state.path_lines[1:]]
    cr = FakeCairo()
    scale = change.get("scale", 1.0)
    cr.get_target = lambda: SimpleNamespace(get_device_scale=lambda: (scale, scale))  # type: ignore[method-assign]
    w, h = change.get("size", (1920, 1080))
    draw_paths(FakeRenderer(), cr, state, w, h, cache)
    assert shape_calls == calls


def test_a_marker_moving_to_another_path_draws_only_the_paths_it_is_on(shape_calls) -> None:
    from openfollow.runtime.overlay_draw_paths import PathCache

    cache = PathCache()
    draw_paths(FakeRenderer(), FakeCairo(), _scene(), 1920, 1080, cache)
    moved = _scene(path_lines=[(LINE, ENDS, True), (CURVE, RELEASE_ENDS, False)])
    cr = FakeCairo()
    draw_paths(FakeRenderer(), cr, moved, 1920, 1080, cache)
    assert shape_calls == [(False, True), (True,), (True,)]
    assert cr.calls.count(("paint",)) == 2


def test_with_no_marker_on_a_path_only_the_paths_are_pasted(shape_calls) -> None:
    from openfollow.runtime.overlay_draw_paths import PathCache

    cr = FakeCairo()
    draw_paths(FakeRenderer(), cr, _scene(path_lines=[(LINE, ENDS, False)]), 1920, 1080, PathCache())
    assert shape_calls == [(False,)]
    assert cr.calls.count(("paint",)) == 1


@pytest.mark.parametrize(
    "line",
    [[(-1.0, -20.0, 0.0), (1.0, -20.0, 0.0)], [(60.0, 2.0, 0.0), (61.0, 2.0, 0.0)]],
    ids=["behind-camera", "off-frame"],
)
def test_paths_nowhere_on_screen_paint_nothing(line: list) -> None:
    from openfollow.runtime.overlay_draw_paths import PathCache

    state = _scene(path_lines=[(line, [], False)])
    cr = FakeCairo()
    draw_paths(FakeRenderer(), cr, state, 1920, 1080, PathCache())
    assert ("paint",) not in cr.calls


def test_a_path_cache_asked_to_draw_without_a_camera_draws_nothing() -> None:
    from openfollow.runtime.overlay_draw_paths import PathCache

    cr = FakeCairo()
    PathCache().draw(cr, _scene(camera_params=None), 1920, 1080)
    assert cr.calls == []
