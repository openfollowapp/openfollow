# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Markers more than one station controls.

Every station sends each marker it controls on every output, so a marker two
stations control jumps between two positions at every receiver. The decision
is made here, once per runtime-stats publish, from this station's controlled
ids and the ones each peer advertises in its catalog-sync beacon; the Operator
Screen, the web marker table and the diagnostics bundle all read that result.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from openfollow.marker_catalog.sync import PeerSelection


@dataclass(frozen=True)
class ControlConflict:
    """One marker controlled by more than one station.

    ``stations`` names the peers that control it, sorted; ``this_station`` is
    whether this station is one of the controllers too.
    """

    marker_id: int
    stations: tuple[str, ...]
    this_station: bool

    def to_dict(self) -> dict[str, object]:
        return {"marker_id": self.marker_id, "stations": list(self.stations), "this_station": self.this_station}


def find_control_conflicts(
    controlled_ids: Iterable[int], peers: Iterable[PeerSelection]
) -> tuple[ControlConflict, ...]:
    """Every marker at least two stations control, by marker id."""
    controllers: dict[int, list[str]] = {}
    for peer in peers:
        name = peer.station_name or peer.station_id
        for marker_id in set(peer.controlled_ids):
            controllers.setdefault(marker_id, []).append(name)
    own = set(controlled_ids)
    conflicts: list[ControlConflict] = []
    for marker_id, names in sorted(controllers.items()):
        mine = marker_id in own
        if len(names) + mine > 1:
            conflicts.append(ControlConflict(marker_id, tuple(sorted(names, key=str.casefold)), mine))
    return tuple(conflicts)
