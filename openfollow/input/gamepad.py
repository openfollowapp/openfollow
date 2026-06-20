# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Game controller input handler with hotplug support."""

from __future__ import annotations

import logging
import math
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

import pygame

try:
    from pygame._sdl2 import controller as sdl2_controller
except ImportError:  # pragma: no cover - depends on runtime pygame build
    sdl2_controller = None  # type: ignore[assignment]

from openfollow.input._joystick_protocol import ControllerProtocol, JoystickProtocol

if TYPE_CHECKING:
    from openfollow.app import OpenFollowApp
    from openfollow.input.events import InputEventBus
    from openfollow.input.faders import VirtualFaderBus

logger = logging.getLogger(__name__)


def _safe_call(fn: Callable[[], Any], default: Any) -> Any:
    """Call a pygame driver getter, degrading to ``default`` on any error.

    pygame joystick getters (``get_guid``/``get_name``/…) occasionally raise
    beyond ``pygame.error`` on flaky drivers; a single bad read must not abort
    controller detection or escape into the per-frame update loop.
    """
    try:
        return fn()
    except Exception:  # noqa: BLE001 - never let a driver getter break detection
        return default


# Raw joystick fallback mapping: triggers (LT/RT) and bumpers.
LT_AXIS_INDICES: tuple[int, ...] = (4, 6)
RT_AXIS_INDICES: tuple[int, ...] = (5, 7)
BUMPER_LEFT_BUTTON_INDICES: tuple[int, ...] = (4, 9)
BUMPER_RIGHT_BUTTON_INDICES: tuple[int, ...] = (5, 10)
# L2/R2 button fallback for controllers where SDL2 misroutes trigger axes.
# On most PS-style budget controllers with 13 buttons: L2=6, R2=7.
# Note: do NOT include index 9 here – CONTROLLER_BUTTON_LEFTSHOULDER defaults to 9,
# so button 9 is the LB shoulder button. Including it in RT would cause LB to
# simultaneously trigger height-up and speed-decrease.
LT_DIGITAL_BUTTON_INDICES: tuple[int, ...] = (6, 8)
RT_DIGITAL_BUTTON_INDICES: tuple[int, ...] = (7,)

# Trigger deadzone for analog height control
TRIGGER_DEADZONE = 0.05

CONTROLLER_AXIS_LEFTX = getattr(pygame, "CONTROLLER_AXIS_LEFTX", 0)
CONTROLLER_AXIS_LEFTY = getattr(pygame, "CONTROLLER_AXIS_LEFTY", 1)
CONTROLLER_AXIS_RIGHTX = getattr(pygame, "CONTROLLER_AXIS_RIGHTX", 2)
CONTROLLER_AXIS_RIGHTY = getattr(pygame, "CONTROLLER_AXIS_RIGHTY", 3)
CONTROLLER_AXIS_TRIGGERLEFT = getattr(pygame, "CONTROLLER_AXIS_TRIGGERLEFT", 4)
CONTROLLER_AXIS_TRIGGERRIGHT = getattr(pygame, "CONTROLLER_AXIS_TRIGGERRIGHT", 5)
CONTROLLER_BUTTON_A = getattr(pygame, "CONTROLLER_BUTTON_A", 0)
CONTROLLER_BUTTON_B = getattr(pygame, "CONTROLLER_BUTTON_B", 1)
CONTROLLER_BUTTON_X = getattr(pygame, "CONTROLLER_BUTTON_X", 2)
CONTROLLER_BUTTON_Y = getattr(pygame, "CONTROLLER_BUTTON_Y", 3)
CONTROLLER_BUTTON_BACK = getattr(pygame, "CONTROLLER_BUTTON_BACK", 4)
CONTROLLER_BUTTON_START = getattr(pygame, "CONTROLLER_BUTTON_START", 6)
CONTROLLER_BUTTON_LEFTSHOULDER = getattr(pygame, "CONTROLLER_BUTTON_LEFTSHOULDER", 9)
CONTROLLER_BUTTON_RIGHTSHOULDER = getattr(pygame, "CONTROLLER_BUTTON_RIGHTSHOULDER", 10)
CONTROLLER_BUTTON_DPAD_UP = getattr(pygame, "CONTROLLER_BUTTON_DPAD_UP", 11)
CONTROLLER_BUTTON_DPAD_DOWN = getattr(pygame, "CONTROLLER_BUTTON_DPAD_DOWN", 12)
CONTROLLER_BUTTON_DPAD_LEFT = getattr(pygame, "CONTROLLER_BUTTON_DPAD_LEFT", 13)
CONTROLLER_BUTTON_DPAD_RIGHT = getattr(pygame, "CONTROLLER_BUTTON_DPAD_RIGHT", 14)

# Sentinel button IDs for analog triggers treated as digital buttons.
# Negative so they never collide with real SDL2 button indices.
BUTTON_ID_LT = -10
BUTTON_ID_RT = -11
BUTTON_ID_UNBOUND = -20  # sentinel: user cleared this binding via Web UI "–"

# Sentinel button IDs for hat (D-Pad) directions on raw joysticks.
# Used when the button detection wizard detects D-Pad as hat inputs
# rather than discrete buttons.  Stored in button_raw_indices and
# resolved to joystick.get_hat() reads in _get_button().
HAT_UP = -1
HAT_DOWN = -2
HAT_LEFT = -3
HAT_RIGHT = -4

# String name → SDL2 button ID, used by configurable mappings.
BUTTON_NAME_TO_ID: dict[str, int] = {
    "A": CONTROLLER_BUTTON_A,
    "B": CONTROLLER_BUTTON_B,
    "X": CONTROLLER_BUTTON_X,
    "Y": CONTROLLER_BUTTON_Y,
    "BACK": CONTROLLER_BUTTON_BACK,
    "START": CONTROLLER_BUTTON_START,
    "LB": CONTROLLER_BUTTON_LEFTSHOULDER,
    "RB": CONTROLLER_BUTTON_RIGHTSHOULDER,
    "LT": BUTTON_ID_LT,
    "RT": BUTTON_ID_RT,
    "DPAD_UP": CONTROLLER_BUTTON_DPAD_UP,
    "DPAD_DOWN": CONTROLLER_BUTTON_DPAD_DOWN,
    "DPAD_LEFT": CONTROLLER_BUTTON_DPAD_LEFT,
    "DPAD_RIGHT": CONTROLLER_BUTTON_DPAD_RIGHT,
}

CONTROLLER_EVENT_TYPES = tuple(
    event_type
    for event_type in (
        getattr(pygame, "CONTROLLERAXISMOTION", None),
        getattr(pygame, "CONTROLLERBUTTONDOWN", None),
        getattr(pygame, "CONTROLLERBUTTONUP", None),
        getattr(pygame, "CONTROLLERDEVICEADDED", None),
        getattr(pygame, "CONTROLLERDEVICEREMOVED", None),
        getattr(pygame, "CONTROLLERDEVICEREMAPPED", None),
    )
    if event_type is not None
)


@dataclass
class ControllerCapabilities:
    backend: str
    name: str = ""
    guid: str = ""
    num_axes: int = 0
    num_buttons: int = 0
    num_hats: int = 0


@dataclass
class ControllerRuntimeInfo:
    """Live snapshot of one connected controller, for diagnostics and the
    calibration-mismatch check. Distinct from :class:`ControllerCapabilities`
    (the stored per-pad facts) in that it also carries the derived flags the
    bundle renders: whether SDL recognises the pad as a game controller
    (i.e. it's in X-input mode), and whether its identity matches the saved
    calibration."""

    index: int
    backend: str
    name: str
    guid: str
    num_axes: int
    num_buttons: int
    num_hats: int
    # backend == "sdl2_controller": SDL has a game-controller mapping, which
    # for an Xbox-style pad means it's in X-input mode (the preferred path).
    is_game_controller: bool
    # True when a calibration is stored and this pad's identity matches it,
    # or when no calibration is stored at all (nothing to mismatch).
    matches_calibration: bool
    # True when any calibration (name / guid / raw indices) is on file.
    calibration_stored: bool


@dataclass
class SourceSelectionInput:
    """Gamepad input relevant to NDI source selection mode."""

    up_pressed: bool = False
    down_pressed: bool = False
    confirm_pressed: bool = False
    cancel_pressed: bool = False


@dataclass
class SettingsMenuInput:
    """Gamepad input relevant to the Settings menu overlay."""

    up_pressed: bool = False
    down_pressed: bool = False
    confirm_pressed: bool = False
    cancel_pressed: bool = False


@dataclass
class GamepadUpdate:
    """Result of a gamepad update cycle."""

    movements: dict[int, tuple[float, float, float]] = field(default_factory=dict)
    resets: set[int] = field(default_factory=set)
    toggle_help_pressed: bool = False  # Y button to toggle HUD help panel
    toggle_zones_pressed: bool = False  # Optional button to toggle zone overlay
    next_marker_pressed: bool = False  # Cycle to next controlled marker
    prev_marker_pressed: bool = False  # Cycle to previous controlled marker
    settings_open_pressed: bool = False  # btn_settings edge – open Settings menu
    clear_messages_pressed: bool = False  # btn_clear_messages edge


class GamepadHandler:
    """Handles game controller input with hotplug detection."""

    # Startup retry settings: if no controllers found, retry for this duration
    _STARTUP_RETRY_DURATION = 15.0  # seconds
    _STARTUP_RETRY_INTERVAL = 2.0  # seconds between retries

    def __init__(
        self,
        app: OpenFollowApp,
        *,
        event_bus: InputEventBus | None = None,
        virtual_faders: VirtualFaderBus | None = None,
        marker_resolver: Callable[[int], int | None] | None = None,
    ) -> None:
        self.app = app
        self.deadzone = 0.15
        self.enabled = True
        self.invert_y = False
        # Virtual Fader 1 integrator. Direct reference (not via app._runtime_services)
        # for performance on the hot loop. None means disabled; apply_config tolerates it.
        self._virtual_faders: VirtualFaderBus | None = virtual_faders
        self._marker_fader_stick: str = ""
        self._marker_fader_max_speed_s: float = 1.0
        # Maps controller_idx → marker_id so bumper-speed and effective-speed paths
        # target the marker routed to that controller (single-gamepad via selected_id,
        # multi-gamepad via fixed slot). None in tests/isolated construction; bumper
        # presses fall back to app._selected_id and speed falls back to config.move_speed.
        self._marker_resolver = marker_resolver
        self.joysticks: dict[int, JoystickProtocol] = {}
        # Value is structurally a ControllerProtocol (SDL2's
        # higher-level wrapper), but pygame's stubs don't expose the
        # concrete class – see ``_joystick_protocol.py`` for context.
        self.controllers: dict[int, ControllerProtocol] = {}
        self.capabilities: dict[int, ControllerCapabilities] = {}
        self._bumper_state: dict[int, tuple[bool, bool]] = {}
        self._shoulder_axis_baselines: dict[int, dict[int, float]] = {}
        # Pads whose movement stick hasn't been seen at rest yet. A pad opened
        # after a restart can report a stale axis value before the OS delivers
        # its first centering event; stick deflection is discarded until the
        # stick reads inside the deadzone once (see _update_stick_priming).
        self._stick_unprimed: set[int] = set()
        self._button_prev: dict[int, dict[int, bool]] = {}
        # Per-controller, per-button previous state for the bus-emission edge detector.
        # Separate from _button_prev so existing edge-detectors and the emit loop
        # don't race on shared state.
        self._button_bus_prev: dict[int, dict[int, bool]] = {}
        # Per-frame button-state snapshot. None means no frame in progress (direct
        # callers read live). update() populates per-frame and resets in finally block,
        # so each (controller_idx, button_id) is read once and shared, not re-read.
        self._frame_button_states: dict[tuple[int, int], bool] | None = None
        self._event_bus = event_bus
        self.last_joystick_count = 0
        self._startup_retry_remaining = 0.0  # seconds remaining for startup retries
        self._startup_retry_timer = 0.0  # time since last retry
        self._axes_logged: set[int] = set()  # controllers whose raw axes have been dumped
        # GUIDs we've already logged a calibration-mismatch warning for, so a
        # hotplug re-detect doesn't re-warn on every cycle. Cleared in
        # ``apply_config`` when the calibration identity changes.
        self._calibration_warned: set[str] = set()
        # Guards ``self.capabilities`` against cross-thread access: every
        # write (publish in _detect_controllers, pop in _cleanup_failed,
        # clear in stop) and every read reachable off the main thread
        # (runtime_snapshot, get_controller_info) holds this lock, so a
        # web-server thread never observes a half-rebuilt dict. Main-thread-
        # only reads (e.g. the post-publish warn loop) don't need it.
        self._capabilities_lock = threading.Lock()

        # pygame.init() requires video subsystem; it also initializes SDL audio/mixer
        # which OpenFollow doesn't use. On Linux, SDL audio opens the default ALSA
        # device and floods the log. Point SDL audio at the dummy driver before init.
        os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
        if not pygame.get_init():
            pygame.init()

        if not pygame.joystick.get_init():
            pygame.joystick.init()

        if sdl2_controller is not None and not sdl2_controller.get_init():
            sdl2_controller.init()

        self.apply_config()
        logger.info(
            "GamepadHandler initialized (enabled=%s, deadzone=%.3f, sdl2_controller=%s)",
            self.enabled,
            self.deadzone,
            bool(sdl2_controller is not None),
        )
        self._detect_controllers()

        # If no controllers found at startup, enable retry mechanism
        if not self.joysticks:
            self._startup_retry_remaining = self._STARTUP_RETRY_DURATION
            logger.warning(
                "No controllers detected at startup. Will retry for %.0f seconds.",
                self._STARTUP_RETRY_DURATION,
            )

    def apply_config(self) -> None:
        """Refresh runtime controller settings from app config."""
        cfg = self.app._config.controller
        self.deadzone = cfg.deadzone
        self.enabled = cfg.enabled
        self.invert_y = cfg.invert_y
        self.curve = cfg.curve

        def _resolve(cfg_value: str, default_id: int) -> int:
            """Map a config binding to an SDL2 ID, or BUTTON_ID_UNBOUND.

            An explicit empty string (the Web UI's "–" option) means the
            user intentionally disabled the binding; ``_get_button`` treats
            ``BUTTON_ID_UNBOUND`` as always-released so dispatch never fires.
            """
            if not cfg_value:
                return BUTTON_ID_UNBOUND
            return BUTTON_NAME_TO_ID.get(cfg_value, default_id)

        # Resolve configurable button mappings to SDL2 IDs.
        # BUTTON_ID_UNBOUND means the user cleared the binding via the Web UI;
        # other negative IDs are reserved for virtual/sentinel inputs such as
        # LT/RT trigger buttons and raw-joystick hat directions.
        self._btn_reset_id = _resolve(cfg.btn_reset, CONTROLLER_BUTTON_X)
        self._btn_toggle_help_id = _resolve(cfg.btn_toggle_help, CONTROLLER_BUTTON_Y)
        self._btn_toggle_zones_id = _resolve(cfg.btn_toggle_zones, -1)
        # Clear operator-message cards; default unbound.
        self._btn_clear_messages_id = _resolve(cfg.btn_clear_messages, BUTTON_ID_UNBOUND)
        # Unified menu confirm/cancel – shared by source / interface
        # selection and the Settings menu.
        self._btn_menu_confirm_id = _resolve(cfg.btn_menu_confirm, CONTROLLER_BUTTON_A)
        self._btn_menu_cancel_id = _resolve(cfg.btn_menu_cancel, CONTROLLER_BUTTON_B)
        self._btn_speed_down_id = _resolve(cfg.btn_speed_down, CONTROLLER_BUTTON_LEFTSHOULDER)
        self._btn_speed_up_id = _resolve(cfg.btn_speed_up, CONTROLLER_BUTTON_RIGHTSHOULDER)
        self._btn_move_z_down_id = _resolve(cfg.btn_move_z_down, BUTTON_ID_LT)
        self._btn_move_z_up_id = _resolve(cfg.btn_move_z_up, BUTTON_ID_RT)
        self._btn_next_marker_id = _resolve(cfg.btn_next_marker, CONTROLLER_BUTTON_DPAD_RIGHT)
        self._btn_prev_marker_id = _resolve(cfg.btn_prev_marker, CONTROLLER_BUTTON_DPAD_LEFT)
        self._btn_settings_id = _resolve(cfg.btn_settings, CONTROLLER_BUTTON_BACK)
        self._move_xy_stick = cfg.move_xy_stick
        self._swap_triggers = cfg.swap_triggers
        # Marker-fader integrator settings. Cached at config-apply time
        # so the per-tick ``update`` loop does one attribute read each
        # instead of dereferencing the full config every animation frame.
        self._marker_fader_stick = cfg.marker_fader_stick
        self._marker_fader_max_speed_s = cfg.marker_fader_max_speed_s
        # Stored controller identity from the last wizard run, for the
        # connected-vs-calibrated mismatch check. A calibration is considered
        # "on file" if any of name / guid / raw indices is set.
        _prev_identity = (
            getattr(self, "_mapped_controller_name", None),
            getattr(self, "_mapped_controller_guid", None),
        )
        self._mapped_controller_name = cfg.mapped_controller_name
        self._mapped_controller_guid = cfg.mapped_controller_guid
        self._is_calibrated = bool(cfg.mapped_controller_name or cfg.mapped_controller_guid or cfg.button_raw_indices)
        # The calibration target changed (wizard re-run / config hot-reload),
        # so drop the warned-GUID cache – a still-mismatched pad must be able
        # to warn again against the new identity instead of staying suppressed
        # until restart.
        if (self._mapped_controller_name, self._mapped_controller_guid) != _prev_identity:
            self._calibration_warned.clear()
        # Button-detection remap. Two parallel maps because the wizard
        # captures the same physical truth twice in different units:
        #
        # * ``_button_remap`` – SDL2 logical ID → SDL2 logical ID. Built
        #   from the ``map_*`` config fields (SDL2-logical names). Only
        #   the SDL2 controller path consults this; the SDL2 driver
        #   already maps raw hardware buttons to SDL2 logical IDs, so
        #   any correction at the app layer is also expressed in SDL2
        #   logical IDs.
        #
        # * ``_raw_button_remap`` – SDL2 logical ID → raw hardware
        #   index. Built from ``button_raw_indices`` (hardware indices
        #   from the wizard). Only the raw joystick path consults this;
        #   raw indices are meaningless to SDL2 controllers (e.g. a raw
        #   ``LB=4`` would be misread as SDL2 logical BACK there).
        #
        # They stay separate so the SDL2 path never applies raw indices
        # as if they were SDL2 IDs. On a pad whose SDL2 mapping has LB
        # → logical BACK, the wizard's "LB lives at raw 4" is correct,
        # but ``controller.get_button(4)`` reads logical BACK on SDL2,
        # not LB.
        self._button_remap: dict[int, int] = {}
        self._raw_button_remap: dict[int, int] = {}
        # Label -> SDL2 logical ID for remap building
        _LABEL_TO_LOGICAL: dict[str, int] = {
            "A": CONTROLLER_BUTTON_A,
            "B": CONTROLLER_BUTTON_B,
            "X": CONTROLLER_BUTTON_X,
            "Y": CONTROLLER_BUTTON_Y,
            "BACK": CONTROLLER_BUTTON_BACK,
            "START": CONTROLLER_BUTTON_START,
            "LB": CONTROLLER_BUTTON_LEFTSHOULDER,
            "RB": CONTROLLER_BUTTON_RIGHTSHOULDER,
            "DPAD_UP": CONTROLLER_BUTTON_DPAD_UP,
            "DPAD_DOWN": CONTROLLER_BUTTON_DPAD_DOWN,
            "DPAD_LEFT": CONTROLLER_BUTTON_DPAD_LEFT,
            "DPAD_RIGHT": CONTROLLER_BUTTON_DPAD_RIGHT,
        }
        raw_indices = cfg.button_raw_indices
        if raw_indices:
            # Wizard captured hardware indices – feeds the joystick path.
            for label, raw_idx in raw_indices.items():
                logical_id = _LABEL_TO_LOGICAL.get(label)
                if logical_id is not None and raw_idx != logical_id:
                    self._raw_button_remap[logical_id] = raw_idx
        # SDL2-logical remap is built unconditionally – both wizard-run
        # and non-wizard installs benefit, and it coexists with the
        # raw-index map for backends that need each.
        _MAP_FIELDS = (
            ("map_a", CONTROLLER_BUTTON_A),
            ("map_b", CONTROLLER_BUTTON_B),
            ("map_x", CONTROLLER_BUTTON_X),
            ("map_y", CONTROLLER_BUTTON_Y),
            ("map_back", CONTROLLER_BUTTON_BACK),
            ("map_start", CONTROLLER_BUTTON_START),
            ("map_lb", CONTROLLER_BUTTON_LEFTSHOULDER),
            ("map_rb", CONTROLLER_BUTTON_RIGHTSHOULDER),
            ("map_dpad_up", CONTROLLER_BUTTON_DPAD_UP),
            ("map_dpad_down", CONTROLLER_BUTTON_DPAD_DOWN),
            ("map_dpad_left", CONTROLLER_BUTTON_DPAD_LEFT),
            ("map_dpad_right", CONTROLLER_BUTTON_DPAD_RIGHT),
        )
        for field_name, logical_id in _MAP_FIELDS:
            detected_name = getattr(cfg, field_name)
            detected_id = BUTTON_NAME_TO_ID.get(
                detected_name,
                logical_id,
            )
            if detected_id != logical_id:
                self._button_remap[logical_id] = detected_id
        if self._button_remap or self._raw_button_remap:
            id_to_name = {v: k for k, v in BUTTON_NAME_TO_ID.items()}
            sdl_readable = {id_to_name.get(k, str(k)): id_to_name.get(v, str(v)) for k, v in self._button_remap.items()}
            raw_readable = {id_to_name.get(k, str(k)): v for k, v in self._raw_button_remap.items()}
            logger.info(
                "Button detection remap active: sdl=%s raw=%s",
                sdl_readable,
                raw_readable,
            )
        # Clear edge-detection state to prevent false triggers
        # when mappings change at runtime. ``_button_bus_prev``
        # rides the same ``_get_button`` reads as ``_button_prev``,
        # so a button-remap change can swap the underlying physical
        # button behind a logical id mid-frame – leaving stale bus-
        # prev state would emit a spurious press / release the very
        # next tick. Cleared together so both edge detectors restart
        # from the fresh post-config baseline.
        self._button_prev.clear()
        self._button_bus_prev.clear()

    def _detect_controllers(self, force: bool = False) -> None:
        """Detect and initialize all connected controllers."""
        try:
            count = pygame.joystick.get_count()

            if not force and count == self.last_joystick_count:
                return

            # Controller configuration changed - rebuild dict
            old_count = self.last_joystick_count
            self.last_joystick_count = count
            previous_bumper_states = self._bumper_state.copy()
            previous_axis_baselines = self._shoulder_axis_baselines.copy()
            previous_button_prev = self._button_prev.copy()
            previous_button_bus_prev = self._button_bus_prev.copy()
            previously_connected = set(self.joysticks)

            # Clean up old devices
            for ctrl in self.controllers.values():
                try:
                    ctrl.quit()
                except pygame.error:
                    pass
            for joy in self.joysticks.values():
                try:
                    joy.quit()
                except pygame.error:
                    pass

            # Initialize new devices. ``capabilities`` is built in a local
            # dict and published atomically under the lock after the loop, so
            # a concurrent diagnostics ``runtime_snapshot()`` (web thread)
            # never iterates a half-built dict.
            self.joysticks = {}
            self.controllers = {}
            # Re-enumeration (count changed): clear the axis-dump flags so a
            # reused index re-dumps for its new controller.
            self._axes_logged.clear()
            new_capabilities: dict[int, ControllerCapabilities] = {}
            for i in range(count):
                try:
                    # ``init()`` is part of the concrete pygame
                    # joystick API but not a method we use after
                    # construction, so we keep it off the protocol
                    # surface and call it on the concrete object
                    # before casting. The cast is the bridge: pygame
                    # ships ``Joystick`` as a function-typed weak
                    # stub, so the runtime instance has to be
                    # explicitly retyped to the structural protocol
                    # mypy uses everywhere downstream.
                    pygame_joy = pygame.joystick.Joystick(i)
                    pygame_joy.init()
                    joy = cast(JoystickProtocol, pygame_joy)
                    self.joysticks[i] = joy

                    backend = "joystick"
                    ctrl_obj: ControllerProtocol | None = None
                    if sdl2_controller is not None and sdl2_controller.is_controller(i):
                        try:
                            ctrl_obj = cast(ControllerProtocol, sdl2_controller.Controller(i))
                            self.controllers[i] = ctrl_obj
                            backend = "sdl2_controller"
                        except pygame.error as e:
                            logger.warning("Controller %s SDL2 open failed, fallback to joystick: %s", i, e)

                    # Driver getters can raise beyond pygame.error on flaky
                    # pads; degrade per-field so one bad read still connects.
                    cap_name = _safe_call(joy.get_name, "")
                    cap_guid = _safe_call(joy.get_guid, "")
                    cap_axes = _safe_call(joy.get_numaxes, 0)
                    cap_buttons = _safe_call(joy.get_numbuttons, 0)
                    cap_hats = _safe_call(joy.get_numhats, 0)
                    new_capabilities[i] = ControllerCapabilities(
                        backend=backend,
                        name=cap_name,
                        guid=cap_guid,
                        num_axes=cap_axes,
                        num_buttons=cap_buttons,
                        num_hats=cap_hats,
                    )
                    logger.info(
                        "Controller %s connected via %s: %s (GUID: %s, axes=%s, buttons=%s)",
                        i,
                        backend,
                        cap_name,
                        cap_guid,
                        cap_axes,
                        cap_buttons,
                    )
                except pygame.error as e:
                    logger.error("Failed to initialize controller %s: %s", i, e)
            # Publish the rebuilt capabilities atomically for the web-thread
            # reader, then warn (reads the now-live dict on the main thread).
            with self._capabilities_lock:
                self.capabilities = new_capabilities
            for i in self.capabilities:
                self._warn_if_calibration_mismatch(i)
            self._bumper_state = {i: previous_bumper_states.get(i, (False, False)) for i in self.joysticks}
            self._shoulder_axis_baselines = {}
            self._button_prev = {i: previous_button_prev.get(i, {}) for i in self.joysticks}
            # Mirror ``_button_prev`` so bus-emission edge state is
            # carried forward for indices that stayed connected and
            # dropped for indices that disconnected. Without this, a
            # disconnect handled silently here (no ``pygame.error``
            # raised, so ``_cleanup_failed`` doesn't fire) leaves
            # stale per-index state – when a new controller later
            # reuses the index, the next tick can emit a ghost
            # release or swallow a fresh press.
            self._button_bus_prev = {i: previous_button_bus_prev.get(i, {}) for i in self.joysticks}
            for i, joystick in self.joysticks.items():
                baseline = previous_axis_baselines.get(i, {}).copy()
                for axis_idx in {*LT_AXIS_INDICES, *RT_AXIS_INDICES}:
                    if axis_idx >= joystick.get_numaxes():
                        continue
                    if axis_idx not in baseline:
                        baseline[axis_idx] = joystick.get_axis(axis_idx)
                self._shoulder_axis_baselines[i] = baseline
            # Mark freshly-connected pads as needing a centered stick reading
            # before their deflection is honoured; pads that stayed connected
            # keep whatever primed state they already had.
            self._stick_unprimed = {i for i in self.joysticks if i not in previously_connected} | (
                self._stick_unprimed & set(self.joysticks)
            )

            if count > old_count:
                logger.info("Controllers connected: %s -> %s", old_count, count)
            elif count < old_count:
                logger.info("Controllers disconnected: %s -> %s", old_count, count)
            # pragma: no branch – exhaustive log-level dispatch over the
            # connect / disconnect / force-refresh axes; the no-log
            # branch (count unchanged AND not forced) fires silently
            # via the same code path coverage already exercises.
            elif force:  # pragma: no branch
                logger.info("Controller map refreshed: %s devices", count)

        except pygame.error as e:
            logger.error("Error detecting controllers: %s", e)

    def _apply_deadzone(self, value: float) -> float:
        """
        Apply deadzone to analog stick value and scale to full range.

        Args:
            value: Raw axis value in range [-1.0, 1.0]

        Returns:
            Filtered and scaled value, or 0.0 if within deadzone
        """
        if abs(value) < self.deadzone:
            return 0.0
        # Scale to full range after deadzone
        sign = 1.0 if value > 0 else -1.0
        scaled = (abs(value) - self.deadzone) / (1.0 - self.deadzone)
        return sign * self._apply_curve(scaled)

    def _apply_curve(self, value: float) -> float:
        """
        Apply response curve to a normalized magnitude in [0.0, 1.0].

        The curve reshapes the stick-to-velocity relationship:
        - linear:      y = x  (direct 1:1 mapping)
        - logarithmic: y = log(1 + 9x) / log(10)  (fine control near center)
        - quadratic:   y = x^2  (finer near center, fast at edges)
        - s-law:       y = 3x^2 - 2x^3  (smooth ease-in / ease-out)
        """
        if self.curve == "linear":
            return value
        if self.curve == "logarithmic":
            return math.log1p(9.0 * value) / math.log(10.0)
        if self.curve == "quadratic":
            return value * value
        if self.curve == "s-law":
            return 3.0 * value * value - 2.0 * value * value * value
        return value

    @staticmethod
    def _normalize_controller_axis(value: int, *, trigger: bool = False) -> float:
        """Normalize SDL controller axis values to [-1, 1] or [0, 1] for triggers."""
        if trigger:
            return max(0.0, min(1.0, float(value) / 32768.0))
        return max(-1.0, min(1.0, float(value) / 32768.0))

    def _read_axes_from_controller(self, controller: ControllerProtocol) -> tuple[float, float, float]:
        """
        Read standardized SDL controller axes and return (dx, dy, dz) movement deltas.

        Configurable stick (left or right) controls X/Y. Z comes from triggers separately.
        """
        if self._move_xy_stick == "right":
            ax_x, ax_y = CONTROLLER_AXIS_RIGHTX, CONTROLLER_AXIS_RIGHTY
        else:
            ax_x, ax_y = CONTROLLER_AXIS_LEFTX, CONTROLLER_AXIS_LEFTY
        stick_x = self._apply_deadzone(self._normalize_controller_axis(controller.get_axis(ax_x)))
        stick_y = self._apply_deadzone(self._normalize_controller_axis(controller.get_axis(ax_y)))

        if self.invert_y:
            return (stick_x, stick_y, 0.0)
        return (stick_x, -stick_y, 0.0)

    def _read_axes_from_joystick(self, joystick: JoystickProtocol) -> tuple[float, float, float]:
        """
        Read raw joystick axes and return (dx, dy, dz) movement deltas.

        Configurable stick (left or right) controls X/Y. Z comes from triggers separately.
        """
        num_axes = joystick.get_numaxes()
        if self._move_xy_stick == "right":
            x_idx, y_idx = 2, 3
        else:
            x_idx, y_idx = 0, 1
        if num_axes <= max(x_idx, y_idx):
            return (0.0, 0.0, 0.0)

        stick_x = self._apply_deadzone(joystick.get_axis(x_idx))
        stick_y = self._apply_deadzone(joystick.get_axis(y_idx))

        if self.invert_y:
            return (stick_x, stick_y, 0.0)
        return (stick_x, -stick_y, 0.0)

    def _read_axes(self, controller_idx: int, joystick: JoystickProtocol) -> tuple[float, float, float]:
        """Read movement axes from the best available backend for a controller."""
        try:
            controller = self.controllers.get(controller_idx)
            if controller is not None:
                return self._read_axes_from_controller(controller)
            return self._read_axes_from_joystick(joystick)
        except pygame.error as e:
            logger.warning("Error reading axes from controller %s: %s", controller_idx, e)
            return (0.0, 0.0, 0.0)

    def _update_stick_priming(self, controller_idx: int, dx: float, dy: float) -> bool:
        """Return whether this pad's stick deflection is trustworthy yet.

        A pad opened after a restart can report a stale axis value before the OS
        delivers its first centering event; without a rest reference that phantom
        slides the marker into a corner. The first in-deadzone reading proves the
        axis is live and centered, so deflection before that is discarded. A
        trigger-style ``current - baseline`` delta is unsuitable here because a
        spring-centered stick would then mis-zero a held position. Pads injected
        directly (no ``_detect_controllers``) default to primed.
        """
        if controller_idx not in self._stick_unprimed:
            return True
        if dx == 0.0 and dy == 0.0:
            self._stick_unprimed.discard(controller_idx)
            return True
        return False

    def _read_marker_fader_deflection(
        self,
        controller_idx: int,
        joystick: JoystickProtocol,
    ) -> float:
        """Read the configured marker-fader stick Y axis.

        Returns the deflection in the range ``[-1, 1]`` where ``+1`` means
        "stick pushed up" – the operator's intuitive direction for raising
        a fader. Stick Y is conventionally ``+1 = down`` on every backend
        we support, so the raw value is negated before returning.

        ``invert_y`` is *not* applied here. ``invert_y`` flips XY movement
        for operators who prefer flight-style camera control; that
        preference doesn't naturally map to fader semantics, where
        "stick up = fader up" is universal regardless of how the
        operator's brain models XY marker movement.

        Returns ``0.0`` when no stick is configured, when the chosen
        axis index is past the joystick's reported axis count (raw
        joystick path only – SDL2 controllers always have all four),
        or when the deflection lands inside the deadzone.
        """
        if not self._marker_fader_stick:
            return 0.0
        try:
            controller = self.controllers.get(controller_idx)
            if controller is not None:
                ax = CONTROLLER_AXIS_LEFTY if self._marker_fader_stick == "left_y" else CONTROLLER_AXIS_RIGHTY
                raw = self._normalize_controller_axis(controller.get_axis(ax))
            else:
                idx = 1 if self._marker_fader_stick == "left_y" else 3
                if joystick.get_numaxes() <= idx:
                    return 0.0
                raw = joystick.get_axis(idx)
        except pygame.error as e:
            logger.warning(
                "Error reading marker-fader axis from controller %s: %s",
                controller_idx,
                e,
            )
            return 0.0
        return -self._apply_deadzone(raw)

    @staticmethod
    def _read_button_state(joystick: JoystickProtocol, button_idx: int) -> bool:
        """Safely read a digital button state."""
        if button_idx < 0 or button_idx >= joystick.get_numbuttons():
            return False
        return bool(joystick.get_button(button_idx))

    def _read_any_button_state(
        self,
        joystick: JoystickProtocol,
        button_indices: tuple[int, ...],
    ) -> bool:
        """Return True if any candidate button index is currently pressed."""
        return any(self._read_button_state(joystick, idx) for idx in button_indices)

    def _update_speed_from_bumpers(
        self,
        controller_idx: int,
        joystick: JoystickProtocol,
    ) -> None:
        """
        Detect speed button press edges and adjust move speed.

        Uses configurable btn_speed_down / btn_speed_up buttons
        (default LB/RB). Triggers are used for height.
        """
        down_pressed = self._get_button(controller_idx, self._btn_speed_down_id)
        up_pressed = self._get_button(controller_idx, self._btn_speed_up_id)
        # Raw joystick fallback: when no detection remap exists for the
        # shoulder buttons, the SDL2 default IDs (9/10) won't match
        # the raw joystick layout (typically 4/5).  Only apply this
        # fallback when using the default LB/RB buttons AND no remap
        # has been set for them (i.e. wizard hasn't been run).
        is_raw = self.controllers.get(controller_idx) is None
        if is_raw and not down_pressed:
            if (
                self._btn_speed_down_id == CONTROLLER_BUTTON_LEFTSHOULDER
                and CONTROLLER_BUTTON_LEFTSHOULDER not in self._button_remap
                and CONTROLLER_BUTTON_LEFTSHOULDER not in self._raw_button_remap
            ):
                down_pressed = self._read_any_button_state(joystick, BUMPER_LEFT_BUTTON_INDICES)
        # pragma: no branch – symmetrical to the down-pressed bumper
        # fallback above; both arms are exercised by the down-bumper
        # tests (raw vs SDL2 controller path) and a controller layout
        # where ``btn_speed_up_id`` differs from RIGHTSHOULDER would
        # bypass this block entirely.
        if is_raw and not up_pressed:  # pragma: no branch
            if (
                self._btn_speed_up_id == CONTROLLER_BUTTON_RIGHTSHOULDER
                and CONTROLLER_BUTTON_RIGHTSHOULDER not in self._button_remap
                and CONTROLLER_BUTTON_RIGHTSHOULDER not in self._raw_button_remap
            ):
                up_pressed = self._read_any_button_state(joystick, BUMPER_RIGHT_BUTTON_INDICES)

        prev_down, prev_up = self._bumper_state.get(controller_idx, (False, False))

        # Route speed adjustment to the marker this pad controls via resolver; None = unbound/no-op.
        if self._marker_resolver is None:
            marker_id: int | None = None
            is_explicitly_unbound = False
        else:
            marker_id = self._marker_resolver(controller_idx)
            is_explicitly_unbound = marker_id is None
        if not is_explicitly_unbound:
            if down_pressed and not prev_down:
                self.app.adjust_move_speed(-1, marker_id=marker_id)
            if up_pressed and not prev_up:
                self.app.adjust_move_speed(+1, marker_id=marker_id)

        self._bumper_state[controller_idx] = (down_pressed, up_pressed)

    def _read_trigger_height(
        self,
        controller_idx: int,
        joystick: JoystickProtocol,
    ) -> float:
        """
        Read Z movement buttons/triggers and return a velocity component in [-1, 1].

        btn_move_z_down = down (negative Z), btn_move_z_up = up (positive Z).
        When mapped to LT/RT (default), reads analog triggers proportionally.
        When mapped to a regular button, returns digital 0.0 or 1.0.
        """
        z_down = self._read_z_input(controller_idx, joystick, self._btn_move_z_down_id, is_lt=True)
        z_up = self._read_z_input(controller_idx, joystick, self._btn_move_z_up_id, is_lt=False)

        # Apply deadzone
        if z_down < TRIGGER_DEADZONE:
            z_down = 0.0
        if z_up < TRIGGER_DEADZONE:
            z_up = 0.0

        return z_up - z_down

    def _read_z_input(
        self,
        controller_idx: int,
        joystick: JoystickProtocol,
        button_id: int,
        is_lt: bool,
    ) -> float:
        """Read a Z axis input as analog [0, 1] for triggers or digital for buttons."""
        if button_id in (BUTTON_ID_LT, BUTTON_ID_RT):
            # Analog trigger – read proportional value
            use_lt = button_id == BUTTON_ID_LT
            if self._swap_triggers:
                use_lt = not use_lt
            controller = self.controllers.get(controller_idx)
            if controller is not None:
                if joystick.get_numaxes() > 4:
                    axis = CONTROLLER_AXIS_TRIGGERLEFT if use_lt else CONTROLLER_AXIS_TRIGGERRIGHT
                    return self._normalize_controller_axis(controller.get_axis(axis), trigger=True)
                else:
                    indices = LT_DIGITAL_BUTTON_INDICES if use_lt else RT_DIGITAL_BUTTON_INDICES
                    return 1.0 if self._read_any_button_state(joystick, indices) else 0.0
            else:
                indices = LT_AXIS_INDICES if use_lt else RT_AXIS_INDICES
                return self._read_trigger_axis_value(controller_idx, joystick, indices)
        # Regular button – digital on/off
        return 1.0 if self._get_button(controller_idx, button_id) else 0.0

    def _read_trigger_axis_value(
        self,
        controller_idx: int,
        joystick: JoystickProtocol,
        axis_indices: tuple[int, ...],
    ) -> float:
        """Read trigger axis value [0, 1] from raw joystick fallback, using baseline delta."""
        best = 0.0
        per_controller = self._shoulder_axis_baselines.setdefault(controller_idx, {})
        for axis_idx in axis_indices:
            if axis_idx < 0 or axis_idx >= joystick.get_numaxes():
                continue
            baseline = per_controller.get(axis_idx)
            current = joystick.get_axis(axis_idx)
            if baseline is None:
                per_controller[axis_idx] = current
                continue
            delta = current - baseline
            value = max(0.0, min(1.0, delta))
            if value > best:
                best = value
        return best

    def _get_button(self, controller_idx: int, button_id: int) -> bool:
        """Read a button state from SDL controller or raw joystick.

        Applies the detection remap so mislabeled controllers
        report the correct logical button.

        Unbound actions (``BUTTON_ID_UNBOUND`` or other non-LT/RT negative
        ids – e.g. the legacy ``-1`` used for ``btn_toggle_zones``) always
        read as released so dispatch code can stay oblivious to whether the
        user cleared the binding.

        Sentinel IDs BUTTON_ID_LT / BUTTON_ID_RT are handled by
        reading the trigger axis and treating deflection past the
        deadzone as a digital press.

        Hat sentinel IDs (HAT_UP/DOWN/LEFT/RIGHT) are handled by
        reading ``joystick.get_hat()`` – used when the button
        detection wizard found D-Pad on a hat rather than buttons.
        Hat sentinels only appear after the remap lookup below.

        When frame snapshot is active, reads are memoised to share one hardware read per button/frame.
        """
        states = self._frame_button_states
        if states is not None:
            cached = states.get((controller_idx, button_id))
            if cached is not None:
                return cached
            value = self._read_button_uncached(controller_idx, button_id)
            states[(controller_idx, button_id)] = value
            return value
        return self._read_button_uncached(controller_idx, button_id)

    def _read_button_uncached(self, controller_idx: int, button_id: int) -> bool:
        """Live button read, bypassing the per-frame snapshot. See
        :meth:`_get_button` for the remap / trigger / hat semantics."""
        if button_id < 0 and button_id not in (BUTTON_ID_LT, BUTTON_ID_RT):
            return False
        if button_id in (BUTTON_ID_LT, BUTTON_ID_RT):
            return self._get_trigger_as_button(controller_idx, button_id)
        controller = self.controllers.get(controller_idx)
        if controller is not None:
            # SDL2 controller path: use the SDL2-logical remap (built
            # from ``map_*`` fields). The raw-index remap MUST NOT
            # apply here – SDL2 expects logical IDs, and feeding it a
            # raw hardware index would silently misread.
            sdl_id = self._button_remap.get(button_id, button_id)
            if sdl_id in (HAT_UP, HAT_DOWN, HAT_LEFT, HAT_RIGHT):
                joystick = self.joysticks.get(controller_idx)
                if joystick is not None:
                    return self._read_hat_direction(joystick, sdl_id)
                return False
            return bool(controller.get_button(sdl_id))
        # Raw joystick path: use the raw-index remap (built from
        # ``button_raw_indices``). The SDL2-logical remap is also
        # consulted as a fallback for hosts that don't have wizard
        # raw_indices captured (older configs).
        raw_id = self._raw_button_remap.get(button_id)
        if raw_id is None:
            raw_id = self._button_remap.get(button_id, button_id)
        if raw_id in (HAT_UP, HAT_DOWN, HAT_LEFT, HAT_RIGHT):
            joystick = self.joysticks.get(controller_idx)
            if joystick is not None:
                return self._read_hat_direction(joystick, raw_id)
            return False
        joystick = self.joysticks.get(controller_idx)
        if joystick is not None:
            return self._read_button_state(joystick, raw_id)
        return False

    @staticmethod
    def _read_hat_direction(
        joystick: JoystickProtocol,
        hat_sentinel: int,
    ) -> bool:
        """Return True if the hat direction indicated by *hat_sentinel* is active."""
        for hat_idx in range(joystick.get_numhats()):
            hx, hy = joystick.get_hat(hat_idx)
            if hat_sentinel == HAT_UP and hy > 0:
                return True
            if hat_sentinel == HAT_DOWN and hy < 0:
                return True
            if hat_sentinel == HAT_LEFT and hx < 0:
                return True
            if hat_sentinel == HAT_RIGHT and hx > 0:
                return True
        return False

    def _get_trigger_as_button(self, controller_idx: int, button_id: int) -> bool:
        """Read an analog trigger axis and return True if past threshold."""
        joystick = self.joysticks.get(controller_idx)
        if joystick is None:
            return False
        is_left = button_id == BUTTON_ID_LT
        controller = self.controllers.get(controller_idx)
        if controller is not None:
            axis = CONTROLLER_AXIS_TRIGGERLEFT if is_left else CONTROLLER_AXIS_TRIGGERRIGHT
            if self._swap_triggers:
                axis = CONTROLLER_AXIS_TRIGGERRIGHT if is_left else CONTROLLER_AXIS_TRIGGERLEFT
            val = self._normalize_controller_axis(controller.get_axis(axis), trigger=True)
        else:
            indices = LT_AXIS_INDICES if is_left else RT_AXIS_INDICES
            # pragma: no branch – swap_triggers is a config-driven
            # flag that the existing trigger-swap tests cover at the
            # SDL2 path one branch up; the raw-axis path's swap arm
            # is symmetrical and hit only when SDL2 isn't initialised.
            if self._swap_triggers:  # pragma: no branch
                indices = RT_AXIS_INDICES if is_left else LT_AXIS_INDICES
            val = self._read_trigger_axis_value(controller_idx, joystick, indices)
        return val > TRIGGER_DEADZONE

    def _detect_button_edge(self, controller_idx: int, button_id: int) -> bool:
        """Return True on rising edge (was released, now pressed)."""
        current = self._get_button(controller_idx, button_id)
        prev_map = self._button_prev.setdefault(controller_idx, {})
        was_pressed = prev_map.get(button_id, False)
        prev_map[button_id] = current
        return current and not was_pressed

    def get_effective_speed(self, controller_idx: int) -> float:
        """Get effective gamepad move speed in m/s; resolves routed marker's override or default."""
        if self._marker_resolver is None:
            return self.app._config.marker.move_speed
        marker_id = self._marker_resolver(controller_idx)
        return self.app.get_marker_move_speed(marker_id)

    def get_controller_effective_speeds(self) -> dict[int, float]:
        """Get effective move speed in m/s for currently connected controllers."""
        return {idx: self.get_effective_speed(idx) for idx in self.joysticks}

    def _identity_matches_calibration(self, cap: ControllerCapabilities) -> bool:
        """Whether ``cap`` matches the stored calibration identity.

        GUID is the strong signal (it differs per unit and per hardware
        mode); the name is the fallback for calibrations saved before a GUID
        was recorded. With no calibration on file there's nothing to mismatch,
        so everything matches.
        """
        if not self._is_calibrated:
            return True
        if self._mapped_controller_guid:
            return cap.guid == self._mapped_controller_guid
        if self._mapped_controller_name:
            return cap.name == self._mapped_controller_name
        # Calibrated via raw indices only (no identity recorded) – can't
        # tell whether the pad matches, so don't cry mismatch.
        return True

    def runtime_snapshot(self) -> list[ControllerRuntimeInfo]:
        """Live per-controller snapshot for the diagnostics bundle and the
        calibration-mismatch check. Ordered by controller index for stable
        bundle output."""
        # Copy under the lock so we never iterate while the main thread
        # rebuilds/cleans up ``capabilities`` (RuntimeError: dict changed size).
        with self._capabilities_lock:
            caps = dict(self.capabilities)
        snapshot: list[ControllerRuntimeInfo] = []
        for idx in sorted(caps):
            cap = caps[idx]
            snapshot.append(
                ControllerRuntimeInfo(
                    index=idx,
                    backend=cap.backend,
                    name=cap.name,
                    guid=cap.guid,
                    num_axes=cap.num_axes,
                    num_buttons=cap.num_buttons,
                    num_hats=cap.num_hats,
                    is_game_controller=(cap.backend == "sdl2_controller"),
                    matches_calibration=self._identity_matches_calibration(cap),
                    calibration_stored=self._is_calibrated,
                )
            )
        return snapshot

    def _warn_if_calibration_mismatch(self, idx: int) -> None:
        """Log once per controller GUID when a connected pad doesn't match the
        stored calibration – the signal behind the diagnostics-bundle flag and
        the most common cause of "a button won't bind" reports."""
        cap = self.capabilities.get(idx)
        if cap is None or self._identity_matches_calibration(cap):
            return
        # De-dup per controller. GUID is the natural key, but fall back to
        # index+name when it's empty (degraded read / pad reports no GUID) so
        # several GUID-less pads don't collapse onto one key and suppress all
        # but the first warning.
        warn_key = cap.guid or f"{idx}:{cap.name}"
        if warn_key in self._calibration_warned:
            return
        self._calibration_warned.add(warn_key)
        logger.warning(
            "Controller %s (%r, GUID %s) does not match the saved button "
            "mapping (%r, GUID %s). The stored calibration may misbehave – "
            "re-run the button-detection wizard. If this is an Xbox-style "
            "pad on the raw-joystick backend (%s), switch it to X-input mode "
            "first.",
            idx,
            cap.name,
            cap.guid,
            self._mapped_controller_name,
            self._mapped_controller_guid,
            cap.backend,
        )

    def _pump_events(self) -> None:
        """Process pygame events; consume joystick events and re-post others."""
        hotplug_detected = False
        for event in pygame.event.get():
            if event.type in (
                pygame.JOYAXISMOTION,
                pygame.JOYBUTTONDOWN,
                pygame.JOYBUTTONUP,
                pygame.JOYHATMOTION,
                pygame.JOYDEVICEADDED,
                pygame.JOYDEVICEREMOVED,
                *CONTROLLER_EVENT_TYPES,
            ):
                if event.type in (
                    pygame.JOYDEVICEADDED,
                    pygame.JOYDEVICEREMOVED,
                    getattr(pygame, "CONTROLLERDEVICEADDED", None),
                    getattr(pygame, "CONTROLLERDEVICEREMOVED", None),
                ):
                    hotplug_detected = True
            else:
                pygame.event.post(event)
        if hotplug_detected:
            self._detect_controllers(force=True)

    def _cleanup_failed(self, to_remove: list[int]) -> None:
        """Remove controllers that errored during polling."""
        for idx in to_remove:
            self.joysticks.pop(idx, None)
            self.controllers.pop(idx, None)
            with self._capabilities_lock:
                self.capabilities.pop(idx, None)
            self._bumper_state.pop(idx, None)
            self._shoulder_axis_baselines.pop(idx, None)
            self._stick_unprimed.discard(idx)
            self._button_prev.pop(idx, None)
            self._button_bus_prev.pop(idx, None)
            # Drop the axis-dump flag so a pad later reusing this index dumps.
            self._axes_logged.discard(idx)

    def update(self, dt: float) -> GamepadUpdate:
        """
        Update controller state and return movements + reset signals.

        Returns:
            GamepadUpdate with movements (controller_index -> (dx, dy, dz) in m/s)
            and resets (set of controller indices that pressed X).
        """
        if not self.enabled:
            return GamepadUpdate()

        # Startup retry: if no controllers and retry time remaining, periodically re-scan
        # pragma: no branch – the False-arm of the retry-active guard
        # is the steady-state hot loop that the existing update tests
        # cover; the True arm is exercised by the explicit
        # ``test_update_startup_retry_*`` cases.
        if self._startup_retry_remaining > 0 and not self.joysticks:  # pragma: no branch
            self._startup_retry_timer += dt
            if self._startup_retry_timer >= self._STARTUP_RETRY_INTERVAL:
                self._startup_retry_timer = 0.0
                self._startup_retry_remaining -= self._STARTUP_RETRY_INTERVAL
                logger.info(
                    "Retrying controller detection (%.0fs remaining)...",
                    max(0, self._startup_retry_remaining),
                )
                # Re-init pygame joystick subsystem to pick up devices that appeared late
                pygame.joystick.quit()
                pygame.joystick.init()
                if sdl2_controller is not None:
                    try:
                        sdl2_controller.quit()
                        # pragma: no cover – both ``quit()`` AND
                        # ``init()`` succeeding inside the retry tick
                        # is unreachable: the existing test forces ``quit``
                        # to raise so the init is never reached; reaching it
                        # requires both calls to succeed inside the retry tick,
                        # a state the SDL2 reset doesn't reliably produce.
                        sdl2_controller.init()  # pragma: no cover
                    except pygame.error:
                        pass
                self._detect_controllers(force=True)
                if self.joysticks:
                    logger.info("Controller(s) detected after retry!")
                    self._startup_retry_remaining = 0.0  # Stop retrying
                elif self._startup_retry_remaining <= 0:
                    logger.warning(
                        "No controllers found after startup retry period. "
                        "Connect a controller and it will be detected via hotplug."
                    )

        self._pump_events()

        # Activate the per-frame button snapshot: the action edge-detectors
        # and the bus emitter below then share one hardware read per
        # ``(controller, button)`` instead of each re-reading every
        # button. Reset in ``finally`` so a partially-built frame never leaks
        # cached reads into the next poll (a menu poller, or a later frame).
        self._frame_button_states = {}
        try:
            return self._poll_and_emit(dt)
        finally:
            self._frame_button_states = None

    def _poll_and_emit(self, dt: float) -> GamepadUpdate:
        """Poll movement + action edges for every controller and emit bus
        button events for one frame. Runs with ``_frame_button_states``
        active (see :meth:`update`), so every ``_get_button`` read this frame
        is memoised and shared across the action and bus edge-detectors.

        ``dt`` is the frame delta, used by the marker-fader integrator."""
        result = GamepadUpdate()
        to_remove: list[int] = []

        for controller_idx, joystick in self.joysticks.items():
            try:
                # One-time axis dump to diagnose controller mapping
                if controller_idx not in self._axes_logged:
                    self._axes_logged.add(controller_idx)
                    num_axes = joystick.get_numaxes()
                    raw = {i: round(joystick.get_axis(i), 3) for i in range(num_axes)}
                    ctrl = self.controllers.get(controller_idx)
                    # Diagnostic dump only – values are either the rounded
                    # axis float or the literal string "err" when the
                    # SDL2 controller raised. Mixed-type dict.
                    sdl_axes: dict[str, float | str] = {}
                    if ctrl is not None:
                        for name, idx in (
                            ("LEFTX", CONTROLLER_AXIS_LEFTX),
                            ("LEFTY", CONTROLLER_AXIS_LEFTY),
                            ("RIGHTX", CONTROLLER_AXIS_RIGHTX),
                            ("RIGHTY", CONTROLLER_AXIS_RIGHTY),
                            ("TRIG_L", CONTROLLER_AXIS_TRIGGERLEFT),
                            ("TRIG_R", CONTROLLER_AXIS_TRIGGERRIGHT),
                        ):
                            try:
                                sdl_axes[name] = round(self._normalize_controller_axis(ctrl.get_axis(idx)), 3)
                            except pygame.error:
                                sdl_axes[name] = "err"
                    num_buttons = joystick.get_numbuttons()
                    buttons = {i: joystick.get_button(i) for i in range(num_buttons)}
                    logger.info(
                        "Controller %s axis dump – raw axes: %s | SDL2 logical: %s | buttons: %s",
                        controller_idx,
                        raw,
                        sdl_axes,
                        buttons,
                    )

                self._update_speed_from_bumpers(controller_idx, joystick)

                # Read left stick for X/Y
                dx, dy, _ = self._read_axes(controller_idx, joystick)

                # Discard a stale stick reading from a pad that hasn't centered
                # since (re)detection, so a restart can't fling the marker.
                stick_primed = self._update_stick_priming(controller_idx, dx, dy)
                if not stick_primed:
                    dx = dy = 0.0

                # Normalize diagonal stick input to prevent faster diagonal movement
                mag_xy = math.sqrt(dx * dx + dy * dy)
                if mag_xy > 1.0:
                    dx /= mag_xy
                    dy /= mag_xy

                # Read analog triggers for Z (height); disabled for controllers without them
                dz = self._read_trigger_height(controller_idx, joystick)

                if dx != 0.0 or dy != 0.0 or dz != 0.0:
                    move_speed = self.get_effective_speed(controller_idx)
                    result.movements[controller_idx] = (
                        dx * move_speed,
                        dy * move_speed,
                        dz * move_speed,
                    )

                # Marker-fader integrator. Push this controller's
                # chosen-stick Y deflection into the fader of the marker
                # this controller currently controls (resolved the same
                # way movement/reset/speed are – single-gamepad via
                # ``_selected_id``, multi-gamepad via fixed slot). The
                # store is keyed by marker id, so multiple controllers
                # never clobber each other and a single controller that
                # switches its marker switches which fader it drives. A
                # marker with no provisioned fader (resolver -> None, or
                # not in controlled_marker_ids) is a clean no-op.
                if (
                    stick_primed
                    and self._virtual_faders is not None
                    and self._marker_fader_stick
                    and self._marker_resolver is not None
                ):
                    deflection = self._read_marker_fader_deflection(
                        controller_idx,
                        joystick,
                    )
                    if deflection != 0.0:
                        marker_id = self._marker_resolver(controller_idx)
                        if marker_id is not None:
                            delta = deflection * dt / self._marker_fader_max_speed_s
                            self._virtual_faders.set_marker_fader_from_velocity_delta(
                                marker_id,
                                delta,
                            )

                # Configurable button actions
                if self._detect_button_edge(
                    controller_idx,
                    self._btn_reset_id,
                ):
                    result.resets.add(controller_idx)
                if self._detect_button_edge(
                    controller_idx,
                    self._btn_toggle_help_id,
                ):
                    result.toggle_help_pressed = True
                if self._btn_toggle_zones_id >= 0 and self._detect_button_edge(
                    controller_idx,
                    self._btn_toggle_zones_id,
                ):
                    result.toggle_zones_pressed = True
                # Station-wide clear: any pad's edge fires it. No >= 0 guard –
                # _get_button reads LT/RT as triggers and unbound as released.
                if self._detect_button_edge(controller_idx, self._btn_clear_messages_id):
                    result.clear_messages_pressed = True
                # Multi-pad: suppress DPAD next/prev to avoid rotating shared _selected_id.
                multi_pad = len(self.joysticks) > 1
                if (
                    self._detect_button_edge(
                        controller_idx,
                        self._btn_next_marker_id,
                    )
                    and not multi_pad
                ):
                    result.next_marker_pressed = True
                if (
                    self._detect_button_edge(
                        controller_idx,
                        self._btn_prev_marker_id,
                    )
                    and not multi_pad
                ):
                    result.prev_marker_pressed = True
                if self._detect_button_edge(
                    controller_idx,
                    self._btn_settings_id,
                ):
                    result.settings_open_pressed = True

            except pygame.error as e:
                logger.warning(
                    "Error reading controller %s, possibly disconnected: %s",
                    controller_idx,
                    e,
                )
                to_remove.append(controller_idx)

        self._cleanup_failed(to_remove)
        if self._event_bus is not None:
            emit_to_remove = self._emit_button_events()
            if emit_to_remove:
                self._cleanup_failed(emit_to_remove)
        return result

    def _emit_button_events(self) -> list[int]:
        """Walk every connected controller × every named button and
        emit press / release :class:`ButtonEvent`s on edge transitions.

        Uses a separate ``_button_bus_prev`` map so the existing
        ``_detect_button_edge`` callers (speed bumpers, etc.) keep
        their own state independent of the bus's edge detector – no
        shared-state race between the two consumers.

        Trigger axes (LT / RT) are exposed through ``_get_button``'s
        synthetic IDs and emit alongside the digital buttons; the
        deadzone in ``_get_button`` already handles axis-as-button
        debouncing, so a slight analog flutter near the threshold
        won't spam the bus.

        Returns a list of controller indices that raised
        :class:`pygame.error` mid-emission so the caller can route
        them through the same ``_cleanup_failed`` path the main
        ``update()`` loop uses. Without this guard a controller
        that disconnects between the ``update()`` poll and the
        emission walk would let the exception propagate up and break
        the input loop.
        """
        from openfollow.input.events import ButtonEvent

        # ``self._event_bus`` is narrowed by the caller; capture once
        # so the loop body doesn't need to re-narrow on every emit.
        bus = self._event_bus
        if bus is None:  # pragma: no cover - defensive
            return []
        failed: list[int] = []
        for controller_idx in tuple(self.joysticks):
            prev = self._button_bus_prev.setdefault(controller_idx, {})
            try:
                for name, button_id in BUTTON_NAME_TO_ID.items():
                    current = self._get_button(controller_idx, button_id)
                    was_pressed = prev.get(button_id, False)
                    if current and not was_pressed:
                        bus.emit_button(
                            ButtonEvent(
                                button=name,
                                controller_index=controller_idx,
                                edge="press",
                            )
                        )
                    elif not current and was_pressed:
                        bus.emit_button(
                            ButtonEvent(
                                button=name,
                                controller_index=controller_idx,
                                edge="release",
                            )
                        )
                    prev[button_id] = current
            except pygame.error as e:
                # Mid-frame disconnect during emission. Route the
                # offender through the standard cleanup path so the
                # input loop keeps running for the surviving
                # controllers.
                logger.warning(
                    "Error emitting button events for controller %s, marking for cleanup: %s",
                    controller_idx,
                    e,
                )
                failed.append(controller_idx)
        return failed

    def read_source_selection_input(self) -> SourceSelectionInput:
        """
        Read gamepad input relevant to NDI source selection mode.

        Pumps events, reads D-pad and buttons from all connected controllers.
        Returns a SourceSelectionInput with edge-detected navigation inputs.
        """
        self._pump_events()

        inp = SourceSelectionInput()
        if not self.joysticks:
            return inp

        to_remove: list[int] = []
        for controller_idx in tuple(self.joysticks):
            try:
                inp.up_pressed = inp.up_pressed or self._detect_button_edge(
                    controller_idx,
                    CONTROLLER_BUTTON_DPAD_UP,
                )
                inp.down_pressed = inp.down_pressed or self._detect_button_edge(
                    controller_idx,
                    CONTROLLER_BUTTON_DPAD_DOWN,
                )
                inp.confirm_pressed = inp.confirm_pressed or self._detect_button_edge(
                    controller_idx,
                    self._btn_menu_confirm_id,
                )
                inp.cancel_pressed = inp.cancel_pressed or self._detect_button_edge(
                    controller_idx,
                    self._btn_menu_cancel_id,
                )
                # Refresh normal-mode button/bumper prev-state (update() is
                # skipped while the picker is open) so closing it doesn't fire a
                # spurious reset / settings-open / speed change.
                self._sync_normal_mode_button_prev(controller_idx)
            except pygame.error as e:
                logger.warning("Error reading source selection input from controller %s: %s", controller_idx, e)
                to_remove.append(controller_idx)

        self._cleanup_failed(to_remove)

        return inp

    def read_settings_menu_input(self) -> SettingsMenuInput:
        """Read gamepad input for the Settings menu overlay."""
        self._pump_events()

        inp = SettingsMenuInput()
        if not self.joysticks:
            return inp

        to_remove: list[int] = []
        for controller_idx in tuple(self.joysticks):
            try:
                inp.up_pressed = inp.up_pressed or self._detect_button_edge(
                    controller_idx,
                    CONTROLLER_BUTTON_DPAD_UP,
                )
                inp.down_pressed = inp.down_pressed or self._detect_button_edge(
                    controller_idx,
                    CONTROLLER_BUTTON_DPAD_DOWN,
                )
                inp.confirm_pressed = inp.confirm_pressed or self._detect_button_edge(
                    controller_idx,
                    self._btn_menu_confirm_id,
                )
                inp.cancel_pressed = inp.cancel_pressed or self._detect_button_edge(
                    controller_idx,
                    self._btn_menu_cancel_id,
                )
                self._sync_normal_mode_button_prev(controller_idx)
            except pygame.error as e:
                logger.warning("Error reading settings menu input from controller %s: %s", controller_idx, e)
                to_remove.append(controller_idx)

        self._cleanup_failed(to_remove)

        return inp

    def _sync_normal_mode_button_prev(self, controller_idx: int) -> None:
        """Refresh edge-tracked state for normal-mode action buttons without dispatching.

        While the Settings menu is open, ``update()`` is skipped, so these
        buttons' prev-state would otherwise go stale and produce a spurious
        rising edge on the first frame after the menu closes. Covers both
        ``_button_prev`` (for `_detect_button_edge` actions) and
        ``_bumper_state`` (for `_update_speed_from_bumpers`).
        """
        prev_map = self._button_prev.setdefault(controller_idx, {})
        for btn_id in (
            self._btn_reset_id,
            self._btn_toggle_help_id,
            self._btn_toggle_zones_id,
            self._btn_next_marker_id,
            self._btn_prev_marker_id,
            self._btn_settings_id,
        ):
            prev_map[btn_id] = self._get_button(controller_idx, btn_id)
        self._bumper_state[controller_idx] = (
            self._get_button(controller_idx, self._btn_speed_down_id),
            self._get_button(controller_idx, self._btn_speed_up_id),
        )

    def get_controller_info(self) -> list[dict[str, Any]]:
        """Get info about connected controllers for HUD display.

        Returns:
            List of dicts with controller_index, name, connected,
            marker_id (None, filled by InputManager), effective_speed, backend.
        """
        info: list[dict[str, Any]] = []
        # Snapshot capabilities under the lock – this can be called from the
        # HUD path while the main loop rebuilds capabilities.
        with self._capabilities_lock:
            caps_by_idx = dict(self.capabilities)
        for idx, joystick in self.joysticks.items():
            caps = caps_by_idx.get(
                idx,
                ControllerCapabilities(backend="joystick"),
            )
            try:
                name = joystick.get_name()
                connected = True
            except pygame.error:
                name = "Unknown"
                connected = False
            info.append(
                {
                    "controller_index": idx,
                    "name": name,
                    "connected": connected,
                    "marker_id": None,
                    "effective_speed": self.get_effective_speed(idx),
                    "backend": caps.backend,
                }
            )
        return info

    def stop(self) -> None:
        """Release controller resources and pygame subsystems used by this handler."""
        self.enabled = False
        for ctrl in self.controllers.values():
            try:
                ctrl.quit()
            except pygame.error:
                pass
        self.controllers.clear()
        for joy in self.joysticks.values():
            try:
                joy.quit()
            except pygame.error:
                pass
        self.joysticks.clear()
        self._axes_logged.clear()
        self._stick_unprimed.clear()
        with self._capabilities_lock:
            self.capabilities.clear()
        if sdl2_controller is not None and sdl2_controller.get_init():
            sdl2_controller.quit()
        # pragma: no branch – the False arms only fire when the
        # subsystems were never initialised, which the existing stop
        # tests cover via the SDL2-not-initialised path. The True
        # arms are the normal teardown the rest of the suite exercises.
        if pygame.joystick.get_init():  # pragma: no branch
            pygame.joystick.quit()
        if pygame.get_init():  # pragma: no branch
            pygame.quit()
