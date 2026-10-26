# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for :mod:`openfollow.button_names`: every button's name in each pad
family the station can be set to, which buttons a family draws as a symbol,
and the HUD's stand-in characters for those symbols."""

from __future__ import annotations

import pytest

from openfollow.button_names import (
    BUTTON_NAME_STYLES,
    SYMBOL_CHARS,
    SYMBOL_SHAPES,
    ButtonNameStyle,
    button_label,
    button_symbol,
    hud_label,
    name_style,
)
from openfollow.configuration import VALID_BUTTON_NAMES

pytestmark = pytest.mark.unit

_BUTTONS = ("A", "B", "X", "Y", "LB", "RB", "LT", "RT", "BACK", "START")

# What each family names a button, in _BUTTONS order (short forms).
_NAMES = {
    ButtonNameStyle.STANDARD: ("A", "B", "X", "Y", "LB", "RB", "LT", "RT", "Back", "Start"),
    ButtonNameStyle.XBOX: ("A", "B", "X", "Y", "LB", "RB", "LT", "RT", "View", "Menu"),
    ButtonNameStyle.PLAYSTATION: ("Cross", "Circle", "Square", "Triangle", "L1", "R1", "L2", "R2", "Create", "Options"),
    ButtonNameStyle.NINTENDO: ("A", "B", "X", "Y", "L", "R", "ZL", "ZR", "-", "+"),
}

_SYMBOLS = {
    ButtonNameStyle.XBOX: {"BACK": "view", "START": "menu"},
    ButtonNameStyle.PLAYSTATION: {"A": "cross", "B": "circle", "X": "square", "Y": "triangle"},
}


@pytest.mark.parametrize(
    ("style", "button", "name"),
    [(style, button, name) for style, names in _NAMES.items() for button, name in zip(_BUTTONS, names, strict=True)],
)
def test_each_family_names_every_button(style: ButtonNameStyle, button: str, name: str) -> None:
    assert button_label(button, style) == name


@pytest.mark.parametrize(
    ("button", "name"),
    [("LB", "Left Bumper"), ("RB", "Right Bumper"), ("LT", "Left Trigger"), ("RT", "Right Trigger"), ("BACK", "Back")],
)
def test_standard_spells_out_the_bumpers_and_triggers_in_long_form(button: str, name: str) -> None:
    assert button_label(button, ButtonNameStyle.STANDARD, short=False) == name


@pytest.mark.parametrize("style", list(ButtonNameStyle))
@pytest.mark.parametrize(
    ("button", "short", "long"),
    [
        ("DPAD_UP", "D-Up", "D-Pad Up"),
        ("DPAD_DOWN", "D-Down", "D-Pad Down"),
        ("DPAD_LEFT", "D-Left", "D-Pad Left"),
        ("DPAD_RIGHT", "D-Right", "D-Pad Right"),
    ],
)
def test_the_dpad_is_named_alike_in_every_family(style: ButtonNameStyle, button: str, short: str, long: str) -> None:
    assert (button_label(button, style), button_label(button, style, short=False)) == (short, long)


@pytest.mark.parametrize("style", list(ButtonNameStyle))
@pytest.mark.parametrize("button", sorted(VALID_BUTTON_NAMES - {""}))
def test_only_playstation_faces_and_xbox_view_menu_are_symbols(style: ButtonNameStyle, button: str) -> None:
    assert button_symbol(button, style) == _SYMBOLS.get(style, {}).get(button)


@pytest.mark.parametrize("style", list(ButtonNameStyle))
@pytest.mark.parametrize("button", sorted(VALID_BUTTON_NAMES - {""}))
def test_the_hud_gets_a_stand_in_for_a_symbol_and_the_word_otherwise(style: ButtonNameStyle, button: str) -> None:
    symbol = _SYMBOLS.get(style, {}).get(button)
    expected = SYMBOL_CHARS[symbol] if symbol else button_label(button, style)
    assert hud_label(button, style) == expected


def test_every_symbol_has_a_shape_and_a_private_use_stand_in() -> None:
    assert set(SYMBOL_CHARS) == set(SYMBOL_SHAPES) == {"cross", "circle", "square", "triangle", "view", "menu"}
    assert all(len(char) == 1 and 0xE000 <= ord(char) <= 0xF8FF for char in SYMBOL_CHARS.values())


@pytest.mark.parametrize("style", list(ButtonNameStyle))
def test_an_unrecognised_stored_value_is_shown_as_it_is(style: ButtonNameStyle) -> None:
    assert button_label("CUSTOM", style) == "CUSTOM"
    assert hud_label("CUSTOM", style) == "CUSTOM"


@pytest.mark.parametrize("value", ["standard", "xbox", "playstation", "nintendo"])
def test_a_configured_style_names_its_family(value: str) -> None:
    assert name_style(value) is ButtonNameStyle(value)


@pytest.mark.parametrize("value", [None, "", "ps5", "Xbox", 3, True])
def test_anything_else_names_buttons_the_standard_way(value: object) -> None:
    assert name_style(value) is ButtonNameStyle.STANDARD


def test_the_config_choices_are_the_four_families() -> None:
    assert BUTTON_NAME_STYLES == ("standard", "xbox", "playstation", "nintendo")


@pytest.mark.parametrize(("button", "word"), [("BACK", "Minus"), ("START", "Plus")])
def test_nintendos_marks_are_spelled_where_a_dash_would_mean_unbound(button: str, word: str) -> None:
    assert button_label(button, ButtonNameStyle.NINTENDO, spelled=True) == word
    assert button_label(button, ButtonNameStyle.NINTENDO) in ("-", "+")


@pytest.mark.parametrize("style", [s for s in ButtonNameStyle if s is not ButtonNameStyle.NINTENDO])
@pytest.mark.parametrize("button", sorted(VALID_BUTTON_NAMES - {""}))
def test_spelling_out_changes_no_other_name(style: ButtonNameStyle, button: str) -> None:
    assert button_label(button, style, spelled=True) == button_label(button, style)
    assert button_label(button, style, short=False, spelled=True) == button_label(button, style, short=False)
