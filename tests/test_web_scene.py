# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The camera-picture routes the plan editors share, and the ground grid's view."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

import numpy as np
import pytest

import openfollow.web.discovery as discovery_module
from openfollow.configuration import load_config, save_config
from openfollow.runtime.overlay_draw_scene import project
from openfollow.scene.ground_grid import grid_segments_in_polygon, main_grid_steps
from openfollow.web.server import ConfigWebServer
from tests._ports import live_on_free_port

pytestmark = pytest.mark.integration

_W, _H = 1920, 1080
_SQUARE = [[0.0, 2.0], [2.0, 2.0], [2.0, 4.0], [0.0, 4.0]]


@pytest.fixture()
def live(tmp_path, monkeypatch):  # noqa: ANN001, ANN201
    for cls in (discovery_module.BeaconSender, discovery_module.BeaconReceiver):
        monkeypatch.setattr(cls, "start", lambda self: None)
        monkeypatch.setattr(cls, "stop", lambda self: None)
    config_path = str(tmp_path / "config.toml")
    with live_on_free_port(
        lambda port: ConfigWebServer(config_path=config_path, host="127.0.0.1", port=port, system_name="TestSystem")
    ) as (_server, base):
        yield base, config_path


def _call(base: str, path: str, body: Any = None, raw: bytes | None = None, method: str = "POST") -> tuple[int, Any]:
    data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
    req = urllib.request.Request(
        f"{base}{path}", data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def _camera(config_path: str, *, k1: float = 0.0, pitch: float | None = None) -> np.ndarray:
    cfg = load_config(config_path)
    cfg.camera.lens_k1 = k1
    if pitch is not None:
        cfg.camera.pitch = pitch
    save_config(cfg, config_path)
    c = cfg.camera
    return np.array([c.pos_x, c.pos_y, c.pos_z, c.pitch, c.yaw, c.roll, c.fov])


class TestProject:
    def test_points_land_where_the_operator_screen_draws_them(self, live) -> None:  # noqa: ANN001
        base, config_path = live
        cam = _camera(config_path)
        world = [[0.0, 3.0, 0.0], [1.5, 4.0, 0.5]]
        status, data = _call(base, "/api/scene/project", {"w": _W, "h": _H, "polylines": [world], "points": world})
        assert status == 200
        expected = project(cam, world, _W, _H).tolist()
        assert data["points"] == [[pytest.approx(v) for v in p] for p in expected]
        assert data["polylines"] == [data["points"]]

    def test_a_lens_centre_shifts_what_the_picture_shows(self, live) -> None:  # noqa: ANN001
        base, config_path = live
        cfg = load_config(config_path)
        cfg.camera.lens_cx, cfg.camera.lens_cy = 0.05, -0.02
        save_config(cfg, config_path)
        cam = _camera(config_path)
        world = [[0.0, 3.0, 0.0]]
        data = _call(base, "/api/scene/project", {"w": _W, "h": _H, "points": world})[1]
        half_diag = 0.5 * np.hypot(_W, _H)
        expected = project(cam, world, _W, _H)[0] + [0.05 * half_diag, -0.02 * half_diag]
        assert data["points"][0] == [pytest.approx(v) for v in expected]

    def test_a_lens_bows_polylines_but_not_single_points(self, live) -> None:  # noqa: ANN001
        base, config_path = live
        _camera(config_path, k1=-0.2)
        line = [[-4.0, 2.0, 0.0], [4.0, 2.0, 0.0]]
        data = _call(base, "/api/scene/project", {"w": _W, "h": _H, "polylines": [line], "points": line})[1]
        # One segment in 24 chords, as the Operator Screen draws it.
        assert len(data["polylines"][0]) == 25
        assert len(data["points"]) == 2
        assert data["polylines"][0][0] == data["points"][0]

    def test_a_point_behind_the_camera_is_null(self, live) -> None:  # noqa: ANN001
        base, config_path = live
        _camera(config_path)
        data = _call(base, "/api/scene/project", {"w": _W, "h": _H, "points": [[0.0, -50.0, 0.0]]})[1]
        assert data["points"] == [None]

    @pytest.mark.parametrize(
        "body",
        [
            {"w": _W, "h": _H, "polylines": "abc"},
            {"w": _W, "h": _H, "polylines": [None]},
            {"w": _W, "h": _H, "polylines": [[[1, 2]]]},
            {"w": _W, "h": _H, "polylines": [[[1, 2, "z"]]]},
            {"w": _W, "h": _H, "polylines": [[[1, 2, True]]]},
            {"w": _W, "h": _H, "points": [[1, 2, float("inf")]]},
            {"w": True, "h": _H, "points": []},
            {"w": 0, "h": _H, "points": []},
            {"h": _H, "points": []},
            {"w": _W, "h": _H, "points": [[None, 0, 0]]},
            {"w": _W, "h": _H, "points": [[{}, 0, 0]]},
            {"w": _W, "h": _H, "points": [[10**400, 0, 0]]},
            {"w": 10**400, "h": _H, "points": []},
        ],
    )
    def test_malformed_input_is_refused(self, live, body: dict) -> None:  # noqa: ANN001
        base, _ = live
        assert _call(base, "/api/scene/project", raw=json.dumps(body).encode())[0] == 400

    def test_too_many_points_are_refused(self, live) -> None:  # noqa: ANN001
        base, _ = live
        line = [[float(i), 0.0, 0.0] for i in range(5000)]
        assert _call(base, "/api/scene/project", {"w": _W, "h": _H, "polylines": [line]})[0] == 400

    @pytest.mark.parametrize(("count", "status"), [(1666, 200), (1667, 400)])
    def test_a_request_may_carry_1666_points(self, live, count: int, status: int) -> None:  # noqa: ANN001
        base, config_path = live
        _camera(config_path, k1=-0.2)
        line = [[0.001 * i, 3.0, 0.0] for i in range(count)]
        assert _call(base, "/api/scene/project", {"w": _W, "h": _H, "polylines": [line]})[0] == status

    def test_a_body_that_is_not_an_object_is_refused(self, live) -> None:  # noqa: ANN001
        base, _ = live
        assert _call(base, "/api/scene/project", raw=b"[]")[0] == 400


class TestUnproject:
    @pytest.mark.parametrize("z", [0.0, -0.8])
    def test_a_picture_point_maps_back_to_the_stage_position_it_shows(self, live, z: float) -> None:  # noqa: ANN001
        base, config_path = live
        _camera(config_path, k1=-0.1)
        seen = _call(base, "/api/scene/project", {"w": _W, "h": _H, "points": [[-3.0, 2.0, z]]})[1]["points"][0]
        status, data = _call(base, "/api/scene/unproject", {"x": seen[0], "y": seen[1], "z": z, "w": _W, "h": _H})
        assert status == 200
        assert (data["x"], data["y"], data["z"]) == (pytest.approx(-3.0), pytest.approx(2.0), z)

    def test_a_point_above_the_horizon_does_not_reach_the_stage(self, live) -> None:  # noqa: ANN001
        base, config_path = live
        _camera(config_path, pitch=0.0)  # level: the top half of the picture is sky
        status, data = _call(base, "/api/scene/unproject", {"x": 960, "y": 0, "z": 0, "w": _W, "h": _H})
        assert status == 422
        assert "does not reach the stage" in data["error"]

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"x": "a", "y": 1, "z": 0, "w": _W, "h": _H},
            {"x": float("nan"), "y": 1, "z": 0, "w": _W, "h": _H},
            {"x": True, "y": 1, "z": 0, "w": _W, "h": _H},
            {"x": 1, "y": 1, "z": None, "w": _W, "h": _H},
            {"x": 10**400, "y": 1, "z": 0, "w": _W, "h": _H},
        ],
    )
    def test_numbers_that_are_not_numbers_are_refused(self, live, body: dict) -> None:  # noqa: ANN001
        base, _ = live
        assert _call(base, "/api/scene/unproject", raw=json.dumps(body).encode())[0] == 400

    def test_a_body_that_is_not_an_object_is_refused(self, live) -> None:  # noqa: ANN001
        base, _ = live
        assert _call(base, "/api/scene/unproject", raw=b"[]")[0] == 400


class TestGroundGridView:
    def test_lines_are_the_operator_screen_s_clipped_lines(self, live) -> None:  # noqa: ANN001
        base, config_path = live
        cam = _camera(config_path)
        g = load_config(config_path).grid
        status, data = _call(base, "/api/ground_grid/view", {"w": _W, "h": _H, "vertices": _SQUARE, "z": -0.8})
        assert status == 200
        step_x, step_y = main_grid_steps(g.width, g.depth, g.spacing, 200)
        segs = grid_segments_in_polygon(
            _SQUARE, g.x_offset - g.width / 2, g.y_offset - g.depth / 2, step_x, step_y, -0.8, 200
        )
        assert len(data["lines"]) == len(segs)
        assert data["lines"][0] == [[pytest.approx(v) for v in p] for p in project(cam, segs[0], _W, _H).tolist()]
        corners = project(cam, [[*v, -0.8] for v in _SQUARE], _W, _H).tolist()
        assert data["corners"] == [[pytest.approx(v) for v in p] for p in corners]
        assert len(data["outline"]) == 5 and data["outline"][-1] == data["outline"][0]

    def test_fewer_than_three_corners_draw_no_outline(self, live) -> None:  # noqa: ANN001
        base, _ = live
        data = _call(base, "/api/ground_grid/view", {"w": _W, "h": _H, "vertices": _SQUARE[:2], "z": 0})[1]
        assert data["outline"] == [] and data["lines"] == []
        assert len(data["corners"]) == 2

    @pytest.mark.parametrize(
        "body",
        [
            {"w": _W, "h": _H, "vertices": _SQUARE, "z": True},
            {"w": _W, "h": _H, "vertices": _SQUARE, "z": None},
            {"w": _W, "h": _H, "vertices": _SQUARE, "z": "low"},
            {"w": _W, "h": _H, "vertices": _SQUARE, "z": "nan"},
            {"w": _W, "h": _H, "vertices": _SQUARE},
            {"w": _W, "h": _H, "vertices": "abc", "z": 0},
            {"w": _W, "h": _H, "vertices": [[0, 0, 0]], "z": 0},
            {"w": _W, "h": _H, "vertices": [[0, "y"]], "z": 0},
            {"w": _W, "h": _H, "vertices": [[None, 0]], "z": 0},
            {"w": _W, "h": _H, "vertices": [[10**400, 0]], "z": 0},
            {"w": _W, "h": _H, "vertices": _SQUARE, "z": 10**400},
        ],
    )
    def test_malformed_input_is_refused(self, live, body: dict) -> None:  # noqa: ANN001
        base, _ = live
        assert _call(base, "/api/ground_grid/view", raw=json.dumps(body).encode())[0] == 400


def test_the_ground_grid_view_refuses_a_body_that_is_not_an_object(live) -> None:  # noqa: ANN001
    base, _ = live
    assert _call(base, "/api/ground_grid/view", raw=b"[]")[0] == 400


def test_the_zone_editor_learns_the_floor_height(live) -> None:  # noqa: ANN001
    base, config_path = live
    cfg = load_config(config_path)
    cfg.grid.z_offset = 0.4
    save_config(cfg, config_path)
    status, data = _call(base, "/api/zones", method="GET")
    assert status == 200
    assert data["grid"]["z_offset"] == 0.4
