# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for the "Show experimental features" opt-in toggle.

Exercises ``/settings/experimental`` against a live ``ConfigWebServer``:
persist on/off, the one-way cascade-disable of mouse input + person
detection when off, and the ``<body>`` show-experimental class + CSS gate.
"""

from __future__ import annotations

import socket
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

import openfollow.web.discovery as discovery_module
from openfollow.configuration import load_config, save_config
from openfollow.web.server import ConfigWebServer

pytestmark = pytest.mark.integration


def _find_free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return int(s.getsockname()[1])


def _wait_for_port(port: int, host: str = "127.0.0.1", timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.1):
                return True
        except OSError:
            time.sleep(0.05)
    return False


@pytest.fixture()
def live_server(tmp_path, monkeypatch):  # noqa: ANN001, ANN201
    """ConfigWebServer on a free localhost port; beacon I/O stubbed."""
    for cls in (discovery_module.BeaconSender, discovery_module.BeaconReceiver):
        monkeypatch.setattr(cls, "start", lambda self: None)
        monkeypatch.setattr(cls, "stop", lambda self: None)
    port = _find_free_tcp_port()
    server = ConfigWebServer(
        config_path=str(tmp_path / "config.toml"),
        host="127.0.0.1",
        port=port,
        system_name="TestSystem",
    )
    server.start()
    assert _wait_for_port(port), f"web server did not start on {port}"
    yield server, f"http://127.0.0.1:{port}"
    server.stop()


def _get(base: str, path: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(f"{base}{path}", timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def _post_form(base: str, path: str, data: dict) -> tuple[int, str]:
    req = urllib.request.Request(
        f"{base}{path}",
        data=urllib.parse.urlencode(data).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def _set_experimental(base: str, on: bool) -> tuple[int, str]:
    # An unchecked checkbox submits no field; send an empty form for off.
    data = {"show_experimental_features": "on"} if on else {}
    return _post_form(base, "/settings/experimental", data)


class TestPersist:
    def test_toggle_on_then_off_persists(self, live_server) -> None:
        server, base = live_server
        status, _ = _set_experimental(base, on=True)
        assert status == 200
        assert load_config(server.config_path).ui.show_experimental_features is True

        status, _ = _set_experimental(base, on=False)
        assert status == 200
        assert load_config(server.config_path).ui.show_experimental_features is False

    def test_default_is_off(self, live_server) -> None:
        server, base = live_server
        # Fresh config, nothing posted yet.
        assert load_config(server.config_path).ui.show_experimental_features is False


class TestCascadeDisable:
    def test_turning_off_disables_mouse_and_detection(self, live_server) -> None:
        server, base = live_server
        # Start with experimental on and both features enabled.
        cfg = load_config(server.config_path)
        cfg.ui.show_experimental_features = True
        cfg.controller.mouse_enabled = True
        cfg.detection.enabled = True
        save_config(cfg, server.config_path)

        status, _ = _set_experimental(base, on=False)
        assert status == 200

        out = load_config(server.config_path)
        assert out.ui.show_experimental_features is False
        # Both features disabled by the cascade.
        assert out.controller.mouse_enabled is False
        assert out.detection.enabled is False

    def test_turning_on_does_not_reenable_features(self, live_server) -> None:
        server, base = live_server
        # Turning on does not re-enable already-off features.
        status, _ = _set_experimental(base, on=True)
        assert status == 200

        out = load_config(server.config_path)
        assert out.ui.show_experimental_features is True
        assert out.controller.mouse_enabled is False
        assert out.detection.enabled is False


class TestBodyGate:
    def test_index_body_has_class_and_gate_when_on(self, live_server) -> None:
        server, base = live_server
        _set_experimental(base, on=True)
        status, body = _get(base, "/")
        assert status == 200
        assert 'class="show-experimental"' in body
        # Gate CSS is shipped.
        assert "body:not(.show-experimental) .experimental-feature" in body

    def test_index_body_lacks_class_when_off(self, live_server) -> None:
        server, base = live_server  # default off
        status, body = _get(base, "/")
        assert status == 200
        assert 'class="show-experimental"' not in body
        # Gate CSS is present regardless.
        assert "body:not(.show-experimental) .experimental-feature" in body
