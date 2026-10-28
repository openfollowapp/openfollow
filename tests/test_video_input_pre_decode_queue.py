# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The queue before a network input's decoder, on a real GStreamer queue."""

from __future__ import annotations

from typing import Any

import pytest

from openfollow.video.inputs._base import PRE_DECODE_QUEUE_BYTES, PRE_DECODE_QUEUE_NS, configure_pre_decode_queue
from tests._real_gst import real_gst

pytestmark = pytest.mark.unit


def _stalled_pre_decode_queue() -> tuple[Any, Any, Any]:
    """A configured queue whose consumer, a paused sink, holds the first buffer: the rest stay queued."""
    gst = real_gst()
    pipeline = gst.Pipeline.new("pre-decode")
    queue = gst.ElementFactory.make("queue", "pre_queue")
    sink = gst.ElementFactory.make("fakesink", "sink")
    pipeline.add(queue)
    pipeline.add(sink)
    queue.link(sink)
    configure_pre_decode_queue(queue)
    # A full queue that does not leak blocks the chain calls of the test for good.
    assert queue.get_property("leaky") == 2
    pipeline.set_state(gst.State.PAUSED)
    pad = queue.get_static_pad("sink")
    pad.send_event(gst.Event.new_stream_start("pre-decode"))
    segment = gst.Segment()
    segment.init(gst.Format.TIME)
    pad.send_event(gst.Event.new_segment(segment))
    return gst, pipeline, queue


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
