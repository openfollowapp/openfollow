# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The ruff the pre-commit hook runs must be the ruff ``make lint`` runs.

``pyproject.toml`` pins ruff to an exact version rather than a caret, because a
pre-1.0 patch can add rules or change formatting and produce a reformat diff on
work unrelated to the change at hand. That pin is only half the guarantee: the
``ruff-pre-commit`` hook fetches its own copy at the ``rev`` in
``.pre-commit-config.yaml``, so the two have to move together or a commit that
the local hook accepts fails ``make lint`` in CI, and vice versa.

Nothing else catches the split. ``make lint`` runs poetry's ruff and never reads
the hook config, so both halves of a one-sided bump pass CI on their own - which
is exactly how they arrive, since Dependabot tracks the pip dependency and the
pre-commit repo as unrelated ecosystems and opens a separate PR for each.
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

_RUFF_HOOK_REPO = "https://github.com/astral-sh/ruff-pre-commit"


def _pyproject_ruff_version() -> str:
    pyproject = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    dev = pyproject["tool"]["poetry"]["group"]["dev"]["dependencies"]
    spec = dev["ruff"]
    version = spec["version"] if isinstance(spec, dict) else spec
    assert isinstance(version, str), f"ruff dev dependency is not a version string: {spec!r}"
    assert re.fullmatch(r"\d+\.\d+\.\d+", version), (
        f"ruff is pinned as '{version}', not an exact version. The pin is deliberate: a "
        "pre-1.0 ruff patch can change formatting, so a range would let CI and a "
        "developer's machine disagree about a clean tree."
    )
    return version


def _pre_commit_ruff_rev() -> str:
    doc: dict[str, Any] = yaml.safe_load(_PRE_COMMIT.read_text(encoding="utf-8"))
    revs = [str(repo["rev"]) for repo in doc["repos"] if str(repo.get("repo", "")) == _RUFF_HOOK_REPO]
    assert len(revs) == 1, f"expected exactly one {_RUFF_HOOK_REPO} entry, found {len(revs)}"
    return revs[0]


def test_the_pre_commit_ruff_hook_matches_the_pinned_ruff() -> None:
    pinned = _pyproject_ruff_version()
    rev = _pre_commit_ruff_rev()
    assert rev == f"v{pinned}", (
        f"the ruff-pre-commit hook is at '{rev}' but pyproject pins ruff '{pinned}'. "
        "The local hook and 'make lint' would run different ruff versions, so a commit "
        "one accepts can fail the other. Bump both in the same change."
    )
