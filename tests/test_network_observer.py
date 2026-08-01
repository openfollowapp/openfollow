# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for NetworkPlaneObserver: a plane follows its configured interface,
stops when that interface has no address, and never moves to another one."""

from __future__ import annotations

import pytest

from openfollow.runtime.network_observer import (
    POLL_INTERVAL_S,
    NetworkPlaneObserver,
    Plane,
)

pytestmark = pytest.mark.unit


class _Recorder:
    """Stand-in for one plane's bind surface."""

    def __init__(self, address: str = "192.168.1.5", status: str = "iface", iface: str = "eth0") -> None:
        self.address = address
        self.status = status
        self.iface = iface
        self.applied: list[str] = []
        self.suspends = 0

    def resolve(self) -> tuple[str, str, str]:
        return self.address, self.status, self.iface

    def apply(self, address: str) -> None:
        self.applied.append(address)

    def suspend(self) -> None:
        self.suspends += 1

    def go_down(self) -> None:
        self.address, self.status = "", "down"

    def come_back(self, address: str) -> None:
        self.address, self.status = address, "iface"

    def plane(self, label: str = "PSN") -> Plane:
        return Plane(label=label, resolve=self.resolve, apply=self.apply, suspend=self.suspend)


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float = POLL_INTERVAL_S) -> None:
        self.now += seconds


def _observer(*recorders: _Recorder, clock: _Clock | None = None) -> tuple[NetworkPlaneObserver, _Clock]:
    clk = clock or _Clock()
    planes = [r.plane(f"plane{i}") for i, r in enumerate(recorders)]
    return NetworkPlaneObserver(planes=planes, clock=clk), clk


class TestFollowingAnInterface:
    def test_first_poll_binds(self) -> None:
        rec = _Recorder()
        obs, _clk = _observer(rec)
        obs.poll()
        assert rec.applied == ["192.168.1.5"]

    def test_unchanged_state_does_not_rebind(self) -> None:
        """A steady station must not churn its sockets once a second."""
        rec = _Recorder()
        obs, clk = _observer(rec)
        for _ in range(4):
            obs.poll()
            clk.advance()
        assert rec.applied == ["192.168.1.5"]

    def test_new_lease_on_the_same_interface_is_followed(self) -> None:
        """Reconnecting a cable and getting a different DHCP address is normal.
        Only the interface is fixed, so the plane must rebind to the new one."""
        rec = _Recorder()
        obs, clk = _observer(rec)
        obs.poll()
        rec.come_back("192.168.1.77")
        clk.advance()
        obs.poll()
        assert rec.applied == ["192.168.1.5", "192.168.1.77"]
        assert rec.suspends == 0


class TestFailingClosed:
    def test_down_interface_suspends_instead_of_rebinding(self) -> None:
        """The load-bearing rule: no traffic on an interface the operator did
        not choose. A stopped output is diagnosable; a misrouted one is not."""
        rec = _Recorder()
        obs, clk = _observer(rec)
        obs.poll()
        rec.go_down()
        clk.advance()
        obs.poll()
        assert rec.suspends == 1
        assert rec.applied == ["192.168.1.5"]

    def test_suspend_happens_once_while_it_stays_down(self) -> None:
        rec = _Recorder()
        obs, clk = _observer(rec)
        rec.go_down()
        for _ in range(4):
            obs.poll()
            clk.advance()
        assert rec.suspends == 1

    def test_interface_returning_resumes_automatically(self) -> None:
        """No restart, no re-save - the operator plugs the cable back in."""
        rec = _Recorder()
        obs, clk = _observer(rec)
        rec.go_down()
        obs.poll()
        clk.advance()
        rec.come_back("10.0.0.9")
        obs.poll()
        assert rec.applied == ["10.0.0.9"]

    def test_alerts_name_the_plane_and_interface(self) -> None:
        rec = _Recorder(iface="eth0.10")
        obs, _clk = _observer(rec)
        rec.go_down()
        obs.poll()
        assert obs.alerts() == ["plane0: eth0.10 is down"]

    def test_alerts_clear_when_the_interface_returns(self) -> None:
        rec = _Recorder()
        obs, clk = _observer(rec)
        rec.go_down()
        obs.poll()
        clk.advance()
        rec.come_back("192.168.1.5")
        obs.poll()
        assert obs.alerts() == []


class TestIsolationAndThrottling:
    def test_one_failing_plane_does_not_stop_the_others(self) -> None:
        """A stalled OTP rebind cannot be allowed to leave PSN on a dead
        address."""
        broken = _Recorder()
        healthy = _Recorder(address="10.0.0.9", iface="eth1")

        def _boom(_address: str) -> None:
            raise OSError("bind failed")

        obs = NetworkPlaneObserver(
            planes=[
                Plane(label="broken", resolve=broken.resolve, apply=_boom, suspend=broken.suspend),
                healthy.plane("healthy"),
            ],
            clock=_Clock(),
        )
        obs.poll()
        assert healthy.applied == ["10.0.0.9"]

    def test_poll_is_throttled(self) -> None:
        """Interface enumeration costs a psutil call and housekeeping runs at
        100ms, so the observer must not resolve on every tick."""
        rec = _Recorder()
        obs, clk = _observer(rec)
        obs.poll()
        rec.come_back("10.0.0.9")
        obs.poll()  # same instant - throttled
        assert rec.applied == ["192.168.1.5"]
        clk.advance()
        obs.poll()
        assert rec.applied == ["192.168.1.5", "10.0.0.9"]

    def test_force_bypasses_the_throttle(self) -> None:
        rec = _Recorder()
        obs, _clk = _observer(rec)
        obs.poll()
        rec.come_back("10.0.0.9")
        obs.poll(force=True)
        assert rec.applied == ["192.168.1.5", "10.0.0.9"]
