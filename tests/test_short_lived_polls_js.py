# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Script polls the operator started, run in node on the fake DOM: they ask nothing while the
browser tab is hidden, keep their timer, and ask again on the first tick once it is shown."""

from __future__ import annotations

import json
import shutil
import subprocess  # nosec B404 - runs the local node binary on a fixed script
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.skipif(shutil.which("node") is None, reason="needs node")]

_TESTS = Path(__file__).parent
_WEB = _TESTS.parent / "openfollow" / "web"

# Runs every timer the script set so far, its own fetch timeouts included.
_TICK = """
async function tick() { var due = timeouts; timeouts = []; due.forEach(function (fn) { fn(); }); await settle(); }
function asked(url) { return requests.filter(function (r) { return r.url === url; }).length; }
"""


def _run(*parts: str) -> Any:
    source = "\n".join(
        [
            (_TESTS / "_fake_dom.js").read_text(encoding="utf-8"),
            _TICK,
            *parts[:-1],
            "(async function () { var out = {}; await settle();",
            parts[-1],
            "process.stdout.write(JSON.stringify(out)); })()"
            ".catch(function (e) { console.error(e && e.stack || e); process.exit(1); });",
        ]
    )
    node = shutil.which("node")
    assert node is not None
    done = subprocess.run([node, "-e", source], capture_output=True, text=True, timeout=20)  # nosec B603
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


# -- Save to USB storage device -------------------------------------------------------------

# The modal helpers stand in for base.tpl's: one dialog at a time, its footer buttons in the page.
_MODAL = """
var modal = null;
function escapeHTML(v) { return String(v); }
function closeModal() { var was = modal; modal = null; if (was) { was.root.remove(); if (was.onClose) was.onClose(); } }
function openModal(opts) {
    if (modal) closeModal();
    var root = document.body.appendChild(document.createElement('div'));
    root.setAttribute('id', 'modal-root');
    parseHTML('<div class="modal-card">' + opts.bodyHTML + '<div id="modal-footer"></div></div>', root);
    (opts.footerButtons || []).forEach(function (b) {
        var btn = document.getElementById('modal-footer').appendChild(document.createElement('button'));
        if (b.kind) btn.className = b.kind;
        btn.textContent = b.label;
    });
    modal = { root: root, onClose: opts.onClose };
}
window.OpenFollow = { saveError: { origin: function () { return null; }, show: function () {} } };
station = function (req) {
    if (req.url === '/api/diagnostics/drives') {
        return { body: { media: [{ id: 'sda1', label: 'STICK', writable: true }] } };
    }
    return { body: { generation: 3, phase: 'collecting', headline: 'Collecting', next_step: '' } };
};
"""
_DRIVES = "/api/diagnostics/drives"
_EXPORT = "/api/diagnostics/export?generation=3"


def _drive_script() -> str:
    text = (_WEB / "templates" / "base.tpl").read_text(encoding="utf-8")
    start = text.index("// Save to USB storage device")
    return text[start : text.index("function switchTab(tabId)", start)]


def _drive(steps: str) -> Any:
    return _run(_MODAL, _drive_script(), steps)


def test_the_picker_lists_the_devices_at_once_and_on_every_tick() -> None:
    out = _drive(
        f"_driveExportPick(); await settle(); out.a = asked('{_DRIVES}'); await tick(); out.b = asked('{_DRIVES}');"
    )
    assert (out["a"], out["b"]) == (1, 2)


def test_the_picker_asks_nothing_while_the_tab_is_hidden_and_again_once_it_is_shown() -> None:
    steps = (
        "_driveExportPick(); await settle(); document.hidden = true; await tick(); await tick();"
        f"out.hidden = asked('{_DRIVES}');"
        f"document.hidden = false; await tick(); out.shown = asked('{_DRIVES}');"
    )
    assert _drive(steps) == {"hidden": 1, "shown": 2}


def test_the_export_progress_asks_nothing_while_the_tab_is_hidden_and_again_once_it_is_shown() -> None:
    steps = (
        "_driveExportProgress({generation: 3, phase: 'collecting', headline: 'Collecting', next_step: ''});"
        f"await tick(); out.open = asked('{_EXPORT}');"
        f"document.hidden = true; await tick(); await tick(); out.hidden = asked('{_EXPORT}');"
        f"document.hidden = false; await tick(); out.shown = asked('{_EXPORT}');"
    )
    assert _drive(steps) == {"open": 1, "hidden": 1, "shown": 2}


# -- Detect button (detect-input.js) --------------------------------------------------------

_DETECT_MARKUP = (
    '<div class="detect-input"><input id="field">'
    '<button type="button" class="detect-btn" data-detect-input="field" data-detect-url="/detect">Detect</button></div>'
)
_DETECT_PAGE = f"""
var now = 0;
Date.now = function () {{ return now; }};
parseHTML({json.dumps(_DETECT_MARKUP)}, document.body);
station = function () {{ return {{ body: {{ value: null }} }}; }};
var btn = document.querySelector('.detect-btn');
"""


def _detect(steps: str) -> Any:
    return _run(_DETECT_PAGE, (_WEB / "static" / "js" / "detect-input.js").read_text(encoding="utf-8"), steps)


def test_detect_asks_until_a_press_arrives() -> None:
    out = _detect("btn.click(); await settle(); out.a = asked('/detect'); await tick(); out.b = asked('/detect');")
    assert (out["a"], out["b"]) == (1, 2)


def test_detect_asks_nothing_while_the_tab_is_hidden_and_again_once_it_is_shown() -> None:
    steps = (
        "btn.click(); await settle(); document.hidden = true; await tick(); await tick();"
        "out.hidden = asked('/detect');"
        "out.listening = btn.disabled; document.hidden = false; await tick(); out.shown = asked('/detect');"
    )
    assert _detect(steps) == {"hidden": 1, "listening": True, "shown": 2}


def test_detect_hidden_past_its_window_stops_listening() -> None:
    steps = (
        "btn.click(); await settle(); document.hidden = true; now = 9000; await tick();"
        "out.listening = btn.disabled; out.label = btn.textContent; out.asked = asked('/detect');"
        "await tick(); out.after = asked('/detect');"
    )
    assert _detect(steps) == {"listening": False, "label": "Detect", "asked": 1, "after": 1}
