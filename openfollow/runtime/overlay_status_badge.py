# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Top-right HUD status badge for system warnings."""

from __future__ import annotations

import math
from typing import Any

from openfollow.runtime.overlay_draw_style import (
    COLOR_DANGER,
    COLOR_OK,
    COLOR_TEXT,
    draw_rounded_rect,
)
from openfollow.runtime.overlay_state import OverlayState

# Max visible rows; longer stacks roll into "+N more" tail.
_MAX_VISIBLE_ROWS = 4

# Vertical offset below system-stats panel.
_TOP_OFFSET = 10 + 24 + 6

# Fixed width to prevent reflow as warnings come/go.
_BADGE_WIDTH = 280.0
_ROW_HEIGHT = 22.0
_ROW_SPACING = 4.0
_ICON_PAD = 8.0
_TEXT_PAD = 24.0  # icon column reserved on the left


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

    badge_x = w - _BADGE_WIDTH - 10.0
    cursor_y = float(_TOP_OFFSET)

    for _key, message, severity in visible:
        _draw_warning_row(
            renderer,
            cr,
            badge_x,
            cursor_y,
            _BADGE_WIDTH,
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
            _BADGE_WIDTH,
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
    """One badge row – background, severity glyph, message text.

    Hoisted into a helper so the overflow row reuses the same chrome
    as a normal row. ``severity`` picks the colour and glyph: ``"error"``
    → danger red with a warning triangle, ``"info"`` → ok green with a
    filled dot. The row's background is hand-painted (rounded rect + 20%
    fill + solid border) rather than going through
    ``draw_panel_background`` so the badge can pick the colour per row
    without reaching into the shared helper's signature.
    """
    # Chrome matches the bottom-left info panel's failure state (rounded
    # rect + 20% fill + solid border) so a row reads with the same visual
    # language as a video / source failure elsewhere on the device UI.
    # Severity picks the colour: "error" → danger red, "info" → ok green.
    color = COLOR_OK if severity == "info" else COLOR_DANGER
    radius = 8.0
    draw_rounded_rect(cr, x, y, w, h, radius)
    cr.set_source_rgba(color[0], color[1], color[2], 0.20)
    cr.fill()
    draw_rounded_rect(cr, x, y, w, h, radius)
    cr.set_source_rgb(*color)
    cr.set_line_width(1.6)
    cr.stroke()

    # Glyph: triangle for errors, dot for info.
    glyph_size = 8.0
    glyph_cx = x + _ICON_PAD + glyph_size * 0.5
    glyph_cy = y + h * 0.5
    cr.set_source_rgb(*color)
    if severity == "info":
        cr.arc(glyph_cx, glyph_cy, glyph_size * 0.5, 0, 2 * math.pi)
        cr.fill()
    else:
        cr.move_to(glyph_cx, glyph_cy - glyph_size * 0.6)
        cr.line_to(glyph_cx - glyph_size * 0.5, glyph_cy + glyph_size * 0.5)
        cr.line_to(glyph_cx + glyph_size * 0.5, glyph_cy + glyph_size * 0.5)
        cr.close_path()
        cr.fill()

    # Message text – bold, truncated.
    renderer._set_ui_font(cr, 10, bold=True)
    cr.set_source_rgb(*COLOR_TEXT)
    text_x = x + _TEXT_PAD
    text_max_w = w - _TEXT_PAD - _ICON_PAD
    truncated = renderer._truncate_text_to_width(cr, message, text_max_w)
    # Baseline matches system stats panel baseline.
    cr.move_to(text_x, y + h * 0.7)
    cr.show_text(truncated)
