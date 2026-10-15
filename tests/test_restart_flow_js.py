# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The one restart flow every Restart button runs, in node on the fake DOM."""

from __future__ import annotations

import json
import re
import shutil
import subprocess  # nosec B404 - runs the local node binary on a fixed script
from pathlib import Path
from typing import Any

import pytest

from tests._template_source import function_body

pytestmark = [pytest.mark.unit, pytest.mark.skipif(shutil.which("node") is None, reason="needs node")]

_TESTS = Path(__file__).parent
_WEB = _TESTS.parent / "openfollow" / "web"
_TEMPLATES = _WEB / "templates"
_DEFAULT_NOTICE = "App is restarting… Please wait."
_REFUSAL = {"error": "An update is running.", "action": "Restart once it has finished."}


def _onclick(partial: str) -> str:
    """The onclick of the partial's one Restart button, as written in the template."""
    text = (_TEMPLATES / "partials" / partial).read_text(encoding="utf-8")
    calls = re.findall(r'onclick="(openfollowRestart\([^"]*\))"', text)
    assert len(calls) == 1, f"{partial}: expected one Restart button"
    return calls[0]


_KEYBOARD = _onclick("keyboard.tpl")


def _markup(onclick: str) -> str:
    return (
        f'<div id="top-restart-notice" class="restart-notice">{_DEFAULT_NOTICE}</div>'
        '<form class="section" id="sec"><div class="actions">'
        f'<button type="button" class="danger" onclick="{onclick}">Restart OpenFollow</button>'
        "</div></form>"
    )


_HELPERS = """
var asked = [], answer = true, reloaded = 0, info = 'unreachable', restartAnswer = { body: { success: true } };
function modalConfirm(opts) { asked.push(opts); return Promise.resolve(answer); }
globalThis.location = { reload: function () { reloaded++; } };
station = function (req) {
    if (req.url === '/api/restart') return restartAnswer;
    if (req.url === '/api/info') return info;
    return { status: 404 };
};
var button = document.querySelector('button.danger');
var notice = document.getElementById('top-restart-notice');
async function press() {
    button.click();
    (0, eval)(button.getAttribute('onclick'));
    await settle();
}
function urls() { return requests.map(function (r) { return r.method + ' ' + r.url; }); }
function errorLine() { var l = document.querySelector('.save-error'); return l ? l.textContent : null; }
"""


def _run(steps: str, onclick: str = _KEYBOARD) -> Any:
    source = "\n".join(
        [
            (_TESTS / "_fake_dom.js").read_text(encoding="utf-8"),
            f"parseHTML({json.dumps(_markup(onclick))}, document.body);",
            (_WEB / "static" / "js" / "save-feedback.js").read_text(encoding="utf-8"),
            "async function openfollowRestart(opts) {"
            + function_body((_TEMPLATES / "base.tpl").read_text(encoding="utf-8"), "openfollowRestart")
            + "}",
            _HELPERS,
            "var out = {};",
            "(async function () {",
            steps,
            "process.stdout.write(JSON.stringify(out));",
            "})();",
        ]
    )
    node = shutil.which("node")
    assert node is not None
    done = subprocess.run([node, "-e", source], capture_output=True, text=True, timeout=20)  # nosec B603
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_a_cancelled_restart_asks_the_station_nothing() -> None:
    out = _run("answer = false; await press(); out.urls = urls(); out.shown = notice.style.display || '';")
    assert out == {"urls": [], "shown": ""}


def test_a_restart_shows_the_notice_and_reloads_once_the_station_answers_again() -> None:
    out = _run(
        "await press();"
        "out.urls = urls(); out.shown = notice.style.display; out.text = notice.textContent;"
        "out.disabled = button.disabled; out.error = errorLine();"
        # Away while it restarts, then answering but not ready: polled, no reload.
        "tickIntervals(); await settle(); out.whileAway = reloaded;"
        "info = { status: 503 }; tickIntervals(); await settle(); out.starting = reloaded;"
        "info = { body: {} }; tickIntervals(); await settle(); out.back = reloaded;"
        # Polling stops once it reloaded.
        "var polls = urls().length; tickIntervals(); await settle(); out.after = urls().length - polls;"
    )
    assert out == {
        "urls": ["POST /api/restart"],
        "shown": "block",
        "text": _DEFAULT_NOTICE,
        "disabled": True,
        "error": None,
        "whileAway": 0,
        "starting": 0,
        "back": 1,
        "after": 0,
    }


def test_a_refused_restart_says_an_update_is_running() -> None:
    out = _run(
        f"restartAnswer = {{ status: 409, body: {json.dumps(_REFUSAL)} }};"
        "await press(); tickIntervals(); await settle();"
        "out.error = errorLine(); out.shown = notice.style.display || ''; out.urls = urls();"
        "out.disabled = button.disabled;"
    )
    assert out == {
        "error": "Not restarted. An update is running. Restart once it has finished.",
        "shown": "",
        "urls": ["POST /api/restart"],
        "disabled": False,
    }


def test_a_restart_that_gets_no_answer_says_so() -> None:
    out = _run(
        "restartAnswer = 'unreachable'; await press(); tickIntervals(); await settle();"
        "out.error = errorLine(); out.shown = notice.style.display || ''; out.urls = urls();"
    )
    assert out == {
        "error": "Not restarted. The station did not answer. "
        "Check that it is switched on and on this network, then save again.",
        "shown": "",
        "urls": ["POST /api/restart"],
    }


@pytest.mark.parametrize(
    ("partial", "title", "message", "notice"),
    [
        ("diagnostics.tpl", "Restart application?", "Video and tracking stop until the station is back.", None),
        ("keyboard.tpl", "Restart OpenFollow?", "Outputs pause until OpenFollow is back.", None),
        (
            "interface_assignment.tpl",
            "Restart OpenFollow?",
            "Outputs pause until OpenFollow is back. The web UI may then answer at a different address.",
            "App is restarting… If this page does not come back, the web UI has moved"
            " - the station's Network screen lists the address that reaches it.",
        ),
    ],
)
def test_every_restart_button_runs_the_shared_flow_with_its_own_wording(
    partial: str, title: str, message: str, notice: str | None
) -> None:
    out = _run("await press(); out.asked = asked; out.urls = urls(); out.text = notice.textContent;", _onclick(partial))
    assert out == {
        "asked": [{"title": title, "message": message, "confirmLabel": "Restart", "danger": True}],
        "urls": ["POST /api/restart"],
        "text": notice or _DEFAULT_NOTICE,
    }


def test_no_template_restarts_by_any_other_route() -> None:
    sources = [*_TEMPLATES.rglob("*.tpl"), _WEB / "routes.py"]
    offenders = [p.name for p in sources if re.search(r"/section/\w+/restart\b", p.read_text(encoding="utf-8"))]
    assert offenders == []
