# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The data and info streams each number their own frames.

``frame_id`` is what a receiver groups a multi-packet frame on and spots a
dropped frame with, so a counter shared between the two streams makes the data
stream skip an id once per info packet. The ids are read back off the wire
rather than off the counters, because that is what a receiver sees.
"""

from __future__ import annotations

import pypsn
import pytest

from openfollow.psn.server import PsnServer

pytestmark = pytest.mark.unit


def _capturing_server(monkeypatch: pytest.MonkeyPatch) -> tuple[PsnServer, list[bytes]]:
    """A server whose datagrams are collected instead of sent."""
    server = PsnServer(mcast_ip=None)
    server.add_marker(1, "T1").set_pos(0.0, 0.0, 0.0)
    sent: list[bytes] = []
    monkeypatch.setattr(server, "_send", sent.append)
    return server, sent


def _frame_ids(sent: list[bytes], packet_type: type) -> list[int]:
    parsed = (pypsn.parse_psn_packet(datagram) for datagram in sent)
    return [p.info.frame_id for p in parsed if isinstance(p, packet_type)]


def test_data_frame_ids_stay_consecutive_across_an_interleaved_info_packet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 1 Hz info loop lands between two data frames. A shared counter steals
    the id in between, which a receiver reads as a dropped frame."""
    server, sent = _capturing_server(monkeypatch)

    for _ in range(3):
        server._send_data_packet()
        server._send_info_packet()
        server._send_data_packet()

    assert _frame_ids(sent, pypsn.PsnDataPacket) == [0, 1, 2, 3, 4, 5]


def test_info_frame_ids_stay_consecutive_across_interleaved_data_packets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other side of the same counter: at 60:1 the info stream's ids would
    jump by ~60 between packets."""
    server, sent = _capturing_server(monkeypatch)

    for _ in range(3):
        server._send_info_packet()
        for _ in range(5):
            server._send_data_packet()

    assert _frame_ids(sent, pypsn.PsnInfoPacket) == [0, 1, 2]


@pytest.mark.parametrize(
    ("send_packet", "packet_type"),
    [
        ("_send_data_packet", pypsn.PsnDataPacket),
        ("_send_info_packet", pypsn.PsnInfoPacket),
    ],
)
def test_frame_id_wraps_at_256(monkeypatch: pytest.MonkeyPatch, send_packet: str, packet_type: type) -> None:
    """frame_id is a uint8 on the wire, and each stream wraps on its own."""
    server, sent = _capturing_server(monkeypatch)

    for _ in range(258):
        getattr(server, send_packet)()

    ids = _frame_ids(sent, packet_type)
    assert ids[:3] == [0, 1, 2]
    assert ids[255:] == [255, 0, 1]
