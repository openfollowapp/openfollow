# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Michel Honold and Paul Hermann
"""A socket layer with macOS's bind semantics, for tests of the web UI's port choice.

On macOS a socket bound with ``SO_REUSEADDR`` to one address shares a TCP port
with a socket on the wildcard, whichever came first; only an exact duplicate is
refused. :class:`SharingKernel` models that and nothing else: ``held`` lists
the ``(address, port)`` pairs other sockets hold, ``""`` being the wildcard.
"""

from __future__ import annotations

import errno
import os
import socket
from collections.abc import Iterable
from types import SimpleNamespace

import pytest

from openfollow import net_utils

STATION_ADDRESS = "192.0.2.10"


class SharingKernel:
    def __init__(
        self,
        held: Iterable[tuple[str, int]] = (),
        refused: dict[tuple[str, int], int] | None = None,
    ) -> None:
        self.held = set(held)
        # ``(address, port)`` -> errno for a bind refused before any conflict check.
        self.refused = dict(refused or {})
        self.binds: list[tuple[str, int]] = []

    def socket(self, *_args: object, **_kwargs: object) -> _Socket:
        return _Socket(self)

    def listen_on(self, host: str, port: int) -> None:
        """Record a listener this process bound, as the station's own threads do."""
        self.held.add(("" if host == net_utils.WEB_BIND_ALL else host, port))


class _Socket:
    def __init__(self, kernel: SharingKernel) -> None:
        self._kernel = kernel

    def setsockopt(self, *_args: object) -> None:
        return None

    def connect(self, _address: tuple[str, int]) -> None:
        # An offline show LAN: the primary-address probe finds no route.
        raise OSError(errno.ENETUNREACH, os.strerror(errno.ENETUNREACH))

    def bind(self, address: tuple[str, int]) -> None:
        self._kernel.binds.append(address)
        code = self._kernel.refused.get(address)
        if code is None and address in self._kernel.held:
            code = errno.EADDRINUSE
        if code is not None:
            raise OSError(code, os.strerror(code))

    def __enter__(self) -> _Socket:
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False


def share_ports(monkeypatch: pytest.MonkeyPatch, kernel: SharingKernel) -> None:
    """Make ``kernel`` this host's, a Mac whose one interface holds ``STATION_ADDRESS``."""
    addrs = {
        "lo0": [SimpleNamespace(family=socket.AF_INET, address="127.0.0.1")],
        "en0": [SimpleNamespace(family=socket.AF_INET, address=STATION_ADDRESS)],
    }
    monkeypatch.setattr(net_utils, "BINDS_SHARE_PORTS", True)
    monkeypatch.setattr(net_utils.socket, "socket", kernel.socket)
    monkeypatch.setattr(net_utils.psutil, "net_if_addrs", lambda: addrs)
