# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Paul Hermann, Michel Honold, and Vinzenz Schultz
# Copyright (C) 2026 Paul Hermann and Michel Honold
"""System statistics collector for CPU, RAM, temperature and power.

Cross-platform: CPU and RAM work everywhere via psutil.
Temperature, under-voltage and throttling are only available on a Raspberry Pi.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import psutil

from openfollow.net_utils import (
    get_iface_for_ip,
    get_local_ipv4_addresses,
    get_primary_local_ipv4,
)
from openfollow.power_health import PowerMonitor, PowerState, PowerWarningHold

# Literal IP or callable for lazy resolution (callable needed before wait_for_source_ip).
PreferredIpProvider = str | Callable[[], str]


# Raspberry Pi thermal zone file (returns temperature in millidegrees Celsius)
_THERMAL_ZONE_PATH = Path("/sys/class/thermal/thermal_zone0/temp")


@dataclass
class SystemStats:
    """Snapshot of system statistics."""

    cpu_percent: float = 0.0
    ram_percent: float = 0.0
    temperature: float | None = None  # Celsius, None if unavailable
    ip_address: str = "N/A"  # Local IPv4 address
    iface_name: str = ""  # Interface holding ip_address; empty if offline/loopback.
    power: PowerState = field(default_factory=PowerState)
    # The Operator Screen's power warning, held past the last active reading; "" when none.
    power_warning: str = ""


class SystemStatsCollector:
    """Collects system statistics with rate limiting.

    Thread model: Call update() from the main thread periodically.
    The collector caches readings to avoid excessive system calls.
    """

    def __init__(
        self,
        update_interval: float = 1.0,
        preferred_ip: PreferredIpProvider = "",
        power: PowerMonitor | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        """Initialize the collector.

        Args:
            update_interval: Minimum seconds between actual system reads.
            preferred_ip: If non-empty, always display this IP instead of
                auto-detecting the primary interface (use when a specific
                network interface is configured, e.g. resolved from
                ``psn_source_iface``). Accepts a literal ``str`` OR a
                zero-arg callable that returns the current preferred IP.
                The callable form is required when the source pin is
                resolved *after* this collector is constructed –
                ``init_video`` runs before the startup
                ``wait_for_source_ip``, so a literal captured then is
                often empty / wrong and the HUD would lag the real
                bind for the rest of the session.
            power: Reads under-voltage and throttling; ``None`` reports them
                as not available.
            clock: Monotonic seconds, for the rate limit and the power
                warning's hold.
        """
        self._update_interval = update_interval
        self._preferred_ip = preferred_ip
        self._power = power
        self._clock = clock if clock is not None else time.monotonic
        self._power_hold = PowerWarningHold()
        self._last_update: float = 0.0
        self._stats = SystemStats()
        self._temp_available: bool | None = None  # None = not yet checked

        # Initialize CPU percent (first call returns 0, need to "prime" it)
        psutil.cpu_percent(interval=None)

    def _resolve_preferred_ip(self) -> str:
        """Return current preferred_ip, resolving callables. Failures return ''."""
        provider = self._preferred_ip
        if callable(provider):
            try:
                return provider() or ""
            except Exception:  # noqa: BLE001
                return ""
        return provider or ""

    @property
    def stats(self) -> SystemStats:
        """Get the most recent statistics snapshot."""
        return self._stats

    def stop(self) -> None:
        """Stop the power monitor's thread."""
        if self._power is not None:
            self._power.stop()

    def update(self) -> SystemStats:
        """Update statistics if enough time has passed.

        Returns:
            Current statistics snapshot.
        """
        now = self._clock()
        if now - self._last_update < self._update_interval:
            return self._stats

        self._last_update = now

        # CPU (non-blocking, uses delta since last call)
        cpu = psutil.cpu_percent(interval=None)

        # RAM
        mem = psutil.virtual_memory()
        ram = mem.percent

        # Temperature (Pi only)
        temp = self._read_temperature()

        # IP address: use configured interface if set and still valid, otherwise auto-detect
        preferred = self._resolve_preferred_ip()
        if preferred and preferred in get_local_ipv4_addresses():
            ip = preferred
        else:
            ip = get_primary_local_ipv4()

        # Reverse-lookup iface name for display; refresh every tick.
        iface = get_iface_for_ip(ip)

        power = self._power.read(now) if self._power is not None else PowerState()

        self._stats = SystemStats(
            cpu_percent=cpu,
            ram_percent=ram,
            temperature=temp,
            ip_address=ip,
            iface_name=iface,
            power=power,
            power_warning=self._power_hold.update(power, now),
        )
        return self._stats

    def _read_temperature(self) -> float | None:
        """Read CPU temperature in Celsius, or None if unavailable."""
        # Skip if we already know temp is unavailable.
        if self._temp_available is False:
            return None

        try:
            raw = _THERMAL_ZONE_PATH.read_text().strip()
            temp = int(raw) / 1000.0  # Convert millidegrees to degrees
            self._temp_available = True
            return temp
        except (OSError, ValueError):
            # File unavailable (not on Pi or unreadable).
            self._temp_available = False
            return None
