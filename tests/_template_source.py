# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Reading the scripts inside templates, for tests that pin their order."""

from __future__ import annotations


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
