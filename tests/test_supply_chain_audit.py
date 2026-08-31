# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Guard tests for the supply-chain audit jobs' dependency closures.

``pip-audit`` reports on what is installed, so an optional extra no audit job
installs is not audited - it reports clean while the lock carries known CVEs,
and nothing else in the tree notices. That is not hypothetical: it is how this
file came to exist.

*Which* audit sees an extra matters as much as whether one does. The gating
audit blocks a release, so it must cover everything a show device installs and
nothing that only ever runs on a build workstation: a CVE in the export
toolchain holding up a Pi release is what gets an audit ignored or deleted.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import tomllib
import yaml

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "ci.yml"
_PYPROJECT = _REPO_ROOT / "pyproject.toml"
_DEB_BUILD = _REPO_ROOT / "packaging" / "build-deb.sh"

_AUDIT_COMMAND = re.compile(r"\b(?:make\s+audit|pip-audit)\b")
_SHELL_SEPARATORS = frozenset({"&&", "||", ";", "|"})


def _strip_comments(run: str) -> str:
    return "\n".join(line.split("#", 1)[0] for line in run.splitlines())


def _shell_commands(run: str) -> list[list[str]]:
    """Every command in a ``run:`` block, tokenised, shell comments dropped.

    Regex over the raw block cannot tell a live install from a commented-out
    one, and only matches whichever flag spelling it was written against.
    """
    commands: list[list[str]] = []
    for line in run.replace("\\\n", " ").splitlines():
        try:
            tokens = shlex.split(line, comments=True)
        except ValueError:  # unbalanced quoting in a step we don't care about
            continue
        current: list[str] = []
        for token in tokens:
            if token in _SHELL_SEPARATORS:
                if current:
                    commands.append(current)
                current = []
            else:
                current.append(token)
        if current:
            commands.append(current)
    return commands


def _poetry_verb(command: list[str]) -> str | None:
    """The subcommand of a ``poetry`` invocation, skipping global flags."""
    if command[:1] != ["poetry"]:
        return None
    return next((token for token in command[1:] if not token.startswith("-")), None)


def _extras_installed(run: str) -> set[str]:
    """Extras a ``run:`` block installs, over every spelling poetry accepts."""
    extras: set[str] = set()
    for command in _shell_commands(run):
        if _poetry_verb(command) not in {"install", "sync"}:
            continue
        expecting = False
        for token in command:
            if expecting:
                extras.add(token)
                expecting = False
            elif token in {"-E", "--extras"}:
                expecting = True
            elif token.startswith("--extras="):
                extras.add(token.split("=", 1)[1])
            elif token.startswith("-E") and len(token) > 2:
                extras.add(token[2:])
    return extras


@dataclass(frozen=True)
class _AuditJob:
    name: str
    gating: bool
    extras: frozenset[str]
    caches_venv: bool


def _audit_jobs() -> list[_AuditJob]:
    """Every ci.yml job that runs pip-audit, with the closure it audits."""
    doc: dict[str, Any] = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    jobs: list[_AuditJob] = []
    for name, job in doc["jobs"].items():
        steps = job.get("steps") or []
        runs = [_strip_comments(str(step.get("run", ""))) for step in steps]
        if not any(_AUDIT_COMMAND.search(run) for run in runs):
            continue
        extras: set[str] = set()
        for run in runs:
            extras |= _extras_installed(run)
        caches_venv = any(
            str(step.get("uses", "")).startswith("actions/cache")
            and ".venv" in str((step.get("with") or {}).get("path", ""))
            for step in steps
        )
        jobs.append(
            _AuditJob(
                name=str(name),
                gating=not bool(job.get("continue-on-error", False)),
                extras=frozenset(extras),
                caches_venv=caches_venv,
            )
        )
    assert jobs, "ci.yml has no job that runs pip-audit"
    return jobs


def _declared_extras() -> list[str]:
    pyproject = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    extras = sorted(pyproject["project"].get("optional-dependencies", {}))
    assert extras, "pyproject declares no optional-dependencies to audit"
    return extras


def _shipped_extras() -> frozenset[str]:
    """Extras the .deb bundles onto a show device, read from its build script."""
    script = _DEB_BUILD.read_text(encoding="utf-8")
    found = set(re.findall(r'pip"?\s+install\s+"\$REPO_ROOT\[([^\]]+)\]"', script))
    extras = {extra.strip() for group in found for extra in group.split(",")}
    assert extras, "no bundled extra found in build-deb.sh - has the install line moved?"
    return frozenset(extras)


@pytest.mark.parametrize("extra", _declared_extras())
def test_every_declared_extra_is_audited_somewhere(extra: str) -> None:
    audited: set[str] = set().union(*(job.extras for job in _audit_jobs()))
    assert extra in audited, (
        f"the '{extra}' extra is installed by no audit job, so pip-audit never sees "
        f"its dependencies - every audit reports clean no matter what CVEs they "
        f"carry. Add '-E {extra}' to an audit job's install step."
    )


def test_extras_the_deb_ships_are_covered_by_a_gating_audit() -> None:
    gated: set[str] = set()
    for job in _audit_jobs():
        if job.gating:
            gated |= job.extras
    missing = _shipped_extras() - gated
    assert not missing, (
        f"{sorted(missing)} ships to show devices in the .deb but is audited only by a "
        "continue-on-error job, so a CVE in it no longer blocks a release. Being "
        "audited somewhere is not enough - a shipped extra has to gate."
    )


def test_extras_the_deb_does_not_ship_never_gate_a_release() -> None:
    workstation_only = set(_declared_extras()) - _shipped_extras()
    for job in _audit_jobs():
        if not job.gating:
            continue
        leaked = workstation_only & job.extras
        assert not leaked, (
            f"the gating audit job '{job.name}' installs {sorted(leaked)}, which no show "
            "device ever loads. A CVE in build-host-only tooling would block every "
            "release - install it in a continue-on-error job instead."
        )


def test_the_workstation_closure_is_never_saved_into_a_venv_cache() -> None:
    workstation_only = set(_declared_extras()) - _shipped_extras()
    for job in _audit_jobs():
        if not (workstation_only & job.extras):
            continue
        assert not job.caches_venv, (
            f"audit job '{job.name}' installs {sorted(workstation_only & job.extras)} and "
            "caches '.venv'. The export closure is multiple GB: it would evict every "
            "other job's cache by LRU, and any job restoring it installs with a poetry "
            "'install' that computes no uninstalls, carrying the surplus forward."
        )
