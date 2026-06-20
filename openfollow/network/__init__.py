# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Backend-agnostic network adapter trait with multiple implementations."""

from openfollow.network.adapter import (
    ApplyResult,
    Ipv4Config,
    Ipv4Method,
    LeaseInfo,
    NetworkAdapter,
    NetworkInterface,
    NetworkState,
)

__all__ = [
    "ApplyResult",
    "Ipv4Config",
    "Ipv4Method",
    "LeaseInfo",
    "NetworkAdapter",
    "NetworkInterface",
    "NetworkState",
]
