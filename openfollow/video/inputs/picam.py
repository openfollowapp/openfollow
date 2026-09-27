# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Raspberry Pi CSI/MIPI camera input plugin via libcamerasrc.

Builds a ``libcamerasrc`` raw-video pipeline and discovers connected cameras
through GStreamer's libcamera device provider, from the same package.
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Callable
from typing import Any

from openfollow.privilege.camera_config import is_raspberry_pi
from openfollow.video.failure import SourceKind
from openfollow.video.inputs._base import (
    ConfigField,
    InputCapabilities,
    ReconnectPolicy,
    VideoInputBase,
    coerce_positive_int,
)

logger = logging.getLogger(__name__)


def discover_cameras() -> list[dict[str, str]]:
    """Cameras libcamera can see, as ``{"model", "path"}``; ``path`` is what ``camera-name`` takes.

    Listed through GStreamer's libcamera device provider, which shares
    libcamerasrc's camera manager, so a camera already streaming is still listed.
    """
    try:
        from gi.repository import Gst
    except ImportError:
        return []
    factory = Gst.DeviceProviderFactory.find("libcameraprovider")
    if factory is None:
        return []
    try:
        devices = factory.get().get_devices()
    except Exception:  # noqa: BLE001 - a failed probe lists nothing rather than failing the page
        logger.debug("Pi Camera discovery failed", exc_info=True)
        return []
    cameras: list[dict[str, str]] = []
    for device in devices:
        path = str(device.get_display_name())
        props = device.get_properties()
        # The string form, not the structure getters GStreamer 1.26.2 broke.
        match = re.search(r"api\.libcamera\.Model=\(string\)([^,;\s]+)", props.to_string() if props else "")
        model = match.group(1) if match else path.rsplit("/", 1)[-1].split("@", 1)[0]
        cameras.append({"model": model, "path": path})
    return cameras


class PiCamInput(VideoInputBase):
    """Raspberry Pi CSI/MIPI camera input via libcamera."""

    input_id = "picam"
    display_name = "Pi Camera"
    source_element_name = "libcamerasrc"
    source_kind = SourceKind.LOCAL

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
        # Listed on any Pi, camera or not, so its camera setup is reachable.
        if not is_raspberry_pi():
            return False, "Pi Camera needs a Raspberry Pi"
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

        # --- Caps filter for format, resolution and framerate ---
        # Left open, libcamera takes the lowest-sorting format it offers (raw
        # Bayer or greyscale), not the camera's YUV420 default. Colorimetry stays
        # open: a value the ISP adjusts can fail negotiation outright.
        capsfilter = make("capsfilter", "capsfilter")
        caps_str = (
            f"video/x-raw,format=(string)I420,width=(int){width},height=(int){height},framerate=(fraction){framerate}/1"
        )
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
        """Discover connected Pi cameras through libcamera."""
        cameras = discover_cameras()
        return [cam["path"] for cam in cameras]

    # -- Web UI ---------------------------------------------------------------

    @classmethod
    def web_ui_html(cls, config: dict[str, Any]) -> str:
        width = config.get("picam_width", 1920)
        height = config.get("picam_height", 1080)
        framerate = config.get("picam_framerate", 30)
        return (
            # Which camera, and naming it in config.txt: the camera setup block.
            # Inside the Video Source form: its own target, or htmx hands it the form's.
            '<div id="picam-camera-setup" hx-get="/section/video_source/camera-setup"'
            ' hx-trigger="load" hx-target="this" hx-swap="innerHTML"></div>'
            '<div class="row">'
            '    <div class="field">'
            "        <label>Width</label>"
            f'        <input type="number" name="picam_width" value="{width}"'
            '                min="320" max="4056">'
            "    </div>"
            '    <div class="field">'
            "        <label>Height</label>"
            f'        <input type="number" name="picam_height" value="{height}"'
            '                min="240" max="3040">'
            "    </div>"
            '    <div class="field">'
            "        <label>FPS</label>"
            f'        <input type="number" name="picam_framerate" value="{framerate}"'
            '                min="1" max="120">'
            "    </div>"
            "</div>"
        )

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
            # must NOT probe for cameras. Derive the label from the stored path's
            # basename instead of discovering the model.
            return f"{camera_name.split('/')[-1]} ({width}x{height}@{framerate})"
        return f"Pi Camera ({width}x{height}@{framerate})"
