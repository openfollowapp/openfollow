# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Central input coordinator that manages all input sources."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from openfollow.input.events import InputEventBus
from openfollow.input.gamepad import GamepadHandler, GamepadUpdate
from openfollow.input.keyboard import KeyboardHandler
from openfollow.input.mouse import MouseHandler
from openfollow.input.mouse3d import MOUSE3D_NAME, Mouse3DHandler, Mouse3DUpdate
from openfollow.logging_setup import ThrottledExceptionLogger
from openfollow.osc.input import OscMarkerAdapter
from openfollow.osc.operator_message import OperatorMessageOscAdapter
from openfollow.runtime.services_detection_pin import (
    assist_pinned_marker_id,
    get_or_create_manual_marker,
)

if TYPE_CHECKING:
    from openfollow.app import OpenFollowApp
    from openfollow.configuration import Mouse3DConfig
    from openfollow.psn.marker import Marker

logger = logging.getLogger(__name__)


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
        self.gamepad_handler = GamepadHandler(
            app,
            event_bus=self.event_bus,
            virtual_faders=app._runtime_services._virtual_faders,
            marker_resolver=self._gamepad_marker_id,
        )
        self.mouse_handler = MouseHandler(app)
        # The mouse glide runs every frame; a persistent failure must not flood
        # the log at the display tick rate.
        self._mouse_update_err_log = ThrottledExceptionLogger(logger, "Mouse update failed this tick.")
        # 3D Mouse (6DOF). The read thread runs for the handler's lifetime;
        # movement application is gated on ``mouse3d.enabled`` in ``update``.
        self.mouse3d_handler = Mouse3DHandler(app._config.mouse3d)
        self.mouse3d_handler.start()
        self._mouse3d_update_err_log = ThrottledExceptionLogger(logger, "3D Mouse update failed this tick.")

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

    def _get_marker(self, marker_id: int) -> Marker | None:
        """Resolve the marker operator input should steer.

        In detection **assist** mode the operator drives a manual *ghost*
        anchor instead of the registered marker (which the detection pin owns
        as the AI-corrected output). For the assist-pinned id we return that
        ghost so every input path – keyboard, gamepad move/reset, OSC – moves
        the anchor with no per-call-site changes. Otherwise we return the
        registered marker as before.

        ``app._server`` is ``PsnServer | None``; the strict type checker
        doesn't see the runtime invariant that ``init_psn`` always runs
        before ``init_input_manager``. Centralising the None-guard here
        keeps the call sites readable instead of every gamepad / OSC
        branch carrying its own ``if self.app._server is None`` skip.
        """
        if marker_id == assist_pinned_marker_id(self.app):
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

    def _controller_slots(self) -> list[tuple[str, int]]:
        """Ordered controllers in the shared id space: 3D mice first, then
        gamepads. The position in this list is the unified controller index
        (0-based internally; surfaced 1-based as the OSC ``cN`` / the ``C1``
        marker-card badge). A 3D mouse counts only while enabled and connected,
        so merely enabling the feature doesn't shift the gamepad numbering.

        Thread-safe snapshot – called from the OSC scheduler thread as well as
        the main loop. ``gamepad_handler.joysticks`` is rebuilt on the main loop
        (``_detect_controllers`` reassigns then repopulates), so iterating it can
        race; fall back to no gamepads for that one call rather than raise.
        """
        slots: list[tuple[str, int]] = []
        if self.app._config.mouse3d.enabled and self.mouse3d_handler.connected:
            slots.append(("mouse3d", 0))
        try:
            gamepad_keys = sorted(self.gamepad_handler.joysticks)
        except RuntimeError:  # dict changed size mid-iteration on the main loop
            gamepad_keys = []
        for idx in gamepad_keys:
            slots.append(("gamepad", idx))
        return slots

    def _controller_marker_id(self, unified_idx: int | None, slots: list[tuple[str, int]] | None = None) -> int | None:
        """Resolve the marker a unified controller index drives.

        Exactly one controller total (a lone gamepad or a lone 3D mouse) and a
        marker selected: route to ``app._selected_id`` so next/prev cycling
        switches what the operator moves. Otherwise the fixed slot
        ``app._controlled_ids[unified_idx]`` so per-operator assignments stay
        stable. ``None`` when the index isn't a live controller or has no slot.

        Pass ``slots`` to reuse a single per-frame snapshot instead of
        recomputing the controller list on every lookup.
        """
        if unified_idx is None:
            return None
        if slots is None:
            slots = self._controller_slots()
        if not 0 <= unified_idx < len(slots):
            return None
        if len(slots) == 1 and self.app._selected_id is not None:
            return self.app._selected_id
        controlled = self.app._controlled_ids
        if unified_idx < len(controlled):
            return controlled[unified_idx]
        return None

    def _gamepad_unified_idx(self, controller_idx: int, slots: list[tuple[str, int]] | None = None) -> int | None:
        """Unified index for a gamepad's pygame index, or ``None`` if absent."""
        if slots is None:
            slots = self._controller_slots()
        for i, (kind, local_idx) in enumerate(slots):
            if kind == "gamepad" and local_idx == controller_idx:
                return i
        return None

    def _mouse3d_unified_idx(self, slots: list[tuple[str, int]] | None = None) -> int | None:
        """Unified index of the 3D mouse controller, or ``None`` if not live."""
        if slots is None:
            slots = self._controller_slots()
        for i, (kind, _local) in enumerate(slots):
            if kind == "mouse3d":
                return i
        return None

    def _gamepad_marker_id(self, controller_idx: int, slots: list[tuple[str, int]] | None = None) -> int | None:
        """Marker a gamepad's movement/reset targets, routed through the shared
        controller-id space (the gamepad's pygame index -> its unified slot)."""
        if slots is None:
            slots = self._controller_slots()
        return self._controller_marker_id(self._gamepad_unified_idx(controller_idx, slots), slots)

    def marker_cycle_active(self, slots: list[tuple[str, int]] | None = None) -> bool:
        """Whether next/prev marker cycling should be honoured this frame.

        Cycling rotates the shared ``_selected_id``, so it is a single-controller
        affordance: active only when at most one unified controller (a lone
        gamepad or a lone 3D mouse) is present. The action suppression
        (``update`` / ``_apply_mouse3d``) and the help-overlay gate both read
        this one predicate so they can't drift. Pass ``slots`` to reuse a
        per-frame snapshot.
        """
        if slots is None:
            slots = self._controller_slots()
        return len(slots) <= 1

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

        # One unified-controller snapshot drives all routing this frame (also
        # avoids rebuilding the slot list per lookup). Next/prev marker cycling
        # is a single-controller affordance: the gamepad handler only
        # self-suppresses for >1 gamepad, so suppress here whenever the unified
        # count (3D mice + gamepads) exceeds one – e.g. a gamepad paired with a
        # 3D mouse.
        slots = self._controller_slots()
        if not self.marker_cycle_active(slots):
            gamepad_result.next_marker_pressed = False
            gamepad_result.prev_marker_pressed = False

        # 3D Mouse (6DOF): consume the latest device snapshot. Button edges fold
        # into the shared action flags; movement/reset/speed route to the marker
        # the mouse's unified controller slot drives. Gated on the live enabled flag.
        if self.app._config.mouse3d.enabled:
            try:
                self._apply_mouse3d(self.mouse3d_handler.update(dt), dt, gamepad_result, slots)
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

        # Get keyboard velocity for selected marker
        try:
            keyboard_velocity = self.keyboard_handler.update(dt)
            if (
                keyboard_velocity is not None
                and self.app._selected_id is not None
                and self.app._config.controller.keyboard_enabled
            ):
                marker = self._get_marker(self.app._selected_id)
                if marker:
                    vx, vy, vz = keyboard_velocity
                    x, y, z = marker.pos
                    marker.set_pos(x + vx * dt, y + vy * dt, z + vz * dt)
        except Exception:
            logger.exception("Keyboard update failed this tick.")

        # Apply gamepad resets (X button -> configurable default position)
        for controller_idx in gamepad_result.resets:
            marker_id = self._gamepad_marker_id(controller_idx, slots)
            if marker_id is None:
                continue
            marker = self._get_marker(marker_id)
            if marker:
                default_pos = self.app._get_default_marker_position()
                marker.set_pos(*default_pos)
                logger.info(
                    "Controller %s reset marker %s to default position (%s, %s, %s)",
                    controller_idx,
                    marker_id,
                    default_pos[0],
                    default_pos[1],
                    default_pos[2],
                )

        # Apply gamepad movements to corresponding markers
        for controller_idx, (vx, vy, vz) in gamepad_result.movements.items():
            marker_id = self._gamepad_marker_id(controller_idx, slots)
            if marker_id is None:
                continue
            marker = self._get_marker(marker_id)
            if marker:
                x, y, z = marker.pos
                marker.set_pos(x + vx * dt, y + vy * dt, z + vz * dt)

        # Apply OSC position jumps (absolute writes – full triples
        # overwrite all axes, per-axis updates merge with the marker's
        # current position so the un-set axes stay where they were).
        if self.osc_handler is not None:
            try:
                for marker_id, axes in self.osc_handler.flush_updates().items():
                    if marker_id not in self.app._controlled_ids:
                        continue
                    marker = self._get_marker(marker_id)
                    if not marker:
                        continue
                    cur_x, cur_y, cur_z = marker.pos
                    x = axes.get("x", cur_x)
                    y = axes.get("y", cur_y)
                    z = axes.get("z", cur_z)
                    marker.set_pos(x, y, z)
                    logger.info(
                        "OSC jump: marker %d → (%.3f, %.3f, %.3f)",
                        marker_id,
                        x,
                        y,
                        z,
                    )
            except Exception:
                logger.exception("OSC flush/apply failed this tick.")

        return gamepad_result

    def _apply_mouse3d(
        self,
        m3d: Mouse3DUpdate,
        dt: float,
        gamepad_result: GamepadUpdate,
        slots: list[tuple[str, int]],
    ) -> None:
        """Apply one 3D Mouse frame.

        Discrete button edges fold into the shared :class:`GamepadUpdate` flags
        so the app's existing dispatch handles them with no second dispatch site.
        Next/prev cycling is a single-controller affordance, so it only folds
        when this is the sole controller. Movement, reset and speed target the
        marker the 3D mouse's unified slot drives (the selected marker when it's
        the only controller, its fixed slot otherwise); the velocity is a unit
        rate scaled here by the marker's move-speed. ``slots`` is the frame's
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
        if m3d.settings:
            gamepad_result.settings_open_pressed = True

        marker_id = self._controller_marker_id(self._mouse3d_unified_idx(slots), slots)
        if marker_id is None:
            return
        marker = self._get_marker(marker_id)
        if marker is None:
            return
        if m3d.reset:
            marker.set_pos(*self.app._get_default_marker_position())
        if m3d.speed_steps:
            direction = 1 if m3d.speed_steps > 0 else -1
            for _ in range(abs(m3d.speed_steps)):
                self.app.adjust_move_speed(direction, marker_id=marker_id)
        vx, vy, vz = m3d.velocity
        if vx or vy or vz:
            speed = self.app.get_marker_move_speed(marker_id)
            x, y, z = marker.pos
            marker.set_pos(x + vx * speed * dt, y + vy * speed * dt, z + vz * speed * dt)
        if m3d.fader_signal:
            # ``fader``-mapped axes integrate into the marker's fader, mirroring
            # the gamepad marker-fader stick: delta = signal × dt / max_speed_s.
            # A marker with no provisioned fader is a clean no-op in the bus.
            bus = getattr(self.app._runtime_services, "_virtual_faders", None)
            if bus is not None:
                max_speed_s = self.app._config.controller.marker_fader_max_speed_s
                bus.set_marker_fader_from_velocity_delta(marker_id, m3d.fader_signal * dt / max_speed_s)

    def get_controller_info(self) -> list[dict[str, Any]]:
        """Unified controller list (3D mice first, then gamepads) for the HUD
        marker-card badge, OSC ``:cN``, and web status.

        ``controller_index`` is the shared 0-based unified index (rendered
        1-based). ``marker_id`` is the marker the controller currently drives,
        mirroring the routing so the HUD label matches what movement targets.
        """
        gamepad_info = {int(item["controller_index"]): item for item in self.gamepad_handler.get_controller_info()}
        out: list[dict[str, Any]] = []
        slots = self._controller_slots()
        for unified_idx, (kind, local_idx) in enumerate(slots):
            marker_id = self._controller_marker_id(unified_idx, slots)
            if kind == "mouse3d":
                out.append(
                    {
                        "controller_index": unified_idx,
                        "name": MOUSE3D_NAME,
                        "connected": self.mouse3d_handler.connected,
                        "marker_id": marker_id,
                        "effective_speed": (
                            self.app.get_marker_move_speed(marker_id) if marker_id is not None else 0.0
                        ),
                        "backend": "mouse3d",
                    }
                )
            else:
                item = dict(gamepad_info.get(local_idx, {}))
                item["controller_index"] = unified_idx
                item["marker_id"] = marker_id
                item.setdefault("name", "")
                item.setdefault("connected", False)
                out.append(item)
        return out

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
        if self.osc_handler is not None:
            self.osc_handler.stop()
            self.osc_handler = None
        if enabled:
            self.osc_handler = OscMarkerAdapter(
                self.app._runtime_services._osc_service,
                port=port,
                allowed_sender_ips=list(allowed_sender_ips or []),
                multicast_group=multicast_group,
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

    def restart_mouse3d(self, config: Mouse3DConfig) -> None:
        """Live-apply a 3D Mouse config change.

        Swaps the mapping config, then starts or stops the read thread to match
        ``enabled`` (the device is only read while the feature is on). Movement
        application is additionally gated on the live ``enabled`` read in
        :meth:`update`.
        """
        self.mouse3d_handler.reload_config(config)
        if config.enabled:
            self.mouse3d_handler.start()
        else:
            self.mouse3d_handler.stop()

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

    def stop(self) -> None:
        """Stop input subsystems and release resources."""
        self.gamepad_handler.stop()
        self.mouse3d_handler.stop()
        self._stop_operator_message_handler()
        if self.osc_handler is not None:
            self.osc_handler.stop()
