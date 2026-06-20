# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for the static test-pattern input plugin.

Covers ``_resolve_stage_asset`` selection (JPG → SVG → error),
``create_pipeline`` for the ``grey`` and ``stage`` patterns, missing
GStreamer element handling, and HTML rendering.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from openfollow.video.inputs import testpattern as tp_module
from openfollow.video.inputs.testpattern import (
    TestPatternInput,
    _resolve_stage_asset,
)
from tests._fake_gst import FakeElement, FakePipeline, make_fake_gst

pytestmark = pytest.mark.unit

# --------------------------------------------------------------------------- #
# _resolve_stage_asset
# --------------------------------------------------------------------------- #


class TestResolveStageAsset:
    def test_prefers_jpg_when_available(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        jpg = tmp_path / "stage_default.jpg"
        svg = tmp_path / "stage_default.svg"
        jpg.write_bytes(b"jpeg")
        svg.write_text("<svg/>")
        monkeypatch.setattr(tp_module, "_STAGE_JPG", jpg)
        monkeypatch.setattr(tp_module, "_STAGE_SVG", svg)
        path, decoder = _resolve_stage_asset()
        assert path == jpg
        assert decoder == "jpegdec"

    def test_falls_back_to_svg_when_jpg_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        jpg = tmp_path / "stage_default.jpg"
        svg = tmp_path / "stage_default.svg"
        svg.write_text("<svg/>")
        monkeypatch.setattr(tp_module, "_STAGE_JPG", jpg)
        monkeypatch.setattr(tp_module, "_STAGE_SVG", svg)
        path, decoder = _resolve_stage_asset()
        assert path == svg
        assert decoder == "rsvgdec"

    def test_neither_present_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(tp_module, "_STAGE_JPG", tmp_path / "missing.jpg")
        monkeypatch.setattr(tp_module, "_STAGE_SVG", tmp_path / "missing.svg")
        with pytest.raises(RuntimeError, match="Stage test pattern asset not found"):
            _resolve_stage_asset()


# --------------------------------------------------------------------------- #
# create_pipeline – grey chain
# --------------------------------------------------------------------------- #


def _build(config=None, *, fake=None) -> FakePipeline:
    from gi.repository import Gst  # noqa: F401

    fake = fake or make_fake_gst()
    sink = FakeElement("shared_videosink")
    with patch("gi.repository.Gst", fake):
        return TestPatternInput().create_pipeline(
            config=config or {},
            sink=sink,
            build_overlay_tail=lambda *a: None,
            prepare_sink=lambda: sink,
        )


class TestCreatePipelineGrey:
    def test_builds_grey_chain(self) -> None:
        pipeline = _build({"testpattern_pattern": "grey", "testpattern_resolution": "720p"})
        src = pipeline.get_by_name("videotestsrc")
        assert src is not None
        assert src.properties["pattern"] == "solid-color"
        assert src.properties["foreground-color"] == 0xFF808080
        assert src.properties["is-live"] is True

        caps = pipeline.get_by_name("capsfilter").properties["caps"].to_string()
        assert "width=1280" in caps
        assert "height=720" in caps

        assert pipeline.get_by_name("convert") is not None

    def test_unknown_pattern_falls_back_to_grey(self) -> None:
        pipeline = _build({"testpattern_pattern": "purple-unicorn"})
        assert pipeline.get_by_name("videotestsrc") is not None

    def test_unknown_resolution_falls_back_to_1080p(self) -> None:
        pipeline = _build({"testpattern_resolution": "9001p"})
        caps = pipeline.get_by_name("capsfilter").properties["caps"].to_string()
        assert "width=1920" in caps
        assert "height=1080" in caps

    def test_missing_videotestsrc_raises(self) -> None:
        fake = make_fake_gst(missing_elements={"videotestsrc"})
        with pytest.raises(RuntimeError, match="videotestsrc"):
            _build(fake=fake)

    def test_videotestsrc_to_capsfilter_link_failure_raises(self) -> None:
        fake = make_fake_gst(link_fail_kinds={"videotestsrc"})
        with pytest.raises(RuntimeError, match="videotestsrc"):
            _build(fake=fake)

    def test_capsfilter_to_videoconvert_link_failure_raises(self) -> None:
        fake = make_fake_gst(link_fail_kinds={"capsfilter"})
        with pytest.raises(RuntimeError, match="capsfilter"):
            _build(fake=fake)


# --------------------------------------------------------------------------- #
# create_pipeline – stage chain
# --------------------------------------------------------------------------- #


class TestCreatePipelineStage:
    def test_builds_stage_chain_with_filesrc(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        jpg = tmp_path / "stage_default.jpg"
        jpg.write_bytes(b"fake")
        monkeypatch.setattr(tp_module, "_STAGE_JPG", jpg)

        pipeline = _build({"testpattern_pattern": "stage", "testpattern_resolution": "1080p"})
        filesrc = pipeline.get_by_name("stagefilesrc")
        assert filesrc is not None
        assert filesrc.properties["location"] == str(jpg)
        assert pipeline.get_by_name("stagedecode") is not None
        assert pipeline.get_by_name("stage_imagefreeze") is not None
        assert pipeline.get_by_name("stage_scale_caps") is not None
        assert pipeline.get_by_name("convert") is not None

    def test_missing_decoder_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        jpg = tmp_path / "stage_default.jpg"
        jpg.write_bytes(b"fake")
        monkeypatch.setattr(tp_module, "_STAGE_JPG", jpg)
        fake = make_fake_gst(missing_elements={"jpegdec"})
        with pytest.raises(RuntimeError, match="jpegdec"):
            _build(
                {"testpattern_pattern": "stage", "testpattern_resolution": "1080p"},
                fake=fake,
            )

    def test_missing_imagefreeze_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        jpg = tmp_path / "stage_default.jpg"
        jpg.write_bytes(b"fake")
        monkeypatch.setattr(tp_module, "_STAGE_JPG", jpg)
        fake = make_fake_gst(missing_elements={"imagefreeze"})
        with pytest.raises(RuntimeError, match="imagefreeze"):
            _build(
                {"testpattern_pattern": "stage", "testpattern_resolution": "1080p"},
                fake=fake,
            )

    def test_missing_filesrc_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """If GStreamer has no ``filesrc`` (an unusual but possible
        broken install), the stage chain raises a precise error."""
        jpg = tmp_path / "stage_default.jpg"
        jpg.write_bytes(b"fake")
        monkeypatch.setattr(tp_module, "_STAGE_JPG", jpg)
        fake = make_fake_gst(missing_elements={"filesrc"})
        with pytest.raises(RuntimeError, match="filesrc"):
            _build(
                {"testpattern_pattern": "stage", "testpattern_resolution": "1080p"},
                fake=fake,
            )

    def test_stage_chain_link_failure_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Any link failure in the long stage element chain surfaces a
        precise ``Failed to link X → Y`` error with the offending pair
        – covers the for-loop link-failure raise."""
        jpg = tmp_path / "stage_default.jpg"
        jpg.write_bytes(b"fake")
        monkeypatch.setattr(tp_module, "_STAGE_JPG", jpg)
        # Failing the filesrc element makes the very first link
        # (filesrc → stagedecode) return False, hitting the raise.
        fake = make_fake_gst(link_fail_kinds={"filesrc"})
        with pytest.raises(RuntimeError, match="Failed to link"):
            _build(
                {"testpattern_pattern": "stage", "testpattern_resolution": "1080p"},
                fake=fake,
            )


class TestCreatePipelineShared:
    def test_prepare_sink_none_raises(self) -> None:
        from gi.repository import Gst  # noqa: F401

        fake = make_fake_gst()
        with patch("gi.repository.Gst", fake):
            with pytest.raises(RuntimeError, match="No video sink"):
                TestPatternInput().create_pipeline(
                    config={},
                    sink=FakeElement("shared_videosink"),
                    build_overlay_tail=lambda *a: None,
                    prepare_sink=lambda: None,
                )


# --------------------------------------------------------------------------- #
# Web UI
# --------------------------------------------------------------------------- #


class TestConfigFieldChoices:
    """The pattern field exposes its valid values via ``ConfigField.choices``
    so the on-device UI can render a list picker instead of a free-text
    URL editor for what's really a 2-option enum."""

    def test_pattern_field_declares_choices(self) -> None:
        fields = {f.name: f for f in TestPatternInput.config_fields()}
        assert "testpattern_pattern" in fields
        choices = dict(fields["testpattern_pattern"].choices)
        assert choices == {"grey": "50% Grey", "stage": "Stage Scene"}

    def test_resolution_field_has_no_choices_yet(self) -> None:
        """Resolution stays web-only; the on-device picker is pattern-only.
        Locking this in prevents accidentally widening the on-device picker
        scope without an explicit decision."""
        fields = {f.name: f for f in TestPatternInput.config_fields()}
        assert fields["testpattern_resolution"].choices == ()


class TestWebUI:
    def test_html_includes_pattern_select(self) -> None:
        html = TestPatternInput.web_ui_html({"testpattern_pattern": "stage", "testpattern_resolution": "720p"})
        assert 'name="testpattern_pattern"' in html
        assert 'name="testpattern_resolution"' in html
        assert 'value="stage" selected' in html
        assert 'value="720p" selected' in html

    def test_html_sanitises_unknown_pattern(self) -> None:
        html = TestPatternInput.web_ui_html({"testpattern_pattern": "not-a-pattern", "testpattern_resolution": "bogus"})
        # Falls back to grey/1080p for the ``selected`` attribute
        assert 'value="grey" selected' in html
        assert 'value="1080p" selected' in html


# --------------------------------------------------------------------------- #
# Labels + hooks
# --------------------------------------------------------------------------- #


class TestGetSourceLabel:
    def test_grey_label(self) -> None:
        label = TestPatternInput.get_source_label({"testpattern_pattern": "grey", "testpattern_resolution": "720p"})
        assert label == "Test Pattern 50% Grey (720p)"

    def test_stage_label(self) -> None:
        label = TestPatternInput.get_source_label({"testpattern_pattern": "stage", "testpattern_resolution": "1080p"})
        assert label == "Test Pattern Stage Scene (1080p)"

    def test_unknown_pattern_falls_back(self) -> None:
        label = TestPatternInput.get_source_label(
            {"testpattern_pattern": "not-real", "testpattern_resolution": "1080p"}
        )
        assert "50% Grey" in label


class TestOnBusAsyncDone:
    def test_forces_zero_latency(self) -> None:
        pipeline = FakePipeline("testpattern")
        TestPatternInput().on_bus_async_done(pipeline)
        assert pipeline.latency_values == [0]
