# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
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
"""

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


def install_station_keyboard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, system: str = SYSTEM_GB) -> None:
    """Point the resolver at a station's catalog and system file, written under ``tmp_path``."""
    catalog = tmp_path / "evdev.lst"
    catalog.write_text(EVDEV_LST, encoding="utf-8")
    keyboard = tmp_path / "keyboard"
    keyboard.write_text(system, encoding="utf-8")
    monkeypatch.setattr(keyboard_layout, "EVDEV_LST", catalog)
    monkeypatch.setattr(keyboard_layout, "SYSTEM_KEYBOARD", keyboard)


def start_session(
    monkeypatch: pytest.MonkeyPatch, source: str = "system", layout: str = "gb", variant: str = ""
) -> None:
    """The environment the packaged session gives the app."""
    monkeypatch.setenv("OPENFOLLOW_KEYBOARD_SOURCE", source)
    monkeypatch.setenv("XKB_DEFAULT_LAYOUT", layout)
    if variant:
        monkeypatch.setenv("XKB_DEFAULT_VARIANT", variant)
    else:
        monkeypatch.delenv("XKB_DEFAULT_VARIANT", raising=False)


def no_session(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("OPENFOLLOW_KEYBOARD_SOURCE", "XKB_DEFAULT_LAYOUT", "XKB_DEFAULT_VARIANT"):
        monkeypatch.delenv(name, raising=False)
