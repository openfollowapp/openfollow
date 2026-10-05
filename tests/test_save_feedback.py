# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Every script-driven save reports a failure the way an HTMX save does."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests._template_source import function_body

pytestmark = pytest.mark.unit

_PACKAGE = Path(__file__).resolve().parent.parent / "openfollow"
_WRITE = re.compile(r"method:\s*['\"](POST|PUT|DELETE|PATCH)['\"]")


def _sources_with_scripts() -> list[Path]:
    return sorted((_PACKAGE / "web" / "templates").rglob("*.tpl")) + sorted(
        (_PACKAGE / "video" / "inputs").glob("*.py")
    )


def test_every_file_that_saves_by_script_reports_failures_through_the_shared_helper() -> None:
    writers = [path for path in _sources_with_scripts() if _WRITE.search(path.read_text(encoding="utf-8"))]
    assert len(writers) >= 8, "the scan no longer finds the script-driven saves"
    silent = [path.name for path in writers if "saveError" not in path.read_text(encoding="utf-8")]
    assert silent == []


def _template(relative: str) -> str:
    return (_PACKAGE / "web" / "templates" / relative).read_text(encoding="utf-8")


def test_a_restart_that_gets_no_answer_says_so() -> None:
    body = function_body(_template("base.tpl"), "confirmRestartApp")
    assert "saveError.UNREACHABLE" in body


def test_a_zone_save_that_saves_nothing_resolves_null() -> None:
    body = function_body(_template("partials/zone_editor.tpl"), "saveSelectedZone")
    assert "Promise.resolve()" not in body


@pytest.mark.parametrize("name", ["duplicateSelectedZone", "onZoneTestSendClick"])
def test_zone_actions_chained_on_a_save_stop_when_it_failed(name: str) -> None:
    body = function_body(_template("partials/zone_editor.tpl"), name)
    assert re.search(r"saveSelectedZone\(\)\.then\(function\s*\((\w+)\)\s*\{\s*if\s*\(!\1\)\s*return", body)


def test_a_template_export_failure_reports_on_the_dialog() -> None:
    body = function_body(_template("base.tpl"), "onExportClick")
    assert "saveError.show(card, await saveError.fromResponse(res), 'Not exported.')" in body
    assert "saveError.show(card, saveError.UNREACHABLE, 'Not exported.')" in body
    assert "showToast" not in body


_NATIVE_DIALOG = re.compile(r"(?<![\w.])(confirm|alert|prompt)\(")


def test_no_template_or_input_plugin_opens_a_native_browser_dialog() -> None:
    offenders = [
        f"{path.name}: {match.group(0)}"
        for path in _sources_with_scripts()
        for match in _NATIVE_DIALOG.finditer(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_every_hx_confirm_names_its_button_and_the_listener_asks_in_the_modal() -> None:
    sources = _sources_with_scripts() + [_PACKAGE / "web" / "routes.py"]
    sites = 0
    for path in sources:
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"hx-confirm=", text):
            sites += 1
            tag = text[match.start() : text.index(">", match.start())]
            assert "data-confirm-label=" in tag, f"{path.name}: hx-confirm without a named button"
    assert sites >= 7, "the scan no longer finds the hx-confirm sites"
    base = _template("base.tpl")
    assert "'htmx:confirm'" in base
    assert "evt.detail.issueRequest(true)" in base


# The actions docs/STATUS_LANGUAGE.md lists under "Destructive actions".
_DESTRUCTIVE_LABELS = ("Delete", "Discard", "Forget", "Import", "Remove", "Restore Defaults", "Restart")


def test_a_confirm_that_stops_or_loses_something_takes_the_danger_button() -> None:
    offenders = []
    for path in _sources_with_scripts() + [_PACKAGE / "web" / "routes.py"]:
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"hx-confirm=", text):
            tag = text[match.start() : text.index(">", match.start())]
            label = re.search(r'data-confirm-label="([^"]*)"', tag)
            if label and label.group(1) in _DESTRUCTIVE_LABELS and "data-confirm-danger" not in tag:
                offenders.append(f"{path.name}: {label.group(1)}")
        for match in re.finditer(r"modalConfirm\(\{(.*?)\}\)", text, re.S):
            label = re.search(r"confirmLabel:\s*['\"]([^'\"]+)", match.group(1))
            if label and label.group(1) in _DESTRUCTIVE_LABELS and "danger: true" not in match.group(1):
                offenders.append(f"{path.name}: {label.group(1)}")
    assert offenders == []
