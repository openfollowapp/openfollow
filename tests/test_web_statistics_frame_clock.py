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


def test_missing_playback_section_does_not_break_the_panel() -> None:
    # A snapshot published by an older peer, or read before the first publish.
    body = template("partials/statistics", stats={})
    assert "Frame clock" in body


@pytest.mark.parametrize("stalled", [True, False])
def test_chip_colour_tracks_the_stall_flag(stalled: bool) -> None:
    body = _render({"stalled": stalled, "seconds_since_last_frame": 1.0})
    expected = "off" if stalled else "ok"
    assert f'<span class="stat-chip {expected}">' in body
