# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Privilege escalation broker.

Single API for every subsystem that needs root:

    broker.run(capability, full_argv, cwd=..., timeout=...)

Behaviour per capability:

- ``PASSWORDLESS``  – covered by a NOPASSWD sudoers entry, runs silently.
- ``NEEDS_PASSWORD`` – covered but requires a password; broker calls the
  registered prompter, then retries with ``sudo -S`` (stdin-piped).
- ``UNAVAILABLE``  – ``sudo`` missing or no rule covers the argv; broker
  raises :class:`PrivilegeError` with the reason.

State is cached per :class:`Capability` and invalidated when the
sudoers drop-in is (re-)installed or when an in-flight call discovers
a stale grant.
"""

from openfollow.privilege.broker import (
    PrivilegeBroker,
    PrivilegeError,
)
from openfollow.privilege.capabilities import (
    ALL_CAPABILITIES,
    Capability,
    CapabilityState,
)

__all__ = [
    "ALL_CAPABILITIES",
    "Capability",
    "CapabilityState",
    "PrivilegeBroker",
    "PrivilegeError",
]
