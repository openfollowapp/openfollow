# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for the operator-message Cairo draw pass.

Uses the recording ``FakeCairo`` + ``FakeRenderer`` to assert against observable
draw calls (text strings, rectangles, chip arcs) without real rendering.
"""

from __future__ import annotations

import pytest

from openfollow.runtime.overlay_draw_messages import (
    _truncate,
    draw_operator_messages,
)
from openfollow.runtime.overlay_state import OperatorMessageView, OverlayState
from tests._fake_cairo import FakeCairo, FakeRenderer

pytestmark = pytest.mark.unit


def _view(
    *,
    message: str = "Next cue",
    info: str = "",
    marker_id: int = 0,
    marker_name: str = "",
    marker_color: str = "#ee5a24",
    is_forever: bool = False,
    remaining_fraction: float = 0.5,
) -> OperatorMessageView:
    return OperatorMessageView(
        message=message,
        info=info,
        marker_id=marker_id,
        marker_name=marker_name,
        marker_color=marker_color,
        is_forever=is_forever,
        remaining_fraction=remaining_fraction,
    )


def _draw(
    views: list[OperatorMessageView],
    *,
    overflow: int = 0,
    position: str = "bottom",
    scale: float = 1.0,
    w: int = 1280,
    h: int = 720,
) -> FakeCairo:
    state = OverlayState()
    state.operator_messages = list(views)
    state.operator_message_overflow = overflow
    state.operator_message_position = position
    state.operator_message_scale = scale
    renderer = FakeRenderer(state=state)
    cr = FakeCairo()
    draw_operator_messages(renderer, cr, state, w, h)
    return cr


def test_empty_is_noop() -> None:
    cr = _draw([])
    assert cr.texts == []
    assert cr.fills == 0


def test_timed_broadcast_card_renders_message_and_bar() -> None:
    cr = _draw([_view(message="Next: SQ12", remaining_fraction=0.5)])
    assert cr.find_texts("Next: SQ12")
    # Countdown bar = track rectangle + remaining-fill rectangle.
    assert len(cr.rects) == 2


def test_timed_card_zero_remaining_skips_fill_rect() -> None:
    cr = _draw([_view(remaining_fraction=0.0)])
    # Only the track rectangle – no fill rectangle when nothing remains.
    assert len(cr.rects) == 1


def test_keyed_forever_card_shows_title_no_bar() -> None:
    cr = _draw([_view(message="Hold", marker_id=3, marker_name="Spot 3", is_forever=True)])
    assert cr.find_texts("Spot 3")  # marker title-bar name
    assert cr.find_texts("Hold")
    assert cr.rects == []  # forever → no countdown bar, no hint


def test_keyed_card_without_name_falls_back_to_m_id() -> None:
    cr = _draw([_view(marker_id=7, marker_name="", is_forever=True)])
    assert cr.find_texts("M7")


def test_marker_title_bar_dark_ink_on_light_colour() -> None:
    # A light marker colour gets dark title text (luminance path) for legibility.
    cr = _draw([_view(marker_id=5, marker_name="Spot 5", marker_color="#ffe600", is_forever=True)])
    title = cr.find_texts("Spot 5")
    assert title and title[0].rgba == (0.08, 0.08, 0.08, 1.0)


def test_marker_title_bar_white_ink_on_dark_colour() -> None:
    # A dark marker colour gets white title text (the other luminance branch).
    cr = _draw([_view(marker_id=6, marker_name="Spot 6", marker_color="#101010", is_forever=True)])
    title = cr.find_texts("Spot 6")
    assert title and title[0].rgba == (1.0, 1.0, 1.0, 1.0)


def test_info_line_rendered() -> None:
    cr = _draw([_view(message="Go", info="stand by please")])
    assert cr.find_texts("stand by please")


def test_wrap_error_message_honors_bold_weight() -> None:
    # Wrap weight must match the requested ``bold``.
    import cairo

    from openfollow.runtime.overlay_draw_hud import _wrap_error_message

    renderer = FakeRenderer()
    cr_regular = FakeCairo()
    _wrap_error_message(renderer, cr_regular, "some words to wrap", 1000, 12.0, bold=False)
    regular = [c[2] for c in cr_regular.calls if c[0] == "set_font_face"]
    assert regular and regular[-1] == cairo.FONT_WEIGHT_NORMAL

    cr_bold = FakeCairo()
    _wrap_error_message(renderer, cr_bold, "some words to wrap", 1000, 12.0, bold=True)
    bold = [c[2] for c in cr_bold.calls if c[0] == "set_font_face"]
    assert bold and bold[-1] == cairo.FONT_WEIGHT_BOLD


def test_overflow_hint_rendered() -> None:
    cr = _draw([_view()], overflow=3)
    assert cr.find_texts("+3 more")


def test_top_vs_bottom_placement_changes_y() -> None:
    top = _draw([_view()], position="top")
    bottom = _draw([_view()], position="bottom")
    top_y = min(t.y for t in top.texts)
    bottom_y = min(t.y for t in bottom.texts)
    assert top_y < 360 < bottom_y  # top hugs the top edge; bottom the bottom


def test_degenerate_width_is_noop() -> None:
    cr = _draw([_view()], w=0)
    assert cr.texts == []


def test_long_chip_name_is_truncated() -> None:
    cr = _draw([_view(marker_id=2, marker_name="X" * 200, is_forever=True)])
    chip_texts = [t.text for t in cr.texts if "…" in t.text]
    assert chip_texts, "expected the long chip name to be ellipsized"


def test_multiple_cards_stack() -> None:
    cr = _draw([_view(message="first"), _view(message="second")])
    assert cr.find_texts("first") and cr.find_texts("second")


# --------------------------------------------------------------------------- #
# Scale – uniform window + text scaling
# --------------------------------------------------------------------------- #
# A wide surface (w=4000) keeps the 60%-of-width cap from clamping the 2× card,
# so the card window genuinely doubles instead of saturating the screen guard.


def test_scale_doubles_message_text_and_window() -> None:
    view = [_view(message="Go", remaining_fraction=0.5)]
    base = _draw(view, w=4000)
    big = _draw(view, w=4000, scale=2.0)

    # Message font size doubles.
    base_msg = base.find_texts("Go")[0]
    big_msg = big.find_texts("Go")[0]
    assert big_msg.font_size == pytest.approx(base_msg.font_size * 2.0)

    # Countdown-bar track (first recorded rect) doubles in width and height –
    # the window scales with the text, not just the glyphs.
    base_track = base.rects[0]
    big_track = big.rects[0]
    assert big_track[2] == pytest.approx(base_track[2] * 2.0)  # width
    assert big_track[3] == pytest.approx(base_track[3] * 2.0)  # height


def test_scale_doubles_marker_title_text() -> None:
    view = [_view(marker_id=3, marker_name="Spot 3", is_forever=True)]
    base = _draw(view, w=4000)
    big = _draw(view, w=4000, scale=2.0)
    assert big.find_texts("Spot 3")[0].font_size == pytest.approx(base.find_texts("Spot 3")[0].font_size * 2.0)


def test_scale_doubles_overflow_hint_text() -> None:
    base = _draw([_view()], overflow=3, w=4000)
    big = _draw([_view()], overflow=3, w=4000, scale=2.0)
    assert big.find_texts("+3 more")[0].font_size == pytest.approx(base.find_texts("+3 more")[0].font_size * 2.0)


# --------------------------------------------------------------------------- #
# _truncate helper
# --------------------------------------------------------------------------- #


def test_truncate_returns_text_when_it_fits() -> None:
    cr = FakeCairo()
    assert _truncate(cr, "short", 10_000) == "short"


def test_truncate_nonpositive_width_returns_text() -> None:
    cr = FakeCairo()
    assert _truncate(cr, "anything", 0) == "anything"


def test_truncate_ellipsizes_long_text() -> None:
    cr = FakeCairo()
    out = _truncate(cr, "a-very-long-marker-name-indeed", 40)
    assert out.endswith("…") and len(out) < len("a-very-long-marker-name-indeed")


def test_truncate_returns_bare_ellipsis_when_nothing_fits() -> None:
    cr = FakeCairo()
    # Width smaller than even a single char + ellipsis → bare ellipsis.
    assert _truncate(cr, "abc", 0.1) == "…"
