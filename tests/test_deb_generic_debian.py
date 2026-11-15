# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Michel Honold and Paul Hermann
"""The .deb on a generic Debian 13 host: network stack and desktop hint."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import openfollow

pytestmark = pytest.mark.unit

_DEBIAN = Path(openfollow.__file__).resolve().parent.parent / "packaging" / "debian"
_POSTINST = _DEBIAN / "postinst"
_STUBS = ("getent", "adduser", "udevadm", "install", "chown", "loginctl")


def _control_field(name: str) -> list[str]:
    control = _DEBIAN / "control.in"
    if not control.is_file():
        pytest.skip("no debian/control.in in this tree")
    for line in control.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{name}:"):
            return [dep.strip() for dep in line.split(":", 1)[1].split(",")]
    return []


def test_deb_adds_no_recommends() -> None:
    """An update installs a new Recommends, and an offline station cannot
    download one, so the update fails; optional packages stay out of it."""
    assert _control_field("Recommends") == ["gir1.2-webkit2-4.1"]


def _run_postinst(tmp_path: Path, *, default_target: str, dm_load_state: str) -> str:
    """Run ``postinst configure`` on stubs; return its stderr."""
    if not _POSTINST.is_file():
        pytest.skip("no packaging/debian/postinst in this tree")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in _STUBS:
        (bin_dir / name).write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (bin_dir / "systemctl").write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        f'  "get-default") echo {default_target} ;;\n'
        f'  "show -P LoadState display-manager.service") echo {dm_load_state} ;;\n'
        '  "show -P Id display-manager.service") echo gdm3.service ;;\n'
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    for stub in bin_dir.iterdir():
        stub.chmod(0o755)
    result = subprocess.run(
        ["/bin/sh", str(_POSTINST), "configure"],
        env={"PATH": str(bin_dir)},
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return result.stderr


def test_postinst_names_a_desktop_that_starts_at_boot(tmp_path: Path) -> None:
    stderr = _run_postinst(tmp_path, default_target="graphical.target", dm_load_state="loaded")
    assert "desktop (gdm3)" in stderr
    assert "systemctl set-default multi-user.target" in stderr


@pytest.mark.parametrize(
    ("default_target", "dm_load_state"),
    [
        ("multi-user.target", "loaded"),  # desktop installed, boots to the console
        ("graphical.target", "not-found"),  # graphical default, no display manager
    ],
)
def test_postinst_stays_quiet_without_a_desktop_at_boot(
    tmp_path: Path, default_target: str, dm_load_state: str
) -> None:
    assert "desktop" not in _run_postinst(tmp_path, default_target=default_target, dm_load_state=dm_load_state)
