# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Stopping or rebinding a running receiver returns at once.

The network observer and the config dispatcher stop and rebind receivers on
the GTK main loop, which also runs the frame clock. A receive thread parked in
a blocking read is not woken by another thread closing its socket, so a stop
that waits for it holds every marker write for the length of the read timeout
and every output reports its markers as stale.

These run real threads on real loopback sockets: the defect is in how a parked
read is woken, which no fake socket reproduces.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Callable
from typing import Any

import pypsn
import pytest
from conftest import send_psn_tracker

from openfollow.osc.service import _PYTHONOSC_AVAILABLE, OscService
from openfollow.psn.receiver import PsnReceiver, _RobustReceiver
from openfollow.receive_gate import ReceiveGate
from tests._ports import bind_free_udp_port

pytestmark = pytest.mark.integration

# Far under PSN's 2 s receive timeout and the OSC listener's 0.5 s poll, and far
# over what waking a parked thread costs, so a slow runner does not flake it.
_PROMPT_S = 0.25
_DELIVERY_S = 2.0


def _wait_for(predicate: Callable[[], bool], timeout: float = _DELIVERY_S) -> bool:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.005)
    return True


def _timed(action: Callable[[], object]) -> float:
    start = time.monotonic()
    action()
    return time.monotonic() - start


def _running_receiver(sockets: list[socket.socket], tracker_id: int = 7) -> PsnReceiver:
    """A started receiver whose thread has delivered a packet and is back in its read."""
    receiver = PsnReceiver(source_ip="127.0.0.1")
    receiver.start()
    send_psn_tracker(sockets[-1].getsockname(), tracker_id)
    assert _wait_for(lambda: receiver.get_marker(tracker_id) is not None)
    return receiver


@pytest.fixture
def spurious_readiness(monkeypatch: pytest.MonkeyPatch) -> threading.Event:
    """Make the next gate wait report the socket readable with nothing to read.

    Linux does this for a datagram it then discards on a bad checksum, so a
    read after readiness must not be able to block. Set once that wait returned.
    """
    real_wait = ReceiveGate.wait
    returned = threading.Event()

    def _wait(gate: ReceiveGate) -> bool:
        if returned.is_set():
            return real_wait(gate)
        returned.set()
        return True

    monkeypatch.setattr(ReceiveGate, "wait", _wait)
    return returned


class TestPsnReceiver:
    def test_stop_does_not_wait_for_the_read_timeout(self, psn_loopback_sockets: list[socket.socket]) -> None:
        receiver = _running_receiver(psn_loopback_sockets)

        assert _timed(receiver.stop) < _PROMPT_S
        assert receiver.bound_source_ip() is None

    def test_stop_closes_the_socket_so_its_membership_is_released(
        self, psn_loopback_sockets: list[socket.socket]
    ) -> None:
        receiver = _running_receiver(psn_loopback_sockets)
        receiver.stop()

        assert psn_loopback_sockets[0].fileno() == -1

    def test_rebind_is_prompt_and_the_new_socket_receives(self, psn_loopback_sockets: list[socket.socket]) -> None:
        receiver = _running_receiver(psn_loopback_sockets)
        try:
            assert _timed(lambda: receiver.rebind("127.0.0.1")) < _PROMPT_S
            assert psn_loopback_sockets[0].fileno() == -1
            send_psn_tracker(psn_loopback_sockets[1].getsockname(), 8)
            assert _wait_for(lambda: receiver.get_marker(8) is not None)
        finally:
            receiver.stop()

    def test_repeated_rebinds_stay_prompt(self, psn_loopback_sockets: list[socket.socket]) -> None:
        receiver = _running_receiver(psn_loopback_sockets)
        try:
            for tracker_id in (11, 12, 13):
                assert _timed(lambda: receiver.rebind("127.0.0.1")) < _PROMPT_S
                send_psn_tracker(psn_loopback_sockets[-1].getsockname(), tracker_id)
                assert _wait_for(lambda tid=tracker_id: receiver.get_marker(tid) is not None)
        finally:
            receiver.stop()

    def test_a_thread_held_mid_packet_is_not_awaited_and_its_packet_is_dropped(
        self,
        psn_loopback_sockets: list[socket.socket],
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Only a hung callback outlasts the wake. The stop still returns on its
        bounded join, and what that thread was holding never lands."""
        receiver = _running_receiver(psn_loopback_sockets)
        entered, release = threading.Event(), threading.Event()
        parse = pypsn.parse_psn_packet

        def _held(data: bytes) -> object:
            entered.set()
            release.wait(_DELIVERY_S)
            return parse(data)

        monkeypatch.setattr(pypsn, "parse_psn_packet", _held)
        monkeypatch.setattr(_RobustReceiver, "_JOIN_TIMEOUT_S", 0.05)
        thread = receiver._receiver
        assert thread is not None
        send_psn_tracker(psn_loopback_sockets[0].getsockname(), 9)
        assert entered.wait(_DELIVERY_S)

        with caplog.at_level("WARNING", logger="openfollow.psn.receiver"):
            assert _timed(receiver.stop) < _PROMPT_S
        assert any("did not stop" in r.message for r in caplog.records)
        assert psn_loopback_sockets[0].fileno() == -1

        release.set()
        thread.join(_DELIVERY_S)
        assert not thread.is_alive()
        assert receiver.get_marker(9) is None

    def test_a_thread_that_could_not_start_still_stops(
        self, psn_loopback_sockets: list[socket.socket], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _refuse(_thread: threading.Thread) -> None:
            raise RuntimeError("can't start new thread")

        receiver = _RobustReceiver(callback=lambda *_: None, ip_addr="127.0.0.1")
        monkeypatch.setattr(threading.Thread, "start", _refuse)
        with pytest.raises(RuntimeError):
            receiver.start()

        receiver.stop()  # joining a thread that never started would raise

        assert psn_loopback_sockets[0].fileno() == -1

    def test_readiness_without_a_datagram_does_not_park_the_thread_in_a_read(
        self, psn_loopback_sockets: list[socket.socket], spurious_readiness: threading.Event
    ) -> None:
        receiver = PsnReceiver(source_ip="127.0.0.1")
        receiver.start()
        assert spurious_readiness.wait(_DELIVERY_S)

        assert _timed(receiver.stop) < _PROMPT_S


@pytest.mark.skipif(not _PYTHONOSC_AVAILABLE, reason="python-osc not installed")
class TestOscListener:
    def _running(self, **kwargs: Any) -> tuple[OscService, int, list[str]]:
        """A listener whose serve loop has dispatched a message and is waiting again."""
        svc = OscService()
        received: list[str] = []
        svc.subscribe("/ping", lambda address, *_args: received.append(address))
        port = bind_free_udp_port(lambda p: svc.start_listener(p, allowed_ips=(), **kwargs))
        svc.send("/ping", [1], host="127.0.0.1", port=port)
        assert _wait_for(lambda: len(received) == 1)
        return svc, port, received

    def test_stop_does_not_wait_for_the_serve_loop_poll(self) -> None:
        svc, _port, _received = self._running()

        assert _timed(svc.stop_listener) < _PROMPT_S
        assert svc.listener_port is None

    def test_restart_is_prompt_and_the_new_listener_receives(self) -> None:
        svc, port, received = self._running()
        try:
            assert _timed(lambda: svc.restart_listener(port=port, allowed_ips=())) < _PROMPT_S
            svc.send("/ping", [2], host="127.0.0.1", port=port)
            assert _wait_for(lambda: len(received) == 2)
        finally:
            svc.shutdown()

    def test_moving_the_membership_is_prompt(self) -> None:
        """The observer's OSC input plane suspends by rebinding with no membership."""
        svc, _port, _received = self._running(multicast_group="239.1.2.3")
        try:
            assert _timed(lambda: svc.set_multicast_iface(None)) < _PROMPT_S
        finally:
            svc.shutdown()

    def test_readiness_without_a_datagram_does_not_park_the_thread_in_a_read(
        self, spurious_readiness: threading.Event
    ) -> None:
        svc = OscService()
        bind_free_udp_port(lambda p: svc.start_listener(p, allowed_ips=()))
        assert spurious_readiness.wait(_DELIVERY_S)

        assert _timed(svc.stop_listener) < _PROMPT_S
