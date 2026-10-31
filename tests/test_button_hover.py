# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Michel Honold and Paul Hermann
"""Buttons stay where they are under the pointer: no hover rule in the web templates moves one."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_TEMPLATES = Path(__file__).parents[1] / "openfollow" / "web" / "templates"
_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")


def _hover_rules() -> list[tuple[str, str, str]]:
    rules = []
    for path in sorted(_TEMPLATES.rglob("*.tpl")):
        for css in re.findall(r"<style[^>]*>(.*?)</style>", path.read_text(encoding="utf-8"), re.S):
            for selector, body in _RULE.findall(css):
                if ":hover" in selector:
                    rules.append((path.name, " ".join(selector.split()), body))
    return rules


def test_the_templates_have_hover_rules_to_check() -> None:
    assert any("button:hover" in selector for _, selector, _ in _hover_rules())


def test_no_hover_rule_moves_an_element() -> None:
    moving = [(name, selector) for name, selector, body in _hover_rules() if "translate" in body]
    assert moving == []


def test_buttons_animate_no_transform() -> None:
    base = (_TEMPLATES / "base.tpl").read_text(encoding="utf-8")
    transition = re.search(r"--btn-transition:([^;]+);", base)
    assert transition is not None
    assert "transform" not in transition.group(1)
