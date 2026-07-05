# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for :class:`openfollow.app.OpenFollowApp`.

The app is a thin shell that composes subsystems via
``AppRuntimeServices`` and delegates events to the ``runtime/`` package.
Tests here cover the parts that aren't already reached through the
runtime-helper tests:

* ``__init__`` dependency wiring (config load, mtime capture, default
  attribute surface).
* ``run()`` – happy path + resilient init-loop error handling + source-IP
  warning + iface-selection redirect.
* ``_on_close`` – shutdown delegate.
* A sampling of the ~25 event delegators (they're all trivial
  one-liners; we assert each forwards to its runtime helper).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import openfollow.app as app_module
from openfollow.app import OpenFollowApp
from openfollow.configuration import AppConfig

pytestmark = pytest.mark.unit

# --------------------------------------------------------------------------- #
# Shared app fixture: stub out services + load_config for a real construction.
# --------------------------------------------------------------------------- #


class _FakeRuntimeServices:
    def __init__(self, app: Any) -> None:
        self.app = app
        self.calls: list[str] = []
        self.init_errors: dict[str, Exception] = {}
        self._status_flags: dict[str, Any] = {}

    def init_canvas(self) -> None:
        self.calls.append("init_canvas")

    def init_camera(self) -> None:
        self.calls.append("init_camera")

    def init_video(self) -> None:
        self.calls.append("init_video")

    def init_web_server(self) -> None:
        self.calls.append("init_web_server")
        if "web_server" in self.init_errors:
            raise self.init_errors["web_server"]

    def init_psn(self) -> None:
        self.calls.append("init_psn")
        if "psn" in self.init_errors:
            raise self.init_errors["psn"]
        # Real ``init_psn`` constructs the foundational PSN server; model it so
        # the server-dependent init group isn't skipped by the None gate.
        self.app._server = object()

    def init_markers(self) -> None:
        self.calls.append("init_markers")

    def init_otp(self) -> None:
        self.calls.append("init_otp")
        if "otp" in self.init_errors:
            raise self.init_errors["otp"]

    def init_rttrpm(self) -> None:
        self.calls.append("init_rttrpm")

    def init_osc_transmitters(self) -> None:
        self.calls.append("init_osc_transmitters")

    def init_psn_receiver(self) -> None:
        self.calls.append("init_psn_receiver")

    def init_zone_engine(self) -> None:
        self.calls.append("init_zone_engine")

    def init_input_manager(self) -> None:
        self.calls.append("init_input_manager")

    def init_midi(self) -> None:
        self.calls.append("init_midi")

    def init_virtual_faders(self) -> None:
        self.calls.append("init_virtual_faders")

    def init_online_sync(self) -> None:
        self.calls.append("init_online_sync")
        if "online_sync" in self.init_errors:
            raise self.init_errors["online_sync"]

    def shutdown(self) -> None:
        self.calls.append("shutdown")


@pytest.fixture
def patched_ctor(monkeypatch: pytest.MonkeyPatch, tmp_path):  # noqa: ANN001
    """Patch ``AppRuntimeServices`` + ``load_config`` + mtime + native loop.

    Returns the patched module so tests can reach into it after construction
    (e.g. to verify the init-loop call order).
    """
    # Neutralise load_config to return a deterministic config.
    cfg = AppConfig(psn_system_name="LifecycleTest")
    monkeypatch.setattr(app_module, "load_config", lambda *a, **kw: cfg)
    # Don't instantiate the real services class (which fails without GStreamer).
    monkeypatch.setattr(app_module, "AppRuntimeServices", _FakeRuntimeServices)
    # Suppress the GTK main loop.
    monkeypatch.setattr(app_module, "runtime_run_native_loop", lambda app: None)
    # run() waits (bounded) for a real IP before init; keep tests deterministic
    # and free of real network I/O by returning an address immediately.
    from openfollow import net_utils

    monkeypatch.setattr(net_utils, "wait_for_source_ip", lambda **kw: "10.0.0.1")
    # Config path needs to exist so os.path.getmtime doesn't fall back to 0.0
    # and surprise us – but the actual mtime is what it is.
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("# test\n")
    return SimpleNamespace(cfg_path=str(cfg_path))


# --------------------------------------------------------------------------- #
# __init__
# --------------------------------------------------------------------------- #


class TestConstruction:
    def test_default_attributes(self, patched_ctor) -> None:  # noqa: ANN001
        app = OpenFollowApp(config_path=patched_ctor.cfg_path)
        # Runtime attribute surface (alphabetical, so breakage is easy to spot).
        assert app._button_detection is None
        assert app._camera is None
        assert app._canvas is None
        assert app._controlled_ids == []
        assert app._iface_selection_active is False
        assert app._input_manager is None
        assert app._otp_server is None
        assert app._psn_receiver is None
        assert app._rttrpm_server is None
        assert app._selected_id is None
        assert app._server is None
        assert app._settings_menu_active is False
        assert app._settings_menu_index == 0
        assert app._show_hud_help is True
        assert app._speed_key_streak == {}
        assert app._update_worker is None
        assert app._viewer_ids == []
        assert app._video_logged is False
        assert app._video_receiver is None
        assert app._web_server is None

    def test_config_path_is_absolute(self, patched_ctor) -> None:  # noqa: ANN001
        app = OpenFollowApp(config_path=patched_ctor.cfg_path)
        import os

        assert os.path.isabs(app._config_path)

    def test_web_commands_and_runtime_services_wired(
        self,
        patched_ctor,  # noqa: ANN001
    ) -> None:
        app = OpenFollowApp(config_path=patched_ctor.cfg_path)
        from openfollow.services import WebCommandQueue

        assert isinstance(app._web_commands, WebCommandQueue)
        # Our fake services was instantiated with the app itself.
        assert isinstance(app._runtime_services, _FakeRuntimeServices)
        assert app._runtime_services.app is app

    def test_config_mtime_captured_from_existing_file(
        self,
        patched_ctor,  # noqa: ANN001
    ) -> None:
        app = OpenFollowApp(config_path=patched_ctor.cfg_path)
        assert app._config_mtime > 0.0


# --------------------------------------------------------------------------- #
# run()
# --------------------------------------------------------------------------- #


class TestRun:
    def test_happy_path_calls_every_init_in_order(
        self,
        patched_ctor,  # noqa: ANN001
    ) -> None:
        app = OpenFollowApp(config_path=patched_ctor.cfg_path)
        app.run()
        # Unguarded trio first, then the try/except isolated init loop.
        assert app._runtime_services.calls == [
            "init_canvas",
            "init_camera",
            "init_video",
            "init_web_server",
            "init_psn",
            "init_markers",
            "init_otp",
            "init_rttrpm",
            "init_osc_transmitters",
            "init_psn_receiver",
            "init_zone_engine",
            "init_input_manager",
            "init_midi",
            "init_virtual_faders",
            "init_online_sync",
        ]

    def test_init_failure_in_isolated_loop_does_not_abort_startup(
        self,
        patched_ctor,
        caplog,  # noqa: ANN001
    ) -> None:
        app = OpenFollowApp(config_path=patched_ctor.cfg_path)
        app._runtime_services.init_errors["otp"] = RuntimeError("OTP failed")
        with caplog.at_level("ERROR"):
            app.run()
        # OTP raised, but later inits still ran.
        assert "init_otp" in app._runtime_services.calls
        assert "init_rttrpm" in app._runtime_services.calls
        assert any("OTP output" in r.message for r in caplog.records)

    def test_psn_failure_skips_dependent_inits_and_badges(
        self,
        patched_ctor,
        caplog,  # noqa: ANN001
    ) -> None:
        app = OpenFollowApp(config_path=patched_ctor.cfg_path)
        app._runtime_services.init_errors["psn"] = RuntimeError("PSN failed")
        with caplog.at_level("ERROR"):
            app.run()
        calls = app._runtime_services.calls
        # Foundational PSN failed → the server-dependent group is skipped.
        assert "init_psn" in calls
        for skipped in ("init_markers", "init_otp", "init_rttrpm", "init_osc_transmitters"):
            assert skipped not in calls
        # Independent inits still run.
        assert "init_psn_receiver" in calls
        assert "init_zone_engine" in calls
        assert app._runtime_services._status_flags.get("psn_init_failed")

    def test_check_pi_network_worker_drains_without_pending(self, patched_ctor) -> None:  # noqa: ANN001
        app = OpenFollowApp(config_path=patched_ctor.cfg_path)
        # No worker result stashed → the main-tick drain is a no-op.
        app._check_pi_network_worker()
        assert app._pi_network_pending_result is None

    def test_loopback_resolution_logs_degraded_warning_not_ready(
        self,
        patched_ctor,
        monkeypatch: pytest.MonkeyPatch,
        caplog,  # noqa: ANN001
    ) -> None:
        """When the bounded IP wait times out (returns loopback), startup
        proceeds but logs a WARNING about degraded/no-recovery mode rather
        than the misleading "Network ready" INFO."""
        from openfollow import net_utils

        monkeypatch.setattr(net_utils, "wait_for_source_ip", lambda **kw: "127.0.0.1")
        app = OpenFollowApp(config_path=patched_ctor.cfg_path)
        with caplog.at_level("INFO"):
            app.run()
        assert any(r.levelname == "WARNING" and "loopback" in r.message for r in caplog.records)
        assert not any("Network ready" in r.message for r in caplog.records)

    def test_uses_short_bounded_ip_wait(
        self,
        patched_ctor,
        monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
    ) -> None:
        """run() passes a short timeout so it doesn't stack a second full
        network wait on top of systemd's NetworkManager-wait-online – the
        app-side wait only covers the brief post-start race."""
        from openfollow import net_utils

        captured: dict[str, object] = {}

        def _spy(**kwargs: object) -> str:
            captured.update(kwargs)
            return "10.0.0.1"

        monkeypatch.setattr(net_utils, "wait_for_source_ip", _spy)
        app = OpenFollowApp(config_path=patched_ctor.cfg_path)
        app.run()
        assert captured["timeout_s"] == 10.0

    def test_stale_pin_with_multi_nic_opens_settings_with_banner(
        self,
        patched_ctor,
        monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
    ) -> None:
        """Auto-open Settings when pinned iface missing on multi-homed host."""
        app = OpenFollowApp(config_path=patched_ctor.cfg_path)
        from dataclasses import replace

        app._config = replace(app._config, psn_source_iface="ghost0")

        from openfollow import net_utils

        monkeypatch.setattr(
            net_utils,
            "get_local_ipv4_addresses",
            lambda: {"127.0.0.1", "10.0.0.1"},
        )
        # ≥2 non-loopback candidates → ambiguous → prompt.
        monkeypatch.setattr(
            net_utils,
            "list_iface_ipv4",
            lambda: [("eth0", "10.0.0.1"), ("wlan0", "10.0.0.2")],
        )
        monkeypatch.setattr(
            net_utils,
            "get_primary_local_ipv4",
            lambda default="": "10.0.0.1",
        )

        banners: list[str] = []
        monkeypatch.setattr(
            app_module,
            "runtime_enter_settings_menu",
            lambda a, *, banner="": banners.append(banner),
        )
        app.run()
        assert len(banners) == 1
        assert "ghost0" in banners[0]

    def test_stale_pin_with_single_nic_proceeds_silently(
        self,
        patched_ctor,
        monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
    ) -> None:
        app = OpenFollowApp(config_path=patched_ctor.cfg_path)
        from dataclasses import replace

        app._config = replace(app._config, psn_source_iface="ghost0")

        from openfollow import net_utils

        monkeypatch.setattr(
            net_utils,
            "get_local_ipv4_addresses",
            lambda: {"127.0.0.1", "10.0.0.1"},
        )
        monkeypatch.setattr(
            net_utils,
            "list_iface_ipv4",
            lambda: [("eth0", "10.0.0.1")],
        )
        monkeypatch.setattr(
            net_utils,
            "get_primary_local_ipv4",
            lambda default="": "10.0.0.1",
        )

        banners: list[str] = []
        monkeypatch.setattr(
            app_module,
            "runtime_enter_settings_menu",
            lambda a, *, banner="": banners.append(banner),
        )
        app.run()
        assert banners == []
        # Degraded surface populated so the web UI / overlay can render
        # the advisory.
        assert app._psn_source_status == "primary"
        assert app._psn_source_resolved_ip == "10.0.0.1"
        assert "ghost0" in app._psn_source_banner

    def test_empty_iface_skips_check(
        self,
        patched_ctor,
        monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
    ) -> None:
        app = OpenFollowApp(config_path=patched_ctor.cfg_path)
        # Default config leaves psn_source_iface="".
        banners: list[str] = []
        monkeypatch.setattr(
            app_module,
            "runtime_enter_settings_menu",
            lambda a, *, banner="": banners.append(banner),
        )
        app.run()
        assert banners == []

    def test_offline_host_with_pin_logs_no_usable_ip_banner(
        self,
        patched_ctor,
        monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
    ) -> None:
        """Offline host shows "no usable interface" instead of Settings menu."""
        app = OpenFollowApp(config_path=patched_ctor.cfg_path)
        from dataclasses import replace

        app._config = replace(app._config, psn_source_iface="eth0")

        from openfollow import net_utils

        monkeypatch.setattr(net_utils, "wait_for_source_ip", lambda **kw: "127.0.0.1")
        monkeypatch.setattr(net_utils, "list_iface_ipv4", lambda: [])
        monkeypatch.setattr(net_utils, "get_local_ipv4_addresses", lambda: {"127.0.0.1"})
        monkeypatch.setattr(net_utils, "get_primary_local_ipv4", lambda default="": "")
        monkeypatch.setattr(
            net_utils.psutil,
            "net_if_addrs",
            lambda: {},
        )

        banners: list[str] = []
        monkeypatch.setattr(
            app_module,
            "runtime_enter_settings_menu",
            lambda a, *, banner="": banners.append(banner),
        )
        app.run()
        assert banners == []  # zero candidates → no Settings prompt
        assert app._psn_source_status == "none"
        assert "No usable interface" in app._psn_source_banner

    def test_iface_pin_resolves_and_skips_settings(
        self,
        patched_ctor,
        monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
    ) -> None:
        """Live pinned iface resolves cleanly without prompts or degraded banner."""
        app = OpenFollowApp(config_path=patched_ctor.cfg_path)
        from dataclasses import replace

        app._config = replace(app._config, psn_source_iface="eth0")

        import socket as _socket
        from types import SimpleNamespace

        from openfollow import net_utils

        monkeypatch.setattr(
            net_utils.psutil,
            "net_if_addrs",
            lambda: {
                "eth0": [SimpleNamespace(family=_socket.AF_INET, address="192.168.178.59")],
            },
        )
        monkeypatch.setattr(
            net_utils,
            "get_local_ipv4_addresses",
            lambda: {"192.168.178.59"},
        )

        banners: list[str] = []
        monkeypatch.setattr(
            app_module,
            "runtime_enter_settings_menu",
            lambda a, *, banner="": banners.append(banner),
        )
        app.run()
        assert banners == []
        assert app._psn_source_status == "iface"
        assert app._psn_source_resolved_ip == "192.168.178.59"


# --------------------------------------------------------------------------- #
# _refresh_psn_source_advisory – keeps the PSN web advisory in sync with
# the active iface pin after live rebinds.
# --------------------------------------------------------------------------- #


class TestRefreshPsnSourceAdvisory:
    @staticmethod
    def _mock_ifaces(monkeypatch, mapping: dict[str, str]) -> None:
        import socket as _socket
        from types import SimpleNamespace

        from openfollow import net_utils

        monkeypatch.setattr(
            net_utils.psutil,
            "net_if_addrs",
            lambda: {name: [SimpleNamespace(family=_socket.AF_INET, address=ip)] for name, ip in mapping.items()},
        )

    def test_clears_stale_banner_when_pin_becomes_live(
        self,
        patched_ctor,
        monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
    ) -> None:
        """Fixing a missed pin live clears the startup advisory on next refresh."""
        app = OpenFollowApp(config_path=patched_ctor.cfg_path)
        # Simulate the startup state after a missed pin.
        app._psn_source_status = "primary"
        app._psn_source_resolved_ip = "10.0.0.1"
        app._psn_source_banner = "Pinned network interface 'ghost0' is not available."
        # Operator picked a live iface; config now reflects it.
        from dataclasses import replace

        app._config = replace(app._config, psn_source_iface="eth0")
        self._mock_ifaces(monkeypatch, {"eth0": "192.168.1.5"})

        banner = app._refresh_psn_source_advisory()

        assert banner == ""
        assert app._psn_source_status == "iface"
        assert app._psn_source_resolved_ip == "192.168.1.5"
        assert app._psn_source_banner == ""

    def test_arms_banner_when_pin_goes_stale(
        self,
        patched_ctor,
        monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
    ) -> None:
        app = OpenFollowApp(config_path=patched_ctor.cfg_path)
        app._psn_source_status = "iface"
        app._psn_source_banner = ""
        from dataclasses import replace

        app._config = replace(app._config, psn_source_iface="ghost0")
        # ghost0 absent; a different live NIC provides the fallback primary.
        self._mock_ifaces(monkeypatch, {"eth0": "10.0.0.7"})
        from openfollow import net_utils

        monkeypatch.setattr(
            net_utils,
            "get_primary_local_ipv4",
            lambda default="": "10.0.0.7",
        )

        banner = app._refresh_psn_source_advisory()

        assert "ghost0" in banner
        assert app._psn_source_status == "primary"
        assert app._psn_source_resolved_ip == "10.0.0.7"
        assert "ghost0" in app._psn_source_banner

    def test_clears_everything_when_pin_removed(
        self,
        patched_ctor,  # noqa: ANN001
    ) -> None:
        """Switching to auto-detect (empty iface) wipes the advisory –
        there is no pin left to warn about."""
        app = OpenFollowApp(config_path=patched_ctor.cfg_path)
        app._psn_source_status = "primary"
        app._psn_source_resolved_ip = "10.0.0.1"
        app._psn_source_banner = "Pinned network interface 'ghost0' is not available."
        # Default config leaves psn_source_iface="".

        banner = app._refresh_psn_source_advisory()

        assert banner == ""
        assert app._psn_source_status == ""
        assert app._psn_source_resolved_ip == ""
        assert app._psn_source_banner == ""


# --------------------------------------------------------------------------- #
# _on_close delegates to shutdown
# --------------------------------------------------------------------------- #


class TestOnClose:
    def test_on_close_invokes_services_shutdown(
        self,
        patched_ctor,  # noqa: ANN001
    ) -> None:
        app = OpenFollowApp(config_path=patched_ctor.cfg_path)
        app._on_close({})
        assert "shutdown" in app._runtime_services.calls


# --------------------------------------------------------------------------- #
# Event + mode delegators
#
# Each method in OpenFollowApp forwards to a ``runtime_*`` helper imported
# at module load time.  We patch the helper, call the method, and assert
# the forwarded payload – that's the entirety of the delegator's contract.
# --------------------------------------------------------------------------- #


class TestDelegators:
    @pytest.mark.parametrize(
        ("method_name", "helper_name", "call_args"),
        [
            ("_animate", "runtime_animate", ()),
            ("_check_restart_request", "runtime_check_restart_request", ()),
            ("_run_housekeeping", "runtime_housekeeping", ()),
            ("_check_update_request", "runtime_check_update_request", ()),
            ("_check_button_detection_request", "runtime_check_button_detection_request", ()),
            ("_process_source_selection_input", "runtime_process_source_selection_input", ()),
            ("_process_iface_selection_input", "runtime_process_iface_selection_input", ()),
            ("_enter_source_selection", "runtime_enter_source_selection", ()),
            ("_refresh_iface_list", "runtime_refresh_iface_list", ()),
            ("_enter_iface_selection", "runtime_enter_iface_selection", ()),
            ("_confirm_iface_selection", "runtime_confirm_iface_selection", ()),
            ("_enter_button_detection", "runtime_enter_button_detection", ()),
            ("_process_button_detection", "runtime_process_button_detection", ()),
            ("_exit_button_detection", "runtime_exit_button_detection", ()),
            ("_enter_settings_menu", "runtime_enter_settings_menu", ()),
            ("_exit_settings_menu", "runtime_exit_settings_menu", ()),
            ("_process_settings_menu_input", "runtime_process_settings_menu_input", ()),
            ("_enter_source_type_selection", "runtime_enter_source_type_selection", ()),
            ("_exit_source_type_selection", "runtime_exit_source_type_selection", ()),
            ("_process_source_type_selection_input", "runtime_process_source_type_selection_input", ()),
            ("_confirm_source_type_selection", "runtime_confirm_source_type_selection", ()),
            ("_enter_url_editor", "runtime_enter_url_editor", ()),
            ("_exit_url_editor", "runtime_exit_url_editor", ()),
            ("_cancel_url_editor", "runtime_cancel_url_editor", ()),
            ("_confirm_url_editor", "runtime_confirm_url_editor", ()),
            ("_enter_field_choice_picker", "runtime_enter_field_choice_picker", ()),
            ("_exit_field_choice_picker", "runtime_exit_field_choice_picker", ()),
            ("_cancel_field_choice_picker", "runtime_cancel_field_choice_picker", ()),
            ("_confirm_field_choice_picker", "runtime_confirm_field_choice_picker", ()),
            ("_process_field_choice_picker_input", "runtime_process_field_choice_picker_input", ()),
            ("_enter_browser", "runtime_enter_browser", ()),
            ("_exit_browser", "runtime_exit_browser", ()),
            ("_process_browser_input", "runtime_process_browser_input", ()),
            ("_check_video_disconnect_banner", "runtime_check_video_disconnect_banner", ()),
            ("_restart_app", "runtime_restart_app", ()),
            ("_check_config_reload", "runtime_check_config_reload", ()),
            ("_check_marker_speeds_persist", "runtime_check_marker_speeds_persist", ()),
        ],
    )
    def test_no_arg_delegators(
        self,
        patched_ctor,
        monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
        method_name: str,
        helper_name: str,
        call_args: tuple,
    ) -> None:
        hits: list[Any] = []
        monkeypatch.setattr(app_module, helper_name, lambda *a, **kw: hits.append(a))

        app = OpenFollowApp(config_path=patched_ctor.cfg_path)
        getattr(app, method_name)(*call_args)
        assert len(hits) == 1
        # All helpers take ``app`` as first arg.
        assert hits[0][0] is app

    @pytest.mark.parametrize(
        ("method_name", "helper_name", "payload"),
        [
            ("_on_key_down", "runtime_on_key_down", {"key": "a"}),
            ("_on_key_up", "runtime_on_key_up", {"key": "a"}),
            ("_on_wheel", "runtime_on_wheel", {"dy": 1.0}),
            ("_on_pointer_down", "runtime_on_pointer_down", {"x": 1, "y": 2}),
            ("_on_pointer_move", "runtime_on_pointer_move", {"x": 3, "y": 4}),
            ("_on_pointer_up", "runtime_on_pointer_up", {"x": 5, "y": 6}),
            ("_on_resize", "runtime_on_resize", {"width": 800, "height": 600}),
        ],
    )
    def test_event_delegators_pass_event_dict(
        self,
        patched_ctor,
        monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
        method_name: str,
        helper_name: str,
        payload: dict,
    ) -> None:
        received: list[dict] = []
        monkeypatch.setattr(app_module, helper_name, lambda app, ev: received.append(ev))
        app = OpenFollowApp(config_path=patched_ctor.cfg_path)
        getattr(app, method_name)(payload)
        assert received == [payload]

    def test_handle_key_press_forwards_key_string(
        self,
        patched_ctor,
        monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
    ) -> None:
        received: list[str] = []
        monkeypatch.setattr(
            app_module,
            "runtime_handle_key_press",
            lambda app, key: received.append(key),
        )
        app = OpenFollowApp(config_path=patched_ctor.cfg_path)
        app._handle_key_press("Escape")
        assert received == ["Escape"]

    def test_process_input_forwards_dt(
        self,
        patched_ctor,
        monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
    ) -> None:
        received: list[float] = []
        monkeypatch.setattr(
            app_module,
            "runtime_process_input",
            lambda app, dt: received.append(dt),
        )
        app = OpenFollowApp(config_path=patched_ctor.cfg_path)
        app._process_input(0.0167)
        assert received == [0.0167]

    def test_adjust_move_speed_forwards_direction(
        self,
        patched_ctor,
        monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
    ) -> None:
        # Thread marker_id to runtime for gamepad bumper resolution.
        move_calls: list[tuple[int, int | None]] = []
        monkeypatch.setattr(
            app_module,
            "runtime_adjust_move_speed",
            lambda app, d, *, marker_id=None: move_calls.append((d, marker_id)),
        )
        app = OpenFollowApp(config_path=patched_ctor.cfg_path)
        app.adjust_move_speed(1)
        app.adjust_move_speed(-1, marker_id=3)
        assert move_calls == [(1, None), (-1, 3)]

    def test_get_marker_move_speed_returns_per_marker_value_or_default(
        self,
        patched_ctor,  # noqa: ANN001
    ) -> None:
        """get_marker_move_speed falls back to global when marker has no override."""
        app = OpenFollowApp(config_path=patched_ctor.cfg_path)
        app._config.marker.move_speed = 2.0
        app._config.marker_move_speeds = {5: 4.5}
        assert app.get_marker_move_speed(5) == pytest.approx(4.5)
        assert app.get_marker_move_speed(99) == pytest.approx(2.0)
        assert app.get_marker_move_speed(None) == pytest.approx(2.0)

    def test_run_deb_update_forwards_request(
        self,
        patched_ctor,
        monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
    ) -> None:
        received: list[dict] = []
        monkeypatch.setattr(
            app_module,
            "runtime_run_deb_update",
            lambda app, req: received.append(req),
        )
        app = OpenFollowApp(config_path=patched_ctor.cfg_path)
        app._run_deb_update({"kind": "deb", "service_name": "openfollow"})
        assert received == [{"kind": "deb", "service_name": "openfollow"}]

    def test_run_local_update_forwards_request(
        self,
        patched_ctor,
        monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
    ) -> None:
        received: list[dict] = []
        monkeypatch.setattr(
            app_module,
            "runtime_run_local_update",
            lambda app, req: received.append(req),
        )
        app = OpenFollowApp(config_path=patched_ctor.cfg_path)
        req = {"kind": "deb-local", "service_name": "openfollow", "deb_path": "/tmp/openfollow-update-x.deb"}
        app._run_local_update(req)
        assert received == [req]

    def test_get_default_marker_position_forwards(
        self,
        patched_ctor,
        monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
    ) -> None:
        monkeypatch.setattr(
            app_module,
            "runtime_get_default_marker_position",
            lambda app: (1.0, 2.0, 3.0),
        )
        app = OpenFollowApp(config_path=patched_ctor.cfg_path)
        assert app._get_default_marker_position() == (1.0, 2.0, 3.0)

    def test_get_config_mtime_forwards(
        self,
        patched_ctor,
        monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
    ) -> None:
        monkeypatch.setattr(app_module, "runtime_get_config_mtime", lambda app: 123.45)
        app = OpenFollowApp(config_path=patched_ctor.cfg_path)
        assert app._get_config_mtime() == pytest.approx(123.45)

    def test_normalize_key_is_static_delegator(
        self,
        patched_ctor,
        monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
    ) -> None:
        monkeypatch.setattr(app_module, "runtime_normalize_key", lambda key: key.upper())
        assert OpenFollowApp._normalize_key("h") == "H"

    def test_run_native_loop_forwards_self(
        self,
        patched_ctor,
        monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
    ) -> None:
        received: list[OpenFollowApp] = []
        monkeypatch.setattr(app_module, "runtime_run_native_loop", lambda app: received.append(app))
        app = OpenFollowApp(config_path=patched_ctor.cfg_path)
        app._run_native_loop()
        assert received == [app]


def test_sync_system_hostname_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    """``_sync_system_hostname`` forwards the broker + station name to
    ``device_repair.sync_station_hostname``."""
    calls: list[tuple[Any, str]] = []
    monkeypatch.setattr(
        "openfollow.privilege.device_repair.sync_station_hostname",
        lambda broker, name: calls.append((broker, name)),
    )
    fake = SimpleNamespace(
        _runtime_services=SimpleNamespace(privilege_broker="BROKER"),
        _config=SimpleNamespace(psn_system_name="Station X"),
    )
    OpenFollowApp._sync_system_hostname(fake)
    assert calls == [("BROKER", "Station X")]


class TestBlurHandler:
    """``_on_blur`` clears held keys when the window loses focus."""

    def test_on_blur_clears_keyboard_when_input_manager_present(self, patched_ctor) -> None:  # noqa: ANN001
        app = OpenFollowApp(config_path=patched_ctor.cfg_path)
        cleared: list[bool] = []
        app._input_manager = SimpleNamespace(
            keyboard_handler=SimpleNamespace(clear=lambda: cleared.append(True)),
        )
        app._on_blur({})
        assert cleared == [True]

    def test_on_blur_noop_without_input_manager(self, patched_ctor) -> None:  # noqa: ANN001
        app = OpenFollowApp(config_path=patched_ctor.cfg_path)
        assert app._input_manager is None
        app._on_blur({})  # must not raise
