# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Checks for scripts/hw_validation/dhcp_fqdn_probe.py: the frame parsing and the verdict
the station FQDN's wire check rests on."""

from __future__ import annotations

import importlib.util
import inspect
import struct
import sys
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.unit


def _load() -> ModuleType:
    source = inspect.getsourcefile(_load)
    assert source, "Could not resolve current test source path"
    script = Path(source).resolve().parents[1] / "scripts" / "hw_validation" / "dhcp_fqdn_probe.py"
    spec = importlib.util.spec_from_file_location("dhcp_fqdn_probe", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Its dataclass resolves string annotations through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


probe = _load()

_MAC = bytes.fromhex("02005e005301")
_FQDN = "of-1.stage.example.com"


def _wire(name: str) -> bytes:
    return b"".join(bytes([len(label)]) + label.encode() for label in name.split(".")) + b"\x00"


def _frame(
    options: bytes, *, op: int = 1, sport: int = 68, dport: int = 67, cookie: bytes = b"\x63\x82\x53\x63"
) -> bytes:
    bootp = bytes([op, 1, 6, 0]) + b"\x00" * 24 + _MAC + b"\x00" * 10 + b"\x00" * 192 + cookie + options + b"\xff"
    udp = struct.pack("!HHHH", sport, dport, 8 + len(bootp), 0) + bootp
    ip = bytes([0x45, 0]) + struct.pack("!H", 20 + len(udp)) + b"\x00" * 5 + bytes([17]) + b"\x00" * 10
    return b"\xff" * 6 + _MAC + b"\x08\x00" + ip + udp


def _request(*extra: bytes) -> bytes:
    return _frame(b"\x35\x01\x03" + b"".join(extra))


def _opt12(name: str) -> bytes:
    return bytes([12, len(name)]) + name.encode()


def _opt81(name: str, flags: int = 0x05) -> bytes:
    body = bytes([flags, 0, 0]) + (_wire(name) if flags & 0x04 else name.encode())
    return bytes([81, len(body)]) + body


def test_a_request_carrying_the_fqdn_is_read_as_the_station_sent_it() -> None:
    message = probe.parse_client_message(_request(_opt81(_FQDN)))
    assert message == probe.ClientMessage(
        kind="REQUEST", mac="02:00:5e:00:53:01", hostname=None, fqdn=_FQDN, fqdn_flags=0x05
    )


def test_a_plain_text_option_81_name_is_read_too() -> None:
    assert probe.parse_client_message(_request(_opt81(_FQDN, flags=0x01))).fqdn == _FQDN


def test_a_request_carrying_the_hostname_is_read() -> None:
    message = probe.parse_client_message(_request(b"\x00", _opt12("openfollow-noble-bear")))
    assert (message.hostname, message.fqdn) == ("openfollow-noble-bear", None)


@pytest.mark.parametrize(
    "frame",
    [
        _frame(b"\x35\x01\x05", op=2, sport=67, dport=68),
        _frame(b"\x35\x01\x03", cookie=b"\x00\x00\x00\x00"),
        _frame(b"\x35\x01\x03", sport=5353, dport=5353),
        _frame(b"\x0c"),
        _frame(b"\x35\x00"),
        _frame(b"\x35\x01\x07"),
        _frame(b"\x35\x01\x04"),
        b"\x00" * 20,
    ],
    ids=["server-reply", "not-dhcp", "other-port", "truncated-option", "empty-type", "release", "decline", "runt"],
)
def test_anything_but_a_client_request_is_ignored(frame: bytes) -> None:
    assert probe.parse_client_message(frame) is None


def _message(**fields) -> object:
    base = {"kind": "REQUEST", "mac": "02:00:5e:00:53:01", "hostname": None, "fqdn": _FQDN, "fqdn_flags": 0x05}
    return probe.ClientMessage(**{**base, **fields})


@pytest.mark.parametrize(
    ("fields", "found"),
    [
        ({}, []),
        ({"fqdn": "OF-1.stage.example.com."}, []),
        ({"fqdn": None, "fqdn_flags": None}, ["no option 81"]),
        ({"fqdn": "other.example.com"}, ["option 81 names 'other.example.com'"]),
        ({"fqdn_flags": 0x04}, ["option 81 does not ask the server to update DNS (S flag clear)"]),
        ({"fqdn_flags": 0x01}, ["option 81 name is not in DNS wire format (E flag clear)"]),
        ({"hostname": "openfollow-noble-bear"}, ["option 12 sent alongside it ('openfollow-noble-bear')"]),
    ],
    ids=["match", "case-and-root-dot", "missing", "wrong-name", "no-s-flag", "no-e-flag", "both-options"],
)
def test_a_request_is_held_to_the_fqdn(fields: dict, found: list[str]) -> None:
    assert probe.problems(_message(**fields), expect_fqdn=_FQDN) == found


@pytest.mark.parametrize(
    ("fields", "found"),
    [
        ({"hostname": "openfollow-noble-bear", "fqdn": None, "fqdn_flags": None}, []),
        ({"hostname": None, "fqdn": None, "fqdn_flags": None}, ["no option 12"]),
        ({"hostname": "raspberrypi", "fqdn": None, "fqdn_flags": None}, ["option 12 names 'raspberrypi'"]),
        ({"hostname": "openfollow-noble-bear"}, [f"option 81 still sent ({_FQDN!r})"]),
    ],
    ids=["match", "missing", "wrong-name", "fqdn-still-sent"],
)
def test_a_request_is_held_to_the_hostname_once_the_fqdn_is_removed(fields: dict, found: list[str]) -> None:
    assert probe.problems(_message(**fields), expect_hostname="openfollow-noble-bear") == found


def test_no_request_is_inconclusive_never_a_pass() -> None:
    """A renewal is unicast to the server, so silence says nothing about what was sent."""
    assert probe.verdict([], expect_fqdn=_FQDN)[0] == probe.INCONCLUSIVE


def test_one_wrong_request_fails_the_run() -> None:
    code, lines = probe.verdict([_message(), _message(fqdn="other.example.com")], expect_fqdn=_FQDN)
    assert code == probe.FAIL
    assert lines[0].startswith("ok ")
    assert lines[1].startswith("FAIL REQUEST from 02:00:5e:00:53:01: option 81 names")


def test_every_request_matching_passes() -> None:
    assert probe.verdict([_message(), _message(kind="DISCOVER")], expect_fqdn=_FQDN)[0] == probe.PASS
