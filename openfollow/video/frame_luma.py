# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Michel Honold and Paul Hermann
"""The mean brightness of a decoded frame, read from every 8th pixel of every 8th row."""

from __future__ import annotations

from typing import Any

import numpy as np

STEP = 8
# Byte offsets of R, G and B in the packed formats gtksink negotiates, and their bytes per pixel.
_PACKED = {
    "RGBx": (0, 1, 2),
    "RGBA": (0, 1, 2),
    "BGRx": (2, 1, 0),
    "BGRA": (2, 1, 0),
    "xRGB": (1, 2, 3),
    "ARGB": (1, 2, 3),
    "xBGR": (3, 2, 1),
    "ABGR": (3, 2, 1),
}
# BT.709 luma weights.
_WEIGHTS = (0.2126, 0.7152, 0.0722)


def mean_luma(data: Any, fmt: str, width: int, height: int, offset: int, stride: int) -> float | None:
    """Mean luma from 0 to 1 of a frame's first plane, None for a format or layout this cannot read.

    I420 averages its Y plane; the packed RGB formats a weighted sum of R, G and B.
    """
    rgb = _PACKED.get(fmt)
    if fmt == "I420":
        pixel = 1
    elif rgb is not None:
        pixel = 4
    else:
        return None
    if width <= 0 or height <= 0 or offset < 0 or stride < width * pixel:
        return None
    buf = np.frombuffer(data, dtype=np.uint8)
    if buf.size < offset + stride * (height - 1) + width * pixel:
        return None
    rows, cols = (height + STEP - 1) // STEP, (width + STEP - 1) // STEP
    view = np.lib.stride_tricks.as_strided(
        buf[offset:], shape=(rows, cols, pixel), strides=(STEP * stride, STEP * pixel, 1), writeable=False
    )
    if rgb is None:
        return float(view.mean()) / 255.0
    r, g, b = (view[:, :, i].mean() for i in rgb)
    return float(_WEIGHTS[0] * r + _WEIGHTS[1] * g + _WEIGHTS[2] * b) / 255.0


def frame_mean_luma(buffer: Any, caps: Any) -> float | None:
    """``mean_luma`` of a GStreamer buffer laid out by its video meta, else by ``caps``."""
    import gi

    gi.require_version("Gst", "1.0")
    gi.require_version("GstVideo", "1.0")
    from gi.repository import Gst, GstVideo

    from openfollow.video.gl_display import frame_layout

    if caps is None:
        return None
    ok, mapped = buffer.map(Gst.MapFlags.READ)
    if not ok:
        return None
    try:
        info = GstVideo.VideoInfo.new_from_caps(caps)
        offsets, strides = frame_layout(GstVideo.buffer_get_video_meta(buffer), info, 1)
        return mean_luma(mapped.data, info.finfo.name, info.width, info.height, offsets[0], strides[0])
    finally:
        buffer.unmap(mapped)
