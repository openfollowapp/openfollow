# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Michel Honold and Paul Hermann
"""Pin a sender's egress to one interface, by name.

Binding the source address is not enough. On Linux the routing table still
picks the device for each destination, so a datagram can leave on another NIC
carrying this interface's address. Forcing the device is what keeps a pinned
output off a network the operator did not choose: a destination the interface
cannot reach then fails instead of roaming.
"""

from __future__ import annotations

import ipaddress
import socket
import sys
from collections.abc import Iterable
from dataclasses import dataclass

from openfollow.net_utils import (
    InterfaceUnavailable,
    get_iface_ipv4,
    get_local_ipv4_addresses,
    offered_loopback,
    plane_source_iface,
)

# <asm-generic/socket.h>. typeshed declares socket.SO_BINDTODEVICE on Linux only,
# and macOS Python exports one that is not the option used there.
_SO_BINDTODEVICE = 25
# <netinet/in.h> on macOS; the socket module does not export it.
_IP_BOUND_IF = 25


# Why a destination off this machine is not sent through its loopback.
LOOPBACK_ONLY = "Loopback reaches only this Mac"


@dataclass(frozen=True)
class Egress:
    """The interface a sender is pinned to, and its address ("" while it has none)."""

    iface: str
    address: str
    # Why nothing is sent although the interface has an address (LOOPBACK_ONLY); "" otherwise.
    refused: str = ""

    @property
    def down(self) -> bool:
        return not self.address

    def reason(self, iface_name: str) -> str:
        """Why a down egress sends nothing, naming its interface as *iface_name*."""
        return self.refused or f"interface {iface_name} is down"


def strands(iface: str, host: str, station_addresses: Iterable[str] | None = None) -> Egress | None:
    """A down egress when *iface* is an offered loopback and unicast *host* is off this machine, else None.

    Loopback cannot carry a unicast send to another machine: the sender stays silent
    rather than leave on another interface. *station_addresses* are this machine's
    own, read here when the caller holds none.
    """
    if not host or not offered_loopback(iface) or is_loopback_host(host) or is_multicast_host(host):
        return None
    # A send to this machine's own address never leaves it either.
    if host.strip() in (get_local_ipv4_addresses() if station_addresses is None else station_addresses):
        return None
    return Egress(iface, "", refused=LOOPBACK_ONLY)


def resolve_egress(pin: str, station_iface: str = "") -> Egress | None:
    """Egress for a sender pinned to *pin*, following *station_iface* when blank.

    ``None`` when neither is set: nothing is pinned and the OS routes.
    """
    iface = plane_source_iface(pin, station_iface)
    if not iface:
        return None
    return Egress(iface, get_iface_ipv4(iface))


def is_loopback_host(host: str) -> bool:
    """Whether *host* is this machine, whose traffic never leaves the box."""
    host = host.strip()
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def is_multicast_host(host: str) -> bool:
    """Whether *host* is an IPv4 multicast literal."""
    try:
        return ipaddress.ip_address(host.strip()).is_multicast
    except ValueError:
        return False


def pin_socket_egress(
    sock: socket.socket,
    egress: Egress,
    *,
    multicast: bool = False,
    platform: str = sys.platform,
) -> None:
    """Force *sock* out of *egress*'s interface, raising rather than roaming.

    Call before ``connect()`` or the first send. Never closes *sock*.
    """
    if egress.down:
        raise InterfaceUnavailable(
            f"{egress.iface} has no address; staying silent until it returns rather than sending on another interface"
        )
    try:
        if platform.startswith("linux"):
            sock.setsockopt(socket.SOL_SOCKET, _SO_BINDTODEVICE, egress.iface.encode())
        elif platform == "darwin":
            sock.setsockopt(socket.IPPROTO_IP, _IP_BOUND_IF, socket.if_nametoindex(egress.iface))
        else:
            raise OSError(f"pinning a sender to an interface is not supported on {platform}")
        sock.bind((egress.address, 0))
        if multicast:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(egress.address))
    except OSError as exc:
        raise InterfaceUnavailable(f"cannot send via {egress.iface} ({egress.address}): {exc}") from exc
