# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Top-right HUD status badge for system warnings."""

from __future__ import annotations

from typing import Any

from openfollow.runtime.overlay_draw_style import (
    COLOR_TEXT,
    PANEL_RADIUS,
    STATUS_LEVELS,
    draw_level_box,
    draw_level_sign,
    status_level,
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
        _draw_status_row(renderer, cr, badge_x, cursor_y, badge_w, _ROW_HEIGHT, message, status_level(severity))
        cursor_y += _ROW_HEIGHT + _ROW_SPACING

    if overflow > 0:
        # The tail takes the gravest level among the rows it hides.
        hidden = state.status_flags[len(visible) :]
        tail_level = min((status_level(s) for _, _, s in hidden), key=STATUS_LEVELS.index)
        _draw_status_row(renderer, cr, badge_x, cursor_y, badge_w, _ROW_HEIGHT, f"+{overflow} more", tail_level)


def _draw_status_row(
    renderer: Any,
    cr: Any,
    x: float,
    y: float,
    w: float,
    h: float,
    message: str,
    level: str,
) -> None:
    """One badge row in its level's chip colours, led by the level's sign."""
    draw_level_box(cr, level, x, y, w, h, radius=PANEL_RADIUS, line_width=1.6)
    draw_level_sign(cr, level, x + _ICON_PAD + 6.0, y + h * 0.5)

    # Message text – bold, truncated.
    renderer._set_ui_font(cr, _ROW_FONT_SIZE, bold=True)
    cr.set_source_rgb(*COLOR_TEXT)
    text_x = x + _TEXT_PAD
    text_max_w = w - _TEXT_PAD - _ICON_PAD
    truncated = renderer._truncate_text_to_width(cr, message, text_max_w)
    # Baseline matches system stats panel baseline.
    cr.move_to(text_x, y + h * 0.7)
    cr.show_text(truncated)
