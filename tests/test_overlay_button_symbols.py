# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Michel Honold and Paul Hermann
"""Tests for :mod:`openfollow.runtime.overlay_button_symbols`: HUD text with
pad button symbols in it is measured and drawn with each symbol one em wide,
on the baseline, in the colour the caller set."""

from __future__ import annotations

import math

import pytest

from openfollow.button_names import SYMBOL_CHARS, SYMBOL_STROKE
from openfollow.runtime import overlay_button_symbols as symbols
from tests._fake_cairo import FakeCairo

pytestmark = pytest.mark.unit

_CROSS = SYMBOL_CHARS["cross"]


def test_a_symbol_takes_one_em_in_the_width() -> None:
    cr = FakeCairo()
    # FakeCairo sizes text at 0.6 em per character at its current font size.
    cr.set_font_size(10.0)
    assert symbols.text_advance(cr, f"{_CROSS}: Confirm", 10.0) == pytest.approx(10.0 + len(": Confirm") * 6.0)
    assert symbols.text_advance(cr, "X: Confirm", 10.0) == pytest.approx(len("X: Confirm") * 6.0)


def test_text_around_a_symbol_is_drawn_after_it() -> None:
    cr = FakeCairo()
    cr.set_font_size(10.0)
    end = symbols.show_text(cr, f"H / {_CROSS}: help", 100.0, 50.0, 10.0)
    first, second = cr.texts
    assert (first.text, first.x, first.y) == ("H / ", 100.0, 50.0)
    assert (second.text, second.x, second.y) == (": help", 100.0 + 4 * 6.0 + 10.0, 50.0)
    assert end == pytest.approx(second.x + len(": help") * 6.0)


def test_text_without_symbols_is_one_run() -> None:
    cr = FakeCairo()
    symbols.show_text(cr, "Back: Settings menu", 0.0, 0.0, 10.0)
    assert cr.show_text_strings() == ["Back: Settings menu"]
    assert cr.strokes == 0


def test_a_symbol_is_stroked_in_the_callers_colour_at_a_text_weight() -> None:
    cr = FakeCairo()
    cr.set_source_rgb(0.9, 0.9, 0.9)
    symbols.draw_symbol(cr, "square", 0.0, 13.0, 16.0)
    assert cr.strokes == 1
    assert ("line_width", SYMBOL_STROKE) in cr.calls
    assert not any(call[0] in ("rgb", "rgba") for call in cr.calls[1:])
    assert (cr.saves, cr.restores) == (1, 1)


def test_a_symbol_sits_on_the_baseline_in_its_em_square() -> None:
    cr = FakeCairo()
    # At 16 px one unit is one pixel; the square's corners are 3.5 and 12.5 units in.
    symbols.draw_symbol(cr, "square", 100.0, 213.0, 16.0)
    assert cr.move_tos == [(103.5, 203.5)]
    assert cr.line_tos == [(112.5, 203.5), (112.5, 212.5), (103.5, 212.5)]
    assert cr.closes == 1


def test_a_circle_symbol_is_a_full_circle() -> None:
    cr = FakeCairo()
    symbols.draw_symbol(cr, "circle", 0.0, 26.0, 32.0)
    [arc] = [c for c in cr.calls if c[0] == "arc"]
    # Baseline 26 at 2 px a unit: the square starts 26 px up, the centre 8 units below that.
    assert arc[1:] == (16.0, 16.0, 9.0, 0.0, math.tau)


@pytest.mark.parametrize("symbol", sorted(SYMBOL_CHARS))
def test_every_symbol_draws_one_stroke(symbol: str) -> None:
    cr = FakeCairo()
    symbols.draw_symbol(cr, symbol, 0.0, 13.0, 16.0)
    assert cr.strokes == 1
