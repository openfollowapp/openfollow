# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Backend-agnostic network adapter trait + value types."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


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


LOOPBACK_NAMES = frozenset({"lo", "lo0"})


def is_loopback(iface: NetworkInterface) -> bool:
    """Return True if interface is loopback (matches kind or well-known names)."""
    if (iface.kind or "").lower() == "loopback":
        return True
    return iface.name in LOOPBACK_NAMES


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


AddressSource = Literal["dhcp", "static", "link-local", "none"]


@dataclass(frozen=True)
class NetworkState:
    interface: NetworkInterface
    ipv4: Ipv4Config
    lease: LeaseInfo | None

    @property
    def address_source(self) -> AddressSource:
        """Where this interface's address came from, for operator display.

        Derived rather than stored so the three backends can't disagree about
        it. ``link-local`` outranks the configured method: NM's DHCP fallback
        hands out a 169.254 address while the profile still reads ``auto``, and
        that address is the thing an operator needs told about.
        """
        from openfollow.network.validate import is_link_local

        if not self.ipv4.address:
            return "none"
        if is_link_local(self.ipv4.address):
            return "link-local"
        # DHCP-with-manual-address counts as static: the address the operator
        # sees is the one they typed, not one a server handed out.
        if self.ipv4.method in (Ipv4Method.STATIC, Ipv4Method.DHCP_WITH_MANUAL_ADDRESS):
            return "static"
        return "dhcp"


@dataclass(frozen=True)
class VlanInterface:
    name: str
    parent: str
    vlan_id: int


@dataclass(frozen=True)
class ApplyResult:
    ok: bool
    message: str = ""
    partial_failures: tuple[str, ...] = field(default_factory=tuple)


VLAN_UNSUPPORTED_MESSAGE = "This network backend cannot create VLAN interfaces."


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

    # ---- VLAN sub-interfaces --------------------------------------------
    #
    # Creating the link is the whole of the new work: once ``eth0.10`` exists
    # it is an ordinary netdev, so listing, addressing and pinning it all run
    # through the paths above unchanged. Backends that do not own links report
    # unsupported here and the UI omits the controls entirely.

    def supports_vlans(self) -> bool:
        """Return True if this backend can create and remove VLAN links."""
        return False

    def list_vlans(self) -> list[VlanInterface]:
        """Return the VLAN sub-interfaces this backend knows about."""
        return []

    def create_vlan(self, parent: str, vlan_id: int) -> ApplyResult:
        """Create a ``<parent>.<vlan_id>`` VLAN link."""
        return ApplyResult(ok=False, message=VLAN_UNSUPPORTED_MESSAGE)

    def delete_vlan(self, name: str) -> ApplyResult:
        """Remove the VLAN link named ``name``."""
        return ApplyResult(ok=False, message=VLAN_UNSUPPORTED_MESSAGE)

    def get_ipv6_state(self, iface: str) -> None:
        """Stub for future IPv6 support."""
        return None
