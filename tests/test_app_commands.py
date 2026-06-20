# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Unit tests for openfollow.runtime.app_commands.

Covers ``restart_app`` (systemd-vs-dev re-exec choice) and the
``check_update_request`` dispatch guard that drives the signed-``.deb``
update worker.
"""

from __future__ import annotations

from typing import Any

import pytest

from openfollow.runtime import app_commands

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# restart_app
# ---------------------------------------------------------------------------


def test_restart_app_shuts_down_canvas_and_calls_execv(monkeypatch) -> None:
    shutdown_calls: list[bool] = []
    close_calls: list[bool] = []

    class _RuntimeServices:
        def shutdown(self) -> None:
            shutdown_calls.append(True)

    class _Canvas:
        def close(self) -> None:
            close_calls.append(True)

    class _App:
        _runtime_services = _RuntimeServices()
        _canvas = _Canvas()

    captured_execv: list[tuple[str, list[str]]] = []

    def _fake_execv(path: str, argv: list[str]) -> None:
        captured_execv.append((path, list(argv)))
        raise SystemExit("execv stub")

    monkeypatch.setattr(app_commands.os, "execv", _fake_execv)
    monkeypatch.setattr(app_commands.sys, "executable", "/usr/bin/python3-stub")
    monkeypatch.setattr(app_commands.sys, "argv", ["openfollow", "--cfg=x.toml"])
    # Pin the non-systemd path: without INVOCATION_ID the process is not a
    # systemd unit, so restart_app re-execs in place (the dev/poetry-run case).
    monkeypatch.delenv("INVOCATION_ID", raising=False)

    with pytest.raises(SystemExit, match="execv stub"):
        app_commands.restart_app(_App())  # type: ignore[arg-type]

    # Cleanup ran before execv.
    assert shutdown_calls == [True]
    assert close_calls == [True]
    # execv was called with the expected argv prefix.
    assert captured_execv == [
        ("/usr/bin/python3-stub", ["/usr/bin/python3-stub", "openfollow", "--cfg=x.toml"]),
    ]


def test_restart_app_exits_for_systemd_instead_of_execv(monkeypatch) -> None:
    """Under systemd (INVOCATION_ID set, Restart=always) restart_app must
    exit cleanly so the unit is respawned fresh (session.sh -> cage -> app),
    NOT os.execv in place – an in-process re-exec reconnects to the existing
    Cage Wayland session and the on-screen GUI does not reliably recover. The
    pre-exit cleanup (runtime shutdown + canvas close) must still run."""
    shutdown_calls: list[bool] = []
    close_calls: list[bool] = []

    class _RuntimeServices:
        def shutdown(self) -> None:
            shutdown_calls.append(True)

    class _Canvas:
        def close(self) -> None:
            close_calls.append(True)

    class _App:
        _runtime_services = _RuntimeServices()
        _canvas = _Canvas()

    def _fail_execv(path: str, argv: list[str]) -> None:
        pytest.fail("must not os.execv under systemd – should exit for a unit respawn")

    monkeypatch.setattr(app_commands.os, "execv", _fail_execv)
    monkeypatch.setenv("INVOCATION_ID", "deadbeefcafe")

    with pytest.raises(SystemExit) as excinfo:
        app_commands.restart_app(_App())  # type: ignore[arg-type]

    # Clean exit code 0 → systemd Restart=always respawns the unit.
    assert excinfo.value.code == 0
    # Cleanup still ran before exiting.
    assert shutdown_calls == [True]
    assert close_calls == [True]


# ---------------------------------------------------------------------------
# check_update_request – worker-teardown / reap dispatch guard
# ---------------------------------------------------------------------------


def _dispatch_app(worker: Any):  # noqa: ANN202
    """App stand-in for check_update_request with a real queue."""
    from openfollow.services import WebCommandQueue

    spawned: dict[str, Any] = {}

    class _App:
        _web_commands = WebCommandQueue()
        _update_worker = worker

        def _run_deb_update(self, request: dict[str, str]) -> None:
            spawned["target"] = "deb"
            spawned["request"] = request

        def _run_local_update(self, request: dict[str, str]) -> None:
            spawned["target"] = "deb-local"
            spawned["request"] = request

    return _App(), spawned


def test_check_update_does_not_drop_request_during_worker_teardown(monkeypatch) -> None:
    """Regression: a failed update sets a terminal status before its
    daemon thread fully exits, so a fast retry can land while the old
    worker still reports ``is_alive() == True``. The housekeeping tick
    must NOT consume-and-discard that request – it has to stay queued so
    the next tick (after the worker exits) processes it. The pre-fix
    code consumed first, then saw the live worker and dropped the
    request, stranding the web UI on a permanent ``running`` status."""

    class _StillAliveWorker:
        def is_alive(self) -> bool:
            return True

    app, _spawned = _dispatch_app(_StillAliveWorker())
    assert app._web_commands.request_deb_update("openfollow") is True

    # No worker may be spawned while the prior one is tearing down.
    monkeypatch.setattr(
        app_commands.threading,
        "Thread",
        lambda **_kw: pytest.fail("must not spawn a worker while one is still alive"),
    )

    app_commands.check_update_request(app)

    # The request survives – it was NOT consumed/dropped. The next tick
    # (worker dead) is free to pick it up.
    assert app._web_commands.consume_update_requested() == {
        "kind": "deb",
        "service_name": "openfollow",
    }


def test_check_update_alive_worker_no_pending_request_is_noop(monkeypatch) -> None:
    """A live worker with no queued request must leave status untouched
    (no spurious ``running``) and spawn nothing – the steady-state of a
    normal in-progress update on every housekeeping tick."""

    class _StillAliveWorker:
        def is_alive(self) -> bool:
            return True

    app, _spawned = _dispatch_app(_StillAliveWorker())
    # Worker is mid-run and set its own status; no new request is queued.
    app._web_commands.set_update_status("running", message="Working...")

    monkeypatch.setattr(
        app_commands.threading,
        "Thread",
        lambda **_kw: pytest.fail("must not spawn a worker"),
    )

    app_commands.check_update_request(app)

    status = app._web_commands.get_update_status()
    assert status["state"] == "running"
    assert status["message"] == "Working..."


def test_check_update_reaps_dead_worker_then_processes_queued_request(monkeypatch) -> None:
    """Once the worker has exited (``is_alive() == False``) the helper
    reaps it (``_update_worker`` reset to None) and the still-queued
    request is dispatched on the next tick."""

    class _DeadWorker:
        def is_alive(self) -> bool:
            return False

    app, spawned = _dispatch_app(_DeadWorker())
    assert app._web_commands.request_deb_update("openfollow") is True

    class _FakeThread:
        def __init__(self, *, target, args, daemon, name):  # noqa: ANN001
            self._target = target
            self._args = args

        def start(self) -> None:
            self._target(*self._args)

        def is_alive(self) -> bool:
            return True

    monkeypatch.setattr(app_commands.threading, "Thread", _FakeThread)

    app_commands.check_update_request(app)

    # Dead worker reaped, fresh worker spawned, request consumed.
    assert isinstance(app._update_worker, _FakeThread)
    assert spawned["target"] == "deb"
    assert spawned["request"]["service_name"] == "openfollow"
    assert app._web_commands.consume_update_requested() is None


def test_check_update_unknown_kind_is_ignored(monkeypatch) -> None:
    """A queued request with an unrecognised ``kind`` (e.g. a stale git-pull
    payload from older code) must be dropped without spawning a worker –
    there is no longer a git fallback target."""
    app, spawned = _dispatch_app(None)
    # Stuff an unknown-kind request straight onto the queue.
    app._web_commands._queue_update_request("repo", service_name="openfollow")

    monkeypatch.setattr(
        app_commands.threading,
        "Thread",
        lambda **_kw: pytest.fail("must not spawn a worker for an unknown update kind"),
    )

    app_commands.check_update_request(app)

    assert spawned == {}
    assert app._update_worker is None
