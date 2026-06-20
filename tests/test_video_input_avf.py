# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for the AVFoundation (macOS USB camera) video input plugin."""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from openfollow.video.inputs import avf as avf_module
from openfollow.video.inputs.avf import (
    AvfInput,
    _discover_avf_devices,
    _resolve_device_index,
)
from tests._fake_gst import FakeElement, FakePipeline, make_fake_gst


class _FakeProps:
    def __init__(self, mapping: dict[str, str]) -> None:
        self._m = mapping

    def get_string(self, key: str) -> str | None:
        return self._m.get(key)


class _FakeDevice:
    def __init__(
        self,
        *,
        props: dict[str, str] | None,
        display_name: str = "",
    ) -> None:
        self._props = _FakeProps(props) if props is not None else None
        self._name = display_name

    def get_properties(self) -> _FakeProps | None:
        return self._props

    def get_display_name(self) -> str:
        return self._name


class _FakeMonitor:
    def __init__(
        self,
        devices: list[_FakeDevice],
        *,
        start_ok: bool = True,
    ) -> None:
        self._devices = devices
        self._start_ok = start_ok
        self.filters: list[tuple[str, object]] = []

    def add_filter(self, classification: str, caps: object) -> None:
        self.filters.append((classification, caps))

    def start(self) -> bool:
        return self._start_ok

    def stop(self) -> None:
        pass

    def get_devices(self) -> list[_FakeDevice]:
        return list(self._devices)


def _make_fake_gst_with_devices(
    devices: list[_FakeDevice],
    *,
    start_ok: bool = True,
):
    fake = make_fake_gst()

    class _DeviceMonitorNs:
        @staticmethod
        def new() -> _FakeMonitor:
            return _FakeMonitor(devices, start_ok=start_ok)

    fake.DeviceMonitor = _DeviceMonitorNs  # type: ignore[attr-defined]
    return fake


pytestmark = pytest.mark.unit


class TestAvfRegistration:
    """Plugin is auto-discovered by the registry."""

    def test_input_id(self) -> None:
        assert AvfInput.input_id == "avf"

    def test_display_name(self) -> None:
        assert AvfInput.display_name == "USB Camera (AVFoundation)"

    def test_in_registry(self) -> None:
        from openfollow.video.inputs import get_registry

        assert "avf" in get_registry()


class TestAvfConfig:
    """Config fields match AppConfig entries."""

    def test_config_fields(self) -> None:
        names = {f.name for f in AvfInput.config_fields()}
        assert names == {
            "avf_unique_id",
            "avf_device_index",
            "avf_width",
            "avf_height",
            "avf_framerate",
        }

    def test_config_defaults(self) -> None:
        fields = {f.name: f.default for f in AvfInput.config_fields()}
        assert fields["avf_unique_id"] == ""
        assert fields["avf_device_index"] == -1
        assert fields["avf_width"] == 1920
        assert fields["avf_height"] == 1080
        assert fields["avf_framerate"] == 30


class TestAvfCapabilities:
    def test_has_discovery(self) -> None:
        caps = AvfInput.capabilities()
        assert caps.has_source_discovery is True
        assert caps.has_source_selection is True

    def test_zero_latency(self) -> None:
        assert AvfInput.capabilities().force_zero_latency is True


class TestAvfReconnectPolicy:
    def test_fallback_to_selection(self) -> None:
        policy = AvfInput.reconnect_policy()
        assert policy.max_attempts == 3
        assert policy.fallback_to_selection is True


class TestAvfIsAvailable:
    def test_unavailable_off_darwin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        ok, reason = AvfInput.is_available()
        assert ok is False
        assert "macOS" in reason

    def test_unavailable_when_factory_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        fake = make_fake_gst()
        with patch("gi.repository.Gst", fake):
            ok, reason = AvfInput.is_available()
        assert ok is False
        assert "avfvideosrc" in reason

    def test_available_on_darwin_with_factory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        fake = make_fake_gst(known_factories={"avfvideosrc": object()})
        with patch("gi.repository.Gst", fake):
            ok, reason = AvfInput.is_available()
        assert ok is True
        assert reason == ""

    def test_unavailable_when_gst_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Exceptions during the GStreamer probe (e.g. ``Gst.init`` fails)
        are reported as a generic ``GStreamer not available``, never
        propagated up to the registry."""
        monkeypatch.setattr(sys, "platform", "darwin")

        class _BoomGst:
            @staticmethod
            def init(_):
                raise RuntimeError("gst boom")

        with patch("gi.repository.Gst", _BoomGst):
            ok, reason = AvfInput.is_available()
        assert ok is False
        assert "GStreamer not available" in reason


class TestDiscoverAvfDevices:
    """Direct coverage of ``_discover_avf_devices`` branches."""

    def test_off_darwin_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        assert _discover_avf_devices() == []

    def test_gi_import_failure_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")

        real_import = (
            __builtins__["__import__"]
            if isinstance(
                __builtins__,
                dict,
            )
            else __builtins__.__import__
        )

        def _fail_gi(name, *args, **kwargs):
            if name == "gi.repository" or name.startswith("gi.repository"):
                raise ImportError("no gi here")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr("builtins.__import__", _fail_gi)
        assert _discover_avf_devices() == []

    def test_monitor_start_failure_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        fake = _make_fake_gst_with_devices([], start_ok=False)
        with patch("gi.repository.Gst", fake):
            assert _discover_avf_devices() == []

    def test_skips_devices_without_properties(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        fake = _make_fake_gst_with_devices(
            [
                _FakeDevice(props=None, display_name="ghost"),
                _FakeDevice(
                    props={
                        "device.api": "avf",
                        "avf.unique_id": "FT",
                    },
                    display_name="FaceTime",
                ),
            ]
        )
        with patch("gi.repository.Gst", fake):
            result = _discover_avf_devices()
        assert result == [
            {"unique_id": "FT", "name": "FaceTime", "index": "0"},
        ]

    def test_skips_non_avf_backends(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """V4L2 / DirectShow devices are filtered out – only AVF devices
        survive into the indexed list, because the index is what
        ``avfvideosrc device-index`` consumes."""
        monkeypatch.setattr(sys, "platform", "darwin")
        fake = _make_fake_gst_with_devices(
            [
                _FakeDevice(
                    props={"device.api": "v4l2"},
                    display_name="not-avf",
                ),
                _FakeDevice(
                    props={
                        "device.api": "avf",
                        "avf.unique_id": "FX30",
                    },
                    display_name="Sony",
                ),
            ]
        )
        with patch("gi.repository.Gst", fake):
            result = _discover_avf_devices()
        assert result == [
            {"unique_id": "FX30", "name": "Sony", "index": "0"},
        ]

    def test_falls_back_to_unique_id_when_display_name_blank(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When AVF returns a blank display_name, we surface the
        unique_id as the label so the dropdown isn't empty."""
        monkeypatch.setattr(sys, "platform", "darwin")
        fake = _make_fake_gst_with_devices(
            [
                _FakeDevice(
                    props={
                        "device.api": "avf",
                        "avf.unique_id": "MYID",
                    },
                    display_name="",
                ),
            ]
        )
        with patch("gi.repository.Gst", fake):
            result = _discover_avf_devices()
        assert result == [
            {"unique_id": "MYID", "name": "MYID", "index": "0"},
        ]

    def test_gst_init_failure_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")

        class _BoomGst:
            @staticmethod
            def init(_):
                raise RuntimeError("plugin registry corrupted")

        with patch("gi.repository.Gst", _BoomGst):
            assert _discover_avf_devices() == []


class TestAvfConfigChanged:
    """config_changed detects field modifications."""

    def test_no_change(self) -> None:
        from openfollow.configuration import AppConfig

        cfg = AppConfig()
        assert AvfInput.config_changed(cfg, cfg) is False

    def test_unique_id_changed(self) -> None:
        from openfollow.configuration import AppConfig

        old = AppConfig()
        new = AppConfig(avf_unique_id="ABCD-1234")
        assert AvfInput.config_changed(old, new) is True

    def test_resolution_changed(self) -> None:
        from openfollow.configuration import AppConfig

        old = AppConfig()
        new = AppConfig(avf_width=1280, avf_height=720)
        assert AvfInput.config_changed(old, new) is True


class TestAvfWebUI:
    def test_html_contains_form_fields(self) -> None:
        html = AvfInput.web_ui_html(
            {
                "avf_unique_id": "ABCD-1234",
                "avf_width": 1920,
                "avf_height": 1080,
                "avf_framerate": 30,
            }
        )
        assert "avf_unique_id" in html
        assert "avf_width" in html
        assert "avf_height" in html
        assert "avf_framerate" in html
        assert "/video-input/avf/devices" in html

    def test_web_routes_declared(self) -> None:
        routes = AvfInput.web_routes()
        assert len(routes) == 1
        assert routes[0].path == "/video-input/avf/devices"


class TestAvfSourceLabel:
    def test_label_uses_unique_id_and_resolution(self) -> None:
        label = AvfInput.get_source_label(
            {
                "avf_unique_id": "ABCD-1234",
                "avf_width": 1280,
                "avf_height": 720,
                "avf_framerate": 60,
            }
        )
        assert label == "ABCD-1234 (1280x720@60)"

    def test_label_falls_back_to_default_when_no_unique_id(self) -> None:
        label = AvfInput.get_source_label(
            {
                "avf_unique_id": "",
                "avf_width": 1920,
                "avf_height": 1080,
                "avf_framerate": 30,
            }
        )
        assert label == "default (1920x1080@30)"

    def test_label_does_not_invoke_device_discovery(self, monkeypatch: pytest.MonkeyPatch) -> None:

        def _boom() -> list[dict[str, str]]:
            raise AssertionError("get_source_label must not call discovery")

        monkeypatch.setattr(avf_module, "_discover_avf_devices", _boom)

        label = AvfInput.get_source_label(
            {
                "avf_unique_id": "ABCD-1234",
                "avf_width": 1280,
                "avf_height": 720,
                "avf_framerate": 60,
            }
        )
        assert label == "ABCD-1234 (1280x720@60)"


class TestAvfResolveDeviceIndex:
    def test_empty_unique_id_returns_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            avf_module,
            "_discover_avf_devices",
            lambda: [],
        )
        assert _resolve_device_index("", 2) == 2
        assert _resolve_device_index("", -1) == -1

    def test_resolves_to_position_in_avf_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            avf_module,
            "_discover_avf_devices",
            lambda: [
                {"unique_id": "FX30", "name": "Sony", "index": "0"},
                {"unique_id": "FT", "name": "FaceTime", "index": "1"},
            ],
        )
        assert _resolve_device_index("FT", -1) == 1
        assert _resolve_device_index("FX30", -1) == 0

    def test_unknown_unique_id_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            avf_module,
            "_discover_avf_devices",
            lambda: [{"unique_id": "FX30", "name": "Sony", "index": "0"}],
        )
        # fallback >= 0 is honored
        assert _resolve_device_index("missing", 3) == 3
        # fallback negative -> default to 0
        assert _resolve_device_index("missing", -1) == 0


# --------------------------------------------------------------------------- #
# Pipeline
# --------------------------------------------------------------------------- #


def _build_avf(
    config=None,
    *,
    fake=None,
    monkeypatch: pytest.MonkeyPatch | None = None,
) -> FakePipeline:
    from gi.repository import Gst  # noqa: F401

    fake = fake or make_fake_gst()
    sink = FakeElement("shared_videosink")
    if monkeypatch is not None:
        monkeypatch.setattr(
            avf_module,
            "_resolve_device_index",
            lambda uid, fb: fb if fb >= 0 else 0,
        )
    with patch("gi.repository.Gst", fake):
        return AvfInput().create_pipeline(
            config=config or {},
            sink=sink,
            build_overlay_tail=lambda *a: None,
            prepare_sink=lambda: sink,
        )


class TestAvfPipeline:
    def test_builds_avf_chain_with_caps_and_index(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        pipeline = _build_avf(
            {
                "avf_unique_id": "",
                "avf_device_index": 2,
                "avf_width": 1280,
                "avf_height": 720,
                "avf_framerate": 30,
            },
            monkeypatch=monkeypatch,
        )
        src = pipeline.get_by_name("avfvideosrc")
        assert src is not None
        assert src.properties["device-index"] == 2

        caps = pipeline.get_by_name("capsfilter").properties["caps"].to_string()
        assert "width=(int)1280" in caps
        assert "height=(int)720" in caps
        assert "framerate=(fraction)30/1" in caps

        assert pipeline.get_by_name("post_queue") is not None
        assert pipeline.get_by_name("convert") is not None

    def test_missing_avfvideosrc_raises(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake = make_fake_gst(missing_elements={"avfvideosrc"})
        with pytest.raises(RuntimeError, match="avfvideosrc"):
            _build_avf(fake=fake, monkeypatch=monkeypatch)

    @pytest.mark.parametrize("missing", ["capsfilter", "queue", "videoconvert"])
    def test_missing_chain_element_raises_descriptive_error(
        self,
        missing: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake = make_fake_gst(missing_elements={missing})
        with pytest.raises(RuntimeError, match=f"{missing} GStreamer element not found"):
            _build_avf(fake=fake, monkeypatch=monkeypatch)

    def test_degenerate_dimensions_fall_back_to_defaults(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        pipeline = _build_avf(
            {"avf_width": 0, "avf_height": -5, "avf_framerate": "x"},
            monkeypatch=monkeypatch,
        )
        caps = pipeline.get_by_name("capsfilter").properties["caps"].to_string()
        assert "width=(int)1920" in caps
        assert "height=(int)1080" in caps
        assert "framerate=(fraction)30/1" in caps

    def test_malformed_device_index_falls_back_to_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A hand-edited non-int ``avf_device_index`` must not raise during
        pipeline build – it coerces to the -1 default, which resolves to
        device-index 0 rather than dropping the camera to the placeholder."""
        pipeline = _build_avf(
            {
                "avf_unique_id": "",
                "avf_device_index": "abc",
                "avf_width": 1280,
                "avf_height": 720,
                "avf_framerate": 30,
            },
            monkeypatch=monkeypatch,
        )
        assert pipeline.get_by_name("avfvideosrc").properties["device-index"] == 0

    def test_prepare_sink_none_raises(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from gi.repository import Gst  # noqa: F401

        monkeypatch.setattr(
            avf_module,
            "_resolve_device_index",
            lambda uid, fb: 0,
        )
        with patch("gi.repository.Gst", make_fake_gst()):
            with pytest.raises(RuntimeError, match="No video sink"):
                AvfInput().create_pipeline(
                    config={},
                    sink=FakeElement("shared_videosink"),
                    build_overlay_tail=lambda *a: None,
                    prepare_sink=lambda: None,
                )

    def test_on_bus_async_done_forces_zero_latency(self) -> None:
        pipeline = FakePipeline("avf")
        AvfInput().on_bus_async_done(pipeline)
        assert pipeline.latency_values == [0]


class TestAvfLinkFailures:
    """Each link in the avfvideosrc → capsfilter → queue → convert chain
    raises a precise RuntimeError when GStreamer refuses the link."""

    def _build_with(
        self,
        link_fail_kinds: set[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from gi.repository import Gst  # noqa: F401

        monkeypatch.setattr(
            avf_module,
            "_resolve_device_index",
            lambda uid, fb: 0,
        )
        fake = make_fake_gst(link_fail_kinds=link_fail_kinds)
        sink = FakeElement("shared_videosink")
        with patch("gi.repository.Gst", fake):
            AvfInput().create_pipeline(
                config={},
                sink=sink,
                build_overlay_tail=lambda *a: None,
                prepare_sink=lambda: sink,
            )

    def test_avfvideosrc_to_capsfilter_link_failure_raises(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        with pytest.raises(RuntimeError, match="avfvideosrc"):
            self._build_with({"avfvideosrc"}, monkeypatch)

    def test_capsfilter_to_queue_link_failure_raises(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        with pytest.raises(RuntimeError, match="capsfilter"):
            self._build_with({"capsfilter"}, monkeypatch)

    def test_queue_to_convert_link_failure_raises(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        with pytest.raises(RuntimeError, match="queue"):
            self._build_with({"queue"}, monkeypatch)


# --------------------------------------------------------------------------- #
# handle_list_devices
# --------------------------------------------------------------------------- #


class TestHandleListDevices:
    def test_no_devices_returns_fallback_option(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            avf_module,
            "_discover_avf_devices",
            lambda: [],
        )
        html = AvfInput().handle_list_devices({"avf_unique_id": ""})
        assert "-- No devices found --" in html

    def test_current_device_is_selected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            avf_module,
            "_discover_avf_devices",
            lambda: [
                {"unique_id": "FX30", "name": "Sony", "index": "0"},
                {"unique_id": "FT", "name": "FaceTime", "index": "1"},
            ],
        )
        html = AvfInput().handle_list_devices({"avf_unique_id": "FT"})
        assert 'value="FT" selected' in html
        assert 'value="FX30">' in html

    def test_device_names_are_escaped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            avf_module,
            "_discover_avf_devices",
            lambda: [{"unique_id": "x", "name": "<evil>", "index": "0"}],
        )
        html = AvfInput().handle_list_devices({})
        assert "<evil>" not in html
        assert "&lt;evil&gt;" in html


# --------------------------------------------------------------------------- #
# discover_sources
# --------------------------------------------------------------------------- #


class TestDiscoverSources:
    def test_returns_unique_ids(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            avf_module,
            "_discover_avf_devices",
            lambda: [
                {"unique_id": "FX30", "name": "Sony", "index": "0"},
                {"unique_id": "FT", "name": "FaceTime", "index": "1"},
            ],
        )
        assert AvfInput.discover_sources() == ["FX30", "FT"]
