# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Status & Diagnostics names each state at its level: error, caution, info, success or neutral."""

from __future__ import annotations

import re

import pytest

from openfollow.web import server as _server_module  # noqa: F401 - registers tpl path
from tests._status_boxes import render_alerts, render_boxes

pytestmark = pytest.mark.unit


def _render(tracking: dict[str, object]) -> str:
    """The Person Detection box: other boxes carry chips of their own."""
    body = render_boxes({"tracking": tracking})
    return body[body.index('<h3 class="stat-panel-title">Person Detection</h3>') :]


@pytest.mark.parametrize(
    ("tracking", "chip"),
    [
        ({"enabled": False}, '<span class="stat-chip">Off</span>'),
        ({"enabled": False, "missing_deps": ["onnxruntime"]}, '<span class="stat-chip">Off</span>'),
        ({"enabled": True, "running": False}, '<span class="stat-chip info">Idle</span>'),
        ({"enabled": True, "running": True}, '<span class="stat-chip ok">Running</span>'),
        # It does not work until someone acts, so it is an error, never a caution.
        ({"enabled": True, "missing_deps": ["onnxruntime"]}, '<span class="stat-chip off">Unavailable</span>'),
    ],
)
def test_person_detection_chip_takes_the_level_of_its_state(tracking: dict[str, object], chip: str) -> None:
    assert chip in _render(tracking)


def test_missing_packages_is_the_error_box_with_the_next_step_below() -> None:
    body = _render({"enabled": True, "missing_deps": ["onnxruntime", "opencv-python"]})
    box = body.split('<div class="notice error">', 1)[1].split("\n        </div>", 1)[0]
    assert "<div>Missing packages: onnxruntime, opencv-python.</div>" in box
    assert '<div class="notice-sub">Install them from the Person Detection section, then restart.</div>' in box


def test_switched_off_detection_raises_no_missing_packages_box() -> None:
    assert "Missing packages" not in _render({"enabled": False, "missing_deps": ["onnxruntime"]})


def test_with_the_feature_off_nothing_speaks_for_the_box() -> None:
    # The runtime stats report detection as not enabled while its feature is off.
    stats = {"tracking": {"enabled": False, "missing_deps": ["onnxruntime"]}}
    assert "Missing packages" not in render_boxes(stats)
    assert "Missing packages" not in render_alerts(stats)


def test_the_box_shows_through_the_detection_feature() -> None:
    # The page's own feature switch hides it at once, as every other detection surface.
    body = render_boxes({})
    assert '<section class="stat-panel diag-detection" data-feature="detection">' in body
    assert re.findall(r'<h3 class="stat-panel-title">([^<]+)</h3>', body) == [
        "Video",
        "This Station",
        "Station network discovery",
        "Person Detection",
    ]
