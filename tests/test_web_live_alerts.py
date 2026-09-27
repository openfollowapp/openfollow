# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Live Statistics alerts: the boxes carry no role, one announcer speaks them."""

from __future__ import annotations

from typing import Any

import pytest
from bottle import html_escape, template

from openfollow.web import server as _server_module  # noqa: F401 - registers tpl path
from openfollow.web.live_alerts import statistics_alerts

pytestmark = pytest.mark.unit

_MISSING_PAD = {
    "controller_index": 1,
    "state": "missing",
    "marker_id": 5,
    "name": "Xbox Wireless Controller",
    "port_label": "USB 2 · port 2",
}


def _stats(**sections: Any) -> dict[str, Any]:
    stats: dict[str, Any] = {"video": {"connected": True}, "controllers": {"items": []}, "tracking": {}}
    stats.update(sections)
    return stats


def _all_three() -> dict[str, Any]:
    return _stats(
        video={
            "connected": False,
            "failure": "unreachable",
            "failure_text": "Nothing answered at the camera's address.",
            "failure_action": "Check the address and port.",
            "error_message": "Could not open resource for reading.",
        },
        controllers={"items": [{"controller_index": 0, "state": "connected"}, _MISSING_PAD]},
        tracking={"enabled": True, "missing_deps": ["onnxruntime"]},
    )


def test_everything_the_boxes_say_is_spoken_in_page_order() -> None:
    assert statistics_alerts(_all_three()).spoken() == [
        "Nothing answered at the camera's address. Check the address and port.",
        "C2 missing · marker 5 · Xbox Wireless Controller (USB 2 · port 2)",
        "Missing packages: onnxruntime. Install them from the Person Detection section, then restart.",
    ]


@pytest.mark.parametrize(
    ("video", "expected"),
    [
        ({"connected": True, "error_message": "stale"}, None),
        ({"connected": False}, None),
        ({"connected": False, "error_message": "raw", "failure": "unknown", "failure_text": "hidden"}, ("raw", "")),
        ({"connected": False, "failure": "none", "failure_text": "stale", "error_message": "raw"}, ("raw", "")),
    ],
    ids=["connected", "no-reason", "unknown-shows-raw", "none-shows-raw"],
)
def test_the_video_alert_matches_the_box(video: dict[str, Any], expected: tuple[str, str] | None) -> None:
    assert statistics_alerts(_stats(video=video)).video == expected


def test_a_controller_line_leaves_out_what_it_does_not_know() -> None:
    alerts = statistics_alerts(_stats(controllers={"items": [{"controller_index": 2, "state": "missing"}]}))
    assert alerts.controllers == ("C3 missing",)


def test_switched_off_detection_raises_no_alert() -> None:
    assert statistics_alerts(_stats(tracking={"enabled": False, "missing_deps": ["onnxruntime"]})).detection is None


def test_the_key_changes_only_with_what_is_said() -> None:
    first = statistics_alerts(_all_three()).key()
    assert statistics_alerts(_all_three()).key() == first
    changed = _all_three()
    changed["tracking"]["missing_deps"] = ["onnxruntime", "opencv-python"]
    assert statistics_alerts(changed).key() != first
    # A metric the boxes don't show moving on is not a change.
    moved = _all_three()
    moved["system"] = {"cpu_percent": 91.0}
    assert statistics_alerts(moved).key() == first


def test_the_panel_boxes_carry_no_role() -> None:
    html = template("partials/statistics", stats=_all_three())
    assert html.count('class="notice error"') == 3
    assert "role=" not in html
    assert "aria-live" not in html


def test_the_announcer_speaks_them_and_polls_with_its_key() -> None:
    stats = _all_three()
    html = template("partials/statistics_alerts", stats=stats)
    alerts = statistics_alerts(stats)
    assert html.startswith(
        '<div id="statistics-alerts" class="visually-hidden" role="alert" aria-live="assertive" aria-atomic="true"'
    )
    assert f'hx-get="/section/statistics/alerts?key={alerts.key()}"' in html
    assert 'hx-swap="outerHTML"' in html
    for line in alerts.spoken():
        assert f"<p>{html_escape(line)}</p>" in html


def test_a_quiet_announcer_says_nothing() -> None:
    assert "<p>" not in template("partials/statistics_alerts", stats=_stats())


def test_the_video_source_box_keeps_its_polite_role() -> None:
    html = template(
        "partials/video_error_box",
        failure_text="Nothing answered.",
        error_message="",
        action="",
        token="t",
        scope="source",
        live="status",
    )
    assert 'role="status" aria-live="polite" aria-atomic="true"' in html
