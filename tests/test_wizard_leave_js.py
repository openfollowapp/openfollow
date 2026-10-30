# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Leave without saving, run in node: out of the Setup Wizard and onto the main page's General tab."""

from __future__ import annotations

import json
import shutil
import subprocess  # nosec B404 - runs the local node binary on a fixed script
from pathlib import Path

import pytest

from tests._template_source import base_functions
from tests._wizard_js import WIZARD_TEMPLATE, wizard_window_function

pytestmark = [pytest.mark.unit, pytest.mark.skipif(shutil.which("node") is None, reason="needs node")]

_MAIN_PAGE = (
    '<button class="tab-btn" data-tab="overview">O</button><button class="tab-btn" data-tab="general">G</button>'
    '<button class="tab-btn" data-tab="camera-grid">C</button>'
    '<div class="tab-content" id="tab-overview"></div><div class="tab-content" id="tab-general"></div>'
    '<div class="tab-content" id="tab-camera-grid"></div>'
)
_BASE_FUNCTIONS = ("readStorage", "writeStorage", "activeTabKey", "rememberTab", "switchTab", "initTabs")


def _run(steps: str) -> object:
    """``steps`` against this browser's storage, shared by the wizard and the main page as it is in a browser."""
    source = "\n".join(
        [
            (Path(__file__).parent / "_fake_dom.js").read_text(encoding="utf-8"),
            "var store = {}; var localStorage = {getItem: function (k) { return k in store ? store[k] : null; },"
            " setItem: function (k, v) { store[k] = String(v); }};",
            "var session = {}; var sessionStorage = {removeItem: function (k) { delete session[k]; }};",
            "function closeHelp() {} function announcePollsShown() {}",
            "var location = {pathname: '/wizard'}; var window = {location: {href: '/wizard'}};",
            base_functions(*_BASE_FUNCTIONS),
            "var out = [];",
            steps,
            "process.stdout.write(JSON.stringify(out));",
        ]
    )
    node = shutil.which("node")
    assert node is not None
    # On stdin: Linux caps one argument at 128 KiB.
    done = subprocess.run([node, "-"], input=source, capture_output=True, text=True, timeout=20)  # nosec B603
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


_LEAVE = (
    # The confirm's red button, as discardAndLeave hands it to the dialog.
    "var STORAGE_KEY = 'openfollow:wizard'; session[STORAGE_KEY] = '{}'; var dialog;"
    "function openModal(opts) { dialog = opts; } function closeModal() {}"
    + wizard_window_function("discardAndLeave")
    + "window.discardAndLeave();"
    "dialog.footerButtons.filter(function (b) { return b.kind === 'danger'; })[0].onClick();"
)


@pytest.mark.parametrize("last_tab", ["overview", "camera-grid"])
def test_leaving_without_saving_opens_the_main_page_on_the_general_tab(last_tab: str) -> None:
    steps = (
        f"store[activeTabKey('/')] = {json.dumps(last_tab)};"
        + _LEAVE
        + "out.push(window.location.href, 'openfollow:wizard' in session);"
        # The main page loads.
        + f"location.pathname = '/'; parseHTML({json.dumps(_MAIN_PAGE)}, document.body); initTabs();"
        + "out.push(document.querySelector('.tab-content.active').id);"
    )
    assert _run(steps) == ["/", False, "tab-general"]


def test_a_tab_chosen_on_the_main_page_is_still_the_one_it_opens_on() -> None:
    steps = (
        f"location.pathname = '/'; parseHTML({json.dumps(_MAIN_PAGE)}, document.body);"
        "initTabs(); switchTab('camera-grid');"
        "document.body.replaceChildren(); "
        f"parseHTML({json.dumps(_MAIN_PAGE)}, document.body); initTabs();"
        "out.push(document.querySelector('.tab-content.active').id);"
    )
    assert _run(steps) == ["tab-camera-grid"]


def test_back_to_settings_opens_the_general_tab_too() -> None:
    header = (
        WIZARD_TEMPLATE.read_text(encoding="utf-8").split('<div class="wizard-header">', 1)[1].split("</div>", 1)[0]
    )
    link = header[header.index("<a ") : header.index(">", header.index("<a ")) + 1]
    assert 'href="/"' in link
    steps = (
        "store[activeTabKey('/')] = 'camera-grid';"
        f"eval({json.dumps(link.split('onclick="', 1)[1].split('"', 1)[0])});"
        f"location.pathname = '/'; parseHTML({json.dumps(_MAIN_PAGE)}, document.body); initTabs();"
        "out.push(document.querySelector('.tab-content.active').id);"
    )
    assert _run(steps) == ["tab-general"]
