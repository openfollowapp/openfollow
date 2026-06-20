# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Shared style constants and helpers for Cairo overlay draw passes."""

from __future__ import annotations

import math
from typing import Any

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
COLOR_DANGER = (1.0, 0.549, 0.549)  # #ff8c8c (RGB, red, offline)

# Typography
FONT_UI_FAMILY = "Inter"


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


def draw_card_background(cr: Any, x: float, y: float, w: float, h: float, radius: float = 10.0) -> None:
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
