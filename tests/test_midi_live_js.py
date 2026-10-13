# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Show live in the MIDI section, run in node: on for ten seconds, off on its own or on a second click."""

from __future__ import annotations

import json
import shutil
import subprocess  # nosec B404 - runs the local node binary on a fixed script
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.skipif(shutil.which("node") is None, reason="needs node")]

_TEMPLATE = Path(__file__).parents[1] / "openfollow" / "web" / "templates" / "partials" / "midi.tpl"

# A page with two Show live buttons, a clock and timers the test moves by hand.
_HARNESS = """
var now = 1000, timers = [], triggered = [];
var buttons;
// What the server renders: two fresh Show live buttons, on the first load and on every re-render.
function render() { buttons = [0, 1].map(function () { return {textContent: 'Show live'}; }); }
var window = {};
var document = {body: 'body', querySelectorAll: function (sel) { return sel === '[data-midi-live]' ? buttons : []; }};
var htmx = {trigger: function (target, name) { triggered.push(name); }};
Date.now = function () { return now; };
function setTimeout(fn, ms) { timers.push({at: now + ms, fn: fn}); return timers.length; }
function clearTimeout(id) { if (id) { timers[id - 1].fn = null; } }
function advance(ms) {
    now += ms;
    timers.forEach(function (t) { if (t.fn && t.at <= now) { var fn = t.fn; t.fn = null; fn(); } });
}
function view() {
    return {active: window.midiLiveActive(), labels: buttons.map(function (b) { return b.textContent; }),
            triggered: triggered.slice()};
}
"""


def _script() -> str:
    text = _TEMPLATE.read_text(encoding="utf-8")
    start = text.index("<script>") + len("<script>")
    return text[start : text.index("</script>", start)]


def _run(steps: str) -> object:
    source = _HARNESS + f"var load = function () {{ render(); {_script()} }};\nload();\nvar out = [];\n{steps}\n"
    source += "process.stdout.write(JSON.stringify(out));"
    node = shutil.which("node")
    assert node is not None
    done = subprocess.run([node, "-e", source], capture_output=True, text=True, check=True, timeout=10)  # nosec B603
    return json.loads(done.stdout)


def test_show_live_polls_at_once_and_marks_both_buttons() -> None:
    before, after = _run("out.push(view()); window.midiToggleLive(); out.push(view());")
    assert before == {"active": False, "labels": ["Show live"] * 2, "triggered": []}
    assert after == {"active": True, "labels": ["Stop live"] * 2, "triggered": ["midi-live"]}


def test_it_ends_on_its_own_after_ten_seconds() -> None:
    almost, ended = _run("window.midiToggleLive(); advance(9999); out.push(view()); advance(1); out.push(view());")
    assert almost["active"] is True
    assert ended["active"] is False
    assert ended["labels"] == ["Show live"] * 2


def test_a_second_click_stops_it_and_its_timer() -> None:
    stopped, later = _run(
        "window.midiToggleLive(); advance(2000); window.midiToggleLive(); out.push(view());"
        "window.midiToggleLive(); advance(9000); out.push(view());"
    )
    assert stopped["active"] is False and stopped["labels"] == ["Show live"] * 2
    # The first timer was cleared: it must not end the second live view early.
    assert later["active"] is True


def test_a_re_render_of_the_section_keeps_a_live_view_going() -> None:
    (after,) = _run("window.midiToggleLive(); advance(3000); load(); out.push(view());")
    assert after["active"] is True
    assert after["labels"] == ["Stop live"] * 2
