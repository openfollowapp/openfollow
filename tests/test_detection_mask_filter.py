# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for ``filter_detections_to_masks`` – the region-of-interest filter
that confines person detection to operator-drawn polygons.

Masks are normalised 0-1 frame polygons; a detection passes when its box
bottom-centre (feet) lies inside the union of enabled masks. With no usable
mask, every box passes through (masking is opt-in)."""

from __future__ import annotations

import pytest

from openfollow.configuration import DetectionMaskConfig
from openfollow.video.detection import DetectionBox, filter_detections_to_masks

pytestmark = pytest.mark.unit


# A unit square covering the top-left quadrant of the frame.
_TOP_LEFT = DetectionMaskConfig(vertices=[[0.0, 0.0], [0.5, 0.0], [0.5, 0.5], [0.0, 0.5]])
# A unit square covering the bottom-right quadrant.
_BOTTOM_RIGHT = DetectionMaskConfig(vertices=[[0.5, 0.5], [1.0, 0.5], [1.0, 1.0], [0.5, 1.0]])


def _box(cx: float, feet_y: float, half_w: float = 0.05, height: float = 0.2) -> DetectionBox:
    """Build a box whose bottom-centre (feet) is at ``(cx, feet_y)``."""
    return DetectionBox(x1=cx - half_w, y1=feet_y - height, x2=cx + half_w, y2=feet_y, confidence=0.9)


def test_no_masks_passes_everything_through() -> None:
    boxes = [_box(0.5, 0.5), _box(0.1, 0.9)]
    assert filter_detections_to_masks(boxes, []) == boxes


def test_all_disabled_masks_passes_everything_through() -> None:
    disabled = DetectionMaskConfig(vertices=[[0.0, 0.0], [0.5, 0.0], [0.5, 0.5]], enabled=False)
    boxes = [_box(0.1, 0.1), _box(0.9, 0.9)]
    assert filter_detections_to_masks(boxes, [disabled]) == boxes


def test_box_with_feet_inside_mask_is_kept() -> None:
    inside = _box(0.25, 0.25)  # feet at (0.25, 0.25) – inside top-left
    assert filter_detections_to_masks([inside], [_TOP_LEFT]) == [inside]


def test_box_with_feet_outside_mask_is_dropped() -> None:
    outside = _box(0.9, 0.9)  # feet at (0.9, 0.9) – outside top-left
    assert filter_detections_to_masks([outside], [_TOP_LEFT]) == []


def test_union_of_two_masks_keeps_boxes_in_either() -> None:
    in_tl = _box(0.25, 0.25)
    in_br = _box(0.75, 0.75)
    in_neither = _box(0.75, 0.25)  # top-right quadrant – in neither mask
    kept = filter_detections_to_masks([in_tl, in_br, in_neither], [_TOP_LEFT, _BOTTOM_RIGHT])
    assert kept == [in_tl, in_br]


def test_uses_feet_not_centre() -> None:
    # Box centre is inside the top-left mask, but the feet are below it.
    # The feet anchor must drop this box.
    box = DetectionBox(x1=0.2, y1=0.2, x2=0.3, y2=0.8, confidence=0.9)
    assert (box.x1 + box.x2) / 2.0 == 0.25  # centre x in-range
    assert filter_detections_to_masks([box], [_TOP_LEFT]) == []


def test_degenerate_mask_under_three_vertices_matches_nothing() -> None:
    two_pt = DetectionMaskConfig(vertices=[[0.0, 0.0], [1.0, 1.0]])
    # No usable polygon → treated as "no mask" → everything passes.
    boxes = [_box(0.25, 0.25), _box(0.9, 0.9)]
    assert filter_detections_to_masks(boxes, [two_pt]) == boxes


def test_disabled_mask_ignored_but_enabled_mask_applies() -> None:
    disabled_br = DetectionMaskConfig(vertices=_BOTTOM_RIGHT.vertices, enabled=False)
    in_tl = _box(0.25, 0.25)
    in_br = _box(0.75, 0.75)
    kept = filter_detections_to_masks([in_tl, in_br], [_TOP_LEFT, disabled_br])
    assert kept == [in_tl]
