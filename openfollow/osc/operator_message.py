# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""OSC operator-message input adapter.

Subscribes the operator-message + clear addresses on the shared
:class:`OscService` and routes incoming cues into an
:class:`~openfollow.operator_messages.OperatorMessageStore`. Positional args,
lenient numeric coercion, malformed packets dropped with a debug log.

Wire format::

    /message  <message:str> <info:str> <markerId:int> <seconds:float>
    /message/clear            ; clear all
    /message/clear  <id:int>  ; clear messages for marker <id>

Trailing args are optional: ``info`` defaults to ``""``, ``markerId`` to
``0`` (broadcast), ``seconds`` to ``0`` (forever). A present numeric arg that
can't be coerced drops the packet.

Ingest routing against the live ``controlled_marker_ids`` callback (when
``route_by_marker`` is on; off accepts everything so all stations show it):

- ``markerId == 0`` → accept (broadcast).
- ``markerId >= 1`` and in ``controlled_marker_ids`` → accept.
- otherwise → drop.

The adapter only subscribes on the shared service; the listener lifecycle
stays with :class:`OscMarkerAdapter`, so ingest is also gated on
``[osc] enabled``.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from typing import Any

from openfollow.operator_messages import OperatorMessageStore
from openfollow.osc.service import OscService

logger = logging.getLogger(__name__)


class OperatorMessageOscAdapter:
    """Routes ``/message`` traffic into the store."""

    _MESSAGE_ADDR = "/message"
    _CLEAR_ADDR = "/message/clear"

    def __init__(
        self,
        service: OscService,
        store: OperatorMessageStore,
        *,
        get_controlled_marker_ids: Callable[[], set[int]],
        route_by_marker: bool = True,
    ) -> None:
        self._service = service
        self._store = store
        self._get_controlled = get_controlled_marker_ids
        self._route_by_marker = route_by_marker
        self._started = False

    def start(self) -> None:
        """Subscribe the message + clear handlers. Idempotent."""
        if self._started:
            return
        self._service.subscribe(self._MESSAGE_ADDR, self._handle_message)
        self._service.subscribe(self._CLEAR_ADDR, self._handle_clear)
        self._started = True

    def stop(self) -> None:
        """Unsubscribe both handlers. Idempotent. Does not touch the listener."""
        if not self._started:
            return
        self._service.unsubscribe(self._MESSAGE_ADDR)
        self._service.unsubscribe(self._CLEAR_ADDR)
        self._started = False

    def _handle_message(self, address: str, *args: Any) -> None:
        if not args:
            logger.debug("OSC %s: no message arg – ignoring", address)
            return
        message = str(args[0])
        if not message.strip():
            logger.debug("OSC %s: empty message – ignoring", address)
            return
        info = str(args[1]) if len(args) >= 2 else ""

        marker_id = 0
        if len(args) >= 3:
            parsed_id = _coerce_int(args[2])
            # Negative id is invalid; drop the packet.
            if parsed_id is None or parsed_id < 0:
                logger.debug("OSC %s: bad markerId %r – ignoring", address, args[2])
                return
            marker_id = parsed_id

        duration_s = 0.0
        if len(args) >= 4:
            parsed_secs = _coerce_float(args[3])
            if parsed_secs is None:
                logger.debug("OSC %s: bad seconds %r – ignoring", address, args[3])
                return
            duration_s = max(0.0, parsed_secs)

        if not self._accepts(marker_id):
            logger.debug(
                "OSC %s: markerId %d not controlled by this station – dropping",
                address,
                marker_id,
            )
            return

        self._store.add(message, info, marker_id, duration_s)
        logger.debug(
            "OSC → operator message (marker %d, %.1fs): %s",
            marker_id,
            duration_s,
            message,
        )

    def _handle_clear(self, address: str, *args: Any) -> None:
        if not args:
            self._store.clear_all()
            logger.debug("OSC %s: cleared all operator messages", address)
            return
        marker_id = _coerce_int(args[0])
        if marker_id is None or marker_id < 0:
            logger.debug("OSC %s: bad clear id %r – ignoring", address, args[0])
            return
        self._store.clear_marker(marker_id)
        logger.debug("OSC %s: cleared operator messages for marker %d", address, marker_id)

    def _accepts(self, marker_id: int) -> bool:
        """Ingest routing filter (broadcast or controlled-marker match).

        With ``route_by_marker`` off, every message is accepted regardless of
        marker so all stations show it.
        """
        if not self._route_by_marker:
            return True
        if marker_id == 0:
            return True
        return marker_id in self._get_controlled()


def _coerce_int(value: Any) -> int | None:
    """Lenient OSC arg → int, or ``None`` when not coercible.

    Accepts ints, finite floats (``3.0`` → ``3``), and numeric strings
    (``"3"`` / ``"3.0"``). Rejects ``bool`` and non-finite floats.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if math.isfinite(value) else None
    if isinstance(value, str):
        s = value.strip()
        try:
            return int(s)
        except ValueError:
            pass
        try:
            f = float(s)
        except ValueError:
            return None
        return int(f) if math.isfinite(f) else None
    return None


def _coerce_float(value: Any) -> float | None:
    """Lenient OSC arg → finite float, or ``None`` when not coercible."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        f = float(value)
        return f if math.isfinite(f) else None
    if isinstance(value, str):
        try:
            f = float(value.strip())
        except ValueError:
            return None
        return f if math.isfinite(f) else None
    return None
