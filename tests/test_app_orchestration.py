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
        self.tick_started: list[object] = []
        self.draw_requests: list[object] = []

    def start_tick_animation(self, callback) -> None:  # noqa: ANN001
        self.tick_started.append(callback)

    def request_draw(self, callback) -> None:  # noqa: ANN001
        self.draw_requests.append(callback)


class _RecordingServices:
    """Captures the order of frame-loop service calls."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.frame_times: list[float] = []
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

    def update_marker_visuals(self) -> None:
        self.calls.append("update_marker_visuals")

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
        _check_video_disconnect_banner=_recorder("check_video_disconnect_banner"),
        _process_input=_recorder("process_input"),
        _refresh_iface_list=_recorder("refresh_iface_list"),
        _get_config_mtime=lambda: app._config_mtime,
    )
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

    def test_requests_next_draw_on_canvas(self) -> None:
        app = _make_fake_app()
        orch.animate(app)
        assert app._canvas.draw_requests == [app._animate]

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
        # Runs on a GLib timeout (not the vsync tick) so a headless device with no
        # display still services web-triggered update/config/restart requests.
        app = _make_fake_app()
        result = orch.housekeeping(app)
        assert result is True  # truthy -> GLib re-arms the timeout
        assert app._calls == [
            "check_config_reload",
            "check_update_request",
            "check_restart_request",
            "check_pi_network_worker",
            "check_button_detection_request",
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

    def test_shuts_down_when_start_tick_animation_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        glib, gtk = _FakeGLib(), _FakeGtk()
        self._patch_gi(monkeypatch, glib, gtk)
        app = _make_fake_app()

        def _boom(_cb: object) -> None:
            raise RuntimeError("canvas init failed")

        app._canvas.start_tick_animation = _boom  # type: ignore[method-assign]

        # A failure starting the tick must still run shutdown (the finally now
        # wraps it) and never reach Gtk.main.
        with pytest.raises(RuntimeError, match="canvas init failed"):
            orch.run_native_loop(app)
        assert app._runtime_services.shutdown_calls == 1
        assert gtk.main_called == 0

    def test_starts_tick_animation_on_canvas(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch_gi(monkeypatch, _FakeGLib(), _FakeGtk())
        app = _make_fake_app()
        orch.run_native_loop(app)
        assert app._canvas.tick_started == [app._animate]

    def test_registers_display_independent_housekeeping_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        glib = _FakeGLib()
        self._patch_gi(monkeypatch, glib, _FakeGtk())
        app = _make_fake_app()
        orch.run_native_loop(app)
        # A GLib timeout drives the web-housekeeping checks so they survive a
        # missing display tick.
        assert glib.timeouts == [(orch._HOUSEKEEPING_INTERVAL_MS, app._run_housekeeping)]

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
