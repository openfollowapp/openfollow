# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The one restart flow every restart runs, in node on the fake DOM: the Restart buttons,
Restore Defaults and a switch to Simple network mode that restarts OpenFollow."""

from __future__ import annotations

import json
import re
import shutil
import subprocess  # nosec B404 - runs the local node binary on a fixed script
from pathlib import Path
from typing import Any

import pytest

from tests._template_source import function_body, restart_script

pytestmark = [pytest.mark.unit, pytest.mark.skipif(shutil.which("node") is None, reason="needs node")]

_TESTS = Path(__file__).parent
_WEB = _TESTS.parent / "openfollow" / "web"
_TEMPLATES = _WEB / "templates"
_REFUSAL = {"error": "An update is running.", "action": "Restart once it has finished."}
_RELOADS = "This page reloads when the station is back."
_UNREACHABLE = (
    "Not restarted. The station did not answer. Check that it is switched on and on this network, then save again."
)


def _onclick(partial: str) -> str:
    """The onclick of the partial's one Restart button, as written in the template."""
    text = (_TEMPLATES / "partials" / partial).read_text(encoding="utf-8")
    calls = re.findall(r'onclick="(openfollowRestart\([^"]*\))"', text)
    assert len(calls) == 1, f"{partial}: expected one Restart button"
    return calls[0]


_KEYBOARD = _onclick("keyboard.tpl")
_MARKUP = (
    '<form class="section" id="sec"><div class="actions">'
    '<button type="button" class="danger">Restart OpenFollow</button></div></form>'
)

# The modal helpers stand in for base.tpl's: they record what each dialog shows.
_HELPERS = """
var asked = [], answer = true, reloaded = 0, info = 'unreachable';
var restartAnswer = { body: { success: true, instance: 'old' } };
var modal = null, now = 0;
performance.now = function () { return now; };
function escapeHTML(v) { return String(v).replace(/&/g, '&amp;').replace(/</g, '&lt;'); }
function modalConfirm(opts) { asked.push(opts); return Promise.resolve(answer); }
function openModal(opts) {
    if (modal) closeModal();
    var specs = opts.footerButtons || [];
    modal = { title: opts.title, body: opts.bodyHTML, locked: opts.dismissable === false, onClose: opts.onClose,
              buttons: specs.map(function (b) { return b.label; }) };
    modal.press = function (label) { specs.find(function (b) { return b.label === label; }).onClick(); };
}
function closeModal() { var was = modal; modal = null; if (was && was.onClose) was.onClose(); }
globalThis.location = { reload: function () { reloaded++; } };
var answers = {};
station = function (req) {
    if (answers[req.method + ' ' + req.url]) return answers[req.method + ' ' + req.url];
    if (req.url === '/api/restart') return restartAnswer;
    if (req.url === '/api/info') return info;
    return { status: 404 };
};
var button = document.querySelector('button.danger');
// Confirm the dialog; the station's answer to the restart waits for release().
async function confirmHeld(onclick) {
    holdWhen = function (r) { return r.method === 'POST'; };
    button.click();
    (0, eval)(onclick);
    await settle();
}
async function press(onclick) { await confirmHeld(onclick); release(); await settle(); }
async function tick(ms) { now += ms; tickIntervals(); await settle(); }
function shown() {
    return modal && { title: modal.title, body: modal.body, locked: modal.locked, buttons: modal.buttons };
}
function urls() { return requests.map(function (r) { return r.method + ' ' + r.url; }); }
function errorLine() { var l = document.querySelector('.save-error'); return l ? l.textContent : null; }
"""


def _progress(note: str = _RELOADS) -> dict[str, Any]:
    return {
        "title": "Restart OpenFollow",
        "body": '<div class="modal-progress"><div class="modal-spinner"></div>'
        '<p role="status" aria-live="polite" tabindex="-1">Restarting OpenFollow…</p></div>'
        f"<p>{note}</p>",
        "locked": True,
        "buttons": [],
    }


def _run(steps: str, onclick: str = _KEYBOARD, markup: str = _MARKUP, script: str = "") -> Any:
    source = "\n".join(
        [
            (_TESTS / "_fake_dom.js").read_text(encoding="utf-8"),
            f"parseHTML({json.dumps(markup)}, document.body);",
            (_WEB / "static" / "js" / "save-feedback.js").read_text(encoding="utf-8"),
            restart_script(),
            script,
            _HELPERS,
            f"var ONCLICK = {json.dumps(onclick)};",
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
    out = _run("answer = false; await press(ONCLICK); out.urls = urls(); out.modal = shown();")
    assert out == {"urls": [], "modal": None}


def test_the_confirmed_dialog_shows_progress_before_the_station_answers() -> None:
    out = _run("await confirmHeld(ONCLICK); out.urls = urls(); out.modal = shown(); out.error = errorLine();")
    assert out == {"urls": ["POST /api/restart"], "modal": _progress(), "error": None}


def test_the_dialog_stays_until_a_new_openfollow_answers_then_the_page_reloads() -> None:
    out = _run(
        "await press(ONCLICK); out.accepted = shown();"
        # The old process still answers while it shuts down, then it is gone: still open, no reload.
        "info = { body: { instance: 'old' } }; await tick(2000); out.dying = [shown(), reloaded];"
        "info = 'unreachable'; await tick(2000); await tick(2000); out.away = [shown(), reloaded];"
        # Up but not serving yet, or not letting this browser in: still not back.
        "for (var st of [401, 403, 503]) { info = { status: st }; await tick(2000); }"
        "out.starting = [shown(), reloaded];"
        "info = { body: { instance: 'new' } }; await tick(2000); out.back = reloaded;"
        # Polling stops once it reloaded.
        "var polls = urls().length; await tick(2000); out.after = urls().length - polls;"
        "out.polls = urls().length; out.error = errorLine();"
    )
    assert out == {
        "accepted": _progress(),
        "dying": [_progress(), 0],
        "away": [_progress(), 0],
        "starting": [_progress(), 0],
        "back": 1,
        "after": 0,
        "polls": 1 + 7,
        "error": None,
    }


def test_two_polls_that_find_the_new_openfollow_reload_once() -> None:
    out = _run(
        "await press(ONCLICK); info = { body: { instance: 'new' } };"
        "holdWhen = function (r) { return r.url === '/api/info'; };"
        "now += 2000; tickIntervals(); now += 2000; tickIntervals(); await settle();"
        "release(); await settle(); out.reloaded = reloaded;"
    )
    assert out == {"reloaded": 1}


def test_a_restart_answer_without_an_identity_still_waits_for_the_station() -> None:
    out = _run(
        "restartAnswer = { body: 'restarting' }; await press(ONCLICK);"
        "await tick(2000); out.away = reloaded;"
        "info = { body: { instance: 'new' } }; await tick(2000); out.back = reloaded;"
    )
    assert out == {"away": 0, "back": 1}


def test_a_refused_restart_closes_the_dialog_and_says_an_update_is_running() -> None:
    out = _run(
        f"restartAnswer = {{ status: 409, body: {json.dumps(_REFUSAL)} }};"
        "await press(ONCLICK); await tick(2000);"
        "out.error = errorLine(); out.modal = shown(); out.urls = urls();"
    )
    assert out == {
        "error": "Not restarted. An update is running. Restart once it has finished.",
        "modal": None,
        "urls": ["POST /api/restart"],
    }


def test_a_restart_that_gets_no_answer_closes_the_dialog_and_says_so() -> None:
    out = _run(
        "restartAnswer = 'unreachable'; await press(ONCLICK); await tick(2000);"
        "out.error = errorLine(); out.modal = shown(); out.urls = urls();"
    )
    assert out == {"error": _UNREACHABLE, "modal": None, "urls": ["POST /api/restart"]}


def test_a_station_that_does_not_come_back_is_reported_after_ninety_seconds() -> None:
    out = _run(
        "await press(ONCLICK);"
        "await tick(89999); out.waiting = shown();"
        "await tick(1); out.gone = shown();"
        # Polling ends with the wait: a station back later reloads nothing.
        "info = { body: { instance: 'new' } }; var polls = urls().length; await tick(2000);"
        "out.after = [urls().length - polls, reloaded];"
        # Closed, it stays closed.
        "modal.press('Close'); await tick(2000); out.closed = shown();"
    )
    assert out == {
        "waiting": _progress(),
        "gone": {
            "title": "Station not back",
            "body": '<div class="notice error">The station has not answered for 90 s.<div class="notice-sub">'
            "Check that it is switched on and on this network, then reload this page.</div></div>",
            "locked": False,
            "buttons": ["Close"],
        },
        "after": [0, 0],
        "closed": None,
    }


_MOVED = "The web UI may have moved: the station's Network screen lists the address that reaches it."


@pytest.mark.parametrize(
    ("partial", "title", "message", "note"),
    [
        ("diagnostics.tpl", "Restart application?", "Video and tracking stop until the station is back.", _RELOADS),
        ("keyboard.tpl", "Restart OpenFollow?", "Outputs pause until OpenFollow is back.", _RELOADS),
        *[
            (
                partial,
                "Restart OpenFollow?",
                message,
                f"{_RELOADS} If it does not, the web UI has moved - the station's Network screen lists the address"
                " that reaches it.",
            )
            for partial, message in [
                (
                    "interface_assignment.tpl",
                    "Outputs pause until OpenFollow is back. The web UI may then answer at a different address.",
                ),
                ("network.tpl", "Outputs pause until OpenFollow is back."),
            ]
        ],
    ],
)
def test_every_restart_button_runs_the_shared_flow_with_its_own_wording(
    partial: str, title: str, message: str, note: str
) -> None:
    out = _run("await press(ONCLICK); out.asked = asked; out.urls = urls(); out.modal = shown();", _onclick(partial))
    assert out == {
        "asked": [{"title": title, "message": message, "confirmLabel": "Restart", "danger": True}],
        "urls": ["POST /api/restart"],
        "modal": _progress(note),
    }


@pytest.mark.parametrize("partial", ["interface_assignment.tpl", "network.tpl"])
def test_a_restart_that_may_move_the_web_ui_names_the_network_screen_if_the_station_is_not_back(
    partial: str,
) -> None:
    out = _run("await press(ONCLICK); await tick(90000); out.body = shown().body;", _onclick(partial))
    assert f'<div class="notice-sub">{_MOVED}</div>' in out["body"]


def test_no_template_restarts_by_any_other_route() -> None:
    sources = [*_TEMPLATES.rglob("*.tpl"), _WEB / "routes.py"]
    offenders = [p.name for p in sources if re.search(r"/section/\w+/restart\b", p.read_text(encoding="utf-8"))]
    assert offenders == []


# -- Restore Defaults: the reset restarts the station ---------------------------------

_TRANSFER = (_TEMPLATES / "partials" / "config_transfer.tpl").read_text(encoding="utf-8")
_TRANSFER_MARKUP = _TRANSFER[: _TRANSFER.index("<script>")]
_TRANSFER_SCRIPT = (
    "var NO_BACKUP_MADE = 'No backup was made. Details in Logs.', toasts = [];"
    "function toastOnNextLoad(message, level) { toasts.push([message, level]); return true; }"
    + _TRANSFER[_TRANSFER.index("<script>") + len("<script>") : _TRANSFER.index("</script>")]
)
_RESET = {"success": True, "needs_restart": True, "restarting": True, "instance": "old"}


def _restore(answer: str, steps: str) -> Any:
    return _run(
        f"answers['POST /api/config/reset'] = {answer};"
        "holdWhen = function (r) { return r.method === 'POST'; }; restoreDefaults(); await settle();" + steps,
        markup=_TRANSFER_MARKUP,
        script=_TRANSFER_SCRIPT,
    )


def test_restore_defaults_waits_for_a_new_openfollow_like_every_restart() -> None:
    out = _restore(
        f"{{ body: {json.dumps(_RESET)} }}",
        "out.asked = asked.map(function (a) { return a.title; }); out.sent = shown();"
        "release(); await settle();"
        "info = { body: { instance: 'old' } }; await tick(2000); out.dying = [shown(), reloaded];"
        "info = 'unreachable'; await tick(2000); out.away = [shown(), reloaded];"
        "info = { body: { instance: 'new' } }; await tick(2000); out.back = reloaded;"
        "out.toasts = toasts; out.error = errorLine();",
    )
    assert out == {
        "asked": ["Restore defaults?"],
        "sent": _progress(),
        "dying": [_progress(), 0],
        "away": [_progress(), 0],
        "back": 1,
        "toasts": [],
        "error": None,
    }


def test_restore_defaults_without_a_backup_says_so_after_the_reload() -> None:
    out = _restore(
        f"{{ body: {json.dumps({**_RESET, 'backup_error': 'disk full'})} }}",
        "release(); await settle(); out.toasts = toasts; out.modal = shown();",
    )
    assert out == {
        "toasts": [["Restored defaults. No backup was made. Details in Logs.", "caution"]],
        "modal": _progress(),
    }


@pytest.mark.parametrize(
    ("answer", "error"),
    [
        ("{ status: 500, body: { error: 'Disk full.' } }", "Not restored. Disk full."),
        ("{ body: { error: 'Locked.' } }", "Not restored. Locked."),
        (
            "'unreachable'",
            "Not restored. The station did not answer. Check that it is switched on and on this network, "
            "then save again.",
        ),
    ],
)
def test_a_restore_that_fails_closes_the_progress_and_says_why(answer: str, error: str) -> None:
    out = _restore(
        answer,
        "release(); await settle(); await tick(2000);out.modal = shown(); out.error = errorLine(); out.urls = urls();",
    )
    assert out == {"modal": None, "error": error, "urls": ["POST /api/config/reset"]}


# -- A switch to Simple network mode that clears a web UI pin in force ---------------------

_BASE = (_TEMPLATES / "base.tpl").read_text(encoding="utf-8")
_SWITCH_SCRIPT = (
    "function netConfirmSimple(preview) {"
    + function_body(_BASE, "netConfirmSimple")
    + "}\nasync function netSwitchMode(input) {"
    + function_body(_BASE, "netSwitchMode")
    + "}"
)
_SWITCH_MARKUP = (
    '<div id="network-interface"><form class="section"><div class="seg-toggle net-mode-switch" data-mode="advanced">'
    '<input type="radio" name="network_mode" value="simple">'
    '<input type="radio" name="network_mode" value="advanced" checked></div>'
    '<div class="actions"></div></form></div>'
)
_PREVIEW = {"station": "eth0", "pins": [], "vlans": [], "kept": [], "remove": [], "restart": True}


def _switch(mode_answer: str, steps: str, *, restart: bool = True) -> Any:
    return _run(
        f"answers['GET /section/network/mode/preview'] = {{ body: {json.dumps({**_PREVIEW, 'restart': restart})} }};"
        f"answers['POST /section/network/mode'] = {mode_answer};"
        "holdWhen = function (r) { return r.method === 'POST'; };"
        "var simple = document.querySelector('input[value=simple]');"
        "netSwitchMode(simple); await settle();"
        "if (modal && modal.title === 'Switch to Simple network mode?') modal.press('Switch to Simple');"
        "await settle();" + steps,
        markup=_SWITCH_MARKUP,
        script=_SWITCH_SCRIPT,
    )


def test_a_switch_that_restarts_waits_for_a_new_openfollow() -> None:
    out = _switch(
        "{ body: '', headers: { 'X-OpenFollow-Restarting': 'old' } }",
        "out.sent = shown(); release(); await settle(); out.answered = shown();"
        "info = { body: { instance: 'old' } }; await tick(2000); out.dying = reloaded;"
        "info = 'unreachable'; await tick(2000); out.away = reloaded;"
        "info = { body: { instance: 'new' } }; await tick(2000); out.back = reloaded;",
    )
    assert out == {"sent": _progress(), "answered": _progress(), "dying": 0, "away": 0, "back": 1}


def test_a_switch_whose_restart_was_refused_reloads_into_the_card_that_offers_it() -> None:
    out = _switch(
        "{ body: '', headers: { 'HX-Refresh': 'true' } }",
        "release(); await settle(); out.reloaded = reloaded; await tick(2000); out.polls = urls().length;",
    )
    assert out == {"reloaded": 1, "polls": 2}


def test_a_switch_that_fails_closes_the_progress_and_puts_the_mode_back() -> None:
    out = _switch(
        "{ status: 500, body: { error: 'Disk full.' } }",
        "release(); await settle();"
        "out.modal = shown(); out.error = errorLine();"
        "out.radios = document.querySelectorAll('input').map(function (r) { return [r.disabled, r.checked]; });",
    )
    assert out == {"modal": None, "error": "Not switched. Disk full.", "radios": [[False, False], [False, True]]}


def test_a_switch_that_does_not_restart_shows_no_restart() -> None:
    out = _switch(
        "{ body: '', headers: { 'HX-Refresh': 'true' } }",
        "out.sent = shown(); release(); await settle(); out.reloaded = reloaded;",
        restart=False,
    )
    assert out == {"sent": None, "reloaded": 1}
