# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The Configuration section's import: one control, one question, and a
confirmation that outlives the reload it ends in."""

from __future__ import annotations

import pytest
from bottle import template

from openfollow.web import server as _server_module  # noqa: F401  (registers the template path)
from tests._template_source import function_body

pytestmark = pytest.mark.unit


def _page() -> str:
    return str(template("partials/config_transfer"))


def _group(page: str, title: str) -> str:
    start = page.index(f'<h3 class="group-title">{title}</h3>')
    return page[start : page.index('<div class="group">', start)]


class TestImportControl:
    def test_one_button_opens_the_picker_and_the_choice_starts_the_import(self) -> None:
        group = _group(_page(), "Import")
        assert group.count("<button") == 1
        assert (
            "onclick=\"document.getElementById('config-import-file').click()\">Import Configuration&hellip;</button>"
            in group
        )
        assert 'onchange="importConfig(this)"' in group

    @pytest.mark.parametrize("title", ["Export", "Import"])
    def test_the_group_carries_no_inline_help(self, title: str) -> None:
        group = _group(_page(), title)
        assert "<p" not in group
        assert "<label" not in group

    def test_cancelling_the_picker_does_nothing(self) -> None:
        body = function_body(_page(), "importConfig")
        cancelled = body.index("if (!file) return;")
        assert cancelled < body.index("input.value = '';")
        assert cancelled < body.index("reader.readAsText(file);")

    def test_the_same_file_can_be_chosen_again_after_a_failure(self) -> None:
        # A file input only fires change when its value changes.
        body = function_body(_page(), "importConfig")
        assert body.index("input.value = '';") < body.index("reader.readAsText(file);")

    def test_a_file_that_is_not_json_fails_before_the_question(self) -> None:
        body = function_body(_page(), "importConfig")
        refused = body.index("_importFailed({error: 'The selected file is not valid JSON.'});")
        assert refused < body.index("modalConfirm(")

    def test_nothing_is_sent_until_the_import_is_confirmed(self) -> None:
        body = function_body(_page(), "importConfig")
        asked = body.index("modalConfirm(")
        declined = body.index("if (!ok) return;")
        sent = body.index("_sendImport(raw, file.name);")
        assert asked < declined < sent

    def test_the_question_names_the_file_and_takes_the_danger_button(self) -> None:
        body = function_body(_page(), "importConfig")
        start = body.index("modalConfirm({")
        question = body[start : body.index("});", start)]
        assert "title: 'Import configuration?'" in question
        assert "' + file.name + '" in question
        assert "including the station '\n                + 'name." in question
        assert "confirmLabel: 'Import'" in question
        assert "danger: true" in question


class TestImportProgress:
    def test_the_button_names_the_file_while_it_imports(self) -> None:
        body = function_body(_page(), "_sendImport")
        busy = body.index("btn.textContent = 'Importing ' + name + '\\u2026';")
        assert body.index("btn.disabled = true;") < busy < body.index("fetch('/api/config/import'")

    def test_a_failed_import_gives_the_button_back(self) -> None:
        page = _page()
        assert "var _IMPORT_LABEL = 'Import Configuration\\u2026';" in page
        body = function_body(page, "_sendImport")
        refused = body[body.index("if (!res.ok || result.error) {") :]
        refused = refused[: refused.index("return;")]
        unreachable = body[body.index(".catch(function() {") :]
        for branch in (refused, unreachable):
            assert "btn.disabled = false;" in branch
            assert "btn.textContent = _IMPORT_LABEL;" in branch

    def test_success_is_confirmed_on_the_reloaded_page(self) -> None:
        body = function_body(_page(), "_sendImport")
        assert "toastAfterReload('Imported ' + name);" in body
        assert "location.reload" not in body


class TestBackupOutcome:
    _SAVED_FIRST = "A backup of '\n            + 'the current settings is saved on the station first."

    def test_the_import_question_says_a_backup_is_saved_first(self) -> None:
        body = function_body(_page(), "importConfig")
        assert "paths. A backup of the current settings is saved on the station first.'," in body

    def test_the_restore_question_says_a_backup_is_saved_first(self) -> None:
        body = function_body(_page(), "restoreDefaults")
        assert self._SAVED_FIRST in body

    def test_an_import_without_a_backup_is_confirmed_in_caution(self) -> None:
        body = function_body(_page(), "_sendImport")
        missing = body[body.index("if (result.backup_error) {") :]
        missing = missing[: missing.index("return;")]
        assert "toastAfterReload('Imported ' + name + '. ' + NO_BACKUP_MADE, 'caution');" in missing
        # The page stays when the toast can't outlive a reload, so the button is ready again first.
        assert missing.index("btn.disabled = false;") < missing.index("toastAfterReload(")
        assert missing.index("btn.textContent = _IMPORT_LABEL;") < missing.index("toastAfterReload(")

    def test_an_import_with_a_backup_is_confirmed_as_before(self) -> None:
        body = function_body(_page(), "_sendImport")
        after = body[body.index("if (result.backup_error) {") :]
        after = after[after.index("return;") :]
        assert "toastAfterReload('Imported ' + name);" in after

    def test_a_restore_without_a_backup_is_confirmed_in_caution_after_the_restart(self) -> None:
        body = function_body(_page(), "restoreDefaults")
        checked = body.index("if (result.backup_error) {")
        noted = body.index("toastOnNextLoad('Restored defaults. ' + NO_BACKUP_MADE, 'caution');")
        assert checked < noted < body.index("_showRestartingState();")
