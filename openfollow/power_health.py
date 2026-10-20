# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Raspberry Pi supply under-voltage and firmware throttling.

Live under-voltage comes from the ``rpi_volt`` hwmon alarm, a cheap sysfs read
done on the caller's thread. Throttling and the since-boot flags exist only as
``vcgencmd get_throttled``, a subprocess, so :class:`ThrottlePoller` runs it on
its own thread and the caller reads its latest result. A host with neither
reports every flag as ``None`` (not available), never as healthy.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ``vcgencmd get_throttled`` bits. The low ones are live; each has a twin
# ``SINCE_BOOT_SHIFT`` higher that latches until the Pi reboots.
UNDER_VOLTAGE_BIT = 0
SINCE_BOOT_SHIFT = 16
# ARM frequency capped, throttled, soft temperature limit: all of them slow the CPU.
_THROTTLE_MASK = 0b1110

THROTTLE_BIT_LABELS: tuple[tuple[int, str], ...] = (
    (0, "under-voltage NOW"),
    (1, "ARM frequency capped NOW"),
    (2, "currently throttled"),
    (3, "soft temperature limit active"),
    (16, "under-voltage has occurred since boot"),
    (17, "ARM frequency capping has occurred since boot"),
    (18, "throttling has occurred since boot"),
    (19, "soft temperature limit has occurred since boot"),
)

HWMON_ROOT = Path("/sys/class/hwmon")
_RPI_VOLT_NAME = "rpi_volt"
_ALARM_FILE = "in0_lcrit_alarm"

THROTTLE_POLL_S = 5.0
VCGENCMD_TIMEOUT_S = 2.0

# How long the Operator Screen keeps the warning after the last active reading.
WARNING_HOLD_S = 10.0
UNDER_VOLTAGE_WARNING = "Power supply under voltage"
THROTTLED_WARNING = "CPU throttled (too hot)"


def parse_throttled(raw: str | None) -> int | None:
    """The flag bits of a ``throttled=0x50005`` reading, or ``None`` if it is not one."""
    if raw is None:
        return None
    key, sep, value = raw.strip().partition("=")
    if not sep or key != "throttled":
        return None
    try:
        bits = int(value, 16)
    except ValueError:
        return None
    return bits if bits >= 0 else None


def run_vcgencmd_throttled(timeout_s: float = VCGENCMD_TIMEOUT_S) -> str | None:
    """``vcgencmd get_throttled``'s output, or ``None`` when it fails or overruns."""
    try:
        result = subprocess.run(
            ["vcgencmd", "get_throttled"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def find_rpi_volt_alarm(hwmon_root: Path = HWMON_ROOT) -> Path | None:
    """The under-voltage alarm file of the hwmon named ``rpi_volt``, if there is one."""
    try:
        entries = sorted(hwmon_root.iterdir())
    except OSError:
        return None
    for entry in entries:
        try:
            name = (entry / "name").read_text().strip()
        except OSError:
            continue
        if name == _RPI_VOLT_NAME:
            return entry / _ALARM_FILE
    return None


def read_alarm(path: Path) -> bool | None:
    try:
        return int(path.read_text().strip()) != 0
    except (OSError, ValueError):
        return None


@dataclass(frozen=True)
class PowerState:
    """One reading. ``None`` means this host cannot tell."""

    under_voltage: bool | None = None
    throttled: bool | None = None
    under_voltage_since_boot: bool | None = None
    throttled_since_boot: bool | None = None

    @property
    def available(self) -> bool:
        return any(
            flag is not None
            for flag in (self.under_voltage, self.throttled, self.under_voltage_since_boot, self.throttled_since_boot)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "under_voltage": self.under_voltage,
            "throttled": self.throttled,
            "under_voltage_since_boot": self.under_voltage_since_boot,
            "throttled_since_boot": self.throttled_since_boot,
        }


def power_state(alarm: bool | None, bits: int | None) -> PowerState:
    """Combine the hwmon alarm and the ``vcgencmd`` bits into one reading."""
    if bits is None:
        return PowerState(under_voltage=alarm)
    return PowerState(
        under_voltage=bool(alarm) or bool(bits & (1 << UNDER_VOLTAGE_BIT)),
        throttled=bool(bits & _THROTTLE_MASK),
        under_voltage_since_boot=bool(bits & (1 << (UNDER_VOLTAGE_BIT + SINCE_BOOT_SHIFT))),
        throttled_since_boot=bool(bits & (_THROTTLE_MASK << SINCE_BOOT_SHIFT)),
    )


def warning_text(state: PowerState) -> str:
    """The Operator Screen's sentence for a live state, or ``""``.

    An under-voltage caps the CPU too, so while it lasts the supply is named, not heat.
    """
    if state.under_voltage:
        return UNDER_VOLTAGE_WARNING
    if state.throttled:
        return THROTTLED_WARNING
    return ""


class ThrottlePoller:
    """Runs ``vcgencmd get_throttled`` every few seconds on its own thread.

    ``latest`` is the last reading's bits, ``None`` until one succeeds or after
    one fails. One thread per poller: it runs from :meth:`start` to :meth:`stop`.
    """

    def __init__(
        self,
        *,
        run: Callable[[], str | None] = run_vcgencmd_throttled,
        interval_s: float = THROTTLE_POLL_S,
    ) -> None:
        self._run = run
        self._interval_s = interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.latest: int | None = None

    def start(self) -> threading.Thread:
        """Start the thread once; later calls return the same one."""
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="PowerThrottle", daemon=True)
            self._thread.start()
        return self._thread

    def stop(self) -> None:
        """End the thread after its current reading, without waiting for it."""
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.latest = parse_throttled(self._run())
            self._stop.wait(self._interval_s)


class PowerMonitor:
    """Reads the power state; logs once when a live state starts and once when it ends.

    Nothing is touched until the first :meth:`read`: the hwmon is looked up and
    the poller started there, so building one has no side effect.
    """

    def __init__(self, *, hwmon_root: Path = HWMON_ROOT, throttle: ThrottlePoller | None = None) -> None:
        self._hwmon_root = hwmon_root
        self._throttle = throttle
        self._alarm_path: Path | None = None
        self._looked_up = False
        self._under_voltage = False
        self._throttled = False
        self._under_voltage_since_boot = False
        self._throttled_since_boot = False

    def read(self) -> PowerState:
        if not self._looked_up:
            self._looked_up = True
            self._alarm_path = find_rpi_volt_alarm(self._hwmon_root)
            if self._throttle is not None:
                self._throttle.start()
        alarm = read_alarm(self._alarm_path) if self._alarm_path is not None else None
        bits = self._throttle.latest if self._throttle is not None else None
        state = power_state(alarm, bits)
        # The firmware clears its since-boot flags only on a reboot, so one seen survives an unknown reading.
        self._under_voltage_since_boot |= state.under_voltage_since_boot is True
        self._throttled_since_boot |= state.throttled_since_boot is True
        state = replace(
            state,
            under_voltage_since_boot=self._under_voltage_since_boot or state.under_voltage_since_boot,
            throttled_since_boot=self._throttled_since_boot or state.throttled_since_boot,
        )
        self._under_voltage = _log_edge(
            self._under_voltage, state.under_voltage, "Power supply under voltage", "Power supply voltage normal"
        )
        self._throttled = _log_edge(self._throttled, state.throttled, "CPU throttled", "CPU throttling ended")
        return state

    def stop(self) -> None:
        if self._throttle is not None:
            self._throttle.stop()


def _log_edge(was: bool, now: bool | None, started: str, ended: str) -> bool:
    # An unknown reading neither starts nor ends a state.
    if now is None:
        return was
    if now and not was:
        logger.warning(started)
    elif was and not now:
        logger.info(ended)
    return now


def system_power_monitor() -> PowerMonitor:
    """The monitor for this host; it polls ``vcgencmd`` only where there is one."""
    return PowerMonitor(throttle=ThrottlePoller() if shutil.which("vcgencmd") else None)


class PowerWarningHold:
    """Keeps the Operator Screen warning up for ``hold_s`` after the last active reading.

    A burst of short dips reads as one steady warning, and a single dip between
    two glances is still seen.
    """

    def __init__(self, hold_s: float = WARNING_HOLD_S) -> None:
        self._hold_s = hold_s
        self._text = ""
        self._until = 0.0

    def update(self, state: PowerState, now: float) -> str:
        """The warning to show at ``now``, in monotonic seconds."""
        text = warning_text(state)
        if text:
            self._text, self._until = text, now + self._hold_s
        elif now >= self._until:
            self._text = ""
        return self._text
