# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The video input pin: a pinned network input dials its camera through the
pinned interface or not at all, and says which of the two it observed."""

from __future__ import annotations

import errno
import socket
import threading
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest

from openfollow import net_egress
from openfollow.net_utils import InterfaceUnavailable
from openfollow.video.failure import VideoFailure
from openfollow.video.inputs import _pin
from openfollow.video.inputs._pin import PinRefusal, check_video_pin, config_pin, pinned_address

pytestmark = pytest.mark.unit

_TABLE = {"eth0": "192.0.2.10", "eth1": "198.51.100.10"}


class _Probe:
    """A UDP socket that records what it is asked; it has no way to send."""

    def __init__(self, local: str, connect_error: int = 0) -> None:
        self.local = local
        self.connect_error = connect_error
        self.connected: tuple[str, int] | None = None
        self.closed = False

    def connect(self, address: tuple[str, int]) -> None:
        if self.connect_error:
            raise OSError(self.connect_error, "Network is unreachable")
        self.connected = address

    def getsockname(self) -> tuple[str, int]:
        return (self.local, 40000)

    def close(self) -> None:
        self.closed = True


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture()
def net(monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    """Interfaces from ``_TABLE``, a probe socket per check, and fresh DNS state."""
    state = SimpleNamespace(
        probes=[],
        local="192.0.2.10",
        connect_error=0,
        pinned=[],
        pin_error=None,
        lookups=[],
        answers={"camera.example": "192.0.2.20"},
        clock=_Clock(),
    )

    def _socket(family: int, kind: int) -> _Probe:
        assert (family, kind) == (socket.AF_INET, socket.SOCK_DGRAM)
        probe = _Probe(state.local, state.connect_error)
        state.probes.append(probe)
        return probe

    def _pin_socket(sock: _Probe, egress: net_egress.Egress) -> None:
        state.pinned.append(egress)
        if state.pin_error is not None:
            raise state.pin_error

    def _getaddrinfo(host: str, port: Any, family: int, kind: int) -> list[tuple[Any, ...]]:
        state.lookups.append(host)
        if host not in state.answers:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        return [(family, kind, 17, "", (state.answers[host], 0))]

    addrs = {name: [SimpleNamespace(family=socket.AF_INET, address=address)] for name, address in _TABLE.items()}
    monkeypatch.setattr(net_egress, "get_iface_ipv4", lambda iface: _TABLE.get(iface, ""))
    monkeypatch.setattr(_pin, "get_iface_ipv4", lambda iface: _TABLE.get(iface, ""))
    monkeypatch.setattr(_pin.psutil, "net_if_addrs", lambda: addrs)
    monkeypatch.setattr(_pin.socket, "socket", _socket)
    monkeypatch.setattr(_pin.socket, "getaddrinfo", _getaddrinfo)
    monkeypatch.setattr(_pin, "pin_socket_egress", _pin_socket)
    monkeypatch.setattr(_pin, "_resolver", _pin._Resolver(clock=state.clock))
    # A lookup thread that dies prints a traceback for every unknown name.
    crashes: list[threading.ExceptHookArgs] = []
    monkeypatch.setattr(threading, "excepthook", crashes.append)
    yield state
    assert crashes == []


# --------------------------------------------------------------------------- #
# Nothing to check
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("host", ["", "192.0.2.20", "camera.example"])
def test_an_unpinned_input_is_never_refused(net: SimpleNamespace, host: str) -> None:
    assert check_video_pin("", host, 554) is None
    assert net.probes == []
    assert net.lookups == []


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_a_camera_on_this_box_is_never_refused(net: SimpleNamespace, host: str) -> None:
    """Loopback traffic never leaves the box, so no interface can carry it."""
    assert check_video_pin("eth9", host, 554) is None
    assert net.probes == []


def test_a_name_that_resolves_to_this_box_is_never_refused(net: SimpleNamespace) -> None:
    net.answers["camera.example"] = "127.0.0.1"
    assert check_video_pin("eth1", "camera.example", 554) is None
    assert net.probes == []


# --------------------------------------------------------------------------- #
# The pinned interface itself
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("host", ["", "192.0.2.20"])
def test_a_pin_with_no_address_refuses_as_interface_down(net: SimpleNamespace, host: str) -> None:
    assert check_video_pin("eth9", host, 554) == PinRefusal(VideoFailure.INTERFACE_DOWN, "eth9 has no address")
    assert net.probes == []


def test_a_listener_on_a_live_pin_is_not_refused(net: SimpleNamespace) -> None:
    assert check_video_pin("eth1") is None
    assert net.probes == []


# --------------------------------------------------------------------------- #
# Forced device (SRT on Linux): reachable through the pin, or not
# --------------------------------------------------------------------------- #


def test_forced_device_accepts_a_camera_the_pin_reaches(net: SimpleNamespace) -> None:
    assert check_video_pin("eth1", "198.51.100.20", 5000, forced_device=True) is None
    assert net.pinned == [net_egress.Egress("eth1", "198.51.100.10")]
    assert net.probes[0].connected == ("198.51.100.20", 5000)
    assert net.probes[0].closed


def test_forced_device_refuses_a_camera_the_pin_cannot_reach(net: SimpleNamespace) -> None:
    net.connect_error = errno.ENETUNREACH
    refusal = check_video_pin("eth1", "192.0.2.20", 5000, forced_device=True)
    assert refusal == PinRefusal(VideoFailure.WRONG_INTERFACE, "192.0.2.20 is not reachable through eth1")
    assert net.probes[0].closed


def test_forced_device_refuses_when_the_socket_cannot_be_pinned(net: SimpleNamespace) -> None:
    net.pin_error = InterfaceUnavailable("cannot send via eth1")
    refusal = check_video_pin("eth1", "192.0.2.20", 5000, forced_device=True)
    assert refusal is not None
    assert refusal.failure is VideoFailure.WRONG_INTERFACE
    assert net.probes[0].connected is None
    assert net.probes[0].closed


def test_a_missing_port_still_consults_the_routing_table(net: SimpleNamespace) -> None:
    """``connect`` needs a port; any one asks the same routing question."""
    assert check_video_pin("eth1", "198.51.100.20", forced_device=True) is None
    assert net.probes[0].connected is not None
    assert net.probes[0].connected[1] != 0


# --------------------------------------------------------------------------- #
# Route check (RTSP, and SRT off Linux): the routing table's own choice
# --------------------------------------------------------------------------- #


def test_route_check_accepts_a_camera_the_routing_table_sends_through_the_pin(net: SimpleNamespace) -> None:
    net.local = "198.51.100.10"
    assert check_video_pin("eth1", "198.51.100.20", 554, forced_device=False) is None
    assert net.pinned == []
    assert net.probes[0].connected == ("198.51.100.20", 554)
    assert net.probes[0].closed


def test_route_check_refuses_a_camera_the_routing_table_sends_elsewhere(net: SimpleNamespace) -> None:
    """The element's own socket is unpinned, so reaching the camera through
    the pin is not enough: the default route is what it will take."""
    net.local = "192.0.2.10"
    refusal = check_video_pin("eth1", "203.0.113.20", 554, forced_device=False)
    assert refusal == PinRefusal(VideoFailure.WRONG_INTERFACE, "203.0.113.20 is reached through eth0, not eth1")


def test_route_check_names_the_source_address_when_no_interface_owns_it(net: SimpleNamespace) -> None:
    net.local = "203.0.113.99"
    refusal = check_video_pin("eth1", "203.0.113.20", 554, forced_device=False)
    assert refusal is not None
    assert refusal.detail == "203.0.113.20 is reached through 203.0.113.99, not eth1"


def test_route_check_leaves_a_camera_with_no_route_at_all_to_the_element(net: SimpleNamespace) -> None:
    """No route anywhere is not a pin fault; the element reports it itself."""
    net.connect_error = errno.ENETUNREACH
    assert check_video_pin("eth1", "203.0.113.20", 554, forced_device=False) is None
    assert net.probes[0].closed


# --------------------------------------------------------------------------- #
# Names
# --------------------------------------------------------------------------- #


def test_a_hostname_is_checked_at_the_address_it_resolves_to(net: SimpleNamespace) -> None:
    net.local = "192.0.2.10"
    refusal = check_video_pin("eth1", "camera.example", 554, forced_device=False)
    assert net.probes[0].connected == ("192.0.2.20", 554)
    assert refusal is not None
    assert refusal.detail == "camera.example is reached through eth0, not eth1"


def test_a_name_that_does_not_resolve_is_refused_as_unreachable(net: SimpleNamespace) -> None:
    refusal = check_video_pin("eth1", "nowhere.example", 554)
    assert refusal == PinRefusal(
        VideoFailure.UNREACHABLE, "nowhere.example could not be resolved to check it against eth1"
    )
    assert net.probes == []


def test_a_resolved_name_is_reused_until_it_expires(net: SimpleNamespace) -> None:
    net.local = "198.51.100.10"
    net.answers["camera.example"] = "198.51.100.20"
    for _ in range(3):
        assert check_video_pin("eth1", "camera.example", 554, forced_device=False) is None
    assert net.lookups == ["camera.example"]

    net.clock.now += 31.0
    net.answers["camera.example"] = "198.51.100.30"
    check_video_pin("eth1", "camera.example", 554, forced_device=False)
    assert net.lookups == ["camera.example", "camera.example"]
    assert net.probes[-1].connected == ("198.51.100.30", 554)


def test_a_failed_lookup_is_not_remembered(net: SimpleNamespace) -> None:
    assert check_video_pin("eth1", "late.example", 554) is not None
    net.answers["late.example"] = "198.51.100.20"
    net.local = "198.51.100.10"
    assert check_video_pin("eth1", "late.example", 554, forced_device=False) is None
    assert net.lookups == ["late.example", "late.example"]


def test_a_slow_lookup_does_not_hold_the_attempt_and_serves_the_next_one(
    net: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The check runs on the main loop: a slow resolver refuses this attempt
    and the answer it brings back serves the retry, from one lookup."""
    release = threading.Event()
    calls: list[str] = []

    def _slow(host: str, port: Any, family: int, kind: int) -> list[tuple[Any, ...]]:
        calls.append(host)
        release.wait(5)
        return [(family, kind, 17, "", ("198.51.100.20", 0))]

    monkeypatch.setattr(_pin.socket, "getaddrinfo", _slow)
    monkeypatch.setattr(_pin, "_RESOLVE_WAIT_S", 0.01)
    net.local = "198.51.100.10"

    first = check_video_pin("eth1", "slow.example", 554, forced_device=False)
    second = check_video_pin("eth1", "slow.example", 554, forced_device=False)
    assert first is not None and first.failure is VideoFailure.UNREACHABLE
    assert second is not None and second.failure is VideoFailure.UNREACHABLE

    release.set()
    monkeypatch.setattr(_pin, "_RESOLVE_WAIT_S", 5.0)
    assert check_video_pin("eth1", "slow.example", 554, forced_device=False) is None
    assert calls == ["slow.example"]


# --------------------------------------------------------------------------- #
# Config and address helpers
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("config", "expected"),
    [({}, ""), ({"video_input_iface": None}, ""), ({"video_input_iface": "eth1"}, "eth1")],
    ids=["absent", "none", "set"],
)
def test_config_pin_reads_the_input_config(config: dict[str, Any], expected: str) -> None:
    assert config_pin(config) == expected


@pytest.mark.parametrize(("pin", "expected"), [("", ""), ("eth1", "198.51.100.10"), ("eth9", "")])
def test_pinned_address(net: SimpleNamespace, pin: str, expected: str) -> None:
    assert pinned_address(pin) == expected
