# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Paul Hermann and Michel Honold
"""When a sender counts as failing, when it counts as recovered, and when it reads as macOS Local Network."""

from __future__ import annotations

import errno
import logging
import threading

import pytest

from openfollow.send_health import (
    FAILING_AFTER_S,
    LOCAL_NETWORK_ALERT,
    LOCAL_NETWORK_HUD_TEXT,
    LOCAL_NETWORK_TEXT,
    RECOVERY_HOLD_S,
    SendAlert,
    SendHealth,
    looks_like_local_network,
    send_alerts,
)

pytestmark = pytest.mark.unit

_GROUP = "236.10.10.10"
_REFUSED = OSError(errno.EHOSTUNREACH, "No route to host")


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _health(platform: str = "darwin") -> tuple[SendHealth, _Clock]:
    clock = _Clock()
    return SendHealth("PSN", clock=clock, platform=platform), clock


def _failing(health: SendHealth, clock: _Clock, exc: OSError = _REFUSED, dest: str = _GROUP) -> None:
    health.record_failure(exc, dest)
    clock.now += FAILING_AFTER_S
    health.record_failure(exc, dest)


def _messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.name == "openfollow.send_health"]


@pytest.mark.parametrize(
    ("err", "multicast", "platform", "expected"),
    [
        (errno.EHOSTUNREACH, True, "darwin", True),
        (errno.EHOSTUNREACH, False, "darwin", False),
        (errno.EHOSTUNREACH, True, "linux", False),
        (errno.ENETUNREACH, True, "darwin", False),
        (None, True, "darwin", False),
    ],
    ids=["macos-multicast", "unicast", "linux", "other-errno", "no-errno"],
)
def test_only_a_refused_multicast_send_on_macos_reads_as_local_network(
    err: int | None, multicast: bool, platform: str, expected: bool
) -> None:
    assert looks_like_local_network(err, multicast, platform) is expected


class TestStreak:
    def test_a_fresh_record_is_quiet(self) -> None:
        health, _clock = _health()
        assert (health.failed, health.failing, health.local_network) == (0, False, False)

    def test_a_burst_at_one_instant_is_not_failing(self) -> None:
        health, _clock = _health()
        for _ in range(50):
            health.record_failure(_REFUSED, _GROUP)
        assert (health.failed, health.failing, health.local_network) == (50, False, False)

    def test_failures_that_keep_arriving_are_failing(self) -> None:
        health, clock = _health()
        _failing(health, clock)
        assert (health.failed, health.failing, health.local_network) == (2, True, True)

    @pytest.mark.parametrize(
        ("exc", "dest", "platform"),
        [
            (_REFUSED, _GROUP, "linux"),
            (_REFUSED, "192.0.2.20", "darwin"),
            (OSError(errno.ENETUNREACH, "Network is unreachable"), _GROUP, "darwin"),
        ],
        ids=["linux", "unicast", "other-errno"],
    )
    def test_failing_any_other_way_is_not_local_network(self, exc: OSError, dest: str, platform: str) -> None:
        health, clock = _health(platform)
        _failing(health, clock, exc, dest)
        assert (health.failing, health.local_network) == (True, False)

    def test_the_last_failure_decides_the_signature(self) -> None:
        health, clock = _health()
        _failing(health, clock)
        health.record_failure(OSError(errno.ENETUNREACH, "Network is unreachable"), _GROUP)
        assert health.local_network is False

    def test_reset_forgets_the_streak(self) -> None:
        health, clock = _health()
        _failing(health, clock)
        health.reset()
        assert (health.failed, health.failing, health.local_network) == (0, False, False)


class TestRecovery:
    def test_a_success_outside_a_streak_changes_nothing(self) -> None:
        health, _clock = _health()
        health.record_success()
        assert health.failed == 0

    def test_one_success_after_the_failures_is_no_recovery(self) -> None:
        """With Local Network denied, a fresh socket's first send returns success and nothing leaves."""
        health, clock = _health()
        _failing(health, clock)
        clock.now += 10.0
        health.record_success()
        assert health.local_network is True

    def test_successes_inside_the_hold_are_no_recovery(self) -> None:
        health, clock = _health()
        _failing(health, clock)
        health.record_success()
        clock.now += RECOVERY_HOLD_S / 2
        health.record_success()
        assert health.failing is True

    def test_successes_that_keep_arriving_end_the_streak(self, caplog: pytest.LogCaptureFixture) -> None:
        health, clock = _health()
        _failing(health, clock)
        health.record_success()
        clock.now += RECOVERY_HOLD_S
        caplog.clear()
        with caplog.at_level(logging.INFO):
            health.record_success()
        assert (health.failed, health.failing) == (0, False)
        assert _messages(caplog) == ["PSN: sending again after 2 s (2 sends failed)."]

    def test_a_failure_between_the_successes_starts_the_hold_again(self) -> None:
        health, clock = _health()
        _failing(health, clock)
        health.record_success()
        health.record_failure(_REFUSED, _GROUP)
        clock.now += RECOVERY_HOLD_S
        health.record_success()
        assert health.failing is True

    def test_a_streak_that_never_failed_ends_without_a_log_line(self, caplog: pytest.LogCaptureFixture) -> None:
        health, clock = _health()
        health.record_failure(_REFUSED, _GROUP)
        health.record_success()
        clock.now += RECOVERY_HOLD_S
        caplog.clear()
        with caplog.at_level(logging.DEBUG):
            health.record_success()
        assert health.failed == 0
        assert _messages(caplog) == []


class TestLogging:
    def test_a_streak_logs_its_start_and_when_it_keeps_failing(self, caplog: pytest.LogCaptureFixture) -> None:
        health, clock = _health()
        with caplog.at_level(logging.DEBUG):
            _failing(health, clock)
            clock.now += 1.0
            health.record_failure(_REFUSED, _GROUP)
        assert [(record.levelno, record.getMessage()) for record in caplog.records] == [
            (logging.WARNING, f"PSN: send failed: {_REFUSED}"),
            (
                logging.ERROR,
                f"PSN: sends keep failing: {_REFUSED} – macOS is refusing them, which is what Local Network"
                " access being off for OpenFollow looks like (System Settings → Privacy & Security → Local Network)",
            ),
        ]

    def test_a_failing_streak_off_macos_names_no_permission(self, caplog: pytest.LogCaptureFixture) -> None:
        health, clock = _health("linux")
        with caplog.at_level(logging.ERROR):
            _failing(health, clock)
        assert _messages(caplog) == [f"PSN: sends keep failing: {_REFUSED}"]

    def test_streak_starts_are_logged_at_most_once_a_minute(self, caplog: pytest.LogCaptureFixture) -> None:
        health, clock = _health()
        with caplog.at_level(logging.WARNING):
            for _ in range(3):
                health.record_failure(_REFUSED, _GROUP)
                health.reset()
                clock.now += 20.0
            health.record_failure(_REFUSED, _GROUP)
        assert _messages(caplog) == [f"PSN: send failed: {_REFUSED}"] * 2


class TestAlerts:
    def test_no_refused_sender_raises_no_row(self) -> None:
        health, clock = _health("linux")
        _failing(health, clock)
        assert send_alerts([health, _health()[0]]) == []

    def test_every_refused_sender_folds_into_one_row(self) -> None:
        refused = []
        for _ in range(3):
            health, clock = _health()
            _failing(health, clock)
            refused.append(health)
        assert send_alerts(refused) == [LOCAL_NETWORK_ALERT]

    def test_the_row_names_the_permission_on_screen_and_the_observation_on_the_web(self) -> None:
        assert (LOCAL_NETWORK_ALERT.text, LOCAL_NETWORK_ALERT.hud_text) == (LOCAL_NETWORK_TEXT, LOCAL_NETWORK_HUD_TEXT)

    def test_a_row_without_its_own_hud_text_shows_its_text(self) -> None:
        assert SendAlert("PSN: eth0 is down").hud_text == "PSN: eth0 is down"


def test_records_from_many_threads_are_all_counted() -> None:
    health, _clock = _health()
    threads = [
        threading.Thread(target=lambda: [health.record_failure(_REFUSED, _GROUP) for _ in range(500)]) for _ in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert health.failed == 2000
