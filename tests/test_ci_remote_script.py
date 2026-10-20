# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""``scripts/ci-remote.sh`` run for real against a stub ``ssh`` that executes
the "remote" side on this host, so the Pi checkout is a local git repo and the
sync, guard and restore are exercised with real ``rsync`` and ``git``."""

from __future__ import annotations

import contextlib
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

import openfollow

pytestmark = pytest.mark.integration

_SCRIPT = Path(openfollow.__file__).resolve().parent.parent / "scripts" / "ci-remote.sh"

# Drops ssh's options and destination, then runs the command the way sshd would: in a
# session of its own, so a signal to the caller's process group stops this "ssh" and not
# the "remote" command, and the command outlives a connection that goes away.
_FAKE_SSH = f"""#!{sys.executable} -I
import os, signal, sys
signal.signal(signal.SIGINT, signal.SIG_DFL)
args = sys.argv[1:]
while args and args[0].startswith("-"):
    args = args[2:] if args[0] in ("-o", "-l", "-p", "-i", "-F") else args[1:]
child = os.fork()
if child == 0:
    os.setsid()
    os.environ["FAKE_SSH_PID"] = str(os.getppid())
    os.execvp("bash", ["bash", "-c", " ".join(args[1:])])
code = os.waitstatus_to_exitcode(os.waitpid(child, 0)[1])
sys.exit(code if code >= 0 else 128 - code)
"""

# A long gate step on the Pi: records its pid once it can be stopped, and on SIGTERM
# whether the synced tree was still in place (the checkout not yet restored under it).
_LONG_STEP = (
    "import os, pathlib, signal, sys, time\n"
    "def stop(*_):\n"
    "    pathlib.Path(sys.argv[2]).write_text(str(os.path.exists('branch_only.txt')))\n"
    "    sys.exit(143)\n"
    "signal.signal(signal.SIGTERM, stop)\n"
    "pathlib.Path(sys.argv[1] + '.tmp').write_text(str(os.getpid()))\n"
    "os.replace(sys.argv[1] + '.tmp', sys.argv[1])\n"
    "time.sleep(60)\n"
)


def _long_step(running: Path, stopped: Path) -> str:
    return " ".join(shlex.quote(a) for a in (sys.executable, "-I", "-c", _LONG_STEP, str(running), str(stopped)))


# Stands in for hud_bench.py --compare: the bench's own exit code, which make would turn into 2.
_FAKE_POETRY = """#!/usr/bin/env bash
[ "$3 $4" = "scripts/hw_validation/hud_bench.py --compare" ] || exit 99
if [ -n "${FAKE_PERF_HOOK:-}" ]; then eval "$FAKE_PERF_HOOK"; fi
echo "bench against: $(cat "$5/openfollow/video/overlay.py")" >> "$FAKE_MAKE_LOG"
echo "$5" > "$FAKE_MAKE_LOG.base"
exit "${FAKE_PERF_STATUS:-0}"
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

    def made(self) -> list[str]:
        return self.make_log.read_text(encoding="utf-8").splitlines() if self.make_log.exists() else []

    def perf_base(self) -> Path:
        return Path(Path(f"{self.make_log}.base").read_text(encoding="utf-8").strip())

    def commit(self, path: str, text: str) -> None:
        """Commit ``text`` to ``path`` on the local branch."""
        (self.local / path).parent.mkdir(parents=True, exist_ok=True)
        (self.local / path).write_text(text, encoding="utf-8")
        for args in (("add", path), ("commit", "-q", "-m", path)):
            subprocess.run(["git", "-C", str(self.local), *args], env=self.env, check=True, capture_output=True)


def _station(tmp_path: Path, *, local_git: str = "directory") -> _Station:
    if shutil.which("rsync") is None:
        pytest.fail("rsync is required: ci-remote.sh syncs with it")
    home = tmp_path / "home"
    home.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("ssh", _FAKE_SSH), ("make", _FAKE_MAKE), ("poetry", _FAKE_POETRY)):
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
    if local_git == "repo":
        # A real branch: the drawing code and a doc as on origin/main, then the branch's own commits.
        (local / "openfollow" / "video").mkdir(parents=True)
        (local / "openfollow" / "video" / "overlay.py").write_text("base", encoding="utf-8")
        (local / "docs").mkdir()
        (local / "docs" / "guide.md").write_text("base\n", encoding="utf-8")
        for args in (
            ("init", "-q"),
            ("add", "-A"),
            ("commit", "-q", "-m", "base"),
            ("update-ref", "refs/remotes/origin/main", "HEAD"),
        ):
            subprocess.run(["git", "-C", str(local), *args], env=env, check=True, capture_output=True)
    elif local_git == "worktree":
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


def _await(path: Path, proc: subprocess.Popen[str]) -> int:
    """The pid a long step wrote to ``path`` once it is running on the Pi."""
    deadline = time.monotonic() + 30
    while not path.exists() and proc.poll() is None and time.monotonic() < deadline:
        time.sleep(0.02)
    assert path.exists(), "the gate never started"
    return int(path.read_text(encoding="utf-8"))


def _gone(pid: int, *, group: bool = False) -> bool:
    try:
        (os.killpg if group else os.kill)(pid, 0)
    except ProcessLookupError:
        return True
    return False


def _end(proc: subprocess.Popen[str], step_pid: int, stopped: Path) -> None:
    """Kill what this test started and is still running: the script's own process group,
    and a long step that never got its SIGTERM (it exits on one, so its pid is not reused)."""
    if proc.poll() is None:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
    if step_pid and not stopped.exists():
        with contextlib.suppress(ProcessLookupError):
            os.kill(step_pid, signal.SIGKILL)


@pytest.mark.parametrize("step", ["make ci", "drawing comparison"])
@pytest.mark.parametrize("interrupt", ["ctrl-c", "sigterm", "dropped connection"])
def test_an_interrupted_gate_is_stopped_on_the_pi_before_its_checkout_is_restored(
    tmp_path: Path, interrupt: str, step: str
) -> None:
    station = _station(tmp_path, local_git="repo")
    station.commit("openfollow/video/overlay.py", "branch")
    running, stopped = tmp_path / "step.running", tmp_path / "step.stopped"
    hook = _long_step(running, stopped)
    if interrupt == "dropped connection":
        hook = f'kill -KILL "$FAKE_SSH_PID"; {hook}'
    # Another gate on the same Pi, in its own session: never this run's to stop.
    other = subprocess.Popen(
        [sys.executable, "-I", "-c", "import time; time.sleep(60)", "make", "ci"], start_new_session=True
    )
    proc = station.start(**{"FAKE_MAKE_HOOK" if step == "make ci" else "FAKE_PERF_HOOK": hook})
    pid = 0
    try:
        pid = _await(running, proc)
        if interrupt != "dropped connection":
            os.killpg(proc.pid, signal.SIGINT if interrupt == "ctrl-c" else signal.SIGTERM)
        _, stderr = proc.communicate(timeout=30)
        other_survived = other.poll() is None
    finally:
        _end(proc, pid, stopped)
        other.kill()
        other.wait()

    assert proc.returncode != 0
    assert "CI PASSED" not in stderr
    # Stopped while the synced tree was still in place, then the checkout restored.
    assert stopped.read_text(encoding="utf-8") == "True"
    assert other_survived
    assert station.git("rev-parse", "HEAD") == station.orig_ref
    assert station.git("status", "--porcelain") == ""


def test_a_gate_left_running_on_the_pi_holds_off_the_next_until_it_ends(tmp_path: Path) -> None:
    station = _station(tmp_path)
    running, stopped = tmp_path / "step.running", tmp_path / "step.stopped"
    first = station.start(FAKE_MAKE_HOOK=_long_step(running, stopped))
    pid = 0
    try:
        pid = _await(running, first)
        # The workstation went away mid-gate: no trap ran, the Pi run carries on.
        os.killpg(first.pid, signal.SIGKILL)
        first.wait()
        refused = station.run(OPENFOLLOW_CI_FORCE="1")
        survived = not _gone(pid)
        # The run ends on its own: TERM lets the long step say it was stopped.
        group = os.getpgid(pid)
        os.killpg(group, signal.SIGTERM)
        deadline = time.monotonic() + 30
        while not _gone(group, group=True) and time.monotonic() < deadline:
            time.sleep(0.02)
        after = station.run(OPENFOLLOW_CI_FORCE="1")
    finally:
        _end(first, pid, stopped)
        if first.stderr is not None:
            first.stderr.close()

    assert refused.returncode == 2
    assert "an earlier gate is still running" in refused.stderr
    assert survived
    assert after.returncode == 0, after.stderr
    assert station.made() == ["make ci", "make ci"]
    assert station.git("rev-parse", "HEAD") == station.orig_ref
    assert station.git("status", "--porcelain") == ""


@pytest.mark.parametrize("record", ["1", "0", "01", "-1", "", "abc"])
def test_a_run_record_naming_no_gate_neither_blocks_nor_stops_anything(tmp_path: Path, record: str) -> None:
    """kill reads group 0 or 1 as "every process", so such a record names no run."""
    station = _station(tmp_path)
    (station.pi / ".git" / "ci-remote.pid").write_text(record, encoding="utf-8")

    result = station.run()

    assert result.returncode == 0, result.stderr
    assert station.made() == ["make ci"]


# --------------------------------------------------------------------------- #
# Operator Screen drawing A/B against the merge-base
# --------------------------------------------------------------------------- #


def test_a_drawing_change_is_timed_against_the_merge_base_s_package_after_a_green_gate(tmp_path: Path) -> None:
    station = _station(tmp_path, local_git="repo")
    station.commit("openfollow/video/overlay.py", "branch")

    result = station.run()

    assert result.returncode == 0, result.stderr
    assert station.made() == ["make ci", "bench against: base"]
    assert "CI PASSED" in result.stderr
    assert not station.perf_base().exists()
    assert station.git("status", "--porcelain") == ""


@pytest.mark.parametrize(
    ("path", "committed"),
    [("openfollow/runtime/overlay_draw_new.py", False), ("tests/_hud_scenes.py", True), ("openfollow/window.py", True)],
)
def test_a_change_to_any_drawing_path_committed_or_not_is_timed(tmp_path: Path, path: str, committed: bool) -> None:
    station = _station(tmp_path, local_git="repo")
    if committed:
        station.commit(path, "branch")
    else:
        (station.local / path).parent.mkdir(parents=True, exist_ok=True)
        (station.local / path).write_text("new", encoding="utf-8")

    result = station.run()

    assert result.returncode == 0, result.stderr
    assert "bench against: base" in station.made()


def test_a_change_off_the_drawing_paths_is_not_timed(tmp_path: Path) -> None:
    station = _station(tmp_path, local_git="repo")
    station.commit("docs/guide.md", "branch\n")

    result = station.run()

    assert result.returncode == 0, result.stderr
    assert station.made() == ["make ci"]


@pytest.mark.parametrize(
    ("perf_status", "message"),
    [("1", "more than 15 % slower"), ("2", "comparison could not run")],
)
def test_a_slower_or_unrunnable_comparison_fails_the_gate_and_the_pi_is_restored(
    tmp_path: Path, perf_status: str, message: str
) -> None:
    station = _station(tmp_path, local_git="repo")
    station.commit("openfollow/video/overlay.py", "branch")

    result = station.run(FAKE_PERF_STATUS=perf_status)

    assert result.returncode == int(perf_status)
    assert message in result.stderr
    assert "CI PASSED" not in result.stderr
    assert not station.perf_base().exists()
    assert station.git("rev-parse", "HEAD") == station.orig_ref
    assert station.git("status", "--porcelain") == ""


def test_a_red_gate_is_not_timed(tmp_path: Path) -> None:
    station = _station(tmp_path, local_git="repo")
    station.commit("openfollow/video/overlay.py", "branch")

    result = station.run(FAKE_MAKE_STATUS="2")

    assert result.returncode == 2
    assert station.made() == ["make ci"]


@pytest.mark.parametrize(
    ("mode", "path", "timed"),
    [("0", "openfollow/video/overlay.py", False), ("1", "docs/guide.md", True)],
)
def test_the_comparison_can_be_forced_off_or_on(tmp_path: Path, mode: str, path: str, timed: bool) -> None:
    station = _station(tmp_path, local_git="repo")
    station.commit(path, "branch")

    result = station.run(OPENFOLLOW_CI_PERF=mode)

    assert result.returncode == 0, result.stderr
    assert ("bench against: base" in station.made()) is timed


def test_without_a_merge_base_the_comparison_is_skipped_and_said_so(tmp_path: Path) -> None:
    station = _station(tmp_path)

    result = station.run(OPENFOLLOW_CI_PERF="1")

    assert result.returncode == 0, result.stderr
    assert "skipping the Operator Screen drawing comparison" in result.stderr
    assert not any(line.startswith("bench") for line in station.made())


@pytest.mark.parametrize(("perf_status", "expected"), [("0", 0), ("1", 1)])
def test_the_local_fallback_times_a_drawing_change_too(tmp_path: Path, perf_status: str, expected: int) -> None:
    station = _station(tmp_path, local_git="repo")
    station.commit("openfollow/video/overlay.py", "branch")

    result = station.run(OPENFOLLOW_CI_LOCAL="1", FAKE_PERF_STATUS=perf_status)

    assert result.returncode == expected, result.stderr
    assert station.made() == [f"make -C {station.local} ci", "bench against: base"]
    assert not station.perf_base().exists()
