# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""``ReceiveGate``: a receive wait another thread can end at once."""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator

import pytest

from openfollow.receive_gate import ReceiveGate

pytestmark = pytest.mark.unit

_BOUND_S = 2.0


@pytest.fixture
def sock() -> Iterator[socket.socket]:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        yield s


def _send(dest: socket.socket) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as tx:
        tx.sendto(b"x", dest.getsockname())


def test_a_datagram_ends_the_wait(sock: socket.socket) -> None:
    gate = ReceiveGate(sock)
    try:
        _send(sock)
        assert gate.wait() is True
    finally:
        gate.close()


def test_stop_wakes_a_thread_already_waiting(sock: socket.socket) -> None:
    gate = ReceiveGate(sock)
    results: list[bool] = []
    waiter = threading.Thread(target=lambda: results.append(gate.wait()))
    waiter.start()
    try:
        start = time.monotonic()
        gate.stop()
        waiter.join(_BOUND_S)
        assert not waiter.is_alive()
        assert time.monotonic() - start < _BOUND_S
        assert results == [False]
    finally:
        gate.close()


def test_a_stopped_gate_never_waits_again_even_with_data_pending(sock: socket.socket) -> None:
    gate = ReceiveGate(sock)
    try:
        gate.stop()
        _send(sock)
        assert gate.wait() is False
        assert gate.wait() is False
    finally:
        gate.close()


def test_stopping_or_closing_a_closed_gate_is_harmless(sock: socket.socket) -> None:
    gate = ReceiveGate(sock)
    gate.close()
    gate.stop()
    gate.close()
    assert gate.wait() is False
    assert sock.fileno() != -1  # the socket stays the caller's
