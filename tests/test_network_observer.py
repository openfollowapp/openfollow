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

import dataclasses
import logging

import pytest

from openfollow.runtime.network_observer import (
    DOWN_POLLS_BEFORE_SUSPEND,
    POLL_INTERVAL_S,
    NetworkPlaneObserver,
    Plane,
    PlaneStatus,
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
        self.current_error: Exception | None = None
        self.suspend_error: Exception | None = None
        self.resolve_error: Exception | None = None
        self.resolves = 0
        self.apply_attempts = 0

    def resolve(self) -> tuple[str, str, str]:
        self.resolves += 1
        if self.resolve_error is not None:
            raise self.resolve_error
        return self.address, self.status, self.iface

    def current(self) -> str | None:
        if self.current_error is not None:
            raise self.current_error
        return self.bound

    def apply(self, address: str) -> None:
        self.apply_attempts += 1
        if self.apply_error is not None:
            raise self.apply_error
        self.applied.append(address)
        self.bound = address

    def suspend(self) -> None:
        if self.suspend_error is not None:
            raise self.suspend_error
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

    def test_an_outage_alerts_from_its_first_poll(self) -> None:
        """The output is off the show as soon as its interface has no address;
        only the suspend waits out the gap an Apply or Renew leaves."""
        rec = _Recorder()
        rec.bound = "192.168.1.5"
        obs, clk = _observer(rec)
        rec.go_down()
        _poll_n(obs, clk, 1)
        assert obs.alerts() == ["plane0: eth0 is down"]
        assert rec.suspends == 0

    def test_a_gap_that_closes_before_the_suspend_clears_its_alert(self) -> None:
        rec = _Recorder()
        rec.bound = "192.168.1.5"
        obs, clk = _observer(rec)
        rec.go_down()
        _poll_n(obs, clk, 2)
        assert obs.alerts() == ["plane0: eth0 is down"]
        rec.come_back("192.168.1.5")
        _poll_n(obs, clk, 1)
        assert obs.alerts() == []
        assert rec.suspends == 0

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
        assert obs.alerts() == ["plane0: eth0 – bind failed"]

    def test_a_failure_without_a_known_interface_reads_as_the_failure_alone(self) -> None:
        rec = _Recorder(iface="")
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

    def test_a_suspend_that_raises_reads_as_its_failure(self) -> None:
        """The row says why the output could not be stopped, also once the
        interface is back and the retry is still waiting out its backoff."""
        rec = _Recorder()
        rec.bound = "192.168.1.5"
        rec.suspend_error = OSError("cannot stop")
        obs, clk = _observer(rec)
        rec.go_down()
        _poll_n(obs, clk, DOWN_POLLS_BEFORE_SUSPEND)
        assert obs.alerts() == ["plane0: eth0 – cannot stop"]
        rec.come_back("192.168.1.5")
        _poll_n(obs, clk, 1)
        assert obs.alerts() == ["plane0: eth0 – cannot stop"]

    def test_a_rebind_that_fails_after_an_outage_reads_as_its_failure(self) -> None:
        rec = _Recorder()
        rec.bound = "192.168.1.5"
        obs, clk = _observer(rec)
        rec.go_down()
        _poll_n(obs, clk, DOWN_POLLS_BEFORE_SUSPEND)
        rec.come_back("192.168.1.5")
        rec.apply_error = OSError("bind failed")
        _poll_n(obs, clk, 2)  # the failed rebind, then a poll inside its backoff
        assert obs.alerts() == ["plane0: eth0 – bind failed"]

    def test_one_failing_plane_does_not_stop_the_others(self) -> None:
        broken = _Recorder()
        broken.apply_error = OSError("bind failed")
        healthy = _Recorder(address="10.0.0.9", iface="eth1")
        obs, _clk = _observer(broken, healthy)
        obs.poll()
        assert healthy.applied == ["10.0.0.9"]

    def test_a_failure_quoting_a_stream_credential_never_reaches_the_alert(self) -> None:
        rec = _Recorder()
        rec.apply_error = OSError("cannot reach rtsp://operator:secret@192.0.2.10/stream")
        obs, _clk = _observer(rec)
        obs.poll()
        assert obs.alerts() == ["plane0: eth0 – cannot reach rtsp://192.0.2.10/stream"]


class TestAFailureMeetsAnOutage:
    """Every poll resolves, so an outage is seen from its first poll and leads
    over a failure recorded before it; the backoff holds back only apply and
    suspend."""

    def test_an_outage_after_a_failed_rebind_reads_as_down_from_its_first_poll(self) -> None:
        rec = _Recorder()
        rec.bound = "192.168.1.5"
        obs, clk = _observer(rec)
        rec.go_down()
        _poll_n(obs, clk, DOWN_POLLS_BEFORE_SUSPEND)
        rec.come_back("192.168.1.5")
        rec.apply_error = OSError("bind failed")
        _poll_n(obs, clk, 1)
        assert obs.alerts() == ["plane0: eth0 – bind failed"]

        rec.go_down()
        _poll_n(obs, clk, 1)
        assert obs.alerts() == ["plane0: eth0 is down"]
        _poll_n(obs, clk, 2 * DOWN_POLLS_BEFORE_SUSPEND)
        assert obs.alerts() == ["plane0: eth0 is down"]

    def test_a_failed_rebind_leads_again_when_an_outage_ends_inside_its_backoff(self) -> None:
        """The plane is still unbound until the retry, so it must not fall silent."""
        rec = _Recorder()
        rec.apply_error = OSError("bind failed")
        obs, clk = _observer(rec)
        obs.poll()
        rec.go_down()
        clk.advance(0.01)
        obs.poll(force=True)
        assert obs.alerts() == ["plane0: eth0 is down"]

        rec.come_back("192.168.1.5")
        clk.advance(0.01)
        obs.poll(force=True)
        assert obs.alerts() == ["plane0: eth0 – bind failed"]
        assert rec.apply_attempts == 1

        rec.apply_error = None
        clk.advance(120.0)
        obs.poll(force=True)
        assert obs.alerts() == []

    def test_a_resolver_error_inside_a_backoff_leaves_what_the_plane_shows(self) -> None:
        """The rebind failure behind the backoff, or the outage in front of it,
        is why the plane is dead; a passing resolver error is not."""
        rec = _Recorder()
        rec.apply_error = OSError("bind failed")
        obs, clk = _observer(rec)
        obs.poll()
        rec.resolve_error = OSError("nmcli timed out")
        clk.advance(0.01)
        obs.poll(force=True)
        assert obs.alerts() == ["plane0: eth0 – bind failed"]

        rec.resolve_error = None
        rec.go_down()
        clk.advance(0.01)
        obs.poll(force=True)
        rec.resolve_error = OSError("nmcli timed out")
        clk.advance(0.01)
        obs.poll(force=True)
        assert obs.alerts() == ["plane0: eth0 is down"]

    def test_a_resolver_that_raised_while_suspended_does_not_stick(self) -> None:
        rec = _Recorder()
        rec.bound = "192.168.1.5"
        obs, clk = _observer(rec)
        rec.go_down()
        _poll_n(obs, clk, DOWN_POLLS_BEFORE_SUSPEND)
        rec.resolve_error = OSError("nmcli timed out")
        _poll_n(obs, clk, 1)
        assert obs.alerts() == ["plane0: eth0 – nmcli timed out"]
        rec.resolve_error = None
        _poll_n(obs, clk, 1)
        assert obs.alerts() == ["plane0: eth0 is down"]

    def test_an_outage_inside_a_backoff_is_reported_at_once_and_stopped_only_after_it(self) -> None:
        rec = _Recorder()
        rec.apply_error = OSError("bind failed")
        obs, clk = _observer(rec)
        obs.poll()
        rec.go_down()
        clk.advance(0.01)
        obs.poll(force=True)
        assert obs.alerts() == ["plane0: eth0 is down"]

        for _ in range(DOWN_POLLS_BEFORE_SUSPEND + 2):
            clk.advance(0.01)
            obs.poll(force=True)
        assert rec.suspends == 0
        assert rec.apply_attempts == 1

        clk.advance(120.0)
        obs.poll(force=True)
        assert rec.suspends == 1

    def test_a_suspend_that_raises_leads_for_the_rest_of_the_outage(self) -> None:
        rec = _Recorder()
        rec.bound = "192.168.1.5"
        rec.suspend_error = OSError("cannot stop")
        obs, clk = _observer(rec)
        rec.go_down()
        _poll_n(obs, clk, DOWN_POLLS_BEFORE_SUSPEND + 3)
        assert obs.alerts() == ["plane0: eth0 – cannot stop"]

    def test_a_resolver_that_keeps_raising_inside_a_backoff_logs_once(self, caplog: pytest.LogCaptureFixture) -> None:
        rec = _Recorder()
        rec.resolve_error = OSError("nmcli timed out")
        obs, clk = _observer(rec)
        with caplog.at_level(logging.ERROR, logger="openfollow.runtime.network_observer"):
            for _ in range(3):
                obs.poll(force=True)
                clk.advance(0.1)
        logged = [r for r in caplog.records if r.name == "openfollow.runtime.network_observer"]
        assert len(logged) == 1
        assert obs.alerts() == ["plane0: nmcli timed out"]

    def test_a_resolver_that_keeps_raising_inside_a_backoff_does_not_push_the_retry_back(self) -> None:
        """Measured against a plane whose resolver raised once: both rebind on the same poll."""
        once, again = _Recorder(), _Recorder(iface="eth1")
        once.resolve_error = OSError("nmcli timed out")
        again.resolve_error = OSError("nmcli timed out")
        obs, clk = _observer(once, again)
        obs.poll()
        once.resolve_error = None
        clk.advance(0.1)
        obs.poll(force=True)
        again.resolve_error = None
        for _ in range(1000):
            clk.advance(0.1)
            obs.poll(force=True)
            if once.applied:
                break
        assert once.applied == ["192.168.1.5"]
        assert again.applied == ["192.168.1.5"]


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
        """Rendering the HUD must not depend on nmcli still answering.

        ``alerts()`` reads the interface name cached by the last poll, so a
        backend that has since died cannot reach the render path at all - and
        the operator still gets the interface named rather than a placeholder.
        """
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
        assert obs.alerts() == ["plane0: eth0 is down"]

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


class TestAlertsStayOffTheRenderPath:
    """``alerts()`` feeds the HUD, so it runs once per rendered frame.

    Resolving there walked every interface via psutil for as long as an outage
    lasted - at 60-120 Hz, on the frame loop, exactly while the station is
    already in trouble.
    """

    def test_alerts_never_resolve(self) -> None:
        rec = _Recorder()
        rec.bound = "192.168.1.5"
        obs, clk = _observer(rec)
        rec.go_down()
        _poll_n(obs, clk, DOWN_POLLS_BEFORE_SUSPEND)
        assert obs.alerts(), "expected a suspended plane to raise an alert"

        before = rec.resolves
        for _ in range(120):
            obs.alerts()
        assert rec.resolves == before

    def test_the_alert_still_names_the_interface(self) -> None:
        """Caching must not cost the operator the one detail that makes the
        line actionable."""
        rec = _Recorder(iface="eth0.10")
        rec.bound = "192.168.1.5"
        obs, clk = _observer(rec)
        rec.go_down()
        _poll_n(obs, clk, DOWN_POLLS_BEFORE_SUSPEND)
        assert any("eth0.10" in line for line in obs.alerts())


class TestAChangingSetOfPlanes:
    """OSC destinations come and go at runtime, so the observer can take its
    planes from a provider. A plane is tracked by its key, not its label."""

    @staticmethod
    def _provided(*planes: Plane) -> tuple[NetworkPlaneObserver, _Clock, list[Plane], list[int]]:
        current = list(planes)
        calls: list[int] = []

        def _provide() -> list[Plane]:
            calls.append(1)
            return list(current)

        clk = _Clock()
        return NetworkPlaneObserver(planes=_provide, clock=clk), clk, current, calls

    def test_the_provider_is_asked_once_per_poll_and_never_by_alerts(self) -> None:
        obs, clk, _current, calls = self._provided(_Recorder().plane())
        _poll_n(obs, clk, 3)
        for _ in range(50):
            obs.alerts()
        assert len(calls) == 3

    def test_a_plane_added_later_is_followed(self) -> None:
        rec = _Recorder()
        obs, clk, current, _calls = self._provided()
        obs.poll()
        current.append(rec.plane())
        clk.advance()
        obs.poll()
        assert rec.applied == ["192.168.1.5"]

    def test_a_plane_that_leaves_takes_its_alert_and_state_with_it(self) -> None:
        rec = _Recorder()
        rec.bound = "192.168.1.5"
        obs, clk, current, _calls = self._provided(rec.plane("OSC output"))
        rec.go_down()
        _poll_n(obs, clk, DOWN_POLLS_BEFORE_SUSPEND - 1)
        assert obs.alerts() == ["OSC output: eth0 is down"]

        current.clear()
        obs.poll()
        assert obs.alerts() == []

        # Back in the set, it counts its outage from zero; the stale count
        # would stop it on this very poll.
        current.append(rec.plane("OSC output"))
        clk.advance()
        obs.poll()
        assert obs.alerts() == ["OSC output: eth0 is down"]
        assert rec.suspends == 0

    def test_planes_sharing_a_label_are_told_apart_by_key(self) -> None:
        eth1, eth2 = _Recorder(iface="eth1"), _Recorder(address="198.51.100.10", iface="eth2")
        eth1.bound = eth1.address
        obs, clk, _current, _calls = self._provided(
            dataclasses.replace(eth1.plane("OSC output"), key="osc_out:eth1"),
            dataclasses.replace(eth2.plane("OSC output"), key="osc_out:eth2"),
        )
        eth1.go_down()
        _poll_n(obs, clk, DOWN_POLLS_BEFORE_SUSPEND)
        assert obs.alerts() == ["OSC output: eth1 is down"]
        assert eth2.applied == ["198.51.100.10"]

    def test_a_relabelled_plane_keeps_its_down_count(self) -> None:
        rec = _Recorder()
        rec.bound = "192.168.1.5"
        obs, clk, current, _calls = self._provided(dataclasses.replace(rec.plane("OSC to FOH"), key="dest-1"))
        rec.go_down()
        _poll_n(obs, clk, DOWN_POLLS_BEFORE_SUSPEND - 1)
        current[0] = dataclasses.replace(rec.plane("OSC to FOH console"), key="dest-1")
        _poll_n(obs, clk, 1)
        assert rec.suspends == 1

    def test_a_provider_that_raises_keeps_the_last_set(self) -> None:
        rec = _Recorder()
        rec.bound = "192.168.1.5"
        state = {"fail": False}

        def _provide() -> list[Plane]:
            if state["fail"]:
                raise RuntimeError("config mid-reload")
            return [rec.plane()]

        clk = _Clock()
        obs = NetworkPlaneObserver(planes=_provide, clock=clk)
        rec.go_down()
        _poll_n(obs, clk, DOWN_POLLS_BEFORE_SUSPEND)
        state["fail"] = True
        assert obs.poll(force=True) is True
        assert obs.alerts() == ["PSN: eth0 is down"]

        # Still followed from the last set, so it resumes when the interface does.
        rec.come_back("192.168.1.6")
        obs.poll(force=True)
        assert rec.applied[-1] == "192.168.1.6"
        assert obs.alerts() == []


class TestSnapshot:
    """What the diagnostics bundle reads: every plane as the last poll left it."""

    def test_a_bound_plane_reads_ok_with_what_it_holds(self) -> None:
        rec = _Recorder()
        obs, _clk = _observer(rec)
        obs.poll()
        assert obs.snapshot() == (PlaneStatus("plane0", "eth0", "192.168.1.5", "192.168.1.5", "ok"),)

    def test_the_bundle_and_the_hud_lead_with_the_same_condition(self) -> None:
        """A failed rebind leads; an outage that begins after it leads instead;
        the failure leads again once the outage ends inside its backoff."""
        rec = _Recorder()
        rec.apply_error = OSError("bind failed")
        obs, clk = _observer(rec)
        obs.poll()
        assert obs.snapshot()[0].state == "failing"
        assert obs.alerts() == ["plane0: eth0 – bind failed"]

        rec.go_down()
        clk.advance(0.01)
        obs.poll(force=True)
        assert obs.snapshot()[0].state == "down"
        assert obs.alerts() == ["plane0: eth0 is down"]

        rec.come_back("192.168.1.5")
        clk.advance(0.01)
        obs.poll(force=True)
        assert obs.snapshot()[0].state == "failing"
        assert obs.alerts() == ["plane0: eth0 – bind failed"]

    def test_a_plane_not_followed_says_so_and_is_not_asked_anything(self) -> None:
        rec = _Recorder()
        rec.is_enabled = False
        obs, _clk = _observer(rec)
        obs.poll()
        assert obs.snapshot() == (PlaneStatus("plane0", "", "", None, "not followed", resolved=False),)
        assert rec.resolves == 0

    def test_a_plane_switched_back_on_is_followed_again(self) -> None:
        rec = _Recorder()
        rec.is_enabled = False
        obs, clk = _observer(rec)
        obs.poll()
        clk.advance()
        rec.is_enabled = True
        obs.poll()
        assert obs.snapshot()[0].state == "ok"

    def test_an_addressless_interface_counts_toward_the_stop(self) -> None:
        rec = _Recorder()
        obs, clk = _observer(rec)
        obs.poll()
        clk.advance()
        rec.go_down()
        _poll_n(obs, clk, 2)
        (status,) = obs.snapshot()
        assert (status.state, status.address) == ("down", "")
        assert status.detail == f"no address for 2 of {DOWN_POLLS_BEFORE_SUSPEND} polls"
        # Not stopped yet: it still holds the old binding.
        assert status.bound == "192.168.1.5"

    def test_a_suspended_plane_reads_stopped_and_bound_to_nothing(self) -> None:
        rec = _Recorder()
        obs, clk = _observer(rec)
        rec.go_down()
        _poll_n(obs, clk, DOWN_POLLS_BEFORE_SUSPEND)
        assert obs.snapshot() == (PlaneStatus("plane0", "eth0", "", None, "stopped", "eth0 has no address"),)

    def test_a_plane_whose_rebind_raises_reads_failing_with_the_reason(self) -> None:
        rec = _Recorder()
        rec.apply_error = OSError("address in use")
        obs, _clk = _observer(rec)
        obs.poll()
        (status,) = obs.snapshot()
        assert (status.state, status.detail, status.bound) == ("failing", "address in use", None)

    def test_a_rebind_that_fails_after_an_outage_reads_failing_not_stopped(self) -> None:
        """The interface is back; what is wrong now is the rebind, not the outage."""
        rec = _Recorder()
        obs, clk = _observer(rec)
        rec.go_down()
        _poll_n(obs, clk, DOWN_POLLS_BEFORE_SUSPEND)
        rec.apply_error = OSError("address in use")
        rec.come_back("192.168.1.6")
        clk.advance()
        obs.poll()
        (status,) = obs.snapshot()
        assert (status.state, status.detail) == ("failing", "address in use")

    def test_a_plane_whose_first_resolve_raises_reads_as_never_resolved(self) -> None:
        """Its blank interface is not auto-detect; once a resolve returns, it is."""
        rec = _Recorder()
        rec.resolve_error = OSError("nmcli timed out")
        obs, clk = _observer(rec)
        obs.poll()
        (status,) = obs.snapshot()
        assert (status.state, status.resolved) == ("failing", False)

        rec.resolve_error = None
        rec.apply_error = OSError("bind failed")
        clk.advance(120.0)
        obs.poll(force=True)
        (status,) = obs.snapshot()
        assert (status.state, status.detail, status.resolved) == ("failing", "bind failed", True)

    def test_a_plane_whose_enabled_check_raises_reads_failing_not_unfollowed(self) -> None:
        state = {"raise": False}
        rec = _Recorder()
        rec.is_enabled = False

        def _enabled() -> bool:
            if state["raise"]:
                raise RuntimeError("config unreadable")
            return rec.is_enabled

        clk = _Clock()
        plane = Plane("plane0", rec.resolve, rec.current, rec.apply, rec.suspend, enabled=_enabled)
        obs = NetworkPlaneObserver(planes=[plane], clock=clk)
        obs.poll()
        assert obs.snapshot()[0].state == "not followed"
        clk.advance()
        state["raise"] = True
        obs.poll()
        (status,) = obs.snapshot()
        assert (status.state, status.detail) == ("failing", "config unreadable")
        # Never resolved, so its blank interface is not auto-detect.
        assert status.resolved is False

    def test_a_current_that_raises_reads_as_nothing_bound(self) -> None:
        rec = _Recorder()
        rec.bound = "192.168.1.5"
        obs, clk = _observer(rec)
        obs.poll()
        clk.advance()
        rec.current_error = RuntimeError("backend gone")
        obs.poll()
        (status,) = obs.snapshot()
        assert status.bound is None
        assert status.state == "failing"

    def test_a_plane_that_leaves_the_set_leaves_the_snapshot(self) -> None:
        recs = {"a": _Recorder(), "b": _Recorder()}
        live = ["a", "b"]
        clk = _Clock()
        obs = NetworkPlaneObserver(planes=lambda: [recs[k].plane(k) for k in live], clock=clk)
        obs.poll()
        clk.advance()
        live.remove("a")
        obs.poll()
        assert [s.label for s in obs.snapshot()] == ["b"]

    def test_the_snapshot_is_replaced_whole_never_changed_in_place(self) -> None:
        """A reader on another thread holds the old tuple; it must not change under it."""
        rec = _Recorder()
        obs, clk = _observer(rec)
        obs.poll()
        before = obs.snapshot()
        clk.advance()
        rec.go_down()
        obs.poll()
        assert before == (PlaneStatus("plane0", "eth0", "192.168.1.5", "192.168.1.5", "ok"),)
        assert obs.snapshot() is not before

    def test_a_throttled_poll_keeps_the_last_snapshot(self) -> None:
        rec = _Recorder()
        obs, _clk = _observer(rec)
        obs.poll()
        before = obs.snapshot()
        obs.poll()  # inside the interval
        assert obs.snapshot() is before

    def test_nothing_polled_yet_is_an_empty_snapshot(self) -> None:
        obs, _clk = _observer(_Recorder())
        assert obs.snapshot() == ()


class TestNamesTheAdapter:
    """Alerts, the bundle and the log name an interface as the operator labelled it,
    and tell an unplugged adapter from one without an address."""

    @staticmethod
    def _named(rec: _Recorder, *, present: set[str]) -> tuple[NetworkPlaneObserver, _Clock]:
        clk = _Clock()
        labels = {"enx9c69d3ac16ab": "Lighting"}
        obs = NetworkPlaneObserver(
            planes=[rec.plane("PSN")],
            clock=clk,
            describe_iface=lambda iface: f"{labels[iface]} ({iface})" if iface in labels else iface,
            iface_present=lambda iface: iface in present,
        )
        return obs, clk

    def test_a_labelled_interface_without_an_address_is_down(self) -> None:
        rec = _Recorder(iface="enx9c69d3ac16ab")
        obs, clk = self._named(rec, present={"enx9c69d3ac16ab"})
        rec.go_down()
        _poll_n(obs, clk, 1)
        assert obs.alerts() == ["PSN: Lighting (enx9c69d3ac16ab) is down"]
        assert obs.snapshot()[0].detail == f"no address for 1 of {DOWN_POLLS_BEFORE_SUSPEND} polls"

    def test_an_unplugged_adapter_is_not_connected(self, caplog: pytest.LogCaptureFixture) -> None:
        rec = _Recorder(iface="enx9c69d3ac16ab")
        obs, clk = self._named(rec, present=set())
        rec.go_down()
        _poll_n(obs, clk, 1)
        assert obs.alerts() == ["PSN: Lighting (enx9c69d3ac16ab) is not connected"]
        assert obs.snapshot()[0].detail == f"not connected for 1 of {DOWN_POLLS_BEFORE_SUSPEND} polls"
        with caplog.at_level(logging.ERROR, logger="openfollow.runtime.network_observer"):
            _poll_n(obs, clk, DOWN_POLLS_BEFORE_SUSPEND)
        assert "configured interface Lighting (enx9c69d3ac16ab) is not connected; output stopped" in caplog.text
        assert obs.snapshot()[0].detail == "Lighting (enx9c69d3ac16ab) is not connected"

    def test_a_stopped_plane_on_a_present_interface_has_no_address(self, caplog: pytest.LogCaptureFixture) -> None:
        rec = _Recorder(iface="eth1")
        obs, clk = self._named(rec, present={"eth1"})
        rec.go_down()
        with caplog.at_level(logging.ERROR, logger="openfollow.runtime.network_observer"):
            _poll_n(obs, clk, DOWN_POLLS_BEFORE_SUSPEND)
        assert "configured interface eth1 has no address; output stopped" in caplog.text
        assert obs.snapshot()[0].detail == "eth1 has no address"

    def test_a_returning_adapter_is_present_again(self, caplog: pytest.LogCaptureFixture) -> None:
        rec = _Recorder(iface="enx9c69d3ac16ab")
        present: set[str] = set()
        obs, clk = self._named(rec, present=present)
        rec.go_down()
        _poll_n(obs, clk, 2)
        present.add("enx9c69d3ac16ab")
        rec.come_back("203.0.113.21")
        with caplog.at_level(logging.INFO, logger="openfollow.runtime.network_observer"):
            _poll_n(obs, clk, 1)
        assert obs.alerts() == []
        assert "interface Lighting (enx9c69d3ac16ab) is back at 203.0.113.21" in caplog.text
        # Gone again without an address: down, not "not connected" from the last outage.
        rec.go_down()
        _poll_n(obs, clk, 1)
        assert obs.alerts() == ["PSN: Lighting (enx9c69d3ac16ab) is down"]

    def test_a_failure_names_the_interface_by_label(self) -> None:
        rec = _Recorder(iface="enx9c69d3ac16ab")
        rec.apply_error = OSError("bind refused")
        obs, clk = self._named(rec, present={"enx9c69d3ac16ab"})
        _poll_n(obs, clk, 1)
        assert obs.alerts() == ["PSN: Lighting (enx9c69d3ac16ab) – bind refused"]

    def test_a_presence_check_that_fails_still_stops_the_plane(self) -> None:
        rec = _Recorder(iface="eth1")
        clk = _Clock()

        def broken(_iface: str) -> bool:
            raise OSError("enumeration failed mid-hotplug")

        obs = NetworkPlaneObserver(planes=[rec.plane("PSN")], clock=clk, iface_present=broken)
        rec.go_down()
        _poll_n(obs, clk, DOWN_POLLS_BEFORE_SUSPEND)
        assert obs.alerts() == ["PSN: eth1 is down"]
        assert rec.suspends == 1
        assert obs.snapshot()[0].state == "stopped"

    def test_naming_that_fails_falls_back_to_the_bare_name(self) -> None:
        rec = _Recorder(iface="eth1")
        clk = _Clock()

        def broken(_iface: str) -> str:
            raise KeyError("labels")

        obs = NetworkPlaneObserver(planes=[rec.plane("PSN")], clock=clk, describe_iface=broken)
        rec.go_down()
        _poll_n(obs, clk, 1)
        assert obs.alerts() == ["PSN: eth1 is down"]
