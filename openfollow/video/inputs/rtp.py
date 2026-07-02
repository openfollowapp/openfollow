# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""RTP video input plugin (multicast/unicast) with auto-detected codec support."""

from __future__ import annotations
from openfollow.i18n import _, _l  # noqa: E402

import logging
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from openfollow.video.inputs._base import (
    ConfigField,
    InputCapabilities,
    ReconnectPolicy,
    VideoInputBase,
)

logger = logging.getLogger(__name__)


def _parse_rtp_url(url: str) -> tuple[str, int, bool]:
    """Parse an RTP URL into ``(address, port, is_multicast)``.

    Accepts:
      - ``rtp://232.255.255.255:4000``
      - ``232.255.255.255:4000``
      - ``0.0.0.0:4000``

    Returns ``(address, port, is_multicast)``.
    """
    raw = url.strip()
    if not raw:
        return "0.0.0.0", 5004, False

    # Normalise to a parseable URL
    if "://" not in raw:
        raw = f"rtp://{raw}"

    parsed = urlparse(raw)
    host = parsed.hostname or "0.0.0.0"
    port = parsed.port or 5004

    # RFC 5771: 224.0.0.0/4  →  first octet 224–239
    try:
        first_octet = int(host.split(".")[0])
        is_multicast = 224 <= first_octet <= 239
    except (ValueError, IndexError):
        is_multicast = False

    return host, port, is_multicast


class RtpInput(VideoInputBase):
    """RTP video input – multicast or unicast UDP receiver."""

    input_id = "rtp"
    display_name = _l("RTP")

    # -- Declarations ---------------------------------------------------------

    # Supported RTP encodings and their clock rates
    _ENCODINGS: dict[str, int] = {
        "H264": 90000,
        "H265": 90000,
        "MP2T": 90000,
    }

    @classmethod
    def config_fields(cls) -> list[ConfigField]:
        return [
            ConfigField("rtp_url", str, "rtp://0.0.0.0:5004", "RTP URL"),
            ConfigField("rtp_encoding", str, "H264", "RTP Encoding"),
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
        """Boost V4L2 hardware decoders, demote slow openh264dec."""
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
        """Build an RTP receive pipeline.

        ``udpsrc(caps) → queue → rtpjitterbuffer → decodebin
          → queue → videoconvert → [overlay tail] → sink``

        For multicast addresses (224.x–239.x) the ``multicast-group``
        property is set so ``udpsrc`` joins the multicast group.
        """
        from gi.repository import Gst

        self._prefer_hardware_decoders()

        address, port, is_multicast = _parse_rtp_url(config.get("rtp_url", "rtp://0.0.0.0:5004"))
        # Constrain to the allow-list before interpolating into the caps
        # string – a hand-edited config can hold caps metacharacters.
        encoding = config.get("rtp_encoding", "H264").upper()
        if encoding not in self._ENCODINGS:
            logger.warning("Unknown RTP encoding %r – falling back to H264", encoding)
            encoding = "H264"
        clock_rate = self._ENCODINGS[encoding]

        pipeline = Gst.Pipeline.new("rtp-sink")

        # --- UDP source ---
        udpsrc = Gst.ElementFactory.make("udpsrc", "udpsrc")
        if udpsrc is None:
            raise RuntimeError("udpsrc GStreamer element not found – install gst-plugins-good")
        udpsrc.set_property("port", port)

        if is_multicast:
            udpsrc.set_property("multicast-group", address)
            udpsrc.set_property("auto-multicast", True)
            logger.info("RTP multicast: %s:%d", address, port)
        else:
            udpsrc.set_property("address", address)
            logger.info("RTP unicast: %s:%d", address, port)

        # Tag buffers with encoding so decodebin finds the right depayloader
        caps_str = (
            f"application/x-rtp, media=(string)video, clock-rate=(int){clock_rate}, encoding-name=(string){encoding}"
        )
        udpsrc.set_property("caps", Gst.Caps.from_string(caps_str))
        logger.info("RTP caps: %s", caps_str)

        # --- Pre-decode leaky queue ---
        pre_queue = Gst.ElementFactory.make("queue", "pre_queue")
        pre_queue.set_property("max-size-buffers", 4)
        pre_queue.set_property("max-size-bytes", 0)
        pre_queue.set_property("max-size-time", 0)
        pre_queue.set_property("leaky", 2)  # downstream – drop old data

        # --- RTP jitter buffer ---
        jitterbuf = Gst.ElementFactory.make("rtpjitterbuffer", "jitterbuf")
        if jitterbuf is not None:
            jitterbuf.set_property("latency", 0)
            jitterbuf.set_property("drop-on-latency", True)
        else:
            logger.warning("rtpjitterbuffer not available – skipping")

        decodebin = Gst.ElementFactory.make("decodebin", "decodebin")

        # --- Post-decode leaky queue ---
        post_queue = Gst.ElementFactory.make("queue", "post_queue")
        post_queue.set_property("max-size-buffers", 2)
        post_queue.set_property("max-size-bytes", 0)
        post_queue.set_property("max-size-time", 0)
        post_queue.set_property("leaky", 2)

        convert = Gst.ElementFactory.make("videoconvert", "convert")

        sink = prepare_sink()
        if sink is None:
            raise RuntimeError("No video sink available for RTP pipeline")

        # Build element chain (skip jitterbuf if unavailable)
        elements = [udpsrc, pre_queue]
        if jitterbuf is not None:
            elements.append(jitterbuf)
        elements.extend([decodebin, post_queue, convert, sink])

        for elem in elements:
            pipeline.add(elem)

        # Static links: udpsrc → queue → [jitterbuf →] decodebin
        if not udpsrc.link(pre_queue):
            raise RuntimeError("Failed to link udpsrc → pre_queue")
        pre_decode = pre_queue
        if jitterbuf is not None:
            if not pre_queue.link(jitterbuf):
                raise RuntimeError("Failed to link pre_queue → rtpjitterbuffer")
            pre_decode = jitterbuf
        if not pre_decode.link(decodebin):
            raise RuntimeError("Failed to link → decodebin")
        if not post_queue.link(convert):
            raise RuntimeError("Failed to link post_queue → videoconvert")

        def on_pad_added(element: Any, pad: Any) -> None:
            sink_pad = post_queue.get_static_pad("sink")
            if sink_pad is None or sink_pad.is_linked():
                return
            result = pad.link(sink_pad)
            if result == Gst.PadLinkReturn.OK:
                logger.info("Linked decodebin pad %s → post_queue", pad.get_name())
            elif result not in (
                Gst.PadLinkReturn.NOFORMAT,
                Gst.PadLinkReturn.REFUSED,
            ):
                logger.error("Failed to link decodebin pad %s: %s", pad.get_name(), result)

        def on_element_added(_bin: Any, element: Any) -> None:
            factory = element.get_factory()
            if factory is None:
                return
            klass = factory.get_metadata("klass") or ""
            name = factory.get_name()
            if "Video" in klass and "Decoder" in klass:
                logger.info("RTP pipeline using: %s (%s)", name, klass)
                # Drop corrupt frames instead of showing artifacts
                for prop, value in (("max-threads", 2), ("output-corrupt", False)):
                    try:
                        element.set_property(prop, value)
                        logger.info("  %s: %s=%s", name, prop, value)
                    except TypeError:
                        pass
            elif "Depay" in klass:
                logger.info("RTP pipeline using: %s (%s)", name, klass)
            elif "Parse" in klass:
                logger.info("RTP pipeline using: %s (%s)", name, klass)
                # Split at each NAL unit for frame-accurate decoding
                try:
                    element.set_property("config-interval", -1)
                    logger.info("  %s: config-interval=-1", name)
                except TypeError:
                    pass

        decodebin.connect("pad-added", on_pad_added)
        decodebin.connect("element-added", on_element_added)
        build_overlay_tail(pipeline, convert, sink)
        return pipeline

    # -- Lifecycle hooks ------------------------------------------------------

    def on_bus_async_done(self, pipeline: Any) -> None:
        """RTP: force zero latency for minimal delay."""
        pipeline.set_latency(0)
        logger.info("Pipeline ASYNC_DONE (RTP) – latency forced to 0")

    # -- Web UI ---------------------------------------------------------------

    @classmethod
    def web_ui_html(cls, config: dict[str, Any]) -> str:
        rtp_url = cls._esc(config.get("rtp_url", "rtp://0.0.0.0:5004"))
        cur_enc = config.get("rtp_encoding", "H264").upper()
        options = []
        for enc in cls._ENCODINGS:
            sel = " selected" if enc == cur_enc else ""
            options.append(f'<option value="{enc}"{sel}>{enc}</option>')
        opts_html = "\n".join(options)
        return (
            '<div class="row">'
            '    <div class="field wide">'
            f"        <label>{_("RTP URL")}</label>"
            f'        <input type="text" name="rtp_url" value="{rtp_url}"'
            '               placeholder="rtp://232.255.255.255:4000">'
            "    </div>"
            "</div>"
            '<div class="row">'
            '    <div class="field">'
            f"        <label>{_("Encoding")}</label>"
            f'        <select name="rtp_encoding">{opts_html}</select>'
            "    </div>"
            "</div>"
        )

    # -- Config ---------------------------------------------------------------

    @classmethod
    def get_source_label(cls, config: dict[str, Any]) -> str:
        url = config.get("rtp_url", "rtp://0.0.0.0:5004")
        encoding = config.get("rtp_encoding", "H264").upper()
        address, port, is_multicast = _parse_rtp_url(url)
        mode = "mcast" if is_multicast else "unicast"
        return f"RTP {address}:{port} {encoding} ({mode})"
