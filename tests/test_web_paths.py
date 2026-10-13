# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The path editor's routes: the paths, recording, marker positions, and the view over the picture."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

import pytest

import openfollow.web.discovery as discovery_module
from openfollow.configuration import MAX_PATHS, PathConfig, PathPointConfig, load_config, save_config
from openfollow.web.server import ConfigWebServer
from tests._ports import live_on_free_port

pytestmark = pytest.mark.integration

MARKERS = {2: (1.5, 3.0, 1.6), 3: (-2.0, 4.0, 0.0)}


class _Recorder:
    def __init__(self, config_path: str) -> None:
        self.calls: list[str | None] = []
        self.answer = True
        self.config_path = config_path
        # Each rename the station was told of, with the names the file held at that moment.
        self.renames: list[tuple[str, str, list[str]]] = []

    def __call__(self, name: str | None) -> bool:
        self.calls.append(name)
        return self.answer

    def renaming(self, old: str, new: str) -> None:
        self.renames.append((old, new, [p.name for p in load_config(self.config_path).paths.paths]))


@pytest.fixture()
def live(tmp_path, monkeypatch):  # noqa: ANN001, ANN201
    for cls in (discovery_module.BeaconSender, discovery_module.BeaconReceiver):
        monkeypatch.setattr(cls, "start", lambda self: None)
        monkeypatch.setattr(cls, "stop", lambda self: None)
    config_path = str(tmp_path / "config.toml")
    recorder = _Recorder(config_path)
    with live_on_free_port(
        lambda port: ConfigWebServer(
            config_path=config_path,
            host="127.0.0.1",
            port=port,
            system_name="TestSystem",
            # Marker 4 is listed but has gone by the time its height is asked for.
            marker_positions_provider=lambda: [*((m, x, y) for m, (x, y, _z) in MARKERS.items()), (4, 0.0, 0.0)],
            marker_position_provider=MARKERS.get,
            path_state_provider=lambda: {"recording": "cross", "locks": {2: "cross"}},
            path_recorder=recorder,
            path_renamer=recorder.renaming,
        )
    ) as (server, base):
        yield base, config_path, recorder


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


def _with_paths(config_path: str, *paths: PathConfig) -> None:
    cfg = load_config(config_path)
    cfg.paths.paths = list(paths)
    save_config(cfg, config_path)


def _cross(**kw: Any) -> PathConfig:
    return PathConfig(name="cross", points=[PathPointConfig(-3, 2, 0, 0), PathPointConfig(3, 2, 0, 0)], **kw)


class TestList:
    def test_the_list_carries_the_paths_and_what_the_station_is_doing_with_them(self, live) -> None:  # noqa: ANN001
        base, config_path, _ = live
        _with_paths(config_path, _cross(active=True))
        cfg = load_config(config_path)
        cfg.grid.z_offset = 0.4  # the stage floor, where a click in the picture adds a point
        save_config(cfg, config_path)
        status, data = _call(base, "GET", "/api/paths")
        assert status == 200
        assert [p["name"] for p in data["paths"]] == ["cross"]
        assert (data["recording"], data["locks"]) == ("cross", {"2": "cross"})
        assert (data["slide_time"], data["show_paths"], data["floor_z"]) == (1.0, True, 0.4)
        assert data["markers"] == [2, 3, 4]
        assert (data["max_paths"], data["max_points"]) == (32, 100)


class TestCreate:
    def test_a_new_path_without_a_name_gets_one(self, live) -> None:  # noqa: ANN001
        base, config_path, _ = live
        assert _call(base, "POST", "/api/paths", {}) == (200, {"success": True, "name": "path-1"})
        assert _call(base, "POST", "/api/paths", {}) == (200, {"success": True, "name": "path-2"})
        assert [p.active for p in load_config(config_path).paths.paths] == [False, False]

    def test_a_named_path_keeps_its_name(self, live) -> None:  # noqa: ANN001
        base, config_path, _ = live
        assert _call(base, "POST", "/api/paths", {"name": "Cross"})[1]["name"] == "Cross"

    @pytest.mark.parametrize(
        ("name", "error"),
        [
            ("cross", "A path called Cross already exists."),
            ("!!", "Use letters"),
            # A URL resolves a name of dots alone away, so the path could never be reached.
            ("..", "Use letters, digits, '.', '-' or '_' in a path name, and not dots alone."),
        ],
    )
    def test_a_name_that_clashes_or_has_nothing_usable_is_refused(self, live, name: str, error: str) -> None:  # noqa: ANN001
        base, config_path, _ = live
        _with_paths(config_path, PathConfig(name="Cross"))
        status, data = _call(base, "POST", "/api/paths", {"name": name})
        assert status == 400
        assert data["error"].startswith(error)

    def test_no_more_paths_than_the_cap(self, live) -> None:  # noqa: ANN001
        base, config_path, _ = live
        _with_paths(config_path, *[PathConfig(name=f"p{i}") for i in range(MAX_PATHS)])
        assert _call(base, "POST", "/api/paths", {})[0] == 409

    @pytest.mark.parametrize("raw", [b"[1, 2]", b"not json"])
    def test_a_body_that_is_not_an_object_is_refused(self, live, raw: bytes) -> None:  # noqa: ANN001
        base, _, _ = live
        assert _call(base, "POST", "/api/paths", raw=raw)[0] == 400


class TestUpdate:
    def test_points_and_options_are_replaced(self, live) -> None:  # noqa: ANN001
        base, config_path, _ = live
        _with_paths(config_path, _cross())
        status, data = _call(
            base,
            "PUT",
            "/api/paths/CROSS",
            {"points": [{"x": 1, "y": 2, "z": 0.5}, {"x": 4, "y": 2}], "closed": "no", "release_at_end": True},
        )
        assert status == 200
        path = load_config(config_path).paths.paths[0]
        assert [(p.x, p.z, p.radius) for p in path.points] == [(1.0, 0.5, 0.5), (4.0, 0.0, 0.5)]
        assert (path.closed, path.release_at_end) == (False, True)
        assert data["path"]["name"] == "cross"

    def test_a_rename_keeps_to_the_name_rules(self, live) -> None:  # noqa: ANN001
        base, config_path, _ = live
        _with_paths(config_path, _cross(), PathConfig(name="loop"))
        assert _call(base, "PUT", "/api/paths/cross", {"name": "Down Stage"})[1]["path"]["name"] == "DownStage"
        assert _call(base, "PUT", "/api/paths/DownStage", {"name": "LOOP"})[0] == 400
        assert _call(base, "PUT", "/api/paths/DownStage", {"name": "."})[0] == 400
        assert _call(base, "PUT", "/api/paths/DownStage", {"name": "DownStage"})[0] == 200
        assert [p.name for p in load_config(config_path).paths.paths] == ["DownStage", "loop"]

    def test_a_rename_tells_the_station_before_it_is_saved(self, live) -> None:  # noqa: ANN001
        base, config_path, recorder = live
        _with_paths(config_path, _cross(), PathConfig(name="loop"))
        _call(base, "PUT", "/api/paths/cross", {"name": "Down Stage"})
        assert recorder.renames == [("cross", "DownStage", ["cross", "loop"])]
        assert [p.name for p in load_config(config_path).paths.paths] == ["DownStage", "loop"]

    @pytest.mark.parametrize(
        "body", [{"name": "cross"}, {"name": "LOOP"}, {"closed": True}, {"name": "ds", "points": "x"}]
    )
    def test_no_rename_is_told_when_none_is_saved(self, live, body: dict) -> None:  # noqa: ANN001
        base, config_path, recorder = live
        _with_paths(config_path, _cross(), PathConfig(name="loop"))
        _call(base, "PUT", "/api/paths/cross", body)
        assert recorder.renames == []

    def test_points_that_are_not_a_list_are_refused(self, live) -> None:  # noqa: ANN001
        base, config_path, _ = live
        _with_paths(config_path, _cross())
        assert _call(base, "PUT", "/api/paths/cross", {"points": "x"})[0] == 400
        assert len(load_config(config_path).paths.paths[0].points) == 2

    def test_an_unknown_path_is_not_found_on_every_route(self, live) -> None:  # noqa: ANN001
        base, _, _ = live
        assert _call(base, "PUT", "/api/paths/nope", {})[0] == 404
        assert _call(base, "POST", "/api/paths/nope/active", {"active": True})[0] == 404
        assert _call(base, "DELETE", "/api/paths/nope")[0] == 404

    @pytest.mark.parametrize(("method", "path"), [("PUT", "/api/paths/cross"), ("POST", "/api/paths/cross/active")])
    def test_a_body_that_is_not_an_object_is_refused(self, live, method: str, path: str) -> None:  # noqa: ANN001
        base, config_path, _ = live
        _with_paths(config_path, _cross())
        assert _call(base, method, path, raw=b"[]")[0] == 400


class TestActiveAndDelete:
    def test_a_path_is_switched_on_and_off(self, live) -> None:  # noqa: ANN001
        base, config_path, _ = live
        _with_paths(config_path, _cross())
        assert _call(base, "POST", "/api/paths/cross/active", {"active": True}) == (
            200,
            {"success": True, "active": True},
        )
        assert load_config(config_path).paths.paths[0].active is True
        _call(base, "POST", "/api/paths/cross/active", {"active": "off"})
        assert load_config(config_path).paths.paths[0].active is False

    def test_a_path_is_deleted(self, live) -> None:  # noqa: ANN001
        base, config_path, _ = live
        _with_paths(config_path, _cross(), PathConfig(name="loop"))
        assert _call(base, "DELETE", "/api/paths/cross")[0] == 200
        assert [p.name for p in load_config(config_path).paths.paths] == ["loop"]


class TestRecord:
    def test_record_and_stop_reach_the_station(self, live) -> None:  # noqa: ANN001
        base, config_path, recorder = live
        _with_paths(config_path, _cross())
        assert _call(base, "POST", "/api/paths/cross/record", {"on": True}) == (
            200,
            {"success": True, "recording": "cross"},
        )
        assert _call(base, "POST", "/api/paths/cross/record", {"on": False})[1]["recording"] is None
        assert recorder.calls == ["cross", None]

    def test_a_refused_recording_says_so(self, live) -> None:  # noqa: ANN001
        base, config_path, recorder = live
        _with_paths(config_path, _cross())
        recorder.answer = False
        assert _call(base, "POST", "/api/paths/cross/record", {"on": True}) == (
            409,
            {"error": "Paths are switched off."},
        )

    @pytest.mark.parametrize("on", [True, False])
    def test_recording_into_an_unknown_path_is_not_found(self, live, on: bool) -> None:  # noqa: ANN001
        base, config_path, recorder = live
        _with_paths(config_path, _cross())
        assert _call(base, "POST", "/api/paths/nope/record", {"on": on}) == (404, {"error": "No path called nope."})
        assert recorder.calls == []

    def test_a_body_that_is_not_an_object_is_refused(self, live) -> None:  # noqa: ANN001
        base, _, recorder = live
        assert _call(base, "POST", "/api/paths/cross/record", raw=b"1")[0] == 400
        assert recorder.calls == []


class TestMarkerPosition:
    def test_a_marker_s_position_is_read_with_its_height(self, live) -> None:  # noqa: ANN001
        base, _, _ = live
        assert _call(base, "GET", "/api/paths/marker/2") == (200, {"x": 1.5, "y": 3.0, "z": 1.6})

    def test_a_marker_without_a_position_is_not_found(self, live) -> None:  # noqa: ANN001
        base, _, _ = live
        assert _call(base, "GET", "/api/paths/marker/9")[0] == 404


class TestView:
    def test_paths_and_markers_are_projected_into_the_picture(self, live) -> None:  # noqa: ANN001
        base, config_path, _ = live
        _with_paths(config_path, _cross(active=True, release_at_end=True))
        status, data = _call(base, "POST", "/api/paths/view", {"w": 1920, "h": 1080})
        assert status == 200
        [view] = data["paths"]
        assert view["name"] == "cross"
        assert len(view["line"]) > 2 and all(pt is not None for pt in view["line"])
        assert view["points"][0][0] < view["points"][1][0]
        assert [e["releases"] for e in view["ends"]] == [False, True]
        assert view["ends"][1]["at"] == view["points"][1]
        assert sorted(m["id"] for m in data["markers"]) == [2, 3]

    @pytest.mark.parametrize("corner_active", [True, False])
    def test_an_end_on_an_active_corner_point_is_drawn_on_to_its_arc(self, live, corner_active: bool) -> None:  # noqa: ANN001
        base, config_path, _ = live
        corner = PathConfig(
            name="corner",
            active=corner_active,
            points=[PathPointConfig(-3, 2, 0, 0), PathPointConfig(0, 2, 0, 0.5), PathPointConfig(0, 5, 0, 0)],
        )
        branch = PathConfig(name="branch", active=True, points=[PathPointConfig(2, 0, 0), PathPointConfig(0, 2, 0)])
        _with_paths(config_path, corner, branch)
        view = _call(base, "POST", "/api/paths/view", {"w": 1920, "h": 1080})[1]["paths"][1]
        assert view["ends"][1]["at"] == view["line"][-1]
        # Carried on, the line ends on the arc beside the point; with the corner's path off, at the point.
        assert (view["line"][-1] != view["points"][-1]) is corner_active

    def test_what_the_camera_cannot_see_is_null(self, live) -> None:  # noqa: ANN001
        base, config_path, _ = live
        behind = PathConfig(name="behind", points=[PathPointConfig(-1, -30, 0), PathPointConfig(1, -30, 0)])
        _with_paths(config_path, behind, PathConfig(name="empty"))
        data = _call(base, "POST", "/api/paths/view", {"w": 1920, "h": 1080})[1]
        assert data["paths"][0]["points"] == [None, None]
        assert data["paths"][1] == {"name": "empty", "line": [], "points": [], "ends": []}

    def test_a_single_point_is_its_own_line(self, live) -> None:  # noqa: ANN001
        base, config_path, _ = live
        _with_paths(config_path, PathConfig(name="dot", points=[PathPointConfig(0, 3, 0)]))
        view = _call(base, "POST", "/api/paths/view", {"w": 1920, "h": 1080})[1]["paths"][0]
        assert view["line"] == view["points"]
        assert len(view["ends"]) == 1

    @pytest.mark.parametrize("body", [{}, {"w": "x", "h": 1}, {"w": 0, "h": 1080}])
    def test_a_picture_size_that_is_not_one_is_refused(self, live, body: dict) -> None:  # noqa: ANN001
        base, _, _ = live
        assert _call(base, "POST", "/api/paths/view", body)[0] == 400

    def test_a_body_that_is_not_an_object_is_refused(self, live) -> None:  # noqa: ANN001
        base, _, _ = live
        assert _call(base, "POST", "/api/paths/view", raw=b"[]")[0] == 400


class TestUnproject:
    def test_a_picture_point_maps_back_to_the_stage_position_it_shows(self, live) -> None:  # noqa: ANN001
        base, config_path, _ = live
        _with_paths(config_path, _cross())
        seen = _call(base, "POST", "/api/paths/view", {"w": 1920, "h": 1080})[1]["paths"][0]["points"][0]
        status, data = _call(
            base, "POST", "/api/paths/unproject", {"x": seen[0], "y": seen[1], "z": 0, "w": 1920, "h": 1080}
        )
        assert status == 200
        assert (data["x"], data["y"], data["z"]) == (pytest.approx(-3.0), pytest.approx(2.0), 0.0)

    def test_a_point_above_the_horizon_does_not_reach_the_stage(self, live) -> None:  # noqa: ANN001
        base, config_path, _ = live
        cfg = load_config(config_path)
        cfg.camera.pitch = 0.0  # level: the top half of the picture is sky
        save_config(cfg, config_path)
        status, data = _call(base, "POST", "/api/paths/unproject", {"x": 960, "y": 0, "z": 0, "w": 1920, "h": 1080})
        assert status == 422
        assert "does not reach the stage" in data["error"]

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"x": "a", "y": 1, "z": 0, "w": 1920, "h": 1080},
            {"x": float("nan"), "y": 1, "z": 0, "w": 1920, "h": 1080},
        ],
    )
    def test_numbers_that_are_not_numbers_are_refused(self, live, body: dict) -> None:  # noqa: ANN001
        base, _, _ = live
        assert _call(base, "POST", "/api/paths/unproject", raw=json.dumps(body).encode())[0] == 400

    def test_a_body_that_is_not_an_object_is_refused(self, live) -> None:  # noqa: ANN001
        base, _, _ = live
        assert _call(base, "POST", "/api/paths/unproject", raw=b"[]")[0] == 400


def test_the_settings_form_saves_visibility_and_slide_time(live) -> None:  # noqa: ANN001
    base, config_path, _ = live
    req = urllib.request.Request(
        f"{base}/section/paths",
        data=b"slide_time=2.5",
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        html = r.read().decode().strip()
    # The form swaps itself out (hx-swap outerHTML): an editor in the answer would land beside the page's own.
    assert html.startswith('<form id="paths-section"') and html.endswith("</form>")
    assert html.count("<form") == 1
    assert "path-editor-section" not in html
    saved = load_config(config_path).paths
    assert (saved.slide_time, saved.show_paths) == (2.5, False)


def test_the_page_has_one_path_editor(live) -> None:  # noqa: ANN001
    base, _, _ = live
    with urllib.request.urlopen(f"{base}/", timeout=5) as r:
        html = r.read().decode()
    assert html.count('id="paths-section"') == 1
    assert html.count('id="path-editor-section"') == 1


def test_a_server_without_a_station_behind_it_answers_empty(tmp_path) -> None:  # noqa: ANN001
    server = ConfigWebServer(config_path=str(tmp_path / "config.toml"), host="127.0.0.1", port=0, system_name="T")
    assert server.get_marker_position(2) is None
    assert server.get_path_state() == {"recording": None, "locks": {}}
    assert server.record_path("cross") is False
    server.path_renaming("cross", "ds")  # nobody to tell
