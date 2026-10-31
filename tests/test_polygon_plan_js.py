# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Michel Honold and Paul Hermann
"""The shared top-down polygon plan (static/js/polygon-plan.js), run in node on a fake canvas."""

from __future__ import annotations

import json
import shutil
import subprocess  # nosec B404 - runs the local node binary on a fixed script
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.skipif(shutil.which("node") is None, reason="needs node")]

_MODULE = Path(__file__).parents[1] / "openfollow" / "web" / "static" / "js" / "polygon-plan.js"

# A 1280x720 canvas shown at 640x360 CSS px, a recording 2D context, and a window.
_HARNESS = """
var listeners = {};
var winListeners = {};
var window = { addEventListener: function (t, f) { (winListeners[t] = winListeners[t] || []).push(f); } };
var ops = [];
var ctx = new Proxy({}, {
    get: function (_, k) {
        if (k === 'measureText') return function () { return { width: 10 }; };
        return function () { ops.push([k].concat(Array.prototype.slice.call(arguments))); };
    },
    set: function () { return true; },
});
var canvas = {
    width: 1280, height: 720,
    getContext: function () { return ctx; },
    getBoundingClientRect: function () { return { left: 0, top: 0, width: 640, height: 360 }; },
    addEventListener: function (t, f) { (listeners[t] = listeners[t] || []).push(f); },
};
// A canvas-pixel point as the CSS-pixel event the browser would send.
function fire(type, sx, sy) {
    (listeners[type] || []).forEach(function (f) { f({ clientX: sx / 2, clientY: sy / 2 }); });
}
function fireWindow(type) { (winListeners[type] || []).forEach(function (f) { f({}); }); }
var calls = [];
function record(name) { return function () { calls.push([name].concat(Array.prototype.slice.call(arguments))); }; }
"""


def _run(body: str) -> Any:
    source = "\n".join(
        [
            _HARNESS,
            _MODULE.read_text(encoding="utf-8"),
            "var out = {};",
            body,
            "process.stdout.write(JSON.stringify(out));",
        ]
    )
    node = shutil.which("node")
    assert node is not None
    done = subprocess.run([node, "-e", source], capture_output=True, text=True, timeout=10)  # nosec B603
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


_PLAN = """
var polys = POLYS;
var selected = SELECTED;
var plan = new window.OpenFollow.PolygonPlan(canvas, {
    polygons: function () { return polys; },
    editable: function () { return selected === null ? null : polys[selected]; },
    onPress: record('press'),
    onDragMove: record('move'),
    onDragEnd: record('end'),
    onInsert: record('insert'),
});
plan.grid = { width: 10, depth: 6, spacing: 1, x_offset: 0, y_offset: 3 };
"""


def _plan(polys: list | None = None, selected: int | None = None) -> str:
    return _PLAN.replace("POLYS", json.dumps(polys or [])).replace("SELECTED", json.dumps(selected))


class TestView:
    def test_grid_centre_is_the_canvas_centre_and_upstage_is_up(self) -> None:
        out = _run(_plan() + "out.c = plan.toScreen(0, 3); out.up = plan.toScreen(0, 4);")
        assert out["c"] == [640, 360]
        assert out["up"][1] < 360

    def test_screen_and_world_round_trip(self) -> None:
        out = _run(_plan() + "var s = plan.toScreen(2.5, -1.25); out.w = plan.toWorld(s[0], s[1]);")
        assert out["w"] == [pytest.approx(2.5), pytest.approx(-1.25)]

    def test_view_widens_to_fit_a_polygon_beyond_the_grid(self) -> None:
        out = _run(_plan([[[0, 0], [20, 0], [20, 1]]]) + "out.e = plan.extent();")
        # Symmetric around the grid centre, one spacing of padding past x = 20.
        assert out["e"][0] == pytest.approx(42.0)

    def test_point_in_polygon_is_even_odd(self) -> None:
        out = _run(
            "var f = window.OpenFollow.pointInPolygon;"
            "var bow = [[0, 0], [2, 2], [2, 0], [0, 2]];"
            "out.r = [f(0.2, 1, bow), f(1.8, 1, bow), f(1, 1.8, bow), f(5, 5, bow)];"
        )
        assert out["r"] == [True, True, False, False]


class TestEdges:
    _SQUARE = [[[0, 0], [2, 0], [2, 2], [0, 2]]]

    def test_a_press_on_an_edge_adds_a_corner_there_and_holds_it(self) -> None:
        out = _run(
            _plan(self._SQUARE, 0)
            + """
            var e = plan.toScreen(1, 0);
            fire('mousedown', e[0], e[1] + 4);
            out.added = polys[0].slice();
            var to = plan.toScreen(1, -1);
            fire('mousemove', to[0], to[1]); fire('mouseup', to[0], to[1]);
            out.corners = polys[0]; out.calls = calls;
            """
        )
        assert out["added"][1] == [pytest.approx(1), pytest.approx(0)]
        assert len(out["corners"]) == 5
        assert out["corners"][1] == [pytest.approx(1), pytest.approx(-1)]
        assert out["calls"] == [["insert", 1], ["move", 1], ["end", 1]]

    def test_a_click_on_an_edge_keeps_the_corner_and_saves_it(self) -> None:
        out = _run(
            _plan(self._SQUARE, 0)
            + "var e = plan.toScreen(2, 1); fire('mousedown', e[0], e[1]); fire('mouseup', e[0], e[1]);"
            + " out.corners = polys[0]; out.calls = calls;"
        )
        assert out["corners"][2] == [pytest.approx(2), pytest.approx(1)]
        assert out["calls"] == [["insert", 2], ["end", 2]]

    def test_a_press_on_a_corner_moves_it_rather_than_adding_one(self) -> None:
        out = _run(
            _plan(self._SQUARE, 0)
            + "var h = plan.toScreen(2, 0); fire('mousedown', h[0], h[1]); out.n = polys[0].length;"
        )
        assert out["n"] == 4

    def test_a_press_away_from_the_edges_adds_nothing(self) -> None:
        out = _run(
            _plan(self._SQUARE, 0)
            + "var p = plan.toScreen(1, 1); fire('mousedown', p[0], p[1]); out.n = polys[0].length;"
            + " out.calls = calls.map(function (c) { return c[0]; });"
        )
        assert out == {"n": 4, "calls": ["press"]}

    def test_an_unchosen_zone_has_no_edges_to_add_to(self) -> None:
        out = _run(
            _plan(self._SQUARE, None)
            + "var e = plan.toScreen(1, 0); fire('mousedown', e[0], e[1]); out.n = polys[0].length;"
        )
        assert out["n"] == 4

    def test_hovering_an_edge_marks_where_the_corner_would_land(self) -> None:
        out = _run(
            _plan(self._SQUARE, 0)
            + """
            var e = plan.toScreen(0, 1);
            fire('mousemove', e[0] + 3, e[1]); out.on = plan.hover;
            var c = plan.toScreen(1, 1);
            fire('mousemove', c[0], c[1]); out.off = plan.hover;
            out.near = e;
            """
        )
        assert out["on"] == [pytest.approx(out["near"][0]), pytest.approx(out["near"][1])]
        assert out["off"] is None


class TestDragging:
    _SQUARE = [[[0, 0], [2, 0], [2, 2], [0, 2]]]

    def test_dragging_a_handle_moves_that_corner(self) -> None:
        out = _run(
            _plan(self._SQUARE, 0)
            + """
            var h = plan.toScreen(2, 2);
            fire('mousedown', h[0], h[1]);
            var to = plan.toScreen(3, 1);
            fire('mousemove', to[0], to[1]);
            fire('mouseup', to[0], to[1]);
            out.corner = polys[0][2]; out.calls = calls; out.drag = plan.drag;
            """
        )
        assert out["corner"] == [pytest.approx(3), pytest.approx(1)]
        assert out["calls"] == [["move", 2], ["end", 2]]
        assert out["drag"] is None

    def test_the_view_holds_still_during_a_drag(self) -> None:
        out = _run(
            _plan(self._SQUARE, 0)
            + """
            var h = plan.toScreen(2, 2);
            fire('mousedown', h[0], h[1]);
            var before = plan.extent();
            fire('mousemove', 1270, 10);
            out.held = plan.extent()[0] === before[0];
            fireWindow('blur');
            out.after = plan.extent()[0] > before[0];
            out.calls = calls.map(function (c) { return c[0]; });
            """
        )
        assert out["held"] is True
        assert out["after"] is True
        assert out["calls"] == ["move", "end"]

    def test_a_press_off_every_handle_reports_the_world_point(self) -> None:
        out = _run(
            _plan(self._SQUARE, 0) + "var p = plan.toScreen(1, 1); fire('mousedown', p[0], p[1]); out.calls = calls;"
        )
        assert out["calls"][0][0] == "press"
        assert out["calls"][0][1] == [pytest.approx(1), pytest.approx(1)]

    def test_no_editable_polygon_means_no_handles(self) -> None:
        out = _run(
            _plan(self._SQUARE, None) + "var h = plan.toScreen(2, 2); fire('mousedown', h[0], h[1]); out.d = plan.drag;"
        )
        assert out["d"] is None


_SQUARE = [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0)]
_L_SHAPE = [(0.0, 0.0), (4.0, 0.0), (4.0, 2.0), (2.0, 2.0), (2.0, 4.0), (0.0, 4.0)]
_SIGHT_LINES = [
    ((2.0, 0.0, 0.0), (2.0, -10.0, 8.0), _SQUARE),
    ((2.0, 4.0, 0.0), (2.0, -10.0, 8.0), _SQUARE),
    ((4.0, 4.0, 0.5), (2.0, -10.0, 8.0), _SQUARE),
    ((4.0, 4.0, 1.0), (2.0, -10.0, 8.0), _SQUARE),
    ((2.0, 5.0, 0.0), (2.0, -10.0, 8.0), _SQUARE),
    ((2.0, 9.0, 0.0), (2.0, -10.0, 8.0), _SQUARE),
    ((2.0, 6.0, 0.5), (2.0, -10.0, 0.5), _SQUARE),
    ((2.0, 6.0, 3.0), (2.0, -10.0, 3.0), _SQUARE),
    ((2.0, 2.2, 0.0), (10.0, -2.0, 2.0), _L_SHAPE),
    ((2.0, 3.5, 0.0), (10.0, 6.0, 2.0), _L_SHAPE),
    ((2.0, 2.0, 0.5), (2.0, 2.0, 0.6), _SQUARE),
]


def test_a_zone_hides_the_same_points_from_the_camera_as_on_the_operator_screen() -> None:
    from openfollow.zones.geometry import hidden_by_zone

    out = _run(
        f"var cases = {json.dumps(_SIGHT_LINES)};"
        "out.hidden = cases.map(function (c) { return window.OpenFollow.hiddenByZone(c[0], c[1], c[2], 0, 1); });"
    )
    want = [hidden_by_zone(point, eye, verts, 0.0, 1.0) for point, eye, verts in _SIGHT_LINES]
    assert out["hidden"] == want
    assert True in want and False in want


class TestMovingWhole:
    _SQUARE = [[[0, 0], [2, 0], [2, 2], [0, 2]]]
    # The page reports the square's label around its centre (1, 1).
    _LABEL = """
    plan.opts.labelAt = function (sx, sy) {
        var c = plan.toScreen(1, 1);
        return Math.abs(sx - c[0]) < 20 && Math.abs(sy - c[1]) < 8 ? 0 : null;
    };
    plan.opts.grab = function (i) { calls.push(['grab', i]); return polys[i]; };
    plan.opts.onGrabMove = record('grabMove');
    plan.opts.onGrabEnd = record('grabEnd');
    """

    def test_a_drag_from_the_label_moves_every_corner(self) -> None:
        out = _run(
            _plan(self._SQUARE, None)
            + self._LABEL
            + """
            var c = plan.toScreen(1, 1), to = plan.toScreen(2, 0.5);
            fire('mousedown', c[0], c[1]); fire('mousemove', to[0], to[1]); fire('mouseup', to[0], to[1]);
            out.corners = polys[0]; out.calls = calls; out.drag = plan.drag;
            """
        )
        want = [[1, -0.5], [3, -0.5], [3, 1.5], [1, 1.5]]
        assert out["corners"] == [[pytest.approx(x), pytest.approx(y)] for x, y in want]
        assert out["calls"] == [["grab", 0], ["grabMove"], ["grabEnd", True]]
        assert out["drag"] is None

    def test_a_click_on_the_label_moves_nothing(self) -> None:
        out = _run(
            _plan(self._SQUARE, None)
            + self._LABEL
            + "var c = plan.toScreen(1, 1); fire('mousedown', c[0], c[1]); fire('mouseup', c[0], c[1]);"
            + " out.corners = polys[0]; out.calls = calls;"
        )
        assert out["corners"] == self._SQUARE[0]
        assert out["calls"] == [["grab", 0], ["grabEnd", False]]

    def test_a_corner_handle_over_the_label_is_the_corner(self) -> None:
        out = _run(
            _plan([[[0, 0], [2, 0], [1, 1], [0, 2]]], 0)
            + self._LABEL
            + "var c = plan.toScreen(1, 1); fire('mousedown', c[0], c[1]); out.calls = calls; out.drag = plan.drag;"
        )
        assert out == {"calls": [], "drag": {"index": 2}}

    def test_the_label_is_no_edge_to_add_a_corner_to(self) -> None:
        out = _run(
            _plan([[[0, 0.9], [2, 0.9], [2, 2], [0, 2]]], 0)
            + self._LABEL
            + """
            var c = plan.toScreen(1, 1);
            fire('mousemove', c[0], c[1]); out.hover = plan.hover;
            fire('mousedown', c[0], c[1]); fire('mouseup', c[0], c[1]); out.n = polys[0].length;
            """
        )
        assert out == {"hover": None, "n": 4}
