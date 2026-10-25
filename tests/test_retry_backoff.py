# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The backoff a failed network operation is retried on."""

from __future__ import annotations

import threading

import pytest

from openfollow.retry_backoff import RETRY_BACKOFF_MAX_S, RETRY_BACKOFF_S, retry_with_backoff

pytestmark = pytest.mark.unit


class _Stop(threading.Event):
    """A stop event whose waits return at once and are recorded; set after *waits* of them."""

    def __init__(self, waits: int | None = None) -> None:
        super().__init__()
        self.waited: list[float] = []
        self._waits = waits

    def wait(self, timeout: float | None = None) -> bool:
        assert timeout is not None
        self.waited.append(timeout)
        if self._waits is not None and len(self.waited) >= self._waits:
            self.set()
        return self.is_set()


def test_the_wait_starts_at_two_seconds_and_doubles_to_a_minute() -> None:
    stop = _Stop()
    tried: list[int] = []

    def attempt(number: int) -> bool:
        tried.append(number)
        return len(tried) == 9

    assert retry_with_backoff(attempt, stop, first=2) == 10
    assert (RETRY_BACKOFF_S, RETRY_BACKOFF_MAX_S) == (2.0, 60.0)
    assert stop.waited == [2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0, 60.0, 60.0]
    assert tried == list(range(2, 11))


def test_it_ends_on_the_first_success() -> None:
    stop = _Stop()
    tried: list[int] = []

    def attempt(number: int) -> bool:
        tried.append(number)
        return True

    assert retry_with_backoff(attempt, stop) == 1
    assert tried == [1]


def test_a_stop_ends_it_without_another_attempt() -> None:
    stop = _Stop(waits=3)
    tried: list[int] = []

    def attempt(number: int) -> bool:
        tried.append(number)
        return False

    assert retry_with_backoff(attempt, stop) is None
    assert tried == [1, 2]


def test_a_stopped_caller_never_attempts() -> None:
    stop = _Stop()
    stop.set()
    assert retry_with_backoff(lambda _number: pytest.fail("attempted after stop"), stop) is None
