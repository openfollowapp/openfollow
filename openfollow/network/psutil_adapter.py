# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Paul Hermann, Michel Honold, and Vinzenz Schultz
# Copyright (C) 2026 Paul Hermann and Michel Honold
"""Read-only fallback adapter for hosts without NetworkManager or dhcpcd."""

from __future__ import annotations

import logging
import socket
from pathlib import Path

import psutil

from openfollow import net_adapters
from openfollow.net_utils import Ipv4Route, read_ipv4_routes
from openfollow.network.adapter import (
    ApplyResult,
    BackendReadError,
    Ipv4Config,
    Ipv4Method,
    NetworkAdapter,
    NetworkInterface,
    NetworkReading,
    NetworkState,
    is_loopback,
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


def _read_gateway(iface: str, routes: list[Ipv4Route] | None) -> str | None:
    if routes is None:
        return None
    # A default route through a gateway, the one the kernel prefers first.
    defaults = [r for r in routes if r.iface == iface and r.network.prefixlen == 0 and r.gateway != "0.0.0.0"]
    return min(defaults, key=lambda r: r.metric).gateway if defaults else None


def read_interfaces() -> list[NetworkInterface]:
    """Every interface psutil reports; raises :class:`BackendReadError` when it cannot."""
    try:
        stats = psutil.net_if_stats()
        addrs = psutil.net_if_addrs()
    except Exception as exc:  # noqa: BLE001 - psutil raises bare Exception on some hosts
        raise BackendReadError(f"psutil: {exc!r}") from exc
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


def _addressed() -> set[str]:
    try:
        addrs = psutil.net_if_addrs()
    except Exception:  # noqa: BLE001 - psutil raises bare Exception on some hosts
        return set()
    return {
        name for name, entries in addrs.items() if any(getattr(e, "family", None) == socket.AF_INET for e in entries)
    }


class PsutilReadOnlyAdapter(NetworkAdapter):
    backend_name = "psutil"

    def list_interfaces(self) -> list[NetworkInterface]:
        """The interfaces an operator can use (:meth:`AdapterReader.operator_interfaces`)."""
        try:
            interfaces = read_interfaces()
        except BackendReadError:
            return []
        usable = set(net_adapters.reader().operator_interfaces([i.name for i in interfaces], _addressed()))
        return [i for i in interfaces if i.name in usable]

    def get_state(self, iface: str) -> NetworkState | None:
        return self.read_network().states.get(iface)

    def read_network(self) -> NetworkReading:
        """One read of the interfaces, addresses, route table and resolver for every interface."""
        interfaces = self.list_interfaces()
        try:
            addrs = psutil.net_if_addrs()
        except Exception:  # noqa: BLE001 - psutil raises bare Exception on some hosts
            addrs = {}
        routes = read_ipv4_routes()
        dns = _read_dns()
        states: dict[str, NetworkState] = {}
        for interface in interfaces:
            addr: str | None = None
            prefix: int | None = None
            for entry in addrs.get(interface.name, []):
                if getattr(entry, "family", None) == socket.AF_INET:
                    addr = entry.address
                    prefix = _netmask_to_prefix(getattr(entry, "netmask", None))
                    break
            ipv4 = Ipv4Config(
                method=Ipv4Method.DHCP,
                address=addr,
                prefix=prefix,
                router=_read_gateway(interface.name, routes),
                dns=dns,
            )
            states[interface.name] = NetworkState(interface=interface, ipv4=ipv4, lease=None)
        return NetworkReading(tuple(i for i in interfaces if not is_loopback(i)), states, ())

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

    def reports_ipv4_method(self) -> bool:
        return False
