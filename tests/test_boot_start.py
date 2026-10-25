# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The session starts once the network is online: the splash is stopped after its minimum time, and no unit sleeps
a fixed time before the session."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from openfollow.privilege.systemd_unit import render_service_unit

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent
_STOP_SPLASH = _REPO_ROOT / "packaging" / "debian" / "stop-splash.sh"
_DEB_UNIT = _REPO_ROOT / "packaging" / "debian" / "openfollow.service"
_SOURCE_UNIT = _REPO_ROOT / "config" / "openfollow.service"
_BUILD_DEB = _REPO_ROOT / "packaging" / "build-deb.sh"
_NOW_S = 42.0


@pytest.fixture(params=["sh", "dash"])
def shell(request: pytest.FixtureRequest) -> str:
    """The station's ``/bin/sh`` is dash; run under the host's ``sh`` too."""
    path = "/bin/sh" if request.param == "sh" else shutil.which("dash")
    if path is None:
        pytest.skip("no dash on this host")
    return path


def _stop_splash(tmp_path: Path, shell: str, state: str, started_us: str, *args: str) -> tuple[list[str], list[str]]:
    """Run the splash step with ``systemctl`` and ``sleep`` stubbed, ``_NOW_S`` seconds after boot.

    Returns the ``sleep`` arguments and the ``systemctl`` calls other than ``show``.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    clock = tmp_path / "uptime"
    clock.write_text(f"{_NOW_S:.2f} 0.00\n", encoding="utf-8")
    stubs = {
        "systemctl": (
            'if [ "$1" = show ]; then\n'
            f'  case "$3" in ActiveState) echo "{state}" ;;\n'
            f'  ActiveEnterTimestampMonotonic) echo "{started_us}" ;; esac\n'
            "  exit 0\n"
            "fi\n"
            f'echo "$*" >> "{tmp_path}/calls"\n'
        ),
        "sleep": f'echo "$1" >> "{tmp_path}/sleeps"\n',
    }
    for name, body in stubs.items():
        stub = bin_dir / name
        stub.write_text("#!/bin/sh\n" + body, encoding="utf-8")
        stub.chmod(0o755)
    result = subprocess.run(
        [shell, str(_STOP_SPLASH), *args],
        env={"PATH": f"{bin_dir}:/usr/bin:/bin", "OPENFOLLOW_UPTIME": str(clock)},
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr

    def lines(name: str) -> list[str]:
        path = tmp_path / name
        return path.read_text(encoding="utf-8").split("\n")[:-1] if path.exists() else []

    return lines("sleeps"), lines("calls")


def _started(seconds_ago: float) -> str:
    return str(round((_NOW_S - seconds_ago) * 1_000_000))


_STOP = ["stop openfollow-splash.service"]


@pytest.mark.parametrize(
    ("seconds_ago", "sleeps"),
    [
        pytest.param(10.0, [], id="up-long-ago"),
        pytest.param(1.5, [], id="up-exactly-the-minimum"),
        pytest.param(0.5, ["1.000"], id="half-a-second-up"),
        pytest.param(0.0, ["1.500"], id="just-started"),
        pytest.param(-5.0, ["1.500"], id="start-time-ahead-of-the-clock"),
    ],
)
def test_the_splash_stops_once_it_has_been_up_its_minimum(
    tmp_path: Path, shell: str, seconds_ago: float, sleeps: list[str]
) -> None:
    """On a normal boot the network takes longer than the minimum, so the step stops the splash at once; it never
    waits longer than the minimum, whatever the start time reads."""
    assert _stop_splash(tmp_path, shell, "active", _started(seconds_ago), "1.5") == (sleeps, _STOP)


def test_the_splash_minimum_defaults_to_one_and_a_half_seconds(tmp_path: Path, shell: str) -> None:
    assert _stop_splash(tmp_path, shell, "active", _started(0.0)) == (["1.500"], _STOP)


def test_the_splash_minimum_is_the_argument(tmp_path: Path, shell: str) -> None:
    assert _stop_splash(tmp_path, shell, "active", _started(1.0), "3") == (["2.000"], _STOP)


@pytest.mark.parametrize(
    "state", ["inactive", "failed", "deactivating", ""], ids=["inactive", "failed", "stopping", "unknown"]
)
def test_a_splash_that_is_not_running_is_left_alone_without_waiting(tmp_path: Path, shell: str, state: str) -> None:
    """A restart of the unit, or a station without a display: nothing to stop, nothing to wait for."""
    assert _stop_splash(tmp_path, shell, state, _started(0.0), "1.5") == ([], [])


@pytest.mark.parametrize("started_us", ["", "n/a", "12.5", "-3"], ids=["empty", "text", "fraction", "negative"])
def test_an_unreadable_start_time_stops_the_splash_at_once(tmp_path: Path, shell: str, started_us: str) -> None:
    assert _stop_splash(tmp_path, shell, "active", started_us, "1.5") == ([], _STOP)


def _exec_start_pre(unit_text: str) -> list[str]:
    return re.findall(r"^ExecStartPre=(.*)$", unit_text, re.MULTILINE)


def test_the_deb_unit_stops_the_splash_as_root_and_sleeps_nowhere() -> None:
    """Both fixed waits are gone: the splash step waits only as long as the splash still needs, and USB devices
    need none, the unit coming after udev settles and the inputs picking up later devices on hotplug."""
    text = _DEB_UNIT.read_text(encoding="utf-8")
    assert _exec_start_pre(text) == ["+-/usr/share/openfollow/stop-splash.sh 1.5"]
    assert "sleep" not in "".join(re.findall(r"^Exec.*$", text, re.MULTILINE))
    after = re.search(r"^After=(.*)$", text, re.MULTILINE)
    assert after is not None and "systemd-udev-settle.service" in after.group(1).split()


def test_the_source_install_units_do_not_sleep_for_usb_devices() -> None:
    rendered = render_service_unit(user="bob", uid=1001, repo_dir=Path("/x"), poetry_bin=Path("/y"))
    for text in (rendered, _SOURCE_UNIT.read_text(encoding="utf-8")):
        assert not any(line.startswith("/bin/sleep") for line in _exec_start_pre(text)), text
        after = re.search(r"^After=(.*)$", text, re.MULTILINE)
        assert after is not None and "systemd-udev-settle.service" in after.group(1).split()


def test_the_deb_installs_the_splash_step_executable() -> None:
    text = _BUILD_DEB.read_text(encoding="utf-8")
    assert re.search(r'install -m 0755 "\$DEBIAN_DIR/stop-splash\.sh"\s+"\$SHARE/stop-splash\.sh"', text)
