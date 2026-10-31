# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Michel Honold and Paul Hermann
"""A host whose lo0 carries a peer alias with no netmask, as a 3D Mouse driver adds on macOS."""

from __future__ import annotations

import socket
from types import SimpleNamespace

import multicast_expert
import psutil
import pytest

import openfollow.net_utils as net_utils

ADDRS = {
    "en0": [
        SimpleNamespace(family=psutil.AF_LINK, address="02:00:00:00:00:01", netmask=None),
        SimpleNamespace(family=socket.AF_INET, address="192.0.2.10", netmask="255.255.255.0"),
    ],
    "lo0": [
        SimpleNamespace(family=socket.AF_INET, address="127.0.0.1", netmask="255.0.0.0"),
        SimpleNamespace(family=socket.AF_INET, address="127.51.68.120", netmask=None),
    ],
}
INDEX = {"en0": 4, "lo0": 1}


def _index(name: str) -> int:
    if name not in INDEX:
        raise OSError(6, "Device not configured")
    return INDEX[name]


def _library_scan() -> None:
    # What multicast_expert's own scan raises on the alias.
    raise KeyError("netmask")


def install(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(net_utils.psutil, "net_if_addrs", lambda: ADDRS)
    monkeypatch.setattr(net_utils.socket, "if_nametoindex", _index)
    monkeypatch.setattr(multicast_expert.interfaces, "scan_interfaces", _library_scan)
