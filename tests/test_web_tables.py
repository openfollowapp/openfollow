# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Every table in the web UI looks the same: the shared ``.data-table`` style."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_PACKAGE = Path(__file__).resolve().parent.parent / "openfollow"
_TEMPLATES = _PACKAGE / "web" / "templates"


def _sources() -> list[Path]:
    return [
        *sorted(_TEMPLATES.rglob("*.tpl")),
        *sorted((_PACKAGE / "web").glob("*.py")),
        *sorted((_PACKAGE / "video" / "inputs").glob("*.py")),
    ]


def _css_rules() -> dict[str, str]:
    base = (_TEMPLATES / "base.tpl").read_text(encoding="utf-8")
    css = re.sub(r"/\*.*?\*/", "", "".join(re.findall(r"<style[^>]*>(.*?)</style>", base, re.S)), flags=re.S)
    return {" ".join(sel.split()): body for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css)}


def test_every_table_uses_the_shared_style() -> None:
    tables = [
        (path.name, tag)
        for path in _sources()
        for tag in re.findall(r"<table\b[^>]*>", path.read_text(encoding="utf-8"))
    ]
    assert len(tables) >= 5, "the scan no longer finds the tables"
    assert [t for t in tables if "data-table" not in t[1] or "style=" in t[1]] == []


def test_no_cell_sets_its_own_layout() -> None:
    offenders = [
        f"{path.name}: {tag}"
        for path in _sources()
        for tag in re.findall(
            r"<t[hd]\b[^>]*style=\"[^\"]*(?:padding|font-size|border)[^>]*>", path.read_text(encoding="utf-8")
        )
    ]
    assert offenders == []


def test_the_shared_style_centres_cells_mutes_headers_and_puts_actions_right() -> None:
    rules = _css_rules()
    assert "font-size: var(--btn-font-sm)" in rules[".data-table"]
    assert "vertical-align: middle" in rules[".data-table th, .data-table td"]
    assert "color: var(--muted)" in rules[".data-table thead th"]
    assert "text-align: right" in rules[".data-table td.row-actions"]


@pytest.mark.parametrize("name", ["controller_slots_table.tpl", "midi.tpl", "marker.tpl"])
def test_the_buttons_in_a_row_are_small(name: str) -> None:
    text = next(_TEMPLATES.rglob(name)).read_text(encoding="utf-8")
    cells = re.findall(r"class=\"row-actions\">(.*?)</td>", text, re.S)
    buttons = [b for cell in cells for b in re.findall(r"<button\b[^>]*class=\"([^\"]+)\"", cell)]
    assert buttons, f"{name}: no row actions found"
    assert [b for b in buttons if "small" not in b.split()] == []
