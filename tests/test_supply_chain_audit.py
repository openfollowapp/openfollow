# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Paul Hermann and Michel Honold
"""Guard tests for the supply-chain audit jobs' dependency closures.

``pip-audit`` reports on what is installed, so an optional extra no audit job
installs is not audited - it reports clean while the lock carries known CVEs,
and nothing else in the tree notices. That is not hypothetical: it is how this
file came to exist.

*Which* audit sees an extra matters as much as whether one does. The gating
audit blocks a release, so it must cover every extra a show device installs.

It deliberately does not stop there: the ``dev`` group is in that closure too,
because ruff, pytest and bandit execute inside the release pipeline itself, so a
compromise there reaches what ships. The line is drawn at code that neither runs
on a device nor takes part in producing the artifact - today that is the
``export`` extra, several GB of ML tooling the .deb build never installs. A CVE
in *that* holding up a Pi release is what gets an audit ignored or deleted.
"""

from __future__ import annotations

import functools
import re
import shlex
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml
from packaging.markers import Marker

from tests._gha_expr import evaluate

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "ci.yml"
_PYPROJECT = _REPO_ROOT / "pyproject.toml"
_DEB_BUILD = _REPO_ROOT / "packaging" / "build-deb.sh"
_LOCK = _REPO_ROOT / "poetry.lock"

_AUDIT_COMMAND = re.compile(r"\b(?:make\s+audit|pip-audit)\b")
_SHELL_SEPARATORS = frozenset({"&&", "||", ";", "|"})
# Whether an audit gates is judged where its verdict counts: main and the weekly run.
_GATING_EVENTS = ("push", "schedule")


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


def _option_values(run: str, names: set[str]) -> list[str]:
    """Values a poetry install/sync in ``run`` passes to ``names``, over every spelling poetry accepts."""
    values: list[str] = []
    for command in _shell_commands(run):
        if _poetry_verb(command) not in {"install", "sync"}:
            continue
        expecting = False
        for token in command:
            if expecting:
                values.append(token)
                expecting = False
            elif token in names:
                expecting = True
            else:
                for name in names:
                    if name.startswith("--") and token.startswith(f"{name}="):
                        values.append(token.split("=", 1)[1])
                    elif not name.startswith("--") and token.startswith(name) and len(token) > len(name):
                        values.append(token[len(name) :])
    return values


def _extras_installed(run: str) -> set[str]:
    """Extras a ``run:`` block installs; poetry splits one ``-E`` value on whitespace."""
    return {extra for value in _option_values(run, {"-E", "--extras"}) for extra in value.split()}


def _groups_installed(run: str) -> set[str]:
    """Optional Poetry groups a ``run:`` block adds with ``--with``."""
    groups = (group.strip() for value in _option_values(run, {"--with"}) for group in value.split(","))
    return {group for group in groups if group}


def _install_flags(run: str) -> set[str]:
    """Option flags passed to a poetry install/sync, ``--opt=value`` normalised."""
    flags: set[str] = set()
    for command in _shell_commands(run):
        if _poetry_verb(command) not in {"install", "sync"}:
            continue
        flags |= {token.split("=", 1)[0] for token in command if token.startswith("-")}
    return flags


def _gates(job: dict[str, Any], audit_steps: list[dict[str, Any]]) -> bool:
    """Whether a failed audit fails the job on every gating event.

    A ``continue-on-error`` on the audit step itself is as non-gating as one on
    the job: the job goes green with the CVE verdict discarded.
    """
    for event in _GATING_EVENTS:
        context = {"github.event_name": event}
        if evaluate(job.get("continue-on-error", False), context):
            return False
        if any(evaluate(step.get("continue-on-error", False), context) for step in audit_steps):
            return False
    return True


def _caches_venv(steps: list[dict[str, Any]]) -> bool:
    return any(
        str(step.get("uses", "")).startswith("actions/cache")
        and ".venv" in str((step.get("with") or {}).get("path", ""))
        for step in steps
    )


@dataclass(frozen=True)
class _AuditJob:
    name: str
    gating: bool
    extras: frozenset[str]
    groups: frozenset[str]
    caches_venv: bool
    install_flags: frozenset[str]
    runners: tuple[str, ...]

    @property
    def on_macos(self) -> bool:
        return any("macos" in runner.lower() for runner in self.runners)

    @property
    def on_linux(self) -> bool:
        return any("macos" not in runner.lower() for runner in self.runners)


@functools.cache
def _workflow_text() -> str:
    return _WORKFLOW.read_text(encoding="utf-8")


@functools.cache
def _workflow_doc() -> dict[str, Any]:
    doc: dict[str, Any] = yaml.safe_load(_workflow_text())
    return doc


def _runners(job: dict[str, Any]) -> tuple[str, ...]:
    """Each runner a job runs on, a ``matrix.os`` runs-on expanded to its legs."""
    runs_on = job.get("runs-on", "")
    if isinstance(runs_on, list):
        return (" ".join(map(str, runs_on)),)
    if "matrix.os" in str(runs_on):
        matrix = (job.get("strategy") or {}).get("matrix") or {}
        legs = [*matrix.get("os", []), *(entry["os"] for entry in matrix.get("include", []) if "os" in entry)]
        return tuple(map(str, legs))
    return (str(runs_on),)


@functools.cache
def _audit_jobs() -> tuple[_AuditJob, ...]:
    """Every ci.yml job that runs pip-audit, with the closure it audits."""
    jobs: list[_AuditJob] = []
    for name, job in _workflow_doc()["jobs"].items():
        steps = job.get("steps") or []
        runs = [_strip_comments(str(step.get("run", ""))) for step in steps]
        audit_steps = [step for step, run in zip(steps, runs, strict=True) if _AUDIT_COMMAND.search(run)]
        if not audit_steps:
            continue
        extras: set[str] = set()
        groups: set[str] = set()
        flags: set[str] = set()
        for run in runs:
            extras |= _extras_installed(run)
            groups |= _groups_installed(run)
            flags |= _install_flags(run)
        caches_venv = _caches_venv(steps)
        jobs.append(
            _AuditJob(
                name=str(name),
                gating=_gates(job, audit_steps),
                extras=frozenset(extras),
                groups=frozenset(groups),
                caches_venv=caches_venv,
                install_flags=frozenset(flags),
                runners=_runners(job),
            )
        )
    assert jobs, "ci.yml has no job that runs pip-audit"
    return tuple(jobs)


def _declared_extras() -> list[str]:
    pyproject = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    extras = sorted(pyproject["project"].get("optional-dependencies", {}))
    assert extras, "pyproject declares no optional-dependencies to audit"
    return extras


def _optional_groups() -> list[str]:
    """Poetry groups outside the default install, so outside the gating closure."""
    pyproject = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    groups = pyproject["tool"]["poetry"].get("group", {})
    return sorted(name for name, group in groups.items() if group.get("optional"))


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


@pytest.mark.parametrize("group", _optional_groups())
def test_every_optional_group_is_audited_somewhere(group: str) -> None:
    """Installed by some audit job; platform-specific packages may still be skipped (see below)."""
    audited: set[str] = set().union(*(job.groups for job in _audit_jobs()))
    assert group in audited, (
        f"the optional '{group}' Poetry group is installed by no audit job, so pip-audit "
        f"never sees its dependencies. Add '--with {group}' to an audit job's install step."
    )


# The audit runners' platform, and macOS for comparison.
_LINUX = {
    "sys_platform": "linux",
    "platform_system": "Linux",
    "platform_machine": "x86_64",
    "platform_release": "",
    "platform_version": "",
    "os_name": "posix",
    "python_version": "3.13",
    "python_full_version": "3.13.0",
    "implementation_name": "cpython",
    "implementation_version": "3.13.0",
    "platform_python_implementation": "CPython",
}
_DARWIN = [
    {**_LINUX, "sys_platform": "darwin", "platform_system": "Darwin", "platform_machine": machine}
    for machine in ("arm64", "x86_64")
]


def _installs(marker: str | None, env: dict[str, str], extras: set[str]) -> bool:
    if not marker:
        return True
    return any(Marker(marker).evaluate({**env, "extra": extra}) for extra in extras | {""})


@functools.cache
def _lock_packages() -> tuple[dict[str, Any], ...]:
    return tuple(tomllib.loads(_LOCK.read_text(encoding="utf-8"))["package"])


def _darwin_only_packages(group: str, jobs: tuple[_AuditJob, ...], packages: tuple[dict[str, Any], ...]) -> list[str]:
    """Lock packages ``group`` installs on macOS but no Linux audit installs at all."""
    linux_jobs = [job for job in jobs if job.on_linux]
    extras: set[str] = set().union(*(job.extras for job in linux_jobs))
    audited_groups = {"main", "dev"}.union(*(job.groups for job in linux_jobs))
    names: list[str] = []
    for package in packages:
        groups = package.get("groups", [])
        if group not in groups:
            continue
        markers = package.get("markers")

        def marker_for(name: str, markers: Any = markers) -> str | None:
            return markers.get(name) if isinstance(markers, dict) else markers

        on_darwin_only = not _installs(marker_for(group), _LINUX, extras) and any(
            _installs(marker_for(group), env, extras) for env in _DARWIN
        )
        covered = any(_installs(marker_for(g), _LINUX, extras) for g in groups if g in audited_groups)
        if on_darwin_only and not covered:
            names.append(package["name"])
    return sorted(names)


def _job_comments(workflow: str, job_name: str) -> str:
    """The comment block above a workflow job and every comment inside it."""
    lines = workflow.splitlines()
    start = lines.index(f"  {job_name}:")
    first = start
    while first > 0 and lines[first - 1].startswith("  #"):
        first -= 1
    last = start + 1
    while last < len(lines) and not re.match(r"^  [A-Za-z0-9_-]+:\s*$", lines[last]):
        last += 1
    while last > start + 1 and (not lines[last - 1].strip() or lines[last - 1].startswith("  #")):
        last -= 1  # the next job's own preamble
    return " ".join(line.strip().lstrip("#").strip() for line in lines[first:last] if line.strip().startswith("#"))


@pytest.mark.parametrize("group", _optional_groups())
def test_darwin_only_dependencies_audited_on_linux_are_named_as_uncovered(group: str) -> None:
    """An audit on a Linux runner skips the group's macOS-only packages. Its job comment must say which."""
    jobs = [job for job in _audit_jobs() if group in job.groups]
    comments = " ".join(_job_comments(_workflow_text(), job.name) for job in jobs)
    darwin_only = (
        [] if any(job.on_macos for job in jobs) else _darwin_only_packages(group, _audit_jobs(), _lock_packages())
    )
    unnamed = [n for n in darwin_only if not re.search(rf"\b{re.escape(n)}\b", comments)]
    assert not unnamed, (
        f"the '{group}' group is audited only on Linux, where {unnamed} never install, so "
        "pip-audit never sees them. Name them in the audit job's comment as not covered."
    )


@pytest.mark.parametrize("group", _optional_groups())
def test_optional_groups_never_gate_a_release_or_reach_a_venv_cache(group: str) -> None:
    for job in _audit_jobs():
        assert not (job.gating and group in job.groups), (
            f"the gating audit job '{job.name}' installs the optional '{group}' group, "
            "workstation tooling whose CVEs must not block a release."
        )
        assert not (job.caches_venv and group in job.groups), (
            f"audit job '{job.name}' installs the optional '{group}' group and caches '.venv', "
            "so every job restoring that cache would start with it."
        )


def test_every_job_restoring_a_cached_venv_syncs_it_to_the_lock() -> None:
    """``poetry install`` keeps packages outside the selected closure, so a restored venv would too."""
    for name, job in _workflow_doc()["jobs"].items():
        steps = job.get("steps") or []
        if not _caches_venv(steps):
            continue
        verbs = {
            _poetry_verb(command)
            for step in steps
            for command in _shell_commands(str(step.get("run", "")))
            if _poetry_verb(command) in {"install", "sync"}
        }
        assert verbs == {"sync"}, (
            f"job '{name}' restores a cached .venv and installs with {sorted(verbs)}. Use "
            "'poetry sync' so packages the lock no longer lists are removed."
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
            "other job's cache by LRU."
        )


def test_the_gating_audit_keeps_the_build_toolchain_in_its_closure() -> None:
    """Poetry's ``dev`` group is non-optional, so the gate covers it. Keep it that way.

    ruff, pytest, mypy and bandit run inside the release pipeline, so a CVE in one
    of them is a CVE in what produces the artifact. Narrowing the gating install
    to the runtime closure would read as tightening the boundary this file draws,
    while actually dropping that coverage.
    """
    narrowing = {"--only", "--without", "--no-dev"}
    for job in _audit_jobs():
        if not job.gating:
            continue
        used = narrowing & job.install_flags
        assert not used, (
            f"the gating audit job '{job.name}' installs with {sorted(used)}, which drops "
            "Poetry groups from the audited closure. The 'dev' group is meant to be in it: "
            "its tooling runs in the pipeline that builds the release."
        )


# --- the parsers the guards above rely on -----------------------------------


@pytest.mark.parametrize(
    ("run", "extras", "groups"),
    [
        ("poetry sync -E detection --with mutation", {"detection"}, {"mutation"}),
        ("poetry install --extras=export --with=a,b", {"export"}, {"a", "b"}),
        ("poetry sync -Edetection --with a --with b", {"detection"}, {"a", "b"}),
        ('poetry install -E "detection export"', {"detection", "export"}, set()),
        ("poetry run pytest -E x --with y", set(), set()),
        ("# poetry sync -E detection", set(), set()),
    ],
    ids=["separate", "equals", "attached-repeated", "space-separated", "not-an-install", "comment"],
)
def test_install_parsers_read_every_spelling(run: str, extras: set[str], groups: set[str]) -> None:
    assert _extras_installed(run) == extras
    assert _groups_installed(run) == groups


@pytest.mark.parametrize(
    ("job", "runners"),
    [
        ({"runs-on": "ubuntu-latest"}, ("ubuntu-latest",)),
        ({"runs-on": ["self-hosted", "macOS"]}, ("self-hosted macOS",)),
        (
            {
                "runs-on": "${{ matrix.os }}",
                "strategy": {"matrix": {"os": ["ubuntu-latest"], "include": [{"os": "macos-14"}]}},
            },
            ("ubuntu-latest", "macos-14"),
        ),
    ],
    ids=["literal", "label-list", "matrix"],
)
def test_runners_expand_a_matrix_into_its_legs(job: dict[str, Any], runners: tuple[str, ...]) -> None:
    assert _runners(job) == runners


_TWO_JOBS = """\
jobs:
  # audit preamble names macholib
  audit:
    steps:
      # step comment
      - run: make audit

  # next preamble names pyinstaller

  next:
    steps: []
"""


def test_job_comments_stop_before_the_next_jobs_preamble() -> None:
    comments = _job_comments(_TWO_JOBS, "audit")
    assert "macholib" in comments
    assert "step comment" in comments
    assert "pyinstaller" not in comments


def _job(*, groups: set[str], runners: tuple[str, ...] = ("ubuntu-latest",)) -> _AuditJob:
    return _AuditJob(
        name="job",
        gating=False,
        extras=frozenset(),
        groups=frozenset(groups),
        caches_venv=False,
        install_flags=frozenset(),
        runners=runners,
    )


_MAC = 'sys_platform == "darwin"'


@pytest.mark.parametrize(
    ("package", "jobs", "uncovered"),
    [
        ({"groups": ["macos"], "markers": _MAC}, (_job(groups={"macos"}),), True),
        (
            {"groups": ["macos"], "markers": f'{_MAC} and platform_machine == "arm64"'},
            (_job(groups={"macos"}),),
            True,
        ),
        ({"groups": ["dev", "macos"], "markers": {"macos": _MAC}}, (_job(groups={"macos"}),), False),
        (
            {"groups": ["mutation", "macos"], "markers": {"macos": _MAC}},
            (_job(groups={"macos", "mutation"}),),
            False,
        ),
        (
            {"groups": ["mutation", "macos"], "markers": {"macos": _MAC}},
            (_job(groups={"macos"}), _job(groups={"mutation"}, runners=("macos-14",))),
            True,
        ),
        ({"groups": ["macos"], "markers": 'sys_platform == "win32"'}, (_job(groups={"macos"}),), False),
        ({"groups": ["macos"]}, (_job(groups={"macos"}),), False),
    ],
    ids=[
        "darwin-only",
        "compound-marker",
        "dev-installs-it-on-linux",
        "linux-audited-group-installs-it",
        "macos-job-is-not-linux-coverage",
        "windows-only",
        "unconditional",
    ],
)
def test_darwin_only_packages(package: dict[str, Any], jobs: tuple[_AuditJob, ...], uncovered: bool) -> None:
    named = _darwin_only_packages("macos", jobs, ({"name": "pkg", **package},))
    assert named == (["pkg"] if uncovered else [])
