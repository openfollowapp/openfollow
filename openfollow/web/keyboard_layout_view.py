# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The Keyboard Layout row of the Keyboard Input section.

Shown only where the packaged kiosk session resolved the layout, which it marks
by exporting ``OPENFOLLOW_KEYBOARD_SOURCE``: anywhere else the setting could not
take effect. The layout in use is what that session exported; a saved setting
that resolves differently takes effect on the next restart.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from openfollow import keyboard_layout
from openfollow.configuration import AppConfig

KEYBOARD_SOURCE_ENV = "OPENFOLLOW_KEYBOARD_SOURCE"
_SYSTEM_PREFIX = "System – "

_cache_lock = threading.Lock()
_cache: tuple[tuple[str, int, str, int], keyboard_layout.XkbCatalog] | None = None
_SESSION_ENV = ("XKB_DEFAULT_LAYOUT", "XKB_DEFAULT_VARIANT", "XKB_DEFAULT_MODEL", "XKB_DEFAULT_OPTIONS")


def station_catalog() -> keyboard_layout.XkbCatalog:
    """The station's xkb catalog, re-read only when the rules file or the symbols directory changes."""
    global _cache
    path = Path(keyboard_layout.EVDEV_LST)
    root = Path(keyboard_layout.XKB_ROOT)
    symbols = root / "symbols"
    try:
        key = (str(path), path.stat().st_mtime_ns, str(symbols), symbols.stat().st_mtime_ns)
    except OSError:
        return keyboard_layout.XkbCatalog()
    with _cache_lock:
        if _cache is None or _cache[0] != key:
            _cache = (key, keyboard_layout.read_catalog(path, root))
        return _cache[1]


def variant_options(catalog: keyboard_layout.XkbCatalog, layout: str) -> list[tuple[str, str]]:
    """``(value, label)`` pairs for the Variant picker: ``Standard`` first, then the layout's own."""
    return [("", "Standard"), *catalog.variants_of(layout)]


def _describe(catalog: keyboard_layout.XkbCatalog, layout: str, variant: str) -> str:
    for name, label in catalog.variants_of(layout):
        if name == variant:
            return label
    return catalog.layouts.get(layout, layout)


@dataclass(frozen=True)
class KeyboardLayoutView:
    layouts: list[tuple[str, str]]
    variants: list[tuple[str, str]]
    layout: str
    variant: str
    # A restart would change the keyboard in use: its layout, or only the system file's model or options.
    pending: bool
    layout_pending: bool
    in_use_label: str


def build_keyboard_layout_view(
    cfg: AppConfig,
    environ: Mapping[str, str] | None = None,
) -> KeyboardLayoutView | None:
    """The row's content, or ``None`` where the kiosk session did not resolve the layout."""
    env = os.environ if environ is None else environ
    if KEYBOARD_SOURCE_ENV not in env:
        return None
    catalog = station_catalog()
    system = keyboard_layout.read_system_keyboard()
    on_system = keyboard_layout.resolve_settings("", "", catalog=catalog, system=system)
    saved = keyboard_layout.resolve_settings(
        cfg.controller.keyboard_layout,
        cfg.controller.keyboard_variant,
        catalog=catalog,
        system=system,
    )
    layout = cfg.controller.keyboard_layout if cfg.controller.keyboard_layout in catalog.layouts else ""
    variant = cfg.controller.keyboard_variant if catalog.has_variant(layout, cfg.controller.keyboard_variant) else ""
    in_use = tuple(env.get(name, "") for name in _SESSION_ENV)
    return KeyboardLayoutView(
        layouts=[
            ("", _SYSTEM_PREFIX + _describe(catalog, on_system.layout, on_system.variant)),
            *sorted(catalog.layouts.items(), key=lambda item: (item[1].casefold(), item[0])),
        ],
        variants=variant_options(catalog, layout),
        layout=layout,
        variant=variant,
        pending=(saved.layout, saved.variant, saved.model, saved.options) != in_use,
        layout_pending=(saved.layout, saved.variant) != in_use[:2],
        in_use_label=_describe(catalog, in_use[0], in_use[1]),
    )
