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
from openfollow.osc.input import OscMarkerAdapter
from openfollow.osc.operator_message import OperatorMessageOscAdapter
from openfollow.runtime.services_detection_pin import (
    assist_pinned_marker_id,
    get_or_create_manual_marker,
)

if TYPE_CHECKING:
    from openfollow.app import OpenFollowApp
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

    def _gamepad_marker_id(self, controller_idx: int) -> int | None:
        """Resolve the marker a gamepad's movement/reset should target.

        Single-gamepad mode (exactly one controller connected AND a marker
        is selected): route to ``self.app._selected_id`` so DPAD next/prev
        actually switches what the operator is moving – same contract as the
        keyboard. Multi-gamepad mode: fixed slot mapping
        ``self.app._controlled_ids[controller_idx]`` so per-operator
        assignments stay stable across the shared selection.

        Returns ``None`` when ``controller_idx`` is not a connected controller,
        so a ``:cN`` reference to an absent controller drives no marker.
        """
        # Snapshot both rebindable containers once: gamepad_handler.joysticks is
        # rebuilt by _detect_controllers and app._controlled_ids is rebound on
        # marker hot-reload, both on the main loop, while the :cN OSC provider
        # reads them on the scheduler thread; each check/index must hit one object.
        joysticks = self.gamepad_handler.joysticks
        # :cN names a specific controller; if controller N isn't connected it
        # drives no marker, so skip rather than emit a stale slot.
        if controller_idx not in joysticks:
            return None
        if len(joysticks) == 1 and self.app._selected_id is not None:
            return self.app._selected_id
        controlled = self.app._controlled_ids
        if controller_idx < len(controlled):
            return controlled[controller_idx]
        return None

    def controller_marker_id(self, controller_idx: int) -> int | None:
        """Public seam over :meth:`_gamepad_marker_id` for the OSC ``:cN``
        controller reference. Returns the marker the 0-based controller
        currently drives (selected marker in single-gamepad mode, the fixed
        slot otherwise), or ``None`` when it drives none.
        """
        return self._gamepad_marker_id(controller_idx)

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

        # Mouse marker control glides toward the cursor target every frame so
        # smoothing is independent of pointer-event rate. The handler no-ops
        # when not actively grabbing a marker.
        if self.app._config.controller.mouse_enabled:
            try:
                self.mouse_handler.update()
            except Exception:
                logger.exception("Mouse update failed this tick.")

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
            marker_id = self._gamepad_marker_id(controller_idx)
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
            marker_id = self._gamepad_marker_id(controller_idx)
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

    def get_controller_info(self) -> list[dict[str, Any]]:
        """
        Get controller connection and mapping info for HUD display.

        Returns list of dicts with:
        - controller_index: int
        - name: str
        - connected: bool
        - marker_id: int or None
        """
        info = self.gamepad_handler.get_controller_info()

        # Fill in marker_id mapping – mirrors _gamepad_marker_id so the HUD
        # label matches the marker that movement/reset actually targets.
        for item in info:
            item["marker_id"] = self._gamepad_marker_id(item["controller_index"])

        return info

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
        self._stop_operator_message_handler()
        if self.osc_handler is not None:
            self.osc_handler.stop()
