# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Egress pinning: a pinned sender leaves on its interface or not at all."""

from __future__ import annotations

import errno
import socket
import sys

import pytest

from openfollow import net_egress
from openfollow.net_egress import Egress, is_loopback_host, is_multicast_host, pin_socket_egress, resolve_egress
from openfollow.net_utils import InterfaceUnavailable

pytestmark = pytest.mark.unit


class _FakeSocket:
    """Records the calls a pin makes; ``fail_on`` names the one to refuse."""

    def __init__(self, fail_on: str = "") -> None:
        self.calls: list[tuple] = []
        self._fail_on = fail_on

    def setsockopt(self, level: int, option: int, value: object) -> None:
        self.calls.append(("setsockopt", level, option, value))
        if self._fail_on == "setsockopt":
            raise PermissionError(errno.EPERM, "Operation not permitted")

    def bind(self, address: tuple[str, int]) -> None:
        self.calls.append(("bind", address))
        if self._fail_on == "bind":
            raise OSError(errno.EADDRNOTAVAIL, "Cannot assign requested address")


# --------------------------------------------------------------------------- #
# resolve_egress
# --------------------------------------------------------------------------- #


@pytest.fixture()
def addresses(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    table = {"eth0": "192.0.2.10", "eth1": "198.51.100.10"}
    monkeypatch.setattr(net_egress, "get_iface_ipv4", lambda iface: table.get(iface, ""))
    return table


@pytest.mark.parametrize(
    ("pin", "station", "expected"),
    [
        ("", "", None),
        ("eth1", "", Egress("eth1", "198.51.100.10")),
        ("", "eth0", Egress("eth0", "192.0.2.10")),
        ("eth1", "eth0", Egress("eth1", "198.51.100.10")),
        ("eth9", "eth0", Egress("eth9", "")),
    ],
    ids=["unconfigured", "own-pin", "follows-station", "pin-beats-station", "pinned-but-down"],
)
def test_resolve_egress(addresses: dict[str, str], pin: str, station: str, expected: Egress | None) -> None:
    assert resolve_egress(pin, station) == expected


def test_a_pinned_interface_without_an_address_is_down(addresses: dict[str, str]) -> None:
    egress = resolve_egress("eth9")
    assert egress is not None and egress.down is True
    assert Egress("eth0", "192.0.2.10").down is False


# --------------------------------------------------------------------------- #
# host classification
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("host", "loopback"),
    [("127.0.0.1", True), ("127.8.9.10", True), (" localhost ", True), ("LOCALHOST", True), ("192.0.2.1", False)],
)
def test_is_loopback_host(host: str, loopback: bool) -> None:
    assert is_loopback_host(host) is loopback


@pytest.mark.parametrize("host", ["console.local", "", "not an address"])
def test_a_name_other_than_localhost_is_not_loopback(host: str) -> None:
    assert is_loopback_host(host) is False


@pytest.mark.parametrize(
    ("host", "multicast"),
    [("239.20.20.20", True), ("224.0.0.1", True), ("192.0.2.1", False), ("255.255.255.255", False), ("x", False)],
)
def test_is_multicast_host(host: str, multicast: bool) -> None:
    assert is_multicast_host(host) is multicast


# --------------------------------------------------------------------------- #
# pin_socket_egress
# --------------------------------------------------------------------------- #


def test_linux_forces_the_device_by_name_then_binds_the_address() -> None:
    sock = _FakeSocket()
    pin_socket_egress(sock, Egress("eth1", "198.51.100.10"), platform="linux")  # type: ignore[arg-type]
    assert sock.calls == [
        ("setsockopt", socket.SOL_SOCKET, 25, b"eth1"),
        ("bind", ("198.51.100.10", 0)),
    ]


def test_darwin_uses_ip_bound_if_even_though_the_module_exports_so_bindtodevice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """macOS Python exports a SO_BINDTODEVICE, so a feature test would pick the
    Linux option on the Mac. The platform decides."""
    monkeypatch.setattr(socket, "SO_BINDTODEVICE", 4404, raising=False)
    monkeypatch.setattr(socket, "if_nametoindex", lambda name: {"en1": 7}[name])
    sock = _FakeSocket()
    pin_socket_egress(sock, Egress("en1", "198.51.100.10"), platform="darwin")  # type: ignore[arg-type]
    assert sock.calls == [
        ("setsockopt", socket.IPPROTO_IP, 25, 7),
        ("bind", ("198.51.100.10", 0)),
    ]


def test_multicast_also_sets_the_multicast_interface() -> None:
    sock = _FakeSocket()
    pin_socket_egress(sock, Egress("eth1", "198.51.100.10"), multicast=True, platform="linux")  # type: ignore[arg-type]
    assert sock.calls[-1] == (
        "setsockopt",
        socket.IPPROTO_IP,
        socket.IP_MULTICAST_IF,
        socket.inet_aton("198.51.100.10"),
    )


def test_a_down_egress_raises_and_touches_nothing() -> None:
    sock = _FakeSocket()
    with pytest.raises(InterfaceUnavailable, match="eth1 has no address"):
        pin_socket_egress(sock, Egress("eth1", ""), platform="linux")  # type: ignore[arg-type]
    assert sock.calls == []


@pytest.mark.parametrize("fail_on", ["setsockopt", "bind"])
def test_a_refused_pin_is_raised_as_unavailable(fail_on: str) -> None:
    with pytest.raises(InterfaceUnavailable, match=r"cannot send via eth1 \(198\.51\.100\.10\)") as caught:
        pin_socket_egress(_FakeSocket(fail_on), Egress("eth1", "198.51.100.10"), platform="linux")  # type: ignore[arg-type]
    assert isinstance(caught.value.__cause__, OSError)


def test_an_unknown_darwin_interface_is_raised_as_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    def _no_such(name: str) -> int:
        raise OSError(errno.ENXIO, "no interface with this name")

    monkeypatch.setattr(socket, "if_nametoindex", _no_such)
    sock = _FakeSocket()
    with pytest.raises(InterfaceUnavailable):
        pin_socket_egress(sock, Egress("en9", "198.51.100.10"), platform="darwin")  # type: ignore[arg-type]
    assert sock.calls == []


def test_an_unsupported_platform_refuses_rather_than_sending_unpinned() -> None:
    sock = _FakeSocket()
    with pytest.raises(InterfaceUnavailable, match="not supported on win32"):
        pin_socket_egress(sock, Egress("eth1", "198.51.100.10"), platform="win32")  # type: ignore[arg-type]
    assert sock.calls == []


_LOOPBACK_IFACE = {"linux": "lo", "darwin": "lo0"}.get("linux" if sys.platform.startswith("linux") else sys.platform)


@pytest.mark.skipif(_LOOPBACK_IFACE is None, reason="egress pinning is implemented for Linux and macOS")
def test_a_real_socket_pinned_to_loopback_delivers() -> None:
    """The option is accepted by the running kernel, unprivileged, and traffic
    still flows through the pinned device."""
    assert _LOOPBACK_IFACE is not None
    with (
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver,
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender,
    ):
        receiver.bind(("127.0.0.1", 0))
        receiver.settimeout(2.0)
        pin_socket_egress(sender, Egress(_LOOPBACK_IFACE, "127.0.0.1"))
        sender.sendto(b"pinned", receiver.getsockname())
        assert receiver.recvfrom(64)[0] == b"pinned"
