# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Michel Honold and Paul Hermann
"""The Operator Screen as a list of pictures instead of one canvas-sized picture.

Kept pictures pass through by reference; what is drawn between them is recorded
and cut to its ink at each kept picture and at each ``cut``. A compositor puts the
list on screen in order, uploading a kept picture only when it is a new one.
"""

from __future__ import annotations

import functools
import math
from dataclasses import dataclass
from typing import Any

import cairo


@dataclass(frozen=True)
class Sprite:
    """A picture and where its top-left corner goes, in canvas units.

    A kept picture is the same object for as long as its drawing holds and is never
    drawn into again, so a compositor may keep what it made of it by identity. Every
    picture is flushed before it is listed, so a compositor on another thread only reads it.
    """

    picture: cairo.ImageSurface
    x: int
    y: int
    kept: bool


@dataclass(frozen=True)
class Layers:
    """One HUD picture as sprites, bottom first."""

    width: int
    height: int
    scale: tuple[float, float]
    sprites: tuple[Sprite, ...]


class LayerContext:
    """A cairo context drawing into segments; everything not overridden here goes to the current one.

    Segments end only at the top level (no save or group beyond ``mark_top``'s,
    identity matrix), so a drawing's state never has to cross into the next segment.
    """

    def __init__(self, width: int, height: int, scale: tuple[float, float], options: cairo.FontOptions) -> None:
        self._size = (width, height)
        self._scale = scale
        self._options = options
        self._sprites: list[Sprite] = []
        self._depth = self._top = 0
        self._begin()

    def _begin(self) -> None:
        self._recording = cairo.RecordingSurface(cairo.CONTENT_COLOR_ALPHA, None)
        self._recording.set_device_scale(*self._scale)
        self._ctx = cairo.Context(self._recording)
        self._ctx.set_font_options(self._options)
        # The saves still open at the top level, so the drawing's restores match on the new segment.
        for _ in range(self._top):
            self._ctx.save()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._ctx, name)

    @property
    def cairo_context(self) -> cairo.Context[Any]:
        """The current segment's context, for an API that takes a real one."""
        return self._ctx

    def save(self) -> None:
        self._depth += 1
        self._ctx.save()

    def restore(self) -> None:
        self._depth -= 1
        self._top = min(self._top, self._depth)
        self._ctx.restore()

    def push_group(self) -> None:
        self._depth += 1
        self._ctx.push_group()

    def push_group_with_content(self, content: cairo.Content) -> None:
        self._depth += 1
        self._ctx.push_group_with_content(content)

    def pop_group(self) -> cairo.Pattern:
        self._depth -= 1
        return self._ctx.pop_group()

    def pop_group_to_source(self) -> None:
        self._depth -= 1
        self._ctx.pop_group_to_source()

    def mark_top(self) -> None:
        """The current save depth is the top level, for a drawing that saves once around everything."""
        self._top = self._depth

    def _at_top(self) -> bool:
        return self._depth == self._top and self._ctx.get_matrix() == cairo.Matrix()

    def cut(self) -> None:
        """End the current segment here, when at the top level."""
        if self._at_top():
            self._flush()

    def keep(self, picture: cairo.ImageSurface, x: int, y: int) -> bool:
        """Put a kept picture in the list; False below the top level, where the caller pastes it."""
        if not self._at_top():
            return False
        self._flush()
        picture.flush()
        self._sprites.append(Sprite(picture, x, y, kept=True))
        return True

    def keep_fill(self, rgba: tuple[float, float, float, float]) -> bool:
        """Put the whole canvas in one colour in the list as a kept picture; False below the top level."""
        return self.keep(_filled(*self._size, self._scale, rgba), 0, 0)

    def finish(self) -> Layers:
        self._flush()
        return Layers(*self._size, self._scale, tuple(self._sprites))

    def _flush(self) -> None:
        recording = self._recording
        self._begin()
        ix, iy, iw, ih = recording.ink_extents()
        if iw == 0 or ih == 0:
            return
        w, h = self._size
        sx, sy = self._scale
        if iw < 0 or ih < 0:
            # Cairo's fixed point wraps an extent past 2**23 device pixels.
            x0, y0, x1, y1 = 0, 0, w, h
        else:
            # One pixel more: Quartz renders a glyph up to a pixel past the extents it measures.
            x0, y0 = max(0, math.floor(ix / sx) - 1), max(0, math.floor(iy / sy) - 1)
            x1, y1 = min(w, math.ceil((ix + iw) / sx) + 1), min(h, math.ceil((iy + ih) / sy) + 1)
            if x1 <= x0 or y1 <= y0:
                return
        picture = cairo.ImageSurface(cairo.FORMAT_ARGB32, math.ceil((x1 - x0) * sx), math.ceil((y1 - y0) * sy))
        picture.set_device_scale(sx, sy)
        ctx = cairo.Context(picture)
        ctx.set_source_surface(recording, -x0, -y0)
        ctx.paint()
        picture.flush()
        self._sprites.append(Sprite(picture, x0, y0, kept=False))


# One: a canvas-sized picture is megabytes, and a screen shows one fill at a time.
@functools.lru_cache(maxsize=1)
def _filled(
    width: int, height: int, scale: tuple[float, float], rgba: tuple[float, float, float, float]
) -> cairo.ImageSurface:
    picture = cairo.ImageSurface(cairo.FORMAT_ARGB32, math.ceil(width * scale[0]), math.ceil(height * scale[1]))
    picture.set_device_scale(*scale)
    ctx = cairo.Context(picture)
    ctx.set_source_rgba(*rgba)
    ctx.paint()
    return picture


def keep(cr: Any, picture: cairo.ImageSurface, x: int, y: int) -> bool:
    """On a layered canvas, put a kept picture in the list; False where the caller pastes it."""
    return isinstance(cr, LayerContext) and cr.keep(picture, x, y)


def keep_fill(cr: Any, rgba: tuple[float, float, float, float]) -> bool:
    """On a layered canvas, put the whole canvas in one colour in the list; False where the caller fills it."""
    return isinstance(cr, LayerContext) and cr.keep_fill(rgba)


def mark_top(cr: Any) -> None:
    """On a layered canvas, make the current save depth the top level."""
    if isinstance(cr, LayerContext):
        cr.mark_top()


def cut(cr: Any) -> None:
    """On a layered canvas, end the current segment."""
    if isinstance(cr, LayerContext):
        cr.cut()


def cairo_context(cr: Any) -> Any:
    """The real cairo context behind ``cr``, for an API that cannot take a layered one."""
    return cr.cairo_context if isinstance(cr, LayerContext) else cr
