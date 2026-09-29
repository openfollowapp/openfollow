# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The Operator Screen's drive picker and diagnostics export screen.

Settings → Export Diagnostics File for Support opens the picker; Enter on a
drive starts the shared export on a worker thread and shows its progress,
then its result. Esc or B leaves while the export carries on; its result
then lands in the status corner, a success clearing itself after a while,
a failure staying until the export is opened again (where its reason shows)
or a later export succeeds. Only exports started here post there.

The picker takes a title and an action, so another file (settings) can reuse it.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from openfollow.runtime.diagnostics_export import COLLECTING, DONE, HUD, RETRY, WEB, WRITING, ExportStatus
from openfollow.runtime.removable_media import Media, MediaWatch, list_media

if TYPE_CHECKING:
    from openfollow.app import OpenFollowApp
    from openfollow.runtime.diagnostics_export import DiagnosticsExport

EXPORT_TITLE = "SAVE DIAGNOSTICS"
PICKER_SUBTITLE = "Pick a USB storage device, Enter to save, Esc to cancel."
DIAGNOSTICS = "diagnostics"
BADGE_KEY = "diagnostics_export"
SUCCESS_BADGE_S = 15.0


def diagnostics_export(app: OpenFollowApp) -> DiagnosticsExport | None:
    return getattr(getattr(app, "_runtime_services", None), "diagnostics_export", None)


def _status_flags(app: OpenFollowApp) -> dict[str, Any]:
    return app._runtime_services._status_flags


def _watch(app: OpenFollowApp) -> MediaWatch:
    if app._media_watch is None:
        broker = getattr(app._runtime_services, "_privilege_broker", None)
        app._media_watch = MediaWatch(lambda: list_media(broker))
    return app._media_watch


def _back_to_settings(app: OpenFollowApp) -> None:
    from openfollow.runtime.app_modes import _back_to_settings as back

    back(app)


# --- entering -----------------------------------------------------------------------------------


def _is_failure(flag: object) -> bool:
    return isinstance(flag, tuple) and flag[0] == "error"


def enter_diagnostics_export(app: OpenFollowApp) -> None:
    """The Settings row: the running export's progress, the failure the badge points to, else the picker."""
    export = diagnostics_export(app)
    if export is None:
        return
    flags = _status_flags(app)
    failed = _is_failure(flags.get(BADGE_KEY))
    # Opening the export is where a failure's reason is read, so the badge row goes.
    flags[BADGE_KEY] = None
    app._media_export_badge_at = None
    if export.status().phase in (COLLECTING, WRITING) or failed:
        app._media_export_active = True
    else:
        enter_media_picker(app, EXPORT_TITLE, DIAGNOSTICS)


def enter_media_picker(app: OpenFollowApp, title: str, action: str) -> None:
    app._media_picker_active = True
    app._media_picker_title = title
    app._media_picker_action = action
    app._media_picker_selected = ""
    _watch(app).start()


def exit_media_picker(app: OpenFollowApp, *, back_to_settings: bool = True) -> None:
    app._media_picker_active = False
    _watch(app).stop()
    if back_to_settings:
        _back_to_settings(app)


def exit_export_screen(app: OpenFollowApp) -> None:
    export = diagnostics_export(app)
    if export is not None and export.status().phase == DONE:
        # Seen here, so it never posts to the badge.
        app._media_export_seen = export.status().generation
    app._media_export_active = False
    _back_to_settings(app)


# --- the picker ---------------------------------------------------------------------------------


def picker_rows(app: OpenFollowApp) -> tuple[list[Media], bool]:
    return _watch(app).snapshot()


def picker_index(app: OpenFollowApp, media: list[Media]) -> int:
    """The highlighted row: the picked drive while it is still writable, else the first writable one; -1 for none."""
    writable = [i for i, m in enumerate(media) if m.writable]
    for i in writable:
        if media[i].id == app._media_picker_selected:
            return i
    return writable[0] if writable else -1


def _move(app: OpenFollowApp, step: int) -> None:
    media, _ = picker_rows(app)
    writable = [i for i, m in enumerate(media) if m.writable]
    if not writable:
        return
    current = picker_index(app, media)
    position = (writable.index(current) + step) % len(writable)
    app._media_picker_selected = media[writable[position]].id


def _confirm(app: OpenFollowApp) -> None:
    media, _ = picker_rows(app)
    index = picker_index(app, media)
    if index < 0:
        return
    chosen = media[index]
    exit_media_picker(app, back_to_settings=False)
    # pragma: no branch – the one action today; a settings export adds the next arm.
    if app._media_picker_action == DIAGNOSTICS:  # pragma: no branch
        export = diagnostics_export(app)
        if export is None:
            _back_to_settings(app)
            return
        # Refused while another export runs: the screen then shows that one.
        export.start(chosen.id, chosen.name, HUD)
        app._media_export_active = True


def _export_screen_confirm(app: OpenFollowApp) -> None:
    export = diagnostics_export(app)
    if export is None or export.status().phase != DONE:
        return
    app._media_export_seen = export.status().generation
    app._media_export_active = False
    enter_media_picker(app, EXPORT_TITLE, DIAGNOSTICS)


# --- input --------------------------------------------------------------------------------------


def handle_media_key(app: OpenFollowApp, key: str) -> bool:
    """Keyboard for the picker and the export screen; True when one of them is open."""
    # Read defensively like the other modal flags, so partial app doubles don't trip.
    if getattr(app, "_media_picker_active", False):
        if key == "ArrowUp":
            _move(app, -1)
        elif key == "ArrowDown":
            _move(app, +1)
        elif key == "Enter":
            _confirm(app)
        elif key == "Escape":
            exit_media_picker(app)
        return True
    if getattr(app, "_media_export_active", False):
        if key == "Enter":
            _export_screen_confirm(app)
        elif key == "Escape":
            exit_export_screen(app)
        return True
    return False


def process_media_input(app: OpenFollowApp) -> bool:
    """Gamepad for the picker and the export screen; True when one of them is open."""
    if not (getattr(app, "_media_picker_active", False) or getattr(app, "_media_export_active", False)):
        return False
    input_manager = app._input_manager
    if input_manager is None:
        return True
    inp = input_manager.gamepad_handler.read_settings_menu_input()
    if app._media_picker_active:
        if inp.up_pressed:
            _move(app, -1)
        if inp.down_pressed:
            _move(app, +1)
        if inp.confirm_pressed:
            _confirm(app)
        elif inp.cancel_pressed:
            exit_media_picker(app)
    elif inp.confirm_pressed:
        _export_screen_confirm(app)
    elif inp.cancel_pressed:
        exit_export_screen(app)
    return True


# --- the status corner --------------------------------------------------------------------------


def check_diagnostics_export(app: OpenFollowApp, now: float | None = None) -> None:
    """Housekeeping: post a finished HUD export the operator left before it ended, and age a success out."""
    export = diagnostics_export(app)
    if export is None:
        return
    now = time.monotonic() if now is None else now
    flags = _status_flags(app)
    status = export.status()
    if status.phase == DONE and status.generation != app._media_export_seen:
        app._media_export_seen = status.generation
        if status.origin == HUD and not app._media_export_active:
            flags[BADGE_KEY] = badge_row(status)
            app._media_export_badge_at = now if status.ok else None
        elif status.origin == WEB and status.ok and _is_failure(flags.get(BADGE_KEY)):
            # A web export posts nothing, but its success makes a failure row stale.
            flags[BADGE_KEY] = None
    posted = app._media_export_badge_at
    if posted is not None and now - posted >= SUCCESS_BADGE_S:
        flags[BADGE_KEY] = None
        app._media_export_badge_at = None


def badge_row(status: ExportStatus) -> tuple[str, str]:
    if status.ok:
        return ("info", f"Diagnostics saved to {status.drive}")
    return ("error", f"Export failed: {status.message.rstrip('.')}")


def export_screen_lines(status: ExportStatus) -> tuple[str, str, bool | None]:
    """``(what is happening or happened, the next step, ok)`` for the export screen; ``ok`` is None while it runs."""
    if status.phase == COLLECTING:
        return "Collecting diagnostics", "The export continues in the background.", None
    if status.phase == WRITING:
        return f"Writing to {status.drive}", "The export continues in the background.", None
    if status.ok:
        return status.message, status.action, True
    return status.message, status.action or RETRY, False
