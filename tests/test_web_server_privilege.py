# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Unit tests for the wiring on :class:`ConfigWebServer`.

Covers the thin pass-through methods + provider plumbing. Real HTTP
round-trips for the routes themselves are exercised in
``tests/test_web_routes_privilege.py``.
"""

from __future__ import annotations

import pytest

import openfollow.web.discovery as discovery_module
from openfollow.web.server import ConfigWebServer

pytestmark = pytest.mark.unit


def _make_quiet_server(tmp_path, monkeypatch, **kwargs):
    monkeypatch.setattr(discovery_module.BeaconSender, "start", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconSender, "stop", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconReceiver, "start", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconReceiver, "stop", lambda self: None)
    return ConfigWebServer(
        config_path=str(tmp_path / "config.toml"),
        host="127.0.0.1",
        port=18080,
        local_ip="",
        **kwargs,
    )


class TestProvidersReturnEmptyByDefault:
    def test_capability_states_returns_empty_dict(self, tmp_path, monkeypatch) -> None:
        srv = _make_quiet_server(tmp_path, monkeypatch)
        assert srv.get_privilege_capability_states() == {}


class TestProvidersPassThrough:
    def test_capability_states_invokes_provider(self, tmp_path, monkeypatch) -> None:
        expected = {"service.restart": "passwordless"}
        srv = _make_quiet_server(
            tmp_path,
            monkeypatch,
            privilege_states_provider=lambda: expected,
        )
        assert srv.get_privilege_capability_states() == expected

    def test_capability_states_swallows_provider_exception(
        self,
        tmp_path,
        monkeypatch,
    ) -> None:
        def boom():
            raise RuntimeError("broker offline")

        srv = _make_quiet_server(
            tmp_path,
            monkeypatch,
            privilege_states_provider=boom,
        )
        # Misbehaving provider must not crash the page render – the
        # diagnostics bundle just reports no capabilities.
        assert srv.get_privilege_capability_states() == {}


class TestPasswordQueuePassThrough:
    def test_methods_delegate_to_command_queue(self, tmp_path, monkeypatch) -> None:
        srv = _make_quiet_server(tmp_path, monkeypatch)
        # Idle → no pending prompt.
        assert srv.pending_privilege_password_request() is None
        # Request, then submit + verify the round-trip.
        ok = srv._command_queue.request_privilege_password(
            reason="x",
            capability_name="y",
        )
        assert ok is True
        pending = srv.pending_privilege_password_request()
        assert pending == {"reason": "x", "capability_name": "y"}
        srv.submit_privilege_password("hunter2")
        assert srv._command_queue.consume_privilege_password(timeout=1.0) == "hunter2"

    def test_cancel_delegates(self, tmp_path, monkeypatch) -> None:
        srv = _make_quiet_server(tmp_path, monkeypatch)
        srv._command_queue.request_privilege_password(
            reason="x",
            capability_name="y",
        )
        srv.cancel_privilege_password()
        assert srv._command_queue.consume_privilege_password(timeout=1.0) is None
