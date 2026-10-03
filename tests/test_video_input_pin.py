# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The video input pin: a pinned network input dials its camera through the
pinned interface or not at all, and says which of the two it observed."""

from __future__ import annotations

import socket
import struct
import threading
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from openfollow import net_egress, net_utils
from openfollow.net_utils import BoundedResolver
from openfollow.video.failure import VideoFailure
from openfollow.video.inputs import _pin
from openfollow.video.inputs._pin import (
    PinRefusal,
    binds_device,
    check_listen_address,
    check_video_pin,
    config_pin,
    is_local_destination,
)

pytestmark = pytest.mark.unit

# Captured before any fixture swaps in a resolver on a test clock.
_PRODUCTION_RESOLVER = _pin._resolver

_V4 = {"eth0": "192.0.2.10", "eth1": "198.51.100.10"}
_V6 = {"eth0": "2001:db8::10", "eth1": "2001:db8:1::10"}


def _word(address: str) -> str:
    """An IPv4 address the way /proc/net/route prints it."""
    return f"{struct.unpack('=I', socket.inet_aton(address))[0]:08X}"


def _route_table(*routes: tuple[str, str, str, int]) -> str:
    """``(iface, destination, mask, flags)`` rows under the kernel's header."""
    header = "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT"
    rows = [
        f"{iface}\t{_word(dest)}\t00000000\t{flags:04X}\t0\t0\t100\t{_word(mask)}\t0\t0\t0"
        for iface, dest, mask, flags in routes
    ]
    return "\n".join([header, *rows]) + "\n"


class _Probe:
    """A UDP socket that records what it is asked; it has no way to send."""

    def __init__(self, family: int, local: str, connect_error: int = 0) -> None:
        self.family = family
        self.local = local
        self.connect_error = connect_error
        self.connected: tuple[str, int] | None = None
        self.closed = False

    def setsockopt(self, level: int, option: int, value: int) -> None:
        pass

    def connect(self, address: tuple[str, int]) -> None:
        if self.connect_error:
            raise OSError(self.connect_error, "Network is unreachable")
        self.connected = address

    def getsockname(self) -> tuple[Any, ...]:
        return (self.local, 40000)

    def close(self) -> None:
        self.closed = True


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture()
def net(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[SimpleNamespace]:
    """Interfaces from ``_V4`` / ``_V6``, a probe socket per check, a route
    table file, and fresh DNS state."""
    routes = tmp_path / "route"
    state = SimpleNamespace(
        probes=[],
        local={socket.AF_INET: _V4["eth0"], socket.AF_INET6: _V6["eth0"]},
        connect_error=0,
        lookups=[],
        answers={"camera.example": ["192.0.2.20"]},
        clock=_Clock(),
        routes=routes,
    )
    routes.write_text(_route_table(("eth0", "0.0.0.0", "0.0.0.0", 0x3), ("eth1", "198.51.100.0", "255.255.255.0", 0x1)))

    def _socket(family: int, kind: int) -> _Probe:
        assert kind == socket.SOCK_DGRAM
        probe = _Probe(family, state.local[family], state.connect_error)
        state.probes.append(probe)
        return probe

    def _getaddrinfo(host: str, port: Any, *args: Any, **kwargs: Any) -> list[tuple[Any, ...]]:
        state.lookups.append(host)
        if host not in state.answers:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        return [
            (socket.AF_INET6 if ":" in a else socket.AF_INET, socket.SOCK_DGRAM, 17, "", (a, 0))
            for a in state.answers[host]
        ]

    addrs = {
        name: [
            SimpleNamespace(family=socket.AF_INET, address=_V4[name]),
            SimpleNamespace(family=socket.AF_INET6, address=_V6[name]),
            SimpleNamespace(family=socket.AF_INET6, address=f"fe80::{name[-1]}%{name}"),
        ]
        for name in _V4
    }
    monkeypatch.setattr(net_egress, "get_iface_ipv4", lambda iface: _V4.get(iface, ""))
    monkeypatch.setattr(_pin.psutil, "net_if_addrs", lambda: addrs)
    monkeypatch.setattr(_pin.socket, "socket", _socket)
    monkeypatch.setattr(_pin.socket, "getaddrinfo", _getaddrinfo)
    monkeypatch.setattr(net_utils, "_PROC_NET_ROUTE", routes)
    monkeypatch.setattr(
        _pin,
        "_resolver",
        # The lifetimes of the production resolver, on a clock the test turns.
        BoundedResolver(ttl_s=30.0, failure_ttl_s=10.0, thread_name="test-pin-dns", clock=state.clock),
    )
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


@pytest.mark.parametrize("forced", [True, False], ids=["forced-device", "route-check"])
@pytest.mark.parametrize("address", ["192.0.2.10", "2001:db8::10", "127.0.0.1"])
def test_a_camera_served_by_this_station_is_never_refused(net: SimpleNamespace, forced: bool, address: str) -> None:
    """A restream at one of this station's own addresses never leaves the box,
    whichever interface holds that address."""
    net.answers["camera.example"] = [address]
    net.routes.write_text(_route_table())
    assert check_video_pin("eth1", "camera.example", 8554, forced_device=forced) is None
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
# Forced device (SRT on Linux): a route through the pin must exist
# --------------------------------------------------------------------------- #


def test_forced_device_accepts_a_camera_on_a_route_through_the_pin(net: SimpleNamespace) -> None:
    assert check_video_pin("eth1", "198.51.100.20", 5000, forced_device=True) is None
    # A probe connect proves nothing here: the kernel lets a device-bound
    # socket connect anywhere by assuming the destination is on-link.
    assert net.probes == []


def test_forced_device_refuses_a_camera_with_no_route_through_the_pin(net: SimpleNamespace) -> None:
    refusal = check_video_pin("eth1", "203.0.113.20", 5000, forced_device=True)
    assert refusal == PinRefusal(VideoFailure.WRONG_INTERFACE, "203.0.113.20 is not reachable through eth1")


def test_forced_device_accepts_any_camera_when_the_pin_carries_a_default_route(net: SimpleNamespace) -> None:
    net.routes.write_text(_route_table(("eth1", "0.0.0.0", "0.0.0.0", 0x3)))
    assert check_video_pin("eth1", "203.0.113.20", 5000, forced_device=True) is None


def test_forced_device_skips_lines_it_cannot_read(net: SimpleNamespace) -> None:
    table = _route_table(("eth1", "198.51.100.0", "255.255.255.0", 0x1))
    header, rest = table.split("\n", 1)
    net.routes.write_text(f"{header}\neth1\tshort\neth1\tzz\t0\t1\t0\t0\t0\tzz\n{rest}")
    assert check_video_pin("eth1", "198.51.100.20", 5000, forced_device=True) is None
    assert check_video_pin("eth1", "203.0.113.20", 5000, forced_device=True) is not None


def test_forced_device_without_a_readable_route_table_does_not_refuse(net: SimpleNamespace) -> None:
    """The element is bound to the device anyway; nothing can leave elsewhere."""
    net.routes.unlink()
    assert check_video_pin("eth1", "203.0.113.20", 5000, forced_device=True) is None


def test_forced_device_route_checks_an_ipv6_address(net: SimpleNamespace) -> None:
    """libsrt refuses a device on an IPv6 socket, so IPv6 takes the routing table's choice."""
    net.answers["camera.example"] = ["2001:db8:2::20"]
    refusal = check_video_pin("eth1", "camera.example", 5000, forced_device=True)
    assert refusal == PinRefusal(VideoFailure.WRONG_INTERFACE, "camera.example is reached through eth0, not eth1")
    assert net.probes[0].family == socket.AF_INET6


def test_forced_device_route_checks_every_address_of_a_name_with_an_ipv6_one(net: SimpleNamespace) -> None:
    """The connection is not bound to the device then, so the IPv4 address is
    judged by where the routing table sends it too, not by any route through the pin."""
    net.answers["camera.example"] = ["203.0.113.20", "2001:db8:1::20"]
    net.local = {socket.AF_INET: _V4["eth1"], socket.AF_INET6: _V6["eth1"]}
    assert check_video_pin("eth1", "camera.example", 5000, forced_device=True) is None
    assert [p.family for p in net.probes] == [socket.AF_INET, socket.AF_INET6]


def test_a_name_with_this_stations_ipv6_address_is_not_judged_as_bound(net: SimpleNamespace) -> None:
    """libsrt may dial the local IPv6 answer, so no device is bound and the remote
    IPv4 answer is judged by the routing table's choice, not a route through the pin."""
    net.answers["camera.example"] = [_V6["eth0"], "203.0.113.20"]
    net.local[socket.AF_INET] = _V4["eth1"]
    assert check_video_pin("eth1", "camera.example", 5000, forced_device=True) is None
    assert [p.family for p in net.probes] == [socket.AF_INET]


# --------------------------------------------------------------------------- #
# Route check (RTSP, and SRT off Linux): the routing table's own choice
# --------------------------------------------------------------------------- #


def test_route_check_accepts_a_camera_the_routing_table_sends_through_the_pin(net: SimpleNamespace) -> None:
    net.local[socket.AF_INET] = _V4["eth1"]
    assert check_video_pin("eth1", "198.51.100.20", 554, forced_device=False) is None
    assert net.probes[0].connected == ("198.51.100.20", 554)
    assert net.probes[0].closed


def test_route_check_refuses_a_camera_the_routing_table_sends_elsewhere(net: SimpleNamespace) -> None:
    """The element's own socket is unpinned, so reaching the camera through
    the pin is not enough: the default route is what it will take."""
    refusal = check_video_pin("eth1", "203.0.113.20", 554, forced_device=False)
    assert refusal == PinRefusal(VideoFailure.WRONG_INTERFACE, "203.0.113.20 is reached through eth0, not eth1")
    assert net.probes[0].closed


def test_route_check_names_the_source_address_when_no_interface_owns_it(net: SimpleNamespace) -> None:
    net.local[socket.AF_INET] = "203.0.113.99"
    refusal = check_video_pin("eth1", "203.0.113.20", 554, forced_device=False)
    assert refusal is not None
    assert refusal.detail == "203.0.113.20 is reached through 203.0.113.99, not eth1"


def test_route_check_leaves_a_camera_with_no_route_at_all_to_the_element(net: SimpleNamespace) -> None:
    """No route anywhere is not a pin fault; the element reports it itself."""
    net.connect_error = 101  # ENETUNREACH
    assert check_video_pin("eth1", "203.0.113.20", 554, forced_device=False) is None
    assert net.probes[0].closed


def test_a_missing_port_still_consults_the_routing_table(net: SimpleNamespace) -> None:
    """``connect`` needs a port; any one asks the same routing question."""
    net.local[socket.AF_INET] = _V4["eth1"]
    assert check_video_pin("eth1", "198.51.100.20", forced_device=False) is None
    assert net.probes[0].connected is not None
    assert net.probes[0].connected[1] != 0


def test_every_address_a_name_has_is_checked(net: SimpleNamespace) -> None:
    """The element may dial the IPv6 address: one that routes elsewhere refuses
    even when the IPv4 one is on the pin."""
    net.answers["camera.example"] = ["198.51.100.20", "2001:db8:2::20"]
    net.local[socket.AF_INET] = _V4["eth1"]
    refusal = check_video_pin("eth1", "camera.example", 554, forced_device=False)
    assert refusal == PinRefusal(VideoFailure.WRONG_INTERFACE, "camera.example is reached through eth0, not eth1")
    assert [p.family for p in net.probes] == [socket.AF_INET, socket.AF_INET6]


def test_an_ipv6_address_on_the_pin_is_accepted(net: SimpleNamespace) -> None:
    net.local[socket.AF_INET6] = _V6["eth1"]
    assert check_video_pin("eth1", "2001:db8:1::20", 554, forced_device=False) is None


# --------------------------------------------------------------------------- #
# Names
# --------------------------------------------------------------------------- #


def test_a_hostname_is_checked_at_the_address_it_resolves_to(net: SimpleNamespace) -> None:
    refusal = check_video_pin("eth1", "camera.example", 554, forced_device=False)
    assert net.probes[0].connected == ("192.0.2.20", 554)
    assert refusal is not None
    assert refusal.detail == "camera.example is reached through eth0, not eth1"


def test_a_name_that_does_not_resolve_says_so_without_claiming_anything_answered(net: SimpleNamespace) -> None:
    refusal = check_video_pin("eth1", "nowhere.example", 554)
    assert refusal == PinRefusal(
        VideoFailure.UNKNOWN, "nowhere.example does not resolve, so it could not be checked against eth1"
    )
    assert net.probes == []


def test_a_resolved_name_is_reused_until_it_expires(net: SimpleNamespace) -> None:
    net.local[socket.AF_INET] = _V4["eth1"]
    net.answers["camera.example"] = ["198.51.100.20"]
    for _ in range(3):
        assert check_video_pin("eth1", "camera.example", 554, forced_device=False) is None
    assert net.lookups == ["camera.example"]

    net.clock.now += 31.0
    net.answers["camera.example"] = ["198.51.100.30"]
    check_video_pin("eth1", "camera.example", 554, forced_device=False)
    assert net.lookups == ["camera.example", "camera.example"]
    assert net.probes[-1].connected == ("198.51.100.30", 554)


def test_a_failed_lookup_is_retried_once_its_failure_expires(net: SimpleNamespace) -> None:
    """Every reconnect would otherwise wait on the main loop for a camera that is off."""
    assert check_video_pin("eth1", "late.example", 554) is not None
    net.answers["late.example"] = ["198.51.100.20"]
    net.local[socket.AF_INET] = _V4["eth1"]
    assert check_video_pin("eth1", "late.example", 554, forced_device=False) is not None
    assert net.lookups == ["late.example"]

    net.clock.now += 11.0
    assert check_video_pin("eth1", "late.example", 554, forced_device=False) is None
    assert net.lookups == ["late.example", "late.example"]


def test_a_malformed_name_neither_crashes_the_lookup_nor_sticks(
    net: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """IDNA encoding rejects an empty label with a ValueError, not an OSError."""
    calls: list[str] = []

    def _idna(host: str, *args: Any, **kwargs: Any) -> list[tuple[Any, ...]]:
        calls.append(host)
        raise UnicodeError("label empty or too long")

    monkeypatch.setattr(_pin.socket, "getaddrinfo", _idna)
    for _ in range(2):
        refusal = check_video_pin("eth1", "cam..example", 554)
        assert refusal is not None and refusal.failure is VideoFailure.UNKNOWN
        net.clock.now += 11.0
    assert calls == ["cam..example", "cam..example"]


def _join_lookups() -> None:
    for thread in threading.enumerate():
        if thread.name == "test-pin-dns":
            threading.Thread.join(thread, 5)


def test_a_slow_lookup_holds_one_attempt_and_serves_a_later_one(
    net: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The check runs on the main loop: a retry after the wait has passed does
    not wait again, and the one lookup's answer serves a later attempt."""
    release = threading.Event()
    calls: list[str] = []

    def _slow(host: str, *args: Any, **kwargs: Any) -> list[tuple[Any, ...]]:
        calls.append(host)
        release.wait(5)
        return [(socket.AF_INET, socket.SOCK_DGRAM, 17, "", ("198.51.100.20", 0))]

    monkeypatch.setattr(_pin.socket, "getaddrinfo", _slow)
    monkeypatch.setattr(_pin, "_RESOLVE_WAIT_S", 0.2)
    net.local[socket.AF_INET] = _V4["eth1"]
    pending = PinRefusal(VideoFailure.UNKNOWN, "slow.example did not resolve in time to check it against eth1")

    assert check_video_pin("eth1", "slow.example", 554, forced_device=False) == pending
    net.clock.now += 2.0
    # An answer while a second wait would still run tells a wait from none.
    threading.Timer(0.05, release.set).start()
    assert check_video_pin("eth1", "slow.example", 554, forced_device=False) == pending

    _join_lookups()
    assert check_video_pin("eth1", "slow.example", 554, forced_device=False) is None
    assert calls == ["slow.example"]


def test_a_lookup_that_cannot_start_a_thread_says_so_and_is_tried_again(
    net: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    starts: list[int] = []

    class _NoThread:
        def start(self) -> None:
            raise RuntimeError("can't start new thread")

    def _factory(**kwargs: Any) -> Any:
        starts.append(1)
        return _NoThread() if len(starts) == 1 else threading.Thread(**kwargs)

    monkeypatch.setattr(_pin, "_resolver", BoundedResolver(ttl_s=30.0, thread_factory=_factory))
    assert check_video_pin("eth1", "camera.example", 554) == PinRefusal(
        VideoFailure.UNKNOWN, "camera.example could not be looked up (can't start new thread) to check it against eth1"
    )

    net.answers["camera.example"] = ["198.51.100.20"]
    net.local[socket.AF_INET] = _V4["eth1"]
    assert check_video_pin("eth1", "camera.example", 554, forced_device=False) is None


# --------------------------------------------------------------------------- #
# Whether a destination is this station
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("host", "expected"),
    [("", False), ("localhost", True), ("127.0.0.1", True), ("192.0.2.10", True), ("203.0.113.20", False)],
)
def test_is_local_destination(net: SimpleNamespace, host: str, expected: bool) -> None:
    assert is_local_destination(host) is expected


def test_a_name_with_one_remote_address_is_not_local(net: SimpleNamespace) -> None:
    """The element may dial the remote one, which needs the device binding."""
    net.answers["relay.example"] = ["192.0.2.10", "203.0.113.20"]
    check_video_pin("eth1", "relay.example", 9000)
    assert is_local_destination("relay.example") is False


def test_a_local_name_is_not_refused_when_the_pin_is_down(net: SimpleNamespace) -> None:
    """Its traffic never touches the pinned interface, so that interface's outage
    is not its fault, whether or not the name was looked up lately."""
    net.answers["relay.example"] = ["192.0.2.10"]
    assert check_video_pin("eth9", "relay.example", 9000) is None
    net.clock.now += 60.0
    assert check_video_pin("eth9", "relay.example", 9000) is None


def test_the_pin_remembers_a_name_it_checked(net: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    """The SRT build asks whether its camera is this station without a lookup,
    right after the check resolved the name; the answer has to outlive the check."""
    monkeypatch.setattr(_pin, "_resolver", _PRODUCTION_RESOLVER)
    _PRODUCTION_RESOLVER.clear()
    try:
        net.answers["relay.example"] = ["192.0.2.10"]
        check_video_pin("eth1", "relay.example", 9000)
        assert is_local_destination("relay.example") is True
    finally:
        # Shared with every other test in the session.
        _PRODUCTION_RESOLVER.clear()


def test_a_name_is_local_only_once_it_is_known_to_resolve_here(net: SimpleNamespace) -> None:
    """Never looked up from here: an unknown name is treated as remote."""
    net.answers["relay.example"] = ["192.0.2.10"]
    assert is_local_destination("relay.example") is False
    assert net.lookups == []
    check_video_pin("eth1", "relay.example", 9000)
    assert is_local_destination("relay.example") is True


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


@pytest.mark.parametrize(
    ("answers", "expected"),
    [
        ([], True),
        (["203.0.113.20"], True),
        (["192.0.2.10", "203.0.113.20"], True),
        (["192.0.2.10"], False),
        (["203.0.113.20", "2001:db8:2::20"], False),
        (["2001:db8::10", "203.0.113.20"], False),
    ],
    ids=["unknown", "remote-ipv4", "remote-and-local", "local", "with-ipv6", "with-a-local-ipv6"],
)
def test_binds_device_only_for_a_remote_ipv4_destination(
    net: SimpleNamespace, answers: list[str], expected: bool
) -> None:
    """libsrt refuses a device on an IPv6 socket, and a local destination is not
    reached through one; a name never resolved is bound, so nothing leaves elsewhere."""
    if answers:
        net.answers["camera.example"] = answers
        check_video_pin("eth0", "camera.example", 5000)
    assert binds_device("camera.example") is expected


@pytest.mark.parametrize("host", ["", "localhost", "127.0.0.1"])
def test_binds_device_for_nothing_configured_or_this_box(net: SimpleNamespace, host: str) -> None:
    assert binds_device(host) is False


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("198.51.100.10", None),
        ("2001:db8:1::10", None),
        ("192.0.2.10", PinRefusal(VideoFailure.WRONG_INTERFACE, "192.0.2.10 is not an address of eth1")),
        ("203.0.113.9", PinRefusal(VideoFailure.WRONG_INTERFACE, "203.0.113.9 is not an address of eth1")),
    ],
)
def test_check_listen_address(net: SimpleNamespace, address: str, expected: PinRefusal | None) -> None:
    assert check_listen_address("eth1", address) == expected


def test_a_listen_address_given_as_a_name_is_checked_where_it_resolves(net: SimpleNamespace) -> None:
    net.answers["rx.example"] = ["198.51.100.10"]
    assert check_listen_address("eth1", "rx.example") is None
    assert check_listen_address("eth0", "rx.example") == PinRefusal(
        VideoFailure.WRONG_INTERFACE, "rx.example is not an address of eth0"
    )


def test_a_listen_address_that_does_not_resolve_says_so(net: SimpleNamespace) -> None:
    assert check_listen_address("eth1", "nowhere.example") == PinRefusal(
        VideoFailure.UNKNOWN, "nowhere.example does not resolve, so it could not be checked against eth1"
    )


def test_a_refusal_can_name_its_pin_another_way() -> None:
    """The text keeps the interface separate, so the station can put its label there."""
    from openfollow.video.failure import VideoFailure
    from openfollow.video.inputs._pin import PinRefusal, _naming

    refusal = _naming(VideoFailure.WRONG_INTERFACE, "192.0.2.20 is reached through eth0, not {pin}", "eth1")
    assert refusal.detail == "192.0.2.20 is reached through eth0, not eth1"
    assert refusal.text("Video (eth1)") == "192.0.2.20 is reached through eth0, not Video (eth1)"
    # Compared by what it says, as before.
    assert refusal == PinRefusal(VideoFailure.WRONG_INTERFACE, "192.0.2.20 is reached through eth0, not eth1")
    plain = PinRefusal(VideoFailure.UNKNOWN, "the interface pin could not be checked")
    assert plain.text("anything") == "the interface pin could not be checked"
