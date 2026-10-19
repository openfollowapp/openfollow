# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The GL display's plain parts: the switch, the I420 plane layout, the letterbox and the YUV to RGB terms."""

from __future__ import annotations

from pathlib import Path

import cairo
import pytest

from openfollow.runtime.overlay_layers import Sprite
from openfollow.video import gl_display
from openfollow.video.gl_display import Plane, i420_planes, letterbox, sprite_quad, yuv_to_rgb

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("env", "flag", "on"),
    [("1", False, True), ("", True, True), ("", False, False), ("0", False, False), ("yes", False, False)],
    ids=["env", "flag-file", "neither", "env-0", "env-not-1"],
)
def test_the_switch_is_the_environment_or_the_flag_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, env: str, flag: bool, on: bool
) -> None:
    monkeypatch.setenv(gl_display.ENV_SWITCH, env)
    flag_file = tmp_path / "flag"
    if flag:
        flag_file.touch()
    monkeypatch.setattr(gl_display, "FLAG_FILE", str(flag_file))
    assert gl_display.gl_display_requested() is on


def test_i420_planes_halve_the_chroma_rounding_up() -> None:
    assert i420_planes(1921, 1081, (0, 100, 200), (1984, 992, 992)) == [
        Plane(0, 1984, 1921, 1081),
        Plane(100, 992, 961, 541),
        Plane(200, 992, 961, 541),
    ]


@pytest.mark.parametrize(
    ("frame", "area", "box"),
    [
        ((1920, 1080), (1920, 1080), (-1.0, -1.0, 1.0, 1.0)),
        ((1280, 720), (1920, 1080), (-1.0, -1.0, 1.0, 1.0)),
        ((1440, 1080), (1920, 1080), (-0.75, -1.0, 0.75, 1.0)),
        ((1920, 1080), (1920, 1440), (-1.0, -0.75, 1.0, 0.75)),
        ((0, 1080), (1920, 1080), (-1.0, -1.0, 1.0, 1.0)),
        ((1920, 1080), (0, 0), (-1.0, -1.0, 1.0, 1.0)),
    ],
    ids=["same", "scaled-up", "pillarbox", "letterbox", "no-frame", "no-area"],
)
def test_a_frame_keeps_its_aspect(
    frame: tuple[int, int], area: tuple[int, int], box: tuple[float, float, float, float]
) -> None:
    assert letterbox(*frame, *area) == pytest.approx(box)


# Published BT.709 / BT.601 YUV to RGB terms (V->R, U->G, V->G, U->B), limited and full range.
_BT709_LIMITED = (1.7927, 0.2132, 0.5329, 2.1124)
_BT709_FULL = (1.5748, 0.1873, 0.4681, 1.8556)
_BT601_LIMITED = (1.5960, 0.3918, 0.8130, 2.0172)
_BT601_FULL = (1.4020, 0.3441, 0.7141, 1.7720)
_BT2020_LIMITED = (1.6787, 0.1873, 0.6504, 2.1418)


@pytest.mark.parametrize(
    ("height", "matrix", "terms"),
    [
        (1080, "bt709", _BT709_LIMITED),
        (480, "bt709", _BT709_LIMITED),
        (1080, "bt601", _BT601_LIMITED),
        (1080, "bt2020", _BT2020_LIMITED),
        (1080, "", _BT709_LIMITED),
        (720, "unknown", _BT709_LIMITED),
        (576, "", _BT601_LIMITED),
        (577, "", _BT709_LIMITED),
    ],
    ids=["bt709", "bt709-sd", "bt601", "bt2020", "hd-unnamed", "hd-unknown", "sd-unnamed", "above-sd-unnamed"],
)
def test_the_colour_matrix_follows_the_caps_else_the_frame_size(
    height: int, matrix: str, terms: tuple[float, ...]
) -> None:
    assert yuv_to_rgb(height, matrix).m == pytest.approx(terms, abs=1e-4)


@pytest.mark.parametrize(
    ("matrix", "color_range", "y_black", "y_scale", "terms"),
    [
        ("bt709", "16-235", 16 / 255, 255 / 219, _BT709_LIMITED),
        ("bt709", "unknown", 16 / 255, 255 / 219, _BT709_LIMITED),
        ("bt709", "0-255", 0.0, 1.0, _BT709_FULL),
        ("bt601", "0-255", 0.0, 1.0, _BT601_FULL),
    ],
    ids=["limited", "unknown-is-limited", "full-709", "full-601"],
)
def test_a_full_range_frame_keeps_its_black_and_white(
    matrix: str, color_range: str, y_black: float, y_scale: float, terms: tuple[float, ...]
) -> None:
    colour = yuv_to_rgb(1080, matrix, color_range)
    assert (colour.y_black, colour.y_scale) == pytest.approx((y_black, y_scale))
    assert colour.m == pytest.approx(terms, abs=1e-4)


@pytest.mark.parametrize(
    ("x", "y", "size", "scale", "expected"),
    [
        (0, 0, (100, 50), (1.0, 1.0), (-1.0, 0.8, -0.8, 1.0)),
        (900, 450, (100, 50), (1.0, 1.0), (0.8, -1.0, 1.0, -0.8)),
        (450, 225, (100, 50), (2.0, 2.0), (0.8, -1.0, 1.0, -0.8)),
    ],
    ids=["top-left", "bottom-right", "hidpi"],
)
def test_a_sprite_lands_where_its_corner_and_size_put_it_in_clip_space(
    x: int, y: int, size: tuple[int, int], scale: tuple[float, float], expected: tuple[float, ...]
) -> None:
    sprite = Sprite(cairo.ImageSurface(cairo.FORMAT_ARGB32, *size), x, y, kept=False)

    assert sprite_quad(sprite, scale, 1000, 500) == pytest.approx(expected)


def test_durations_read_as_count_median_and_99th_percentile() -> None:
    assert gl_display.describe_durations("render", (600, 1.234, 5.0)) == "render 600, p50 1.23 ms p99 5.00 ms"


class _CapsSink:
    """A sink whose sink pad answers a caps query with ``caps``, or has no pad, or fails the query."""

    def __init__(self, caps: str | None, *, pad: bool = True, raises: bool = False) -> None:
        self._caps, self._pad, self._raises = caps, pad, raises

    def get_static_pad(self, name: str) -> object:
        if not self._pad:
            return None
        sink = self

        class _Pad:
            def query_caps(self, _filter: object) -> object:
                if sink._raises:
                    raise TypeError(name)
                if sink._caps is None:
                    return None
                caps = sink._caps

                class _Caps:
                    def to_string(self) -> str:
                        return caps

                return _Caps()

        return _Pad()


@pytest.mark.parametrize(
    ("sink", "expected"),
    [
        (_CapsSink(gl_display.SINK_CAPS), True),
        (_CapsSink("video/x-raw,format=I420"), False),
        (_CapsSink(None), False),
        (_CapsSink(None, pad=False), False),
        (_CapsSink(None, raises=True), False),
    ],
    ids=["gl-display", "memory-only", "no-caps", "no-pad", "query-fails"],
)
def test_only_a_sink_asking_for_dmabuf_takes_it(sink: _CapsSink, expected: bool) -> None:
    assert gl_display.accepts_dmabuf(sink) is expected


@pytest.mark.parametrize(
    ("modifier", "stride", "expected"),
    [
        (0x0700000000000004, 136 << 16 | 15, 0x0700000000044004),
        (0x0700000000044004, 68 << 16 | 15, 0x0700000000044004),
        (0x0700000000000003, 136 << 16 | 15, 0x0700000000000003),
        (0x0100000000000001, 136 << 16 | 15, 0x0100000000000001),
        (0, 1920, 0),
    ],
    ids=["sand128-no-height", "sand128-with-height", "sand64", "other-vendor", "linear"],
)
def test_a_sand128_modifier_gets_its_column_height_from_the_tiled_stride(
    modifier: int, stride: int, expected: int
) -> None:
    assert gl_display.sand_column_height_modifier(modifier, stride) == expected


@pytest.mark.parametrize(
    ("drm_format", "expected"),
    [
        ("NV12:0x0700000000044004", (0x3231564E, 0x0700000000044004)),
        ("NV12", (0x3231564E, 0)),
        ("R8", (0x20203852, 0)),
    ],
    ids=["sand128", "linear", "short-name"],
)
def test_a_drm_format_reads_as_fourcc_and_modifier(drm_format: str, expected: tuple[int, int]) -> None:
    assert gl_display.parse_drm_format(drm_format) == expected


def test_dmabuf_attributes_name_each_plane_with_its_modifier_and_the_colour_hints() -> None:
    attrs = gl_display.dmabuf_image_attributes(
        1920, 1080, 0x3231564E, 0x0700000000044004, [(7, 0, 1920), (7, 4096, 1920)], "bt709", "16-235"
    )

    pairs = dict(zip(attrs[:-1:2], attrs[1::2], strict=True))
    assert attrs[-1] == 0x3038
    assert pairs[0x3057] == 1920 and pairs[0x3056] == 1080 and pairs[0x3271] == 0x3231564E
    assert (pairs[0x3272], pairs[0x3273], pairs[0x3274]) == (7, 0, 1920)
    assert (pairs[0x3275], pairs[0x3276], pairs[0x3277]) == (7, 4096, 1920)
    assert (pairs[0x3443], pairs[0x3444]) == (0x00044004, 0x07000000)
    assert (pairs[0x3445], pairs[0x3446]) == (0x00044004, 0x07000000)
    assert (pairs[0x327B], pairs[0x327C]) == (0x3280, 0x3283)


def test_dmabuf_attributes_leave_out_an_invalid_modifier() -> None:
    attrs = gl_display.dmabuf_image_attributes(64, 32, 0x3231564E, gl_display.DRM_FORMAT_MOD_INVALID, [(3, 0, 64)])

    assert 0x3443 not in attrs[::2]


@pytest.mark.parametrize(
    ("height", "matrix", "color_range", "expected"),
    [
        (1080, "", "", (0x3280, 0x3283)),
        (480, "", "", (0x327F, 0x3283)),
        (1080, "bt601", "0-255", (0x327F, 0x3282)),
        (480, "bt2020", "", (0x3281, 0x3283)),
    ],
    ids=["hd-unnamed", "sd-unnamed", "named-601-full", "named-2020"],
)
def test_dmabuf_colour_hints_follow_the_caps_and_size(
    height: int, matrix: str, color_range: str, expected: tuple[int, int]
) -> None:
    attrs = gl_display.dmabuf_image_attributes(64, height, 0x3231564E, 0, [(3, 0, 64)], matrix, color_range)
    pairs = dict(zip(attrs[:-1:2], attrs[1::2], strict=True))

    assert (pairs[0x327B], pairs[0x327C]) == expected
