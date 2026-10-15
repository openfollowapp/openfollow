# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The touch remote's routes: the gate, sessions, holds, driving, presses and the card's state."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

import pytest

import openfollow.web.discovery as discovery_module
from openfollow.configuration import load_config, save_config
from openfollow.input.touch_remote import GLIDE, PATHFINDER, RESET, RemoteAction, TouchRemote
from openfollow.web.server import ConfigWebServer
from tests._ports import live_on_free_port

pytestmark = pytest.mark.integration

POSITIONS = {1: (1.5, 3.0, 1.6), 2: (-2.0, 4.0, 0.0)}


@pytest.fixture()
def live(tmp_path, monkeypatch):  # noqa: ANN001, ANN201
    for cls in (discovery_module.BeaconSender, discovery_module.BeaconReceiver):
        monkeypatch.setattr(cls, "start", lambda self: None)
        monkeypatch.setattr(cls, "stop", lambda self: None)
    config_path = str(tmp_path / "config.toml")
    cfg = load_config(config_path)
    cfg.touch_remote.enabled = True
    cfg.controlled_marker_ids = [1, 2]
    save_config(cfg, config_path)
    remote = TouchRemote()
    with live_on_free_port(
        lambda port: ConfigWebServer(
            config_path=config_path,
            host="127.0.0.1",
            port=port,
            system_name="TestSystem",
            marker_position_provider=POSITIONS.get,
            path_state_provider=lambda: {"recording": "cross", "locks": {2: "cross"}},
            touch_remote=remote,
        )
    ) as (_server, base):
        yield base, config_path, remote


def _call(base: str, method: str, path: str, body: Any = None, raw: bytes | None = None) -> tuple[int, Any]:
    data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
    req = urllib.request.Request(
        f"{base}{path}", data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def _open(base: str) -> str:
    status, data = _call(base, "POST", "/api/remote/open", {})
    assert status == 200
    return data["token"]


def _holding(base: str, marker: int = 1) -> str:
    token = _open(base)
    assert _call(base, "POST", "/api/remote/hold", {"token": token, "marker": marker})[0] == 200
    return token


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/remote/open"),
        ("POST", "/api/remote/close"),
        ("POST", "/api/remote/hold"),
        ("POST", "/api/remote/drive"),
        ("POST", "/api/remote/action"),
        ("GET", "/api/remote/state?token=x"),
    ],
)
def test_every_route_is_refused_while_the_remote_is_switched_off(live, method: str, path: str) -> None:  # noqa: ANN001
    base, config_path, _remote = live
    cfg = load_config(config_path)
    cfg.touch_remote.enabled = False
    save_config(cfg, config_path)
    status, data = _call(base, method, path, {"token": "x", "marker": 1} if method == "POST" else None)
    assert status == 403
    assert "switched off" in data["error"]


def test_a_hold_takes_a_controlled_marker(live) -> None:  # noqa: ANN001
    base, _path, remote = live
    token = _open(base)
    status, data = _call(base, "POST", "/api/remote/hold", {"token": token, "marker": 2})
    assert (status, data) == (200, {"success": True, "detection_off": False})
    assert remote.held_ids() == {2}


def test_a_hold_with_detection_on_says_detection_goes_off(live) -> None:  # noqa: ANN001
    base, config_path, _remote = live
    cfg = load_config(config_path)
    cfg.detection.enabled = True
    save_config(cfg, config_path)
    token = _open(base)
    assert _call(base, "POST", "/api/remote/hold", {"token": token, "marker": 1})[1]["detection_off"] is True


def test_a_hold_from_an_ended_session_is_404(live) -> None:  # noqa: ANN001
    base, _path, remote = live
    assert _call(base, "POST", "/api/remote/hold", {"token": "gone", "marker": 1})[0] == 404
    assert remote.held_ids() == frozenset()


def test_a_marker_this_station_does_not_control_cannot_be_held(live) -> None:  # noqa: ANN001
    base, _path, remote = live
    token = _open(base)
    status, data = _call(base, "POST", "/api/remote/hold", {"token": token, "marker": 7})
    assert status == 409
    assert "does not control marker 7" in data["error"]
    assert remote.held_ids() == frozenset()


def test_a_second_remote_takes_the_marker_and_the_first_gets_409(live) -> None:  # noqa: ANN001
    base, _path, _remote = live
    first = _holding(base)
    _holding(base)
    status, data = _call(base, "POST", "/api/remote/drive", {"token": first, "marker": 1, "sx": 0.5, "sy": 0.0})
    assert status == 409
    assert "Another remote" in data["error"]
    state = _call(base, "GET", f"/api/remote/state?token={first}")[1]
    assert (state["held"], state["lost"]) == (None, True)


def test_an_ended_session_answers_404(live) -> None:  # noqa: ANN001
    base, _path, _remote = live
    token = _holding(base)
    assert _call(base, "POST", "/api/remote/close", {"token": token})[0] == 200
    assert _call(base, "POST", "/api/remote/drive", {"token": token, "marker": 1})[0] == 404
    assert _call(base, "GET", f"/api/remote/state?token={token}")[0] == 404


def test_drive_reaches_the_frame(live) -> None:  # noqa: ANN001
    base, _path, remote = live
    token = _holding(base)
    body = {"token": token, "marker": 1, "sx": 0.25, "sy": -0.5, "z_notches": 3}
    assert _call(base, "POST", "/api/remote/drive", body) == (200, {"success": True})
    (frame,) = remote.take()
    assert (frame.stick, frame.z_notches) == ((0.25, -0.5), 3)


@pytest.mark.parametrize(
    ("action", "extra", "want"),
    [
        (RESET, {}, RemoteAction(RESET)),
        (PATHFINDER, {}, RemoteAction(PATHFINDER)),
        (GLIDE, {"x": 1, "y": -2.5, "z": 1.6}, RemoteAction(GLIDE, (1.0, -2.5, 1.6))),
    ],
)
def test_an_action_reaches_the_frame(live, action: str, extra: dict[str, Any], want: RemoteAction) -> None:  # noqa: ANN001
    base, _path, remote = live
    token = _holding(base)
    assert _call(base, "POST", "/api/remote/action", {"token": token, "marker": 1, "action": action, **extra})[0] == 200
    assert remote.take()[0].actions == (want,)


@pytest.mark.parametrize("raw", [b"not json", b"[1, 2]", b"null"])
def test_a_malformed_body_is_400(live, raw: bytes) -> None:  # noqa: ANN001
    base, _path, _remote = live
    assert _call(base, "POST", "/api/remote/drive", raw=raw)[0] == 400


@pytest.mark.parametrize("token", [None, 5, "", True])
def test_a_missing_or_wrong_type_token_is_400(live, token: Any) -> None:  # noqa: ANN001
    base, _path, _remote = live
    assert _call(base, "POST", "/api/remote/hold", {"token": token, "marker": 1})[0] == 400


@pytest.mark.parametrize("marker", [None, "1", True, 1.5])
def test_a_wrong_type_marker_is_400(live, marker: Any) -> None:  # noqa: ANN001
    base, _path, _remote = live
    token = _open(base)
    for route, body in (
        ("hold", {}),
        ("drive", {}),
        ("action", {"action": RESET}),
    ):
        assert _call(base, "POST", f"/api/remote/{route}", {"token": token, "marker": marker, **body})[0] == 400


@pytest.mark.parametrize(
    "fields",
    [
        {"sx": "abc"},
        {"sx": None},
        {"sy": True},
        {"sx": float("inf")},
        {"z_notches": 1.5},
        {"z_notches": "2"},
        {"z_notches": False},
        {"z_notches": 100000},
        {"sx": 10**400},
        {"z_notches": 10**400},
    ],
)
def test_a_wrong_type_or_out_of_range_drive_is_400(live, fields: dict[str, Any]) -> None:  # noqa: ANN001
    base, _path, _remote = live
    token = _holding(base)
    raw = json.dumps({"token": token, "marker": 1, **fields}).replace("Infinity", "1e999").encode()
    assert _call(base, "POST", "/api/remote/drive", raw=raw)[0] == 400


@pytest.mark.parametrize("action", ["jump", None, 3, ""])
def test_an_unknown_action_is_400(live, action: Any) -> None:  # noqa: ANN001
    base, _path, _remote = live
    token = _holding(base)
    assert _call(base, "POST", "/api/remote/action", {"token": token, "marker": 1, "action": action})[0] == 400


@pytest.mark.parametrize(
    "target",
    [
        {"x": 1, "y": 2},
        {"x": "1", "y": 2, "z": 3},
        {"x": True, "y": 2, "z": 3},
        {"x": 10**400, "y": 2, "z": 3},
        {"x": 1, "y": 2, "z": 10_000.5},
        {"x": -1.7e308, "y": 2, "z": 3},
    ],
)
def test_a_glide_without_three_numbers_is_400(live, target: dict[str, Any]) -> None:  # noqa: ANN001
    base, _path, _remote = live
    token = _holding(base)
    body = {"token": token, "marker": 1, "action": GLIDE, **target}
    assert _call(base, "POST", "/api/remote/action", body)[0] == 400


def test_a_glide_far_off_the_grid_is_taken_as_typed(live) -> None:  # noqa: ANN001
    base, _path, remote = live
    token = _holding(base)
    body = {"token": token, "marker": 1, "action": GLIDE, "x": 500, "y": -300, "z": 40}
    assert _call(base, "POST", "/api/remote/action", body)[0] == 200
    assert remote.take()[0].actions == (RemoteAction(GLIDE, (500.0, -300.0, 40.0)),)


def test_the_state_lists_the_controlled_markers_and_the_settings(live) -> None:  # noqa: ANN001
    base, config_path, _remote = live
    cfg = load_config(config_path)
    cfg.marker.max_speed = 2.5
    cfg.touch_remote.z_step_m = 0.1
    cfg.ui.unit_system = "imperial"
    save_config(cfg, config_path)
    token = _holding(base, 2)
    status, state = _call(base, "GET", f"/api/remote/state?token={token}")
    assert status == 200
    assert [(m["id"], m["pos"]) for m in state["markers"]] == [(1, [1.5, 3.0, 1.6]), (2, [-2.0, 4.0, 0.0])]
    assert state["held"] == 2
    assert state["lost"] is False
    assert state["max_speed"] == 2.5
    assert state["z_step_m"] == 0.1
    assert state["z_notch_px"] == 20
    assert state["unit_system"] == "imperial"
    assert state["recording"] is True
    assert state["locked"] == [2]
    assert state["inverted"] is False


def test_without_a_registry_every_route_is_refused(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    for cls in (discovery_module.BeaconSender, discovery_module.BeaconReceiver):
        monkeypatch.setattr(cls, "start", lambda self: None)
        monkeypatch.setattr(cls, "stop", lambda self: None)
    config_path = str(tmp_path / "config.toml")
    cfg = load_config(config_path)
    cfg.touch_remote.enabled = True
    save_config(cfg, config_path)
    with live_on_free_port(
        lambda port: ConfigWebServer(config_path=config_path, host="127.0.0.1", port=port, system_name="T")
    ) as (_server, base):
        assert _call(base, "POST", "/api/remote/open", {})[0] == 403


def test_the_index_shows_the_launcher_only_while_the_remote_is_on(live) -> None:  # noqa: ANN001
    base, config_path, _remote = live
    with urllib.request.urlopen(f"{base}/", timeout=5) as r:
        page = r.read().decode()
    assert 'id="remote-launcher"' in page
    assert "/assets/js/touch-remote.js" in page
    cfg = load_config(config_path)
    cfg.touch_remote.enabled = False
    save_config(cfg, config_path)
    with urllib.request.urlopen(f"{base}/", timeout=5) as r:
        page = r.read().decode()
    assert 'id="remote-launcher"' not in page
    assert "touch-remote.js" not in page
    assert 'id="touch-remote-enabled"' in page


def test_the_section_saves_the_switch_and_keeps_the_tuning(live) -> None:  # noqa: ANN001
    base, config_path, _remote = live
    cfg = load_config(config_path)
    cfg.touch_remote.stick_curve = 1.5
    save_config(cfg, config_path)
    req = urllib.request.Request(f"{base}/section/touch_remote", data=b"", method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        assert r.status == 200
    saved = load_config(config_path).touch_remote
    assert saved.enabled is False
    assert saved.stick_curve == 1.5


@pytest.mark.parametrize(
    ("before", "form", "refresh"), [(True, b"", "true"), (False, b"enabled=on", "true"), (True, b"enabled=on", None)]
)
def test_flipping_the_switch_reloads_the_page_for_the_launcher(
    live, before: bool, form: bytes, refresh: str | None
) -> None:  # noqa: ANN001
    base, config_path, _remote = live
    cfg = load_config(config_path)
    cfg.touch_remote.enabled = before
    save_config(cfg, config_path)
    req = urllib.request.Request(f"{base}/section/touch_remote", data=form, method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        assert r.headers.get("HX-Refresh") == refresh
