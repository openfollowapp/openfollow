# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Michel Honold and Paul Hermann
"""Zone heights: which zones raise markers, and each marker's lift eased across a zone's edge."""

from __future__ import annotations

import pytest

from openfollow.configuration import DEFAULT_SHOWN_FEATURES, AppConfig, TriggerZoneConfig
from openfollow.zones.heights import ZoneHeights, raising_zones

pytestmark = pytest.mark.unit

_INSIDE = (1.0, 1.0)
_OUTSIDE = (5.0, 5.0)


def _zone(z: float = 1.0, transition: float = 0.4, *, size: float = 2.0, **kw: object) -> TriggerZoneConfig:
    square = [[0.0, 0.0], [size, 0.0], [size, size], [0.0, size]]
    return TriggerZoneConfig(vertices=square, z_offset=z, raise_markers=True, height_transition_s=transition, **kw)


def _square_at(x0: float, z: float, *, transition: float) -> TriggerZoneConfig:
    """A 1 m square from ``x0`` along X, at height ``z``."""
    square = [[x0, 0.0], [x0 + 1.0, 0.0], [x0 + 1.0, 1.0], [x0, 1.0]]
    return TriggerZoneConfig(vertices=square, z_offset=z, raise_markers=True, height_transition_s=transition)


def _run(heights: ZoneHeights, at: tuple[float, float], zones: list[TriggerZoneConfig], steps: int, dt: float) -> float:
    lift = 0.0
    for _ in range(steps):
        lift = heights.step({1: at}, zones, dt)[1]
    return lift


class TestRaisingZones:
    def _cfg(self, *zones: TriggerZoneConfig, features: tuple[str, ...] = ("zones", "zone_heights")) -> AppConfig:
        cfg = AppConfig()
        cfg.ui.shown_features = list(features)
        cfg.trigger_zones.zones = list(zones)
        return cfg

    def test_a_raising_zone_counts(self) -> None:
        zone = _zone()
        assert raising_zones(self._cfg(zone)) == [zone]

    @pytest.mark.parametrize(
        "zone",
        [
            TriggerZoneConfig(vertices=[[0, 0], [1, 0], [1, 1]], z_offset=1.0),
            _zone(enabled=False),
            TriggerZoneConfig(vertices=[[0, 0], [1, 0]], z_offset=1.0, raise_markers=True),
        ],
        ids=["marker height off", "zone off", "two corners"],
    )
    def test_a_zone_that_does_not_raise_is_left_out(self, zone: TriggerZoneConfig) -> None:
        assert raising_zones(self._cfg(zone)) == []

    @pytest.mark.parametrize("features", [("zones",), ("zone_heights",), DEFAULT_SHOWN_FEATURES])
    def test_nothing_raises_without_both_features(self, features: tuple[str, ...]) -> None:
        assert raising_zones(self._cfg(_zone(), features=features)) == []


class TestLift:
    def test_a_marker_outside_every_zone_is_not_lifted(self) -> None:
        assert _run(ZoneHeights(), _OUTSIDE, [_zone()], 5, 0.1) == 0.0

    def test_entering_eases_up_over_the_zones_transition(self) -> None:
        heights = ZoneHeights()
        zones = [_zone(1.0, 0.4)]
        assert heights.step({1: _INSIDE}, zones, 0.1)[1] == pytest.approx(0.25)
        assert heights.step({1: _INSIDE}, zones, 0.1)[1] == pytest.approx(0.5)
        assert heights.step({1: _INSIDE}, zones, 0.2)[1] == 1.0
        assert heights.step({1: _INSIDE}, zones, 0.1)[1] == 1.0

    def test_leaving_eases_down_over_the_transition_of_the_zone_left(self) -> None:
        heights = ZoneHeights()
        zones = [_zone(2.0, 0.5)]
        _run(heights, _INSIDE, zones, 1, 1.0)
        assert heights.step({1: _OUTSIDE}, zones, 0.25)[1] == pytest.approx(1.0)
        assert heights.step({1: _OUTSIDE}, zones, 0.25)[1] == 0.0

    @pytest.mark.parametrize("dt", [0.0, 1 / 60])
    def test_a_zero_transition_steps(self, dt: float) -> None:
        heights = ZoneHeights()
        zones = [_zone(1.5, 0.0)]
        assert heights.step({1: _INSIDE}, zones, dt)[1] == 1.5
        assert heights.step({1: _OUTSIDE}, zones, dt)[1] == 0.0

    def test_the_highest_overlapping_zone_wins(self) -> None:
        zones = [_zone(0.5, 0.0), _zone(1.25, 0.0, size=3.0), _zone(0.75, 0.0)]
        assert _run(ZoneHeights(), _INSIDE, zones, 1, 0.1) == 1.25

    def test_a_zone_below_the_stage_lowers(self) -> None:
        assert _run(ZoneHeights(), _INSIDE, [_zone(-0.5, 0.0)], 1, 0.1) == -0.5

    def test_stepping_from_a_high_zone_into_a_neighbouring_low_one_eases_over_the_zone_entered(self) -> None:
        heights = ZoneHeights()
        high = _square_at(0.0, 2.0, transition=0.0)
        low = _square_at(1.0, 1.0, transition=1.0)
        _run(heights, (0.5, 0.5), [high, low], 1, 0.1)
        assert heights.step({1: (1.5, 0.5)}, [high, low], 0.5)[1] == pytest.approx(1.5)

    def test_stepping_out_of_a_high_zone_into_a_low_one_already_holding_it_eases_over_the_zone_left(self) -> None:
        # A small high zone inside a big low one: nothing is entered, the high zone is left.
        heights = ZoneHeights()
        high = _square_at(0.0, 2.0, transition=1.0)
        low = _zone(1.0, 0.0, size=4.0)
        _run(heights, (0.5, 0.5), [high, low], 1, 1.0)
        assert heights.step({1: (3.0, 3.0)}, [high, low], 0.5)[1] == pytest.approx(1.5)

    def test_stepping_into_a_high_zone_inside_a_low_one_eases_over_the_zone_entered(self) -> None:
        heights = ZoneHeights()
        high = _square_at(0.0, 2.0, transition=1.0)
        low = _zone(1.0, 0.0, size=4.0)
        _run(heights, (3.0, 3.0), [high, low], 1, 0.1)
        assert heights.step({1: (0.5, 0.5)}, [high, low], 0.5)[1] == pytest.approx(1.5)

    def test_entering_several_zones_at_once_eases_over_the_one_setting_the_height(self) -> None:
        heights = ZoneHeights()
        low = _zone(1.0, 0.0, size=4.0)
        high = _zone(2.0, 1.0, size=3.0)
        assert heights.step({1: (0.5, 0.5)}, [low, high], 0.5)[1] == pytest.approx(1.0)

    def test_entering_a_lower_zone_while_leaving_the_one_setting_the_height_eases_over_the_zone_entered(self) -> None:
        # Held by a high and a middle zone; one step leaves the high one and enters a low one.
        heights = ZoneHeights()
        high = _square_at(0.0, 3.0, transition=0.0)
        middle = _zone(2.0, 0.0, size=4.0)
        low = _square_at(1.0, 1.0, transition=1.0)
        _run(heights, (0.5, 0.5), [high, middle, low], 1, 0.1)
        assert heights.step({1: (1.5, 0.5)}, [high, middle, low], 0.5)[1] == pytest.approx(2.5)

    def test_of_several_zones_entered_below_the_one_setting_the_height_the_highest_decides(self) -> None:
        heights = ZoneHeights()
        high = _square_at(0.0, 3.0, transition=0.0)
        middle = _zone(2.5, 0.0, size=4.0)
        lower = _square_at(1.0, 1.0, transition=0.0)
        upper = TriggerZoneConfig(
            vertices=[[1, 0], [2, 0], [2, 2], [1, 2]], z_offset=2.0, raise_markers=True, height_transition_s=1.0
        )
        zones = [high, middle, lower, upper]
        _run(heights, (0.5, 0.5), zones, 1, 0.1)
        assert heights.step({1: (1.5, 0.5)}, zones, 0.5)[1] == pytest.approx(2.75)

    def test_neighbouring_zones_at_one_height_do_not_ease(self) -> None:
        heights = ZoneHeights()
        left = _zone(1.0, 0.0)
        right = TriggerZoneConfig(
            vertices=[[2, 0], [4, 0], [4, 2], [2, 2]], z_offset=1.0, raise_markers=True, height_transition_s=5.0
        )
        _run(heights, _INSIDE, [left, right], 1, 0.1)
        assert heights.step({1: (3.0, 1.0)}, [left, right], 0.1)[1] == 1.0

    def test_a_zone_raised_meanwhile_eases_from_where_the_lift_is(self) -> None:
        heights = ZoneHeights()
        _run(heights, _INSIDE, [_zone(1.0, 0.0)], 1, 0.1)
        assert heights.step({1: _INSIDE}, [_zone(3.0, 1.0)], 0.5)[1] == pytest.approx(2.0)

    def test_an_edit_mid_ramp_eases_on_from_the_lift_reached(self) -> None:
        heights = ZoneHeights()
        _run(heights, _INSIDE, [_zone(2.0, 1.0)], 1, 0.5)
        assert heights.step({1: _INSIDE}, [_zone(3.0, 1.0)], 0.5)[1] == pytest.approx(2.0)

    @pytest.mark.parametrize("gone", [[], [_zone(1.0, 0.2, enabled=False)]], ids=["deleted", "disabled"])
    def test_a_zone_removed_mid_ramp_eases_out_over_its_own_transition(self, gone: list[TriggerZoneConfig]) -> None:
        heights = ZoneHeights()
        _run(heights, _INSIDE, [_zone(1.0, 0.2)], 1, 0.1)
        # The caller passes only raising zones; a disabled one is filtered out before.
        zones = [z for z in gone if z.enabled]
        assert heights.step({1: _INSIDE}, zones, 0.1)[1] == pytest.approx(0.25)
        assert heights.step({1: _INSIDE}, zones, 0.1)[1] == 0.0

    def test_the_zone_lefts_transition_is_read_as_it_was_last(self) -> None:
        heights = ZoneHeights()
        _run(heights, _INSIDE, [_zone(1.0, 0.0)], 1, 0.1)
        _run(heights, _INSIDE, [_zone(1.0, 1.0)], 1, 0.1)
        assert heights.step({1: _OUTSIDE}, [], 0.5)[1] == pytest.approx(0.5)

    def test_markers_are_lifted_on_their_own(self) -> None:
        heights = ZoneHeights()
        lifts = heights.step({1: _INSIDE, 2: _OUTSIDE}, [_zone(1.0, 0.0)], 0.1)
        assert lifts == {1: 1.0, 2: 0.0}

    def test_a_marker_that_returns_starts_from_the_floor(self) -> None:
        heights = ZoneHeights()
        _run(heights, _INSIDE, [_zone(1.0, 1.0)], 1, 1.0)
        heights.step({}, [_zone(1.0, 1.0)], 0.1)
        assert heights.step({1: _INSIDE}, [_zone(1.0, 1.0)], 0.5)[1] == pytest.approx(0.5)

    def test_a_negative_step_never_reverses_the_ramp(self) -> None:
        heights = ZoneHeights()
        zones = [_zone(1.0, 1.0)]
        heights.step({1: _INSIDE}, zones, 0.5)
        assert heights.step({1: _INSIDE}, zones, -0.4)[1] == pytest.approx(0.5)
