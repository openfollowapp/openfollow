# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""When a sender counts as failing, when it counts as recovered, and the rows it raises."""

from __future__ import annotations

import errno
import logging
import threading

import pytest

from openfollow.send_health import (
    FAILING_AFTER_S,
    LOCAL_NETWORK_ACTION,
    LOCAL_NETWORK_HUD_TEXT,
    LOCAL_NETWORK_TEXT,
    OSC_SEND_FAILING_ACTION,
    RECOVERY_HOLD_S,
    SEND_FAILING_ACTION,
    SendAlert,
    SendHealth,
    SendHealthStatus,
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


def _health(platform: str = "linux") -> tuple[SendHealth, _Clock]:
    clock = _Clock()
    return SendHealth("PSN", clock=clock, platform=platform), clock


def _failing(health: SendHealth, clock: _Clock, exc: OSError = _REFUSED, dest: str = _GROUP) -> None:
    health.record_failure(exc, dest)
    clock.now += FAILING_AFTER_S
    health.record_failure(exc, dest)


def _messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.name == "openfollow.send_health"]


class TestStreak:
    def test_a_sender_that_never_failed_is_ok(self) -> None:
        health, _clock = _health()
        health.record_success()
        status = health.status()
        assert (status.state, status.failing_for_s, status.failed, status.error) == ("ok", None, 0, "")
        assert (status.sent_total, status.failed_total) == (1, 0)

    def test_one_failure_is_retrying_not_failing(self) -> None:
        health, clock = _health()
        health.record_failure(_REFUSED, _GROUP)
        clock.now += 30.0
        status = health.status()
        assert status.state == "retrying"
        assert not status.failing
        assert status.failing_for_s == pytest.approx(30.0)
        assert (status.error, status.errno, status.failed) == ("No route to host", errno.EHOSTUNREACH, 1)

    def test_a_burst_at_one_instant_never_becomes_failing(self) -> None:
        """However slowly the sender then rebuilds its socket."""
        health, clock = _health()
        for _ in range(500):
            health.record_failure(_REFUSED, _GROUP)
        clock.now += 60.0
        assert health.status().state == "retrying"

    def test_failures_that_keep_arriving_are_failing(self) -> None:
        health, clock = _health()
        health.record_failure(_REFUSED, _GROUP)
        clock.now += FAILING_AFTER_S - 0.01
        health.record_failure(_REFUSED, _GROUP)
        assert health.status().state == "retrying"
        clock.now += 0.01
        health.record_failure(_REFUSED, _GROUP)
        status = health.status()
        assert status.failing
        assert status.failed == 3

    def test_an_exception_without_os_wording_reads_as_its_text(self) -> None:
        health, _clock = _health()
        health.record_failure(OSError("socket went away"), _GROUP)
        status = health.status()
        assert (status.error, status.errno) == ("socket went away", None)

    def test_reset_forgets_the_streak_but_keeps_the_totals(self) -> None:
        health, clock = _health()
        health.record_success()
        _failing(health, clock)
        health.reset()
        status = health.status()
        assert (status.state, status.failed, status.error, status.errno) == ("ok", 0, "", None)
        assert (status.sent_total, status.failed_total) == (1, 2)


class TestRecovery:
    def test_one_good_send_after_failing_is_not_a_recovery(self) -> None:
        """With Local Network access off, a fresh socket's first send returns success and nothing leaves."""
        health, clock = _health()
        _failing(health, clock)
        health.record_success()
        assert health.status().failing

    def test_sends_that_keep_succeeding_for_the_hold_are(self) -> None:
        health, clock = _health()
        _failing(health, clock)
        health.record_success()
        clock.now += RECOVERY_HOLD_S - 0.01
        health.record_success()
        assert health.status().failing
        clock.now += 0.01
        health.record_success()
        assert health.status().state == "ok"

    def test_a_failure_between_good_sends_restarts_the_hold(self) -> None:
        health, clock = _health()
        _failing(health, clock)
        health.record_success()
        clock.now += 0.5
        health.record_failure(_REFUSED, _GROUP)
        health.record_success()
        clock.now += RECOVERY_HOLD_S - 0.01
        health.record_success()
        assert health.status().failing

    def test_a_retrying_streak_also_needs_the_hold(self) -> None:
        health, clock = _health()
        health.record_failure(_REFUSED, _GROUP)
        health.record_success()
        assert health.status().state == "retrying"
        clock.now += RECOVERY_HOLD_S
        health.record_success()
        assert health.status().state == "ok"


class TestLogging:
    def test_a_streak_logs_its_start_its_persistence_and_its_end(self, caplog: pytest.LogCaptureFixture) -> None:
        health, clock = _health()
        with caplog.at_level(logging.INFO):
            _failing(health, clock, OSError(errno.ENETUNREACH, "Network is unreachable"), "192.0.2.10")
            for _ in range(50):
                health.record_failure(OSError(errno.ENETUNREACH, "Network is unreachable"), "192.0.2.10")
            health.record_success()
            clock.now += RECOVERY_HOLD_S
            health.record_success()
        assert _messages(caplog) == [
            f"PSN: send failed: [Errno {errno.ENETUNREACH}] Network is unreachable",
            f"PSN: sends keep failing: [Errno {errno.ENETUNREACH}] Network is unreachable",
            "PSN: sending again after 2 s (52 sends failed).",
        ]
        levels = [record.levelno for record in caplog.records if record.name == "openfollow.send_health"]
        assert levels == [logging.WARNING, logging.ERROR, logging.INFO]

    def test_a_failing_streak_cut_short_by_a_reset_logs_its_end(self, caplog: pytest.LogCaptureFixture) -> None:
        health, clock = _health()
        _failing(health, clock)
        clock.now += 4
        caplog.clear()
        with caplog.at_level(logging.DEBUG, logger="openfollow.send_health"):
            health.reset()
        assert _messages(caplog) == ["PSN: send record reset after 5 s of failing sends (2 failed)."]

    def test_a_reset_outside_a_failing_streak_logs_nothing(self, caplog: pytest.LogCaptureFixture) -> None:
        health, _clock = _health()
        health.record_failure(_REFUSED, _GROUP)
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="openfollow.send_health"):
            health.reset()
        assert _messages(caplog) == []

    def test_a_failing_streak_reminds_once_a_minute(self, caplog: pytest.LogCaptureFixture) -> None:
        health, clock = _health()
        _failing(health, clock)
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            clock.now += 59.0
            health.record_failure(_REFUSED, _GROUP)
            clock.now += 1.0
            health.record_failure(_REFUSED, _GROUP)
            health.record_failure(_REFUSED, _GROUP)
        assert _messages(caplog) == [
            f"PSN: sends still failing after 61 s (4 failed): [Errno {errno.EHOSTUNREACH}] No route to host"
        ]

    def test_sporadic_failures_log_once_a_minute(self, caplog: pytest.LogCaptureFixture) -> None:
        """One failure every 1.5 s at 60 Hz: each is a streak that clears without ever failing."""
        health, clock = _health()
        start = clock.now
        next_failure = start + 0.5
        with caplog.at_level(logging.DEBUG, logger="openfollow.send_health"):
            for frame in range(10 * 60 * 60):
                clock.now = start + frame / 60
                if clock.now >= next_failure:
                    health.record_failure(OSError(errno.ENOBUFS, "No buffer space available"), _GROUP)
                    next_failure += 1.5
                else:
                    health.record_success()
        levels = [record.levelno for record in caplog.records if record.name == "openfollow.send_health"]
        assert levels.count(logging.WARNING) == 10
        assert levels.count(logging.INFO) == levels.count(logging.ERROR) == 0
        reports = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
        assert reports[0] == f"PSN: send failed: [Errno {errno.ENOBUFS}] No buffer space available"
        assert reports[1] == f"{reports[0]} (39 brief failures since the last report)"

    def test_a_blip_that_never_failed_ends_at_debug(self, caplog: pytest.LogCaptureFixture) -> None:
        health, clock = _health()
        with caplog.at_level(logging.DEBUG, logger="openfollow.send_health"):
            health.record_failure(_REFUSED, _GROUP)
            health.record_success()
            clock.now += RECOVERY_HOLD_S
            health.record_success()
        assert health.status().state == "ok"
        ended = [record for record in caplog.records if "sending again" in record.getMessage()]
        assert [record.levelno for record in ended] == [logging.DEBUG]

    def test_a_streak_that_keeps_failing_is_reported_inside_the_quiet_minute(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        health, clock = _health()
        health.record_failure(_REFUSED, _GROUP)
        health.record_success()
        clock.now += RECOVERY_HOLD_S
        health.record_success()
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="openfollow.send_health"):
            clock.now += 10.0
            _failing(health, clock)
            health.record_success()
            clock.now += RECOVERY_HOLD_S
            health.record_success()
        assert _messages(caplog) == [
            f"PSN: sends keep failing: [Errno {errno.EHOSTUNREACH}] No route to host",
            "PSN: sending again after 2 s (2 sends failed).",
        ]

    def test_a_brief_failure_reported_later_is_not_counted_twice(self, caplog: pytest.LogCaptureFixture) -> None:
        """A quiet streak that goes on to fail has its own line, so the next summary leaves it out."""
        health, clock = _health()
        health.record_failure(_REFUSED, _GROUP)
        for _ in range(2):
            health.record_success()
            clock.now += RECOVERY_HOLD_S
            health.record_success()
            clock.now += 5.0
            health.record_failure(_REFUSED, _GROUP)
        clock.now += FAILING_AFTER_S
        health.record_failure(_REFUSED, _GROUP)
        health.record_success()
        clock.now += RECOVERY_HOLD_S
        health.record_success()
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="openfollow.send_health"):
            clock.now += 60.0
            health.record_failure(_REFUSED, _GROUP)
        assert _messages(caplog) == [
            f"PSN: send failed: [Errno {errno.EHOSTUNREACH}] No route to host (1 brief failure since the last report)"
        ]

    @pytest.mark.parametrize(("platform", "named"), [("darwin", True), ("linux", False)])
    def test_only_macos_names_the_local_network_permission(
        self, caplog: pytest.LogCaptureFixture, platform: str, named: bool
    ) -> None:
        health, clock = _health(platform)
        with caplog.at_level(logging.ERROR):
            _failing(health, clock)
        (message,) = _messages(caplog)
        assert ("Local Network" in message) is named


class TestLocalNetwork:
    @pytest.mark.parametrize(
        ("platform", "err", "multicast", "expected"),
        [
            ("darwin", errno.EHOSTUNREACH, True, True),
            ("linux", errno.EHOSTUNREACH, True, False),
            ("darwin", errno.ENETUNREACH, True, False),
            ("darwin", errno.EHOSTUNREACH, False, False),
            ("darwin", None, True, False),
        ],
        ids=["macos-multicast-refused", "linux", "other-errno", "unicast", "no-errno"],
    )
    def test_the_signature(self, platform: str, err: int | None, multicast: bool, expected: bool) -> None:
        assert looks_like_local_network(err, multicast, platform) is expected

    @pytest.mark.parametrize(
        ("dest", "expected"),
        [(_GROUP, True), ("192.0.2.10", False), ("lighting-desk.local", False)],
        ids=["group", "unicast", "name"],
    )
    def test_the_status_judges_the_destination_it_failed_on(self, dest: str, expected: bool) -> None:
        health, clock = _health("darwin")
        _failing(health, clock, dest=dest)
        assert health.status().local_network is expected

    def test_a_healthy_sender_carries_no_signature(self) -> None:
        health, clock = _health("darwin")
        _failing(health, clock)
        health.reset()
        assert health.status().local_network is False


def _status(
    label: str, *, state: str = "failing", err: int | None = errno.ENETUNREACH, local: bool = False
) -> SendHealthStatus:
    return SendHealthStatus(
        label=label,
        state=state,
        failing_for_s=3.0,
        failed=4,
        error="Network is unreachable" if err == errno.ENETUNREACH else "No route to host",
        errno=err,
        local_network=local,
        sent_total=0,
        failed_total=4,
    )


class TestSendAlerts:
    def test_nothing_failing_raises_nothing(self) -> None:
        assert send_alerts([_status("PSN", state="ok"), _status("Discovery", state="retrying")]) == []

    def test_a_failing_sender_names_itself_and_the_os_wording(self) -> None:
        assert send_alerts([_status("RTTrPM output")]) == [
            SendAlert("RTTrPM output: sends keep failing (Network is unreachable)", SEND_FAILING_ACTION)
        ]

    def test_every_sender_macos_refuses_shares_one_row_naming_the_permission(self) -> None:
        statuses = [
            _status("PSN", err=errno.EHOSTUNREACH, local=True),
            _status("Discovery", err=errno.EHOSTUNREACH, local=True),
            # A unicast sender refused the same way is the same switch.
            _status("RTTrPM output", err=errno.EHOSTUNREACH),
            _status("OTP output"),
        ]
        assert send_alerts(statuses) == [
            SendAlert(LOCAL_NETWORK_TEXT, LOCAL_NETWORK_ACTION, LOCAL_NETWORK_HUD_TEXT),
            SendAlert("OTP output: sends keep failing (Network is unreachable)", SEND_FAILING_ACTION),
        ]

    def test_a_sender_can_carry_its_own_next_step(self) -> None:
        statuses = [_status("OSC to Lighting desk"), _status("OTP output")]
        assert send_alerts(statuses, actions={"OSC to Lighting desk": OSC_SEND_FAILING_ACTION}) == [
            SendAlert("OSC to Lighting desk: sends keep failing (Network is unreachable)", OSC_SEND_FAILING_ACTION),
            SendAlert("OTP output: sends keep failing (Network is unreachable)", SEND_FAILING_ACTION),
        ]

    def test_a_refused_unicast_send_alone_does_not_name_the_permission(self) -> None:
        (alert,) = send_alerts([_status("RTTrPM output", err=errno.EHOSTUNREACH)])
        assert alert.text == "RTTrPM output: sends keep failing (No route to host)"

    def test_a_signature_still_retrying_names_nothing(self) -> None:
        assert send_alerts([_status("PSN", state="retrying", err=errno.EHOSTUNREACH, local=True)]) == []

    def test_the_rows_fit_two_status_row_lines(self) -> None:
        assert len(LOCAL_NETWORK_TEXT) <= 80
        assert len(LOCAL_NETWORK_HUD_TEXT) <= 80

    def test_the_operator_screen_row_points_at_the_permission(self) -> None:
        """The Operator Screen shows no next step, and the operator is standing at this station."""
        (alert,) = send_alerts([_status("PSN", err=errno.EHOSTUNREACH, local=True)])
        assert alert.hud_text == LOCAL_NETWORK_HUD_TEXT
        assert "Local Network" in alert.hud_text

    def test_a_row_without_its_own_operator_screen_text_shows_its_main_line(self) -> None:
        (alert,) = send_alerts([_status("OTP output")])
        assert alert.hud_text == alert.text

    def test_the_local_network_row_states_what_was_observed(self) -> None:
        """The permission is the inferred cause, so it belongs in the next step, not the main line."""
        assert "Local Network" not in LOCAL_NETWORK_TEXT
        assert "Local Network" in LOCAL_NETWORK_ACTION


def test_concurrent_senders_lose_no_count() -> None:
    health = SendHealth("PSN")

    def _send(n: int) -> None:
        for i in range(n):
            if i % 2:
                health.record_failure(_REFUSED, _GROUP)
            else:
                health.record_success()

    threads = [threading.Thread(target=_send, args=(2000,)) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    status = health.status()
    assert (status.sent_total, status.failed_total) == (4000, 4000)


def test_the_status_serialises_for_the_wire() -> None:
    health, clock = _health("darwin")
    _failing(health, clock)
    assert health.status().to_dict() == {
        "label": "PSN",
        "state": "failing",
        "failing_for_s": pytest.approx(FAILING_AFTER_S),
        "failed": 2,
        "error": "No route to host",
        "errno": errno.EHOSTUNREACH,
        "local_network": True,
        "sent_total": 0,
        "failed_total": 2,
    }
