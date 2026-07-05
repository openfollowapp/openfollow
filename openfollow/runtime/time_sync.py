# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Trusted-time fetch (SNTP) and system-clock set via the privilege broker.

A Pi has no battery-backed RTC, so it boots with a stale clock (restored from
``fake-hwclock`` at last shutdown). When the show LAN briefly has internet,
fetch the real time over NTP and set the clock – this runs *before* any HTTPS
so TLS certificate validation isn't broken by a wildly wrong clock.
"""

from __future__ import annotations

import logging
import socket
import struct

from openfollow.privilege.broker import PrivilegeBroker, PrivilegeError
from openfollow.privilege.capabilities import SYSTEM_SET_CLOCK, CapabilityState

logger = logging.getLogger(__name__)

# Seconds between the NTP epoch (1900-01-01) and the Unix epoch (1970-01-01).
_NTP_EPOCH_OFFSET = 2_208_988_800
_NTP_PORT = 123
# 48-byte SNTP request: first byte 0x1B = LI 0, VN 3, Mode 3 (client).
_NTP_REQUEST = b"\x1b" + 47 * b"\x00"

# Plausibility window for a fetched wall-clock time. A value outside it is a
# malformed / garbage reply, never a real drift correction, so we refuse to
# set the clock to it. Lower bound sits comfortably in the past (well before
# this code could run); upper bound guards a far-future garbage value.
_MIN_PLAUSIBLE_EPOCH = 1_735_689_600  # 2025-01-01T00:00:00Z
_MAX_PLAUSIBLE_EPOCH = 4_102_444_800  # 2100-01-01T00:00:00Z

# Don't churn the clock for sub-second noise; only correct meaningful drift.
DRIFT_THRESHOLD_S = 2.0


def query_ntp(server: str, timeout: float = 5.0) -> float:
    """Return *server*'s current wall-clock time as Unix epoch seconds.

    Minimal SNTP client (RFC 4330): one UDP request, parse the transmit
    timestamp from the reply. Raises ``OSError`` (network) or ``ValueError``
    (empty server / malformed reply); the caller swallows both.
    """
    server = (server or "").strip()
    if not server:
        raise ValueError("empty NTP server")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        sock.sendto(_NTP_REQUEST, (server, _NTP_PORT))
        data, _addr = sock.recvfrom(48)
    if len(data) < 48:
        raise ValueError(f"short NTP reply ({len(data)} bytes)")
    # Transmit timestamp: bytes 40-47, seconds in the first 4 (big-endian).
    seconds: int = struct.unpack("!I", data[40:44])[0]
    if seconds == 0:
        raise ValueError("NTP reply has a zero transmit timestamp")
    return float(seconds - _NTP_EPOCH_OFFSET)


def is_plausible_epoch(epoch: float) -> bool:
    """True when *epoch* falls inside the accepted clock-set window."""
    return _MIN_PLAUSIBLE_EPOCH <= epoch <= _MAX_PLAUSIBLE_EPOCH


def set_system_clock(broker: PrivilegeBroker | None, epoch: int) -> bool:
    """Set the system clock to *epoch* (Unix seconds) via ``date -s @<epoch>``.

    Returns ``True`` on success. Returns ``False`` (logged, never raised) when
    the value is implausible, the broker is missing, the capability is not
    granted passwordless, or the command fails – the caller treats every
    non-success as a silent no-op.

    The capability is run ONLY when already ``PASSWORDLESS``: a background sync
    must never pop the operator password prompt. Until the sudoers drop-in is
    applied (Device page), auto time-sync simply no-ops.
    """
    if broker is None:
        return False
    epoch = int(epoch)
    if not is_plausible_epoch(epoch):
        logger.warning("Refusing to set the clock to an implausible epoch (%d).", epoch)
        return False
    if broker.state(SYSTEM_SET_CLOCK) is not CapabilityState.PASSWORDLESS:
        logger.debug("Clock-set not granted passwordless; skipping auto time-sync.")
        return False
    try:
        # ``allow_prompt=False``: if the grant vanished since the (cached) state
        # check, fail closed rather than pop a password dialog from this
        # background thread.
        broker.run(
            SYSTEM_SET_CLOCK,
            ["/usr/bin/date", "-s", f"@{epoch}"],
            reason="Sync system clock from a trusted time source",
            timeout=10,
            allow_prompt=False,
        )
    except PrivilegeError as exc:
        logger.debug("Clock-set failed: %s", exc)
        return False
    logger.info("System clock set from trusted time source (epoch %d).", epoch)
    return True
