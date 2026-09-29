#!/usr/bin/python3 -I
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Write one file to the top level of a removable drive.

Shared by the app, which lists drives and writes to a drive that is already
mounted, and by root: the ``.deb`` installs a copy of this file as
``/usr/share/openfollow/write-to-media``, which sudo runs as
``write-to-media write <device>`` with ``<name>\\n<bytes>`` on stdin, for a
drive nothing has mounted. It mounts the drive privately, writes, syncs and
unmounts on every path, so the drive is safe to pull afterwards.

Keep it standard-library only: root never imports from the app's venv.
"""

from __future__ import annotations

import errno
import json
import os
import re
import subprocess  # nosec B404
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any, BinaryIO, TextIO

__all__ = [
    "DEVICE_RE",
    "EXTENSIONS",
    "HELPER_PATH",
    "MAX_BYTES",
    "MOUNT_TYPES",
    "MediaWriteError",
    "check_name",
    "is_system_disk",
    "main",
    "nest_devices",
    "write_exclusive",
]

HELPER_PATH = Path("/usr/share/openfollow/write-to-media")
LSBLK = "/usr/bin/lsblk"
MOUNT = "/usr/bin/mount"
UMOUNT = "/usr/bin/umount"
RUN_DIR = Path("/run/openfollow-media")

# A USB drive or card-reader partition, or a whole drive formatted without a partition table.
DEVICE_RE = r"^/dev/sd[a-z]+[0-9]*$"
# Top level only, no hidden files, only the kinds of file OpenFollow writes.
NAME_RE = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$"
EXTENSIONS = (".txt", ".ofsettings", ".oftemplate")
MAX_BYTES = 32 * 1024 * 1024
# The filesystem as lsblk names it, and the kernel driver that writes it.
MOUNT_TYPES = {"vfat": "vfat", "exfat": "exfat", "ntfs": "ntfs3", "ext4": "ext4"}
# A disk carrying any of these is the station's own, whatever its bus.
SYSTEM_MOUNTS = frozenset({"/", "/boot/firmware", "/mnt/nvme"})
_MAX_SUFFIX = 99
_WRITE_FAILED = "The file could not be written to the USB storage device."
_NAME_LINE_MAX = 256
# Each step's own bound; a caller must wait longer than their sum plus the write, or it kills the helper mid-step.
LSBLK_TIMEOUT_S = 10
MOUNT_TIMEOUT_S = 30
UMOUNT_TIMEOUT_S = 30
STEP_TIMEOUTS_S = LSBLK_TIMEOUT_S + MOUNT_TIMEOUT_S + UMOUNT_TIMEOUT_S

EXIT_USAGE = 2
EXIT_BAD_NAME = 3
EXIT_NOT_REMOVABLE = 4
EXIT_MOUNTED = 5
EXIT_UNSUPPORTED = 6
EXIT_MOUNT_FAILED = 7
EXIT_FULL = 8
EXIT_TOO_LARGE = 9
EXIT_WRITE_FAILED = 10
EXIT_NAME_TAKEN = 11
EXIT_UNMOUNT_FAILED = 12
# Exits whose one stderr line is the operator's sentence; EXIT_USAGE is the caller's mistake.
SENTENCE_EXITS = frozenset(range(EXIT_BAD_NAME, EXIT_UNMOUNT_FAILED + 1))


class MediaWriteError(Exception):
    """A write that did not happen: ``str()`` is what the operator reads, ``code`` the helper's exit status."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def check_name(name: str) -> None:
    """Refuse a file name OpenFollow does not write."""
    if not re.fullmatch(NAME_RE, name) or not name.endswith(EXTENSIONS):
        raise MediaWriteError(EXIT_BAD_NAME, "That is not a file name OpenFollow writes.")


def _numbered(name: str, n: int) -> str:
    stem, dot, ext = name.rpartition(".")
    return f"{stem}-{n}{dot}{ext}"


def write_exclusive(directory: Path, name: str, data: bytes) -> str:
    """Write *data* as *name* in *directory*, never over an existing file; returns the name used.

    A clash takes ``-1``, ``-2`` before the extension. The file is synced
    before this returns.
    """
    for n in range(_MAX_SUFFIX + 1):
        candidate = name if n == 0 else _numbered(name, n)
        try:
            fd = os.open(directory / candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        except FileExistsError:
            continue
        except OSError as exc:
            raise _write_error(exc) from exc
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view) :]
            os.fsync(fd)
        except OSError as exc:
            _close_quietly(fd)
            _remove_quietly(directory / candidate)
            raise _write_error(exc) from exc
        try:
            os.close(fd)
        except OSError as exc:
            _remove_quietly(directory / candidate)
            raise _write_error(exc) from exc
        return candidate
    raise MediaWriteError(EXIT_NAME_TAKEN, "Every name for this file is already taken on the USB storage device.")


def _write_error(exc: OSError) -> MediaWriteError:
    if exc.errno == errno.ENOSPC:
        return MediaWriteError(EXIT_FULL, "The USB storage device is full.")
    if exc.errno == errno.EROFS:
        return MediaWriteError(EXIT_WRITE_FAILED, "The USB storage device is read-only.")
    return MediaWriteError(EXIT_WRITE_FAILED, _WRITE_FAILED)


def _close_quietly(fd: int) -> None:
    try:
        os.close(fd)
    except OSError:
        pass


def _remove_quietly(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _mountpoints(node: dict[str, Any]) -> list[str]:
    return [m for m in node.get("mountpoints") or () if m]


def _walk(
    nodes: list[dict[str, Any]], parent: dict[str, Any] | None = None
) -> Iterator[tuple[dict[str, Any], dict[str, Any] | None]]:
    """Every node with the disk it sits on (``None`` for a disk itself)."""
    for node in nodes:
        yield node, parent
        yield from _walk(node.get("children") or [], node if parent is None else parent)


def nest_devices(tree: dict[str, Any]) -> list[dict[str, Any]]:
    """The disks with their partitions under them, whichever shape ``lsblk -J`` printed.

    It nests only when its output carries the tree column (``NAME``); a flat
    list is nested again through ``PKNAME``, so a partition is never read as
    its own disk.
    """
    top: list[dict[str, Any]] = tree.get("blockdevices") or []
    if any(node.get("children") for node in top):
        return top
    by_name = {node.get("name"): dict(node) for node in top}
    disks: list[dict[str, Any]] = []
    for node in by_name.values():
        parent = by_name.get(node.get("pkname"))
        if parent is None or parent is node:
            disks.append(node)
        else:
            parent.setdefault("children", []).append(node)
    return disks


def is_system_disk(disk: dict[str, Any]) -> bool:
    """Whether *disk*, or any partition on it, carries one of the station's own mounts."""
    return any(SYSTEM_MOUNTS.intersection(_mountpoints(node)) for node, _ in _walk([disk]))


def _find(disks: list[dict[str, Any]], device: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """The node for *device* and the disk it sits on."""
    for node, parent in _walk(disks):
        if node.get("path") == device:
            return node, parent or node
    return None


def _read_request(stdin: BinaryIO) -> tuple[str, bytes]:
    raw = stdin.read(_NAME_LINE_MAX + MAX_BYTES + 1)
    head, newline, data = raw.partition(b"\n")
    if not newline or len(head) > _NAME_LINE_MAX:
        raise MediaWriteError(EXIT_BAD_NAME, "That is not a file name OpenFollow writes.")
    if len(data) > MAX_BYTES:
        raise MediaWriteError(EXIT_TOO_LARGE, f"The file is larger than {MAX_BYTES // (1024 * 1024)} MB.")
    name = head.decode("ascii", "replace")
    check_name(name)
    return name, data


def _check_device(device: str, lsblk: str) -> str:
    """The mount type for *device*, once it is known to be an unmounted USB drive of a kind we write."""
    try:
        out = subprocess.run(  # noqa: S603  # nosec B603
            [lsblk, "-J", "-o", "NAME,PATH,PKNAME,TYPE,TRAN,FSTYPE,MOUNTPOINTS"],
            capture_output=True,
            text=True,
            timeout=LSBLK_TIMEOUT_S,
            check=True,
        ).stdout
        found = _find(nest_devices(json.loads(out)), device)
    except (subprocess.SubprocessError, OSError, ValueError):
        found = None
    if found is None:
        raise MediaWriteError(EXIT_NOT_REMOVABLE, "That USB storage device is no longer attached.")
    node, disk = found
    if disk.get("tran") != "usb" or is_system_disk(disk):
        raise MediaWriteError(EXIT_NOT_REMOVABLE, "That is not a removable USB storage device.")
    if _mountpoints(node):
        raise MediaWriteError(EXIT_MOUNTED, "The USB storage device is already mounted.")
    mount_type = MOUNT_TYPES.get(node.get("fstype") or "")
    if mount_type is None:
        raise MediaWriteError(
            EXIT_UNSUPPORTED, "The USB storage device's format can't be written. Use FAT32, exFAT or NTFS."
        )
    return mount_type


def _write_to_device(
    device: str,
    name: str,
    data: bytes,
    *,
    lsblk: str,
    mount: str,
    umount: str,
    run_dir: Path,
) -> str:
    mount_type = _check_device(device, lsblk)
    run_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    mountpoint = Path(tempfile.mkdtemp(dir=run_dir))
    try:
        mounted = subprocess.run(  # noqa: S603  # nosec B603
            [mount, "-t", mount_type, "-o", "nosuid,nodev,noexec", device, str(mountpoint)],
            capture_output=True,
            timeout=MOUNT_TIMEOUT_S,
            check=False,
        )
        if mounted.returncode != 0:
            raise MediaWriteError(EXIT_MOUNT_FAILED, "The USB storage device could not be mounted.")
        try:
            written = write_exclusive(mountpoint, name, data)
        except Exception as exc:
            error = exc if isinstance(exc, MediaWriteError) else MediaWriteError(EXIT_WRITE_FAILED, _WRITE_FAILED)
            # Still mounted outranks why the write failed: pulling the device now can corrupt it.
            if not _unmount(umount, mountpoint):
                raise MediaWriteError(EXIT_UNMOUNT_FAILED, f"{error} It could not be unmounted either.") from exc
            raise error from exc
        if not _unmount(umount, mountpoint):
            raise MediaWriteError(
                EXIT_UNMOUNT_FAILED,
                f"{written} was written, but the USB storage device could not be unmounted.",
            )
        return written
    finally:
        try:
            mountpoint.rmdir()
        except OSError:
            pass


def _unmount(umount: str, mountpoint: Path) -> bool:
    try:
        done = subprocess.run([umount, str(mountpoint)], capture_output=True, timeout=UMOUNT_TIMEOUT_S, check=False)  # noqa: S603  # nosec B603
        return done.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def main(
    argv: list[str],
    *,
    stdin: BinaryIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    lsblk: str = LSBLK,
    mount: str = MOUNT,
    umount: str = UMOUNT,
    run_dir: Path = RUN_DIR,
) -> int:
    """``write-to-media write <device>``, run as root through sudo; prints the name written.

    The sudoers rule already bounds the device; it is checked again here, with
    the drive itself, because this is the process that mounts it.
    """
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    if len(argv) != 2 or argv[0] != "write" or not re.fullmatch(DEVICE_RE, argv[1]):
        print("usage: write-to-media write /dev/sdXN", file=err)
        return EXIT_USAGE
    try:
        name, data = _read_request(stdin or sys.stdin.buffer)
        written = _write_to_device(argv[1], name, data, lsblk=lsblk, mount=mount, umount=umount, run_dir=run_dir)
    except MediaWriteError as exc:
        print(exc, file=err)
        return exc.code
    print(written, file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
