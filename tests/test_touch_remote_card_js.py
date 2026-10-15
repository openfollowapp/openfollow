# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The touch remote card (static/js/touch-remote.js) in node, on a fake page and a scripted station."""

from __future__ import annotations

import json
import shutil
import subprocess  # nosec B404 - runs the local node binary on a fixed script
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.skipif(shutil.which("node") is None, reason="needs node")]

_MODULE = Path(__file__).parents[1] / "openfollow" / "web" / "static" / "js" / "touch-remote.js"

# Every element the card names, a fetch that queues each request until the test answers it,
# and timers the test runs by hand.
_HARNESS = """
function El(id) {
    this.id = id; this.hidden = id === 'remote-card'; this.textContent = ''; this.value = '';
    this.dataset = {}; this.children = []; this.style = {}; this.attrs = {}; this.listeners = {};
    this.offsetWidth = 72;
    var self = this;
    this.classList = {
        set: {},
        add: function (c) { this.set[c] = true; }, remove: function (c) { delete this.set[c]; },
        toggle: function (c, on) { if (on) this.set[c] = true; else delete this.set[c]; },
    };
    this.setAttribute = function (k, v) { self.attrs[k] = v; };
    this.addEventListener = function (t, f) { (self.listeners[t] = self.listeners[t] || []).push(f); };
    this.appendChild = function (c) { self.children.push(c); };
    this.setPointerCapture = function () {};
    this.getBoundingClientRect = function () { return { left: 0, top: 0, width: 184, height: 184 }; };
    this.fire = function (t, e) { (self.listeners[t] || []).forEach(function (f) { f(e || {}); }); };
}
Object.defineProperty(El.prototype, 'textContent', {
    get: function () { return this._text || ''; },
    set: function (v) { this._text = v; if (v === '') this.children = []; },
});
var els = {};
var document = {
    hidden: false,
    body: new El('body'),
    activeElement: null,
    listeners: {},
    getElementById: function (id) { return els[id] || (els[id] = new El(id)); },
    createElement: function (tag) { return new El(tag); },
    createTextNode: function (t) { return { text: t }; },
    addEventListener: function (t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); },
};
var timers = [];
var window = {
    localStorage: { getItem: function () { return null; }, setItem: function () {} },
    setInterval: function (f) { timers.push(f); return timers.length; },
    clearInterval: function () {},
    setTimeout: function (f) { f(); },
};
var navigator = {};
var requests = [];
function fetch(url, opts) {
    return new Promise(function (resolve, reject) {
        var body = opts && opts.body ? JSON.parse(opts.body) : null;
        requests.push({ url: url, body: body, resolve: resolve, reject: reject });
    });
}
function answer(index, status, data) {
    var r = requests[index];
    var ok = status >= 200 && status < 300;
    r.resolve({ status: status, ok: ok, json: function () { return Promise.resolve(data); } });
}
function paths() { return requests.map(function (r) { return r.url.replace(/\\?.*/, ''); }); }
function settle() { return new Promise(function (r) { setTimeout(r, 0); }); }
function tick() { timers.forEach(function (f) { f(); }); }
var STATE = { held: null, lost: false, markers: [{ id: 1, name: 'Lead', color: '#ffbc00', pos: [0, 0, 1.6] }],
              max_speed: 3, z_step_m: 0.05, z_notch_px: 20, unit_system: 'metric', paths_on: false,
              recording: false, locked: [], detection_on: false };
"""


def _run(body: str) -> Any:
    source = "\n".join(
        [
            _HARNESS,
            _MODULE.read_text(encoding="utf-8"),
            "var out = {};",
            "(async function () {",
            body,
            "process.stdout.write(JSON.stringify(out));",
            "})().catch(function (e) { console.error(e && e.stack || e); process.exit(1); });",
        ]
    )
    node = shutil.which("node")
    assert node is not None
    done = subprocess.run([node, "-e", source], capture_output=True, text=True, timeout=10)  # nosec B603
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


_OPENED = """
els['remote-launcher'].fire('click');
answer(0, 200, { token: 't1' }); await settle();
answer(1, 200, STATE); await settle();
"""


def test_opening_the_card_takes_no_marker() -> None:
    out = _run(_OPENED + "out.paths = paths(); out.chips = els['remote-chips'].children.length;")
    assert out["paths"] == ["/api/remote/open", "/api/remote/state"]
    assert out["chips"] == 1


def test_tapping_a_chip_takes_its_marker() -> None:
    out = _run(
        _OPENED
        + """
document.getElementById('remote-detection-off').hidden = true;
els['remote-chips'].children[0].fire('click');
out.hold = requests[2].body;
answer(2, 200, { success: true, detection_off: true }); await settle();
out.held = els['remote-held'].hidden;
out.detection = els['remote-detection-off'].hidden;
"""
    )
    assert out["hold"] == {"token": "t1", "marker": 1}
    assert out["held"] is False
    assert out["detection"] is False


def test_a_card_closed_before_the_station_answered_takes_nothing() -> None:
    out = _run(
        """
els['remote-launcher'].fire('click');
els['remote-launcher'].fire('click');
answer(0, 200, { token: 'late' }); await settle();
tick(); await settle();
out.paths = paths();
"""
    )
    assert out["paths"] == ["/api/remote/open"]


def test_two_requests_learning_the_session_ended_open_one_new_session() -> None:
    out = _run(
        _OPENED
        + """
els['remote-chips'].children[0].fire('click');
answer(2, 200, { success: true, detection_off: false }); await settle();
tick(); await settle();
var drive = paths().indexOf('/api/remote/drive'), state = paths().lastIndexOf('/api/remote/state');
answer(drive, 404, {}); answer(state, 404, {}); await settle();
out.opens = paths().filter(function (p) { return p === '/api/remote/open'; }).length;
answer(requests.length - 1, 200, { token: 't2' }); await settle();
out.rehold = requests[requests.length - 1].body;
"""
    )
    assert out["opens"] == 2
    assert out["rehold"] == {"token": "t2", "marker": 1}


@pytest.mark.parametrize(
    ("lost", "taken", "held"),
    [
        (True, False, True),  # another remote took it
        (False, True, True),  # the station let go: no claim that another remote has it
    ],
)
def test_a_409_asks_the_station_what_happened(lost: bool, taken: bool, held: bool) -> None:
    out = _run(
        _OPENED
        + f"""
els['remote-chips'].children[0].fire('click');
answer(2, 200, {{ success: true, detection_off: false }}); await settle();
document.hidden = true;
tick(); await settle();
document.hidden = false;
els['remote-stick'].fire('pointerdown', {{ pointerId: 1, clientX: 140, clientY: 92 }});
tick(); await settle();
var drive = paths().indexOf('/api/remote/drive');
answer(drive, 409, {{}}); await settle();
var after = Object.assign({{}}, STATE, {{ held: null, lost: {str(lost).lower()} }});
answer(requests.length - 1, 200, after); await settle();
out.lostHidden = els['remote-lost'].hidden;
out.heldHidden = els['remote-held'].hidden;
"""
    )
    assert out["lostHidden"] is taken
    assert out["heldHidden"] is held
