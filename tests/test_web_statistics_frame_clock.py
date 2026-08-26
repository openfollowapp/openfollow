# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Pin the Statistics panel's frame-clock indicator.

A station whose frame loop has stopped keeps transmitting the last known marker
position at full rate on PSN, OTP, RTTrPM, and OSC, so from a console it is
indistinguishable from a healthy stream. This chip is the operator-facing way to
tell the two apart, which makes its wiring worth pinning: rendering ``Running``
against a stalled loop would be worse than showing nothing at all.
"""

from __future__ import annotations

import pytest
from bottle import template

from openfollow.web import server as _server_module  # noqa: F401 – registers tpl path

pytestmark = pytest.mark.unit


def _render(playback: dict[str, object]) -> str:
    return template("partials/statistics", stats={"playback": playback})


def test_running_loop_renders_an_ok_chip() -> None:
    body = _render({"stalled": False, "seconds_since_last_frame": 0.017})
    assert "Frame clock" in body
    assert '<span class="stat-chip ok">Running</span>' in body


def test_stalled_loop_renders_an_alert_chip_with_the_outage_length() -> None:
    body = _render({"stalled": True, "seconds_since_last_frame": 42.4})
    assert '<span class="stat-chip off">Stalled 42 s</span>' in body


def test_stalled_loop_without_an_age_still_renders_the_alert() -> None:
    body = _render({"stalled": True, "seconds_since_last_frame": None})
    assert '<span class="stat-chip off">Stalled</span>' in body


def test_before_the_first_frame_reads_as_starting() -> None:
    body = _render({"stalled": False, "seconds_since_last_frame": None})
    assert '<span class="stat-chip warn">Starting</span>' in body


def test_the_threshold_comes_from_the_payload_not_a_literal() -> None:
    """The UI must judge staleness by the same threshold the outputs use. A
    literal here drifts silently the day ``MARKER_STALE_AFTER_S`` moves, and the
    chip would then contradict what PSN, OTP, RTTrPM and OSC are doing."""
    body = _render({"stalled": False, "seconds_since_last_frame": 2.5, "stale_after_s": 5.0})
    assert '<span class="stat-chip ok">Running</span>' in body
    body = _render({"stalled": False, "seconds_since_last_frame": 6.0, "stale_after_s": 5.0})
    assert '<span class="stat-chip off">Stalled 6 s</span>' in body


def test_a_missing_threshold_falls_back_rather_than_breaking() -> None:
    # A snapshot published before this field existed (an older peer).
    body = _render({"stalled": False, "seconds_since_last_frame": 8.0})
    assert '<span class="stat-chip off">Stalled 8 s</span>' in body


def test_a_growing_age_reads_as_stalled_even_when_the_watchdog_never_fired() -> None:
    """The watchdog runs on the housekeeping timeout - the same main loop as the
    frame clock - so a block inside one callback stops both, leaving ``stalled``
    False forever. The age is computed on the web thread and keeps growing, and
    rendering a green Running chip against it is exactly the false-healthy signal
    this whole change exists to remove."""
    body = _render({"stalled": False, "seconds_since_last_frame": 8.0})
    assert '<span class="stat-chip off">Stalled 8 s</span>' in body


@pytest.mark.parametrize("age", [0.0, 0.017, 0.9])
def test_a_healthy_age_still_reads_running(age: float) -> None:
    body = _render({"stalled": False, "seconds_since_last_frame": age})
    assert '<span class="stat-chip ok">Running</span>' in body


def test_missing_playback_section_does_not_break_the_panel() -> None:
    # A snapshot published by an older peer, or read before the first publish.
    body = template("partials/statistics", stats={})
    assert "Frame clock" in body


@pytest.mark.parametrize(("stalled", "age", "expected"), [(True, 0.01, "off"), (False, 0.01, "ok")])
def test_chip_colour_tracks_the_stall_flag(stalled: bool, age: float, expected: str) -> None:
    # Age held healthy so this isolates the flag; the age path has its own tests
    # above. A 1 s age is itself stalled, whatever the flag says.
    body = _render({"stalled": stalled, "seconds_since_last_frame": age})
    assert f'<span class="stat-chip {expected}">' in body
