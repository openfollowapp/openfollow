# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Michel Honold and Paul Hermann
"""feature-save.js, run in node on the fake DOM: the Features table applies on the Station Settings
Save, a Save that switches a feature off asks first and names what stops, and the page flips its
feature classes only once the save has gone through."""

from __future__ import annotations

import json
import shutil
import subprocess  # nosec B404 - runs the local node binary on a fixed script
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.skipif(shutil.which("node") is None, reason="needs node")]

_TESTS = Path(__file__).parent
_SCRIPT = _TESTS.parent / "openfollow" / "web" / "static" / "js" / "feature-save.js"

_MARKUP = (
    '<form id="general-network-section"></form><form id="other"></form>'
    '<div id="features-section">'
    '<input type="checkbox" name="zones" form="general-network-section" data-saved="1"'
    ' data-title="Trigger zones" data-off="zones stop firing.">'
    '<input type="checkbox" name="paths" form="general-network-section" data-saved="1"'
    ' data-title="Paths" data-off="every marker on a path is let go.">'
    '<input type="checkbox" name="midi" form="general-network-section" data-saved="0"'
    ' data-title="MIDI" data-off="MIDI devices are closed.">'
    "</div>"
)

# The modal helpers stand in for base.tpl's: one dialog at a time, its buttons clickable.
_PAGE = f"""
var modal = null, title = null;
function escapeHTML(v) {{ return String(v); }}
function closeModal() {{ var was = modal; modal = null; if (was) was.remove(); }}
function openModal(opts) {{
    title = opts.title;
    modal = document.body.appendChild(document.createElement('div'));
    parseHTML('<div class="modal-card">' + opts.bodyHTML + '<div class="modal-footer"></div></div>', modal);
    opts.footerButtons.forEach(function (b) {{
        var btn = modal.querySelector('.modal-footer').appendChild(document.createElement('button'));
        if (b.kind) btn.className = b.kind;
        btn.textContent = b.label;
        btn.addEventListener('click', b.onClick);
    }});
}}
parseHTML({json.dumps(_MARKUP)}, document.body);
document.body.className = 'feature-zones feature-paths';
function box(name) {{ return document.querySelector('input[name="' + name + '"]'); }}
box('zones').checked = true;
box('paths').checked = true;
var issued = [];
function save(formId) {{
    var ev = makeEvent('htmx:confirm', {{ detail: {{
        elt: document.getElementById(formId || 'general-network-section'), question: null,
        issueRequest: function (skip) {{ issued.push(skip); }},
    }} }});
    ev.detail.elt.dispatchEvent(ev);
    return ev;
}}
function saved(ok) {{
    // htmx's order: beforeSwap on the form, the response replaces it, then afterRequest on the detached form.
    var form = document.getElementById('general-network-section');
    form.dispatchEvent(makeEvent('htmx:beforeSwap', {{ detail: {{ elt: form, isError: !ok }} }}));
    if (ok) {{
        var fresh = document.createElement('form');
        fresh.setAttribute('id', 'general-network-section');
        form.parentNode.insertBefore(fresh, form);
        form.remove();
    }}
    form.dispatchEvent(makeEvent('htmx:afterRequest', {{ detail: {{ elt: form, successful: ok }} }}));
}}
function button(label) {{
    return modal.querySelectorAll('button').filter(function (b) {{ return b.textContent === label; }})[0];
}}
function state() {{
    return {{
        body: document.body.className.split(' ').filter(Boolean).sort(),
        saved: ['zones', 'paths', 'midi'].map(function (n) {{ return box(n).dataset.saved; }}),
    }};
}}
"""


def _run(steps: str) -> Any:
    source = "\n".join(
        [
            (_TESTS / "_fake_dom.js").read_text(encoding="utf-8"),
            _PAGE,
            _SCRIPT.read_text(encoding="utf-8"),
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


def test_a_save_that_switches_nothing_off_goes_straight_through() -> None:
    out = _run("var ev = save(); out.prevented = ev.defaultPrevented; out.modal = modal !== null;")
    assert out == {"prevented": False, "modal": False}


def test_switching_a_feature_on_asks_nothing() -> None:
    out = _run("box('midi').checked = true; var ev = save(); out.prevented = ev.defaultPrevented;")
    assert out == {"prevented": False}


def test_switching_features_off_names_each_one_and_what_stops() -> None:
    out = _run(
        "box('zones').checked = false; box('paths').checked = false; var ev = save();"
        "out.prevented = ev.defaultPrevented; out.title = title; out.text = modal.textContent;"
        "out.issued = issued.length;"
    )
    assert out["prevented"] is True
    assert out["title"] == "Turn these features off?"
    assert "Trigger zones: zones stop firing." in out["text"]
    assert "Paths: every marker on a path is let go." in out["text"]
    assert "MIDI" not in out["text"]
    assert out["issued"] == 0


def test_one_feature_switched_off_is_asked_about_on_its_own() -> None:
    out = _run("box('zones').checked = false; save(); out.title = title; out.text = modal.textContent;")
    assert out["title"] == "Turn this feature off?"
    assert "Paths" not in out["text"]


def test_cancel_sends_nothing() -> None:
    out = _run(
        "box('zones').checked = false; save(); button('Cancel').click();"
        "out.issued = issued.length; out.modal = modal !== null;"
    )
    assert out == {"issued": 0, "modal": False}


def test_turn_off_is_the_danger_button_and_saves() -> None:
    out = _run(
        "box('zones').checked = false; save(); var b = button('Turn off'); out.kind = b.className; b.click();"
        "out.issued = issued; out.modal = modal !== null;"
    )
    assert out == {"kind": "danger", "issued": [True], "modal": False}


def test_a_successful_save_flips_the_classes_and_records_the_stored_state() -> None:
    out = _run("box('zones').checked = false; box('midi').checked = true; saved(true); out.after = state();")
    assert out["after"] == {"body": ["feature-midi", "feature-paths"], "saved": ["0", "1", "1"]}


def test_a_feature_switched_off_by_an_earlier_save_is_not_asked_about_again() -> None:
    out = _run(
        "box('zones').checked = false; save(); button('Turn off').click(); saved(true);"
        "var ev = save(); out.prevented = ev.defaultPrevented; out.modal = modal !== null;"
    )
    assert out == {"prevented": False, "modal": False}


def test_nothing_changes_before_the_save_or_after_a_failed_one() -> None:
    out = _run("box('zones').checked = false; out.ticked = state(); saved(false); out.failed = state();")
    unchanged = {"body": ["feature-paths", "feature-zones"], "saved": ["1", "1", "0"]}
    assert out == {"ticked": unchanged, "failed": unchanged}


def test_other_forms_are_left_alone() -> None:
    out = _run("box('zones').checked = false; var ev = save('other'); out.prevented = ev.defaultPrevented;")
    assert out == {"prevented": False}
