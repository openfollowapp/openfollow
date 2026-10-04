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
    for name, code, output in (("apt-get", apt_exit, apt_output), ("systemctl", systemctl_exit, ""), ("dpkg", 0, "")):
        # ``systemctl start`` also logs the recorded state, so the order of the two can be checked.
        seen_at_start = f'[ "$1" = start ] && echo "state at start: $(cat "{state}")" >> "{calls}"\n'
        stub = bin_dir / name
        stub.write_text(
            f'#!/bin/sh\necho "{name} $*" >> "{calls}"\n'
            + (seen_at_start if name == "systemctl" else "")
            + f'printf "%s\\n" "{output}"\nexit {code}\n',
            encoding="utf-8",
        )
        stub.chmod(0o755)

    # The script refuses any package outside this prefix.
    fd, spec = tempfile.mkstemp(prefix="openfollow-update-", suffix=".deb", dir="/tmp")
    os.close(fd)
    try:
        result = subprocess.run(
            ["/bin/sh", str(script), spec],
            env={**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"},
            check=False,
            timeout=30,
        )
    finally:
        spec_left = Path(spec).exists()
        Path(spec).unlink(missing_ok=True)
    assert not spec_left, "the staged package must be removed whatever the outcome"
    recorded = json.loads(state.read_text(encoding="utf-8"))
    # The transient unit's exit status is what the journal shows support.
    assert (result.returncode != 0) == (recorded["state"] == "failed")
    return calls.read_text(encoding="utf-8").splitlines(), recorded


def test_installs_even_when_that_version_is_already_installed(tmp_path: Path) -> None:
    # Without --reinstall apt skips a package at the installed version and still
    # exits 0, so a same-version offline install reported success and changed nothing.
    calls, state = _run(tmp_path)
    apt = next(line for line in calls if line.startswith("apt-get "))
    assert "--reinstall" in apt.split()
    assert "--allow-downgrades" in apt.split()
    assert state["state"] == "restarting"


def test_a_conffile_the_operator_changed_never_stops_the_install(tmp_path: Path) -> None:
    """dpkg asks about a conffile changed on both sides, and nothing answers that
    prompt here: the install would end at it. The operator's version is kept and
    an unchanged one takes the update."""
    calls, _state = _run(tmp_path)
    apt = next(line for line in calls if line.startswith("apt-get ")).split()
    assert "Dpkg::Options::=--force-confdef" in apt
    assert "Dpkg::Options::=--force-confold" in apt
    assert "Dpkg::Options::=--force-confnew" not in apt


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


_DPKG_ERROR = "E: Sub-process /usr/bin/dpkg returned an error code (1)"


def test_a_failed_install_brings_the_station_back(tmp_path: Path) -> None:
    """prerm disabled the units and postinst never ran, so nothing else would start them."""
    calls, state = _run(tmp_path, apt_exit=100, apt_output=_DPKG_ERROR)
    assert state["state"] == "failed"
    assert state["message"] == "Update failed."
    assert _DPKG_ERROR in state["error"]
    recovery = [line for line in calls if not line.startswith(("apt-get ", "state at start"))]
    assert recovery == [
        "dpkg --force-confdef --force-confold --configure -a",
        "systemctl daemon-reload",
        "systemctl enable openfollow.service openfollow-splash.service",
        "systemctl start openfollow.service",
    ]


def test_the_failure_is_recorded_before_the_station_starts(tmp_path: Path) -> None:
    """A starting app that read ``running`` would take itself for the update and open What's new."""
    calls, _state = _run(tmp_path, apt_exit=100, apt_output=_DPKG_ERROR)
    (seen,) = [line for line in calls if line.startswith("state at start: ")]
    assert json.loads(seen.removeprefix("state at start: "))["state"] == "failed"


def test_a_station_that_cannot_start_again_says_so(tmp_path: Path) -> None:
    _calls, state = _run(tmp_path, apt_exit=100, apt_output=_DPKG_ERROR, systemctl_exit=1)
    assert state["state"] == "failed"
    assert state["message"] == "Update failed, and OpenFollow could not be started again."
    assert _DPKG_ERROR in state["error"]
