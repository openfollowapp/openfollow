# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""RTTrPM (Real Time Tracking Protocol – Motion) output server.

Transmits marker position data via UDP unicast using the RTTrP v2 binary
format (Big Endian).  OpenFollow is a send-only participant: it never
receives RTTrPM packets.

Packet structure (Big Endian throughout):

  Data packet (``fps`` Hz, default 60):
    Header | [Per marker: Trackable(name) | CentroidPosition(x, y, z)]

Header (18 bytes) – struct "!HHHIBHIB":
  uint16  intSig     = 0x4154  (Big Endian integer signifier)
  uint16  floatSig   = 0x4334  (Big Endian float signifier)
  uint16  version    = 0x0002
  uint32  pktId      (sequence counter, wraps at 2^32)
  uint8   pktFormat  = 0x00    (Raw / uncompressed)
  uint16  size       (total packet size in bytes, header included)
  uint32  context    (user-definable, configurable)
  uint8   numModules (count of top-level Trackable modules in this packet)

Trackable module (type 0x01) per marker:
  uint8   pkType   = 0x01
  uint16  size     (total module size including pkType and size field)
  uint8   nameLen  (byte length of the UTF-8 name)
  bytes   name     (nameLen bytes, UTF-8)
  uint8   numMods  = 1
  [child modules follow immediately]

Centroid Position module (type 0x02) – struct "!BHHddd" = 29 bytes:
  uint8   pkType   = 0x02
  uint16  size     = 29
  uint16  latency  = 0  (milliseconds; unknown → 0)
  double  x        (metres, IEEE 754 double precision)
  double  y        (metres, IEEE 754 double precision)
  double  z        (metres, IEEE 754 double precision)

Positions are taken directly from Marker.pos (metres) with no unit
conversion – RTTrPM uses doubles where OTP uses integer micrometres.

Example sizes:
  One marker named "T"  (1-byte name): 18 + 35 = 53 bytes
  One marker named "T0" (2-byte name): 18 + 36 = 54 bytes
"""

from __future__ import annotations

import errno
import logging
import socket
import struct
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openfollow.psn.marker import Marker

logger = logging.getLogger(__name__)

# Transient network errors that warrant socket rebuild.
_TRANSIENT_SEND_ERRNOS: frozenset[int] = frozenset(
    {
        errno.EADDRNOTAVAIL,
        errno.ENETUNREACH,
        errno.ENETDOWN,
        errno.EHOSTDOWN,
        errno.EHOSTUNREACH,
    }
)
_SOCKET_REBUILD_MIN_INTERVAL_SECONDS = 1.0

# ---------------------------------------------------------------------------
# RTTrP protocol constants
# ---------------------------------------------------------------------------

_INT_SIG = 0x4154  # Big Endian integer signifier
_FLOAT_SIG = 0x4334  # Big Endian float signifier
_VERSION = 0x0002  # Protocol version 2
_PKT_FORMAT_RAW = 0x00  # Uncompressed / Raw packet format

_PKT_TYPE_TRACKABLE = 0x01  # Trackable module (no timestamp)
_PKT_TYPE_CENTROID = 0x02  # Centroid Position module

# Centroid module is always exactly 29 bytes:
#   B(1) + H(2) + H(2) + d(8) + d(8) + d(8) = 29
_CENTROID_SIZE = 29

# Header is always exactly 18 bytes:
#   H(2) + H(2) + H(2) + I(4) + B(1) + H(2) + I(4) + B(1) = 18
_HEADER_SIZE = 18

# Trackable names limited by uint8 name-length field.
_MAX_TRACKABLE_NAME_BYTES = 255

# Packet caps: numModules is a uint8 and size is a uint16 – exceeding either
# makes struct.pack raise. Also bound by the max UDP/IPv4 payload.
_MAX_MODULES = 255
_MAX_PACKET_BYTES = 65507


DEFAULT_PORT = 36700


# ---------------------------------------------------------------------------
# Encoding helpers (pure functions – no side effects, safe to unit-test)
# ---------------------------------------------------------------------------


def encode_rttrpm_centroid_module(
    x: float,
    y: float,
    z: float,
    latency: int = 0,
) -> bytes:
    """Encode a Centroid Position module (type 0x02, 29 bytes).

    Args:
        x: X coordinate in metres.
        y: Y coordinate in metres.
        z: Z coordinate in metres.
        latency: Data latency in milliseconds (0 when unknown).

    Returns:
        29-byte Big Endian binary module.
    """
    return struct.pack("!BHHddd", _PKT_TYPE_CENTROID, _CENTROID_SIZE, latency, x, y, z)


def _encode_trackable_name(name: str) -> bytes:
    """Encode name as UTF-8, truncating on rune boundary to stay under 255 bytes."""
    raw = name.encode("utf-8")
    if len(raw) <= _MAX_TRACKABLE_NAME_BYTES:
        return raw
    cut = _MAX_TRACKABLE_NAME_BYTES
    while cut > 0 and (raw[cut] & 0xC0) == 0x80:
        cut -= 1
    return raw[:cut]


def encode_rttrpm_trackable_module(name: str, centroid: bytes) -> bytes:
    """Encode a Trackable module (type 0x01) wrapping a single centroid.

    Args:
        name: Marker name encoded as UTF-8. Truncated on a rune
            boundary if it exceeds 255 bytes (the uint8 ``nameLen``
            field's cap).
        centroid: Pre-encoded Centroid Position module bytes.

    Returns:
        Variable-length Big Endian binary module.
    """
    name_bytes = _encode_trackable_name(name)
    # body = nameLen(1) + name(N) + numMods(1) + centroid(29)
    body = struct.pack("!B", len(name_bytes)) + name_bytes + struct.pack("!B", 1) + centroid
    # total = pkType(1) + size_field(2) + body
    total = 1 + 2 + len(body)
    return struct.pack("!BH", _PKT_TYPE_TRACKABLE, total) + body


def encode_rttrpm_packet(
    pkt_id: int,
    markers: list[Marker],
    context: int = 0,
) -> bytes:
    """Encode a complete RTTrPM UDP payload.

    Args:
        pkt_id: Sequence number (masked to 32 bits).
        markers: Marker objects whose ``.pos`` and ``.name`` are read.
        context: User-definable 32-bit context field (default 0).

    Returns:
        Full packet bytes ready to send via UDP.
    """
    # Build modules one at a time and cap the packet: ``numModules`` is a
    # uint8 (≤ 255) and ``size`` is a uint16, so > 255 markers (or fewer with
    # long names exceeding the UDP payload) would make struct.pack raise and
    # kill the send thread. Drop the overflow silently – a hard backstop; the
    # operator-facing limit belongs in controlled_marker_ids validation, and
    # the send loop surfaces capping (see ``RttrpmServer._send_packet``).
    module_blobs: list[bytes] = []
    total_size = _HEADER_SIZE
    for t in markers:
        if len(module_blobs) >= _MAX_MODULES:
            break
        blob = encode_rttrpm_trackable_module(t.name, encode_rttrpm_centroid_module(*t.pos))
        if total_size + len(blob) > _MAX_PACKET_BYTES:
            break
        module_blobs.append(blob)
        total_size += len(blob)

    header = struct.pack(
        "!HHHIBHIB",
        _INT_SIG,
        _FLOAT_SIG,
        _VERSION,
        pkt_id & 0xFFFFFFFF,
        _PKT_FORMAT_RAW,
        total_size,
        context & 0xFFFFFFFF,
        len(module_blobs),
    )
    return header + b"".join(module_blobs)


# ---------------------------------------------------------------------------
# RttrpmServer
# ---------------------------------------------------------------------------


class RttrpmServer:
    """Sends RTTrPM marker position data via UDP unicast.

    Accepts references to existing ``Marker`` objects (from
    openfollow.psn.marker) so that PSN and RTTrPM share the same position
    state without duplication.

    Runs a single background thread:
    - Send thread (``fps`` Hz, default 60): position data packets

    Thread-safe via ``_lock``.
    """

    def __init__(
        self,
        host: str,
        port: int = DEFAULT_PORT,
        fps: float = 60.0,
        context: int = 0,
    ) -> None:
        self._host = host
        self._port = port
        self._fps = fps
        self._context = context

        # Shared marker references (keyed by marker_id)
        self._markers: dict[int, Marker] = {}
        self._lock = threading.Lock()

        self._pkt_id: int = 0
        self._stop_event = threading.Event()
        self._socket: socket.socket | None = None
        self._send_thread: threading.Thread | None = None
        self._send_errors: int = 0
        self._send_total: int = 0
        self._next_rebuild_at: float = 0.0
        # Warn-once-per-episode guard: True while a run of capped frames is
        # being suppressed; re-armed once frames stop capping so a later
        # misconfiguration surfaces again.
        self._cap_warned: bool = False

    # -- Marker management ---------------------------------------------------

    def register_marker(self, marker: Marker) -> None:
        """Register an existing Marker reference for RTTrPM output.

        The Marker must be the same object used by PsnServer so that
        position updates are automatically reflected in RTTrPM packets.
        """
        with self._lock:
            self._markers[marker.marker_id] = marker

    def unregister_marker(self, marker_id: int) -> None:
        """Remove a marker from RTTrPM output (no-op if absent)."""
        with self._lock:
            self._markers.pop(marker_id, None)

    def get_marker(self, marker_id: int) -> Marker | None:
        """Return a registered marker by ID, or ``None``."""
        with self._lock:
            return self._markers.get(marker_id)

    # -- Lifecycle ------------------------------------------------------------

    def start(self) -> None:
        """Open the UDP socket and start the send thread."""
        self._stop_event.clear()
        self._cap_warned = False
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._send_thread = threading.Thread(target=self._send_loop, daemon=True, name="RTTrPM-Send")
        self._send_thread.start()

    def stop(self) -> None:
        """Signal the send thread to stop, wait for it, then close the socket."""
        self._stop_event.set()
        if self._send_thread is not None:
            self._send_thread.join(timeout=1.0)
            if self._send_thread.is_alive():
                logger.warning("RTTrPM send thread did not stop within timeout")
            self._send_thread = None
        sock = self._socket
        self._socket = None
        if sock is not None:
            sock.close()

    def restart(self, *, host: str, port: int, fps: float, context: int) -> None:
        """Stop, reconfigure, and restart; marker registrations survive."""
        self.stop()
        self._host = host
        self._port = port
        self._fps = fps
        self._context = context
        self.start()

    def __enter__(self) -> RttrpmServer:
        self.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.stop()

    # -- Send loop ------------------------------------------------------------

    def _send_loop(self) -> None:
        # Guard against zero fps in direct constructor calls.
        fps = self._fps if self._fps > 0 else 1
        interval = 1.0 / fps
        while not self._stop_event.is_set():
            self._send_packet()
            self._stop_event.wait(interval)

    def _send_packet(self) -> None:
        with self._lock:
            markers = list(self._markers.values())
        if not markers:
            return
        # A prior socket rebuild that failed leaves _socket None; retry it
        # (throttled) here so the loop can recover instead of spinning on a
        # dead None socket for the rest of the server's life.
        if self._socket is None:
            self._maybe_rebuild_socket_after_error()
            if self._socket is None:
                # Still down: skip encoding a packet _send() would only drop.
                return
        pkt_id = self._pkt_id
        self._pkt_id = (self._pkt_id + 1) & 0xFFFFFFFF
        payload = encode_rttrpm_packet(pkt_id, markers, self._context)
        self._warn_if_capped(len(markers), payload)
        self._send(payload)

    def _warn_if_capped(self, submitted: int, payload: bytes) -> None:
        """Warn once per cap episode when markers are dropped from a packet.

        ``numModules`` is the uint8 at offset 17. When fewer modules ship
        than were submitted, the packet was capped. Re-arms once a frame is
        no longer capped so a recurring or newly-introduced misconfiguration
        is surfaced rather than suppressed for the rest of the process.
        """
        encoded = payload[17]
        dropped = submitted - encoded
        if dropped <= 0:
            self._cap_warned = False
            return
        if self._cap_warned:
            return
        self._cap_warned = True
        logger.warning(
            "RTTrPM packet capped: dropped %d of %d markers (uint8 count / UDP size limit).",
            dropped,
            submitted,
        )

    def _send(self, data: bytes) -> None:
        sock = self._socket
        if sock is None:
            return
        with self._lock:
            self._send_total += 1
        try:
            sock.sendto(data, (self._host, self._port))
        except OSError as exc:
            with self._lock:
                self._send_errors += 1
                errors = self._send_errors
                total = self._send_total
            if errors <= 5 or errors % 100 == 0:
                logger.warning(
                    "RTTrPM send failed (%d/%d errors): %s",
                    errors,
                    total,
                    exc,
                )
            if exc.errno in _TRANSIENT_SEND_ERRNOS:
                self._maybe_rebuild_socket_after_error()

    def _maybe_rebuild_socket_after_error(self) -> None:
        with self._lock:
            now = time.monotonic()
            if now < self._next_rebuild_at:
                return
            self._next_rebuild_at = now + _SOCKET_REBUILD_MIN_INTERVAL_SECONDS
        self._rebuild_socket_after_error()

    def _rebuild_socket_after_error(self) -> None:
        """Close and reopen the UDP socket after transient interface errors."""
        with self._lock:
            old_sock = self._socket
            try:
                self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            except OSError as exc:
                logger.warning("RTTrPM: socket rebuild failed: %s", exc)
                self._socket = None
        if old_sock is not None:
            try:
                old_sock.close()
            except OSError:
                pass
