# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The Measure Camera Latency dialog's script in node on the fake DOM: closing it takes the warnings'
renewal with it, and warnings that arrive after it closed never renew the Operator Screen's card."""

from __future__ import annotations

import json
import shutil
import subprocess  # nosec B404 - runs the local node binary on a fixed script
from pathlib import Path
from typing import Any

import pytest
from bottle import SimpleTemplate

pytestmark = [pytest.mark.unit, pytest.mark.skipif(shutil.which("node") is None, reason="needs node")]

_TESTS = Path(__file__).parent
_TEMPLATES = _TESTS.parent / "openfollow" / "web" / "templates"
_TOKEN = "Abc_DEF-123456789"

# Stand-ins for base.tpl's modal and htmx: one modal, the GET the dialog asks for held until answered.
_PAGE = """
parseHTML('<div id="modal-root" hidden><div id="modal-body"></div><div id="modal-footer"></div></div>'
    + '<div id="video-source-latency"><button>Measure latency</button></div>', document.body);
var onClose = null, locked = null, beacons = [], asked = [];
function openModal(opts) {
    var root = document.getElementById('modal-root');
    document.getElementById('modal-body').innerHTML = opts.bodyHTML;
    onClose = opts.onClose;
    root.hidden = false;
}
function closeModal() {
    document.getElementById('modal-root').hidden = true;
    var handler = onClose; onClose = null;
    if (handler) handler();
}
function modalLock(on) { locked = on; }
navigator.sendBeacon = function (url) { beacons.push(url); return true; };
var htmx = { ajax: function (method, url, opts) {
    asked.push({method: method, url: url, target: opts.target === '#latency-dialog'
        ? document.getElementById('latency-dialog') : opts.target});
    return Promise.resolve();
} };
// What htmx does with the dialog's answer: in place of the element asked for, the footer out of band, scripts run.
function answer(target, html) {
    var holder = document.createElement('div');
    parseHTML(html, holder);
    var dialog = holder.querySelector('#latency-dialog');
    if (target.parentNode) { target.after(dialog); target.remove(); }
    var footer = holder.querySelector('#modal-footer'), shown = document.getElementById('modal-footer');
    if (footer && shown) { shown.after(footer); shown.remove(); }
    var script = holder.querySelector('script');
    (0, eval)(script.textContent);
    return dialog;
}
"""


def _dialog_script() -> str:
    text = (_TEMPLATES / "partials" / "video_source.tpl").read_text(encoding="utf-8")
    start = text.index("// Measure latency: the station renders")
    return text[start : text.index("</script>", start)]


def _render(state: str, warning: str) -> str:
    source = (_TEMPLATES / "partials" / "video_source_latency_dialog.tpl").read_text(encoding="utf-8")
    result = {"median_ms": 64.0, "min_ms": 37.0, "max_ms": 79.0, "samples": 10, "level": "success"}
    dialog = {
        "state": state,
        "measured": 10,
        "total": 10,
        "result": result if state == "done" else None,
        "result_class": "success",
        "failure": None,
    }
    return str(SimpleTemplate(source, lookup=[str(_TEMPLATES)]).render(dialog=dialog, warning=warning))


def _run(steps: str) -> Any:
    source = "\n".join(
        [
            (_TESTS / "_fake_dom.js").read_text(encoding="utf-8"),
            _PAGE,
            _dialog_script(),
            f"var INTRO = {json.dumps(_render('intro', _TOKEN))};",
            f"var RESULT = {json.dumps(_render('done', ''))};",
            "(async function () { var out = {};",
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


def test_closing_the_dialog_takes_its_warnings_renewal_with_it_and_takes_the_card_down() -> None:
    out = _run(
        "ofLatencyDialog(); answer(asked[0].target, INTRO);"
        "var renewal = document.getElementById('latency-warning-renewal');"
        "out.live = ofLatencyWarningLive(renewal);"
        "closeModal(); await settle();"
        "out.gone = document.getElementById('latency-warning-renewal') === null;"
        "out.renews = ofLatencyWarningLive(renewal);"
        "out.beacons = beacons; out.row = asked[1].url;"
    )
    assert out == {
        "live": True,
        "gone": True,
        "renews": False,
        "beacons": [f"/section/video_source/latency/warning/close?token={_TOKEN}"],
        "row": "/section/video_source/latency",
    }


@pytest.mark.parametrize("reopened", [False, True], ids=["closed", "another-open"])
def test_warnings_that_arrive_after_the_dialog_closed_never_renew_the_card(reopened: bool) -> None:
    out = _run(
        # Escape before the station answered: the warnings land in a dialog that is gone,
        # while the modal stays shut or shows a dialog opened since.
        "ofLatencyDialog(); var asked_for = asked[0].target; closeModal(); await settle();"
        + ("ofLatencyDialog();" if reopened else "")
        + "var dialog = answer(asked_for, INTRO);"
        "out.renews = ofLatencyWarningLive(dialog.querySelector('#latency-warning-renewal'));"
        "out.token = window.ofLatencyWarning || ''; out.locked = locked;"
        "closeModal(); out.beacons = beacons;"
    )
    assert out == {"renews": False, "token": "", "locked": None, "beacons": []}


def test_the_warnings_focus_cancel_so_one_enter_never_starts_a_run() -> None:
    out = _run(
        "ofLatencyDialog(); answer(asked[0].target, INTRO);"
        "out.focus = document.activeElement.textContent; out.locked = locked;"
    )
    assert out == {"focus": "Cancel", "locked": False}


def test_a_result_focuses_close_so_one_enter_never_starts_another_run() -> None:
    out = _run(
        "ofLatencyDialog(); answer(asked[0].target, RESULT);"
        "out.focus = document.activeElement.textContent.trim(); out.locked = locked;"
    )
    assert out == {"focus": "Close", "locked": False}
