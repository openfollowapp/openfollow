# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""A receive loop that another thread can end at once.

A thread parked in ``recvfrom`` or ``select`` is not woken when another thread
closes its socket, so a stop that joins it waits out the read timeout. A
:class:`ReceiveGate` waits on the socket and on a private socket pair, and
:meth:`ReceiveGate.stop` writes to the pair, so the wait returns immediately.

One gate serves one socket and one receive thread. Once stopped it never waits
again, so a thread that outlives its stop cannot read a socket opened after it.
Close it from that thread, or once that thread has been joined.
"""

from __future__ import annotations

import selectors
import socket
import threading


class ReceiveGate:
    """Waits for *sock* to become readable, or for :meth:`stop` from any thread."""

    def __init__(self, sock: socket.socket) -> None:
        self._stopped = threading.Event()
        self._wake_r, self._wake_w = socket.socketpair()
        self._wake_r.setblocking(False)
        self._wake_w.setblocking(False)
        # A selector, not select(): its fd numbers are not capped at FD_SETSIZE.
        self._selector = selectors.DefaultSelector()
        self._selector.register(sock, selectors.EVENT_READ)
        self._selector.register(self._wake_r, selectors.EVENT_READ)

    def wait(self) -> bool:
        """Block until the socket is readable (True) or the gate is stopped (False)."""
        if self._stopped.is_set():
            return False
        self._selector.select()
        return not self._stopped.is_set()

    def stop(self) -> None:
        """End the current and every later :meth:`wait`. Never blocks."""
        self._stopped.set()
        try:
            self._wake_w.send(b"\0")
        except OSError:
            pass  # closed, or a wake already pending: the wait ends either way

    def close(self) -> None:
        """Stop, then release the wake pair and the selector; the socket stays the caller's."""
        self.stop()
        self._selector.close()
        self._wake_r.close()
        self._wake_w.close()
