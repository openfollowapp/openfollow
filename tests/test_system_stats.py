# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for SystemStatsCollector: IP preference, thermal read, rate-limiting."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import openfollow.system_stats as stats_module
from openfollow.system_stats import SystemStatsCollector

pytestmark = pytest.mark.unit


def _stub_psutil(monkeypatch, cpu_value: float = 42.0, ram_value: float = 61.5) -> None:
    monkeypatch.setattr(stats_module.psutil, "cpu_percent", lambda interval=None: cpu_value)
    monkeypatch.setattr(
        stats_module.psutil,
        "virtual_memory",
        lambda: SimpleNamespace(percent=ram_value),
    )


def test_stats_collector_prefers_configured_ip_when_available(monkeypatch, tmp_path: Path) -> None:
    _stub_psutil(monkeypatch)
    temp_file = tmp_path / "thermal"
    temp_file.write_text("42000", encoding="utf-8")
    monkeypatch.setattr(stats_module, "_THERMAL_ZONE_PATH", temp_file)
    monkeypatch.setattr(stats_module, "get_local_ipv4_addresses", lambda: {"10.0.0.2"})
    monkeypatch.setattr(stats_module, "get_primary_local_ipv4", lambda default="N/A": "10.0.0.99")
    monkeypatch.setattr(
        stats_module,
        "get_iface_for_ip",
        lambda ip: "eth0" if ip == "10.0.0.2" else "",
    )

    collector = SystemStatsCollector(update_interval=0.0, preferred_ip="10.0.0.2")
    stats = collector.update()

    assert stats.ip_address == "10.0.0.2"
    # Iface name is reverse-resolved from the IP for HUD display.
    assert stats.iface_name == "eth0"
    assert stats.temperature == pytest.approx(42.0)
    assert stats.cpu_percent == 42.0
    assert stats.ram_percent == 61.5


def test_stats_collector_falls_back_to_primary_ip(monkeypatch, tmp_path: Path) -> None:
    _stub_psutil(monkeypatch)
    temp_file = tmp_path / "missing-thermal"
    monkeypatch.setattr(stats_module, "_THERMAL_ZONE_PATH", temp_file)
    monkeypatch.setattr(stats_module, "get_local_ipv4_addresses", lambda: {"10.0.0.3"})
    monkeypatch.setattr(stats_module, "get_primary_local_ipv4", lambda default="N/A": "10.0.0.99")
    monkeypatch.setattr(
        stats_module,
        "get_iface_for_ip",
        lambda ip: "wlan0" if ip == "10.0.0.99" else "",
    )

    collector = SystemStatsCollector(update_interval=0.0, preferred_ip="10.0.0.2")
    stats = collector.update()

    assert stats.ip_address == "10.0.0.99"
    assert stats.iface_name == "wlan0"
    assert stats.temperature is None


def test_stats_collector_stats_property_returns_current_snapshot(monkeypatch) -> None:
    """The ``stats`` property exposes the cached snapshot without
    triggering an update."""
    _stub_psutil(monkeypatch)
    monkeypatch.setattr(stats_module, "get_local_ipv4_addresses", lambda: set())
    monkeypatch.setattr(
        stats_module,
        "get_primary_local_ipv4",
        lambda default="N/A": "10.0.0.1",
    )
    monkeypatch.setattr(stats_module, "get_iface_for_ip", lambda _ip: "")
    collector = SystemStatsCollector(update_interval=0.0)
    snapshot = collector.stats
    assert snapshot is collector._stats


def test_stats_collector_iface_empty_when_ip_not_bound(monkeypatch) -> None:
    """When the resolved IP isn't currently held by any local NIC
    (offline host / stale snapshot), ``iface_name`` is empty so the
    HUD falls back to the bare IP rendering."""
    _stub_psutil(monkeypatch)
    monkeypatch.setattr(stats_module, "get_local_ipv4_addresses", lambda: set())
    monkeypatch.setattr(
        stats_module,
        "get_primary_local_ipv4",
        lambda default="N/A": "10.0.0.1",
    )
    monkeypatch.setattr(stats_module, "get_iface_for_ip", lambda _ip: "")

    collector = SystemStatsCollector(update_interval=0.0)
    stats = collector.update()

    assert stats.ip_address == "10.0.0.1"
    assert stats.iface_name == ""


def test_stats_collector_skips_temperature_read_after_first_failure(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Once ``_read_temperature`` failed to find the thermal-zone file,
    subsequent calls short-circuit at the ``_temp_available is False``
    guard rather than re-attempting the read on every tick."""
    _stub_psutil(monkeypatch)
    monkeypatch.setattr(stats_module, "_THERMAL_ZONE_PATH", tmp_path / "missing")
    monkeypatch.setattr(stats_module, "get_local_ipv4_addresses", lambda: set())
    monkeypatch.setattr(
        stats_module,
        "get_primary_local_ipv4",
        lambda default="N/A": "10.0.0.1",
    )
    collector = SystemStatsCollector(update_interval=0.0)
    # First call: probes the thermal file, fails, sets _temp_available=False.
    first = collector.update()
    assert first.temperature is None
    assert collector._temp_available is False
    # Second call: short-circuits without touching the filesystem.
    second = collector.update()
    assert second.temperature is None


def test_stats_collector_rate_limits_system_calls(monkeypatch, tmp_path: Path) -> None:
    calls = {"cpu": 0}

    def fake_cpu_percent(interval=None):  # noqa: ANN001
        calls["cpu"] += 1
        return 25.0 + calls["cpu"]

    monkeypatch.setattr(stats_module.psutil, "cpu_percent", fake_cpu_percent)
    monkeypatch.setattr(
        stats_module.psutil,
        "virtual_memory",
        lambda: SimpleNamespace(percent=50.0),
    )
    temp_file = tmp_path / "thermal"
    temp_file.write_text("50000", encoding="utf-8")
    monkeypatch.setattr(stats_module, "_THERMAL_ZONE_PATH", temp_file)
    monkeypatch.setattr(stats_module, "get_local_ipv4_addresses", lambda: {"10.0.0.1"})
    monkeypatch.setattr(stats_module, "get_primary_local_ipv4", lambda default="N/A": "10.0.0.1")
    # Padded with a fallthrough value so we don't crash with
    # ``StopIteration`` if some platform-specific path (psutil's
    # ``cpu_percent`` internals on Linux, etc.) leaks an extra
    # ``time.monotonic()`` call beyond the three production-code
    # calls this test cares about. The fallthrough is larger than
    # any preceding value, so the assertions (rate-limit second
    # call → returns cached, third call → refreshes) still hold.
    _ts = iter([100.0, 100.4, 101.2])

    def _fake_monotonic() -> float:
        try:
            return next(_ts)
        except StopIteration:
            return 200.0

    monkeypatch.setattr(stats_module.time, "monotonic", _fake_monotonic)

    collector = SystemStatsCollector(update_interval=1.0)
    first = collector.update()
    second = collector.update()
    third = collector.update()

    assert calls["cpu"] == 3  # 1 prime call in __init__ + 2 real updates
    assert second is first
    assert third is not first


def test_stats_collector_accepts_lazy_preferred_ip_provider(monkeypatch) -> None:
    """Collector constructed before startup ``wait_for_source_ip`` completes.
    Callable form re-resolves on every tick so HUD catches up when iface online."""
    _stub_psutil(monkeypatch)
    monkeypatch.setattr(stats_module, "get_local_ipv4_addresses", lambda: {"10.0.0.99"})
    monkeypatch.setattr(
        stats_module,
        "get_primary_local_ipv4",
        lambda default="N/A": "10.0.0.1",
    )
    monkeypatch.setattr(stats_module, "get_iface_for_ip", lambda _ip: "")

    # First tick: provider hasn't resolved yet (DHCP pending).
    # Second tick: iface is up, provider returns the real bind IP.
    resolutions = iter(["", "10.0.0.99"])
    provider_calls: list[str] = []

    def _provider() -> str:
        result = next(resolutions)
        provider_calls.append(result)
        return result

    collector = SystemStatsCollector(update_interval=0.0, preferred_ip=_provider)
    first = collector.update()
    second = collector.update()

    # First tick: empty pin → primary fallback.
    assert first.ip_address == "10.0.0.1"
    # Second tick: live pin from the provider.
    assert second.ip_address == "10.0.0.99"
    # Provider invoked once per tick (not captured at init).
    assert provider_calls == ["", "10.0.0.99"]


def test_stats_collector_provider_exception_falls_back_to_primary(monkeypatch) -> None:
    _stub_psutil(monkeypatch)
    monkeypatch.setattr(stats_module, "get_local_ipv4_addresses", lambda: set())
    monkeypatch.setattr(
        stats_module,
        "get_primary_local_ipv4",
        lambda default="N/A": "10.0.0.1",
    )
    monkeypatch.setattr(stats_module, "get_iface_for_ip", lambda _ip: "")

    def _boom() -> str:
        raise RuntimeError("resolver raised")

    collector = SystemStatsCollector(update_interval=0.0, preferred_ip=_boom)
    stats = collector.update()

    assert stats.ip_address == "10.0.0.1"
