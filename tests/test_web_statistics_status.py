# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The Statistics panel names each state at its level: error, caution, info, success or neutral."""

from __future__ import annotations

import pytest
from bottle import template

from openfollow.web import server as _server_module  # noqa: F401 - registers tpl path

pytestmark = pytest.mark.unit


def _render(tracking: dict[str, object]) -> str:
    return template("partials/statistics", stats={"tracking": tracking})


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
