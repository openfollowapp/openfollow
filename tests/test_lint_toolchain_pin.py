# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The ruff and bandit the pre-commit hooks run must be the ones ``make`` runs.

A ``ruff-pre-commit`` or ``bandit`` hook fetched from its own repo carries a
``rev:`` of its own, which is a second pin of a tool ``pyproject.toml`` already
pins. The two then have to move together or a commit the hook accepts fails the
gate in CI, and vice versa: ``make lint`` for ruff, ``make security`` for
bandit.

Nothing keeps them together. Both gates run the poetry-installed tool and
neither reads the hook config, so both halves of a one-sided bump pass CI on
their own, and one-sided is how they arrive: Dependabot tracks the pip
dependency and the pre-commit repo as unrelated ecosystems and opens a separate
PR for each, which it cannot group because grouping works only within one
ecosystem.

So these hooks run out of the poetry env instead and there is only one pin.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import tomllib
import yaml

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PYPROJECT = _REPO_ROOT / "pyproject.toml"
_PRE_COMMIT = _REPO_ROOT / ".pre-commit-config.yaml"

# hook id -> the dev dependency whose version it would otherwise re-pin.
_POETRY_MANAGED_HOOKS = {
    "ruff-check": "ruff",
    "ruff-format": "ruff",
    "bandit": "bandit",
}


def _pre_commit_doc() -> dict[str, Any]:
    doc: dict[str, Any] = yaml.safe_load(_PRE_COMMIT.read_text(encoding="utf-8"))
    return doc


def _hooks_by_id() -> dict[str, list[tuple[str, dict[str, Any]]]]:
    """Map each hook id to the ``(repo, hook)`` pairs declaring it."""
    found: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for repo in _pre_commit_doc()["repos"]:
        source = str(repo.get("repo", ""))
        for hook in repo.get("hooks", []):
            found.setdefault(str(hook["id"]), []).append((source, hook))
    return found


def test_ruff_is_pinned_to_an_exact_version() -> None:
    pyproject = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    spec = pyproject["tool"]["poetry"]["group"]["dev"]["dependencies"]["ruff"]
    version = spec["version"] if isinstance(spec, dict) else spec
    assert isinstance(version, str), f"ruff dev dependency is not a version string: {spec!r}"
    assert re.fullmatch(r"\d+\.\d+\.\d+", version), (
        f"ruff is pinned as '{version}', not an exact version. The pin is deliberate: a "
        "pre-1.0 ruff patch can change formatting, so a range would let CI and a "
        "developer's machine disagree about a clean tree."
    )


@pytest.mark.parametrize(("hook_id", "tool"), sorted(_POETRY_MANAGED_HOOKS.items()))
def test_the_hook_runs_the_tool_poetry_installed(hook_id: str, tool: str) -> None:
    declarations = _hooks_by_id().get(hook_id, [])
    assert len(declarations) == 1, f"expected exactly one '{hook_id}' hook, found {len(declarations)}"
    source, hook = declarations[0]
    assert source == "local", (
        f"'{hook_id}' comes from '{source}', which pins its own {tool} at that repo's rev. "
        f"pyproject already pins {tool}, and the two can only be bumped in separate "
        "Dependabot PRs that each fail with the other's half missing. Run it from the "
        "poetry env instead."
    )
    assert hook.get("language") == "system", (
        f"'{hook_id}' is local but does not use language: system, so pre-commit builds it "
        f"an isolated env and installs its own {tool} rather than using poetry's."
    )
    assert str(hook.get("entry", "")).startswith(f"poetry run {tool}"), (
        f"'{hook_id}' does not run 'poetry run {tool}', so it can resolve to a {tool} "
        f"outside the project env: entry is {hook.get('entry')!r}"
    )


def test_no_remote_hook_repo_supplies_a_poetry_managed_tool() -> None:
    offenders = {
        hook_id: source
        for hook_id, declarations in _hooks_by_id().items()
        for source, _hook in declarations
        if hook_id in _POETRY_MANAGED_HOOKS and source != "local"
    }
    assert not offenders, (
        f"these hooks bring their own copy of a tool pyproject pins: {offenders}. Each adds "
        "a second version to keep in sync with no way to bump both at once."
    )
