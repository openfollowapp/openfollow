# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The failed-save line in node on the fake DOM: an action that saves nothing names its own lead,
asks to try again rather than save again, and reports in its own box."""

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
    '<div class="field" id="row" data-error-box>'
    '<button type="button" id="measure" data-error-lead="Not started.">Measure latency</button>'
    '<button type="button" id="stop" data-error-lead="Not stopped.">Stop</button></div>'
    '<input id="field"><div class="actions" id="actions"><button type="submit" id="save">Save</button></div></form>'
)
_REFUSED = json.dumps({"error": "No video is arriving.", "action": "Measure once the camera's picture shows."})


def _failed(event: str, button: str) -> Any:
    """What the page shows after ``button``'s request fails with ``event``: the line, its box, and which ring."""
    xhr = f"{{status: 409, responseText: {json.dumps(_REFUSED)}}}"
    detail = f"{{elt: document.getElementById('{button}'), requestConfig: {{verb: 'post'}}, xhr: {xhr}}}"
    source = "\n".join(
        [
            (_TESTS / "_fake_dom.js").read_text(encoding="utf-8"),
            f"parseHTML({json.dumps(_MARKUP)}, document.body);",
            (_WEB / "static" / "js" / "save-feedback.js").read_text(encoding="utf-8"),
            f"document.dispatchEvent({{type: '{event}', bubbles: true, detail: {detail}}});",
            "var line = document.querySelector('.save-error');",
            "var ringed = ['row', 'sec'].filter(function (id) {",
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


def test_a_refused_start_reads_with_its_own_lead_in_its_own_box() -> None:
    assert _failed("htmx:responseError", "measure") == {
        "line": "Not started. No video is arriving. Measure once the camera's picture shows.",
        "box": "row",
        "ringed": ["row"],
    }


def test_a_refused_stop_reads_with_its_own_lead() -> None:
    assert _failed("htmx:responseError", "stop")["line"].startswith("Not stopped. No video is arriving.")


@pytest.mark.parametrize(("button", "lead"), [("measure", "Not started."), ("stop", "Not stopped.")])
def test_an_unanswered_action_asks_to_try_again_not_to_save(button: str, lead: str) -> None:
    assert _failed("htmx:sendError", button)["line"] == (
        f"{lead} The station did not answer. Check that it is switched on and on this network, then try again."
    )


def test_a_save_outside_the_box_keeps_not_saved_and_the_form_ring() -> None:
    out = _failed("htmx:responseError", "save")
    assert out["line"].startswith("Not saved. No video is arriving.")
    assert (out["box"], out["ringed"]) == ("sec", ["sec"])


def test_an_unanswered_save_still_asks_to_save_again() -> None:
    assert _failed("htmx:sendError", "save")["line"].endswith("then save again.")
