# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Mutable state wrapper for a single PSN marker."""

from __future__ import annotations

import threading
from collections.abc import Callable

import pypsn

from openfollow.psn.clock import psn_timestamp_usec

Vec3 = tuple[float, float, float]
_ZERO: Vec3 = (0.0, 0.0, 0.0)
# PSN_DATA_TRACKER_STATUS carries the tracker's validity as a float. A marker we
# are actively driving is fully valid; 0.0 means "no data written yet".
_VALID = 1.0
_INVALID = 0.0


def _clamped_status(status: float) -> float:
    """Coerce a status into the spec's 0.0-1.0 range.

    A non-numeric or NaN value falls back to the declared default rather than
    raising: callers derive this from tracking confidence on the frame path and
    from untrusted wire data on the receive path, where an exception would take
    the frame (or the rest of the packet) down.
    """
    try:
        value = float(status)
    except (TypeError, ValueError):
        return _INVALID
    if value != value:  # NaN would reach struct.pack and ship a NaN status.
        return _INVALID
    return min(1.0, max(0.0, value))


def _clamped_timestamp(timestamp: int) -> int:
    """Coerce a tracker timestamp into a non-negative microsecond count."""
    try:
        value = int(timestamp)
    except (TypeError, ValueError):
        return 0
    return max(0, value)


class Marker:
    """Mutable state wrapper for a single PSN marker.

    Stores position, speed, orientation, and other PSN marker fields.
    Provides conversion methods to ``pypsn`` data types for network transmission.

    Thread-safety: Position/speed/orientation reads and writes are protected
    by an internal lock to prevent torn reads when background PSN threads
    read state while the main thread updates it.

    Every data write stamps ``timestamp`` and marks the marker valid, so a
    receiver can tell a marker that is still being updated from a stale one.

    A marker built from received data (``remote=True``, written via
    ``apply_remote``) instead holds the values its sender published. Those count
    from *that sender's* start, so they must never be compared against
    ``psn_timestamp_usec()`` or re-broadcast as ours; ``is_remote`` is how a
    holder tells the two epochs apart. Local freshness for a received marker is
    ``PsnReceiver.is_marker_online``, which times arrival instead.
    """

    __slots__ = (
        "marker_id",
        "name",
        "_pos",
        "_speed",
        "_ori",
        "_accel",
        "_trgtpos",
        "_status",
        "_timestamp",
        "_remote",
        "_clock",
        "_lock",
    )

    def __init__(
        self,
        marker_id: int,
        name: str,
        *,
        remote: bool = False,
        clock: Callable[[], int] = psn_timestamp_usec,
    ) -> None:
        # Marker id 0 reserved on PSN wire; validate early.
        if not isinstance(marker_id, int) or isinstance(marker_id, bool):
            raise ValueError("marker_id must be int")
        if marker_id < 1:
            raise ValueError("marker_id must be >= 1")
        self.marker_id: int = marker_id
        self.name: str = name
        self._pos: Vec3 = _ZERO
        self._speed: Vec3 = _ZERO
        self._ori: Vec3 = _ZERO
        self._accel: Vec3 = _ZERO
        self._trgtpos: Vec3 = _ZERO
        self._status: float = _INVALID
        self._timestamp: int = 0
        self._remote: bool = bool(remote)
        self._clock = clock
        self._lock = threading.Lock()

    @property
    def pos(self) -> Vec3:
        with self._lock:
            return self._pos

    @property
    def speed(self) -> Vec3:
        with self._lock:
            return self._speed

    @property
    def ori(self) -> Vec3:
        with self._lock:
            return self._ori

    @property
    def accel(self) -> Vec3:
        with self._lock:
            return self._accel

    @property
    def trgtpos(self) -> Vec3:
        with self._lock:
            return self._trgtpos

    @property
    def status(self) -> float:
        with self._lock:
            return self._status

    @property
    def timestamp(self) -> int:
        with self._lock:
            return self._timestamp

    @property
    def is_remote(self) -> bool:
        """True when this marker mirrors a sender on the network."""
        return self._remote

    def set_pos(self, x: float, y: float, z: float) -> None:
        """Set the marker position in PSN coordinates."""
        with self._lock:
            self._pos = (x, y, z)
            self._stamp_locked()

    def set_name(self, name: str) -> None:
        """Update the marker name (used by live catalog rename)."""
        with self._lock:
            self.name = name

    def set_speed(self, x: float, y: float, z: float) -> None:
        """Set the marker speed vector."""
        with self._lock:
            self._speed = (x, y, z)
            self._stamp_locked()

    def set_status(self, status: float) -> None:
        """Set the tracker validity, clamped to the 0.0-1.0 range."""
        value = _clamped_status(status)
        with self._lock:
            self._status = value

    def apply_remote(
        self,
        pos: Vec3,
        speed: Vec3 | None = None,
        *,
        timestamp: int,
        status: float,
    ) -> None:
        """Write one received tracker: the sender's values, never a local stamp.

        *speed* of ``None`` keeps the previous vector, so a sender that stops
        publishing speed does not zero the last known one. The whole tracker
        lands under a single lock acquisition.
        """
        remote_status = _clamped_status(status)
        remote_timestamp = _clamped_timestamp(timestamp)
        with self._lock:
            self._pos = pos
            if speed is not None:
                self._speed = speed
            self._status = remote_status
            self._timestamp = remote_timestamp

    def _stamp_locked(self) -> None:
        """Record the time of this data write. Caller holds ``_lock``.

        ``set_name`` deliberately does not stamp - a rename is metadata, not
        tracker data, and must not make a stale marker look fresh. The first
        data write promotes an untouched marker to valid; an explicit
        ``set_status`` afterwards stands, so a caller deriving validity from
        tracking confidence is not overwritten on the next write.
        """
        self._timestamp = self._clock()
        if self._status == _INVALID:
            self._status = _VALID

    def to_psn_marker(self) -> pypsn.PsnTracker:
        """Convert to pypsn.PsnTracker with all fields under lock."""
        with self._lock:
            # pypsn uses tracker_id (wire protocol); we translate at boundary.
            return pypsn.PsnTracker(
                tracker_id=self.marker_id,
                pos=pypsn.PsnVector3(*self._pos),
                speed=pypsn.PsnVector3(*self._speed),
                ori=pypsn.PsnVector3(*self._ori),
                accel=pypsn.PsnVector3(*self._accel),
                trgtpos=pypsn.PsnVector3(*self._trgtpos),
                status=self._status,
                timestamp=self._timestamp,
            )

    def to_psn_marker_info(self) -> pypsn.PsnTrackerInfo:
        """Convert to pypsn.PsnTrackerInfo (wire-protocol names)."""
        return pypsn.PsnTrackerInfo(
            tracker_id=self.marker_id,
            tracker_name=self.name,
        )
