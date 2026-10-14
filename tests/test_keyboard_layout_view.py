# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The Keyboard Layout row: where it shows, what it offers, and when a restart is owed."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from openfollow import keyboard_layout
from openfollow.configuration import AppConfig
from openfollow.web import keyboard_layout_view as view
from tests._xkb import install_station_keyboard

pytestmark = pytest.mark.unit

# What the session exports for the station's system file, and for a layout the station chose.
_SESSION = {"OPENFOLLOW_KEYBOARD_SOURCE": "system", "XKB_DEFAULT_LAYOUT": "gb", "XKB_DEFAULT_MODEL": "pc105"}
_CHOSEN = {"OPENFOLLOW_KEYBOARD_SOURCE": "config", "XKB_DEFAULT_MODEL": "pc105"}


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
        ("gb", "", {**_CHOSEN, "XKB_DEFAULT_LAYOUT": "gb"}),
        ("de", "nodeadkeys", {**_CHOSEN, "XKB_DEFAULT_LAYOUT": "de", "XKB_DEFAULT_VARIANT": "nodeadkeys"}),
        ("gb", "nodeadkeys", {**_CHOSEN, "XKB_DEFAULT_LAYOUT": "gb"}),  # the session drops a variant gb lacks
        ("xx", "", _SESSION),  # the session ignores an unknown layout
        ("gb", "", _SESSION),  # the system layout, picked by name
        ("", "", {**_CHOSEN, "XKB_DEFAULT_LAYOUT": "gb"}),  # back to System, which is that same layout
    ],
)
def test_nothing_is_pending_while_the_saved_layout_is_in_use(layout: str, variant: str, session: dict) -> None:
    row = view.build_keyboard_layout_view(_cfg(layout, variant), session)
    assert row is not None
    assert (row.pending, row.layout_pending) == (False, False)


@pytest.mark.parametrize(
    ("layout", "variant", "session", "in_use"),
    [
        ("de", "", _SESSION, "English (UK)"),
        ("de", "nodeadkeys", {**_CHOSEN, "XKB_DEFAULT_LAYOUT": "de"}, "German"),
        ("de", "", {**_CHOSEN, "XKB_DEFAULT_LAYOUT": "de", "XKB_DEFAULT_VARIANT": "T3"}, "German (T3)"),
        ("", "", {**_SESSION, "XKB_DEFAULT_LAYOUT": "de"}, "German"),
        ("", "", {**_SESSION, "XKB_DEFAULT_LAYOUT": "fr"}, "fr"),  # a layout this catalog does not describe
    ],
)
def test_a_saved_layout_that_differs_from_the_one_in_use_is_pending(
    layout: str, variant: str, session: dict, in_use: str
) -> None:
    row = view.build_keyboard_layout_view(_cfg(layout, variant), session)
    assert row is not None
    assert (row.pending, row.layout_pending, row.in_use_label) == (True, True, in_use)


@pytest.fixture
def system_without_caps_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """A system file with an option, and the session it started."""
    install_station_keyboard(tmp_path, monkeypatch, 'XKBMODEL="pc105"\nXKBLAYOUT="gb"\nXKBOPTIONS="ctrl:nocaps"\n')
    return {**_SESSION, "XKB_DEFAULT_OPTIONS": "ctrl:nocaps"}


def test_the_system_options_in_use_are_nothing_pending(system_without_caps_lock: dict[str, str]) -> None:
    row = view.build_keyboard_layout_view(_cfg(), system_without_caps_lock)
    assert row is not None
    assert row.pending is False


def test_choosing_the_system_layout_by_name_keeps_its_options(system_without_caps_lock: dict[str, str]) -> None:
    session = {**system_without_caps_lock, "OPENFOLLOW_KEYBOARD_SOURCE": "config"}
    for layout in ("gb", ""):
        row = view.build_keyboard_layout_view(_cfg(layout), session)
        assert row is not None
        assert row.pending is False


def test_a_chosen_layout_keeps_the_system_options(system_without_caps_lock: dict[str, str]) -> None:
    """Only the layout changes on restart: the session starts German with Caps Lock still as Ctrl."""
    session = {**system_without_caps_lock, "OPENFOLLOW_KEYBOARD_SOURCE": "config", "XKB_DEFAULT_LAYOUT": "de"}
    row = view.build_keyboard_layout_view(_cfg("de"), session)
    assert row is not None
    assert row.pending is False


@pytest.mark.parametrize(
    "changed",
    [
        {"XKB_DEFAULT_MODEL": "pc104"},
        {"XKB_DEFAULT_OPTIONS": "grp:alt_shift_toggle"},
    ],
)
def test_a_session_started_with_another_model_or_options_is_pending(changed: dict[str, str]) -> None:
    """The layout stays: only the system keyboard settings change on restart."""
    for layout in ("", "gb"):
        row = view.build_keyboard_layout_view(_cfg(layout), {**_SESSION, **changed})
        assert row is not None
        assert (row.pending, row.layout_pending) == (True, False)


def test_a_changed_system_file_is_pending_too() -> None:
    """The session reads it at start; an edit to it takes effect the same way."""
    keyboard_layout.SYSTEM_KEYBOARD.write_text('XKBLAYOUT="de"\n')
    row = view.build_keyboard_layout_view(_cfg(), _SESSION)
    assert row is not None
    assert row.pending is True


def test_the_catalog_is_read_again_only_when_it_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    reads: list[Path] = []
    real = keyboard_layout.read_catalog

    def counting(path: Path | None = None, xkb_root: Path | None = None) -> keyboard_layout.XkbCatalog:
        reads.append(path)
        return real(path, xkb_root)

    monkeypatch.setattr(keyboard_layout, "read_catalog", counting)
    (keyboard_layout.XKB_ROOT / "symbols" / "fr").write_text("")
    first = view.station_catalog()
    assert view.station_catalog() is first
    assert len(reads) == 1
    stat = keyboard_layout.EVDEV_LST.stat()
    keyboard_layout.EVDEV_LST.write_text("! layout\n  fr  French\n")
    os.utime(keyboard_layout.EVDEV_LST, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    assert view.station_catalog().layouts == {"fr": "French"}
    assert len(reads) == 2


def test_the_catalog_is_read_again_when_the_symbols_change() -> None:
    symbols = keyboard_layout.XKB_ROOT / "symbols"
    assert "de" in view.station_catalog().layouts
    stat = symbols.stat()
    (symbols / "de").unlink()
    os.utime(symbols, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    assert "de" not in view.station_catalog().layouts


def test_without_the_symbols_directory_only_system_is_offered() -> None:
    shutil.rmtree(keyboard_layout.XKB_ROOT / "symbols")
    row = view.build_keyboard_layout_view(_cfg("de"), _SESSION)
    assert row is not None
    assert row.layouts == [("", "System – us")]


def test_without_a_catalog_only_system_is_offered() -> None:
    keyboard_layout.EVDEV_LST.unlink()
    row = view.build_keyboard_layout_view(_cfg("de"), _SESSION)
    assert row is not None
    assert row.layouts == [("", "System – us")]
    assert row.variants == [("", "Standard")]
