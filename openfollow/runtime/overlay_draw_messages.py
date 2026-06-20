# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Cairo draw pass for operator / next-cue message cards.

Pure painter: consumes the ``OperatorMessageView`` list on :class:`OverlayState`
and draws a centered stack of cards (top or bottom, per config).

Marker-targeted cards (``marker_id >= 1``) get a solid title bar in the marker
colour with the marker name on it; broadcast cards have neither. Below that:
bold message, optional muted detail line, and a shrinking countdown bar for
timed messages (forever ones have no footer). A ``+N more`` line caps the stack.
"""

from __future__ import annotations

import math
from typing import Any

from openfollow.runtime.overlay_draw_hud import _wrap_error_message
from openfollow.runtime.overlay_draw_style import (
    COLOR_ACCENT,
    COLOR_BORDER,
    COLOR_TEXT,
    COLOR_TEXT_MUTED,
    draw_card_background,
    parse_hex,
)
from openfollow.runtime.overlay_state import OperatorMessageView, OverlayState

# Layout constants (px).
_MARGIN = 24.0  # gap from the top/bottom screen edge
_MAX_CARD_W = 460.0
_CARD_W_FRACTION = 0.6  # of surface width, clamped to _MAX_CARD_W
_PAD = 12.0
_RADIUS = 10.0
_GAP = 8.0  # between stacked cards / the overflow line

_MSG_SIZE = 11.0
_MSG_LINE_H = 16.0
_INFO_SIZE = 11.0
_INFO_LINE_H = 16.0
_TITLE_SIZE = 11.0
_TITLE_H = 22.0  # solid marker-colour title bar height
_TITLE_GAP = 6.0  # gap between the title bar and the message
_BAR_GAP = 6.0
_BAR_H = 3.0
_OVERFLOW_SIZE = 11.0
_OVERFLOW_H = 18.0


def _contrast_ink(rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    """Dark or white text for legibility on a coloured title bar, by luminance."""
    lum = 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]
    return (0.08, 0.08, 0.08) if lum > 0.6 else (1.0, 1.0, 1.0)


def draw_operator_messages(renderer: Any, cr: Any, state: OverlayState, w: int, h: int) -> None:
    """Draw the operator-message stack onto ``cr``."""
    views = state.operator_messages
    overflow = state.operator_message_overflow
    if not views and overflow <= 0:
        return

    # Uniform scale for the card window + text. The screen-overrun guard
    # (_CARD_W_FRACTION) and the edge gap (_MARGIN) stay unscaled.
    scale = state.operator_message_scale
    pad = _PAD * scale
    gap = _GAP * scale

    card_w = min(_MAX_CARD_W * scale, w * _CARD_W_FRACTION)
    text_w = card_w - 2 * pad
    if text_w <= 0:
        # Degenerate (zero/tiny) surface: skip layout.
        return
    x = (w - card_w) / 2.0

    # Measure every card to anchor the stack with a known total height.
    cards: list[tuple[OperatorMessageView, list[str], list[str], float]] = []
    for v in views:
        # Wrap in the render weight (message bold, info regular).
        msg_lines = _wrap_error_message(renderer, cr, v.message, text_w, _MSG_SIZE * scale, bold=True)
        info_lines = (
            _wrap_error_message(renderer, cr, v.info, text_w, _INFO_SIZE * scale, bold=False) if v.info.strip() else []
        )
        cards.append((v, msg_lines, info_lines, _card_height(v, msg_lines, info_lines, scale)))

    element_heights = [c[3] for c in cards]
    if overflow > 0:
        element_heights.append(_OVERFLOW_H * scale)
    total_h = sum(element_heights) + gap * (len(element_heights) - 1)

    if state.operator_message_position == "top":
        cur_y = _MARGIN
    else:  # bottom: anchor above the bottom edge
        cur_y = max(_MARGIN, h - _MARGIN - total_h)

    for v, msg_lines, info_lines, card_h in cards:
        _draw_card(renderer, cr, v, msg_lines, info_lines, x, cur_y, card_w, card_h, scale)
        cur_y += card_h + gap

    if overflow > 0:
        _draw_overflow(renderer, cr, overflow, x, cur_y, card_w, scale)


def _card_height(v: OperatorMessageView, msg_lines: list[str], info_lines: list[str], scale: float) -> float:
    """Total card height for the measured content; mirrors the cursor advances in :func:`_draw_card`.

    All terms scale uniformly with ``scale`` – kept as a single trailing
    multiply so it stays in lockstep with the scaled cursor advances.
    """
    # Marker cards: a flush title bar replaces the top pad + chip.
    total = _PAD + (_TITLE_H + _TITLE_GAP if v.marker_id >= 1 else _PAD)
    total += len(msg_lines) * _MSG_LINE_H
    total += len(info_lines) * _INFO_LINE_H
    if not v.is_forever:
        total += _BAR_GAP + _BAR_H  # timed cards add a countdown bar
    return total * scale


def _draw_card(
    renderer: Any,
    cr: Any,
    v: OperatorMessageView,
    msg_lines: list[str],
    info_lines: list[str],
    x: float,
    y: float,
    card_w: float,
    card_h: float,
    scale: float,
) -> None:
    pad = _PAD * scale
    radius = _RADIUS * scale
    title_h = _TITLE_H * scale
    title_gap = _TITLE_GAP * scale
    msg_size = _MSG_SIZE * scale
    msg_line_h = _MSG_LINE_H * scale
    info_size = _INFO_SIZE * scale
    info_line_h = _INFO_LINE_H * scale
    bar_gap = _BAR_GAP * scale
    bar_h = _BAR_H * scale

    # Background fill + soft border (shared overlay-card chrome).
    draw_card_background(cr, x, y, card_w, card_h, radius)

    inner_x = x + pad
    accent = parse_hex(v.marker_color) if v.marker_id >= 1 else COLOR_ACCENT

    # Marker-targeted cards: solid title bar in the marker colour (rounded top
    # to match the card, square bottom) with the marker name on it. Broadcast
    # cards have no bar – the message starts straight under the top padding.
    if v.marker_id >= 1:
        cr.move_to(x, y + title_h)
        cr.line_to(x, y + radius)
        cr.arc(x + radius, y + radius, radius, math.pi, 1.5 * math.pi)
        cr.line_to(x + card_w - radius, y)
        cr.arc(x + card_w - radius, y + radius, radius, 1.5 * math.pi, 2.0 * math.pi)
        cr.line_to(x + card_w, y + title_h)
        cr.close_path()
        cr.set_source_rgb(*accent)
        cr.fill()
        label = v.marker_name or f"M{v.marker_id}"
        renderer._set_ui_font(cr, _TITLE_SIZE * scale, bold=True)
        cr.set_source_rgb(*_contrast_ink(accent))
        label = _truncate(cr, label, card_w - 2 * pad)
        # Center the actual glyph box in the bar so caps clear the top edge.
        te = cr.text_extents(label)
        cr.move_to(inner_x, y + (title_h - te.height) / 2.0 - te.y_bearing)
        cr.show_text(label)
        cur = y + title_h + title_gap
    else:
        cur = y + pad

    # Message (bold).
    renderer._set_ui_font(cr, msg_size, bold=True)
    cr.set_source_rgb(*COLOR_TEXT)
    for line in msg_lines:
        cr.move_to(inner_x, cur + msg_size)
        cr.show_text(line)
        cur += msg_line_h

    # Detail / info (muted).
    if info_lines:
        renderer._set_ui_font(cr, info_size, bold=False)
        cr.set_source_rgba(*COLOR_TEXT_MUTED)
        for line in info_lines:
            cr.move_to(inner_x, cur + info_size)
            cr.show_text(line)
            cur += info_line_h

    # Footer: shrinking countdown bar for timed cards (forever cards have none).
    if not v.is_forever:
        bar_top = cur + bar_gap
        track_w = card_w - 2 * pad
        # Track.
        cr.set_source_rgba(*COLOR_BORDER)
        cr.rectangle(inner_x, bar_top, track_w, bar_h)
        cr.fill()
        # Remaining portion in the accent / marker color.
        fill_w = track_w * max(0.0, min(1.0, v.remaining_fraction))
        if fill_w > 0:
            cr.set_source_rgb(*accent)
            cr.rectangle(inner_x, bar_top, fill_w, bar_h)
            cr.fill()


def _draw_overflow(renderer: Any, cr: Any, overflow: int, x: float, y: float, card_w: float, scale: float) -> None:
    """Draw the ``+N more`` hint below the stack (centered)."""
    overflow_size = _OVERFLOW_SIZE * scale
    renderer._set_ui_font(cr, overflow_size, bold=False)
    cr.set_source_rgba(*COLOR_TEXT_MUTED)
    text = f"+{overflow} more"
    width = cr.text_extents(text).width
    cr.move_to(x + (card_w - width) / 2.0, y + overflow_size)
    cr.show_text(text)


def _truncate(cr: Any, text: str, max_width: float) -> str:
    """Ellipsize ``text`` to fit ``max_width``."""
    if max_width <= 0 or cr.text_extents(text).width <= max_width:
        return text
    ellipsis = "…"
    end = len(text)
    while end > 0:
        candidate = text[:end].rstrip() + ellipsis
        if cr.text_extents(candidate).width <= max_width:
            return candidate
        end -= 1
    return ellipsis
