# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""AVFoundation (macOS USB camera / capture card) video input plugin.

Builds an ``avfvideosrc`` capture pipeline and persists the device's
``avf.unique_id``, resolving it to a live device-index at build time so plug
order changes don't break the selection. macOS-only via ``is_available()``.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from typing import Any

from openfollow.video.failure import SourceKind, VideoFailure
from openfollow.video.inputs._base import (
    ConfigField,
    InputCapabilities,
    ReconnectPolicy,
    VideoInputBase,
    WebRoute,
    coerce_positive_int,
)
from openfollow.video.inputs._pin import PinRefusal

logger = logging.getLogger(__name__)

# Every camera listed this run, by unique ID in the order first listed, kept
# after an unplug. Labels read it because a listing stalls and can prompt for
# camera access.
_camera_names: dict[str, str] = {}

# The index the last preflight found, for the build that follows it on the main
# loop: one listing per build, so the camera cannot move between two.
_checked_index: dict[str, int] = {}


def _camera_name(unique_id: str, stored: object = "") -> str:
    """The camera's name, told apart from any listed camera that shares it.

    As listed this run, numbered from 1 in listing order where two share a
    name; else as stored with its ID, followed by the ID where a listed camera
    has that name; else the ID itself.
    """
    names = tuple(_camera_names.items())
    listed = dict(names).get(unique_id)
    if listed:
        twins = [uid for uid, name in names if name == listed]
        return listed if len(twins) == 1 else f"{listed} ({twins.index(unique_id) + 1})"
    name = stored if isinstance(stored, str) else ""
    if not name:
        return unique_id
    return f"{name} ({unique_id})" if any(listed_name == name for _uid, listed_name in names) else name


def _list_avf_devices() -> list[dict[str, str]]:
    """Enumerate AVFoundation video sources via ``Gst.DeviceMonitor``.

    Returns a list of dicts with keys: ``unique_id``, ``name``, ``index``.
    The ``index`` is the device's position within the AVF-only filtered
    list – that matches what ``avfvideosrc device-index`` expects, since
    GStreamer's AVF provider iterates ``AVCaptureDevice.devices`` in the
    same order.

    Raises when the cameras cannot be listed, so that is never read as "no
    camera". Empty off macOS, so contract tests on Linux never scan.
    """
    if sys.platform != "darwin":
        return []
    from gi.repository import Gst

    Gst.init(None)
    monitor = Gst.DeviceMonitor.new()
    monitor.add_filter("Video/Source", None)
    if not monitor.start():
        raise RuntimeError("the device monitor did not start")
    try:
        devices = monitor.get_devices() or []
    finally:
        monitor.stop()

    result: list[dict[str, str]] = []
    avf_index = 0
    for dev in devices:
        props = dev.get_properties()
        if props is None:
            continue
        api = props.get_string("device.api") or ""
        if api != "avf":
            continue
        unique_id = props.get_string("avf.unique_id") or ""
        name = dev.get_display_name() or unique_id or f"avf{avf_index}"
        result.append(
            {
                "unique_id": unique_id,
                "name": name,
                "index": str(avf_index),
            }
        )
        avf_index += 1
    _camera_names.update({dev["unique_id"]: dev["name"] for dev in result if dev["unique_id"]})
    return result


def _discover_avf_devices() -> list[dict[str, str]]:
    """The cameras for a list an operator reads, or none when they cannot be listed."""
    try:
        return _list_avf_devices()
    except Exception as exc:
        logger.debug("AVFoundation device discovery failed: %s", exc)
        return []


def _resolve_device_index(
    unique_id: str,
    fallback_index: int,
) -> int | None:
    """Map a stored ``unique_id`` to a live ``avfvideosrc`` device-index.

    Indices shift when devices are plugged or unplugged, so we persist
    ``avf.unique_id`` and resolve to an index at pipeline-build time.

    An empty ``unique_id`` returns ``fallback_index``; such a build is never
    played, because with no camera chosen the receiver opens the picker. A
    ``unique_id`` that is not connected returns ``None``: another camera's
    index would open the wrong device and report it connected.
    """
    if not unique_id:
        return fallback_index
    for dev in _discover_avf_devices():
        if dev["unique_id"] == unique_id:
            return int(dev["index"])
    return None


class AvfInput(VideoInputBase):
    """USB camera / capture card input via AVFoundation (macOS)."""

    input_id = "avf"
    display_name = "USB Camera (AVFoundation)"
    source_element_name = "avfvideosrc"
    source_kind = SourceKind.LOCAL

    # -- Declarations ---------------------------------------------------

    @classmethod
    def config_fields(cls) -> list[ConfigField]:
        return [
            ConfigField("avf_unique_id", str, "", "Device"),
            # Recorded on the first save of an older config, or when macOS
            # reports another name; neither may black the video out.
            ConfigField("avf_device_name", str, "", "Device name", device_editable=False, rebuilds=False),
            ConfigField(
                "avf_device_index",
                int,
                -1,
                "Device index",
            ),
            ConfigField("avf_width", int, 1920, "Width"),
            ConfigField("avf_height", int, 1080, "Height"),
            ConfigField("avf_framerate", int, 30, "Framerate"),
        ]

    @classmethod
    def capabilities(cls) -> InputCapabilities:
        return InputCapabilities(
            has_source_discovery=True,
            has_source_selection=True,
            selection_title="SELECT USB CAMERA",
            force_zero_latency=True,
        )

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        if sys.platform != "darwin":
            return False, "AVFoundation is macOS-only"
        try:
            from gi.repository import Gst

            Gst.init(None)
            if Gst.ElementFactory.find("avfvideosrc") is None:
                return (
                    False,
                    "avfvideosrc GStreamer element not found – install the applemedia plugin (brew install gstreamer)",
                )
        except Exception:
            return False, "GStreamer not available"
        return True, ""

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
    def preflight(cls, config: dict[str, Any]) -> PinRefusal | None:
        """Refuse to build for a remembered camera that is not connected, or
        that cannot be looked for; the build uses the index found here."""
        _checked_index.clear()
        unique_id = str(config.get("avf_unique_id", "") or "")
        if not unique_id:
            return None
        try:
            devices = _list_avf_devices()
        except Exception as exc:
            return PinRefusal(VideoFailure.UNKNOWN, f"the camera list could not be read: {exc}")
        for dev in devices:
            if dev["unique_id"] == unique_id:
                _checked_index[unique_id] = int(dev["index"])
                return None
        return PinRefusal(VideoFailure.STREAM_NOT_FOUND, f"no camera with unique ID {unique_id} is connected")

    # -- Pipeline -------------------------------------------------------

    def create_pipeline(
        self,
        config: dict[str, Any],
        sink: Any,
        build_overlay_tail: Callable[..., Any],
        prepare_sink: Callable[..., Any],
    ) -> Any:
        """Build an AVFoundation capture pipeline.

        ``avfvideosrc -> capsfilter -> queue -> videoconvert
        -> [overlay tail] -> sink``

        The capsfilter requests plain ``video/x-raw`` (no
        ``memory:GLMemory`` feature) so AVF emits CPU frames that
        downstream ``videoconvert`` can handle without a GL download.
        """
        from gi.repository import Gst

        unique_id = config.get("avf_unique_id", "") or ""
        # ``avf_device_index`` allows the -1 sentinel, so coerce_positive_int
        # (which floors at 1) doesn't fit; guard a hand-edited non-int defensively.
        try:
            fallback_index = int(config.get("avf_device_index", -1))
        except (TypeError, ValueError):
            fallback_index = -1
        width = coerce_positive_int(config.get("avf_width", 1920), 1920)
        height = coerce_positive_int(config.get("avf_height", 1080), 1080)
        framerate = coerce_positive_int(config.get("avf_framerate", 30), 30)

        device_index = _checked_index.pop(unique_id, None) if unique_id else None
        if device_index is None:
            device_index = _resolve_device_index(unique_id, fallback_index)
        if device_index is None:
            raise RuntimeError(f"No camera with unique ID {unique_id} is connected")

        def make(kind: str, name: str) -> Any:
            elem = Gst.ElementFactory.make(kind, name)
            if elem is None:
                raise RuntimeError(f"{kind} GStreamer element not found – install gstreamer1.0-plugins-base/good")
            return elem

        pipeline = Gst.Pipeline.new("avf-sink")

        src = Gst.ElementFactory.make("avfvideosrc", "avfvideosrc")
        if src is None:
            raise RuntimeError("avfvideosrc GStreamer element not found – install the applemedia GStreamer plugin")
        src.set_property("device-index", device_index)
        logger.info(
            "AVF source: device-index=%d (unique_id=%s)",
            device_index,
            unique_id or "<default>",
        )

        capsfilter = make("capsfilter", "capsfilter")
        caps_str = f"video/x-raw,width=(int){width},height=(int){height},framerate=(fraction){framerate}/1"
        capsfilter.set_property(
            "caps",
            Gst.Caps.from_string(caps_str),
        )
        logger.info("AVF caps: %s", caps_str)

        queue = make("queue", "post_queue")
        queue.set_property("max-size-buffers", 2)
        queue.set_property("max-size-bytes", 0)
        queue.set_property("max-size-time", 0)
        queue.set_property("leaky", 2)

        convert = make("videoconvert", "convert")

        sink = prepare_sink()
        if sink is None:
            raise RuntimeError("No video sink available for AVF pipeline")

        for elem in (src, capsfilter, queue, convert, sink):
            pipeline.add(elem)

        if not src.link(capsfilter):
            raise RuntimeError("Failed to link avfvideosrc -> capsfilter")
        if not capsfilter.link(queue):
            raise RuntimeError("Failed to link capsfilter -> queue")
        if not queue.link(convert):
            raise RuntimeError("Failed to link queue -> videoconvert")

        build_overlay_tail(pipeline, convert, sink)
        return pipeline

    # -- Lifecycle hooks ------------------------------------------------

    def on_bus_async_done(self, pipeline: Any) -> None:
        """AVF: force zero latency – local device."""
        pipeline.set_latency(0)
        logger.info("Pipeline ASYNC_DONE (AVF) – latency forced to 0")

    # -- Source discovery -----------------------------------------------

    @classmethod
    def discover_sources(cls, timeout: float = 2.0) -> list[str]:
        """Return AVF camera ``unique_id``s (stable across reboots)."""
        return [d["unique_id"] for d in _discover_avf_devices()]

    @classmethod
    def source_labels(cls, sources: list[str]) -> list[str]:
        """Each camera's name, numbered where two cameras share one."""
        return [_camera_name(uid) for uid in sources]

    # -- Web UI ---------------------------------------------------------

    @classmethod
    def web_ui_html(cls, config: dict[str, Any]) -> str:
        stored = str(config.get("avf_unique_id", "") or "")
        unique_id = cls._esc(stored)
        shown = _camera_name(stored, config.get("avf_device_name")) if stored else ""
        # ``avf_device_index`` drives nothing (with no camera chosen no pipeline
        # plays); a hidden input keeps its stored value through a save.
        device_index = int(config.get("avf_device_index", -1))
        width = config.get("avf_width", 1920)
        height = config.get("avf_height", 1080)
        framerate = config.get("avf_framerate", 30)
        return (
            '<div class="row ndi-row">'
            '    <div class="field wide">'
            "        <label>Device</label>"
            '        <select name="avf_unique_id"'
            '                hx-get="/video-input/avf/devices"'
            '                hx-trigger="load, click from:'
            '#refresh-avf"'
            '                hx-target="this"'
            '                hx-swap="innerHTML">'
            f'            <option value="{unique_id}">'
            f"              {cls._esc(shown) or '-- Loading... --'}"
            "            </option>"
            "        </select>"
            "    </div>"
            '    <button type="button" id="refresh-avf"'
            '            class="secondary"'
            '            style="margin-bottom:0;">'
            "Scan</button>"
            f'    <input type="hidden" name="avf_device_index"'
            f'           value="{device_index}">'
            "</div>"
            '<div class="row">'
            '    <div class="field">'
            "        <label>Width</label>"
            '        <input type="number"'
            f'               name="avf_width" value="{width}"'
            '                min="160" max="3840">'
            "    </div>"
            '    <div class="field">'
            "        <label>Height</label>"
            '        <input type="number"'
            f'               name="avf_height"'
            f'               value="{height}"'
            '                min="120" max="2160">'
            "    </div>"
            '    <div class="field">'
            "        <label>FPS</label>"
            '        <input type="number"'
            f'               name="avf_framerate"'
            f'               value="{framerate}"'
            '                min="1" max="120">'
            "    </div>"
            "</div>"
        )

    @classmethod
    def web_routes(cls) -> list[WebRoute]:
        return [
            WebRoute(
                "GET",
                "/video-input/avf/devices",
                "handle_list_devices",
            ),
        ]

    def handle_list_devices(
        self,
        config: dict[str, Any],
    ) -> str:
        """Return ``<option>`` elements for the device dropdown."""
        try:
            devices = _list_avf_devices()
            listed = True
        except Exception as exc:
            logger.debug("AVFoundation device discovery failed: %s", exc)
            devices, listed = [], False
        current = config.get("avf_unique_id", "")

        options: list[str] = []
        # A remembered camera that is not listed stays selected, so saving the
        # section never silently replaces it with the first one listed. Only a
        # list that was read can say it is not connected.
        if current and current not in {dev["unique_id"] for dev in devices}:
            cur = self._esc(current)
            name = self._esc(_camera_name(current, config.get("avf_device_name")))
            suffix = " (unavailable)" if listed else ""
            options.append(f'<option value="{cur}" selected>{name}{suffix}</option>')
        for dev in devices:
            uid = self._esc(dev["unique_id"])
            label = self._esc(_camera_name(dev["unique_id"]) if dev["unique_id"] else dev["name"])
            sel = " selected" if dev["unique_id"] == current else ""
            options.append(f'<option value="{uid}"{sel}>{label}</option>')
        if not options:
            options.append('<option value="">-- No devices found --</option>')
        return "\n".join(options)

    # -- Config ---------------------------------------------------------

    @classmethod
    def get_source_label(
        cls,
        config: dict[str, Any],
    ) -> str:
        # Keep side-effect free: no Gst.DeviceMonitor scan here (stalls + re-prompts for permission on macOS).
        unique_id = str(config.get("avf_unique_id", "") or "")
        # No camera chosen reads as no source, so the receiver asks for one
        # rather than opening whichever camera the system offers.
        if not unique_id:
            return ""
        width = config.get("avf_width", 1920)
        height = config.get("avf_height", 1080)
        framerate = config.get("avf_framerate", 30)
        return f"{_camera_name(unique_id, config.get('avf_device_name'))} ({width}x{height}@{framerate})"

    @classmethod
    def apply_config_fields(cls, cfg: Any, form_data: dict[str, Any]) -> None:
        """Store the chosen camera's name with its unique ID.

        A name listed this run wins; otherwise a name sent with the ID (an
        import, a peer) is kept, and a camera left as it was keeps its name.
        """
        from openfollow.configuration import clean_avf_device_name

        stored_id = getattr(cfg, "avf_unique_id", "")
        stored_name = getattr(cfg, "avf_device_name", "")
        super().apply_config_fields(cfg, form_data)
        unique_id = str(getattr(cfg, "avf_unique_id", "") or "")
        name: object = ""
        if unique_id in _camera_names:
            name = _camera_names[unique_id]
        elif unique_id and "avf_device_name" in form_data:
            name = form_data["avf_device_name"]
        elif unique_id and unique_id == stored_id:
            name = stored_name
        cfg.avf_device_name = clean_avf_device_name(name)
