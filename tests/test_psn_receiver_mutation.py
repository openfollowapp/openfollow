# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Mutation-audit kills for :mod:`openfollow.psn.receiver`.

Pins wire-format parse + speed-derivation semantics of
``PsnReceiver._on_packet`` against mutation survivors. Targets specific
branches that survived mutation:

* ``Marker(t.tracker_id, f"Marker {t.tracker_id}", remote=True)`` – the
  id, the default name, and the remote flag must flow through unchanged.
  The remote flag and the four ``apply_remote`` arguments are killed in
  ``test_psn_receiver.py`` instead, where the wire values they carry are
  the subject rather than the parse.
* ``wire is not None`` vs ``is None`` – whether a packet carries a
  speed at all; ``None`` falls through to position derivation.
* ``wire.x != 0.0 or wire.y != 0.0 or wire.z != 0.0`` – the disjunction
  that promotes a sender to wire-trusted for a tracker. Mutants swapping
  one ``or`` for ``and`` must fail against a packet where exactly one
  component is non-zero.
* ``self._wire_speed_sender.get(tid) == sender`` – verbatim storage vs
  position derivation, and the drop that revokes trust on eviction.
* Position-derived speed delta arithmetic (``new_pos[0] - prev_pos[0]``)
  and the dt-window guard ``0.001 < dt < 1.0``.
* ``self._last_seen[tid] = now`` – ``is_marker_online`` reads this
  field; any mutation that drops the timestamp write must flip
  ``is_marker_online`` to False.
* ``self._last_pos[tid] = new_pos`` – subsequent packets rely on this
  to compute deltas; any mutation skipping the write breaks the next
  speed-derivation attempt.

Equivalent-mutant categories (log-message string edits, `exc_info=True`
vs `False` / None, `recvfrom(1500)` vs `recvfrom(1501)` buffer-size
shifts well above MTU) are documented in ``docs/COVERAGE.md`` §"Surviving
mutants audit log" rather than killed – killing them would force
tautological assertions on log text and implementation constants.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from conftest import psn_packet_clock as _packet_clock

import openfollow.psn.receiver as receiver_module
from openfollow.psn.receiver import PsnReceiver

pytestmark = pytest.mark.unit


@dataclass
class _Vec:
    x: float
    y: float
    z: float


@dataclass
class _PacketTracker:
    """Mirror of ``pypsn.PsnTracker`` – the wire-protocol attribute
    names (``tracker_id``) flow up to ``PsnReceiver`` which then
    translates into our domain's ``Marker`` objects."""

    tracker_id: int
    pos: _Vec | None
    speed: _Vec | None
    timestamp: int = 0
    status: float = 0.0


class _FakeDataPacket:
    def __init__(self, trackers: list[_PacketTracker]) -> None:
        self.trackers = trackers


# --------------------------------------------------------------------------- #
# Marker constructor arg preservation (mutmut_5, mutmut_8)
# --------------------------------------------------------------------------- #


class TestMarkerIdAndNamePreserved:
    """``Marker(t.tracker_id, f"Marker {t.tracker_id}")`` – mutmut
    can swap either arg for ``None``; the receiver then hands back a
    ``Marker`` with a ``None`` field.
    """

    def test_marker_id_is_the_received_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(receiver_module.pypsn, "PsnDataPacket", _FakeDataPacket)
        monkeypatch.setattr(receiver_module.time, "monotonic", lambda: 10.0)
        recv = PsnReceiver()
        recv._on_packet(
            _FakeDataPacket(
                [
                    _PacketTracker(7, _Vec(1.0, 2.0, 3.0), _Vec(0.0, 0.0, 0.0)),
                ]
            )
        )
        marker = recv.get_marker(7)
        assert marker is not None
        # Kills the ``Marker(None, ...)`` mutant that would give a
        # marker whose id is None (or silently crashes downstream).
        assert marker.marker_id == 7

    def test_default_marker_name_follows_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default name is ``f"Marker {marker_id}"``.  Mutants setting
        the name to ``None`` / blank still register a marker but with
        the wrong label – kills the second-arg mutation.
        """
        monkeypatch.setattr(receiver_module.pypsn, "PsnDataPacket", _FakeDataPacket)
        monkeypatch.setattr(receiver_module.time, "monotonic", lambda: 10.0)
        recv = PsnReceiver()
        recv._on_packet(
            _FakeDataPacket(
                [
                    _PacketTracker(42, _Vec(1.0, 2.0, 3.0), _Vec(0.0, 0.0, 0.0)),
                ]
            )
        )
        marker = recv.get_marker(42)
        assert marker is not None
        assert marker.name == "Marker 42"


# --------------------------------------------------------------------------- #
# Wire-speed trust dispatch
# --------------------------------------------------------------------------- #


class TestWireSpeedDispatch:
    """Guards the non-zero disjunction that promotes a sender to
    wire-trusted for a tracker against ``or → and`` swaps, the sender
    comparison that dispatches verbatim storage vs derivation, the drop on
    eviction, and the outer ``is not None`` vs ``is None``.
    """

    @pytest.mark.parametrize(
        "speed_vec",
        [
            # Only X non-zero – ``or`` → ``and`` mutants would miss this
            # because ``0 != 0`` is False; an ``and`` chain needs all three
            # to be non-zero.
            _Vec(3.0, 0.0, 0.0),
            # Only Y non-zero.
            _Vec(0.0, 4.0, 0.0),
            # Only Z non-zero.
            _Vec(0.0, 0.0, 5.0),
            # Two components – kills an axis swap in the verbatim copy.
            _Vec(3.0, 4.0, 0.0),
        ],
    )
    def test_wire_speed_vector_is_stored_verbatim(
        self,
        monkeypatch: pytest.MonkeyPatch,
        speed_vec: _Vec,
    ) -> None:
        monkeypatch.setattr(receiver_module.pypsn, "PsnDataPacket", _FakeDataPacket)
        monkeypatch.setattr(receiver_module.time, "monotonic", lambda: 10.0)
        recv = PsnReceiver()
        recv._on_packet(
            _FakeDataPacket(
                [
                    _PacketTracker(1, _Vec(0.0, 0.0, 0.0), speed_vec),
                ]
            )
        )
        assert recv.get_marker(1).speed == (speed_vec.x, speed_vec.y, speed_vec.z)

    def test_zero_after_nonzero_is_stored_as_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Kills ``==`` → ``!=`` on the sender comparison: a trusted sender's
        zero is stored, never replaced by a value derived from the delta."""
        monkeypatch.setattr(receiver_module.pypsn, "PsnDataPacket", _FakeDataPacket)
        monkeypatch.setattr(receiver_module.time, "monotonic", _packet_clock(1.0, 1.5))
        recv = PsnReceiver()
        recv._on_packet(_FakeDataPacket([_PacketTracker(1, _Vec(0.0, 0.0, 0.0), _Vec(0.0, 2.0, 0.0))]))
        recv._on_packet(_FakeDataPacket([_PacketTracker(1, _Vec(1.0, 0.0, 0.0), _Vec(0.0, 0.0, 0.0))]))
        assert recv.get_marker(1).speed == (0.0, 0.0, 0.0)

    def test_zero_only_sender_still_derives(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Kills an unconditional promotion (trust granted on a zero vector)."""
        monkeypatch.setattr(receiver_module.pypsn, "PsnDataPacket", _FakeDataPacket)
        monkeypatch.setattr(receiver_module.time, "monotonic", _packet_clock(1.0, 1.5))
        recv = PsnReceiver()
        recv._on_packet(_FakeDataPacket([_PacketTracker(1, _Vec(0.0, 0.0, 0.0), _Vec(0.0, 0.0, 0.0))]))
        recv._on_packet(_FakeDataPacket([_PacketTracker(1, _Vec(1.0, 0.0, 0.0), _Vec(0.0, 0.0, 0.0))]))
        assert recv.get_marker(1).speed == pytest.approx((2.0, 0.0, 0.0))

    def test_eviction_forgets_trust(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Kills removal of the trust drop on TTL eviction."""
        monkeypatch.setattr(receiver_module.pypsn, "PsnDataPacket", _FakeDataPacket)
        now = {"t": 0.0}
        monkeypatch.setattr(receiver_module.time, "monotonic", lambda: now["t"])
        recv = PsnReceiver()
        recv._on_packet(_FakeDataPacket([_PacketTracker(1, _Vec(0.0, 0.0, 0.0), _Vec(0.0, 2.0, 0.0))]))
        now["t"] = 100.0
        recv._on_packet(_FakeDataPacket([_PacketTracker(2, _Vec(0.0, 0.0, 0.0), _Vec(0.0, 0.0, 0.0))]))
        assert 1 not in recv._wire_speed_sender

    def test_speed_none_triggers_position_derivation_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Mutant ``wire is not None`` → ``is None`` inverts the guard:
        with ``speed=None``, the original skips the non-zero test and
        derives from the position delta; the mutant would evaluate
        ``.x`` on None → AttributeError → mutant dies.  This test pins
        the **correct** branch (derived speed) fires.
        """
        monkeypatch.setattr(receiver_module.pypsn, "PsnDataPacket", _FakeDataPacket)
        monkeypatch.setattr(receiver_module.time, "monotonic", _packet_clock(1.0, 1.1))
        recv = PsnReceiver()
        # Two packets with None speed → position delta used.
        recv._on_packet(
            _FakeDataPacket(
                [
                    _PacketTracker(3, _Vec(0.0, 0.0, 0.0), None),
                ]
            )
        )
        recv._on_packet(
            _FakeDataPacket(
                [
                    _PacketTracker(3, _Vec(2.0, 0.0, 0.0), None),
                ]
            )
        )
        vx, _vy, _vz = recv.get_marker(3).speed
        # dx = 2.0, dt = 0.1 → vx = 20.0
        assert vx == pytest.approx(20.0)


# --------------------------------------------------------------------------- #
# Position-derived speed math (mutmut_40, _41, _45, _50, _55)
# --------------------------------------------------------------------------- #


class TestPositionDerivedSpeedMath:
    """Pins the per-axis delta arithmetic:

    * ``dx = new_pos[0] - prev_pos[0]`` – any mutation that swaps the
      subtraction for addition or indexes the wrong axis produces a
      different derived velocity.
    * ``if dx != 0 or dy != 0 or dz != 0`` – any mutation that swaps an
      ``or`` for ``==`` / ``and`` short-circuits on the wrong axis.
    """

    def test_nonzero_prev_pos_exposes_subtraction_direction(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Use a non-zero ``prev_pos`` so ``new_pos - prev_pos`` differs
        from ``new_pos + prev_pos``.  The existing suite uses
        ``prev_pos == (0, 0, 0)`` where addition and subtraction are
        identical, hiding the sign mutant.
        """
        monkeypatch.setattr(receiver_module.pypsn, "PsnDataPacket", _FakeDataPacket)
        monkeypatch.setattr(receiver_module.time, "monotonic", _packet_clock(1.0, 1.5))
        recv = PsnReceiver()
        # First packet at (10, 0, 0), second at (12, 0, 0) → dx = +2,
        # dt = 0.5 → vx = 4.0.  If subtraction mutates to addition we
        # would get vx = (12 + 10) / 0.5 = 44.0.
        recv._on_packet(
            _FakeDataPacket(
                [
                    _PacketTracker(9, _Vec(10.0, 0.0, 0.0), _Vec(0.0, 0.0, 0.0)),
                ]
            )
        )
        recv._on_packet(
            _FakeDataPacket(
                [
                    _PacketTracker(9, _Vec(12.0, 0.0, 0.0), _Vec(0.0, 0.0, 0.0)),
                ]
            )
        )
        vx, _vy, _vz = recv.get_marker(9).speed
        assert vx == pytest.approx(4.0)

    def test_all_three_axes_are_independently_derived(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A single packet pair with distinct deltas on every axis pins
        the axis-index correctness of ``dx``/``dy``/``dz``.  Mutants
        swapping ``prev_pos[0]`` for ``prev_pos[1]`` etc. will assign
        the wrong value to ``vx``/``vy``/``vz`` and fail this assertion.
        """
        monkeypatch.setattr(receiver_module.pypsn, "PsnDataPacket", _FakeDataPacket)
        # dt must be strictly inside (0.001, 1.0) for the derivation
        # block to fire; use 0.999.  prev = (1, 2, 3), new = (11, 7, 13)
        # → deltas (10, 5, 10).  Non-zero prev so additive mutants produce
        # different numbers; distinct deltas per axis so index swaps diverge.
        monkeypatch.setattr(receiver_module.time, "monotonic", _packet_clock(0.0, 0.999))
        recv = PsnReceiver()
        recv._on_packet(
            _FakeDataPacket(
                [
                    _PacketTracker(4, _Vec(1.0, 2.0, 3.0), _Vec(0.0, 0.0, 0.0)),
                ]
            )
        )
        recv._on_packet(
            _FakeDataPacket(
                [
                    _PacketTracker(4, _Vec(11.0, 7.0, 13.0), _Vec(0.0, 0.0, 0.0)),
                ]
            )
        )
        vx, vy, vz = recv.get_marker(4).speed
        # Deltas (10, 5, 10) / dt 0.999 ≈ (10.01, 5.005, 10.01).
        assert vx == pytest.approx(10.0 / 0.999, rel=1e-3)
        assert vy == pytest.approx(5.0 / 0.999, rel=1e-3)
        assert vz == pytest.approx(10.0 / 0.999, rel=1e-3)


# --------------------------------------------------------------------------- #
# dt window guard (mutmut_34, _35, _36, _37, _38)
# --------------------------------------------------------------------------- #


class TestDtWindowGuard:
    """``0.001 < dt < 1.0`` – both sides of the window matter.

    Out-of-window values must preserve the marker's last known speed
    (covered by the existing ``test_dt_outside_window_skips_speed_derivation``
    with dt = 2.0).  This test pins the in-window upper bound: a dt
    value that is strictly less than the upper bound (e.g. 0.9) should
    still derive speed, and a mutant that shifts the upper bound down
    to ``< 0.5`` would silently stop deriving speed for reasonable
    frame gaps.
    """

    def test_dt_just_below_upper_bound_still_derives(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(receiver_module.pypsn, "PsnDataPacket", _FakeDataPacket)
        monkeypatch.setattr(
            receiver_module.time, "monotonic", _packet_clock(0.0, 0.9)
        )  # dt = 0.9, inside the (0.001, 1.0) window
        recv = PsnReceiver()
        recv._on_packet(
            _FakeDataPacket(
                [
                    _PacketTracker(2, _Vec(0.0, 0.0, 0.0), _Vec(0.0, 0.0, 0.0)),
                ]
            )
        )
        recv._on_packet(
            _FakeDataPacket(
                [
                    _PacketTracker(2, _Vec(0.9, 0.0, 0.0), _Vec(0.0, 0.0, 0.0)),
                ]
            )
        )
        vx, _vy, _vz = recv.get_marker(2).speed
        # dx = 0.9, dt = 0.9 → vx = 1.0.
        assert vx == pytest.approx(1.0)

    def test_dt_just_above_lower_bound_still_derives(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """dt = 0.01 – comfortably above the 0.001 lower bound.  A
        mutant that raises the lower bound to ``> 0.01`` would skip
        this window.
        """
        monkeypatch.setattr(receiver_module.pypsn, "PsnDataPacket", _FakeDataPacket)
        monkeypatch.setattr(receiver_module.time, "monotonic", _packet_clock(0.0, 0.01))
        recv = PsnReceiver()
        recv._on_packet(
            _FakeDataPacket(
                [
                    _PacketTracker(6, _Vec(0.0, 0.0, 0.0), _Vec(0.0, 0.0, 0.0)),
                ]
            )
        )
        recv._on_packet(
            _FakeDataPacket(
                [
                    _PacketTracker(6, _Vec(0.01, 0.0, 0.0), _Vec(0.0, 0.0, 0.0)),
                ]
            )
        )
        vx, _vy, _vz = recv.get_marker(6).speed
        # dx = 0.01, dt = 0.01 → vx = 1.0.
        assert vx == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# Bookkeeping writes (mutmut_60)
# --------------------------------------------------------------------------- #


class TestPerPacketBookkeeping:
    """``self._last_seen[tid] = now`` and ``self._last_pos[tid] = new_pos``
    are the bookkeeping writes that power ``is_marker_online`` and
    the next speed-derivation.  Mutants that drop them (or overwrite
    with ``None``) silently break downstream behaviour.
    """

    def test_packet_marks_marker_online(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Kills mutants on the ``self._last_seen[tid] = now`` line.
        A clean packet → ``is_marker_online`` returns True within the
        timeout.
        """
        monkeypatch.setattr(receiver_module.pypsn, "PsnDataPacket", _FakeDataPacket)
        monkeypatch.setattr(receiver_module.time, "monotonic", _packet_clock(5.0, 5.1))
        recv = PsnReceiver()
        recv._on_packet(
            _FakeDataPacket(
                [
                    _PacketTracker(1, _Vec(0.0, 0.0, 0.0), None),
                ]
            )
        )
        assert recv.is_marker_online(1, timeout=1.0) is True

    def test_second_packet_uses_first_packet_prev_pos(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Kills mutants on ``self._last_pos[tid] = new_pos``.  If the
        first packet doesn't persist position, the second packet's
        derivation uses ``prev_pos=None`` and skips the speed update.
        """
        monkeypatch.setattr(receiver_module.pypsn, "PsnDataPacket", _FakeDataPacket)
        monkeypatch.setattr(receiver_module.time, "monotonic", _packet_clock(0.0, 0.5))
        recv = PsnReceiver()
        recv._on_packet(
            _FakeDataPacket(
                [
                    _PacketTracker(11, _Vec(1.0, 0.0, 0.0), _Vec(0.0, 0.0, 0.0)),
                ]
            )
        )
        recv._on_packet(
            _FakeDataPacket(
                [
                    _PacketTracker(11, _Vec(3.0, 0.0, 0.0), _Vec(0.0, 0.0, 0.0)),
                ]
            )
        )
        vx, _vy, _vz = recv.get_marker(11).speed
        # dx = 2.0, dt = 0.5 → vx = 4.0.  If ``_last_pos`` never got
        # written, ``prev_pos`` would be None on the second call and
        # the speed would stay at (0, 0, 0) from init.
        assert vx == pytest.approx(4.0)


# --------------------------------------------------------------------------- #
# Default-arg mutants (__init__ source_ip, is_marker_online timeout)
# --------------------------------------------------------------------------- #


class TestDefaultKeywordArguments:
    """Mutmut mutates default values of keyword arguments to random
    alternatives.  Existing tests pass the kwargs explicitly so the
    defaults never fire – this class drops the kwargs to pin the
    defaults themselves.
    """

    def test_default_source_ip_is_empty_string(self) -> None:
        """``source_ip: str = ""`` – an empty default means "listen on
        all interfaces".  Mutants that change the default to a
        concrete string would quietly bind to the wrong NIC.
        """
        recv = PsnReceiver()
        assert recv._source_ip == ""

    def test_default_is_marker_online_timeout_is_two_seconds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``is_marker_online(tid, timeout: float = 2.0)`` – the
        HUD's online / offline dot policy relies on this default
        being 2 s.  A mutant that shifts it to 3 s would delay the
        offline indicator by a second.
        """
        recv = PsnReceiver()
        # Packet received at t=10.0, then check at t=12.5.  Elapsed =
        # 2.5 s → out of window with default timeout 2.0 but inside a
        # 3.0 default.  Without an explicit kwarg, the result must be
        # False under the true default.
        recv._last_seen[1] = 10.0
        monkeypatch.setattr(receiver_module.time, "monotonic", lambda: 12.5)
        assert recv.is_marker_online(1) is False

    def test_is_marker_online_strict_less_than_at_boundary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``(now - last) < timeout`` – ``<`` not ``<=``.  At the
        exact boundary the marker has *just* aged out.  Returning
        True at the boundary would delay the offline dot by one tick.
        """
        recv = PsnReceiver()
        recv._last_seen[1] = 10.0
        monkeypatch.setattr(receiver_module.time, "monotonic", lambda: 12.0)
        # elapsed == 2.0, timeout == 2.0 → strict < fails → offline.
        assert recv.is_marker_online(1, timeout=2.0) is False


# --------------------------------------------------------------------------- #
# Elapsed-time arithmetic under a realistic monotonic clock
# --------------------------------------------------------------------------- #


class TestElapsedTimeIsADifference:
    """``time.monotonic()`` on a box that has been up for a day reads in the
    hundred-thousands, so ``now - seen`` and ``now + seen`` are indistinguishable
    when a test starts its fake clock near zero: both are far past every
    threshold. Drive the clock from a realistic uptime instead, where only the
    difference stays small.
    """

    def test_a_marker_inside_the_ttl_survives_a_sweep(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(receiver_module.pypsn, "PsnDataPacket", _FakeDataPacket)
        now = {"t": 100_000.0}
        monkeypatch.setattr(receiver_module.time, "monotonic", lambda: now["t"])
        recv = PsnReceiver()
        recv._on_packet(
            _FakeDataPacket(
                [
                    _PacketTracker(1, _Vec(0.0, 0.0, 0.0), _Vec(0.0, 0.0, 0.0)),
                    _PacketTracker(2, _Vec(0.0, 0.0, 0.0), _Vec(0.0, 0.0, 0.0)),
                ]
            )
        )

        # 30 s later: long enough for a sweep to run, half the marker TTL.
        now["t"] = 100_030.0
        recv._on_packet(_FakeDataPacket([_PacketTracker(2, _Vec(1.0, 0.0, 0.0), _Vec(0.0, 0.0, 0.0))]))
        assert recv.get_marker(1) is not None

        now["t"] = 100_100.0  # marker 1 has now been silent for 100 s
        recv._on_packet(_FakeDataPacket([_PacketTracker(2, _Vec(2.0, 0.0, 0.0), _Vec(0.0, 0.0, 0.0))]))
        assert recv.get_marker(1) is None

    def test_the_sweep_stays_throttled_on_a_long_running_box(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(receiver_module.pypsn, "PsnDataPacket", _FakeDataPacket)
        now = {"t": 100_000.0}
        monkeypatch.setattr(receiver_module.time, "monotonic", lambda: now["t"])
        recv = PsnReceiver()
        packet = _FakeDataPacket([_PacketTracker(1, _Vec(0.0, 0.0, 0.0), _Vec(0.0, 0.0, 0.0))])
        recv._on_packet(packet)  # first packet sweeps; nothing is stale yet
        recv._last_seen[99] = now["t"] - 1_000.0  # stale, but no sweep is due

        now["t"] = 100_001.0  # 1 s after the last sweep
        recv._on_packet(packet)
        assert 99 in recv._last_seen

        now["t"] = 100_010.0  # 10 s after the last sweep
        recv._on_packet(packet)
        assert 99 not in recv._last_seen


# --------------------------------------------------------------------------- #
# Position-derivation guards
# --------------------------------------------------------------------------- #


class TestDerivationGuards:
    """The ``or`` chain over the three axis deltas and the dt window that gates
    the derivation.
    """

    @pytest.mark.parametrize("axis", [0, 1, 2])
    def test_movement_on_any_single_axis_derives(self, monkeypatch: pytest.MonkeyPatch, axis: int) -> None:
        """Kills an ``or`` → ``and`` swap in ``dx != 0 or dy != 0 or dz != 0``:
        a marker sliding along one axis moves, and the precedence of a single
        swapped operator makes the other two axes read as still."""
        monkeypatch.setattr(receiver_module.pypsn, "PsnDataPacket", _FakeDataPacket)
        monkeypatch.setattr(receiver_module.time, "monotonic", _packet_clock(1.0, 1.5))
        recv = PsnReceiver()
        recv._on_packet(_FakeDataPacket([_PacketTracker(1, _Vec(0.0, 0.0, 0.0), _Vec(0.0, 0.0, 0.0))]))
        moved = [0.0, 0.0, 0.0]
        moved[axis] = 1.0
        recv._on_packet(_FakeDataPacket([_PacketTracker(1, _Vec(*moved), _Vec(0.0, 0.0, 0.0))]))
        expected = [0.0, 0.0, 0.0]
        expected[axis] = 2.0  # 1 m over the 0.5 s between the two packets
        assert recv.get_marker(1).speed == pytest.approx(tuple(expected))

    @pytest.mark.parametrize("dt", [0.001, 1.0, 1.5])
    def test_a_gap_outside_the_window_does_not_derive(self, monkeypatch: pytest.MonkeyPatch, dt: float) -> None:
        """``0.001 < dt < 1.0``, both bounds strict. Below the floor the divisor
        is small enough to turn rounding into metres per second; at or past the
        ceiling the two packets are too far apart to describe one motion, and
        the last known speed stays on the card instead."""
        monkeypatch.setattr(receiver_module.pypsn, "PsnDataPacket", _FakeDataPacket)
        monkeypatch.setattr(receiver_module.time, "monotonic", _packet_clock(1.0, 1.0 + dt))
        recv = PsnReceiver()
        recv._on_packet(_FakeDataPacket([_PacketTracker(1, _Vec(0.0, 0.0, 0.0), _Vec(0.0, 0.0, 0.0))]))
        recv._on_packet(_FakeDataPacket([_PacketTracker(1, _Vec(1.0, 0.0, 0.0), _Vec(0.0, 0.0, 0.0))]))
        assert recv.get_marker(1).speed == (0.0, 0.0, 0.0)


# --------------------------------------------------------------------------- #
# start() wiring
# --------------------------------------------------------------------------- #


class TestStartWiresThePacketHandler:
    def test_the_receive_thread_calls_back_into_packet_handling(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Kills ``callback=self._on_packet`` → ``callback=None``: the socket
        binds and the thread starts either way, so nothing else in the class
        notices that no received packet ever reaches a marker."""
        monkeypatch.setattr(receiver_module.pypsn, "PsnDataPacket", _FakeDataPacket)
        monkeypatch.setattr(receiver_module.time, "monotonic", lambda: 10.0)
        captured: list[object] = []

        class _StubRecvThread:
            def __init__(self, *, callback: object, ip_addr: str, mcast_port: int) -> None:
                captured.append(callback)
                self.daemon = False

            def start(self) -> None:
                pass

        monkeypatch.setattr(receiver_module, "_RobustReceiver", _StubRecvThread)

        recv = PsnReceiver()
        recv.start()

        deliver = captured[0]
        assert callable(deliver)
        deliver(_FakeDataPacket([_PacketTracker(4, _Vec(1.0, 2.0, 3.0), _Vec(0.0, 0.0, 0.0))]), "10.0.0.5")
        marker = recv.get_marker(4)
        assert marker is not None
        assert marker.pos == pytest.approx((1.0, 2.0, 3.0))
