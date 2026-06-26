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
    q.set_update_available(None)  # type: ignore[arg-type]
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
