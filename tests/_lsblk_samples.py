# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""``lsblk -J`` output captured on a Pi 5 bench station (util-linux 2.41.5).

A Mac-formatted USB stick (GPT: a 200 MB EFI partition, then the volume) next to
the station's own SD card. ``lsblk`` nests children only when its output carries
the ``NAME`` column; ``--list`` shows the flat shape it prints otherwise.
"""

from __future__ import annotations

import json

# lsblk -J -o NAME,PATH,PKNAME,TYPE,TRAN,FSTYPE,MOUNTPOINTS
HELPER_TREE = json.loads(
    """
{
    "blockdevices": [
        {
            "name": "sda",
            "path": "/dev/sda",
            "pkname": null,
            "type": "disk",
            "tran": "usb",
            "fstype": null,
            "mountpoints": [],
            "children": [
                {
                    "name": "sda1",
                    "path": "/dev/sda1",
                    "pkname": "sda",
                    "type": "part",
                    "tran": null,
                    "fstype": "vfat",
                    "mountpoints": []
                },
                {
                    "name": "sda2",
                    "path": "/dev/sda2",
                    "pkname": "sda",
                    "type": "part",
                    "tran": null,
                    "fstype": "vfat",
                    "mountpoints": []
                }
            ]
        },
        {
            "name": "mmcblk0",
            "path": "/dev/mmcblk0",
            "pkname": null,
            "type": "disk",
            "tran": "mmc",
            "fstype": null,
            "mountpoints": [],
            "children": [
                {
                    "name": "mmcblk0p1",
                    "path": "/dev/mmcblk0p1",
                    "pkname": "mmcblk0",
                    "type": "part",
                    "tran": "mmc",
                    "fstype": "vfat",
                    "mountpoints": [
                        "/boot/firmware"
                    ]
                },
                {
                    "name": "mmcblk0p2",
                    "path": "/dev/mmcblk0p2",
                    "pkname": "mmcblk0",
                    "type": "part",
                    "tran": "mmc",
                    "fstype": "ext4",
                    "mountpoints": [
                        "/"
                    ]
                }
            ]
        }
    ]
}
"""
)

# lsblk -J --list -o NAME,PATH,PKNAME,TYPE,TRAN,FSTYPE,MOUNTPOINTS
HELPER_LIST = json.loads(
    """
{
    "blockdevices": [
        {
            "name": "sda",
            "path": "/dev/sda",
            "pkname": null,
            "type": "disk",
            "tran": "usb",
            "fstype": null,
            "mountpoints": []
        },
        {
            "name": "sda1",
            "path": "/dev/sda1",
            "pkname": "sda",
            "type": "part",
            "tran": null,
            "fstype": "vfat",
            "mountpoints": []
        },
        {
            "name": "sda2",
            "path": "/dev/sda2",
            "pkname": "sda",
            "type": "part",
            "tran": null,
            "fstype": "vfat",
            "mountpoints": []
        },
        {
            "name": "mmcblk0",
            "path": "/dev/mmcblk0",
            "pkname": null,
            "type": "disk",
            "tran": "mmc",
            "fstype": null,
            "mountpoints": []
        },
        {
            "name": "mmcblk0p1",
            "path": "/dev/mmcblk0p1",
            "pkname": "mmcblk0",
            "type": "part",
            "tran": "mmc",
            "fstype": "vfat",
            "mountpoints": [
                "/boot/firmware"
            ]
        },
        {
            "name": "mmcblk0p2",
            "path": "/dev/mmcblk0p2",
            "pkname": "mmcblk0",
            "type": "part",
            "tran": "mmc",
            "fstype": "ext4",
            "mountpoints": [
                "/"
            ]
        }
    ]
}
"""
)

# lsblk -J -b -o NAME,PATH,PKNAME,TYPE,TRAN,FSTYPE,FSVER,LABEL,PARTTYPE,SIZE,MOUNTPOINTS,VENDOR,MODEL
APP_TREE = json.loads(
    """
{
    "blockdevices": [
        {
            "name": "sda",
            "path": "/dev/sda",
            "pkname": null,
            "type": "disk",
            "tran": "usb",
            "fstype": null,
            "fsver": null,
            "label": null,
            "parttype": null,
            "size": 30752636928,
            "mountpoints": [],
            "vendor": "SanDisk ",
            "model": "Ultra",
            "children": [
                {
                    "name": "sda1",
                    "path": "/dev/sda1",
                    "pkname": "sda",
                    "type": "part",
                    "tran": null,
                    "fstype": "vfat",
                    "fsver": "FAT32",
                    "label": "EFI",
                    "parttype": "c12a7328-f81f-11d2-ba4b-00a0c93ec93b",
                    "size": 209715200,
                    "mountpoints": [],
                    "vendor": null,
                    "model": null
                },
                {
                    "name": "sda2",
                    "path": "/dev/sda2",
                    "pkname": "sda",
                    "type": "part",
                    "tran": null,
                    "fstype": "vfat",
                    "fsver": "FAT32",
                    "label": "OHNE TITEL",
                    "parttype": "ebd0a0a2-b9e5-4433-87c0-68b6b72699c7",
                    "size": 30540824576,
                    "mountpoints": [],
                    "vendor": null,
                    "model": null
                }
            ]
        },
        {
            "name": "mmcblk0",
            "path": "/dev/mmcblk0",
            "pkname": null,
            "type": "disk",
            "tran": "mmc",
            "fstype": null,
            "fsver": null,
            "label": null,
            "parttype": null,
            "size": 57671680000,
            "mountpoints": [],
            "vendor": null,
            "model": null,
            "children": [
                {
                    "name": "mmcblk0p1",
                    "path": "/dev/mmcblk0p1",
                    "pkname": "mmcblk0",
                    "type": "part",
                    "tran": "mmc",
                    "fstype": "vfat",
                    "fsver": "FAT16",
                    "label": "BOOT",
                    "parttype": "0xc",
                    "size": 109051904,
                    "mountpoints": [
                        "/boot/firmware"
                    ],
                    "vendor": null,
                    "model": null
                },
                {
                    "name": "mmcblk0p2",
                    "path": "/dev/mmcblk0p2",
                    "pkname": "mmcblk0",
                    "type": "part",
                    "tran": "mmc",
                    "fstype": "ext4",
                    "fsver": "1.0",
                    "label": "ROOT",
                    "parttype": "0x83",
                    "size": 57554222592,
                    "mountpoints": [
                        "/"
                    ],
                    "vendor": null,
                    "model": null
                }
            ]
        }
    ]
}
"""
)

# The same with --list
APP_LIST = json.loads(
    """
{
    "blockdevices": [
        {
            "name": "sda",
            "path": "/dev/sda",
            "pkname": null,
            "type": "disk",
            "tran": "usb",
            "fstype": null,
            "fsver": null,
            "label": null,
            "parttype": null,
            "size": 30752636928,
            "mountpoints": [],
            "vendor": "SanDisk ",
            "model": "Ultra"
        },
        {
            "name": "sda1",
            "path": "/dev/sda1",
            "pkname": "sda",
            "type": "part",
            "tran": null,
            "fstype": "vfat",
            "fsver": "FAT32",
            "label": "EFI",
            "parttype": "c12a7328-f81f-11d2-ba4b-00a0c93ec93b",
            "size": 209715200,
            "mountpoints": [],
            "vendor": null,
            "model": null
        },
        {
            "name": "sda2",
            "path": "/dev/sda2",
            "pkname": "sda",
            "type": "part",
            "tran": null,
            "fstype": "vfat",
            "fsver": "FAT32",
            "label": "OHNE TITEL",
            "parttype": "ebd0a0a2-b9e5-4433-87c0-68b6b72699c7",
            "size": 30540824576,
            "mountpoints": [],
            "vendor": null,
            "model": null
        },
        {
            "name": "mmcblk0",
            "path": "/dev/mmcblk0",
            "pkname": null,
            "type": "disk",
            "tran": "mmc",
            "fstype": null,
            "fsver": null,
            "label": null,
            "parttype": null,
            "size": 57671680000,
            "mountpoints": [],
            "vendor": null,
            "model": null
        },
        {
            "name": "mmcblk0p1",
            "path": "/dev/mmcblk0p1",
            "pkname": "mmcblk0",
            "type": "part",
            "tran": "mmc",
            "fstype": "vfat",
            "fsver": "FAT16",
            "label": "BOOT",
            "parttype": "0xc",
            "size": 109051904,
            "mountpoints": [
                "/boot/firmware"
            ],
            "vendor": null,
            "model": null
        },
        {
            "name": "mmcblk0p2",
            "path": "/dev/mmcblk0p2",
            "pkname": "mmcblk0",
            "type": "part",
            "tran": "mmc",
            "fstype": "ext4",
            "fsver": "1.0",
            "label": "ROOT",
            "parttype": "0x83",
            "size": 57554222592,
            "mountpoints": [
                "/"
            ],
            "vendor": null,
            "model": null
        }
    ]
}
"""
)
