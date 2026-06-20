# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Mutation-audit kills for :mod:`openfollow.configuration`.

High-value mutation kills targeting ``openfollow/configuration.py``:

* ``_coerce_optional_float`` None-check inversion (``is None`` →
  ``is not None``) and forced ``float(None)`` call.
* ``save_config`` default path (``"config.toml"``) – the existing
  :mod:`tests.test_configuration` suite always passes a
  ``temp_config_path`` explicitly, hiding the default-value mutants.
* ``apply_runtime_config_changes`` field-propagation mutants on
  ``video_source_type`` (among others) – covers the runtime-update
  path that actually wires the new value into ``app._config``.

The remaining survivors are in ``_warn_deprecated_controller_bindings``
(log-message text) – audit-logged rather than killed, since killing them
would force tautological assertions on log text.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openfollow.configuration import (
    AppConfig,
    ControllerConfig,
    _coerce_optional_float,
    apply_runtime_config_changes,
    load_config,
    save_config,
)

pytestmark = pytest.mark.unit

# --------------------------------------------------------------------------- #
# _coerce_optional_float – None-check and float(value) call guards
# --------------------------------------------------------------------------- #


class TestCoerceOptionalFloatNoneHandling:
    def test_none_input_returns_none(self) -> None:
        assert _coerce_optional_float(None, default=5.0) is None

    def test_valid_numeric_returns_float(self) -> None:
        assert _coerce_optional_float(3, default=99.0) == 3.0
        assert _coerce_optional_float(2.5, default=99.0) == 2.5
        assert _coerce_optional_float("7.25", default=99.0) == 7.25

    def test_invalid_numeric_string_returns_default(self) -> None:
        assert _coerce_optional_float("not-a-number", default=42.0) == 42.0

    def test_lo_clamp_applies(self) -> None:
        """``lo`` argument clamps low values – covers line 152."""
        assert _coerce_optional_float(0.5, default=99.0, lo=1.0) == 1.0

    def test_hi_clamp_applies(self) -> None:
        """``hi`` argument clamps high values – covers line 154."""
        assert _coerce_optional_float(10.0, default=99.0, hi=5.0) == 5.0


# --------------------------------------------------------------------------- #
# save_config – default path value preserved
# --------------------------------------------------------------------------- #


class TestSaveConfigDefaultPath:
    def test_default_path_is_config_toml(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Mutant ``path: str = "config.toml"`` → ``"XXconfig.tomlXX"``
        / ``"CONFIG.TOML"``: call ``save_config`` without ``path``
        kwarg and assert the file lands at the expected relative
        path.  Run from ``tmp_path`` so the write doesn't pollute the
        working tree.
        """
        monkeypatch.chdir(tmp_path)
        save_config(AppConfig(psn_system_name="DefaultPath"))
        assert (tmp_path / "config.toml").is_file()
        # Round-trip: load it back via the same default.
        loaded = load_config()
        assert loaded.psn_system_name == "DefaultPath"


# --------------------------------------------------------------------------- #
# apply_runtime_config_changes – field propagation
# --------------------------------------------------------------------------- #


class _DummyRuntimeServices:
    def __init__(self) -> None:
        self.system_name_changes: list[str] = []
        self.video_swaps: list[AppConfig] = []

    def apply_psn_system_name_change(self, name: str) -> None:
        self.system_name_changes.append(name)

    def swap_video(self, new_cfg: AppConfig) -> None:
        self.video_swaps.append(new_cfg)


class _DummyWebCommands:
    def __init__(self) -> None:
        self.restart_requests = 0

    def request_restart(self) -> None:
        self.restart_requests += 1


class _DummyApp:
    """Minimal app stand-in that carries the fields
    ``apply_runtime_config_changes`` reaches into on the live-reload
    path.  The restart path is gated on ``_web_commands.request_restart``
    firing rather than any downstream side-effect, so we only need
    fakes for the live fields.
    """

    def __init__(self, cfg: AppConfig) -> None:
        self._config = cfg
        self._web_commands = _DummyWebCommands()
        self._runtime_services = _DummyRuntimeServices()
        self._web_server = None  # skips the web-server system-name update
        self._controlled_ids = list(cfg.controlled_marker_ids)
        self._viewer_ids = list(cfg.viewer_marker_ids)
        self._camera = None
        self._input_manager = None
        # ``controlled_marker_ids`` live-update is gated on both of
        # these being non-None.  We leave them None so the assertion
        # path we're testing is the plain ``app._config.*`` assignment
        # at the tail of the branch.
        self._server = None
        self._psn_receiver = None
        # Live recovery-timer push targets the active receiver when present;
        # None exercises the no-receiver arm (config still updates).
        self._video_receiver = None
        # Canvas stand-in records pointer-visibility updates driven by the
        # controller block's mouse_enabled handling.
        self._canvas = _DummyCanvas()


class _DummyCanvas:
    """Records ``set_pointer_base_visible`` calls from the live-reload path."""

    def __init__(self) -> None:
        self.pointer_calls: list[bool] = []

    def set_pointer_base_visible(self, visible: bool) -> None:
        self.pointer_calls.append(visible)


class _DummyReceiver:
    """Captures ``set_recovery_timers`` calls for the live-apply assertion."""

    def __init__(self) -> None:
        self.calls: list[dict[str, float]] = []

    def set_recovery_timers(self, *, stall_timeout: float, heal_interval: float) -> None:
        self.calls.append({"stall_timeout": stall_timeout, "heal_interval": heal_interval})


class TestApplyRuntimeConfigChanges:
    def test_video_source_type_change_routes_through_swap_video(self) -> None:
        """Video source changes route through swap_video (live-rebuild)
        not restart. Post-condition asserts the assignment lands."""
        app = _DummyApp(AppConfig(video_source_type="rtsp"))
        new_cfg = AppConfig(video_source_type="ndi")

        apply_runtime_config_changes(app, new_cfg)

        assert app._config.video_source_type == "ndi"
        assert app._runtime_services.video_swaps == [new_cfg]
        assert app._web_commands.restart_requests == 0

    def test_controlled_marker_ids_config_propagates(self) -> None:
        app = _DummyApp(AppConfig(controlled_marker_ids=[0, 1]))
        new_cfg = AppConfig(controlled_marker_ids=[2, 3, 4])

        apply_runtime_config_changes(app, new_cfg)

        assert app._config.controlled_marker_ids == [2, 3, 4]
        assert app._web_commands.restart_requests == 0

    def test_identical_configs_produce_no_change(self) -> None:
        app = _DummyApp(AppConfig(video_source_type="ndi"))
        # Construct an identical AppConfig so every "if new != current"
        # check evaluates False.
        identical = AppConfig(video_source_type="ndi")
        apply_runtime_config_changes(app, identical)
        # No restart triggered, no field flipped.
        assert app._web_commands.restart_requests == 0

    def test_system_name_change_routes_through_orchestrator(self) -> None:
        app = _DummyApp(AppConfig(psn_system_name="Old"))
        new_cfg = AppConfig(psn_system_name="New")

        apply_runtime_config_changes(app, new_cfg)

        assert app._config.psn_system_name == "New"
        assert app._runtime_services.system_name_changes == ["New"]

    def test_recovery_timers_change_updates_config_without_receiver(self) -> None:
        """``stall_timeout`` / ``heal_interval`` are live-reloadable. With no
        active receiver (``_video_receiver`` is None) the values still land in
        ``app._config`` so the next pass's diff doesn't re-fire."""
        app = _DummyApp(AppConfig(stall_timeout=3.0, heal_interval=5.0))
        new_cfg = AppConfig(stall_timeout=1.5, heal_interval=10.0)

        apply_runtime_config_changes(app, new_cfg)

        assert app._config.stall_timeout == 1.5
        assert app._config.heal_interval == 10.0
        assert app._web_commands.restart_requests == 0

    def test_recovery_timers_change_pushed_to_live_receiver(self) -> None:
        """When a receiver is attached, the new timers are pushed to it via
        ``set_recovery_timers`` (no pipeline rebuild)."""
        app = _DummyApp(AppConfig(stall_timeout=3.0, heal_interval=5.0))
        receiver = _DummyReceiver()
        app._video_receiver = receiver
        new_cfg = AppConfig(stall_timeout=2.0, heal_interval=7.0)

        apply_runtime_config_changes(app, new_cfg)

        assert receiver.calls == [{"stall_timeout": 2.0, "heal_interval": 7.0}]
        assert app._config.stall_timeout == 2.0
        assert app._config.heal_interval == 7.0

    def test_mouse_disabled_toggle_hides_pointer_live(self) -> None:
        app = _DummyApp(AppConfig(controller=ControllerConfig(mouse_enabled=True)))
        new_cfg = AppConfig(controller=ControllerConfig(mouse_enabled=False))

        apply_runtime_config_changes(app, new_cfg)

        assert app._config.controller.mouse_enabled is False
        assert app._canvas.pointer_calls == [False]

    def test_mouse_enabled_toggle_shows_pointer_live(self) -> None:
        """Turning mouse input back on restores the pointer live."""
        app = _DummyApp(AppConfig(controller=ControllerConfig(mouse_enabled=False)))
        new_cfg = AppConfig(controller=ControllerConfig(mouse_enabled=True))

        apply_runtime_config_changes(app, new_cfg)

        assert app._config.controller.mouse_enabled is True
        assert app._canvas.pointer_calls == [True]
