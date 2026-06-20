# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for :class:`ReceiverPipelineAssembler`.

The assembler owns all the tee-splitting and branch-wiring logic the
receiver uses at pipeline-construction time. These tests run it against
a fake ``Gst`` so they stay hermetic while still exercising:

 - straight ``convert → sink`` when no tee branch is requested;
 - tee + display_queue fan-out when detection / preview / snapshot is
   requested;
 - detection branch wiring (queue → scale → capsfilter → convert →
   appsink) and the detector's appsink wire-up;
 - preview branch graceful degradation when ``jpegenc`` is not available
   (returns without wiring the provider);
 - snapshot branch wiring + graceful degradation;
 - every ``RuntimeError`` raised when a GStreamer link fails.
"""

from __future__ import annotations

import logging

import pytest

from openfollow.runtime.receiver_pipeline import ReceiverPipelineAssembler

pytestmark = pytest.mark.unit

# --------------------------------------------------------------------------- #
# Fake Gst
# --------------------------------------------------------------------------- #


class FakePad:
    def __init__(self, *, link_returns_ok: bool = True) -> None:
        self._link_returns_ok = link_returns_ok
        self.linked_to: FakePad | None = None

    def link(self, other: FakePad) -> str:
        self.linked_to = other
        return "ok" if self._link_returns_ok else "fail"


class FakeElement:
    """Records set_property / link / pad requests."""

    def __init__(self, name: str, *, link_ok: bool = True, pad_link_ok: bool = True) -> None:
        self.name = name
        self.properties: dict[str, object] = {}
        self.links: list[FakeElement] = []
        self.pads_requested: list[str] = []
        self._link_ok = link_ok
        self._pad_link_ok = pad_link_ok

    def get_name(self) -> str:
        return self.name

    def set_property(self, key: str, value: object) -> None:
        self.properties[key] = value

    def link(self, other: FakeElement) -> bool:
        self.links.append(other)
        return self._link_ok

    def get_static_pad(self, name: str) -> FakePad:
        return FakePad(link_returns_ok=self._pad_link_ok)

    def request_pad_simple(self, template: str) -> FakePad:
        self.pads_requested.append(template)
        return FakePad(link_returns_ok=self._pad_link_ok)


class FakePipeline:
    def __init__(self) -> None:
        self.elements: list[FakeElement] = []

    def add(self, element: FakeElement) -> None:
        self.elements.append(element)

    def get_by_name(self, name: str) -> FakeElement | None:
        for e in self.elements:
            if e.name == name:
                return e
        return None


def _make_fake_gst(
    *,
    element_factory_overrides: dict[str, FakeElement | None] | None = None,
):
    """Produce a FakeGst namespace with an ElementFactory the tests
    can partially override (eg. to simulate missing ``jpegenc``)."""

    overrides = dict(element_factory_overrides or {})

    class FakeGst:
        class Pipeline:
            @staticmethod
            def new(name: str) -> FakePipeline:
                return FakePipeline()

        class ElementFactory:
            @staticmethod
            def make(kind: str, name: str) -> FakeElement | None:
                # If the test pre-declared a replacement for this element
                # name, honour it (including None for missing plugins).
                if name in overrides:
                    return overrides[name]
                return FakeElement(name)

        class Caps:
            @staticmethod
            def from_string(value: str) -> str:
                return value

        class PadLinkReturn:
            OK = "ok"

    return FakeGst


# --------------------------------------------------------------------------- #
# Fake providers / detector
# --------------------------------------------------------------------------- #


class FakeDetector:
    def __init__(self, *, available: bool = True, input_resolution: tuple[int, int] = (640, 640)) -> None:
        self.available = available
        self.input_resolution = input_resolution
        self.appsink: object | None = None

    def set_appsink(self, sink: object) -> None:
        self.appsink = sink


class FakePreviewProvider:
    def __init__(self) -> None:
        self.appsink: object | None = None
        self.valve: object | None = None
        self.started = False

    def set_appsink(self, sink: object) -> None:
        self.appsink = sink

    def set_valve(self, valve: object) -> None:
        self.valve = valve

    def start(self) -> None:
        self.started = True


class FakeSnapshotProvider:
    def __init__(self) -> None:
        self.appsink: object | None = None
        self.valve: object | None = None

    def set_appsink(self, sink: object) -> None:
        self.appsink = sink

    def set_valve(self, valve: object) -> None:
        self.valve = valve


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _build_convert_sink():
    """Return (convert, sink) pair wired with the standard element fakes."""
    return FakeElement("videoconvert"), FakeElement("videosink")


def _make_assembler(
    *,
    gst=None,
    detector: FakeDetector | None = None,
    preview: FakePreviewProvider | None = None,
    snapshot: FakeSnapshotProvider | None = None,
    sink: FakeElement | None = None,
) -> ReceiverPipelineAssembler:
    return ReceiverPipelineAssembler(
        gst=gst or _make_fake_gst(),
        logger=logging.getLogger("test"),
        overlay_renderer=object(),
        detector=detector,
        prepare_sink=lambda: sink if sink is not None else FakeElement("videosink"),
        preview_provider=preview,
        snapshot_provider=snapshot,
    )


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


class TestOverlayTailNoTee:
    def test_direct_convert_to_sink_without_any_branches(self) -> None:
        """When no detection/preview/snapshot is requested, convert
        links straight to the sink (no tee inserted)."""
        assembler = _make_assembler()
        pipeline = FakePipeline()
        convert, sink = _build_convert_sink()

        assembler.build_overlay_tail(pipeline, convert, sink)

        # No tee element was added
        assert pipeline.get_by_name("detection_tee") is None
        # convert was linked directly to sink
        assert sink in convert.links

    def test_failure_to_link_head_to_sink_raises(self) -> None:
        assembler = _make_assembler()
        pipeline = FakePipeline()
        convert = FakeElement("videoconvert", link_ok=False)
        sink = FakeElement("videosink")
        with pytest.raises(RuntimeError, match="head → videosink"):
            assembler.build_overlay_tail(pipeline, convert, sink)


class TestOverlayTailDetection:
    def test_detection_branch_wires_appsink_into_detector(self) -> None:
        detector = FakeDetector(input_resolution=(320, 320))
        assembler = _make_assembler(detector=detector)
        pipeline = FakePipeline()
        convert, sink = _build_convert_sink()

        assembler.build_overlay_tail(pipeline, convert, sink)

        # Tee was inserted
        assert pipeline.get_by_name("detection_tee") is not None
        assert pipeline.get_by_name("display_queue") is not None
        # Detection elements
        assert pipeline.get_by_name("detection_queue") is not None
        assert pipeline.get_by_name("det_appsink") is not None
        # Detector received the appsink
        assert detector.appsink is not None
        assert isinstance(detector.appsink, FakeElement)
        assert detector.appsink.name == "det_appsink"
        # Detection capsfilter uses the detector's configured resolution
        det_caps = pipeline.get_by_name("det_caps")
        assert det_caps is not None
        assert "width=320" in det_caps.properties["caps"]
        assert "height=320" in det_caps.properties["caps"]

    def test_unavailable_detector_is_not_wired(self) -> None:
        detector = FakeDetector(available=False)
        assembler = _make_assembler(detector=detector)
        pipeline = FakePipeline()
        convert, sink = _build_convert_sink()

        assembler.build_overlay_tail(pipeline, convert, sink)

        # need_tee=False → no tee, and no detection branch
        assert pipeline.get_by_name("detection_tee") is None
        assert detector.appsink is None


class TestOverlayTailPreview:
    def test_preview_branch_wires_and_starts_provider(self) -> None:
        preview = FakePreviewProvider()
        assembler = _make_assembler(preview=preview)
        pipeline = FakePipeline()
        convert, sink = _build_convert_sink()

        assembler.build_overlay_tail(pipeline, convert, sink)

        assert pipeline.get_by_name("preview_appsink") is not None
        assert preview.appsink is not None
        assert preview.started is True
        # Encoder is gated behind a closed valve that the provider can open/close on demand.
        valve = pipeline.get_by_name("preview_valve")
        assert valve is not None
        assert valve.properties["drop"] is True
        assert preview.valve is valve
        # async/sync False allows the closed valve to gate without blocking the display pipeline.
        appsink = pipeline.get_by_name("preview_appsink")
        assert appsink.properties["async"] is False
        assert appsink.properties["sync"] is False

    def test_preview_without_jpegenc_gracefully_skips(self) -> None:
        preview = FakePreviewProvider()
        gst = _make_fake_gst(
            element_factory_overrides={"preview_jpegenc": None},
        )
        assembler = _make_assembler(gst=gst, preview=preview)
        pipeline = FakePipeline()
        convert, sink = _build_convert_sink()

        assembler.build_overlay_tail(pipeline, convert, sink)

        assert preview.appsink is None
        assert preview.started is False

    def test_preview_without_valve_degrades_to_always_on(self) -> None:
        """No ``valve`` element → branch still wires and starts, just with
        no on-demand gating (provider gets no valve)."""
        preview = FakePreviewProvider()
        gst = _make_fake_gst(element_factory_overrides={"preview_valve": None})
        assembler = _make_assembler(gst=gst, preview=preview)
        pipeline = FakePipeline()
        convert, sink = _build_convert_sink()

        assembler.build_overlay_tail(pipeline, convert, sink)

        assert pipeline.get_by_name("preview_appsink") is not None
        assert pipeline.get_by_name("preview_valve") is None
        assert preview.appsink is not None
        assert preview.valve is None
        assert preview.started is True

    def test_preview_clears_stale_valve_when_valve_missing(self) -> None:
        preview = FakePreviewProvider()
        preview.valve = object()  # stale valve from a previous pipeline build
        gst = _make_fake_gst(element_factory_overrides={"preview_valve": None})
        assembler = _make_assembler(gst=gst, preview=preview)
        pipeline = FakePipeline()
        convert, sink = _build_convert_sink()

        assembler.build_overlay_tail(pipeline, convert, sink)

        assert pipeline.get_by_name("preview_valve") is None
        assert preview.valve is None  # stale reference explicitly cleared
        assert preview.started is True


class TestOverlayTailSnapshot:
    def test_snapshot_branch_wires_appsink(self) -> None:
        snapshot = FakeSnapshotProvider()
        assembler = _make_assembler(snapshot=snapshot)
        pipeline = FakePipeline()
        convert, sink = _build_convert_sink()

        assembler.build_overlay_tail(pipeline, convert, sink)

        assert pipeline.get_by_name("snapshot_appsink") is not None
        assert snapshot.appsink is not None
        # Full-res encoder is gated behind a closed valve that the provider can open per-request.
        valve = pipeline.get_by_name("snapshot_valve")
        assert valve is not None
        assert valve.properties["drop"] is True
        assert snapshot.valve is valve
        appsink = pipeline.get_by_name("snapshot_appsink")
        assert appsink.properties["async"] is False
        assert appsink.properties["sync"] is False

    def test_snapshot_without_jpegenc_gracefully_skips(self) -> None:
        snapshot = FakeSnapshotProvider()
        gst = _make_fake_gst(
            element_factory_overrides={"snapshot_jpegenc": None},
        )
        assembler = _make_assembler(gst=gst, snapshot=snapshot)
        pipeline = FakePipeline()
        convert, sink = _build_convert_sink()

        assembler.build_overlay_tail(pipeline, convert, sink)

        assert snapshot.appsink is None

    def test_snapshot_without_valve_degrades_to_always_on(self) -> None:
        """No ``valve`` element → branch still wires, provider gets no valve."""
        snapshot = FakeSnapshotProvider()
        gst = _make_fake_gst(element_factory_overrides={"snapshot_valve": None})
        assembler = _make_assembler(gst=gst, snapshot=snapshot)
        pipeline = FakePipeline()
        convert, sink = _build_convert_sink()

        assembler.build_overlay_tail(pipeline, convert, sink)

        assert pipeline.get_by_name("snapshot_appsink") is not None
        assert pipeline.get_by_name("snapshot_valve") is None
        assert snapshot.appsink is not None
        assert snapshot.valve is None

    def test_snapshot_clears_stale_valve_when_valve_missing(self) -> None:
        """Like preview: a missing valve on rebuild clears the provider's stale reference."""
        snapshot = FakeSnapshotProvider()
        snapshot.valve = object()  # stale valve from a previous pipeline build
        gst = _make_fake_gst(element_factory_overrides={"snapshot_valve": None})
        assembler = _make_assembler(gst=gst, snapshot=snapshot)
        pipeline = FakePipeline()
        convert, sink = _build_convert_sink()

        assembler.build_overlay_tail(pipeline, convert, sink)

        assert pipeline.get_by_name("snapshot_valve") is None
        assert snapshot.valve is None  # stale reference explicitly cleared


class TestLinkFailures:
    def test_convert_to_tee_link_failure_raises(self) -> None:
        assembler = _make_assembler(detector=FakeDetector())
        pipeline = FakePipeline()
        convert = FakeElement("videoconvert", link_ok=False)
        sink = FakeElement("videosink")
        with pytest.raises(RuntimeError, match="videoconvert → tee"):
            assembler.build_overlay_tail(pipeline, convert, sink)

    def test_tee_to_display_queue_pad_link_failure_raises(self) -> None:
        """The shared tee sources every branch via a request pad. If the
        first pad-link to the display queue fails, the operator gets a
        targeted error instead of an opaque pipeline crash."""
        gst = _make_fake_gst(
            element_factory_overrides={
                "detection_tee": FakeElement("detection_tee", pad_link_ok=False),
            }
        )
        assembler = _make_assembler(gst=gst, detector=FakeDetector())
        pipeline = FakePipeline()
        convert, sink = _build_convert_sink()
        with pytest.raises(RuntimeError, match="tee → display_queue"):
            assembler.build_overlay_tail(pipeline, convert, sink)

    def _tee_failing_after_first_pad_request(self) -> FakeElement:
        """Build a tee whose first pad-link succeeds (for display) but
        subsequent pad-link calls return failure. The shared tee is
        called once per active branch, so this lets each branch test
        target *its* tee → branch_queue link without affecting the
        display link that ran first."""
        tee = FakeElement("detection_tee")

        call_count = {"n": 0}

        def _stateful_request(template: str) -> FakePad:
            call_count["n"] += 1
            tee.pads_requested.append(template)
            return FakePad(link_returns_ok=(call_count["n"] == 1))

        tee.request_pad_simple = _stateful_request  # type: ignore[assignment]
        return tee

    def test_detection_tee_pad_link_failure_raises(self) -> None:
        gst = _make_fake_gst(
            element_factory_overrides={
                "detection_tee": self._tee_failing_after_first_pad_request(),
            }
        )
        assembler = _make_assembler(gst=gst, detector=FakeDetector())
        pipeline = FakePipeline()
        convert, sink = _build_convert_sink()
        with pytest.raises(RuntimeError, match="tee → detection_queue"):
            assembler.build_overlay_tail(pipeline, convert, sink)

    @pytest.mark.parametrize(
        "broken_name,expected_match",
        [
            ("detection_queue", "detection_queue → videoscale"),
            ("det_scale", "videoscale → capsfilter"),
            ("det_caps", "capsfilter → videoconvert"),
            ("det_convert", "videoconvert → appsink"),
        ],
    )
    def test_detection_chain_link_failure_raises_at_each_seam(
        self,
        broken_name: str,
        expected_match: str,
    ) -> None:
        """Each detection-branch link has its own targeted RuntimeError.
        Parametrized so a regression on any one seam fails its specific
        case (rather than bundling them all into one opaque test)."""
        gst = _make_fake_gst(
            element_factory_overrides={
                broken_name: FakeElement(broken_name, link_ok=False),
            }
        )
        assembler = _make_assembler(gst=gst, detector=FakeDetector())
        pipeline = FakePipeline()
        convert, sink = _build_convert_sink()
        with pytest.raises(RuntimeError, match=expected_match):
            assembler.build_overlay_tail(pipeline, convert, sink)

    def test_preview_tee_pad_link_failure_raises(self) -> None:
        gst = _make_fake_gst(
            element_factory_overrides={
                "detection_tee": self._tee_failing_after_first_pad_request(),
            }
        )
        assembler = _make_assembler(gst=gst, preview=FakePreviewProvider())
        pipeline = FakePipeline()
        convert, sink = _build_convert_sink()
        with pytest.raises(RuntimeError, match="tee → preview_queue"):
            assembler.build_overlay_tail(pipeline, convert, sink)

    def test_preview_chain_link_failure_inside_loop_raises(self) -> None:
        # In this fake Gst model, the upstream element's ``link()`` result
        # controls the failure, so breaking the ``preview_convert →
        # preview_jpegenc`` seam means making ``preview_convert`` refuse.
        gst = _make_fake_gst(
            element_factory_overrides={
                "preview_convert": FakeElement("preview_convert", link_ok=False),
            }
        )
        assembler = _make_assembler(gst=gst, preview=FakePreviewProvider())
        pipeline = FakePipeline()
        convert, sink = _build_convert_sink()
        with pytest.raises(RuntimeError, match="preview_convert →"):
            assembler.build_overlay_tail(pipeline, convert, sink)

    def test_snapshot_tee_pad_link_failure_raises(self) -> None:
        """Snapshot branch tee-pad link failure surfaces a targeted error."""
        gst = _make_fake_gst(
            element_factory_overrides={
                "detection_tee": self._tee_failing_after_first_pad_request(),
            }
        )
        assembler = _make_assembler(gst=gst, snapshot=FakeSnapshotProvider())
        pipeline = FakePipeline()
        convert, sink = _build_convert_sink()
        with pytest.raises(RuntimeError, match="tee → snapshot_queue"):
            assembler.build_overlay_tail(pipeline, convert, sink)

    def test_snapshot_chain_link_failure_inside_loop_raises(self) -> None:
        """Inside the snapshot branch's chain (queue → convert → enc →
        appsink), a refused link surfaces a precise error."""
        gst = _make_fake_gst(
            element_factory_overrides={
                "snapshot_convert": FakeElement(
                    "snapshot_convert",
                    link_ok=False,
                ),
            }
        )
        assembler = _make_assembler(gst=gst, snapshot=FakeSnapshotProvider())
        pipeline = FakePipeline()
        convert, sink = _build_convert_sink()
        with pytest.raises(RuntimeError, match="snapshot_convert →"):
            assembler.build_overlay_tail(pipeline, convert, sink)

    def test_placeholder_capsfilter_to_convert_link_failure_raises(self) -> None:
        gst = _make_fake_gst(
            element_factory_overrides={
                "caps": FakeElement("caps", link_ok=False),
            }
        )
        assembler = _make_assembler(gst=gst)
        with pytest.raises(RuntimeError, match="capsfilter → videoconvert"):
            assembler.create_placeholder_pipeline()


class TestPlaceholderPipeline:
    def test_placeholder_pipeline_builds(self) -> None:
        assembler = _make_assembler()
        pipeline = assembler.create_placeholder_pipeline()
        assert pipeline is not None
        videotestsrc = pipeline.get_by_name("videotestsrc")
        assert videotestsrc is not None
        assert videotestsrc.properties["pattern"] == 2  # black
        assert videotestsrc.properties["is-live"] is True

    def test_placeholder_uses_configured_resolution_in_caps(self) -> None:
        assembler = _make_assembler()
        pipeline = assembler.create_placeholder_pipeline()
        caps_filter = pipeline.get_by_name("caps")
        assert "width=1920" in caps_filter.properties["caps"]
        assert "height=1080" in caps_filter.properties["caps"]

    def test_placeholder_returns_none_when_sink_unavailable(self) -> None:
        assembler = ReceiverPipelineAssembler(
            gst=_make_fake_gst(),
            logger=logging.getLogger("test"),
            overlay_renderer=None,
            detector=None,
            prepare_sink=lambda: None,
        )
        assert assembler.create_placeholder_pipeline() is None

    def test_placeholder_link_failure_raises(self) -> None:
        class UnlinkableElement(FakeElement):
            def link(self, other: FakeElement) -> bool:
                return False

        gst = _make_fake_gst(
            element_factory_overrides={
                "videotestsrc": UnlinkableElement("videotestsrc"),
            },
        )
        assembler = _make_assembler(gst=gst)
        with pytest.raises(RuntimeError, match="videotestsrc → capsfilter"):
            assembler.create_placeholder_pipeline()


class TestPlaceholderResolution:
    def test_property_returns_default_tuple(self) -> None:
        assembler = _make_assembler()
        assert assembler.placeholder_resolution == (1920, 1080)


class TestCombinedBranches:
    def test_detection_plus_preview_plus_snapshot_all_wired(self) -> None:
        detector = FakeDetector()
        preview = FakePreviewProvider()
        snapshot = FakeSnapshotProvider()
        assembler = _make_assembler(
            detector=detector,
            preview=preview,
            snapshot=snapshot,
        )
        pipeline = FakePipeline()
        convert, sink = _build_convert_sink()

        assembler.build_overlay_tail(pipeline, convert, sink)

        assert detector.appsink is not None
        assert preview.appsink is not None
        assert snapshot.appsink is not None
        # Each branch allocates its own queue off the shared tee
        for name in ("detection_queue", "preview_queue", "snapshot_queue"):
            assert pipeline.get_by_name(name) is not None
