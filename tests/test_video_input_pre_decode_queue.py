# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The queue before a network input's decoder, on a real GStreamer queue."""

from __future__ import annotations

from typing import Any

import pytest

from openfollow.video.inputs._base import PRE_DECODE_QUEUE_BYTES, PRE_DECODE_QUEUE_NS, configure_pre_decode_queue

pytestmark = pytest.mark.unit


def _stalled_pre_decode_queue() -> tuple[Any, Any, Any]:
    """A configured queue whose consumer, a paused sink, holds the first buffer: the rest stay queued."""
    try:
        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst
    except (ImportError, ValueError):
        pytest.skip("PyGObject / GStreamer is not importable")
    Gst.init(None)
    pipeline = Gst.Pipeline.new("pre-decode")
    queue = Gst.ElementFactory.make("queue", "pre_queue")
    sink = Gst.ElementFactory.make("fakesink", "sink")
    pipeline.add(queue)
    pipeline.add(sink)
    queue.link(sink)
    configure_pre_decode_queue(queue)
    # A full queue that does not leak blocks the chain calls of the test for good.
    assert queue.get_property("leaky") == 2
    pipeline.set_state(Gst.State.PAUSED)
    pad = queue.get_static_pad("sink")
    pad.send_event(Gst.Event.new_stream_start("pre-decode"))
    segment = Gst.Segment()
    segment.init(Gst.Format.TIME)
    pad.send_event(Gst.Event.new_segment(segment))
    return Gst, pipeline, queue


@pytest.mark.parametrize(
    ("stamp", "bound"),
    [
        (lambda i: i * 10_000_000, ("current-level-time", PRE_DECODE_QUEUE_NS)),
        (lambda i: None, ("current-level-bytes", PRE_DECODE_QUEUE_BYTES)),
        (lambda i: (1000 - i) * 10_000_000 if i else 0, ("current-level-bytes", PRE_DECODE_QUEUE_BYTES)),
    ],
    ids=["timestamped", "no-timestamps", "timestamps-step-back"],
)
def test_the_queue_before_the_decoder_stays_bounded_while_the_decoder_stalls(
    stamp: Any, bound: tuple[str, int]
) -> None:
    gst, pipeline, queue = _stalled_pre_decode_queue()
    try:
        pad = queue.get_static_pad("sink")
        for i in range(200):
            buffer = gst.Buffer.new_wrapped(bytes(65_536))
            pts = stamp(i)
            if pts is not None:
                buffer.pts = pts
            assert pad.chain(buffer) == gst.FlowReturn.OK
        level, limit = bound
        assert 0 < queue.get_property(level) <= limit
        assert queue.get_property("current-level-buffers") < 199
    finally:
        pipeline.set_state(gst.State.NULL)
