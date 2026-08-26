# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Sends PSN marker data via multicast or unicast UDP.

``PsnServer`` registers markers and broadcasts PSN data and info packets
from two background threads, with bounded socket-open retry and transient
send-error recovery.
"""

import contextlib
import errno
import logging
import socket
import threading
from collections.abc import Callable

import multicast_expert
import pypsn

from openfollow.psn.clock import psn_timestamp_usec
from openfollow.psn.marker import Marker, is_marker_stale

logger = logging.getLogger(__name__)

DEFAULT_MCAST_IP = "236.10.10.10"
DEFAULT_PORT = 56565

_MAX_SOCKET_RETRIES = 3
_SOCKET_RETRY_DELAY = 2.0  # seconds
_FRAME_ID_WRAP = 256  # frame_id is a uint8 on the wire


class _Unchanged:
    """Sentinel to distinguish "unchanged" from None (which is a valid value)."""


_UNCHANGED = _Unchanged()


# Transient interface errors worth retrying (vs. fatal EACCES).
_TRANSIENT_SEND_ERRNOS: frozenset[int] = frozenset(
    {
        errno.EADDRNOTAVAIL,
        errno.ENETUNREACH,
        errno.ENETDOWN,
        errno.EHOSTDOWN,
        errno.EHOSTUNREACH,
    }
)


class PsnServer:
    """Sends PSN marker data via multicast or unicast UDP.

    Runs two background threads – one for data packets (60 fps default)
    and one for info packets (1 fps default).  Thread-safe via ``_lock``.
    """

    def __init__(
        self,
        system_name: str = "OpenFollow",
        target_ip: str = "127.0.0.1",
        port: int = DEFAULT_PORT,
        mcast_ip: str | None = DEFAULT_MCAST_IP,
        source_ip: str = "",
        data_fps: float = 60.0,
        info_fps: float = 1.0,
        clock: Callable[[], int] = psn_timestamp_usec,
    ) -> None:
        self._system_name = system_name
        self._target_ip = target_ip
        self._port = port
        self._mcast_ip = mcast_ip
        self._source_ip = source_ip.strip()
        self._data_fps = data_fps
        self._info_fps = info_fps
        self._clock = clock

        self._markers: dict[int, Marker] = {}
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        # A receiver reads a gap in a stream's frame ids as a dropped frame, so
        # the two streams must not draw from one sequence.
        self._data_frame_id: int = 0
        self._info_frame_id: int = 0
        self._socket: multicast_expert.McastTxSocket | socket.socket | None = None
        self._exit_stack: contextlib.ExitStack = contextlib.ExitStack()
        self._data_thread: threading.Thread | None = None
        self._info_thread: threading.Thread | None = None
        self._socket_thread: threading.Thread | None = None
        self._send_errors: int = 0
        self._send_total: int = 0

    def add_marker(self, marker_id: int, name: str) -> Marker:
        """Register new marker (marker_id must be >= 1)."""
        marker = Marker(marker_id, name, clock=self._clock)
        with self._lock:
            self._markers[marker_id] = marker
        return marker

    def update_marker_name(self, marker_id: int, name: str) -> bool:
        """Rename marker under single lock to prevent dict-level races."""
        with self._lock:
            marker = self._markers.get(marker_id)
            if marker is None:
                return False
            marker.set_name(name)
            return True

    def remove_marker(self, marker_id: int) -> None:
        """Remove a marker by ID (no-op if absent)."""
        with self._lock:
            self._markers.pop(marker_id, None)

    def get_marker(self, marker_id: int) -> Marker | None:
        """Return a marker by ID, or ``None``."""
        with self._lock:
            return self._markers.get(marker_id)

    def start(self) -> None:
        """Open the network socket and start send threads."""
        # One stop signal per generation. A thread that outlived its join in
        # stop() keeps the previous, permanently-set one and exits on its next
        # check, rather than being revived by a shared event being cleared here.
        stop_event = threading.Event()
        self._stop_event = stop_event
        self._exit_stack = contextlib.ExitStack()
        if self._mcast_ip:
            if not self._try_open_multicast_socket_once(attempt=1):
                self._socket_thread = threading.Thread(
                    target=self._retry_multicast_socket_background,
                    args=(stop_event,),
                    daemon=True,
                    name="PSN-SocketRetry",
                )
                self._socket_thread.start()
        else:
            self._socket = self._exit_stack.enter_context(socket.socket(socket.AF_INET, socket.SOCK_DGRAM))
        self._data_thread = threading.Thread(target=self._data_loop, args=(stop_event,), daemon=True, name="PSN-Data")
        self._info_thread = threading.Thread(target=self._info_loop, args=(stop_event,), daemon=True, name="PSN-Info")
        self._data_thread.start()
        self._info_thread.start()

    def stop(self) -> None:
        """Signal threads to stop, wait for them, then close the socket."""
        self._stop_event.set()
        # Stop retry thread first so it can't mutate _socket/_exit_stack after close.
        if self._socket_thread is not None:
            self._socket_thread.join(timeout=_SOCKET_RETRY_DELAY + 1.0)
            if self._socket_thread.is_alive():
                logger.warning("PSN socket-retry thread did not stop within timeout")
            self._socket_thread = None
        # Wait for send threads before closing socket.
        if self._data_thread is not None:
            self._data_thread.join(timeout=0.5)
            if self._data_thread.is_alive():
                logger.warning("PSN data thread did not stop within timeout")
            self._data_thread = None
        if self._info_thread is not None:
            self._info_thread.join(timeout=1.5)
            if self._info_thread.is_alive():
                logger.warning("PSN info thread did not stop within timeout")
            self._info_thread = None
        # Now safe to close the socket. Null it and grab the exit stack under
        # the lock so a concurrent _handle_send_error spawn (which mutates the
        # same state under _lock) can't interleave with teardown. The join above
        # stays OUTSIDE the lock – the recovery thread takes _lock itself, so
        # holding it across the join would deadlock. Close the stack outside the
        # lock to avoid stalling a send loop on the FD close.
        with self._lock:
            self._socket = None
            stack = self._exit_stack
        stack.close()

    def rebind(
        self,
        source_ip: str,
        *,
        mcast_ip: str | None | _Unchanged = _UNCHANGED,
    ) -> None:
        """Recreate multicast socket on new interface. Raises OSError on sync failure (live-apply requires signal)."""
        self.stop()
        self._source_ip = source_ip.strip()
        if not isinstance(mcast_ip, _Unchanged):
            if isinstance(mcast_ip, str):
                mcast_ip = mcast_ip.strip()
            self._mcast_ip = mcast_ip
        self.start()
        if self._mcast_ip and self._socket is None:
            self.stop()
            raise OSError(
                f"PSN server failed to open multicast socket on "
                f"mcast_ip={self._mcast_ip!r}, source_ip={self._source_ip!r}",
            )

    def rebind_mcast_ip(self, mcast_ip: str | None) -> None:
        """Recreate multicast socket on new multicast group (preserves source_ip)."""
        self.rebind(self._source_ip, mcast_ip=mcast_ip)

    def update_system_name(self, name: str) -> None:
        """Update system name announced in PSN info packets."""
        with self._lock:
            self._system_name = name

    def __enter__(self) -> "PsnServer":
        self.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.stop()

    def _resolve_stop(self, stop_event: threading.Event | None) -> threading.Event:
        """The caller's generation event; the live one for direct calls."""
        return self._stop_event if stop_event is None else stop_event

    # -- Socket helpers -------------------------------------------------------

    def _try_open_multicast_socket_once(self, attempt: int, stop_event: threading.Event | None = None) -> bool:
        """Attempt to create the multicast TX socket once. Returns True on success."""
        mcast_ip = self._mcast_ip
        if not mcast_ip:  # pragma: no cover
            return False
        # Auto-detect primary outbound IPv4 to avoid default-route ambiguity.
        from openfollow.net_utils import resolve_iface_ip

        iface_ip = resolve_iface_ip(self._source_ip)
        staging = contextlib.ExitStack()
        try:
            if iface_ip:
                sock = multicast_expert.McastTxSocket(
                    socket.AF_INET,
                    mcast_ips=[mcast_ip],
                    iface_ip=iface_ip,
                    enable_external_loopback=True,
                )
            else:
                sock = multicast_expert.McastTxSocket(
                    socket.AF_INET,
                    mcast_ips=[mcast_ip],
                    enable_external_loopback=True,
                )
            opened = staging.enter_context(sock)
        except Exception as exc:
            logger.warning(
                "PSN multicast socket failed (attempt %d/%d): %s",
                attempt,
                _MAX_SOCKET_RETRIES,
                exc,
            )
            return False
        # The open runs off the lock so a retry on a flapping NIC can't stall the
        # send loops or stop(); only the hand-over is locked, which is what pairs
        # with stop()'s teardown. A socket thread from a superseded generation
        # must not hand the live one a socket bound to the interface it just left.
        with self._lock:
            if not self._resolve_stop(stop_event).is_set():
                self._socket = opened
                self._exit_stack.push(staging.pop_all())
                return True
            stale = staging.pop_all()
        stale.close()
        return False

    def _retry_multicast_socket_background(self, stop_event: threading.Event | None = None) -> None:
        """Retry multicast socket creation bounded by _MAX_SOCKET_RETRIES."""
        stop = self._resolve_stop(stop_event)
        for attempt in range(2, _MAX_SOCKET_RETRIES + 1):
            stop.wait(_SOCKET_RETRY_DELAY)
            if stop.is_set():
                return
            if self._try_open_multicast_socket_once(attempt=attempt, stop_event=stop):
                logger.info(
                    "PSN multicast socket connected on retry %d/%d.",
                    attempt,
                    _MAX_SOCKET_RETRIES,
                )
                return
        logger.error(
            "PSN multicast socket failed after %d attempts – PSN output disabled.",
            _MAX_SOCKET_RETRIES,
        )

    def _recover_multicast_socket_background(self, stop_event: threading.Event | None = None) -> None:
        """Recover multicast socket after transient send failure (unbounded until stop_event)."""
        stop = self._resolve_stop(stop_event)
        attempt = 0
        while not stop.is_set():
            attempt += 1
            stop.wait(_SOCKET_RETRY_DELAY)
            if stop.is_set():
                return
            if self._try_open_multicast_socket_once(attempt=attempt, stop_event=stop):
                logger.info("PSN multicast socket recovered on attempt %d.", attempt)
                return

    def _handle_send_error(self, exc: OSError, stop_event: threading.Event | None = None) -> None:
        """On transient interface error, rebuild socket in background."""
        # Once stopping, teardown owns the socket/exit-stack lifecycle: a recovery
        # thread spawned here would be orphaned (stop() already joined+nulled the
        # socket thread) and could leak an FD racing stop()'s stack close.
        # A send that fails on a superseded generation is judged by its own
        # event, so a dying survivor can't tear down the live socket.
        stop = self._resolve_stop(stop_event)
        if stop.is_set():
            return
        if exc.errno not in _TRANSIENT_SEND_ERRNOS:
            return
        # Unicast mode needs no socket rebuild; just retry next send.
        if not self._mcast_ip:
            return
        # Lock prevents multiple recovery threads on concurrent send failures.
        with self._lock:
            # Re-check under the lock: stop() sets the event before tearing the
            # socket/exit-stack down, so once it's set no recovery thread is
            # spawned – spawn and teardown observe one consistent stop state
            # rather than relying on the recovery loop's own stop-check.
            if stop.is_set():
                return
            if self._socket_thread is not None and self._socket_thread.is_alive():
                return
            # Tear down broken socket before spawning recovery thread.
            old_stack = self._exit_stack
            self._socket = None
            self._exit_stack = contextlib.ExitStack()
            self._socket_thread = threading.Thread(
                target=self._recover_multicast_socket_background,
                args=(stop,),
                daemon=True,
                name="PSN-SocketRecover",
            )
            self._socket_thread.start()
        # Close old stack outside lock to avoid stalling send loops.
        try:
            old_stack.close()
        except Exception:
            logger.exception("PSN: closing stale socket stack failed")

    # -- Send loops -----------------------------------------------------------

    def _data_loop(self, stop_event: threading.Event | None = None) -> None:
        stop = self._resolve_stop(stop_event)
        interval = 1.0 / self._data_fps
        while not stop.is_set():
            self._send_data_packet(stop)
            stop.wait(interval)

    def _info_loop(self, stop_event: threading.Event | None = None) -> None:
        stop = self._resolve_stop(stop_event)
        interval = 1.0 / self._info_fps
        while not stop.is_set():
            self._send_info_packet(stop)
            stop.wait(interval)

    def _make_psn_info(self, frame_id: int) -> pypsn.PsnInfo:
        """Build a ``PsnInfo`` header carrying ``frame_id``."""
        return pypsn.PsnInfo(
            timestamp=self._clock(),
            version_high=2,
            version_low=0,
            frame_id=frame_id,
            packet_count=1,
        )

    def _next_data_header(self) -> pypsn.PsnInfo:
        """Build a data-packet header, advancing the data stream's frame counter."""
        with self._lock:
            frame_id = self._data_frame_id
            self._data_frame_id = (self._data_frame_id + 1) % _FRAME_ID_WRAP
        return self._make_psn_info(frame_id)

    def _next_info_header(self) -> pypsn.PsnInfo:
        """Build an info-packet header, advancing the info stream's frame counter."""
        with self._lock:
            frame_id = self._info_frame_id
            self._info_frame_id = (self._info_frame_id + 1) % _FRAME_ID_WRAP
        return self._make_psn_info(frame_id)

    def _snapshot_markers(self) -> list[Marker]:
        """Return a consistent copy of the marker list under the lock."""
        with self._lock:
            return list(self._markers.values())

    def _send_data_packet(self, stop_event: threading.Event | None = None) -> None:
        markers = self._snapshot_markers()
        if not markers:
            return
        # PSN spec uses "trackers" field name; internally called markers.
        # Snapshot the trackers before stamping the header: a marker written in
        # between would otherwise carry a timestamp ahead of the header it ships
        # in, which underflows a receiver computing age as unsigned.
        # A marker the frame loop has stopped writing publishes STATUS as
        # invalid: this thread keeps transmitting at full rate regardless, so
        # validity is the only field that can say the position is no longer live.
        trackers = [t.to_psn_marker(stale=is_marker_stale(t)) for t in markers]
        packet = pypsn.PsnDataPacket(info=self._next_data_header(), trackers=trackers)
        self._send(pypsn.prepare_psn_data_packet_bytes(packet), stop_event)

    def _send_info_packet(self, stop_event: threading.Event | None = None) -> None:
        markers = self._snapshot_markers()
        if not markers:
            return
        packet = pypsn.PsnInfoPacket(
            info=self._next_info_header(),
            name=self._system_name,
            trackers=[t.to_psn_marker_info() for t in markers],
        )
        self._send(pypsn.prepare_psn_info_packet_bytes(packet), stop_event)

    def _send(self, data: bytes, stop_event: threading.Event | None = None) -> None:
        sock = self._socket
        if sock is None:
            return
        dest = self._mcast_ip if self._mcast_ip else self._target_ip
        with self._lock:
            self._send_total += 1
        try:
            sock.sendto(data, (dest, self._port))
        except OSError as exc:
            with self._lock:
                self._send_errors += 1
                errors = self._send_errors
                total = self._send_total
            if errors <= 5 or errors % 100 == 0:
                logger.warning(
                    "PSN send failed (%d/%d errors): %s",
                    errors,
                    total,
                    exc,
                )
            # Rebuild socket on transient interface errors.
            self._handle_send_error(exc, stop_event)
