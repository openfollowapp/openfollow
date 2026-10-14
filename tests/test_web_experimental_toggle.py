# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for the "Show experimental features" opt-in toggle.

Exercises ``/settings/experimental`` against a live ``ConfigWebServer``:
persist on/off, the one-way cascade-disable of person detection when off
(mouse input is no longer experimental and is left alone), and the
``<body>`` show-experimental class + CSS gate.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

import pytest

import openfollow.web.discovery as discovery_module
from openfollow.configuration import load_config, save_config
from openfollow.web.server import ConfigWebServer
from tests._ports import live_on_free_port

pytestmark = pytest.mark.integration


@pytest.fixture()
def live_server(tmp_path, monkeypatch):  # noqa: ANN001, ANN201
    """ConfigWebServer on a free localhost port; beacon I/O stubbed."""
    for cls in (discovery_module.BeaconSender, discovery_module.BeaconReceiver):
        monkeypatch.setattr(cls, "start", lambda self: None)
        monkeypatch.setattr(cls, "stop", lambda self: None)
    with live_on_free_port(
        lambda port: ConfigWebServer(
            config_path=str(tmp_path / "config.toml"),
            host="127.0.0.1",
            port=port,
            system_name="TestSystem",
        )
    ) as (server, base):
        yield server, base


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
    def test_turning_off_disables_detection_but_not_mouse(self, live_server) -> None:
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
        # Detection still cascades off; mouse input is no longer experimental
        # so it is left enabled.
        assert out.detection.enabled is False
        assert out.controller.mouse_enabled is True

    def test_turning_off_disables_the_ground_grid_and_keeps_its_polygon(self, live_server) -> None:
        server, base = live_server
        cfg = load_config(server.config_path)
        cfg.ui.show_experimental_features = True
        cfg.ground_grid.enabled = True
        cfg.ground_grid.vertices = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]]
        save_config(cfg, server.config_path)

        status, _ = _set_experimental(base, on=False)
        assert status == 200

        out = load_config(server.config_path)
        assert out.ground_grid.enabled is False
        assert out.ground_grid.vertices == [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]]

    def test_turning_on_does_not_reenable_detection(self, live_server) -> None:
        server, base = live_server
        # Turning on does not re-enable an already-off feature.
        status, _ = _set_experimental(base, on=True)
        assert status == 200

        out = load_config(server.config_path)
        assert out.ui.show_experimental_features is True
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

    def test_camera_lens_group_renders_ungated_when_off(self, live_server) -> None:
        _, base = live_server  # default off
        status, body = _get(base, "/")
        assert status == 200
        group = body.split('<h3 class="group-title">Lens distortion</h3>')[0].rsplit("<div", 1)[1]
        assert "experimental-feature" not in group
        assert 'id="camera-lens-k1"' in body and 'id="camera-lens-k2"' in body


class TestPathsCutOff:
    def test_turning_off_deactivates_every_path_and_keeps_them(self, live_server) -> None:
        from openfollow.configuration import PathConfig

        server, base = live_server
        cfg = load_config(server.config_path)
        cfg.ui.show_experimental_features = True
        cfg.paths.paths = [PathConfig(name="cross", active=True), PathConfig(name="loop", active=True)]
        save_config(cfg, server.config_path)

        assert _set_experimental(base, on=False)[0] == 200
        out = load_config(server.config_path).paths.paths
        assert [(p.name, p.active) for p in out] == [("cross", False), ("loop", False)]

    def test_turning_on_activates_nothing(self, live_server) -> None:
        from openfollow.configuration import PathConfig

        server, base = live_server
        cfg = load_config(server.config_path)
        cfg.paths.paths = [PathConfig(name="cross")]
        save_config(cfg, server.config_path)

        assert _set_experimental(base, on=True)[0] == 200
        assert load_config(server.config_path).paths.paths[0].active is False

    def test_an_import_turning_experimental_features_off_deactivates_every_path(self, live_server) -> None:
        from openfollow.configuration import PathConfig

        server, base = live_server
        cfg = load_config(server.config_path)
        cfg.ui.show_experimental_features = True
        cfg.paths.paths = [PathConfig(name="cross", active=True)]
        save_config(cfg, server.config_path)

        body = {"ui": {"show_experimental_features": False}, "paths": {"paths": [{"name": "cross", "active": True}]}}
        req = urllib.request.Request(
            f"{base}/api/config/import",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            assert r.status == 200
        out = load_config(server.config_path)
        assert out.ui.show_experimental_features is False
        assert [(p.name, p.active) for p in out.paths.paths] == [("cross", False)]
