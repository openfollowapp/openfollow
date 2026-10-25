# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""V4L2 (USB camera / capture card) video input plugin."""

from __future__ import annotations

import errno
import glob
import logging
import os
import struct
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from openfollow.video.failure import SourceKind
from openfollow.video.inputs._base import (
    ConfigField,
    InputCapabilities,
    ReconnectPolicy,
    VideoInputBase,
    WebRoute,
    coerce_positive_int,
)

logger = logging.getLogger(__name__)


# V4L2 capability probe so the camera picker only ever offers nodes that can
# deliver video. Modern kernels register several ``/dev/videoN`` nodes per
# device: the real capture node plus metadata and codec/ISP M2M nodes.
# Handing ``v4l2src`` a metadata node lets the pipeline start but no buffers
# flow. We read the per-node capability via ``VIDIOC_QUERYCAP``.
#
# ``struct v4l2_capability`` (linux/videodev2.h): driver[16] + card[32] +
# bus_info[32] + version(u32) + capabilities(u32) + device_caps(u32) +
# reserved[3] = 104 bytes. The two cap words start at offset 84.
_V4L2_CAP_STRUCT_SIZE = 104
_V4L2_CAP_FLAGS_OFFSET = 84
_V4L2_CAP_VIDEO_CAPTURE = 0x00000001
_V4L2_CAP_DEVICE_CAPS = 0x80000000
# VIDIOC_QUERYCAP = _IOR('V', 0, struct v4l2_capability) on the generic ioctl
# encoding shared by x86/ARM/aarch64: dir(READ=2)<<30 | size<<16 | 'V'<<8 | 0.
_VIDIOC_QUERYCAP = (2 << 30) | (_V4L2_CAP_STRUCT_SIZE << 16) | (ord("V") << 8) | 0

# Render-resolution presets the picker offers. "native" passes the device's own
# resolution through unscaled. A capture card only advertises the incoming
# signal's resolution, so the source is never pinned to a fixed size; videoscale
# brings the stream to the chosen render size instead.
_RENDER_DIMENSIONS = {
    "2160p": (3840, 2160),
    "1080p": (1920, 1080),
    "720p": (1280, 720),
}
_RENDER_CHOICES = (
    ("native", "Native size"),
    ("2160p", "2160p"),
    ("1080p", "1080p"),
    ("720p", "720p"),
)
_DEFAULT_RENDER = "1080p"


def _normalize_render(value: str) -> str:
    """Map a render-resolution value to a known preset. Unknown / legacy
    values (e.g. a hand-edited config) fall back to the default so the UI,
    label, and pipeline all agree on the effective setting."""
    if value == "native" or value in _RENDER_DIMENSIONS:
        return value
    return _DEFAULT_RENDER


def _render_dimensions(value: str) -> tuple[int, int] | None:
    """Target ``(width, height)`` for a render-resolution value, or ``None`` for
    ``native`` (no scaling)."""
    value = _normalize_render(value)
    if value == "native":
        return None
    return _RENDER_DIMENSIONS[value]


def _node_supports_video_capture(path: str) -> bool:
    """True if ``path`` advertises ``V4L2_CAP_VIDEO_CAPTURE``.

    Uses the node's *effective* caps – ``device_caps`` when the device sets
    ``V4L2_CAP_DEVICE_CAPS`` (a multi-node device, where ``capabilities`` is
    the union across all its nodes and would falsely include capture for the
    metadata sibling), otherwise ``capabilities``.

    Fail-open: if the node can't be opened or doesn't answer ``QUERYCAP`` (a
    quirky driver, a permission hiccup, or a non-Linux test host where the
    path isn't a real V4L2 device), keep it. The filter only drops nodes it
    can *prove* are non-capture; it never hides a possibly-good camera.
    """
    # ``fcntl`` is a Unix-only stdlib module; import it lazily so this plugin
    # file stays importable on platforms without it (the registry imports
    # every ``video/inputs/*.py`` regardless of ``is_available()``). This
    # function only runs on Linux, where ``glob`` actually finds video nodes.
    import fcntl

    try:
        fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
    except OSError:
        return True
    try:
        buf = bytearray(_V4L2_CAP_STRUCT_SIZE)
        fcntl.ioctl(fd, _VIDIOC_QUERYCAP, buf, True)
    except OSError:
        return True
    finally:
        os.close(fd)
    capabilities, device_caps = struct.unpack_from("=II", buf, _V4L2_CAP_FLAGS_OFFSET)
    effective = device_caps if capabilities & _V4L2_CAP_DEVICE_CAPS else capabilities
    return bool(effective & _V4L2_CAP_VIDEO_CAPTURE)


# The camera's mode list, so the source is pinned to a mode the camera can
# deliver. A UVC camera may list raw modes its USB link cannot carry: those
# frames arrive cut short. Structs from linux/videodev2.h, generic _IOWR.
def _iowr(nr: int, size: int) -> int:
    return (3 << 30) | (size << 16) | (ord("V") << 8) | nr


_FMTDESC = struct.Struct("=III32sII12x")  # index, type, flags, description, pixelformat, mbus_code
_FRMSIZE = struct.Struct("=IIIII16x8x")  # index, pixel_format, type, discrete width/height
_FRMIVAL = struct.Struct("=IIIIIII16x8x")  # index, pixel_format, width, height, type, discrete interval
_VIDIOC_ENUM_FMT = _iowr(2, _FMTDESC.size)
_VIDIOC_ENUM_FRAMESIZES = _iowr(74, _FRMSIZE.size)
_VIDIOC_ENUM_FRAMEINTERVALS = _iowr(75, _FRMIVAL.size)
_V4L2_BUF_TYPE_VIDEO_CAPTURE = 1
_V4L2_DISCRETE = 1
_MAX_ENUM_ENTRIES = 256

# V4L2 fourcc -> (GStreamer raw format, bytes per pixel).
_RAW_FORMATS = {
    "YUYV": ("YUY2", 2.0),
    "UYVY": ("UYVY", 2.0),
    "YVYU": ("YVYU", 2.0),
    "NV12": ("NV12", 1.5),
    "YU12": ("I420", 1.5),
    "YV12": ("YV12", 1.5),
    "GREY": ("GRAY8", 1.0),
    "RGB3": ("RGB", 3.0),
    "BGR3": ("BGR", 3.0),
}
_MJPEG_FOURCCS = frozenset({"MJPG", "JPEG"})

# USB 2.0 high speed carries three 1024-byte isochronous packets per 125 µs microframe.
_USB2_VIDEO_BYTES_S = 3 * 1024 * 8000
_USB1_VIDEO_BYTES_S = 1023 * 1000


@dataclass(frozen=True)
class _CaptureMode:
    fourcc: str
    width: int
    height: int
    rate: Fraction

    @property
    def area(self) -> int:
        return self.width * self.height

    def describe(self) -> str:
        return f"{self.fourcc} {self.width}x{self.height}@{float(self.rate):g}"


class _UnlistedModes(Exception):
    """The node gives sizes or rates as ranges, so its modes cannot be listed."""


def _enumerate(
    ioctl: Callable[[int, bytearray], Any], request: int, layout: struct.Struct, *fields: Any
) -> Iterator[tuple[Any, ...]]:
    """Each entry of a V4L2 enumeration, unpacked; EINVAL ends the list, any other error propagates."""
    for index in range(_MAX_ENUM_ENTRIES):
        buf = bytearray(layout.pack(index, *fields))
        try:
            ioctl(request, buf)
        except OSError as exc:
            if exc.errno == errno.EINVAL:
                return
            raise
        yield layout.unpack(buf)
    raise _UnlistedModes(f"more than {_MAX_ENUM_ENTRIES} entries")


def _list_modes(ioctl: Callable[[int, bytearray], Any]) -> list[_CaptureMode]:
    modes: list[_CaptureMode] = []
    for fmt in _enumerate(ioctl, _VIDIOC_ENUM_FMT, _FMTDESC, _V4L2_BUF_TYPE_VIDEO_CAPTURE, 0, b"", 0, 0):
        pixelformat = fmt[4]
        fourcc = struct.pack("<I", pixelformat).decode("ascii", "replace")
        for size in _enumerate(ioctl, _VIDIOC_ENUM_FRAMESIZES, _FRMSIZE, pixelformat, 0, 0, 0):
            _, _, size_type, width, height = size
            if size_type != _V4L2_DISCRETE:
                raise _UnlistedModes(f"{fourcc} sizes are a range")
            for ival in _enumerate(ioctl, _VIDIOC_ENUM_FRAMEINTERVALS, _FRMIVAL, pixelformat, width, height, 0, 0, 0):
                *_, ival_type, num, den = ival
                if ival_type != _V4L2_DISCRETE:
                    raise _UnlistedModes(f"{fourcc} {width}x{height} rates are a range")
                if num and den:
                    modes.append(_CaptureMode(fourcc, width, height, Fraction(den, num)))
    return modes


def _read_capture_modes(device: str) -> list[_CaptureMode] | None:
    """Every mode the capture node lists, or ``None`` when the list cannot be read in full."""
    import fcntl

    try:
        fd = os.open(device, os.O_RDWR | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        return _list_modes(lambda request, buf: fcntl.ioctl(fd, request, buf, True))
    except (OSError, _UnlistedModes) as exc:
        logger.info("V4L2 %s: camera modes not listed (%s); the source negotiates its own", device, exc)
        return None
    finally:
        os.close(fd)


def _usb_link_budget(device: str, sysfs_root: str = "/sys") -> float | None:
    """Video bytes per second the camera's USB link carries, or ``None`` off USB or when unreadable."""
    path = os.path.realpath(os.path.join(sysfs_root, "class/video4linux", os.path.basename(device), "device"))
    devices = os.path.realpath(os.path.join(sysfs_root, "devices"))
    while path.startswith(devices + os.sep):
        try:
            with open(os.path.join(path, "speed")) as f:
                mbps = float(f.read().strip())
        except FileNotFoundError:
            path = os.path.dirname(path)
            continue
        except (OSError, ValueError):
            return None
        if mbps >= 5000:
            return mbps * 1e6 / 10  # SuperSpeed line rate after 8b/10b coding
        return _USB2_VIDEO_BYTES_S if mbps >= 480 else _USB1_VIDEO_BYTES_S
    return None


def _fits_link(mode: _CaptureMode, budget: float | None) -> bool:
    bytes_per_pixel = _RAW_FORMATS[mode.fourcc][1]
    return budget is None or mode.area * bytes_per_pixel * float(mode.rate) <= budget


def _choose_mode(
    modes: list[_CaptureMode], dims: tuple[int, int] | None, framerate: int, budget: float | None
) -> _CaptureMode | None:
    """The mode to pin: raw when the link carries it at the render size, else MJPEG, else the largest left.

    A rate above the camera's fastest asks for its fastest; videorate repeats frames
    up to the request. ``dims`` ``None`` (native) asks for the largest size listed.
    ``None`` when no raw mode fits the link and there is no MJPEG: the source then
    negotiates as it would unpinned.
    """
    deliverable = [
        m for m in modes if m.fourcc in _MJPEG_FOURCCS or (m.fourcc in _RAW_FORMATS and _fits_link(m, budget))
    ]
    if not deliverable:
        return None
    rate = min(Fraction(framerate), max(m.rate for m in deliverable))
    usable = [m for m in deliverable if m.rate >= rate - Fraction(1, 2)]
    raw = [m for m in usable if m.fourcc in _RAW_FORMATS]
    mjpeg = [m for m in usable if m.fourcc in _MJPEG_FOURCCS]
    width, height = dims or max(((m.width, m.height) for m in usable), key=lambda wh: wh[0] * wh[1])
    for candidates in (raw, mjpeg):
        covering = [m for m in candidates if m.width >= width and m.height >= height]
        if covering:
            return min(covering, key=lambda m: (m.area, abs(m.rate - rate)))
    return max(usable, key=lambda m: (m.area, m.fourcc in _RAW_FORMATS))


def _mode_caps(mode: _CaptureMode) -> str:
    size = f"width=(int){mode.width},height=(int){mode.height}"
    rate = f"framerate=(fraction){mode.rate.numerator}/{mode.rate.denominator}"
    if mode.fourcc in _MJPEG_FOURCCS:
        return f"image/jpeg,{size},{rate}"
    return f"video/x-raw,format=(string){_RAW_FORMATS[mode.fourcc][0]},{size},{rate}"


def _discover_v4l2_devices() -> list[dict[str, str]]:
    """Enumerate ``/dev/video*`` capture nodes and read their names.

    Skips metadata / codec / ISP nodes that can't deliver video frames
    (see :func:`_node_supports_video_capture`). Returns a list
    of dicts with keys: ``path``, ``name``.
    """
    devices: list[dict[str, str]] = []
    for path in sorted(glob.glob("/dev/video*")):
        if not _node_supports_video_capture(path):
            continue
        devices.append({"path": path, "name": _read_device_name(path)})
    return devices


def _read_device_name(device_path: str) -> str:
    """Read the human-readable name from sysfs, with basename fallback.

    Always returns a non-empty string: the device's sysfs ``name`` file
    contents when readable AND non-empty after stripping, otherwise the
    device basename (e.g. ``video0``). An empty sysfs ``name`` file
    happens on some kernels/drivers and would otherwise produce blank
    device labels in the web UI's source picker.
    """
    # /dev/video0 -> /sys/class/video4linux/video0/name
    basename = os.path.basename(device_path)
    sysfs = f"/sys/class/video4linux/{basename}/name"
    try:
        with open(sysfs) as f:
            name = f.read().strip()
    except OSError:
        return basename
    return name or basename


class V4l2Input(VideoInputBase):
    """USB camera / capture card input via V4L2."""

    input_id = "v4l2"
    display_name = "USB Camera"
    source_element_name = "v4l2src"
    source_kind = SourceKind.LOCAL

    # -- Declarations ---------------------------------------------------

    @classmethod
    def config_fields(cls) -> list[ConfigField]:
        return [
            ConfigField(
                "v4l2_device",
                str,
                "/dev/video0",
                "Device",
            ),
            ConfigField(
                "v4l2_render_resolution",
                str,
                _DEFAULT_RENDER,
                "Render resolution",
                choices=_RENDER_CHOICES,
            ),
            ConfigField(
                "v4l2_framerate",
                int,
                30,
                "Framerate",
            ),
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
        # v4l2src is Linux-only; on macOS we offer AVFoundation as a
        # separate plugin and on Windows the user needs an alternative.
        if not sys.platform.startswith("linux"):
            return (
                False,
                "V4L2 USB Camera is Linux-only (use the AVFoundation USB Camera on macOS)",
            )
        try:
            from gi.repository import Gst

            Gst.init(None)
            if Gst.ElementFactory.find("v4l2src") is None:
                return (
                    False,
                    "v4l2src GStreamer element not found – install gstreamer1.0-plugins-good",
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

    # -- Pipeline -------------------------------------------------------

    def create_pipeline(
        self,
        config: dict[str, Any],
        sink: Any,
        build_overlay_tail: Callable[..., Any],
        prepare_sink: Callable[..., Any],
    ) -> Any:
        """Build a V4L2 capture pipeline.

        ``v4l2src -> [src_caps] -> queue -> [jpegdec] -> [videoscale]
        -> videorate -> capsfilter -> videoconvert -> [overlay tail] -> sink``

        ``src_caps`` pins a mode from the camera's own list (``_choose_mode``),
        never a fixed size: a capture card only lists the incoming signal's
        resolution. With no list to choose from the source stays open. A
        render-resolution preset adds videoscale + a size-pinning capsfilter to
        scale the stream down; ``native`` omits videoscale so the device's own
        resolution flows through (the sink scales it for the widget).
        """
        from gi.repository import Gst

        def make(kind: str, name: str) -> Any:
            elem = Gst.ElementFactory.make(kind, name)
            if elem is None:
                raise RuntimeError(f"{kind} GStreamer element not found – install gstreamer1.0-plugins-base/good")
            return elem

        device = config.get("v4l2_device", "/dev/video0")
        framerate = coerce_positive_int(config.get("v4l2_framerate", 30), 30)
        dims = _render_dimensions(config.get("v4l2_render_resolution", _DEFAULT_RENDER))

        pipeline = Gst.Pipeline.new("v4l2-sink")

        src = make("v4l2src", "v4l2src")
        src.set_property("device", device)

        queue = make("queue", "post_queue")
        queue.set_property("max-size-buffers", 2)
        queue.set_property("max-size-bytes", 0)
        queue.set_property("max-size-time", 0)
        queue.set_property("leaky", 2)

        rate = make("videorate", "videorate")
        capsfilter = make("capsfilter", "capsfilter")
        caps_parts = ["video/x-raw"]
        if dims is not None:
            caps_parts.append(f"width=(int){dims[0]}")
            caps_parts.append(f"height=(int){dims[1]}")
        caps_parts.append(f"framerate=(fraction){framerate}/1")
        caps_str = ",".join(caps_parts)
        capsfilter.set_property("caps", Gst.Caps.from_string(caps_str))
        convert = make("videoconvert", "convert")

        modes = _read_capture_modes(device)
        mode = _choose_mode(modes, dims, framerate, _usb_link_budget(device)) if modes else None
        logger.info(
            "V4L2 source: %s, camera mode: %s, render: %s",
            device,
            mode.describe() if mode else "negotiated",
            caps_str,
        )

        sink = prepare_sink()
        if sink is None:
            raise RuntimeError("No video sink available for V4L2 pipeline")

        chain = [src]
        if mode is not None:
            src_caps = make("capsfilter", "src_caps")
            src_caps.set_property("caps", Gst.Caps.from_string(_mode_caps(mode)))
            chain.append(src_caps)
        chain.append(queue)
        if mode is not None and mode.fourcc in _MJPEG_FOURCCS:
            chain.append(make("jpegdec", "jpegdec"))
        # videoscale only for a fixed render size; with native, a downstream
        # scaler would let the sink's widget size shrink the buffer below the
        # device resolution.
        if dims is not None:
            chain.append(make("videoscale", "videoscale"))
        chain += [rate, capsfilter, convert]

        for elem in (*chain, sink):
            pipeline.add(elem)

        prev = chain[0]
        for elem in chain[1:]:
            if not prev.link(elem):
                raise RuntimeError(f"Failed to link {prev.get_name()} -> {elem.get_name()}")
            prev = elem

        build_overlay_tail(pipeline, convert, sink)
        return pipeline

    # -- Lifecycle hooks ------------------------------------------------

    def on_bus_async_done(self, pipeline: Any) -> None:
        """V4L2: force zero latency – local device."""
        pipeline.set_latency(0)
        logger.info("Pipeline ASYNC_DONE (V4L2) – latency forced to 0")

    # -- Source discovery ------------------------------------------------

    @classmethod
    def discover_sources(cls, timeout: float = 2.0) -> list[str]:
        """Discover V4L2 video devices."""
        return [d["path"] for d in _discover_v4l2_devices()]

    # -- Web UI ---------------------------------------------------------

    @classmethod
    def web_ui_html(cls, config: dict[str, Any]) -> str:
        device = cls._esc(
            config.get("v4l2_device", "/dev/video0"),
        )
        render = _normalize_render(config.get("v4l2_render_resolution", _DEFAULT_RENDER))
        framerate = config.get("v4l2_framerate", 30)
        render_options = "".join(
            f'<option value="{val}"{" selected" if val == render else ""}>{cls._esc(label)}</option>'
            for val, label in _RENDER_CHOICES
        )
        return (
            '<div class="row ndi-row">'
            '    <div class="field wide">'
            "        <label>Device</label>"
            '        <select name="v4l2_device"'
            '                hx-get="/video-input/v4l2/devices"'
            '                hx-trigger="load, click from:'
            '#refresh-v4l2"'
            '                hx-target="this"'
            '                hx-swap="innerHTML">'
            f'            <option value="{device}">'
            f"              {device or '-- Loading... --'}"
            "            </option>"
            "        </select>"
            "    </div>"
            '    <button type="button" id="refresh-v4l2"'
            '            class="secondary"'
            '            style="margin-bottom:0;">'
            "Scan</button>"
            "</div>"
            '<div class="row">'
            '    <div class="field">'
            "        <label>Render resolution</label>"
            '        <select name="v4l2_render_resolution">'
            f"            {render_options}"
            "        </select>"
            "    </div>"
            '    <div class="field">'
            "        <label>FPS</label>"
            '        <input type="number"'
            f'               name="v4l2_framerate"'
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
                "/video-input/v4l2/devices",
                "handle_list_devices",
            ),
        ]

    def handle_list_devices(
        self,
        config: dict[str, Any],
    ) -> str:
        """Return ``<option>`` elements for the device dropdown."""
        devices = _discover_v4l2_devices()
        current = config.get("v4l2_device", "/dev/video0")
        known = {dev["path"] for dev in devices}

        options: list[str] = []
        # Keep the saved device visible whenever it isn't among the current
        # capture nodes – whether it was filtered out (a metadata/codec node
        # picked before the filter was added) or is simply unplugged – so the
        # dropdown never silently rewrites the operator's configured value.
        # Runs regardless of whether the list is empty; a blank saved value
        # gets no phantom entry. "(unavailable)" stays accurate for both the
        # filtered-out and the missing case.
        if current and current not in known:
            cur = self._esc(current)
            options.append(f'<option value="{cur}" selected>{cur} (unavailable)</option>')
        for dev in devices:
            path = self._esc(dev["path"])
            name = self._esc(dev["name"])
            sel = " selected" if dev["path"] == current else ""
            label = f"{name} ({path})"
            options.append(f'<option value="{path}"{sel}>{label}</option>')
        if not options:
            options.append('<option value="/dev/video0">-- No devices found --</option>')
        return "\n".join(options)

    # -- Config ---------------------------------------------------------

    @classmethod
    def get_source_label(
        cls,
        config: dict[str, Any],
    ) -> str:
        device = config.get("v4l2_device", "/dev/video0")
        render = _normalize_render(config.get("v4l2_render_resolution", _DEFAULT_RENDER))
        framerate = config.get("v4l2_framerate", 30)
        name = _read_device_name(device) or device
        return f"{name} ({render}@{framerate})"
