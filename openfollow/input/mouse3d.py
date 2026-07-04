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

import contextlib
import importlib.util
import io
import logging
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from openfollow.configuration import MOUSE3D_AXES
from openfollow.input.shaping import shape_axis

if TYPE_CHECKING:
    from openfollow.configuration import Mouse3DConfig

logger = logging.getLogger(__name__)

# User-facing controller name (HUD / web status) for a 3D Mouse "controller".
MOUSE3D_NAME = "3D Mouse"

# Reconnect backoff bounds (seconds) and poll cadence. The poll is interruptible
# via the stop Event so shutdown is prompt.
_RECONNECT_MIN_S = 0.5
_RECONNECT_MAX_S = 5.0
_IDLE_POLL_S = 0.005
_POLL_S = 0.004
# Move-speed steps per second at full deflection for a ``speed``-mapped axis.
_SPEED_AXIS_RATE = 6.0
# Web "Detect" flow: how long to watch for a button press after the click, and
# the poll cadence within that window.
_DETECT_TIMEOUT_S = 2.0
_DETECT_POLL_S = 0.01

# Config source-axis name -> device State attribute.
_AXIS_SOURCE = {
    "pan_x": "x",
    "pan_y": "y",
    "lift": "z",
    "pitch": "pitch",
    "yaw": "yaw",
    "roll": "roll",
}

# 3Dconnexion USB vendor/product range (kept in step with the udev rule in
# packaging/udev/99-openfollow-3dmouse.rules).
_VID_3DCONNEXION = 0x256F
_VID_LOGITECH_LEGACY = 0x046D
_SPACEMOUSE_PID_LO = 0xC600
_SPACEMOUSE_PID_HI = 0xC6FF

# How often the manager re-enumerates connected pucks (hotplug), seconds.
_ENUMERATE_INTERVAL_S = 1.5


def _is_supported_puck(vendor_id: int, product_id: int) -> bool:
    """True for a USB VID/PID in the 3Dconnexion range the backend can drive."""
    if vendor_id == _VID_3DCONNEXION:
        return True
    return vendor_id == _VID_LOGITECH_LEGACY and _SPACEMOUSE_PID_LO <= product_id <= _SPACEMOUSE_PID_HI


@dataclass(frozen=True)
class Mouse3DDeviceInfo:
    """Identity of one connected 3D Mouse, keyed by its stable hidraw path."""

    path: str
    product_name: str = ""
    serial: str = ""


class Mouse3DBackend(Protocol):
    """Enumerates and opens 3D mice. Injected as a fake in tests."""

    def enumerate(self) -> list[Mouse3DDeviceInfo]: ...

    def open(self, path: str) -> Any: ...


class _PySpaceMouseBackend:
    """Real backend over ``pyspacemouse`` (2.x) + ``easyhid``.

    ``easyhid`` reports one entry per HID collection, so a single puck appears
    several times at the same ``/dev/hidraw*`` node; we dedup by path and open
    each unique node once with ``open_by_path``.
    """

    def enumerate(self) -> list[Mouse3DDeviceInfo]:
        try:
            from easyhid import Enumeration  # lazy: dlopens libhidapi (may raise OSError)
        except (ImportError, OSError) as exc:
            logger.debug("3D Mouse enumeration unavailable: %s", exc)
            return []
        try:
            devices = Enumeration().find()
        except OSError as exc:
            logger.debug("3D Mouse enumeration failed: %s", exc)
            return []
        by_path: dict[str, Mouse3DDeviceInfo] = {}
        for dev in devices:
            vid = int(getattr(dev, "vendor_id", 0) or 0)
            pid = int(getattr(dev, "product_id", 0) or 0)
            if not _is_supported_puck(vid, pid):
                continue
            path = getattr(dev, "path", None)
            if isinstance(path, bytes):
                path = path.decode("utf-8", "replace")
            if not path or path in by_path:
                continue
            by_path[path] = Mouse3DDeviceInfo(
                path=path,
                product_name=str(getattr(dev, "product_string", "") or ""),
                serial=str(getattr(dev, "serial_number", "") or ""),
            )
        return [by_path[p] for p in sorted(by_path)]

    def open(self, path: str) -> Any:
        import pyspacemouse  # lazy

        # ``open_by_path`` prints to stdout on success; keep the log clean.
        with contextlib.redirect_stdout(io.StringIO()):
            return pyspacemouse.open_by_path(path)


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
    adjustments to apply this frame (button edges + ``speed``-axis ramp).
    ``fader_signal`` is a unit rate for ``fader``-mapped axes (the caller scales
    it by ``dt / marker_fader_max_speed_s`` and integrates into the marker's
    fader). The booleans fold into the shared action flags so the app's existing
    dispatch handles them.
    """

    velocity: tuple[float, float, float] = (0.0, 0.0, 0.0)
    speed_steps: int = 0
    fader_signal: float = 0.0
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

    The read thread runs only while the feature is enabled: the InputManager
    starts it on enable and stops it on disable to match ``Mouse3DConfig.enabled``,
    which gates both the device read and movement application. The web Detect
    flow still works while disabled – ``detect_pressed_button`` opens the device
    for a one-shot poll when the thread isn't running.
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
        # Serialises the web Detect flow so two concurrent requests can't both
        # open the singleton HID device.
        self._detect_lock = threading.Lock()
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
        # A fresh stop Event per worker: a prior worker whose join timed out keeps
        # watching its own (still-set) event, so it can't resurrect by reading a
        # shared event the new worker cleared.
        stop = threading.Event()
        self._stop = stop
        self._thread = threading.Thread(target=self._run, args=(stop,), daemon=True, name="Mouse3D")
        self._thread.start()

    def stop(self, *, wait: bool = False) -> None:
        """Signal the read thread to stop and release the device.

        Non-blocking by default: a live *disable* runs on the GTK main loop, so
        joining a worker parked in a blocking HID read would stall the HUD. We
        only set the (per-worker) stop Event and return; the daemon worker exits
        on its next stop check and closes the device in its ``finally``, and the
        pre-publish guard in ``_pump`` keeps a stopping worker from writing a
        reading after the stop (so the snapshot-clear below holds). App shutdown
        passes ``wait=True`` to join so the device is released before teardown.
        """
        self._stop.set()
        thread = self._thread
        self._thread = None
        if wait and thread is not None:
            thread.join(timeout=2.0)
        # Drop the last reading so re-enabling never re-applies a stale deflection.
        with self._lock:
            self._snapshot = None

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
        """Return a currently-pressed button index from the latest snapshot."""
        with self._lock:
            snap = self._snapshot
        if snap is None:
            return None
        for idx, pressed in enumerate(snap.buttons):
            if pressed:
                return idx
        return None

    def detect_pressed_button(self, timeout: float = _DETECT_TIMEOUT_S) -> int | None:
        """Watch briefly for a button press, for the web Detect bind flow.

        The operator clicks Detect, then presses the button to bind. When the
        read thread is running (feature enabled) this watches the published
        snapshot; otherwise it opens the device for a one-shot poll so buttons
        can be bound while the feature is still disabled. Returns the first
        pressed index seen within ``timeout`` seconds, or ``None``.
        """
        # Serialise so two concurrent web requests can't both open the singleton
        # HID device; a second concurrent detect no-ops rather than racing
        # open/read/close on the same node.
        if not self._detect_lock.acquire(blocking=False):
            return None
        try:
            with self._lock:
                running = self._thread is not None and self._thread.is_alive()
            if running:
                return self._poll_snapshot_button(timeout)
            return self._poll_device_button(timeout)
        finally:
            self._detect_lock.release()

    def _poll_snapshot_button(self, timeout: float) -> int | None:
        deadline = time.monotonic() + timeout
        while True:
            idx = self.latest_button()
            if idx is not None:
                return idx
            if time.monotonic() >= deadline:
                return None
            time.sleep(_DETECT_POLL_S)

    def _poll_device_button(self, timeout: float) -> int | None:
        try:
            factory = self._resolve_factory()
        except (ImportError, OSError) as exc:
            logger.debug("3D Mouse detect unavailable: %s", exc)
            return None
        device = self._open_device(factory)
        if device is None:
            return None
        try:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    state = device.read()
                except OSError:
                    return None
                if state is not None:
                    buttons = tuple(int(bool(b)) for b in (getattr(state, "buttons", None) or ()))
                    for idx, pressed in enumerate(buttons):
                        if pressed:
                            return idx
                time.sleep(_DETECT_POLL_S)
            return None
        finally:
            _safe_close(device)

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
        fader_signal = 0.0
        for axis in MOUSE3D_AXES:
            raw = getattr(snap, _AXIS_SOURCE[axis])
            if getattr(cfg, f"invert_{axis}"):
                raw = -raw
            deadzone = float(getattr(cfg, f"deadzone_{axis}"))
            shaped = self._shape(raw, deadzone, cfg.curve) * float(getattr(cfg, f"sens_{axis}"))
            target = getattr(cfg, f"map_{axis}")
            if target == "x":
                vx += shaped
            elif target == "y":
                vy += shaped
            elif target == "z":
                vz += shaped
            elif target == "speed":
                speed_signal += shaped
            elif target == "fader":
                fader_signal += shaped
            # "none" -> ignore

        speed_steps = self._accumulate_speed(speed_signal, dt)
        edges = self._button_edges(snap.buttons)
        return Mouse3DUpdate(
            velocity=(vx, vy, vz),
            fader_signal=fader_signal,
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

    def _shape(self, value: float, deadzone: float, curve: str) -> float:
        """Per-axis deadzone + response curve (shared with the gamepad)."""
        return shape_axis(value, deadzone, curve)

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

    def _run(self, stop: threading.Event) -> None:
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
        while not stop.is_set():
            device = self._open_device(factory)
            if device is None:
                with self._lock:
                    self._connected = False
                if stop.wait(backoff):
                    break
                backoff = min(backoff * 2.0, _RECONNECT_MAX_S)
                continue
            with self._lock:
                self._connected = True
            try:
                read_any = self._pump(device, stop)
            finally:
                with self._lock:
                    self._connected = False
                    # Don't serve the last deflection on the next connect.
                    self._snapshot = None
                _safe_close(device)
            # Reset the backoff only once a connection actually delivered a
            # reading; an open that never reads (immediate read error) backs off
            # like a failed open so a flaky device can't drive a tight reopen loop.
            if read_any:
                backoff = _RECONNECT_MIN_S
            elif stop.wait(backoff):
                break
            else:
                backoff = min(backoff * 2.0, _RECONNECT_MAX_S)

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
        except (OSError, RuntimeError) as exc:
            # hidraw permission denied (OSError) or "no connected/supported
            # device" (pyspacemouse raises RuntimeError) – both retryable, so the
            # reconnect loop keeps polling instead of dying.
            logger.debug("3D Mouse open failed: %s", exc)
            return None
        # An opener returns a falsy value when no device is present.
        return device or None

    def _pump(self, device: Any, stop: threading.Event) -> bool:
        """Pump device readings into ``_snapshot`` until stop or disconnect.

        Returns whether at least one reading was delivered, so the caller can
        tell a healthy connection (reset backoff) from an open that immediately
        failed to read (back off instead of tight-looping).
        """
        read_any = False
        while not stop.is_set():
            try:
                state = device.read()
            except OSError as exc:
                logger.info("3D Mouse read failed (disconnect?): %s", exc)
                return read_any
            if state is None:
                if stop.wait(_IDLE_POLL_S):
                    return read_any
                continue
            read_any = True
            snapshot = _Snapshot(
                x=_finite_axis(getattr(state, "x", 0.0)),
                y=_finite_axis(getattr(state, "y", 0.0)),
                z=_finite_axis(getattr(state, "z", 0.0)),
                roll=_finite_axis(getattr(state, "roll", 0.0)),
                pitch=_finite_axis(getattr(state, "pitch", 0.0)),
                yaw=_finite_axis(getattr(state, "yaw", 0.0)),
                buttons=tuple(int(bool(b)) for b in (getattr(state, "buttons", None) or ())),
            )
            # A stop may have arrived during the read. Don't publish a now-stale
            # reading: a non-blocking ``stop()`` clears the snapshot and relies on
            # the stopping worker not writing one back before it exits.
            if stop.is_set():
                return read_any
            with self._lock:
                self._snapshot = snapshot
            if stop.wait(_POLL_S):
                return read_any
        return read_any


class Mouse3DManager:
    """Owns one :class:`Mouse3DHandler` per connected 3D Mouse.

    A background supervisor thread re-enumerates connected pucks (hotplug) and
    keys a handler per stable hidraw path. Devices are ordered by sorted path –
    the plug-order analog – so each connected puck's position is its local index;
    the InputManager turns that into a unified controller slot, mirroring
    gamepads. The axis/button mapping is shared across every puck (one
    ``Mouse3DConfig``); each puck drives the marker at its slot.
    """

    def __init__(self, config: Mouse3DConfig, *, backend: Mouse3DBackend | None = None) -> None:
        self._cfg = config
        self._backend = backend
        self._lock = threading.Lock()
        self._detect_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # Ordered by sorted path; keyed handlers open one node each.
        self._infos: list[Mouse3DDeviceInfo] = []
        self._handlers: dict[str, Mouse3DHandler] = {}
        self._available = backend is not None or not check_mouse3d_dependencies()

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Start the enumeration supervisor (idempotent, no-op when disabled)."""
        if self._thread is not None and self._thread.is_alive():
            return
        if not self._cfg.enabled:
            return
        if self._backend is None and check_mouse3d_dependencies():
            with self._lock:
                self._available = False
            return
        stop = threading.Event()
        self._stop = stop
        self._thread = threading.Thread(target=self._supervise, args=(stop,), daemon=True, name="Mouse3DMgr")
        self._thread.start()

    def stop(self, *, wait: bool = False) -> None:
        """Stop the supervisor and every per-device handler."""
        self._stop.set()
        thread = self._thread
        self._thread = None
        with self._lock:
            handlers = list(self._handlers.values())
            self._handlers = {}
            self._infos = []
        for handler in handlers:
            handler.stop(wait=wait)
        if wait and thread is not None:
            thread.join(timeout=2.0)

    def reload_config(self, config: Mouse3DConfig) -> None:
        """Swap the shared mapping into the manager and every live handler."""
        with self._lock:
            self._cfg = config
            handlers = list(self._handlers.values())
        for handler in handlers:
            handler.reload_config(config)

    # -- supervisor thread -------------------------------------------------

    def _resolve_backend(self) -> Mouse3DBackend:
        if self._backend is not None:
            return self._backend
        return _PySpaceMouseBackend()

    def _supervise(self, stop: threading.Event) -> None:
        backend = self._resolve_backend()
        with self._lock:
            self._backend = backend
            self._available = True
        while not stop.is_set():
            try:
                infos = backend.enumerate()
            except Exception:  # noqa: BLE001 - a bad enumeration must not kill the loop
                logger.debug("3D Mouse enumeration raised", exc_info=True)
                infos = []
            self._reconcile(backend, infos)
            if stop.wait(_ENUMERATE_INTERVAL_S):
                break

    def _reconcile(self, backend: Mouse3DBackend, infos: list[Mouse3DDeviceInfo]) -> None:
        """Start handlers for new paths, stop handlers for departed ones.

        Dedups by hidraw path (``easyhid`` reports one entry per HID collection,
        so a single puck shows up several times at the same node) and orders by
        path so each connected puck's position is its stable local index.
        """
        deduped: dict[str, Mouse3DDeviceInfo] = {}
        for info in infos:
            if info.path and info.path not in deduped:
                deduped[info.path] = info
        infos = [deduped[path] for path in sorted(deduped)]
        wanted = set(deduped)
        with self._lock:
            to_add = [info for info in infos if info.path not in self._handlers]
            started: list[Mouse3DHandler] = []
            for info in to_add:
                handler = Mouse3DHandler(self._cfg, device_factory=self._opener_for(backend, info.path))
                self._handlers[info.path] = handler
                started.append(handler)
            removed = [self._handlers.pop(path) for path in list(self._handlers) if path not in wanted]
            self._infos = infos
        for handler in started:
            handler.start()
        for handler in removed:
            handler.stop()

    @staticmethod
    def _opener_for(backend: Mouse3DBackend, path: str) -> Callable[[], Any]:
        def _open() -> Any:
            return backend.open(path)

        return _open

    def _connected_ordered(self) -> list[tuple[Mouse3DDeviceInfo, Mouse3DHandler]]:
        """Connected (info, handler) pairs in sorted-path order; position = index."""
        with self._lock:
            pairs = [(info, self._handlers.get(info.path)) for info in self._infos]
        return [(info, handler) for info, handler in pairs if handler is not None and handler.connected]

    # -- status (web/UI) ---------------------------------------------------

    @property
    def available(self) -> bool:
        """True when ``pyspacemouse`` + ``libhidapi`` are usable."""
        with self._lock:
            return self._available

    @property
    def connected(self) -> bool:
        """True when at least one puck is currently open."""
        with self._lock:
            handlers = list(self._handlers.values())
        return any(handler.connected for handler in handlers)

    def connected_indices(self) -> list[int]:
        """Local indices (``0..N-1``, sorted-path order) of open pucks."""
        return list(range(len(self._connected_ordered())))

    def connected_devices(self) -> list[Mouse3DDeviceInfo]:
        """Identity of each open puck, in local-index order."""
        return [info for info, _handler in self._connected_ordered()]

    def latest_button(self) -> int | None:
        """First currently-pressed button across all open pucks (bindings shared)."""
        for _info, handler in self._connected_ordered():
            idx = handler.latest_button()
            if idx is not None:
                return idx
        return None

    def detect_pressed_button(self, timeout: float = _DETECT_TIMEOUT_S) -> int | None:
        """Watch every connected puck for a button press (web Detect bind flow)."""
        if not self._detect_lock.acquire(blocking=False):
            return None
        try:
            handlers = [handler for _info, handler in self._connected_ordered()]
            if handlers:
                return self._poll_snapshot_button(handlers, timeout)
            return self._poll_devices_button(timeout)
        finally:
            self._detect_lock.release()

    def _poll_snapshot_button(self, handlers: list[Mouse3DHandler], timeout: float) -> int | None:
        deadline = time.monotonic() + timeout
        while True:
            for handler in handlers:
                idx = handler.latest_button()
                if idx is not None:
                    return idx
            if time.monotonic() >= deadline:
                return None
            time.sleep(_DETECT_POLL_S)

    def _poll_devices_button(self, timeout: float) -> int | None:
        """One-shot open+poll of every enumerated puck (feature disabled path)."""
        try:
            backend = self._resolve_backend()
            infos = backend.enumerate()
        except Exception:  # noqa: BLE001 - detect is best-effort
            return None
        devices: list[Any] = []
        try:
            for info in infos:
                try:
                    device = backend.open(info.path)
                except (OSError, RuntimeError) as exc:
                    logger.debug("3D Mouse detect open failed: %s", exc)
                    continue
                if device:
                    devices.append(device)
            if not devices:
                return None
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                for device in devices:
                    try:
                        state = device.read()
                    except OSError:
                        continue
                    if state is not None:
                        buttons = tuple(int(bool(b)) for b in (getattr(state, "buttons", None) or ()))
                        for idx, pressed in enumerate(buttons):
                            if pressed:
                                return idx
                time.sleep(_DETECT_POLL_S)
            return None
        finally:
            for device in devices:
                _safe_close(device)

    # -- per-frame consume (GTK thread) ------------------------------------

    def update(self, dt: float) -> dict[int, Mouse3DUpdate]:
        """Per-device results keyed by local index (only connected pucks)."""
        return {idx: handler.update(dt) for idx, (_info, handler) in enumerate(self._connected_ordered())}


def _safe_close(device: Any) -> None:
    close = getattr(device, "close", None)
    if close is None:
        return
    try:
        close()
    except Exception:  # noqa: BLE001 - close is best-effort on teardown
        logger.debug("3D Mouse close raised", exc_info=True)
