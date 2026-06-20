# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Integration tests for PsnReceiver packet handling.

Drives ``_on_packet`` with fake packets: ignore-id filter, protocol-speed
vs position-derived speed, and the ``is_marker_online`` timeout.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

import openfollow.psn.receiver as receiver_module
from openfollow.psn.receiver import PsnReceiver

pytestmark = pytest.mark.integration


@dataclass
class _Vec:
    x: float
    y: float
    z: float


@dataclass
class _PacketTracker:
    """Mirror of ``pypsn.PsnTracker`` for the fake-packet path. pypsn keeps
    the wire-protocol field names (``tracker_id`` / ``trackers``); the
    domain layer translates them into ``Marker`` objects when
    ``PsnReceiver._on_packet`` reads a packet."""

    tracker_id: int
    pos: _Vec | None
    speed: _Vec | None


class _FakeDataPacket:
    def __init__(self, trackers: list[_PacketTracker]) -> None:
        self.trackers = trackers


def test_receiver_ignores_ids_and_uses_protocol_speed(monkeypatch) -> None:
    monkeypatch.setattr(receiver_module.pypsn, "PsnDataPacket", _FakeDataPacket)
    monkeypatch.setattr(receiver_module.time, "monotonic", lambda: 10.0)

    receiver = PsnReceiver(ignore_ids=[1])
    packet = _FakeDataPacket(
        [
            _PacketTracker(1, _Vec(0.0, 0.0, 0.0), _Vec(0.0, 0.0, 0.0)),
            _PacketTracker(2, _Vec(1.0, 2.0, 3.0), _Vec(3.0, 4.0, 0.0)),
        ]
    )
    receiver._on_packet(packet)

    assert receiver.get_marker(1) is None
    marker = receiver.get_marker(2)
    assert marker is not None
    vx, vy, vz = marker.speed
    assert vx == pytest.approx(5.0)
    assert vy == 0.0
    assert vz == 0.0


def test_receiver_derives_speed_from_position_when_protocol_speed_is_zero(monkeypatch) -> None:
    monkeypatch.setattr(receiver_module.pypsn, "PsnDataPacket", _FakeDataPacket)
    times = iter([1.0, 1.5])
    monkeypatch.setattr(receiver_module.time, "monotonic", lambda: next(times))

    receiver = PsnReceiver()
    receiver._on_packet(_FakeDataPacket([_PacketTracker(5, _Vec(0.0, 0.0, 0.0), _Vec(0.0, 0.0, 0.0))]))
    receiver._on_packet(_FakeDataPacket([_PacketTracker(5, _Vec(1.0, 0.0, 0.0), _Vec(0.0, 0.0, 0.0))]))

    marker = receiver.get_marker(5)
    assert marker is not None
    vx, _, _ = marker.speed
    assert vx == pytest.approx(2.0)


def test_receiver_online_timeout_uses_last_seen(monkeypatch) -> None:
    receiver = PsnReceiver()
    receiver._last_seen[9] = 10.0

    monkeypatch.setattr(receiver_module.time, "monotonic", lambda: 11.0)
    assert receiver.is_marker_online(9, timeout=2.0) is True
    assert receiver.is_marker_online(9, timeout=0.5) is False


def test_receiver_skips_tracker_id_zero_without_dropping_later_trackers(monkeypatch) -> None:
    """A wire tracker_id 0 (the reserved 'ignored' id) must be skipped, not
    abort the per-packet loop and drop every later tracker in the frame."""
    monkeypatch.setattr(receiver_module.pypsn, "PsnDataPacket", _FakeDataPacket)
    monkeypatch.setattr(receiver_module.time, "monotonic", lambda: 10.0)

    receiver = PsnReceiver()
    packet = _FakeDataPacket(
        [
            _PacketTracker(0, _Vec(9.0, 9.0, 9.0), _Vec(0.0, 0.0, 0.0)),
            _PacketTracker(5, _Vec(1.0, 2.0, 3.0), _Vec(0.0, 0.0, 0.0)),
        ]
    )
    receiver._on_packet(packet)

    assert receiver.get_marker(0) is None  # id 0 skipped, no ValueError
    marker = receiver.get_marker(5)  # later tracker still processed
    assert marker is not None
    assert marker.pos == pytest.approx((1.0, 2.0, 3.0))


def test_receiver_skips_non_int_tracker_id_without_dropping_later_trackers(monkeypatch) -> None:
    """#540 Low: a non-int tracker_id must be skipped, not raise on the ``< 1``
    compare and abort the whole frame."""
    monkeypatch.setattr(receiver_module.pypsn, "PsnDataPacket", _FakeDataPacket)
    monkeypatch.setattr(receiver_module.time, "monotonic", lambda: 10.0)

    receiver = PsnReceiver()
    packet = _FakeDataPacket(
        [
            _PacketTracker("x", _Vec(9.0, 9.0, 9.0), _Vec(0.0, 0.0, 0.0)),  # non-int id
            _PacketTracker(5, _Vec(1.0, 2.0, 3.0), _Vec(0.0, 0.0, 0.0)),
        ]
    )
    receiver._on_packet(packet)  # must not raise

    marker = receiver.get_marker(5)
    assert marker is not None
    assert marker.pos == pytest.approx((1.0, 2.0, 3.0))


def test_receiver_evicts_stale_markers(monkeypatch) -> None:
    """#540: markers silent past the TTL are dropped from all three dicts so an
    enumerated tracker_id can't persist for the process lifetime."""
    monkeypatch.setattr(receiver_module.pypsn, "PsnDataPacket", _FakeDataPacket)
    times = iter([0.0, 100.0])  # create at t=0, sweep+update at t=100 (> TTL + interval)
    monkeypatch.setattr(receiver_module.time, "monotonic", lambda: next(times))

    receiver = PsnReceiver()
    receiver._on_packet(
        _FakeDataPacket(
            [
                _PacketTracker(1, _Vec(0.0, 0.0, 0.0), _Vec(0.0, 0.0, 0.0)),
                _PacketTracker(2, _Vec(0.0, 0.0, 0.0), _Vec(0.0, 0.0, 0.0)),
                _PacketTracker(3, _Vec(0.0, 0.0, 0.0), _Vec(0.0, 0.0, 0.0)),
            ]
        )
    )
    assert set(receiver._markers) == {1, 2, 3}

    # A later packet for marker 2 sweeps the now-stale 1 and 3 out of every dict.
    receiver._on_packet(_FakeDataPacket([_PacketTracker(2, _Vec(1.0, 0.0, 0.0), _Vec(0.0, 0.0, 0.0))]))
    assert set(receiver._markers) == {2}
    assert set(receiver._last_seen) == {2}
    assert set(receiver._last_pos) == {2}


def test_receiver_eviction_sweep_is_throttled(monkeypatch) -> None:
    """#540: the sweep runs at most every _EVICT_SWEEP_INTERVAL_S so a packet
    flood can't make it hot – a stale entry survives until the next sweep."""
    monkeypatch.setattr(receiver_module.pypsn, "PsnDataPacket", _FakeDataPacket)
    times = iter([2.0, 3.0])  # both within the sweep interval of the 0.0 baseline
    monkeypatch.setattr(receiver_module.time, "monotonic", lambda: next(times))

    receiver = PsnReceiver()
    receiver._last_seen[99] = -1000.0  # very stale, but no sweep is due yet
    receiver._on_packet(_FakeDataPacket([_PacketTracker(1, _Vec(0.0, 0.0, 0.0), _Vec(0.0, 0.0, 0.0))]))
    receiver._on_packet(_FakeDataPacket([_PacketTracker(1, _Vec(0.0, 0.0, 0.0), _Vec(0.0, 0.0, 0.0))]))
    assert 99 in receiver._last_seen  # throttled – not yet swept
