# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for ``runtime/online_sync.OnlineSyncWorker``.

Hermetic and thread-free: every network / clock action is injected as a stub
and the trigger logic is exercised by calling ``_maybe_cycle`` / ``_run_cycle``
directly with fake clocks. No real thread is started.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any

import pytest

from openfollow.runtime import online_sync
from openfollow.runtime.online_sync import OnlineSyncWorker, _is_real_ip

pytestmark = pytest.mark.unit


def test_periodic_interval_is_daily() -> None:
    # Pin the cadence: startup + IP-change cover the time-critical cases; the
    # periodic backstop runs once a day, not every few hours.
    assert online_sync._PERIODIC_INTERVAL_S == 24 * 3600.0


class _Commands:
    """Captures ``set_update_available`` calls."""

    def __init__(self) -> None:
        self.values: list[str] = []

    def set_update_available(self, value: str) -> None:
        self.values.append(value)

    @property
    def last(self) -> str | None:
        return self.values[-1] if self.values else None


def _cfg(**over: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "auto_time_sync": True,
        "auto_update_check": True,
        "time_sync_server": "ptbtime1.ptb.de",
        "update_github_repo": "owner/repo",
        "update_include_prereleases": False,
    }
    base.update(over)
    return SimpleNamespace(**base)


def _worker(
    *,
    cfg: SimpleNamespace | None = None,
    commands: _Commands | None = None,
    broker: Any = "BROKER",
    can_set_clock: bool = True,
    ntp_query: Any = None,
    set_clock: Any = None,
    update_check: Any = None,
    now: Any = None,
    wall: Any = None,
    ip: str = "192.168.1.5",
    periodic_interval: float = 24 * 3600.0,
    retry_interval: float = 300.0,
    min_cycle_interval: float = 60.0,
) -> OnlineSyncWorker:
    return OnlineSyncWorker(
        config_provider=lambda: cfg or _cfg(),
        ip_provider=lambda: ip,
        broker=broker,
        web_commands=commands or _Commands(),
        version="0.3.0",
        periodic_interval=periodic_interval,
        retry_interval=retry_interval,
        min_cycle_interval=min_cycle_interval,
        ntp_query=ntp_query or (lambda s, t: 1_735_700_000.0),
        set_clock=set_clock or (lambda b, e: True),
        update_check=update_check or (lambda repo, ver, **kw: {"available": False, "latest": ""}),
        monotonic=now or (lambda: 0.0),
        wall_clock=wall or (lambda: 1_735_700_000.0),
        platform_can_set_clock=can_set_clock,
    )


# ---------------------------------------------------------------------------
# _is_real_ip
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ip,expected",
    [
        ("192.168.1.5", True),
        ("10.0.0.2", True),
        ("2001:db8::1", True),
        ("", False),
        ("N/A", False),
        ("0.0.0.0", False),
        ("127.0.0.1", False),
        ("169.254.10.20", False),  # link-local / APIPA – DHCP-failure address
        ("::1", False),  # IPv6 loopback
        ("fe80::1", False),  # IPv6 link-local
    ],
)
def test_is_real_ip(ip: str, expected: bool) -> None:
    assert _is_real_ip(ip) is expected


# ---------------------------------------------------------------------------
# _check_update
# ---------------------------------------------------------------------------


def test_check_update_publishes_when_available() -> None:
    cmds = _Commands()
    w = _worker(commands=cmds, update_check=lambda r, v, **kw: {"available": True, "latest": "0.4.0"})
    w._check_update(_cfg())
    assert cmds.last == "0.4.0"


def test_check_update_clears_when_up_to_date() -> None:
    cmds = _Commands()
    w = _worker(commands=cmds, update_check=lambda r, v, **kw: {"available": False, "latest": "0.3.0"})
    w._check_update(_cfg())
    assert cmds.last == ""


def test_check_update_swallows_error_and_leaves_state() -> None:
    cmds = _Commands()

    def _boom(repo: str, ver: str, **kw: Any) -> dict[str, Any]:
        raise RuntimeError("offline")

    w = _worker(commands=cmds, update_check=_boom)
    w._check_update(_cfg())
    assert cmds.values == []  # never touched the banner state


def test_check_update_forwards_prerelease_flag() -> None:
    captured: dict[str, Any] = {}

    def _check(repo: str, ver: str, **kw: Any) -> dict[str, Any]:
        captured.update(kw)
        return {"available": False, "latest": ""}

    w = _worker(update_check=_check)
    w._check_update(_cfg(update_include_prereleases=True))
    assert captured == {"include_prereleases": True}


def test_check_update_skips_blank_repo() -> None:
    cmds = _Commands()
    called = []
    w = _worker(commands=cmds, update_check=lambda r, v, **kw: called.append(1) or {"available": False})
    w._check_update(_cfg(update_github_repo="  "))
    assert called == [] and cmds.values == []


def test_check_update_swallows_bad_repo_type() -> None:
    # A malformed (non-string) update_github_repo hits ``.strip()`` inside the
    # try now, so a hand-edited config can't raise out of the cycle.
    cmds = _Commands()
    called = []
    w = _worker(commands=cmds, update_check=lambda r, v, **kw: called.append(1) or {"available": False})
    w._check_update(_cfg(update_github_repo=123))  # must not raise
    assert called == [] and cmds.values == []


# ---------------------------------------------------------------------------
# _sync_time
# ---------------------------------------------------------------------------


def test_sync_time_sets_clock_on_drift() -> None:
    calls: list[int] = []
    w = _worker(
        ntp_query=lambda s, t: 1_735_700_100.0,
        wall=lambda: 1_735_700_000.0,  # 100 s behind -> correct it
        set_clock=lambda b, e: calls.append(e) or True,
    )
    w._sync_time(_cfg())
    assert calls == [1_735_700_100]


def test_sync_time_skips_within_threshold() -> None:
    calls: list[int] = []
    w = _worker(
        ntp_query=lambda s, t: 1_735_700_000.5,
        wall=lambda: 1_735_700_000.0,  # 0.5 s drift -> no set
        set_clock=lambda b, e: calls.append(e) or True,
    )
    w._sync_time(_cfg())
    assert calls == []


def test_sync_time_skips_on_non_linux() -> None:
    calls: list[int] = []
    w = _worker(can_set_clock=False, set_clock=lambda b, e: calls.append(e) or True)
    w._sync_time(_cfg())
    assert calls == []


def test_sync_time_skips_without_broker() -> None:
    calls: list[int] = []
    w = _worker(broker=None, set_clock=lambda b, e: calls.append(e) or True)
    w._sync_time(_cfg())
    assert calls == []


def test_sync_time_skips_on_ntp_error() -> None:
    calls: list[int] = []

    def _boom(server: str, timeout: float) -> float:
        raise OSError("no route to host")

    w = _worker(ntp_query=_boom, set_clock=lambda b, e: calls.append(e) or True)
    w._sync_time(_cfg())
    assert calls == []


def test_sync_time_skips_implausible_epoch() -> None:
    calls: list[int] = []
    w = _worker(ntp_query=lambda s, t: 100.0, set_clock=lambda b, e: calls.append(e) or True)
    w._sync_time(_cfg())
    assert calls == []


def test_sync_time_passes_configured_server() -> None:
    seen: list[str] = []
    w = _worker(ntp_query=lambda s, t: seen.append(s) or 1_735_700_100.0, wall=lambda: 1_735_700_000.0)
    w._sync_time(_cfg(time_sync_server="time.cloudflare.com"))
    assert seen == ["time.cloudflare.com"]


# ---------------------------------------------------------------------------
# _run_cycle – flag gating + ordering
# ---------------------------------------------------------------------------


def test_run_cycle_respects_disabled_flags() -> None:
    cmds = _Commands()
    ntp_calls: list[int] = []
    w = _worker(
        cfg=_cfg(auto_time_sync=False, auto_update_check=False),
        commands=cmds,
        ntp_query=lambda s, t: ntp_calls.append(1) or 1_735_700_100.0,
        update_check=lambda r, v, **kw: {"available": True, "latest": "9.9"},
    )
    w._run_cycle("test")
    assert ntp_calls == []  # time-sync skipped
    assert cmds.values == []  # update-check skipped


def test_run_cycle_time_sync_runs_before_update_check() -> None:
    order: list[str] = []
    w = _worker(
        ntp_query=lambda s, t: order.append("ntp") or 1_735_700_100.0,
        wall=lambda: 1_735_700_000.0,
        set_clock=lambda b, e: order.append("set") or True,
        update_check=lambda r, v, **kw: order.append("update") or {"available": False, "latest": ""},
    )
    w._run_cycle("test")
    assert order == ["ntp", "set", "update"]


def test_run_cycle_marks_online_when_github_reached() -> None:
    w = _worker(
        cfg=_cfg(auto_time_sync=False, auto_update_check=True),
        update_check=lambda r, v, **kw: {"available": False, "latest": ""},
    )
    assert w._online is False
    w._run_cycle("test")
    assert w._online is True  # reached GitHub


def test_run_cycle_marks_online_when_ntp_reached_without_setting() -> None:
    # NTP reachable but drift below threshold: clock not set, yet we DID reach
    # the network, so the daily cadence should take over.
    w = _worker(
        cfg=_cfg(auto_time_sync=True, auto_update_check=False),
        ntp_query=lambda s, t: 1_735_700_000.5,
        wall=lambda: 1_735_700_000.0,  # drift 0.5 s < threshold
    )
    w._run_cycle("test")
    assert w._online is True


def test_run_cycle_marks_offline_when_unreachable() -> None:
    def _boom(r: str, v: str, **kw: Any) -> dict[str, Any]:
        raise RuntimeError("offline")

    w = _worker(
        cfg=_cfg(auto_time_sync=False, auto_update_check=True),
        update_check=_boom,
    )
    w._online = True  # was online previously
    w._run_cycle("test")
    assert w._online is False  # couldn't reach -> back to retry cadence


def test_run_cycle_leaves_online_untouched_when_both_flags_off() -> None:
    w = _worker(cfg=_cfg(auto_time_sync=False, auto_update_check=False))
    w._online = True
    w._run_cycle("test")
    assert w._online is True  # no network work attempted -> verdict unchanged


# ---------------------------------------------------------------------------
# _maybe_cycle – trigger logic
# ---------------------------------------------------------------------------


def _spy_cycle(w: OnlineSyncWorker) -> list[str]:
    fired: list[str] = []
    w._run_cycle = lambda reason: fired.append(reason)  # type: ignore[method-assign]
    return fired


def test_maybe_cycle_fires_on_ip_change_to_real() -> None:
    w = _worker(ip="192.168.1.9")
    fired = _spy_cycle(w)
    w._last_ip = "192.168.1.5"
    # No prior cycle (elapsed is None) -> the IP-change rate-limit is not in play.
    w._maybe_cycle()
    assert fired == ["ip-change"]


def test_maybe_cycle_ignores_change_to_loopback() -> None:
    w = _worker(ip="127.0.0.1")
    fired = _spy_cycle(w)
    w._last_ip = "192.168.1.5"
    w._last_cycle_monotonic = 0.0  # periodic not due (now=0)
    w._maybe_cycle()
    assert fired == []


def test_maybe_cycle_no_fire_when_unchanged_and_not_due() -> None:
    w = _worker(ip="192.168.1.5")
    fired = _spy_cycle(w)
    w._last_ip = "192.168.1.5"
    w._last_cycle_monotonic = 0.0
    w._maybe_cycle()
    assert fired == []


def test_maybe_cycle_rate_limits_ip_change() -> None:
    # A real IP change too soon after the last cycle is suppressed (a flapping
    # IP must not fire an NTP + GitHub cycle on every poll).
    now = {"t": 30.0}
    w = _worker(ip="192.168.1.9", now=lambda: now["t"], min_cycle_interval=60.0)
    fired = _spy_cycle(w)
    w._last_ip = "192.168.1.5"
    w._last_cycle_monotonic = 0.0  # elapsed 30 s < 60 s floor
    w._maybe_cycle()
    assert fired == []


def test_maybe_cycle_retries_sooner_when_offline() -> None:
    # Offline: an uplink appearing after boot without an IP change is picked up
    # on the retry cadence (minutes), well before the daily periodic backstop.
    now = {"t": 400.0}
    w = _worker(
        ip="192.168.1.5",
        now=lambda: now["t"],
        periodic_interval=86_400.0,
        retry_interval=300.0,
    )
    fired = _spy_cycle(w)
    w._online = False
    w._last_ip = "192.168.1.5"  # unchanged IP
    w._last_cycle_monotonic = 0.0  # 400 s >= retry 300, < periodic 86_400
    w._maybe_cycle()
    assert fired == ["retry"]


def test_maybe_cycle_fires_periodic_when_online_and_due() -> None:
    now = {"t": 10_000.0}
    w = _worker(ip="192.168.1.5", now=lambda: now["t"], periodic_interval=3600.0)
    fired = _spy_cycle(w)
    w._online = True  # online -> daily backstop cadence
    w._last_ip = "192.168.1.5"
    w._last_cycle_monotonic = 0.0  # 10_000 s elapsed >= 3600 -> due
    w._maybe_cycle()
    assert fired == ["periodic"]


def test_maybe_cycle_online_not_due_within_daily_window() -> None:
    # Online and only minutes since the last cycle: the retry cadence must NOT
    # apply once online – wait for the daily backstop.
    now = {"t": 400.0}
    w = _worker(ip="192.168.1.5", now=lambda: now["t"], periodic_interval=86_400.0, retry_interval=300.0)
    fired = _spy_cycle(w)
    w._online = True
    w._last_ip = "192.168.1.5"
    w._last_cycle_monotonic = 0.0  # 400 s < daily 86_400
    w._maybe_cycle()
    assert fired == []


def test_maybe_cycle_skips_when_both_flags_disabled() -> None:
    # Nothing to do -> don't even resolve the IP (interface enumeration).
    probes = {"n": 0}
    w = _worker(cfg=_cfg(auto_time_sync=False, auto_update_check=False))
    fired = _spy_cycle(w)

    def _ip() -> str:
        probes["n"] += 1
        return "192.168.1.9"

    w._ip_provider = _ip  # type: ignore[assignment]
    w._last_ip = "192.168.1.5"
    w._maybe_cycle()
    assert fired == [] and probes["n"] == 0


# ---------------------------------------------------------------------------
# thread lifecycle (start / stop / _run)
# ---------------------------------------------------------------------------


def test_platform_can_set_clock_defaults_to_linux_check() -> None:
    import sys

    # Constructed without ``platform_can_set_clock`` -> derived from sys.platform.
    w = OnlineSyncWorker(
        config_provider=_cfg,
        ip_provider=lambda: "192.168.1.5",
        broker=None,
        web_commands=_Commands(),
        version="0.3.0",
    )
    assert w._can_set_clock == sys.platform.startswith("linux")


def test_start_is_idempotent_and_stop_cleans_up() -> None:
    started = threading.Event()
    release = threading.Event()
    w = _worker()

    def _block() -> None:
        started.set()
        release.wait(5.0)  # keep the thread alive across the second start()

    w._run = _block  # type: ignore[method-assign]
    w.start()
    assert started.wait(1.0)
    thread = w._thread
    assert thread is not None
    w.start()  # already running (alive) -> no second thread
    assert w._thread is thread
    release.set()  # let the thread exit
    w.stop()
    assert w._thread is None
    w.stop()  # already stopped -> no-op


def test_start_restarts_after_thread_died() -> None:
    # A stale (dead) _thread reference left by a timed-out stop() must not block
    # a later restart – start() keys on liveness, not mere presence.
    w = _worker()
    w._thread = _FakeThread(alive=False)  # type: ignore[assignment]
    ran = threading.Event()
    w._run = ran.set  # type: ignore[method-assign]
    w.start()
    assert ran.wait(1.0)  # a fresh worker thread actually ran
    assert isinstance(w._thread, threading.Thread)


class _FakeThread:
    """Stand-in whose join is a no-op so we control the post-join liveness."""

    def __init__(self, *, alive: bool) -> None:
        self._alive = alive
        self.join_timeout: float | None = None

    def join(self, timeout: float | None = None) -> None:
        self.join_timeout = timeout

    def is_alive(self) -> bool:
        return self._alive


def test_stop_keeps_thread_ref_when_join_times_out() -> None:
    # join() timing out on an in-flight NTP/GitHub call must NOT drop the
    # reference: a later start() would otherwise clear _stop_event and spawn a
    # second worker alongside the still-live old thread.
    w = _worker()
    still_running = _FakeThread(alive=True)
    w._thread = still_running  # type: ignore[assignment]

    w.stop()

    assert w._stop_event.is_set()
    assert still_running.join_timeout == 2.0
    assert w._thread is still_running  # ref retained -> no double-spawn

    # start() sees a live worker and refuses to launch a second one, leaving the
    # stop signal in place so the old thread still winds down.
    w.start()
    assert w._thread is still_running
    assert w._stop_event.is_set()


def test_stop_drops_thread_ref_once_thread_exits() -> None:
    # The normal path: join succeeds, thread is gone, reference is cleared so a
    # later start() can bring the worker back up.
    w = _worker()
    exited = _FakeThread(alive=False)
    w._thread = exited  # type: ignore[assignment]

    w.stop()

    assert w._thread is None


def test_run_executes_startup_then_one_loop_iteration() -> None:
    seq: list[Any] = []
    w = _worker()
    w._initial_delay = 0.0
    w._poll_interval = 0.0
    w._run_cycle = lambda reason: seq.append(("cycle", reason))  # type: ignore[method-assign]

    def _maybe() -> None:
        seq.append(("maybe",))
        w._stop_event.set()  # exit the loop after one iteration

    w._maybe_cycle = _maybe  # type: ignore[method-assign]
    w._run()  # synchronous; no real thread
    assert seq == [("cycle", "startup"), ("maybe",)]


def test_run_swallows_maybe_cycle_exception() -> None:
    ran: list[int] = []
    w = _worker()
    w._initial_delay = 0.0
    w._poll_interval = 0.0
    w._run_cycle = lambda reason: None  # type: ignore[method-assign]

    def _boom() -> None:
        ran.append(1)
        w._stop_event.set()
        raise ValueError("boom")

    w._maybe_cycle = _boom  # type: ignore[method-assign]
    w._run()  # must not raise
    assert ran == [1]


def test_run_returns_immediately_when_stopped_during_initial_delay() -> None:
    fired: list[str] = []
    w = _worker()
    w._initial_delay = 0.0
    w._run_cycle = lambda reason: fired.append(reason)  # type: ignore[method-assign]
    w._stop_event.set()  # initial wait returns True -> early return, no cycle
    w._run()
    assert fired == []


def test_run_swallows_startup_cycle_exception() -> None:
    # A raising startup cycle must be caught so the resilient poll loop still
    # starts, not kill the daemon thread before it ever loops.
    seq: list[str] = []
    w = _worker()
    w._initial_delay = 0.0
    w._poll_interval = 0.0

    def _boom(reason: str) -> None:
        seq.append(reason)
        raise RuntimeError("startup boom")

    w._run_cycle = _boom  # type: ignore[method-assign]

    def _maybe() -> None:
        seq.append("maybe")
        w._stop_event.set()  # exit after one loop iteration

    w._maybe_cycle = _maybe  # type: ignore[method-assign]
    w._run()  # must not raise; loop still reached
    assert seq == ["startup", "maybe"]


# ---------------------------------------------------------------------------
# health() – the passive uplink observation the bundle reports
# ---------------------------------------------------------------------------


class _MonoClock:
    """Injectable monotonic clock, so ages are asserted exactly."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _raiser(exc: Exception) -> Any:
    def _raise(*_args: Any, **_kwargs: Any) -> Any:
        raise exc

    return _raise


def test_health_starts_as_never_attempted() -> None:
    """Before the first cycle there is no observation at all, and that must not
    read as "offline" - nothing has asked the network anything yet."""
    health = _worker().health()
    assert health["cycles"] == 0
    assert health["last_cycle_age_s"] is None
    assert health["time_sync"]["outcome"] == "never attempted"
    assert health["update_check"]["outcome"] == "never attempted"
    assert health["time_sync"]["age_s"] is None


def test_health_reports_both_targets_unreachable() -> None:
    w = _worker(
        ntp_query=_raiser(TimeoutError("timed out")),
        update_check=_raiser(OSError("Network is unreachable")),
    )
    w._run_cycle("retry")
    health = w.health()
    assert health["online"] is False
    assert health["time_sync"]["outcome"] == "unreachable"
    assert health["time_sync"]["detail"] == "TimeoutError: timed out"
    assert health["update_check"]["outcome"] == "unreachable"
    assert health["cycles"] == 1
    assert health["last_reason"] == "retry"


def test_health_reports_reached_with_what_happened() -> None:
    w = _worker(
        ntp_query=lambda _s, _t: 1_735_700_000.0,
        update_check=lambda *_a, **_k: {"available": True, "latest": "0.4.3"},
    )
    w._run_cycle("startup")
    health = w.health()
    assert health["online"] is True
    assert health["time_sync"]["outcome"] == "reached"
    assert "not set" in health["time_sync"]["detail"]
    assert health["update_check"]["detail"] == "update available: 0.4.3"


def test_health_ages_run_on_the_monotonic_clock() -> None:
    """This worker *moves the wall clock*, so an age computed across a
    successful sync would be fiction - the very window most likely to be read."""
    clock = _MonoClock()
    w = _worker(now=clock, ntp_query=_raiser(TimeoutError("x")), update_check=_raiser(OSError("y")))
    w._run_cycle("startup")
    clock.advance(42.0)
    health = w.health()
    assert health["last_cycle_age_s"] == pytest.approx(42.0)
    assert health["time_sync"]["age_s"] == pytest.approx(42.0)


def test_health_cadence_follows_the_online_verdict() -> None:
    """``retry`` means no cycle has reached the network this session, which is
    what makes a "no uplink" reading trustworthy rather than merely stale."""
    reachable = {"ok": False}

    def _update(*_a: Any, **_k: Any) -> dict[str, Any]:
        if not reachable["ok"]:
            raise OSError("Network is unreachable")
        return {"available": False}

    w = _worker(cfg=_cfg(auto_time_sync=False), update_check=_update)
    w._run_cycle("startup")
    assert w.health()["cadence_s"] == 300.0
    # Driven through a real cycle rather than by poking ``_online``: the
    # cadence is published with the rest of the snapshot, so a test that set
    # the flag directly would assert on a state the worker cannot reach.
    reachable["ok"] = True
    w._run_cycle("retry")
    assert w.health()["cadence_s"] == 24 * 3600.0


def test_health_marks_disabled_checks_as_not_attempted() -> None:
    w = _worker(cfg=_cfg(auto_time_sync=False, auto_update_check=False))
    w._run_cycle("startup")
    health = w.health()
    assert health["time_sync"]["outcome"] == "not attempted"
    assert health["time_sync"]["detail"] == "auto_time_sync is off"
    assert health["update_check"]["detail"] == "auto_update_check is off"


def test_health_marks_a_host_that_cannot_set_its_clock_as_not_attempted() -> None:
    """The skip happens before any NTP packet is sent, so it says nothing about
    the network."""
    w = _worker(can_set_clock=False, cfg=_cfg(auto_update_check=False))
    w._run_cycle("startup")
    health = w.health()
    assert health["time_sync"]["outcome"] == "not attempted"
    assert "clock-set unavailable" in health["time_sync"]["detail"]


def test_a_skipped_target_does_not_decide_the_online_verdict() -> None:
    """Regression: a host that cannot set its clock, with the update check off,
    left ``_online`` False forever - so it retried every 5 minutes having made
    no request at all, and reported itself offline on a working network."""
    w = _worker(can_set_clock=False, cfg=_cfg(auto_update_check=False))
    w._online = True
    w._run_cycle("periodic")
    assert w._online is True  # nothing attempted -> verdict untouched


def test_health_reports_a_blank_repo_as_not_attempted() -> None:
    w = _worker(cfg=_cfg(auto_time_sync=False, update_github_repo="  "))
    w._run_cycle("startup")
    assert w.health()["update_check"]["outcome"] == "not attempted"
    assert "no update_github_repo" in w.health()["update_check"]["detail"]


def test_health_reports_an_implausible_epoch_as_reached() -> None:
    """The server answered, which is the connectivity fact. Ignoring what it
    said is not a network failure."""
    w = _worker(cfg=_cfg(auto_update_check=False), ntp_query=lambda _s, _t: 1.0)
    w._run_cycle("startup")
    health = w.health()
    assert health["time_sync"]["outcome"] == "reached"
    assert "implausible epoch" in health["time_sync"]["detail"]


def test_health_reports_up_to_date() -> None:
    w = _worker(cfg=_cfg(auto_time_sync=False), update_check=lambda *_a, **_k: {"available": False})
    w._run_cycle("startup")
    assert "up to date" in w.health()["update_check"]["detail"]


def test_health_truncates_a_long_error_detail() -> None:
    """Error text lands in an artefact attached to public issues; an unbounded
    exception string would take the section with it."""
    w = _worker(cfg=_cfg(auto_update_check=False), ntp_query=_raiser(OSError("x" * 500)))
    w._run_cycle("startup")
    assert len(w.health()["time_sync"]["detail"]) == 200


def test_health_reports_the_configured_targets_before_any_cycle() -> None:
    """The startup delay is long enough to download a bundle inside it. An
    empty record read as "both checks disabled" on a station where both are
    on, because the renderer keys that verdict on the enabled flags."""
    w = _worker(cfg=_cfg(time_sync_server="ptbtime1.ptb.de", update_github_repo="owner/repo"))
    health = w.health()
    assert health["cycles"] == 0
    assert health["time_sync"]["outcome"] == "never attempted"
    assert health["time_sync"]["enabled"] is True
    assert health["time_sync"]["target"] == "ptbtime1.ptb.de"
    assert health["update_check"]["enabled"] is True
    assert health["update_check"]["target"] == "owner/repo"


def test_health_reports_disabled_checks_before_any_cycle() -> None:
    w = _worker(cfg=_cfg(auto_time_sync=False, auto_update_check=False))
    health = w.health()
    assert health["time_sync"]["enabled"] is False
    assert health["update_check"]["enabled"] is False


def test_health_keeps_a_recorded_row_over_the_live_config() -> None:
    """Once a cycle has run, the row describes what that cycle actually did -
    a config edited afterwards must not rewrite history."""
    w = _worker(cfg=_cfg(auto_update_check=False), ntp_query=_raiser(TimeoutError("x")))
    w._run_cycle("startup")
    health = w.health()
    assert health["time_sync"]["outcome"] == "unreachable"
    assert health["update_check"]["outcome"] == "not attempted"
    assert health["update_check"]["enabled"] is False


def test_health_says_so_when_the_clock_could_not_be_set() -> None:
    """``set_system_clock`` returns False - it never raises - when the
    ``system.set_clock`` grant is absent or was revoked since the cached state
    was read. Reaching the server is still the connectivity fact, but
    reporting "clock set" regardless would put a plain untruth in the bundle."""
    w = _worker(
        cfg=_cfg(auto_update_check=False),
        ntp_query=lambda _s, _t: 1_735_700_000.0,
        wall=lambda: 1_700_000_000.0,
        set_clock=lambda _b, _e: False,
    )
    w._run_cycle("startup")
    health = w.health()
    assert health["time_sync"]["outcome"] == "reached"  # connectivity is unchanged
    assert "NOT set" in health["time_sync"]["detail"]
    assert "system.set_clock grant" in health["time_sync"]["detail"]


def test_health_reports_a_clock_that_was_actually_set() -> None:
    w = _worker(
        cfg=_cfg(auto_update_check=False),
        ntp_query=lambda _s, _t: 1_735_700_000.0,
        wall=lambda: 1_700_000_000.0,
        set_clock=lambda _b, _e: True,
    )
    w._run_cycle("startup")
    assert "clock set (drift was" in w.health()["time_sync"]["detail"]
