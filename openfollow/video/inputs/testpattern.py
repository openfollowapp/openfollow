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

from openfollow.i18n import _, _l  # noqa: E402
from openfollow.video.inputs._base import (
    ConfigField,
    InputCapabilities,
    ReconnectPolicy,
    VideoInputBase,
)

logger = logging.getLogger(__name__)

# Fixed output resolution for the Grey pattern, images, and clips. It must be a
# fixed size, not a range: with a range, videoscale defers to the gtksink's
# preferred size (a tiny default), so the picture renders at that size and looks
# soft on the HDMI display. videoscale runs with add-borders so non-16:9 media
# letterboxes (preserving aspect) instead of stretching.
_OUTPUT_WIDTH = 1920
_OUTPUT_HEIGHT = 1080
_FRAMERATE = "30/1"
_OUTPUT_CAPS = f"video/x-raw,width={_OUTPUT_WIDTH},height={_OUTPUT_HEIGHT}"

# Maps the Stage asset's file type to its GStreamer decoder. The asset path and
# the JPG-preferred-over-SVG ordering live in ``media_store`` (single source).
_STAGE_DECODERS = {".jpg": "jpegdec", ".svg": "rsvgdec"}


def _resolve_stage_asset() -> tuple[Path, str]:
    """Return (path, gstreamer-decoder-name) for the best Stage asset.

    Reuses ``media_store.stage_asset_path`` (JPG photoreal preferred over the
    SVG line-art demo) and maps the suffix to its decoder. Raises if neither
    asset is present.
    """
    from openfollow.video import media_store

    path = media_store.stage_asset_path()
    if path is None:
        raise RuntimeError(
            f"Stage asset not found. Looked for {media_store.STAGE_ASSET_JPG} and {media_store.STAGE_ASSET_SVG}."
        )
    return path, _STAGE_DECODERS[path.suffix]


class MediaGalleryInput(VideoInputBase):
    """Media Gallery source: still image, looping VP8 clip, or a bundled default."""

    input_id = "testpattern"
    display_name = _l("Media Gallery")

    def __init__(self) -> None:
        super().__init__()
        # Set per build; gates seamless looping (only clips loop).
        self._is_video = False
        # True once the segment-loop seek has been issued for this pipeline.
        self._loop_armed = False

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
        self._loop_armed = False
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
        scale.set_property("add-borders", True)
        scale_caps = Gst.ElementFactory.make("capsfilter", "image_scale_caps")
        scale_caps.set_property("caps", Gst.Caps.from_string(_OUTPUT_CAPS))

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
        ``pad-added``; audio pads (if any) are ignored. Looping is seamless: a
        segment seek (armed in :meth:`on_bus_async_done`) makes the clip post
        ``SEGMENT_DONE`` instead of ``EOS`` at the end, and
        :meth:`on_bus_segment_done` queues the next iteration with a
        non-flushing seek so the sink never stops.
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
        scale.set_property("add-borders", True)
        scale_caps = Gst.ElementFactory.make("capsfilter", "clip_scale_caps")
        scale_caps.set_property("caps", Gst.Caps.from_string(_OUTPUT_CAPS))
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
        """Link a decodebin video src pad into the scaler, ignoring audio.

        A pad is skipped only when its caps positively identify it as
        non-video (audio / subtitle). An un-negotiated pad whose caps aren't
        resolved yet (``None`` / ``ANY``) is treated as the video pad so the
        clip's sole video stream is never dropped. A failed link is logged
        rather than silently swallowed, so a negotiation failure leaves a trace
        instead of a mute reconnect loop.
        """
        from gi.repository import Gst

        caps = pad.get_current_caps() or pad.query_caps(None)
        media_type = caps.to_string() if caps is not None else ""
        if media_type.startswith(("audio/", "subtitle/", "text/")):
            return
        sink_pad = scale.get_static_pad("sink")
        if sink_pad is None or sink_pad.is_linked():
            return
        if pad.link(sink_pad) != Gst.PadLinkReturn.OK:
            logger.warning("Media Gallery clip: failed to link decoded video pad to the scaler")

    def _segment_seek(self, pipeline: Any, *, flush: bool) -> bool:
        """Seek 0 → end in SEGMENT mode.

        SEGMENT mode makes the clip post ``SEGMENT_DONE`` (not ``EOS``) when it
        reaches the end. The non-flushing variant (``flush=False``) queues the
        next iteration gaplessly – the sink keeps running, so no frame gap, no
        re-preroll, and the stall watchdog never trips. The flushing variant
        arms the loop (or recovers from a stray EOS).
        """
        from gi.repository import Gst

        flags = Gst.SeekFlags.SEGMENT
        if flush:
            flags |= Gst.SeekFlags.FLUSH
        ok, duration = pipeline.query_duration(Gst.Format.TIME)
        if ok and duration > 0:
            return bool(pipeline.seek(1.0, Gst.Format.TIME, flags, Gst.SeekType.SET, 0, Gst.SeekType.SET, duration))
        # Duration not known yet: leave the stop edge alone (plays to natural
        # end, where SEGMENT mode still posts SEGMENT_DONE).
        return bool(pipeline.seek(1.0, Gst.Format.TIME, flags, Gst.SeekType.SET, 0, Gst.SeekType.NONE, 0))

    def on_bus_async_done(self, pipeline: Any) -> None:
        pipeline.set_latency(0)
        # Arm seamless looping once per pipeline. The arming seek flushes, but
        # it happens at startup (the clip is at ~0), so there is no visible glitch.
        if self._is_video and not self._loop_armed and self._segment_seek(pipeline, flush=True):
            self._loop_armed = True

    def on_bus_segment_done(self, pipeline: Any) -> bool:
        """Loop the clip gaplessly with a non-flushing segment seek. Stills /
        Grey never arm a segment, so they never reach here."""
        if not self._is_video:
            return False
        return self._segment_seek(pipeline, flush=False)

    def on_bus_eos(self, pipeline: Any) -> bool:
        """SEGMENT looping suppresses EOS; a stray EOS (e.g. before the loop is
        armed) re-arms the seamless loop rather than reading as a disconnect.
        Report unhandled if the re-seek fails so the receiver can recover."""
        if not self._is_video:
            return False
        self._loop_armed = self._segment_seek(pipeline, flush=True)
        return self._loop_armed

    @classmethod
    def web_ui_html(cls, config: dict[str, Any]) -> str:
        from openfollow.video import media_store

        # The grid is loaded + re-rendered by the gallery management routes;
        # selection is persisted there (POST /video-input/testpattern/select).
        # The selection is deliberately NOT mirrored into a form field: a
        # video-source form Save would otherwise post a stale render-time value
        # and revert a selection made via the grid. Upload streams the raw file
        # body to the upload route.
        max_bytes = media_store.MAX_VIDEO_UPLOAD_BYTES
        max_mb = max_bytes // media_store.BYTES_PER_MB
        return (
            '<div class="row"><div class="field wide">'
            '<div class="gallery-toolbar">'
            # A real <button> (not a <label>) so the form's label styling doesn't
            # render it as an uppercase header; it clicks the hidden file input.
            '<button type="button" class="btn-link gallery-upload-btn" '
            f'onclick="this.nextElementSibling.click()">{_("Upload image or clip")}</button>'
            '<input type="file" accept="image/jpeg,image/png,image/webp,video/webm" '
            'onchange="openfollowGalleryUpload(this)" hidden>'
            "</div>"
            # hx-target="this" is required: without it the grid inherits the
            # parent video-source form's hx-target and would replace the whole
            # form on load instead of just itself.
            '<div id="gallery-grid" class="gallery-grid" '
            'hx-get="/video-input/testpattern/list" hx-trigger="load" hx-target="this" hx-swap="outerHTML"></div>'
            "<script>"
            f"var OPENFOLLOW_MEDIA_MAX_BYTES={max_bytes};"
            # Surface an inline banner without discarding the loaded tiles - the
            # server-rendered grid uses the same .gallery-error markup, so an
            # error never disappears silently and the next render clears it.
            "function openfollowGalleryError(msg){"
            "var g=document.getElementById('gallery-grid');if(!g)return;"
            "g.classList.remove('is-loading');"
            "var b=g.querySelector('.gallery-error');"
            "if(!b){b=document.createElement('div');b.className='gallery-error';"
            "b.setAttribute('role','alert');g.insertBefore(b,g.firstChild);}"
            "b.textContent=msg;}"
            "function openfollowGalleryUpload(input){"
            "var f=input.files[0];if(!f)return;"
            # Reject over-cap files before the fetch: a browser keeps streaming
            # the body while the server returns early, which resets the socket
            # mid-upload and rejects the fetch with no response to render.
            f"if(f.size>OPENFOLLOW_MEDIA_MAX_BYTES){{openfollowGalleryError("
            f"'File too large (max {max_mb} MB).');input.value='';return;}}"
            "var g=document.getElementById('gallery-grid');if(g)g.classList.add('is-loading');"
            "fetch('/video-input/testpattern/upload',{method:'POST',body:f})"
            ".then(function(r){return r.text().then(function(html){return {ok:r.ok,html:html};});})"
            ".then(function(res){var el=document.getElementById('gallery-grid');if(!el)return;"
            "var t=document.createElement('template');t.innerHTML=(res.html||'').trim();"
            "var node=t.content.firstChild;"
            # Only swap in a real grid partial. A non-2xx status or a non-grid
            # body (login redirect, proxy error) would otherwise replace - and
            # wipe - the grid container, leaving it unrecoverable without reload.
            "if(!res.ok||!node||node.id!=='gallery-grid'){openfollowGalleryError("
            "'Upload failed. Check the connection and try again.');return;}"
            "el.replaceWith(node);if(window.htmx)htmx.process(document.getElementById('gallery-grid'));})"
            ".catch(function(){openfollowGalleryError("
            "'Upload failed. Check the connection and try again.');});"
            "input.value='';}"
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
