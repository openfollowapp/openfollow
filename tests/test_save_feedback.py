# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Every script-driven save reports a failure the way an HTMX save does."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

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


def _function_body(source: str, name: str) -> str:
    """The body of ``function name(...) { ... }``, matched to its closing brace."""
    start = source.index(f"function {name}(")
    opening = source.index("{", start)
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[opening + 1 : index]
    raise AssertionError(f"{name} has no closing brace")


def _template(relative: str) -> str:
    return (_PACKAGE / "web" / "templates" / relative).read_text(encoding="utf-8")


def test_a_restart_that_gets_no_answer_says_so() -> None:
    body = _function_body(_template("base.tpl"), "confirmRestartApp")
    assert "saveError.UNREACHABLE" in body


def test_a_zone_save_that_saves_nothing_resolves_null() -> None:
    body = _function_body(_template("partials/zone_editor.tpl"), "saveSelectedZone")
    assert "Promise.resolve()" not in body


@pytest.mark.parametrize("name", ["duplicateSelectedZone", "onZoneTestSendClick"])
def test_zone_actions_chained_on_a_save_stop_when_it_failed(name: str) -> None:
    body = _function_body(_template("partials/zone_editor.tpl"), name)
    assert re.search(r"saveSelectedZone\(\)\.then\(function\s*\((\w+)\)\s*\{\s*if\s*\(!\1\)\s*return", body)


def test_a_template_export_failure_reports_on_the_dialog() -> None:
    body = _function_body(_template("base.tpl"), "onExportClick")
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
