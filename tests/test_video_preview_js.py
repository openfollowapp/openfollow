# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Michel Honold and Paul Hermann
"""The Video Source snapshot preview, run in node: it fetches only while its section is in view."""

from __future__ import annotations

import json
import shutil
import subprocess  # nosec B404 - runs the local node binary on a fixed script
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.skipif(shutil.which("node") is None, reason="needs node")]

_TESTS = Path(__file__).parent
_TEMPLATE = _TESTS.parent / "openfollow" / "web" / "templates" / "partials" / "video_source.tpl"
_MARKUP = (
    '<div class="section" id="video-source-section"><input type="checkbox" id="show-preview-cb">'
    '<div id="video-preview-wrap"><img id="video-preview"><span id="video-preview-hint"></span></div></div>'
)
# ``shown`` stands in for poll-gate.js; every ``new Image()`` is one snapshot request.
_HELPERS = """
var shown = true, fetched = 0;
function pollShown() { return shown; }
function Image() { fetched++; }
function tick() { tickIntervals(); }
function announce() { document.body.dispatchEvent(makeEvent('poll-shown')); }
function turnOn() {
    var cb = document.getElementById('show-preview-cb');
    cb.checked = true;
    cb.dispatchEvent(makeEvent('change'));
}
"""


def _preview_script() -> str:
    text = _TEMPLATE.read_text(encoding="utf-8")
    anchor = text.index("getElementById('show-preview-cb')")
    start = text.rindex("<script>", 0, anchor) + len("<script>")
    return text[start : text.index("</script>", anchor)]


def _run(steps: str) -> object:
    source = "\n".join(
        [
            (_TESTS / "_fake_dom.js").read_text(encoding="utf-8"),
            f"var load = function () {{ parseHTML({json.dumps(_MARKUP)}, document.body); {_preview_script()} }};",
            "load();",
            _HELPERS,
            "var out = [];",
            steps,
            "process.stdout.write(JSON.stringify(out));",
        ]
    )
    node = shutil.which("node")
    assert node is not None
    done = subprocess.run([node, "-e", source], capture_output=True, text=True, timeout=20)  # nosec B603
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_turning_it_on_fetches_at_once_and_on_every_tick() -> None:
    assert _run("turnOn(); out.push(fetched); tick(); tick(); out.push(fetched);") == [1, 3]


def test_it_fetches_nothing_while_its_section_is_out_of_view() -> None:
    assert _run("turnOn(); shown = false; tick(); tick(); out.push(fetched);") == [1]


def test_coming_back_into_view_fetches_at_once() -> None:
    assert _run("turnOn(); shown = false; tick(); shown = true; announce(); out.push(fetched);") == [2]


def test_coming_into_view_while_it_is_off_fetches_nothing() -> None:
    assert _run("announce(); out.push(fetched);") == [0]


def test_a_re_render_leaves_one_listener() -> None:
    # The section is swapped while the preview is on: the old checkbox leaves the page and the script runs again.
    steps = (
        "turnOn(); document.getElementById('video-source-section').remove(); load(); turnOn(); out.push(fetched);"
        "announce(); out.push(fetched); announce(); out.push(fetched);"
    )
    assert _run(steps) == [2, 3, 4]
