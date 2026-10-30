# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Status & Diagnostics alerts: the boxes carry no role, one announcer speaks them."""

from __future__ import annotations

from typing import Any

import pytest
from bottle import html_escape, template

from openfollow.video.source_limits import OverLimit, SourceReading, limits_snapshot
from openfollow.web import server as _server_module  # noqa: F401 - registers tpl path
from openfollow.web.live_alerts import diagnostics_alerts
from tests._status_boxes import render_alerts, render_boxes

pytestmark = pytest.mark.unit

_MISSING_PAD = {
    "controller_index": 1,
    "state": "missing",
    "marker_id": 5,
    "name": "Xbox Wireless Controller",
    "port_label": "USB 2, port 2",
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
    assert diagnostics_alerts(_all_three()).spoken() == [
        "Nothing answered at the camera's address. Check the address and port.",
        "C2 missing · marker 5 · Xbox Wireless Controller (USB 2, port 2)",
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
    assert diagnostics_alerts(_stats(video=video)).video == expected


def test_a_controller_line_leaves_out_what_it_does_not_know() -> None:
    alerts = diagnostics_alerts(_stats(controllers={"items": [{"controller_index": 2, "state": "missing"}]}))
    assert alerts.controllers == ("C3 missing",)


def test_switched_off_detection_raises_no_alert() -> None:
    assert diagnostics_alerts(_stats(tracking={"enabled": False, "missing_deps": ["onnxruntime"]})).detection is None


def test_the_key_changes_only_with_what_is_said() -> None:
    first = diagnostics_alerts(_all_three()).key()
    assert diagnostics_alerts(_all_three()).key() == first
    changed = _all_three()
    changed["tracking"]["missing_deps"] = ["onnxruntime", "opencv-python"]
    assert diagnostics_alerts(changed).key() != first
    # A metric the boxes don't show moving on is not a change.
    moved = _all_three()
    moved["system"] = {"cpu_percent": 91.0}
    assert diagnostics_alerts(moved).key() == first


def test_the_panel_boxes_carry_no_role() -> None:
    html = render_boxes(_all_three())
    assert html.count('class="notice error"') == 3
    assert "role=" not in html
    assert "aria-live" not in html


def test_the_announcer_speaks_them_and_polls_with_its_key() -> None:
    stats = _all_three()
    html = render_alerts(stats)
    alerts = diagnostics_alerts(stats)
    assert html.startswith(
        '<div id="diagnostics-alerts" class="visually-hidden" role="alert" aria-live="assertive" aria-atomic="true"'
    )
    assert f'hx-get="/section/diagnostics/alerts?key={alerts.key()}"' in html
    assert 'hx-swap="outerHTML"' in html
    for line in alerts.spoken():
        assert f"<p>{html_escape(line)}</p>" in html


def test_a_quiet_announcer_says_nothing() -> None:
    assert "<p>" not in render_alerts(_stats())


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


def _power(**flags: bool | None) -> dict[str, Any]:
    power: dict[str, Any] = {
        "available": True,
        "under_voltage": False,
        "throttled": False,
        "under_voltage_since_boot": False,
        "throttled_since_boot": False,
    }
    power.update(flags)
    return _stats(system={"power": power})


_UV_NOW = (
    "error",
    "Power supply under voltage now.",
    "Use a 5 V 5 A supply or unplug bus-powered USB devices.",
)
_HOT_NOW = ("error", "CPU throttled now: the Pi is too hot.", "Check the fan and airflow.")
_UV_PAST = ("caution", "Under voltage reported since the station last started.", "")
_HOT_PAST = ("caution", "CPU throttling reported since the station last started.", "")


@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        ({}, []),
        ({"under_voltage": True, "under_voltage_since_boot": True}, [_UV_NOW]),
        ({"throttled": True, "throttled_since_boot": True}, [_HOT_NOW]),
        ({"under_voltage_since_boot": True}, [_UV_PAST]),
        ({"throttled_since_boot": True}, [_HOT_PAST]),
        ({"under_voltage_since_boot": True, "throttled_since_boot": True}, [_UV_PAST, _HOT_PAST]),
        ({"under_voltage_since_boot": True, "throttled": True, "throttled_since_boot": True}, [_HOT_NOW, _UV_PAST]),
        ({"under_voltage": True, "throttled": True, "throttled_since_boot": True}, [_UV_NOW]),
        ({"under_voltage": True, "throttled_since_boot": True}, [_UV_NOW, _HOT_PAST]),
    ],
    ids=[
        "healthy",
        "under-voltage-now",
        "too-hot-now",
        "under-voltage-past",
        "throttled-past",
        "both-past",
        "errors-first",
        "supply-named-over-heat",
        "past-throttle-beside-live-supply",
    ],
)
def test_a_power_alert_is_an_error_while_active_and_a_caution_after(
    flags: dict[str, bool], expected: list[tuple[str, str, str]]
) -> None:
    alerts = diagnostics_alerts(_power(**flags)).power
    assert [(a.level, a.text, a.step) for a in alerts] == expected


@pytest.mark.parametrize(
    "stats",
    [
        _stats(),
        _stats(system={}),
        _stats(system={"power": None}),
        _power(
            available=False,
            under_voltage=None,
            throttled=None,
            under_voltage_since_boot=None,
            throttled_since_boot=None,
        ),
    ],
    ids=["no-system", "no-power", "power-null", "not-available"],
)
def test_a_host_that_cannot_tell_shows_no_power_alert(stats: dict[str, Any]) -> None:
    assert diagnostics_alerts(stats).power == ()
    html = render_boxes(stats)
    assert "notice warning" not in html
    assert "under voltage" not in html


def test_the_power_boxes_take_their_level_component() -> None:
    html = render_boxes(_power(under_voltage=True, throttled_since_boot=True))
    assert html.count('class="notice error"') == 1
    assert html.count('class="notice warning"') == 1
    assert (
        "<div>Power supply under voltage now.</div>\n"
        '            <div class="notice-sub">Use a 5 V 5 A supply or unplug bus-powered USB devices.</div>'
    ) in html
    caution = html[html.index('class="notice warning"') :]
    assert caution.split("</div>\n        </div>")[0].endswith(
        "<div>CPU throttling reported since the station last started."
    )
    assert "role=" not in html


def test_the_announcer_speaks_the_power_alerts_after_the_controllers() -> None:
    stats = _power(under_voltage=True, throttled_since_boot=True)
    stats["controllers"] = {"items": [_MISSING_PAD]}
    assert diagnostics_alerts(stats).spoken() == [
        "C2 missing · marker 5 · Xbox Wireless Controller (USB 2, port 2)",
        "Power supply under voltage now. Use a 5 V 5 A supply or unplug bus-powered USB devices.",
        "CPU throttling reported since the station last started.",
    ]
    html = render_alerts(stats)
    assert "<p>CPU throttling reported since the station last started.</p>" in html


def test_the_key_changes_when_a_power_alert_ends() -> None:
    live = diagnostics_alerts(_power(under_voltage=True, under_voltage_since_boot=True)).key()
    assert diagnostics_alerts(_power(under_voltage_since_boot=True)).key() != live


_OVER = limits_snapshot(SourceReading(3840, 2160, 60.0, (OverLimit.RESOLUTION, OverLimit.FRAME_RATE)))


def _over_limits(**video: Any) -> dict[str, Any]:
    return _stats(video={"connected": True, "limits": _OVER, **video})


def test_a_source_over_the_limits_is_a_box_beside_the_video_panel() -> None:
    html = render_boxes(_over_limits())
    video = html[: html.index('<h3 class="stat-panel-title">This Station</h3>')]
    assert video.count('class="notice error"') == 1
    assert "<div>Video arrives at 3840×2160 and 60 fps, over Full HD (1920×1080) and 30 fps.</div>" in video
    assert '<div class="notice-sub">Set the camera or encoder to 1920×1080 at 30 fps or less.</div>' in video
    assert 'id="video-error-stats-limits-' in video
    assert "role=" not in video


@pytest.mark.parametrize(
    "limits",
    [None, {}, limits_snapshot(None), limits_snapshot(SourceReading(1920, 1080, 30.0, ()))],
    ids=["absent", "empty", "unmeasured", "within"],
)
def test_a_source_within_the_limits_raises_no_box(limits: dict[str, Any] | None) -> None:
    stats = _stats(video={"connected": True, "limits": limits})
    assert diagnostics_alerts(stats).video_limits is None
    assert 'class="notice error"' not in render_boxes(stats)


def test_the_announcer_speaks_the_limits_after_the_video_failure() -> None:
    stats = _over_limits(
        connected=False,
        failure="stalled",
        failure_text="Video stopped arriving.",
        failure_action="Check the camera.",
    )
    assert diagnostics_alerts(stats).spoken()[:2] == [
        "Video stopped arriving. Check the camera.",
        "Video arrives at 3840×2160 and 60 fps, over Full HD (1920×1080) and 30 fps. "
        "Set the camera or encoder to 1920×1080 at 30 fps or less.",
    ]


def test_the_key_changes_when_the_source_comes_within_the_limits() -> None:
    over = diagnostics_alerts(_over_limits()).key()
    assert diagnostics_alerts(_stats(video={"connected": True, "limits": limits_snapshot(None)})).key() != over
