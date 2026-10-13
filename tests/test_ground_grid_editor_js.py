# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The Ground Grid section's plan editor, run in node on the fake DOM."""

from __future__ import annotations

import json
import shutil
import subprocess  # nosec B404 - runs the local node binary on a fixed script
from pathlib import Path
from typing import Any

import pytest
from bottle import template

from openfollow.configuration import AppConfig
from openfollow.web import server as _server_module  # noqa: F401 – registers the template path

pytestmark = [pytest.mark.unit, pytest.mark.skipif(shutil.which("node") is None, reason="needs node")]

_TESTS = Path(__file__).parent
_STATIC = _TESTS.parent / "openfollow" / "web" / "static" / "js"

_PAGE = """
El.prototype.getContext = function () {
    return new Proxy({}, {
        get: function (_, k) {
            if (k === 'measureText') return function () { return { width: 10 }; };
            return function () {};
        },
        set: function () { return true; },
    });
};
window.addEventListener = function () {};
station = function (req) {
    if (req.url === '/api/zones') {
        var grid = { width: 10, depth: 6, spacing: 1, x_offset: 0, y_offset: 3 };
        return { body: { zones: [], markers: [], grid: grid } };
    }
    // The picture shows stage (x, y) at pixel (640 + 100 x, 360 - 100 y).
    var seen = function (p) { return [640 + 100 * p[0], 360 - 100 * p[1]]; };
    if (req.url === '/api/ground_grid/view') {
        var v = req.body.vertices;
        return { body: { outline: v.length >= 3 ? v.concat([v[0]]).map(seen) : [], lines: [], corners: v.map(seen) } };
    }
    if (req.url === '/api/scene/unproject') {
        return { body: { x: (req.body.x - 640) / 100, y: (360 - req.body.y) / 100, z: req.body.z } };
    }
    if (req.url === '/api/video/snapshot/full') return { body: 'jpeg' };
    return { body: '' };
};
"""

_SETUP = """
var canvas = document.getElementById('ground-grid-canvas');
canvas.width = 1280; canvas.height = 720;
canvas.getBoundingClientRect = function () { return { left: 0, top: 0, width: 1280, height: 720 }; };
function at(type, x, y) { canvas.dispatchEvent(makeEvent(type, { clientX: x, clientY: y })); }
function button(id) { document.getElementById(id).click(); }
function stored() { return JSON.parse(document.getElementById('ground-grid-vertices').value); }
function status() { return document.getElementById('ground-grid-status').textContent; }
"""


def _cfg(vertices: list | None = None) -> AppConfig:
    cfg = AppConfig()
    cfg.ground_grid.vertices = vertices or []
    return cfg


def _editor(steps: str, cfg: AppConfig | None = None, field: str | None = None, setup: str = "") -> Any:
    rendered = template("partials/ground_grid", config=cfg or _cfg())
    start = rendered.index("<script>")
    markup = rendered[:start] + "</form>"
    script = rendered[start + len("<script>") : rendered.index("</script>", start)]
    override = f"document.getElementById('ground-grid-vertices').value = {json.dumps(field)};" if field else ""
    source = "\n".join(
        [
            (_TESTS / "_fake_dom.js").read_text(encoding="utf-8"),
            _PAGE,
            (_STATIC / "units.js").read_text(encoding="utf-8"),
            (_STATIC / "save-feedback.js").read_text(encoding="utf-8"),
            (_STATIC / "polygon-plan.js").read_text(encoding="utf-8"),
            (_STATIC / "picture-view.js").read_text(encoding="utf-8"),
            f"parseHTML({json.dumps(markup)}, document.body);",
            _SETUP,
            override,
            setup,
            script,
            "(async function () { var out = {}; await settle();",
            steps,
            "process.stdout.write(JSON.stringify(out)); })()"
            ".catch(function (e) { console.error(e && e.stack || e); process.exit(1); });",
        ]
    )
    node = shutil.which("node")
    assert node is not None
    done = subprocess.run([node, "-e", source], capture_output=True, text=True, timeout=20)  # nosec B603
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


_SQUARE = [[0.0, 2.0], [2.0, 2.0], [2.0, 4.0], [0.0, 4.0]]


def test_drawing_a_polygon_fills_the_form_field() -> None:
    out = _editor(
        """
        out.before = status();
        button('ground-grid-draw-btn');
        at('click', 640, 360); at('click', 740, 360); at('click', 740, 260);
        button('ground-grid-finish-btn');
        out.verts = stored(); out.after = status();
        """
    )
    assert out["before"] == "No polygon yet."
    assert len(out["verts"]) == 3
    assert out["verts"][0] == [pytest.approx(0.0), pytest.approx(3.0)]
    assert out["after"] == "Polygon changed. Save to keep it."


def test_dragging_a_corner_updates_the_form_field() -> None:
    # The fitted view spans 12 x 8 m around (0, 3) at 85 px per metre: (2, 4) is (810, 275).
    out = _editor(
        """
        out.before = status();
        at('mousedown', 810, 275); at('mousemove', 895, 275); at('mouseup', 895, 275);
        out.verts = stored(); out.after = status();
        """,
        _cfg(_SQUARE),
    )
    assert out["before"] == ""
    assert out["verts"][2] == [pytest.approx(3.0), pytest.approx(4.0)]
    assert out["verts"][:2] == _SQUARE[:2]
    assert out["after"] == "Polygon changed. Save to keep it."


def test_clear_empties_the_polygon() -> None:
    out = _editor(
        "button('ground-grid-clear-btn'); out.verts = stored(); "
        "out.clearDisabled = document.getElementById('ground-grid-clear-btn').disabled;",
        _cfg(_SQUARE),
    )
    assert out == {"verts": [], "clearDisabled": True}


def test_an_unreadable_field_starts_with_no_polygon() -> None:
    out = _editor("out.status = status();", field="not json")
    assert out["status"] == "No polygon yet."


def test_the_plan_starts_on_the_saved_main_grid() -> None:
    cfg = _cfg()
    cfg.grid.width, cfg.grid.x_offset = 14.0, 1.5
    rendered = template("partials/ground_grid", config=cfg)
    assert '"width": 14.0' in rendered
    assert '"x_offset": 1.5' in rendered


_CAMERA = """
function choose(mode) {
    var radio = document.querySelector('#ground-grid-view-switch input[value="' + mode + '"]');
    radio.checked = true;
    radio.dispatchEvent(makeEvent('change'));
}
async function showPicture() {
    await settle();
    var img = document.querySelector('#ground-grid-picture img');
    img.naturalWidth = 1280; img.naturalHeight = 720;
    img.onload();
    await settle();
}
function svg() { return document.querySelector('#ground-grid-picture svg'); }
function views() { return requests.filter(function (r) { return r.url === '/api/ground_grid/view'; }); }
function pointer(target, type, x, y) {
    target.dispatchEvent(makeEvent(type, { clientX: x, clientY: y, pointerId: 1 }));
}
"""


def test_camera_view_draws_the_polygon_at_the_typed_height() -> None:
    cfg = _cfg(_SQUARE)
    cfg.ground_grid.z_offset = -0.8
    out = _editor(
        _CAMERA
        + """
        choose('camera'); await showPicture();
        out.planHidden = document.getElementById('ground-grid-plan-wrap').hidden;
        out.outline = svg().querySelectorAll('.pv-outline').map(function (l) { return l.getAttribute('points'); });
        out.handles = svg().querySelectorAll('.pv-handle').length;
        out.firstZ = views()[views().length - 1].body.z;
        typeInto(document.getElementById('ground-grid-z-offset'), '-1.5');
        await settle();
        out.typedZ = views()[views().length - 1].body.z;
        """,
        cfg,
    )
    assert out["planHidden"] is True
    assert out["outline"] == ["640,160 840,160 840,-40 640,-40 640,160"]
    assert out["handles"] == 4
    assert out["firstZ"] == -0.8
    assert out["typedZ"] == -1.5


def test_camera_view_drops_a_corner_on_the_ground_grid_plane() -> None:
    cfg = _cfg(_SQUARE)
    cfg.ground_grid.z_offset = -0.8
    out = _editor(
        _CAMERA
        + """
        choose('camera'); await showPicture();
        // The corner (2, 4) shows at (840, -40); drop it at (940, -40), stage (3, 4).
        pointer(svg().querySelectorAll('.pv-handle')[2], 'pointerdown', 840, -40);
        pointer(svg(), 'pointerup', 940, -40);
        await settle();
        out.verts = stored(); out.status = status();
        out.z = requests.filter(function (r) { return r.url === '/api/scene/unproject'; })[0].body.z;
        """,
        cfg,
    )
    assert out["z"] == -0.8
    assert out["verts"] == [[0.0, 2.0], [2.0, 2.0], [pytest.approx(3.0), pytest.approx(4.0)], [0.0, 4.0]]
    assert out["status"] == "Polygon changed. Save to keep it."


def test_camera_view_disables_drawing() -> None:
    out = _editor(
        _CAMERA
        + "choose('camera'); await settle(); out.draw = document.getElementById('ground-grid-draw-btn').disabled;"
    )
    assert out["draw"] is True


_ROWS = """
function cell(i, axis) { return document.querySelector('[data-vertex="' + i + ':' + axis + '"]'); }
function rows() {
    return document.querySelectorAll('#ground-grid-vertex-list input').map(function (n) { return n.value; });
}
function removeButtons() {
    return document.querySelectorAll('#ground-grid-vertex-list button').map(function (b) { return b.disabled; });
}
function listHidden() { return document.getElementById('ground-grid-vertex-field').hidden; }
"""


def test_the_saved_polygon_is_listed_vertex_by_vertex() -> None:
    out = _editor(
        _ROWS + "out.rows = rows(); out.removable = removeButtons(); out.hidden = listHidden();", _cfg(_SQUARE)
    )
    assert out["rows"] == ["0.000", "2.000", "2.000", "2.000", "2.000", "4.000", "0.000", "4.000"]
    assert out["removable"] == [False] * 4
    assert out["hidden"] is False


def test_with_no_polygon_there_is_no_list() -> None:
    out = _editor(_ROWS + "out.rows = rows(); out.hidden = listHidden();")
    assert out == {"rows": [], "hidden": True}


def test_a_typed_coordinate_moves_its_vertex() -> None:
    out = _editor(
        _ROWS + "typeInto(cell(1, 1), '2.5'); out.verts = stored(); out.status = status();",
        _cfg(_SQUARE),
    )
    assert out["verts"] == [[0.0, 2.0], [2.0, 2.5], [2.0, 4.0], [0.0, 4.0]]
    assert out["status"] == "Polygon changed. Save to keep it."


def test_an_unreadable_coordinate_keeps_the_vertex_and_shows_it_again_on_leaving() -> None:
    out = _editor(
        _ROWS + "typeInto(cell(0, 0), 'abc'); out.verts = stored(); leave(cell(0, 0)); out.shown = cell(0, 0).value;",
        _cfg(_SQUARE),
    )
    assert out["verts"] == _SQUARE
    assert out["shown"] == "0.000"


def test_a_vertex_is_removed_down_to_three() -> None:
    out = _editor(
        _ROWS
        + """
        document.querySelectorAll('#ground-grid-vertex-list button')[1].click();
        out.verts = stored(); out.rows = rows(); out.removable = removeButtons();
        document.querySelectorAll('#ground-grid-vertex-list button')[0].click();
        out.after = stored();
        """,
        _cfg(_SQUARE),
    )
    assert out["verts"] == [[0.0, 2.0], [2.0, 4.0], [0.0, 4.0]]
    assert out["rows"] == ["0.000", "2.000", "2.000", "4.000", "0.000", "4.000"]
    assert out["removable"] == [True] * 3
    assert out["after"] == out["verts"]


def test_dragging_a_corner_on_the_plan_moves_its_numbers_with_it() -> None:
    # (2, 4) is (810, 275) on the fitted plan, 85 px per metre.
    out = _editor(
        _ROWS
        + """
        at('mousedown', 810, 275); at('mousemove', 895, 275);
        out.during = [cell(2, 0).value, cell(2, 1).value];
        at('mouseup', 895, 275);
        """,
        _cfg(_SQUARE),
    )
    assert out["during"] == ["3.000", "4.000"]


def test_a_drawn_polygon_is_listed_and_a_cleared_one_is_not() -> None:
    out = _editor(
        _ROWS
        + """
        button('ground-grid-draw-btn');
        at('click', 640, 360); at('click', 740, 360); at('click', 740, 260);
        button('ground-grid-finish-btn');
        out.count = rows().length;
        button('ground-grid-clear-btn');
        out.cleared = rows(); out.hidden = listHidden();
        """
    )
    assert out == {"count": 6, "cleared": [], "hidden": True}


def test_a_corner_dropped_in_the_camera_view_moves_its_numbers() -> None:
    out = _editor(
        _CAMERA
        + _ROWS
        + """
        choose('camera'); await showPicture();
        pointer(svg().querySelectorAll('.pv-handle')[2], 'pointerdown', 840, -40);
        pointer(svg(), 'pointerup', 940, -40);
        await settle();
        out.row = [cell(2, 0).value, cell(2, 1).value];
        """,
        _cfg(_SQUARE),
    )
    assert out["row"] == ["3.000", "4.000"]


def test_an_imperial_station_types_feet_and_sees_what_is_stored() -> None:
    cfg = _cfg(_SQUARE)
    cfg.ui.unit_system = "imperial"
    out = _editor(
        _ROWS
        + """
        out.type = cell(0, 0).type;
        typeInto(cell(0, 0), '3 ft');
        out.x = stored()[0][0];
        out.echo = document.querySelector('[data-vertex-echo="0"]').textContent;
        """,
        cfg,
        setup="window.OpenFollow.units.setSystem('imperial');",
    )
    assert out["type"] == "text"
    assert out["x"] == pytest.approx(0.9144)
    assert out["echo"] == "Stored: 0.914 m, 2.000 m"
