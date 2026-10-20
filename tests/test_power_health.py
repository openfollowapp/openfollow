# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Supply under-voltage and throttling: the hwmon alarm, ``vcgencmd``, the hold."""

from __future__ import annotations

import logging
import subprocess
import threading
from pathlib import Path
from typing import Any

import pytest

import openfollow.power_health as ph
from openfollow.power_health import (
    THROTTLED_WARNING,
    UNDER_VOLTAGE_WARNING,
    WARNING_HOLD_S,
    PowerMonitor,
    PowerState,
    PowerWarningHold,
    ThrottlePoller,
    find_rpi_volt_alarm,
    parse_throttled,
    power_state,
    read_alarm,
    run_vcgencmd_throttled,
    system_power_monitor,
    warning_text,
)

pytestmark = pytest.mark.unit


def _hwmon(root: Path, index: int, name: str, alarm: str | None = None) -> Path:
    entry = root / f"hwmon{index}"
    entry.mkdir(parents=True)
    (entry / "name").write_text(f"{name}\n")
    if alarm is not None:
        (entry / "in0_lcrit_alarm").write_text(f"{alarm}\n")
    return entry


def _polled(*readings: str | None) -> ThrottlePoller:
    """A poller that has already taken ``readings`` in order, on its own thread."""
    stop = threading.Event()
    queue = list(readings)

    def run() -> str | None:
        reading = queue.pop(0)
        if not queue:
            stop.set()
        return reading

    poller = ThrottlePoller(run=run, interval_s=0.0, stop=stop)
    poller.start().join(timeout=5.0)
    return poller


# --------------------------------------------------------------------------- #
# vcgencmd output
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("throttled=0x0", 0),
        ("throttled=0x50005\n", 0x50005),
        ("  throttled=0x80000  ", 0x80000),
    ],
)
def test_a_reading_parses_to_its_bits(raw: str, expected: int) -> None:
    assert parse_throttled(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [None, "", "garbage", "throttled=", "throttled=0xzz", "temp=0x1", "VCHI initialization failed"],
    ids=["none", "empty", "garbage", "no-value", "not-hex", "other-key", "error-text"],
)
def test_anything_else_is_not_a_reading(raw: str | None) -> None:
    assert parse_throttled(raw) is None


class _Completed:
    def __init__(self, returncode: int, stdout: str) -> None:
        self.returncode = returncode
        self.stdout = stdout


def test_vcgencmd_runs_with_a_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_run(cmd: list[str], **kwargs: Any) -> _Completed:
        seen["cmd"] = cmd
        seen.update(kwargs)
        return _Completed(0, "throttled=0x1\n")

    monkeypatch.setattr(ph.subprocess, "run", fake_run)
    assert run_vcgencmd_throttled() == "throttled=0x1\n"
    assert seen["cmd"] == ["vcgencmd", "get_throttled"]
    assert seen["timeout"] == ph.VCGENCMD_TIMEOUT_S


def test_a_failing_vcgencmd_reads_as_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ph.subprocess, "run", lambda *_a, **_k: _Completed(255, "throttled=0x0"))
    assert run_vcgencmd_throttled() is None


@pytest.mark.parametrize(
    "error",
    [FileNotFoundError("vcgencmd"), PermissionError("vcgencmd"), subprocess.TimeoutExpired("vcgencmd", 2.0)],
    ids=["missing", "not-permitted", "timed-out"],
)
def test_a_vcgencmd_that_cannot_answer_reads_as_nothing(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    def fake_run(*_a: Any, **_k: Any) -> _Completed:
        raise error

    monkeypatch.setattr(ph.subprocess, "run", fake_run)
    assert run_vcgencmd_throttled() is None


# --------------------------------------------------------------------------- #
# The rpi_volt hwmon
# --------------------------------------------------------------------------- #


def test_the_alarm_is_found_by_the_hwmon_name(tmp_path: Path) -> None:
    _hwmon(tmp_path, 0, "cpu_thermal")
    volt = _hwmon(tmp_path, 1, "rpi_volt", "0")
    assert find_rpi_volt_alarm(tmp_path) == volt / "in0_lcrit_alarm"


def test_an_hwmon_without_a_name_is_skipped(tmp_path: Path) -> None:
    (tmp_path / "hwmon0").mkdir()
    volt = _hwmon(tmp_path, 1, "rpi_volt", "0")
    assert find_rpi_volt_alarm(tmp_path) == volt / "in0_lcrit_alarm"


@pytest.mark.parametrize("layout", ["no-root", "other-hwmons"])
def test_no_rpi_volt_hwmon_means_no_alarm(tmp_path: Path, layout: str) -> None:
    root = tmp_path / "hwmon-root"
    if layout == "other-hwmons":
        _hwmon(root, 0, "cpu_thermal")
        _hwmon(root, 1, "rpi_volt_x", "1")
    assert find_rpi_volt_alarm(root) is None


@pytest.mark.parametrize(("content", "expected"), [("1\n", True), ("0\n", False)])
def test_the_alarm_reads_as_a_flag(tmp_path: Path, content: str, expected: bool) -> None:
    alarm = tmp_path / "in0_lcrit_alarm"
    alarm.write_text(content)
    assert read_alarm(alarm) is expected


@pytest.mark.parametrize("layout", ["missing", "malformed", "a-directory"])
def test_an_unreadable_alarm_is_unknown(tmp_path: Path, layout: str) -> None:
    alarm = tmp_path / "in0_lcrit_alarm"
    if layout == "malformed":
        alarm.write_text("abc")
    elif layout == "a-directory":
        alarm.mkdir()
    assert read_alarm(alarm) is None


# --------------------------------------------------------------------------- #
# One reading
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("bits", "expected"),
    [
        (0x0, PowerState(False, False, False, False)),
        (0x1, PowerState(True, False, False, False)),
        (0x2, PowerState(False, True, False, False)),
        (0x4, PowerState(False, True, False, False)),
        (0x8, PowerState(False, True, False, False)),
        (0x10000, PowerState(False, False, True, False)),
        (0x20000, PowerState(False, False, False, True)),
        (0x40000, PowerState(False, False, False, True)),
        (0x80000, PowerState(False, False, False, True)),
        (0x50005, PowerState(True, True, True, True)),
        (0x100, PowerState(False, False, False, False)),
    ],
    ids=[
        "none",
        "under-voltage",
        "capped",
        "throttled",
        "soft-limit",
        "under-voltage-since-boot",
        "capped-since-boot",
        "throttled-since-boot",
        "soft-limit-since-boot",
        "all-of-it",
        "unknown-bit",
    ],
)
def test_each_bit_lands_on_its_flag(bits: int, expected: PowerState) -> None:
    assert power_state(None, bits) == expected


@pytest.mark.parametrize(
    ("alarm", "bits", "under_voltage"),
    [(True, 0x0, True), (False, 0x1, True), (False, 0x0, False), (None, 0x1, True)],
    ids=["alarm-only", "vcgencmd-only", "neither", "no-hwmon"],
)
def test_either_source_raises_under_voltage(alarm: bool | None, bits: int, under_voltage: bool) -> None:
    assert power_state(alarm, bits).under_voltage is under_voltage


def test_without_vcgencmd_only_the_live_under_voltage_is_known() -> None:
    assert power_state(True, None) == PowerState(under_voltage=True)
    assert power_state(True, None).available is True


def test_a_host_with_neither_reports_not_available_never_healthy() -> None:
    state = power_state(None, None)
    assert state.available is False
    assert state.to_dict() == {
        "available": False,
        "under_voltage": None,
        "throttled": None,
        "under_voltage_since_boot": None,
        "throttled_since_boot": None,
    }


def test_a_reading_is_published_flag_by_flag() -> None:
    assert power_state(False, 0x40000).to_dict() == {
        "available": True,
        "under_voltage": False,
        "throttled": False,
        "under_voltage_since_boot": False,
        "throttled_since_boot": True,
    }


@pytest.mark.parametrize(
    ("state", "text"),
    [
        (PowerState(under_voltage=True, throttled=True), UNDER_VOLTAGE_WARNING),
        (PowerState(under_voltage=False, throttled=True), THROTTLED_WARNING),
        (PowerState(under_voltage=False, throttled=False, throttled_since_boot=True), ""),
        (PowerState(), ""),
    ],
    ids=["supply-named-over-heat", "too-hot", "past-only", "not-available"],
)
def test_the_operator_screen_names_the_live_cause(state: PowerState, text: str) -> None:
    assert warning_text(state) == text


# --------------------------------------------------------------------------- #
# The Operator Screen hold
# --------------------------------------------------------------------------- #

_DIP = PowerState(under_voltage=True, throttled=False)
_HOT = PowerState(under_voltage=False, throttled=True)
_CLEAR = PowerState(under_voltage=False, throttled=False)


def test_a_single_dip_is_held_for_the_hold_time() -> None:
    hold = PowerWarningHold()
    assert hold.update(_DIP, 100.0) == UNDER_VOLTAGE_WARNING
    assert hold.update(_CLEAR, 100.0 + WARNING_HOLD_S - 0.1) == UNDER_VOLTAGE_WARNING
    assert hold.update(_CLEAR, 100.0 + WARNING_HOLD_S) == ""


def test_a_burst_of_dips_reads_as_one_steady_warning() -> None:
    hold = PowerWarningHold(hold_s=10.0)
    shown = [hold.update(_DIP if t % 6 == 0 else _CLEAR, float(t)) for t in range(0, 25)]
    assert shown == [UNDER_VOLTAGE_WARNING] * 25
    assert hold.update(_CLEAR, 34.0) == ""


def test_the_latest_cause_is_the_one_held() -> None:
    hold = PowerWarningHold()
    hold.update(_DIP, 0.0)
    assert hold.update(_HOT, 1.0) == THROTTLED_WARNING
    assert hold.update(_CLEAR, 2.0) == THROTTLED_WARNING


def test_nothing_is_shown_without_a_live_state() -> None:
    assert PowerWarningHold().update(PowerState(), 0.0) == ""


# --------------------------------------------------------------------------- #
# The vcgencmd thread
# --------------------------------------------------------------------------- #


def test_the_poller_keeps_the_latest_reading() -> None:
    assert _polled("throttled=0x0", "throttled=0x50005").latest == 0x50005


@pytest.mark.parametrize("failure", [None, "garbage"], ids=["failed", "malformed"])
def test_a_failed_poll_after_a_good_one_is_unknown(failure: str | None) -> None:
    assert _polled("throttled=0x1", failure).latest is None


def test_the_poller_starts_one_thread() -> None:
    poller = _polled("throttled=0x0")
    assert poller.start() is poller.start()


def test_a_poller_has_nothing_before_its_first_reading() -> None:
    assert ThrottlePoller(run=lambda: "throttled=0x1").latest is None


def test_the_poller_waits_between_readings() -> None:
    waits: list[float] = []

    class _Stop:
        def wait(self, timeout: float) -> bool:
            waits.append(timeout)
            return True

    poller = ThrottlePoller(run=lambda: "throttled=0x0", stop=_Stop())  # type: ignore[arg-type]
    poller.start().join(timeout=5.0)
    assert waits == [ph.THROTTLE_POLL_S]


# --------------------------------------------------------------------------- #
# The monitor
# --------------------------------------------------------------------------- #


def test_a_host_without_either_source_reads_not_available(tmp_path: Path) -> None:
    assert PowerMonitor(hwmon_root=tmp_path / "absent").read().available is False


def test_the_monitor_combines_the_alarm_with_the_poller(tmp_path: Path) -> None:
    _hwmon(tmp_path, 0, "rpi_volt", "1")
    state = PowerMonitor(hwmon_root=tmp_path, throttle=_polled("throttled=0x40000")).read()
    assert state == PowerState(
        under_voltage=True, throttled=False, under_voltage_since_boot=False, throttled_since_boot=True
    )


def test_the_monitor_follows_the_alarm(tmp_path: Path) -> None:
    entry = _hwmon(tmp_path, 0, "rpi_volt", "1")
    monitor = PowerMonitor(hwmon_root=tmp_path)
    assert monitor.read().under_voltage is True
    (entry / "in0_lcrit_alarm").write_text("0\n")
    assert monitor.read().under_voltage is False


def test_the_hwmon_is_scanned_once_not_every_second(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _hwmon(tmp_path, 0, "rpi_volt", "1")
    scans: list[Path] = []

    def counting(root: Path) -> Path | None:
        scans.append(root)
        return find_rpi_volt_alarm(root)

    monkeypatch.setattr(ph, "find_rpi_volt_alarm", counting)
    monitor = PowerMonitor(hwmon_root=tmp_path)
    assert [monitor.read().under_voltage for _ in range(3)] == [True, True, True]
    assert scans == [tmp_path]


def test_an_alarm_that_becomes_unreadable_is_unknown(tmp_path: Path) -> None:
    entry = _hwmon(tmp_path, 0, "rpi_volt", "1")
    monitor = PowerMonitor(hwmon_root=tmp_path)
    monitor.read()
    (entry / "in0_lcrit_alarm").unlink()
    assert monitor.read().under_voltage is None


def test_the_monitor_starts_the_poller_on_its_first_read(tmp_path: Path) -> None:
    started = threading.Event()
    stop = threading.Event()
    stop.set()

    def run() -> str:
        started.set()
        return "throttled=0x0"

    PowerMonitor(hwmon_root=tmp_path, throttle=ThrottlePoller(run=run, stop=stop))
    assert not started.is_set()
    PowerMonitor(hwmon_root=tmp_path, throttle=ThrottlePoller(run=run, stop=stop)).read()
    assert started.wait(timeout=5.0)


def test_a_state_is_logged_when_it_starts_and_when_it_ends(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    entry = _hwmon(tmp_path, 0, "rpi_volt", "1")
    alarm = entry / "in0_lcrit_alarm"
    monitor = PowerMonitor(hwmon_root=tmp_path)
    caplog.set_level(logging.INFO, logger=ph.__name__)
    monitor.read()
    monitor.read()
    alarm.write_text("0\n")
    monitor.read()
    monitor.read()
    assert [(r.levelno, r.getMessage()) for r in caplog.records] == [
        (logging.WARNING, "Power supply under voltage"),
        (logging.INFO, "Power supply voltage normal"),
    ]


def test_throttling_is_logged_when_it_starts_and_when_it_ends(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger=ph.__name__)
    poller = _polled("throttled=0x4")
    monitor = PowerMonitor(hwmon_root=tmp_path, throttle=poller)
    monitor.read()
    monitor.read()
    poller.latest = 0x40000
    monitor.read()
    assert [(r.levelno, r.getMessage()) for r in caplog.records] == [
        (logging.WARNING, "CPU throttled"),
        (logging.INFO, "CPU throttling ended"),
    ]


@pytest.mark.parametrize(("found", "polls"), [("/usr/bin/vcgencmd", True), (None, False)])
def test_the_station_polls_vcgencmd_only_where_it_exists(
    monkeypatch: pytest.MonkeyPatch, found: str | None, polls: bool
) -> None:
    built: list[object] = []

    class _Poller:
        def __init__(self) -> None:
            built.append(self)

    monkeypatch.setattr(ph.shutil, "which", lambda name: found if name == "vcgencmd" else None)
    monkeypatch.setattr(ph, "ThrottlePoller", _Poller)
    assert isinstance(system_power_monitor(), PowerMonitor)
    assert bool(built) is polls


def test_an_unreadable_alarm_neither_ends_nor_restarts_under_voltage(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=ph.__name__)
    alarm = _hwmon(tmp_path, 0, "rpi_volt", "1") / "in0_lcrit_alarm"
    monitor = PowerMonitor(hwmon_root=tmp_path)
    monitor.read()
    alarm.write_text("abc")
    monitor.read()
    alarm.write_text("1\n")
    monitor.read()
    assert [(r.levelno, r.getMessage()) for r in caplog.records] == [(logging.WARNING, "Power supply under voltage")]


def test_a_failed_poll_neither_ends_nor_restarts_throttling(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger=ph.__name__)
    poller = _polled("throttled=0x4")
    monitor = PowerMonitor(hwmon_root=tmp_path, throttle=poller)
    monitor.read()
    poller.latest = None
    monitor.read()
    poller.latest = 0x4
    monitor.read()
    assert [(r.levelno, r.getMessage()) for r in caplog.records] == [(logging.WARNING, "CPU throttled")]
