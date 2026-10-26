# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Gamepad button names for the web UI: the word, or its symbol as inline SVG.

The SVG is hidden from assistive tech and followed by the word in a
visually hidden span, so a screen reader reads "Cross", not a picture.
A ``<select>`` option can't hold markup, so options use the word.
"""

from __future__ import annotations

import html

from openfollow.button_names import (
    SYMBOL_SHAPES,
    SYMBOL_STROKE,
    ButtonNameStyle,
    button_label,
    button_symbol,
)


def _num(value: float) -> str:
    return f"{value:g}"


def symbol_svg(symbol: str) -> str:
    """``symbol`` as an inline SVG, one em square in the text colour."""
    shape = SYMBOL_SHAPES[symbol]
    paths = [
        "M" + "L".join(f"{_num(x)} {_num(y)}" for x, y in points) + ("Z" if closed else "")
        for points, closed in (*((p, False) for p in shape.lines), *((p, True) for p in shape.closed))
    ]
    body = "".join(f'<path d="{d}"/>' for d in paths) + "".join(
        f'<circle cx="{_num(cx)}" cy="{_num(cy)}" r="{_num(r)}"/>' for cx, cy, r in shape.circles
    )
    return (
        f'<svg class="btn-symbol-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor"'
        f' stroke-width="{_num(SYMBOL_STROKE)}" stroke-linecap="round" stroke-linejoin="round"'
        f' aria-hidden="true" focusable="false">{body}</svg>'
    )


def symbol_html(symbol: str, word: str) -> str:
    """The symbol with ``word`` beside it for screen readers only (none when the word is shown anyway)."""
    hidden = f'<span class="visually-hidden">{html.escape(word)}</span>' if word else ""
    return f'<span class="btn-symbol">{symbol_svg(symbol)}{hidden}</span>'


def button_html(button: str, style: ButtonNameStyle, *, short: bool = False) -> str:
    """The button's name as safe HTML: its symbol where ``style`` has one, else the escaped word."""
    word = button_label(button, style, short=short, spelled=True)
    symbol = button_symbol(button, style)
    return html.escape(word) if symbol is None else symbol_html(symbol, word)
