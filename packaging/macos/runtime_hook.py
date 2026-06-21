# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""PyInstaller runtime hook: point the bundled native stack at itself.

Runs before the launcher (and therefore before any ``gi`` import), so GTK,
GObject-Introspection, GStreamer, and GdkPixbuf resolve their typelibs, plugins,
loaders, schemas, and icon themes from inside the .app rather than from a
Homebrew prefix that won't exist on a clean Mac.

Defensive by design: PyInstaller's exact collected layout shifts between
versions, so each var is set to the first bundled path that actually exists.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "OpenFollow"


def _meipass() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def _first_existing(*candidates: Path) -> Path | None:
    return next((p for p in candidates if p.exists()), None)


def _set_path(var: str, path: Path | None) -> None:
    if path is not None:
        os.environ[var] = str(path)


def _writable_cache_dir() -> Path:
    cache = Path.home() / "Library" / "Caches" / APP_NAME
    cache.mkdir(parents=True, exist_ok=True)
    return cache


def configure(root: Path) -> None:
    """Set the GI / GStreamer / GTK / GdkPixbuf env vars relative to ``root``."""
    # GObject-Introspection typelibs.
    _set_path(
        "GI_TYPELIB_PATH",
        _first_existing(
            root / "gi_typelibs",
            root / "girepository-1.0",
            root / "lib" / "girepository-1.0",
        ),
    )

    # GStreamer plugins. The bundled plugins live in gst_plugins/. The VERSIONED
    # GST_PLUGIN_SYSTEM_PATH_1_0 *replaces* GStreamer's computed default scan path
    # (which, for a relocated build, resolves to the bundle's Frameworks/ root and
    # would recursively dlopen every .so there - matplotlib/cv2/... - as a would-be
    # plugin, crashing on dlclose). Pin both the versioned and legacy names so the
    # scan is confined to gst_plugins/. Forking the scanner is fragile in a
    # relocated bundle, so scan in-process with a per-user writable registry.
    plugins = _first_existing(
        root / "gst_plugins",
        root / "gstreamer-1.0",
        root / "lib" / "gstreamer-1.0",
    )
    if plugins is not None:
        for var in ("GST_PLUGIN_SYSTEM_PATH_1_0", "GST_PLUGIN_SYSTEM_PATH", "GST_PLUGIN_PATH_1_0", "GST_PLUGIN_PATH"):
            os.environ[var] = str(plugins)
    os.environ["GST_REGISTRY_FORK"] = "no"
    registry = str(_writable_cache_dir() / "gstreamer-1.0.registry.bin")
    os.environ["GST_REGISTRY_1_0"] = registry
    os.environ["GST_REGISTRY"] = registry

    # GdkPixbuf loaders (PNG / SVG used by the HUD + about screen).
    _set_path(
        "GDK_PIXBUF_MODULE_FILE",
        _first_existing(
            root / "lib" / "gdk-pixbuf-2.0" / "2.10.0" / "loaders.cache",
            root / "gdk_pixbuf" / "loaders.cache",
        ),
    )

    # GTK theme / schema / icon lookup.
    os.environ["GTK_EXE_PREFIX"] = str(root)
    os.environ["GTK_PATH"] = str(root)
    share = _first_existing(root / "share")
    if share is not None:
        existing = os.environ.get("XDG_DATA_DIRS", "")
        os.environ["XDG_DATA_DIRS"] = f"{share}:{existing}" if existing else str(share)
    _set_path(
        "GSETTINGS_SCHEMA_DIR",
        _first_existing(root / "share" / "glib-2.0" / "schemas"),
    )
    _set_path("FONTCONFIG_PATH", _first_existing(root / "etc" / "fonts"))


configure(_meipass())
