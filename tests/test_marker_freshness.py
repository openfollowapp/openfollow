# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The shared rule behind every output protocol's staleness handling.

``build_marker_visual_state`` rewrites each controlled marker once per frame, so
``Marker.timestamp`` doubles as a per-marker "the frame loop ran" stamp. PSN,
OTP, RTTrPM, and the OSC transmitter each map that one rule onto their own
idiom, which is only safe while they agree on when a position stops counting -
hence the single source of truth these tests pin.
"""

from __future__ import annotations

import math

import pytest
from conftest import PsnStepClock as _StepClock

from openfollow.psn.marker import MARKER_STALE_AFTER_S, Marker, is_marker_stale, marker_age_s

pytestmark = pytest.mark.unit


def _aged(age_s: float, *, now_us: int = 10_000_000) -> Marker:
    """A locally-driven marker written ``age_s`` ago on its own clock.

    The clock is driven rather than the module function: ``Marker`` binds its
    clock at construction (``PsnServer`` passes one down), and that is the epoch
    a marker's age is measured in.
    """
    clock = _StepClock(now_us - int(age_s * 1_000_000))
    m = Marker(marker_id=1, name="T1", clock=clock)
    m.set_pos(1.0, 2.0, 3.0)
    clock.now = now_us  # time moves on; the stamp does not
    return m


class TestMarkerAge:
    def test_age_is_the_gap_since_the_last_write(self) -> None:
        assert marker_age_s(_aged(0.25)) == pytest.approx(0.25)

    def test_a_fresh_write_resets_the_age(self) -> None:
        clock = _StepClock(1_000_000)
        m = Marker(marker_id=1, name="T1", clock=clock)
        m.set_pos(0.0, 0.0, 0.0)
        clock.now = 9_000_000
        assert marker_age_s(m) == pytest.approx(8.0)
        m.set_speed(1.0, 0.0, 0.0)  # the per-frame speed write is what re-stamps
        assert marker_age_s(m) == pytest.approx(0.0)

    def test_age_is_measured_on_the_markers_own_clock(self) -> None:
        """``PsnServer`` passes its ``clock=`` down to every marker it creates,
        so a station driving an injected clock would otherwise have every one of
        its markers aged against an unrelated epoch and read as permanently
        stale - shipping STATUS 0.0 for a freshly written position."""
        clock = _StepClock(1_000)
        m = Marker(marker_id=1, name="T1", clock=clock)
        m.set_pos(1.0, 2.0, 3.0)
        assert marker_age_s(m) == pytest.approx(0.0)
        assert is_marker_stale(m) is False

    def test_now_us_pins_one_reference_for_a_whole_packet(self) -> None:
        # Ageing several markers against separate clock reads would give points
        # in one packet slightly different notions of "now".
        a, b = _aged(2.0, now_us=3_000_000), _aged(1.0, now_us=3_000_000)
        assert marker_age_s(a, now_us=3_000_000) == pytest.approx(2.0)
        assert marker_age_s(b, now_us=3_000_000) == pytest.approx(1.0)

    def test_never_written_marker_has_unknown_age(self) -> None:
        # Registered but never driven: there is no stamp to measure from, and
        # reporting 0 would publish it as live.
        assert marker_age_s(Marker(marker_id=1, name="T1")) == math.inf

    def test_remote_marker_age_is_unknowable(self) -> None:
        """A received marker's timestamp is in its *sender's* epoch. Subtracting
        our clock from it produces a number, just not a meaningful one."""
        clock = _StepClock(0)
        m = Marker(marker_id=1, name="T1", remote=True, clock=clock)
        m.apply_remote((0.0, 0.0, 0.0), timestamp=4_000_000, status=1.0)
        clock.now = 4_000_100
        assert marker_age_s(m) == math.inf


class TestIsMarkerStale:
    @pytest.mark.parametrize("age_s", [0.0, 0.25, MARKER_STALE_AFTER_S - 0.001])
    def test_a_recently_written_marker_is_live(self, age_s: float) -> None:
        assert is_marker_stale(_aged(age_s)) is False

    @pytest.mark.parametrize("age_s", [MARKER_STALE_AFTER_S, 5.0, 3600.0])
    def test_a_marker_the_loop_stopped_writing_goes_stale(self, age_s: float) -> None:
        assert is_marker_stale(_aged(age_s, now_us=4_000_000_000)) is True

    def test_unknown_age_fails_safe_as_stale(self) -> None:
        # Anything that can't be aged must not be published as a live position.
        assert is_marker_stale(Marker(marker_id=1, name="T1")) is True

    def test_threshold_is_wide_enough_to_ride_out_a_frame_hitch(self) -> None:
        """At the ~60 Hz frame clock this is roughly 60 missed frames. Tightening
        it towards a single frame time would make a GC pause or a config reload
        drop a marker off the wire mid-show."""
        assert MARKER_STALE_AFTER_S >= 0.5
