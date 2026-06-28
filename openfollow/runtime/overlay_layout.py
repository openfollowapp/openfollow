# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Pure overlay text + layout helpers for Cairo renderer."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CenteredPanelLayout:
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class SelectableListLayout:
    max_visible: int
    scroll_offset: int


@dataclass(frozen=True)
class BottomLeftInfoPanelLayout:
    panel_x: float
    panel_y: float
    panel_w: float
    panel_h: float
    label_x: float
    value_x: float
    value_max_w: float


# Friendly labels for the 3D Mouse help section (axis -> name, target -> action).
_MOUSE3D_AXIS_NAMES: dict[str, str] = {
    "pan_x": "Pan X",
    "pan_y": "Pan Y",
    "lift": "Lift",
    "pitch": "Pitch",
    "yaw": "Yaw",
    "roll": "Roll",
}
_MOUSE3D_TARGET_LABELS: dict[str, str] = {
    "x": "Move X",
    "y": "Move Y",
    "z": "Move Z",
    "speed": "Speed",
    "fader": "Fader",
}
_MOUSE3D_BTN_LABELS: tuple[tuple[str, str], ...] = (
    ("reset", "Reset marker"),
    ("next_marker", "Marker next"),
    ("prev_marker", "Marker prev"),
    ("speed_up", "Speed +"),
    ("speed_down", "Speed -"),
    ("toggle_help", "Toggle help"),
    ("toggle_zones", "Toggle zones"),
    ("settings", "Settings menu"),
)


# Friendly short names shown in the help overlay for each button code.
_BUTTON_HELP_NAMES: dict[str, str] = {
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


def friendly_button_label(raw: str) -> str:
    """Map button codes like DPAD_RIGHT to short labels like D-Right."""
    return _BUTTON_HELP_NAMES.get(raw, raw)


# Type alias for help sections: list of (title, lines) pairs.
HelpSections = list[tuple[str, list[str]]]


_MOVEMENT_LAYOUT_KEYS: dict[str, tuple[str, str, str, str]] = {
    "wasd": ("w", "a", "s", "d"),
    "ijkl": ("i", "j", "k", "l"),
    "numpad": ("Numpad8", "Numpad4", "Numpad2", "Numpad6"),
}


def _key_label(key: str, fallback: str = "?") -> str:
    if not key:
        return fallback
    # Compact numpad labels: Numpad8 -> Num8.
    if key.startswith("Numpad") and len(key) > len("Numpad"):
        return "Num" + key[len("Numpad") :]
    return key.upper()


# Alias for HUD code.
key_label = _key_label


def _movement_xy_label(labels: dict[str, str]) -> str:
    layout = labels.get("move_layout", "wasd")
    forward, left, back, right = _MOVEMENT_LAYOUT_KEYS.get(layout, _MOVEMENT_LAYOUT_KEYS["wasd"])
    return "/".join(_key_label(p) for p in (forward, left, back, right))


def _movement_lines(labels: dict[str, str]) -> list[str]:
    xy = _movement_xy_label(labels)
    lines = [f"{xy}: Move X/Y"]
    z_up = labels.get("move_z_up", "q")
    if z_up:
        lines.append(f"{_key_label(z_up)}: Z+")
    z_down = labels.get("move_z_down", "v")
    if z_down:
        lines.append(f"{_key_label(z_down)}: Z-")
    return lines


def _pair_line(
    next_lbl: str,
    prev_lbl: str,
    both: str,
    *,
    next_only: str | None = None,
    prev_only: str | None = None,
) -> str | None:
    """Format next/prev pair line, handling empty labels gracefully."""
    if next_lbl and prev_lbl:
        return f"{next_lbl}/{prev_lbl}: {both}"
    if next_lbl:
        return f"{next_lbl}: {next_only if next_only is not None else both}"
    if prev_lbl:
        return f"{prev_lbl}: {prev_only if prev_only is not None else both}"
    return None


def _cycle_line(
    labels: dict[str, str],
    label_prefix: str,
    *,
    default_next: str = "Tab",
) -> str | None:
    """Format the next/prev cycle line or return None if nothing is bound."""
    next_key = labels.get("next_marker", default_next)
    prev_key = labels.get("prev_marker", "")
    next_lbl = _key_label(next_key) if next_key else ""
    prev_lbl = _key_label(prev_key) if prev_key else ""
    return _pair_line(
        next_lbl,
        prev_lbl,
        both=f"{label_prefix} next/prev",
        next_only=f"{label_prefix} next",
        prev_only=f"{label_prefix} prev",
    )


def build_help_sections(
    *,
    mode: str,
    keyboard_connected: bool,
    controller_connected: bool,
    mouse_enabled: bool = False,
    double_click_reset: bool = True,
    scroll_z: bool = True,
    button_labels: dict[str, str] | None = None,
    keyboard_labels: dict[str, str] | None = None,
    mouse3d_connected: bool = False,
    mouse3d_axis_map: dict[str, str] | None = None,
    mouse3d_buttons: dict[str, int] | None = None,
    marker_cycle_enabled: bool = True,
) -> HelpSections:
    """Build titled help-line sections for overlay display."""
    keyboard: list[str] = []
    controller: list[str] = []
    mouse: list[str] = []
    mouse3d: list[str] = []

    bl = button_labels or {}
    kl = keyboard_labels or {}

    def _btn(action: str, default: str) -> str:
        """Get controller binding label; explicit empty means unbound."""
        raw = bl.get(action, default)
        if raw == "":
            return ""
        return friendly_button_label(raw)

    if mode == "normal":
        if keyboard_connected:
            speed_down_raw = kl.get("speed_down", "r")
            speed_up_raw = kl.get("speed_up", "t")
            reset_raw = kl.get("reset", "x")
            help_raw = kl.get("toggle_help", "h")
            settings_key = kl.get("settings", "m")
            cycle = _cycle_line(kl, "Marker")
            speed_line = _pair_line(
                _key_label(speed_down_raw) if speed_down_raw else "",
                _key_label(speed_up_raw) if speed_up_raw else "",
                both="Speed - / +",
                next_only="Speed -",
                prev_only="Speed +",
            )
            keyboard = [
                *_movement_lines(kl),
                *([cycle] if cycle else []),
                *([speed_line] if speed_line else []),
            ]
            if reset_raw:
                keyboard.append(f"{_key_label(reset_raw)}: Reset marker")
            if help_raw:
                keyboard.append(f"{_key_label(help_raw)}: Toggle help")
            zones_key = kl.get("toggle_zones", "")
            if zones_key:
                keyboard.append(f"{_key_label(zones_key)}: Toggle zone overlay")
            if settings_key:
                keyboard.append(f"{_key_label(settings_key)}: Settings menu")
        if controller_connected:
            stick = bl.get("move_xy_stick", "left")
            stick_label = "L-Stick" if stick == "left" else "R-Stick"
            marker_cycle = _pair_line(
                _btn("next_marker", "DPAD_RIGHT"),
                _btn("prev_marker", "DPAD_LEFT"),
                both="Marker next/prev",
                next_only="Marker next",
                prev_only="Marker prev",
            )
            speed_line = _pair_line(
                _btn("speed_down", "LB"),
                _btn("speed_up", "RB"),
                both="Speed - / +",
                next_only="Speed -",
                prev_only="Speed +",
            )
            controller = [f"{stick_label}: Move X/Y"]
            z_up = _btn("move_z_up", "RT")
            if z_up:
                controller.append(f"{z_up}: Z+")
            z_down = _btn("move_z_down", "LT")
            if z_down:
                controller.append(f"{z_down}: Z-")
            if marker_cycle and marker_cycle_enabled:
                controller.append(marker_cycle)
            if speed_line:
                controller.append(speed_line)
            reset_btn = _btn("reset", "X")
            if reset_btn:
                controller.append(f"{reset_btn}: Reset marker")
            help_btn = _btn("toggle_help", "Y")
            if help_btn:
                controller.append(f"{help_btn}: Toggle help")
            settings_btn = _btn("settings", "Back")
            if settings_btn:
                controller.append(f"{settings_btn}: Settings menu")
            if bl.get("toggle_zones"):
                controller.append(f"{_btn('toggle_zones', 'B')}: Toggle zone overlay")
        if mouse_enabled:
            mouse = [
                "Left click: Grab marker",
                "Move: Set X/Y position",
                "Right click: Release",
            ]
            if double_click_reset:
                mouse.append("Double right click: Reset")
            # The scroll wheel can't be polled on macOS, so wheel-Z is
            # unavailable there – the caller passes scroll_z=False to hide it.
            if scroll_z:
                mouse.append("Scroll wheel: Adjust Z")
        if mouse3d_connected:
            axis_map = mouse3d_axis_map or {}
            buttons = mouse3d_buttons or {}
            for axis, axis_name in _MOUSE3D_AXIS_NAMES.items():
                target_label = _MOUSE3D_TARGET_LABELS.get(axis_map.get(axis, "none"))
                if target_label is not None:  # "none" -> not shown
                    mouse3d.append(f"{axis_name}: {target_label}")
            for action, label in _MOUSE3D_BTN_LABELS:
                # Marker next/prev hidden in multi-controller mode (the action
                # is suppressed there, same as the gamepad cycle row).
                if not marker_cycle_enabled and action in ("next_marker", "prev_marker"):
                    continue
                idx = buttons.get(action, -1)
                if idx >= 0:
                    mouse3d.append(f"Btn {idx}: {label}")
    elif mode == "source-selection":
        if keyboard_connected:
            keyboard = [
                "Arrow Up/Down: Select source",
                "Enter: Confirm source",
                "Esc: Cancel source menu",
            ]
        if controller_connected:
            controller = [
                "D-Pad Up/Down: Select source",
                f"{_btn('menu_confirm', 'A')}: Confirm source",
                f"{_btn('menu_cancel', 'B')}: Cancel source menu",
            ]
    elif mode == "source-type-selection":
        if keyboard_connected:
            keyboard = [
                "Arrow Up/Down: Select source type",
                "Enter: Confirm source type",
                "Esc: Cancel source type menu",
            ]
        if controller_connected:
            controller = [
                "D-Pad Up/Down: Select source type",
                f"{_btn('menu_confirm', 'A')}: Confirm source type",
                f"{_btn('menu_cancel', 'B')}: Cancel source type menu",
            ]
    elif mode == "iface-selection":
        if keyboard_connected:
            keyboard = [
                "Arrow Up/Down: Select interface",
                "Enter: Apply interface (live)",
                "Esc: Cancel interface menu",
            ]
        if controller_connected:
            controller = [
                "D-Pad Up/Down: Select interface",
                f"{_btn('menu_confirm', 'A')}: Apply interface (live)",
                f"{_btn('menu_cancel', 'B')}: Cancel interface menu",
            ]
    elif mode == "button-detection":
        if keyboard_connected:
            keyboard = [
                "Esc: Cancel detection",
            ]
        if controller_connected:
            controller = [
                "Press the prompted button",
            ]
    elif mode == "settings":
        if keyboard_connected:
            keyboard = [
                "Arrow Up/Down: Navigate",
                "Enter: Confirm",
                "Esc: Cancel menu",
            ]
        if controller_connected:
            controller = [
                "D-Pad Up/Down: Navigate",
                f"{_btn('menu_confirm', 'A')}: Confirm",
                f"{_btn('menu_cancel', 'B')}: Cancel menu",
            ]
    sections: HelpSections = []
    if keyboard:
        sections.append(("Keyboard", keyboard))
    if controller:
        sections.append(("Controller", controller))
    if mouse:
        sections.append(("Mouse", mouse))
    if mouse3d:
        sections.append(("3D Mouse", mouse3d))
    return sections


def help_sections_height(sections: HelpSections) -> float:
    """Calculate pixel height required by a list of help sections."""
    if not sections:
        return 0.0
    title_h = 13.0
    line_h = 14.0
    section_gap = 8.0
    return sum(title_h + line_h * len(lines) for _, lines in sections) + section_gap * (len(sections) - 1)


def format_source_text(video_source_type: str, source_label: str, max_len: int = 38) -> str:
    if not source_label:
        return "Not configured"
    src_type = video_source_type.upper()
    src_name = source_label
    if src_name.upper().startswith(src_type + " "):
        src_name = src_name[len(src_type) + 1 :]
    if len(src_name) > max_len:
        src_name = src_name[: max_len - 3] + "..."
    return f"{src_type}: {src_name}"


def build_system_stats_text(cpu_percent: float, ram_percent: float, temperature: float | None) -> str:
    parts = [
        f"CPU: {cpu_percent:4.1f}%",
        f"RAM: {ram_percent:4.1f}%",
    ]
    if temperature is not None:
        parts.append(f"{temperature:4.1f}\u00b0C")
    return "  |  ".join(parts)


def centered_panel_layout(
    frame_width: int,
    frame_height: int,
    panel_w: float,
    panel_h: float,
    max_frame_padding: float = 20.0,
) -> CenteredPanelLayout:
    width = min(panel_w, frame_width - max_frame_padding)
    height = min(panel_h, frame_height - max_frame_padding)
    x = (frame_width - width) / 2.0
    y = (frame_height - height) / 2.0
    return CenteredPanelLayout(x=x, y=y, width=width, height=height)


def selectable_list_layout(
    item_count: int,
    selected_idx: int,
    list_height: float,
    item_h: float = 30.0,
    vertical_padding: float = 14.0,
) -> SelectableListLayout:
    if item_count <= 0:
        return SelectableListLayout(max_visible=0, scroll_offset=0)

    max_visible = max(1, int((list_height - vertical_padding) // item_h))
    max_visible = min(max_visible, item_count)
    scroll_offset = 0
    if selected_idx >= max_visible:
        scroll_offset = selected_idx - max_visible + 1
    return SelectableListLayout(max_visible=max_visible, scroll_offset=scroll_offset)


def bottom_left_info_panel_layout(
    frame_width: int,
    frame_height: int,
    *,
    label_w: float,
    value_w: float,
    side_padding: float,
    panel_h: float,
    label_value_gap: float = 8.0,
    min_panel_w: float = 290.0,
    min_value_w: float = 20.0,
) -> BottomLeftInfoPanelLayout:
    panel_w = max(min_panel_w, side_padding * 2 + label_w + label_value_gap + value_w)
    panel_w = min(panel_w, frame_width - 20.0)
    panel_x = 10.0
    panel_y = frame_height - panel_h - 10.0
    label_x = panel_x + side_padding
    value_x = label_x + label_w + label_value_gap
    value_max_w = max(min_value_w, panel_x + panel_w - side_padding - value_x)
    return BottomLeftInfoPanelLayout(
        panel_x=panel_x,
        panel_y=panel_y,
        panel_w=panel_w,
        panel_h=panel_h,
        label_x=label_x,
        value_x=value_x,
        value_max_w=value_max_w,
    )


def marker_card_y(
    index: int,
    frame_height: int,
    card_h: float = 64.0,
    card_margin: float = 8.0,
    bottom_padding: float = 14.0,
) -> float:
    return frame_height - bottom_padding - card_h - index * (card_h + card_margin)


def virtual_fader_card_y(
    index: int,
    frame_height: int,
    card_h: float = 28.0,
    card_margin: float = 6.0,
    bottom_padding: float = 98.0,
) -> float:
    """Y position for the bottom-left virtual fader stack;
    mirrors :func:`marker_card_y` stacking bottom-up.

    Default ``bottom_padding`` of 98 px clears the bottom-left info
    panel (74 px tall – three rows: IP / Video Source / Station – +
    14 px gap + 10 px bottom margin) so the lowest fader card sits
    just above the info box without overlap. The ``index`` is the position within the
    visible stack (0 = bottom-most), not the fader's runtime
    index – the renderer enumerates only faders with
    ``show_on_display`` set, in fader-number order, so the
    lowest stack position naturally maps to the lowest enabled
    fader number.
    """
    return frame_height - bottom_padding - card_h - index * (card_h + card_margin)
