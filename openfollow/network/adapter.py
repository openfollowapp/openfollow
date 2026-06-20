# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Backend-agnostic network adapter trait + value types."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum


class Ipv4Method(str, Enum):
    DHCP = "dhcp"
    DHCP_WITH_MANUAL_ADDRESS = "dhcp_manual"
    STATIC = "static"


@dataclass(frozen=True)
class NetworkInterface:
    name: str
    mac: str | None
    kind: str | None
    is_up: bool


_LOOPBACK_NAMES = frozenset({"lo", "lo0"})


def is_loopback(iface: NetworkInterface) -> bool:
    """Return True if interface is loopback (matches kind or well-known names)."""
    if (iface.kind or "").lower() == "loopback":
        return True
    return iface.name in _LOOPBACK_NAMES


@dataclass(frozen=True)
class Ipv4Config:
    method: Ipv4Method
    address: str | None = None
    prefix: int | None = None
    router: str | None = None
    dns: tuple[str, ...] = ()


@dataclass(frozen=True)
class LeaseInfo:
    address: str | None
    prefix: int | None
    router: str | None
    dns: tuple[str, ...]
    lease_seconds_remaining: int | None


@dataclass(frozen=True)
class NetworkState:
    interface: NetworkInterface
    ipv4: Ipv4Config
    lease: LeaseInfo | None


@dataclass(frozen=True)
class ApplyResult:
    ok: bool
    message: str = ""
    partial_failures: tuple[str, ...] = field(default_factory=tuple)


class NetworkAdapter(ABC):
    """Abstract adapter for reading/writing host network config."""

    backend_name: str = "unknown"

    @abstractmethod
    def list_interfaces(self) -> list[NetworkInterface]:
        """Return all physical/virtual network interfaces."""

    @abstractmethod
    def get_state(self, iface: str) -> NetworkState | None:
        """Return current state for ``iface`` or ``None`` if unknown."""

    @abstractmethod
    def apply_ipv4(self, iface: str, config: Ipv4Config) -> ApplyResult:
        """Persist ``config`` to ``iface`` and bring the interface up."""

    @abstractmethod
    def renew_lease(self, iface: str) -> ApplyResult:
        """Release + re-acquire the DHCP lease for ``iface``."""

    def is_writable(self) -> bool:
        """Return True if this adapter can mutate host state."""
        return True

    def get_ipv6_state(self, iface: str) -> None:
        """Stub for future IPv6 support."""
        return None
