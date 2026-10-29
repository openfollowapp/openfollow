# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Zone heights: the Z a zone adds to a marker inside it, eased across the zone's edge."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from openfollow.configuration import TriggerZoneConfig, feature_on
from openfollow.zones.geometry import point_in_polygon


def raising_zones(cfg: Any) -> list[TriggerZoneConfig]:
    """The zones that raise markers now: none while Trigger zones or Zone heights is off."""
    if not (feature_on(cfg, "zones") and feature_on(cfg, "zone_heights")):
        return []
    return [zone for zone in cfg.trigger_zones.zones if zone.enabled and zone.raise_markers and len(zone.vertices) >= 3]


@dataclass
class _Lift:
    value: float = 0.0
    target: float = 0.0
    start: float = 0.0
    elapsed: float = 0.0
    duration: float = 0.0
    # The transition time of the zone that set the target last, used when the marker leaves it.
    transition_s: float = 0.0


class ZoneHeights:
    """Each marker's lift: the highest ``z_offset`` of the raising zones holding its X/Y, ramped linearly.

    A change of target eases from the current lift over the transition time of
    the zone that now sets it, or else of the zone that set it before; 0 s steps.
    Timed only by the ``dt`` it is stepped with.
    """

    def __init__(self) -> None:
        self._lifts: dict[int, _Lift] = {}

    def step(
        self, positions: Mapping[int, tuple[float, float]], zones: Sequence[TriggerZoneConfig], dt: float
    ) -> dict[int, float]:
        """Advance every marker in ``positions`` by ``dt`` seconds; markers not in it are forgotten."""
        for marker_id in [m for m in self._lifts if m not in positions]:
            del self._lifts[marker_id]
        dt = max(0.0, dt)
        polygons = [(zone, [(v[0], v[1]) for v in zone.vertices]) for zone in zones]
        lifts: dict[int, float] = {}
        for marker_id, (x, y) in positions.items():
            lift = self._lifts.setdefault(marker_id, _Lift())
            holding = [zone for zone, polygon in polygons if point_in_polygon(x, y, polygon)]
            zone = max(holding, key=lambda z: z.z_offset) if holding else None
            target = zone.z_offset if zone is not None else 0.0
            if target != lift.target:
                lift.target, lift.start, lift.elapsed = target, lift.value, 0.0
                lift.duration = zone.height_transition_s if zone is not None else lift.transition_s
            if zone is not None:
                lift.transition_s = zone.height_transition_s
            lifts[marker_id] = _advance(lift, dt)
        return lifts


def _advance(lift: _Lift, dt: float) -> float:
    lift.elapsed += dt
    if lift.duration <= 0.0 or lift.elapsed >= lift.duration:
        lift.value = lift.target
    else:
        lift.value = lift.start + (lift.target - lift.start) * (lift.elapsed / lift.duration)
    return lift.value
