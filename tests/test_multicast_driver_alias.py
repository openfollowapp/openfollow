# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Michel Honold and Paul Hermann
"""PSN and OTP open their multicast socket on a host whose interface scan raises."""

from __future__ import annotations

import contextlib
from typing import Any

import multicast_expert
import pytest

from openfollow.otp.server import OtpServer
from openfollow.psn.server import PsnServer
from tests import _driver_alias

pytestmark = pytest.mark.unit


@pytest.fixture
def host(monkeypatch: pytest.MonkeyPatch) -> None:
    """The driver-alias host, with the library's real socket class; only the OS socket is left out."""
    _driver_alias.install(monkeypatch)
    monkeypatch.setattr(multicast_expert.McastTxSocket, "__enter__", lambda self: self)
    monkeypatch.setattr(multicast_expert.McastTxSocket, "__exit__", lambda self, *exc: None)
    monkeypatch.setattr(multicast_expert.McastTxSocket, "getsockname", lambda self: ("192.0.2.10", 50000))


PINS = [
    pytest.param({"source_iface": "en0", "source_ip": "192.0.2.10"}, id="by-name"),
    pytest.param({"source_ip": "192.0.2.10"}, id="by-address"),
]


@pytest.mark.parametrize("pin", PINS)
@pytest.mark.parametrize("server_cls", [PsnServer, OtpServer], ids=["psn", "otp"])
def test_the_sender_opens_on_the_pinned_interface(host: None, server_cls: Any, pin: dict[str, str]) -> None:
    server = server_cls(mcast_ip="236.10.10.10", **pin)
    server._exit_stack = contextlib.ExitStack()
    assert server._try_open_multicast_socket_once(attempt=1) is True
    assert server._socket._iface_info.machine_name == "en0"
    assert server._socket._iface_info.index == 4
