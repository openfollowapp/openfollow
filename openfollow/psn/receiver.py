# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Receives PSN marker data via multicast and maintains per-marker state.

``PsnReceiver`` runs a background thread (``_RobustReceiver``) that parses
incoming PSN packets, ignoring locally-controlled marker IDs and deriving
speed from position deltas when the protocol speed is zero.
"""

from __future__ import annotations

import logging
import threading
import time

import pypsn

from openfollow.psn.marker import Marker

logger = logging.getLogger(__name__)


class _RobustReceiver(pypsn.Receiver):  # type: ignore[misc]
    """pypsn.Receiver with proper exception handling.

    The upstream implementation prints socket timeouts to stdout and runs
    parse_psn_packet/callback outside the try/except, so a bad packet or
    callback error crashes the thread.  This subclass fixes both issues.
    """

    def run(self) -> None:
        if self.socket is None:
            return
        while self.running:
            try:
                # Max UDP payload: a PSN data packet exceeds 1500 B at ~15
                # trackers, and a short read truncates the tail → struct.error →
                # the whole frame is dropped. Size to the largest datagram.
                data, _ = self.socket.recvfrom(65535)
            except TimeoutError:
                continue  # no data within timeout – normal, keep looping
            except OSError:
                if not self.running:
                    break  # socket was closed by stop()
                logger.debug("PSN recv socket error", exc_info=True)
                continue
            except Exception:
                logger.debug("PSN recv unexpected error", exc_info=True)
                continue

            try:
                psn_data = pypsn.parse_psn_packet(data)
                self.callback(psn_data)
            except Exception:
                logger.debug("PSN parse/callback error", exc_info=True)


DEFAULT_PORT = 56565

# Evict markers whose last packet aged well past the 2 s online window so an
# enumerated / abandoned tracker_id (1–65535, untrusted wire data) can't keep a
# Marker (+ its lock) and the three per-id dict entries alive for the whole
# process lifetime. A returning marker is re-created on its next packet.
_MARKER_TTL_S = 60.0
_EVICT_SWEEP_INTERVAL_S = 5.0  # throttle the sweep so a packet flood can't make it hot


class PsnReceiver:
    """Receives PSN marker data via multicast and maintains marker states.

    Marker IDs listed in *ignore_ids* are not updated from the network; use
    this to exclude locally-controlled markers so received loopback packets
    do not overwrite their positions.

    *source_ip* binds the multicast socket to a specific network interface.
    Leave empty to listen on all interfaces (default behaviour).
    """

    def __init__(
        self,
        port: int = DEFAULT_PORT,
        ignore_ids: list[int] | None = None,
        source_ip: str = "",
    ) -> None:
        self._port = port
        # Strip whitespace; empty/whitespace-only → listen on all interfaces.
        self._source_ip = source_ip.strip()
        self._lock = threading.Lock()
        self._ignore_ids: set[int] = set(ignore_ids or [])
        self._markers: dict[int, Marker] = {}
        self._last_seen: dict[int, float] = {}
        self._last_pos: dict[int, tuple[float, float, float]] = {}
        self._last_evict_sweep: float = 0.0
        self._receiver: pypsn.Receiver | None = None

    def start(self) -> None:
        """Begin listening for PSN packets on the multicast group."""
        try:
            self._receiver = _RobustReceiver(
                callback=self._on_packet,
                ip_addr=self._source_ip or "0.0.0.0",
                mcast_port=self._port,
            )
        except OSError as exc:
            logger.error(
                "PSN receiver socket failed: %s. PSN input disabled – check your network interface IP.",
                exc,
            )
            return
        self._receiver.daemon = True
        self._receiver.start()

    def stop(self) -> None:
        """Stop listening and release the receiver thread."""
        if self._receiver is not None:
            self._receiver.stop()
            self._receiver = None

    def rebind(self, source_ip: str) -> None:
        """Recreate socket bound to new interface; raises on failure."""
        self.stop()
        # Strip whitespace same as __init__.
        self._source_ip = source_ip.strip()
        self.start()
        if self._receiver is None:
            raise OSError(
                f"PSN receiver failed to bind to source_ip={self._source_ip!r}",
            )

    def get_marker(self, marker_id: int) -> Marker | None:
        """Return a received marker by ID, or ``None``."""
        with self._lock:
            return self._markers.get(marker_id)

    def is_marker_online(self, marker_id: int, timeout: float = 2.0) -> bool:
        """Return True if a packet for this marker was received within *timeout* seconds."""
        with self._lock:
            last = self._last_seen.get(marker_id)
        return last is not None and (time.monotonic() - last) < timeout

    def set_ignore_ids(self, ignore_ids: set[int]) -> None:
        """Update the set of marker IDs that will not be updated from the network."""
        with self._lock:
            self._ignore_ids = set(ignore_ids)

    def _on_packet(self, data: object) -> None:
        """Callback invoked by pypsn on each received packet."""
        try:
            if not isinstance(data, pypsn.PsnDataPacket):
                return
            # Acquire lock once for the entire batch of marker updates.
            with self._lock:
                # ``data.trackers`` and ``t.tracker_id`` are pypsn's
                # wire-protocol-derived names; our domain layer
                # translates them into Marker instances at this seam.
                # One ``time.monotonic()`` per packet: all trackers in a packet
                # arrive together, so a single read is cheaper and makes timing consistent.
                now = time.monotonic()
                if now - self._last_evict_sweep >= _EVICT_SWEEP_INTERVAL_S:
                    self._last_evict_sweep = now
                    self._evict_stale_markers_locked(now)
                for t in data.trackers:
                    # tracker_id 0 is the reserved "ignored" id on the PSN wire;
                    # Marker(0) raises, which would abort the loop and drop every
                    # later tracker in this frame. The isinstance guard keeps a
                    # non-int id (latent if pypsn's parser is ever swapped) from
                    # raising on the ``< 1`` compare and dropping the rest of the
                    # frame. Skip both before constructing.
                    if (
                        t.pos is None
                        or not isinstance(t.tracker_id, int)
                        or t.tracker_id < 1
                        or t.tracker_id in self._ignore_ids
                    ):
                        continue
                    if t.tracker_id not in self._markers:
                        self._markers[t.tracker_id] = Marker(t.tracker_id, f"Marker {t.tracker_id}")
                    tid = t.tracker_id
                    new_pos = (t.pos.x, t.pos.y, t.pos.z)
                    self._markers[tid].set_pos(*new_pos)
                    # Use protocol speed only if non-zero; many servers (including
                    # OpenFollow itself) always send speed=(0,0,0) even when
                    # the marker is moving, so fall back to position derivation.
                    proto_speed_nonzero = t.speed is not None and (
                        t.speed.x != 0.0 or t.speed.y != 0.0 or t.speed.z != 0.0
                    )
                    if proto_speed_nonzero:
                        # Store as scalar magnitude in the x component so
                        # services.py can read it without directional noise.
                        mag = (t.speed.x**2 + t.speed.y**2 + t.speed.z**2) ** 0.5
                        self._markers[tid].set_speed(mag, 0.0, 0.0)
                    else:
                        # Derive speed from position delta; only update when
                        # actually moving so the last known speed stays visible.
                        prev_pos = self._last_pos.get(tid)
                        prev_t = self._last_seen.get(tid)
                        if prev_pos is not None and prev_t is not None:
                            dt = now - prev_t
                            if 0.001 < dt < 1.0:
                                dx = new_pos[0] - prev_pos[0]
                                dy = new_pos[1] - prev_pos[1]
                                dz = new_pos[2] - prev_pos[2]
                                if dx != 0.0 or dy != 0.0 or dz != 0.0:
                                    self._markers[tid].set_speed(dx / dt, dy / dt, dz / dt)
                    self._last_pos[tid] = new_pos
                    self._last_seen[tid] = now
        except Exception:
            logger.debug("Malformed PSN packet ignored", exc_info=True)

    def _evict_stale_markers_locked(self, now: float) -> None:
        """Drop markers silent longer than ``_MARKER_TTL_S``. Caller holds ``_lock``."""
        stale = [tid for tid, seen in self._last_seen.items() if (now - seen) > _MARKER_TTL_S]
        for tid in stale:
            self._markers.pop(tid, None)
            self._last_seen.pop(tid, None)
            self._last_pos.pop(tid, None)
