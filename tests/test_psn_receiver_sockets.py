# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Socket + thread-lifecycle coverage for :mod:`openfollow.psn.receiver`.

Covers the gaps left open by the ``_on_packet`` parsing suite:

* ``_RobustReceiver.run`` – the upstream ``pypsn.Receiver.run`` prints
  socket errors to stdout and runs ``parse_psn_packet``/``callback``
  outside the try/except, so bad packets crashed the thread.  Our
  subclass wraps all three failure modes (``socket.timeout``,
  ``OSError`` with / without ``running``, generic ``Exception``) plus
  the parse/callback error path.  Drive the loop with a fake socket so
  every branch fires under a bounded iteration count.
* ``PsnReceiver.start`` – constructs the receiver and recovers gracefully
  from an ``OSError`` on socket bind (common in CI where multicast is
  blocked).  Tests both the happy path and the logged-error branch.
* ``PsnReceiver.stop`` – no-op when ``start`` never succeeded.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

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
            # via ``self.running = False`` by now.
            raise StopIteration("script exhausted")
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item, ("127.0.0.1", 56565)


def _make_robust_receiver(
    *,
    socket_obj: Any = None,
    callback: Any = None,
) -> _RobustReceiver:
    """Build a ``_RobustReceiver`` without touching the pypsn socket
    constructor.

    ``pypsn.Receiver.__init__`` binds a real multicast socket; bypass it
    via ``object.__new__`` and populate the fields ``run()`` actually
    reads (``socket``, ``running``, ``callback``).
    """
    instance = object.__new__(_RobustReceiver)
    instance.socket = socket_obj
    instance.running = True
    instance.callback = callback or (lambda _data: None)
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


class TestRobustReceiverTimeoutContinues:
    def test_timeout_does_not_break_the_loop(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls: list[Any] = []
        monkeypatch.setattr(receiver_module.pypsn, "parse_psn_packet", lambda data: data)

        sock = _ScriptedSocket(
            [
                TimeoutError("no data"),
                b"\x00\x01\x02",  # real payload on the second attempt
            ]
        )
        r = _make_robust_receiver(
            socket_obj=sock,
            callback=lambda data: (calls.append(data), setattr(r, "running", False))[0],
        )
        r.run()
        assert sock.recv_calls == 2
        assert calls == [b"\x00\x01\x02"]


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
        r.callback = lambda data: (calls.append(data), setattr(r, "running", False))[0]

        with caplog.at_level("DEBUG", logger="openfollow.psn.receiver"):
            r.run()

        assert calls == [b"\x00"]
        assert any("PSN recv socket error" in rec.message for rec in caplog.records if rec.levelname == "DEBUG")

    def test_oserror_after_stop_breaks_the_loop(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When ``stop()`` closes the socket, the next ``recvfrom``
        raises OSError.  Inside the handler ``if not self.running: break``
        – otherwise the loop would spin on a closed fd burning CPU.
        Simulate the race where ``stop()`` flips ``running`` between
        ``recvfrom`` and the handler.
        """
        monkeypatch.setattr(receiver_module.pypsn, "parse_psn_packet", lambda data: data)

        # First recvfrom raises OSError.  ``running`` is still True when
        # the ``while`` condition is evaluated, so the loop body runs
        # once; the socket then flips ``running`` to False on the way
        # into OSError so the handler hits the break path at line 33.
        raise_then_stop_sock = type(
            "_StopOnEnter",
            (),
            {
                "calls": 0,
                "recvfrom": lambda self, _n: (
                    setattr(r, "running", False),
                    (_ for _ in ()).throw(OSError(9, "Bad file descriptor")),
                )[-1],
            },
        )()
        r = _make_robust_receiver(socket_obj=raise_then_stop_sock)
        r.run()  # break path – must terminate without looping


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
        r.callback = lambda data: (calls.append(data), setattr(r, "running", False))[0]

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

        def _record(data: Any) -> None:
            callback_calls.append(data)

        sock = _ScriptedSocket([b"\x00\x00", b"\x00\x01"])
        r = _make_robust_receiver(socket_obj=sock, callback=_record)
        # Exit after two bad-parse iterations.
        iters = {"n": 0}

        def _stop_after_two(_data: bytes) -> Any:
            iters["n"] += 1
            if iters["n"] >= 2:
                r.running = False
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

        def _flaky(data: Any) -> None:
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("downstream exploded")
            # Second call signals success and exits the loop.
            r.running = False

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

    def __init__(self, *, callback: Any, ip_addr: str, mcast_port: int) -> None:
        if _FakeRecvThread.construct_raises is not None:
            raise _FakeRecvThread.construct_raises
        self.callback = callback
        self.ip_addr = ip_addr
        self.mcast_port = mcast_port
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
        times = iter([1.0, 3.0])
        monkeypatch.setattr(receiver_module.time, "monotonic", lambda: next(times))
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
        times = iter([1.0, 1.1])  # dt=0.1, inside the window
        monkeypatch.setattr(receiver_module.time, "monotonic", lambda: next(times))
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
