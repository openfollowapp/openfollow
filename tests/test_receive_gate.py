# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""``ReceiveGate``: a receive wait another thread can end at once."""

from __future__ import annotations

import selectors
import socket
import threading
import time
from collections.abc import Iterator

import pytest

from openfollow import receive_gate
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
    assert gate.closed
    assert gate.wait() is False
    assert sock.fileno() != -1  # the socket stays the caller's


def test_a_stop_racing_a_close_never_writes_to_a_closed_wake_pair(sock: socket.socket) -> None:
    """The owner stops while the receive thread closes on its way out. The close
    runs inside the stop, after it has decided to wake and before it writes."""
    gate = ReceiveGate(sock)
    closed = threading.Event()
    closer = threading.Thread(target=lambda: (gate.close(), closed.set()))

    class _CloseDuringStop(threading.Event):
        def set(self) -> None:
            super().set()
            if not closer.is_alive() and not closed.is_set():
                closer.start()
                closed.wait(0.2)  # a serialised close cannot finish until the stop has

    gate._stopped = _CloseDuringStop()
    gate.stop()

    closer.join(_BOUND_S)
    assert closed.is_set()
    assert gate.closed


def test_close_releases_the_wake_pair_and_the_selector(sock: socket.socket, monkeypatch: pytest.MonkeyPatch) -> None:
    opened = _record_opened(monkeypatch)
    gate = ReceiveGate(sock)
    assert not gate.closed
    gate.close()

    assert [s.fileno() for s in opened.pairs] == [-1, -1]
    assert all(sel.get_map() is None for sel in opened.selectors)


class _Opened:
    def __init__(self) -> None:
        self.pairs: list[socket.socket] = []
        self.selectors: list[selectors.BaseSelector] = []


def _record_opened(monkeypatch: pytest.MonkeyPatch) -> _Opened:
    opened = _Opened()
    real_pair, real_selector = socket.socketpair, selectors.DefaultSelector

    def _pair() -> tuple[socket.socket, socket.socket]:
        pair = real_pair()
        opened.pairs.extend(pair)
        return pair

    def _selector() -> selectors.BaseSelector:
        sel = real_selector()
        opened.selectors.append(sel)
        return sel

    monkeypatch.setattr(receive_gate.socket, "socketpair", _pair)
    monkeypatch.setattr(receive_gate.selectors, "DefaultSelector", _selector)
    return opened


def test_a_gate_that_cannot_be_built_releases_what_it_opened(monkeypatch: pytest.MonkeyPatch) -> None:
    """A closed socket cannot be registered; the pair and selector opened before must not leak."""
    opened = _record_opened(monkeypatch)
    closed = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    closed.close()

    with pytest.raises(ValueError):
        ReceiveGate(closed)

    assert [s.fileno() for s in opened.pairs] == [-1, -1]
    assert all(sel.get_map() is None for sel in opened.selectors)
