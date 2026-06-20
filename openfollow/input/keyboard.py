# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Keyboard input handling with direct hardware polling.

This module provides reliable keyboard state by querying the OS directly,
avoiding GTK's event queue which can drop events under heavy load (e.g.,
when GStreamer video pipelines are running).

Platform support:
- macOS: Uses Quartz CGEventSourceKeyState
- Linux/Windows: Falls back to event-based tracking
"""

from __future__ import annotations

import ctypes
import glob
import logging
import math
import platform
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from openfollow.app import OpenFollowApp
    from openfollow.configuration import ControllerConfig
    from openfollow.input.events import InputEventBus

logger = logging.getLogger(__name__)

# XY movement key layouts.  The active layout is selected by
# ControllerConfig.key_move_layout.
MOVEMENT_LAYOUTS: dict[str, dict[str, str]] = {
    "wasd": {"forward": "w", "back": "s", "left": "a", "right": "d"},
    "ijkl": {"forward": "i", "back": "k", "left": "j", "right": "l"},
    "numpad": {
        "forward": "Numpad8",
        "back": "Numpad2",
        "left": "Numpad4",
        "right": "Numpad6",
    },
}


# Keys polled directly; consumers narrow to mapped subset per config.
POLLED_KEYS: list[str] = [
    "a",
    "b",
    "c",
    "d",
    "e",
    "f",
    "g",
    "h",
    "i",
    "j",
    "k",
    "l",
    "m",
    "n",
    "o",
    "p",
    "q",
    "r",
    "s",
    "t",
    "u",
    "v",
    "w",
    "x",
    "y",
    "z",
    "Tab",
    "Enter",
    "Escape",
    "Shift",
    "Control",
    "Alt",
    "Meta",
    "Space",
    "ArrowUp",
    "ArrowDown",
    "ArrowLeft",
    "ArrowRight",
    "Numpad0",
    "Numpad1",
    "Numpad2",
    "Numpad3",
    "Numpad4",
    "Numpad5",
    "Numpad6",
    "Numpad7",
    "Numpad8",
    "Numpad9",
]

# Virtual key codes for macOS (Carbon/HIToolbox KeyCodes)
_MAC_KEY_CODES: dict[str, int] = {
    "a": 0x00,
    "b": 0x0B,
    "c": 0x08,
    "d": 0x02,
    "e": 0x0E,
    "f": 0x03,
    "g": 0x05,
    "h": 0x04,
    "i": 0x22,
    "j": 0x26,
    "k": 0x28,
    "l": 0x25,
    "m": 0x2E,
    "n": 0x2D,
    "o": 0x1F,
    "p": 0x23,
    "q": 0x0C,
    "r": 0x0F,
    "s": 0x01,
    "t": 0x11,
    "u": 0x20,
    "v": 0x09,
    "w": 0x0D,
    "x": 0x07,
    "y": 0x10,
    "z": 0x06,
    "Space": 0x31,
    "Tab": 0x30,
    "Enter": 0x24,
    "Escape": 0x35,
    "Shift": 0x38,
    "RightShift": 0x3C,
    "Control": 0x3B,
    "RightControl": 0x3E,
    "Alt": 0x3A,
    "RightAlt": 0x3D,
    # macOS Command key (Carbon ``kVK_Command`` / ``kVK_RightCommand``).
    # Without these, the ``Meta`` branch of
    # :data:`KeyboardHandler._MODIFIER_KEY_TO_NAME` never reports as
    # held on macOS, and Cmd-modified hotkeys (plus the existing
    # Ctrl/Cmd+S handler) silently fail. Both sides of the keyboard
    # are mapped so the right-variant fallback in
    # :meth:`MacOSKeyboardPoller.is_key_pressed` can pick up either.
    "Meta": 0x37,
    "RightMeta": 0x36,
    "ArrowUp": 0x7E,
    "ArrowDown": 0x7D,
    "ArrowLeft": 0x7B,
    "ArrowRight": 0x7C,
    # Numeric keypad (Carbon/HIToolbox kVK_ANSI_Keypad* codes).
    "Numpad0": 0x52,
    "Numpad1": 0x53,
    "Numpad2": 0x54,
    "Numpad3": 0x55,
    "Numpad4": 0x56,
    "Numpad5": 0x57,
    "Numpad6": 0x58,
    "Numpad7": 0x59,
    "Numpad8": 0x5B,
    "Numpad9": 0x5C,
}


# ---------------------------------------------------------------------------
# Keyboard Pollers (Platform Abstraction)
# ---------------------------------------------------------------------------


class KeyboardPoller(ABC):
    """Abstract base class for keyboard state polling."""

    @abstractmethod
    def is_key_pressed(self, key: str) -> bool:
        """Check if a specific key is currently pressed."""

    def get_pressed_keys(self, keys: list[str]) -> set[str]:
        """Get set of currently pressed keys from a list of keys to check."""
        return {k for k in keys if self.is_key_pressed(k)}

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this poller is functional on the current system."""


def _build_layout_keycode_map() -> dict[str, int] | None:
    """Return a ``{char: keycode}`` map for the active macOS keyboard layout.

    ``CGEventSourceKeyState`` polls **hardware keycodes**, which are fixed
    positions on the physical keyboard and do not follow the user's
    selected layout.  On QWERTZ (German) for example, the key labelled
    ``Z`` sits at keycode ``0x10`` – the same position US QWERTY uses for
    ``Y``.  Without this map, polling for ``"z"`` would read the wrong
    physical key.

    Uses Carbon's ``TISCopyCurrentKeyboardLayoutInputSource`` +
    ``UCKeyTranslate`` to translate each virtual keycode back to the
    character it produces on the current layout, and builds an inverse
    lookup for letters.  Returns ``None`` if the Carbon/CoreFoundation
    APIs cannot be loaded (in which case callers should fall back to the
    hard-coded US mapping).
    """
    try:
        carbon = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/Carbon.framework/Carbon")
        cf = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    except OSError:
        return None

    try:
        key_layout_data_prop = ctypes.c_void_p.in_dll(carbon, "kTISPropertyUnicodeKeyLayoutData")
    except ValueError:
        return None

    carbon.TISCopyCurrentKeyboardLayoutInputSource.restype = ctypes.c_void_p
    carbon.TISCopyCurrentKeyboardLayoutInputSource.argtypes = []
    carbon.TISGetInputSourceProperty.restype = ctypes.c_void_p
    carbon.TISGetInputSourceProperty.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    carbon.LMGetKbdType.restype = ctypes.c_uint8
    carbon.LMGetKbdType.argtypes = []
    carbon.UCKeyTranslate.restype = ctypes.c_int32
    carbon.UCKeyTranslate.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint16,
        ctypes.c_uint16,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.c_uint16),
    ]
    cf.CFDataGetBytePtr.restype = ctypes.c_void_p
    cf.CFDataGetBytePtr.argtypes = [ctypes.c_void_p]
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    cf.CFRelease.restype = None

    source = carbon.TISCopyCurrentKeyboardLayoutInputSource()
    if not source:
        return None
    try:
        layout_data = carbon.TISGetInputSourceProperty(source, key_layout_data_prop.value)
        if not layout_data:
            return None
        layout_ptr = cf.CFDataGetBytePtr(layout_data)
        if not layout_ptr:
            return None
        kbd_type = carbon.LMGetKbdType()
        k_ucKeyActionDisplay = 3
        k_ucKeyTranslateNoDeadKeysMask = 1
        mapping: dict[str, int] = {}
        for keycode in range(128):
            dead_key_state = ctypes.c_uint32(0)
            chars = (ctypes.c_uint16 * 4)()
            actual = ctypes.c_ulong(0)
            status = carbon.UCKeyTranslate(
                layout_ptr,
                keycode,
                k_ucKeyActionDisplay,
                0,
                kbd_type,
                k_ucKeyTranslateNoDeadKeysMask,
                ctypes.byref(dead_key_state),
                4,
                ctypes.byref(actual),
                chars,
            )
            if status != 0 or actual.value == 0:
                continue
            ch = chr(chars[0]).lower()
            # Map letters AND digits/symbols – a bindable field (movement, Z,
            # a discrete action) can use any of them, and on AZERTY/QWERTZ the
            # digit/symbol positions differ from US. Ascending ``keycode``
            # iteration + first-wins keeps the lowest/primary keycode when a
            # char is reachable from several physical keys.
            if len(ch) == 1 and ch.isprintable() and not ch.isspace() and ch not in mapping:
                mapping[ch] = keycode
        return mapping or None
    finally:
        cf.CFRelease(source)


class MacOSKeyboardPoller(KeyboardPoller):
    """macOS keyboard poller using Quartz CGEventSourceKeyState."""

    _STATE_ID = 1  # kCGEventSourceStateHIDSystemState

    def __init__(self) -> None:
        self._quartz = None
        self._cg_event_source_key_state = None
        self._available = False
        self._key_codes: dict[str, int] = dict(_MAC_KEY_CODES)
        # The layout->keycode map is resolved once below, but the operator can
        # switch input source (QWERTZ<->US) at runtime. Re-resolve on a cadence
        # so polling doesn't keep reading the old physical key. Injectable clock
        # for tests.
        self._layout_clock = time.monotonic
        self._layout_check_interval = 1.0
        # Seed to "now" so the first is_key_pressed() doesn't immediately
        # re-resolve the map we just built below; refreshes start one interval
        # later.
        self._last_layout_check = self._layout_clock()

        try:
            self._quartz = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/Quartz.framework/Quartz")
            func = self._quartz.CGEventSourceKeyState
            func.argtypes = [ctypes.c_int32, ctypes.c_uint16]
            func.restype = ctypes.c_bool
            self._cg_event_source_key_state = func
            self._available = True
            logger.info("macOS keyboard poller initialized successfully")
        except OSError as e:
            logger.warning("Failed to load Quartz framework: %s", e)
            return

        layout_map = _build_layout_keycode_map()
        if layout_map:
            self._key_codes.update(layout_map)
            z_code = layout_map.get("z")
            if z_code is not None and z_code != _MAC_KEY_CODES.get("z"):
                logger.info(
                    "Non-US keyboard layout detected (z=0x%02X); using layout-aware keycodes for letter polling.",
                    z_code,
                )
        else:
            logger.warning(
                "Could not resolve active keyboard layout; falling back to "
                "US QWERTY keycodes (Z/Y may not match a QWERTZ layout)."
            )

    def is_available(self) -> bool:
        return self._available

    def _maybe_refresh_layout(self) -> None:
        """Re-resolve the layout->keycode map if the input source may have
        changed since the last check (throttled). A runtime QWERTZ<->US switch
        otherwise leaves ``_key_codes`` frozen at construction, so polling
        ``"z"`` reads the keycode for the old layout's physical position."""
        now = self._layout_clock()
        if now - self._last_layout_check < self._layout_check_interval:
            return
        self._last_layout_check = now
        new_map = _build_layout_keycode_map()
        if not new_map:
            return
        rebuilt = dict(_MAC_KEY_CODES)
        rebuilt.update(new_map)
        if rebuilt != self._key_codes:
            self._key_codes = rebuilt
            logger.info("Keyboard layout change detected; refreshed layout-aware keycodes.")

    def is_key_pressed(self, key: str) -> bool:
        if not self._available or self._cg_event_source_key_state is None:
            return False
        self._maybe_refresh_layout()

        keycode = self._key_codes.get(key)
        if keycode is None:
            return False

        result = self._cg_event_source_key_state(self._STATE_ID, keycode)

        # Modifiers exist on both sides of the keyboard – fall back to the
        # right-hand variant when the left isn't pressed. ``Meta`` covers
        # the macOS Command key; both Carbon keycodes are populated by
        # ``_MAC_KEY_CODES`` so ``cmd``-modified hotkeys are reachable
        # regardless of which physical Cmd the operator presses.
        if not result:
            right_variant = {
                "Shift": "RightShift",
                "Control": "RightControl",
                "Alt": "RightAlt",
                "Meta": "RightMeta",
            }.get(key)
            if right_variant is not None:
                rcode = self._key_codes.get(right_variant)
                if rcode is not None:
                    result = self._cg_event_source_key_state(self._STATE_ID, rcode)

        return bool(result)


class FallbackKeyboardPoller(KeyboardPoller):
    """Fallback poller that tracks key state via events."""

    def __init__(self) -> None:
        self._pressed: set[str] = set()
        logger.info("Using fallback event-based keyboard tracking")

    def is_available(self) -> bool:
        return True

    def is_key_pressed(self, key: str) -> bool:
        return key in self._pressed

    def on_key_down(self, key: str) -> None:
        """Track key press (called by event handler)."""
        self._pressed.add(key)

    def on_key_up(self, key: str) -> None:
        """Track key release (called by event handler)."""
        self._pressed.discard(key)

    def clear(self) -> None:
        """Clear all pressed keys."""
        self._pressed.clear()


def create_keyboard_poller() -> KeyboardPoller:
    """Create the appropriate keyboard poller for the current platform."""
    system = platform.system()

    if system == "Darwin":
        poller = MacOSKeyboardPoller()
        if poller.is_available():
            return poller
        logger.warning("macOS native poller unavailable, using fallback")

    return FallbackKeyboardPoller()


# ---------------------------------------------------------------------------
# Keyboard Handler (Application-Level)
# ---------------------------------------------------------------------------


class KeyboardHandler:
    """Handles keyboard input for marker and camera control.

    Uses direct hardware polling (macOS) or event-based tracking (fallback)
    to get reliable keyboard state, bypassing GTK's event system which can
    drop events under heavy GStreamer video load.
    """

    # Modal/navigation keys polled regardless of user mapping (used by
    # calibration mode, source selection, button-detection wizard).
    _MODAL_DISCRETE_KEYS: frozenset[str] = frozenset(
        {
            "Tab",
            "Enter",
            "Escape",
            "ArrowUp",
            "ArrowDown",
            "ArrowLeft",
            "ArrowRight",
            "b",
        }
    )

    # Normal-mode discrete actions read from controller config.
    _DISCRETE_ACTION_FIELDS: tuple[str, ...] = (
        "key_reset",
        "key_toggle_help",
        "key_toggle_zones",
        "key_speed_down",
        "key_speed_up",
        "key_next_marker",
        "key_prev_marker",
    )

    # Map physical modifier keys to the canonical names used by
    # ``HotkeyTrigger.modifiers`` and ``KeyEvent.modifiers``. Source of
    # truth for both sides is ``VALID_TRIGGER_MODIFIERS`` in
    # ``openfollow.configuration``; this map is the keyboard-poller
    # bridge from physical key names to that canonical set.
    _MODIFIER_KEY_TO_NAME: dict[str, str] = {
        "Shift": "shift",
        "Control": "ctrl",
        "Alt": "alt",
        "Meta": "cmd",
    }

    def __init__(
        self,
        app: OpenFollowApp,
        *,
        event_bus: InputEventBus | None = None,
    ) -> None:
        self.app: OpenFollowApp = app
        self._poller: KeyboardPoller = create_keyboard_poller()
        self._prev_key_state: dict[str, bool] = {}
        self._pending_presses: list[str] = []
        self._is_fallback: bool = isinstance(self._poller, FallbackKeyboardPoller)
        self._keyboard_connected_cache: bool = self._poller.is_available()
        self._next_keyboard_probe_t: float = 0.0
        # Hardware-emission sequence guard: the input
        # event bus that ``poll_discrete_keys`` publishes to. ``None``
        # disables emission (e.g. test harnesses without an
        # InputManager). The bus is owned by InputManager – this
        # handler doesn't manage its lifetime.
        self._event_bus = event_bus
        # Memoise the discrete-key set (rebuilt every frame but only changes
        # when controller config is rebound). Hot-reload reassigns
        # ``app._config.controller`` to a new object
        # only when it actually changes (``apply_runtime_config_changes``), so
        # an identity check is a correct, allocation-free invalidation signal.
        self._discrete_keys_cc: ControllerConfig | None = None
        self._discrete_keys_cache: frozenset[str] = frozenset()
        if self._is_fallback:
            logger.warning("Using fallback keyboard tracking – key events may be lost")

    def _discrete_polled_keys(self) -> frozenset[str]:
        cc = self.app._config.controller
        if cc is self._discrete_keys_cc:
            return self._discrete_keys_cache
        keys = set(self._MODAL_DISCRETE_KEYS)
        for fname in self._DISCRETE_ACTION_FIELDS:
            val = getattr(cc, fname, "")
            if val:
                keys.add(val)
        self._discrete_keys_cc = cc
        self._discrete_keys_cache = frozenset(keys)
        return self._discrete_keys_cache

    @property
    def keys(self) -> set[str]:
        """Return set of currently pressed keys (for compatibility)."""
        return self._poller.get_pressed_keys(POLLED_KEYS)

    def is_connected(self) -> bool:
        """Return whether a keyboard appears to be connected and usable."""
        now = time.monotonic()
        if now < self._next_keyboard_probe_t:
            return self._keyboard_connected_cache

        self._next_keyboard_probe_t = now + 1.0
        self._keyboard_connected_cache = self._probe_keyboard_connected()
        return self._keyboard_connected_cache

    def _probe_keyboard_connected(self) -> bool:
        system = platform.system()
        if system == "Darwin":
            # On macOS, Quartz polling only works when a keyboard input source exists.
            return self._poller.is_available()
        if system == "Linux":
            return self._probe_linux_keyboard_connected()
        return self._poller.is_available()

    @staticmethod
    def _probe_linux_keyboard_connected() -> bool:
        """Best-effort Linux keyboard detection using procfs and input symlinks."""
        # Parse kernel input devices first (works even without /dev/input/by-id).
        try:
            with open("/proc/bus/input/devices", encoding="utf-8") as f:
                blocks = f.read().split("\n\n")
            for block in blocks:
                lines = [line.strip() for line in block.splitlines() if line.strip()]
                if not lines:
                    continue  # pragma: no cover - peephole-elided continue (see docs/COVERAGE.md)
                name = ""
                handlers = ""
                for line in lines:
                    if line.startswith("N: Name="):
                        name = line.split("=", 1)[1].strip().strip('"').lower()
                    elif line.startswith("H: Handlers="):
                        handlers = line.split("=", 1)[1].strip().lower()
                if "kbd" not in handlers:
                    continue
                # Ignore pseudo keyboard handlers that are typically just power buttons.
                if "gpio" in name or "power button" in name or "sleep button" in name:
                    continue
                return True
            # If procfs is present and no keyboard-like device matched, treat as disconnected.
            return False
        except OSError:
            pass

        # Fallback: standard Linux input naming convention.
        return bool(glob.glob("/dev/input/by-id/*-kbd"))

    def _current_modifiers(self) -> frozenset[str]:
        """Snapshot the held-modifier set at the moment of polling.

        Used to stamp every emitted ``KeyEvent`` with the modifier
        state matching ``HotkeyTrigger.modifiers`` (canonical
        lower-case names from
        :data:`openfollow.configuration.VALID_TRIGGER_MODIFIERS`).
        Polled once per frame at the top of ``poll_discrete_keys``;
        all per-key edge events emitted from that frame share the
        same snapshot, which is the right model for the polled-state
        contract – modifier flicker faster than 60 Hz isn't
        observable through this interface anyway.
        """
        return frozenset(
            canonical for key, canonical in self._MODIFIER_KEY_TO_NAME.items() if self._poller.is_key_pressed(key)
        )

    def poll_discrete_keys(self) -> None:
        """Poll keyboard state and detect press / release edges.

        Two paths share the same poll loop:

        - **Discrete-action queue** (legacy): keys in
          :meth:`_discrete_polled_keys` populate ``_pending_presses``
          on the press edge so the app-orchestration loop's
          ``consume_key_presses`` can drive controller-config actions.
        - **Event bus**: if a bus is attached, every key in :data:`POLLED_KEYS` emits a
          :class:`KeyEvent` on each press / release transition.
          ``OscTransmitterManager`` subscribes to enqueue plans for
          matching ``HotkeyTrigger`` rows.

        Modifier state is snapshotted once at the top of the call,
        so every event emitted from this frame carries the same
        modifier set – see :meth:`_current_modifiers`.

        When no bus is attached (headless / test harnesses), the
        bus-only ``POLLED_KEYS`` walk would burn ``is_key_pressed``
        syscalls for keys nothing consumes – narrow the loop to just
        the discrete-action set in that case. The bus binding is
        fixed at construction (no runtime setter), but the discrete set
        itself can shrink on a config rebind (e.g. ``key_next_marker``
        ``n`` -> ``m``), which would otherwise leave the dropped key's
        ``_prev_key_state`` entry stranded at ``True``. The prune below
        drops edge state for any key no longer in ``keys_to_poll`` so a
        later re-add re-sees the press edge instead of a phantom held state.
        """
        discrete = self._discrete_polled_keys()
        if self._event_bus is not None:
            modifiers = self._current_modifiers()
            keys_to_poll: list[str] | frozenset[str] = POLLED_KEYS
        else:
            # No bus → no emission, no need to query modifier state.
            modifiers = frozenset()
            keys_to_poll = discrete
        for key in keys_to_poll:
            is_pressed = self._poller.is_key_pressed(key)
            was_pressed = self._prev_key_state.get(key, False)
            if is_pressed and not was_pressed:
                if key in discrete:
                    self._pending_presses.append(key)
                if self._event_bus is not None:
                    self._emit_key_event(key, modifiers, "press")
            elif not is_pressed and was_pressed:
                if self._event_bus is not None:
                    self._emit_key_event(key, modifiers, "release")
            self._prev_key_state[key] = is_pressed
        # Drop edge state for keys no longer polled (discrete-set shrink on a
        # rebind, or the bus-less narrowing) so a key that leaves the polled
        # set can't strand a True ``was_pressed`` and swallow its next edge.
        stale = [k for k in self._prev_key_state if k not in keys_to_poll]
        for k in stale:
            del self._prev_key_state[k]

    def _emit_key_event(
        self,
        key: str,
        modifiers: frozenset[str],
        edge: Literal["press", "release"],
    ) -> None:
        """Emit one :class:`KeyEvent` to the bus. Lazy-imported to keep
        the keyboard module load-free of the OSC trigger model when
        no bus is attached (e.g. headless test harnesses)."""
        # pragma: no cover – guarded by the call-site check; mypy
        # narrows ``self._event_bus`` only inside the ``if`` body
        # there, so the explicit guard here is belt-and-braces.
        if self._event_bus is None:  # pragma: no cover
            return
        from openfollow.input.events import KeyEvent

        self._event_bus.emit_key(
            KeyEvent(
                key=key,
                modifiers=modifiers,
                edge=edge,
            )
        )

    def consume_key_presses(self) -> list[str]:
        """Return and clear pending key presses. Call after poll_discrete_keys."""
        presses = self._pending_presses[:]
        self._pending_presses.clear()
        return presses

    def on_key_down(self, event: dict[str, Any]) -> None:
        """Handle key press events from GTK (fallback mode only)."""
        if not self._is_fallback:
            return

        key = event.get("key", "")
        if len(key) == 1:
            key = key.lower()

        if key in POLLED_KEYS:
            self._poller.on_key_down(key)  # type: ignore[attr-defined]

    def on_key_up(self, event: dict[str, Any]) -> None:
        """Handle key release events from GTK (fallback mode only)."""
        if not self._is_fallback:
            return

        key = event.get("key", "")
        if len(key) == 1:
            key = key.lower()

        if key in POLLED_KEYS:
            self._poller.on_key_up(key)  # type: ignore[attr-defined]

    def clear(self) -> None:
        """Drop all tracked key state.

        Wired to context switches – return from a modal/overlay to direct
        marker control, and window focus-out – so a key held across the switch
        (or one whose key-up GTK dropped under load) can't keep driving the
        marker once control comes back. Resets
        the fallback poller's pressed set and the handler's edge state together
        so the next poll sees no stale ``was_pressed`` (no spurious release
        edge). The macOS poller reads hardware directly and has no ``clear``;
        only its edge state is reset.

        Note: this recovers on a context switch, not mid-gameplay – a dropped
        key-up during continuous control still relies on the operator tapping
        the key, pending the planned hardware-level evdev poller.
        """
        clear_fn = getattr(self._poller, "clear", None)
        if callable(clear_fn):
            clear_fn()
        self._prev_key_state.clear()

    def update(self, dt: float) -> tuple[float, float, float] | None:
        """Process keyboard input and return marker movement velocity.

        Args:
            dt: Delta time (unused - kept for API consistency)

        Returns:
            (vx, vy, vz) velocity in m/s if marker should move, None otherwise
        """
        cc = self.app._config.controller
        # Keyboard movement scales by the selected marker's
        # per-marker speed (falls back to ``MarkerConfig.move_speed``).
        # The caller in ``InputManager.update`` skips movement application
        # when ``_selected_id`` is None, so the fallback only fires for
        # tests that don't go through that gate.
        move_speed = self.app.get_marker_move_speed(self.app._selected_id)

        def pressed(key: str) -> bool:
            return bool(key) and self._poller.is_key_pressed(key)

        layout = MOVEMENT_LAYOUTS.get(cc.key_move_layout, MOVEMENT_LAYOUTS["wasd"])
        vx = vy = vz = 0.0

        if pressed(layout["forward"]):
            vy += move_speed
        if pressed(layout["back"]):
            vy -= move_speed
        if pressed(layout["left"]):
            vx -= move_speed
        if pressed(layout["right"]):
            vx += move_speed

        if vx != 0.0 and vy != 0.0:
            mag = math.sqrt(vx * vx + vy * vy)
            vx = vx / mag * move_speed
            vy = vy / mag * move_speed

        if pressed(cc.key_move_z_up):
            vz += move_speed
        if pressed(cc.key_move_z_down):
            vz -= move_speed

        if vx != 0.0 or vy != 0.0 or vz != 0.0:
            return (vx, vy, vz)

        return None
