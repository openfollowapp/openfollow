# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Paths on the frame loop: Pathfinder presses, recording, toggling, OSC commands, the status row."""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from openfollow.configuration import (
    MAX_PATH_POINTS,
    AppConfig,
    PathConfig,
    PathPointConfig,
    PathsConfig,
    load_config,
    save_config,
)
from openfollow.osc.paths import MarkerPath, PathActive, PathCommand
from openfollow.paths.engine import PathEngine
from openfollow.runtime import paths_runtime

pytestmark = pytest.mark.unit


class _Marker:
    def __init__(self, x: float, y: float, z: float) -> None:
        self.pos = (x, y, z)


class _App:
    def __init__(self, tmp_path: Path, *paths: PathConfig, experimental: bool = True) -> None:
        self._config = AppConfig(paths=PathsConfig(slide_time=0.0, paths=list(paths)))
        self._config.ui.show_experimental_features = experimental
        self._config_path = str(tmp_path / "config.toml")
        self._config_mtime = 0.0
        self._paths = PathEngine()
        paths_runtime.reload_paths(self)

    def _get_config_mtime(self) -> float:
        return Path(self._config_path).stat().st_mtime


def _cross(active: bool = True) -> PathConfig:
    return PathConfig(name="cross", active=active, points=[PathPointConfig(-5, 0, 0), PathPointConfig(5, 0, 0)])


def _levels(caplog: pytest.LogCaptureFixture, message: str) -> list[int]:
    return [record.levelno for record in caplog.records if record.getMessage() == message]


class TestPathfinder:
    def test_a_press_locks_and_a_second_releases(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        app = _App(tmp_path, _cross())
        with caplog.at_level(logging.INFO):
            paths_runtime.pathfinder(app, 3, _Marker(1, 2, 0))
            assert app._paths.locked_path(3) == "cross"
            paths_runtime.pathfinder(app, 3, _Marker(1, 0, 0))
        assert not app._paths.is_locked(3)
        assert "Marker 3 locked onto path cross" in caplog.text
        assert "Marker 3 released from its path" in caplog.text

    def test_a_press_with_nothing_active_changes_nothing(self, tmp_path: Path) -> None:
        app = _App(tmp_path, _cross(active=False))
        paths_runtime.pathfinder(app, 3, _Marker(1, 2, 0))
        assert not app._paths.is_locked(3)
        assert not Path(app._config_path).exists()

    def test_while_recording_a_press_adds_the_marker_s_position_and_saves_it(self, tmp_path: Path) -> None:
        draft = PathConfig(name="draft", points=[PathPointConfig(0, 0, 0)])
        app = _App(tmp_path, draft)
        app._paths.record("draft", app._config.paths)
        paths_runtime.pathfinder(app, 3, _Marker(1.5, 2.0, 0.8))
        assert app._config.paths.paths[0].points[-1] == PathPointConfig(1.5, 2.0, 0.8)
        saved = load_config(app._config_path).paths.paths[0].points
        assert [(p.x, p.y, p.z, p.radius) for p in saved] == [(0.0, 0.0, 0.0, 0.5), (1.5, 2.0, 0.8, 0.5)]
        # The engine already draws the new point.
        assert app._paths.drawings(app._config.paths)[0].ends[-1].at == pytest.approx((1.5, 2.0, 0.8))

    def test_a_full_path_takes_no_more_points(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        full = PathConfig(name="full", points=[PathPointConfig(x=i) for i in range(MAX_PATH_POINTS)])
        app = _App(tmp_path, full)
        app._paths.record("full", app._config.paths)
        with caplog.at_level(logging.WARNING):
            paths_runtime.pathfinder(app, 3, _Marker(9, 9, 0))
        assert len(app._config.paths.paths[0].points) == MAX_PATH_POINTS
        assert len(load_config(app._config_path).paths.paths[0].points) == MAX_PATH_POINTS
        assert caplog.text.count("already has 100 points") == 1

    def test_a_point_added_to_an_active_path_is_part_of_it_at_once(self, tmp_path: Path) -> None:
        app = _App(tmp_path, _cross())
        app._paths.record("cross", app._config.paths)
        paths_runtime.pathfinder(app, 3, _Marker(5, 3, 0))
        assert app._paths.drawings(app._config.paths)[0].ends[-1].at == pytest.approx((5.0, 3.0, 0.0))

    def test_a_path_made_on_the_web_moments_ago_is_recorded_into_the_file(self, tmp_path: Path) -> None:
        app = _saved_after(tmp_path, lambda web: web.paths.paths.append(PathConfig(name="fresh")))
        # Recording starts from the file, before the reload watcher has brought the new path in.
        assert app._paths.record("fresh", load_config(app._config_path).paths)
        paths_runtime.pathfinder(app, 3, _Marker(1, 2, 0))
        paths_runtime.pathfinder(app, 3, _Marker(3, 2, 0))
        fresh = load_config(app._config_path).paths.find("fresh")
        assert fresh is not None
        assert [(p.x, p.y) for p in fresh.points] == [(1.0, 2.0), (3.0, 2.0)]
        assert app._paths.recording == "fresh"


def test_toggle_paths_flips_visibility_and_saves_it(tmp_path: Path) -> None:
    app = _App(tmp_path, _cross())
    paths_runtime.toggle_paths(app)
    assert app._config.paths.show_paths is False
    assert load_config(app._config_path).paths.show_paths is False
    paths_runtime.toggle_paths(app)
    assert app._config.paths.show_paths is True


def test_toggle_paths_does_nothing_while_experimental_features_are_off(tmp_path: Path) -> None:
    app = _App(tmp_path, _cross(), experimental=False)
    paths_runtime.toggle_paths(app)
    assert app._config.paths.show_paths is True
    assert not Path(app._config_path).exists()


def test_a_failed_save_is_logged_and_still_applied(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    app = _App(tmp_path, _cross(active=False))
    app._config_path = str(tmp_path / "missing" / "config.toml")
    with caplog.at_level(logging.ERROR):
        paths_runtime.apply_commands(app, [PathActive("cross", True)], [], lambda _id: None)
    assert "Failed to save the paths change." in caplog.text
    assert app._paths.press(1, (0.0, 1.0, 0.0)).value == "locked"


def _record_a_point(app: _App) -> None:
    app._paths.record("draft", app._config.paths)
    paths_runtime.pathfinder(app, 3, _Marker(1, 2, 0))


def _switch_cross_off(app: _App) -> None:
    paths_runtime.apply_commands(app, [PathActive("cross", False)], [], lambda _id: None)


def _saved_after(tmp_path: Path, web_change: Any) -> _App:
    """An app whose config file a web save changed with ``web_change`` after the app loaded it."""
    app = _App(tmp_path, _cross(), PathConfig(name="draft", points=[PathPointConfig(0, 0, 0)]))
    save_config(app._config, app._config_path)
    app._config_mtime = app._get_config_mtime()
    web = load_config(app._config_path)
    web_change(web)
    save_config(web, app._config_path)
    return app


class TestSavesLeaveWebSavesAlone:
    """A paths save writes only its change into the file as it is, and leaves the file for the watcher."""

    @pytest.mark.parametrize(
        ("save", "saved"),
        [
            (_switch_cross_off, lambda cfg: cfg.paths.paths[0].active is False),
            (_record_a_point, lambda cfg: len(cfg.paths.paths[1].points) == 2),
            (paths_runtime.toggle_paths, lambda cfg: cfg.paths.show_paths is False),
        ],
    )
    def test_a_web_save_made_just_before_survives_and_is_still_applied(
        self, tmp_path: Path, save: Any, saved: Any
    ) -> None:
        def web_edit(web: AppConfig) -> None:
            web.psn_system_name = "Saved-From-Web"
            web.paths.paths[0].points[0].radius = 2.0

        app = _saved_after(tmp_path, web_edit)
        save(app)
        on_disk = load_config(app._config_path)
        assert on_disk.psn_system_name == "Saved-From-Web"
        assert on_disk.paths.paths[0].points[0].radius == 2.0
        assert saved(on_disk)
        # Not marked as loaded: the reload watcher still applies the web save.
        assert app._get_config_mtime() > app._config_mtime

    @pytest.mark.parametrize("save", [_switch_cross_off, _record_a_point])
    def test_a_path_a_web_save_removed_is_not_written_back(
        self, tmp_path: Path, save: Any, caplog: pytest.LogCaptureFixture
    ) -> None:
        app = _saved_after(tmp_path, lambda web: setattr(web.paths, "paths", []))
        save(app)
        assert load_config(app._config_path).paths.paths == []
        assert "Failed to save" not in caplog.text


class TestCommands:
    def test_switching_a_path_on_saves_it_and_makes_it_lockable(self, tmp_path: Path) -> None:
        app = _App(tmp_path, _cross(active=False))
        paths_runtime.apply_commands(app, [PathActive("CROSS", True)], [], lambda _id: None)
        assert app._config.paths.paths[0].active is True
        assert load_config(app._config_path).paths.paths[0].active is True
        assert app._paths.lock_onto(1, "cross", (0.0, 1.0, 0.0))

    def test_switching_a_path_to_what_it_is_writes_nothing(self, tmp_path: Path) -> None:
        app = _App(tmp_path, _cross())
        paths_runtime.apply_commands(app, [PathActive("cross", True)], [], lambda _id: None)
        assert not Path(app._config_path).exists()

    def test_an_unknown_path_is_logged_at_debug(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        app = _App(tmp_path, _cross())
        with caplog.at_level(logging.DEBUG):
            paths_runtime.apply_commands(app, [PathActive("nope", True)], [], lambda _id: None)
        # A source resending every frame must not flood the journal.
        assert _levels(caplog, "OSC: no path called 'nope'") == [logging.DEBUG]

    def test_a_controlled_marker_locks_and_is_released(self, tmp_path: Path) -> None:
        app = _App(tmp_path, _cross())
        markers = {3: _Marker(1, 2, 0)}
        paths_runtime.apply_commands(app, [MarkerPath(3, "Cross")], [3], markers.get)
        assert app._paths.locked_path(3) == "cross"
        paths_runtime.apply_commands(app, [MarkerPath(3, None)], [3], markers.get)
        assert not app._paths.is_locked(3)

    def test_a_marker_this_station_does_not_control_is_left_alone(self, tmp_path: Path) -> None:
        app = _App(tmp_path, _cross())
        paths_runtime.apply_commands(app, [MarkerPath(4, "cross")], [3], {4: _Marker(0, 1, 0)}.get)
        assert not app._paths.is_locked(4)

    def test_a_marker_that_cannot_be_found_is_skipped(self, tmp_path: Path) -> None:
        app = _App(tmp_path, _cross())
        paths_runtime.apply_commands(app, [MarkerPath(3, "cross")], [3], lambda _id: None)
        assert not app._paths.is_locked(3)

    def test_locking_onto_a_path_that_is_not_active_is_logged_at_debug(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        app = _App(tmp_path, _cross(active=False))
        with caplog.at_level(logging.DEBUG):
            paths_runtime.apply_commands(app, [MarkerPath(3, "cross")], [3], {3: _Marker(0, 1, 0)}.get)
        assert _levels(caplog, "OSC: no active path 'cross' to lock marker 3 onto") == [logging.DEBUG]

    def test_with_paths_switched_off_commands_do_nothing(self, tmp_path: Path) -> None:
        app = _App(tmp_path, _cross(active=False), experimental=False)
        paths_runtime.apply_commands(app, [PathActive("cross", True)], [], lambda _id: None)
        assert app._config.paths.paths[0].active is False


def _side(active: bool = False) -> PathConfig:
    return PathConfig(name="side", active=active, points=[PathPointConfig(0, 3, 0), PathPointConfig(5, 3, 0)])


class TestOneFrameOfCommands:
    """A frame's switches reach the file and the engine once, net; locks and releases keep their order."""

    @pytest.fixture
    def counted(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[_App, dict[str, int]]:
        app = _App(tmp_path, _cross(), _side())
        calls = {"save": 0, "reload": 0}
        save, reload = paths_runtime.save_config_change, app._paths.reload

        def counted_save(*args: Any) -> None:
            calls["save"] += 1
            save(*args)

        def counted_reload(*args: Any, **kwargs: Any) -> None:
            calls["reload"] += 1
            reload(*args, **kwargs)

        monkeypatch.setattr(paths_runtime, "save_config_change", counted_save)
        monkeypatch.setattr(app._paths, "reload", counted_reload)
        return app, calls

    def test_a_burst_of_switches_is_one_save_and_one_rebuild_and_the_last_per_path_wins(
        self, counted: tuple[_App, dict[str, int]]
    ) -> None:
        app, calls = counted
        # Cross ends switched off, side switched on.
        burst = [PathActive("CROSS", i % 2 == 0) for i in range(200)] + [
            PathActive("side", on) for on in (True, False, True)
        ]
        paths_runtime.apply_commands(app, burst, [], lambda _id: None)
        assert calls == {"save": 1, "reload": 1}
        on_disk = load_config(app._config_path).paths
        assert [p.active for p in app._config.paths.paths] == [p.active for p in on_disk.paths] == [False, True]
        assert not app._paths.lock_onto(1, "cross", (0.0, 1.0, 0.0))
        assert app._paths.lock_onto(1, "side", (0.0, 1.0, 0.0))

    def test_a_path_switched_off_and_on_again_in_one_frame_changes_nothing(
        self, counted: tuple[_App, dict[str, int]]
    ) -> None:
        app, calls = counted
        app._paths.press(3, (0.0, 1.0, 0.0))
        paths_runtime.apply_commands(app, [PathActive("cross", False), PathActive("cross", True)], [], lambda _id: None)
        assert calls == {"save": 0, "reload": 0}
        assert app._paths.is_locked(3)
        assert not Path(app._config_path).exists()

    @pytest.mark.parametrize(
        ("commands", "locked", "saves", "reloads"),
        [
            # A lock onto a path switched on before it in the frame finds it.
            ([PathActive("side", True), MarkerPath(3, "side")], "side", 1, 1),
            # A lock sent before the switch does not.
            ([MarkerPath(3, "side"), PathActive("side", True)], None, 1, 1),
            # A lock onto a path then switched off is released by it.
            ([MarkerPath(3, "cross"), PathActive("cross", False)], None, 1, 1),
            # A lock onto a path nobody switched needs no rebuild first, so another path's blip costs nothing.
            ([PathActive("side", True), MarkerPath(3, "cross"), PathActive("side", False)], "cross", 0, 0),
        ],
    )
    def test_locks_keep_their_place_among_the_switches(
        self,
        counted: tuple[_App, dict[str, int]],
        commands: list[PathCommand],
        locked: str | None,
        saves: int,
        reloads: int,
    ) -> None:
        app, calls = counted
        markers = {3: _Marker(1, 2, 0)}
        paths_runtime.apply_commands(app, commands, [3], markers.get)
        assert app._paths.locked_path(3) == locked
        assert calls == {"save": saves, "reload": reloads}

    def test_a_release_needs_no_rebuild(self, counted: tuple[_App, dict[str, int]]) -> None:
        app, calls = counted
        app._paths.press(3, (0.0, 1.0, 0.0))
        paths_runtime.apply_commands(app, [PathActive("side", True), MarkerPath(3, None)], [3], {}.get)
        assert not app._paths.is_locked(3)
        assert calls == {"save": 1, "reload": 1}


class TestRecordingRow:
    def test_no_row_while_nothing_records(self, tmp_path: Path) -> None:
        assert paths_runtime.recording_row(_App(tmp_path, _cross())) is None

    @pytest.mark.parametrize(("count", "text"), [(1, "1 point"), (2, "2 points"), (0, "0 points")])
    def test_the_row_names_the_path_and_counts_its_points(self, tmp_path: Path, count: int, text: str) -> None:
        draft = PathConfig(name="draft", points=[PathPointConfig(x=i) for i in range(count)])
        app = _App(tmp_path, draft)
        app._paths.record("draft", app._config.paths)
        assert paths_runtime.recording_row(app) == ("info", f"Recording path draft: {text}")


class TestRelease:
    def test_release_lets_a_marker_go(self, tmp_path: Path) -> None:
        app = _App(tmp_path, _cross())
        app._paths.press(3, (0.0, 1.0, 0.0))
        paths_runtime.release(app, 3)
        assert not app._paths.is_locked(3)

    def test_release_without_paths_or_without_a_marker_does_nothing(self, tmp_path: Path) -> None:
        paths_runtime.release(SimpleNamespace(), 3)
        app = _App(tmp_path, _cross())
        app._paths.press(3, (0.0, 1.0, 0.0))
        paths_runtime.release(app, None)
        assert app._paths.is_locked(3)
