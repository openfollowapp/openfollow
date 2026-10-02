# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The video input's interface pin: whether a pinned network input may dial its camera."""

from __future__ import annotations

import ipaddress
import socket
import struct
import sys
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import psutil

from openfollow.net_egress import is_loopback_host, resolve_egress
from openfollow.net_utils import route_source
from openfollow.video.failure import VideoFailure

# Linux pins the element's own socket to the device (``bindtodevice``); elsewhere
# the element's socket is unpinned, so the routing table's choice is what counts.
FORCES_DEVICE = sys.platform.startswith("linux")

# A connect attempt runs on the main loop: a name that does not resolve in time
# is checked on a later attempt, from the cache the lookup fills meanwhile.
_RESOLVE_WAIT_S = 0.3
_RESOLVE_TTL_S = 30.0
# A name that failed is not looked up again for this long, so a camera that is
# off does not cost every reconnect attempt a wait on the main loop.
_FAILURE_TTL_S = 10.0

# The main routing table. A socket bound to a device with no route there may
# still connect (the kernel assumes the destination is on-link), so a probe
# connect cannot tell whether the device reaches the camera.
_PROC_ROUTE = "/proc/net/route"
_RTF_UP = 0x1


@dataclass(frozen=True)
class PinRefusal:
    """Why a pinned input may not dial; ``detail`` names the interfaces."""

    failure: VideoFailure
    detail: str


@dataclass(frozen=True)
class _Lookup:
    addresses: tuple[str, ...]
    pending: bool = False


class _Resolver:
    """Lookups bounded for the main loop, cached for the attempts after."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, tuple[str, ...]]] = {}
        self._pending: dict[str, threading.Thread] = {}

    def lookup(self, host: str) -> _Lookup:
        """Every address *host* resolves to, both families, or that it is still resolving.

        Only the call that starts a lookup waits for it; one already running
        answers "pending" at once, so a hanging resolver costs one wait.
        """
        literal = _literal(host)
        if literal:
            return _Lookup((literal,))
        with self._lock:
            cached = self._fresh(host)
            if cached is not None:
                return _Lookup(cached)
            if host in self._pending:
                return _Lookup((), pending=True)
            worker = threading.Thread(target=self._resolve, args=(host,), name="video-pin-dns", daemon=True)
            self._pending[host] = worker
        try:
            worker.start()
        except RuntimeError:
            with self._lock:
                self._pending.pop(host, None)
            return _Lookup(())
        worker.join(_RESOLVE_WAIT_S)
        with self._lock:
            if host in self._pending:
                return _Lookup((), pending=True)
            return _Lookup(self._fresh(host) or ())

    def cached(self, host: str) -> tuple[str, ...]:
        """What *host* is known to resolve to, without looking it up."""
        literal = _literal(host)
        if literal:
            return (literal,)
        with self._lock:
            return self._fresh(host) or ()

    def _fresh(self, host: str) -> tuple[str, ...] | None:
        cached = self._cache.get(host)
        if cached is None:
            return None
        when, addresses = cached
        if self._clock() - when < (_RESOLVE_TTL_S if addresses else _FAILURE_TTL_S):
            return addresses
        return None

    def _resolve(self, host: str) -> None:
        addresses: tuple[str, ...] = ()
        try:
            infos = socket.getaddrinfo(host, None, type=socket.SOCK_DGRAM)
            addresses = tuple(dict.fromkeys(str(info[4][0]) for info in infos))
        except (OSError, ValueError):
            # ValueError: a label that is empty or too long fails IDNA encoding.
            pass
        finally:
            with self._lock:
                self._pending.pop(host, None)
                self._cache[host] = (self._clock(), addresses)


_resolver = _Resolver()


def _literal(host: str) -> str:
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return ""


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


def _route_key(field: str) -> int:
    # The kernel prints each network-order word as a native integer.
    return int(ipaddress.IPv4Address(struct.pack("=I", int(field, 16))))


def _routed_through(address: str, iface: str) -> bool | None:
    """Whether the main routing table reaches *address* through *iface*; None when unreadable."""
    try:
        with open(_PROC_ROUTE, encoding="ascii") as fh:
            lines = fh.read().splitlines()[1:]
    except OSError:
        return None
    target = int(ipaddress.IPv4Address(address))
    for line in lines:
        fields = line.split()
        if len(fields) < 8 or fields[0] != iface:
            continue
        try:
            network, flags, mask = _route_key(fields[1]), int(fields[3], 16), _route_key(fields[7])
        except ValueError:
            continue
        if flags & _RTF_UP and target & mask == network & mask:
            return True
    return False


def check_video_pin(
    pin: str, host: str = "", port: int = 0, *, forced_device: bool = FORCES_DEVICE
) -> PinRefusal | None:
    """None when *host* may be dialled under *pin*; otherwise why not.

    Every address the name resolves to is checked, since the element may dial
    any of them. With *forced_device* the element's socket is bound to the
    device, so a route through it must exist; libsrt binds only IPv4, so a name
    with an IPv6 address takes the routing table's own choice for every address.
    """
    if not pin or (host and is_local_destination(host)):
        return None
    egress = resolve_egress(pin)
    if egress is None or egress.down:
        return PinRefusal(VideoFailure.INTERFACE_DOWN, f"{pin} has no address")
    if not host:
        return None
    found = _resolver.lookup(host)
    unresolved = _unresolved(found, host, pin)
    if unresolved is not None:
        return unresolved
    owners = _own_addresses()
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
    found = _resolver.lookup(address)
    unresolved = _unresolved(found, address, pin)
    if unresolved is not None:
        return unresolved
    owners = _own_addresses()
    if any(owners.get(a.split("%")[0]) != pin for a in found.addresses):
        return PinRefusal(VideoFailure.WRONG_INTERFACE, f"{address} is not an address of {pin}")
    return None


def _unresolved(found: _Lookup, host: str, pin: str) -> PinRefusal | None:
    if found.pending:
        return PinRefusal(VideoFailure.UNKNOWN, f"{host} did not resolve in time to check it against {pin}")
    if not found.addresses:
        return PinRefusal(VideoFailure.UNKNOWN, f"{host} does not resolve, so it could not be checked against {pin}")
    return None


def config_pin(config: Mapping[str, Any]) -> str:
    """The ``video_input_iface`` an input's config carries, "" when unpinned."""
    return str(config.get("video_input_iface", "") or "")
