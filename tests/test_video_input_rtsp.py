# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for the RTSP video input plugin.

Covers ``create_pipeline`` topology (rtspsrc → decodebin → queue →
videoconvert), the pad-added callbacks for both ``rtspsrc`` and
``decodebin``, the hardware-decoder preference hook, and the zero-latency
``on_bus_async_done`` behaviour.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from openfollow.video.inputs.rtsp import RtspInput
from tests._fake_gst import FakeElement, FakePad, FakePipeline, make_fake_gst

pytestmark = pytest.mark.unit


class _RecordingFactory:
    def __init__(self, name: str) -> None:
        self.name = name
        self.ranks: list[int] = []

    def set_rank(self, value: int) -> None:
        self.ranks.append(value)


class TestPreferHardwareDecoders:
    def test_bumps_v4l2_and_demotes_openh264(self) -> None:
        from gi.repository import Gst  # noqa: F401

        v4l2 = _RecordingFactory("v4l2h264dec")
        openh264 = _RecordingFactory("openh264dec")
        fake = make_fake_gst(
            known_factories={"v4l2h264dec": v4l2, "openh264dec": openh264},
        )
        with patch("gi.repository.Gst", fake):
            RtspInput._prefer_hardware_decoders()

        assert v4l2.ranks == [fake.Rank.PRIMARY + 1]
        assert openh264.ranks == [fake.Rank.MARGINAL]


class TestCreatePipeline:
    def _build(self, *, fake=None, config=None) -> FakePipeline:
        from gi.repository import Gst  # noqa: F401

        fake = fake or make_fake_gst()
        sink = FakeElement("shared_videosink")
        with patch("gi.repository.Gst", fake):
            return RtspInput().create_pipeline(
                config=config or {},
                sink=sink,
                build_overlay_tail=lambda *a: None,
                prepare_sink=lambda: sink,
            )

    def test_rtspsrc_configured_with_zero_latency_and_all_transports(self) -> None:
        pipeline = self._build(config={"rtsp_url": "rtsp://host:554/stream"})
        rtspsrc = pipeline.get_by_name("rtspsrc")
        assert rtspsrc is not None
        assert rtspsrc.properties["location"] == "rtsp://host:554/stream"
        assert rtspsrc.properties["latency"] == 0
        assert rtspsrc.properties["drop-on-latency"] is True
        assert rtspsrc.properties["buffer-mode"] == 0
        # tcp+udp+multicast = 0b111 = 7
        assert rtspsrc.properties["protocols"] == 0x7

    def test_missing_rtspsrc_raises(self) -> None:
        fake = make_fake_gst(missing_elements={"rtspsrc"})
        with pytest.raises(RuntimeError, match="rtspsrc"):
            self._build(fake=fake)

    def test_prepare_sink_none_raises(self) -> None:
        from gi.repository import Gst  # noqa: F401

        with patch("gi.repository.Gst", make_fake_gst()):
            with pytest.raises(RuntimeError, match="No video sink"):
                RtspInput().create_pipeline(
                    config={},
                    sink=FakeElement("shared_videosink"),
                    build_overlay_tail=lambda *a: None,
                    prepare_sink=lambda: None,
                )

    def test_post_queue_is_leaky(self) -> None:
        pipeline = self._build()
        post = pipeline.get_by_name("post_queue")
        assert post is not None
        assert post.properties["leaky"] == 2


class TestRtspsrcPadAdded:
    def test_links_rtspsrc_dynamic_pad_into_decodebin(self) -> None:
        from gi.repository import Gst  # noqa: F401

        fake = make_fake_gst()
        sink = FakeElement("shared_videosink")
        with patch("gi.repository.Gst", fake):
            pipeline = RtspInput().create_pipeline(
                config={},
                sink=sink,
                build_overlay_tail=lambda *a: None,
                prepare_sink=lambda: sink,
            )
        rtspsrc = pipeline.get_by_name("rtspsrc")
        decodebin = pipeline.get_by_name("decodebin")
        pad_cb = next(cb for sig, cb in rtspsrc.signals if sig == "pad-added")
        pad = FakePad("recv_rtp_src_0")
        pad_cb(rtspsrc, pad)
        assert pad.linked_to is decodebin.get_static_pad("sink")

    def test_second_pad_added_is_ignored_once_decodebin_is_linked(self) -> None:
        from gi.repository import Gst  # noqa: F401

        fake = make_fake_gst()
        sink = FakeElement("shared_videosink")
        with patch("gi.repository.Gst", fake):
            pipeline = RtspInput().create_pipeline(
                config={},
                sink=sink,
                build_overlay_tail=lambda *a: None,
                prepare_sink=lambda: sink,
            )
        rtspsrc = pipeline.get_by_name("rtspsrc")
        pad_cb = next(cb for sig, cb in rtspsrc.signals if sig == "pad-added")

        pad1 = FakePad("recv_rtp_src_0")
        pad_cb(rtspsrc, pad1)
        pad2 = FakePad("recv_rtp_src_1")
        pad_cb(rtspsrc, pad2)
        # second pad must not link
        assert pad2.linked_to is None


class TestRtspsrcPadAddedFailureArms:
    """The rtspsrc pad-added callback has three exits: skip when sink_pad
    is missing, skip when already linked, log-error when link returns
    non-OK. The existing tests cover the success and second-pad arms;
    this fills the failure arms."""

    def _get_pad_cb(self):
        from gi.repository import Gst  # noqa: F401

        fake = make_fake_gst()
        sink = FakeElement("shared_videosink")
        with patch("gi.repository.Gst", fake):
            pipeline = RtspInput().create_pipeline(
                config={},
                sink=sink,
                build_overlay_tail=lambda *a: None,
                prepare_sink=lambda: sink,
            )
        rtspsrc = pipeline.get_by_name("rtspsrc")
        decodebin = pipeline.get_by_name("decodebin")
        pad_cb = next(cb for sig, cb in rtspsrc.signals if sig == "pad-added")
        return pipeline, rtspsrc, decodebin, pad_cb

    def test_pad_added_skips_when_decodebin_static_sink_missing(self) -> None:
        _pipeline, rtspsrc, decodebin, pad_cb = self._get_pad_cb()
        decodebin.get_static_pad = lambda _name: None  # type: ignore[method-assign]
        pad = FakePad("recv_rtp_src_0")
        pad_cb(rtspsrc, pad)
        assert pad.linked_to is None

    def test_pad_added_logs_error_when_link_fails(self) -> None:
        _pipeline, rtspsrc, _decodebin, pad_cb = self._get_pad_cb()
        pad = FakePad("recv_rtp_src_0", link_returns="some_error")
        # Must not raise – error arm fires.
        pad_cb(rtspsrc, pad)


class TestRtspsrcPadAddedMediaFilter:
    """#558: link only the video RTP track. rtspsrc exposes one pad per SDP
    media track, so an audio-first SDP must not steal the single decodebin
    sink and drop video entirely."""

    def _get_pad_cb(self):
        from gi.repository import Gst  # noqa: F401

        fake = make_fake_gst()
        sink = FakeElement("shared_videosink")
        with patch("gi.repository.Gst", fake):
            pipeline = RtspInput().create_pipeline(
                config={},
                sink=sink,
                build_overlay_tail=lambda *a: None,
                prepare_sink=lambda: sink,
            )
        rtspsrc = pipeline.get_by_name("rtspsrc")
        decodebin = pipeline.get_by_name("decodebin")
        pad_cb = next(cb for sig, cb in rtspsrc.signals if sig == "pad-added")
        return decodebin, rtspsrc, pad_cb

    @staticmethod
    def _caps(value: str):
        return type("C", (), {"to_string": staticmethod(lambda: value)})()

    def test_audio_first_then_video_links_only_video(self) -> None:
        decodebin, rtspsrc, pad_cb = self._get_pad_cb()
        audio = FakePad("recv_rtp_src_0")
        audio.get_current_caps = lambda: self._caps("application/x-rtp, media=(string)audio, payload=(int)97")
        pad_cb(rtspsrc, audio)
        video = FakePad("recv_rtp_src_1")
        video.get_current_caps = lambda: self._caps("application/x-rtp, media=(string)video, payload=(int)96")
        pad_cb(rtspsrc, video)
        assert audio.linked_to is None  # audio skipped – sink stays free
        assert video.linked_to is decodebin.get_static_pad("sink")

    def test_unreadable_caps_pad_is_linked(self) -> None:
        # Caps not yet negotiable (no media field) → link rather than stall.
        decodebin, rtspsrc, pad_cb = self._get_pad_cb()
        pad = FakePad("recv_rtp_src_0")
        pad.get_current_caps = lambda: None
        pad.query_caps = lambda _f: None
        pad_cb(rtspsrc, pad)
        assert pad.linked_to is decodebin.get_static_pad("sink")


class TestDecodebinPadAddedFailureArms:
    """The decodebin pad-added callback's failure arms: missing sink
    pad, already-linked sink pad, NOFORMAT/REFUSED tolerance, and the
    error arm for any other link result."""

    def _get_decodebin_pad_cb(self):
        from gi.repository import Gst  # noqa: F401

        fake = make_fake_gst()
        sink = FakeElement("shared_videosink")
        with patch("gi.repository.Gst", fake):
            pipeline = RtspInput().create_pipeline(
                config={},
                sink=sink,
                build_overlay_tail=lambda *a: None,
                prepare_sink=lambda: sink,
            )
        decodebin = pipeline.get_by_name("decodebin")
        pad_cb = next(cb for sig, cb in decodebin.signals if sig == "pad-added")
        return pipeline, decodebin, pad_cb

    def test_pad_added_skips_when_post_queue_static_sink_missing(self) -> None:
        pipeline, decodebin, pad_cb = self._get_decodebin_pad_cb()
        post_queue = pipeline.get_by_name("post_queue")
        post_queue.get_static_pad = lambda _name: None  # type: ignore[method-assign]
        pad = FakePad("src_0")
        pad_cb(decodebin, pad)
        assert pad.linked_to is None

    def test_pad_added_skips_when_post_queue_already_linked(self) -> None:
        pipeline, decodebin, pad_cb = self._get_decodebin_pad_cb()
        post_queue = pipeline.get_by_name("post_queue")
        existing = FakePad("existing")
        existing.link(post_queue.get_static_pad("sink"))
        pad = FakePad("src_1")
        pad_cb(decodebin, pad)
        assert pad.linked_to is None

    def test_pad_added_link_success_logs_info(self) -> None:
        pipeline, decodebin, pad_cb = self._get_decodebin_pad_cb()
        pad = FakePad("src_2")
        pad_cb(decodebin, pad)
        assert pad.linked_to is pipeline.get_by_name("post_queue").get_static_pad("sink")

    def test_pad_added_noformat_swallowed(self) -> None:
        """NOFORMAT / REFUSED are tolerated silently – they fall outside
        the elif's "log error" path."""
        pipeline, decodebin, pad_cb = self._get_decodebin_pad_cb()
        pad = FakePad("src_nf", link_returns="noformat")
        pad_cb(decodebin, pad)  # must not raise; no error log

    def test_pad_added_other_failure_logs_error(self) -> None:
        pipeline, decodebin, pad_cb = self._get_decodebin_pad_cb()
        pad = FakePad("src_err", link_returns="some_other_failure")
        pad_cb(decodebin, pad)


class TestRtspElementAddedFailureArms:
    """The element-added callback covers: factory=None early-return,
    decoder-with-set_property-TypeError swallow, and unknown-klass
    fall-through (no decoder branch)."""

    def _get_element_cb(self):
        from gi.repository import Gst  # noqa: F401

        fake = make_fake_gst()
        sink = FakeElement("shared_videosink")
        with patch("gi.repository.Gst", fake):
            pipeline = RtspInput().create_pipeline(
                config={},
                sink=sink,
                build_overlay_tail=lambda *a: None,
                prepare_sink=lambda: sink,
            )
        decodebin = pipeline.get_by_name("decodebin")
        element_cb = next(cb for sig, cb in decodebin.signals if sig == "element-added")
        return decodebin, element_cb

    def test_element_added_factory_none_returns_early(self) -> None:
        decodebin, element_cb = self._get_element_cb()
        elem = FakeElement("weird")
        elem.get_factory = lambda: None  # type: ignore[method-assign]
        element_cb(decodebin, elem)  # must not raise

    def test_element_added_unknown_klass_skips_decoder_props(self) -> None:
        decodebin, element_cb = self._get_element_cb()
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

    def test_element_added_decoder_swallows_typeerror(self) -> None:
        decodebin, element_cb = self._get_element_cb()
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


class TestRtspCreatePipelineLinkFailures:
    def test_post_queue_link_failure_raises(self) -> None:
        from gi.repository import Gst  # noqa: F401

        # Only one queue is created (post_queue), so failing all queue
        # kinds breaks exactly that link.
        fake = make_fake_gst(link_fail_kinds={"queue"})
        sink = FakeElement("shared_videosink")
        with patch("gi.repository.Gst", fake):
            with pytest.raises(RuntimeError, match="post_queue"):
                RtspInput().create_pipeline(
                    config={},
                    sink=sink,
                    build_overlay_tail=lambda *a: None,
                    prepare_sink=lambda: sink,
                )


class TestDecodebinElementAdded:
    def test_sets_decoder_properties(self) -> None:
        from gi.repository import Gst  # noqa: F401

        fake = make_fake_gst()
        sink = FakeElement("shared_videosink")
        with patch("gi.repository.Gst", fake):
            pipeline = RtspInput().create_pipeline(
                config={},
                sink=sink,
                build_overlay_tail=lambda *a: None,
                prepare_sink=lambda: sink,
            )
        decodebin = pipeline.get_by_name("decodebin")
        element_cb = next(cb for sig, cb in decodebin.signals if sig == "element-added")

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

        assert dec.properties.get("max-threads") == 2
        assert dec.properties.get("output-corrupt") is False


class TestOnBusAsyncDone:
    def test_forces_zero_latency(self) -> None:
        pipeline = FakePipeline("rtsp")
        RtspInput().on_bus_async_done(pipeline)
        assert pipeline.latency_values == [0]


class TestGetSourceLabel:
    def test_label_returns_url(self) -> None:
        assert RtspInput.get_source_label({"rtsp_url": "rtsp://cam/stream"}) == "rtsp://cam/stream"
