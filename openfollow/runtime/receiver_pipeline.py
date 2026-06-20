# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Pipeline assembly helpers for ``GstNativeSinkReceiver``."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openfollow.video.detection import PersonDetector
    from openfollow.video.overlay import CairoOverlayRenderer
    from openfollow.video.preview import PreviewProvider, SnapshotProvider

# prepare_sink returns gtksink element or None if unavailable.
PrepareSinkCallback = Callable[[], "Any | None"]


class ReceiverPipelineAssembler:
    """Builds receiver pipelines while keeping the receiver facade thin."""

    def __init__(
        self,
        gst: Any,
        logger: logging.Logger,
        overlay_renderer: CairoOverlayRenderer | None,
        detector: PersonDetector | None,
        prepare_sink: PrepareSinkCallback,
        preview_provider: PreviewProvider | None = None,
        snapshot_provider: SnapshotProvider | None = None,
    ) -> None:
        self._gst = gst
        self._logger = logger
        self._overlay_renderer = overlay_renderer
        self._detector = detector
        self._prepare_sink = prepare_sink
        self._preview_provider = preview_provider
        self._snapshot_provider = snapshot_provider
        self._placeholder_resolution = (1920, 1080)

    @property
    def placeholder_resolution(self) -> tuple[int, int]:
        return self._placeholder_resolution

    def set_detector(self, detector: PersonDetector | None) -> None:
        """Update detector reference for pipeline rebuild."""
        self._detector = detector

    def build_overlay_tail(
        self,
        pipeline: Any,
        convert: Any,
        sink: Any,
    ) -> None:
        """Link convert → optional tee branches → sink."""
        need_detection = self._detector is not None and self._detector.available
        need_preview = self._preview_provider is not None
        need_snapshot = self._snapshot_provider is not None
        need_tee = need_detection or need_preview or need_snapshot

        if need_tee:
            tee = self._gst.ElementFactory.make("tee", "detection_tee")
            display_queue = self._gst.ElementFactory.make("queue", "display_queue")
            display_queue.set_property("max-size-buffers", 2)
            display_queue.set_property("leaky", 2)

            pipeline.add(tee)
            pipeline.add(display_queue)

            if not convert.link(tee):
                raise RuntimeError("Failed to link videoconvert → tee")

            _request_pad = getattr(tee, "request_pad_simple", None) or tee.get_request_pad
            tee_display_pad = _request_pad("src_%u")
            display_sink_pad = display_queue.get_static_pad("sink")
            if tee_display_pad.link(display_sink_pad) != self._gst.PadLinkReturn.OK:
                raise RuntimeError("Failed to link tee → display_queue")

            if need_detection:
                self._add_detection_branch(pipeline, tee, _request_pad)

            if need_preview:
                self._add_preview_branch(pipeline, tee, _request_pad)

            if need_snapshot:
                self._add_snapshot_branch(pipeline, tee, _request_pad)

            head = display_queue
        else:
            head = convert

        if not head.link(sink):
            raise RuntimeError("Failed to link head → videosink")

    def create_placeholder_pipeline(self) -> Any | None:
        """Build black-frame pipeline; returns None if no sink available."""
        pipeline = self._gst.Pipeline.new("placeholder-pipeline")

        videotestsrc = self._gst.ElementFactory.make("videotestsrc", "videotestsrc")
        videotestsrc.set_property("pattern", 2)  # Black
        videotestsrc.set_property("is-live", True)

        caps_filter = self._gst.ElementFactory.make("capsfilter", "caps")
        width, height = self._placeholder_resolution
        caps = self._gst.Caps.from_string(f"video/x-raw,width={width},height={height},framerate=30/1")
        caps_filter.set_property("caps", caps)

        convert = self._gst.ElementFactory.make("videoconvert", "convert")
        sink = self._prepare_sink()
        if sink is None:
            self._logger.error("No video sink available for placeholder pipeline")
            return None

        for elem in (videotestsrc, caps_filter, convert, sink):
            pipeline.add(elem)

        if not videotestsrc.link(caps_filter):
            raise RuntimeError("Failed to link videotestsrc → capsfilter")
        if not caps_filter.link(convert):
            raise RuntimeError("Failed to link capsfilter → videoconvert")

        self.build_overlay_tail(pipeline, convert, sink)
        return pipeline

    def _add_detection_branch(self, pipeline: Any, tee: Any, request_pad: Any) -> None:
        """Wire a detection appsink branch off the shared tee.

        Caller (``build_overlay_tail``) gates this on
        ``self._detector is not None and self._detector.available``,
        so the assertion documents the invariant for both reviewers
        and mypy.
        """
        assert self._detector is not None
        det_queue = self._gst.ElementFactory.make("queue", "detection_queue")
        det_queue.set_property("max-size-buffers", 1)
        det_queue.set_property("max-size-bytes", 0)
        det_queue.set_property("max-size-time", 0)
        det_queue.set_property("leaky", 2)

        det_scale = self._gst.ElementFactory.make("videoscale", "det_scale")
        det_caps = self._gst.ElementFactory.make("capsfilter", "det_caps")
        det_w, det_h = self._detector.input_resolution
        det_caps.set_property(
            "caps",
            self._gst.Caps.from_string(
                f"video/x-raw,width={det_w},height={det_h}",
            ),
        )
        det_convert = self._gst.ElementFactory.make("videoconvert", "det_convert")
        det_sink = self._gst.ElementFactory.make("appsink", "det_appsink")
        det_sink.set_property("emit-signals", False)
        det_sink.set_property("drop", True)
        det_sink.set_property("max-buffers", 1)
        det_sink.set_property(
            "caps",
            self._gst.Caps.from_string("video/x-raw,format=RGB"),
        )

        for el in (det_queue, det_scale, det_caps, det_convert, det_sink):
            pipeline.add(el)

        tee_pad = request_pad("src_%u")
        sink_pad = det_queue.get_static_pad("sink")
        if tee_pad.link(sink_pad) != self._gst.PadLinkReturn.OK:
            raise RuntimeError("Failed to link tee → detection_queue")
        if not det_queue.link(det_scale):
            raise RuntimeError("Failed to link detection_queue → videoscale")
        if not det_scale.link(det_caps):
            raise RuntimeError("Failed to link videoscale → capsfilter")
        if not det_caps.link(det_convert):
            raise RuntimeError("Failed to link capsfilter → videoconvert")
        if not det_convert.link(det_sink):
            raise RuntimeError("Failed to link videoconvert → appsink")

        self._detector.set_appsink(det_sink)
        self._logger.info("Detection appsink branch added to pipeline")

    def _add_preview_branch(self, pipeline: Any, tee: Any, request_pad: Any) -> None:
        """Wire preview appsink branch; GStreamer handles scaling/encoding."""
        assert self._preview_provider is not None
        from openfollow.video.preview import (
            _JPEG_QUALITY,
            PREVIEW_HEIGHT,
            PREVIEW_WIDTH,
        )

        q = self._gst.ElementFactory.make(
            "queue",
            "preview_queue",
        )
        q.set_property("max-size-buffers", 1)
        q.set_property("max-size-bytes", 0)
        q.set_property("max-size-time", 0)
        q.set_property("leaky", 2)

        scale = self._gst.ElementFactory.make(
            "videoscale",
            "preview_scale",
        )
        caps = self._gst.ElementFactory.make(
            "capsfilter",
            "preview_caps",
        )
        caps.set_property(
            "caps",
            self._gst.Caps.from_string(
                f"video/x-raw,width={PREVIEW_WIDTH},height={PREVIEW_HEIGHT}",
            ),
        )
        conv = self._gst.ElementFactory.make(
            "videoconvert",
            "preview_convert",
        )
        enc = self._gst.ElementFactory.make(
            "jpegenc",
            "preview_jpegenc",
        )
        if enc is None:
            self._logger.warning("jpegenc not available – preview disabled")
            return
        enc.set_property("quality", _JPEG_QUALITY)

        appsink = self._gst.ElementFactory.make(
            "appsink",
            "preview_appsink",
        )
        appsink.set_property("emit-signals", False)
        appsink.set_property("drop", True)
        appsink.set_property("max-buffers", 1)
        # async=False / sync=False so this branch never gates the pipeline's
        # state change on a preroll buffer; the valve starts closed so we need
        # async=False to avoid deadlock.
        appsink.set_property("async", False)
        appsink.set_property("sync", False)
        appsink.set_property(
            "caps",
            self._gst.Caps.from_string("image/jpeg"),
        )

        # Valve gates the videoscale/videoconvert/jpegenc so preview encoding
        # only runs when polled. Sits after the queue; degrade if missing.
        valve = self._gst.ElementFactory.make("valve", "preview_valve")
        if valve is not None:
            valve.set_property("drop", True)

        post_queue = [el for el in (valve, scale, caps, conv, enc, appsink) if el is not None]
        for el in (q, *post_queue):
            pipeline.add(el)

        tee_pad = request_pad("src_%u")
        sink_pad = q.get_static_pad("sink")
        if tee_pad.link(sink_pad) != self._gst.PadLinkReturn.OK:
            raise RuntimeError(
                "Failed to link tee → preview_queue",
            )
        prev = q
        for el in post_queue:
            if not prev.link(el):
                raise RuntimeError(
                    f"Failed to link {prev.get_name()} → {el.get_name()}",
                )
            prev = el

        self._preview_provider.set_appsink(appsink)
        # Pass valve unconditionally to clear stale cached reference.
        self._preview_provider.set_valve(valve)
        self._preview_provider.start()
        self._logger.info("Preview appsink branch added to pipeline")

    def _add_snapshot_branch(self, pipeline: Any, tee: Any, request_pad: Any) -> None:
        """Wire a full-resolution snapshot appsink branch off the shared tee.

        Pipeline: queue(leaky) → videoconvert → jpegenc(quality=92) → appsink
        No videoscale – frames pass through at source resolution. Caller
        (``build_overlay_tail``) gates this on
        ``self._snapshot_provider is not None``; assertion documents
        the invariant.
        """
        assert self._snapshot_provider is not None
        from openfollow.video.preview import _SNAPSHOT_JPEG_QUALITY

        q = self._gst.ElementFactory.make("queue", "snapshot_queue")
        q.set_property("max-size-buffers", 1)
        q.set_property("max-size-bytes", 0)
        q.set_property("max-size-time", 0)
        q.set_property("leaky", 2)

        conv = self._gst.ElementFactory.make("videoconvert", "snapshot_convert")
        enc = self._gst.ElementFactory.make("jpegenc", "snapshot_jpegenc")
        if enc is None:
            self._logger.warning("jpegenc not available – snapshot disabled")
            return
        enc.set_property("quality", _SNAPSHOT_JPEG_QUALITY)

        appsink = self._gst.ElementFactory.make("appsink", "snapshot_appsink")
        appsink.set_property("emit-signals", False)
        appsink.set_property("drop", True)
        appsink.set_property("max-buffers", 1)
        # async=False / sync=False so the closed valve below can't hold
        # the pipeline in ASYNC waiting for a preroll buffer.
        appsink.set_property("async", False)
        appsink.set_property("sync", False)
        appsink.set_property(
            "caps",
            self._gst.Caps.from_string("image/jpeg"),
        )

        # Gate the full-res videoconvert/jpegenc behind a valve so they only
        # run on request. Full-resolution encode is the largest avoidable cost
        # when idle. Sits after the queue; ``drop=True`` starts closed and
        # ``SnapshotProvider`` opens it per request. Degrade if missing.
        valve = self._gst.ElementFactory.make("valve", "snapshot_valve")
        if valve is not None:
            valve.set_property("drop", True)

        post_queue = [el for el in (valve, conv, enc, appsink) if el is not None]
        for el in (q, *post_queue):
            pipeline.add(el)

        tee_pad = request_pad("src_%u")
        sink_pad = q.get_static_pad("sink")
        if tee_pad.link(sink_pad) != self._gst.PadLinkReturn.OK:
            raise RuntimeError("Failed to link tee → snapshot_queue")
        prev = q
        for el in post_queue:
            if not prev.link(el):
                raise RuntimeError(f"Failed to link {prev.get_name()} → {el.get_name()}")
            prev = el

        self._snapshot_provider.set_appsink(appsink)
        # Unconditional like the preview branch: ``None`` clears a cached valve
        # so the provider's state stays aligned with the branch wiring.
        self._snapshot_provider.set_valve(valve)
        self._logger.info("Snapshot appsink branch added to pipeline (full resolution)")
