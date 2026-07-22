# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Raspberry Pi CSI/MIPI camera input plugin via libcamerasrc.

Builds a ``libcamerasrc`` raw-video pipeline and discovers connected cameras by
parsing ``rpicam-hello --list-cameras``.
"""

from __future__ import annotations

import logging
import re
import subprocess
import sys
from collections.abc import Callable
from typing import Any

from openfollow.i18n import _, _l  # noqa: E402
from openfollow.video.inputs._base import (
    ConfigField,
    InputCapabilities,
    ReconnectPolicy,
    VideoInputBase,
    WebRoute,
    coerce_positive_int,
)

logger = logging.getLogger(__name__)


def _discover_cameras() -> list[dict[str, str]]:
    """Parse rpicam-hello output to find connected cameras."""
    try:
        result = subprocess.run(
            ["rpicam-hello", "--list-cameras"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        output = result.stdout + result.stderr
    except (OSError, subprocess.TimeoutExpired):
        return []

    cameras: list[dict[str, str]] = []
    # Match lines like: 0 : imx219 [3280x2464 ...] (/base/axi/...)
    for match in re.finditer(
        r"^(\d+)\s*:\s*(\S+)\s*\[.*?\]\s*\(([^)]+)\)",
        output,
        re.MULTILINE,
    ):
        cameras.append(
            {
                "index": match.group(1),
                "model": match.group(2),
                "path": match.group(3),
            }
        )
    return cameras


class PiCamInput(VideoInputBase):
    """Raspberry Pi CSI/MIPI camera input via libcamera."""

    input_id = "picam"
    display_name = _l("Pi Camera")

    # -- Declarations ---------------------------------------------------------

    @classmethod
    def config_fields(cls) -> list[ConfigField]:
        return [
            ConfigField("picam_camera_name", str, "", "Camera"),
            ConfigField("picam_width", int, 1920, "Width"),
            ConfigField("picam_height", int, 1080, "Height"),
            ConfigField("picam_framerate", int, 30, "Framerate"),
        ]

    @classmethod
    def capabilities(cls) -> InputCapabilities:
        return InputCapabilities(
            has_source_discovery=True,
            has_source_selection=True,
            selection_title="SELECT CAMERA",
            force_zero_latency=True,
        )

    @classmethod
    def reconnect_policy(cls) -> ReconnectPolicy:
        return ReconnectPolicy(
            max_attempts=3,
            min_delay=1.0,
            max_delay=5.0,
            backoff_multiplier=2.0,
            connection_timeout=5.0,
            fallback_to_selection=True,
        )

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        # libcamerasrc and Pi CSI/MIPI cameras are Linux/Raspberry Pi-only;
        # on macOS the user has no such hardware (use the AVFoundation USB
        # Camera) and on Windows there is no equivalent backend.
        if not sys.platform.startswith("linux"):
            return False, "Pi Camera is Linux/Raspberry Pi-only"
        try:
            from gi.repository import Gst

            Gst.init(None)
            if Gst.ElementFactory.find("libcamerasrc") is None:
                return (
                    False,
                    "libcamerasrc GStreamer element not found – install gstreamer1.0-libcamera",
                )
        except Exception:
            return False, "GStreamer not available"
        return True, ""

    # -- Pipeline -------------------------------------------------------------

    def create_pipeline(
        self,
        config: dict[str, Any],
        sink: Any,
        build_overlay_tail: Callable[..., Any],
        prepare_sink: Callable[..., Any],
    ) -> Any:
        """Build a Pi camera pipeline.

        ``libcamerasrc → capsfilter → queue → videoconvert → [overlay tail] → sink``

        No decoding needed – libcamerasrc outputs raw video directly.
        """
        from gi.repository import Gst

        camera_name = config.get("picam_camera_name", "")
        width = coerce_positive_int(config.get("picam_width", 1920), 1920)
        height = coerce_positive_int(config.get("picam_height", 1080), 1080)
        framerate = coerce_positive_int(config.get("picam_framerate", 30), 30)

        def make(kind: str, name: str) -> Any:
            elem = Gst.ElementFactory.make(kind, name)
            if elem is None:
                raise RuntimeError(f"{kind} GStreamer element not found – install gstreamer1.0-plugins-base/good")
            return elem

        pipeline = Gst.Pipeline.new("picam-sink")

        # --- Camera source ---
        src = Gst.ElementFactory.make("libcamerasrc", "libcamerasrc")
        if src is None:
            raise RuntimeError("libcamerasrc GStreamer element not found – install gstreamer1.0-libcamera")
        if camera_name:
            src.set_property("camera-name", camera_name)
            logger.info("Pi Camera source: %s", camera_name)
        else:
            logger.info("Pi Camera source: auto-detect")

        # --- Caps filter for resolution and framerate ---
        capsfilter = make("capsfilter", "capsfilter")
        caps_str = f"video/x-raw,width=(int){width},height=(int){height},framerate=(fraction){framerate}/1"
        capsfilter.set_property("caps", Gst.Caps.from_string(caps_str))
        logger.info("Pi Camera caps: %s", caps_str)

        # --- Post-source queue ---
        queue = make("queue", "post_queue")
        queue.set_property("max-size-buffers", 2)
        queue.set_property("max-size-bytes", 0)
        queue.set_property("max-size-time", 0)
        queue.set_property("leaky", 2)

        convert = make("videoconvert", "convert")

        sink = prepare_sink()
        if sink is None:
            raise RuntimeError("No video sink available for Pi Camera pipeline")

        for elem in (src, capsfilter, queue, convert, sink):
            pipeline.add(elem)

        if not src.link(capsfilter):
            raise RuntimeError("Failed to link libcamerasrc → capsfilter")
        if not capsfilter.link(queue):
            raise RuntimeError("Failed to link capsfilter → queue")
        if not queue.link(convert):
            raise RuntimeError("Failed to link queue → videoconvert")

        build_overlay_tail(pipeline, convert, sink)
        return pipeline

    # -- Lifecycle hooks ------------------------------------------------------

    def on_bus_async_done(self, pipeline: Any) -> None:
        """Pi Camera: force zero latency – local device."""
        pipeline.set_latency(0)
        logger.info("Pipeline ASYNC_DONE (Pi Camera) – latency forced to 0")

    # -- Source discovery ------------------------------------------------------

    @classmethod
    def discover_sources(cls, timeout: float = 2.0) -> list[str]:
        """Discover connected Pi cameras via rpicam-hello."""
        cameras = _discover_cameras()
        return [cam["path"] for cam in cameras]

    # -- Web UI ---------------------------------------------------------------

    @classmethod
    def web_ui_html(cls, config: dict[str, Any]) -> str:
        camera_name = cls._esc(config.get("picam_camera_name", ""))
        width = config.get("picam_width", 1920)
        height = config.get("picam_height", 1080)
        framerate = config.get("picam_framerate", 30)
        return (
            '<div class="row ndi-row">'
            '    <div class="field wide">'
            f"        <label>{_('Camera')}</label>"
            '        <select name="picam_camera_name"'
            '                hx-get="/video-input/picam/cameras"'
            '                hx-trigger="load, click from:#refresh-picam"'
            '                hx-target="this" hx-swap="innerHTML">'
            f'            <option value="{camera_name}">'
            f"              {camera_name or '-- Loading... --'}"
            "            </option>"
            "        </select>"
            "    </div>"
            '    <button type="button" id="refresh-picam"'
            '            class="secondary"'
            '            style="margin-bottom:0;">Scan</button>'
            "</div>"
            '<div class="row">'
            '    <div class="field">'
            f"        <label>{_('Width')}</label>"
            f'        <input type="number" name="picam_width" value="{width}"'
            '                min="320" max="4056">'
            "    </div>"
            '    <div class="field">'
            f"        <label>{_('Height')}</label>"
            f'        <input type="number" name="picam_height" value="{height}"'
            '                min="240" max="3040">'
            "    </div>"
            '    <div class="field">'
            f"        <label>{_('FPS')}</label>"
            f'        <input type="number" name="picam_framerate" value="{framerate}"'
            '                min="1" max="120">'
            "    </div>"
            "</div>"
        )

    @classmethod
    def web_routes(cls) -> list[WebRoute]:
        return [
            WebRoute("GET", "/video-input/picam/cameras", "handle_list_cameras"),
        ]

    def handle_list_cameras(self, config: dict[str, Any]) -> str:
        """Return ``<option>`` elements for camera dropdown."""
        cameras = _discover_cameras()
        current = config.get("picam_camera_name", "")

        options: list[str] = ['<option value="">Auto-detect</option>']
        if not cameras:
            options.append('<option value="" disabled>-- No cameras found --</option>')
        for cam in cameras:
            path = self._esc(cam["path"])
            model = self._esc(cam["model"])
            selected = " selected" if cam["path"] == current else ""
            label = f"{model} ({path})"
            options.append(f'<option value="{path}"{selected}>{label}</option>')
        return "\n".join(options)

    # -- Config ---------------------------------------------------------------

    @classmethod
    def get_source_label(cls, config: dict[str, Any]) -> str:
        camera_name = config.get("picam_camera_name", "")
        # Clamp the same way create_pipeline does so the label never advertises
        # an invalid 0/negative/non-int dimension while the caps run at default.
        width = coerce_positive_int(config.get("picam_width", 1920), 1920)
        height = coerce_positive_int(config.get("picam_height", 1080), 1080)
        framerate = coerce_positive_int(config.get("picam_framerate", 30), 30)
        if camera_name:
            # Side-effect free: this runs on the GTK main thread on hot paths
            # (per-stats publish, play(), every reconnect/watchdog event), so it
            # must NOT spawn the blocking ``rpicam-hello`` scan (5s timeout, and
            # it contends with libcamerasrc for the device). Derive the label
            # from the stored path's basename instead of discovering the model.
            return f"{camera_name.split('/')[-1]} ({width}x{height}@{framerate})"
        return f"Pi Camera ({width}x{height}@{framerate})"
