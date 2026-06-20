# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for the RTP video input plugin.

Covers ``_parse_rtp_url`` classification, ``_prefer_hardware_decoders``,
``create_pipeline`` in both multicast and unicast modes, jitterbuffer
absence, and the decoder/depayloader/parser ``element-added`` hook.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from openfollow.video.inputs.rtp import RtpInput, _parse_rtp_url
from tests._fake_gst import FakeElement, FakePad, FakePipeline, make_fake_gst

pytestmark = pytest.mark.unit

# --------------------------------------------------------------------------- #
# _parse_rtp_url
# --------------------------------------------------------------------------- #


class TestParseRtpUrl:
    def test_empty_yields_listen_all(self) -> None:
        assert _parse_rtp_url("") == ("0.0.0.0", 5004, False)

    def test_unicast_without_scheme(self) -> None:
        assert _parse_rtp_url("10.0.0.5:5006") == ("10.0.0.5", 5006, False)

    def test_multicast_within_range_detected(self) -> None:
        assert _parse_rtp_url("rtp://232.0.0.1:4000") == ("232.0.0.1", 4000, True)

    def test_multicast_at_lower_boundary(self) -> None:
        assert _parse_rtp_url("rtp://224.0.0.1:5004") == ("224.0.0.1", 5004, True)

    def test_multicast_at_upper_boundary(self) -> None:
        assert _parse_rtp_url("rtp://239.255.255.255:5004") == (
            "239.255.255.255",
            5004,
            True,
        )

    def test_above_multicast_range_is_unicast(self) -> None:
        assert _parse_rtp_url("rtp://240.0.0.1:5004") == ("240.0.0.1", 5004, False)

    def test_below_multicast_range_is_unicast(self) -> None:
        assert _parse_rtp_url("rtp://192.168.1.1:5004") == (
            "192.168.1.1",
            5004,
            False,
        )

    def test_hostname_without_numeric_octet_falls_back_to_unicast(self) -> None:
        host, port, is_multicast = _parse_rtp_url("rtp://mcast.local:5004")
        assert host == "mcast.local"
        assert port == 5004
        assert is_multicast is False


# --------------------------------------------------------------------------- #
# _prefer_hardware_decoders (same as SRT/RTSP but keep explicit coverage)
# --------------------------------------------------------------------------- #


class _RecordingFactory:
    def __init__(self, name: str) -> None:
        self.name = name
        self.ranks: list[int] = []

    def set_rank(self, value: int) -> None:
        self.ranks.append(value)


class TestPreferHardwareDecoders:
    def test_bumps_v4l2_ranks(self) -> None:
        from gi.repository import Gst  # noqa: F401

        v4l2h264 = _RecordingFactory("v4l2h264dec")
        v4l2h265 = _RecordingFactory("v4l2h265dec")
        openh264 = _RecordingFactory("openh264dec")
        fake = make_fake_gst(
            known_factories={
                "v4l2h264dec": v4l2h264,
                "v4l2h265dec": v4l2h265,
                "openh264dec": openh264,
            },
        )
        with patch("gi.repository.Gst", fake):
            RtpInput._prefer_hardware_decoders()
        assert v4l2h264.ranks == [fake.Rank.PRIMARY + 1]
        assert openh264.ranks == [fake.Rank.MARGINAL]


# --------------------------------------------------------------------------- #
# create_pipeline
# --------------------------------------------------------------------------- #


def _build(config=None, *, fake=None) -> FakePipeline:
    from gi.repository import Gst  # noqa: F401

    fake = fake or make_fake_gst()
    sink = FakeElement("shared_videosink")
    with patch("gi.repository.Gst", fake):
        pipeline = RtpInput().create_pipeline(
            config=config or {},
            sink=sink,
            build_overlay_tail=lambda *a: None,
            prepare_sink=lambda: sink,
        )
    return pipeline


class TestCreatePipelineUnicast:
    def test_configures_udpsrc_for_unicast_url(self) -> None:
        pipeline = _build({"rtp_url": "rtp://10.0.0.5:5006", "rtp_encoding": "H264"})
        udpsrc = pipeline.get_by_name("udpsrc")
        assert udpsrc is not None
        assert udpsrc.properties["address"] == "10.0.0.5"
        assert udpsrc.properties["port"] == 5006
        assert "multicast-group" not in udpsrc.properties
        # Caps string encodes chosen encoding / clock rate
        caps = udpsrc.properties["caps"]
        assert "encoding-name=(string)H264" in str(caps.to_string())
        assert "clock-rate=(int)90000" in str(caps.to_string())


class TestCreatePipelineMulticast:
    def test_sets_multicast_group_and_auto_multicast(self) -> None:
        pipeline = _build({"rtp_url": "rtp://232.255.255.255:4000"})
        udpsrc = pipeline.get_by_name("udpsrc")
        assert udpsrc is not None
        assert udpsrc.properties["multicast-group"] == "232.255.255.255"
        assert udpsrc.properties["auto-multicast"] is True
        assert udpsrc.properties["port"] == 4000
        assert "address" not in udpsrc.properties


class TestCreatePipelineJitterbuffer:
    def test_configures_jitterbuffer_when_available(self) -> None:
        pipeline = _build()
        jitter = pipeline.get_by_name("jitterbuf")
        assert jitter is not None
        assert jitter.properties["latency"] == 0
        assert jitter.properties["drop-on-latency"] is True

    def test_skips_jitterbuffer_when_missing(self) -> None:
        fake = make_fake_gst(missing_elements={"rtpjitterbuffer"})
        pipeline = _build(fake=fake)
        # If jitterbuf is missing the element is never added
        assert pipeline.get_by_name("jitterbuf") is None
        # udpsrc → pre_queue still present
        assert pipeline.get_by_name("pre_queue") is not None


class TestCreatePipelineFailures:
    def test_missing_udpsrc_raises(self) -> None:
        fake = make_fake_gst(missing_elements={"udpsrc"})
        with pytest.raises(RuntimeError, match="udpsrc"):
            _build(fake=fake)

    def test_prepare_sink_none_raises(self) -> None:
        from gi.repository import Gst  # noqa: F401

        with patch("gi.repository.Gst", make_fake_gst()):
            with pytest.raises(RuntimeError, match="No video sink"):
                RtpInput().create_pipeline(
                    config={},
                    sink=FakeElement("shared_videosink"),
                    build_overlay_tail=lambda *a: None,
                    prepare_sink=lambda: None,
                )


class TestUnknownEncoding:
    def test_unknown_encoding_falls_back_to_90kHz(self) -> None:
        pipeline = _build({"rtp_encoding": "totally-made-up"})
        udpsrc = pipeline.get_by_name("udpsrc")
        caps = udpsrc.properties["caps"].to_string()
        # Clock rate defaults to 90000
        assert "clock-rate=(int)90000" in caps

    def test_caps_metacharacter_injection_is_rejected(self) -> None:
        # A crafted config (caps-injecting encoding) must not leak raw
        # metacharacters into the caps string; it falls back to H264 so
        # the embedded token is exactly the known encoding name.
        pipeline = _build({"rtp_encoding": "H264, foo=(int)1"})
        udpsrc = pipeline.get_by_name("udpsrc")
        caps = udpsrc.properties["caps"].to_string()
        assert caps.endswith("encoding-name=(string)H264")
        assert "FOO=(INT)1" not in caps
        assert "clock-rate=(int)90000" in caps

    def test_valid_encoding_passes_through(self) -> None:
        pipeline = _build({"rtp_encoding": "h265"})
        udpsrc = pipeline.get_by_name("udpsrc")
        caps = udpsrc.properties["caps"].to_string()
        assert "encoding-name=(string)H265" in caps


# --------------------------------------------------------------------------- #
# Decodebin callbacks
# --------------------------------------------------------------------------- #


class TestDecodebinCallbacks:
    def _setup(self, fake=None):
        from gi.repository import Gst  # noqa: F401

        fake = fake or make_fake_gst()
        pipeline = _build(fake=fake)
        decodebin = pipeline.get_by_name("decodebin")
        pad_cb = next(cb for sig, cb in decodebin.signals if sig == "pad-added")
        element_cb = next(cb for sig, cb in decodebin.signals if sig == "element-added")
        return pipeline, decodebin, pad_cb, element_cb

    def test_pad_added_links_into_post_queue(self) -> None:
        pipeline, decodebin, pad_cb, _ = self._setup()
        pad = FakePad("src")
        pad_cb(decodebin, pad)
        post_sink = pipeline.get_by_name("post_queue").get_static_pad("sink")
        assert pad.linked_to is post_sink

    def test_pad_added_ignores_second_pad(self) -> None:
        pipeline, decodebin, pad_cb, _ = self._setup()
        pad_cb(decodebin, FakePad("src_0"))
        pad2 = FakePad("src_1")
        pad_cb(decodebin, pad2)
        assert pad2.linked_to is None

    def test_element_added_sets_decoder_properties(self) -> None:
        _, decodebin, _, element_cb = self._setup()
        dec = FakeElement("v4l2h264dec")

        class _Factory:
            @staticmethod
            def get_metadata(key: str) -> str:
                return "Video/Decoder" if key == "klass" else ""

            @staticmethod
            def get_name() -> str:
                return "v4l2h264dec"

        dec.get_factory = lambda: _Factory()  # type: ignore[method-assign]
        element_cb(decodebin, dec)
        assert dec.properties["max-threads"] == 2
        assert dec.properties["output-corrupt"] is False

    def test_element_added_handles_parser(self) -> None:
        _, decodebin, _, element_cb = self._setup()
        parser = FakeElement("h264parse")

        class _Factory:
            @staticmethod
            def get_metadata(key: str) -> str:
                return "Codec/Parser/Converter/Video" if key == "klass" else ""

            @staticmethod
            def get_name() -> str:
                return "h264parse"

        parser.get_factory = lambda: _Factory()  # type: ignore[method-assign]
        element_cb(decodebin, parser)
        assert parser.properties["config-interval"] == -1

    def test_element_added_handles_depay(self) -> None:
        _, decodebin, _, element_cb = self._setup()
        depay = FakeElement("rtph264depay")

        class _Factory:
            @staticmethod
            def get_metadata(key: str) -> str:
                return "Codec/Depayloader" if key == "klass" else ""

            @staticmethod
            def get_name() -> str:
                return "rtph264depay"

        depay.get_factory = lambda: _Factory()  # type: ignore[method-assign]
        # No property updates – just log. Must not raise.
        element_cb(decodebin, depay)

    def test_element_added_without_factory_is_skipped(self) -> None:
        _, decodebin, _, element_cb = self._setup()
        elem = FakeElement("weird")
        elem.get_factory = lambda: None  # type: ignore[method-assign]
        element_cb(decodebin, elem)

    def test_pad_added_skips_when_post_queue_static_sink_missing(self) -> None:
        pipeline, decodebin, pad_cb, _ = self._setup()
        post_queue = pipeline.get_by_name("post_queue")
        post_queue.get_static_pad = lambda _name: None  # type: ignore[method-assign]
        pad = FakePad("src_x")
        pad_cb(decodebin, pad)
        assert pad.linked_to is None

    def test_pad_added_other_failure_logs_error(self) -> None:
        """A non-OK / non-NOFORMAT / non-REFUSED link result hits the
        error arm – covers the 228→232 elif body."""
        _, decodebin, pad_cb, _ = self._setup()
        pad = FakePad("src_err", link_returns="some_error")
        pad_cb(decodebin, pad)  # must not raise

    def test_pad_added_noformat_swallowed(self) -> None:
        _, decodebin, pad_cb, _ = self._setup()
        pad = FakePad("src_nf", link_returns="noformat")
        pad_cb(decodebin, pad)

    def test_element_added_decoder_swallows_typeerror(self) -> None:
        _, decodebin, _, element_cb = self._setup()
        dec = FakeElement("v4l2h264dec")

        def _raising(key: str, value: object) -> None:
            raise TypeError(f"no property {key}")

        dec.set_property = _raising  # type: ignore[method-assign]

        class _Factory:
            @staticmethod
            def get_metadata(key: str) -> str:
                return "Video/Decoder" if key == "klass" else ""

            @staticmethod
            def get_name() -> str:
                return "v4l2h264dec"

        dec.get_factory = lambda: _Factory()  # type: ignore[method-assign]
        element_cb(decodebin, dec)  # must not raise

    def test_element_added_parser_swallows_typeerror(self) -> None:
        """A parser without ``config-interval`` – TypeError swallowed."""
        _, decodebin, _, element_cb = self._setup()
        parser = FakeElement("legacyparse")

        def _raising(key: str, value: object) -> None:
            raise TypeError(f"no property {key}")

        parser.set_property = _raising  # type: ignore[method-assign]

        class _Factory:
            @staticmethod
            def get_metadata(key: str) -> str:
                return "Codec/Parser/Converter/Video" if key == "klass" else ""

            @staticmethod
            def get_name() -> str:
                return "legacyparse"

        parser.get_factory = lambda: _Factory()  # type: ignore[method-assign]
        element_cb(decodebin, parser)  # must not raise

    def test_element_added_unknown_klass_falls_through(self) -> None:
        """An element that's neither decoder/depay/parser falls through
        all three arms silently – covers the 253→exit partial branch."""
        _, decodebin, _, element_cb = self._setup()
        elem = FakeElement("typefind")

        class _Factory:
            @staticmethod
            def get_metadata(key: str) -> str:
                return "Generic" if key == "klass" else ""

            @staticmethod
            def get_name() -> str:
                return "typefind"

        elem.get_factory = lambda: _Factory()  # type: ignore[method-assign]
        element_cb(decodebin, elem)
        assert "max-threads" not in elem.properties
        assert "config-interval" not in elem.properties


class TestRtpCreatePipelineLinkFailures:
    """Each link in the RTP chain has its own RuntimeError arm."""

    def _build_with(self, link_fail_kinds: set[str], **extra) -> None:
        from gi.repository import Gst  # noqa: F401

        fake = make_fake_gst(link_fail_kinds=link_fail_kinds, **extra)
        sink = FakeElement("shared_videosink")
        with patch("gi.repository.Gst", fake):
            RtpInput().create_pipeline(
                config={},
                sink=sink,
                build_overlay_tail=lambda *a: None,
                prepare_sink=lambda: sink,
            )

    def test_udpsrc_link_failure_raises(self) -> None:
        with pytest.raises(RuntimeError, match="udpsrc"):
            self._build_with({"udpsrc"})

    def test_pre_queue_to_jitterbuf_link_failure_raises(self) -> None:
        # Failing all queue kinds breaks pre_queue → jitterbuf first.
        with pytest.raises(RuntimeError, match="rtpjitterbuffer"):
            self._build_with({"queue"})

    def test_pre_decode_to_decodebin_link_failure_raises(self) -> None:
        # Without jitterbuf in the chain, pre_queue links directly to
        # decodebin. Failing rtpjitterbuffer's element factory drops it
        # from the chain; failing all queues then targets pre_queue →
        # decodebin specifically.
        from gi.repository import Gst  # noqa: F401

        fake = make_fake_gst(
            missing_elements={"rtpjitterbuffer"},
            link_fail_kinds={"queue"},
        )
        sink = FakeElement("shared_videosink")
        with patch("gi.repository.Gst", fake):
            with pytest.raises(RuntimeError, match="decodebin"):
                RtpInput().create_pipeline(
                    config={},
                    sink=sink,
                    build_overlay_tail=lambda *a: None,
                    prepare_sink=lambda: sink,
                )

    def test_post_queue_link_failure_raises(self) -> None:
        # Make only the post_queue (second queue created) fail.
        from gi.repository import Gst  # noqa: F401

        fake = make_fake_gst()
        original_make = fake.ElementFactory.make
        queue_count = {"n": 0}

        def _make(kind: str, name: str):
            elem = original_make(kind, name)
            if kind == "queue":
                queue_count["n"] += 1
                if queue_count["n"] == 2:
                    elem._link_ok = False
            return elem

        fake.ElementFactory.make = staticmethod(_make)  # type: ignore[assignment]
        sink = FakeElement("shared_videosink")
        with patch("gi.repository.Gst", fake):
            with pytest.raises(RuntimeError, match="post_queue"):
                RtpInput().create_pipeline(
                    config={},
                    sink=sink,
                    build_overlay_tail=lambda *a: None,
                    prepare_sink=lambda: sink,
                )


# --------------------------------------------------------------------------- #
# Lifecycle + labels
# --------------------------------------------------------------------------- #


class TestOnBusAsyncDone:
    def test_forces_zero_latency(self) -> None:
        pipeline = FakePipeline("rtp")
        RtpInput().on_bus_async_done(pipeline)
        assert pipeline.latency_values == [0]


class TestGetSourceLabel:
    def test_multicast_label(self) -> None:
        label = RtpInput.get_source_label(
            {"rtp_url": "rtp://232.0.0.1:4000", "rtp_encoding": "H265"},
        )
        assert "mcast" in label
        assert "H265" in label
        assert "232.0.0.1:4000" in label

    def test_unicast_label(self) -> None:
        label = RtpInput.get_source_label({"rtp_url": "10.0.0.5:5006"})
        assert "unicast" in label
        assert "H264" in label
