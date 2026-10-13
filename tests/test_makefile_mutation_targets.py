# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The ``make mutation*`` targets: stop before touching anything when mutmut is missing, run it when present."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")]

_MAKEFILE = Path(__file__).resolve().parents[1] / "Makefile"

# Every ``poetry`` call fails, as ``poetry run python -c "import mutmut"`` does without the
# group; the delay lets a target that clears in parallel do so before the check fails.
_FAILING_POETRY = "#!/bin/sh\nsleep 0.5\nexit 1\n"
_RECORDING_POETRY = '#!/bin/sh\necho "$*" >> "$POETRY_LOG"\n'


def _make(tmp_path: Path, poetry_script: str, *args: str) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Run the repo Makefile from ``tmp_path`` (not the repo) with a stand-in ``poetry``."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    poetry = bin_dir / "poetry"
    poetry.write_text(poetry_script, encoding="utf-8")
    poetry.chmod(0o755)
    log = tmp_path / "poetry.log"
    env = {k: v for k, v in os.environ.items() if k not in {"MAKEFLAGS", "MFLAGS", "MAKELEVEL"}}
    env["PATH"] = os.pathsep.join([str(bin_dir), env.get("PATH", "")])
    env["POETRY_LOG"] = str(log)
    run = subprocess.run(
        ["make", "--no-print-directory", "-f", str(_MAKEFILE), *args],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return run, log


def _previous_results(tmp_path: Path) -> Path:
    previous = tmp_path / "mutants" / "previous-run"
    previous.parent.mkdir()
    previous.write_text("results", encoding="utf-8")
    return previous


@pytest.mark.parametrize("jobs", ["-j1", "-j4"])
def test_mutation_keeps_the_previous_results_when_mutmut_is_missing(tmp_path: Path, jobs: str) -> None:
    previous = _previous_results(tmp_path)
    run, _ = _make(tmp_path, _FAILING_POETRY, jobs, "mutation")
    assert run.returncode != 0, run.stdout + run.stderr
    assert "mutmut" in run.stdout + run.stderr
    assert previous.is_file(), "the previous mutation results were deleted before the mutmut check failed"


@pytest.mark.parametrize("jobs", ["-j1", "-j4"])
def test_mutation_clears_the_previous_results_then_runs_mutmut(tmp_path: Path, jobs: str) -> None:
    previous = _previous_results(tmp_path)
    run, log = _make(tmp_path, _RECORDING_POETRY, jobs, "mutation")
    assert run.returncode == 0, run.stdout + run.stderr
    assert not previous.exists()
    assert log.read_text(encoding="utf-8").splitlines()[-1] == "run mutmut run"


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("MODULE=openfollow.zones.engine mutation-module", "run mutmut run openfollow.zones.engine.*"),
        (
            "MUTANT=openfollow.zones.engine.x_update__mutmut_1 mutation-show",
            "run mutmut show openfollow.zones.engine.x_update__mutmut_1",
        ),
        ("mutation-results", "run mutmut results"),
    ],
    ids=["module", "show", "results"],
)
def test_mutation_targets_run_mutmut_once_it_is_installed(tmp_path: Path, target: str, expected: str) -> None:
    run, log = _make(tmp_path, _RECORDING_POETRY, *target.split())
    assert run.returncode == 0, run.stdout + run.stderr
    calls = log.read_text(encoding="utf-8").splitlines()
    assert "run python -c import mutmut" in calls
    assert calls[-1] == expected


@pytest.mark.parametrize(
    "target",
    [
        "MODULE=openfollow.zones.engine mutation-module",
        "MUTANT=openfollow.zones.engine.x_update__mutmut_1 mutation-show",
        "mutation-results",
    ],
    ids=["module", "show", "results"],
)
def test_mutation_targets_stop_before_mutmut_when_it_is_missing(tmp_path: Path, target: str) -> None:
    failing = '#!/bin/sh\necho "$*" >> "$POETRY_LOG"\nexit 1\n'
    run, log = _make(tmp_path, failing, *target.split())
    assert run.returncode != 0, run.stdout + run.stderr
    assert "mutmut could not be imported" in run.stdout + run.stderr
    assert not [call for call in log.read_text(encoding="utf-8").splitlines() if call.startswith("run mutmut")]
