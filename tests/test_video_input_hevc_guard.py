# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""HEVC stays off the Pi 5 hardware decoder on kernels that can hang stopping it."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from openfollow.video.inputs import _hevc_guard
from openfollow.video.inputs._hevc_guard import guard_hevc_decoder, hevc_hardware_decoder_safe, kernel_version
from openfollow.video.inputs.rtp import RtpInput
from openfollow.video.inputs.rtsp import RtspInput
from openfollow.video.inputs.srt import SrtInput
from tests._fake_gst import make_fake_gst

pytestmark = pytest.mark.unit


class _Factory:
    def __init__(self) -> None:
        self.ranks: list[int] = []

    def set_rank(self, value: int) -> None:
        self.ranks.append(value)


@pytest.fixture(autouse=True)
def _fresh_log(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_hevc_guard, "_logged", False)


@pytest.fixture
def pi_model(tmp_path: Path) -> Path:
    model = tmp_path / "model"
    model.write_bytes(b"Raspberry Pi 5 Model B Rev 1.0\x00")
    return model


@pytest.mark.parametrize(
    ("release", "expected"),
    [
        ("6.18.39+rpt-rpi-2712", (6, 18, 39)),
        ("6.19", (6, 19, 0)),
        ("24.6.0", (24, 6, 0)),
        ("rpi-kernel", None),
        ("", None),
    ],
    ids=["pi-os", "no-patch", "darwin", "garbage", "empty"],
)
def test_a_kernel_release_reads_as_its_version(release: str, expected: tuple[int, int, int] | None) -> None:
    assert kernel_version(release) == expected


@pytest.mark.parametrize(
    ("release", "safe"),
    [
        ("6.18.39+rpt-rpi-2712", False),
        ("6.18.48-v8-16k+", False),
        ("6.18.49-v8-16k+", True),
        ("6.18.50+rpt-rpi-2712", True),
        ("6.12.47+rpt-rpi-2712", False),
        ("6.19.14-v8-16k+", False),
        ("7.0.20-v8-16k+", False),
        ("7.2.11-v8-16k+", False),
        ("7.3.0-v8-16k+", True),
        ("unknown", False),
    ],
    ids=[
        "packaged-before-fix",
        "built-before-and-after-fix",
        "fix",
        "packaged-after-fix",
        "older-branch",
        "6.19-never-fixed",
        "7.0-never-fixed",
        "7.2-fixed-partway",
        "first-fixed-branch",
        "unreadable",
    ],
)
def test_only_kernels_with_the_stop_fix_keep_the_hevc_hardware_decoder(release: str, safe: bool) -> None:
    assert hevc_hardware_decoder_safe(release) is safe


@pytest.mark.parametrize(("release", "ranked_out"), [("6.18.39+rpt-rpi-2712", True), ("6.18.50+rpt-rpi-2712", False)])
def test_the_hevc_hardware_decoder_is_ranked_out_only_on_an_unsafe_kernel(
    release: str, ranked_out: bool, pi_model: Path
) -> None:
    factory = _Factory()
    gst = make_fake_gst(known_factories={"v4l2slh265dec": factory})

    guard_hevc_decoder(gst, release, pi_model)

    assert factory.ranks == ([gst.Rank.NONE] if ranked_out else [])


@pytest.mark.parametrize("model", [b"Radxa ROCK 5B\x00", None], ids=["other-board", "no-device-tree"])
def test_another_board_keeps_its_hevc_hardware_decoder(tmp_path: Path, model: bytes | None) -> None:
    model_path = tmp_path / "model"
    if model is not None:
        model_path.write_bytes(model)
    factory = _Factory()

    guard_hevc_decoder(make_fake_gst(known_factories={"v4l2slh265dec": factory}), "6.12.47", model_path)

    assert factory.ranks == []


def test_the_board_is_read_from_the_device_tree_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    read: list[Path] = []
    monkeypatch.setattr(_hevc_guard, "is_raspberry_pi", lambda path: read.append(path) or False)

    guard_hevc_decoder(make_fake_gst(), "6.18.39+rpt-rpi-2712")

    assert read == [Path("/proc/device-tree/model")]


def test_an_unsafe_kernel_without_the_hardware_decoder_changes_nothing(
    pi_model: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger=_hevc_guard.__name__):
        guard_hevc_decoder(make_fake_gst(), "6.18.39+rpt-rpi-2712", pi_model)

    assert caplog.records == []


def test_the_software_fallback_is_logged_once(caplog: pytest.LogCaptureFixture, pi_model: Path) -> None:
    gst = make_fake_gst(known_factories={"v4l2slh265dec": _Factory()})
    with caplog.at_level(logging.WARNING, logger=_hevc_guard.__name__):
        guard_hevc_decoder(gst, "6.18.39+rpt-rpi-2712", pi_model)
        guard_hevc_decoder(gst, "6.18.39+rpt-rpi-2712", pi_model)

    messages = [r.getMessage() for r in caplog.records]
    assert len(messages) == 1 and "6.18.39" in messages[0] and "6.18.49" in messages[0]


@pytest.mark.parametrize(("release", "ranked_out"), [("6.18.39+rpt-rpi-2712", True), ("6.18.50+rpt-rpi-2712", False)])
def test_the_running_kernel_is_read_when_none_is_given(
    monkeypatch: pytest.MonkeyPatch, pi_model: Path, release: str, ranked_out: bool
) -> None:
    factory = _Factory()
    monkeypatch.setattr("platform.release", lambda: release)

    guard_hevc_decoder(make_fake_gst(known_factories={"v4l2slh265dec": factory}), model_path=pi_model)

    assert bool(factory.ranks) is ranked_out


@pytest.mark.parametrize("input_cls", [SrtInput, RtpInput, RtspInput], ids=["srt", "rtp", "rtsp"])
def test_every_network_input_guards_the_hevc_decoder(input_cls: type[SrtInput | RtpInput | RtspInput]) -> None:
    from gi.repository import Gst  # noqa: F401

    fake = make_fake_gst()
    seen: list[object] = []
    module = input_cls.__module__
    with patch("gi.repository.Gst", fake), patch(f"{module}.guard_hevc_decoder", seen.append):
        input_cls._prefer_hardware_decoders()

    assert seen == [fake]
