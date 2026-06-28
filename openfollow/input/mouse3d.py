# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""3D Mouse (3Dconnexion 6DOF) input handler.

A background daemon thread polls the device over HID and publishes the latest
six-axis deflection plus button state under a lock; the per-frame ``update``
runs on the GTK tick, consuming the latest snapshot so a blocking HID read
never lands on the vsync callback. ``pyspacemouse`` is imported lazily inside
the thread (``easyhid-ng`` ``dlopen``s ``libhidapi`` at import, so a missing
library raises ``OSError``, not just ``ImportError``); when it can't load, the
handler stays inert and the rest of the input subsystem is unaffected.

Axis shaping (deadzone + response curve) mirrors the gamepad handler. Each of
the six source axes resolves to a marker target (``none``/``x``/``y``/``z``/
``speed``) with its own sensitivity and invert; ``speed``-mapped axes ramp the
move-speed while held. The returned velocity is a unit rate – the caller scales
it by the marker's move-speed – so the handler stays free of app state.
"""

from __future__ import annotations

import importlib.util
import logging
import math
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from openfollow.configuration import MOUSE3D_AXES

if TYPE_CHECKING:
    from openfollow.configuration import Mouse3DConfig

logger = logging.getLogger(__name__)

# Reconnect backoff bounds (seconds) and poll cadence. The poll is interruptible
# via the stop Event so shutdown is prompt.
_RECONNECT_MIN_S = 0.5
_RECONNECT_MAX_S = 5.0
_IDLE_POLL_S = 0.005
_POLL_S = 0.004
# Move-speed steps per second at full deflection for a ``speed``-mapped axis.
_SPEED_AXIS_RATE = 6.0

# Config source-axis name -> device State attribute.
_AXIS_SOURCE = {
    "pan_x": "x",
    "pan_y": "y",
    "lift": "z",
    "pitch": "pitch",
    "yaw": "yaw",
    "roll": "roll",
}


def check_mouse3d_dependencies() -> list[str]:
    """Return pip-install names of missing 3D Mouse deps (``[]`` when present).

    Probes via :func:`importlib.util.find_spec` so the check never triggers
    ``easyhid``'s ``libhidapi`` ``dlopen`` (which a bare import would).
    """
    if importlib.util.find_spec("pyspacemouse") is None:
        return ["pyspacemouse"]
    return []


@dataclass(frozen=True)
class _Snapshot:
    """Latest raw device reading, published by the worker for ``update``."""

    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    roll: float = 0.0
    pitch: float = 0.0
    yaw: float = 0.0
    buttons: tuple[int, ...] = ()


@dataclass
class Mouse3DUpdate:
    """Per-frame result the InputManager applies.

    ``velocity`` is a unit rate (the caller multiplies by the marker's
    move-speed). ``speed_steps`` is the signed number of discrete move-speed
    adjustments to apply this frame (button edges + ``speed``-axis ramp). The
    booleans fold into the shared action flags so the app's existing dispatch
    handles them.
    """

    velocity: tuple[float, float, float] = (0.0, 0.0, 0.0)
    speed_steps: int = 0
    reset: bool = False
    next_marker: bool = False
    prev_marker: bool = False
    toggle_help: bool = False
    toggle_zones: bool = False
    settings: bool = False


def _finite_axis(value: Any) -> float:
    """Coerce a device axis to a finite float clamped to [-1, 1]."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(out):
        return 0.0
    return max(-1.0, min(1.0, out))


class Mouse3DHandler:
    """Reads a 3D Mouse on a background thread; maps deflection to marker input.

    The read thread runs for the handler's lifetime (started by the
    InputManager); ``Mouse3DConfig.enabled`` gates movement application in the
    InputManager, not the read, so the Detect flow and the connected-status
    badge work without a mode toggle.
    """

    def __init__(
        self,
        config: Mouse3DConfig,
        *,
        device_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._cfg = config
        # ``None`` -> lazy ``pyspacemouse.open`` resolved on the worker thread.
        self._device_factory = device_factory
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._snapshot: _Snapshot | None = None
        self._available = device_factory is not None  # real availability set by worker
        self._connected = False
        self._import_error: str | None = None
        # GTK-side edge-detection + speed-ramp accumulator (consumer state).
        self._prev_buttons: dict[int, bool] = {}
        self._speed_accum = 0.0

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Start the read thread (idempotent).

        No-op unless the feature is enabled – the device is only read while in
        use. Also a no-op when ``pyspacemouse`` isn't installed (no point
        spawning a reconnect loop that can never open a device). An injected
        ``device_factory`` (tests) bypasses the dependency check.
        """
        if self._thread is not None and self._thread.is_alive():
            return
        if not self._cfg.enabled:
            return
        if self._device_factory is None and check_mouse3d_dependencies():
            with self._lock:
                self._available = False
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="Mouse3D")
        self._thread.start()

    def stop(self) -> None:
        """Stop the read thread and release the device."""
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2.0)
            self._thread = None

    def reload_config(self, config: Mouse3DConfig) -> None:
        """Swap the mapping config (mapping is read on the GTK side)."""
        with self._lock:
            self._cfg = config

    # -- status (web/UI) ---------------------------------------------------

    @property
    def available(self) -> bool:
        """True when ``pyspacemouse`` + ``libhidapi`` loaded."""
        with self._lock:
            return self._available

    @property
    def connected(self) -> bool:
        """True when a device is currently open."""
        with self._lock:
            return self._connected

    def latest_button(self) -> int | None:
        """Return a currently-pressed button index, or ``None`` (Detect flow)."""
        with self._lock:
            snap = self._snapshot
        if snap is None:
            return None
        for idx, pressed in enumerate(snap.buttons):
            if pressed:
                return idx
        return None

    # -- per-frame consume (GTK thread) ------------------------------------

    def update(self, dt: float) -> Mouse3DUpdate:
        """Map the latest snapshot to a marker-input result for this frame."""
        with self._lock:
            snap = self._snapshot
            cfg = self._cfg
        if snap is None:
            return Mouse3DUpdate()

        vx = vy = vz = 0.0
        speed_signal = 0.0
        for axis in MOUSE3D_AXES:
            raw = getattr(snap, _AXIS_SOURCE[axis])
            if getattr(cfg, f"invert_{axis}"):
                raw = -raw
            shaped = self._shape(raw, cfg) * float(getattr(cfg, f"sens_{axis}"))
            target = getattr(cfg, f"map_{axis}")
            if target == "x":
                vx += shaped
            elif target == "y":
                vy += shaped
            elif target == "z":
                vz += shaped
            elif target == "speed":
                speed_signal += shaped
            # "none" -> ignore

        speed_steps = self._accumulate_speed(speed_signal, dt)
        edges = self._button_edges(snap.buttons)
        return Mouse3DUpdate(
            velocity=(vx, vy, vz),
            speed_steps=speed_steps
            + (1 if self._fired(cfg.btn_speed_up, edges) else 0)
            - (1 if self._fired(cfg.btn_speed_down, edges) else 0),
            reset=self._fired(cfg.btn_reset, edges),
            next_marker=self._fired(cfg.btn_next_marker, edges),
            prev_marker=self._fired(cfg.btn_prev_marker, edges),
            toggle_help=self._fired(cfg.btn_toggle_help, edges),
            toggle_zones=self._fired(cfg.btn_toggle_zones, edges),
            settings=self._fired(cfg.btn_settings, edges),
        )

    def _shape(self, value: float, cfg: Mouse3DConfig) -> float:
        """Deadzone + response curve, mirroring the gamepad shaping."""
        dz = cfg.deadzone
        if dz >= 1.0 or abs(value) < dz:
            return 0.0
        sign = 1.0 if value > 0 else -1.0
        scaled = (abs(value) - dz) / (1.0 - dz)
        return sign * self._curve(scaled, cfg.curve)

    @staticmethod
    def _curve(value: float, curve: str) -> float:
        if curve == "logarithmic":
            return math.log1p(9.0 * value) / math.log(10.0)
        if curve == "quadratic":
            return value * value
        if curve == "s-law":
            return 3.0 * value * value - 2.0 * value * value * value
        return value  # linear

    def _accumulate_speed(self, signal: float, dt: float) -> int:
        """Integrate a ``speed``-axis signal into discrete steps (held ramps)."""
        if signal == 0.0:
            self._speed_accum = 0.0
            return 0
        self._speed_accum += signal * _SPEED_AXIS_RATE * dt
        steps = 0
        while self._speed_accum >= 1.0:
            steps += 1
            self._speed_accum -= 1.0
        while self._speed_accum <= -1.0:
            steps -= 1
            self._speed_accum += 1.0
        return steps

    def _button_edges(self, buttons: tuple[int, ...]) -> set[int]:
        """Rising edges since the last frame; updates the prev-state map."""
        edges: set[int] = set()
        new_prev: dict[int, bool] = {}
        for idx, value in enumerate(buttons):
            cur = bool(value)
            new_prev[idx] = cur
            if cur and not self._prev_buttons.get(idx, False):
                edges.add(idx)
        self._prev_buttons = new_prev
        return edges

    @staticmethod
    def _fired(button_index: int, edges: set[int]) -> bool:
        """True when a bound (``>= 0``) button index rose this frame."""
        return button_index >= 0 and button_index in edges

    # -- worker thread -----------------------------------------------------

    def _run(self) -> None:
        try:
            factory = self._resolve_factory()
        except (ImportError, OSError) as exc:
            logger.warning("3D Mouse support unavailable (pyspacemouse/libhidapi): %s", exc)
            with self._lock:
                self._available = False
                self._import_error = str(exc)
            return
        with self._lock:
            self._available = True
        backoff = _RECONNECT_MIN_S
        while not self._stop.is_set():
            device = self._open_device(factory)
            if device is None:
                with self._lock:
                    self._connected = False
                if self._stop.wait(backoff):
                    break
                backoff = min(backoff * 2.0, _RECONNECT_MAX_S)
                continue
            backoff = _RECONNECT_MIN_S
            with self._lock:
                self._connected = True
            try:
                self._pump(device)
            finally:
                with self._lock:
                    self._connected = False
                _safe_close(device)

    def _resolve_factory(self) -> Callable[[], Any]:
        if self._device_factory is not None:
            return self._device_factory
        import pyspacemouse  # lazy: easyhid dlopens libhidapi here (may raise OSError)

        open_fn: Callable[[], Any] = pyspacemouse.open
        return open_fn

    @staticmethod
    def _open_device(factory: Callable[[], Any]) -> Any | None:
        try:
            device = factory()
        except OSError as exc:  # e.g. hidraw permission denied – retryable
            logger.debug("3D Mouse open failed: %s", exc)
            return None
        # ``pyspacemouse.open`` returns ``False`` when no device is present.
        return device or None

    def _pump(self, device: Any) -> None:
        while not self._stop.is_set():
            try:
                state = device.read()
            except OSError as exc:
                logger.info("3D Mouse read failed (disconnect?): %s", exc)
                return
            if state is None:
                if self._stop.wait(_IDLE_POLL_S):
                    return
                continue
            snapshot = _Snapshot(
                x=_finite_axis(getattr(state, "x", 0.0)),
                y=_finite_axis(getattr(state, "y", 0.0)),
                z=_finite_axis(getattr(state, "z", 0.0)),
                roll=_finite_axis(getattr(state, "roll", 0.0)),
                pitch=_finite_axis(getattr(state, "pitch", 0.0)),
                yaw=_finite_axis(getattr(state, "yaw", 0.0)),
                buttons=tuple(int(bool(b)) for b in (getattr(state, "buttons", None) or ())),
            )
            with self._lock:
                self._snapshot = snapshot
            if self._stop.wait(_POLL_S):
                return


def _safe_close(device: Any) -> None:
    close = getattr(device, "close", None)
    if close is None:
        return
    try:
        close()
    except Exception:  # noqa: BLE001 - close is best-effort on teardown
        logger.debug("3D Mouse close raised", exc_info=True)
