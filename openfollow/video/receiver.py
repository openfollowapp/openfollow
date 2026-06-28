# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""GStreamer native sink receiver with HUD overlay via gtksink."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from openfollow.runtime.receiver_bus import ReceiverBusHandler
from openfollow.runtime.receiver_pipeline import ReceiverPipelineAssembler
from openfollow.runtime.receiver_state import ReceiverStateMachine
from openfollow.video.connection_status import NdiStatusMarker
from openfollow.video.inputs import get_input_class
from openfollow.video.inputs._base import InputCapabilities, ReconnectPolicy

if TYPE_CHECKING:
    from openfollow.video.detection import PersonDetector
    from openfollow.video.inputs._base import VideoInputBase
    from openfollow.video.overlay import CairoOverlayRenderer
    from openfollow.video.preview import PreviewProvider, SnapshotProvider

try:
    import gi

    gi.require_version("Gst", "1.0")
    gi.require_version("GstApp", "1.0")
    from gi.repository import GLib, Gst, GstApp  # noqa: F401
except Exception:  # pragma: no cover - depends on system gstreamer bindings
    Gst = None
    GLib = None

logger = logging.getLogger(__name__)

# Timeout passed to ``pipeline.get_state()`` inside the swap worker thread
# (nanoseconds). Must be long enough for a healthy NULL transition to
# complete; if exceeded the thread stays alive past ``t.join(2.0)`` and
# ``swap_input`` raises ``PipelineStuckError``.
_NULL_CONFIRM_TIMEOUT_NS: int = 2_000_000_000  # 2 s in nanoseconds


def gst_runtime_available() -> bool:
    return Gst is not None


class PipelineStuckError(OSError):
    """Pipeline NULL transition/discovery stuck (timeout exceeded).

    Do NOT rollback – old pipeline still owns gtksink. Keep receiver
    attached and revert config; retry or restart to resolve.
    """


# Backward-compat shim – callers that import ``discover_sources`` from here
# (e.g. the ``/ndi/sources`` web route) will continue to work.
def discover_sources(timeout: float = 0.1) -> list[str]:  # noqa: D401
    """Scan for available NDI sources (delegates to the NDI plugin)."""
    cls = get_input_class("ndi")
    if cls is None:
        return []
    return cls.discover_sources(timeout=timeout)


class GstNativeSinkReceiver:
    """Displays video via GStreamer gtksink with Cairo overlay.

    Satisfies the ``VideoReceiver`` protocol.  Delegates protocol-specific
    work to the active :class:`VideoInputBase` plugin.
    """

    def __init__(
        self,
        source_type: str = "ndi",
        input_config: dict[str, Any] | None = None,
        reconnect_delay: float = 1.0,
        overlay_renderer: CairoOverlayRenderer | None = None,
        on_widget_changed: Callable[..., Any] | None = None,
        config_path: str = "config.toml",
        detector: PersonDetector | None = None,
        preview_provider: PreviewProvider | None = None,
        snapshot_provider: SnapshotProvider | None = None,
        stall_timeout: float | None = None,
        heal_interval: float | None = None,
    ) -> None:
        # Resolve the active video input plugin
        input_cls = get_input_class(source_type)
        if input_cls is None:
            raise ValueError(f"Unknown video input type: {source_type!r}")

        # User overrides for the recovery timers (from AppConfig). ``None``
        # keeps the per-plugin policy default. Stored so they survive a
        # ``swap_input`` (which re-reads the new plugin's policy).
        self._stall_timeout_override = stall_timeout
        self._heal_interval_override = heal_interval

        self._input: VideoInputBase = input_cls()
        self._input_caps: InputCapabilities = input_cls.capabilities()
        self._reconnect_policy: ReconnectPolicy = input_cls.reconnect_policy()
        self._apply_policy_overrides()
        self._source_type = source_type
        self._input_config: dict[str, Any] = input_config or {}

        self._config_path = config_path
        self._reconnect_delay = max(0.1, reconnect_delay)
        self._state = ReceiverStateMachine(reconnect_delay=self._reconnect_delay)
        self._overlay_renderer = overlay_renderer
        self._on_widget_changed = on_widget_changed
        self._detector = detector
        self._preview_provider = preview_provider
        self._snapshot_provider = snapshot_provider

        self._pipeline = None
        self._discovery_source_id: int | None = None
        self._heal_source_id: int | None = None
        self._watchdog_source_id: int | None = None
        # Monotonic timestamp of the last decoded frame seen at the sink,
        # written by the BUFFER pad probe (streaming thread) and read by the
        # silent-stall watchdog (main thread). 0.0 until the first frame.
        self._last_frame_monotonic: float = 0.0

        # Source discovery and selection
        self._discovered_sources: list[str] = []
        self._selected_source_index: int = 0
        self._discovery_thread: threading.Thread | None = None
        self._discovery_lock = threading.Lock()
        self._discovery_running = False

        # Status tracking for UI feedback
        self._status_marker = NdiStatusMarker()
        source_label = self._input.get_source_label(self._input_config)
        if not source_label:
            self._status_marker.set_disconnected(f"No {self._input.display_name} source configured")
        else:
            self._status_marker.set_disconnected()

        # Shared sink created lazily
        self._shared_sink = None
        # The gtksink is shared across pipeline rebuilds, and pad probes live
        # on its (persistent) sink pad – so attach them exactly once. Without
        # this guard every reconnect/heal rebuild would stack another pair of
        # probes, multiplying per-buffer/CAPS callbacks over a long run.
        self._sink_probes_added = False
        self._pipeline_assembler = ReceiverPipelineAssembler(
            gst=Gst,
            logger=logger,
            overlay_renderer=self._overlay_renderer,
            detector=self._detector,
            prepare_sink=self._prepare_sink,
            preview_provider=self._preview_provider,
            snapshot_provider=self._snapshot_provider,
        )
        self._bus_handler = ReceiverBusHandler(
            gst=Gst,
            logger=logger,
            get_pipeline=lambda: self._pipeline,
            on_async_done=self._handle_bus_async_done,
            on_error=self._handle_bus_error,
            on_eos=self._handle_bus_eos,
            is_placeholder_pipeline=lambda: self._state.is_placeholder_pipeline,
            get_input_display_name=lambda: self._input.display_name,
        )

    # -- Plugin-driven properties ---------------------------------------------

    @property
    def has_source_selection(self) -> bool:
        """Whether the active input supports source selection."""
        return self._input_caps.has_source_selection

    @property
    def source_selection_hotkey(self) -> str:
        """Keyboard shortcut to enter source selection (empty = none)."""
        return self._input_caps.hotkey

    @property
    def source_selection_title(self) -> str:
        """Title for the source-selection overlay."""
        return self._input_caps.selection_title

    # -- Shared sink management -----------------------------------------------

    def _create_shared_sink(self) -> Any:
        """Create a single gtksink that will be reused across all pipelines."""
        Gst.init(None)

        sink_name = self._select_sink_name()
        if sink_name is None:
            logger.error("No embeddable video sink available")
            return None

        sink = Gst.ElementFactory.make(sink_name, "shared_videosink")
        if sink is None:
            logger.error("Failed to create %s video sink", sink_name)
            return None

        try:
            sink.set_property("sync", False)
        except Exception:
            pass

        logger.info("Created shared %s element for all pipelines", sink_name)
        return sink

    def _get_shared_sink(self) -> Any:
        """Get the shared sink, creating it if needed."""
        if self._shared_sink is None:
            self._shared_sink = self._create_shared_sink()
        return self._shared_sink

    def _prepare_sink(self) -> Any:
        """Detach the shared sink from any previous pipeline and return it."""
        sink = self._get_shared_sink()
        if sink is None:
            return None
        parent = sink.get_parent()
        if parent is not None:
            parent.remove(sink)
        try:
            sink.set_property("sync", False)
        except Exception:
            pass
        return sink

    @staticmethod
    def _select_sink_name() -> str | None:
        """Select a video sink that can be embedded in our GTK window."""
        if Gst.ElementFactory.find("gtksink"):
            return "gtksink"
        return None

    # -- VideoReceiver protocol -----------------------------------------------

    @property
    def connected(self) -> bool:
        return self._state.connected

    @property
    def resolution(self) -> tuple[int, int]:
        return self._state.resolution

    @property
    def source_framerate(self) -> float:
        """Return framerate declared in the negotiated video caps (0.0 if unknown)."""
        return self._state.source_framerate

    @property
    def status_marker(self) -> NdiStatusMarker:
        return self._status_marker

    @property
    def source_name(self) -> str:
        """Return the active source label."""
        return self._input.get_source_label(self._input_config)

    def _has_configured_source(self) -> bool:
        """Return True when the active input has a non-empty primary source value."""
        fields = self._input.config_fields()
        if not fields:
            return bool(self._input.get_source_label(self._input_config).strip())
        primary_field = fields[0].name
        value = self._input_config.get(primary_field, "")
        return bool(str(value).strip())

    def _reset_video_flow_state(self) -> None:
        self._state.reset_video_flow()

    def _handle_video_connected(self) -> None:
        """Mark feed connected on first real frame from non-placeholder pipeline.

        Driven by actual buffer flow, not CAPS events, to avoid false
        connections from sticky CAPS re-delivery on the shared sink.
        """
        self._cancel_connection_timeout()
        self._status_marker.set_connected(self.source_name)
        width, height = self._state.resolution
        logger.info("Video flowing – marking as connected (%dx%d)", width, height)
        self._start_watchdog()

    @property
    def discovered_sources(self) -> list[str]:
        with self._discovery_lock:
            return list(self._discovered_sources)

    @property
    def selected_source_index(self) -> int:
        with self._discovery_lock:
            return self._selected_source_index

    @property
    def source_selection_active(self) -> bool:
        return self._state.source_selection_active

    def start(self) -> None:
        self.play()

    def stop(self) -> None:
        self._cancel_connection_timeout()
        self._cancel_reconnect()
        self._cancel_heal()
        self._cancel_watchdog()
        self._cancel_discovery()
        if self._discovery_thread is not None:
            self._discovery_thread.join(timeout=3.0)
            self._discovery_thread = None
        if self._on_widget_changed is not None:
            self._on_widget_changed(None)
        if self._pipeline is not None:
            pipeline = self._pipeline
            self._pipeline = None
            # Drop the bus signal watch so the pipeline (and its sockets) is
            # freed once NULL completes – otherwise stop() leaks fds.
            self._bus_handler.teardown_bus(pipeline)
            # set_state(NULL) can block indefinitely on network pipelines (RTSP/SRT)
            # when the remote end is unresponsive. Run in a daemon thread with a
            # timeout so shutdown never enters an uninterruptible kernel wait.
            t = threading.Thread(
                target=lambda: pipeline.set_state(Gst.State.NULL),
                daemon=True,
                name="GstStopPipeline",
            )
            t.start()
            t.join(timeout=2.0)
            if t.is_alive():
                # The NULL transition is wedged (unresponsive network source).
                # We abandon the daemon thread so shutdown isn't blocked, but it
                # keeps the pipeline + its sockets/fds alive until the process
                # exits – log it so a flapping source's accumulated leak is
                # visible in the diagnostics bundle.
                logger.warning(
                    "%s: pipeline NULL transition did not complete within 2s on stop – "
                    "abandoning thread; its pipeline/sockets stay alive until process exit.",
                    self._input.display_name,
                )
        self._reset_video_flow_state()
        self._state.deactivate_source_selection()
        self._status_marker.set_disconnected("Stopped")
        self._input.cleanup()

    def _null_transition_current_pipeline(self, *, swap_label: str) -> None:
        """Tear down current pipeline via NULL transition with 2s timeout.

        Used by swap_input and swap_detection_branch to guarantee the prior
        pipeline reaches NULL before handing the shared sink to a new one.

        On success: ``self._pipeline`` is cleared. On stuck pipeline: raises
        ``PipelineStuckError`` with ``self._pipeline`` still set so retries
        re-enter the guard. Updates ``status_marker`` on failure.
        """
        if self._pipeline is None:
            return
        pipeline = self._pipeline

        confirmed: list[bool] = []

        def _null_and_confirm() -> None:
            pipeline.set_state(Gst.State.NULL)
            state_result, current_state, _pending = pipeline.get_state(
                _NULL_CONFIRM_TIMEOUT_NS,
            )
            confirmed.append(state_result == Gst.StateChangeReturn.SUCCESS and current_state == Gst.State.NULL)

        t = threading.Thread(
            target=_null_and_confirm,
            daemon=True,
            name=f"GstSwap-{swap_label}",
        )
        t.start()
        t.join(timeout=2.0)
        # Three failure modes converge on the same recovery –
        # leave ``self._pipeline`` attached so a retry re-enters
        # this guard, raise ``PipelineStuckError`` so the
        # orchestrator skips its rollback (a rollback would race
        # on the still-alive shared sink):
        #   1. ``set_state`` itself hung →   t.is_alive() is True.
        #   2. ``get_state`` budget expired with the pipeline
        #      still in ASYNC → t.is_alive() is False but the
        #      thread appended ``False``.
        #   3. ``set_state`` raised before reaching ``get_state``
        #      (extremely rare – daemon threads silently drop
        #      uncaught exceptions) → ``confirmed`` stays empty.
        if t.is_alive() or not confirmed or not confirmed[0]:
            error_message = (
                f"{swap_label}: prior pipeline did not reach NULL "
                "within 2 s – refusing to hand the shared sink to "
                "a new pipeline while the old one may still be "
                "running"
            )
            self._status_marker.set_disconnected(error_message)
            raise PipelineStuckError(error_message)
        # Stop late messages from the old pipeline bus being dispatched into
        # the next play/swap cycle after ``self._pipeline`` is repointed.
        self._bus_handler.teardown_bus(pipeline)
        self._pipeline = None

    def swap_input(
        self,
        source_type: str,
        input_config: dict[str, Any],
    ) -> None:
        """Hot-swap the active input plugin and rebuild the pipeline.

        Tears down current pipeline, cleans up prior plugin resources,
        swaps to the new plugin, and rebuilds the chain. The shared
        ``gtksink`` widget survives in the canvas (brief blackout
        instead of full re-embed). Only runs on GTK main thread.

        Raises ``ValueError`` if ``source_type`` is unknown.
        """
        new_input_cls = get_input_class(source_type)
        if new_input_cls is None:
            raise ValueError(f"Unknown video input type: {source_type!r}")

        # Tear down the current pipeline + plugin without detaching
        # the shared sink's widget – keeps it embedded across the
        # swap so the canvas doesn't flash.
        self._cancel_connection_timeout()
        self._cancel_reconnect()
        self._cancel_heal()
        self._cancel_watchdog()
        self._cancel_discovery()
        if self._discovery_thread is not None:
            discovery_thread = self._discovery_thread
            discovery_thread.join(timeout=3.0)
            if discovery_thread.is_alive():
                error_message = (
                    "swap_input: prior discovery thread did not stop "
                    "within 3 s – refusing to rebuild input state "
                    "while discovery may still be running"
                )
                self._status_marker.set_disconnected(error_message)
                raise PipelineStuckError(error_message)
            self._discovery_thread = None
        self._null_transition_current_pipeline(swap_label="swap_input")
        self._reset_video_flow_state()
        self._state.reset_reconnect_backoff()
        self._state.deactivate_source_selection()
        self._input.cleanup()

        # Only plugin-specific state; bus handler/assembler auto-pick up identity.
        self._input = new_input_cls()
        self._input_caps = new_input_cls.capabilities()
        self._reconnect_policy = new_input_cls.reconnect_policy()
        self._apply_policy_overrides()
        self._source_type = source_type
        self._input_config = dict(input_config)

        # Drop any cached preview/snapshot frame from the old source so a
        # request during the new source's connect window returns "no feed"
        # instead of the previous source's stale frame (e.g. the Setup Wizard
        # preview after switching the video source).
        if self._snapshot_provider is not None:
            self._snapshot_provider.clear_cache()
        if self._preview_provider is not None:
            self._preview_provider.clear_cache()

        # Reset per-plugin state that doesn't carry over across a
        # plugin swap (sources discovered for the old type are
        # meaningless for the new one; the placeholder flag will be
        # re-set by ``play()`` if the new plugin needs a placeholder).
        with self._discovery_lock:
            self._discovered_sources = []
            self._selected_source_index = 0
        self._state.set_placeholder_pipeline(False)

        source_label = self._input.get_source_label(self._input_config)
        if not source_label:
            self._status_marker.set_disconnected(f"No {self._input.display_name} source configured")
        else:
            self._status_marker.set_disconnected()

        # Build the new pipeline + start it. ``play()`` handles every
        # input-type-specific case (no source configured, source-
        # selection placeholder, ready-to-go) and (re-)embeds the
        # shared sink widget via ``on_widget_changed`` when needed.
        # ``embed_widget`` no-ops when the widget is already parented,
        # so re-embedding is free during a swap that lands on the
        # same shared sink.
        self.play()

    def swap_detection_branch(
        self,
        new_detector: PersonDetector | None,
    ) -> None:
        """Hot-swap the person detector and rebuild the pipeline.

        Symmetric to ``swap_input`` but only the detector changes.
        The shared ``gtksink`` widget survives. Stops old detector's
        worker after NULL transition (when appsink is detached), then
        starts new detector's worker after rebuild succeeds.

        Raises ``PipelineStuckError`` if pipeline doesn't reach NULL
        within 2s (pipeline reference NOT cleared on failure).
        """
        self._cancel_connection_timeout()
        self._cancel_reconnect()
        self._cancel_heal()
        self._cancel_watchdog()

        self._null_transition_current_pipeline(
            swap_label="swap_detection_branch",
        )
        self._reset_video_flow_state()

        old_detector = self._detector
        if old_detector is not None:
            old_detector.stop()

        self._detector = new_detector
        self._pipeline_assembler.set_detector(new_detector)

        self.play()

        if new_detector is not None:
            new_detector.start()

    # -- Public API (main thread) ---------------------------------------------

    def create_pipeline(self) -> None:
        """Build the GStreamer pipeline via the active input plugin."""
        Gst.init(None)
        self._reset_video_flow_state()
        available, reason = self._input.__class__.is_available()
        if not available:
            logger.warning("%s is not available: %s", self._input.display_name, reason)
            self._status_marker.set_disconnected(reason)
            self._state.set_placeholder_pipeline(True)
            self._create_placeholder_pipeline()
            return
        try:
            self._pipeline = self._input.create_pipeline(
                config=self._input_config,
                sink=self._get_shared_sink(),
                build_overlay_tail=self._build_overlay_tail,
                prepare_sink=self._prepare_sink,
            )
        except Exception as e:
            logger.warning(
                "Failed to create %s pipeline: %s – falling back to placeholder",
                self._input.display_name,
                e,
            )
            self._status_marker.set_disconnected(str(e))
            self._state.set_placeholder_pipeline(True)
            self._create_placeholder_pipeline()
            return
        self._state.set_placeholder_pipeline(False)
        self._setup_bus()
        # Pad probe for resolution detection (backup for non-overlay mode)
        # ``create_pipeline`` either returned a Gst pipeline (assigned to
        # ``self._pipeline`` above) or raised, in which case the except
        # branch already returned. Strict typing can't track the success
        # path of the try across statements; assert documents the invariant.
        assert self._pipeline is not None
        # Attach the resolution/flow probes once for the shared sink's
        # lifetime – the sink (and its sink pad) survive every rebuild, so
        # re-adding here each time would accumulate duplicate probes.
        if not self._sink_probes_added:
            sink = self._pipeline.get_by_name("videosink") or self._pipeline.get_by_name("shared_videosink")
            if sink is not None:
                pad = sink.get_static_pad("sink")
                if pad is not None:
                    pad.add_probe(Gst.PadProbeType.EVENT_DOWNSTREAM, self._on_pad_event)
                    # Stamp each decoded frame's arrival so the silent-stall
                    # watchdog can tell a frozen feed (no buffers, no error)
                    # from a healthy one.
                    pad.add_probe(Gst.PadProbeType.BUFFER, self._on_sink_buffer)
                    self._sink_probes_added = True

    def get_sink_widget(self) -> Any:
        """Return the GTK widget from shared gtksink."""
        sink = self._get_shared_sink()
        if sink is None:
            return None
        try:
            return sink.get_property("widget")
        except Exception:
            return None

    def play(self) -> None:
        """Set pipeline to PLAYING state."""
        caps = self._input_caps
        source_label = self._input.get_source_label(self._input_config)
        source_configured = self._has_configured_source()

        if not caps.has_source_selection:
            # Inputs without source selection (e.g. SRT, RTSP): start directly,
            # but show placeholder when no URL is configured.
            self._state.deactivate_source_selection()
            if not source_configured:
                self._status_marker.set_disconnected(f"No {self._input.display_name} URL configured")
                self._state.set_placeholder_pipeline(True)
                self._create_placeholder_pipeline()
                widget = self.get_sink_widget()
                if self._on_widget_changed is not None and widget is not None:
                    self._on_widget_changed(widget)
                if self._pipeline is not None:
                    self._pipeline.set_state(Gst.State.PLAYING)
                return
            if self._pipeline is None:
                self.create_pipeline()
            if self._pipeline is not None:
                result = self._pipeline.set_state(Gst.State.PLAYING)
                if result == Gst.StateChangeReturn.FAILURE:
                    bus = self._pipeline.get_bus()
                    msg = bus.timed_pop_filtered(200 * Gst.MSECOND, Gst.MessageType.ERROR)
                    if msg:
                        err, dbg = msg.parse_error()
                        error_msg = f"{err.message} – {dbg}"
                    else:
                        error_msg = f"{self._input.display_name} pipeline failed to start"
                    logger.error("%s pipeline error: %s", self._input.display_name, error_msg)
                    self._schedule_reconnect(error_msg)
                else:
                    if self._state.is_placeholder_pipeline:
                        logger.info(
                            "Placeholder pipeline started for %s input.",
                            self._input.display_name,
                        )
                    else:
                        self._status_marker.set_connecting(source_label)
                        timeout = self._reconnect_policy.connection_timeout
                        if timeout > 0:
                            self._start_connection_timeout()
                        logger.info(
                            "%s pipeline started – waiting for first frame from %s.",
                            self._input.display_name,
                            source_label,
                        )
            return

        # Input with source selection (e.g. NDI)
        if not source_label:
            # No source configured – show placeholder and start discovery
            self._state.activate_source_selection()
            self._status_marker.set_disconnected(f"Select a {self._input.display_name} source")
            self._create_placeholder_pipeline()

            widget = self.get_sink_widget()
            if self._on_widget_changed is not None and widget is not None:
                self._on_widget_changed(widget)

            if self._pipeline is not None:
                result = self._pipeline.set_state(Gst.State.PLAYING)
                if result == Gst.StateChangeReturn.FAILURE:
                    logger.error("Failed to start initial placeholder pipeline")
                else:
                    logger.info("Initial placeholder pipeline started")

            self._schedule_discovery()
            return

        self._state.deactivate_source_selection()
        self._cancel_discovery()
        self._status_marker.set_connecting(source_label)

        if self._pipeline is None:
            self.create_pipeline()
        if self._pipeline is not None:
            result = self._pipeline.set_state(Gst.State.PLAYING)
            if result == Gst.StateChangeReturn.FAILURE:
                logger.error("Pipeline set_state(PLAYING) returned FAILURE.")
                self._schedule_reconnect("Pipeline failed to start")
            else:
                if self._state.is_placeholder_pipeline:
                    logger.info("Placeholder pipeline started after source startup failure.")
                else:
                    self._state.deactivate_source_selection()
                    timeout = self._reconnect_policy.connection_timeout
                    if timeout > 0:
                        self._start_connection_timeout()
                    logger.info("Native sink pipeline started – waiting for first frame.")

    def set_source(self, source_name: str) -> None:
        """Change the source and attempt to connect."""
        source_name = source_name.strip()
        # Update the primary config field
        if self._input.config_fields():
            primary_field = self._input.config_fields()[0].name
            self._input_config[primary_field] = source_name

        self._state.reset_reconnect_backoff()
        self._state.deactivate_source_selection()

        self._cancel_connection_timeout()
        self._cancel_reconnect()
        self._cancel_heal()
        self._cancel_watchdog()
        self._cancel_discovery()
        # Tear down the prior pipeline through the same timed-thread +
        # NULL-confirmation guard as swap_input, so a network source whose
        # remote end is unresponsive can't block the main thread on
        # set_state(NULL), and we never hand the shared sink to the new
        # pipeline while the old one may still be running. The callers of
        # set_source don't handle PipelineStuckError, so abort the source
        # change here (status already surfaced) rather than propagating.
        try:
            self._null_transition_current_pipeline(swap_label="set_source")
        except PipelineStuckError:
            logger.warning("set_source: prior pipeline did not reach NULL – aborting source change")
            return
        self._reset_video_flow_state()

        source_label = self._input.get_source_label(self._input_config)
        if self._has_configured_source():
            logger.info(
                "Switching to %s source: %s",
                self._input.display_name,
                source_label,
            )
            try:
                self.create_pipeline()
                widget = self.get_sink_widget()
                if self._on_widget_changed is not None and widget is not None:
                    self._on_widget_changed(widget)
                self.play()
            except Exception as e:
                logger.warning("Failed to connect to source %s: %s", source_label, e)
                self._schedule_reconnect(str(e))
        else:
            self.play()

    # -- Source selection (controller navigation) -----------------------------

    def select_source_up(self) -> None:
        with self._discovery_lock:
            if self._discovered_sources and self._state.source_selection_active:
                self._selected_source_index = max(0, self._selected_source_index - 1)

    def select_source_down(self) -> None:
        with self._discovery_lock:
            if self._discovered_sources and self._state.source_selection_active:
                self._selected_source_index = min(
                    len(self._discovered_sources) - 1,
                    self._selected_source_index + 1,
                )

    def confirm_source_selection(self) -> bool:
        """Confirm the selected source and connect.  Returns True on success."""
        with self._discovery_lock:
            if not self._discovered_sources or not self._state.source_selection_active:
                return False
            if 0 <= self._selected_source_index < len(self._discovered_sources):
                source = self._discovered_sources[self._selected_source_index]
            else:
                return False
        logger.info("User selected %s source: %s", self._input.display_name, source)
        self.set_source(source)
        self._save_source_to_config(source)
        return True

    def _save_source_to_config(self, source_name: str) -> None:
        """Persist the selected source name back to the config file."""
        try:
            from openfollow.configuration import load_config, save_config

            cfg = load_config(self._config_path)
            if self._input.config_fields():
                primary_field = self._input.config_fields()[0].name
                setattr(cfg, primary_field, source_name)
            save_config(cfg, self._config_path)
            logger.info(
                "Saved %s source '%s' to %s",
                self._input.display_name,
                source_name,
                self._config_path,
            )
        except Exception as e:
            logger.warning("Failed to save source to config: %s", e)

    def enter_source_selection(self) -> None:
        """Enter source selection mode (only for inputs with discovery)."""
        if not self._input_caps.has_source_selection:
            return
        self._state.enter_source_selection()
        with self._discovery_lock:
            self._selected_source_index = 0
            self._discovered_sources = []
        self._schedule_discovery()

    def exit_source_selection(self) -> None:
        """Exit source selection mode without selecting a source."""
        self._state.deactivate_source_selection()
        self._cancel_discovery()

        source_label = self._input.get_source_label(self._input_config)
        if self._state.restore_connection_after_selection():
            self._status_marker.set_connected(source_label)
        elif source_label:
            self._schedule_reconnect("Reconnecting to previous source")
        else:
            self._status_marker.set_disconnected("Source selection cancelled")

    # -- Overlay tail (shared) ------------------------------------------------

    def _build_overlay_tail(self, pipeline: Any, convert: Any, sink: Any) -> None:
        """Link the display tail (with optional detection/preview tees)."""
        self._pipeline_assembler.build_overlay_tail(pipeline, convert, sink)

    # -- Placeholder pipeline -------------------------------------------------

    def _create_placeholder_pipeline(self) -> None:
        """Build a black-frame pipeline for the 'No Signal' overlay."""
        # Dispose any live pipeline first: ``self._pipeline`` is overwritten
        # below, so without this an existing pipeline (and its bus signal watch
        # + sockets) would be orphaned – a file-descriptor leak. Route through
        # the timed NULL-confirmation guard so an unresponsive network source
        # can't block the GTK main thread here (the same hazard set_source and
        # the swap paths guard against). If the prior pipeline is stuck, abort
        # rather than overwrite it – every caller checks ``self._pipeline`` is
        # not None before play()ing, so leaving the old reference attached is
        # safe. Most callers already cleared it, so this is usually a no-op.
        try:
            self._null_transition_current_pipeline(swap_label="placeholder")
        except PipelineStuckError:
            logger.warning("placeholder: prior pipeline did not reach NULL – aborting placeholder creation")
            return

        pipeline = self._pipeline_assembler.create_placeholder_pipeline()
        if pipeline is None:
            return

        self._pipeline = pipeline
        self._setup_bus()
        self._state.set_placeholder_pipeline(True)
        width, height = self._pipeline_assembler.placeholder_resolution
        self._state.set_resolution(width, height)

    # -- Bus handling ---------------------------------------------------------

    def _setup_bus(self) -> None:
        self._bus_handler.setup_bus(self._pipeline, self._on_bus_message)

    def _on_bus_message(self, bus: Any, message: Any) -> None:
        try:
            self._bus_handler.handle_message(bus, message)
        except Exception:
            logger.exception("Unhandled error in GStreamer bus handler")

    def _handle_bus_async_done(self, pipeline: Any) -> None:
        self._input.on_bus_async_done(pipeline)

    def _handle_bus_error(self, error_message: str) -> None:
        self._state.mark_disconnected()
        self._schedule_reconnect(error_message)

    def _handle_bus_eos(self) -> None:
        # A looping input (Media Gallery clip) seeks back to start on EOS and
        # reports it handled, so end-of-stream is not a disconnect for it.
        if self._pipeline is not None and self._input.on_bus_eos(self._pipeline):
            return
        self._state.mark_disconnected()
        self._schedule_reconnect("End of stream")

    def _on_pad_event(self, pad: Any, info: Any) -> int:
        event = info.get_event()
        if event is not None and event.type == Gst.EventType.CAPS:
            caps = event.parse_caps()
            if caps is not None:
                structure = caps.get_structure(0)
                if structure is not None:
                    w = structure.get_value("width")
                    h = structure.get_value("height")
                    if isinstance(w, int) and isinstance(h, int) and w > 0 and h > 0:
                        if self._state.set_resolution(w, h):
                            logger.info("Video resolution: %dx%d", w, h)
                    self._apply_framerate_from_structure(structure)
        return cast(int, Gst.PadProbeReturn.OK)  # mypy narrowing

    def _on_sink_buffer(self, pad: Any, info: Any) -> int:
        """Record frame arrival time; detect connection on real buffer flow.

        Runs on GStreamer streaming thread (float write is GIL-atomic).
        Stray placeholder buffers can't fake connection since placeholder
        pipeline is NULL before real pipeline starts.
        """
        self._last_frame_monotonic = time.monotonic()
        if self._state.mark_frame_received():
            self._handle_video_connected()
        return cast(int, Gst.PadProbeReturn.OK)

    def _apply_framerate_from_structure(self, structure: Any) -> None:
        """Parse negotiated framerate (Gst.Fraction) from a caps structure."""
        try:
            ok, num, den = structure.get_fraction("framerate")
        except Exception:
            return
        if not ok or den <= 0 or num < 0:
            return
        self._state.set_source_framerate(num / den)

    # -- Reconnection ---------------------------------------------------------

    def _schedule_reconnect(self, error_message: str = "") -> None:
        self._cancel_connection_timeout()
        self._cancel_reconnect()
        self._cancel_heal()
        self._cancel_watchdog()
        self._reset_video_flow_state()
        if self._pipeline is not None:
            try:
                self._bus_handler.teardown_bus(self._pipeline)
                self._pipeline.set_state(Gst.State.NULL)
            except Exception:
                logger.exception("Error setting pipeline to NULL during reconnect")
            finally:
                self._pipeline = None

        schedule = self._state.build_reconnect_schedule(self._reconnect_policy)
        self._status_marker.set_reconnecting(schedule.attempt, error_message)

        max_attempts = self._reconnect_policy.max_attempts
        logger.info(
            "Scheduling reconnect attempt %d in %.1fs (max attempts: %s)",
            schedule.attempt,
            schedule.delay_ms / 1000,
            max_attempts if max_attempts > 0 else "unlimited",
        )
        self._state.reconnect_source_id = GLib.timeout_add(schedule.delay_ms, self._do_reconnect)

    def _cancel_reconnect(self) -> None:
        if self._state.reconnect_source_id is not None:
            GLib.source_remove(self._state.reconnect_source_id)
            self._state.clear_reconnect_source()

    def _start_connection_timeout(self) -> None:
        self._cancel_connection_timeout()
        timeout = self._reconnect_policy.connection_timeout
        if timeout <= 0:
            return
        delay_ms = int(timeout * 1000)
        self._state.connection_timeout_id = GLib.timeout_add(delay_ms, self._on_connection_timeout)

    def _cancel_connection_timeout(self) -> None:
        if self._state.connection_timeout_id is not None:
            GLib.source_remove(self._state.connection_timeout_id)
            self._state.clear_connection_timeout()

    def _on_connection_timeout(self) -> bool:
        self._state.clear_connection_timeout()
        if not self._state.video_flow_detected and not self._state.is_placeholder_pipeline:
            source_label = self._input.get_source_label(self._input_config)
            timeout = self._reconnect_policy.connection_timeout
            logger.warning(
                "%s source '%s': no video received after %.0fs – reconnecting",
                self._input.display_name,
                source_label,
                timeout,
            )
            self._schedule_reconnect("No video received (connection timeout)")
        return False  # one-shot

    def _do_reconnect(self) -> bool:
        self._state.clear_reconnect_source()

        if self._pipeline is not None:
            self._bus_handler.teardown_bus(self._pipeline)
            self._pipeline.set_state(Gst.State.NULL)
            self._pipeline = None

        max_attempts = self._reconnect_policy.max_attempts
        should_fallback = self._state.should_fallback_to_placeholder(self._reconnect_policy)

        if should_fallback:
            source_label = self._input.get_source_label(self._input_config)
            logger.warning(
                "Max reconnect attempts (%d) reached for source '%s'. Falling back to no-signal placeholder.",
                max_attempts,
                source_label,
            )

            self._state.reset_reconnect_backoff()
            if self._input_caps.has_source_selection:
                # Clear primary config field for selection-based inputs.
                if self._input.config_fields():
                    primary_field = self._input.config_fields()[0].name
                    self._input_config[primary_field] = ""
                self._state.activate_source_selection()
                self._status_marker.set_disconnected(f"No {self._input.display_name} connection")
            else:
                self._state.deactivate_source_selection()
                self._status_marker.set_disconnected(f"No {self._input.display_name} connection")

            self._create_placeholder_pipeline()
            if self._input_caps.has_source_discovery:
                self._schedule_discovery()
            elif self._has_configured_source():
                # Fixed-URL input with no discovery: keep probing the URL
                # in the background so the feed self-heals when it returns.
                self._schedule_heal()

            widget = self.get_sink_widget()
            if self._on_widget_changed is not None and widget is not None:
                self._on_widget_changed(widget)

            if self._pipeline is not None:
                result = self._pipeline.set_state(Gst.State.PLAYING)
                if result == Gst.StateChangeReturn.FAILURE:
                    logger.error("Failed to start placeholder pipeline")
                else:
                    logger.info("Placeholder pipeline started - showing 'No Signal' overlay")

            return False

        source_label = self._input.get_source_label(self._input_config)
        if not self._input_caps.has_source_selection and not self._has_configured_source():
            logger.info("Reconnect skipped – no URL configured for %s", self._input.display_name)
            self.play()  # play() will show placeholder
            return False

        logger.info("Reconnecting pipeline (attempt %d)...", self._state.reconnect_attempt)
        try:
            self.create_pipeline()
            # ``create_pipeline`` swallows a build failure and installs the
            # no-signal placeholder without re-raising – so the ``except`` below
            # never fires for that case. For a fixed-URL feed, ``play()`` on the
            # placeholder arms neither a connection timeout nor a reconnect, and
            # this (non-fallback) path schedules no heal either, stranding the
            # feed permanently. Detect the placeholder and reschedule the
            # reconnect so the source keeps being retried (and eventually falls
            # through to the max-attempts heal path).
            if self._state.is_placeholder_pipeline:
                logger.warning("Pipeline build failed during reconnect – rescheduling")
                self._schedule_reconnect("Pipeline build failed during reconnect")
                return False
            widget = self.get_sink_widget()
            if self._on_widget_changed is not None and widget is not None:
                self._on_widget_changed(widget)
            self.play()
        except Exception as exc:
            logger.warning("Reconnect failed: %s", exc)
            self._schedule_reconnect(str(exc))
        return False

    # -- Background healing ----------------------------------------------------

    def _schedule_heal(self) -> None:
        """Slow background retry for URL-based inputs on no-signal placeholder."""
        interval = self._reconnect_policy.heal_interval
        if interval <= 0:
            return
        # Mirror _schedule_reconnect's full cancel set so a heal tick starts
        # from a known-clean timer state. _do_heal calls _do_reconnect, which
        # arms a connection timeout; without cancelling the connection-timeout
        # and reconnect timers here, a heal re-armed from the max-attempts
        # fallback could overlap a still-pending one from the prior cycle.
        self._cancel_connection_timeout()
        self._cancel_reconnect()
        self._cancel_heal()
        self._cancel_watchdog()
        delay_ms = int(interval * 1000)
        self._heal_source_id = GLib.timeout_add(delay_ms, self._do_heal)

    def _cancel_heal(self) -> None:
        if self._heal_source_id is not None:
            GLib.source_remove(self._heal_source_id)
            self._heal_source_id = None

    def _do_heal(self) -> bool:
        self._heal_source_id = None

        if not self._state.is_placeholder_pipeline:
            return False
        if not self._has_configured_source():
            self._schedule_heal()
            return False

        logger.debug(
            "Auto-heal: re-attempting %s connection to %s",
            self._input.display_name,
            self._input.get_source_label(self._input_config),
        )
        self._state.reset_reconnect_backoff()
        self._do_reconnect()
        return False

    # -- Recovery-timer overrides ----------------------------------------------

    def _apply_policy_overrides(self) -> None:
        """Apply user's stall_timeout/heal_interval overrides to plugin policy."""
        if self._stall_timeout_override is not None:
            self._reconnect_policy.stall_timeout = self._stall_timeout_override
        if self._heal_interval_override is not None:
            self._reconnect_policy.heal_interval = self._heal_interval_override

    def set_recovery_timers(
        self,
        *,
        stall_timeout: float | None = None,
        heal_interval: float | None = None,
    ) -> None:
        """Live-update stall_timeout and heal_interval without pipeline rebuild."""
        if stall_timeout is not None:
            self._stall_timeout_override = stall_timeout
            self._reconnect_policy.stall_timeout = stall_timeout
        if heal_interval is not None:
            self._heal_interval_override = heal_interval
            self._reconnect_policy.heal_interval = heal_interval
        if self._watchdog_source_id is not None and self._state.connected:
            self._start_watchdog(reseed=False)

    # -- Silent-stall watchdog -------------------------------------------------

    def _start_watchdog(self, *, reseed: bool = True) -> None:
        """Arm watchdog to detect silently stalled network feeds.

        reseed=True sets last-frame clock to now (skip false positives on
        fresh connect); reseed=False preserves timestamp (live config changes).
        """
        timeout = self._reconnect_policy.stall_timeout
        if timeout <= 0:
            return
        self._cancel_watchdog()
        if reseed:
            self._last_frame_monotonic = time.monotonic()
        check_ms = max(500, int(timeout * 1000 / 3))
        self._watchdog_source_id = GLib.timeout_add(check_ms, self._do_watchdog)

    def _cancel_watchdog(self) -> None:
        if self._watchdog_source_id is not None:
            GLib.source_remove(self._watchdog_source_id)
            self._watchdog_source_id = None

    def _do_watchdog(self) -> bool:
        """Periodic check for stalled live feeds."""
        timeout = self._reconnect_policy.stall_timeout
        if timeout <= 0:
            self._watchdog_source_id = None
            return False
        if not self._state.connected or self._state.is_placeholder_pipeline:
            self._watchdog_source_id = None
            return False
        elapsed = time.monotonic() - self._last_frame_monotonic
        if elapsed < timeout:
            return True  # healthy – stay armed
        source_label = self._input.get_source_label(self._input_config)
        logger.warning(
            "%s source '%s': no frame for %.1fs (>= %.1fs stall timeout) – "
            "feed stalled with no error; tearing down and reconnecting",
            self._input.display_name,
            source_label,
            elapsed,
            timeout,
        )
        self._watchdog_source_id = None
        self._schedule_reconnect("Stream stalled (no frames received)")
        return False

    # -- Source discovery ------------------------------------------------------

    def _schedule_discovery(self) -> None:
        if not self._input_caps.has_source_discovery:
            return
        self._cancel_discovery()
        self._do_discovery()
        interval = self._input_caps.discovery_interval
        delay_ms = int(interval * 1000)
        self._discovery_source_id = GLib.timeout_add(delay_ms, self._do_discovery)

    def _cancel_discovery(self) -> None:
        if self._discovery_source_id is not None:
            GLib.source_remove(self._discovery_source_id)
            self._discovery_source_id = None

    def _do_discovery(self) -> bool:
        if self._state.connected and not self._state.source_selection_active:
            self._discovery_source_id = None
            return False

        with self._discovery_lock:
            if self._discovery_running:
                return True
            self._discovery_running = True

        self._discovery_thread = threading.Thread(target=self._run_discovery, daemon=True, name="SourceDiscovery")
        self._discovery_thread.start()
        return True

    def _run_discovery(self) -> None:
        try:
            try:
                sources = self._input.discover_sources(timeout=2.0)
            except Exception as e:
                logger.warning("Source discovery failed: %s", e)
                return

            with self._discovery_lock:
                old_sources = self._discovered_sources
                old_idx = self._selected_source_index

                new_idx = 0
                if sources and old_sources and old_idx < len(old_sources):
                    old_selected = old_sources[old_idx]
                    if old_selected in sources:
                        new_idx = sources.index(old_selected)
                    else:
                        new_idx = min(old_idx, len(sources) - 1)

                self._discovered_sources = sources
                self._selected_source_index = new_idx

            if sources:
                logger.debug("Discovered %d source(s): %s", len(sources), sources)
            else:
                logger.debug("No sources discovered")
        finally:
            with self._discovery_lock:
                self._discovery_running = False
