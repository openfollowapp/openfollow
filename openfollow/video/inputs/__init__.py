# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Video input plugin registry with pkgutil auto-discovery.

Imports each module under this package to trigger ``VideoInputBase`` subclass
registration, then exposes lookup helpers (``get_registry``,
``get_available_registry``, ``get_input_class``) and USB-camera enumeration for
the diagnostics bundle.
"""

from __future__ import annotations

import importlib
import inspect
import logging
import os
import pkgutil
import platform

from openfollow.video.inputs._base import VideoInputBase

from openfollow.i18n import _l  # noqa: E402  # i18n lazy string for plugin display_name

logger = logging.getLogger(__name__)

_registry: dict[str, type[VideoInputBase]] = {}
_discovered = False


def _discover_inputs() -> None:
    """Import all modules in this package to trigger subclass registration."""
    global _discovered
    if _discovered:
        return
    _discovered = True

    package_path = __path__
    for _finder, name, _ispkg in pkgutil.iter_modules(package_path):
        if name.startswith("_"):
            continue
        try:
            importlib.import_module(f"{__name__}.{name}")
        except Exception:
            logger.warning("Failed to load video input plugin: %s", name, exc_info=True)

    for cls in VideoInputBase.__subclasses__():
        if not cls.input_id or cls.input_id in _registry:
            continue
        # Runtime enforcement: reject accidentally-abstract subclasses
        if inspect.isabstract(cls):  # pragma: no cover – defensive
            logger.warning(
                "Skipping abstract video input plugin: %s (missing abstract method implementations)",
                cls.__name__,
            )
            continue
        _registry[cls.input_id] = cls  # type: ignore[type-abstract]
        logger.info("Registered video input: %s (%s)", cls.input_id, cls.display_name)


def get_registry() -> dict[str, type[VideoInputBase]]:
    """Return {input_id: class} for all discovered input plugins."""
    _discover_inputs()
    return dict(_registry)


def get_available_registry() -> dict[str, type[VideoInputBase]]:
    """Return {input_id: class} for plugins whose is_available() is True."""
    _discover_inputs()
    return {iid: cls for iid, cls in _registry.items() if cls.is_available()[0]}


def get_input_class(input_id: str) -> type[VideoInputBase] | None:
    """Look up a video input class by its input_id."""
    _discover_inputs()
    return _registry.get(input_id)


def get_available_input_ids() -> list[str]:
    """Return sorted list of input type IDs whose ``is_available()`` is True."""
    return sorted(get_available_registry().keys())


def _v4l2_node_is_usb(dev_path: str) -> bool:
    """True if /dev/videoN is backed by USB, not platform codec/ISP."""
    node = os.path.basename(dev_path)
    target = os.path.realpath(f"/sys/class/video4linux/{node}/device")
    return "/usb" in target


def usb_camera_names() -> list[str]:
    """Return human-readable names of USB-class cameras for diagnostics bundle."""
    system = platform.system()
    try:
        if system == "Linux":
            from openfollow.video.inputs.v4l2 import _discover_v4l2_devices

            names: set[str] = set()
            for d in _discover_v4l2_devices():
                name = d.get("name")
                if name and _v4l2_node_is_usb(d.get("path", "")):
                    names.add(name)
            return sorted(names)
        if system == "Darwin":
            from openfollow.video.inputs.avf import _discover_avf_devices

            return [d["name"] for d in _discover_avf_devices() if d.get("name")]
    except Exception:  # noqa: BLE001 – never abort the bundle on a backend hiccup
        logger.warning("usb_camera_names enumeration failed", exc_info=True)
    return []
