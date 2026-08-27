# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for :mod:`openfollow.runtime.app_orchestration`.

This module is the wiring between the GTK main loop, the per-frame
``animate`` tick, and the config-file hot-reload machinery.  Each function
is a thin seam that delegates to ``AppRuntimeServices`` + app callbacks;
the tests drive them through a recording fake app so we can assert on
ordering, throttling, and error-path semantics without touching GTK,
the filesystem, or a real GLib main loop.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

import openfollow.runtime.app_orchestration as orch

pytestmark = pytest.mark.unit

# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


class _FakeKeyboardHandler:
    def __init__(self, key_presses: list[str] | None = None) -> None:
        self._key_presses = list(key_presses or [])
        self.polled = 0
        self.consumed = 0

    def poll_discrete_keys(self) -> None:
        self.polled += 1

    def consume_key_presses(self) -> list[str]:
        self.consumed += 1
        out = list(self._key_presses)
        self._key_presses.clear()
        return out


class _FakeInputManager:
    def __init__(self, keys: list[str] | None = None) -> None:
        self.keyboard_handler = _FakeKeyboardHandler(keys)


class _FakeCanvas:
    def __init__(self) -> None:
        self.hud_ticks_started = 0
        self.is_closing = False

    def start_hud_tick(self) -> None:
        self.hud_ticks_started += 1


class _RecordingErrorLog:
    """Stand-in for the frame clock's ``ThrottledExceptionLogger``."""

    def __init__(self) -> None:
        self.calls = 0

    def log(self) -> None:
        self.calls += 1


class _RecordingServices:
    """Captures the order of frame-loop service calls."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.frame_times: list[float] = []
        self.marker_visual_dts: list[float] = []
        self.runtime_stats_calls = 0
        self._frame_metrics = SimpleNamespace(add_frame=self._record_frame_time)
        self.shutdown_calls = 0

    def _record_frame_time(self, frame_time: float) -> None:
        self.frame_times.append(frame_time)

    def update_video(self) -> None:
        self.calls.append("update_video")

    def apply_detection_pin(self, dt: float = 0.0) -> None:
        self.calls.append("apply_detection_pin")

    def update_zone_triggers(self) -> None:
        self.calls.append("update_zone_triggers")

    def update_marker_visuals(self, dt: float = 0.0) -> None:
        self.calls.append("update_marker_visuals")
        self.marker_visual_dts.append(dt)

    def publish_runtime_stats(self) -> None:
        self.runtime_stats_calls += 1

    def shutdown(self) -> None:
        self.shutdown_calls += 1


def _make_fake_app(
    *,
    iface_selection_active: bool = False,
    input_manager: _FakeInputManager | None = None,
    config_path: str = "/tmp/does-not-exist.toml",
) -> SimpleNamespace:
    """Return a ``SimpleNamespace`` that satisfies the orchestration API."""
    calls: list[str] = []

    def _recorder(name: str):
        def _fn(*args, **kwargs) -> None:
            calls.append(name)

        return _fn

    canvas = _FakeCanvas()
    services = _RecordingServices()
    app = SimpleNamespace(
        _input_manager=input_manager,
        _iface_selection_active=iface_selection_active,
        _last_iface_refresh=0.0,
        _last_animate_time=None,
        _runtime_services=services,
        _canvas=canvas,
        _animate=lambda: None,
        _run_housekeeping=lambda: True,
        _config_path=config_path,
        _last_config_check=0.0,
        _config_mtime=100.0,
        # recorders
        _calls=calls,
        _handle_key_press=_recorder("handle_key_press"),
        _check_config_reload=_recorder("check_config_reload"),
        _check_update_request=_recorder("check_update_request"),
        _check_restart_request=_recorder("check_restart_request"),
        _check_pi_network_worker=_recorder("check_pi_network_worker"),
        _check_button_detection_request=_recorder("check_button_detection_request"),
        _check_marker_speeds_persist=_recorder("check_marker_speeds_persist"),
        _check_frame_loop_stall=_recorder("check_frame_loop_stall"),
        _frame_stalled=False,
        _frame_stall_since=0.0,
        _check_video_disconnect_banner=_recorder("check_video_disconnect_banner"),
        _process_input=_recorder("process_input"),
        _refresh_iface_list=_recorder("refresh_iface_list"),
        _get_config_mtime=lambda: app._config_mtime,
        _frame_err_log=_RecordingErrorLog(),
        _last_frame_completed=None,
    )
    app._animate = lambda: orch.animate(app)
    app._run_frame = lambda: orch.run_frame(app)
    return app


# --------------------------------------------------------------------------- #
# animate
# --------------------------------------------------------------------------- #


class TestAnimate:
    def test_frame_loop_service_call_order_is_stable(self) -> None:
        app = _make_fake_app()
        orch.animate(app)
        # update_controller_status was removed when binding moved to cards.
        # Contract: update_video → apply_detection_pin →
        # update_zone_triggers → update_marker_visuals.
        assert app._runtime_services.calls == [
            "update_video",
            "apply_detection_pin",
            "update_zone_triggers",
            "update_marker_visuals",
        ]

    def test_calls_publish_runtime_stats_each_tick(self) -> None:
        app = _make_fake_app()
        orch.animate(app)
        orch.animate(app)
        assert app._runtime_services.runtime_stats_calls == 2

    def test_records_frame_timing_sample(self) -> None:
        app = _make_fake_app()
        orch.animate(app)
        assert len(app._runtime_services.frame_times) == 1
        assert app._runtime_services.frame_times[0] >= 0.0

    def test_dt_first_tick_uses_fallback(self) -> None:
        # #553: no previous tick yet → fall back to the nominal 1/60 step.
        captured: list[float] = []
        app = _make_fake_app()
        app._process_input = lambda dt: captured.append(dt)
        orch.animate(app)
        assert captured == [pytest.approx(orch._DEFAULT_FRAME_DT)]
        assert app._last_animate_time is not None

    def test_dt_clamped_after_stall(self) -> None:
        # #553: a long gap since the last tick is clamped so the marker can't
        # teleport on the catch-up frame.
        captured: list[float] = []
        app = _make_fake_app()
        app._process_input = lambda dt: captured.append(dt)
        orch.animate(app)  # establishes _last_animate_time
        app._last_animate_time -= 5.0  # simulate a 5 s stall since the last tick
        orch.animate(app)
        assert captured[-1] == pytest.approx(orch._MAX_FRAME_DT)

    def test_frame_dt_reaches_update_marker_visuals(self) -> None:
        """The velocity each controlled marker broadcasts integrates against
        the same frame step the input path moved it by."""
        app = _make_fake_app()
        orch.animate(app)
        app._last_animate_time -= 5.0
        orch.animate(app)
        assert app._runtime_services.marker_visual_dts == [
            pytest.approx(orch._DEFAULT_FRAME_DT),
            pytest.approx(orch._MAX_FRAME_DT),
        ]

    def test_no_input_manager_skips_keyboard_poll(self) -> None:
        app = _make_fake_app(input_manager=None)
        orch.animate(app)
        # Still runs the normal-mode branch; no exception from missing manager.
        assert "process_input" in app._calls

    def test_input_manager_polls_and_consumes_keys(self) -> None:
        mgr = _FakeInputManager(["a", "b"])
        app = _make_fake_app(input_manager=mgr)
        orch.animate(app)
        assert mgr.keyboard_handler.polled == 1
        assert mgr.keyboard_handler.consumed == 1
        assert app._calls.count("handle_key_press") == 2

    def test_runs_display_bound_checks(self) -> None:
        app = _make_fake_app()
        orch.animate(app)
        assert "process_input" in app._calls
        assert "check_video_disconnect_banner" in app._calls

    def test_does_not_run_web_housekeeping(self) -> None:
        # Update / config-reload / restart / button-detection moved to the
        # display-independent housekeeping timeout. animate must NOT run them, or
        # they'd double-fire on a device that does have a display tick.
        app = _make_fake_app()
        orch.animate(app)
        for moved in (
            "check_config_reload",
            "check_update_request",
            "check_restart_request",
            "check_pi_network_worker",
            "check_button_detection_request",
        ):
            assert moved not in app._calls

    def test_iface_selection_refresh_respects_1hz_throttle(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`_refresh_iface_list` should run once per second (monotonic)."""
        times = iter([1000.0, 1000.0, 1000.5, 1001.5])
        monkeypatch.setattr(orch.time, "monotonic", lambda: next(times))

        app = _make_fake_app(iface_selection_active=True)
        app._last_iface_refresh = 0.0
        orch.animate(app)  # monotonic=1000.0 → first refresh (delta 1000 ≥ 1)
        orch.animate(app)  # monotonic=1000.5 → throttled (delta 0.5 < 1)
        assert app._calls.count("refresh_iface_list") == 1

    def test_iface_selection_inactive_never_refreshes(self) -> None:
        app = _make_fake_app(iface_selection_active=False)
        orch.animate(app)
        assert "refresh_iface_list" not in app._calls


# --------------------------------------------------------------------------- #
# housekeeping
# --------------------------------------------------------------------------- #


class TestHousekeeping:
    def test_runs_web_driven_checks_and_rearms(self) -> None:
        # Its own 10 Hz timeout, so these keep their cadence rather than running
        # 60 times a second alongside the frame clock.
        app = _make_fake_app()
        result = orch.housekeeping(app)
        assert result is True  # truthy -> GLib re-arms the timeout
        assert app._calls == [
            "check_config_reload",
            "check_update_request",
            "check_restart_request",
            "check_pi_network_worker",
            "check_button_detection_request",
            "check_marker_speeds_persist",
            # The stall watchdog belongs here and not on the frame clock: a
            # stalled loop cannot report its own stall.
            "check_frame_loop_stall",
        ]

    def test_swallows_check_exception_and_keeps_timer(self) -> None:
        # #553 Low (+ review on #608): one raising check must not tear down the
        # GLib source AND must not starve the checks after it – each is guarded
        # independently.
        app = _make_fake_app()

        def _boom() -> None:
            raise RuntimeError("check blew up")

        app._check_restart_request = _boom
        assert orch.housekeeping(app) is True  # still re-arms
        # The checks before and after the failing one all ran.
        assert app._calls == [
            "check_config_reload",
            "check_update_request",
            "check_pi_network_worker",
            "check_button_detection_request",
            "check_marker_speeds_persist",
            "check_frame_loop_stall",
        ]


# --------------------------------------------------------------------------- #
# get_config_mtime
# --------------------------------------------------------------------------- #


class TestGetConfigMtime:
    def test_returns_filesystem_mtime_for_existing_file(self, tmp_path) -> None:  # noqa: ANN001
        p = tmp_path / "config.toml"
        p.write_text("[grid]\n")
        app = SimpleNamespace(_config_path=str(p))
        mtime = orch.get_config_mtime(app)
        assert mtime > 0.0

    def test_returns_zero_when_path_missing(self, tmp_path) -> None:  # noqa: ANN001
        app = SimpleNamespace(_config_path=str(tmp_path / "nope.toml"))
        assert orch.get_config_mtime(app) == 0.0


# --------------------------------------------------------------------------- #
# check_config_reload
# --------------------------------------------------------------------------- #


class TestCheckConfigReload:
    def _app(self, config_path: str) -> SimpleNamespace:
        return SimpleNamespace(
            _config_path=config_path,
            _config_mtime=100.0,
            _last_config_check=0.0,
            _get_config_mtime=lambda: 200.0,
            _config=None,
        )

    def test_throttled_within_1_second(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(orch.time, "monotonic", lambda: 10.0)
        load_calls: list[str] = []
        monkeypatch.setattr(orch, "load_config", lambda *a, **kw: load_calls.append("load"))

        app = self._app("/tmp/x.toml")
        app._last_config_check = 9.5  # < 1.0s ago
        orch.check_config_reload(app)
        # Neither mtime read nor load_config should fire.
        assert load_calls == []

    def test_no_reload_when_mtime_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(orch.time, "monotonic", lambda: 10.0)
        load_calls: list[str] = []
        monkeypatch.setattr(orch, "load_config", lambda *a, **kw: load_calls.append("load"))

        app = self._app("/tmp/x.toml")
        app._get_config_mtime = lambda: 100.0  # equal to _config_mtime
        orch.check_config_reload(app)
        assert load_calls == []

    def test_load_failure_keeps_old_config_mtime(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(orch.time, "monotonic", lambda: 10.0)

        def _boom(*a, **kw):  # noqa: ANN001
            raise RuntimeError("malformed toml")

        monkeypatch.setattr(orch, "load_config", _boom)

        apply_calls: list[object] = []
        monkeypatch.setattr(
            orch,
            "apply_runtime_config_changes",
            lambda *a, **kw: apply_calls.append(a),
        )
        app = self._app("/tmp/x.toml")
        orch.check_config_reload(app)
        # Load errored → apply_runtime_config_changes not called; mtime keeps
        # its old value so a retry on the same mtime still re-attempts.
        assert apply_calls == []
        assert app._config_mtime == 100.0

    def test_apply_failure_keeps_old_config_mtime(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(orch.time, "monotonic", lambda: 10.0)
        monkeypatch.setattr(orch, "load_config", lambda *a, **kw: object())

        def _boom(*a, **kw):  # noqa: ANN001
            raise RuntimeError("camera re-init failed")

        monkeypatch.setattr(orch, "apply_runtime_config_changes", _boom)

        app = self._app("/tmp/x.toml")
        orch.check_config_reload(app)
        # Apply errored → _config_mtime stays at its old value.
        assert app._config_mtime == 100.0

    def test_successful_reload_advances_mtime(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(orch.time, "monotonic", lambda: 10.0)
        monkeypatch.setattr(orch, "load_config", lambda *a, **kw: object())

        applied: list[object] = []

        def _apply(app, cfg):  # noqa: ANN001
            applied.append(cfg)
            return True  # fully applied

        monkeypatch.setattr(orch, "apply_runtime_config_changes", _apply)

        app = self._app("/tmp/x.toml")
        orch.check_config_reload(app)
        assert app._config_mtime == 200.0
        assert len(applied) == 1

    def test_partial_apply_keeps_old_mtime_for_retry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # A degraded _apply_with_fallback section returns False (not a raise):
        # the mtime must stay put so the next poll retries the reverted section.
        monkeypatch.setattr(orch.time, "monotonic", lambda: 10.0)
        monkeypatch.setattr(orch, "load_config", lambda *a, **kw: object())

        calls: list[object] = []

        def _apply(app, cfg):  # noqa: ANN001
            calls.append(cfg)
            return False  # one section degraded + reverted

        monkeypatch.setattr(orch, "apply_runtime_config_changes", _apply)

        app = self._app("/tmp/x.toml")
        orch.check_config_reload(app)
        assert app._config_mtime == 100.0  # not advanced → retries next poll
        assert len(calls) == 1


# --------------------------------------------------------------------------- #
# run_native_loop
# --------------------------------------------------------------------------- #


class _FakeGLib:
    def __init__(self) -> None:
        self.signals: list[tuple[int, int, object]] = []
        self.timeouts: list[tuple[int, object]] = []
        self.PRIORITY_HIGH = 300

    def unix_signal_add(self, priority: int, sig: int, handler: object) -> None:
        self.signals.append((priority, sig, handler))

    def timeout_add(self, interval_ms: int, callback: object) -> int:
        self.timeouts.append((interval_ms, callback))
        return len(self.timeouts)


class _FakeGtk:
    def __init__(self, raise_in_main: bool = False) -> None:
        self.main_called = 0
        self._raise = raise_in_main

    def main_quit(self) -> None:
        pass

    def main(self) -> None:
        self.main_called += 1
        if self._raise:
            raise KeyboardInterrupt("simulated Ctrl+C")


class TestRunNativeLoop:
    def _patch_gi(self, monkeypatch: pytest.MonkeyPatch, glib, gtk) -> None:  # noqa: ANN001
        import gi.repository as repo

        monkeypatch.setattr(repo, "GLib", glib, raising=False)
        monkeypatch.setattr(repo, "Gtk", gtk, raising=False)

    def test_wires_sigint_and_sigterm_to_gtk_main_quit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import signal as _signal

        glib = _FakeGLib()
        gtk = _FakeGtk()
        self._patch_gi(monkeypatch, glib, gtk)

        app = _make_fake_app()
        orch.run_native_loop(app)

        # Both SIGINT (dev Ctrl-C) and SIGTERM (systemd stop/restart) route to
        # Gtk.main_quit so the graceful-shutdown finally always runs.
        assert {sig for _prio, sig, _h in glib.signals} == {_signal.SIGINT, _signal.SIGTERM}
        for priority, _sig, handler in glib.signals:
            assert priority == glib.PRIORITY_HIGH
            assert handler == gtk.main_quit

    def test_shuts_down_when_start_hud_tick_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        glib, gtk = _FakeGLib(), _FakeGtk()
        self._patch_gi(monkeypatch, glib, gtk)
        app = _make_fake_app()

        def _boom() -> None:
            raise RuntimeError("canvas init failed")

        app._canvas.start_hud_tick = _boom  # type: ignore[method-assign]

        # A failure starting the tick must still run shutdown (the finally now
        # wraps it) and never reach Gtk.main.
        with pytest.raises(RuntimeError, match="canvas init failed"):
            orch.run_native_loop(app)
        assert app._runtime_services.shutdown_calls == 1
        assert gtk.main_called == 0

    def test_starts_a_hud_only_tick_on_canvas(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch_gi(monkeypatch, _FakeGLib(), _FakeGtk())
        app = _make_fake_app()
        orch.run_native_loop(app)
        # The display tick redraws the HUD and takes no app callback: handing it
        # the frame loop is what froze marker state on a headless unit.
        assert app._canvas.hud_ticks_started == 1

    def test_registers_display_independent_frame_and_housekeeping_timeouts(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        glib = _FakeGLib()
        self._patch_gi(monkeypatch, glib, _FakeGtk())
        app = _make_fake_app()
        orch.run_native_loop(app)
        # Both clocks are GLib timeouts, so neither depends on a display.
        assert glib.timeouts == [
            (orch._FRAME_INTERVAL_MS, app._run_frame),
            (orch._HOUSEKEEPING_INTERVAL_MS, app._run_housekeeping),
        ]

    def test_calls_gtk_main(self, monkeypatch: pytest.MonkeyPatch) -> None:
        glib, gtk = _FakeGLib(), _FakeGtk()
        self._patch_gi(monkeypatch, glib, gtk)
        app = _make_fake_app()
        orch.run_native_loop(app)
        assert gtk.main_called == 1

    def test_shuts_down_services_after_main_returns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        glib, gtk = _FakeGLib(), _FakeGtk()
        self._patch_gi(monkeypatch, glib, gtk)
        app = _make_fake_app()
        orch.run_native_loop(app)
        assert app._runtime_services.shutdown_calls == 1

    def test_shuts_down_services_even_when_main_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        glib, gtk = _FakeGLib(), _FakeGtk(raise_in_main=True)
        self._patch_gi(monkeypatch, glib, gtk)
        app = _make_fake_app()
        with pytest.raises(KeyboardInterrupt):
            orch.run_native_loop(app)
        assert app._runtime_services.shutdown_calls == 1


# --------------------------------------------------------------------------- #
# run_frame
# --------------------------------------------------------------------------- #


class TestRunFrame:
    """The frame-clock timeout callback.

    A unit with no display attached gets no compositor frame clock, so anything
    driven by the display tick never runs. These pin that the frame loop is
    reachable without one, and that it cannot silently stop.
    """

    def test_headless_unit_still_runs_the_frame_loop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        glib, gtk = _FakeGLib(), _FakeGtk()
        import gi.repository as repo

        monkeypatch.setattr(repo, "GLib", glib, raising=False)
        monkeypatch.setattr(repo, "Gtk", gtk, raising=False)
        app = _make_fake_app()
        orch.run_native_loop(app)

        # Nothing ever fires the canvas tick – exactly a unit with no HDMI
        # attached. Drive only the registered GLib timeouts.
        frame_cb = next(cb for interval, cb in glib.timeouts if interval == orch._FRAME_INTERVAL_MS)
        for _ in range(3):
            assert frame_cb() is True

        # Marker state, detection pinning and zone evaluation all advanced.
        assert app._runtime_services.calls.count("update_marker_visuals") == 3
        assert app._runtime_services.calls.count("apply_detection_pin") == 3
        assert app._calls.count("process_input") == 3

    def test_returns_true_so_the_timeout_rearms(self) -> None:
        app = _make_fake_app()
        assert orch.run_frame(app) is True

    def test_raising_frame_is_logged_and_the_loop_survives(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app = _make_fake_app()

        def _boom() -> None:
            raise RuntimeError("frame blew up")

        app._animate = _boom
        # Dropping the source here would freeze marker state for the rest of the
        # process while every output kept transmitting the last position.
        assert orch.run_frame(app) is True
        assert orch.run_frame(app) is True
        assert app._frame_err_log.calls == 2

    def test_a_frame_that_raises_every_tick_reads_as_stalled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The stamp is taken on completion, not entry. Stamping on entry would
        let a frame that raises on every tick advance the liveness clock while
        no marker is ever written: the watchdog and the chip would both read
        healthy, and the only symptom would be every output going quiet."""
        app = _make_fake_app()
        app._last_frame_completed = 500.0

        def _boom() -> None:
            raise RuntimeError("frame blew up")

        app._animate = _boom
        for _ in range(5):
            assert orch.run_frame(app) is True
        # Nothing completed, so the liveness stamp never moved.
        assert app._last_frame_completed == 500.0

        monkeypatch.setattr(orch.time, "perf_counter", lambda: 505.0)
        orch.check_frame_loop_stall(app)
        assert app._frame_stalled is True

    def test_a_completed_frame_advances_the_liveness_stamp(self) -> None:
        app = _make_fake_app()
        assert app._last_frame_completed is None
        orch.run_frame(app)
        assert app._last_frame_completed is not None

    def test_drops_the_source_while_the_window_is_closing(self) -> None:
        app = _make_fake_app()
        app._canvas.is_closing = True
        assert orch.run_frame(app) is False
        assert app._runtime_services.calls == []

    def test_drops_the_source_when_the_canvas_is_gone(self) -> None:
        app = _make_fake_app()
        app._canvas = None
        assert orch.run_frame(app) is False
        assert app._runtime_services.calls == []


# --------------------------------------------------------------------------- #
# check_frame_loop_stall
# --------------------------------------------------------------------------- #


class TestCheckFrameLoopStall:
    """The watchdog that makes a dead frame loop loud instead of silent.

    Every assertion drives an injected ``perf_counter`` – a real sleep here
    would be a wall-clock flake, and the threshold is a full second.
    """

    def _run_at(self, app, monkeypatch: pytest.MonkeyPatch, now: float) -> None:  # noqa: ANN001
        monkeypatch.setattr(orch.time, "perf_counter", lambda: now)
        orch.check_frame_loop_stall(app)

    def test_no_verdict_before_the_first_frame(self, monkeypatch: pytest.MonkeyPatch, caplog) -> None:  # noqa: ANN001
        app = _make_fake_app()
        app._last_frame_completed = None
        with caplog.at_level(logging.INFO, logger=orch.logger.name):
            self._run_at(app, monkeypatch, 500.0)
        assert app._frame_stalled is False
        assert caplog.records == []

    @pytest.mark.parametrize("elapsed", [0.0, 0.5, 0.999])
    def test_a_merely_slow_frame_is_not_a_stall(
        self,
        monkeypatch: pytest.MonkeyPatch,
        elapsed: float,
    ) -> None:
        app = _make_fake_app()
        app._last_frame_completed = 100.0
        self._run_at(app, monkeypatch, 100.0 + elapsed)
        assert app._frame_stalled is False

    @pytest.mark.parametrize("elapsed", [1.0, 4.0])
    def test_flags_and_warns_once_the_threshold_is_crossed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog,  # noqa: ANN001
        elapsed: float,
    ) -> None:
        app = _make_fake_app()
        app._last_frame_completed = 100.0
        with caplog.at_level(logging.WARNING, logger=orch.logger.name):
            self._run_at(app, monkeypatch, 100.0 + elapsed)
        assert app._frame_stalled is True
        assert len(caplog.records) == 1
        # The operator-facing symptom, not the internal cause: a console sees a
        # healthy stream whose coordinates never move.
        assert "being withheld" in caplog.records[0].getMessage()

    def test_warns_once_per_episode_not_once_per_poll(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog,  # noqa: ANN001
    ) -> None:
        app = _make_fake_app()
        app._last_frame_completed = 100.0
        with caplog.at_level(logging.WARNING, logger=orch.logger.name):
            for tick in range(10):
                self._run_at(app, monkeypatch, 102.0 + tick * 0.1)
        # At 10 Hz an un-deduplicated warning would be 10 lines here, and would
        # keep going for as long as the station is wedged.
        assert len(caplog.records) == 1

    def test_recovery_logs_the_whole_outage_not_the_time_since_detection(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog,  # noqa: ANN001
    ) -> None:
        """The loop stops at 100 and recovers at 108, so the outage is 8s. Timing
        it from detection (105) reports 3s and leaves an operator to add two
        journal lines together to find out how long the station was frozen."""
        app = _make_fake_app()
        app._last_frame_completed = 100.0
        self._run_at(app, monkeypatch, 105.0)
        assert app._frame_stalled is True

        app._last_frame_completed = 108.0  # frames landing again
        caplog.clear()
        with caplog.at_level(logging.INFO, logger=orch.logger.name):
            self._run_at(app, monkeypatch, 108.0)
        assert app._frame_stalled is False
        assert len(caplog.records) == 1
        assert "resumed after 8.0s" in caplog.records[0].getMessage()

    def test_a_second_episode_warns_again(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog,  # noqa: ANN001
    ) -> None:
        app = _make_fake_app()
        with caplog.at_level(logging.WARNING, logger=orch.logger.name):
            app._last_frame_completed = 100.0
            self._run_at(app, monkeypatch, 102.0)
            app._last_frame_completed = 103.0
            self._run_at(app, monkeypatch, 103.0)
            app._last_frame_completed = 103.0
            self._run_at(app, monkeypatch, 106.0)
        # A recovered-then-restalled station must not stay silent the second time.
        assert len(caplog.records) == 2


# --------------------------------------------------------------------------- #
# check_marker_speeds_persist
# --------------------------------------------------------------------------- #


class TestCheckMarkerSpeedsPersist:
    """The debounced disk flush for the runtime-authoritative per-marker speeds.

    Exercised through the public ``check_marker_speeds_persist`` boundary against
    a real config file, so the assertions are about observable disk state, not
    the helper's internals.
    """

    def _app(self, tmp_path, live_speeds: dict[int, float], dirty: bool, dirty_since: float):
        from openfollow.configuration import AppConfig, save_config

        config_path = tmp_path / "config.toml"
        # Seed a real on-disk config the flush will re-load fresh.
        save_config(AppConfig(controlled_marker_ids=list(live_speeds)), str(config_path))
        cfg = AppConfig(
            controlled_marker_ids=list(live_speeds),
            marker_move_speeds=dict(live_speeds),
        )
        return SimpleNamespace(
            _config_path=str(config_path),
            _config=cfg,
            _marker_speeds_dirty=dirty,
            _marker_speeds_dirty_since=dirty_since,
        )

    def test_clean_app_never_flushes(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        from openfollow.configuration import load_config

        monkeypatch.setattr(orch.time, "monotonic", lambda: 1000.0)
        app = self._app(tmp_path, {5: 2.7}, dirty=False, dirty_since=0.0)
        orch.check_marker_speeds_persist(app)
        # Disk still has no speeds; nothing was written.
        assert load_config(app._config_path).marker_move_speeds == {}
        assert app._marker_speeds_dirty is False

    def test_dirty_before_settle_window_does_not_flush(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        from openfollow.configuration import load_config

        monkeypatch.setattr(orch.time, "monotonic", lambda: 100.0)
        # Edited 1.0s ago; the settle window is 2.5s, so no flush yet.
        app = self._app(tmp_path, {5: 2.7}, dirty=True, dirty_since=99.0)
        orch.check_marker_speeds_persist(app)
        assert load_config(app._config_path).marker_move_speeds == {}
        assert app._marker_speeds_dirty is True  # still pending

    def test_flushes_and_clears_after_settle_window(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        from openfollow.configuration import load_config

        monkeypatch.setattr(orch.time, "monotonic", lambda: 100.0)
        # Edited 3.0s ago (>= 2.5s settle) → flush.
        app = self._app(tmp_path, {5: 2.7}, dirty=True, dirty_since=97.0)
        orch.check_marker_speeds_persist(app)
        assert load_config(app._config_path).marker_move_speeds == {5: 2.7}
        assert app._marker_speeds_dirty is False

    def test_flush_holds_config_write_lock(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The flush must serialise on ``config_write_lock`` so it can't race a
        concurrent web section save."""
        monkeypatch.setattr(orch.time, "monotonic", lambda: 100.0)
        app = self._app(tmp_path, {5: 2.7}, dirty=True, dirty_since=90.0)

        observed: list[bool] = []
        real_save = orch.save_config

        def _spy_save(cfg, path):  # noqa: ANN001
            observed.append(orch.config_write_lock.locked())
            return real_save(cfg, path)

        monkeypatch.setattr(orch, "save_config", _spy_save)
        orch.check_marker_speeds_persist(app)
        assert observed == [True]  # lock held across the save

    def test_save_failure_is_swallowed_stays_dirty_and_backs_off(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failed disk write must not crash the housekeeping loop and must leave
        the config marked dirty so a later tick retries. On failure the edit
        timestamp is advanced to now, so a persistent failure backs off to the
        settle cadence instead of retrying + logging on every tick."""
        monkeypatch.setattr(orch.time, "monotonic", lambda: 100.0)
        app = self._app(tmp_path, {5: 2.7}, dirty=True, dirty_since=90.0)
        before = app._marker_speeds_dirty_since

        def _boom(*_a, **_kw):
            raise OSError("disk full")

        monkeypatch.setattr(orch, "save_config", _boom)
        # Must not raise.
        orch.check_marker_speeds_persist(app)
        # Still dirty → retried later; the lock was released.
        assert app._marker_speeds_dirty is True
        assert orch.config_write_lock.locked() is False
        # Backoff: the timestamp was advanced to now, so the next flush attempt
        # waits out the settle window again rather than firing every tick.
        assert app._marker_speeds_dirty_since == 100.0
        assert app._marker_speeds_dirty_since != before

    def test_malformed_config_is_not_overwritten(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The flush loads ``strict=True``, so a malformed / unparseable
        ``config.toml`` (e.g. an in-progress hand-edit) is left byte-for-byte
        intact instead of being silently healed to its ``.bak`` snapshot or to
        defaults. A background flush must never clobber a bad config."""
        monkeypatch.setattr(orch.time, "monotonic", lambda: 100.0)
        app = self._app(tmp_path, {5: 2.7}, dirty=True, dirty_since=90.0)

        # Corrupt the on-disk config (unterminated table header -> invalid TOML).
        bad_bytes = b"[grid\nthis = is not valid toml ]]]"
        config_path = tmp_path / "config.toml"
        config_path.write_bytes(bad_bytes)

        # Must not raise.
        orch.check_marker_speeds_persist(app)

        # The bad file is untouched (strict load raised before any save) and the
        # config stays dirty so it retries once the file is valid again.
        assert config_path.read_bytes() == bad_bytes
        assert app._marker_speeds_dirty is True

    def test_flush_preserves_a_concurrently_saved_section(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The flush loads the config fresh from disk and only injects the live
        speeds, so an unrelated field another writer just persisted survives –
        it does NOT save the whole in-memory config wholesale."""
        from openfollow.configuration import AppConfig, load_config, save_config

        monkeypatch.setattr(orch.time, "monotonic", lambda: 100.0)
        config_path = tmp_path / "config.toml"
        # A web save landed on disk with a changed grid width; the live app's
        # in-memory config still has the OLD width (it hasn't reloaded yet).
        on_disk = AppConfig(controlled_marker_ids=[5])
        on_disk.grid.width = 42.0
        save_config(on_disk, str(config_path))

        live = AppConfig(controlled_marker_ids=[5], marker_move_speeds={5: 2.7})
        live.grid.width = 10.0  # stale in-memory value
        app = SimpleNamespace(
            _config_path=str(config_path),
            _config=live,
            _marker_speeds_dirty=True,
            _marker_speeds_dirty_since=90.0,
        )

        orch.check_marker_speeds_persist(app)

        result = load_config(str(config_path))
        assert result.marker_move_speeds == {5: 2.7}  # live speeds injected
        assert result.grid.width == 42.0  # concurrent save NOT clobbered

    def test_restart_survival_round_trip(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        """adjust -> settle -> flush -> a fresh ``load_config`` (as a restart would
        do) shows the persisted speed."""
        from openfollow.configuration import AppConfig, load_config, save_config
        from openfollow.runtime.app_modes import adjust_move_speed

        config_path = tmp_path / "config.toml"
        save_config(AppConfig(controlled_marker_ids=[1]), str(config_path))

        cfg = AppConfig(controlled_marker_ids=[1], marker_move_speeds={1: 1.0})
        app = SimpleNamespace(
            _config_path=str(config_path),
            _config=cfg,
            _selected_id=1,
            _speed_key_streak={},
            _speed_key_last_t={},
            _speed_key_last_dir={},
            _marker_speeds_dirty=False,
            _marker_speeds_dirty_since=0.0,
            get_marker_move_speed=lambda mid: cfg.marker_move_speeds.get(mid, cfg.marker.move_speed),
        )

        monkeypatch.setattr(orch.time, "monotonic", lambda: 100.0)
        # app_modes uses its own ``time`` module; patch that too for a stable stamp.
        import openfollow.runtime.app_modes as modes

        monkeypatch.setattr(modes.time, "monotonic", lambda: 100.0)
        adjust_move_speed(app, +1)  # 1.0 -> 1.1, marks dirty at t=100.0

        # Settle window elapsed.
        monkeypatch.setattr(orch.time, "monotonic", lambda: 103.0)
        orch.check_marker_speeds_persist(app)

        reloaded = load_config(str(config_path))
        assert reloaded.marker_move_speeds == {1: 1.1}
