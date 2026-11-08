# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""macOS: answer the quit Apple Event the Dock, logout, restart and shutdown send.

Under a bare ``Gtk.main()`` nothing calls ``-[NSApplication finishLaunching]``,
so AppKit installs no handler for it and answers "not handled": the app keeps
running and a logout waits on it. A Carbon Apple Event handler fills that gap
on its own, with no app delegate. It answers at once and leaves the quit to
the caller, so the process ends through the app's own shutdown, never through
AppKit's ``exit()``.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

_CORE_SERVICES = "/System/Library/Frameworks/CoreServices.framework/CoreServices"

_CORE_EVENT_CLASS = int.from_bytes(b"aevt", "big")
_QUIT_APPLICATION = int.from_bytes(b"quit", "big")
_NO_ERR = 0
_ERR_AE_EVENT_FAILED = -10000

# OSErr (*AEEventHandlerProcPtr)(const AppleEvent *event, AppleEvent *reply, SRefCon refcon)
_AE_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int16, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)

# The installed handler. The Apple Event Manager keeps only its address, so
# dropping this reference frees the code it calls.
_handler: Any = None


def _load_core_services() -> Any:
    return ctypes.CDLL(_CORE_SERVICES)


def _quit_handler(on_quit: Callable[[], None]) -> Any:
    def handle(_event: Any, _reply: Any, _refcon: Any) -> int:
        logger.info("Quit requested by macOS (Dock, logout, restart or shutdown).")
        try:
            on_quit()
        except Exception:
            logger.exception("Could not start the quit macOS asked for.")
            return _ERR_AE_EVENT_FAILED
        return _NO_ERR

    return _AE_HANDLER(handle)


def install_quit_handler(
    on_quit: Callable[[], None],
    *,
    load_library: Callable[[], Any] = _load_core_services,
) -> bool:
    """Call ``on_quit`` whenever macOS asks the app to quit; True once the handler is installed.

    ``on_quit`` runs inside the main loop's event poll and must only schedule
    the quit. A later install replaces the handler. On other platforms nothing
    is installed; on macOS a refused install is logged and the app runs on.
    """
    global _handler
    if sys.platform != "darwin":
        return False
    handler = _quit_handler(on_quit)
    try:
        install = load_library().AEInstallEventHandler
        install.argtypes = [ctypes.c_uint32, ctypes.c_uint32, _AE_HANDLER, ctypes.c_void_p, ctypes.c_ubyte]
        install.restype = ctypes.c_int16
        error = install(_CORE_EVENT_CLASS, _QUIT_APPLICATION, handler, None, False)
    except Exception:
        logger.warning("macOS quit requests go unanswered: the Apple Event Manager is unavailable.", exc_info=True)
        return False
    if error != _NO_ERR:
        logger.warning("macOS quit requests go unanswered: AEInstallEventHandler returned %d.", error)
        return False
    _handler = handler
    return True
