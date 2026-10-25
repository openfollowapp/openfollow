# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Socket-lifecycle + multicast-retry coverage for :mod:`openfollow.psn.server`.

Closes the gaps left by the packet-content integration suite:

* ``_try_open_multicast_socket_once`` – success path (``source_ip``
  honoured, empty source_ip leaves ``iface_ip`` unset) and exception
  path (warning logged, return False).
* ``_retry_multicast_socket_background`` – retries on a backoff that
  doubles to a minute until success, early-exit on ``stop_event.set()``.
* ``_recover_multicast_socket_background`` – loops indefinitely until
  success (the reopen logged at DEBUG, never as a recovery), early-exits
  on ``stop_event.set()``.
* ``start`` – opens multicast sync-success path AND sync-failure →
  spawns the retry thread, or raises before any thread starts when a live
  apply asks it not to retry.  Also the unicast path when ``mcast_ip`` is
  falsy.
* ``stop`` – joins background threads with bounded timeouts; logs
  WARNING on any thread that doesn't stop in time.
* ``_send`` – one log line per failure streak however many sends it
  fails, and the "broken stack close raises" defensive branch in
  ``_handle_send_error``.
"""

from __future__ import annotations

import contextlib
import errno
import gc
import logging
import socket
import threading
import weakref
from typing import Any

import pytest

import openfollow.psn.server as server_module
from openfollow.psn.server import PsnServer

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


class _FakeMcastSocket:
    """Stand-in for ``multicast_expert.McastTxSocket``.

    Records construction kwargs + ``sendto`` calls and can be scripted to
    fail on specific attempts. Implements the context-manager protocol so
    ``ExitStack.enter_context`` is happy.
    """

    instances: list[_FakeMcastSocket] = []
    script: list[Exception | None] = []
    sendto_raises: OSError | None = None

    def __init__(self, _family: int, **kwargs: Any) -> None:
        # Pop the next scripted failure, if any.
        if _FakeMcastSocket.script:
            failure = _FakeMcastSocket.script.pop(0)
            if failure is not None:
                raise failure
        self.kwargs = kwargs
        self.sendto_calls: list[tuple[bytes, tuple[str, int]]] = []
        self.entered = False
        self.closed = False
        _FakeMcastSocket.instances.append(self)

    def sendto(self, data: bytes, dest: tuple[str, int]) -> None:
        self.sendto_calls.append((data, dest))
        if _FakeMcastSocket.sendto_raises is not None:
            raise _FakeMcastSocket.sendto_raises

    def __enter__(self) -> _FakeMcastSocket:
        self.entered = True
        return self

    def __exit__(self, *exc: object) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _reset_fake_mcast() -> None:
    _FakeMcastSocket.instances = []
    _FakeMcastSocket.script = []
    _FakeMcastSocket.sendto_raises = None


@pytest.fixture(autouse=True)
def _patch_mcast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server_module.multicast_expert, "McastTxSocket", _FakeMcastSocket)


# --------------------------------------------------------------------------- #
# _try_open_multicast_socket_once
# --------------------------------------------------------------------------- #


class TestTryOpenMulticastSocketOnce:
    def test_success_wires_socket_and_honours_source_ip(self) -> None:
        """Non-empty ``source_ip`` must flow through to
        ``multicast_expert`` as ``iface_ip`` so the operator's interface
        choice pins the egress NIC on multi-homed hosts.
        """
        srv = PsnServer(source_ip="10.0.0.5", mcast_ip="236.10.10.10")
        # Fresh exit stack – mimic the state after ``start`` started it.
        srv._exit_stack = contextlib.ExitStack()
        ok = srv._try_open_multicast_socket_once(attempt=1)
        assert ok is True
        assert isinstance(srv._socket, _FakeMcastSocket)
        assert srv._socket.kwargs["iface_ip"] == "10.0.0.5"
        assert srv._socket.kwargs["mcast_ips"] == ["236.10.10.10"]
        assert srv._socket.kwargs["enable_external_loopback"] is True

    def test_a_named_interface_is_selected_by_name_not_address(self) -> None:
        """By name, ``multicast_expert`` selects the interface by index. Given
        the address alone it looks up whichever interface holds it at open
        time, which after a replug can be a VLAN child holding the same
        link-local address."""
        srv = PsnServer(source_ip="169.254.7.7", source_iface="eth1", mcast_ip="236.10.10.10")
        srv._exit_stack = contextlib.ExitStack()
        assert srv._try_open_multicast_socket_once(attempt=1) is True
        assert isinstance(srv._socket, _FakeMcastSocket)
        assert srv._socket.kwargs["iface"] == "eth1"
        assert "iface_ip" not in srv._socket.kwargs

    def test_rebind_takes_the_new_interface_and_keeps_it_when_not_given(self) -> None:
        srv = PsnServer(source_ip="10.0.0.5", source_iface="eth0", mcast_ip="236.10.10.10")
        srv.rebind("10.0.1.5", source_iface="eth1")
        try:
            assert _FakeMcastSocket.instances[-1].kwargs["iface"] == "eth1"
            srv.rebind_mcast_ip("236.10.10.11")
            assert _FakeMcastSocket.instances[-1].kwargs["iface"] == "eth1"
        finally:
            srv.stop()

    def test_empty_source_ip_resolves_to_primary_interface(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``Auto`` (empty ``source_ip``) resolves to the primary
        outbound IPv4 so multicast TX pins to a real interface
        instead of relying on the OS default route. The latter
        often doesn't match the LAN the PSN console listens on,
        silently dropping all PSN data."""
        from openfollow import net_utils

        monkeypatch.setattr(net_utils, "resolve_iface_ip", lambda _: "10.0.0.7")
        srv = PsnServer(source_ip="")
        srv._exit_stack = contextlib.ExitStack()
        assert srv._try_open_multicast_socket_once(attempt=1) is True
        assert srv._socket.kwargs["iface_ip"] == "10.0.0.7"  # type: ignore[union-attr]

    def test_empty_source_ip_omits_iface_when_no_primary_available(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Offline host: ``resolve_iface_ip`` returns "" and we fall
        through to the no-iface_ip branch so multicast_expert doesn't
        receive an empty string (some versions reject that)."""
        from openfollow import net_utils

        monkeypatch.setattr(net_utils, "resolve_iface_ip", lambda _: "")
        srv = PsnServer(source_ip="")
        srv._exit_stack = contextlib.ExitStack()
        assert srv._try_open_multicast_socket_once(attempt=1) is True
        assert "iface_ip" not in srv._socket.kwargs  # type: ignore[union-attr]

    def test_exception_logs_warning_and_returns_false(self, caplog: pytest.LogCaptureFixture) -> None:
        _FakeMcastSocket.script = [OSError(99, "Cannot assign requested address")]
        srv = PsnServer()
        srv._exit_stack = contextlib.ExitStack()
        with caplog.at_level("WARNING", logger="openfollow.psn.server"):
            ok = srv._try_open_multicast_socket_once(attempt=2)
        assert ok is False
        assert srv._socket is None
        assert any(
            "PSN multicast socket failed (attempt 2" in rec.message
            for rec in caplog.records
            if rec.levelname == "WARNING"
        )


# --------------------------------------------------------------------------- #
# _retry_multicast_socket_background
# --------------------------------------------------------------------------- #


class _RecordedWaits:
    """A generation's ``stop.wait`` that returns at once and records each wait; stops after *waits*."""

    def __init__(self, stop: threading.Event, waits: int | None = None) -> None:
        self._stop = stop
        self._waits = waits
        self.timeouts: list[float] = []

    def __call__(self, timeout: float | None = None) -> bool:
        assert timeout is not None
        self.timeouts.append(timeout)
        if self._waits is not None and len(self.timeouts) >= self._waits:
            self._stop.set()
        return self._stop.is_set()


def _record_waits(monkeypatch: pytest.MonkeyPatch, srv: PsnServer, waits: int | None = None) -> _RecordedWaits:
    recorded = _RecordedWaits(srv._stop_event, waits)
    monkeypatch.setattr(srv._stop_event, "wait", recorded)
    return recorded


class TestRetryMulticastSocketBackground:
    def test_early_stop_exits_before_any_retry(self) -> None:
        srv = PsnServer()
        srv._stop_event.set()
        srv._retry_multicast_socket_background()
        assert srv._socket is None
        assert _FakeMcastSocket.instances == []

    def test_success_on_a_retry_logs_info(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _FakeMcastSocket.script = [OSError(99, "boom")]  # attempt 2 fails
        srv = PsnServer()
        srv._exit_stack = contextlib.ExitStack()
        recorded = _record_waits(monkeypatch, srv)
        with caplog.at_level("INFO", logger="openfollow.psn.server"):
            srv._retry_multicast_socket_background()
        assert srv._socket is not None
        assert recorded.timeouts == [2.0, 4.0]
        assert [rec.message for rec in caplog.records if rec.levelname == "INFO"] == [
            "PSN multicast socket connected on retry 3."
        ]

    def test_it_keeps_retrying_on_a_backoff_that_doubles_to_a_minute(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A network fixed long after the start must still bring PSN back, without a restart."""
        _FakeMcastSocket.script = [OSError(errno.EADDRNOTAVAIL, "Can't assign requested address")] * 8
        srv = PsnServer()
        srv._exit_stack = contextlib.ExitStack()
        recorded = _record_waits(monkeypatch, srv)
        with caplog.at_level("ERROR", logger="openfollow.psn.server"):
            srv._retry_multicast_socket_background()
        assert srv._socket is not None
        assert recorded.timeouts == [2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0, 60.0, 60.0]
        assert caplog.records == []

    def test_a_stop_ends_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _FakeMcastSocket.script = [OSError(errno.EADDRNOTAVAIL, "Can't assign requested address")] * 10
        srv = PsnServer()
        srv._exit_stack = contextlib.ExitStack()
        _record_waits(monkeypatch, srv, waits=3)
        srv._retry_multicast_socket_background()
        assert srv._socket is None
        assert len(_FakeMcastSocket.script) == 8


# --------------------------------------------------------------------------- #
# _recover_multicast_socket_background
# --------------------------------------------------------------------------- #


class TestRecoverMulticastSocketBackground:
    def test_loops_until_stop_event(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Recovery is unbounded – an interface may stay down for hours
        during a laptop sleep, so we loop until ``stop_event.set()``
        fires.  Verified here with a stop-event primed before entry.
        """
        monkeypatch.setattr(server_module, "_SOCKET_RETRY_DELAY", 0.0)
        srv = PsnServer()
        srv._stop_event.set()
        srv._recover_multicast_socket_background()
        assert srv._socket is None

    def test_stop_set_during_wait_exits_before_socket_open(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The loop is entered (stop not yet set), but ``stop_event`` fires
        during the retry backoff. The post-wait stop check must bail
        immediately rather than attempt another socket open. Deterministic
        stand-in for the real thread race: ``wait`` itself sets the event."""
        monkeypatch.setattr(server_module, "_SOCKET_RETRY_DELAY", 0.0)
        srv = PsnServer()

        def _wait_then_stop(_timeout: float) -> bool:
            srv._stop_event.set()
            return True

        monkeypatch.setattr(srv._stop_event, "wait", _wait_then_stop)
        srv._recover_multicast_socket_background()
        # Bailed at the post-wait stop check → no socket open attempted.
        assert srv._socket is None

    def test_eventual_reopen_is_not_reported_as_a_recovery(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A reopened socket proves nothing: with macOS Local Network access
        off, a fresh socket opens fine and its first send even returns
        success. Only sends that keep succeeding count as a recovery."""
        monkeypatch.setattr(server_module, "_SOCKET_RETRY_DELAY", 0.0)
        # Two failures, then success on the third construction.
        _FakeMcastSocket.script = [OSError(99, "boom"), OSError(99, "boom")]
        srv = PsnServer()
        srv._exit_stack = contextlib.ExitStack()
        srv.send_health.record_failure(OSError(errno.EHOSTUNREACH, "No route to host"), "236.10.10.10")
        with caplog.at_level("INFO"):
            srv._recover_multicast_socket_background()
        assert srv._socket is not None
        assert not [rec for rec in caplog.records if rec.levelno >= logging.INFO and "recover" in rec.message]
        assert srv.send_health.status().state == "retrying"


# --------------------------------------------------------------------------- #
# start / stop – background thread wiring
# --------------------------------------------------------------------------- #


class _DummyThread:
    """Thread stand-in that records start() but never actually runs the
    target – the unit tests drive ``_data_loop`` / ``_info_loop`` /
    retry loops directly by calling the method.
    """

    instances: list[_DummyThread] = []

    def __init__(
        self,
        *,
        target: Any,
        args: tuple[Any, ...] = (),
        daemon: bool = False,
        name: str = "",
    ) -> None:
        self.target = target
        self.args = args
        self.daemon = daemon
        self.name = name
        self.started = False
        self.join_timeouts: list[float] = []
        self._alive = False
        _DummyThread.instances.append(self)

    def start(self) -> None:
        self.started = True
        self._alive = True

    def run(self) -> None:
        """Run the target to its end, as the thread would."""
        self.target(*self.args)
        self._alive = False

    def join(self, timeout: float = 0.0) -> None:
        self.join_timeouts.append(timeout)
        # Simulate a clean join.
        self._alive = False

    def is_alive(self) -> bool:
        return self._alive


class _OnePass(Exception):
    """Ends a loop at its first wait."""


class _RunsOnePassThread(_DummyThread):
    """A thread scheduled the moment it starts: its loop runs one pass, up to its first wait."""

    def start(self) -> None:
        super().start()
        stop = self.args[0]

        def wait(_timeout: float | None = None) -> bool:
            raise _OnePass

        stop.wait = wait  # type: ignore[method-assign]
        with contextlib.suppress(_OnePass):
            self.target(*self.args)
        self._alive = False


@pytest.fixture(autouse=True)
def _reset_fake_threads() -> None:
    _DummyThread.instances = []


class TestStartStop:
    def test_start_multicast_success_does_not_spawn_retry_thread(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(server_module.threading, "Thread", _DummyThread)
        srv = PsnServer(mcast_ip="236.10.10.10")
        srv.start()
        assert srv._socket_thread is None  # sync success – no retry thread
        # Exactly two threads: data + info.
        names = sorted(t.name for t in _DummyThread.instances)
        assert names == ["PSN-Data", "PSN-Info"]
        srv._stop_event.set()
        srv.stop()

    def test_start_multicast_failure_spawns_retry_thread(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """First attempt fails → retry thread armed. Unblocks later via
        the normal retry window (not exercised here; this test only
        verifies the arming branch).
        """
        monkeypatch.setattr(server_module.threading, "Thread", _DummyThread)
        _FakeMcastSocket.script = [OSError(99, "boom")]
        srv = PsnServer(mcast_ip="236.10.10.10")
        srv.start()
        assert srv._socket_thread is not None
        assert srv._socket_thread.started is True
        names = sorted(t.name for t in _DummyThread.instances)
        assert names == ["PSN-Data", "PSN-Info", "PSN-SocketRetry"]
        srv._stop_event.set()
        srv.stop()

    def test_start_unicast_opens_plain_udp_socket(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``mcast_ip=None`` switches to unicast – no multicast_expert
        dance, just a plain AF_INET/DGRAM socket held in the exit stack.
        """
        monkeypatch.setattr(server_module.threading, "Thread", _DummyThread)
        srv = PsnServer(mcast_ip=None, target_ip="192.168.1.10")
        srv.start()
        assert isinstance(srv._socket, socket.socket)
        srv._stop_event.set()
        srv.stop()

    def test_stop_without_start_is_noop(self) -> None:
        """Config teardown calls ``stop()`` unconditionally – before
        ``start()``, every thread field is ``None``, so every
        ``if self._*_thread is not None`` branch takes the False side.
        Must not raise on the ``None.join()`` that the guards prevent.
        """
        srv = PsnServer()
        srv.stop()  # must not raise
        # Socket stays None (stack close is a no-op on an empty stack).
        assert srv._socket is None
        assert srv._data_thread is None
        assert srv._info_thread is None
        assert srv._socket_thread is None

    def test_stop_warns_when_threads_time_out(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """``join(timeout=...)`` lands on ``is_alive() == True`` → WARNING.
        This catches producer threads that deadlocked on a send buffer.
        """

        class _ZombieThread(_DummyThread):
            def join(self, timeout: float = 0.0) -> None:
                self.join_timeouts.append(timeout)
                # Never clears _alive – simulates a thread stuck in send.

        monkeypatch.setattr(server_module.threading, "Thread", _ZombieThread)
        _FakeMcastSocket.script = [OSError(99, "boom")]  # force retry thread
        srv = PsnServer()
        srv.start()
        with caplog.at_level("WARNING", logger="openfollow.psn.server"):
            srv.stop()
        warnings = [r.message for r in caplog.records if r.levelname == "WARNING"]
        assert any("socket-retry thread" in m for m in warnings)
        assert any("data thread" in m for m in warnings)
        assert any("info thread" in m for m in warnings)


# --------------------------------------------------------------------------- #
# _send throttle + _handle_send_error defensive branches
# --------------------------------------------------------------------------- #


class TestSendThrottleAndRecovery:
    def test_a_burst_of_send_errors_logs_once(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """One streak logs its first failure, however many sends it fails, so
        the log stays readable while an interface is down for a while."""
        # Non-transient errno so _handle_send_error is a no-op – we
        # want to isolate the logging, not the recovery.
        monkeypatch.setattr(server_module, "_SOCKET_RETRY_DELAY", 0.0)
        # However long the burst takes, it never reaches failing.
        monkeypatch.setattr("openfollow.send_health.FAILING_AFTER_S", float("inf"))
        _FakeMcastSocket.sendto_raises = OSError(errno.EACCES, "operation not permitted")
        srv = PsnServer(mcast_ip="236.10.10.10")
        srv._exit_stack = contextlib.ExitStack()
        assert srv._try_open_multicast_socket_once(attempt=1) is True

        with caplog.at_level("WARNING"):
            for _ in range(105):
                srv._send(b"\x00\x01")

        warnings = [r for r in caplog.records if "PSN" in r.message and "fail" in r.message]
        assert [r.message for r in warnings] == ["PSN: send failed: [Errno 13] operation not permitted"]
        assert srv.send_health.status().failed == 105

    def test_handle_send_error_unicast_is_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without multicast, there's no socket to rebuild – unicast UDP
        is connectionless. ``_handle_send_error`` must not spawn a
        recovery thread or it would try to ``_try_open_multicast_socket_once``
        with a falsy mcast_ip and permanently zero out ``_socket``.
        """
        monkeypatch.setattr(server_module.threading, "Thread", _DummyThread)
        srv = PsnServer(mcast_ip=None)
        srv._handle_send_error(OSError(errno.ENETUNREACH, "network down"))
        assert srv._socket_thread is None
        assert _DummyThread.instances == []

    def test_handle_send_error_tolerates_broken_stack_close(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The outgoing ExitStack's ``close`` may itself raise (e.g. the
        socket is already half-closed and the OS errors on the final
        close(2)).  The handler must log and keep going – we already
        swapped in the fresh stack before the close, so the recovery
        thread still has something to build on.
        """
        monkeypatch.setattr(server_module.threading, "Thread", _DummyThread)

        class _ExplodingStack:
            def close(self) -> None:
                raise RuntimeError("close failed")

        srv = PsnServer(mcast_ip="236.10.10.10")
        srv._exit_stack = _ExplodingStack()  # type: ignore[assignment]
        with caplog.at_level("ERROR", logger="openfollow.psn.server"):
            srv._handle_send_error(OSError(errno.ENETUNREACH, "network down"))
        # Recovery thread still spawned despite the close failure.
        assert srv._socket_thread is not None
        assert any(
            "closing stale socket stack failed" in rec.message for rec in caplog.records if rec.levelname == "ERROR"
        )

    def test_handle_send_error_coalesces_concurrent_raisers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Both data and info loops can race into the handler on the
        same outage. The ``is_alive`` check under the lock must mean
        only one recovery thread is ever spawned per socket outage.
        """
        monkeypatch.setattr(server_module.threading, "Thread", _DummyThread)
        srv = PsnServer(mcast_ip="236.10.10.10")
        srv._exit_stack = contextlib.ExitStack()

        srv._handle_send_error(OSError(errno.ENETUNREACH, "down"))
        first_thread = srv._socket_thread
        assert first_thread is not None

        # Simulate the first recovery thread still in flight.
        first_thread._alive = True  # type: ignore[attr-defined]
        srv._handle_send_error(OSError(errno.ENETUNREACH, "still down"))
        assert srv._socket_thread is first_thread  # coalesced – no new thread


# --------------------------------------------------------------------------- #
# _send with no socket → early return (covers the common startup-race
# where _data_loop ticks before _try_open_multicast_socket_once resolved)
# --------------------------------------------------------------------------- #


class TestSendBeforeSocketOpen:
    def test_send_returns_silently_when_socket_is_none(self) -> None:
        srv = PsnServer()
        srv._socket = None
        # Must not raise.
        srv._send(b"\x00")
        status = srv.send_health.status()
        assert (status.sent_total, status.failed_total) == (0, 0)

    def test_a_send_failing_after_its_generation_stopped_records_nothing(self) -> None:
        """A send thread that outlived stop()'s join must not open a streak on the next generation."""
        srv = PsnServer(mcast_ip="236.10.10.10")
        _FakeMcastSocket.sendto_raises = OSError(errno.EBADF, "Bad file descriptor")
        srv._socket = _FakeMcastSocket(socket.AF_INET)
        stale = threading.Event()
        stale.set()
        srv._send(b"\x00", stale)
        assert srv.send_health.status().failed_total == 0

    def test_a_send_skipped_while_the_socket_is_rebuilt_is_a_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A rebuild that keeps failing must not hold the streak short of a row."""
        # The recovery thread never runs, so the socket stays down.
        monkeypatch.setattr(server_module.threading, "Thread", _DummyThread)
        monkeypatch.setattr("openfollow.send_health.FAILING_AFTER_S", 0.0)
        srv = PsnServer(mcast_ip="236.10.10.10")
        srv._exit_stack = contextlib.ExitStack()
        assert srv._try_open_multicast_socket_once(attempt=1) is True
        _FakeMcastSocket.sendto_raises = OSError(errno.ENETUNREACH, "Network is unreachable")
        srv._send(b"\x00")
        assert srv._socket is None
        srv._send(b"\x00")
        status = srv.send_health.status()
        assert status.state == "failing"
        assert (status.failed, status.error) == (2, "Network is unreachable")

    @pytest.mark.parametrize(
        ("refusal", "error"),
        [
            (OSError(errno.EADDRNOTAVAIL, "Can't assign requested address"), "Can't assign requested address"),
            (
                server_module.multicast_expert.MulticastExpertError("Interface eth9 not found"),
                "Interface eth9 not found",
            ),
        ],
        ids=["os-error", "multicast-expert-error"],
    )
    def test_a_socket_that_never_opened_raises_a_row_named_by_its_error(
        self, monkeypatch: pytest.MonkeyPatch, refusal: Exception, error: str
    ) -> None:
        """No send ever failed, yet nothing goes out while the socket will not open."""
        monkeypatch.setattr(server_module.threading, "Thread", _DummyThread)
        monkeypatch.setattr("openfollow.send_health.FAILING_AFTER_S", 0.0)
        _FakeMcastSocket.script = [refusal]
        srv = PsnServer(mcast_ip="236.10.10.10")
        srv.start()
        assert srv._socket is None
        srv._send(b"\x00")
        srv._send(b"\x00")
        status = srv.send_health.status()
        assert (status.state, status.error) == ("failing", error)
        srv.stop()
        srv._send(b"\x00")
        assert srv.send_health.status().state == "ok"

    def test_a_stopped_generations_failed_open_names_no_streak(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("openfollow.send_health.FAILING_AFTER_S", 0.0)
        _FakeMcastSocket.script = [OSError(errno.EADDRNOTAVAIL, "Can't assign requested address")]
        srv = PsnServer(mcast_ip="236.10.10.10")
        stale = threading.Event()
        stale.set()
        assert srv._try_open_multicast_socket_once(attempt=2, stop_event=stale) is False
        srv._send(b"\x00")
        srv._send(b"\x00")
        assert srv.send_health.status().failed_total == 0

    def test_a_send_skipped_after_its_generation_stopped_records_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("openfollow.send_health.FAILING_AFTER_S", 0.0)
        srv = PsnServer(mcast_ip="236.10.10.10")
        srv.send_health.record_failure(OSError(errno.ENETUNREACH, "Network is unreachable"), "236.10.10.10")
        srv._socket = None
        stale = threading.Event()
        stale.set()
        srv._send(b"\x00", stale)
        status = srv.send_health.status()
        assert (status.state, status.failed_total) == ("retrying", 1)


def _not_assignable() -> OSError:
    return OSError(errno.EADDRNOTAVAIL, "Can't assign requested address")


_NOT_ASSIGNABLE = _not_assignable()


class TestSocketOpenRetry:
    """A multicast socket that will not open at the start is retried until it opens or its generation ends."""

    def test_a_socket_that_opens_late_clears_the_row(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(server_module.threading, "Thread", _DummyThread)
        monkeypatch.setattr("openfollow.send_health.FAILING_AFTER_S", 0.0)
        monkeypatch.setattr("openfollow.send_health.RECOVERY_HOLD_S", 0.0)
        _FakeMcastSocket.script = [_NOT_ASSIGNABLE] * 5
        srv = PsnServer(mcast_ip="236.10.10.10")
        srv.start()
        srv._send(b"\x00")
        srv._send(b"\x00")
        assert srv.send_health.status().state == "failing"
        recorded = _record_waits(monkeypatch, srv)
        retry = srv._socket_thread
        assert retry is not None
        retry.run()
        assert recorded.timeouts == [2.0, 4.0, 8.0, 16.0, 32.0]
        srv._send(b"\x00")
        srv._send(b"\x00")
        status = srv.send_health.status()
        assert (status.state, status.sent_total) == ("ok", 2)
        srv.stop()

    def test_stopping_ends_a_retry_waiting_out_its_backoff(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(server_module.threading, "Thread", _DummyThread)
        _FakeMcastSocket.script = [_NOT_ASSIGNABLE]
        srv = PsnServer(mcast_ip="236.10.10.10")
        srv.start()
        retry = srv._socket_thread
        assert retry is not None
        generation = srv._stop_event
        waits: list[float | None] = []

        def stopped_while_waiting(timeout: float | None = None) -> bool:
            waits.append(timeout)
            srv.stop()
            return threading.Event.wait(generation, timeout)

        monkeypatch.setattr(generation, "wait", stopped_while_waiting)
        retry.run()
        assert waits == [2.0]
        assert _FakeMcastSocket.instances == []

    def test_a_rebind_ends_the_previous_generations_retry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(server_module.threading, "Thread", _DummyThread)
        _FakeMcastSocket.script = [_NOT_ASSIGNABLE]
        srv = PsnServer(mcast_ip="236.10.10.10")
        srv.start()
        superseded = srv._socket_thread
        assert superseded is not None
        srv.rebind("192.0.2.10")
        live = srv._socket
        superseded.run()
        assert srv._socket is live
        assert len(_FakeMcastSocket.instances) == 1
        srv.stop()

    @pytest.mark.parametrize("opens", [False, True], ids=["refused", "opened"])
    def test_a_retry_whose_generation_stops_mid_open_neither_stores_nor_sends(
        self, monkeypatch: pytest.MonkeyPatch, opens: bool
    ) -> None:
        monkeypatch.setattr(server_module.threading, "Thread", _DummyThread)
        monkeypatch.setattr("openfollow.send_health.FAILING_AFTER_S", 0.0)
        _FakeMcastSocket.script = [_NOT_ASSIGNABLE]
        srv = PsnServer(mcast_ip="236.10.10.10")
        srv.start()
        superseded = srv._socket_thread
        assert superseded is not None
        _record_waits(monkeypatch, srv)
        restarted = False

        class _RestartedMidOpen(_FakeMcastSocket):
            def __init__(self, family: int, **kwargs: Any) -> None:
                nonlocal restarted
                if not restarted:
                    restarted = True
                    _FakeMcastSocket.script = [OSError(errno.ENODEV, "No such device")]
                    srv.stop()
                    srv.start()
                    if not opens:
                        raise OSError(errno.EADDRNOTAVAIL, "Can't assign requested address")
                super().__init__(family, **kwargs)

        monkeypatch.setattr(server_module.multicast_expert, "McastTxSocket", _RestartedMidOpen)
        superseded.run()
        srv._send(b"\x00")
        srv._send(b"\x00")
        status = srv.send_health.status()
        assert (status.state, status.error) == ("failing", "No such device")
        assert [(sock.closed, sock.sendto_calls) for sock in _FakeMcastSocket.instances] == (
            [(True, [])] if opens else []
        )
        srv.stop()

    def test_a_failed_open_keeps_nothing_it_half_opened_alive(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The cause outlives the attempt; the exception's traceback would hold the half-open socket's fd with it."""
        # A captured log record keeps the exception in its args.
        caplog.set_level(logging.ERROR, logger="openfollow.psn.server")
        monkeypatch.setattr(server_module.threading, "Thread", _DummyThread)
        monkeypatch.setattr("openfollow.send_health.FAILING_AFTER_S", 0.0)
        half_open: list[weakref.ref[object]] = []

        class _HalfOpen:
            pass

        class _RefusedAfterSocket(_FakeMcastSocket):
            def __init__(self, family: int, **kwargs: Any) -> None:
                held = _HalfOpen()
                half_open.append(weakref.ref(held))
                raise OSError(errno.EADDRNOTAVAIL, "Can't assign requested address")

        monkeypatch.setattr(server_module.multicast_expert, "McastTxSocket", _RefusedAfterSocket)
        srv = PsnServer(mcast_ip="236.10.10.10")
        srv.start()
        gc.collect()
        assert half_open[0]() is None
        srv._send(b"\x00")
        srv._send(b"\x00")
        assert srv.send_health.status().error == "Can't assign requested address"
        srv.stop()


class TestIdleRecord:
    def test_removing_every_marker_ends_the_failing_row(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With no marker PSN sends nothing, so nothing is failing; the row returns with the markers."""
        monkeypatch.setattr(server_module.threading, "Thread", _DummyThread)
        monkeypatch.setattr("openfollow.send_health.FAILING_AFTER_S", 0.0)
        _FakeMcastSocket.script = [_NOT_ASSIGNABLE]
        srv = PsnServer(mcast_ip="236.10.10.10")
        srv.start()
        srv.add_marker(1, "M1")
        srv._send_data_packet()
        srv._send_data_packet()
        assert srv.send_health.status().state == "failing"
        srv.remove_marker(1)
        srv._send_data_packet()
        assert srv.send_health.status().state == "ok"
        srv.add_marker(1, "M1")
        srv._send_data_packet()
        srv._send_data_packet()
        assert srv.send_health.status().state == "failing"
        srv.stop()

    def test_a_stopped_generation_with_no_markers_leaves_the_record_alone(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("openfollow.send_health.FAILING_AFTER_S", 0.0)
        srv = PsnServer(mcast_ip="236.10.10.10")
        for _ in range(2):
            srv.send_health.record_failure(OSError(errno.ENETUNREACH, "Network is unreachable"), "236.10.10.10")
        stale = threading.Event()
        stale.set()
        srv._send_data_packet(stale)
        assert srv.send_health.status().state == "failing"

    def test_the_open_error_is_forgotten_once_the_socket_opens(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A socket torn down after a send error is being rebuilt, not refusing to open."""
        monkeypatch.setattr(server_module.threading, "Thread", _DummyThread)
        _FakeMcastSocket.script = [_NOT_ASSIGNABLE]
        srv = PsnServer(mcast_ip="236.10.10.10")
        srv.start()
        _record_waits(monkeypatch, srv)
        retry = srv._socket_thread
        assert retry is not None
        retry.run()
        srv.add_marker(1, "M1")
        _FakeMcastSocket.sendto_raises = OSError(errno.ENETUNREACH, "Network is unreachable")
        srv._send_data_packet()
        srv.remove_marker(1)
        srv._send_data_packet()
        srv.add_marker(1, "M1")
        srv._send_data_packet()
        status = srv.send_health.status()
        assert (status.state, status.failed_total) == ("ok", 1)
        srv.stop()


# --------------------------------------------------------------------------- #
# Live-apply seams: rebind_mcast_ip + update_system_name
# --------------------------------------------------------------------------- #


class TestSendHealthLifecycle:
    def test_a_rebind_starts_a_new_record(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(server_module.threading, "Thread", _DummyThread)
        srv = PsnServer(mcast_ip="236.10.10.10")
        srv.start()
        srv.send_health.record_failure(OSError(errno.ENETUNREACH, "Network is unreachable"), "236.10.10.10")
        srv.rebind("192.0.2.10")
        assert srv.send_health.status().state == "ok"
        srv.stop()

    def test_stopping_forgets_the_streak(self) -> None:
        """A stopped output is off, not failing."""
        srv = PsnServer(mcast_ip="236.10.10.10")
        srv.send_health.record_failure(OSError(errno.ENETUNREACH, "Network is unreachable"), "236.10.10.10")
        srv.stop()
        assert srv.send_health.status().state == "ok"

    def test_a_good_send_is_counted_against_the_group(self) -> None:
        srv = PsnServer(mcast_ip="236.10.10.10")
        srv._exit_stack = contextlib.ExitStack()
        assert srv._try_open_multicast_socket_once(attempt=1) is True
        srv._send(b"\x00")
        assert srv.send_health.status().sent_total == 1
        _FakeMcastSocket.sendto_raises = OSError(errno.EHOSTUNREACH, "No route to host")
        srv._handle_send_error = lambda exc, stop_event=None: None  # type: ignore[method-assign]
        srv._send(b"\x00")
        status = srv.send_health.status()
        assert (status.failed, status.errno) == (1, errno.EHOSTUNREACH)


class TestRebindMcastIp:
    def test_rebinds_socket_on_new_mcast_ip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``rebind_mcast_ip`` recycles the multicast socket onto the
        new group: stop, mutate ``_mcast_ip``, start again. Marker
        registrations survive."""
        monkeypatch.setattr(server_module.threading, "Thread", _DummyThread)
        srv = PsnServer(mcast_ip="236.10.10.10")
        srv.start()
        srv.add_marker(1, "T1")

        srv.rebind_mcast_ip("239.0.0.1")

        assert srv._mcast_ip == "239.0.0.1"
        assert 1 in srv._markers  # registrations survived
        new_kwargs = _FakeMcastSocket.instances[-1].kwargs
        assert new_kwargs["mcast_ips"] == ["239.0.0.1"]
        srv.stop()

    def test_rebind_failure_tears_down_and_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If the new multicast socket can't open synchronously,
        ``rebind_mcast_ip`` must raise so the dispatcher can revert –
        the silent retry-thread behaviour from ``start()`` is wrong
        for live-apply."""
        monkeypatch.setattr(server_module.threading, "Thread", _DummyThread)
        srv = PsnServer(mcast_ip="236.10.10.10")
        srv.start()
        # Force every subsequent _try_open call to fail so ``start``'s
        # sync attempt returns False and the post-check fires.
        _FakeMcastSocket.script = [OSError(99, "boom")]
        with pytest.raises(OSError, match="failed to open multicast socket"):
            srv.rebind_mcast_ip("239.0.0.1")

    def test_a_rebind_that_cannot_open_logs_only_the_refusal(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Its generation is torn down at once, so a send it skipped would only add to the log on every retry."""
        monkeypatch.setattr(server_module.threading, "Thread", _RunsOnePassThread)
        srv = PsnServer(mcast_ip="236.10.10.10")
        srv.add_marker(1, "M1")
        _FakeMcastSocket.script = [_not_assignable()]
        with caplog.at_level(logging.DEBUG), pytest.raises(OSError, match="failed to open multicast socket"):
            srv.rebind("192.0.2.10")
        assert [(r.levelno, r.getMessage()) for r in caplog.records if r.name.startswith("openfollow")] == [
            (logging.WARNING, f"PSN multicast socket failed (attempt 1): {_not_assignable()}")
        ]
        assert srv.send_health.status().failed_total == 0
        assert srv.bound_source_ip() is None

    def test_a_start_that_cannot_open_still_raises_its_row(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """At boot the generation lives on, retrying, and nothing it skips goes out."""
        monkeypatch.setattr(server_module.threading, "Thread", _RunsOnePassThread)
        srv = PsnServer(mcast_ip="236.10.10.10")
        srv.add_marker(1, "M1")
        _FakeMcastSocket.script = [_not_assignable()]
        srv.start()
        assert srv.send_health.status().failed_total > 0

    def test_rebind_strips_whitespace_from_mcast_ip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Hand-edited TOML or web-UI values can carry leading/trailing
        whitespace; ``rebind_mcast_ip`` must normalise so the multicast
        bind doesn't get a tainted address. Mirrors the
        ``rebind(source_ip)`` strip behaviour."""
        monkeypatch.setattr(server_module.threading, "Thread", _DummyThread)
        srv = PsnServer(mcast_ip="236.10.10.10")
        srv.start()

        srv.rebind_mcast_ip("  239.0.0.1  ")

        assert srv._mcast_ip == "239.0.0.1"
        new_kwargs = _FakeMcastSocket.instances[-1].kwargs
        assert new_kwargs["mcast_ips"] == ["239.0.0.1"]
        srv.stop()

    def test_rebind_preserves_none_for_unicast(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``None`` is the unicast / disable-multicast sentinel; the
        strip must not coerce it to an empty string."""
        monkeypatch.setattr(server_module.threading, "Thread", _DummyThread)
        srv = PsnServer(mcast_ip="236.10.10.10")
        srv.start()

        srv.rebind_mcast_ip(None)

        assert srv._mcast_ip is None
        srv.stop()


class _NameReadProbe(PsnServer):
    """Counts reads of ``_system_name``, split by whether ``_lock`` was held.

    The getter consults the lock but the setter does not: ``__init__``
    assigns the attribute before ``_lock`` exists.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.locked_reads = 0
        self.unlocked_reads = 0
        super().__init__(*args, **kwargs)

    @property
    def _system_name(self) -> str:
        if self._lock.locked():
            self.locked_reads += 1
        else:
            self.unlocked_reads += 1
        return self._name_value

    @_system_name.setter
    def _system_name(self, value: str) -> None:
        self._name_value = value


class TestUpdateSystemName:
    def test_in_place_mutation_no_socket_recycle(self) -> None:
        """``update_system_name`` writes the attribute under the lock –
        no socket / thread recycle."""
        srv = PsnServer(system_name="Old")
        srv.update_system_name("New")
        assert srv._system_name == "New"

    def test_rename_reaches_the_next_info_packet(self) -> None:
        """Through the real property, not a probe: a rename has to land on
        the wire, not just on the attribute."""
        srv = PsnServer(system_name="Alpha", mcast_ip="236.10.10.10")
        srv._exit_stack = contextlib.ExitStack()
        assert srv._try_open_multicast_socket_once(attempt=1) is True
        srv.add_marker(1, "M1")

        srv.update_system_name("Bravo")
        srv._send_info_packet()

        payload = srv._socket.sendto_calls[-1][0]  # type: ignore[union-attr]
        assert b"Bravo" in payload
        assert b"Alpha" not in payload

    def test_info_packet_reads_the_name_under_the_lock(self) -> None:
        """The write side takes ``_lock``; the read side has to as well,
        or the lock orders nothing and a field added alongside the name
        later would tear without any sign that it could."""
        srv = _NameReadProbe(system_name="Old")
        srv.add_marker(1, "M1")
        # Unstarted: ``_socket`` is None, so ``_send`` returns before
        # touching the network. Encoding still reads the name.
        srv._send_info_packet()

        # Both halves: reading it under the lock, and reading it at all –
        # a packet that stopped carrying the name would satisfy the
        # unlocked count on its own.
        assert srv.locked_reads >= 1
        assert srv.unlocked_reads == 0


# --------------------------------------------------------------------------- #
# Thread generations – a survivor of stop() must not be revived by start()
# --------------------------------------------------------------------------- #

_JOIN_BUDGET_S = 5.0


class TestThreadGenerations:
    def test_start_does_not_resurrect_a_send_thread_that_outlived_stop(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A data thread still stuck in a send when stop()'s join expires must
        not be put back to work by the next start() – ``rebind()`` is stop()
        then start(), so the survivor would stream alongside the fresh thread.
        """
        srv = PsnServer(mcast_ip=None)
        in_send = threading.Event()
        release = threading.Event()
        sends: dict[int, int] = {}

        def blocking_send(stop_event: threading.Event | None = None) -> None:
            ident = threading.get_ident()
            sends[ident] = sends.get(ident, 0) + 1
            in_send.set()
            release.wait(_JOIN_BUDGET_S)

        monkeypatch.setattr(srv, "_send_data_packet", blocking_send)
        monkeypatch.setattr(srv, "_send_info_packet", lambda stop_event=None: None)

        srv.start()
        assert in_send.wait(_JOIN_BUDGET_S)
        survivor = srv._data_thread
        assert survivor is not None

        srv.stop()  # the join expires – the thread is wedged in the send
        assert survivor.is_alive()

        srv.start()  # a fresh generation, as rebind() would
        try:
            release.set()
            survivor.join(timeout=_JOIN_BUDGET_S)
            assert not survivor.is_alive(), "the survivor was revived by start()"
            assert sends[survivor.ident] == 1, "the survivor sent again after stop()"
        finally:
            release.set()
            srv.stop()

    def test_a_stale_generation_send_error_leaves_the_live_socket_alone(self) -> None:
        """What wedges a sender is a send that won't return – the same condition
        an operator answers by repointing PSN at another interface. The dying
        survivor's error must not tear down the socket the new generation
        streams on.
        """
        srv = PsnServer(mcast_ip="236.10.10.10")
        _FakeMcastSocket.sendto_raises = OSError(errno.ENETUNREACH, "iface gone")
        assert srv._try_open_multicast_socket_once(attempt=1) is True
        live_socket = srv._socket
        stale = threading.Event()
        stale.set()

        srv._send(b"\x00", stale)

        assert srv._socket is live_socket
        assert srv._socket_thread is None

    def test_a_stale_generation_does_not_adopt_a_socket(self) -> None:
        """A retry thread mid-open when its generation stopped must not hand the
        live one a socket bound to the interface it just left.
        """
        srv = PsnServer(mcast_ip="236.10.10.10")
        stale = threading.Event()
        stale.set()

        assert srv._try_open_multicast_socket_once(attempt=1, stop_event=stale) is False

        assert srv._socket is None
        # Opened off the lock, so the discarded socket has to be closed here.
        assert _FakeMcastSocket.instances[-1].closed is True

    @pytest.mark.parametrize(
        ("thread_name", "send_method"),
        [("PSN-Data", "_send_data_packet"), ("PSN-Info", "_send_info_packet")],
    )
    def test_a_send_loop_obeys_the_generation_that_spawned_it(
        self,
        monkeypatch: pytest.MonkeyPatch,
        thread_name: str,
        send_method: str,
    ) -> None:
        """Each loop is handed its generation instead of reading the live one when
        it first gets scheduled – a thread that starts late would otherwise take
        orders from a generation it does not belong to.
        """
        monkeypatch.setattr(server_module.threading, "Thread", _DummyThread)
        srv = PsnServer(mcast_ip=None)
        srv.start()
        spawned = next(t for t in _DummyThread.instances if t.name == thread_name)
        srv.stop()
        srv.start()  # a second generation is live; the first thread never ran

        sends: list[object] = []

        def stub(stop_event: threading.Event | None = None) -> None:
            sends.append(stop_event)
            srv._stop_event.set()  # bound the loop if it wrongly took the live one

        monkeypatch.setattr(srv, send_method, stub)
        spawned.target(*spawned.args)

        assert sends == [], "the superseded loop ran under the live generation"
        srv.stop()

    def test_a_send_loop_hands_its_generation_to_the_send(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Whatever judges the loop must judge its sends too, or the survivor's
        failing send is weighed against the live generation.
        """
        monkeypatch.setattr(server_module.threading, "Thread", _DummyThread)
        srv = PsnServer(mcast_ip=None)
        srv.start()
        generation = srv._stop_event
        loops = {t.name: t for t in _DummyThread.instances}
        seen: list[object] = []

        def stub(stop_event: threading.Event | None = None) -> None:
            seen.append(stop_event)
            generation.set()  # one pass per loop

        monkeypatch.setattr(srv, "_send_data_packet", stub)
        monkeypatch.setattr(srv, "_send_info_packet", stub)

        loops["PSN-Data"].target(*loops["PSN-Data"].args)
        generation.clear()
        loops["PSN-Info"].target(*loops["PSN-Info"].args)

        assert seen == [generation, generation]
        srv.stop()

    def test_the_recovery_thread_is_handed_the_failing_generation(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(server_module.threading, "Thread", _DummyThread)
        srv = PsnServer(mcast_ip="236.10.10.10")
        srv._socket = object()  # type: ignore[assignment]

        srv._handle_send_error(OSError(errno.ENETUNREACH, "iface gone"))

        recover = next(t for t in _DummyThread.instances if t.name == "PSN-SocketRecover")
        assert recover.args == (srv._stop_event,)

    def test_the_socket_loops_forward_their_generation_to_the_open(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        srv = PsnServer(mcast_ip="236.10.10.10")
        generation = threading.Event()
        seen: list[object] = []

        def fake_open(*, attempt: int, stop_event: threading.Event | None = None) -> bool:
            seen.append(stop_event)
            return True

        monkeypatch.setattr(srv, "_try_open_multicast_socket_once", fake_open)
        monkeypatch.setattr(generation, "wait", lambda timeout=None: False)

        srv._retry_multicast_socket_background(generation)
        srv._recover_multicast_socket_background(generation)

        assert seen == [generation, generation]

    def test_the_socket_retry_thread_is_handed_its_generation(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(server_module.threading, "Thread", _DummyThread)
        _FakeMcastSocket.script = [OSError(99, "boom")]
        srv = PsnServer(mcast_ip="236.10.10.10")
        srv.start()

        retry = next(t for t in _DummyThread.instances if t.name == "PSN-SocketRetry")
        assert retry.args == (srv._stop_event,)
        srv.stop()
