# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for the shared free-port helpers in ``tests/_ports.py``.

The reported flake was a TOCTOU: the port was released between the pick and
the bind, so another process on the host could take it. These pin the
retry contract that absorbs it - including the cases where retrying would be
wrong (a non-bind failure must surface, an exhausted retry must fail loudly).
"""

from __future__ import annotations

import errno
import socket

import pytest

from openfollow.osc.service import _PYTHONOSC_AVAILABLE, OscService
from tests import _ports
from tests._ports import (
    bind_free_udp_port,
    free_tcp_port,
    free_udp_port,
    live_on_free_port,
    start_on_free_port,
    wait_for_port,
)

pytestmark = pytest.mark.unit


class _FakeServer:
    """Server whose ``start()`` only really listens from ``listen_from`` on."""

    instances: list[_FakeServer] = []

    def __init__(self, port: int, listen_from: int) -> None:
        self.port = port
        self.attempt = len(_FakeServer.instances)
        self.listen_from = listen_from
        self.sock: socket.socket | None = None
        self.stopped = False
        _FakeServer.instances.append(self)

    def start(self) -> None:
        if self.attempt < self.listen_from:
            return
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", self.port))
        self.sock.listen(16)

    def stop(self) -> None:
        self.stopped = True
        if self.sock is not None:
            self.sock.close()
            self.sock = None


@pytest.fixture()
def fake_servers() -> list[_FakeServer]:
    _FakeServer.instances = []
    yield _FakeServer.instances
    for server in _FakeServer.instances:
        server.stop()


# ---------------------------------------------------------------------------
# Port pickers
# ---------------------------------------------------------------------------


def test_free_udp_port_is_bindable() -> None:
    port = free_udp_port()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("", port))


def test_free_tcp_port_is_bindable() -> None:
    port = free_tcp_port("127.0.0.1")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", port))


# ---------------------------------------------------------------------------
# bind_free_udp_port
# ---------------------------------------------------------------------------


def test_bind_free_udp_port_returns_the_port_it_bound() -> None:
    bound: list[int] = []
    port = bind_free_udp_port(bound.append)
    assert bound == [port]


def test_bind_free_udp_port_retries_a_stolen_port() -> None:
    """The flake itself: the first pick loses the race, the second wins."""
    seen: list[int] = []

    def _bind(port: int) -> None:
        seen.append(port)
        if len(seen) == 1:
            raise OSError(errno.EADDRINUSE, "address already in use")

    port = bind_free_udp_port(_bind)
    assert len(seen) == 2
    assert port == seen[1]


def test_bind_free_udp_port_gives_up_loudly() -> None:
    """An always-failing bind must fail as an assertion, not spin forever."""
    attempts = 0

    def _bind(port: int) -> None:
        nonlocal attempts
        attempts += 1
        raise OSError(errno.EADDRINUSE, "address already in use")

    with pytest.raises(AssertionError, match="no free UDP port after 3 attempts"):
        bind_free_udp_port(_bind, attempts=3)
    assert attempts == 3


def test_bind_free_udp_port_does_not_swallow_a_non_bind_failure() -> None:
    """Retrying a failure the test injected on purpose would hide it."""
    attempts = 0

    def _bind(port: int) -> None:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("can't start new thread")

    with pytest.raises(RuntimeError, match="can't start new thread"):
        bind_free_udp_port(_bind)
    assert attempts == 1


def test_bind_free_udp_port_reraises_an_oserror_that_is_not_a_lost_race() -> None:
    """Only EADDRINUSE means "someone took it, pick another". A persistent
    failure - fd exhaustion is the realistic one under xdist - would otherwise
    be retried four more times and reported as this helper's AssertionError,
    losing the errno that says what actually happened."""
    attempts = 0

    def _bind(port: int) -> None:
        nonlocal attempts
        attempts += 1
        raise OSError(errno.EMFILE, "Too many open files")

    with pytest.raises(OSError, match="Too many open files") as excinfo:
        bind_free_udp_port(_bind)
    assert excinfo.value.errno == errno.EMFILE
    assert attempts == 1


@pytest.mark.integration
@pytest.mark.skipif(not _PYTHONOSC_AVAILABLE, reason="python-osc not installed")
def test_osc_listener_survives_a_stolen_port(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end shape of the reported failure: the first port handed out is
    already occupied, so the listener's bind raises and the retry takes over."""
    blocker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    blocker.bind(("127.0.0.1", 0))
    stolen = blocker.getsockname()[1]
    picks = iter([stolen])
    monkeypatch.setattr(_ports, "free_udp_port", lambda: next(picks, free_udp_port()))

    svc = OscService()
    try:
        port = bind_free_udp_port(lambda p: svc.start_listener(p, allowed_ips=()))
        assert port != stolen
        assert svc.listener_port == port
    finally:
        svc.shutdown()
        blocker.close()


# ---------------------------------------------------------------------------
# wait_for_port
# ---------------------------------------------------------------------------


def test_wait_for_port_sees_a_listening_socket() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        assert wait_for_port(sock.getsockname()[1]) is True


def test_wait_for_port_times_out_on_a_closed_port() -> None:
    """Hold the port bound but never ``listen()``: connections are refused, and
    nothing else can take it mid-test. Asserting against a merely *released*
    port would be the same TOCTOU these helpers exist to close - another worker
    could bind and listen inside the window and flip the result."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        assert wait_for_port(sock.getsockname()[1], timeout=0.2) is False


# ---------------------------------------------------------------------------
# start_on_free_port / live_on_free_port
# ---------------------------------------------------------------------------


def test_start_on_free_port_returns_a_reachable_server(fake_servers: list[_FakeServer]) -> None:
    server, base = start_on_free_port(lambda port: _FakeServer(port, listen_from=0))
    try:
        assert base == f"http://127.0.0.1:{server.port}"
        assert wait_for_port(server.port) is True
    finally:
        server.stop()


def test_start_on_free_port_retries_when_the_port_never_opens(fake_servers: list[_FakeServer]) -> None:
    """A ``ConfigWebServer`` that loses the race serves a fallback port instead
    of raising, so the retry has to key off the port never opening."""
    server, _ = start_on_free_port(lambda port: _FakeServer(port, listen_from=1), timeout=0.2)
    try:
        assert len(fake_servers) == 2
        assert fake_servers[0].stopped is True
        assert server is fake_servers[1]
    finally:
        server.stop()


def test_start_on_free_port_gives_up_loudly(fake_servers: list[_FakeServer]) -> None:
    with pytest.raises(AssertionError, match="no free TCP port after 2 attempts"):
        start_on_free_port(lambda port: _FakeServer(port, listen_from=99), attempts=2, timeout=0.1)
    assert len(fake_servers) == 2
    assert all(server.stopped for server in fake_servers)


def test_live_on_free_port_stops_the_server_when_the_body_raises(fake_servers: list[_FakeServer]) -> None:
    with pytest.raises(ValueError, match="boom"):
        with live_on_free_port(lambda port: _FakeServer(port, listen_from=0)):
            raise ValueError("boom")
    assert fake_servers[-1].stopped is True
