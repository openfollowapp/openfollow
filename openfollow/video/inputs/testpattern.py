# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Media Gallery input plugin.

Plays a still image, a looping VP8/WebM clip, or one of the two read-only
bundled defaults (Stage scene, Grey) selected from the device-local media
store. The active item is identified by ``testpattern_selected_media``; an
unresolvable id silently falls back to the Stage default.

The plugin ``input_id`` stays ``testpattern`` for config compatibility; the
operator-facing name is "Media Gallery". The selection is web-only
(``device_editable=False``) so the on-device menu can pick this source but not
change its content.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from openfollow.video.inputs._base import (
    ConfigField,
    InputCapabilities,
    ReconnectPolicy,
    VideoInputBase,
)

logger = logging.getLogger(__name__)

# Output resolution for the synthetic Grey pattern, and the scale ceiling for
# images/clips. Images and clips fit within this box preserving aspect (the
# capsfilter range never upscales); the sink scales to the window.
_OUTPUT_WIDTH = 1920
_OUTPUT_HEIGHT = 1080
_FRAMERATE = "30/1"
_FIT_CAPS = (
    f"video/x-raw,width=(int)[1,{_OUTPUT_WIDTH}],height=(int)[1,{_OUTPUT_HEIGHT}],pixel-aspect-ratio=(fraction)1/1"
)

_ASSET_DIR = Path(__file__).parent / "assets"
_STAGE_JPG = _ASSET_DIR / "stage_default.jpg"
_STAGE_SVG = _ASSET_DIR / "stage_default.svg"


def _resolve_stage_asset() -> tuple[Path, str]:
    """Return (path, gstreamer-decoder-name) for the best Stage asset.

    Prefers the JPG (photoreal) over the SVG (line-art demo). Raises if neither
    is present.
    """
    if _STAGE_JPG.exists():
        return _STAGE_JPG, "jpegdec"
    if _STAGE_SVG.exists():
        return _STAGE_SVG, "rsvgdec"
    raise RuntimeError(f"Stage asset not found. Looked for {_STAGE_JPG} and {_STAGE_SVG}.")


class MediaGalleryInput(VideoInputBase):
    """Media Gallery source: still image, looping VP8 clip, or a bundled default."""

    input_id = "testpattern"
    display_name = "Media Gallery"

    def __init__(self) -> None:
        super().__init__()
        # Set per build; gates EOS-driven looping (only clips loop).
        self._is_video = False

    @classmethod
    def config_fields(cls) -> list[ConfigField]:
        from openfollow.video import media_store

        return [
            ConfigField(
                "testpattern_selected_media",
                str,
                media_store.DEFAULT_SELECTED_MEDIA,
                "Selected media",
                device_editable=False,
            ),
        ]

    @classmethod
    def capabilities(cls) -> InputCapabilities:
        return InputCapabilities(force_zero_latency=True)

    @classmethod
    def reconnect_policy(cls) -> ReconnectPolicy:
        return ReconnectPolicy(max_attempts=1, connection_timeout=2.0)

    def create_pipeline(
        self,
        config: dict[str, Any],
        sink: Any,
        build_overlay_tail: Callable[..., Any],
        prepare_sink: Callable[..., Any],
    ) -> Any:
        from gi.repository import Gst

        from openfollow.video import media_store

        selected = config.get("testpattern_selected_media", media_store.DEFAULT_SELECTED_MEDIA)
        item = media_store.resolve(selected)
        if item is None:
            logger.info("Media %r not found; falling back to the Stage default.", selected)
            item = media_store.resolve(media_store.DEFAULT_STAGE_ID)
        assert item is not None  # the Stage default always resolves

        pipeline = Gst.Pipeline.new("media-gallery")
        prepared = prepare_sink()
        if prepared is None:
            raise RuntimeError("No video sink available for media gallery pipeline")

        self._is_video = item.kind == "video"
        if item.media_id == media_store.DEFAULT_GREY_ID:
            head = self._build_grey_chain(pipeline)
        elif item.kind == "video":
            head = self._build_video_chain(pipeline, item.path)
        else:
            head = self._build_image_chain(pipeline, item)

        logger.info("Media Gallery source: %s", item.label)
        pipeline.add(prepared)
        build_overlay_tail(pipeline, head, prepared)
        return pipeline

    @staticmethod
    def _build_grey_chain(pipeline: Any) -> Any:
        """``videotestsrc → capsfilter → videoconvert``. Returns the convert tail."""
        from gi.repository import Gst

        src = Gst.ElementFactory.make("videotestsrc", "videotestsrc")
        if src is None:
            raise RuntimeError("videotestsrc GStreamer element not found")
        src.set_property("pattern", "solid-color")
        src.set_property("foreground-color", 0xFF808080)
        src.set_property("is-live", True)

        capsfilter = Gst.ElementFactory.make("capsfilter", "capsfilter")
        capsfilter.set_property(
            "caps",
            Gst.Caps.from_string(f"video/x-raw,width={_OUTPUT_WIDTH},height={_OUTPUT_HEIGHT},framerate={_FRAMERATE}"),
        )
        convert = Gst.ElementFactory.make("videoconvert", "convert")

        for elem in (src, capsfilter, convert):
            pipeline.add(elem)
        if not src.link(capsfilter):
            raise RuntimeError("Failed to link videotestsrc → capsfilter")
        if not capsfilter.link(convert):
            raise RuntimeError("Failed to link capsfilter → videoconvert")
        return convert

    @staticmethod
    def _build_image_chain(pipeline: Any, item: Any) -> Any:
        """``filesrc → decode → scale → imagefreeze → rate → convert`` for a still.

        Serves both the Stage default (asset + jpeg/svg decoder) and user images
        (always normalised to JPEG on store, so ``jpegdec``).
        """
        from gi.repository import Gst

        from openfollow.video import media_store

        if item.media_id == media_store.DEFAULT_STAGE_ID:
            asset_path, decoder_name = _resolve_stage_asset()
        else:
            asset_path, decoder_name = item.path, "jpegdec"
        logger.info("Media Gallery image: %s (%s)", Path(asset_path).name, decoder_name)

        src = Gst.ElementFactory.make("filesrc", "imagefilesrc")
        if src is None:
            raise RuntimeError("filesrc GStreamer element not found")
        src.set_property("location", str(asset_path))

        decode = Gst.ElementFactory.make(decoder_name, "imagedecode")
        if decode is None:
            raise RuntimeError(
                f"GStreamer decoder '{decoder_name}' not available – install the matching gst-plugins package."
            )

        pre_convert = Gst.ElementFactory.make("videoconvert", "image_pre_convert")
        scale = Gst.ElementFactory.make("videoscale", "imagevideoscale")
        scale_caps = Gst.ElementFactory.make("capsfilter", "image_scale_caps")
        scale_caps.set_property("caps", Gst.Caps.from_string(_FIT_CAPS))

        freeze = Gst.ElementFactory.make("imagefreeze", "image_imagefreeze")
        if freeze is None:
            raise RuntimeError("imagefreeze GStreamer element not found")
        freeze.set_property("is-live", True)

        rate_caps = Gst.ElementFactory.make("capsfilter", "image_rate_caps")
        rate_caps.set_property("caps", Gst.Caps.from_string(f"video/x-raw,framerate={_FRAMERATE}"))
        convert = Gst.ElementFactory.make("videoconvert", "convert")

        elems = (src, decode, pre_convert, scale, scale_caps, freeze, rate_caps, convert)
        for elem in elems:
            pipeline.add(elem)
        prev = src
        for nxt in elems[1:]:
            if not prev.link(nxt):
                raise RuntimeError(f"Failed to link {prev.get_name()} → {nxt.get_name()}")
            prev = nxt
        return convert

    @classmethod
    def _build_video_chain(cls, pipeline: Any, path: Any) -> Any:
        """``filesrc → decodebin → scale → convert`` for a looping clip.

        ``decodebin`` exposes the decoded video on a dynamic pad, linked on
        ``pad-added``; audio pads (if any) are ignored. Looping is handled by
        :meth:`on_bus_eos` seeking back to the start.
        """
        from gi.repository import Gst

        src = Gst.ElementFactory.make("filesrc", "clipfilesrc")
        if src is None:
            raise RuntimeError("filesrc GStreamer element not found")
        src.set_property("location", str(path))

        decode = Gst.ElementFactory.make("decodebin", "clipdecode")
        if decode is None:
            raise RuntimeError("decodebin GStreamer element not found")

        scale = Gst.ElementFactory.make("videoscale", "clipvideoscale")
        scale_caps = Gst.ElementFactory.make("capsfilter", "clip_scale_caps")
        scale_caps.set_property("caps", Gst.Caps.from_string(_FIT_CAPS))
        convert = Gst.ElementFactory.make("videoconvert", "convert")

        for elem in (src, decode, scale, scale_caps, convert):
            pipeline.add(elem)
        if not src.link(decode):
            raise RuntimeError("Failed to link filesrc → decodebin")
        if not scale.link(scale_caps):
            raise RuntimeError("Failed to link videoscale → capsfilter")
        if not scale_caps.link(convert):
            raise RuntimeError("Failed to link capsfilter → videoconvert")

        decode.connect("pad-added", lambda _dbin, pad: cls._link_video_pad(pad, scale))
        return convert

    @staticmethod
    def _link_video_pad(pad: Any, scale: Any) -> None:
        """Link a decodebin video src pad into the scaler, ignoring audio."""
        caps = pad.get_current_caps() or pad.query_caps(None)
        if caps is None or not caps.to_string().startswith("video/"):
            return
        sink_pad = scale.get_static_pad("sink")
        if sink_pad is not None and not sink_pad.is_linked():
            pad.link(sink_pad)

    def on_bus_async_done(self, pipeline: Any) -> None:
        pipeline.set_latency(0)

    def on_bus_eos(self, pipeline: Any) -> bool:
        """Loop a clip by seeking to the start; report handled so the receiver
        does not treat EOS as a disconnect. Stills/Grey never reach EOS."""
        if not self._is_video:
            return False
        from gi.repository import Gst

        ok = pipeline.seek_simple(
            Gst.Format.TIME,
            Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
            0,
        )
        return bool(ok)

    @classmethod
    def web_ui_html(cls, config: dict[str, Any]) -> str:
        from openfollow.video import media_store

        # The grid is loaded + re-rendered by the gallery management routes; the
        # hidden field carries the current selection so a video-source save
        # round-trips it. Upload streams the raw file body to the upload route.
        selected = config.get("testpattern_selected_media", media_store.DEFAULT_SELECTED_MEDIA)
        return (
            '<div class="row"><div class="field wide">'
            '<p class="section-note">Click an image or clip to make it the source. '
            "Capture a frame from any live source, or upload your own.</p>"
            f'<input type="hidden" name="testpattern_selected_media" value="{cls._esc(selected)}">'
            '<div class="gallery-toolbar">'
            '<label class="btn-link gallery-upload-btn">Upload image or clip'
            '<input type="file" accept="image/jpeg,image/png,image/webp,video/webm" '
            'onchange="openfollowGalleryUpload(this)" hidden></label>'
            "</div>"
            # hx-target="this" is required: without it the grid inherits the
            # parent video-source form's hx-target and would replace the whole
            # form on load instead of just itself.
            '<div id="gallery-grid" class="gallery-grid" '
            'hx-get="/video-input/testpattern/list" hx-trigger="load" hx-target="this" hx-swap="outerHTML"></div>'
            "<script>"
            "function openfollowGalleryUpload(input){"
            "var f=input.files[0];if(!f)return;"
            "var g=document.getElementById('gallery-grid');if(g)g.classList.add('is-loading');"
            "fetch('/video-input/testpattern/upload',{method:'POST',body:f})"
            ".then(function(r){return r.text();})"
            ".then(function(html){var el=document.getElementById('gallery-grid');"
            "if(el){var t=document.createElement('template');t.innerHTML=html.trim();"
            "el.replaceWith(t.content.firstChild);if(window.htmx)htmx.process(document.getElementById('gallery-grid'));}})"
            ".catch(function(){var el=document.getElementById('gallery-grid');"
            "if(el)el.classList.remove('is-loading');});input.value='';}"
            "</script>"
            "</div></div>"
        )

    @classmethod
    def get_source_label(cls, config: dict[str, Any]) -> str:
        from openfollow.video import media_store

        selected = config.get("testpattern_selected_media", media_store.DEFAULT_SELECTED_MEDIA)
        item = media_store.resolve(selected) or media_store.resolve(media_store.DEFAULT_STAGE_ID)
        if item is None:
            return "Media Gallery"
        if item.read_only:
            return f"Media Gallery ({item.label})"
        kind = "Clip" if item.kind == "video" else "Image"
        return f"Media Gallery ({kind})"
