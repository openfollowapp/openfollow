# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Guard tests for the supply-chain audit job's dependency closure.

``pip-audit`` reports on what is installed, so an optional extra the audit job
never installs is not audited - it reports clean while the lock carries known
CVEs. Dependabot does not cover the gap either: it opens version PRs only for
packages ``pyproject.toml`` names, so a vulnerable transitive dependency of an
un-audited extra is reported by neither guard.
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
_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "ci.yml"
_PYPROJECT = _REPO_ROOT / "pyproject.toml"


def _audit_job() -> dict[str, Any]:
    doc = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    job = doc["jobs"].get("audit")
    assert job, "ci.yml has no 'audit' job"
    return dict(job)


def _install_steps() -> list[tuple[str, str]]:
    """``(step name, run command)`` for every poetry install/sync in the audit job."""
    steps = []
    for step in _audit_job()["steps"]:
        run = str(step.get("run", ""))
        if re.search(r"\bpoetry\s+(install|sync)\b", run):
            steps.append((str(step.get("name", "")), run))
    return steps


def _declared_extras() -> list[str]:
    pyproject = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    extras = list(pyproject["project"].get("optional-dependencies", {}))
    assert extras, "pyproject declares no optional-dependencies to audit"
    return extras


@pytest.mark.parametrize("extra", _declared_extras())
def test_every_declared_extra_is_installed_somewhere_in_the_audit_job(extra: str) -> None:
    commands = [run for _, run in _install_steps()]
    assert any(re.search(rf"-E\s+{re.escape(extra)}\b", run) for run in commands), (
        f"the '{extra}' extra is never installed in the audit job, so pip-audit "
        f"never sees its dependencies - the job reports clean no matter what CVEs "
        f"they carry. Add '-E {extra}' to an install step."
    )


def test_first_audit_install_prunes_when_a_later_step_widens_the_closure() -> None:
    steps = _install_steps()
    assert len(steps) >= 1, "audit job installs nothing"
    if len(steps) == 1:
        return
    first_name, first_run = steps[0]
    assert re.search(r"\bpoetry\s+sync\b", first_run), (
        f"'{first_name}' must use 'poetry sync', not 'poetry install': a later step "
        "widens this same venv and the venv cache is saved afterwards, so on a cache "
        "hit the wider closure would be restored into the gating audit and fail it on "
        "a CVE that does not gate a release."
    )
