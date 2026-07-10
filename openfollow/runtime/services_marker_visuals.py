# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Marker and overlay state helpers for runtime services."""

from __future__ import annotations

import math
import re
import time
from typing import Any

import numpy.typing as npt

from openfollow.configuration import MOUSE3D_AXES, MOUSE3D_BUTTON_FIELDS, GridConfig
from openfollow.net_utils import list_iface_ipv4
from openfollow.palette import AUTO_PICK_ORDER as _PALETTE_AUTO_PICK_ORDER
from openfollow.runtime.overlay_state import (
    MarkerOverlayData,
    OperatorMessageView,
    OverlayState,
    VirtualFaderDisplayData,
)
from openfollow.runtime.services_detection_pin import is_assist_controlled
from openfollow.runtime_metrics import OverlayStatePool
from openfollow.units import UnitSystem

# Same pattern ``GridConfig.__post_init__`` enforces. Duplicated here rather
# than imported from configuration.py so this module doesn't reach into the
# dataclass module's private helpers – the pattern itself is the contract.
_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

# Single source of truth for sync_grid_config fallbacks. Instantiated once at
# module load so shim objects / in-place mutations that skip
# ``GridConfig.__post_init__`` still fall back to whatever the dataclass
# currently declares – no silent desync if GridConfig defaults change later.
_GRID_DEFAULTS = GridConfig()


def _soft_int(value: Any, default: int, *, lo: int | None = None) -> int:
    """Coerce to int, clamp to at least ``lo`` if given; fall back to ``default``.

    Two-stage behaviour:

    1. Convert ``value`` to ``int`` via ``int(value)``, which truncates
       toward zero for floats (e.g. ``int(1.9) == 1``, ``int(-1.9) == -1``).
       Bad input (``bool``, ``TypeError``/``ValueError``/``OverflowError``)
       falls back to ``default`` unclamped.
    2. If ``lo`` is given, the converted result is then lower-bounded:
       ``out = max(lo, out)``. This is a one-sided clamp – there is no
       upper bound. The ``default`` fallback path skips this step.

    Mirrors ``configuration._coerce_int`` for the shim path:

    - Rejects ``bool`` explicitly. ``bool`` is an ``int`` subclass in Python,
      so a shim object that smuggles ``thickness=True`` past
      ``GridConfig.__post_init__`` would otherwise silently coerce to ``1``
      instead of falling back to the dataclass default.
    - Catches ``OverflowError`` because ``int(float('inf'))`` raises overflow
      rather than ``ValueError``, and shim objects can carry non-finite floats.

    Duplicated rather than imported from configuration.py so this module
    keeps its local "pattern is the contract" independence – see the same
    note on ``_soft_float`` / ``_soft_bool`` above.
    """
    if isinstance(value, bool):
        return default
    try:
        out = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    # pragma: no branch – every current caller passes ``lo=1``; the
    # signature keeps ``lo`` optional for symmetry with the
    # configuration-module helper, so the False arm is structurally
    # reachable but unused at every present call site.
    if lo is not None:  # pragma: no branch
        out = max(lo, out)
    return out


def _soft_float(value: Any, default: float, *, lo: float | None = None) -> float:
    """Coerce to float, floor-clamp to ``lo`` if given; fall back to ``default``.

    Belt-and-suspenders for the overlay update path. ``GridConfig.__post_init__``
    normalises first; this only kicks in for shim objects or in-place mutations
    that skip the dataclass.

    Catches ``OverflowError`` alongside ``TypeError``/``ValueError`` –
    ``float(huge_int)`` raises overflow (TOML ints are arbitrary-precision
    Python ints), and the whole point of this helper is to never raise on
    the overlay update path.

    Also rejects non-finite results (``inf``/``-inf``/``nan``): ``float(value)``
    happily returns those, but ``draw_grid`` downstream does
    ``int(width / spacing)`` which then raises. Falling back to ``default``
    here keeps the "never raise on the overlay update path" guarantee even
    when a shim object smuggles ``inf``/``nan`` past the dataclass.
    """
    try:
        out = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if not math.isfinite(out):
        return default
    if lo is not None and out < lo:
        return lo
    return out


# Mirror of ``_coerce_bool`` in configuration.py, duplicated so this module
# keeps its local "pattern is the contract" independence.
_TRUTHY_STRINGS = frozenset({"1", "true", "yes", "on"})
_FALSY_STRINGS = frozenset({"0", "false", "no", "off"})


def _soft_bool(value: Any, default: bool) -> bool:
    """Accept real bools or recognised string forms; fall back to ``default``.

    ``bool("false") is True`` – shim objects that skip
    ``GridConfig.__post_init__`` must not flip a disabled flag to enabled
    just because the operator wrote the word out.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _TRUTHY_STRINGS:
            return True
        if lowered in _FALSY_STRINGS:
            return False
    return default


def _resolve_marker_color(app: Any, marker_id: int) -> str:
    """Resolve a marker's display color: catalog first, palette fallback.

    The fallback covers a transient race where a ``controlled_id``
    exists without a catalog entry yet (e.g. a peer's delete arrived
    a tick before our seed re-created it). Falls through to the
    canonical ``openfollow.palette.AUTO_PICK_ORDER`` since the
    user-editable palette field was removed – new-marker colour
    assignment happens in the web UI's catalog editor (first-unused
    pick over the same sequence).

    Lowercased to match catalog-stored colours (``_coerce_hex_color``
    in ``configuration.py`` normalizes everything written to the
    catalog) – without this, two consecutive calls returning the same
    semantic colour could differ in case depending on whether the
    catalog had the entry yet.
    """
    catalog = getattr(app, "_marker_catalog", None)
    if catalog is not None:
        entry = catalog.get(marker_id)
        if entry is not None:
            return str(entry.color)
    return _PALETTE_AUTO_PICK_ORDER[marker_id % len(_PALETTE_AUTO_PICK_ORDER)].lower()


def _resolve_marker_name(app: Any, marker_id: int) -> str:
    """Resolve a marker's display name from the catalog, or empty
    string if no entry. The HUD turns the empty string into a
    ``M<id>`` fallback label so the operator still sees *something*
    during the transient races covered by ``_resolve_marker_color``.
    """
    catalog = getattr(app, "_marker_catalog", None)
    if catalog is None:
        return ""
    entry = catalog.get(marker_id)
    if entry is None:
        return ""
    return str(entry.name)


def sync_marker_config(state: OverlayState, cfg: Any) -> None:
    """Copy marker visual config fields into the overlay state."""
    tc = cfg.marker
    state.show_ball = tc.ball_visible
    state.show_crosshair = tc.crosshair_visible
    state.crosshair_size = tc.crosshair_size
    state.crosshair_color = tc.crosshair_color
    state.crosshair_thickness = int(tc.crosshair_thickness)
    state.transparency = tc.transparency
    state.show_drop_line = tc.drop_line
    state.drop_line_thickness = int(tc.drop_line_thickness)
    state.show_ground_circle = tc.ground_circle
    state.ground_circle_size = tc.ground_circle_size
    state.ground_circle_filled = tc.ground_circle_filled
    state.z_display_from_stage = tc.z_display_from_stage
    state.min_speed = tc.min_speed
    state.max_speed = tc.max_speed


def sync_grid_config(state: OverlayState, cfg: Any) -> None:
    """Copy grid config fields into the overlay state."""
    g = cfg.grid
    # ``GridConfig.__post_init__`` is the primary line of defence and already
    # normalises every field here. These tolerant copies are belt-and-
    # suspenders for two cases the dataclass doesn't cover: (1) tests and
    # runtime shims that pass a ``SimpleNamespace`` / duck-typed object
    # instead of a real ``GridConfig`` (skipping ``__post_init__`` entirely),
    # and (2) code paths that mutate ``cfg.grid`` attributes in place without
    # calling ``__post_init__`` again. A raise on the overlay update path
    # would freeze the HUD, so fail soft to ``_GRID_DEFAULTS`` – that is a
    # live ``GridConfig()`` instance, so changing field defaults in the
    # dataclass automatically updates this fallback path.
    d = _GRID_DEFAULTS
    state.grid_config = (
        _soft_float(g.width, d.width, lo=0.1),
        _soft_float(g.depth, d.depth, lo=0.1),
        _soft_float(g.spacing, d.spacing, lo=0.1),
        _soft_float(g.x_offset, d.x_offset),
        _soft_float(g.y_offset, d.y_offset),
        _soft_float(g.z_offset, d.z_offset),
    )
    # getattr keeps the per-tick path safe if a shim/partial grid object
    # predates this field; a real GridConfig always carries it.
    state.grid_visible = _soft_bool(getattr(g, "visible", d.visible), d.visible)
    state.grid_color = g.color.lower() if isinstance(g.color, str) and _HEX_COLOR_RE.match(g.color) else d.color
    state.grid_thickness = _soft_int(g.thickness, d.thickness, lo=1)
    # ``_soft_float`` rejects non-finite (inf/nan) to ``default``; the
    # ``max/min`` then clamps any merely out-of-range valid float to
    # [0, 1]. Replaces an inline ``float()`` + clamp that silently
    # mapped ``inf → 1.0`` and let ``nan`` propagate into draw calls.
    state.grid_transparency = max(0.0, min(1.0, _soft_float(g.transparency, d.transparency)))
    state.show_origin = _soft_bool(g.origin_visible, d.origin_visible)
    # lo=0.1 matches ``GridConfig.__post_init__``'s clamp so the dataclass
    # and the shim fallback agree on the invariant (origin_length >= 0.1).
    state.origin_length = _soft_float(g.origin_length, d.origin_length, lo=0.1)
    state.origin_thickness = _soft_int(g.origin_thickness, d.origin_thickness, lo=1)


def sync_ui_config(state: OverlayState, cfg: Any) -> None:
    """Copy the UI display unit system into the overlay state.

    Called on the per-frame overlay update path, so flipping
    ``[ui] unit_system`` in config.toml live-reloads the HUD without a
    restart. Fails soft to metric: ``cfg`` may be a duck-typed shim with
    no ``ui`` section (tests, runtime stubs), and a bad value should
    degrade rather than freeze the draw pass. ``UiConfig`` already
    normalises the string, so the ``except`` only guards the shim path –
    which can also smuggle a non-string (an unhashable dict/list makes
    ``UnitSystem(raw)`` raise ``TypeError``, not ``ValueError``).
    """
    ui = getattr(cfg, "ui", None)
    raw = getattr(ui, "unit_system", "metric") if ui is not None else "metric"
    try:
        state.unit_system = UnitSystem(raw)
    except (ValueError, TypeError):
        state.unit_system = UnitSystem.METRIC


def _populate_pi_network_overlay(app: Any, state: OverlayState) -> None:
    """Snapshot the Network screens for draw-pass consumption."""
    target = state.pi_network
    target.reset()
    target.screen_active = bool(getattr(app, "_pi_network_active", False))
    if target.screen_active:
        from openfollow.runtime.app_modes_network import build_pi_network_rows

        target.rows = build_pi_network_rows(app)
        target.selected_index = int(getattr(app, "_pi_network_index", 0))
        target.active_iface = str(getattr(app, "_pi_network_active_iface", ""))
        target.banner = str(getattr(app, "_pi_network_banner", ""))
    target.iface_picker_active = bool(getattr(app, "_pi_network_iface_picker_active", False))
    if target.iface_picker_active:
        target.iface_picker_items = [i.name for i in getattr(app, "_pi_network_interfaces", [])]
        target.iface_picker_selected_index = int(getattr(app, "_pi_network_iface_picker_index", 0))
    target.method_picker_active = bool(getattr(app, "_pi_network_method_picker_active", False))
    if target.method_picker_active:
        from openfollow.runtime.app_modes_network import method_picker_items

        target.method_picker_items = [label for _, label in method_picker_items()]
        target.method_picker_selected_index = int(getattr(app, "_pi_network_method_picker_index", 0))
    target.field_edit_active = bool(getattr(app, "_pi_network_field_edit_active", False))
    if target.field_edit_active:
        target.field_label = str(getattr(app, "_pi_network_field_name", "")).replace("_", " ").title()
        target.field_value = str(getattr(app, "_pi_network_field_value", ""))


def _populate_zone_overlay(state: OverlayState, cfg: Any, app: Any) -> None:
    """Copy zone polygons + occupancy into the overlay state."""
    tz = cfg.trigger_zones
    # ``enabled`` and ``show_overlay`` are independent UI toggles: the former
    # arms the trigger engine, the latter controls visualisation. The overlay
    # hotkey only flips ``show_overlay``, so gating rendering on ``enabled``
    # would make the hotkey a silent no-op whenever triggers are off.
    state.show_zones = bool(tz.show_overlay)
    state.zone_z_offset = float(cfg.grid.z_offset)
    if not state.show_zones:
        state.zone_polygons = []
        return

    occupancy_by_index: dict[int, tuple[bool, int]] = {}
    if tz.enabled:
        zone_engine = getattr(app._runtime_services, "_zone_engine", None)
        if zone_engine is not None:
            for idx, is_occupied, count in zone_engine.get_zone_states():
                occupancy_by_index[idx] = (is_occupied, count)

    polygons: list[tuple[list[tuple[float, float]], str, str, bool, int]] = []
    for idx, zone in enumerate(tz.zones):
        if not zone.enabled or len(zone.vertices) < 3:
            continue
        # Vertices, marker.pos, and detection world coords all share the
        # PSN-absolute frame – no translation needed on any side of the
        # zone-membership comparison.
        verts_world = [(float(v[0]), float(v[1])) for v in zone.vertices]
        is_occupied, count = occupancy_by_index.get(idx, (False, 0))
        polygons.append((verts_world, zone.color, zone.name, is_occupied, count))
    state.zone_polygons = polygons


def build_initial_overlay_state(cfg: Any) -> OverlayState:
    """Create the initial disconnected overlay state from config."""
    state = OverlayState()
    sync_grid_config(state, cfg)
    sync_marker_config(state, cfg)
    sync_ui_config(state, cfg)

    state.video_connected = False
    state.source_label = ""
    state.reconnect_attempt = 0
    state.error_message = ""
    state.controller_connected = False
    state.keyboard_connected = False
    state.source_selection_active = False
    state.source_selection_title = "SELECT SOURCE"
    state.discovered_sources = []
    state.selected_source_index = 0
    state.iface_selection_active = False
    state.available_interfaces = []
    state.selected_iface_index = 0
    state.settings_menu_active = False
    state.settings_items = []
    state.settings_items_enabled = []
    state.settings_items_disabled_reasons = []
    state.settings_selected_index = 0

    return state


def build_marker_visual_state(
    app: Any,
    *,
    overlay_state_pool: OverlayStatePool,
    system_stats: Any,
    person_detector: Any,
    cam_params_buffer: npt.NDArray[Any],
) -> OverlayState:
    """Build a complete OverlayState snapshot for atomic renderer swap."""
    controlled_set = set(app._controlled_ids)
    marker_speeds = app._input_manager.get_marker_gamepad_speeds() if app._input_manager is not None else {}

    # Fetch controller info once and build a reverse map so the per-marker
    # loop can stamp each marker card with its bound controller without an
    # O(n) scan per marker. ``controller_info`` is also used below for the
    # connected/unbound-list state stamps so the same call powers all three
    # consumers.
    controller_info: list[dict[str, Any]] = (
        app._input_manager.get_controller_info() if app._input_manager is not None else []
    )
    controller_by_marker: dict[int, tuple[int, bool]] = {
        info["marker_id"]: (
            int(info["controller_index"]),
            bool(info["connected"]),
        )
        for info in controller_info
        if info["marker_id"] is not None
    }

    state = overlay_state_pool.acquire()

    if system_stats is not None:
        stats = system_stats.update()
        state.cpu_percent = stats.cpu_percent
        state.ram_percent = stats.ram_percent
        state.temperature = stats.temperature
        ip = stats.ip_address
        if ip and ip != "N/A":
            # Prefer the actually-bound port from the running web server so
            # the HUD reflects reachability, not just configuration. Fall
            # back to the configured port when the server is not yet wired
            # (e.g. early-startup snapshots, unit tests with a stubbed app).
            web_server = getattr(app, "_web_server", None)
            port = web_server.display_port if web_server is not None else app._config.web_port
            # Port 80 is the HTTP default and is implicit in URLs typed
            # into a browser, so omit it for a cleaner display.
            base = ip if port == 80 else f"{ip}:{port}"
            # Append the iface name in parens so the operator on a
            # multi-homed host can tell at a glance which NIC the IP belongs
            # to (``"192.168.178.61 (eth0)"``). Empty iface (offline /
            # loopback fallback / IP not bound to any NIC) is left bare.
            state.ip_text = f"{base} ({stats.iface_name})" if stats.iface_name else base
        else:
            state.ip_text = ip

    # The station name is the operator-set ``psn_system_name`` (the
    # same value the discovery beacon and PSN info packets advertise).
    # Bottom-left HUD + Settings card both surface it so a fleet of
    # near-identical Pis stays distinguishable at a glance.
    state.station_name = app._config.psn_system_name

    video_receiver = app._video_receiver
    status_marker = video_receiver.status_marker
    # Read the four status fields as one consistent unit – separate property
    # reads could each catch a different _update generation and render a mixed
    # HUD line (e.g. connected=True with a stale reconnect error).
    status = status_marker.snapshot()
    state.video_source_type = app._config.video_source_type
    state.video_connected = status.is_connected
    state.source_label = video_receiver.source_name
    state.reconnect_attempt = status.reconnect_attempt
    state.error_message = status.error_message
    state.source_selection_active = video_receiver.source_selection_active
    state.discovered_sources = video_receiver.discovered_sources
    state.selected_source_index = video_receiver.selected_source_index
    state.source_selection_title = video_receiver.source_selection_title
    state.iface_selection_active = app._iface_selection_active
    # Render each picker row as ``"eth0 (192.168.178.61)"`` so on a
    # multi-homed host the operator can tell which network each interface
    # is on without leaving the menu. ``app._available_interfaces`` stays
    # as the iface-name list (the value used by the picker / dispatcher);
    # the parallel labels here are display-only. ``""`` (auto-detect)
    # passes through unformatted so the renderer can label it itself.
    #
    # Only the iface picker overlay reads ``state.available_interfaces``,
    # so gate the ``psutil.net_if_addrs()`` snapshot behind the picker
    # being open – otherwise every overlay frame (~60 Hz) would walk
    # every NIC for labels nothing reads.
    if app._iface_selection_active:
        iface_ips = dict(list_iface_ipv4())
        state.available_interfaces = [
            f"{name} ({iface_ips[name]})" if name and name in iface_ips else name for name in app._available_interfaces
        ]
    else:
        state.available_interfaces = list(app._available_interfaces)
    state.selected_iface_index = app._selected_iface_index
    state.source_type_selection_active = app._source_type_selection_active
    state.available_source_types = list(app._available_source_types)
    state.selected_source_type_index = app._selected_source_type_index
    state.url_editor_active = app._url_editor_active
    if app._url_editor_active:
        state.url_editor_field_label = app._url_editor_field_label
        state.url_editor_value = app._url_editor_value
        state.url_editor_banner = app._url_editor_banner
    else:
        state.url_editor_field_label = ""
        state.url_editor_value = ""
        state.url_editor_banner = ""
    state.field_choice_active = app._field_choice_active
    if app._field_choice_active:
        state.field_choice_title = app._field_choice_field_label
        state.field_choice_items = list(app._field_choice_items)
        state.field_choice_selected_index = app._field_choice_selected_index
    else:
        state.field_choice_title = ""
        state.field_choice_items = []
        state.field_choice_selected_index = 0
    state.settings_menu_active = app._settings_menu_active
    if app._settings_menu_active:
        from openfollow.runtime.app_modes import build_settings_menu_items

        labels, enabled, reasons = build_settings_menu_items(app)
        state.settings_items = labels
        state.settings_items_enabled = enabled
        state.settings_items_disabled_reasons = reasons
        state.settings_selected_index = app._settings_menu_index
        state.settings_menu_banner = app._settings_menu_banner
    else:
        state.settings_items = []
        state.settings_items_enabled = []
        state.settings_items_disabled_reasons = []
        state.settings_selected_index = 0
        state.settings_menu_banner = ""

    state.about_active = getattr(app, "_about_active", False)

    _populate_pi_network_overlay(app, state)

    cfg = app._config
    pool = state._marker_pool
    marker_idx = 0
    # Marker-fader bus for the per-marker fader value shown on each card.
    # Same ``getattr`` boot / mid-restart guard as the fader-stack build
    # below; ``None`` → no marker-fader value on any card this frame.
    _runtime_services = getattr(app, "_runtime_services", None)
    _fader_bus = getattr(_runtime_services, "_virtual_faders", None) if _runtime_services is not None else None
    # Assist mode renders each controlled marker's operator-steered manual anchor
    # as the solid carded marker (what the operator moves) and the AI-corrected
    # PSN output as a dim ghost below it. Assist refines every controlled marker,
    # so one ghost is built per assist-controlled marker while iterating and they
    # are appended after the loop.
    ghost_entries: list[MarkerOverlayData] = []
    for tid in app._viewer_ids:
        if tid in controlled_set:
            marker = app._server.get_marker(tid)
        else:
            marker = app._psn_receiver.get_marker(tid)
        if marker is None:
            continue

        # Per-marker fallback. ``marker_speeds`` is populated only for
        # markers that have a connected controller; everything else
        # (controlled markers without a pad, or with a stale stored
        # override) reads the per-marker value via the accessor so the
        # speed card stays consistent with what bumper / R / T edits
        # actually wrote.
        speed = marker_speeds.get(tid)
        if speed is None and tid in controlled_set:
            speed = app.get_marker_move_speed(tid)
        if tid in controlled_set:
            broadcast_speed = speed if speed is not None else app.get_marker_move_speed(tid)
            marker.set_speed(broadcast_speed, 0.0, 0.0)
        if speed is None and tid not in controlled_set:
            vx, vy, vz = marker.speed
            speed = (vx * vx + vy * vy + vz * vz) ** 0.5

        if tid in controlled_set:
            online = True
        else:
            online = app._psn_receiver.is_marker_online(tid)

        color = _resolve_marker_color(app, tid)
        name = _resolve_marker_name(app, tid)
        ctrl_binding = controller_by_marker.get(tid)
        ctrl_idx = ctrl_binding[0] if ctrl_binding is not None else None
        ctrl_conn = ctrl_binding[1] if ctrl_binding is not None else False
        is_controlled = tid in controlled_set
        marker_fader = _fader_bus.marker_fader_value(tid) if _fader_bus is not None else None

        # Read the position tuple once so x/y/z come from a single locked
        # snapshot. The receiver thread can call ``set_pos`` between separate
        # ``marker.pos`` accesses, which would tear X/Y/Z across two packets.
        px, py, pz = marker.pos

        # Each assist-controlled marker's card sits at its operator-steered
        # anchor, not the broadcast position. Capture the registered (AI-
        # corrected) position as a dim ghost, then move the carded marker to the
        # anchor. Until the anchor is seeded the two coincide.
        if is_assist_controlled(app, tid):
            ghost_entries.append(
                MarkerOverlayData(
                    marker_id=tid,
                    x=px,
                    y=py,
                    z=pz,
                    color=color,
                    radius=cfg.marker.ball_size,
                    speed=None,
                    online=True,
                    is_controlled=False,
                    name=name,
                    is_assist_ghost=True,
                )
            )
            anchor = app._assist_manual.get(tid)
            if anchor is not None:
                px, py, pz = anchor.pos

        if marker_idx < len(pool):
            td = pool[marker_idx]
            td.marker_id = tid
            td.x = px
            td.y = py
            td.z = pz
            td.color = color
            td.radius = cfg.marker.ball_size
            td.speed = speed
            td.online = online
            td.controller_idx = ctrl_idx
            td.controller_connected = ctrl_conn
            td.is_controlled = is_controlled
            td.name = name
            td.marker_fader = marker_fader
        else:
            td = MarkerOverlayData(
                marker_id=tid,
                x=px,
                y=py,
                z=pz,
                color=color,
                radius=cfg.marker.ball_size,
                speed=speed,
                online=online,
                controller_idx=ctrl_idx,
                controller_connected=ctrl_conn,
                is_controlled=is_controlled,
                name=name,
                marker_fader=marker_fader,
            )
        state.markers.append(td)
        marker_idx += 1

    # The dim AI-output ghosts are scene-only (no card, filtered in the HUD loop);
    # each marks where one marker's AI-corrected output is actually broadcast while
    # its solid carded marker above sits at the operator-steered anchor.
    state.markers.extend(ghost_entries)

    cam_cfg = app._camera.to_config()
    cam_params_buffer[0] = cam_cfg.pos_x
    cam_params_buffer[1] = cam_cfg.pos_y
    cam_params_buffer[2] = cam_cfg.pos_z
    cam_params_buffer[3] = cam_cfg.pitch
    cam_params_buffer[4] = cam_cfg.yaw
    cam_params_buffer[5] = cam_cfg.roll
    cam_params_buffer[6] = cam_cfg.fov
    state.camera_params = cam_params_buffer.copy()
    # Lens-distortion coefficients live on the app config (the Camera object is
    # pinhole and doesn't carry them); read them straight from the live config so
    # slider edits hot-reload onto the HUD.
    state.lens_k1 = cfg.camera.lens_k1
    state.lens_k2 = cfg.camera.lens_k2

    state.selected_id = app._selected_id

    sync_marker_config(state, cfg)
    sync_grid_config(state, cfg)
    sync_ui_config(state, cfg)

    if person_detector is not None:
        state.detections = person_detector.detections
        dc = cfg.detection
        state.detection_show_boxes = dc.show_boxes
        state.detection_show_labels = dc.show_labels
        state.detection_box_color = dc.box_color
        state.detection_box_thickness = dc.box_thickness
        # Highlight each box attached to a marker in that marker's colour so the
        # operator can see which detection is driving each followspot. Assist
        # drives every controlled marker, so several boxes can be attached.
        attached_colors: dict[int, str] = {}
        for st in app._detection_pin_states.values():
            if st.attached_track_id is not None and st.attached_marker_id is not None:
                attached_colors[st.attached_track_id] = _resolve_marker_color(app, st.attached_marker_id)
        state.detection_attached_colors = attached_colors

    if app._button_detection is not None:
        state.button_detection = app._button_detection.get_state()
    else:
        state.button_detection = None

    cc = cfg.controller
    # Marker next/prev cycling rotates the shared ``_selected_id``. With ≥2
    # controllers connected (gamepads + 3D mice, the unified slot space), the
    # InputManager suppresses the cycling action so operators don't fight over
    # the selection; mirror that by hiding the next/prev rows from the gamepad
    # and 3D mouse help so neither promises a no-op control. Read the same
    # predicate the action gate uses so the help and the action can't drift.
    state.marker_cycle_enabled = app._input_manager is None or app._input_manager.marker_cycle_active()
    state.button_labels = {
        "reset": cc.btn_reset,
        "toggle_help": cc.btn_toggle_help,
        "toggle_zones": cc.btn_toggle_zones,
        "speed_down": cc.btn_speed_down,
        "speed_up": cc.btn_speed_up,
        "move_z_down": cc.btn_move_z_down,
        "move_z_up": cc.btn_move_z_up,
        "next_marker": cc.btn_next_marker,
        "prev_marker": cc.btn_prev_marker,
        "settings": cc.btn_settings,
        "menu_confirm": cc.btn_menu_confirm,
        "menu_cancel": cc.btn_menu_cancel,
        "move_xy_stick": cc.move_xy_stick,
    }
    state.keyboard_labels = {
        "move_layout": cc.key_move_layout,
        "move_z_up": cc.key_move_z_up,
        "move_z_down": cc.key_move_z_down,
        "reset": cc.key_reset,
        "toggle_help": cc.key_toggle_help,
        "toggle_zones": cc.key_toggle_zones,
        "speed_down": cc.key_speed_down,
        "speed_up": cc.key_speed_up,
        "next_marker": cc.key_next_marker,
        "prev_marker": cc.key_prev_marker,
        "settings": cc.key_settings,
    }

    _populate_zone_overlay(state, cfg, app)

    # Only a gamepad lights the gamepad help section / status: a 3D mouse is a
    # unified controller (it gets a card badge) but has its own help section, so
    # its presence must not advertise gamepad-only controls.
    state.controller_connected = cfg.controller.enabled and any(
        bool(info["connected"]) for info in controller_info if info.get("backend") != "mouse3d"
    )
    # Connected pads that map to no marker (more pads than
    # ``controlled_marker_ids``) are surfaced in the Settings menu's info
    # card; there is no bottom-center status panel.
    state.unbound_controller_indices = [
        int(info["controller_index"])
        for info in controller_info
        if info["marker_id"] is None and bool(info["connected"])
    ]
    state.keyboard_connected = (
        cfg.controller.keyboard_enabled
        and app._input_manager is not None
        and app._input_manager.is_keyboard_connected()
    )
    state.mouse_enabled = cfg.controller.mouse_enabled
    state.mouse_double_click_reset = cfg.controller.mouse_double_click_reset
    # 3D Mouse help: show its axis / button bindings when the feature is enabled
    # and a device is actually connected (mirrors the gamepad-connected gate).
    # The maps are only read by the help overlay (gated on mouse3d_connected), so
    # skip building them every frame on the common feature-off path.
    m3d_cfg = cfg.mouse3d
    state.mouse3d_connected = bool(
        m3d_cfg.enabled and app._input_manager is not None and app._input_manager.mouse3d_manager.connected
    )
    if state.mouse3d_connected:
        state.mouse3d_axis_map = {axis: getattr(m3d_cfg, f"map_{axis}") for axis in MOUSE3D_AXES}
        state.mouse3d_buttons = {name[4:]: getattr(m3d_cfg, name) for name in MOUSE3D_BUTTON_FIELDS}
    else:
        state.mouse3d_axis_map = {}
        state.mouse3d_buttons = {}
    state.show_hud_help = app._show_hud_help

    # Virtual fader stack. Read from the running bus and surface only the
    # faders the operator opted into ``show_on_display``. ``getattr`` guard
    # covers boot / mid-restart windows where the runtime services haven't
    # yet constructed the bus; the empty default keeps the renderer's path
    # identical to "no fader configured to show".
    runtime_services = getattr(app, "_runtime_services", None)
    bus = getattr(runtime_services, "_virtual_faders", None) if runtime_services is not None else None
    if bus is not None:
        for index in range(1, bus.fader_count + 1):
            if not bus.show_on_display(index):
                continue
            state.virtual_faders_display.append(
                VirtualFaderDisplayData(
                    index=index,
                    name=bus.name(index),
                    value=bus.value(index),
                    picked_up=bus.is_picked_up(index),
                )
            )

    # Top-right status badge. Snapshot every truthy entry in the runtime's
    # shared status-flags dict; the renderer iterates the list and skips
    # painting entirely on an empty list. ``getattr`` guard mirrors the
    # fader-bus path: boot / mid-restart windows have no flags dict yet,
    # which we surface as "no warnings" rather than crashing the overlay.
    flags_dict = getattr(runtime_services, "_status_flags", None) if runtime_services is not None else None
    if flags_dict:
        # ``dict`` insertion order preserves the order subsystems
        # registered their slots; surface flags in that order so
        # the badge stack reads consistently across frames. Filter
        # to truthy values inline – a key with a ``None`` /
        # empty-string value means the condition cleared.
        # Snapshot the items via ``tuple`` so a concurrent write
        # from a subsystem callback (MIDI hot-reload, peer
        # discovery) can't trigger ``RuntimeError: dictionary
        # changed size during iteration`` mid-frame. Cheap copy
        # – handful of entries.
        for key, raw in tuple(flags_dict.items()):
            if not raw:
                continue
            # A subsystem writes either a plain message string (the
            # back-compat form, styled as an "error") or a
            # ``(severity, message)`` tuple to choose "error" (red) vs
            # "info" (green) badge styling. Be defensive about the tuple's
            # arity – this runs on the per-frame overlay-build path, so a
            # malformed writer (wrong-length tuple) must degrade rather than
            # raise ValueError and abort the frame. An empty tuple was
            # already dropped by the ``if not raw`` guard above, so the
            # first element is always present; a missing message coerces to
            # "" and is filtered out below like a cleared condition.
            if isinstance(raw, tuple):
                severity = raw[0]
                message = raw[1] if len(raw) > 1 else ""
            else:
                severity, message = "error", raw
            if message:
                state.status_flags.append((key, message, severity))

    # Snapshot the store (newest-first), resolve marker name/color for keyed
    # cards, compute each countdown against one frame clock, and cap to
    # ``max_visible`` with the remainder surfaced as ``+N more``. ``getattr``
    # guards windows where services or the store aren't wired yet.
    op_store = getattr(runtime_services, "_operator_message_store", None) if runtime_services is not None else None
    op_cfg = app._config.operator_messages
    if op_store is not None and op_cfg.enabled:
        state.operator_message_position = op_cfg.position
        state.operator_message_scale = op_cfg.scale
        now = time.monotonic()
        messages = op_store.snapshot(now)
        max_visible = op_cfg.max_visible
        state.operator_message_overflow = max(0, len(messages) - max_visible)
        for msg in messages[:max_visible]:
            keyed = msg.marker_id >= 1
            state.operator_messages.append(
                OperatorMessageView(
                    message=msg.message,
                    info=msg.info,
                    marker_id=msg.marker_id,
                    marker_name=_resolve_marker_name(app, msg.marker_id) if keyed else "",
                    marker_color=_resolve_marker_color(app, msg.marker_id) if keyed else "",
                    is_forever=msg.duration_s <= 0.0,
                    remaining_fraction=msg.remaining_fraction(now),
                )
            )

    return state
