# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Socket + thread-lifecycle coverage for :mod:`openfollow.psn.receiver`.

Covers the gaps left open by the ``_on_packet`` parsing suite:

* ``_RobustReceiver.run`` – the upstream ``pypsn.Receiver.run`` prints
  socket errors to stdout and runs ``parse_psn_packet``/``callback``
  outside the try/except, so bad packets crashed the thread.  Our
  subclass wraps all three failure modes (a readiness with no datagram,
  ``OSError`` before / after stop, generic ``Exception``) plus the
  parse/callback error path.  Drive the loop with a fake socket and gate
  so every branch fires under a bounded iteration count.
* ``PsnReceiver.start`` – constructs the receiver and recovers gracefully
  from an ``OSError`` on socket bind (common in CI where multicast is
  blocked).  Tests both the happy path and the logged-error branch.
* ``PsnReceiver.stop`` – no-op when ``start`` never succeeded.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import Any

import pytest
from conftest import psn_packet_clock as _packet_clock

import openfollow.psn.receiver as receiver_module
from openfollow.psn.receiver import PsnReceiver, _RobustReceiver

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


@dataclass
class _Vec:
    x: float
    y: float
    z: float


@dataclass
class _PacketTracker:
    tracker_id: int
    pos: _Vec | None
    speed: _Vec | None
    timestamp: int = 0
    status: float = 0.0


class _FakeDataPacket:
    def __init__(self, trackers: list[_PacketTracker]) -> None:
        self.trackers = trackers


class _ScriptedSocket:
    """``socket.socket`` stand-in for ``_RobustReceiver.run``.

    ``recvfrom`` pops from ``script``: each entry is either a ``bytes``
    payload (returned as ``(data, ("1.2.3.4", 56565))``) or an
    ``Exception`` instance (raised immediately). When ``script`` is
    empty, ``recvfrom`` raises ``StopIteration`` so the test thread
    terminates deterministically even if the guard logic is broken.
    """

    def __init__(self, script: list[bytes | Exception]) -> None:
        self._script = list(script)
        self.recv_calls = 0

    def recvfrom(self, _size: int) -> tuple[bytes, tuple[str, int]]:
        self.recv_calls += 1
        if not self._script:
            # Deterministic stop – the loop should already have exited
            # on its stopped gate by now.
            raise StopIteration("script exhausted")
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item, ("127.0.0.1", 56565)


class _ScriptedGate:
    """Stands in for ``ReceiveGate``: reports the socket readable until stopped."""

    def __init__(self) -> None:
        self.stopped = False
        self.closed = False

    def wait(self) -> bool:
        return not self.stopped

    def stop(self) -> None:
        self.stopped = True

    def close(self) -> None:
        self.closed = True


def _make_robust_receiver(
    *,
    socket_obj: Any = None,
    callback: Any = None,
) -> _RobustReceiver:
    """Build a ``_RobustReceiver`` without touching the pypsn socket
    constructor.

    ``pypsn.Receiver.__init__`` binds a real multicast socket; bypass it
    via ``object.__new__`` and populate the fields ``run()`` actually
    reads (``socket``, ``_gate``, ``callback``). A test ends the loop by
    stopping the gate.
    """
    instance = object.__new__(_RobustReceiver)
    instance.socket = socket_obj
    instance._gate = _ScriptedGate() if socket_obj is not None else None
    instance.callback = callback or (lambda _data, _sender: None)
    instance._own_source = lambda: None
    return instance


# --------------------------------------------------------------------------- #
# _RobustReceiver.run – socket error paths
# --------------------------------------------------------------------------- #


class TestRobustReceiverSocketNone:
    def test_no_socket_returns_immediately(self) -> None:
        """Bypass for the early-abort state where the pypsn constructor
        failed before binding but ``run`` was still scheduled.
        """
        r = _make_robust_receiver(socket_obj=None)
        # Returns without accessing self.callback – no assertion needed
        # beyond not raising.
        r.run()

    def test_no_socket_starts_no_thread_and_stops_cleanly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """pypsn's ``get_socket`` returns None on a platform it cannot join on."""
        monkeypatch.setattr(receiver_module.pypsn, "get_socket", lambda *_args: None)
        r = _RobustReceiver(callback=lambda *_: None, ip_addr="10.0.0.5", mcast_port=56565)
        r.start()
        assert r.ident is None
        r.stop()  # joining a thread that never started would raise


class TestRobustReceiverReadinessWithoutData:
    def test_an_empty_read_does_not_break_the_loop(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls: list[Any] = []
        monkeypatch.setattr(receiver_module.pypsn, "parse_psn_packet", lambda data: data)

        sock = _ScriptedSocket(
            [
                BlockingIOError("no datagram after all"),
                b"\x00\x01\x02",  # real payload on the second attempt
            ]
        )
        r = _make_robust_receiver(
            socket_obj=sock,
            callback=lambda data, _sender: (calls.append(data), r._gate.stop())[0],
        )
        r.run()
        assert sock.recv_calls == 2
        assert calls == [b"\x00\x01\x02"]

    def test_datagram_source_address_reaches_the_handler(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The speed handling has to tell two stations publishing the same
        tracker id apart, and the source address is the only thing on a PSN
        datagram that identifies its sender."""
        senders: list[str] = []
        monkeypatch.setattr(receiver_module.pypsn, "parse_psn_packet", lambda data: data)

        sock = _ScriptedSocket([b"\x00"])
        r = _make_robust_receiver(
            socket_obj=sock,
            callback=lambda _data, sender: (senders.append(sender), r._gate.stop())[0],
        )
        r.run()
        assert senders == ["127.0.0.1"]


class TestRobustReceiverOSError:
    def test_oserror_while_running_continues_with_debug_log(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Transient OSError (e.g. network interface flap) – debug-log
        and keep looping rather than kill the receive thread.
        """
        monkeypatch.setattr(receiver_module.pypsn, "parse_psn_packet", lambda data: data)
        sock = _ScriptedSocket(
            [
                OSError(4, "Interrupted system call"),
                b"\x00",
            ]
        )
        calls: list[Any] = []
        r = _make_robust_receiver(socket_obj=sock)
        r.callback = lambda data, _sender: (calls.append(data), r._gate.stop())[0]

        with caplog.at_level("DEBUG", logger="openfollow.psn.receiver"):
            r.run()

        assert calls == [b"\x00"]
        assert any("PSN recv socket error" in rec.message for rec in caplog.records if rec.levelname == "DEBUG")

    def test_a_read_failing_after_stop_ends_the_loop_and_closes_the_gate(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``stop()`` closes the socket, so the read in flight fails; the loop
        must end on the stopped gate rather than spin on a closed fd."""
        monkeypatch.setattr(receiver_module.pypsn, "parse_psn_packet", lambda data: data)

        class _ClosedUnderRead:
            calls = 0

            def recvfrom(self, _n: int) -> Any:
                self.calls += 1
                r._gate.stop()
                raise OSError(9, "Bad file descriptor")

        sock = _ClosedUnderRead()
        r = _make_robust_receiver(socket_obj=sock)
        gate = r._gate
        r.run()
        assert sock.calls == 1
        assert gate.closed is True


class TestRobustReceiverUnexpectedException:
    def test_unknown_error_is_caught_and_loop_continues(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Socket subclasses can raise non-OSError subclasses on some
        platforms. The blanket ``except Exception`` keeps the thread
        alive across those too.
        """
        monkeypatch.setattr(receiver_module.pypsn, "parse_psn_packet", lambda data: data)
        sock = _ScriptedSocket(
            [
                RuntimeError("platform-specific error"),
                b"\x42",
            ]
        )
        calls: list[Any] = []
        r = _make_robust_receiver(socket_obj=sock)
        r.callback = lambda data, _sender: (calls.append(data), r._gate.stop())[0]

        with caplog.at_level("DEBUG", logger="openfollow.psn.receiver"):
            r.run()

        assert calls == [b"\x42"]
        assert any("PSN recv unexpected error" in rec.message for rec in caplog.records)


class TestRobustReceiverParseAndCallbackErrors:
    def test_parse_error_is_swallowed_and_loop_continues(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        callback_calls: list[Any] = []

        def _record(data: Any, _sender: str) -> None:
            callback_calls.append(data)

        sock = _ScriptedSocket([b"\x00\x00", b"\x00\x01"])
        r = _make_robust_receiver(socket_obj=sock, callback=_record)
        # Exit after two bad-parse iterations.
        iters = {"n": 0}

        def _stop_after_two(_data: bytes) -> Any:
            iters["n"] += 1
            if iters["n"] >= 2:
                r._gate.stop()
            raise ValueError("bad header")

        monkeypatch.setattr(receiver_module.pypsn, "parse_psn_packet", _stop_after_two)

        with caplog.at_level("DEBUG", logger="openfollow.psn.receiver"):
            r.run()

        assert callback_calls == []  # callback never invoked on parse failure
        assert any("parse/callback error" in rec.message for rec in caplog.records)

    def test_callback_exception_does_not_kill_thread(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(receiver_module.pypsn, "parse_psn_packet", lambda data: data)
        call_count = {"n": 0}

        def _flaky(data: Any, _sender: str) -> None:
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("downstream exploded")
            # Second call signals success and exits the loop.
            r._gate.stop()

        sock = _ScriptedSocket([b"\x00", b"\x01"])
        r = _make_robust_receiver(socket_obj=sock, callback=_flaky)

        with caplog.at_level("DEBUG", logger="openfollow.psn.receiver"):
            r.run()

        assert call_count["n"] == 2  # survived past the raise
        assert any("parse/callback error" in rec.message for rec in caplog.records)


# --------------------------------------------------------------------------- #
# PsnReceiver.start / stop
# --------------------------------------------------------------------------- #


class _FakeRecvThread:
    """Stand-in for ``_RobustReceiver`` used by PsnReceiver.start tests."""

    construct_raises: OSError | None = None
    instances: list[_FakeRecvThread] = []

    def __init__(
        self, *, callback: Any, ip_addr: str, mcast_port: int, iface: str = "", own_source: Any = None
    ) -> None:
        if _FakeRecvThread.construct_raises is not None:
            raise _FakeRecvThread.construct_raises
        self.callback = callback
        self.ip_addr = ip_addr
        self.mcast_port = mcast_port
        self.iface = iface
        self.daemon = False
        self.started = False
        self.stopped = False
        _FakeRecvThread.instances.append(self)

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True


@pytest.fixture(autouse=True)
def _reset_fake_recv() -> None:
    _FakeRecvThread.construct_raises = None
    _FakeRecvThread.instances = []


class TestPsnReceiverStart:
    def test_happy_path_constructs_and_starts_daemon(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(receiver_module, "_RobustReceiver", _FakeRecvThread)
        recv = PsnReceiver(port=56565, ignore_ids=[1, 2], source_ip="10.0.0.5")
        recv.start()

        assert len(_FakeRecvThread.instances) == 1
        fake = _FakeRecvThread.instances[0]
        assert fake.ip_addr == "10.0.0.5"
        assert fake.mcast_port == 56565
        assert fake.daemon is True
        assert fake.started is True
        assert recv._receiver is fake

    def test_empty_source_ip_falls_back_to_0_0_0_0(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Config default is the empty string ("listen on all
        interfaces") – the receiver must translate that to the literal
        ``0.0.0.0`` that the pypsn socket-binder expects.
        """
        monkeypatch.setattr(receiver_module, "_RobustReceiver", _FakeRecvThread)
        recv = PsnReceiver(source_ip="")
        recv.start()
        assert _FakeRecvThread.instances[0].ip_addr == "0.0.0.0"

    def test_oserror_on_construct_is_logged_and_leaves_receiver_none(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        _FakeRecvThread.construct_raises = OSError(99, "Cannot assign requested address")
        monkeypatch.setattr(receiver_module, "_RobustReceiver", _FakeRecvThread)
        recv = PsnReceiver(source_ip="10.99.99.99")

        with caplog.at_level("ERROR", logger="openfollow.psn.receiver"):
            recv.start()

        assert recv._receiver is None
        assert any("PSN receiver socket failed" in rec.message for rec in caplog.records if rec.levelname == "ERROR")


class TestPsnReceiverEnabled:
    """The PSN input feature: a receiver that is not enabled never binds, on any path."""

    def test_a_disabled_receiver_never_binds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(receiver_module, "_RobustReceiver", _FakeRecvThread)
        recv = PsnReceiver(source_ip="192.0.2.5", enabled=False)
        recv.start()
        recv.rebind("192.0.2.6", source_iface="eth1")
        assert _FakeRecvThread.instances == []
        assert recv.bound_source_ip() is None
        assert (recv._source_ip, recv._source_iface) == ("192.0.2.6", "eth1")

    def test_disabling_stops_it_and_forgets_every_marker(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(receiver_module, "_RobustReceiver", _FakeRecvThread)
        recv = PsnReceiver(source_ip="192.0.2.5")
        recv.start()
        recv._markers[7] = object()  # type: ignore[assignment]
        recv._last_seen[7] = 1.0
        recv.set_enabled(False)
        assert _FakeRecvThread.instances[0].stopped is True
        assert recv.bound_source_ip() is None
        assert recv.get_marker(7) is None
        assert recv.is_marker_online(7) is False
        assert recv.enabled is False

    def test_enabling_again_lets_a_rebind_bind(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(receiver_module, "_RobustReceiver", _FakeRecvThread)
        recv = PsnReceiver(source_ip="192.0.2.5", enabled=False)
        recv.set_enabled(True)
        recv.rebind("192.0.2.5")
        assert recv.bound_source_ip() == "192.0.2.5"


class _RecordingRecvSocket:
    """A receive socket that records what a pinned ``_RobustReceiver`` does to it."""

    def __init__(self, *args: Any) -> None:
        self.args = args
        self.options: list[tuple[int, int, int]] = []
        self.bound: tuple[str, int] | None = None
        self.timeout: float | None = None
        self.closed = False

    def setsockopt(self, level: int, option: int, value: int) -> None:
        self.options.append((level, option, value))

    def bind(self, address: tuple[str, int]) -> None:
        self.bound = address

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def close(self) -> None:
        self.closed = True


class TestPinnedReceiveSocket:
    """pypsn joins its group by address; a pinned receiver joins it by name, so a
    VLAN child holding the same link-local address cannot take the membership."""

    @pytest.fixture
    def sockets(self, monkeypatch: pytest.MonkeyPatch) -> list[_RecordingRecvSocket]:
        import socket as real_socket
        import types

        made: list[_RecordingRecvSocket] = []

        def _make(*args: Any) -> _RecordingRecvSocket:
            made.append(_RecordingRecvSocket(*args))
            return made[-1]

        fake = types.SimpleNamespace(**{k: getattr(real_socket, k) for k in dir(real_socket) if k.isupper()})
        fake.socket = _make
        monkeypatch.setattr(receiver_module, "socket", fake)
        return made

    def test_a_pinned_receiver_joins_the_group_by_name(
        self, monkeypatch: pytest.MonkeyPatch, sockets: list[_RecordingRecvSocket]
    ) -> None:
        joins: list[tuple[Any, str, str, str]] = []
        monkeypatch.setattr(receiver_module, "join_multicast_group_on_iface", lambda *args: joins.append(args))
        receiver = _RobustReceiver(callback=lambda *_: None, ip_addr="169.254.7.7", mcast_port=56565, iface="eth1")
        (sock,) = sockets
        assert receiver.socket is sock
        assert sock.args == (socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        # Every station on the group shares the PSN port.
        assert sock.options == [(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)]
        assert sock.bound == ("236.10.10.10", 56565)
        assert joins == [(sock, "236.10.10.10", "169.254.7.7", "eth1")]

    def test_a_refused_join_closes_the_socket_and_raises(
        self, monkeypatch: pytest.MonkeyPatch, sockets: list[_RecordingRecvSocket]
    ) -> None:
        from openfollow.net_utils import InterfaceUnavailable

        def _refuse(*_args: Any) -> None:
            raise InterfaceUnavailable("eth1 is not present")

        monkeypatch.setattr(receiver_module, "join_multicast_group_on_iface", _refuse)
        with pytest.raises(InterfaceUnavailable):
            _RobustReceiver(callback=lambda *_: None, ip_addr="169.254.7.7", mcast_port=56565, iface="eth1")
        assert sockets[0].closed is True

    def test_an_unpinned_receiver_keeps_pypsns_socket(
        self, monkeypatch: pytest.MonkeyPatch, sockets: list[_RecordingRecvSocket]
    ) -> None:
        import pypsn

        opened: list[tuple[str, int]] = []
        theirs = _RecordingRecvSocket()

        def _get_socket(ip_addr: str, mcast_port: int) -> _RecordingRecvSocket:
            opened.append((ip_addr, mcast_port))
            return theirs

        monkeypatch.setattr(pypsn, "get_socket", _get_socket)
        receiver = _RobustReceiver(callback=lambda *_: None, ip_addr="10.0.0.5", mcast_port=56999)
        assert opened == [("10.0.0.5", 56999)]
        assert receiver.socket is theirs
        assert sockets == []

    def test_the_receiver_hands_its_interface_to_the_receive_thread(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(receiver_module, "_RobustReceiver", _FakeRecvThread)
        recv = PsnReceiver(source_ip="10.0.0.5", source_iface="eth1")
        recv.start()
        recv.rebind("10.0.1.5", source_iface="eth2")
        recv.rebind("10.0.1.6")
        assert [fake.iface for fake in _FakeRecvThread.instances] == ["eth1", "eth2", "eth2"]


class TestPsnReceiverStop:
    def test_stop_with_no_receiver_is_noop(self) -> None:
        recv = PsnReceiver()
        recv.stop()  # must not raise

    def test_stop_calls_receiver_stop_and_clears_reference(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(receiver_module, "_RobustReceiver", _FakeRecvThread)
        recv = PsnReceiver()
        recv.start()
        fake = _FakeRecvThread.instances[0]
        recv.stop()
        assert fake.stopped is True
        assert recv._receiver is None


class TestPsnReceiverRebind:
    def test_rebind_recreates_receiver_with_new_source_ip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Rebind swaps socket interface binding with new _RobustReceiver.
        IGMP membership lives on fd, so fresh socket required. Marker state survives."""
        monkeypatch.setattr(receiver_module, "_RobustReceiver", _FakeRecvThread)
        recv = PsnReceiver(port=56565, source_ip="10.0.0.5", ignore_ids=[1])
        recv._last_seen[42] = 99.0  # arbitrary marker-state we expect to survive

        recv.start()
        first = _FakeRecvThread.instances[0]
        assert first.ip_addr == "10.0.0.5"

        recv.rebind("192.168.1.5")

        # Old receiver was stopped, new one constructed with the new IP.
        assert first.stopped is True
        assert len(_FakeRecvThread.instances) == 2
        second = _FakeRecvThread.instances[1]
        assert second.ip_addr == "192.168.1.5"
        assert second.mcast_port == 56565
        assert second.started is True
        assert recv._receiver is second
        # Marker-state survives the rebind – the dict is not touched.
        assert recv._last_seen[42] == 99.0
        # ``_source_ip`` reflects the new value so a subsequent rebind to
        # the same IP would still be a real change against the prior cfg.
        assert recv._source_ip == "192.168.1.5"


class TestStoppedGenerationIsDropped:
    """A receive thread that outlives its stop must not land what it read."""

    def _packet(self, tracker_id: int) -> _FakeDataPacket:
        return _FakeDataPacket([_PacketTracker(tracker_id, _Vec(1.0, 2.0, 3.0), _Vec(0.0, 0.0, 0.0))])

    def test_a_packet_from_a_stopped_thread_is_dropped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(receiver_module, "_RobustReceiver", _FakeRecvThread)
        monkeypatch.setattr(receiver_module.pypsn, "PsnDataPacket", _FakeDataPacket)
        recv = PsnReceiver()
        recv.start()
        late = _FakeRecvThread.instances[0].callback
        recv.stop()

        late(self._packet(5), "10.0.0.1")

        assert recv.get_marker(5) is None

    def test_after_a_rebind_only_the_new_thread_is_heard(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(receiver_module, "_RobustReceiver", _FakeRecvThread)
        monkeypatch.setattr(receiver_module.pypsn, "PsnDataPacket", _FakeDataPacket)
        recv = PsnReceiver(source_ip="10.0.0.5")
        recv.start()
        recv.rebind("10.0.0.6")
        old, new = (fake.callback for fake in _FakeRecvThread.instances)

        old(self._packet(5), "10.0.0.1")
        new(self._packet(6), "10.0.0.1")

        assert recv.get_marker(5) is None
        assert recv.get_marker(6) is not None


# --------------------------------------------------------------------------- #
# set_ignore_ids – runtime mutation honoured by the next _on_packet
# --------------------------------------------------------------------------- #


class TestOnPacketEdgeCases:
    """Residual ``_on_packet`` branches not hit by the integration suite."""

    def test_non_data_packet_returns_without_touching_markers(self, monkeypatch: pytest.MonkeyPatch) -> None:

        class _InfoPacket:
            pass

        monkeypatch.setattr(receiver_module.pypsn, "PsnDataPacket", _FakeDataPacket)
        recv = PsnReceiver()
        recv._on_packet(_InfoPacket())
        assert recv.get_marker(0) is None

    def test_dt_outside_window_skips_speed_derivation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(receiver_module.pypsn, "PsnDataPacket", _FakeDataPacket)
        # First packet at t=1, second at t=3 → dt=2.0, outside window.
        monkeypatch.setattr(receiver_module.time, "monotonic", _packet_clock(1.0, 3.0))
        recv = PsnReceiver()
        recv._on_packet(
            _FakeDataPacket(
                [
                    _PacketTracker(3, _Vec(0.0, 0.0, 0.0), _Vec(0.0, 0.0, 0.0)),
                ]
            )
        )
        # Manually prime a known speed so we can verify it wasn't
        # overwritten by the out-of-window branch.
        recv._markers[3].set_speed(7.0, 0.0, 0.0)
        recv._on_packet(
            _FakeDataPacket(
                [
                    _PacketTracker(3, _Vec(5.0, 0.0, 0.0), _Vec(0.0, 0.0, 0.0)),
                ]
            )
        )
        assert recv.get_marker(3).speed == pytest.approx((7.0, 0.0, 0.0))

    def test_identical_position_skips_speed_update(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When a marker reports the same position as last frame,
        ``dx == dy == dz == 0`` – the speed-update block is skipped so
        the last known non-zero speed stays visible on the HUD instead
        of snapping to zero between movement bursts.
        """
        monkeypatch.setattr(receiver_module.pypsn, "PsnDataPacket", _FakeDataPacket)
        monkeypatch.setattr(receiver_module.time, "monotonic", _packet_clock(1.0, 1.1))  # dt=0.1, inside the window
        recv = PsnReceiver()
        recv._on_packet(
            _FakeDataPacket(
                [
                    _PacketTracker(4, _Vec(1.0, 0.0, 0.0), _Vec(0.0, 0.0, 0.0)),
                ]
            )
        )
        recv._markers[4].set_speed(5.0, 0.0, 0.0)
        recv._on_packet(
            _FakeDataPacket(
                [
                    _PacketTracker(4, _Vec(1.0, 0.0, 0.0), _Vec(0.0, 0.0, 0.0)),
                ]
            )
        )
        # Identical position → speed unchanged from the manually seeded
        # (5.0, 0.0, 0.0).
        assert recv.get_marker(4).speed == pytest.approx((5.0, 0.0, 0.0))

    def test_outer_exception_handler_catches_marker_iter_failure(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:

        class _ExplodingMarkers:
            trackers = property(lambda self: (_ for _ in ()).throw(RuntimeError("bad iter")))

        # Patch isinstance check so our exploder passes the gate.
        fake_packet = _ExplodingMarkers()
        monkeypatch.setattr(receiver_module.pypsn, "PsnDataPacket", _ExplodingMarkers)
        recv = PsnReceiver()
        with caplog.at_level("DEBUG", logger="openfollow.psn.receiver"):
            recv._on_packet(fake_packet)
        assert any("Malformed PSN packet ignored" in rec.message for rec in caplog.records)


class TestSetIgnoreIds:
    def test_runtime_update_takes_effect_immediately(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(receiver_module.pypsn, "PsnDataPacket", _FakeDataPacket)
        monkeypatch.setattr(receiver_module.time, "monotonic", lambda: 10.0)
        recv = PsnReceiver(ignore_ids=[])
        recv._on_packet(
            _FakeDataPacket(
                [
                    _PacketTracker(7, _Vec(1.0, 2.0, 3.0), _Vec(0.0, 0.0, 0.0)),
                ]
            )
        )
        assert recv.get_marker(7) is not None

        recv.set_ignore_ids({7})
        recv._on_packet(
            _FakeDataPacket(
                [
                    _PacketTracker(7, _Vec(9.0, 9.0, 9.0), _Vec(0.0, 0.0, 0.0)),
                ]
            )
        )
        # Position from the second packet must not have applied.
        marker = recv.get_marker(7)
        assert marker is not None
        assert marker.pos == pytest.approx((1.0, 2.0, 3.0))


# --------------------------------------------------------------------------- #
# _RobustReceiver – this station's own output
# --------------------------------------------------------------------------- #


class _AddressedSocket:
    """``recvfrom`` returns each scripted ``(data, (address, port))`` in turn."""

    def __init__(self, script: list[tuple[bytes, tuple[str, int]]]) -> None:
        self._script = list(script)

    def recvfrom(self, _size: int) -> tuple[bytes, tuple[str, int]]:
        return self._script.pop(0)

    def settimeout(self, _timeout: float) -> None:
        pass


_OWN = ("192.0.2.10", 50000)
_PEER = ("198.51.100.1", 56565)


class TestOwnOutputIsNotDecoded:
    def _run(
        self,
        monkeypatch: pytest.MonkeyPatch,
        script: list[tuple[bytes, tuple[str, int]]],
        own: Any,
        on_packet: Any = None,
    ) -> list[bytes]:
        """Feed ``script`` then a last packet from a peer; return what was decoded."""
        decoded: list[bytes] = []
        monkeypatch.setattr(receiver_module.pypsn, "parse_psn_packet", lambda data: decoded.append(data) or data)

        def callback(data: bytes, _sender: str) -> None:
            if on_packet is not None:
                on_packet(data)
            if data == b"last":
                r._gate.stop()

        r = _make_robust_receiver(socket_obj=_AddressedSocket([*script, (b"last", _PEER)]), callback=callback)
        r._own_source = own
        r.run()
        return decoded

    @pytest.mark.parametrize(
        ("sender", "is_decoded"),
        [(_OWN, False), (("192.0.2.10", 50001), True), (("192.0.2.11", 50000), True)],
        ids=["own-address-and-port", "own-address-other-port", "other-address-own-port"],
    )
    def test_only_this_station_s_own_packets_are_dropped(
        self, monkeypatch: pytest.MonkeyPatch, sender: tuple[str, int], is_decoded: bool
    ) -> None:
        decoded = self._run(monkeypatch, [(b"probe", sender)], lambda: _OWN)
        assert decoded == ([b"probe", b"last"] if is_decoded else [b"last"])

    def test_without_an_own_output_every_packet_is_decoded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert self._run(monkeypatch, [(b"probe", _OWN)], lambda: None) == [b"probe", b"last"]

    def test_a_rebuilt_output_socket_is_followed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        new = ("192.0.2.10", 50002)
        current = {"own": _OWN}

        def rebuild(data: bytes) -> None:
            if data == b"rebuild":
                current["own"] = new

        script = [(b"old-before", _OWN), (b"rebuild", _PEER), (b"old-after", _OWN), (b"new", new)]
        decoded = self._run(monkeypatch, script, lambda: current["own"], rebuild)
        assert decoded == [b"rebuild", b"old-after", b"last"]


class _ReadsThenStops(_ScriptedGate):
    """Reports the socket readable ``reads`` times, then stops."""

    def __init__(self, reads: int) -> None:
        super().__init__()
        self._reads = reads

    def wait(self) -> bool:
        self._reads -= 1
        return self._reads >= 0


class TestPsnReceiverHandsOwnSourceToItsReceiveThread:
    @pytest.mark.parametrize("iface", ["", "eth1"], ids=["unpinned", "pinned"])
    def test_its_station_s_own_packets_are_dropped(self, monkeypatch: pytest.MonkeyPatch, iface: str) -> None:
        sock = _AddressedSocket([(b"own", _OWN), (b"peer", _PEER)])
        monkeypatch.setattr(receiver_module.pypsn, "get_socket", lambda *_args: sock)
        monkeypatch.setattr(receiver_module, "_pinned_socket", lambda *_args: sock)
        started: list[_RobustReceiver] = []
        monkeypatch.setattr(_RobustReceiver, "start", lambda self: started.append(self))
        decoded: list[bytes] = []
        monkeypatch.setattr(receiver_module.pypsn, "parse_psn_packet", lambda data: decoded.append(data) or data)

        PsnReceiver(source_ip="10.0.0.5", source_iface=iface, own_source=lambda: _OWN).start()
        (thread,) = started
        thread._gate = _ReadsThenStops(2)
        thread.run()
        assert decoded == [b"peer"]
