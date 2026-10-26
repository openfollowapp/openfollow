# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Kept Operator Screen pictures: how one is drawn and pasted, a drawing cropped to its ink, and the HUD panels."""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any, TypeAlias

import cairo

from openfollow.runtime.overlay_layers import keep

Paint: TypeAlias = "Callable[[cairo.Context[Any]], None]"


def target_font_options(cr: Any) -> cairo.FontOptions:
    """The font options text on ``cr`` is drawn with: its target's, overridden by its own."""
    options: cairo.FontOptions = cr.get_target().get_font_options()
    options.merge(cr.get_font_options())
    return options


def ink_picture(
    paint: Paint, w: int, h: int, scale: tuple[float, float], options: cairo.FontOptions
) -> tuple[cairo.ImageSurface | None, int, int]:
    """``paint`` drawn into a picture cropped to its ink within the ``w`` x ``h`` canvas, and where it goes.

    Pasted at the returned origin, the picture is pixel for pixel what ``paint`` draws on the canvas.
    ``None`` when ``paint`` leaves no ink there.
    """
    sx, sy = scale
    # Ink extents cover a miter tip or a glyph past its box; scale and font
    # options are the target's, as hinted glyph metrics depend on both.
    recording = cairo.RecordingSurface(cairo.CONTENT_COLOR_ALPHA, None)
    recording.set_device_scale(sx, sy)
    measure = cairo.Context(recording)
    measure.set_font_options(options)
    paint(measure)
    ix, iy, iw, ih = recording.ink_extents()
    if iw == 0 or ih == 0:
        return None, 0, 0
    if iw < 0 or ih < 0:
        # Cairo's fixed point wraps an extent past 2**23 device pixels, which a point near the horizon reaches.
        x0, y0, x1, y1 = 0, 0, w, h
    else:
        # One pixel more: Quartz renders a glyph up to a pixel past the extents it measures.
        x0 = max(0, math.floor(ix / sx) - 1)
        y0 = max(0, math.floor(iy / sy) - 1)
        x1 = min(w, math.ceil((ix + iw) / sx) + 1)
        y1 = min(h, math.ceil((iy + ih) / sy) + 1)
        if x1 <= x0 or y1 <= y0:
            return None, 0, 0
    # Painted again rather than replayed: a replayed recording lands text differently on a HiDPI picture.
    return box_picture(paint, x0, y0, x1, y1, scale, options), x0, y0


def box_picture(
    paint: Paint, x0: int, y0: int, x1: int, y1: int, scale: tuple[float, float], options: cairo.FontOptions | None
) -> cairo.ImageSurface:
    """``paint`` drawn into a new picture of the canvas from (``x0``, ``y0``) to (``x1``, ``y1``).

    Pasted at (``x0``, ``y0``), the picture is pixel for pixel what ``paint`` draws inside that box.
    ``options`` are the target's font options; ``None`` leaves cairo's defaults, for a drawing without text.
    """
    sx, sy = scale
    # Drawn at the target's device scale, so a HiDPI screen gets a sharp picture.
    picture = cairo.ImageSurface(cairo.FORMAT_ARGB32, math.ceil((x1 - x0) * sx), math.ceil((y1 - y0) * sy))
    picture.set_device_scale(sx, sy)
    ctx = cairo.Context(picture)
    if options is not None:
        ctx.set_font_options(options)
    ctx.translate(-x0, -y0)
    paint(ctx)
    return picture


def paste(cr: Any, picture: cairo.ImageSurface | None, x0: int, y0: int) -> None:
    """Put a kept picture on ``cr`` at (``x0``, ``y0``): a layer on a layered canvas, else one paint."""
    if picture is not None and not keep(cr, picture, x0, y0):
        cr.set_source_surface(picture, x0, y0)
        cr.paint()


class PanelCache:
    """HUD panels kept as pictures, each drawn again only when an input it is drawn from changes.

    Panels such as the help or the info panel change a few times a minute; while they hold, each
    is one paste instead of measuring, wrapping and drawing its text every picture.
    """

    def __init__(self) -> None:
        self._entries: dict[str, tuple[object, cairo.ImageSurface | None, int, int]] = {}

    def draw(self, cr: Any, slot: str, key: object, paint: Paint, w: int, h: int) -> None:
        """Paste the panel in ``slot``; ``key`` holds every input ``paint`` draws it from, or it freezes on screen."""
        scale = cr.get_target().get_device_scale()
        options = target_font_options(cr)
        full_key = (key, w, h, scale, options)
        entry = self._entries.get(slot)
        if entry is None or entry[0] != full_key:
            entry = (full_key, *ink_picture(paint, w, h, scale, options))
            self._entries[slot] = entry
        _, picture, x0, y0 = entry
        paste(cr, picture, x0, y0)
