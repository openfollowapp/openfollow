# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The toast: how long it stays, how it goes, its caution level, and the one
that outlives a reload."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests._template_source import function_body

pytestmark = pytest.mark.unit

_BASE = Path(__file__).resolve().parent.parent / "openfollow" / "web" / "templates" / "base.tpl"


def _base() -> str:
    return _BASE.read_text(encoding="utf-8")


def _rule(selector: str) -> str:
    """The declarations of the CSS rule whose selector is exactly ``selector``."""
    match = re.search(r"\n\s*" + re.escape(selector) + r"\s*\{([^{}]*)\}", _base())
    assert match, selector
    return match.group(1)


class TestToast:
    def test_it_stays_ten_seconds(self) -> None:
        assert "const TOAST_MS = 10000;" in _base()
        assert "_toastTimer = setTimeout(hideToast, TOAST_MS);" in function_body(_base(), "showToast")

    def test_a_new_toast_gets_the_whole_time(self) -> None:
        body = function_body(_base(), "showToast")
        assert body.index("clearTimeout(_toastTimer);") < body.index("_toastTimer = setTimeout(hideToast, TOAST_MS);")

    def test_a_click_closes_it(self) -> None:
        assert '<div id="toast" class="toast" onclick="hideToast()"></div>' in _base()
        body = function_body(_base(), "hideToast")
        assert "clearTimeout(_toastTimer);" in body
        assert "toast.classList.remove('show');" in body

    def test_a_hidden_toast_lets_clicks_through(self) -> None:
        assert "pointer-events: none;" in _rule(".toast")
        assert "pointer-events: auto;" in _rule(".toast.show")

    def test_the_level_is_set_on_every_toast(self) -> None:
        # Toggled, never only added: a confirmation after a caution must not stay amber.
        body = function_body(_base(), "showToast")
        assert "toast.classList.toggle('toast-caution', level === 'caution');" in body

    def test_the_caution_toast_takes_the_caution_colours_and_the_i_sign(self) -> None:
        rule = _rule(".toast.toast-caution")
        assert "border-color: var(--caution-border);" in rule
        assert "linear-gradient(var(--caution-fill), var(--caution-fill))" in rule
        assert "background-image: var(--info-sign);" in _rule(".toast.toast-caution::before")

    def test_the_missing_backup_sentence_names_no_reason(self) -> None:
        assert "const NO_BACKUP_MADE = 'No backup was made. Details in Logs.';" in _base()


class TestToastOnNextLoad:
    def test_the_message_and_its_level_are_stored(self) -> None:
        body = function_body(_base(), "toastOnNextLoad")
        stored = body.index("sessionStorage.setItem(_TOAST_AFTER_RELOAD_KEY, JSON.stringify({ message, level }));")
        assert stored < body.index("return true;")

    def test_without_storage_it_is_shown_now(self) -> None:
        body = function_body(_base(), "toastOnNextLoad")
        fallback = body[body.index("} catch (err) {") :]
        assert fallback.index("showToast(message, level);") < fallback.index("return false;")

    def test_the_next_page_shows_it_once_with_its_level(self) -> None:
        base = _base()
        start = base.index("const stored = sessionStorage.getItem(_TOAST_AFTER_RELOAD_KEY);")
        listener = base[start : base.index("if (pending) showToast(pending.message, pending.level);", start)]
        # Removed before parsing, so a value that doesn't parse is not read again on every load.
        assert listener.index("sessionStorage.removeItem(_TOAST_AFTER_RELOAD_KEY);") < listener.index(
            "pending = JSON.parse(stored || 'null');"
        )
        # Storage that throws on read must not take the page's scripts down.
        assert "} catch (err) {\n return;\n }" in listener


class TestToastAfterReload:
    def test_a_stored_toast_reloads_at_once(self) -> None:
        body = function_body(_base(), "toastAfterReload")
        stored = body[: body.index("} else if")]
        assert "if (toastOnNextLoad(message, level)) {" in stored
        assert "window.location.reload();" in stored

    def test_shown_now_a_confirmation_still_reloads_and_a_caution_keeps_the_page(self) -> None:
        body = function_body(_base(), "toastAfterReload")
        shown_now = body[body.index("} else if") :]
        assert "} else if (level !== 'caution') {" in shown_now
        assert "setTimeout(() => window.location.reload(), 600);" in shown_now
