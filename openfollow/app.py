# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""OpenFollowApp: top-level GTK application object.

Owns config load/hot-reload, the marker catalog, and all on-screen modal
state (settings menu, source/iface/URL pickers, Pi network editor). Wires
``AppRuntimeServices`` for subsystem startup and the per-frame loop, and
delegates input/mode handling to ``openfollow.runtime.app_modes`` and
orchestration to ``openfollow.runtime.app_orchestration``.
"""

from __future__ import annotations

import logging
import os
import threading
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from openfollow.configuration import (
    AppConfig,
    bootstrap_config_if_missing,
    config_write_lock,
    load_config,
    save_config,
)
from openfollow.marker_catalog import (
    MarkerCatalog,
    derive_station_name,
    load_catalog,
    save_catalog,
)
from openfollow.palette import AUTO_PICK_ORDER as _PALETTE_AUTO_PICK_ORDER
from openfollow.runtime.app_commands import (
    check_button_detection_request as runtime_check_button_detection_request,
)
from openfollow.runtime.app_commands import (
    check_restart_request as runtime_check_restart_request,
)
from openfollow.runtime.app_commands import (
    check_update_request as runtime_check_update_request,
)
from openfollow.runtime.app_commands import (
    restart_app as runtime_restart_app,
)
from openfollow.runtime.app_modes import (
    adjust_move_speed as runtime_adjust_move_speed,
)
from openfollow.runtime.app_modes import (
    cancel_field_choice_picker as runtime_cancel_field_choice_picker,
)
from openfollow.runtime.app_modes import (
    cancel_url_editor as runtime_cancel_url_editor,
)
from openfollow.runtime.app_modes import (
    check_video_disconnect_banner as runtime_check_video_disconnect_banner,
)
from openfollow.runtime.app_modes import (
    confirm_field_choice_picker as runtime_confirm_field_choice_picker,
)
from openfollow.runtime.app_modes import (
    confirm_iface_selection as runtime_confirm_iface_selection,
)
from openfollow.runtime.app_modes import (
    confirm_source_type_selection as runtime_confirm_source_type_selection,
)
from openfollow.runtime.app_modes import (
    confirm_url_editor as runtime_confirm_url_editor,
)
from openfollow.runtime.app_modes import (
    enter_browser as runtime_enter_browser,
)
from openfollow.runtime.app_modes import (
    enter_button_detection as runtime_enter_button_detection,
)
from openfollow.runtime.app_modes import (
    enter_field_choice_picker as runtime_enter_field_choice_picker,
)
from openfollow.runtime.app_modes import (
    enter_iface_selection as runtime_enter_iface_selection,
)
from openfollow.runtime.app_modes import (
    enter_settings_menu as runtime_enter_settings_menu,
)
from openfollow.runtime.app_modes import (
    enter_source_selection as runtime_enter_source_selection,
)
from openfollow.runtime.app_modes import (
    enter_source_type_selection as runtime_enter_source_type_selection,
)
from openfollow.runtime.app_modes import (
    enter_url_editor as runtime_enter_url_editor,
)
from openfollow.runtime.app_modes import (
    exit_browser as runtime_exit_browser,
)
from openfollow.runtime.app_modes import (
    exit_button_detection as runtime_exit_button_detection,
)
from openfollow.runtime.app_modes import (
    exit_field_choice_picker as runtime_exit_field_choice_picker,
)
from openfollow.runtime.app_modes import (
    exit_settings_menu as runtime_exit_settings_menu,
)
from openfollow.runtime.app_modes import (
    exit_source_type_selection as runtime_exit_source_type_selection,
)
from openfollow.runtime.app_modes import (
    exit_url_editor as runtime_exit_url_editor,
)
from openfollow.runtime.app_modes import (
    get_default_marker_position as runtime_get_default_marker_position,
)
from openfollow.runtime.app_modes import (
    handle_key_press as runtime_handle_key_press,
)
from openfollow.runtime.app_modes import (
    normalize_key as runtime_normalize_key,
)
from openfollow.runtime.app_modes import (
    on_key_down as runtime_on_key_down,
)
from openfollow.runtime.app_modes import (
    on_key_up as runtime_on_key_up,
)
from openfollow.runtime.app_modes import (
    on_pointer_down as runtime_on_pointer_down,
)
from openfollow.runtime.app_modes import (
    on_pointer_move as runtime_on_pointer_move,
)
from openfollow.runtime.app_modes import (
    on_pointer_up as runtime_on_pointer_up,
)
from openfollow.runtime.app_modes import (
    on_resize as runtime_on_resize,
)
from openfollow.runtime.app_modes import (
    on_wheel as runtime_on_wheel,
)
from openfollow.runtime.app_modes import (
    process_browser_input as runtime_process_browser_input,
)
from openfollow.runtime.app_modes import (
    process_button_detection as runtime_process_button_detection,
)
from openfollow.runtime.app_modes import (
    process_field_choice_picker_input as runtime_process_field_choice_picker_input,
)
from openfollow.runtime.app_modes import (
    process_iface_selection_input as runtime_process_iface_selection_input,
)
from openfollow.runtime.app_modes import (
    process_input as runtime_process_input,
)
from openfollow.runtime.app_modes import (
    process_settings_menu_input as runtime_process_settings_menu_input,
)
from openfollow.runtime.app_modes import (
    process_source_selection_input as runtime_process_source_selection_input,
)
from openfollow.runtime.app_modes import (
    process_source_type_selection_input as runtime_process_source_type_selection_input,
)
from openfollow.runtime.app_modes import (
    refresh_iface_list as runtime_refresh_iface_list,
)
from openfollow.runtime.app_orchestration import (
    animate as runtime_animate,
)
from openfollow.runtime.app_orchestration import (
    check_config_reload as runtime_check_config_reload,
)
from openfollow.runtime.app_orchestration import (
    check_marker_speeds_persist as runtime_check_marker_speeds_persist,
)
from openfollow.runtime.app_orchestration import (
    get_config_mtime as runtime_get_config_mtime,
)
from openfollow.runtime.app_orchestration import (
    housekeeping as runtime_housekeeping,
)
from openfollow.runtime.app_orchestration import (
    run_native_loop as runtime_run_native_loop,
)
from openfollow.runtime.deb_update import (
    run_deb_update as runtime_run_deb_update,
)
from openfollow.runtime.deb_update import (
    run_local_update as runtime_run_local_update,
)
from openfollow.services import AppRuntimeServices, WebCommandQueue

if TYPE_CHECKING:
    from openfollow.input import InputManager
    from openfollow.input.button_detection import ButtonDetectionWizard
    from openfollow.logging_setup import RingBufferLogHandler
    from openfollow.otp import OtpServer
    from openfollow.psn import Marker, PsnReceiver, PsnServer
    from openfollow.rttrpm import RttrpmServer
    from openfollow.runtime.services_detection_pin import DetectionPinState
    from openfollow.scene.camera import Camera
    from openfollow.video.receiver import GstNativeSinkReceiver
    from openfollow.web import ConfigWebServer
    from openfollow.window import GtkNativeSinkWindow

logger = logging.getLogger(__name__)


class OpenFollowApp:
    """Main GUI application: video input + 3D marker overlay."""

    def __init__(
        self,
        config_path: str = "config.toml",
        *,
        log_ring: RingBufferLogHandler | None = None,
    ) -> None:
        self._config_path: str = os.path.abspath(config_path)
        self._log_ring = log_ring
        bootstrap_config_if_missing(self._config_path)
        self._config: AppConfig = load_config(self._config_path)
        self._bootstrap_station_identity()
        self._marker_catalog: MarkerCatalog = self._load_or_seed_marker_catalog()
        self._marker_catalog_sync: Any = None
        self._config_mtime: float = self._get_config_mtime()
        self._last_config_check: float = 0.0
        self._web_commands: WebCommandQueue = WebCommandQueue()
        self._runtime_services: AppRuntimeServices = AppRuntimeServices(self)

        self._canvas: GtkNativeSinkWindow | None = None
        self._camera: Camera | None = None
        self._video_receiver: GstNativeSinkReceiver | None = None
        self._video_logged: bool = False
        self._server: PsnServer | None = None
        self._otp_server: OtpServer | None = None
        self._rttrpm_server: RttrpmServer | None = None
        self._controlled_ids: list[int] = []
        self._viewer_ids: list[int] = []
        self._selected_id: int | None = None
        # Assist-mode manual anchor markers, keyed by the pinned marker id.
        # These are operator-steered "ghosts" that are never registered with
        # ``_server`` (so never broadcast) – the detection pin reads them as the
        # clip anchor and writes the corrected position to the registered marker.
        self._assist_manual: dict[int, Marker] = {}
        # Per-marker detection-pin smoothing state, keyed by marker id. Assist
        # mode drives every controlled marker (one entry each); replace mode
        # drives the single resolved marker (one entry). Created lazily and
        # pruned when a marker leaves the driven set.
        self._detection_pin_states: dict[int, DetectionPinState] = {}
        self._psn_receiver: PsnReceiver | None = None
        self._web_server: ConfigWebServer | None = None
        self._input_manager: InputManager | None = None

        self._iface_selection_active: bool = False
        self._available_interfaces: list[str] = []
        self._selected_iface_index: int = 0
        self._last_iface_refresh: float = 0.0
        # ``time.perf_counter()`` of the previous animate tick (monotonic, not
        # wall clock); drives the real-elapsed frame dt.
        self._last_animate_time: float | None = None

        self._source_type_selection_active: bool = False
        self._available_source_types: list[tuple[str, str]] = []
        self._selected_source_type_index: int = 0

        self._url_editor_active: bool = False
        self._url_editor_field_name: str = ""
        self._url_editor_field_label: str = ""
        self._url_editor_value: str = ""
        self._url_editor_banner: str = ""
        self._url_editor_revert_type: str = ""

        self._field_choice_active: bool = False
        self._field_choice_field_name: str = ""
        self._field_choice_field_label: str = ""
        self._field_choice_options: list[tuple[str, str]] = []
        self._field_choice_items: list[str] = []
        self._field_choice_selected_index: int = 0
        self._field_choice_revert_type: str = ""

        self._browser_active: bool = False
        self._browser_overlay: Any | None = None
        self._browser_hidden_sink: Any | None = None

        self._video_disconnect_deadline: float = float("inf")
        self._video_disconnect_banner_shown: bool = False
        self._video_was_connected: bool = False
        # Settings menu is the one the startup disconnect check auto-opened.
        self._video_disconnect_menu_open: bool = False

        self._psn_source_resolved_ip: str = ""
        self._psn_source_status: str = ""
        self._psn_source_banner: str = ""

        self._settings_menu_active: bool = False
        self._settings_menu_index: int = 0
        self._settings_menu_banner: str = ""
        self._settings_key_pressed: bool = False
        # True while a modal/overlay suspends direct marker control; drives the
        # one-shot keyboard clear when control RETURNS to the marker (the
        # modal->marker-control edge, see app_modes).
        self._marker_control_suspended: bool = False

        self._about_active: bool = False

        from openfollow.network.adapter import (
            Ipv4Config as _Ipv4Config,
        )
        from openfollow.network.adapter import (
            NetworkInterface as _NetworkInterface,
        )
        from openfollow.network.adapter import (
            NetworkState as _NetworkState,
        )

        self._pi_network_active: bool = False
        self._pi_network_index: int = 0
        self._pi_network_interfaces: list[_NetworkInterface] = []
        self._pi_network_active_iface: str = ""
        self._pi_network_state_cache: _NetworkState | None = None
        self._pi_network_pending_config: _Ipv4Config | None = None
        self._pi_network_iface_picker_active: bool = False
        self._pi_network_iface_picker_index: int = 0
        self._pi_network_method_picker_active: bool = False
        self._pi_network_method_picker_index: int = 0
        self._pi_network_field_edit_active: bool = False
        self._pi_network_field_name: str = ""
        self._pi_network_field_value: str = ""
        self._pi_network_banner: str = ""
        self._pi_network_busy: bool = False
        self._pi_network_worker: threading.Thread | None = None
        # Generation counter to drop results from orphaned worker threads
        self._pi_network_worker_generation: int = 0
        # Hand-off slot for a finished network worker, drained on the main tick.
        self._pi_network_worker_lock = threading.Lock()
        self._pi_network_pending_result: tuple[Any, str, int, Any] | None = None

        self._show_hud_help: bool = True

        # Tap-streak acceleration per marker (avoids leaking multiplier between controllers).
        self._speed_key_streak: dict[int, int] = {}
        self._speed_key_last_t: dict[int, float] = {}
        self._speed_key_last_dir: dict[int, int] = {}

        # Debounced persistence of the runtime-authoritative per-marker move speeds.
        self._marker_speeds_dirty: bool = False
        self._marker_speeds_dirty_since: float = 0.0

        self._button_detection: ButtonDetectionWizard | None = None

        self._update_worker: threading.Thread | None = None

    def run(self) -> None:
        """Initialize subsystems and start GTK main loop (critical+non-critical for degraded mode)."""
        svc = self._runtime_services
        svc.init_canvas()
        svc.init_camera()
        svc.init_video()

        # Bounded wait for pinned source IP (prevents loopback latch on fresh boot).
        from openfollow.net_utils import wait_for_source_ip

        resolved_ip = wait_for_source_ip(
            iface=self._config.psn_source_iface,
            timeout_s=10.0,
        )
        if resolved_ip.startswith("127."):
            # Timed out; subsystems bind once (won't self-heal).
            logger.warning(
                "Starting without a usable network address (local IP %s) – "
                "web server, peer discovery, and PSN binding will use loopback "
                "and will not recover automatically; restart OpenFollow once "
                "networking is available.",
                resolved_ip,
            )
        else:
            logger.info("Network ready for startup – local IP %s", resolved_ip)

        # PSN is foundational: the group below registers markers on it. If PSN
        # failed (``_server`` None), skip them and surface a badge.
        server_dependent = {"markers", "OTP output", "RTTrPM output", "OSC transmitters"}
        for init_fn, name in (
            (svc.init_web_server, "web server"),
            (svc.init_psn, "PSN output"),
            (svc.init_markers, "markers"),
            (svc.init_otp, "OTP output"),
            (svc.init_rttrpm, "RTTrPM output"),
            (svc.init_osc_transmitters, "OSC transmitters"),
            (svc.init_psn_receiver, "PSN receiver"),
            (svc.init_zone_engine, "zone engine"),
            (svc.init_input_manager, "input manager"),
            (svc.init_midi, "MIDI subsystem"),
            (svc.init_virtual_faders, "virtual fader bus"),
            (self._init_marker_catalog_sync, "marker catalog sync"),
            (self._sync_system_hostname, "hostname sync"),
        ):
            if name in server_dependent and self._server is None:
                logger.error("Skipping %s – PSN output unavailable.", name)
                svc._status_flags["psn_init_failed"] = (
                    "error",
                    "PSN output unavailable – outputs disabled",
                )
                continue
            try:
                init_fn()
            except Exception:
                logger.exception("Failed to initialize %s – continuing without it.", name)

        # Re-resolve with fallback to decide whether to prompt.
        pinned_iface = self._config.psn_source_iface
        if pinned_iface:
            banner = self._refresh_psn_source_advisory()
            if banner:
                from openfollow.net_utils import list_iface_ipv4

                candidates = list_iface_ipv4()
                resolved = self._psn_source_resolved_ip
                logger.warning(
                    "Pinned PSN source iface=%r unavailable – %s",
                    pinned_iface,
                    f"auto-detected {resolved}" if resolved else "no usable IP",
                )
                # Prompt only when ambiguous (≥2 candidates); single-NIC has one answer.
                if len(candidates) >= 2:
                    self._enter_settings_menu(banner=banner)

        # Arm disconnect-banner deadline for startup (prompts if receiver never connects).
        import time as _time

        from openfollow.runtime import app_modes as _modes

        self._video_disconnect_deadline = _time.monotonic() + _modes.VIDEO_DISCONNECT_BANNER_DELAY

        self._run_native_loop()

    def _refresh_psn_source_advisory(self) -> str:
        """Recompute PSN-source advisory from active iface pin (single source of truth)."""
        from openfollow.net_utils import resolve_source_ip

        pinned_iface = self._config.psn_source_iface
        if not pinned_iface:
            # Auto-detect: nothing pinned, nothing to surface.
            self._psn_source_resolved_ip = ""
            self._psn_source_status = ""
            self._psn_source_banner = ""
            return ""
        resolved, status = resolve_source_ip(pinned_iface)
        self._psn_source_resolved_ip = resolved
        self._psn_source_status = status
        if status == "iface":
            # Pin honoured; clear any stale advisory.
            self._psn_source_banner = ""
            return ""
        banner = f"Pinned network interface '{pinned_iface}' is not available. "
        if resolved:
            banner += f"Using auto-detected {resolved}."
        else:
            banner += "No usable interface – pick one to continue."
        self._psn_source_banner = banner
        return banner

    def _run_native_loop(self) -> None:
        runtime_run_native_loop(self)

    def _animate(self) -> None:
        runtime_animate(self)

    def _run_housekeeping(self) -> bool:
        return runtime_housekeeping(self)

    def _check_restart_request(self) -> None:
        runtime_check_restart_request(self)

    def _check_marker_speeds_persist(self) -> None:
        runtime_check_marker_speeds_persist(self)

    def _check_pi_network_worker(self) -> None:
        from openfollow.runtime.app_modes_network import drain_pi_network_worker

        drain_pi_network_worker(self)

    def _check_update_request(self) -> None:
        runtime_check_update_request(self)

    def _check_button_detection_request(self) -> None:
        runtime_check_button_detection_request(self)

    def _run_deb_update(self, request: dict[str, str]) -> None:
        runtime_run_deb_update(self, request)

    def _run_local_update(self, request: dict[str, str]) -> None:
        runtime_run_local_update(self, request)

    def _process_input(self, dt: float) -> None:
        runtime_process_input(self, dt)

    def _process_source_selection_input(self) -> None:
        runtime_process_source_selection_input(self)

    def _process_iface_selection_input(self) -> None:
        runtime_process_iface_selection_input(self)

    def _process_browser_input(self) -> None:
        runtime_process_browser_input(self)

    @staticmethod
    def _normalize_key(key: str) -> str:
        return runtime_normalize_key(key)

    def adjust_move_speed(
        self,
        direction: int,
        marker_id: int | None = None,
    ) -> None:
        runtime_adjust_move_speed(self, direction, marker_id=marker_id)

    def get_marker_move_speed(self, marker_id: int | None) -> float:
        """Resolve the per-marker move speed.

        Falls back to the global ``MarkerConfig.move_speed`` default when the
        marker has no override (or no marker_id is given). Reads always go
        through this accessor so per-marker storage stays a single source of
        truth across the keyboard, gamepad, and HUD paths.
        """
        if marker_id is None:
            return self._config.marker.move_speed
        return self._config.marker_move_speeds.get(
            marker_id,
            self._config.marker.move_speed,
        )

    def _clear_operator_messages(self) -> None:
        """Clear all operator-message cards. Idempotent."""
        self._runtime_services._operator_message_store.clear_all()

    def _handle_key_press(self, key: str) -> None:
        runtime_handle_key_press(self, key)

    def _on_key_down(self, event: dict[str, Any]) -> None:
        runtime_on_key_down(self, event)

    def _on_key_up(self, event: dict[str, Any]) -> None:
        runtime_on_key_up(self, event)

    def _on_wheel(self, event: dict[str, Any]) -> None:
        runtime_on_wheel(self, event)

    def _on_pointer_down(self, event: dict[str, Any]) -> None:
        runtime_on_pointer_down(self, event)

    def _on_pointer_move(self, event: dict[str, Any]) -> None:
        runtime_on_pointer_move(self, event)

    def _on_pointer_up(self, event: dict[str, Any]) -> None:
        runtime_on_pointer_up(self, event)

    def _on_resize(self, event: dict[str, Any]) -> None:
        runtime_on_resize(self, event)

    def _on_close(self, _event: dict[str, Any]) -> None:
        self._runtime_services.shutdown()

    def _on_blur(self, _event: dict[str, Any]) -> None:
        # Window lost keyboard focus – clear held keys so a key whose release
        # can't be delivered to an unfocused window doesn't keep driving the
        # marker. No-op if input isn't initialised yet.
        if self._input_manager is not None:
            self._input_manager.keyboard_handler.clear()

    def _restart_app(self) -> None:
        runtime_restart_app(self)

    def _enter_source_selection(self) -> None:
        runtime_enter_source_selection(self)

    def _refresh_iface_list(self) -> None:
        runtime_refresh_iface_list(self)

    def _enter_iface_selection(self) -> None:
        runtime_enter_iface_selection(self)

    def _confirm_iface_selection(self) -> None:
        runtime_confirm_iface_selection(self)

    def _enter_button_detection(self) -> None:
        runtime_enter_button_detection(self)

    def _process_button_detection(self) -> None:
        runtime_process_button_detection(self)

    def _exit_button_detection(self) -> None:
        runtime_exit_button_detection(self)

    def _enter_source_type_selection(self) -> None:
        runtime_enter_source_type_selection(self)

    def _exit_source_type_selection(self) -> None:
        runtime_exit_source_type_selection(self)

    def _process_source_type_selection_input(self) -> None:
        runtime_process_source_type_selection_input(self)

    def _confirm_source_type_selection(self) -> None:
        runtime_confirm_source_type_selection(self)

    def _check_video_disconnect_banner(self) -> None:
        runtime_check_video_disconnect_banner(self)

    def _enter_browser(self) -> None:
        runtime_enter_browser(self)

    def _exit_browser(self) -> None:
        runtime_exit_browser(self)

    def _enter_url_editor(
        self,
        *,
        banner: str = "",
        revert_type: str = "",
    ) -> None:
        runtime_enter_url_editor(self, banner=banner, revert_type=revert_type)

    def _exit_url_editor(self) -> None:
        runtime_exit_url_editor(self)

    def _cancel_url_editor(self) -> None:
        runtime_cancel_url_editor(self)

    def _confirm_url_editor(self) -> None:
        runtime_confirm_url_editor(self)

    def _enter_field_choice_picker(self, *, revert_type: str = "") -> None:
        runtime_enter_field_choice_picker(self, revert_type=revert_type)

    def _exit_field_choice_picker(self) -> None:
        runtime_exit_field_choice_picker(self)

    def _cancel_field_choice_picker(self) -> None:
        runtime_cancel_field_choice_picker(self)

    def _confirm_field_choice_picker(self) -> None:
        runtime_confirm_field_choice_picker(self)

    def _process_field_choice_picker_input(self) -> None:
        runtime_process_field_choice_picker_input(self)

    def _enter_settings_menu(self, *, banner: str = "") -> None:
        runtime_enter_settings_menu(self, banner=banner)

    def _exit_settings_menu(self) -> None:
        runtime_exit_settings_menu(self)

    def _process_settings_menu_input(self) -> None:
        runtime_process_settings_menu_input(self)

    def _get_default_marker_position(self) -> tuple[float, float, float]:
        return runtime_get_default_marker_position(self)

    def _get_config_mtime(self) -> float:
        return runtime_get_config_mtime(self)

    def _check_config_reload(self) -> None:
        runtime_check_config_reload(self)

    def _bootstrap_station_identity(self) -> None:
        """Seed ``station_id`` (UUID) and a derived ``psn_system_name``.

        Runs once per install: only when the operator hasn't already
        configured a station name (``"OpenFollow"`` is the historical
        default the loader exposes when the field is blank or
        unrecognised – anything else is operator intent and we leave
        it alone).
        """
        dirty = False
        if not self._config.station_id:
            self._config.station_id = uuid.uuid4().hex
            dirty = True
        if self._config.psn_system_name in ("", "OpenFollow"):
            self._config.psn_system_name = derive_station_name(self._config.station_id)
            dirty = True
        if dirty:
            try:
                save_config(self._config, self._config_path)
            except OSError:
                logger.exception(
                    "Failed to persist seeded station identity to %s",
                    self._config_path,
                )

    def _marker_catalog_path(self) -> str:
        """Resolve the catalog path relative to ``config.toml``'s dir."""
        raw = self._config.markers_catalog_path or "markers.toml"
        p = Path(raw)
        if p.is_absolute():
            return str(p)
        return str(Path(self._config_path).parent / p)

    def _prune_selection_ids(self, ids: list[int]) -> None:
        """Drop ids from persisted selection (on-disk config for race-free hot-reload)."""
        drop = set(ids)
        with config_write_lock:
            cfg = load_config(self._config_path)
            new_controlled = [t for t in cfg.controlled_marker_ids if t not in drop]
            new_viewer = [t for t in cfg.viewer_marker_ids if t not in drop]
            if new_controlled == cfg.controlled_marker_ids and new_viewer == cfg.viewer_marker_ids:
                return  # none of the deleted ids were in our selection
            cfg.controlled_marker_ids = new_controlled
            cfg.viewer_marker_ids = new_viewer
            try:
                save_config(cfg, self._config_path)
            except OSError:
                logger.exception(
                    "Failed to persist selection after a peer deleted markers %s",
                    sorted(drop),
                )

    def _sync_system_hostname(self) -> None:
        """Self-name the device after its station identity so it advertises a
        memorable ``<slug>.local`` over mDNS (avahi). Passwordless-gated and
        best-effort – a no-op on dev hosts or anywhere the broker lacks a
        silent ``device.set_hostname`` grant. See
        :func:`openfollow.privilege.device_repair.sync_station_hostname`."""
        from openfollow.privilege.device_repair import sync_station_hostname

        broker = self._runtime_services.privilege_broker
        sync_station_hostname(broker, self._config.psn_system_name)

    def _init_marker_catalog_sync(self) -> None:
        """Start the multicast catalog sync (mirrors the discovery beacon)."""
        from openfollow.marker_catalog.sync import MarkerCatalogSync

        def _on_change(changed_ids: list[int]) -> None:
            # Background sync receiver: apply renames + prune deletions.
            server = self._server
            if server is None:
                return
            catalog = self._marker_catalog
            controlled = set(self._controlled_ids)
            tombstoned: list[int] = []
            for tid in changed_ids:
                entry = catalog.get(tid)
                if entry is None:
                    # get() hides tombstones; None means peer deleted it.
                    if catalog.get_any(tid) is not None:
                        tombstoned.append(tid)
                    continue
                if tid in controlled and entry.name:
                    server.update_marker_name(tid, entry.name)
            # Persist remote changes.
            try:
                save_catalog(self._marker_catalog, self._marker_catalog_path())
            except OSError:
                logger.exception(
                    "Failed to persist catalog updates from peer",
                )
            if tombstoned:
                self._prune_selection_ids(tombstoned)

        def _selection_provider() -> tuple[list[int], list[int]]:
            return (
                list(self._controlled_ids),
                list(self._viewer_ids),
            )

        # Bind sync to PSN interface for multi-homed hosts.
        sync = MarkerCatalogSync(
            self._marker_catalog,
            self._config.station_id,
            station_name_provider=lambda: self._config.psn_system_name,
            selection_provider=_selection_provider,
            on_change=_on_change,
            iface_ip=self._runtime_services._resolved_source_ip(),
        )
        # Track before start so a mid-``start`` thread-launch failure still
        # leaves the partially-started sync visible to shutdown().
        self._marker_catalog_sync = sync
        sync.start()

    def _load_or_seed_marker_catalog(self) -> MarkerCatalog:
        """Load catalog from disk; seed from selection on first run. Tombstone deleted markers."""
        path = self._marker_catalog_path()
        catalog = load_catalog(path)
        seeded = False
        controlled = list(self._config.controlled_marker_ids)
        viewer = list(self._config.viewer_marker_ids)
        for tid in controlled + viewer:
            if tid < 1:
                continue
            # Use get_any to skip tombstoned entries
            if catalog.get_any(tid) is not None:
                continue
            catalog.upsert(
                tid,
                f"Marker {tid}",
                _PALETTE_AUTO_PICK_ORDER[tid % len(_PALETTE_AUTO_PICK_ORDER)],
                origin=self._config.station_id,
            )
            seeded = True
        if seeded:
            try:
                save_catalog(catalog, path)
            except OSError:
                logger.exception("Failed to persist seeded catalog to %s", path)

        # Prune selection for deleted markers (persisted to avoid re-seeding).
        def _is_tombstoned(tid: int) -> bool:
            return catalog.get_any(tid) is not None and catalog.get(tid) is None

        new_controlled = [t for t in controlled if not _is_tombstoned(t)]
        new_viewer = [t for t in viewer if not _is_tombstoned(t)]
        if new_controlled != controlled or new_viewer != viewer:
            self._config.controlled_marker_ids = new_controlled
            self._config.viewer_marker_ids = new_viewer
            try:
                save_config(self._config, self._config_path)
            except OSError:
                logger.exception(
                    "Failed to persist selection after pruning deleted markers from %s",
                    self._config_path,
                )
        return catalog
