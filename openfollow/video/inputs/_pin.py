# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The video input's interface pin: whether a pinned network input may dial its camera."""

from __future__ import annotations

import ipaddress
import socket
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import psutil

from openfollow.net_egress import is_loopback_host, resolve_egress
from openfollow.net_utils import HOST_RESOLVER, HostLookup, read_ipv4_routes, route_source
from openfollow.video.failure import VideoFailure

# Linux pins the element's own socket to the device (``bindtodevice``); elsewhere
# the element's socket is unpinned, so the routing table's choice is what counts.
FORCES_DEVICE = sys.platform.startswith("linux")

# A connect attempt runs on the main loop: a name that does not resolve in time
# is checked on a later attempt, from the answer the lookup leaves behind.
_RESOLVE_WAIT_S = 0.3


@dataclass(frozen=True)
class PinRefusal:
    """Why a pinned input may not dial; ``detail`` names the interfaces."""

    failure: VideoFailure
    detail: str


_resolver = HOST_RESOLVER


def _own_addresses() -> dict[str, str]:
    """Every address of this station, both families, mapped to its interface."""
    owners: dict[str, str] = {}
    for name, addrs in psutil.net_if_addrs().items():
        for addr in addrs:
            if addr.family in (socket.AF_INET, socket.AF_INET6):
                owners.setdefault(str(addr.address).split("%")[0], str(name))
    return owners


def _is_local(address: str, owners: Mapping[str, str]) -> bool:
    """Traffic to *address* never leaves the box, so no interface carries it."""
    return is_loopback_host(address) or address.split("%")[0] in owners


def is_local_destination(host: str) -> bool:
    """Whether *host* is this station, as far as is known without a lookup.

    Every address must be local: the element may dial any of them, and one
    remote address dialled without the device binding could leave elsewhere.
    """
    if not host:
        return False
    if is_loopback_host(host):
        return True
    owners = _own_addresses()
    addresses = _resolver.cached(host)
    return bool(addresses) and all(_is_local(address, owners) for address in addresses)


def binds_device(host: str) -> bool:
    """Whether a pinned SRT connection to *host* is bound to the device.

    libsrt refuses a device on anything but an IPv4 socket and may dial any
    answer, so a name with an IPv6 address, even this station's own, is left to
    the routing table, which the pin check verified. A name not yet resolved is
    bound, so nothing can leave elsewhere.
    """
    if not host or is_loopback_host(host):
        return False
    addresses = _resolver.cached(host)
    owners = _own_addresses()
    remote = [a for a in addresses if not _is_local(a, owners)]
    return not addresses or (bool(remote) and all(":" not in a for a in addresses))


def _routed_through(address: str, iface: str) -> bool | None:
    """Whether the main routing table reaches *address* through *iface*; None when unreadable.

    Read from the table, never probed: a socket bound to a device with no route
    through it may still connect, because the kernel assumes the destination is
    on-link.
    """
    routes = read_ipv4_routes()
    if routes is None:
        return None
    target = ipaddress.IPv4Address(address)
    return any(route.iface == iface and target in route.network for route in routes)


def check_video_pin(
    pin: str, host: str = "", port: int = 0, *, forced_device: bool = FORCES_DEVICE
) -> PinRefusal | None:
    """None when *host* may be dialled under *pin*; otherwise why not.

    Every address the name resolves to is checked, since the element may dial
    any of them. With *forced_device* the element's socket is bound to the
    device, so a route through it must exist; libsrt binds only IPv4, so a name
    with an IPv6 address takes the routing table's own choice for every address.
    """
    if not pin or (host and is_loopback_host(host)):
        return None
    owners = _own_addresses()
    found = _resolver.lookup(host, _RESOLVE_WAIT_S) if host else HostLookup("failed")
    # Looked up before the interface is judged, so a name that resolves to this
    # station is never refused for an outage, whatever the cache held.
    if found.addresses and all(_is_local(address, owners) for address in found.addresses):
        return None
    egress = resolve_egress(pin)
    if egress is None or egress.down:
        return PinRefusal(VideoFailure.INTERFACE_DOWN, f"{pin} has no address")
    if not host:
        return None
    unresolved = _unresolved(found, host, pin)
    if unresolved is not None:
        return unresolved
    remote = [address for address in found.addresses if not _is_local(address, owners)]
    forced = forced_device and all(":" not in address for address in found.addresses)
    for address in remote:
        if forced:
            if _routed_through(address, pin) is False:
                return PinRefusal(VideoFailure.WRONG_INTERFACE, f"{host} is not reachable through {pin}")
            continue
        local = route_source(address, port)
        if local is None:
            # No route at all: the element reports that itself.
            continue
        via = owners.get(local, "")
        if via != pin:
            return PinRefusal(VideoFailure.WRONG_INTERFACE, f"{host} is reached through {via or local}, not {pin}")
    return None


def check_listen_address(pin: str, address: str) -> PinRefusal | None:
    """None when every address *address* resolves to belongs to *pin*; otherwise why not."""
    found = _resolver.lookup(address, _RESOLVE_WAIT_S)
    unresolved = _unresolved(found, address, pin)
    if unresolved is not None:
        return unresolved
    owners = _own_addresses()
    if any(owners.get(a.split("%")[0]) != pin for a in found.addresses):
        return PinRefusal(VideoFailure.WRONG_INTERFACE, f"{address} is not an address of {pin}")
    return None


def _unresolved(found: HostLookup, host: str, pin: str) -> PinRefusal | None:
    if found.outcome == "pending":
        return PinRefusal(VideoFailure.UNKNOWN, f"{host} did not resolve in time to check it against {pin}")
    if found.outcome == "skipped":
        return PinRefusal(
            VideoFailure.UNKNOWN, f"{host} could not be looked up ({found.error}) to check it against {pin}"
        )
    if not found.addresses:
        return PinRefusal(VideoFailure.UNKNOWN, f"{host} does not resolve, so it could not be checked against {pin}")
    return None


def config_pin(config: Mapping[str, Any]) -> str:
    """The ``video_input_iface`` an input's config carries, "" when unpinned."""
    return str(config.get("video_input_iface", "") or "")
