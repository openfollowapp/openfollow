# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Michel Honold and Paul Hermann
"""The web UI's poll gate, run in node: a poll asks only while its section is in view."""

from __future__ import annotations

import json
import shutil
import subprocess  # nosec B404 - runs the local node binary on a fixed script
from pathlib import Path

import pytest

from tests._template_source import base_functions

pytestmark = [pytest.mark.unit, pytest.mark.skipif(shutil.which("node") is None, reason="needs node")]

_SCRIPT = Path(__file__).parents[1] / "openfollow" / "web" / "static" / "js" / "poll-gate.js"

# A section holding a poll element, a loose element outside any section, and a
# browser tab the test hides and shows. ``laidOut`` stands in for layout: false
# is what an inactive tab, a hidden wizard step or ``display:none`` leaves.
_HARNESS = """
var triggered = [], visibility = [];
function el(parent, cls, laidOut) {
    var node = {parent: parent, cls: cls ? [cls] : [], laidOut: laidOut,
        classList: {contains: function (c) { return node.cls.indexOf(c) >= 0; }},
        closest: function (sel) {
            for (var n = node; n; n = n.parent) { if (sel === '.section' && n.classList.contains('section')) return n; }
            return null;
        },
        getClientRects: function () { return node.laidOut ? [{}] : []; }};
    return node;
}
var section = el(null, 'section', true);
var poll = el(section, '', true);
var driver = el(section, '', false);
var loose = el(null, '', true);
var document = {hidden: false, body: 'body',
    addEventListener: function (name, fn) { if (name === 'visibilitychange') visibility.push(fn); }};
var window = {document: document, htmx: {trigger: function (target, name) { triggered.push([target, name]); }}};
function setHidden(hidden) { document.hidden = hidden; visibility.forEach(function (fn) { fn(); }); }
"""


def _run(steps: str) -> object:
    source = _HARNESS + _SCRIPT.read_text(encoding="utf-8") + f"\nvar out = [];\n{steps}\n"
    source += "process.stdout.write(JSON.stringify(out));"
    node = shutil.which("node")
    assert node is not None
    done = subprocess.run([node, "-e", source], capture_output=True, text=True, check=True, timeout=10)  # nosec B603
    return json.loads(done.stdout)


def test_a_poll_in_an_open_section_on_screen_asks() -> None:
    assert _run("out.push(window.pollShown(poll));") == [True]


def test_a_hidden_browser_tab_asks_nothing() -> None:
    assert _run("document.hidden = true; out.push(window.pollShown(poll), window.pollShown(loose));") == [False, False]


def test_a_folded_section_asks_nothing() -> None:
    assert _run("section.cls.push('is-collapsed'); out.push(window.pollShown(poll));") == [False]


def test_a_section_on_another_tab_asks_nothing() -> None:
    assert _run("section.laidOut = false; out.push(window.pollShown(poll));") == [False]


def test_a_hidden_driver_asks_while_its_section_is_in_view() -> None:
    # A ``hidden`` poll driver has no layout of its own: the section decides.
    assert _run("out.push(window.pollShown(driver));") == [True]


@pytest.mark.parametrize(("laid_out", "expected"), [(True, True), (False, False)])
def test_outside_any_section_the_element_itself_decides(laid_out: bool, expected: bool) -> None:
    assert _run(f"loose.laidOut = {json.dumps(laid_out)}; out.push(window.pollShown(loose));") == [expected]


def test_the_answer_is_strictly_boolean() -> None:
    # htmx runs a trigger only when its filter returns exactly ``true``.
    types = _run("out.push(typeof window.pollShown(poll), typeof window.pollShown(loose));")
    assert types == ["boolean", "boolean"]


def test_showing_the_browser_tab_again_announces_once_and_hiding_it_does_not() -> None:
    steps = "setHidden(true); out.push(triggered.slice()); setHidden(false); out.push(triggered.slice());"
    (hidden, shown) = _run(steps)
    assert hidden == []
    assert shown == [["body", "poll-shown"]]


def test_an_announce_reaches_every_poll_through_the_body() -> None:
    assert _run("window.announcePollsShown(); out.push(triggered);") == [[["body", "poll-shown"]]]


def test_an_announce_before_htmx_loads_is_a_no_op() -> None:
    assert _run("delete window.htmx; window.announcePollsShown(); out.push(triggered.length);") == [0]


# ---------------------------------------------------------------------------
# base.tpl: a tab switch and an unfold bring their polls up to date at once
# ---------------------------------------------------------------------------

_TAB_MARKUP = (
    '<button class="tab-btn" data-tab="overview">O</button><button class="tab-btn" data-tab="input">I</button>'
    '<div class="tab-content" id="tab-overview"><div class="section" data-help="status">'
    '<div class="section-head"><h2>Status</h2></div></div></div>'
    '<div class="tab-content" id="tab-input"><div class="section" data-fold-key="slots" data-fold-default="collapsed">'
    '<div class="section-head"><h2>Slots</h2></div><div id="slots"></div></div></div>'
)
_PAGE_FUNCTIONS = ("switchTab", "goToSection", "initTabs", "setSectionCollapsed", "initializeSectionFolding")


def _page_script() -> str:
    return base_functions(*_PAGE_FUNCTIONS)


def _run_page(steps: str) -> object:
    source = "\n".join(
        [
            (Path(__file__).parent / "_fake_dom.js").read_text(encoding="utf-8"),
            f"parseHTML({json.dumps(_TAB_MARKUP)}, document.body);",
            "var announced = 0; function announcePollsShown() { announced++; }",
            "function closeHelp() {} function readStorage() { return null; } function writeStorage() {}",
            "function getSectionFoldStorageKey(key) { return key; } function requestAnimationFrame() {}",
            "var location = {pathname: '/', search: '', hash: ''};",
            _page_script(),
            "initTabs(); initializeSectionFolding(document);",
            "var toggle = document.querySelector('.section-toggle');",
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


def test_opening_the_page_announces_nothing() -> None:
    # The page was just rendered: nothing it shows is stale yet.
    assert _run_page("out.push(announced);") == [0]


def test_clicking_a_tab_announces_once() -> None:
    assert _run_page("document.querySelectorAll('.tab-btn')[1].click(); out.push(announced);") == [1]


def test_a_cross_tab_link_announces_once() -> None:
    assert _run_page("goToSection('input', 'slots'); out.push(announced);") == [1]


def test_unfolding_a_section_announces_and_folding_it_does_not() -> None:
    assert _run_page("toggle.click(); out.push(announced); toggle.click(); out.push(announced);") == [1, 1]


def test_a_section_without_a_fold_key_gets_its_help_and_no_toggle() -> None:
    steps = (
        "var only = document.querySelector('#tab-overview .section');"
        "out.push(only.querySelectorAll('.section-help-btn').length, only.querySelectorAll('.section-toggle').length);"
        "out.push(only.classList.contains('is-collapsed'));"
        "initializeSectionFolding(document);"
        "out.push(only.querySelectorAll('.section-help-btn').length);"
    )
    assert _run_page(steps) == [1, 0, False, 1]
