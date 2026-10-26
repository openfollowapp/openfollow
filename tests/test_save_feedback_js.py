# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The failed-save line in node on the fake DOM: an action that saves nothing names its own lead."""

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
    '<div data-error-lead="Not started."><button type="button" id="measure">Measure latency</button></div>'
    '<input id="field"><div class="actions"><button type="submit" id="save">Save</button></div></form>'
)
_REFUSED = json.dumps({"error": "No video is arriving.", "action": "Measure once the camera's picture shows."})


def _line(event: str, button: str) -> Any:
    xhr = f"{{status: 409, responseText: {json.dumps(_REFUSED)}}}"
    detail = f"{{elt: document.getElementById('{button}'), requestConfig: {{verb: 'post'}}, xhr: {xhr}}}"
    source = "\n".join(
        [
            (_TESTS / "_fake_dom.js").read_text(encoding="utf-8"),
            f"parseHTML({json.dumps(_MARKUP)}, document.body);",
            (_WEB / "static" / "js" / "save-feedback.js").read_text(encoding="utf-8"),
            f"document.dispatchEvent({{type: '{event}', bubbles: true, detail: {detail}}});",
            "var line = document.querySelector('.save-error');",
            "process.stdout.write(JSON.stringify(line ? line.textContent : null));",
        ]
    )
    node = shutil.which("node")
    assert node is not None
    done = subprocess.run([node, "-e", source], capture_output=True, text=True, timeout=20)  # nosec B603
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_a_refused_action_reads_with_the_lead_its_row_names() -> None:
    assert _line("htmx:responseError", "measure") == (
        "Not started. No video is arriving. Measure once the camera's picture shows."
    )


def test_an_unanswered_action_reads_with_the_lead_its_row_names() -> None:
    assert _line("htmx:sendError", "measure").startswith("Not started. The station did not answer.")


def test_a_save_outside_the_row_keeps_not_saved() -> None:
    assert _line("htmx:responseError", "save").startswith("Not saved. No video is arriving.")
