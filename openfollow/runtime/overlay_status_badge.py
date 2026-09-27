# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Top-right HUD status badge for system warnings."""

from __future__ import annotations

from typing import Any

from openfollow.runtime.overlay_draw_style import (
    COLOR_INFO_BG,
    COLOR_INFO_BORDER,
    COLOR_INFO_FILL,
    COLOR_TEXT,
    COLOR_WARNING_BORDER,
    COLOR_WARNING_FILL,
    PANEL_RADIUS,
    draw_info_sign,
    draw_rounded_rect,
    draw_warning_sign,
)
from openfollow.runtime.overlay_state import OverlayState

# Max visible rows; longer stacks roll into "+N more" tail.
_MAX_VISIBLE_ROWS = 4

# Vertical offset below system-stats panel.
_TOP_OFFSET = 10 + 24 + 6

# The stack sizes to its widest row, clamped: narrow enough that a single short
# verdict ("Video: Unreachable") is not mostly empty box, wide enough that a row
# stays readable, and capped so a long one truncates instead of crossing the
# frame. Every row shares the width so the stack reads as one column.
_BADGE_MIN_WIDTH = 132.0
_BADGE_MAX_WIDTH = 280.0
_ROW_HEIGHT = 22.0
_ROW_SPACING = 4.0
_ICON_PAD = 8.0
_TEXT_PAD = 28.0  # icon column reserved on the left
# Font the rows are drawn in; measuring has to match or the fit is wrong.
_ROW_FONT_SIZE = 10.0


def _badge_width(renderer: Any, cr: Any, messages: list[str]) -> float:
    """Width that fits the widest message, clamped to the badge bounds."""
    renderer._set_ui_font(cr, _ROW_FONT_SIZE, bold=True)
    widest = max((cr.text_extents(m).width for m in messages), default=0.0)
    return min(_BADGE_MAX_WIDTH, max(_BADGE_MIN_WIDTH, widest + _TEXT_PAD + _ICON_PAD))


def draw_status_badge(
    renderer: Any,
    cr: Any,
    state: OverlayState,
    w: int,
    h: int,
) -> None:
    """Render top-right warning-row stack; empty flags short-circuit."""
    if not state.status_flags:
        return

    visible = state.status_flags[:_MAX_VISIBLE_ROWS]
    overflow = len(state.status_flags) - len(visible)

    messages = [message for _key, message, _severity in visible]
    if overflow > 0:
        messages.append(f"+{overflow} more")
    badge_w = _badge_width(renderer, cr, messages)
    badge_x = w - badge_w - 10.0
    cursor_y = float(_TOP_OFFSET)

    for _key, message, severity in visible:
        _draw_warning_row(
            renderer,
            cr,
            badge_x,
            cursor_y,
            badge_w,
            _ROW_HEIGHT,
            message,
            severity,
        )
        cursor_y += _ROW_HEIGHT + _ROW_SPACING

    if overflow > 0:
        # Tail row reads as an error if any hidden row is one, else info –
        # so a stack of pure-info rows doesn't sprout a stray red tail.
        hidden = state.status_flags[len(visible) :]
        tail_severity = "error" if any(s == "error" for _, _, s in hidden) else "info"
        _draw_warning_row(
            renderer,
            cr,
            badge_x,
            cursor_y,
            badge_w,
            _ROW_HEIGHT,
            f"+{overflow} more",
            tail_severity,
        )


def _draw_warning_row(
    renderer: Any,
    cr: Any,
    x: float,
    y: float,
    w: float,
    h: float,
    message: str,
    severity: str = "error",
) -> None:
    """One badge row: background, severity glyph, message text.

    ``"error"`` is the HUD's warning red with a warning sign, ``"info"`` the
    info blue with an "i" sign. The overflow row reuses it.
    """
    info = severity == "info"
    radius = PANEL_RADIUS
    draw_rounded_rect(cr, x, y, w, h, radius)
    if info:
        cr.set_source_rgba(*COLOR_INFO_FILL)
    else:
        cr.set_source_rgba(*COLOR_WARNING_FILL)
    cr.fill()
    draw_rounded_rect(cr, x, y, w, h, radius)
    cr.set_source_rgb(*(COLOR_INFO_BORDER if info else COLOR_WARNING_BORDER))
    cr.set_line_width(1.6)
    cr.stroke()

    glyph_cx = x + _ICON_PAD + 6.0
    glyph_cy = y + h * 0.5
    if info:
        draw_info_sign(cr, glyph_cx, glyph_cy, cut=COLOR_INFO_BG)
    else:
        draw_warning_sign(cr, glyph_cx, glyph_cy)

    # Message text – bold, truncated.
    renderer._set_ui_font(cr, _ROW_FONT_SIZE, bold=True)
    cr.set_source_rgb(*COLOR_TEXT)
    text_x = x + _TEXT_PAD
    text_max_w = w - _TEXT_PAD - _ICON_PAD
    truncated = renderer._truncate_text_to_width(cr, message, text_max_w)
    # Baseline matches system stats panel baseline.
    cr.move_to(text_x, y + h * 0.7)
    cr.show_text(truncated)
