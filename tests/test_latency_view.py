# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Michel Honold and Paul Hermann
"""What the Camera Latency row and its Measure dialog show, from the stats and the run's status."""

from __future__ import annotations

import time

import pytest

from openfollow.web.latency_view import latency_dialog_view, latency_row_view, measured_when

pytestmark = pytest.mark.unit

# Noon of a fixed day in the station's own zone, so "today" never crosses midnight.
_NOON = time.mktime((2026, 10, 26, 12, 0, 0, 0, 0, -1))
_DECODING = {"video": {"source_type": "rtsp", "connected": True, "phase": "decoding"}, "hud": {"window_s": 2.0}}
_LAST = {"median_ms": 64.2, "min_ms": 37.0, "max_ms": 79.4, "samples": 10, "finished_at": _NOON - 3600.0}


def test_a_run_from_today_reads_with_its_time() -> None:
    assert measured_when(_NOON - 3600.0, _NOON) == "Measured today at 11:00"


def test_an_older_run_reads_with_its_date() -> None:
    earlier = time.mktime((2026, 10, 23, 12, 0, 0, 0, 0, -1))
    assert measured_when(earlier, _NOON) == "Measured on 2026-10-23 at 12:00"


def test_the_row_shows_the_last_result_and_when() -> None:
    view = latency_row_view({**_DECODING, "latency": _LAST}, gallery=False, now=_NOON)
    assert view["unavailable"] is None
    assert view["last"] == {"median_ms": 64.2, "min_ms": 37.0, "max_ms": 79.4, "when": "Measured today at 11:00"}


def test_the_row_key_follows_what_it_shows() -> None:
    before = latency_row_view(_DECODING, gallery=False, now=_NOON)
    after = latency_row_view({**_DECODING, "latency": _LAST}, gallery=False, now=_NOON)
    same = latency_row_view(_DECODING, gallery=False, now=_NOON + 60.0)
    assert before["key"] != after["key"]
    assert before["key"] == same["key"]


@pytest.mark.parametrize(
    ("status", "before_start", "state"),
    [
        ({"state": "idle"}, True, "intro"),
        ({"state": "done", "result": {"level": "success"}}, True, "intro"),
        ({"state": "running", "measured": 3}, True, "running"),
        ({"state": "running", "measured": 3}, False, "running"),
        ({"state": "done", "result": {"level": "caution"}}, False, "done"),
        ({"state": "failed", "failure": {"reason": "no_rise"}}, False, "failed"),
        ({"state": "idle"}, False, "intro"),
    ],
)
def test_the_dialog_opens_on_its_warnings_unless_a_run_is_going(status: dict, before_start: bool, state: str) -> None:
    assert latency_dialog_view(status, before_start=before_start)["state"] == state


@pytest.mark.parametrize(("level", "notice"), [("success", "success"), ("caution", "warning"), ("error", "error")])
def test_a_result_takes_the_box_of_its_level(level: str, notice: str) -> None:
    view = latency_dialog_view({"state": "done", "result": {"level": level}}, before_start=False)
    assert view["result_class"] == notice
