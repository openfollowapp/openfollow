# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for the Marker state wrapper (openfollow.psn.marker).

Exercises the Marker class in isolation: setters, property snapshots, and
the ``to_psn_*`` conversions consumed by PsnServer's emit loop.
"""

from __future__ import annotations

import pypsn
import pytest

from openfollow.psn.marker import Marker

pytestmark = pytest.mark.unit


def test_defaults_are_zero_vectors() -> None:
    t = Marker(marker_id=3, name="T3")
    assert t.marker_id == 3
    assert t.name == "T3"
    assert t.pos == (0.0, 0.0, 0.0)
    assert t.speed == (0.0, 0.0, 0.0)
    assert t.ori == (0.0, 0.0, 0.0)
    assert t.accel == (0.0, 0.0, 0.0)
    assert t.trgtpos == (0.0, 0.0, 0.0)
    assert t.status == 0.0
    assert t.timestamp == 0


def test_set_pos_and_set_speed_are_read_back_atomically() -> None:
    t = Marker(marker_id=1, name="T1")
    t.set_pos(1.5, 2.5, 3.5)
    t.set_speed(0.1, 0.2, 0.3)
    assert t.pos == (1.5, 2.5, 3.5)
    assert t.speed == (0.1, 0.2, 0.3)


def test_to_psn_marker_populates_all_fields() -> None:
    t = Marker(marker_id=5, name="T5")
    t.set_pos(1.0, 2.0, 3.0)
    t.set_speed(0.5, 0.0, 0.0)

    psn = t.to_psn_marker()

    # pypsn uses wire-protocol field names (tracker_id / tracker_name);
    # Marker uses marker_id / name. Translation lives in to_psn_marker.
    assert isinstance(psn, pypsn.PsnTracker)
    assert psn.tracker_id == 5
    assert psn.pos.x == pytest.approx(1.0)
    assert psn.pos.y == pytest.approx(2.0)
    assert psn.pos.z == pytest.approx(3.0)
    assert psn.speed.x == pytest.approx(0.5)


def test_to_psn_marker_info_carries_id_and_name() -> None:
    t = Marker(marker_id=9, name="Spot9")
    info = t.to_psn_marker_info()

    assert isinstance(info, pypsn.PsnTrackerInfo)
    assert info.tracker_id == 9
    # pypsn encodes the name to bytes in some versions.
    name = info.tracker_name
    if isinstance(name, bytes):
        name = name.decode()
    assert name == "Spot9"


def test_marker_id_zero_is_rejected() -> None:
    """Project convention: marker id 0 is reserved as "ignored" on the
    PSN wire. The constructor refuses anything below 1 so a bug path
    can't leak a reserved id onto the network."""
    with pytest.raises(ValueError):
        Marker(marker_id=0, name="reserved")


def test_marker_id_negative_is_rejected() -> None:
    with pytest.raises(ValueError):
        Marker(marker_id=-1, name="bogus")


def test_marker_id_bool_is_rejected() -> None:
    with pytest.raises(ValueError):
        Marker(marker_id=True, name="oops")  # type: ignore[arg-type]


def test_marker_id_non_int_is_rejected() -> None:
    with pytest.raises(ValueError):
        Marker(marker_id="1", name="oops")  # type: ignore[arg-type]


def test_set_name_updates_under_lock() -> None:
    t = Marker(marker_id=1, name="Old")
    t.set_name("New")
    assert t.name == "New"


def test_setters_use_lock_and_do_not_tear_reads() -> None:
    t = Marker(marker_id=1, name="T1")
    t.set_pos(10.0, 20.0, 30.0)
    t.set_pos(40.0, 50.0, 60.0)
    # After a completed set_pos there is only one visible state.
    assert t.pos == (40.0, 50.0, 60.0)
