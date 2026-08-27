# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Integration tests for PsnReceiver packet handling.

Drives ``_on_packet`` with fake packets: ignore-id filter, wire speed vs
position-derived speed, and the ``is_marker_online`` timeout.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from conftest import psn_packet_clock as _packet_clock

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
    timestamp: int = 0
    status: float = 0.0


@dataclass
class _FieldlessPacketTracker:
    """A tracker whose parser emitted neither optional chunk - the shape
    ``pypsn`` would hand us if it stopped exposing the two attributes."""

    tracker_id: int
    pos: _Vec | None
    speed: _Vec | None


class _FakeDataPacket:
    def __init__(self, trackers: list[_PacketTracker]) -> None:
        self.trackers = trackers


def test_receiver_ignores_ids_and_stores_the_wire_speed_vector(monkeypatch) -> None:
    """The PSN speed field is a velocity in the position's frame; it lands on
    the marker as sent, direction included."""
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
    assert marker.speed == pytest.approx((3.0, 4.0, 0.0))


def test_receiver_derives_speed_from_position_when_protocol_speed_is_zero(monkeypatch) -> None:
    monkeypatch.setattr(receiver_module.pypsn, "PsnDataPacket", _FakeDataPacket)
    monkeypatch.setattr(receiver_module.time, "monotonic", _packet_clock(1.0, 1.5))

    receiver = PsnReceiver()
    receiver._on_packet(_FakeDataPacket([_PacketTracker(5, _Vec(0.0, 0.0, 0.0), _Vec(0.0, 0.0, 0.0))]))
    receiver._on_packet(_FakeDataPacket([_PacketTracker(5, _Vec(1.0, 0.0, 0.0), _Vec(0.0, 0.0, 0.0))]))

    marker = receiver.get_marker(5)
    assert marker is not None
    vx, _, _ = marker.speed
    assert vx == pytest.approx(2.0)


def test_wire_zero_after_a_nonzero_speed_is_stored_as_zero(monkeypatch) -> None:
    """A sender that publishes speed is trusted when it says the marker stands
    still: its zero is the velocity, not a cue to derive one from the position
    delta. Otherwise a marker that stopped would keep its last speed on the
    viewer's card."""
    monkeypatch.setattr(receiver_module.pypsn, "PsnDataPacket", _FakeDataPacket)
    monkeypatch.setattr(receiver_module.time, "monotonic", _packet_clock(1.0, 1.5))

    receiver = PsnReceiver()
    receiver._on_packet(_FakeDataPacket([_PacketTracker(5, _Vec(0.0, 0.0, 0.0), _Vec(0.0, 2.0, 0.0))]))
    receiver._on_packet(_FakeDataPacket([_PacketTracker(5, _Vec(1.0, 0.0, 0.0), _Vec(0.0, 0.0, 0.0))]))

    marker = receiver.get_marker(5)
    assert marker is not None
    assert marker.speed == (0.0, 0.0, 0.0)


def test_wire_speed_tracker_keeps_previous_vector_when_a_packet_omits_it(monkeypatch) -> None:
    monkeypatch.setattr(receiver_module.pypsn, "PsnDataPacket", _FakeDataPacket)
    monkeypatch.setattr(receiver_module.time, "monotonic", _packet_clock(1.0, 1.5))

    receiver = PsnReceiver()
    receiver._on_packet(_FakeDataPacket([_PacketTracker(5, _Vec(0.0, 0.0, 0.0), _Vec(1.0, 0.0, 0.0))]))
    receiver._on_packet(_FakeDataPacket([_PacketTracker(5, _Vec(1.0, 0.0, 0.0), None)]))

    marker = receiver.get_marker(5)
    assert marker is not None
    assert marker.speed == (1.0, 0.0, 0.0)


def test_wire_trust_is_per_tracker(monkeypatch) -> None:
    """One sender filling the field does not switch off derivation for a
    tracker whose sender never does."""
    monkeypatch.setattr(receiver_module.pypsn, "PsnDataPacket", _FakeDataPacket)
    monkeypatch.setattr(receiver_module.time, "monotonic", _packet_clock(1.0, 1.5))

    receiver = PsnReceiver()
    for x in (0.0, 1.0):
        receiver._on_packet(
            _FakeDataPacket(
                [
                    _PacketTracker(1, _Vec(x, 0.0, 0.0), _Vec(0.0, 0.0, 3.0)),
                    _PacketTracker(2, _Vec(x, 0.0, 0.0), _Vec(0.0, 0.0, 0.0)),
                ]
            )
        )

    trusted = receiver.get_marker(1)
    derived = receiver.get_marker(2)
    assert trusted is not None and derived is not None
    assert trusted.speed == (0.0, 0.0, 3.0)
    assert derived.speed == pytest.approx((2.0, 0.0, 0.0))


def test_evicted_tracker_forgets_its_wire_speed_trust(monkeypatch) -> None:
    """After TTL eviction a returning tracker id may belong to a different
    sender; it starts over on the derive path until it publishes a speed."""
    monkeypatch.setattr(receiver_module.pypsn, "PsnDataPacket", _FakeDataPacket)
    now = {"t": 0.0}
    monkeypatch.setattr(receiver_module.time, "monotonic", lambda: now["t"])

    receiver = PsnReceiver()
    receiver._on_packet(_FakeDataPacket([_PacketTracker(1, _Vec(0.0, 0.0, 0.0), _Vec(0.0, 2.0, 0.0))]))
    assert 1 in receiver._wire_speed_ids

    now["t"] = 100.0  # past the TTL and the sweep interval
    receiver._on_packet(_FakeDataPacket([_PacketTracker(2, _Vec(0.0, 0.0, 0.0), _Vec(0.0, 0.0, 0.0))]))
    assert receiver.get_marker(1) is None
    assert 1 not in receiver._wire_speed_ids

    receiver._on_packet(_FakeDataPacket([_PacketTracker(1, _Vec(0.0, 0.0, 0.0), _Vec(0.0, 0.0, 0.0))]))
    now["t"] = 100.5
    receiver._on_packet(_FakeDataPacket([_PacketTracker(1, _Vec(1.0, 0.0, 0.0), _Vec(0.0, 0.0, 0.0))]))
    returned = receiver.get_marker(1)
    assert returned is not None
    assert returned.speed == pytest.approx((2.0, 0.0, 0.0))


def test_receiver_carries_the_wire_timestamp_and_status(monkeypatch) -> None:
    """Both fields belong to the sender: ``timestamp`` counts from the sender's
    start and ``status`` is the validity it published. Stamping our own clock
    (or promoting the status) reports a local quantity under a remote name."""
    monkeypatch.setattr(receiver_module.pypsn, "PsnDataPacket", _FakeDataPacket)
    monkeypatch.setattr(receiver_module.time, "monotonic", lambda: 10.0)

    receiver = PsnReceiver()
    receiver._on_packet(
        _FakeDataPacket([_PacketTracker(5, _Vec(1.0, 2.0, 3.0), _Vec(0.0, 0.0, 0.0), timestamp=4_242, status=0.25)])
    )

    marker = receiver.get_marker(5)
    assert marker is not None
    assert marker.timestamp == 4_242
    assert marker.status == pytest.approx(0.25)
    assert marker.is_remote is True


def test_receiver_lets_the_tracker_timestamp_go_backwards(monkeypatch) -> None:
    """A restarted sender resets its tracker clock. No local stamp can move
    backwards, so this fails any implementation that stamps - without the test
    itself having to know what our clock reads."""
    monkeypatch.setattr(receiver_module.pypsn, "PsnDataPacket", _FakeDataPacket)
    monkeypatch.setattr(receiver_module.time, "monotonic", _packet_clock(1.0, 1.5))

    receiver = PsnReceiver()
    zero = _Vec(0.0, 0.0, 0.0)
    receiver._on_packet(_FakeDataPacket([_PacketTracker(5, _Vec(1.0, 0.0, 0.0), zero, timestamp=9_000_000)]))
    receiver._on_packet(_FakeDataPacket([_PacketTracker(5, _Vec(2.0, 0.0, 0.0), zero, timestamp=25)]))

    marker = receiver.get_marker(5)
    assert marker is not None
    assert marker.timestamp == 25


def test_receiver_survives_a_tracker_without_timestamp_or_status(monkeypatch) -> None:
    """``pypsn`` is an unpinned git dependency. If a parser swap stops exposing
    either field, the tracker must degrade to the default - not raise and take
    every later tracker in the frame down with it."""
    monkeypatch.setattr(receiver_module.pypsn, "PsnDataPacket", _FakeDataPacket)
    monkeypatch.setattr(receiver_module.time, "monotonic", lambda: 10.0)

    receiver = PsnReceiver()
    zero = _Vec(0.0, 0.0, 0.0)
    receiver._on_packet(
        _FakeDataPacket(
            [
                _FieldlessPacketTracker(5, _Vec(1.0, 2.0, 3.0), zero),
                _PacketTracker(6, _Vec(4.0, 5.0, 6.0), zero, timestamp=77, status=1.0),
            ]
        )
    )

    fieldless = receiver.get_marker(5)
    assert fieldless is not None
    assert fieldless.pos == (1.0, 2.0, 3.0)
    assert fieldless.timestamp == 0
    assert fieldless.status == 0.0
    later = receiver.get_marker(6)
    assert later is not None
    assert later.timestamp == 77


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
    """A non-int tracker_id must be skipped, not raise on the ``< 1`` compare
    and abort the whole frame."""
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
    """#540: markers silent past the TTL are dropped from every per-id
    structure so an enumerated tracker_id can't persist for the process
    lifetime."""
    monkeypatch.setattr(receiver_module.pypsn, "PsnDataPacket", _FakeDataPacket)
    monkeypatch.setattr(
        receiver_module.time, "monotonic", _packet_clock(0.0, 100.0)
    )  # create at t=0, sweep+update at t=100 (> TTL + interval)

    receiver = PsnReceiver()
    receiver._on_packet(
        _FakeDataPacket(
            [
                _PacketTracker(1, _Vec(0.0, 0.0, 0.0), _Vec(1.0, 0.0, 0.0)),
                _PacketTracker(2, _Vec(0.0, 0.0, 0.0), _Vec(0.0, 0.0, 0.0)),
                _PacketTracker(3, _Vec(0.0, 0.0, 0.0), _Vec(0.0, 0.0, 0.0)),
            ]
        )
    )
    assert set(receiver._markers) == {1, 2, 3}
    assert receiver._wire_speed_ids == {1}

    # A later packet for marker 2 sweeps the now-stale 1 and 3 out of every structure.
    receiver._on_packet(_FakeDataPacket([_PacketTracker(2, _Vec(1.0, 0.0, 0.0), _Vec(0.0, 0.0, 0.0))]))
    assert set(receiver._markers) == {2}
    assert set(receiver._last_seen) == {2}
    assert set(receiver._last_pos) == {2}
    assert receiver._wire_speed_ids == set()


def test_receiver_eviction_sweep_is_throttled(monkeypatch) -> None:
    """#540: the sweep runs at most every _EVICT_SWEEP_INTERVAL_S so a packet
    flood can't make it hot – a stale entry survives until the next sweep."""
    monkeypatch.setattr(receiver_module.pypsn, "PsnDataPacket", _FakeDataPacket)
    monkeypatch.setattr(
        receiver_module.time, "monotonic", _packet_clock(2.0, 3.0)
    )  # both within the sweep interval of the 0.0 baseline

    receiver = PsnReceiver()
    receiver._last_seen[99] = -1000.0  # very stale, but no sweep is due yet
    receiver._on_packet(_FakeDataPacket([_PacketTracker(1, _Vec(0.0, 0.0, 0.0), _Vec(0.0, 0.0, 0.0))]))
    receiver._on_packet(_FakeDataPacket([_PacketTracker(1, _Vec(0.0, 0.0, 0.0), _Vec(0.0, 0.0, 0.0))]))
    assert 99 in receiver._last_seen  # throttled – not yet swept
