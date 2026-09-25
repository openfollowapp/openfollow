# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""``scripts/ci-remote.sh`` run for real against a stub ``ssh`` that executes
the "remote" side on this host, so the Pi checkout is a local git repo and the
sync, guard and restore are exercised with real ``rsync`` and ``git``."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

import openfollow

pytestmark = pytest.mark.integration

_SCRIPT = Path(openfollow.__file__).resolve().parent.parent / "scripts" / "ci-remote.sh"

# Drops ssh's options and destination, then runs the command the way sshd would.
_FAKE_SSH = """#!/usr/bin/env bash
while [ $# -gt 0 ]; do
  case "$1" in
    -o|-l|-p|-i|-F) shift 2 ;;
    -*) shift ;;
    *) break ;;
  esac
done
shift
exec bash -c "$*"
"""

_FAKE_MAKE = """#!/usr/bin/env bash
echo "make $*" >> "$FAKE_MAKE_LOG"
if [ -n "${FAKE_MAKE_HOOK:-}" ]; then eval "$FAKE_MAKE_HOOK"; fi
exit "${FAKE_MAKE_STATUS:-0}"
"""


@dataclass
class _Station:
    local: Path
    pi: Path
    make_log: Path
    env: dict[str, str]
    orig_ref: str

    def git(self, *args: str) -> str:
        out = subprocess.run(
            ["git", "-C", str(self.pi), *args], env=self.env, check=True, capture_output=True, text=True
        )
        return out.stdout.strip()

    def run(self, **extra_env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(self.local / "scripts" / "ci-remote.sh")],
            env={**self.env, **extra_env},
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def start(self, **extra_env: str) -> subprocess.Popen[str]:
        # Own process group, so a signal reaches the whole run the way Ctrl-C does.
        return subprocess.Popen(
            ["bash", str(self.local / "scripts" / "ci-remote.sh")],
            env={**self.env, **extra_env},
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )

    def make_ran(self) -> bool:
        return self.make_log.exists()


def _station(tmp_path: Path, *, local_git: str = "directory") -> _Station:
    if shutil.which("rsync") is None:
        pytest.fail("rsync is required: ci-remote.sh syncs with it")
    home = tmp_path / "home"
    home.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("ssh", _FAKE_SSH), ("make", _FAKE_MAKE)):
        (bin_dir / name).write_text(body, encoding="utf-8")
        (bin_dir / name).chmod(0o755)

    env = {k: v for k, v in os.environ.items() if not k.startswith(("OPENFOLLOW_CI_", "GIT_"))}
    env.update(
        PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        HOME=str(home),
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CEILING_DIRECTORIES=str(tmp_path),
        GIT_AUTHOR_NAME="ci",
        GIT_AUTHOR_EMAIL="ci@example.invalid",
        GIT_COMMITTER_NAME="ci",
        GIT_COMMITTER_EMAIL="ci@example.invalid",
        OPENFOLLOW_CI_HOSTS="pi.invalid",
        OPENFOLLOW_CI_DIR=str(home / "openfollow"),
        FAKE_MAKE_LOG=str(tmp_path / "make.log"),
    )

    pi = home / "openfollow"
    pi.mkdir()
    (pi / "tracked.txt").write_text("pi\n", encoding="utf-8")
    for args in (("init", "-q"), ("add", "tracked.txt"), ("commit", "-q", "-m", "pi")):
        subprocess.run(["git", "-C", str(pi), *args], env=env, check=True, capture_output=True)

    local = tmp_path / "local"
    (local / "scripts").mkdir(parents=True)
    shutil.copy(_SCRIPT, local / "scripts" / "ci-remote.sh")
    (local / "branch_only.txt").write_text("branch\n", encoding="utf-8")
    if local_git == "worktree":
        (local / ".git").write_text(f"gitdir: {tmp_path}/main/.git/worktrees/wt\n", encoding="utf-8")
    else:
        (local / ".git").mkdir()
        (local / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

    station = _Station(local=local, pi=pi, make_log=tmp_path / "make.log", env=env, orig_ref="")
    station.orig_ref = station.git("rev-parse", "HEAD")
    return station


@pytest.mark.parametrize("local_git", ["directory", "worktree"])
def test_the_gate_runs_on_the_synced_tree_and_the_pi_is_restored(tmp_path: Path, local_git: str) -> None:
    station = _station(tmp_path, local_git=local_git)
    result = station.run(FAKE_MAKE_HOOK='test -f branch_only.txt && echo synced >> "$FAKE_MAKE_LOG"')

    assert result.returncode == 0, result.stderr
    assert "CI PASSED" in result.stderr
    assert station.make_log.read_text(encoding="utf-8").splitlines() == ["make ci", "synced"]
    # A worktree's .git is a pointer file; syncing it would replace the Pi's repository.
    assert (station.pi / ".git").is_dir()
    assert station.git("rev-parse", "HEAD") == station.orig_ref
    assert station.git("status", "--porcelain") == ""
    assert not (station.pi / "branch_only.txt").exists()


def _no_git(pi: Path) -> None:
    shutil.rmtree(pi / ".git")


def _git_is_a_pointer_file(pi: Path) -> None:
    # A worktree's .git pointer file, synced over the Pi's repository.
    shutil.rmtree(pi / ".git")
    (pi / ".git").write_text("gitdir: /nonexistent/.git/worktrees/wt\n", encoding="utf-8")


def _no_commits(pi: Path) -> None:
    shutil.rmtree(pi / ".git")
    subprocess.run(["git", "-C", str(pi), "init", "-q"], check=True, capture_output=True)


def _inside_another_repo(pi: Path) -> None:
    # git would find the enclosing repository's HEAD and treat it as the Pi's.
    shutil.rmtree(pi / ".git")
    home = pi.parent
    (home / ".profile").write_text("\n", encoding="utf-8")
    for args in (("init", "-q"), ("add", ".profile"), ("commit", "-q", "-m", "dotfiles")):
        subprocess.run(
            ["git", "-c", "user.name=ci", "-c", "user.email=ci@example.invalid", "-C", str(home), *args],
            check=True,
            capture_output=True,
        )


@pytest.mark.parametrize("break_checkout", [_no_git, _git_is_a_pointer_file, _no_commits, _inside_another_repo])
def test_a_pi_checkout_with_no_head_is_refused_before_anything_is_synced(
    tmp_path: Path, break_checkout: Callable[[Path], None]
) -> None:
    station = _station(tmp_path)
    break_checkout(station.pi)

    result = station.run()

    assert result.returncode == 2
    assert "no git checkout" in result.stderr
    assert "CI PASSED" not in result.stderr
    assert not station.make_ran()
    assert (station.pi / "tracked.txt").exists()
    assert not (station.pi / "branch_only.txt").exists()


def test_a_dirty_pi_checkout_is_refused_and_left_as_it_was(tmp_path: Path) -> None:
    station = _station(tmp_path)
    (station.pi / "tracked.txt").write_text("edited on the pi\n", encoding="utf-8")
    (station.pi / "untracked.txt").write_text("new\n", encoding="utf-8")

    result = station.run()

    assert result.returncode == 2
    assert "has 2 uncommitted change(s)" in result.stderr
    assert not station.make_ran()
    assert (station.pi / "tracked.txt").read_text(encoding="utf-8") == "edited on the pi\n"
    assert (station.pi / "untracked.txt").exists()


def test_force_runs_over_a_dirty_pi_checkout_and_restores_its_commit(tmp_path: Path) -> None:
    station = _station(tmp_path)
    (station.pi / "tracked.txt").write_text("edited on the pi\n", encoding="utf-8")

    result = station.run(OPENFOLLOW_CI_FORCE="1")

    assert result.returncode == 0, result.stderr
    assert station.make_ran()
    assert station.git("rev-parse", "HEAD") == station.orig_ref
    assert station.git("status", "--porcelain") == ""


def test_a_failing_gate_keeps_its_exit_status_and_the_pi_is_restored(tmp_path: Path) -> None:
    station = _station(tmp_path)

    result = station.run(FAKE_MAKE_STATUS="2")

    assert result.returncode == 2
    assert "CI FAILED on pi.invalid (exit 2)" in result.stderr
    assert station.git("rev-parse", "HEAD") == station.orig_ref
    assert station.git("status", "--porcelain") == ""


@pytest.mark.parametrize("make_status", ["0", "2"])
def test_a_failed_restore_fails_the_run(tmp_path: Path, make_status: str) -> None:
    station = _station(tmp_path)

    result = station.run(FAKE_MAKE_HOOK="rm -rf .git", FAKE_MAKE_STATUS=make_status)

    assert result.returncode != 0
    assert "CI PASSED" not in result.stderr
    assert "could not be restored" in result.stderr


def test_an_interrupted_gate_still_restores_the_pi(tmp_path: Path) -> None:
    station = _station(tmp_path)
    running = tmp_path / "make.running"

    proc = station.start(FAKE_MAKE_HOOK=f'touch "{running}"; sleep 60')
    try:
        deadline = time.monotonic() + 30
        while not running.exists() and proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert running.exists(), "the gate never started"
        os.killpg(proc.pid, signal.SIGINT)
        _, stderr = proc.communicate(timeout=30)
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()

    assert proc.returncode != 0
    assert "CI PASSED" not in stderr
    assert station.git("rev-parse", "HEAD") == station.orig_ref
    assert station.git("status", "--porcelain") == ""
