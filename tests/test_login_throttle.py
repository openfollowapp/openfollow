# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Unit tests for openfollow.web.login_throttle."""

from __future__ import annotations

import threading

import pytest

from openfollow.web.login_throttle import LoginThrottle

pytestmark = pytest.mark.unit


class _FakeClock:
    """Monotonic clock that only advances on demand. Lets the tests
    pin lockout-window arithmetic without relying on ``time.sleep``."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _throttle(clock: _FakeClock, **kwargs: float) -> LoginThrottle:
    return LoginThrottle(clock=clock, **kwargs)


def test_no_failures_means_no_lockout() -> None:
    clock = _FakeClock()
    t = _throttle(clock)

    assert t.remaining_lockout("1.2.3.4") == 0.0


def test_begin_attempt_reserves_then_serializes_same_ip() -> None:
    """First guess reserves (provisional armed); a concurrent guess for the
    same IP is blocked until the window clears; other IPs are independent."""
    clock = _FakeClock()
    t = _throttle(clock)

    assert t.begin_attempt("1.2.3.4") == 0.0
    assert t.begin_attempt("1.2.3.4") > 0.0  # busy → serialized out
    assert t.begin_attempt("5.6.7.8") == 0.0  # different IP unaffected


def test_begin_attempt_provisional_self_heals_when_unrecorded() -> None:
    clock = _FakeClock()
    t = _throttle(clock, provisional_lockout_s=1.0)

    assert t.begin_attempt("1.2.3.4") == 0.0
    clock.advance(1.0)  # caller dropped without recording; window expires
    assert t.begin_attempt("1.2.3.4") == 0.0


def test_begin_attempt_blocked_while_locked_out() -> None:
    clock = _FakeClock()
    t = _throttle(clock)

    t.record_failure("1.2.3.4")  # arms a real 1s lockout
    assert t.begin_attempt("1.2.3.4") > 0.0


def test_begin_attempt_one_guess_one_failure_escalates() -> None:
    clock = _FakeClock()
    t = _throttle(clock)

    assert t.begin_attempt("1.2.3.4") == 0.0
    t.record_failure("1.2.3.4")
    clock.advance(2.0)
    assert t.begin_attempt("1.2.3.4") == 0.0
    t.record_failure("1.2.3.4")
    assert t.remaining_lockout("1.2.3.4") > 1.0  # 2nd failure → longer backoff


def test_begin_attempt_then_success_clears_provisional() -> None:
    clock = _FakeClock()
    t = _throttle(clock)

    assert t.begin_attempt("1.2.3.4") == 0.0
    t.record_success("1.2.3.4")
    assert t.begin_attempt("1.2.3.4") == 0.0


def test_first_failure_locks_for_one_second() -> None:
    """Exponential backoff base case: the very first failure already
    introduces a 1 s pause, so an attacker can't pipeline attempts."""
    clock = _FakeClock()
    t = _throttle(clock)

    t.record_failure("1.2.3.4")

    assert t.remaining_lockout("1.2.3.4") == pytest.approx(1.0)


def test_lockout_doubles_with_each_consecutive_failure() -> None:
    """Lockout doubles: 1s, 2s, 4s, 8s, etc. Geometric progression assertion."""
    clock = _FakeClock()
    t = _throttle(clock)

    expected_delays = [1.0, 2.0, 4.0, 8.0, 16.0]
    for expected in expected_delays:
        t.record_failure("1.2.3.4")
        assert t.remaining_lockout("1.2.3.4") == pytest.approx(expected)
        clock.advance(expected)  # ride out the lockout before next attempt


def test_record_failure_does_not_overflow_under_sustained_attack() -> None:
    clock = _FakeClock()
    t = _throttle(clock, max_lockout_s=30.0)

    # Way past the float-exponent ceiling. Without the saturation guard
    # this loop would raise OverflowError around iteration 1024.
    for _ in range(2000):
        t.record_failure("1.2.3.4")

    # Cap still holds; behaviour past the safe-exponent bound is identical
    # to behaviour at the cap.
    assert t.remaining_lockout("1.2.3.4") == pytest.approx(30.0)


def test_lockout_caps_at_max_lockout_s() -> None:
    clock = _FakeClock()
    t = _throttle(clock, max_lockout_s=5.0)

    for _ in range(20):
        t.record_failure("1.2.3.4")
        clock.advance(5.0)

    # 2 ** 19 would be ~524k seconds without the cap; capped == 5.0.
    t.record_failure("1.2.3.4")
    assert t.remaining_lockout("1.2.3.4") == pytest.approx(5.0)


def test_remaining_decays_as_clock_advances() -> None:
    """Within the lockout window, ``remaining_lockout`` returns a
    monotonically-shrinking value down to 0.0 once the window expires."""
    clock = _FakeClock()
    t = _throttle(clock)

    t.record_failure("1.2.3.4")  # 1 s lockout
    assert t.remaining_lockout("1.2.3.4") == pytest.approx(1.0)

    clock.advance(0.4)
    assert t.remaining_lockout("1.2.3.4") == pytest.approx(0.6)

    clock.advance(0.6)
    assert t.remaining_lockout("1.2.3.4") == 0.0

    clock.advance(10.0)
    assert t.remaining_lockout("1.2.3.4") == 0.0


def test_record_success_clears_history() -> None:
    """A correct PIN entry resets the counter – otherwise a user who
    typo'd once would carry a doubling penalty into their next session."""
    clock = _FakeClock()
    t = _throttle(clock)

    t.record_failure("1.2.3.4")
    t.record_failure("1.2.3.4")
    assert t.remaining_lockout("1.2.3.4") > 0.0

    t.record_success("1.2.3.4")
    assert t.remaining_lockout("1.2.3.4") == 0.0

    # Subsequent failure starts the curve over at 1 s, not 8 s.
    t.record_failure("1.2.3.4")
    assert t.remaining_lockout("1.2.3.4") == pytest.approx(1.0)


def test_record_success_on_unknown_ip_is_a_noop() -> None:
    """Don't blow up if a peer's first request is a successful one – the
    ``pop`` path needs to handle the missing-key case cleanly."""
    clock = _FakeClock()
    t = _throttle(clock)

    t.record_success("never.seen.before")  # must not raise


def test_separate_ips_have_independent_lockouts() -> None:
    clock = _FakeClock()
    t = _throttle(clock)

    for _ in range(5):
        t.record_failure("attacker")

    assert t.remaining_lockout("attacker") > 0.0
    assert t.remaining_lockout("legit") == 0.0


def test_idle_entry_resets_to_fresh_curve() -> None:
    """After ``reset_after_s`` of inactivity, the failure counter is
    cleared so a returning legitimate user doesn't pay for stale
    failures from earlier in the day."""
    clock = _FakeClock()
    t = _throttle(clock, reset_after_s=60.0)

    t.record_failure("1.2.3.4")
    t.record_failure("1.2.3.4")  # arms a 2 s lockout
    clock.advance(2.0)  # lockout window has elapsed

    clock.advance(60.0)  # idle threshold reached

    t.record_failure("1.2.3.4")
    # Counter started over – 1 s, not 4 s as it would be otherwise.
    assert t.remaining_lockout("1.2.3.4") == pytest.approx(1.0)


def test_idle_entry_is_garbage_collected_on_remaining_lockout() -> None:
    """``remaining_lockout`` opportunistically GCs entries that have
    aged past ``reset_after_s`` – keeps the dict bounded on a daemon
    that gets scanned by many transient IPs."""
    clock = _FakeClock()
    t = _throttle(clock, reset_after_s=60.0)

    t.record_failure("transient.ip")
    clock.advance(120.0)

    assert t.remaining_lockout("transient.ip") == 0.0
    assert "transient.ip" not in t._entries


def test_failure_after_idle_resets_in_place_when_sweep_skipped() -> None:
    clock = _FakeClock()
    t = _throttle(clock, reset_after_s=60.0)

    # Sweep on empty dict, then arm two failures on ip_A.
    t.record_failure("ip_A")
    clock.advance(30.0)
    t.record_failure("ip_A")  # ip_A: failures=2, last_failure_at=now

    # ip_B's failure 40 s later triggers the periodic sweep, but ip_A's
    # entry is only 40 s old at that point – survives the 60 s threshold.
    clock.advance(40.0)
    t.record_failure("ip_B")
    assert "ip_A" in t._entries  # survived the sweep

    # Another 30 s later, ip_A's entry is 70 s old – past the idle
    # threshold. But the sweep won't re-run (only 30 s since last
    # sweep), so the elif branch is what catches the stale state.
    clock.advance(30.0)
    t.record_failure("ip_A")

    # Reset to a fresh sequence – first failure of the new burst, so
    # 1 s lockout (would be 4 s if the count had carried).
    assert t.remaining_lockout("ip_A") == pytest.approx(1.0)


def test_gc_does_not_remove_active_lockout_under_misconfig() -> None:
    clock = _FakeClock()
    # Lockout cap (30 s) longer than idle reset (5 s) – the misconfig.
    t = _throttle(clock, max_lockout_s=30.0, reset_after_s=5.0)

    # Pack failures tightly enough that GC doesn't fire between them
    # (we want to actually reach the 30 s cap, which takes 6+ failures).
    for _ in range(6):
        t.record_failure("1.2.3.4")
        clock.advance(0.01)
    t.record_failure("1.2.3.4")  # arms a 30 s lockout, last_failure_at = now

    # Wait past the idle threshold but well inside the lockout window.
    clock.advance(10.0)

    # The GC paths must keep the entry – a query mid-lockout still
    # reports the remaining time, and a sweep doesn't drop it either.
    assert t.remaining_lockout("1.2.3.4") == pytest.approx(20.0, abs=0.1)
    assert "1.2.3.4" in t._entries


def test_record_failure_sweeps_stale_entries_from_rotating_ips() -> None:
    clock = _FakeClock()
    t = _throttle(clock, reset_after_s=60.0)

    # Burst of transient IPs that each fail once and never come back.
    for i in range(50):
        t.record_failure(f"scanner.{i}")
    assert len(t._entries) == 50

    # Age past the idle threshold and trigger a single new failure –
    # the periodic sweep must drop all 50 stale entries, leaving only
    # the new one.
    clock.advance(120.0)
    t.record_failure("legit.user")

    assert t._entries.keys() == {"legit.user"}


def test_lockout_is_thread_safe_under_concurrent_failures() -> None:
    clock = _FakeClock()
    t = _throttle(clock)
    n_threads = 20

    barrier = threading.Barrier(n_threads)

    def hammer() -> None:
        barrier.wait()
        t.record_failure("1.2.3.4")

    threads = [threading.Thread(target=hammer) for _ in range(n_threads)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    # Every increment landed; no lost updates from a torn read.
    assert t._entries["1.2.3.4"].failures == n_threads
