# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Name the Pi camera in the boot configuration, starting it now where the kernel allows."""

from __future__ import annotations

from collections.abc import Callable

from openfollow.privilege.broker import PrivilegeBroker, PrivilegeError
from openfollow.privilege.camera_config import AUTOMATIC, Camera, CameraSetupState, read_camera_setup
from openfollow.privilege.capabilities import (
    CAMERA_CONFIG_WRITE,
    CAMERA_OVERLAY_LOAD,
    CAMERA_OVERLAY_UNLOAD,
    CAMERA_SETUP_SCRIPT,
    SYSTEM_REBOOT,
)

_TIMEOUT_S = 30.0


def apply_camera(
    broker: PrivilegeBroker,
    choice: Camera | None,
    *,
    read: Callable[[], CameraSetupState] = read_camera_setup,
    release: Callable[[], bool] = lambda: True,
) -> tuple[CameraSetupState, bool]:
    """Name *choice* in config.txt (None: back to auto-detect), and load it now if possible.

    Returns the state afterwards, and whether the loaded camera changed, so the
    caller can rebuild a Pi Camera pipeline. A camera loaded at boot cannot be
    unloaded while running: then only config.txt changes, and the state reports
    a pending restart. *release* runs before a live camera is unloaded and must
    stop anything streaming from it; when it returns False the change waits for
    a restart too. Raises :class:`PrivilegeError` when the change is refused.
    """
    state = read()
    if not state.available:
        raise PrivilegeError(state.reason)
    if choice is not None and choice.sensor not in state.sensors:
        raise PrivilegeError(f"This station has no {choice.sensor} camera overlay.")
    token = choice.token() if choice is not None else AUTOMATIC
    broker.run(
        CAMERA_CONFIG_WRITE,
        [CAMERA_SETUP_SCRIPT, "write", token],
        reason="Name the Pi camera in the boot configuration",
        timeout=_TIMEOUT_S,
    )
    changed = False
    loaded_at_boot = [camera for camera in state.active if camera != state.live]
    if not loaded_at_boot:
        if state.live is not None and state.live != choice:
            if not release():
                return read(), False
            broker.run(
                CAMERA_OVERLAY_UNLOAD,
                [CAMERA_SETUP_SCRIPT, "unload", state.live.token()],
                reason="Stop the Pi camera started earlier",
                timeout=_TIMEOUT_S,
            )
            changed = True
        if choice is not None and choice != state.live:
            broker.run(
                CAMERA_OVERLAY_LOAD,
                [CAMERA_SETUP_SCRIPT, "load", token],
                reason="Start the Pi camera",
                timeout=_TIMEOUT_S,
            )
            changed = True
    return read(), changed


def restart_station(broker: PrivilegeBroker) -> None:
    """Reboot, so a camera change that could not be made live takes effect."""
    broker.run(
        SYSTEM_REBOOT,
        ["/usr/bin/systemctl", "reboot"],
        reason="Restart the station to load the camera",
        timeout=_TIMEOUT_S,
    )
