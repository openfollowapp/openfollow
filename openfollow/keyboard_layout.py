#!/usr/bin/python3 -I
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The keyboard layout the kiosk session starts with.

The station's own setting (``controller.keyboard_layout`` / ``keyboard_variant``
in ``config.toml``) wins when the system knows that layout, then the system's
``/etc/default/keyboard``, then ``us``. The system knows a layout its xkb rules
list and its xkb data ships symbols for. A variant the chosen layout does not
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
    "XKB_ROOT",
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

XKB_ROOT = Path("/usr/share/X11/xkb")
EVDEV_LST = XKB_ROOT / "rules" / "evdev.lst"
SYSTEM_KEYBOARD = Path("/etc/default/keyboard")
DEFAULT_LAYOUT = "us"

LAYOUT_RE = re.compile(r"[a-z0-9_-]{1,32}")
VARIANT_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
# What the launcher accepts on each output line; anything else it ignores.
_WORD_RE = re.compile(r"[A-Za-z0-9_-]*")
_OPTION_RE = re.compile(r"[A-Za-z0-9_:-]+")
_CATALOG_LINE_RE = re.compile(r"^\s+(\S+)\s+(.+?)\s*$")
_ASSIGNMENT_RE = re.compile(r"(?:export\s+)?(XKB\w*)=(.*)")
# Listed in the rules but needs files a user adds; without them no keymap compiles.
_CUSTOM_OPTION_GROUP = "custom:"


@dataclass(frozen=True)
class XkbCatalog:
    """The layouts and variants the system's xkb rules know, with their descriptions, and its options."""

    layouts: dict[str, str] = field(default_factory=dict)
    variants: dict[str, tuple[tuple[str, str], ...]] = field(default_factory=dict)
    options: frozenset[str] = frozenset()

    def variants_of(self, layout: str) -> tuple[tuple[str, str], ...]:
        return self.variants.get(layout, ())

    def has_variant(self, layout: str, variant: str) -> bool:
        return any(name == variant for name, _ in self.variants_of(layout))


@dataclass(frozen=True)
class Resolution:
    """What the session starts with, and which source decided it.

    ``model`` / ``options`` come from the system file whatever the ``source``:
    they describe the keyboard, not the layout.
    """

    layout: str
    variant: str
    source: str
    model: str = ""
    options: str = ""


def parse_catalog(text: str, symbols: Path) -> XkbCatalog:
    """Read an xkb ``evdev.lst``, keeping only the layouts with a file in the ``symbols`` directory."""
    layouts: dict[str, str] = {}
    variants: dict[str, list[tuple[str, str]]] = {}
    options: set[str] = set()
    section = ""
    for line in text.splitlines():
        if line.startswith("!"):
            section = line[1:].strip()
            continue
        match = _CATALOG_LINE_RE.match(line)
        if match is None:
            continue
        name, description = match.groups()
        if section == "layout" and LAYOUT_RE.fullmatch(name) and (symbols / name).is_file():
            layouts[name] = description
        elif section == "variant" and VARIANT_RE.fullmatch(name):
            owner, sep, label = description.partition(": ")
            if sep and LAYOUT_RE.fullmatch(owner):
                variants.setdefault(owner, []).append((name, label))
        elif section == "option" and _OPTION_RE.fullmatch(name) and not name.startswith(_CUSTOM_OPTION_GROUP):
            options.add(name)
    return XkbCatalog(layouts, {owner: tuple(rows) for owner, rows in variants.items()}, frozenset(options))


def read_catalog(path: Path | None = None, xkb_root: Path | None = None) -> XkbCatalog:
    """The system's catalog; empty when it cannot be read."""
    try:
        text = (path or EVDEV_LST).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return XkbCatalog()
    return parse_catalog(text, (xkb_root or XKB_ROOT) / "symbols")


def parse_system_keyboard(text: str) -> dict[str, str]:
    """``KEY=value`` assignments of ``/etc/default/keyboard``, shell quoting removed."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        match = _ASSIGNMENT_RE.fullmatch(line.strip())
        if match is None:
            continue
        key, raw = match.groups()
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


def _known_options(value: str, catalog: XkbCatalog) -> str:
    return ",".join(option for option in (part.strip() for part in value.split(",")) if option in catalog.options)


def resolve_settings(
    layout: str,
    variant: str,
    *,
    catalog: XkbCatalog,
    system: dict[str, str],
) -> Resolution:
    """Resolve a station setting against the catalog and the system file."""
    model = system.get("XKBMODEL", "")
    keyboard = (model if _WORD_RE.fullmatch(model) else "", _known_options(system.get("XKBOPTIONS", ""), catalog))
    if layout in catalog.layouts:
        return Resolution(layout, variant if catalog.has_variant(layout, variant) else "", "config", *keyboard)
    system_layout = _first(system.get("XKBLAYOUT", ""))
    if system_layout in catalog.layouts:
        system_variant = _first(system.get("XKBVARIANT", ""))
        variant = system_variant if catalog.has_variant(system_layout, system_variant) else ""
        return Resolution(system_layout, variant, "system", *keyboard)
    return Resolution(DEFAULT_LAYOUT, "", "default", *keyboard)


def resolve(
    config_path: Path,
    *,
    catalog_path: Path | None = None,
    system_path: Path | None = None,
    xkb_root: Path | None = None,
) -> Resolution:
    """What the session starts with for the config at ``config_path``. Never raises."""
    layout, variant = read_configured(config_path)
    return resolve_settings(
        layout,
        variant,
        catalog=read_catalog(catalog_path, xkb_root),
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
