# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Michel Honold and Paul Hermann
"""Stand-in for ``net_utils.multicast_iface_info``: the pinned interface as the sender named it."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import openfollow.net_utils as net_utils

# Bound before any test swaps in the stand-in.
REAL = net_utils.multicast_iface_info


def pinned(iface: str = "", iface_ip: str = "") -> SimpleNamespace:
    return SimpleNamespace(machine_name=iface, iface_ip=iface_ip)


def install(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(net_utils, "multicast_iface_info", pinned)
