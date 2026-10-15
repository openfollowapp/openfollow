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
    // The picture shows stage (x, y) at pixel (640 + 100 x, 360 - 100 y).
    if (req.url === '/api/scene/project') {
        var seen = function (p) { return [640 + 100 * p[0], 360 - 100 * p[1]]; };
        return { body: { polylines: req.body.polylines.map(function (l) { return l.map(seen); }),
                         points: req.body.points.map(seen) } };
    }
    if (req.url === '/api/scene/unproject') {
        return { body: { x: (req.body.x - 640) / 100, y: (360 - req.body.y) / 100, z: req.body.z } };
    }
    if (req.url === '/api/video/snapshot/full') return { body: 'jpeg' };
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


def _editor(steps: str, setup: str = "") -> Any:
    rendered = template("partials/zone_editor", config=AppConfig())
    markup = rendered[: rendered.index("<script>")]
    start = rendered.index("<script>") + len("<script>")
    script = rendered[start : rendered.index("</script>", start)]
    source = "\n".join(
        [
            (_TESTS / "_fake_dom.js").read_text(encoding="utf-8"),
            _PAGE,
            (_STATIC / "poll-gate.js").read_text(encoding="utf-8"),
            (_STATIC / "units.js").read_text(encoding="utf-8"),
            (_STATIC / "save-feedback.js").read_text(encoding="utf-8"),
            (_STATIC / "polygon-plan.js").read_text(encoding="utf-8"),
            (_STATIC / "picture-view.js").read_text(encoding="utf-8"),
            f"parseHTML({json.dumps(markup)}, document.body);",
            _SETUP_CANVAS,
            setup,
            f"var editorMarkup = {json.dumps(markup)}, editorScript = {json.dumps(script)};",
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


# -- the camera view ---------------------------------------------------------------

_CAMERA = """
function choose(mode) {
    var radio = document.querySelector('#zone-view-switch input[value="' + mode + '"]');
    radio.checked = true;
    radio.dispatchEvent(makeEvent('change'));
}
async function showPicture() {
    await settle();
    var img = document.querySelector('#zone-picture img');
    img.naturalWidth = 1280; img.naturalHeight = 720;
    img.onload();
    await settle();
}
function svg() { return document.querySelector('#zone-picture svg'); }
function zoneLines() { return svg().querySelectorAll('polyline'); }
function cornerHandles() { return svg().querySelectorAll('.pv-handle'); }
function pointer(target, type, x, y) {
    target.dispatchEvent(makeEvent(type, { clientX: x, clientY: y, pointerId: 1 }));
}
"""


def test_camera_view_shows_the_zones_and_hides_the_plan() -> None:
    out = _editor(
        _CAMERA
        + """
        choose('camera'); await showPicture();
        out.planHidden = document.getElementById('zone-canvas-wrap').hidden;
        out.pictureShown = !document.getElementById('zone-picture').hidden;
        out.lines = zoneLines().map(function (l) { return l.getAttribute('points'); });
        out.addDisabled = document.getElementById('zone-add-btn').disabled;
        out.projected = requests.filter(function (r) { return r.url === '/api/scene/project'; }).map(function (r) {
            return r.body.polylines;
        })[0];
        """
    )
    assert out["planHidden"] is True and out["pictureShown"] is True
    assert out["addDisabled"] is True
    # The closed outline on the floor plane (the grid's z_offset, absent here: 0).
    assert out["projected"] == [[[0, 2, 0], [2, 2, 0], [2, 4, 0], [0, 4, 0], [0, 2, 0]]]
    assert out["lines"] == ["640,160 840,160 840,-40 640,-40 640,160"]


def test_camera_view_selects_a_zone_and_drags_its_corner_onto_the_floor() -> None:
    out = _editor(
        _CAMERA
        + """
        choose('camera'); await showPicture();
        out.before = cornerHandles().length;
        zoneLines()[0].dispatchEvent(makeEvent('click'));
        await settle();
        out.after = cornerHandles().length;
        // The corner (2, 4) shows at (840, -40); drop it at (940, -40), stage (3, 4).
        pointer(cornerHandles()[2], 'pointerdown', 840, -40);
        pointer(svg(), 'pointerup', 940, -40);
        await settle();
        var putReqs = requests.filter(function (r) { return r.method === 'PUT'; });
        out.puts = putReqs.map(function (r) { return r.body.vertices; });
        out.unproject = requests.filter(function (r) { return r.url === '/api/scene/unproject'; }).map(function (r) {
            return r.body.z;
        });
        """
    )
    assert out["before"] == 0 and out["after"] == 4
    assert out["unproject"] == [0]
    assert out["puts"] == [[[0, 2], [2, 2], [pytest.approx(3.0), pytest.approx(4.0)], [0, 4]]]


def test_no_picture_says_so() -> None:
    out = _editor(
        _CAMERA
        + """
        var real = station;
        station = function (req) {
            return req.url === '/api/video/snapshot/full' ? { status: 503, body: 'no feed' } : real(req);
        };
        choose('camera'); await settle();
        out.nofeed = !document.getElementById('zone-picture-nofeed').hidden;
        out.stage = document.getElementById('zone-picture').hidden;
        """
    )
    assert out == {"nofeed": True, "stage": True}


def test_the_chosen_view_is_remembered_in_this_browser() -> None:
    out = _editor(
        _CAMERA
        + "choose('plan'); out.saved = saved; out.planHidden = document.getElementById('zone-canvas-wrap').hidden;",
        setup="var saved = {}; globalThis.localStorage = { getItem: function (k) { return 'camera'; },"
        " setItem: function (k, v) { saved[k] = v; } };",
    )
    assert out == {"saved": {"openfollow:plan-view:zones": "plan"}, "planHidden": False}
    opened = _editor(
        "out.planHidden = document.getElementById('zone-canvas-wrap').hidden;",
        setup="globalThis.localStorage = { getItem: function () { return 'camera'; }, setItem: function () {} };",
    )
    assert opened["planHidden"] is True


def test_storage_that_throws_leaves_the_switch_working() -> None:
    out = _editor(
        _CAMERA
        + "choose('camera'); await settle(); out.planHidden = document.getElementById('zone-canvas-wrap').hidden;",
        setup="globalThis.localStorage = { getItem: function () { throw new Error('denied'); },"
        " setItem: function () { throw new Error('denied'); } };",
    )
    assert out["planHidden"] is True


def test_a_held_corner_follows_the_pointer_before_it_drops() -> None:
    out = _editor(
        _CAMERA
        + """
        choose('camera'); await showPicture();
        zoneLines()[0].dispatchEvent(makeEvent('click'));
        await settle();
        var corner = cornerHandles()[2];
        pointer(corner, 'pointerdown', 840, -40);
        pointer(svg(), 'pointermove', 900, -10);
        out.at = [corner.getAttribute('cx'), corner.getAttribute('cy')];
        out.puts = requests.filter(function (r) { return r.method === 'PUT'; }).length;
        """
    )
    assert out["at"] == ["900", "-10"]
    assert out["puts"] == 0


_ZONE_GETS = """
function zoneGets() {
    return requests.filter(function (r) { return r.method === 'GET' && r.url === '/api/zones'; }).length;
}
function section() { return document.getElementById('zone-editor-section'); }
function announce() { document.body.dispatchEvent(makeEvent('poll-shown')); }
"""


def test_the_editor_asks_for_the_zones_every_tick_while_it_is_in_view() -> None:
    out = _editor("var was = zoneGets(); tickIntervals(); await settle(); out.asked = zoneGets() - was;", _ZONE_GETS)
    assert out["asked"] == 1


@pytest.mark.parametrize(
    "hide",
    ["section().classList.add('is-collapsed');", "document.hidden = true;", "section().hidden = true;"],
    ids=["folded", "browser tab hidden", "another tab"],
)
def test_the_editor_asks_nothing_while_it_is_out_of_view(hide: str) -> None:
    steps = f"{hide} var was = zoneGets(); tickIntervals(); await settle(); out.asked = zoneGets() - was;"
    assert _editor(steps, _ZONE_GETS)["asked"] == 0


def test_coming_back_into_view_asks_at_once() -> None:
    steps = (
        "section().classList.add('is-collapsed'); var was = zoneGets();"
        "section().classList.remove('is-collapsed'); announce(); await settle(); out.asked = zoneGets() - was;"
    )
    assert _editor(steps, _ZONE_GETS)["asked"] == 1


def test_a_replaced_editor_stops_listening() -> None:
    # The section is swapped: the old editor's timer lets go on its next tick, and the new one starts.
    steps = (
        "section().remove(); tickIntervals(); parseHTML(editorMarkup, document.body); (0, eval)(editorScript);"
        "await settle(); var was = zoneGets(); announce(); await settle(); out.asked = zoneGets() - was;"
    )
    assert _editor(steps, _ZONE_GETS)["asked"] == 1
