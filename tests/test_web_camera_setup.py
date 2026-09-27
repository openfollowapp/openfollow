# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""System tests for the Pi Camera's camera setup (Video Source -> Pi Camera).

Real HTTP against a live server. The host's boot configuration is supplied by
the same provider trio the runtime wires, so these tests exercise the routes,
template and copy without touching config.txt or the privilege broker.
"""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any

import pytest
from bottle import template

import openfollow.web.discovery as discovery_module
from openfollow.configuration import AppConfig, load_config, save_config
from openfollow.web.server import ConfigWebServer
from tests._ports import live_on_free_port

pytestmark = pytest.mark.integration

_AUTOMATIC_STATE: dict[str, Any] = {
    "available": True,
    "reason": "",
    "sensors": ["imx708", "ov5647"],
    "configured": "",
    "managed": False,
    "active": [],
    "live": "",
    "pending": False,
}


class _Host:
    """Records what the routes ask of the host and answers with canned state."""

    def __init__(self, **state: Any) -> None:
        self.state = {**_AUTOMATIC_STATE, **state}
        self.applies: list[str] = []
        self.restarts = 0
        self.apply_result: dict | None = None
        self.restart_result: dict = {"ok": True}

    def read(self) -> dict:
        return dict(self.state)

    def apply(self, token: str) -> dict:
        self.applies.append(token)
        if self.apply_result is not None:
            return dict(self.apply_result)
        configured = "" if token == "automatic" else token
        self.state.update(configured=configured, managed=bool(configured))
        return {"ok": True, **self.state}

    def restart(self) -> dict:
        self.restarts += 1
        return dict(self.restart_result)


def _raise(*_args: Any) -> dict:
    raise RuntimeError("host exploded")


def _serve(tmp_path, monkeypatch, *, read=None, apply=None, restart=None):  # noqa: ANN001, ANN202
    monkeypatch.setattr(discovery_module.BeaconSender, "start", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconSender, "stop", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconReceiver, "start", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconReceiver, "stop", lambda self: None)
    config_path = tmp_path / "config.toml"
    save_config(AppConfig(), str(config_path))
    return live_on_free_port(
        lambda port: ConfigWebServer(
            config_path=str(config_path),
            host="127.0.0.1",
            port=port,
            system_name="TestSystem",
            camera_setup_state_provider=read,
            camera_setup_apply_handler=apply,
            camera_setup_restart_handler=restart,
        )
    )


def _serve_host(tmp_path, monkeypatch, host: _Host):  # noqa: ANN001, ANN202
    return _serve(tmp_path, monkeypatch, read=host.read, apply=host.apply, restart=host.restart)


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


def _selected(body: str, select_id: str) -> str:
    """The value of the ``<option>`` marked selected inside ``select_id``."""
    parser = _Selected(select_id)
    parser.feed(body)
    return parser.value


class _Selected(HTMLParser):
    def __init__(self, select_id: str) -> None:
        super().__init__()
        self._id = select_id
        self._inside = False
        self.value = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "select":
            self._inside = a.get("id") == self._id
        elif tag == "option" and self._inside and "selected" in a:
            self.value = a.get("value") or ""


_APPLY = 'hx-post="/section/video_source/camera-setup"'
_RESTART = 'hx-post="/section/video_source/camera-setup/restart"'


class TestReading:
    def test_automatic_lists_every_sensor_the_station_has(self, tmp_path, monkeypatch) -> None:
        with _serve_host(tmp_path, monkeypatch, _Host()) as (_server, base):
            status, body = _get(base, "/section/video_source/camera-setup")
        assert status == 200
        assert "Automatic: the station detects Raspberry Pi camera modules by itself." in body
        assert _selected(body, "camsetup-sensor") == "automatic"
        assert '<option value="imx708" >imx708</option>' in body
        assert '<option value="ov5647" >ov5647</option>' in body
        assert _APPLY in body
        assert _RESTART not in body

    @pytest.mark.parametrize(
        ("configured", "sensor", "connector", "label"),
        [
            ("ov5647,cam0", "ov5647", "cam0", "ov5647 on CAM/DISP 0"),
            ("imx708,cam1", "imx708", "cam1", "imx708 on CAM/DISP 1"),
        ],
    )
    def test_a_named_camera_preselects_its_sensor_and_connector(
        self, tmp_path, monkeypatch, configured: str, sensor: str, connector: str, label: str
    ) -> None:
        host = _Host(configured=configured, managed=True)
        with _serve_host(tmp_path, monkeypatch, host) as (_server, base):
            _status, body = _get(base, "/section/video_source/camera-setup")
        assert f"{label}, set up here." in body
        assert _selected(body, "camsetup-sensor") == sensor
        assert _selected(body, "camsetup-connector") == connector

    def test_a_hand_written_camera_says_where_it_came_from(self, tmp_path, monkeypatch) -> None:
        host = _Host(configured="ov5647,cam0", managed=False)
        with _serve_host(tmp_path, monkeypatch, host) as (_server, base):
            _status, body = _get(base, "/section/video_source/camera-setup")
        assert "ov5647 on CAM/DISP 0, set in config.txt." in body

    def test_a_change_waiting_for_a_restart_offers_one(self, tmp_path, monkeypatch) -> None:
        host = _Host(configured="imx708,cam0", managed=True, active=["ov5647,cam0"], pending=True)
        with _serve_host(tmp_path, monkeypatch, host) as (_server, base):
            _status, body = _get(base, "/section/video_source/camera-setup")
        assert "Takes effect after the next restart." in body
        assert _RESTART in body
        assert 'hx-confirm="Restart the station now?' in body

    def test_a_station_that_cannot_set_up_a_camera_says_why(self, tmp_path, monkeypatch) -> None:
        host = _Host(available=False, reason="Camera setup needs a Raspberry Pi.")
        with _serve_host(tmp_path, monkeypatch, host) as (_server, base):
            _status, body = _get(base, "/section/video_source/camera-setup")
        assert "Camera setup needs a Raspberry Pi." in body
        assert _APPLY not in body

    def test_not_wired(self, tmp_path, monkeypatch) -> None:
        with _serve(tmp_path, monkeypatch) as (_server, base):
            _status, body = _get(base, "/section/video_source/camera-setup")
        assert "Camera setup is not available on this build." in body
        assert _APPLY not in body

    def test_an_unreadable_host(self, tmp_path, monkeypatch) -> None:
        with _serve(tmp_path, monkeypatch, read=_raise) as (_server, base):
            _status, body = _get(base, "/section/video_source/camera-setup")
        assert "Could not read this station&#039;s camera configuration." in body
        assert _APPLY not in body


class TestApplying:
    @pytest.mark.parametrize(
        ("form", "token", "banner"),
        [
            (
                {"camera_sensor": "ov5647", "camera_connector": "cam1"},
                "ov5647,cam1",
                "Camera set up: ov5647 on CAM/DISP 1.",
            ),
            (
                {"camera_sensor": "automatic", "camera_connector": "cam1"},
                "automatic",
                "Back to automatic camera detection.",
            ),
        ],
    )
    def test_apply_names_the_camera_and_confirms(
        self, tmp_path, monkeypatch, form: dict, token: str, banner: str
    ) -> None:
        host = _Host()
        with _serve_host(tmp_path, monkeypatch, host) as (_server, base):
            status, body = _post_form(base, "/section/video_source/camera-setup", form)
        assert status == 200
        assert host.applies == [token]
        assert 'role="status"' in body and banner in body

    def test_a_change_that_waits_for_a_restart_says_so(self, tmp_path, monkeypatch) -> None:
        host = _Host()
        host.apply_result = {"ok": True, **host.state, "configured": "imx708,cam0", "pending": True}
        with _serve_host(tmp_path, monkeypatch, host) as (_server, base):
            _status, body = _post_form(
                base, "/section/video_source/camera-setup", {"camera_sensor": "imx708", "camera_connector": "cam0"}
            )
        assert "Saved. The camera changes when the station restarts." in body
        assert _RESTART in body

    @pytest.mark.parametrize(
        ("form", "token"),
        [
            ({}, ","),
            ({"camera_sensor": "ov5647,cam0", "camera_connector": "cam1"}, "ov5647,cam0,cam1"),
        ],
    )
    def test_the_route_passes_what_it_got_for_the_handler_to_refuse(
        self, tmp_path, monkeypatch, form: dict, token: str
    ) -> None:
        host = _Host()
        with _serve_host(tmp_path, monkeypatch, host) as (_server, base):
            _post_form(base, "/section/video_source/camera-setup", form)
        assert host.applies == [token]

    def test_a_refused_change_shows_the_reason_as_an_alert(self, tmp_path, monkeypatch) -> None:
        host = _Host()
        host.apply_result = {"ok": False, "error": "a <b>sudo</b> refusal", **host.state}
        with _serve_host(tmp_path, monkeypatch, host) as (_server, base):
            _status, body = _post_form(
                base, "/section/video_source/camera-setup", {"camera_sensor": "ov5647", "camera_connector": "cam0"}
            )
        assert 'role="alert"' in body
        assert "a &lt;b&gt;sudo&lt;/b&gt; refusal" in body
        assert _APPLY in body  # the form stays, to try again

    def test_a_handler_that_raises(self, tmp_path, monkeypatch) -> None:
        host = _Host()
        with _serve(tmp_path, monkeypatch, read=host.read, apply=_raise) as (_server, base):
            _status, body = _post_form(
                base, "/section/video_source/camera-setup", {"camera_sensor": "ov5647", "camera_connector": "cam0"}
            )
        assert "The camera setting could not be changed." in body
        assert "Automatic: the station detects" in body  # re-read after the failure

    def test_not_wired(self, tmp_path, monkeypatch) -> None:
        with _serve(tmp_path, monkeypatch) as (_server, base):
            _status, body = _post_form(
                base, "/section/video_source/camera-setup", {"camera_sensor": "ov5647", "camera_connector": "cam0"}
            )
        assert 'role="alert"' in body and "Camera setup is not available on this build." in body

    def test_saving_the_video_source_leaves_the_boot_configuration_alone(self, tmp_path, monkeypatch) -> None:
        """The setup's selects sit inside the Video Source form, so its Save submits them too."""
        host = _Host()
        with _serve_host(tmp_path, monkeypatch, host) as (server, base):
            status, _body = _post_form(
                base,
                "/section/video_source",
                {"video_source_type": "testpattern", "camera_sensor": "ov5647", "camera_connector": "cam0"},
            )
            saved = load_config(server.config_path)
        assert status == 200
        assert host.applies == []
        assert saved.video_source_type == "testpattern"


class TestRestarting:
    def test_restart_reboots_and_drops_the_form(self, tmp_path, monkeypatch) -> None:
        host = _Host(pending=True)
        with _serve_host(tmp_path, monkeypatch, host) as (_server, base):
            _status, body = _post_form(base, "/section/video_source/camera-setup/restart", {})
        assert host.restarts == 1
        assert "Restarting the station." in body
        assert _APPLY not in body and _RESTART not in body

    def test_a_refused_restart_keeps_the_offer(self, tmp_path, monkeypatch) -> None:
        host = _Host(pending=True)
        host.restart_result = {"ok": False, "error": "Restart refused."}
        with _serve_host(tmp_path, monkeypatch, host) as (_server, base):
            _status, body = _post_form(base, "/section/video_source/camera-setup/restart", {})
        assert 'role="alert"' in body and "Restart refused." in body
        assert _RESTART in body

    @pytest.mark.parametrize(
        ("restart", "error"),
        [
            (None, "Camera setup is not available on this build."),
            (_raise, "The camera setting could not be changed."),
        ],
    )
    def test_a_restart_that_cannot_run(self, tmp_path, monkeypatch, restart, error: str) -> None:  # noqa: ANN001
        host = _Host(pending=True)
        with _serve(tmp_path, monkeypatch, read=host.read, restart=restart) as (_server, base):
            _status, body = _post_form(base, "/section/video_source/camera-setup/restart", {})
        assert 'role="alert"' in body and error in body


class _Requests(HTMLParser):
    """Each htmx request in a page, with the ``hx-target`` htmx resolves for it: its own, else inherited."""

    _VOID = {"br", "hr", "img", "input", "link", "meta"}

    def __init__(self) -> None:
        super().__init__()
        self._targets: list[str | None] = []
        self.requests: dict[str, str | None] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        own = a.get("hx-target")
        path = a.get("hx-post") or a.get("hx-get")
        if path is not None:
            self.requests[path] = (
                own if own is not None else next((t for t in reversed(self._targets) if t is not None), None)
            )
        if tag not in self._VOID:
            self._targets.append(own)

    def handle_endtag(self, tag: str) -> None:
        if tag not in self._VOID and self._targets:
            self._targets.pop()


@pytest.mark.unit
def test_every_request_swaps_only_the_setup_inside_the_video_source_form() -> None:
    """The form targets the whole section for Save; a button that inherited that
    target would replace the section with the setup block."""
    setup = {**_AUTOMATIC_STATE, "configured": "imx708,cam0", "managed": True, "pending": True}
    html = (
        '<form hx-post="/section/video_source" hx-target="#video-source-section">'
        '<div id="picam-camera-setup">' + template("partials/camera_setup", setup=setup, banner=None) + "</div></form>"
    )
    parser = _Requests()
    parser.feed(html)
    assert parser.requests == {
        "/section/video_source": "#video-source-section",
        "/section/video_source/camera-setup": "#picam-camera-setup",
        "/section/video_source/camera-setup/restart": "#picam-camera-setup",
    }
