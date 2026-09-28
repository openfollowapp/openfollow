# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Saving the diagnostics bundle to a removable drive, one export at a time.

Collecting the bundle takes up to ~20 s, which the GTK main loop must never
wait on: the HUD starts an export on a worker thread (``start``) and reads
``status()`` each frame, while the web UI runs one in its own request thread
(``run``). Both share one slot, so a second export while one runs is refused.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace

from openfollow.runtime.removable_media import MediaError, WriteResult

logger = logging.getLogger(__name__)

IDLE = "idle"
COLLECTING = "collecting"
WRITING = "writing"
DONE = "done"

HUD = "hud"
WEB = "web"

# The next step after a failed export, on the Operator Screen and the web UI alike.
RETRY = "Pick a USB storage device to try again."


@dataclass(frozen=True)
class ExportStatus:
    """Where the latest export is. ``generation`` counts exports, so a reader tells a new result from a seen one."""

    phase: str = IDLE
    origin: str = ""
    drive: str = ""
    ok: bool | None = None
    message: str = ""
    generation: int = 0
    # The one next step, on success.
    action: str = ""


class ExportBusy(Exception):
    """Another export holds the slot."""

    def __init__(self) -> None:
        super().__init__("Another diagnostics export is still running.")


class DiagnosticsExport:
    """Builds the bundle and saves it to a drive; ``build`` returns ``(filename, text)``."""

    def __init__(
        self,
        build: Callable[[], tuple[str, str]],
        write: Callable[[str, str, bytes], WriteResult],
    ) -> None:
        self._build = build
        self._write = write
        self._slot = threading.Lock()
        self._lock = threading.Lock()
        self._status = ExportStatus()

    def status(self) -> ExportStatus:
        with self._lock:
            return self._status

    def run(self, media_id: str, drive: str, origin: str) -> ExportStatus:
        """Export in the caller's thread and return the result; raises :class:`ExportBusy`."""
        if not self._slot.acquire(blocking=False):
            raise ExportBusy()
        self._begin(drive, origin)
        self._export(media_id)
        return self.status()

    def start(self, media_id: str, drive: str, origin: str) -> bool:
        """Export on a worker thread; ``False`` when another export is running."""
        if not self._slot.acquire(blocking=False):
            return False
        self._begin(drive, origin)
        try:
            threading.Thread(target=self._export, args=(media_id,), daemon=True, name="DiagnosticsExport").start()
        except RuntimeError:
            logger.exception("Starting the diagnostics export failed.")
            self._finish(False, "The export could not be started.")
        return True

    def _begin(self, drive: str, origin: str) -> None:
        with self._lock:
            self._status = ExportStatus(COLLECTING, origin, drive, generation=self._status.generation + 1)

    def _set_phase(self, phase: str) -> None:
        with self._lock:
            self._status = replace(self._status, phase=phase)

    def _finish(self, ok: bool, message: str, action: str = "") -> None:
        with self._lock:
            self._status = replace(self._status, phase=DONE, ok=ok, message=message, action=action)
        self._slot.release()

    def _export(self, media_id: str) -> None:
        """Holds the slot on entry and releases it in ``_finish``, whatever happens."""
        try:
            filename, text = self._build()
        except Exception:
            logger.exception("Collecting the diagnostics bundle failed.")
            self._finish(False, "The diagnostics could not be collected.")
            return
        self._set_phase(WRITING)
        try:
            result = self._write(media_id, filename, text.encode("utf-8"))
        except MediaError as exc:
            logger.warning("Saving the diagnostics bundle to %s failed: %s", self.status().drive, exc)
            self._finish(False, str(exc))
        except Exception:
            logger.exception("Saving the diagnostics bundle failed.")
            self._finish(False, "The file could not be saved.")
        else:
            media = result.media
            logger.info("Saved the diagnostics bundle as %s to %s (%s).", result.filename, media.name, media.device)
            self._finish(True, result.message, result.action)
