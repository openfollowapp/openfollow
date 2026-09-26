# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""``packaging/debian/apply-update.sh`` run for real against stub ``apt-get`` /
``systemctl`` commands, so what it asks apt to do is checked, not just how it
is launched."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

import openfollow

pytestmark = pytest.mark.unit

_SCRIPT = Path(openfollow.__file__).resolve().parent.parent / "packaging" / "debian" / "apply-update.sh"
_STATE_LINE = "STATE_FILE=/var/lib/openfollow/update-state.json"


def _run(tmp_path: Path, *, apt_exit: int = 0, apt_output: str = "", systemctl_exit: int = 0) -> tuple[list[str], dict]:
    if not _SCRIPT.is_file():
        pytest.skip("no packaging/debian/apply-update.sh in this tree")
    source = _SCRIPT.read_text(encoding="utf-8")
    assert source.count(_STATE_LINE) == 1
    state = tmp_path / "update-state.json"
    # The real path is the live station's state dir, which the gate host has.
    script = tmp_path / "apply-update.sh"
    script.write_text(source.replace(_STATE_LINE, f"STATE_FILE={state}"), encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls"
    for name, code, output in (("apt-get", apt_exit, apt_output), ("systemctl", systemctl_exit, "")):
        stub = bin_dir / name
        stub.write_text(
            f'#!/bin/sh\necho "{name} $*" >> "{calls}"\nprintf "%s\\n" "{output}"\nexit {code}\n',
            encoding="utf-8",
        )
        stub.chmod(0o755)

    # The script refuses any package outside this prefix.
    fd, spec = tempfile.mkstemp(prefix="openfollow-update-", suffix=".deb", dir="/tmp")
    os.close(fd)
    try:
        subprocess.run(
            ["/bin/sh", str(script), spec],
            env={**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"},
            check=False,
            timeout=30,
        )
    finally:
        spec_left = Path(spec).exists()
        Path(spec).unlink(missing_ok=True)
    assert not spec_left, "the staged package must be removed whatever the outcome"
    return calls.read_text(encoding="utf-8").splitlines(), json.loads(state.read_text(encoding="utf-8"))


def test_installs_even_when_that_version_is_already_installed(tmp_path: Path) -> None:
    # Without --reinstall apt skips a package at the installed version and still
    # exits 0, so a same-version offline install reported success and changed nothing.
    calls, state = _run(tmp_path)
    apt = next(line for line in calls if line.startswith("apt-get "))
    assert "--reinstall" in apt.split()
    assert "--allow-downgrades" in apt.split()
    assert state["state"] == "restarting"


def test_starts_the_service_without_cycling_the_instance_postinst_started(tmp_path: Path) -> None:
    # prerm stops the unit and postinst starts it again, so a restart here would
    # stop the new version while it is still starting.
    calls, _state = _run(tmp_path)
    systemctl = [line for line in calls if line.startswith("systemctl ")]
    assert systemctl == ["systemctl start openfollow.service"]


def test_a_failed_start_is_reported(tmp_path: Path) -> None:
    _calls, state = _run(tmp_path, systemctl_exit=1)
    assert state["state"] == "failed"
    assert state["message"] == "Start failed. Service may need manual attention."


def test_a_failed_install_reports_apts_error_and_skips_the_restart(tmp_path: Path) -> None:
    calls, state = _run(tmp_path, apt_exit=100, apt_output="E: Sub-process /usr/bin/dpkg returned an error code (1)")
    assert state["state"] == "failed"
    assert "E: Sub-process /usr/bin/dpkg returned an error code (1)" in state["error"]
    assert not any(line.startswith("systemctl") for line in calls)
