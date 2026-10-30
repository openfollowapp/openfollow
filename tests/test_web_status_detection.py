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


def test_with_the_feature_off_there_is_no_box_and_no_alert() -> None:
    # The runtime stats report detection as not enabled while its feature is off.
    stats = {"tracking": {"enabled": False, "missing_deps": ["onnxruntime"]}}
    body = render_boxes(stats, detection_shown=False)
    assert "Person Detection" not in body
    assert "Missing packages" not in body
    assert '<div class="stats-columns diag-bento">' in body
    assert "Missing packages" not in render_alerts(stats)


def test_with_the_feature_on_it_is_the_fourth_box() -> None:
    body = render_boxes({})
    assert '<div class="stats-columns diag-bento with-detection">' in body
    assert re.findall(r'<h3 class="stat-panel-title">([^<]+)</h3>', body) == [
        "Video",
        "This Station",
        "Station network discovery",
        "Person Detection",
    ]
