# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Paul Hermann, Michel Honold, and Vinzenz Schultz
# Copyright (C) 2026 Paul Hermann and Michel Honold
"""The station's system hostname and its /etc/hosts loopback line, kept in step with the station name."""

from __future__ import annotations

import logging
import re
import shutil
import socket
import threading
from pathlib import Path

from openfollow.marker_catalog.station_name import station_name_to_hostname
from openfollow.privilege.broker import PrivilegeBroker, PrivilegeError
from openfollow.privilege.capabilities import (
    DEVICE_HOSTS_WRITE,
    DEVICE_SET_HOSTNAME,
    CapabilityState,
)

logger = logging.getLogger(__name__)


def full_hostname() -> str:
    """Return the host's full hostname as the OS reports it now, or ``""`` on error."""
    try:
        return socket.gethostname()
    except OSError:
        return ""


def current_hostname() -> str:
    """Return the host's short (un-qualified) hostname, or ``""`` on error."""
    return full_hostname().split(".", 1)[0]


# A rename (main loop) and an FQDN change (its worker) both rewrite /etc/hosts from
# the running hostname; one at a time, or the slower one maps a name already gone.
_HOSTS_LOCK = threading.Lock()


def sync_station_hostname(broker: PrivilegeBroker, station_name: str, fqdn: str = "") -> bool:
    """Set the system hostname to the slug of ``station_name``; see ``_sync_station_hostname``."""
    with _HOSTS_LOCK:
        return _sync_station_hostname(broker, station_name, fqdn)


def sync_running_hostname_hosts(broker: PrivilegeBroker, fqdn: str) -> bool:
    """Map the running hostname, with ``fqdn`` before it, on /etc/hosts' loopback line."""
    with _HOSTS_LOCK:
        name = current_hostname()
        return bool(name) and sync_etc_hosts(broker, name, fqdn)


def _sync_station_hostname(broker: PrivilegeBroker, station_name: str, fqdn: str = "") -> bool:
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
        sync_etc_hosts(broker, desired, fqdn)
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
            # The state check above can be stale; a prompt here would wait unseen.
            allow_prompt=False,
        )
    except PrivilegeError:
        logger.warning("Could not set system hostname to %r", desired)
        return False
    logger.info("Set system hostname to %s (from station name %r)", desired, station_name)
    # hostnamectl doesn't touch /etc/hosts; keep the 127.0.1.1 loopback line in
    # sync so sudo can resolve the new name (best-effort, never fatal).
    sync_etc_hosts(broker, desired, fqdn)
    return True


_ETC_HOSTS = Path("/etc/hosts")
_LOOPBACK_RE = re.compile(r"^\s*127\.0\.1\.1\b")
_LOCALHOST_RE = re.compile(r"^\s*127\.0\.0\.1\b")


def ensure_loopback_hosts_line(text: str, hostname: str, fqdn: str = "") -> str:
    """Return ``text`` with exactly one ``127.0.1.1 [<fqdn>] <hostname>`` line.

    The FQDN goes first, as the canonical name. Replaces any existing 127.0.1.1
    entries (dropping duplicates); when none exists it's inserted right after
    the 127.0.0.1 line, or appended if that's absent too. The result always ends
    with a trailing newline.
    """
    new_line = f"127.0.1.1\t{fqdn} {hostname}" if fqdn else f"127.0.1.1\t{hostname}"
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


def sync_etc_hosts(broker: PrivilegeBroker, hostname: str, fqdn: str = "") -> bool:
    """Best-effort: keep /etc/hosts' 127.0.1.1 line mapped to ``hostname`` (and ``fqdn``).

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
    updated = ensure_loopback_hosts_line(current, hostname, fqdn)
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
            allow_prompt=False,
        )
    except PrivilegeError:
        logger.warning("Could not update /etc/hosts hostname mapping for %r", hostname)
        return False
    logger.info("Synced /etc/hosts 127.0.1.1 -> %s", f"{fqdn} {hostname}" if fqdn else hostname)
    return True
