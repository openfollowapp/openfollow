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

import errno
import socket
import threading
import time
from collections.abc import Callable
from typing import Any

import pypsn
import pytest
from conftest import send_psn_tracker

import openfollow.osc.service as service_module
import openfollow.psn.receiver as receiver_module
from openfollow.osc.service import _PYTHONOSC_AVAILABLE, OscService
from openfollow.psn.receiver import PsnReceiver, _RobustReceiver
from openfollow.receive_gate import ReceiveGate
from tests._ports import bind_free_udp_port, free_udp_port

pytestmark = pytest.mark.integration

# The bound is generous so a scheduler stall cannot flake it. Detection comes
# from the other side: every wait a stop could fall back on (the read, the join,
# the serve poll) is stretched to _SLOW_S, so a stop that does not wake the
# thread overruns the bound by far.
_BOUND_S = 5.0
_SLOW_S = 30.0
_DELIVERY_S = 2.0


@pytest.fixture(autouse=True)
def _slow_fallbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_RobustReceiver, "_JOIN_TIMEOUT_S", _SLOW_S)
    monkeypatch.setattr(service_module, "_LISTENER_JOIN_TIMEOUT_S", _SLOW_S, raising=False)
    if _PYTHONOSC_AVAILABLE:
        serve = service_module._FilteredOSCUDPServer.serve_forever
        monkeypatch.setattr(
            service_module._FilteredOSCUDPServer,
            "serve_forever",
            lambda self, poll_interval=0.5: serve(self, poll_interval=_SLOW_S),
        )


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


@pytest.fixture
def gates(monkeypatch: pytest.MonkeyPatch) -> list[ReceiveGate]:
    """Every gate the receivers open, so a test can check each one is released."""
    made: list[ReceiveGate] = []

    class _Recorded(ReceiveGate):
        def __init__(self, sock: socket.socket) -> None:
            super().__init__(sock)
            made.append(self)

    monkeypatch.setattr(receiver_module, "ReceiveGate", _Recorded)
    monkeypatch.setattr(service_module, "ReceiveGate", _Recorded)
    return made


def _refuse_gate(_sock: socket.socket) -> ReceiveGate:
    raise OSError(errno.EMFILE, "Too many open files")


def _refuse_thread(_thread: threading.Thread) -> None:
    raise RuntimeError("can't start new thread")


class TestPsnReceiver:
    def test_stop_does_not_wait_for_the_read_timeout(self, psn_loopback_sockets: list[socket.socket]) -> None:
        receiver = _running_receiver(psn_loopback_sockets)

        assert _timed(receiver.stop) < _BOUND_S
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
            assert _timed(lambda: receiver.rebind("127.0.0.1")) < _BOUND_S
            assert psn_loopback_sockets[0].fileno() == -1
            send_psn_tracker(psn_loopback_sockets[1].getsockname(), 8)
            assert _wait_for(lambda: receiver.get_marker(8) is not None)
        finally:
            receiver.stop()

    def test_repeated_rebinds_stay_prompt(self, psn_loopback_sockets: list[socket.socket]) -> None:
        receiver = _running_receiver(psn_loopback_sockets)
        try:
            for tracker_id in (11, 12, 13):
                assert _timed(lambda: receiver.rebind("127.0.0.1")) < _BOUND_S
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
            assert _timed(receiver.stop) < _BOUND_S
        assert any("did not stop" in r.message for r in caplog.records)
        assert psn_loopback_sockets[0].fileno() == -1

        release.set()
        thread.join(_DELIVERY_S)
        assert not thread.is_alive()
        assert receiver.get_marker(9) is None

    def test_a_thread_that_could_not_start_releases_its_gate_and_socket(
        self, psn_loopback_sockets: list[socket.socket], gates: list[ReceiveGate], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        receiver = _RobustReceiver(callback=lambda *_: None, ip_addr="127.0.0.1", mcast_port=56565)
        monkeypatch.setattr(threading.Thread, "start", _refuse_thread)
        with pytest.raises(RuntimeError):
            receiver.start()

        assert [gate.closed for gate in gates] == [True]
        assert psn_loopback_sockets[0].fileno() == -1
        receiver.stop()  # joining a thread that never started would raise

    @pytest.mark.parametrize(
        ("target", "refusal"),
        [("gate", _refuse_gate), ("thread", _refuse_thread)],
    )
    def test_a_receiver_that_cannot_start_reads_as_stopped(
        self,
        psn_loopback_sockets: list[socket.socket],
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        target: str,
        refusal: Callable[..., object],
    ) -> None:
        """A full fd or thread table must not leave a receiver that reports
        itself bound while holding an open socket and no thread."""
        if target == "gate":
            monkeypatch.setattr(receiver_module, "ReceiveGate", refusal)
        else:
            monkeypatch.setattr(threading.Thread, "start", refusal)
        receiver = PsnReceiver(source_ip="127.0.0.1")
        with caplog.at_level("ERROR", logger="openfollow.psn.receiver"):
            receiver.start()

        assert receiver.bound_source_ip() is None
        assert psn_loopback_sockets[0].fileno() == -1
        assert any("could not start" in r.message for r in caplog.records)
        with pytest.raises(OSError, match="failed to bind"):
            receiver.rebind("127.0.0.1")

    def test_a_pinned_receiver_hands_its_packets_to_the_callback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.bind(("127.0.0.1", 0))
            monkeypatch.setattr(receiver_module, "_pinned_socket", lambda *_args: sock)
            receiver = PsnReceiver(source_ip="127.0.0.1", source_iface="lo")
            receiver.start()
            try:
                send_psn_tracker(sock.getsockname(), 4)
                assert _wait_for(lambda: receiver.get_marker(4) is not None)
            finally:
                receiver.stop()

    def test_readiness_without_a_datagram_does_not_park_the_thread_in_a_read(
        self, psn_loopback_sockets: list[socket.socket], spurious_readiness: threading.Event
    ) -> None:
        receiver = PsnReceiver(source_ip="127.0.0.1")
        receiver.start()
        assert spurious_readiness.wait(_DELIVERY_S)

        assert _timed(receiver.stop) < _BOUND_S


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

        assert _timed(svc.stop_listener) < _BOUND_S
        assert svc.listener_port is None

    def test_restart_is_prompt_and_the_new_listener_receives(self) -> None:
        svc, port, received = self._running()
        try:
            assert _timed(lambda: svc.restart_listener(port=port, allowed_ips=())) < _BOUND_S
            svc.send("/ping", [2], host="127.0.0.1", port=port)
            assert _wait_for(lambda: len(received) == 2)
        finally:
            svc.shutdown()

    def test_moving_the_membership_is_prompt(self) -> None:
        """The observer's OSC input plane suspends by rebinding with no membership."""
        svc, _port, _received = self._running(multicast_group="239.1.2.3")
        try:
            assert _timed(lambda: svc.set_multicast_iface(None)) < _BOUND_S
        finally:
            svc.shutdown()

    def test_readiness_without_a_datagram_does_not_park_the_thread_in_a_read(
        self, spurious_readiness: threading.Event
    ) -> None:
        svc = OscService()
        bind_free_udp_port(lambda p: svc.start_listener(p, allowed_ips=()))
        assert spurious_readiness.wait(_DELIVERY_S)

        assert _timed(svc.stop_listener) < _BOUND_S

    def test_the_serving_thread_releases_its_gate(self, gates: list[ReceiveGate]) -> None:
        svc, _port, _received = self._running()
        svc.stop_listener()

        assert [gate.closed for gate in gates] == [True]

    def test_a_listener_whose_thread_never_ran_releases_its_gate(
        self, gates: list[ReceiveGate], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        svc = OscService()
        monkeypatch.setattr(threading.Thread, "start", _refuse_thread)
        with pytest.raises(RuntimeError):
            bind_free_udp_port(lambda p: svc.restart_listener(port=p, allowed_ips=()))

        assert [gate.closed for gate in gates] == [True]

    def test_a_stop_that_outlives_its_join_leaves_the_gate_to_the_serving_thread(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A serving thread delayed between checking its gate and waiting on it
        must still find the gate open, or the wait fails instead of ending."""
        svc, _port, _received = self._running()
        listener = svc._listener
        assert listener is not None
        gate = listener._gate
        entered, release = threading.Event(), threading.Event()
        select = gate._selector.select

        def _delayed(timeout: float | None = None) -> object:
            entered.set()
            release.wait(_DELIVERY_S)
            return select(timeout)

        failures: list[BaseException | None] = []
        monkeypatch.setattr(threading, "excepthook", lambda args: failures.append(args.exc_value))
        thread = svc._listener_thread
        assert thread is not None
        gate._selector.select = _delayed  # type: ignore[method-assign]
        svc.send("/ping", [2], host="127.0.0.1", port=_port)
        assert entered.wait(_DELIVERY_S)
        monkeypatch.setattr(service_module, "_LISTENER_JOIN_TIMEOUT_S", 0.05)

        svc.stop_listener()
        release.set()
        thread.join(_DELIVERY_S)

        assert not thread.is_alive()
        assert failures == []
        assert gate.closed

    def test_a_listener_that_cannot_open_its_gate_closes_its_socket_and_says_why(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        svc = OscService()
        port = free_udp_port()
        monkeypatch.setattr(service_module, "ReceiveGate", _refuse_gate)
        with caplog.at_level("ERROR", logger="openfollow.osc.service"), pytest.raises(OSError):
            svc.restart_listener(port=port, allowed_ips=())

        assert not any("port likely in use" in r.message for r in caplog.records)
        assert any("Too many open files" in r.message for r in caplog.records)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.bind(("", port))  # the failed listener let the port go
