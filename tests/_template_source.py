# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Reading the scripts inside templates, for tests that pin their order."""

from __future__ import annotations

from pathlib import Path

_BASE = Path(__file__).parent.parent / "openfollow" / "web" / "templates" / "base.tpl"


def base_functions(*names: str) -> str:
    """The named ``function name(...) { ... }`` declarations from base.tpl's page script, in order."""
    text = _BASE.read_text(encoding="utf-8")
    parts = []
    for name in names:
        start = text.index(f"function {name}(")
        parts.append(text[start : text.index("{", start)] + "{" + function_body(text, name) + "}")
    return "\n".join(parts)


def function_body(source: str, name: str) -> str:
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


def reorder_script() -> str:
    """The shared drag-reorder listeners from base.tpl's page script."""
    text = _BASE.read_text(encoding="utf-8")
    start = text.index("// Generic drag-reorder shared by")
    return text[start : text.index("document.addEventListener('DOMContentLoaded'", start)]


def restart_script() -> str:
    """The shared restart flow from base.tpl's page script: progress view, wait and Restart buttons."""
    text = _BASE.read_text(encoding="utf-8")
    start = text.index("// Every restart shows one locked progress view")
    return text[start : text.index("// Save to USB storage device", start)]
