# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for runtime/app_commands.py dispatch helpers.

The module glues web-UI commands onto ``OpenFollowApp`` via the
``WebCommandQueue`` – we test each ``check_*`` entry point against a
SimpleNamespace app graph.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from openfollow.runtime import app_commands
from openfollow.services import WebCommandQueue

pytestmark = pytest.mark.unit


def _make_app(**overrides):  # noqa: ANN202
    defaults = {
        "_update_worker": None,
        "_web_commands": WebCommandQueue(),
        "_restart_called": False,
        "_enter_button_detection_called": False,
        "_exit_button_detection_called": False,
        "_run_deb_update_args": [],
        "_run_local_update_args": [],
    }
    defaults.update(overrides)
    app = SimpleNamespace(**defaults)

    def _restart_app() -> None:
        app._restart_called = True

    def _enter_button_detection() -> None:
        app._enter_button_detection_called = True

    def _exit_button_detection() -> None:
        app._exit_button_detection_called = True

    def _run_deb_update(request) -> None:  # noqa: ANN001
        app._run_deb_update_args.append(request)

    def _run_local_update(request) -> None:  # noqa: ANN001
        app._run_local_update_args.append(request)

    app._restart_app = _restart_app
    app._enter_button_detection = _enter_button_detection
    app._exit_button_detection = _exit_button_detection
    app._run_deb_update = _run_deb_update
    app._run_local_update = _run_local_update
    return app


# ---------------------------------------------------------------------------
# check_restart_request
# ---------------------------------------------------------------------------


def test_check_restart_triggers_restart_when_requested() -> None:
    app = _make_app()
    app._web_commands.request_restart()

    app_commands.check_restart_request(app)

    assert app._restart_called is True


def test_check_restart_is_noop_when_no_request() -> None:
    app = _make_app()
    app_commands.check_restart_request(app)
    assert app._restart_called is False


def test_check_restart_skips_when_update_worker_alive() -> None:
    app = _make_app()
    app._web_commands.request_restart()

    class _LiveWorker:
        def is_alive(self) -> bool:
            return True

    app._update_worker = _LiveWorker()
    app_commands.check_restart_request(app)
    assert app._restart_called is False

    # Worker finishes – next tick must now fire the restart, which is
    # only possible if the request stayed armed across the skipped tick.
    app._update_worker = None
    app_commands.check_restart_request(app)
    assert app._restart_called is True


# ---------------------------------------------------------------------------
# check_button_detection_request
# ---------------------------------------------------------------------------


def test_check_button_detection_enters_wizard_when_requested() -> None:
    app = _make_app()
    app._web_commands.request_button_detection()

    app_commands.check_button_detection_request(app)

    assert app._enter_button_detection_called is True


def test_check_button_detection_is_noop_when_no_request() -> None:
    app = _make_app()
    app_commands.check_button_detection_request(app)
    assert app._enter_button_detection_called is False


def test_check_button_detection_exits_wizard_when_cancel_requested() -> None:
    """Web-queued cancel drains on main loop and calls exit_button_detection."""
    app = _make_app()
    app._web_commands.request_button_detection_cancel()

    app_commands.check_button_detection_request(app)

    assert app._exit_button_detection_called is True
    # Start path untouched by a cancel-only request.
    assert app._enter_button_detection_called is False


def test_check_button_detection_cancel_is_noop_when_not_requested() -> None:
    app = _make_app()
    app_commands.check_button_detection_request(app)
    assert app._exit_button_detection_called is False


# ---------------------------------------------------------------------------
# check_update_request
# ---------------------------------------------------------------------------


def test_check_update_spawns_worker_thread_with_request(monkeypatch) -> None:
    app = _make_app()
    assert app._web_commands.request_deb_update("openfollow")

    spawned: dict = {}

    class _FakeThread:
        def __init__(self, *, target, args, daemon, name):  # noqa: ANN001
            spawned["target"] = target
            spawned["args"] = args
            spawned["daemon"] = daemon
            spawned["name"] = name
            self.started = False

        def start(self) -> None:
            self.started = True

        def is_alive(self) -> bool:
            return self.started

    monkeypatch.setattr(app_commands.threading, "Thread", _FakeThread)

    app_commands.check_update_request(app)

    assert isinstance(app._update_worker, _FakeThread)
    assert app._update_worker.started is True
    assert spawned["daemon"] is True
    assert spawned["name"] == "WebUpdateWorker"
    assert spawned["target"] is app._run_deb_update
    # The request dict is passed positionally to the worker.
    assert spawned["args"][0]["service_name"] == "openfollow"


def _capture_spawned_thread(monkeypatch) -> dict:  # noqa: ANN202
    """Patch threading.Thread to record the spawned target/args."""
    spawned: dict = {}

    class _FakeThread:
        def __init__(self, *, target, args, daemon, name):  # noqa: ANN001
            spawned["target"] = target
            spawned["args"] = args
            self.started = False

        def start(self) -> None:
            self.started = True

        def is_alive(self) -> bool:
            return self.started

    monkeypatch.setattr(app_commands.threading, "Thread", _FakeThread)
    return spawned


def test_check_update_routes_deb_kind_to_deb_worker(monkeypatch) -> None:
    app = _make_app()
    assert app._web_commands.request_deb_update("openfollow")
    spawned = _capture_spawned_thread(monkeypatch)

    app_commands.check_update_request(app)

    assert spawned["target"] is app._run_deb_update
    assert spawned["args"][0]["kind"] == "deb"


def test_check_update_routes_deb_local_kind_to_local_worker(monkeypatch) -> None:
    app = _make_app()
    assert app._web_commands.request_local_update("openfollow", deb_path="/tmp/openfollow-update-x.deb")
    spawned = _capture_spawned_thread(monkeypatch)

    app_commands.check_update_request(app)

    assert spawned["target"] is app._run_local_update
    assert spawned["args"][0]["kind"] == "deb-local"
    assert spawned["args"][0]["deb_path"] == "/tmp/openfollow-update-x.deb"


def test_check_update_is_noop_without_request() -> None:
    app = _make_app()
    app_commands.check_update_request(app)
    assert app._update_worker is None


def test_check_update_rejects_second_request_while_running(monkeypatch) -> None:
    app = _make_app()

    class _AliveWorker:
        def is_alive(self) -> bool:
            return True

    app._update_worker = _AliveWorker()
    # Queue a *new* request to simulate a second click; the helper must
    # surface the "already running" status instead of spawning a worker.
    assert app._web_commands.request_deb_update("openfollow") is True

    monkeypatch.setattr(
        app_commands.threading,
        "Thread",
        lambda **_kw: pytest.fail("Thread must not be spawned while running"),
    )

    app_commands.check_update_request(app)
    status = app._web_commands.get_update_status()
    assert status["state"] == "running"
    assert "already in progress" in status["message"]


# ---------------------------------------------------------------------------
# check_controller_slot_actions
# ---------------------------------------------------------------------------


class _SlotRecorder:
    def __init__(self, refs: tuple[str, ...] = ("r0", "r1")) -> None:
        self.refs = refs
        self.calls: list[tuple[str, int]] = []
        self.raises_on: set[int] = set()

    def slot_ref(self, index: int) -> str | None:
        return self.refs[index] if 0 <= index < len(self.refs) else None

    def identify_slot(self, index: int) -> bool:
        self.calls.append(("identify", index))
        if index in self.raises_on:
            raise RuntimeError("device gone")
        return True

    def forget_slot(self, index: int) -> bool:
        self.calls.append(("forget", index))
        return True


def test_slot_actions_run_once_in_the_order_they_were_clicked() -> None:
    app = _make_app(_input_manager=_SlotRecorder())
    app._web_commands.request_slot_action("identify", 1, "r1")
    app._web_commands.request_slot_action("forget", 0, "r0")
    app_commands.check_controller_slot_actions(app)
    app_commands.check_controller_slot_actions(app)
    assert app._input_manager.calls == [("identify", 1), ("forget", 0)]


def test_an_unknown_slot_action_does_nothing() -> None:
    app = _make_app(_input_manager=_SlotRecorder())
    app._web_commands.request_slot_action("delete", 0, "r0")
    app_commands.check_controller_slot_actions(app)
    assert app._input_manager.calls == []


@pytest.mark.parametrize(("index", "ref"), [(0, "r1"), (0, ""), (5, "r0")])
def test_a_slot_that_changed_since_the_click_is_left_alone(caplog, index: int, ref: str) -> None:
    app = _make_app(_input_manager=_SlotRecorder())
    app._web_commands.request_slot_action("forget", index, ref)
    with caplog.at_level("INFO", logger=app_commands.__name__):
        app_commands.check_controller_slot_actions(app)
    assert app._input_manager.calls == []
    assert f"C{index + 1} changed since it was clicked; forget not applied" in caplog.text


def test_a_failing_slot_action_does_not_drop_the_next(caplog) -> None:
    app = _make_app(_input_manager=_SlotRecorder())
    app._input_manager.raises_on = {0}
    app._web_commands.request_slot_action("identify", 0, "r0")
    app._web_commands.request_slot_action("identify", 1, "r1")
    with caplog.at_level("ERROR", logger=app_commands.__name__):
        app_commands.check_controller_slot_actions(app)
    assert app._input_manager.calls == [("identify", 0), ("identify", 1)]
    assert "Controller slot action identify on C1 failed" in caplog.text


def test_slot_actions_without_input_are_dropped() -> None:
    app = _make_app(_input_manager=None)
    app._web_commands.request_slot_action("identify", 0, "r0")
    app_commands.check_controller_slot_actions(app)
    assert app._web_commands.consume_slot_actions() == []


# ---------------------------------------------------------------------------
# check_camera_setup_requests
# ---------------------------------------------------------------------------


class _Picam:
    """Records what the main loop does to the Pi Camera pipeline."""

    def __init__(self, source_type: str = "picam", *, fails: str = "") -> None:
        self._source_type = source_type
        self._input_config = {"picam_width": 1920}
        self._fails = fails
        self.calls: list[str] = []
        self.swaps: list[tuple[str, dict]] = []

    def release_source(self) -> None:
        self.calls.append("release")
        if self._fails == "release":
            raise RuntimeError("pipeline stuck")

    def swap_input(self, source_type: str, config: dict) -> None:
        self.calls.append("rebuild")
        self.swaps.append((source_type, config))
        if self._fails == "rebuild":
            raise RuntimeError("pipeline stuck")


def _release_from_web(app, timeout: float = 5.0) -> bool:  # noqa: ANN001
    """The web thread's answer to a release, with the main loop polling as it does."""
    answer: list[bool] = []
    worker = threading.Thread(target=lambda: answer.append(app._web_commands.release_camera(timeout)))
    worker.start()
    while worker.is_alive():
        app_commands.check_camera_setup_requests(app)
        worker.join(timeout=0.005)
    return answer[0]


def test_a_live_camera_rebuilds_the_pi_camera_pipeline_once() -> None:
    receiver = _Picam()
    app = _make_app(_video_receiver=receiver)
    app._web_commands.request_video_rebuild()
    app_commands.check_camera_setup_requests(app)
    app_commands.check_camera_setup_requests(app)
    assert receiver.swaps == [("picam", {"picam_width": 1920})]
    # Its own copy: the rebuild must not share the receiver's dict.
    assert receiver.swaps[0][1] is not receiver._input_config


def test_no_request_touches_nothing() -> None:
    receiver = _Picam()
    app_commands.check_camera_setup_requests(_make_app(_video_receiver=receiver))
    assert receiver.calls == []


def test_a_release_stops_the_pipeline_and_answers_the_web_thread() -> None:
    receiver = _Picam()
    app = _make_app(_video_receiver=receiver)
    assert _release_from_web(app) is True
    assert receiver.calls == ["release"]


def test_release_before_rebuild_in_one_pass() -> None:
    receiver = _Picam()
    app = _make_app(_video_receiver=receiver)
    assert app._web_commands.release_camera(0) is False  # the web side stopped waiting; the request stands
    app._web_commands.request_video_rebuild()
    app_commands.check_camera_setup_requests(app)
    assert receiver.calls == ["release", "rebuild"]


@pytest.mark.parametrize("receiver", [None, _Picam("rtsp")], ids=["no-receiver", "another-source"])
def test_only_an_active_pi_camera_is_touched(receiver) -> None:  # noqa: ANN001
    app = _make_app(_video_receiver=receiver)
    assert _release_from_web(app) is True  # nothing streams from the camera: safe to unload
    app._web_commands.request_video_rebuild()
    app_commands.check_camera_setup_requests(app)
    assert receiver is None or receiver.calls == []
    assert app._web_commands.consume_video_rebuild_requested() is False  # taken off the queue


def test_a_stuck_pipeline_is_not_confirmed(caplog) -> None:  # noqa: ANN001
    receiver = _Picam(fails="release")
    app = _make_app(_video_receiver=receiver)
    with caplog.at_level("ERROR", logger=app_commands.__name__):
        assert _release_from_web(app, timeout=0.2) is False
    assert receiver.calls == ["release"]
    assert "Stopping the Pi Camera pipeline failed." in caplog.text


def test_a_failed_rebuild_is_logged_not_raised(caplog) -> None:  # noqa: ANN001
    receiver = _Picam(fails="rebuild")
    app = _make_app(_video_receiver=receiver)
    app._web_commands.request_video_rebuild()
    with caplog.at_level("ERROR", logger=app_commands.__name__):
        app_commands.check_camera_setup_requests(app)
    assert "Rebuilding the Pi Camera pipeline failed." in caplog.text


def test_an_unanswered_release_times_out() -> None:
    assert _make_app()._web_commands.release_camera(0.01) is False
