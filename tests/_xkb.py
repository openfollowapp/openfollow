# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Michel Honold and Paul Hermann
"""xkb fixtures shared by the keyboard layout tests: what a station's files look like."""

from __future__ import annotations

from pathlib import Path

import pytest

from openfollow import keyboard_layout

# An excerpt of a station's xkb ``evdev.lst``, shape and spacing kept.
EVDEV_LST = """\
! model
  pc86            Generic 86-key PC
  pc105           Generic 105-key PC

! layout
  al              Albanian
  us              English (US)
  gb              English (UK)
  de              German
  jp              Japanese
  custom          A user-defined custom Layout
  Bad             Upper case is not a layout name

! variant
  plisi           al: Albanian (Plisi)
  intl            us: English (US, intl., with dead keys)
  extd            gb: English (UK, extended, with Win keys)
  nodeadkeys      de: German (no dead keys)
  T3              de: German (T3)
  us              de: German (US)
  OADG109A        jp: Japanese (OADG 109A)
  orphan          a line without an owner
  bad;name        de: not a variant name

! option
  grp                  Switching to another layout
  grp:alt_shift_toggle Alt+Shift
  ctrl:nocaps          Caps Lock as Ctrl
  custom               Miscellaneous options
  custom:types         Use user-defined custom XKB types
"""

# The layouts xkb-data ships symbols for: every listed one but ``custom``.
SYMBOLS = ("al", "us", "gb", "de", "jp")

# A station's ``/etc/default/keyboard``.
SYSTEM_GB = """\
# KEYBOARD CONFIGURATION FILE

# Consult the keyboard(5) manual page.

XKBMODEL="pc105"
XKBLAYOUT="gb"
XKBVARIANT=""
XKBOPTIONS=""

BACKSPACE="guess"
"""


def write_xkb_root(root: Path, catalog: str = EVDEV_LST, symbols: tuple[str, ...] = SYMBOLS) -> Path:
    """An xkb data directory under ``root``: the rules' catalog and a symbols file per shipped layout."""
    (root / "rules").mkdir(parents=True, exist_ok=True)
    (root / "rules" / "evdev.lst").write_text(catalog, encoding="utf-8")
    (root / "symbols").mkdir(exist_ok=True)
    for name in symbols:
        (root / "symbols" / name).write_text("", encoding="utf-8")
    return root


def install_station_keyboard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, system: str = SYSTEM_GB) -> None:
    """Point the resolver at a station's xkb data and system file, written under ``tmp_path``."""
    root = write_xkb_root(tmp_path / "xkb")
    keyboard = tmp_path / "keyboard"
    keyboard.write_text(system, encoding="utf-8")
    monkeypatch.setattr(keyboard_layout, "XKB_ROOT", root)
    monkeypatch.setattr(keyboard_layout, "EVDEV_LST", root / "rules" / "evdev.lst")
    monkeypatch.setattr(keyboard_layout, "SYSTEM_KEYBOARD", keyboard)


def start_session(
    monkeypatch: pytest.MonkeyPatch,
    source: str = "system",
    layout: str = "gb",
    variant: str = "",
    model: str = "pc105",
    options: str = "",
) -> None:
    """The environment the packaged session gives the app; by default what it exports for ``SYSTEM_GB``."""
    values = {
        "OPENFOLLOW_KEYBOARD_SOURCE": source,
        "XKB_DEFAULT_LAYOUT": layout,
        "XKB_DEFAULT_VARIANT": variant,
        "XKB_DEFAULT_MODEL": model,
        "XKB_DEFAULT_OPTIONS": options,
    }
    for name, value in values.items():
        if value:
            monkeypatch.setenv(name, value)
        else:
            monkeypatch.delenv(name, raising=False)


SESSION_ENV = (
    "OPENFOLLOW_KEYBOARD_SOURCE",
    "XKB_DEFAULT_LAYOUT",
    "XKB_DEFAULT_VARIANT",
    "XKB_DEFAULT_MODEL",
    "XKB_DEFAULT_OPTIONS",
)


def no_session(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in SESSION_ENV:
        monkeypatch.delenv(name, raising=False)
