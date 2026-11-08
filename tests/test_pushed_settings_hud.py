# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The status corner's row for settings another station pushed here."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openfollow.runtime.pushed_settings_hud import BADGE_KEY, badge_row, check_pushed_settings, remove_pushed_warning
from openfollow.web.pushed_settings import SHOWN, PushedSettings

pytestmark = pytest.mark.unit


def _app(register: PushedSettings | None) -> SimpleNamespace:
    web = None if register is None else SimpleNamespace(pushed_settings=register)
    return SimpleNamespace(_web_server=web, _runtime_services=SimpleNamespace(_status_flags={}))


def test_one_push_names_its_station_at_the_info_level() -> None:
    register = PushedSettings()
    register.record("Stage Left", "198.51.100.7", "Grid")
    assert badge_row(*register.snapshot()) == ("info", "Settings pushed from Stage Left")


def test_a_station_without_a_name_is_named_by_its_address() -> None:
    register = PushedSettings()
    register.record("", "198.51.100.7", "Grid")
    assert badge_row(*register.snapshot()) == ("info", "Settings pushed from 198.51.100.7")


def test_more_pushes_are_counted_behind_the_newest() -> None:
    register = PushedSettings()
    for n in range(SHOWN + 3):
        register.record(f"S{n}", "198.51.100.7", "Grid")
    assert badge_row(*register.snapshot()) == ("info", f"Settings pushed from S{SHOWN + 2} (+{SHOWN + 2} more)")


def test_no_push_means_no_row() -> None:
    assert badge_row((), 0) is None


def test_housekeeping_posts_the_row_and_clears_it_once_removed() -> None:
    register = PushedSettings()
    push = register.record("Stage Left", "198.51.100.7", "Grid")
    app = _app(register)
    check_pushed_settings(app)
    assert app._runtime_services._status_flags[BADGE_KEY] == ("info", "Settings pushed from Stage Left")
    register.remove(push.id)
    check_pushed_settings(app)
    assert app._runtime_services._status_flags[BADGE_KEY] is None


def test_without_a_web_server_housekeeping_leaves_the_corner_alone() -> None:
    app = _app(None)
    check_pushed_settings(app)
    assert app._runtime_services._status_flags == {}


def test_removing_without_a_web_server_leaves_the_corner_alone() -> None:
    app = _app(None)
    remove_pushed_warning(app, 3)
    assert app._runtime_services._status_flags == {}
