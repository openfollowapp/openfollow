# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Michel Honold and Paul Hermann
"""HUD panels kept as pictures (:mod:`openfollow.runtime.overlay_picture`): how they look and when they change."""

from __future__ import annotations

import dataclasses
from typing import Any

import cairo
import numpy as np
import pytest

from openfollow.runtime.overlay_layers import LayerContext, Sprite
from openfollow.runtime.overlay_picture import PanelCache, box_picture, ink_picture, paste
from openfollow.runtime.overlay_state import OverlayState
from openfollow.video.overlay import CairoOverlayRenderer
from tests._fake_cairo import DirectPanels, FakeCairo

pytestmark = pytest.mark.unit

W, H = 1280, 720


def _state(**changes: Any) -> OverlayState:
    state = OverlayState(
        ip_text="192.0.2.10",
        hostname_text="of-1.local",
        source_label="rtsp://192.0.2.20/stream",
        video_source_type="rtsp",
        station_name="Spot 1",
        cpu_percent=31.0,
        ram_percent=12.0,
        temperature=48.0,
        keyboard_connected=True,
        show_hud_help=True,
    )
    return dataclasses.replace(state, **changes)


def _hud(
    renderer: CairoOverlayRenderer,
    state: OverlayState,
    scale: float = 1.0,
    w: int = W,
    options: cairo.FontOptions | None = None,
) -> np.ndarray:
    """The HUD alone (no camera), drawn by ``renderer`` onto a cleared picture, as its pixels."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, int(w * scale), int(H * scale))
    surface.set_device_scale(scale, scale)
    cr = cairo.Context(surface)
    if options is not None:
        cr.set_font_options(options)
    renderer.draw(cr, w, H, state)
    surface.flush()
    return np.frombuffer(surface.get_data(), dtype=np.uint8).copy()


def _direct(
    state: OverlayState, scale: float = 1.0, w: int = W, options: cairo.FontOptions | None = None
) -> np.ndarray:
    renderer = CairoOverlayRenderer()
    renderer._panel_cache = DirectPanels()
    return _hud(renderer, state, scale, w, options)


@pytest.mark.parametrize("scale", [1.0, 2.0])
@pytest.mark.parametrize(
    "changes",
    [{}, {"show_hud_help": False}, {"error_message": "Stream refused"}],
    ids=["help", "hint", "error"],
)
def test_kept_panels_look_like_panels_drawn_directly(scale: float, changes: dict[str, Any]) -> None:
    state = _state(**changes)
    kept = _hud(CairoOverlayRenderer(), state, scale)
    assert kept.any()
    assert np.array_equal(kept, _direct(state, scale))


@pytest.mark.parametrize(
    "changes",
    [
        {"ip_text": "192.0.2.11"},
        {"ip_is_fallback": True},
        {"hostname_text": "of-2.local"},
        {"source_label": "rtsp://192.0.2.21/stream"},
        {"video_source_type": "srt"},
        {"station_name": "Spot 2"},
        {"error_message": "Stream refused"},
        {"settings_menu_banner": "Restart required"},
        {"video_failure_text": "No video"},
        {"network_alerts": ["PSN output down"]},
        {"cpu_percent": 72.0},
        {"ram_percent": 40.0},
        {"temperature": 61.0},
        {"show_hud_help": False},
        {"keyboard_connected": False},
        {"mouse_enabled": True},
        {"keyboard_labels": {"toggle_help": "j"}},
    ],
    ids=lambda changes: next(iter(changes)),
)
def test_a_panel_is_drawn_again_when_an_input_it_shows_changes(changes: dict[str, Any]) -> None:
    renderer = CairoOverlayRenderer()
    before = _hud(renderer, _state())
    after = _hud(renderer, _state(**changes))
    assert not np.array_equal(before, after)
    assert np.array_equal(after, _direct(_state(**changes)))


@pytest.mark.parametrize(
    ("changes", "w"),
    [({"keyboard_connected": False}, W), ({}, 1024)],
    ids=["keyboard", "width"],
)
def test_the_menu_help_is_drawn_again_when_an_input_it_shows_changes(changes: dict[str, Any], w: int) -> None:
    renderer, menu = CairoOverlayRenderer(), {"settings_menu_active": True}
    before = _hud(renderer, _state(**menu))
    after = _hud(renderer, _state(**menu, **changes), w=w)
    assert before.shape != after.shape or not np.array_equal(before, after)
    # Over the menu's scrim a kept panel can differ from a direct one by a step; it must match a fresh one.
    assert np.array_equal(after, _hud(CairoOverlayRenderer(), _state(**menu, **changes), w=w))


def test_kept_panels_draw_text_with_the_font_options_of_the_context() -> None:
    options = cairo.FontOptions()
    options.set_antialias(cairo.ANTIALIAS_NONE)
    kept = _hud(CairoOverlayRenderer(), _state(), options=options)
    assert not np.array_equal(kept, _direct(_state()))
    assert np.array_equal(kept, _direct(_state(), options=options))


@pytest.mark.parametrize(("scale", "w"), [(2.0, W), (1.0, 1920)], ids=["device-scale", "width"])
def test_panels_are_drawn_again_for_a_new_picture_size(scale: float, w: int) -> None:
    renderer = CairoOverlayRenderer()
    _hud(renderer, _state())
    assert np.array_equal(_hud(renderer, _state(), scale, w), _direct(_state(), scale, w))


def _paint_box(ctx: cairo.Context) -> None:
    ctx.rectangle(40, 30, 20, 10)
    ctx.fill()


def _canvas(paint: Any) -> np.ndarray:
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
    paint(cairo.Context(surface))
    surface.flush()
    return np.frombuffer(surface.get_data(), dtype=np.uint8).copy()


def test_a_panel_is_one_paste_while_it_does_not_change() -> None:
    cache, painted = PanelCache(), []

    def paint(ctx: cairo.Context) -> None:
        painted.append(1)
        _paint_box(ctx)

    frames = [_canvas(lambda cr: cache.draw(cr, "box", "same", paint, W, H)) for _ in range(3)]
    # Measured once and drawn once into the kept picture.
    assert painted == [1, 1]
    assert all(np.array_equal(frame, _canvas(_paint_box)) for frame in frames)


def test_a_panel_that_failed_to_draw_is_drawn_again_next_picture() -> None:
    cache, fail = PanelCache(), [True]

    def paint(ctx: cairo.Context) -> None:
        if fail[0]:
            raise cairo.Error("out of memory")
        _paint_box(ctx)

    with pytest.raises(cairo.Error):
        cache.draw(FakeCairo(), "box", "same", paint, W, H)
    fail[0] = False
    cr = FakeCairo()
    cache.draw(cr, "box", "same", paint, W, H)
    assert ("paint",) in cr.calls


def test_a_panel_with_nothing_to_show_pastes_nothing() -> None:
    cr = FakeCairo()
    PanelCache().draw(cr, "empty", "same", lambda ctx: None, W, H)
    assert cr.surfaces == []


@pytest.mark.parametrize("scale", [1.0, 2.0])
def test_an_ink_picture_pasted_at_its_origin_is_the_drawing(scale: float) -> None:
    def paint(ctx: cairo.Context) -> None:
        ctx.set_line_width(3.0)
        ctx.arc(100.5, 80.25, 30.0, 0.0, 6.0)
        ctx.stroke()
        ctx.move_to(20, 200)
        ctx.set_font_size(18)
        ctx.show_text("Stage left")

    pictures = []
    for kept in (False, True):
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, int(320 * scale), int(240 * scale))
        surface.set_device_scale(scale, scale)
        cr = cairo.Context(surface)
        if kept:
            picture, x0, y0 = ink_picture(paint, 320, 240, (scale, scale), surface.get_font_options())
            assert picture is not None and picture.get_width() < 320 * scale
            cr.set_source_surface(picture, x0, y0)
            cr.paint()
        else:
            paint(cr)
        surface.flush()
        pictures.append(np.frombuffer(surface.get_data(), dtype=np.uint8).copy())
    assert np.array_equal(*pictures)


def _ring(ctx: cairo.Context) -> None:
    ctx.set_source_rgba(0.2, 0.8, 0.4, 0.7)
    ctx.set_line_width(4.0)
    ctx.arc(60.5, 40.25, 25.0, 0.0, 6.0)
    ctx.stroke()


@pytest.mark.parametrize("scale", [1.0, 2.0])
def test_a_box_picture_pasted_at_its_origin_is_the_drawing_inside_the_box(scale: float) -> None:
    # The box cuts through the ring, so what lies outside it must stay out of the picture.
    x0, y0, x1, y1 = 30, 20, 70, 90
    picture = box_picture(_ring, x0, y0, x1, y1, (scale, scale), None)
    assert (picture.get_width(), picture.get_height()) == (40 * scale, 70 * scale)
    assert picture.get_device_scale() == (scale, scale)

    pictures = []
    for kept in (False, True):
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, int(160 * scale), int(120 * scale))
        surface.set_device_scale(scale, scale)
        cr = cairo.Context(surface)
        if kept:
            paste(cr, picture, x0, y0)
        else:
            cr.rectangle(x0, y0, x1 - x0, y1 - y0)
            cr.clip()
            _ring(cr)
        surface.flush()
        pictures.append(np.frombuffer(surface.get_data(), dtype=np.uint8).copy())
    assert pictures[0].any()
    assert np.array_equal(*pictures)


def test_a_box_picture_draws_text_with_the_font_options_it_is_given() -> None:
    def label(ctx: cairo.Context) -> None:
        ctx.set_font_size(18)
        ctx.move_to(4, 24)
        ctx.show_text("Stage left")

    def pixels(options: cairo.FontOptions | None) -> np.ndarray:
        picture = box_picture(label, 0, 0, 120, 32, (1.0, 1.0), options)
        picture.flush()
        return np.frombuffer(picture.get_data(), dtype=np.uint8).copy()

    gray, none = cairo.FontOptions(), cairo.FontOptions()
    gray.set_antialias(cairo.ANTIALIAS_GRAY)
    none.set_antialias(cairo.ANTIALIAS_NONE)
    assert not np.array_equal(pixels(gray), pixels(none))


def test_a_pasted_picture_is_a_kept_layer_on_a_layered_canvas() -> None:
    picture = box_picture(_ring, 30, 20, 70, 90, (1.0, 1.0), None)
    cr = LayerContext(160, 120, (1.0, 1.0), cairo.FontOptions())
    paste(cr, picture, 30, 20)
    assert cr.finish().sprites == (Sprite(picture, 30, 20, kept=True),)
