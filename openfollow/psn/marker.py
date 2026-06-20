# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Mutable state wrapper for a single PSN marker."""

from __future__ import annotations

import threading

import pypsn

Vec3 = tuple[float, float, float]
_ZERO: Vec3 = (0.0, 0.0, 0.0)


class Marker:
    """Mutable state wrapper for a single PSN marker.

    Stores position, speed, orientation, and other PSN marker fields.
    Provides conversion methods to ``pypsn`` data types for network transmission.

    Thread-safety: Position/speed/orientation reads and writes are protected
    by an internal lock to prevent torn reads when background PSN threads
    read state while the main thread updates it.
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
        "_lock",
    )

    def __init__(self, marker_id: int, name: str) -> None:
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
        self._status: float = 0.0
        self._timestamp: int = 0
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

    def set_pos(self, x: float, y: float, z: float) -> None:
        """Set the marker position in PSN coordinates."""
        with self._lock:
            self._pos = (x, y, z)

    def set_name(self, name: str) -> None:
        """Update the marker name (used by live catalog rename)."""
        with self._lock:
            self.name = name

    def set_speed(self, x: float, y: float, z: float) -> None:
        """Set the marker speed vector."""
        with self._lock:
            self._speed = (x, y, z)

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
