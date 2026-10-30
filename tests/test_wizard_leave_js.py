# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Leaving the Setup Wizard, run in node: out of it and onto the main page's General tab."""

from __future__ import annotations

import json
import subprocess  # nosec B404 - runs the local node binary on a fixed script
from pathlib import Path

import pytest

from tests._template_source import base_functions
from tests._wizard_js import NODE, SETTINGS_URL, WIZARD_TEMPLATE, needs_node, wizard_var, wizard_window_function

pytestmark = [pytest.mark.unit, needs_node]

_TEMPLATES = Path(__file__).parents[1] / "openfollow" / "web" / "templates"

_MAIN_PAGE = (
    '<button class="tab-btn" data-tab="overview">O</button><button class="tab-btn" data-tab="general">G</button>'
    '<button class="tab-btn" data-tab="camera-grid">C</button>'
    '<div class="tab-content" id="tab-overview"></div><div class="tab-content" id="tab-general"></div>'
    '<div class="tab-content" id="tab-camera-grid"></div>'
)


def _run(steps: str, *, storage: str = "true") -> object:
    """``steps`` in a browser whose storage works when ``storage`` is true and throws when false."""
    source = "\n".join(
        [
            (Path(__file__).parent / "_fake_dom.js").read_text(encoding="utf-8"),
            f"var storageWorks = {storage}; var store = {{}};",
            "var localStorage = {"
            " getItem: function (k) { if (!storageWorks) throw new Error('blocked');"
            " return k in store ? store[k] : null; },"
            " setItem: function (k, v) { if (!storageWorks) throw new Error('blocked'); store[k] = String(v); }};",
            "var session = {}; var sessionStorage = {removeItem: function (k) { delete session[k]; }};",
            "var replaced;",
            "var history = {replaceState: function (s, t, url) {"
            " var q = url.split('#')[0].split('?')[1]; location.search = q ? '?' + q : ''; replaced = url; } };",
            "function closeHelp() {} function announcePollsShown() {}",
            "var location = {pathname: '/wizard', search: '', hash: ''}; var window = {location: {href: '/wizard'}};",
            base_functions("readStorage", "writeStorage", "switchTab", "initTabs"),
            "var out = [];",
            steps,
            "process.stdout.write(JSON.stringify(out));",
        ]
    )
    assert NODE is not None
    # On stdin: Linux caps one argument at 128 KiB.
    done = subprocess.run([NODE, "-"], input=source, capture_output=True, text=True, timeout=60)  # nosec B603
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _open_main_page(url: str) -> str:
    """The main page loading at ``url``, then the tab it shows."""
    path, _, query = url.partition("?")
    return (
        f"location.pathname = {json.dumps(path)}; location.search = {json.dumps('?' + query if query else '')};"
        f"parseHTML({json.dumps(_MAIN_PAGE)}, document.body); initTabs();"
        "out.push(document.querySelector('.tab-content.active').id);"
    )


_LEAVE = (
    # The confirm's red button, as discardAndLeave hands it to the dialog.
    wizard_var("STORAGE_KEY") + "session[STORAGE_KEY] = '{}'; var dialog;"
    "function openModal(opts) { dialog = opts; } function closeModal() {}"
    + wizard_window_function("discardAndLeave")
    + "window.discardAndLeave();"
    "dialog.footerButtons.filter(function (b) { return b.kind === 'danger'; })[0].onClick();"
    "out.push(window.location.href, STORAGE_KEY in session);"
)


def _back_to_settings() -> str:
    header = (
        WIZARD_TEMPLATE.read_text(encoding="utf-8").split('<div class="wizard-header">', 1)[1].split("</div>", 1)[0]
    )
    link = header[header.index("<a ") : header.index(">", header.index("<a ")) + 1]
    assert "onclick" not in link
    return link.split('href="', 1)[1].split('"', 1)[0].replace("{{_settings_url}}", SETTINGS_URL)


@pytest.mark.parametrize("storage", ["true", "false"], ids=["storage", "storage-blocked"])
def test_leaving_without_saving_opens_the_main_page_on_the_general_tab(storage: str) -> None:
    steps = "store['psnfs:active-tab:/'] = 'camera-grid';" + _LEAVE
    steps += _open_main_page("/?tab=general") + "out.push(location.search, replaced);"
    assert _run(steps, storage=storage) == ["/?tab=general", False, "tab-general", "", "/"]


def test_back_to_settings_opens_the_general_tab_too() -> None:
    url = _back_to_settings()
    assert url == "/?tab=general"
    assert _run("store['psnfs:active-tab:/'] = 'camera-grid';" + _open_main_page(url)) == ["tab-general"]


def test_without_a_named_tab_the_main_page_opens_where_it_was_left() -> None:
    steps = "store['psnfs:active-tab:/'] = 'camera-grid';" + _open_main_page("/") + "out.push(replaced === undefined);"
    assert _run(steps) == ["tab-camera-grid", True]


def test_the_main_page_has_the_tab_the_wizard_names() -> None:
    tab = SETTINGS_URL.split("?tab=", 1)[1]
    index = (_TEMPLATES / "index.tpl").read_text(encoding="utf-8")
    assert f'data-tab="{tab}"' in index
    assert f'<div class="tab-content" id="tab-{tab}">' in index


@pytest.mark.parametrize("asked", ["detection", "nowhere"], ids=["feature-off", "unknown"])
def test_a_named_tab_that_is_not_shown_opens_the_remembered_one(asked: str) -> None:
    page = _MAIN_PAGE + '<div class="tab-content" id="tab-detection" data-feature="detection"></div>'
    steps = (
        "store['psnfs:active-tab:/'] = 'camera-grid';"
        f"location.pathname = '/'; location.search = '?tab={asked}';"
        f"parseHTML({json.dumps(page)}, document.body); initTabs();"
        "out.push(document.querySelector('.tab-content.active').id);"
    )
    assert _run(steps) == ["tab-camera-grid"]


def test_only_the_tab_parameter_leaves_the_address() -> None:
    steps = "location.hash = '#x';" + _open_main_page("/?keep=1&tab=general") + "out.push(replaced);"
    assert _run(steps) == ["tab-general", "/?keep=1#x"]


def test_with_storage_blocked_a_named_tab_not_shown_opens_the_first() -> None:
    steps = "store['psnfs:active-tab:/'] = 'camera-grid';" + _open_main_page("/?tab=nowhere")
    assert _run(steps, storage="false") == ["tab-overview"]


def test_a_named_element_that_is_not_a_tab_is_not_opened() -> None:
    page = _MAIN_PAGE + '<div id="tab-strip"></div>'
    steps = (
        "store['psnfs:active-tab:/'] = 'camera-grid';"
        f"location.pathname = '/'; location.search = '?tab=strip'; parseHTML({json.dumps(page)}, document.body);"
        "initTabs(); out.push(document.querySelector('.tab-content.active').id);"
    )
    assert _run(steps) == ["tab-camera-grid"]


def test_the_landing_url_opens_the_general_tab_over_a_remembered_one() -> None:
    # Save and finish (test_wizard.TestWizardApply), Leave without saving and Back to Settings go here.
    assert SETTINGS_URL == "/?tab=general"
    assert _run("store['psnfs:active-tab:/'] = 'camera-grid';" + _open_main_page(SETTINGS_URL)) == ["tab-general"]
