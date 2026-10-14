# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The Keyboard Layout row: where it shows, what it offers, and when a restart is owed."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from openfollow import keyboard_layout
from openfollow.configuration import AppConfig
from openfollow.web import keyboard_layout_view as view
from tests._xkb import install_station_keyboard

pytestmark = pytest.mark.unit

_SESSION = {"OPENFOLLOW_KEYBOARD_SOURCE": "system", "XKB_DEFAULT_LAYOUT": "gb"}


@pytest.fixture(autouse=True)
def _station(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install_station_keyboard(tmp_path, monkeypatch)


def _cfg(layout: str = "", variant: str = "") -> AppConfig:
    cfg = AppConfig()
    cfg.controller.keyboard_layout = layout
    cfg.controller.keyboard_variant = variant
    return cfg


def test_the_row_is_hidden_where_the_session_did_not_resolve_the_layout() -> None:
    """A desktop, macOS, or a launcher without the helper: the setting could not take effect."""
    assert view.build_keyboard_layout_view(_cfg("de"), {"XKB_DEFAULT_LAYOUT": "de"}) is None


def test_the_row_reads_the_apps_own_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENFOLLOW_KEYBOARD_SOURCE", raising=False)
    assert view.build_keyboard_layout_view(_cfg()) is None
    monkeypatch.setenv("OPENFOLLOW_KEYBOARD_SOURCE", "default")
    assert view.build_keyboard_layout_view(_cfg()) is not None


def test_system_comes_first_and_names_what_it_resolves_to() -> None:
    row = view.build_keyboard_layout_view(_cfg(), _SESSION)
    assert row is not None
    assert row.layouts[0] == ("", "System – English (UK)")


def test_the_layouts_are_listed_by_their_description() -> None:
    row = view.build_keyboard_layout_view(_cfg(), _SESSION)
    assert row is not None
    assert row.layouts[1:] == [
        ("al", "Albanian"),
        ("gb", "English (UK)"),
        ("us", "English (US)"),
        ("de", "German"),
        ("jp", "Japanese"),
    ]


def test_system_names_the_system_variant_too(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install_station_keyboard(tmp_path, monkeypatch, 'XKBLAYOUT="de"\nXKBVARIANT="nodeadkeys"\n')
    row = view.build_keyboard_layout_view(_cfg(), _SESSION)
    assert row is not None
    assert row.layouts[0] == ("", "System – German (no dead keys)")


def test_system_without_a_system_file_is_the_default_layout(tmp_path: Path) -> None:
    keyboard_layout.SYSTEM_KEYBOARD.unlink()
    row = view.build_keyboard_layout_view(_cfg(), _SESSION)
    assert row is not None
    assert row.layouts[0] == ("", "System – English (US)")


def test_system_offers_only_standard() -> None:
    row = view.build_keyboard_layout_view(_cfg(), _SESSION)
    assert row is not None
    assert (row.layout, row.variant, row.variants) == ("", "", [("", "Standard")])


def test_a_chosen_layout_offers_its_own_variants() -> None:
    row = view.build_keyboard_layout_view(_cfg("de", "T3"), _SESSION)
    assert row is not None
    assert (row.layout, row.variant) == ("de", "T3")
    assert row.variants == [
        ("", "Standard"),
        ("nodeadkeys", "German (no dead keys)"),
        ("T3", "German (T3)"),
        ("us", "German (US)"),
    ]


def test_a_saved_layout_the_station_does_not_know_shows_as_system() -> None:
    """The session would not use it either, so the picker says what will happen."""
    row = view.build_keyboard_layout_view(_cfg("xx"), _SESSION)
    assert row is not None
    assert (row.layout, row.variant) == ("", "")


def test_a_saved_variant_of_another_layout_shows_as_standard() -> None:
    row = view.build_keyboard_layout_view(_cfg("gb", "nodeadkeys"), _SESSION)
    assert row is not None
    assert (row.layout, row.variant) == ("gb", "")


@pytest.mark.parametrize(
    ("layout", "variant", "session"),
    [
        ("", "", _SESSION),  # System, in use
        ("gb", "", _SESSION),  # the same layout, chosen
        ("de", "nodeadkeys", {**_SESSION, "XKB_DEFAULT_LAYOUT": "de", "XKB_DEFAULT_VARIANT": "nodeadkeys"}),
        ("gb", "nodeadkeys", _SESSION),  # the session drops a variant gb lacks
        ("xx", "", _SESSION),  # the session ignores an unknown layout
    ],
)
def test_nothing_is_pending_while_the_saved_layout_is_in_use(layout: str, variant: str, session: dict) -> None:
    row = view.build_keyboard_layout_view(_cfg(layout, variant), session)
    assert row is not None
    assert row.pending is False


@pytest.mark.parametrize(
    ("layout", "variant", "session", "in_use"),
    [
        ("de", "", _SESSION, "English (UK)"),
        ("de", "nodeadkeys", {**_SESSION, "XKB_DEFAULT_LAYOUT": "de"}, "German"),
        ("de", "", {**_SESSION, "XKB_DEFAULT_LAYOUT": "de", "XKB_DEFAULT_VARIANT": "T3"}, "German (T3)"),
        ("", "", {**_SESSION, "XKB_DEFAULT_LAYOUT": "de"}, "German"),
        ("", "", {**_SESSION, "XKB_DEFAULT_LAYOUT": "fr"}, "fr"),  # a layout this catalog does not describe
    ],
)
def test_a_saved_layout_that_differs_from_the_one_in_use_is_pending(
    layout: str, variant: str, session: dict, in_use: str
) -> None:
    row = view.build_keyboard_layout_view(_cfg(layout, variant), session)
    assert row is not None
    assert (row.pending, row.in_use_label) == (True, in_use)


def test_a_changed_system_file_is_pending_too() -> None:
    """The session reads it at start; an edit to it takes effect the same way."""
    keyboard_layout.SYSTEM_KEYBOARD.write_text('XKBLAYOUT="de"\n')
    row = view.build_keyboard_layout_view(_cfg(), _SESSION)
    assert row is not None
    assert row.pending is True


def test_the_catalog_is_read_again_only_when_it_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    reads: list[Path] = []
    real = keyboard_layout.read_catalog

    def counting(path: Path | None = None) -> keyboard_layout.XkbCatalog:
        reads.append(path)
        return real(path)

    monkeypatch.setattr(keyboard_layout, "read_catalog", counting)
    first = view.station_catalog()
    assert view.station_catalog() is first
    assert len(reads) == 1
    stat = keyboard_layout.EVDEV_LST.stat()
    keyboard_layout.EVDEV_LST.write_text("! layout\n  fr  French\n")
    os.utime(keyboard_layout.EVDEV_LST, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    assert view.station_catalog().layouts == {"fr": "French"}
    assert len(reads) == 2


def test_without_a_catalog_only_system_is_offered() -> None:
    keyboard_layout.EVDEV_LST.unlink()
    row = view.build_keyboard_layout_view(_cfg("de"), _SESSION)
    assert row is not None
    assert row.layouts == [("", "System – us")]
    assert row.variants == [("", "Standard")]
