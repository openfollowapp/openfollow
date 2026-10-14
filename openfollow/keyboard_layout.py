#!/usr/bin/python3 -I
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The keyboard layout the kiosk session starts with.

The station's own setting (``controller.keyboard_layout`` / ``keyboard_variant``
in ``config.toml``) wins when the system knows that layout, then the system's
``/etc/default/keyboard``, then ``us``. A variant the chosen layout does not
have is dropped, never passed on.

The session launcher runs this file as ``/usr/share/openfollow/keyboard-layout
env config.toml`` before it starts Cage, and the web UI imports it to tell what
a restart would change. The launcher runs it with the system interpreter, so
keep it standard-library only and free of ``openfollow`` imports.
"""

from __future__ import annotations

import re
import shlex
import sys
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "DEFAULT_LAYOUT",
    "LAYOUT_RE",
    "VARIANT_RE",
    "Resolution",
    "XkbCatalog",
    "main",
    "parse_catalog",
    "parse_system_keyboard",
    "read_catalog",
    "read_configured",
    "read_system_keyboard",
    "resolve",
    "resolve_settings",
]

EVDEV_LST = Path("/usr/share/X11/xkb/rules/evdev.lst")
SYSTEM_KEYBOARD = Path("/etc/default/keyboard")
DEFAULT_LAYOUT = "us"

LAYOUT_RE = re.compile(r"[a-z0-9_-]{1,32}")
VARIANT_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
# What the launcher accepts on each output line; anything else it ignores.
_WORD_RE = re.compile(r"[A-Za-z0-9_-]*")
_OPTIONS_RE = re.compile(r"[A-Za-z0-9_:,-]*")
_CATALOG_LINE_RE = re.compile(r"^\s+(\S+)\s+(.+?)\s*$")


@dataclass(frozen=True)
class XkbCatalog:
    """The layouts and variants the system's xkb rules know, with their descriptions."""

    layouts: dict[str, str] = field(default_factory=dict)
    variants: dict[str, tuple[tuple[str, str], ...]] = field(default_factory=dict)

    def variants_of(self, layout: str) -> tuple[tuple[str, str], ...]:
        return self.variants.get(layout, ())

    def has_variant(self, layout: str, variant: str) -> bool:
        return any(name == variant for name, _ in self.variants_of(layout))


@dataclass(frozen=True)
class Resolution:
    """What the session starts with, and which source decided it.

    ``model`` / ``options`` come from the system file and are empty unless
    ``source`` is ``system``: they describe that keyboard, not another layout.
    """

    layout: str
    variant: str
    source: str
    model: str = ""
    options: str = ""


def parse_catalog(text: str) -> XkbCatalog:
    """Read an xkb ``evdev.lst``: ``! layout`` and ``! variant`` sections."""
    layouts: dict[str, str] = {}
    variants: dict[str, list[tuple[str, str]]] = {}
    section = ""
    for line in text.splitlines():
        if line.startswith("!"):
            section = line[1:].strip()
            continue
        match = _CATALOG_LINE_RE.match(line)
        if match is None:
            continue
        name, description = match.groups()
        if section == "layout" and LAYOUT_RE.fullmatch(name):
            layouts[name] = description
        elif section == "variant" and VARIANT_RE.fullmatch(name):
            owner, sep, label = description.partition(": ")
            if sep and LAYOUT_RE.fullmatch(owner):
                variants.setdefault(owner, []).append((name, label))
    return XkbCatalog(layouts, {owner: tuple(rows) for owner, rows in variants.items()})


def read_catalog(path: Path | None = None) -> XkbCatalog:
    """The system's catalog; empty when it cannot be read."""
    try:
        text = (path or EVDEV_LST).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return XkbCatalog()
    return parse_catalog(text)


def parse_system_keyboard(text: str) -> dict[str, str]:
    """``KEY=value`` assignments of ``/etc/default/keyboard``, shell quoting removed."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, raw = line.strip().partition("=")
        if not sep or not key.startswith("XKB"):
            continue
        try:
            words = shlex.split(raw, comments=True)
        except ValueError:
            continue
        values[key] = words[0] if words else ""
    return values


def read_system_keyboard(path: Path | None = None) -> dict[str, str]:
    """The system file's settings; empty when it cannot be read."""
    try:
        text = (path or SYSTEM_KEYBOARD).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    return parse_system_keyboard(text)


def read_configured(config_path: Path) -> tuple[str, str]:
    """The station's layout and variant from its config; blank when unset or unreadable."""
    try:
        import tomllib  # noqa: PLC0415 - Python 3.10 has none; the kiosk session never runs there

        with config_path.open("rb") as handle:
            data = tomllib.load(handle)
    except (ImportError, OSError, ValueError):  # ValueError: TOML syntax or not UTF-8
        return "", ""
    controller = data.get("controller")
    if not isinstance(controller, dict):
        return "", ""
    layout, variant = controller.get("keyboard_layout"), controller.get("keyboard_variant")
    return (
        layout.strip() if isinstance(layout, str) else "",
        variant.strip() if isinstance(variant, str) else "",
    )


def _first(value: str) -> str:
    return value.split(",", 1)[0].strip()


def resolve_settings(
    layout: str,
    variant: str,
    *,
    catalog: XkbCatalog,
    system: dict[str, str],
) -> Resolution:
    """Resolve a station setting against the catalog and the system file."""
    if layout in catalog.layouts:
        return Resolution(layout, variant if catalog.has_variant(layout, variant) else "", "config")
    system_layout = _first(system.get("XKBLAYOUT", ""))
    if system_layout in catalog.layouts:
        system_variant = _first(system.get("XKBVARIANT", ""))
        model = system.get("XKBMODEL", "")
        options = system.get("XKBOPTIONS", "")
        return Resolution(
            system_layout,
            system_variant if catalog.has_variant(system_layout, system_variant) else "",
            "system",
            model if _WORD_RE.fullmatch(model) else "",
            options if _OPTIONS_RE.fullmatch(options) else "",
        )
    return Resolution(DEFAULT_LAYOUT, "", "default")


def resolve(
    config_path: Path,
    *,
    catalog_path: Path | None = None,
    system_path: Path | None = None,
) -> Resolution:
    """What the session starts with for the config at ``config_path``. Never raises."""
    layout, variant = read_configured(config_path)
    return resolve_settings(
        layout,
        variant,
        catalog=read_catalog(catalog_path),
        system=read_system_keyboard(system_path),
    )


def main(argv: list[str]) -> int:
    """``keyboard-layout env [CONFIG]``: one ``key=value`` line per setting."""
    if not argv or argv[0] != "env" or len(argv) > 2:
        print("usage: keyboard-layout env [CONFIG]", file=sys.stderr)
        return 2
    found = resolve(Path(argv[1] if len(argv) == 2 else "config.toml"))
    print(f"layout={found.layout}")
    print(f"variant={found.variant}")
    print(f"source={found.source}")
    print(f"model={found.model}")
    print(f"options={found.options}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
