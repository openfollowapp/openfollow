# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Receives PSN marker data via multicast and maintains per-marker state.

``PsnReceiver`` runs a background thread (``_RobustReceiver``) that parses
incoming PSN packets and ignores locally-controlled marker IDs. A tracker's
speed is the sender's vector while that sender is publishing one; otherwise it
is derived from position deltas.
"""

from __future__ import annotations

import functools
import logging
import socket
import threading
import time
from collections.abc import Callable

import pypsn

from openfollow.net_utils import join_multicast_group_on_iface
from openfollow.psn.marker import Marker
from openfollow.receive_gate import ReceiveGate

logger = logging.getLogger(__name__)


class _RobustReceiver(pypsn.Receiver):  # type: ignore[misc]
    """pypsn.Receiver with proper exception handling and a prompt stop.

    The upstream implementation prints socket timeouts to stdout, runs
    parse_psn_packet/callback outside the try/except, and its stop waits out a
    2 s read. This subclass reads through a :class:`ReceiveGate`, so a stop
    from the main loop returns at once.
    """

    def __init__(
        self,
        callback: Callable[..., None],
        ip_addr: str,
        mcast_port: int,
        iface: str = "",
        own_source: Callable[[], tuple[str, int] | None] = lambda: None,
    ) -> None:
        self._own_source = own_source
        if iface:
            # pypsn joins its group by address; a pinned interface is joined by index.
            threading.Thread.__init__(self)
            self.callback = callback
            self.socket = _pinned_socket(ip_addr, mcast_port, iface)
        else:
            super().__init__(callback=callback, ip_addr=ip_addr, mcast_port=mcast_port)
        self._gate: ReceiveGate | None = None

    # Only a callback that hangs can hold this up: the gate ends the wait itself.
    _JOIN_TIMEOUT_S = 1.0

    def start(self) -> None:
        """Start the receive thread; on failure release the gate and the socket, then raise."""
        if self.socket is None:
            return  # pypsn opened none: there is nothing to read
        try:
            # Read only once the gate reports data, so a read never blocks.
            self.socket.setblocking(False)
            self._gate = ReceiveGate(self.socket)
            super().start()
        except BaseException:
            gate, self._gate = self._gate, None
            if gate is not None:
                gate.close()
            self.socket.close()
            raise

    def stop(self) -> None:
        """End the loop and close the socket, releasing its membership. Never waits on a read."""
        if self._gate is not None:
            self._gate.stop()
            self.join(timeout=self._JOIN_TIMEOUT_S)
            if self.is_alive():
                logger.warning("PSN receiver thread did not stop within %.0fs", self._JOIN_TIMEOUT_S)
        if self.socket is not None:
            try:
                self.socket.close()
            except OSError:
                pass

    def run(self) -> None:
        gate = self._gate
        if gate is None:
            return
        try:
            self._receive(gate)
        finally:
            gate.close()

    def _receive(self, gate: ReceiveGate) -> None:
        while gate.wait():
            try:
                # Max UDP payload: a PSN data packet exceeds 1500 B at ~15
                # trackers, and a short read truncates the tail → struct.error →
                # the whole frame is dropped. Size to the largest datagram.
                data, addr = self.socket.recvfrom(65535)
            except BlockingIOError:
                continue  # readiness without a datagram
            except OSError:
                logger.debug("PSN recv socket error", exc_info=True)
                continue
            except Exception:
                logger.debug("PSN recv unexpected error", exc_info=True)
                continue
            # This station's own output, looped back: dropped before it is decoded.
            if addr[:2] == self._own_source():
                continue

            try:
                psn_data = pypsn.parse_psn_packet(data)
                # The sender's address, so speed handling can tell two stations
                # publishing the same tracker id apart.
                self.callback(psn_data, addr[0])
            except Exception:
                logger.debug("PSN parse/callback error", exc_info=True)


DEFAULT_PORT = 56565
# The group pypsn's own receiver joins.
_PSN_GROUP = "236.10.10.10"


def _pinned_socket(ip_addr: str, port: int, iface: str) -> socket.socket:
    """pypsn's POSIX receive socket, with the group joined on *iface* by index."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((_PSN_GROUP, port))
        join_multicast_group_on_iface(sock, _PSN_GROUP, ip_addr, iface)
    except OSError:
        sock.close()
        raise
    return sock


# Evict markers whose last packet aged well past the 2 s online window so an
# enumerated / abandoned tracker_id (1–65535, untrusted wire data) can't keep a
# Marker (+ its lock) and its per-id bookkeeping entries alive for the whole
# process lifetime. A returning marker is re-created on its next packet.
_MARKER_TTL_S = 60.0
_EVICT_SWEEP_INTERVAL_S = 5.0  # throttle the sweep so a packet flood can't make it hot
_INVALID_STATUS = 0.0  # what a tracker's validity reads as when the sender publishes none


class PsnReceiver:
    """Receives PSN marker data via multicast and maintains marker states.

    Marker IDs listed in *ignore_ids* are not updated from the network; use
    this to exclude locally-controlled markers so received loopback packets
    do not overwrite their positions.

    *source_ip* binds the multicast socket to a specific network interface.
    Leave empty to listen on all interfaces (default behaviour). *source_iface*
    names that interface, so the membership is taken on it by index.
    While not *enabled* (the PSN input feature is off) it never binds.
    """

    def __init__(
        self,
        port: int = DEFAULT_PORT,
        ignore_ids: list[int] | None = None,
        source_ip: str = "",
        source_iface: str = "",
        *,
        enabled: bool = True,
        own_source: Callable[[], tuple[str, int] | None] = lambda: None,
    ) -> None:
        self._port = port
        # Where this station's own PSN output comes from, read per packet.
        self._own_source = own_source
        self._enabled = enabled
        # Strip whitespace; empty/whitespace-only → listen on all interfaces.
        self._source_ip = source_ip.strip()
        self._source_iface = source_iface.strip()
        self._lock = threading.Lock()
        self._ignore_ids: set[int] = set(ignore_ids or [])
        self._markers: dict[int, Marker] = {}
        self._last_seen: dict[int, float] = {}
        self._last_pos: dict[int, tuple[float, float, float]] = {}
        # Per tracker, the address of the sender last seen publishing a
        # non-zero speed for it. Only that sender's vectors are stored verbatim.
        self._wire_speed_sender: dict[int, str] = {}
        self._last_evict_sweep: float = 0.0
        self._receiver: pypsn.Receiver | None = None
        # The running receive thread's token; a packet carrying any other is dropped.
        self._generation: object | None = None

    @property
    def enabled(self) -> bool:
        """Whether the receiver may listen at all."""
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        """Allow or forbid listening; forbidding stops it and forgets every received marker."""
        self._enabled = enabled
        if enabled:
            return
        self.stop()
        with self._lock:
            self._markers.clear()
            self._last_seen.clear()
            self._last_pos.clear()
            self._wire_speed_sender.clear()

    def start(self) -> None:
        """Begin listening for PSN packets on the multicast group; a no-op while not enabled."""
        if not self._enabled:
            return
        generation = object()
        try:
            receiver = _RobustReceiver(
                callback=functools.partial(self._on_packet, generation=generation),
                ip_addr=self._source_ip or "0.0.0.0",
                mcast_port=self._port,
                iface=self._source_iface,
                own_source=self._own_source,
            )
        except OSError as exc:
            logger.error(
                "PSN receiver socket failed: %s. PSN input disabled – check your network interface IP.",
                exc,
            )
            return
        receiver.daemon = True
        try:
            receiver.start()
        except (OSError, RuntimeError) as exc:
            # A full fd table or thread table; the receiver has released its socket.
            logger.error("PSN receiver could not start: %s. PSN input disabled.", exc)
            return
        with self._lock:
            self._generation = generation
        self._receiver = receiver

    def stop(self) -> None:
        """Stop listening and release the receiver thread."""
        with self._lock:
            self._generation = None
        if self._receiver is not None:
            self._receiver.stop()
            self._receiver = None

    def bound_source_ip(self) -> str | None:
        """Address this receiver is joined on, or ``None`` when stopped."""
        if self._receiver is None:
            return None
        return self._source_ip

    def rebind(self, source_ip: str, *, source_iface: str | None = None) -> None:
        """Recreate socket bound to new interface; raises on failure. ``source_iface=None`` keeps the name.

        While not enabled it only records the interface.
        """
        self.stop()
        # Strip whitespace same as __init__.
        self._source_ip = source_ip.strip()
        if source_iface is not None:
            self._source_iface = source_iface.strip()
        if not self._enabled:
            return
        self.start()
        if self._receiver is None:
            raise OSError(
                f"PSN receiver failed to bind to source_ip={self._source_ip!r}",
            )

    def get_marker(self, marker_id: int) -> Marker | None:
        """Return a received marker by ID, or ``None`` once it has been silent past the TTL."""
        with self._lock:
            # Swept on read too: this station's own packets are never decoded,
            # so with no peer left sending, no packet would ever sweep.
            self._sweep_if_due_locked(time.monotonic())
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

    def _on_packet(self, data: object, sender: str = "", *, generation: object | None = None) -> None:
        """Callback invoked by pypsn on each received packet.

        *sender* is the source address of the datagram; it scopes the wire-speed
        decision to one station so a second station publishing the same tracker
        id cannot claim the first one's trust. *generation* is the receive
        thread's token: once that thread is stopped its packets are dropped, so
        nothing it read lands after :meth:`stop` returns.
        """
        try:
            if not isinstance(data, pypsn.PsnDataPacket):
                return
            # Acquire lock once for the entire batch of marker updates.
            with self._lock:
                if generation is not None and generation is not self._generation:
                    return
                # ``data.trackers`` and ``t.tracker_id`` are pypsn's
                # wire-protocol-derived names; our domain layer
                # translates them into Marker instances at this seam.
                # One read for the whole packet: all trackers in a packet arrive
                # together, so they share an arrival time. Local and monotonic -
                # not comparable to the sender-timed stamp the marker carries.
                now = time.monotonic()
                self._sweep_if_due_locked(now)
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
                        self._markers[t.tracker_id] = Marker(t.tracker_id, f"Marker {t.tracker_id}", remote=True)
                    tid = t.tracker_id
                    new_pos = (t.pos.x, t.pos.y, t.pos.z)
                    wire = t.speed
                    if wire is not None and (wire.x != 0.0 or wire.y != 0.0 or wire.z != 0.0):
                        self._wire_speed_sender[tid] = sender
                    speed: tuple[float, float, float] | None = None
                    if wire is not None and self._wire_speed_sender.get(tid) == sender:
                        # This sender publishes speed for this tracker, so its
                        # vector is taken verbatim, exact zeros included: a marker
                        # it holds still reports zero. Both conditions are per
                        # packet - a frame that omits the speed chunk, or one from
                        # a second station on the same tracker id, derives instead
                        # of freezing on a vector nobody is refreshing.
                        speed = (wire.x, wire.y, wire.z)
                    else:
                        # No speed to trust: derive it from the position delta,
                        # only when moving, so the last known speed stays visible
                        # between bursts.
                        prev_pos = self._last_pos.get(tid)
                        prev_t = self._last_seen.get(tid)
                        if prev_pos is not None and prev_t is not None:
                            dt = now - prev_t
                            if 0.001 < dt < 1.0:
                                dx = new_pos[0] - prev_pos[0]
                                dy = new_pos[1] - prev_pos[1]
                                dz = new_pos[2] - prev_pos[2]
                                if dx != 0.0 or dy != 0.0 or dz != 0.0:
                                    speed = (dx / dt, dy / dt, dz / dt)
                    # The sender's own timestamp and status, normalised but never
                    # fabricated; absent fields default rather than abort the frame.
                    self._markers[tid].apply_remote(
                        new_pos,
                        speed,
                        timestamp=getattr(t, "timestamp", 0),
                        status=getattr(t, "status", _INVALID_STATUS),
                    )
                    self._last_pos[tid] = new_pos
                    self._last_seen[tid] = now
        except Exception:
            logger.debug("Malformed PSN packet ignored", exc_info=True)

    def _sweep_if_due_locked(self, now: float) -> None:
        """Evict stale markers at most every ``_EVICT_SWEEP_INTERVAL_S``. Caller holds ``_lock``."""
        if now - self._last_evict_sweep >= _EVICT_SWEEP_INTERVAL_S:
            self._last_evict_sweep = now
            self._evict_stale_markers_locked(now)

    def _evict_stale_markers_locked(self, now: float) -> None:
        """Drop markers silent longer than ``_MARKER_TTL_S``. Caller holds ``_lock``."""
        stale = [tid for tid, seen in self._last_seen.items() if (now - seen) > _MARKER_TTL_S]
        for tid in stale:
            self._markers.pop(tid, None)
            self._last_seen.pop(tid, None)
            self._last_pos.pop(tid, None)
            self._wire_speed_sender.pop(tid, None)
