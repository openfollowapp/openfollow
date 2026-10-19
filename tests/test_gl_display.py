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
