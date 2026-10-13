# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The path engine: locking, sliding, moving on a path, reloads, recording and drawing."""

from __future__ import annotations

import math
import sys
import threading
from types import FrameType
from typing import Any

import pytest

from openfollow.configuration import PathConfig, PathPointConfig, PathsConfig
from openfollow.paths.engine import PathEngine, Press

pytestmark = pytest.mark.unit


def _path(name: str, points: list[tuple[float, float, float]], **kw: object) -> PathConfig:
    return PathConfig(name=name, points=[PathPointConfig(x, y, z, 0.0) for x, y, z in points], **kw)  # type: ignore[arg-type]


# A floor-level cross along y = 0, and a ramp along x = 0: level up to the cross, then rising to 2 m.
CROSS = _path("cross", [(-5, 0, 0), (5, 0, 0)], active=True)
RAMP = _path("Ramp", [(0, -5, 0), (0, 0, 0), (0, 5, 2)], active=True)


def _engine(*paths: PathConfig, slide_time: float = 0.5, enabled: bool = True) -> PathEngine:
    engine = PathEngine()
    engine.reload(PathsConfig(slide_time=slide_time, paths=list(paths)), enabled=enabled)
    return engine


def _landed(engine: PathEngine, marker_id: int = 1) -> tuple[float, float, float]:
    """Run the slide to its end; where the marker landed."""
    out = engine.tick(10.0)
    return out[marker_id]


def _round(p: tuple[float, ...] | None) -> tuple[float, ...] | None:
    return None if p is None else tuple(round(v, 6) for v in p)


class TestPress:
    def test_a_press_slides_the_marker_to_the_nearest_active_path(self) -> None:
        engine = _engine(CROSS)
        assert engine.press(1, (2.0, 1.5, 0.7)) is Press.LOCKED
        assert engine.locked_path(1) == "cross"
        assert _round(_landed(engine)) == (2.0, 0.0, 0.0)

    def test_a_second_press_releases(self) -> None:
        engine = _engine(CROSS)
        engine.press(1, (2.0, 1.5, 0.0))
        assert engine.press(1, (2.0, 0.0, 0.0)) is Press.RELEASED
        assert not engine.is_locked(1)
        assert engine.locked_path(1) is None

    def test_a_press_with_no_active_path_does_nothing(self) -> None:
        engine = _engine(_path("off", [(-5, 0, 0), (5, 0, 0)]))
        assert engine.press(1, (0.0, 0.0, 0.0)) is Press.NONE
        assert not engine.is_locked(1)

    def test_with_paths_switched_off_a_press_does_nothing(self) -> None:
        engine = _engine(CROSS, enabled=False)
        assert engine.press(1, (0.0, 0.0, 0.0)) is Press.NONE

    def test_while_recording_a_press_asks_for_a_point(self) -> None:
        cfg = PathsConfig(paths=[CROSS])
        engine = _engine(CROSS)
        assert engine.record("CROSS", cfg)
        assert engine.recording == "cross"
        assert engine.press(1, (0.0, 0.0, 0.0)) is Press.RECORD
        assert not engine.is_locked(1)

    def test_markers_lock_independently(self) -> None:
        engine = _engine(CROSS, RAMP)
        engine.press(1, (3.0, 0.5, 0.0))
        engine.press(2, (0.2, 4.0, 0.0))
        assert (engine.locked_path(1), engine.locked_path(2)) == ("cross", "Ramp")
        engine.press(1, (3.0, 0.0, 0.0))
        assert engine.is_locked(2)


class TestPull:
    """A Pathfinder press pulls the marker in while it keeps moving, harder until it is on the path."""

    def test_the_pull_starts_where_the_marker_stood_and_grows(self) -> None:
        engine = _engine(CROSS, slide_time=1.0)
        engine.press(1, (2.0, 2.0, 1.0))
        assert _round(engine.tick(0.0)[1]) == (2.0, 2.0, 1.0)
        # Halfway in time is halfway in: the pull eases in and out.
        assert _round(engine.tick(0.5)[1]) == (2.0, 1.0, 0.5)
        # Three quarters of the time pulls more than three quarters of the way.
        assert engine.tick(0.25)[1][1] < 0.5

    def test_the_marker_keeps_its_own_movement_while_it_is_pulled_in(self) -> None:
        engine = _engine(CROSS, slide_time=1.0)
        engine.press(1, (0.0, 2.0, 0.0))
        engine.tick(0.5)
        # Moving 2 m/s along the stage: it goes on moving, now half pulled onto the cross.
        assert _round(engine.move(1, 2.0, 0.0, 0.5)) == (1.0, 1.0, 0.0)
        engine.tick(0.5)
        # Landed on the cross where its movement took it, not where it was pressed.
        assert _round(engine.move(1, 0.0, 0.0, 0.0)) == (1.0, 0.0, 0.0)
        assert engine.locked_path(1) == "cross"

    def test_height_and_osc_carry_on_during_the_pull(self) -> None:
        engine = _engine(CROSS, slide_time=1.0)
        engine.press(1, (0.0, 2.0, 0.0))
        engine.tick(0.5)
        assert _round(engine.move(1, 0.0, 0.0, 0.5, vz=2.0)) == (0.0, 1.0, 0.5)
        assert _round(engine.project(1, 3.0, 2.0)) == (3.0, 1.0, 0.5)

    def test_the_pull_ends_on_the_path_s_height(self) -> None:
        engine = _engine(RAMP)
        engine.press(1, (1.0, 2.5, 3.0))
        assert _round(_landed(engine)) == (0.0, 2.5, 1.0)
        assert engine.tick(0.1) == {}

    def test_the_free_position_is_known_only_while_the_marker_is_pulled_in(self) -> None:
        engine = _engine(CROSS, slide_time=1.0)
        engine.press(1, (2.0, 2.0, 1.0))
        engine.move(1, 1.0, 0.0, 0.5)
        assert engine.free_position(1) == (2.5, 2.0, 1.0)
        engine.tick(1.0)
        assert engine.free_position(1) is None

    def test_a_zero_slide_time_lands_at_once(self) -> None:
        engine = _engine(CROSS, slide_time=0.0)
        engine.press(1, (2.0, 2.0, 0.0))
        assert _round(engine.tick(0.0)[1]) == (2.0, 0.0, 0.0)

    def test_a_press_during_the_pull_releases_where_it_is(self) -> None:
        engine = _engine(CROSS, slide_time=1.0)
        engine.press(1, (2.0, 2.0, 0.0))
        engine.tick(0.5)
        assert engine.press(1, (2.0, 1.0, 0.0)) is Press.RELEASED
        assert engine.tick(0.5) == {}


class TestMove:
    def _locked(self, *paths: PathConfig, at: tuple[float, float, float] = (0.0, 0.0, 0.0)) -> PathEngine:
        engine = _engine(*paths, slide_time=0.0)
        engine.press(1, at)
        engine.tick(0.0)
        return engine

    def test_a_push_moves_the_marker_along_its_path(self) -> None:
        engine = self._locked(CROSS, at=(1.0, 0.0, 0.0))
        assert _round(engine.move(1, 1.0, 1.0, 1.0)) == (2.0, 0.0, 0.0)

    def test_height_follows_the_path(self) -> None:
        engine = self._locked(RAMP, at=(0.0, 0.0, 0.0))
        assert _round(engine.move(1, 0.0, 2.5, 1.0)) == (0.0, 2.5, 1.0)

    def test_an_unlocked_marker_gets_no_answer(self) -> None:
        engine = _engine(CROSS)
        assert engine.move(1, 1.0, 0.0, 1.0) is None
        assert engine.project(1, 0.0, 0.0) is None
        assert engine.free_position(1) is None
        assert engine.constrain(1, 0.0, 0.0) is None

    def test_an_end_that_releases_lets_the_marker_go(self) -> None:
        engine = self._locked(_path("out", [(0, 0, 0), (2, 0, 0)], active=True, release_at_end=True), at=(1, 0, 0))
        assert _round(engine.move(1, 1.0, 0.0, 5.0)) == (2.0, 0.0, 0.0)
        assert not engine.is_locked(1)

    def test_an_absolute_position_past_an_end_that_releases_stops_there(self) -> None:
        """Only a push lets go at such an end: OSC and Fully Automatic keep the marker on the path."""
        engine = self._locked(_path("out", [(0, 0, 0), (2, 0, 0)], active=True, release_at_end=True), at=(1, 0, 0))
        assert _round(engine.project(1, 9.0, 0.0)) == (2.0, 0.0, 0.0)
        assert engine.is_locked(1)

    def test_an_absolute_position_moves_along_the_path_and_onto_another_at_a_junction(self) -> None:
        apart = _path("apart", [(20, 0, 0), (30, 0, 0)], active=True)
        engine = self._locked(CROSS, RAMP, apart, at=(-3.0, 0.0, 0.0))
        # Far up the ramp: the nearest point of the cross.
        assert _round(engine.project(1, 0.3, 4.0)) == (0.3, 0.0, 0.0)
        # Beside the crossing: onto the ramp, its height easing up from the cross's.
        assert _round(engine.project(1, 0.0, 0.15)) == (0.0, 0.15, 0.0)
        assert engine.locked_path(1) == "Ramp"
        # Along the ramp from there.
        assert _round(engine.project(1, 0.3, 4.0)) == (0.0, 4.0, 1.54)
        assert _round(engine.tick(0.5)[1]) == (0.0, 4.0, 1.6)
        # The separate path is nearer to (19, 0) but cannot be reached from here.
        assert _round(engine.project(1, 19.0, 0.0)) == (0.0, 0.0, 0.0)


def _follow(engine: PathEngine, samples: list[tuple[float, float]]) -> list[tuple[str | None, tuple[float, ...]]]:
    """Project each sampled position in turn, 15 a second; the path and position of each answer."""
    out = []
    for x, y in samples:
        at = engine.project(1, x, y)
        assert at is not None
        engine.tick(1 / 15)
        out.append((engine.locked_path(1), at))
    return out


class TestPositionsSwitchOnlyAtJunctions:
    """A position moves the lock along its path, and onto another path only near a junction with it."""

    def _locked(self, *paths: PathConfig, at: tuple[float, float, float]) -> PathEngine:
        engine = _engine(*paths, slide_time=0.0)
        engine.press(1, at)
        engine.tick(0.0)
        return engine

    def test_a_bridge_overhead_joined_only_through_a_third_path_is_never_taken(self) -> None:
        floor = _path("floor", [(-5, 0, 0), (5, 0, 0)], active=True)
        bridge = _path("bridge", [(0, -5, 2), (0, 5, 2)], active=True)
        ramp = _path("ramp", [(5, 0, 0), (0, 5, 2)], active=True)
        engine = self._locked(bridge, floor, ramp, at=(-1.0, 0.0, 0.0))
        walked = _follow(engine, [(k / 20, 0.04) for k in range(-10, 13)])
        assert {name for name, _ in walked} == {"floor"}
        assert all(at[1:] == (0.0, 0.0) for _, at in walked)

    def test_a_path_crossing_over_its_own_junction_s_path_is_not_taken_at_the_crossing(self) -> None:
        floor = _path("floor", [(-5, 0, 0), (5, 0, 0)], active=True)
        # Leaves the floor at (4, 0), climbs, and crosses back over it at (0, 0), 2 m up.
        over = _path("over", [(4, 0, 0), (4, 3, 1), (0, 3, 2), (0, -3, 2)], active=True)
        engine = self._locked(floor, over, at=(-1.0, 0.0, 0.0))
        assert {name for name, _ in _follow(engine, [(k / 20, 0.04) for k in range(-10, 11)])} == {"floor"}
        engine = self._locked(floor, over, at=(0.0, 1.0, 2.0))
        walked = _follow(engine, [(0.04, 1.0 - k / 10) for k in range(21)])
        assert {name for name, _ in walked} == {"over"}
        assert {at[2] for _, at in walked} == {2.0}

    def test_a_path_passing_back_over_its_junction_higher_up_is_not_taken_there(self) -> None:
        floor = _path("floor", [(-5, 0, 0), (5, 0, 0)], active=True)
        # Leaves the floor at (0, 0), climbs, and passes back over it 15 cm on, 2 m up.
        loop = _path("loop", [(0, 0, 0), (0, 3, 1), (0.15, 3, 2), (0.15, -3, 2)], active=True)
        engine = self._locked(floor, loop, at=(-1.0, 0.0, 0.0))
        assert _round(engine.project(1, 0.15, 0.04)) == (0.15, 0.0, 0.0)
        assert engine.locked_path(1) == "floor"

    def test_turning_onto_a_branch_at_its_junction_switches_without_a_jump(self) -> None:
        main = _path("main", [(-5, 0, 0), (5, 0, 0)], active=True)
        branch = _path("branch", [(2, 0, 0), (2, 5, 0)], active=True)
        engine = self._locked(main, branch, at=(0.0, 0.0, 0.0))
        person = [(k / 10, 0.0) for k in range(21)] + [(2.0, k / 10) for k in range(1, 15)]
        walked = _follow(engine, person)
        assert [name for name, _ in walked] == ["main"] * 21 + ["branch"] * 14
        assert [c for _, at in walked for c in at[:2]] == pytest.approx([c for xy in person for c in xy])

    @pytest.mark.parametrize("degrees", [20, 25, 30])
    @pytest.mark.parametrize("phase", [0.01, 0.05, 0.09])
    def test_an_oblique_branch_is_taken_at_the_first_sample_on_it(self, degrees: int, phase: float) -> None:
        a = math.radians(degrees)
        main = _path("main", [(-5, 0, 0), (5, 0, 0)], active=True)
        branch = _path("branch", [(0, 0, 0), (5 * math.cos(a), 5 * math.sin(a), 0)], active=True)
        engine = self._locked(main, branch, at=(-1.0, 0.0, 0.0))
        # About 10 cm a sample at 15 a second, along the main path and on along the branch.
        steps = [phase - 1.0 + k / 10 for k in range(25)]
        person = [(u, 0.0) if u <= 0.0 else (u * math.cos(a), u * math.sin(a)) for u in steps]
        walked = _follow(engine, person)
        assert [name for name, _ in walked] == ["main" if u <= 0.0 else "branch" for u in steps]
        assert [c for _, at in walked for c in at[:2]] == pytest.approx([c for xy in person for c in xy])

    def test_a_position_far_along_another_path_lands_on_this_path_s_nearest_point(self) -> None:
        main = _path("main", [(-5, 0, 0), (5, 0, 0)], active=True)
        square = _path("square", [(2, 0, 0), (2, 5, 0)], active=True)
        oblique = _path("oblique", [(-2, 0, 0), (-4, 4, 0)], active=True)
        engine = self._locked(main, square, oblique, at=(-3.0, 0.0, 0.0))
        assert _round(engine.project(1, 2.0, 4.5)) == (2.0, 0.0, 0.0)
        assert _round(engine.project(1, -3.9, 3.8)) == (-3.9, 0.0, 0.0)
        assert engine.locked_path(1) == "main"

    def test_a_position_far_from_every_junction_stays_on_this_path_beside_a_short_spur(self) -> None:
        main = _path("main", [(-5, 0, 0), (5, 0, 0)], active=True)
        spur = _path("spur", [(0, 0, 0), (0, 0.1, 0)], active=True)
        engine = self._locked(main, spur, at=(-1.0, 0.0, 0.0))
        # Nearer the spur's end than the main path, but 2 m from where they meet.
        assert _round(engine.project(1, 0.0, 2.0)) == (0.0, 0.0, 0.0)
        assert engine.locked_path(1) == "main"

    def test_of_two_paths_at_a_junction_the_nearer_one_is_taken(self) -> None:
        main = _path("main", [(-5, 0, 0), (5, 0, 0)], active=True)
        upright = _path("upright", [(0, 0, 0), (0, 5, 0)], active=True)
        a = math.radians(30)
        slant = _path("slant", [(0, 0, 0), (5 * math.cos(a), 5 * math.sin(a), 0)], active=True)
        engine = self._locked(main, upright, slant, at=(-1.0, 0.0, 0.0))
        # Nearer both than the main path, and nearest the upright one.
        assert _round(engine.project(1, 0.02, 0.15)) == (0.0, 0.15, 0.0)
        assert engine.locked_path(1) == "upright"

    def test_switching_at_a_junction_with_a_height_step_eases_the_height(self) -> None:
        low = _path("low", [(-5, 0, 0), (5, 0, 0)], active=True)
        high = _path("high", [(0, -5, 0.08), (0, 5, 0.08)], active=True)
        engine = self._locked(low, high, at=(-1.0, 0.0, 0.0))
        assert _round(engine.project(1, 0.0, 0.1)) == (0.0, 0.1, 0.0)
        assert engine.locked_path(1) == "high"
        assert engine.tick(0.25)[1][2] == pytest.approx(0.04)
        assert engine.tick(0.25)[1][2] == pytest.approx(0.08)


class TestConstrain:
    """A second position following a locked marker is put on its path as ``project`` would, the lock left alone."""

    def _locked(self, *paths: PathConfig, at: tuple[float, float, float]) -> PathEngine:
        engine = _engine(*paths, slide_time=0.0)
        engine.press(1, at)
        engine.tick(0.0)
        return engine

    def test_a_position_lands_where_project_would_put_it(self) -> None:
        apart = _path("apart", [(20, 0, 0), (30, 0, 0)], active=True)
        engine = self._locked(CROSS, RAMP, apart, at=(-3.0, 0.0, 0.0))
        assert _round(engine.constrain(1, 3.0, 2.0)) == (3.0, 0.0, 0.0)
        assert _round(engine.constrain(1, 0.3, 4.0)) == (0.3, 0.0, 0.0)
        assert _round(engine.constrain(1, 0.0, 0.15)) == (0.0, 0.15, 0.06)
        assert _round(engine.constrain(1, 19.0, 0.0)) == (5.0, 0.0, 0.0)
        # The marker is where it was, on the path it was on.
        assert _round(engine.move(1, 0.0, 0.0, 0.0)) == (-3.0, 0.0, 0.0)
        assert engine.locked_path(1) == "cross"

    def test_it_shares_what_is_left_of_the_marker_s_height_switch(self) -> None:
        engine = self._locked(CROSS, RAMP, at=(-3.0, 0.0, 0.0))
        engine.take_over(1, (0.0, 3.0, 0.7))
        # The ramp is 1.2 m up at y = 3, and the marker's 0.5 m step up has not begun to ease.
        assert _round(engine.constrain(1, 0.0, 3.0)) == (0.0, 3.0, 0.7)
        engine.tick(0.25)
        assert _round(engine.constrain(1, 0.0, 3.0)) == (0.0, 3.0, 0.95)

    def test_during_the_pull_it_is_pulled_in_as_far_as_the_marker(self) -> None:
        engine = _engine(CROSS, slide_time=1.0)
        engine.press(1, (0.0, 2.0, 1.0))
        engine.tick(0.5)
        # Half pulled in: half way from (3, 2) at the marker's free height to (3, 0) on the cross.
        assert _round(engine.constrain(1, 3.0, 2.0)) == (3.0, 1.0, 0.5)
        assert _round(engine.tick(0.0)[1]) == (0.0, 1.0, 0.5)


class TestTakeOver:
    """Another position takes a locked marker over: the lock moves to it, and nothing jumps."""

    def test_a_landed_lock_moves_to_the_reachable_point_nearest_it(self) -> None:
        engine = _engine(CROSS, RAMP, slide_time=0.0)
        engine.press(1, (0.0, 4.0, 1.6))
        engine.tick(0.0)
        # Onto the cross, its height eased out in full.
        engine.project(1, -0.1, 0.0)
        engine.tick(10.0)
        engine.take_over(1, (0.0, 3.0, 0.7))
        assert engine.locked_path(1) == "Ramp"
        # Written from where it was taken over, the height easing to the ramp's 1.2 m.
        assert _round(engine.move(1, 0.0, 0.0, 0.0)) == (0.0, 3.0, 0.7)
        assert _round(engine.tick(10.0)[1]) == (0.0, 3.0, 1.2)

    def test_a_lock_being_pulled_in_is_pulled_in_afresh_from_it(self) -> None:
        engine = _engine(CROSS, slide_time=1.0)
        engine.press(1, (0.0, 2.0, 0.0))
        engine.tick(0.5)
        engine.take_over(1, (3.0, 1.5, 0.4))
        assert _round(engine.tick(0.0)[1]) == (3.0, 1.5, 0.4)
        assert _round(engine.tick(0.5)[1]) == (3.0, 0.75, 0.2)
        assert _round(engine.tick(0.5)[1]) == (3.0, 0.0, 0.0)

    def test_an_unlocked_marker_is_left_alone(self) -> None:
        engine = _engine(CROSS)
        engine.take_over(1, (0.0, 1.0, 0.0))
        assert not engine.is_locked(1)


class TestLockOnto:
    def test_a_marker_locks_onto_a_named_path_ignoring_case(self) -> None:
        engine = _engine(CROSS, RAMP)
        assert engine.lock_onto(1, "ramp", (3.0, 1.0, 0.0))
        assert engine.locked_path(1) == "Ramp"
        assert _round(_landed(engine)) == (0.0, 1.0, 0.4)

    def test_locking_again_onto_the_path_it_is_on_leaves_a_landed_marker_on_it(self) -> None:
        engine = _engine(CROSS, RAMP)
        engine.lock_onto(1, "cross", (3.0, 1.0, 0.0))
        landed = _landed(engine)
        # A desk resending the lock, in any case, for the marker standing where it landed.
        assert engine.lock_onto(1, "CROSS", landed)
        assert engine.free_position(1) is None
        # A push off the path does not move it off.
        assert _round(engine.move(1, 0.0, 1.0, 0.1)) == (3.0, 0.0, 0.0)

    def test_locking_onto_another_path_pulls_the_marker_onto_that_one(self) -> None:
        engine = _engine(CROSS, RAMP)
        engine.lock_onto(1, "cross", (3.0, 1.0, 0.0))
        landed = _landed(engine)
        assert engine.lock_onto(1, "ramp", landed)
        assert engine.free_position(1) == landed
        assert _round(_landed(engine)) == (0.0, 0.0, 0.0)

    @pytest.mark.parametrize("name", ["nope", "off"])
    def test_only_an_active_path_can_be_locked_onto(self, name: str) -> None:
        engine = _engine(CROSS, _path("off", [(0, 0, 0), (1, 0, 0)]))
        assert not engine.lock_onto(1, name, (0.0, 0.0, 0.0))
        assert not engine.is_locked(1)

    def test_with_paths_switched_off_nothing_locks(self) -> None:
        assert not _engine(CROSS, enabled=False).lock_onto(1, "cross", (0.0, 0.0, 0.0))

    def test_release_and_release_all_but_some(self) -> None:
        engine = _engine(CROSS)
        for marker_id in (1, 2, 3):
            engine.press(marker_id, (0.0, 1.0, 0.0))
        engine.release(2)
        engine.release(9)
        engine.release_all(keep=[3])
        assert [engine.is_locked(m) for m in (1, 2, 3)] == [False, False, True]


class TestReload:
    def test_deactivating_a_path_releases_its_markers(self) -> None:
        engine = _engine(CROSS, RAMP)
        engine.press(1, (3.0, 0.2, 0.0))
        engine.press(2, (0.2, 3.0, 0.0))
        off = _path("cross", [(-5, 0, 0), (5, 0, 0)])
        engine.reload(PathsConfig(paths=[off, RAMP]), enabled=True)
        assert not engine.is_locked(1)
        assert engine.is_locked(2)

    def test_an_edit_puts_a_locked_marker_back_on_the_edited_path(self) -> None:
        engine = _engine(CROSS, slide_time=0.0)
        engine.press(1, (3.0, 0.0, 0.0))
        engine.tick(0.0)
        moved = _path("cross", [(-5, 1, 0.5), (5, 1, 0.5)], active=True)
        engine.reload(PathsConfig(paths=[moved]), enabled=True)
        assert _round(engine.tick(0.02)[1]) == (3.0, 1.0, 0.5)
        # Written once: the next frame leaves it to the marker's input.
        assert engine.tick(0.02) == {}

    def test_an_edit_during_the_pull_retargets_it(self) -> None:
        engine = _engine(CROSS, slide_time=1.0)
        engine.press(1, (3.0, 2.0, 0.0))
        moved = _path("cross", [(-5, 1, 0), (5, 1, 0)], active=True)
        engine.reload(PathsConfig(slide_time=1.0, paths=[moved]), enabled=True)
        assert _round(_landed(engine)) == (3.0, 1.0, 0.0)

    def test_an_edit_during_the_pull_aims_from_where_the_marker_would_be(self) -> None:
        engine = _engine(CROSS, slide_time=1.0)
        engine.press(1, (3.0, 2.0, 0.0))
        # The cross now runs up the stage at x = 4: nearest to (3, 2), not to the old target (3, 0).
        turned = _path("cross", [(4, -5, 0), (4, 5, 0)], active=True)
        engine.reload(PathsConfig(slide_time=1.0, paths=[turned]), enabled=True)
        assert _round(_landed(engine)) == (4.0, 2.0, 0.0)

    def test_switching_paths_off_releases_everyone_and_stops_recording(self) -> None:
        cfg = PathsConfig(paths=[CROSS])
        engine = _engine(CROSS)
        engine.press(1, (0.0, 1.0, 0.0))
        engine.record("cross", cfg)
        engine.reload(cfg, enabled=False)
        assert not engine.enabled
        assert not engine.is_locked(1)
        assert engine.recording is None
        assert engine.drawings(cfg) == []

    def test_deleting_the_recorded_path_stops_recording(self) -> None:
        cfg = PathsConfig(paths=[CROSS, RAMP])
        engine = _engine(CROSS, RAMP)
        engine.record("ramp", cfg)
        engine.reload(PathsConfig(paths=[CROSS]), enabled=True)
        assert engine.recording is None

    def test_a_path_with_fewer_than_two_points_is_never_active(self) -> None:
        engine = _engine(_path("dot", [(1, 1, 0)], active=True))
        assert engine.press(1, (1.0, 1.0, 0.0)) is Press.NONE


class TestRecord:
    def test_recording_needs_a_path_that_exists_and_paths_on(self) -> None:
        cfg = PathsConfig(paths=[CROSS])
        assert not _engine(CROSS).record("nope", cfg)
        assert not _engine(CROSS, enabled=False).record("cross", cfg)

    def test_none_stops_recording(self) -> None:
        cfg = PathsConfig(paths=[CROSS])
        engine = _engine(CROSS)
        engine.record("cross", cfg)
        assert engine.record(None, cfg)
        assert engine.recording is None


class TestDrawings:
    def test_every_active_path_is_drawn_white_while_a_marker_is_on_it(self) -> None:
        cfg = PathsConfig(paths=[CROSS, RAMP, _path("off", [(0, 0, 0), (1, 0, 0)])])
        engine = PathEngine()
        engine.reload(cfg, enabled=True)
        engine.press(1, (0.2, 3.0, 0.0))
        drawn = {d.name: d for d in engine.drawings(cfg)}
        assert set(drawn) == {"cross", "Ramp"}
        assert (drawn["cross"].locked, drawn["Ramp"].locked) == (False, True)
        # The two ends are marked; the middle point is a corner, which shows as the curve.
        assert [_round(e.at) for e in drawn["Ramp"].ends] == [(0.0, -5.0, 0.0), (0.0, 5.0, 2.0)]

    def test_each_end_knows_which_way_is_out_and_whether_it_releases(self) -> None:
        line = _path("line", [(0, 0, 0), (4, 0, 0)], active=True, release_at_end=True)
        [start, end] = _engine(line).drawings(PathsConfig(paths=[line]))[0].ends
        assert (_round(start.at), _round(start.inside), start.releases) == ((0.0, 0.0, 0.0), (0.5, 0.0, 0.0), False)
        assert (_round(end.at), _round(end.inside), end.releases) == ((4.0, 0.0, 0.0), (3.5, 0.0, 0.0), True)

    def test_a_short_path_looks_no_further_in_than_its_middle(self) -> None:
        stub = _path("stub", [(0, 0, 0), (0.4, 0, 0)], active=True)
        [start, _end] = _engine(stub).drawings(PathsConfig(paths=[stub]))[0].ends
        assert _round(start.inside) == (0.2, 0.0, 0.0)

    def test_a_closed_path_has_no_ends(self) -> None:
        ring = _path("ring", [(0, 0, 0), (2, 0, 0), (2, 2, 0)], active=True, closed=True)
        cfg = PathsConfig(paths=[ring])
        assert _engine(ring).drawings(cfg)[0].ends == []

    def test_an_end_carried_on_to_a_corner_is_drawn_to_the_corner_arc(self) -> None:
        corner = _path("corner", [(0, 0, 0), (3, 0, 0), (3, 3, 0)], active=True)
        corner.points[1].radius = 0.5
        branch = _path("branch", [(5, -2, 0), (3, 0, 0)], active=True)
        cfg = PathsConfig(paths=[corner, branch])
        drawn = {d.name: d for d in _engine(corner, branch).drawings(cfg)}
        # The 0.5 m arc's middle (sampled, so within a few millimetres), where the walked curve now ends.
        vertex = (2.5 + 0.5 / math.sqrt(2), 0.5 - 0.5 / math.sqrt(2), 0.0)
        assert math.dist(drawn["branch"].curve[-1], vertex) < 0.005
        assert math.dist(drawn["branch"].ends[-1].at, vertex) < 0.005

    def test_a_long_straight_run_is_drawn_in_short_pieces(self) -> None:
        drawn = _engine(CROSS).drawings(PathsConfig(paths=[CROSS]))[0]
        steps = [math.dist(a, b) for a, b in zip(drawn.curve, drawn.curve[1:], strict=False)]
        assert max(steps) <= 0.25 + 1e-9
        assert drawn.curve[0] == (-5.0, 0.0, 0.0)
        assert drawn.curve[-1] == (5.0, 0.0, 0.0)

    def test_the_path_being_recorded_is_drawn_even_while_inactive(self) -> None:
        draft = _path("draft", [(0, 0, 0), (1, 0, 0)])
        cfg = PathsConfig(paths=[draft])
        engine = _engine(draft)
        engine.record("draft", cfg)
        first = engine.drawings(cfg)
        assert [(d.name, d.locked) for d in first] == [("draft", False)]
        # Unchanged, it is not built again.
        assert engine.drawings(cfg)[0] is first[0]
        draft.points.append(PathPointConfig(1, 1, 0))
        assert engine.drawings(cfg)[0] is not first[0]

    def test_a_recorded_path_of_one_point_is_drawn_as_that_point(self) -> None:
        draft = _path("draft", [(2, 3, 0)])
        cfg = PathsConfig(paths=[draft])
        engine = _engine(draft)
        engine.record("draft", cfg)
        [drawn] = engine.drawings(cfg)
        assert drawn.curve == [(2.0, 3.0, 0.0)]
        assert [(e.at, e.releases) for e in drawn.ends] == [((2.0, 3.0, 0.0), False)]

    def test_a_recorded_path_with_no_points_has_nothing_to_mark(self) -> None:
        draft = _path("draft", [])
        cfg = PathsConfig(paths=[draft])
        engine = _engine(draft)
        engine.record("draft", cfg)
        assert engine.drawings(cfg)[0].ends == []

    def test_a_recorded_active_path_is_drawn_once(self) -> None:
        cfg = PathsConfig(paths=[CROSS])
        engine = _engine(CROSS)
        engine.record("cross", cfg)
        assert [d.name for d in engine.drawings(cfg)] == ["cross"]


class TestSwitchingPaths:
    """Through a junction onto another path: the lock follows, and a height step eases."""

    def _crossing(self, height: float) -> tuple[PathEngine, PathsConfig]:
        low = _path("low", [(-5, 0, 0), (5, 0, 0)], active=True)
        high = _path("high", [(0, -5, height), (0, 5, height)], active=True)
        cfg = PathsConfig(slide_time=0.0, paths=[low, high])
        engine = PathEngine()
        engine.reload(cfg, enabled=True)
        engine.press(1, (-1.0, 0.0, 0.0))
        engine.tick(0.0)
        return engine, cfg

    def test_the_lock_and_the_highlight_follow_the_marker_onto_the_other_path(self) -> None:
        engine, cfg = self._crossing(0.0)
        engine.move(1, 0.5, 1.0, 3.0)
        assert engine.locked_path(1) == "high"
        assert {d.name: d.locked for d in engine.drawings(cfg)} == {"low": False, "high": True}

    def test_a_height_step_at_a_junction_eases_over_half_a_second(self) -> None:
        engine, _ = self._crossing(0.08)
        # Onto the higher path: the height starts where the marker was.
        assert engine.move(1, 0.5, 1.0, 3.0)[2] == pytest.approx(0.0)
        assert engine.tick(0.25)[1][2] == pytest.approx(0.04)
        assert engine.tick(0.25)[1][2] == pytest.approx(0.08)
        # Done: nothing more for the engine to write, and moves carry the path's height.
        assert engine.tick(0.1) == {}
        assert engine.move(1, 0.0, 1.0, 0.1)[2] == pytest.approx(0.08)

    def test_a_switch_during_an_ease_starts_from_where_the_height_is(self) -> None:
        engine, _ = self._crossing(0.08)
        engine.move(1, 0.5, 1.0, 3.0)
        mid = engine.tick(0.25)[1][2]
        # Straight back down onto the lower path through the same crossing.
        assert engine.move(1, -0.5, -1.0, 0.0001)[2] == pytest.approx(mid, abs=1e-3)


class TestRename:
    @staticmethod
    def _renamed(name: str, *others: PathConfig) -> PathsConfig:
        return PathsConfig(paths=[_path(name, [(-5, 0, 0), (5, 0, 0)], active=True), *others])

    def _locked_and_recording(self) -> PathEngine:
        engine = _engine(CROSS)
        engine.press(1, (3.0, 0.2, 0.0))
        engine.record("cross", PathsConfig(paths=[CROSS]))
        return engine

    def test_a_staged_rename_keeps_the_markers_on_the_path_and_its_recording(self) -> None:
        engine = self._locked_and_recording()
        engine.stage_rename("cross", "DownStage")
        engine.reload(self._renamed("DownStage"), enabled=True)
        assert (engine.locked_path(1), engine.recording) == ("DownStage", "DownStage")
        assert engine.move(1, 1.0, 0.0, 0.1) is not None

    def test_a_rename_nobody_staged_releases_them(self) -> None:
        engine = self._locked_and_recording()
        engine.reload(self._renamed("DownStage"), enabled=True)
        assert (engine.is_locked(1), engine.recording) == (False, None)

    def test_a_reload_before_the_rename_is_saved_keeps_it_for_the_next(self) -> None:
        engine = self._locked_and_recording()
        engine.stage_rename("cross", "DownStage")
        engine.reload(PathsConfig(paths=[CROSS]), enabled=True)
        assert (engine.locked_path(1), engine.recording) == ("cross", "cross")
        engine.reload(self._renamed("DownStage"), enabled=True)
        assert (engine.locked_path(1), engine.recording) == ("DownStage", "DownStage")

    def test_two_renames_before_the_reload_land_on_the_last_name(self) -> None:
        loop = _path("loop", [(0, -5, 0), (0, 5, 0)], active=True)
        engine = _engine(CROSS, loop)
        engine.press(1, (3.0, 0.2, 0.0))
        engine.press(2, (0.2, 3.0, 0.0))
        engine.stage_rename("loop", "arc")  # another path's rename, not part of this one's
        engine.stage_rename("cross", "a")
        engine.stage_rename("A", "b")
        engine.reload(self._renamed("b", _path("arc", [(0, -5, 0), (0, 5, 0)], active=True)), enabled=True)
        assert (engine.locked_path(1), engine.locked_path(2)) == ("b", "arc")

    def test_a_change_of_case_carries_the_new_spelling(self) -> None:
        engine = self._locked_and_recording()
        engine.stage_rename("cross", "Cross")
        engine.reload(self._renamed("Cross"), enabled=True)
        assert (engine.locked_path(1), engine.recording) == ("Cross", "Cross")

    def test_a_rename_whose_old_name_is_still_there_moves_nothing_and_is_used_up(self) -> None:
        engine = self._locked_and_recording()
        engine.stage_rename("cross", "ds")
        # The rename was lost and a path called ds was made beside cross.
        engine.reload(PathsConfig(paths=[CROSS, _path("ds", [(0, -5, 0), (0, 5, 0)], active=True)]), enabled=True)
        assert (engine.locked_path(1), engine.recording) == ("cross", "cross")
        engine.reload(self._renamed("ds"), enabled=True)
        assert (engine.is_locked(1), engine.recording) == (False, None)

    def test_names_match_ignoring_case(self) -> None:
        engine = _engine(RAMP)
        engine.press(2, (0.2, 3.0, 0.0))
        engine.record("ramp", PathsConfig(paths=[RAMP]))
        engine.stage_rename("RAMP", "Slope")
        slope = _path("Slope", [(0, -5, 0), (0, 0, 0), (0, 5, 2)], active=True)
        engine.reload(PathsConfig(paths=[slope]), enabled=True)
        assert (engine.locked_path(2), engine.recording) == ("Slope", "Slope")


class TestRenamesThatReuseANameBeforeTheReload:
    # Two parallel paths 6 m apart, with no junction: a marker carried to the wrong one jumps 6 m.
    DS = [(-5, 0, 0), (5, 0, 0)]
    US = [(-5, 6, 0), (5, 6, 0)]

    def _two_locked(self) -> PathEngine:
        engine = _engine(_path("cross", self.DS, active=True), _path("loop", self.US, active=True), slide_time=0.0)
        engine.press(1, (3.0, 0.2, 0.0))
        engine.press(2, (3.0, 5.8, 0.0))
        engine.tick(1.0)
        return engine

    def _reload(self, engine: PathEngine, *named: tuple[str, list[tuple[float, float, float]]]) -> None:
        engine.reload(PathsConfig(slide_time=0.0, paths=[_path(n, pts, active=True) for n, pts in named]), enabled=True)

    def test_a_name_given_up_and_taken_by_another_path_carries_both(self) -> None:
        engine = self._two_locked()
        engine.stage_rename("cross", "ds")
        engine.stage_rename("loop", "cross")
        self._reload(engine, ("ds", self.DS), ("cross", self.US))
        assert (engine.locked_path(1), engine.locked_path(2)) == ("ds", "cross")
        assert engine.tick(0.02) == {1: pytest.approx((3.0, 0.0, 0.0)), 2: pytest.approx((3.0, 6.0, 0.0))}

    def test_the_reused_name_renamed_again_follows_the_path_that_took_it(self) -> None:
        engine = self._two_locked()
        engine.stage_rename("cross", "ds")
        engine.stage_rename("loop", "cross")
        engine.stage_rename("CROSS", "us")
        self._reload(engine, ("ds", self.DS), ("us", self.US))
        assert (engine.locked_path(1), engine.locked_path(2)) == ("ds", "us")

    def test_two_paths_swap_names_through_a_third(self) -> None:
        engine = self._two_locked()
        engine.record("cross", PathsConfig(paths=[_path("cross", self.DS)]))
        for old, new in [("cross", "tmp"), ("loop", "cross"), ("tmp", "loop")]:
            engine.stage_rename(old, new)
        self._reload(engine, ("loop", self.DS), ("cross", self.US))
        assert (engine.locked_path(1), engine.locked_path(2), engine.recording) == ("loop", "cross", "loop")

    def test_a_path_made_since_the_reload_under_a_given_up_name_takes_nothing(self) -> None:
        engine = self._two_locked()
        engine.stage_rename("cross", "ds")
        engine.stage_rename("cross", "fresh")  # a new path called cross, renamed
        self._reload(engine, ("ds", self.DS), ("fresh", self.US), ("loop", self.US))
        assert (engine.locked_path(1), engine.locked_path(2)) == ("ds", "loop")


class TestTheWebView:
    def test_a_staged_rename_names_the_locks_and_the_recording_before_the_reload(self) -> None:
        engine = _engine(CROSS, RAMP)
        engine.press(1, (3.0, 0.2, 0.0))
        engine.press(2, (0.2, 3.0, 0.0))
        engine.record("cross", PathsConfig(paths=[CROSS]))
        engine.stage_rename("CROSS", "ds")
        assert engine.web_state() == ("ds", {1: "ds", 2: "Ramp"})
        assert (engine.locked_path(1), engine.recording) == ("cross", "cross")

    def test_nothing_recording(self) -> None:
        assert _engine(CROSS).web_state() == (None, {})


def _stop_at(step: int, prepare: Any, run: Any, functions: set[str]) -> tuple[PathEngine, int]:
    """Run ``run(engine, cfg)`` with a web thread's Stop sent at bytecode ``step`` of the engine's ``functions``.

    The Stop runs on its own thread and is given a moment to land there; one that has to wait for the
    engine's lock lands once the lock is let go. Returns the engine and how many steps there were.
    """
    from openfollow.paths import engine as engine_module

    engine, cfg = prepare()
    count = 0
    stop = threading.Thread(target=engine.record, args=(None, cfg))

    def local(frame: FrameType, event: str, arg: object) -> Any:
        nonlocal count
        frame.f_trace_opcodes = True
        if event == "opcode":
            count += 1
            if count == step:
                stop.start()
                stop.join(timeout=0.02)
        return local

    def tracer(frame: FrameType, event: str, arg: object) -> Any:
        if frame.f_globals is vars(engine_module) and frame.f_code.co_name in functions:
            return local
        return None

    before = sys.gettrace()
    sys.settrace(tracer)
    try:
        run(engine, cfg)
    finally:
        sys.settrace(before)
    if stop.ident is not None:
        stop.join()
    return engine, count


def _stop_lands_at_every_step(prepare: Any, run: Any, functions: set[str]) -> list[PathEngine]:
    """The engines left by a web Stop sent at each step in turn; the first had no Stop."""
    first, steps = _stop_at(0, prepare, run, functions)
    return [first, *(_stop_at(step, prepare, run, functions)[0] for step in range(1, steps + 1))]


class TestAStopFromTheWebThread:
    @staticmethod
    def _recording(*, renamed: bool) -> tuple[PathEngine, PathsConfig]:
        engine = _engine(CROSS)
        engine.record("cross", PathsConfig(paths=[CROSS]))
        if renamed:
            engine.stage_rename("cross", "ds")
        return engine, PathsConfig(paths=[_path("ds" if renamed else "cross", [(-5, 0, 0), (5, 0, 0)])])

    @pytest.mark.parametrize("renamed", [False, True])
    def test_is_never_undone_by_a_reload_wherever_it_lands(self, renamed: bool) -> None:
        engines = _stop_lands_at_every_step(
            lambda: self._recording(renamed=renamed),
            lambda engine, cfg: engine.reload(cfg, enabled=True),
            {"reload", "_carry_renames", "_replace_recording"},
        )
        assert len(engines) > 20
        assert [engine.recording for engine in engines[1:]] == [None] * (len(engines) - 1)

    def test_is_never_undone_by_switching_paths_off(self) -> None:
        engines = _stop_lands_at_every_step(
            lambda: self._recording(renamed=False),
            lambda engine, cfg: engine.reload(cfg, enabled=False),
            {"reload", "_replace_recording"},
        )
        assert len(engines) > 20
        assert {engine.recording for engine in engines} == {None}

    def test_a_recording_started_while_the_old_one_is_dropped_is_kept(self) -> None:
        engine = _engine(CROSS, RAMP)
        engine.record("cross", PathsConfig(paths=[CROSS]))
        cfg = PathsConfig(paths=[RAMP])  # cross deleted
        find = cfg.find

        def find_and_record(name: str) -> PathConfig | None:
            # The reload asks whether the recorded path is gone: the web thread records Ramp right then.
            if name == "cross":
                engine.record("ramp", cfg)
            return find(name)

        cfg.find = find_and_record  # type: ignore[method-assign]
        engine.reload(cfg, enabled=True)
        assert engine.recording == "Ramp"

    def test_never_breaks_drawing_the_recorded_path(self) -> None:
        engines = _stop_lands_at_every_step(
            lambda: self._recording(renamed=False),
            lambda engine, cfg: engine.drawings(cfg),
            {"drawings"},
        )
        assert len(engines) > 5


class TestSwitchArrows:
    def test_a_landed_marker_near_a_junction_gets_its_arrows(self) -> None:
        engine = _engine(CROSS, RAMP)
        engine.press(1, (-0.8, 0.0, 0.0))
        _landed(engine)
        assert engine.switch_arrows() == []
        # Pushed at 2 m/s the switch area reaches 1 m ahead.
        engine.move(1, 2.0, 0.0, 0.01)
        assert len(engine.switch_arrows()) == 3

    def test_a_frame_without_a_push_narrows_the_arrows_to_the_switch_area_floor(self) -> None:
        engine = _engine(CROSS, RAMP)
        engine.press(1, (-0.8, 0.0, 0.0))
        _landed(engine)
        engine.move(1, 2.0, 0.0, 0.01)
        assert engine.switch_arrows()
        # The marker stands 0.78 m short of the crossing: outside the 0.5 m area once nothing pushes it.
        engine.tick(1 / 60)
        assert engine.switch_arrows() == []
        engine.tick(1 / 60)
        assert engine.switch_arrows() == []
        engine.move(1, 2.0, 0.0, 0.01)
        assert engine.switch_arrows()

    def test_a_marker_still_pulled_onto_its_path_gets_none(self) -> None:
        engine = _engine(CROSS, RAMP, slide_time=1.0)
        engine.press(1, (0.1, 0.5, 0.0))
        assert engine.switch_arrows() == []

    def test_no_active_path_no_arrows(self) -> None:
        assert _engine().switch_arrows() == []
