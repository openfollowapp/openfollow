# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Regression guard: pytest-xdist workers must not retitle themselves per test.

With setproctitle importable, xdist renames each worker twice per test. On
macOS every rename is a synchronous LaunchServices update that is broadcast to
each GUI process, and a parallel run sends tens of thousands of them: the
cursor, media keys and window switching stall for the whole run.
tests/conftest.py silences the rename; these tests pin that.

Each test runs a nested ``pytest -n 1`` with a stand-in ``setproctitle`` first
on ``PYTHONPATH`` that records every title it is handed, so nothing reaches the
real one and the check runs the same on Linux.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

pytest.importorskip("xdist")

_CONFTEST = Path(__file__).resolve().with_name("conftest.py")

_RECORDER = """\
import os

def _record(line):
    with open(os.environ["TITLE_LOG"], "a", encoding="utf-8") as log:
        log.write(line + "\\n")

_record("imported")

def setproctitle(title):
    _record(title)

def getproctitle():
    return ""
"""


def _worker_titles(tmp_path: Path, *, with_conftest: bool) -> list[str]:
    """Run one trivial test on one xdist worker; return the titles it was given."""
    shim = tmp_path / "shim"
    shim.mkdir()
    (shim / "setproctitle.py").write_text(_RECORDER)
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "pytest.ini").write_text("[pytest]\nmarkers =\n    unit\n")
    (suite / "test_probe.py").write_text("import pytest\n\n\n@pytest.mark.unit\ndef test_probe():\n    pass\n")
    if with_conftest:
        shutil.copy(_CONFTEST, suite / "conftest.py")
    log = tmp_path / "titles.log"
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "COV_CORE_"))}
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(shim), env.get("PYTHONPATH")]))
    env["TITLE_LOG"] = str(log)
    run = subprocess.run(
        [sys.executable, "-m", "pytest", "-n", "1", "-p", "no:cacheprovider", "-q", "test_probe.py"],
        cwd=suite,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    lines = log.read_text().splitlines()
    assert "imported" in lines, "the stand-in setproctitle was never imported"
    return [line for line in lines if line != "imported"]


def test_probe_sees_xdist_retitle_its_workers(tmp_path: Path) -> None:
    """Without the conftest, xdist hands the stand-in a title per test.

    Proves the probe observes what xdist sends, so an empty record in the
    guarded run means the rename was silenced, not that the probe saw nothing.
    """
    titles = _worker_titles(tmp_path, with_conftest=False)
    assert any(t.startswith("[pytest-xdist running]") and t.endswith("test_probe") for t in titles), titles


def test_conftest_keeps_xdist_workers_from_retitling(tmp_path: Path) -> None:
    assert _worker_titles(tmp_path, with_conftest=True) == []
