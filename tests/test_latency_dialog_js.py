# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Michel Honold and Paul Hermann
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
_OTHER = "Xyz_GHI-987654321"

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
var htmx = {
    ajax: function (method, url, opts) {
        asked.push({method: method, url: url, target: opts.target === '#latency-dialog'
            ? document.getElementById('latency-dialog') : opts.target});
        return Promise.resolve();
    },
    process: function () {},
};
// What htmx does with the dialog's answer: in place of the element asked for, scripts run. The worst case:
// the script runs even when the element it was asked for has left the page.
function answer(target, html) {
    var holder = document.createElement('div');
    parseHTML(html, holder);
    var dialog = holder.querySelector('#latency-dialog');
    if (target.parentNode) { target.after(dialog); target.remove(); }
    var script = holder.querySelector('script');
    (0, eval)(script.textContent);
    return dialog;
}
function footer() {
    return document.getElementById('modal-footer').children.map(function (b) { return b.textContent.trim(); });
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
            f"var INTRO_OTHER = {json.dumps(_render('intro', _OTHER))};",
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


@pytest.mark.parametrize("then", ["nothing", "another-modal", "another-latency-dialog"])
def test_warnings_that_arrive_after_the_dialog_closed_change_nothing(then: str) -> None:
    opened = {
        "nothing": "",
        "another-modal": "openModal({bodyHTML: '<p>Other</p>'});"
        "document.getElementById('modal-footer').innerHTML = '<button>Other</button>';",
        "another-latency-dialog": "ofLatencyDialog();",
    }[then]
    out = _run(
        # Escape before the station answered: the warnings land in a dialog that is gone.
        "ofLatencyDialog(); var asked_for = asked[0].target; closeModal(); await settle();"
        + opened
        + "var dialog = answer(asked_for, INTRO);"
        "out.renews = ofLatencyWarningLive(dialog.querySelector('#latency-warning-renewal'));"
        "out.token = window.ofLatencyWarning || ''; out.locked = locked; out.footer = footer();"
    )
    assert out == {
        "renews": False,
        "token": "",
        "locked": None,
        "footer": ["Other"] if then == "another-modal" else [],
    }


def test_a_reopened_dialog_keeps_its_own_token_when_the_first_answer_comes_last() -> None:
    out = _run(
        "ofLatencyDialog(); var first = asked[0].target; closeModal(); await settle();"
        "ofLatencyDialog(); answer(asked[2].target, INTRO_OTHER); answer(first, INTRO);"
        "out.token = window.ofLatencyWarning; out.footer = footer();"
        "out.start = document.querySelector('#modal-footer .primary').getAttribute('hx-vals');"
        "closeModal(); out.beacons = beacons;"
    )
    assert out == {
        "token": _OTHER,
        "footer": ["Cancel", "Start"],
        "start": f'{{"token": "{_OTHER}"}}',
        "beacons": [f"/section/video_source/latency/warning/close?token={_OTHER}"],
    }


@pytest.mark.parametrize("state", ["INTRO", "RESULT"])
@pytest.mark.parametrize(("successful", "locked"), [(False, False), (True, True)])
def test_a_start_locks_the_modal_on_the_click_and_a_refused_one_releases_it(
    state: str, successful: bool, locked: bool
) -> None:
    out = _run(
        f"ofLatencyDialog(); answer(asked[0].target, {state});"
        "var start = document.querySelector('#modal-footer [hx-post]');"
        "(function () { eval(start.getAttribute('hx-on::before-request')); })(); out.clicked = locked;"
        f"(function (event) {{ eval(start.getAttribute('hx-on::after-request')); }})"
        f"({{detail: {{successful: {'true' if successful else 'false'}}}}});"
        "out.after = locked;"
    )
    assert out == {"clicked": True, "after": locked}


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
