# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Read-only fallback adapter for hosts without NetworkManager or dhcpcd."""

from __future__ import annotations

import logging
import socket
from pathlib import Path

import psutil

from openfollow.network.adapter import (
    ApplyResult,
    Ipv4Config,
    Ipv4Method,
    NetworkAdapter,
    NetworkInterface,
    NetworkState,
)

logger = logging.getLogger(__name__)
_RESOLV_CONF = Path("/etc/resolv.conf")
_PROC_ROUTE = Path("/proc/net/route")


def _netmask_to_prefix(netmask: str | None) -> int | None:
    if not netmask:
        return None
    try:
        packed = socket.inet_aton(netmask)
    except OSError:
        return None
    bits = "".join(f"{byte:08b}" for byte in packed)
    if "01" in bits:
        return None
    return bits.count("1")


def _read_dns() -> tuple[str, ...]:
    if not _RESOLV_CONF.exists():
        return ()
    out: list[str] = []
    try:
        for line in _RESOLV_CONF.read_text().splitlines():
            line = line.strip()
            if not line.startswith("nameserver"):
                continue
            parts = line.split()
            if len(parts) >= 2 and "." in parts[1]:
                out.append(parts[1])
    except OSError:
        return ()
    return tuple(out[:3])


def _read_gateway(iface: str) -> str | None:
    if not _PROC_ROUTE.exists():
        return None
    try:
        for line in _PROC_ROUTE.read_text().splitlines()[1:]:
            fields = line.split()
            if len(fields) < 4:
                continue
            name, dest_hex, gw_hex, flags_hex = fields[0], fields[1], fields[2], fields[3]
            if name != iface or dest_hex != "00000000":
                continue
            try:
                flags = int(flags_hex, 16)
            except ValueError:
                continue
            if not (flags & 0x2):  # Platform-specific RTF_GATEWAY flag
                continue
            try:
                gw_int = int(gw_hex, 16)
            except ValueError:
                continue
            packed = gw_int.to_bytes(4, "little")
            return socket.inet_ntoa(packed)
    except OSError:
        return None
    return None


class PsutilReadOnlyAdapter(NetworkAdapter):
    backend_name = "psutil"

    def list_interfaces(self) -> list[NetworkInterface]:
        try:
            stats = psutil.net_if_stats()
            addrs = psutil.net_if_addrs()
        except Exception:  # noqa: BLE001 - psutil raises bare Exception on some hosts
            return []
        out: list[NetworkInterface] = []
        for name, addr_list in addrs.items():
            mac: str | None = None
            for addr in addr_list:
                family = getattr(addr, "family", None)
                if family is not None and getattr(family, "name", "") in (
                    "AF_LINK",
                    "AF_PACKET",
                ):
                    mac = addr.address
                    break
            stat = stats.get(name)
            out.append(
                NetworkInterface(
                    name=name,
                    mac=mac,
                    kind=None,
                    is_up=bool(stat and stat.isup),
                )
            )
        return out

    def get_state(self, iface: str) -> NetworkState | None:
        ifaces = {i.name: i for i in self.list_interfaces()}
        if iface not in ifaces:
            return None
        addr: str | None = None
        prefix: int | None = None
        try:
            for entry in psutil.net_if_addrs().get(iface, []):
                family = getattr(entry, "family", None)
                if family == socket.AF_INET:
                    addr = entry.address
                    prefix = _netmask_to_prefix(getattr(entry, "netmask", None))
                    break
        except Exception:  # noqa: BLE001
            pass
        router = _read_gateway(iface)
        dns = _read_dns()
        ipv4 = Ipv4Config(
            method=Ipv4Method.DHCP,
            address=addr,
            prefix=prefix,
            router=router,
            dns=dns,
        )
        return NetworkState(interface=ifaces[iface], ipv4=ipv4, lease=None)

    def apply_ipv4(self, iface: str, config: Ipv4Config) -> ApplyResult:
        return ApplyResult(
            ok=False,
            message="Read-only host – install NetworkManager or dhcpcd to edit.",
        )

    def renew_lease(self, iface: str) -> ApplyResult:
        return ApplyResult(
            ok=False,
            message="Read-only host – install NetworkManager or dhcpcd to edit.",
        )

    def is_writable(self) -> bool:
        return False
