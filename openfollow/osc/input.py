# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""OSC marker-position input: subscriptions & sparse update queue (last-write-wins)."""

from __future__ import annotations

import functools
import logging
import math
import threading
from typing import Any

from openfollow.osc.service import OscService

logger = logging.getLogger(__name__)


# String axis keys so the queue JSON round-trips.
_AXES: tuple[str, ...] = ("x", "y", "z")


class OscMarkerAdapter:
    """Listens for marker-position OSC messages and queues them.

    Marker id is parsed from the address (``/marker/2`` → 2). The pending
    queue is sparse: ``flush_updates()`` returns
    ``dict[int, dict[str, float]]`` whose inner dicts carry only the axes
    written in the current window; the consumer fills unset axes from the
    marker's current position before calling ``set_pos``.

    :meth:`start` subscribes and starts the listener; :meth:`stop`
    reverses it. The service may be shared with other callers; only the
    listener is owned exclusively by marker input.
    """

    _TRIPLE_PATTERN = "/marker/*"
    _AXIS_PATTERN_FMT = "/marker/*/{axis}"

    # Alias kept for external callers that reference ``PATTERN``.
    PATTERN = _TRIPLE_PATTERN

    def __init__(
        self,
        service: OscService,
        *,
        port: int = 8765,
        allowed_sender_ips: list[str] | None = None,
        multicast_group: str = "",
    ) -> None:
        self._service = service
        self._port = port
        self._allowed_ips = list(allowed_sender_ips or [])
        # Optional multicast group the listener joins.
        self._multicast_group = multicast_group
        self._lock = threading.Lock()
        self._pending: dict[int, dict[str, float]] = {}
        self._started = False

    def _all_patterns(self) -> tuple[str, ...]:
        return (
            self._TRIPLE_PATTERN,
            *(self._AXIS_PATTERN_FMT.format(axis=a) for a in _AXES),
        )

    def start(self) -> None:
        """Subscribe and start the inbound listener.

        A second call without an intervening :meth:`stop` is a no-op.
        """
        if self._started:
            return
        self._service.subscribe(self._TRIPLE_PATTERN, self._handle_triple)
        for axis in _AXES:
            self._service.subscribe(
                self._AXIS_PATTERN_FMT.format(axis=axis),
                functools.partial(self._handle_axis, axis),
            )
        try:
            self._service.start_listener(
                self._port,
                allowed_ips=self._allowed_ips,
                multicast_group=self._multicast_group,
            )
        except OSError:
            # Bind failed; unsubscribe and allow retry on restart.
            for pattern in self._all_patterns():
                self._service.unsubscribe(pattern)
            raise
        self._started = True

    def stop(self) -> None:
        """Unsubscribe and stop the inbound listener. Idempotent."""
        if not self._started:
            return
        for pattern in self._all_patterns():
            self._service.unsubscribe(pattern)
        self._service.stop_listener()
        self._started = False

    def flush_updates(self) -> dict[int, dict[str, float]]:
        """Return all pending position updates and clear the queue.

        Sparse: each marker id maps to a dict carrying only the axes
        written in this window (a subset of ``{'x', 'y', 'z'}``); the
        consumer merges missing axes against the marker's current
        position before applying.
        """
        with self._lock:
            updates = {tid: dict(axes) for tid, axes in self._pending.items()}
            self._pending.clear()
        return updates

    @property
    def port(self) -> int:
        """Configured listener port."""
        return self._port

    def _parse_marker_id(self, address: str, expected_parts: int) -> int | None:
        """Pull the integer marker id out of the address path.

        Returns ``None`` if the address shape doesn't match the expected
        number of segments (``2`` for ``/marker/N``, ``3`` for
        ``/marker/N/<axis>``) or the id segment isn't an integer.
        """
        parts = address.strip("/").split("/")
        if len(parts) != expected_parts:
            return None
        try:
            return int(parts[1])
        except ValueError:
            return None

    def _handle_triple(self, address: str, *args: Any) -> None:
        marker_id = self._parse_marker_id(address, expected_parts=2)
        if marker_id is None:
            return
        if len(args) < 3:
            logger.debug(
                "OSC %s: expected 3 float args, got %d – ignoring",
                address,
                len(args),
            )
            return
        try:
            x, y, z = float(args[0]), float(args[1]), float(args[2])
        except (TypeError, ValueError) as exc:
            logger.debug("OSC %s: bad args %s – %s", address, args, exc)
            return
        if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)):
            logger.debug("OSC %s: non-finite args %s – ignoring", address, args)
            return
        with self._lock:
            # Triple replaces any per-axis writes in the same window.
            self._pending[marker_id] = {"x": x, "y": y, "z": z}
        logger.debug("OSC → marker %d  (%.3f, %.3f, %.3f)", marker_id, x, y, z)

    def _handle_axis(self, axis: str, address: str, *args: Any) -> None:
        # Confirm the address's trailing token matches the bound axis.
        parts = address.strip("/").split("/")
        if len(parts) != 3 or parts[2] != axis or axis not in _AXES:
            return
        try:
            marker_id = int(parts[1])
        except ValueError:
            return
        if len(args) != 1:
            logger.debug(
                "OSC %s: expected 1 numeric arg, got %d – ignoring",
                address,
                len(args),
            )
            return
        try:
            value = float(args[0])
        except (TypeError, ValueError) as exc:
            logger.debug("OSC %s: bad arg %r – %s", address, args[0], exc)
            return
        if not math.isfinite(value):
            logger.debug("OSC %s: non-finite arg %r – ignoring", address, args[0])
            return
        with self._lock:
            self._pending.setdefault(marker_id, {})[axis] = value
        logger.debug("OSC → marker %d  %s=%.3f", marker_id, axis, value)
