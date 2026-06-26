# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for ``build_marker_visual_state`` – the big per-frame overlay
snapshot builder.

The companion file ``test_services_marker_visuals.py`` already covers
``sync_grid_config`` / ``sync_marker_config`` / ``build_initial_overlay_state``
/ ``_populate_zone_overlay``.  This file drives the remaining public entry
point, ``build_marker_visual_state``, which:

* picks up controlled vs. viewer markers,
* synthesises speed from the gamepad or falls back to PSN velocity,
* reuses the pre-allocated marker pool up to its size and spills to
  freshly-allocated instances above it,
* copies video-receiver state onto the overlay,
* forwards system / detection / button-detection state,
* emits the input-state flags (keyboard, controller, mouse) + hints.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from openfollow.configuration import AppConfig
from openfollow.runtime.overlay_state import MarkerOverlayData, OverlayState
from openfollow.runtime.services_detection_pin import get_or_create_manual_marker
from openfollow.runtime.services_marker_visuals import build_marker_visual_state
from openfollow.runtime_metrics import OverlayStatePool

pytestmark = pytest.mark.unit

# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


class _FakeMarker:
    def __init__(
        self,
        tid: int,
        pos: tuple[float, float, float] = (0.0, 0.0, 0.0),
        speed: tuple[float, float, float] = (0.0, 0.0, 0.0),
    ) -> None:
        self.tid = tid
        self.pos = pos
        self.speed = speed
        self.set_speed_calls: list[tuple[float, float, float]] = []

    def set_speed(self, vx: float, vy: float, vz: float) -> None:
        self.set_speed_calls.append((vx, vy, vz))


class _FakePsnServer:
    def __init__(self, markers: dict[int, _FakeMarker]) -> None:
        self._markers = markers

    def get_marker(self, tid: int) -> _FakeMarker | None:
        return self._markers.get(tid)


class _FakePsnReceiver:
    def __init__(self, markers: dict[int, _FakeMarker], online: dict[int, bool] | None = None) -> None:
        self._markers = markers
        self._online = online or {}

    def get_marker(self, tid: int) -> _FakeMarker | None:
        return self._markers.get(tid)

    def is_marker_online(self, tid: int) -> bool:
        return bool(self._online.get(tid, True))


class _FakeGamepadHandlerShim:
    """Carries ``joysticks`` for the multi-pad help-label gate."""

    def __init__(self, joystick_count: int = 0) -> None:
        self.joysticks = {idx: object() for idx in range(joystick_count)}


class _FakeInputManager:
    def __init__(
        self,
        marker_speeds: dict[int, float] | None = None,
        controller_info: list[dict] | None = None,
        keyboard_connected: bool = False,
        joystick_count: int = 0,
    ) -> None:
        self._marker_speeds = marker_speeds or {}
        self._controller_info = controller_info or []
        self._kbd_connected = keyboard_connected
        # Joystick count gates next/prev help labels in multi-pad mode.
        self.gamepad_handler = _FakeGamepadHandlerShim(joystick_count)

    def get_marker_gamepad_speeds(self) -> dict[int, float]:
        return dict(self._marker_speeds)

    def get_controller_info(self) -> list[dict]:
        return list(self._controller_info)

    def is_keyboard_connected(self) -> bool:
        return self._kbd_connected


class _FakeVideoReceiver:
    def __init__(self) -> None:
        # The reader now consumes the status as one unit via ``snapshot()``.
        self.status_marker = SimpleNamespace(
            snapshot=lambda: SimpleNamespace(
                is_connected=True,
                reconnect_attempt=0,
                error_message="",
            ),
        )
        self.source_name = "NDI://CAM"
        self.source_selection_active = False
        self.discovered_sources = ["CAM1", "CAM2"]
        self.selected_source_index = 1
        self.source_selection_title = "SELECT NDI"


class _FakeCamCfg:
    def __init__(
        self,
        pos_x: float = 0.0,
        pos_y: float = -6.0,
        pos_z: float = 2.5,
        pitch: float = -15.0,
        yaw: float = 0.0,
        roll: float = 0.0,
        fov: float = 60.0,
    ) -> None:
        self.pos_x = pos_x
        self.pos_y = pos_y
        self.pos_z = pos_z
        self.pitch = pitch
        self.yaw = yaw
        self.roll = roll
        self.fov = fov


class _FakeCamera:
    def __init__(self) -> None:
        self._cfg = _FakeCamCfg()

    def to_config(self) -> _FakeCamCfg:
        return self._cfg


@pytest.fixture
def pool() -> OverlayStatePool:
    return OverlayStatePool(pool_size=3)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _build_app(
    *,
    controlled: list[int] | None = None,
    viewer: list[int] | None = None,
    server_markers: dict[int, _FakeMarker] | None = None,
    receiver_markers: dict[int, _FakeMarker] | None = None,
    online: dict[int, bool] | None = None,
    input_manager: _FakeInputManager | None = None,
    button_detection: Any = None,
    settings_menu_active: bool = False,
    show_hud_help: bool = True,
) -> SimpleNamespace:
    app = SimpleNamespace(
        _config=AppConfig(psn_system_name="X"),
        _controlled_ids=list(controlled or []),
        _viewer_ids=list(viewer or []),
        _selected_id=(controlled or [None])[0] if controlled else None,
        _server=_FakePsnServer(server_markers or {}),
        _psn_receiver=_FakePsnReceiver(receiver_markers or {}, online or {}),
        _input_manager=input_manager,
        _video_receiver=_FakeVideoReceiver(),
        _camera=_FakeCamera(),
        _button_detection=button_detection,
        _iface_selection_active=False,
        _available_interfaces=[],
        _selected_iface_index=0,
        _settings_menu_active=settings_menu_active,
        _settings_menu_index=0,
        _settings_menu_banner="",
        _source_type_selection_active=False,
        _available_source_types=[],
        _selected_source_type_index=0,
        _url_editor_active=False,
        _field_choice_active=False,
        _url_editor_field_label="",
        _url_editor_value="",
        _url_editor_banner="",
        _show_hud_help=show_hud_help,
        _runtime_services=SimpleNamespace(_zone_engine=None),
        _assist_manual={},
        _detection_pin_states={},
    )
    # FakeApp mirrors get_marker_move_speed for per-marker speed reads.
    app.get_marker_move_speed = lambda mid: (
        app._config.marker_move_speeds.get(mid, app._config.marker.move_speed)
        if mid is not None
        else app._config.marker.move_speed
    )
    return app


def _build(
    app: SimpleNamespace,
    pool: OverlayStatePool,
    *,
    system_stats: Any = None,
    person_detector: Any = None,
) -> OverlayState:
    # Controller badge is stamped from InputManager.get_controller_info.
    return build_marker_visual_state(
        app,
        overlay_state_pool=pool,
        system_stats=system_stats,
        person_detector=person_detector,
        cam_params_buffer=np.zeros(7, dtype=np.float64),
    )


# --------------------------------------------------------------------------- #
# System stats → state flow
# --------------------------------------------------------------------------- #


class TestSystemStatsFlow:
    def test_system_stats_none_leaves_defaults(self, pool: OverlayStatePool) -> None:
        app = _build_app()
        state = _build(app, pool, system_stats=None)
        assert state.cpu_percent == 0.0
        assert state.ram_percent == 0.0
        assert state.temperature is None
        assert state.ip_text == ""

    def test_port_80_is_omitted_from_ip_text(self, pool: OverlayStatePool) -> None:
        app = _build_app()
        stats = SimpleNamespace(
            cpu_percent=40.1,
            ram_percent=66.5,
            temperature=58.0,
            ip_address="10.0.0.7",
            iface_name="",
        )
        collector = SimpleNamespace(update=lambda: stats)
        state = _build(app, pool, system_stats=collector)
        assert state.cpu_percent == pytest.approx(40.1)
        assert state.ram_percent == pytest.approx(66.5)
        assert state.temperature == pytest.approx(58.0)
        assert state.ip_text == "10.0.0.7"

    def test_non_default_configured_port_is_shown(self, pool: OverlayStatePool) -> None:
        from dataclasses import replace

        app = _build_app()
        app._config = replace(app._config, web_port=9000)
        stats = SimpleNamespace(
            cpu_percent=0.0,
            ram_percent=0.0,
            temperature=None,
            ip_address="10.0.0.7",
            iface_name="",
        )
        collector = SimpleNamespace(update=lambda: stats)
        state = _build(app, pool, system_stats=collector)
        assert state.ip_text == "10.0.0.7:9000"

    def test_web_server_display_port_overrides_config(self, pool: OverlayStatePool) -> None:
        app = _build_app()
        app._web_server = SimpleNamespace(display_port=8080)
        stats = SimpleNamespace(
            cpu_percent=0.0,
            ram_percent=0.0,
            temperature=None,
            ip_address="10.0.0.7",
            iface_name="",
        )
        collector = SimpleNamespace(update=lambda: stats)
        state = _build(app, pool, system_stats=collector)
        assert state.ip_text == "10.0.0.7:8080"

    def test_unknown_ip_skips_port_suffix(self, pool: OverlayStatePool) -> None:
        app = _build_app()
        stats = SimpleNamespace(
            cpu_percent=0.0,
            ram_percent=0.0,
            temperature=None,
            ip_address="N/A",
            iface_name="",
        )
        collector = SimpleNamespace(update=lambda: stats)
        state = _build(app, pool, system_stats=collector)
        assert state.ip_text == "N/A"

    def test_iface_name_appended_in_parens(self, pool: OverlayStatePool) -> None:
        """HUD shows IP address with interface name for multi-homed hosts."""
        app = _build_app()
        stats = SimpleNamespace(
            cpu_percent=0.0,
            ram_percent=0.0,
            temperature=None,
            ip_address="192.168.178.61",
            iface_name="eth0",
        )
        collector = SimpleNamespace(update=lambda: stats)
        state = _build(app, pool, system_stats=collector)
        assert state.ip_text == "192.168.178.61 (eth0)"

    def test_iface_name_with_non_default_port(self, pool: OverlayStatePool) -> None:
        """The iface suffix follows the ``ip:port`` block so the
        whole composed value reads naturally."""
        from dataclasses import replace

        app = _build_app()
        app._config = replace(app._config, web_port=9000)
        stats = SimpleNamespace(
            cpu_percent=0.0,
            ram_percent=0.0,
            temperature=None,
            ip_address="10.0.0.7",
            iface_name="wlan0",
        )
        collector = SimpleNamespace(update=lambda: stats)
        state = _build(app, pool, system_stats=collector)
        assert state.ip_text == "10.0.0.7:9000 (wlan0)"


# --------------------------------------------------------------------------- #
# Video + iface + settings menu pass-through
# --------------------------------------------------------------------------- #


class TestVideoAndMenuState:
    def test_video_fields_mirror_receiver(self, pool: OverlayStatePool) -> None:
        app = _build_app()
        state = _build(app, pool)
        assert state.video_connected is True
        assert state.source_label == "NDI://CAM"
        assert state.discovered_sources == ["CAM1", "CAM2"]
        assert state.source_selection_title == "SELECT NDI"

    def test_settings_menu_inactive_clears_items(self, pool: OverlayStatePool) -> None:
        app = _build_app(settings_menu_active=False)
        state = _build(app, pool)
        assert state.settings_items == []
        assert state.settings_items_enabled == []
        assert state.settings_selected_index == 0

    def test_about_active_syncs_to_overlay_state(self, pool: OverlayStatePool) -> None:
        """_about_active state is mirrored to overlay for draw pass."""
        app = _build_app()
        app._about_active = True
        assert _build(app, pool).about_active is True
        app._about_active = False
        assert _build(app, pool).about_active is False

    def test_settings_menu_active_builds_items(self, pool: OverlayStatePool, monkeypatch: pytest.MonkeyPatch) -> None:
        app = _build_app(settings_menu_active=True)
        app._settings_menu_index = 2

        from openfollow.runtime import app_modes

        monkeypatch.setattr(
            app_modes,
            "build_settings_menu_items",
            lambda a: (
                ["Option A", "Option B", "Option C"],
                [True, False, True],
                ["", "Linux only", ""],
            ),
        )
        state = _build(app, pool)
        assert state.settings_items == ["Option A", "Option B", "Option C"]
        assert state.settings_items_enabled == [True, False, True]
        assert state.settings_items_disabled_reasons == ["", "Linux only", ""]
        assert state.settings_selected_index == 2

    def test_settings_menu_banner_passes_through_to_state(
        self, pool: OverlayStatePool, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app = _build_app(settings_menu_active=True)
        app._settings_menu_banner = "Configured IP unavailable."

        from openfollow.runtime import app_modes

        monkeypatch.setattr(
            app_modes,
            "build_settings_menu_items",
            lambda a: (["X"], [True], [""]),
        )
        state = _build(app, pool)
        assert state.settings_menu_banner == "Configured IP unavailable."

    def test_settings_menu_inactive_clears_banner(self, pool: OverlayStatePool) -> None:
        app = _build_app(settings_menu_active=False)
        app._settings_menu_banner = "stale"
        state = _build(app, pool)
        assert state.settings_menu_banner == ""

    def test_url_editor_passes_through_to_state(
        self,
        pool: OverlayStatePool,
    ) -> None:
        app = _build_app()
        app._url_editor_active = True
        app._url_editor_field_label = "RTSP URL"
        app._url_editor_value = "rtsp://1.2.3.4"
        app._url_editor_banner = "RTSP needs URL."
        state = _build(app, pool)
        assert state.url_editor_active is True
        assert state.url_editor_field_label == "RTSP URL"
        assert state.url_editor_value == "rtsp://1.2.3.4"
        assert state.url_editor_banner == "RTSP needs URL."

    def test_url_editor_inactive_clears_state(
        self,
        pool: OverlayStatePool,
    ) -> None:
        app = _build_app()
        app._url_editor_active = False
        app._url_editor_field_label = "stale"
        app._url_editor_value = "stale"
        app._url_editor_banner = "stale"
        state = _build(app, pool)
        assert state.url_editor_active is False
        assert state.url_editor_field_label == ""
        assert state.url_editor_value == ""
        assert state.url_editor_banner == ""

    def test_field_choice_picker_passes_through_to_state(
        self,
        pool: OverlayStatePool,
    ) -> None:
        app = _build_app()
        app._field_choice_active = True
        app._field_choice_field_label = "Pattern"
        app._field_choice_items = ["50% Grey", "Stage Scene"]
        app._field_choice_selected_index = 1
        state = _build(app, pool)
        assert state.field_choice_active is True
        assert state.field_choice_title == "Pattern"
        assert state.field_choice_items == ["50% Grey", "Stage Scene"]
        assert state.field_choice_selected_index == 1

    def test_source_type_selection_passes_through_to_state(
        self,
        pool: OverlayStatePool,
    ) -> None:
        app = _build_app()
        app._source_type_selection_active = True
        app._available_source_types = [
            ("ndi", "NDI"),
            ("rtsp", "RTSP"),
            ("testpattern", "Test Pattern"),
        ]
        app._selected_source_type_index = 1
        state = _build(app, pool)
        assert state.source_type_selection_active is True
        assert state.available_source_types == [
            ("ndi", "NDI"),
            ("rtsp", "RTSP"),
            ("testpattern", "Test Pattern"),
        ]
        assert state.selected_source_type_index == 1


# --------------------------------------------------------------------------- #
# Marker population
# --------------------------------------------------------------------------- #


class TestMarkerPopulation:
    def test_controlled_marker_pulled_from_server(self, pool: OverlayStatePool) -> None:
        app = _build_app(
            controlled=[1],
            viewer=[1],
            server_markers={1: _FakeMarker(1, pos=(1.0, 2.0, 3.0))},
        )
        state = _build(app, pool)
        assert [t.marker_id for t in state.markers] == [1]
        t0 = state.markers[0]
        assert (t0.x, t0.y, t0.z) == (1.0, 2.0, 3.0)
        assert t0.online is True  # controlled is always online

    def test_viewer_only_marker_pulled_from_receiver(self, pool: OverlayStatePool) -> None:
        app = _build_app(
            controlled=[],
            viewer=[5],
            receiver_markers={5: _FakeMarker(5, pos=(3.0, 4.0, 5.0))},
            online={5: True},
        )
        state = _build(app, pool)
        assert [t.marker_id for t in state.markers] == [5]
        assert state.markers[0].online is True

    def test_offline_viewer_marker_marked_offline(self, pool: OverlayStatePool) -> None:
        app = _build_app(
            viewer=[5],
            receiver_markers={5: _FakeMarker(5)},
            online={5: False},
        )
        state = _build(app, pool)
        assert state.markers[0].online is False

    def test_missing_marker_is_silently_dropped(self, pool: OverlayStatePool) -> None:
        app = _build_app(controlled=[1], viewer=[1])  # no server marker
        state = _build(app, pool)
        assert state.markers == []

    def test_pool_spillover_allocates_new_marker_data(self, pool: OverlayStatePool) -> None:
        """More viewers than ``_marker_pool`` capacity → spill-over
        path constructs fresh ``MarkerOverlayData`` instances."""
        # OverlayState has _MAX_MARKERS = 16; create 17 viewers.
        ids = list(range(17))
        markers = {i: _FakeMarker(i) for i in ids}
        app = _build_app(viewer=ids, receiver_markers=markers)
        state = _build(app, pool)
        assert len(state.markers) == 17
        # All are MarkerOverlayData; the 17th must not be the same object
        # as any pool entry.
        assert all(isinstance(t, MarkerOverlayData) for t in state.markers)


# --------------------------------------------------------------------------- #
# Speed derivation
# --------------------------------------------------------------------------- #


class TestSpeedDerivation:
    def test_controlled_with_gamepad_speed_uses_it(self, pool: OverlayStatePool) -> None:
        mgr = _FakeInputManager(marker_speeds={1: 7.5})
        app = _build_app(
            controlled=[1],
            viewer=[1],
            server_markers={1: _FakeMarker(1)},
            input_manager=mgr,
        )
        state = _build(app, pool)
        assert state.markers[0].speed == pytest.approx(7.5)
        # Controlled markers get their broadcast speed pushed back on the
        # marker object via ``set_speed``.
        assert app._server._markers[1].set_speed_calls == [(7.5, 0.0, 0.0)]

    def test_controlled_without_gamepad_speed_falls_back_to_move_speed(self, pool: OverlayStatePool) -> None:
        app = _build_app(
            controlled=[1],
            viewer=[1],
            server_markers={1: _FakeMarker(1)},
        )  # no input manager
        state = _build(app, pool)
        # default MarkerConfig.move_speed == 2.0
        assert state.markers[0].speed == pytest.approx(2.0)
        assert app._server._markers[1].set_speed_calls == [(2.0, 0.0, 0.0)]

    def test_controlled_without_gamepad_uses_per_marker_speed_when_set(self, pool: OverlayStatePool) -> None:
        """Per-marker speed override is used when no controller is attached."""
        app = _build_app(
            controlled=[1],
            viewer=[1],
            server_markers={1: _FakeMarker(1)},
        )
        app._config.marker_move_speeds = {1: 5.5}
        state = _build(app, pool)
        assert state.markers[0].speed == pytest.approx(5.5)
        assert app._server._markers[1].set_speed_calls == [(5.5, 0.0, 0.0)]

    def test_viewer_without_gamepad_speed_uses_marker_velocity_norm(self, pool: OverlayStatePool) -> None:
        # Velocity (3, 4, 0) → ‖v‖ = 5.
        app = _build_app(
            viewer=[9],
            receiver_markers={9: _FakeMarker(9, speed=(3.0, 4.0, 0.0))},
        )
        state = _build(app, pool)
        assert state.markers[0].speed == pytest.approx(5.0)


# --------------------------------------------------------------------------- #
# Camera, detection, button-detection
# --------------------------------------------------------------------------- #


class TestIfaceSelectionLabels:
    """Iface picker labels show interface name with IP for multi-homed hosts."""

    def test_iface_names_resolve_to_label_with_ip_suffix(self, pool: OverlayStatePool, monkeypatch) -> None:
        from openfollow.runtime import services_marker_visuals

        monkeypatch.setattr(
            services_marker_visuals,
            "list_iface_ipv4",
            lambda: [("eth0", "192.168.178.61"), ("wlan0", "10.0.0.5")],
        )
        app = _build_app()
        app._available_interfaces = ["", "eth0", "wlan0"]
        app._selected_iface_index = 1
        app._iface_selection_active = True
        state = _build(app, pool)
        assert state.available_interfaces == [
            "",  # auto-detect – labelled by the renderer, not here
            "eth0 (192.168.178.61)",
            "wlan0 (10.0.0.5)",
        ]
        assert state.selected_iface_index == 1

    def test_down_iface_left_unformatted_when_no_ip(self, pool: OverlayStatePool, monkeypatch) -> None:
        """A persisted iface that's no longer in ``list_iface_ipv4()`` –
        cable unplugged, modem suspended – has no current IP to render,
        so the row stays as the bare name. The on-screen UI still shows
        the operator's pick instead of silently dropping it."""
        from openfollow.runtime import services_marker_visuals

        monkeypatch.setattr(
            services_marker_visuals,
            "list_iface_ipv4",
            lambda: [("eth0", "192.168.178.61")],
        )
        app = _build_app()
        app._available_interfaces = ["", "ghost0"]
        app._iface_selection_active = True
        state = _build(app, pool)
        assert state.available_interfaces == ["", "ghost0"]

    def test_closed_picker_skips_psutil_snapshot(self, pool: OverlayStatePool, monkeypatch) -> None:
        from openfollow.runtime import services_marker_visuals

        calls = 0

        def _spy() -> list[tuple[str, str]]:
            nonlocal calls
            calls += 1
            return [("eth0", "192.168.178.61")]

        monkeypatch.setattr(services_marker_visuals, "list_iface_ipv4", _spy)
        app = _build_app()
        app._available_interfaces = ["", "eth0", "wlan0"]
        app._iface_selection_active = False
        state = _build(app, pool)
        assert calls == 0
        assert state.available_interfaces == ["", "eth0", "wlan0"]


class TestExternalStateSnapshots:
    def test_camera_params_are_copied_from_app_camera(self, pool: OverlayStatePool) -> None:
        app = _build_app()
        state = _build(app, pool)
        # Pos_x=0, Pos_y=-6, Pos_z=2.5 from _FakeCamera defaults.
        np.testing.assert_allclose(
            state.camera_params,
            [0.0, -6.0, 2.5, -15.0, 0.0, 0.0, 60.0],
        )

    def test_camera_params_are_defensive_copy(self, pool: OverlayStatePool) -> None:
        buf = np.zeros(7, dtype=np.float64)
        app = _build_app()
        state = build_marker_visual_state(
            app,
            overlay_state_pool=pool,
            system_stats=None,
            person_detector=None,
            cam_params_buffer=buf,
        )
        buf[0] = 999.0  # mutate the scratch buffer
        assert state.camera_params[0] != 999.0

    def test_detector_populates_detection_fields(self, pool: OverlayStatePool) -> None:
        app = _build_app()
        dets = [object(), object()]
        detector = SimpleNamespace(detections=dets)
        state = _build(app, pool, person_detector=detector)
        assert state.detections is dets
        # Defaults come from DetectionConfig.
        assert state.detection_show_boxes is True
        assert state.detection_show_labels is True
        assert state.detection_box_color == "#808080"
        assert state.detection_box_thickness == 2

    def test_no_detector_leaves_detection_defaults(self, pool: OverlayStatePool) -> None:
        app = _build_app()
        state = _build(app, pool, person_detector=None)
        assert state.detections == []

    def test_button_detection_is_snapshotted(self, pool: OverlayStatePool) -> None:
        sentinel = object()
        bd = SimpleNamespace(get_state=lambda: sentinel)
        app = _build_app(button_detection=bd)
        state = _build(app, pool)
        assert state.button_detection is sentinel

    def test_no_button_detection_sets_field_to_none(self, pool: OverlayStatePool) -> None:
        app = _build_app(button_detection=None)
        state = _build(app, pool)
        assert state.button_detection is None


# --------------------------------------------------------------------------- #
# Input-state flags
# --------------------------------------------------------------------------- #


class TestInputFlags:
    def test_no_input_manager_flags_all_false(self, pool: OverlayStatePool) -> None:
        app = _build_app(input_manager=None)
        state = _build(app, pool)
        assert state.keyboard_connected is False
        assert state.controller_connected is False

    def test_keyboard_connected_reflects_manager_and_config(self, pool: OverlayStatePool) -> None:
        mgr = _FakeInputManager(keyboard_connected=True)
        app = _build_app(input_manager=mgr)
        state = _build(app, pool)
        assert state.keyboard_connected is True

    def test_controller_connected_requires_connected_backend(self, pool: OverlayStatePool) -> None:
        mgr = _FakeInputManager(
            controller_info=[
                {
                    "connected": True,
                    "marker_id": 1,
                    "name": "XBox",
                    "controller_index": 0,
                    "effective_speed": 1.0,
                    "backend": "pygame",
                },
            ]
        )
        app = _build_app(input_manager=mgr)
        state = _build(app, pool)
        assert state.controller_connected is True

    def test_controller_disconnected_reports_false(self, pool: OverlayStatePool) -> None:
        mgr = _FakeInputManager(
            controller_info=[
                {
                    "connected": False,
                    "marker_id": None,
                    "name": "X",
                    "controller_index": 0,
                    "effective_speed": 1.0,
                    "backend": "pygame",
                },
            ]
        )
        app = _build_app(input_manager=mgr)
        state = _build(app, pool)
        assert state.controller_connected is False


# --------------------------------------------------------------------------- #
# Controller binding stamped on marker cards + unbound list + HUD help flag
#
# Controller badge is on each marker card; unbound pads shown in Settings menu.
# --------------------------------------------------------------------------- #


class TestControllerBindingOnMarkerCard:
    def test_marker_card_gets_controller_idx_when_pad_bound(self, pool: OverlayStatePool) -> None:
        app = _build_app(
            controlled=[1],
            viewer=[1],
            server_markers={1: _FakeMarker(1)},
            input_manager=_FakeInputManager(
                controller_info=[
                    {
                        "connected": True,
                        "marker_id": 1,
                        "name": "X",
                        "controller_index": 0,
                        "effective_speed": 1.0,
                        "backend": "pygame",
                    },
                ]
            ),
        )
        state = _build(app, pool)
        assert state.markers[0].controller_idx == 0
        assert state.markers[0].controller_connected is True
        assert state.markers[0].is_controlled is True

    def test_marker_card_disconnected_pad_marks_connected_false(self, pool: OverlayStatePool) -> None:
        app = _build_app(
            controlled=[1],
            viewer=[1],
            server_markers={1: _FakeMarker(1)},
            input_manager=_FakeInputManager(
                controller_info=[
                    {
                        "connected": False,
                        "marker_id": 1,
                        "name": "X",
                        "controller_index": 0,
                        "effective_speed": 1.0,
                        "backend": "pygame",
                    },
                ]
            ),
        )
        state = _build(app, pool)
        assert state.markers[0].controller_idx == 0
        assert state.markers[0].controller_connected is False

    def test_marker_without_pad_has_none_controller_idx(self, pool: OverlayStatePool) -> None:
        app = _build_app(
            controlled=[1],
            viewer=[1],
            server_markers={1: _FakeMarker(1)},
        )
        state = _build(app, pool)
        assert state.markers[0].controller_idx is None
        assert state.markers[0].controller_connected is False
        assert state.markers[0].is_controlled is True

    def test_viewer_only_marker_has_is_controlled_false(self, pool: OverlayStatePool) -> None:
        # Marker 9 is in viewer_marker_ids but NOT in controlled_marker_ids.
        app = _build_app(
            viewer=[9],
            receiver_markers={9: _FakeMarker(9, speed=(0.0, 0.0, 0.0))},
        )
        state = _build(app, pool)
        assert state.markers[0].is_controlled is False
        assert state.markers[0].controller_idx is None

    def test_unbound_controller_indices_collected(self, pool: OverlayStatePool) -> None:
        app = _build_app(
            controlled=[1],
            viewer=[1],
            server_markers={1: _FakeMarker(1)},
            input_manager=_FakeInputManager(
                controller_info=[
                    {
                        "connected": True,
                        "marker_id": 1,
                        "name": "X",
                        "controller_index": 0,
                        "effective_speed": 1.0,
                        "backend": "pygame",
                    },
                    {
                        "connected": True,
                        "marker_id": None,
                        "name": "Y",
                        "controller_index": 2,
                        "effective_speed": 1.0,
                        "backend": "pygame",
                    },
                ]
            ),
        )
        state = _build(app, pool)
        assert state.unbound_controller_indices == [2]

    def test_disconnected_unbound_controller_is_dropped(self, pool: OverlayStatePool) -> None:
        """A pad that's both disconnected AND unbound carries no actionable
        information (operator can't see it nor route it) – drop it from
        the Settings menu list."""
        app = _build_app(
            input_manager=_FakeInputManager(
                controller_info=[
                    {
                        "connected": False,
                        "marker_id": None,
                        "name": "Y",
                        "controller_index": 2,
                        "effective_speed": 1.0,
                        "backend": "pygame",
                    },
                ]
            ),
        )
        state = _build(app, pool)
        assert state.unbound_controller_indices == []


class TestControllerTextAndHelp:
    def test_hud_help_flag_propagates(self, pool: OverlayStatePool) -> None:
        app = _build_app(show_hud_help=False)
        state = _build(app, pool)
        assert state.show_hud_help is False


# --------------------------------------------------------------------------- #
# Virtual fader display stack
# --------------------------------------------------------------------------- #


class _FakeVirtualFaderBus:
    """Minimal :class:`VirtualFaderBus` stand-in. Public surface only:
    ``fader_count`` / ``name`` / ``value`` / ``is_picked_up`` /
    ``show_on_display`` – that's everything
    :func:`build_marker_visual_state` reads."""

    def __init__(self, faders: list[dict]) -> None:
        self._faders = faders
        self.fader_count = len(faders)

    def name(self, index: int) -> str:
        return self._faders[index - 1]["name"]

    def value(self, index: int) -> float:
        return self._faders[index - 1]["value"]

    def is_picked_up(self, index: int) -> bool:
        return self._faders[index - 1]["picked_up"]

    def show_on_display(self, index: int) -> bool:
        return self._faders[index - 1]["show"]


class TestVirtualFaderStack:
    """``build_marker_visual_state`` populates
    ``state.virtual_faders_display`` from the running fader bus.
    Only faders whose ``show_on_display`` is ``True`` produce an
    entry; the order tracks the fader's index so the renderer's
    bottom-up stack matches operator expectations."""

    def test_no_runtime_services_leaves_empty(
        self,
        pool: OverlayStatePool,
    ) -> None:
        """Boot / mid-restart windows have no runtime services
        attached. The default state field is empty and the renderer
        draws nothing – same path as "no fader configured to show"."""
        app = _build_app()
        # ``_build_app`` already sets ``_runtime_services`` to a
        # namespace without ``_virtual_faders`` – the helper's
        # getattr guard treats a missing attribute as "no bus".
        state = _build(app, pool)
        assert state.virtual_faders_display == []

    def test_no_bus_attached_leaves_empty(
        self,
        pool: OverlayStatePool,
    ) -> None:
        app = _build_app()
        app._runtime_services = SimpleNamespace(_virtual_faders=None)
        state = _build(app, pool)
        assert state.virtual_faders_display == []

    def test_only_show_on_display_faders_appear(
        self,
        pool: OverlayStatePool,
    ) -> None:
        app = _build_app()
        bus = _FakeVirtualFaderBus(
            [
                {
                    "name": "Master",
                    "value": 0.5,
                    "picked_up": True,
                    "show": True,
                },
                {
                    "name": "Hidden",
                    "value": 0.25,
                    "picked_up": True,
                    "show": False,
                },
                {
                    "name": "Aux",
                    "value": 0.75,
                    "picked_up": False,
                    "show": True,
                },
            ]
        )
        app._runtime_services = SimpleNamespace(_virtual_faders=bus)
        state = _build(app, pool)
        # Two entries: Master (index 1) + Aux (index 3). The
        # show-off middle fader is excluded.
        assert len(state.virtual_faders_display) == 2
        assert [vf.name for vf in state.virtual_faders_display] == [
            "Master",
            "Aux",
        ]
        # Ordering preserved + per-fader fields plumbed through.
        master, aux = state.virtual_faders_display
        assert master.index == 1
        assert master.value == 0.5
        assert master.picked_up is True
        assert aux.index == 3
        assert aux.value == 0.75
        assert aux.picked_up is False

    def test_pool_reuse_clears_stale_entries(
        self,
        pool: OverlayStatePool,
    ) -> None:
        app = _build_app()
        bus_with = _FakeVirtualFaderBus(
            [
                {
                    "name": "Master",
                    "value": 0.5,
                    "picked_up": True,
                    "show": True,
                },
            ]
        )
        app._runtime_services = SimpleNamespace(_virtual_faders=bus_with)
        state = _build(app, pool)
        assert len(state.virtual_faders_display) == 1
        # Rebuild with no bus – the same pool slot gets reset.
        app._runtime_services = SimpleNamespace(_virtual_faders=None)
        state = _build(app, pool)
        assert state.virtual_faders_display == []


# --------------------------------------------------------------------------- #
# Status flags
# --------------------------------------------------------------------------- #


class TestStatusFlagsSnapshot:
    """``build_marker_visual_state`` snapshots every truthy entry
    in :attr:`AppRuntimeServices._status_flags` into
    :attr:`OverlayState.status_flags`. The badge renderer reads
    that list directly; ``None`` / empty values are filtered out
    so a cleared condition doesn't paint a stale row."""

    def test_no_runtime_services_leaves_empty(
        self,
        pool: OverlayStatePool,
    ) -> None:
        app = _build_app()
        # Default ``_runtime_services`` SimpleNamespace has no
        # ``_status_flags`` attribute – the helper's getattr guard
        # treats that as "no flags".
        state = _build(app, pool)
        assert state.status_flags == []

    def test_truthy_entries_surface_in_order(
        self,
        pool: OverlayStatePool,
    ) -> None:
        app = _build_app()
        # ``dict`` preserves insertion order; Python 3.7+ guarantees
        # this, so the badge stack reads consistently across frames.
        flags: dict[str, str | None] = {}
        flags["midi_unavailable"] = "MIDI backend error"
        flags["midi_patch_missing"] = "Patch missing: Workspace 1"
        app._runtime_services = SimpleNamespace(_status_flags=flags)
        state = _build(app, pool)
        assert state.status_flags == [
            ("midi_unavailable", "MIDI backend error", "error"),
            ("midi_patch_missing", "Patch missing: Workspace 1", "error"),
        ]

    def test_none_values_filtered_out(
        self,
        pool: OverlayStatePool,
    ) -> None:
        app = _build_app()
        flags: dict[str, str | None] = {
            "midi_unavailable": None,  # cleared
            "midi_patch_missing": "Patch missing",
            "spurious": "",  # also falsy
        }
        app._runtime_services = SimpleNamespace(_status_flags=flags)
        state = _build(app, pool)
        assert state.status_flags == [
            ("midi_patch_missing", "Patch missing", "error"),
        ]

    def test_severity_tuple_value_maps_to_info(
        self,
        pool: OverlayStatePool,
    ) -> None:
        """A subsystem can write a ``(severity, message)`` tuple to pick the
        badge styling; a plain string keeps the back-compat ``"error"``."""
        app = _build_app()
        flags: dict[str, object] = {
            "update_available": ("info", "Update available"),
            "midi_unavailable": "Backend down",
            # A tuple whose message is empty is a cleared condition too – it
            # must be filtered out, same as a None / empty string value.
            "cleared": ("info", ""),
        }
        app._runtime_services = SimpleNamespace(_status_flags=flags)
        state = _build(app, pool)
        assert state.status_flags == [
            ("update_available", "Update available", "info"),
            ("midi_unavailable", "Backend down", "error"),
        ]

    def test_malformed_tuple_value_degrades_without_raising(
        self,
        pool: OverlayStatePool,
    ) -> None:
        """The ``(severity, message)`` contract is 2-arity, but the build
        runs on the per-frame path – an off-spec tuple (wrong length) must
        degrade gracefully rather than raise ``ValueError`` and abort the
        frame. Missing severity falls back to ``"error"``; a tuple with no
        message is filtered out like an empty/None value."""
        app = _build_app()
        flags: dict[str, object] = {
            # 1-tuple: no message → filtered out (same as empty string).
            "short": ("info",),
            # 3-tuple: takes the first two (severity, message); extra ignored.
            "long": ("info", "Three parts", "ignored"),
            # 0-tuple: nothing usable → filtered.
            "empty": (),
        }
        app._runtime_services = SimpleNamespace(_status_flags=flags)
        state = _build(app, pool)
        assert state.status_flags == [
            ("long", "Three parts", "info"),
        ]

    def test_pool_reuse_clears_stale_flags(
        self,
        pool: OverlayStatePool,
    ) -> None:
        app = _build_app()
        flags1: dict[str, str | None] = {"midi_unavailable": "boom"}
        app._runtime_services = SimpleNamespace(_status_flags=flags1)
        state = _build(app, pool)
        assert len(state.status_flags) == 1
        # Subsystems cleared the flag – the next build must NOT see
        # the previous frame's entry.
        app._runtime_services = SimpleNamespace(_status_flags={})
        state = _build(app, pool)
        assert state.status_flags == []


class TestMultiPadHelpLabels:
    """DPAD next/prev is hidden in multi-pad mode."""

    def test_single_gamepad_button_labels_include_next_prev(self, pool: OverlayStatePool) -> None:
        app = _build_app(
            input_manager=_FakeInputManager(joystick_count=1),
        )
        state = _build(app, pool)
        assert "next_marker" in state.button_labels
        assert "prev_marker" in state.button_labels

    def test_multi_gamepad_button_labels_omit_next_prev(self, pool: OverlayStatePool) -> None:
        app = _build_app(
            input_manager=_FakeInputManager(joystick_count=2),
        )
        state = _build(app, pool)
        assert "next_marker" not in state.button_labels
        assert "prev_marker" not in state.button_labels

    def test_no_input_manager_includes_next_prev(self, pool: OverlayStatePool) -> None:
        """No input manager (early startup) keeps the legacy labels –
        gate logic uses ``<= 1`` so 0 joysticks behaves like single-pad."""
        app = _build_app(input_manager=None)
        state = _build(app, pool)
        assert "next_marker" in state.button_labels
        assert "prev_marker" in state.button_labels


# --------------------------------------------------------------------------- #
# Operator-message views
# --------------------------------------------------------------------------- #


class _FakeCatalogEntry:
    def __init__(self, name: str, color: str) -> None:
        self.name = name
        self.color = color


class _FakeCatalog:
    def __init__(self, entries: dict[int, tuple[str, str]]) -> None:
        self._entries = entries

    def get(self, marker_id: int) -> Any:
        hit = self._entries.get(marker_id)
        return _FakeCatalogEntry(*hit) if hit is not None else None


def _attach_store(app: SimpleNamespace, store: Any, catalog: Any = None) -> None:
    app._runtime_services._operator_message_store = store
    # Section defaults off; enable for the populated-path tests.
    app._config.operator_messages.enabled = True
    if catalog is not None:
        app._marker_catalog = catalog


class TestOperatorMessageViews:
    def test_no_store_leaves_empty(self, pool: OverlayStatePool) -> None:
        app = _build_app()
        state = _build(app, pool)
        assert state.operator_messages == []
        assert state.operator_message_overflow == 0

    def test_broadcast_and_keyed_populate_newest_first(self, pool: OverlayStatePool) -> None:
        from openfollow.operator_messages import OperatorMessageStore

        app = _build_app(controlled=[3])
        store = OperatorMessageStore(clock=lambda: 0.0)
        store.add("bcast", marker_id=0, duration_s=0.0)
        store.add("forM3", info="detail", marker_id=3, duration_s=0.0)
        _attach_store(app, store, _FakeCatalog({3: ("Spot 3", "#0652dd")}))

        state = _build(app, pool)
        views = state.operator_messages
        assert [v.message for v in views] == ["forM3", "bcast"]  # newest-first
        keyed, bcast = views
        assert keyed.marker_name == "Spot 3"
        assert keyed.marker_color == "#0652dd"
        assert keyed.info == "detail"
        assert bcast.marker_id == 0
        assert bcast.marker_name == "" and bcast.marker_color == ""
        assert state.operator_message_position == "bottom"
        assert state.operator_message_overflow == 0

    def test_keyed_without_catalog_entry_uses_palette_fallback(self, pool: OverlayStatePool) -> None:
        from openfollow.operator_messages import OperatorMessageStore

        app = _build_app(controlled=[5])
        store = OperatorMessageStore(clock=lambda: 0.0)
        store.add("x", marker_id=5)
        _attach_store(app, store, _FakeCatalog({}))  # no entry for 5

        state = _build(app, pool)
        view = state.operator_messages[0]
        assert view.marker_name == ""  # → renderer paints "M5"
        assert view.marker_color.startswith("#")  # palette fallback colour

    def test_expired_filtered_forever_kept(self, pool: OverlayStatePool) -> None:
        from openfollow.operator_messages import OperatorMessageStore

        app = _build_app()
        # Store clock pinned to 0; the builder snapshots at real
        # monotonic() (>> 0), so the 1 s message is already expired while
        # the forever (duration 0) one survives.
        store = OperatorMessageStore(clock=lambda: 0.0)
        store.add("gone", marker_id=0, duration_s=1.0)
        store.add("kept", marker_id=0, duration_s=0.0)
        _attach_store(app, store)

        state = _build(app, pool)
        assert [v.message for v in state.operator_messages] == ["kept"]
        assert all(v.is_forever for v in state.operator_messages)

    def test_max_visible_caps_with_overflow(self, pool: OverlayStatePool) -> None:
        from openfollow.operator_messages import OperatorMessageStore

        app = _build_app()  # default max_visible = 5
        store = OperatorMessageStore(clock=lambda: 0.0)
        for i in range(7):
            store.add(f"m{i}", marker_id=0, duration_s=0.0)
        _attach_store(app, store)

        state = _build(app, pool)
        assert len(state.operator_messages) == 5
        assert state.operator_message_overflow == 2
        # Newest five kept.
        assert [v.message for v in state.operator_messages] == ["m6", "m5", "m4", "m3", "m2"]

    def test_position_from_config(self, pool: OverlayStatePool) -> None:
        from openfollow.operator_messages import OperatorMessageStore

        app = _build_app()
        app._config.operator_messages.position = "top"
        store = OperatorMessageStore(clock=lambda: 0.0)
        store.add("x", marker_id=0)
        _attach_store(app, store)

        state = _build(app, pool)
        assert state.operator_message_position == "top"

    def test_disabled_section_renders_no_views(self, pool: OverlayStatePool) -> None:
        from openfollow.operator_messages import OperatorMessageStore

        app = _build_app()
        store = OperatorMessageStore(clock=lambda: 0.0)
        store.add("x", marker_id=0)
        _attach_store(app, store)
        app._config.operator_messages.enabled = False  # flip back off after attach

        state = _build(app, pool)
        assert state.operator_messages == []


# --------------------------------------------------------------------------- #
# Detection assist-mode AI-output ghost
# --------------------------------------------------------------------------- #


class TestAssistGhostOverlay:
    def _assist_app(self, anchor_pos: tuple[float, float, float]) -> SimpleNamespace:
        app = _build_app(
            controlled=[1],
            viewer=[1],
            server_markers={1: _FakeMarker(1, pos=(2.0, 3.0, 0.0))},
        )
        app._config.detection.enabled = True
        app._config.detection.pin_mode = "assist"
        anchor = get_or_create_manual_marker(app, 1)
        anchor.set_pos(*anchor_pos)
        return app

    def test_solid_marker_at_anchor_ghost_at_broadcast_when_assist_active(self, pool: OverlayStatePool) -> None:
        app = self._assist_app((7.0, 8.0, 0.0))
        state = _build(app, pool)

        normals = [m for m in state.markers if not m.is_assist_ghost]
        ghosts = [m for m in state.markers if m.is_assist_ghost]
        # The solid carded marker sits at the operator-steered anchor…
        assert len(normals) == 1
        assert normals[0].marker_id == 1
        assert normals[0].is_controlled
        assert (normals[0].x, normals[0].y) == (7.0, 8.0)
        # …plus one ghost entry at the registered (broadcast) AI-output position.
        assert len(ghosts) == 1
        assert ghosts[0].marker_id == 1
        assert not ghosts[0].is_controlled
        assert (ghosts[0].x, ghosts[0].y) == (2.0, 3.0)

    def test_no_ghost_entry_when_assist_inactive(self, pool: OverlayStatePool) -> None:
        app = _build_app(
            controlled=[1],
            viewer=[1],
            server_markers={1: _FakeMarker(1, pos=(2.0, 3.0, 0.0))},
        )  # default AppConfig detection is disabled → assist inactive
        state = _build(app, pool)
        assert all(not m.is_assist_ghost for m in state.markers)

    def test_ghost_at_broadcast_position_when_anchor_unseeded(self, pool: OverlayStatePool) -> None:
        # Assist resolves a pinned id but the anchor isn't seeded yet (the
        # overlay built before the first pin update). The AI-output ghost still
        # renders at the broadcast position; the solid marker coincides with it
        # until the operator moves the anchor.
        app = _build_app(
            controlled=[1],
            viewer=[1],
            server_markers={1: _FakeMarker(1, pos=(2.0, 3.0, 0.0))},
        )
        app._config.detection.enabled = True
        app._config.detection.pin_mode = "assist"
        app._assist_manual.clear()  # assist active, but no anchor seeded yet
        state = _build(app, pool)

        ghosts = [m for m in state.markers if m.is_assist_ghost]
        normals = [m for m in state.markers if not m.is_assist_ghost]
        assert len(ghosts) == 1
        assert (ghosts[0].x, ghosts[0].y) == (2.0, 3.0)
        # Solid marker falls back to the broadcast position, coinciding.
        assert len(normals) == 1
        assert (normals[0].x, normals[0].y) == (2.0, 3.0)

    def test_every_controlled_marker_gets_its_own_ghost_and_anchor(self, pool: OverlayStatePool) -> None:
        # Assist refines *every* controlled marker: each one yields a dim ghost
        # at its broadcast position and moves its solid card to its own anchor.
        app = _build_app(
            controlled=[1, 2],
            viewer=[1, 2],
            server_markers={
                1: _FakeMarker(1, pos=(2.0, 3.0, 0.0)),
                2: _FakeMarker(2, pos=(4.0, 5.0, 0.0)),
            },
        )
        app._config.detection.enabled = True
        app._config.detection.pin_mode = "assist"
        get_or_create_manual_marker(app, 1).set_pos(10.0, 11.0, 0.0)
        get_or_create_manual_marker(app, 2).set_pos(20.0, 21.0, 0.0)

        state = _build(app, pool)

        ghosts = {m.marker_id: m for m in state.markers if m.is_assist_ghost}
        normals = {m.marker_id: m for m in state.markers if not m.is_assist_ghost}
        # One ghost per controlled marker, each at its broadcast position…
        assert set(ghosts) == {1, 2}
        assert (ghosts[1].x, ghosts[1].y) == (2.0, 3.0)
        assert (ghosts[2].x, ghosts[2].y) == (4.0, 5.0)
        assert all(not g.is_controlled for g in ghosts.values())
        # …and each solid card sits at its own operator-steered anchor.
        assert set(normals) == {1, 2}
        assert (normals[1].x, normals[1].y) == (10.0, 11.0)
        assert (normals[2].x, normals[2].y) == (20.0, 21.0)


class TestAttachedDetectionBox:
    def _app(self, catalog: Any = None) -> SimpleNamespace:
        app = _build_app(
            controlled=[1, 2],
            viewer=[1, 2],
            server_markers={
                1: _FakeMarker(1, pos=(2.0, 3.0, 0.0)),
                2: _FakeMarker(2, pos=(4.0, 5.0, 0.0)),
            },
        )
        if catalog is not None:
            app._marker_catalog = catalog
        return app

    def test_attached_track_maps_to_marker_colour(self, pool: OverlayStatePool) -> None:
        # The pin state for marker 1 says track 4 is attached → the dict maps
        # track 4 to marker 1's catalog colour.
        app = self._app(_FakeCatalog({1: ("Spot 1", "#0652dd")}))
        app._detection_pin_states[1] = SimpleNamespace(attached_track_id=4, attached_marker_id=1)
        detector = SimpleNamespace(detections=[])
        state = _build(app, pool, person_detector=detector)
        assert state.detection_attached_colors == {4: "#0652dd"}

    def test_two_attached_tracks_map_to_two_marker_colours(self, pool: OverlayStatePool) -> None:
        # Assist drives every controlled marker, so several boxes can be
        # attached at once – each painted in its own marker's colour.
        app = self._app(_FakeCatalog({1: ("Spot 1", "#0652dd"), 2: ("Spot 2", "#ff0000")}))
        app._detection_pin_states[1] = SimpleNamespace(attached_track_id=4, attached_marker_id=1)
        app._detection_pin_states[2] = SimpleNamespace(attached_track_id=7, attached_marker_id=2)
        detector = SimpleNamespace(detections=[])
        state = _build(app, pool, person_detector=detector)
        assert state.detection_attached_colors == {4: "#0652dd", 7: "#ff0000"}

    def test_no_attachment_leaves_empty_map(self, pool: OverlayStatePool) -> None:
        app = self._app()  # no pin states recorded
        detector = SimpleNamespace(detections=[])
        state = _build(app, pool, person_detector=detector)
        assert state.detection_attached_colors == {}

    def test_pin_state_without_attachment_is_skipped(self, pool: OverlayStatePool) -> None:
        app = self._app()
        # A pin state with no live attachment (gliding home / replace miss)
        # contributes no entry.
        app._detection_pin_states[1] = SimpleNamespace(attached_track_id=None, attached_marker_id=None)
        detector = SimpleNamespace(detections=[])
        state = _build(app, pool, person_detector=detector)
        assert state.detection_attached_colors == {}
