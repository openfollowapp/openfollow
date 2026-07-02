# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""NDI video input plugin with runtime source discovery.

Builds an ``ndisrc``/``ndisrcdemux`` receive pipeline and discovers NDI sources
via the NDI SDK loaded through ctypes; locates ``libndi`` on disk and points the
GStreamer Rust NDI plugin at the SDK.
"""

from __future__ import annotations
from openfollow.i18n import _, _l  # noqa: E402

import ctypes
import ctypes.util
import html
import logging
import os
from collections.abc import Callable
from typing import Any

from openfollow.video.inputs._base import (
    ConfigField,
    InputCapabilities,
    ReconnectPolicy,
    VideoInputBase,
    WebRoute,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# NDI SDK library path setup
# ---------------------------------------------------------------------------

_NDI_LIB_DIRS = (
    "/Library/NDI SDK for Apple/lib/macOS",
    "/usr/local/lib",
    "/usr/lib",
    "/opt/homebrew/lib",
)

# Ensure the GStreamer Rust NDI plugin can find the SDK at runtime.
# pragma: no cover – module-level NDI library search runs once at
# import time. By the time pytest imports this module, the dev
# machine's NDI_RUNTIME_DIR_V{5,6} env vars are already set (or aren't,
# and the SDK isn't installed); reproducing the unset-env-var-but-SDK-
# present case in tests would require a subprocess re-import. Tests
# for ``_find_ndi_library`` (a separate function) cover the runtime
# library-resolution logic. left this as a known gap.
for _env in ("NDI_RUNTIME_DIR_V6", "NDI_RUNTIME_DIR_V5"):  # pragma: no cover
    if os.environ.get(_env):
        break
else:  # pragma: no cover
    for _d in _NDI_LIB_DIRS:
        if os.path.isfile(os.path.join(_d, "libndi.dylib")) or os.path.isfile(os.path.join(_d, "libndi.so")):
            os.environ["NDI_RUNTIME_DIR_V6"] = _d
            break


def _find_ndi_library() -> str | None:
    """Locate the NDI shared library on disk."""
    for env in ("NDI_RUNTIME_DIR_V6", "NDI_RUNTIME_DIR_V5"):
        d = os.environ.get(env)
        if not d:
            continue
        for name in ("libndi.dylib", "libndi.so", "Processing.NDI.Lib.x64.dll"):
            path = os.path.join(d, name)
            if os.path.isfile(path):
                return path
    # pragma: no cover – fallback default-dirs scan: only reachable
    # when the env-var search above falls through without returning
    # AND a default dir contains the SDK. The existing test for the
    # ctypes-fallback arm (``test_falls_back_to_ctypes_util_when_no_sdk_files_exist``)
    # already exercises the post-loop ``ctypes.util.find_library``.
    for d in _NDI_LIB_DIRS:  # pragma: no cover
        for name in ("libndi.dylib", "libndi.so", "Processing.NDI.Lib.x64.dll"):
            path = os.path.join(d, name)
            if os.path.isfile(path):
                return path
    return ctypes.util.find_library("ndi")


# ---------------------------------------------------------------------------
# Plugin class
# ---------------------------------------------------------------------------


class NdiInput(VideoInputBase):
    """NDI video input with source discovery and on-screen selection."""

    input_id = "ndi"
    display_name = _l("NDI®")

    # -- Declarations ---------------------------------------------------------

    @classmethod
    def config_fields(cls) -> list[ConfigField]:
        return [
            ConfigField("ndi_source_name", str, "", "NDI Source"),
        ]

    @classmethod
    def capabilities(cls) -> InputCapabilities:
        return InputCapabilities(
            has_source_discovery=True,
            has_source_selection=True,
            discovery_interval=5.0,
            selection_title="SELECT NDI SOURCE",
            hotkey="n",
            hotkey_label="N=NDI",
            force_zero_latency=True,
        )

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        try:
            from gi.repository import Gst

            Gst.init(None)
            if Gst.ElementFactory.find("ndisrc") is None:
                return False, "NDI® GStreamer plugin not installed (gst-plugin-ndi required)"
        except Exception:
            return False, "GStreamer not available"
        return True, ""

    @classmethod
    def reconnect_policy(cls) -> ReconnectPolicy:
        return ReconnectPolicy(
            max_attempts=1,
            min_delay=0.5,
            max_delay=3.0,
            backoff_multiplier=1.5,
            connection_timeout=8.0,
            fallback_to_selection=True,
        )

    # -- Pipeline -------------------------------------------------------------

    def create_pipeline(
        self,
        config: dict[str, Any],
        sink: Any,
        build_overlay_tail: Callable[..., Any],
        prepare_sink: Callable[..., Any],
    ) -> Any:
        """Build an NDI receive pipeline.

        ``ndisrc → ndisrcdemux ┬ video → queue → videoconvert → [overlay tail] → sink
                               └ audio → queue → fakesink``

        The leaky queue between ``ndisrcdemux`` and ``videoconvert`` creates a
        thread boundary so the demux streaming thread cannot starve the GTK
        main loop by running the overlay/sink chain inline.
        """
        from gi.repository import Gst

        source_name = config.get("ndi_source_name", "")
        pipeline = Gst.Pipeline.new("native-sink")

        ndisrc = Gst.ElementFactory.make("ndisrc", "ndisrc")
        if ndisrc is None:
            raise RuntimeError("NDI GStreamer plugin not available – install gst-plugin-ndi")
        ndisrc.set_property("ndi-name", source_name)
        demux = Gst.ElementFactory.make("ndisrcdemux", "demux")
        if demux is None:
            raise RuntimeError("NDI demux element not available – install gst-plugin-ndi")
        video_queue = Gst.ElementFactory.make("queue", "ndi_video_queue")
        if video_queue is None:
            raise RuntimeError("Failed to create video queue element")
        video_queue.set_property("leaky", 2)  # downstream – drop oldest if full
        video_queue.set_property("max-size-buffers", 3)
        video_queue.set_property("max-size-time", 0)
        video_queue.set_property("max-size-bytes", 0)
        convert = Gst.ElementFactory.make("videoconvert", "convert")
        if convert is None:
            raise RuntimeError("videoconvert GStreamer element not found – install gstreamer1.0-plugins-base")

        sink = prepare_sink()
        if sink is None:
            raise RuntimeError("No video sink available for NDI pipeline")

        for elem in (ndisrc, demux, video_queue, convert, sink):
            pipeline.add(elem)

        if not ndisrc.link(demux):
            raise RuntimeError("Failed to link ndisrc → ndisrcdemux")
        if not video_queue.link(convert):
            raise RuntimeError("Failed to link ndi_video_queue → videoconvert")

        build_overlay_tail(pipeline, convert, sink)

        def on_pad_added(element: Any, pad: Any) -> None:
            pad_name = pad.get_name()
            if pad_name.startswith("video"):
                sink_pad = video_queue.get_static_pad("sink")
                if sink_pad is not None and not sink_pad.is_linked():
                    result = pad.link(sink_pad)
                    if result == Gst.PadLinkReturn.OK:
                        caps = pad.get_current_caps() or pad.query_caps(None)
                        caps_str = caps.to_string() if caps else "unknown"
                        logger.info("NDI video linked: %s (%s)", pad_name, caps_str)
                    else:
                        logger.error(
                            "Failed to link demux.%s → ndi_video_queue: %s",
                            pad_name,
                            result,
                        )
            elif pad_name.startswith("audio"):
                audio_queue = Gst.ElementFactory.make("queue", "ndi_audio_queue")
                fakesink = Gst.ElementFactory.make("fakesink", "audio_fakesink")
                if audio_queue is None or fakesink is None:
                    return
                audio_queue.set_property("leaky", 2)
                audio_queue.set_property("max-size-buffers", 2)
                audio_queue.set_property("max-size-time", 0)
                audio_queue.set_property("max-size-bytes", 0)
                fakesink.set_property("sync", False)
                fakesink.set_property("async", False)
                fakesink.set_property("silent", True)
                pipeline.add(audio_queue)
                pipeline.add(fakesink)
                if not audio_queue.link(fakesink):
                    logger.error("Failed to link ndi_audio_queue → audio_fakesink")
                    return
                audio_queue.sync_state_with_parent()
                fakesink.sync_state_with_parent()
                audio_result = pad.link(audio_queue.get_static_pad("sink"))
                if audio_result != Gst.PadLinkReturn.OK:
                    logger.warning(
                        "Failed to link demux.%s → ndi_audio_queue: %s",
                        pad_name,
                        audio_result,
                    )

        demux.connect("pad-added", on_pad_added)
        pipeline.set_latency(0)
        return pipeline

    # -- Lifecycle hooks ------------------------------------------------------

    def on_bus_async_done(self, pipeline: Any) -> None:
        """NDI: force pipeline latency to 0 so all queues flush immediately."""
        pipeline.set_latency(0)
        logger.debug("Pipeline ASYNC_DONE (NDI) – latency forced to 0")

    # -- Source discovery -----------------------------------------------------

    @classmethod
    def discover_sources(cls, timeout: float = 2.0) -> list[str]:
        """Scan for available NDI sources using the NDI SDK via ctypes."""
        lib_path = _find_ndi_library()
        if lib_path is None:
            logger.warning("NDI® library not found – cannot discover sources.")
            return []

        try:
            ndi = ctypes.cdll.LoadLibrary(lib_path)
        except OSError as e:
            logger.warning("Failed to load NDI library: %s", e)
            return []

        try:
            ndi.NDIlib_initialize.restype = ctypes.c_bool
            ndi.NDIlib_initialize.argtypes = []
            if not ndi.NDIlib_initialize():
                return []
        except AttributeError:
            pass  # older SDK versions may not require explicit init

        class _NDISource(ctypes.Structure):
            _fields_ = [
                ("p_ndi_name", ctypes.c_char_p),
                ("p_url_address", ctypes.c_char_p),
            ]

        ndi.NDIlib_find_create_v2.restype = ctypes.c_void_p
        ndi.NDIlib_find_create_v2.argtypes = [ctypes.c_void_p]
        ndi.NDIlib_find_wait_for_sources.restype = ctypes.c_bool
        ndi.NDIlib_find_wait_for_sources.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        ndi.NDIlib_find_get_current_sources.restype = ctypes.POINTER(_NDISource)
        ndi.NDIlib_find_get_current_sources.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
        ]
        ndi.NDIlib_find_destroy.restype = None
        ndi.NDIlib_find_destroy.argtypes = [ctypes.c_void_p]

        finder = ndi.NDIlib_find_create_v2(None)
        if not finder:
            return []

        try:
            ndi.NDIlib_find_wait_for_sources(finder, int(timeout * 1000))
            num = ctypes.c_uint32(0)
            sources = ndi.NDIlib_find_get_current_sources(finder, ctypes.byref(num))
            return [
                sources[i].p_ndi_name.decode("utf-8", errors="replace")
                for i in range(num.value)
                if sources[i].p_ndi_name
            ]
        finally:
            ndi.NDIlib_find_destroy(finder)

    # -- Web UI ---------------------------------------------------------------

    @classmethod
    def web_ui_html(cls, config: dict[str, Any]) -> str:
        source_name = cls._esc(config.get("ndi_source_name", ""))
        available, reason = cls.is_available()
        warning = (
            (
                f'<div style="color:#c0392b;background:#fdf2f2;padding:8px 12px;'
                f'border-radius:4px;margin-bottom:8px;font-size:0.9em;">'
                f"&#9888; NDI not available: {html.escape(reason)}</div>"
            )
            if not available
            else ""
        )
        return warning + (
            '<div class="row ndi-row">'
            '    <div class="field wide">'
            f"        <label>{_('NDI® Source')}</label>"
            '        <select name="ndi_source_name" hx-get="/video-input/ndi/sources"'
            '                hx-trigger="load, click from:#refresh-ndi"'
            '                hx-target="this" hx-swap="innerHTML">'
            f'            <option value="{source_name}">'
            f"              {source_name or '-- Loading... --'}"
            "            </option>"
            "        </select>"
            "    </div>"
            '    <button type="button" id="refresh-ndi" class="secondary"'
            '            style="margin-bottom:0;">Scan</button>'
            "</div>"
            '<p style="font-size:0.75em;color:#888;margin-top:6px;">'
            "NDI&#174; is a registered trademark of Vizrt NDI AB. "
            'More Information on <a href="https://ndi.video" target="_blank" rel="noopener">ndi.video</a>.'
            "</p>"
        )

    @classmethod
    def web_routes(cls) -> list[WebRoute]:
        return [
            WebRoute("GET", "/video-input/ndi/sources", "handle_discover_sources"),
        ]

    def handle_discover_sources(self, config: dict[str, Any]) -> str:
        """Return ``<option>`` elements for NDI source dropdown."""
        sources = self.discover_sources(timeout=2.0)
        current = config.get("ndi_source_name", "")

        options: list[str] = []
        if not sources:
            options.append('<option value="">-- No sources found --</option>')
        for src in sources:
            safe = self._esc(src)
            # Exact match only; a substring match could mark several options
            # selected and let the browser silently switch the saved source.
            selected = " selected" if src == current else ""
            options.append(f'<option value="{safe}"{selected}>{safe}</option>')
        return "\n".join(options)

    # -- Config ---------------------------------------------------------------

    @classmethod
    def get_source_label(cls, config: dict[str, Any]) -> str:
        label = config.get("ndi_source_name", "")
        return str(label) if label else ""
