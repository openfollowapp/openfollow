# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Touch remote: the session registry, its shaping helpers, and how the frame loop applies a hold."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

import openfollow.input.input_manager as input_manager_module
from openfollow.configuration import AppConfig, PathConfig, PathPointConfig, PathsConfig, load_config, save_config
from openfollow.input.gamepad import GamepadUpdate
from openfollow.input.input_manager import InputManager
from openfollow.input.parking import set_parked
from openfollow.input.touch_remote import (
    DRIVE_STALE_S,
    GLIDE,
    HOLD_STALE_S,
    KEPT,
    LOST,
    MAX_PENDING_ACTIONS,
    MAX_REACH_M,
    MAX_SESSIONS,
    OK,
    PATHFINDER,
    RESET,
    UNKNOWN,
    RemoteAction,
    TouchRemote,
    holds,
    snap_z,
    status_row,
    stick_velocity,
)
from openfollow.operator_messages import OperatorMessageStore
from openfollow.paths.engine import PathEngine
from openfollow.psn.marker import Marker

pytestmark = pytest.mark.usefixtures("every_feature_on")


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


@pytest.fixture
def remote(clock: _Clock) -> TouchRemote:
    return TouchRemote(clock=clock)


# -- Registry ------------------------------------------------------------------------------


@pytest.mark.unit
def test_a_hold_gives_the_session_its_marker(remote: TouchRemote) -> None:
    token = remote.open()
    assert remote.hold(token, 3) == OK
    assert remote.held_ids() == {3}
    assert remote.status(token) == {"held": 3, "lost": False}


@pytest.mark.unit
def test_the_newest_hold_takes_the_marker_and_the_first_remote_learns_it(remote: TouchRemote) -> None:
    first, second = remote.open(), remote.open()
    remote.hold(first, 3)
    assert remote.hold(second, 3) == OK
    assert remote.status(first) == {"held": None, "lost": True}
    assert remote.drive(first, 3, (1.0, 0.0), 0) == LOST
    assert [frame.marker_id for frame in remote.take()] == [3]
    assert remote.drive(second, 3, (1.0, 0.0), 0) == OK


@pytest.mark.unit
def test_taking_the_marker_back_steals_it_again(remote: TouchRemote) -> None:
    first, second = remote.open(), remote.open()
    remote.hold(first, 3)
    remote.hold(second, 3)
    assert remote.hold(first, 3) == OK
    assert remote.status(first) == {"held": 3, "lost": False}
    assert remote.status(second) == {"held": None, "lost": True}


@pytest.mark.unit
def test_holding_the_same_marker_again_keeps_it(remote: TouchRemote) -> None:
    token = remote.open()
    remote.hold(token, 3)
    remote.drive(token, 3, (0.5, 0.0), 2)
    assert remote.hold(token, 3) == KEPT
    (frame,) = remote.take()
    assert (frame.stick, frame.z_notches) == ((0.5, 0.0), 2)


@pytest.mark.unit
def test_picking_another_marker_lets_go_of_the_first(remote: TouchRemote) -> None:
    token = remote.open()
    remote.hold(token, 3)
    remote.hold(token, 4)
    assert remote.held_ids() == {4}


@pytest.mark.unit
def test_closing_lets_go_and_ends_the_session(remote: TouchRemote) -> None:
    token = remote.open()
    remote.hold(token, 3)
    remote.close(token)
    assert remote.held_ids() == frozenset()
    assert remote.status(token) is None
    assert remote.drive(token, 3, (0.0, 0.0), 0) == UNKNOWN


@pytest.mark.unit
def test_closing_an_unknown_session_changes_nothing(remote: TouchRemote) -> None:
    token = remote.open()
    remote.hold(token, 3)
    remote.close("nope")
    assert remote.held_ids() == {3}


@pytest.mark.unit
@pytest.mark.parametrize("call", ["hold", "drive", "act"])
def test_an_unknown_token_is_refused(remote: TouchRemote, call: str) -> None:
    outcome = {
        "hold": lambda: remote.hold("nope", 3),
        "drive": lambda: remote.drive("nope", 3, (0.0, 0.0), 0),
        "act": lambda: remote.act("nope", 3, RemoteAction(RESET)),
    }[call]()
    assert outcome == UNKNOWN


@pytest.mark.unit
def test_driving_a_marker_the_session_does_not_hold_is_lost(remote: TouchRemote) -> None:
    token = remote.open()
    remote.hold(token, 3)
    assert remote.drive(token, 4, (1.0, 0.0), 0) == LOST
    assert remote.act(token, 4, RemoteAction(RESET)) == LOST


@pytest.mark.unit
def test_a_frame_carries_the_stick_notches_and_presses_once(remote: TouchRemote) -> None:
    token = remote.open()
    remote.hold(token, 3)
    remote.drive(token, 3, (0.5, -0.25), 2)
    remote.drive(token, 3, (0.5, -0.25), -1)
    remote.act(token, 3, RemoteAction(RESET))
    (frame,) = remote.take()
    assert frame.stick == (0.5, -0.25)
    assert frame.z_notches == 1
    assert frame.actions == (RemoteAction(RESET),)
    (again,) = remote.take()
    assert again.z_notches == 0
    assert again.actions == ()


@pytest.mark.unit
def test_a_deflection_past_full_is_cut_to_full(remote: TouchRemote) -> None:
    token = remote.open()
    remote.hold(token, 3)
    remote.drive(token, 3, (3.0, 4.0), 0)
    (frame,) = remote.take()
    assert frame.stick == pytest.approx((0.6, 0.8))


@pytest.mark.unit
def test_presses_queue_up_to_their_cap(remote: TouchRemote) -> None:
    token = remote.open()
    remote.hold(token, 3)
    for _ in range(MAX_PENDING_ACTIONS + 3):
        assert remote.act(token, 3, RemoteAction(RESET)) == OK
    (frame,) = remote.take()
    assert len(frame.actions) == MAX_PENDING_ACTIONS


@pytest.mark.unit
def test_the_marker_stops_when_drive_messages_stop(remote: TouchRemote, clock: _Clock) -> None:
    token = remote.open()
    remote.hold(token, 3)
    remote.drive(token, 3, (1.0, 0.0), 0)
    clock.now += DRIVE_STALE_S
    assert remote.take()[0].stick == (1.0, 0.0)
    clock.now += 0.01
    assert remote.take()[0].stick == (0.0, 0.0)


@pytest.mark.unit
def test_a_quiet_session_lets_go_after_the_hold_timeout(remote: TouchRemote, clock: _Clock) -> None:
    token = remote.open()
    remote.hold(token, 3)
    clock.now += HOLD_STALE_S
    assert remote.held_ids() == {3}
    remote.take()
    assert remote.held_ids() == {3}
    clock.now += 0.01
    assert remote.take() == []
    assert remote.held_ids() == frozenset()
    assert remote.status(token) is None


@pytest.mark.unit
def test_a_lapsed_hold_is_logged_with_its_marker(
    remote: TouchRemote, clock: _Clock, caplog: pytest.LogCaptureFixture
) -> None:
    holding, idle = remote.open(), remote.open()
    remote.hold(holding, 3)
    clock.now += HOLD_STALE_S
    with caplog.at_level("INFO", logger="openfollow.input.touch_remote"):
        remote.take()
        assert caplog.messages == []
        clock.now += 0.01
        remote.take()
    assert caplog.messages == ["Web remote sent nothing for 10 s – released marker 3."]
    assert remote.status(idle) is None


@pytest.mark.unit
def test_a_status_poll_keeps_the_session_alive(remote: TouchRemote, clock: _Clock) -> None:
    token = remote.open()
    remote.hold(token, 3)
    clock.now += HOLD_STALE_S
    remote.status(token)
    clock.now += HOLD_STALE_S
    remote.take()
    assert remote.held_ids() == {3}


@pytest.mark.unit
def test_opening_past_the_cap_forgets_the_quietest_session(remote: TouchRemote, clock: _Clock) -> None:
    oldest = remote.open()
    remote.hold(oldest, 3)
    for _ in range(MAX_SESSIONS - 1):
        clock.now += 1.0
        remote.open()
    clock.now += 1.0
    remote.open()
    assert remote.status(oldest) is None
    assert remote.held_ids() == frozenset()


@pytest.mark.unit
def test_release_uncontrolled_and_release_all(remote: TouchRemote) -> None:
    a, b = remote.open(), remote.open()
    remote.hold(a, 3)
    remote.hold(b, 4)
    remote.release_uncontrolled([4])
    assert remote.held_ids() == {4}
    assert remote.status(a) == {"held": None, "lost": False}
    remote.release_all()
    assert remote.held_ids() == frozenset()


@pytest.mark.unit
def test_holds_asks_the_app_s_registry(remote: TouchRemote) -> None:
    token = remote.open()
    remote.hold(token, 3)
    app = SimpleNamespace(_touch_remote=remote)
    assert holds(app, 3)
    assert not holds(app, 4)
    assert not holds(app, None)
    assert not holds(SimpleNamespace(), 3)


@pytest.mark.unit
def test_the_status_row_names_the_held_markers(remote: TouchRemote) -> None:
    app = SimpleNamespace(_touch_remote=remote)
    assert status_row(app) is None
    remote.hold(remote.open(), 4)
    assert status_row(app) == ("caution", "Web remote holds marker 4")
    remote.hold(remote.open(), 2)
    assert status_row(app) == ("caution", "Web remote holds markers 2, 4")


# -- Shaping -------------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("stick", "curve", "want"),
    [
        ((0.05, 0.0), 2.0, (0.0, 0.0)),  # inside the dead zone
        ((1.0, 0.0), 2.0, (3.0, 0.0)),  # full deflection: max speed
        ((0.0, -1.0), 1.0, (0.0, -3.0)),
        ((0.54, 0.0), 1.0, (1.5, 0.0)),  # half way past the dead zone, linear
        ((0.54, 0.0), 2.0, (0.75, 0.0)),  # same, squared
    ],
)
def test_stick_velocity_shapes_past_the_dead_zone(
    stick: tuple[float, float], curve: float, want: tuple[float, float]
) -> None:
    assert stick_velocity(stick, curve, 0.08, 3.0) == pytest.approx(want)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("z", "notches", "step", "want"),
    [
        (1.6, 1, 0.05, 1.65),  # on a line: one step
        (1.6, -1, 0.05, 1.55),
        (0.0, 3, 0.01, 0.03),
        (0.075, 1, 0.05, 0.10),  # between lines: the first notch reaches the next line
        (0.075, -1, 0.05, 0.05),
        (0.025, 1, 0.05, 0.05),
        (1.0, 1, 0.3, 1.2),
        (1.62, -2, 0.05, 1.55),
    ],
)
def test_snap_z_moves_line_by_line_along_the_step_grid(z: float, notches: int, step: float, want: float) -> None:
    assert snap_z(z, notches, step) == pytest.approx(want)


# -- The frame loop ------------------------------------------------------------------------


class _Server:
    def __init__(self) -> None:
        self.markers: dict[int, Marker] = {}

    def get_marker(self, marker_id: int) -> Marker | None:
        return self.markers.get(marker_id)


class _Keyboard:
    velocity: tuple[float, float, float] | None = None

    def __init__(self, app, *, event_bus=None) -> None:  # noqa: ANN001
        self.app = app

    @property
    def keys(self) -> set[str]:
        return set()

    def update(self, _dt: float) -> tuple[float, float, float] | None:
        return self.velocity


class _Gamepad:
    next_update = GamepadUpdate()

    def __init__(self, app, *, event_bus=None, virtual_faders=None, marker_resolver=None) -> None:  # noqa: ANN001
        self.joysticks = {0: object()}

    def update(self, _dt: float) -> GamepadUpdate:
        return self.next_update

    def device_identities(self) -> dict[int, tuple[str | None, str, tuple[str, ...]]]:
        return {0: (None, "Pad", ())}

    def stop(self) -> None:
        pass


class _App:
    def __init__(self, clock: _Clock, config_path: str) -> None:
        self._config = AppConfig()
        self._config.osc.enabled = False
        self._config.touch_remote.enabled = True
        self._config.marker.max_speed = 2.0
        self._config.marker.default_pos_x = 5.0
        self._config.marker.default_pos_y = 6.0
        self._config.marker.default_pos_z = 7.0
        self._config_path = config_path
        self._controlled_ids = [10, 11]
        self._selected_id = 10
        self._server = _Server()
        for marker_id in self._controlled_ids:
            self._server.markers[marker_id] = Marker(marker_id, f"Marker {marker_id}")
            self._server.markers[marker_id].set_pos(0.0, 0.0, 1.6)
        self._assist_manual: dict[int, Marker] = {}
        self.detection_applied: list[bool] = []
        self._runtime_services = SimpleNamespace(
            _virtual_faders=None,
            _operator_message_store=OperatorMessageStore(),
            apply_detection_change=lambda cfg: self.detection_applied.append(cfg.enabled),
        )
        self._touch_remote = TouchRemote(clock=clock)
        self._paths = PathEngine()

    def _get_default_marker_position(self) -> tuple[float, float, float]:
        cfg = self._config.marker
        return (cfg.default_pos_x, cfg.default_pos_y, cfg.default_pos_z)


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch, clock: _Clock, tmp_path) -> _App:  # noqa: ANN001
    monkeypatch.setattr(input_manager_module, "KeyboardHandler", _Keyboard)
    monkeypatch.setattr(input_manager_module, "GamepadHandler", _Gamepad)
    _Keyboard.velocity = None
    _Gamepad.next_update = GamepadUpdate()
    path = str(tmp_path / "config.toml")
    built = _App(clock, path)
    save_config(built._config, path)
    return built


def _held(app: _App, marker_id: int = 10) -> str:
    token = app._touch_remote.open()
    app._touch_remote.hold(token, marker_id)
    return token


def _pos(app: _App, marker_id: int = 10) -> tuple[float, float, float]:
    return app._server.markers[marker_id].pos


@pytest.mark.integration
def test_the_stick_moves_the_held_marker_at_the_station_s_max_speed(app: _App) -> None:
    token = _held(app)
    app._config.marker_move_speeds[10] = 0.2
    app._touch_remote.drive(token, 10, (1.0, 0.0), 0)
    InputManager(app).update_touch_remote(0.5)
    assert _pos(app) == pytest.approx((1.0, 0.0, 1.6))


@pytest.mark.integration
def test_the_remote_moves_and_resets_a_parked_marker_it_holds(app: _App) -> None:
    """A hold names one marker explicitly, like a mouse grab, so parking does not stop it."""
    set_parked(app, [10])
    token = _held(app)
    app._touch_remote.drive(token, 10, (1.0, 0.0), 0)
    manager = InputManager(app)
    manager.update_touch_remote(0.5)
    assert _pos(app) == pytest.approx((1.0, 0.0, 1.6))
    app._touch_remote.drive(token, 10, (0.0, 0.0), 0)
    app._touch_remote.act(token, 10, RemoteAction(RESET))
    manager.update_touch_remote(0.02)
    assert _pos(app) == pytest.approx((5.0, 6.0, 7.0))


@pytest.mark.integration
@pytest.mark.parametrize("frame", ["heading", "camera", "screen"])
def test_the_stick_steers_in_stage_directions_whatever_the_control_frame(app: _App, frame: str) -> None:
    app._config.marker.control_frame = frame
    app._config.marker.control_heading = 180.0
    token = _held(app)
    app._touch_remote.drive(token, 10, (0.0, 1.0), 0)
    InputManager(app).update_touch_remote(0.5)
    assert _pos(app) == pytest.approx((0.0, 1.0, 1.6))


@pytest.mark.integration
def test_z_notches_step_the_height(app: _App) -> None:
    token = _held(app)
    app._touch_remote.drive(token, 10, (0.0, 0.0), 3)
    InputManager(app).update_touch_remote(0.02)
    assert _pos(app) == pytest.approx((0.0, 0.0, 1.75))


@pytest.mark.integration
def test_reset_sends_the_held_marker_home(app: _App) -> None:
    token = _held(app)
    app._touch_remote.act(token, 10, RemoteAction(RESET))
    InputManager(app).update_touch_remote(0.02)
    assert _pos(app) == pytest.approx((5.0, 6.0, 7.0))


@pytest.mark.integration
def test_a_glide_travels_at_max_speed_and_lands(app: _App) -> None:
    token = _held(app)
    app._touch_remote.drive(token, 10, (0.0, 0.0), 0)
    app._touch_remote.act(token, 10, RemoteAction(GLIDE, (3.0, 0.0, 1.6)))
    manager = InputManager(app)
    manager.update_touch_remote(0.5)
    assert _pos(app) == pytest.approx((1.0, 0.0, 1.6))
    manager.update_touch_remote(1.0)
    manager.update_touch_remote(1.0)
    assert _pos(app) == pytest.approx((3.0, 0.0, 1.6))


@pytest.mark.integration
def test_the_stick_cancels_a_glide(app: _App, clock: _Clock) -> None:
    token = _held(app)
    app._touch_remote.drive(token, 10, (0.0, 0.0), 0)
    app._touch_remote.act(token, 10, RemoteAction(GLIDE, (10.0, 0.0, 1.6)))
    manager = InputManager(app)
    manager.update_touch_remote(0.5)
    app._touch_remote.drive(token, 10, (0.0, 1.0), 0)
    manager.update_touch_remote(0.5)
    clock.now += 1.0
    manager.update_touch_remote(1.0)
    assert _pos(app) == pytest.approx((1.0, 1.0, 1.6))


def _cross(app: _App) -> None:
    cross = PathConfig(name="cross", active=True, points=[PathPointConfig(-5, 0, 0, 0), PathPointConfig(5, 0, 0, 0)])
    app._paths.reload(PathsConfig(slide_time=0.0, paths=[cross]), enabled=True)


@pytest.mark.integration
def test_pathfinder_locks_the_held_marker_and_the_z_wheel_leaves_its_height(app: _App) -> None:
    _cross(app)
    app._server.markers[10].set_pos(1.0, 2.0, 1.6)
    token = _held(app)
    app._touch_remote.act(token, 10, RemoteAction(PATHFINDER))
    manager = InputManager(app)
    manager.update_touch_remote(0.02)
    assert app._paths.locked_path(10) == "cross"
    manager.update_paths(0.02)
    landed = _pos(app)
    app._touch_remote.drive(token, 10, (0.0, 0.0), 4)
    manager.update_touch_remote(0.02)
    assert _pos(app) == pytest.approx(landed)


@pytest.mark.integration
def test_a_glide_releases_the_path_first(app: _App) -> None:
    _cross(app)
    app._paths.press(10, (0.0, 1.0, 0.0))
    token = _held(app)
    app._touch_remote.drive(token, 10, (0.0, 0.0), 0)
    app._touch_remote.act(token, 10, RemoteAction(GLIDE, (0.0, 0.5, 1.0)))
    InputManager(app).update_touch_remote(1.0)
    assert not app._paths.is_locked(10)
    assert _pos(app) == pytest.approx((0.0, 0.5, 1.0))


@pytest.mark.integration
def test_other_inputs_leave_a_held_marker_alone(app: _App) -> None:
    _held(app)
    _Keyboard.velocity = (1.0, 0.0, 0.0)
    _Gamepad.next_update = GamepadUpdate(movements={0: (1.0, 0.0, 0.0)}, resets={0})
    manager = InputManager(app)
    manager.osc_handler = SimpleNamespace(flush_parks=dict, flush_updates=lambda: {10: {"x": 4.0}})
    manager.update(1.0)
    assert _pos(app) == pytest.approx((0.0, 0.0, 1.6))


@pytest.mark.integration
def test_other_inputs_move_the_marker_again_once_the_remote_closes(app: _App) -> None:
    token = _held(app)
    app._touch_remote.close(token)
    _Keyboard.velocity = (1.0, 0.0, 0.0)
    InputManager(app).update(1.0)
    assert _pos(app) == pytest.approx((1.0, 0.0, 1.6))


@pytest.mark.integration
def test_an_osc_path_lock_skips_a_held_marker(app: _App) -> None:
    from openfollow.osc.paths import MarkerPath

    _cross(app)
    _held(app)
    manager = InputManager(app)
    manager.path_osc_handler = SimpleNamespace(take=lambda: [MarkerPath(10, "cross")])
    manager.update_paths(0.02)
    assert not app._paths.is_locked(10)


@pytest.mark.integration
def test_a_controller_pathfinder_press_skips_a_held_marker(app: _App) -> None:
    _cross(app)
    _held(app)
    InputManager(app).pathfinder(10)
    assert not app._paths.is_locked(10)


@pytest.mark.integration
def test_switching_the_remote_off_lets_go_of_every_marker(app: _App) -> None:
    _held(app)
    app._config.touch_remote.enabled = False
    InputManager(app).update_touch_remote(0.02)
    assert app._touch_remote.held_ids() == frozenset()


@pytest.mark.integration
def test_a_marker_no_longer_controlled_is_let_go(app: _App) -> None:
    _held(app, 11)
    app._controlled_ids = [10]
    InputManager(app).update_touch_remote(0.02)
    assert app._touch_remote.held_ids() == frozenset()


@pytest.mark.integration
def test_a_hold_switches_person_detection_off_in_the_running_config_and_the_file(app: _App) -> None:
    app._config.detection.enabled = True
    _held(app)
    manager = InputManager(app)
    manager.update_touch_remote(0.02)
    manager.update_touch_remote(0.02)
    assert app._config.detection.enabled is False
    assert load_config(app._config_path).detection.enabled is False
    assert app.detection_applied == [False]


@pytest.mark.integration
def test_without_a_hold_detection_stays_on(app: _App) -> None:
    app._config.detection.enabled = True
    InputManager(app).update_touch_remote(0.02)
    assert app._config.detection.enabled is True
    assert app.detection_applied == []


@pytest.mark.integration
def test_an_app_without_a_registry_is_left_alone(app: _App) -> None:
    del app._touch_remote
    _Keyboard.velocity = (1.0, 0.0, 0.0)
    manager = InputManager(app)
    manager.update_touch_remote(0.5)
    manager.update(1.0)
    assert _pos(app) == pytest.approx((1.0, 0.0, 1.6))


@pytest.mark.integration
def test_a_glide_stops_when_the_remote_lets_go(app: _App) -> None:
    token = _held(app)
    app._touch_remote.drive(token, 10, (0.0, 0.0), 0)
    app._touch_remote.act(token, 10, RemoteAction(GLIDE, (4.0, 0.0, 1.6)))
    manager = InputManager(app)
    manager.update_touch_remote(0.5)
    app._touch_remote.close(token)
    manager.update_touch_remote(0.5)
    _held(app)
    manager.update_touch_remote(0.5)
    assert _pos(app) == pytest.approx((1.0, 0.0, 1.6))


@pytest.mark.integration
def test_a_held_marker_this_station_has_no_marker_for_is_skipped(app: _App) -> None:
    del app._server.markers[11]
    token = _held(app, 11)
    app._touch_remote.drive(token, 11, (1.0, 0.0), 1)
    InputManager(app).update_touch_remote(0.5)
    assert _pos(app) == pytest.approx((0.0, 0.0, 1.6))


@pytest.mark.integration
def test_detection_goes_off_even_when_the_file_cannot_be_written(app: _App, tmp_path, caplog) -> None:  # noqa: ANN001
    app._config_path = str(tmp_path)  # a directory: the save raises
    app._config.detection.enabled = True
    _held(app)
    manager = InputManager(app)
    manager.update_touch_remote(0.02)
    manager.update_touch_remote(0.02)
    assert app._config.detection.enabled is False
    assert app.detection_applied == [False]
    assert "Failed to save person detection switched off." in caplog.text


@pytest.mark.integration
def test_a_glide_too_far_for_a_float_distance_lands_at_once(app: _App) -> None:
    app._server.markers[10].set_pos(1e308, 0.0, 1.6)
    token = _held(app)
    app._touch_remote.drive(token, 10, (0.0, 0.0), 0)
    app._touch_remote.act(token, 10, RemoteAction(GLIDE, (-1e308, 0.0, 1.6)))
    manager = InputManager(app)
    manager.update_touch_remote(0.02)
    assert _pos(app) == pytest.approx((-1e308, 0.0, 1.6))


@pytest.mark.integration
def test_a_failing_remote_step_is_logged_and_the_frame_carries_on(app: _App, caplog) -> None:  # noqa: ANN001
    _held(app)

    def boom() -> list:  # noqa: ANN202
        raise RuntimeError("boom")

    app._touch_remote.take = boom  # type: ignore[method-assign]
    InputManager(app).update_touch_remote(0.02)
    assert "Touch remote update failed this tick." in caplog.text


@pytest.mark.integration
def test_the_dead_man_holds_a_glide_until_drive_messages_return(app: _App, clock: _Clock) -> None:
    token = _held(app)
    app._touch_remote.drive(token, 10, (0.0, 0.0), 0)
    app._touch_remote.act(token, 10, RemoteAction(GLIDE, (10.0, 0.0, 1.6)))
    manager = InputManager(app)
    manager.update_touch_remote(0.5)
    clock.now += DRIVE_STALE_S + 0.01
    manager.update_touch_remote(1.0)
    assert _pos(app) == pytest.approx((1.0, 0.0, 1.6))
    app._touch_remote.drive(token, 10, (0.0, 0.0), 0)
    manager.update_touch_remote(1.0)
    assert _pos(app) == pytest.approx((3.0, 0.0, 1.6))


@pytest.mark.unit
def test_a_frame_says_whether_the_remote_is_still_talking(remote: TouchRemote, clock: _Clock) -> None:
    token = remote.open()
    remote.hold(token, 3)
    assert remote.take()[0].live is False
    remote.drive(token, 3, (0.0, 0.0), 0)
    assert remote.take()[0].live is True
    clock.now += DRIVE_STALE_S + 0.01
    assert remote.take()[0].live is False


@pytest.mark.integration
def test_z_stays_within_reach(app: _App) -> None:
    app._config.touch_remote.z_step_m = 1.0
    token = _held(app)
    app._touch_remote.drive(token, 10, (0.0, 0.0), 1000)
    manager = InputManager(app)
    for _ in range(12):
        app._touch_remote.drive(token, 10, (0.0, 0.0), 1000)
        manager.update_touch_remote(0.02)
    assert _pos(app)[2] == MAX_REACH_M


@pytest.mark.integration
def test_a_failing_detector_stop_is_reverted_like_a_hot_reload(app: _App) -> None:
    app._config.detection.enabled = True
    save_config(app._config, app._config_path)
    app._runtime_services._person_detector = object()

    def fail(_cfg) -> None:  # noqa: ANN001
        raise RuntimeError("stuck detector")

    app._runtime_services.apply_detection_change = fail
    _held(app)
    InputManager(app).update_touch_remote(0.02)
    assert app._runtime_services._person_detector is None
    assert app._config.detection.enabled is True
    assert load_config(app._config_path).detection.enabled is True


@pytest.mark.integration
def test_a_glide_never_passes_to_the_remote_that_takes_the_marker_over(app: _App) -> None:
    first = _held(app)
    app._touch_remote.drive(first, 10, (0.0, 0.0), 0)
    app._touch_remote.act(first, 10, RemoteAction(GLIDE, (10.0, 0.0, 1.6)))
    manager = InputManager(app)
    manager.update_touch_remote(0.5)
    second = _held(app)
    app._touch_remote.drive(second, 10, (0.0, 0.0), 0)
    manager.update_touch_remote(1.0)
    assert _pos(app) == pytest.approx((1.0, 0.0, 1.6))


@pytest.mark.integration
def test_one_marker_failing_never_costs_another_its_presses(app: _App, caplog) -> None:  # noqa: ANN001
    first, second = _held(app, 10), _held(app, 11)
    app._touch_remote.act(first, 10, RemoteAction(PATHFINDER))
    app._touch_remote.act(second, 11, RemoteAction(RESET))
    manager = InputManager(app)

    def boom(_marker_id, *, remote: bool = False) -> None:  # noqa: ANN001
        raise RuntimeError("boom")

    manager.pathfinder = boom  # type: ignore[method-assign]
    manager.update_touch_remote(0.02)
    assert _pos(app, 11) == pytest.approx((5.0, 6.0, 7.0))
    assert "Touch remote update failed this tick." in caplog.text


@pytest.mark.integration
def test_detection_switched_back_on_during_a_hold_stays_on(app: _App) -> None:
    app._config.detection.enabled = True
    _held(app)
    manager = InputManager(app)
    manager.update_touch_remote(0.02)
    assert app._config.detection.enabled is False
    app._config.detection = replace(app._config.detection, enabled=True)
    manager.update_touch_remote(0.02)
    assert app._config.detection.enabled is True
    assert app.detection_applied == [False]


@pytest.mark.integration
def test_switching_detection_off_never_edits_the_config_object_the_detector_holds(app: _App) -> None:
    app._config.detection.enabled = True
    held_by_detector = app._config.detection
    _held(app)
    InputManager(app).update_touch_remote(0.02)
    assert held_by_detector.enabled is True
    assert app._config.detection is not held_by_detector


@pytest.mark.unit
def test_a_frame_says_its_marker_was_newly_taken_once(remote: TouchRemote) -> None:
    token = remote.open()
    remote.hold(token, 3)
    assert [f.new for f in remote.take()] == [True]
    assert [f.new for f in remote.take()] == [False]
    remote.hold(token, 3)
    assert [f.new for f in remote.take()] == [False]
    remote.hold(remote.open(), 3)
    assert [f.new for f in remote.take()] == [True]


@pytest.mark.integration
def test_a_hold_let_go_before_any_frame_leaves_detection_on(app: _App) -> None:
    app._config.detection.enabled = True
    _held(app, 11)
    app._controlled_ids = [10]
    InputManager(app).update_touch_remote(0.02)
    assert app._config.detection.enabled is True
    assert app.detection_applied == []


@pytest.mark.integration
def test_a_switch_off_during_a_restore_s_restart_is_logged_as_the_restart(
    app: _App, caplog: pytest.LogCaptureFixture
) -> None:
    from openfollow import restore_hold

    app._config.detection.enabled = True
    restore_hold.hold(app._config_path)
    _held(app)
    with caplog.at_level("INFO"):
        InputManager(app).update_touch_remote(0.02)
    assert app._config.detection.enabled is False
    assert "OpenFollow is restarting after a restore" in caplog.text
    assert [r for r in caplog.records if r.levelname == "ERROR"] == []
