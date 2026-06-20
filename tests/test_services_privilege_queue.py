# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for the privilege-password queue on :class:`WebCommandQueue`."""

from __future__ import annotations

import threading

import pytest

from openfollow.services import WebCommandQueue

pytestmark = pytest.mark.unit


@pytest.fixture
def queue() -> WebCommandQueue:
    return WebCommandQueue()


class TestRequestPasswordPrompt:
    def test_request_returns_true_when_idle(self, queue) -> None:
        ok = queue.request_privilege_password(
            reason="Apply network changes",
            capability_name="network.apply",
        )
        assert ok is True
        pending = queue.pending_privilege_password_request()
        assert pending == {
            "reason": "Apply network changes",
            "capability_name": "network.apply",
        }

    def test_request_returns_false_when_already_in_flight(self, queue) -> None:
        queue.request_privilege_password(
            reason="first",
            capability_name="cap.a",
        )
        ok = queue.request_privilege_password(
            reason="second",
            capability_name="cap.b",
        )
        assert ok is False
        # Original prompt is preserved – second request didn't clobber.
        pending = queue.pending_privilege_password_request()
        assert pending["reason"] == "first"

    def test_pending_returns_none_when_idle(self, queue) -> None:
        assert queue.pending_privilege_password_request() is None

    def test_pending_returns_copy(self, queue) -> None:
        queue.request_privilege_password(reason="x", capability_name="y")
        first = queue.pending_privilege_password_request()
        first["reason"] = "tampered"
        second = queue.pending_privilege_password_request()
        assert second["reason"] == "x"


class TestSubmitAndConsume:
    def test_submit_then_consume_returns_password(self, queue) -> None:
        queue.request_privilege_password(reason="x", capability_name="y")
        queue.submit_privilege_password("hunter2")
        assert queue.consume_privilege_password(timeout=1.0) == "hunter2"
        # Pending slot clears after consumption.
        assert queue.pending_privilege_password_request() is None

    def test_cancel_then_consume_returns_none(self, queue) -> None:
        queue.request_privilege_password(reason="x", capability_name="y")
        queue.cancel_privilege_password()
        assert queue.consume_privilege_password(timeout=1.0) is None
        assert queue.pending_privilege_password_request() is None

    def test_timeout_returns_none_and_clears_request(self, queue) -> None:
        queue.request_privilege_password(reason="x", capability_name="y")
        result = queue.consume_privilege_password(timeout=0.05)
        assert result is None
        # Timeout clears the pending so the UI dismisses the modal.
        assert queue.pending_privilege_password_request() is None

    def test_submit_concurrent_with_consume(self, queue) -> None:
        queue.request_privilege_password(reason="x", capability_name="y")

        results: list[str | None] = []

        def consumer():
            results.append(queue.consume_privilege_password(timeout=2.0))

        t = threading.Thread(target=consumer)
        t.start()
        queue.submit_privilege_password("hunter2")
        t.join(timeout=3.0)
        assert results == ["hunter2"]

    def test_fresh_request_after_consume_works(self, queue) -> None:
        """Once a prompt is consumed, the next request_/consume_ cycle
        starts clean – no stale event, no stale password."""
        queue.request_privilege_password(reason="x", capability_name="y")
        queue.submit_privilege_password("first")
        queue.consume_privilege_password(timeout=1.0)
        ok = queue.request_privilege_password(reason="x2", capability_name="y2")
        assert ok is True
        queue.submit_privilege_password("second")
        assert queue.consume_privilege_password(timeout=1.0) == "second"

    def test_late_event_promoted_under_lock(self, queue, monkeypatch) -> None:
        queue.request_privilege_password(reason="x", capability_name="y")
        # Stage a late event set: wait returns False, then we set the
        # event under the lock (mock the event's ``wait`` to false but
        # leave ``is_set`` true after queueing the password).
        queue._privilege_password = "race-winner"
        queue._privilege_password_event.set()
        # Force the wait to claim a timeout (it really wouldn't – the
        # event is already set – but the safety check below should
        # still observe is_set and treat it as signalled).
        monkeypatch.setattr(
            queue._privilege_password_event,
            "wait",
            lambda timeout=None: False,
        )
        assert queue.consume_privilege_password(timeout=0.05) == "race-winner"

    def test_stale_event_doesnt_leak_into_next_request(self, queue) -> None:
        queue.submit_privilege_password("ignored")  # no active request
        ok = queue.request_privilege_password(reason="x", capability_name="y")
        assert ok is True
        # The stale "ignored" submit must not be returned.
        result = queue.consume_privilege_password(timeout=0.05)
        assert result is None
