# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The ``make mutation*`` targets stop before touching anything when mutmut is missing."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_MAKEFILE = Path(__file__).resolve().parents[1] / "Makefile"

# Every ``poetry`` call fails, as ``poetry run python -c "import mutmut"`` does without the
# group; the delay lets a target that clears in parallel do so before the check fails.
_FAILING_POETRY = "#!/bin/sh\nsleep 0.5\nexit 1\n"


@pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")
@pytest.mark.parametrize("jobs", ["-j1", "-j4"])
def test_mutation_keeps_the_previous_results_when_mutmut_is_missing(tmp_path: Path, jobs: str) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    poetry = bin_dir / "poetry"
    poetry.write_text(_FAILING_POETRY, encoding="utf-8")
    poetry.chmod(0o755)
    previous = tmp_path / "mutants" / "previous-run"
    previous.parent.mkdir()
    previous.write_text("results", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in {"MAKEFLAGS", "MFLAGS", "MAKELEVEL"}}
    env["PATH"] = os.pathsep.join([str(bin_dir), env.get("PATH", "")])

    run = subprocess.run(
        ["make", "--no-print-directory", "-f", str(_MAKEFILE), jobs, "mutation"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert run.returncode != 0, run.stdout + run.stderr
    assert "mutmut is not installed" in run.stdout + run.stderr
    assert previous.is_file(), "the previous mutation results were deleted before the mutmut check failed"
