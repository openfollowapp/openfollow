# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Read and set whether the OpenFollow systemd unit starts at boot."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass

from openfollow.privilege.broker import PrivilegeBroker, PrivilegeError
from openfollow.privilege.capabilities import SERVICE_DISABLE, SERVICE_ENABLE

logger = logging.getLogger(__name__)

_PROBE_TIMEOUT_S = 5.0
_APPLY_TIMEOUT_S = 10.0

# The two states that mean the unit has an enablement an operator can revoke.
_ENABLED_STATES = frozenset({"enabled", "enabled-runtime"})

# States no enable / disable can move, with the sentence shown instead of the
# toggle. Offering a switch here would hand the operator a control whose only
# outcome is an error.
_BLOCKED_STATES: dict[str, str] = {
    "masked": "This service is masked on this host.",
    "masked-runtime": "This service is masked on this host.",
    "not-found": "OpenFollow does not run as a system service on this host.",
    "static": "This service has no boot configuration to switch.",
    "generated": "This service has no boot configuration to switch.",
    "transient": "This service has no boot configuration to switch.",
}

_UNREADABLE = "Could not read whether this service starts at boot."

# systemd's unit-name grammar, matching what the web layer accepts for
# ``update_service_name``. A leading ``-`` is rejected separately: the name is
# appended to ``systemctl enable`` and would otherwise parse as an option.
_UNIT_NAME_RE = re.compile(r"^[A-Za-z0-9_.@-]+$")


@dataclass(frozen=True)
class AutostartState:
    """What ``systemctl is-enabled`` reports for the unit right now.

    ``available`` False means the toggle cannot be offered at all, and
    ``reason`` says why. There is deliberately no stored counterpart: a config
    flag mirroring this would drift the moment anyone ran ``systemctl`` over
    SSH, and the read is one cheap subprocess call.
    """

    available: bool
    enabled: bool
    reason: str = ""


def unit_file_name(service_name: str) -> str:
    """``openfollow`` -> ``openfollow.service``; an explicit suffix is kept.

    A blank name yields ``""``, not the bare suffix - ``.service`` is a unit
    name systemd would accept as an argv token.
    """
    name = service_name.strip()
    if not name:
        return ""
    return name if "." in name else f"{name}.service"


def _unit_name_ok(unit: str) -> bool:
    return bool(unit) and not unit.startswith("-") and bool(_UNIT_NAME_RE.fullmatch(unit))


def read_autostart(service_name: str) -> AutostartState:
    """Fold ``systemctl is-enabled <unit>`` into the toggle's two states.

    Reads the host, never a stored flag, so the switch cannot disagree with
    what systemd will actually do at the next boot. A missing unit reports
    ``not-found`` on *stdout* with a non-zero exit, so the exit code is not the
    signal here - the printed state is.
    """
    unit = unit_file_name(service_name)
    if not _unit_name_ok(unit):
        return AutostartState(available=False, enabled=False, reason="The configured service name is not valid.")
    if shutil.which("systemctl") is None:
        return AutostartState(available=False, enabled=False, reason="This host does not run systemd.")
    try:
        proc = subprocess.run(
            ["systemctl", "is-enabled", unit],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_S,
            check=False,
        )
    except (subprocess.SubprocessError, OSError):
        logger.exception("Reading the boot state of %s failed", unit)
        return AutostartState(available=False, enabled=False, reason=_UNREADABLE)
    state = proc.stdout.strip()
    if not state:
        return AutostartState(available=False, enabled=False, reason=_UNREADABLE)
    blocked = _BLOCKED_STATES.get(state)
    if blocked is not None:
        return AutostartState(available=False, enabled=False, reason=blocked)
    return AutostartState(available=True, enabled=state in _ENABLED_STATES)


def set_autostart(broker: PrivilegeBroker, service_name: str, *, enabled: bool) -> AutostartState:
    """Enable or disable the unit at boot, returning the state systemd then reports.

    Idempotent - a unit already in the requested state costs no sudo call.
    Raises :class:`PrivilegeError` when the host can't be changed or the
    elevation fails; the caller turns that into the banner the operator reads.
    """
    current = read_autostart(service_name)
    if not current.available:
        raise PrivilegeError(current.reason or _UNREADABLE)
    if current.enabled == enabled:
        return current
    unit = unit_file_name(service_name)
    capability = SERVICE_ENABLE if enabled else SERVICE_DISABLE
    verb = "enable" if enabled else "disable"
    broker.run(
        capability,
        ["/usr/bin/systemctl", verb, unit],
        reason=f"{verb.capitalize()} {unit} at boot",
        timeout=_APPLY_TIMEOUT_S,
    )
    return read_autostart(service_name)
