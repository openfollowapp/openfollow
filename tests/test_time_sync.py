# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for the SNTP client + system-clock set in ``runtime/time_sync``.

Hermetic: the UDP socket is faked (no real network) and the clock is never
actually set – a :class:`FakeBroker` records the would-be ``date`` invocation.
"""

from __future__ import annotations

import socket as _socket
import struct

import pytest

from openfollow.privilege.capabilities import SYSTEM_SET_CLOCK, CapabilityState
from openfollow.runtime import time_sync
from openfollow.runtime.time_sync import (
    is_plausible_epoch,
    query_ntp,
    set_system_clock,
)
from tests._fake_broker import FakeBroker, make_failure

pytestmark = pytest.mark.unit

_NTP_OFFSET = 2_208_988_800


def _ntp_reply(unix_epoch: int) -> bytes:
    """48-byte SNTP reply carrying *unix_epoch* in the transmit timestamp.

    The NTP seconds field wraps mod 2**32, so a post-2036 (era 1) epoch is
    represented the same way a real server would send it.
    """
    ntp_secs = (unix_epoch + _NTP_OFFSET) % (2**32)
    return b"\x00" * 40 + struct.pack("!I", ntp_secs) + b"\x00" * 4


class _FakeSocket:
    def __init__(self, reply: bytes, *, echo_nonce: bool = True) -> None:
        self._reply = reply
        self._echo_nonce = echo_nonce
        self.sent: list[bytes] = []
        self.connected: object = None

    def settimeout(self, _t: float) -> None:
        pass

    def connect(self, addr: object) -> None:
        self.connected = addr

    def send(self, data: bytes) -> None:
        self.sent.append(data)

    def recv(self, _n: int) -> bytes:
        reply = self._reply
        # A real server echoes the request's transmit timestamp into the reply's
        # originate field (bytes 24-31). Mirror that so the nonce check passes,
        # unless the test wants a spoofed (non-echoing) reply.
        if self._echo_nonce and self.sent and len(reply) >= 48:
            nonce = self.sent[-1][40:48]
            reply = reply[:24] + nonce + reply[32:]
        return reply

    def __enter__(self) -> _FakeSocket:
        return self

    def __exit__(self, *_a: object) -> bool:
        return False


def _patch_socket(monkeypatch: pytest.MonkeyPatch, reply: bytes, *, echo_nonce: bool = True) -> _FakeSocket:
    fake = _FakeSocket(reply, echo_nonce=echo_nonce)
    monkeypatch.setattr(_socket, "socket", lambda *a, **k: fake)
    return fake


# ---------------------------------------------------------------------------
# query_ntp
# ---------------------------------------------------------------------------


def test_query_ntp_parses_transmit_timestamp(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _patch_socket(monkeypatch, _ntp_reply(1_735_700_000))
    assert query_ntp("ptbtime1.ptb.de") == pytest.approx(1_735_700_000.0)
    # The socket was connect()-ed to the NTP server (so only its replies land).
    assert fake.connected == ("ptbtime1.ptb.de", 123)
    assert fake.sent and len(fake.sent[0]) == 48


def test_query_ntp_rejects_unechoed_nonce(monkeypatch: pytest.MonkeyPatch) -> None:
    # A reply whose originate field doesn't echo our nonce is a spoof / stray
    # datagram and must be rejected, not used to set the clock.
    _patch_socket(monkeypatch, _ntp_reply(1_735_700_000), echo_nonce=False)
    with pytest.raises(ValueError, match="does not echo"):
        query_ntp("ptbtime1.ptb.de")


def test_query_ntp_handles_era1_rollover(monkeypatch: pytest.MonkeyPatch) -> None:
    # A post-2036 (NTP era 1) timestamp must convert to the correct Unix epoch,
    # not a negative value that silently disables time-sync forever.
    future = 2_500_000_000  # ~2049, inside the plausibility window
    _patch_socket(monkeypatch, _ntp_reply(future))
    assert query_ntp("ptbtime1.ptb.de") == pytest.approx(float(future))


def test_query_ntp_rejects_empty_server() -> None:
    with pytest.raises(ValueError, match="empty NTP server"):
        query_ntp("")


def test_query_ntp_rejects_short_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_socket(monkeypatch, b"\x00" * 10)
    with pytest.raises(ValueError, match="short NTP reply"):
        query_ntp("ptbtime1.ptb.de")


def test_query_ntp_rejects_zero_timestamp(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_socket(monkeypatch, b"\x00" * 48)
    with pytest.raises(ValueError, match="zero transmit timestamp"):
        query_ntp("ptbtime1.ptb.de")


def test_query_ntp_propagates_socket_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Timeout(_FakeSocket):
        def recv(self, _n: int) -> bytes:
            raise TimeoutError("timed out")

    monkeypatch.setattr(_socket, "socket", lambda *a, **k: _Timeout(b""))
    with pytest.raises(TimeoutError):
        query_ntp("ptbtime1.ptb.de")


# ---------------------------------------------------------------------------
# is_plausible_epoch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "epoch,expected",
    [
        (1_735_700_000, True),  # 2025 – inside window
        (100, False),  # 1970 – below floor (a Pi booting un-synced)
        (5_000_000_000, False),  # ~2128 – above ceiling
    ],
)
def test_is_plausible_epoch(epoch: int, expected: bool) -> None:
    assert is_plausible_epoch(epoch) is expected


# ---------------------------------------------------------------------------
# set_system_clock
# ---------------------------------------------------------------------------


def test_set_clock_passwordless_runs_date() -> None:
    broker = FakeBroker()
    assert set_system_clock(broker, 1_735_700_000) is True
    assert len(broker.calls) == 1
    call = broker.calls[0]
    assert call.capability is SYSTEM_SET_CLOCK
    assert call.argv == ["/usr/bin/date", "-s", "@1735700000"]
    # A background sync must never be able to pop a password prompt.
    assert call.allow_prompt is False


def test_set_clock_skips_when_needs_password() -> None:
    broker = FakeBroker(states_map={SYSTEM_SET_CLOCK.name: CapabilityState.NEEDS_PASSWORD})
    assert set_system_clock(broker, 1_735_700_000) is False
    assert broker.calls == []  # never ran -> never prompts


def test_set_clock_skips_when_unavailable() -> None:
    broker = FakeBroker(states_map={SYSTEM_SET_CLOCK.name: CapabilityState.UNAVAILABLE})
    assert set_system_clock(broker, 1_735_700_000) is False
    assert broker.calls == []


def test_set_clock_refuses_implausible_epoch() -> None:
    broker = FakeBroker()
    assert set_system_clock(broker, 100) is False
    assert broker.calls == []


def test_set_clock_none_broker() -> None:
    assert set_system_clock(None, 1_735_700_000) is False


def test_set_clock_returns_false_on_privilege_error() -> None:
    broker = FakeBroker(exceptions=[make_failure("clock denied")])
    assert set_system_clock(broker, 1_735_700_000) is False


def test_drift_threshold_constant_is_small() -> None:
    # The worker only corrects meaningful drift; pin the contract.
    assert pytest.approx(2.0) == time_sync.DRIFT_THRESHOLD_S
