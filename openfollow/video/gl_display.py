# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Video and HUD on one ``Gtk.GLArea``: decoded frames and HUD pictures go up as textures, composited each refresh.

Asked for with ``OPENFOLLOW_GL_DISPLAY=1`` or the flag file: the receiver ends in this display's sink
instead of gtksink, and the window renders this area once per display refresh instead of pasting the
HUD with Cairo. Each refresh shows the newest frame that arrived, and the HUD as ``Layers``: a kept
picture is uploaded once, the pictures drawn between them each time the HUD changes. PyOpenGL is
imported only when the area is realized.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from openfollow.logging_setup import ThrottledExceptionLogger
from openfollow.runtime.overlay_layers import Layers, Sprite

logger = logging.getLogger(__name__)

ENV_SWITCH = "OPENFOLLOW_GL_DISPLAY"


# The same on a station, where the service's environment lives under /etc.
FLAG_FILE = os.path.join(os.path.expanduser("~"), ".openfollow-gl-display")


def gl_display_requested() -> bool:
    """Whether the GL display is asked for here."""
    return os.environ.get(ENV_SWITCH, "") == "1" or os.path.exists(FLAG_FILE)


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


def planes_end(planes: list[Plane]) -> int:
    """The bytes a buffer needs to hold every plane."""
    return max(p.offset + p.stride * p.height for p in planes)


class LatestFrame:
    """The newest frame from the streaming thread, taken by the GTK main thread at its next render.

    Closed, it holds nothing and takes nothing: a decoder may not stop while frames it decoded are
    held, so the display closes before every NULL transition and opens at the next stream's start.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._frame: Any = None
        self._open = True

    def put(self, frame: Any) -> None:
        with self._lock:
            if self._open:
                self._frame = frame

    def take(self) -> Any:
        """The frame put since the last take, or None."""
        with self._lock:
            frame, self._frame = self._frame, None
            return frame

    def close(self) -> None:
        with self._lock:
            self._open = False
            self._frame = None

    def open(self) -> None:
        with self._lock:
            self._open = True


class SpriteTextures:
    """The textures a sprite list is drawn from, in one GL context: a kept sprite's made once, the others reused.

    ``new`` makes a texture, ``upload`` fills one from a picture, ``delete`` frees a list of them.
    """

    def __init__(
        self, new: Callable[[], int], upload: Callable[[int, Any], None], delete: Callable[[list[int]], None]
    ) -> None:
        self._new, self._upload, self._delete = new, upload, delete
        # Kept sprites' textures by picture identity, holding the picture so the identity stays unique.
        self._kept: dict[int, tuple[Any, int]] = {}
        self._spare: list[int] = []
        self.drawn: list[int] = []

    def update(self, sprites: Sequence[Sprite]) -> None:
        """Textures for a new list, one per sprite in ``drawn``; a kept texture no sprite shows is deleted."""
        kept_before = {tex for _, tex in self._kept.values()}
        pool = self._spare + [t for t in self.drawn if t not in kept_before]
        kept = {id(s.picture) for s in sprites if s.kept}
        if gone := [k for k in self._kept if k not in kept]:
            self._delete([self._kept.pop(k)[1] for k in gone])
        drawn: list[int] = []
        for s in sprites:
            if s.kept:
                entry = self._kept.get(id(s.picture))
                if entry is None:
                    entry = (s.picture, self._new())
                    self._upload(entry[1], s.picture)
                    self._kept[id(s.picture)] = entry
                drawn.append(entry[1])
            else:
                tex = pool.pop() if pool else self._new()
                self._upload(tex, s.picture)
                drawn.append(tex)
        self.drawn = drawn
        self._spare = pool

    def held(self) -> list[int]:
        """Every texture made and not deleted, each once."""
        kept = {tex for _, tex in self._kept.values()}
        return [*self._spare, *(set(self.drawn) - kept), *kept]


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


# Video caps the display's sink takes: frames converted into memory upstream.
FRAME_CAPS = "video/x-raw,format=I420"
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
        gi.require_version("GstVideo", "1.0")
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gst, Gtk

        self._Gst = Gst
        self._hud = hud
        Gst.init(None)
        # fakevideosink, not appsink: it announces GstVideoMeta, so a decoder writes into it without a copy.
        sink = Gst.parse_bin_from_description(
            f'capsfilter caps="{FRAME_CAPS}" ! fakevideosink name=frames sync=false enable-last-sample=false'
            " signal-handoffs=true",
            True,
        )
        sink.set_name("shared_videosink")
        frames = sink.get_by_name("frames")
        frames.connect("handoff", self._on_handoff)
        frames.get_static_pad("sink").add_probe(Gst.PadProbeType.EVENT_DOWNSTREAM, self._on_event)
        self.sink = sink
        self._latest = LatestFrame()
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
        self._colour = yuv_to_rgb(1080)
        self._render_err_log = ThrottledExceptionLogger(logger, "GL display render failed")

    def release_frames(self) -> None:
        """Hand every held frame back before the pipeline stops, and take none until the next stream starts.

        GTK main thread, like the render that takes the frame.
        """
        self._latest.close()

    def set_hud(self, hud: Any) -> None:
        self._hud = hud

    def queue_render(self) -> None:
        self.widget.queue_render()

    def _on_event(self, _pad: Any, info: Any) -> Any:
        # A new pipeline's first event: its frames may be held again.
        if info.get_event().type == self._Gst.EventType.STREAM_START:
            self._latest.open()
        return self._Gst.PadProbeReturn.OK

    def _on_handoff(self, _sink: Any, buf: Any, pad: Any) -> None:
        # Streaming thread: the GTK main thread takes the newest at its next render.
        self._latest.put(self._Gst.Sample.new(buf, pad.get_current_caps(), None, None))

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
            GL.glDeleteTextures([*self._t_yuv, self._t_hud, *self._sprite_tex.held()])
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
        import OpenGL

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
        self._sprite_tex = SpriteTextures(self._texture, self._upload_texture, GL.glDeleteTextures)
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
        # New storage per draw: writing into storage an earlier draw still reads waits for that draw.
        GL.glBufferData(GL.GL_ARRAY_BUFFER, data.nbytes, data, GL.GL_STREAM_DRAW)
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
        self._upload_latest_frame()
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
            self._sprite_tex.update(layers.sprites)
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
        for i, tex in enumerate(self._sprite_tex.drawn):
            GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
            GL.glDrawArrays(GL.GL_TRIANGLE_STRIP, 4 * i, 4)

    def _upload_texture(self, tex: int, picture: Any) -> None:
        import numpy as np

        GL = self._gl
        w, h, stride = picture.get_width(), picture.get_height(), picture.get_stride()
        GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
        GL.glPixelStorei(GL.GL_UNPACK_ROW_LENGTH, stride // 4)
        data = np.frombuffer(picture.get_data(), dtype=np.uint8)
        GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA8, w, h, 0, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, data)
        GL.glPixelStorei(GL.GL_UNPACK_ROW_LENGTH, 0)

    def _upload_latest_frame(self) -> None:
        Gst = self._Gst
        sample = self._latest.take()
        if sample is None:
            return
        from gi.repository import GstVideo

        info = GstVideo.VideoInfo.new_from_caps(sample.get_caps())
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
