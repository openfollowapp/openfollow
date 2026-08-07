# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Local IPv4 enumeration and source-interface resolution helpers.

Wraps ``psutil.net_if_addrs()`` to list/select interface IPv4 addresses and
resolves a configured (or pinned) interface to a concrete bind IP, with
auto-detect fallback. ``resolve_source_ip`` returns ``(ip, ResolveStatus)``.
"""

from __future__ import annotations

import socket
import time
from typing import Literal

import psutil

# Status from resolve_source_ip: "iface" (pinned), "primary" (auto), "none" (offline).
ResolveStatus = Literal["iface", "primary", "none"]


def get_primary_local_ipv4(default: str = "N/A") -> str:
    """Return the primary local IPv4 address, or *default* on failure."""
    # Try outbound interface via dummy connection; fall back to first non-loopback address.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            ip = str(sock.getsockname()[0])
            if ip and not ip.startswith("127."):
                return ip
    except OSError:
        pass

    for ip in get_local_ipv4_addresses():
        if not ip.startswith("127."):
            return ip

    return default


def get_local_ipv4_addresses() -> set[str]:
    """Return all local IPv4 addresses across every network interface."""
    ips: set[str] = set()
    for addrs in psutil.net_if_addrs().values():
        for addr in addrs:
            if addr.family == socket.AF_INET:
                ips.add(addr.address)
    return ips


def get_iface_ipv4(iface_name: str) -> str:
    """Return first non-loopback IPv4 on iface, or empty string if unavailable."""
    if not iface_name:
        return ""
    addrs = psutil.net_if_addrs().get(iface_name, [])
    for addr in addrs:
        if addr.family == socket.AF_INET and not addr.address.startswith("127."):
            return str(addr.address)
    return ""


def get_iface_for_ip(ip: str) -> str:
    """Return interface name holding IP, or empty string."""
    if not ip or ip.startswith("127."):
        return ""
    for iface, addrs in psutil.net_if_addrs().items():
        for addr in addrs:
            if addr.family == socket.AF_INET and addr.address == ip:
                return str(iface)
    return ""


def list_iface_ipv4() -> list[tuple[str, str]]:
    """Return [(iface_name, ipv4)] for every non-loopback IPv4, sorted by name."""
    out: list[tuple[str, str]] = []
    for iface, addrs in psutil.net_if_addrs().items():
        for addr in addrs:
            if addr.family != socket.AF_INET:
                continue
            if addr.address.startswith("127."):
                continue
            out.append((str(iface), str(addr.address)))
            break  # first non-loopback IPv4 per iface is enough
    out.sort(key=lambda item: item[0])
    return out


def resolve_source_ip(
    iface: str,
    *,
    fallback: bool = True,
) -> tuple[str, ResolveStatus]:
    """Resolve pinned interface to concrete bind IP + status.

    Status: "iface" (pinned live), "primary" (auto-detect), "none" (unavailable).
    """
    if iface:
        ip_for_iface = get_iface_ipv4(iface)
        if ip_for_iface:
            return ip_for_iface, "iface"
        # Pinned iface is down / missing – fall through to fallback
        # rather than failing closed.

    if not fallback:
        return "", "none"

    primary = get_primary_local_ipv4(default="")
    if primary and not primary.startswith("127."):
        return primary, "primary"
    return "", "none"


def resolve_iface_ip(configured: str) -> str:
    """Return configured IP, or auto-detect primary for multicast binding."""
    if configured:
        return configured
    primary = get_primary_local_ipv4(default="")
    if primary and not primary.startswith("127."):
        return primary
    return ""


def wait_for_source_ip(
    iface: str = "",
    timeout_s: float = 30.0,
    interval_s: float = 1.0,
) -> str:
    """Block until pinned source IP is live (returns loopback on timeout)."""
    # When pinned, use fallback=False to wait for target; otherwise accept primary.
    pinned = bool(iface)
    deadline = time.monotonic() + timeout_s
    while True:
        resolved, status = resolve_source_ip(iface, fallback=not pinned)
        if status != "none":
            return resolved
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            # On timeout, fall back to primary or loopback.
            primary = get_primary_local_ipv4(default="")
            if primary and not primary.startswith("127."):
                return primary
            return "127.0.0.1"
        # Don't sleep past deadline.
        time.sleep(min(interval_s, remaining))
