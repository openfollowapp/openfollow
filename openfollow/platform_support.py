# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Michel Honold and Paul Hermann
"""Whether this station runs on a platform OpenFollow supports.

Supported: a Raspberry Pi of generation 5 or later (Pi 5, CM5, Pi 500), and on
hardware that is not a Pi, Debian 13 or later or a Debian derivative, each with a
64-bit OS and no desktop. Anything else (an older Pi, macOS, Windows, another
Linux, a 32-bit OS, a desktop install) is unsupported, and every surface warns
with the same words unless ``ui.developer_mode`` is on.
:func:`current` detects once per process; hardware and OS don't change at runtime.
"""

from __future__ import annotations

import functools
import os
import re
import struct
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "SHORT_STEP",
    "STEP",
    "PlatformSupport",
    "current",
    "detect",
    "stats_entry",
    "warning_shown",
]

MODEL_PATHS: tuple[Path, ...] = (Path("/proc/device-tree/model"), Path("/sys/firmware/devicetree/base/model"))
OS_RELEASE_PATHS: tuple[Path, ...] = (Path("/etc/os-release"), Path("/usr/lib/os-release"))
# Every display manager (lightdm, gdm3, sddm) enables itself through this link; the boot
# target cannot tell, since a station whose kiosk starts with the session is graphical too.
DISPLAY_MANAGER = Path("/etc/systemd/system/display-manager.service")
# The OS's word size as the running Python sees it: a 32-bit OS on a Pi 5 boots a 64-bit kernel.
_POINTER_BITS = struct.calcsize("P") * 8

STEP = (
    "OpenFollow is not developed to work on this platform and errors are expected. "
    "No support is provided. Never use this setup in a show!"
)
# The Operator Screen's corner row holds two lines.
SHORT_STEP = "Errors are expected. Never use this setup in a show!"

FIRST_SUPPORTED_PI = 5
FIRST_SUPPORTED_DEBIAN = 13

# "Raspberry Pi 5 Model B", "Raspberry Pi Compute Module 5", "Raspberry Pi 500"; a Zero has no number.
_PI_GENERATION = re.compile(r"raspberry pi (?:compute module )?(\d+)", re.IGNORECASE)
_REVISION = re.compile(r"\s+rev\s+\S+$", re.IGNORECASE)


@dataclass(frozen=True)
class PlatformSupport:
    supported: bool
    # What the station runs on, as the operator would name it: "Raspberry Pi 4 Model B", "macOS".
    name: str

    @property
    def title(self) -> str:
        return f"Unsupported platform: {self.name}"


def detect(
    platform: str = sys.platform,
    model_paths: Sequence[Path] = MODEL_PATHS,
    os_release_paths: Sequence[Path] = OS_RELEASE_PATHS,
    display_manager: Path = DISPLAY_MANAGER,
    bits: int = _POINTER_BITS,
) -> PlatformSupport:
    if platform == "darwin":
        return PlatformSupport(False, "macOS")
    if platform == "win32":
        return PlatformSupport(False, "Windows")
    if not platform.startswith("linux"):
        return PlatformSupport(False, platform)
    model = _first_text(model_paths)
    if "raspberry pi" in model.lower():
        support = _pi_support(model)
    else:
        support = _os_support(_os_release(os_release_paths))
    if not support.supported:
        return support
    if bits < 64:
        return PlatformSupport(False, f"{support.name} with a 32-bit OS")
    # A dangling link still means a display manager is enabled.
    if os.path.lexists(display_manager):
        return PlatformSupport(False, f"{support.name} with a desktop")
    return support


@functools.cache
def current() -> PlatformSupport:
    return detect()


def warning_shown(config: Any) -> bool:
    """Whether the unsupported-platform warning shows; developer mode hides it."""
    return not current().supported and not config.ui.developer_mode


def stats_entry(config: Any) -> dict[str, Any]:
    """The ``system.platform`` entry of the runtime stats snapshot."""
    support = current()
    return {"supported": support.supported, "name": support.name, "warning": warning_shown(config)}


def _first_text(paths: Sequence[Path]) -> str:
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore").strip("\x00").strip()
        except OSError:
            continue
        if text:
            return text
    return ""


def _pi_support(model: str) -> PlatformSupport:
    name = _REVISION.sub("", model)
    match = _PI_GENERATION.search(model)
    if match is None:
        return PlatformSupport(False, name)
    number = int(match.group(1))
    # The keyboard models carry the generation in the hundreds: Pi 400, Pi 500.
    generation = number // 100 if number >= 100 else number
    return PlatformSupport(generation >= FIRST_SUPPORTED_PI, name)


def _os_release(paths: Sequence[Path]) -> dict[str, str]:
    for path in paths:
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        fields = {}
        for line in lines:
            key, sep, value = line.partition("=")
            if sep and not key.lstrip().startswith("#"):
                fields[key.strip()] = value.strip().strip("\"'")
        return fields
    return {}


def _os_support(fields: dict[str, str]) -> PlatformSupport:
    name = fields.get("PRETTY_NAME") or fields.get("NAME") or "Linux"
    if "debian" in fields.get("ID_LIKE", "").lower().split():
        return PlatformSupport(True, name)
    if fields.get("ID", "").lower() != "debian":
        return PlatformSupport(False, name)
    version = fields.get("VERSION_ID", "")
    # Debian testing and unstable carry no VERSION_ID and are newer than any release.
    if not version:
        return PlatformSupport(True, name)
    try:
        return PlatformSupport(int(version.split(".")[0]) >= FIRST_SUPPORTED_DEBIAN, name)
    except ValueError:
        return PlatformSupport(False, name)
