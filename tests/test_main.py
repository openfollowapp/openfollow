# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for the CLI entry point.

Covers behaviour required by the spec, not the implementation:
 - config path argument resolution (default + positional)
 - clean exit on ``KeyboardInterrupt`` and normal ``app.run()`` return
 - ``SystemExit`` propagates unchanged (is not treated as crash)
 - arbitrary exceptions trigger a restart with a 2s back-off
 - circuit breaker trips after ``_MAX_RESTARTS`` failures inside
   ``_RESTART_WINDOW`` and the process exits with status 1
 - restart timestamps older than the window are pruned so slow leaks
   never trigger the breaker
"""

from __future__ import annotations

import pytest

import openfollow.main as main_module

pytestmark = pytest.mark.unit


class _FakeRuntimeServices:
    def __init__(self, raise_on_shutdown: bool = False) -> None:
        self.shutdown_calls = 0
        self._raise = raise_on_shutdown

    def shutdown(self) -> None:
        self.shutdown_calls += 1
        if self._raise:
            raise RuntimeError("teardown boom")


class _FakeApp:
    """Scripted ``OpenFollowApp`` replacement.

    ``behaviours`` is a list of callables, one per ``OpenFollowApp(...)``
    construction.  Each behaviour is invoked when ``run()`` is called and
    its return value / raised exception drives the main loop branch
    under test.
    """

    def __init__(self, behaviours: list, call_log: list, *, raise_on_shutdown: bool = False) -> None:
        self._behaviours = behaviours
        self._call_log = call_log
        self._raise_on_shutdown = raise_on_shutdown
        # One services double per construction so a test can assert the loop
        # tore down each exited generation (the restart-leak fix).
        self.services_generations: list[_FakeRuntimeServices] = []
        self._runtime_services = _FakeRuntimeServices()

    def __call__(self, config_path: str, *, log_ring: object = None) -> _FakeApp:
        # log_ring is forwarded by main.py for the diagnostics bundle's journalctl fallback.
        # The test fake just records the construction; the kwarg is captured but not exercised.
        self._call_log.append(config_path)
        self._runtime_services = _FakeRuntimeServices(self._raise_on_shutdown)
        self.services_generations.append(self._runtime_services)
        return self

    def run(self) -> None:
        if not self._behaviours:
            raise AssertionError("run() called more times than scripted")
        behaviour = self._behaviours.pop(0)
        behaviour()


class _FakeClock:
    def __init__(self, times: list[float]) -> None:
        self._times = list(times)
        self._sleeps: list[float] = []

    def time(self) -> float:
        # Repeat the last value so overrun doesn't crash the loop.
        if len(self._times) == 1:
            return self._times[0]
        return self._times.pop(0)

    # The breaker window is timed with the monotonic clock; the loop reads it
    # where it previously read time.time(), so the scripted values drive both.
    monotonic = time

    def sleep(self, seconds: float) -> None:
        self._sleeps.append(seconds)


@pytest.fixture
def patched_main(monkeypatch):
    """Install a scripted ``OpenFollowApp`` and deterministic clock.

    Returns a helper that wires everything up for a given list of
    behaviours and a list of scripted ``time.time()`` values returned
    in order.
    """

    def _install(behaviours, times, argv=None):
        call_log: list[str] = []
        fake_app = _FakeApp(behaviours, call_log)
        clock = _FakeClock(times)
        monkeypatch.setattr(main_module, "OpenFollowApp", fake_app)
        monkeypatch.setattr(main_module.time, "time", clock.time)
        monkeypatch.setattr(main_module.time, "monotonic", clock.monotonic)
        monkeypatch.setattr(main_module.time, "sleep", clock.sleep)
        monkeypatch.setattr(main_module.sys, "argv", list(argv or ["openfollow"]))
        # main() calls setup_logging; isolate to avoid root logger mutation.
        # assertions). The CLI-loop tests don't care about logging
        # config so a no-op stand-in keeps them isolated.
        monkeypatch.setattr(
            main_module,
            "setup_logging",
            lambda **kw: None,
        )
        return call_log, clock

    return _install


def test_clean_exit_runs_once_with_default_config(patched_main) -> None:
    call_log, clock = patched_main(behaviours=[lambda: None], times=[0.0])

    main_module.main()

    assert call_log == ["config.toml"]
    assert clock._sleeps == []  # no restarts, no sleeps


def test_cli_forwards_positional_config_path(patched_main) -> None:
    call_log, _ = patched_main(
        behaviours=[lambda: None],
        times=[0.0],
        argv=["openfollow", "/tmp/custom.toml"],
    )

    main_module.main()

    assert call_log == ["/tmp/custom.toml"]


def test_keyboard_interrupt_exits_without_restart(patched_main) -> None:
    def _raise():
        raise KeyboardInterrupt

    call_log, clock = patched_main(behaviours=[_raise], times=[0.0])

    main_module.main()

    assert len(call_log) == 1
    assert clock._sleeps == []


def test_system_exit_propagates_unchanged(patched_main) -> None:
    def _sys_exit():
        raise SystemExit(7)

    patched_main(behaviours=[_sys_exit], times=[0.0])

    with pytest.raises(SystemExit) as excinfo:
        main_module.main()
    assert excinfo.value.code == 7


def test_crash_triggers_restart_then_clean_exit(patched_main) -> None:
    def _raise_once():
        raise RuntimeError("boom")

    call_log, clock = patched_main(
        behaviours=[_raise_once, lambda: None],
        times=[100.0, 100.1],
    )

    main_module.main()

    assert len(call_log) == 2
    assert clock._sleeps == [2]  # 2s back-off between attempts


def test_each_generation_is_torn_down_before_respawn(patched_main) -> None:
    """Restart-leak guard: the crashed instance's subsystems are shut down
    before the next generation, and a clean exit tears down too."""

    def _crash():
        raise RuntimeError("boom")

    patched_main(behaviours=[_crash, lambda: None], times=[100.0, 100.1])
    main_module.main()

    fake_app = main_module.OpenFollowApp  # the monkeypatched fake
    assert len(fake_app.services_generations) == 2
    assert all(s.shutdown_calls == 1 for s in fake_app.services_generations)


def test_constructor_crash_skips_teardown_and_restarts(monkeypatch) -> None:
    """If ``OpenFollowApp(...)`` itself raises, ``app`` stays unbound – the
    finally must skip the teardown (no AttributeError) and still restart."""
    calls = {"n": 0}

    def _flaky_ctor(config_path: str, *, log_ring: object = None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("ctor boom")
        return _FakeApp([lambda: None], [])(config_path, log_ring=log_ring)

    clock = _FakeClock([100.0, 100.1])
    monkeypatch.setattr(main_module, "OpenFollowApp", _flaky_ctor)
    monkeypatch.setattr(main_module.time, "time", clock.time)
    monkeypatch.setattr(main_module.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(main_module.time, "sleep", clock.sleep)
    monkeypatch.setattr(main_module.sys, "argv", ["openfollow"])
    monkeypatch.setattr(main_module, "setup_logging", lambda **kw: None)

    main_module.main()  # must not raise AttributeError

    assert calls["n"] == 2
    assert clock._sleeps == [2]


def test_teardown_error_is_swallowed_and_loop_continues(monkeypatch) -> None:
    """A teardown that itself raises is logged, never propagated, so the
    restart loop keeps going."""

    def _crash():
        raise RuntimeError("boom")

    call_log: list[str] = []
    fake_app = _FakeApp([_crash, lambda: None], call_log, raise_on_shutdown=True)
    clock = _FakeClock([100.0, 100.1])
    monkeypatch.setattr(main_module, "OpenFollowApp", fake_app)
    monkeypatch.setattr(main_module.time, "time", clock.time)
    monkeypatch.setattr(main_module.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(main_module.time, "sleep", clock.sleep)
    monkeypatch.setattr(main_module.sys, "argv", ["openfollow"])
    monkeypatch.setattr(main_module, "setup_logging", lambda **kw: None)

    main_module.main()  # teardown raising must not break the loop

    assert len(call_log) == 2


def test_circuit_breaker_exits_after_max_restarts_in_window(patched_main) -> None:
    def _always_crash():
        raise RuntimeError("crash")

    # MAX = 5, WINDOW = 300s.  All failures happen inside the window.
    behaviours = [_always_crash] * main_module._MAX_RESTARTS
    times = [float(i) for i in range(main_module._MAX_RESTARTS * 2 + 2)]

    call_log, _ = patched_main(behaviours=behaviours, times=times)

    with pytest.raises(SystemExit) as excinfo:
        main_module.main()

    assert excinfo.value.code == 1
    # Every construction crashed; breaker trips before the (MAX+1)th attempt.
    assert len(call_log) == main_module._MAX_RESTARTS


def test_old_restart_timestamps_are_pruned(patched_main) -> None:

    def _crash():
        raise RuntimeError("spaced crash")

    def _ok():
        return None

    behaviours = [_crash, _crash, _crash, _crash, _crash, _ok]
    # For each of 6 attempts we need one current_time read plus one
    # timestamp-append read after the exception.  Space them 400s apart.
    times: list[float] = []
    for i in range(6):
        base = i * 400.0
        times.extend([base, base + 0.1])

    call_log, _ = patched_main(behaviours=behaviours, times=times)

    # Should NOT raise SystemExit – each prior restart is pruned out.
    main_module.main()

    assert len(call_log) == 6


def test_forward_wall_clock_jump_does_not_defeat_breaker(monkeypatch) -> None:
    """A wall-clock jump forward of >window between restarts must not widen
    or reset the breaker window: the monotonic clock keeps the crashes inside
    the window so the breaker still trips after _MAX_RESTARTS.

    Pre-fix the loop pruned with time.time(); a forward jump there discarded
    every recorded restart so the breaker never tripped – the trailing clean
    behaviour would run and main() would return instead of exiting.
    """

    def _crash():
        raise RuntimeError("crash")

    # _MAX_RESTARTS crashes, then a clean exit. Post-fix the breaker trips
    # before the clean behaviour is ever reached; pre-fix all of them run.
    call_log: list[str] = []
    behaviours = [_crash] * main_module._MAX_RESTARTS + [lambda: None]
    fake_app = _FakeApp(behaviours, call_log)

    # Monotonic advances by a hair per attempt – every crash stays inside the
    # 300s window. Two reads per iteration (window check + crash timestamp).
    mono_clock = _FakeClock([float(i) for i in range(len(behaviours) * 2 + 2)])

    # The wall clock leaps far past the window on every iteration. If the loop
    # (wrongly) pruned with this, the breaker would never trip.
    wall_clock = _FakeClock([i * (main_module._RESTART_WINDOW + 1000.0) for i in range(len(behaviours) + 2)])

    monkeypatch.setattr(main_module, "OpenFollowApp", fake_app)
    monkeypatch.setattr(main_module.time, "time", wall_clock.time)
    monkeypatch.setattr(main_module.time, "monotonic", mono_clock.monotonic)
    monkeypatch.setattr(main_module.time, "sleep", lambda s: None)
    monkeypatch.setattr(main_module.sys, "argv", ["openfollow"])
    monkeypatch.setattr(main_module, "setup_logging", lambda **kw: None)

    with pytest.raises(SystemExit) as excinfo:
        main_module.main()

    assert excinfo.value.code == 1
    # Breaker tripped on the _MAX_RESTARTS crashes; the clean behaviour after
    # them is never reached.
    assert len(call_log) == main_module._MAX_RESTARTS
