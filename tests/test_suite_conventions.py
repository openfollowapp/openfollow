# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Convention check: a test must not bail out with a bare ``return``.

An early ``return`` inside a test reports as a **pass**. It is indistinguishable
in the gate output from a test that ran every assertion, so a guard that stops
being true - a plugin that gains a field, a fixture that starts returning
something else - silently converts the test into a no-op that still reports
green. ``pytest.skip(reason)`` expresses the same "not applicable" and is
counted separately, so the suite line (``9897 passed, 11 skipped``) shows it.

This is not hypothetical. Three parametrized tests in the video-input suite
guarded on a plugin attribute and returned; one of them was exempting a whole
category of plugin from the rule it existed to enforce, and neither coverage nor
the assertion count could show it - the test reported as passed either way.

Only returns belonging to the test function itself count. A nested helper or a
fake class method (``def join(self): return``) is ordinary code and is ignored,
which is why this walks the body rather than using ``ast.walk``.

Hypothesis tests are exempt: ``@given`` runs the body once per example and a
``return`` discards that example, which is a per-example filter rather than a
whole-test bail-out. ``pytest.skip`` cannot express that - it would abort the
entire test on the first uninteresting example.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

pytestmark = pytest.mark.unit

TESTS_DIR = pathlib.Path(__file__).parent

# Decorator names that mark a Hypothesis-driven test, matched on the final
# attribute so both ``given`` and ``hypothesis.given`` are recognised.
_HYPOTHESIS_DECORATORS = {"given"}


def _own_bare_returns(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.Return]:
    """Bare ``return`` statements in *fn*'s own body, not in a nested scope."""
    found: list[ast.Return] = []

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
                continue
            if isinstance(child, ast.Return) and child.value is None:
                found.append(child)
            visit(child)

    visit(fn)
    return found


def _is_hypothesis_test(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for decorator in fn.decorator_list:
        name = ast.unparse(decorator).split("(")[0].split(".")[-1]
        if name in _HYPOTHESIS_DECORATORS:
            return True
    return False


def test_no_test_bails_out_with_a_bare_return() -> None:
    offenders: list[str] = []

    for path in sorted(TESTS_DIR.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if not node.name.startswith("test_") or _is_hypothesis_test(node):
                continue
            for statement in _own_bare_returns(node):
                offenders.append(f"{path.name}:{statement.lineno} {node.name}")

    assert not offenders, (
        "These tests bail out with a bare return, which reports as a pass and hides a test "
        "that asserted nothing. Use pytest.skip(reason) so the gate counts it:\n  " + "\n  ".join(offenders)
    )
