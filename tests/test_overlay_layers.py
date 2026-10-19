# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The layered Operator Screen: kept pictures by reference, the rest cut to its ink."""

from __future__ import annotations

import dataclasses
from typing import Any

import cairo
import numpy as np
import numpy.typing as npt
import pytest

from openfollow.runtime.overlay_layers import LayerContext, Layers, Sprite, cairo_context, cut, keep, mark_top
from openfollow.video.overlay import CairoOverlayRenderer
from tests._hud_scenes import SCENES, frames

pytestmark = pytest.mark.unit

W, H = 200, 100
SCALE = (1.0, 1.0)


def _layers() -> LayerContext:
    return LayerContext(W, H, SCALE, cairo.FontOptions())


def _box(cr: Any, x: float, y: float, w: float = 10, h: float = 10) -> None:
    cr.set_source_rgba(1, 0, 0, 1)
    cr.rectangle(x, y, w, h)
    cr.fill()


def _composite(layers: Layers) -> cairo.ImageSurface:
    w, h = layers.width, layers.height
    sx, sy = layers.scale
    out = cairo.ImageSurface(cairo.FORMAT_ARGB32, round(w * sx), round(h * sy))
    out.set_device_scale(sx, sy)
    ctx = cairo.Context(out)
    for s in layers.sprites:
        ctx.set_source_surface(s.picture, s.x, s.y)
        ctx.paint()
    return out


def _pixels(surface: cairo.ImageSurface) -> npt.NDArray[np.uint8]:
    surface.flush()
    return np.frombuffer(surface.get_data(), dtype=np.uint8).reshape(surface.get_height(), surface.get_stride())


def test_a_cut_splits_the_drawing_into_sprites_cropped_to_their_ink() -> None:
    cr = _layers()
    _box(cr, 20, 30)
    cut(cr)
    _box(cr, 150, 60)

    sprites = cr.finish().sprites
    assert [(s.x, s.y, s.picture.get_width(), s.picture.get_height(), s.kept) for s in sprites] == [
        (19, 29, 12, 12, False),
        (149, 59, 12, 12, False),
    ]


def test_a_kept_picture_passes_by_reference_between_the_drawings_around_it() -> None:
    kept = cairo.ImageSurface(cairo.FORMAT_ARGB32, 5, 5)
    cr = _layers()
    _box(cr, 20, 30)

    assert keep(cr, kept, 40, 50) is True
    _box(cr, 150, 60)
    sprites = cr.finish().sprites
    assert sprites[1] == Sprite(kept, 40, 50, kept=True)
    assert [s.x for s in sprites] == [19, 40, 149]


@pytest.mark.parametrize("enter", ["save", "group", "group-with-content", "translate"])
def test_below_the_top_level_nothing_is_cut_and_a_kept_picture_is_pasted(enter: str) -> None:
    kept = cairo.ImageSurface(cairo.FORMAT_ARGB32, 5, 5)
    cr = _layers()
    if enter == "save":
        cr.save()
    elif enter == "group":
        cr.push_group()
    elif enter == "group-with-content":
        cr.push_group_with_content(cairo.CONTENT_COLOR_ALPHA)
    else:
        cr.translate(1, 0)
    _box(cr, 20, 30)
    cut(cr)
    kept_out = keep(cr, kept, 40, 50)
    _box(cr, 150, 60)
    if enter == "save":
        cr.restore()
    elif enter.startswith("group"):
        cr.pop_group_to_source()
        cr.paint()

    assert kept_out is False
    assert len(cr.finish().sprites) == 1


def test_closing_a_save_or_group_returns_to_the_top_level() -> None:
    cr = _layers()
    cr.save()
    cr.push_group_with_content(cairo.CONTENT_COLOR_ALPHA)
    _box(cr, 20, 30)
    cr.set_source(cr.pop_group())
    cr.paint()
    cr.restore()
    cut(cr)
    _box(cr, 150, 60)

    assert len(cr.finish().sprites) == 2


def test_a_marked_save_is_the_top_level_until_it_is_restored() -> None:
    cr = _layers()
    cr.save()
    mark_top(cr)
    _box(cr, 20, 30)
    cut(cr)
    _box(cr, 150, 60)
    cr.restore()
    cr.save()
    cut(cr)
    _box(cr, 100, 20)
    cr.restore()

    assert [s.x for s in cr.finish().sprites] == [19, 99]


def test_an_empty_drawing_and_ink_off_the_canvas_make_no_sprite() -> None:
    cr = _layers()
    cut(cr)
    _box(cr, W + 50, H + 50)

    assert cr.finish().sprites == ()


def test_ink_past_the_edge_is_cropped_to_the_canvas() -> None:
    cr = _layers()
    _box(cr, -20, H - 5, 40, 40)

    (sprite,) = cr.finish().sprites
    assert (sprite.x, sprite.y, sprite.picture.get_width(), sprite.picture.get_height()) == (0, H - 6, 21, 6)


def test_an_extent_past_cairos_range_takes_the_whole_canvas() -> None:
    cr = _layers()
    cr.set_source_rgba(1, 1, 1, 1)
    cr.move_to(-1e8, 50)
    cr.line_to(1e8, 50)
    cr.stroke()

    (sprite,) = cr.finish().sprites
    assert (sprite.x, sprite.y, sprite.picture.get_width(), sprite.picture.get_height()) == (0, 0, W, H)


def test_sprites_are_drawn_at_the_canvas_device_scale() -> None:
    cr = LayerContext(W, H, (2.0, 2.0), cairo.FontOptions())
    _box(cr, 20, 30)

    (sprite,) = cr.finish().sprites
    assert (sprite.picture.get_width(), sprite.picture.get_device_scale()) == (24, (2.0, 2.0))


def test_the_real_context_is_the_current_segments_on_a_layered_canvas_and_itself_otherwise() -> None:
    cr = _layers()
    plain = cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1))

    _box(cairo_context(cr), 20, 30)
    assert cairo_context(plain) is plain
    assert len(cr.finish().sprites) == 1
    assert keep(plain, cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1), 0, 0) is False
    cut(plain)


@pytest.mark.parametrize("scene", sorted(SCENES))
def test_a_layered_picture_composites_to_the_canvas_sized_one(scene: str) -> None:
    for w, h, state in frames(scene, count=3):
        whole = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        CairoOverlayRenderer().draw(cairo.Context(whole), w, h, state)
        layered = LayerContext(w, h, SCALE, whole.get_font_options())
        CairoOverlayRenderer().draw(layered, w, h, state)
        layers = layered.finish()

        diff = np.abs(_pixels(_composite(layers)).astype(int) - _pixels(whole).astype(int))
        assert (diff > 8).sum() < 50, f"{scene}: {(diff > 8).sum()} bytes differ"
        assert len(layers.sprites) > 1


def _grid(layers: Layers) -> Sprite:
    return next(s for s in layers.sprites if s.kept and s.picture.get_width() == layers.width)


def test_a_new_grid_on_a_layered_canvas_is_a_new_picture_leaving_the_shown_one_as_it_was() -> None:
    renderer = CairoOverlayRenderer()
    w, h, state = frames("full", count=1)[0]
    first = LayerContext(w, h, SCALE, cairo.FontOptions())
    renderer.draw(first, w, h, state)
    shown = _grid(first.finish()).picture
    before = _pixels(shown).copy()
    assert state.grid_config is not None
    moved = dataclasses.replace(state, grid_config=(13.0, *state.grid_config[1:]))

    again = LayerContext(w, h, SCALE, cairo.FontOptions())
    renderer.draw(again, w, h, moved)

    assert _grid(again.finish()).picture is not shown
    assert np.array_equal(_pixels(shown), before)


@pytest.mark.parametrize("scene", sorted(set(SCENES) - {"menu"}))
def test_each_marker_card_and_hint_is_its_own_small_sprite(scene: str) -> None:
    for w, h, state in frames(scene, count=3):
        layered = LayerContext(w, h, SCALE, cairo.FontOptions())
        CairoOverlayRenderer().draw(layered, w, h, state)
        drawn = [s.picture for s in layered.finish().sprites if not s.kept]
        assert max(p.get_width() * p.get_height() for p in drawn) < 40_000


def test_every_segment_draws_text_with_the_canvas_font_options() -> None:
    options = cairo.FontOptions()
    options.set_antialias(cairo.ANTIALIAS_NONE)
    cr = LayerContext(W, H, SCALE, options)
    for x in (10, 110):
        cr.set_source_rgba(1, 1, 1, 1)
        cr.set_font_size(20)
        cr.move_to(x, 50)
        cr.show_text("Ag")
        cut(cr)

    for sprite in cr.finish().sprites:
        alpha = _pixels(sprite.picture).reshape(sprite.picture.get_height(), -1, 4)[..., 3]
        assert set(np.unique(alpha)) <= {0, 255}


def test_the_about_screen_keeps_its_logo_on_a_layered_canvas() -> None:
    w, h, state = frames("light", count=1)[0]
    about = dataclasses.replace(state, about_active=True)
    whole = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    CairoOverlayRenderer().draw(cairo.Context(whole), w, h, about)
    layered = LayerContext(w, h, SCALE, whole.get_font_options())
    CairoOverlayRenderer().draw(layered, w, h, about)

    diff = np.abs(_pixels(_composite(layered.finish())).astype(int) - _pixels(whole).astype(int))
    assert (diff > 8).sum() < 50
