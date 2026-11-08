# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Michel Honold and Paul Hermann
"""A zone's lift on the wire: every output sends Z plus the lift, and the lift never touches freshness.

The bytes are read, as a receiver would; the OSC transmitter's half lives with
its harness in ``test_osc_transmitter.py``.
"""

from __future__ import annotations

import struct

import pypsn
import pytest
from conftest import PsnStepClock as _StepClock

from openfollow.otp.server import encode_otp_transform_packet
from openfollow.psn.marker import Marker, is_marker_stale, marker_age_s
from openfollow.psn.server import PsnServer
from openfollow.rttrpm.server import RttrpmServer

pytestmark = pytest.mark.unit

_NOW_US = 4_000_000_000


def _lifted(lift: float = 0.75, *, clock: _StepClock | None = None) -> Marker:
    marker = Marker(1, "T1", clock=clock or _StepClock(_NOW_US))
    marker.set_pos(1.0, 2.0, 1.5)
    marker.set_lift(lift)
    return marker


class TestTheMarker:
    def test_sent_pos_adds_the_lift_to_z_only(self) -> None:
        assert _lifted().sent_pos == (1.0, 2.0, 2.25)

    def test_the_own_position_keeps_the_operators_z(self) -> None:
        marker = _lifted()
        assert marker.pos == (1.0, 2.0, 1.5)
        assert marker.lift == 0.75

    def test_a_new_marker_has_no_lift(self) -> None:
        marker = Marker(1, "T1")
        marker.set_pos(0.0, 0.0, 2.0)
        assert marker.sent_pos == (0.0, 0.0, 2.0)

    def test_a_new_position_keeps_the_lift(self) -> None:
        marker = _lifted()
        marker.set_pos(3.0, 4.0, 0.5)
        assert marker.sent_pos == (3.0, 4.0, 1.25)

    def test_setting_the_lift_does_not_stamp(self) -> None:
        clock = _StepClock(_NOW_US)
        marker = _lifted(clock=clock)
        clock.now = _NOW_US + 5_000_000
        marker.set_lift(2.0)
        assert marker.timestamp == _NOW_US
        assert marker_age_s(marker) == 5.0
        assert is_marker_stale(marker)

    def test_setting_the_lift_does_not_mark_an_unwritten_marker_valid(self) -> None:
        marker = Marker(1, "T1")
        marker.set_lift(1.0)
        assert (marker.status, marker.timestamp) == (0.0, 0)

    def test_setting_the_lift_keeps_the_status(self) -> None:
        marker = _lifted()
        marker.set_status(0.4)
        marker.set_lift(0.2)
        assert marker.status == 0.4


class TestEveryOutputSendsTheLiftedZ:
    def test_psn(self, monkeypatch: pytest.MonkeyPatch) -> None:
        clock = _StepClock(_NOW_US)
        server = PsnServer(mcast_ip=None, clock=clock)
        marker = server.add_marker(1, "T1")
        marker.set_pos(1.0, 2.0, 1.5)
        marker.set_lift(0.75)
        sent: list[bytes] = []
        monkeypatch.setattr(server, "_send", lambda data, stop_event=None: sent.append(data))
        server._send_data_packet()
        tracker = pypsn.parse_psn_packet(sent[0]).trackers[0]
        assert (tracker.pos.x, tracker.pos.y, tracker.pos.z) == (1.0, 2.0, 2.25)
        assert tracker.status == 1.0

    def test_psn_tracker_from_the_marker(self) -> None:
        tracker = _lifted().to_psn_marker()
        assert (tracker.pos.x, tracker.pos.y, tracker.pos.z) == (1.0, 2.0, 2.25)

    def test_otp(self) -> None:
        packet = encode_otp_transform_packet(
            cid=b"\x11" * 16,
            component_name="X",
            folio=1,
            system_number=1,
            timestamp_us=0,
            markers=[_lifted()],
            priority=100,
        )
        # The first point's Position Module: options at 127, then X, Y, Z in µm.
        assert struct.unpack("!iii", packet[128:140]) == (1_000_000, 2_000_000, 2_250_000)

    def test_rttrpm(self, monkeypatch: pytest.MonkeyPatch) -> None:
        server = RttrpmServer(host="127.0.0.1", port=24_002)
        server.register_marker(_lifted(clock=_StepClock(_NOW_US)))
        sent: list[bytes] = []
        monkeypatch.setattr(server, "_socket", object())
        monkeypatch.setattr(server, "_send", lambda data, stop_event=None: sent.append(data))
        server._send_packet()
        # One trackable: its centroid module's X, Y, Z doubles end the packet.
        assert struct.unpack("!ddd", sent[0][-24:]) == (1.0, 2.0, 2.25)
