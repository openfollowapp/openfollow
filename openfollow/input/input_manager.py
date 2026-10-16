# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Central input coordinator that manages all input sources."""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from openfollow.configuration import SlotMarkerConfig, SlotPinConfig, _apply_with_fallback, save_config_fields
from openfollow.input.control_frame import ControlFrame
from openfollow.input.controller_identity import port_label, usb_host_paths
from openfollow.input.controller_slots import (
    CONNECTED,
    EMPTY,
    MISSING,
    NOTE_CANNOT_IDENTIFY,
    RESERVED,
    ControllerSlotTable,
    LiveController,
    SlotEntry,
    SlotPin,
    pick_marker,
    slot_markers,
    slot_ref,
)
from openfollow.input.events import InputEventBus
from openfollow.input.gamepad import GamepadHandler, GamepadUpdate
from openfollow.input.keyboard import KeyboardHandler
from openfollow.input.mouse import MouseHandler
from openfollow.input.mouse3d import MOUSE3D_NAME, Mouse3DManager, Mouse3DUpdate
from openfollow.input.slot_pin_capture import (
    CANCELLED,
    NO_PORT,
    NO_SLOT,
    PINNED,
    PRESSED,
    TIMED_OUT,
    TIMEOUT,
    CaptureResult,
    PinCapture,
)
from openfollow.input.touch_remote import MAX_REACH_M, RESET, holds, snap_z, stick_velocity
from openfollow.logging_setup import ThrottledExceptionLogger
from openfollow.net_utils import plane_source_iface
from openfollow.osc.input import OscMarkerAdapter
from openfollow.osc.operator_message import OperatorMessageOscAdapter
from openfollow.osc.paths import PathOscAdapter
from openfollow.runtime import paths_runtime
from openfollow.runtime.services_detection_pin import (
    get_or_create_manual_marker,
    hand_over_anchors,
    is_assist_controlled,
)

if TYPE_CHECKING:
    from openfollow.app import OpenFollowApp
    from openfollow.configuration import Mouse3DConfig
    from openfollow.input.touch_remote import RemoteFrame, TouchRemote
    from openfollow.paths.engine import PathEngine
    from openfollow.psn.marker import Marker

logger = logging.getLogger(__name__)


def _table_pins(pins: Sequence[SlotPinConfig]) -> dict[int, SlotPin]:
    """The configuration's 1-based pins as the slot table's 0-based ones."""
    return {pin.slot - 1: SlotPin(key=pin.key, kind=pin.kind, label=pin.label) for pin in pins}


def _occupied(slots: Sequence[SlotEntry]) -> int:
    """Slots that hold or held a controller: empty slots below a pin don't count."""
    return sum(1 for slot in slots if slot.state != EMPTY)


# How long Identify flashes the marker card on the Operator Screen.
_IDENTIFY_FLASH_S = 3.0
# How long /api/stats reports how the latest Pin here ended.
_PIN_RESULT_SHOWN_S = 10.0

# OSC writes to a marker this far apart count as a new stream, logged at INFO.
_OSC_STREAM_GAP_S = 5.0


def resolve_osc_multicast_iface(listen_iface: str, station_iface: str) -> str | None:
    """Interface the OSC listener should take its multicast membership on.

    ``""`` leaves the choice to the routing table, an address pins it, and
    None means an interface is pinned but currently has no address - so no
    membership is taken rather than one on an interface the operator excluded.

    The listener itself is unaffected either way: it binds every interface, so
    unicast and broadcast keep arriving while the group is unavailable.
    """
    from openfollow.net_utils import resolve_multicast_iface

    address, status = resolve_multicast_iface(listen_iface, station_iface)
    return None if status == "down" else address


class InputManager:
    """Coordinates input from keyboard, gamepads, and OSC.

    Owns a single :class:`InputEventBus` that consumers (the OSC
    transmitter manager's Hotkey / ControllerButton trigger dispatch)
    subscribe to via :attr:`event_bus`. The bus survives the lifetime
    of the InputManager – subscribers are responsible for
    unsubscribing on their own shutdown so a stopping consumer can't
    see further events.

    Hardware → bus emission is wired through the keyboard and gamepad
    handlers: each receives the bus at construction and emits press /
    release events on edge transitions. ``KeyboardHandler.poll_discrete_keys``
    and ``GamepadHandler.update`` are the per-frame seams where the
    edge detection runs.

    The two handlers take different shapes here intentionally:
    ``KeyboardHandler`` reuses its single ``_prev_key_state`` map
    because the legacy discrete-action queue and the bus-emission
    path want exactly the same press / release semantics for any
    given key.  ``GamepadHandler``, by contrast, keeps a separate
    ``_button_bus_prev`` from the existing ``_button_prev`` so the
    bus-emission edges don't interfere with the in-handler consumers
    (speed bumpers, calibration mode, button-detection wizard) that
    care about edge state for their own scoped lifecycles.
    """

    def __init__(self, app: OpenFollowApp):
        self.app = app
        # Bus first – handlers need it at construction so edge events
        # can flow from frame zero. Lifetime is the InputManager's.
        self.event_bus = InputEventBus()
        self.keyboard_handler = KeyboardHandler(app, event_bus=self.event_bus)
        # Pass the virtual fader bus to the gamepad handler so its
        # per-tick ``update`` can integrate the chosen stick Y deflection
        # into Fader 1 directly (no app-services dereference on the hot
        # path). Also hand it a resolver mapping controller_idx ->
        # marker_id so the bumper-speed and effective-speed paths target
        # the marker actually routed to that controller.
        # ``_gamepad_marker_id`` is the same hub the movement / reset
        # paths already use, so the four routing surfaces stay in sync.
        # The virtual-fader bus is shared: the gamepad handler integrates its
        # stick into it, and the 3D-mouse fader path (``_apply_mouse3d``) writes
        # to the same reference – captured once here, not re-dereferenced per tick.
        self._virtual_faders = app._runtime_services._virtual_faders
        self.gamepad_handler = GamepadHandler(
            app,
            event_bus=self.event_bus,
            virtual_faders=self._virtual_faders,
            marker_resolver=self._gamepad_marker_id,
        )
        self.mouse_handler = MouseHandler(app)
        self.control_frame = ControlFrame(app)
        # The mouse glide runs every frame; a persistent failure must not flood
        # the log at the frame rate.
        self._mouse_update_err_log = ThrottledExceptionLogger(logger, "Mouse update failed this tick.")
        # 3D Mouse (6DOF). One handler per connected puck, enumerated like
        # gamepads; movement application is gated on ``mouse3d.enabled`` in
        # ``update``.
        self.mouse3d_manager = Mouse3DManager(app._config.mouse3d)
        self.mouse3d_manager.start()
        self._mouse3d_update_err_log = ThrottledExceptionLogger(logger, "3D Mouse update failed this tick.")
        self._remote_update_err_log = ThrottledExceptionLogger(logger, "Touch remote update failed this tick.")
        self._gamepad_move_err_log = ThrottledExceptionLogger(logger, "Gamepad move failed this tick.")
        # Which slot each controller holds for the session; refreshed on the
        # main loop only, read from any thread via ``_controller_slots``.
        self._clock = time.monotonic
        self._slot_table = ControllerSlotTable(pins=_table_pins(app._config.controller.slot_pins))
        self._usb_hosts = usb_host_paths()
        # (slot index, clock time it started) of the latest Identify.
        self._identify_flash: tuple[int, float] | None = None
        # The armed Pin here, and how the latest one ended with its clock time.
        self._pin_capture: PinCapture | None = None
        self._pin_result: tuple[CaptureResult, float] | None = None
        # Clock time of each marker's latest OSC position write.
        self._osc_last_write: dict[int, float] = {}
        # Where each remote-held marker glides to, and for which session, until it lands or is steered.
        self._remote_glides: dict[int, tuple[str, tuple[float, float, float]]] = {}
        self._refresh_slots()

        osc_cfg = app._config.osc
        # Marker-position OSC input flows through the unified OSC
        # service. The adapter subscribes to /marker/* on the shared
        # service and exposes the same flush_updates() shape the rest of
        # the input loop already calls every frame.
        self.osc_handler: OscMarkerAdapter | None = (
            OscMarkerAdapter(
                app._runtime_services._osc_service,
                port=osc_cfg.port,
                allowed_sender_ips=list(osc_cfg.allowed_sender_ips),
                multicast_group=osc_cfg.multicast_group,
                multicast_iface=resolve_osc_multicast_iface(
                    osc_cfg.listen_iface,
                    app._config.psn_source_iface,
                ),
                multicast_iface_name=plane_source_iface(osc_cfg.listen_iface, app._config.psn_source_iface),
            )
            if osc_cfg.enabled
            else None
        )
        if self.osc_handler is not None:
            # The adapter (via ``OscService.start_listener``) raises
            # ``OSError`` on bind failure for the live-apply orchestrator
            # – but at startup we want the legacy ``OscInputHandler``
            # contract: log and continue without OSC input rather than
            # crash the whole input subsystem because one port is in use.
            try:
                self.osc_handler.start()
            except OSError as exc:
                logger.error(
                    "OSC input bind failed on port %d: %s. OSC input disabled; the rest of the input system continues.",
                    osc_cfg.port,
                    exc,
                )
                self.osc_handler = None
        # Operator-message ingest subscribes on the marker adapter's listener;
        # gated on ``osc_handler is not None`` and ``[operator_messages] enabled``.
        self.operator_message_handler: OperatorMessageOscAdapter | None = None
        self._rebuild_operator_message_handler()
        self.path_osc_handler: PathOscAdapter | None = None
        self._rebuild_path_osc_handler()

    def _get_marker(self, marker_id: int) -> Marker | None:
        """Resolve the marker operator input should steer.

        In detection **assist** mode the operator drives a manual *ghost*
        anchor instead of the registered marker (which the detection pin owns
        as the AI-corrected output). Assist refines every controlled marker, so
        for any controlled id under assist we return that ghost so every input
        path – keyboard, gamepad move/reset, OSC – moves the anchor with no
        per-call-site changes. Otherwise we return the registered marker.

        ``app._server`` is ``PsnServer | None``; the strict type checker
        doesn't see the runtime invariant that ``init_psn`` always runs
        before ``init_input_manager``. Centralising the None-guard here
        keeps the call sites readable instead of every gamepad / OSC
        branch carrying its own ``if self.app._server is None`` skip.
        """
        if is_assist_controlled(self.app, marker_id):
            return get_or_create_manual_marker(self.app, marker_id)
        server = self.app._server
        # pragma: no cover – ``init_psn`` runs before ``init_input_manager``
        # in ``OpenFollowApp.run``, so ``app._server`` is always set by the
        # time any input handler reaches this lookup. The None arm exists
        # purely as a strict-mode narrowing aid for ``app._server: PsnServer
        # | None``; production never enters it.
        if server is None:  # pragma: no cover
            return None
        return server.get_marker(marker_id)

    def _paths(self) -> PathEngine | None:
        return getattr(self.app, "_paths", None)

    def _move(self, marker_id: int, marker: Marker, vx: float, vy: float, vz: float, dt: float) -> None:
        """Move by a velocity: along its path for a locked marker, freely otherwise."""
        paths = self._paths()
        on_path = paths.move(marker_id, vx, vy, dt, vz) if paths is not None else None
        if on_path is not None:
            marker.set_pos(*on_path)
            return
        x, y, z = marker.pos
        marker.set_pos(x + vx * dt, y + vy * dt, z + vz * dt)

    def _reset(self, marker_id: int, marker: Marker) -> None:
        """Send a marker to its default position, releasing it from any path."""
        paths = self._paths()
        if paths is not None:
            paths.release(marker_id)
        marker.set_pos(*self.app._get_default_marker_position())

    def pathfinder(self, marker_id: int | None, *, remote: bool = False) -> None:
        """A Pathfinder press for ``marker_id``: the keyboard's selected marker, a controller's own, or the remote's."""
        if not remote and holds(self.app, marker_id):
            return
        marker = self._get_marker(marker_id) if marker_id is not None else None
        if marker_id is not None and marker is not None and self._paths() is not None:
            paths_runtime.pathfinder(self.app, marker_id, marker)

    def _live_controllers(self) -> list[LiveController]:
        """Controllers attached now, of every kind that is switched on.

        A kind switched off leaves its slots missing, like unplugged devices.
        """
        live: list[LiveController] = []
        if self.app._config.controller.enabled:
            for idx, (key, name, notes) in self.gamepad_handler.device_identities().items():
                live.append(LiveController(kind="gamepad", local_id=idx, key=key, name=name, notes=notes))
        if self.app._config.mouse3d.enabled:
            with_led = self.mouse3d_manager.led_ids()
            for idx, info in self.mouse3d_manager.connected_devices().items():
                name = info.product_name or MOUSE3D_NAME
                notes = () if idx in with_led else (NOTE_CANNOT_IDENTIFY,)
                live.append(LiveController(kind="mouse3d", local_id=idx, key=info.port_key, name=name, notes=notes))
        return live

    def _refresh_slots(self) -> tuple[SlotEntry, ...]:
        """Fold this frame's controllers into the slot table (main loop only)."""
        settled = self.mouse3d_manager.initial_scan_settled()
        if self._slot_table.update(self._live_controllers(), settled=settled):
            logger.info("Controller slots: %s", self._describe_slots())
        return self._slot_table.slots

    def _describe_slots(self) -> str:
        parts = []
        for index, slot in enumerate(self._slot_table.slots):
            state = "" if slot.state == CONNECTED else f" {slot.state}"
            parts.append(f"C{index + 1}{state} {slot.name} ({port_label(slot.key, self._usb_hosts)})")
        return ", ".join(parts) or "none"

    def _controller_slots(self) -> tuple[SlotEntry, ...]:
        """The session's controller slots; the position is the unified index
        (0-based internally, 1-based as OSC ``cN`` and the ``C1`` badge).

        Thread-safe: the table publishes an immutable tuple, so the OSC
        scheduler thread and the web threads read it without a lock.
        """
        return self._slot_table.slots

    def _controller_marker_id(self, unified_idx: int | None, slots: tuple[SlotEntry, ...] | None = None) -> int | None:
        """Resolve the marker a unified controller index drives.

        Exactly one slot (a lone controller; empty slots below a pin don't count) and a marker selected: route to
        ``app._selected_id`` so next/prev cycling switches what the operator
        moves. Otherwise the slot's own marker: the one picked for it, else its
        place among the controlled markers no slot picked, so per-operator
        assignments stay stable while a controller is missing too.
        ``None`` for a reserved or empty slot, an index out of range, or no marker.

        Pass ``slots`` to reuse a single per-frame snapshot.
        """
        if unified_idx is None:
            return None
        if slots is None:
            slots = self._controller_slots()
        if not 0 <= unified_idx < len(slots) or slots[unified_idx].state in (RESERVED, EMPTY):
            return None
        if _occupied(slots) == 1 and self.app._selected_id is not None:
            return self.app._selected_id
        return slot_markers(len(slots), self.app._controlled_ids, self._marker_picks())[unified_idx]

    def _marker_picks(self) -> dict[int, int]:
        """The configuration's 1-based marker picks as 0-based slot -> marker."""
        return {pick.slot - 1: pick.marker for pick in self.app._config.controller.slot_markers}

    def pick_slot_marker(self, unified_idx: int, marker_id: int) -> bool:
        """Give a slot one of the controlled markers; the slot that drove it takes this slot's old one. Main loop only.

        Updates ``app._config.controller.slot_markers``; saving it is the caller's. False on a reserved or
        empty slot, a station with one slot (it follows the selection), or a marker this station does not control.
        """
        slots = self._controller_slots()
        if not 0 <= unified_idx < len(slots) or slots[unified_idx].state not in (CONNECTED, MISSING):
            return False
        if _occupied(slots) == 1:
            return False
        picks = pick_marker(len(slots), self.app._controlled_ids, self._marker_picks(), unified_idx, marker_id)
        if picks is None:
            return False
        self.app._config.controller.slot_markers = [
            SlotMarkerConfig(slot=index + 1, marker=marker) for index, marker in sorted(picks.items())
        ]
        logger.info("Controller slots: C%d drives marker %d; %s", unified_idx + 1, marker_id, self._describe_markers())
        return True

    def _describe_markers(self) -> str:
        slots = self._controller_slots()
        return ", ".join(
            f"C{index + 1} {'-' if marker is None else marker}"
            for index, marker in enumerate(slot_markers(len(slots), self.app._controlled_ids, self._marker_picks()))
        )

    @staticmethod
    def _unified_idx(kind: str, local_id: int, slots: tuple[SlotEntry, ...]) -> int | None:
        for i, slot in enumerate(slots):
            if slot.kind == kind and slot.local_id == local_id:
                return i
        return None

    def _gamepad_unified_idx(self, controller_idx: int, slots: tuple[SlotEntry, ...] | None = None) -> int | None:
        """Unified index of a gamepad (by SDL instance id), or ``None`` if it has no slot."""
        return self._unified_idx("gamepad", controller_idx, self._controller_slots() if slots is None else slots)

    def _mouse3d_unified_idx(self, local_idx: int, slots: tuple[SlotEntry, ...] | None = None) -> int | None:
        """Unified index of a 3D mouse (by instance id), or ``None`` if it has no slot."""
        return self._unified_idx("mouse3d", local_idx, self._controller_slots() if slots is None else slots)

    def _gamepad_marker_id(self, controller_idx: int, slots: tuple[SlotEntry, ...] | None = None) -> int | None:
        """Marker a gamepad's movement/reset targets, routed through the shared
        controller-id space (the gamepad's handler key -> its unified slot)."""
        if slots is None:
            slots = self._controller_slots()
        return self._controller_marker_id(self._gamepad_unified_idx(controller_idx, slots), slots)

    def marker_cycle_active(self, slots: tuple[SlotEntry, ...] | None = None) -> bool:
        """Whether next/prev marker cycling should be honoured this frame.

        Cycling rotates the shared ``_selected_id``, so it is a single-controller
        affordance: active only when at most one unified controller (a lone
        gamepad or a lone 3D mouse) holds a slot; a missing slot counts, so nobody
        starts cycling because somebody else unplugged. The action suppression
        (``update`` / ``_apply_mouse3d``) and the help-overlay gate both read
        this one predicate so they can't drift. Pass ``slots`` to reuse a
        per-frame snapshot.
        """
        if slots is None:
            slots = self._controller_slots()
        return _occupied(slots) <= 1

    def controller_marker_id(self, controller_idx: int) -> int | None:
        """Public seam for the OSC ``:cN`` reference. ``controller_idx`` is the
        0-based unified index (``:c1`` -> 0), spanning 3D mice + gamepads.
        """
        return self._controller_marker_id(controller_idx)

    def update(self, dt: float) -> GamepadUpdate:
        """
        Update all input handlers and apply movements to markers.

        Args:
            dt: Delta time in seconds

        Returns:
            GamepadUpdate with movement results and button signals.
        """
        # Keep controller hotplug and LED policy alive even when there are no
        # controlled markers. Movement application remains gated below.
        gamepad_result = self.gamepad_handler.update(dt)

        # One unified-controller snapshot drives all routing this frame,
        # refreshed after gamepad hotplug ran. Next/prev marker cycling is a
        # single-controller affordance: the gamepad handler only self-suppresses
        # for >1 gamepad, so suppress here whenever more than one slot exists –
        # e.g. a gamepad paired with a 3D mouse.
        slots = self._refresh_slots()
        if not self.marker_cycle_active(slots):
            gamepad_result.next_marker_pressed = False
            gamepad_result.prev_marker_pressed = False

        self.control_frame.begin_frame()

        # 3D Mouse (6DOF): consume the latest device snapshot. Button edges fold
        # into the shared action flags; movement/reset/speed route to the marker
        # the mouse's unified controller slot drives. Gated on the live enabled flag.
        if self.app._config.mouse3d.enabled:
            try:
                for local_idx, m3d in self.mouse3d_manager.update(dt).items():
                    self._apply_mouse3d(m3d, local_idx, dt, gamepad_result, slots)
            except Exception:
                self._mouse3d_update_err_log.log()

        # Mouse marker control glides toward the cursor target every frame so
        # smoothing is independent of pointer-event rate. The handler no-ops
        # when not actively grabbing a marker.
        if self.app._config.controller.mouse_enabled:
            try:
                self.mouse_handler.update()
            except Exception:
                self._mouse_update_err_log.log()

        # MIDI hotplug: keep the patch-missing badge (and listener ports)
        # tracking live hardware between config saves, mirroring the gamepad
        # hotplug above. Runs before the no-controlled-markers early-exit so an
        # unplug is noticed regardless of marker state. Throttled inside
        # ``poll_hotplug`` so this per-tick call stays cheap.
        # Isolate later subsystems so one raising can't drop the gamepad result.
        midi = getattr(getattr(self.app, "_runtime_services", None), "_midi", None)
        if midi is not None:
            try:
                midi.poll_hotplug()
            except Exception:
                logger.exception("MIDI hotplug poll failed this tick.")

        # Early exit for movement if no controlled markers
        if not self.app._controlled_ids:
            return gamepad_result

        paths = self._paths()

        # Get keyboard velocity for selected marker
        try:
            keyboard_velocity = self.keyboard_handler.update(dt)
            if (
                keyboard_velocity is not None
                and self.app._selected_id is not None
                and self.app._config.controller.keyboard_enabled
                and not holds(self.app, self.app._selected_id)
            ):
                marker = self._get_marker(self.app._selected_id)
                if marker:
                    vx, vy, vz = keyboard_velocity
                    vx, vy = self.control_frame.orient(("keyboard", self.app._selected_id), vx, vy, marker.pos)
                    self._move(self.app._selected_id, marker, vx, vy, vz, dt)
        except Exception:
            logger.exception("Keyboard update failed this tick.")

        # Apply gamepad resets (X button -> configurable default position)
        for controller_idx in gamepad_result.resets:
            marker_id = self._gamepad_marker_id(controller_idx, slots)
            if marker_id is None or holds(self.app, marker_id):
                continue
            marker = self._get_marker(marker_id)
            if marker:
                default_pos = self.app._get_default_marker_position()
                self._reset(marker_id, marker)
                logger.info(
                    "Gamepad instance %s reset marker %s to default position (%s, %s, %s)",
                    controller_idx,
                    marker_id,
                    default_pos[0],
                    default_pos[1],
                    default_pos[2],
                )

        # Apply gamepad movements to corresponding markers
        for controller_idx, (vx, vy, vz) in gamepad_result.movements.items():
            marker_id = self._gamepad_marker_id(controller_idx, slots)
            if marker_id is None or holds(self.app, marker_id):
                continue
            marker = self._get_marker(marker_id)
            if marker:
                # One pad's failure never costs the rest of the frame (other pads, OSC, paths).
                try:
                    ovx, ovy = self.control_frame.orient(("gamepad", controller_idx, marker_id), vx, vy, marker.pos)
                    self._move(marker_id, marker, ovx, ovy, vz, dt)
                except Exception:
                    self._gamepad_move_err_log.log()

        for controller_idx in gamepad_result.pathfinder_presses:
            self.pathfinder(self._gamepad_marker_id(controller_idx, slots))

        # Apply OSC position jumps (absolute writes – full triples
        # overwrite all axes, per-axis updates merge with the marker's
        # current position so the un-set axes stay where they were).
        if self.osc_handler is not None:
            try:
                for marker_id, axes in self.osc_handler.flush_updates().items():
                    if marker_id not in self.app._controlled_ids or holds(self.app, marker_id):
                        continue
                    marker = self._get_marker(marker_id)
                    if not marker:
                        continue
                    # While pulled onto a path, the axes left out come from where it would be without the path.
                    free = paths.free_position(marker_id) if paths is not None else None
                    cur_x, cur_y, cur_z = free if free is not None else marker.pos
                    x = axes.get("x", cur_x)
                    y = axes.get("y", cur_y)
                    z = axes.get("z", cur_z)
                    landed = paths.project(marker_id, x, y) if paths is not None else None
                    x, y, z = landed if landed is not None else (x, y, z)
                    marker.set_pos(x, y, z)
                    now = self._clock()
                    last = self._osc_last_write.get(marker_id)
                    self._osc_last_write[marker_id] = now
                    if last is None or now - last >= _OSC_STREAM_GAP_S:
                        logger.info(
                            "OSC positions arriving for marker %d, first at (%.3f, %.3f, %.3f)",
                            marker_id,
                            x,
                            y,
                            z,
                        )
                    logger.debug(
                        "OSC jump: marker %d → (%.3f, %.3f, %.3f)",
                        marker_id,
                        x,
                        y,
                        z,
                    )
            except Exception:
                logger.exception("OSC flush/apply failed this tick.")

        return gamepad_result

    def update_paths(self, dt: float) -> None:
        """Run the path engine for one frame, whatever input does: menus open or no marker controlled.

        Hands the lock of a marker assist no longer drives from its anchor to its output, releases
        markers no longer controlled, applies OSC path commands, then writes the markers the engine
        moved itself (slides, edits). ``animate`` calls it after the frame's discrete key presses
        (Reset, Pathfinder) and before :meth:`update`.
        """
        commands = self.path_osc_handler.take() if self.path_osc_handler is not None else []
        paths = self._paths()
        if paths is None:
            return
        hand_over_anchors(self.app)
        paths.release_all(keep=self.app._controlled_ids)
        # An OSC lock or release never reaches a marker a remote holds.
        steerable = [m for m in self.app._controlled_ids if not holds(self.app, m)]
        paths_runtime.apply_commands(self.app, commands, steerable, self._get_marker)
        for slid_id, slid_to in paths.tick(dt).items():
            slid = self._get_marker(slid_id)
            if slid:
                slid.set_pos(*slid_to)

    def update_touch_remote(self, dt: float) -> None:
        """Apply what the web touch remote asks of each marker it holds, whatever the menus do.

        ``animate`` calls it every frame after :meth:`update_paths`. The stick is relative input:
        shaped by ``[touch_remote]``, scaled to the station's ``marker.max_speed`` and oriented like
        every other relative input. A hold with person detection on switches detection off.
        """
        remote = getattr(self.app, "_touch_remote", None)
        if remote is None:
            return
        try:
            self._apply_touch_remote(remote, dt)
        except Exception:
            self._remote_update_err_log.log()

    def _apply_touch_remote(self, remote: TouchRemote, dt: float) -> None:
        cfg = self.app._config
        if not cfg.touch_remote.enabled:
            remote.release_all()
        remote.release_uncontrolled(self.app._controlled_ids)
        frames = remote.take()
        holders = {frame.marker_id: frame.holder for frame in frames}
        for marker_id in [m for m, (who, _t) in self._remote_glides.items() if holders.get(m) != who]:
            del self._remote_glides[marker_id]
        # Once per new hold, before its presses, so they move the marker and not an assist anchor;
        # detection switched back on later while held stays on.
        if any(frame.new for frame in frames) and cfg.detection.enabled:
            self._switch_detection_off()
        for frame in frames:
            # One marker's failure never costs another marker its presses.
            try:
                self._apply_remote_frame(frame, dt)
            except Exception:
                self._remote_update_err_log.log()

    def _apply_remote_frame(self, frame: RemoteFrame, dt: float) -> None:
        cfg = self.app._config
        tuning = cfg.touch_remote
        max_speed = cfg.marker.max_speed
        paths = self._paths()
        marker_id = frame.marker_id
        marker = self._get_marker(marker_id)
        if marker is None:
            return
        for action in frame.actions:
            self._remote_glides.pop(marker_id, None)
            if action.target is not None:
                paths_runtime.release(self.app, marker_id)
                self._remote_glides[marker_id] = (frame.holder, action.target)
            elif action.kind == RESET:
                self._reset(marker_id, marker)
            else:
                self.pathfinder(marker_id, remote=True)
        vx, vy = stick_velocity(frame.stick, tuning.stick_curve, tuning.stick_dead_zone, max_speed)
        if vx or vy:
            self._remote_glides.pop(marker_id, None)
            # Stage directions: a tablet operator need not be watching the camera's picture.
            self._move(marker_id, marker, vx, vy, 0.0, dt)
        if frame.z_notches and not (paths is not None and paths.is_locked(marker_id)):
            self._remote_glides.pop(marker_id, None)
            x, y, z = marker.pos
            z = snap_z(z, frame.z_notches, tuning.z_step_m)
            marker.set_pos(x, y, max(-MAX_REACH_M, min(MAX_REACH_M, z)))
        # The dead-man holds a glide where it is; it carries on once drive messages arrive again.
        glide = self._remote_glides.get(marker_id) if frame.live else None
        if glide is not None:
            self._glide(marker_id, marker, glide[1], max_speed * dt)

    def _glide(self, marker_id: int, marker: Marker, target: tuple[float, float, float], step: float) -> None:
        x, y, z = marker.pos
        tx, ty, tz = target
        distance = math.dist((x, y, z), target)
        # A target too far for a float distance lands at once rather than never.
        if distance <= step or not math.isfinite(distance):
            marker.set_pos(tx, ty, tz)
            del self._remote_glides[marker_id]
            return
        k = step / distance
        marker.set_pos(x + (tx - x) * k, y + (ty - y) * k, z + (tz - z) * k)

    def _switch_detection_off(self) -> None:
        """Turn person detection off, live and in the file, as a hot reload would: a remote took a marker.

        A new config object, never an edit of the one the detector worker holds; reverted if it fails.
        """
        app = self.app
        services = app._runtime_services
        before = app._config.detection
        app._config.detection = replace(before, enabled=False)
        applied = _apply_with_fallback(
            "detection",
            lambda: services.apply_detection_change(app._config.detection),
            on_failure=lambda: setattr(services, "_person_detector", None),
        )
        if not applied:
            app._config.detection = before
            return
        try:
            save_config_fields(app._config, app._config_path, ("detection.enabled",))
        except Exception:
            logger.exception("Failed to save person detection switched off.")
        logger.warning("The touch remote took a marker: person detection is switched off.")

    def _apply_mouse3d(
        self,
        m3d: Mouse3DUpdate,
        local_idx: int,
        dt: float,
        gamepad_result: GamepadUpdate,
        slots: tuple[SlotEntry, ...],
    ) -> None:
        """Apply one connected 3D Mouse's frame (``local_idx`` = its instance id).

        Discrete button edges fold into the shared :class:`GamepadUpdate` flags
        so the app's existing dispatch handles them with no second dispatch site.
        Next/prev cycling is a single-controller affordance, so it only folds
        when this is the sole controller. Movement, reset and speed target the
        marker this puck's unified slot drives (the selected marker when it's the
        only controller, its fixed slot otherwise); the velocity is a unit rate
        scaled here by the marker's move-speed. ``slots`` is the frame's
        unified-controller snapshot.
        """
        if self.marker_cycle_active(slots):
            if m3d.next_marker:
                gamepad_result.next_marker_pressed = True
            if m3d.prev_marker:
                gamepad_result.prev_marker_pressed = True
        if m3d.toggle_help:
            gamepad_result.toggle_help_pressed = True
        if m3d.toggle_zones:
            gamepad_result.toggle_zones_pressed = True
        if m3d.toggle_paths:
            gamepad_result.toggle_paths_pressed = True

        marker_id = self._controller_marker_id(self._mouse3d_unified_idx(local_idx, slots), slots)
        if marker_id is None:
            return
        marker = self._get_marker(marker_id)
        if marker is None:
            return
        # A remote hold blocks what moves the marker; its speed and fader stay with the puck.
        held = holds(self.app, marker_id)
        if m3d.reset and not held:
            self._reset(marker_id, marker)
        if m3d.pathfinder:
            self.pathfinder(marker_id)
        if m3d.speed_steps:
            direction = 1 if m3d.speed_steps > 0 else -1
            for _ in range(abs(m3d.speed_steps)):
                self.app.adjust_move_speed(direction, marker_id=marker_id)
        vx, vy, vz = m3d.velocity
        if (vx or vy or vz) and not held:
            vx, vy = self.control_frame.orient(("mouse3d", local_idx, marker_id), vx, vy, marker.pos)
            speed = self.app.get_marker_move_speed(marker_id)
            self._move(marker_id, marker, vx * speed, vy * speed, vz * speed, dt)
        if m3d.fader_signal:
            # ``fader``-mapped axes integrate into the marker's fader, mirroring
            # the gamepad marker-fader stick: delta = signal × dt / max_speed_s.
            # A marker with no provisioned fader is a clean no-op in the bus.
            bus = self._virtual_faders
            if bus is not None:
                max_speed_s = self.app._config.controller.marker_fader_max_speed_s
                bus.set_marker_fader_from_velocity_delta(marker_id, m3d.fader_signal * dt / max_speed_s)

    def get_controller_info(self) -> list[dict[str, Any]]:
        """One entry per controller slot, in slot order, for the HUD marker-card
        badge, OSC ``:cN``, the web Controller Slots table and ``/api/stats``.

        ``controller_index`` is the 0-based unified index (rendered 1-based).
        ``state`` is ``connected``, ``missing`` (its device left; the slot keeps
        its marker), ``reserved`` (forgotten: drives nothing) or ``empty`` (kept
        free below a pin; drives nothing). ``pinned`` slots belong to the socket
        named by ``port_key``. ``marker_id``
        mirrors the routing, so the badge sits where movement goes.
        ``seconds_since_input`` is ``None`` until the device has been used.
        ``notes`` says what the controller can't do (``NOTE_*``); a missing slot
        keeps its last controller's.
        """
        gamepad_info = {int(item["controller_index"]): item for item in self.gamepad_handler.get_controller_info()}
        mouse_devices = self.mouse3d_manager.connected_devices()
        last_input = {
            **{("gamepad", k): v for k, v in self.gamepad_handler.last_input().items()},
            **{("mouse3d", k): v for k, v in self.mouse3d_manager.last_input().items()},
        }
        now = self._clock()
        slots = self._controller_slots()
        out: list[dict[str, Any]] = []
        for unified_idx, slot in enumerate(slots):
            marker_id = self._controller_marker_id(unified_idx, slots)
            live = slot.state == CONNECTED
            used = last_input.get((slot.kind, slot.local_id)) if live and slot.local_id is not None else None
            if slot.kind == "gamepad":
                pad = gamepad_info.get(slot.local_id, {}) if slot.local_id is not None else {}
                backend = str(pad.get("backend", ""))
                speed = float(pad.get("effective_speed", 0.0)) if live else 0.0
            elif slot.kind == "mouse3d":
                backend = "mouse3d"
                speed = self.app.get_marker_move_speed(marker_id) if live and marker_id is not None else 0.0
            else:
                backend, speed = "", 0.0
            item: dict[str, Any] = {
                "controller_index": unified_idx,
                "name": slot.name,
                "kind": slot.kind,
                "state": slot.state,
                "connected": live,
                "marker_id": marker_id,
                "effective_speed": speed,
                "backend": backend,
                "port_key": slot.key,
                "port_label": port_label(slot.key, self._usb_hosts),
                "pinned": slot.pinned,
                # One slot follows the selection: it drives whichever marker is selected.
                "all_markers": _occupied(slots) == 1 and slot.state not in (RESERVED, EMPTY),
                "slot_ref": slot_ref(slot),
                "seconds_since_input": None if used is None else max(0.0, now - used),
                "notes": list(slot.notes),
            }
            if slot.kind == "mouse3d":
                info = mouse_devices.get(slot.local_id) if slot.local_id is not None else None
                item["product_name"] = info.product_name if info is not None else ""
                item["serial"] = info.serial if info is not None else ""
            out.append(item)
        return out

    def slot_ref(self, unified_idx: int) -> str | None:
        """What the slot at ``unified_idx`` holds now, to check a queued action against."""
        slots = self._controller_slots()
        return slot_ref(slots[unified_idx]) if 0 <= unified_idx < len(slots) else None

    def pin_slot(self, unified_idx: int, kind: str, local_id: int) -> bool:
        """Pin the socket of controller ``(kind, local_id)`` to a slot, moving it there. Main loop only."""
        if not self._slot_table.pin(unified_idx, kind, local_id):
            return False
        key = self._slot_table.slots[unified_idx].key
        logger.info(
            "Controller slots: C%d pinned to %s; %s",
            unified_idx + 1,
            port_label(key, self._usb_hosts),
            self._describe_slots(),
        )
        return True

    @property
    def pin_capture_active(self) -> bool:
        return self._pin_capture is not None

    def start_pin_capture(self, unified_idx: int) -> bool:
        """Arm Pin here: the next button pressed on any controller pins its socket to the slot. Main loop only.

        While armed, :meth:`update` must not run; :meth:`poll_pin_capture` reads the controllers instead.
        """
        if self._pin_capture is not None or not 0 <= unified_idx < len(self._controller_slots()):
            return False
        self._pin_capture = PinCapture(unified_idx, now=self._clock(), pad_held=self._held_pad_inputs())
        # A puck press latched before the capture armed is not the answer.
        self._take_puck_presses()
        self._pin_result = None
        logger.info("Controller slots: waiting for a button press to pin C%d.", unified_idx + 1)
        return True

    def poll_pin_capture(self) -> CaptureResult | None:
        """Advance the armed capture; how it ended, on the frame it ends. Main loop only."""
        capture = self._pin_capture
        if capture is None:
            return None
        # A controller plugged in while waiting takes a slot, so pressing it pins it.
        self._refresh_slots()
        step = capture.step(
            self._clock(),
            self._held_pad_inputs(),
            self._take_puck_presses(),
            self.mouse3d_manager.held_buttons() if self.app._config.mouse3d.enabled else {},
        )
        if step == TIMED_OUT:
            return self._end_pin_capture(CaptureResult(capture.slot, TIMEOUT))
        if step != PRESSED or capture.pressed is None:
            return None
        return self._end_pin_capture(self._pin_pressed(capture.slot, *capture.pressed))

    def cancel_pin_capture(self) -> CaptureResult | None:
        """End the armed capture without pinning; ``None`` when none is armed. Main loop only."""
        capture = self._pin_capture
        return None if capture is None else self._end_pin_capture(CaptureResult(capture.slot, CANCELLED))

    def pin_capture_status(self) -> dict[str, Any]:
        """The armed capture's slot and seconds left, and how the latest one ended while it is recent."""
        now = self._clock()
        capture = self._pin_capture
        result = self._pin_result
        recent = result is not None and now - result[1] < _PIN_RESULT_SHOWN_S
        return {
            "slot": None if capture is None else capture.slot,
            "seconds_left": 0.0 if capture is None else capture.seconds_left(now),
            "result": (
                {"slot": result[0].slot, "outcome": result[0].outcome, "detail": result[0].detail}
                if result is not None and recent
                else None
            ),
        }

    def _held_pad_inputs(self) -> dict[int, frozenset[int]]:
        return self.gamepad_handler.held_inputs() if self.app._config.controller.enabled else {}

    def _take_puck_presses(self) -> dict[int, set[int]]:
        return self.mouse3d_manager.take_presses() if self.app._config.mouse3d.enabled else {}

    def _pin_pressed(self, index: int, kind: str, local_id: int) -> CaptureResult:
        slot = next(
            (s for s in self._controller_slots() if s.state == CONNECTED and (s.kind, s.local_id) == (kind, local_id)),
            None,
        )
        if slot is None:
            return CaptureResult(index, NO_SLOT)
        if slot.key is None:
            return CaptureResult(index, NO_PORT, slot.name)
        self.pin_slot(index, kind, local_id)
        return CaptureResult(index, PINNED, port_label(slot.key, self._usb_hosts))

    def _end_pin_capture(self, result: CaptureResult) -> CaptureResult:
        self._pin_capture = None
        # The press that ended it, or any made while waiting, must not fire an action now.
        self.gamepad_handler.resync_edges()
        self.mouse3d_manager.take_presses()
        self._pin_result = (result, self._clock())
        if result.outcome != PINNED:
            logger.info("Controller slots: pinning C%d ended without a pin (%s).", result.slot + 1, result.outcome)
        return result

    def unpin_slot(self, unified_idx: int) -> bool:
        """Drop a slot's pin; nothing moves. Main loop only."""
        unpinned = self._slot_table.unpin(unified_idx)
        if unpinned:
            logger.info("Controller slots: C%d unpinned; %s", unified_idx + 1, self._describe_slots())
        return unpinned

    def slot_pins(self) -> list[SlotPinConfig]:
        """The table's pins as the configuration stores them, by slot."""
        return [
            SlotPinConfig(slot=index + 1, key=pin.key, kind=pin.kind, label=pin.label)
            for index, pin in sorted(self._slot_table.pins.items())
        ]

    def apply_slot_pins(self, pins: Sequence[SlotPinConfig]) -> None:
        """Take the configuration's pins, moving controllers to them. Main loop only."""
        before = self._slot_table.slots
        self._slot_table.set_pins(_table_pins(pins))
        if self._slot_table.slots != before:
            logger.info("Controller slots: pins changed; %s", self._describe_slots())

    def forget_slot(self, unified_idx: int) -> bool:
        """Silence a missing slot; it keeps its place. Main loop only."""
        forgotten = self._slot_table.forget(unified_idx)
        if forgotten:
            logger.info("Controller slots: C%d forgotten; %s", unified_idx + 1, self._describe_slots())
        return forgotten

    def identify_slot(self, unified_idx: int) -> bool:
        """Flash the slot's marker card on the HUD and pulse its device where it can.

        Returns whether the device itself responded (rumble or LED); the card
        flashes either way. Main loop only: pads are driven on this thread.
        """
        slots = self._controller_slots()
        if not 0 <= unified_idx < len(slots):
            return False
        self._identify_flash = (unified_idx, self._clock())
        slot = slots[unified_idx]
        if slot.state != CONNECTED or slot.local_id is None:
            return False
        if slot.kind == "gamepad":
            return self.gamepad_handler.identify(slot.local_id)
        return self.mouse3d_manager.identify(slot.local_id)

    def identify_flash_marker(self) -> int | None:
        """Marker whose card is lit right now by a running Identify (on/off at 2 Hz)."""
        flash = self._identify_flash
        if flash is None:
            return None
        elapsed = self._clock() - flash[1]
        if elapsed >= _IDENTIFY_FLASH_S or int(elapsed * 4) % 2:
            return None
        return self._controller_marker_id(flash[0])

    def get_marker_gamepad_speeds(self) -> dict[int, float]:
        """
        Map marker_id -> effective gamepad movement speed in m/s.

        Only includes markers that have both a controller and a controlled marker mapping.
        """
        controller_speeds = self.gamepad_handler.get_controller_effective_speeds()
        marker_speeds: dict[int, float] = {}
        for controller_idx, speed in controller_speeds.items():
            marker_id = self._gamepad_marker_id(controller_idx)
            if marker_id is not None:
                marker_speeds[marker_id] = speed
        return marker_speeds

    def is_keyboard_connected(self) -> bool:
        """Return whether keyboard guidance should be shown in the overlay."""
        return self.keyboard_handler.is_connected()

    def restart_osc(
        self,
        enabled: bool,
        port: int,
        allowed_sender_ips: list[str] | None = None,
        *,
        multicast_group: str = "",
        listen_iface: str = "",
    ) -> None:
        """Stop the current OSC handler (if any) and start a new one if enabled.

        Same bind-failure contract as ``__init__``: an ``OSError`` on
        listener bind logs an error and leaves ``osc_handler=None``
        instead of crashing the live-reload pass. The dispatcher's
        config-revert path then re-attempts on the next reload pass.

        The operator-message adapter is torn down and rebuilt alongside the
        marker adapter, since it subscribes on that listener.
        """
        # Drop the operator-message adapter before tearing the listener down.
        self._stop_operator_message_handler()
        self._stop_path_osc_handler()
        if self.osc_handler is not None:
            self.osc_handler.stop()
            self.osc_handler = None
        self._osc_last_write.clear()
        if enabled:
            self.osc_handler = OscMarkerAdapter(
                self.app._runtime_services._osc_service,
                port=port,
                allowed_sender_ips=list(allowed_sender_ips or []),
                multicast_group=multicast_group,
                multicast_iface=resolve_osc_multicast_iface(
                    listen_iface,
                    self.app._config.psn_source_iface,
                ),
                multicast_iface_name=plane_source_iface(listen_iface, self.app._config.psn_source_iface),
            )
            try:
                self.osc_handler.start()
            except OSError as exc:
                logger.error(
                    "OSC input restart failed on port %d: %s. OSC input disabled.",
                    port,
                    exc,
                )
                self.osc_handler = None
        self._rebuild_operator_message_handler()
        self._rebuild_path_osc_handler()

    def restart_mouse3d(self, config: Mouse3DConfig) -> None:
        """Live-apply a 3D Mouse config change.

        Swaps the mapping config, then starts or stops the read thread to match
        ``enabled`` (the device is only read while the feature is on). Movement
        application is additionally gated on the live ``enabled`` read in
        :meth:`update`.
        """
        self.mouse3d_manager.reload_config(config)
        if config.enabled:
            self.mouse3d_manager.start()
        else:
            self.mouse3d_manager.stop()

    def restart_operator_messages(self) -> None:
        """Re-evaluate the operator-message adapter against current config.

        Only the ``enabled`` gate needs handler action; ``max_visible`` /
        ``position`` are read live by the overlay builder.
        """
        self._rebuild_operator_message_handler()

    def _rebuild_operator_message_handler(self) -> None:
        """Stop any existing operator-message adapter and start a fresh one
        when the listener is up and ``[operator_messages]`` is enabled.
        """
        self._stop_operator_message_handler()
        op_cfg = self.app._config.operator_messages
        services = self.app._runtime_services
        # Adapter requires a live listener (``osc_handler``).
        if self.osc_handler is None or not op_cfg.enabled:
            # Disabled / no listener: drop retained cards.
            services._operator_message_store.clear_all()
            return
        self.operator_message_handler = OperatorMessageOscAdapter(
            services._osc_service,
            services._operator_message_store,
            get_controlled_marker_ids=lambda: set(self.app._controlled_ids),
            route_by_marker=op_cfg.route_by_marker,
        )
        self.operator_message_handler.start()

    def _stop_operator_message_handler(self) -> None:
        if self.operator_message_handler is not None:
            self.operator_message_handler.stop()
            self.operator_message_handler = None

    def _rebuild_path_osc_handler(self) -> None:
        """Subscribe the path commands whenever the OSC listener is up."""
        self._stop_path_osc_handler()
        if self.osc_handler is None:
            return
        self.path_osc_handler = PathOscAdapter(self.app._runtime_services._osc_service)
        self.path_osc_handler.start()

    def _stop_path_osc_handler(self) -> None:
        if self.path_osc_handler is not None:
            self.path_osc_handler.stop()
            self.path_osc_handler = None

    def stop(self) -> None:
        """Stop input subsystems and release resources."""
        self.gamepad_handler.stop()
        # Shutdown: join the worker so the device is released before teardown.
        # The live-disable path (restart_mouse3d) uses the non-blocking default.
        self.mouse3d_manager.stop(wait=True)
        self._stop_operator_message_handler()
        self._stop_path_osc_handler()
        if self.osc_handler is not None:
            self.osc_handler.stop()
