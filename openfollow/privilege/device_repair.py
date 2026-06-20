# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Idempotent device-state repair actions (groups, services, permissions)."""

from __future__ import annotations

import grp
import logging
import os
import pwd
import re
import shutil
import socket
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from openfollow.marker_catalog.station_name import station_name_to_hostname
from openfollow.privilege.broker import PrivilegeBroker, PrivilegeError
from openfollow.privilege.capabilities import (
    DEVICE_GROUP_JOIN,
    DEVICE_HOSTS_WRITE,
    DEVICE_LINGER_ENABLE,
    DEVICE_SET_HOSTNAME,
    JOURNAL_GROUP_JOIN,
    REQUIRED_HARDWARE_GROUPS,
    REQUIRED_HARDWARE_GROUPS_JOINED,
    SERVICE_DISABLE,
    SERVICE_ENABLE,
    SERVICE_MASK,
    CapabilityState,
)
from openfollow.privilege.drop_in import _is_safe_user, current_device_user

logger = logging.getLogger(__name__)


# Supplemental groups needed for cage, GPU, gamepad, udev access.
REQUIRED_GROUPS: tuple[str, ...] = REQUIRED_HARDWARE_GROUPS

# Services disabled by Ansible installer to shave boot time.
BOOT_DELAY_SERVICES: tuple[str, ...] = (
    "NetworkManager-wait-online.service",
    "cloud-init-main.service",
    "cloud-init-network.service",
    "cloud-init-local.service",
    "cloud-init.target",
    "cloud-config.service",
    "cloud-final.service",
    "bluetooth.service",
    "ModemManager.service",
)


@dataclass(frozen=True)
class RepairAction:
    """One Device Setup Repair row (probe + idempotent apply)."""

    name: str
    description: str
    probe: Callable[[], bool]
    apply: Callable[[PrivilegeBroker], None]


# ----- probes -----


def _user_in_group(user: str, group_name: str) -> bool:
    """True if user is in group (checks primary GID + supplemental via NSS)."""
    try:
        target_gid = grp.getgrnam(group_name).gr_gid
    except KeyError:
        return False
    try:
        pw_gid = pwd.getpwnam(user).pw_gid
    except KeyError:
        return False
    if pw_gid == target_gid:
        return True
    try:
        return target_gid in os.getgrouplist(user, pw_gid)
    except OSError:
        # Fallback to the supplemental-list check if libc can't resolve
        # (unusual; treat as best-effort).
        try:
            return user in grp.getgrnam(group_name).gr_mem
        except KeyError:
            return False


def probe_hardware_groups(user: str | None = None) -> bool:
    """True when ``user`` is a member of every existing
    :data:`REQUIRED_GROUPS` entry.

    Groups missing on the host are skipped by the probe (so the row
    reads "satisfied" rather than "fixable") – but :func:`apply_hardware_groups`
    refuses to run with any required group missing and raises
    :class:`PrivilegeError`. The probe is intentionally less strict
    than apply: showing the row as fixable on a host where the fix
    can't run would confuse the operator more than hiding it.
    """
    resolved = user or current_device_user()
    return all(_user_in_group(resolved, g) for g in REQUIRED_GROUPS if _group_exists(g))


def probe_journal_group(user: str | None = None) -> bool:
    """User is in ``systemd-journal``?  Hosts without the group
    (non-systemd Linux, BSD, …) are treated as "already satisfied"
    so the row doesn't surface as fixable when no fix would help."""
    if not _group_exists("systemd-journal"):
        return True
    return _user_in_group(user or current_device_user(), "systemd-journal")


def probe_linger(user: str | None = None) -> bool:
    """``loginctl show-user <user>`` reports ``Linger=yes``?"""
    if shutil.which("loginctl") is None:
        return True  # nothing to repair on hosts without loginctl
    target = user or current_device_user()
    try:
        proc = subprocess.run(
            ["loginctl", "show-user", target, "--property=Linger"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (subprocess.SubprocessError, OSError):
        # Fail closed: a transient error (timeout / wedged dbus) leaves the
        # row flagged for repair rather than silently satisfied. The
        # binary-missing case is the ``which`` guard above; the idempotent
        # apply re-runs harmlessly if the probe was a false alarm.
        return False
    return "Linger=yes" in proc.stdout


def probe_service_disabled(unit: str) -> bool:
    """``systemctl is-enabled <unit>`` returns ``disabled`` / ``masked``
    / ``static`` / not-installed? Anything except ``enabled`` /
    ``enabled-runtime`` counts as already-disabled for our purposes.

    Special case: a unit in ``enabled-runtime`` whose unit file has
    NO ``[Install]`` section can't actually be disabled – sudo /
    systemctl rejects ``systemctl disable`` with "The unit files
    have no installation config" and the state stays
    ``enabled-runtime`` forever (cloud-init.target is the
    canonical Pi-OS example: pulled in at runtime by another
    target, no persistent enable to revoke). Treat it as
    already-satisfied so the repair loop doesn't keep reporting
    one phantom pending repair on every Apply click. Mirrors
    Ansible's ``failed_when: false`` semantics on the same
    boot-delay list.
    """
    if shutil.which("systemctl") is None:
        return True
    try:
        proc = subprocess.run(
            ["systemctl", "is-enabled", unit],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (subprocess.SubprocessError, OSError):
        return False  # fail closed: transient error → flag for repair, not satisfied
    state = proc.stdout.strip()
    if state not in {"enabled", "enabled-runtime"}:
        return True
    return state == "enabled-runtime" and not _unit_has_install_section(unit)


def _unit_has_install_section(unit: str) -> bool:
    """Return True if ``systemctl cat <unit>`` output contains an
    ``[Install]`` header. Used to detect units that can't be
    disabled via ``systemctl disable`` (no install config). Errors
    default to True so the caller treats unknown units as
    disable-able rather than silently masking a real config gap.
    """
    if shutil.which("systemctl") is None:
        return True
    try:
        proc = subprocess.run(
            ["systemctl", "cat", unit],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (subprocess.SubprocessError, OSError):
        return True
    if proc.returncode != 0:
        return True
    return "[Install]" in proc.stdout


def probe_service_masked(unit: str) -> bool:
    """``systemctl is-enabled <unit>`` returns ``masked``?"""
    if shutil.which("systemctl") is None:
        return True
    try:
        proc = subprocess.run(
            ["systemctl", "is-enabled", unit],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (subprocess.SubprocessError, OSError):
        return False  # fail closed: transient error → flag for repair, not satisfied
    return proc.stdout.strip() == "masked"


# NetworkManager-wait-online is the one boot-delay service the installer
# deliberately keeps *enabled*: network-online.target must wait so the device
# boots with a real IP (otherwise PSN/discovery binds to the wrong address).
# To stop a network-less boot hanging on it, the installer drops
# in a bounded nm-online timeout. The repair must treat that intentional,
# bounded config as already-satisfied; otherwise every Ansible run leaves a
# permanent phantom "pending" on the boot-delay row. A genuinely un-bounded
# enabled unit (no drop-in) is still flagged.
_NM_WAIT_ONLINE = "NetworkManager-wait-online.service"  # == BOOT_DELAY_SERVICES[0]
_NM_WAIT_ONLINE_TIMEOUT_DROPIN = "/etc/systemd/system/NetworkManager-wait-online.service.d/timeout.conf"


def _nm_wait_online_timeout_bounded() -> bool:
    """Check if nm-online timeout drop-in is present."""
    try:
        with open(_NM_WAIT_ONLINE_TIMEOUT_DROPIN, encoding="utf-8") as fh:
            return "--timeout=" in fh.read()
    except OSError:
        return False


def _boot_delay_service_satisfied(unit: str) -> bool:
    """Check if boot-delay service is satisfied (disabled or intentionally enabled)."""
    if unit == _NM_WAIT_ONLINE and _nm_wait_online_timeout_bounded():
        return True
    return probe_service_disabled(unit)


def probe_all_boot_delay_disabled() -> bool:
    """Check if all boot-delay services are satisfied."""
    return all(_boot_delay_service_satisfied(s) for s in BOOT_DELAY_SERVICES)


# ----- appliers -----


def _validated_user(user: str | None) -> str:
    """Resolve the target user and reject an unsafe name before it reaches a
    privileged ``usermod`` / ``loginctl`` argv – the same allowlist
    :func:`render_drop_in` applies before writing the value into a sudoers
    rule. ``current_device_user`` reads unvalidated ``LOGNAME`` / ``USER``."""
    target = user or current_device_user()
    if not _is_safe_user(target):
        raise PrivilegeError(f"Refusing to repair for unsafe user name: {target!r}")
    return target


def apply_hardware_groups(broker: PrivilegeBroker, user: str | None = None) -> None:
    """Add device user to required supplemental groups with canonical list."""
    target = _validated_user(user)
    missing_on_host = [g for g in REQUIRED_GROUPS if not _group_exists(g)]
    if missing_on_host:
        raise PrivilegeError(
            "Cannot apply hardware-group repair: groups missing on this "
            f"host: {', '.join(missing_on_host)}. Create them with "
            f"``sudo groupadd <name>`` first."
        )
    if all(_user_in_group(target, g) for g in REQUIRED_GROUPS):
        # Already a member of every group – nothing to do.
        return
    broker.run(
        DEVICE_GROUP_JOIN,
        ["/usr/sbin/usermod", "-aG", REQUIRED_HARDWARE_GROUPS_JOINED, target],
        reason=f"Add {target} to hardware groups ({REQUIRED_HARDWARE_GROUPS_JOINED})",
        timeout=10,
    )


def apply_journal_group(broker: PrivilegeBroker, user: str | None = None) -> None:
    """Add the device user to ``systemd-journal``. No-op on hosts
    without the group."""
    if not _group_exists("systemd-journal"):
        return
    target = _validated_user(user)
    if _user_in_group(target, "systemd-journal"):
        return
    broker.run(
        JOURNAL_GROUP_JOIN,
        ["/usr/sbin/usermod", "-aG", "systemd-journal", target],
        reason=f"Grant {target} permanent log access",
        timeout=10,
    )


def apply_linger(broker: PrivilegeBroker, user: str | None = None) -> None:
    """``loginctl enable-linger <user>``. Required for
    ``user@<uid>.service`` to start at boot (which cage depends on)."""
    target = _validated_user(user)
    if probe_linger(target):
        return
    broker.run(
        DEVICE_LINGER_ENABLE,
        ["/usr/bin/loginctl", "enable-linger", target],
        reason=f"Enable user linger for {target}",
        timeout=10,
    )


def probe_service_enabled(unit: str) -> bool:
    """``systemctl is-enabled <unit>`` returns ``enabled`` / ``enabled-runtime``?

    Opposite of :func:`probe_service_disabled` – used for the
    "enable at boot" repairs where the apply step does not start the
    service (no ``--now``). Probing for ``is-active`` would never
    succeed on a host where the service is enabled but not running.
    """
    if shutil.which("systemctl") is None:
        return True  # nothing to repair without systemctl
    try:
        proc = subprocess.run(
            ["systemctl", "is-enabled", unit],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (subprocess.SubprocessError, OSError):
        return False  # fail closed: transient error → flag for repair, not satisfied
    return proc.stdout.strip() in {"enabled", "enabled-runtime"}


def apply_enable_seatd(broker: PrivilegeBroker) -> None:
    """``systemctl enable seatd``. cage refuses to launch without it
    on Wayland. We don't pass ``--now`` – the operator can start
    seatd by hand or wait for the next boot. The probe checks
    ``is-enabled`` (not ``is-active``) so the row reads "satisfied"
    after a successful enable, even before the next reboot."""
    if probe_service_enabled("seatd"):
        return
    broker.run(
        SERVICE_ENABLE,
        ["/usr/bin/systemctl", "enable", "seatd"],
        reason="Enable seatd at boot",
        timeout=10,
    )


def apply_disable_boot_delay(broker: PrivilegeBroker) -> None:
    """Disable missing boot-delay services in a single batched call.

    All-or-nothing by design: if any unit in the batch errors, systemd
    returns non-zero and the broker raises, even though earlier units in
    the list may already be disabled. That's acceptable here – the
    ``_boot_delay_service_satisfied`` pre-filter already removes the one
    known un-disable-able shape (``enabled-runtime`` with no ``[Install]``),
    so a residual failure is rare, self-heals on the next Apply (the filter
    re-narrows the batch to only the still-pending units), and the single
    call keeps this to one sudo invocation rather than one per service.
    """
    missing = [unit for unit in BOOT_DELAY_SERVICES if not _boot_delay_service_satisfied(unit)]
    if not missing:
        return
    broker.run(
        SERVICE_DISABLE,
        ["/usr/bin/systemctl", "disable", *missing],
        reason=f"Disable {len(missing)} boot-delay services",
        timeout=15,
    )


def apply_mask_getty(broker: PrivilegeBroker) -> None:
    """``systemctl mask getty@tty1``. Prevents the kernel login
    prompt from fighting cage for the framebuffer."""
    if probe_service_masked("getty@tty1.service"):
        return
    broker.run(
        SERVICE_MASK,
        ["/usr/bin/systemctl", "mask", "getty@tty1.service"],
        reason="Mask getty on tty1",
        timeout=10,
    )


def current_hostname() -> str:
    """Return the host's short (un-qualified) hostname, or ``""`` on error."""
    try:
        return socket.gethostname().split(".", 1)[0]
    except OSError:  # pragma: no cover - gethostname failing is not reproducible
        return ""


def sync_station_hostname(broker: PrivilegeBroker, station_name: str) -> bool:
    """Set the system hostname to the slug of ``station_name`` at boot.

    Self-names the appliance after its OpenFollow identity (e.g.
    ``openfollow-noble-bear``) so avahi advertises a memorable
    ``<slug>.local`` and the operator never has to chase the DHCP address.

    Idempotent and unobtrusive: skips the rename when the hostname already
    matches, when ``hostnamectl`` is absent (dev macOS / minimal host), or when
    the ``device.set_hostname`` grant isn't passwordless – it deliberately never
    prompts for a password just to set a cosmetic hostname, and never raises.
    Returns ``True`` only when it actually changed the hostname.

    When the hostname already matches, or after a successful rename, it also
    repairs /etc/hosts' 127.0.1.1 line (best-effort) – fresh images frequently
    ship a stale loopback mapping that makes every ``sudo`` log "unable to
    resolve host". It deliberately does NOT touch /etc/hosts on the skip paths
    (``hostnamectl`` absent / grant not passwordless): writing ``desired`` there
    would map a name the running system doesn't actually have.
    """
    desired = station_name_to_hostname(station_name)
    if not desired:
        return False
    if desired == current_hostname():
        # Hostname already correct – still repair a stale /etc/hosts loopback
        # line so sudo resolves the name offline (no rename, returns False).
        sync_etc_hosts(broker, desired)
        return False
    if shutil.which("hostnamectl") is None:
        return False
    if broker.state(DEVICE_SET_HOSTNAME) != CapabilityState.PASSWORDLESS:
        # No silent grant – leave the hostname alone rather than popping a
        # password prompt during startup for a non-essential change.
        return False
    try:
        broker.run(
            DEVICE_SET_HOSTNAME,
            ["/usr/bin/hostnamectl", "set-hostname", desired],
            reason="Set device hostname",
            timeout=10,
        )
    except PrivilegeError:
        logger.warning("Could not set system hostname to %r", desired)
        return False
    logger.info("Set system hostname to %s (from station name %r)", desired, station_name)
    # hostnamectl doesn't touch /etc/hosts; keep the 127.0.1.1 loopback line in
    # sync so sudo can resolve the new name (best-effort, never fatal).
    sync_etc_hosts(broker, desired)
    return True


_ETC_HOSTS = Path("/etc/hosts")
_LOOPBACK_RE = re.compile(r"^\s*127\.0\.1\.1\b")
_LOCALHOST_RE = re.compile(r"^\s*127\.0\.0\.1\b")


def ensure_loopback_hosts_line(text: str, hostname: str) -> str:
    """Return ``text`` with exactly one ``127.0.1.1 <hostname>`` line.

    Replaces any existing 127.0.1.1 entries (dropping duplicates); when none
    exists it's inserted right after the 127.0.0.1 line, or appended if that's
    absent too. The result always ends with a trailing newline.
    """
    new_line = f"127.0.1.1\t{hostname}"
    out: list[str] = []
    replaced = False
    for line in text.splitlines():
        if _LOOPBACK_RE.match(line):
            if not replaced:
                out.append(new_line)
                replaced = True
            continue  # drop duplicate / stale 127.0.1.1 lines
        out.append(line)
    if not replaced:
        insert_at = next((i + 1 for i, line in enumerate(out) if _LOCALHOST_RE.match(line)), len(out))
        out.insert(insert_at, new_line)
    return "\n".join(out) + "\n"


def sync_etc_hosts(broker: PrivilegeBroker, hostname: str) -> bool:
    """Best-effort: keep /etc/hosts' 127.0.1.1 line mapped to ``hostname``.

    A no-op when it's already correct, when /etc/hosts can't be read (missing,
    unreadable, or not valid UTF-8), or when the grant isn't passwordless (no
    startup password prompt for a cosmetic fix). Never raises. Returns ``True``
    only when it rewrote the file.
    """
    try:
        # UnicodeDecodeError (a ValueError, not an OSError) for a non-UTF-8
        # /etc/hosts must not escape – this runs at startup.
        current = _ETC_HOSTS.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    updated = ensure_loopback_hosts_line(current, hostname)
    if updated == current:
        return False
    if broker.state(DEVICE_HOSTS_WRITE) != CapabilityState.PASSWORDLESS:
        return False
    try:
        broker.run(
            DEVICE_HOSTS_WRITE,
            ["/usr/bin/tee", str(_ETC_HOSTS)],
            stdin=updated,
            reason="Sync /etc/hosts loopback hostname",
            timeout=10,
        )
    except PrivilegeError:
        logger.warning("Could not update /etc/hosts hostname mapping for %r", hostname)
        return False
    logger.info("Synced /etc/hosts 127.0.1.1 -> %s", hostname)
    return True


# ----- registry -----


def all_actions() -> tuple[RepairAction, ...]:
    """Return every repair action in render order.

    Keeping this a function (rather than a module-level constant)
    means tests can stub ``current_device_user`` without needing to
    reload the module.
    """
    return (
        RepairAction(
            name="repair.hardware_groups",
            description="Join hardware groups (video, render, input, ...)",
            probe=probe_hardware_groups,
            apply=apply_hardware_groups,
        ),
        RepairAction(
            name="repair.journal_group",
            description="Grant permanent log access (systemd-journal group)",
            probe=probe_journal_group,
            apply=apply_journal_group,
        ),
        RepairAction(
            name="repair.linger",
            description="Enable user linger (autostart at boot)",
            probe=probe_linger,
            apply=apply_linger,
        ),
        RepairAction(
            name="repair.seatd",
            description="Enable seatd (required by cage)",
            probe=lambda: probe_service_enabled("seatd"),
            apply=apply_enable_seatd,
        ),
        RepairAction(
            name="repair.boot_delay",
            description="Disable boot-delay services (NM-wait-online, cloud-init, ...)",
            probe=probe_all_boot_delay_disabled,
            apply=apply_disable_boot_delay,
        ),
        RepairAction(
            name="repair.getty_tty1",
            description="Mask getty on tty1",
            probe=lambda: probe_service_masked("getty@tty1.service"),
            apply=apply_mask_getty,
        ),
    )


# ----- helpers -----


def _group_exists(name: str) -> bool:
    try:
        grp.getgrnam(name)
    except KeyError:
        return False
    return True
