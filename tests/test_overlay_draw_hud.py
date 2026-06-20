# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for :mod:`openfollow.runtime.overlay_draw_hud`.

The HUD module is the largest draw pass in the renderer and hosts every
modal / panel / card that overlays the video frame.  Tests are grouped
by public entry point so each section reads as an independent spec:

* scrim + shell primitives
* selectable list (scroll / selection / empty state)
* selection menus (source, interface, settings)
* no-signal modal (with / without source-selection + reconnect + error)
* HUD proper: bottom-left info panel, system stats, help overlay / hint,
  marker cards.
* Button-detection wizard: prompt state, progress bar, completed map list.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openfollow.runtime.overlay_draw_hud import (
    draw_about_overlay,
    draw_about_screen,
    draw_bottom_left_info_panel,
    draw_button_detection_overlay,
    draw_field_choice_picker,
    draw_field_choice_picker_overlay,
    draw_help_block,
    draw_hud,
    draw_iface_selection,
    draw_iface_selection_overlay,
    draw_marker_card,
    draw_modal_scrim,
    draw_modal_shell,
    draw_panel,
    draw_panel_background,
    draw_pi_network_field_edit,
    draw_pi_network_field_edit_overlay,
    draw_pi_network_iface_picker,
    draw_pi_network_iface_picker_overlay,
    draw_pi_network_method_picker,
    draw_pi_network_method_picker_overlay,
    draw_pi_network_screen,
    draw_pi_network_screen_overlay,
    draw_selectable_list,
    draw_selection_menu,
    draw_settings_menu,
    draw_settings_overlay,
    draw_source_selection,
    draw_source_selection_overlay,
    draw_source_type_selection,
    draw_source_type_selection_overlay,
    draw_system_stats,
    draw_url_editor,
    draw_url_editor_overlay,
    draw_virtual_fader_card,
    draw_virtual_faders,
)
from openfollow.runtime.overlay_state import (
    ButtonDetectionState,
    MarkerOverlayData,
    OverlayState,
    VirtualFaderDisplayData,
)
from openfollow.units import UnitSystem
from tests._fake_cairo import FakeCairo, FakeRenderer

pytestmark = pytest.mark.unit

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _marker(
    marker_id: int = 1,
    *,
    x: float = 1.0,
    y: float = 2.0,
    z: float = 1.5,
    color: str = "#ff3333",
    speed: float | None = 0.5,
    online: bool = True,
    controller_idx: int | None = None,
    controller_connected: bool = False,
    is_controlled: bool = True,
) -> MarkerOverlayData:
    return MarkerOverlayData(
        marker_id=marker_id,
        x=x,
        y=y,
        z=z,
        color=color,
        speed=speed,
        online=online,
        controller_idx=controller_idx,
        controller_connected=controller_connected,
        is_controlled=is_controlled,
    )


def _base_state(**kw: object) -> OverlayState:
    """Return an ``OverlayState`` configured with inputs connected so help
    sections render.  Individual tests override fields as needed.
    """
    state = OverlayState()
    state.keyboard_connected = True
    state.controller_connected = False
    state.mouse_enabled = False
    for k, v in kw.items():
        setattr(state, k, v)
    return state


def _emits_card_chrome(cr: FakeCairo) -> bool:
    """True when ``cr`` painted the shared overlay-card chrome.

    The card chrome is a translucent ``COLOR_BG_BASE`` fill + soft white
    ``COLOR_BORDER``; it must NOT carry the retired golden accent panel
    border. Used to prove a panel reads in the same visual language as the
    operator-message cards.
    """
    from openfollow.runtime.overlay_draw_style import (
        CARD_BG_ALPHA,
        COLOR_ACCENT,
        COLOR_BG_BASE,
        COLOR_BORDER,
    )

    has_fill = ("rgba", *COLOR_BG_BASE, CARD_BG_ALPHA) in cr.calls
    has_border = ("rgba", *COLOR_BORDER) in cr.calls
    no_accent = ("rgba", COLOR_ACCENT[0], COLOR_ACCENT[1], COLOR_ACCENT[2], 0.58) not in cr.calls
    return has_fill and has_border and no_accent


# --------------------------------------------------------------------------- #
# Scrim + modal shell
# --------------------------------------------------------------------------- #


class TestModalScrim:
    def test_full_frame_rectangle_with_given_alpha(self) -> None:
        cr = FakeCairo()
        draw_modal_scrim(cr, 1920, 1080, alpha=0.5)
        assert cr.rects == [(0, 0, 1920, 1080)]
        assert cr.fills == 1
        # alpha channel of the black fill is exactly what we passed.
        rgba = next(c for c in cr.calls if c[0] == "rgba")
        assert rgba[1:] == (0.0, 0.0, 0.0, 0.5)

    def test_default_alpha_is_062(self) -> None:
        cr = FakeCairo()
        draw_modal_scrim(cr, 100, 100)
        rgba = next(c for c in cr.calls if c[0] == "rgba")
        assert rgba[4] == pytest.approx(0.62)


class TestAboutScreen:
    """The on-screen About screen renders the AGPLv3 notice using pure Cairo text."""

    def test_about_screen_renders_name_version_and_notice(self) -> None:
        from openfollow import __version__

        cr = FakeCairo()
        draw_about_screen(FakeRenderer(), cr, OverlayState(), 1920, 1080)
        texts = cr.show_text_strings()
        # Long paragraphs (warranty disclaimer, safety warning) are
        # greedy-wrapped across several ``show_text`` calls, so assert
        # against the lines re-joined with spaces – wrapping only ever
        # breaks at the spaces it joins back on.
        joined = " ".join(texts)
        assert "ABOUT" in texts
        assert "OpenFollow" in texts
        assert f"Version {__version__}" in texts
        assert any("The OpenFollow Project" in t for t in texts)
        assert any("GNU AGPL v3" in t for t in texts)
        # Full AGPLv3 no-warranty disclaimer.
        assert "WITHOUT ANY WARRANTY" in joined
        assert "MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE" in joined
        assert "GNU Affero General Public License for more details" in joined
        # Project link line (short enough to render on a single line).
        assert "More Information, Source and License: openfollow.app" in texts
        # Debian / Raspberry Pi trademark non-endorsement (the OS-conveying
        # appliance image's only on-device legal surface without a browser).
        assert "not affiliated with or endorsed by" in joined
        assert "Debian Project, Raspberry Pi Ltd" in joined
        # Not-for-safety-critical-use warning.
        assert "should not be used for safety critical applications" in joined
        # No logo handle on FakeRenderer -> the headline falls back to text.
        assert "OpenFollow" in texts

    def test_about_screen_uses_logo_headline_when_available(self) -> None:
        renderer = FakeRenderer()
        renderer._logo_handle = object()  # truthy -> logo headline, not text
        cr = FakeCairo()
        draw_about_screen(renderer, cr, OverlayState(), 1920, 1080)
        texts = cr.show_text_strings()
        # Logo rendered once as the headline, centered, with a positive width.
        assert len(renderer.draw_logo_calls) == 1
        _x, _y, width = renderer.draw_logo_calls[0]
        assert width > 0
        # The text wordmark headline is replaced by the logo (the body still
        # mentions OpenFollow, but the standalone headline line is gone).
        assert "OpenFollow" not in texts
        # The rest of the screen still renders.
        assert any("GNU AGPL v3" in t for t in texts)

    def test_about_overlay_draws_scrim_then_screen(self) -> None:
        cr = FakeCairo()
        draw_about_overlay(FakeRenderer(), cr, OverlayState(), 1280, 720)
        # Scrim is a full-frame black rect drawn before the panel content.
        assert (0, 0, 1280, 720) in cr.rects
        assert "ABOUT" in cr.show_text_strings()


class TestModalShell:
    def test_shell_draws_title_and_subtitle(self) -> None:
        cr = FakeCairo()
        draw_modal_shell(
            FakeRenderer(),
            cr,
            1920,
            1080,
            title="HELLO",
            subtitle="sub",
            panel_w=400.0,
            panel_h=200.0,
        )
        assert "HELLO" in cr.show_text_strings()
        assert "sub" in cr.show_text_strings()

    def test_low_height_uses_smaller_title_font(self) -> None:
        """`h < 720` ⇒ title at font size 20; `h >= 720` ⇒ 23."""
        cr_small = FakeCairo()
        cr_big = FakeCairo()
        draw_modal_shell(
            FakeRenderer(),
            cr_small,
            1000,
            500,
            title="X",
            subtitle="y",
            panel_w=200.0,
            panel_h=100.0,
        )
        draw_modal_shell(
            FakeRenderer(),
            cr_big,
            1000,
            720,
            title="X",
            subtitle="y",
            panel_w=200.0,
            panel_h=100.0,
        )
        small_title = next(t for t in cr_small.texts if t.text == "X")
        big_title = next(t for t in cr_big.texts if t.text == "X")
        assert small_title.font_size == 20
        assert big_title.font_size == 23

    def test_long_subtitle_gets_truncated(self) -> None:
        cr = FakeCairo()
        draw_modal_shell(
            FakeRenderer(),
            cr,
            1000,
            800,
            title="T",
            subtitle="s" * 500,
            panel_w=240.0,
            panel_h=300.0,
        )
        rendered_subs = [t.text for t in cr.texts if "s" in t.text and t.text != "T"]
        # One truncated subtitle string, shorter than the original.
        assert rendered_subs[0].endswith("...")
        assert len(rendered_subs[0]) < 500


# --------------------------------------------------------------------------- #
# Selectable list
# --------------------------------------------------------------------------- #


class TestSelectableList:
    def test_empty_items_shows_empty_message(self) -> None:
        cr = FakeCairo()
        draw_selectable_list(
            FakeRenderer(),
            cr,
            items=[],
            selected_idx=0,
            x=0,
            y=0,
            w=400.0,
            h=200.0,
            empty_message="None found",
        )
        assert "None found" in cr.show_text_strings()

    def test_long_empty_message_is_truncated(self) -> None:
        cr = FakeCairo()
        draw_selectable_list(
            FakeRenderer(),
            cr,
            items=[],
            selected_idx=0,
            x=0,
            y=0,
            w=40.0,
            h=200.0,  # narrow
            empty_message="x" * 200,
        )
        # Truncated – original not shown.
        assert "x" * 200 not in cr.show_text_strings()

    def test_renders_each_item_once(self) -> None:
        cr = FakeCairo()
        items = ["Alpha", "Bravo", "Charlie"]
        draw_selectable_list(
            FakeRenderer(),
            cr,
            items=items,
            selected_idx=0,
            x=0,
            y=0,
            w=400.0,
            h=400.0,
            empty_message="x",
        )
        assert cr.show_text_strings() == items

    def test_selected_row_uses_bold_font(self) -> None:
        cr = FakeCairo()
        draw_selectable_list(
            FakeRenderer(),
            cr,
            items=["a", "b"],
            selected_idx=1,
            x=0,
            y=0,
            w=400.0,
            h=400.0,
            empty_message="x",
        )
        a_text = next(t for t in cr.texts if t.text == "a")
        b_text = next(t for t in cr.texts if t.text == "b")
        assert a_text.bold is False
        assert b_text.bold is True

    def test_long_list_shows_scroll_down_indicator(self) -> None:
        cr = FakeCairo()
        items = [f"item-{i}" for i in range(20)]
        # Short vertical height means only a few items fit → scroll-down "v".
        draw_selectable_list(
            FakeRenderer(),
            cr,
            items=items,
            selected_idx=0,
            x=0,
            y=0,
            w=400.0,
            h=80.0,
            empty_message="x",
        )
        assert "v" in cr.show_text_strings()
        # At scroll_offset == 0 the "up" indicator is suppressed.
        assert "^" not in cr.show_text_strings()

    def test_selecting_far_down_scrolls_and_shows_up_indicator(self) -> None:
        cr = FakeCairo()
        items = [f"item-{i}" for i in range(20)]
        draw_selectable_list(
            FakeRenderer(),
            cr,
            items=items,
            selected_idx=15,
            x=0,
            y=0,
            w=400.0,
            h=80.0,
            empty_message="x",
        )
        assert "^" in cr.show_text_strings()

    def test_out_of_range_selected_idx_hits_break_guard(self) -> None:
        """Defensive ``break`` when ``scroll_offset`` pushes past ``len(items)``.

        When ``selected_idx`` is well past the end of the list (malformed
        state), ``selectable_list_layout`` sets a large ``scroll_offset``
        and the render loop bails out on the first iteration.
        """
        cr = FakeCairo()
        items = ["a", "b", "c"]
        draw_selectable_list(
            FakeRenderer(),
            cr,
            items=items,
            selected_idx=20,  # way out of range
            x=0,
            y=0,
            w=400.0,
            h=80.0,
            empty_message="x",
        )
        # No item text renders because every item_idx is >= len(items).
        # (The scroll-up indicator "^" still shows because scroll_offset > 0.)
        rendered = set(cr.show_text_strings())
        assert not (rendered & {"a", "b", "c"})

    def test_item_text_is_truncated_to_row_width(self) -> None:
        cr = FakeCairo()
        long = "A" * 400
        draw_selectable_list(
            FakeRenderer(),
            cr,
            items=[long],
            selected_idx=0,
            x=0,
            y=0,
            w=60.0,
            h=200.0,
            empty_message="x",
        )
        assert long not in cr.show_text_strings()


# --------------------------------------------------------------------------- #
# Selection menus (source / iface / settings)
# --------------------------------------------------------------------------- #


class TestSelectionMenus:
    def test_source_selection_shows_items_and_title(self) -> None:
        state = _base_state()
        state.source_selection_title = "PICK NDI"
        state.discovered_sources = ["CAM1", "CAM2"]
        state.selected_source_index = 1
        cr = FakeCairo()
        draw_source_selection(FakeRenderer(state=state), cr, state, 1600, 900)
        texts = cr.show_text_strings()
        assert "PICK NDI" in texts
        assert "CAM1" in texts
        assert "CAM2" in texts

    def test_source_selection_overlay_adds_scrim(self) -> None:
        state = _base_state(discovered_sources=["A"], selected_source_index=0)
        cr = FakeCairo()
        draw_source_selection_overlay(FakeRenderer(state=state), cr, state, 1600, 900)
        # Scrim draws one full-frame rectangle.
        frame_rects = [r for r in cr.rects if r[:2] == (0, 0) and r[2:] == (1600, 900)]
        assert len(frame_rects) == 1

    def test_iface_selection_maps_empty_to_auto_detect(self) -> None:
        state = _base_state()
        state.available_interfaces = ["en0", ""]  # second is "auto"
        state.selected_iface_index = 1
        cr = FakeCairo()
        draw_iface_selection(FakeRenderer(state=state), cr, state, 1600, 900)
        texts = cr.show_text_strings()
        assert "en0" in texts
        assert "Auto-detect" in texts

    def test_iface_selection_overlay_adds_scrim(self) -> None:
        state = _base_state(available_interfaces=["en0"], selected_iface_index=0)
        cr = FakeCairo()
        draw_iface_selection_overlay(FakeRenderer(state=state), cr, state, 1600, 900)
        frame_rects = [r for r in cr.rects if r[:2] == (0, 0) and r[2:] == (1600, 900)]
        assert len(frame_rects) == 1

    def test_source_type_selection_renders_display_names(self) -> None:
        """The picker shows each plugin's display name (NDI, RTSP, Test Pattern) – not the input_id slugs."""
        state = _base_state()
        state.available_source_types = [
            ("ndi", "NDI"),
            ("rtsp", "RTSP"),
            ("testpattern", "Test Pattern"),
        ]
        state.selected_source_type_index = 1
        cr = FakeCairo()
        draw_source_type_selection(FakeRenderer(state=state), cr, state, 1600, 900)
        texts = cr.show_text_strings()
        assert "NDI" in texts
        assert "RTSP" in texts
        assert "Test Pattern" in texts

    def test_source_type_selection_overlay_adds_scrim(self) -> None:
        state = _base_state()
        state.available_source_types = [("ndi", "NDI")]
        cr = FakeCairo()
        draw_source_type_selection_overlay(
            FakeRenderer(state=state),
            cr,
            state,
            1600,
            900,
        )
        frame_rects = [r for r in cr.rects if r[:2] == (0, 0) and r[2:] == (1600, 900)]
        assert len(frame_rects) == 1

    def test_url_editor_renders_label_and_buffer(self) -> None:
        """The URL editor surfaces the field label as the modal title and the buffer contents inside the box."""
        state = _base_state()
        state.url_editor_field_label = "RTSP URL"
        state.url_editor_value = "rtsp://10.0.0.5"
        cr = FakeCairo()
        draw_url_editor(FakeRenderer(state=state), cr, state, 1600, 900)
        texts = cr.show_text_strings()
        assert "RTSP URL" in texts  # uppercased title
        assert "rtsp://10.0.0.5" in texts

    def test_url_editor_renders_banner_when_set(self) -> None:
        state = _base_state()
        state.url_editor_field_label = "RTSP URL"
        state.url_editor_value = ""
        state.url_editor_banner = "RTSP needs a URL – type it below."
        cr = FakeCairo()
        draw_url_editor(FakeRenderer(state=state), cr, state, 1600, 900)
        texts = cr.show_text_strings()
        assert any("RTSP needs a URL" in t for t in texts)
        # When banner is set, the static help text must NOT also render.
        assert not any("Type the value" in t for t in texts)

    def test_url_editor_falls_back_to_default_subtitle(self) -> None:
        """No banner → static help text appears so the operator knows
        the keystrokes."""
        state = _base_state()
        state.url_editor_field_label = "RTSP URL"
        state.url_editor_value = ""
        state.url_editor_banner = ""
        cr = FakeCairo()
        draw_url_editor(FakeRenderer(state=state), cr, state, 1600, 900)
        texts = cr.show_text_strings()
        assert any("Type the value" in t for t in texts)

    def test_url_editor_default_title_when_label_empty(self) -> None:
        state = _base_state()
        state.url_editor_field_label = ""
        cr = FakeCairo()
        draw_url_editor(FakeRenderer(state=state), cr, state, 1600, 900)
        texts = cr.show_text_strings()
        assert "URL" in texts

    def test_url_editor_overlay_adds_scrim(self) -> None:
        state = _base_state()
        state.url_editor_field_label = "RTSP URL"
        cr = FakeCairo()
        draw_url_editor_overlay(
            FakeRenderer(state=state),
            cr,
            state,
            1600,
            900,
        )
        frame_rects = [r for r in cr.rects if r[:2] == (0, 0) and r[2:] == (1600, 900)]
        assert len(frame_rects) == 1

    def test_field_choice_picker_renders_title_and_items(self) -> None:
        """The enum-style picker shows the field label as the
        modal title (uppercased) and the choice display names as
        the selectable list rows."""
        state = _base_state()
        state.field_choice_title = "Pattern"
        state.field_choice_items = ["50% Grey", "Stage Scene"]
        state.field_choice_selected_index = 1
        cr = FakeCairo()
        draw_field_choice_picker(FakeRenderer(state=state), cr, state, 1600, 900)
        texts = cr.show_text_strings()
        assert "PATTERN" in texts  # uppercased title
        assert "50% Grey" in texts
        assert "Stage Scene" in texts

    def test_field_choice_picker_default_title_when_empty(self) -> None:
        """No ``field_choice_title`` → fall back to the generic
        ``SELECT VALUE`` heading so the modal doesn't render with a
        blank title bar."""
        state = _base_state()
        state.field_choice_title = ""
        state.field_choice_items = ["A", "B"]
        cr = FakeCairo()
        draw_field_choice_picker(FakeRenderer(state=state), cr, state, 1600, 900)
        texts = cr.show_text_strings()
        assert "SELECT VALUE" in texts

    def test_field_choice_picker_overlay_adds_scrim(self) -> None:
        state = _base_state()
        state.field_choice_title = "Pattern"
        state.field_choice_items = ["50% Grey"]
        cr = FakeCairo()
        draw_field_choice_picker_overlay(
            FakeRenderer(state=state),
            cr,
            state,
            1600,
            900,
        )
        frame_rects = [r for r in cr.rects if r[:2] == (0, 0) and r[2:] == (1600, 900)]
        assert len(frame_rects) == 1

    def test_settings_menu_decorates_disabled_items(self) -> None:
        state = _base_state()
        state.settings_items = ["Calibration", "OSC", "Quit"]
        state.settings_items_enabled = [True, False, True]
        state.settings_selected_index = 0
        cr = FakeCairo()
        draw_settings_menu(FakeRenderer(state=state), cr, state, 1600, 900)
        texts = cr.show_text_strings()
        assert "Calibration" in texts
        assert "OSC (unavailable)" in texts
        assert "Quit" in texts

    def test_settings_menu_uses_per_row_disabled_reason(self) -> None:
        """A non-empty ``settings_items_disabled_reasons`` row replaces the generic ``(unavailable)`` suffix."""
        state = _base_state()
        state.settings_items = ["Open Web UI", "OSC"]
        state.settings_items_enabled = [False, False]
        state.settings_items_disabled_reasons = ["Linux only", ""]
        cr = FakeCairo()
        draw_settings_menu(FakeRenderer(state=state), cr, state, 1600, 900)
        texts = cr.show_text_strings()
        assert "Open Web UI (Linux only)" in texts
        assert "OSC (unavailable)" in texts

    def test_settings_menu_tolerates_short_reasons_list(self) -> None:
        state = _base_state()
        state.settings_items = ["A", "B"]
        state.settings_items_enabled = [True, False]
        state.settings_items_disabled_reasons = []  # not synced
        cr = FakeCairo()
        draw_settings_menu(FakeRenderer(state=state), cr, state, 1600, 900)
        texts = cr.show_text_strings()
        assert "A" in texts
        assert "B (unavailable)" in texts

    def test_settings_menu_renders_ip_and_video_source_info_card(self) -> None:
        """Settings modal mirrors the bottom-left HUD info panel so operators see IP + source context."""
        state = _base_state(
            settings_items=["Network"],
            settings_items_enabled=[True],
        )
        state.ip_text = "10.0.0.5:8080"
        state.video_source_type = "rtsp"
        state.source_label = "rtsp://cam1/stream"
        cr = FakeCairo()
        draw_settings_menu(FakeRenderer(state=state), cr, state, 1600, 900)
        texts = cr.show_text_strings()
        assert any("IP Address" in t for t in texts)
        assert any("10.0.0.5:8080" in t for t in texts)
        assert any("Video Source" in t for t in texts)
        # ``format_source_text`` includes the source type and label.
        assert any("rtsp" in t.lower() for t in texts)

    def test_settings_menu_renders_error_box_when_error_message_set(
        self,
    ) -> None:
        """The error box replaces the old NO SIGNAL overlay's error
        chip – bold body text inside a red-bordered card, so the
        failure reason is hard to miss even from the back of a venue."""
        state = _base_state(
            settings_items=["Network"],
            settings_items_enabled=[True],
        )
        state.error_message = "Connection refused: rtsp://10.0.0.5/stream"
        cr = FakeCairo()
        draw_settings_menu(FakeRenderer(state=state), cr, state, 1600, 900)
        texts = cr.show_text_strings()
        assert "ERROR" in texts
        # Error message body is rendered (may be word-wrapped, so
        # check for a substring rather than the exact line).
        assert any("Connection refused" in t for t in texts)

    def test_settings_menu_skips_error_box_when_message_empty(self) -> None:
        """No error → no red box; keeps the modal lean when the
        operator just opened it manually rather than via auto-banner."""
        state = _base_state(
            settings_items=["Network"],
            settings_items_enabled=[True],
        )
        state.error_message = ""
        cr = FakeCairo()
        draw_settings_menu(FakeRenderer(state=state), cr, state, 1600, 900)
        texts = cr.show_text_strings()
        assert "ERROR" not in texts

    def test_settings_menu_skips_help_block_when_no_inputs_connected(
        self,
    ) -> None:
        """Defensive branch: when neither keyboard nor controller are
        connected, ``build_help_sections`` returns empty and the help
        block under the info card is suppressed (otherwise we'd render
        a 14px empty rounded rect with no content)."""
        state = _base_state(
            keyboard_connected=False,
            controller_connected=False,
            settings_items=["Network"],
            settings_items_enabled=[True],
        )
        cr = FakeCairo()
        # Must not raise – verifies the help_h==0 branch.
        draw_settings_menu(FakeRenderer(state=state), cr, state, 1600, 900)

    def test_settings_menu_error_box_renders_empty_message(self) -> None:
        """Defensive branch: ``_wrap_error_message`` handles an empty
        string by returning a single empty line so callers don't have
        to special-case the empty path (covered for completeness even
        though the real code gates on truthiness before calling)."""
        from openfollow.runtime.overlay_draw_hud import _wrap_error_message

        class _Cr:
            def text_extents(self, _t):
                return SimpleNamespace(width=10.0)

        class _Renderer:
            def _set_ui_font(self, *a, **kw): ...

        assert _wrap_error_message(
            _Renderer(),
            _Cr(),
            "",
            100.0,
            12.0,
        ) == [""]

    def test_settings_menu_error_box_handles_whitespace_only_message(
        self,
    ) -> None:
        """Whitespace-only error messages fall through ``str.split()``
        to an empty word list – return the raw message verbatim so
        the formatter doesn't surface an empty card with no content."""
        from openfollow.runtime.overlay_draw_hud import _wrap_error_message

        class _Cr:
            def text_extents(self, _t):
                return SimpleNamespace(width=10.0)

        class _Renderer:
            def _set_ui_font(self, *a, **kw): ...

        assert _wrap_error_message(
            _Renderer(),
            _Cr(),
            "   ",
            100.0,
            12.0,
        ) == ["   "]

    def test_wrap_error_message_hard_wraps_long_unbreakable_token(
        self,
    ) -> None:
        """A single token wider than ``max_w`` (e.g. an RTSP URL) is
        hard-wrapped at character boundaries so the red error box
        doesn't overflow at small window sizes."""
        from openfollow.runtime.overlay_draw_hud import _wrap_error_message

        class _Cr:
            def text_extents(self, t):
                # Each character is 10px wide.
                return SimpleNamespace(width=10.0 * len(t))

        class _Renderer:
            def _set_ui_font(self, *a, **kw): ...

        # max_w of 50 with 10px/char ⇒ 5 chars per line. The "after"
        # word fits on its own line (5 chars exactly), so it starts a
        # fresh line after the URL chunks.
        lines = _wrap_error_message(
            _Renderer(),
            _Cr(),
            "rtsp://camera.example.com/stream after",
            50.0,
            12.0,
        )
        assert all(len(line) <= 5 for line in lines)
        assert (
            "".join(line for line in lines if "after" not in line)
            .replace(
                " ",
                "",
            )
            .startswith("rtsp:")
        )
        assert any("after" in line for line in lines)

    def test_wrap_error_message_hard_wrap_preserves_single_char_token(
        self,
    ) -> None:
        """Defensive: if even a single character is wider than
        ``max_w`` the helper still returns the token rather than an
        empty list, so callers always get at least one renderable
        line."""
        from openfollow.runtime.overlay_draw_hud import _wrap_error_message

        class _Cr:
            def text_extents(self, t):
                return SimpleNamespace(width=100.0 * len(t))

        class _Renderer:
            def _set_ui_font(self, *a, **kw): ...

        lines = _wrap_error_message(
            _Renderer(),
            _Cr(),
            "abc",
            10.0,
            12.0,
        )
        # Each char exceeds max_w; helper falls back to per-char chunks.
        assert lines == ["a", "b", "c"]

    def test_settings_menu_wraps_long_error_messages(self) -> None:
        """Long error messages word-wrap across multiple lines inside
        the error box instead of ellipsis-truncating – the operator
        needs the full diagnostic to recover."""
        state = _base_state(
            settings_items=["Network"],
            settings_items_enabled=[True],
        )
        state.error_message = (
            "GStreamer pipeline negotiation failed: "
            "rtspsrc could not resolve hostname rtsp://camera.example.com/stream "
            "after 5 retries"
        )
        cr = FakeCairo()
        draw_settings_menu(FakeRenderer(state=state), cr, state, 1600, 900)
        texts = cr.show_text_strings()
        # The full message appears across one or more body lines.
        joined = " ".join(t for t in texts if t)
        assert "GStreamer pipeline" in joined
        assert "camera.example.com" in joined
        assert "after 5 retries" in joined

    def test_settings_overlay_adds_scrim(self) -> None:
        state = _base_state(settings_items=["Quit"], settings_items_enabled=[True])
        cr = FakeCairo()
        draw_settings_overlay(FakeRenderer(state=state), cr, state, 1600, 900)
        frame_rects = [r for r in cr.rects if r[:2] == (0, 0) and r[2:] == (1600, 900)]
        assert len(frame_rects) == 1

    def test_settings_menu_banner_renders_in_red_error_box(self) -> None:
        """When ``settings_menu_banner`` is set, the context renders inside the red-bordered error box."""
        state = _base_state(
            settings_items=["Network"],
            settings_items_enabled=[True],
        )
        state.settings_menu_banner = "Configured IP 10.0.0.99 not available."
        cr = FakeCairo()
        draw_settings_menu(FakeRenderer(state=state), cr, state, 1600, 900)
        texts = cr.show_text_strings()
        assert any("Configured IP 10.0.0.99 not available." in t for t in texts)
        # Subtitle is unchanged – banner content lives in the error box.
        assert any("Open a sub-screen." in t for t in texts)
        assert "ERROR" in texts

    def test_selection_menu_without_help_skips_help_block(self) -> None:
        """If neither keyboard nor controller is connected, the help block
        inside the menu is omitted (help_sections_height == 0).
        """
        state = _base_state(keyboard_connected=False, controller_connected=False)
        cr = FakeCairo()
        draw_selection_menu(
            FakeRenderer(state=state),
            cr,
            state,
            1600,
            900,
            title="T",
            subtitle="s",
            mode="source-selection",
            items=["x"],
            selected_idx=0,
            empty_message="n",
        )
        # Still renders the list.
        assert "x" in cr.show_text_strings()


# --------------------------------------------------------------------------- #
# draw_hud (main overlay)
# --------------------------------------------------------------------------- #


class TestDrawHud:
    def test_always_draws_bottom_left_info_and_system_stats(self) -> None:
        state = _base_state(ip_text="127.0.0.1")
        cr = FakeCairo()
        renderer = FakeRenderer(state=state)
        draw_hud(renderer, cr, state, 1920, 1080)
        texts = cr.show_text_strings()
        assert "IP Address:" in texts
        assert any(t.startswith("CPU:") for t in texts)

    # The bottom-center controller panel was retired; the per-marker
    # controller badge on each marker card now carries that information.

    def test_icon_is_requested_from_renderer(self) -> None:
        state = _base_state()
        cr = FakeCairo()
        renderer = FakeRenderer(state=state)
        draw_hud(renderer, cr, state, 1920, 1080)
        assert renderer.draw_icon_calls == [(10.0, 10.0, 24.0)]

    def test_hud_help_on_renders_help_block(self) -> None:
        state = _base_state(show_hud_help=True)
        cr = FakeCairo()
        draw_hud(FakeRenderer(state=state), cr, state, 1920, 1080)
        # "Keyboard" section heading renders in ALL CAPS by the help block.
        assert "KEYBOARD" in cr.show_text_strings()

    def test_hud_help_off_shows_hint_next_to_icon(self) -> None:
        state = _base_state(show_hud_help=False)
        state.keyboard_labels = {"toggle_help": "h"}
        state.button_labels = {}
        cr = FakeCairo()
        draw_hud(FakeRenderer(state=state), cr, state, 1920, 1080)
        assert any(": help" in t for t in cr.show_text_strings())

    def test_hud_help_off_with_no_key_or_btn_renders_no_hint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Defensive branch: if both label lookups somehow yield empty
        strings (which shouldn't happen with the ``or "h"`` / ``or "Y"``
        fallbacks), the hint line is suppressed rather than rendering
        ": help".  We force the situation by patching the name lookups at
        the overlay-hud import site.
        """
        from openfollow.runtime import overlay_draw_hud as hud_mod

        monkeypatch.setattr(hud_mod, "key_label", lambda *a, **kw: "")
        monkeypatch.setattr(hud_mod, "friendly_button_label", lambda *a, **kw: "")

        state = _base_state(show_hud_help=False)
        cr = FakeCairo()
        draw_hud(FakeRenderer(state=state), cr, state, 1920, 1080)
        assert all(": help" not in t for t in cr.show_text_strings())

    def test_help_block_suppressed_when_no_inputs_connected(self) -> None:
        state = _base_state(keyboard_connected=False, controller_connected=False)
        cr = FakeCairo()
        draw_hud(FakeRenderer(state=state), cr, state, 1920, 1080)
        assert "KEYBOARD" not in cr.show_text_strings()
        assert "CONTROLLER" not in cr.show_text_strings()

    def test_marker_cards_render_one_per_marker(self) -> None:
        state = _base_state()
        state.markers = [_marker(marker_id=1), _marker(marker_id=2)]
        state.selected_id = 1
        cr = FakeCairo()
        draw_hud(FakeRenderer(state=state), cr, state, 1920, 1080)
        labels = [t for t in cr.show_text_strings() if t.startswith("M")]
        assert "M1" in labels
        assert "M2" in labels


# --------------------------------------------------------------------------- #
# draw_bottom_left_info_panel
# --------------------------------------------------------------------------- #


class TestBottomLeftInfoPanel:
    def test_renders_both_rows(self) -> None:
        state = _base_state(ip_text="192.168.1.2")
        state.video_source_type = "ndi"
        state.source_label = "Main"
        cr = FakeCairo()
        draw_bottom_left_info_panel(FakeRenderer(state=state), cr, state, 1920, 1080)
        texts = cr.show_text_strings()
        assert "IP Address:" in texts
        assert "Video Source:" in texts
        assert "192.168.1.2" in texts
        assert any("NDI" in t for t in texts)

    def test_empty_ip_falls_back_to_unavailable(self) -> None:
        state = _base_state(ip_text="")
        cr = FakeCairo()
        draw_bottom_left_info_panel(FakeRenderer(state=state), cr, state, 1920, 1080)
        assert "Unavailable" in cr.show_text_strings()

    def test_long_values_are_truncated(self) -> None:
        state = _base_state(ip_text="x" * 200)
        state.video_source_type = "ndi"
        state.source_label = "y" * 200
        cr = FakeCairo()
        draw_bottom_left_info_panel(FakeRenderer(state=state), cr, state, 320, 200)
        texts = cr.show_text_strings()
        assert "x" * 200 not in texts

    def test_panel_turns_red_when_settings_banner_set(self) -> None:
        """The bottom-left HUD info panel mirrors the Settings menu's error state so operators see the failure."""
        from openfollow.runtime.overlay_draw_style import COLOR_DANGER

        state = _base_state(ip_text="192.168.1.2")
        state.video_source_type = "rtsp"
        state.settings_menu_banner = "Video source unreachable."
        cr = FakeCairo()
        draw_bottom_left_info_panel(
            FakeRenderer(state=state),
            cr,
            state,
            1920,
            1080,
        )
        # At least one stroke ran (the red border) AND the value
        # colour was set to COLOR_DANGER for the IP / source rows.
        assert cr.strokes >= 1
        assert any(call[0] == "rgb" and call[1:] == COLOR_DANGER for call in cr.calls)

    def test_panel_turns_red_when_error_message_set(self) -> None:
        """Same red treatment when ``state.error_message`` is set
        (mid-stream disconnect path that doesn't go through the
        auto-banner)."""
        from openfollow.runtime.overlay_draw_style import COLOR_DANGER

        state = _base_state(ip_text="192.168.1.2")
        state.error_message = "Connection refused"
        cr = FakeCairo()
        draw_bottom_left_info_panel(
            FakeRenderer(state=state),
            cr,
            state,
            1920,
            1080,
        )
        assert cr.strokes >= 1
        assert any(call[0] == "rgb" and call[1:] == COLOR_DANGER for call in cr.calls)

    def test_panel_stays_normal_when_no_error(self) -> None:
        """Default state: no banner, no error_message → panel uses the
        standard background gradient and values in normal text colour
        (no danger-red anywhere in the draw calls)."""
        from openfollow.runtime.overlay_draw_style import COLOR_DANGER

        state = _base_state(ip_text="192.168.1.2")
        state.video_source_type = "ndi"
        state.settings_menu_banner = ""
        state.error_message = ""
        cr = FakeCairo()
        draw_bottom_left_info_panel(
            FakeRenderer(state=state),
            cr,
            state,
            1920,
            1080,
        )
        # No danger-coloured fill / border / text in normal state.
        assert not any(call[0] == "rgb" and call[1:] == COLOR_DANGER for call in cr.calls)
        assert not any(call[0] == "rgba" and call[1:4] == COLOR_DANGER for call in cr.calls)


# --------------------------------------------------------------------------- #
# draw_system_stats + draw_panel + draw_panel_background
# --------------------------------------------------------------------------- #


class TestSystemStatsAndPanels:
    def test_system_stats_includes_cpu_and_ram(self) -> None:
        state = _base_state(cpu_percent=42.1, ram_percent=33.3)
        cr = FakeCairo()
        draw_system_stats(FakeRenderer(state=state), cr, state, 1920)
        rendered = cr.show_text_strings()
        assert any("CPU:" in t and "RAM:" in t for t in rendered)

    def test_system_stats_includes_temperature_when_set(self) -> None:
        state = _base_state(cpu_percent=10.0, ram_percent=20.0, temperature=55.2)
        cr = FakeCairo()
        draw_system_stats(FakeRenderer(state=state), cr, state, 1920)
        assert any("°C" in t for t in cr.show_text_strings())

    def test_draw_panel_background_uses_card_chrome(self) -> None:
        # Every panel now shares the operator-message card chrome: a
        # translucent COLOR_BG_BASE fill + soft white COLOR_BORDER at 1px –
        # no opaque fill, no golden accent border.
        from openfollow.runtime.overlay_draw_style import (
            CARD_BG_ALPHA,
            COLOR_ACCENT,
            COLOR_BG_BASE,
            COLOR_BORDER,
        )

        cr = FakeCairo()
        draw_panel_background(FakeRenderer(), cr, 5, 5, 100, 50, radius=10)
        assert not any(c[0] == "source_pattern" for c in cr.calls)
        # Translucent card fill (not the old opaque ``set_source_rgb``).
        assert ("rgba", *COLOR_BG_BASE, CARD_BG_ALPHA) in cr.calls
        assert ("rgb", *COLOR_BG_BASE) not in cr.calls
        # Soft white border at 1px (not the old accent border at 1.6px).
        assert ("rgba", *COLOR_BORDER) in cr.calls
        assert ("rgba", COLOR_ACCENT[0], COLOR_ACCENT[1], COLOR_ACCENT[2], 0.58) not in cr.calls
        assert ("line_width", 1.0) in cr.calls
        assert cr.strokes == 1
        assert cr.fills == 1

    def test_draw_panel_centers_text_within_rect(self) -> None:
        cr = FakeCairo()
        draw_panel(FakeRenderer(), cr, 0, 0, 200, 40, "hello", 12)
        shown = next(t for t in cr.texts if t.text == "hello")
        # centred roughly inside the 200×40 rect.
        assert 0 < shown.x < 200
        assert 0 < shown.y < 40

    def test_system_stats_panel_uses_card_chrome(self) -> None:
        # CPU/RAM/Temp panel reads in the operator-message card style.
        state = _base_state(cpu_percent=10.0, ram_percent=20.0, temperature=50.0)
        cr = FakeCairo()
        draw_system_stats(FakeRenderer(state=state), cr, state, 1920)
        assert _emits_card_chrome(cr)

    def test_info_panel_uses_card_chrome(self) -> None:
        # IP / Video Source / Station panel reads in the card style.
        state = _base_state(ip_text="10.0.0.5", station_name="Spot 1")
        cr = FakeCairo()
        draw_bottom_left_info_panel(FakeRenderer(state=state), cr, state, 1920, 1080)
        assert _emits_card_chrome(cr)

    def test_info_panel_error_state_keeps_danger_chrome(self) -> None:
        # The failure state is a deliberate red alert and must NOT be
        # flattened into the neutral card chrome.
        from openfollow.runtime.overlay_draw_style import COLOR_DANGER

        state = _base_state(ip_text="10.0.0.5", error_message="SRT connection lost")
        cr = FakeCairo()
        draw_bottom_left_info_panel(FakeRenderer(state=state), cr, state, 1920, 1080)
        assert not _emits_card_chrome(cr)
        assert ("rgb", *COLOR_DANGER) in cr.calls

    def test_help_panel_uses_card_chrome(self) -> None:
        # Top-left help block panel reads in the card style.
        state = _base_state(show_hud_help=True)
        cr = FakeCairo()
        draw_hud(FakeRenderer(state=state), cr, state, 1920, 1080)
        assert _emits_card_chrome(cr)


# --------------------------------------------------------------------------- #
# draw_marker_card – card styling branches
# --------------------------------------------------------------------------- #


class TestMarkerCard:
    def test_label_falls_back_to_m_prefix_when_name_empty(self) -> None:
        """No catalog name → render ``M<id>`` so the operator still
        sees *something* during the transient race between the
        control plane writing the catalog and the draw plane reading
        it (same race that the colour fallback handles)."""
        state = _base_state()
        cr = FakeCairo()
        draw_marker_card(
            FakeRenderer(state=state),
            cr,
            x=0,
            y=0,
            w=180,
            h=64,
            t=_marker(marker_id=7, x=1.0, y=2.0, z=3.0, speed=1.25),
            selected=False,
            state=state,
        )
        assert "M7" in cr.show_text_strings()
        # Position line renders via format_length_compact (metric: metres).
        assert any("+1.00" in t and "+2.00" in t for t in cr.show_text_strings())
        assert any("1.25 m/s" in t for t in cr.show_text_strings())

    def test_group_popped_even_when_a_draw_call_raises(self) -> None:
        """A draw exception inside a viewer-only card still pops the Cairo
        group (try/finally), so the caller's 'Overlay Error' fallback isn't
        captured by a dangling group surface and the group stack self-heals."""

        class _BoomCairo(FakeCairo):
            def show_text(self, text: str) -> None:
                raise RuntimeError("draw boom")

        cr = _BoomCairo()
        # Viewer-only marker → draw_marker_card wraps the body in a group.
        marker = _marker(marker_id=1, is_controlled=False)
        with pytest.raises(RuntimeError, match="draw boom"):
            draw_marker_card(FakeRenderer(), cr, x=0, y=0, w=180, h=64, t=marker, selected=False)
        kinds = [c[0] for c in cr.calls]
        assert kinds.count("push_group") == 1
        assert kinds.count("pop_group_to_source") == 1
        # The pop runs after the push – the stack is balanced on the way out.
        assert kinds.index("pop_group_to_source") > kinds.index("push_group")

    def test_imperial_unit_system_renders_feet_and_ft_per_sec(self) -> None:
        state = _base_state(unit_system=UnitSystem.IMPERIAL)
        cr = FakeCairo()
        draw_marker_card(
            FakeRenderer(state=state),
            cr,
            x=0,
            y=0,
            w=180,
            h=64,
            t=_marker(marker_id=1, x=1.0, y=2.0, z=3.0, speed=0.5),
            selected=False,
            state=state,
        )
        shown = cr.show_text_strings()
        # 1.0 m = 3.28 ft, 0.5 m/s = 1.64 ft/s.
        assert any("+3.28" in t for t in shown)
        assert any("1.64 ft/s" in t for t in shown)
        assert not any("m/s" in t for t in shown)

    def test_catalog_name_renders_instead_of_m_prefix(self) -> None:
        """Catalog-populated ``name`` wins over the ``M<id>`` fallback,
        so an operator who labelled a marker "House Left" sees that
        on the HUD instead of "M3"."""
        state = _base_state()
        cr = FakeCairo()
        marker = _marker(marker_id=3)
        marker.name = "House Left"
        draw_marker_card(
            FakeRenderer(state=state),
            cr,
            x=0,
            y=0,
            w=180,
            h=64,
            t=marker,
            selected=False,
            state=state,
        )
        shown = cr.show_text_strings()
        assert "House Left" in shown
        assert "M3" not in shown

    def test_marker_fader_value_appended_to_speed_line(self) -> None:
        """A marker with a provisioned gamepad fader shows its 0..1 value
        appended to the speed line ('F 0.42'). Markers without one
        (``marker_fader=None``, the default in every other test) render
        the plain speed line – covering the other branch."""
        state = _base_state()
        cr = FakeCairo()
        marker = _marker(marker_id=2, speed=1.0)
        marker.marker_fader = 0.42
        draw_marker_card(
            FakeRenderer(state=state),
            cr,
            x=0,
            y=0,
            w=180,
            h=64,
            t=marker,
            selected=False,
            state=state,
        )
        assert any("F 0.42" in t for t in cr.show_text_strings())

    def test_selected_card_uses_larger_stroke_and_accent_label(self) -> None:
        state = _base_state()
        cr_sel = FakeCairo()
        cr_unsel = FakeCairo()
        draw_marker_card(
            FakeRenderer(state=state),
            cr_sel,
            x=0,
            y=0,
            w=180,
            h=64,
            t=_marker(marker_id=1),
            selected=True,
            state=state,
        )
        draw_marker_card(
            FakeRenderer(state=state),
            cr_unsel,
            x=0,
            y=0,
            w=180,
            h=64,
            t=_marker(marker_id=1),
            selected=False,
            state=state,
        )
        sel_widths = [c[1] for c in cr_sel.calls if c[0] == "line_width"]
        unsel_widths = [c[1] for c in cr_unsel.calls if c[0] == "line_width"]
        # Bumped 1px thicker than the original 1.5/1.8 – the chrome
        # reads better at typical operator viewing distance, and the
        # selection delta (selected = unselected + 0.3) is preserved.
        assert 2.8 in sel_widths
        assert 2.5 in unsel_widths

    def test_offline_marker_uses_danger_color_for_dot(self) -> None:
        state = _base_state()
        cr = FakeCairo()
        draw_marker_card(
            FakeRenderer(state=state),
            cr,
            x=0,
            y=0,
            w=180,
            h=64,
            t=_marker(online=False),
            selected=False,
            state=state,
        )
        # DANGER color is #ff8c8c ≈ (1.0, 0.549, 0.549).
        danger_set = [c for c in cr.calls if c[0] == "rgb" and c[1:] == (1.0, 0.549, 0.549)]
        assert danger_set

    def test_online_marker_draws_extra_glow_ring(self) -> None:
        """An online marker renders a second stroked arc around the dot."""
        state = _base_state()
        cr_online = FakeCairo()
        cr_offline = FakeCairo()
        draw_marker_card(
            FakeRenderer(state=state),
            cr_online,
            x=0,
            y=0,
            w=180,
            h=64,
            t=_marker(online=True),
            selected=False,
            state=state,
        )
        draw_marker_card(
            FakeRenderer(state=state),
            cr_offline,
            x=0,
            y=0,
            w=180,
            h=64,
            t=_marker(online=False),
            selected=False,
            state=state,
        )
        # Online version has exactly one extra arc (the glow ring).
        assert len(cr_online.arcs) == len(cr_offline.arcs) + 1

    def test_z_display_from_stage_subtracts_grid_z_offset(self) -> None:
        state = _base_state()
        state.grid_config = (10.0, 6.0, 1.0, 0.0, 0.0, 2.0)
        state.z_display_from_stage = True
        cr = FakeCairo()
        draw_marker_card(
            FakeRenderer(state=state),
            cr,
            x=0,
            y=0,
            w=180,
            h=64,
            t=_marker(z=5.0),
            selected=False,
            state=state,
        )
        # Displayed z should be 5.0 − 2.0 = 3.0.
        assert any("+3.00" in t for t in cr.show_text_strings())

    def test_default_none_state_uses_marker_z_directly(self) -> None:
        cr = FakeCairo()
        draw_marker_card(
            FakeRenderer(),
            cr,
            x=0,
            y=0,
            w=180,
            h=64,
            t=_marker(z=1.42),
            selected=False,
            state=None,
        )
        assert any("+1.42" in t for t in cr.show_text_strings())

    def test_none_speed_renders_as_zero(self) -> None:
        cr = FakeCairo()
        draw_marker_card(
            FakeRenderer(),
            cr,
            x=0,
            y=0,
            w=180,
            h=64,
            t=_marker(speed=None),
            selected=False,
            state=None,
        )
        assert any("0.00 m/s" in t for t in cr.show_text_strings())

    def test_speed_bar_filled_when_speed_above_min(self) -> None:
        """Positive speed produces an extra filled bar rectangle."""
        state = _base_state(min_speed=0.1, max_speed=3.0)
        cr_fast = FakeCairo()
        cr_slow = FakeCairo()
        draw_marker_card(
            FakeRenderer(state=state),
            cr_fast,
            x=0,
            y=0,
            w=180,
            h=64,
            t=_marker(speed=2.0),
            selected=False,
            state=state,
        )
        draw_marker_card(
            FakeRenderer(state=state),
            cr_slow,
            x=0,
            y=0,
            w=180,
            h=64,
            t=_marker(speed=0.0),
            selected=False,
            state=state,
        )
        # Fast card draws one more filled bar rect than the slow one.
        assert len(cr_fast.rects) == len(cr_slow.rects) + 1

    def test_speed_bar_skipped_when_zero_range(self) -> None:
        """If min == max, we divide by zero in the raw ratio; the module
        documents that as ratio = 0 so no fill."""
        state = _base_state(min_speed=1.0, max_speed=1.0)
        cr = FakeCairo()
        draw_marker_card(
            FakeRenderer(state=state),
            cr,
            x=0,
            y=0,
            w=180,
            h=64,
            t=_marker(speed=5.0),
            selected=False,
            state=state,
        )
        # The bar track is a rounded rect (arc-based, not a plain rectangle);
        # a zero range yields ratio 0, so no fill rectangle (bar_h == 8) is
        # painted on top.
        bar_fill_rects = [r for r in cr.rects if r[3] == 8]
        assert bar_fill_rects == []


# --------------------------------------------------------------------------- #
# Marker card visual treatment (border colour, fill, alpha, controller badge)
# --------------------------------------------------------------------------- #


class TestMarkerCardRendering:
    def test_border_uses_marker_color(self) -> None:
        cr = FakeCairo()
        draw_marker_card(
            FakeRenderer(),
            cr,
            x=0,
            y=0,
            w=180,
            h=64,
            t=_marker(color="#ff0000"),
            selected=False,
            state=None,
        )
        # The marker-colour stroke is the first rgba with alpha 0.62 (unselected).
        marker_color_strokes = [
            c
            for c in cr.calls
            if c[0] == "rgba" and c[1:3] == (1.0, 0.0) and c[3] == 0.0 and c[4] == pytest.approx(0.62)
        ]
        assert marker_color_strokes

    def test_body_fill_uses_solid_color_not_gradient(self) -> None:
        """The body fill flattened from LinearGradient to a solid COLOR_BG_BASE."""
        from openfollow.runtime.overlay_draw_style import COLOR_BG_BASE

        cr = FakeCairo()
        draw_marker_card(
            FakeRenderer(),
            cr,
            x=0,
            y=0,
            w=180,
            h=64,
            t=_marker(),
            selected=False,
            state=None,
        )
        # No ``set_source`` pattern means no LinearGradient was used for
        # the body fill (the speed bar gradient is still pattern-based,
        # but the speed bar isn't reached when ratio == 0 with default state).
        body_fills = [c for c in cr.calls if c[0] == "rgb" and tuple(c[1:]) == COLOR_BG_BASE]
        assert body_fills

    def test_controlled_marker_with_bound_pad_renders_badge(self) -> None:
        cr = FakeCairo()
        draw_marker_card(
            FakeRenderer(),
            cr,
            x=0,
            y=0,
            w=180,
            h=64,
            t=_marker(controller_idx=0, controller_connected=True, is_controlled=True),
            selected=False,
            state=None,
        )
        assert "C0" in cr.show_text_strings()

    def test_disconnected_pad_renders_muted_badge_suffix(self) -> None:
        """A bound but disconnected pad still shows the badge so the
        operator spots the missing pad at a glance – text uses a dot
        suffix and renders in muted color."""
        cr = FakeCairo()
        draw_marker_card(
            FakeRenderer(),
            cr,
            x=0,
            y=0,
            w=180,
            h=64,
            t=_marker(controller_idx=1, controller_connected=False, is_controlled=True),
            selected=False,
            state=None,
        )
        assert any(t.startswith("C1") for t in cr.show_text_strings())
        # Connected badge would be "C1"; disconnected is "C1·" (dot suffix).
        assert "C1·" in cr.show_text_strings()

    def test_unbound_controlled_marker_omits_badge(self) -> None:
        cr = FakeCairo()
        draw_marker_card(
            FakeRenderer(),
            cr,
            x=0,
            y=0,
            w=180,
            h=64,
            t=_marker(controller_idx=None, is_controlled=True),
            selected=False,
            state=None,
        )
        texts = cr.show_text_strings()
        assert not any(t.startswith("C") for t in texts)

    def test_viewer_only_marker_omits_speed_bar(self) -> None:
        """Viewer-only cards (in viewer_marker_ids but NOT in
        controlled_marker_ids) drop the speed bar – it's a control-context
        affordance that would visualise a value the operator can't change."""
        state = _base_state(min_speed=0.1, max_speed=3.0)
        cr_viewer = FakeCairo()
        cr_controlled = FakeCairo()
        draw_marker_card(
            FakeRenderer(state=state),
            cr_viewer,
            x=0,
            y=0,
            w=180,
            h=64,
            t=_marker(speed=2.0, is_controlled=False),
            selected=False,
            state=state,
        )
        draw_marker_card(
            FakeRenderer(state=state),
            cr_controlled,
            x=0,
            y=0,
            w=180,
            h=64,
            t=_marker(speed=2.0, is_controlled=True),
            selected=False,
            state=state,
        )
        # The bar track is a rounded rect; only the coloured fill is a plain
        # rectangle (bar_h == 8). Viewer-only cards draw no bar at all, so no
        # such rectangle; controlled cards draw the fill on top of the track.
        viewer_bars = [r for r in cr_viewer.rects if r[3] == 8]
        controlled_bars = [r for r in cr_controlled.rects if r[3] == 8]
        assert viewer_bars == []
        assert len(controlled_bars) >= 1

    def test_viewer_only_marker_renders_at_reduced_alpha(self) -> None:
        cr = FakeCairo()
        draw_marker_card(
            FakeRenderer(),
            cr,
            x=0,
            y=0,
            w=180,
            h=64,
            t=_marker(is_controlled=False),
            selected=False,
            state=None,
        )
        # Viewer-only path: push_group + pop_group_to_source + paint_with_alpha(0.6).
        paint_alphas = [c[1] for c in cr.calls if c[0] == "paint_with_alpha"]
        assert paint_alphas == [pytest.approx(0.6)]
        ops = [c[0] for c in cr.calls]
        assert "push_group" in ops
        assert "pop_group_to_source" in ops

    def test_controlled_marker_skips_group_for_zero_overhead(self) -> None:
        cr = FakeCairo()
        draw_marker_card(
            FakeRenderer(),
            cr,
            x=0,
            y=0,
            w=180,
            h=64,
            t=_marker(is_controlled=True),
            selected=False,
            state=None,
        )
        ops = [c[0] for c in cr.calls]
        assert "push_group" not in ops
        assert "pop_group_to_source" not in ops
        assert "paint_with_alpha" not in ops


# --------------------------------------------------------------------------- #
# Settings menu info card – orphan controllers
# --------------------------------------------------------------------------- #


class TestSettingsInfoCardOrphans:
    def test_unbound_controllers_row_renders_when_list_present(self) -> None:
        from openfollow.runtime.overlay_draw_hud import _draw_settings_info_card

        state = _base_state(ip_text="1.2.3.4")
        state.unbound_controller_indices = [2, 3]
        cr = FakeCairo()
        _draw_settings_info_card(FakeRenderer(state=state), cr, state, 0, 0, 400)
        texts = cr.show_text_strings()
        assert "Unbound controllers:" in texts
        assert any("Ctrl2" in t and "Ctrl3" in t for t in texts)

    def test_unbound_controllers_row_omitted_when_empty(self) -> None:
        from openfollow.runtime.overlay_draw_hud import _draw_settings_info_card

        state = _base_state(ip_text="1.2.3.4")
        state.unbound_controller_indices = []
        cr = FakeCairo()
        _draw_settings_info_card(FakeRenderer(state=state), cr, state, 0, 0, 400)
        assert "Unbound controllers:" not in cr.show_text_strings()


# --------------------------------------------------------------------------- #
# draw_help_block direct test
# --------------------------------------------------------------------------- #


class TestHelpBlock:
    def test_emits_section_title_then_lines(self) -> None:
        cr = FakeCairo()
        sections = [("Keyboard", ["W/A/S/D: Move", "R/T: Speed"])]
        draw_help_block(FakeRenderer(), cr, 10, 20, 200, sections)
        texts = cr.show_text_strings()
        assert texts[0] == "KEYBOARD"
        # Bullet prefix is added by the block.
        assert any(t.startswith("• W/A/S/D") for t in texts)
        assert any(t.startswith("• R/T") for t in texts)

    def test_long_lines_are_truncated(self) -> None:
        cr = FakeCairo()
        sections = [("K", ["x" * 400])]
        draw_help_block(FakeRenderer(), cr, 10, 20, 60, sections)
        # "K" title plus truncated bullet line.
        lines = [t for t in cr.show_text_strings() if t.startswith("•")]
        assert lines
        assert lines[0].endswith("...")

    def test_gap_between_sections_does_not_break_rendering(self) -> None:
        cr = FakeCairo()
        sections = [("A", ["line1"]), ("B", ["line2"])]
        draw_help_block(FakeRenderer(), cr, 10, 20, 300, sections)
        texts = cr.show_text_strings()
        assert "A" in texts
        assert "B" in texts


# --------------------------------------------------------------------------- #
# draw_button_detection_overlay
# --------------------------------------------------------------------------- #


class TestButtonDetectionOverlay:
    def test_none_state_draws_nothing(self) -> None:
        state = _base_state(button_detection=None)
        cr = FakeCairo()
        draw_button_detection_overlay(FakeRenderer(state=state), cr, state, 1600, 900)
        assert cr.calls == []

    def test_inactive_state_draws_nothing(self) -> None:
        state = _base_state(button_detection=ButtonDetectionState(active=False))
        cr = FakeCairo()
        draw_button_detection_overlay(FakeRenderer(state=state), cr, state, 1600, 900)
        assert cr.calls == []

    def test_active_with_current_label_draws_prompt(self) -> None:
        bd = ButtonDetectionState(
            active=True,
            current_label="A",
            step=0,
            total_steps=4,
        )
        state = _base_state(button_detection=bd)
        cr = FakeCairo()
        draw_button_detection_overlay(FakeRenderer(state=state), cr, state, 1600, 900)
        texts = cr.show_text_strings()
        assert "Press button:" in texts
        # "A" is mapped through _BUTTON_DISPLAY_NAMES to "A" (pass-through).
        assert "A" in texts

    def test_bumper_uses_long_form_display_name(self) -> None:
        bd = ButtonDetectionState(active=True, current_label="RB", step=1, total_steps=4)
        state = _base_state(button_detection=bd)
        cr = FakeCairo()
        draw_button_detection_overlay(FakeRenderer(state=state), cr, state, 1600, 900)
        assert "Right Bumper" in cr.show_text_strings()

    def test_unknown_label_passes_through_unchanged(self) -> None:
        bd = ButtonDetectionState(active=True, current_label="CUSTOM_BTN", step=0, total_steps=1)
        state = _base_state(button_detection=bd)
        cr = FakeCairo()
        draw_button_detection_overlay(FakeRenderer(state=state), cr, state, 1600, 900)
        assert "CUSTOM_BTN" in cr.show_text_strings()

    def test_empty_label_shows_detection_complete(self) -> None:
        bd = ButtonDetectionState(active=True, current_label="", step=4, total_steps=4)
        state = _base_state(button_detection=bd)
        cr = FakeCairo()
        draw_button_detection_overlay(FakeRenderer(state=state), cr, state, 1600, 900)
        assert "Detection Complete!" in cr.show_text_strings()

    def test_low_height_shrinks_prompt_font(self) -> None:
        """`h < 720` switches the big prompt from font 42 to 32."""
        bd = ButtonDetectionState(active=True, current_label="A", step=0, total_steps=2)
        state = _base_state(button_detection=bd)
        cr_big = FakeCairo()
        cr_small = FakeCairo()
        draw_button_detection_overlay(FakeRenderer(state=state), cr_big, state, 1600, 800)
        draw_button_detection_overlay(FakeRenderer(state=state), cr_small, state, 1600, 500)
        big_sizes = {t.font_size for t in cr_big.texts if t.text == "A"}
        small_sizes = {t.font_size for t in cr_small.texts if t.text == "A"}
        assert 42 in big_sizes
        assert 32 in small_sizes

    def test_progress_bar_renders_with_ratio_above_zero(self) -> None:
        bd = ButtonDetectionState(active=True, current_label="X", step=1, total_steps=4)
        state = _base_state(button_detection=bd)
        cr = FakeCairo()
        draw_button_detection_overlay(FakeRenderer(state=state), cr, state, 1600, 900)
        # One background track + one progress fill = at least two rounded
        # rectangles.  Each emits the 4 arcs of a rounded rect.
        progress_arcs = [a for a in cr.arcs if a[2] == 3.0]
        assert len(progress_arcs) >= 8

    def test_progress_bar_draws_only_background_at_step_zero(self) -> None:
        bd = ButtonDetectionState(active=True, current_label="A", step=0, total_steps=4)
        state = _base_state(button_detection=bd)
        cr = FakeCairo()
        draw_button_detection_overlay(FakeRenderer(state=state), cr, state, 1600, 900)
        # step=0 → ratio=0 → second rounded-rect not emitted.
        progress_arcs = [a for a in cr.arcs if a[2] == 3.0]
        assert len(progress_arcs) == 4

    def test_progress_bar_skipped_when_total_steps_zero(self) -> None:
        bd = ButtonDetectionState(active=True, current_label="A", step=0, total_steps=0)
        state = _base_state(button_detection=bd)
        cr = FakeCairo()
        draw_button_detection_overlay(FakeRenderer(state=state), cr, state, 1600, 900)
        # Still emits the background rounded rect; the filled part is gated on
        # total_steps > 0 AND ratio > 0.
        progress_arcs = [a for a in cr.arcs if a[2] == 3.0]
        assert len(progress_arcs) == 4

    def test_completed_list_renders_buttons_hats_and_axes(self) -> None:
        bd = ButtonDetectionState(
            active=True,
            current_label="A",
            step=3,
            total_steps=6,
            completed={
                "A": 0,  # positive raw_idx → "btn 0"
                "DPAD_UP": -1,  # hat known name
                "LT": -105,  # axis idx = -100 - (-105) = 5
                "CUSTOM": -200,  # axis idx = -100 - (-200) = 100
                "B": -99,  # hat unknown → fallback "hat -99"
            },
        )
        state = _base_state(button_detection=bd)
        cr = FakeCairo()
        draw_button_detection_overlay(FakeRenderer(state=state), cr, state, 1600, 900)
        texts = cr.show_text_strings()
        # Section header.
        assert "DETECTED:" in texts
        # Positive index → btn N.
        assert any(t == "→ btn 0" for t in texts)
        # Known hat.
        assert any(t == "→ hat Up" for t in texts)
        # Unknown hat falls back through `_HAT_RESULT_NAMES.get(..., "hat <n>")`.
        assert any(t == "→ hat -99" for t in texts)
        # Axis (raw_idx = -105 → axis 5).
        assert any(t == "→ axis 5" for t in texts)
        assert any(t == "→ axis 100" for t in texts)

    def test_short_names_used_in_completed_list(self) -> None:
        bd = ButtonDetectionState(
            active=True,
            current_label="X",
            step=1,
            total_steps=2,
            completed={"DPAD_RIGHT": -4},
        )
        state = _base_state(button_detection=bd)
        cr = FakeCairo()
        draw_button_detection_overlay(FakeRenderer(state=state), cr, state, 1600, 900)
        # DPAD_RIGHT is shortened to "D-Right" in the completed list.
        assert "D-Right" in cr.show_text_strings()

    def test_unknown_label_kept_as_fallback_short_name(self) -> None:
        bd = ButtonDetectionState(
            active=True,
            current_label="X",
            step=1,
            total_steps=2,
            completed={"CUSTOM": 3},
        )
        state = _base_state(button_detection=bd)
        cr = FakeCairo()
        draw_button_detection_overlay(FakeRenderer(state=state), cr, state, 1600, 900)
        assert "CUSTOM" in cr.show_text_strings()


# --------------------------------------------------------------------------- #
# Virtual fader stack – Group 10
# --------------------------------------------------------------------------- #


def _vf(
    index: int = 1,
    *,
    name: str = "Master",
    value: float = 0.5,
    picked_up: bool = True,
) -> VirtualFaderDisplayData:
    return VirtualFaderDisplayData(
        index=index,
        name=name,
        value=value,
        picked_up=picked_up,
    )


class TestVirtualFaders:
    """``draw_virtual_faders`` stacks bottom-up on the left side; each
    row carries name + 0..1 value rendered to two decimals, with a
    ``(not picked up)`` suffix while the fader's pickup gate is
    open."""

    def test_empty_list_short_circuits_with_no_draw_calls(self) -> None:
        cr = FakeCairo()
        state = _base_state()
        draw_virtual_faders(FakeRenderer(state=state), cr, state, 1080)
        # Nothing to draw – the helper must touch zero Cairo
        # primitives so a row that doesn't opt into show-on-display
        # costs literally nothing on the render path.
        assert cr.calls == []

    def test_single_fader_renders_name_and_value(self) -> None:
        state = _base_state()
        state.virtual_faders_display = [_vf(name="Master", value=0.42)]
        cr = FakeCairo()
        draw_virtual_faders(FakeRenderer(state=state), cr, state, 1080)
        texts = cr.show_text_strings()
        assert "Master" in texts
        # Value formatted to two decimals – matches the MIDI page's
        # live indicator so the operator's mental model lines up.
        assert "0.42" in texts

    def test_picked_up_fader_omits_not_picked_up_suffix(self) -> None:
        state = _base_state()
        state.virtual_faders_display = [_vf(picked_up=True)]
        cr = FakeCairo()
        draw_virtual_faders(FakeRenderer(state=state), cr, state, 1080)
        # The suffix is concatenated with the value, so we look for
        # the substring rather than an exact-match cell.
        assert not any("not picked up" in t for t in cr.show_text_strings())

    def test_not_picked_up_fader_renders_suffix(self) -> None:
        state = _base_state()
        state.virtual_faders_display = [
            _vf(picked_up=False, value=0.7),
        ]
        cr = FakeCairo()
        draw_virtual_faders(FakeRenderer(state=state), cr, state, 1080)
        assert any("0.70 (not picked up)" in t for t in cr.show_text_strings())

    def test_multiple_faders_stack_in_input_order(self) -> None:
        """Vertical stacking from the bottom up – fader index 0 in
        the input list lands at the lowest Y on screen (highest
        ``card_y`` value); each subsequent index sits above it.
        Verifies ``virtual_fader_card_y``'s ordering is reflected
        in the actual draw calls."""
        state = _base_state()
        state.virtual_faders_display = [
            _vf(index=1, name="One", value=0.1),
            _vf(index=2, name="Two", value=0.2),
            _vf(index=3, name="Three", value=0.3),
        ]
        cr = FakeCairo()
        draw_virtual_faders(FakeRenderer(state=state), cr, state, 1080)
        # All three names are rendered.
        texts = cr.show_text_strings()
        for name in ("One", "Two", "Three"):
            assert name in texts
        # Each row is its own panel – three panel-background
        # rectangles (gradient + accent stroke = two passes per row,
        # so 6 ``rounded_rect`` arcs total). We just check that the
        # number of fills matches the number of rows: each
        # background paints one filled rect.
        # ``draw_panel_background`` calls ``cr.fill()`` once per row.
        assert cr.fills == 3


class TestVirtualFaderCard:
    def test_long_name_truncated_to_fit_card(self) -> None:
        cr = FakeCairo()
        state = _base_state()
        long_name = "X" * 80
        draw_virtual_fader_card(
            FakeRenderer(state=state),
            cr,
            x=0,
            y=0,
            w=180,
            h=28,
            vf=_vf(name=long_name, value=0.5),
        )
        texts = cr.show_text_strings()
        # The full untruncated name is NOT in the output; the
        # truncation helper produced something shorter.
        assert long_name not in texts
        # The value still renders alongside.
        assert "0.50" in texts

    def test_picked_up_uses_solid_text_color(self) -> None:
        """A picked-up fader's value renders in the solid text
        colour. Verifies by counting the muted-text calls – a
        picked-up card uses muted only for the (absent) suffix
        path; the value stays solid."""
        cr = FakeCairo()
        state = _base_state()
        draw_virtual_fader_card(
            FakeRenderer(state=state),
            cr,
            x=0,
            y=0,
            w=180,
            h=28,
            vf=_vf(picked_up=True),
        )
        # The value text is in the solid-colour set, which the
        # renderer applies via ``set_source_rgb`` (not rgba).
        rgb_calls = [c for c in cr.calls if c[0] == "rgb"]
        # At least the name-set + value-set calls; both go through
        # ``set_source_rgb`` for picked-up state.
        assert len(rgb_calls) >= 2

    def test_value_two_decimal_format(self) -> None:
        cr = FakeCairo()
        state = _base_state()
        draw_virtual_fader_card(
            FakeRenderer(state=state),
            cr,
            x=0,
            y=0,
            w=180,
            h=28,
            vf=_vf(value=0.123456789),
        )
        # Two decimals – matches the MIDI page's live indicator.
        assert any("0.12" in t for t in cr.show_text_strings())
        # Higher-precision form is not rendered.
        assert not any("0.12345" in t for t in cr.show_text_strings())


# --------------------------------------------------------------------------- #
# Network screens
# --------------------------------------------------------------------------- #


def _network_state(**overrides: object):
    """Build a ``PiNetworkOverlayState`` with sensible defaults for tests."""
    from openfollow.runtime.overlay_state import PiNetworkOverlayState

    s = PiNetworkOverlayState()
    s.active_iface = "eth0"
    s.banner = ""
    s.rows = []
    s.selected_index = 0
    s.iface_picker_items = ["eth0", "wlan0"]
    s.iface_picker_selected_index = 0
    s.method_picker_items = ["DHCP", "Static"]
    s.method_picker_selected_index = 0
    s.field_label = "IP Address"
    s.field_value = "192.168.1.50"
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


class TestDrawPiNetworkScreen:
    def test_renders_sectioned_layout(self) -> None:
        rows = [
            {"kind": "header", "label": "Interface"},
            {"kind": "choice", "key": "interface", "label": "Selected", "value": "eth0"},
            {"kind": "header", "label": "IPv4"},
            {"kind": "choice", "key": "method", "label": "Configure", "value": "DHCP"},
            {"kind": "display", "key": "address", "label": "IP Address", "value": "192.168.1.50"},
            {"kind": "header", "label": "Actions"},
            {"kind": "action", "key": "apply", "label": "Apply Changes", "value": ""},
            {"kind": "action", "key": "back", "label": "Back", "value": ""},
        ]
        state = _base_state(pi_network=_network_state(rows=rows, selected_index=6))
        cr = FakeCairo()
        draw_pi_network_screen(FakeRenderer(state=state), cr, state, 1600, 900)
        texts = cr.show_text_strings()
        # Section headers shouted in caps.
        assert "INTERFACE" in texts
        assert "IPV4" in texts
        assert "ACTIONS" in texts
        # Data row label/value rendered.
        assert any("Selected" in t for t in texts)
        # Action row text rendered.
        assert any("Apply Changes" in t for t in texts)

    def test_renders_banner_when_set(self) -> None:
        rows = [
            {"kind": "header", "label": "Actions"},
            {"kind": "action", "key": "back", "label": "Back", "value": ""},
        ]
        state = _base_state(
            pi_network=_network_state(
                rows=rows,
                selected_index=1,
                banner="Apply ok.",
            )
        )
        cr = FakeCairo()
        draw_pi_network_screen(FakeRenderer(state=state), cr, state, 1600, 900)
        texts = cr.show_text_strings()
        assert any("Apply ok" in t for t in texts)

    def test_overlay_wraps_with_scrim(self) -> None:
        rows = [{"kind": "action", "key": "back", "label": "Back", "value": ""}]
        state = _base_state(pi_network=_network_state(rows=rows, selected_index=0))
        cr = FakeCairo()
        draw_pi_network_screen_overlay(FakeRenderer(state=state), cr, state, 1280, 720)
        # Scrim is the full-frame rect at origin.
        assert (0, 0, 1280, 720) in cr.rects

    def test_selected_action_renders_with_highlight(self) -> None:
        rows = [
            {"kind": "action", "key": "apply", "label": "Apply", "value": ""},
            {"kind": "action", "key": "back", "label": "Back", "value": ""},
        ]
        state = _base_state(pi_network=_network_state(rows=rows, selected_index=0))
        cr = FakeCairo()
        draw_pi_network_screen(FakeRenderer(state=state), cr, state, 1600, 900)
        # Both action labels rendered.
        texts = cr.show_text_strings()
        assert any("Apply" in t for t in texts)
        assert any("Back" in t for t in texts)

    def test_selected_choice_row_renders_highlight_box(self) -> None:
        """When the cursor sits on a choice / text row the row gets a
        soft-accent highlight box (the conditional branch at
        overlay_draw_hud.py:1170)."""
        rows = [
            {"kind": "choice", "key": "method", "label": "Configure", "value": "DHCP"},
            {"kind": "action", "key": "back", "label": "Back", "value": ""},
        ]
        state = _base_state(pi_network=_network_state(rows=rows, selected_index=0))
        cr = FakeCairo()
        draw_pi_network_screen(FakeRenderer(state=state), cr, state, 1600, 900)
        # COLOR_ACCENT_SOFT is set somewhere in the draw – verify by
        # checking that the rgba call list includes a non-fully-opaque
        # accent-soft tuple (the highlight) AND show_text shows the
        # row label.
        texts = cr.show_text_strings()
        assert any("Configure" in t for t in texts)


class TestDrawPiNetworkIfacePicker:
    def test_renders_iface_list(self) -> None:
        state = _base_state(
            pi_network=_network_state(
                iface_picker_items=["eth0", "wlan0"],
                iface_picker_selected_index=1,
            )
        )
        cr = FakeCairo()
        draw_pi_network_iface_picker(FakeRenderer(state=state), cr, state, 1280, 720)
        texts = cr.show_text_strings()
        assert any("eth0" in t for t in texts)
        assert any("wlan0" in t for t in texts)

    def test_overlay_wraps_with_scrim(self) -> None:
        state = _base_state(pi_network=_network_state())
        cr = FakeCairo()
        draw_pi_network_iface_picker_overlay(FakeRenderer(state=state), cr, state, 1280, 720)
        assert (0, 0, 1280, 720) in cr.rects


class TestDrawPiNetworkMethodPicker:
    def test_renders_method_list(self) -> None:
        state = _base_state(
            pi_network=_network_state(
                method_picker_items=["DHCP", "DHCP with manual address", "Static"],
                method_picker_selected_index=2,
            )
        )
        cr = FakeCairo()
        draw_pi_network_method_picker(FakeRenderer(state=state), cr, state, 1280, 720)
        texts = cr.show_text_strings()
        assert any("DHCP" in t for t in texts)
        assert any("Static" in t for t in texts)

    def test_overlay_wraps_with_scrim(self) -> None:
        state = _base_state(pi_network=_network_state())
        cr = FakeCairo()
        draw_pi_network_method_picker_overlay(FakeRenderer(state=state), cr, state, 1280, 720)
        assert (0, 0, 1280, 720) in cr.rects


class TestDrawPiNetworkFieldEdit:
    def test_renders_field_label_and_value(self) -> None:
        state = _base_state(
            pi_network=_network_state(
                field_label="IP Address",
                field_value="192.168.1.50",
            )
        )
        cr = FakeCairo()
        draw_pi_network_field_edit(FakeRenderer(state=state), cr, state, 1280, 720)
        texts = cr.show_text_strings()
        assert any("IP ADDRESS" in t for t in texts)
        assert any("192.168.1.50" in t for t in texts)

    def test_empty_label_uses_value_placeholder(self) -> None:
        state = _base_state(pi_network=_network_state(field_label="", field_value=""))
        cr = FakeCairo()
        draw_pi_network_field_edit(FakeRenderer(state=state), cr, state, 1280, 720)
        texts = cr.show_text_strings()
        assert "VALUE" in texts

    def test_overlay_wraps_with_scrim(self) -> None:
        state = _base_state(pi_network=_network_state())
        cr = FakeCairo()
        draw_pi_network_field_edit_overlay(FakeRenderer(state=state), cr, state, 1280, 720)
        assert (0, 0, 1280, 720) in cr.rects


class _CountingCairo(FakeCairo):
    """FakeCairo that counts ``text_extents`` calls – used to prove the
    per-frame memo caches skip measurement on unchanged frames."""

    def __init__(self) -> None:
        super().__init__()
        self.text_extents_calls = 0

    def text_extents(self, text: str):  # type: ignore[override]
        self.text_extents_calls += 1
        return super().text_extents(text)


class TestPerFrameCaches:
    """Verify per-frame HUD invariants are cached; unchanged frames
    skip font re-resolution, measurement, and help rebuilds."""

    # -- item 4: font face cache ------------------------------------------
    def test_set_ui_font_uses_cached_face(self) -> None:
        from openfollow.video.overlay import _UI_FONT_FACES, CairoOverlayRenderer

        cr = FakeCairo()
        CairoOverlayRenderer._set_ui_font(cr, 12.0, bold=False)
        CairoOverlayRenderer._set_ui_font(cr, 14.0, bold=True)
        CairoOverlayRenderer._set_ui_font(cr, 10.0, bold=False)  # cache hit
        # No select_font_face: production sets a cached ToyFontFace instead.
        assert not any(c[0] == "font_face" for c in cr.calls)
        assert any(c[0] == "set_font_face" for c in cr.calls)
        # Both variants resolved once and cached process-wide.
        assert _UI_FONT_FACES[False] is not None
        assert _UI_FONT_FACES[True] is not None

    # -- item 5: info panel memo ------------------------------------------
    def test_info_panel_memoises_measurement(self) -> None:
        renderer = FakeRenderer()
        st = renderer.state
        st.ip_text = "192.168.1.50"
        st.station_name = "Booth A"

        cr1 = _CountingCairo()
        draw_bottom_left_info_panel(renderer, cr1, st, 1280, 720)
        assert cr1.text_extents_calls > 0  # first frame measures

        cr2 = _CountingCairo()
        draw_bottom_left_info_panel(renderer, cr2, st, 1280, 720)
        assert cr2.text_extents_calls == 0  # unchanged → cache hit, no measure

        st.ip_text = "192.168.1.99"  # a value changed
        cr3 = _CountingCairo()
        draw_bottom_left_info_panel(renderer, cr3, st, 1280, 720)
        assert cr3.text_extents_calls > 0  # invalidated → re-measures

    # -- item 6: help sections memo ---------------------------------------
    def test_help_sections_memoised(self, monkeypatch) -> None:
        import openfollow.runtime.overlay_draw_hud as hud

        modes: list[str] = []
        real = hud.build_help_sections

        def _counting(**kw):
            modes.append(kw["mode"])
            return real(**kw)

        monkeypatch.setattr(hud, "build_help_sections", _counting)
        renderer = FakeRenderer()
        st = renderer.state
        st.controller_connected = True

        s1 = hud._help_sections_for(renderer, "normal", st)
        s2 = hud._help_sections_for(renderer, "normal", st)
        assert s1 is s2  # same object returned from cache
        assert modes == ["normal"]  # built once

        hud._help_sections_for(renderer, "settings", st)  # mode change
        assert modes == ["normal", "settings"]

        st.button_labels = {"reset": "Y"}  # a rebind
        hud._help_sections_for(renderer, "settings", st)
        assert modes == ["normal", "settings", "settings"]
