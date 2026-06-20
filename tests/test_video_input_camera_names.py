# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Unit tests for ``usb_camera_names``.

The helper dispatches by platform to the V4L2 (Linux) / AVFoundation
(macOS) device-name enumerators and must never raise – a missing or
broken capture backend degrades to an empty list rather than aborting.
"""

from __future__ import annotations

import pytest

from openfollow.video import inputs

pytestmark = pytest.mark.unit


def test_linux_returns_v4l2_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(inputs.platform, "system", lambda: "Linux")
    monkeypatch.setattr(inputs, "_v4l2_node_is_usb", lambda _path: True)
    monkeypatch.setattr(
        "openfollow.video.inputs.v4l2._discover_v4l2_devices",
        lambda: [
            {"path": "/dev/video0", "name": "USB Capture HDMI"},
            {"path": "/dev/video1", "name": "Integrated Camera"},
        ],
    )
    # Sorted + de-duplicated (a USB camera can expose several /dev/video* nodes).
    assert inputs.usb_camera_names() == ["Integrated Camera", "USB Capture HDMI"]


def test_linux_filters_empty_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(inputs.platform, "system", lambda: "Linux")
    monkeypatch.setattr(inputs, "_v4l2_node_is_usb", lambda _path: True)
    monkeypatch.setattr(
        "openfollow.video.inputs.v4l2._discover_v4l2_devices",
        lambda: [
            {"path": "/dev/video0", "name": ""},
            {"path": "/dev/video1", "name": "Webcam"},
        ],
    )
    assert inputs.usb_camera_names() == ["Webcam"]


def test_linux_filters_non_usb_nodes(monkeypatch: pytest.MonkeyPatch) -> None:
    # Filter codec/ISP nodes (bcm2835-codec, video10–23) on Pi; only USB nodes belong in the index.
    monkeypatch.setattr(inputs.platform, "system", lambda: "Linux")
    monkeypatch.setattr(inputs, "_v4l2_node_is_usb", lambda path: path == "/dev/video0")
    monkeypatch.setattr(
        "openfollow.video.inputs.v4l2._discover_v4l2_devices",
        lambda: [
            {"path": "/dev/video0", "name": "USB Capture HDMI"},
            {"path": "/dev/video10", "name": "bcm2835-codec-decode"},
        ],
    )
    assert inputs.usb_camera_names() == ["USB Capture HDMI"]


def test_v4l2_node_is_usb(monkeypatch: pytest.MonkeyPatch) -> None:
    # Resolves the sysfs ``device`` link; a ``/usb`` component ⇒ USB-backed.
    monkeypatch.setattr(
        inputs.os.path,
        "realpath",
        lambda _p: "/sys/devices/platform/soc/fe9c0000.usb/usb1/1-1/1-1:1.0",
    )
    assert inputs._v4l2_node_is_usb("/dev/video0") is True
    monkeypatch.setattr(
        inputs.os.path,
        "realpath",
        lambda _p: "/sys/devices/platform/soc/fec00000.codec",
    )
    assert inputs._v4l2_node_is_usb("/dev/video10") is False


def test_macos_returns_avf_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(inputs.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        "openfollow.video.inputs.avf._discover_avf_devices",
        lambda: [{"unique_id": "X", "name": "FaceTime HD", "index": "0"}],
    )
    assert inputs.usb_camera_names() == ["FaceTime HD"]


def test_unsupported_platform_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(inputs.platform, "system", lambda: "Windows")
    assert inputs.usb_camera_names() == []


def test_enumeration_failure_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom() -> list[dict[str, str]]:
        raise RuntimeError("v4l2 backend exploded")

    monkeypatch.setattr(inputs.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        "openfollow.video.inputs.v4l2._discover_v4l2_devices",
        _boom,
    )
    # Never raises – degrades to an empty list so the bundle's visibility
    # column shows "–" rather than aborting.
    assert inputs.usb_camera_names() == []
