# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""On-device mode/overlay state machine: input routing, Settings menu, source
selection, interface picker, source-type switcher, URL editor, field-choice
picker, embedded browser, button-detection wizard, and startup video banner.

Each mode exposes enter/exit/process/handle functions keyed off ``OpenFollowApp``
flags; ``process_input`` gates direct marker control behind the active modal."""

from __future__ import annotations

import logging
import time
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from openfollow.configuration import save_config

if TYPE_CHECKING:
    from openfollow.app import OpenFollowApp

logger = logging.getLogger(__name__)


def process_input(app: OpenFollowApp, dt: float) -> None:
    """Process input through the InputManager."""
    if app._input_manager is None:
        return

    # A modal/overlay state suspends direct marker control. Track that edge so
    # that when control RETURNS to the marker, any key still tracked as held
    # (e.g. held across the menu, or whose key-up GTK dropped) is dropped
    # instead of drifting the marker. Reads are defensive so partial test apps
    # don't trip; the clear runs only on the modal->marker-control transition,
    # where ``_input_manager`` is guaranteed present. Mirrors the early-return
    # guards below – keep in sync when adding a modal. (A key-up dropped during
    # continuous control is out of scope; that awaits the planned evdev poller.)
    modal_active = (
        getattr(app, "_button_detection", None) is not None
        or getattr(app, "_settings_menu_active", False)
        or getattr(app, "_about_active", False)
        or getattr(app, "_pi_network_field_edit_active", False)
        or getattr(app, "_pi_network_method_picker_active", False)
        or getattr(app, "_pi_network_iface_picker_active", False)
        or getattr(app, "_pi_network_active", False)
        or getattr(getattr(app, "_video_receiver", None), "source_selection_active", False)
        or getattr(app, "_iface_selection_active", False)
        or getattr(app, "_source_type_selection_active", False)
        or getattr(app, "_field_choice_active", False)
        or getattr(app, "_url_editor_active", False)
        or getattr(app, "_browser_active", False)
    )
    if modal_active:
        app._marker_control_suspended = True
    elif getattr(app, "_marker_control_suspended", False):
        app._input_manager.keyboard_handler.clear()
        app._marker_control_suspended = False

    # Button detection wizard takes exclusive control of input.
    if app._button_detection is not None:
        app._process_button_detection()
        return

    keys = app._input_manager.keyboard_handler.keys

    if app._settings_menu_active:
        app._process_settings_menu_input()
        return

    # About screen: read-only, consumes input exclusively so confirm/cancel
    # backs out instead of leaking to marker movement beneath it.
    if getattr(app, "_about_active", False):
        process_about_input(app)
        return

    # Network submenu + Pi network sub-screens. Order matters – deeper
    # sub-states (field edit, method picker, iface picker) take priority over
    # the parent screen.
    if getattr(app, "_pi_network_field_edit_active", False):
        # Field editor: only the gamepad Cancel button has meaning; typing
        # remains keyboard-only. Without this poll a gamepad-only operator
        # would be stranded inside the editor with no way out.
        from openfollow.runtime.app_modes_network import process_pi_network_field_edit_input

        process_pi_network_field_edit_input(app)
        return
    if getattr(app, "_pi_network_method_picker_active", False):
        from openfollow.runtime.app_modes_network import process_pi_network_method_picker_input

        process_pi_network_method_picker_input(app)
        return
    if getattr(app, "_pi_network_iface_picker_active", False):
        from openfollow.runtime.app_modes_network import process_pi_network_iface_picker_input

        process_pi_network_iface_picker_input(app)
        return
    if getattr(app, "_pi_network_active", False):
        from openfollow.runtime.app_modes_network import process_pi_network_input

        process_pi_network_input(app)
        return

    if app._video_receiver is not None and app._video_receiver.source_selection_active:
        app._process_source_selection_input()
        return

    if app._iface_selection_active:
        app._process_iface_selection_input()
        return

    if app._source_type_selection_active:
        app._process_source_type_selection_input()
        return

    if app._field_choice_active:
        app._process_field_choice_picker_input()
        return

    # URL editor consumes input exclusively. Typing characters has no
    # gamepad equivalent, but a gamepad-only operator still needs the
    # Cancel button to back out – without this poll they'd be stranded
    # inside the dialog with no way to dismiss it.
    if app._url_editor_active:
        process_url_editor_input(app)
        return

    # Browser overlay: the WebView's own GTK key handler intercepts Esc, but
    # gamepad-only operators have no key path out. Route the gamepad poll into
    # ``process_browser_input`` so cancel (B) dismisses the overlay; everything
    # else (marker movement, settings-menu-toggle) stays gated by the early
    # return.
    if app._browser_active:
        app._process_browser_input()
        return

    key_settings = app._config.controller.key_settings
    if key_settings and key_settings in keys and not app._settings_key_pressed:
        app._settings_key_pressed = True
        app._enter_settings_menu()
        return
    elif not key_settings or key_settings not in keys:
        app._settings_key_pressed = False

    result = app._input_manager.update(dt)
    if result.settings_open_pressed:
        app._enter_settings_menu()
        return
    if result.toggle_help_pressed:
        app._show_hud_help = not app._show_hud_help
    if result.toggle_zones_pressed:
        _toggle_zone_overlay(app)
    if result.next_marker_pressed:
        cycle_marker(app, +1)
    if result.prev_marker_pressed:
        cycle_marker(app, -1)
    if result.clear_messages_pressed:
        app._clear_operator_messages()


def _toggle_zone_overlay(app: OpenFollowApp) -> None:
    """Flip trigger_zones.show_overlay and persist the change."""
    cfg = app._config.trigger_zones
    cfg.show_overlay = not cfg.show_overlay
    try:
        save_config(app._config, app._config_path)
    except Exception as error:  # noqa: BLE001
        logger.warning("Failed to persist zone overlay toggle: %s", error)


def _persist_config(app: OpenFollowApp) -> bool:
    """Save config + advance mtime; log and return False on failure (no raise)."""
    try:
        save_config(app._config, app._config_path)
    except Exception:
        logger.exception("Failed to persist config.")
        return False
    app._config_mtime = app._get_config_mtime()
    return True


def process_source_selection_input(app: OpenFollowApp) -> None:
    """Read gamepad input and apply to source selection."""
    # Source-selection mode is only entered when both an InputManager
    # and a video receiver have been wired up by ``run()``; the early
    # return mirrors ``process_input``'s top-of-function pattern and
    # documents the runtime invariant that mypy's strict narrowing
    # can't otherwise see.
    input_manager = app._input_manager
    video_receiver = app._video_receiver
    if input_manager is None or video_receiver is None:
        return
    try:
        src_input = input_manager.gamepad_handler.read_source_selection_input()

        if src_input.up_pressed:
            video_receiver.select_source_up()
        if src_input.down_pressed:
            video_receiver.select_source_down()
        if src_input.confirm_pressed:
            video_receiver.confirm_source_selection()
        if src_input.cancel_pressed:
            # Deactivate the picker before opening Settings – without
            # ``exit_source_selection`` the receiver keeps
            # ``source_selection_active=True`` and would silently
            # drop the operator back into the hidden picker the
            # moment they close Settings.
            video_receiver.exit_source_selection()
            _back_to_settings(app)
    except Exception as error:
        logger.warning("Source selection input error: %s", error)


def _back_to_settings(app: OpenFollowApp) -> None:
    """Re-open the Settings menu after a sub-screen Esc / cancel.

    Every sub-screen (iface / source-type / URL editor / source picker /
    browser / button detection) is reachable via the Settings menu, so
    cancelling them lands the operator back where they started – not on the
    bare video surface. ``Esc`` only closes the Settings menu itself when
    invoked from the main list.
    """
    app._enter_settings_menu()


def process_iface_selection_input(app: OpenFollowApp) -> None:
    """Read gamepad input and apply to network interface selection."""
    input_manager = app._input_manager
    if input_manager is None:
        return
    try:
        inp = input_manager.gamepad_handler.read_source_selection_input()

        if not app._available_interfaces:
            return
        if inp.up_pressed:
            app._selected_iface_index = max(0, app._selected_iface_index - 1)
        if inp.down_pressed:
            app._selected_iface_index = min(
                len(app._available_interfaces) - 1,
                app._selected_iface_index + 1,
            )
        if inp.confirm_pressed:
            app._confirm_iface_selection()
        if inp.cancel_pressed:
            app._iface_selection_active = False
            _back_to_settings(app)
    except Exception as error:
        logger.warning("Interface selection input error: %s", error)


_SETTINGS_MENU_ITEMS: tuple[tuple[str, str], ...] = (
    ("Network", "network"),
    # Single guided entry point for everything video: the operator picks a
    # type and is automatically routed to the right next step (URL editor for
    # RTSP/SRT/RTP/NDI, source picker for discovery-capable plugins like NDI).
    ("Change Video Source", "change_video_source"),
    ("Button Detection", "button_detection"),
    ("Open Web UI", "web_ui"),
    ("Restart", "restart"),
    # Read-only license/version screen. Reachable without the embedded WebKit
    # browser so the AGPLv3 notice is always available on the device.
    ("About", "about"),
)


def _first_video_text_field(video_source_type: str) -> tuple[str, str] | None:
    """Return ``(field_name, label)`` for the active plugin's first
    string ``ConfigField`` *without* enum choices, or ``None``.

    Used by the on-device URL editor to pick which field to open. Numeric
    fields and string fields that declare ``choices`` (enum-style) fall
    through – choices are handled by :func:`_first_video_choice_field` which
    routes to the picker.
    """
    from openfollow.video.inputs import get_input_class

    cls = get_input_class(video_source_type)
    if cls is None:
        return None
    for f in cls.config_fields():
        if f.type is str and not f.choices and f.device_editable:
            return f.name, f.label
    return None


def _first_video_choice_field(
    video_source_type: str,
) -> tuple[str, str, tuple[tuple[str, str], ...]] | None:
    """Return ``(field_name, label, choices)`` for the active plugin's
    first string ``ConfigField`` that declares enum-style ``choices``,
    or ``None``.

    Used by the on-device flow to open a list picker instead of the
    free-text URL editor for fields whose valid values are a small,
    fixed set (e.g. testpattern's ``grey`` / ``stage``).
    """
    from openfollow.video.inputs import get_input_class

    cls = get_input_class(video_source_type)
    if cls is None:
        return None
    for f in cls.config_fields():
        if f.type is str and f.choices and f.device_editable:
            return f.name, f.label, tuple(f.choices)
    return None


def _web_ui_disabled_reason() -> str:
    """Explain why "Open Web UI" is disabled – operators see this
    in place of the generic "(unavailable)" suffix.

    macOS support for WebKit2GTK via Homebrew is brittle (Linux-first
    library, sometimes broken on Apple Silicon), so we call it out
    explicitly rather than leaving operators guessing about a missing
    typelib. On Linux without the typelib installed, name BOTH apt
    package candidates: ``gir1.2-webkit2-4.1`` is current on Debian
    Bookworm / Raspberry Pi OS, ``gir1.2-webkit2-4.0`` is the older
    name still shipped on Bullseye and some derivatives. The runtime
    import tries 4.1 first and falls back to 4.0, so either package
    name unblocks the menu item.
    """
    import sys

    if sys.platform == "darwin":
        return "Linux only"
    return "install gir1.2-webkit2-4.1 (or -4.0 on Bullseye)"


def build_settings_menu_items(
    app: OpenFollowApp,
) -> tuple[list[str], list[bool], list[str]]:
    """Return (labels, enabled_flags, disabled_reasons) for the Settings menu.

    Items whose prerequisites aren't met render as disabled so the menu
    shape stays stable regardless of runtime state. ``disabled_reasons``
    is a parallel list; each non-empty entry overrides the generic
    "(unavailable)" suffix the draw layer otherwise appends – used to
    tell operators *why* a row is greyed (e.g. "Linux only" for the
    embedded browser on macOS).
    """
    labels: list[str] = []
    enabled: list[bool] = []
    reasons: list[str] = []
    has_controller = app._input_manager is not None and bool(app._input_manager.gamepad_handler.joysticks)
    has_video = app._video_receiver is not None
    from openfollow.runtime import webkit_browser

    has_browser = webkit_browser.AVAILABLE
    for label, action in _SETTINGS_MENU_ITEMS:
        labels.append(label)
        reason = ""
        if action == "button_detection":
            is_enabled = has_controller
        elif action == "change_video_source":
            # Enabled whenever a video receiver is wired – the picker
            # itself filters available plugins via
            # ``get_available_registry``, so we don't need to gate on
            # specific capabilities here.
            is_enabled = has_video
        elif action == "web_ui":
            is_enabled = has_browser
            if not is_enabled:
                reason = _web_ui_disabled_reason()
        else:
            is_enabled = True
        enabled.append(is_enabled)
        reasons.append(reason)
    return labels, enabled, reasons


def _settings_menu_action(app: OpenFollowApp, index: int) -> str | None:
    if not 0 <= index < len(_SETTINGS_MENU_ITEMS):
        return None
    _, action = _SETTINGS_MENU_ITEMS[index]
    return action


def enter_settings_menu(app: OpenFollowApp, *, banner: str = "") -> None:
    """Open the Settings menu overlay.

    ``banner`` is stored on the app and rendered inside the menu's
    red-bordered error box (word-wrapped, bold body) – used by the
    startup auto-open and the runtime ``check_video_disconnect_banner``
    so the operator sees *why* the menu auto-opened. The subtitle row
    keeps its static "Open a sub-screen." text regardless.
    """
    if app._settings_menu_active:
        return
    app._settings_menu_active = True
    app._settings_menu_index = 0
    app._settings_menu_banner = banner


def exit_settings_menu(app: OpenFollowApp) -> None:
    app._settings_menu_active = False
    app._settings_menu_banner = ""


def enter_about(app: OpenFollowApp) -> None:
    """Open the read-only About screen.

    A Cairo-drawn modal showing program name, version, copyright, the
    AGPLv3-or-later notice + no-warranty line, and source/license links.
    Deliberately self-contained (no WebKit dependency) so the legal notice
    is reachable on the device even when the embedded browser isn't
    available. There's nothing to edit, so the only action is to back out.
    """
    app._about_active = True


def exit_about(app: OpenFollowApp) -> None:
    """Close the About screen and return to the Settings menu."""
    app._about_active = False
    _back_to_settings(app)


def process_about_input(app: OpenFollowApp) -> None:
    """Read gamepad input for the About screen – confirm or cancel backs out."""
    input_manager = app._input_manager
    if input_manager is None:
        return
    try:
        inp = input_manager.gamepad_handler.read_settings_menu_input()
    except Exception as error:  # pragma: no cover - defensive, mirrors siblings
        logger.warning("About screen input error: %s", error)
        return
    if inp.confirm_pressed or inp.cancel_pressed:
        exit_about(app)


def _settings_menu_move(app: OpenFollowApp, step: int) -> None:
    _, enabled, _reasons = build_settings_menu_items(app)
    if not enabled:
        return
    idx = app._settings_menu_index
    total = len(enabled)
    # pragma: no branch – the loop-completes-without-finding-enabled
    # arm fires only when every menu entry is False, which the
    # default settings menu (Network / Restart are always enabled)
    # cannot produce.
    for _ in range(total):  # pragma: no branch
        idx = (idx + step) % total
        if enabled[idx]:
            app._settings_menu_index = idx
            return


def _settings_menu_confirm(app: OpenFollowApp) -> None:
    _, enabled, _reasons = build_settings_menu_items(app)
    idx = app._settings_menu_index
    if not 0 <= idx < len(enabled) or not enabled[idx]:
        return
    action = _settings_menu_action(app, idx)
    exit_settings_menu(app)
    if action == "network":
        from openfollow.runtime.app_modes_network import enter_pi_network

        enter_pi_network(app)
    elif action == "change_video_source":
        app._enter_source_type_selection()
    elif action == "button_detection":
        app._enter_button_detection()
    elif action == "web_ui":
        app._enter_browser()
    elif action == "restart":
        app._restart_app()
    # pragma: no branch – exhaustive elif chain over the static action set
    # (network / change_video_source / button_detection / web_ui / restart /
    # about); the final arm always matches, so there's no fall-through.
    elif action == "about":  # pragma: no branch
        enter_about(app)


def process_settings_menu_input(app: OpenFollowApp) -> None:
    """Read gamepad input for the Settings menu."""
    input_manager = app._input_manager
    if input_manager is None:
        return
    try:
        inp = input_manager.gamepad_handler.read_settings_menu_input()
    except Exception as error:
        logger.warning("Settings menu input error: %s", error)
        return
    if inp.up_pressed:
        _settings_menu_move(app, -1)
    if inp.down_pressed:
        _settings_menu_move(app, +1)
    if inp.confirm_pressed:
        _settings_menu_confirm(app)
    elif inp.cancel_pressed:
        exit_settings_menu(app)


def normalize_key(key: str) -> str:
    """Normalize single-char keys to lowercase for consistent lookup."""
    if len(key) == 1:
        return key.lower()
    return key


def cycle_marker(app: OpenFollowApp, direction: int) -> None:
    """Cycle controlled marker selection by *direction* (+1 next, -1 prev)."""
    if not app._controlled_ids:
        return
    selected = app._selected_id
    if selected is None:
        # No current selection → wrap so direction +1 lands on idx 0
        # (first) and direction -1 lands on the last entry. Same
        # outcome the prior ``list.index(None)`` produced via the
        # ValueError branch – ``list.index`` doesn't accept ``None``
        # under strict typing, so we narrow first.
        idx = -1 if direction > 0 else 0
    else:
        try:
            idx = app._controlled_ids.index(selected)
        except ValueError:
            idx = -1 if direction > 0 else 0
    step = 1 if direction > 0 else -1
    app._selected_id = app._controlled_ids[(idx + step) % len(app._controlled_ids)]


def adjust_move_speed(
    app: OpenFollowApp,
    direction: int,
    marker_id: int | None = None,
) -> None:
    """Increase (+1) or decrease (-1) the per-marker move_speed with streak acceleration.

    ``marker_id`` resolves to ``app._selected_id`` when omitted (keyboard R/T
    path); the gamepad bumper path passes the controller-routed marker id
    explicitly via the resolver. When neither resolves to a marker, the call
    is a no-op – there is no global speed to adjust.

    Streak state is keyed by the target ``marker_id``. Each controller
    slot-maps to a distinct marker, so the tap-streak – and the resulting
    1x/3x/8x multiplier – stays independent per controller: one operator
    ramping a pad to the 8x tier no longer leaks that multiplier to another
    pad's next press, and one operator's streak isn't reset by another nudging
    a different marker. Reversing ``direction`` clears that marker's streak so
    backing off by one tic after a fast ramp doesn't overshoot at the ramp's
    multiplier.
    """
    if marker_id is None:
        marker_id = app._selected_id
    if marker_id is None:
        return

    now = time.monotonic()
    if direction != app._speed_key_last_dir.get(marker_id, 0):
        # Reversing direction starts a fresh tap-streak so the first press after
        # a reversal nudges by a single tic instead of inheriting the previous
        # direction's accelerated multiplier.
        streak = 0
    elif now - app._speed_key_last_t.get(marker_id, 0.0) < 0.75:
        streak = app._speed_key_streak.get(marker_id, 0) + 1
    else:
        streak = 0
    app._speed_key_streak[marker_id] = streak
    app._speed_key_last_t[marker_id] = now
    app._speed_key_last_dir[marker_id] = direction

    current = app.get_marker_move_speed(marker_id)
    base_step = 0.1
    if streak < 5:
        multiplier = 1
    elif streak < 10:
        multiplier = 3
    else:
        multiplier = 8
    step = base_step * multiplier
    if direction < 0:
        new_speed = max(app._config.marker.min_speed, round(current - step, 1))
    else:
        new_speed = min(app._config.marker.max_speed, round(current + step, 1))
    app._config.marker_move_speeds[marker_id] = new_speed


def handle_key_press(app: OpenFollowApp, key: str) -> None:
    """Handle a discrete key press (from polling edge detection)."""
    # Button detection wizard consumes all input (Escape handled in process_button_detection).
    if app._button_detection is not None:
        return

    if app._settings_menu_active:
        if key == "ArrowUp":
            _settings_menu_move(app, -1)
        elif key == "ArrowDown":
            _settings_menu_move(app, +1)
        elif key == "Enter":
            _settings_menu_confirm(app)
        elif key == "Escape":
            exit_settings_menu(app)
        return

    # About screen: read-only, so Enter or Escape returns to Settings.
    if getattr(app, "_about_active", False):
        if key in ("Enter", "Escape"):
            exit_about(app)
        return

    # Field editor text comes in via ``on_key_down`` (like the URL editor):
    # it needs the top-row digits, '.' and Backspace, none of which are in
    # ``POLLED_KEYS`` (which polls a-z, the modifiers, Space, the arrows,
    # Tab/Enter/Escape and the numpad). Early-return here so the polled keys
    # that ARE in that set don't double-insert or fire app shortcuts beneath
    # the editor.
    if getattr(app, "_pi_network_field_edit_active", False):
        return
    if getattr(app, "_pi_network_method_picker_active", False):
        from openfollow.runtime.app_modes_network import handle_pi_network_method_picker_key

        handle_pi_network_method_picker_key(app, key)
        return
    if getattr(app, "_pi_network_iface_picker_active", False):
        from openfollow.runtime.app_modes_network import handle_pi_network_iface_picker_key

        handle_pi_network_iface_picker_key(app, key)
        return
    if getattr(app, "_pi_network_active", False):
        from openfollow.runtime.app_modes_network import handle_pi_network_key

        handle_pi_network_key(app, key)
        return

    if app._video_receiver is not None and app._video_receiver.source_selection_active:
        if key == "ArrowUp":
            app._video_receiver.select_source_up()
        elif key == "ArrowDown":
            app._video_receiver.select_source_down()
        elif key == "Enter":
            # pragma: no branch – the Enter→confirm fallback only trips when
            # confirm returns False AND a stale source name remains, a state
            # the source-selection handler clears before exposing Enter.
            if not app._video_receiver.confirm_source_selection():  # pragma: no branch
                source_label = app._video_receiver.source_name
                if source_label:  # pragma: no branch
                    app._video_receiver.set_source(source_label)
        # pragma: no branch – source-selection Escape arm completes the elif chain.
        elif key == "Escape":  # pragma: no branch
            app._video_receiver.exit_source_selection()
            _back_to_settings(app)
        return

    if app._iface_selection_active:
        if not app._available_interfaces:
            return
        if key == "ArrowUp":
            app._selected_iface_index = max(0, app._selected_iface_index - 1)
        elif key == "ArrowDown":
            app._selected_iface_index = min(
                len(app._available_interfaces) - 1,
                app._selected_iface_index + 1,
            )
        # pragma: no branch – iface-selection Enter elif True arm fires only
        # after the ArrowUp/ArrowDown chain ran; existing tests confirm the
        # exhaustive-elif coverage.
        elif key == "Enter":  # pragma: no branch
            app._confirm_iface_selection()
        # pragma: no branch – iface-selection Escape arm.
        elif key == "Escape":  # pragma: no branch
            app._iface_selection_active = False
            _back_to_settings(app)
        return

    # URL editor input flows exclusively through ``on_key_down`` so it
    # can capture digits / punctuation / Backspace that aren't in
    # ``POLLED_KEYS``. Early-return here so the polled-key path
    # (``a``-``z``, Enter, Escape, …) doesn't double-insert characters
    # already written into the buffer by the GTK event handler.
    if app._url_editor_active:
        return

    # Browser overlay shadows main-mode input. The WebView's own
    # ``key-press-event`` handler intercepts Esc and dismisses the
    # overlay; everything else flows into the page. Polled letter
    # keys must not fire app-level shortcuts while the operator is
    # interacting with the web UI.
    if app._browser_active:
        return

    if app._source_type_selection_active:
        # Esc must always remain a way out, even when the registry
        # returned no plugins – otherwise an empty
        # ``_available_source_types`` (e.g. on a host without any
        # available video plugin) would trap the operator in the
        # modal. Matches the gamepad path's cancel-on-empty behaviour
        # in ``process_source_type_selection_input``.
        if key == "Escape":
            app._exit_source_type_selection()
            _back_to_settings(app)
            return
        if not app._available_source_types:
            return
        if key == "ArrowUp":
            app._selected_source_type_index = max(
                0,
                app._selected_source_type_index - 1,
            )
        elif key == "ArrowDown":
            app._selected_source_type_index = min(
                len(app._available_source_types) - 1,
                app._selected_source_type_index + 1,
            )
        elif key == "Enter":
            app._confirm_source_type_selection()
        return

    if app._field_choice_active:
        # Esc cancels with the same revert-on-auto-chain semantics as
        # the URL editor.
        if key == "Escape":
            app._cancel_field_choice_picker()
            return
        if not app._field_choice_items:
            return
        if key == "ArrowUp":
            app._field_choice_selected_index = max(
                0,
                app._field_choice_selected_index - 1,
            )
        elif key == "ArrowDown":
            app._field_choice_selected_index = min(
                len(app._field_choice_items) - 1,
                app._field_choice_selected_index + 1,
            )
        elif key == "Enter":
            app._confirm_field_choice_picker()
        return

    cc = app._config.controller
    if cc.key_next_marker and key == cc.key_next_marker:
        cycle_marker(app, +1)
    elif cc.key_prev_marker and key == cc.key_prev_marker:
        cycle_marker(app, -1)
    elif cc.key_reset and key == cc.key_reset and app._selected_id is not None and app._server is not None:
        marker = app._server.get_marker(app._selected_id)
        # pragma: no branch – _selected_id is set only when its marker
        # is registered with the server, so get_marker(_selected_id)
        # never returns None at the call site.
        if marker is not None:  # pragma: no branch
            marker.set_pos(*app._get_default_marker_position())
    elif cc.key_toggle_help and key == cc.key_toggle_help:
        app._show_hud_help = not app._show_hud_help
    elif cc.key_toggle_zones and key == cc.key_toggle_zones:
        _toggle_zone_overlay(app)
    elif cc.key_speed_down and key == cc.key_speed_down:
        app.adjust_move_speed(-1)
    elif cc.key_speed_up and key == cc.key_speed_up:
        app.adjust_move_speed(+1)
    elif cc.key_clear_messages and key == cc.key_clear_messages:
        app._clear_operator_messages()


def on_key_down(app: OpenFollowApp, event: dict[str, Any]) -> None:
    """Handle GTK key down events (fallback mode only for polled keys).

    Also handles edge-triggered shortcuts that don't belong in the polled
    movement loop: digit keys pick a marker directly, and Ctrl/Cmd+S writes
    the current config to disk.
    """
    key = app._normalize_key(event.get("key", ""))

    # URL editor / browser overlays own input exclusively. Skip the
    # forward to ``keyboard_handler.on_key_down`` so the polled-key
    # tracker doesn't latch keys as "pressed" while the overlay is
    # up – otherwise the matching ``on_key_up`` (also gated below) is
    # the only way they'd clear, and a release that lands after the
    # overlay closes leaves the key flagged pressed and can fire app
    # shortcuts on the next polled tick.
    if app._url_editor_active:
        # URL editor needs every printable key and Backspace, neither
        # of which flow through the polled discrete-key path
        # (``KeyboardHandler.POLLED_KEYS`` covers letters / arrows /
        # Tab / Enter / Escape but not digits / punctuation /
        # Backspace). Route everything to ``handle_url_editor_key``
        # and skip the digit-as-marker shortcut below so typing "1"
        # goes into the URL buffer instead of picking marker 0.
        handle_url_editor_key(app, key)
        return

    # Pi network field editor: same constraint as the URL editor – it
    # accepts the top-row digits, '.' and Backspace, none of which flow
    # through the polled discrete-key path. ``POLLED_KEYS`` polls a-z, the
    # modifiers, Space, the arrows, Tab/Enter/Escape and the numpad – but not
    # the top number/punctuation row or Backspace. Route GTK key events
    # straight to the field handler and skip the digit-as-marker shortcut
    # below so typing an IP octet edits the buffer instead of reselecting a
    # marker.
    if getattr(app, "_pi_network_field_edit_active", False):
        from openfollow.runtime.app_modes_network import handle_pi_network_field_edit_key

        handle_pi_network_field_edit_key(app, key)
        return

    # Field-choice picker also owns input exclusively. ArrowUp/Down/
    # Enter/Escape are handled on the polled path; the early-return
    # here keeps the digit-marker shortcut and Ctrl/Cmd+S save from
    # firing while the operator is choosing a value.
    if app._field_choice_active:
        return

    # Browser overlay: the WebView's own ``key-press-event`` handler
    # interprets keys for the rendered page and intercepts Esc to
    # dismiss. Skip the digit-marker shortcut and Ctrl/Cmd+S save
    # below so typing into a form field can't fire a side-effect on
    # the underlying app.
    if app._browser_active:
        return

    if app._input_manager is not None:
        app._input_manager.keyboard_handler.on_key_down(event)

    # A modal / picker owns the screen (settings menu, about, iface /
    # source-type / Pi-network pickers, source selection). The forward above
    # keeps in-modal navigation working, but the digit-marker reselect and
    # Ctrl/Cmd+S save below must not fire underneath it – mirror the gate the
    # polled handle_key_press path already applies.
    if _exclusive_mode_active(app):
        return

    if len(key) == 1 and key.isdigit() and key != "0":
        idx = int(key) - 1
        if idx < len(app._controlled_ids):
            app._selected_id = app._controlled_ids[idx]

    modifier_keys = app._input_manager.keyboard_handler.keys if app._input_manager is not None else set()
    if key == "s" and ("Control" in modifier_keys or "Meta" in modifier_keys) and app._camera is not None:
        app._config.camera = app._camera.to_config()
        # Route through _persist_config so a save failure (read-only fs, disk
        # full) is logged and swallowed like every other persist site, instead
        # of raising out of the GTK key handler.
        if _persist_config(app):
            logger.info("Config saved.")


def on_key_up(app: OpenFollowApp, event: dict[str, Any]) -> None:
    # Symmetric with ``on_key_down``: while an exclusive overlay owns
    # input, the polled-key tracker stays frozen so a release that
    # arrives after the overlay closes can't clear a key it never saw
    # pressed (which would otherwise underflow ``keys`` or strand a
    # different key as latched-pressed).
    if (
        app._url_editor_active
        or app._browser_active
        or app._field_choice_active
        or getattr(app, "_pi_network_field_edit_active", False)
    ):
        return
    if app._input_manager is not None:
        app._input_manager.keyboard_handler.on_key_up(event)


def on_wheel(app: OpenFollowApp, event: dict[str, Any]) -> None:
    """Mouse wheel – adjusts Z height when mouse input is active."""
    # A modal / overlay owns the screen (settings menu, about, pickers, source
    # selection, URL editor, browser) – suspend mouse-driven marker control so
    # a wheel underneath it can't shift the selected marker's Z height.
    if _exclusive_mode_active(app):
        return
    if app._input_manager is not None and app._config.controller.mouse_enabled:
        app._input_manager.mouse_handler.on_wheel(event.get("dy", 0))


def on_pointer_down(app: OpenFollowApp, event: dict[str, Any]) -> None:
    # Suspend marker control while any modal owns the screen (not just the
    # browser): a click would otherwise activate MouseHandler and reposition the
    # selected marker underneath a visible modal.
    if _exclusive_mode_active(app):
        return
    if app._input_manager is not None and app._config.controller.mouse_enabled:
        app._input_manager.mouse_handler.on_pointer_down(
            event.get("x", 0),
            event.get("y", 0),
            event.get("button", 0),
        )


def on_pointer_move(app: OpenFollowApp, event: dict[str, Any]) -> None:
    # Suspend marker control while any modal owns the screen.
    if _exclusive_mode_active(app):
        return
    # pragma: no branch – pointer events only flow through this function
    # while ``_input_manager`` is initialised; the existing mouse-enabled
    # tests cover the True direction. The False arm fires only during
    # the pre-init startup window where pointer events are also
    # explicitly drained by the GTK canvas.
    if app._input_manager is not None and app._config.controller.mouse_enabled:  # pragma: no branch
        app._input_manager.mouse_handler.on_pointer_move(
            event.get("x", 0),
            event.get("y", 0),
        )


def on_pointer_up(app: OpenFollowApp, event: dict[str, Any]) -> None:
    # Suspend marker control while any modal owns the screen.
    if _exclusive_mode_active(app):
        return
    # pragma: no branch – same reasoning as on_pointer_move above.
    if app._input_manager is not None and app._config.controller.mouse_enabled:  # pragma: no branch
        app._input_manager.mouse_handler.on_pointer_up(
            event.get("x", 0),
            event.get("y", 0),
            event.get("button", 0),
        )


def on_resize(app: OpenFollowApp, event: dict[str, Any]) -> None:
    # No-op on the runtime path. Kept as the documented hook so the
    # canvas-resize callback in ``runtime/app_orchestration.py`` has
    # a stable target – future on-device modes plug in here.
    return


def enter_source_selection(app: OpenFollowApp) -> None:
    """Enter source selection mode (only for inputs with discovery)."""
    if app._video_receiver is not None and app._video_receiver.has_source_selection:
        app._video_receiver.enter_source_selection()


def refresh_iface_list(app: OpenFollowApp) -> None:
    """Re-scan network interfaces, preserving the current selection."""
    from openfollow.net_utils import list_iface_ipv4

    prev_selected = (
        app._available_interfaces[app._selected_iface_index]
        if app._available_interfaces and app._selected_iface_index < len(app._available_interfaces)
        else None
    )
    # Work in iface names. ``""`` stays as the auto-detect option at the head
    # of the list.
    ifaces = [name for name, _ip in list_iface_ipv4()]
    app._available_interfaces = [""] + ifaces
    if prev_selected is not None and prev_selected in app._available_interfaces:
        app._selected_iface_index = app._available_interfaces.index(prev_selected)
    else:
        app._selected_iface_index = min(
            app._selected_iface_index,
            len(app._available_interfaces) - 1,
        )


def enter_iface_selection(app: OpenFollowApp) -> None:
    """Enter network interface selection mode.

    Picker storage is the iface name, but options still display the IP (with
    the iface name hinted alongside in the menu renderer) so the operator can
    pick visually by which network they want on the show. Seeds the selection
    to the currently-pinned iface so the menu doesn't visually reset.
    """
    from openfollow.net_utils import list_iface_ipv4

    ifaces = [name for name, _ip in list_iface_ipv4()]
    # ``""`` is the auto-detect option; always first so the operator
    # can clear an iface pin without picking a specific one.
    app._available_interfaces = [""] + ifaces
    current = app._config.psn_source_iface
    try:
        app._selected_iface_index = app._available_interfaces.index(current)
    except ValueError:
        app._selected_iface_index = 0
    app._iface_selection_active = True


def confirm_iface_selection(app: OpenFollowApp) -> None:
    """Apply the selected interface live (no restart).

    Store ``psn_source_iface`` (the stable name), then resolve to its current
    IPv4 and route through ``apply_psn_source_ip_change`` – same path the
    hot-reload dispatcher uses when the operator edits the web UI – so PSN
    input + output rebind in one transactional cycle. On failure, restore the
    prior iface and keep the picker open so the operator can pick a different
    one without an SSH detour.
    """
    if not app._available_interfaces:
        app._iface_selection_active = False
        return
    selected = app._available_interfaces[app._selected_iface_index]
    old_iface = app._config.psn_source_iface
    if selected == old_iface:
        # No-op pick – close the picker without touching the runtime.
        app._iface_selection_active = False
        return

    from openfollow.net_utils import resolve_source_ip

    resolved_ip, _status = resolve_source_ip(selected)
    app._config.psn_source_iface = selected
    try:
        app._runtime_services.apply_psn_source_ip_change(resolved_ip)
    except Exception as error:  # noqa: BLE001
        app._config.psn_source_iface = old_iface
        # Restore the advisory to the prior iface's state (the PSN web
        # partial reads it every render).
        app._refresh_psn_source_advisory()
        logger.warning(
            "Failed to live-apply network interface %r: %s – keeping %r.",
            selected,
            error,
            old_iface,
        )
        # Keep the picker open so the operator can choose a working
        # interface; ``apply_psn_source_ip_change`` already rolled the
        # PSN sockets back to the prior IP.
        return

    _persist_config(app)
    # Clear/refresh the stale-iface advisory now the pin is honoured so
    # the PSN web section stops warning about the old miss.
    app._refresh_psn_source_advisory()
    logger.info(
        "Network interface set to: %s (live)",
        selected or "auto-detect",
    )
    app._iface_selection_active = False


# ---------------------------------------------------------------------------
# Video Source Type switcher
# ---------------------------------------------------------------------------


def enter_source_type_selection(app: OpenFollowApp) -> None:
    """Open the on-device Video Source Type picker.

    Lists every plugin in the registry whose ``is_available()`` returns
    True on the running platform – operators see the same set of
    options the web UI's source-type dropdown surfaces. Selection seeds
    to the currently-active type so the picker doesn't visually reset
    on every reopen.
    """
    from openfollow.video.inputs import get_available_registry

    registry = get_available_registry()
    types = sorted(
        ((iid, cls.display_name) for iid, cls in registry.items()),
        key=lambda pair: pair[1].lower(),
    )
    app._available_source_types = types
    current = app._config.video_source_type
    app._selected_source_type_index = 0
    for i, (iid, _) in enumerate(types):
        if iid == current:
            app._selected_source_type_index = i
            break
    app._source_type_selection_active = True


def exit_source_type_selection(app: OpenFollowApp) -> None:
    app._source_type_selection_active = False


def process_source_type_selection_input(app: OpenFollowApp) -> None:
    """Read gamepad input for the Video Source Type picker."""
    input_manager = app._input_manager
    if input_manager is None:
        return
    try:
        inp = input_manager.gamepad_handler.read_source_selection_input()
    except Exception as error:
        logger.warning("Source-type selection input error: %s", error)
        return
    if not app._available_source_types:
        if inp.cancel_pressed:
            exit_source_type_selection(app)
            _back_to_settings(app)
        return
    if inp.up_pressed:
        app._selected_source_type_index = max(
            0,
            app._selected_source_type_index - 1,
        )
    if inp.down_pressed:
        app._selected_source_type_index = min(
            len(app._available_source_types) - 1,
            app._selected_source_type_index + 1,
        )
    if inp.confirm_pressed:
        app._confirm_source_type_selection()
    elif inp.cancel_pressed:
        exit_source_type_selection(app)
        _back_to_settings(app)


def _route_after_video_source_change(
    app: OpenFollowApp,
    source_type: str,
) -> None:
    """After a source-type pick (or same-type confirm), route to the
    natural next step for that plugin: discovery-based source picker
    for NDI, URL editor for RTSP / SRT / RTP / NDI URL entry, no-op
    for types whose defaults work out-of-box.

    The operator's mental model is "Change Video Source", and the next step
    is whatever surface they'd realistically reach for after picking a type.
    Splitting those into separate menu items created three rows that all read
    as "video" and confused operators.
    """
    from openfollow.video.inputs import get_input_class

    cls = get_input_class(source_type)
    has_picker = bool(cls and cls.capabilities().has_source_selection)
    if has_picker:
        app._enter_source_selection()
        return
    choice_field = _first_video_choice_field(source_type)
    if choice_field is not None:
        app._enter_field_choice_picker()
        return
    text_field = _first_video_text_field(source_type)
    if text_field is not None:
        app._enter_url_editor()


def confirm_source_type_selection(app: OpenFollowApp) -> None:
    """Apply the selected source type live via ``swap_video`` and
    continue to the per-type next step.

    Mirrors :func:`confirm_iface_selection`'s transactional shape:
    on a successful swap, persist + route to next step (URL editor
    or source picker per the plugin's capabilities); on failure
    with an empty URL field, auto-chain into the URL editor with
    rollback semantics; otherwise revert the stored type and bounce
    into the Settings menu with a banner.

    When the operator picks the SAME type already running, skip the
    swap but still route to the next step – that path is the way to
    "edit my RTSP URL" or "pick a different NDI source" without a
    redundant pipeline cycle.
    """
    if not app._available_source_types:
        exit_source_type_selection(app)
        return
    new_type, display = app._available_source_types[app._selected_source_type_index]
    old_type = app._config.video_source_type

    if new_type != old_type:
        new_cfg = replace(app._config, video_source_type=new_type)
        app._config.video_source_type = new_type
        try:
            app._runtime_services.swap_video(new_cfg)
        except Exception as error:  # noqa: BLE001
            # Auto-chain into the URL editor when the plugin we picked
            # has a string field whose current value is empty – almost
            # always the failure cause for
            # RTSP / SRT / NDI / RTP. Drop the operator into the
            # editor directly with ``revert_type`` so cancelling
            # rolls the partial type change back.
            #
            # Choice-bearing fields (testpattern) take a parallel
            # auto-chain into the on-device list picker so the
            # operator never gets stuck typing an enum value.
            choice_field = _first_video_choice_field(new_type)
            if choice_field is not None:
                exit_source_type_selection(app)
                app._enter_field_choice_picker(revert_type=old_type)
                return
            text_field = _first_video_text_field(new_type)
            if text_field is not None and not str(getattr(app._config, text_field[0], "")).strip():
                exit_source_type_selection(app)
                app._enter_url_editor(
                    banner=(f"{display} needs a {text_field[1]}. Type it below and press Enter."),
                    revert_type=old_type,
                )
                return
            app._config.video_source_type = old_type
            logger.warning(
                "Failed to live-apply video source type %r: %s – keeping %r.",
                new_type,
                error,
                old_type,
            )
            exit_source_type_selection(app)
            app._enter_settings_menu(
                banner=(f"Couldn't switch to {display}: {error}. Configure it in the web UI, then try again."),
            )
            return
        save_config(app._config, app._config_path)
        app._config_mtime = app._get_config_mtime()
        logger.info("Video source type set to: %s (live)", new_type)

    exit_source_type_selection(app)
    _route_after_video_source_change(app, new_type)


# ---------------------------------------------------------------------------
# On-device URL editor
# ---------------------------------------------------------------------------


def enter_url_editor(
    app: OpenFollowApp,
    *,
    banner: str = "",
    revert_type: str = "",
) -> None:
    """Open the on-device text editor for the active plugin's URL field.

    ``revert_type`` is non-empty when the editor was auto-chained from
    a failed source-type swap: on cancel the type rolls back to the prior
    value so a partial picker → empty-URL state can't escape a Settings-menu
    round-trip.
    """
    field = _first_video_text_field(app._config.video_source_type)
    if field is None:
        return
    name, label = field
    app._url_editor_active = True
    app._url_editor_field_name = name
    app._url_editor_field_label = label
    app._url_editor_value = str(getattr(app._config, name, ""))
    app._url_editor_banner = banner
    app._url_editor_revert_type = revert_type


def exit_url_editor(app: OpenFollowApp) -> None:
    app._url_editor_active = False
    app._url_editor_field_name = ""
    app._url_editor_field_label = ""
    app._url_editor_value = ""
    app._url_editor_banner = ""
    app._url_editor_revert_type = ""


def cancel_url_editor(app: OpenFollowApp) -> None:
    """Close the editor and discard any in-progress edits.

    When auto-chained, ``_url_editor_revert_type`` carries the prior
    ``video_source_type``: restore it so the operator's
    aborted picker → empty-URL flow leaves no half-applied state.
    The receiver is already on the old plugin (``swap_video`` keeps it
    attached on failure), so this is a pure config restore.
    """
    revert_type = app._url_editor_revert_type
    if revert_type:
        app._config.video_source_type = revert_type
    exit_url_editor(app)
    _back_to_settings(app)


def confirm_url_editor(app: OpenFollowApp) -> None:
    """Persist the typed value, optionally retry the swap that brought
    us here, and close the editor.

    On the auto-chain path (``_url_editor_revert_type`` non-empty), a
    successful save triggers ``swap_video`` so the operator's intent
    ("switch to RTSP with this URL") completes in one step. If
    ``swap_video`` then fails for an unrelated reason – bad URL
    format, server unreachable – the type rolls back and the operator
    lands in the Settings menu with a banner.
    """
    if not app._url_editor_field_name:
        exit_url_editor(app)
        return
    field = app._url_editor_field_name
    label = app._url_editor_field_label
    new_value = app._url_editor_value
    revert_type = app._url_editor_revert_type
    old_value = getattr(app._config, field, "")
    setattr(app._config, field, new_value)
    _persist_config(app)

    # Always rebuild the pipeline on confirm. Re-selecting the active
    # source (same type, unchanged URL) is the operator's "reconnect
    # now" gesture: the same-type path skips swap_video in
    # confirm_source_type_selection, so without rebuilding here a dead
    # RTSP/SRT pipeline stayed stuck until the operator bounced through
    # another source type. ``revert_type`` is only set on the auto-chain
    # path (empty URL after a type pick), where a failed swap rolls the
    # type back; on the plain edit path we keep the type and surface the
    # error, leaving the auto-heal loop to keep retrying in the
    # background.
    try:
        new_cfg = replace(app._config)
        app._runtime_services.swap_video(new_cfg)
        # Log the applied change only after swap_video succeeds. On
        # failure the except below rolls the value back, so emitting an
        # info "Set …" line before the swap would falsely read as a
        # successful change when diagnosing reconnect issues.
        logger.info("Set %s to %r", field, new_value)
    except Exception as error:  # noqa: BLE001
        from openfollow.video.inputs import get_input_class

        failed_type = app._config.video_source_type
        # swap_video rolls the receiver back to its prior plugin/config
        # on failure, so undo the stored change too – otherwise config
        # would diverge from the live receiver and the auto-heal loop
        # (which reads the receiver's input_config) would keep targeting
        # the old value. Auto-chain rolls the type back; the plain-edit
        # path rolls the URL field back.
        if revert_type:
            app._config.video_source_type = revert_type
        else:
            setattr(app._config, field, old_value)
        _persist_config(app)
        logger.warning(
            "URL editor: swap to %r failed: %s%s.",
            failed_type,
            error,
            f" – reverting to {revert_type!r}" if revert_type else f" – reverting {field} to {old_value!r}",
        )
        failed_cls = get_input_class(failed_type)
        target = failed_cls.display_name if failed_cls else failed_type
        exit_url_editor(app)
        app._enter_settings_menu(
            banner=(f"Couldn't switch to {target}: {error}. Configure {label} in the web UI, then try again."),
        )
        return
    exit_url_editor(app)


def process_url_editor_input(app: OpenFollowApp) -> None:
    """Gamepad poll for the URL editor – Cancel backs out so a
    gamepad-only operator isn't stranded inside the dialog."""
    input_manager = app._input_manager
    if input_manager is None:
        return
    try:
        inp = input_manager.gamepad_handler.read_settings_menu_input()
    except Exception as exc:  # noqa: BLE001
        logger.warning("URL editor gamepad input error: %s", exc)
        return
    if inp.cancel_pressed:
        cancel_url_editor(app)


def handle_url_editor_key(app: OpenFollowApp, key: str) -> None:
    """Process a single key press inside the URL editor.

    Single-char keys append to the buffer; ``Backspace`` deletes the
    last char; ``Enter`` confirms; ``Escape`` cancels. Modifier keys
    (Shift, Control, Meta, Alt) and unknown multi-char names like
    ``ArrowUp`` are silently dropped – they have no editor semantics
    and would otherwise insert noise into the URL.
    """
    if key == "Escape":
        cancel_url_editor(app)
    elif key == "Enter":
        app._confirm_url_editor()
    elif key == "Backspace":
        app._url_editor_value = app._url_editor_value[:-1]
    elif len(key) == 1 and key.isprintable():
        app._url_editor_value += key


# ---------------------------------------------------------------------------
# On-device field-choice picker (enum-style replacement for URL editor)
# ---------------------------------------------------------------------------


def enter_field_choice_picker(
    app: OpenFollowApp,
    *,
    revert_type: str = "",
) -> None:
    """Open the on-device list picker for the active plugin's first
    choices-bearing string field.

    ``revert_type`` mirrors :func:`enter_url_editor` – non-empty when
    the picker was auto-chained from a failed source-type swap, so
    cancelling rolls the partial type change back.
    """
    field = _first_video_choice_field(app._config.video_source_type)
    if field is None:
        return
    name, label, choices = field
    current = str(getattr(app._config, name, ""))
    items = [display for _value, display in choices]
    selected_idx = 0
    for i, (value, _display) in enumerate(choices):
        if value == current:
            selected_idx = i
            break
    app._field_choice_active = True
    app._field_choice_field_name = name
    app._field_choice_field_label = label
    app._field_choice_options = list(choices)
    app._field_choice_items = items
    app._field_choice_selected_index = selected_idx
    app._field_choice_revert_type = revert_type


def exit_field_choice_picker(app: OpenFollowApp) -> None:
    app._field_choice_active = False
    app._field_choice_field_name = ""
    app._field_choice_field_label = ""
    app._field_choice_options = []
    app._field_choice_items = []
    app._field_choice_selected_index = 0
    app._field_choice_revert_type = ""


def cancel_field_choice_picker(app: OpenFollowApp) -> None:
    """Close the picker and discard the selection.

    On the auto-chain path (``_field_choice_revert_type`` non-empty),
    restore the prior ``video_source_type`` so the operator's aborted
    picker → empty / invalid value flow leaves no half-applied state.
    The receiver is already on the old plugin (``swap_video`` keeps it
    attached on failure), so this is a pure config restore.
    """
    revert_type = app._field_choice_revert_type
    if revert_type:
        app._config.video_source_type = revert_type
    exit_field_choice_picker(app)
    _back_to_settings(app)


def confirm_field_choice_picker(app: OpenFollowApp) -> None:
    """Persist the selected value, live-apply it, and close the picker.

    ``swap_video`` runs on every confirm, not just the auto-chain
    path – the operator's intent is "show me this value NOW", whether
    they reached the picker by changing source-type (auto-chain,
    ``revert_type`` set) or by editing the value on the already-active
    plugin (``revert_type`` empty). Without the unconditional swap the
    same-type-value-change path saved to ``config.toml`` but left the
    running pipeline on the old value, surprising operators who'd
    expect the change to apply immediately (consistent with how the
    web UI's section POST handlers live-apply).

    On swap failure:
    - Auto-chain path (``revert_type`` set): roll the source TYPE
      back to the prior plugin and banner.
    - Same-type path (no ``revert_type``): roll the FIELD value back
      to what was running before the picker opened and banner.
    """
    if not app._field_choice_field_name or not app._field_choice_options:
        exit_field_choice_picker(app)
        return
    idx = max(0, min(app._field_choice_selected_index, len(app._field_choice_options) - 1))
    value, _display = app._field_choice_options[idx]
    field_name = app._field_choice_field_name
    label = app._field_choice_field_label
    revert_type = app._field_choice_revert_type
    # Snapshot the prior value for the same-type rollback path –
    # captured before ``setattr`` so a failed swap on the new value
    # can restore the running pipeline's config exactly.
    prior_value = getattr(app._config, field_name, None)
    setattr(app._config, field_name, value)
    _persist_config(app)
    logger.info("Set %s to %r", field_name, value)

    try:
        new_cfg = replace(app._config)
        app._runtime_services.swap_video(new_cfg)
    except Exception as error:  # noqa: BLE001
        if revert_type:
            from openfollow.video.inputs import get_input_class

            failed_type = app._config.video_source_type
            app._config.video_source_type = revert_type
            _persist_config(app)
            logger.warning(
                "Field-choice picker: swap to %r still failed: %s – reverting to %r.",
                failed_type,
                error,
                revert_type,
            )
            failed_cls = get_input_class(failed_type)
            target = failed_cls.display_name if failed_cls else failed_type
            exit_field_choice_picker(app)
            app._enter_settings_menu(
                banner=(f"Couldn't switch to {target}: {error}. Configure {label} in the web UI, then try again."),
            )
            return
        # Same-type path: revert just the field value. The running
        # pipeline is still on ``prior_value`` (swap_video keeps the
        # receiver attached to the old config on failure), so this
        # restores stored-config / running-pipeline consistency.
        setattr(app._config, field_name, prior_value)
        _persist_config(app)
        logger.warning(
            "Field-choice picker: swap with %s=%r failed: %s – reverting to %r.",
            field_name,
            value,
            error,
            prior_value,
        )
        exit_field_choice_picker(app)
        app._enter_settings_menu(
            banner=(f"Couldn't apply {label} = {value!r}: {error}. Try a different value, or edit in the web UI."),
        )
        return
    exit_field_choice_picker(app)


def process_field_choice_picker_input(app: OpenFollowApp) -> None:
    """Read gamepad input for the field-choice picker."""
    input_manager = app._input_manager
    if input_manager is None:
        return
    try:
        inp = input_manager.gamepad_handler.read_source_selection_input()
    except Exception as error:
        logger.warning("Field-choice picker input error: %s", error)
        return
    if not app._field_choice_items:
        if inp.cancel_pressed:
            cancel_field_choice_picker(app)
        return
    if inp.up_pressed:
        app._field_choice_selected_index = max(
            0,
            app._field_choice_selected_index - 1,
        )
    if inp.down_pressed:
        app._field_choice_selected_index = min(
            len(app._field_choice_items) - 1,
            app._field_choice_selected_index + 1,
        )
    if inp.confirm_pressed:
        confirm_field_choice_picker(app)
    elif inp.cancel_pressed:
        cancel_field_choice_picker(app)


# ---------------------------------------------------------------------------
# Startup video-connect banner
# ---------------------------------------------------------------------------


def _video_disconnect_banner_text(app: OpenFollowApp) -> str:
    """Compose the Settings-menu banner that replaces the old "No
    Signal" overlay.

    Carries every field the prior overlay surfaced – source type,
    source label / URL, reconnect-attempt count, and the receiver's
    error message – concatenated into a single string. The Settings
    menu's red-bordered error box renders this with bold body text
    and greedy word-wrap (no ellipsis truncation), so the full
    diagnostic stays readable even for long error messages.
    """
    source_type = app._config.video_source_type
    receiver = app._video_receiver
    status = getattr(receiver, "status_marker", None) if receiver else None
    source_label = (getattr(status, "source_name", "") if status else "") or ""
    error = (getattr(status, "error_message", "") if status else "") or ""
    attempt = int(getattr(status, "reconnect_attempt", 0) or 0)

    parts: list[str] = [f"Video source ({source_type}) is not available"]
    if source_label:
        parts[0] += f" – {source_label}"
    parts[0] += "."
    if error:
        parts.append(f"Error: {error}.")
    if attempt > 0:
        parts.append(f"Reconnect attempt {attempt}.")
    parts.append("Switch source type or edit the URL to recover.")
    return " ".join(parts)


def check_video_disconnect_banner(app: OpenFollowApp) -> None:
    """Startup-only Settings-menu auto-open for a video source that
    never comes up. Replaces the standalone "No Signal" overlay;
    mid-stream auto-open is intentionally not surfaced.

    Behaviour:

    * At startup ``run()`` arms ``_video_disconnect_deadline``. If the
      receiver is still disconnected when the deadline passes – i.e.
      the source never produced a frame, usually a misconfiguration
      the pipeline's self-healing cannot fix – the Settings menu opens.
      The banner string is passed via ``_enter_settings_menu(banner=...)``
      and rendered inside the menu's red-bordered error box
      (word-wrapped, bold body), NOT as the modal subtitle.
    * Mid-stream disconnects are intentionally NOT surfaced. Once a
      feed has connected, the video pipeline self-heals transient
      drops; auto-opening the menu on every drop left it stuck open
      after the feed recovered. ``_video_was_connected`` latches True
      on the first connect and is never cleared, permanently disabling
      the auto-open for the rest of the session. The operator can
      always open Settings manually for recovery.
    * Once the banner fires, ``_video_disconnect_banner_shown`` latches,
      preventing per-frame menu re-opens after the operator dismisses
      it.
    * Defers to any modal already open.
    """
    receiver = app._video_receiver
    if receiver is None:
        return

    if receiver.connected:
        # First successful connect permanently disables the startup
        # prompt – from here video recovery is the pipeline's job
        # (self-healing), not an auto-opened menu.
        app._video_was_connected = True
        app._video_disconnect_deadline = float("inf")
        return

    # Disconnected. Only the startup case (never connected) auto-opens;
    # a drop after a successful connect is left to self-healing.
    if app._video_was_connected:
        return
    if app._video_disconnect_banner_shown:
        return
    if time.monotonic() < app._video_disconnect_deadline:
        return
    if (
        app._settings_menu_active
        or app._iface_selection_active
        or app._source_type_selection_active
        or app._url_editor_active
        or app._field_choice_active
        or app._browser_active
        or app._button_detection is not None
        or getattr(app, "_pi_network_active", False)
    ):
        return

    app._video_disconnect_banner_shown = True
    logger.warning(
        "Video source %r never connected after startup – opening Settings.",
        app._config.video_source_type,
    )
    app._enter_settings_menu(banner=_video_disconnect_banner_text(app))


# Grace period between startup and the Settings-menu auto-open when
# the video source never connects. Armed once in ``app.run()``; only
# the startup case fires (mid-stream disconnects are left to the
# pipeline's self-healing).
VIDEO_DISCONNECT_BANNER_DELAY: float = 5.0


# ---------------------------------------------------------------------------
# Embedded browser overlay
# ---------------------------------------------------------------------------


def enter_browser(app: OpenFollowApp) -> None:
    """Mount the embedded WebKit browser as a fullscreen overlay.

    Loads ``http://127.0.0.1:<web_port>/`` so the operator can reach
    the running web UI from the device itself when no other host on
    the LAN can – the canonical recovery path for kiosk / isolated-Pi
    installs.

    Hides the ``gtksink`` widget for the duration of the overlay. On
    Linux ``gtksink`` paints into a native X11/Wayland subsurface that
    GTK's overlay compositor doesn't occlude, so an unhidden video
    layer keeps blitting frames over the WebView's region and produces
    striped / flickering corruption on first paint. The pipeline state
    is left untouched (decoder keeps running into a hidden widget);
    ``exit_browser`` restores visibility without a resume cycle.
    """
    from openfollow.runtime import webkit_browser

    if app._browser_active:
        return
    if not webkit_browser.AVAILABLE:
        logger.warning(
            "Cannot open embedded browser – WebKit2 not available: %s",
            webkit_browser.IMPORT_ERROR or "module not installed",
        )
        return
    if app._canvas is None:
        # The browser mounts onto the GTK canvas; without it there's
        # no surface to attach to. ``run()`` always wires the canvas
        # before any input dispatches, so this is a defensive guard
        # rather than a path the operator can hit normally.
        return
    url = webkit_browser.build_url(
        app._config,
        web_server=getattr(app, "_web_server", None),
    )
    overlay = webkit_browser.WebKitBrowserOverlay(
        url,
        on_close=app._exit_browser,
    )
    app._browser_overlay = overlay
    app._canvas.add_overlay_widget(overlay.widget)
    # Mark active once mounted so exit/cancel can always dismiss the overlay;
    # roll back if a later step raises.
    app._browser_active = True
    try:
        # Stash the sink widget so exit re-shows the same instance even if the
        # receiver later swaps sinks. ``None`` is the no-receiver startup path.
        receiver = getattr(app, "_video_receiver", None)
        sink_widget = receiver.get_sink_widget() if receiver is not None else None
        app._browser_hidden_sink = sink_widget
        if sink_widget is not None:
            sink_widget.hide()
    except Exception:
        logger.exception("Browser entry failed after mount – rolling back.")
        exit_browser(app)


def exit_browser(app: OpenFollowApp) -> None:
    """Tear down the embedded browser overlay if active.

    Re-opens the Settings menu so Esc inside the WebView lands the
    operator back on the recovery hub, matching the "Esc = back"
    behaviour of the other sub-screens. Re-shows the
    ``gtksink`` widget hidden by ``enter_browser`` so video returns.
    """
    if not app._browser_active:
        return
    overlay = app._browser_overlay
    if overlay is not None:
        if app._canvas is not None:
            app._canvas.remove_overlay_widget(overlay.widget)
        # Tear down the WebView's web process now, not at GC.
        overlay.close()
    sink_widget = app._browser_hidden_sink
    if sink_widget is not None:
        sink_widget.show()
    app._browser_hidden_sink = None
    app._browser_overlay = None
    app._browser_active = False
    _back_to_settings(app)


def process_browser_input(app: OpenFollowApp) -> None:
    """Poll the gamepad for a cancel press to dismiss the browser overlay.

    Gamepad-only operators have no Esc key path; the WebView's own
    ``key-press-event`` handler swallows Esc on hosts that have a
    keyboard but doesn't see any gamepad activity. Mirrors the
    cancel-only pattern of ``process_iface_selection_input`` –
    ``cancel_pressed`` from ``read_source_selection_input`` is the
    operator's mapped ``btn_menu_cancel`` (default ``B``).
    """
    input_manager = app._input_manager
    if input_manager is None:
        return
    try:
        inp = input_manager.gamepad_handler.read_source_selection_input()
        if inp.cancel_pressed:
            app._exit_browser()
    except Exception as error:  # noqa: BLE001
        logger.warning("Browser input error: %s", error)


def get_default_marker_position(app: OpenFollowApp) -> tuple[float, float, float]:
    cfg = app._config.marker
    return (cfg.default_pos_x, cfg.default_pos_y, cfg.default_pos_z)


# ---------------------------------------------------------------------------
# Button Detection Wizard
# ---------------------------------------------------------------------------


def _exclusive_mode_active(app: OpenFollowApp) -> bool:
    """True when a local modal / wizard owns the screen and input.

    Mirrors the full set ``process_input`` suspends marker control for –
    including the button-detection wizard, which consumes all input – so the
    GTK key/pointer handlers gate the same states as the polled path.
    """
    receiver = getattr(app, "_video_receiver", None)
    if receiver is not None and receiver.source_selection_active:
        return True
    return bool(
        getattr(app, "_button_detection", None) is not None
        or getattr(app, "_settings_menu_active", False)
        or getattr(app, "_about_active", False)
        or getattr(app, "_pi_network_field_edit_active", False)
        or getattr(app, "_pi_network_method_picker_active", False)
        or getattr(app, "_pi_network_iface_picker_active", False)
        or getattr(app, "_pi_network_active", False)
        or getattr(app, "_iface_selection_active", False)
        or getattr(app, "_source_type_selection_active", False)
        or getattr(app, "_field_choice_active", False)
        or getattr(app, "_url_editor_active", False)
        or getattr(app, "_browser_active", False)
    )


def enter_button_detection(app: OpenFollowApp) -> None:
    """Start the interactive button detection wizard."""
    from openfollow.input.button_detection import ButtonDetectionWizard

    if app._button_detection is not None:
        return
    # The web request is queued blindly; refuse when a local modal owns input.
    if _exclusive_mode_active(app):
        logger.warning("Button detection request ignored – another mode is active.")
        return
    if app._input_manager is None or not app._input_manager.gamepad_handler.joysticks:
        logger.warning("No controller connected – cannot start button detection.")
        return
    app._button_detection = ButtonDetectionWizard(app=app)
    if app._web_server is not None:
        app._web_server.set_button_detection_active(True)


def process_button_detection(app: OpenFollowApp) -> None:
    """Poll the wizard each frame; apply results when done."""
    wizard = app._button_detection
    if wizard is None:
        return
    # Wizard is only constructable via ``enter_button_detection``,
    # which itself early-returns when ``_input_manager`` is None or
    # has no joysticks (lines above). So a non-None wizard implies a
    # non-None input_manager – but mypy can't see that cross-function
    # invariant; narrow with a local for the strict gate.
    input_manager = app._input_manager
    if input_manager is None:
        return

    # Keyboard Escape cancels. ``KeyboardHandler.POLLED_KEYS`` stores
    # the named key as ``"Escape"`` (capitalised); the lowercase form
    # never appears, so the check must use the capitalised spelling
    # to match every other modal Escape handler in this module.
    keys = input_manager.keyboard_handler.keys
    if "Escape" in keys:
        wizard.cancel()
        app._button_detection = None
        if app._web_server is not None:
            app._web_server.set_button_detection_active(False)
        _back_to_settings(app)
        return

    wizard.poll()

    if wizard.is_done and wizard.results:
        detect_map = wizard.build_detection_map()
        for field_name, value in detect_map.items():
            setattr(app._config.controller, field_name, value)
        app._config.controller.button_raw_indices = wizard.build_raw_index_map()
        app._config.controller.swap_triggers = wizard.should_swap_triggers()
        app._config.controller.mapped_controller_name = wizard.controller_name
        app._config.controller.mapped_controller_guid = wizard.controller_guid
        app._config.controller.__post_init__()
        # Non-raising: tear-down below must run so a disk error can't loop the tick.
        _persist_config(app)
        input_manager.gamepad_handler.apply_config()
        logger.info("Button detection map applied: %s", detect_map)
        app._button_detection = None
        # pragma: no branch – button-detection wizard is only entered
        # via the web server's button-detection action, so ``_web_server``
        # is always non-None at the call site.
        if app._web_server is not None:  # pragma: no branch
            app._web_server.set_button_detection_active(False)
    # pragma: no branch – wizard is_done is the exhaustive elif over
    # the wizard-completion state; the False arm only fires while the
    # wizard is mid-poll, which is guarded by the early-return above.
    elif wizard.is_done:  # pragma: no branch
        # Cancelled with no results
        app._button_detection = None
        if app._web_server is not None:  # pragma: no branch
            app._web_server.set_button_detection_active(False)


def exit_button_detection(app: OpenFollowApp) -> None:
    """Cancel and close the button detection wizard."""
    if app._button_detection is not None:
        app._button_detection.cancel()
        app._button_detection = None
        if app._web_server is not None:
            app._web_server.set_button_detection_active(False)
