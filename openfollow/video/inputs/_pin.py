# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The video input's interface pin: whether a pinned network input may dial its camera."""

from __future__ import annotations

import ipaddress
import socket
import sys
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import psutil

from openfollow.net_egress import is_loopback_host, pin_socket_egress, resolve_egress
from openfollow.net_utils import get_iface_ipv4
from openfollow.video.failure import VideoFailure

# Linux pins the element's own socket to the device (``bindtodevice``); elsewhere
# the element's socket is unpinned, so the routing table's choice is what counts.
FORCES_DEVICE = sys.platform.startswith("linux")

# A connect attempt runs on the main loop: a name that does not resolve in time
# is checked on a later attempt, from the cache the lookup fills meanwhile.
_RESOLVE_WAIT_S = 0.3
_RESOLVE_TTL_S = 30.0


@dataclass(frozen=True)
class PinRefusal:
    """Why a pinned input may not dial; ``detail`` names the interfaces."""

    failure: VideoFailure
    detail: str


class _Resolver:
    """IPv4 lookups bounded for the main loop, cached for the attempts after."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, str]] = {}
        self._pending: dict[str, threading.Thread] = {}

    def lookup(self, host: str) -> str | None:
        """The host's first IPv4 address, or None when it is not known yet."""
        try:
            return str(ipaddress.IPv4Address(host))
        except ValueError:
            pass
        with self._lock:
            cached = self._cache.get(host)
            if cached is not None and self._clock() - cached[0] < _RESOLVE_TTL_S:
                return cached[1]
            worker = self._pending.get(host)
            if worker is None:
                worker = threading.Thread(target=self._resolve, args=(host,), name="video-pin-dns", daemon=True)
                self._pending[host] = worker
                worker.start()
        worker.join(_RESOLVE_WAIT_S)
        with self._lock:
            cached = self._cache.get(host)
        return cached[1] if cached is not None else None

    def _resolve(self, host: str) -> None:
        try:
            infos = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_DGRAM)
        except OSError:
            infos = []
        with self._lock:
            self._pending.pop(host, None)
            if infos:
                self._cache[host] = (self._clock(), str(infos[0][4][0]))


_resolver = _Resolver()


def _iface_owning(address: str) -> str:
    for name, addrs in psutil.net_if_addrs().items():
        if any(a.family == socket.AF_INET and a.address == address for a in addrs):
            return str(name)
    return ""


def check_video_pin(
    pin: str, host: str = "", port: int = 0, *, forced_device: bool = FORCES_DEVICE
) -> PinRefusal | None:
    """None when *host* may be dialled under *pin*; otherwise why not.

    No packet is sent: connecting a UDP socket only consults the routing table.
    """
    if not pin or (host and is_loopback_host(host)):
        return None
    egress = resolve_egress(pin)
    if egress is None or egress.down:
        return PinRefusal(VideoFailure.INTERFACE_DOWN, f"{pin} has no address")
    if not host:
        return None
    ip = _resolver.lookup(host)
    if ip is None:
        return PinRefusal(VideoFailure.UNREACHABLE, f"{host} could not be resolved to check it against {pin}")
    if is_loopback_host(ip):
        return None
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        if forced_device:
            try:
                pin_socket_egress(probe, egress)
                probe.connect((ip, port or 9))
            except OSError:
                return PinRefusal(VideoFailure.WRONG_INTERFACE, f"{host} is not reachable through {pin}")
            return None
        try:
            probe.connect((ip, port or 9))
        except OSError:
            # No route anywhere: the element reports that itself.
            return None
        local = str(probe.getsockname()[0])
    finally:
        probe.close()
    via = _iface_owning(local)
    if via == pin:
        return None
    return PinRefusal(VideoFailure.WRONG_INTERFACE, f"{host} is reached through {via or local}, not {pin}")


def config_pin(config: Mapping[str, Any]) -> str:
    """The ``video_input_iface`` an input's config carries, "" when unpinned."""
    return str(config.get("video_input_iface", "") or "")


def pinned_address(pin: str) -> str:
    """The pinned interface's IPv4, or "" when unpinned or down."""
    return get_iface_ipv4(pin) if pin else ""
