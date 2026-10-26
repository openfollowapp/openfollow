# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Overlay state containers shared across runtime and Cairo rendering."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy.typing as npt

from openfollow.button_names import ButtonNameStyle
from openfollow.configuration import (
    _GRID_COLOR_DEFAULT,
    _GRID_THICKNESS_DEFAULT,
    _GRID_TRANSPARENCY_DEFAULT,
)
from openfollow.units import UnitSystem

if TYPE_CHECKING:
    from openfollow.video.detection import DetectionBox


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
    # Interface whose own screen is open; "" is the interface list.
    open_iface: str = ""
    # That screen's headline: "Network Interface: Lighting (enx…)", or a Simple screen's.
    title: str = ""
    # Set for the Simple screens, which have no interface list above them.
    subtitle: str = ""
    banner: str = ""
    # "error", "caution", "info" or "success".
    banner_level: str = ""
    # An apply or renew is running; the banner leads with the spinner.
    busy: bool = False

    field_edit_active: bool = False
    field_label: str = ""
    field_value: str = ""
    # Character offset of the d-pad cursor within ``field_value``, or -1
    # when the value is freely typed and the caret belongs at the end.
    field_caret_offset: int = -1


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
    # Controller binding for marker card badge. A bound controller that is not
    # connected is missing: its slot still owns this marker.
    controller_idx: int | None = None
    controller_connected: bool = False
    # On/off phase of an Identify flash on this card, set per frame.
    identify_flash: bool = False
    is_controlled: bool = False
    # Parked: no relative input moves it; the card carries a P badge.
    parked: bool = False
    # Another station controls this marker too.
    control_conflict: bool = False
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
    forever message. A station-made card carries a ``headline`` on a bar in its
    ``headline_level``'s chip fill (a status level) in place of a marker's.
    """

    message: str
    info: str
    marker_id: int
    marker_name: str
    marker_color: str
    is_forever: bool
    remaining_fraction: float
    headline: str = ""
    headline_level: str = ""


@dataclass
class OverlayState:
    """Snapshot of overlay state, built new each frame and never changed once the render thread receives it."""

    markers: list[MarkerOverlayData] = field(default_factory=list)
    selected_id: int | None = None
    # Camera: [pos_x, pos_y, pos_z, pitch, yaw, roll, fov]
    camera_params: npt.NDArray[Any] | None = None
    # The lens the overlay is bowed to match (openfollow.lens_model): the pair and
    # the lens centre. A 0 pair draws straight lines; a centre alone shifts them.
    lens_k1: float = 0.0
    lens_k2: float = 0.0
    lens_cx: float = 0.0
    lens_cy: float = 0.0
    # Grid: (width, depth, spacing, x_offset, y_offset, z_offset)
    grid_config: tuple[float, float, float, float, float, float] | None = None
    grid_visible: bool = True
    grid_color: str = _GRID_COLOR_DEFAULT
    grid_thickness: int = _GRID_THICKNESS_DEFAULT
    grid_transparency: float = _GRID_TRANSPARENCY_DEFAULT
    # Ground grid: a polygon floor at its own Z. ``ground_grid_active`` redirects
    # the floor elements of markers inside it; ``ground_grid_visible`` draws it, on its own.
    ground_grid_active: bool = False
    ground_grid_visible: bool = False
    ground_grid_z: float = 0.0
    ground_grid_vertices: list[tuple[float, float]] = field(default_factory=list)
    # Marker visual config
    marker_style: str = "crosshair"
    cone_base_diameter: float = 0.6
    cone_top_diameter: float = 0.3
    cone_thickness: int = 2
    cone_filled: bool = True
    cone_opacity: float = 0.4
    cone_shaded: bool = True
    show_ball: bool = True
    show_crosshair: bool = True
    crosshair_size: float = 0.3
    crosshair_color: str = "#ffffff"
    crosshair_thickness: int = 2
    transparency: float = 0.5
    show_z_line: bool = True
    z_line_thickness: int = 1
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
    mouse_double_click_reset: bool = True
    mouse_wheel_z_enabled: bool = True
    ip_text: str = ""
    # ``<hostname>.local`` – the recovery route that works whatever address
    # the station ends up with, so the HUD carries it alongside the IP. Always
    # the real ``current_hostname()``: advertising a name avahi isn't
    # answering to is worse than showing none.
    hostname_text: str = ""
    # True when ip_text is an RFC 3927 self-assigned address, i.e. DHCP never
    # answered. Drives the qualifier that stops an operator reading a
    # 169.254 address as a working lease.
    ip_is_fallback: bool = False
    # One line per network plane whose configured interface has no address,
    # from the first poll that finds none (the plane itself is stopped later).
    # The HUD is the only surface left when the interface that carries the
    # web UI is the one that went away.
    network_alerts: list[str] = field(default_factory=list)
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
    # The operator sentence for what stopped the feed. The web UI leads with
    # it, so the device says the same thing rather than only the element's
    # wording.
    video_failure_text: str = ""
    video_failure_action: str = ""
    # Source selection (generic – driven by plugin capabilities)
    source_selection_active: bool = False
    source_selection_title: str = "SELECT SOURCE"
    discovered_sources: list[str] = field(default_factory=list)
    selected_source_index: int = 0
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
    # Which entries open another screen rather than acting where they stand.
    settings_items_submenu: list[bool] = field(default_factory=list)
    settings_selected_index: int = 0
    settings_menu_banner: str = ""
    settings_menu_banner_action: str = ""
    # About / license screen – read-only, no extra payload.
    about_active: bool = False
    # Drive picker + diagnostics export screen.
    media_picker_active: bool = False
    media_picker_title: str = ""
    media_picker_items: list[str] = field(default_factory=list)
    # Parallel to ``media_picker_items``: False for a drive that can't be written.
    media_picker_enabled: list[bool] = field(default_factory=list)
    media_picker_index: int = -1
    media_picker_empty: str = ""
    media_export_active: bool = False
    # ``(headline, next step, ok)``; ``ok`` is None while the export runs.
    media_export_lines: tuple[str, str, bool | None] = ("", "", None)
    # Pi Network screens: submenu, main screen, sub-pickers, editor.
    pi_network: PiNetworkOverlayState = field(default_factory=PiNetworkOverlayState)
    # Person detection bounding boxes
    detections: list[DetectionBox] = field(default_factory=list)
    detection_show_boxes: bool = False
    detection_show_labels: bool = False
    detection_box_color: str = "#808080"
    detection_box_thickness: int = 2
    # Detection tracks currently attached to a marker, each painted in that
    # marker's colour: ``{track_id: hex_colour}``. Empty = no highlight. Assist
    # drives every controlled marker, so several boxes can be attached at once.
    detection_attached_colors: dict[int, str] = field(default_factory=dict)
    # Button detection wizard
    button_detection: ButtonDetectionState | None = None
    # The latency probe's screen level (0 black, 1 white) covering the whole canvas; None draws the HUD.
    latency_flash: float | None = None
    # Configurable button labels for help overlay (action -> button name)
    button_labels: dict[str, str] = field(default_factory=dict)
    # Pad family the station names buttons after (controller.button_name_style)
    button_name_style: ButtonNameStyle = ButtonNameStyle.STANDARD
    # Configurable keyboard labels for help overlay (action -> key name)
    keyboard_labels: dict[str, str] = field(default_factory=dict)
    # 3D Mouse bindings for the help overlay: axis -> target, action -> button
    # index. ``mouse3d_connected`` gates the help section (enabled + a device).
    mouse3d_connected: bool = False
    mouse3d_axis_map: dict[str, str] = field(default_factory=dict)
    mouse3d_buttons: dict[str, int] = field(default_factory=dict)
    # Marker next/prev cycling is a single-controller affordance: with two or
    # more controllers (gamepads + 3D mice) bound to their own markers, cycling
    # the shared selection is suppressed, so the help overlay hides those rows
    # for both gamepad and 3D mouse. Keyboard cycling is unaffected.
    marker_cycle_enabled: bool = True
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
    # Paths: (curve, ends, locked) each, world X/Y/Z; an end is (at, a point just inside the path, releases).
    show_paths: bool = False
    path_lines: list[
        tuple[
            list[tuple[float, float, float]],
            list[tuple[tuple[float, float, float], tuple[float, float, float], bool]],
            bool,
        ]
    ] = field(default_factory=list)
    # Where a locked marker can turn at a nearby junction: a short (tail, tip) stretch along each way.
    path_arrows: list[tuple[tuple[float, float, float], tuple[float, float, float]]] = field(default_factory=list)

    @property
    def lens(self) -> tuple[float, float, float, float]:
        """``(k1, k2, cx, cy)``, in the order the warp functions take them."""
        return self.lens_k1, self.lens_k2, self.lens_cx, self.lens_cy
