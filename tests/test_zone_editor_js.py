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
# Its camera looks straight down from 6.4 m over (0, 3) with a 90 degree picture:
# stage (x, y) on the floor is picture pixel (640 + 100 x, 360 - 100 (y - 3)).
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
    camera: { pos_x: 0, pos_y: 3, pos_z: 6.4, pitch: -90, yaw: 0, roll: 0, fov: 90, k1: 0, k2: 0, cx: 0, cy: 0 },
    zones: [{ name: 'Square', color: '#ff8000', vertices: [[0, 2], [2, 2], [2, 4], [0, 4]], enabled: true,
              trigger_source: 'markers', triggered_by: [], is_occupied: false, occupant_count: 0,
              z_offset: 0, raise_markers: false, height_transition_s: 0.3 }],
};
station = function (req) {
    var m;
    if (req.url === '/api/zones' && req.method === 'GET') {
        return { body: { zones: JSON.parse(JSON.stringify(model.zones)), markers: [], destinations: [],
                         grid: { width: 10, depth: 6, spacing: 1, x_offset: 0, y_offset: 3,
                                 z_offset: model.floor || 0 },
                         camera: model.camera } };
    }
    if (req.url === '/api/zones' && req.method === 'POST') {
        model.zones.push(req.body);
        return { body: { success: true, index: model.zones.length - 1 } };
    }
    if (req.url === '/api/video/snapshot/full') return { body: 'jpeg' };
    if ((m = /^\\/api\\/zones\\/(\\d+)$/.exec(req.url)) && req.method === 'PUT') {
        if (model.refuse) return { status: 400, body: { error: model.refuse } };
        Object.assign(model.zones[+m[1]], JSON.parse(JSON.stringify(req.body)));
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
            (_STATIC / "camera-model.js").read_text(encoding="utf-8"),
            (_STATIC / "picture-polygon.js").read_text(encoding="utf-8"),
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
    # On stdin: the editor script outgrows the 128 KiB a single argv string may hold.
    done = subprocess.run([node, "-"], input=source, capture_output=True, text=True, timeout=20)  # nosec B603
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
function svg() { return document.getElementById('zone-picture-svg'); }
function zoneLines() { return svg().querySelectorAll('polyline.pv-zone'); }
function pins() { return svg().querySelectorAll('.pv-pin').map(function (g) { return g.getAttribute('aria-label'); }); }
function pin(n) { return svg().querySelector('.pv-pin[aria-label="V' + n + '"]'); }
function spot(n) {
    var m = /translate\\(([^,]+),([^)]+)\\)/.exec(pin(n).getAttribute('transform'));
    return [Math.round(+m[1] * 100) / 100, Math.round(+m[2] * 100) / 100];
}
function chosen() { var g = svg().querySelector('.pv-pin.active'); return g ? g.getAttribute('aria-label') : null; }
function ringed() {
    var r = svg().querySelector('.pv-pin-ring');
    return r ? r.parentNode.getAttribute('aria-label') : null;
}
function bands(i) { return svg().querySelectorAll('.pv-edge[data-edge="' + i + '"]'); }
function pointer(target, type, x, y, kind) {
    target.dispatchEvent(makeEvent(type, { clientX: x, clientY: y, pointerId: 1, pointerType: kind || 'mouse' }));
}
function drag(target, from, to, kind) {
    pointer(target, 'pointerdown', from[0], from[1], kind);
    pointer(svg(), 'pointermove', to[0], to[1], kind);
    pointer(svg(), 'pointerup', to[0], to[1], kind);
}
function key(k, shift) {
    var ev = makeEvent('keydown', { key: k, shiftKey: !!shift });
    (document.activeElement || svg()).dispatchEvent(ev);
    return ev.defaultPrevented;
}
function runTimeouts() { timeouts.splice(0).forEach(function (fn) { fn(); }); }
function round(v) {
    return v.map(function (p) { return [Math.round(p[0] * 1000) / 1000, Math.round(p[1] * 1000) / 1000]; });
}
function stored() { return puts().map(function (r) { return round(r.body.vertices); }); }
function loupe() { var l = document.querySelector('#zone-picture .pv-loupe'); return !!l && !l.hidden; }
function readout() {
    var r = document.querySelector('#zone-picture .pv-readout');
    return r && !r.hidden ? r.textContent : null;
}
// The camera view with the square chosen: V1 (640, 460), V2 (840, 460), V3 (840, 260), V4 (640, 260).
async function pick() {
    tickIntervals(); await settle();
    choose('camera'); await showPicture();
    pointer(zoneLines()[0], 'pointerdown', 740, 360);
    pointer(svg(), 'pointerup', 740, 360);
    await settle();
}
"""


def _camera(steps: str, setup: str = "") -> Any:
    return _editor(_CAMERA + steps, setup)


def test_camera_view_draws_the_zones_in_the_browser_and_hides_the_plan() -> None:
    out = _camera(
        """
        choose('camera'); await showPicture();
        out.planHidden = document.getElementById('zone-canvas-wrap').hidden;
        out.pictureShown = !document.getElementById('zone-picture').hidden;
        out.lines = zoneLines().map(function (l) { return l.getAttribute('points'); });
        out.addDisabled = document.getElementById('zone-add-btn').disabled;
        out.cornerButton = !document.getElementById('zone-corner-delete-btn').hidden;
        out.asked = requests.filter(function (r) { return r.url.indexOf('/api/scene/') === 0; }).length;
        out.pins = pins();
        """
    )
    assert out["planHidden"] is True and out["pictureShown"] is True
    assert out["addDisabled"] is True and out["cornerButton"] is True
    # No round trip: the saved camera came with the zones.
    assert out["asked"] == 0
    assert out["pins"] == []
    corners = [[float(v) for v in p.split(",")] for p in out["lines"][0].split(" ")]
    want = [[640, 460], [840, 460], [840, 260], [640, 260], [640, 460]]
    assert corners == [[pytest.approx(x, abs=1e-6), pytest.approx(y, abs=1e-6)] for x, y in want]


def test_the_plan_view_has_no_delete_corner_button() -> None:
    out = _camera(
        "choose('camera'); await showPicture(); choose('plan');"
        "out.hidden = document.getElementById('zone-corner-delete-btn').hidden;"
    )
    assert out == {"hidden": True}


def test_choosing_a_zone_names_its_corners_like_the_top_down_view() -> None:
    out = _camera(
        """
        await pick();
        out.pins = pins();
        out.spots = [1, 2, 3, 4].map(spot);
        out.chosen = zoneLines()[0].getAttribute('class');
        out.status = document.getElementById('zone-fs-status').textContent;
        """
    )
    assert out["pins"] == ["V1", "V2", "V3", "V4"]
    assert out["spots"] == [[640, 460], [840, 460], [840, 260], [640, 260]]
    assert out["chosen"] == "pv-zone pv-zone-selected"
    assert out["status"] == "Square · 4 corners"


def test_pressing_another_zone_chooses_it() -> None:
    out = _camera(
        """
        model.zones.push({ name: 'Other', color: '#33aaff', vertices: [[-3, 2], [-2, 2], [-2, 3]], enabled: true,
                           trigger_source: 'markers', triggered_by: [] });
        tickIntervals(); await pick();
        out.first = pins().length;
        pointer(zoneLines()[1], 'pointerdown', 400, 400);
        await settle();
        out.second = pins();
        out.classes = zoneLines().map(function (l) { return l.getAttribute('class'); });
        """
    )
    assert out["first"] == 4
    assert out["second"] == ["V1", "V2", "V3"]
    assert out["classes"] == ["pv-zone", "pv-zone pv-zone-selected"]


def test_a_dragged_corner_follows_with_the_loupe_and_saves_when_let_go() -> None:
    out = _camera(
        """
        await pick();
        pointer(pin(3), 'pointerdown', 840, 260);
        pointer(svg(), 'pointermove', 940, 260);
        out.during = { spot: spot(3), loupe: loupe(), readout: readout(), puts: puts().length,
                       line: zoneLines()[0].getAttribute('points') };
        pointer(svg(), 'pointerup', 940, 260);
        await settle();
        out.after = { loupe: loupe(), saved: stored(), chosen: chosen() };
        """
    )
    assert out["during"]["spot"] == [940, 260]
    assert out["during"]["loupe"] is True and out["during"]["puts"] == 0
    assert out["during"]["readout"] == "V3 · X 3.000 m · Y 4.000 m"
    assert "940" in out["during"]["line"]
    assert out["after"] == {"loupe": False, "saved": [[[0, 2], [2, 2], [3, 4], [0, 4]]], "chosen": "V3"}


@pytest.mark.parametrize(
    "kind, by, moves",
    [("mouse", 2, False), ("mouse", 3, True), ("touch", 7, False), ("pen", 7, False), ("touch", 8, True)],
    ids=["mouse 2 px", "mouse 3 px", "touch 7 px", "pen 7 px", "touch 8 px"],
)
def test_a_press_that_barely_moves_only_chooses_the_corner(kind: str, by: int, moves: bool) -> None:
    out = _camera(
        f"""
        await pick();
        pointer(pin(2), 'pointerdown', 840, 460, '{kind}');
        pointer(svg(), 'pointermove', 840 + {by}, 460, '{kind}');
        pointer(svg(), 'pointerup', 840 + {by}, 460, '{kind}');
        await settle();
        out.chosen = chosen(); out.spot = spot(2); out.puts = puts().length;
        """
    )
    assert out["chosen"] == "V2"
    assert out["spot"] == ([840 + by, 460] if moves else [840, 460])
    assert out["puts"] == (1 if moves else 0)


def test_a_cancelled_drag_saves_where_the_corner_was_left() -> None:
    out = _camera(
        """
        await pick();
        pointer(pin(3), 'pointerdown', 840, 260);
        pointer(svg(), 'pointermove', 860, 260);
        pointer(svg(), 'pointercancel', 860, 260);
        await settle();
        out.saved = stored(); out.loupe = loupe();
        """
    )
    assert out == {"saved": [[[0, 2], [2, 2], [2.2, 4], [0, 4]]], "loupe": False}


def test_a_touch_drag_holds_the_loupe_a_finger_away() -> None:
    out = _camera(
        """
        await pick();
        var box = { left: 0, top: 0, width: 1280, height: 720 };
        svg().getBoundingClientRect = function () { return box; };
        pointer(pin(3), 'pointerdown', 840, 260, 'mouse');
        pointer(svg(), 'pointermove', 900, 300, 'mouse');
        var l = document.querySelector('#zone-picture .pv-loupe');
        out.mouse = [l.style.left, l.style.top];
        pointer(svg(), 'pointerup', 900, 300, 'mouse');
        pointer(pin(3), 'pointerdown', 900, 300, 'touch');
        pointer(svg(), 'pointermove', 900, 400, 'touch');
        out.touch = [l.style.left, l.style.top];
        """
    )
    # Up and right of the point, its edge 12 px (mouse) or 30 px (touch) away along the diagonal.
    assert out["mouse"] == [f"{900 + (60 + 12) / 2**0.5 - 60}px", f"{300 - (60 + 12) / 2**0.5 - 60}px"]
    assert out["touch"] == [f"{900 + (60 + 30) / 2**0.5 - 60}px", f"{400 - (60 + 30) / 2**0.5 - 60}px"]


def test_keys_go_round_the_corners_and_tab_leaves_at_the_end() -> None:
    out = _camera(
        """
        await pick();
        svg().focus();
        out.steps = [];
        [[false], [false], [true], [true], [true]].forEach(function (s) {
            var held = key('Tab', s[0]);
            out.steps.push([chosen(), ringed(), held]);
        });
        """
    )
    assert out["steps"] == [
        ["V1", "V1", True],
        ["V2", "V2", True],
        ["V1", "V1", True],
        [None, None, False],
        [None, None, False],
    ]


def test_tab_past_the_last_corner_lets_go_and_leaves() -> None:
    out = _camera(
        """
        await pick();
        svg().focus();
        [1, 2, 3, 4].forEach(function () { key('Tab'); });
        out.last = chosen();
        out.held = key('Tab');
        out.after = chosen();
        """
    )
    assert out == {"last": "V4", "held": False, "after": None}


def test_corners_the_camera_does_not_see_have_no_pin_and_tab_skips_them() -> None:
    out = _camera(
        """
        // V2 sits 7 m upstage of the picture's top edge.
        model.zones[0].vertices = [[0, 2], [0, 10], [2, 4], [0, 4]];
        await pick();
        out.pins = pins();
        svg().focus();
        key('Tab'); var a = chosen(); key('Tab'); var b = chosen();
        out.tabs = [a, b];
        out.bands = [0, 1, 2, 3].map(function (i) { return bands(i).length > 0; });
        """
    )
    assert out["pins"] == ["V1", "V3", "V4"]
    assert out["tabs"] == ["V1", "V3"]
    assert out["bands"] == [True, True, True, True]


def test_arrows_move_the_corner_a_pixel_or_ten_and_save_once_after_the_last() -> None:
    out = _camera(
        """
        await pick();
        svg().focus();
        key('Tab'); key('Tab');
        out.held = key('ArrowRight');
        out.one = spot(2);
        key('ArrowUp', true);
        out.ten = spot(2);
        out.loupe = loupe();
        out.before = puts().length;
        runTimeouts(); await settle();
        out.saved = stored();
        out.loupeAfter = loupe();
        """
    )
    assert out["held"] is True
    assert out["one"] == [841, 460] and out["ten"] == [841, 450]
    assert out["loupe"] is True and out["before"] == 0
    assert out["saved"] == [[[0, 2], [2.01, 2.1], [2, 4], [0, 4]]]
    assert out["loupeAfter"] is False


@pytest.mark.parametrize("k, d", [("ArrowLeft", [-1, 0]), ("ArrowDown", [0, 1])])
def test_each_arrow_moves_its_own_way(k: str, d: list[int]) -> None:
    out = _camera(f"await pick(); svg().focus(); key('Tab'); key('{k}'); out.spot = spot(1);")
    assert out["spot"] == [640 + d[0], 460 + d[1]]


def test_an_arrow_that_would_leave_the_picture_moves_nothing() -> None:
    out = _camera(
        """
        model.zones[0].vertices = [[0, 2], [2, 2], [2, 4], [-6.4, 4]];
        await pick();
        svg().focus();
        [1, 2, 3, 4].forEach(function () { key('Tab'); });
        out.held = key('ArrowLeft');
        out.spot = spot(4);
        runTimeouts(); await settle();
        out.puts = puts().length;
        """
    )
    assert out == {"held": True, "spot": [0, 260], "puts": 0}


def test_keys_do_nothing_without_a_chosen_corner() -> None:
    out = _camera(
        "await pick(); svg().focus(); out.held = [key('ArrowRight'), key('Delete')]; out.puts = puts().length;"
    )
    assert out == {"held": [False, False], "puts": 0}


@pytest.mark.parametrize("k", ["Delete", "Backspace"])
def test_delete_removes_the_chosen_corner_and_saves(k: str) -> None:
    out = _camera(
        f"""
        await pick();
        svg().focus();
        key('Tab'); key('Tab');
        out.held = key('{k}');
        await settle();
        out.saved = stored(); out.pins = pins(); out.chosen = chosen();
        """
    )
    assert out["held"] is True
    assert out["saved"] == [[[0, 2], [2, 4], [0, 4]]]
    assert out["pins"] == ["V1", "V2", "V3"]
    assert out["chosen"] == "V2"


def test_a_zone_keeps_three_corners() -> None:
    out = _camera(
        """
        model.zones[0].vertices = [[0, 2], [2, 2], [2, 4]];
        await pick();
        pointer(pin(1), 'pointerdown', 640, 460); pointer(svg(), 'pointerup', 640, 460);
        out.button = document.getElementById('zone-corner-delete-btn').disabled;
        out.fsButton = document.getElementById('zone-fs-corner-delete').disabled;
        key('Delete'); document.getElementById('zone-corner-delete-btn').click();
        await settle();
        out.puts = puts().length; out.pins = pins().length;
        """
    )
    assert out == {"button": True, "fsButton": True, "puts": 0, "pins": 3}


def test_the_delete_corner_button_removes_the_chosen_corner() -> None:
    out = _camera(
        """
        await pick();
        out.idle = document.getElementById('zone-corner-delete-btn').disabled;
        pointer(pin(4), 'pointerdown', 640, 260); pointer(svg(), 'pointerup', 640, 260);
        out.ready = document.getElementById('zone-corner-delete-btn').disabled;
        document.getElementById('zone-corner-delete-btn').click();
        await settle();
        out.saved = stored(); out.chosen = chosen();
        out.after = document.getElementById('zone-corner-delete-btn').disabled;
        """
    )
    assert out["idle"] is True and out["ready"] is False
    assert out["saved"] == [[[0, 2], [2, 2], [2, 4]]]
    # The last corner gone, its neighbour before it is chosen; three left, no more deleting.
    assert out["chosen"] == "V3" and out["after"] is True


def test_deleting_a_corner_mid_key_edit_saves_once() -> None:
    out = _camera(
        """
        await pick();
        svg().focus();
        key('Tab'); key('ArrowRight'); key('Delete');
        runTimeouts(); await settle();
        out.saved = stored();
        """
    )
    assert out["saved"] == [[[2, 2], [2, 4], [0, 4]]]


def test_escape_lets_go_of_the_corner() -> None:
    out = _camera(
        """
        await pick();
        svg().focus();
        key('Tab');
        out.first = key('Escape');
        out.chosen = chosen();
        out.second = key('Escape');
        """
    )
    assert out == {"first": True, "chosen": None, "second": False}


def test_dragging_from_an_edge_adds_a_corner_between_its_two() -> None:
    out = _camera(
        """
        await pick();
        // The edge V1-V2 runs along y = 460.
        out.band = bands(0).length;
        pointer(bands(0)[0], 'pointerdown', 740, 460);
        pointer(svg(), 'pointermove', 740, 480);
        out.during = { pins: pins(), chosen: chosen(), spot: spot(2), puts: puts().length };
        pointer(svg(), 'pointermove', 760, 490);
        pointer(svg(), 'pointerup', 760, 490);
        await settle();
        out.saved = stored();
        out.status = document.getElementById('zone-fs-status').textContent;
        """
    )
    assert out["band"] == 1
    assert out["during"] == {"pins": ["V1", "V2", "V3", "V4", "V5"], "chosen": "V2", "spot": [740, 480], "puts": 0}
    assert out["saved"] == [[[0, 2], [1.2, 1.7], [2, 2], [2, 4], [0, 4]]]
    assert out["status"] == "Square · 5 corners"


@pytest.mark.parametrize("kind, by", [("mouse", 2), ("touch", 7), ("mouse", 0)])
def test_a_press_on_an_edge_that_does_not_move_adds_nothing(kind: str, by: int) -> None:
    out = _camera(
        f"""
        await pick();
        pointer(bands(1)[0], 'pointerdown', 840, 360, '{kind}');
        pointer(svg(), 'pointermove', 840 + {by}, 360, '{kind}');
        pointer(svg(), 'pointerup', 840 + {by}, 360, '{kind}');
        await settle();
        out.pins = pins().length; out.puts = puts().length;
        """
    )
    assert out == {"pins": 4, "puts": 0}


def test_the_edge_band_is_twenty_css_pixels_wide() -> None:
    out = _camera(
        """
        await pick();
        out.narrow = bands(0)[0].getAttribute('stroke-width');
        svg().getBoundingClientRect = function () { return { left: 0, top: 0, width: 640, height: 360 }; };
        window.dispatchEvent && null;
        (winListeners.resize || []).forEach(function (f) { f(); });
        out.half = bands(0)[0].getAttribute('stroke-width');
        out.ring = pin(1).querySelector('rect').getAttribute('width');
        """
    )
    # At half size a CSS pixel is two picture pixels: the band and the 44 px target double in picture pixels.
    assert out == {"narrow": "20", "half": "40", "ring": "88"}


def test_a_mouse_over_an_edge_shows_where_a_corner_would_start() -> None:
    out = _camera(
        """
        await pick();
        var ghost = function () { return svg().querySelector('.pv-ghost'); };
        pointer(bands(0)[0], 'pointermove', 700, 466);
        out.mouse = [ghost().style.display, ghost().getAttribute('cx'), ghost().getAttribute('cy')];
        pointer(bands(0)[0], 'pointermove', 700, 466, 'touch');
        out.touch = ghost().style.display;
        pointer(bands(0)[0], 'pointermove', 700, 466);
        svg().dispatchEvent(makeEvent('pointerleave', { pointerType: 'mouse' }));
        out.left = ghost().style.display;
        pointer(zoneLines()[0], 'pointermove', 700, 400);
        out.inside = ghost().style.display;
        """
    )
    assert (
        out["mouse"][0] == ""
        and float(out["mouse"][1]) == pytest.approx(700)
        and float(out["mouse"][2]) == pytest.approx(460)
    )
    assert out["touch"] == "none" and out["left"] == "none" and out["inside"] == "none"


def test_the_poll_does_not_undo_a_corner_in_hand() -> None:
    out = _camera(
        """
        await pick();
        pointer(pin(3), 'pointerdown', 840, 260);
        pointer(svg(), 'pointermove', 900, 260);
        var line = zoneLines()[0];
        tickIntervals(); await settle();
        out.drag = { spot: spot(3), same: zoneLines()[0] === line };
        pointer(svg(), 'pointerup', 900, 260);
        await settle();
        key('Tab');
        key('ArrowRight');
        tickIntervals(); await settle();
        out.key = spot(4);
        """
    )
    assert out["drag"] == {"spot": [900, 260], "same": True}
    assert out["key"] == [641, 260]


def test_a_poll_asked_before_an_edit_is_not_applied_after_it() -> None:
    out = _camera(
        """
        await pick();
        holdWhen = function (r) { return r.method === 'GET' && r.url === '/api/zones'; };
        tickIntervals();
        drag(pin(3), [840, 260], [900, 260]);
        holdWhen = null;
        // The save lands first, then the answer asked before the drag.
        await settle();
        release(); await settle();
        out.spot = spot(3);
        // The next poll, once the save has landed, carries the new corner.
        tickIntervals(); await settle();
        out.later = spot(3);
        """
    )
    assert out == {"spot": [900, 260], "later": [900, 260]}


def test_the_poll_holds_while_a_save_is_on_its_way() -> None:
    out = _camera(
        """
        await pick();
        holdWhen = function (r) { return r.method === 'PUT'; };
        drag(pin(3), [840, 260], [900, 260]);
        await settle();
        model.zones[0].vertices = [[0, 2], [2, 2], [2, 4], [0, 4]];
        tickIntervals(); await settle();
        out.spot = spot(3);
        """
    )
    assert out == {"spot": [900, 260]}


def test_a_poll_asked_while_a_save_was_on_its_way_is_not_applied() -> None:
    out = _camera(
        """
        await pick();
        holdWhen = function (r) { return r.method === 'PUT' || r.method === 'GET'; };
        drag(pin(3), [840, 260], [900, 260]);
        // The station has not taken the save in when the poll asks.
        var landed = JSON.parse(JSON.stringify(model.zones[0].vertices));
        model.zones[0].vertices = [[0, 2], [2, 2], [2, 4], [0, 4]];
        tickIntervals();
        model.zones[0].vertices = landed;
        holdWhen = null;
        releaseFirst(); await settle();
        release(); await settle();
        out.spot = spot(3);
        """
    )
    assert out == {"spot": [900, 260]}


def test_pressing_the_chosen_zone_keeps_its_details_open() -> None:
    out = _camera(
        """
        await pick();
        var panel = document.querySelector('#zone-details .group');
        pointer(zoneLines()[0], 'pointerdown', 700, 400);
        await settle();
        out.same = document.querySelector('#zone-details .group') === panel;
        """
    )
    assert out == {"same": True}


def test_an_idle_poll_does_not_redraw_the_picture() -> None:
    out = _camera(
        """
        await pick();
        var line = zoneLines()[0];
        tickIntervals(); await settle();
        out.same = zoneLines()[0] === line;
        model.zones[0].vertices[2] = [3, 4];
        tickIntervals(); await settle();
        out.moved = spot(3);
        """
    )
    assert out == {"same": True, "moved": [940, 260]}


def test_a_refused_corner_save_says_why() -> None:
    out = _camera(
        """
        await pick();
        model.refuse = 'Vertices must be finite';
        drag(pin(3), [840, 260], [900, 260]);
        await settle();
        var line = document.querySelector('#zone-editor-section .save-error');
        out.error = line ? line.textContent : null;
        """
    )
    assert out["error"] is not None and "Vertices must be finite" in out["error"]


def test_zones_sit_on_the_stage_plane_of_the_main_grid() -> None:
    out = _camera("model.floor = 1.6; await pick(); out.spot = spot(3); out.readout = null;")
    # 1.6 m up, the plane is 4.8 m from the camera: 133.3 px per metre.
    assert out["spot"] == [pytest.approx(640 + 2 * 400 / 3, abs=0.01), pytest.approx(360 - 400 / 3, abs=0.01)]


_HEIGHTS = "document.body.classList.add('feature-zone_heights');"


@pytest.mark.parametrize("on", [True, False], ids=["zone heights on", "zone heights off"])
def test_a_zone_is_drawn_and_picked_at_its_z_offset_only_with_zone_heights_on(on: bool) -> None:
    out = _camera(
        ("" if not on else _HEIGHTS)
        + """
        model.zones[0].z_offset = 1.6;
        await pick();
        out.spot = spot(3);
        pointer(pin(3), 'pointerdown', spot(3)[0], spot(3)[1]);
        pointer(svg(), 'pointermove', spot(3)[0] + 100, spot(3)[1]);
        out.readout = readout();
        pointer(svg(), 'pointerup', spot(3)[0] + 100, spot(3)[1]);
        await settle();
        out.saved = stored();
        """
    )
    if on:
        assert out["spot"] == [pytest.approx(906.67, abs=0.01), pytest.approx(226.67, abs=0.01)]
        # 100 px at 133.3 px per metre is 0.75 m.
        assert out["saved"] == [[[0, 2], [2, 2], [2.75, 4], [0, 4]]]
        assert out["readout"] == "V3 · X 2.750 m · Y 4.000 m · Z 1.600 m"
    else:
        assert out["spot"] == [840, 260]
        assert out["saved"] == [[[0, 2], [2, 2], [3, 4], [0, 4]]]
        assert out["readout"] == "V3 · X 3.000 m · Y 4.000 m"


def test_a_lens_bows_each_edge_in_chords_as_the_operator_screen_does() -> None:
    out = _camera(
        """
        model.camera.k1 = -0.2;
        await pick();
        out.points = zoneLines()[0].getAttribute('points').split(' ').length;
        out.band = bands(0)[0].getAttribute('points').split(' ').length;
        """
    )
    # 24 chords per edge, the closed outline's last point back on its first.
    assert out == {"points": 4 * 24 + 1, "band": 25}


def test_without_the_camera_the_picture_draws_nothing() -> None:
    out = _camera(
        "model.camera = undefined; tickIntervals(); choose('camera'); await showPicture();"
        "out.lines = zoneLines().length;"
    )
    assert out == {"lines": 0}


def test_the_picture_takes_the_focus_and_names_the_chosen_corner() -> None:
    out = _camera(
        """
        await pick();
        out.focusable = [svg().getAttribute('tabindex'), svg().getAttribute('aria-label')];
        svg().focus(); key('Tab'); key('Tab');
        out.active = svg().getAttribute('aria-activedescendant');
        out.target = pin(2).getAttribute('id');
        key('Escape');
        out.after = svg().getAttribute('aria-activedescendant');
        """
    )
    assert out["focusable"] == ["0", "Zone corners on the camera picture"]
    assert out["active"] == out["target"] == "zone-picture-svg-v2"
    assert out["after"] is None


def test_pressing_a_corner_focuses_the_picture_for_its_keys() -> None:
    out = _camera(
        """
        await pick();
        pointer(pin(2), 'pointerdown', 840, 460); pointer(svg(), 'pointerup', 840, 460);
        out.focused = document.activeElement === svg();
        out.ring = ringed();
        key('ArrowDown');
        out.spot = spot(2); out.ringAfter = ringed();
        """
    )
    assert out == {"focused": True, "ring": None, "spot": [840, 461], "ringAfter": "V2"}


def test_leaving_the_picture_saves_a_key_edit_at_once() -> None:
    out = _camera(
        """
        await pick();
        svg().focus(); key('Tab'); key('ArrowRight');
        document.getElementById('zone-refresh-btn').focus();
        await settle();
        out.saved = stored();
        runTimeouts(); await settle();
        out.again = puts().length;
        """
    )
    assert out == {"saved": [[[0.01, 2], [2, 2], [2, 4], [0, 4]]], "again": 1}


# -- full screen ---------------------------------------------------------------

_FULL = """
function shell() { return document.getElementById('zone-fs-shell'); }
function inShell() { return shell().contains(document.getElementById('zone-picture')); }
"""


def test_full_screen_moves_the_picture_into_its_own_view() -> None:
    out = _camera(
        _FULL
        + """
        await pick();
        document.getElementById('zone-fs-btn').click();
        out.open = { shown: !shell().hidden, onBody: shell().parentNode === document.body, picture: inShell(),
                     focus: document.activeElement === svg(),
                     status: document.getElementById('zone-fs-status').textContent,
                     scroll: document.body.style.overflow };
        document.getElementById('zone-fs-exit').click();
        out.closed = { shown: !shell().hidden,
                       inSection: document.getElementById('zone-editor-section').contains(shell()),
                       picture: inShell(), back: document.getElementById('zone-canvas-wrap').previousSibling.id,
                       focus: document.activeElement.id, scroll: document.body.style.overflow };
        """
    )
    assert out["open"] == {
        "shown": True,
        "onBody": True,
        "picture": True,
        "focus": True,
        "status": "Square · 4 corners",
        "scroll": "hidden",
    }
    assert out["closed"] == {
        "shown": False,
        "inSection": True,
        "picture": False,
        "back": "zone-picture-nofeed",
        "focus": "zone-fs-btn",
        "scroll": "",
    }


def test_escape_lets_go_of_the_corner_then_leaves_full_screen() -> None:
    out = _camera(
        _FULL
        + """
        await pick();
        document.getElementById('zone-fs-btn').click();
        key('Tab');
        key('Escape');
        out.first = [chosen(), !shell().hidden];
        key('Escape');
        out.second = !shell().hidden;
        """
    )
    assert out == {"first": [None, True], "second": False}


def test_escape_on_a_bar_button_leaves_full_screen() -> None:
    out = _camera(
        _FULL
        + """
        await pick();
        document.getElementById('zone-fs-btn').click();
        document.getElementById('zone-fs-refresh').focus();
        out.held = key('Escape');
        out.open = !shell().hidden;
        """
    )
    assert out == {"held": True, "open": False}


def test_the_bar_refreshes_the_picture_and_deletes_the_chosen_corner() -> None:
    out = _camera(
        _FULL
        + """
        await pick();
        document.getElementById('zone-fs-btn').click();
        var snaps = function () {
            return requests.filter(function (r) { return r.url === '/api/video/snapshot/full'; }).length;
        };
        var was = snaps();
        document.getElementById('zone-fs-refresh').click();
        await settle();
        out.refreshed = snaps() - was;
        out.idle = document.getElementById('zone-fs-corner-delete').disabled;
        svg().focus(); key('Tab');
        out.ready = document.getElementById('zone-fs-corner-delete').disabled;
        document.getElementById('zone-fs-corner-delete').click();
        await settle();
        out.saved = stored();
        out.status = document.getElementById('zone-fs-status').textContent;
        """
    )
    assert out["refreshed"] == 1
    assert out["idle"] is True and out["ready"] is False
    assert out["saved"] == [[[2, 2], [2, 4], [0, 4]]]
    assert out["status"] == "Square · 3 corners"


def test_tab_stays_inside_full_screen() -> None:
    out = _camera(
        _FULL
        + """
        await pick();
        document.getElementById('zone-fs-btn').click();
        document.getElementById('zone-fs-refresh').focus();
        out.back = key('Tab', true); out.toLast = document.activeElement.id;
        // Entered from after it, the picture starts at its last corner; Tab past it goes on to the bar's first button.
        svg().dispatchEvent(makeEvent('keyup', { key: 'Tab' }));
        out.entered = chosen();
        out.forward = key('Tab'); out.toFirst = document.activeElement.id;
        document.getElementById('zone-fs-refresh').focus();
        out.middle = key('Tab');
        """
    )
    assert out == {
        "back": True,
        "toLast": "zone-picture-svg",
        "entered": "V4",
        "forward": True,
        "toFirst": "zone-fs-refresh",
        "middle": False,
    }


def test_full_screen_sizes_the_picture_to_the_screen() -> None:
    out = _camera(
        _FULL
        + """
        await pick();
        var fsStage = document.getElementById('zone-fs-stage');
        fsStage.getBoundingClientRect = function () { return { width: 2000, height: 900 }; };
        document.getElementById('zone-fs-btn').click();
        out.wide = document.getElementById('zone-picture').style.width;
        fsStage.getBoundingClientRect = function () { return { width: 1000, height: 900 }; };
        (winListeners.resize || []).forEach(function (f) { f(); });
        out.narrow = document.getElementById('zone-picture').style.width;
        document.getElementById('zone-fs-exit').click();
        out.closed = document.getElementById('zone-picture').style.width;
        """
    )
    assert out == {"wide": "1600px", "narrow": "1000px", "closed": ""}


def test_switching_to_the_top_down_view_lets_go_of_the_corner() -> None:
    out = _camera(
        """
        await pick();
        svg().focus(); key('Tab'); key('ArrowRight');
        choose('plan'); await settle();
        out.saved = stored();
        choose('camera'); await showPicture();
        out.chosen = chosen();
        """
    )
    assert out == {"saved": [[[0.01, 2], [2, 2], [2, 4], [0, 4]]], "chosen": None}


def test_the_zone_delete_button_is_named_for_the_zone() -> None:
    out = _camera("out.text = document.getElementById('zone-delete-btn').textContent;")
    assert out == {"text": "Delete zone"}


def test_no_picture_says_so() -> None:
    out = _camera(
        """
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


# -- a drag always ends ----------------------------------------------------------------


def test_escape_mid_drag_puts_the_corner_back_and_saves_nothing() -> None:
    out = _camera(
        _FULL
        + """
        await pick();
        document.getElementById('zone-fs-btn').click();
        pointer(pin(3), 'pointerdown', 840, 260);
        pointer(svg(), 'pointermove', 900, 260);
        out.held = key('Escape');
        out.open = !shell().hidden;
        out.spot = spot(3); out.loupe = loupe();
        model.zones[0].name = 'Renamed';
        tickIntervals(); await settle();
        out.status = document.getElementById('zone-fs-status').textContent;
        out.puts = puts().length;
        pointer(svg(), 'pointerup', 900, 260);
        await settle();
        out.after = puts().length;
        out.tab = key('Tab');
        """
    )
    assert out == {
        "held": True,
        "open": True,
        "spot": [840, 260],
        "loupe": False,
        "status": "Renamed · 4 corners",
        "puts": 0,
        "after": 0,
        "tab": True,
    }


def test_escape_mid_edge_drag_takes_the_new_corner_away() -> None:
    out = _camera(
        """
        await pick();
        pointer(bands(0)[0], 'pointerdown', 740, 460);
        pointer(svg(), 'pointermove', 740, 480);
        out.during = pins().length;
        key('Escape');
        await settle();
        out.pins = pins(); out.chosen = chosen(); out.puts = puts().length;
        out.area = document.querySelectorAll('#zone-details [data-vertex-field="x"]').length;
        """
    )
    assert out == {"during": 5, "pins": ["V1", "V2", "V3", "V4"], "chosen": None, "puts": 0, "area": 4}


def test_leaving_full_screen_mid_drag_ends_the_drag() -> None:
    out = _camera(
        _FULL
        + """
        await pick();
        document.getElementById('zone-fs-btn').click();
        pointer(pin(3), 'pointerdown', 840, 260);
        pointer(svg(), 'pointermove', 900, 260);
        document.getElementById('zone-fs-exit').click();
        out.spot = spot(3); out.loupe = loupe();
        model.zones[0].vertices[0] = [0.5, 2];
        tickIntervals(); await settle();
        out.polled = spot(1);
        out.puts = puts().length;
        """
    )
    assert out == {"spot": [840, 260], "loupe": False, "polled": [690, 460], "puts": 0}


@pytest.mark.parametrize("how", ["lostpointercapture", "pointercancel"])
def test_a_drag_the_browser_takes_away_keeps_the_corner_and_saves(how: str) -> None:
    out = _camera(
        f"""
        await pick();
        pointer(pin(3), 'pointerdown', 840, 260);
        pointer(svg(), 'pointermove', 900, 260);
        pointer(svg(), '{how}', 900, 260);
        await settle();
        out.saved = stored(); out.loupe = loupe();
        model.zones[0].name = 'Renamed';
        tickIntervals(); await settle();
        out.status = document.getElementById('zone-fs-status').textContent;
        """
    )
    assert out == {"saved": [[[0, 2], [2, 2], [2.6, 4], [0, 4]]], "loupe": False, "status": "Renamed · 4 corners"}


def test_a_second_pointer_does_not_take_over_a_drag() -> None:
    out = _camera(
        """
        function touch(target, type, x, y, id) {
            target.dispatchEvent(makeEvent(type, { clientX: x, clientY: y, pointerId: id, pointerType: 'touch' }));
        }
        await pick();
        touch(pin(3), 'pointerdown', 840, 260, 1);
        touch(svg(), 'pointermove', 900, 260, 1);
        touch(pin(1), 'pointerdown', 640, 460, 2);
        touch(svg(), 'pointermove', 600, 460, 2);
        touch(svg(), 'pointerup', 900, 260, 1);
        touch(svg(), 'pointerup', 600, 460, 2);
        await settle();
        out.saved = stored(); out.chosen = chosen();
        tickIntervals(); await settle();
        out.spots = [spot(1), spot(3)];
        """
    )
    assert out == {"saved": [[[0, 2], [2, 2], [2.6, 4], [0, 4]]], "chosen": "V3", "spots": [[640, 460], [900, 260]]}


def test_after_a_delete_the_nearest_seen_corner_is_chosen() -> None:
    out = _camera(
        """
        // V4 sits upstage of the picture.
        model.zones[0].vertices = [[0, 2], [2, 2], [2, 4], [0, 10], [0, 4]];
        await pick();
        pointer(pin(3), 'pointerdown', 840, 260); pointer(svg(), 'pointerup', 840, 260);
        key('Delete');
        out.chosen = chosen();
        key('Delete');
        await settle();
        out.saved = stored();
        """
    )
    assert out["chosen"] == "V2"
    assert out["saved"][-1] == [[0, 2], [0, 10], [0, 4]]


def test_after_a_delete_with_no_seen_corner_left_nothing_is_chosen() -> None:
    out = _camera(
        """
        model.zones[0].vertices = [[0, 2], [0, 10], [1, 10], [2, 10]];
        await pick();
        pointer(pin(1), 'pointerdown', 640, 460); pointer(svg(), 'pointerup', 640, 460);
        document.getElementById('zone-corner-delete-btn').click();
        await settle();
        out.chosen = chosen(); out.puts = puts().length;
        out.button = document.getElementById('zone-corner-delete-btn').disabled;
        """
    )
    assert out == {"chosen": None, "puts": 1, "button": True}


# -- saves in order --------------------------------------------------------------------


def test_corner_saves_go_one_at_a_time_and_the_latest_follows() -> None:
    # Drags while one save is out make one save after it, with the corners as they are then.
    out = _camera(
        """
        await pick();
        holdWhen = function (r) { return r.method === 'PUT'; };
        drag(pin(3), [840, 260], [900, 260]);
        await settle();
        drag(pin(3), [900, 260], [920, 260]);
        drag(pin(3), [920, 260], [940, 260]);
        await settle();
        out.first = puts().length;
        releaseFirst(); await settle();
        out.second = stored();
        holdWhen = null;
        release(); await settle();
        out.total = puts().length;
        """
    )
    assert out["first"] == 1
    assert out["second"] == [[[0, 2], [2, 2], [2.6, 4], [0, 4]], [[0, 2], [2, 2], [3, 4], [0, 4]]]
    assert out["total"] == 2


def test_a_corner_save_that_hangs_is_given_up_and_says_so() -> None:
    out = _camera(
        """
        await pick();
        holdWhen = function (r) { return r.method === 'PUT'; };
        drag(pin(3), [840, 260], [900, 260]);
        await settle();
        runTimeouts(); await settle();
        var line = document.querySelector('#zone-editor-section .save-error');
        out.error = line ? line.textContent : null;
        holdWhen = null;
        model.zones[0].name = 'Renamed';
        tickIntervals(); await settle();
        out.status = document.getElementById('zone-fs-status').textContent;
        """
    )
    assert out["error"] is not None and "The station did not answer." in out["error"]
    assert out["status"] == "Renamed · 4 corners"


# -- screen readers and Shift+Tab --------------------------------------------------------


def test_the_stage_position_is_announced_once_when_the_drag_ends() -> None:
    out = _camera(
        """
        await pick();
        pointer(pin(3), 'pointerdown', 840, 260);
        pointer(svg(), 'pointermove', 900, 260);
        var live = function () { return document.querySelector('#zone-picture [aria-live]'); };
        out.readoutLive = document.querySelector('#zone-picture .pv-readout').getAttribute('aria-live');
        out.during = live() ? live().textContent : '';
        pointer(svg(), 'pointerup', 900, 260);
        out.after = live().textContent;
        """
    )
    assert out == {"readoutLive": None, "during": "", "after": "V3 · X 2.600 m · Y 4.000 m"}


def test_the_stage_position_is_announced_when_a_key_edit_is_saved() -> None:
    out = _camera(
        """
        await pick();
        var live = function () {
            var l = document.querySelector('#zone-picture [aria-live]');
            return l ? l.textContent : '';
        };
        svg().focus(); key('Tab'); key('ArrowRight');
        out.during = live();
        runTimeouts();
        out.after = live();
        """
    )
    assert out == {"during": "", "after": "V1 · X 0.010 m · Y 2.000 m"}


def test_shift_tab_into_the_picture_enters_at_the_last_seen_corner() -> None:
    out = _camera(
        """
        model.zones[0].vertices = [[0, 2], [2, 2], [2, 4], [0, 10]];
        await pick();
        var after = document.getElementById('zone-fs-btn');
        after.focus();
        after.dispatchEvent(makeEvent('keydown', { key: 'Tab', shiftKey: true }));
        svg().focus();
        out.back = [chosen(), ringed()];
        svg().dispatchEvent(makeEvent('keyup', { key: 'Tab', shiftKey: true }));
        key('Escape'); svg().blur();
        document.body.dispatchEvent(makeEvent('keydown', { key: 'Tab' }));
        svg().focus();
        out.forward = chosen();
        """
    )
    assert out == {"back": ["V3", "V3"], "forward": None}


def test_after_a_delete_a_seen_corner_further_on_is_chosen_when_none_before_is() -> None:
    out = _camera(
        """
        // V1 and V3 sit upstage of the picture.
        model.zones[0].vertices = [[0, 10], [2, 2], [1, 10], [2, 4], [0, 4]];
        await pick();
        pointer(pin(2), 'pointerdown', 840, 460); pointer(svg(), 'pointerup', 840, 460);
        key('Delete');
        out.chosen = chosen();
        """
    )
    assert out == {"chosen": "V3"}


def test_a_focus_after_shift_tab_was_let_go_enters_with_nothing_chosen() -> None:
    out = _camera(
        """
        await pick();
        document.body.dispatchEvent(makeEvent('keydown', { key: 'Tab', shiftKey: true }));
        document.body.dispatchEvent(makeEvent('keyup', { key: 'Tab', shiftKey: true }));
        svg().focus();
        out.chosen = chosen();
        """
    )
    assert out == {"chosen": None}


# -- saves and deletes keep to their zone ----------------------------------------------

_THREE = """
model.zones.unshift({ name: 'Other', color: '#33aaff', vertices: [[-3, 2], [-2, 2], [-2, 3]], enabled: true,
                      trigger_source: 'markers', triggered_by: [] });
model.zones.push({ name: 'Third', color: '#33aa00', vertices: [[3, 2], [4, 2], [4, 3]], enabled: true,
                   trigger_source: 'markers', triggered_by: [] });
var base = station;
station = function (req) {
    var m = /^\\/api\\/zones\\/(\\d+)$/.exec(req.url);
    if (m && req.method === 'DELETE') {
        if (+m[1] >= model.zones.length) return { status: 404, body: { error: 'Zone index out of range' } };
        model.zones.splice(+m[1], 1);
        return { body: { success: true } };
    }
    if (m && req.method === 'PUT' && +m[1] >= model.zones.length) {
        return { status: 404, body: { error: 'Zone index out of range' } };
    }
    return base(req);
};
"""

_ZONE_STEPS = """
function line(i) { return zoneLines().filter(function (l) { return l.dataset.zone === String(i); })[0]; }
async function chooseZone(i, x, y) {
    pointer(line(i), 'pointerdown', x, y); pointer(svg(), 'pointerup', x, y); await settle();
}
function writes() {
    var sent = requests.filter(function (r) { return r.method !== 'GET'; });
    return sent.map(function (r) { return r.method + ' ' + r.url; });
}
function names() { return model.zones.map(function (z) { return z.name; }); }
function errorLine() {
    var e = document.querySelector('#zone-editor-section .save-error');
    return e ? e.textContent : null;
}
"""


def test_a_queued_corner_save_follows_its_zone_past_a_delete() -> None:
    out = _camera(
        _ZONE_STEPS
        + """
        tickIntervals(); await settle();
        choose('camera'); await showPicture();
        await chooseZone(1, 740, 360);
        holdWhen = function (r) { return r.method === 'PUT'; };
        drag(pin(3), [840, 260], [900, 260]);
        await settle();
        drag(pin(3), [900, 260], [920, 260]);
        await settle();
        await chooseZone(0, 400, 400);
        document.getElementById('zone-delete-btn').click();
        await settle();
        out.held = writes();
        holdWhen = null;
        release(); await settle();
        out.writes = writes();
        out.names = names();
        out.square = model.zones[0].vertices;
        out.third = model.zones[1].vertices;
        out.error = errorLine();
        """,
        _THREE,
    )
    assert out["held"] == ["PUT /api/zones/1"]
    assert out["writes"] == ["PUT /api/zones/1", "PUT /api/zones/1", "DELETE /api/zones/0"]
    assert out["names"] == ["Square", "Third"]
    assert [[round(v, 3) for v in p] for p in out["square"]] == [[0, 2], [2, 2], [2.8, 4], [0, 4]]
    assert out["third"] == [[3, 2], [4, 2], [4, 3]]
    assert out["error"] is None


def test_a_corner_save_asked_after_its_zone_was_deleted_is_dropped() -> None:
    out = _camera(
        _ZONE_STEPS
        + """
        tickIntervals(); await settle();
        choose('camera'); await showPicture();
        await chooseZone(2, 1040, 360);
        out.pins = pins();
        holdWhen = function (r) { return r.method === 'PUT'; };
        drag(pin(2), [1040, 460], [1060, 460]);
        await settle();
        document.getElementById('zone-delete-btn').click();
        await settle();
        drag(pin(2), [1060, 460], [1080, 460]);
        await settle();
        holdWhen = null;
        release(); await settle();
        out.writes = writes();
        out.names = names();
        out.error = errorLine();
        """,
        _THREE,
    )
    assert out["pins"] == ["V1", "V2", "V3"]
    assert out["writes"] == ["PUT /api/zones/2", "DELETE /api/zones/2"]
    assert out["names"] == ["Other", "Square"]
    assert out["error"] is None


def test_the_last_zone_deleted_twice_over_reports_the_station_answer() -> None:
    # Another browser deleted it first: the station's 404 is shown, nothing else is written.
    out = _camera(
        _ZONE_STEPS
        + """
        tickIntervals(); await settle();
        choose('camera'); await showPicture();
        await chooseZone(2, 1040, 360);
        model.zones.pop();
        document.getElementById('zone-delete-btn').click();
        await settle();
        out.writes = writes();
        out.error = errorLine();
        """,
        _THREE,
    )
    assert out["writes"] == ["DELETE /api/zones/2"]
    assert out["error"] is not None and "Zone index out of range" in out["error"]


def test_delete_zone_mid_drag_lets_go_of_the_corner_first() -> None:
    out = _camera(
        _ZONE_STEPS
        + """
        await pick();
        pointer(pin(3), 'pointerdown', 840, 260, 'touch');
        pointer(svg(), 'pointermove', 900, 260, 'touch');
        document.getElementById('zone-delete-btn').click();
        await settle();
        out.writes = writes();
        out.loupe = loupe();
        """,
        _THREE.replace("model.zones.unshift", "void").replace("model.zones.push", "void"),
    )
    assert out == {"writes": ["DELETE /api/zones/0"], "loupe": False}


def test_no_corner_is_deleted_during_a_drag() -> None:
    out = _camera(
        """
        await pick();
        pointer(pin(3), 'pointerdown', 840, 260, 'touch');
        pointer(svg(), 'pointermove', 900, 260, 'touch');
        out.buttons = [document.getElementById('zone-corner-delete-btn').disabled,
                       document.getElementById('zone-fs-corner-delete').disabled];
        document.getElementById('zone-corner-delete-btn').click();
        document.getElementById('zone-fs-corner-delete').click();
        out.pins = pins().length;
        pointer(svg(), 'pointerup', 900, 260, 'touch');
        await settle();
        out.after = document.getElementById('zone-corner-delete-btn').disabled;
        out.saved = stored();
        """
    )
    assert out == {"buttons": [True, True], "pins": 4, "after": False, "saved": [[[0, 2], [2, 2], [2.6, 4], [0, 4]]]}


def test_switching_to_the_top_down_view_mid_drag_puts_the_corner_back() -> None:
    out = _camera(
        """
        await pick();
        pointer(pin(3), 'pointerdown', 840, 260);
        pointer(svg(), 'pointermove', 900, 260);
        choose('plan'); await settle();
        out.puts = puts().length;
        model.zones[0].name = 'Renamed';
        tickIntervals(); await settle();
        choose('camera'); await showPicture();
        pointer(zoneLines()[0], 'pointerdown', 740, 360); pointer(svg(), 'pointerup', 740, 360); await settle();
        out.spot = spot(3);
        out.status = document.getElementById('zone-fs-status').textContent;
        """
    )
    assert out == {"puts": 0, "spot": [840, 260], "status": "Renamed · 4 corners"}


@pytest.mark.parametrize("button", [1, 2])
def test_a_press_with_another_mouse_button_starts_nothing(button: int) -> None:
    out = _camera(
        f"""
        await pick();
        var press = function (target, type, x, y) {{
            target.dispatchEvent(makeEvent(type, {{ clientX: x, clientY: y, pointerId: 1, pointerType: 'mouse',
                                                   button: {button}, buttons: {1 << (button)} }}));
        }};
        press(pin(3), 'pointerdown', 840, 260);
        press(svg(), 'pointermove', 900, 260);
        press(svg(), 'pointerup', 900, 260);
        press(bands(0)[0], 'pointerdown', 740, 460);
        press(svg(), 'pointermove', 740, 480);
        await settle();
        out.spot = spot(3); out.pins = pins().length; out.chosen = chosen(); out.puts = puts().length;
        """
    )
    assert out == {"spot": [840, 260], "pins": 4, "chosen": None, "puts": 0}


def test_a_context_menu_mid_drag_puts_the_corner_back() -> None:
    out = _camera(
        """
        await pick();
        pointer(pin(3), 'pointerdown', 840, 260);
        pointer(svg(), 'pointermove', 900, 260);
        svg().dispatchEvent(makeEvent('contextmenu', { clientX: 900, clientY: 260 }));
        await settle();
        out.spot = spot(3); out.puts = puts().length; out.loupe = loupe();
        """
    )
    assert out == {"spot": [840, 260], "puts": 0, "loupe": False}


def test_a_move_with_no_button_held_ends_the_drag_where_it_is() -> None:
    out = _camera(
        """
        await pick();
        pointer(pin(3), 'pointerdown', 840, 260);
        var move = function (x, held) {
            svg().dispatchEvent(makeEvent('pointermove', { clientX: x, clientY: 260, pointerId: 1, pointerType: 'mouse',
                                                         buttons: held }));
        };
        move(900, 1);
        move(960, 0);
        await settle();
        out.saved = stored(); out.loupe = loupe(); out.spot = spot(3);
        """
    )
    assert out == {"saved": [[[0, 2], [2, 2], [2.6, 4], [0, 4]]], "loupe": False, "spot": [900, 260]}


# -- Esc and the help drawer, the live region, listeners ---------------------------------

_HELP_ESC = """
var helpClosed = 0;
function closeHelp() { helpClosed++; }
"""


def _help_esc_listener() -> str:
    base = (_TESTS.parent / "openfollow" / "web" / "templates" / "base.tpl").read_text(encoding="utf-8")
    start = base.rindex("document.addEventListener('keydown'", 0, base.index("// An Esc a control already used"))
    return base[start : base.index("\n });", start) + len("\n });")]


def test_an_esc_the_picture_used_leaves_the_help_drawer_open() -> None:
    out = _camera(
        _HELP_ESC
        + _help_esc_listener()
        + """
        await pick();
        document.body.classList.add('help-open');
        svg().focus(); key('Tab');
        key('Escape');
        out.used = helpClosed;
        key('Escape');
        out.unused = helpClosed;
        """
    )
    assert out == {"used": 0, "unused": 1}


def test_the_live_region_is_in_place_before_the_first_announcement() -> None:
    out = _camera("out.live = !!document.querySelector('#zone-picture [aria-live]');")
    assert out == {"live": True}


def test_a_new_picture_polygon_adds_no_document_listeners() -> None:
    out = _camera(
        """
        var count = function () { return Object.keys(document.listeners).reduce(function (n, k) {
            return n + document.listeners[k].length; }, 0); };
        var was = count();
        new window.OpenFollow.PicturePolygon(new window.OpenFollow.PictureView({
            stage: document.createElement('div'), img: document.createElement('img'),
            svg: document.createElementNS('http://www.w3.org/2000/svg', 'svg'), nofeed: document.createElement('div'),
        }), { corners: function () { return null; } });
        out.added = count() - was;
        """
    )
    assert out == {"added": 0}


def test_a_deleted_zone_brings_the_list_up_to_date_at_once() -> None:
    out = _camera(
        _ZONE_STEPS
        + """
        tickIntervals(); await settle();
        choose('camera'); await showPicture();
        await chooseZone(0, 400, 400);
        model.zones[1].name = 'Renamed';
        var gets = function () {
            return requests.filter(function (r) { return r.method === 'GET' && r.url === '/api/zones'; }).length;
        };
        var was = gets();
        document.getElementById('zone-delete-btn').click();
        await settle();
        out.asked = gets() - was;
        out.lines = zoneLines().length;
        await chooseZone(0, 740, 360);
        out.status = document.getElementById('zone-fs-status').textContent;
        """,
        _THREE,
    )
    assert out == {"asked": 1, "lines": 2, "status": "Renamed · 4 corners"}


@pytest.mark.parametrize("kind", ["touch", "pen"])
def test_a_long_press_keeps_a_touch_or_pen_drag(kind: str) -> None:
    out = _camera(
        f"""
        await pick();
        pointer(pin(3), 'pointerdown', 840, 260, '{kind}');
        var menu = makeEvent('contextmenu', {{ clientX: 840, clientY: 260 }});
        svg().dispatchEvent(menu);
        out.prevented = menu.defaultPrevented;
        pointer(svg(), 'pointermove', 900, 260, '{kind}');
        out.during = spot(3);
        pointer(svg(), 'pointerup', 900, 260, '{kind}');
        await settle();
        out.saved = stored();
        """
    )
    assert out == {"prevented": True, "during": [900, 260], "saved": [[[0, 2], [2, 2], [2.6, 4], [0, 4]]]}


def test_a_context_menu_without_a_drag_is_left_alone() -> None:
    out = _camera(
        """
        await pick();
        var menu = makeEvent('contextmenu', { clientX: 840, clientY: 260 });
        svg().dispatchEvent(menu);
        out.prevented = menu.defaultPrevented;
        """
    )
    assert out == {"prevented": False}


# -- the details panel's writes keep to their zone too -------------------------------------

_DETAIL_ROUTES = """
var base2 = station;
station = function (req) {
    var m = /^\\/api\\/zones\\/(\\d+)\\/(duplicate|test_send)/.exec(req.url);
    if (m && m[2] === 'duplicate') {
        model.zones.push(JSON.parse(JSON.stringify(model.zones[+m[1]])));
        return { body: { success: true, index: model.zones.length - 1 } };
    }
    if (m) return { body: { sent: model.zones[+m[1]].name } };
    return base2(req);
};
"""

_DELETE_OTHER = """
tickIntervals(); await settle();
choose('camera'); await showPicture();
await chooseZone(0, 400, 400);
holdWhen = function (r) { return r.method === 'DELETE'; };
document.getElementById('zone-delete-btn').click();
await settle();
await chooseZone(1, 740, 360);
"""


@pytest.mark.parametrize(
    "act, then",
    [
        ("document.getElementById('zone-save-btn').click();", ["PUT /api/zones/0"]),
        (
            "document.getElementById('zone-save-btn').click(); await settle(); await chooseZone(2, 1040, 360);",
            ["PUT /api/zones/0"],
        ),
        ("document.getElementById('zone-duplicate-btn').click();", ["PUT /api/zones/0", "POST /api/zones/0/duplicate"]),
        (
            "document.querySelector('[data-zone-test=\"first\"]').click();",
            ["PUT /api/zones/0", "POST /api/zones/0/test_send?which=first"],
        ),
    ],
    ids=["save", "save then choose another", "duplicate", "test send"],
)
def test_a_details_write_after_a_delete_reaches_its_own_zone(act: str, then: list[str]) -> None:
    out = _camera(
        _ZONE_STEPS
        + _DELETE_OTHER
        + act
        + """
        await settle();
        out.held = writes();
        holdWhen = null;
        release(); await settle();
        out.writes = writes();
        out.names = model.zones.map(function (z) { return z.name; });
        var pre = document.getElementById('zone-diag-test-result');
        out.sent = pre && !pre.hidden ? pre.textContent : null;
        """,
        _THREE + _DETAIL_ROUTES,
    )
    assert out["held"] == ["DELETE /api/zones/0"]
    assert out["writes"] == ["DELETE /api/zones/0", *then]
    assert out["names"][:2] == ["Square", "Third"]
    if "test_send" in then[-1]:
        assert out["sent"] is not None and '"sent": "Square"' in out["sent"]


def test_a_details_save_for_a_zone_deleted_meanwhile_is_dropped() -> None:
    out = _camera(
        _ZONE_STEPS
        + """
        tickIntervals(); await settle();
        choose('camera'); await showPicture();
        await chooseZone(0, 400, 400);
        holdWhen = function (r) { return r.method === 'DELETE'; };
        document.getElementById('zone-delete-btn').click();
        await settle();
        document.getElementById('zone-save-btn').click();
        await settle();
        holdWhen = null;
        release(); await settle();
        out.writes = writes();
        """,
        _THREE,
    )
    assert out == {"writes": ["DELETE /api/zones/0"]}


def test_a_delete_landing_keeps_what_is_typed_in_another_zone() -> None:
    out = _camera(
        _ZONE_STEPS
        + _DELETE_OTHER
        + """
        var panel = document.querySelector('#zone-details .group');
        var name = document.querySelector('#zone-details [data-zone-field="name"]');
        typeInto(name, 'Lead vocal');
        holdWhen = null;
        release(); await settle();
        out.same = document.querySelector('#zone-details .group') === panel;
        out.heading = document.querySelector('#zone-details .group-title').textContent;
        out.typed = document.querySelector('#zone-details [data-zone-field="name"]').value;
        tickIntervals(); await settle();
        out.afterPoll = document.querySelector('#zone-details [data-zone-field="name"]').value;
        """,
        _THREE,
    )
    assert out == {"same": True, "heading": "Zone 1 Details", "typed": "Lead vocal", "afterPoll": "Lead vocal"}


def test_a_saved_zone_brings_the_list_up_to_date_at_once() -> None:
    out = _camera(
        """
        await pick();
        var gets = function () {
            return requests.filter(function (r) { return r.method === 'GET' && r.url === '/api/zones'; }).length;
        };
        var was = gets();
        // Another browser adds a zone meanwhile: the reload after the save shows it.
        model.zones.push({ name: 'Elsewhere', color: '#33aaff', vertices: [[-3, 2], [-2, 2], [-2, 3]] });
        document.getElementById('zone-save-btn').click();
        await settle();
        out.asked = gets() - was;
        out.lines = zoneLines().length;
        """
    )
    assert out == {"asked": 1, "lines": 2}


def test_a_test_send_with_no_answer_is_given_up() -> None:
    out = _camera(
        """
        await pick();
        holdWhen = function (r) { return r.url.indexOf('/test_send') > 0; };
        document.querySelector('[data-zone-test="first"]').click();
        await settle();
        runTimeouts(); await settle();
        out.shown = !document.getElementById('zone-diag-test-result').hidden;
        var line = document.querySelector('#zone-editor-section .save-error');
        out.error = line ? line.textContent : null;
        holdWhen = null;
        model.zones[0].name = 'Renamed';
        // Off the details, so the poll may refresh the chosen zone too.
        document.activeElement.blur();
        tickIntervals(); await settle();
        out.status = document.getElementById('zone-fs-status').textContent;
        """
    )
    assert out["shown"] is False
    assert out["error"] is not None and "The station did not answer." in out["error"]
    assert "abort" not in out["error"].lower()
    assert out["status"] == "Renamed · 4 corners"


def test_a_test_send_for_a_zone_deleted_meanwhile_says_so() -> None:
    out = _camera(
        _ZONE_STEPS
        + """
        tickIntervals(); await settle();
        choose('camera'); await showPicture();
        await chooseZone(0, 400, 400);
        holdWhen = function (r) { return r.method === 'DELETE'; };
        document.getElementById('zone-delete-btn').click();
        await settle();
        document.querySelector('[data-zone-test="first"]').click();
        await settle();
        holdWhen = null;
        release(); await settle();
        out.writes = writes();
        var pre = document.getElementById('zone-diag-test-result');
        out.shown = !!pre && !pre.hidden;
        out.error = errorLine();
        // The zone's details are gone: the line sits under the editor's buttons.
        var line = document.querySelector('#zone-editor-section .save-error');
        out.underButtons = line.previousSibling === document.getElementById('zone-delete-btn').parentNode;
        """,
        _THREE,
    )
    assert out["writes"] == ["DELETE /api/zones/0"]
    assert out["underButtons"] is True
    assert out["shown"] is False
    assert out["error"] is not None and "The zone was deleted before the test was sent." in out["error"]


# -- zone heights in the details panel ---------------------------------------------

_CHOOSE_THE_ZONE = """
at('mousedown', 725, 360); at('mouseup', 725, 360);
await settle();
function field(name) { return document.querySelector('#zone-details [data-zone-field="' + name + '"]'); }
"""


def test_the_zone_height_fields_sit_in_a_row_gated_on_their_feature() -> None:
    out = _editor(
        _CHOOSE_THE_ZONE
        + """
        var row = document.querySelector('#zone-details [data-feature="zone_heights"]');
        out.labels = Array.prototype.map.call(row.querySelectorAll('label'), function (l) { return l.textContent; });
        out.inRow = ['z_offset', 'raise_markers', 'height_transition_s']
            .every(function (n) { return row.contains(field(n)); });
        out.values = [field('z_offset').value, field('height_transition_s').value,
                      field('raise_markers').hasAttribute('checked')];
        """
    )
    assert out["labels"] == [
        "Z offset (m)",
        "Marker height",
        "Add the Z offset inside this zone",
        "Height transition (s)",
    ]
    assert out["inRow"] is True
    assert out["values"] == ["0.000", "0.3", False]


def test_saving_a_zone_sends_its_heights() -> None:
    out = _editor(
        _CHOOSE_THE_ZONE
        + """
        field('z_offset').value = '0.45';
        field('height_transition_s').value = '1.2';
        field('raise_markers').click();
        button('zone-save-btn');
        await settle();
        var body = puts()[puts().length - 1].body;
        out.sent = [body.z_offset, body.raise_markers, body.height_transition_s];
        """,
        setup="model.zones[0].z_offset = 0.2;",
    )
    assert out["sent"] == ["0.45", True, "1.2"]


def test_a_zone_height_is_checked_when_it_loses_focus() -> None:
    out = _editor(
        _CHOOSE_THE_ZONE
        + """
        field('z_offset').value = '99';
        field('z_offset').dispatchEvent(makeEvent('blur'));
        field('height_transition_s').value = '-1';
        field('height_transition_s').dispatchEvent(makeEvent('blur'));
        await settle();
        out.asked = requests.filter(function (r) { return r.url.indexOf('/api/validate/zone/') === 0; })
            .map(function (r) { return r.url; });
        """
    )
    assert out["asked"] == [
        "/api/validate/zone/z_offset?z_offset=99",
        "/api/validate/zone/height_transition_s?height_transition_s=-1",
    ]


def test_an_imperial_station_types_the_z_offset_in_feet_and_sends_metres() -> None:
    out = _editor(
        _CHOOSE_THE_ZONE
        + """
        out.label = field('z_offset').parentNode.querySelector('label').textContent;
        out.shown = field('z_offset').value;
        field('z_offset').value = '2 ft';
        button('zone-save-btn');
        await settle();
        out.sent = puts()[puts().length - 1].body.z_offset;
        """,
        setup="window.OpenFollow.units.setSystem('imperial'); model.zones[0].z_offset = 0.3048;",
    )
    assert out["label"] == "Z offset (ft / in)"
    assert out["shown"] == "1 ft 0.00 in"
    assert out["sent"] == pytest.approx(0.6096)


def test_an_imperial_length_the_parser_refuses_is_sent_as_typed() -> None:
    out = _editor(
        _CHOOSE_THE_ZONE
        + """
        field('z_offset').value = 'high';
        button('zone-save-btn');
        await settle();
        out.sent = puts()[puts().length - 1].body.z_offset;
        """,
        setup="window.OpenFollow.units.setSystem('imperial');",
    )
    assert out["sent"] == "high"


@pytest.mark.parametrize("imperial", [False, True], ids=["metric", "imperial"])
def test_an_unrelated_edit_leaves_the_zone_heights_as_stored(imperial: bool) -> None:
    out = _editor(
        _CHOOSE_THE_ZONE
        + """
        field('name').value = 'Riser';
        button('zone-save-btn');
        await settle();
        var body = puts()[puts().length - 1].body;
        out.sentHeights = 'z_offset' in body || 'height_transition_s' in body;
        out.stored = [model.zones[0].z_offset, model.zones[0].height_transition_s, model.zones[0].name];
        """,
        setup=("window.OpenFollow.units.setSystem('imperial');" if imperial else "")
        + "model.zones[0].z_offset = 0.123456789; model.zones[0].height_transition_s = 0.25;",
    )
    assert out["sentHeights"] is False
    assert out["stored"] == [0.123456789, 0.25, "Riser"]


@pytest.mark.parametrize(
    ("imperial", "typed", "back"),
    [(False, "1", "0.300"), (True, "3 ft", "11.81 in")],
    ids=["metric", "imperial"],
)
def test_a_height_saved_then_typed_back_is_saved_again(imperial: bool, typed: str, back: str) -> None:
    out = _editor(
        _CHOOSE_THE_ZONE
        + f"""
        field('z_offset').value = '{typed}';
        button('zone-save-btn');
        await settle();
        field('z_offset').value = '{back}';
        button('zone-save-btn');
        await settle();
        out.sent = puts().map(function (r) {{ return r.body.z_offset; }});
        """,
        setup=("window.OpenFollow.units.setSystem('imperial');" if imperial else "") + "model.zones[0].z_offset = 0.3;",
    )
    assert len(out["sent"]) == 2
    assert out["sent"][1] == (pytest.approx(0.3, abs=1e-3) if imperial else back)
