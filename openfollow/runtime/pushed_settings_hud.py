# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Settings another station pushed here, on the Operator Screen.

An info row in the status corner names the newest push until the warning is
removed, from the web UI or from the Settings menu's first entry.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openfollow.app import OpenFollowApp
    from openfollow.web.pushed_settings import Push, PushedSettings

BADGE_KEY = "pushed_settings"


def _register(app: OpenFollowApp) -> PushedSettings | None:
    return getattr(getattr(app, "_web_server", None), "pushed_settings", None)


def newest_pending(app: OpenFollowApp) -> int | None:
    """The id of the newest push still warned about, or ``None``."""
    register = _register(app)
    if register is None:
        return None
    pushes, _earlier = register.snapshot()
    return pushes[0].id if pushes else None


def badge_row(pushes: tuple[Push, ...], earlier: int) -> tuple[str, str] | None:
    if not pushes:
        return None
    newest = pushes[0]
    text = f"Settings pushed from {newest.station or newest.address}"
    more = len(pushes) - 1 + earlier
    if more:
        text += f" (+{more} more)"
    return ("info", text)


def check_pushed_settings(app: OpenFollowApp) -> None:
    """Housekeeping: keep the status corner's row in step with the pushes held."""
    register = _register(app)
    if register is None:
        return
    flags: dict[str, Any] = app._runtime_services._status_flags
    flags[BADGE_KEY] = badge_row(*register.snapshot())


def remove_pushed_warning(app: OpenFollowApp, upto: int | None) -> None:
    """Remove the warning for every push up to ``upto``, the newest one the menu offered."""
    register = _register(app)
    if register is not None and upto is not None:
        register.remove(upto)
    check_pushed_settings(app)
