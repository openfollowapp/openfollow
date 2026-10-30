# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Michel Honold and Paul Hermann
"""The mean brightness of a decoded frame, from its bytes and from a real GStreamer buffer."""

from __future__ import annotations

from typing import Any

import pytest

from openfollow.video.frame_luma import mean_luma

pytestmark = pytest.mark.unit


def _plane(width: int, height: int, stride: int, pixel: bytes, pad: int = 0xFF, offset: int = 0) -> bytes:
    """Rows of ``pixel``, each padded to ``stride`` with ``pad``; ``offset`` bytes of ``pad`` before them."""
    row = pixel * width
    return bytes([pad]) * offset + (row + bytes([pad]) * (stride - len(row))) * height


@pytest.mark.parametrize(("width", "height", "stride"), [(16, 8, 16), (13, 9, 16), (1, 1, 4), (17, 33, 24)])
def test_i420_reads_the_y_plane_and_never_its_padding(width: int, height: int, stride: int) -> None:
    data = _plane(width, height, stride, bytes([51]))
    assert mean_luma(data, "I420", width, height, 0, stride) == pytest.approx(0.2)


def test_i420_reads_from_the_planes_offset() -> None:
    data = _plane(13, 9, 16, bytes([102]), offset=40)
    assert mean_luma(data, "I420", 13, 9, 40, 16) == pytest.approx(0.4)


def test_only_every_8th_pixel_of_every_8th_row_is_read() -> None:
    width = height = 16
    data = bytearray(_plane(width, height, width, bytes([0])))
    for y in (0, 8):
        for x in (0, 8):
            data[y * width + x] = 255
    assert mean_luma(bytes(data), "I420", width, height, 0, width) == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("fmt", "pixel"),
    [
        ("BGRx", bytes([10, 20, 30, 0xEE])),
        ("BGRA", bytes([10, 20, 30, 0xEE])),
        ("RGBx", bytes([30, 20, 10, 0xEE])),
        ("RGBA", bytes([30, 20, 10, 0xEE])),
        ("xRGB", bytes([0xEE, 30, 20, 10])),
        ("ARGB", bytes([0xEE, 30, 20, 10])),
        ("xBGR", bytes([0xEE, 10, 20, 30])),
        ("ABGR", bytes([0xEE, 10, 20, 30])),
    ],
)
def test_packed_rgb_weighs_red_green_and_blue_and_skips_the_fourth_byte(fmt: str, pixel: bytes) -> None:
    # R 30, G 20, B 10 in every format.
    expected = (0.2126 * 30 + 0.7152 * 20 + 0.0722 * 10) / 255
    assert mean_luma(_plane(13, 9, 56, pixel), fmt, 13, 9, 0, 56) == pytest.approx(expected)


@pytest.mark.parametrize("fmt", ["NV12", "YUY2", "GRAY8", "ENCODED", ""])
def test_a_format_it_cannot_read_is_none(fmt: str) -> None:
    assert mean_luma(bytes(4096), fmt, 16, 16, 0, 64) is None


@pytest.mark.parametrize(
    ("fmt", "width", "height", "offset", "stride", "size"),
    [
        ("I420", 16, 8, 0, 16, 16 * 7),  # shorter than its last row
        ("BGRx", 16, 8, 0, 64, 64 * 7 + 63),
        ("I420", 16, 8, 0, 8, 4096),  # a stride shorter than a row
        ("I420", 0, 8, 0, 16, 4096),
        ("I420", 16, 0, 0, 16, 4096),
        ("I420", 16, 8, -1, 16, 4096),
    ],
)
def test_a_layout_the_buffer_cannot_hold_is_none(
    fmt: str, width: int, height: int, offset: int, stride: int, size: int
) -> None:
    assert mean_luma(bytes(size), fmt, width, height, offset, stride) is None


# -- real GStreamer buffers ------------------------------------------------------------------


def _gst() -> Any:
    try:
        import gi

        gi.require_version("Gst", "1.0")
        gi.require_version("GstVideo", "1.0")
        from gi.repository import Gst
    except (ImportError, ValueError):
        pytest.skip("PyGObject / GStreamer is not importable")
    Gst.init(None)
    return Gst


@pytest.mark.parametrize(("width", "height"), [(16, 8), (13, 9), (1919, 1081)])
def test_an_i420_buffer_of_known_value(width: int, height: int) -> None:
    from openfollow.video.frame_luma import frame_mean_luma

    gst = _gst()
    from gi.repository import GstVideo

    caps = gst.Caps.from_string(f"video/x-raw,format=I420,width={width},height={height}")
    info = GstVideo.VideoInfo.new_from_caps(caps)
    # Y at 204; the chroma planes after it at 0 must not count.
    y_bytes = info.offset[1]
    buf = gst.Buffer.new_wrapped(bytes([204]) * y_bytes + bytes(info.size - y_bytes))
    assert frame_mean_luma(buf, caps) == pytest.approx(0.8)


@pytest.mark.parametrize(("fmt", "pixel"), [("BGRx", bytes([0, 0, 255, 0])), ("RGBx", bytes([255, 0, 0, 0]))])
def test_a_packed_rgb_buffer_of_known_value(fmt: str, pixel: bytes) -> None:
    from openfollow.video.frame_luma import frame_mean_luma

    gst = _gst()
    from gi.repository import GstVideo

    caps = gst.Caps.from_string(f"video/x-raw,format={fmt},width=13,height=9")
    info = GstVideo.VideoInfo.new_from_caps(caps)
    stride = info.stride[0]
    buf = gst.Buffer.new_wrapped(_plane(13, 9, stride, pixel, pad=0))
    assert frame_mean_luma(buf, caps) == pytest.approx(0.2126)


def test_an_unsupported_buffer_is_none() -> None:
    from openfollow.video.frame_luma import frame_mean_luma

    gst = _gst()
    caps = gst.Caps.from_string("video/x-raw,format=NV12,width=16,height=8")
    assert frame_mean_luma(gst.Buffer.new_wrapped(bytes(16 * 12)), caps) is None
    assert frame_mean_luma(gst.Buffer.new_wrapped(bytes(16)), None) is None


def test_a_buffer_that_cannot_be_mapped_is_none() -> None:
    from openfollow.video.frame_luma import frame_mean_luma

    gst = _gst()
    caps = gst.Caps.from_string("video/x-raw,format=I420,width=16,height=8")

    class _Unmappable:
        def map(self, _flags: Any) -> tuple[bool, None]:
            return False, None

    assert frame_mean_luma(_Unmappable(), caps) is None
