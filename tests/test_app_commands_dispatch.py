# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for runtime/app_commands.py dispatch helpers.

The module glues web-UI commands onto ``OpenFollowApp`` via the
``WebCommandQueue`` – we test each ``check_*`` entry point against a
SimpleNamespace app graph.
"""

from __future__ import annotations

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
