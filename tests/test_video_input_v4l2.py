# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for the V4L2 video input plugin."""

from __future__ import annotations

import fcntl
import struct
import sys
from unittest.mock import patch

import pytest

from openfollow.video.inputs import v4l2 as v4l2_module
from openfollow.video.inputs.v4l2 import (
    V4l2Input,
    _discover_v4l2_devices,
    _node_supports_video_capture,
    _read_device_name,
)
from tests._fake_gst import FakeElement, FakePipeline, make_fake_gst

pytestmark = pytest.mark.unit


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
