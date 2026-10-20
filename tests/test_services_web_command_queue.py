# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Unit tests for ``openfollow.services.WebCommandQueue``.

The queue carries web-UI commands (restart / update / button-detection) to
the main thread. We verify the state machine transitions around update
requests and the consume/arm semantics of every boolean flag – especially
the "reject while queued/running/restarting" path that prevents double
spawns from a second click.
"""

from __future__ import annotations

import pytest

from openfollow.services import WebCommandQueue

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# restart
# ---------------------------------------------------------------------------


def test_restart_request_consume_once() -> None:
    q = WebCommandQueue()
    assert q.consume_restart_requested() is False
    q.request_restart()
    assert q.consume_restart_requested() is True
    # Second consume drains the flag.
    assert q.consume_restart_requested() is False


def test_a_restart_request_is_taken_only_after_its_grace() -> None:
    now = [10.0]
    q = WebCommandQueue(clock=lambda: now[0])
    q.request_restart()
    now[0] = 10.4
    assert q.consume_restart_requested(grace_s=0.5) is False
    now[0] = 10.5
    assert q.consume_restart_requested(grace_s=0.5) is True
    assert q.consume_restart_requested(grace_s=0.5) is False


def test_a_restart_allowed_past_an_update_check_waits_its_grace_too() -> None:
    now = [10.0]
    q = WebCommandQueue(clock=lambda: now[0])
    assert q.request_restart_unless_updating() is True
    assert q.consume_restart_requested(grace_s=0.5) is False
    now[0] = 10.5
    assert q.consume_restart_requested(grace_s=0.5) is True


# ---------------------------------------------------------------------------
# update-available holder (drives the banner + footer flag)
# ---------------------------------------------------------------------------


def test_update_available_defaults_empty() -> None:
    assert WebCommandQueue().get_update_available() == ""


def test_set_and_get_update_available_strips() -> None:
    q = WebCommandQueue()
    q.set_update_available("  0.4.0 ")
    assert q.get_update_available() == "0.4.0"


def test_update_available_can_be_cleared() -> None:
    q = WebCommandQueue()
    q.set_update_available("0.4.0")
    q.set_update_available("")
    assert q.get_update_available() == ""


def test_update_available_none_clears() -> None:
    q = WebCommandQueue()
    q.set_update_available("0.4.0")
    q.set_update_available(None)
    assert q.get_update_available() == ""


def test_update_available_independent_of_install_status() -> None:
    # A queued install must not wipe the discovered-version banner state.
    q = WebCommandQueue()
    q.set_update_available("0.4.0")
    q.set_update_status("running", message="installing")
    assert q.get_update_available() == "0.4.0"


# ---------------------------------------------------------------------------
# button detection
# ---------------------------------------------------------------------------


def test_button_detection_request_and_active_flags_are_independent() -> None:
    q = WebCommandQueue()
    assert q.is_button_detection_active() is False
    assert q.consume_button_detection_requested() is False

    q.request_button_detection()
    assert q.consume_button_detection_requested() is True
    # The "active" flag must be a separate event from the request edge.
    assert q.is_button_detection_active() is False

    q.set_button_detection_active(True)
    assert q.is_button_detection_active() is True
    q.set_button_detection_active(False)
    assert q.is_button_detection_active() is False


def test_button_detection_cancel_is_a_separate_edge() -> None:
    """Cancel request is its own one-shot edge, independent of start and active."""
    q = WebCommandQueue()
    assert q.consume_button_detection_cancel_requested() is False

    q.request_button_detection_cancel()
    assert q.consume_button_detection_cancel_requested() is True
    # One-shot: a second consume sees nothing.
    assert q.consume_button_detection_cancel_requested() is False

    # Independent of the start-request edge.
    q.request_button_detection()
    assert q.consume_button_detection_cancel_requested() is False
    assert q.consume_button_detection_requested() is True


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


def test_request_deb_update_stores_normalized_request() -> None:
    q = WebCommandQueue()
    # Blank service_name normalises to "openfollow"; kind is recorded.
    assert q.request_deb_update("")
    request = q.consume_update_requested()
    assert request is not None
    assert request["kind"] == "deb"
    assert request["service_name"] == "openfollow"

    status = q.get_update_status()
    assert status["state"] == "queued"
    assert "queued" in status["message"].lower()


def test_request_local_update_carries_deb_path() -> None:
    q = WebCommandQueue()
    assert q.request_local_update("openfollow", deb_path="/tmp/openfollow-update-x.deb")
    request = q.consume_update_requested()
    assert request is not None
    assert request["kind"] == "deb-local"
    assert request["service_name"] == "openfollow"
    assert request["deb_path"] == "/tmp/openfollow-update-x.deb"


def test_request_update_rejected_while_queued() -> None:
    q = WebCommandQueue()
    assert q.request_deb_update("openfollow") is True
    # Second click while state == "queued" must be refused.
    assert q.request_deb_update("openfollow") is False


def test_request_update_rejected_while_running() -> None:
    q = WebCommandQueue()
    q.set_update_status(state="running", message="installing")
    assert q.request_deb_update("openfollow") is False


def test_request_update_rejected_while_restarting() -> None:
    q = WebCommandQueue()
    q.set_update_status(state="restarting", message="exec")
    assert q.request_deb_update("openfollow") is False


def test_request_update_accepted_after_failed_status() -> None:
    q = WebCommandQueue()
    q.set_update_status(state="failed", error="boom")
    assert q.request_deb_update("openfollow") is True


def test_consume_update_requested_returns_none_when_unset() -> None:
    q = WebCommandQueue()
    assert q.consume_update_requested() is None


def test_consume_update_requested_isolates_caller_mutations() -> None:
    q = WebCommandQueue()
    assert q.request_local_update("svc", deb_path="/orig.deb")
    first = q.consume_update_requested()
    assert first is not None
    assert first == {
        "kind": "deb-local",
        "service_name": "svc",
        "deb_path": "/orig.deb",
    }

    # Mutate every field of the returned dict.
    first["kind"] = "corrupted"
    first["service_name"] = "corrupted"
    first["deb_path"] = "/corrupted"

    # Reset the status gate so a fresh request is accepted (in
    # production the worker transitions state to completed/failed).
    q.set_update_status(state="idle")

    # Fresh cycle – the queue's new payload must be pristine, and the
    # dict identity must differ from the one we just mutated.
    assert q.request_local_update("newsvc", deb_path="/new.deb")
    second = q.consume_update_requested()
    assert second == {
        "kind": "deb-local",
        "service_name": "newsvc",
        "deb_path": "/new.deb",
    }
    assert second is not first


def test_get_update_status_defaults_to_idle() -> None:
    q = WebCommandQueue()
    status = q.get_update_status()
    assert status == {"state": "idle", "message": "", "error": ""}


def test_set_update_status_overwrites_and_copies() -> None:
    q = WebCommandQueue()
    q.set_update_status(state="running", message="pulling", error="")
    first = q.get_update_status()
    first["state"] = "mutated"
    # The internal status is returned by copy each time.
    assert q.get_update_status()["state"] == "running"


def test_a_restart_is_requested_when_no_update_is_busy() -> None:
    q = WebCommandQueue()
    assert q.request_restart_unless_updating() is True
    assert q.consume_restart_requested() is True


@pytest.mark.parametrize("state", ["queued", "running", "restarting"])
def test_a_restart_is_refused_while_an_update_is_busy(state: str) -> None:
    q = WebCommandQueue()
    q.set_update_status(state)
    assert q.request_restart_unless_updating() is False
    assert q.consume_restart_requested() is False


@pytest.mark.parametrize(
    ("state", "busy"),
    [("idle", False), ("queued", True), ("running", True), ("restarting", True), ("failed", False), ("done", False)],
)
def test_an_update_is_busy_from_queued_until_its_install_ends(state: str, busy: bool) -> None:
    q = WebCommandQueue()
    q.set_update_status(state)
    assert q.is_update_busy() is busy
    # Asking requests nothing.
    assert q.consume_restart_requested() is False


def test_a_restart_and_an_update_cannot_both_go_ahead() -> None:
    """The check and the request happen under the lock update queueing takes,
    so an update queued at the same moment is either seen or not started."""
    import threading

    q = WebCommandQueue()
    result: list[bool] = []
    with q._update_lock:
        worker = threading.Thread(target=lambda: result.append(q.request_restart_unless_updating()))
        worker.start()
        worker.join(timeout=0.2)
        assert worker.is_alive(), "decided without the update lock"
        # An update queued while the restart waits for the lock wins.
        q._update_status = {"state": "queued", "message": "", "error": ""}
    worker.join(timeout=5)
    assert result == [False]
    assert q.consume_restart_requested() is False


def test_a_change_runs_then_its_restart_is_requested() -> None:
    q = WebCommandQueue()
    ran: list[bool] = []
    assert q.restart_after(lambda: ran.append(q.is_restart_pending())) is True
    # Requested after the change, never before it.
    assert ran == [False]
    assert q.consume_restart_requested() is True


@pytest.mark.parametrize("state", ["queued", "running", "restarting"])
def test_a_change_waiting_on_a_restart_is_not_made_while_an_update_is_busy(state: str) -> None:
    q = WebCommandQueue()
    q.set_update_status(state)
    ran: list[bool] = []
    assert q.restart_after(lambda: ran.append(True)) is False
    assert ran == []
    assert q.consume_restart_requested() is False


def test_a_change_that_fails_requests_no_restart() -> None:
    q = WebCommandQueue()

    def fail() -> None:
        raise OSError("disk full")

    with pytest.raises(OSError):
        q.restart_after(fail)
    assert q.consume_restart_requested() is False
    # The lock is free again.
    assert q.request_deb_update("openfollow") is True


def test_an_update_asked_for_during_a_change_is_refused_for_its_restart(monkeypatch) -> None:
    import threading

    import openfollow.services as services_module

    monkeypatch.setattr(services_module, "clear_detached_update_state", lambda: None)
    q = WebCommandQueue()
    seen: list[tuple[bool, bool]] = []
    workers: list[threading.Thread] = []

    def change() -> None:
        worker = threading.Thread(
            target=lambda: seen.append((q.request_deb_update("x"), q.consume_restart_requested()))
        )
        worker.start()
        worker.join(timeout=0.2)
        assert worker.is_alive(), "an update was queued during the change"
        workers.append(worker)

    assert q.restart_after(change) is True
    workers[0].join(timeout=5)
    # It waited for the change, then found the restart pending.
    assert seen == [(False, True)]
    assert q.get_update_status()["state"] == "idle"


# ---------------------------------------------------------------------------
# A pending restart and an update never both act
# ---------------------------------------------------------------------------


@pytest.fixture
def no_state_file(monkeypatch, tmp_path):
    import openfollow.services as services_module

    path = tmp_path / "update-state.json"
    monkeypatch.setattr(services_module, "_DETACHED_UPDATE_STATE_FILE", str(path))
    return path


@pytest.mark.parametrize("queue", ["deb", "local"])
def test_an_update_is_refused_while_a_restart_waits_its_grace(queue: str, no_state_file) -> None:
    now = [10.0]
    q = WebCommandQueue(clock=lambda: now[0])
    q.request_restart()
    queued = q.request_deb_update("x") if queue == "deb" else q.request_local_update("x", deb_path="/tmp/b")
    assert queued is False
    assert q.get_update_status()["state"] == "idle"
    assert q.consume_update_requested() is None
    now[0] = 10.5
    assert q.consume_restart_requested(grace_s=0.5) is True


def test_an_update_is_refused_once_a_restart_is_taken(no_state_file) -> None:
    q = WebCommandQueue()
    q.request_restart()
    assert q.consume_restart_requested() is True
    assert q.is_restart_pending() is True
    assert q.request_deb_update("x") is False


def test_a_restart_is_pending_from_its_request() -> None:
    q = WebCommandQueue()
    assert q.is_restart_pending() is False
    q.request_restart()
    assert q.is_restart_pending() is True


@pytest.mark.parametrize("state", ["queued", "running", "restarting"])
def test_a_pending_restart_is_held_while_an_update_is_busy(state: str) -> None:
    q = WebCommandQueue()
    q.request_restart()
    q.set_update_status(state)
    assert q.consume_restart_requested() is False
    # Held, not dropped: it goes ahead once the update has ended without restarting.
    q.set_update_status("failed")
    assert q.consume_restart_requested() is True


# ---------------------------------------------------------------------------
# An installer that ends without stopping this process
# ---------------------------------------------------------------------------


def _write_state(path, state: str, message: str = "", error: str = "") -> None:
    import json

    path.write_text(json.dumps({"state": state, "message": message, "error": error, "ts": 1}))


def test_a_failed_install_ends_the_busy_state_with_its_reason(no_state_file) -> None:
    q = WebCommandQueue()
    q.set_update_status("restarting", "Installing version 0.5.1")
    _write_state(no_state_file, "failed", "Update failed.", "E: unmet dependencies")
    assert q.settle_finished_install() == {
        "state": "failed",
        "message": "Update failed.",
        "error": "E: unmet dependencies",
    }
    assert q.is_update_busy() is False
    assert q.request_restart_unless_updating() is True


def test_an_install_that_did_not_restart_this_process_says_so(no_state_file) -> None:
    q = WebCommandQueue()
    q.set_update_status("restarting")
    _write_state(no_state_file, "restarting", "Update installed. Restarting…")
    settled = q.settle_finished_install()
    assert settled["state"] == "failed"
    assert settled["message"] == "Update installed, but OpenFollow was not restarted."
    assert q.is_update_busy() is False


@pytest.mark.parametrize("written", [None, "running", "garbage"], ids=["no-file", "running", "unreadable"])
def test_an_installer_that_ended_without_a_result_ends_the_busy_state(written, no_state_file) -> None:
    q = WebCommandQueue()
    q.set_update_status("restarting")
    if written == "running":
        _write_state(no_state_file, "running", "Installing update…")
    elif written == "garbage":
        no_state_file.write_text("{not json")
    assert q.settle_finished_install() == {
        "state": "failed",
        "message": "Update failed.",
        "error": "The installer ended without a result.",
    }
    assert q.is_update_busy() is False


@pytest.mark.parametrize("state", ["idle", "queued", "running", "failed"])
def test_settling_leaves_a_status_set_since_the_install_alone(state: str, no_state_file) -> None:
    q = WebCommandQueue()
    q.set_update_status(state, "since")
    _write_state(no_state_file, "failed", "Update failed.", "old")
    assert q.settle_finished_install()["message"] == "since"
    assert q.get_update_status()["state"] == state
