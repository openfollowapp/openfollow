# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Michel Honold and Paul Hermann
"""The failed-save line in node on the fake DOM: an action that saves nothing names its own lead,
asks to try again rather than save again, and reports in the dialog it sits in."""

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
_MARKUP = (
    '<form class="section" id="sec">'
    '<input id="field"><div class="actions" id="actions"><button type="submit" id="save">Save</button></div></form>'
    '<div class="modal-card" id="card"><div class="modal-body"></div><div class="modal-footer" id="footer">'
    '<button type="button" id="start" data-error-lead="Not started.">Start</button></div>'
    '<div id="renewal" hidden data-error-quiet></div></div>'
)
_ACTION = "Measure when the camera's picture is on the Operator Screen."
_REFUSED = json.dumps({"error": "No video is arriving.", "action": _ACTION})


def _failed(event: str, button: str, then: str = "") -> Any:
    """After ``button``'s request fails with ``event`` (and ``then``): the line, its box, the ring."""
    xhr = f"{{status: 409, responseText: {json.dumps(_REFUSED)}}}"
    detail = f"{{elt: document.getElementById('{button}'), requestConfig: {{verb: 'post'}}, xhr: {xhr}}}"
    source = "\n".join(
        [
            (_TESTS / "_fake_dom.js").read_text(encoding="utf-8"),
            f"parseHTML({json.dumps(_MARKUP)}, document.body);",
            (_WEB / "static" / "js" / "save-feedback.js").read_text(encoding="utf-8"),
            f"document.dispatchEvent({{type: '{event}', bubbles: true, detail: {detail}}});",
            then,
            "var line = document.querySelector('.save-error');",
            "var ringed = ['card', 'sec'].filter(function (id) {",
            "    return document.getElementById(id).classList.contains('save-failed'); });",
            "process.stdout.write(JSON.stringify({line: line && line.textContent,",
            "    box: line && line.parentNode.getAttribute('id'), ringed: ringed}));",
        ]
    )
    node = shutil.which("node")
    assert node is not None
    done = subprocess.run([node, "-e", source], capture_output=True, text=True, timeout=20)  # nosec B603
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_a_refused_start_reads_with_its_own_lead_in_its_dialog() -> None:
    assert _failed("htmx:responseError", "start") == {
        "line": f"Not started. No video is arriving. {_ACTION}",
        "box": "card",
        "ringed": ["card"],
    }


def test_an_unanswered_start_asks_to_try_again_not_to_save() -> None:
    assert _failed("htmx:sendError", "start")["line"] == (
        "Not started. The station did not answer. Check that it is switched on and on this network, then try again."
    )


def test_a_save_outside_the_dialog_keeps_not_saved_and_the_form_ring() -> None:
    out = _failed("htmx:responseError", "save")
    assert out["line"].startswith("Not saved. No video is arriving.")
    assert (out["box"], out["ringed"]) == ("sec", ["sec"])


@pytest.mark.parametrize("event", ["htmx:responseError", "htmx:sendError"])
def test_a_quiet_background_request_reports_nothing(event: str) -> None:
    assert _failed(event, "renewal") == {"line": None, "box": None, "ringed": []}


def _request(elt: str) -> str:
    """htmx about to send a POST from ``elt``."""
    detail = f"{{elt: document.getElementById('{elt}'), requestConfig: {{verb: 'post'}}}}"
    return f"document.dispatchEvent({{type: 'htmx:beforeRequest', bubbles: true, detail: {detail}}});"


def test_a_refused_start_s_line_outlasts_the_renewals_posting_beside_it() -> None:
    out = _failed("htmx:responseError", "start", _request("renewal") * 3)
    assert out["line"] == f"Not started. No video is arriving. {_ACTION}"


def test_another_start_clears_the_line() -> None:
    assert _failed("htmx:responseError", "start", _request("start"))["line"] is None


def test_an_unanswered_save_still_asks_to_save_again() -> None:
    assert _failed("htmx:sendError", "save")["line"].endswith("then save again.")
