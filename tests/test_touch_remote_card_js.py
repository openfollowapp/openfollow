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
    // Timeouts (a drive's give-up, the notch flash) fire only when a test says so.
    timeouts: [],
    setTimeout: function (f) { window.timeouts.push(f); return window.timeouts.length; },
    clearTimeout: function () {},
    listeners: {},
    addEventListener: function (t, f) { (window.listeners[t] = window.listeners[t] || []).push(f); },
    AbortController: function () {
        var self = this;
        this.signal = { aborted: false };
        this.abort = function () { self.signal.aborted = true; aborts.push(true); };
    },
};
var aborts = [];
var navigator = {};
var requests = [];
function fetch(url, opts) {
    return new Promise(function (resolve, reject) {
        var body = opts && opts.body ? JSON.parse(opts.body) : null;
        requests.push({ url: url, body: body, opts: opts || {}, resolve: resolve, reject: reject });
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
              z_step_m: 0.05, z_notch_px: 20, paths_on: false, recording: false, locked: [], inverted: false };
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


def test_two_requests_learning_the_session_ended_open_one_new_session_and_take_nothing() -> None:
    out = _run(
        _OPENED
        + """
els['remote-chips'].children[0].fire('click');
answer(2, 200, { success: true, detection_off: false }); await settle();
tick(); await settle();
var drive = paths().indexOf('/api/remote/drive'), state = paths().lastIndexOf('/api/remote/state');
answer(drive, 404, {}); answer(state, 404, {}); await settle();
out.opens = paths().filter(function (p) { return p === '/api/remote/open'; }).length;
var before = requests.length;
answer(requests.length - 1, 200, { token: 't2' }); await settle();
out.after = paths().slice(before);
out.heldHidden = els['remote-held'].hidden;
"""
    )
    assert out["opens"] == 2
    # The marker may be another tablet's by now: taking it again is a chip tap.
    assert "/api/remote/hold" not in out["after"]
    assert out["heldHidden"] is True


def test_a_failed_open_is_retried_by_the_next_state_tick() -> None:
    out = _run(
        """
els['remote-launcher'].fire('click');
requests[0].reject(new Error('offline')); await settle();
out.offline = els['remote-offline'].hidden;
tick(); await settle();
out.paths = paths();
"""
    )
    assert out["offline"] is False
    assert out["paths"] == ["/api/remote/open", "/api/remote/open"]


def test_the_station_s_hold_wins_over_the_card_s() -> None:
    out = _run(
        _OPENED
        + """
tick(); await settle();
var state = paths().lastIndexOf('/api/remote/state');
answer(state, 200, Object.assign({}, STATE, { held: 1 })); await settle();
out.held = els['remote-held'].textContent;
"""
    )
    assert out["held"] == "Remote has 1 · Lead"


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


_HOLDING = (
    _OPENED
    + """
els['remote-chips'].children[0].fire('click');
answer(2, 200, { success: true, detection_off: false }); await settle();
"""
)


def test_a_hung_drive_is_given_up_so_the_next_heartbeat_goes() -> None:
    out = _run(
        _HOLDING
        + """
tick(); await settle();
var first = paths().indexOf('/api/remote/drive');
tick(); await settle();
out.whileHung = paths().filter(function (p) { return p === '/api/remote/drive'; }).length;
window.timeouts.forEach(function (f) { f(); });
requests[first].reject(new Error('aborted')); await settle();
tick(); await settle();
out.after = paths().filter(function (p) { return p === '/api/remote/drive'; }).length;
out.aborted = aborts.length;
"""
    )
    assert out["whileHung"] == 1
    assert out["aborted"] == 1
    assert out["after"] == 2


def test_leaving_the_page_closes_the_session_with_keepalive() -> None:
    out = _run(
        _HOLDING
        + """
window.listeners.pagehide.forEach(function (f) { f(); });
var close = requests[requests.length - 1];
out.path = close.url;
out.keepalive = close.opts.keepalive;
out.cardHidden = els['remote-card'].hidden;
"""
    )
    assert out["path"] == "/api/remote/close"
    assert out["keepalive"] is True
    assert out["cardHidden"] is True


def test_a_state_poll_sent_before_a_chip_tap_does_not_undo_it() -> None:
    out = _run(
        _OPENED
        + """
tick(); await settle();
var poll = paths().lastIndexOf('/api/remote/state');
els['remote-chips'].children[0].fire('click');
answer(requests.length - 1, 200, { success: true, detection_off: false }); await settle();
answer(poll, 200, STATE); await settle();
out.heldHidden = els['remote-held'].hidden;
"""
    )
    assert out["heldHidden"] is False


def test_an_answer_from_before_a_close_and_reopen_changes_nothing() -> None:
    out = _run(
        _HOLDING
        + """
tick(); await settle();
var drive = paths().indexOf('/api/remote/drive');
els['remote-close'].fire('click');
els['remote-launcher'].fire('click');
var before = requests.length;
answer(drive, 404, {}); await settle();
out.opens = paths().slice(before).filter(function (p) { return p === '/api/remote/open'; }).length;
"""
    )
    assert out["opens"] == 0


def test_the_drum_ticks_follow_the_notch_spacing() -> None:
    out = _run(
        """
els['remote-launcher'].fire('click');
answer(0, 200, { token: 't1' }); await settle();
answer(1, 200, Object.assign({}, STATE, { z_notch_px: 40 })); await settle();
out.size = els['remote-ticks'].style.backgroundSize;
"""
    )
    assert out["size"] == "100% 40px"


def test_notches_in_a_failed_drive_are_not_sent_again() -> None:
    out = _run(
        _HOLDING
        + """
var drum = els['remote-drum'];
drum.fire('pointerdown', { pointerId: 2, clientY: 100 });
drum.fire('pointermove', { pointerId: 2, clientY: 60 });
tick(); await settle();
var first = paths().indexOf('/api/remote/drive');
out.sent = requests[first].body.z_notches;
requests[first].reject(new Error('aborted')); await settle();
tick(); await settle();
out.resent = requests[paths().lastIndexOf('/api/remote/drive')].body.z_notches;
"""
    )
    assert out["sent"] == 2
    assert out["resent"] == 0


def test_a_later_poll_clears_a_stale_taken() -> None:
    out = _run(
        _HOLDING
        + """
tick(); await settle();
answer(paths().lastIndexOf('/api/remote/state'), 200, Object.assign({}, STATE, { lost: true })); await settle();
out.lostShown = !els['remote-lost'].hidden;
tick(); await settle();
answer(paths().lastIndexOf('/api/remote/state'), 200, Object.assign({}, STATE, { held: 1 })); await settle();
out.lostAfter = !els['remote-lost'].hidden;
out.heldAfter = !els['remote-held'].hidden;
"""
    )
    assert out == {"lostShown": True, "lostAfter": False, "heldAfter": True}


@pytest.mark.parametrize(
    ("inverted", "flip", "up"),
    [(False, False, "UPSTAGE"), (True, False, "DOWNSTAGE"), (True, True, "UPSTAGE"), (False, True, "DOWNSTAGE")],
)
def test_the_stick_labels_say_where_it_drives(inverted: bool, flip: bool, up: str) -> None:
    out = _run(
        f"""
els['remote-launcher'].fire('click');
answer(0, 200, {{ token: 't1' }}); await settle();
if ({str(flip).lower()}) els['remote-flip'].fire('click');
answer(1, 200, Object.assign({{}}, STATE, {{ inverted: {str(inverted).lower()} }})); await settle();
out.up = els['remote-up'].textContent;
"""
    )
    assert out["up"] == up


def test_the_xyz_fields_follow_the_marker_until_typed_in() -> None:
    out = _run(
        _HOLDING
        + """
els['remote-xyz'].fire('click');
tick(); await settle();
var moved = Object.assign({}, STATE, { held: 1, markers: [Object.assign({}, STATE.markers[0], { pos: [4, 5, 1.6] })] });
answer(paths().lastIndexOf('/api/remote/state'), 200, moved); await settle();
out.x = els['remote-x'].value;
els['remote-z-in'].value = '1.8';
els['remote-z-in'].fire('input');
tick(); await settle();
var again = Object.assign({}, moved, { markers: [Object.assign({}, STATE.markers[0], { pos: [4.5, 5, 1.6] })] });
answer(paths().lastIndexOf('/api/remote/state'), 200, again); await settle();
out.x2 = els['remote-x'].value;
out.z = els['remote-z-in'].value;
"""
    )
    assert out == {"x": "4.000 m", "x2": "4.500 m", "z": "1.8"}


def test_a_lost_marker_asks_the_station_once_and_stops_driving_meanwhile() -> None:
    out = _run(
        _HOLDING
        + """
document.hidden = true;
els['remote-stick'].fire('pointerdown', { pointerId: 1, clientX: 140, clientY: 92 });
document.hidden = false;
tick(); await settle();
answer(paths().lastIndexOf('/api/remote/drive'), 409, {}); await settle();
var before = requests.length;
tick(); await settle(); tick(); await settle();
out.drives = paths().slice(before).filter(function (p) { return p === '/api/remote/drive'; }).length;
"""
    )
    assert out["drives"] == 0
