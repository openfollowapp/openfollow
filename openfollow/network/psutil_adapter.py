# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Read-only fallback adapter for hosts without NetworkManager or dhcpcd."""

from __future__ import annotations

import logging
import socket
from pathlib import Path

import psutil

from openfollow.net_utils import read_ipv4_routes
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
    routes = read_ipv4_routes()
    if routes is None:
        return None
    # A default route through a gateway, the one the kernel prefers first.
    defaults = [r for r in routes if r.iface == iface and r.network.prefixlen == 0 and r.gateway != "0.0.0.0"]
    return min(defaults, key=lambda r: r.metric).gateway if defaults else None


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
