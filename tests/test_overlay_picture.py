# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""HUD panels kept as pictures (:mod:`openfollow.runtime.overlay_picture`): how they look and when they change."""

from __future__ import annotations

import dataclasses
from typing import Any

import cairo
import numpy as np
import pytest

from openfollow.runtime.overlay_picture import PanelCache, ink_picture
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
    ],
    ids=lambda changes: next(iter(changes)),
)
def test_a_panel_is_drawn_again_when_an_input_it_shows_changes(changes: dict[str, Any]) -> None:
    renderer = CairoOverlayRenderer()
    before = _hud(renderer, _state())
    after = _hud(renderer, _state(**changes))
    assert not np.array_equal(before, after)
    assert np.array_equal(after, _direct(_state(**changes)))


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


def test_a_panel_is_one_paste_while_it_does_not_change() -> None:
    cache, painted = PanelCache(), []

    def paint(ctx: cairo.Context) -> None:
        painted.append(1)
        _paint_box(ctx)

    frames = [FakeCairo() for _ in range(3)]
    for cr in frames:
        cache.draw(cr, "box", "same", paint, W, H)
    # Measured once and drawn once into the kept picture.
    assert painted == [1, 1]
    assert all(cr.calls[-2:] == [("source_surface", 39, 29), ("paint",)] for cr in frames)
    assert all(cr.surfaces[0] is frames[0].surfaces[0] for cr in frames)


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
