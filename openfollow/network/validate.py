# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Pure validation helpers for IPv4 network input."""

from __future__ import annotations

import ipaddress
import re

from openfollow.network.adapter import Ipv4Method

_DNS_SEP_RE = re.compile(r"[\s,;]+")
_MAX_DNS = 3


def parse_ipv4(value: str | None) -> str | None:
    """Return canonical dotted-quad form or ``None`` if invalid."""
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        addr = ipaddress.IPv4Address(text)
    except (ipaddress.AddressValueError, ValueError):
        return None
    return str(addr)


def parse_prefix(value: str | int | None) -> int | None:
    """Accept ``24`` or ``255.255.255.0`` style masks; return 0..32 or ``None``."""
    if value is None:
        return None
    if isinstance(value, int):
        return value if 0 <= value <= 32 else None
    text = value.strip()
    if not text:
        return None
    if "." in text:
        try:
            mask = ipaddress.IPv4Address(text)
        except (ipaddress.AddressValueError, ValueError):
            return None
        bits = bin(int(mask))[2:].zfill(32)
        if "01" in bits:
            return None
        return bits.count("1")
    # Strip at most one leading "/" then require ASCII digits: ``str.isdigit``
    # is True for Unicode digits (superscripts, Arabic-Indic) that ``int()``
    # rejects with ValueError (or silently coerces), so a crafted subnet_mask
    # like "²⁴" would otherwise crash the apply route / a lease read.
    candidate = text[1:] if text.startswith("/") else text
    if not candidate.isascii() or not candidate.isdigit():
        return None
    n = int(candidate)
    return n if 0 <= n <= 32 else None


def prefix_to_mask(prefix: int | None) -> str | None:
    """Render CIDR prefix length as dotted IPv4 mask (e.g. 24 → "255.255.255.0")."""
    if not isinstance(prefix, int) or not 0 <= prefix <= 32:
        return None
    if prefix == 0:
        return "0.0.0.0"
    mask_int = (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF
    return str(ipaddress.IPv4Address(mask_int))


def router_in_subnet(address: str, prefix: int, router: str) -> bool:
    """Return True when ``router`` sits inside the ``address/prefix`` subnet."""
    try:
        net = ipaddress.IPv4Network(f"{address}/{prefix}", strict=False)
        return ipaddress.IPv4Address(router) in net
    except (ipaddress.AddressValueError, ipaddress.NetmaskValueError, ValueError):
        return False


def parse_dns_list(value: str | None) -> list[str]:
    """Split ``value`` on commas/whitespace; return up to 3 valid IPv4 entries."""
    if not value:
        return []
    out: list[str] = []
    for token in _DNS_SEP_RE.split(value.strip()):
        if not token:
            continue
        canon = parse_ipv4(token)
        if canon is None:
            continue
        if canon in out:
            continue
        out.append(canon)
        if len(out) >= _MAX_DNS:
            break
    return out


def validate_apply(
    method: Ipv4Method,
    address: str | None,
    prefix: int | None,
    router: str | None,
    dns: list[str] | tuple[str, ...] | None,
) -> list[str]:
    """Return a list of human-readable error strings; empty when valid."""
    errors: list[str] = []
    dns_list = list(dns or ())
    if len(dns_list) > _MAX_DNS:
        errors.append(f"At most {_MAX_DNS} DNS servers are allowed.")
    for entry in dns_list:
        if parse_ipv4(entry) is None:
            errors.append(f"DNS server {entry!r} is not a valid IPv4 address.")

    if method == Ipv4Method.DHCP:
        return errors

    # STATIC or DHCP_WITH_MANUAL_ADDRESS both require address validation.
    # Normalize once so subnet checks use same canonical values as validation.
    canon_address = parse_ipv4(address)
    canon_router = parse_ipv4(router)
    if canon_address is None:
        errors.append("IP address must be a valid IPv4 address.")
    if method == Ipv4Method.STATIC:
        if prefix is None or not (0 <= prefix <= 32):
            errors.append("Subnet prefix must be between 0 and 32.")
        # Gateway is optional for show networks (no internet egress).
        # When provided, must be a valid IPv4 inside the subnet.
        if router and router.strip() and canon_router is None:
            errors.append("Router must be a valid IPv4 address.")
        if (
            canon_address is not None
            and prefix is not None
            and 0 <= prefix <= 32
            and canon_router is not None
            and not router_in_subnet(canon_address, prefix, canon_router)
        ):
            errors.append("Router is not inside the subnet of the address.")
    return errors
