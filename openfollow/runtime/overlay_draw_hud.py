# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""HUD and modal draw passes for the Cairo overlay renderer.

Draws the marker cards, info panels, system stats, virtual faders, and every
modal overlay (Settings, source/iface/method pickers, URL editor, About,
button-detection wizard, Pi network screens)."""

from __future__ import annotations

import sys
from typing import Any, cast

import cairo

from openfollow.runtime.overlay_draw_style import (
    COLOR_ACCENT,
    COLOR_ACCENT_SOFT,
    COLOR_BG_BASE,
    COLOR_BORDER_SOFT,
    COLOR_DANGER,
    COLOR_OK,
    COLOR_TEXT,
    COLOR_TEXT_MUTED,
    draw_card_background,
    draw_rounded_rect,
    parse_hex,
    speed_color,
)
from openfollow.runtime.overlay_layout import (
    HelpSections,
    bottom_left_info_panel_layout,
    build_help_sections,
    build_system_stats_text,
    centered_panel_layout,
    format_source_text,
    friendly_button_label,
    help_sections_height,
    key_label,
    marker_card_y,
    selectable_list_layout,
    virtual_fader_card_y,
)
from openfollow.runtime.overlay_state import (
    MarkerOverlayData,
    OverlayState,
    VirtualFaderDisplayData,
)
from openfollow.runtime.overlay_status_badge import draw_status_badge
from openfollow.units import UnitSystem, format_length_compact, format_speed


def _help_sections_for(
    renderer: Any,
    mode: str,
    state: OverlayState,
) -> HelpSections:
    """Memoised build_help_sections result; cheap recompute on rebind."""
    key = (
        mode,
        state.keyboard_connected,
        state.controller_connected,
        state.mouse_enabled,
        state.mouse_double_click_reset,
        tuple(sorted((state.button_labels or {}).items())),
        tuple(sorted((state.keyboard_labels or {}).items())),
        state.mouse3d_connected,
        tuple(sorted((state.mouse3d_axis_map or {}).items())),
        tuple(sorted((state.mouse3d_buttons or {}).items())),
        state.marker_cycle_enabled,
    )
    cached = renderer._help_sections_cache
    if cached is not None and cached[0] == key:
        return cast(HelpSections, cached[1])
    result = build_help_sections(
        mode=mode,
        keyboard_connected=state.keyboard_connected,
        controller_connected=state.controller_connected,
        mouse_enabled=state.mouse_enabled,
        double_click_reset=state.mouse_double_click_reset,
        # Scroll-wheel Z can't be polled on macOS – hide the hint there.
        scroll_z=sys.platform != "darwin",
        button_labels=state.button_labels,
        keyboard_labels=state.keyboard_labels,
        mouse3d_connected=state.mouse3d_connected,
        mouse3d_axis_map=state.mouse3d_axis_map,
        mouse3d_buttons=state.mouse3d_buttons,
        marker_cycle_enabled=state.marker_cycle_enabled,
    )
    renderer._help_sections_cache = (key, result)
    return result


def draw_help_block(
    renderer: Any,
    cr: Any,
    x: float,
    y: float,
    width: float,
    sections: HelpSections,
) -> None:
    y_cursor = y
    for idx, (title, lines) in enumerate(sections):
        renderer._set_ui_font(cr, 10.5, bold=True)
        cr.set_source_rgb(*COLOR_ACCENT)
        cr.move_to(x, y_cursor)
        cr.show_text(title.upper())
        y_cursor += 13.0

        renderer._set_ui_font(cr, 10)
        cr.set_source_rgb(*COLOR_TEXT)
        for line in lines:
            line_text = renderer._truncate_text_to_width(cr, f"• {line}", width)
            cr.move_to(x, y_cursor)
            cr.show_text(line_text)
            y_cursor += 14.0

        if idx < len(sections) - 1:
            y_cursor += 8.0


def draw_modal_scrim(cr: Any, w: int, h: int, alpha: float = 0.62) -> None:
    cr.set_source_rgba(0.0, 0.0, 0.0, alpha)
    cr.rectangle(0, 0, w, h)
    cr.fill()


def draw_modal_shell(
    renderer: Any,
    cr: Any,
    w: int,
    h: int,
    *,
    title: str,
    subtitle: str,
    panel_w: float,
    panel_h: float,
    title_color: tuple[float, float, float] = COLOR_ACCENT,
) -> tuple[float, float, float, float]:
    panel_layout = centered_panel_layout(w, h, panel_w, panel_h)
    panel_x, panel_y = panel_layout.x, panel_layout.y
    panel_w, panel_h = panel_layout.width, panel_layout.height

    draw_panel_background(renderer, cr, panel_x, panel_y, panel_w, panel_h, radius=14)
    cr.set_source_rgba(*COLOR_BORDER_SOFT)
    draw_rounded_rect(cr, panel_x, panel_y, panel_w, panel_h, 14)
    cr.set_line_width(1.0)
    cr.stroke()

    renderer._set_ui_font(cr, 23 if h >= 720 else 20, bold=True)
    cr.set_source_rgb(*title_color)
    title_ext = cr.text_extents(title)
    title_x = panel_x + (panel_w - title_ext.width) / 2.0 - title_ext.x_bearing
    title_y = panel_y + 34.0
    cr.move_to(title_x, title_y)
    cr.show_text(title)

    renderer._set_ui_font(cr, 11)
    cr.set_source_rgba(*COLOR_TEXT_MUTED)
    subtitle_text = renderer._truncate_text_to_width(cr, subtitle, panel_w - 48.0)
    subtitle_ext = cr.text_extents(subtitle_text)
    subtitle_x = panel_x + (panel_w - subtitle_ext.width) / 2.0 - subtitle_ext.x_bearing
    subtitle_y = panel_y + 56.0
    cr.move_to(subtitle_x, subtitle_y)
    cr.show_text(subtitle_text)
    return panel_x, panel_y, panel_w, panel_h


def draw_selectable_list(
    renderer: Any,
    cr: Any,
    *,
    items: list[str],
    selected_idx: int,
    x: float,
    y: float,
    w: float,
    h: float,
    empty_message: str,
) -> None:
    draw_rounded_rect(cr, x, y, w, h, 10.0)
    cr.set_source_rgba(0.0, 0.0, 0.0, 0.26)
    cr.fill()
    cr.set_source_rgba(*COLOR_BORDER_SOFT)
    draw_rounded_rect(cr, x, y, w, h, 10.0)
    cr.set_line_width(1.0)
    cr.stroke()

    if not items:
        renderer._set_ui_font(cr, 12)
        cr.set_source_rgba(*COLOR_TEXT_MUTED)
        msg = renderer._truncate_text_to_width(cr, empty_message, w - 20.0)
        ext = cr.text_extents(msg)
        cr.move_to(x + (w - ext.width) / 2.0, y + h / 2.0)
        cr.show_text(msg)
        return

    item_h = 30.0
    list_layout = selectable_list_layout(len(items), selected_idx, h, item_h=item_h, vertical_padding=14.0)
    max_visible = list_layout.max_visible
    scroll_offset = list_layout.scroll_offset

    text_max_w = w - 32.0
    for i in range(max_visible):
        item_idx = i + scroll_offset
        if item_idx >= len(items):
            break

        row_x = x + 7.0
        row_y = y + 7.0 + i * item_h
        row_w = w - 14.0
        row_h = item_h - 3.0
        is_selected = item_idx == selected_idx
        if is_selected:
            cr.set_source_rgba(*COLOR_ACCENT_SOFT)
            draw_rounded_rect(cr, row_x, row_y, row_w, row_h, 7.0)
            cr.fill()
            cr.set_source_rgba(COLOR_ACCENT[0], COLOR_ACCENT[1], COLOR_ACCENT[2], 0.42)
            draw_rounded_rect(cr, row_x, row_y, row_w, row_h, 7.0)
            cr.set_line_width(1.1)
            cr.stroke()
            renderer._set_ui_font(cr, 12, bold=True)
            cr.set_source_rgb(*COLOR_TEXT)
        else:
            renderer._set_ui_font(cr, 11.5)
            cr.set_source_rgba(*COLOR_TEXT_MUTED)

        text = renderer._truncate_text_to_width(cr, items[item_idx], text_max_w)
        cr.move_to(row_x + 10.0, row_y + row_h / 2.0 + 4.0)
        cr.show_text(text)

    if scroll_offset > 0:
        renderer._set_ui_font(cr, 11)
        cr.set_source_rgba(*COLOR_TEXT_MUTED)
        cr.move_to(x + w - 18.0, y + 16.0)
        cr.show_text("^")
    if scroll_offset + max_visible < len(items):
        renderer._set_ui_font(cr, 11)
        cr.set_source_rgba(*COLOR_TEXT_MUTED)
        cr.move_to(x + w - 18.0, y + h - 10.0)
        cr.show_text("v")


def draw_selection_menu(
    renderer: Any,
    cr: Any,
    state: OverlayState,
    w: int,
    h: int,
    *,
    title: str,
    subtitle: str,
    mode: str,
    items: list[str],
    selected_idx: int,
    empty_message: str,
) -> None:
    panel_w = min(w * 0.52, 760.0)
    panel_h = min(h * 0.84, 720.0)
    panel_x, panel_y, panel_w, panel_h = draw_modal_shell(
        renderer,
        cr,
        w,
        h,
        title=title,
        subtitle=subtitle,
        panel_w=panel_w,
        panel_h=panel_h,
    )

    content_x = panel_x + 16.0
    content_w = panel_w - 32.0
    cursor_y = panel_y + 74.0

    help_secs = _help_sections_for(renderer, mode, state)
    help_h = help_sections_height(help_secs)
    if help_h > 0:
        block_h = help_h + 14.0
        draw_panel_background(renderer, cr, content_x, cursor_y, content_w, block_h, radius=10)
        draw_help_block(
            renderer,
            cr,
            content_x + 10.0,
            cursor_y + 13.0,
            content_w - 20.0,
            help_secs,
        )
        cursor_y += block_h + 12.0

    list_h = max(80.0, panel_y + panel_h - cursor_y - 14.0)
    draw_selectable_list(
        renderer,
        cr,
        items=items,
        selected_idx=selected_idx,
        x=content_x,
        y=cursor_y,
        w=content_w,
        h=list_h,
        empty_message=empty_message,
    )


def draw_source_selection(renderer: Any, cr: Any, state: OverlayState, w: int, h: int) -> None:
    draw_selection_menu(
        renderer,
        cr,
        state,
        w,
        h,
        title=state.source_selection_title,
        subtitle="Choose a source and confirm to reconnect video.",
        mode="source-selection",
        items=list(state.discovered_sources),
        selected_idx=state.selected_source_index,
        empty_message="Scanning for available sources...",
    )


def draw_source_selection_overlay(renderer: Any, cr: Any, state: OverlayState, w: int, h: int) -> None:
    draw_modal_scrim(cr, w, h, alpha=0.56)
    draw_source_selection(renderer, cr, state, w, h)


def draw_iface_selection(renderer: Any, cr: Any, state: OverlayState, w: int, h: int) -> None:
    formatted_ifaces = [iface if iface else "Auto-detect" for iface in state.available_interfaces]
    draw_selection_menu(
        renderer,
        cr,
        state,
        w,
        h,
        title="SELECT NETWORK INTERFACE",
        subtitle="Choose the interface used for PSN, mDNS, and receiver binding.",
        mode="iface-selection",
        items=formatted_ifaces,
        selected_idx=state.selected_iface_index,
        empty_message="No interfaces detected.",
    )


def draw_iface_selection_overlay(renderer: Any, cr: Any, state: OverlayState, w: int, h: int) -> None:
    draw_modal_scrim(cr, w, h, alpha=0.56)
    draw_iface_selection(renderer, cr, state, w, h)


def draw_source_type_selection(
    renderer: Any,
    cr: Any,
    state: OverlayState,
    w: int,
    h: int,
) -> None:
    items = [display for _iid, display in state.available_source_types]
    draw_selection_menu(
        renderer,
        cr,
        state,
        w,
        h,
        title="VIDEO SOURCE TYPE",
        subtitle="Switch the active video plugin (RTSP, NDI, Test Pattern, …).",
        mode="source-type-selection",
        items=items,
        selected_idx=state.selected_source_type_index,
        empty_message="No video source plugins available on this device.",
    )


def draw_source_type_selection_overlay(
    renderer: Any,
    cr: Any,
    state: OverlayState,
    w: int,
    h: int,
) -> None:
    draw_modal_scrim(cr, w, h, alpha=0.56)
    draw_source_type_selection(renderer, cr, state, w, h)


def draw_field_choice_picker(
    renderer: Any,
    cr: Any,
    state: OverlayState,
    w: int,
    h: int,
) -> None:
    """Render the on-device list picker for an enum-style plugin field
    (e.g. testpattern's grey vs stage)."""
    title = (state.field_choice_title or "SELECT VALUE").upper()
    draw_selection_menu(
        renderer,
        cr,
        state,
        w,
        h,
        title=title,
        subtitle="Pick a value, Enter to confirm, Esc to cancel.",
        mode="field-choice",
        items=list(state.field_choice_items),
        selected_idx=state.field_choice_selected_index,
        empty_message="No options available.",
    )


def draw_field_choice_picker_overlay(
    renderer: Any,
    cr: Any,
    state: OverlayState,
    w: int,
    h: int,
) -> None:
    draw_modal_scrim(cr, w, h, alpha=0.56)
    draw_field_choice_picker(renderer, cr, state, w, h)


def draw_url_editor(
    renderer: Any,
    cr: Any,
    state: OverlayState,
    w: int,
    h: int,
) -> None:
    """Render on-device single-line text editor with caret."""
    title = (state.url_editor_field_label or "URL").upper()
    subtitle = state.url_editor_banner or ("Type the value, Backspace to delete, Enter to save, Esc to cancel.")
    panel_w = min(w * 0.72, 980.0)
    panel_h = min(h * 0.32, 280.0)
    panel_x, panel_y, panel_w, panel_h = draw_modal_shell(
        renderer,
        cr,
        w,
        h,
        title=title,
        subtitle=subtitle,
        panel_w=panel_w,
        panel_h=panel_h,
    )

    # Text-field box with steady caret (no blink animation).
    box_x = panel_x + 24.0
    box_y = panel_y + 84.0
    box_w = panel_w - 48.0
    box_h = 56.0
    draw_panel_background(renderer, cr, box_x, box_y, box_w, box_h, radius=10)

    renderer._set_ui_font(cr, 18.0)
    cr.set_source_rgba(*COLOR_TEXT)
    text_x = box_x + 16.0
    text_y = box_y + box_h / 2.0 + 6.0
    value = state.url_editor_value
    rendered = renderer._truncate_text_to_width(cr, value, box_w - 40.0)
    cr.move_to(text_x, text_y)
    cr.show_text(rendered)

    rendered_ext = cr.text_extents(rendered)
    cursor_x = text_x + rendered_ext.x_advance + 1.0
    cursor_y_top = box_y + 14.0
    cursor_y_bot = box_y + box_h - 14.0
    cr.set_source_rgba(*COLOR_ACCENT)
    cr.set_line_width(1.6)
    cr.move_to(cursor_x, cursor_y_top)
    cr.line_to(cursor_x, cursor_y_bot)
    cr.stroke()


def draw_url_editor_overlay(
    renderer: Any,
    cr: Any,
    state: OverlayState,
    w: int,
    h: int,
) -> None:
    draw_modal_scrim(cr, w, h, alpha=0.56)
    draw_url_editor(renderer, cr, state, w, h)


def draw_settings_menu(renderer: Any, cr: Any, state: OverlayState, w: int, h: int) -> None:
    """Render Settings menu modal with info card and error box."""
    labels = list(state.settings_items)
    enabled = list(state.settings_items_enabled)
    reasons = list(state.settings_items_disabled_reasons)
    if len(reasons) < len(labels):
        reasons.extend([""] * (len(labels) - len(reasons)))
    items: list[str] = []
    for label, is_enabled, reason in zip(labels, enabled, reasons, strict=False):
        if is_enabled:
            items.append(label)
        else:
            items.append(f"{label} ({reason or 'unavailable'})")

    # Modal shell; recovery context (banner/error_message) in red-bordered box below for prominence.
    panel_w = min(w * 0.56, 820.0)
    panel_h = min(h * 0.88, 760.0)
    panel_x, panel_y, panel_w, panel_h = draw_modal_shell(
        renderer,
        cr,
        w,
        h,
        title="SETTINGS",
        subtitle="Open a sub-screen.",
        panel_w=panel_w,
        panel_h=panel_h,
    )

    content_x = panel_x + 16.0
    content_w = panel_w - 32.0
    cursor_y = panel_y + 74.0

    cursor_y = _draw_settings_info_card(
        renderer,
        cr,
        state,
        content_x,
        cursor_y,
        content_w,
    )
    # Single error surface: banner takes priority (auto-open path
    # passes a structured message including source + GStreamer error),
    # falling back to a raw ``error_message`` if the operator opened
    # Settings manually while video was already in an error state.
    error_text = state.settings_menu_banner or state.error_message
    if error_text:
        cursor_y = _draw_settings_error_box(
            renderer,
            cr,
            error_text,
            content_x,
            cursor_y,
            content_w,
        )

    help_secs = _help_sections_for(renderer, "settings", state)
    help_h = help_sections_height(help_secs)
    if help_h > 0:
        block_h = help_h + 14.0
        draw_panel_background(
            renderer,
            cr,
            content_x,
            cursor_y,
            content_w,
            block_h,
            radius=10,
        )
        draw_help_block(
            renderer,
            cr,
            content_x + 10.0,
            cursor_y + 13.0,
            content_w - 20.0,
            help_secs,
        )
        cursor_y += block_h + 12.0

    list_h = max(80.0, panel_y + panel_h - cursor_y - 14.0)
    draw_selectable_list(
        renderer,
        cr,
        items=items,
        selected_idx=state.settings_selected_index,
        x=content_x,
        y=cursor_y,
        w=content_w,
        h=list_h,
        empty_message="No settings available.",
    )


def _draw_settings_info_card(
    renderer: Any,
    cr: Any,
    state: OverlayState,
    x: float,
    y: float,
    w: float,
) -> float:
    """Render IP/Source info card matching bottom-left panel; return y-cursor."""
    ip_value = state.ip_text or "Unavailable"
    src_value = format_source_text(
        state.video_source_type,
        state.source_label,
        max_len=64,
    )
    station_value = state.station_name or "OpenFollow"
    rows = [
        ("IP Address:", ip_value),
        ("Video Source:", src_value),
        ("Station:", station_value),
    ]
    # Surface unbound controller pads so operators can spot stray input.
    # 1-based to match the marker-card badge + OSC ``:cN``.
    if state.unbound_controller_indices:
        joined = ", ".join(f"Ctrl{i + 1}" for i in state.unbound_controller_indices)
        rows.append(("Unbound controllers:", joined))
    row_h = 20.0
    card_h = row_h * len(rows) + 14.0
    draw_panel_background(renderer, cr, x, y, w, card_h, radius=10)
    row_y = y + 17.0
    label_w = 110.0
    for label, value in rows:
        renderer._set_ui_font(cr, 10.5, bold=True)
        cr.set_source_rgba(*COLOR_TEXT_MUTED)
        cr.move_to(x + 12.0, row_y)
        cr.show_text(label)
        renderer._set_ui_font(cr, 11.5)
        cr.set_source_rgb(*COLOR_TEXT)
        max_value_w = max(40.0, w - label_w - 24.0)
        safe = renderer._truncate_text_to_width(cr, value, max_value_w)
        cr.move_to(x + 12.0 + label_w, row_y)
        cr.show_text(safe)
        row_y += row_h
    return y + card_h + 10.0


def _draw_settings_error_box(
    renderer: Any,
    cr: Any,
    message: str,
    x: float,
    y: float,
    w: float,
) -> float:
    """Render red-bordered error box for Settings modal."""
    label = "ERROR"
    pad = 12.0
    title_size = 11.0
    body_size = 13.0
    body_lines = _wrap_error_message(renderer, cr, message, w - 2 * pad, body_size)
    body_line_h = body_size + 6.0
    body_h = body_line_h * len(body_lines)
    card_h = pad * 2 + title_size + 6.0 + body_h

    # Subtle red wash inside, hard red border outside.
    draw_rounded_rect(cr, x, y, w, card_h, 10.0)
    cr.set_source_rgba(
        COLOR_DANGER[0],
        COLOR_DANGER[1],
        COLOR_DANGER[2],
        0.18,
    )
    cr.fill()
    cr.set_source_rgb(*COLOR_DANGER)
    draw_rounded_rect(cr, x, y, w, card_h, 10.0)
    cr.set_line_width(2.0)
    cr.stroke()

    renderer._set_ui_font(cr, title_size, bold=True)
    cr.set_source_rgb(*COLOR_DANGER)
    cr.move_to(x + pad, y + pad + title_size - 2.0)
    cr.show_text(label)

    renderer._set_ui_font(cr, body_size, bold=True)
    cr.set_source_rgb(*COLOR_TEXT)
    line_y = y + pad + title_size + 6.0 + body_size
    for line in body_lines:
        cr.move_to(x + pad, line_y)
        cr.show_text(line)
        line_y += body_line_h
    return y + card_h + 10.0


def _wrap_error_message(
    renderer: Any,
    cr: Any,
    message: str,
    max_w: float,
    font_size: float,
    bold: bool = True,
) -> list[str]:
    """Greedy word-wrap respecting ``max_w`` at the given font size and weight.

    ``bold`` must match the render weight (bold glyphs are wider). Returns at
    least one line.
    """
    renderer._set_ui_font(cr, font_size, bold=bold)
    if not message:
        return [""]
    words = message.split()
    if not words:
        return [message]

    # Hard-wrap single tokens wider than max_w to prevent overflow.
    def _split_long(token: str) -> list[str]:
        if cr.text_extents(token).width <= max_w:
            return [token]
        chunks: list[str] = []
        buf = ""
        for ch in token:
            candidate = buf + ch
            if cr.text_extents(candidate).width <= max_w:
                buf = candidate
            else:
                if buf:
                    chunks.append(buf)
                buf = ch
        chunks.append(buf)
        return chunks

    lines: list[str] = []
    current = ""
    for word in words:
        pieces = _split_long(word)
        for i, piece in enumerate(pieces):
            if not current:
                current = piece
                continue
            sep = "" if i > 0 else " "
            candidate = f"{current}{sep}{piece}"
            if cr.text_extents(candidate).width <= max_w:
                current = candidate
            else:
                lines.append(current)
                current = piece
    lines.append(current)
    return lines


def draw_settings_overlay(renderer: Any, cr: Any, state: OverlayState, w: int, h: int) -> None:
    draw_modal_scrim(cr, w, h, alpha=0.56)
    draw_settings_menu(renderer, cr, state, w, h)


def draw_about_screen(renderer: Any, cr: Any, state: OverlayState, w: int, h: int) -> None:
    """Render the read-only About / license screen.

    Self-contained Cairo text – no WebKit dependency – so the program name,
    version, copyright, the AGPLv3-or-later + full no-warranty notice, the
    project link and the not-for-safety-critical-use warning are always
    reachable on the device, even when the embedded browser isn't available.
    """
    from openfollow import __version__

    panel_w = min(w * 0.62, 720.0)
    panel_h = min(h * 0.86, 640.0)
    panel_x, panel_y, panel_w, panel_h = draw_modal_shell(
        renderer,
        cr,
        w,
        h,
        title="ABOUT",
        subtitle="Press OK / Back (Enter / Esc) to return",
        panel_w=panel_w,
        panel_h=panel_h,
    )

    cx = panel_x + panel_w / 2.0
    cursor_y = panel_y + 72.0

    def _line(
        text: str,
        size: float,
        color: tuple[float, ...],
        *,
        bold: bool = False,
        gap: float = 6.0,
    ) -> None:
        nonlocal cursor_y
        renderer._set_ui_font(cr, size, bold=bold)
        # Style palette mixes RGB (COLOR_ACCENT / COLOR_TEXT) and RGBA
        # (COLOR_TEXT_MUTED carries a dimming alpha); pick the right setter.
        if len(color) == 4:
            cr.set_source_rgba(*color)
        else:
            cr.set_source_rgb(*color)
        ext = cr.text_extents(text)
        cr.move_to(cx - ext.width / 2.0 - ext.x_bearing, cursor_y + ext.height)
        cr.show_text(text)
        cursor_y += ext.height + gap

    def _para(
        text: str,
        size: float,
        color: tuple[float, ...],
        *,
        bold: bool = False,
        gap: float = 16.0,
        line_gap: float = 2.0,
    ) -> None:
        """Greedy word-wrap a paragraph to the panel width, drawing each
        wrapped line centered. Tight ``line_gap`` between a paragraph's
        own lines; the larger ``gap`` applies only after its last line."""
        renderer._set_ui_font(cr, size, bold=bold)
        max_w = panel_w - 64.0
        wrapped: list[str] = []
        current = ""
        for word in text.split():
            candidate = f"{current} {word}".strip()
            if not current or cr.text_extents(candidate).width <= max_w:
                current = candidate
            else:
                wrapped.append(current)
                current = word
        # pragma: no branch – every ``_para`` caller passes a non-empty
        # paragraph, so the loop always leaves ``current`` holding the
        # final line; the empty-``current`` arm is defensive only.
        if current:  # pragma: no branch
            wrapped.append(current)
        for i, ln in enumerate(wrapped):
            last = i == len(wrapped) - 1
            _line(ln, size, color, bold=bold, gap=gap if last else line_gap)

    # Wordmark logo as the headline; fall back to the text name when the SVG
    # logo can't be rendered (Rsvg unavailable / load failed).
    logo_handle = getattr(renderer, "_logo_handle", None)
    if logo_handle is not None:
        logo_w = min(panel_w * 0.42, 240.0)
        logo_h = renderer._draw_logo(cr, cx - logo_w / 2.0, cursor_y, logo_w)
        cursor_y += logo_h + 16.0
    else:
        _line("OpenFollow", 30, COLOR_ACCENT, bold=True, gap=4.0)
    _line(f"Version {__version__}", 13, COLOR_TEXT_MUTED, gap=18.0)
    _line("Copyright (C) 2026 The OpenFollow Project", 13, COLOR_TEXT, gap=4.0)
    _line(
        "Paul Hermann · Michel Honold · Vinzenz Schultz",
        12,
        COLOR_TEXT_MUTED,
        gap=18.0,
    )
    _line("Licensed under the GNU AGPL v3 or later.", 13, COLOR_TEXT, gap=8.0)
    _para(
        "OpenFollow is distributed in the hope that it will be useful, but "
        "WITHOUT ANY WARRANTY; without even the implied warranty of "
        "MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU "
        "Affero General Public License for more details.",
        11,
        COLOR_TEXT_MUTED,
        gap=16.0,
    )
    _line(
        "More Information, Source and License: openfollow.app",
        12,
        COLOR_TEXT,
        gap=18.0,
    )
    _para(
        "Built on Debian GNU/Linux and Raspberry Pi OS – not affiliated with or "
        "endorsed by the Debian Project, Raspberry Pi Ltd, or Software in the "
        "Public Interest.",
        11,
        COLOR_TEXT_MUTED,
        gap=18.0,
    )
    _para(
        "OpenFollow is intended to coordinate visual and audio elements of a "
        "production and should not be used for safety critical applications.",
        12,
        COLOR_DANGER,
        bold=True,
        gap=4.0,
    )


def draw_about_overlay(renderer: Any, cr: Any, state: OverlayState, w: int, h: int) -> None:
    draw_modal_scrim(cr, w, h, alpha=0.56)
    draw_about_screen(renderer, cr, state, w, h)


def draw_hud(renderer: Any, cr: Any, state: OverlayState, w: int, h: int) -> None:
    # Controller badges render per-marker card; unbound pads in Settings menu.
    draw_bottom_left_info_panel(renderer, cr, state, w, h)

    icon_size = 24.0
    icon_x, icon_y = 10.0, 10.0
    renderer._draw_icon(cr, icon_x, icon_y, icon_size)

    if state.show_hud_help:
        help_secs = _help_sections_for(renderer, "normal", state)
        help_h = help_sections_height(help_secs)
        if help_h > 0:
            panel_x = 10.0
            panel_y = icon_y + icon_size + 4.0
            panel_w = max(160.0, min(220.0, w - 20.0))
            panel_h = help_h + 20.0
            draw_panel_background(renderer, cr, panel_x, panel_y, panel_w, panel_h, radius=11)
            draw_help_block(
                renderer,
                cr,
                panel_x + 12.0,
                panel_y + 18.0,
                panel_w - 24.0,
                help_secs,
            )
    else:
        renderer._set_ui_font(cr, 9)
        cr.set_source_rgba(*COLOR_TEXT_MUTED)
        help_key = key_label(state.keyboard_labels.get("toggle_help") or "h", fallback="")
        help_btn = friendly_button_label(state.button_labels.get("toggle_help") or "Y")
        parts = [p for p in (help_key, help_btn) if p]
        if parts:
            hint = f"{' / '.join(parts)}: help"
            cr.move_to(icon_x + icon_size + 4.0, icon_y + icon_size * 0.7)
            cr.show_text(hint)

    draw_system_stats(renderer, cr, state, w)

    # card_h 67 (was 64): the speed number (top-anchored at y+47) and the
    # bottom-anchored speed bar (y + h - 14) were only ~3px apart; the extra
    # 3px lands between them for readability without crowding the text above.
    card_w, card_h, card_m = 180, 67, 8
    card_x = w - 16 - card_w
    # The assist-mode AI-output ghost is scene-only – it has no marker card.
    card_markers = [t for t in state.markers if not t.is_assist_ghost]
    for i, t in enumerate(card_markers):
        card_y = marker_card_y(i, h, card_h=card_h, card_margin=card_m, bottom_padding=14.0)
        draw_marker_card(renderer, cr, card_x, card_y, card_w, card_h, t, t.marker_id == state.selected_id, state)

    # Virtual fader stack mirroring marker-card visual weight on bottom-left.
    draw_virtual_faders(renderer, cr, state, h)

    # Top-right status badge; rendered last so modals sit on top.
    draw_status_badge(renderer, cr, state, w, h)


def draw_bottom_left_info_panel(renderer: Any, cr: Any, state: OverlayState, w: int, h: int) -> None:
    ip_label = "IP Address:"
    src_label = "Video Source:"
    station_label = "Station:"
    ip_value = state.ip_text or "Unavailable"
    src_value = format_source_text(state.video_source_type, state.source_label)
    station_value = state.station_name or "OpenFollow"

    label_size = 9.5
    value_size = 10.8
    side_padding = 12.0
    # Three rows of 20 px + 14 px top/bottom padding.
    panel_h = 74.0

    # Memoize layout + truncated values so unchanged panel skips measurement.
    cache_key = (ip_value, src_value, station_value, w, h)
    cached = renderer._info_panel_cache
    if cached is not None and cached[0] == cache_key:
        layout, ip_value, src_value, station_value = cached[1]
    else:
        renderer._set_ui_font(cr, label_size, bold=True)
        label_w = max(
            cr.text_extents(ip_label).width,
            cr.text_extents(src_label).width,
            cr.text_extents(station_label).width,
        )
        renderer._set_ui_font(cr, value_size)
        value_w = max(
            cr.text_extents(ip_value).width,
            cr.text_extents(src_value).width,
            cr.text_extents(station_value).width,
        )
        layout = bottom_left_info_panel_layout(
            frame_width=w,
            frame_height=h,
            label_w=label_w,
            value_w=value_w,
            side_padding=side_padding,
            panel_h=panel_h,
        )
        ip_value = renderer._truncate_text_to_width(
            cr,
            ip_value,
            layout.value_max_w,
        )
        src_value = renderer._truncate_text_to_width(
            cr,
            src_value,
            layout.value_max_w,
        )
        station_value = renderer._truncate_text_to_width(
            cr,
            station_value,
            layout.value_max_w,
        )
        renderer._info_panel_cache = (
            cache_key,
            (layout, ip_value, src_value, station_value),
        )

    panel_x = layout.panel_x
    panel_y = layout.panel_y
    panel_w = layout.panel_w
    label_x = layout.label_x
    value_x = layout.value_x

    # When the Settings menu carries a banner or the receiver
    # surfaces an error_message, the bottom-left info panel turns red
    # so operators glancing at the HUD spot the failure even when
    # they don't have the Settings menu open. Matches the trigger
    # condition for the Settings menu's red-bordered error box.
    in_error = bool(state.settings_menu_banner or state.error_message)
    if in_error:
        draw_rounded_rect(cr, panel_x, panel_y, panel_w, panel_h, 11)
        cr.set_source_rgba(
            COLOR_DANGER[0],
            COLOR_DANGER[1],
            COLOR_DANGER[2],
            0.20,
        )
        cr.fill()
        cr.set_source_rgb(*COLOR_DANGER)
        draw_rounded_rect(cr, panel_x, panel_y, panel_w, panel_h, 11)
        cr.set_line_width(1.6)
        cr.stroke()
    else:
        draw_panel_background(renderer, cr, panel_x, panel_y, panel_w, panel_h, radius=11)

    row1_y = panel_y + 20
    row2_y = panel_y + 40
    row3_y = panel_y + 60
    renderer._set_ui_font(cr, label_size, bold=True)
    cr.set_source_rgba(*COLOR_TEXT_MUTED)
    cr.move_to(label_x, row1_y)
    cr.show_text(ip_label)
    cr.move_to(label_x, row2_y)
    cr.show_text(src_label)
    cr.move_to(label_x, row3_y)
    cr.show_text(station_label)

    renderer._set_ui_font(cr, value_size)
    if in_error:
        cr.set_source_rgb(*COLOR_DANGER)
    else:
        cr.set_source_rgb(*COLOR_TEXT)
    cr.move_to(value_x, row1_y)
    cr.show_text(ip_value)
    cr.move_to(value_x, row2_y)
    cr.show_text(src_value)
    cr.move_to(value_x, row3_y)
    cr.show_text(station_value)


def draw_system_stats(renderer: Any, cr: Any, state: OverlayState, w: int) -> None:
    stats_text = build_system_stats_text(state.cpu_percent, state.ram_percent, state.temperature)

    renderer._set_ui_font(cr, 11)
    ext = cr.text_extents(stats_text)
    panel_w = ext.width + 20
    panel_h = 24
    panel_x = w - panel_w - 10

    draw_panel(renderer, cr, panel_x, 10, panel_w, panel_h, stats_text, 11)


def draw_panel_background(renderer: Any, cr: Any, x: float, y: float, w: float, h: float, radius: float = 10) -> None:
    # Shared overlay-card chrome (translucent fill + soft border) so every
    # panel reads in the same visual language as the operator-message cards.
    draw_card_background(cr, x, y, w, h, radius)


def draw_panel(renderer: Any, cr: Any, x: float, y: float, w: float, h: float, text: str, font_size: float) -> None:
    draw_panel_background(renderer, cr, x, y, w, h)

    cr.set_source_rgb(*COLOR_TEXT)
    renderer._set_ui_font(cr, font_size)
    ext = cr.text_extents(text)
    tx = x + (w - ext.width) / 2 - ext.x_bearing
    ty = y + (h - ext.height) / 2 - ext.y_bearing
    cr.move_to(tx, ty)
    cr.show_text(text)


def draw_marker_card(
    renderer: Any,
    cr: Any,
    x: float,
    y: float,
    w: float,
    h: float,
    t: MarkerOverlayData,
    selected: bool,
    state: OverlayState | None = None,
) -> None:
    radius = 10

    # Viewer-only markers: use Cairo group for uniform alpha; skip grouping for controlled markers (hot path).
    use_group = not t.is_controlled
    if use_group:
        cr.push_group()
    try:
        # Solid background; border uses marker color for per-card identity.
        cr.set_source_rgb(*COLOR_BG_BASE)
        draw_rounded_rect(cr, x, y, w, h, radius)
        cr.fill()

        # Selection differentiates via edge_alpha + line_width.
        edge_alpha = 0.95 if selected else 0.62
        br, bg, bb = parse_hex(t.color)
        cr.set_source_rgba(br, bg, bb, edge_alpha)
        draw_rounded_rect(cr, x, y, w, h, radius)
        cr.set_line_width(2.8 if selected else 2.5)
        cr.stroke()

        renderer._set_ui_font(cr, 10)

        dot_r = 4.5
        dot_x = x + w - dot_r - 6
        dot_y = y + dot_r + 6
        if t.online:
            cr.set_source_rgb(*COLOR_OK)
        else:
            cr.set_source_rgb(*COLOR_DANGER)
        cr.arc(dot_x, dot_y, dot_r, 0, 6.2832)
        cr.fill()

        if t.online:
            cr.set_source_rgba(*COLOR_OK, 0.3)
            cr.arc(dot_x, dot_y, dot_r + 1.5, 0, 6.2832)
            cr.stroke()

        # Controller badge top-left (controlled markers only); disconnected pads
        # render with dot suffix. 1-based to match the OSC ``:cN`` reference.
        if t.is_controlled and t.controller_idx is not None:
            renderer._set_ui_font(cr, 9)
            if t.controller_connected:
                cr.set_source_rgb(*COLOR_TEXT)
                badge = f"C{t.controller_idx + 1}"
            else:
                cr.set_source_rgba(*COLOR_TEXT_MUTED)
                badge = f"C{t.controller_idx + 1}·"
            cr.move_to(x + 8, y + 14)
            cr.show_text(badge)

        if selected:
            cr.set_source_rgb(*COLOR_ACCENT)
        else:
            cr.set_source_rgb(*COLOR_TEXT)
        renderer._set_ui_font(cr, 13, bold=True)
        # Catalog name preferred over M<id> synthetic label; fallback when no catalog entry.
        label = t.name if t.name else f"M{t.marker_id}"
        ext = cr.text_extents(label)
        cr.move_to(x + (w - ext.width) / 2, y + 18)
        cr.show_text(label)

        cr.set_source_rgba(*COLOR_TEXT_MUTED)
        renderer._set_ui_font(cr, 10)
        display_z = t.z
        if state is not None and state.z_display_from_stage and state.grid_config:
            display_z = t.z - state.grid_config[5]
        # Position/speed in active unit system; state can be None → default metric.
        us = state.unit_system if state is not None else UnitSystem.METRIC
        pos = (
            f"{format_length_compact(t.x, us)}  "
            f"{format_length_compact(t.y, us)}  "
            f"{format_length_compact(display_z, us)}"
        )
        ext = cr.text_extents(pos)
        cr.move_to(x + (w - ext.width) / 2, y + 34)
        cr.show_text(pos)

        speed = max(0.0, float(t.speed) if t.speed is not None else 0.0)
        cr.set_source_rgb(*COLOR_TEXT)
        renderer._set_ui_font(cr, 9)
        stxt = format_speed(speed, us)
        # Per-marker gamepad fader appended to speed line; only when marker_fader is provisioned.
        if t.marker_fader is not None:
            stxt = f"{stxt}   F {t.marker_fader:.2f}"
        ext = cr.text_extents(stxt)
        cr.move_to(x + (w - ext.width) / 2, y + 47)
        cr.show_text(stxt)

        # Viewer-only markers omit speed bar (control-context affordance); speed text stays.
        if t.is_controlled:
            bar_w, bar_h = 124, 8
            bar_x = x + (w - bar_w) / 2
            bar_y = y + h - 14

            # Slightly rounded ends (2.5px) to match card chrome without full pill.
            bar_radius = 2.5
            cr.set_source_rgba(0.08, 0.08, 0.1, 0.9)
            draw_rounded_rect(cr, bar_x, bar_y, bar_w, bar_h, bar_radius)
            cr.fill()

            min_spd = state.min_speed if state is not None else 0.1
            max_spd = state.max_speed if state is not None else 3.0
            spd_range = max_spd - min_spd
            ratio = min((speed - min_spd) / spd_range, 1.0) if spd_range > 0 else 0
            ratio = max(ratio, 0.0)
            if ratio > 0.001:
                sr, sg, sb = speed_color(speed - min_spd, spd_range)
                bar_gradient = cairo.LinearGradient(bar_x, bar_y, bar_x + bar_w * ratio, bar_y)
                bar_gradient.add_color_stop_rgb(0, sr * 0.8, sg * 0.8, sb * 0.8)
                bar_gradient.add_color_stop_rgb(1, sr, sg, sb)
                # Clip progress fill to rounded track for rounded corners + crisp edge.
                # try/finally so a fill error can't leave the clip save on the
                # stack – the caller's outer ``restore`` would otherwise pop
                # this instead of its own (misaligned state on the next frame).
                cr.save()
                try:
                    draw_rounded_rect(cr, bar_x, bar_y, bar_w, bar_h, bar_radius)
                    cr.clip()
                    cr.set_source(bar_gradient)
                    cr.rectangle(bar_x, bar_y, bar_w * ratio, bar_h)
                    cr.fill()
                finally:
                    cr.restore()
    finally:
        # Always balance the group stack, even if the body raised: an open
        # group would redirect the caller's "Overlay Error" fallback
        # (video/overlay.py) onto the unpopped group surface, so neither the
        # HUD nor the error banner would reach the screen on that frame.
        if use_group:
            cr.pop_group_to_source()
            cr.paint_with_alpha(0.6)


def draw_virtual_faders(
    renderer: Any,
    cr: Any,
    state: OverlayState,
    h: int,
) -> None:
    """Stack virtual faders on bottom-left; mirroring marker card visual language."""
    if not state.virtual_faders_display:
        return
    card_w = 180.0
    card_h = 28.0
    card_m = 6.0
    # Left edge aligned with info panel for shared left margin.
    card_x = 10.0
    for i, vf in enumerate(state.virtual_faders_display):
        card_y = virtual_fader_card_y(
            i,
            h,
            card_h=card_h,
            card_margin=card_m,
        )
        draw_virtual_fader_card(
            renderer,
            cr,
            card_x,
            card_y,
            card_w,
            card_h,
            vf,
        )


def draw_virtual_fader_card(
    renderer: Any,
    cr: Any,
    x: float,
    y: float,
    w: float,
    h: float,
    vf: VirtualFaderDisplayData,
) -> None:
    """One virtual fader row. Layout:

    - Background panel (gradient + accent border) via the shared
      :func:`draw_panel_background`, so the fader card and the
      marker card share the same chrome.
    - Name on the left, padded inside the panel.
    - Value on the right, two-decimals. Muted colour + the
      "(not picked up)" suffix when the fader's pickup gate
      hasn't engaged yet.
    """
    draw_panel_background(renderer, cr, x, y, w, h, radius=8)

    # baseline-ish; matches the marker card's internal vertical rhythm.
    text_y = y + h * 0.65

    # Measure the value text first so the name budget reflects the
    # space the right-aligned value actually needs. The previous
    # ``w * 0.55`` heuristic could let a long "(not picked up)"
    # suffix overlap the name on narrower cards.
    renderer._set_ui_font(cr, 11)
    if vf.picked_up:
        value_text = f"{vf.value:.2f}"
    else:
        value_text = f"{vf.value:.2f} (not picked up)"
    value_ext = cr.text_extents(value_text)
    # Padding budget: 10 px each side for the card edges + an 8 px
    # gap between name and value so they don't visually fuse.
    name_budget = max(0.0, w - value_ext.width - 10.0 - 10.0 - 8.0)

    # Name on the left – bold so it reads as a label, not a value.
    renderer._set_ui_font(cr, 11, bold=True)
    cr.set_source_rgb(*COLOR_TEXT)
    cr.move_to(x + 10.0, text_y)
    name = renderer._truncate_text_to_width(cr, vf.name, name_budget)
    cr.show_text(name)

    # Value on the right.
    renderer._set_ui_font(cr, 11)
    if vf.picked_up:
        cr.set_source_rgb(*COLOR_TEXT)
    else:
        cr.set_source_rgba(*COLOR_TEXT_MUTED)
    cr.move_to(x + w - 10.0 - value_ext.width, text_y)
    cr.show_text(value_text)


_BUTTON_DISPLAY_NAMES: dict[str, str] = {
    "A": "A",
    "B": "B",
    "X": "X",
    "Y": "Y",
    "LB": "Left Bumper",
    "RB": "Right Bumper",
    "LT": "Left Trigger",
    "RT": "Right Trigger",
    "BACK": "Back / Select",
    "START": "Start / Menu",
    "DPAD_UP": "D-Pad Up",
    "DPAD_DOWN": "D-Pad Down",
    "DPAD_LEFT": "D-Pad Left",
    "DPAD_RIGHT": "D-Pad Right",
}

_BUTTON_SHORT_NAMES: dict[str, str] = {
    "A": "A",
    "B": "B",
    "X": "X",
    "Y": "Y",
    "LB": "LB",
    "RB": "RB",
    "LT": "LT",
    "RT": "RT",
    "BACK": "Back",
    "START": "Start",
    "DPAD_UP": "D-Up",
    "DPAD_DOWN": "D-Down",
    "DPAD_LEFT": "D-Left",
    "DPAD_RIGHT": "D-Right",
}

_HAT_RESULT_NAMES: dict[int, str] = {
    -1: "hat Up",
    -2: "hat Down",
    -3: "hat Left",
    -4: "hat Right",
}


def draw_button_detection_overlay(renderer: Any, cr: Any, state: OverlayState, w: int, h: int) -> None:
    """Draw the button detection wizard as a fullscreen modal."""
    bd = state.button_detection
    if bd is None or not bd.active:
        return

    draw_modal_scrim(cr, w, h, alpha=0.72)

    panel_w = min(w * 0.52, 520.0)
    panel_h = min(h * 0.78, 520.0)
    panel_x, panel_y, panel_w, panel_h = draw_modal_shell(
        renderer,
        cr,
        w,
        h,
        title="BUTTON DETECTION",
        subtitle=f"Step {bd.step + 1} of {bd.total_steps}  \u2013  Press Esc to cancel",
        panel_w=panel_w,
        panel_h=panel_h,
    )

    content_x = panel_x + 24.0
    content_w = panel_w - 48.0
    cursor_y = panel_y + 76.0

    # Prompt: "Press: <LABEL>"
    if bd.current_label:
        renderer._set_ui_font(cr, 14)
        cr.set_source_rgba(*COLOR_TEXT_MUTED)
        prompt_text = "Press button:"
        ext = cr.text_extents(prompt_text)
        cr.move_to(panel_x + (panel_w - ext.width) / 2.0, cursor_y)
        cr.show_text(prompt_text)
        cursor_y += 10.0

        display_name = _BUTTON_DISPLAY_NAMES.get(bd.current_label, bd.current_label)
        renderer._set_ui_font(cr, 42 if h >= 720 else 32, bold=True)
        cr.set_source_rgb(*COLOR_ACCENT)
        ext = cr.text_extents(display_name)
        cr.move_to(panel_x + (panel_w - ext.width) / 2.0 - ext.x_bearing, cursor_y + ext.height)
        cr.show_text(display_name)
        cursor_y += ext.height + 24.0
    else:
        renderer._set_ui_font(cr, 18, bold=True)
        cr.set_source_rgb(*COLOR_OK)
        done_text = "Detection Complete!"
        ext = cr.text_extents(done_text)
        cr.move_to(panel_x + (panel_w - ext.width) / 2.0, cursor_y + 20.0)
        cr.show_text(done_text)
        cursor_y += 50.0

    # Progress bar
    bar_h = 6.0
    bar_x = content_x
    bar_w = content_w
    cr.set_source_rgba(0.08, 0.08, 0.1, 0.9)
    draw_rounded_rect(cr, bar_x, cursor_y, bar_w, bar_h, 3.0)
    cr.fill()
    if bd.total_steps > 0:
        ratio = bd.step / bd.total_steps
        if ratio > 0:
            cr.set_source_rgb(*COLOR_ACCENT)
            draw_rounded_rect(cr, bar_x, cursor_y, bar_w * ratio, bar_h, 3.0)
            cr.fill()
    cursor_y += bar_h + 16.0

    # Completed mappings list
    if bd.completed:
        renderer._set_ui_font(cr, 10.5, bold=True)
        cr.set_source_rgb(*COLOR_ACCENT)
        cr.move_to(content_x, cursor_y)
        cr.show_text("DETECTED:")
        cursor_y += 16.0

        col_w = content_w / 2.0
        row_h = 16.0
        items = list(bd.completed.items())
        for i, (label, raw_idx) in enumerate(items):
            col = i % 2
            row = i // 2
            x = content_x + col * col_w
            y = cursor_y + row * row_h

            short_name = _BUTTON_SHORT_NAMES.get(label, label)
            renderer._set_ui_font(cr, 10, bold=True)
            cr.set_source_rgb(*COLOR_TEXT)
            cr.move_to(x, y)
            cr.show_text(short_name)

            renderer._set_ui_font(cr, 10)
            cr.set_source_rgba(*COLOR_TEXT_MUTED)
            cr.move_to(x + 70.0, y)
            if raw_idx <= -100:
                axis_idx = -100 - raw_idx
                cr.show_text(f"\u2192 axis {axis_idx}")
            elif raw_idx < 0:
                cr.show_text(f"\u2192 {_HAT_RESULT_NAMES.get(raw_idx, f'hat {raw_idx}')}")
            else:
                cr.show_text(f"\u2192 btn {raw_idx}")


# ---------------------------------------------------------------------------
# Network screens
# ---------------------------------------------------------------------------


def draw_pi_network_screen(
    renderer: Any,
    cr: Any,
    state: OverlayState,
    w: int,
    h: int,
) -> None:
    """Apple Network-pane style: sectioned headers + indented rows.

    Headers and display rows are non-selectable; the cursor only lands
    on choice / text / action rows. Rows are drawn directly (not via
    ``draw_selectable_list``) so headers can render bold and unindented
    while data rows sit indented below them.
    """
    net = state.pi_network
    subtitle = "Use \u2191\u2193 to move, Enter to edit, Esc / Back to leave."
    panel_w = min(w * 0.62, 880.0)
    panel_h = min(h * 0.92, 760.0)
    panel_x, panel_y, panel_w, panel_h = draw_modal_shell(
        renderer,
        cr,
        w,
        h,
        title="NETWORK",
        subtitle=subtitle,
        panel_w=panel_w,
        panel_h=panel_h,
    )

    content_x = panel_x + 16.0
    content_w = panel_w - 32.0
    cursor_y = panel_y + 74.0

    if net.banner:
        bar_h = 36.0
        cr.set_source_rgba(0.13, 0.13, 0.17, 0.95)
        draw_rounded_rect(cr, content_x, cursor_y, content_w, bar_h, 8.0)
        cr.fill()
        renderer._set_ui_font(cr, 12, bold=True)
        cr.set_source_rgba(*COLOR_TEXT)
        text = renderer._truncate_text_to_width(cr, net.banner, content_w - 24.0)
        cr.move_to(content_x + 12.0, cursor_y + bar_h / 2.0 + 5.0)
        cr.show_text(text)
        cursor_y += bar_h + 10.0

    # Container panel for the row list.
    list_y = cursor_y
    list_h = max(160.0, panel_y + panel_h - cursor_y - 14.0)
    draw_rounded_rect(cr, content_x, list_y, content_w, list_h, 10.0)
    cr.set_source_rgba(0.0, 0.0, 0.0, 0.26)
    cr.fill()
    cr.set_source_rgba(*COLOR_BORDER_SOFT)
    draw_rounded_rect(cr, content_x, list_y, content_w, list_h, 10.0)
    cr.set_line_width(1.0)
    cr.stroke()

    inner_x = content_x + 14.0
    inner_w = content_w - 28.0
    row_y = list_y + 14.0
    header_h = 24.0
    data_row_h = 24.0
    action_row_h = 30.0
    spacing_after_header = 4.0
    spacing_after_section = 10.0

    selected_idx = net.selected_index
    for idx, row in enumerate(net.rows):
        kind = row.get("kind", "display")
        label = str(row.get("label", ""))
        value = str(row.get("value", ""))

        if kind == "header":
            # Bold section heading; no chrome.
            renderer._set_ui_font(cr, 12, bold=True)
            cr.set_source_rgba(*COLOR_ACCENT)
            cr.move_to(inner_x, row_y + header_h * 0.75)
            cr.show_text(label.upper())
            row_y += header_h + spacing_after_header
            continue

        if kind == "action":
            # Button-styled row.
            is_selected = idx == selected_idx
            box_x = inner_x
            box_w = inner_w
            box_h = action_row_h
            if is_selected:
                cr.set_source_rgba(*COLOR_ACCENT_SOFT)
                draw_rounded_rect(cr, box_x, row_y, box_w, box_h, 7.0)
                cr.fill()
                cr.set_source_rgba(COLOR_ACCENT[0], COLOR_ACCENT[1], COLOR_ACCENT[2], 0.42)
                draw_rounded_rect(cr, box_x, row_y, box_w, box_h, 7.0)
                cr.set_line_width(1.1)
                cr.stroke()
                renderer._set_ui_font(cr, 12, bold=True)
                cr.set_source_rgb(*COLOR_TEXT)
            else:
                cr.set_source_rgba(0.18, 0.18, 0.22, 0.7)
                draw_rounded_rect(cr, box_x, row_y, box_w, box_h, 7.0)
                cr.fill()
                renderer._set_ui_font(cr, 12)
                cr.set_source_rgba(*COLOR_TEXT)
            text = renderer._truncate_text_to_width(cr, label, box_w - 24.0)
            ext = cr.text_extents(text)
            cr.move_to(box_x + (box_w - ext.width) / 2.0, row_y + box_h / 2.0 + 4.0)
            cr.show_text(text)
            row_y += action_row_h + 4.0
            continue

        # choice / text / display rows: "label    value"
        is_selected = idx == selected_idx
        if is_selected:
            cr.set_source_rgba(*COLOR_ACCENT_SOFT)
            draw_rounded_rect(cr, inner_x, row_y - 2.0, inner_w, data_row_h, 6.0)
            cr.fill()
        label_x = inner_x + 14.0
        value_x = inner_x + 180.0
        renderer._set_ui_font(cr, 11.5, bold=is_selected)
        cr.set_source_rgba(*COLOR_TEXT_MUTED if kind == "display" else COLOR_TEXT)
        cr.move_to(label_x, row_y + data_row_h * 0.65)
        cr.show_text(renderer._truncate_text_to_width(cr, label, value_x - label_x - 8.0))
        renderer._set_ui_font(cr, 11.5, bold=is_selected)
        cr.set_source_rgba(*COLOR_TEXT_MUTED if kind == "display" else COLOR_TEXT)
        cr.move_to(value_x, row_y + data_row_h * 0.65)
        cr.show_text(renderer._truncate_text_to_width(cr, value, inner_w - (value_x - inner_x) - 14.0))
        row_y += data_row_h

        # Slight extra gap after the last DNS / lease / router row before
        # the next header to make sections visually distinct.
        next_idx = idx + 1
        if next_idx < len(net.rows) and net.rows[next_idx].get("kind") == "header":
            row_y += spacing_after_section


def draw_pi_network_screen_overlay(
    renderer: Any,
    cr: Any,
    state: OverlayState,
    w: int,
    h: int,
) -> None:
    draw_modal_scrim(cr, w, h, alpha=0.56)
    draw_pi_network_screen(renderer, cr, state, w, h)


def draw_pi_network_iface_picker(
    renderer: Any,
    cr: Any,
    state: OverlayState,
    w: int,
    h: int,
) -> None:
    net = state.pi_network
    draw_selection_menu(
        renderer,
        cr,
        state,
        w,
        h,
        title="SELECT INTERFACE",
        subtitle="Pick an interface, Enter to confirm, Esc to cancel.",
        mode="pi-network-iface",
        items=list(net.iface_picker_items),
        selected_idx=net.iface_picker_selected_index,
        empty_message="No interfaces detected.",
    )


def draw_pi_network_iface_picker_overlay(
    renderer: Any,
    cr: Any,
    state: OverlayState,
    w: int,
    h: int,
) -> None:
    draw_modal_scrim(cr, w, h, alpha=0.56)
    draw_pi_network_iface_picker(renderer, cr, state, w, h)


def draw_pi_network_method_picker(
    renderer: Any,
    cr: Any,
    state: OverlayState,
    w: int,
    h: int,
) -> None:
    net = state.pi_network
    draw_selection_menu(
        renderer,
        cr,
        state,
        w,
        h,
        title="CONFIGURE IPv4",
        subtitle="Pick a method, Enter to confirm, Esc to cancel.",
        mode="pi-network-method",
        items=list(net.method_picker_items),
        selected_idx=net.method_picker_selected_index,
        empty_message="No methods available.",
    )


def draw_pi_network_method_picker_overlay(
    renderer: Any,
    cr: Any,
    state: OverlayState,
    w: int,
    h: int,
) -> None:
    draw_modal_scrim(cr, w, h, alpha=0.56)
    draw_pi_network_method_picker(renderer, cr, state, w, h)


def draw_pi_network_field_edit(
    renderer: Any,
    cr: Any,
    state: OverlayState,
    w: int,
    h: int,
) -> None:
    net = state.pi_network
    title = (net.field_label or "VALUE").upper()
    subtitle = "Type digits and dots only, Enter to save, Esc to cancel."
    panel_w = min(w * 0.62, 720.0)
    panel_h = min(h * 0.30, 240.0)
    panel_x, panel_y, panel_w, panel_h = draw_modal_shell(
        renderer,
        cr,
        w,
        h,
        title=title,
        subtitle=subtitle,
        panel_w=panel_w,
        panel_h=panel_h,
    )

    box_x = panel_x + 24.0
    box_y = panel_y + 84.0
    box_w = panel_w - 48.0
    box_h = 52.0
    cr.set_source_rgba(0.12, 0.12, 0.16, 0.95)
    draw_rounded_rect(cr, box_x, box_y, box_w, box_h, 10.0)
    cr.fill()

    renderer._set_ui_font(cr, 18.0)
    cr.set_source_rgba(*COLOR_TEXT)
    rendered = renderer._truncate_text_to_width(cr, net.field_value, box_w - 40.0)
    text_x = box_x + 16.0
    text_y = box_y + box_h / 2.0 + 6.0
    cr.move_to(text_x, text_y)
    cr.show_text(rendered)

    rendered_ext = cr.text_extents(rendered)
    cursor_x = text_x + rendered_ext.x_advance + 1.0
    cr.set_source_rgba(*COLOR_ACCENT)
    cr.set_line_width(1.6)
    cr.move_to(cursor_x, box_y + 14.0)
    cr.line_to(cursor_x, box_y + box_h - 14.0)
    cr.stroke()


def draw_pi_network_field_edit_overlay(
    renderer: Any,
    cr: Any,
    state: OverlayState,
    w: int,
    h: int,
) -> None:
    draw_modal_scrim(cr, w, h, alpha=0.56)
    draw_pi_network_field_edit(renderer, cr, state, w, h)
