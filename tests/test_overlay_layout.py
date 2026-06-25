# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for :mod:`openfollow.runtime.overlay_layout`: help-section assembly,
panel/card geometry helpers, and key-label fallbacks across HUD modes."""

from __future__ import annotations

import pytest

from openfollow.runtime.overlay_layout import (
    bottom_left_info_panel_layout,
    build_help_sections,
    centered_panel_layout,
    format_source_text,
    help_sections_height,
    marker_card_y,
    selectable_list_layout,
    virtual_fader_card_y,
)

pytestmark = pytest.mark.unit


def test_build_help_sections_normal_mode_advertises_settings_menu() -> None:
    sections = build_help_sections(
        mode="normal",
        keyboard_connected=True,
        controller_connected=True,
        keyboard_labels={"settings": "m"},
        button_labels={"settings": "BACK"},
    )

    section_dict = dict(sections)
    keyboard = section_dict["Keyboard"]
    controller = section_dict["Controller"]
    assert any(line == "M: Settings menu" for line in keyboard)
    assert any(line == "Back: Settings menu" for line in controller)
    # Legacy direct shortcuts must be gone now that the Settings menu owns the entry points.
    assert not any("Network interface" in line for line in keyboard)
    assert not any(line.startswith("C: Calibration") for line in keyboard)
    assert not any("Button detection" in line for line in keyboard)
    assert not any("Source menu" in line for line in keyboard)
    assert not any("Source menu" in line for line in controller)
    assert not any("Exit app" in line for line in keyboard)
    assert not any(line.startswith("Start:") for line in controller)


def test_build_help_sections_normal_mode_keyboard_without_settings_key_omits_line() -> None:
    sections = build_help_sections(
        mode="normal",
        keyboard_connected=True,
        controller_connected=False,
        keyboard_labels={"settings": ""},
    )
    keyboard = dict(sections)["Keyboard"]
    assert not any("Settings menu" in line for line in keyboard)


def test_build_help_sections_mouse_uses_grab_release_wording() -> None:
    mouse = dict(
        build_help_sections(
            mode="normal",
            keyboard_connected=False,
            controller_connected=False,
            mouse_enabled=True,
        )
    )["Mouse"]
    assert any("Left click: Grab marker" in line for line in mouse)
    assert any("Right click: Release" in line for line in mouse)
    # The pre-grab-rework wording is gone.
    assert not any("Activate" in line for line in mouse)
    assert not any("Deactivate" in line for line in mouse)


def test_build_help_sections_mouse_includes_reset_by_default() -> None:
    mouse = dict(
        build_help_sections(
            mode="normal",
            keyboard_connected=False,
            controller_connected=False,
            mouse_enabled=True,
        )
    )["Mouse"]
    assert any("Double right click: Reset" in line for line in mouse)


def test_build_help_sections_mouse_omits_reset_when_disabled() -> None:
    mouse = dict(
        build_help_sections(
            mode="normal",
            keyboard_connected=False,
            controller_connected=False,
            mouse_enabled=True,
            double_click_reset=False,
        )
    )["Mouse"]
    assert not any("Reset" in line for line in mouse)


def test_build_help_sections_mouse_includes_scroll_z_by_default() -> None:
    mouse = dict(
        build_help_sections(
            mode="normal",
            keyboard_connected=False,
            controller_connected=False,
            mouse_enabled=True,
        )
    )["Mouse"]
    assert any("Scroll wheel: Adjust Z" in line for line in mouse)


def test_build_help_sections_mouse_omits_scroll_z_when_unavailable() -> None:
    # macOS can't poll the scroll wheel, so the caller passes scroll_z=False.
    mouse = dict(
        build_help_sections(
            mode="normal",
            keyboard_connected=False,
            controller_connected=False,
            mouse_enabled=True,
            scroll_z=False,
        )
    )["Mouse"]
    assert not any("Scroll wheel" in line for line in mouse)
    assert any("Move: Set X/Y position" in line for line in mouse)  # other lines remain


def test_build_help_sections_reflects_keyboard_labels() -> None:
    sections = build_help_sections(
        mode="normal",
        keyboard_connected=True,
        controller_connected=False,
        keyboard_labels={
            "move_layout": "wasd",
            "move_z_up": "q",
            "move_z_down": "v",
            "reset": "x",
            "toggle_help": "h",
            "speed_down": "r",
            "speed_up": "t",
        },
    )
    keyboard = dict(sections)["Keyboard"]
    assert keyboard[0] == "W/A/S/D: Move X/Y"
    assert keyboard[1] == "Q: Z+"
    assert keyboard[2] == "V: Z-"
    assert keyboard[4] == "R/T: Speed - / +"
    assert keyboard[5] == "X: Reset marker"


def test_build_help_sections_ijkl_layout() -> None:
    sections = build_help_sections(
        mode="normal",
        keyboard_connected=True,
        controller_connected=False,
        keyboard_labels={"move_layout": "ijkl"},
    )
    assert dict(sections)["Keyboard"][0] == "I/J/K/L: Move X/Y"


def test_build_help_sections_numpad_layout_uses_short_labels() -> None:
    sections = build_help_sections(
        mode="normal",
        keyboard_connected=True,
        controller_connected=False,
        keyboard_labels={"move_layout": "numpad"},
    )
    assert dict(sections)["Keyboard"][0] == "Num8/Num4/Num2/Num6: Move X/Y"


def test_build_help_sections_falls_back_to_defaults_without_labels() -> None:
    sections = build_help_sections(
        mode="normal",
        keyboard_connected=True,
        controller_connected=False,
    )
    keyboard = dict(sections)["Keyboard"]
    assert keyboard[0] == "W/A/S/D: Move X/Y"
    assert keyboard[1] == "Q: Z+"
    assert keyboard[2] == "V: Z-"


def test_build_help_sections_cycle_line_uses_next_and_prev_keys() -> None:
    sections = build_help_sections(
        mode="normal",
        keyboard_connected=True,
        controller_connected=False,
        keyboard_labels={"next_marker": "Tab", "prev_marker": "p"},
    )
    keyboard = dict(sections)["Keyboard"]
    assert keyboard[3] == "TAB/P: Marker next/prev"


def test_build_help_sections_cycle_line_omitted_when_both_unbound() -> None:
    sections = build_help_sections(
        mode="normal",
        keyboard_connected=True,
        controller_connected=False,
        keyboard_labels={"next_marker": "", "prev_marker": ""},
    )
    keyboard = dict(sections)["Keyboard"]
    assert not any("Marker next" in line or "Marker prev" in line for line in keyboard)


def test_build_help_sections_settings_mode_lists_navigation() -> None:
    sections = build_help_sections(
        mode="settings",
        keyboard_connected=True,
        controller_connected=True,
        button_labels={"menu_confirm": "A", "menu_cancel": "B"},
    )
    section_dict = dict(sections)
    keyboard = section_dict["Keyboard"]
    controller = section_dict["Controller"]
    assert any("Navigate" in line for line in keyboard)
    assert any(line.startswith("Enter: Confirm") for line in keyboard)
    assert any("Esc: Cancel" in line for line in keyboard)
    assert any("Navigate" in line for line in controller)
    assert any(line.startswith("A: Confirm") for line in controller)
    assert any(line.startswith("B: Cancel") for line in controller)


def test_help_sections_height_matches_section_math() -> None:
    sections = [("Keyboard", ["A", "B"]), ("Controller", ["C"])]
    height = help_sections_height(sections)
    assert height == pytest.approx(76.0)
    assert help_sections_height([]) == 0.0


def test_format_source_text_strips_type_prefix_and_truncates() -> None:
    assert format_source_text("srt", "SRT srt://0.0.0.0:5000") == "SRT: srt://0.0.0.0:5000"
    assert format_source_text("ndi", "", max_len=12) == "Not configured"
    assert format_source_text("ndi", "VeryLongSourceName", max_len=8).endswith("...")


def test_centered_panel_layout_clamps_to_frame_bounds() -> None:
    layout = centered_panel_layout(100, 80, panel_w=200.0, panel_h=40.0)
    assert layout.width == 80.0
    assert layout.height == 40.0
    assert layout.x == 10.0
    assert layout.y == 20.0


def test_selectable_list_layout_scroll_window() -> None:
    layout = selectable_list_layout(item_count=10, selected_idx=8, list_height=104.0)
    assert layout.max_visible == 3
    assert layout.scroll_offset == 6
    assert selectable_list_layout(item_count=0, selected_idx=0, list_height=100.0).max_visible == 0


def test_bottom_left_info_panel_layout_calculations() -> None:
    layout = bottom_left_info_panel_layout(
        frame_width=800,
        frame_height=600,
        label_w=100.0,
        value_w=300.0,
        side_padding=12.0,
        panel_h=54.0,
    )

    assert layout.panel_w == 432.0
    assert layout.panel_y == 536.0
    assert layout.value_x == 130.0
    assert layout.value_max_w == 300.0


def test_marker_card_y_stacking() -> None:
    assert marker_card_y(0, frame_height=720) == 642.0
    assert marker_card_y(2, frame_height=720) == 498.0


def test_virtual_fader_card_y_stacks_bottom_up() -> None:
    """Lowest stack index sits at highest Y (bottom edge); each index
    is exactly ``card_h + card_margin`` pixels higher than the next."""
    # Default: card_h=28, card_margin=6, bottom_padding=98.
    # Index 0 → 720 - 98 - 28 = 594.
    assert virtual_fader_card_y(0, frame_height=720) == 594.0
    # Index 1 → 594 - (28 + 6) = 560.
    assert virtual_fader_card_y(1, frame_height=720) == 560.0
    # Index 2 → 560 - 34 = 526.
    assert virtual_fader_card_y(2, frame_height=720) == 526.0


def test_virtual_fader_card_y_clears_bottom_left_info_panel() -> None:
    """The default ``bottom_padding`` is set so the lowest fader
    card sits above the bottom-left info panel – preserving the
    layout invariant."""
    # Derive the info-panel reserved height from the layout helper rather
    # than hard-coding it, so a panel-height change can't mask a regression.
    from openfollow.runtime.overlay_layout import (
        bottom_left_info_panel_layout,
    )

    info_panel_h = 54.0
    info_layout = bottom_left_info_panel_layout(
        frame_width=1280,
        frame_height=720,
        label_w=80.0,
        value_w=120.0,
        side_padding=12.0,
        panel_h=info_panel_h,
    )
    reserved = 720 - info_layout.panel_y  # info_panel_h + bottom margin
    bottom_y = (
        virtual_fader_card_y(0, frame_height=720) + 28.0  # card_h
    )
    assert 720 - bottom_y >= reserved


# ---------------------------------------------------------------------------
# Key label fallbacks, controller-only modes, and selection/detection arms
# ---------------------------------------------------------------------------


def test_key_label_returns_fallback_for_empty_string() -> None:
    """Direct test of the empty-key fallback used when an action is
    unbound – saves callers from rendering a literal blank cell."""
    from openfollow.runtime.overlay_layout import _key_label

    assert _key_label("") == "?"
    assert _key_label("", fallback="–") == "–"


def test_movement_lines_omit_z_up_when_unbound() -> None:
    from openfollow.runtime.overlay_layout import _movement_lines

    lines = _movement_lines({"move_z_up": ""})
    assert not any("Z+" in line for line in lines)


def test_movement_lines_omit_z_down_when_unbound() -> None:
    from openfollow.runtime.overlay_layout import _movement_lines

    lines = _movement_lines({"move_z_down": ""})
    assert not any("Z-" in line for line in lines)


def test_pair_line_returns_prev_only_form_when_only_prev_bound() -> None:
    """The next/prev label formatter must degrade gracefully when only
    one side is bound. Without this, a partially-cleared binding would
    surface as a malformed `"/X: Marker prev"` line."""
    from openfollow.runtime.overlay_layout import _pair_line

    line = _pair_line(
        "",
        "PREV",
        both="Marker next/prev",
        next_only="Marker next",
        prev_only="Marker prev",
    )
    assert line == "PREV: Marker prev"


def test_pair_line_returns_none_when_both_sides_unbound() -> None:
    from openfollow.runtime.overlay_layout import _pair_line

    assert _pair_line("", "", both="ignored") is None


def test_build_help_sections_normal_drops_lines_for_empty_keyboard_actions() -> None:
    sections = build_help_sections(
        mode="normal",
        keyboard_connected=True,
        controller_connected=False,
        keyboard_labels={
            "reset": "",
            "toggle_help": "",
            "settings": "",
            "speed_down": "",
            "speed_up": "",
            "next_marker": "",
            "prev_marker": "",
            "toggle_zones": "",
        },
    )
    keyboard = next(lines for title, lines in sections if title == "Keyboard")
    assert not any("Reset marker" in line for line in keyboard)
    assert not any("Toggle help" in line for line in keyboard)
    assert not any("Settings menu" in line for line in keyboard)
    # Movement still rendered (non-empty defaults).
    assert any("Move X/Y" in line for line in keyboard)


def test_build_help_sections_normal_includes_zone_overlay_line_when_bound() -> None:
    """The toggle-zones binding only renders when the operator actually
    bound it (default is empty); covers the zones-key truthy branch."""
    sections = build_help_sections(
        mode="normal",
        keyboard_connected=True,
        controller_connected=False,
        keyboard_labels={"toggle_zones": "z"},
    )
    keyboard = next(lines for title, lines in sections if title == "Keyboard")
    assert any(line == "Z: Toggle zone overlay" for line in keyboard)


def test_build_help_sections_normal_btn_returns_empty_when_action_explicitly_cleared() -> None:
    """`_btn` must return "" when the operator explicitly cleared a
    controller binding (raw=="")  – the calling code then drops the line.
    Without this, the help overlay would render a literal `"": Reset
    marker` row."""
    sections = build_help_sections(
        mode="normal",
        keyboard_connected=False,
        controller_connected=True,
        button_labels={
            "reset": "",
            "toggle_help": "",
            "settings": "",
            "next_marker": "",
            "prev_marker": "",
            "speed_down": "",
            "speed_up": "",
            "move_z_up": "",
            "move_z_down": "",
        },
    )
    controller = next(lines for title, lines in sections if title == "Controller")
    assert not any("Reset marker" in line for line in controller)
    assert not any("Toggle help" in line for line in controller)
    assert not any("Settings menu" in line for line in controller)
    # The L-Stick movement line is unconditional.
    assert any("Move X/Y" in line for line in controller)


def test_build_help_sections_normal_includes_mouse_section_when_enabled() -> None:
    """Mouse section appears only when `mouse_enabled=True` – without
    that gate every help overlay would advertise scroll-to-Z even on
    pure-controller setups."""
    sections = build_help_sections(
        mode="normal",
        keyboard_connected=False,
        controller_connected=False,
        mouse_enabled=True,
    )
    titles = [title for title, _ in sections]
    assert "Mouse" in titles
    mouse = next(lines for title, lines in sections if title == "Mouse")
    assert any("Scroll wheel" in line for line in mouse)


def test_build_help_sections_normal_controller_renders_zone_overlay_line_only_when_bound() -> None:
    """Controller toggle-zones line only renders when the operator
    explicitly bound it – closes the truthy branch on `bl.get("toggle_zones")`."""
    sections = build_help_sections(
        mode="normal",
        keyboard_connected=False,
        controller_connected=True,
        button_labels={"toggle_zones": "B"},
    )
    controller = next(lines for title, lines in sections if title == "Controller")
    assert any("Toggle zone overlay" in line for line in controller)


def test_build_help_sections_source_selection_controller_lists_dpad_navigation() -> None:
    """Source-selection mode controller branch. Without this, controllers
    with no keyboard wouldn't see the menu instructions."""
    sections = build_help_sections(
        mode="source-selection",
        keyboard_connected=False,
        controller_connected=True,
    )
    controller = next(lines for title, lines in sections if title == "Controller")
    assert any("D-Pad Up/Down" in line for line in controller)
    assert any("Confirm source" in line for line in controller)


def test_build_help_sections_source_type_selection_keyboard_lists_navigation() -> None:
    """Source-type picker keyboard arm covers the new
    ``source-type-selection`` mode in draw_selection_menu."""
    sections = build_help_sections(
        mode="source-type-selection",
        keyboard_connected=True,
        controller_connected=False,
    )
    keyboard = next(lines for title, lines in sections if title == "Keyboard")
    assert any("Select source type" in line for line in keyboard)
    assert any("Confirm source type" in line for line in keyboard)
    assert any("Cancel source type menu" in line for line in keyboard)


def test_build_help_sections_source_type_selection_controller_lists_dpad() -> None:
    """Source-type picker controller arm."""
    sections = build_help_sections(
        mode="source-type-selection",
        keyboard_connected=False,
        controller_connected=True,
    )
    controller = next(lines for title, lines in sections if title == "Controller")
    assert any("D-Pad Up/Down" in line for line in controller)
    assert any("Confirm source type" in line for line in controller)
    assert any("Cancel source type menu" in line for line in controller)


def test_build_help_sections_iface_selection_controller_lists_dpad_navigation() -> None:
    """Iface-selection mode controller branch."""
    sections = build_help_sections(
        mode="iface-selection",
        keyboard_connected=False,
        controller_connected=True,
    )
    controller = next(lines for title, lines in sections if title == "Controller")
    assert any("D-Pad Up/Down" in line for line in controller)
    assert any("Apply interface" in line for line in controller)


def test_build_help_sections_button_detection_keyboard_only_lists_escape() -> None:
    """Button-detection mode keyboard branch."""
    sections = build_help_sections(
        mode="button-detection",
        keyboard_connected=True,
        controller_connected=False,
    )
    keyboard = next(lines for title, lines in sections if title == "Keyboard")
    assert any("Cancel detection" in line for line in keyboard)


def test_build_help_sections_button_detection_controller_prompts_user() -> None:
    """Button-detection mode controller branch."""
    sections = build_help_sections(
        mode="button-detection",
        keyboard_connected=False,
        controller_connected=True,
    )
    controller = next(lines for title, lines in sections if title == "Controller")
    assert any("Press the prompted button" in line for line in controller)


def test_build_help_sections_unknown_mode_returns_empty() -> None:
    """An unknown mode falls through every elif and returns an empty
    section list – closes the elif-chain → exit branch."""
    sections = build_help_sections(
        mode="not-a-real-mode",
        keyboard_connected=True,
        controller_connected=True,
    )
    assert sections == []


@pytest.mark.parametrize(
    "mode,expected_substring",
    [
        ("source-selection", "Confirm source"),
        ("iface-selection", "Apply interface"),
        ("settings", "Confirm"),
    ],
)
def test_build_help_sections_keyboard_only_renders_keyboard_section(
    mode: str,
    expected_substring: str,
) -> None:
    """Each modal mode renders a keyboard-only section when no controller
    is connected. Without these tests the keyboard-only arms (and the
    branches that skip the controller arm) stay unexercised."""
    sections = build_help_sections(
        mode=mode,
        keyboard_connected=True,
        controller_connected=False,
    )
    titles = [title for title, _ in sections]
    assert "Keyboard" in titles
    assert "Controller" not in titles
    keyboard = next(lines for title, lines in sections if title == "Keyboard")
    assert any(expected_substring in line for line in keyboard)


def test_build_system_stats_text_includes_temperature_when_provided() -> None:
    """System stats line surfaces temperature only when a sensor reading
    is available – covers the `if temperature is not None` truthy
    branch (head-end of the helper)."""
    from openfollow.runtime.overlay_layout import build_system_stats_text

    text = build_system_stats_text(cpu_percent=12.3, ram_percent=45.6, temperature=67.8)
    assert "12.3%" in text
    assert "45.6%" in text
    assert "67.8" in text and "°C" in text


def test_build_system_stats_text_omits_temperature_when_none() -> None:
    """The complementary `temperature is None` arm – used on hosts
    without a thermal sensor (most macOS dev boxes)."""
    from openfollow.runtime.overlay_layout import build_system_stats_text

    text = build_system_stats_text(cpu_percent=10.0, ram_percent=20.0, temperature=None)
    assert "°C" not in text
    assert "CPU" in text and "RAM" in text


def test_build_help_sections_settings_mode_no_devices_returns_empty() -> None:
    """Settings mode with neither keyboard nor controller connected
    returns empty sections – covers the partial branches that skip
    both per-device blocks."""
    sections = build_help_sections(
        mode="settings",
        keyboard_connected=False,
        controller_connected=False,
    )
    assert sections == []


def test_selectable_list_layout_no_scroll_when_selected_within_visible() -> None:
    from openfollow.runtime.overlay_layout import selectable_list_layout

    layout = selectable_list_layout(
        item_count=10,
        selected_idx=1,
        list_height=200.0,
    )
    # `max_visible` will be ≥ 2 with this height, so selected_idx=1 fits.
    assert layout.scroll_offset == 0
    assert layout.max_visible >= 2
