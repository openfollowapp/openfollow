# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""OSC path commands: switch a path on or off, lock a marker onto one or release it.

Wire format::

    /path/<name>/active  <on>       ; 1 / 0, true / false
    /marker/<id>/path    <name>     ; lock marker <id> onto that path (it slides there)
    /marker/<id>/path               ; release marker <id>

Path names match ignoring case. Commands are queued here and applied on the
frame loop, which owns the config and the path engine. Like the operator-message
adapter this only subscribes on the shared service: the listener, and with it
``[osc] enabled`` and the sender allowlist, belong to the marker adapter.
"""

from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass
from typing import Any

from openfollow.osc.service import OscService

logger = logging.getLogger(__name__)

_TRUE = frozenset({"1", "true", "on", "yes"})
_FALSE = frozenset({"0", "false", "off", "no"})


@dataclass(frozen=True)
class PathActive:
    name: str
    active: bool


@dataclass(frozen=True)
class MarkerPath:
    marker_id: int
    # None releases the marker.
    name: str | None


PathCommand = PathActive | MarkerPath


class PathOscAdapter:
    """Queues ``/path/<name>/active`` and ``/marker/<id>/path`` for the frame loop."""

    _ACTIVE_PATTERN = "/path/*/active"
    _MARKER_PATTERN = "/marker/*/path"

    def __init__(self, service: OscService) -> None:
        self._service = service
        self._lock = threading.Lock()
        self._pending: list[PathCommand] = []
        self._started = False

    def start(self) -> None:
        """Subscribe both handlers. Idempotent."""
        if self._started:
            return
        self._service.subscribe(self._ACTIVE_PATTERN, self._handle_active)
        self._service.subscribe(self._MARKER_PATTERN, self._handle_marker)
        self._started = True

    def stop(self) -> None:
        """Unsubscribe both handlers. Idempotent. Does not touch the listener."""
        if not self._started:
            return
        self._service.unsubscribe(self._ACTIVE_PATTERN)
        self._service.unsubscribe(self._MARKER_PATTERN)
        self._started = False

    def take(self) -> list[PathCommand]:
        """Every command received since the last call, oldest first."""
        with self._lock:
            pending, self._pending = self._pending, []
        return pending

    def _push(self, command: PathCommand) -> None:
        with self._lock:
            self._pending.append(command)

    def _handle_active(self, address: str, *args: Any) -> None:
        parts = address.strip("/").split("/")
        if len(parts) != 3 or not parts[1]:
            return
        active = _as_switch(args[0]) if len(args) == 1 else None
        if active is None:
            logger.debug("OSC %s: expected one on/off argument, got %r - ignoring", address, args)
            return
        self._push(PathActive(parts[1], active))

    def _handle_marker(self, address: str, *args: Any) -> None:
        parts = address.strip("/").split("/")
        if len(parts) != 3:
            return
        try:
            marker_id = int(parts[1])
        except ValueError:
            return
        if len(args) > 1 or (args and not isinstance(args[0], str)):
            logger.debug("OSC %s: expected a path name or nothing, got %r - ignoring", address, args)
            return
        name = args[0].strip() if args else ""
        self._push(MarkerPath(marker_id, name or None))


def _as_switch(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value != 0 if math.isfinite(value) else None
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _TRUE:
            return True
        if lowered in _FALSE:
            return False
    return None
