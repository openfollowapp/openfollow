# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Video and HUD on one ``Gtk.GLArea``: decoded frames and the HUD picture go up as textures, composited each refresh.

Spike, run where ``gl_display_blocker`` and ``probe_gl_context`` allow it: the receiver ends in this
display's sink instead of gtksink, and the window renders this area once per display refresh instead of
pasting the HUD with Cairo. PyOpenGL is imported only by the probe and when the area is realized.
"""

from __future__ import annotations

import ctypes
import importlib.util
import logging
import os
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from openfollow.logging_setup import ThrottledExceptionLogger
from openfollow.runtime.overlay_layers import Layers, Sprite
from openfollow.video.gl_cadence import MAX_QUEUED, Cadence, Durations

logger = logging.getLogger(__name__)

# "0" forces the Cairo Operator Screen where the GL display would run, for an A/B.
ENV_SWITCH = "OPENFOLLOW_GL_DISPLAY"
# The same on a station, where the service's environment lives under /etc.
CAIRO_FLAG = os.path.join(os.path.expanduser("~"), ".openfollow-cairo-display")
# Renders failing in a row, about 2 s at 60 Hz, before the GL display gives way to Cairo.
FAILED_RENDERS_TO_FALL_BACK = 120
# How video frames meet the refresh, for an A/B: "locked" holds them to an even cadence covering the
# arrival spread, "capped" covers it up to a refresh past the median arrival; anything else shows each as it arrives.
CADENCE_FLAG = os.path.join(os.path.expanduser("~"), ".openfollow-gl-cadence")
DEFAULT_REFRESH_US = 16_667
REPORT_EVERY_US = 10_000_000


def gl_display_blocker(platform: str = sys.platform, *, pyopengl: bool | None = None) -> str | None:
    """Why the Operator Screen must stay on Cairo here, or None when the GL display may run.

    The GL area only renders through GTK on Linux (on macOS GTK3's never draws).
    """
    if not platform.startswith("linux"):
        return f"no GL display on {platform}"
    if os.environ.get(ENV_SWITCH, "") == "0" or os.path.exists(CAIRO_FLAG):
        return "Cairo asked for"
    if pyopengl is None:
        pyopengl = importlib.util.find_spec("OpenGL") is not None
    if not pyopengl:
        return "PyOpenGL is not installed"
    return None


def is_gles(version: str | bytes | None) -> bool:
    """Whether a ``GL_VERSION`` string names OpenGL ES 3.0 or later; None when no context is current."""
    text = version.decode() if isinstance(version, bytes) else version
    if text is None or not text.startswith("OpenGL ES "):
        return False
    major = text.removeprefix("OpenGL ES ").split(".")[0]
    return major.isdigit() and int(major) >= 3


def probe_gl_context(gdk_window: Any) -> str | None:  # pragma: no cover - needs a display and a GPU
    """Why ``gdk_window`` cannot give the GL display its context and shaders, or None when it can."""
    if gdk_window is None:
        return "no window to make a GL context on"
    from gi.repository import Gdk

    try:
        context = gdk_window.create_gl_context()
        context.set_use_es(True)
        context.set_required_version(3, 0)
        context.realize()
        context.make_current()
        os.environ.setdefault("PYOPENGL_PLATFORM", "egl")
        import OpenGL  # type: ignore[import-not-found]

        OpenGL.ERROR_CHECKING = False
        from OpenGL import GL

        version = GL.glGetString(GL.GL_VERSION)
        if not is_gles(version):
            return f"no OpenGL ES context ({version!r})"
        for fragment in (_FS_YUV, _FS_NV12, _FS_EXTERNAL, _FS_HUD):
            program = GL.glCreateProgram()
            for kind, source in ((GL.GL_VERTEX_SHADER, _VS), (GL.GL_FRAGMENT_SHADER, fragment)):
                shader = GL.glCreateShader(kind)
                GL.glShaderSource(shader, source)
                GL.glCompileShader(shader)
                if not GL.glGetShaderiv(shader, GL.GL_COMPILE_STATUS):
                    return f"a display shader does not compile: {GL.glGetShaderInfoLog(shader)!r}"
                GL.glAttachShader(program, shader)
                GL.glDeleteShader(shader)
            GL.glLinkProgram(program)
            linked = GL.glGetProgramiv(program, GL.GL_LINK_STATUS)
            GL.glDeleteProgram(program)
            if not linked:
                return "a display shader program does not link"
        return None
    except Exception as exc:
        return f"no GL context: {exc}"
    finally:
        Gdk.GLContext.clear_current()


def cadence_for_mode() -> Cadence[Any]:
    """The cadence the flag file asks for; by default each frame shows at the refresh after it arrives."""
    try:
        with open(CADENCE_FLAG, encoding="utf-8", errors="replace") as flag:
            mode = flag.read().strip()
    except OSError:
        mode = ""
    if mode == "locked":
        return Cadence()
    if mode == "capped":
        return Cadence(max_hold_refreshes=1)
    return Cadence(locked=False)


@dataclass(frozen=True)
class Plane:
    offset: int
    stride: int
    width: int
    height: int


def i420_planes(width: int, height: int, offsets: Sequence[int], strides: Sequence[int]) -> list[Plane]:
    """The Y, U and V planes of an I420 frame; chroma is half size, rounded up."""
    cw, ch = (width + 1) // 2, (height + 1) // 2
    return [
        Plane(offsets[0], strides[0], width, height),
        Plane(offsets[1], strides[1], cw, ch),
        Plane(offsets[2], strides[2], cw, ch),
    ]


def frame_layout(meta: Any, info: Any, n_planes: int) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """A frame's plane offsets and strides: its buffer's video meta when it has one, else its caps' layout."""
    source = info if meta is None else meta
    return tuple(source.offset[:n_planes]), tuple(source.stride[:n_planes])


def nv12_planes(width: int, height: int, layout: tuple[tuple[int, int], tuple[int, int]] | None = None) -> list[Plane]:
    """The Y and interleaved UV planes of an NV12 frame, at ``layout``'s (offsets, strides) or GStreamer's default.

    A UV plane's width counts sample pairs; its stride stays in bytes.
    """
    if layout is None:
        stride = (width + 3) // 4 * 4
        layout = ((0, stride * ((height + 1) // 2 * 2)), (stride, stride))
    (y_offset, uv_offset), (y_stride, uv_stride) = layout
    return [
        Plane(y_offset, y_stride, width, height),
        Plane(uv_offset, uv_stride, (width + 1) // 2, (height + 1) // 2),
    ]


def planes_end(planes: list[Plane]) -> int:
    """The bytes a buffer needs to hold every plane."""
    return max(p.offset + p.stride * p.height for p in planes)


def sprite_quad(
    sprite: Sprite, scale: tuple[float, float], area_w: int, area_h: int
) -> tuple[float, float, float, float]:
    """Where a sprite lands in an area of ``area_w`` x ``area_h`` device pixels, as (x0, y0, x1, y1) in clip space."""
    px, py = sprite.x * scale[0], sprite.y * scale[1]
    pw, ph = sprite.picture.get_width(), sprite.picture.get_height()
    return (
        -1.0 + 2.0 * px / area_w,
        1.0 - 2.0 * (py + ph) / area_h,
        -1.0 + 2.0 * (px + pw) / area_w,
        1.0 - 2.0 * py / area_h,
    )


def describe_durations(name: str, taken: tuple[int, float, float]) -> str:
    count, p50, p99 = taken
    return f"{name} {count}, p50 {p50:.2f} ms p99 {p99:.2f} ms"


def letterbox(frame_w: int, frame_h: int, area_w: int, area_h: int) -> tuple[float, float, float, float]:
    """Where a frame lands in the area keeping its aspect, as (x0, y0, x1, y1) in clip space (-1..1, y up)."""
    if frame_w <= 0 or frame_h <= 0 or area_w <= 0 or area_h <= 0:
        return (-1.0, -1.0, 1.0, 1.0)
    scale = min(area_w / frame_w, area_h / frame_h)
    sx, sy = frame_w * scale / area_w, frame_h * scale / area_h
    return (-sx, -sy, sx, sy)


_VS = """#version 300 es
in vec2 pos; in vec2 uv; out vec2 v_uv;
void main() { v_uv = uv; gl_Position = vec4(pos, 0.0, 1.0); }"""
# ``yr`` is luma's black level and scale; ``m`` holds the V->R, U->G, V->G, U->B terms.
_FS_YUV = """#version 300 es
precision highp float;
in vec2 v_uv; out vec4 o; uniform sampler2D ty, tu, tv; uniform vec2 yr; uniform vec4 m;
void main() {
  float y = yr.y * (texture(ty, v_uv).r - yr.x);
  float u = texture(tu, v_uv).r - 128.0 / 255.0, v = texture(tv, v_uv).r - 128.0 / 255.0;
  o = vec4(y + m.x * v, y - m.y * u - m.z * v, y + m.w * u, 1.0);
}"""
# NV12: chroma interleaved in one two-channel plane.
_FS_NV12 = _FS_YUV.replace("texture(tv, v_uv).r", "texture(tu, v_uv).g")
# Luma weights (Kr, Kb) of the matrices GStreamer names.
LUMA_WEIGHTS = {
    "bt709": (0.2126, 0.0722),
    "bt601": (0.299, 0.114),
    "bt2020": (0.2627, 0.0593),
    "smpte240m": (0.212, 0.087),
    "fcc": (0.30, 0.11),
}


@dataclass(frozen=True)
class YuvToRgb:
    y_black: float
    y_scale: float
    m: tuple[float, float, float, float]


def yuv_to_rgb(height: int, matrix: str = "", color_range: str = "") -> YuvToRgb:
    """The caps' matrix and range; an unnamed matrix is BT.709 above SD, BT.601 else, an unnamed range limited."""
    kr, kb = LUMA_WEIGHTS.get(matrix) or LUMA_WEIGHTS["bt709" if height > 576 else "bt601"]
    kg = 1.0 - kr - kb
    full = color_range == "0-255"
    c = 1.0 if full else 255 / 224
    m = (2 * (1 - kr) * c, 2 * kb * (1 - kb) / kg * c, 2 * kr * (1 - kr) / kg * c, 2 * (1 - kb) * c)
    return YuvToRgb(0.0, 1.0, m) if full else YuvToRgb(16 / 255, 255 / 219, m)


# A decoder's dmabuf frame, sampled through an EGLImage: the driver converts YUV to RGB.
_FS_EXTERNAL = """#version 300 es
#extension GL_OES_EGL_image_external_essl3 : require
precision highp float;
in vec2 v_uv; out vec4 o; uniform samplerExternalOES t;
void main() { o = vec4(texture(t, v_uv).rgb, 1.0); }"""
# Video caps the GL display takes: a decoder's dmabuf as it is, else I420 in memory.
SINK_CAPS = "video/x-raw(memory:DMABuf),format=DMA_DRM; video/x-raw,format=I420"
DRM_FORMAT_MOD_INVALID = 0x00FFFFFFFFFFFFFF
# A hardware decoder's frames queued at most: it has a few buffers beyond its references, and a decode
# waiting too long for one fails; on a Pi 5 stopping the decoder after that hangs the driver.
DMABUF_QUEUED = 2
_EGL_WIDTH, _EGL_HEIGHT, _EGL_NONE = 0x3057, 0x3056, 0x3038
_EGL_LINUX_DMA_BUF_EXT = 0x3270
_GL_TEXTURE_2D, _GL_TEXTURE_EXTERNAL_OES = 0x0DE1, 0x8D65
_EGL_LINUX_DRM_FOURCC_EXT = 0x3271
# Per plane: fd, offset, pitch, modifier low and high words.
_EGL_PLANE_ATTRS = (
    (0x3272, 0x3273, 0x3274, 0x3443, 0x3444),
    (0x3275, 0x3276, 0x3277, 0x3445, 0x3446),
    (0x3278, 0x3279, 0x327A, 0x3447, 0x3448),
)
_EGL_YUV_COLOR_SPACE_HINT_EXT, _EGL_SAMPLE_RANGE_HINT_EXT = 0x327B, 0x327C
_EGL_ITU = {"bt601": 0x327F, "bt709": 0x3280, "bt2020": 0x3281}
_EGL_YUV_FULL_RANGE_EXT, _EGL_YUV_NARROW_RANGE_EXT = 0x3282, 0x3283


def _pad_caps(element: Any, pad_name: str) -> str:
    try:
        pad = element.get_static_pad(pad_name)
        caps = pad.query_caps(None) if pad is not None else None
    except Exception:
        return ""
    return caps.to_string() if caps is not None else ""


def accepts_dmabuf(sink: Any) -> bool:
    """Whether ``sink`` takes a decoder's dmabuf frames as they are, so nothing may convert them on the way."""
    return "memory:DMABuf" in _pad_caps(sink, "sink")


def passes_dmabuf(head: Any) -> bool:
    """Whether ``head`` may hand a decoder's dmabuf frames on: it converts nothing, or names that memory."""
    caps = _pad_caps(head, "src")
    return caps == "ANY" or "memory:DMABuf" in caps


_DRM_VENDOR_BROADCOM, _BROADCOM_SAND128 = 0x07, 4


def _is_sand128(modifier: int) -> bool:
    return modifier >> 56 == _DRM_VENDOR_BROADCOM and modifier & 0xFF == _BROADCOM_SAND128


def dmabuf_pitch(modifier: int, stride: int, width: int) -> int:
    """A plane's pitch for EGL: its meta stride, or the frame width for SAND128, whose stride packs tile counts."""
    return width if _is_sand128(modifier) else stride


def sand_column_height_modifier(modifier: int, luma_stride: int) -> int:
    """A Broadcom SAND128 modifier with its column height filled in from GStreamer's tiled luma stride.

    The decoder's caps leave the height out; GStreamer's stride for a column format packs
    the 8-line tile rows in its upper half. Any other modifier passes unchanged.
    """
    if not _is_sand128(modifier) or (modifier >> 8) & 0xFFFFFFFFFFFF:
        return modifier
    return modifier | ((luma_stride >> 16) * 8) << 8


def parse_drm_format(drm_format: str) -> tuple[int, int]:
    """A caps ``drm-format`` such as ``NV12:0x0700000000044004`` as its DRM fourcc and modifier."""
    name, _, modifier = drm_format.partition(":")
    code = name.ljust(4)[:4].encode("ascii")
    fourcc = code[0] | code[1] << 8 | code[2] << 16 | code[3] << 24
    return fourcc, int(modifier, 16) if modifier else 0


def dmabuf_image_attributes(
    width: int,
    height: int,
    fourcc: int,
    modifier: int,
    planes: list[tuple[int, int, int]],
    matrix: str = "",
    color_range: str = "",
) -> list[int]:
    """The ``eglCreateImageKHR`` attributes importing a dmabuf frame of (fd, offset, pitch) planes."""
    attrs = [_EGL_WIDTH, width, _EGL_HEIGHT, height, _EGL_LINUX_DRM_FOURCC_EXT, fourcc]
    for (fd, offset, pitch), (a_fd, a_offset, a_pitch, a_lo, a_hi) in zip(planes, _EGL_PLANE_ATTRS, strict=False):
        attrs += [a_fd, fd, a_offset, offset, a_pitch, pitch]
        if modifier != DRM_FORMAT_MOD_INVALID:
            attrs += [a_lo, modifier & 0xFFFFFFFF, a_hi, modifier >> 32]
    itu = _EGL_ITU.get("bt709" if matrix not in _EGL_ITU and height > 576 else matrix, _EGL_ITU["bt601"])
    full = color_range == "0-255"
    attrs += [_EGL_YUV_COLOR_SPACE_HINT_EXT, itu]
    attrs += [_EGL_SAMPLE_RANGE_HINT_EXT, _EGL_YUV_FULL_RANGE_EXT if full else _EGL_YUV_NARROW_RANGE_EXT]
    return [*attrs, _EGL_NONE]


# Cairo ARGB32 is premultiplied BGRA in memory.
_FS_HUD = """#version 300 es
precision highp float;
in vec2 v_uv; out vec4 o; uniform sampler2D t;
void main() { o = texture(t, v_uv).bgra; }"""


class GlDisplay:  # pragma: no cover - GTK GL area and GL calls, verified on the station
    """The receiver's video sink and the window's main widget, in one."""

    def __init__(self, hud: Any = None) -> None:
        import gi

        gi.require_version("Gst", "1.0")
        gi.require_version("Gtk", "3.0")
        from gi.repository import GLib, Gst, Gtk

        self._Gst = Gst
        self._monotonic_us = GLib.get_monotonic_time
        self._hud = hud
        Gst.init(None)
        # fakevideosink, not appsink: a decoder hands out its dmabufs only to a sink announcing GstVideoMeta,
        # and appsink's allocation query cannot be answered from Python.
        sink = Gst.parse_bin_from_description(
            f'capsfilter caps="{SINK_CAPS}" ! fakevideosink name=frames sync=false enable-last-sample=false'
            " signal-handoffs=true",
            True,
        )
        sink.set_name("shared_videosink")
        frames = sink.get_by_name("frames")
        frames.connect("handoff", self._on_handoff)
        frames.get_static_pad("sink").add_probe(Gst.PadProbeType.EVENT_DOWNSTREAM, self._on_event)
        self.sink = sink
        self._cadence: Cadence[Any] = cadence_for_mode()
        self._refresh = (0, DEFAULT_REFRESH_US)
        self._report_at = 0
        area = Gtk.GLArea()
        area.set_use_es(True)
        area.set_required_version(3, 0)
        area.set_has_alpha(False)
        area.set_auto_render(False)
        area.connect("realize", self._on_realize)
        area.connect("render", self._on_render)
        area.connect("unrealize", self._on_unrealize)
        self.widget = area
        self._gl: Any = None
        self._ready = False
        self._frame: tuple[int, int] = (0, 0)
        self._hud_serial = -1
        # Kept sprites' textures by picture identity, holding the picture so the identity stays unique.
        self._kept_tex: dict[int, tuple[Any, int]] = {}
        self._drawn_tex: list[int] = []
        self._spare_tex: list[int] = []
        self._colour = yuv_to_rgb(1080)
        # The dmabuf frame on screen: its EGLImage and the sample that keeps its buffer from the decoder.
        self._image: Any = None
        self._image_sample: Any = None
        self._egl: Any = None
        self._external = False
        # A memory frame in NV12 (the decoder's cropped copy) rather than I420.
        self._nv12 = False
        # GTK main thread time per render, per video frame upload and per new sprite list upload.
        self._took = {
            "render": Durations(),
            "video upload": Durations(),
            "video import": Durations(),
            "sprite upload": Durations(),
        }
        self._render_err_log = ThrottledExceptionLogger(logger, "GL display render failed")
        # Told once, on the GTK main thread, why the display cannot go on; the app falls back to Cairo.
        self.on_unavailable: Any = None
        self._failed_renders = 0
        self._gave_up = False

    def release_frames(self) -> None:
        """Hand every held frame back before the pipeline stops, and take none until the next stream starts.

        A Pi 5 HEVC decoder stopping while frames it decoded are still held waits for them forever,
        in the kernel. The image on screen keeps its own reference to the memory, not to the frame.
        GTK main thread, like the render that holds the frame on screen.
        """
        self._cadence.close()
        self._image_sample = None

    def set_hud(self, hud: Any) -> None:
        self._hud = hud

    def queue_render(self, frame_clock: Any) -> None:
        """Render at the refresh ``frame_clock`` is drawing."""
        now = frame_clock.get_frame_time()
        interval, _presentation = frame_clock.get_refresh_info(now)
        self._refresh = (now, interval or DEFAULT_REFRESH_US)
        self.widget.queue_render()

    def _on_event(self, _pad: Any, info: Any) -> Any:
        # A new pipeline's first event: its frames may be held again.
        if info.get_event().type == self._Gst.EventType.STREAM_START:
            self._cadence.open()
        return self._Gst.PadProbeReturn.OK

    def _on_handoff(self, _sink: Any, buf: Any, pad: Any) -> None:
        # Streaming thread: stamp the arrival, the GTK main thread picks per refresh.
        Gst = self._Gst
        arrival = self._monotonic_us()
        event = pad.get_sticky_event(Gst.EventType.SEGMENT, 0)
        segment = event.parse_segment() if event is not None else None
        sample = Gst.Sample.new(buf, pad.get_current_caps(), segment, None)
        # Running time, not pts: a looping clip's pts restart each pass, its running time runs on.
        pts = buf.pts
        none = Gst.CLOCK_TIME_NONE
        ts = none if pts == none or segment is None else segment.to_running_time(Gst.Format.TIME, pts)
        dmabuf = sample.get_caps().get_features(0).contains("memory:DMABuf")
        self._cadence.push(sample, None if ts == none else ts // 1000, arrival, DMABUF_QUEUED if dmabuf else MAX_QUEUED)

    # -- GL ---------------------------------------------------------------

    def _on_realize(self, area: Any) -> None:
        try:
            self._realize(area)
        except Exception as exc:
            logger.exception("GL display could not start")
            self._give_up(f"the GL area could not start: {exc}")

    def _give_up(self, reason: str) -> None:
        if self._gave_up or self.on_unavailable is None:
            return
        self._gave_up = True
        from gi.repository import GLib

        # Not from inside a GTK signal handler of the area the fallback removes.
        GLib.idle_add(lambda: self.on_unavailable(reason) and False)

    def _on_unrealize(self, area: Any) -> None:
        # The area's context shares objects with the window's, so they outlive it unless deleted here.
        if not self._ready:
            return
        self._ready = False
        try:
            area.make_current()
            if area.get_error() is not None:
                return
            GL = self._gl
            kept = {tex for _, tex in self._kept_tex.values()}
            GL.glDeleteTextures([*self._t_yuv, self._t_hud, *self._spare_tex, *set(self._drawn_tex) - kept, *kept])
            GL.glDeleteBuffers(1, [self._quad[1]])
            GL.glDeleteVertexArrays(1, [self._quad[0]])
            GL.glDeleteProgram(self._p_yuv)
            GL.glDeleteProgram(self._p_hud)
            GL.glDeleteTextures([self._t_ext])
            GL.glDeleteProgram(self._p_ext)
            GL.glDeleteProgram(self._p_nv12)
            self._release_image()
        except Exception:
            logger.exception("GL display could not free its objects")

    def _realize(self, area: Any) -> None:
        # A new context starts with empty textures: the next frame and HUD picture allocate them again.
        self._frame = (0, 0)
        self._hud_serial = -1
        self._kept_tex, self._drawn_tex, self._spare_tex = {}, [], []
        self._image = self._image_sample = None
        self._external = False
        area.make_current()
        if area.get_error() is not None:
            raise RuntimeError(area.get_error())
        if sys.platform.startswith("linux"):
            os.environ.setdefault("PYOPENGL_PLATFORM", "egl")
        import OpenGL

        # A glGetError after every call costs more than the calls on a Pi.
        OpenGL.ERROR_CHECKING = False
        from OpenGL import GL

        self._gl = GL
        logger.info("GL display: %s on %s", GL.glGetString(GL.GL_VERSION), GL.glGetString(GL.GL_RENDERER))
        self._p_yuv = self._program(_VS, _FS_YUV)
        self._p_hud = self._program(_VS, _FS_HUD)
        self._p_ext = self._program(_VS, _FS_EXTERNAL)
        self._p_nv12 = self._program(_VS, _FS_NV12)
        self._t_ext = self._texture(_GL_TEXTURE_EXTERNAL_OES)
        self._quad = self._make_quad()
        GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 1)
        self._t_yuv = [self._texture() for _ in range(3)]
        self._t_hud = self._texture()
        self._hud_size: tuple[int, int] = (0, 0)
        GL.glUseProgram(self._p_yuv)
        for unit, name in enumerate(("ty", "tu", "tv")):
            GL.glUniform1i(GL.glGetUniformLocation(self._p_yuv, name), unit)
        GL.glUseProgram(self._p_hud)
        GL.glUniform1i(GL.glGetUniformLocation(self._p_hud, "t"), 0)
        GL.glUseProgram(self._p_ext)
        GL.glUniform1i(GL.glGetUniformLocation(self._p_ext, "t"), 0)
        GL.glUseProgram(self._p_nv12)
        for unit, name in enumerate(("ty", "tu")):
            GL.glUniform1i(GL.glGetUniformLocation(self._p_nv12, name), unit)
        self._ready = True

    def _program(self, vs: str, fs: str) -> int:
        GL = self._gl

        def shader(src: str, kind: int) -> int:
            s = GL.glCreateShader(kind)
            GL.glShaderSource(s, src)
            GL.glCompileShader(s)
            if not GL.glGetShaderiv(s, GL.GL_COMPILE_STATUS):
                raise RuntimeError(GL.glGetShaderInfoLog(s))
            return int(s)

        p = GL.glCreateProgram()
        shaders = (shader(vs, GL.GL_VERTEX_SHADER), shader(fs, GL.GL_FRAGMENT_SHADER))
        for s in shaders:
            GL.glAttachShader(p, s)
        GL.glBindAttribLocation(p, 0, "pos")
        GL.glBindAttribLocation(p, 1, "uv")
        GL.glLinkProgram(p)
        # Freed with the program.
        for s in shaders:
            GL.glDeleteShader(s)
        if not GL.glGetProgramiv(p, GL.GL_LINK_STATUS):
            raise RuntimeError(GL.glGetProgramInfoLog(p))
        return int(p)

    def _texture(self, target: int = _GL_TEXTURE_2D) -> int:
        GL = self._gl
        t = GL.glGenTextures(1)
        GL.glBindTexture(target, t)
        for k in (GL.GL_TEXTURE_MIN_FILTER, GL.GL_TEXTURE_MAG_FILTER):
            GL.glTexParameteri(target, k, GL.GL_LINEAR)
        for k in (GL.GL_TEXTURE_WRAP_S, GL.GL_TEXTURE_WRAP_T):
            GL.glTexParameteri(target, k, GL.GL_CLAMP_TO_EDGE)
        return int(t)

    def _make_quad(self) -> tuple[int, int]:
        GL = self._gl
        vao = GL.glGenVertexArrays(1)
        GL.glBindVertexArray(vao)
        vbo = GL.glGenBuffers(1)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, vbo)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, 16 * 4, None, GL.GL_DYNAMIC_DRAW)
        GL.glEnableVertexAttribArray(0)
        GL.glVertexAttribPointer(0, 2, GL.GL_FLOAT, False, 16, ctypes.c_void_p(0))
        GL.glEnableVertexAttribArray(1)
        GL.glVertexAttribPointer(1, 2, GL.GL_FLOAT, False, 16, ctypes.c_void_p(8))
        return int(vao), int(vbo)

    def _draw_quad(self, x0: float, y0: float, x1: float, y1: float) -> None:
        import numpy as np

        GL = self._gl
        vao, vbo = self._quad
        # Texture rows run top-down; clip space y runs up.
        data = np.array([x0, y1, 0, 0, x1, y1, 1, 0, x0, y0, 0, 1, x1, y0, 1, 1], dtype=np.float32)
        GL.glBindVertexArray(vao)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, vbo)
        # New storage per draw: writing into storage an earlier draw still reads waits for that draw.
        GL.glBufferData(GL.GL_ARRAY_BUFFER, data.nbytes, data, GL.GL_STREAM_DRAW)
        GL.glDrawArrays(GL.GL_TRIANGLE_STRIP, 0, 4)

    # -- per refresh --------------------------------------------------------

    def _on_render(self, area: Any, _context: Any) -> bool:
        if not self._ready:
            return True
        try:
            self._render(area)
            self._failed_renders = 0
        except Exception:
            self._render_err_log.log()
            self._failed_renders += 1
            if self._failed_renders >= FAILED_RENDERS_TO_FALL_BACK:
                self._give_up(f"{self._failed_renders} renders in a row failed")
        return True

    def _render(self, area: Any) -> None:
        started = time.perf_counter_ns()
        self._draw(area)
        self._took["render"].add((time.perf_counter_ns() - started) // 1000)

    def _draw(self, area: Any) -> None:
        GL = self._gl
        scale = area.get_scale_factor()
        alloc = area.get_allocation()
        aw, ah = alloc.width * scale, alloc.height * scale
        self._upload_due_frame()
        self._log_cadence()
        GL.glClearColor(0.0, 0.0, 0.0, 1.0)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT)
        if self._frame != (0, 0) and self._external:
            GL.glDisable(GL.GL_BLEND)
            GL.glUseProgram(self._p_ext)
            GL.glActiveTexture(GL.GL_TEXTURE0)
            GL.glBindTexture(_GL_TEXTURE_EXTERNAL_OES, self._t_ext)
            self._draw_quad(*letterbox(*self._frame, aw, ah))
        elif self._frame != (0, 0):
            GL.glDisable(GL.GL_BLEND)
            program = self._p_nv12 if self._nv12 else self._p_yuv
            GL.glUseProgram(program)
            c = self._colour
            GL.glUniform2f(GL.glGetUniformLocation(program, "yr"), c.y_black, c.y_scale)
            GL.glUniform4f(GL.glGetUniformLocation(program, "m"), *c.m)
            for unit, tex in enumerate(self._t_yuv):
                GL.glActiveTexture(GL.GL_TEXTURE0 + unit)
                GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
            self._draw_quad(*letterbox(*self._frame, aw, ah))
        hud = self._hud
        if hud is not None:
            hud.set_canvas(alloc.width, alloc.height, (float(scale), float(scale)))
            picture, serial = hud.shown_picture()
            if isinstance(picture, Layers):
                self._draw_layers(picture, serial, aw, ah)
            elif picture is not None:
                self._upload_hud(picture, serial)
                GL.glEnable(GL.GL_BLEND)
                GL.glBlendFunc(GL.GL_ONE, GL.GL_ONE_MINUS_SRC_ALPHA)
                GL.glUseProgram(self._p_hud)
                GL.glActiveTexture(GL.GL_TEXTURE0)
                GL.glBindTexture(GL.GL_TEXTURE_2D, self._t_hud)
                self._draw_quad(-1.0, -1.0, 1.0, 1.0)

    def _draw_layers(self, layers: Layers, serial: int, aw: int, ah: int) -> None:
        import numpy as np

        GL = self._gl
        if serial != self._hud_serial:
            started = time.perf_counter_ns()
            self._upload_layers(layers)
            self._took["sprite upload"].add((time.perf_counter_ns() - started) // 1000)
            self._hud_serial = serial
        if not layers.sprites:
            return
        quads = [sprite_quad(s, layers.scale, aw, ah) for s in layers.sprites]
        # Texture rows run top-down; clip space y runs up.
        data = np.array(
            [v for x0, y0, x1, y1 in quads for v in (x0, y1, 0, 0, x1, y1, 1, 0, x0, y0, 0, 1, x1, y0, 1, 1)],
            dtype=np.float32,
        )
        vao, vbo = self._quad
        GL.glBindVertexArray(vao)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, vbo)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, data.nbytes, data, GL.GL_STREAM_DRAW)
        GL.glEnable(GL.GL_BLEND)
        GL.glBlendFunc(GL.GL_ONE, GL.GL_ONE_MINUS_SRC_ALPHA)
        GL.glUseProgram(self._p_hud)
        GL.glActiveTexture(GL.GL_TEXTURE0)
        for i, tex in enumerate(self._drawn_tex):
            GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
            GL.glDrawArrays(GL.GL_TRIANGLE_STRIP, 4 * i, 4)

    def _upload_layers(self, layers: Layers) -> None:
        """Textures for a new list: kept sprites only when new, the others into reused textures."""
        GL = self._gl
        kept_before = {tex for _, tex in self._kept_tex.values()}
        pool = self._spare_tex + [t for t in self._drawn_tex if t not in kept_before]
        kept = {id(s.picture) for s in layers.sprites if s.kept}
        gone = [k for k in self._kept_tex if k not in kept]
        if gone:
            GL.glDeleteTextures([self._kept_tex.pop(k)[1] for k in gone])
        drawn: list[int] = []
        for s in layers.sprites:
            if s.kept:
                entry = self._kept_tex.get(id(s.picture))
                if entry is None:
                    entry = (s.picture, self._texture())
                    self._upload_texture(entry[1], s.picture)
                    self._kept_tex[id(s.picture)] = entry
                drawn.append(entry[1])
            else:
                tex = pool.pop() if pool else self._texture()
                self._upload_texture(tex, s.picture)
                drawn.append(tex)
        self._drawn_tex = drawn
        self._spare_tex = pool

    def _upload_texture(self, tex: int, picture: Any) -> None:
        import numpy as np

        GL = self._gl
        w, h, stride = picture.get_width(), picture.get_height(), picture.get_stride()
        GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
        GL.glPixelStorei(GL.GL_UNPACK_ROW_LENGTH, stride // 4)
        data = np.frombuffer(picture.get_data(), dtype=np.uint8)
        GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA8, w, h, 0, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, data)
        GL.glPixelStorei(GL.GL_UNPACK_ROW_LENGTH, 0)

    def _log_cadence(self) -> None:
        now = self._refresh[0]
        if now - self._report_at < REPORT_EVERY_US:
            return
        if self._report_at and (pacing := getattr(self._hud, "pacing_report", None)) is not None:
            shown, late, held = pacing()
            logger.info(
                "GL HUD: %d pictures, %.1f %% on time, held %d refreshes",
                shown,
                100.0 * (shown - late) / shown if shown else 0.0,
                held,
            )
        if self._report_at:
            logger.info(
                "GL main thread: %s",
                ", ".join(describe_durations(name, d.take()) for name, d in self._took.items()),
            )
            r = self._cadence.report()
            logger.info(
                "GL video cadence: %d shown, %.1f %% for two refreshes, refreshes %s, %d skipped, %d late,"
                " wait p50 %.1f ms p99 %.1f ms, arrival spread p50 %.1f ms p98 %.1f ms",
                r.shown,
                100.0 * r.share(2),
                dict(sorted(r.refreshes.items())),
                r.skipped,
                r.late,
                r.wait_ms_p50,
                r.wait_ms_p99,
                r.spread_ms_p50,
                r.spread_ms_p98,
            )
        self._report_at = now

    def _upload_due_frame(self) -> None:
        Gst = self._Gst
        # A render before the first tick has no refresh time to anchor the cadence on.
        if not self._refresh[0]:
            return
        sample = self._cadence.pick(*self._refresh)
        if sample is None:
            return
        caps = sample.get_caps()
        if caps.get_features(0).contains("memory:DMABuf"):
            started = time.perf_counter_ns()
            self._import_dmabuf(sample)
            self._took["video import"].add((time.perf_counter_ns() - started) // 1000)
            return
        from gi.repository import GstVideo

        if self._external or self._nv12:
            # The plane textures were last given another layout, or none.
            self._frame = (0, 0)
        self._release_image()
        self._external = self._nv12 = False
        info = GstVideo.VideoInfo.new_from_caps(caps)
        cm = info.colorimetry
        self._colour = yuv_to_rgb(info.height, cm.matrix.value_nick, cm.range.value_nick)
        buf = sample.get_buffer()
        # A decoder writing straight into a sink that announces the meta pads its planes past the caps' layout.
        offsets, strides = frame_layout(GstVideo.buffer_get_video_meta(buf), info, 3)
        ok, mapinfo = buf.map(Gst.MapFlags.READ)
        if not ok:
            return
        try:
            planes = i420_planes(info.width, info.height, offsets, strides)
            if mapinfo.size < planes_end(planes):
                raise RuntimeError(f"{mapinfo.size} bytes cannot hold a {info.width}x{info.height} I420 frame")
            started = time.perf_counter_ns()
            self._upload_planes(mapinfo.data, planes, (info.width, info.height))
            self._took["video upload"].add((time.perf_counter_ns() - started) // 1000)
        finally:
            buf.unmap(mapinfo)

    def _import_dmabuf(self, sample: Any) -> None:
        """Bind a decoder's dmabuf frame to the external texture through an EGLImage: no copy, no CPU conversion."""
        import gi

        gi.require_version("GstAllocators", "1.0")
        gi.require_version("GstVideo", "1.0")
        from gi.repository import GstAllocators, GstVideo

        structure = sample.get_caps().get_structure(0)
        fourcc, modifier = parse_drm_format(structure.get_string("drm-format") or "")
        width, height = structure.get_int("width")[1], structure.get_int("height")[1]
        colorimetry = GstVideo.VideoColorimetry()
        colorimetry.from_string(structure.get_string("colorimetry") or "")
        buf = sample.get_buffer()
        meta = GstVideo.buffer_get_video_meta(buf)
        # get_memory, not peek_memory: PyGObject copies a borrowed memory into system memory.
        if meta is None or not GstAllocators.is_dmabuf_memory(buf.get_memory(0)):
            # The decoder cropped the frame into a linear NV12 copy in memory (coded size above the shown one).
            self._upload_cropped_nv12(buf, width, height, colorimetry, meta)
            return
        modifier = sand_column_height_modifier(modifier, meta.stride[0])
        planes = []
        for i in range(meta.n_planes):
            found, index, _length, skip = buf.find_memory(meta.offset[i], 1)
            if not found:
                raise RuntimeError(f"dmabuf plane {i} lies outside the buffer")
            memory = buf.get_memory(index)
            pitch = dmabuf_pitch(modifier, meta.stride[i], width)
            planes.append((GstAllocators.dmabuf_memory_get_fd(memory), memory.offset + skip, pitch))
        attrs = dmabuf_image_attributes(
            width,
            height,
            fourcc,
            modifier,
            planes,
            colorimetry.matrix.value_nick,
            colorimetry.range.value_nick,
        )
        egl = self._egl_calls()
        image = egl.create_image(
            egl.current_display(), None, _EGL_LINUX_DMA_BUF_EXT, None, (ctypes.c_int32 * len(attrs))(*attrs)
        )
        if not image:
            raise RuntimeError(f"EGL refused the dmabuf frame (error 0x{egl.get_error():x})")
        GL = self._gl
        GL.glActiveTexture(GL.GL_TEXTURE0)
        GL.glBindTexture(_GL_TEXTURE_EXTERNAL_OES, self._t_ext)
        egl.target_texture(_GL_TEXTURE_EXTERNAL_OES, image)
        # The previous frame's image and buffer are free once the texture names the new one.
        self._release_image()
        self._image, self._image_sample = image, sample
        self._frame = (width, height)
        self._external = True

    def _upload_cropped_nv12(self, buf: Any, width: int, height: int, colorimetry: Any, meta: Any) -> None:
        Gst, GL = self._Gst, self._gl
        layout = None if meta is None else ((meta.offset[0], meta.offset[1]), (meta.stride[0], meta.stride[1]))
        planes = nv12_planes(width, height, layout)
        ok, mapinfo = buf.map(Gst.MapFlags.READ)
        if not ok:
            return
        try:
            if mapinfo.size < planes_end(planes):
                raise RuntimeError(f"{mapinfo.size} bytes cannot hold a {width}x{height} NV12 frame")
            data = mapinfo.data
            started = time.perf_counter_ns()
            realloc = (width, height) != self._frame or not self._nv12
            for unit, (plane, fmt, channels, pixel) in enumerate(
                zip(planes, (GL.GL_R8, GL.GL_RG8), (GL.GL_RED, GL.GL_RG), (1, 2), strict=True)
            ):
                chunk = data[plane.offset : plane.offset + plane.stride * plane.height]
                size = (plane.width, plane.height)
                GL.glActiveTexture(GL.GL_TEXTURE0 + unit)
                GL.glBindTexture(GL.GL_TEXTURE_2D, self._t_yuv[unit])
                GL.glPixelStorei(GL.GL_UNPACK_ROW_LENGTH, plane.stride // pixel)
                if realloc:
                    GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, fmt, *size, 0, channels, GL.GL_UNSIGNED_BYTE, chunk)
                else:
                    GL.glTexSubImage2D(GL.GL_TEXTURE_2D, 0, 0, 0, *size, channels, GL.GL_UNSIGNED_BYTE, chunk)
            GL.glPixelStorei(GL.GL_UNPACK_ROW_LENGTH, 0)
            self._took["video upload"].add((time.perf_counter_ns() - started) // 1000)
        finally:
            buf.unmap(mapinfo)
        self._colour = yuv_to_rgb(height, colorimetry.matrix.value_nick, colorimetry.range.value_nick)
        self._release_image()
        self._frame = (width, height)
        self._external, self._nv12 = False, True

    def _release_image(self) -> None:
        if self._image is not None:
            egl = self._egl_calls()
            egl.destroy_image(egl.current_display(), self._image)
        self._image = self._image_sample = None

    def _egl_calls(self) -> Any:
        """The EGL image calls, straight from libEGL: PyOpenGL's EGL module cannot load with error checking off."""
        if self._egl is None:
            from types import SimpleNamespace

            lib = ctypes.CDLL("libEGL.so.1")
            lib.eglGetProcAddress.restype = ctypes.c_void_p
            lib.eglGetProcAddress.argtypes = [ctypes.c_char_p]
            lib.eglGetCurrentDisplay.restype = ctypes.c_void_p
            lib.eglGetError.restype = ctypes.c_int32
            vp, u32 = ctypes.c_void_p, ctypes.c_uint32

            def proc(name: str, *signature: Any) -> Any:
                address = lib.eglGetProcAddress(name.encode())
                if not address:
                    raise RuntimeError(f"EGL has no {name}")
                return ctypes.CFUNCTYPE(*signature)(address)

            self._egl = SimpleNamespace(
                current_display=lib.eglGetCurrentDisplay,
                get_error=lib.eglGetError,
                create_image=proc("eglCreateImageKHR", vp, vp, vp, u32, vp, ctypes.POINTER(ctypes.c_int32)),
                destroy_image=proc("eglDestroyImageKHR", u32, vp, vp),
                target_texture=proc("glEGLImageTargetTexture2DOES", None, u32, vp),
            )
        return self._egl

    def _upload_planes(self, data: Any, planes: list[Plane], size: tuple[int, int]) -> None:
        GL = self._gl
        realloc = size != self._frame
        for unit, (tex, plane) in enumerate(zip(self._t_yuv, planes, strict=True)):
            GL.glActiveTexture(GL.GL_TEXTURE0 + unit)
            GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
            GL.glPixelStorei(GL.GL_UNPACK_ROW_LENGTH, plane.stride)
            chunk = data[plane.offset : plane.offset + plane.stride * plane.height]
            if realloc:
                GL.glTexImage2D(
                    GL.GL_TEXTURE_2D, 0, GL.GL_R8, plane.width, plane.height, 0, GL.GL_RED, GL.GL_UNSIGNED_BYTE, chunk
                )
            else:
                GL.glTexSubImage2D(
                    GL.GL_TEXTURE_2D, 0, 0, 0, plane.width, plane.height, GL.GL_RED, GL.GL_UNSIGNED_BYTE, chunk
                )
        GL.glPixelStorei(GL.GL_UNPACK_ROW_LENGTH, 0)
        self._frame = size

    def _upload_hud(self, picture: Any, serial: int) -> None:
        # The HUD's two pictures take turns, so a picture is new when its serial is.
        if serial == self._hud_serial:
            return
        import numpy as np

        GL = self._gl
        picture.flush()
        w, h, stride = picture.get_width(), picture.get_height(), picture.get_stride()
        data = np.frombuffer(picture.get_data(), dtype=np.uint8)
        GL.glActiveTexture(GL.GL_TEXTURE0)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self._t_hud)
        GL.glPixelStorei(GL.GL_UNPACK_ROW_LENGTH, stride // 4)
        if (w, h) != self._hud_size:
            GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA8, w, h, 0, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, data)
            self._hud_size = (w, h)
        else:
            GL.glTexSubImage2D(GL.GL_TEXTURE_2D, 0, 0, 0, w, h, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, data)
        GL.glPixelStorei(GL.GL_UNPACK_ROW_LENGTH, 0)
        self._hud_serial = serial
