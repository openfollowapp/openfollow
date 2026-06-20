# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Extended dispatch tests for ``openfollow.runtime.app_modes``.

``test_app_modes.py`` covers speed adjustment, normalize_key, Settings menu
and legacy shortcut regression guards. This file fills in:

- ``cycle_marker`` edge cases (empty list, current==None, wrap-around)
- ``enter_iface_selection`` + ``confirm_iface_selection`` + ``refresh_iface_list``
- ``enter_source_selection`` gating on ``has_source_selection``
- ``enter_button_detection`` refusal paths + ``process_button_detection``
- ``on_key_down`` digit shortcuts and Ctrl/Meta+S save flow
- ``process_input`` guard paths (button-detection, settings menu, source,
  iface, key_settings edge gating)
- ``process_source_selection_input`` / ``process_iface_selection_input``
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from openfollow.runtime import app_modes

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# cycle_marker
# ---------------------------------------------------------------------------


def test_cycle_marker_noop_when_empty() -> None:
    app = SimpleNamespace(_controlled_ids=[], _selected_id=None)
    app_modes.cycle_marker(app, +1)
    assert app._selected_id is None


def test_cycle_marker_forward_wraps() -> None:
    app = SimpleNamespace(_controlled_ids=[1, 2, 3], _selected_id=3)
    app_modes.cycle_marker(app, +1)
    assert app._selected_id == 1


def test_cycle_marker_backward_wraps() -> None:
    app = SimpleNamespace(_controlled_ids=[1, 2, 3], _selected_id=1)
    app_modes.cycle_marker(app, -1)
    assert app._selected_id == 3


def test_cycle_marker_selects_first_when_current_missing_forward() -> None:
    """If ``_selected_id`` is not a member, forward step starts from -1 →
    index 0. Covers the ValueError fallback branch."""
    app = SimpleNamespace(_controlled_ids=[4, 5, 6], _selected_id=99)
    app_modes.cycle_marker(app, +1)
    assert app._selected_id == 4


def test_cycle_marker_selects_last_when_current_missing_backward() -> None:
    app = SimpleNamespace(_controlled_ids=[4, 5, 6], _selected_id=99)
    app_modes.cycle_marker(app, -1)
    # idx = 0 (else branch), step = -1 → index -1 → 6
    assert app._selected_id == 6


# ---------------------------------------------------------------------------
# Interface selection: enter / confirm / refresh
# ---------------------------------------------------------------------------


def test_enter_iface_selection_lists_interfaces(monkeypatch) -> None:
    """Picker lists interface names with auto-detect option first."""
    from openfollow.configuration import AppConfig

    monkeypatch.setattr(
        "openfollow.net_utils.list_iface_ipv4",
        lambda: [("en0", "10.0.0.3"), ("eth0", "192.168.1.5")],
    )
    app = SimpleNamespace(
        _config=AppConfig(),
        _available_interfaces=[],
        _selected_iface_index=0,
        _iface_selection_active=False,
        _source_type_selection_active=False,
        _url_editor_active=False,
        _field_choice_active=False,
        _browser_active=False,
    )
    app_modes.enter_iface_selection(app)
    # "" (auto) + the iface names from ``list_iface_ipv4``.
    assert app._available_interfaces[0] == ""
    assert "eth0" in app._available_interfaces
    assert "en0" in app._available_interfaces
    assert app._iface_selection_active is True
    # Unconfigured psn_source_iface → "" matches index 0.
    assert app._selected_iface_index == 0


def test_enter_iface_selection_preserves_selection(monkeypatch) -> None:
    """Seeded to the currently-pinned iface so the menu doesn't
    visually reset on every reopen."""
    from openfollow.configuration import AppConfig

    monkeypatch.setattr(
        "openfollow.net_utils.list_iface_ipv4",
        lambda: [("en0", "10.0.0.3"), ("eth0", "192.168.1.5")],
    )
    cfg = AppConfig()
    cfg.psn_source_iface = "en0"
    app = SimpleNamespace(
        _config=cfg,
        _available_interfaces=[],
        _selected_iface_index=0,
        _iface_selection_active=False,
        _source_type_selection_active=False,
        _url_editor_active=False,
        _field_choice_active=False,
        _browser_active=False,
    )
    app_modes.enter_iface_selection(app)
    assert app._available_interfaces[app._selected_iface_index] == "en0"


def _make_iface_confirm_app(
    tmp_path,
    monkeypatch,
    *,
    current_iface: str = "",
) -> SimpleNamespace:
    """Build SimpleNamespace app with confirm_iface_selection slots for testing without full OpenFollowApp."""
    import socket as _socket
    from types import SimpleNamespace as _SN

    from openfollow.configuration import AppConfig

    monkeypatch.setattr(
        "openfollow.net_utils.psutil.net_if_addrs",
        lambda: {
            "en0": [_SN(family=_socket.AF_INET, address="10.0.0.3")],
            "eth0": [_SN(family=_socket.AF_INET, address="192.168.1.5")],
        },
    )

    cfg = AppConfig()
    cfg.psn_source_iface = current_iface
    cfg_path = tmp_path / "c.toml"
    cfg_path.write_text("")  # mtime read needs the file to exist

    apply_calls: list[str] = []

    class _Services:
        def apply_psn_source_ip_change(self, new_ip: str) -> None:  # noqa: ARG002
            apply_calls.append(new_ip)

    app = SimpleNamespace(
        _config=cfg,
        _config_path=str(cfg_path),
        _config_mtime=0.0,
        _runtime_services=_Services(),
        _available_interfaces=["", "en0", "eth0"],
        _selected_iface_index=1,  # en0
        _iface_selection_active=True,
        _restart_called=False,
        _apply_calls=apply_calls,
    )
    app._restart_app = lambda: setattr(app, "_restart_called", True)
    app._get_config_mtime = lambda: 1234.5
    # Bind production helper to exercise the refresh path.
    from types import MethodType

    from openfollow.app import OpenFollowApp

    app._psn_source_resolved_ip = ""
    app._psn_source_status = ""
    app._psn_source_banner = ""
    app._refresh_psn_source_advisory = MethodType(
        OpenFollowApp._refresh_psn_source_advisory,
        app,
    )
    return app


def test_confirm_iface_selection_live_applies_without_restart(
    monkeypatch,
    tmp_path,
) -> None:
    saves: list = []
    monkeypatch.setattr(
        app_modes,
        "save_config",
        lambda cfg, path: saves.append((cfg.psn_source_iface, path)),
    )
    app = _make_iface_confirm_app(tmp_path, monkeypatch, current_iface="")
    # Seed stale advisory; live apply must clear it.
    app._psn_source_banner = "Pinned network interface 'ghost0' is not available."
    app._psn_source_status = "primary"
    app_modes.confirm_iface_selection(app)
    assert app._config.psn_source_iface == "en0"
    # Resolver translates en0 → 10.0.0.3, which is what the orchestrator sees.
    assert app._apply_calls == ["10.0.0.3"]
    assert saves == [("en0", app._config_path)]
    assert app._iface_selection_active is False
    assert app._restart_called is False
    assert app._config_mtime == 1234.5
    # Advisory re-synced to the now-honoured pin.
    assert app._psn_source_status == "iface"
    assert app._psn_source_banner == ""


def test_confirm_iface_selection_no_op_when_pick_matches_current(
    monkeypatch,
    tmp_path,
) -> None:
    """Selecting the iface already in use closes the picker without
    rebinding anything – pointless socket churn would interrupt PSN
    streaming for no operator-visible benefit."""
    monkeypatch.setattr(
        app_modes,
        "save_config",
        lambda *a, **kw: pytest.fail("must not save on no-op pick"),
    )
    app = _make_iface_confirm_app(tmp_path, monkeypatch, current_iface="en0")
    app_modes.confirm_iface_selection(app)
    assert app._apply_calls == []
    assert app._iface_selection_active is False


def test_confirm_iface_selection_rolls_back_on_apply_failure(
    monkeypatch,
    tmp_path,
) -> None:
    """If the live-apply rebind fails (e.g. the new iface goes down by
    the time we try to bind to it), the stored config reverts and the
    iface picker stays open so the operator can pick a working one."""
    monkeypatch.setattr(
        app_modes,
        "save_config",
        lambda *a, **kw: pytest.fail("must not save on apply failure"),
    )
    app = _make_iface_confirm_app(tmp_path, monkeypatch, current_iface="eth0")

    def _boom(_new_ip: str) -> None:
        raise OSError("bind failed")

    app._runtime_services.apply_psn_source_ip_change = _boom
    # Seed a stale advisory; after rollback the refresh must recompute it
    # against the restored (prior) iface rather than leaving it stale.
    app._psn_source_banner = "leftover"
    app._psn_source_status = "leftover"
    app_modes.confirm_iface_selection(app)
    # Stored config reverted to the prior iface.
    assert app._config.psn_source_iface == "eth0"
    # Picker stays open so operator can re-pick.
    assert app._iface_selection_active is True
    assert app._restart_called is False
    # Advisory recomputed for the restored iface (eth0 is live → 192.168.1.5).
    assert app._psn_source_status == "iface"
    assert app._psn_source_banner == ""


def test_confirm_iface_selection_bails_when_list_empty() -> None:
    app = SimpleNamespace(
        _available_interfaces=[],
        _selected_iface_index=0,
        _iface_selection_active=True,
    )
    app._restart_app = lambda: pytest.fail("Must not restart with no ifaces")
    app_modes.confirm_iface_selection(app)
    assert app._iface_selection_active is False


def test_refresh_iface_list_keeps_valid_selection(monkeypatch) -> None:
    monkeypatch.setattr(
        "openfollow.net_utils.list_iface_ipv4",
        lambda: [("en0", "10.0.0.3"), ("eth0", "192.168.1.5")],
    )
    app = SimpleNamespace(
        _available_interfaces=["", "en0"],
        _selected_iface_index=1,
    )
    app_modes.refresh_iface_list(app)
    assert app._available_interfaces[app._selected_iface_index] == "en0"


def test_refresh_iface_list_clamps_selection_when_lost(monkeypatch) -> None:
    monkeypatch.setattr(
        "openfollow.net_utils.list_iface_ipv4",
        lambda: [("eth9", "10.0.0.9")],
    )
    app = SimpleNamespace(
        _available_interfaces=["", "10.0.0.3"],
        _selected_iface_index=1,
    )
    app_modes.refresh_iface_list(app)
    assert app._available_interfaces == ["", "eth9"]
    assert app._selected_iface_index <= len(app._available_interfaces) - 1


# ---------------------------------------------------------------------------
# Source selection
# ---------------------------------------------------------------------------


def test_enter_source_selection_when_receiver_supports_it() -> None:
    called: list[bool] = []

    class _Receiver:
        has_source_selection = True

        def enter_source_selection(self) -> None:
            called.append(True)

    app = SimpleNamespace(_video_receiver=_Receiver())
    app_modes.enter_source_selection(app)
    assert called == [True]


def test_enter_source_selection_noop_without_capability() -> None:
    class _Receiver:
        has_source_selection = False

        def enter_source_selection(self) -> None:
            pytest.fail("Must not enter when capability is absent")

    app = SimpleNamespace(_video_receiver=_Receiver())
    app_modes.enter_source_selection(app)  # Must not raise or call.


def test_enter_source_selection_noop_when_receiver_is_none() -> None:
    app = SimpleNamespace(_video_receiver=None)
    app_modes.enter_source_selection(app)  # Must not raise.


# ---------------------------------------------------------------------------
# Button detection wizard
# ---------------------------------------------------------------------------


def test_enter_button_detection_warns_without_controller(caplog) -> None:
    import logging

    class _GamepadHandler:
        joysticks = {}

    class _InputManager:
        gamepad_handler = _GamepadHandler()

    app = SimpleNamespace(
        _input_manager=_InputManager(),
        _button_detection=None,
        _web_server=None,
    )
    with caplog.at_level(logging.WARNING, logger=app_modes.logger.name):
        app_modes.enter_button_detection(app)
    assert app._button_detection is None
    assert any("No controller" in r.getMessage() for r in caplog.records)


def test_enter_button_detection_noop_when_already_running() -> None:
    """Second entry is a no-op – the wizard owns input exclusively."""
    sentinel = object()
    app = SimpleNamespace(_button_detection=sentinel, _input_manager=None, _web_server=None)
    app_modes.enter_button_detection(app)
    assert app._button_detection is sentinel


def test_exit_button_detection_cancels_and_clears() -> None:
    class _Wizard:
        def __init__(self) -> None:
            self.cancelled = False

        def cancel(self) -> None:
            self.cancelled = True

    class _WebServer:
        def __init__(self) -> None:
            self.active = True

        def set_button_detection_active(self, flag: bool) -> None:
            self.active = flag

    wizard = _Wizard()
    web = _WebServer()
    app = SimpleNamespace(_button_detection=wizard, _web_server=web)
    app_modes.exit_button_detection(app)
    assert wizard.cancelled is True
    assert app._button_detection is None
    assert web.active is False


def test_exit_button_detection_when_nothing_active() -> None:
    app = SimpleNamespace(_button_detection=None, _web_server=None)
    app_modes.exit_button_detection(app)  # Must not raise.


def test_process_button_detection_escape_cancels() -> None:
    class _Wizard:
        def __init__(self) -> None:
            self.cancelled = False
            self.is_done = False
            self.results = {}

        def cancel(self) -> None:
            self.cancelled = True

        def poll(self) -> None:
            pytest.fail("Must not poll after escape")

    class _KeyboardHandler:
        # ``KeyboardHandler.POLLED_KEYS`` stores the named key as
        # ``"Escape"``; the lowercase form would never appear at runtime.
        keys: set[str] = {"Escape"}

    class _InputManager:
        keyboard_handler = _KeyboardHandler()

    wizard = _Wizard()
    back_calls: list[bool] = []
    app = SimpleNamespace(
        _button_detection=wizard,
        _input_manager=_InputManager(),
        _web_server=None,
    )
    app._enter_settings_menu = lambda *, banner="": back_calls.append(True)
    app_modes.process_button_detection(app)
    assert wizard.cancelled is True
    assert app._button_detection is None
    # Esc inside wizard re-opens Settings menu.
    assert back_calls == [True]


def test_process_button_detection_noop_when_wizard_none() -> None:
    app = SimpleNamespace(_button_detection=None)
    app_modes.process_button_detection(app)  # Must not raise.


def test_process_button_detection_done_with_no_results_clears_wizard() -> None:
    class _Wizard:
        is_done = True
        results: dict = {}

        def poll(self) -> None:
            pass

    class _KeyboardHandler:
        keys: set[str] = set()

    class _InputManager:
        keyboard_handler = _KeyboardHandler()

    app = SimpleNamespace(
        _button_detection=_Wizard(),
        _input_manager=_InputManager(),
        _web_server=None,
    )
    app_modes.process_button_detection(app)
    assert app._button_detection is None


# ---------------------------------------------------------------------------
# on_key_down – digit shortcut + Ctrl+S save
# ---------------------------------------------------------------------------


def test_on_key_down_digit_key_selects_controlled_marker() -> None:
    class _KeyboardHandler:
        keys: set[str] = set()

        def on_key_down(self, event) -> None:  # noqa: ANN001
            pass

    class _InputManager:
        keyboard_handler = _KeyboardHandler()

    app = SimpleNamespace(
        _input_manager=_InputManager(),
        _controlled_ids=[10, 20, 30],
        _selected_id=10,
        _url_editor_active=False,
        _field_choice_active=False,
        _browser_active=False,
    )
    app._normalize_key = app_modes.normalize_key
    app_modes.on_key_down(app, {"key": "2"})
    assert app._selected_id == 20


def test_on_key_down_digit_key_out_of_range_is_noop() -> None:
    class _KeyboardHandler:
        keys: set[str] = set()

        def on_key_down(self, event) -> None:  # noqa: ANN001
            pass

    class _InputManager:
        keyboard_handler = _KeyboardHandler()

    app = SimpleNamespace(
        _input_manager=_InputManager(),
        _controlled_ids=[1],
        _selected_id=1,
        _url_editor_active=False,
        _field_choice_active=False,
        _browser_active=False,
    )
    app._normalize_key = app_modes.normalize_key
    app_modes.on_key_down(app, {"key": "9"})  # only one marker
    assert app._selected_id == 1


def test_on_key_down_ctrl_s_saves_camera(monkeypatch, tmp_path) -> None:
    from openfollow.configuration import AppConfig, CameraConfig

    writes: list = []

    def _fake_save(cfg, path) -> None:  # noqa: ANN001
        writes.append(path)

    monkeypatch.setattr(app_modes, "save_config", _fake_save)

    class _KeyboardHandler:
        keys: set[str] = {"Control"}

        def on_key_down(self, event) -> None:  # noqa: ANN001
            pass

    class _InputManager:
        keyboard_handler = _KeyboardHandler()

    class _Camera:
        def to_config(self):  # noqa: ANN201
            return CameraConfig(fov=42.0)

    app = SimpleNamespace(
        _input_manager=_InputManager(),
        _controlled_ids=[],
        _selected_id=None,
        _config=AppConfig(),
        _config_path=str(tmp_path / "c.toml"),
        _config_mtime=0,
        _camera=_Camera(),
        _url_editor_active=False,
        _field_choice_active=False,
        _browser_active=False,
    )
    app._normalize_key = app_modes.normalize_key
    app._get_config_mtime = lambda: 1
    app_modes.on_key_down(app, {"key": "s"})
    assert writes == [app._config_path]
    assert app._config.camera.fov == pytest.approx(42.0)
    assert app._config_mtime == 1


def test_on_key_down_without_input_manager() -> None:
    app = SimpleNamespace(
        _input_manager=None,
        _controlled_ids=[],
        _url_editor_active=False,
        _browser_active=False,
        _field_choice_active=False,
    )
    app._normalize_key = app_modes.normalize_key
    app_modes.on_key_down(app, {"key": "q"})  # Must not raise.


def _key_app(**flags):
    """Fake app for on_key_down whose modal flags default to False."""

    class _KeyboardHandler:
        keys: set[str] = set()

        def on_key_down(self, event) -> None:  # noqa: ANN001
            pass

    class _InputManager:
        keyboard_handler = _KeyboardHandler()

    app = SimpleNamespace(
        _input_manager=_InputManager(),
        _controlled_ids=[10, 20, 30],
        _selected_id=10,
        _url_editor_active=False,
        _field_choice_active=False,
        _browser_active=False,
        **flags,
    )
    app._normalize_key = app_modes.normalize_key
    return app


def test_on_key_down_digit_gated_while_modal_open() -> None:
    # #551: a digit must not silently reselect the marker under a modal.
    app = _key_app(_settings_menu_active=True)
    app_modes.on_key_down(app, {"key": "2"})
    assert app._selected_id == 10  # unchanged


def test_on_key_down_ctrl_s_gated_while_modal_open(monkeypatch) -> None:
    # #551: Ctrl/Cmd+S must not write config from under a modal.
    writes: list = []
    monkeypatch.setattr(app_modes, "save_config", lambda cfg, path: writes.append(path))
    app = _key_app(_about_active=True, _camera=object())
    app._input_manager.keyboard_handler.keys = {"Control"}
    app_modes.on_key_down(app, {"key": "s"})
    assert writes == []  # save suppressed while modal owns the screen


def test_on_key_down_ctrl_s_save_failure_is_swallowed(monkeypatch, tmp_path) -> None:
    # #551 Low: a save failure routes through _persist_config (logged, not raised).
    from openfollow.configuration import AppConfig, CameraConfig

    def _boom(cfg, path) -> None:  # noqa: ANN001
        raise OSError("read-only fs")

    monkeypatch.setattr(app_modes, "save_config", _boom)

    class _Camera:
        def to_config(self):  # noqa: ANN201
            return CameraConfig(fov=42.0)

    app = _key_app(
        _config=AppConfig(),
        _config_path=str(tmp_path / "c.toml"),
        _config_mtime=0,
        _camera=_Camera(),
    )
    app._input_manager.keyboard_handler.keys = {"Meta"}
    app._get_config_mtime = lambda: 1
    app_modes.on_key_down(app, {"key": "s"})  # must not raise
    assert app._config_mtime == 0  # mtime not advanced on failed persist


def test_on_key_down_digit_gated_during_button_detection() -> None:
    # #607: the button-detection wizard consumes all input – a digit must not
    # reselect the marker through the GTK key path either.
    app = _key_app(_button_detection=object())
    app_modes.on_key_down(app, {"key": "2"})
    assert app._selected_id == 10  # unchanged


# ---------------------------------------------------------------------------
# Pointer / wheel / resize delegation
# ---------------------------------------------------------------------------


def test_on_wheel_delegates_to_mouse_when_enabled() -> None:
    class _MouseHandler:
        def __init__(self) -> None:
            self.dy: float | None = None

        def on_wheel(self, dy) -> None:  # noqa: ANN001
            self.dy = dy

    class _InputManager:
        mouse_handler = _MouseHandler()

    from openfollow.configuration import AppConfig

    cfg = AppConfig()
    cfg.controller.mouse_enabled = True
    app = SimpleNamespace(
        _input_manager=_InputManager(),
        _config=cfg,
        _browser_active=False,
    )
    app_modes.on_wheel(app, {"dy": 2.5})
    assert app._input_manager.mouse_handler.dy == pytest.approx(2.5)


def test_on_wheel_noop_when_mouse_disabled() -> None:
    class _MouseHandler:
        def on_wheel(self, dy) -> None:  # noqa: ANN001
            pytest.fail("Must not forward wheel when mouse is disabled")

    class _InputManager:
        mouse_handler = _MouseHandler()

    from openfollow.configuration import AppConfig

    cfg = AppConfig()
    cfg.controller.mouse_enabled = False
    app = SimpleNamespace(
        _input_manager=_InputManager(),
        _config=cfg,
        _browser_active=False,
    )
    app_modes.on_wheel(app, {"dy": 1.0})


def test_on_wheel_skipped_while_browser_active() -> None:
    """The embedded WebKit browser handles scroll for the rendered
    page; a leak into the tracker's wheel handler would shift Z
    while the operator scrolls the web UI."""

    class _MouseHandler:
        def on_wheel(self, dy) -> None:  # noqa: ANN001
            pytest.fail("Must not forward wheel while browser overlay is active")

    class _InputManager:
        mouse_handler = _MouseHandler()

    from openfollow.configuration import AppConfig

    cfg = AppConfig()
    cfg.controller.mouse_enabled = True
    app = SimpleNamespace(
        _input_manager=_InputManager(),
        _config=cfg,
        _browser_active=True,
    )
    app_modes.on_wheel(app, {"dy": 1.0})


def test_on_pointer_move_routes_to_mouse_handler_when_mouse_enabled() -> None:
    mouse_calls: list = []

    class _MouseHandler:
        def on_pointer_move(self, x, y) -> None:  # noqa: ANN001
            mouse_calls.append((x, y))

    class _InputManager:
        mouse_handler = _MouseHandler()

    from openfollow.configuration import AppConfig

    cfg = AppConfig()
    cfg.controller.mouse_enabled = True
    app = SimpleNamespace(
        _input_manager=_InputManager(),
        _config=cfg,
        _browser_active=False,
    )
    app_modes.on_pointer_move(app, {"x": 3, "y": 4})
    assert mouse_calls == [(3, 4)]


def test_on_pointer_move_skipped_while_browser_active() -> None:

    class _MouseHandler:
        def on_pointer_move(self, x, y) -> None:  # noqa: ANN001
            pytest.fail("Must not forward pointer-move while browser is active")

    class _InputManager:
        mouse_handler = _MouseHandler()

    from openfollow.configuration import AppConfig

    cfg = AppConfig()
    cfg.controller.mouse_enabled = True
    app = SimpleNamespace(
        _input_manager=_InputManager(),
        _config=cfg,
        _browser_active=True,
    )
    app_modes.on_pointer_move(app, {"x": 3, "y": 4})


def _mouse_app(handler, **flags):
    """Fake app for pointer/wheel tests with mouse enabled; flags default False."""
    from openfollow.configuration import AppConfig

    class _InputManager:
        mouse_handler = handler

    cfg = AppConfig()
    cfg.controller.mouse_enabled = True
    return SimpleNamespace(_input_manager=_InputManager(), _config=cfg, _browser_active=False, **flags)


def test_on_wheel_skipped_while_modal_active() -> None:
    # #551: a non-browser modal must also suspend wheel-driven Z control.
    class _MouseHandler:
        def on_wheel(self, dy) -> None:  # noqa: ANN001
            pytest.fail("wheel must be gated while a modal owns the screen")

    app = _mouse_app(_MouseHandler(), _settings_menu_active=True)
    app_modes.on_wheel(app, {"dy": 1.0})


def test_on_pointer_down_skipped_while_modal_active() -> None:
    class _MouseHandler:
        def on_pointer_down(self, x, y, b) -> None:  # noqa: ANN001
            pytest.fail("pointer-down must be gated while a modal owns the screen")

    app = _mouse_app(_MouseHandler(), _iface_selection_active=True)
    app_modes.on_pointer_down(app, {"x": 1, "y": 2, "button": 1})


def test_on_pointer_move_skipped_while_modal_active() -> None:
    class _MouseHandler:
        def on_pointer_move(self, x, y) -> None:  # noqa: ANN001
            pytest.fail("pointer-move must be gated while a modal owns the screen")

    app = _mouse_app(_MouseHandler(), _source_type_selection_active=True)
    app_modes.on_pointer_move(app, {"x": 1, "y": 2})


def test_on_pointer_up_skipped_while_modal_active() -> None:
    class _MouseHandler:
        def on_pointer_up(self, x, y, b) -> None:  # noqa: ANN001
            pytest.fail("pointer-up must be gated while a modal owns the screen")

    app = _mouse_app(_MouseHandler(), _about_active=True)
    app_modes.on_pointer_up(app, {"x": 1, "y": 2, "button": 1})


def test_on_pointer_down_gated_during_button_detection() -> None:
    # #607: mouse control is suspended while the button-detection wizard runs.
    class _MouseHandler:
        def on_pointer_down(self, x, y, b) -> None:  # noqa: ANN001
            pytest.fail("pointer-down must be gated during button detection")

    app = _mouse_app(_MouseHandler(), _button_detection=object())
    app_modes.on_pointer_down(app, {"x": 1, "y": 2, "button": 1})


def test_on_pointer_up_delegates_to_mouse_when_enabled() -> None:
    calls: list = []

    class _MouseHandler:
        def on_pointer_up(self, x, y, b) -> None:  # noqa: ANN001
            calls.append((x, y, b))

    app = _mouse_app(_MouseHandler())
    app_modes.on_pointer_up(app, {"x": 3, "y": 4, "button": 1})
    assert calls == [(3, 4, 1)]


def test_on_pointer_down_delegates_to_mouse_when_enabled() -> None:
    calls: list = []

    class _MouseHandler:
        def on_pointer_down(self, x, y, b) -> None:  # noqa: ANN001
            calls.append((x, y, b))

    app = _mouse_app(_MouseHandler())
    app_modes.on_pointer_down(app, {"x": 5, "y": 6, "button": 2})
    assert calls == [(5, 6, 2)]


def test_on_resize_is_noop() -> None:
    app = SimpleNamespace()
    assert app_modes.on_resize(app, {"width": 100, "height": 100}) is None


# ---------------------------------------------------------------------------
# process_input guard paths
# ---------------------------------------------------------------------------


def test_process_input_exits_without_input_manager() -> None:
    app = SimpleNamespace(_input_manager=None)
    app_modes.process_input(app, 0.01)  # Must not raise.


def test_process_input_button_detection_takes_exclusive_control() -> None:
    called: list[bool] = []

    class _KeyboardHandler:
        keys: set[str] = set()

    class _InputManager:
        keyboard_handler = _KeyboardHandler()

    app = SimpleNamespace(
        _input_manager=_InputManager(),
        _button_detection=object(),
        _settings_menu_active=False,
        _video_receiver=None,
        _iface_selection_active=False,
        _source_type_selection_active=False,
        _url_editor_active=False,
        _field_choice_active=False,
        _browser_active=False,
    )
    app._process_button_detection = lambda: called.append(True)
    app_modes.process_input(app, 0.01)
    assert called == [True]


def test_process_input_delegates_to_settings_menu_when_active() -> None:
    called: list[bool] = []

    class _KeyboardHandler:
        keys: set[str] = set()

    class _InputManager:
        keyboard_handler = _KeyboardHandler()

    app = SimpleNamespace(
        _input_manager=_InputManager(),
        _button_detection=None,
        _settings_menu_active=True,
        _video_receiver=None,
        _iface_selection_active=False,
        _source_type_selection_active=False,
        _url_editor_active=False,
        _field_choice_active=False,
        _browser_active=False,
    )
    app._process_settings_menu_input = lambda: called.append(True)
    app_modes.process_input(app, 0.01)
    assert called == [True]


def test_process_input_routes_to_source_selection_when_active() -> None:
    called: list[bool] = []

    class _Receiver:
        source_selection_active = True

    class _KeyboardHandler:
        keys: set[str] = set()

    class _InputManager:
        keyboard_handler = _KeyboardHandler()

    app = SimpleNamespace(
        _input_manager=_InputManager(),
        _button_detection=None,
        _settings_menu_active=False,
        _video_receiver=_Receiver(),
        _iface_selection_active=False,
        _source_type_selection_active=False,
        _url_editor_active=False,
        _field_choice_active=False,
        _browser_active=False,
    )
    app._process_source_selection_input = lambda: called.append(True)
    app_modes.process_input(app, 0.01)
    assert called == [True]


def test_process_input_routes_to_iface_selection_when_active() -> None:
    called: list[bool] = []

    class _Receiver:
        source_selection_active = False

    class _KeyboardHandler:
        keys: set[str] = set()

    class _InputManager:
        keyboard_handler = _KeyboardHandler()

    app = SimpleNamespace(
        _input_manager=_InputManager(),
        _button_detection=None,
        _settings_menu_active=False,
        _video_receiver=_Receiver(),
        _iface_selection_active=True,
    )
    app._process_iface_selection_input = lambda: called.append(True)
    app_modes.process_input(app, 0.01)
    assert called == [True]


def test_process_input_routes_to_field_choice_picker_when_active() -> None:
    """The field-choice picker (enum-style config field selection) owns
    the gamepad poll while active, same shape as the other sub-screens.
    Without this branch the operator's nav input would fall through to
    main-mode shortcuts while choosing a value."""
    called: list[bool] = []

    class _Receiver:
        source_selection_active = False

    class _KeyboardHandler:
        keys: set[str] = set()

    class _InputManager:
        keyboard_handler = _KeyboardHandler()

    app = SimpleNamespace(
        _input_manager=_InputManager(),
        _button_detection=None,
        _settings_menu_active=False,
        _video_receiver=_Receiver(),
        _iface_selection_active=False,
        _source_type_selection_active=False,
        _field_choice_active=True,
    )
    app._process_field_choice_picker_input = lambda: called.append(True)
    app_modes.process_input(app, 0.01)
    assert called == [True]


def test_process_input_routes_to_browser_input_when_active() -> None:
    """Browser overlay routes gamepad poll into ``process_browser_input``
    (cancel-button-only) instead of short-circuiting entirely – without
    this, gamepad-only operators have no way to dismiss the WebView."""
    called: list[bool] = []

    class _Receiver:
        source_selection_active = False

    class _KeyboardHandler:
        keys: set[str] = set()

    class _InputManager:
        keyboard_handler = _KeyboardHandler()

    app = SimpleNamespace(
        _input_manager=_InputManager(),
        _button_detection=None,
        _settings_menu_active=False,
        _video_receiver=_Receiver(),
        _iface_selection_active=False,
        _source_type_selection_active=False,
        _field_choice_active=False,
        _url_editor_active=False,
        _browser_active=True,
    )
    app._process_browser_input = lambda: called.append(True)
    app_modes.process_input(app, 0.01)
    assert called == [True]


def test_process_input_dispatches_input_manager_result_actions() -> None:
    from openfollow.configuration import AppConfig
    from openfollow.input.gamepad import GamepadUpdate

    class _KeyboardHandler:
        keys: set[str] = set()

    class _InputManager:
        def __init__(self) -> None:
            self.keyboard_handler = _KeyboardHandler()

        def update(self, dt):  # noqa: ANN001, ANN201
            return GamepadUpdate(
                toggle_help_pressed=True,
                next_marker_pressed=True,
            )

    app = SimpleNamespace(
        _input_manager=_InputManager(),
        _button_detection=None,
        _settings_menu_active=False,
        _video_receiver=None,
        _iface_selection_active=False,
        _source_type_selection_active=False,
        _url_editor_active=False,
        _field_choice_active=False,
        _browser_active=False,
        _config=AppConfig(),
        _settings_key_pressed=False,
        _show_hud_help=False,
        _controlled_ids=[1, 2, 3],
        _selected_id=1,
    )
    app_modes.process_input(app, 0.01)
    assert app._show_hud_help is True
    assert app._selected_id == 2


# ---------------------------------------------------------------------------
# process_source_selection_input / process_iface_selection_input
# ---------------------------------------------------------------------------


class _SrcInput:
    def __init__(
        self,
        *,
        up: bool = False,
        down: bool = False,
        confirm: bool = False,
        cancel: bool = False,
    ) -> None:
        self.up_pressed = up
        self.down_pressed = down
        self.confirm_pressed = confirm
        self.cancel_pressed = cancel


def _make_src_selection_app(src_input, receiver_calls):  # noqa: ANN001, ANN202
    class _Gamepad:
        def read_source_selection_input(self):  # noqa: ANN201
            return src_input

    class _IM:
        gamepad_handler = _Gamepad()

    class _Receiver:
        def select_source_up(self) -> None:
            receiver_calls.append("up")

        def select_source_down(self) -> None:
            receiver_calls.append("down")

        def confirm_source_selection(self) -> None:
            receiver_calls.append("confirm")

        def exit_source_selection(self) -> None:
            receiver_calls.append("cancel")

    app = SimpleNamespace(_input_manager=_IM(), _video_receiver=_Receiver())
    # source-selection cancel re-opens Settings (Esc = back).
    app._enter_settings_menu = lambda *, banner="": None
    return app


def test_process_source_selection_input_forwards_all_buttons() -> None:
    calls: list[str] = []
    app = _make_src_selection_app(
        _SrcInput(up=True, down=True, confirm=True, cancel=True),
        calls,
    )
    app_modes.process_source_selection_input(app)
    assert calls == ["up", "down", "confirm", "cancel"]


def test_process_iface_selection_up_and_down_clamp_at_bounds() -> None:
    class _Gamepad:
        def read_source_selection_input(self):  # noqa: ANN201
            return _SrcInput(up=True)

    class _IM:
        gamepad_handler = _Gamepad()

    app = SimpleNamespace(
        _input_manager=_IM(),
        _available_interfaces=["", "a", "b"],
        _selected_iface_index=0,
        _iface_selection_active=True,
    )
    app._confirm_iface_selection = lambda: pytest.fail("must not confirm on up-only")
    app_modes.process_iface_selection_input(app)
    assert app._selected_iface_index == 0  # Clamped at 0, not negative.


def test_process_iface_selection_noop_when_empty() -> None:
    class _Gamepad:
        def read_source_selection_input(self):  # noqa: ANN201
            return _SrcInput(confirm=True)

    class _IM:
        gamepad_handler = _Gamepad()

    app = SimpleNamespace(
        _input_manager=_IM(),
        _available_interfaces=[],
        _selected_iface_index=0,
        _iface_selection_active=True,
    )
    app._confirm_iface_selection = lambda: pytest.fail("must not confirm with empty list")
    app_modes.process_iface_selection_input(app)


def test_process_iface_selection_cancel_exits() -> None:
    class _Gamepad:
        def read_source_selection_input(self):  # noqa: ANN201
            return _SrcInput(cancel=True)

    class _IM:
        gamepad_handler = _Gamepad()

    app = SimpleNamespace(
        _input_manager=_IM(),
        _available_interfaces=["", "a"],
        _selected_iface_index=0,
        _iface_selection_active=True,
    )
    app._confirm_iface_selection = lambda: pytest.fail("must not confirm on cancel")
    app_modes.process_iface_selection_input(app)
    assert app._iface_selection_active is False


# ---------------------------------------------------------------------------
# get_default_marker_position
# ---------------------------------------------------------------------------


def test_get_default_marker_position_reads_config() -> None:
    from openfollow.configuration import AppConfig

    cfg = AppConfig()
    cfg.marker.default_pos_x = 1.0
    cfg.marker.default_pos_y = 2.0
    cfg.marker.default_pos_z = 3.0
    app = SimpleNamespace(_config=cfg)
    assert app_modes.get_default_marker_position(app) == (1.0, 2.0, 3.0)


# ---------------------------------------------------------------------------
# process_input: settings-key edge gating + result actions
# ---------------------------------------------------------------------------


def _make_process_input_app(
    *,
    keys=None,
    settings_key="m",
    settings_key_pressed=False,
    update_result=None,
    settings_menu_active=False,
):
    from openfollow.configuration import AppConfig
    from openfollow.input.gamepad import GamepadUpdate

    class _KB:
        def __init__(self):
            self.keys = set(keys or [])

    class _IM:
        def __init__(self):
            self.keyboard_handler = _KB()

        def update(self, dt):
            return update_result if update_result is not None else GamepadUpdate()

    cfg = AppConfig()
    cfg.controller.key_settings = settings_key
    return SimpleNamespace(
        _input_manager=_IM(),
        _button_detection=None,
        _settings_menu_active=settings_menu_active,
        _video_receiver=None,
        _iface_selection_active=False,
        _source_type_selection_active=False,
        _url_editor_active=False,
        _field_choice_active=False,
        _browser_active=False,
        _available_source_types=[],
        _selected_source_type_index=0,
        _config=cfg,
        _settings_key_pressed=settings_key_pressed,
        _show_hud_help=False,
        _controlled_ids=[1, 2, 3],
        _selected_id=1,
    )


def test_process_input_settings_key_press_enters_settings_menu() -> None:
    """When `key_settings` becomes pressed for the first time (rising edge),
    the menu opens via the app's `_enter_settings_menu` method, and the
    edge-track flag latches so a held key doesn't re-fire each frame.
    Use ``"m"`` – the actual ``key_settings`` default and a member of
    ``VALID_KEY_NAMES`` / ``KeyboardHandler.POLLED_KEYS`` – so the
    scenario matches what a real user would actually see."""
    entered: list[bool] = []
    app = _make_process_input_app(keys=["m"])
    app._enter_settings_menu = lambda: entered.append(True)
    app_modes.process_input(app, 0.01)
    assert entered == [True]
    assert app._settings_key_pressed is True


def test_process_input_settings_key_held_does_not_re_enter_menu() -> None:
    entered: list[bool] = []
    app = _make_process_input_app(keys=["m"], settings_key_pressed=True)
    app._enter_settings_menu = lambda: entered.append(True)
    app_modes.process_input(app, 0.01)
    assert entered == []


def test_process_input_settings_key_release_clears_edge_latch() -> None:
    app = _make_process_input_app(keys=[], settings_key_pressed=True)
    app._enter_settings_menu = lambda: pytest.fail("must not enter on release")
    app_modes.process_input(app, 0.01)
    assert app._settings_key_pressed is False


def test_process_input_settings_open_pressed_via_gamepad_enters_menu() -> None:
    """When the input-manager update returns settings_open_pressed=True the
    menu opens via the gamepad path (independent of the key-edge gate)."""
    from openfollow.input.gamepad import GamepadUpdate

    entered: list[bool] = []
    app = _make_process_input_app(
        update_result=GamepadUpdate(settings_open_pressed=True),
    )
    app._enter_settings_menu = lambda: entered.append(True)
    app_modes.process_input(app, 0.01)
    assert entered == [True]


def test_process_input_toggle_zones_pressed_flips_overlay(tmp_path) -> None:
    """`result.toggle_zones_pressed` invokes `_toggle_zone_overlay`, which
    flips and persists `trigger_zones.show_overlay`."""
    from openfollow.configuration import AppConfig, save_config
    from openfollow.input.gamepad import GamepadUpdate

    cfg_path = tmp_path / "cfg.toml"
    save_config(AppConfig(), str(cfg_path))

    cfg = AppConfig()
    cfg.controller.key_settings = ""  # disabled – avoid the key-edge branch
    cfg.trigger_zones.show_overlay = False

    class _KB:
        keys = set()

    class _IM:
        keyboard_handler = _KB()

        def update(self, dt):
            return GamepadUpdate(toggle_zones_pressed=True)

    app = SimpleNamespace(
        _input_manager=_IM(),
        _button_detection=None,
        _settings_menu_active=False,
        _video_receiver=None,
        _iface_selection_active=False,
        _source_type_selection_active=False,
        _url_editor_active=False,
        _field_choice_active=False,
        _browser_active=False,
        _config=cfg,
        _config_path=str(cfg_path),
        _settings_key_pressed=False,
    )
    app_modes.process_input(app, 0.01)
    assert app._config.trigger_zones.show_overlay is True


def test_process_input_prev_marker_pressed_cycles_backwards() -> None:
    from openfollow.input.gamepad import GamepadUpdate

    app = _make_process_input_app(
        update_result=GamepadUpdate(prev_marker_pressed=True),
    )
    app._selected_id = 2
    app_modes.process_input(app, 0.01)
    assert app._selected_id == 1


def test_toggle_zone_overlay_logs_warning_on_save_failure(tmp_path, caplog) -> None:
    import logging as _logging

    from openfollow.configuration import AppConfig

    cfg = AppConfig()
    cfg.trigger_zones.show_overlay = False
    app = SimpleNamespace(_config=cfg, _config_path=str(tmp_path / "missing-dir" / "cfg.toml"))

    with caplog.at_level(_logging.WARNING, logger="openfollow.runtime.app_modes"):
        app_modes._toggle_zone_overlay(app)

    assert cfg.trigger_zones.show_overlay is True
    assert any("Failed to persist zone overlay toggle" in rec.message for rec in caplog.records)


def test_process_source_selection_input_logs_on_exception(caplog) -> None:
    import logging as _logging

    class _Gamepad:
        def read_source_selection_input(self):
            raise RuntimeError("gamepad gone")

    class _IM:
        gamepad_handler = _Gamepad()

    app = SimpleNamespace(_input_manager=_IM(), _video_receiver=object())
    with caplog.at_level(_logging.WARNING, logger="openfollow.runtime.app_modes"):
        app_modes.process_source_selection_input(app)
    assert any("Source selection input error" in rec.message for rec in caplog.records)


def test_process_iface_selection_logs_on_exception(caplog) -> None:
    import logging as _logging

    class _Gamepad:
        def read_source_selection_input(self):
            raise RuntimeError("boom")

    class _IM:
        gamepad_handler = _Gamepad()

    app = SimpleNamespace(_input_manager=_IM(), _available_interfaces=["a"], _selected_iface_index=0)
    with caplog.at_level(_logging.WARNING, logger="openfollow.runtime.app_modes"):
        app_modes.process_iface_selection_input(app)
    assert any("Interface selection input error" in rec.message for rec in caplog.records)


def test_process_iface_selection_confirm_invokes_app_callback() -> None:
    confirms: list[bool] = []

    class _Gamepad:
        def read_source_selection_input(self):
            return _SrcInput(confirm=True)

    class _IM:
        gamepad_handler = _Gamepad()

    app = SimpleNamespace(
        _input_manager=_IM(),
        _available_interfaces=["", "1.2.3.4"],
        _selected_iface_index=1,
        _iface_selection_active=True,
    )
    app._confirm_iface_selection = lambda: confirms.append(True)
    app_modes.process_iface_selection_input(app)
    assert confirms == [True]


# ---------------------------------------------------------------------------
# Settings menu helpers
# ---------------------------------------------------------------------------


def _make_settings_menu_app(*, has_controller=True, has_source=True, menu_index=0):
    from openfollow.configuration import AppConfig

    class _Gamepad:
        joysticks = {0: object()} if has_controller else {}

    class _IM:
        gamepad_handler = _Gamepad()

    receiver = SimpleNamespace(has_source_selection=has_source) if has_source is not None else None

    return SimpleNamespace(
        _config=AppConfig(),
        _input_manager=_IM(),
        _video_receiver=receiver,
        _settings_menu_active=False,
        _settings_menu_index=menu_index,
    )


def test_build_settings_menu_items_disables_controller_dependent_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """6 rows total. Change Video Source enabled whenever ANY video receiver
    exists; Button Detection requires a connected controller."""
    from openfollow.runtime import webkit_browser

    monkeypatch.setattr(webkit_browser, "AVAILABLE", True)
    app = _make_settings_menu_app(has_controller=False, has_source=False)
    labels, enabled, _reasons = app_modes.build_settings_menu_items(app)
    assert labels == [
        "Network",
        "Change Video Source",
        "Button Detection",
        "Open Web UI",
        "Restart",
        "About",
    ]
    assert enabled[0] is True  # Network
    assert enabled[1] is True  # Change Video Source – receiver exists
    assert enabled[3] is True  # Open Web UI – WebKit mocked True
    assert enabled[4] is True  # Restart
    assert enabled[5] is True  # About – always available (no prerequisites)
    # Button Detection disabled by missing controller.
    assert enabled[2] is False


def test_build_settings_menu_items_disables_change_video_source_without_receiver() -> None:
    app = _make_settings_menu_app(has_controller=True, has_source=None)
    labels, enabled, _reasons = app_modes.build_settings_menu_items(app)
    idx = labels.index("Change Video Source")
    assert enabled[idx] is False


def test_build_settings_menu_items_web_ui_says_linux_only_on_mac(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On macOS when WebKit2GTK typelib is absent, menu shows "Linux only" hint."""
    import sys as _sys

    from openfollow.runtime import webkit_browser

    monkeypatch.setattr(webkit_browser, "AVAILABLE", False)
    monkeypatch.setattr(_sys, "platform", "darwin")
    app = _make_settings_menu_app(has_controller=True, has_source=True)
    labels, enabled, reasons = app_modes.build_settings_menu_items(app)
    idx = labels.index("Open Web UI")
    assert enabled[idx] is False
    assert reasons[idx] == "Linux only"


def test_build_settings_menu_items_web_ui_says_install_hint_on_linux(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On Linux without typelib, surface apt package candidates for versions 4.1 and 4.0."""
    import sys as _sys

    from openfollow.runtime import webkit_browser

    monkeypatch.setattr(webkit_browser, "AVAILABLE", False)
    monkeypatch.setattr(_sys, "platform", "linux")
    app = _make_settings_menu_app(has_controller=True, has_source=True)
    labels, enabled, reasons = app_modes.build_settings_menu_items(app)
    idx = labels.index("Open Web UI")
    assert enabled[idx] is False
    assert "gir1.2-webkit2-4.1" in reasons[idx]
    assert "4.0" in reasons[idx]


def test_build_settings_menu_items_web_ui_no_reason_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the row is enabled, the reason stays empty – the draw
    layer never reads it."""
    from openfollow.runtime import webkit_browser

    monkeypatch.setattr(webkit_browser, "AVAILABLE", True)
    app = _make_settings_menu_app(has_controller=True, has_source=True)
    labels, enabled, reasons = app_modes.build_settings_menu_items(app)
    idx = labels.index("Open Web UI")
    assert enabled[idx] is True
    assert reasons[idx] == ""


def test_settings_menu_action_returns_none_for_invalid_index() -> None:
    app = SimpleNamespace()
    assert app_modes._settings_menu_action(app, -1) is None
    assert app_modes._settings_menu_action(app, 99) is None
    assert app_modes._settings_menu_action(app, 0) == "network"


def test_enter_settings_menu_is_idempotent_when_already_active() -> None:
    app = SimpleNamespace(_settings_menu_active=True, _settings_menu_index=4)
    app_modes.enter_settings_menu(app)
    assert app._settings_menu_active is True
    assert app._settings_menu_index == 4


def test_settings_menu_move_skips_disabled_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # has_source=None disables Change Video Source (1);
    # has_controller=False disables Button Detection (2);
    # WebKit unavailable disables Open Web UI (3).
    # ArrowDown from Network (0) lands on Restart (4).
    from openfollow.runtime import webkit_browser

    monkeypatch.setattr(webkit_browser, "AVAILABLE", False)
    app = _make_settings_menu_app(has_controller=False, has_source=None, menu_index=0)
    app_modes._settings_menu_move(app, +1)
    assert app._settings_menu_index == 4


def test_settings_menu_move_no_op_when_no_items_enabled() -> None:
    # Override build_settings_menu_items to return nothing enabled.
    app = SimpleNamespace(_settings_menu_index=0, _input_manager=None, _video_receiver=None)
    # Force build_settings_menu_items to return ([], [])
    saved = app_modes.build_settings_menu_items
    app_modes.build_settings_menu_items = lambda _app: ([], [], [])
    try:
        app_modes._settings_menu_move(app, +1)
        assert app._settings_menu_index == 0
    finally:
        app_modes.build_settings_menu_items = saved


def test_settings_menu_confirm_no_op_on_disabled_row() -> None:
    """Confirming a disabled row must not invoke any app callback."""
    # Button Detection (index 2) disabled when no controller.
    # Confirming must not fire wizard.
    app = _make_settings_menu_app(has_controller=False, has_source=True, menu_index=2)
    app._settings_menu_active = True
    app._enter_button_detection = lambda: pytest.fail("must not fire on disabled row")
    app_modes._settings_menu_confirm(app)
    # Menu stays open since the action was rejected.
    assert app._settings_menu_active is True


@pytest.mark.parametrize(
    "menu_index,expected_callback",
    [
        (1, "_enter_source_type_selection"),
        (2, "_enter_button_detection"),
        (3, "_enter_browser"),
        (4, "_restart_app"),
    ],
)
def test_settings_menu_confirm_dispatches_to_correct_app_callback(
    menu_index: int,
    expected_callback: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each enabled menu row dispatches to a specific app method and exits
    the menu. Video rows collapsed into 'Change Video Source' entry."""
    from openfollow.runtime import webkit_browser

    monkeypatch.setattr(webkit_browser, "AVAILABLE", True)
    calls: list[str] = []
    app = _make_settings_menu_app(has_controller=True, has_source=True, menu_index=menu_index)
    app._settings_menu_active = True
    # ``_enter_url_editor`` accepts kwargs (banner / revert_type) on
    # the real app; the dispatcher invokes it with no args from the
    # menu, but the lambda captures whatever's passed via ``**kw``.
    for name in (
        "_enter_iface_selection",
        "_enter_source_type_selection",
        "_enter_button_detection",
        "_enter_browser",
        "_restart_app",
    ):
        setattr(app, name, lambda *_a, _name=name, **_kw: calls.append(_name))
    app_modes._settings_menu_confirm(app)
    assert calls == [expected_callback]
    assert app._settings_menu_active is False


def _make_minimal_app_for_dispatch() -> SimpleNamespace:
    """Bare app with every state flag and gate the network dispatch checks."""
    from openfollow.configuration import AppConfig

    class _KB:
        keys: set[str] = set()

    class _IM:
        keyboard_handler = _KB()

        def update(self, _dt):
            return SimpleNamespace(
                settings_open_pressed=False,
                toggle_help_pressed=False,
                toggle_zones_pressed=False,
                next_marker_pressed=False,
                prev_marker_pressed=False,
            )

    return SimpleNamespace(
        _input_manager=_IM(),
        _button_detection=None,
        _settings_menu_active=False,
        _video_receiver=None,
        _iface_selection_active=False,
        _source_type_selection_active=False,
        _field_choice_active=False,
        _url_editor_active=False,
        _browser_active=False,
        _pi_network_field_edit_active=False,
        _pi_network_method_picker_active=False,
        _pi_network_iface_picker_active=False,
        _pi_network_active=False,
        _config=AppConfig(),
        _settings_key_pressed=False,
    )


@pytest.mark.parametrize(
    "state_attr,dispatcher_name",
    [
        ("_pi_network_field_edit_active", "process_pi_network_field_edit_input"),
        ("_pi_network_method_picker_active", "process_pi_network_method_picker_input"),
        ("_pi_network_iface_picker_active", "process_pi_network_iface_picker_input"),
        ("_pi_network_active", "process_pi_network_input"),
    ],
)
def test_process_input_routes_into_network_substate(
    state_attr: str,
    dispatcher_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openfollow.runtime import app_modes_network as anm

    called: list[bool] = []
    monkeypatch.setattr(anm, dispatcher_name, lambda _app: called.append(True))
    app = _make_minimal_app_for_dispatch()
    setattr(app, state_attr, True)
    app_modes.process_input(app, 0.01)
    assert called == [True]


@pytest.mark.parametrize(
    "state_attr,handler_name",
    [
        ("_pi_network_method_picker_active", "handle_pi_network_method_picker_key"),
        ("_pi_network_iface_picker_active", "handle_pi_network_iface_picker_key"),
        ("_pi_network_active", "handle_pi_network_key"),
    ],
)
def test_handle_key_press_routes_into_network_substate(
    state_attr: str,
    handler_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same priority chain as ``process_input`` but for keyboard input
    (Esc on the on-screen menu, single-char typing, …)."""
    from openfollow.runtime import app_modes_network as anm

    called: list[str] = []
    monkeypatch.setattr(anm, handler_name, lambda _app, key: called.append(key))
    app = _make_minimal_app_for_dispatch()
    setattr(app, state_attr, True)
    app_modes.handle_key_press(app, "Enter")
    assert called == ["Enter"]


def test_handle_key_press_field_edit_does_not_route_to_field_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openfollow.runtime import app_modes_network as anm

    called: list[str] = []
    monkeypatch.setattr(
        anm,
        "handle_pi_network_field_edit_key",
        lambda _app, key: called.append(key),
    )
    app = _make_minimal_app_for_dispatch()
    app._pi_network_field_edit_active = True
    app_modes.handle_key_press(app, "Enter")
    assert called == []


def test_on_key_down_field_edit_accepts_typed_characters() -> None:
    """Regression (diagnostics: attached USB keyboard "dead" in network
    settings): digits / '.' / Backspace reach the Pi network field editor
    through ``on_key_down`` even though they're absent from ``POLLED_KEYS``.
    On Linux the keyboard runs in fallback event-based mode, so the polled
    path silently dropped every typeable IP character."""
    app = SimpleNamespace(
        _url_editor_active=False,
        _field_choice_active=False,
        _browser_active=False,
        _pi_network_field_edit_active=True,
        _pi_network_field_value="",
        _input_manager=None,
        _controlled_ids=[],
    )
    app._normalize_key = app_modes.normalize_key
    # window's _GDK_KEY_MAP has already mapped 'period' -> '.' and digits
    # pass through, so on_key_down sees the canonical characters.
    for k in ["1", "9", "2", ".", "1", "6", "8"]:
        app_modes.on_key_down(app, {"key": k})
    assert app._pi_network_field_value == "192.168"
    app_modes.on_key_down(app, {"key": "Backspace"})
    assert app._pi_network_field_value == "192.16"


def test_on_key_down_digit_does_not_reselect_marker_during_field_edit() -> None:
    app = SimpleNamespace(
        _url_editor_active=False,
        _field_choice_active=False,
        _browser_active=False,
        _pi_network_field_edit_active=True,
        _pi_network_field_value="",
        _input_manager=None,
        _controlled_ids=[10, 20, 30],
        _selected_id=10,
    )
    app._normalize_key = app_modes.normalize_key
    app_modes.on_key_down(app, {"key": "2"})
    assert app._pi_network_field_value == "2"
    assert app._selected_id == 10  # marker unchanged – shortcut skipped


def test_settings_menu_confirm_index_0_opens_network_screen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Index 0 (Network) opens the Network screen; legacy iface picker was merged into it."""
    calls: list[str] = []
    app = _make_settings_menu_app(has_controller=True, has_source=True, menu_index=0)
    app._settings_menu_active = True
    # Minimal state slots the Network screen entry needs.
    app._pi_network_active = False
    app._pi_network_index = 0
    app._pi_network_interfaces = []
    app._pi_network_active_iface = ""
    app._pi_network_state_cache = None
    app._pi_network_pending_config = None
    app._pi_network_banner = ""
    app._pi_network_busy = False
    app._runtime_services = SimpleNamespace(network_adapter=None)
    for name in (
        "_enter_iface_selection",
        "_enter_source_type_selection",
        "_enter_button_detection",
        "_enter_browser",
        "_restart_app",
    ):
        setattr(app, name, lambda *_a, _name=name, **_kw: calls.append(_name))
    app_modes._settings_menu_confirm(app)
    assert calls == []  # no legacy app callback fired
    assert app._pi_network_active is True
    assert app._settings_menu_active is False


def test_process_settings_menu_input_up_down_confirm_cancel() -> None:
    """Each gamepad direction maps 1:1 to the corresponding menu helper."""
    moves: list[int] = []
    confirms: list[bool] = []

    class _MenuInput:
        up_pressed = True
        down_pressed = True
        confirm_pressed = True
        cancel_pressed = False  # confirm_pressed is checked first

    class _Gamepad:
        def read_settings_menu_input(self):
            return _MenuInput()

    class _IM:
        gamepad_handler = _Gamepad()

    app = SimpleNamespace(_input_manager=_IM(), _settings_menu_active=True)

    saved_move = app_modes._settings_menu_move
    saved_confirm = app_modes._settings_menu_confirm
    saved_exit = app_modes.exit_settings_menu

    try:
        app_modes._settings_menu_move = lambda _a, step: moves.append(step)
        app_modes._settings_menu_confirm = lambda _a: confirms.append(True)
        app_modes.exit_settings_menu = lambda _a: pytest.fail("cancel must not fire when confirm did")
        app_modes.process_settings_menu_input(app)
    finally:
        app_modes._settings_menu_move = saved_move
        app_modes._settings_menu_confirm = saved_confirm
        app_modes.exit_settings_menu = saved_exit

    assert moves == [-1, +1]
    assert confirms == [True]


def test_process_settings_menu_input_cancel_exits_menu() -> None:
    """When confirm is False but cancel is True, the menu closes."""
    closed: list[bool] = []

    class _MenuInput:
        up_pressed = False
        down_pressed = False
        confirm_pressed = False
        cancel_pressed = True

    class _Gamepad:
        def read_settings_menu_input(self):
            return _MenuInput()

    class _IM:
        gamepad_handler = _Gamepad()

    app = SimpleNamespace(_input_manager=_IM(), _settings_menu_active=True)
    saved_exit = app_modes.exit_settings_menu
    try:
        app_modes.exit_settings_menu = lambda _a: closed.append(True)
        app_modes.process_settings_menu_input(app)
    finally:
        app_modes.exit_settings_menu = saved_exit
    assert closed == [True]


def test_process_settings_menu_input_swallows_gamepad_exception(caplog) -> None:
    import logging as _logging

    class _Gamepad:
        def read_settings_menu_input(self):
            raise RuntimeError("gamepad gone")

    class _IM:
        gamepad_handler = _Gamepad()

    app = SimpleNamespace(_input_manager=_IM(), _settings_menu_active=True)
    with caplog.at_level(_logging.WARNING, logger="openfollow.runtime.app_modes"):
        app_modes.process_settings_menu_input(app)
    assert any("Settings menu input error" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# Adjust move speed
# ---------------------------------------------------------------------------

# adjust_move_speed writes to per-marker dict with global move_speed fallback


def _make_speed_app(
    *,
    move_speed: float,
    min_speed: float = 0.0,
    max_speed: float = 20.0,
    selected_id: int = 1,
    last_t: float = 0.0,
    streak: int = 0,
    last_dir: int = 0,
) -> SimpleNamespace:
    from openfollow.configuration import AppConfig

    cfg = AppConfig()
    cfg.marker.move_speed = move_speed
    cfg.marker.min_speed = min_speed
    cfg.marker.max_speed = max_speed
    cfg.marker_move_speeds = {selected_id: move_speed}
    # Seed helper marker so streak parameters take effect.
    app = SimpleNamespace(
        _config=cfg,
        _selected_id=selected_id,
        _speed_key_last_t={selected_id: last_t},
        _speed_key_streak={selected_id: streak},
        _speed_key_last_dir={selected_id: last_dir},
    )
    app.get_marker_move_speed = lambda mid: (
        cfg.marker_move_speeds.get(mid, cfg.marker.move_speed) if mid is not None else cfg.marker.move_speed
    )
    return app


def test_adjust_move_speed_negative_decreases_within_min_floor() -> None:
    app = _make_speed_app(move_speed=3.0, min_speed=0.0, max_speed=10.0)
    app_modes.adjust_move_speed(app, -1)
    # base_step=0.1 (flat), streak=0 → multiplier=1 → 3.0 - 0.1
    assert app._config.marker_move_speeds[1] == pytest.approx(2.9)


def test_adjust_move_speed_clamps_to_min_speed_floor() -> None:
    app = _make_speed_app(move_speed=0.05, min_speed=0.0, max_speed=10.0)
    app_modes.adjust_move_speed(app, -1)
    assert app._config.marker_move_speeds[1] == pytest.approx(0.0)


def test_adjust_move_speed_base_step_is_flat_at_all_speeds() -> None:
    """The per-press base step is a flat 0.1 m/s at every speed – no
    speed-tiered 0.2/0.4 steps (the streak multiplier still applies)."""
    for start in (5.0, 7.0):
        app = _make_speed_app(move_speed=start, max_speed=20.0)
        app_modes.adjust_move_speed(app, +1)
        assert app._config.marker_move_speeds[1] == pytest.approx(start + 0.1)


def test_adjust_move_speed_streak_acceleration_3x_after_5_presses() -> None:
    """Streak 5-9 multiplies the step by 3 – needed so the user can ramp
    speed quickly with held key presses."""
    app = _make_speed_app(
        move_speed=1.0,
        max_speed=20.0,
        last_t=time_module_now(),
        streak=5,
        last_dir=+1,
    )
    app_modes.adjust_move_speed(app, +1)
    # 1.0 + 0.1 * 3 = 1.3
    assert app._config.marker_move_speeds[1] == pytest.approx(1.3)


def test_adjust_move_speed_streak_acceleration_8x_after_10_presses() -> None:
    app = _make_speed_app(
        move_speed=1.0,
        max_speed=20.0,
        last_t=time_module_now(),
        streak=10,
        last_dir=+1,
    )
    app_modes.adjust_move_speed(app, +1)
    # 1.0 + 0.1 * 8 = 1.8
    assert app._config.marker_move_speeds[1] == pytest.approx(1.8)


def test_adjust_move_speed_streak_is_independent_per_marker(monkeypatch) -> None:
    app = _make_speed_app(move_speed=1.0, max_speed=20.0, selected_id=1)
    app._config.marker_move_speeds[2] = 1.0
    # 13 timestamps spaced 0.05s apart (< the 0.75s streak window): 12 for the
    # marker-1 ramp + 1 for the marker-2 press.
    times = iter([10.0 + 0.05 * i for i in range(13)])
    monkeypatch.setattr(time, "monotonic", lambda: next(times))

    # Pad A: ramp marker 1 well into the 8x tier with rapid same-direction taps.
    for _ in range(12):
        app_modes.adjust_move_speed(app, +1, marker_id=1)
    assert app._speed_key_streak[1] >= 10  # marker 1 reached the 8x tier

    speed1_before = app._config.marker_move_speeds[1]
    # Pad B: a single press on marker 2 must start a fresh 1x streak (step 0.1),
    # NOT inherit marker 1's 8x multiplier (which would step 0.8 → 1.8).
    app_modes.adjust_move_speed(app, +1, marker_id=2)
    assert app._config.marker_move_speeds[2] == pytest.approx(1.1)  # 1.0 + 0.1 * 1
    assert app._speed_key_streak[2] == 0
    # Marker 1's streak / value are untouched by pad B's press.
    assert app._speed_key_streak[1] >= 10
    assert app._config.marker_move_speeds[1] == pytest.approx(speed1_before)


def time_module_now() -> float:
    """Helper: returns a 'recent enough' timestamp so streak logic uses the
    streak parameter rather than resetting it."""
    import time

    return time.monotonic()


# ---------------------------------------------------------------------------
# handle_key_press: early-return guards + per-mode branches
# ---------------------------------------------------------------------------


def test_handle_key_press_button_detection_consumes_input() -> None:
    from openfollow.configuration import AppConfig

    app = SimpleNamespace(
        _config=AppConfig(),
        _button_detection=object(),
    )
    # No other attributes referenced – would AttributeError if the early
    # return weren't taken.
    app_modes.handle_key_press(app, "Enter")


def test_handle_key_press_settings_menu_arrows_and_enter() -> None:
    from openfollow.configuration import AppConfig

    nav: list[int] = []
    confirms: list[bool] = []
    closed: list[bool] = []
    saved_move = app_modes._settings_menu_move
    saved_confirm = app_modes._settings_menu_confirm
    saved_exit = app_modes.exit_settings_menu

    app = SimpleNamespace(
        _config=AppConfig(),
        _button_detection=None,
        _settings_menu_active=True,
    )

    try:
        app_modes._settings_menu_move = lambda _a, step: nav.append(step)
        app_modes._settings_menu_confirm = lambda _a: confirms.append(True)
        app_modes.exit_settings_menu = lambda _a: closed.append(True)

        app_modes.handle_key_press(app, "ArrowUp")
        app_modes.handle_key_press(app, "ArrowDown")
        app_modes.handle_key_press(app, "Enter")
        app_modes.handle_key_press(app, "Escape")
    finally:
        app_modes._settings_menu_move = saved_move
        app_modes._settings_menu_confirm = saved_confirm
        app_modes.exit_settings_menu = saved_exit

    assert nav == [-1, +1]
    assert confirms == [True]
    assert closed == [True]


def test_handle_key_press_source_selection_arrows_enter_escape() -> None:
    from openfollow.configuration import AppConfig

    log: list[str] = []

    class _Receiver:
        source_selection_active = True
        source_name = "Cam-A"

        def select_source_up(self):
            log.append("up")

        def select_source_down(self):
            log.append("down")

        def confirm_source_selection(self):
            log.append("confirm")
            return False  # Forces the set_source fallback.

        def set_source(self, label):
            log.append(f"set:{label}")

        def exit_source_selection(self):
            log.append("exit")

    app = SimpleNamespace(
        _config=AppConfig(),
        _button_detection=None,
        _settings_menu_active=False,
        _video_receiver=_Receiver(),
    )
    # Esc in sub-screen re-opens Settings menu.
    app._enter_settings_menu = lambda *, banner="": log.append("back-to-settings")

    for key in ("ArrowUp", "ArrowDown", "Enter", "Escape"):
        app_modes.handle_key_press(app, key)

    assert log == [
        "up",
        "down",
        "confirm",
        "set:Cam-A",
        "exit",
        "back-to-settings",
    ]


def test_handle_key_press_source_selection_enter_skips_set_source_when_confirm_succeeds() -> None:
    from openfollow.configuration import AppConfig

    log: list[str] = []

    class _Receiver:
        source_selection_active = True
        source_name = "Cam-B"

        def confirm_source_selection(self):
            log.append("confirm")
            return True

        def set_source(self, label):
            log.append(f"set:{label}")

        def select_source_up(self): ...
        def select_source_down(self): ...
        def exit_source_selection(self): ...

    app = SimpleNamespace(
        _config=AppConfig(),
        _button_detection=None,
        _settings_menu_active=False,
        _video_receiver=_Receiver(),
    )
    app_modes.handle_key_press(app, "Enter")
    assert log == ["confirm"]


def test_handle_key_press_iface_selection_arrows_clamp_at_bounds() -> None:
    from openfollow.configuration import AppConfig

    app = SimpleNamespace(
        _config=AppConfig(),
        _button_detection=None,
        _settings_menu_active=False,
        _video_receiver=None,
        _iface_selection_active=True,
        _available_interfaces=["", "1.2.3.4", "5.6.7.8"],
        _selected_iface_index=0,
    )
    app._confirm_iface_selection = lambda: pytest.fail("not on arrows")

    app_modes.handle_key_press(app, "ArrowUp")
    assert app._selected_iface_index == 0  # already at top
    app_modes.handle_key_press(app, "ArrowDown")
    assert app._selected_iface_index == 1
    app_modes.handle_key_press(app, "ArrowDown")
    app_modes.handle_key_press(app, "ArrowDown")  # would exceed len-1
    assert app._selected_iface_index == 2


def test_handle_key_press_iface_selection_enter_confirms_and_escape_exits() -> None:
    from openfollow.configuration import AppConfig

    confirms: list[bool] = []
    back_calls: list[bool] = []

    app = SimpleNamespace(
        _config=AppConfig(),
        _button_detection=None,
        _settings_menu_active=False,
        _video_receiver=None,
        _iface_selection_active=True,
        _available_interfaces=["", "10.0.0.1"],
        _selected_iface_index=1,
    )
    app._confirm_iface_selection = lambda: confirms.append(True)
    app._enter_settings_menu = lambda *, banner="": back_calls.append(True)

    app_modes.handle_key_press(app, "Enter")
    assert confirms == [True]

    app_modes.handle_key_press(app, "Escape")
    assert app._iface_selection_active is False
    # Esc returns to Settings menu, not normal mode.
    assert back_calls == [True]


def test_handle_key_press_iface_selection_no_op_when_empty() -> None:
    """An empty interface list means there's nothing to navigate – the
    early return prevents IndexError on the bounds clamp arithmetic."""
    from openfollow.configuration import AppConfig

    app = SimpleNamespace(
        _config=AppConfig(),
        _button_detection=None,
        _settings_menu_active=False,
        _video_receiver=None,
        _iface_selection_active=True,
        _available_interfaces=[],
        _selected_iface_index=0,
    )
    app_modes.handle_key_press(app, "ArrowUp")  # must not raise


def test_handle_key_press_main_mode_action_keys_dispatch_correctly() -> None:
    """In normal mode (no overlays / wizards open) action keys map to:
    cycle_marker(+/-), reset, toggle help, toggle zones, speed up/down."""
    from openfollow.configuration import AppConfig

    speed_calls: list[int] = []
    reset_calls: list = []

    class _Marker:
        def set_pos(self, x, y, z):
            reset_calls.append((x, y, z))

    class _Server:
        def get_marker(self, _id):
            return _Marker()

    # Bindings drawn from ``VALID_KEY_NAMES`` (lowercase letters / Tab) –
    # the dataclass defaults wherever possible – so the scenario
    # represents a config a user could actually load and ``__post_init__``
    # wouldn't reset.
    cfg = AppConfig()
    cfg.controller.key_next_marker = "n"
    cfg.controller.key_prev_marker = "p"
    cfg.controller.key_reset = "x"  # default
    cfg.controller.key_toggle_help = "h"  # default
    cfg.controller.key_toggle_zones = "z"  # default
    cfg.controller.key_speed_down = "r"  # default
    cfg.controller.key_speed_up = "t"  # default

    app = SimpleNamespace(
        _config=cfg,
        _button_detection=None,
        _settings_menu_active=False,
        _video_receiver=None,
        _iface_selection_active=False,
        _source_type_selection_active=False,
        _url_editor_active=False,
        _field_choice_active=False,
        _browser_active=False,
        _show_hud_help=False,
        _controlled_ids=[7, 8, 9],
        _selected_id=7,
        _server=_Server(),
        _config_path="/dev/null",
    )
    app.adjust_move_speed = lambda direction: speed_calls.append(direction)
    app._get_default_marker_position = lambda: (1.0, 2.0, 3.0)

    app_modes.handle_key_press(app, "n")
    assert app._selected_id == 8
    app_modes.handle_key_press(app, "p")
    assert app._selected_id == 7
    app_modes.handle_key_press(app, "x")
    assert reset_calls == [(1.0, 2.0, 3.0)]
    app_modes.handle_key_press(app, "h")
    assert app._show_hud_help is True
    app_modes.handle_key_press(app, "r")
    app_modes.handle_key_press(app, "t")
    assert speed_calls == [-1, +1]


def test_handle_key_press_main_mode_reset_no_op_without_selected_id() -> None:
    """The reset key must do nothing when no marker is selected – without
    the guard, `get_marker(None)` would surface as an AttributeError.
    Use the dataclass default ``"x"`` (which is in ``VALID_KEY_NAMES`` and
    survives ``__post_init__``); other action bindings cleared to ``""``
    so they can't accidentally fire on the test press."""
    from openfollow.configuration import AppConfig

    cfg = AppConfig()
    cfg.controller.key_reset = "x"  # default, in VALID_KEY_NAMES
    cfg.controller.key_next_marker = ""
    cfg.controller.key_prev_marker = ""
    cfg.controller.key_toggle_help = ""
    cfg.controller.key_toggle_zones = ""
    cfg.controller.key_speed_down = ""
    cfg.controller.key_speed_up = ""

    app = SimpleNamespace(
        _config=cfg,
        _button_detection=None,
        _settings_menu_active=False,
        _video_receiver=None,
        _iface_selection_active=False,
        _source_type_selection_active=False,
        _url_editor_active=False,
        _field_choice_active=False,
        _browser_active=False,
        _selected_id=None,
        _server=None,  # would AttributeError if guard missing
    )
    app_modes.handle_key_press(app, "x")  # must not raise


# ---------------------------------------------------------------------------
# on_key_up + on_pointer_* mouse delegation paths
# ---------------------------------------------------------------------------


def test_on_key_up_forwards_event_to_keyboard_handler() -> None:
    captured: list[dict] = []

    class _KB:
        def on_key_up(self, event):
            captured.append(event)

    class _IM:
        keyboard_handler = _KB()

    app = SimpleNamespace(
        _input_manager=_IM(),
        _url_editor_active=False,
        _field_choice_active=False,
        _browser_active=False,
    )
    app_modes.on_key_up(app, {"key": "w"})
    assert captured == [{"key": "w"}]


def test_on_key_up_no_op_without_input_manager() -> None:
    app = SimpleNamespace(
        _input_manager=None,
        _url_editor_active=False,
        _field_choice_active=False,
        _browser_active=False,
    )
    app_modes.on_key_up(app, {"key": "w"})  # must not raise


def test_on_key_up_skipped_while_url_editor_active() -> None:
    captured: list[dict] = []

    class _KB:
        def on_key_up(self, event):
            captured.append(event)

    class _IM:
        keyboard_handler = _KB()

    app = SimpleNamespace(
        _input_manager=_IM(),
        _url_editor_active=True,
        _browser_active=False,
    )
    app_modes.on_key_up(app, {"key": "w"})
    assert captured == []


def test_on_key_up_skipped_while_browser_active() -> None:
    """Symmetric guard for the browser overlay."""
    captured: list[dict] = []

    class _KB:
        def on_key_up(self, event):
            captured.append(event)

    class _IM:
        keyboard_handler = _KB()

    app = SimpleNamespace(
        _input_manager=_IM(),
        _url_editor_active=False,
        _field_choice_active=False,
        _browser_active=True,
    )
    app_modes.on_key_up(app, {"key": "w"})
    assert captured == []


def test_on_pointer_down_routes_to_mouse_handler_when_mouse_enabled() -> None:
    from openfollow.configuration import AppConfig

    captured: list = []

    class _Mouse:
        def on_pointer_down(self, x, y, button):
            captured.append((x, y, button))

    class _IM:
        mouse_handler = _Mouse()

    cfg = AppConfig()
    cfg.controller.mouse_enabled = True
    app = SimpleNamespace(_input_manager=_IM(), _config=cfg, _browser_active=False)

    app_modes.on_pointer_down(app, {"x": 10, "y": 20, "button": 1})
    assert captured == [(10, 20, 1)]


def test_on_pointer_up_routes_to_mouse_handler_when_mouse_enabled() -> None:
    from openfollow.configuration import AppConfig

    captured: list = []

    class _Mouse:
        def on_pointer_up(self, x, y, button):
            captured.append((x, y, button))

    class _IM:
        mouse_handler = _Mouse()

    cfg = AppConfig()
    cfg.controller.mouse_enabled = True
    app = SimpleNamespace(_input_manager=_IM(), _config=cfg, _browser_active=False)

    app_modes.on_pointer_up(app, {"x": 5, "y": 7, "button": 2})
    assert captured == [(5, 7, 2)]


def test_on_pointer_down_no_op_when_mouse_disabled() -> None:
    from openfollow.configuration import AppConfig

    class _Mouse:
        def on_pointer_down(self, *_):
            pytest.fail("must not fire when mouse_enabled is False")

    class _IM:
        mouse_handler = _Mouse()

    cfg = AppConfig()
    cfg.controller.mouse_enabled = False
    app = SimpleNamespace(_input_manager=_IM(), _config=cfg, _browser_active=False)

    app_modes.on_pointer_down(app, {"x": 0, "y": 0, "button": 1})


def test_on_pointer_down_skipped_while_browser_active() -> None:
    """Clicks on empty WebView areas bubble up to the toplevel
    window; without this gate they'd drive the tracker's marker
    position while the operator is clicking around the web UI."""

    class _Mouse:
        def on_pointer_down(self, *_):
            pytest.fail("Must not forward pointer-down while browser is active")

    class _IM:
        mouse_handler = _Mouse()

    from openfollow.configuration import AppConfig

    cfg = AppConfig()
    cfg.controller.mouse_enabled = True
    app = SimpleNamespace(_input_manager=_IM(), _config=cfg, _browser_active=True)
    app_modes.on_pointer_down(app, {"x": 1, "y": 1, "button": 1})


def test_on_pointer_up_skipped_while_browser_active() -> None:

    class _Mouse:
        def on_pointer_up(self, *_):
            pytest.fail("Must not forward pointer-up while browser is active")

    class _IM:
        mouse_handler = _Mouse()

    from openfollow.configuration import AppConfig

    cfg = AppConfig()
    cfg.controller.mouse_enabled = True
    app = SimpleNamespace(_input_manager=_IM(), _config=cfg, _browser_active=True)
    app_modes.on_pointer_up(app, {"x": 1, "y": 1, "button": 1})


# ---------------------------------------------------------------------------
# confirm_iface_selection + button-detection completion paths
# ---------------------------------------------------------------------------


def test_confirm_iface_selection_with_empty_list_clears_active_flag() -> None:
    app = SimpleNamespace(
        _available_interfaces=[],
        _selected_iface_index=0,
        _iface_selection_active=True,
    )
    app._restart_app = lambda: pytest.fail("must not restart with empty list")
    app_modes.confirm_iface_selection(app)
    assert app._iface_selection_active is False


def test_enter_button_detection_sets_web_server_active_flag(monkeypatch) -> None:
    flags: list[bool] = []

    class _FakeWizard:
        def __init__(self, *, app):
            pass

    monkeypatch.setattr(
        "openfollow.input.button_detection.ButtonDetectionWizard",
        _FakeWizard,
    )

    class _Gamepad:
        joysticks = {0: object()}

    class _IM:
        gamepad_handler = _Gamepad()

    class _WebServer:
        def set_button_detection_active(self, value):
            flags.append(value)

    app = SimpleNamespace(
        _input_manager=_IM(),
        _button_detection=None,
        _web_server=_WebServer(),
    )
    app_modes.enter_button_detection(app)

    assert flags == [True]
    assert isinstance(app._button_detection, _FakeWizard)


def test_enter_button_detection_handles_no_web_server() -> None:

    class _Gamepad:
        joysticks = {0: object()}

    class _IM:
        gamepad_handler = _Gamepad()

    class _FakeWizard:
        def __init__(self, *, app):
            pass

    import openfollow.input.button_detection as bd_module

    saved_cls = bd_module.ButtonDetectionWizard
    bd_module.ButtonDetectionWizard = _FakeWizard
    try:
        app = SimpleNamespace(
            _input_manager=_IM(),
            _button_detection=None,
            _web_server=None,
        )
        app_modes.enter_button_detection(app)
    finally:
        bd_module.ButtonDetectionWizard = saved_cls

    assert isinstance(app._button_detection, _FakeWizard)


def test_process_button_detection_completion_applies_results(tmp_path) -> None:
    """When the wizard completes successfully, the result map is applied to
    `controller.*`, `__post_init__` re-runs validation, the config is
    saved to disk, the gamepad handler reloads, and the web flag clears."""
    from openfollow.configuration import AppConfig, save_config

    cfg_path = tmp_path / "cfg.toml"
    save_config(AppConfig(), str(cfg_path))

    apply_called: list[bool] = []
    web_flags: list[bool] = []

    class _KB:
        keys: set[str] = set()

    class _Gamepad:
        def apply_config(self):
            apply_called.append(True)

    class _IM:
        keyboard_handler = _KB()
        gamepad_handler = _Gamepad()

    class _Wizard:
        is_done = True
        results = {"a": "A"}
        controller_name = "Test Controller"
        controller_guid = "test-guid"

        def poll(self): ...

        def cancel(self): ...

        def build_detection_map(self):
            return {"map_a": "A"}

        def build_raw_index_map(self):
            return {"A": 0}

        def should_swap_triggers(self):
            return True

    class _WebServer:
        def set_button_detection_active(self, value):
            web_flags.append(value)

    cfg = AppConfig()
    app = SimpleNamespace(
        _config=cfg,
        _config_path=str(cfg_path),
        _config_mtime=0,
        _input_manager=_IM(),
        _button_detection=_Wizard(),
        _web_server=_WebServer(),
    )
    app._get_config_mtime = lambda: 1

    app_modes.process_button_detection(app)

    assert app._button_detection is None
    assert apply_called == [True]
    assert web_flags == [False]
    # Detection map was applied to the controller config.
    assert cfg.controller.map_a == "A"
    assert cfg.controller.swap_triggers is True
    assert cfg.controller.mapped_controller_name == "Test Controller"
    assert cfg.controller.mapped_controller_guid == "test-guid"


def test_process_button_detection_completion_with_no_results_clears_wizard_only() -> None:
    """`is_done=True` with empty `results` is the cancel path – clear the
    wizard reference and the web flag, but DON'T touch the config."""
    from openfollow.configuration import AppConfig

    web_flags: list[bool] = []

    class _KB:
        keys: set[str] = set()

    class _IM:
        keyboard_handler = _KB()

    class _Wizard:
        is_done = True
        results = {}

        def poll(self): ...

    class _WebServer:
        def set_button_detection_active(self, value):
            web_flags.append(value)

    cfg = AppConfig()
    original_swap = cfg.controller.swap_triggers
    app = SimpleNamespace(
        _config=cfg,
        _input_manager=_IM(),
        _button_detection=_Wizard(),
        _web_server=_WebServer(),
    )

    app_modes.process_button_detection(app)
    assert app._button_detection is None
    assert web_flags == [False]
    assert cfg.controller.swap_triggers == original_swap


def test_process_button_detection_escape_cancels_with_web_flag_clear() -> None:
    """Escape during the wizard cancels and clears the web-server badge.
    The keyboard handler stores the named key as ``"Escape"`` per
    ``KeyboardHandler.POLLED_KEYS``; the lowercase form would never
    appear at runtime."""
    from openfollow.configuration import AppConfig

    web_flags: list[bool] = []
    cancels: list[bool] = []

    class _KB:
        keys: set[str] = {"Escape"}

    class _IM:
        keyboard_handler = _KB()

    class _Wizard:
        is_done = False
        results = {}

        def poll(self): ...

        def cancel(self):
            cancels.append(True)

    class _WebServer:
        def set_button_detection_active(self, value):
            web_flags.append(value)

    app = SimpleNamespace(
        _config=AppConfig(),
        _input_manager=_IM(),
        _button_detection=_Wizard(),
        _web_server=_WebServer(),
    )
    app._enter_settings_menu = lambda *, banner="": None
    app_modes.process_button_detection(app)

    assert app._button_detection is None
    assert cancels == [True]
    assert web_flags == [False]


def test_enter_settings_menu_when_not_active_opens_at_index_zero() -> None:
    app = SimpleNamespace(_settings_menu_active=False, _settings_menu_index=42)
    app_modes.enter_settings_menu(app)
    assert app._settings_menu_active is True
    assert app._settings_menu_index == 0


def test_enter_iface_selection_falls_back_to_zero_when_current_not_in_list(monkeypatch) -> None:
    """When persisted iface is unavailable, cursor defaults to auto-detect entry."""
    from openfollow.configuration import AppConfig

    monkeypatch.setattr(
        "openfollow.net_utils.list_iface_ipv4",
        lambda: [("en0", "10.0.0.5"), ("eth0", "10.0.0.6")],
    )
    cfg = AppConfig()
    cfg.psn_source_iface = "ghost0"  # not in the list above
    app = SimpleNamespace(
        _config=cfg,
        _available_interfaces=[],
        _selected_iface_index=99,
        _iface_selection_active=False,
        _source_type_selection_active=False,
        _url_editor_active=False,
        _field_choice_active=False,
        _browser_active=False,
    )
    app_modes.enter_iface_selection(app)
    assert app._available_interfaces == ["", "en0", "eth0"]
    assert app._selected_iface_index == 0
    assert app._iface_selection_active is True


def test_normalize_key_passes_multi_char_keys_through_unchanged() -> None:
    """Single-char alpha keys are lower-cased for consistent layout-aware
    lookup; named keys (Enter, ArrowUp, etc.) are preserved verbatim."""
    assert app_modes.normalize_key("a") == "a"
    assert app_modes.normalize_key("A") == "a"
    assert app_modes.normalize_key("Enter") == "Enter"
    assert app_modes.normalize_key("ArrowUp") == "ArrowUp"


def test_process_iface_selection_down_pressed_clamps_at_last_index() -> None:

    class _Gamepad:
        def read_source_selection_input(self):
            return _SrcInput(down=True)

    class _IM:
        gamepad_handler = _Gamepad()

    app = SimpleNamespace(
        _input_manager=_IM(),
        _available_interfaces=["", "a", "b"],
        _selected_iface_index=2,  # already at last
        _iface_selection_active=True,
    )
    app._confirm_iface_selection = lambda: pytest.fail("must not confirm on down-only")
    app_modes.process_iface_selection_input(app)
    assert app._selected_iface_index == 2  # clamped, not 3


def test_handle_key_press_main_mode_toggle_zones_persists_overlay(tmp_path) -> None:
    """The main-mode `key_toggle_zones` arm flips
    `trigger_zones.show_overlay` and persists. Distinct from the gamepad
    path tested in process_input."""
    from openfollow.configuration import AppConfig, save_config

    cfg_path = tmp_path / "cfg.toml"
    save_config(AppConfig(), str(cfg_path))

    cfg = AppConfig()
    cfg.trigger_zones.show_overlay = False
    cfg.controller.key_toggle_zones = "z"
    cfg.controller.key_next_marker = ""
    cfg.controller.key_prev_marker = ""
    cfg.controller.key_reset = ""
    cfg.controller.key_toggle_help = ""
    cfg.controller.key_speed_down = ""
    cfg.controller.key_speed_up = ""

    app = SimpleNamespace(
        _config=cfg,
        _config_path=str(cfg_path),
        _button_detection=None,
        _settings_menu_active=False,
        _video_receiver=None,
        _iface_selection_active=False,
        _source_type_selection_active=False,
        _url_editor_active=False,
        _field_choice_active=False,
        _browser_active=False,
    )
    app_modes.handle_key_press(app, "z")
    assert cfg.trigger_zones.show_overlay is True


def test_exit_button_detection_with_web_server_clears_active_flag() -> None:
    web_flags: list[bool] = []
    cancels: list[bool] = []

    class _Wizard:
        def cancel(self):
            cancels.append(True)

    class _WebServer:
        def set_button_detection_active(self, value):
            web_flags.append(value)

    app = SimpleNamespace(_button_detection=_Wizard(), _web_server=_WebServer())
    app_modes.exit_button_detection(app)
    assert app._button_detection is None
    assert cancels == [True]
    assert web_flags == [False]


# ---------------------------------------------------------------------------
# Exit partial branches across app_modes:
# no-op exits when input flags are all False, web_server is None, etc.
# ---------------------------------------------------------------------------


def test_process_source_selection_input_all_flags_false_is_noop() -> None:
    """Polling source-selection input with no buttons pressed exits
    cleanly – covers the 78→80 / 80→82 / 82→84 / 84→exit chain."""
    app = _make_src_selection_app(_SrcInput(), [])
    app_modes.process_source_selection_input(app)


def test_process_settings_menu_input_neither_confirm_nor_cancel_is_noop() -> None:

    class _Inp:
        up_pressed = False
        down_pressed = False
        confirm_pressed = False
        cancel_pressed = False

    class _Gamepad:
        def read_settings_menu_input(self):
            return _Inp()

    class _IM:
        gamepad_handler = _Gamepad()

    app = SimpleNamespace(_input_manager=_IM())
    app_modes.process_settings_menu_input(app)


def test_exit_button_detection_without_web_server_clears_wizard() -> None:
    """``exit_button_detection`` with ``_web_server=None`` still clears
    the wizard – covers 670→exit partial branch."""
    cancels: list[bool] = []

    class _Wizard:
        def cancel(self):
            cancels.append(True)

    app = SimpleNamespace(_button_detection=_Wizard(), _web_server=None)
    app_modes.exit_button_detection(app)
    assert app._button_detection is None
    assert cancels == [True]


# ---------------------------------------------------------------------------
# Strict-typing guards: early-returns when required runtime references are None
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------


def test_process_source_selection_input_noop_when_input_manager_none() -> None:
    """Strict-typing guard: with no InputManager wired up yet, the
    function returns immediately without touching ``_video_receiver``."""
    app = SimpleNamespace(_input_manager=None, _video_receiver=object())
    app_modes.process_source_selection_input(app)  # must not raise


def test_process_source_selection_input_noop_when_video_receiver_none() -> None:
    """Strict-typing guard: source selection requires a video receiver.
    With it None, return without consulting the gamepad."""

    class _Gamepad:
        def read_source_selection_input(self):
            raise AssertionError("gamepad must not be polled when receiver is None")

    class _IM:
        gamepad_handler = _Gamepad()

    app = SimpleNamespace(_input_manager=_IM(), _video_receiver=None)
    app_modes.process_source_selection_input(app)  # must not raise


def test_process_iface_selection_input_noop_when_input_manager_none() -> None:
    app = SimpleNamespace(_input_manager=None)
    app_modes.process_iface_selection_input(app)


def test_process_settings_menu_input_noop_when_input_manager_none() -> None:
    app = SimpleNamespace(_input_manager=None)
    app_modes.process_settings_menu_input(app)


def test_cycle_marker_with_no_selection_advances_forward() -> None:
    app = SimpleNamespace(_controlled_ids=[10, 20, 30], _selected_id=None)
    app_modes.cycle_marker(app, +1)
    assert app._selected_id == 10


def test_cycle_marker_with_no_selection_advances_backward() -> None:
    app = SimpleNamespace(_controlled_ids=[10, 20, 30], _selected_id=None)
    app_modes.cycle_marker(app, -1)
    assert app._selected_id == 30


def test_process_button_detection_noop_when_input_manager_none() -> None:
    """Wizard is normally only constructable when ``_input_manager`` is
    non-None (``enter_button_detection`` enforces this); the defensive
    early-return covers the case where the wizard somehow outlived the
    input manager teardown."""

    class _Wizard:
        is_done = False
        results = None

        def poll(self):  # pragma: no cover – mustn't be reached
            raise AssertionError("wizard.poll must not run when input_manager is None")

    app = SimpleNamespace(_button_detection=_Wizard(), _input_manager=None)
    app_modes.process_button_detection(app)


# --------------------------------------------------------------------------- #
# About screen
# --------------------------------------------------------------------------- #


def test_settings_menu_confirm_about_enters_about_screen() -> None:
    """The About row opens the read-only About screen and closes the menu."""
    app = _make_settings_menu_app(has_controller=True, has_source=True, menu_index=5)
    app._settings_menu_active = True
    app._settings_menu_banner = ""
    app._about_active = False
    app_modes._settings_menu_confirm(app)
    assert app._about_active is True
    assert app._settings_menu_active is False


def test_enter_about_sets_flag() -> None:
    app = SimpleNamespace(_about_active=False)
    app_modes.enter_about(app)
    assert app._about_active is True


def test_exit_about_clears_flag_and_reopens_settings() -> None:
    reopened: list[bool] = []
    app = SimpleNamespace(_about_active=True)
    app._enter_settings_menu = lambda *_a, **_k: reopened.append(True)
    app_modes.exit_about(app)
    assert app._about_active is False
    assert reopened == [True]


def _about_input_app(*, confirm: bool, cancel: bool):
    class _MenuInput:
        confirm_pressed = confirm
        cancel_pressed = cancel

    class _Gamepad:
        def read_settings_menu_input(self):
            return _MenuInput()

    class _IM:
        gamepad_handler = _Gamepad()

    return SimpleNamespace(_input_manager=_IM(), _about_active=True)


@pytest.mark.parametrize("confirm,cancel", [(True, False), (False, True)])
def test_process_about_input_confirm_or_cancel_exits(confirm, cancel) -> None:
    exits: list[bool] = []
    app = _about_input_app(confirm=confirm, cancel=cancel)
    saved = app_modes.exit_about
    try:
        app_modes.exit_about = lambda _a: exits.append(True)
        app_modes.process_about_input(app)
    finally:
        app_modes.exit_about = saved
    assert exits == [True]


def test_process_about_input_no_press_is_noop() -> None:
    exits: list[bool] = []
    app = _about_input_app(confirm=False, cancel=False)
    saved = app_modes.exit_about
    try:
        app_modes.exit_about = lambda _a: exits.append(True)
        app_modes.process_about_input(app)
    finally:
        app_modes.exit_about = saved
    assert exits == []


def test_process_about_input_no_input_manager_is_noop() -> None:
    app = SimpleNamespace(_input_manager=None, _about_active=True)
    app_modes.process_about_input(app)  # must not raise


def test_process_input_routes_to_about_when_active() -> None:
    called: list[bool] = []
    app = SimpleNamespace(
        _input_manager=SimpleNamespace(
            keyboard_handler=SimpleNamespace(keys=set()),
            gamepad_handler=object(),
        ),
        _button_detection=None,
        _settings_menu_active=False,
        _about_active=True,
    )
    saved = app_modes.process_about_input
    try:
        app_modes.process_about_input = lambda _a: called.append(True)
        app_modes.process_input(app, 0.016)
    finally:
        app_modes.process_about_input = saved
    assert called == [True]


def test_process_input_clears_keyboard_on_return_from_modal() -> None:
    """Returning to direct marker control after a modal drops any key still
    tracked as held (one-shot clear) so it can't drift the marker now."""
    from openfollow.configuration import AppConfig
    from openfollow.input.gamepad import GamepadUpdate

    cleared: list[bool] = []

    class _KB:
        keys: set[str] = set()

        def clear(self) -> None:
            cleared.append(True)

    class _IM:
        keyboard_handler = _KB()

        def update(self, _dt):  # noqa: ANN001
            return GamepadUpdate()

    cfg = AppConfig()
    app = SimpleNamespace(
        _input_manager=_IM(),
        _button_detection=None,
        _settings_menu_active=False,
        _video_receiver=None,
        _iface_selection_active=False,
        _source_type_selection_active=False,
        _url_editor_active=False,
        _field_choice_active=False,
        _browser_active=False,
        _config=cfg,
        _settings_key_pressed=False,
        _show_hud_help=False,
        _controlled_ids=[1],
        _selected_id=1,
        _marker_control_suspended=True,  # just exited a modal
    )
    app_modes.process_input(app, 0.016)
    assert cleared == [True]
    assert app._marker_control_suspended is False


def test_process_input_modal_entry_sets_suspended_without_clearing() -> None:
    """Entering a modal latches the suspended flag but does NOT clear – the
    modal's own keyboard input must keep working."""
    cleared: list[bool] = []

    class _KB:
        keys: set[str] = set()

        def clear(self) -> None:
            cleared.append(True)

    class _IM:
        keyboard_handler = _KB()

    app = SimpleNamespace(
        _input_manager=_IM(),
        _button_detection=None,
        _settings_menu_active=True,  # modal active
        _marker_control_suspended=False,
    )
    app._process_settings_menu_input = lambda: None
    app_modes.process_input(app, 0.016)
    assert cleared == []
    assert app._marker_control_suspended is True
