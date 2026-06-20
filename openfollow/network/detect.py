# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Pick a :class:`NetworkAdapter` implementation for the current host."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Literal

from openfollow.network.adapter import NetworkAdapter
from openfollow.network.dhcpcd_adapter import DhcpcdAdapter
from openfollow.network.nm_adapter import NetworkManagerAdapter
from openfollow.network.psutil_adapter import PsutilReadOnlyAdapter
from openfollow.privilege.broker import PrivilegeBroker

logger = logging.getLogger(__name__)

BackendName = Literal["auto", "nm", "dhcpcd", "psutil"]
_DHCPCD_CONF = Path("/etc/dhcpcd.conf")


def _systemctl_is_active(unit: str) -> bool:
    if shutil.which("systemctl") is None:
        return False
    try:
        result = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (subprocess.SubprocessError, OSError):
        return False
    return result.stdout.strip() == "active"


def _has_nmcli() -> bool:
    return shutil.which("nmcli") is not None


def _detect_auto(broker: PrivilegeBroker | None) -> NetworkAdapter:
    if _has_nmcli() and _systemctl_is_active("NetworkManager"):
        return NetworkManagerAdapter(broker=broker)
    if _DHCPCD_CONF.exists() and _systemctl_is_active("dhcpcd"):
        return DhcpcdAdapter(broker=broker)
    return PsutilReadOnlyAdapter()


def select_adapter(
    backend: BackendName | str = "auto",
    *,
    broker: PrivilegeBroker | None = None,
) -> NetworkAdapter:
    """Return requested adapter or auto-detect if backend is "auto"."""
    forced = os.environ.get("OPENFOLLOW_NETWORK_BACKEND", "").strip().lower()
    choice = (forced or backend or "auto").lower()

    if choice == "nm":
        adapter: NetworkAdapter = NetworkManagerAdapter(broker=broker)
    elif choice == "dhcpcd":
        adapter = DhcpcdAdapter(broker=broker)
    elif choice == "psutil":
        adapter = PsutilReadOnlyAdapter()
    elif choice == "auto":
        adapter = _detect_auto(broker)
    else:
        logger.warning("Unknown network backend %r; falling back to auto-detect.", choice)
        adapter = _detect_auto(broker)

    logger.info("Network backend selected: %s", adapter.backend_name)
    return adapter
