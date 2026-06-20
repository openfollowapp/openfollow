# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Regression checks for scripts/install-ndi.sh libndi runtime selection."""

from __future__ import annotations

import inspect
import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _install_ndi_path() -> Path:
    source = inspect.getsourcefile(_install_ndi_path)
    assert source, "Could not resolve current test source path"
    return Path(source).resolve().parents[1] / "scripts" / "install-ndi.sh"


def _libndi_selection_snippet() -> str:
    """The two ``ndi_real="$(find ...)"`` lines that choose which libndi to install."""
    lines = _install_ndi_path().read_text().splitlines()
    sel = [ln for ln in lines if 'ndi_real="$(find' in ln]
    assert len(sel) == 2, f"expected 2 ndi_real selection lines, found {len(sel)}"
    return "\n".join(sel)


def _sort_v_supported() -> bool:
    return subprocess.run(["bash", "-c", "printf 'a\\n' | sort -V >/dev/null 2>&1"]).returncode == 0


def _make_fake_sdk(root: Path) -> Path:
    """Mirror the real Linux NDI SDK layout and return the real shared object.

    lib/ ships the real ``libndi.so.N.N.N`` plus its SONAME/dev symlinks; bin/
    ships only a relative cross-directory SONAME symlink – the trap the old
    ``find | sort | head -1`` selection fell into (bin/ sorts before lib/).
    """
    sdk = root / "NDI SDK for Linux"
    libdir = sdk / "lib" / "aarch64-rpi4-linux-gnueabi"
    bindir = sdk / "bin" / "aarch64-rpi4-linux-gnueabi"
    libdir.mkdir(parents=True)
    bindir.mkdir(parents=True)
    real = libdir / "libndi.so.6.3.2"
    real.write_bytes(b"\x7fELF fake")
    (libdir / "libndi.so.6").symlink_to("libndi.so.6.3.2")
    (libdir / "libndi.so").symlink_to("libndi.so.6.3.2")
    (bindir / "libndi.so.6").symlink_to("../../lib/aarch64-rpi4-linux-gnueabi/libndi.so.6.3.2")
    return real


@pytest.mark.skipif(not _sort_v_supported(), reason="host sort lacks -V")
def test_install_ndi_selects_real_lib_not_bin_symlink(tmp_path: Path) -> None:
    """install-ndi.sh must install the real shared object from the SDK's lib/
    dir, never the bin/ dir's cross-directory symlink – copying the latter into
    /usr/local/lib lands a dangling link, leaving libndi unresolvable at runtime
    even though ndisrc (which dlopens libndi lazily) still loads."""
    real = _make_fake_sdk(tmp_path)
    script = f'{_libndi_selection_snippet()}\nprintf "%s" "$ndi_real"\n'
    result = subprocess.run(
        ["bash"],
        input=script,
        text=True,
        capture_output=True,
        env={"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path), "ARCH": "aarch64"},
    )
    assert result.returncode == 0, result.stderr
    selected = result.stdout.strip()
    assert selected == str(real), f"selected {selected!r}, expected {real}"
    assert "/bin/" not in selected, f"selected the bin/ symlink: {selected}"


def _latest_libndi_line() -> str:
    """The ``latest="$(ls -1 ...)"`` line that backs the unversioned libndi.so dev
    link on the already-present path."""
    lines = _install_ndi_path().read_text().splitlines()
    sel = [ln for ln in lines if 'latest="$(ls -1' in ln]
    assert len(sel) == 1, f"expected 1 'latest=' line, found {len(sel)}"
    return sel[0]


@pytest.mark.skipif(not _sort_v_supported(), reason="host sort lacks -V")
def test_install_ndi_already_present_links_newest_version(tmp_path: Path) -> None:
    """On the already-present path, a prior install that left only versioned
    libndi.so.N gets an unversioned libndi.so dev link (the app's discovery looks
    for that file) pointed at the NEWEST version, not an arbitrary one."""
    for name in ("libndi.so.6", "libndi.so.6.1.1", "libndi.so.6.3.2"):
        (tmp_path / name).write_text("")
    line = _latest_libndi_line().replace("/usr/local/lib", str(tmp_path))
    result = subprocess.run(
        ["bash"],
        input=f'{line}\nprintf "%s" "$latest"\n',
        text=True,
        capture_output=True,
        env={"PATH": os.environ.get("PATH", "")},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(tmp_path / "libndi.so.6.3.2")
