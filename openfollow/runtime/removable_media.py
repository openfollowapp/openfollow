# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Removable drives this station can save a file to: listing and writing.

On Linux, drives come from ``lsblk``. One that something else mounted (a
desktop's automount) is written directly and left mounted; one nothing
mounted goes through the ``media.write`` helper, which mounts, writes and
unmounts it, so it is safe to pull straight away. On macOS, drives come from
``diskutil`` and only mounted volumes are written, directly.

``write_file`` enumerates afresh and accepts only an id from that list, never a
path: the web UI and the HUD hand in what the operator picked, not what to open.
"""

from __future__ import annotations

import json
import logging
import os
import plistlib
import subprocess  # nosec B404
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from openfollow.privilege.broker import PrivilegeBroker, PrivilegeError
from openfollow.privilege.capabilities import MEDIA_WRITE, MEDIA_WRITE_SCRIPT, CapabilityState
from openfollow.privilege.media_writer import (
    MOUNT_TYPES,
    MediaWriteError,
    check_name,
    is_system_disk,
    write_exclusive,
)

logger = logging.getLogger(__name__)

_LSBLK_COLUMNS = "NAME,PATH,TYPE,TRAN,FSTYPE,FSVER,LABEL,SIZE,MOUNTPOINTS,VENDOR,MODEL"
_PROBE_TIMEOUT_S = 5.0
# Mounting, writing and syncing a slow stick; the helper bounds each step itself.
_WRITE_TIMEOUT_S = 120.0

_FS_NAMES = {
    "exfat": "exFAT",
    "ntfs": "NTFS",
    "ext4": "ext4",
    "hfsplus": "HFS+",
    "hfs": "HFS+",
    "apfs": "APFS",
    "btrfs": "Btrfs",
    "xfs": "XFS",
}

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


@dataclass(frozen=True)
class Media:
    """One partition a file could go to. ``id`` is the kernel's name for it (``sda1``, ``disk4s1``)."""

    id: str
    device: str
    name: str
    label: str
    mountpoint: str | None
    writable: bool
    reason: str = ""


@dataclass(frozen=True)
class WriteResult:
    """A file that was saved: the name it got, the drive it is on, what happened and the one next step."""

    filename: str
    media: Media
    message: str
    action: str


class MediaError(Exception):
    """A file that was not saved; ``str()`` is what the operator reads."""


def _size(n: int) -> str:
    for unit, scale in (("TB", 10**12), ("GB", 10**9), ("MB", 10**6)):
        if n >= scale:
            value = n / scale
            return f"{value:.0f} {unit}" if value >= 10 else f"{value:.1f} {unit}"
    return f"{n} B"


def _fs_name(fstype: str | None, version: str | None = None) -> str:
    if fstype == "vfat":
        return version or "FAT"
    if not fstype:
        return "no filesystem"
    return _FS_NAMES.get(fstype, fstype)


def _drive_name(disk: dict[str, Any]) -> str:
    parts = [str(disk.get(key) or "").strip() for key in ("vendor", "model")]
    return " ".join(p for p in parts if p) or "USB drive"


def _linux_media(tree: dict[str, Any], can_mount: bool) -> list[Media]:
    media: list[Media] = []
    for disk in tree.get("blockdevices") or []:
        if disk.get("tran") != "usb" or is_system_disk(disk) or not int(disk.get("size") or 0):
            continue
        # A drive formatted without a partition table carries the filesystem itself.
        parts = [c for c in disk.get("children") or [] if c.get("type") == "part"] or (
            [disk] if disk.get("fstype") else []
        )
        name = _drive_name(disk)
        for part in parts:
            fstype = part.get("fstype")
            fs = _fs_name(fstype, part.get("fsver"))
            mountpoint = next((m for m in part.get("mountpoints") or () if m), None)
            if fstype not in MOUNT_TYPES:
                writable, reason = False, f"{fs} can't be written"
            elif mountpoint is not None:
                writable = os.access(mountpoint, os.W_OK)
                reason = "" if writable else "mounted read-only"
            else:
                writable, reason = can_mount, "" if can_mount else "needs Apply Permissions"
            which = f" ({part.get('label') or part.get('name')})" if len(parts) > 1 else ""
            media.append(
                Media(
                    id=str(part.get("name")),
                    device=str(part.get("path")),
                    name=f"{name}{which}",
                    label=f"{name}{which} · {fs} · {_size(int(part.get('size') or 0))}",
                    mountpoint=mountpoint,
                    writable=writable,
                    reason=reason,
                )
            )
    return media


def _mac_fs_name(info: dict[str, Any]) -> str:
    fstype = info.get("FilesystemType")
    if fstype == "msdos":
        return "FAT32" if "FAT32" in str(info.get("FilesystemUserVisibleName", "")) else "FAT"
    return _fs_name(fstype)


def _mac_media(run: Runner) -> list[Media]:
    listing = plistlib.loads(_output(run, ["/usr/sbin/diskutil", "list", "-plist", "external", "physical"]).encode())
    media: list[Media] = []
    for disk in listing.get("AllDisksAndPartitions") or []:
        for part in disk.get("Partitions") or [disk]:
            ident = part.get("DeviceIdentifier")
            if not ident:
                continue
            info = plistlib.loads(_output(run, ["/usr/sbin/diskutil", "info", "-plist", ident]).encode())
            if not info.get("FilesystemType") or info.get("VolumeName") == "EFI":
                continue
            mountpoint = info.get("MountPoint") or None
            name = str(info.get("MediaName") or info.get("VolumeName") or "USB drive").strip()
            if mountpoint is None:
                writable, reason = False, "not mounted"
            elif not info.get("WritableVolume") or not os.access(mountpoint, os.W_OK):
                writable, reason = False, "mounted read-only"
            else:
                writable, reason = True, ""
            media.append(
                Media(
                    id=ident,
                    device=f"/dev/{ident}",
                    name=name,
                    label=f"{name} · {_mac_fs_name(info)} · {_size(int(info.get('TotalSize') or 0))}",
                    mountpoint=mountpoint,
                    writable=writable,
                    reason=reason,
                )
            )
    return media


def _output(run: Runner, argv: list[str]) -> str:
    return run(argv, capture_output=True, text=True, timeout=_PROBE_TIMEOUT_S, check=True).stdout


def _can_mount(broker: PrivilegeBroker | None) -> bool:
    return broker is not None and broker.state(MEDIA_WRITE) is CapabilityState.PASSWORDLESS


def list_media(
    broker: PrivilegeBroker | None = None,
    *,
    platform: str = sys.platform,
    run: Runner = subprocess.run,
) -> list[Media]:
    """Every removable partition, writable or not (the reason says why not); never the station's own disks."""
    try:
        if platform.startswith("linux"):
            tree = json.loads(_output(run, ["/usr/bin/lsblk", "-J", "-b", "-o", _LSBLK_COLUMNS]))
            return _linux_media(tree, _can_mount(broker))
        if platform == "darwin":
            return _mac_media(run)
    except (OSError, subprocess.SubprocessError, ValueError, plistlib.InvalidFileException) as exc:
        logger.warning("Listing removable drives failed: %s", exc)
    return []


def write_file(
    media_id: str,
    filename: str,
    data: bytes,
    broker: PrivilegeBroker | None = None,
    *,
    platform: str = sys.platform,
    run: Runner = subprocess.run,
) -> WriteResult:
    """Save *data* as *filename* at the top level of the drive *media_id* names; raises :class:`MediaError`."""
    try:
        check_name(filename)
    except MediaWriteError as exc:
        raise MediaError(str(exc)) from exc
    media = next((m for m in list_media(broker, platform=platform, run=run) if m.id == media_id), None)
    if media is None:
        raise MediaError("That drive is no longer attached.")
    if not media.writable:
        raise MediaError(f"{media.name} can't be written: {media.reason}.")
    if media.mountpoint is not None:
        try:
            written = write_exclusive(Path(media.mountpoint), filename, data)
        except MediaWriteError as exc:
            raise MediaError(str(exc)) from exc
        # Only what the station mounted does it unmount again.
        action = (
            "Eject it in Finder before unplugging it." if platform == "darwin" else "Unmount it before removing it."
        )
    else:
        # Listed writable while unmounted only when the grant is passwordless, which takes a broker.
        written = _write_through_helper(media, filename, data, cast(PrivilegeBroker, broker))
        action = "It can be removed now."
    return WriteResult(written, media, f"Saved {written} to {media.name}.", action)


def _write_through_helper(media: Media, filename: str, data: bytes, broker: PrivilegeBroker) -> str:
    try:
        proc = broker.run(
            MEDIA_WRITE,
            [MEDIA_WRITE_SCRIPT, "write", media.device],
            reason="Save a file to a removable drive",
            stdin=filename.encode() + b"\n" + data,
            allow_prompt=False,
            timeout=_WRITE_TIMEOUT_S,
        )
    except PrivilegeError as exc:
        # The helper's own sentence follows the capability's description.
        raise MediaError(str(exc).partition(": ")[2] or str(exc)) from exc
    return proc.stdout.strip() or filename
