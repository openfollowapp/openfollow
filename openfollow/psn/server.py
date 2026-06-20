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
import time

import multicast_expert
import pypsn

from openfollow.psn.marker import Marker

logger = logging.getLogger(__name__)

DEFAULT_MCAST_IP = "236.10.10.10"
DEFAULT_PORT = 56565

_MAX_SOCKET_RETRIES = 3
_SOCKET_RETRY_DELAY = 2.0  # seconds


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
    ) -> None:
        self._system_name = system_name
        self._target_ip = target_ip
        self._port = port
        self._mcast_ip = mcast_ip
        self._source_ip = source_ip.strip()
        self._data_fps = data_fps
        self._info_fps = info_fps

        self._markers: dict[int, Marker] = {}
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._frame_id: int = 0
        self._socket: multicast_expert.McastTxSocket | socket.socket | None = None
        self._exit_stack: contextlib.ExitStack = contextlib.ExitStack()
        self._data_thread: threading.Thread | None = None
        self._info_thread: threading.Thread | None = None
        self._socket_thread: threading.Thread | None = None
        self._send_errors: int = 0
        self._send_total: int = 0

    def add_marker(self, marker_id: int, name: str) -> Marker:
        """Register new marker (marker_id must be >= 1)."""
        marker = Marker(marker_id, name)
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
        self._stop_event.clear()
        self._exit_stack = contextlib.ExitStack()
        if self._mcast_ip:
            if not self._try_open_multicast_socket_once(attempt=1):
                self._socket_thread = threading.Thread(
                    target=self._retry_multicast_socket_background,
                    daemon=True,
                    name="PSN-SocketRetry",
                )
                self._socket_thread.start()
        else:
            self._socket = self._exit_stack.enter_context(socket.socket(socket.AF_INET, socket.SOCK_DGRAM))
        self._data_thread = threading.Thread(target=self._data_loop, daemon=True, name="PSN-Data")
        self._info_thread = threading.Thread(target=self._info_loop, daemon=True, name="PSN-Info")
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

    # -- Socket helpers -------------------------------------------------------

    def _try_open_multicast_socket_once(self, attempt: int) -> bool:
        """Attempt to create the multicast TX socket once. Returns True on success."""
        mcast_ip = self._mcast_ip
        if not mcast_ip:  # pragma: no cover
            return False
        # Auto-detect primary outbound IPv4 to avoid default-route ambiguity.
        from openfollow.net_utils import resolve_iface_ip

        iface_ip = resolve_iface_ip(self._source_ip)
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
            self._socket = self._exit_stack.enter_context(sock)
            return True
        except Exception as exc:
            logger.warning(
                "PSN multicast socket failed (attempt %d/%d): %s",
                attempt,
                _MAX_SOCKET_RETRIES,
                exc,
            )
            return False

    def _retry_multicast_socket_background(self) -> None:
        """Retry multicast socket creation bounded by _MAX_SOCKET_RETRIES."""
        for attempt in range(2, _MAX_SOCKET_RETRIES + 1):
            self._stop_event.wait(_SOCKET_RETRY_DELAY)
            if self._stop_event.is_set():
                return
            if self._try_open_multicast_socket_once(attempt=attempt):
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

    def _recover_multicast_socket_background(self) -> None:
        """Recover multicast socket after transient send failure (unbounded until stop_event)."""
        attempt = 0
        while not self._stop_event.is_set():
            attempt += 1
            self._stop_event.wait(_SOCKET_RETRY_DELAY)
            if self._stop_event.is_set():
                return
            if self._try_open_multicast_socket_once(attempt=attempt):
                logger.info("PSN multicast socket recovered on attempt %d.", attempt)
                return

    def _handle_send_error(self, exc: OSError) -> None:
        """On transient interface error, rebuild socket in background."""
        # Once stopping, teardown owns the socket/exit-stack lifecycle: a recovery
        # thread spawned here would be orphaned (stop() already joined+nulled the
        # socket thread) and could leak an FD racing stop()'s stack close.
        if self._stop_event.is_set():
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
            if self._stop_event.is_set():
                return
            if self._socket_thread is not None and self._socket_thread.is_alive():
                return
            # Tear down broken socket before spawning recovery thread.
            old_stack = self._exit_stack
            self._socket = None
            self._exit_stack = contextlib.ExitStack()
            self._socket_thread = threading.Thread(
                target=self._recover_multicast_socket_background,
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

    def _data_loop(self) -> None:
        interval = 1.0 / self._data_fps
        while not self._stop_event.is_set():
            self._send_data_packet()
            self._stop_event.wait(interval)

    def _info_loop(self) -> None:
        interval = 1.0 / self._info_fps
        while not self._stop_event.is_set():
            self._send_info_packet()
            self._stop_event.wait(interval)

    def _make_psn_info(self) -> pypsn.PsnInfo:
        """Build a ``PsnInfo`` header and advance the frame counter."""
        with self._lock:
            frame_id = self._frame_id
            self._frame_id = (self._frame_id + 1) % 256
        info = pypsn.PsnInfo(
            timestamp=int(time.time() * 1000),
            version_high=2,
            version_low=0,
            frame_id=frame_id,
            packet_count=1,
        )
        return info

    def _snapshot_markers(self) -> list[Marker]:
        """Return a consistent copy of the marker list under the lock."""
        with self._lock:
            return list(self._markers.values())

    def _send_data_packet(self) -> None:
        markers = self._snapshot_markers()
        if not markers:
            return
        # PSN spec uses "trackers" field name; internally called markers.
        packet = pypsn.PsnDataPacket(
            info=self._make_psn_info(),
            trackers=[t.to_psn_marker() for t in markers],
        )
        self._send(pypsn.prepare_psn_data_packet_bytes(packet))

    def _send_info_packet(self) -> None:
        markers = self._snapshot_markers()
        if not markers:
            return
        packet = pypsn.PsnInfoPacket(
            info=self._make_psn_info(),
            name=self._system_name,
            trackers=[t.to_psn_marker_info() for t in markers],
        )
        self._send(pypsn.prepare_psn_info_packet_bytes(packet))

    def _send(self, data: bytes) -> None:
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
            self._handle_send_error(exc)
