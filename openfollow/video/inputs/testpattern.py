# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Static test pattern input plugin with grey and stage modes."""

from __future__ import annotations
from openfollow.i18n import _, _l  # noqa: E402

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


_RESOLUTIONS: dict[str, tuple[int, int]] = {
    "720p": (1280, 720),
    "1080p": (1920, 1080),
    "2160p": (3840, 2160),
}

_PATTERNS: dict[str, str] = {
    "grey": "50% Grey",
    "stage": "Stage Scene",
}

_ASSET_DIR = Path(__file__).parent / "assets"
_STAGE_JPG = _ASSET_DIR / "stage_default.jpg"
_STAGE_SVG = _ASSET_DIR / "stage_default.svg"


def _resolve_stage_asset() -> tuple[Path, str]:
    """Return (path, gstreamer-decoder-name) for the best stage asset.

    Prefers the JPG (photoreal version) over the SVG (line-art demo).
    Raises if neither is present.
    """
    if _STAGE_JPG.exists():
        return _STAGE_JPG, "jpegdec"
    if _STAGE_SVG.exists():
        return _STAGE_SVG, "rsvgdec"
    raise RuntimeError(f"Stage test pattern asset not found. Looked for {_STAGE_JPG} and {_STAGE_SVG}.")


class TestPatternInput(VideoInputBase):
    """Static test image – choose grey or stage scene."""

    input_id = "testpattern"
    display_name = _l("Test Pattern")

    @classmethod
    def config_fields(cls) -> list[ConfigField]:
        return [
            ConfigField(
                "testpattern_pattern",
                str,
                "stage",
                "Pattern",
                choices=tuple(_PATTERNS.items()),
            ),
            ConfigField("testpattern_resolution", str, "1080p", "Resolution"),
        ]

    @classmethod
    def capabilities(cls) -> InputCapabilities:
        return InputCapabilities(force_zero_latency=True)

    @classmethod
    def reconnect_policy(cls) -> ReconnectPolicy:
        return ReconnectPolicy(
            max_attempts=1,
            connection_timeout=2.0,
        )

    def create_pipeline(
        self,
        config: dict[str, Any],
        sink: Any,
        build_overlay_tail: Callable[..., Any],
        prepare_sink: Callable[..., Any],
    ) -> Any:
        from gi.repository import Gst

        pattern = config.get("testpattern_pattern", "grey")
        if pattern not in _PATTERNS:
            pattern = "grey"

        resolution = config.get("testpattern_resolution", "1080p")
        width, height = _RESOLUTIONS.get(resolution, _RESOLUTIONS["1080p"])

        pipeline = Gst.Pipeline.new("testpattern-sink")

        sink = prepare_sink()
        if sink is None:
            raise RuntimeError("No video sink available for test pattern pipeline")

        if pattern == "grey":
            head = self._build_grey_chain(pipeline, width, height)
        else:
            head = self._build_stage_chain(pipeline, width, height)

        logger.info("Test pattern: %s @ %dx%d", _PATTERNS[pattern], width, height)

        pipeline.add(sink)
        build_overlay_tail(pipeline, head, sink)
        return pipeline

    @staticmethod
    def _build_grey_chain(pipeline: Any, width: int, height: int) -> Any:
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
            "caps", Gst.Caps.from_string(f"video/x-raw,width={width},height={height},framerate=30/1")
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
    def _build_stage_chain(pipeline: Any, width: int, height: int) -> Any:
        """``filesrc → decode → scale → imagefreeze → rate → convert``.

        Returns the final ``videoconvert`` element so it can be handed to
        ``build_overlay_tail`` exactly like the grey-chain head.
        """
        from gi.repository import Gst

        asset_path, decoder_name = _resolve_stage_asset()
        logger.info("Stage test pattern source: %s (%s)", asset_path.name, decoder_name)

        src = Gst.ElementFactory.make("filesrc", "stagefilesrc")
        if src is None:
            raise RuntimeError("filesrc GStreamer element not found")
        src.set_property("location", str(asset_path))

        decode = Gst.ElementFactory.make(decoder_name, "stagedecode")
        if decode is None:
            raise RuntimeError(
                f"GStreamer decoder '{decoder_name}' not available – install the matching gst-plugins package."
            )

        pre_convert = Gst.ElementFactory.make("videoconvert", "stage_pre_convert")
        scale = Gst.ElementFactory.make("videoscale", "stagevideoscale")
        scale_caps = Gst.ElementFactory.make("capsfilter", "stage_scale_caps")
        scale_caps.set_property("caps", Gst.Caps.from_string(f"video/x-raw,width={width},height={height}"))

        freeze = Gst.ElementFactory.make("imagefreeze", "stage_imagefreeze")
        if freeze is None:
            raise RuntimeError("imagefreeze GStreamer element not found")
        freeze.set_property("is-live", True)

        rate_caps = Gst.ElementFactory.make("capsfilter", "stage_rate_caps")
        rate_caps.set_property(
            "caps", Gst.Caps.from_string(f"video/x-raw,width={width},height={height},framerate=30/1")
        )

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

    def on_bus_async_done(self, pipeline: Any) -> None:
        pipeline.set_latency(0)

    @classmethod
    def web_ui_html(cls, config: dict[str, Any]) -> str:
        current_pattern = config.get("testpattern_pattern", "grey")
        if current_pattern not in _PATTERNS:
            current_pattern = "grey"
        pattern_options = "".join(
            f'<option value="{key}"{" selected" if key == current_pattern else ""}>{label}</option>'
            for key, label in _PATTERNS.items()
        )

        current_res = config.get("testpattern_resolution", "1080p")
        if current_res not in _RESOLUTIONS:
            current_res = "1080p"
        res_options = "".join(
            f'<option value="{key}"{" selected" if key == current_res else ""}>{key} ({w}×{h})</option>'
            for key, (w, h) in _RESOLUTIONS.items()
        )

        return (
            '<div class="row">'
            '    <div class="field wide">'
            '        <p style="margin:0 0 0.5rem 0;color:var(--text-muted,#888);">'
            f"            {_('Static test image – useful for debugging overlay, Operator Screen, and detection without a live source.')}"
            "        </p>"
            "    </div>"
            "</div>"
            '<div class="row">'
            '    <div class="field">'
            f"        <label>{_('Pattern')}</label>"
            '        <select name="testpattern_pattern">'
            f"            {pattern_options}"
            "        </select>"
            "    </div>"
            '    <div class="field">'
            f"        <label>{_('Resolution')}</label>"
            '        <select name="testpattern_resolution">'
            f"            {res_options}"
            "        </select>"
            "    </div>"
            "</div>"
        )

    @classmethod
    def get_source_label(cls, config: dict[str, Any]) -> str:
        pattern = config.get("testpattern_pattern", "grey")
        label = _PATTERNS.get(pattern, _PATTERNS["grey"])
        resolution = config.get("testpattern_resolution", "1080p")
        return f"Test Pattern {label} ({resolution})"
