# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for the TCP OSC transport.

Mix of unit and integration: hermetic backoff / state-machine checks
that don't touch the network, and real-localhost TCP integration that
proves the wire format and the auto-reconnect contract.
"""

from __future__ import annotations

import select
import socket
import threading
import time
from typing import Any

import pytest

import openfollow.osc.transport as transport_module
from openfollow.osc.transport import TcpOscSender

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Test fixtures: a localhost TCP server that records every framed message
# ---------------------------------------------------------------------------


def _free_port() -> int:
    # TCP-bind so the returned port is actually free in the TCP
    # namespace. A UDP bind to port 0 can hand back a port that's
    # already bound by another TCP listener, which then fails when
    # ``_LocalTcpServer`` tries to bind it (or, for the
    # "connect-to-nobody" tests, can collide with a real TCP listener
    # and produce a flaky connect-success instead of ECONNREFUSED).
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class _LocalTcpServer:
    """Background TCP listener that decodes 4-byte length-prefixed
    OSC frames and records the message bytes per connection.

    Tests use it both to assert wire framing and to simulate peer
    disconnects (call ``drop_connection`` to close the active socket
    so the sender's next write fails)."""

    def __init__(self) -> None:
        self.port = _free_port()
        self.frames: list[bytes] = []
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", self.port))
        self._listener.listen(2)
        self._listener.settimeout(0.1)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._active: socket.socket | None = None
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _addr = self._listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            conn.settimeout(0.5)
            with self._lock:
                self._active = conn
            try:
                self._read_frames(conn)
            except (OSError, ValueError):
                pass
            with self._lock:
                if self._active is conn:
                    self._active = None
            try:
                conn.close()
            except OSError:
                pass

    def _read_frames(self, conn: socket.socket) -> None:
        buf = b""
        while not self._stop.is_set():
            try:
                ready, _, _ = select.select([conn], [], [], 0.1)
            except (OSError, ValueError):
                return
            if not ready:
                continue
            try:
                chunk = conn.recv(4096)
            except (BlockingIOError, OSError):
                return
            if not chunk:
                return
            buf += chunk
            while len(buf) >= 4:
                length = int.from_bytes(buf[:4], "big")
                if len(buf) < 4 + length:
                    break
                self.frames.append(buf[4 : 4 + length])
                buf = buf[4 + length :]

    def drop_connection(self) -> None:
        """Force-close the active accepted connection so the sender's
        next write fails. Used to test the reconnect path."""
        with self._lock:
            sock = self._active
            self._active = None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    def stop(self) -> None:
        self._stop.set()
        try:
            self._listener.close()
        except OSError:
            pass
        self._thread.join(timeout=2.0)


@pytest.fixture
def server() -> Any:
    s = _LocalTcpServer()
    try:
        yield s
    finally:
        s.stop()


# ---------------------------------------------------------------------------
# Wire format
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestWireFormat:
    def test_send_emits_length_prefixed_osc_message(
        self,
        server: _LocalTcpServer,
    ) -> None:
        sender = TcpOscSender("127.0.0.1", server.port)
        try:
            sender.send_message("/cue/1/go", [])
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline and not server.frames:
                time.sleep(0.01)
            assert server.frames, "no framed message arrived"
            assert server.frames[0].startswith(b"/cue/1/go")
        finally:
            sender.close()

    def test_send_with_args_round_trips(
        self,
        server: _LocalTcpServer,
    ) -> None:
        sender = TcpOscSender("127.0.0.1", server.port)
        try:
            sender.send_message("/track/0", [1.5, -2.0, 0.25])
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline and not server.frames:
                time.sleep(0.01)
            # Address still appears at the start; full OSC body is
            # encoded after the typetag string.
            assert server.frames[0].startswith(b"/track/0")
        finally:
            sender.close()

    def test_multiple_sends_share_one_connection(
        self,
        server: _LocalTcpServer,
    ) -> None:
        sender = TcpOscSender("127.0.0.1", server.port)
        try:
            for i in range(5):
                sender.send_message(f"/cue/{i}", [])
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline and len(server.frames) < 5:
                time.sleep(0.01)
            assert len(server.frames) == 5
        finally:
            sender.close()


# ---------------------------------------------------------------------------
# Reconnect / backoff / drop-on-down
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestReconnect:
    def test_send_after_peer_drop_reconnects(
        self,
        server: _LocalTcpServer,
    ) -> None:
        """First send opens a socket. Peer drops the connection. The
        sender eventually reconnects and the next message arrives.

        TCP is intentionally fuzzy here – depending on kernel timing,
        the post-drop send can succeed once (bytes buffered) before
        the RST takes effect, then the next send fails and triggers
        the reconnect. We assert the eventual outcome (a reconnected
        frame arrives) rather than the exact sequence of failures."""
        sender = TcpOscSender("127.0.0.1", server.port)
        try:
            sender.send_message("/before", [])
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline and not server.frames:
                time.sleep(0.01)
            assert len(server.frames) == 1

            server.drop_connection()
            time.sleep(0.05)
            # Probe until the sender has noticed the disconnect AND
            # reconnected. Bypass each backoff window between probes.
            reconnected = False
            for attempt in range(20):
                sender._next_attempt_at = 0.0
                try:
                    sender.send_message(f"/probe-{attempt}", [])
                except OSError:
                    # Disconnect detected – backoff scheduled. Reset
                    # for the next probe.
                    continue
                # No error → either still on the old socket (TCP fuzz)
                # or the reconnect already happened. Wait briefly to
                # see if the bytes actually surface on the server.
                deadline = time.monotonic() + 0.3
                target = len(server.frames) + 1
                while time.monotonic() < deadline:
                    if any(f.startswith(f"/probe-{attempt}".encode()) for f in server.frames):
                        reconnected = True
                        break
                    if len(server.frames) >= target:
                        break
                    time.sleep(0.01)
                if reconnected:
                    break
            assert reconnected, "sender failed to reconnect after peer drop"
        finally:
            sender.close()


# ---------------------------------------------------------------------------
# Hermetic state-machine coverage (no real network)
# ---------------------------------------------------------------------------


class TestBackoffStateMachine:
    def test_send_during_backoff_window_raises_without_io(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A send inside the post-failure backoff window raises OSError
        immediately without attempting to connect or call into the OS
        socket layer."""
        sender = TcpOscSender("127.0.0.1", _free_port())
        # Pretend a previous failure scheduled the next attempt 5s out.
        sender._next_attempt_at = 1e18
        with pytest.raises(OSError, match="backoff window"):
            sender.send_message("/x", [])

    def test_connect_failure_advances_backoff(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Pick a port nothing's bound to. Use a high port to avoid
        # accidentally hitting a real service.
        sender = TcpOscSender("127.0.0.1", 1)  # port 1: connect refused
        # Skip backoff window for the first attempt.
        sender._next_attempt_at = 0.0
        for _ in range(6):
            sender._next_attempt_at = 0.0  # bypass for each attempt
            with pytest.raises(OSError):
                sender.send_message("/x", [])
        # After repeated failures, the index should be capped at the
        # last entry of the schedule.
        assert sender._backoff_idx == len(transport_module._BACKOFF_SCHEDULE_MS) - 1

    def test_close_is_idempotent(self) -> None:
        sender = TcpOscSender("127.0.0.1", _free_port())
        sender.close()
        sender.close()  # No raise.

    def test_close_with_no_active_connection_is_noop(self) -> None:
        sender = TcpOscSender("127.0.0.1", _free_port())
        # Never sent → never connected. Close should not raise.
        sender.close()
        assert sender._sock is None
        assert sender._reader_thread is None


# ---------------------------------------------------------------------------
# Reader thread shutdown
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestReaderThread:
    def test_reader_exits_when_close_is_called(
        self,
        server: _LocalTcpServer,
    ) -> None:
        sender = TcpOscSender("127.0.0.1", server.port)
        sender.send_message("/x", [])  # forces connect + reader thread start
        thread = sender._reader_thread
        assert thread is not None
        sender.close()
        thread.join(timeout=2.0)
        assert not thread.is_alive()

    def test_reader_exits_when_mark_failed_runs(
        self,
        server: _LocalTcpServer,
    ) -> None:
        """When a send detects transport failure, ``_mark_failed`` sets
        the reader's stop event AND closes the socket – both deterministic
        signals for the reader to exit. The natural peer-EOF path also
        exits the reader, but the timing depends on the TCP stack's
        readability semantics; this test pins the path the production
        scheduler actually triggers."""
        sender = TcpOscSender("127.0.0.1", server.port)
        try:
            sender.send_message("/x", [])
            thread = sender._reader_thread
            assert thread is not None
            sender._mark_failed()
            thread.join(timeout=2.0)
            assert not thread.is_alive(), "reader thread did not exit after _mark_failed"
        finally:
            sender.close()


# ---------------------------------------------------------------------------
# Reader thread start failure (OS thread exhaustion)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestReaderThreadStartFailure:
    def test_thread_start_failure_closes_socket_and_schedules_backoff(
        self,
        server: _LocalTcpServer,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If the reader thread can't start (OS thread exhaustion), the
        just-opened socket must be closed rather than left installed with
        no reader draining its RX, and the backoff schedule must advance –
        surfaced as the documented OSError, not a raw RuntimeError."""
        sender = TcpOscSender("127.0.0.1", server.port)
        try:
            real_start = threading.Thread.start

            def _boom(self: threading.Thread) -> None:
                raise RuntimeError("can't start new thread")

            monkeypatch.setattr(threading.Thread, "start", _boom)
            with pytest.raises(OSError, match="reader thread start failed"):
                sender.send_message("/x", [])
            # Socket was not leaked – the failed open dropped it.
            assert sender._sock is None
            # Backoff was scheduled so the next send is throttled.
            assert sender._next_attempt_at > 0
            # The reader thread reference was cleared too.
            assert sender._reader_thread is None
            # Restore so close()/any later real threads work.
            monkeypatch.setattr(threading.Thread, "start", real_start)
        finally:
            sender.close()


# ---------------------------------------------------------------------------
# Successful send resets backoff
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestBackoffReset:
    def test_successful_send_clears_backoff_index(
        self,
        server: _LocalTcpServer,
    ) -> None:
        sender = TcpOscSender("127.0.0.1", server.port)
        try:
            # Manually bump the index to simulate a prior failure.
            sender._backoff_idx = 3
            sender.send_message("/ok", [])
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline and not server.frames:
                time.sleep(0.01)
            # Successful send → reset to 0.
            assert sender._backoff_idx == 0
        finally:
            sender.close()


# ---------------------------------------------------------------------------
# Write deadline (head-of-line block guard)
# ---------------------------------------------------------------------------


class TestWriteDeadline:
    def test_select_returning_no_writers_eventually_raises(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Force ``select.select`` to never report write-readiness so
        the deadline expires; the sender raises OSError. This pinches
        the head-of-line guard without needing a real slow receiver."""
        sender = TcpOscSender("127.0.0.1", _free_port())
        # Plant a fake socket so ``_write`` doesn't try to open one.
        sender._sock = object()  # type: ignore[assignment]
        # Make select return empty write-ready lists.
        monkeypatch.setattr(
            transport_module.select,
            "select",
            lambda r, w, x, t: ([], [], []),
        )
        # Drive _now forward past the deadline on the second call.
        timestamps = iter([0.0, 0.05, 0.20])
        monkeypatch.setattr(
            transport_module,
            "_now",
            lambda: next(timestamps),
        )
        with pytest.raises(OSError, match="write deadline"):
            sender._write(b"\x00\x00\x00\x04test")
        # _mark_failed runs in the public path; here we called _write
        # directly so manually clean up.
        sender._sock = None

    def test_select_oserror_translates_to_oserror(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        sender = TcpOscSender("127.0.0.1", _free_port())
        sender._sock = object()  # type: ignore[assignment]

        def _boom(*_args: Any, **_kwargs: Any) -> Any:
            raise OSError("select interrupted")

        monkeypatch.setattr(transport_module.select, "select", _boom)
        with pytest.raises(OSError, match="select failed"):
            sender._write(b"\x00\x00\x00\x04test")
        sender._sock = None


# ---------------------------------------------------------------------------
# Send paths: BlockingIOError mid-send and zero-byte send
# ---------------------------------------------------------------------------


class TestSendPathInternals:
    def test_zero_byte_send_raises_socket_closed(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If ``socket.send`` returns 0 the kernel reports the peer has
        closed; treat as a transport error so backoff kicks in."""
        sender = TcpOscSender("127.0.0.1", _free_port())

        class _ZeroSendSock:
            def send(self, _data: bytes) -> int:
                return 0

        sender._sock = _ZeroSendSock()  # type: ignore[assignment]
        monkeypatch.setattr(
            transport_module.select,
            "select",
            lambda r, w, x, t: ([], [sender._sock], []),
        )
        timestamps = iter([0.0] * 10)
        monkeypatch.setattr(
            transport_module,
            "_now",
            lambda: next(timestamps, 0.0),
        )
        with pytest.raises(OSError, match="closed by peer"):
            sender._write(b"\x00\x00\x00\x04test")
        sender._sock = None

    def test_blocking_io_error_in_send_loops_until_deadline(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        sender = TcpOscSender("127.0.0.1", _free_port())
        attempts = {"count": 0}

        class _AlwaysBlocksSock:
            def send(self, _data: bytes) -> int:
                attempts["count"] += 1
                raise BlockingIOError("would block")

        sender._sock = _AlwaysBlocksSock()  # type: ignore[assignment]
        monkeypatch.setattr(
            transport_module.select,
            "select",
            lambda r, w, x, t: ([], [sender._sock], []),
        )
        # Two timestamps: start and "after deadline".
        timestamps = iter([0.0, 0.05, 0.05, 0.20, 0.20])
        monkeypatch.setattr(
            transport_module,
            "_now",
            lambda: next(timestamps, 0.20),
        )
        with pytest.raises(OSError, match="write deadline"):
            sender._write(b"\x00\x00\x00\x04test")
        assert attempts["count"] >= 1
        sender._sock = None


# ---------------------------------------------------------------------------
# Defensive: pythonosc missing
# ---------------------------------------------------------------------------


def test_build_packet_uses_osc_message_builder() -> None:
    """Sanity that ``_build_packet`` produces a well-formed packet –
    starts with the address, has the right length prefix."""
    sender = TcpOscSender("127.0.0.1", 1)
    packet = sender._build_packet("/foo", [1, 2.5, "bar"])
    assert len(packet) >= 4
    length = int.from_bytes(packet[:4], "big")
    assert length == len(packet) - 4
    assert packet[4:].startswith(b"/foo")


# ---------------------------------------------------------------------------
# Defensive close paths – sock.close() raising
# ---------------------------------------------------------------------------


class _ClosingExplodes:
    """Stand-in socket whose ``close()`` raises OSError. Used to verify
    the close paths in ``close()`` and ``_mark_failed`` swallow it."""

    def close(self) -> None:
        raise OSError("simulated close failure")


class TestCloseTolerance:
    def test_close_swallows_socket_close_oserror(self) -> None:
        sender = TcpOscSender("127.0.0.1", 1)
        sender._sock = _ClosingExplodes()  # type: ignore[assignment]
        # No reader thread → the close path skips the join. Only the
        # ``sock.close()`` arm runs; it must not propagate.
        sender.close()

    def test_mark_failed_swallows_socket_close_oserror(self) -> None:
        sender = TcpOscSender("127.0.0.1", 1)
        sender._sock = _ClosingExplodes()  # type: ignore[assignment]
        sender._mark_failed()
        # Backoff was still scheduled despite the close error.
        assert sender._next_attempt_at > 0


class TestReadLoop:
    def test_read_loop_exits_immediately_if_stop_already_set(self) -> None:
        sender = TcpOscSender("127.0.0.1", 1)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            stop = threading.Event()
            stop.set()
            sender._read_loop(sock, stop)
        finally:
            sock.close()

    def test_read_loop_returns_on_recv_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A ``BlockingIOError`` / ``OSError`` from ``recv`` exits the
        loop cleanly so the thread doesn't spin on a broken socket."""
        sender = TcpOscSender("127.0.0.1", 1)

        class _ReadErrorSock:
            def recv(self, _n: int) -> bytes:
                raise OSError("recv failed")

        sock = _ReadErrorSock()
        # Patch select to return the socket as ready once, then we'd
        # never get there because recv raises and the loop exits.
        monkeypatch.setattr(
            transport_module.select,
            "select",
            lambda r, w, x, t: ([sock], [], []),
        )
        sender._read_loop(sock, threading.Event())  # type: ignore[arg-type]

    def test_read_loop_returns_when_select_raises(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        sender = TcpOscSender("127.0.0.1", 1)

        def _boom(*_args: Any, **_kwargs: Any) -> Any:
            raise OSError("Bad file descriptor")

        monkeypatch.setattr(transport_module.select, "select", _boom)
        # Should return promptly without raising.
        sender._read_loop(object(), threading.Event())  # type: ignore[arg-type]

    def test_read_loop_continues_when_select_times_out(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Idle path: select returns no readers (poll timeout); the
        loop continues until stop fires. Without this, a quiet peer
        would force the reader to busy-spin."""
        sender = TcpOscSender("127.0.0.1", 1)
        stop = threading.Event()
        select_calls = {"count": 0}

        def _fake_select(
            r: Any,
            w: Any,
            x: Any,
            t: Any,
        ) -> tuple[list[Any], list[Any], list[Any]]:
            select_calls["count"] += 1
            if select_calls["count"] >= 2:
                stop.set()
            return ([], [], [])

        monkeypatch.setattr(transport_module.select, "select", _fake_select)
        sender._read_loop(object(), stop)  # type: ignore[arg-type]
        # Two select calls: first idle continue, second stops the loop.
        assert select_calls["count"] >= 2

    def test_read_loop_continues_after_data_then_exits_on_stop(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Reader loops back to the top after a successful ``recv``;
        the next iteration exits because ``stop`` was set in between."""
        sender = TcpOscSender("127.0.0.1", 1)
        stop = threading.Event()
        recv_calls = {"count": 0}

        class _DataSock:
            def recv(self, _n: int) -> bytes:
                recv_calls["count"] += 1
                # Set stop after the first successful recv so the loop
                # exits on the next iteration's ``while not stop.is_set()``
                # check.
                stop.set()
                return b"some-bytes"

        sock = _DataSock()
        monkeypatch.setattr(
            transport_module.select,
            "select",
            lambda r, w, x, t: ([sock], [], []),
        )
        sender._read_loop(sock, stop)  # type: ignore[arg-type]
        assert recv_calls["count"] == 1


# ---------------------------------------------------------------------------
# SLIP framing (RFC 1055)
# ---------------------------------------------------------------------------


class TestSlipEncoder:
    """Unit tests for ``_slip_encode`` – pure bytes-in/bytes-out, no
    network. Covers the four corners of RFC 1055: clean payload, the
    two escape sequences, and the empty case."""

    def test_clean_payload_is_bracketed_with_end(self) -> None:
        """Payload with no special bytes is wrapped END..END verbatim."""
        encoded = transport_module._slip_encode(b"hello")
        assert encoded == b"\xc0hello\xc0"

    def test_end_byte_in_payload_is_escaped(self) -> None:
        """``0xC0`` inside the payload becomes the two-byte escape
        ``0xDB 0xDC`` so receivers don't see it as a frame boundary."""
        encoded = transport_module._slip_encode(b"\xc0")
        assert encoded == b"\xc0\xdb\xdc\xc0"

    def test_esc_byte_in_payload_is_escaped(self) -> None:
        """``0xDB`` inside the payload becomes ``0xDB 0xDD`` so the
        receiver can disambiguate from the escape introducer."""
        encoded = transport_module._slip_encode(b"\xdb")
        assert encoded == b"\xc0\xdb\xdd\xc0"

    def test_mixed_escape_bytes_round_trip(self) -> None:
        """A payload with both special bytes interleaved with normal
        bytes encodes both escapes in-place and preserves the others."""
        encoded = transport_module._slip_encode(b"a\xc0b\xdbc")
        assert encoded == b"\xc0a\xdb\xdcb\xdb\xddc\xc0"

    def test_empty_payload_is_two_end_bytes(self) -> None:
        """Empty payload still emits a complete frame: ``[END, END]``.
        A receiver sees an empty packet and ignores it, but the framing
        is well-formed."""
        encoded = transport_module._slip_encode(b"")
        assert encoded == b"\xc0\xc0"


class TestLengthPrefixEncoder:
    """Unit test for ``_length_prefix_encode`` – pairs with the SLIP
    encoder tests so both wire formats have a hermetic baseline."""

    def test_emits_4_byte_big_endian_length_then_payload(self) -> None:
        encoded = transport_module._length_prefix_encode(b"hello")
        assert encoded == b"\x00\x00\x00\x05hello"

    def test_empty_payload(self) -> None:
        encoded = transport_module._length_prefix_encode(b"")
        assert encoded == b"\x00\x00\x00\x00"


class TestTcpSenderFramingDispatch:
    """``TcpOscSender._build_packet`` must dispatch on the constructor's
    framing – same OSC payload, different wire bytes."""

    def test_default_constructor_uses_length_prefix(self) -> None:
        """Backwards-compatible default for direct construction. The
        runtime path always passes ``framing`` explicitly through
        ``_make_client`` – this default only affects test / ad-hoc code."""
        sender = TcpOscSender("127.0.0.1", 1)
        packet = sender._build_packet("/foo", [])
        # 4-byte big-endian length header followed by the OSC body.
        length = int.from_bytes(packet[:4], "big")
        assert length == len(packet) - 4
        assert packet[4:].startswith(b"/foo")

    def test_slip_framing_brackets_with_end_byte(self) -> None:
        sender = TcpOscSender("127.0.0.1", 1, framing="slip")
        packet = sender._build_packet("/foo", [])
        # SLIP frames start AND end with END=0xC0.
        assert packet[0] == 0xC0
        assert packet[-1] == 0xC0
        # Inner bytes contain the OSC address.
        assert b"/foo" in packet[1:-1]

    def test_invalid_framing_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="framing must be one of"):
            TcpOscSender("127.0.0.1", 1, framing="bogus")


class _LocalSlipTcpServer:
    """Minimal localhost TCP listener that decodes SLIP-framed OSC
    messages and records the inner payloads.

    Mirrors ``_LocalTcpServer`` but for the SLIP framing variant –
    needed because introduces a second wire format and the
    integration test for SLIP must verify the actual bytes the sender
    emits land at the receiver as expected.
    """

    def __init__(self) -> None:
        self.port = _free_port()
        self.frames: list[bytes] = []
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", self.port))
        self._listener.listen(2)
        self._listener.settimeout(0.1)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _addr = self._listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            conn.settimeout(0.5)
            try:
                self._read_slip_frames(conn)
            except (OSError, ValueError):
                pass
            try:
                conn.close()
            except OSError:
                pass

    def _read_slip_frames(self, conn: socket.socket) -> None:
        buf = bytearray()
        while not self._stop.is_set():
            try:
                ready, _, _ = select.select([conn], [], [], 0.1)
            except (OSError, ValueError):
                return
            if not ready:
                continue
            try:
                chunk = conn.recv(4096)
            except (BlockingIOError, OSError):
                return
            if not chunk:
                return
            buf.extend(chunk)
            # Decode complete SLIP frames out of the buffer.
            while True:
                try:
                    end_idx = buf.index(0xC0)
                except ValueError:
                    break
                frame_bytes = bytes(buf[:end_idx])
                del buf[: end_idx + 1]
                if not frame_bytes:
                    # Leading END: either start-of-frame marker or an
                    # empty frame (a stand-alone END pair). Skip and
                    # let the next iteration capture the payload.
                    continue
                self.frames.append(_slip_decode(frame_bytes))

    def stop(self) -> None:
        self._stop.set()
        try:
            self._listener.close()
        except OSError:
            pass
        self._thread.join(timeout=2.0)


def _slip_decode(payload: bytes) -> bytes:
    """Reverse the RFC 1055 escape sequences. Used only by the test
    server – the production transport never decodes."""
    out = bytearray()
    i = 0
    while i < len(payload):
        b = payload[i]
        if b == 0xDB and i + 1 < len(payload):
            nxt = payload[i + 1]
            if nxt == 0xDC:
                out.append(0xC0)
                i += 2
                continue
            if nxt == 0xDD:
                out.append(0xDB)
                i += 2
                continue
        out.append(b)
        i += 1
    return bytes(out)


@pytest.mark.integration
class TestSlipWireFormat:
    """Round-trip test: the sender encodes SLIP, the listener decodes,
    and the inner OSC payload starts with the address."""

    def test_slip_framed_send_round_trips(self) -> None:
        server = _LocalSlipTcpServer()
        try:
            sender = TcpOscSender(
                "127.0.0.1",
                server.port,
                framing="slip",
            )
            try:
                sender.send_message("/cue/1/go", [])
                deadline = time.monotonic() + 1.0
                while time.monotonic() < deadline and not server.frames:
                    time.sleep(0.01)
                assert server.frames, "no SLIP-framed message arrived"
                assert server.frames[0].startswith(b"/cue/1/go")
            finally:
                sender.close()
        finally:
            server.stop()
