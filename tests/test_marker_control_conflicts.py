# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Which markers more than one station controls, from this station's ids and the peers' beacons."""

from __future__ import annotations

import pytest

from openfollow.marker_catalog.control_conflicts import ControlConflict, find_control_conflicts
from openfollow.marker_catalog.sync import PeerSelection

pytestmark = pytest.mark.unit


def _peer(name: str, controlled: list[int], *, station_id: str = "") -> PeerSelection:
    return PeerSelection(station_id=station_id or f"id-{name}", station_name=name, controlled_ids=controlled)


def test_a_marker_this_station_and_a_peer_control_is_a_conflict() -> None:
    conflicts = find_control_conflicts([3, 4], [_peer("Stage Left", [3])])
    assert conflicts == (ControlConflict(marker_id=3, stations=("Stage Left",), this_station=True),)


def test_a_marker_two_peers_control_is_a_conflict_this_station_is_not_in() -> None:
    conflicts = find_control_conflicts([1], [_peer("Stage Left", [5]), _peer("FOH", [5])])
    assert conflicts == (ControlConflict(marker_id=5, stations=("FOH", "Stage Left"), this_station=False),)


@pytest.mark.parametrize(
    ("controlled", "peers"),
    [
        pytest.param([3], [], id="no-peers"),
        pytest.param([3], [_peer("Stage Left", [4])], id="different-markers"),
        pytest.param([], [_peer("Stage Left", [4])], id="one-peer-alone"),
        pytest.param([], [_peer("Stage Left", [4, 4])], id="a-peer-listing-a-marker-twice"),
        pytest.param([3, 3], [], id="this-station-listing-a-marker-twice"),
    ],
)
def test_a_marker_one_station_controls_is_no_conflict(controlled: list[int], peers: list[PeerSelection]) -> None:
    assert find_control_conflicts(controlled, peers) == ()


def test_a_peer_without_a_name_is_named_by_its_station_id() -> None:
    conflicts = find_control_conflicts([3], [_peer("", [3], station_id="5f0c-station")])
    assert conflicts[0].stations == ("5f0c-station",)


def test_conflicts_come_by_marker_id_with_their_stations_in_name_order() -> None:
    peers = [_peer("stage right", [9, 2]), _peer("FOH", [9]), _peer("Booth", [2])]
    conflicts = find_control_conflicts([9], peers)
    assert [(c.marker_id, c.stations, c.this_station) for c in conflicts] == [
        (2, ("Booth", "stage right"), False),
        (9, ("FOH", "stage right"), True),
    ]


def test_a_conflict_serialises_for_the_stats_api() -> None:
    conflict = ControlConflict(marker_id=3, stations=("FOH", "Stage Left"), this_station=True)
    assert conflict.to_dict() == {"marker_id": 3, "stations": ["FOH", "Stage Left"], "this_station": True}
