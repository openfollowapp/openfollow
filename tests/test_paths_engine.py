# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The path engine: locking, sliding, moving on a path, reloads, recording and drawing."""

from __future__ import annotations

import math

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

    def test_height_and_mouse_and_osc_carry_on_during_the_pull(self) -> None:
        engine = _engine(CROSS, slide_time=1.0)
        engine.press(1, (0.0, 2.0, 0.0))
        engine.tick(0.5)
        assert _round(engine.move(1, 0.0, 0.0, 0.5, vz=2.0)) == (0.0, 1.0, 0.5)
        assert _round(engine.project(1, 3.0, 2.0)) == (3.0, 1.0, 0.5)
        # The mouse moves the free position a fraction of the way to the cursor.
        assert _round(engine.glide(1, 5.0, 2.0, 0.5)) == (4.0, 1.0, 0.5)

    def test_the_pull_ends_on_the_path_s_height(self) -> None:
        engine = _engine(RAMP)
        engine.press(1, (1.0, 2.5, 3.0))
        assert _round(_landed(engine)) == (0.0, 2.5, 1.0)
        assert engine.tick(0.1) == {}

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
        assert engine.glide(1, 0.0, 0.0, 0.5) is None
        assert engine.constrain(1, 0.0, 0.0) is None

    def test_an_end_that_releases_lets_the_marker_go(self) -> None:
        engine = self._locked(_path("out", [(0, 0, 0), (2, 0, 0)], active=True, release_at_end=True), at=(1, 0, 0))
        assert _round(engine.move(1, 1.0, 0.0, 5.0)) == (2.0, 0.0, 0.0)
        assert not engine.is_locked(1)

    def test_an_absolute_position_lands_on_the_nearest_point_it_can_reach(self) -> None:
        apart = _path("apart", [(20, 0, 0), (30, 0, 0)], active=True)
        engine = self._locked(CROSS, RAMP, apart, at=(-3.0, 0.0, 0.0))
        # Up the ramp, which crosses the cross: reachable. The height eases up to the ramp's.
        assert _round(engine.project(1, 0.3, 4.0)) == (0.0, 4.0, 0.0)
        assert engine.locked_path(1) == "Ramp"
        assert _round(engine.tick(0.5)[1]) == (0.0, 4.0, 1.6)
        # The separate path is nearer to (19, 0) but cannot be reached from here.
        assert _round(engine.project(1, 19.0, 0.0)) == (5.0, 0.0, 1.6)
        assert _round(engine.tick(0.5)[1]) == (5.0, 0.0, 0.0)

    def test_a_glide_never_aims_at_a_path_it_cannot_reach(self) -> None:
        apart = _path("apart", [(20, 0, 0), (30, 0, 0)], active=True)
        engine = self._locked(CROSS, apart, at=(-4.0, 0.0, 0.0))
        # Nearest to (19, 0) overall is the separate path; the marker heads for the cross's end instead.
        assert _round(engine.glide(1, 19.0, 0.0, 0.5)) == (0.5, 0.0, 0.0)

    def test_a_glide_travels_towards_the_reachable_point_nearest_the_target(self) -> None:
        engine = self._locked(CROSS, RAMP, at=(-4.0, 0.0, 0.0))
        # 4 m along the cross, then 4 m up the ramp: half way is the crossing.
        assert _round(engine.glide(1, 0.0, 4.0, 0.5)) == (0.0, 0.0, 0.0)
        # The paths meet at one height, so the ramp's own rise is all the height there is.
        assert _round(engine.glide(1, 0.0, 4.0, 1.0)) == (0.0, 4.0, 1.6)
        assert engine.locked_path(1) == "Ramp"


class TestConstrain:
    """A second position following a locked marker is put on its path as ``project`` would, the lock left alone."""

    def _locked(self, *paths: PathConfig, at: tuple[float, float, float]) -> PathEngine:
        engine = _engine(*paths, slide_time=0.0)
        engine.press(1, at)
        engine.tick(0.0)
        return engine

    def test_a_position_lands_on_the_nearest_point_the_marker_can_reach(self) -> None:
        apart = _path("apart", [(20, 0, 0), (30, 0, 0)], active=True)
        engine = self._locked(CROSS, RAMP, apart, at=(-3.0, 0.0, 0.0))
        assert _round(engine.constrain(1, 3.0, 2.0)) == (3.0, 0.0, 0.0)
        assert _round(engine.constrain(1, 0.3, 4.0)) == (0.0, 4.0, 1.6)
        assert _round(engine.constrain(1, 19.0, 0.0)) == (5.0, 0.0, 0.0)
        # The marker is where it was, on the path it was on.
        assert _round(engine.move(1, 0.0, 0.0, 0.0)) == (-3.0, 0.0, 0.0)
        assert engine.locked_path(1) == "cross"

    def test_it_shares_what_is_left_of_the_marker_s_height_switch(self) -> None:
        engine = self._locked(CROSS, RAMP, at=(-3.0, 0.0, 0.0))
        engine.project(1, 0.3, 4.0)
        # The ramp is 1.2 m up at y = 3, and the marker's 1.6 m step up has not begun to ease.
        assert _round(engine.constrain(1, 0.0, 3.0)) == (0.0, 3.0, -0.4)
        engine.tick(0.25)
        assert _round(engine.constrain(1, 0.0, 3.0)) == (0.0, 3.0, 0.4)

    def test_during_the_pull_it_is_pulled_in_as_far_as_the_marker(self) -> None:
        engine = _engine(CROSS, slide_time=1.0)
        engine.press(1, (0.0, 2.0, 1.0))
        engine.tick(0.5)
        # Half pulled in: half way from (3, 2) at the marker's free height to (3, 0) on the cross.
        assert _round(engine.constrain(1, 3.0, 2.0)) == (3.0, 1.0, 0.5)
        assert _round(engine.tick(0.0)[1]) == (0.0, 1.0, 0.5)


class TestLockOnto:
    def test_a_marker_locks_onto_a_named_path_ignoring_case(self) -> None:
        engine = _engine(CROSS, RAMP)
        assert engine.lock_onto(1, "ramp", (3.0, 1.0, 0.0))
        assert engine.locked_path(1) == "Ramp"
        assert _round(_landed(engine)) == (0.0, 1.0, 0.4)

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
