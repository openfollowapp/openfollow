# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for ``runtime/online_sync.OnlineSyncWorker``.

Hermetic and thread-free: every network / clock action is injected as a stub
and the trigger logic is exercised by calling ``_maybe_cycle`` / ``_run_cycle``
directly with fake clocks. No real thread is started.
"""

from __future__ import annotations

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
) -> OnlineSyncWorker:
    return OnlineSyncWorker(
        config_provider=lambda: cfg or _cfg(),
        ip_provider=lambda: ip,
        broker=broker,
        web_commands=commands or _Commands(),
        version="0.3.0",
        periodic_interval=periodic_interval,
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
        ("", False),
        ("N/A", False),
        ("0.0.0.0", False),
        ("127.0.0.1", False),
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
    w._last_cycle_monotonic = 0.0
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


def test_maybe_cycle_fires_periodic_when_due() -> None:
    now = {"t": 10_000.0}
    w = _worker(ip="192.168.1.5", now=lambda: now["t"], periodic_interval=3600.0)
    fired = _spy_cycle(w)
    w._last_ip = "192.168.1.5"
    w._last_cycle_monotonic = 0.0  # 10_000 s elapsed >= 3600 -> due
    w._maybe_cycle()
    assert fired == ["periodic"]


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
    w = _worker()
    w._run = lambda: None  # type: ignore[method-assign]  # trivial thread body
    w.start()
    thread = w._thread
    assert thread is not None
    w.start()  # already running -> no second thread
    assert w._thread is thread
    w.stop()
    assert w._thread is None
    w.stop()  # already stopped -> no-op


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
