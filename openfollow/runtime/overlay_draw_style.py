# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Shared style constants and helpers for Cairo overlay draw passes."""

from __future__ import annotations

import math
from typing import Any

import cairo

# ==================================================================
# Color Palette (adapted from web UI)
# ==================================================================
# Background colors
COLOR_BG_BASE = (0.027, 0.075, 0.051)  # #07130d (RGB)
COLOR_BG_SOFT = (0.059, 0.133, 0.067)  # #0f2118 (RGB)

# Text colors
COLOR_TEXT = (0.969, 0.961, 0.914)  # #f7f5e9 (RGB, main text)
COLOR_TEXT_MUTED = (0.969, 0.961, 0.914, 0.68)  # muted text (RGBA)

# Accent (golden)
COLOR_ACCENT = (1.0, 0.737, 0.0)  # #ffbc00 (RGB)
COLOR_ACCENT_SOFT = (1.0, 0.737, 0.0, 0.12)  # soft accent background (RGBA)

# Borders and UI
COLOR_BORDER_SOFT = (1.0, 1.0, 1.0, 0.08)  # soft border (RGBA)
COLOR_BORDER = (1.0, 1.0, 1.0, 0.12)  # standard border (RGBA)

# Translucent fill for the shared overlay-card chrome (operator-message cards
# + every HUD panel). Marker cards paint their own opaque colour-coded chrome.
CARD_BG_ALPHA = 0.9

# Status indicators
COLOR_OK = (0.494, 0.898, 0.624)  # #7de59f (RGB, green, online)
# The HUD's warning red. Status rows, failure panels and a missing controller's
# card fill with it (the card keeps its marker-coloured border); dark enough
# that the HUD's normal text stays readable on it.
COLOR_DANGER_BG = (0.42, 0.08, 0.08)  # #6b1414 (RGB)
COLOR_WARNING_FILL = (*COLOR_DANGER_BG, 0.8)  # #6b1414 at 80% (RGBA)
COLOR_WARNING_BORDER = (0.69, 0.149, 0.149)  # #b02626 (RGB)
COLOR_INFO_BG = (0.09, 0.239, 0.42)  # #173d6b (RGB)
COLOR_INFO_FILL = (*COLOR_INFO_BG, 0.8)  # #173d6b at 80% (RGBA)
COLOR_INFO_BORDER = (0.149, 0.392, 0.69)  # #2664b0 (RGB)

# Typography
FONT_UI_FAMILY = "Inter"


# One corner radius per nesting level, so an inner corner is never rounder than its container.
MODAL_RADIUS = 14.0
PANEL_RADIUS = 6.0
ROW_RADIUS = 4.0


def draw_rounded_rect(cr: Any, x: float, y: float, w: float, h: float, radius: float) -> None:
    """Draw a rounded rectangle path on the given Cairo context."""
    if radius <= 0:
        cr.rectangle(x, y, w, h)
        return

    radius = min(radius, w / 2, h / 2)

    cr.move_to(x + radius, y)
    cr.line_to(x + w - radius, y)
    cr.arc(x + w - radius, y + radius, radius, -math.pi / 2, 0)
    cr.line_to(x + w, y + h - radius)
    cr.arc(x + w - radius, y + h - radius, radius, 0, math.pi / 2)
    cr.line_to(x + radius, y + h)
    cr.arc(x + radius, y + h - radius, radius, math.pi / 2, math.pi)
    cr.line_to(x, y + radius)
    cr.arc(x + radius, y + radius, radius, math.pi, 3 * math.pi / 2)
    cr.close_path()


def draw_warning_sign(cr: Any, cx: float, cy: float, size: float = 13.0) -> None:
    """Off-white warning triangle with its "!" in the warning red, centred on (cx, cy)."""
    half = size * 0.58
    cr.save()
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.set_line_width(size * 0.154)
    cr.move_to(cx, cy - size * 0.55)
    cr.line_to(cx - half, cy + size * 0.45)
    cr.line_to(cx + half, cy + size * 0.45)
    cr.close_path()
    cr.set_source_rgb(*COLOR_TEXT)
    cr.fill_preserve()
    cr.stroke()
    mark_h = size * 0.62
    bar_w = mark_h * 0.2
    bar_h = mark_h * 0.52
    bar_top = cy + size * 0.092 - mark_h * 0.42
    cr.set_source_rgb(*COLOR_DANGER_BG)
    cr.rectangle(cx - bar_w / 2, bar_top, bar_w, bar_h)
    cr.fill()
    cr.arc(cx, bar_top + bar_h + bar_w * 1.25, bar_w * 0.62, 0, 2 * math.pi)
    cr.fill()
    cr.restore()


def draw_info_sign(cr: Any, cx: float, cy: float, size: float = 13.0, cut: tuple[float, ...] = COLOR_BG_BASE) -> None:
    """Off-white disc with its "i" cut out in ``cut``, centred on (cx, cy)."""
    cr.save()
    cr.set_source_rgb(*COLOR_TEXT)
    cr.arc(cx, cy, size * 0.45, 0, 2 * math.pi)
    cr.fill()
    cr.set_source_rgb(*cut)
    stem_w = size * 0.1125
    cr.rectangle(cx - stem_w / 2, cy - size * 0.0875, stem_w, size * 0.325)
    cr.fill()
    cr.arc(cx, cy - size * 0.206, size * 0.069, 0, 2 * math.pi)
    cr.fill()
    cr.restore()


def draw_card_background(cr: Any, x: float, y: float, w: float, h: float, radius: float = PANEL_RADIUS) -> None:
    """Translucent card fill + soft 1px border – the shared overlay-card chrome.

    Used by the operator-message cards and every HUD panel (help, bottom-left
    info, system stats, modals, selection lists, faders) so their chrome reads
    as one visual language. Marker cards paint their own colour-coded chrome
    and deliberately don't route through here.
    """
    draw_rounded_rect(cr, x, y, w, h, radius)
    cr.set_source_rgba(COLOR_BG_BASE[0], COLOR_BG_BASE[1], COLOR_BG_BASE[2], CARD_BG_ALPHA)
    cr.fill()
    draw_rounded_rect(cr, x, y, w, h, radius)
    cr.set_source_rgba(*COLOR_BORDER)
    cr.set_line_width(1.0)
    cr.stroke()


def parse_hex(color: str) -> tuple[float, float, float]:
    """Parse '#rrggbb' to (r, g, b) floats in [0,1]."""
    c = color.lstrip("#")
    if len(c) < 6:
        return (1.0, 1.0, 1.0)
    try:
        return int(c[0:2], 16) / 255, int(c[2:4], 16) / 255, int(c[4:6], 16) / 255
    except ValueError:
        return (1.0, 1.0, 1.0)


def speed_color(speed: float, max_speed: float = 20.0) -> tuple[float, float, float]:
    """Map speed → (r, g, b) gradient: green → yellow → red."""
    if speed <= 0:
        return (0.133, 0.773, 0.369)
    t = min(speed / max_speed, 1.0)
    if t <= 0.5:
        s = t * 2.0
        return ((34 + 221 * s) / 255, (197 + 58 * s) / 255, 94 * (1 - s) / 255)
    s = (t - 0.5) * 2.0
    return (1.0, 1.0 - s, 0.0)
