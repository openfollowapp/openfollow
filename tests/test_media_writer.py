# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The removable-drive writer: its file name rule, exclusive write, and the root helper."""

from __future__ import annotations

import ast
import errno
import io
import json
import os
import runpy
import stat
import sys
from pathlib import Path

import pytest

import openfollow.privilege.media_writer as mw
from openfollow.privilege.media_writer import MediaWriteError, check_name, main, write_exclusive
from tests._lsblk_samples import HELPER_LIST, HELPER_TREE

pytestmark = pytest.mark.unit

# One table for every place the name rule is enforced.
NAME_CASES = [
    ("ofdiag-Stage_Left-20260928T101500Z.txt", True),
    ("Stage Left.ofsettings", False),
    ("Stage_Left.ofsettings", True),
    ("show.oftemplate", True),
    (".hidden.txt", False),
    ("../escape.txt", False),
    ("dir/file.txt", False),
    ("bundle.sh", False),
    ("bundle.txt.sh", False),
    ("bundle", False),
    ("ÜBER.txt", False),
    ("a" * 97 + ".txt", False),
    ("a" * 96 + ".txt", True),
    ("-leading-dash.txt", False),
]
NAME_IDS = [
    "diagnostics",
    "space",
    "settings",
    "template",
    "hidden",
    "traversal",
    "subdirectory",
    "script",
    "double-extension",
    "no-extension",
    "non-ascii",
    "too-long",
    "longest",
    "leading-dash",
]


@pytest.mark.parametrize(("name", "ok"), NAME_CASES, ids=NAME_IDS)
def test_the_name_rule(name: str, ok: bool) -> None:
    if ok:
        check_name(name)
    else:
        with pytest.raises(MediaWriteError) as exc:
            check_name(name)
        assert exc.value.code == mw.EXIT_BAD_NAME


class TestWriteExclusive:
    def test_writes_and_returns_the_name(self, tmp_path: Path) -> None:
        assert write_exclusive(tmp_path, "b.txt", b"\x00data") == "b.txt"
        assert (tmp_path / "b.txt").read_bytes() == b"\x00data"

    def test_never_overwrites_a_clash_takes_a_number(self, tmp_path: Path) -> None:
        (tmp_path / "b.txt").write_bytes(b"old")
        (tmp_path / "b-1.txt").write_bytes(b"old")
        assert write_exclusive(tmp_path, "b.txt", b"new") == "b-2.txt"
        assert (tmp_path / "b.txt").read_bytes() == b"old"

    def test_every_name_taken(self, tmp_path: Path) -> None:
        (tmp_path / "b.txt").write_bytes(b"")
        for n in range(1, 100):
            (tmp_path / f"b-{n}.txt").write_bytes(b"")
        with pytest.raises(MediaWriteError) as exc:
            write_exclusive(tmp_path, "b.txt", b"x")
        assert exc.value.code == mw.EXIT_NAME_TAKEN

    @pytest.mark.parametrize(
        ("err", "code", "text"),
        [
            (errno.ENOSPC, mw.EXIT_FULL, "The USB storage device is full."),
            (errno.EROFS, mw.EXIT_WRITE_FAILED, "The USB storage device is read-only."),
            (errno.EIO, mw.EXIT_WRITE_FAILED, "The file could not be written to the USB storage device."),
        ],
        ids=["full", "read-only", "io-error"],
    )
    def test_a_failed_write_leaves_no_partial_file(self, tmp_path: Path, monkeypatch, err, code, text) -> None:  # noqa: ANN001
        def _fail(_fd: int, _data: object) -> int:
            raise OSError(err, os.strerror(err))

        monkeypatch.setattr(mw.os, "write", _fail)
        with pytest.raises(MediaWriteError) as exc:
            write_exclusive(tmp_path, "b.txt", b"x")
        assert (exc.value.code, str(exc.value)) == (code, text)
        assert list(tmp_path.iterdir()) == []

    def test_a_partial_file_that_cannot_be_removed_still_reports_the_failure(self, tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
        def _full(_fd: int, _data: object) -> int:
            raise OSError(errno.ENOSPC, "full")

        def _refuse(_self: Path) -> None:
            raise PermissionError("read-only")

        monkeypatch.setattr(mw.os, "write", _full)
        monkeypatch.setattr(Path, "unlink", _refuse)
        with pytest.raises(MediaWriteError) as exc:
            write_exclusive(tmp_path, "b.txt", b"x")
        assert exc.value.code == mw.EXIT_FULL

    def test_a_directory_that_refuses_the_file(self, tmp_path: Path) -> None:
        with pytest.raises(MediaWriteError) as exc:
            write_exclusive(tmp_path / "gone", "b.txt", b"x")
        assert exc.value.code == mw.EXIT_WRITE_FAILED

    def test_a_short_write_is_continued(self, tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
        real_write = os.write
        monkeypatch.setattr(mw.os, "write", lambda fd, data: real_write(fd, bytes(data[:2])))
        write_exclusive(tmp_path, "b.txt", b"abcdef")
        assert (tmp_path / "b.txt").read_bytes() == b"abcdef"


# --- the root helper ------------------------------------------------------------------------------


def _disk(
    path: str, tran: str | None, *parts: dict, mountpoints: list | None = None, fstype: str | None = None
) -> dict:
    return {
        "path": path,
        "type": "disk",
        "tran": tran,
        "fstype": fstype,
        "mountpoints": mountpoints or [None],
        "children": list(parts),
    }


def _part(path: str, fstype: str | None = "vfat", mountpoints: list | None = None) -> dict:
    return {"path": path, "type": "part", "tran": None, "fstype": fstype, "mountpoints": mountpoints or [None]}


_STATION = [
    _disk(
        "/dev/mmcblk0",
        "mmc",
        _part("/dev/mmcblk0p1", "vfat", ["/boot/firmware"]),
        _part("/dev/mmcblk0p2", "ext4", ["/"]),
    ),
    _disk("/dev/nvme0n1", "nvme", _part("/dev/nvme0n1p1", "ext4", ["/mnt/nvme"])),
]


def _exe(path: Path, body: str) -> str:
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


class _Host:
    """lsblk / mount / umount stand-ins: real executables that log their argv."""

    def __init__(self, tmp_path: Path, devices: list[dict], *, mount_rc: int = 0, umount_rc: int = 0) -> None:
        self.calls = tmp_path / "calls"
        self.calls.write_text("")
        tree = tmp_path / "lsblk.json"
        tree.write_text(json.dumps({"blockdevices": devices}))
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        self.run_dir = tmp_path / "run"
        self.lsblk = _exe(bin_dir / "lsblk", f'cat "{tree}"\n')
        self.mount = _exe(bin_dir / "mount", f'echo "mount $*" >> "{self.calls}"\nexit {mount_rc}\n')
        self.umount = _exe(bin_dir / "umount", f'echo "umount $*" >> "{self.calls}"\nexit {umount_rc}\n')

    def run(self, argv: list[str], payload: bytes) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        code = main(
            argv,
            stdin=io.BytesIO(payload),
            stdout=out,
            stderr=err,
            lsblk=self.lsblk,
            mount=self.mount,
            umount=self.umount,
            run_dir=self.run_dir,
        )
        return code, out.getvalue().strip(), err.getvalue().strip()

    def log(self) -> list[str]:
        return self.calls.read_text().splitlines()

    def written(self) -> dict[str, bytes]:
        """Files left in the (stub-mounted) directories, which a real unmount would have taken along."""
        return {p.name: p.read_bytes() for d in self.run_dir.iterdir() for p in d.iterdir()}


def _stick(fstype: str | None = "vfat", mountpoints: list | None = None) -> list[dict]:
    return [*_STATION, _disk("/dev/sda", "usb", _part("/dev/sda1", fstype, mountpoints))]


class TestRootHelper:
    def test_writes_and_unmounts_a_usb_drive(self, tmp_path: Path) -> None:
        host = _Host(tmp_path, _stick())
        code, out, err = host.run(["write", "/dev/sda1"], b"ofdiag-rig-20260928T101500Z.txt\n\x00bundle")
        assert (code, out, err) == (0, "ofdiag-rig-20260928T101500Z.txt", "")
        mount, umount = host.log()
        assert mount.startswith("mount -t vfat -o nosuid,nodev,noexec /dev/sda1 ")
        mountpoint = mount.rsplit(" ", 1)[1]
        assert Path(mountpoint).parent == host.run_dir
        assert umount == f"umount {mountpoint}"
        assert host.written() == {"ofdiag-rig-20260928T101500Z.txt": b"\x00bundle"}
        assert stat.S_IMODE(host.run_dir.stat().st_mode) == 0o700

    @pytest.mark.parametrize(("fstype", "driver"), [("exfat", "exfat"), ("ntfs", "ntfs3"), ("ext4", "ext4")])
    def test_each_format_mounts_with_its_own_driver(self, tmp_path: Path, fstype: str, driver: str) -> None:
        host = _Host(tmp_path, _stick(fstype))
        assert host.run(["write", "/dev/sda1"], b"b.txt\nx")[0] == 0
        assert host.log()[0].startswith(f"mount -t {driver} ")

    def test_a_drive_formatted_without_partitions(self, tmp_path: Path) -> None:
        host = _Host(tmp_path, [*_STATION, _disk("/dev/sdb", "usb", fstype="exfat")])
        assert host.run(["write", "/dev/sdb"], b"b.txt\nx")[0] == 0

    @pytest.mark.parametrize(
        "argv",
        [[], ["write"], ["read", "/dev/sda1"], ["write", "/dev/sda1", "extra"], ["write", "/dev/mmcblk0p1"],
         ["write", "/dev/sda1/../mmcblk0p2"], ["write", "/dev/nvme0n1p1"]],
        ids=["no-args", "no-device", "unknown-command", "extra-arg", "sd-slot", "traversal", "nvme"],
    )  # fmt: skip
    def test_refuses_bad_arguments(self, tmp_path: Path, argv: list[str]) -> None:
        host = _Host(tmp_path, _stick())
        code, _, err = host.run(argv, b"b.txt\nx")
        assert (code, err) == (mw.EXIT_USAGE, "usage: write-to-media write /dev/sdXN")
        assert host.log() == []

    @pytest.mark.parametrize(("name", "ok"), NAME_CASES, ids=NAME_IDS)
    def test_the_name_rule_holds_for_root_too(self, tmp_path: Path, name: str, ok: bool) -> None:
        host = _Host(tmp_path, _stick())
        code, _, _ = host.run(["write", "/dev/sda1"], name.encode() + b"\nx")
        assert code == (0 if ok else mw.EXIT_BAD_NAME)

    @pytest.mark.parametrize(
        "payload",
        [b"no-newline.txt", b"x" * 300 + b".txt\ndata"],
        ids=["no-name-line", "name-line-too-long"],
    )
    def test_refuses_a_malformed_request(self, tmp_path: Path, payload: bytes) -> None:
        host = _Host(tmp_path, _stick())
        assert host.run(["write", "/dev/sda1"], payload)[0] == mw.EXIT_BAD_NAME
        assert host.log() == []

    def test_refuses_a_file_over_the_cap(self, tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
        monkeypatch.setattr(mw, "MAX_BYTES", 4)
        host = _Host(tmp_path, _stick())
        code, _, err = host.run(["write", "/dev/sda1"], b"b.txt\n12345")
        assert (code, host.log()) == (mw.EXIT_TOO_LARGE, [])
        assert "larger than" in err

    @pytest.mark.parametrize(
        ("devices", "code", "text"),
        [
            ([*_STATION], mw.EXIT_NOT_REMOVABLE, "That USB storage device is no longer attached."),
            (
                [*_STATION, _disk("/dev/sda", "sata", _part("/dev/sda1"))],
                mw.EXIT_NOT_REMOVABLE,
                "That is not a removable USB storage device.",
            ),
            (
                [_disk("/dev/sda", "usb", _part("/dev/sda1"), _part("/dev/sda2", "ext4", ["/"]))],
                mw.EXIT_NOT_REMOVABLE,
                "That is not a removable USB storage device.",
            ),
            (_stick(mountpoints=["/media/pi/STICK"]), mw.EXIT_MOUNTED, "The USB storage device is already mounted."),
            (
                _stick("apfs"),
                mw.EXIT_UNSUPPORTED,
                "The USB storage device's format can't be written. Use FAT32, exFAT or NTFS.",
            ),
            (
                _stick(None),
                mw.EXIT_UNSUPPORTED,
                "The USB storage device's format can't be written. Use FAT32, exFAT or NTFS.",
            ),
        ],
        ids=["gone", "not-usb", "usb-root-disk", "already-mounted", "apfs", "no-filesystem"],
    )
    def test_checks_the_drive_itself(self, tmp_path: Path, devices: list[dict], code: int, text: str) -> None:
        host = _Host(tmp_path, devices)
        assert host.run(["write", "/dev/sda1"], b"b.txt\nx") == (code, "", text)
        assert host.log() == []

    @pytest.mark.parametrize("sample", [HELPER_TREE, HELPER_LIST], ids=["tree", "flat-list"])
    def test_a_real_stick_is_found_on_its_disk_whichever_shape_lsblk_prints(self, tmp_path: Path, sample: dict) -> None:
        host = _Host(tmp_path, sample["blockdevices"])
        assert host.run(["write", "/dev/sda2"], b"b.txt\nx")[0] == 0

    def test_a_flat_list_still_knows_the_stations_own_disk(self, tmp_path: Path) -> None:
        import copy

        devices = copy.deepcopy(HELPER_LIST["blockdevices"])
        next(d for d in devices if d["name"] == "sda2")["mountpoints"] = ["/"]
        host = _Host(tmp_path, devices)
        assert host.run(["write", "/dev/sda1"], b"b.txt\nx") == (
            mw.EXIT_NOT_REMOVABLE,
            "",
            "That is not a removable USB storage device.",
        )

    def test_a_flat_list_still_sees_a_mounted_partition(self, tmp_path: Path) -> None:
        import copy

        devices = copy.deepcopy(HELPER_LIST["blockdevices"])
        next(d for d in devices if d["name"] == "sda2")["mountpoints"] = ["/media/stick"]
        host = _Host(tmp_path, devices)
        assert host.run(["write", "/dev/sda2"], b"b.txt\nx")[0] == mw.EXIT_MOUNTED

    def test_an_unreadable_device_list_is_no_drive(self, tmp_path: Path) -> None:
        host = _Host(tmp_path, _stick())
        Path(host.lsblk).write_text("#!/bin/sh\necho not-json\n")
        assert host.run(["write", "/dev/sda1"], b"b.txt\nx")[0] == mw.EXIT_NOT_REMOVABLE

    def test_a_failed_mount_writes_nothing_and_leaves_no_directory(self, tmp_path: Path) -> None:
        host = _Host(tmp_path, _stick(), mount_rc=32)
        assert host.run(["write", "/dev/sda1"], b"b.txt\nx") == (
            mw.EXIT_MOUNT_FAILED,
            "",
            "The USB storage device could not be mounted.",
        )
        assert [line.split()[0] for line in host.log()] == ["mount"]
        assert list(host.run_dir.iterdir()) == []

    def test_a_failed_write_still_unmounts(self, tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
        def _full(_fd: int, _data: object) -> int:
            raise OSError(errno.ENOSPC, "full")

        monkeypatch.setattr(mw.os, "write", _full)
        host = _Host(tmp_path, _stick())
        assert host.run(["write", "/dev/sda1"], b"b.txt\nx") == (mw.EXIT_FULL, "", "The USB storage device is full.")
        assert [line.split()[0] for line in host.log()] == ["mount", "umount"]

    def test_a_failed_unmount_is_reported(self, tmp_path: Path) -> None:
        host = _Host(tmp_path, _stick(), umount_rc=32)
        code, out, err = host.run(["write", "/dev/sda1"], b"b.txt\nx")
        assert (code, out) == (mw.EXIT_UNMOUNT_FAILED, "")
        assert err == "b.txt was written, but the USB storage device could not be unmounted. Wait before removing it."

    def test_an_unmount_that_cannot_run(self, tmp_path: Path) -> None:
        host = _Host(tmp_path, _stick())
        host.umount = str(tmp_path / "missing-umount")
        assert host.run(["write", "/dev/sda1"], b"b.txt\nx")[0] == mw.EXIT_UNMOUNT_FAILED

    def test_runs_as_the_installed_helper(self, monkeypatch) -> None:  # noqa: ANN001
        monkeypatch.setattr(sys, "argv", ["write-to-media"])
        with pytest.raises(SystemExit) as exc:
            runpy.run_path(mw.__file__, run_name="__main__")
        assert exc.value.code == mw.EXIT_USAGE


def test_the_helper_file_is_standard_library_only() -> None:
    source = Path(mw.__file__).read_text()
    assert source.startswith("#!/usr/bin/python3 -I\n")
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= set(sys.stdlib_module_names) | {"__future__"}


def test_the_package_installs_it_as_the_helper() -> None:
    build = (Path(__file__).resolve().parent.parent / "packaging" / "build-deb.sh").read_text()
    assert 'install -m 0755 "$REPO_ROOT/openfollow/privilege/media_writer.py" "$SHARE/write-to-media"' in build


class TestGrant:
    def test_the_rendered_sudoers_line(self) -> None:
        from openfollow.privilege.drop_in import render_drop_in

        assert (
            "openfollow ALL=(root) NOPASSWD: /usr/share/openfollow/write-to-media write ^/dev/sd[a-z]+[0-9]*$"
            in render_drop_in("openfollow").splitlines()
        )

    @pytest.mark.parametrize("device", ["/dev/sda", "/dev/sda1", "/dev/sdab12"])
    def test_admits_usb_drives(self, device: str) -> None:
        from openfollow.privilege.capabilities import MEDIA_WRITE, MEDIA_WRITE_SCRIPT

        MEDIA_WRITE.assert_argv_allowed([MEDIA_WRITE_SCRIPT, "write", device])

    @pytest.mark.parametrize(
        "argv",
        [
            ["/usr/share/openfollow/write-to-media", "write", "/dev/mmcblk0p2"],
            ["/usr/share/openfollow/write-to-media", "write", "/dev/nvme0n1p1"],
            ["/usr/share/openfollow/write-to-media", "write", "/dev/sda1", "extra"],
            ["/usr/share/openfollow/write-to-media", "write", "/dev/sda1/../mmcblk0p2"],
            ["/usr/share/openfollow/write-to-media", "read", "/dev/sda1"],
            ["/tmp/write-to-media", "write", "/dev/sda1"],
        ],
        ids=["sd-slot", "nvme", "extra-arg", "traversal", "other-command", "other-binary"],
    )
    def test_refuses_anything_else(self, argv: list[str]) -> None:
        from openfollow.privilege.capabilities import MEDIA_WRITE

        with pytest.raises(ValueError):
            MEDIA_WRITE.assert_argv_allowed(argv)

    def test_the_probe_satisfies_its_own_rule(self) -> None:
        from openfollow.privilege.capabilities import MEDIA_WRITE

        MEDIA_WRITE.assert_argv_allowed(MEDIA_WRITE.probe_command())
