# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Runtime services for OpenFollow application.

- WebCommandQueue: thread-safe command queue for web-triggered runtime commands
- AppRuntimeServices: grouped startup and per-frame services for OpenFollowApp
"""

from __future__ import annotations

import copy
import gc
import json
import logging
import os
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import numpy as np

from openfollow.configuration import (
    ControllerButtonTrigger,
    HotkeyTrigger,
)
from openfollow.input import InputManager
from openfollow.otp import OtpServer
from openfollow.psn import PsnReceiver, PsnServer
from openfollow.psn.server import _UNCHANGED, _Unchanged
from openfollow.rttrpm import RttrpmServer
from openfollow.runtime.overlay_state import OverlayState
from openfollow.runtime.services_detection_pin import _NOMINAL_FRAME_DT
from openfollow.runtime.services_detection_pin import (
    apply_detection_pin as apply_detection_pin_helper,
)
from openfollow.runtime.services_frame import (
    prepare_overlay_state_swap,
)
from openfollow.runtime.services_frame import (
    update_video as update_video_helper,
)
from openfollow.runtime.services_marker_visuals import (
    build_initial_overlay_state,
    build_marker_visual_state,
)
from openfollow.runtime_metrics import FrameMetrics, OverlayStatePool
from openfollow.scene.camera import Camera
from openfollow.system_stats import SystemStatsCollector
from openfollow.video.overlay import CairoOverlayRenderer
from openfollow.video.receiver import GstNativeSinkReceiver, gst_runtime_available
from openfollow.window import GtkNativeSinkWindow

if TYPE_CHECKING:
    from collections.abc import Callable

    from openfollow.app import OpenFollowApp
    from openfollow.configuration import (
        AppConfig,
        DetectionConfig,
        OscDestinationsConfig,
        OscTransmittersConfig,
        OtpOutputConfig,
        RttrpmOutputConfig,
    )
    from openfollow.input.midi import StatusFlagValue
    from openfollow.network.adapter import (  # noqa: F401
        ApplyResult,
        Ipv4Config,
        NetworkAdapter,
    )
    from openfollow.privilege import PrivilegeBroker  # noqa: F401
    from openfollow.psn.marker import Marker
    from openfollow.video.detection import PersonDetector
    from openfollow.web import ConfigWebServer  # noqa: F401

logger = logging.getLogger(__name__)


# Cap on the flattened most-recent-first recent-OSC-send list the
# diagnostics bundle shows; bounds output rather than dumping every row's
# full per-row ring.
_RECENT_OSC_SENDS_LIMIT = 100


def _format_lease_remaining(seconds: int | None) -> str | None:
    """Render a DHCP lease's seconds-remaining as a compact human label.

    Returns ``None`` when no lease was surfaced (DHCP not in use, or
    backend didn't report it). Otherwise:

    - ``< 60s``: ``"X s"``.
    - ``< 3600s``: ``"Y min"``.
    - ``< 24h``: ``"Hh Mm"`` (e.g. ``"2h 13m"``).
    - longer: ``"Dd Hh"`` (e.g. ``"3d 04h"``).
    """
    if seconds is None:
        return None
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds // 60} min"
    hours, rem = divmod(seconds, 3600)
    minutes = rem // 60
    if hours < 24:
        return f"{hours}h {minutes:02d}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours:02d}h"


# ---------------------------------------------------------------------------
# Web Command Queue
# ---------------------------------------------------------------------------

# Progress of the detached installer (apply-update.sh), polled by the web UI.
# In the openfollow-owned state dir so this (openfollow-user) process can clear
# it. Keep this path in sync with apply-update.sh's STATE_FILE.
_DETACHED_UPDATE_STATE_FILE = "/var/lib/openfollow/update-state.json"


def _read_detached_update_state() -> dict[str, str] | None:
    """Return the detached installer's status, or None if absent/unreadable."""
    try:
        with open(_DETACHED_UPDATE_STATE_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or "state" not in data:
        return None
    return {
        "state": str(data.get("state", "")),
        "message": str(data.get("message", "")),
        "error": str(data.get("error", "")),
    }


def clear_detached_update_state() -> None:
    """Remove the detached installer's status file (best-effort)."""
    try:
        os.unlink(_DETACHED_UPDATE_STATE_FILE)
    except OSError:
        pass


class WebCommandQueue:
    """Thread-safe command queue for web-triggered runtime commands."""

    def __init__(self) -> None:
        self._restart_requested = threading.Event()
        self._update_requested = threading.Event()
        self._button_detection_requested = threading.Event()
        self._button_detection_cancel_requested = threading.Event()
        self._button_detection_active = threading.Event()
        self._update_lock = threading.Lock()
        self._update_request: dict[str, str] | None = None
        self._update_status: dict[str, str] = {
            "state": "idle",
            "message": "",
            "error": "",
        }
        # Fresh boot: clear a completed/failed detached update.
        clear_detached_update_state()
        # Generic privilege-password prompt. The PrivilegeBroker uses this
        # any time a sudoers grant is missing and a password is needed to
        # elevate. Not tied to the update state machine – any subsystem
        # (network apply, sudoers drop-in install, device repair) can park
        # on it. ``_privilege_request`` is the active prompt payload
        # (capability name, operator-facing reason); the web modal is the
        # sole consumer.
        self._privilege_lock = threading.Lock()
        self._privilege_request: dict[str, str] | None = None
        self._privilege_password_event = threading.Event()
        self._privilege_password: str | None = None
        self._privilege_password_cancelled = False
        # Detection install/uninstall job state. The web section
        # auto-refreshes every second while ``state == "running"``.
        # ``state`` transitions: idle → running → success | error → idle
        # (back to idle on the render after the operator sees the terminal
        # state).
        self._detection_install_lock = threading.Lock()
        self._detection_install_status: dict[str, str] = {
            "state": "idle",
            "extra": "",
            "action": "",
            "message": "",
            "tail": "",
        }

    def request_restart(self) -> None:
        self._restart_requested.set()

    def consume_restart_requested(self) -> bool:
        if self._restart_requested.is_set():
            self._restart_requested.clear()
            return True
        return False

    def request_button_detection(self) -> None:
        self._button_detection_requested.set()

    def consume_button_detection_requested(self) -> bool:
        if self._button_detection_requested.is_set():
            self._button_detection_requested.clear()
            return True
        return False

    def request_button_detection_cancel(self) -> None:
        """Web-triggered cancel of the in-progress wizard. The main loop
        drains this and calls ``exit_button_detection`` so a keyboardless
        operator isn't stranded once the wizard has grabbed exclusive input."""
        self._button_detection_cancel_requested.set()

    def consume_button_detection_cancel_requested(self) -> bool:
        if self._button_detection_cancel_requested.is_set():
            self._button_detection_cancel_requested.clear()
            return True
        return False

    def set_button_detection_active(self, active: bool) -> None:
        if active:
            self._button_detection_active.set()
        else:
            self._button_detection_active.clear()

    def is_button_detection_active(self) -> bool:
        return self._button_detection_active.is_set()

    def request_deb_update(self, service_name: str) -> bool:
        """Queue a GitHub-release .deb update. Returns False if another update is running."""
        return self._queue_update_request("deb", service_name=service_name)

    def request_local_update(self, service_name: str, *, deb_path: str | None = None) -> bool:
        """Queue an offline install of an operator-uploaded ``.deb``.

        Returns False if another update is running (same in-progress guard as
        :meth:`request_deb_update`).
        """
        return self._queue_update_request("deb-local", service_name=service_name, deb_path=deb_path or "")

    def _queue_update_request(self, kind: str, **fields: str) -> bool:
        """Shared guard and enqueue logic for all update kinds."""
        with self._update_lock:
            if self._update_status.get("state", "idle") in {"queued", "running", "restarting"}:
                return False
            # Clear the detached installer's status file only once we've
            # committed to queueing – a rejected duplicate must not wipe a
            # live installer's progress.
            clear_detached_update_state()
            self._update_request = {
                "kind": kind,
                "service_name": (fields.get("service_name") or "").strip() or "openfollow",
                **{k: v for k, v in fields.items() if k != "service_name"},
            }
            self._update_status = {"state": "queued", "message": "Update queued.", "error": ""}
            self._update_requested.set()
            return True

    def consume_update_requested(self) -> dict[str, str] | None:
        """Consume and return pending update request payload, if any."""
        with self._update_lock:
            if not self._update_requested.is_set():
                return None
            self._update_requested.clear()
            request = self._update_request
            self._update_request = None
            if request is None:
                return None
            return dict(request)

    def set_update_status(self, state: str, message: str = "", error: str = "") -> None:
        """Update status shown in the web UI."""
        with self._update_lock:
            self._update_status = {
                "state": state,
                "message": message,
                "error": error,
            }

    def get_update_status(self) -> dict[str, str]:
        """Return update status.

        While a detached installer is expected (in-memory state
        ``restarting``), its on-disk status file wins so the root-owned
        installer's progress/failure surfaces. Gating on ``restarting``
        keeps a stale terminal file from a prior run from shadowing a
        fresh in-memory status.
        """
        with self._update_lock:
            status = dict(self._update_status)
        if status.get("state") == "restarting":
            detached = _read_detached_update_state()
            if detached is not None:
                return detached
        return status

    # ----- generic privilege password prompt -----------

    def request_privilege_password(
        self,
        *,
        reason: str,
        capability_name: str,
    ) -> bool:
        """Park a new privilege-password prompt for the operator.

        Returns False when another prompt is already in flight (callers
        serialise on a single in-flight prompt; two concurrent workers
        racing would confuse the operator). On success, the web modal
        renders the prompt on its next poll.
        """
        with self._privilege_lock:
            if self._privilege_request is not None:
                return False
            self._privilege_request = {
                "reason": reason,
                "capability_name": capability_name,
            }
            # Reset the result slot so a stale prior submit can't
            # leak into this prompt.
            self._privilege_password = None
            self._privilege_password_cancelled = False
            self._privilege_password_event.clear()
            return True

    def pending_privilege_password_request(self) -> dict[str, str] | None:
        """Snapshot of the active prompt (or ``None`` when idle).

        Returned dict is a copy so callers can render from it without
        risking a concurrent clear from the consume path.
        """
        with self._privilege_lock:
            if self._privilege_request is None:
                return None
            return dict(self._privilege_request)

    def submit_privilege_password(self, password: str) -> None:
        """Hand the operator-typed password to the parked worker."""
        with self._privilege_lock:
            self._privilege_password = password
            self._privilege_password_cancelled = False
            self._privilege_password_event.set()

    def cancel_privilege_password(self) -> None:
        """Operator dismissed the modal. Wake the worker so it can
        surface a clean cancellation error instead of timing out."""
        with self._privilege_lock:
            self._privilege_password = None
            self._privilege_password_cancelled = True
            self._privilege_password_event.set()

    def consume_privilege_password(self, timeout: float) -> str | None:
        """Block until submit/cancel/timeout. Returns the password on
        success, ``None`` on cancel/timeout.

        Clears ``_privilege_request`` after consumption so the next
        :func:`pending_privilege_password_request` poll sees idle
        state – both UI surfaces then dismiss the prompt overlay.
        """
        signaled = self._privilege_password_event.wait(timeout=timeout)
        with self._privilege_lock:
            # Race window: ``wait`` returned False but the producer
            # set the event before we acquired the lock. Re-check
            # under the lock so a late submission isn't dropped.
            if not signaled and self._privilege_password_event.is_set():
                signaled = True
            if not signaled:
                # Timeout – clear the active prompt so the UI dismisses
                # the modal. The caller of ``consume`` raises a clean
                # "cancelled or timed out" PrivilegeError.
                self._privilege_request = None
                return None
            password = self._privilege_password
            cancelled = self._privilege_password_cancelled
            self._privilege_password = None
            self._privilege_password_cancelled = False
            self._privilege_password_event.clear()
            self._privilege_request = None
        if cancelled:
            return None
        return password

    # ----- detection install/uninstall -------------------------------

    def try_claim_detection_install(
        self,
        *,
        action: str,
        extra: str,
        message: str = "",
    ) -> bool:
        """Atomically transition the detection-install slot to ``running``.

        Returns True when the caller claimed the slot and should kick off
        the worker; False when another install is already in flight.
        Combines lock acquisition + state mutation so a double-click can't
        race two threads past the same check.

        Rejects only the ``running`` state. A new claim is permitted
        directly from ``success`` / ``error``: the polling endpoint
        dismisses terminal states within ~1 s, and a fresh claim before
        that tick is itself an implicit dismissal of the prior result.
        """
        with self._detection_install_lock:
            if self._detection_install_status.get("state") == "running":
                return False
            self._detection_install_status = {
                "state": "running",
                "extra": extra,
                "action": action,
                "message": message,
                "tail": "",
            }
            return True

    def set_detection_install_status(
        self,
        *,
        state: str,
        message: str = "",
        tail: str = "",
        extra: str | None = None,
        action: str | None = None,
    ) -> None:
        """Update the detection install/uninstall job status.

        The worker thread calls this to publish its terminal state
        (``success`` / ``error``) plus the trailing pip output for the UI
        tail block. ``extra`` and ``action`` default to whatever the
        running job recorded – except when transitioning to ``idle``,
        where omitted values reset to empty so the post-dismissal snapshot
        matches the initial-state shape instead of carrying stale ids.
        """
        with self._detection_install_lock:
            current = self._detection_install_status
            default_extra = "" if state == "idle" else current.get("extra", "")
            default_action = "" if state == "idle" else current.get("action", "")
            self._detection_install_status = {
                "state": state,
                "extra": extra if extra is not None else default_extra,
                "action": action if action is not None else default_action,
                "message": message,
                "tail": tail,
            }

    def get_detection_install_status(self) -> dict[str, str]:
        """Return a copy of detection install/uninstall job status."""
        with self._detection_install_lock:
            return dict(self._detection_install_status)


# ---------------------------------------------------------------------------
# Application Runtime Services
# ---------------------------------------------------------------------------


class AppRuntimeServices:
    """Grouped startup and per-frame services for OpenFollowApp."""

    def __init__(self, app: OpenFollowApp) -> None:
        self._app = app
        self._shutdown_in_progress = False
        self._is_pi = self._is_raspberry_pi()
        # Central registry of input-binding ownership, pre-populated with
        # the movement-key reservations under owner "system:movement".
        from openfollow.configuration import RESERVED_MOVEMENT_KEYS
        from openfollow.input.conflicts import default_registry

        self._conflict_registry = default_registry(RESERVED_MOVEMENT_KEYS)
        self._overlay_renderer: CairoOverlayRenderer | None = None  # set in init_video
        self._system_stats: SystemStatsCollector | None = None  # set in init_video
        self._person_detector: PersonDetector | None = None  # set in init_video if enabled

        # Object pool for OverlayState to reduce GC pressure.
        self._overlay_state_pool = OverlayStatePool(pool_size=3)
        self._old_overlay_state: OverlayState | None = None

        self._frame_metrics = FrameMetrics()
        self._frame_start_time = 0.0
        self._runtime_stats_interval = 0.25
        self._last_runtime_stats_publish = 0.0
        self._runtime_stats_lock = threading.Lock()
        self._runtime_stats_snapshot: dict[str, Any] = self._default_runtime_stats_snapshot()
        # Cache the detection-deps probe so the 4Hz stats loop doesn't call
        # importlib.util.find_spec() on every publish; invalidated by TTL and
        # by any change to the detection config signature.
        self._detection_deps_cache: list[str] | None = None
        self._detection_deps_cache_signature: str | None = None
        self._detection_deps_cache_at: float = 0.0
        self._detection_deps_cache_ttl = 5.0

        self._setup_gc_tuning()

        # Pre-allocated NumPy buffers (reduce array allocation churn)
        self._cam_params_buffer = np.zeros(7, dtype=np.float64)
        self._unproject_cam_buffer = np.zeros(7, dtype=np.float64)
        self._screen_point_buffer = np.zeros((1, 2), dtype=np.float64)

        # Unified OSC service: one shared client cache and listener for
        # zone OSC output, marker OSC input, and the transmitter system.
        # Created eagerly because the constructor opens no sockets – all
        # I/O happens on first send / listener start.
        from openfollow.operator_messages import OperatorMessageStore
        from openfollow.osc.service import OscService
        from openfollow.osc.transmitter import OscTransmitterManager
        from openfollow.zones.engine import ZoneEngine

        self._osc_service: OscService = OscService()
        # Store for OSC-driven operator messages; written by the OSC adapter,
        # read by the overlay builder. Created eagerly – it owns no I/O.
        self._operator_message_store = OperatorMessageStore()
        self._osc_transmitter_manager: OscTransmitterManager | None = None
        self._zone_engine: ZoneEngine | None = None
        self._last_zone_eval: float = 0.0
        # Reused buffers for detection-to-world unprojection during zone eval
        self._zone_cam_buffer = np.zeros(7, dtype=np.float64)
        self._zone_screen_buffer = np.zeros((1, 2), dtype=np.float64)

        # Shared status flags surfaced by the operator-screen badge. A
        # subsystem writes, per key, one of:
        #   * a ``str``                          -> "error" row (red),
        #   * a ``(severity, message)`` tuple, ``severity`` =
        #     ``"error"`` (red) / ``"info"`` (green),
        #   * ``None`` to clear the condition.
        # The badge consumer filters falsy values. Created eagerly so
        # subsystem constructors can write into it before init_* runs.
        self._status_flags: dict[str, StatusFlagValue] = {}

        # MIDI subsystem. Constructed eagerly (no I/O); ``init_midi``
        # opens listener ports for each configured alias later.
        from openfollow.input.midi import MidiSubsystem

        self._midi: MidiSubsystem = MidiSubsystem(
            status_flags=self._status_flags,
        )

        # Virtual fader bus: eight normalised 0-1 faders bridging gamepad
        # / MIDI / unsourced inputs to downstream consumers. Constructed
        # eagerly with the persisted config so subscribers can register
        # before init runs and reads see coherent defaults from frame one.
        from openfollow.input.faders import VirtualFaderBus

        self._virtual_faders: VirtualFaderBus = VirtualFaderBus(
            faders_config=self._app._config.virtual_faders,
        )

        # MIDI → virtual-fader dispatch: bridges the MIDI event stream
        # onto the fader bus for any fader with ``source_kind == "midi"``.
        # The subscription is wired in ``init_midi`` so it tracks the
        # subsystem lifecycle and re-attaches idempotently on hot-reload.
        from openfollow.input.fader_dispatch import MidiFaderDispatcher

        self._midi_fader_dispatcher: MidiFaderDispatcher = MidiFaderDispatcher(self._virtual_faders)

        # MIDI Learn broker for the OSC binding form's Capture button.
        # Constructed eagerly so the route layer can read the idle state
        # from the first request without a startup race; arms a one-shot
        # subscription on each Capture click.
        from openfollow.web.midi_capture import MidiCaptureBroker

        self._midi_capture_broker: MidiCaptureBroker = MidiCaptureBroker(
            self._midi,
        )

        # Privilege broker: owns the ``sudo -n -l`` probe + per-action
        # password prompt for every subsystem that needs root. Created
        # before the network adapter so adapter constructors can take it.
        # The prompter is wired to the WebCommandQueue's
        # ``request_privilege_password`` slot; the web modal is the only
        # surface that consumes it.
        from openfollow.privilege import PrivilegeBroker
        from openfollow.privilege.broker import Prompter
        from openfollow.privilege.capabilities import Capability

        web_commands = getattr(app, "_web_commands", None)

        def _prompt_for_password(capability: Capability, reason: str) -> str | None:
            # The ``web_commands is None`` case sets ``prompter=None`` at
            # the broker constructor below, so this closure only runs when
            # web_commands is real; the assert narrows for mypy.
            assert web_commands is not None
            if not web_commands.request_privilege_password(
                reason=reason,
                capability_name=capability.name,
            ):
                # Another prompt is already in flight. Refuse rather than
                # blocking on a queue serving a different worker – the
                # operator would otherwise see the wrong reason text.
                return None
            password: str | None = web_commands.consume_privilege_password(
                timeout=300.0,
            )
            return password

        self._privilege_broker = PrivilegeBroker(
            prompter=_prompt_for_password if web_commands is not None else None,
        )
        _ = Prompter  # keep import alive for typing.TYPE_CHECKING readers

        # Network adapter: auto-detect NM / dhcpcd / psutil at startup.
        # Lazy import so the optional subprocess probing runs once per
        # app launch.
        from openfollow.network.detect import select_adapter as _select_network_adapter

        backend_choice = self._network_backend_choice(app)
        self._network_adapter = _select_network_adapter(
            backend_choice,
            broker=self._privilege_broker,
        )
        # Serialise web-driven apply / renew so two concurrent requests
        # can't race the host's network state.
        self._network_op_lock = threading.Lock()

        if not gst_runtime_available():
            logger.critical("GStreamer not available. Native sink mode requires GStreamer.")
            sys.exit(1)

    @property
    def network_adapter(self) -> NetworkAdapter:
        """Host-network adapter (NM / dhcpcd / psutil)."""
        return self._network_adapter

    @property
    def privilege_broker(self) -> PrivilegeBroker:
        """Per-capability sudo probe + on-demand password prompt."""
        return self._privilege_broker

    @staticmethod
    def _network_backend_choice(app: OpenFollowApp) -> str:
        """Read ``[network] backend`` from config, default ``"auto"``."""
        cfg = getattr(app, "_config", None)
        if cfg is None:
            return "auto"
        network_section = getattr(cfg, "network", None)
        if network_section is None:
            return "auto"
        return str(getattr(network_section, "backend", "auto") or "auto")

    @staticmethod
    def _is_raspberry_pi() -> bool:
        for path in (
            Path("/proc/device-tree/model"),
            Path("/sys/firmware/devicetree/base/model"),
            Path("/proc/cpuinfo"),
        ):
            try:
                text = path.read_text(encoding="utf-8", errors="ignore").strip("\x00").lower()
            except OSError:
                continue
            if "raspberry pi" in text:
                return True
        return False

    @staticmethod
    def _setup_gc_tuning() -> None:
        """Configure Python GC for real-time performance.

        Raises the gen-0 threshold from the default 700 to 2000
        allocations, trading rarer GC runs for slightly longer pauses.
        """
        current_threshold = gc.get_threshold()
        gc.set_threshold(2000, 15, 15)
        new_threshold = gc.get_threshold()
        logger.info(
            "GC tuning: threshold increased from %s to %s",
            current_threshold,
            new_threshold,
        )

    def init_canvas(self) -> None:
        cfg = self._app._config
        win = GtkNativeSinkWindow(
            max(1, int(cfg.window_width)),
            max(1, int(cfg.window_height)),
        )
        if self._is_pi:
            win.fullscreen()
        self._app._canvas = win

        # Hide the pointer at startup unless mouse input is enabled. A
        # desktop compositor would otherwise park a cursor over the video
        # with no mouse attached; Cage draws none, so it's a no-op there.
        win.set_pointer_base_visible(cfg.controller.mouse_enabled)

        self._apply_window_title(cfg.psn_system_name)

        self._app._canvas.add_event_handler(self._app._on_key_down, "key_down")
        self._app._canvas.add_event_handler(self._app._on_key_up, "key_up")
        self._app._canvas.add_event_handler(self._app._on_wheel, "wheel")
        self._app._canvas.add_event_handler(self._app._on_resize, "resize")
        self._app._canvas.add_event_handler(self._app._on_pointer_down, "pointer_down")
        self._app._canvas.add_event_handler(self._app._on_pointer_move, "pointer_move")
        self._app._canvas.add_event_handler(self._app._on_pointer_up, "pointer_up")
        self._app._canvas.add_event_handler(self._app._on_close, "close")
        self._app._canvas.add_event_handler(self._app._on_blur, "blur")

    def _apply_window_title(self, title: str) -> None:
        window_title = title.strip() or "OpenFollow"
        canvas = self._app._canvas
        if canvas is not None and hasattr(canvas, "set_title"):
            canvas.set_title(window_title)

    def init_camera(self) -> None:
        self._app._camera = Camera.from_config(self._app._config.camera)

    def _seed_bundled_detection_models(self, cfg: AppConfig) -> None:
        """Copy bundled tier models into the resolved storage ``models/`` folder.

        No-op when nothing is bundled (a source checkout) or on any I/O error –
        seeding is best-effort and must never block startup.
        """
        from openfollow.model_seed import bundled_models_dir, seed_bundled_models
        from openfollow.video.detection import resolve_detection_storage_path

        source = bundled_models_dir()
        if source is None:
            return
        storage_root = Path(resolve_detection_storage_path(cfg.detection.storage_path))
        seed_bundled_models(source, storage_root / "models")

    def init_video(self) -> None:
        overlay = CairoOverlayRenderer()
        self._overlay_renderer = overlay
        self._init_overlay_state(overlay)

        # System stats collector (CPU, RAM, temperature). Pass the
        # resolved-source-IP as a callable, not a literal: ``init_video``
        # runs BEFORE the startup ``wait_for_source_ip``, so a literal
        # would often capture ``""`` (DHCP pending) or a transient fallback
        # and stick with it for the whole session. The lazy form
        # re-resolves on each ~1 s update tick.
        self._system_stats = SystemStatsCollector(
            update_interval=1.0,
            preferred_ip=self._resolved_source_ip,
        )

        canvas = self._app._canvas
        # ``init_canvas`` runs before ``init_video``, so canvas is non-None
        # at runtime; the assert is a strict-mode narrowing aid.
        assert canvas is not None, "init_canvas must run before init_video"
        cfg = self._app._config

        # Seed the pre-shipped quality-tier models into the storage folder so
        # detection (and the web tier picker) works offline out of the box.
        # No-op on a source checkout. Runs before the web server starts.
        self._seed_bundled_detection_models(cfg)

        # Optional person detection (lazy import, zero cost when disabled)
        detector = None
        if cfg.detection.enabled:
            from openfollow.video.detection import (
                PersonDetector,
                check_detection_dependencies,
            )

            missing = check_detection_dependencies(cfg.detection)
            if missing:
                logger.warning(
                    "Person detection enabled but required packages are missing: %s. "
                    "Install with: pip install openfollow[detection]",
                    ", ".join(missing),
                )
            else:
                detector = PersonDetector(cfg.detection)

        # Video preview for web UI
        from openfollow.video.preview import PreviewProvider, SnapshotProvider

        self._preview_provider = PreviewProvider()
        self._snapshot_provider = SnapshotProvider()

        # Build input-specific config dict from the active plugin
        from openfollow.video.inputs import get_input_class

        input_cls = get_input_class(cfg.video_source_type)
        input_config = input_cls.get_config_field_values(cfg) if input_cls else {}

        receiver = GstNativeSinkReceiver(
            source_type=cfg.video_source_type,
            input_config=input_config,
            overlay_renderer=overlay,
            on_widget_changed=canvas.embed_widget,
            config_path=self._app._config_path,
            detector=detector,
            preview_provider=self._preview_provider,
            snapshot_provider=self._snapshot_provider,
            stall_timeout=cfg.stall_timeout,
            heal_interval=cfg.heal_interval,
        )

        # Create pipeline - may fail if no source, but receiver handles this gracefully
        try:
            receiver.create_pipeline()
            widget = receiver.get_sink_widget()
            if widget is not None:
                canvas.embed_widget(widget)
        except Exception as e:
            logger.warning("Failed to create video pipeline: %s", e)

        # Drive HUD redraws from the GTK display tick rather than from
        # cairooverlay buffer flow so a stalled (e.g. NDI 0 fps) source
        # cannot freeze the HUD.
        if hasattr(canvas, "attach_hud"):
            canvas.attach_hud(overlay.draw)

        self._app._video_receiver = receiver
        # Critical phase: a raise skips shutdown(), so tear the already-started
        # receiver/detector down before re-raising to avoid leaking threads.
        try:
            receiver.start()
            self._app._video_logged = False

            # Adopt the detector only once the receiver is up; the receiver
            # holds its own ``detector`` ref and tears it down on ``stop()``.
            self._person_detector = detector
            if self._person_detector is not None:
                self._person_detector.start()
        except Exception:
            try:
                receiver.stop()
            except Exception:
                logger.exception("Video receiver stop after failed init raised.")
            self._app._video_receiver = None
            self._person_detector = None
            raise

    def _init_overlay_state(self, overlay: CairoOverlayRenderer) -> None:
        """Populate initial overlay state from config."""
        state = build_initial_overlay_state(self._app._config)
        overlay.state = state

    def _resolved_source_ip(self) -> str:
        """Return the concrete IP to bind PSN/marker-sync sockets to.

        Central resolution point for the iface-pin model. Resolves the
        active ``psn_source_iface`` with fallback enabled so a stale pin
        never disables PSN: when the pinned interface isn't live, the
        auto-detected primary wins and the app keeps running.
        ``init_psn`` / ``init_psn_receiver`` / the marker-catalog sync
        all route through here so they agree on the same validated IP.
        """
        from openfollow.net_utils import resolve_source_ip

        resolved, _status = resolve_source_ip(
            self._app._config.psn_source_iface,
        )
        return resolved

    def _resolve_web_bind(self) -> str:
        """Resolve the web UI listen address.

        ``web_bind`` set → that explicit address. Empty → ``0.0.0.0`` (all
        interfaces) so the UI stays reachable across an interface IP change
        without a restart. When ``web_pin`` is set, access is gated by session
        auth plus the CSRF / DNS-rebind guards; with it empty those are
        disabled. Set ``web_bind`` to pin the UI to a single address.
        """
        return self._app._config.web_bind or "0.0.0.0"

    def init_psn(self) -> None:
        source_ip = self._resolved_source_ip()
        server = PsnServer(
            system_name=self._app._config.psn_system_name,
            mcast_ip=self._app._config.psn_mcast_ip,
            source_ip=source_ip,
        )
        # Assign only after start() succeeds: a failed start must leave
        # ``_server`` None so the dependent init group is skipped, not run
        # against a server whose send threads never came up.
        try:
            server.start()
        except Exception:
            try:
                server.stop()
            except Exception:
                logger.exception("PSN server stop after failed start raised.")
            raise
        self._app._server = server

    def init_markers(self) -> None:
        # Defense in depth against loader bypass (test fixture, in-place
        # config edit). ``load_config`` already drops id 0 (the reserved
        # "ignored" id on the PSN wire); without this, 0 would reach
        # ``Marker.__init__`` and raise at startup.
        #
        # ``bool`` rejected explicitly: ``bool`` is an ``int`` subclass,
        # so ``controlled_marker_ids = [True]`` would pass ``tid >= 1``
        # (``True >= 1``) and then crash ``Marker.__init__``.
        #
        # Dedup preserving first-seen order, matching the ``load_config``
        # and ``/api/markers/selection`` normalisations. Duplicates would
        # render extra cards and double-register with PSN / RTTrPM, since
        # ``build_marker_visual_state`` iterates these lists directly.
        def _normalise(ids: list[Any]) -> list[int]:
            seen: set[int] = set()
            out: list[int] = []
            for tid in ids:
                if not (isinstance(tid, int) and not isinstance(tid, bool) and tid >= 1):
                    continue
                if tid in seen:
                    continue
                seen.add(tid)
                out.append(tid)
            return out

        self._app._controlled_ids = _normalise(self._app._config.controlled_marker_ids)
        self._app._viewer_ids = _normalise(self._app._config.viewer_marker_ids)
        tc = self._app._config.marker
        default_pos = (tc.default_pos_x, tc.default_pos_y, tc.default_pos_z)
        # ``init_psn`` runs before ``init_markers``; narrow once for the
        # strict checker instead of guarding every loop.
        server = self._app._server
        assert server is not None, "init_psn must run before init_markers"
        catalog = getattr(self._app, "_marker_catalog", None)
        for tid in self._app._controlled_ids:
            entry = catalog.get(tid) if catalog is not None else None
            name = entry.name if (entry is not None and entry.name) else f"Marker {tid}"
            marker = server.add_marker(tid, name)
            marker.set_pos(*default_pos)
        self._app._selected_id = self._app._controlled_ids[0] if self._app._controlled_ids else None

    @staticmethod
    def _resolved_otp_source_ip(cfg: OtpOutputConfig) -> str:
        """Resolve the OTP output's pinned interface to a concrete bind IP.

        Mirrors PSN (``_resolved_source_ip``): a pinned interface that's down –
        or unset – falls back to the primary interface so a stale pin never
        silently stalls multicast output. Empty result lets the OS pick.
        """
        from openfollow.net_utils import resolve_source_ip

        resolved, status = resolve_source_ip(cfg.source_iface, fallback=True)
        if cfg.source_iface and status != "iface":
            logger.warning(
                "Configured otp_output.source_iface '%s' is unavailable; "
                "falling back to %s. OTP multicast may use the wrong interface.",
                cfg.source_iface,
                resolved or "OS default",
            )
        return resolved

    def init_otp(self) -> None:
        cfg = self._app._config.otp_output
        if not cfg.enabled:
            return
        self._app._otp_server = OtpServer(
            system_name=self._app._config.psn_system_name,
            system_number=cfg.system_number,
            port=cfg.port,
            source_ip=self._resolved_otp_source_ip(cfg),
            priority=cfg.priority,
        )
        server = self._app._server
        # Share Marker objects with PsnServer – no duplicated state.
        # ``init_psn`` runs before ``init_otp``, so the None arm is
        # unreachable; the guard exists only for the strict checker.
        if server is not None:  # pragma: no branch
            for tid in self._app._controlled_ids:
                marker = server.get_marker(tid)
                if marker is not None:
                    self._app._otp_server.register_marker(marker)
        self._app._otp_server.start()

    def init_rttrpm(self) -> None:
        cfg = self._app._config.rttrpm_output
        if not cfg.enabled:
            return
        self._app._rttrpm_server = RttrpmServer(
            host=cfg.host,
            port=cfg.port,
            fps=float(cfg.fps),
            context=cfg.context,
        )
        server = self._app._server
        # Same lifecycle guarantee as ``init_otp``; ``init_psn`` runs first.
        if server is not None:  # pragma: no branch
            for tid in self._app._controlled_ids:
                marker = server.get_marker(tid)
                if marker is not None:
                    self._app._rttrpm_server.register_marker(marker)
        self._app._rttrpm_server.start()

    def init_psn_receiver(self) -> None:
        # Read the resolved IP (iface pin first, then explicit IP, then
        # auto-detect) so the receiver binds to the same interface as the
        # server. Reading ``psn_source_ip`` directly would skip iface-pin.
        self._app._psn_receiver = PsnReceiver(
            ignore_ids=self._app._controlled_ids,
            source_ip=self._resolved_source_ip(),
        )
        self._app._psn_receiver.start()

    def init_virtual_faders(self) -> None:
        """Re-apply the persisted virtual-fader config and provision the
        per-controlled-marker faders (one per id in
        ``controlled_marker_ids``).

        The bus was already constructed in ``__init__``, so re-applying
        config is a no-op on a clean boot; hot-reload reuses this path.
        ``init_markers`` runs earlier so ``_controlled_ids`` is populated.
        """
        self._virtual_faders.apply_config(self._app._config.virtual_faders)
        self._virtual_faders.provision_marker_faders(
            self._app._controlled_ids,
        )

    def init_midi(self) -> None:
        """Open MIDI listener ports for each configured patch.

        The :class:`MidiSubsystem` was constructed in ``__init__`` so
        subscribers can register before init runs; this opens the actual
        rtmidi ports. ``apply_config`` does the discover + match + open
        work and writes the ``midi_patch_missing`` status flag for any
        patch whose target device isn't connected.

        Re-callable on hot-reload (``apply_config`` is idempotent – same
        patch on the same port is a no-op).
        """
        self._midi.apply_config(self._app._config.midi.patches)
        # (Re)subscribe the MIDI → fader dispatcher. ``attach`` drops any
        # previous subscription first, so reload passes can't leak
        # duplicate callbacks.
        self._midi_fader_dispatcher.attach(self._midi)

    def init_osc_transmitters(self) -> None:
        """Spin up the OSC transmitter manager.

        The manager always exists once initialised – even with zero rows
        – because the rest of the lifecycle (hot-reload, shutdown) assumes
        a non-None reference. Skipping construction when the config has no
        rows would force a later row-add to bootstrap from scratch.
        """
        from openfollow.osc.transmitter import OscTransmitterManager

        manager = OscTransmitterManager(
            osc_service=self._osc_service,
            marker_provider=self._marker_provider,
            grid_provider=self._grid_provider,
            # Fader placeholders resolve through the bus. The MIDI / fader
            # subscriptions are attached just below (after ``restart``
            # populates the row list) so an early event never arrives at
            # an empty manager. The MIDI subsystem and fader bus were
            # constructed eagerly in ``__init__``, so attaching here is
            # safe – unlike the input event bus, attached later in
            # :meth:`init_input_manager` because ``InputManager`` doesn't
            # exist yet.
            fader_provider=self._fader_provider,
            marker_fader_provider=self._marker_fader_provider,
            # ``:cN`` controller reference. Reads ``_input_manager``
            # lazily – it's constructed after this manager (see
            # :meth:`init_input_manager`), so the closure tolerates ``None``
            # until it's populated, long before any 60 Hz render.
            controller_marker_provider=self._controller_marker_provider,
            # ``markers`` field's ``all`` token + controlled-only validity
            # filter. Reads the live controlled-id list each tick so a
            # hot-reload of ``controlled_marker_ids`` re-expands ``all``
            # without a manager restart.
            controlled_markers_provider=self._controlled_markers_provider,
        )
        manager.restart(
            self._app._config.osc_transmitters,
            self._app._config.osc_destinations,
        )
        manager.start()
        self._osc_transmitter_manager = manager
        self._sync_osc_binding_conflicts()
        # Subscribe after ``restart`` populates the row list so an early
        # event can't reach a manager with no rows configured yet.
        manager.attach_midi_subsystem(self._midi)
        manager.attach_virtual_fader_bus(self._virtual_faders)
        # Re-attach the input event bus when the InputManager already
        # exists. At startup this is a no-op – ``init_input_manager`` runs
        # later and does the attach – but on a hot-reload re-init (manager
        # was None and is rebuilt here, after ``init_input_manager`` already
        # ran) it restores the HotkeyTrigger / ControllerButtonTrigger
        # dispatch the fresh manager would otherwise silently lose.
        if self._app._input_manager is not None:
            manager.attach_event_bus(self._app._input_manager.event_bus)

    def _sync_osc_binding_conflicts(self) -> None:
        """Mirror the current OSC-binding triggers into the
        :class:`ConflictRegistry` under the ``osc:hotkey`` /
        ``osc:controller_button`` owner names, so the web UI's blur
        validator sees the operator's Hotkey / ControllerButton claims.

        Called from :meth:`init_osc_transmitters` and
        :meth:`apply_osc_transmitters_change` so boot, CRUD edits, and
        TOML reloads converge to the same registry state.
        ``replace_owner_bindings`` swaps the owner's claim set atomically
        under the registry lock – readers never see a transient gap.

        These owners are distinct from ``system:movement``, so a movement
        key plus a Hotkey row claiming the same key surfaces as a conflict
        (different owners on the same binding = collision).
        """
        from openfollow.input.conflicts import InputBinding

        key_bindings: list[InputBinding] = []
        button_bindings: list[InputBinding] = []
        for row in self._app._config.osc_transmitters.transmitters:
            trigger = row.trigger
            if isinstance(trigger, HotkeyTrigger) and trigger.key:
                key_bindings.append(
                    InputBinding(kind="key", identifier=trigger.key),
                )
            elif isinstance(trigger, ControllerButtonTrigger) and trigger.button:
                button_bindings.append(
                    InputBinding(
                        kind="controller_button",
                        identifier=trigger.button,
                    ),
                )
        self._conflict_registry.replace_owner_bindings(
            "osc:hotkey",
            key_bindings,
        )
        self._conflict_registry.replace_owner_bindings(
            "osc:controller_button",
            button_bindings,
        )

    def _marker_provider(self, marker_id: int) -> Marker | None:
        """Look up a marker on the running PSN server. ``None`` when
        the id has no matching marker – the manager treats that as
        skip-on-no-data."""
        server = self._app._server
        if server is None:  # pragma: no cover - init_psn always runs first
            return None
        return server.get_marker(marker_id)

    def _fader_provider(self, fader_index: int) -> float | None:
        """Look up a virtual fader's current value (0..1). ``None`` when
        the index isn't registered (e.g. ``[fader:9]`` on the eight-fader
        bus). Bounds-check up front rather than relying on
        :meth:`VirtualFaderBus.value`'s ``IndexError`` so the defensive
        path is unit-testable without provoking an exception in the bus."""
        if 1 <= fader_index <= self._virtual_faders.fader_count:
            return self._virtual_faders.value(fader_index)
        return None

    def _marker_fader_provider(self, marker_id: int) -> float | None:
        """Look up a marker's gamepad fader value (0..1) for the
        ``[markerfader]`` placeholder. ``None`` when the marker has no
        provisioned fader (it isn't in ``controlled_marker_ids``), which
        the renderer turns into a ring-buffer skip."""
        return self._virtual_faders.marker_fader_value(marker_id)

    def _controller_marker_provider(self, controller_idx: int) -> int | None:
        """Map a 0-based controller index to the marker it currently drives,
        for the OSC ``:cN`` reference. ``None`` when no marker is driven –
        the renderer turns that into a ring-buffer skip.

        Reads ``_input_manager`` lazily: the transmitter manager is built in
        :meth:`init_osc_transmitters`, before :meth:`init_input_manager`, so
        a render before the input manager exists must tolerate ``None``
        rather than crash. ``_gamepad_marker_id`` snapshots the rebindable
        controlled-id list so a concurrent hot-reload can't raise."""
        im = self._app._input_manager
        if im is None:
            return None
        return im.controller_marker_id(controller_idx)

    def _controlled_markers_provider(self) -> list[int]:
        """Snapshot of ``controlled_marker_ids`` for the OSC transmitter
        ``markers`` field's ``all`` token + controlled-only validity filter.

        Reads ``app._controlled_ids`` (the same list the gamepad routing and
        marker-fader provisioning use), copied so a concurrent hot-reload
        swapping the list can't tear a mid-tick read."""
        return list(self._app._controlled_ids)

    def _marker_fader_values_provider(self) -> list[dict[str, Any]]:
        """Snapshot of the per-controlled-marker faders for the MIDI
        page's read-only live viz. One entry per id in
        ``controlled_marker_ids`` (those are exactly the markers with a
        provisioned fader); ``name`` falls back to ``""`` (the template
        renders ``M<id>``). Pulled per poll, like the virtual-fader
        snapshot. A controlled id without a provisioned fader yet (a
        transient between a ``controlled_marker_ids`` edit and the
        re-provision) is skipped so the viz never shows a phantom row."""
        from openfollow.runtime.services_marker_visuals import (
            _resolve_marker_color,
            _resolve_marker_name,
        )

        out: list[dict[str, Any]] = []
        for marker_id in self._app._controlled_ids:
            value = self._virtual_faders.marker_fader_value(marker_id)
            if value is None:
                continue
            out.append(
                {
                    "marker_id": marker_id,
                    "name": _resolve_marker_name(self._app, marker_id),
                    "value": value,
                    # Marker catalog colour so the read-only fader strip can
                    # tint itself to match its marker on the overlay.
                    "color": _resolve_marker_color(self._app, marker_id),
                }
            )
        return out

    def _midi_discovered_devices_provider(self) -> list[dict[str, Any]]:
        """Currently-connected MIDI devices for the MIDI page's Devices
        table. Bridges :meth:`MidiSubsystem.discover` into the plain-dict
        shape the route layer consumes.

        ``identifier`` is the substrate's stable matching key
        (serial-prefixed when present, port+product fallback otherwise) –
        the alias-edit form posts it back so :meth:`MidiSubsystem._match`
        can resolve the operator's choice to a port. Empty list while
        ``discover`` returns nothing (no devices, or broken backend)."""
        out: list[dict[str, Any]] = []
        for device in self._midi.discover():
            out.append(
                {
                    "identifier": device.identifier,
                    "port_name": device.port_name,
                    "product": device.product,
                    "serial": device.serial,
                }
            )
        return out

    def _midi_fader_values_provider(self) -> list[dict[str, Any]]:
        """Snapshot of the eight virtual faders for the live-poll
        endpoint. Pulled from the bus's read methods rather than pushed on
        every change, because the poll cadence (100 ms) is much coarser
        than the bus event rate (a fader sweep is ~127 events/sec).
        Avoids maintaining a separate snapshot cache + invalidation."""
        out: list[dict[str, Any]] = []
        for index in range(1, self._virtual_faders.fader_count + 1):
            out.append(
                {
                    "index": index,
                    "name": self._virtual_faders.name(index),
                    "value": self._virtual_faders.value(index),
                    "picked_up": self._virtual_faders.is_picked_up(index),
                    "show_on_display": (self._virtual_faders.show_on_display(index)),
                }
            )
        return out

    def _grid_provider(self) -> tuple[float, float, float, float]:
        """Snapshot the current grid dimensions for fractional placeholders.
        Read on every tick so a hot-reload of ``GridConfig`` takes effect
        without a manager restart.

        Returns ``(width, depth, max_height, z_offset)``. ``max_height`` is
        the upward extent for ``[fz]`` / ``[ifz]``; ``z_offset`` shifts the
        volume's floor (``0`` by default, raised when the grid plane sits
        above stage zero). ``max_height = 0`` means \"unset\" – the renderer
        surfaces ``[fz]`` / ``[ifz]`` as ``RenderError`` so the per-binding
        ring buffer shows a clear skip reason.
        """
        grid = self._app._config.grid
        return (
            float(grid.width),
            float(grid.depth),
            float(grid.max_height),
            float(grid.z_offset),
        )

    # -- Live config-apply orchestrators -----------------

    def apply_otp_output_change(self, new_cfg: OtpOutputConfig) -> None:
        """Apply an ``otp_output`` config change live, covering all four
        enabled-state transitions:

        - on  → on  (different cfg) : in-place ``OtpServer.restart(...)``
        - on  → off                 : ``stop()`` + drop the reference
        - off → on                  : full ``init_otp()``
        - off → off                 : no-op
        """
        server = self._app._otp_server
        if server is not None and new_cfg.enabled:
            # Snapshot only otp_output-owned fields so a failed restart can
            # be rolled back. ``system_name`` is NOT captured: it's owned by
            # ``psn_system_name``, applied earlier in the same hot-reload
            # pass. Capturing it would freeze it at the prior value and, on
            # rollback, silently undo an unrelated ``psn_system_name``
            # change. Both forward and rollback restarts pull ``system_name``
            # from the current config, so rollback only undoes its own fields.
            old_system_number = server._system_number
            old_port = server._port
            old_source_ip = server._source_ip
            old_priority = server._priority
            try:
                server.restart(
                    system_name=self._app._config.psn_system_name,
                    system_number=new_cfg.system_number,
                    port=new_cfg.port,
                    source_ip=self._resolved_otp_source_ip(new_cfg),
                    priority=new_cfg.priority,
                )
            except Exception:
                # Best-effort rollback to the prior cfg so output stays
                # alive on the old interface. If rollback also fails, log
                # and re-raise the original (more informative) error. Only
                # null the reference when the server is genuinely dead
                # (rollback raised), so rollback-success doesn't orphan a
                # still-running server.
                try:
                    server.restart(
                        system_name=self._app._config.psn_system_name,
                        system_number=old_system_number,
                        port=old_port,
                        source_ip=old_source_ip,
                        priority=old_priority,
                    )
                except Exception:
                    logger.exception(
                        "OTP server rollback to prior config failed; output is stopped",
                    )
                    self._app._otp_server = None
                raise
        elif server is not None and not new_cfg.enabled:
            server.stop()
            self._app._otp_server = None
        elif server is None and new_cfg.enabled:
            self.init_otp()
            # ``init_otp`` → ``OtpServer.start()`` swallows multicast-bind
            # failures and spawns a daemon retry thread. Fine at startup,
            # wrong here: the dispatcher needs a hard signal so
            # ``_apply_with_fallback`` can revert ``otp_output``. Mirror
            # ``OtpServer.restart``'s post-start guard so off→on isn't
            # silently a no-op.
            new_server = self._app._otp_server
            if new_server is not None and new_server._is_multicast_mode() and new_server._socket is None:
                new_server.stop()
                self._app._otp_server = None
                raise OSError(
                    f"OTP off→on init failed to open multicast socket "
                    f"(transform={new_server._transform_dest!r}, "
                    f"advertisement={new_server._advertisement_dest!r}, "
                    f"source_ip={new_server._source_ip!r})",
                )
        # else: off → off, no-op

    def apply_rttrpm_output_change(self, new_cfg: RttrpmOutputConfig) -> None:
        """Apply an ``rttrpm_output`` config change live; same four-state
        matrix as ``apply_otp_output_change``."""
        server = self._app._rttrpm_server
        if server is not None and new_cfg.enabled:
            # Same transactional-rollback rationale as
            # ``apply_otp_output_change``: capture prior cfg so a
            # failed restart can be undone, keeping runtime aligned
            # with the dispatcher's config-revert.
            old_host = server._host
            old_port = server._port
            old_fps = float(server._fps)
            old_context = server._context
            try:
                server.restart(
                    host=new_cfg.host,
                    port=new_cfg.port,
                    fps=float(new_cfg.fps),
                    context=new_cfg.context,
                )
            except Exception:
                # Mirror the OTP rationale: only null the reference
                # when rollback also failed (server is genuinely
                # dead), so a successful rollback preserves the
                # running server for the dispatcher's config-revert.
                try:
                    server.restart(
                        host=old_host,
                        port=old_port,
                        fps=old_fps,
                        context=old_context,
                    )
                except Exception:
                    logger.exception(
                        "RTTrPM server rollback to prior config failed; output is stopped",
                    )
                    self._app._rttrpm_server = None
                raise
        elif server is not None and not new_cfg.enabled:
            server.stop()
            self._app._rttrpm_server = None
        elif server is None and new_cfg.enabled:
            self.init_rttrpm()
        # else: off → off, no-op

    def apply_osc_transmitters_change(
        self,
        new_cfg: OscTransmittersConfig,
        destinations: OscDestinationsConfig | None = None,
    ) -> None:
        """Apply an ``osc_transmitters`` (or destinations) change live.

        Two-state matrix instead of OTP/RTTrPM's four-state because the
        manager always exists once :meth:`init_osc_transmitters` has run:

        - **Manager not yet initialised** (pre-init or post-shutdown):
          run :meth:`init_osc_transmitters` to construct + populate +
          start it.
        - **Manager exists**: hand the new config to ``manager.restart``.
          Rows are diffed by id; the scheduler thread keeps running
          throughout. An all-rows-deleted config leaves an idle manager
          that wakes up cheaply if rows return on the next reload.

        ``destinations`` stages the current OSC destination profiles so a
        destination-only edit (e.g. an IP change) re-resolves every row;
        ``None`` falls back to the app's current set.
        """
        if destinations is None:
            destinations = self._app._config.osc_destinations
        manager = self._osc_transmitter_manager
        if manager is None:
            self.init_osc_transmitters()
            return
        manager.restart(new_cfg, destinations)
        # ``init_osc_transmitters`` syncs conflicts in its own body; this
        # hot-reload branch needs a parallel sync. Keeping it outside
        # ``manager.restart`` keeps the manager registry-agnostic.
        self._sync_osc_binding_conflicts()

    def apply_psn_source_ip_change(
        self,
        new_source_ip: str,
        *,
        new_mcast_ip: str | None | _Unchanged = _UNCHANGED,
    ) -> None:
        """Apply a ``psn_source_ip`` change live by rebinding both the
        PSN input (receiver) and PSN output (server) sockets.

        ``psn_source_ip`` is read by both ``init_psn_receiver`` and
        ``init_psn`` at startup, so a live change must propagate to
        both – otherwise the output keeps sending from the old
        interface and operators see a one-way effect (input bound to
        the new IP, output stuck on the old one).

        ``new_mcast_ip`` is optional: pass it together with
        ``new_source_ip`` to apply both changes in a single server
        stop/start cycle. Without it, the first server rebind would
        briefly bind one new value against the other's old value before a
        second rebind corrected it.

        Receiver is rebound first because its ``rebind`` raises on
        failure: if the new interface IP is bad, ``_apply_with_fallback``
        catches the exception before we touch the working server.

        **Transactional rollback**: a successful receiver rebind makes
        a server rebind extremely likely to succeed too (same kernel
        routing tables) – but if it doesn't, we'd be left with input
        on the new IP and output dead while the dispatcher reverts
        ``app._config.psn_source_ip`` to the old value, leaving runtime
        state inconsistent with the stored config. To avoid that, on
        any partial-failure we best-effort restore each side that was
        attempted to its prior IP (and prior mcast_ip when that was
        also being changed) so the receiver, server, and config end
        up agreeing again. The roll-back is best-effort: if it also
        fails, log and re-raise the original failure so the
        dispatcher's degrade-on-fail wiring still kicks in.

        Note: ``PsnServer.rebind`` does ``stop()`` → set source (and
        optionally mcast_ip) → ``start()`` and raises if start fails.
        On its raise the server is stopped with the new values set
        internally, so the rollback rebind needs to restore both
        fields when both were changed – without it,
        ``app._config.psn_source_ip`` reverts to old but PSN output
        stays bound to the new (broken) values until the operator
        re-saves.
        """
        receiver = self._app._psn_receiver
        server = self._app._server
        old_recv_ip = receiver._source_ip if receiver is not None else None
        old_srv_ip = server._source_ip if server is not None else None
        old_srv_mcast = server._mcast_ip if server is not None else None
        receiver_attempted = False
        server_attempted = False
        try:
            if receiver is not None:
                receiver_attempted = True
                receiver.rebind(new_source_ip)
            if server is not None:
                server_attempted = True
                if isinstance(new_mcast_ip, _Unchanged):
                    server.rebind(new_source_ip)
                else:
                    server.rebind(new_source_ip, mcast_ip=new_mcast_ip)
        except Exception:
            if receiver_attempted and receiver is not None and old_recv_ip is not None:
                try:
                    receiver.rebind(old_recv_ip)
                except Exception:
                    logger.exception(
                        "PSN receiver rollback to source_ip=%r failed",
                        old_recv_ip,
                    )
            if server_attempted and server is not None and old_srv_ip is not None:
                try:
                    if isinstance(new_mcast_ip, _Unchanged):
                        server.rebind(old_srv_ip)
                    else:
                        server.rebind(old_srv_ip, mcast_ip=old_srv_mcast)
                except Exception:
                    logger.exception(
                        "PSN server rollback to source_ip=%r failed",
                        old_srv_ip,
                    )
            raise

    def apply_psn_mcast_ip_change(self, new_mcast_ip: str) -> None:
        """Apply a ``psn_mcast_ip`` change live by rebinding the PSN
        output socket onto the new multicast group.

        Mirrors ``apply_psn_source_ip_change``'s shape: the running
        ``PsnServer`` is recycled in place via ``rebind_mcast_ip`` so
        the next data/info packet broadcasts on the new group.
        Marker registrations survive – only the socket and worker
        threads recycle.

        Best-effort transactional rollback: if ``rebind_mcast_ip``
        raises (e.g. multicast bind fails on the new group / interface
        combo), restore the prior ``mcast_ip`` so output keeps running
        on the working group while the dispatcher reverts
        ``app._config.psn_mcast_ip``. If rollback also fails, log and
        re-raise the original failure so ``_apply_with_fallback`` still
        reverts stored config and the next reload retries.

        Defensive on ``server is None``: live-apply that arrives during
        a brief teardown window must not ``AttributeError`` on
        ``None.rebind_mcast_ip``.
        """
        server = self._app._server
        if server is None:
            return
        old_mcast_ip = server._mcast_ip
        try:
            server.rebind_mcast_ip(new_mcast_ip)
        except Exception:
            try:
                server.rebind_mcast_ip(old_mcast_ip)
            except Exception:
                logger.exception(
                    "PSN server rollback to mcast_ip=%r failed",
                    old_mcast_ip,
                )
            raise

    def apply_psn_system_name_change(self, new_name: str) -> None:
        """Propagate a ``psn_system_name`` change into running services.

        Touches:

        - ``PsnServer._system_name`` (info packet name field)
        - ``OtpServer._system_name`` (held for parity; encoders don't
          read it today, but keeping it in sync prevents drift if a
          future advertisement PDU gains a name field)
        - ``ConfigWebServer.update_system_name`` (web beacon)
        - the GTK window title via ``_apply_window_title``

        Computes the canonical name (strip + fall back to ``"OpenFollow"``
        on empty) once and reuses it for every update so all services
        agree – otherwise an empty/whitespace name would land as
        ``"OpenFollow"`` on the title but the raw string elsewhere.

        No socket recycle: each callee mutates an attribute under its own
        lock so the next outbound packet announces the new name.
        """
        canonical = new_name.strip() or "OpenFollow"
        self._apply_window_title(canonical)
        if self._app._server is not None:
            self._app._server.update_system_name(canonical)
        if self._app._otp_server is not None:
            self._app._otp_server.update_system_name(canonical)
        if self._app._web_server is not None:
            self._app._web_server.update_system_name(canonical)

        # Keep the OS hostname (and thus the ``<slug>.local`` mDNS name) in
        # step with the station name, so renaming the unit in the web UI also
        # renames it on the network. Best-effort + passwordless-gated: a no-op
        # off-appliance or when the name already matches; never raises.
        from openfollow.privilege.device_repair import sync_station_hostname

        sync_station_hostname(self._privilege_broker, canonical)

    def apply_detection_change(self, new_cfg: DetectionConfig) -> None:
        """Apply a ``detection`` config change live for the three
        transitions the detector worker can handle in-process:

        - on  → on  (different cfg) : ``PersonDetector.reload_config``
          stages the swap; the worker drains it between frames and
          rebuilds the backend session if model / backend / device /
          storage_path changed.
        - on  → off                 : ``stop()`` + drop the reference.
        - off → off                 : no-op.

        The fourth state – ``off → on`` – needs the receiver pipeline to
        wire the appsink into a freshly-constructed detector, which
        ``init_video`` only does at startup; the dispatcher catches that
        case and falls back to ``request_restart()``.

        The detector reference lives on ``AppRuntimeServices`` (set in
        ``init_video``), not on ``OpenFollowApp``, so read/write
        ``self._person_detector`` – ``self._app._person_detector`` does
        not exist on the real app and would raise ``AttributeError`` in
        production while still passing ``SimpleNamespace`` tests.
        """
        detector = self._person_detector
        if detector is not None and new_cfg.enabled:
            detector.reload_config(new_cfg)
        elif detector is not None and not new_cfg.enabled:
            detector.stop()
            self._person_detector = None
        # else: detector is None – caller (dispatcher) handles the
        # off→on case via request_restart and the off→off case is a
        # genuine no-op.

    def swap_detector(self, new_cfg: DetectionConfig) -> None:
        """Apply a ``detection`` config change live for the three
        transitions ``apply_detection_change`` cannot serve: off→on,
        unavailable→on (re-probed backend), and ``inference_size``
        change while detection is enabled.

        Each case reduces to the same primitive: rebuild the receiver
        pipeline so the detection branch matches the new detector's
        appsink caps. ``PersonDetector`` construction is cheap once the
        model file is cached – ``_load_backend`` is the only heavy step,
        accepted as a brief GTK-thread stall on save. Backend-init
        failures land as ``available=False`` (swallowed in ``__init__``);
        the reason surfaces via runtime-stats ``missing_deps``, not a raise.

        Skip-rebuild fast path: when **both** old and new detector are
        unavailable (or there's no old detector) the prior pipeline never
        wired a detection appsink, so a rebuild would just blackout for no
        observable change. Replacing the reference is enough.

        **Transactional rollback** mirrors ``swap_video``: on
        ``swap_detection_branch`` failure, rebuild against the
        prior detector so the receiver stays consistent with the
        dispatcher's reverted ``app._config.detection``. Receiver
        reference stays attached across every failure mode –
        runtime / UI paths read ``app._video_receiver.<x>`` every
        frame. ``PipelineStuckError`` is the one failure mode where
        rollback is unsafe (the rollback would re-attach the shared
        sink to a new pipeline while the old one is still alive);
        re-raise WITHOUT a rollback attempt.
        """
        from openfollow.video.detection import PersonDetector
        from openfollow.video.receiver import PipelineStuckError

        receiver = self._app._video_receiver
        # A missing receiver means the runtime isn't initialised; raise so
        # ``_apply_with_fallback`` reverts rather than committing the new
        # cfg against a missing runtime.
        if receiver is None:
            raise RuntimeError(
                "swap_detector: no video receiver to swap – runtime is not initialised",
            )

        old_detector = self._person_detector
        old_was_available = old_detector is not None and old_detector.available

        # ``self._person_detector`` and the receiver's own ``_detector``
        # can diverge: ``apply_detection_change`` (on→off) and the
        # rollback-failure path below clear the orchestrator reference
        # without touching the receiver, which may still have a detector
        # wired into the pipeline. Treat the receiver's view as
        # authoritative for whether a detection branch exists, else the
        # skip-rebuild fast path would strand an orphan branch on the next
        # swap that also lands on an unavailable detector.
        receiver_detector = getattr(receiver, "_detector", None)
        if receiver_detector is not None and getattr(receiver_detector, "available", False):
            old_was_available = True

        # ``PersonDetector.__init__`` self-gates on ``config.enabled``
        # (returns early without touching cv2 / the backend when
        # disabled). The dispatcher only routes here when ``new_cfg.enabled``
        # is True, so the unavailable branch below covers genuine
        # backend-init failures.
        new_detector = PersonDetector(new_cfg)

        if not new_detector.available and not old_was_available:
            # Both ends unavailable: neither pipeline has a detection
            # branch. Skip the NULL transition and just refresh the
            # reference so runtime stats publish the new ``missing_deps``.
            # ``stop()`` is no-op-safe when the outgoing detector never
            # started (its worker thread was never created).
            if old_detector is not None:
                old_detector.stop()
            self._person_detector = new_detector
            return

        # Either the new detector is available (needs fresh
        # appsink wiring) or the old one was (need to drop the
        # now-orphaned detection branch). Either way the pipeline
        # has to be rebuilt.
        try:
            receiver.swap_detection_branch(new_detector)
        except PipelineStuckError:
            # ``logger.error`` (not ``logger.exception``): the caller
            # ``_apply_with_fallback`` already runs ``logger.exception``,
            # so a traceback here would just duplicate the stack frame.
            # The new detector was never wired, so there's nothing to
            # clean up – its backend session is alive in the unreferenced
            # instance, but its worker thread never started.
            logger.error(
                "detection swap blocked by stuck prior pipeline; "
                "keeping detector attached on prior cfg – next "
                "reload or restart will retry",
            )
            raise
        except Exception:
            # Best-effort rollback to the prior detector. Surface the
            # PRIMARY (forward-swap) exception even when rollback also
            # raises – the rollback failure is logged and silenced so the
            # operator sees the original cause.
            #
            # If rollback also raises, the receiver is in a known-bad
            # state – drop the detector reference so subsequent passes
            # route through off→on again instead of calling
            # ``reload_config`` on a half-applied detector.
            try:
                receiver.swap_detection_branch(old_detector)
            except Exception:
                logger.error(
                    "detection receiver rollback to prior detector failed; clearing reference",
                )
                self._person_detector = None
            else:
                self._person_detector = old_detector
            raise

        # ``swap_detection_branch`` already stopped the OLD
        # detector and started the NEW one inside the same call;
        # the orchestrator only owns the reference update.
        self._person_detector = new_detector

    def swap_video(self, new_cfg: AppConfig) -> None:
        """Apply a video-source config change live by swapping the
        receiver's input plugin in place.

        Routed via the dispatcher when the active plugin's
        ``config_changed(old, new)`` returns True or the
        ``video_source_type`` itself changes.

        Snapshots the receiver's current plugin/config before the swap so
        a failed forward swap can be rolled back to the prior runtime
        state, keeping the receiver alive on the old source.

        The receiver reference STAYS attached across every failure mode:
        runtime / UI paths assume a non-None ``app._video_receiver`` (e.g.
        ``build_marker_visual_state`` reads ``video_receiver.status_marker``
        every tick with no guard), so dropping it would crash the next
        frame. Re-raising without dropping lets the dispatcher revert
        config; the receiver stays on whatever state the failed swap left
        it in (typically the OLD plugin, since ``swap_input`` only assigns
        ``self._input`` AFTER teardown succeeds).
        """
        from openfollow.video.receiver import PipelineStuckError

        receiver = self._app._video_receiver
        # The animation tick runs after ``init_video`` populates
        # ``_video_receiver``; ``None`` here means the receiver was never
        # initialised. Raise so ``_apply_with_fallback`` reverts the
        # committed config instead of leaving stored cfg out of sync with
        # a missing runtime.
        if receiver is None:
            raise RuntimeError(
                "swap_video: no video receiver to swap – runtime is not initialised",
            )

        from openfollow.video.inputs import get_input_class

        new_input_cls = get_input_class(new_cfg.video_source_type)
        if new_input_cls is None:
            raise ValueError(
                f"Unknown video input type: {new_cfg.video_source_type!r}",
            )
        new_input_config = new_input_cls.get_config_field_values(new_cfg)

        old_source_type = receiver._source_type
        old_input_config = dict(receiver._input_config)

        try:
            receiver.swap_input(new_cfg.video_source_type, new_input_config)
        except PipelineStuckError:
            # A stuck old pipeline / discovery thread is the one failure
            # mode where rollback is unsafe – the rollback ``swap_input``
            # would re-attach the shared sink to a new pipeline while the
            # old one is still alive, racing on the resource we just
            # refused to hand over. Re-raise WITHOUT rollback; the receiver
            # stays attached reading its (still-old) ``_input`` and the
            # stuck thread finishes in the background.
            #
            # ``logger.error`` (not ``logger.exception``): the caller
            # ``_apply_with_fallback`` already logs the traceback.
            logger.error(
                "video swap blocked by stuck prior pipeline; keeping "
                "receiver attached on old plugin – next reload or "
                "restart will retry",
            )
            raise
        except Exception:
            # Best-effort rollback to the prior plugin/config. If rollback
            # ALSO raises, the receiver is in a known-bad state, but keep
            # the reference attached so runtime code reading
            # ``app._video_receiver.<x>`` on the next tick doesn't
            # AttributeError. The HUD surfaces the error via the status
            # marker; an explicit restart re-runs ``init_video`` cleanly.
            try:
                receiver.swap_input(old_source_type, old_input_config)
            except Exception:
                # ``logger.error``: ``_apply_with_fallback`` already
                # provides the primary stack trace.
                logger.error(
                    "video receiver rollback to prior cfg failed; leaving receiver attached in degraded state",
                )
            raise

    def init_web_server(self) -> None:
        from openfollow.web import ConfigWebServer  # noqa: F811

        self._app._web_server = ConfigWebServer(
            config_path=self._app._config_path,
            host=self._resolve_web_bind(),
            port=self._app._config.web_port,
            system_name=self._app._config.psn_system_name,
            command_queue=self._app._web_commands,
            # Resolved PSN bind IP for the web UI header; the provider
            # re-resolves it live so an IP change updates the self-row.
            local_ip=self._resolved_source_ip(),
            local_ip_provider=self._resolved_source_ip,
            runtime_stats_provider=self.get_runtime_stats_snapshot,
            preview_snapshot_provider=self._preview_provider.get_snapshot,
            zone_state_provider=self._get_zone_states_snapshot,
            zone_diagnostics_provider=self._get_zone_diagnostics_snapshot,
            zone_test_send=self._zone_test_send,
            marker_positions_provider=self._get_marker_positions_snapshot,
            # Live per-marker move speeds; the ``dict(...)`` copy is the
            # mutation-isolation boundary so the web thread never holds the live dict.
            marker_move_speeds_provider=lambda: dict(self._app._config.marker_move_speeds),
            full_snapshot_provider=self._snapshot_provider.get_snapshot,
            # Diagnostics + conflict-probe hooks. ``init_web_server`` runs
            # before ``init_osc_transmitters``, so the manager is ``None``
            # at construction time. These providers dereference
            # ``self._osc_transmitter_manager`` lazily on each call so they
            # pick it up once initialised and report ``None`` (not raise)
            # while it's still absent (boot, mid-restart).
            osc_binding_status_provider=self._osc_binding_status_provider,
            osc_binding_preview_provider=self._osc_binding_preview_provider,
            osc_binding_test_send=self._osc_binding_test_send,
            osc_binding_conflicts_provider=self._osc_binding_conflicts_provider,
            osc_manager_attached_provider=lambda: self._osc_transmitter_manager is not None,
            # MIDI Learn for the OSC binding form's Capture button. Pass
            # the broker's methods directly so the web layer doesn't import
            # the broker class.
            midi_capture_arm=self._midi_capture_broker.arm,
            midi_capture_poll=self._midi_capture_broker.poll,
            # MIDI page reads. Provider closures bridge the substrate's
            # typed shapes into plain dicts. Both substrates are eagerly
            # constructed in ``__init__``, so they exist by now.
            midi_discovered_devices_provider=(self._midi_discovered_devices_provider),
            midi_fader_values_provider=self._midi_fader_values_provider,
            marker_fader_values_provider=self._marker_fader_values_provider,
            # Diagnostics log-tail fallback so the bundle / log-tail
            # endpoint has a source on non-systemd hosts.
            log_ring=self._app._log_ring,
            # Read-only host-network snapshot for the Overview block.
            # Lazy-deferred so the adapter is queried only on Overview open.
            network_state_provider=self._network_state_provider,
            # Web write path: raw editable config snapshot for the form +
            # apply / renew handlers (broker-elevated, serialised).
            network_config_provider=self._network_config_provider,
            network_apply_handler=self._handle_network_apply,
            network_renew_handler=self._handle_network_renew,
            # Privilege capability snapshot for the diagnostics bundle.
            privilege_states_provider=self._privilege_states_provider,
            marker_catalog_provider=lambda: self._app._marker_catalog,
            marker_catalog_sync_provider=lambda: self._app._marker_catalog_sync,
            # Live gamepad snapshot for the diagnostics bundle's E9 section.
            gamepad_runtime_provider=self._gamepad_runtime_snapshot,
            # Diagnostics I/O providers (recent I/O + USB visibility).
            # Deferred closures so they read fresh state per bundle and
            # degrade gracefully while a subsystem is booting.
            recent_osc_sends_provider=self._recent_osc_sends_provider,
            osc_listener_status_provider=self._osc_listener_status_provider,
            recent_midi_events_provider=self._recent_midi_events_provider,
            midi_port_names_provider=self._midi_port_names_provider,
            camera_names_provider=self._camera_names_provider,
            # Startup PSN-source advisory for the PSN section.
            psn_source_advisory_provider=self._psn_source_advisory,
        )
        self._app._web_server.start()

    def _psn_source_advisory(self) -> dict[str, str]:
        """Web provider: the startup PSN-source advisory recorded by
        ``OpenFollowApp.run()`` when the pinned ``psn_source_iface`` could
        not be honoured. Read live (not snapshotted) so the PSN section
        reflects the current degraded state.
        """
        return {
            "status": str(getattr(self._app, "_psn_source_status", "")),
            "banner": str(getattr(self._app, "_psn_source_banner", "")),
            "resolved_ip": str(getattr(self._app, "_psn_source_resolved_ip", "")),
        }

    def _gamepad_runtime_snapshot(self) -> list[dict[str, Any]]:
        """Diagnostics provider: live per-controller SDL state as plain dicts.

        Deferred (called per bundle generation) so it picks up the input
        manager once it's initialised and degrades to an empty list while
        it's still absent (boot / mid-restart) rather than raising."""
        im = self._app._input_manager
        if im is None:
            return []
        return [asdict(info) for info in im.gamepad_handler.runtime_snapshot()]

    def _osc_listener_status_provider(self) -> dict[str, Any]:
        """Diagnostics provider: live OSC inbound-listener status (bound port,
        multicast group + whether the join actually succeeded, sender
        allowlist). Deferred so it reads the running ``OscService`` per
        bundle."""
        return self._osc_service.listener_status()

    def _recent_osc_sends_provider(self) -> list[dict[str, Any]]:
        """Diagnostics provider: recent OSC send / skip events across every
        transmitter row, most-recent-first, in the dict shape the bundle
        renders (``age_s`` / ``status`` / ``address`` / ``args``).

        Flattens each row's
        :class:`~openfollow.osc.transmitter.BindingRingBuffer`. Deferred so
        it picks up the manager once ``init_osc_transmitters`` runs and
        degrades to ``[]`` while the manager is still absent."""
        manager = self._osc_transmitter_manager
        if manager is None:
            return []
        now = time.monotonic()
        flattened: list[dict[str, Any]] = []
        for row_id in manager.row_ids():
            entries = manager.ring_buffer_for(row_id)
            if not entries:
                continue
            for entry in entries:
                flattened.append(
                    {
                        "age_s": max(0.0, now - entry.ts),
                        "status": entry.status,
                        # A skip often has an empty address (the address
                        # template may be what failed to render); fold the
                        # skip reason in so the line is self-explanatory.
                        # ``error`` is "" on every ``sent`` entry, so this
                        # only folds for skips without a status guard.
                        "address": entry.address or entry.error,
                        "args": tuple(entry.args),
                    }
                )
        # Smallest age == most recent; ``ts`` is monotonic so the ordering is
        # comparable across rows. Truncate to keep the bundle bounded.
        flattened.sort(key=lambda d: d["age_s"])
        return flattened[:_RECENT_OSC_SENDS_LIMIT]

    def _recent_midi_events_provider(self) -> list[dict[str, Any]]:
        """Diagnostics provider: recent MIDI events in the dict shape the
        bundle renders (``age_s`` / ``patch_id`` / ``type`` / ``channel``
        / ``number`` / ``value``).

        Reads :meth:`MidiSubsystem.recent_events` – the always-on receive
        ring, distinct from the one-shot MIDI-Learn capture broker, so the
        bundle reflects activity outside learn mode. The subsystem is
        constructed eagerly in ``__init__`` so this never sees a ``None``."""
        now = time.monotonic()
        # Newest-first to match the adjacent most-recent-first OSC list;
        # ``recent_events()`` returns the ring oldest-first.
        return [
            {"age_s": max(0.0, now - event.timestamp), **event.as_dict()}
            for event in reversed(self._midi.recent_events())
        ]

    def _midi_port_names_provider(self) -> list[str]:
        """Diagnostics provider: names of the connected MIDI input devices,
        for the USB-visibility cross-reference. Reads
        :meth:`MidiSubsystem.connected_port_names` – the hotplug-tracked
        cache – rather than a live ``discover()``, so a bundle download
        never blocks the web thread on an ALSA enumeration or flips the
        ``midi_unavailable`` badge as a side effect. The normalised name
        substring-matches the USB device string."""
        return self._midi.connected_port_names()

    def _camera_names_provider(self) -> list[str]:
        """Diagnostics provider: OS-enumerated USB camera names for the
        USB-visibility cross-reference. Thin bridge to
        :func:`openfollow.video.inputs.usb_camera_names` (V4L2 on Linux,
        AVFoundation on macOS); never raises."""
        from openfollow.video import inputs

        return inputs.usb_camera_names()

    def _network_state_provider(self) -> dict[str, Any] | None:
        """Snapshot the active adapter's state for the web Overview.

        Returns:
            ``None`` only when no adapter is wired (boot mid-stream).
            ``{"interfaces": [], "writable": bool}`` when the adapter
            reports no non-loopback interfaces – the Overview partial
            and tests rely on this empty-dict shape.
            Otherwise a full snapshot dict with ``active_interface``,
            ``method``, ``address``, etc.

        The Overview partial degrades gracefully on every shape –
        renders the "configure from the device" banner when the dict
        is empty / missing fields.
        """
        from openfollow.network.adapter import is_loopback

        adapter = getattr(self, "_network_adapter", None)
        if adapter is None:
            return None
        # Drop loopback – it's always ``is_up`` on Linux and would make
        # the Overview report 127.0.0.1 as the device address.
        interfaces = [i for i in adapter.list_interfaces() if not is_loopback(i)]
        if not interfaces:
            return {"interfaces": [], "writable": adapter.is_writable()}
        # Pick the first interface that's up; fall back to the first.
        chosen = next((i for i in interfaces if i.is_up), interfaces[0])
        state = adapter.get_state(chosen.name)
        if state is None:
            return {
                "interfaces": [i.name for i in interfaces],
                "writable": adapter.is_writable(),
                "backend": adapter.backend_name,
            }
        method_label = {
            "dhcp": "DHCP",
            "dhcp_manual": "DHCP with manual address",
            "static": "Static",
        }.get(state.ipv4.method.value, state.ipv4.method.value)
        lease = state.lease
        # Pre-format the dotted subnet mask and lease label server-side
        # so the template doesn't need to import network helpers.
        # ``prefix`` and ``lease_remaining`` stay in the payload as
        # numeric fields for any future API caller that wants the raw
        # values.
        from openfollow.network.validate import prefix_to_mask

        lease_seconds = lease.lease_seconds_remaining if lease and lease.lease_seconds_remaining is not None else None
        return {
            "interfaces": [i.name for i in interfaces],
            "active_interface": state.interface.name,
            "method": method_label,
            "address": state.ipv4.address or "–",
            "prefix": state.ipv4.prefix,
            "subnet_mask": prefix_to_mask(state.ipv4.prefix),
            "router": state.ipv4.router or "–",
            "dns": list(state.ipv4.dns),
            "lease_remaining": lease_seconds,
            "lease_remaining_display": _format_lease_remaining(lease_seconds),
            "writable": adapter.is_writable(),
            "backend": adapter.backend_name,
        }

    def _network_config_provider(
        self,
        iface: str | None = None,
    ) -> dict[str, Any] | None:
        """Raw editable snapshot of host network config for the web write
        form.

        Like :meth:`_network_state_provider`, but returns the IPv4 *method
        value* (``"dhcp"`` / ``"dhcp_manual"`` / ``"static"``) and raw
        address / prefix / router / dns (empty string / ``None`` / ``[]``,
        not the ``"–"`` display dashes) so the form can pre-fill its inputs.
        ``iface`` selects a specific interface; ``None`` picks the first one
        that's up (else the first). Returns ``None`` when no adapter is wired.
        """
        from openfollow.network.adapter import is_loopback

        adapter = getattr(self, "_network_adapter", None)
        if adapter is None:
            return None
        interfaces = [i for i in adapter.list_interfaces() if not is_loopback(i)]
        names = [i.name for i in interfaces]
        base: dict[str, Any] = {
            "interfaces": names,
            "writable": adapter.is_writable(),
            "backend": adapter.backend_name,
        }
        if not interfaces:
            return base
        chosen_name = iface if iface in names else None
        if chosen_name is None:
            chosen = next((i for i in interfaces if i.is_up), interfaces[0])
            chosen_name = chosen.name
        base["active_interface"] = chosen_name
        state = adapter.get_state(chosen_name)
        if state is None:
            base.update(
                method="dhcp",
                address="",
                prefix=None,
                subnet_mask="",
                router="",
                dns=[],
                lease_display=None,
            )
            return base
        lease = state.lease
        lease_seconds = lease.lease_seconds_remaining if lease and lease.lease_seconds_remaining is not None else None
        from openfollow.network.validate import prefix_to_mask

        base.update(
            method=state.ipv4.method.value,
            address=state.ipv4.address or "",
            prefix=state.ipv4.prefix,
            subnet_mask=prefix_to_mask(state.ipv4.prefix) or "",
            router=state.ipv4.router or "",
            dns=list(state.ipv4.dns),
            lease_display=_format_lease_remaining(lease_seconds),
        )
        return base

    def _handle_network_apply(
        self,
        iface: str,
        config: Ipv4Config,
    ) -> ApplyResult:
        """Apply an IPv4 config to ``iface`` via the adapter.

        Serialised with the renew path (``_network_op_lock``) so concurrent
        web requests can't race the host's network state. Validation is the
        caller's job – the route runs :func:`validate_apply` first.
        """
        from openfollow.network.adapter import ApplyResult

        adapter = getattr(self, "_network_adapter", None)
        if adapter is None:
            return ApplyResult(ok=False, message="No network adapter available.")
        if not adapter.is_writable():
            return ApplyResult(ok=False, message="Read-only host – cannot apply.")
        with self._network_op_lock:
            return cast(ApplyResult, adapter.apply_ipv4(iface, config))

    def _handle_network_renew(self, iface: str) -> ApplyResult:
        """Renew the DHCP lease on ``iface`` via the adapter, serialised
        with apply (see :meth:`_handle_network_apply`)."""
        from openfollow.network.adapter import ApplyResult

        adapter = getattr(self, "_network_adapter", None)
        if adapter is None:
            return ApplyResult(ok=False, message="No network adapter available.")
        if not adapter.is_writable():
            return ApplyResult(ok=False, message="Read-only host – cannot renew.")
        with self._network_op_lock:
            return cast(ApplyResult, adapter.renew_lease(iface))

    def _privilege_states_provider(self) -> dict[str, str]:
        """Snapshot every capability's state for the web UI.

        Returns ``{name: state.value}`` so the template can compare
        against string literals without an enum import.
        """
        broker = getattr(self, "_privilege_broker", None)
        if broker is None:
            return {}
        return {name: state.value for name, state in broker.states().items()}

    def _osc_binding_status_provider(
        self,
        row_id: str,
    ) -> dict[str, Any] | None:
        """Bridge from the web layer's status hook to the live
        :class:`OscTransmitterManager`. ``None`` while the manager
        isn't attached so the route surfaces ``available=False``."""
        manager = self._osc_transmitter_manager
        if manager is None:
            return None
        return manager.status_for(row_id)

    def _osc_binding_preview_provider(
        self,
        row_id: str,
    ) -> dict[str, Any] | None:
        """Render the row's templates against current marker state
        without sending. ``None`` while the manager isn't attached."""
        manager = self._osc_transmitter_manager
        if manager is None:
            return None
        return manager.preview_for(row_id)

    def _osc_binding_test_send(self, row_id: str) -> dict[str, Any]:
        """One-shot send for the row, bypassing its ``enabled`` flag.
        Empty dict when the manager isn't attached or the row is
        unknown – both collapse to ``available=False`` in the route
        layer's contract."""
        manager = self._osc_transmitter_manager
        if manager is None:
            return {}
        result = manager.test_send(row_id)
        return result or {}

    def _osc_binding_conflicts_provider(
        self,
        kind: str,
        identifier: str,
        owner: str,
    ) -> list[str]:
        """Conflict-probe bridge for the on-blur validator. Wraps
        :meth:`ConflictRegistry.conflicts_for` with the
        ``InputBinding`` construction so the web layer doesn't have
        to reach into the input subsystem's types."""
        from openfollow.input.conflicts import InputBinding

        return self._conflict_registry.conflicts_for(
            InputBinding(kind=kind, identifier=identifier),  # type: ignore[arg-type]
            owner=owner,
        )

    def init_input_manager(self) -> None:
        self._app._input_manager = InputManager(self._app)
        # Wire the OSC transmitter manager's Hotkey / ControllerButton
        # trigger dispatch to the input event bus. ``init_osc_transmitters``
        # ran before the InputManager's bus existed, so this late-binding
        # ``attach_event_bus`` is the seam where both halves meet. ``None``
        # means it was skipped or failed – that run just gets no OSC trigger
        # dispatch; the rest of the input system still works.
        if self._osc_transmitter_manager is not None:
            self._osc_transmitter_manager.attach_event_bus(
                self._app._input_manager.event_bus,
            )

    def update_video(self) -> None:
        update_video_helper(self._app, logger)

    def update_marker_visuals(self) -> None:
        """Push current marker + camera state to the Cairo overlay renderer.

        Builds a complete new OverlayState and swaps it atomically so the
        GStreamer rendering thread never sees partially-updated state.
        Uses the object pool + pre-allocated camera-params buffer to
        reduce allocation churn.
        """
        state = build_marker_visual_state(
            self._app,
            overlay_state_pool=self._overlay_state_pool,
            system_stats=self._system_stats,
            person_detector=self._person_detector,
            cam_params_buffer=self._cam_params_buffer,
        )

        # Atomic swap: release old state back to pool
        self._old_overlay_state = prepare_overlay_state_swap(
            overlay_state_pool=self._overlay_state_pool,
            old_overlay_state=self._old_overlay_state,
            new_overlay_state=state,
        )

        # GIL-atomic assignment (GStreamer thread reads this snapshot).
        # ``init_video`` assigns ``self._overlay_renderer`` before this path
        # runs (frame tick is gated by canvas readiness), so the None arm is
        # unreachable at runtime.
        if self._overlay_renderer is not None:  # pragma: no branch
            self._overlay_renderer.state = state

    def apply_detection_pin(self, dt: float = _NOMINAL_FRAME_DT) -> None:
        """Drive controlled marker(s) from detection with EMA smoothing.

        ``dt`` (seconds since the previous animate frame) keeps the smoothing /
        prediction frame-rate-independent.
        """
        apply_detection_pin_helper(
            self._app,
            person_detector=self._person_detector,
            unproject_cam_buffer=self._unproject_cam_buffer,
            screen_point_buffer=self._screen_point_buffer,
            dt=dt,
        )

    def init_zone_engine(self) -> None:
        """Create the zone engine from config, wired to the shared OSC service."""
        from openfollow.zones import ZoneEngine

        cfg = self._app._config.trigger_zones
        self._zone_engine = ZoneEngine(
            cfg,
            self._osc_service,
            self._app._config.osc_destinations,
        )

    def update_zone_triggers(self) -> None:
        """Evaluate zone membership and emit OSC, throttled by ``eval_fps``."""
        if self._zone_engine is None:
            return
        app = self._app
        cfg = app._config.trigger_zones
        if not cfg.enabled:
            return

        now = time.monotonic()
        interval = 1.0 / max(1, int(cfg.eval_fps))
        if now - self._last_zone_eval < interval:
            return
        self._last_zone_eval = now

        marker_positions = self._collect_marker_positions()
        detection_positions = self._collect_detection_positions()
        self._zone_engine.update(marker_positions, detection_positions)

    def _collect_marker_positions(self) -> list[tuple[tuple[str, int], float, float]]:
        """PSN-absolute XY positions of all viewable markers (controlled + remote).

        ``marker.pos`` is the single canonical frame for marker state:
        PSN-absolute world coordinates, matching the frame PSN packets use,
        the frame the renderer projects from, and the frame zone vertices
        are stored in. No grid-offset translation here.
        """
        app = self._app
        controlled = set(app._controlled_ids)
        result: list[tuple[tuple[str, int], float, float]] = []
        for tid in app._viewer_ids:
            if tid in controlled:
                marker = app._server.get_marker(tid) if app._server is not None else None
            else:
                marker = app._psn_receiver.get_marker(tid) if app._psn_receiver is not None else None
            if marker is None:
                continue
            if tid not in controlled and app._psn_receiver is not None:
                if not app._psn_receiver.is_marker_online(tid):
                    continue
            pos = marker.pos
            result.append(
                (
                    ("marker", int(tid)),
                    float(pos[0]),
                    float(pos[1]),
                )
            )
        return result

    def _collect_detection_positions(self) -> list[tuple[tuple[str, int], float, float]]:
        """World-XY foot positions of every visible AI detection."""
        app = self._app
        detector = self._person_detector
        if detector is None:
            return []
        detections = detector.detections
        if not detections:
            return []
        receiver = app._video_receiver
        if receiver is None:
            return []
        w, h = receiver.resolution
        if w <= 0 or h <= 0:
            return []
        if app._camera is None:
            return []

        from openfollow.scene.solver import invert_overlay_distortion, unproject_to_plane

        cam_cfg = app._camera.to_config()
        # Lens-distortion coefficients live on the config (the pinhole Camera
        # doesn't carry them), so read them straight from there.
        lens = app._config.camera
        params = self._zone_cam_buffer
        params[0] = cam_cfg.pos_x
        params[1] = cam_cfg.pos_y
        params[2] = cam_cfg.pos_z
        params[3] = cam_cfg.pitch
        params[4] = cam_cfg.yaw
        params[5] = cam_cfg.roll
        params[6] = cam_cfg.fov

        plane_z = app._config.grid.z_offset
        screen_pt = self._zone_screen_buffer
        result: list[tuple[tuple[str, int], float, float]] = []
        for det in detections:
            screen_pt[0, 0] = (det.x1 + det.x2) / 2.0 * w
            screen_pt[0, 1] = det.y2 * h  # foot position
            # The detection sits on the (lens-distorted) video, so undistort the
            # foot point back to the pinhole frame before unprojecting. Identity
            # when no lens distortion is configured.
            undistorted = invert_overlay_distortion(screen_pt, float(w), float(h), lens.lens_k1, lens.lens_k2)
            world = unproject_to_plane(params, undistorted, float(w), float(h), plane_z)
            if not np.all(np.isfinite(world[0])):
                continue
            # unproject_to_plane returns PSN-absolute world coords, matching
            # the zone vertex frame.
            wx = float(world[0, 0])
            wy = float(world[0, 1])
            # Detections without an assigned track_id use a negative index; that
            # works for a single-frame classification but prevents enter/exit
            # tracking across evaluations. Use -1 for all untracked boxes and
            # rely on the stable track_id otherwise.
            #
            # Consequence: simultaneous untracked boxes collapse to a single
            # ("detection", -1) entity in the engine's occupant set. Do not
            # "fix" this by generating synthetic per-box IDs – that would
            # make every frame look like a full enter/exit cycle.
            tid = int(det.track_id) if det.track_id >= 0 else -1
            result.append((("detection", tid), wx, wy))
        return result

    def _get_zone_states_snapshot(self) -> list[tuple[int, bool, int]]:
        """Return zone occupancy for web API – safe to call from web thread."""
        if self._zone_engine is None:
            return []
        return self._zone_engine.get_zone_states()

    # Diagnostics tab providers. Both run on the web thread; the engine's
    # underlying reads are GIL-atomic per ``ZoneEngine.get_zone_diagnostics``.
    def _get_zone_diagnostics_snapshot(
        self,
        index: int,
    ) -> dict[str, Any] | None:
        if self._zone_engine is None:
            return None
        return self._zone_engine.get_zone_diagnostics(index)

    def _zone_test_send(self, index: int, which: str) -> dict[str, Any]:
        """Force a one-shot OSC send for the zone at ``index`` on the
        ``which`` field (``"first"`` / ``"additional"`` / ``"partial"``
        / ``"final"``). Routed through the same ``OscService.send``
        path the engine uses so the operator hears in the test exactly
        what they'd hear in production. An empty / unconfigured field
        returns ``{"skipped": True}`` instead of an error – same
        lenient contract the engine applies on real transitions."""
        from openfollow.osc.parser import coerce_osc_args, tokenize_osc_message

        # Field-name lookup mirrors the route layer; keep in sync. The
        # route validates ``which`` first, but this guard makes the helper
        # safe to call directly from tests.
        field_map = {
            "first": "osc_address_first_entry",
            "additional": "osc_address_additional_entry",
            "partial": "osc_address_partial_exit",
            "final": "osc_address_final_exit",
        }
        field_name = field_map.get(which)
        if field_name is None:
            return {"error": f"unknown which: {which}"}
        cfg = self._app._config.trigger_zones
        if index < 0 or index >= len(cfg.zones):
            return {"error": "Zone index out of range"}
        zone = cfg.zones[index]
        raw = getattr(zone, field_name)
        if not raw:
            return {"skipped": True, "reason": "field is empty"}
        try:
            address, str_args = tokenize_osc_message(raw)
        except ValueError as exc:
            return {"error": f"unclosed quote in field: {exc}"}
        if not address:
            return {"skipped": True, "reason": "field has no address"}
        dest = self._app._config.osc_destinations.get(zone.destination_id)
        if dest is None:
            return {"skipped": True, "reason": "no destination selected"}
        typed_args = coerce_osc_args(str_args)
        self._osc_service.send(
            address,
            args=typed_args,
            host=dest.host,
            port=dest.port,
            protocol=dest.protocol,
            framing=dest.framing,
        )
        return {
            "success": True,
            "address": address,
            "args": list(typed_args),
            "host": dest.host,
            "port": dest.port,
        }

    def _get_marker_positions_snapshot(self) -> list[tuple[int, float, float]]:
        """Return PSN-absolute marker positions for the zone editor."""
        try:
            positions = self._collect_marker_positions()
        except Exception:  # noqa: BLE001
            return []
        return [(tid, x, y) for (_kind, tid), x, y in positions]

    def update_window_title(self, title: str) -> None:
        self._apply_window_title(title)

    def apply_window_size_change(self, width: int, height: int) -> None:
        """Resize the live GTK window to ``(width, height)`` pixels.

        Mirrors the ``max(1, int(...))`` clamp ``init_canvas`` applies
        at startup so a hand-edited zero/negative TOML value doesn't
        propagate into a Gdk geometry call. Caller is on the GTK main
        thread (hot-reload runs from the animation tick, which already
        asserts ``app._canvas is not None``).
        """
        canvas = self._app._canvas
        if canvas is None:  # pragma: no cover
            return
        canvas.apply_window_size(
            max(1, int(width)),
            max(1, int(height)),
        )

    def _default_runtime_stats_snapshot(self) -> dict[str, Any]:
        cfg = self._app._config
        return {
            "timestamp": float(time.time()),
            "system": {
                "cpu_percent": 0.0,
                "ram_percent": 0.0,
                "temperature_c": None,
                "ip": "N/A",
            },
            "video": {
                "source_type": cfg.video_source_type,
                "source_label": "",
                "pipeline_state": "disconnected",
                "connected": False,
                "reconnect_attempt": 0,
                "error_message": "",
                "resolution": {"width": 0, "height": 0},
                "source_selection_active": False,
                "fps": 0.0,
                "source_fps": 0.0,
            },
            "controllers": {
                "connected_count": 0,
                "mapped_count": 0,
                "items": [],
            },
            "playback": self._frame_metrics.snapshot(),
            "tracking": {
                "enabled": bool(cfg.detection.enabled),
                "available": False,
                "running": False,
                "model": cfg.detection.model,
                "interval_ms": int(cfg.detection.interval_ms),
                "inference_count": 0,
                "inference_hz": 0.0,
                "inference_avg_ms": 0.0,
                "inference_p95_ms": 0.0,
                "inference_max_ms": 0.0,
                "inference_last_ms": 0.0,
                "inference_errors": 0,
                "sample_timeouts": 0,
                "sample_failures": 0,
                "detections_last": 0,
                "detections_avg": 0.0,
                "tracked_people": 0,
                "pinned_track_id": None,
                "last_inference_age_ms": None,
                "missing_deps": [],
            },
        }

    def _resolve_detection_missing_deps(
        self,
        detection_cfg: Any,
        now_monotonic: float,
    ) -> list[str]:
        signature = repr(detection_cfg)
        cached = self._detection_deps_cache
        if (
            cached is not None
            and self._detection_deps_cache_signature == signature
            and (now_monotonic - self._detection_deps_cache_at) < self._detection_deps_cache_ttl
        ):
            return list(cached)
        from openfollow.video.detection import check_detection_dependencies

        fresh = check_detection_dependencies(detection_cfg)
        self._detection_deps_cache = list(fresh)
        self._detection_deps_cache_signature = signature
        self._detection_deps_cache_at = now_monotonic
        return fresh

    def publish_runtime_stats(self, *, force: bool = False) -> None:
        """Publish a thread-safe runtime telemetry snapshot for the web UI."""
        now_monotonic = time.monotonic()
        if (
            not force
            and self._last_runtime_stats_publish > 0.0
            and now_monotonic - self._last_runtime_stats_publish < self._runtime_stats_interval
        ):
            return
        self._last_runtime_stats_publish = now_monotonic

        app = self._app
        cfg = app._config
        system_stats = self._system_stats.stats if self._system_stats is not None else None

        cpu_percent = float(system_stats.cpu_percent) if system_stats is not None else 0.0
        ram_percent = float(system_stats.ram_percent) if system_stats is not None else 0.0
        temperature = (
            float(system_stats.temperature)
            if system_stats is not None and system_stats.temperature is not None
            else None
        )
        ip_address = system_stats.ip_address if system_stats is not None else "N/A"

        fps = 0.0
        if self._overlay_renderer is not None:
            fps = float(self._overlay_renderer.measured_fps())

        video_snapshot: dict[str, Any] = {
            "source_type": cfg.video_source_type,
            "source_label": "",
            "pipeline_state": "disconnected",
            "connected": False,
            "reconnect_attempt": 0,
            "error_message": "",
            "resolution": {"width": 0, "height": 0},
            "source_selection_active": False,
            "fps": fps,
            "source_fps": 0.0,
        }
        receiver = getattr(app, "_video_receiver", None)
        if receiver is not None:
            status = receiver.status_marker
            width, height = receiver.resolution
            video_snapshot = {
                "source_type": cfg.video_source_type,
                "source_label": receiver.source_name,
                "pipeline_state": status.status.name.lower(),
                "connected": bool(status.is_connected),
                "reconnect_attempt": int(status.reconnect_attempt),
                "error_message": status.error_message,
                "resolution": {"width": int(width), "height": int(height)},
                "source_selection_active": bool(receiver.source_selection_active),
                "fps": fps,
                "source_fps": float(getattr(receiver, "source_framerate", 0.0)),
            }

        controller_items: list[dict[str, Any]] = []
        input_manager = getattr(app, "_input_manager", None)
        if input_manager is not None:
            for item in input_manager.get_controller_info():
                controller_items.append(
                    {
                        "controller_index": int(item["controller_index"]),
                        "name": str(item["name"]),
                        "connected": bool(item["connected"]),
                        "marker_id": (int(item["marker_id"]) if item["marker_id"] is not None else None),
                        "effective_speed": float(item["effective_speed"]),
                        "backend": str(item["backend"]),
                    }
                )

        playback_snapshot = self._frame_metrics.snapshot()

        detection_snapshot: dict[str, Any] = {
            "enabled": bool(cfg.detection.enabled),
            "available": False,
            "running": False,
            "model": cfg.detection.model,
            "interval_ms": int(cfg.detection.interval_ms),
            "inference_count": 0,
            "inference_hz": 0.0,
            "inference_avg_ms": 0.0,
            "inference_p95_ms": 0.0,
            "inference_max_ms": 0.0,
            "inference_last_ms": 0.0,
            "inference_errors": 0,
            "sample_timeouts": 0,
            "sample_failures": 0,
            "detections_last": 0,
            "detections_avg": 0.0,
            "tracked_people": 0,
            "pinned_track_id": None,
            "last_inference_age_ms": None,
        }
        if self._person_detector is not None:
            detection_snapshot = dict(self._person_detector.performance_stats)
        detection_snapshot["missing_deps"] = self._resolve_detection_missing_deps(
            cfg.detection,
            now_monotonic,
        )

        snapshot = {
            "timestamp": float(time.time()),
            "system": {
                "cpu_percent": cpu_percent,
                "ram_percent": ram_percent,
                "temperature_c": temperature,
                "ip": ip_address,
            },
            "video": video_snapshot,
            "controllers": {
                "connected_count": int(sum(1 for item in controller_items if item["connected"])),
                "mapped_count": int(sum(1 for item in controller_items if item["marker_id"] is not None)),
                "items": controller_items,
            },
            "playback": playback_snapshot,
            "tracking": detection_snapshot,
        }

        with self._runtime_stats_lock:
            self._runtime_stats_snapshot = snapshot

    def get_runtime_stats_snapshot(self) -> dict[str, Any]:
        """Return a defensive copy of the latest runtime telemetry snapshot."""
        with self._runtime_stats_lock:
            return copy.deepcopy(self._runtime_stats_snapshot)

    def _safe_stop(self, name: str, fn: Callable[[], Any]) -> None:
        """Run one teardown step, logging and swallowing any exception.

        ``shutdown`` is a flat sequence of subsystem stops; without this a
        single raising ``stop()`` (GStreamer/GTK teardown can throw) would
        skip every later teardown – most importantly the final
        ``_osc_service.shutdown()`` drain and ``_midi.shutdown()`` port
        close – and, on the ``os.execv`` re-exec path, leak threads still
        holding sockets / multicast groups / rtmidi ports into the new
        process (``address already in use`` / doubly-opened ports).
        """
        try:
            fn()
        except Exception:
            logger.exception("Error stopping %s during shutdown", name)

    def _stop_osc_transmitter_manager(self) -> None:
        """Stop the transmitter scheduler before draining the OSC service
        so it can finish any in-flight tick rather than racing with socket
        close. On a join timeout (wedged in a slow TCP send) log and
        proceed – we're on the app-exit path, the scheduler is a daemon
        thread, and any sockets it holds are reaped at process exit. Keep
        the reference on timeout so the still-alive thread stays observable.
        """
        if self._osc_transmitter_manager is None:  # pragma: no cover - caller guards
            return
        stopped = self._osc_transmitter_manager.stop()
        if stopped:
            self._osc_transmitter_manager = None
        else:
            logger.warning(
                "OSC transmitter scheduler did not stop in time; "
                "draining OSC service anyway – any in-flight ticks "
                "may now race with socket close (sockets are reclaimed "
                "at process exit).",
            )

    def shutdown(self) -> None:
        if self._shutdown_in_progress:
            return
        self._shutdown_in_progress = True
        app = self._app
        # Each step is isolated (see ``_safe_stop``) so one failing
        # teardown can't strand the rest – the OSC-service drain and MIDI
        # close at the end must always run.
        if self._person_detector is not None:
            self._safe_stop("person detector", self._person_detector.stop)
        if app._input_manager is not None:
            self._safe_stop("input manager", app._input_manager.stop)
        # Stop catalog sync before the web server so the receive
        # thread doesn't keep buffering packets while the rest of
        # the app is tearing down.
        sync = getattr(app, "_marker_catalog_sync", None)
        if sync is not None:
            self._safe_stop("marker catalog sync", sync.stop)
        if app._web_server is not None:
            self._safe_stop("web server", app._web_server.stop)
        if app._video_receiver is not None:
            self._safe_stop("video receiver", app._video_receiver.stop)
        if app._otp_server is not None:
            self._safe_stop("OTP server", app._otp_server.stop)
        if app._rttrpm_server is not None:
            self._safe_stop("RTTrPM server", app._rttrpm_server.stop)
        if app._server is not None:
            self._safe_stop("PSN server", app._server.stop)
        if app._psn_receiver is not None:
            self._safe_stop("PSN receiver", app._psn_receiver.stop)
        if self._osc_transmitter_manager is not None:
            self._safe_stop("OSC transmitter scheduler", self._stop_osc_transmitter_manager)
        # Drain the shared OSC service before MIDI so final cleanup-time
        # sends from OSC-using subsystems (transmitter manager, zone
        # engine) still go out. MIDI is closed afterwards because nothing
        # feeds it on shutdown – the rtmidi listener threads only deliver
        # inbound events, so dropping them last loses no observer.
        self._safe_stop("OSC service", self._osc_service.shutdown)
        # Drop the MIDI → fader subscription before teardown. ``shutdown``
        # clears all subscribers anyway, but detaching explicitly keeps the
        # dispatcher's own state honest.
        self._safe_stop("MIDI fader dispatcher", self._midi_fader_dispatcher.detach)
        self._safe_stop("MIDI", self._midi.shutdown)
