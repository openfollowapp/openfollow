# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Video and HUD on one ``Gtk.GLArea``: decoded frames and the HUD picture go up as textures, composited each refresh.

Spike (``OPENFOLLOW_GL_DISPLAY=1``): the receiver ends in an ``appsink`` instead of
gtksink, and the window renders this area once per display refresh instead of
pasting the HUD with Cairo. PyOpenGL is imported only when the area is realized.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
from dataclasses import dataclass
from typing import Any

from openfollow.logging_setup import ThrottledExceptionLogger
from openfollow.runtime.overlay_layers import Layers, Sprite
from openfollow.video.gl_cadence import Cadence

logger = logging.getLogger(__name__)

ENV_SWITCH = "OPENFOLLOW_GL_DISPLAY"


# Spike switch on a station, where the service's environment lives under /etc.
FLAG_FILE = os.path.join(os.path.expanduser("~"), ".openfollow-gl-display")
# Shows the newest frame each refresh instead of the locked cadence, for an A/B.
LATEST_FRAME_FLAG = os.path.join(os.path.expanduser("~"), ".openfollow-gl-latest-frame")
DEFAULT_REFRESH_US = 16_667
REPORT_EVERY_US = 10_000_000


def gl_display_requested() -> bool:
    return os.environ.get(ENV_SWITCH, "") == "1" or os.path.exists(FLAG_FILE)


@dataclass(frozen=True)
class Plane:
    offset: int
    stride: int
    width: int
    height: int


def i420_planes(width: int, height: int, offsets: tuple[int, int, int], strides: tuple[int, int, int]) -> list[Plane]:
    """The Y, U and V planes of an I420 frame; chroma is half size, rounded up."""
    cw, ch = (width + 1) // 2, (height + 1) // 2
    return [
        Plane(offsets[0], strides[0], width, height),
        Plane(offsets[1], strides[1], cw, ch),
        Plane(offsets[2], strides[2], cw, ch),
    ]


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
        sink = Gst.ElementFactory.make("appsink", "shared_videosink")
        sink.set_property("caps", Gst.Caps.from_string("video/x-raw,format=I420"))
        sink.set_property("max-buffers", 2)
        sink.set_property("drop", True)
        sink.set_property("sync", False)
        sink.set_property("emit-signals", True)
        sink.connect("new-sample", self._on_new_sample)
        self.sink = sink
        self._cadence: Cadence[Any] = Cadence(locked=not os.path.exists(LATEST_FRAME_FLAG))
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
        self._render_err_log = ThrottledExceptionLogger(logger, "GL display render failed")

    def set_hud(self, hud: Any) -> None:
        self._hud = hud

    def queue_render(self, frame_clock: Any) -> None:
        """Render at the refresh ``frame_clock`` is drawing."""
        now = frame_clock.get_frame_time()
        interval, _presentation = frame_clock.get_refresh_info(now)
        self._refresh = (now, interval or DEFAULT_REFRESH_US)
        self.widget.queue_render()

    def _on_new_sample(self, sink: Any) -> Any:
        # Streaming thread: stamp the arrival, the GTK main thread picks per refresh.
        Gst = self._Gst
        sample = sink.try_pull_sample(0)
        if sample is not None:
            # Running time, not pts: a looping clip's pts restart each pass, its running time runs on.
            pts = sample.get_buffer().pts
            none = Gst.CLOCK_TIME_NONE
            ts = none if pts == none else sample.get_segment().to_running_time(Gst.Format.TIME, pts)
            self._cadence.push(sample, None if ts == none else ts // 1000, self._monotonic_us())
        return Gst.FlowReturn.OK

    # -- GL ---------------------------------------------------------------

    def _on_realize(self, area: Any) -> None:
        try:
            self._realize(area)
        except Exception:
            logger.exception("GL display could not start")

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
            GL.glDeleteTextures([*self._t_yuv, self._t_hud, *self._spare_tex, *self._drawn_tex])
            if self._kept_tex:
                GL.glDeleteTextures([tex for _, tex in self._kept_tex.values()])
            self._kept_tex, self._drawn_tex, self._spare_tex = {}, [], []
            GL.glDeleteBuffers(1, [self._quad[1]])
            GL.glDeleteVertexArrays(1, [self._quad[0]])
            GL.glDeleteProgram(self._p_yuv)
            GL.glDeleteProgram(self._p_hud)
        except Exception:
            logger.exception("GL display could not free its objects")

    def _realize(self, area: Any) -> None:
        # A new context starts with empty textures: the next frame and HUD picture allocate them again.
        self._frame = (0, 0)
        self._hud_serial = -1
        area.make_current()
        if area.get_error() is not None:
            raise RuntimeError(area.get_error())
        if sys.platform.startswith("linux"):
            os.environ.setdefault("PYOPENGL_PLATFORM", "egl")
        import OpenGL  # type: ignore[import-not-found]  # spike: installed on the test station only

        # A glGetError after every call costs more than the calls on a Pi.
        OpenGL.ERROR_CHECKING = False
        from OpenGL import GL

        self._gl = GL
        logger.info("GL display: %s on %s", GL.glGetString(GL.GL_VERSION), GL.glGetString(GL.GL_RENDERER))
        self._p_yuv = self._program(_VS, _FS_YUV)
        self._p_hud = self._program(_VS, _FS_HUD)
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

    def _texture(self) -> int:
        GL = self._gl
        t = GL.glGenTextures(1)
        GL.glBindTexture(GL.GL_TEXTURE_2D, t)
        for k in (GL.GL_TEXTURE_MIN_FILTER, GL.GL_TEXTURE_MAG_FILTER):
            GL.glTexParameteri(GL.GL_TEXTURE_2D, k, GL.GL_LINEAR)
        for k in (GL.GL_TEXTURE_WRAP_S, GL.GL_TEXTURE_WRAP_T):
            GL.glTexParameteri(GL.GL_TEXTURE_2D, k, GL.GL_CLAMP_TO_EDGE)
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
        GL.glBufferSubData(GL.GL_ARRAY_BUFFER, 0, data.nbytes, data)
        GL.glDrawArrays(GL.GL_TRIANGLE_STRIP, 0, 4)

    # -- per refresh --------------------------------------------------------

    def _on_render(self, area: Any, _context: Any) -> bool:
        if not self._ready:
            return True
        try:
            self._render(area)
        except Exception:
            self._render_err_log.log()
        return True

    def _render(self, area: Any) -> None:
        GL = self._gl
        scale = area.get_scale_factor()
        alloc = area.get_allocation()
        aw, ah = alloc.width * scale, alloc.height * scale
        self._upload_due_frame()
        self._log_cadence()
        GL.glClearColor(0.0, 0.0, 0.0, 1.0)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT)
        if self._frame != (0, 0):
            GL.glDisable(GL.GL_BLEND)
            GL.glUseProgram(self._p_yuv)
            c = self._colour
            GL.glUniform2f(GL.glGetUniformLocation(self._p_yuv, "yr"), c.y_black, c.y_scale)
            GL.glUniform4f(GL.glGetUniformLocation(self._p_yuv, "m"), *c.m)
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
            self._upload_layers(layers)
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
        picture.flush()
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
        from gi.repository import GstVideo

        info = GstVideo.VideoInfo.new_from_caps(sample.get_caps())
        cm = info.colorimetry
        self._colour = yuv_to_rgb(info.height, cm.matrix.value_nick, cm.range.value_nick)
        buf = sample.get_buffer()
        ok, mapinfo = buf.map(Gst.MapFlags.READ)
        if not ok:
            return
        try:
            planes = i420_planes(
                info.width,
                info.height,
                (info.offset[0], info.offset[1], info.offset[2]),
                (info.stride[0], info.stride[1], info.stride[2]),
            )
            self._upload_planes(mapinfo.data, planes, (info.width, info.height))
        finally:
            buf.unmap(mapinfo)

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
