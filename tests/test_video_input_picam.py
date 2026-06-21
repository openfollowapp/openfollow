# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for the Raspberry Pi camera input plugin."""

from __future__ import annotations

import subprocess
import sys
from unittest.mock import patch

import pytest

from openfollow.video.inputs import picam as picam_module
from openfollow.video.inputs.picam import PiCamInput, _discover_cameras
from tests._fake_gst import FakeElement, FakePipeline, make_fake_gst

pytestmark = pytest.mark.unit

# --------------------------------------------------------------------------- #
# _discover_cameras
# --------------------------------------------------------------------------- #


class _FakeCompleted:
    def __init__(self, stdout: str = "", stderr: str = "") -> None:
        self.stdout = stdout
        self.stderr = stderr


class TestDiscoverCameras:
    def test_parses_rpicam_output(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sample = (
            "Available cameras\n"
            "-----------------\n"
            "0 : imx219 [3280x2464 10-bit RGGB] (/base/axi/pcie@120000/rp1/i2c@88000/imx219@10)\n"
            "1 : imx477 [4056x3040 12-bit RGGB] (/base/axi/pcie@120000/rp1/i2c@80000/imx477@1a)\n"
        )
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: _FakeCompleted(stdout=sample),
        )
        cams = _discover_cameras()
        assert cams == [
            {
                "index": "0",
                "model": "imx219",
                "path": "/base/axi/pcie@120000/rp1/i2c@88000/imx219@10",
            },
            {
                "index": "1",
                "model": "imx477",
                "path": "/base/axi/pcie@120000/rp1/i2c@80000/imx477@1a",
            },
        ]

    def test_reads_stderr_output(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Some rpicam-hello builds print to stderr – the parser merges both."""
        sample = "0 : imx708 [4608x2592 10-bit RGGB] (/base/axi/camera)"
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: _FakeCompleted(stderr=sample),
        )
        assert _discover_cameras()[0]["model"] == "imx708"

    def test_file_not_found_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _raise(*a, **k):
            raise FileNotFoundError("rpicam-hello")

        monkeypatch.setattr(subprocess, "run", _raise)
        assert _discover_cameras() == []

    def test_timeout_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _raise(*a, **k):
            raise subprocess.TimeoutExpired(cmd="rpicam-hello", timeout=5)

        monkeypatch.setattr(subprocess, "run", _raise)
        assert _discover_cameras() == []

    @pytest.mark.parametrize(
        "exc",
        [
            PermissionError("rpicam-hello not executable"),
            OSError(8, "Exec format error"),
        ],
    )
    def test_os_error_returns_empty(self, monkeypatch: pytest.MonkeyPatch, exc: OSError) -> None:
        # A non-executable / wrong-arch binary raises an OSError variable other
        # than FileNotFoundError; discovery must degrade to an empty list so the
        # web camera dropdown doesn't surface a 500 instead of "no cameras".
        def _raise(*a, **k):
            raise exc

        monkeypatch.setattr(subprocess, "run", _raise)
        assert _discover_cameras() == []


class TestDiscoverSources:
    def test_returns_paths_from_discover_cameras(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            picam_module,
            "_discover_cameras",
            lambda: [{"path": "/base/a", "model": "x"}, {"path": "/base/b", "model": "y"}],
        )
        assert PiCamInput.discover_sources() == ["/base/a", "/base/b"]


# --------------------------------------------------------------------------- #
# create_pipeline
# --------------------------------------------------------------------------- #


def _build(config=None, *, fake=None) -> FakePipeline:
    from gi.repository import Gst  # noqa: F401

    fake = fake or make_fake_gst()
    sink = FakeElement("shared_videosink")
    with patch("gi.repository.Gst", fake):
        return PiCamInput().create_pipeline(
            config=config or {},
            sink=sink,
            build_overlay_tail=lambda *a: None,
            prepare_sink=lambda: sink,
        )


class TestCreatePipeline:
    def test_builds_libcamera_chain_with_caps(self) -> None:
        pipeline = _build(
            {
                "picam_camera_name": "/base/imx219",
                "picam_width": 1920,
                "picam_height": 1080,
                "picam_framerate": 30,
            }
        )
        src = pipeline.get_by_name("libcamerasrc")
        assert src is not None
        assert src.properties["camera-name"] == "/base/imx219"

        capsfilter = pipeline.get_by_name("capsfilter")
        caps = capsfilter.properties["caps"].to_string()
        assert "width=(int)1920" in caps
        assert "height=(int)1080" in caps
        assert "framerate=(fraction)30/1" in caps

        assert pipeline.get_by_name("post_queue") is not None
        assert pipeline.get_by_name("convert") is not None

    def test_empty_camera_name_does_not_set_property(self) -> None:
        pipeline = _build({"picam_camera_name": ""})
        src = pipeline.get_by_name("libcamerasrc")
        assert "camera-name" not in src.properties

    def test_missing_libcamera_raises(self) -> None:
        fake = make_fake_gst(missing_elements={"libcamerasrc"})
        with pytest.raises(RuntimeError, match="libcamerasrc"):
            _build(fake=fake)

    @pytest.mark.parametrize("missing", ["capsfilter", "queue", "videoconvert"])
    def test_missing_chain_element_raises_descriptive_error(self, missing: str) -> None:
        # The make() helper raises a clear "not found" instead of a later
        # opaque AttributeError on the None element.
        fake = make_fake_gst(missing_elements={missing})
        with pytest.raises(RuntimeError, match=f"{missing} GStreamer element not found"):
            _build(fake=fake)

    def test_degenerate_dimensions_fall_back_to_defaults(self) -> None:
        # A 0 / negative / non-int width must not reach the caps string.
        pipeline = _build({"picam_width": 0, "picam_height": -5, "picam_framerate": "x"})
        caps = pipeline.get_by_name("capsfilter").properties["caps"].to_string()
        assert "width=(int)1920" in caps
        assert "height=(int)1080" in caps
        assert "framerate=(fraction)30/1" in caps

    def test_prepare_sink_none_raises(self) -> None:
        from gi.repository import Gst  # noqa: F401

        with patch("gi.repository.Gst", make_fake_gst()):
            with pytest.raises(RuntimeError, match="No video sink"):
                PiCamInput().create_pipeline(
                    config={},
                    sink=FakeElement("shared_videosink"),
                    build_overlay_tail=lambda *a: None,
                    prepare_sink=lambda: None,
                )


class TestPicamLinkFailures:
    """Each link in the libcamerasrc → capsfilter → queue → convert
    chain raises a precise RuntimeError when GStreamer refuses the
    link."""

    def _build_with(self, link_fail_kinds: set[str]) -> None:
        from gi.repository import Gst  # noqa: F401

        fake = make_fake_gst(link_fail_kinds=link_fail_kinds)
        sink = FakeElement("shared_videosink")
        with patch("gi.repository.Gst", fake):
            PiCamInput().create_pipeline(
                config={},
                sink=sink,
                build_overlay_tail=lambda *a: None,
                prepare_sink=lambda: sink,
            )

    def test_libcamerasrc_to_capsfilter_link_failure_raises(self) -> None:
        with pytest.raises(RuntimeError, match="libcamerasrc"):
            self._build_with({"libcamerasrc"})

    def test_capsfilter_to_queue_link_failure_raises(self) -> None:
        with pytest.raises(RuntimeError, match="capsfilter"):
            self._build_with({"capsfilter"})

    def test_queue_to_convert_link_failure_raises(self) -> None:
        with pytest.raises(RuntimeError, match="queue"):
            self._build_with({"queue"})


# --------------------------------------------------------------------------- #
# Web UI / handlers
# --------------------------------------------------------------------------- #


class TestHandleListCameras:
    def test_auto_detect_entry_always_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(picam_module, "_discover_cameras", lambda: [])
        html = PiCamInput().handle_list_cameras({"picam_camera_name": ""})
        assert "Auto-detect" in html
        assert "-- No cameras found --" in html

    def test_selected_matches_current_camera(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            picam_module,
            "_discover_cameras",
            lambda: [
                {"path": "/base/a", "model": "imx219"},
                {"path": "/base/b", "model": "imx477"},
            ],
        )
        html = PiCamInput().handle_list_cameras({"picam_camera_name": "/base/b"})
        assert '<option value="/base/b" selected>imx477 (/base/b)</option>' in html
        assert '<option value="/base/a">imx219 (/base/a)</option>' in html

    def test_values_escaped_in_options(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            picam_module,
            "_discover_cameras",
            lambda: [{"path": "</p>hack", "model": "<>"}],
        )
        html = PiCamInput().handle_list_cameras({})
        assert "</p>hack" not in html
        assert "&lt;&gt;" in html


# --------------------------------------------------------------------------- #
# Lifecycle + labels
# --------------------------------------------------------------------------- #


class TestOnBusAsyncDone:
    def test_forces_zero_latency(self) -> None:
        pipeline = FakePipeline("picam")
        PiCamInput().on_bus_async_done(pipeline)
        assert pipeline.latency_values == [0]


class TestGetSourceLabel:
    def test_auto_detect_label(self) -> None:
        assert (
            PiCamInput.get_source_label(
                {
                    "picam_camera_name": "",
                    "picam_width": 1920,
                    "picam_height": 1080,
                    "picam_framerate": 30,
                }
            )
            == "Pi Camera (1920x1080@30)"
        )

    def test_label_from_path_basename_without_scanning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Side-effect free: get_source_label runs on the GTK main thread on hot
        # paths and must NOT spawn the blocking rpicam-hello scan.
        def _boom() -> list:
            raise AssertionError("_discover_cameras must not be called from get_source_label")

        monkeypatch.setattr(picam_module, "_discover_cameras", _boom)
        label = PiCamInput.get_source_label(
            {
                "picam_camera_name": "/base/imx219",
                "picam_width": 1280,
                "picam_height": 720,
                "picam_framerate": 60,
            }
        )
        assert label == "imx219 (1280x720@60)"

    def test_label_clamps_invalid_dimensions_to_defaults(self) -> None:
        # An invalid 0/negative/non-int config drives the caps to defaults in
        # create_pipeline; the label must show the same clamped values, not the
        # misleading raw ones.
        label = PiCamInput.get_source_label(
            {
                "picam_camera_name": "",
                "picam_width": 0,
                "picam_height": -1,
                "picam_framerate": "bad",
            }
        )
        assert label == "Pi Camera (1920x1080@30)"


class TestPiCamIsAvailable:
    def test_unavailable_off_linux(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        ok, reason = PiCamInput.is_available()
        assert ok is False
        assert "Linux" in reason

    def test_unavailable_when_factory_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        fake = make_fake_gst()
        with patch("gi.repository.Gst", fake):
            ok, reason = PiCamInput.is_available()
        assert ok is False
        assert "libcamerasrc" in reason

    def test_available_on_linux_with_factory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        fake = make_fake_gst(known_factories={"libcamerasrc": object()})
        with patch("gi.repository.Gst", fake):
            ok, reason = PiCamInput.is_available()
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
            ok, reason = PiCamInput.is_available()
        assert ok is False
        assert "GStreamer not available" in reason
