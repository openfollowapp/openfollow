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


_Outline = tuple[tuple[float, float], ...]
_Held = tuple[TriggerZoneConfig, _Outline]


@dataclass
class _Lift:
    value: float = 0.0
    target: float = 0.0
    start: float = 0.0
    elapsed: float = 0.0
    duration: float = 0.0
    # The zone that set the target last, by outline, and its transition time as last seen.
    setter: _Outline = ()
    transition_s: float = 0.0
    # The raising zones that held the marker on the previous step, by outline.
    inside: frozenset[_Outline] = frozenset()


class ZoneHeights:
    """Each marker's lift: the highest ``z_offset`` of the raising zones holding its X/Y, ramped linearly.

    A change of target eases from the current lift over the transition time of
    the zone entered (one holding the marker now and not on the previous step;
    of several, the one setting the target, else the highest), or else of the
    zone that set the previous target; 0 s steps. A zone is known by its
    outline, so one whose outline changes to take the marker in is entered.
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
        outlines = [(zone, tuple((v[0], v[1]) for v in zone.vertices)) for zone in zones]
        lifts: dict[int, float] = {}
        for marker_id, (x, y) in positions.items():
            lift = self._lifts.setdefault(marker_id, _Lift())
            holding = [(zone, outline) for zone, outline in outlines if point_in_polygon(x, y, outline)]
            top = max(holding, key=lambda held: held[0].z_offset) if holding else None
            target = top[0].z_offset if top is not None else 0.0
            if target != lift.target:
                lift.target, lift.start, lift.elapsed = target, lift.value, 0.0
                lift.duration = _transition_s(lift, holding, top)
            if top is not None:
                lift.setter, lift.transition_s = top[1], top[0].height_transition_s
            lift.inside = frozenset(outline for _, outline in holding)
            lifts[marker_id] = _advance(lift, dt)
        return lifts


def _transition_s(lift: _Lift, holding: list[_Held], top: _Held | None) -> float:
    """The time of the zone entered (the one setting the target, else the highest), else of the zone left."""
    entered = [held for held in holding if held[1] not in lift.inside]
    if entered:
        chosen = top if top in entered else max(entered, key=lambda held: held[0].z_offset)
        return chosen[0].height_transition_s
    # The zone left is still here when it was only edited: its time as it is now.
    still = [zone for zone, outline in holding if outline == lift.setter]
    return still[0].height_transition_s if still else lift.transition_s


def _advance(lift: _Lift, dt: float) -> float:
    lift.elapsed += dt
    if lift.duration <= 0.0 or lift.elapsed >= lift.duration:
        lift.value = lift.target
    else:
        lift.value = lift.start + (lift.target - lift.start) * (lift.elapsed / lift.duration)
    return lift.value
