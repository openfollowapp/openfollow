# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The Setup Wizard's solve and saved-lens routes: ``/api/wizard/solve`` and ``/api/wizard/lens/calibrations``."""

from __future__ import annotations

import copy
import json
import math
import re
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest

import openfollow
import openfollow.web.discovery as discovery_module
from openfollow.scene import calibration_points as cp
from openfollow.scene.solver import apply_overlay_distortion, project_points
from openfollow.web import lens_calibrations
from openfollow.web.server import ConfigWebServer
from tests._ports import live_on_free_port

pytestmark = pytest.mark.integration

W, H = 1920, 1080
TRUE = np.array([0.0, 13.0, 12.0, -48.0, 180.0, 0.0, 70.0])
HINT = [0.0, 13.0, 12.0]
CORNERS = [(6.0, 0.0, 0.0), (-6.0, 0.0, 0.0), (-6.0, 6.0, 0.0), (6.0, 6.0, 0.0)]
# A strong lens whose centre sits off the frame centre.
LENS = (-0.3, -0.05, 0.05, -0.03)
_PINHOLE = (0.0, 0.0, 0.0, 0.0)


@pytest.fixture(scope="module")
def station(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[ConfigWebServer, str]]:
    """One station for the module: the solve keeps no state but the turns, which each test names afresh."""
    with pytest.MonkeyPatch.context() as mp:
        for cls in (discovery_module.BeaconSender, discovery_module.BeaconReceiver):
            mp.setattr(cls, "start", lambda self: None)
            mp.setattr(cls, "stop", lambda self: None)
        config_path = tmp_path_factory.mktemp("station") / "config.toml"
        with live_on_free_port(
            lambda port: ConfigWebServer(config_path=str(config_path), host="127.0.0.1", port=port)
        ) as (server, base):
            yield server, base


@pytest.fixture
def base(station: tuple[ConfigWebServer, str]) -> str:
    return station[1]


@pytest.fixture
def lens_dir(station: tuple[ConfigWebServer, str]) -> Iterator[Path]:
    directory = Path(station[0].config_path).parent / "lens-calibrations"
    for path in directory.glob("*.json") if directory.is_dir() else ():
        path.unlink()
    yield directory


def _send(base: str, path: str, payload: Any = None, *, raw: bytes | None = None) -> tuple[int, Any]:
    data = raw if raw is not None else json.dumps(payload).encode()
    method = "GET" if payload is None and raw is None else "POST"
    req = urllib.request.Request(
        f"{base}{path}",
        data=None if method == "GET" else data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def _solve(base: str, body: Any) -> tuple[int, Any]:
    return _send(base, "/api/wizard/solve", body)


def _pin(place, lens=_PINHOLE) -> list[float]:
    seen = project_points(TRUE, np.array([place], dtype=np.float64), W, H)
    return apply_overlay_distortion(seen, W, H, *lens)[0].tolist()


def _known(mark_id: int, place, lens=_PINHOLE, *, pinned: bool = True) -> dict[str, Any]:
    x, y, z = place
    return {"id": mark_id, "kind": "known", "x": x, "y": y, "z": z, "pin": _pin(place, lens) if pinned else None}


def _triangulated(mark_id: int, place, a: tuple[int, tuple], b: tuple[int, tuple], lens=_PINHOLE) -> dict[str, Any]:
    return {
        "id": mark_id,
        "kind": "triangulated",
        "from_a": a[0],
        "from_b": b[0],
        "distance_a": float(np.linalg.norm(np.subtract(place, a[1]))),
        "distance_b": float(np.linalg.norm(np.subtract(place, b[1]))),
        "z": place[2],
        "pin": _pin(place, lens),
    }


def _simple_marks(lens=_PINHOLE) -> list[dict[str, Any]]:
    return [_known(n, place, lens) for n, place in enumerate(CORNERS, start=1)]


P5, P6 = (2.0, 4.0, 0.0), (-3.0, 5.0, 0.5)


def _advanced_marks(lens=_PINHOLE) -> list[dict[str, Any]]:
    return [
        _known(1, CORNERS[0], lens),
        _known(2, CORNERS[1], lens),
        _triangulated(5, P5, (1, CORNERS[0]), (2, CORNERS[1]), lens),
        _triangulated(6, P6, (5, P5), (1, CORNERS[0]), lens),
    ]


def _body(mode: str = "simple", marks=None, *, hint=None, lens=_PINHOLE, **extra: Any) -> dict[str, Any]:
    return {
        "image_width": W,
        "image_height": H,
        "mode": mode,
        "hint": hint,
        "saved_fov": 60.0,
        "marks": _simple_marks(lens) if marks is None else marks,
        "camera": dict(zip(("lens_k1", "lens_k2", "lens_cx", "lens_cy"), lens, strict=True)),
        **extra,
    }


def _camera_vector(camera: dict[str, float]) -> np.ndarray:
    return np.array([camera[k] for k in ("pos_x", "pos_y", "pos_z", "pitch", "yaw", "roll", "fov")])


def _page() -> str:
    return f"page-{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# /api/wizard/solve – answers
# ---------------------------------------------------------------------------


def test_simple_pins_on_a_curved_lens_give_the_camera_and_the_seen_places_on_the_snapshot_as_shown(base: str) -> None:
    status, data = _solve(base, _body(lens=LENS))
    assert status == 200
    np.testing.assert_allclose(_camera_vector(data["camera"]), TRUE, atol=1e-3)
    assert set(data) == {"camera", "marks", "count", "largest_error", "steep", "reasons"}
    assert (data["count"], data["reasons"]) == (4, [])
    assert data["largest_error"] == pytest.approx(0.0, abs=1e-3)
    pins = [m["pin"] for m in _simple_marks(LENS)]
    for answer, pin, place in zip(data["marks"], pins, CORNERS, strict=True):
        assert set(answer) == {"id", "position", "reason", "seen", "error", "distance_error"}
        assert answer["seen"] == pytest.approx(pin, abs=1e-3)  # on the curved snapshot, not the pinhole one
        assert answer["error"] == pytest.approx(0.0, abs=1e-3)
        assert answer["position"] == pytest.approx(list(place))
        assert (answer["reason"], answer["distance_error"]) == ("", None)


def test_errors_are_measured_on_the_curved_snapshot_as_shown(base: str) -> None:
    """Near the edge the curve shrinks distances, so a pinhole error would read larger than the one shown."""
    edge = (7.5, 3.0, 0.0)
    marks = [*_simple_marks(LENS), _known(5, edge, LENS), _known(6, (0.0, 3.0, 0.0), LENS)]
    marks[4]["pin"] = [marks[4]["pin"][0] - 40.0, marks[4]["pin"][1]]
    status, data = _solve(base, _body("advanced", marks, hint=HINT, lens=LENS))
    assert status == 200
    pins = {m["id"]: m["pin"] for m in marks}
    for answer in data["marks"]:
        assert answer["error"] == pytest.approx(math.dist(answer["seen"], pins[answer["id"]]))
    assert {m["id"]: m for m in data["marks"]}[5]["error"] > cp.FLAG_PX
    assert data["largest_error"] == max(m["error"] for m in data["marks"])


def test_simple_pins_on_a_curved_lens_read_as_pinhole_miss_the_camera(base: str) -> None:
    """The pins are undistorted with the posted lens: without it the same pins give another camera."""
    status, data = _solve(base, _body(marks=_simple_marks(LENS)))
    assert status == 200
    assert not np.allclose(_camera_vector(data["camera"]), TRUE, atol=0.1)


def test_simple_pins_read_without_their_lens_centre_miss_the_camera(base: str) -> None:
    status, data = _solve(base, _body(marks=_simple_marks(LENS), lens=(*LENS[:2], 0.0, 0.0)))
    assert status == 200
    assert not np.allclose(_camera_vector(data["camera"]), TRUE, atol=0.05)


def test_a_lens_the_station_would_pull_back_is_solved_as_the_station_keeps_it(base: str) -> None:
    """A centre past its bound is clamped and the pair pulled back, as CameraConfig does, never refused."""
    kept = (-0.99 / 1.25**2, 0.0, 0.25, 0.0)  # 1 - 1.5 t s reaches 0 at the far corner, s = 1.25^2
    status, data = _solve(base, _body(marks=_simple_marks(kept), lens=(-1.5, 0.0, 0.9, 0.0)))
    assert status == 200
    np.testing.assert_allclose(_camera_vector(data["camera"]), TRUE, atol=1e-3)


def test_a_body_without_a_camera_reads_the_pins_as_pinhole(base: str) -> None:
    status, data = _solve(base, _with({"camera": _DROP}))
    assert status == 200
    np.testing.assert_allclose(_camera_vector(data["camera"]), TRUE, atol=1e-3)


def test_advanced_gives_the_camera_and_each_triangulated_place(base: str) -> None:
    status, data = _solve(base, _body("advanced", _advanced_marks(LENS), hint=HINT, lens=LENS))
    assert status == 200
    np.testing.assert_allclose(_camera_vector(data["camera"]), TRUE, atol=1e-3)
    by_id = {m["id"]: m for m in data["marks"]}
    assert by_id[5]["position"] == pytest.approx(list(P5), abs=1e-3)
    assert by_id[6]["position"] == pytest.approx(list(P6), abs=1e-3)
    assert by_id[6]["distance_error"] == pytest.approx(0.0, abs=1e-3)
    assert by_id[1]["distance_error"] is None
    assert data["count"] == 4


# 12 m up, 2° off straight down; each corner pinned about half a pixel off.
_STRAIGHT_DOWN = np.array([0.0, 1.0, 12.0, -88.0, 180.0, 0.0, 70.0])
_UNDER_IT = [(6.0, -1.0, 0.0), (-6.0, -1.0, 0.0), (-6.0, 3.0, 0.0), (6.0, 3.0, 0.0)]
_HALF_PX = [(0.6, -0.4), (-0.5, 0.5), (0.4, 0.6), (-0.6, -0.3)]


def _straight_down_marks(camera: np.ndarray = _STRAIGHT_DOWN, under=_UNDER_IT, off=_HALF_PX) -> list[dict[str, Any]]:
    seen = project_points(camera, np.array(under), W, H)
    return [
        {"id": i, "kind": "known", "x": p[0], "y": p[1], "z": p[2], "pin": [float(q[0]) + d[0], float(q[1]) + d[1]]}
        for i, (p, q, d) in enumerate(zip(under, seen, off, strict=True), start=1)
    ]


_ABSENT = object()


@pytest.mark.parametrize("height", [_ABSENT, None], ids=["absent", "null"])
def test_without_a_camera_height_a_camera_straight_down_drifts_and_says_it_is_steep(base: str, height: Any) -> None:
    body = _body(marks=_straight_down_marks())
    if height is not _ABSENT:
        body["camera_height"] = height
    status, data = _solve(base, body)
    assert status == 200
    assert data["steep"] is True
    assert abs(data["camera"]["pos_z"] - 12.0) > 1.0


def test_the_camera_height_holds_a_camera_straight_down(base: str) -> None:
    status, data = _solve(base, _body(marks=_straight_down_marks(), camera_height=12.0))
    assert status == 200
    assert data["steep"] is True
    assert data["camera"]["pos_z"] == pytest.approx(12.0, abs=0.01)
    np.testing.assert_allclose(_camera_vector(data["camera"])[:3], _STRAIGHT_DOWN[:3], atol=0.08)


def test_without_a_camera_position_the_camera_height_holds_a_camera_looking_exactly_down(base: str) -> None:
    exactly_down = np.array([0.3, 0.7, 13.0, -90.0, 30.0, 0.0, 75.0])
    under = [(4.0, -2.0, 0.0), (-4.0, -2.0, 0.0), (-4.0, 3.0, 0.0), (4.0, 3.0, 0.0)]
    off = [(0.4, -0.3), (-0.5, 0.2), (0.3, 0.4), (-0.2, -0.4)]
    status, data = _solve(base, _body(marks=_straight_down_marks(exactly_down, under, off), camera_height=13.0))
    assert status == 200
    assert data["steep"] is True
    assert data["camera"]["pos_z"] == pytest.approx(13.0, abs=0.01)


@pytest.mark.parametrize("height", [0.0, -2.5])
def test_a_camera_height_not_above_the_marks_is_named(base: str, height: float) -> None:
    status, data = _solve(base, _body(hint=HINT, camera_height=height))
    assert status == 422
    assert data["error"] == cp.NO_FIT_AT_HEIGHT


def test_a_camera_looking_at_the_stage_is_not_steep(base: str) -> None:
    status, data = _solve(base, _body())
    assert (status, data["steep"]) == (200, False)


def test_a_known_mark_without_a_pin_has_no_error_and_is_not_counted(base: str) -> None:
    marks = [*_simple_marks(), _known(7, (0.0, 3.0, 0.0), pinned=False)]
    status, data = _solve(base, _body("advanced", marks, hint=HINT))
    assert status == 200
    unpinned = {m["id"]: m for m in data["marks"]}[7]
    assert unpinned["error"] is None
    assert unpinned["seen"] == pytest.approx(_pin((0.0, 3.0, 0.0)), abs=1e-3)
    assert data["count"] == 4


def test_a_pin_that_disagrees_is_named_in_the_reasons(base: str) -> None:
    marks = [*_simple_marks(), _known(7, (0.0, 3.0, 0.0)), _known(8, (3.0, 3.0, 1.0))]
    marks[5]["pin"] = [marks[5]["pin"][0] + 40.0, marks[5]["pin"][1]]
    status, data = _solve(base, _body("advanced", marks, hint=HINT))
    assert status == 200
    assert data["largest_error"] > cp.FLAG_PX
    assert 8 in {r["id"] for r in data["reasons"] if r["kind"] == "pixel"}


def test_a_camera_position_far_from_the_solve_is_named_in_the_reasons(base: str) -> None:
    status, data = _solve(base, _body("advanced", _advanced_marks(), hint=[0.0, 13.0, 4.0]))
    assert status == 200
    (offset,) = [r for r in data["reasons"] if r["kind"] == "offset"]
    assert offset["per_axis"] is True
    assert offset["delta"] == pytest.approx([0.0, 0.0, 8.0], abs=1e-3)


def test_simple_checks_the_whole_gap_to_the_camera_position(base: str) -> None:
    """2 m off in height alone is past Simple's 1.6 m in all, though inside Advanced's 2.5 m per axis."""
    status, data = _solve(base, _body("simple", hint=[0.0, 13.0, 14.0]))
    assert status == 200
    (offset,) = [r for r in data["reasons"] if r["kind"] == "offset"]
    assert offset["per_axis"] is False
    assert offset["off"] == pytest.approx(2.0, abs=1e-3)


def test_a_floor_moved_off_the_level_corners_is_named_in_the_reasons(base: str) -> None:
    """A mark 2 m up typed at 4 m bends the camera until the floor beyond the corners is twice as far."""
    mistyped = {**_known(5, (0.0, 6.0, 2.0)), "z": 4.0}
    status, data = _solve(base, _body("advanced", [*_simple_marks(), mistyped], hint=HINT))
    assert status == 200
    assert {"kind": "reach"} in data["reasons"]


# ---------------------------------------------------------------------------
# /api/wizard/solve – what is still missing, and no camera
# ---------------------------------------------------------------------------


_LACKING = {
    "advanced without a camera position": ("advanced", "advanced", None, ["the camera position"]),
    "advanced with one known": ("advanced", "one known", HINT, ["1 more known point", "3 more pinned points"]),
    "advanced with none": ("advanced", "none", HINT, ["2 more known points", "4 more pinned points"]),
    "advanced with nothing": (
        "advanced",
        "none",
        None,
        ["the camera position", "2 more known points", "4 more pinned points"],
    ),
    "simple with three pinned": ("simple", "three pinned", None, ["1 more pinned point"]),
    "simple with none": ("simple", "none", None, ["4 more pinned points"]),
}


@pytest.mark.parametrize(("mode", "marks", "hint", "need"), list(_LACKING.values()), ids=list(_LACKING))
def test_a_solve_that_lacks_something_says_what(base: str, mode, marks, hint, need) -> None:
    chosen = {
        "advanced": _advanced_marks(),
        "one known": _simple_marks()[:1],
        "three pinned": [*_simple_marks()[:3], _known(4, CORNERS[3], pinned=False)],
        "none": [],
    }[marks]
    status, data = _solve(base, _body(mode, chosen, hint=hint))
    assert status == 422
    assert data == {"error": "The camera cannot be calculated yet.", "need": need}


def test_marks_their_distances_place_nowhere_fit_no_camera_and_say_why(base: str) -> None:
    nowhere = [
        {**_triangulated(n, P5, (1, CORNERS[0]), (2, CORNERS[1])), "distance_a": 1.0, "distance_b": 1.0} for n in (5, 6)
    ]
    status, data = _solve(base, _body("advanced", [*_advanced_marks()[:2], *nowhere], hint=HINT))
    assert status == 422
    assert data["error"] == cp.NO_FIT
    assert [(m["id"], m["position"], m["reason"]) for m in data["marks"]] == [
        (1, None, ""),
        (2, None, ""),
        (5, None, cp.REASON_SHORT),
        (6, None, cp.REASON_SHORT),
    ]


# ---------------------------------------------------------------------------
# /api/wizard/solve – malformed bodies
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("payload", [[], "x", 5, None, True], ids=["array", "string", "number", "null", "bool"])
def test_a_body_that_is_not_an_object_is_refused(base: str, payload: Any) -> None:
    status, _ = _send(base, "/api/wizard/solve", raw=json.dumps(payload).encode())
    assert status == 400


def test_a_body_that_is_not_json_is_refused(base: str) -> None:
    assert _send(base, "/api/wizard/solve", raw=b"{not json")[0] == 400


def _with(change: dict[str, Any]) -> dict[str, Any]:
    body = _body()
    for key, value in change.items():
        if value is _DROP:
            body.pop(key)
        else:
            body[key] = value
    return body


_DROP = object()

_MALFORMED_TOP = {
    "mode off the list": {"mode": "expert"},
    "mode none": {"mode": None},
    "mode number": {"mode": 1},
    "mode missing": {"mode": _DROP},
    "hint of two": {"hint": [0.0, 13.0]},
    "hint of four": {"hint": [0.0, 13.0, 12.0, 1.0]},
    "hint a string": {"hint": "0, 13, 12"},
    "hint with text": {"hint": [0.0, "13", 12.0]},
    "hint with a bool": {"hint": [0.0, True, 12.0]},
    "hint with none": {"hint": [0.0, None, 12.0]},
    "hint beyond reach": {"hint": [0.0, 13.0, 10_000.5]},
    "saved_fov below 1": {"saved_fov": 0.5},
    "saved_fov above 179": {"saved_fov": 180.0},
    "saved_fov text": {"saved_fov": "60"},
    "saved_fov bool": {"saved_fov": True},
    "camera_height text": {"camera_height": "12"},
    "camera_height bool": {"camera_height": True},
    "camera_height list": {"camera_height": [12.0]},
    "camera_height nan": {"camera_height": math.nan},
    "camera_height infinite": {"camera_height": math.inf},
    "camera_height huge": {"camera_height": 10**400},
    "camera_height beyond reach": {"camera_height": 10_000.5},
    "camera_height beyond reach below": {"camera_height": -10_000.5},
    "image_width zero": {"image_width": 0},
    "image_width text": {"image_width": "1920"},
    "image_height missing": {"image_height": _DROP},
    "marks not a list": {"marks": {"id": 1}},
    "marks none": {"marks": None},
    "marks missing": {"marks": _DROP},
    "camera not an object": {"camera": [-0.2, 0.03]},
    "lens_k1 text": {"camera": {"lens_k1": "-0.2", "lens_k2": 0.0}},
    "lens_k2 bool": {"camera": {"lens_k1": 0.0, "lens_k2": True}},
    "lens_k1 huge": {"camera": {"lens_k1": 10**400, "lens_k2": 0.0}},
    "lens_cx text": {"camera": {"lens_k1": 0.0, "lens_cx": "0.1"}},
    "lens_cy bool": {"camera": {"lens_k1": 0.0, "lens_cy": False}},
    "lens_cx huge": {"camera": {"lens_cx": 10**400}},
    "lens_cy none": {"camera": {"lens_cy": None}},
}


@pytest.mark.parametrize("change", list(_MALFORMED_TOP.values()), ids=list(_MALFORMED_TOP))
def test_a_malformed_body_is_refused(base: str, change: dict[str, Any]) -> None:
    status, data = _solve(base, _with(change))
    assert status == 400
    assert data["error"]


def _mark_change(index: int, **change: Any) -> list[dict[str, Any]]:
    marks = copy.deepcopy(_advanced_marks())
    for key, value in change.items():
        if value is _DROP:
            marks[index].pop(key)
        else:
            marks[index][key] = value
    return marks


_MALFORMED_MARKS = {
    "not an object": [*_advanced_marks()[:3], 5],
    "kind off the list": _mark_change(0, kind="laser"),
    "kind missing": _mark_change(0, kind=_DROP),
    "id text": _mark_change(0, id="1"),
    "id bool": _mark_change(0, id=True),
    "id fraction": _mark_change(0, id=1.5),
    "id zero": _mark_change(0, id=0),
    "id past 999": _mark_change(0, id=1000),
    "x text": _mark_change(0, x="6"),
    "x bool": _mark_change(0, x=True),
    "x missing": _mark_change(0, x=_DROP),
    "z none": _mark_change(1, z=None),
    "from_a text": _mark_change(2, from_a="1"),
    "from_b bool": _mark_change(2, from_b=True),
    "distance_a text": _mark_change(2, distance_a="21"),
    "distance_a bool": _mark_change(2, distance_a=True),
    "distance_b past 200 m": _mark_change(2, distance_b=250.0),
    "triangulated unpinned": _mark_change(2, pin=None),
    "pin of one": _mark_change(0, pin=[100.0]),
    "pin of three": _mark_change(0, pin=[100.0, 100.0, 1.0]),
    "pin a string": _mark_change(0, pin="100, 100"),
    "pin with a bool": _mark_change(0, pin=[True, 100.0]),
    "pin off the snapshot": _mark_change(0, pin=[2000.0, 100.0]),
    "a repeated id": [*_simple_marks(), _known(4, (0.0, 3.0, 0.0))],
    "measured from a missing mark": _mark_change(2, from_b=9),
    "measured from itself": _mark_change(2, from_a=5),
    "a loop": _mark_change(2, from_b=6),
    "thirteen marks": [*_simple_marks(), *[_known(n, (float(n) - 8.0, 3.0, 0.0)) for n in range(5, 14)]],
}


@pytest.mark.parametrize(
    "marks",
    [
        [*_simple_marks()[:3], _known(5, (6.0, 6.0, 0.0))],
        [*_simple_marks(), *_advanced_marks()[2:3]],
        [*_simple_marks()[:3], _triangulated(4, CORNERS[3], (1, CORNERS[0]), (2, CORNERS[1]))],
    ],
    ids=["a fifth corner", "a triangulated mark", "a triangulated fourth corner"],
)
def test_simple_takes_its_four_corners_only(base: str, marks: list[Any]) -> None:
    status, data = _solve(base, _body("simple", marks))
    assert status == 400
    assert "four corners" in data["error"]


@pytest.mark.parametrize("marks", list(_MALFORMED_MARKS.values()), ids=list(_MALFORMED_MARKS))
def test_a_malformed_mark_is_refused(base: str, marks: list[Any]) -> None:
    status, data = _solve(base, _body("advanced", marks, hint=HINT))
    assert status == 400
    assert data["error"]


def test_twelve_marks_are_accepted(base: str) -> None:
    marks = [*_simple_marks(), *[_known(n, (float(n) - 8.0, 3.0, 0.0)) for n in range(5, 13)]]
    assert _solve(base, _body("advanced", marks, hint=HINT))[0] == 200


# ---------------------------------------------------------------------------
# /api/wizard/solve – superseded turns
# ---------------------------------------------------------------------------


def test_a_solve_older_than_one_already_asked_for_is_dropped(base: str) -> None:
    page, other = _page(), _page()
    assert _solve(base, _body(solve_page=page, solve_seq=7))[0] == 200
    assert _solve(base, _body(solve_page=page, solve_seq=6)) == (409, {"error": "A newer calculation was requested."})
    assert _solve(base, _body(solve_page=other, solve_seq=1))[0] == 200
    assert _solve(base, _body(solve_page=page, solve_seq=7))[0] == 200


@pytest.mark.parametrize(
    "broken", [{"mode": "expert"}, {"marks": [5]}, {"image_width": 0}], ids=["mode", "marks", "canvas"]
)
def test_a_newer_solve_refused_as_malformed_still_supersedes_the_older(base: str, broken: dict) -> None:
    page = _page()
    assert _solve(base, {**_body(solve_page=page, solve_seq=8), **broken})[0] == 400
    assert _solve(base, _body(solve_page=page, solve_seq=7))[0] == 409


def test_a_fit_overtaken_while_it_runs_is_dropped(base: str, monkeypatch: pytest.MonkeyPatch) -> None:
    page = _page()
    real = cp.solve_marks
    newer: list[int] = []

    def overtaken(*args: Any, **kwargs: Any) -> Any:
        if not newer:
            newer.append(0)
            newer[0] = _solve(base, _body(solve_page=page, solve_seq=8))[0]
        return real(*args, **kwargs)

    monkeypatch.setattr(cp, "solve_marks", overtaken)
    assert _solve(base, _body(solve_page=page, solve_seq=7)) == (409, {"error": "A newer calculation was requested."})
    assert newer == [200]


def test_a_page_pushed_out_by_sixty_four_newer_ones_is_forgotten(base: str) -> None:
    first = _page()
    assert _solve(base, _body(solve_page=first, solve_seq=9))[0] == 200
    for n in range(64):
        assert _solve(base, _body(solve_page=f"{first}-{n}", solve_seq=1))[0] == 200
    assert _solve(base, _body(solve_page=first, solve_seq=1))[0] == 200


@pytest.mark.parametrize(
    "change",
    [
        {"solve_page": 3, "solve_seq": 1},
        {"solve_page": "", "solve_seq": 1},
        {"solve_page": "x" * 65, "solve_seq": 1},
        {"solve_page": "page", "solve_seq": "1"},
        {"solve_page": "page", "solve_seq": True},
        {"solve_page": "page", "solve_seq": 1.5},
        {"solve_page": "page"},
        {"solve_seq": 1},
    ],
)
def test_a_malformed_solve_sequence_is_refused(base: str, change: dict) -> None:
    assert _solve(base, {**_body(), **change})[0] == 400


# ---------------------------------------------------------------------------
# /api/wizard/lens/calibrations
# ---------------------------------------------------------------------------


def test_a_station_without_saved_lenses_lists_none(base: str, lens_dir: Path) -> None:
    assert _send(base, "/api/wizard/lens/calibrations") == (200, {"calibrations": []})


def test_a_saved_lens_lands_beside_the_config_with_the_version(base: str, lens_dir: Path) -> None:
    body = {"name": "Zoom wide", "k1": -0.2, "k2": 0.03, "cx": 0.04, "cy": -0.03}
    status, data = _send(base, "/api/wizard/lens/calibrations", body)
    assert status == 200
    saved = data["saved"]
    assert (saved["name"], saved["k1"], saved["k2"], saved["cx"], saved["cy"]) == ("Zoom wide", -0.2, 0.03, 0.04, -0.03)
    assert saved["openfollow_version"] == openfollow.__version__
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", saved["saved"])
    (path,) = lens_dir.glob("*.json")
    assert json.loads(path.read_text(encoding="utf-8")) == saved
    assert _send(base, "/api/wizard/lens/calibrations") == (200, {"calibrations": [saved]})


def test_saving_a_name_again_ignoring_case_replaces_it(base: str, lens_dir: Path) -> None:
    _send(base, "/api/wizard/lens/calibrations", {"name": "Zoom Wide", "k1": -0.2, "k2": 0.03, "cx": 0.0, "cy": 0.0})
    _send(base, "/api/wizard/lens/calibrations", {"name": "zoom wide", "k1": -0.1, "k2": 0.0, "cx": 0.0, "cy": 0.0})
    _, data = _send(base, "/api/wizard/lens/calibrations")
    assert [(c["name"], c["k1"]) for c in data["calibrations"]] == [("zoom wide", -0.1)]
    assert len(list(lens_dir.glob("*.json"))) == 1


_BAD_LENS = {
    "empty name": {"name": "", "k1": -0.2, "k2": 0.03, "cx": 0.0, "cy": 0.0},
    "blank name": {"name": "   ", "k1": -0.2, "k2": 0.03, "cx": 0.0, "cy": 0.0},
    "name none": {"name": None, "k1": -0.2, "k2": 0.03, "cx": 0.0, "cy": 0.0},
    "name number": {"name": 5, "k1": -0.2, "k2": 0.03, "cx": 0.0, "cy": 0.0},
    "name too long": {"name": "x" * 61, "k1": -0.2, "k2": 0.03, "cx": 0.0, "cy": 0.0},
    "name with a newline": {"name": "Zoom\nwide", "k1": -0.2, "k2": 0.03, "cx": 0.0, "cy": 0.0},
    "name with a lone surrogate": {"name": "Zoom\ud800", "k1": -0.2, "k2": 0.03, "cx": 0.0, "cy": 0.0},
    "name missing": {"k1": -0.2, "k2": 0.03, "cx": 0.0, "cy": 0.0},
    "k1 text": {"name": "Zoom", "k1": "-0.2", "k2": 0.03, "cx": 0.0, "cy": 0.0},
    "k1 bool": {"name": "Zoom", "k1": True, "k2": 0.03, "cx": 0.0, "cy": 0.0},
    "k2 none": {"name": "Zoom", "k1": -0.2, "k2": None, "cx": 0.0, "cy": 0.0},
    "k2 missing": {"name": "Zoom", "k1": -0.2, "cx": 0.0, "cy": 0.0},
    "cx missing": {"name": "Zoom", "k1": -0.2, "k2": 0.03, "cy": 0.0},
    "cy missing": {"name": "Zoom", "k1": -0.2, "k2": 0.03, "cx": 0.0},
    "cx bool": {"name": "Zoom", "k1": -0.2, "k2": 0.03, "cx": True, "cy": 0.0},
    "cy text": {"name": "Zoom", "k1": -0.2, "k2": 0.03, "cx": 0.0, "cy": "0"},
    "cx huge": {"name": "Zoom", "k1": -0.2, "k2": 0.03, "cx": 10**400, "cy": 0.0},
    "cy out of bounds": {"name": "Zoom", "k1": -0.2, "k2": 0.03, "cx": 0.0, "cy": -0.3},
    "centre short of a corner": {"name": "Zoom", "k1": -0.9, "k2": 0.0, "cx": 0.1, "cy": 0.0},
    "k1 huge": {"name": "Zoom", "k1": 10**400, "k2": 0.0, "cx": 0.0, "cy": 0.0},
    "pair short of a corner": {"name": "Zoom", "k1": -1.2, "k2": 0.0, "cx": 0.0, "cy": 0.0},
}


@pytest.mark.parametrize("payload", list(_BAD_LENS.values()), ids=list(_BAD_LENS))
def test_a_lens_that_cannot_be_saved_is_refused_and_nothing_is_written(base: str, lens_dir: Path, payload) -> None:
    status, data = _send(base, "/api/wizard/lens/calibrations", payload)
    assert status == 400
    assert data["error"]
    assert not lens_dir.is_dir() or list(lens_dir.glob("*.json")) == []


@pytest.mark.parametrize("raw", [b"[]", b'"Zoom"', b"5", b"null", b"{not json"])
def test_a_lens_body_that_is_not_an_object_is_refused(base: str, lens_dir: Path, raw: bytes) -> None:
    assert _send(base, "/api/wizard/lens/calibrations", raw=raw)[0] == 400


def test_a_lens_the_station_cannot_write_answers_500(
    base: str, lens_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise PermissionError("read-only file system")

    monkeypatch.setattr(lens_calibrations, "save_calibration", refuse)
    status, data = _send(
        base, "/api/wizard/lens/calibrations", {"name": "Zoom wide", "k1": -0.2, "k2": 0.03, "cx": 0.0, "cy": 0.0}
    )
    assert (status, data) == (500, {"error": "The station could not write the file."})


def test_the_list_is_newest_first_and_leaves_out_what_does_not_read(base: str, lens_dir: Path) -> None:
    lens_dir.mkdir(exist_ok=True)
    for name, saved in (("Older", "2026-01-02"), ("Newest", "2026-10-20"), ("alpha", "2026-01-02")):
        body = {
            "name": name,
            "k1": -0.1,
            "k2": 0.0,
            "cx": 0.0,
            "cy": 0.0,
            "saved": saved,
            "openfollow_version": "0.5.0",
        }
        (lens_dir / f"{name}.json").write_text(json.dumps(body), encoding="utf-8")
    (lens_dir / "broken.json").write_text('{"name": "Broken", "k1": 1' + "0" * 400 + ', "k2": 0}', "utf-8")
    status, data = _send(base, "/api/wizard/lens/calibrations")
    assert status == 200
    assert [(c["name"], c["openfollow_version"]) for c in data["calibrations"]] == [
        ("Newest", "0.5.0"),
        ("alpha", "0.5.0"),
        ("Older", "0.5.0"),
    ]


def test_a_saved_lens_never_travels_with_an_export(base: str, lens_dir: Path) -> None:
    _send(
        base,
        "/api/wizard/lens/calibrations",
        {"name": "Unmistakable Lens", "k1": -0.2, "k2": 0.03, "cx": 0.0, "cy": 0.0},
    )
    with urllib.request.urlopen(f"{base}/api/config/export", timeout=10) as r:
        exported = r.read().decode()
    assert "Unmistakable Lens" not in exported
