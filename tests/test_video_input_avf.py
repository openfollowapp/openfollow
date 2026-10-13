# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for the AVFoundation (macOS USB camera) video input plugin."""

from __future__ import annotations

import sys
from typing import Any
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


@pytest.fixture(autouse=True)
def _no_cameras_listed_yet(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Each test starts as a fresh run that has listed no camera."""
    names: dict[str, str] = {}
    monkeypatch.setattr(avf_module, "_camera_names", names)
    monkeypatch.setattr(avf_module, "_checked_index", {})
    # A listing nothing stubs never reaches this machine's cameras; a test of the
    # real listing sets darwin itself.
    monkeypatch.setattr(sys, "platform", "linux")
    return names


def _cameras(monkeypatch: pytest.MonkeyPatch, *cameras: tuple[str, str]) -> list[int]:
    """Have the device monitor list *cameras* (unique ID, name) in order; one entry per listing."""
    listings: list[int] = []

    def _list() -> list[dict[str, str]]:
        listings.append(1)
        avf_module._camera_names.update(dict(cameras))
        return [{"unique_id": uid, "name": name, "index": str(i)} for i, (uid, name) in enumerate(cameras)]

    monkeypatch.setattr(avf_module, "_list_avf_devices", _list)
    return listings


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
            "avf_device_name",
            "avf_device_index",
            "avf_width",
            "avf_height",
            "avf_framerate",
        }

    def test_the_unique_id_stays_the_stored_key(self) -> None:
        """The first field is what the picker selects and the receiver clears."""
        assert AvfInput.config_fields()[0].name == "avf_unique_id"

    def test_the_name_is_never_edited_on_screen(self) -> None:
        """It is written with the camera, not typed; the on-screen editor skips it."""
        field = next(f for f in AvfInput.config_fields() if f.name == "avf_device_name")
        assert field.device_editable is False

    def test_config_defaults(self) -> None:
        fields = {f.name: f.default for f in AvfInput.config_fields()}
        assert fields["avf_unique_id"] == ""
        assert fields["avf_device_name"] == ""
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

    def test_a_listing_remembers_each_cameras_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Labels name a camera without listing again, which stalls and can ask for camera access."""
        monkeypatch.setattr(sys, "platform", "darwin")
        fake = _make_fake_gst_with_devices(
            [
                _FakeDevice(
                    props={"device.api": "avf", "avf.unique_id": "47B4B64B"}, display_name="FaceTime HD Camera"
                ),
                _FakeDevice(props={"device.api": "avf", "avf.unique_id": ""}, display_name="No ID"),
            ]
        )
        with patch("gi.repository.Gst", fake):
            _discover_avf_devices()

        assert avf_module._camera_names == {"47B4B64B": "FaceTime HD Camera"}

    def test_a_listed_name_is_cleaned_as_a_stored_one_is(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Drawn on the HUD, and compared with the stored (cleaned) name to tell two cameras apart."""
        from openfollow.configuration import AVF_DEVICE_NAME_MAX_LEN

        monkeypatch.setattr(sys, "platform", "darwin")
        fake = _make_fake_gst_with_devices(
            [
                _FakeDevice(props={"device.api": "avf", "avf.unique_id": "A"}, display_name="Capture\u202e Card\n"),
                _FakeDevice(props={"device.api": "avf", "avf.unique_id": "B"}, display_name="C" * 1000),
                _FakeDevice(props={"device.api": "avf", "avf.unique_id": "C"}, display_name="\u200e"),
            ]
        )
        with patch("gi.repository.Gst", fake):
            names = [dev["name"] for dev in avf_module._list_avf_devices()]

        assert names == ["Capture Card", "C" * AVF_DEVICE_NAME_MAX_LEN, "C"]
        assert avf_module._camera_names == dict(zip("ABC", names, strict=True))

    def test_a_listing_that_cannot_be_read_says_so(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The build must not read a broken listing as "no camera"; the lists an
        operator reads still show none."""
        monkeypatch.setattr(sys, "platform", "darwin")
        with patch("gi.repository.Gst", _make_fake_gst_with_devices([], start_ok=False)):
            with pytest.raises(RuntimeError, match="device monitor did not start"):
                avf_module._list_avf_devices()
            assert _discover_avf_devices() == []

    def test_off_darwin_the_listing_is_empty_not_failed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        assert avf_module._list_avf_devices() == []


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

    def test_a_name_alone_reconnects_nothing(self) -> None:
        """The name never affects the build; a swap would black the video out for it."""
        from openfollow.configuration import AppConfig

        old = AppConfig(video_source_type="avf", avf_unique_id="0x14100000")
        new = AppConfig(video_source_type="avf", avf_unique_id="0x14100000", avf_device_name="Capture Card")
        assert AvfInput.config_changed(old, new) is False

    def test_another_camera_with_its_name_still_reconnects(self) -> None:
        from openfollow.configuration import AppConfig

        old = AppConfig(video_source_type="avf", avf_unique_id="0x14100000", avf_device_name="Capture Card")
        new = AppConfig(video_source_type="avf", avf_unique_id="47B4B64B", avf_device_name="FaceTime HD Camera")
        assert AvfInput.config_changed(old, new) is True

    def test_the_first_unchanged_save_of_an_older_config_reconnects_nothing(self, _no_cameras_listed_yet) -> None:
        """An older config has the camera but no name; that save records it."""
        import copy

        from openfollow.configuration import AppConfig
        from openfollow.web.routes import apply_section_data

        _no_cameras_listed_yet["0x14100000"] = "Capture Card"
        old = AppConfig(video_source_type="avf", avf_unique_id="0x14100000")
        new = copy.deepcopy(old)
        form = {
            "video_source_type": "avf",
            "avf_unique_id": "0x14100000",
            "avf_device_index": "-1",
            "avf_width": "1920",
        }

        apply_section_data(new, "video_source", form)

        assert new.avf_device_name == "Capture Card"
        assert AvfInput.config_changed(old, new) is False


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

    def test_the_stored_camera_shows_by_name_until_the_list_loads(self, _no_cameras_listed_yet) -> None:
        _no_cameras_listed_yet["47B4B64B"] = "FaceTime HD Camera"

        html = AvfInput.web_ui_html({"avf_unique_id": "47B4B64B"})

        assert '<option value="47B4B64B">' in html
        assert "FaceTime HD Camera" in html

    def test_no_stored_camera_shows_loading(self) -> None:
        assert "-- Loading... --" in AvfInput.web_ui_html({"avf_unique_id": ""})

    def test_a_camera_missing_since_startup_shows_its_stored_name(self) -> None:
        html = AvfInput.web_ui_html({"avf_unique_id": "0x14100000", "avf_device_name": "Capture <Card>"})

        assert '<option value="0x14100000">' in html
        assert "Capture &lt;Card&gt;" in html


class TestAvfSourceLabel:
    def test_a_camera_not_listed_this_run_reads_as_its_unique_id(self) -> None:
        label = AvfInput.get_source_label(
            {
                "avf_unique_id": "ABCD-1234",
                "avf_width": 1280,
                "avf_height": 720,
                "avf_framerate": 60,
            }
        )
        assert label == "ABCD-1234 (1280x720@60)"

    def test_a_listed_camera_reads_as_its_name(self, _no_cameras_listed_yet) -> None:
        _no_cameras_listed_yet["47B4B64B"] = "FaceTime HD Camera"

        label = AvfInput.get_source_label(
            {"avf_unique_id": "47B4B64B", "avf_width": 1920, "avf_height": 1080, "avf_framerate": 30}
        )

        assert label == "FaceTime HD Camera (1920x1080@30)"

    def test_a_camera_unplugged_after_it_was_listed_keeps_its_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The failure sentence for a camera that dropped out names the camera."""
        monkeypatch.setattr(sys, "platform", "darwin")
        card = _FakeDevice(props={"device.api": "avf", "avf.unique_id": "0x1410"}, display_name="Capture Card")
        with patch("gi.repository.Gst", _make_fake_gst_with_devices([card])):
            _discover_avf_devices()
        with patch("gi.repository.Gst", _make_fake_gst_with_devices([])):
            assert AvfInput.preflight({"avf_unique_id": "0x1410"}) is not None

        label = AvfInput.get_source_label({"avf_unique_id": "0x1410"})

        assert label.startswith("Capture Card (")

    def test_a_camera_missing_since_startup_reads_as_its_stored_name(self) -> None:
        """Never listed this run, so only the name stored when it was chosen can name it."""
        label = AvfInput.get_source_label(
            {"avf_unique_id": "0x14100000", "avf_device_name": "Capture Card", "avf_width": 1920}
        )

        assert label.startswith("Capture Card (1920x")

    def test_a_listed_name_wins_over_the_stored_one(self, _no_cameras_listed_yet) -> None:
        _no_cameras_listed_yet["47B4B64B"] = "FaceTime HD Camera"

        label = AvfInput.get_source_label({"avf_unique_id": "47B4B64B", "avf_device_name": "Old Name"})

        assert label.startswith("FaceTime HD Camera (")

    @pytest.mark.parametrize("stored", [None, 5, ""])
    def test_a_stored_name_that_is_not_text_reads_as_the_unique_id(self, stored: object) -> None:
        label = AvfInput.get_source_label({"avf_unique_id": "0x14100000", "avf_device_name": stored})

        assert label.startswith("0x14100000 (")

    def test_two_listed_cameras_of_one_name_are_told_apart(self, _no_cameras_listed_yet) -> None:
        """The source line, the failure sentence and /api/stats read the label,
        so it numbers them the way the picker and the web list do."""
        _no_cameras_listed_yet.update({"A": "USB Capture", "B": "FaceTime HD Camera", "C": "USB Capture"})

        assert AvfInput.get_source_label({"avf_unique_id": "C"}).startswith("USB Capture (2) (")
        assert AvfInput.get_source_label({"avf_unique_id": "B"}).startswith("FaceTime HD Camera (")

    def test_a_stored_name_a_listed_camera_shares_carries_the_unique_id(self, _no_cameras_listed_yet) -> None:
        """The missing card must not read as the identical card that is plugged in."""
        _no_cameras_listed_yet["A"] = "USB Capture"

        label = AvfInput.get_source_label({"avf_unique_id": "0x14100000", "avf_device_name": "USB Capture"})

        assert label.startswith("USB Capture (0x14100000) (")

    def test_no_camera_chosen_reads_as_no_source(self) -> None:
        """An empty label makes the receiver ask for a camera; "default" opened
        whichever camera the system offered, the built-in one on a laptop."""
        label = AvfInput.get_source_label(
            {
                "avf_unique_id": "",
                "avf_width": 1920,
                "avf_height": 1080,
                "avf_framerate": 30,
            }
        )
        assert label == ""

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

    @pytest.mark.parametrize("fallback", [3, 0, -1])
    def test_a_remembered_camera_that_is_missing_resolves_to_no_device(
        self, monkeypatch: pytest.MonkeyPatch, fallback: int
    ) -> None:
        """Any index would open another camera and report it connected: on a
        laptop, a capture card that dropped out came back as the built-in one."""
        monkeypatch.setattr(
            avf_module,
            "_discover_avf_devices",
            lambda: [{"unique_id": "FX30", "name": "Sony", "index": "0"}],
        )
        assert _resolve_device_index("missing", fallback) is None


class TestAvfPreflight:
    """The receiver builds nothing for a remembered camera that is absent and
    retries, so the camera is reported not found rather than replaced."""

    def test_a_missing_camera_is_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from openfollow.video.failure import VideoFailure
        from openfollow.video.inputs._pin import PinRefusal

        _cameras(monkeypatch, ("47B4B64B", "FaceTime HD Camera"))

        assert AvfInput.preflight({"avf_unique_id": "0x14100000"}) == PinRefusal(
            VideoFailure.STREAM_NOT_FOUND, "no camera with unique ID 0x14100000 is connected"
        )

    def test_a_connected_camera_is_built(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _cameras(monkeypatch, ("47B4B64B", "FaceTime HD Camera"), ("0x14100000", "Capture Card"))

        assert AvfInput.preflight({"avf_unique_id": "0x14100000"}) is None

    @pytest.mark.parametrize("config", [{}, {"avf_unique_id": ""}, {"avf_unique_id": None}])
    def test_no_stored_camera_lists_nothing(self, monkeypatch: pytest.MonkeyPatch, config: dict[str, Any]) -> None:
        listings = _cameras(monkeypatch)

        assert AvfInput.preflight(config) is None
        assert listings == []

    @pytest.mark.parametrize("reason", ["the device monitor did not start", "device provider broke"])
    def test_a_list_that_cannot_be_read_is_not_a_missing_camera(
        self, monkeypatch: pytest.MonkeyPatch, reason: str
    ) -> None:
        """Read as "not found", a connected camera would be reported missing and,
        once the retries ran out, dropped for the picker."""
        from openfollow.video.failure import VideoFailure
        from openfollow.video.inputs._pin import PinRefusal

        def _broken() -> list[dict[str, str]]:
            raise RuntimeError(reason)

        monkeypatch.setattr(avf_module, "_list_avf_devices", _broken)

        assert AvfInput.preflight({"avf_unique_id": "47B4B64B"}) == PinRefusal(
            VideoFailure.UNKNOWN, f"the camera list could not be read: {reason}"
        )

    @pytest.mark.parametrize("start_ok", [False, None], ids=["monitor-not-started", "gst-init-raises"])
    def test_a_broken_device_monitor_is_not_a_missing_camera(
        self, monkeypatch: pytest.MonkeyPatch, start_ok: bool | None
    ) -> None:
        from openfollow.video.failure import VideoFailure

        monkeypatch.setattr(sys, "platform", "darwin")

        class _BoomGst:
            @staticmethod
            def init(_):
                raise RuntimeError("device provider broke")

        fake = _BoomGst if start_ok is None else _make_fake_gst_with_devices([], start_ok=start_ok)
        with patch("gi.repository.Gst", fake):
            refusal = AvfInput.preflight({"avf_unique_id": "47B4B64B"})

        assert refusal is not None
        assert refusal.failure is VideoFailure.UNKNOWN

    def test_the_build_uses_the_index_the_check_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """One listing per build: the camera cannot move between a check and a second listing."""
        listings = _cameras(monkeypatch, ("47B4B64B", "FaceTime HD Camera"), ("0x14100000", "Capture Card"))
        config = {"avf_unique_id": "0x14100000"}

        assert AvfInput.preflight(config) is None
        pipeline = _build_avf(config)

        assert pipeline.get_by_name("avfvideosrc").properties["device-index"] == 1
        assert listings == [1]

    def test_a_build_without_a_check_looks_the_camera_up_and_never_replaces_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _cameras(monkeypatch, ("47B4B64B", "FaceTime HD Camera"))

        with pytest.raises(RuntimeError, match="0x14100000"):
            _build_avf({"avf_unique_id": "0x14100000"})

    def test_a_check_for_another_camera_is_not_used(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _cameras(monkeypatch, ("47B4B64B", "FaceTime HD Camera"), ("0x14100000", "Capture Card"))
        AvfInput.preflight({"avf_unique_id": "0x14100000"})

        pipeline = _build_avf({"avf_unique_id": "47B4B64B"})

        assert pipeline.get_by_name("avfvideosrc").properties["device-index"] == 0


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
            lambda uid, fb: fb,
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
        """A hand-edited non-int ``avf_device_index`` must not raise during a
        build – it coerces to the -1 default. With no camera chosen such a
        build is never played: the receiver opens the picker instead."""
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
        assert pipeline.get_by_name("avfvideosrc").properties["device-index"] == -1

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
        _cameras(monkeypatch)
        html = AvfInput().handle_list_devices({"avf_unique_id": ""})
        assert "-- No devices found --" in html

    def test_current_device_is_selected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _cameras(monkeypatch, ("FX30", "Sony"), ("FT", "FaceTime"))
        html = AvfInput().handle_list_devices({"avf_unique_id": "FT"})
        assert '<option value="FT" selected>FaceTime</option>' in html
        assert '<option value="FX30">Sony</option>' in html

    def test_device_names_are_escaped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _cameras(monkeypatch, ("x", "<evil>"))
        html = AvfInput().handle_list_devices({})
        assert "<evil>" not in html
        assert "&lt;evil&gt;" in html

    def test_two_cameras_of_one_name_are_numbered_as_everywhere_else(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """From 1, as on the picker, the source line and the failure sentence."""
        _cameras(monkeypatch, ("A", "USB Capture"), ("B", "FaceTime HD Camera"), ("C", "USB Capture"))

        html = AvfInput().handle_list_devices({"avf_unique_id": "C"})

        assert html.split("\n") == [
            '<option value="A">USB Capture (1)</option>',
            '<option value="B">FaceTime HD Camera</option>',
            '<option value="C" selected>USB Capture (2)</option>',
        ]

    def test_a_camera_without_a_unique_id_shows_its_listed_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(avf_module, "_list_avf_devices", lambda: [{"unique_id": "", "name": "avf0", "index": "0"}])

        assert ">avf0</option>" in AvfInput().handle_list_devices({})

    def test_a_remembered_camera_that_is_missing_stays_selected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Otherwise the browser selects the first camera listed, and any save
        of the section replaces the remembered one for good."""
        _cameras(monkeypatch, ("47B4B64B", "FaceTime HD Camera"))

        html = AvfInput().handle_list_devices({"avf_unique_id": "0x14100000"})

        options = html.split("\n")
        assert options[0] == '<option value="0x14100000" selected>0x14100000 (unavailable)</option>'
        assert options[1] == '<option value="47B4B64B">FaceTime HD Camera</option>'

    def test_a_camera_missing_since_startup_is_named_as_stored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _cameras(monkeypatch, ("47B4B64B", "FaceTime HD Camera"))

        html = AvfInput().handle_list_devices({"avf_unique_id": "0x14100000", "avf_device_name": "Capture Card"})

        assert html.split("\n")[0] == '<option value="0x14100000" selected>Capture Card (unavailable)</option>'

    def test_a_missing_camera_listed_earlier_is_named(
        self, monkeypatch: pytest.MonkeyPatch, _no_cameras_listed_yet
    ) -> None:
        _no_cameras_listed_yet["0x14100000"] = "Capture <Card>"
        _cameras(monkeypatch)

        html = AvfInput().handle_list_devices({"avf_unique_id": "0x14100000"})

        assert html == '<option value="0x14100000" selected>Capture &lt;Card&gt; (unavailable)</option>'

    def test_a_remembered_camera_hides_the_empty_list_marker(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An empty value would clear the remembered camera on the next save."""
        _cameras(monkeypatch)

        html = AvfInput().handle_list_devices({"avf_unique_id": "0x14100000"})

        assert "No devices found" not in html
        assert 'value=""' not in html

    def test_a_remembered_value_is_escaped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _cameras(monkeypatch)

        html = AvfInput().handle_list_devices({"avf_unique_id": '"><script>'})

        assert "<script>" not in html

    def test_a_list_that_cannot_be_read_does_not_call_the_camera_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ "(unavailable)" means not connected, which a list that was never read
        cannot tell; the camera stays selected, as the build refuses as unknown."""

        def _broken() -> list[dict[str, str]]:
            raise RuntimeError("the device monitor did not start")

        monkeypatch.setattr(avf_module, "_list_avf_devices", _broken)

        html = AvfInput().handle_list_devices({"avf_unique_id": "0x14100000", "avf_device_name": "Capture Card"})

        assert html == '<option value="0x14100000" selected>Capture Card</option>'

    def test_a_list_that_cannot_be_read_with_no_camera_chosen_says_so(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Not "No devices found": that sends the operator to a cable, not to the listing."""

        def _broken() -> list[dict[str, str]]:
            raise RuntimeError("the device monitor did not start")

        monkeypatch.setattr(avf_module, "_list_avf_devices", _broken)

        assert AvfInput().handle_list_devices({"avf_unique_id": ""}) == (
            '<option value="">-- Camera list could not be read --</option>'
        )


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

    def test_the_picker_names_each_camera(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The unique ID stays the key; the operator reads the camera's name."""
        monkeypatch.setattr(sys, "platform", "darwin")
        cameras = [
            _FakeDevice(props={"device.api": "avf", "avf.unique_id": "47B4B64B"}, display_name="FaceTime HD Camera"),
            _FakeDevice(props={"device.api": "avf", "avf.unique_id": "7626645E"}, display_name="OBS Virtual Camera"),
        ]
        with patch("gi.repository.Gst", _make_fake_gst_with_devices(cameras)):
            sources = AvfInput.discover_sources()

        assert sources == ["47B4B64B", "7626645E"]
        assert AvfInput.source_labels(sources) == ["FaceTime HD Camera", "OBS Virtual Camera"]

    def test_two_cameras_of_one_name_are_told_apart(self, _no_cameras_listed_yet) -> None:
        _no_cameras_listed_yet.update({"A": "USB Capture", "B": "FaceTime HD Camera", "C": "USB Capture"})

        assert AvfInput.source_labels(["A", "B", "C"]) == ["USB Capture (1)", "FaceTime HD Camera", "USB Capture (2)"]

    def test_a_camera_keeps_its_number_while_its_twin_is_unplugged(self, _no_cameras_listed_yet) -> None:
        """Its number names it on every surface; it must not change under the operator."""
        _no_cameras_listed_yet.update({"A": "USB Capture", "C": "USB Capture"})

        assert AvfInput.source_labels(["C"]) == ["USB Capture (2)"]


class TestTheNameIsStoredWithTheCamera:
    """``avf_device_name`` names a remembered camera that has not been listed
    since startup. It is written by every path that chooses a camera: the web
    save and the on-screen picker both go through ``apply_config_fields``."""

    def _saved(self, data: dict[str, Any], **stored: Any) -> Any:
        from openfollow.configuration import AppConfig
        from openfollow.web.routes import apply_section_data

        cfg = AppConfig(video_source_type="avf", **stored)
        apply_section_data(cfg, "video_source", data)
        return cfg

    def test_choosing_a_listed_camera_stores_its_name(self, _no_cameras_listed_yet) -> None:
        _no_cameras_listed_yet["0x14100000"] = "Capture Card"

        cfg = self._saved({"avf_unique_id": "0x14100000"})

        assert (cfg.avf_unique_id, cfg.avf_device_name) == ("0x14100000", "Capture Card")

    def test_a_listed_name_wins_over_a_posted_one(self, _no_cameras_listed_yet) -> None:
        _no_cameras_listed_yet["0x14100000"] = "Capture Card"

        cfg = self._saved({"avf_unique_id": "0x14100000", "avf_device_name": "Something Else"})

        assert cfg.avf_device_name == "Capture Card"

    def test_a_name_sent_with_an_id_not_listed_here_is_kept(self) -> None:
        """An import or a peer names a camera this station has not listed."""
        cfg = self._saved({"avf_unique_id": "0x14100000", "avf_device_name": "Capture Card"})

        assert cfg.avf_device_name == "Capture Card"

    def test_saving_a_missing_camera_unchanged_keeps_its_name(self) -> None:
        cfg = self._saved(
            {"avf_unique_id": "0x14100000", "avf_width": "1280"},
            avf_unique_id="0x14100000",
            avf_device_name="Capture Card",
        )

        assert cfg.avf_device_name == "Capture Card"
        assert cfg.avf_width == 1280

    def test_a_save_that_names_no_camera_keeps_the_pair(self) -> None:
        cfg = self._saved({"avf_width": "1280"}, avf_unique_id="0x14100000", avf_device_name="Capture Card")

        assert (cfg.avf_unique_id, cfg.avf_device_name) == ("0x14100000", "Capture Card")

    def test_another_camera_never_inherits_the_old_name(self) -> None:
        cfg = self._saved({"avf_unique_id": "47B4B64B"}, avf_unique_id="0x14100000", avf_device_name="Capture Card")

        assert cfg.avf_device_name == ""

    def test_no_camera_chosen_stores_no_name(self) -> None:
        cfg = self._saved(
            {"avf_unique_id": "", "avf_device_name": "Capture Card"},
            avf_unique_id="0x14100000",
            avf_device_name="Capture Card",
        )

        assert (cfg.avf_unique_id, cfg.avf_device_name) == ("", "")

    @pytest.mark.parametrize(
        ("posted", "stored"),
        [
            ("Capture\x00 Card‮", "Capture Card"),
            ("  Capture Card \n", "Capture Card"),
            ("C" * 300, "C" * 128),
            (None, ""),
            (5, ""),
            (True, ""),
            (["Capture Card"], ""),
        ],
        ids=["control-and-bidi", "whitespace", "overlong", "none", "int", "bool", "list"],
    )
    def test_a_crafted_name_is_cleaned_as_a_load_would(self, posted: object, stored: str) -> None:
        cfg = self._saved({"avf_unique_id": "0x14100000", "avf_device_name": posted})

        assert cfg.avf_device_name == stored

    def test_a_camera_never_listed_reads_as_its_unique_id(self) -> None:
        assert AvfInput.source_labels(["47B4B64B"]) == ["47B4B64B"]

    def test_naming_never_lists_cameras(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _boom() -> list[dict[str, str]]:
            raise AssertionError("source_labels must not list cameras")

        monkeypatch.setattr(avf_module, "_discover_avf_devices", _boom)

        assert AvfInput.source_labels(["47B4B64B"]) == ["47B4B64B"]
