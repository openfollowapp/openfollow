# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Paul Hermann, Michel Honold, and Vinzenz Schultz
"""Whether this station can run its privileged actions, for the web UI's page banner."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Final

from openfollow.privilege.broker import (
    INSTALL_SUDO_REMEDY,
    REINSTALL_REMEDY,
    PrivilegeBroker,
    sudo_installed,
)
from openfollow.privilege.capabilities import PACKAGE_SELF_UPDATE, CapabilityState

# The .deb's private venv; a source checkout or the macOS bundle runs from elsewhere.
PACKAGED_PREFIX: Final[str] = "/opt/openfollow/venv"


@dataclass(frozen=True)
class PrivilegeWarning:
    title: str
    step: str


NO_SUDO = PrivilegeWarning(
    "sudo is not installed: updates, USB export, network and clock changes can't run.", INSTALL_SUDO_REMEDY
)
RULES_MISSING = PrivilegeWarning(
    "System permissions are missing: updates, USB export, network and clock changes can't run.", REINSTALL_REMEDY
)


def current(broker: PrivilegeBroker | None, *, prefix: str | None = None) -> PrivilegeWarning | None:
    """The banner to show, or ``None`` when privileged actions can run.

    A missing sudo warns on any install. A missing rule warns only on the .deb, which ships every rule;
    a source checkout or the macOS bundle asks for the device password instead.
    """
    if not sudo_installed():
        return NO_SUDO
    if broker is None or (prefix if prefix is not None else sys.prefix) != PACKAGED_PREFIX:
        return None
    if broker.state(PACKAGE_SELF_UPDATE) is not CapabilityState.PASSWORDLESS:
        return RULES_MISSING
    return None
