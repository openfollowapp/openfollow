# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The Pi Camera's Camera row and its setup (Video Source -> Pi Camera).

The block's states are rendered from the partial directly; the routes run as
real HTTP against a live server, with the host supplied by the same provider
trio the runtime wires, so nothing touches config.txt or the privilege broker.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest
from bottle import template

import openfollow.web.discovery as discovery_module
from openfollow.configuration import AppConfig, load_config, save_config
from openfollow.web.server import ConfigWebServer
from tests._ports import live_on_free_port

pytestmark = pytest.mark.integration

_SETUP = "/section/video_source/camera-setup"
_BLOCK = "#picam-camera-setup"
_OV = {"model": "ov5647", "path": "/base/axi/i2c@88000/ov5647@36"}
_STATE: dict[str, Any] = {
    "available": True,
    "reason": "",
    "sensors": ["arducam-pivariety", "imx219", "imx290", "imx708", "ov5647"],
    "configured": "",
    "managed": False,
    "active": [],
    "live": "",
    "pending": False,
    "detected": [_OV],
}


def _state(**changes: Any) -> dict[str, Any]:
    return {**_STATE, **changes}


class _Page(HTMLParser):
    """What an operator can read and press in the block, and where each htmx request swaps."""

    _VOID = {"br", "hr", "img", "input", "link", "meta"}

    def __init__(self, html: str) -> None:
        super().__init__()
        self.html = html
        self.selects: dict[str, dict[str, Any]] = {}
        self.value: str | None = None
        self.notices: list[tuple[str, str, str]] = []
        self.buttons: list[str] = []
        self.requests: dict[str, str | None] = {}
        self._targets: list[str | None] = []
        self._select: str | None = None
        self._group: str | None = None
        self._capture: tuple[str, Callable[[str], None]] | None = None
        self._depth = 0
        self._parts: list[str] = []
        self.feed(html)
        self.close()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        own = a.get("hx-target")
        for verb in ("get", "post"):
            path = a.get(f"hx-{verb}")
            if path is not None:
                inherited = next((t for t in reversed(self._targets) if t is not None), None)
                self.requests[f"{verb.upper()} {path}"] = own if own is not None else inherited
        if tag not in self._VOID:
            self._targets.append(own)
        if self._capture is not None:
            self._parts.append(" ")  # a nested element starts a new line of text
            self._depth += tag == self._capture[0]
            return
        if tag == "select":
            self._select = a.get("id") or ""
            self.selects[self._select] = {"name": a.get("name"), "disabled": "disabled" in a, "options": []}
        elif tag == "optgroup":
            self._group = a.get("label")
        elif tag == "option":
            entry = (a.get("value") or "", "selected" in a, self._group)
            self._start(
                "option", lambda text: self.selects[self._select]["options"].append((entry[0], text, *entry[1:]))
            )
        elif tag == "output":
            self._start("output", lambda text: setattr(self, "value", text))
        elif tag == "button":
            self._start("button", self.buttons.append)
        elif tag == "div" and "notice" in (a.get("class") or "").split():
            cls, role = a.get("class") or "", a.get("role") or ""
            self._start("div", lambda text: self.notices.append((cls, role, text)))

    def _start(self, tag: str, sink: Callable[[str], None]) -> None:
        self._capture, self._depth, self._parts = (tag, sink), 1, []

    def handle_data(self, data: str) -> None:
        if self._capture is not None:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag not in self._VOID and self._targets:
            self._targets.pop()
        if self._capture is not None and tag == self._capture[0]:
            self._depth -= 1
            if self._depth == 0:
                self._capture[1](" ".join("".join(self._parts).split()))
                self._capture = None
        elif tag == "optgroup":
            self._group = None

    def selected(self, select_id: str) -> str:
        return next((value for value, _text, chosen, _group in self.selects[select_id]["options"] if chosen), "")


def _render(setup: dict[str, Any], *, mode: str = "view", camera_name: str = "", banner: Any = None) -> _Page:
    """The partial where it loads: inside the Video Source form, whose target is the whole section."""
    block = template("partials/camera_setup", setup=setup, mode=mode, banner=banner, camera_name=camera_name)
    return _Page(
        '<form hx-post="/section/video_source" hx-target="#video-source-section">'
        f'<div id="picam-camera-setup">{block}</div></form>'
    )


# -- the block's states ----------------------------------------------------------


@pytest.mark.unit
class TestTheCameraRow:
    def test_a_camera_named_here_reads_as_module_and_connector(self) -> None:
        page = _render(_state(configured="ov5647,cam0", managed=True, active=["ov5647,cam0"]))
        assert page.value == "Camera Module 1 (ov5647) on CAM/DISP 0"
        assert page.buttons == ["Change"]
        assert page.notices == []
        assert "camsetup-sensor" not in page.selects

    def test_a_hand_written_camera_says_where_it_came_from(self) -> None:
        page = _render(_state(configured="ov5647,cam0", managed=False, active=["ov5647,cam0"]))
        assert page.value == "Camera Module 1 (ov5647) on CAM/DISP 0, set in config.txt"

    def test_an_automatic_camera_reads_as_found(self) -> None:
        page = _render(_state(detected=[{"model": "imx708_wide", "path": "/a"}]))
        assert page.value == "Camera Module 3 (imx708_wide), found automatically"
        assert page.buttons == ["Change"]

    def test_one_camera_needs_no_choice(self) -> None:
        page = _render(_state(), camera_name=_OV["path"])
        assert "camsetup-stream" not in page.selects

    def test_several_cameras_offer_a_choice_saved_with_the_section(self) -> None:
        detected = [
            {"model": "imx708", "path": "/a"},
            {"model": "imx708", "path": "/b"},
            {"model": "ov5647", "path": "/c"},
        ]
        page = _render(_state(detected=detected), camera_name="/b")
        stream = page.selects["camsetup-stream"]
        assert stream["name"] == "picam_camera_name"
        assert stream["options"] == [
            ("", "First camera found", False, None),
            ("/a", "Camera Module 3 (imx708) #1", False, None),
            ("/b", "Camera Module 3 (imx708) #2", True, None),
            ("/c", "Camera Module 1 (ov5647)", False, None),
        ]
        assert page.value is None
        assert page.buttons == ["Change"]

    def test_a_saved_camera_that_is_gone_stays_visible(self) -> None:
        page = _render(_state(), camera_name="/base/old/imx219@10")
        assert page.selects["camsetup-stream"]["options"][-1] == (
            "/base/old/imx219@10",
            "imx219@10 (not found)",
            True,
            None,
        )

    def test_names_are_escaped(self) -> None:
        page = _render(_state(detected=[{"model": "<b>x", "path": "/a"}]))
        assert "&lt;b&gt;x" in page.html
        assert "<b>x" not in page.html


@pytest.mark.unit
class TestNoCameraFound:
    def test_the_setup_opens_by_itself(self) -> None:
        page = _render(_state(detected=[]))
        assert page.notices == [
            (
                "notice error",
                "alert",
                "No camera found on this station. Name the camera and the connector it is plugged into.",
            )
        ]
        assert page.buttons == ["Apply"]  # nothing to cancel back to
        assert page.value is None
        assert page.selected("camsetup-sensor") == "automatic"

    def test_a_named_camera_that_does_not_answer_is_offered_again(self) -> None:
        page = _render(_state(configured="imx219,cam1", managed=True, detected=[]))
        assert page.notices[0][2].startswith("No camera found on this station.")
        assert (page.selected("camsetup-sensor"), page.selected("camsetup-connector")) == ("imx219", "cam1")


@pytest.mark.unit
class TestChanging:
    def test_change_opens_the_module_and_connector_with_cancel(self) -> None:
        page = _render(_state(configured="ov5647,cam1", managed=True, active=["ov5647,cam1"]), mode="edit")
        assert page.value == "Camera Module 1 (ov5647) on CAM/DISP 1"
        assert page.buttons == ["Apply", "Cancel"]
        assert (page.selected("camsetup-sensor"), page.selected("camsetup-connector")) == ("ov5647", "cam1")
        assert page.selects["camsetup-connector"]["disabled"] is False

    def test_raspberry_pi_modules_come_first_by_name(self) -> None:
        page = _render(_state(), mode="edit")
        modules, others = "Raspberry Pi camera modules", "Other sensors"
        assert [(v, t, g) for v, t, _s, g in page.selects["camsetup-sensor"]["options"]] == [
            ("automatic", "Automatic", None),
            ("imx708", "Camera Module 3 (imx708)", modules),
            ("imx219", "Camera Module 2 (imx219)", modules),
            ("ov5647", "Camera Module 1 (ov5647)", modules),
            ("arducam-pivariety", "arducam-pivariety", others),
            ("imx290", "imx290", others),
        ]

    def test_automatic_needs_no_connector(self) -> None:
        page = _render(_state(), mode="edit")
        assert page.selected("camsetup-sensor") == "automatic"
        assert page.selects["camsetup-connector"]["disabled"] is True


@pytest.mark.unit
class TestWaitingForARestart:
    def test_the_restart_is_offered_with_what_runs_now(self) -> None:
        page = _render(_state(configured="imx708,cam1", managed=True, active=["ov5647,cam0"], pending=True))
        assert page.value == "Camera Module 3 (imx708) on CAM/DISP 1"
        assert page.notices == [
            (
                "notice warning",
                "status",
                "Takes effect after the next restart. Running now: Camera Module 1 (ov5647) on CAM/DISP 0",
            )
        ]
        assert page.buttons == ["Change", "Restart now"]
        assert 'hx-confirm="Restart the station now? Video and tracking stop until it is back."' in page.html

    def test_a_restart_that_leaves_no_camera_is_not_a_missing_camera(self) -> None:
        page = _render(_state(active=["ov5647,cam0"], pending=True, detected=[]))
        assert page.value == "Automatic"
        assert [text for _cls, _role, text in page.notices] == [
            "Takes effect after the next restart. Running now: Camera Module 1 (ov5647) on CAM/DISP 0"
        ]

    def test_nothing_running_says_so(self) -> None:
        page = _render(_state(configured="ov5647,cam0", managed=True, active=[], pending=True))
        assert page.notices[0][2].endswith("Running now: no camera")


@pytest.mark.unit
class TestAfterApply:
    def test_a_live_change_is_checked_again(self) -> None:
        """A camera named here but not answering turns into "No camera found" instead of a false success."""
        page = _render(_state(configured="ov5647,cam0", managed=True, detected=[]), mode="checking")
        assert page.value == "Camera Module 1 (ov5647) on CAM/DISP 0"
        assert page.notices == [("notice", "status", "Checking the camera…")]
        assert page.buttons == []
        assert page.requests[f"GET {_SETUP}"] == _BLOCK
        assert 'hx-trigger="load delay:3s"' in page.html

    def test_a_refused_change_keeps_the_choice_open(self) -> None:
        banner = {"kind": "error", "text": "a <b>sudo</b> refusal"}
        page = _render(_state(configured="ov5647,cam0", managed=True, detected=[]), mode="edit", banner=banner)
        assert page.notices == [("notice error", "alert", "a <b>sudo</b> refusal")]
        assert "a &lt;b&gt;sudo&lt;/b&gt; refusal" in page.html
        assert page.buttons == ["Apply", "Cancel"]


@pytest.mark.unit
class TestWithoutSetup:
    def test_a_camera_still_shows_without_a_change(self) -> None:
        page = _render(_state(available=False, reason="Camera setup is not installed on this station."))
        assert page.value == "Camera Module 1 (ov5647), found automatically"
        assert page.buttons == []
        assert page.notices == [("notice warning", "status", "Camera setup is not installed on this station.")]

    def test_no_camera_says_why_it_cannot_be_named(self) -> None:
        page = _render({"available": False, "reason": "Camera setup is not installed on this station."})
        assert page.notices == [
            ("notice error", "alert", "No camera found on this station. Camera setup is not installed on this station.")
        ]
        assert page.selects == {}

    def test_restarting_shows_only_that(self) -> None:
        page = _render({}, mode="restarting", banner={"kind": "ok", "text": "Restarting the station."})
        assert page.notices == [("notice success", "status", "Restarting the station.")]
        assert page.buttons == []
        assert page.value is None


@pytest.mark.unit
def test_the_block_keeps_a_row_gap_before_the_width_row() -> None:
    """It ends in a row whose own margin ``.row:last-child`` removes, so the
    block itself has to carry the gap to the Width row below it."""
    css = (Path(__file__).resolve().parents[1] / "openfollow/web/templates/base.tpl").read_text()

    def margin_bottom(selector: str) -> str:
        rule = re.search(rf"\n {re.escape(selector)} \{{([^}}]*)\}}", css)
        assert rule is not None, selector
        return re.search(r"margin-bottom:\s*([^;]+);", rule.group(1)).group(1)

    assert margin_bottom(".camera-block") == margin_bottom(".row")
    assert _render(_state()).html.split(">", 3)[2].strip().startswith('<div class="camera-block"')


@pytest.mark.unit
@pytest.mark.parametrize(
    ("setup", "mode"),
    [
        (_state(detected=[{"model": "imx708", "path": "/a"}, {"model": "imx219", "path": "/b"}]), "view"),
        (_state(detected=[]), "view"),
        (_state(configured="ov5647,cam0", managed=True), "edit"),
        (_state(configured="imx708,cam0", managed=True, active=["ov5647,cam0"], pending=True), "view"),
        (_state(configured="ov5647,cam0", managed=True), "checking"),
    ],
    ids=["choice", "no-camera", "change", "pending", "checking"],
)
def test_every_request_swaps_only_the_block_inside_the_video_source_form(setup: dict, mode: str) -> None:
    """The form targets the whole section for Save; a request that inherited
    that target would replace the section with this block."""
    requests = _render(setup, mode=mode).requests
    assert requests.pop("POST /section/video_source") == "#video-source-section"
    assert requests
    assert set(requests.values()) == {_BLOCK}


# -- the routes -------------------------------------------------------------------


class _Host:
    """Records what the routes ask of the host and answers with canned state."""

    def __init__(self, **state: Any) -> None:
        self.state = _state(**state)
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
        return {"ok": True, **{k: v for k, v in self.state.items() if k != "detected"}}

    def restart(self) -> dict:
        self.restarts += 1
        return dict(self.restart_result)


def _raise(*_args: Any) -> dict:
    raise RuntimeError("host exploded")


def _serve(tmp_path, monkeypatch, *, read=None, apply=None, restart=None, camera_name: str = ""):  # noqa: ANN001, ANN202
    monkeypatch.setattr(discovery_module.BeaconSender, "start", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconSender, "stop", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconReceiver, "start", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconReceiver, "stop", lambda self: None)
    config_path = tmp_path / "config.toml"
    cfg = AppConfig()
    cfg.picam_camera_name = camera_name
    save_config(cfg, str(config_path))
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


def _serve_host(tmp_path, monkeypatch, host: _Host, **kwargs: Any):  # noqa: ANN001, ANN202
    return _serve(tmp_path, monkeypatch, read=host.read, apply=host.apply, restart=host.restart, **kwargs)


def _get(base: str, path: str) -> tuple[int, _Page]:
    try:
        with urllib.request.urlopen(f"{base}{path}", timeout=5) as r:
            return r.status, _Page(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, _Page(e.read().decode())


def _post(base: str, path: str, data: dict) -> tuple[int, _Page]:
    req = urllib.request.Request(
        f"{base}{path}",
        data=urllib.parse.urlencode(data).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, _Page(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, _Page(e.read().decode())


_OV_FORM = {"camera_sensor": "ov5647", "camera_connector": "cam0"}


class TestReading:
    def test_the_view_reads_the_host_and_the_saved_camera_choice(self, tmp_path, monkeypatch) -> None:
        host = _Host(detected=[{"model": "imx708", "path": "/a"}, {"model": "imx219", "path": "/b"}])
        with _serve_host(tmp_path, monkeypatch, host, camera_name="/b") as (_server, base):
            status, page = _get(base, _SETUP)
        assert status == 200
        assert page.selected("camsetup-stream") == "/b"
        assert page.buttons == ["Change"]

    def test_change_asks_for_the_edit_view(self, tmp_path, monkeypatch) -> None:
        with _serve_host(tmp_path, monkeypatch, _Host()) as (_server, base):
            _status, view = _get(base, _SETUP)
            _status, edit = _get(base, f"{_SETUP}?edit=1")
        assert view.requests[f"GET {_SETUP}?edit=1"] == _BLOCK
        assert edit.buttons == ["Apply", "Cancel"]
        assert edit.requests[f"GET {_SETUP}"] == _BLOCK

    @pytest.mark.parametrize(
        ("kwargs", "reason"),
        [
            ({}, "Camera setup is not available on this build."),
            ({"read": _raise}, "Could not read this station's camera configuration."),
        ],
        ids=["not-wired", "unreadable"],
    )
    def test_a_host_that_cannot_answer(self, tmp_path, monkeypatch, kwargs: dict, reason: str) -> None:
        with _serve(tmp_path, monkeypatch, **kwargs) as (_server, base):
            _status, page = _get(base, _SETUP)
        assert page.notices == [("notice error", "alert", f"No camera found on this station. {reason}")]
        assert page.buttons == []


class TestApplying:
    @pytest.mark.parametrize(
        ("form", "token"),
        [
            (_OV_FORM, "ov5647,cam0"),
            ({"camera_sensor": "automatic", "camera_connector": "cam1"}, "automatic"),
            ({"camera_sensor": "automatic"}, "automatic"),  # the connector is disabled, so not sent
        ],
    )
    def test_a_live_change_is_checked_again(self, tmp_path, monkeypatch, form: dict, token: str) -> None:
        host = _Host()
        with _serve_host(tmp_path, monkeypatch, host) as (_server, base):
            status, page = _post(base, _SETUP, form)
        assert status == 200
        assert host.applies == [token]
        assert page.notices == [("notice", "status", "Checking the camera…")]

    def test_a_change_that_waits_for_a_restart_says_so(self, tmp_path, monkeypatch) -> None:
        host = _Host(active=["ov5647,cam0"])
        host.apply_result = {"ok": True, **_state(configured="imx708,cam0", managed=True, pending=True)}
        with _serve_host(tmp_path, monkeypatch, host) as (_server, base):
            _status, page = _post(base, _SETUP, {"camera_sensor": "imx708", "camera_connector": "cam0"})
        assert page.notices[0][2].startswith("Takes effect after the next restart.")
        assert "Restart now" in page.buttons

    @pytest.mark.parametrize(
        ("form", "token"),
        [({}, ","), ({"camera_sensor": "ov5647,cam0", "camera_connector": "cam1"}, "ov5647,cam0,cam1")],
    )
    def test_the_route_passes_what_it_got_for_the_handler_to_refuse(
        self, tmp_path, monkeypatch, form: dict, token: str
    ) -> None:
        host = _Host()
        with _serve_host(tmp_path, monkeypatch, host) as (_server, base):
            _post(base, _SETUP, form)
        assert host.applies == [token]

    def test_a_refused_change_shows_the_reason_and_keeps_the_choice_open(self, tmp_path, monkeypatch) -> None:
        host = _Host()
        host.apply_result = {"ok": False, "error": "This user is not in the sudoers file.", **_state()}
        with _serve_host(tmp_path, monkeypatch, host) as (_server, base):
            _status, page = _post(base, _SETUP, _OV_FORM)
        assert page.notices[0] == ("notice error", "alert", "This user is not in the sudoers file.")
        assert page.buttons == ["Apply", "Cancel"]

    @pytest.mark.parametrize(
        ("apply", "error"),
        [
            (None, "Camera setup is not available on this build."),
            (_raise, "The camera setting could not be changed."),
        ],
        ids=["not-wired", "raises"],
    )
    def test_a_change_that_cannot_run(self, tmp_path, monkeypatch, apply, error: str) -> None:  # noqa: ANN001
        with _serve(tmp_path, monkeypatch, read=_Host().read, apply=apply) as (_server, base):
            _status, page = _post(base, _SETUP, _OV_FORM)
        assert page.notices[0] == ("notice error", "alert", error)

    def test_saving_the_video_source_leaves_the_boot_configuration_alone(self, tmp_path, monkeypatch) -> None:
        """The setup's selects sit inside the Video Source form, so its Save submits them too."""
        host = _Host()
        with _serve_host(tmp_path, monkeypatch, host) as (server, base):
            status, _page = _post(base, "/section/video_source", {"video_source_type": "testpattern", **_OV_FORM})
            saved = load_config(server.config_path)
        assert status == 200
        assert host.applies == []
        assert saved.video_source_type == "testpattern"


class TestRestarting:
    def test_restart_reboots_and_drops_the_block(self, tmp_path, monkeypatch) -> None:
        host = _Host(pending=True, active=["ov5647,cam0"])
        with _serve_host(tmp_path, monkeypatch, host) as (_server, base):
            _status, page = _post(base, f"{_SETUP}/restart", {})
        assert host.restarts == 1
        assert page.notices == [("notice success", "status", "Restarting the station.")]
        assert page.buttons == []

    def test_a_refused_restart_keeps_the_offer(self, tmp_path, monkeypatch) -> None:
        host = _Host(pending=True, active=["ov5647,cam0"])
        host.restart_result = {"ok": False, "error": "Restart refused."}
        with _serve_host(tmp_path, monkeypatch, host) as (_server, base):
            _status, page = _post(base, f"{_SETUP}/restart", {})
        assert page.notices[0] == ("notice error", "alert", "Restart refused.")
        assert "Restart now" in page.buttons

    @pytest.mark.parametrize(
        ("restart", "error"),
        [
            (None, "Camera setup is not available on this build."),
            (_raise, "The camera setting could not be changed."),
        ],
        ids=["not-wired", "raises"],
    )
    def test_a_restart_that_cannot_run(self, tmp_path, monkeypatch, restart, error: str) -> None:  # noqa: ANN001
        host = _Host(pending=True)
        with _serve(tmp_path, monkeypatch, read=host.read, restart=restart) as (_server, base):
            _status, page = _post(base, f"{_SETUP}/restart", {})
        assert page.notices[0] == ("notice error", "alert", error)
