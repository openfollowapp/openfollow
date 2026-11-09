# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Paul Hermann and Michel Honold
"""A Mac's ``netstat -rn -f inet``, and a host that answers it instead of ``/proc/net/route``."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from typing import Any

import pytest

import openfollow.net_utils as net_utils

# Wi-Fi on en0 is the primary service; a USB adapter on en7 holds a scoped
# default; a VPN tunnel holds one too. Laid out as macOS 15 prints it.
SEQUOIA = """\
Routing tables

Internet:
Destination        Gateway            Flags               Netif Expire
default            192.0.2.1          UGScg                 en0
default            198.51.100.1       UGScIg                en7
default            link#21            UCSIg               utun3
127                127.0.0.1          UCS                   lo0
127.0.0.1          127.0.0.1          UH                    lo0
169.254            link#6             UCS                   en0      !
192.0.2            link#6             UCS                   en0      !
192.0.2.1/32       link#6             UCS                   en0      !
192.0.2.1          2:0:0:0:0:1        UHLWIir               en0   1186
192.0.2.10/32      link#6             UCS                   en0      !
198.51.100         link#9             UCS                   en7      !
198.51.100         link#9             UCSI                  en7      !
203.0.113.128/25   192.0.2.254        UGSc                  en0
203.0.113.7        127.0.0.1          UGHSB                 lo0
203.0.113.8        127.0.0.1          UGHSR                 lo0
224.0.0/4          link#6             UmCS                  en0      !
255.255.255.255/32 link#6             UCS                   en0      !
"""

# Older releases print Refs and Use before Netif.
CATALINA = """\
Routing tables

Internet:
Destination        Gateway            Flags        Refs      Use   Netif Expire
default            192.0.2.1          UGSc           79        0     en0
192.0.2            link#4             UCS             2        0     en0      !
"""


def netstat_run(
    stdout: str = SEQUOIA, *, returncode: int = 0, raises: BaseException | None = None
) -> Callable[..., subprocess.CompletedProcess[str]]:
    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert argv == ["/usr/sbin/netstat", "-rn", "-f", "inet"]
        assert kwargs.get("timeout")
        if raises is not None:
            raise raises
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr="")

    return run


def install(monkeypatch: pytest.MonkeyPatch, run: Callable[..., Any]) -> None:
    """Read routes the way macOS does, from *run*; ``/proc/net/route`` is pointed nowhere."""
    monkeypatch.setattr(net_utils, "ROUTES_FROM_NETSTAT", True)
    monkeypatch.setattr(net_utils, "_PROC_NET_ROUTE", net_utils.Path("/nonexistent/proc/net/route"))
    monkeypatch.setattr(net_utils.subprocess, "run", run)
