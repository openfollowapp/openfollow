# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""pytest-xdist workers must not retitle themselves per test.

A nested ``pytest -n 1`` runs with a stand-in ``setproctitle`` first on
``PYTHONPATH`` that records, per process, every title it is handed.
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
        log.write(f"{os.getpid()} {line}\\n")

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
    (suite / "test_probe.py").write_text(
        "import os\n\nimport pytest\n\n\n@pytest.mark.unit\ndef test_probe():\n"
        "    with open(os.environ['TITLE_LOG'], 'a', encoding='utf-8') as log:\n"
        "        log.write(f'{os.getpid()} worker\\n')\n"
    )
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
    records = [line.split(" ", 1) for line in log.read_text().splitlines()]
    workers = {pid for pid, what in records if what == "worker"}
    assert len(workers) == 1, records
    worker = workers.pop()
    # The controller imports it too, so only the worker's own import proves the probe.
    assert [worker, "imported"] in records, "the worker never imported the stand-in setproctitle"
    return [what for pid, what in records if pid == worker and what not in {"imported", "worker"}]


def test_probe_sees_xdist_retitle_its_workers(tmp_path: Path) -> None:
    """Control: without the conftest, the worker hands the stand-in a title per test."""
    titles = _worker_titles(tmp_path, with_conftest=False)
    assert any(t.startswith("[pytest-xdist running]") and t.endswith("test_probe") for t in titles), titles


def test_conftest_keeps_xdist_workers_from_retitling(tmp_path: Path) -> None:
    assert _worker_titles(tmp_path, with_conftest=True) == []
