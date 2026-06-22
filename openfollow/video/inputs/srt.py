# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""SRT video input plugin with automatic reconnect and background healing."""

from __future__ import annotations
from openfollow.i18n import _, _l  # noqa: E402

import logging
from collections.abc import Callable
from typing import Any

from openfollow.video.inputs._base import (
    ConfigField,
    InputCapabilities,
    ReconnectPolicy,
    VideoInputBase,
    redact_uri,
)

logger = logging.getLogger(__name__)


def _resolve_srt_uri(srt_host: str) -> str:
    """Return an SRT URI, allowing ``host:port`` shorthand."""
    raw = srt_host.strip()
    if not raw:
        return "srt://0.0.0.0:5000"
    if raw.lower().startswith("srt://"):
        return raw
    return f"srt://{raw}"


class SrtInput(VideoInputBase):
    """SRT video input – caller mode; fast reconnect then background self-heal."""

    input_id = "srt"
    display_name = _l("SRT")

    # -- Declarations ---------------------------------------------------------

    @classmethod
    def config_fields(cls) -> list[ConfigField]:
        return [
            ConfigField("srt_host", str, "srt://0.0.0.0:5000", "SRT URL"),
        ]

    @classmethod
    def capabilities(cls) -> InputCapabilities:
        return InputCapabilities(
            has_source_discovery=False,
            has_source_selection=False,
            force_zero_latency=False,
        )

    @classmethod
    def reconnect_policy(cls) -> ReconnectPolicy:
        return ReconnectPolicy(
            max_attempts=3,
            min_delay=0.5,
            max_delay=3.0,
            backoff_multiplier=1.5,
            connection_timeout=8.0,
            fallback_to_selection=True,
            heal_interval=5.0,
            stall_timeout=3.0,
        )

    # -- Pipeline -------------------------------------------------------------

    @staticmethod
    def _prefer_hardware_decoders() -> None:
        """Boost hardware decoder ranks so decodebin prefers them over software.

        openh264dec is pure software and extremely slow on ARM – demote it
        below avdec_h264 (FFmpeg) and any V4L2 hardware decoder.
        """
        from gi.repository import Gst

        for name in ("v4l2h264dec", "v4l2h265dec"):
            factory = Gst.ElementFactory.find(name)
            if factory:
                factory.set_rank(Gst.Rank.PRIMARY + 1)
                logger.info("Decoder priority: %s → PRIMARY+1", name)
        factory = Gst.ElementFactory.find("openh264dec")
        if factory:
            factory.set_rank(Gst.Rank.MARGINAL)
            logger.info("Decoder priority: openh264dec → MARGINAL")

    def create_pipeline(
        self,
        config: dict[str, Any],
        sink: Any,
        build_overlay_tail: Callable[..., Any],
        prepare_sink: Callable[..., Any],
    ) -> Any:
        """Build an SRT receive pipeline.

        ``srtsrc → pre_queue → decodebin → post_queue → videoconvert
          → [overlay tail] → sink``
        """
        from gi.repository import Gst

        self._prefer_hardware_decoders()

        srt_uri = _resolve_srt_uri(config.get("srt_host", "srt://0.0.0.0:5000"))

        pipeline = Gst.Pipeline.new("srt-sink")

        srtsrc = Gst.ElementFactory.make("srtsrc", "srtsrc")
        if srtsrc is None:
            raise RuntimeError("srtsrc GStreamer element not found – install gst-plugins-bad")
        srtsrc.set_property("uri", srt_uri)
        srtsrc.set_property("mode", "caller")
        srtsrc.set_property("wait-for-connection", True)
        srtsrc.set_property("latency", 125)

        pre_queue = Gst.ElementFactory.make("queue", "pre_queue")
        pre_queue.set_property("max-size-buffers", 4)
        pre_queue.set_property("max-size-bytes", 0)
        pre_queue.set_property("max-size-time", 0)
        pre_queue.set_property("leaky", 2)

        decodebin = Gst.ElementFactory.make("decodebin", "decodebin")

        post_queue = Gst.ElementFactory.make("queue", "post_queue")
        post_queue.set_property("max-size-buffers", 2)
        post_queue.set_property("max-size-bytes", 0)
        post_queue.set_property("max-size-time", 0)
        post_queue.set_property("leaky", 2)

        convert = Gst.ElementFactory.make("videoconvert", "convert")

        sink = prepare_sink()
        if sink is None:
            raise RuntimeError("No video sink available for SRT pipeline")

        for elem in (srtsrc, pre_queue, decodebin, post_queue, convert, sink):
            pipeline.add(elem)

        if not srtsrc.link(pre_queue):
            raise RuntimeError("Failed to link srtsrc → pre_queue")
        if not pre_queue.link(decodebin):
            raise RuntimeError("Failed to link pre_queue → decodebin")
        if not post_queue.link(convert):
            raise RuntimeError("Failed to link post_queue → videoconvert")

        def on_pad_added(element: Any, pad: Any) -> None:
            sink_pad = post_queue.get_static_pad("sink")
            if sink_pad is None:
                logger.error("post_queue sink pad missing for decodebin linkage")
                return

            caps = pad.get_current_caps() or pad.query_caps(None)
            caps_str = caps.to_string() if caps else "unknown"
            if caps_str and not caps_str.startswith("video/"):
                logger.debug("Ignoring non-video decodebin pad %s (%s)", pad.get_name(), caps_str)
                return

            if sink_pad.is_linked():
                logger.debug(
                    "Decodebin produced additional video pad %s after video already linked",
                    pad.get_name(),
                )
                return

            result = pad.link(sink_pad)
            if result == Gst.PadLinkReturn.OK:
                logger.info("Linked decodebin pad %s → post_queue", pad.get_name())
            elif result in (
                Gst.PadLinkReturn.NOFORMAT,
                Gst.PadLinkReturn.REFUSED,
            ):
                logger.warning(
                    "Decodebin video pad %s rejected by post_queue (%s, caps=%s)",
                    pad.get_name(),
                    result,
                    caps_str,
                )
            else:
                logger.error("Failed to link decodebin pad %s: %s", pad.get_name(), result)

        def on_element_added(_bin: Any, element: Any) -> None:
            factory = element.get_factory()
            if factory is not None:
                klass = factory.get_metadata("klass") or ""
                name = factory.get_name()
                if "Video" in klass and "Decoder" in klass:
                    logger.info("SRT pipeline using: %s (%s)", name, klass)
                    for prop, value in (("max-threads", 2), ("output-corrupt", True)):
                        try:
                            element.set_property(prop, value)
                            logger.info("  %s: %s=%s", name, prop, value)
                        except TypeError:
                            pass
                elif "Depay" in klass:
                    logger.info("SRT pipeline using: %s (%s)", name, klass)

        decodebin.connect("pad-added", on_pad_added)
        decodebin.connect("element-added", on_element_added)
        build_overlay_tail(pipeline, convert, sink)
        return pipeline

    # -- Lifecycle hooks ------------------------------------------------------

    def on_bus_async_done(self, pipeline: Any) -> None:
        """SRT: preserve decoder latency – do nothing."""
        logger.debug("Pipeline ASYNC_DONE (SRT) – preserving decoder latency")

    # -- Web UI ---------------------------------------------------------------

    @classmethod
    def web_ui_html(cls, config: dict[str, Any]) -> str:
        srt_host = cls._esc(config.get("srt_host", "srt://0.0.0.0:5000"))
        return (
            '<div class="row">'
            '    <div class="field wide">'
            f"        <label>{_("SRT URL")}</label>"
            f'        <input type="text" name="srt_host" value="{srt_host}"'
            '               placeholder="srt://192.168.0.182:1600?streamid=r=0">'
            "    </div>"
            "</div>"
        )

    # -- Config ---------------------------------------------------------------

    @classmethod
    def get_source_label(cls, config: dict[str, Any]) -> str:
        host = config.get("srt_host", "srt://0.0.0.0:5000")
        return f"SRT {redact_uri(_resolve_srt_uri(host))}"
