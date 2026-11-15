# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Paul Hermann and Michel Honold
"""The device-scaled sink: frames reach the inner sink at the widget's device pixels."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from openfollow.video.device_scale import device_scaled_sink, follow_widget, set_device_size
from tests._real_gst import real_gst

pytestmark = pytest.mark.unit


def _run(gst: Any, sink_bin: Any, src_caps: str) -> tuple[str, str]:
    """Push two frames of ``src_caps`` into ``sink_bin``; return the caps at its ghost pad and at the inner sink."""
    pipeline = gst.Pipeline.new("device-scale-test")
    src = gst.ElementFactory.make("videotestsrc", None)
    src.set_property("num-buffers", 2)
    src_filter = gst.ElementFactory.make("capsfilter", None)
    src_filter.set_property("caps", gst.Caps.from_string(src_caps))
    for elem in (src, src_filter, sink_bin):
        pipeline.add(elem)
    src.link(src_filter)
    src_filter.link(sink_bin)
    pipeline.set_state(gst.State.PLAYING)
    msg = pipeline.get_bus().timed_pop_filtered(5 * gst.SECOND, gst.MessageType.EOS | gst.MessageType.ERROR)
    outer = sink_bin.get_static_pad("sink").get_current_caps().to_string()
    inner = sink_bin.get_by_name("inner").get_static_pad("sink").get_current_caps().to_string()
    pipeline.set_state(gst.State.NULL)
    assert msg is not None and msg.type == gst.MessageType.EOS
    return outer, inner


def _sink_bin(gst: Any) -> Any:
    return device_scaled_sink(gst, gst.ElementFactory.make("fakesink", "inner"), "scaled")


def test_frames_pass_at_their_own_size_until_a_device_size_is_set() -> None:
    gst = real_gst()
    outer, inner = _run(gst, _sink_bin(gst), "video/x-raw,format=BGRx,width=320,height=180")
    assert "width=(int)320" in outer and "width=(int)320" in inner


def test_frames_reach_the_inner_sink_at_the_device_size_letterboxed() -> None:
    gst = real_gst()
    sink_bin = _sink_bin(gst)
    set_device_size(gst, sink_bin, 400, 300)
    outer, inner = _run(gst, sink_bin, "video/x-raw,format=BGRx,width=320,height=180")
    assert "width=(int)320" in outer and "height=(int)180" in outer
    assert "width=(int)400" in inner and "height=(int)300" in inner
    assert "pixel-aspect-ratio=(fraction)1/1" in inner


@pytest.mark.parametrize(("width", "height"), [(0, 300), (400, 0), (-1, 300)])
def test_an_empty_device_size_is_ignored(width: int, height: int) -> None:
    gst = real_gst()
    sink_bin = _sink_bin(gst)
    set_device_size(gst, sink_bin, width, height)
    _outer, inner = _run(gst, sink_bin, "video/x-raw,format=BGRx,width=320,height=180")
    assert "width=(int)320" in inner


def test_the_device_size_follows_the_widget_allocation_times_its_scale() -> None:
    gst = real_gst()
    sink_bin = _sink_bin(gst)
    handlers: dict[str, Any] = {}
    widget = SimpleNamespace(
        connect=lambda signal, cb: handlers.__setitem__(signal, cb),
        get_scale_factor=lambda: 2,
    )
    follow_widget(gst, sink_bin, widget)
    handlers["size-allocate"](widget, SimpleNamespace(width=200, height=150))
    _outer, inner = _run(gst, sink_bin, "video/x-raw,format=BGRx,width=320,height=180")
    assert "width=(int)400" in inner and "height=(int)300" in inner


def test_the_same_device_size_twice_keeps_the_target_caps() -> None:
    gst = real_gst()
    sink_bin = _sink_bin(gst)
    set_device_size(gst, sink_bin, 400, 300)
    target = sink_bin.get_by_name("scaled_size")
    caps = target.get_property("caps")
    set_device_size(gst, sink_bin, 400, 300)
    assert target.get_property("caps") is caps or target.get_property("caps").is_equal(caps)
    _outer, inner = _run(gst, sink_bin, "video/x-raw,format=BGRx,width=320,height=180")
    assert "width=(int)400" in inner
