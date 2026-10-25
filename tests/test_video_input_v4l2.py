# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for the V4L2 video input plugin."""

from __future__ import annotations

import errno
import fcntl
import logging
import struct
import sys
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch

import pytest

from openfollow.runtime.receiver_bus import BusError
from openfollow.video.inputs import v4l2 as v4l2_module
from openfollow.video.inputs.v4l2 import (
    V4l2Input,
    _CaptureMode,
    _choose_mode,
    _discover_v4l2_devices,
    _list_modes,
    _mode_caps,
    _node_supports_video_capture,
    _read_capture_modes,
    _read_device_name,
    _UnlistedModes,
    _usb_link_budget,
)
from tests._fake_gst import FakeElement, FakePipeline, make_fake_gst

pytestmark = pytest.mark.unit

# A USB 2.0 webcam's list, as such a camera gives it: MJPEG up to 1080p30, raw
# modes whose larger sizes at 30 fps need more than USB 2.0 carries.
# {fourcc: {(width, height): [interval (numerator, denominator), ...]}}
WEBCAM = {
    "MJPG": {(640, 480): [(1, 30), (1, 10)], (1280, 720): [(1, 30), (1, 10)], (1920, 1080): [(1, 30), (1, 10)]},
    "YUYV": {
        (640, 480): [(1, 30)],
        (848, 480): [(1, 30)],
        (1024, 768): [(1, 30)],
        (1280, 720): [(1, 10), (1, 5)],
        (1920, 1080): [(1, 5)],
    },
}
# A USB 3 capture card: raw only, the incoming signal's size.
CAPTURE_CARD = {"YUYV": {(1920, 1080): [(1, 60), (1, 30)]}}
# Isochronous video per second less the 12-byte UVC header in each packet.
USB2 = 3 * (1024 - 12) * 8000
USB3 = 48 * (1024 - 12) * 8000


def _modes(table: dict) -> list[_CaptureMode]:
    return [
        _CaptureMode(fourcc, w, h, Fraction(den, num))
        for fourcc, sizes in table.items()
        for (w, h), intervals in sizes.items()
        for num, den in intervals
    ]


def _fourcc(text: str) -> int:
    return struct.unpack("<I", text.encode())[0]


class _FakeNode:
    """Answers the three V4L2 listing ioctls from a mode table, like a capture node."""

    def __init__(
        self,
        table: dict,
        *,
        size_type: int = 1,
        ival_type: int = 1,
        error: int | None = None,
        error_request: int | None = None,
    ) -> None:
        self.formats = {_fourcc(f): sizes for f, sizes in table.items()}
        self.size_type = size_type
        self.ival_type = ival_type
        self.error = error
        self.error_request = error_request

    def __call__(self, request: int, buf: bytearray) -> int:
        if self.error is not None and self.error_request in (None, request):
            raise OSError(self.error, "fake ioctl failure")
        if request == v4l2_module._VIDIOC_ENUM_FMT:
            index = struct.unpack_from("=I", buf)[0]
            if index >= len(self.formats):
                raise OSError(errno.EINVAL, "end")
            v4l2_module._FMTDESC.pack_into(buf, 0, index, 1, 0, b"", list(self.formats)[index], 0)
        elif request == v4l2_module._VIDIOC_ENUM_FRAMESIZES:
            index, pixelformat = struct.unpack_from("=II", buf)
            sizes = list(self.formats[pixelformat])
            if index >= len(sizes):
                raise OSError(errno.EINVAL, "end")
            v4l2_module._FRMSIZE.pack_into(buf, 0, index, pixelformat, self.size_type, *sizes[index])
        elif request == v4l2_module._VIDIOC_ENUM_FRAMEINTERVALS:
            index, pixelformat, width, height = struct.unpack_from("=IIII", buf)
            intervals = self.formats[pixelformat][(width, height)]
            if index >= len(intervals):
                raise OSError(errno.EINVAL, "end")
            v4l2_module._FRMIVAL.pack_into(buf, 0, index, pixelformat, width, height, self.ival_type, *intervals[index])
        else:
            raise OSError(errno.ENOTTY, "not a listing ioctl")
        return 0


class TestV4l2Registration:
    """Plugin is auto-discovered by the registry."""

    def test_input_id(self) -> None:
        assert V4l2Input.input_id == "v4l2"

    def test_display_name(self) -> None:
        assert V4l2Input.display_name == "USB Camera"

    def test_in_registry(self) -> None:
        from openfollow.video.inputs import get_registry

        assert "v4l2" in get_registry()


class TestV4l2Config:
    """Config fields match AppConfig entries."""

    def test_config_fields(self) -> None:
        fields = V4l2Input.config_fields()
        names = [f.name for f in fields]
        assert "v4l2_device" in names
        assert "v4l2_render_resolution" in names
        assert "v4l2_framerate" in names

    def test_config_defaults(self) -> None:
        fields = {f.name: f.default for f in V4l2Input.config_fields()}
        assert fields["v4l2_device"] == "/dev/video0"
        assert fields["v4l2_render_resolution"] == "1080p"
        assert fields["v4l2_framerate"] == 30

    def test_render_resolution_choices(self) -> None:
        field = {f.name: f for f in V4l2Input.config_fields()}["v4l2_render_resolution"]
        values = [c[0] for c in field.choices]
        assert values == ["native", "2160p", "1080p", "720p"]


class TestV4l2Capabilities:
    """Capabilities match expected feature flags."""

    def test_has_discovery(self) -> None:
        caps = V4l2Input.capabilities()
        assert caps.has_source_discovery is True
        assert caps.has_source_selection is True

    def test_zero_latency(self) -> None:
        caps = V4l2Input.capabilities()
        assert caps.force_zero_latency is True


class TestV4l2ReconnectPolicy:
    def test_fallback_to_selection(self) -> None:
        policy = V4l2Input.reconnect_policy()
        assert policy.max_attempts == 3
        assert policy.fallback_to_selection is True


class TestV4l2IsAvailable:
    def test_unavailable_off_linux(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        ok, reason = V4l2Input.is_available()
        assert ok is False
        assert "Linux" in reason

    def test_unavailable_when_factory_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        fake = make_fake_gst()
        with patch("gi.repository.Gst", fake):
            ok, reason = V4l2Input.is_available()
        assert ok is False
        assert "v4l2src" in reason

    def test_available_on_linux_with_factory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        fake = make_fake_gst(known_factories={"v4l2src": object()})
        with patch("gi.repository.Gst", fake):
            ok, reason = V4l2Input.is_available()
        assert ok is True
        assert reason == ""

    def test_unavailable_when_gst_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Any exception during the GStreamer probe is reported as a
        generic ``GStreamer not available`` rather than crashing the
        availability check."""
        monkeypatch.setattr(sys, "platform", "linux")

        class _BoomGst:
            @staticmethod
            def init(_):
                raise RuntimeError("gst boom")

        with patch("gi.repository.Gst", _BoomGst):
            ok, reason = V4l2Input.is_available()
        assert ok is False
        assert "GStreamer not available" in reason


class TestV4l2ConfigChanged:
    """config_changed detects field modifications."""

    def test_no_change(self) -> None:
        from openfollow.configuration import AppConfig

        cfg = AppConfig()
        assert V4l2Input.config_changed(cfg, cfg) is False

    def test_device_changed(self) -> None:
        from openfollow.configuration import AppConfig

        old = AppConfig()
        new = AppConfig(v4l2_device="/dev/video1")
        assert V4l2Input.config_changed(old, new) is True


class TestV4l2WebUI:
    """Web UI HTML contains expected form elements."""

    def test_html_contains_device_select(self) -> None:
        html = V4l2Input.web_ui_html(
            {
                "v4l2_device": "/dev/video0",
                "v4l2_render_resolution": "1080p",
                "v4l2_framerate": 30,
            }
        )
        assert "v4l2_device" in html
        assert "v4l2_render_resolution" in html
        assert "v4l2_framerate" in html
        assert "/video-input/v4l2/devices" in html
        assert "Native size" in html
        assert 'value="1080p" selected' in html

    def test_web_routes_declared(self) -> None:
        routes = V4l2Input.web_routes()
        assert len(routes) == 1
        assert routes[0].path == "/video-input/v4l2/devices"


class TestV4l2SourceLabel:
    """Source label formatting."""

    def test_label_format(self) -> None:
        label = V4l2Input.get_source_label(
            {
                "v4l2_device": "/dev/video0",
                "v4l2_render_resolution": "720p",
                "v4l2_framerate": 60,
            }
        )
        assert "720p@60" in label

    def test_unknown_render_normalized_in_label(self) -> None:
        # A legacy/unknown value resolves to the default preset the pipeline
        # actually uses, so the label isn't misleading.
        label = V4l2Input.get_source_label(
            {"v4l2_device": "/dev/video0", "v4l2_render_resolution": "4k", "v4l2_framerate": 30}
        )
        assert "1080p@30" in label
        assert "4k" not in label


class TestV4l2RenderNormalization:
    """Unknown/legacy render values normalize to the default everywhere."""

    def test_web_ui_selects_default_for_unknown(self) -> None:
        html = V4l2Input.web_ui_html({"v4l2_render_resolution": "4k", "v4l2_framerate": 30})
        assert 'value="1080p" selected' in html
        # exactly one render option marked selected
        assert html.count(" selected>") == 1


class TestV4l2Discovery:
    """Device discovery via /dev/video* enumeration."""

    def test_discover_returns_list(self) -> None:
        # May be empty on macOS / CI – just verify the type
        result = V4l2Input.discover_sources()
        assert isinstance(result, list)

    def test_discover_devices_returns_list(self) -> None:
        result = _discover_v4l2_devices()
        assert isinstance(result, list)

    def test_discover_devices_from_stub_fs(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / "video0").write_text("")
        (tmp_path / "video1").write_text("")
        monkeypatch.setattr(
            v4l2_module.glob,
            "glob",
            lambda pattern: [
                str(tmp_path / "video0"),
                str(tmp_path / "video1"),
            ],
        )
        monkeypatch.setattr(
            v4l2_module,
            "_read_device_name",
            lambda path: f"usb-cam-{path.rsplit('/', 1)[-1]}",
        )
        result = _discover_v4l2_devices()
        assert result == [
            {"path": str(tmp_path / "video0"), "name": "usb-cam-video0"},
            {"path": str(tmp_path / "video1"), "name": "usb-cam-video1"},
        ]

    def test_discover_uses_basename_for_unreadable_devices(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Unreadable sysfs entries fall back to the device basename.

        ``_read_device_name`` returns the basename rather than ``None``
        when the sysfs name file can't be opened, so
        ``_discover_v4l2_devices`` surfaces the device with a best-effort
        label instead of silently dropping it.  This is the real
        observable behaviour – a prior version of this test stubbed
        ``_read_device_name`` to return ``None``, but that branch is
        unreachable in production.
        """

        class _FakeOpen:
            def __init__(self, payload: str) -> None:
                self._payload = payload

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self) -> str:
                return self._payload

        monkeypatch.setattr(
            v4l2_module.glob,
            "glob",
            lambda pattern: ["/dev/video0", "/dev/video1"],
        )

        def _fake_open(path, *args, **kwargs):
            if "video1" in str(path):
                raise OSError("permission denied")
            return _FakeOpen("ok\n")

        monkeypatch.setattr("builtins.open", _fake_open)
        # Otherwise the capture probe ioctls whatever the host has at /dev/video1.
        monkeypatch.setattr(v4l2_module, "_node_supports_video_capture", lambda path: True)

        result = _discover_v4l2_devices()

        assert result == [
            {"path": "/dev/video0", "name": "ok"},
            {"path": "/dev/video1", "name": "video1"},
        ]

    def test_discover_drops_non_capture_nodes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Metadata / codec nodes are filtered out – only nodes that
        advertise VIDEO_CAPTURE reach the picker."""
        monkeypatch.setattr(
            v4l2_module.glob,
            "glob",
            lambda pattern: ["/dev/video0", "/dev/video1"],
        )
        monkeypatch.setattr(
            v4l2_module,
            "_read_device_name",
            lambda path: f"cam-{path.rsplit('/', 1)[-1]}",
        )
        monkeypatch.setattr(
            v4l2_module,
            "_node_supports_video_capture",
            lambda path: path == "/dev/video0",  # video1 is the metadata node
        )
        result = _discover_v4l2_devices()
        assert result == [{"path": "/dev/video0", "name": "cam-video0"}]

    def test_read_device_name_nonexistent(self) -> None:
        # Should return basename fallback, not crash
        name = _read_device_name("/dev/video999")
        assert name is not None

    def test_read_device_name_from_sysfs(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        """When sysfs exposes a name, it should be returned – not the basename."""

        class _FakeOpen:
            def __init__(self, payload: str) -> None:
                self._payload = payload

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self) -> str:
                return self._payload

        monkeypatch.setattr(
            "builtins.open",
            lambda path, *a, **k: _FakeOpen("HD Pro Webcam C920\n"),
        )
        assert _read_device_name("/dev/video0") == "HD Pro Webcam C920"

    def test_read_device_name_empty_sysfs_falls_back_to_basename(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Some kernels/drivers expose an empty ``name`` file in sysfs.
        ``f.read().strip()`` would yield ``""`` and propagate as a blank
        device label into the web UI's source picker; the helper must
        treat empty-after-strip as missing and fall back to the basename.
        """

        class _FakeOpen:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self) -> str:
                return "   \n"  # whitespace only -> "" after strip

        monkeypatch.setattr(
            "builtins.open",
            lambda path, *a, **k: _FakeOpen(),
        )
        assert _read_device_name("/dev/video0") == "video0"


class TestV4l2CaptureCapability:
    """``_node_supports_video_capture`` filters non-capture nodes."""

    @staticmethod
    def _fake_open(_path, _flags):
        return 7  # a stand-in fd

    @staticmethod
    def _querycap(capabilities: int, device_caps: int):
        """Fake ``fcntl.ioctl`` that fills the v4l2_capability cap words."""

        def _ioctl(_fd, _request, buf, _mutate):
            struct.pack_into(
                "=II",
                buf,
                v4l2_module._V4L2_CAP_FLAGS_OFFSET,
                capabilities,
                device_caps,
            )
            return 0

        return _ioctl

    def test_capture_node_kept(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(v4l2_module.os, "open", self._fake_open)
        monkeypatch.setattr(v4l2_module.os, "close", lambda fd: None)
        monkeypatch.setattr(
            fcntl,
            "ioctl",
            self._querycap(v4l2_module._V4L2_CAP_VIDEO_CAPTURE, 0),
        )
        assert _node_supports_video_capture("/dev/video0") is True

    def test_metadata_node_dropped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # META_CAPTURE (0x00800000) set, no VIDEO_CAPTURE, no DEVICE_CAPS.
        monkeypatch.setattr(v4l2_module.os, "open", self._fake_open)
        monkeypatch.setattr(v4l2_module.os, "close", lambda fd: None)
        monkeypatch.setattr(fcntl, "ioctl", self._querycap(0x00800000, 0))
        assert _node_supports_video_capture("/dev/video1") is False

    def test_device_caps_trusted_over_union(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When ``DEVICE_CAPS`` is set, the per-node ``device_caps`` decides –
        even though the ``capabilities`` union (across all the device's nodes)
        advertises VIDEO_CAPTURE, this metadata node's ``device_caps`` does
        not, so it is dropped."""
        union = v4l2_module._V4L2_CAP_VIDEO_CAPTURE | v4l2_module._V4L2_CAP_DEVICE_CAPS
        monkeypatch.setattr(v4l2_module.os, "open", self._fake_open)
        monkeypatch.setattr(v4l2_module.os, "close", lambda fd: None)
        monkeypatch.setattr(fcntl, "ioctl", self._querycap(union, 0x00800000))
        assert _node_supports_video_capture("/dev/video1") is False

    def test_device_caps_capture_node_kept(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # DEVICE_CAPS set; the node's own device_caps carries VIDEO_CAPTURE.
        monkeypatch.setattr(v4l2_module.os, "open", self._fake_open)
        monkeypatch.setattr(v4l2_module.os, "close", lambda fd: None)
        monkeypatch.setattr(
            fcntl,
            "ioctl",
            self._querycap(v4l2_module._V4L2_CAP_DEVICE_CAPS, v4l2_module._V4L2_CAP_VIDEO_CAPTURE),
        )
        assert _node_supports_video_capture("/dev/video0") is True

    def test_open_error_fails_open(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _boom(_path, _flags):
            raise OSError("no such device")

        monkeypatch.setattr(v4l2_module.os, "open", _boom)
        # Unprovable -> kept, never hide a possibly-good camera.
        assert _node_supports_video_capture("/dev/video0") is True

    def test_ioctl_error_fails_open_and_closes_fd(self, monkeypatch: pytest.MonkeyPatch) -> None:
        closed: list[int] = []
        monkeypatch.setattr(v4l2_module.os, "open", self._fake_open)
        monkeypatch.setattr(v4l2_module.os, "close", lambda fd: closed.append(fd))

        def _boom(*_args):
            raise OSError("inappropriate ioctl for device")

        monkeypatch.setattr(fcntl, "ioctl", _boom)
        assert _node_supports_video_capture("/dev/video0") is True
        assert closed == [7]  # fd still closed despite the QUERYCAP error


# --------------------------------------------------------------------------- #
# Camera modes
# --------------------------------------------------------------------------- #


class TestListModes:
    def test_lists_every_listed_mode_with_its_rate(self) -> None:
        modes = _list_modes(_FakeNode(WEBCAM))
        assert sorted(modes, key=lambda m: (m.fourcc, m.width, m.rate)) == sorted(
            _modes(WEBCAM), key=lambda m: (m.fourcc, m.width, m.rate)
        )

    def test_keeps_a_fractional_rate_exactly(self) -> None:
        modes = _list_modes(_FakeNode({"MJPG": {(1920, 1080): [(1001, 30000)]}}))
        assert modes == [_CaptureMode("MJPG", 1920, 1080, Fraction(30000, 1001))]

    def test_skips_a_zero_interval(self) -> None:
        assert _list_modes(_FakeNode({"MJPG": {(640, 480): [(0, 30), (1, 30)]}})) == [
            _CaptureMode("MJPG", 640, 480, Fraction(30))
        ]

    @pytest.mark.parametrize("kind", ["size_type", "ival_type"])
    @pytest.mark.parametrize("value", [2, 3])  # continuous, stepwise
    def test_a_range_cannot_be_listed(self, kind: str, value: int) -> None:
        with pytest.raises(_UnlistedModes):
            _list_modes(_FakeNode(WEBCAM, **{kind: value}))

    def test_an_error_other_than_end_of_list_propagates(self) -> None:
        with pytest.raises(OSError):
            _list_modes(_FakeNode(WEBCAM, error=errno.EIO))

    def test_an_error_partway_through_the_list_propagates(self) -> None:
        node = _FakeNode(WEBCAM, error=errno.ENOTTY, error_request=v4l2_module._VIDIOC_ENUM_FRAMEINTERVALS)
        with pytest.raises(OSError):
            _list_modes(node)

    def test_the_fields_sit_at_their_videodev2_offsets(self) -> None:
        fmt = bytearray(64)
        struct.pack_into("=I", fmt, 44, _fourcc("MJPG"))  # v4l2_fmtdesc.pixelformat
        assert v4l2_module._FMTDESC.unpack(fmt)[4] == _fourcc("MJPG")
        size = bytearray(44)
        struct.pack_into("=III", size, 8, 1, 1920, 1080)  # type, discrete.width, discrete.height
        assert v4l2_module._FRMSIZE.unpack(size)[2:] == (1, 1920, 1080)
        ival = bytearray(52)
        struct.pack_into("=III", ival, 16, 1, 1, 30)  # type, discrete.numerator, discrete.denominator
        assert v4l2_module._FRMIVAL.unpack(ival)[4:] == (1, 1, 30)

    def test_the_structs_and_requests_match_videodev2(self) -> None:
        # Sizes and request numbers of v4l2_fmtdesc, v4l2_frmsizeenum and v4l2_frmivalenum.
        assert (v4l2_module._FMTDESC.size, v4l2_module._FRMSIZE.size, v4l2_module._FRMIVAL.size) == (64, 44, 52)
        assert (
            v4l2_module._VIDIOC_ENUM_FMT,
            v4l2_module._VIDIOC_ENUM_FRAMESIZES,
            v4l2_module._VIDIOC_ENUM_FRAMEINTERVALS,
        ) == (0xC0405602, 0xC02C564A, 0xC034564B)

    def test_a_list_that_never_ends_cannot_be_listed(self) -> None:
        def endless(request: int, buf: bytearray) -> int:
            if request != v4l2_module._VIDIOC_ENUM_FMT:
                raise OSError(errno.EINVAL, "end")
            v4l2_module._FMTDESC.pack_into(buf, 0, 0, 1, 0, b"", _fourcc("H264"), 0)
            return 0

        with pytest.raises(_UnlistedModes):
            _list_modes(endless)


class TestReadCaptureModes:
    """The device read itself; the suite's default reads no host camera."""

    @pytest.fixture
    def node(self, monkeypatch: pytest.MonkeyPatch) -> list[int]:
        closed: list[int] = []
        monkeypatch.setattr(v4l2_module.os, "open", lambda path, flags: 7)
        monkeypatch.setattr(v4l2_module.os, "close", closed.append)
        return closed

    def test_reads_the_list_and_closes_the_node(self, node: list[int], monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeNode(CAPTURE_CARD)
        monkeypatch.setattr(fcntl, "ioctl", lambda fd, request, buf, mutate: fake(request, buf))
        assert _read_capture_modes("/dev/video0") == _modes(CAPTURE_CARD)
        assert node == [7]

    @pytest.mark.parametrize("fake", [_FakeNode(WEBCAM, error=errno.EIO), _FakeNode(WEBCAM, size_type=3)])
    def test_an_unreadable_list_reads_as_none_and_closes_the_node(
        self, node: list[int], monkeypatch: pytest.MonkeyPatch, fake: _FakeNode, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setattr(fcntl, "ioctl", lambda fd, request, buf, mutate: fake(request, buf))
        with caplog.at_level(logging.INFO, logger=v4l2_module.__name__):
            assert _read_capture_modes("/dev/video0") is None
        assert node == [7]
        assert "camera modes not listed" in caplog.text

    def test_a_node_that_does_not_open_reads_as_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def refuse(path: str, flags: int) -> int:
            raise OSError(errno.ENOENT, "gone")

        monkeypatch.setattr(v4l2_module.os, "open", refuse)
        assert _read_capture_modes("/dev/video0") is None


class TestUsbLinkBudget:
    @staticmethod
    def _sysfs(tmp_path: Path, speed: str | None, *, under_devices: bool = True) -> Path:
        """``class/video4linux/video0/device`` pointing at a USB interface whose device has ``speed``."""
        base = tmp_path / ("devices" if under_devices else "elsewhere")
        interface = base / "platform" / "usb1" / "1-1" / "1-1:1.0"
        interface.mkdir(parents=True)
        if speed is not None:
            (interface.parent / "speed").write_text(f"{speed}\n")
        (tmp_path / "devices").mkdir(exist_ok=True)
        node = tmp_path / "class" / "video4linux" / "video0"
        node.mkdir(parents=True)
        (node / "device").symlink_to(interface)
        return tmp_path

    @pytest.mark.parametrize(
        ("speed", "budget"),
        [
            ("480", 24_288_000),
            ("12", 1_011_000),
            ("1.5", 1_011_000),
            ("5000", 388_608_000),
            ("10000", 777_216_000),
        ],
    )
    def test_budget_follows_the_link_speed(self, tmp_path: Path, speed: str, budget: float) -> None:
        assert _usb_link_budget("/dev/video0", sysfs_root=str(self._sysfs(tmp_path, speed))) == budget

    def test_a_device_off_usb_has_no_budget(self, tmp_path: Path) -> None:
        assert _usb_link_budget("/dev/video0", sysfs_root=str(self._sysfs(tmp_path, None))) is None

    @pytest.mark.parametrize("speed", ["abc", ""])
    def test_an_unreadable_speed_has_no_budget(self, tmp_path: Path, speed: str) -> None:
        assert _usb_link_budget("/dev/video0", sysfs_root=str(self._sysfs(tmp_path, speed))) is None

    def test_a_device_outside_the_devices_tree_has_no_budget(self, tmp_path: Path) -> None:
        root = self._sysfs(tmp_path, "480", under_devices=False)
        assert _usb_link_budget("/dev/video0", sysfs_root=str(root)) is None

    def test_an_unknown_node_has_no_budget(self, tmp_path: Path) -> None:
        assert _usb_link_budget("/dev/video9", sysfs_root=str(self._sysfs(tmp_path, "480"))) is None

    def test_a_symlinked_device_path_finds_its_node(self, tmp_path: Path) -> None:
        dev = tmp_path / "dev"
        (dev / "v4l" / "by-id").mkdir(parents=True)
        (dev / "video0").touch()
        link = dev / "v4l" / "by-id" / "usb-webcam-video-index0"
        link.symlink_to(dev / "video0")
        root = self._sysfs(tmp_path, "480")
        assert _usb_link_budget(str(link), sysfs_root=str(root)) == 24_288_000

    def test_a_speed_that_cannot_be_read_has_no_budget(self, tmp_path: Path) -> None:
        root = self._sysfs(tmp_path, None)
        (root / "devices" / "platform" / "usb1" / "1-1" / "speed").mkdir()
        assert _usb_link_budget("/dev/video0", sysfs_root=str(root)) is None


def _pick(table: dict, dims: tuple[int, int] | None, framerate: int, budget: float | None) -> str | None:
    mode = _choose_mode(_modes(table), dims, framerate, budget)
    return mode.describe() if mode else None


class TestChooseMode:
    @pytest.mark.parametrize(
        ("dims", "framerate", "expected"),
        [
            ((1920, 1080), 30, "MJPG 1920x1080@30"),  # raw 1080p only at 5 fps
            ((1280, 720), 30, "MJPG 1280x720@30"),  # raw 1024x768@30 is past USB 2.0
            (None, 30, "MJPG 1920x1080@30"),  # native: the largest size listed at the rate
            ((1920, 1080), 10, "MJPG 1920x1080@10"),
            ((640, 480), 30, "YUYV 640x480@30"),  # raw fits the link: no decode
            ((1920, 1080), 60, "MJPG 1920x1080@30"),  # faster than the camera: its fastest
        ],
    )
    def test_usb2_webcam(self, dims: tuple[int, int] | None, framerate: int, expected: str) -> None:
        assert _pick(WEBCAM, dims, framerate, USB2) == expected

    @pytest.mark.parametrize("budget", [USB3, None])
    def test_a_raw_mode_the_link_carries_wins(self, budget: float | None) -> None:
        assert _pick(CAPTURE_CARD, (1920, 1080), 30, budget) == "YUYV 1920x1080@30"
        assert _pick(CAPTURE_CARD, (1280, 720), 30, budget) == "YUYV 1920x1080@30"

    def test_raw_past_the_link_with_no_mjpeg_leaves_the_source_open(self) -> None:
        assert _pick(CAPTURE_CARD, (1920, 1080), 30, USB2) is None

    def test_formats_it_cannot_play_leave_the_source_open(self) -> None:
        assert (
            _pick({"H264": {(1920, 1080): [(1, 30)]}, "ABCD": {(640, 480): [(1, 30)]}}, (1920, 1080), 30, None) is None
        )

    def test_the_smallest_mode_covering_the_render_size(self) -> None:
        table = {"MJPG": {(3840, 2160): [(1, 30)], (1920, 1080): [(1, 30)], (2560, 1440): [(1, 30)]}}
        assert _pick(table, (1920, 1080), 30, USB2) == "MJPG 1920x1080@30"

    def test_the_rate_nearest_the_request_at_one_size(self) -> None:
        table = {"MJPG": {(1920, 1080): [(1, 60), (1, 30)]}}
        assert _pick(table, (1920, 1080), 30, USB2) == "MJPG 1920x1080@30"

    def test_a_rate_just_under_the_request_counts(self) -> None:
        table = {"MJPG": {(1920, 1080): [(1001, 30000)]}, "YUYV": {(640, 480): [(1, 30)]}}
        assert _pick(table, (1920, 1080), 30, USB2) == "MJPG 1920x1080@29.97"

    def test_a_size_offered_only_slower_is_taken_at_its_fastest(self) -> None:
        table = {"MJPG": {(1920, 1080): [(1, 25)]}, "YUYV": {(640, 480): [(1, 30)]}}
        assert _pick(table, (1920, 1080), 30, USB2) == "MJPG 1920x1080@25"

    @pytest.mark.parametrize("dims", [(1920, 1080), None])
    def test_a_faster_small_mode_does_not_take_the_size(self, dims: tuple[int, int] | None) -> None:
        table = {"MJPG": {(1920, 1080): [(1, 30)]}, "YUYV": {(320, 240): [(1, 60)]}}
        assert _pick(table, dims, 60, USB2) == "MJPG 1920x1080@30"

    @pytest.mark.parametrize(("dims", "expected"), [((1280, 720), "MJPG 1280x720@60"), (None, "MJPG 1920x1080@30")])
    def test_a_faster_mode_at_the_render_size_is_taken(self, dims: tuple[int, int] | None, expected: str) -> None:
        table = {"MJPG": {(1920, 1080): [(1, 30)], (1280, 720): [(1, 60), (1, 30)]}}
        assert _pick(table, dims, 60, USB2) == expected

    def test_a_rate_just_under_the_request_counts_at_the_render_size(self) -> None:
        table = {"MJPG": {(1920, 1080): [(1001, 30000)], (2560, 1440): [(1, 30)]}}
        assert _pick(table, (1920, 1080), 30, USB2) == "MJPG 1920x1080@29.97"

    def test_a_raw_mode_a_header_past_the_link_is_not_deliverable(self) -> None:
        # 848x480 YUYV at 30 fps is 24.42 MB/s: under USB 2.0's 24.58, over its 24.29 after the UVC headers.
        table = {"YUYV": {(848, 480): [(1, 30)], (640, 480): [(1, 30)]}, "MJPG": {(848, 480): [(1, 30)]}}
        assert _pick(table, (848, 480), 30, USB2) == "MJPG 848x480@30"
        assert _pick(table, (640, 480), 30, USB2) == "YUYV 640x480@30"

    def test_nothing_covering_takes_the_largest(self) -> None:
        table = {"YUYV": {(640, 480): [(1, 30)], (320, 240): [(1, 30)]}, "MJPG": {(800, 600): [(1, 30)]}}
        assert _pick(table, (1920, 1080), 30, USB2) == "MJPG 800x600@30"

    def test_nothing_covering_prefers_raw_at_the_same_size(self) -> None:
        table = {"MJPG": {(640, 480): [(1, 30)]}, "YUYV": {(640, 480): [(1, 30)]}}
        assert _pick(table, (1920, 1080), 30, USB2) == "YUYV 640x480@30"

    def test_covering_needs_both_width_and_height(self) -> None:
        table = {"YUYV": {(1024, 768): [(1, 30)]}, "MJPG": {(1280, 720): [(1, 30)]}}
        assert _pick(table, (1280, 720), 30, USB3) == "MJPG 1280x720@30"

    @pytest.mark.parametrize(
        ("fourcc", "bytes_per_pixel"),
        [
            ("YUYV", 2),
            ("UYVY", 2),
            ("YVYU", 2),
            ("NV12", 1.5),
            ("YU12", 1.5),
            ("YV12", 1.5),
            ("GREY", 1),
            ("RGB3", 3),
            ("BGR3", 3),
        ],
    )
    def test_the_link_budget_counts_the_format_bytes(self, fourcc: str, bytes_per_pixel: float) -> None:
        mode = _CaptureMode(fourcc, 640, 480, Fraction(30))
        exact = 640 * 480 * bytes_per_pixel * 30
        assert _choose_mode([mode], (640, 480), 30, exact) == mode
        assert _choose_mode([mode], (640, 480), 30, exact - 1) is None


class TestModeCaps:
    @pytest.mark.parametrize(
        ("fourcc", "format_"),
        [
            ("YUYV", "YUY2"),
            ("UYVY", "UYVY"),
            ("YVYU", "YVYU"),
            ("NV12", "NV12"),
            ("YU12", "I420"),
            ("YV12", "YV12"),
            ("GREY", "GRAY8"),
            ("RGB3", "RGB"),
            ("BGR3", "BGR"),
        ],
    )
    def test_raw_caps_name_the_gstreamer_format(self, fourcc: str, format_: str) -> None:
        caps = _mode_caps(_CaptureMode(fourcc, 1280, 720, Fraction(30)))
        assert caps == f"video/x-raw,format=(string){format_},width=(int)1280,height=(int)720,framerate=(fraction)30/1"

    @pytest.mark.parametrize("fourcc", ["MJPG", "JPEG"])
    def test_mjpeg_caps_keep_a_fractional_rate(self, fourcc: str) -> None:
        caps = _mode_caps(_CaptureMode(fourcc, 1920, 1080, Fraction(30000, 1001)))
        assert caps == "image/jpeg,width=(int)1920,height=(int)1080,framerate=(fraction)30000/1001"


# --------------------------------------------------------------------------- #
# Pipeline
# --------------------------------------------------------------------------- #


def _build_v4l2(config=None, *, fake=None) -> FakePipeline:
    from gi.repository import Gst  # noqa: F401

    fake = fake or make_fake_gst()
    sink = FakeElement("shared_videosink")
    with patch("gi.repository.Gst", fake):
        return V4l2Input().create_pipeline(
            config=config or {},
            sink=sink,
            build_overlay_tail=lambda *a: None,
            prepare_sink=lambda: sink,
        )


class TestV4l2Pipeline:
    def test_builds_v4l2_chain_with_caps_and_device(self) -> None:
        pipeline = _build_v4l2(
            {
                "v4l2_device": "/dev/video2",
                "v4l2_render_resolution": "720p",
                "v4l2_framerate": 30,
            }
        )
        src = pipeline.get_by_name("v4l2src")
        assert src is not None
        assert src.properties["device"] == "/dev/video2"

        caps = pipeline.get_by_name("capsfilter").properties["caps"].to_string()
        assert "width=(int)1280" in caps
        assert "height=(int)720" in caps
        assert "framerate=(fraction)30/1" in caps

        for name in ("post_queue", "videoscale", "videorate", "convert"):
            assert pipeline.get_by_name(name) is not None

    def test_source_is_unconstrained_links_to_queue(self) -> None:
        # No caps pinned on the source – it links straight to the queue so it
        # negotiates the device's native resolution.
        pipeline = _build_v4l2({"v4l2_render_resolution": "1080p"})
        src = pipeline.get_by_name("v4l2src")
        assert [e.name for e in src.links] == ["post_queue"]

    def test_native_render_omits_videoscale_and_size_caps(self) -> None:
        # Native passes the device resolution through: no videoscale (which
        # would let the sink widget shrink the buffer) and no size in the caps.
        pipeline = _build_v4l2({"v4l2_render_resolution": "native", "v4l2_framerate": 30})
        assert pipeline.get_by_name("videoscale") is None
        caps = pipeline.get_by_name("capsfilter").properties["caps"].to_string()
        assert "width=" not in caps
        assert "height=" not in caps
        assert "framerate=(fraction)30/1" in caps

    def test_missing_v4l2src_raises(self) -> None:
        fake = make_fake_gst(missing_elements={"v4l2src"})
        with pytest.raises(RuntimeError, match="v4l2src"):
            _build_v4l2(fake=fake)

    def test_missing_downstream_element_raises(self) -> None:
        # A missing scale/convert/rate plugin fails fast naming the element.
        fake = make_fake_gst(missing_elements={"videoscale"})
        with pytest.raises(RuntimeError, match="videoscale"):
            _build_v4l2({"v4l2_render_resolution": "1080p"}, fake=fake)

    def test_prepare_sink_none_raises(self) -> None:
        from gi.repository import Gst  # noqa: F401

        with patch("gi.repository.Gst", make_fake_gst()):
            with pytest.raises(RuntimeError, match="No video sink"):
                V4l2Input().create_pipeline(
                    config={},
                    sink=FakeElement("shared_videosink"),
                    build_overlay_tail=lambda *a: None,
                    prepare_sink=lambda: None,
                )

    def test_on_bus_async_done_forces_zero_latency(self) -> None:
        pipeline = FakePipeline("v4l2")
        V4l2Input().on_bus_async_done(pipeline)
        assert pipeline.latency_values == [0]

    @pytest.mark.parametrize("bad_framerate", [0, -10, "x", None])
    def test_degenerate_framerate_falls_back_to_default(self, bad_framerate) -> None:
        # A 0 / negative / non-int framerate from a hand-edited config or
        # crafted POST must not reach the caps string – a malformed
        # ``framerate=(fraction)0/1`` never negotiates and wedges the receiver.
        pipeline = _build_v4l2({"v4l2_render_resolution": "native", "v4l2_framerate": bad_framerate})
        caps = pipeline.get_by_name("capsfilter").properties["caps"].to_string()
        assert "framerate=(fraction)30/1" in caps


class TestV4l2PinnedMode:
    """With the camera's list readable, the source is pinned to the chosen mode."""

    @pytest.fixture
    def camera(self, monkeypatch: pytest.MonkeyPatch):
        asked: list[str] = []

        def install(table: dict, budget: float | None) -> list[str]:
            monkeypatch.setattr(
                v4l2_module, "_read_capture_modes", lambda device: asked.append(device) or _modes(table)
            )
            monkeypatch.setattr(v4l2_module, "_usb_link_budget", lambda device: asked.append(device) or budget)
            return asked

        return install

    @staticmethod
    def _chain(pipeline: FakePipeline) -> list[str]:
        names, element = [], pipeline.get_by_name("v4l2src")
        while element is not None:
            names.append(element.name)
            element = element.links[0] if element.links else None
        return names

    def test_mjpeg_is_pinned_and_decoded_before_scaling(self, camera) -> None:
        asked = camera(WEBCAM, USB2)
        pipeline = _build_v4l2({"v4l2_device": "/dev/video2", "v4l2_render_resolution": "1080p", "v4l2_framerate": 30})
        assert self._chain(pipeline) == [
            "v4l2src",
            "src_caps",
            "post_queue",
            "jpegdec",
            "videoscale",
            "videorate",
            "capsfilter",
            "convert",
        ]
        caps = pipeline.get_by_name("src_caps").properties["caps"].to_string()
        assert caps == "image/jpeg,width=(int)1920,height=(int)1080,framerate=(fraction)30/1"
        assert set(asked) == {"/dev/video2"}

    def test_raw_is_pinned_without_a_decoder(self, camera) -> None:
        camera(CAPTURE_CARD, USB3)
        pipeline = _build_v4l2({"v4l2_render_resolution": "720p", "v4l2_framerate": 30})
        assert self._chain(pipeline) == [
            "v4l2src",
            "src_caps",
            "post_queue",
            "videoscale",
            "videorate",
            "capsfilter",
            "convert",
        ]
        caps = pipeline.get_by_name("src_caps").properties["caps"].to_string()
        assert caps == "video/x-raw,format=(string)YUY2,width=(int)1920,height=(int)1080,framerate=(fraction)30/1"

    def test_native_mjpeg_decodes_without_scaling(self, camera) -> None:
        camera(WEBCAM, USB2)
        pipeline = _build_v4l2({"v4l2_render_resolution": "native", "v4l2_framerate": 30})
        assert self._chain(pipeline) == [
            "v4l2src",
            "src_caps",
            "post_queue",
            "jpegdec",
            "videorate",
            "capsfilter",
            "convert",
        ]

    def test_no_mode_to_choose_leaves_the_source_open(self, camera) -> None:
        camera(CAPTURE_CARD, USB2)
        pipeline = _build_v4l2({"v4l2_render_resolution": "1080p"})
        assert pipeline.get_by_name("src_caps") is None
        assert [e.name for e in pipeline.get_by_name("v4l2src").links] == ["post_queue"]

    def test_an_empty_list_leaves_the_source_open(self, camera) -> None:
        camera({}, USB2)
        pipeline = _build_v4l2({"v4l2_render_resolution": "1080p"})
        assert pipeline.get_by_name("src_caps") is None

    def test_the_chosen_mode_is_logged(self, camera, caplog: pytest.LogCaptureFixture) -> None:
        camera(WEBCAM, USB2)
        with caplog.at_level(logging.INFO, logger=v4l2_module.__name__):
            _build_v4l2({"v4l2_render_resolution": "720p", "v4l2_framerate": 30})
        assert "camera mode: MJPG 1280x720@30" in caplog.text

    def test_an_open_source_is_logged_as_negotiated(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO, logger=v4l2_module.__name__):
            _build_v4l2({"v4l2_render_resolution": "720p"})
        assert "camera mode: negotiated" in caplog.text

    def test_missing_jpegdec_raises(self, camera) -> None:
        camera(WEBCAM, USB2)
        with pytest.raises(RuntimeError, match="jpegdec"):
            _build_v4l2({"v4l2_render_resolution": "1080p"}, fake=make_fake_gst(missing_elements={"jpegdec"}))

    def test_jpegdec_link_failure_raises(self, camera) -> None:
        camera(WEBCAM, USB2)
        with pytest.raises(RuntimeError, match="jpegdec"):
            _build_v4l2({"v4l2_render_resolution": "1080p"}, fake=make_fake_gst(link_fail_kinds={"jpegdec"}))


class TestV4l2PinFallback:
    """A failed attempt with a pinned mode leaves the next attempt open, once, while the camera is present."""

    @staticmethod
    def _build(source: V4l2Input, device: str) -> FakePipeline:
        sink = FakeElement("shared_videosink")
        with patch("gi.repository.Gst", make_fake_gst()):
            return source.create_pipeline(
                config={"v4l2_device": device, "v4l2_render_resolution": "1080p", "v4l2_framerate": 30},
                sink=sink,
                build_overlay_tail=lambda *a: None,
                prepare_sink=lambda: sink,
            )

    @pytest.fixture
    def webcam(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
        device = tmp_path / "video0"
        device.touch()
        monkeypatch.setattr(v4l2_module, "_read_capture_modes", lambda path: _modes(WEBCAM))
        monkeypatch.setattr(v4l2_module, "_usb_link_budget", lambda path: USB2)
        return str(device)

    def test_the_attempt_after_a_failed_pin_is_open_and_the_next_pins_again(
        self, webcam: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        source = V4l2Input()
        assert self._build(source, webcam).get_by_name("src_caps") is not None
        with caplog.at_level(logging.INFO, logger=v4l2_module.__name__):
            source.on_bus_error(BusError("Internal data stream error."))
            assert self._build(source, webcam).get_by_name("src_caps") is None
        assert "camera mode: negotiated after the pinned mode failed" in caplog.text
        assert self._build(source, webcam).get_by_name("src_caps") is not None

    @staticmethod
    def _show_video(pipeline: FakePipeline) -> None:
        ((probe_type, callback),) = pipeline.get_by_name("convert").get_static_pad("sink").probes
        assert probe_type == "buffer"
        assert callback(None, None) == "remove"

    def test_a_pin_that_showed_a_picture_keeps_its_pin_after_an_error(self, webcam: str) -> None:
        source = V4l2Input()
        self._show_video(self._build(source, webcam))
        source.on_bus_error(BusError("Could not read from resource."))
        assert self._build(source, webcam).get_by_name("src_caps") is not None

    def test_each_attempt_must_show_its_own_picture(self, webcam: str) -> None:
        source = V4l2Input()
        self._show_video(self._build(source, webcam))
        source.on_bus_error(BusError("Could not read from resource."))
        self._build(source, webcam)  # pinned again, fails before a picture
        source.on_bus_error(BusError("Internal data stream error."))
        assert self._build(source, webcam).get_by_name("src_caps") is None

    def test_an_open_build_watches_for_no_picture(self, webcam: str, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(v4l2_module, "_read_capture_modes", lambda path: None)
        pipeline = self._build(V4l2Input(), webcam)
        assert pipeline.get_by_name("convert").get_static_pad("sink").probes == []

    def test_the_open_retry_belongs_to_the_device_that_failed(self, webcam: str, tmp_path: Path) -> None:
        other = tmp_path / "video2"
        other.touch()
        source = V4l2Input()
        self._build(source, webcam)
        source.on_bus_error(BusError("Internal data stream error."))
        assert self._build(source, str(other)).get_by_name("src_caps") is not None
        assert self._build(source, webcam).get_by_name("src_caps") is not None

    def test_a_camera_that_went_away_keeps_its_pin(self, webcam: str) -> None:
        source = V4l2Input()
        self._build(source, webcam)
        Path(webcam).unlink()
        source.on_bus_error(BusError("Cannot identify device"))
        assert self._build(source, webcam).get_by_name("src_caps") is not None

    def test_an_error_on_an_open_source_changes_nothing(self, webcam: str, monkeypatch: pytest.MonkeyPatch) -> None:
        source = V4l2Input()
        monkeypatch.setattr(v4l2_module, "_read_capture_modes", lambda path: None)
        self._build(source, webcam)
        source.on_bus_error(BusError("Internal data stream error."))
        monkeypatch.setattr(v4l2_module, "_read_capture_modes", lambda path: _modes(WEBCAM))
        assert self._build(source, webcam).get_by_name("src_caps") is not None


class TestV4l2LinkFailures:
    """Each link in the v4l2src → queue → videoscale → videorate →
    capsfilter → videoconvert chain raises a precise RuntimeError when
    GStreamer refuses the link."""

    def _build_with(self, link_fail_kinds: set[str]) -> None:
        from gi.repository import Gst  # noqa: F401

        fake = make_fake_gst(link_fail_kinds=link_fail_kinds)
        sink = FakeElement("shared_videosink")
        with patch("gi.repository.Gst", fake):
            V4l2Input().create_pipeline(
                config={},
                sink=sink,
                build_overlay_tail=lambda *a: None,
                prepare_sink=lambda: sink,
            )

    def test_v4l2src_to_queue_link_failure_raises(self) -> None:
        with pytest.raises(RuntimeError, match="v4l2src"):
            self._build_with({"v4l2src"})

    def test_queue_to_videoscale_link_failure_raises(self) -> None:
        with pytest.raises(RuntimeError, match="queue"):
            self._build_with({"queue"})

    def test_videoscale_to_videorate_link_failure_raises(self) -> None:
        with pytest.raises(RuntimeError, match="videoscale"):
            self._build_with({"videoscale"})

    def test_videorate_to_capsfilter_link_failure_raises(self) -> None:
        with pytest.raises(RuntimeError, match="videorate"):
            self._build_with({"videorate"})

    def test_capsfilter_to_videoconvert_link_failure_raises(self) -> None:
        with pytest.raises(RuntimeError, match="capsfilter"):
            self._build_with({"capsfilter"})


# --------------------------------------------------------------------------- #
# handle_list_devices
# --------------------------------------------------------------------------- #


class TestHandleListDevices:
    def test_no_devices_preserves_saved_device(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With no capture nodes present, a saved device is still surfaced
        (flagged unavailable) rather than silently dropped – even when the
        discovered list is empty."""
        monkeypatch.setattr(v4l2_module, "_discover_v4l2_devices", lambda: [])
        html = V4l2Input().handle_list_devices({"v4l2_device": "/dev/video0"})
        assert 'value="/dev/video0" selected' in html
        assert "unavailable" in html
        assert "-- No devices found --" not in html

    def test_no_devices_and_blank_current_shows_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Only when there is genuinely nothing to show – no devices and no
        saved value – does the fallback option appear."""
        monkeypatch.setattr(v4l2_module, "_discover_v4l2_devices", lambda: [])
        html = V4l2Input().handle_list_devices({"v4l2_device": ""})
        assert "-- No devices found --" in html

    def test_current_device_is_selected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            v4l2_module,
            "_discover_v4l2_devices",
            lambda: [
                {"path": "/dev/video0", "name": "Cam One"},
                {"path": "/dev/video1", "name": "Cam Two"},
            ],
        )
        html = V4l2Input().handle_list_devices({"v4l2_device": "/dev/video1"})
        assert 'value="/dev/video1" selected' in html
        assert 'value="/dev/video0">' in html

    def test_device_names_are_escaped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            v4l2_module,
            "_discover_v4l2_devices",
            lambda: [{"path": "/dev/video0", "name": "<evil>"}],
        )
        html = V4l2Input().handle_list_devices({})
        assert "<evil>" not in html
        assert "&lt;evil&gt;" in html

    def test_saved_non_capture_device_kept_visible(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A saved device that no longer passes the capture filter stays in
        the dropdown, selected and flagged, so the operator sees what to
        change – alongside the real capture node."""
        monkeypatch.setattr(
            v4l2_module,
            "_discover_v4l2_devices",
            lambda: [{"path": "/dev/video0", "name": "Cam Link 4K"}],
        )
        html = V4l2Input().handle_list_devices({"v4l2_device": "/dev/video1"})
        assert 'value="/dev/video1" selected' in html
        assert "unavailable" in html
        assert 'value="/dev/video0">' in html

    def test_blank_saved_device_adds_no_phantom_option(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A blank saved device must not produce a phantom '(unavailable)'
        option – only a real, missing path does."""
        monkeypatch.setattr(
            v4l2_module,
            "_discover_v4l2_devices",
            lambda: [{"path": "/dev/video0", "name": "Cam"}],
        )
        html = V4l2Input().handle_list_devices({"v4l2_device": ""})
        assert "unavailable" not in html
        assert 'value="/dev/video0">' in html


# --------------------------------------------------------------------------- #
# Label
# --------------------------------------------------------------------------- #


class TestV4l2Label:
    def test_label_uses_sysfs_name_when_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(v4l2_module, "_read_device_name", lambda path: "Built-in HD Cam")
        label = V4l2Input.get_source_label(
            {
                "v4l2_device": "/dev/video0",
                "v4l2_render_resolution": "1080p",
                "v4l2_framerate": 30,
            }
        )
        assert label == "Built-in HD Cam (1080p@30)"

    def test_label_falls_back_to_device_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(v4l2_module, "_read_device_name", lambda path: "")
        label = V4l2Input.get_source_label(
            {
                "v4l2_device": "/dev/video42",
                "v4l2_render_resolution": "720p",
                "v4l2_framerate": 15,
            }
        )
        assert "/dev/video42" in label
        assert "720p@15" in label
