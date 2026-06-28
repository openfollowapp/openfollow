# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for the Media Gallery input plugin (``input_id = "testpattern"``).

Covers ``_resolve_stage_asset`` selection, the grey / stage / image / clip
``create_pipeline`` chains, the Stage fallback for an unresolvable selection,
the EOS loop hook, missing-element handling, and the source label.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from openfollow.video import media_store
from openfollow.video.inputs import testpattern as tp_module
from openfollow.video.inputs.testpattern import MediaGalleryInput, _resolve_stage_asset
from tests._fake_gst import FakeCaps, FakeElement, FakePad, FakePipeline, make_fake_gst

pytestmark = pytest.mark.unit


def _build(config=None, *, fake=None, plugin=None) -> FakePipeline:
    from gi.repository import Gst  # noqa: F401

    fake = fake or make_fake_gst()
    sink = FakeElement("shared_videosink")
    plugin = plugin or MediaGalleryInput()
    with patch("gi.repository.Gst", fake):
        return plugin.create_pipeline(
            config=config or {},
            sink=sink,
            build_overlay_tail=lambda *a: None,
            prepare_sink=lambda: sink,
        )


def _user_item(media_id: str, kind: str, path: Path) -> media_store.MediaItem:
    return media_store.MediaItem(media_id, kind, False, path, path.stat().st_size if path.exists() else 0, media_id)


# --------------------------------------------------------------------------- #
# _resolve_stage_asset
# --------------------------------------------------------------------------- #


class TestResolveStageAsset:
    def test_prefers_jpg(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        jpg = tmp_path / "stage_default.jpg"
        jpg.write_bytes(b"jpeg")
        monkeypatch.setattr(tp_module, "_STAGE_JPG", jpg)
        monkeypatch.setattr(tp_module, "_STAGE_SVG", tmp_path / "stage_default.svg")
        assert _resolve_stage_asset() == (jpg, "jpegdec")

    def test_falls_back_to_svg(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        svg = tmp_path / "stage_default.svg"
        svg.write_text("<svg/>")
        monkeypatch.setattr(tp_module, "_STAGE_JPG", tmp_path / "stage_default.jpg")
        monkeypatch.setattr(tp_module, "_STAGE_SVG", svg)
        assert _resolve_stage_asset() == (svg, "rsvgdec")

    def test_neither_present_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(tp_module, "_STAGE_JPG", tmp_path / "missing.jpg")
        monkeypatch.setattr(tp_module, "_STAGE_SVG", tmp_path / "missing.svg")
        with pytest.raises(RuntimeError, match="Stage asset not found"):
            _resolve_stage_asset()


# --------------------------------------------------------------------------- #
# Grey chain (default:grey)
# --------------------------------------------------------------------------- #


class TestGreyChain:
    def test_builds_grey_chain(self) -> None:
        pipeline = _build({"testpattern_selected_media": media_store.DEFAULT_GREY_ID})
        src = pipeline.get_by_name("videotestsrc")
        assert src is not None
        assert src.properties["pattern"] == "solid-color"
        assert src.properties["foreground-color"] == 0xFF808080
        assert src.properties["is-live"] is True
        assert pipeline.get_by_name("capsfilter") is not None
        assert pipeline.get_by_name("convert") is not None

    def test_missing_videotestsrc_raises(self) -> None:
        fake = make_fake_gst(missing_elements={"videotestsrc"})
        with pytest.raises(RuntimeError, match="videotestsrc"):
            _build({"testpattern_selected_media": media_store.DEFAULT_GREY_ID}, fake=fake)

    def test_capsfilter_link_failure_raises(self) -> None:
        fake = make_fake_gst(link_fail_kinds={"videotestsrc"})
        with pytest.raises(RuntimeError, match="Failed to link videotestsrc"):
            _build({"testpattern_selected_media": media_store.DEFAULT_GREY_ID}, fake=fake)


# --------------------------------------------------------------------------- #
# Image chain (stage default + user image)
# --------------------------------------------------------------------------- #


class TestImageChain:
    def test_stage_default_builds_image_chain(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        jpg = tmp_path / "stage_default.jpg"
        jpg.write_bytes(b"fake")
        monkeypatch.setattr(tp_module, "_STAGE_JPG", jpg)
        pipeline = _build({"testpattern_selected_media": media_store.DEFAULT_STAGE_ID})
        assert pipeline.get_by_name("imagefilesrc").properties["location"] == str(jpg)
        assert pipeline.get_by_name("imagedecode") is not None
        assert pipeline.get_by_name("image_imagefreeze") is not None
        assert pipeline.get_by_name("image_scale_caps") is not None
        assert pipeline.get_by_name("convert") is not None

    def test_user_image_builds_image_chain(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        img = tmp_path / "pic.jpg"
        img.write_bytes(b"jpeg")
        monkeypatch.setattr(media_store, "resolve", lambda mid: _user_item("0123456789abcdef", "image", img))
        pipeline = _build({"testpattern_selected_media": "0123456789abcdef"})
        assert pipeline.get_by_name("imagefilesrc").properties["location"] == str(img)
        assert pipeline.get_by_name("image_imagefreeze") is not None

    def test_user_image_requires_jpegdec(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        img = tmp_path / "pic.jpg"
        img.write_bytes(b"jpeg")
        monkeypatch.setattr(media_store, "resolve", lambda mid: _user_item("0123456789abcdef", "image", img))
        fake = make_fake_gst(missing_elements={"jpegdec"})
        with pytest.raises(RuntimeError, match="jpegdec"):
            _build({"testpattern_selected_media": "0123456789abcdef"}, fake=fake)

    def test_missing_imagefreeze_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        jpg = tmp_path / "stage_default.jpg"
        jpg.write_bytes(b"fake")
        monkeypatch.setattr(tp_module, "_STAGE_JPG", jpg)
        fake = make_fake_gst(missing_elements={"imagefreeze"})
        with pytest.raises(RuntimeError, match="imagefreeze"):
            _build({"testpattern_selected_media": media_store.DEFAULT_STAGE_ID}, fake=fake)

    def test_image_chain_link_failure_names_pair(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        jpg = tmp_path / "stage_default.jpg"
        jpg.write_bytes(b"fake")
        monkeypatch.setattr(tp_module, "_STAGE_JPG", jpg)
        fake = make_fake_gst(link_fail_kinds={"filesrc"})
        with pytest.raises(RuntimeError, match="Failed to link"):
            _build({"testpattern_selected_media": media_store.DEFAULT_STAGE_ID}, fake=fake)

    def test_unresolvable_selection_falls_back_to_stage(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # Empty store -> a valid-but-absent user id does not resolve.
        monkeypatch.setattr(media_store, "resolve_media_storage_path", lambda: tmp_path / "empty")
        jpg = tmp_path / "stage_default.jpg"
        jpg.write_bytes(b"fake")
        monkeypatch.setattr(tp_module, "_STAGE_JPG", jpg)
        pipeline = _build({"testpattern_selected_media": "ffffffffffffffff"})
        # Rendered the Stage default image chain, not a blank/error pipeline.
        assert pipeline.get_by_name("imagefilesrc").properties["location"] == str(jpg)


# --------------------------------------------------------------------------- #
# Clip chain (user video)
# --------------------------------------------------------------------------- #


class TestClipChain:
    def test_builds_clip_chain_and_marks_video(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        clip = tmp_path / "loop.webm"
        clip.write_bytes(b"webm")
        monkeypatch.setattr(media_store, "resolve", lambda mid: _user_item("0123456789abcdef", "video", clip))
        plugin = MediaGalleryInput()
        pipeline = _build({"testpattern_selected_media": "0123456789abcdef"}, plugin=plugin)

        assert pipeline.get_by_name("clipfilesrc").properties["location"] == str(clip)
        assert pipeline.get_by_name("clipdecode") is not None
        assert pipeline.get_by_name("clip_scale_caps") is not None
        assert pipeline.get_by_name("convert") is not None
        # decodebin's dynamic pad is linked on pad-added.
        assert any(sig == "pad-added" for sig, _cb in pipeline.get_by_name("clipdecode").signals)
        assert plugin._is_video is True

    def test_missing_decodebin_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        clip = tmp_path / "loop.webm"
        clip.write_bytes(b"webm")
        monkeypatch.setattr(media_store, "resolve", lambda mid: _user_item("0123456789abcdef", "video", clip))
        fake = make_fake_gst(missing_elements={"decodebin"})
        with pytest.raises(RuntimeError, match="decodebin"):
            _build({"testpattern_selected_media": "0123456789abcdef"}, fake=fake)


class TestLinkVideoPad:
    def test_links_video_pad_to_scaler(self) -> None:
        pad = FakePad("src_0")  # FakePad caps are "video/..." -> linked
        scale = FakeElement("scale")
        MediaGalleryInput._link_video_pad(pad, scale)
        assert pad.linked_to is scale.get_static_pad("sink")

    def test_ignores_audio_pad(self) -> None:
        class _AudioPad(FakePad):
            def get_current_caps(self) -> FakeCaps:
                return FakeCaps("audio/x-raw")

        pad = _AudioPad("audio_0")
        scale = FakeElement("scale")
        MediaGalleryInput._link_video_pad(pad, scale)
        assert pad.linked_to is None

    def test_skips_when_sink_already_linked(self) -> None:
        pad = FakePad("src_1")
        scale = FakeElement("scale")
        scale.get_static_pad("sink")._linked_into = True  # already linked
        MediaGalleryInput._link_video_pad(pad, scale)
        assert pad.linked_to is None


# --------------------------------------------------------------------------- #
# Shared / hooks
# --------------------------------------------------------------------------- #


class TestSharedAndHooks:
    def test_prepare_sink_none_raises(self) -> None:
        from gi.repository import Gst  # noqa: F401

        fake = make_fake_gst()
        with patch("gi.repository.Gst", fake), pytest.raises(RuntimeError, match="No video sink"):
            MediaGalleryInput().create_pipeline(
                config={},
                sink=FakeElement("shared_videosink"),
                build_overlay_tail=lambda *a: None,
                prepare_sink=lambda: None,
            )

    def test_on_bus_async_done_forces_zero_latency(self) -> None:
        pipeline = FakePipeline("media-gallery")
        MediaGalleryInput().on_bus_async_done(pipeline)
        assert pipeline.latency_values == [0]

    def test_on_bus_eos_loops_a_clip(self) -> None:
        fake = make_fake_gst()
        plugin = MediaGalleryInput()
        plugin._is_video = True
        pipeline = FakePipeline("clip")
        with patch("gi.repository.Gst", fake):
            handled = plugin.on_bus_eos(pipeline)
        assert handled is True
        assert len(pipeline.seeks) == 1 and pipeline.seeks[0][2] == 0

    def test_on_bus_eos_ignores_non_video(self) -> None:
        plugin = MediaGalleryInput()  # _is_video defaults False
        pipeline = FakePipeline("img")
        assert plugin.on_bus_eos(pipeline) is False
        assert pipeline.seeks == []

    def test_on_bus_eos_seek_failure_reports_unhandled(self) -> None:
        fake = make_fake_gst()
        plugin = MediaGalleryInput()
        plugin._is_video = True
        pipeline = FakePipeline("clip")
        pipeline.seek_ok = False
        with patch("gi.repository.Gst", fake):
            assert plugin.on_bus_eos(pipeline) is False


# --------------------------------------------------------------------------- #
# Config field + labels + web UI
# --------------------------------------------------------------------------- #


class TestConfigAndLabels:
    def test_selection_field_is_web_only(self) -> None:
        fields = {f.name: f for f in MediaGalleryInput.config_fields()}
        field = fields["testpattern_selected_media"]
        assert field.device_editable is False  # on-device picker/editor skip it
        assert field.default == media_store.DEFAULT_SELECTED_MEDIA
        assert field.choices == ()

    def test_display_name_is_media_gallery(self) -> None:
        assert MediaGalleryInput.display_name == "Media Gallery"

    @pytest.mark.parametrize(
        ("media_id", "expected"),
        [
            (media_store.DEFAULT_STAGE_ID, "Media Gallery (Stage)"),
            (media_store.DEFAULT_GREY_ID, "Media Gallery (Grey)"),
        ],
    )
    def test_label_for_defaults(self, media_id: str, expected: str) -> None:
        assert MediaGalleryInput.get_source_label({"testpattern_selected_media": media_id}) == expected

    def test_label_for_user_media(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        img = tmp_path / "p.jpg"
        img.write_bytes(b"x")
        monkeypatch.setattr(media_store, "resolve", lambda mid: _user_item("0123456789abcdef", "image", img))
        assert MediaGalleryInput.get_source_label({"testpattern_selected_media": "0123456789abcdef"}) == (
            "Media Gallery (Image)"
        )
        monkeypatch.setattr(media_store, "resolve", lambda mid: _user_item("0123456789abcdef", "video", img))
        assert MediaGalleryInput.get_source_label({"testpattern_selected_media": "0123456789abcdef"}) == (
            "Media Gallery (Clip)"
        )

    def test_label_unresolvable_returns_bare_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(media_store, "resolve", lambda mid: None)
        assert MediaGalleryInput.get_source_label({"testpattern_selected_media": "whatever"}) == "Media Gallery"

    def test_web_ui_html_is_a_fragment(self) -> None:
        html = MediaGalleryInput.web_ui_html({})
        assert "section-note" in html and "gallery" in html.lower()
