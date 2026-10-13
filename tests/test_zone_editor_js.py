# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The Zone Editor's canvas on the shared polygon plan, run in node on the fake DOM."""

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

# A 1280x720 canvas whose client pixels are canvas pixels, what base.tpl gives
# every partial, and a station with one square zone on a 10 x 6 m grid at y = 3.
_PAGE = """
var ops = [];
El.prototype.getContext = function () {
    return new Proxy({}, {
        get: function (_, k) {
            if (k === 'measureText') return function () { return { width: 10 }; };
            return function () { ops.push(k); };
        },
        set: function () { return true; },
    });
};
var winListeners = {};
window.addEventListener = function (t, f) { (winListeners[t] = winListeners[t] || []).push(f); };
window.OpenFollow = window.OpenFollow || {};
window.OpenFollow.nextUnusedColor = function () { return '#33aaff'; };
window.OpenFollow.attachColorPicker = function () {};
window.switchRowTab = function () {};
window.markFormDirty = function () {};
window.markFormClean = function () {};
function modalConfirm() { return Promise.resolve(true); }
var model = {
    zones: [{ name: 'Square', color: '#ff8000', vertices: [[0, 2], [2, 2], [2, 4], [0, 4]], enabled: true,
              trigger_source: 'markers', triggered_by: [], is_occupied: false, occupant_count: 0 }],
};
station = function (req) {
    var m;
    if (req.url === '/api/zones' && req.method === 'GET') {
        return { body: { zones: JSON.parse(JSON.stringify(model.zones)), markers: [], destinations: [],
                         grid: { width: 10, depth: 6, spacing: 1, x_offset: 0, y_offset: 3 } } };
    }
    if (req.url === '/api/zones' && req.method === 'POST') {
        model.zones.push(req.body);
        return { body: { success: true, index: model.zones.length - 1 } };
    }
    if ((m = /^\\/api\\/zones\\/(\\d+)$/.exec(req.url)) && req.method === 'PUT') {
        Object.assign(model.zones[+m[1]], req.body);
        return { body: { success: true } };
    }
    return { body: '' };
};
"""

_SETUP_CANVAS = """
var canvas = document.getElementById('zone-canvas');
canvas.width = 1280; canvas.height = 720;
canvas.getBoundingClientRect = function () { return { left: 0, top: 0, width: 1280, height: 720 }; };
function at(type, x, y) { canvas.dispatchEvent(makeEvent(type, { clientX: x, clientY: y })); }
// The details panel is built from markup.
Object.defineProperty(document.getElementById('zone-details'), 'innerHTML', {
    set: function (v) { this.replaceChildren(); if (v) parseHTML(String(v), this); },
});
function button(id) { document.getElementById(id).click(); }
function posts() { return requests.filter(function (r) { return r.method === 'POST' && r.url === '/api/zones'; }); }
function puts() { return requests.filter(function (r) { return r.method === 'PUT'; }); }
"""


def _editor(steps: str) -> Any:
    rendered = template("partials/zone_editor", config=AppConfig())
    markup = rendered[: rendered.index("<script>")]
    start = rendered.index("<script>") + len("<script>")
    script = rendered[start : rendered.index("</script>", start)]
    source = "\n".join(
        [
            (_TESTS / "_fake_dom.js").read_text(encoding="utf-8"),
            _PAGE,
            (_STATIC / "units.js").read_text(encoding="utf-8"),
            (_STATIC / "save-feedback.js").read_text(encoding="utf-8"),
            (_STATIC / "polygon-plan.js").read_text(encoding="utf-8"),
            f"parseHTML({json.dumps(markup)}, document.body);",
            _SETUP_CANVAS,
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


def test_the_canvas_draws_once_the_zones_arrive() -> None:
    out = _editor("out.drew = ops.indexOf('fill') >= 0 && ops.indexOf('stroke') >= 0;")
    assert out["drew"] is True


def test_drawing_a_new_zone_posts_its_corners_in_world_metres() -> None:
    out = _editor(
        """
        button('zone-add-btn');
        // The canvas centre is the grid centre (0, 3).
        at('click', 640, 360); at('click', 740, 360); at('click', 740, 260);
        out.finishEnabled = !document.getElementById('zone-finish-btn').disabled;
        button('zone-finish-btn');
        await settle();
        out.posts = posts().map(function (r) { return r.body.vertices; });
        """
    )
    assert out["finishEnabled"] is True
    assert len(out["posts"]) == 1
    first, second, third = out["posts"][0]
    assert first == [pytest.approx(0.0), pytest.approx(3.0)]
    assert second[0] > 0 and second[1] == pytest.approx(3.0)
    assert third[0] == pytest.approx(second[0]) and third[1] > 3.0


def test_cancel_drawing_posts_nothing() -> None:
    out = _editor(
        """
        button('zone-add-btn');
        at('click', 640, 360); at('click', 740, 360);
        button('zone-cancel-btn');
        await settle();
        out.posts = posts().length;
        out.cancelDisabled = document.getElementById('zone-cancel-btn').disabled;
        """
    )
    assert out == {"posts": 0, "cancelDisabled": True}


def test_pressing_inside_a_zone_selects_it_and_dragging_a_corner_saves_it() -> None:
    # The fitted view spans 12 x 8 m around (0, 3) at 85 px per metre:
    # (1, 3) is (725, 360) and the corner (2, 4) is (810, 275).
    out = _editor(
        """
        at('mousedown', 725, 360); at('mouseup', 725, 360);
        await settle();
        out.deleteEnabled = !document.getElementById('zone-delete-btn').disabled;
        at('mousedown', 810, 275);
        at('mousemove', 895, 275);
        at('mouseup', 895, 275);
        await settle();
        out.puts = puts().map(function (r) { return r.body.vertices; });
        """
    )
    assert out["deleteEnabled"] is True
    assert len(out["puts"]) == 1
    moved = out["puts"][0]
    assert moved[:2] == [[0, 2], [2, 2]] and moved[3] == [0, 4]
    assert moved[2] == [pytest.approx(3.0), pytest.approx(4.0)]
