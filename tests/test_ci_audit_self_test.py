# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The gating audit job runs on a PR that edits it, without a CVE blocking that PR.

The audit is kept off pull requests so a newly disclosed CVE cannot block
unrelated work, which leaves an edit to the job itself untested until it reaches
``main``. A PR touching ``ci.yml`` or the ``Makefile`` (which holds the ``audit``
target) therefore runs it, with only the pip-audit verdict advisory: the install
and cache mechanics the edit could break still fail the job.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests._gha_expr import evaluate

pytestmark = pytest.mark.unit

_WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"
_NON_PR_EVENTS = ("push", "schedule", "workflow_dispatch")


def _jobs() -> dict[str, Any]:
    doc: dict[str, Any] = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    jobs: dict[str, Any] = doc["jobs"]
    return jobs


def _needs(job: dict[str, Any]) -> list[str]:
    needs = job.get("needs", [])
    return [needs] if isinstance(needs, str) else list(needs)


def _audit_step(job: dict[str, Any]) -> dict[str, Any]:
    steps = [step for step in job["steps"] if "make audit" in str(step.get("run", ""))]
    assert len(steps) == 1, "expected exactly one 'make audit' step in the audit job"
    return steps[0]


def _job_runs(job: dict[str, Any], event: str, *, changes_output: str, changes_succeeded: bool) -> bool:
    context = {"github.event_name": event, "needs.changes.outputs.audit": changes_output}
    return evaluate(job.get("if", True), context, needs_succeeded=changes_succeeded)


# --- when the audit job runs ------------------------------------------------


@pytest.mark.parametrize("event", _NON_PR_EVENTS)
def test_audit_runs_outside_prs_although_changes_is_skipped(event: str) -> None:
    # ``changes`` only runs on PRs, and a skipped need fails the implicit success().
    assert _job_runs(_jobs()["audit"], event, changes_output="", changes_succeeded=False)


def test_audit_runs_on_a_pr_that_edits_it() -> None:
    assert _job_runs(_jobs()["audit"], "pull_request", changes_output="true", changes_succeeded=True)


def test_audit_stays_off_a_pr_that_does_not_edit_it() -> None:
    assert not _job_runs(_jobs()["audit"], "pull_request", changes_output="false", changes_succeeded=True)


def test_audit_stays_off_a_pr_whose_change_detection_failed() -> None:
    # The failed ``changes`` job is what reports this; the audit must not guess.
    assert not _job_runs(_jobs()["audit"], "pull_request", changes_output="", changes_succeeded=False)


def test_audit_reads_the_changes_job_it_depends_on() -> None:
    assert "changes" in _needs(_jobs()["audit"])


@pytest.mark.parametrize("output", ["true", "false"])
def test_export_audit_never_runs_on_a_pr(output: str) -> None:
    # Several GB of torch + CUDA wheels is too high a price for a workflow edit.
    assert not _job_runs(_jobs()["audit-export"], "pull_request", changes_output=output, changes_succeeded=True)


# --- what may fail on a PR run ----------------------------------------------


def test_the_cve_verdict_is_advisory_on_a_pr() -> None:
    step = _audit_step(_jobs()["audit"])
    assert evaluate(step.get("continue-on-error", False), {"github.event_name": "pull_request"})


@pytest.mark.parametrize("event", _NON_PR_EVENTS)
def test_the_cve_verdict_gates_outside_prs(event: str) -> None:
    step = _audit_step(_jobs()["audit"])
    assert not evaluate(step.get("continue-on-error", False), {"github.event_name": event})


def test_the_install_mechanics_fail_the_pr_run() -> None:
    job = _jobs()["audit"]
    context = {"github.event_name": "pull_request"}
    assert not evaluate(job.get("continue-on-error", False), context)
    audit_step = _audit_step(job)
    tolerant = [
        step.get("name", step.get("uses"))
        for step in job["steps"]
        if step is not audit_step and evaluate(step.get("continue-on-error", False), context)
    ]
    assert not tolerant, f"{tolerant} may fail without failing the PR run - that is what the run exists to test"


# --- change detection -------------------------------------------------------


def test_changes_runs_only_on_prs() -> None:
    job = _jobs()["changes"]
    assert evaluate(job["if"], {"github.event_name": "pull_request"})
    for event in _NON_PR_EVENTS:
        assert not evaluate(job["if"], {"github.event_name": event})


def test_changes_exposes_its_detection_step_output() -> None:
    job = _jobs()["changes"]
    step_ids = {step.get("id") for step in job["steps"]}
    assert job["outputs"]["audit"] == "${{ steps.diff.outputs.audit }}"
    assert "diff" in step_ids


def test_changes_fetches_the_merge_commits_first_parent() -> None:
    steps = _jobs()["changes"]["steps"]
    checkout = next(step for step in steps if str(step.get("uses", "")).startswith("actions/checkout"))
    assert int((checkout.get("with") or {}).get("fetch-depth", 1)) >= 2


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def _git_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
    }.items():
        monkeypatch.setenv(key, value)


def _merge_commit(tmp_path: Path, pr_paths: list[str], base_paths: list[str]) -> Path:
    """A repo whose HEAD is the merge of a PR into a base that moved on meanwhile."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    for rel in (".github/workflows/ci.yml", "Makefile", "docs/Makefile", "README.md"):
        _write(repo, rel, "base\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "checkout", "-q", "-b", "pr")
    for rel in pr_paths:
        _write(repo, rel, "pr\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "pr")
    _git(repo, "checkout", "-q", "main")
    for rel in base_paths:
        _write(repo, rel, "main\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "main moved on")
    _git(repo, "merge", "-q", "--no-ff", "-m", "merge", "pr")
    return repo


@pytest.mark.usefixtures("_git_env")
@pytest.mark.parametrize(
    "shell",
    [["bash", "-e"], ["bash", "--noprofile", "--norc", "-eo", "pipefail"]],
    ids=["default-shell", "shell-bash"],
)
@pytest.mark.parametrize(
    ("pr_paths", "base_paths", "expected"),
    [
        ([".github/workflows/ci.yml"], [], "true"),
        (["Makefile"], [], "true"),
        (["README.md", ".github/workflows/ci.yml"], [], "true"),
        (["README.md"], [], "false"),
        (["docs/Makefile"], [], "false"),
        ([".github/workflows/release-deb.yml"], [], "false"),
        # An edit that reached the base while the PR was open is not the PR's.
        (["README.md"], [".github/workflows/ci.yml", "Makefile"], "false"),
    ],
    ids=["ci-yml", "makefile", "mixed", "unrelated", "nested-makefile", "other-workflow", "base-only-edit"],
)
def test_change_detection_flags_only_the_prs_own_edits(
    tmp_path: Path, shell: list[str], pr_paths: list[str], base_paths: list[str], expected: str
) -> None:
    repo = _merge_commit(tmp_path, pr_paths, base_paths)
    step = next(step for step in _jobs()["changes"]["steps"] if step.get("id") == "diff")
    script = tmp_path / "step.sh"
    script.write_text(str(step["run"]), encoding="utf-8")
    output = tmp_path / "github_output"
    output.touch()
    subprocess.run(
        [*shell, str(script)],
        cwd=repo,
        env={**os.environ, "GITHUB_OUTPUT": str(output)},
        check=True,
        capture_output=True,
    )
    assert output.read_text(encoding="utf-8").splitlines() == [f"audit={expected}"]


# --- the expression evaluator the tests above rely on ------------------------


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        (True, True),
        (False, False),
        ("${{ github.event_name == 'push' }}", True),
        ("github.event_name != 'push'", False),
        ("${{ !(github.event_name == 'push') }}", False),
        ("false || true && false", False),  # && binds tighter than ||
        ("(false || true) && true", True),
        ("x.unset == ''", True),
        ("'it''s' == 'it''s'", True),
        ("always()", True),
    ],
)
def test_evaluator_semantics(expr: object, expected: bool) -> None:
    assert evaluate(expr, {"github.event_name": "push"}) is expected


def test_evaluator_applies_the_implicit_success_only_without_a_status_function() -> None:
    assert not evaluate("true", {}, needs_succeeded=False)
    assert evaluate("!cancelled() && true", {}, needs_succeeded=False)
    assert not evaluate("success()", {}, needs_succeeded=False)


@pytest.mark.parametrize("expr", ["a > b", "contains(a, 'b')", "a ==", "(a", "a b", "fromJSON()"])
def test_evaluator_rejects_what_it_does_not_model(expr: str) -> None:
    with pytest.raises(ValueError):
        evaluate(expr, {})
