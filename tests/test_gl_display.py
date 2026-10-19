# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The GL display's plain parts: the switch, the I420 plane layout, the letterbox and the colour matrix."""

from __future__ import annotations

from pathlib import Path

import pytest

from openfollow.video import gl_display
from openfollow.video.gl_display import BT601, BT709, Plane, i420_planes, letterbox, yuv_matrix

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


@pytest.mark.parametrize(
    ("height", "matrix", "expected"),
    [
        (1080, "bt709", BT709),
        (480, "bt709", BT709),
        (480, "bt2020", BT709),
        (1080, "bt601", BT601),
        (1080, "smpte240m", BT601),
        (1080, "fcc", BT601),
        (1080, "", BT709),
        (720, "unknown", BT709),
        (576, "", BT601),
    ],
)
def test_the_colour_matrix_follows_the_caps_else_the_frame_size(
    height: int, matrix: str, expected: tuple[float, ...]
) -> None:
    assert yuv_matrix(height, matrix) == expected
