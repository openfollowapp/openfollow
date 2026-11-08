# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Michel Honold and Paul Hermann
"""Pad button symbols painted inline in HUD text.

``button_names.hud_label`` puts a private-use character where a symbol goes
(Cross, View, ...); these helpers measure and draw such text, painting each
character as its vector symbol in the current source colour, one em wide and
sitting on the baseline like a capital.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from typing import Any

import cairo

from openfollow.button_names import SYMBOL_FOR_CHAR, SYMBOL_SHAPES, SYMBOL_STROKE

# The 16-unit square's row that sits on the baseline.
_BASELINE_UNITS = 13.0


def _runs(text: str) -> Iterator[tuple[str | None, str]]:
    """``(symbol, "")`` for each stand-in character, ``(None, run)`` for the text between."""
    run = ""
    for char in text:
        symbol = SYMBOL_FOR_CHAR.get(char)
        if symbol is None:
            run += char
            continue
        if run:
            yield None, run
            run = ""
        yield symbol, ""
    if run:
        yield None, run


def text_advance(cr: Any, text: str, size: float) -> float:
    """Width ``text`` takes at font ``size``, each symbol one em."""
    return sum(size if symbol else cr.text_extents(run).x_advance for symbol, run in _runs(text))


def show_text(cr: Any, text: str, x: float, y: float, size: float) -> float:
    """Draw ``text`` with its baseline at ``(x, y)`` and return where it ends."""
    for symbol, run in _runs(text):
        if symbol is None:
            cr.move_to(x, y)
            cr.show_text(run)
            x += cr.text_extents(run).x_advance
        else:
            draw_symbol(cr, symbol, x, y, size)
            x += size
    return x


def draw_symbol(cr: Any, symbol: str, x: float, baseline: float, size: float) -> None:
    """Stroke ``symbol`` in the current source over the em square at ``x`` on ``baseline``."""
    shape = SYMBOL_SHAPES[symbol]
    unit = size / 16.0
    top = baseline - _BASELINE_UNITS * unit

    def at(point: tuple[float, float]) -> tuple[float, float]:
        return x + point[0] * unit, top + point[1] * unit

    cr.save()
    cr.set_line_width(SYMBOL_STROKE * unit)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    for points, closed in (*((p, False) for p in shape.lines), *((p, True) for p in shape.closed)):
        cr.move_to(*at(points[0]))
        for point in points[1:]:
            cr.line_to(*at(point))
        if closed:
            cr.close_path()
    for cx, cy, radius in shape.circles:
        cr.new_sub_path()
        cr.arc(*at((cx, cy)), radius * unit, 0.0, math.tau)
    cr.stroke()
    cr.restore()
