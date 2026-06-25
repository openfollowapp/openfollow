# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Regression checks for scripts/install-ndi.sh libndi runtime selection."""

from __future__ import annotations

import inspect
import os
import shlex
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _install_ndi_path() -> Path:
    source = inspect.getsourcefile(_install_ndi_path)
    assert source, "Could not resolve current test source path"
    return Path(source).resolve().parents[1] / "scripts" / "install-ndi.sh"


def _diagnostics_block() -> str:
    """The clock / index diagnostic helper defs, between their marker comments.

    install-ndi.sh runs procedurally on source (no main guard), so the pure
    helpers are tested by sourcing just this delimited block.
    """
    text = _install_ndi_path().read_text()
    start = text.index("# === clock / index diagnostics")
    end = text.index("# === end diagnostics")
    block = text[start:end]
    assert "clock_skew_in()" in block and "broken_index_in()" in block
    return block


def _run_diag(snippet: str) -> subprocess.CompletedProcess[str]:
    code = f"{_diagnostics_block()}\n{snippet}\n"
    return subprocess.run(
        ["bash", "-c", code],
        text=True,
        capture_output=True,
        env={"PATH": os.environ.get("PATH", "")},
    )


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


# --- clock-skew / stale-index diagnostics -----------------------------------


@pytest.mark.parametrize(
    "sample",
    [
        # The exact sqv wording from a Pi whose clock is behind real time.
        "Sub-process /usr/bin/sqv returned an error code (1) … Not live until 2026-06-25T01:42:42Z",
        "Verifying signature: not yet valid",
    ],
)
def test_clock_skew_in_detects_future_signature(sample: str) -> None:
    r = _run_diag(f"printf '%s' {shlex.quote(sample)} | clock_skew_in && echo SKEW || echo CLEAN")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "SKEW"


@pytest.mark.parametrize(
    "sample",
    [
        "Reading package lists... Done",
        # An unreachable mirror is NOT a clock problem – it must fall through to
        # the cached-index path, not die.
        "Failed to fetch http://deb.debian.org/debian … Could not connect",
    ],
)
def test_clock_skew_in_ignores_unrelated_update_failures(sample: str) -> None:
    r = _run_diag(f"printf '%s' {shlex.quote(sample)} | clock_skew_in && echo SKEW || echo CLEAN")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "CLEAN"


@pytest.mark.parametrize(
    "sample",
    [
        "E: Unable to correct problems, you have held broken packages.",
        "The following packages have unmet dependencies:",
    ],
)
def test_broken_index_in_detects_dependency_conflicts(sample: str) -> None:
    r = _run_diag(f"printf '%s' {shlex.quote(sample)} | broken_index_in && echo BROKEN || echo CLEAN")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "BROKEN"


def test_diagnostic_messages_are_actionable() -> None:
    skew = _run_diag("clock_skew_message")
    assert "clock" in skew.stdout.lower()
    assert "timedatectl" in skew.stdout and "date -s" in skew.stdout
    broken = _run_diag("broken_index_message")
    assert "full-upgrade" in broken.stdout


def test_apt_failures_are_wired_to_the_diagnostics() -> None:
    text = _install_ndi_path().read_text()
    # Both the update and install failure paths must consult the diagnostics so a
    # wrong clock / stale index surfaces as a clear message, not raw apt output.
    assert text.count('die "$(clock_skew_message)"') >= 2
    assert 'die "$(broken_index_message)"' in text
    assert "clock_skew_in <" in text and "broken_index_in <" in text
