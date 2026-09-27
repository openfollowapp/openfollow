# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""What the 3D Mouse subsystem observed, and the sentences that say it.

One module owns the state names and the operator-facing text, so the web
section, ``/api/stats`` and the diagnostics bundle cannot describe the same
puck differently. The state values are a wire interface: ``/api/stats``
publishes them for support tooling.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

__all__ = [
    "BackendState",
    "DeviceState",
    "Mouse3DDeviceStatus",
    "Mouse3DStatus",
    "Notice",
    "none_connected",
    "status_key",
    "status_notices",
]


class BackendState(Enum):
    OK = "ok"
    NOT_INSTALLED = "not_installed"
    # Installed, but the HID library won't load or listing devices raises.
    COULD_NOT_START = "could_not_start"


class DeviceState(Enum):
    OPEN = "open"
    # Before the first open attempt, or reopening after a read failure.
    OPENING = "opening"
    NO_PROFILE = "no_profile"
    NOT_PERMITTED = "not_permitted"
    OPEN_FAILED = "open_failed"


@dataclass(frozen=True)
class Mouse3DDeviceStatus:
    path: str
    product_name: str
    vendor_id: int
    product_id: int
    port_key: str | None
    state: DeviceState
    # The open failure's own wording; bundle and /api/stats only.
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "product_name": self.product_name,
            "usb_id": f"{self.vendor_id:04x}:{self.product_id:04x}",
            "port_key": self.port_key,
            "state": self.state.value,
            "error": self.error,
        }


@dataclass(frozen=True)
class Mouse3DStatus:
    enabled: bool
    # False where the backend cannot read a puck at all; nothing is scanned.
    supported: bool
    # The first scan, and each puck it found, has had its first open attempt.
    scanned: bool
    backend: BackendState = BackendState.OK
    backend_error: str = ""
    backend_version: str = ""
    devices: tuple[Mouse3DDeviceStatus, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "supported": self.supported,
            "scanned": self.scanned,
            "backend": self.backend.value,
            "backend_error": self.backend_error,
            "backend_version": self.backend_version,
            "devices": [device.to_dict() for device in self.devices],
        }


@dataclass(frozen=True)
class Notice:
    """One status box: ``level`` is a STATUS_LANGUAGE level, ``step`` its second line."""

    level: str
    text: str
    step: str = ""


_UNSUPPORTED_PLATFORM = "3D Mouse input is not supported on macOS by this version of OpenFollow."
_REINSTALL = "Reinstall OpenFollow."
_BACKEND_NOTICES = {
    BackendState.NOT_INSTALLED.value: Notice("error", "3D Mouse support is not installed.", _REINSTALL),
    BackendState.COULD_NOT_START.value: Notice(
        "error", "3D Mouse support could not start, so no 3D Mouse can be read.", _REINSTALL
    ),
}
# ``{device}`` is the product name and its USB id, e.g. ``SpaceNavigator (046d:c626)``.
_DEVICE_NOTICES = {
    DeviceState.NO_PROFILE.value: (
        "{device} is not supported by this version of OpenFollow.",
        "Use a supported model.",
    ),
    DeviceState.NOT_PERMITTED.value: (
        "{device} was found, but OpenFollow is not allowed to open it.",
        "Reinstall OpenFollow, then replug the 3D Mouse.",
    ),
    DeviceState.OPEN_FAILED.value: (
        "{device} was found, but could not be opened.",
        "Unplug it and plug it back in.",
    ),
}


def status_notices(block: Mapping[str, Any]) -> list[Notice]:
    """The boxes the 3D Mouse section shows for a published status block.

    Only faults get a box, plus the platform statement, which shows whether the
    feature is enabled or not. A working puck is listed under Controller Slots.
    """
    if not block.get("supported", True):
        return [Notice("info", _UNSUPPORTED_PLATFORM)]
    if not block.get("enabled"):
        return []
    backend = _BACKEND_NOTICES.get(str(block.get("backend", BackendState.OK.value)))
    if backend is not None:
        return [backend]
    notices = []
    for device in block.get("devices") or []:
        template = _DEVICE_NOTICES.get(str(device.get("state", "")))
        if template is None:
            continue
        name = f"{device.get('product_name') or '3D Mouse'} ({device.get('usb_id', '')})"
        text, step = template
        notices.append(Notice("error", text.format(device=name), step))
    return notices


def none_connected(block: Mapping[str, Any]) -> bool:
    """Enabled and able to look, the first scan done, and nothing found."""
    return bool(
        block.get("supported", True)
        and block.get("enabled")
        and block.get("backend") == BackendState.OK.value
        and block.get("scanned")
        and not block.get("devices")
    )


def status_key(block: Mapping[str, Any]) -> str:
    """Identify what the section shows, so an unchanged poll can answer 204."""
    shown = repr((status_notices(block), none_connected(block)))
    return hashlib.sha256(shown.encode("utf-8")).hexdigest()[:12]
