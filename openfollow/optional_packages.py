# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Michel Honold and Paul Hermann
"""System packages a feature needs but the package does not depend on.

An update never installs one: an offline station cannot download it, and a
failed download fails the whole update. So the station names what is missing
instead. Each check reads the capability, not the package name, since a
derivative may ship it under another one.
"""

from __future__ import annotations

import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class MissingPackage:
    name: str
    effect: str


NETWORK_MANAGER = MissingPackage("NetworkManager", "network page read only")
WEBKIT = MissingPackage("WebKit", "Open Web UI unavailable")


def _has_command(name: str) -> bool:
    return shutil.which(name) is not None


def network_manager_missing(
    *,
    writable: bool,
    platform: str | None = None,
    has_command: Callable[[str], bool] | None = None,
) -> bool:
    """``True`` on Linux when ``nmcli`` is absent and no other backend can write the network."""
    has = has_command or _has_command
    return (platform or sys.platform).startswith("linux") and not writable and not has("nmcli")


def _webkit_available() -> bool:
    from openfollow.runtime import webkit_browser  # noqa: PLC0415

    return webkit_browser.AVAILABLE


def missing(
    *,
    network_writable: bool,
    platform: str | None = None,
    has_command: Callable[[str], bool] | None = None,
    webkit_available: Callable[[], bool] | None = None,
) -> list[MissingPackage]:
    """Every optional package this Linux station lacks; empty elsewhere."""
    if not (platform or sys.platform).startswith("linux"):
        return []
    out: list[MissingPackage] = []
    if network_manager_missing(writable=network_writable, platform=platform, has_command=has_command):
        out.append(NETWORK_MANAGER)
    if not (webkit_available or _webkit_available)():
        out.append(WEBKIT)
    return out
