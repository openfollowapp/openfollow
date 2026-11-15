# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Paul Hermann and Michel Honold
"""A gtksink fed frames already scaled to its widget's device pixels.

gtksink scales each frame to its widget on the GTK thread with cairo's bilinear
filter. On a HiDPI screen at fullscreen size that paint takes longer than a
display frame, and the whole Operator Screen (pointer, keys, HUD) runs at its
pace. Scaled on the streaming thread instead, gtksink's paint is a 1:1 copy.
"""

from __future__ import annotations

from typing import Any

SCALER_THREADS = 4


def device_scaled_sink(gst: Any, sink: Any, name: str) -> Any:
    """A bin named ``name``: a scaler, its target caps, then ``sink``, behind a ghost ``sink`` pad.

    The ghost pad carries the source caps and buffers, so probes on it see the
    source picture, not the scaled one. Until ``set_device_size`` is called
    the frames pass at their own size.
    """
    scale = gst.ElementFactory.make("videoscale", f"{name}_scale")
    target = gst.ElementFactory.make("capsfilter", f"{name}_size")
    scale.set_property("n-threads", SCALER_THREADS)
    sink_bin = gst.Bin.new(name)
    for elem in (scale, target, sink):
        sink_bin.add(elem)
    scale.link(target)
    target.link(sink)
    sink_bin.add_pad(gst.GhostPad.new("sink", scale.get_static_pad("sink")))
    return sink_bin


def set_device_size(gst: Any, sink_bin: Any, width: int, height: int) -> None:
    """Scale the frames entering ``sink_bin`` to ``width`` x ``height`` square pixels, letterboxed."""
    target = sink_bin.get_by_name(f"{sink_bin.get_name()}_size")
    if target is None or width < 1 or height < 1:
        return
    caps = gst.Caps.from_string(f"video/x-raw,width={width},height={height},pixel-aspect-ratio=1/1")
    current = target.get_property("caps")
    if current is None or not current.is_equal(caps):
        target.set_property("caps", caps)


def follow_widget(gst: Any, sink_bin: Any, widget: Any) -> None:
    """Keep ``sink_bin``'s target size at ``widget``'s allocation in device pixels."""

    def _on_allocate(w: Any, allocation: Any) -> None:
        scale = w.get_scale_factor()
        set_device_size(gst, sink_bin, allocation.width * scale, allocation.height * scale)

    widget.connect("size-allocate", _on_allocate)
