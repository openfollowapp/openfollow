# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Guard tests for the image root/boot UUID rewrite.

The default rpi-image-gen layout references the root and boot filesystems by
``/dev/disk/by-slot/*`` symlinks, created by an ``RPI_ONBOOTDEV``-gated udev
rule that has to resolve inside the initramfs. On a Pi 5 that does not happen in
time and boot drops to the initramfs rescue shell. Two SRCROOT hooks fix this:
``pre-image.sh`` chains ``openfollow-root-ref.sh`` onto each genimage exec-pre,
and ``openfollow-root-ref.sh`` rewrites the by-slot references to filesystem
UUIDs.

The image build is config-only (no Python runtime), so ``make ci`` can't boot a
flashed unit - these tests drive the two shell scripts directly and assert the
rewrite happens, the by-slot reference never survives, and a changed upstream
format fails the build loudly instead of silently shipping a by-slot image.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_IMAGE_DIR = Path(__file__).resolve().parents[1] / "packaging" / "image"
_ROOT_REF = _IMAGE_DIR / "openfollow-root-ref.sh"
_PRE_IMAGE = _IMAGE_DIR / "pre-image.sh"

_ROOT_UUID = "0123abcd-1111-2222-3333-0123456789ab"
_BOOT_UUID = "ABCD-1234"

# The two by-slot lines the layout's setup.sh writes into /etc/fstab.
_FSTAB_BY_SLOT = (
    "/dev/disk/by-slot/system  /  ext4 rw,relatime,errors=remount-ro,commit=30 0 1\n"
    "/dev/disk/by-slot/boot  /boot/firmware  vfat defaults,rw,noatime,errors=remount-ro 0 2\n"
)
# A realistic cmdline.txt after setup.sh (root=by-slot) plus our layer's
# console/quiet edits, so the test also pins that the tail is preserved.
_CMDLINE_BY_SLOT = (
    "console=serial0,115200 console=tty3 root=/dev/disk/by-slot/system "
    "fsck.repair=yes rootwait quiet loglevel=0 logo.nologo "
    "vt.global_cursor_default=0 systemd.show_status=0\n"
)

requires_sh = pytest.mark.skipif(shutil.which("sh") is None, reason="no POSIX sh available")


def _write_uuids(outdir: Path) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "img_uuids").write_text(f'BOOT_UUID="{_BOOT_UUID}"\nROOT_UUID="{_ROOT_UUID}"\n', encoding="utf-8")


def _run_root_ref(label: str, mnt: Path, outdir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(_ROOT_REF), label, str(outdir)],
        env={**os.environ, "IMAGEMOUNTPATH": str(mnt)},
        capture_output=True,
        text=True,
    )


def _genimage_cfg(setup: str = "'/abs/simple_dual/setup.sh'", boot: str = "BOOT", root: str = "ROOT") -> str:
    return (
        "image boot.vfat {\n"
        '   vfat { label = "BOOT" }\n'
        '   mountpoint = "/boot/firmware"\n'
        f'   exec-pre = "{setup} {boot}"\n'
        "}\n"
        "image root.ext4 {\n"
        '   ext4 { label = "ROOT" }\n'
        '   mountpoint = "/"\n'
        f'   exec-pre = "{setup} {root}"\n'
        "}\n"
    )


# --- script hygiene ---------------------------------------------------------


@pytest.mark.parametrize("script", [_ROOT_REF, _PRE_IMAGE])
def test_scripts_are_executable_sh(script: Path) -> None:
    assert script.read_text(encoding="utf-8").startswith("#!/bin/sh\n")
    assert os.access(script, os.X_OK), f"{script.name} must be committed executable"


@requires_sh
@pytest.mark.parametrize("script", [_ROOT_REF, _PRE_IMAGE])
def test_scripts_are_sh_syntax_clean(script: Path) -> None:
    proc = subprocess.run(["sh", "-n", str(script)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


# --- openfollow-root-ref.sh: the rewrite ------------------------------------


@requires_sh
def test_root_label_rewrites_fstab_to_uuid(tmp_path: Path) -> None:
    mnt = tmp_path / "mnt"
    (mnt / "etc").mkdir(parents=True)
    (mnt / "etc" / "fstab").write_text(_FSTAB_BY_SLOT, encoding="utf-8")
    _write_uuids(tmp_path / "out")

    proc = _run_root_ref("ROOT", mnt, tmp_path / "out")
    assert proc.returncode == 0, proc.stderr

    fstab = (mnt / "etc" / "fstab").read_text(encoding="utf-8")
    assert "by-slot" not in fstab
    assert f"UUID={_ROOT_UUID}" in fstab
    assert f"UUID={_BOOT_UUID}" in fstab
    # Mount points and options are untouched - only the device column changed.
    assert "/boot/firmware" in fstab
    assert "errors=remount-ro,commit=30 0 1" in fstab


@requires_sh
def test_boot_label_rewrites_cmdline_and_preserves_tail(tmp_path: Path) -> None:
    mnt = tmp_path / "mnt"
    mnt.mkdir()
    (mnt / "cmdline.txt").write_text(_CMDLINE_BY_SLOT, encoding="utf-8")
    _write_uuids(tmp_path / "out")

    proc = _run_root_ref("BOOT", mnt, tmp_path / "out")
    assert proc.returncode == 0, proc.stderr

    cmdline = (mnt / "cmdline.txt").read_text(encoding="utf-8")
    assert "by-slot" not in cmdline
    assert f"root=UUID={_ROOT_UUID}" in cmdline
    # The kiosk console/quiet edits and rootwait must survive the rewrite.
    assert "console=tty3" in cmdline
    assert "rootwait" in cmdline
    assert "systemd.show_status=0" in cmdline


@requires_sh
def test_root_ref_fails_when_uuids_missing(tmp_path: Path) -> None:
    mnt = tmp_path / "mnt"
    mnt.mkdir()
    (mnt / "cmdline.txt").write_text(_CMDLINE_BY_SLOT, encoding="utf-8")
    # No img_uuids written - must fail loudly, never ship a by-slot image.
    proc = _run_root_ref("BOOT", mnt, tmp_path / "out")
    assert proc.returncode != 0


@requires_sh
def test_root_ref_rejects_unknown_label(tmp_path: Path) -> None:
    mnt = tmp_path / "mnt"
    mnt.mkdir()
    _write_uuids(tmp_path / "out")
    proc = _run_root_ref("BOGUS", mnt, tmp_path / "out")
    assert proc.returncode != 0


# --- pre-image.sh: the chaining ---------------------------------------------


@requires_sh
def test_pre_image_chains_patch_for_both_partitions(tmp_path: Path) -> None:
    outdir = tmp_path / "out"
    outdir.mkdir()
    (outdir / "genimage.cfg").write_text(_genimage_cfg(), encoding="utf-8")

    proc = subprocess.run(["sh", str(_PRE_IMAGE), "/fake/target", str(outdir)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr

    cfg = (outdir / "genimage.cfg").read_text(encoding="utf-8")
    for label in ("BOOT", "ROOT"):
        # Upstream setup.sh still runs first, then our patch is chained after it.
        assert f"setup.sh' {label} && '" in cfg
        assert f"openfollow-root-ref.sh' {label} '{outdir}'" in cfg


@requires_sh
def test_pre_image_is_idempotent(tmp_path: Path) -> None:
    outdir = tmp_path / "out"
    outdir.mkdir()
    (outdir / "genimage.cfg").write_text(_genimage_cfg(), encoding="utf-8")

    for _ in range(2):
        proc = subprocess.run(["sh", str(_PRE_IMAGE), "/fake/target", str(outdir)], capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr

    cfg = (outdir / "genimage.cfg").read_text(encoding="utf-8")
    assert cfg.count("openfollow-root-ref.sh") == 2


@requires_sh
def test_pre_image_fails_on_changed_exec_pre_format(tmp_path: Path) -> None:
    outdir = tmp_path / "out"
    outdir.mkdir()
    # An exec-pre that no longer ends in BOOT/ROOT - the chain can't land, so the
    # build must fail rather than silently emit a by-slot image.
    (outdir / "genimage.cfg").write_text(_genimage_cfg(boot="FOO", root="BAR"), encoding="utf-8")

    proc = subprocess.run(["sh", str(_PRE_IMAGE), "/fake/target", str(outdir)], capture_output=True, text=True)
    assert proc.returncode != 0


@requires_sh
def test_pre_image_fails_when_genimage_cfg_absent(tmp_path: Path) -> None:
    outdir = tmp_path / "out"
    outdir.mkdir()
    proc = subprocess.run(["sh", str(_PRE_IMAGE), "/fake/target", str(outdir)], capture_output=True, text=True)
    assert proc.returncode != 0
