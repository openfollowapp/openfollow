# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for NetworkPlaneObserver.

A plane follows its configured interface, stops when that interface has no
address, and never moves to another one. Three properties get their own
sections because each was a real defect: decisions compare against what is
actually bound, suspension is debounced, and a down-to-up transition rebuilds
even when the address is unchanged.
"""

from __future__ import annotations

import pytest

from openfollow.runtime.network_observer import (
    DOWN_POLLS_BEFORE_SUSPEND,
    POLL_INTERVAL_S,
    NetworkPlaneObserver,
    Plane,
)

pytestmark = pytest.mark.unit


class _Recorder:
    """Stand-in for one plane's bind surface, tracking what is 'bound'."""

    def __init__(self, address: str = "192.168.1.5", status: str = "iface", iface: str = "eth0") -> None:
        self.address = address
        self.status = status
        self.iface = iface
        self.bound: str | None = None
        self.applied: list[str] = []
        self.suspends = 0
        self.is_enabled = True
        self.apply_error: Exception | None = None

    def resolve(self) -> tuple[str, str, str]:
        return self.address, self.status, self.iface

    def current(self) -> str | None:
        return self.bound

    def apply(self, address: str) -> None:
        if self.apply_error is not None:
            raise self.apply_error
        self.applied.append(address)
        self.bound = address

    def suspend(self) -> None:
        self.suspends += 1
        self.bound = None

    def enabled(self) -> bool:
        return self.is_enabled

    def go_down(self) -> None:
        self.address, self.status = "", "down"

    def come_back(self, address: str) -> None:
        self.address, self.status = address, "iface"

    def plane(self, label: str = "PSN") -> Plane:
        return Plane(
            label=label,
            resolve=self.resolve,
            current=self.current,
            apply=self.apply,
            suspend=self.suspend,
            enabled=self.enabled,
        )


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float = POLL_INTERVAL_S) -> None:
        self.now += seconds


def _observer(*recorders: _Recorder) -> tuple[NetworkPlaneObserver, _Clock]:
    clk = _Clock()
    planes = [r.plane(f"plane{i}") for i, r in enumerate(recorders)]
    return NetworkPlaneObserver(planes=planes, clock=clk), clk


def _poll_n(obs: NetworkPlaneObserver, clk: _Clock, n: int) -> None:
    for _ in range(n):
        obs.poll()
        clk.advance()


class TestComparesAgainstWhatIsBound:
    """Keying idempotence on the previous resolution instead of the live
    binding meant the first poll tore down and rebuilt a plane the runtime had
    already bound correctly - which on PSN kills the retry thread that recovers
    a boot where the multicast route came up late."""

    def test_a_correctly_bound_plane_is_left_alone(self) -> None:
        rec = _Recorder()
        rec.bound = "192.168.1.5"  # services bound this at startup
        obs, _clk = _observer(rec)
        obs.poll()
        assert rec.applied == []

    def test_an_unbound_plane_is_bound(self) -> None:
        rec = _Recorder()
        obs, _clk = _observer(rec)
        obs.poll()
        assert rec.applied == ["192.168.1.5"]

    def test_an_external_rebind_of_a_suspended_plane_is_noticed(self) -> None:
        """Saving an unrelated setting restarts the output on a wildcard bind.
        Keyed on the resolution, the observer would see an identical tuple and
        never re-suspend, leaving it misrouted while the HUD says 'is down'."""
        rec = _Recorder()
        obs, clk = _observer(rec)
        rec.go_down()
        _poll_n(obs, clk, DOWN_POLLS_BEFORE_SUSPEND)
        assert rec.suspends == 1

        rec.bound = ""  # something else restarted it behind our back
        _poll_n(obs, clk, 1)
        assert rec.suspends == 2

    def test_a_steady_station_does_no_work(self) -> None:
        rec = _Recorder()
        rec.bound = "192.168.1.5"
        obs, clk = _observer(rec)
        _poll_n(obs, clk, 5)
        assert rec.applied == []
        assert rec.suspends == 0


class TestDebouncedSuspension:
    """Apply and Renew DHCP lease both run nmcli con down then con up, so the
    interface is legitimately addressless for a second or more. Suspending on
    the first sample means the Network page's own buttons drop stage output."""

    def test_a_brief_outage_does_not_suspend(self) -> None:
        rec = _Recorder()
        rec.bound = "192.168.1.5"
        obs, clk = _observer(rec)
        rec.go_down()
        _poll_n(obs, clk, DOWN_POLLS_BEFORE_SUSPEND - 1)
        assert rec.suspends == 0

    def test_a_sustained_outage_suspends(self) -> None:
        rec = _Recorder()
        rec.bound = "192.168.1.5"
        obs, clk = _observer(rec)
        rec.go_down()
        _poll_n(obs, clk, DOWN_POLLS_BEFORE_SUSPEND)
        assert rec.suspends == 1

    def test_recovery_within_the_window_never_stops_output(self) -> None:
        """The renew-lease case end to end: gone for a few polls, back before
        the debounce expires, output never interrupted."""
        rec = _Recorder()
        rec.bound = "192.168.1.5"
        obs, clk = _observer(rec)
        rec.go_down()
        _poll_n(obs, clk, DOWN_POLLS_BEFORE_SUSPEND - 1)
        rec.come_back("192.168.1.5")
        _poll_n(obs, clk, 1)
        assert rec.suspends == 0

    def test_suspend_fires_once_while_it_stays_down(self) -> None:
        rec = _Recorder()
        rec.bound = "192.168.1.5"
        obs, clk = _observer(rec)
        rec.go_down()
        _poll_n(obs, clk, DOWN_POLLS_BEFORE_SUSPEND + 5)
        assert rec.suspends == 1


class TestRebuildAfterAnOutage:
    """Removing an address drops the interface's multicast memberships and
    routes. Getting the same DHCP lease back does not restore them, and
    comparing address strings cannot see that."""

    def test_same_lease_after_an_outage_still_rebuilds(self) -> None:
        rec = _Recorder()
        rec.bound = "192.168.1.5"
        obs, clk = _observer(rec)
        rec.go_down()
        _poll_n(obs, clk, 2)
        rec.come_back("192.168.1.5")  # identical address
        _poll_n(obs, clk, 1)
        assert rec.applied == ["192.168.1.5"]

    def test_new_lease_after_an_outage_rebuilds(self) -> None:
        rec = _Recorder()
        rec.bound = "192.168.1.5"
        obs, clk = _observer(rec)
        rec.go_down()
        _poll_n(obs, clk, 2)
        rec.come_back("192.168.1.77")
        _poll_n(obs, clk, 1)
        assert rec.applied == ["192.168.1.77"]

    def test_resuming_from_suspended_rebuilds_and_clears_the_alert(self) -> None:
        rec = _Recorder()
        rec.bound = "192.168.1.5"
        obs, clk = _observer(rec)
        rec.go_down()
        _poll_n(obs, clk, DOWN_POLLS_BEFORE_SUSPEND)
        rec.come_back("192.168.1.5")
        _poll_n(obs, clk, 1)
        assert rec.applied == ["192.168.1.5"]
        assert obs.alerts() == []


class TestNoAddressAtAll:
    def test_status_none_is_treated_as_down(self) -> None:
        """Nothing configured and nothing detected means the station has no
        address. Binding "" would hand the plane INADDR_ANY, i.e. whatever
        interface the kernel picks."""
        rec = _Recorder(address="", status="none", iface="")
        obs, clk = _observer(rec)
        _poll_n(obs, clk, DOWN_POLLS_BEFORE_SUSPEND)
        assert rec.applied == []
        assert rec.suspends == 1


class TestDisabledPlanes:
    def test_a_switched_off_output_is_never_touched_or_alerted(self) -> None:
        """A disabled output is not broken, it is off. Alerting on it puts a
        second fault on the HUD for a protocol nobody enabled."""
        rec = _Recorder()
        rec.is_enabled = False
        rec.go_down()
        obs, clk = _observer(rec)
        _poll_n(obs, clk, DOWN_POLLS_BEFORE_SUSPEND + 2)
        assert rec.suspends == 0
        assert rec.applied == []
        assert obs.alerts() == []

    def test_disabling_clears_a_standing_alert(self) -> None:
        rec = _Recorder()
        obs, clk = _observer(rec)
        rec.go_down()
        _poll_n(obs, clk, DOWN_POLLS_BEFORE_SUSPEND)
        assert obs.alerts()
        rec.is_enabled = False
        _poll_n(obs, clk, 1)
        assert obs.alerts() == []


class TestFailureHandling:
    def test_a_failing_plane_backs_off_instead_of_retrying_every_poll(self) -> None:
        """Each PSN retry is four stop/start cycles with thread joins on the
        GTK thread; once a second for the length of a show is not acceptable."""
        rec = _Recorder()
        attempts = [0]
        original = rec.apply

        def _counting(address: str) -> None:
            attempts[0] += 1
            original(address)

        rec.apply = _counting  # type: ignore[method-assign]
        rec.apply_error = OSError("bind failed")
        obs, clk = _observer(rec)
        _poll_n(obs, clk, 6)
        assert attempts[0] < 6, "retried on every poll instead of backing off"

    def test_a_failing_plane_raises_an_alert(self) -> None:
        """It is exactly as dead as a suspended one, and used to surface
        nowhere at all."""
        rec = _Recorder()
        rec.apply_error = OSError("bind failed")
        obs, _clk = _observer(rec)
        obs.poll()
        assert obs.alerts() == ["plane0: bind failed"]

    def test_a_recovered_plane_clears_its_alert(self) -> None:
        rec = _Recorder()
        rec.apply_error = OSError("bind failed")
        obs, clk = _observer(rec)
        obs.poll()
        rec.apply_error = None
        clk.advance(120.0)  # past the backoff window
        obs.poll()
        assert obs.alerts() == []
        assert rec.applied == ["192.168.1.5"]

    def test_one_failing_plane_does_not_stop_the_others(self) -> None:
        broken = _Recorder()
        broken.apply_error = OSError("bind failed")
        healthy = _Recorder(address="10.0.0.9", iface="eth1")
        obs, _clk = _observer(broken, healthy)
        obs.poll()
        assert healthy.applied == ["10.0.0.9"]


class TestAlertsAndThrottling:
    def test_alerts_name_the_plane_and_interface(self) -> None:
        rec = _Recorder(iface="eth0.10")
        obs, clk = _observer(rec)
        rec.go_down()
        _poll_n(obs, clk, DOWN_POLLS_BEFORE_SUSPEND)
        assert obs.alerts() == ["plane0: eth0.10 is down"]

    def test_alerts_follow_plane_order(self) -> None:
        """Sorted output would reshuffle the rows between frames."""
        first = _Recorder(iface="zzz0")
        second = _Recorder(iface="aaa0")
        obs, clk = _observer(first, second)
        first.go_down()
        second.go_down()
        _poll_n(obs, clk, DOWN_POLLS_BEFORE_SUSPEND)
        assert obs.alerts() == ["plane0: zzz0 is down", "plane1: aaa0 is down"]

    def test_alert_falls_back_when_the_interface_cannot_be_named(self) -> None:
        rec = _Recorder(iface="")
        obs, clk = _observer(rec)
        rec.go_down()
        _poll_n(obs, clk, DOWN_POLLS_BEFORE_SUSPEND)
        assert obs.alerts() == ["plane0: interface is down"]

    def test_alert_survives_a_resolver_that_raises(self) -> None:
        """Rendering the HUD must not depend on nmcli still answering."""
        rec = _Recorder()
        failing = {"now": False}

        def _resolve() -> tuple[str, str, str]:
            if failing["now"]:
                raise OSError("nmcli gone")
            return rec.resolve()

        plane = Plane(
            label="plane0",
            resolve=_resolve,
            current=rec.current,
            apply=rec.apply,
            suspend=rec.suspend,
        )
        clk = _Clock()
        obs = NetworkPlaneObserver(planes=[plane], clock=clk)
        rec.go_down()
        _poll_n(obs, clk, DOWN_POLLS_BEFORE_SUSPEND)
        failing["now"] = True
        assert obs.alerts() == ["plane0: interface is down"]

    def test_a_plane_never_polled_contributes_no_alert(self) -> None:
        """A plane added but not yet reached - a disabled one, or the first
        tick - must not render a phantom row."""
        rec = _Recorder()
        obs, _clk = _observer(rec)
        assert obs.alerts() == []

    def test_poll_is_throttled(self) -> None:
        rec = _Recorder()
        obs, clk = _observer(rec)
        assert obs.poll() is True
        assert obs.poll() is False
        clk.advance()
        assert obs.poll() is True

    def test_force_bypasses_the_throttle(self) -> None:
        rec = _Recorder()
        obs, _clk = _observer(rec)
        obs.poll()
        assert obs.poll(force=True) is True
