# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""A receive loop that another thread can end at once.

A thread parked in ``recvfrom`` or ``select`` is not woken when another thread
closes its socket, so a stop that joins it waits out the read timeout. A
:class:`ReceiveGate` waits on the socket and on a private socket pair, and
:meth:`ReceiveGate.stop` writes to the pair, so the wait returns immediately.

One gate serves one socket and one receive thread. Once stopped it never waits
again, so a thread that outlives its stop cannot read a socket opened after it.
Only that thread closes it, after its last wait, or its owner when no thread
ever ran: closing under a wait would fail the wait instead of ending it.
"""

from __future__ import annotations

import contextlib
import selectors
import socket
import threading


class ReceiveGate:
    """Waits for *sock* to become readable, or for :meth:`stop` from any thread."""

    def __init__(self, sock: socket.socket) -> None:
        self._stopped = threading.Event()
        # Held across the wake write, so close cannot release the pair under it.
        self._lock = threading.Lock()
        self._closed = False
        with contextlib.ExitStack() as undo:
            # A selector, not select(): its fd numbers are not capped at FD_SETSIZE.
            self._selector = selectors.DefaultSelector()
            undo.callback(self._selector.close)
            self._wake_r, self._wake_w = socket.socketpair()
            undo.callback(self._wake_r.close)
            undo.callback(self._wake_w.close)
            self._wake_r.setblocking(False)
            self._wake_w.setblocking(False)
            self._selector.register(sock, selectors.EVENT_READ)
            self._selector.register(self._wake_r, selectors.EVENT_READ)
            undo.pop_all()

    @property
    def closed(self) -> bool:
        return self._closed

    def wait(self) -> bool:
        """Block until the socket is readable (True) or the gate is stopped (False)."""
        if self._stopped.is_set():
            return False
        self._selector.select()
        return not self._stopped.is_set()

    def stop(self) -> None:
        """End the current and every later :meth:`wait`, without waiting for the receive thread. Idempotent."""
        with self._lock:
            if self._stopped.is_set():
                return
            self._stopped.set()
            # Already stopped either way; a failed wake must not cost the caller its join or close.
            with contextlib.suppress(OSError):
                self._wake_w.send(b"\0")

    def close(self) -> None:
        """Stop, then release the wake pair and the selector; the socket stays the caller's."""
        # Waits out a stop already writing its wake; any later stop finds the gate stopped.
        self.stop()
        self._closed = True
        self._selector.close()
        self._wake_r.close()
        self._wake_w.close()
