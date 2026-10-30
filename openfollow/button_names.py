# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Michel Honold and Paul Hermann
"""Gamepad buttons named the way the station's chosen pad family prints them.

Stored bindings keep SDL's names (``BACK``, ``START``, ...); this module only
decides what an operator reads, from ``ControllerConfig.button_name_style``.
The Nintendo names assume SDL reports a Nintendo pad's face buttons by their
printed letters, as its default does. Stdlib-only: the HUD imports it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ButtonNameStyle(StrEnum):
    """Which pad family's printing the station names buttons after."""

    STANDARD = "standard"
    XBOX = "xbox"
    PLAYSTATION = "playstation"
    NINTENDO = "nintendo"


BUTTON_NAME_STYLES: tuple[str, ...] = tuple(ButtonNameStyle)

STYLE_TITLES: dict[ButtonNameStyle, str] = {
    ButtonNameStyle.STANDARD: "Standard",
    ButtonNameStyle.XBOX: "Xbox",
    ButtonNameStyle.PLAYSTATION: "PlayStation",
    ButtonNameStyle.NINTENDO: "Nintendo",
}

# Stored value -> SDL name (short, long). Order is the help table's row order.
SDL_NAMES: dict[str, tuple[str, str]] = {
    "A": ("A", "A"),
    "B": ("B", "B"),
    "X": ("X", "X"),
    "Y": ("Y", "Y"),
    "LB": ("LB", "Left Bumper"),
    "RB": ("RB", "Right Bumper"),
    "LT": ("LT", "Left Trigger"),
    "RT": ("RT", "Right Trigger"),
    "BACK": ("Back", "Back"),
    "START": ("Start", "Start"),
    "DPAD_UP": ("D-Up", "D-Pad Up"),
    "DPAD_DOWN": ("D-Down", "D-Pad Down"),
    "DPAD_LEFT": ("D-Left", "D-Pad Left"),
    "DPAD_RIGHT": ("D-Right", "D-Pad Right"),
}

# What each family prints; a button left out (the D-Pad) keeps its SDL name.
PRINTED_NAMES: dict[ButtonNameStyle, dict[str, str]] = {
    ButtonNameStyle.XBOX: {
        "A": "A",
        "B": "B",
        "X": "X",
        "Y": "Y",
        "LB": "LB",
        "RB": "RB",
        "LT": "LT",
        "RT": "RT",
        "BACK": "View",
        "START": "Menu",
    },
    ButtonNameStyle.PLAYSTATION: {
        "A": "Cross",
        "B": "Circle",
        "X": "Square",
        "Y": "Triangle",
        "LB": "L1",
        "RB": "R1",
        "LT": "L2",
        "RT": "R2",
        "BACK": "Create",
        "START": "Options",
    },
    ButtonNameStyle.NINTENDO: {
        "A": "A",
        "B": "B",
        "X": "X",
        "Y": "Y",
        "LB": "L",
        "RB": "R",
        "LT": "ZL",
        "RT": "ZR",
        "BACK": "-",
        "START": "+",
    },
}

# Buttons a family marks with a symbol rather than a word; the word is its text name.
SYMBOLS: dict[ButtonNameStyle, dict[str, str]] = {
    ButtonNameStyle.XBOX: {"BACK": "view", "START": "menu"},
    ButtonNameStyle.PLAYSTATION: {"A": "cross", "B": "circle", "X": "square", "Y": "triangle"},
}

# Private-use characters that stand for a drawn symbol inside HUD text.
SYMBOL_CHARS: dict[str, str] = {
    "cross": "\ue000",
    "circle": "\ue001",
    "square": "\ue002",
    "triangle": "\ue003",
    "view": "\ue004",
    "menu": "\ue005",
}
SYMBOL_FOR_CHAR: dict[str, str] = {char: symbol for symbol, char in SYMBOL_CHARS.items()}


def name_style(value: object) -> ButtonNameStyle:
    """The style ``value`` names; ``STANDARD`` for anything else."""
    return ButtonNameStyle(value) if value in BUTTON_NAME_STYLES else ButtonNameStyle.STANDARD


# Marks spelled out where a lone "-" would pass for the web's unbound "–" option.
_SPELLED: dict[str, str] = {"-": "Minus", "+": "Plus"}


def button_label(button: str, style: ButtonNameStyle, *, short: bool = True, spelled: bool = False) -> str:
    """The button's name in words; an unrecognised stored value as it is.

    ``spelled`` writes Nintendo's ``-`` / ``+`` as Minus / Plus.
    """
    names = SDL_NAMES.get(button)
    if names is None:
        return button
    printed = PRINTED_NAMES.get(style, {}).get(button)
    if printed is not None:
        return _SPELLED.get(printed, printed) if spelled else printed
    return names[0] if short else names[1]


def button_symbol(button: str, style: ButtonNameStyle) -> str | None:
    """The symbol ``style`` draws for ``button`` (``"cross"``, ``"view"``, ...), if any."""
    return SYMBOLS.get(style, {}).get(button)


def hud_label(button: str, style: ButtonNameStyle, *, short: bool = True) -> str:
    """The HUD's name: a symbol's stand-in character, else the word."""
    symbol = button_symbol(button, style)
    return SYMBOL_CHARS[symbol] if symbol is not None else button_label(button, style, short=short)


@dataclass(frozen=True)
class SymbolShape:
    """A symbol's strokes on a 16-unit em square, y down."""

    lines: tuple[tuple[tuple[float, float], ...], ...] = ()
    closed: tuple[tuple[tuple[float, float], ...], ...] = ()
    circles: tuple[tuple[float, float, float], ...] = ()


# One drawing for the HUD (Cairo) and the web (SVG), so the two look alike.
SYMBOL_SHAPES: dict[str, SymbolShape] = {
    "cross": SymbolShape(lines=(((4.5, 4.5), (11.5, 11.5)), ((11.5, 4.5), (4.5, 11.5)))),
    "circle": SymbolShape(circles=((8.0, 8.0, 4.5),)),
    "square": SymbolShape(closed=(((3.5, 3.5), (12.5, 3.5), (12.5, 12.5), (3.5, 12.5)),)),
    "triangle": SymbolShape(closed=(((8.0, 3.0), (13.0, 12.0), (3.0, 12.0)),)),
    # View: a front window over a back one, whose hidden corner is left out.
    "view": SymbolShape(
        lines=(((6.0, 9.5), (2.5, 9.5), (2.5, 3.5), (10.0, 3.5), (10.0, 6.5)),),
        closed=(((6.0, 6.5), (13.5, 6.5), (13.5, 12.5), (6.0, 12.5)),),
    ),
    "menu": SymbolShape(lines=(((3.0, 4.5), (13.0, 4.5)), ((3.0, 8.0), (13.0, 8.0)), ((3.0, 11.5), (13.0, 11.5)))),
}
# Stroke weight in the same units, close to the UI font's regular stem.
SYMBOL_STROKE = 1.5
