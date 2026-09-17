# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""RTSP video input plugin with automatic codec negotiation and flexible transport."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from openfollow.uri_redaction import redact_uri, strip_uri_userinfo
from openfollow.video.failure import ConnectionPhase, SourceKind
from openfollow.video.inputs._base import (
    ConfigField,
    InputCapabilities,
    PhaseReporter,
    ReconnectPolicy,
    SourceEndpoint,
    VideoInputBase,
)

logger = logging.getLogger(__name__)


def _endpoint_from_url(
    url: str, *, scheme: str, default_port: int, connection_oriented: bool = True
) -> SourceEndpoint | None:
    """``SourceEndpoint`` for a configured URL, or ``None`` if it names no host.

    The wildcard placeholder every URL field ships with (``0.0.0.0``) names no
    host to reach, so it reads as unconfigured rather than as an address that
    failed.
    """
    raw = url.strip()
    if not raw:
        return None
    parts = urlsplit(raw if "://" in raw else f"{scheme}://{raw}")
    host = parts.hostname or ""
    if not host or host == "0.0.0.0":  # noqa: S104 - comparison, not a bind
        return None
    try:
        port = parts.port or default_port
    except ValueError:
        # An omitted port means "the default"; a malformed or out-of-range one
        # means the URL is unusable, and GStreamer will fail on it. Quietly
        # substituting the default would have diagnostics probe - and possibly
        # report success for - an endpoint the pipeline never opens.
        return SourceEndpoint(
            host=host,
            port=0,
            connection_oriented=connection_oriented,
            problem="the URL's port is not a usable number",
        )
    return SourceEndpoint(host=host, port=port, connection_oriented=connection_oriented)


class RtspInput(VideoInputBase):
    """RTSP video input with auto-codec and multi-transport negotiation."""

    input_id = "rtsp"
    display_name = "RTSP"
    source_element_name = "rtspsrc"
    source_kind = SourceKind.REMOTE

    # -- Declarations ---------------------------------------------------------

    @classmethod
    def config_fields(cls) -> list[ConfigField]:
        return [
            ConfigField("rtsp_url", str, "rtsp://0.0.0.0:554/stream", "RTSP URL"),
            # Web-only: the on-device editor picks the first device-editable
            # string field, and a credential must never be the one it opens
            # in plain text on a stage projector.
            ConfigField("rtsp_user", str, "", "Username", device_editable=False),
            ConfigField("rtsp_password", str, "", "Password", device_editable=False, strip=False),
        ]

    @classmethod
    def capabilities(cls) -> InputCapabilities:
        return InputCapabilities(
            has_source_discovery=False,
            has_source_selection=False,
            force_zero_latency=True,
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
                logger.info("Decoder priority: %s -> PRIMARY+1", name)
        factory = Gst.ElementFactory.find("openh264dec")
        if factory:
            factory.set_rank(Gst.Rank.MARGINAL)
            logger.info("Decoder priority: openh264dec -> MARGINAL")

    @staticmethod
    def _credentials(config: dict[str, Any]) -> tuple[str, str]:
        """Return the configured ``(user, password)``, blank when unset.

        The password is taken verbatim – edge whitespace can be part of it and
        is invisible in a password field – while the username is stripped, so
        a pasted identifier with a trailing space still authenticates.
        """
        user = str(config.get("rtsp_user", "") or "").strip()
        password = str(config.get("rtsp_password", "") or "")
        return user, password

    def create_pipeline(
        self,
        config: dict[str, Any],
        sink: Any,
        build_overlay_tail: Callable[..., Any],
        prepare_sink: Callable[..., Any],
    ) -> Any:
        """Build an RTSP receive pipeline.

        ``rtspsrc → decodebin → queue → videoconvert → [overlay tail] → sink``

        ``rtspsrc`` handles SDP negotiation, RTP depayloading, and jitter
        buffering internally.
        """
        from gi.repository import Gst

        self._prefer_hardware_decoders()

        rtsp_url = str(config.get("rtsp_url", "rtsp://0.0.0.0:554/stream"))
        user, password = self._credentials(config)

        pipeline = Gst.Pipeline.new("rtsp-sink")

        # --- RTSP source ---
        rtspsrc = Gst.ElementFactory.make("rtspsrc", "rtspsrc")
        if rtspsrc is None:
            raise RuntimeError("rtspsrc GStreamer element not found -- install gst-plugins-good")
        # With explicit credentials, hand over a bare location: rtspsrc tries
        # the URL's own userinfo first, so a stale one left in the URL would
        # outrank what the operator typed into the login fields.
        location = strip_uri_userinfo(rtsp_url) if (user or password) else rtsp_url
        rtspsrc.set_property("location", location)
        if user or password:
            rtspsrc.set_property("user-id", user)
            rtspsrc.set_property("user-pw", password)
        rtspsrc.set_property("latency", 0)
        rtspsrc.set_property("drop-on-latency", True)
        rtspsrc.set_property("buffer-mode", 0)  # none – lowest latency
        # Allow TCP + UDP + UDP-multicast so RTSP can negotiate the best working
        # transport for the current network (Pi/macOS/firewall differences).
        rtspsrc.set_property("protocols", 0x00000007)
        logger.info(
            "RTSP source: %s (latency=0, tcp+udp+multicast, login=%s)",
            redact_uri(location),
            "set" if (user or password) else "none",
        )

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
            raise RuntimeError("No video sink available for RTSP pipeline")

        for elem in (rtspsrc, decodebin, post_queue, convert, sink):
            pipeline.add(elem)

        # rtspsrc has dynamic pads – link to decodebin on pad-added
        def on_rtspsrc_pad_added(element: Any, pad: Any) -> None:
            sink_pad = decodebin.get_static_pad("sink")
            if sink_pad is None or sink_pad.is_linked():
                return
            # rtspsrc exposes one RTP src pad per SDP media track
            # (application/x-rtp, media=(string)video|audio|...). Link only the
            # video track – blindly linking the first pad drops video entirely
            # when a camera/NVR lists its audio track first in the SDP. Caps we
            # can't read yet carry no media field; link those rather than stall.
            caps = pad.get_current_caps() or pad.query_caps(None)
            caps_str = caps.to_string() if caps is not None else ""
            if "media=(string)" in caps_str and "media=(string)video" not in caps_str:
                logger.debug("Ignoring non-video rtspsrc pad %s (%s)", pad.get_name(), caps_str)
                return
            result = pad.link(sink_pad)
            if result == Gst.PadLinkReturn.OK:
                logger.info("Linked rtspsrc pad %s -> decodebin", pad.get_name())
            else:
                logger.error("Failed to link rtspsrc pad %s: %s", pad.get_name(), result)

        rtspsrc.connect("pad-added", on_rtspsrc_pad_added)

        if not post_queue.link(convert):
            raise RuntimeError("Failed to link post_queue -> videoconvert")

        def on_decodebin_pad_added(element: Any, pad: Any) -> None:
            sink_pad = post_queue.get_static_pad("sink")
            if sink_pad is None or sink_pad.is_linked():
                return
            result = pad.link(sink_pad)
            if result == Gst.PadLinkReturn.OK:
                logger.info("Linked decodebin pad %s -> post_queue", pad.get_name())
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
                logger.info("RTSP pipeline using: %s (%s)", name, klass)
                for prop, value in (("max-threads", 2), ("output-corrupt", False)):
                    try:
                        element.set_property(prop, value)
                        logger.info("  %s: %s=%s", name, prop, value)
                    except TypeError:
                        pass

        decodebin.connect("pad-added", on_decodebin_pad_added)
        decodebin.connect("element-added", on_element_added)
        build_overlay_tail(pipeline, convert, sink)
        return pipeline

    # -- Lifecycle hooks ------------------------------------------------------

    def on_bus_async_done(self, pipeline: Any) -> None:
        """RTSP: force zero latency for minimal delay."""
        pipeline.set_latency(0)
        logger.info("Pipeline ASYNC_DONE (RTSP) -- latency forced to 0")

    def observe_progress(self, pipeline: Any, report: PhaseReporter) -> None:
        """Report the SDP: the server answered and described its media."""
        rtspsrc = pipeline.get_by_name("rtspsrc")
        if rtspsrc is None:
            return

        def _on_sdp(_element: Any, _sdp: Any) -> None:
            report(ConnectionPhase.TRANSPORT_UP)
            report(ConnectionPhase.STREAM_DESCRIBED)

        try:
            rtspsrc.connect("on-sdp", _on_sdp)
        except Exception:
            logger.debug("rtspsrc has no on-sdp signal on this GStreamer build", exc_info=True)

    # -- Web UI ---------------------------------------------------------------

    @classmethod
    def web_ui_html(cls, config: dict[str, Any]) -> str:
        rtsp_url = cls._esc(config.get("rtsp_url", "rtsp://0.0.0.0:554/stream"))
        rtsp_user = cls._esc(config.get("rtsp_user", ""))
        rtsp_password = cls._esc(config.get("rtsp_password", ""))
        return (
            '<div class="row">'
            '    <div class="field wide">'
            "        <label>RTSP URL</label>"
            f'        <input type="text" name="rtsp_url" value="{rtsp_url}"'
            '               placeholder="rtsp://192.168.0.182:554/stream">'
            "    </div>"
            "</div>"
            '<div class="row">'
            '    <div class="field">'
            "        <label>Username (optional)</label>"
            f'        <input type="text" name="rtsp_user" value="{rtsp_user}"'
            '               autocomplete="off">'
            "    </div>"
            '    <div class="field">'
            "        <label>Password (optional)</label>"
            f'        <input type="password" name="rtsp_password" value="{rtsp_password}"'
            '               autocomplete="off">'
            "    </div>"
            "</div>"
        )

    # -- Config ---------------------------------------------------------------

    @classmethod
    def source_endpoint(cls, config: dict[str, Any]) -> SourceEndpoint | None:
        return _endpoint_from_url(str(config.get("rtsp_url", "") or ""), scheme="rtsp", default_port=554)

    @classmethod
    def get_source_label(cls, config: dict[str, Any]) -> str:
        label = config.get("rtsp_url", "rtsp://0.0.0.0:554/stream")
        return redact_uri(str(label)) if label else "rtsp://0.0.0.0:554/stream"
