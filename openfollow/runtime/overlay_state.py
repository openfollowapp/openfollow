# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Overlay state containers shared across runtime and Cairo rendering."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy.typing as npt

from openfollow.configuration import (
    _GRID_COLOR_DEFAULT,
    _GRID_THICKNESS_DEFAULT,
    _GRID_TRANSPARENCY_DEFAULT,
)
from openfollow.units import UnitSystem

if TYPE_CHECKING:
    from openfollow.video.detection import DetectionBox

_MAX_MARKERS = 16  # Pre-allocated pool size


@dataclass
class ButtonDetectionState:
    """State for the button detection wizard overlay."""

    active: bool = False
    # Label the user is asked to press (e.g. "X", "A", "LT")
    current_label: str = ""
    # Index into the prompt sequence (0-based)
    step: int = 0
    total_steps: int = 0
    # Labels already completed (label -> raw index that fired)
    completed: dict[str, int] = field(default_factory=dict)


@dataclass
class PiNetworkOverlayState:
    """Snapshot of Network screens for draw-pass; bundles all four sub-states."""

    screen_active: bool = False
    rows: list[dict[str, object]] = field(default_factory=list)
    selected_index: int = 0
    active_iface: str = ""
    banner: str = ""

    iface_picker_active: bool = False
    iface_picker_items: list[str] = field(default_factory=list)
    iface_picker_selected_index: int = 0

    method_picker_active: bool = False
    method_picker_items: list[str] = field(default_factory=list)
    method_picker_selected_index: int = 0

    field_edit_active: bool = False
    field_label: str = ""
    field_value: str = ""

    def reset(self) -> None:
        self.screen_active = False
        self.rows = []
        self.selected_index = 0
        self.active_iface = ""
        self.banner = ""
        self.iface_picker_active = False
        self.iface_picker_items.clear()
        self.iface_picker_selected_index = 0
        self.method_picker_active = False
        self.method_picker_items.clear()
        self.method_picker_selected_index = 0
        self.field_edit_active = False
        self.field_label = ""
        self.field_value = ""


@dataclass
class MarkerOverlayData:
    marker_id: int
    x: float
    y: float
    z: float
    color: str
    radius: float = 0.15
    speed: float | None = None
    online: bool = True
    # Catalog name; empty falls back to M<id>.
    name: str = ""
    # Controller binding for marker card badge.
    controller_idx: int | None = None
    controller_connected: bool = False
    is_controlled: bool = False
    # Per-controlled-marker gamepad fader value (0..1) or None.
    marker_fader: float | None = None
    # Detection assist-mode "ghost": the AI-corrected PSN output, drawn as a dim
    # crosshair + ground ring (no filled ball, no card) so it reads as secondary
    # to the solid marker the operator steers (the manual anchor).
    is_assist_ghost: bool = False


@dataclass
class VirtualFaderDisplayData:
    """Virtual fader HUD-display snapshot for left-side fader stack."""

    index: int
    name: str
    value: float
    picked_up: bool


@dataclass
class OperatorMessageView:
    """Render-only snapshot of one operator-message card.

    ``marker_id == 0`` is a broadcast card (no marker chip). For keyed cards,
    ``marker_name`` is the resolved catalog name (empty falls back to ``M<id>``)
    and ``marker_color`` is its hex. ``remaining_fraction`` is ``1.0`` for a
    forever message.
    """

    message: str
    info: str
    marker_id: int
    marker_name: str
    marker_color: str
    is_forever: bool
    remaining_fraction: float


@dataclass
class OverlayState:
    """Snapshot of overlay state. Swapped atomically (GIL) between threads."""

    markers: list[MarkerOverlayData] = field(default_factory=list)
    # Pre-allocated marker data objects (reuse to avoid per-frame allocation)
    _marker_pool: list[MarkerOverlayData] = field(
        default_factory=lambda: [MarkerOverlayData(marker_id=0, x=0, y=0, z=0, color="") for _ in range(_MAX_MARKERS)],
        repr=False,
    )
    selected_id: int | None = None
    # Camera: [pos_x, pos_y, pos_z, pitch, yaw, roll, fov]
    camera_params: npt.NDArray[Any] | None = None
    # Grid: (width, depth, spacing, x_offset, y_offset, z_offset)
    grid_config: tuple[float, float, float, float, float, float] | None = None
    grid_visible: bool = True
    grid_color: str = _GRID_COLOR_DEFAULT
    grid_thickness: int = _GRID_THICKNESS_DEFAULT
    grid_transparency: float = _GRID_TRANSPARENCY_DEFAULT
    # Marker visual config
    show_ball: bool = True
    show_crosshair: bool = True
    crosshair_size: float = 0.3
    crosshair_color: str = "#ffffff"
    crosshair_thickness: int = 2
    transparency: float = 0.5
    show_drop_line: bool = True
    drop_line_thickness: int = 1
    show_ground_circle: bool = False
    ground_circle_size: float = 0.3
    ground_circle_filled: bool = True
    z_display_from_stage: bool = False
    min_speed: float = 0.1
    max_speed: float = 3.0
    # Unit system enum (not str) – format_* use is identity checks.
    unit_system: UnitSystem = UnitSystem.METRIC
    # Origin marker
    show_origin: bool = False
    origin_length: float = 1.0
    origin_thickness: int = 3
    # HUD
    controller_connected: bool = False
    keyboard_connected: bool = False
    # Indices of connected gamepads not bound to any marker; shown in Settings info card.
    unbound_controller_indices: list[int] = field(default_factory=list)
    mouse_enabled: bool = False
    ip_text: str = ""
    show_hud_help: bool = True
    # System stats (CPU, RAM, temperature)
    cpu_percent: float = 0.0
    ram_percent: float = 0.0
    temperature: float | None = None  # Celsius, None if unavailable (e.g., on Mac)
    # Operator-set station label (psn_system_name) – shown in the
    # bottom-left HUD info panel and the Settings card so a fleet
    # of near-identical Pis stays distinguishable at a glance.
    station_name: str = ""
    # Video source
    video_source_type: str = "ndi"
    video_connected: bool = True
    source_label: str = ""
    reconnect_attempt: int = 0
    error_message: str = ""
    # Source selection (generic – driven by plugin capabilities)
    source_selection_active: bool = False
    source_selection_title: str = "SELECT SOURCE"
    discovered_sources: list[str] = field(default_factory=list)
    selected_source_index: int = 0
    # Network interface selection
    iface_selection_active: bool = False
    available_interfaces: list[str] = field(default_factory=list)
    selected_iface_index: int = 0
    # Video source-type selection
    source_type_selection_active: bool = False
    # Each entry: (input_id, display_name) – pre-sorted by display.
    available_source_types: list[tuple[str, str]] = field(default_factory=list)
    selected_source_type_index: int = 0
    # On-device URL editor
    url_editor_active: bool = False
    url_editor_field_label: str = ""
    url_editor_value: str = ""
    url_editor_banner: str = ""
    # On-device field-choice picker – enum-style replacement for the URL
    # editor when the active plugin's first text field declares
    # ``ConfigField.choices`` (e.g. testpattern grey vs stage).
    field_choice_active: bool = False
    field_choice_title: str = ""
    field_choice_items: list[str] = field(default_factory=list)
    field_choice_selected_index: int = 0
    # Settings menu
    settings_menu_active: bool = False
    settings_items: list[str] = field(default_factory=list)
    settings_items_enabled: list[bool] = field(default_factory=list)
    # Per-row disabled-reason override. Empty string
    # falls back to the generic "(unavailable)" suffix in the draw pass.
    settings_items_disabled_reasons: list[str] = field(default_factory=list)
    settings_selected_index: int = 0
    settings_menu_banner: str = ""
    # About / license screen – read-only, no extra payload.
    about_active: bool = False
    # Pi Network screens: submenu, main screen, sub-pickers, editor.
    pi_network: PiNetworkOverlayState = field(default_factory=PiNetworkOverlayState)
    # Person detection bounding boxes
    detections: list[DetectionBox] = field(default_factory=list)
    detection_show_boxes: bool = False
    detection_show_labels: bool = False
    detection_box_color: str = "#808080"
    detection_box_thickness: int = 2
    # The detection track currently attached to a marker, painted in that
    # marker's colour. ``None`` track id (or empty colour) = no highlight.
    detection_attached_track_id: int | None = None
    detection_attached_color: str = ""
    # Button detection wizard
    button_detection: ButtonDetectionState | None = None
    # Configurable button labels for help overlay (action -> button name)
    button_labels: dict[str, str] = field(default_factory=dict)
    # Configurable keyboard labels for help overlay (action -> key name)
    keyboard_labels: dict[str, str] = field(default_factory=dict)
    # Virtual fader HUD stack; populated from VirtualFaderBus with show_on_display=True.
    virtual_faders_display: list[VirtualFaderDisplayData] = field(
        default_factory=list,
    )
    # Top-right status badge messages; snapshot of AppRuntimeServices._status_flags at frame-build time.
    status_flags: list[tuple[str, str, str]] = field(default_factory=list)
    # Operator-message cards (newest-first), capped to ``max_visible`` with
    # ``operator_message_overflow`` carrying the ``+N more`` count.
    # ``operator_message_position`` is ``"bottom"`` (default) or ``"top"``.
    operator_messages: list[OperatorMessageView] = field(default_factory=list)
    operator_message_position: str = "bottom"
    operator_message_overflow: int = 0
    operator_message_scale: float = 1.0
    # Trigger zones
    show_zones: bool = False
    # Each entry: (vertices_world_xy, color_hex, name, is_occupied, count)
    zone_polygons: list[tuple[list[tuple[float, float]], str, str, bool, int]] = field(default_factory=list)
    zone_z_offset: float = 0.0

    def reset(self) -> None:
        """Reset state for reuse in object pool."""
        self.markers.clear()
        self.selected_id = None
        self.camera_params = None
        self.grid_config = None
        self.grid_visible = True
        self.grid_color = _GRID_COLOR_DEFAULT
        self.grid_thickness = _GRID_THICKNESS_DEFAULT
        self.grid_transparency = _GRID_TRANSPARENCY_DEFAULT
        self.show_ball = True
        self.show_crosshair = True
        self.crosshair_size = 0.3
        self.crosshair_color = "#ffffff"
        self.crosshair_thickness = 2
        self.transparency = 0.5
        self.show_drop_line = True
        self.drop_line_thickness = 1
        self.show_ground_circle = False
        self.ground_circle_size = 0.3
        self.ground_circle_filled = True
        self.z_display_from_stage = False
        self.min_speed = 0.1
        self.max_speed = 3.0
        self.unit_system = UnitSystem.METRIC
        self.show_origin = False
        self.origin_length = 1.0
        self.origin_thickness = 3
        self.controller_connected = False
        self.keyboard_connected = False
        self.unbound_controller_indices = []
        self.mouse_enabled = False
        self.ip_text = ""
        self.show_hud_help = True
        self.cpu_percent = 0.0
        self.ram_percent = 0.0
        self.temperature = None
        self.station_name = ""
        self.video_source_type = "ndi"
        self.video_connected = True
        self.source_label = ""
        self.reconnect_attempt = 0
        self.error_message = ""
        self.source_selection_active = False
        self.source_selection_title = "SELECT SOURCE"
        self.discovered_sources.clear()
        self.selected_source_index = 0
        self.iface_selection_active = False
        self.available_interfaces.clear()
        self.selected_iface_index = 0
        self.source_type_selection_active = False
        self.available_source_types.clear()
        self.selected_source_type_index = 0
        self.url_editor_active = False
        self.url_editor_field_label = ""
        self.url_editor_value = ""
        self.url_editor_banner = ""
        self.field_choice_active = False
        self.field_choice_title = ""
        self.field_choice_items.clear()
        self.field_choice_selected_index = 0
        self.settings_menu_active = False
        self.settings_items.clear()
        self.settings_items_enabled.clear()
        self.settings_items_disabled_reasons.clear()
        self.settings_selected_index = 0
        self.settings_menu_banner = ""
        self.about_active = False
        self.pi_network.reset()
        self.detections = []
        self.detection_show_boxes = False
        self.detection_show_labels = False
        self.detection_box_color = "#808080"
        self.detection_box_thickness = 2
        self.detection_attached_track_id = None
        self.detection_attached_color = ""
        self.button_detection = None
        self.button_labels = {}
        self.keyboard_labels = {}
        self.show_zones = False
        self.zone_polygons = []
        self.zone_z_offset = 0.0
        # Clear fader/badge stacks on pool reuse to avoid stale entries.
        self.virtual_faders_display = []
        self.status_flags = []
        # Clear operator-message cards on pool reuse.
        self.operator_messages = []
        self.operator_message_position = "bottom"
        self.operator_message_overflow = 0
        self.operator_message_scale = 1.0
