# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for ``openfollow.runtime.app_modes``: speed adjustment, key
normalization, the on-screen Settings menu, and the video source-type /
URL / field-choice picker state machines, plus legacy-shortcut regression
guards."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from openfollow.runtime.app_modes import (
    adjust_move_speed,
    build_settings_menu_items,
    enter_settings_menu,
    exit_settings_menu,
    handle_key_press,
    normalize_key,
)

pytestmark = pytest.mark.unit


class TestNormalizeKey:
    def test_lowercase_single_char(self) -> None:
        assert normalize_key("A") == "a"
        assert normalize_key("Z") == "z"

    def test_already_lowercase(self) -> None:
        assert normalize_key("a") == "a"

    def test_digit_unchanged(self) -> None:
        assert normalize_key("5") == "5"

    def test_multi_char_unchanged(self) -> None:
        assert normalize_key("ArrowUp") == "ArrowUp"
        assert normalize_key("Enter") == "Enter"
        assert normalize_key("Escape") == "Escape"

    def test_empty_string(self) -> None:
        assert normalize_key("") == ""


class TestAdjustMoveSpeed:
    def _make_app(
        self,
        move_speed: float = 1.0,
        min_speed: float = 0.1,
        max_speed: float = 20.0,
        selected_id: int | None = 1,
    ) -> object:
        # Fake mirrors adjust_move_speed contract: per-marker dict keyed by marker_id.
        class FakeMarkerConfig:
            def __init__(self):
                self.move_speed = move_speed
                self.min_speed = min_speed
                self.max_speed = max_speed

        class FakeConfig:
            def __init__(self):
                self.marker = FakeMarkerConfig()
                self.marker_move_speeds: dict[int, float] = {}

        class FakeApp:
            def __init__(self):
                self._config = FakeConfig()
                self._selected_id = selected_id
                self._speed_key_last_t: dict[int, float] = {}
                self._speed_key_streak: dict[int, int] = {}
                self._speed_key_last_dir: dict[int, int] = {}
                self._marker_speeds_dirty = False
                self._marker_speeds_dirty_since = 0.0
                # Seed the per-marker entry to the test's starting speed so
                # the first ``adjust_move_speed`` call has a defined base.
                if selected_id is not None:
                    self._config.marker_move_speeds[selected_id] = move_speed

            def get_marker_move_speed(self, marker_id: int | None) -> float:
                if marker_id is None:
                    return self._config.marker.move_speed
                return self._config.marker_move_speeds.get(
                    marker_id,
                    self._config.marker.move_speed,
                )

        return FakeApp()

    def test_increase_speed(self) -> None:
        app = self._make_app(move_speed=1.0)
        adjust_move_speed(app, +1)
        assert app._config.marker_move_speeds[1] == pytest.approx(1.1)

    def test_decrease_speed(self) -> None:
        app = self._make_app(move_speed=1.0)
        adjust_move_speed(app, -1)
        assert app._config.marker_move_speeds[1] == pytest.approx(0.9)

    def test_speed_does_not_go_below_min_speed(self) -> None:
        app = self._make_app(move_speed=0.1, min_speed=0.1)
        adjust_move_speed(app, -1)
        assert app._config.marker_move_speeds[1] == pytest.approx(0.1)

    def test_speed_does_not_exceed_max_speed(self) -> None:
        app = self._make_app(move_speed=3.0, max_speed=3.0)
        adjust_move_speed(app, +1)
        assert app._config.marker_move_speeds[1] == pytest.approx(3.0)

    def test_custom_min_speed_is_respected(self) -> None:
        app = self._make_app(move_speed=1.0, min_speed=1.0)
        adjust_move_speed(app, -1)
        assert app._config.marker_move_speeds[1] == pytest.approx(1.0)

    def test_custom_max_speed_is_respected(self) -> None:
        app = self._make_app(move_speed=2.0, max_speed=2.0)
        adjust_move_speed(app, +1)
        assert app._config.marker_move_speeds[1] == pytest.approx(2.0)

    def test_base_step_is_flat_regardless_of_speed(self) -> None:
        # The per-press base step is a flat 0.1 m/s at every speed – no
        # speed-tiered 0.2/0.4 steps (the streak multiplier still applies).
        for start in (1.0, 5.0, 7.0):
            app = self._make_app(move_speed=start)
            adjust_move_speed(app, +1)
            assert app._config.marker_move_speeds[1] == pytest.approx(start + 0.1)

    def test_streak_acceleration(self, monkeypatch) -> None:
        app = self._make_app(move_speed=1.0)
        # Simulate rapid key presses (within 0.75s)
        times = iter([10.0, 10.1, 10.2, 10.3, 10.4, 10.5])
        monkeypatch.setattr(time, "monotonic", lambda: next(times))

        for _ in range(5):
            adjust_move_speed(app, +1)
        # After 5 rapid presses, streak should be >= 5 → multiplier = 3
        assert app._speed_key_streak[1] >= 4

    def test_streak_resets_after_gap(self, monkeypatch) -> None:
        app = self._make_app(move_speed=1.0)
        times = iter([10.0, 11.0])  # >0.75s gap
        monkeypatch.setattr(time, "monotonic", lambda: next(times))

        adjust_move_speed(app, +1)
        adjust_move_speed(app, +1)
        assert app._speed_key_streak[1] == 0

    def test_streak_resets_on_direction_reversal(self, monkeypatch) -> None:
        app = self._make_app(move_speed=1.0)
        # Twelve rapid up-presses build the streak past the 8x threshold, then
        # one rapid down-press reverses direction.
        times = iter([10.0 + 0.1 * i for i in range(13)])
        monkeypatch.setattr(time, "monotonic", lambda: next(times))

        for _ in range(12):
            adjust_move_speed(app, +1)
        assert app._speed_key_streak[1] >= 10  # 8x multiplier in effect
        high_speed = app._config.marker_move_speeds[1]

        adjust_move_speed(app, -1)
        # Reversal clears the streak, so the down-press uses the base step only.
        assert app._speed_key_streak[1] == 0
        # Flat 0.1 base step; the single tic must not jump by the 8x multiplier.
        assert app._config.marker_move_speeds[1] == pytest.approx(high_speed - 0.1)

    def test_no_selection_is_noop(self) -> None:
        """With no selected marker, the call short-circuits without changes."""
        app = self._make_app(move_speed=1.0, selected_id=None)
        adjust_move_speed(app, +1)
        assert app._config.marker_move_speeds == {}
        assert app._speed_key_streak == {}

    def test_marks_dirty_with_edit_timestamp(self, monkeypatch) -> None:
        """A speed edit marks the config dirty and stamps the edit time so the
        housekeeping loop can flush it after the settle window."""
        app = self._make_app(move_speed=1.0)
        monkeypatch.setattr(time, "monotonic", lambda: 123.0)
        adjust_move_speed(app, +1)
        assert app._marker_speeds_dirty is True
        assert app._marker_speeds_dirty_since == 123.0

    def test_no_selection_does_not_mark_dirty(self) -> None:
        """A no-op call (no marker resolved) must not mark the config dirty –
        there is nothing to persist."""
        app = self._make_app(move_speed=1.0, selected_id=None)
        adjust_move_speed(app, +1)
        assert app._marker_speeds_dirty is False

    def test_explicit_marker_id_overrides_selection(self) -> None:
        """``marker_id`` passed by the gamepad bumper resolver overrides
        the keyboard-path fallback to ``_selected_id``. The selection's
        own entry (if any) stays untouched."""
        app = self._make_app(move_speed=1.0, selected_id=1)
        adjust_move_speed(app, +1, marker_id=7)
        # Selected marker's entry stays at its starting value
        assert app._config.marker_move_speeds[1] == pytest.approx(1.0)
        # Explicit marker received the bump
        assert app._config.marker_move_speeds[7] == pytest.approx(1.1)

    def test_two_markers_independent(self, monkeypatch) -> None:
        app = self._make_app(move_speed=1.0, selected_id=None)
        app._config.marker_move_speeds[1] = 1.0
        app._config.marker_move_speeds[2] = 1.0
        # 12 rapid (< 0.75s apart) same-direction presses on marker 1 + 1 on
        # marker 2 = 13 timestamps.
        times = iter([10.0 + 0.1 * i for i in range(13)])
        monkeypatch.setattr(time, "monotonic", lambda: next(times))

        for _ in range(12):
            adjust_move_speed(app, +1, marker_id=1)
        assert app._speed_key_streak[1] >= 10  # marker 1 reached the 8x tier
        speed1_before = app._config.marker_move_speeds[1]

        adjust_move_speed(app, +1, marker_id=2)
        # Marker 2 steps at 1x off its own fresh streak, not marker 1's 8x.
        assert app._config.marker_move_speeds[2] == pytest.approx(1.1)
        assert app._speed_key_streak[2] == 0
        # Marker 1's streak and value are untouched by the marker-2 press.
        assert app._speed_key_streak[1] >= 10
        assert app._config.marker_move_speeds[1] == pytest.approx(speed1_before)


class TestSettingsMenu:
    def _make_app(
        self,
        *,
        has_controller: bool = True,
        has_source_selection: bool = True,
    ) -> object:
        from openfollow.configuration import AppConfig

        class FakeGamepadHandler:
            def __init__(self) -> None:
                self.joysticks = {0: object()} if has_controller else {}

        class FakeInputManager:
            def __init__(self) -> None:
                self.gamepad_handler = FakeGamepadHandler()

        _has_source_selection = has_source_selection

        class FakeVideoReceiver:
            has_source_selection = _has_source_selection

        class FakeCanvas:
            def __init__(self) -> None:
                self.closed = False

            def close(self) -> None:
                self.closed = True

        class FakeApp:
            def __init__(self) -> None:
                self._config = AppConfig()
                self._settings_menu_active = False
                self._settings_menu_index = 0
                self._settings_menu_banner = ""
                self._input_manager = FakeInputManager()
                self._video_receiver = FakeVideoReceiver()
                self._canvas = FakeCanvas()
                self._button_detection = None
                self._iface_selection_active = False
                self._source_type_selection_active = False
                self._available_source_types: list[tuple[str, str]] = []
                self._selected_source_type_index = 0
                self._url_editor_active = False
                self._field_choice_active = False
                self._url_editor_field_name = ""
                self._url_editor_field_label = ""
                self._url_editor_value = ""
                self._url_editor_banner = ""
                self._url_editor_revert_type = ""
                self._browser_active = False
                self._browser_overlay = None
                self._iface_entered = False
                self._source_entered = False
                self._source_type_entered = False
                self._url_editor_entered = False
                self._browser_entered = False
                self._button_detection_entered = False
                self._restart_called = False
                # Network submenu state attrs.
                self._pi_network_active = False
                self._pi_network_index = 0
                self._pi_network_interfaces: list = []
                self._pi_network_active_iface = ""
                self._pi_network_state_cache = None
                self._pi_network_pending_config = None
                self._pi_network_iface_picker_active = False
                self._pi_network_iface_picker_index = 0
                self._pi_network_method_picker_active = False
                self._pi_network_method_picker_index = 0
                self._pi_network_field_edit_active = False
                self._pi_network_field_name = ""
                self._pi_network_field_value = ""
                self._pi_network_banner = ""
                self._pi_network_busy = False

            def _enter_iface_selection(self) -> None:
                self._iface_entered = True

            def _enter_source_selection(self) -> None:
                self._source_entered = True

            def _enter_source_type_selection(self) -> None:
                self._source_type_entered = True

            def _enter_url_editor(self, *, banner: str = "", revert_type: str = "") -> None:  # noqa: ARG002
                self._url_editor_entered = True

            def _enter_browser(self) -> None:
                self._browser_entered = True

            def _enter_button_detection(self) -> None:
                self._button_detection_entered = True

            def _restart_app(self) -> None:
                self._restart_called = True

            def _enter_settings_menu(self, *, banner: str = "") -> None:
                from openfollow.runtime.app_modes import enter_settings_menu

                enter_settings_menu(self, banner=banner)

        return FakeApp()

    def test_build_items_disables_button_detection_without_controller(self) -> None:
        app = self._make_app(has_controller=False)
        labels, enabled, _reasons = build_settings_menu_items(app)
        assert "Button Detection" in labels
        assert enabled[labels.index("Button Detection")] is False

    def test_enter_and_exit(self) -> None:
        app = self._make_app()
        enter_settings_menu(app)
        assert app._settings_menu_active is True
        assert app._settings_menu_index == 0
        assert app._settings_menu_banner == ""
        exit_settings_menu(app)
        assert app._settings_menu_active is False

    def test_enter_with_banner_stores_banner(self) -> None:
        """The startup auto-open path passes a banner explaining why
        the menu opened (e.g. unreachable network interface). The
        banner is stored on the app for the draw pass to surface."""
        app = self._make_app()
        enter_settings_menu(app, banner="Configured interface unavailable.")
        assert app._settings_menu_active is True
        assert app._settings_menu_banner == "Configured interface unavailable."

    def test_exit_clears_banner(self) -> None:
        app = self._make_app()
        enter_settings_menu(app, banner="X")
        exit_settings_menu(app)
        assert app._settings_menu_banner == ""

    def test_key_navigation_and_confirm_network(self) -> None:
        """Default selection is the Network entry. Enter opens the
        Network screen directly."""
        app = self._make_app()
        enter_settings_menu(app)
        assert app._settings_menu_index == 0
        handle_key_press(app, "Enter")
        assert app._pi_network_active is True
        assert app._iface_entered is False
        assert app._settings_menu_active is False

    def test_key_arrow_down_skips_disabled_items(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import sys as _sys

        from openfollow.runtime import webkit_browser

        # Linux-only gating: Open Web UI (3) disables without the WebKit2 typelib.
        # macOS opens the default browser, so its row is always enabled there.
        monkeypatch.setattr(webkit_browser, "AVAILABLE", False)
        monkeypatch.setattr(_sys, "platform", "linux")
        app = self._make_app(has_controller=False, has_source_selection=False)
        enter_settings_menu(app)
        # Menu order: 0 Network, 1 Change Video Source, 2 Button Detection
        # (disabled), 3 Open Web UI (disabled), 4 Restart.
        # Park on Change
        # Video Source (1), ArrowDown skips 2 + 3 to Restart (4).
        app._settings_menu_index = 1
        handle_key_press(app, "ArrowDown")
        assert app._settings_menu_index == 4

    def test_key_arrow_up_wraps_to_last_enabled_item(self) -> None:
        app = self._make_app()
        enter_settings_menu(app)
        assert app._settings_menu_index == 0
        handle_key_press(app, "ArrowUp")
        # 6 items total (Network, Change Video Source, Button Detection,
        # Open Web UI, Restart, About) – wrap to About at index 5.
        assert app._settings_menu_index == 5

    def test_key_escape_cancels_menu(self) -> None:
        app = self._make_app()
        enter_settings_menu(app)
        handle_key_press(app, "Escape")
        assert app._settings_menu_active is False

    def test_about_opens_from_menu_and_escape_returns(self) -> None:
        app = self._make_app()
        enter_settings_menu(app)
        app._settings_menu_index = 5  # About (last row)
        handle_key_press(app, "Enter")
        assert app._about_active is True
        assert app._settings_menu_active is False
        handle_key_press(app, "Escape")
        assert app._about_active is False
        assert app._settings_menu_active is True

    def test_about_enter_also_returns_to_settings(self) -> None:
        """Enter (not just Escape) backs out of the read-only About screen."""
        app = self._make_app()
        enter_settings_menu(app)
        app._settings_menu_index = 5
        handle_key_press(app, "Enter")  # open
        handle_key_press(app, "Enter")  # confirm/dismiss
        assert app._about_active is False
        assert app._settings_menu_active is True

    def test_about_ignores_other_keys(self) -> None:
        """A non-confirm/cancel key on the About screen is a no-op (stays open)."""
        app = self._make_app()
        app._about_active = True
        handle_key_press(app, "ArrowUp")
        assert app._about_active is True

    def test_confirm_restart_calls_restart(self) -> None:
        app = self._make_app()
        enter_settings_menu(app)
        app._settings_menu_index = 4  # Restart
        handle_key_press(app, "Enter")
        assert app._restart_called is True

    def test_confirm_open_web_ui_enters_browser(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Off macOS, confirming Open Web UI mounts the embedded overlay."""
        import sys as _sys

        from openfollow.runtime import webkit_browser

        monkeypatch.setattr(webkit_browser, "AVAILABLE", True)
        monkeypatch.setattr(_sys, "platform", "linux")
        app = self._make_app()
        enter_settings_menu(app)
        app._settings_menu_index = 3  # Open Web UI
        handle_key_press(app, "Enter")
        assert app._browser_entered is True
        assert app._settings_menu_active is False

    def test_confirm_open_web_ui_opens_default_browser_on_mac(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """On macOS, confirming Open Web UI launches the system default browser
        via ``open`` instead of the (unavailable) embedded overlay."""
        import sys as _sys

        from openfollow.runtime import app_modes

        monkeypatch.setattr(_sys, "platform", "darwin")
        captured: dict[str, list[str]] = {}
        monkeypatch.setattr(
            app_modes.subprocess,
            "run",
            lambda argv, **_kw: captured.__setitem__("argv", argv) or SimpleNamespace(returncode=0),
        )
        app = self._make_app()
        app._web_server = None
        enter_settings_menu(app)
        app._settings_menu_index = 3  # Open Web UI
        handle_key_press(app, "Enter")
        assert app._browser_entered is False
        assert app._settings_menu_active is False
        assert captured["argv"][0] == "open"
        assert captured["argv"][1].startswith("http://127.0.0.1")

    def test_confirm_network_opens_screen_directly(self) -> None:
        """Network entry opens the Network screen; legacy iface picker merged into it."""
        app = self._make_app()
        enter_settings_menu(app)
        app._settings_menu_index = 0  # Network
        handle_key_press(app, "Enter")
        assert app._pi_network_active is True
        assert app._iface_entered is False
        assert app._settings_menu_active is False

    def test_confirm_change_video_source_enters_picker(self) -> None:
        app = self._make_app()
        enter_settings_menu(app)
        app._settings_menu_index = 1  # Change Video Source
        handle_key_press(app, "Enter")
        assert app._source_type_entered is True
        assert app._settings_menu_active is False

    def test_confirm_button_detection_enters_wizard(self) -> None:
        app = self._make_app()
        enter_settings_menu(app)
        app._settings_menu_index = 2  # Button Detection
        handle_key_press(app, "Enter")
        assert app._button_detection_entered is True
        assert app._settings_menu_active is False

    def test_confirm_on_disabled_item_does_nothing(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openfollow.runtime import webkit_browser

        monkeypatch.setattr(webkit_browser, "AVAILABLE", True)
        app = self._make_app(has_controller=False)  # Button Detection disabled
        enter_settings_menu(app)
        app._settings_menu_index = 2  # Button Detection (disabled)
        handle_key_press(app, "Enter")
        assert app._button_detection_entered is False
        # Menu stays open so the user can pick something else.
        assert app._settings_menu_active is True


class TestSourceTypeSelection:
    """On-device Video Source Type switcher tests."""

    def _make_app(
        self,
        *,
        current_type: str = "ndi",
        rtsp_url: str = "",
        swap_raises: Exception | None = None,
    ) -> object:
        from openfollow.configuration import AppConfig

        cfg = AppConfig()
        cfg.video_source_type = current_type
        cfg.rtsp_url = rtsp_url

        swap_calls: list[str] = []

        class _Services:
            def swap_video(self, new_cfg) -> None:  # noqa: ANN001
                if swap_raises is not None:
                    raise swap_raises
                swap_calls.append(new_cfg.video_source_type)

        class _Gamepad:
            joysticks: dict[int, object] = {}

            def read_source_selection_input(self):  # noqa: ANN001
                return self._next  # type: ignore[attr-defined]

        class _IM:
            gamepad_handler = _Gamepad()

        class _Canvas:
            def close(self) -> None: ...

        class FakeApp:
            def __init__(self) -> None:
                self._config = cfg
                self._config_path = "/dev/null"
                self._config_mtime = 0.0
                self._runtime_services = _Services()
                self._input_manager = _IM()
                self._canvas = _Canvas()
                self._source_type_selection_active = False
                self._available_source_types: list[tuple[str, str]] = []
                self._selected_source_type_index = 0
                self._settings_menu_active = False
                self._settings_menu_index = 0
                self._settings_menu_banner = ""
                # ``handle_key_press`` reads these before the source-type
                # branch fires; tests park them in their disabled state
                # so the source-type guard is the only mode in play.
                self._button_detection = None
                self._video_receiver = None
                self._iface_selection_active = False
                self._url_editor_active = False
                self._field_choice_active = False
                self._url_editor_field_name = ""
                self._url_editor_field_label = ""
                self._url_editor_value = ""
                self._url_editor_banner = ""
                self._url_editor_revert_type = ""
                self._browser_active = False
                self._browser_overlay = None
                self._swap_calls = swap_calls
                self._save_calls: list[tuple[str, str]] = []
                # Routing-target recorders; _route_after_video_source_change calls
                # one of
                # ``_enter_url_editor`` / ``_enter_source_selection``
                # depending on the picked plugin's capabilities; tests
                # introspect these lists to verify the routing.
                self._url_editor_calls: list[bool] = []
                self._source_selection_calls: list[bool] = []

            def _get_config_mtime(self) -> float:
                return 1234.5

            def _enter_settings_menu(self, *, banner: str = "") -> None:
                self._settings_menu_active = True
                self._settings_menu_banner = banner

            def _enter_url_editor(
                self,
                *,
                banner: str = "",
                revert_type: str = "",
            ) -> None:  # noqa: ARG002
                self._url_editor_calls.append(True)

            def _enter_source_selection(self) -> None:
                self._source_selection_calls.append(True)

            def _confirm_source_type_selection(self) -> None:
                from openfollow.runtime.app_modes import (
                    confirm_source_type_selection,
                )

                confirm_source_type_selection(self)

            def _exit_source_type_selection(self) -> None:
                from openfollow.runtime.app_modes import (
                    exit_source_type_selection,
                )

                exit_source_type_selection(self)

        return FakeApp()

    def test_enter_lists_available_plugins_seeded_to_current(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openfollow.runtime import app_modes

        # Stub the registry so the test doesn't depend on platform plugin
        # availability (NDI on Linux dev boxes is flaky in CI).
        class _Cls:
            def __init__(self, name: str) -> None:
                self.display_name = name

        registry = {
            "rtsp": _Cls("RTSP"),
            "ndi": _Cls("NDI"),
            "testpattern": _Cls("Test Pattern"),
        }
        monkeypatch.setattr(
            "openfollow.video.inputs.get_available_registry",
            lambda: registry,
        )
        app = self._make_app(current_type="ndi")
        app_modes.enter_source_type_selection(app)
        assert app._source_type_selection_active is True
        # Sorted alphabetically by display name → NDI, RTSP, Test Pattern
        assert [iid for iid, _ in app._available_source_types] == [
            "ndi",
            "rtsp",
            "testpattern",
        ]
        # Cursor seeds to current type's index.
        assert app._selected_source_type_index == 0

    def test_enter_seeds_to_zero_when_current_type_unknown(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openfollow.runtime import app_modes

        class _Cls:
            display_name = "RTSP"

        monkeypatch.setattr(
            "openfollow.video.inputs.get_available_registry",
            lambda: {"rtsp": _Cls},
        )
        app = self._make_app(current_type="not_in_registry")
        app_modes.enter_source_type_selection(app)
        assert app._selected_source_type_index == 0

    def test_exit_clears_active_flag(self) -> None:
        from openfollow.runtime import app_modes

        app = self._make_app()
        app._source_type_selection_active = True
        app_modes.exit_source_type_selection(app)
        assert app._source_type_selection_active is False

    def test_confirm_no_change_skips_swap_but_still_routes(self) -> None:
        from openfollow.runtime import app_modes

        app = self._make_app(current_type="ndi")
        app._source_type_selection_active = True
        app._available_source_types = [("ndi", "NDI"), ("rtsp", "RTSP")]
        app._selected_source_type_index = 0  # Same as current
        app_modes.confirm_source_type_selection(app)
        assert app._swap_calls == []  # no redundant pipeline cycle
        assert app._source_type_selection_active is False
        # NDI has has_source_selection=True → source picker fires.
        assert app._source_selection_calls == [True]
        assert app._url_editor_calls == []

    def test_confirm_for_type_with_no_capabilities_exits_without_routing(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openfollow.runtime import app_modes
        from openfollow.video.inputs._base import (
            ConfigField,
            InputCapabilities,
        )

        class _BlankPlugin:
            display_name = "Blank"

            @classmethod
            def config_fields(cls):
                return [ConfigField("blank_width", int, 1920, "Width")]

            @classmethod
            def capabilities(cls):
                return InputCapabilities(has_source_selection=False)

        monkeypatch.setattr(
            "openfollow.video.inputs.get_input_class",
            lambda iid: _BlankPlugin if iid == "blank" else None,
        )
        monkeypatch.setattr(app_modes, "save_config", lambda *a, **kw: None)
        app = self._make_app(current_type="ndi")
        app._source_type_selection_active = True
        app._available_source_types = [("ndi", "NDI"), ("blank", "Blank")]
        app._selected_source_type_index = 1
        app_modes.confirm_source_type_selection(app)
        # Swap succeeded but neither editor nor source picker fired.
        assert app._swap_calls == ["blank"]
        assert app._url_editor_calls == []
        assert app._source_selection_calls == []

    def test_confirm_rtsp_routes_to_url_editor(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openfollow.runtime import app_modes

        monkeypatch.setattr(app_modes, "save_config", lambda *a, **kw: None)
        app = self._make_app(current_type="ndi")
        app._source_type_selection_active = True
        app._available_source_types = [("ndi", "NDI"), ("rtsp", "RTSP")]
        app._selected_source_type_index = 1
        # Pre-populate rtsp_url so swap_video doesn't auto-chain on empty.
        app._config.rtsp_url = "rtsp://10.0.0.5/stream"
        app_modes.confirm_source_type_selection(app)
        assert app._swap_calls == ["rtsp"]
        assert app._url_editor_calls == [True]
        assert app._source_selection_calls == []
        assert app._source_type_selection_active is False

    def test_confirm_empty_list_just_exits(self) -> None:
        from openfollow.runtime import app_modes

        app = self._make_app()
        app._source_type_selection_active = True
        app._available_source_types = []
        app_modes.confirm_source_type_selection(app)
        assert app._source_type_selection_active is False

    def test_confirm_ndi_routes_to_source_picker(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """NDI has has_source_selection=True → after a successful
        swap, the discovery-based source picker opens (NOT the URL
        editor, even though NDI also has a string field)."""
        from openfollow.runtime import app_modes

        monkeypatch.setattr(app_modes, "save_config", lambda *a, **kw: None)
        app = self._make_app(current_type="rtsp", rtsp_url="rtsp://x")
        app._source_type_selection_active = True
        app._available_source_types = [("rtsp", "RTSP"), ("ndi", "NDI")]
        app._selected_source_type_index = 1
        app_modes.confirm_source_type_selection(app)
        assert app._swap_calls == ["ndi"]
        assert app._source_selection_calls == [True]
        assert app._url_editor_calls == []

    def test_confirm_live_swaps_and_persists(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openfollow.runtime import app_modes

        saves: list = []
        monkeypatch.setattr(
            app_modes,
            "save_config",
            lambda cfg, path: saves.append((cfg.video_source_type, path)),
        )
        app = self._make_app(current_type="ndi", rtsp_url="rtsp://1.1.1.1")
        app._source_type_selection_active = True
        app._available_source_types = [("ndi", "NDI"), ("rtsp", "RTSP")]
        app._selected_source_type_index = 1  # Switch to RTSP
        app_modes.confirm_source_type_selection(app)
        assert app._swap_calls == ["rtsp"]
        assert saves == [("rtsp", "/dev/null")]
        assert app._config.video_source_type == "rtsp"
        assert app._source_type_selection_active is False
        assert app._settings_menu_active is False  # No banner on success
        assert app._config_mtime == 1234.5
        # Success routes to URL editor for operator verification/edit.
        assert app._url_editor_calls == [True]

    def test_confirm_rollback_on_swap_failure_reopens_settings_with_banner(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When swap_video raises for a reason OTHER than empty URL
        (e.g. server unreachable, malformed URL, plugin crash), the
        stored type reverts and the operator lands in the Settings
        menu with a banner. Empty-URL
        failures route to the URL editor instead (covered separately
        in TestSourceTypeAutoChainEditor)."""
        from openfollow.runtime import app_modes

        monkeypatch.setattr(
            app_modes,
            "save_config",
            lambda *a, **kw: pytest.fail("must not save on apply failure"),
        )
        # Pre-populate rtsp_url so the failure isn't the empty-URL
        # auto-chain shape – exercises the Settings-banner rollback.
        app = self._make_app(
            current_type="ndi",
            rtsp_url="rtsp://10.0.0.5/stream",
            swap_raises=ConnectionRefusedError("server unreachable"),
        )
        app._source_type_selection_active = True
        app._available_source_types = [("ndi", "NDI"), ("rtsp", "RTSP")]
        app._selected_source_type_index = 1  # RTSP
        app_modes.confirm_source_type_selection(app)
        assert app._config.video_source_type == "ndi"  # rolled back
        assert app._source_type_selection_active is False
        assert app._settings_menu_active is True
        assert "RTSP" in app._settings_menu_banner
        assert "server unreachable" in app._settings_menu_banner

    def test_keyboard_arrow_down_advances_cursor(self) -> None:
        from openfollow.runtime import app_modes

        app = self._make_app()
        app._source_type_selection_active = True
        app._available_source_types = [("a", "A"), ("b", "B"), ("c", "C")]
        app._selected_source_type_index = 0
        app_modes.handle_key_press(app, "ArrowDown")
        assert app._selected_source_type_index == 1

    def test_keyboard_arrow_up_at_top_clamps(self) -> None:
        from openfollow.runtime import app_modes

        app = self._make_app()
        app._source_type_selection_active = True
        app._available_source_types = [("a", "A"), ("b", "B")]
        app._selected_source_type_index = 0
        app_modes.handle_key_press(app, "ArrowUp")
        assert app._selected_source_type_index == 0

    def test_keyboard_escape_exits_picker(self) -> None:
        from openfollow.runtime import app_modes

        app = self._make_app()
        app._source_type_selection_active = True
        app._available_source_types = [("a", "A")]
        app_modes.handle_key_press(app, "Escape")
        assert app._source_type_selection_active is False

    def test_keyboard_enter_confirms(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openfollow.runtime import app_modes

        monkeypatch.setattr(app_modes, "save_config", lambda *a, **kw: None)
        app = self._make_app(current_type="ndi")
        app._source_type_selection_active = True
        app._available_source_types = [("ndi", "NDI"), ("rtsp", "RTSP")]
        app._selected_source_type_index = 1
        app_modes.handle_key_press(app, "Enter")
        assert app._swap_calls == ["rtsp"]
        assert app._source_type_selection_active is False

    def test_keyboard_no_op_when_list_empty(self) -> None:
        from openfollow.runtime import app_modes

        app = self._make_app()
        app._source_type_selection_active = True
        app._available_source_types = []
        # Must early-return without raising or mutating state.
        app_modes.handle_key_press(app, "ArrowDown")
        assert app._source_type_selection_active is True

    def test_gamepad_up_down_confirm_cancel(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Gamepad parity: read_source_selection_input drives nav for the
        source-type picker just like it does for the iface picker."""
        from openfollow.runtime import app_modes

        monkeypatch.setattr(app_modes, "save_config", lambda *a, **kw: None)

        class _Inp:
            def __init__(self, *, up=False, down=False, confirm=False, cancel=False) -> None:
                self.up_pressed = up
                self.down_pressed = down
                self.confirm_pressed = confirm
                self.cancel_pressed = cancel

        readings: list[object] = []

        class _Gamepad:
            def read_source_selection_input(self):
                return readings.pop(0)

        class _IM:
            gamepad_handler = _Gamepad()

        app = self._make_app(current_type="ndi")
        app._input_manager = _IM()
        app._source_type_selection_active = True
        app._available_source_types = [("ndi", "NDI"), ("rtsp", "RTSP")]
        app._selected_source_type_index = 0

        readings.append(_Inp(down=True))
        app_modes.process_source_type_selection_input(app)
        assert app._selected_source_type_index == 1

        readings.append(_Inp(up=True))
        app_modes.process_source_type_selection_input(app)
        assert app._selected_source_type_index == 0

        readings.append(_Inp(cancel=True))
        app_modes.process_source_type_selection_input(app)
        assert app._source_type_selection_active is False

    def test_gamepad_no_input_manager_no_ops(self) -> None:
        from openfollow.runtime import app_modes

        app = self._make_app()
        app._input_manager = None
        app._source_type_selection_active = True
        # Must return without raising.
        app_modes.process_source_type_selection_input(app)
        assert app._source_type_selection_active is True

    def test_gamepad_logs_and_returns_on_read_error(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        from openfollow.runtime import app_modes

        class _Gamepad:
            def read_source_selection_input(self):
                raise RuntimeError("controller disconnected")

        class _IM:
            gamepad_handler = _Gamepad()

        app = self._make_app()
        app._input_manager = _IM()
        app._source_type_selection_active = True
        with caplog.at_level("WARNING"):
            app_modes.process_source_type_selection_input(app)
        assert any("Source-type selection input error" in rec.message for rec in caplog.records)

    def test_gamepad_cancel_with_empty_list_still_closes_picker(self) -> None:
        from openfollow.runtime import app_modes

        class _Inp:
            up_pressed = False
            down_pressed = False
            confirm_pressed = False
            cancel_pressed = True

        class _Gamepad:
            def read_source_selection_input(self):
                return _Inp()

        class _IM:
            gamepad_handler = _Gamepad()

        app = self._make_app()
        app._input_manager = _IM()
        app._source_type_selection_active = True
        app._available_source_types = []
        app_modes.process_source_type_selection_input(app)
        assert app._source_type_selection_active is False

    def test_keyboard_unknown_key_in_picker_no_ops(self) -> None:
        from openfollow.runtime import app_modes

        app = self._make_app()
        app._source_type_selection_active = True
        app._available_source_types = [("ndi", "NDI")]
        app._selected_source_type_index = 0
        app_modes.handle_key_press(app, "x")
        assert app._source_type_selection_active is True
        assert app._selected_source_type_index == 0
        assert app._swap_calls == []

    def test_gamepad_empty_list_without_cancel_just_returns(self) -> None:
        from openfollow.runtime import app_modes

        class _Inp:
            up_pressed = False
            down_pressed = False
            confirm_pressed = False
            cancel_pressed = False

        class _Gamepad:
            def read_source_selection_input(self):
                return _Inp()

        class _IM:
            gamepad_handler = _Gamepad()

        app = self._make_app()
        app._input_manager = _IM()
        app._source_type_selection_active = True
        app._available_source_types = []
        app_modes.process_source_type_selection_input(app)
        # Mode stays active because cancel wasn't pressed.
        assert app._source_type_selection_active is True

    def test_process_input_routes_to_source_type_handler_when_active(self) -> None:
        from openfollow.configuration import AppConfig
        from openfollow.runtime import app_modes

        called: list[bool] = []

        class _KB:
            keys: set[str] = set()

        class _IM:
            keyboard_handler = _KB()

            def update(self, _dt):  # noqa: ANN001
                pytest.fail("must not reach main-mode update path")

        app = SimpleNamespace(
            _input_manager=_IM(),
            _button_detection=None,
            _settings_menu_active=False,
            _video_receiver=None,
            _iface_selection_active=False,
            _source_type_selection_active=True,
            _field_choice_active=False,
            _config=AppConfig(),
        )
        app._process_source_type_selection_input = lambda: called.append(True)
        app_modes.process_input(app, 0.01)
        assert called == [True]

    def test_gamepad_confirm_dispatches_to_app_method(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openfollow.runtime import app_modes

        monkeypatch.setattr(app_modes, "save_config", lambda *a, **kw: None)

        class _Inp:
            up_pressed = False
            down_pressed = False
            confirm_pressed = True
            cancel_pressed = False

        class _Gamepad:
            def read_source_selection_input(self):
                return _Inp()

        class _IM:
            gamepad_handler = _Gamepad()

        app = self._make_app(current_type="ndi")
        app._input_manager = _IM()
        app._source_type_selection_active = True
        app._available_source_types = [("ndi", "NDI"), ("rtsp", "RTSP")]
        app._selected_source_type_index = 1
        app_modes.process_source_type_selection_input(app)
        assert app._swap_calls == ["rtsp"]


class TestUrlEditor:
    """On-device text editor for active video plugin URL field."""

    def _make_app(
        self,
        *,
        video_source_type: str = "rtsp",
        rtsp_url: str = "",
        srt_host: str = "",
        ndi_source_name: str = "",
        swap_raises: Exception | None = None,
    ) -> object:
        from openfollow.configuration import AppConfig

        cfg = AppConfig()
        cfg.video_source_type = video_source_type
        cfg.rtsp_url = rtsp_url
        cfg.srt_host = srt_host
        cfg.ndi_source_name = ndi_source_name

        swap_calls: list[str] = []

        class _Services:
            def swap_video(self, new_cfg) -> None:  # noqa: ANN001
                if swap_raises is not None:
                    raise swap_raises
                swap_calls.append(new_cfg.video_source_type)

        class _Canvas:
            def close(self) -> None: ...

        class FakeApp:
            def __init__(self) -> None:
                self._config = cfg
                self._config_path = "/dev/null"
                self._config_mtime = 0.0
                self._runtime_services = _Services()
                self._canvas = _Canvas()
                self._url_editor_active = False
                self._field_choice_active = False
                self._url_editor_field_name = ""
                self._url_editor_field_label = ""
                self._url_editor_value = ""
                self._url_editor_banner = ""
                self._url_editor_revert_type = ""
                self._settings_menu_active = False
                self._settings_menu_index = 0
                self._settings_menu_banner = ""
                self._swap_calls = swap_calls
                self._button_detection = None
                self._iface_selection_active = False
                self._source_type_selection_active = False
                self._video_receiver = None
                self._input_manager = None

            def _get_config_mtime(self) -> float:
                return 1234.5

            def _confirm_url_editor(self) -> None:
                from openfollow.runtime.app_modes import confirm_url_editor

                confirm_url_editor(self)

            def _exit_url_editor(self) -> None:
                from openfollow.runtime.app_modes import exit_url_editor

                exit_url_editor(self)

            def _enter_settings_menu(self, *, banner: str = "") -> None:
                self._settings_menu_active = True
                self._settings_menu_banner = banner

        return FakeApp()

    def test_enter_picks_first_string_field_for_rtsp(self) -> None:
        from openfollow.runtime import app_modes

        app = self._make_app(video_source_type="rtsp", rtsp_url="rtsp://x")
        app_modes.enter_url_editor(app)
        assert app._url_editor_active is True
        assert app._url_editor_field_name == "rtsp_url"
        assert app._url_editor_field_label == "RTSP URL"
        assert app._url_editor_value == "rtsp://x"

    def test_enter_picks_first_string_field_for_ndi(self) -> None:
        from openfollow.runtime import app_modes

        app = self._make_app(
            video_source_type="ndi",
            ndi_source_name="STUDIO (CAM1)",
        )
        app_modes.enter_url_editor(app)
        assert app._url_editor_field_name == "ndi_source_name"
        assert app._url_editor_value == "STUDIO (CAM1)"

    def test_enter_no_op_when_plugin_unknown(self) -> None:
        from openfollow.runtime import app_modes

        app = self._make_app(video_source_type="bogus")
        app_modes.enter_url_editor(app)
        assert app._url_editor_active is False

    def test_enter_no_op_when_plugin_has_no_string_field(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openfollow.runtime import app_modes
        from openfollow.video.inputs._base import ConfigField

        class _IntOnly:
            @classmethod
            def config_fields(cls):
                return [
                    ConfigField("intish_w", int, 1920, "Width"),
                    ConfigField("intish_h", int, 1080, "Height"),
                ]

        monkeypatch.setattr(
            "openfollow.video.inputs.get_input_class",
            lambda iid: _IntOnly if iid == "intonly" else None,
        )
        app = self._make_app(video_source_type="intonly")
        app_modes.enter_url_editor(app)
        assert app._url_editor_active is False

    def test_enter_carries_banner_and_revert_type(self) -> None:
        from openfollow.runtime import app_modes

        app = self._make_app(video_source_type="rtsp")
        app_modes.enter_url_editor(
            app,
            banner="RTSP needs URL.",
            revert_type="ndi",
        )
        assert app._url_editor_banner == "RTSP needs URL."
        assert app._url_editor_revert_type == "ndi"

    def test_typing_appends_chars_to_value(self) -> None:
        from openfollow.runtime import app_modes

        app = self._make_app(video_source_type="rtsp")
        app_modes.enter_url_editor(app)
        for ch in "rtsp://1.2.3.4:554/stream":
            app_modes.handle_url_editor_key(app, ch)
        assert app._url_editor_value == "rtsp://1.2.3.4:554/stream"

    def test_backspace_deletes_last_char(self) -> None:
        from openfollow.runtime import app_modes

        app = self._make_app(video_source_type="rtsp", rtsp_url="abcd")
        app_modes.enter_url_editor(app)
        app_modes.handle_url_editor_key(app, "Backspace")
        assert app._url_editor_value == "abc"

    def test_backspace_on_empty_buffer_is_safe(self) -> None:
        from openfollow.runtime import app_modes

        app = self._make_app(video_source_type="rtsp")
        app_modes.enter_url_editor(app)
        app_modes.handle_url_editor_key(app, "Backspace")
        assert app._url_editor_value == ""

    def test_modifier_keys_dropped(self) -> None:
        from openfollow.runtime import app_modes

        app = self._make_app(video_source_type="rtsp")
        app_modes.enter_url_editor(app)
        for k in ("Shift", "Control", "Meta", "Alt", "ArrowUp", "Tab"):
            app_modes.handle_url_editor_key(app, k)
        assert app._url_editor_value == ""

    def test_escape_cancels_without_save(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openfollow.runtime import app_modes

        monkeypatch.setattr(
            app_modes,
            "save_config",
            lambda *a, **kw: pytest.fail("must not save on cancel"),
        )
        app = self._make_app(video_source_type="rtsp")
        app_modes.enter_url_editor(app)
        for ch in "rtsp://x":
            app_modes.handle_url_editor_key(app, ch)
        app_modes.handle_url_editor_key(app, "Escape")
        assert app._url_editor_active is False
        # Original config field stays empty (no setattr happened).
        assert app._config.rtsp_url == ""

    def test_enter_confirms_and_persists(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openfollow.runtime import app_modes

        saves: list = []
        monkeypatch.setattr(
            app_modes,
            "save_config",
            lambda cfg, path: saves.append((cfg.rtsp_url, path)),
        )
        app = self._make_app(video_source_type="rtsp")
        app_modes.enter_url_editor(app)
        for ch in "rtsp://1.1.1.1":
            app_modes.handle_url_editor_key(app, ch)
        app_modes.handle_url_editor_key(app, "Enter")
        assert app._config.rtsp_url == "rtsp://1.1.1.1"
        assert saves == [("rtsp://1.1.1.1", "/dev/null")]
        assert app._url_editor_active is False
        assert app._config_mtime == 1234.5

    def test_confirm_no_field_just_exits(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openfollow.runtime import app_modes

        monkeypatch.setattr(
            app_modes,
            "save_config",
            lambda *a, **kw: pytest.fail("must not save without field"),
        )
        app = self._make_app(video_source_type="rtsp")
        app._url_editor_active = True
        app._url_editor_field_name = ""
        app_modes.confirm_url_editor(app)
        assert app._url_editor_active is False

    def test_confirm_auto_chained_retries_swap_video(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openfollow.runtime import app_modes

        monkeypatch.setattr(app_modes, "save_config", lambda *a, **kw: None)
        app = self._make_app(video_source_type="rtsp")
        app_modes.enter_url_editor(app, revert_type="ndi")
        for ch in "rtsp://x":
            app_modes.handle_url_editor_key(app, ch)
        app_modes.handle_url_editor_key(app, "Enter")
        assert app._swap_calls == ["rtsp"]
        assert app._url_editor_active is False
        assert app._config.video_source_type == "rtsp"

    def test_confirm_auto_chained_swap_failure_reverts_type_and_opens_settings(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Auto-chain failure path: a still-broken URL surfaces a
        Settings-menu banner; the type rolls back to the prior plugin
        so the operator can pick again or fix in the web UI."""
        from openfollow.runtime import app_modes

        monkeypatch.setattr(app_modes, "save_config", lambda *a, **kw: None)
        app = self._make_app(
            video_source_type="rtsp",
            swap_raises=ValueError("malformed URL"),
        )
        app_modes.enter_url_editor(app, revert_type="ndi")
        for ch in "garbage":
            app_modes.handle_url_editor_key(app, ch)
        app_modes.handle_url_editor_key(app, "Enter")
        assert app._config.video_source_type == "ndi"
        assert app._url_editor_active is False
        assert app._settings_menu_active is True
        assert "RTSP URL" in app._settings_menu_banner
        assert "malformed URL" in app._settings_menu_banner

    def test_confirm_plain_edit_rebuilds_pipeline(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openfollow.runtime import app_modes

        monkeypatch.setattr(app_modes, "save_config", lambda *a, **kw: None)
        app = self._make_app(video_source_type="rtsp", rtsp_url="rtsp://x")
        app_modes.enter_url_editor(app)  # no revert_type
        app_modes.handle_url_editor_key(app, "Enter")
        assert app._swap_calls == ["rtsp"]
        assert app._url_editor_active is False
        assert app._config.video_source_type == "rtsp"

    def test_confirm_plain_edit_swap_failure_keeps_type_and_opens_settings(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Plain edit path failure: a broken URL surfaces a Settings
        banner, keeps the type (no revert_type to roll back to), and
        rolls the URL field back so stored config matches the receiver
        that swap_video restored – otherwise the heal loop would target
        a URL the receiver never adopted."""
        from openfollow.runtime import app_modes

        monkeypatch.setattr(app_modes, "save_config", lambda *a, **kw: None)
        app = self._make_app(
            video_source_type="rtsp",
            rtsp_url="rtsp://good",
            swap_raises=ValueError("malformed URL"),
        )
        app_modes.enter_url_editor(app)  # no revert_type
        # Replace the URL with a value whose swap fails.
        for _ in "rtsp://good":
            app_modes.handle_url_editor_key(app, "Backspace")
        for ch in "rtsp://bad":
            app_modes.handle_url_editor_key(app, ch)
        app_modes.handle_url_editor_key(app, "Enter")
        assert app._config.video_source_type == "rtsp"  # kept, not reverted
        assert app._config.rtsp_url == "rtsp://good"  # URL rolled back
        assert app._url_editor_active is False
        assert app._settings_menu_active is True
        assert "RTSP URL" in app._settings_menu_banner
        assert "malformed URL" in app._settings_menu_banner

    def test_cancel_auto_chained_reverts_type(self) -> None:
        """Esc on the auto-chain path restores the prior type – the
        receiver is already on the old plugin (swap_video preserves it
        on failure), so this is a pure config restore."""
        from openfollow.runtime import app_modes

        app = self._make_app(video_source_type="rtsp")
        app_modes.enter_url_editor(app, revert_type="ndi")
        app_modes.handle_url_editor_key(app, "Escape")
        assert app._config.video_source_type == "ndi"
        assert app._url_editor_active is False

    def test_cancel_manual_does_not_change_type(self) -> None:
        """When opened from the Settings menu (no revert_type),
        cancelling leaves the type alone."""
        from openfollow.runtime import app_modes

        app = self._make_app(video_source_type="rtsp")
        app_modes.enter_url_editor(app)
        app_modes.handle_url_editor_key(app, "Escape")
        assert app._config.video_source_type == "rtsp"


class TestUrlEditorInputRouting:
    """Editor shadows main-mode input completely."""

    def test_process_input_early_returns_when_editor_active(self) -> None:
        from openfollow.configuration import AppConfig
        from openfollow.runtime import app_modes

        class _KB:
            keys: set[str] = set()

        class _GP:
            def read_settings_menu_input(self):
                return SimpleNamespace(cancel_pressed=False)

        class _IM:
            keyboard_handler = _KB()
            gamepad_handler = _GP()

            def update(self, _dt):  # noqa: ANN001
                pytest.fail("must not run main-mode update while editing")

        app = SimpleNamespace(
            _input_manager=_IM(),
            _button_detection=None,
            _settings_menu_active=False,
            _video_receiver=None,
            _iface_selection_active=False,
            _source_type_selection_active=False,
            _field_choice_active=False,
            _url_editor_active=True,
            _browser_active=False,
            _config=AppConfig(),
        )
        app_modes.process_input(app, 0.01)

    def test_process_input_gamepad_cancel_closes_editor(self) -> None:
        from openfollow.configuration import AppConfig
        from openfollow.runtime import app_modes

        class _KB:
            keys: set[str] = set()

        class _GP:
            def read_settings_menu_input(self):
                return SimpleNamespace(cancel_pressed=True)

        class _IM:
            keyboard_handler = _KB()
            gamepad_handler = _GP()

            def update(self, _dt):  # noqa: ANN001
                pytest.fail("must not run main-mode update while editing")

        app = SimpleNamespace(
            _input_manager=_IM(),
            _button_detection=None,
            _settings_menu_active=False,
            _settings_menu_index=0,
            _settings_menu_banner="",
            _video_receiver=None,
            _iface_selection_active=False,
            _source_type_selection_active=False,
            _field_choice_active=False,
            _url_editor_active=True,
            _url_editor_revert_type="",
            _browser_active=False,
            _config=AppConfig(),
        )
        app._enter_settings_menu = lambda *, banner="": setattr(  # noqa: ARG005
            app,
            "_settings_menu_active",
            True,
        )
        app_modes.process_input(app, 0.01)
        assert app._url_editor_active is False
        assert app._settings_menu_active is True

    def test_process_url_editor_input_no_manager_is_noop(self) -> None:
        from openfollow.runtime import app_modes

        app = SimpleNamespace(_input_manager=None, _url_editor_active=True)
        app_modes.process_url_editor_input(app)
        assert app._url_editor_active is True

    def test_process_url_editor_input_swallows_gamepad_exception(self) -> None:
        from openfollow.runtime import app_modes

        app = SimpleNamespace(
            _input_manager=SimpleNamespace(
                gamepad_handler=SimpleNamespace(
                    read_settings_menu_input=lambda: (_ for _ in ()).throw(
                        RuntimeError("gamepad failure"),
                    ),
                ),
            ),
            _url_editor_active=True,
        )
        app_modes.process_url_editor_input(app)
        assert app._url_editor_active is True

    def test_handle_key_press_early_returns_when_editor_active(self) -> None:
        from openfollow.configuration import AppConfig
        from openfollow.runtime import app_modes

        app = SimpleNamespace(
            _config=AppConfig(),
            _button_detection=None,
            _settings_menu_active=False,
            _video_receiver=None,
            _iface_selection_active=False,
            _source_type_selection_active=False,
            _field_choice_active=False,
            _url_editor_active=True,
            _browser_active=False,
            _url_editor_value="",
        )
        # Polled "a" must not append (the on_key_down path owns typing).
        app_modes.handle_key_press(app, "a")
        assert app._url_editor_value == ""

    def test_on_key_down_routes_printables_into_editor(self) -> None:
        from openfollow.runtime import app_modes

        class _KB:
            def on_key_down(self, _ev) -> None: ...

        class _IM:
            keyboard_handler = _KB()

        app = SimpleNamespace(
            _input_manager=_IM(),
            _field_choice_active=False,
            _url_editor_active=True,
            _browser_active=False,
            _url_editor_value="",
            _controlled_ids=[1, 2, 3],
            _selected_id=1,
        )
        app._normalize_key = app_modes.normalize_key
        app_modes.on_key_down(app, {"key": "/"})
        app_modes.on_key_down(app, {"key": "1"})
        app_modes.on_key_down(app, {"key": "Backspace"})
        # "1" went into buffer NOT into marker selection (which would
        # have moved ``_selected_id`` to ``_controlled_ids[0]==1``).
        assert app._url_editor_value == "/"
        assert app._selected_id == 1


class TestSourceTypeAutoChainEditor:
    """Source-type switcher auto-chains to URL editor when field is empty."""

    def _make_app(
        self,
        *,
        current_type: str = "ndi",
        rtsp_url: str = "",
    ) -> object:
        from openfollow.configuration import AppConfig

        cfg = AppConfig()
        cfg.video_source_type = current_type
        cfg.rtsp_url = rtsp_url

        editor_calls: list[dict] = []
        settings_calls: list[dict] = []

        class _Services:
            def swap_video(self, _new_cfg) -> None:  # noqa: ARG002
                raise ValueError("rtsp_url is empty")

        class _Canvas:
            def close(self) -> None: ...

        class FakeApp:
            def __init__(self) -> None:
                self._config = cfg
                self._config_path = "/dev/null"
                self._config_mtime = 0.0
                self._runtime_services = _Services()
                self._canvas = _Canvas()
                self._source_type_selection_active = True
                self._available_source_types = [
                    ("ndi", "NDI"),
                    ("rtsp", "RTSP"),
                ]
                self._selected_source_type_index = 1  # RTSP
                self._settings_menu_active = False
                self._settings_menu_index = 0
                self._settings_menu_banner = ""
                self._editor_calls = editor_calls
                self._settings_calls = settings_calls

            def _enter_url_editor(
                self,
                *,
                banner: str = "",
                revert_type: str = "",
            ) -> None:
                self._editor_calls.append(
                    {"banner": banner, "revert_type": revert_type},
                )

            def _enter_settings_menu(self, *, banner: str = "") -> None:
                self._settings_calls.append({"banner": banner})

        return FakeApp()

    def test_empty_url_routes_to_editor_not_settings(self) -> None:
        from openfollow.runtime import app_modes

        app = self._make_app(current_type="ndi", rtsp_url="")
        app_modes.confirm_source_type_selection(app)
        assert app._source_type_selection_active is False
        assert app._settings_calls == []
        assert len(app._editor_calls) == 1
        call = app._editor_calls[0]
        assert call["revert_type"] == "ndi"
        assert "RTSP URL" in call["banner"]

    def test_empty_url_keeps_new_type_for_editor_to_persist(self) -> None:
        from openfollow.runtime import app_modes

        app = self._make_app(current_type="ndi", rtsp_url="")
        app_modes.confirm_source_type_selection(app)
        assert app._config.video_source_type == "rtsp"

    def test_populated_url_with_other_failure_uses_settings_banner(self) -> None:
        """When URL populated but swap fails (e.g. server unreachable),
        show banner on Settings rather than auto-chain editor."""
        from openfollow.runtime import app_modes

        app = self._make_app(
            current_type="ndi",
            rtsp_url="rtsp://1.2.3.4/stream",
        )
        app_modes.confirm_source_type_selection(app)
        assert app._editor_calls == []
        assert len(app._settings_calls) == 1
        assert app._config.video_source_type == "ndi"  # rolled back


class TestFieldChoicePicker:
    """On-device list picker for enum-style plugin fields (e.g.
    testpattern grey vs stage) – sibling to the URL editor."""

    def _make_app(
        self,
        *,
        video_source_type: str = "testpattern",
        testpattern_pattern: str = "grey",
        swap_raises: Exception | None = None,
    ) -> object:
        from openfollow.configuration import AppConfig

        cfg = AppConfig()
        cfg.video_source_type = video_source_type
        cfg.testpattern_pattern = testpattern_pattern

        swap_calls: list[str] = []

        class _Services:
            def swap_video(self, new_cfg) -> None:  # noqa: ANN001
                if swap_raises is not None:
                    raise swap_raises
                swap_calls.append(new_cfg.video_source_type)

        class _Canvas:
            def close(self) -> None: ...

        class _Gamepad:
            def __init__(self) -> None:
                self._next = SimpleNamespace(
                    up_pressed=False,
                    down_pressed=False,
                    confirm_pressed=False,
                    cancel_pressed=False,
                )
                self.joysticks: dict[int, object] = {}

            def read_source_selection_input(self) -> object:
                return self._next

        class _IM:
            gamepad_handler = _Gamepad()

        class FakeApp:
            def __init__(self) -> None:
                self._config = cfg
                self._config_path = "/dev/null"
                self._config_mtime = 0.0
                self._runtime_services = _Services()
                self._canvas = _Canvas()
                self._input_manager = _IM()
                self._field_choice_active = False
                self._field_choice_field_name = ""
                self._field_choice_field_label = ""
                self._field_choice_options: list[tuple[str, str]] = []
                self._field_choice_items: list[str] = []
                self._field_choice_selected_index = 0
                self._field_choice_revert_type = ""
                self._settings_menu_active = False
                self._settings_menu_index = 0
                self._settings_menu_banner = ""
                self._swap_calls = swap_calls
                self._editor_calls: list[dict] = []
                self._picker_calls: list[dict] = []
                self._source_selection_calls: list[bool] = []
                self._button_detection = None
                self._iface_selection_active = False
                self._url_editor_active = False
                self._field_choice_active = False
                self._url_editor_field_name = ""
                self._url_editor_field_label = ""
                self._url_editor_value = ""
                self._url_editor_banner = ""
                self._url_editor_revert_type = ""
                self._source_type_selection_active = False
                self._available_source_types: list[tuple[str, str]] = []
                self._selected_source_type_index = 0
                self._browser_active = False
                self._video_receiver = None

            def _get_config_mtime(self) -> float:
                return 1234.5

            def _enter_settings_menu(self, *, banner: str = "") -> None:
                self._settings_menu_active = True
                self._settings_menu_banner = banner

            def _enter_url_editor(
                self,
                *,
                banner: str = "",
                revert_type: str = "",
            ) -> None:
                self._editor_calls.append(
                    {"banner": banner, "revert_type": revert_type},
                )

            def _enter_field_choice_picker(self, *, revert_type: str = "") -> None:
                from openfollow.runtime.app_modes import enter_field_choice_picker

                self._picker_calls.append({"revert_type": revert_type})
                enter_field_choice_picker(self, revert_type=revert_type)

            def _enter_source_selection(self) -> None:
                self._source_selection_calls.append(True)

            def _exit_source_type_selection(self) -> None:
                from openfollow.runtime.app_modes import exit_source_type_selection

                exit_source_type_selection(self)

            def _confirm_field_choice_picker(self) -> None:
                from openfollow.runtime.app_modes import confirm_field_choice_picker

                confirm_field_choice_picker(self)

            def _cancel_field_choice_picker(self) -> None:
                from openfollow.runtime.app_modes import cancel_field_choice_picker

                cancel_field_choice_picker(self)

        return FakeApp()

    def test_enter_seeds_state_from_current_value(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openfollow.runtime import app_modes

        monkeypatch.setattr("openfollow.configuration.save_config", lambda *_a, **_k: None)
        app = self._make_app(testpattern_pattern="stage")
        app_modes.enter_field_choice_picker(app)
        assert app._field_choice_active is True
        assert app._field_choice_field_name == "testpattern_pattern"
        assert app._field_choice_field_label == "Pattern"
        assert app._field_choice_items == ["50% Grey", "Stage Scene"]
        # Cursor seeds to ``stage`` (index 1) because that's the current value.
        assert app._field_choice_selected_index == 1

    def test_enter_with_unknown_current_value_seeds_to_zero(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openfollow.runtime import app_modes

        monkeypatch.setattr("openfollow.configuration.save_config", lambda *_a, **_k: None)
        app = self._make_app(testpattern_pattern="not-a-pattern")
        app_modes.enter_field_choice_picker(app)
        assert app._field_choice_selected_index == 0

    def test_enter_does_nothing_for_plugin_without_choices(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openfollow.runtime import app_modes

        monkeypatch.setattr("openfollow.configuration.save_config", lambda *_a, **_k: None)
        app = self._make_app(video_source_type="rtsp")
        app_modes.enter_field_choice_picker(app)
        assert app._field_choice_active is False

    def test_exit_clears_state(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from openfollow.runtime import app_modes

        monkeypatch.setattr("openfollow.configuration.save_config", lambda *_a, **_k: None)
        app = self._make_app()
        app_modes.enter_field_choice_picker(app)
        app_modes.exit_field_choice_picker(app)
        assert app._field_choice_active is False
        assert app._field_choice_field_name == ""
        assert app._field_choice_items == []

    def test_confirm_writes_selected_value_to_config(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openfollow.runtime import app_modes

        saved: list[object] = []
        monkeypatch.setattr(
            "openfollow.runtime.app_modes.save_config",
            lambda cfg, _path: saved.append(cfg),
        )
        app = self._make_app(testpattern_pattern="grey")
        app_modes.enter_field_choice_picker(app)
        app._field_choice_selected_index = 1  # stage
        app_modes.confirm_field_choice_picker(app)
        assert app._config.testpattern_pattern == "stage"
        assert app._field_choice_active is False
        assert len(saved) == 1

    def test_confirm_live_applies_via_swap_video_on_same_type_value_change(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openfollow.runtime import app_modes

        monkeypatch.setattr(
            "openfollow.runtime.app_modes.save_config",
            lambda *_a, **_k: None,
        )
        app = self._make_app(testpattern_pattern="grey")
        # No revert_type – operator is already on testpattern.
        app_modes.enter_field_choice_picker(app)
        app._field_choice_selected_index = 1  # stage
        app_modes.confirm_field_choice_picker(app)
        # The fixture records swap_video calls by source_type; on the
        # same-type path we expect a single swap onto the same type so
        # the receiver picks up the new value.
        assert app._swap_calls == ["testpattern"]
        assert app._config.testpattern_pattern == "stage"

    def test_confirm_same_type_swap_failure_reverts_field_value(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openfollow.runtime import app_modes

        monkeypatch.setattr(
            "openfollow.runtime.app_modes.save_config",
            lambda *_a, **_k: None,
        )
        app = self._make_app(
            testpattern_pattern="grey",
            swap_raises=RuntimeError("pattern rejected"),
        )
        app_modes.enter_field_choice_picker(app)
        app._field_choice_selected_index = 1  # stage – the failing value
        app_modes.confirm_field_choice_picker(app)
        # Value reverted to the pre-picker state; banner in Settings.
        assert app._config.testpattern_pattern == "grey"
        assert app._field_choice_active is False
        assert app._settings_menu_active is True
        assert "Couldn't apply" in app._settings_menu_banner
        assert "pattern rejected" in app._settings_menu_banner

    def test_cancel_without_revert_just_closes(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openfollow.runtime import app_modes

        monkeypatch.setattr(
            "openfollow.runtime.app_modes.save_config",
            lambda *_a, **_k: None,
        )
        app = self._make_app(testpattern_pattern="grey")
        app_modes.enter_field_choice_picker(app)
        app._field_choice_selected_index = 1
        app_modes.cancel_field_choice_picker(app)
        # No save fired, config still on ``grey``.
        assert app._config.testpattern_pattern == "grey"
        assert app._field_choice_active is False
        # Lands in Settings menu (matches URL editor cancel semantics).
        assert app._settings_menu_active is True

    def test_cancel_with_revert_restores_prior_source_type(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openfollow.runtime import app_modes

        monkeypatch.setattr(
            "openfollow.runtime.app_modes.save_config",
            lambda *_a, **_k: None,
        )
        app = self._make_app(testpattern_pattern="grey")
        app._config.video_source_type = "testpattern"
        app_modes.enter_field_choice_picker(app, revert_type="ndi")
        app_modes.cancel_field_choice_picker(app)
        assert app._config.video_source_type == "ndi"

    def test_confirm_with_revert_retries_swap(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openfollow.runtime import app_modes

        monkeypatch.setattr(
            "openfollow.runtime.app_modes.save_config",
            lambda *_a, **_k: None,
        )
        app = self._make_app()
        app_modes.enter_field_choice_picker(app, revert_type="ndi")
        app._field_choice_selected_index = 1
        app_modes.confirm_field_choice_picker(app)
        assert app._swap_calls == ["testpattern"]
        assert app._config.testpattern_pattern == "stage"

    def test_route_after_change_prefers_picker_over_url_editor(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When switching to a plugin whose first text field has
        choices (testpattern), the on-device flow opens the picker
        instead of the free-text URL editor."""
        from openfollow.runtime import app_modes

        monkeypatch.setattr("openfollow.configuration.save_config", lambda *_a, **_k: None)
        app = self._make_app(video_source_type="testpattern")
        app_modes._route_after_video_source_change(app, "testpattern")
        assert app._picker_calls == [{"revert_type": ""}]
        assert app._editor_calls == []

    def test_gamepad_input_navigates_and_confirms(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openfollow.runtime import app_modes

        saved: list[object] = []
        monkeypatch.setattr(
            "openfollow.runtime.app_modes.save_config",
            lambda cfg, _path: saved.append(cfg),
        )
        app = self._make_app(testpattern_pattern="grey")
        app_modes.enter_field_choice_picker(app)
        gp = app._input_manager.gamepad_handler

        gp._next = SimpleNamespace(
            up_pressed=False,
            down_pressed=True,
            confirm_pressed=False,
            cancel_pressed=False,
        )
        app_modes.process_field_choice_picker_input(app)
        assert app._field_choice_selected_index == 1

        gp._next = SimpleNamespace(
            up_pressed=False,
            down_pressed=False,
            confirm_pressed=True,
            cancel_pressed=False,
        )
        app_modes.process_field_choice_picker_input(app)
        assert app._config.testpattern_pattern == "stage"
        assert app._field_choice_active is False

    def test_keyboard_input_navigates_and_confirms(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openfollow.runtime import app_modes

        saved: list[object] = []
        monkeypatch.setattr(
            "openfollow.runtime.app_modes.save_config",
            lambda cfg, _path: saved.append(cfg),
        )
        app = self._make_app(testpattern_pattern="grey")
        app_modes.enter_field_choice_picker(app)

        app_modes.handle_key_press(app, "ArrowDown")
        assert app._field_choice_selected_index == 1
        app_modes.handle_key_press(app, "ArrowUp")
        assert app._field_choice_selected_index == 0
        app_modes.handle_key_press(app, "ArrowDown")
        app_modes.handle_key_press(app, "Enter")
        assert app._config.testpattern_pattern == "stage"
        assert app._field_choice_active is False

    def test_escape_cancels_with_revert_semantics(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openfollow.runtime import app_modes

        monkeypatch.setattr(
            "openfollow.runtime.app_modes.save_config",
            lambda *_a, **_k: None,
        )
        app = self._make_app()
        app._config.video_source_type = "testpattern"
        app_modes.enter_field_choice_picker(app, revert_type="ndi")
        app_modes.handle_key_press(app, "Escape")
        assert app._field_choice_active is False
        assert app._config.video_source_type == "ndi"

    def test_confirm_with_no_field_name_or_options_just_exits(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openfollow.runtime import app_modes

        monkeypatch.setattr(
            "openfollow.runtime.app_modes.save_config",
            lambda *_a, **_k: None,
        )
        app = self._make_app()
        # Mark active but leave field_name/options empty.
        app._field_choice_active = True
        app._field_choice_field_name = ""
        app._field_choice_options = []
        app_modes.confirm_field_choice_picker(app)
        assert app._field_choice_active is False

    def test_confirm_with_revert_when_swap_still_fails_rolls_back_and_banners(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Auto-chain path: operator picked a value but ``swap_video``
        still fails (e.g., NDI library missing for the destination
        plugin). Roll the type back to ``revert_type``, exit the
        picker, and land in Settings with a banner explaining
        what to fix and where."""
        from openfollow.runtime import app_modes

        monkeypatch.setattr(
            "openfollow.runtime.app_modes.save_config",
            lambda *_a, **_k: None,
        )

        def _swap_fail(_cfg):
            raise RuntimeError("ndi library missing")

        app = self._make_app()
        app._runtime_services.swap_video = _swap_fail  # type: ignore[method-assign]
        app._config.video_source_type = "testpattern"
        app_modes.enter_field_choice_picker(app, revert_type="ndi")
        app._field_choice_selected_index = 1
        app_modes.confirm_field_choice_picker(app)
        # Reverted to the prior plugin so the running session is consistent.
        assert app._config.video_source_type == "ndi"
        assert app._field_choice_active is False
        assert app._settings_menu_active is True
        assert "Couldn't switch" in app._settings_menu_banner
        assert "ndi library missing" in app._settings_menu_banner

    def test_process_input_noop_when_input_manager_missing(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openfollow.runtime import app_modes

        monkeypatch.setattr(
            "openfollow.runtime.app_modes.save_config",
            lambda *_a, **_k: None,
        )
        app = self._make_app()
        app_modes.enter_field_choice_picker(app)
        app._input_manager = None
        # Must not raise.
        app_modes.process_field_choice_picker_input(app)

    def test_process_input_logs_gamepad_read_exception(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        from openfollow.runtime import app_modes

        monkeypatch.setattr(
            "openfollow.runtime.app_modes.save_config",
            lambda *_a, **_k: None,
        )
        app = self._make_app()
        app_modes.enter_field_choice_picker(app)

        def _explode():
            raise RuntimeError("SDL2 wedged")

        app._input_manager.gamepad_handler.read_source_selection_input = _explode  # type: ignore[method-assign]
        with caplog.at_level("WARNING"):
            app_modes.process_field_choice_picker_input(app)
        assert any("Field-choice picker input error" in r.message for r in caplog.records)

    def test_process_input_cancel_with_empty_items_exits(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openfollow.runtime import app_modes

        monkeypatch.setattr(
            "openfollow.runtime.app_modes.save_config",
            lambda *_a, **_k: None,
        )
        app = self._make_app()
        app_modes.enter_field_choice_picker(app)
        app._field_choice_items = []
        gp = app._input_manager.gamepad_handler

        # Non-cancel inputs while items are empty: no-op, picker stays
        # active.
        gp._next = SimpleNamespace(
            up_pressed=True,
            down_pressed=False,
            confirm_pressed=False,
            cancel_pressed=False,
        )
        app_modes.process_field_choice_picker_input(app)
        assert app._field_choice_active is True

        # Cancel with empty items: closes the picker.
        gp._next = SimpleNamespace(
            up_pressed=False,
            down_pressed=False,
            confirm_pressed=False,
            cancel_pressed=True,
        )
        app_modes.process_field_choice_picker_input(app)
        assert app._field_choice_active is False

    def test_process_input_up_arrow_navigates_back(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openfollow.runtime import app_modes

        monkeypatch.setattr(
            "openfollow.runtime.app_modes.save_config",
            lambda *_a, **_k: None,
        )
        app = self._make_app(testpattern_pattern="stage")
        app_modes.enter_field_choice_picker(app)
        gp = app._input_manager.gamepad_handler
        # Selected starts at index 1 (matches "stage"). Up-press → 0.
        gp._next = SimpleNamespace(
            up_pressed=True,
            down_pressed=False,
            confirm_pressed=False,
            cancel_pressed=False,
        )
        app_modes.process_field_choice_picker_input(app)
        assert app._field_choice_selected_index == 0

    def test_process_input_cancel_with_items_closes(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Gamepad cancel-press closes the picker via the
        cancel-with-items branch (separate code path from the
        empty-items cancel-out tested above)."""
        from openfollow.runtime import app_modes

        monkeypatch.setattr(
            "openfollow.runtime.app_modes.save_config",
            lambda *_a, **_k: None,
        )
        app = self._make_app()
        app_modes.enter_field_choice_picker(app)
        gp = app._input_manager.gamepad_handler
        gp._next = SimpleNamespace(
            up_pressed=False,
            down_pressed=False,
            confirm_pressed=False,
            cancel_pressed=True,
        )
        app_modes.process_field_choice_picker_input(app)
        assert app._field_choice_active is False

    def test_handle_key_press_noop_when_items_empty(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openfollow.runtime import app_modes

        monkeypatch.setattr(
            "openfollow.runtime.app_modes.save_config",
            lambda *_a, **_k: None,
        )
        app = self._make_app()
        app_modes.enter_field_choice_picker(app)
        app._field_choice_items = []
        # Doesn't raise; picker stays active.
        app_modes.handle_key_press(app, "ArrowDown")
        assert app._field_choice_active is True

    def test_handle_key_press_ignores_unrelated_keys(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Any key that isn't ArrowUp / ArrowDown / Enter / Escape
        must fall through the elif chain without firing a confirm or
        moving the cursor. Covers the False path of the
        ``elif key == "Enter":`` branch."""
        from openfollow.runtime import app_modes

        monkeypatch.setattr(
            "openfollow.runtime.app_modes.save_config",
            lambda *_a, **_k: None,
        )
        app = self._make_app(testpattern_pattern="grey")
        app_modes.enter_field_choice_picker(app)
        initial_idx = app._field_choice_selected_index
        # A printable key that matches none of the handled cases.
        app_modes.handle_key_press(app, "a")
        # Cursor untouched, picker still active, no save fired.
        assert app._field_choice_selected_index == initial_idx
        assert app._field_choice_active is True
        assert app._config.testpattern_pattern == "grey"

    def test_on_key_down_short_circuits_when_picker_active(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openfollow.runtime import app_modes

        monkeypatch.setattr(
            "openfollow.runtime.app_modes.save_config",
            lambda *_a, **_k: None,
        )
        app = self._make_app()
        app._normalize_key = app_modes.normalize_key
        app_modes.enter_field_choice_picker(app)
        # ``on_key_down`` reads ``_controlled_ids`` / ``_selected_id`` only
        # AFTER the short-circuit; the fact that we don't need to seed
        # them confirms the early return fired.
        app_modes.on_key_down(app, {"key": "2"})  # digit key

    def test_first_video_choice_field_returns_none_for_unknown_type(
        self,
    ) -> None:
        from openfollow.runtime import app_modes

        assert app_modes._first_video_choice_field("not-a-real-plugin") is None


class TestSourceTypeConfirmAutoChainIntoPicker:
    """Swap failure auto-chains into picker for choice-bearing fields instead of URL editor."""

    def _make_app(self) -> object:
        from openfollow.configuration import AppConfig

        cfg = AppConfig()
        cfg.video_source_type = "ndi"  # current

        def _swap_fail(_cfg):
            raise RuntimeError("ndi → testpattern swap blew up")

        class _Services:
            swap_video = staticmethod(_swap_fail)

        picker_calls: list[dict] = []
        app = SimpleNamespace(
            _config=cfg,
            _config_path="/dev/null",
            _config_mtime=0.0,
            _runtime_services=_Services(),
            _available_source_types=[("testpattern", "Test Pattern")],
            _selected_source_type_index=0,
            _source_type_selection_active=True,
            _field_choice_active=False,
        )
        app._picker_calls = picker_calls
        app._enter_field_choice_picker = lambda *, revert_type="": picker_calls.append({"revert_type": revert_type})
        return app

    def test_swap_failure_with_choice_field_routes_to_picker(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from openfollow.runtime import app_modes

        monkeypatch.setattr(
            "openfollow.runtime.app_modes.save_config",
            lambda *_a, **_k: None,
        )
        app = self._make_app()
        app_modes.confirm_source_type_selection(app)
        # Source-type picker closed; chained into the field-choice picker
        # with the prior type captured so cancel can roll back.
        assert app._source_type_selection_active is False
        assert app._picker_calls == [{"revert_type": "ndi"}]


class TestLegacyShortcutsRemoved:
    """Legacy direct shortcuts removed. Must stay inert – Settings menu
    is the only entry point for interface / source / button detection.
    """

    def _make_app(self) -> object:
        from openfollow.configuration import AppConfig

        class FakeCanvas:
            def __init__(self) -> None:
                self.closed = False

            def close(self) -> None:
                self.closed = True

        class FakeServer:
            def get_marker(self, _tid):  # noqa: ANN001
                return None

        class FakeApp:
            def __init__(self) -> None:
                self._config = AppConfig()
                self._settings_menu_active = False
                self._settings_menu_index = 0
                self._button_detection = None
                self._iface_selection_active = False
                self._source_type_selection_active = False
                self._available_source_types: list[tuple[str, str]] = []
                self._selected_source_type_index = 0
                self._url_editor_active = False
                self._field_choice_active = False
                self._url_editor_field_name = ""
                self._url_editor_field_label = ""
                self._url_editor_value = ""
                self._url_editor_banner = ""
                self._url_editor_revert_type = ""
                self._browser_active = False
                self._browser_overlay = None
                self._video_receiver = None
                self._canvas = FakeCanvas()
                self._server = FakeServer()
                self._selected_id = None
                self._show_hud_help = False
                self._iface_entered = False
                self._source_entered = False
                self._button_detection_entered = False

            def _enter_iface_selection(self) -> None:
                self._iface_entered = True

            def _enter_source_selection(self) -> None:
                self._source_entered = True

            def _enter_button_detection(self) -> None:
                self._button_detection_entered = True

        return FakeApp()

    def test_i_key_does_not_enter_iface_selection(self) -> None:
        app = self._make_app()
        handle_key_press(app, "i")
        assert app._iface_entered is False

    def test_n_key_does_not_enter_source_selection(self) -> None:
        app = self._make_app()
        handle_key_press(app, "n")
        assert app._source_entered is False

    def test_b_key_does_not_enter_button_detection(self) -> None:
        app = self._make_app()
        handle_key_press(app, "b")
        assert app._button_detection_entered is False

    def test_escape_does_not_close_canvas_in_normal_mode(self) -> None:
        app = self._make_app()
        handle_key_press(app, "Escape")
        assert app._canvas.closed is False


class TestNormalModeKeyDispatch:
    """Regression guards for normal-mode keyboard action dispatch."""

    def _make_app(self, tmp_path) -> object:  # noqa: ANN001
        from openfollow.configuration import AppConfig

        class FakeCanvas:
            def close(self) -> None: ...

        class FakeServer:
            def get_marker(self, _tid):  # noqa: ANN001
                return None

        class FakeApp:
            def __init__(self) -> None:
                self._config = AppConfig()
                self._settings_menu_active = False
                self._button_detection = None
                self._iface_selection_active = False
                self._source_type_selection_active = False
                self._available_source_types: list[tuple[str, str]] = []
                self._selected_source_type_index = 0
                self._url_editor_active = False
                self._field_choice_active = False
                self._url_editor_field_name = ""
                self._url_editor_field_label = ""
                self._url_editor_value = ""
                self._url_editor_banner = ""
                self._url_editor_revert_type = ""
                self._browser_active = False
                self._browser_overlay = None
                self._video_receiver = None
                self._canvas = FakeCanvas()
                self._server = FakeServer()
                self._selected_id = None
                self._show_hud_help = False
                self._config_path = str(tmp_path / "config.toml")

        return FakeApp()

    def test_z_key_toggles_zone_overlay(self, tmp_path) -> None:
        app = self._make_app(tmp_path)
        assert app._config.controller.key_toggle_zones == "z"
        before = app._config.trigger_zones.show_overlay
        handle_key_press(app, "z")
        assert app._config.trigger_zones.show_overlay is (not before)

    def test_h_key_toggles_help(self, tmp_path) -> None:
        app = self._make_app(tmp_path)
        assert app._show_hud_help is False
        handle_key_press(app, "h")
        assert app._show_hud_help is True

    def test_z_key_suppressed_when_settings_menu_active(self, tmp_path) -> None:
        app = self._make_app(tmp_path)
        app._settings_menu_active = True
        before = app._config.trigger_zones.show_overlay
        handle_key_press(app, "z")
        assert app._config.trigger_zones.show_overlay is before


class TestExclusiveModeGuard:
    def test_exclusive_mode_true_for_source_selection(self) -> None:
        from openfollow.runtime import app_modes

        app = SimpleNamespace(_video_receiver=SimpleNamespace(source_selection_active=True))
        assert app_modes._exclusive_mode_active(app) is True

    def test_exclusive_mode_true_for_modal_flag(self) -> None:
        from openfollow.runtime import app_modes

        app = SimpleNamespace(_video_receiver=None, _settings_menu_active=True)
        assert app_modes._exclusive_mode_active(app) is True

    def test_exclusive_mode_false_when_idle(self) -> None:
        from openfollow.runtime import app_modes

        app = SimpleNamespace(_video_receiver=None)
        assert app_modes._exclusive_mode_active(app) is False

    def test_enter_button_detection_refused_when_modal_active(self, caplog: pytest.LogCaptureFixture) -> None:
        from openfollow.runtime import app_modes

        app = SimpleNamespace(
            _button_detection=None,
            _video_receiver=None,
            _settings_menu_active=True,
        )
        with caplog.at_level("WARNING"):
            app_modes.enter_button_detection(app)
        assert app._button_detection is None
        assert any("another mode is active" in r.message for r in caplog.records)


class TestPersistConfigHelper:
    def test_persist_config_returns_false_on_save_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from openfollow.runtime import app_modes

        def _boom(*_a: object, **_k: object) -> None:
            raise RuntimeError("disk full")

        monkeypatch.setattr(app_modes, "save_config", _boom)
        app = SimpleNamespace(_config=object(), _config_path="x")
        assert app_modes._persist_config(app) is False
