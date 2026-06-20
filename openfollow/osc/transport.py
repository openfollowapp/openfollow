# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""TCP transport for OSC – SLIP and length-prefix framing.

OSC over TCP needs an explicit message boundary. Two framings are
supported per-binding:

- SLIP (RFC 1055): each message is bracketed by ``END=0xC0`` bytes;
  ``0xC0``/``0xDB`` inside the payload are escaped to ``0xDB,0xDC`` and
  ``0xDB,0xDD``. Required by OSC 1.1.
- Length-prefix (OSC 1.0): each message is preceded by a 4-byte
  big-endian length.

Framing is a stateless per-message transform; the persistent
``SOCK_STREAM`` connection, reconnect, and backoff machinery are shared.

Behaviour:

- Drop-on-down, no buffering: a packet arriving during backoff is
  dropped (same skip-on-loss as UDP; bounds memory on an unreachable
  receiver).
- Bounded write deadline: ``select`` waits up to 100 ms for writability;
  if the kernel send buffer is full the packet is dropped rather than
  stalling the 60 Hz scheduler thread.
- Reader thread: a daemon thread drains inbound RX per connection so the
  kernel receive buffer can't fill and back-pressure the writer.
- Backoff schedule: 0.25/0.5/1/2/5 s, steady at 5 s; reset on every
  successful send.
- ``close()`` joins the reader with a 1 s timeout so a stuck receiver
  can't block shutdown beyond ~1 s per target.
"""

from __future__ import annotations

import logging
import select
import socket
import threading
import time
from typing import Any

from openfollow.configuration import VALID_OSC_FRAMINGS as _FRAMINGS

logger = logging.getLogger(__name__)

try:
    from pythonosc.osc_message_builder import OscMessageBuilder

    _PYTHONOSC_AVAILABLE = True
# pragma: no cover – see openfollow.osc.service for the rationale.
except ImportError:  # pragma: no cover
    _PYTHONOSC_AVAILABLE = False


# Backoff in ms. A transient failure advances the index (capped at
# len-1); a successful send resets it to 0. Tail = steady-state interval
# for a permanently-down peer.
_BACKOFF_SCHEDULE_MS: tuple[int, ...] = (250, 500, 1000, 2000, 5000)
# Tight connect timeout: the 60 Hz scheduler thread is the typical
# caller, and a stalling connect blocks every other transmitter row on
# that thread. Unreachable peers fail fast into the backoff schedule.
_CONNECT_TIMEOUT_S = 0.250
_WRITE_DEADLINE_S = 0.100
_READER_POLL_S = 0.5
_SHUTDOWN_JOIN_S = 1.0


def _now() -> float:
    """Monotonic clock; wrapped so tests can monkeypatch it."""
    return time.monotonic()


# RFC 1055 SLIP framing constants.
_SLIP_END = 0xC0
_SLIP_ESC = 0xDB
_SLIP_ESC_END = 0xDC
_SLIP_ESC_ESC = 0xDD


def _slip_encode(payload: bytes) -> bytes:
    """Encode ``payload`` per RFC 1055.

    Brackets the payload with ``END=0xC0`` on both sides (leading END
    flushes preceding line noise; trailing END marks the boundary).
    ``0xC0`` and ``0xDB`` inside the payload are escaped to two-byte
    sequences so they can't be mistaken for framing bytes.
    """
    out = bytearray()
    out.append(_SLIP_END)
    for b in payload:
        if b == _SLIP_END:
            out.append(_SLIP_ESC)
            out.append(_SLIP_ESC_END)
        elif b == _SLIP_ESC:
            out.append(_SLIP_ESC)
            out.append(_SLIP_ESC_ESC)
        else:
            out.append(b)
    out.append(_SLIP_END)
    return bytes(out)


def _length_prefix_encode(payload: bytes) -> bytes:
    """Encode ``payload`` with a 4-byte big-endian length header."""
    return len(payload).to_bytes(4, "big") + payload


class TcpOscSender:
    """Connection-oriented OSC sender for one ``(host, port)`` target.

    Surface mirrors ``SimpleUDPClient``: callers see
    ``send_message(address, args)`` and don't care about transport.
    ``OscService`` caches one instance per target.

    Thread model:

    - ``send_message`` runs on the caller's thread (typically the 60 Hz
      transmitter scheduler); it builds the framed packet and writes it
      through ``select``-bounded loops.
    - One daemon reader thread per connection drains and discards inbound
      bytes; it exits when the socket closes or ``close()`` sets the stop
      event.

    ``send_message`` raises ``OSError`` on transport error or backoff
    drop (same as ``SimpleUDPClient`` on UDP failure), counted in
    ``ClientStats``.
    """

    def __init__(
        self,
        host: str,
        port: int,
        framing: str = "length_prefix",
    ) -> None:
        # Constructor default is ``"length_prefix"``; the runtime path
        # always passes ``framing`` explicitly, so the config-layer
        # default (``"slip"``) is unaffected. Direct callers wanting SLIP
        # must pass ``framing="slip"``.
        if framing not in _FRAMINGS:
            raise ValueError(
                f"TcpOscSender framing must be one of {_FRAMINGS}, got {framing!r}",
            )
        self._host = host
        self._port = port
        self._framing = framing
        self._sock: socket.socket | None = None
        self._reader_thread: threading.Thread | None = None
        self._reader_stop: threading.Event = threading.Event()
        self._lock = threading.Lock()
        # Send-side serialization. Concurrent ``send_message`` callers
        # could otherwise both observe ``_sock is None`` and
        # double-connect, or interleave bytes mid-frame and break the
        # receiver's framing. ``close()`` also takes this lock so shutdown
        # can't race an in-flight ``_open()`` and orphan a fresh socket /
        # reader. ``_lock`` is nested under ``_send_lock`` for
        # field-mutation windows; ordering is always ``_send_lock`` →
        # ``_lock``, never the reverse, so no deadlock cycle.
        self._send_lock = threading.Lock()
        # Backoff: ``_next_attempt_at`` gates the next connection attempt;
        # sends inside the window raise OSError without touching the OS.
        self._next_attempt_at: float = 0.0
        self._backoff_idx: int = 0

    def send_message(self, address: str, args: list[Any]) -> None:
        """Send one OSC message. Raises ``OSError`` on transport failure
        or while the target is in its post-failure backoff window.

        Thread-safe: ``_send_lock`` keeps concurrent callers sharing one
        cached sender from racing through ``_open()`` (leaking sockets)
        or interleaving bytes mid-frame (breaking framing).
        """
        with self._send_lock:
            if self._sock is None:
                now = _now()
                if now < self._next_attempt_at:
                    raise OSError(
                        f"TCP {self._host}:{self._port} in backoff window",
                    )
                self._open()
            packet = self._build_packet(address, args)
            try:
                self._write(packet)
            except (OSError, ValueError):
                self._mark_failed()
                raise
            # Reset backoff on success so a transient blip doesn't leave
            # the target throttled.
            self._backoff_idx = 0

    def close(self) -> None:
        """Stop the reader thread and close the socket. Idempotent.

        Serialised with ``send_message`` via ``_send_lock`` so shutdown
        can't race an in-flight ``_open()`` (which would install a fresh
        socket + reader thread after close declared us shut down).
        Ordering stays ``_send_lock`` → ``_lock``.
        """
        with self._send_lock:
            self._reader_stop.set()
            with self._lock:
                sock = self._sock
                thread = self._reader_thread
                self._sock = None
                self._reader_thread = None
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
            if thread is not None:
                thread.join(timeout=_SHUTDOWN_JOIN_S)

    def _open(self) -> None:
        try:
            sock = socket.create_connection(
                (self._host, self._port),
                timeout=_CONNECT_TIMEOUT_S,
            )
        except OSError:
            self._mark_failed()
            raise
        sock.setblocking(False)
        # Fresh stop event for the new reader; ``close()`` may have set
        # the previous one.
        self._reader_stop = threading.Event()
        thread = threading.Thread(
            target=self._read_loop,
            args=(sock, self._reader_stop),
            daemon=True,
            name=f"OscTcp-{self._host}:{self._port}",
        )
        with self._lock:
            self._sock = sock
            self._reader_thread = thread
        try:
            thread.start()
        except RuntimeError as exc:
            # Thread creation can fail under OS thread exhaustion. The
            # socket is already installed but has no reader draining its
            # RX; route through ``_mark_failed`` so it's closed and the
            # backoff schedule advances, and surface it as a transport
            # error to honour the documented OSError contract.
            self._mark_failed()
            raise OSError(f"TCP reader thread start failed: {exc}") from exc

    def _build_packet(self, address: str, args: list[Any]) -> bytes:
        # python-osc is a hard dependency; this arm fires only for
        # installs that strip it out.
        if not _PYTHONOSC_AVAILABLE:  # pragma: no cover
            raise OSError("python-osc not installed")
        builder = OscMessageBuilder(address=address)
        for arg in args:
            builder.add_arg(arg)
        msg_bytes = builder.build().dgram
        if self._framing == "slip":
            return _slip_encode(msg_bytes)
        return _length_prefix_encode(msg_bytes)

    def _write(self, packet: bytes) -> None:
        sock = self._sock
        if sock is None:  # pragma: no cover - defensive; _open() set it
            raise OSError("TCP socket not connected")
        deadline = _now() + _WRITE_DEADLINE_S
        written = 0
        while written < len(packet):
            remaining = deadline - _now()
            if remaining <= 0:
                raise OSError(
                    f"TCP write deadline exceeded for {self._host}:{self._port}",
                )
            try:
                _, ready, _ = select.select([], [sock], [], remaining)
            except (OSError, ValueError) as exc:
                raise OSError(f"TCP select failed: {exc}") from exc
            if not ready:
                continue
            try:
                n = sock.send(packet[written:])
            except BlockingIOError:
                # Kernel buffer filled between select and send. Loop;
                # the deadline guard bounds the retry.
                continue
            if n == 0:
                raise OSError("TCP socket closed by peer")
            written += n

    def _read_loop(
        self,
        sock: socket.socket,
        stop: threading.Event,
    ) -> None:
        """Drain RX from the peer until the socket closes or stop fires."""
        while not stop.is_set():
            try:
                ready, _, _ = select.select([sock], [], [], _READER_POLL_S)
            except (OSError, ValueError):
                return
            if not ready:
                continue
            try:
                data = sock.recv(4096)
            except (BlockingIOError, OSError):
                return
            if not data:
                # Peer closed cleanly.
                return
            # RX is drained and discarded; this only stops the kernel
            # receive buffer from back-pressuring the writer.

    def _mark_failed(self) -> None:
        """Drop the current socket and schedule the next attempt.

        Does not join the reader thread (that would block the 60 Hz
        sender for up to a poll cycle). Setting ``_reader_stop`` and
        closing the socket suffices: the reader's next ``select`` either
        wakes on the closed fd or hits its 0.5 s timeout and sees the
        stop event. ``close()`` is the path that joins.
        """
        with self._lock:
            sock = self._sock
            self._sock = None
            self._reader_thread = None
        # Stop the reader so it can't race the next reconnect's socket;
        # ``_open`` mints a fresh stop event.
        self._reader_stop.set()
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        ms = _BACKOFF_SCHEDULE_MS[min(self._backoff_idx, len(_BACKOFF_SCHEDULE_MS) - 1)]
        self._next_attempt_at = _now() + ms / 1000.0
        if self._backoff_idx < len(_BACKOFF_SCHEDULE_MS) - 1:
            self._backoff_idx += 1
