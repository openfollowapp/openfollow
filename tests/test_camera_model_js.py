# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The browser's camera model (static/js/camera-model.js) against scene/solver.py, run in node."""

from __future__ import annotations

import json
import math
import shutil
import subprocess  # nosec B404 - runs the local node binary on a fixed script
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from openfollow.lens_model import lens_warp_is_valid
from openfollow.scene.solver import (
    apply_overlay_distortion,
    invert_overlay_distortion,
    project_points,
    unproject_to_plane,
)

pytestmark = [pytest.mark.unit, pytest.mark.skipif(shutil.which("node") is None, reason="needs node")]

_MODULE = Path(__file__).parents[1] / "openfollow" / "web" / "static" / "js" / "camera-model.js"

# Each camera as the zones API sends it, with its picture size.
_CAMERAS = {
    "concert barrel": (
        {
            "pos_x": 0.0,
            "pos_y": 10.0,
            "pos_z": 9.0,
            "pitch": -48.0,
            "yaw": 180.0,
            "roll": 0.0,
            "fov": 82.5,
            "k1": -0.37,
            "k2": -0.17,
            "cx": 0.0,
            "cy": 0.0,
        },
        1280,
        720,
    ),
    "lens centre offset": (
        {
            "pos_x": 1.5,
            "pos_y": -8.0,
            "pos_z": 5.0,
            "pitch": -25.0,
            "yaw": 10.0,
            "roll": 3.0,
            "fov": 70.0,
            "k1": -0.2,
            "k2": 0.0,
            "cx": 0.05,
            "cy": -0.03,
        },
        1920,
        1080,
    ),
    "pincushion with a fold": (
        {
            "pos_x": -2.0,
            "pos_y": -6.0,
            "pos_z": 4.0,
            "pitch": -30.0,
            "yaw": -15.0,
            "roll": -2.0,
            "fov": 60.0,
            "k1": 0.12,
            "k2": 0.04,
            "cx": -0.02,
            "cy": 0.04,
        },
        1280,
        720,
    ),
    "pincushion without k2": (
        {
            "pos_x": 0.5,
            "pos_y": -6.0,
            "pos_z": 4.0,
            "pitch": -28.0,
            "yaw": 5.0,
            "roll": 0.0,
            "fov": 75.0,
            "k1": 0.2,
            "k2": 0.0,
            "cx": 0.0,
            "cy": 0.0,
        },
        1280,
        720,
    ),
    "centre shift only": (
        {
            "pos_x": 0.0,
            "pos_y": -7.0,
            "pos_z": 3.5,
            "pitch": -20.0,
            "yaw": 0.0,
            "roll": 0.0,
            "fov": 90.0,
            "k1": 0.0,
            "k2": 0.0,
            "cx": 0.1,
            "cy": 0.05,
        },
        1024,
        768,
    ),
    "pinhole": (
        {
            "pos_x": 3.0,
            "pos_y": -5.0,
            "pos_z": 6.0,
            "pitch": -40.0,
            "yaw": 30.0,
            "roll": 10.0,
            "fov": 50.0,
            "k1": 0.0,
            "k2": 0.0,
            "cx": 0.0,
            "cy": 0.0,
        },
        1280,
        720,
    ),
}

# A stage grid at three heights, and points far behind and above every camera here.
_WORLD = [[x, y, z] for x in np.linspace(-8, 8, 9) for y in np.linspace(-6, 12, 10) for z in (0.0, 0.6, 2.0)] + [
    [0.0, -40.0, 0.0],
    [0.0, 40.0, 0.0],
    [0.0, 0.0, 40.0],
    [40.0, 0.0, 30.0],
    [-40.0, 0.0, 30.0],
]
_HEIGHTS = (0.0, 0.6, -0.4)


def _params(cam: dict[str, float]) -> np.ndarray:
    return np.array([cam[k] for k in ("pos_x", "pos_y", "pos_z", "pitch", "yaw", "roll", "fov")], dtype=np.float64)


def _lens(cam: dict[str, float]) -> tuple[float, float, float, float]:
    return cam["k1"], cam["k2"], cam["cx"], cam["cy"]


def _python_project(cam: dict[str, float], w: int, h: int) -> list[list[float] | None]:
    screen = apply_overlay_distortion(project_points(_params(cam), np.array(_WORLD), w, h), w, h, *_lens(cam))
    return [[float(x), float(y)] if np.isfinite(x) and np.isfinite(y) else None for x, y in screen]


def _picture_grid(w: int, h: int) -> list[list[float]]:
    return [[float(x), float(y)] for x in np.linspace(0, w, 13) for y in np.linspace(0, h, 9)]


def _python_unproject(cam: dict[str, float], w: int, h: int, z: float) -> list[list[float] | None]:
    pinhole = invert_overlay_distortion(np.array(_picture_grid(w, h)), w, h, *_lens(cam))
    world = unproject_to_plane(_params(cam), pinhole, w, h, z)
    return [[float(p[0]), float(p[1])] if np.isfinite(p[0]) and np.isfinite(p[1]) else None for p in world]


def _node(cam: dict[str, float], w: int, h: int) -> Any:
    source = "\n".join(
        [
            "var window = globalThis;",
            _MODULE.read_text(encoding="utf-8"),
            f"var cam = new window.OpenFollow.CameraModel({json.dumps(cam)}, {w}, {h});",
            f"var world = {json.dumps(_WORLD)}, picture = {json.dumps(_picture_grid(w, h))};",
            f"var heights = {json.dumps(list(_HEIGHTS))};",
            "process.stdout.write(JSON.stringify({",
            "  project: world.map(function (p) { return cam.project(p); }),",
            "  unproject: heights.map(function (z) {",
            "    return picture.map(function (q) { return cam.unproject(q, z); });",
            "  }),",
            "}));",
        ]
    )
    node = shutil.which("node")
    assert node is not None
    done = subprocess.run([node, "-e", source], capture_output=True, text=True, timeout=10)  # nosec B603
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _same(js: list[Any], py: list[Any], tol: float) -> None:
    assert len(js) == len(py)
    for got, want in zip(js, py, strict=True):
        if want is None:
            assert got is None
            continue
        assert got is not None
        scale = max(1.0, abs(want[0]), abs(want[1]))
        assert math.dist(got, want) <= tol * scale, (got, want)


@pytest.mark.parametrize("name", list(_CAMERAS))
def test_the_browser_projects_world_points_as_the_station_does(name: str) -> None:
    cam, w, h = _CAMERAS[name]
    assert lens_warp_is_valid(*_lens(cam))
    out = _node(cam, w, h)
    want = _python_project(cam, w, h)
    # Both sides of the camera are in the set, so null is checked too.
    assert any(p is None for p in want) and sum(p is not None for p in want) > 20
    _same(out["project"], want, 1e-6)


@pytest.mark.parametrize("name", list(_CAMERAS))
def test_the_browser_finds_the_stage_point_under_a_picture_point_as_the_station_does(name: str) -> None:
    cam, w, h = _CAMERAS[name]
    out = _node(cam, w, h)
    for z, got in zip(_HEIGHTS, out["unproject"], strict=True):
        want = _python_unproject(cam, w, h, z)
        assert sum(p is not None for p in want) > 20
        _same(got, want, 1e-7)


def test_points_above_the_horizon_reach_no_stage() -> None:
    cam, w, h = _CAMERAS["concert barrel"]
    level = dict(cam, pitch=-5.0)
    out = _node(level, w, h)
    want = _python_unproject(level, w, h, 0.0)
    assert any(p is None for p in want)
    _same(out["unproject"][0], want, 1e-7)


def test_a_picture_point_past_the_lens_limit_reaches_no_stage() -> None:
    # A strong barrel whose limit radius sits inside the picture's corners: those corners have no pinhole point.
    cam = dict(_CAMERAS["concert barrel"][0], k1=-0.6, k2=-0.4)
    out = _node(cam, 1280, 720)
    want = _python_unproject(cam, 1280, 720, 0.0)
    corner = _picture_grid(1280, 720).index([0.0, 0.0])
    assert want[corner] is None and out["unproject"][0][corner] is None
    _same(out["unproject"][0], want, 1e-7)


def test_a_picture_point_past_a_pincushion_fold_reaches_no_stage() -> None:
    # Past the fold the lens map still has a value, but no pinhole point maps there.
    cam = dict(_CAMERAS["pincushion with a fold"][0], k1=0.3, k2=0.3, cx=0.0, cy=0.0)
    out = _node(cam, 1280, 720)
    want = _python_unproject(cam, 1280, 720, 0.0)
    corner = _picture_grid(1280, 720).index([0.0, 720.0])
    assert want[corner] is None and out["unproject"][0][corner] is None
    _same(out["unproject"][0], want, 1e-7)


def test_a_round_trip_lands_where_it_started() -> None:
    cam, w, h = _CAMERAS["pincushion with a fold"]
    source = "\n".join(
        [
            "var window = globalThis;",
            _MODULE.read_text(encoding="utf-8"),
            f"var cam = new window.OpenFollow.CameraModel({json.dumps(cam)}, {w}, {h});",
            "var q = cam.project([1.2, 2.5, 0.6]); var back = cam.unproject(q, 0.6);",
            "var seg = cam.segment([0, 0, 0], [2, 0, 0], 4);",
            "var direct = cam.project([1, 0, 0]);",
            "process.stdout.write(JSON.stringify({ back: back, seg: seg.length, mid: seg[2], direct: direct }));",
        ]
    )
    node = shutil.which("node")
    assert node is not None
    done = subprocess.run([node, "-e", source], capture_output=True, text=True, timeout=10)  # nosec B603
    assert done.returncode == 0, done.stderr
    out = json.loads(done.stdout)
    assert out["back"] == [pytest.approx(1.2, abs=1e-9), pytest.approx(2.5, abs=1e-9)]
    assert out["seg"] == 5 and out["mid"] == out["direct"]


@pytest.mark.parametrize(
    "value", ["null", "'abc'", "true", "NaN", "undefined"], ids=["null", "text", "bool", "nan", "missing"]
)
@pytest.mark.parametrize("field", ["pitch", "k1", "pos_z"])
def test_an_unreadable_camera_field_reads_as_zero(field: str, value: str) -> None:
    cam = _CAMERAS["concert barrel"][0]
    source = "\n".join(
        [
            "var window = globalThis;",
            _MODULE.read_text(encoding="utf-8"),
            f"var raw = {json.dumps(cam)}, zero = {json.dumps(dict(cam, **{field: 0.0}))};",
            f"raw.{field} = {value};",
            "var pts = [[0, 0, 0], [1, 4, 0.5], [-3, 7, 0]];",
            "var CM = window.OpenFollow.CameraModel, a = new CM(raw, 1280, 720), b = new CM(zero, 1280, 720);",
            "process.stdout.write(JSON.stringify({ a: pts.map(function (p) { return a.project(p); }),",
            "  b: pts.map(function (p) { return b.project(p); }) }));",
        ]
    )
    node = shutil.which("node")
    assert node is not None
    done = subprocess.run([node, "-e", source], capture_output=True, text=True, timeout=10)  # nosec B603
    assert done.returncode == 0, done.stderr
    out = json.loads(done.stdout)
    assert out["a"] == out["b"] and any(p is not None for p in out["a"])
