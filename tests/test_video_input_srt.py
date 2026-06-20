# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for the SRT video input plugin.

Covers:
* ``_resolve_srt_uri`` shorthand / full-URI normalisation,
* ``_prefer_hardware_decoders`` rank bumps,
* ``create_pipeline`` topology and failure branches,
* pad-added / element-added runtime callbacks,
* the no-op ``on_bus_async_done`` hook,
* ``get_source_label`` formatting.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from openfollow.video.inputs.srt import SrtInput, _resolve_srt_uri
from tests._fake_gst import FakeElement, FakePad, FakePipeline, make_fake_gst

pytestmark = pytest.mark.unit

# --------------------------------------------------------------------------- #
# _resolve_srt_uri
# --------------------------------------------------------------------------- #


class TestResolveSrtUri:
    def test_empty_string_yields_wildcard_listener(self) -> None:
        assert _resolve_srt_uri("") == "srt://0.0.0.0:5000"

    def test_whitespace_treated_as_empty(self) -> None:
        assert _resolve_srt_uri("   ") == "srt://0.0.0.0:5000"

    def test_host_port_shorthand_is_prefixed(self) -> None:
        assert _resolve_srt_uri("192.168.0.5:1600") == "srt://192.168.0.5:1600"

    def test_full_uri_passes_through(self) -> None:
        assert _resolve_srt_uri("srt://10.0.0.1:5000?streamid=r=0") == "srt://10.0.0.1:5000?streamid=r=0"

    def test_uppercase_scheme_accepted(self) -> None:
        assert _resolve_srt_uri("SRT://host:1") == "SRT://host:1"


# --------------------------------------------------------------------------- #
# _prefer_hardware_decoders
# --------------------------------------------------------------------------- #


class _RecordingFactory:
    def __init__(self, name: str) -> None:
        self.name = name
        self.ranks: list[int] = []

    def set_rank(self, value: int) -> None:
        self.ranks.append(value)


class TestPreferHardwareDecoders:
    def test_bumps_v4l2_ranks_and_demotes_openh264(self) -> None:
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
            SrtInput._prefer_hardware_decoders()

        assert v4l2h264.ranks == [fake.Rank.PRIMARY + 1]
        assert v4l2h265.ranks == [fake.Rank.PRIMARY + 1]
        assert openh264.ranks == [fake.Rank.MARGINAL]

    def test_missing_factories_is_tolerated(self) -> None:
        from gi.repository import Gst  # noqa: F401

        fake = make_fake_gst()
        with patch("gi.repository.Gst", fake):
            # Must not raise
            SrtInput._prefer_hardware_decoders()


# --------------------------------------------------------------------------- #
# create_pipeline
# --------------------------------------------------------------------------- #


class _OverlayCapture:
    def __init__(self) -> None:
        self.calls: list[tuple[object, object, object]] = []

    def __call__(self, pipeline, head, sink) -> None:
        self.calls.append((pipeline, head, sink))


class TestCreatePipeline:
    def _build(self, config=None, *, fake=None) -> FakePipeline:
        from gi.repository import Gst  # noqa: F401

        sink = FakeElement("shared_videosink")
        fake = fake or make_fake_gst()
        overlay = _OverlayCapture()
        with patch("gi.repository.Gst", fake):
            pipeline = SrtInput().create_pipeline(
                config=config or {},
                sink=sink,
                build_overlay_tail=overlay,
                prepare_sink=lambda: sink,
            )
        assert isinstance(pipeline, FakePipeline)
        assert overlay.calls, "build_overlay_tail was never called"
        return pipeline

    def test_builds_srt_chain_with_expected_elements(self) -> None:
        pipeline = self._build({"srt_host": "10.0.0.5:5000"})
        srtsrc = pipeline.get_by_name("srtsrc")
        assert srtsrc is not None
        assert srtsrc.properties["uri"] == "srt://10.0.0.5:5000"
        assert srtsrc.properties["mode"] == "caller"
        assert srtsrc.properties["wait-for-connection"] is True
        assert srtsrc.properties["latency"] == 125

        assert pipeline.get_by_name("pre_queue") is not None
        assert pipeline.get_by_name("decodebin") is not None
        assert pipeline.get_by_name("post_queue") is not None
        assert pipeline.get_by_name("convert") is not None

    def test_queues_are_leaky_downstream(self) -> None:
        pipeline = self._build()
        for name in ("pre_queue", "post_queue"):
            q = pipeline.get_by_name(name)
            assert q is not None
            assert q.properties["leaky"] == 2

    def test_missing_srtsrc_raises_runtime_error(self) -> None:
        fake = make_fake_gst(missing_elements={"srtsrc"})
        with pytest.raises(RuntimeError, match="srtsrc"):
            self._build(fake=fake)

    def test_prepare_sink_none_raises_runtime_error(self) -> None:
        from gi.repository import Gst  # noqa: F401

        fake = make_fake_gst()
        overlay = _OverlayCapture()
        sink = FakeElement("shared_videosink")
        with patch("gi.repository.Gst", fake):
            with pytest.raises(RuntimeError, match="No video sink"):
                SrtInput().create_pipeline(
                    config={},
                    sink=sink,
                    build_overlay_tail=overlay,
                    prepare_sink=lambda: None,
                )


class TestDecodebinCallbacks:
    def _build_and_get_pad_added_cb(self, fake):
        from gi.repository import Gst  # noqa: F401

        sink = FakeElement("shared_videosink")
        with patch("gi.repository.Gst", fake):
            pipeline = SrtInput().create_pipeline(
                config={},
                sink=sink,
                build_overlay_tail=lambda *a: None,
                prepare_sink=lambda: sink,
            )
        decodebin = pipeline.get_by_name("decodebin")
        assert decodebin is not None
        pad_cb = next(cb for sig, cb in decodebin.signals if sig == "pad-added")
        element_cb = next(cb for sig, cb in decodebin.signals if sig == "element-added")
        return pipeline, decodebin, pad_cb, element_cb

    def test_pad_added_links_video_pad_into_post_queue(self) -> None:
        fake = make_fake_gst()
        pipeline, decodebin, pad_cb, _ = self._build_and_get_pad_added_cb(fake)
        pad = FakePad("src_0")
        # Make the pad report a video caps string
        pad.get_current_caps = lambda: type("C", (), {"to_string": staticmethod(lambda: "video/x-raw")})()
        pad_cb(decodebin, pad)
        post_queue_sink = pipeline.get_by_name("post_queue").get_static_pad("sink")
        assert pad.linked_to is post_queue_sink

    def test_pad_added_ignores_non_video_caps(self) -> None:
        fake = make_fake_gst()
        pipeline, decodebin, pad_cb, _ = self._build_and_get_pad_added_cb(fake)
        pad = FakePad("src_1")
        pad.get_current_caps = lambda: type("C", (), {"to_string": staticmethod(lambda: "audio/x-raw")})()
        pad_cb(decodebin, pad)
        assert pad.linked_to is None

    def test_element_added_logs_decoder_and_sets_properties(self) -> None:
        fake = make_fake_gst()
        pipeline, decodebin, _, element_cb = self._build_and_get_pad_added_cb(fake)
        # Build a decoder with a recording factory
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
        # The plugin should push max-threads and output-corrupt properties
        assert dec.properties.get("max-threads") == 2
        assert dec.properties.get("output-corrupt") is True

    def test_element_added_without_factory_is_skipped(self) -> None:
        fake = make_fake_gst()
        pipeline, decodebin, _, element_cb = self._build_and_get_pad_added_cb(fake)
        elem = FakeElement("weird")
        elem.get_factory = lambda: None  # type: ignore[method-assign]
        # Must not raise
        element_cb(decodebin, elem)

    def test_pad_added_without_static_pad_logs_and_returns(self) -> None:
        fake = make_fake_gst()
        pipeline, decodebin, pad_cb, _ = self._build_and_get_pad_added_cb(fake)
        post_queue = pipeline.get_by_name("post_queue")
        post_queue.get_static_pad = lambda _name: None  # type: ignore[method-assign]
        pad = FakePad("src_x")
        pad_cb(decodebin, pad)
        assert pad.linked_to is None

    def test_pad_added_skips_when_sink_pad_already_linked(self) -> None:
        """If decodebin produces a second video pad after the first has
        already linked, the callback drops it silently."""
        fake = make_fake_gst()
        pipeline, decodebin, pad_cb, _ = self._build_and_get_pad_added_cb(fake)
        post_queue = pipeline.get_by_name("post_queue")
        sink_pad = post_queue.get_static_pad("sink")
        # Pre-mark the sink pad as already linked.
        existing = FakePad("existing")
        existing.link(sink_pad)
        pad = FakePad("src_2")
        pad_cb(decodebin, pad)
        assert pad.linked_to is None

    def test_pad_added_logs_warning_for_noformat_link_result(self) -> None:
        """NOFORMAT/REFUSED is a recoverable mismatch – log a warning
        rather than the generic error path."""
        fake = make_fake_gst()
        pipeline, decodebin, pad_cb, _ = self._build_and_get_pad_added_cb(fake)
        pad = FakePad("src_nf", link_returns="noformat")
        # Caps must look like video for the callback to attempt the link.
        pad.get_current_caps = lambda: type("C", (), {"to_string": staticmethod(lambda: "video/x-raw")})()
        # Returns NOFORMAT – must not raise; the warning arm fires.
        pad_cb(decodebin, pad)

    def test_pad_added_logs_error_for_other_link_failure(self) -> None:
        """Any non-OK / non-NOFORMAT result hits the error arm."""
        fake = make_fake_gst()
        pipeline, decodebin, pad_cb, _ = self._build_and_get_pad_added_cb(fake)
        pad = FakePad("src_err", link_returns="some_other_error")
        pad.get_current_caps = lambda: type("C", (), {"to_string": staticmethod(lambda: "video/x-raw")})()
        pad_cb(decodebin, pad)

    def test_element_added_decoder_swallows_set_property_typeerror(self) -> None:
        fake = make_fake_gst()
        pipeline, decodebin, _, element_cb = self._build_and_get_pad_added_cb(fake)
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
        # Must not raise
        element_cb(decodebin, dec)

    def test_element_added_unknown_klass_silently_skips(self) -> None:
        """Elements that are neither decoder nor depayloader (e.g. a
        plain parser) fall through both arms – covers the 197→exit
        partial branch."""
        fake = make_fake_gst()
        pipeline, decodebin, _, element_cb = self._build_and_get_pad_added_cb(fake)
        elem = FakeElement("typefind")

        class _ParserFactory:
            @staticmethod
            def get_metadata(key: str) -> str:
                return "Generic" if key == "klass" else ""

            @staticmethod
            def get_name() -> str:
                return "typefind"

        elem.get_factory = lambda: _ParserFactory()  # type: ignore[method-assign]
        element_cb(decodebin, elem)
        # No properties touched, no error raised.
        assert "max-threads" not in elem.properties

    def test_element_added_logs_depayloader_branch(self) -> None:
        """A ``Codec/Depayloader`` element triggers the depay log arm
        without setting decoder properties."""
        fake = make_fake_gst()
        pipeline, decodebin, _, element_cb = self._build_and_get_pad_added_cb(fake)
        depay = FakeElement("rtph264depay")

        class _DepayFactory:
            @staticmethod
            def get_metadata(key: str) -> str:
                return "Codec/Depayloader/Network/RTP" if key == "klass" else ""

            @staticmethod
            def get_name() -> str:
                return "rtph264depay"

        depay.get_factory = lambda: _DepayFactory()  # type: ignore[method-assign]
        element_cb(decodebin, depay)
        # Depayloader path does NOT touch decoder properties.
        assert "max-threads" not in depay.properties


class TestCreatePipelineLinkFailures:
    """Each link in the SRT chain has its own ``raise RuntimeError`` arm.
    Targeting them individually keeps a regression on any one seam from
    being masked by an unrelated failure."""

    def _build_with(self, link_fail_kinds: set[str]) -> None:
        from gi.repository import Gst  # noqa: F401

        fake = make_fake_gst(link_fail_kinds=link_fail_kinds)
        sink = FakeElement("shared_videosink")
        with patch("gi.repository.Gst", fake):
            SrtInput().create_pipeline(
                config={},
                sink=sink,
                build_overlay_tail=lambda *a: None,
                prepare_sink=lambda: sink,
            )

    def test_srtsrc_link_failure_raises(self) -> None:
        with pytest.raises(RuntimeError, match="srtsrc"):
            self._build_with({"srtsrc"})

    def test_pre_queue_link_failure_raises(self) -> None:
        # pre_queue is the queue immediately before decodebin.
        with pytest.raises(RuntimeError, match="pre_queue"):
            # The first ``queue`` ElementFactory.make creates pre_queue,
            # then the second creates post_queue. Failing all queues
            # makes pre_queue.link(decodebin) raise first.
            self._build_with({"queue"})

    def test_post_queue_link_failure_raises(self) -> None:
        # Failing only post_queue.link(convert) requires pre_queue's
        # link to succeed. Inject by overriding the make() result.
        from gi.repository import Gst  # noqa: F401

        fake = make_fake_gst()
        original_make = fake.ElementFactory.make
        post_queue_seen = {"n": 0}

        def _make(kind: str, name: str):
            elem = original_make(kind, name)
            if kind == "queue":
                post_queue_seen["n"] += 1
                if post_queue_seen["n"] == 2:
                    elem._link_ok = False
            return elem

        fake.ElementFactory.make = staticmethod(_make)  # type: ignore[assignment]
        sink = FakeElement("shared_videosink")
        with patch("gi.repository.Gst", fake):
            with pytest.raises(RuntimeError, match="post_queue"):
                SrtInput().create_pipeline(
                    config={},
                    sink=sink,
                    build_overlay_tail=lambda *a: None,
                    prepare_sink=lambda: sink,
                )


# --------------------------------------------------------------------------- #
# Lifecycle + labels
# --------------------------------------------------------------------------- #


class TestOnBusAsyncDone:
    def test_does_not_mutate_latency(self) -> None:
        pipeline = FakePipeline("srt")
        SrtInput().on_bus_async_done(pipeline)
        # SRT explicitly preserves decoder latency – it must not call
        # set_latency(0) the way NDI / RTP / RTSP do.
        assert pipeline.latency_values == []


class TestGetSourceLabel:
    def test_label_uses_resolved_uri(self) -> None:
        label = SrtInput.get_source_label({"srt_host": "192.168.1.10:9000"})
        assert label == "SRT srt://192.168.1.10:9000"

    def test_label_uses_default_for_missing_config(self) -> None:
        assert SrtInput.get_source_label({}) == "SRT srt://0.0.0.0:5000"
