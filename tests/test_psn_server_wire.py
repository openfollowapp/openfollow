# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Socket-lifecycle + multicast-retry coverage for :mod:`openfollow.psn.server`.

Closes the gaps left by the packet-content integration suite:

* ``_try_open_multicast_socket_once`` – success path (``source_ip``
  honoured, empty source_ip leaves ``iface_ip`` unset) and exception
  path (warning logged, return False).
* ``_retry_multicast_socket_background`` – retries until success,
  retries until max attempts exhausted (error logged), early-exit on
  ``stop_event.set()`` mid-retry.
* ``_recover_multicast_socket_background`` – loops indefinitely until
  success (INFO log), early-exits on ``stop_event.set()``.
* ``start`` – opens multicast sync-success path AND sync-failure →
  spawns the retry thread.  Also the unicast path when ``mcast_ip`` is
  falsy.
* ``stop`` – joins background threads with bounded timeouts; logs
  WARNING on any thread that doesn't stop in time.
* ``_send`` – throttled warning schedule (first 5 errors + every
  100th) and the "broken stack close raises" defensive branch in
  ``_handle_send_error``.
"""

from __future__ import annotations

import contextlib
import errno
import socket
from typing import Any

import pytest

import openfollow.psn.server as server_module
from openfollow.psn.server import _MAX_SOCKET_RETRIES, PsnServer

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
        self.closed = False
        _FakeMcastSocket.instances.append(self)

    def sendto(self, data: bytes, dest: tuple[str, int]) -> None:
        self.sendto_calls.append((data, dest))
        if _FakeMcastSocket.sendto_raises is not None:
            raise _FakeMcastSocket.sendto_raises

    def __enter__(self) -> _FakeMcastSocket:
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


class TestRetryMulticastSocketBackground:
    def test_early_stop_exits_before_any_retry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``stop_event.set()`` between start and the retry thread must
        cause the background loop to exit immediately without spending
        the full ``_SOCKET_RETRY_DELAY`` budget.
        """
        # Zero-delay wait so the thread loops through the sentinel check
        # fast.  The monkeypatch makes ``stop_event.wait`` non-blocking.
        monkeypatch.setattr(server_module, "_SOCKET_RETRY_DELAY", 0.0)
        srv = PsnServer()
        srv._stop_event.set()
        srv._retry_multicast_socket_background()  # must not raise, must terminate
        # No successful socket constructed.
        assert srv._socket is None

    def test_success_on_second_attempt_logs_info(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(server_module, "_SOCKET_RETRY_DELAY", 0.0)
        _FakeMcastSocket.script = [OSError(99, "boom")]  # attempt 2 fails
        # After the script drains, the next construction succeeds.
        srv = PsnServer()
        srv._exit_stack = contextlib.ExitStack()
        with caplog.at_level("INFO", logger="openfollow.psn.server"):
            srv._retry_multicast_socket_background()
        assert srv._socket is not None
        assert any("connected on retry" in rec.message for rec in caplog.records if rec.levelname == "INFO")

    def test_all_attempts_fail_logs_error(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(server_module, "_SOCKET_RETRY_DELAY", 0.0)
        # Fail every attempt from 2 through _MAX_SOCKET_RETRIES.
        _FakeMcastSocket.script = [OSError(99, "boom")] * (_MAX_SOCKET_RETRIES - 1)
        srv = PsnServer()
        srv._exit_stack = contextlib.ExitStack()
        with caplog.at_level("ERROR", logger="openfollow.psn.server"):
            srv._retry_multicast_socket_background()
        assert srv._socket is None
        assert any(
            "failed after" in rec.message and "attempts" in rec.message
            for rec in caplog.records
            if rec.levelname == "ERROR"
        )


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

    def test_eventual_success_logs_recovered(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(server_module, "_SOCKET_RETRY_DELAY", 0.0)
        # Two failures, then success on the third construction.
        _FakeMcastSocket.script = [OSError(99, "boom"), OSError(99, "boom")]
        srv = PsnServer()
        srv._exit_stack = contextlib.ExitStack()
        with caplog.at_level("INFO", logger="openfollow.psn.server"):
            srv._recover_multicast_socket_background()
        assert srv._socket is not None
        assert any("recovered on attempt" in rec.message for rec in caplog.records if rec.levelname == "INFO")


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
        daemon: bool = False,
        name: str = "",
    ) -> None:
        self.target = target
        self.daemon = daemon
        self.name = name
        self.started = False
        self.join_timeouts: list[float] = []
        self._alive = False
        _DummyThread.instances.append(self)

    def start(self) -> None:
        self.started = True
        self._alive = True

    def join(self, timeout: float = 0.0) -> None:
        self.join_timeouts.append(timeout)
        # Simulate a clean join.
        self._alive = False

    def is_alive(self) -> bool:
        return self._alive


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
    def test_send_errors_throttle_to_every_hundredth_after_five(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """First 5 errors log, then only every 100th. This keeps the log
        readable when an interface is down for a while without losing
        the first-failure signal.
        """
        # Non-transient errno so _handle_send_error is a no-op – we
        # want to isolate the logging-throttle branch, not the recovery.
        monkeypatch.setattr(server_module, "_SOCKET_RETRY_DELAY", 0.0)
        _FakeMcastSocket.sendto_raises = OSError(errno.EACCES, "operation not permitted")
        srv = PsnServer(mcast_ip="236.10.10.10")
        srv._exit_stack = contextlib.ExitStack()
        assert srv._try_open_multicast_socket_once(attempt=1) is True

        with caplog.at_level("WARNING", logger="openfollow.psn.server"):
            for _ in range(105):
                srv._send(b"\x00\x01")

        warnings = [r for r in caplog.records if r.levelname == "WARNING" and "PSN send failed" in r.message]
        # Errors 1-5 log, then 100 is the next (6 total records).
        assert len(warnings) == 6

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
        # Send total stays zero – the guard fires before the lock.
        assert srv._send_total == 0


# --------------------------------------------------------------------------- #
# Live-apply seams: rebind_mcast_ip + update_system_name
# --------------------------------------------------------------------------- #


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
        _FakeMcastSocket.script = [OSError(99, "boom") for _ in range(_MAX_SOCKET_RETRIES + 1)]
        with pytest.raises(OSError, match="failed to open multicast socket"):
            srv.rebind_mcast_ip("239.0.0.1")

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


class TestUpdateSystemName:
    def test_in_place_mutation_no_socket_recycle(self) -> None:
        """``update_system_name`` is a lock-protected attribute write –
        no socket / thread recycle. The next info packet picks up the
        new name from ``_system_name``."""
        srv = PsnServer(system_name="Old")
        # No start() – we only assert the attribute mutation, not
        # packet emission. The latter is covered by the integration
        # suite reading ``_send_info_packet`` with a bound sink.
        srv.update_system_name("New")
        assert srv._system_name == "New"
