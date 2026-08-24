# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Free-port allocation for tests that bind a real socket.

Picking a port by binding a transient socket to ``(*, 0)`` and closing it
before the code under test binds it is a TOCTOU: anything on the host can take
the port in between - another xdist worker (``make test`` runs several), or the
kernel simply re-offering that ephemeral port. The port cannot be held open
across the handoff, because the listener under test has to bind it, so the fix
is to retry the *bind* rather than the pick.

The two retry helpers differ because the two failures look different:
``bind_free_udp_port`` watches for the ``OSError`` a failed bind raises, while
``live_on_free_port`` watches for the port never opening, since ``ConfigWebServer``
answers a taken port by quietly serving a fallback port instead of raising.
"""

from __future__ import annotations

import contextlib
import socket
import time
from collections.abc import Callable, Iterator
from typing import Protocol, TypeVar

BIND_ATTEMPTS = 5
STARTUP_TIMEOUT_S = 5.0


class _Startable(Protocol):
    """The ``start()`` / ``stop()`` surface :func:`live_on_free_port` drives."""

    def start(self) -> None: ...

    def stop(self) -> None: ...


_ServerT = TypeVar("_ServerT", bound=_Startable)


def free_udp_port() -> int:
    """Kernel-assigned free UDP port, released before it is returned."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("", 0))
        return int(sock.getsockname()[1])


def free_tcp_port(host: str = "") -> int:
    """Kernel-assigned free TCP port, released before it is returned."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def bind_free_udp_port(bind: Callable[[int], object], *, attempts: int = BIND_ATTEMPTS) -> int:
    """Call ``bind(port)`` on a free UDP port and return the port it took.

    Only an ``OSError`` from ``bind`` is retried, so a test that injects some
    other failure into the bind path still sees its own exception. A test
    asserting the bind-failure contract itself passes an explicitly occupied
    port and calls the listener directly - it wants the collision.
    """
    for _ in range(attempts):
        port = free_udp_port()
        try:
            bind(port)
        except OSError:
            continue
        return port
    raise AssertionError(f"no free UDP port after {attempts} attempts")


def wait_for_port(port: int, host: str = "127.0.0.1", timeout: float = STARTUP_TIMEOUT_S) -> bool:
    """Poll until ``host:port`` accepts a TCP connection, or ``timeout`` elapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.1):
                return True
        except OSError:
            time.sleep(0.05)
    return False


def start_on_free_port(
    factory: Callable[[int], _ServerT],
    *,
    host: str = "127.0.0.1",
    attempts: int = BIND_ATTEMPTS,
    timeout: float = STARTUP_TIMEOUT_S,
) -> tuple[_ServerT, str]:
    """Start ``factory(port)`` on a free TCP port; return it with its base URL.

    The caller owns ``stop()``; prefer :func:`live_on_free_port` where the
    server's lifetime is a block.
    """
    for _ in range(attempts):
        port = free_tcp_port(host)
        server = factory(port)
        server.start()
        if wait_for_port(port, host, timeout):
            return server, f"http://{host}:{port}"
        server.stop()
    raise AssertionError(f"no free TCP port after {attempts} attempts")


@contextlib.contextmanager
def live_on_free_port(
    factory: Callable[[int], _ServerT],
    *,
    host: str = "127.0.0.1",
    attempts: int = BIND_ATTEMPTS,
    timeout: float = STARTUP_TIMEOUT_S,
) -> Iterator[tuple[_ServerT, str]]:
    """:func:`start_on_free_port`, stopped on the way out even if the body raises."""
    server, base = start_on_free_port(factory, host=host, attempts=attempts, timeout=timeout)
    try:
        yield server, base
    finally:
        server.stop()
