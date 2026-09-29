# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Removable drives: which are listed, which can be written, and how a file gets there."""

from __future__ import annotations

import json
import plistlib
import subprocess
from pathlib import Path
from typing import Any

import pytest

import openfollow.privilege.media_writer as mw
import openfollow.runtime.removable_media as rm
from openfollow.privilege.broker import PrivilegeError
from openfollow.privilege.capabilities import MEDIA_WRITE, MEDIA_WRITE_SCRIPT, CapabilityState
from openfollow.runtime.removable_media import MediaError, list_media, scan_media, write_file
from tests._lsblk_samples import APP_LIST, APP_TREE

pytestmark = pytest.mark.unit

GB32 = 32_000_000_000


def _node(name: str, kind: str, **fields: Any) -> dict[str, Any]:
    return {
        "name": name,
        "path": f"/dev/{name}",
        "type": kind,
        "tran": None,
        "fstype": None,
        "fsver": None,
        "label": None,
        "size": GB32,
        "mountpoints": [None],
        "vendor": None,
        "model": None,
        **fields,
    }


def _tree(tmp_path: Path) -> dict[str, Any]:
    card = tmp_path / "CARD"
    card.mkdir()
    return {
        "blockdevices": [
            _node(
                "mmcblk0",
                "disk",
                tran="mmc",
                children=[
                    _node("mmcblk0p1", "part", fstype="vfat", fsver="FAT32", mountpoints=["/boot/firmware"]),
                    _node("mmcblk0p2", "part", fstype="ext4", mountpoints=["/"]),
                ],
            ),
            _node(
                "nvme0n1",
                "disk",
                tran="nvme",
                children=[_node("nvme0n1p1", "part", fstype="ext4", mountpoints=["/mnt/nvme"])],
            ),
            _node(
                "sda",
                "disk",
                tran="usb",
                vendor="SanDisk ",
                model="Ultra",
                children=[_node("sda1", "part", fstype="vfat", fsver="FAT32", label="STICK")],
            ),
            _node(
                "sdb",
                "disk",
                tran="usb",
                vendor="Generic",
                model="SD Reader",
                children=[_node("sdb1", "part", fstype="exfat", size=128 * 10**9, mountpoints=[str(card)])],
            ),
            _node(
                "sdc",
                "disk",
                tran="usb",
                vendor="WD",
                model="Passport",
                children=[
                    _node("sdc1", "part", fstype="apfs", label="MAC", size=2 * 10**12),
                    _node("sdc2", "part", fstype="ntfs", label="WIN", size=500 * 10**9),
                ],
            ),
            _node("sdd", "disk", tran="usb", vendor="Generic", model="Empty Reader", size=0),
            _node("sde", "disk", tran="usb", children=[_node("sde1", "part", fstype="ext4", mountpoints=["/"])]),
            _node("sdf", "disk", tran="usb", fstype="vfat", fsver="FAT16", size=2 * 10**9),
            _node("sdg", "disk", tran="sata", children=[_node("sdg1", "part", fstype="ext4")]),
            _node("sdh", "disk", tran="usb", model="Blank", children=[_node("sdh1", "part", size=512)]),
        ]
    }


class _Broker:
    def __init__(
        self,
        state: CapabilityState = CapabilityState.PASSWORDLESS,
        *,
        stdout: str = "",
        error: PrivilegeError | None = None,
        state_after_error: CapabilityState | None = None,
    ) -> None:
        self._state = state
        self._stdout = stdout
        self._error = error
        self._state_after_error = state_after_error
        self.calls: list[dict[str, Any]] = []

    def state(self, capability: object) -> CapabilityState:
        assert capability is MEDIA_WRITE
        return self._state

    def run(self, capability: object, argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        MEDIA_WRITE.assert_argv_allowed(argv)
        self.calls.append({"capability": capability, "argv": argv, **kw})
        if self._error:
            # A failed run can change the verdict, as a password requirement does.
            self._state = self._state_after_error or self._state
            raise self._error
        return subprocess.CompletedProcess(argv, 0, self._stdout, "")


def _helper_exit(code: int, sentence: str) -> PrivilegeError:
    return PrivilegeError(f"{MEDIA_WRITE.description}: {sentence}", returncode=code, detail=sentence)


def _linux(tree: dict[str, Any]):  # noqa: ANN202
    def run(argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        assert argv[:4] == ["/usr/bin/lsblk", "-J", "-b", "-o"]
        return subprocess.CompletedProcess(argv, 0, json.dumps(tree), "")

    return run


def _by_id(media: list[rm.Media]) -> dict[str, rm.Media]:
    return {m.id: m for m in media}


class TestLinuxListing:
    def test_lists_usb_partitions_and_never_the_stations_own_disks(self, tmp_path: Path) -> None:
        media = list_media(_Broker(), platform="linux", run=_linux(_tree(tmp_path)))
        assert [m.id for m in media] == ["sda1", "sdb1", "sdc1", "sdc2", "sdf", "sdh1"]

    def test_labels_name_the_drive_its_format_and_size(self, tmp_path: Path) -> None:
        media = _by_id(list_media(_Broker(), platform="linux", run=_linux(_tree(tmp_path))))
        assert media["sda1"].label == "SanDisk Ultra · FAT32 · 32 GB"
        assert media["sdb1"].label == "Generic SD Reader · exFAT · 128 GB"
        assert media["sdc1"].label == "WD Passport (MAC) · APFS · 2.0 TB"
        assert media["sdc2"].label == "WD Passport (WIN) · NTFS · 500 GB"
        assert media["sdf"].label == "USB storage device · FAT16 · 2.0 GB"
        assert media["sdh1"].label == "Blank · no filesystem · 512 B"
        assert (media["sda1"].name, media["sda1"].device) == ("SanDisk Ultra", "/dev/sda1")

    def test_what_can_be_written_with_the_grant(self, tmp_path: Path) -> None:
        media = _by_id(list_media(_Broker(), platform="linux", run=_linux(_tree(tmp_path))))
        assert {i: (m.writable, m.reason) for i, m in media.items()} == {
            "sda1": (True, ""),
            "sdb1": (True, ""),
            "sdc1": (False, "APFS can't be written"),
            "sdc2": (True, ""),
            "sdf": (True, ""),
            "sdh1": (False, "no filesystem can't be written"),
        }
        assert media["sdb1"].mountpoint == str(tmp_path / "CARD")

    @pytest.mark.parametrize("broker", [None, _Broker(CapabilityState.NEEDS_PASSWORD)], ids=["no-broker", "no-grant"])
    def test_without_the_grant_only_mounted_drives_can_be_written(self, tmp_path: Path, broker: _Broker | None) -> None:
        media = _by_id(list_media(broker, platform="linux", run=_linux(_tree(tmp_path))))
        assert (media["sda1"].writable, media["sda1"].reason) == (False, "needs Apply Permissions")
        assert media["sdb1"].writable is True

    def test_a_read_only_mount(self, tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
        monkeypatch.setattr(rm.os, "access", lambda path, mode: False)
        media = _by_id(list_media(_Broker(), platform="linux", run=_linux(_tree(tmp_path))))
        assert (media["sdb1"].writable, media["sdb1"].reason) == (False, "mounted read-only")

    @pytest.mark.parametrize("sample", [APP_TREE, APP_LIST], ids=["tree", "flat-list"])
    def test_a_mac_formatted_stick_lists_its_volume_not_its_efi_partition(self, sample: dict) -> None:
        media = list_media(_Broker(), platform="linux", run=_linux(sample))
        assert [(m.id, m.label, m.writable) for m in media] == [("sda2", "SanDisk Ultra · FAT32 · 31 GB", True)]

    def test_each_partition_names_the_disk_it_sits_on(self, tmp_path: Path) -> None:
        media = list_media(_Broker(), platform="linux", run=_linux(_tree(tmp_path)))
        assert {m.id: m.disk for m in media} == {
            "sda1": "sda",
            "sdb1": "sdb",
            "sdc1": "sdc",
            "sdc2": "sdc",
            "sdf": "sdf",
            "sdh1": "sdh",
        }
        assert [m.disk for m in list_media(_Broker(), platform="linux", run=_linux(APP_LIST))] == ["sda"]

    def test_a_failed_listing_is_no_drives(self, caplog) -> None:  # noqa: ANN001
        def run(argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
            raise FileNotFoundError("lsblk")

        with caplog.at_level("WARNING", logger=rm.__name__):
            assert list_media(_Broker(), platform="linux", run=run) == []
        assert "Listing removable drives failed" in caplog.text

    def test_a_failed_scan_says_so_instead_of_listing_nothing(self) -> None:
        def run(argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
            raise subprocess.CalledProcessError(1, argv)

        with pytest.raises(MediaError, match="USB storage devices could not be listed"):
            scan_media(_Broker(), platform="linux", run=run)

    def test_a_scan_on_another_platform_finds_nothing(self) -> None:
        assert scan_media(platform="win32", run=subprocess.run) == []

    def test_another_platform_lists_nothing(self) -> None:
        def run(argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
            raise AssertionError("nothing to run")

        assert list_media(platform="win32", run=run) == []


def _mac(
    volumes: dict[str, dict[str, Any]],
    layout: list[dict[str, Any]],
    containers: dict[str, list[dict[str, Any]]] | None = None,
):  # noqa: ANN202
    def run(argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        if argv[1] == "list":
            listed = layout if argv[3:] == ["external", "physical"] else (containers or {})[argv[3]]
            return subprocess.CompletedProcess(argv, 0, plistlib.dumps({"AllDisksAndPartitions": listed}).decode(), "")
        return subprocess.CompletedProcess(argv, 0, plistlib.dumps(volumes[argv[3]]).decode(), "")

    return run


def _mac_fixture(tmp_path: Path):  # noqa: ANN202
    stick = tmp_path / "STICK"
    stick.mkdir()
    volumes = {
        "disk4s1": {
            "FilesystemType": "msdos",
            "FilesystemUserVisibleName": "MS-DOS (FAT32)",
            "MountPoint": str(stick),
            "MediaName": "",
            "VolumeName": "STICK",
            "WritableVolume": True,
            "TotalSize": GB32,
        },
        "disk5s1": {"FilesystemType": "msdos", "FilesystemUserVisibleName": "MS-DOS (FAT32)", "VolumeName": "EFI"},
        "disk5s2": {
            "FilesystemType": "ntfs",
            "MountPoint": "/Volumes/WIN",
            "VolumeName": "WIN",
            "WritableVolume": False,
            "TotalSize": 500 * 10**9,
        },
        "disk6s1": {"FilesystemType": "exfat", "VolumeName": "CARD", "TotalSize": 64 * 10**9},
        "disk7": {
            "FilesystemType": "msdos",
            "FilesystemUserVisibleName": "MS-DOS (FAT16)",
            "MediaName": "Old Stick",
            "MountPoint": str(stick),
            "WritableVolume": True,
            "TotalSize": 10**9,
        },  # fmt: skip
        "disk8s1": {"Content": "Apple_APFS"},
        # The running system: an APFS volume on the internal disk's container.
        "/": {"ParentWholeDisk": "disk3", "APFSPhysicalStores": [{"APFSPhysicalStore": "disk0s2"}]},
    }
    layout = [
        {"DeviceIdentifier": "disk4", "Partitions": [{"DeviceIdentifier": "disk4s1"}]},
        {"DeviceIdentifier": "disk5", "Partitions": [{"DeviceIdentifier": "disk5s1"}, {"DeviceIdentifier": "disk5s2"}]},
        {"DeviceIdentifier": "disk6", "Partitions": [{"DeviceIdentifier": "disk6s1"}]},
        {"DeviceIdentifier": "disk7"},
        {"DeviceIdentifier": "disk8", "Partitions": [{"DeviceIdentifier": "disk8s1"}, {"Content": "free"}]},
    ]
    return volumes, layout


class TestMacListing:
    def test_lists_volumes_and_which_can_be_written(self, tmp_path: Path) -> None:
        media = _by_id(list_media(platform="darwin", run=_mac(*_mac_fixture(tmp_path))))
        assert {i: (m.label, m.writable, m.reason) for i, m in media.items()} == {
            "disk4s1": ("STICK · FAT32 · 32 GB", True, ""),
            "disk5s2": ("WIN · NTFS · 500 GB", False, "mounted read-only"),
            "disk6s1": ("CARD · exFAT · 64 GB", False, "not mounted"),
            "disk7": ("Old Stick · FAT · 1.0 GB", True, ""),
        }

    def test_each_volume_names_the_disk_it_sits_on(self, tmp_path: Path) -> None:
        media = list_media(platform="darwin", run=_mac(*_mac_fixture(tmp_path)))
        assert {m.id: m.disk for m in media} == {
            "disk4s1": "disk4",
            "disk5s2": "disk5",
            "disk6s1": "disk6",
            "disk7": "disk7",
        }

    @pytest.mark.parametrize(
        "root",
        [
            {"ParentWholeDisk": "disk9", "APFSPhysicalStores": [{"APFSPhysicalStore": "disk5s2"}]},
            {"ParentWholeDisk": "disk5"},
        ],
        ids=["apfs-container-on-it", "hfs-volume-on-it"],
    )
    def test_never_the_disk_an_externally_booted_mac_runs_from(self, tmp_path: Path, root: dict[str, Any]) -> None:
        volumes, layout = _mac_fixture(tmp_path)
        volumes["/"] = root
        media = list_media(platform="darwin", run=_mac(volumes, layout))
        assert [m.id for m in media] == ["disk4s1", "disk6s1", "disk7"]

    def test_a_listing_with_broken_xml_is_no_drives(self, caplog) -> None:  # noqa: ANN001
        def run(argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(argv, 0, '<?xml version="1.0"?><plist><dict><key>x</oops>', "")

        with caplog.at_level("WARNING", logger=rm.__name__):
            assert list_media(platform="darwin", run=run) == []
        assert "Listing removable drives failed" in caplog.text

    def test_an_apfs_stick_offers_the_volumes_of_its_container(self, tmp_path: Path) -> None:
        # The physical partition is only the container's store; its volumes sit on a synthesized disk.
        stick = tmp_path / "MACSTICK"
        stick.mkdir()
        volumes = {
            "/": {"ParentWholeDisk": "disk3", "APFSPhysicalStores": [{"APFSPhysicalStore": "disk0s2"}]},
            "disk9s1": {"FilesystemType": "msdos", "VolumeName": "EFI"},
            "disk9s2": {"Content": "Apple_APFS", "APFSContainerReference": "disk10"},
            "disk10s1": {
                "FilesystemType": "apfs",
                "MountPoint": str(stick),
                "VolumeName": "MACSTICK",
                "WritableVolume": True,
                "TotalSize": 64 * 10**9,
            },
        }
        layout = [
            {
                "DeviceIdentifier": "disk9",
                "Partitions": [{"DeviceIdentifier": "disk9s1"}, {"DeviceIdentifier": "disk9s2"}],
            }
        ]
        containers = {"disk10": [{"DeviceIdentifier": "disk10", "APFSVolumes": [{"DeviceIdentifier": "disk10s1"}, {}]}]}
        run = _mac(volumes, layout, containers)
        media = list_media(platform="darwin", run=run)
        assert [(m.id, m.device, m.label, m.writable) for m in media] == [
            ("disk10s1", "/dev/disk10s1", "MACSTICK · APFS · 64 GB", True)
        ]
        # The physical disk, which is what the USB device lists, not the synthesized container.
        assert media[0].disk == "disk9"
        result = write_file("disk10s1", "b.txt", b"x", platform="darwin", run=run)
        assert (result.filename, (stick / "b.txt").read_bytes()) == ("b.txt", b"x")

    def test_a_listing_diskutil_cannot_parse(self) -> None:
        def run(argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(argv, 0, "not a plist", "")

        assert list_media(platform="darwin", run=run) == []


class TestWriting:
    def test_a_mounted_drive_is_written_directly_and_never_overwritten(self, tmp_path: Path) -> None:
        tree = _tree(tmp_path)
        (tmp_path / "CARD" / "ofdiag-rig.txt").write_bytes(b"old")
        result = write_file("sdb1", "ofdiag-rig.txt", b"new", _Broker(), platform="linux", run=_linux(tree))
        assert result.filename == "ofdiag-rig-1.txt"
        assert (tmp_path / "CARD" / "ofdiag-rig-1.txt").read_bytes() == b"new"
        assert (result.message, result.action) == (
            "Saved ofdiag-rig-1.txt to Generic SD Reader.",
            "Unmount it before removing it.",
        )

    def test_macos_says_to_eject_it(self, tmp_path: Path) -> None:
        result = write_file("disk4s1", "b.txt", b"x", platform="darwin", run=_mac(*_mac_fixture(tmp_path)))
        assert (result.message, result.action) == ("Saved b.txt to STICK.", "Eject it in Finder before unplugging it.")

    def test_an_unmounted_drive_goes_through_the_helper_without_prompting(self, tmp_path: Path) -> None:
        broker = _Broker(stdout="ofdiag-rig-2.txt\n")
        result = write_file(
            "sda1", "ofdiag-rig.txt", b"\x00data", broker, platform="linux", run=_linux(_tree(tmp_path))
        )
        (call,) = broker.calls
        assert call["argv"] == [MEDIA_WRITE_SCRIPT, "write", "/dev/sda1"]
        assert (call["stdin"], call["allow_prompt"]) == (b"ofdiag-rig.txt\n\x00data", False)
        # Outlasts the helper's own step bounds with room to write: killed mid-step, it never unmounts.
        assert call["timeout"] >= mw.STEP_TIMEOUTS_S + 60
        assert (result.filename, result.media.id, result.action) == (
            "ofdiag-rig-2.txt",
            "sda1",
            "It can be removed now.",
        )

    @pytest.mark.parametrize(
        ("error", "state_after", "sentence", "action"),
        [
            (_helper_exit(mw.EXIT_FULL, "The device is full."), None, "The device is full.", ""),
            (
                _helper_exit(mw.EXIT_UNMOUNT_FAILED, "b.txt was written, but not unmounted."),
                None,
                "b.txt was written, but not unmounted.",
                "Wait before removing it.",
            ),
            (
                PrivilegeError(f"{MEDIA_WRITE.description}: timed out after 180s."),
                None,
                "The USB storage device could not be written.",
                "",
            ),
            (
                PrivilegeError("x: Traceback", returncode=1, detail="Traceback (most recent call last):\nRuntimeError"),
                None,
                "The USB storage device could not be written.",
                "",
            ),
            (
                PrivilegeError(f"{MEDIA_WRITE.description}: password required but prompting is disabled here."),
                CapabilityState.NEEDS_PASSWORD,
                "Writing to a USB storage device needs Apply Permissions.",
                "Run Apply Permissions on the Device page, then try again.",
            ),
        ],
        ids=["helper-sentence", "written-but-still-mounted", "timed-out", "helper-crashed", "grant-gone"],
    )
    def test_what_the_operator_reads_when_the_helper_fails(
        self,
        tmp_path: Path,
        caplog,
        error: PrivilegeError,
        state_after,
        sentence: str,
        action: str,  # noqa: ANN001
    ) -> None:
        broker = _Broker(error=error, state_after_error=state_after)
        with pytest.raises(MediaError) as exc:
            write_file("sda1", "b.txt", b"x", broker, platform="linux", run=_linux(_tree(tmp_path)))
        assert (str(exc.value), exc.value.action) == (sentence, action)
        # Anything that is not the helper's own sentence goes to the log in full.
        assert (str(error) in caplog.text) is (error.detail == "" or error.returncode == 1)

    @pytest.mark.parametrize(
        ("media_id", "filename", "text"),
        [
            ("sda1", "../b.txt", "That is not a file name OpenFollow writes."),
            ("/dev/sda1", "b.txt", "That USB storage device is no longer attached."),
            ("mmcblk0p2", "b.txt", "That USB storage device is no longer attached."),
            ("sdc1", "b.txt", "WD Passport (MAC) can't be written: APFS can't be written."),
        ],
        ids=["bad-name", "a-path-not-an-id", "the-root-disk", "not-writable"],
    )
    def test_refuses_before_writing(self, tmp_path: Path, media_id: str, filename: str, text: str) -> None:
        broker = _Broker()
        with pytest.raises(MediaError) as exc:
            write_file(media_id, filename, b"x", broker, platform="linux", run=_linux(_tree(tmp_path)))
        assert (str(exc.value), broker.calls) == (text, [])

    def test_a_full_mounted_drive(self, tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
        import openfollow.privilege.media_writer as mw

        def _full(_fd: int, _data: object) -> int:
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(mw.os, "write", _full)
        with pytest.raises(MediaError, match=r"^The USB storage device is full\.$"):
            write_file("sdb1", "b.txt", b"x", _Broker(), platform="linux", run=_linux(_tree(tmp_path)))
