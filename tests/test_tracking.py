# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for the ByteTrack tracker: Kalman filter, two-stage association,
track lifecycle, and id stability.

The tracker takes an explicit ``now`` per update, so these tests advance time
by passing timestamps – no clock monkeypatching needed. Detection boxes use the
real :class:`DetectionBox` (a plain dataclass; importing it does not require
``cv2`` because the detection module guards that import)."""

from __future__ import annotations

import numpy as np
import pytest

from openfollow.video.detection import DetectionBox
from openfollow.video.tracking import (
    LOW_DETECTION_THRESHOLD,
    ByteTracker,
    _iou_matrix,
    _KalmanFilter,
    _tlbr_to_xyah,
    _xyah_to_tlbr,
)

pytestmark = pytest.mark.unit


def _box(x1: float, y1: float, x2: float, y2: float, conf: float) -> DetectionBox:
    return DetectionBox(x1=x1, y1=y1, x2=x2, y2=y2, confidence=conf)


def _tracked_ids(tracks: list, *, state: str | None = None) -> set[int]:
    return {t.track_id for t in tracks if state is None or t.state == state}


# --------------------------------------------------------------------------- #
# IoU matrix
# --------------------------------------------------------------------------- #


def test_iou_matrix_matches_known_overlap() -> None:
    tracks = np.array([[0.0, 0.0, 1.0, 1.0]])
    dets = np.array([[0.5, 0.5, 1.5, 1.5]])
    # intersection 0.25, union 1.75 -> 1/7
    assert _iou_matrix(tracks, dets)[0, 0] == pytest.approx(1.0 / 7.0)


def test_iou_matrix_is_zero_for_disjoint_boxes() -> None:
    tracks = np.array([[0.0, 0.0, 0.1, 0.1]])
    dets = np.array([[0.5, 0.5, 0.6, 0.6]])
    assert _iou_matrix(tracks, dets)[0, 0] == 0.0


def test_iou_matrix_shape_is_tracks_by_dets() -> None:
    tracks = np.array([[0.0, 0.0, 1.0, 1.0], [0.0, 0.0, 0.5, 0.5]])
    dets = np.array([[0.0, 0.0, 1.0, 1.0]])
    assert _iou_matrix(tracks, dets).shape == (2, 1)


# --------------------------------------------------------------------------- #
# Box <-> measurement round trip
# --------------------------------------------------------------------------- #


def test_tlbr_xyah_round_trip() -> None:
    box = _box(0.2, 0.3, 0.6, 0.9, 0.9)
    x1, y1, x2, y2 = _xyah_to_tlbr(_tlbr_to_xyah(box))
    assert (x1, y1, x2, y2) == pytest.approx((0.2, 0.3, 0.6, 0.9))


# --------------------------------------------------------------------------- #
# Kalman filter
# --------------------------------------------------------------------------- #


def test_kalman_update_pulls_state_toward_measurement() -> None:
    kf = _KalmanFilter()
    mean, cov = kf.initiate(_tlbr_to_xyah(_box(0.10, 0.10, 0.40, 0.60, 0.9)))
    measurement = _tlbr_to_xyah(_box(0.20, 0.10, 0.50, 0.60, 0.9))  # cx shifted right
    new_mean, _new_cov = kf.update(mean, cov, measurement)
    # The posterior centre-x sits closer to the measurement than the prior did.
    assert abs(new_mean[0] - measurement[0]) < abs(mean[0] - measurement[0])


def test_kalman_predict_extrapolates_established_velocity() -> None:
    kf = _KalmanFilter()
    mean, cov = kf.initiate(_tlbr_to_xyah(_box(0.10, 0.10, 0.40, 0.60, 0.9)))
    # Two rightward updates build a positive cx velocity.
    for cx1 in (0.20, 0.30):
        mean, cov = kf.predict(mean, cov)
        mean, cov = kf.update(mean, cov, _tlbr_to_xyah(_box(cx1 - 0.15, 0.10, cx1 + 0.15, 0.60, 0.9)))
    cx_before = float(mean[0])
    mean, _cov = kf.predict(mean, cov)
    assert float(mean[0]) > cx_before  # motion continues without a measurement


# --------------------------------------------------------------------------- #
# ByteTracker – association + lifecycle
# --------------------------------------------------------------------------- #


def test_high_detection_spawns_tracked_track() -> None:
    bt = ByteTracker()
    tracks = bt.update([_box(0.1, 0.1, 0.4, 0.6, 0.9)], [], now=0.0, max_lost_time=0.5)
    assert len(tracks) == 1
    assert tracks[0].state == "tracked"
    assert tracks[0].track_id == 0


def test_low_only_detection_does_not_spawn_track() -> None:
    bt = ByteTracker()
    tracks = bt.update([], [_box(0.1, 0.1, 0.4, 0.6, 0.3)], now=0.0, max_lost_time=0.5)
    assert tracks == []


def test_low_confidence_box_recovers_existing_track_id() -> None:
    """The headline ByteTrack win: a performer dimming into shadow (high->low
    score) stays bound to their track instead of being dropped + re-numbered."""
    bt = ByteTracker()
    first = bt.update([_box(0.10, 0.10, 0.40, 0.60, 0.9)], [], now=0.0, max_lost_time=0.5)
    tid = first[0].track_id

    second = bt.update([], [_box(0.11, 0.11, 0.41, 0.61, 0.3)], now=0.03, max_lost_time=0.5)
    assert len(second) == 1
    assert second[0].track_id == tid
    assert second[0].state == "tracked"


def test_unmatched_track_goes_lost_then_expires() -> None:
    bt = ByteTracker()
    first = bt.update([_box(0.1, 0.1, 0.4, 0.6, 0.9)], [], now=0.0, max_lost_time=0.5)
    tid = first[0].track_id

    # Within the retention window: retained but marked lost.
    lost = bt.update([], [], now=0.2, max_lost_time=0.5)
    assert tid in _tracked_ids(lost, state="lost")

    # Past the window: removed entirely.
    gone = bt.update([], [], now=1.0, max_lost_time=0.5)
    assert gone == []


def test_lost_track_revived_by_high_detection_keeps_id() -> None:
    bt = ByteTracker()
    first = bt.update([_box(0.1, 0.1, 0.4, 0.6, 0.9)], [], now=0.0, max_lost_time=1.0)
    tid = first[0].track_id
    bt.update([], [], now=0.1, max_lost_time=1.0)  # -> lost

    revived = bt.update([_box(0.12, 0.12, 0.42, 0.62, 0.9)], [], now=0.2, max_lost_time=1.0)
    assert revived[0].track_id == tid
    assert revived[0].state == "tracked"


def test_lost_track_revived_by_overlapping_low_box_keeps_id() -> None:
    """A performer who dims below the high threshold while their track is already
    lost is recovered by the low-band pass and keeps their id, instead of being
    dropped and re-numbered when they brighten again."""
    bt = ByteTracker()
    first = bt.update([_box(0.10, 0.10, 0.40, 0.60, 0.9)], [], now=0.0, max_lost_time=1.0)
    tid = first[0].track_id
    bt.update([], [], now=0.1, max_lost_time=1.0)  # -> lost

    revived = bt.update([], [_box(0.11, 0.11, 0.41, 0.61, 0.3)], now=0.2, max_lost_time=1.0)
    assert len(revived) == 1
    assert revived[0].track_id == tid
    assert revived[0].state == "tracked"


def test_already_lost_track_is_not_revived_by_distant_low_box() -> None:
    """Low-band recovery reaches already-lost tracks, but the strict
    _LOW_IOU_GATE still rejects a low box that doesn't overlap the track's
    predicted position – a noisy ghost elsewhere must not resurrect it."""
    bt = ByteTracker()
    bt.update([_box(0.1, 0.1, 0.4, 0.6, 0.9)], [], now=0.0, max_lost_time=1.0)
    bt.update([], [], now=0.1, max_lost_time=1.0)  # track now lost

    tracks = bt.update([], [_box(0.70, 0.70, 0.95, 0.95, 0.3)], now=0.2, max_lost_time=1.0)
    assert _tracked_ids(tracks, state="tracked") == set()


def test_track_id_stable_across_motion() -> None:
    bt = ByteTracker()
    tid: int | None = None
    for step in range(6):
        x = 0.1 + step * 0.05
        tracks = bt.update([_box(x, 0.1, x + 0.3, 0.6, 0.9)], [], now=step * 0.03, max_lost_time=0.5)
        assert len(tracks) == 1
        if tid is None:
            tid = tracks[0].track_id
        assert tracks[0].track_id == tid


def test_lost_track_box_extrapolates_along_motion() -> None:
    bt = ByteTracker()
    last_x = 0.0
    for step in range(5):
        last_x = 0.1 + step * 0.06
        bt.update([_box(last_x, 0.1, last_x + 0.3, 0.6, 0.9)], [], now=step * 0.03, max_lost_time=1.0)
    track = bt.tracks[0]
    cx_tracked = (track.tlbr[0] + track.tlbr[2]) / 2.0

    # Drop detections: the lost track's reported box should keep moving right.
    bt.update([], [], now=0.2, max_lost_time=1.0)
    track = bt.tracks[0]
    assert track.state == "lost"
    cx_lost = (track.tlbr[0] + track.tlbr[2]) / 2.0
    assert cx_lost > cx_tracked


def test_two_people_get_distinct_ids() -> None:
    bt = ByteTracker()
    tracks = bt.update(
        [_box(0.0, 0.0, 0.2, 0.4, 0.9), _box(0.6, 0.0, 0.8, 0.4, 0.9)],
        [],
        now=0.0,
        max_lost_time=0.5,
    )
    assert len(_tracked_ids(tracks)) == 2


def test_two_tracks_keep_distinct_ids_across_frames() -> None:
    """A 2x2 association: each detection overlaps only its own track, so the
    cross pairs fall below the IoU gate and must be rejected (not matched)."""
    bt = ByteTracker()
    f1 = bt.update(
        [_box(0.00, 0.0, 0.20, 0.4, 0.9), _box(0.60, 0.0, 0.80, 0.4, 0.9)],
        [],
        now=0.0,
        max_lost_time=0.5,
    )
    ids1 = sorted(t.track_id for t in f1)
    f2 = bt.update(
        [_box(0.02, 0.0, 0.22, 0.4, 0.9), _box(0.62, 0.0, 0.82, 0.4, 0.9)],
        [],
        now=0.03,
        max_lost_time=0.5,
    )
    ids2 = sorted(t.track_id for t in f2)
    assert ids1 == ids2
    assert len(set(ids2)) == 2


def test_reset_clears_tracks_and_id_counter() -> None:
    bt = ByteTracker()
    bt.update([_box(0.1, 0.1, 0.4, 0.6, 0.9)], [], now=0.0, max_lost_time=0.5)
    bt.reset()
    assert bt.tracks == []
    # Ids restart at 0 after a reset.
    tracks = bt.update([_box(0.1, 0.1, 0.4, 0.6, 0.9)], [], now=1.0, max_lost_time=0.5)
    assert tracks[0].track_id == 0


def test_low_detection_threshold_is_below_typical_confidence() -> None:
    # Sanity guard for the split contract: the recovery floor must sit below a
    # normal operating confidence so a real low band exists.
    assert 0.0 < LOW_DETECTION_THRESHOLD < 0.2


# --------------------------------------------------------------------------- #
# Time-aware motion model (dt scales the extrapolation)
# --------------------------------------------------------------------------- #


def test_kalman_predict_scales_extrapolation_with_dt() -> None:
    """A larger dt extrapolates the centre proportionally further: position
    advances by velocity x dt, so a dropped frame (dt=2) moves twice as far."""
    kf = _KalmanFilter()
    mean, cov = kf.initiate(_tlbr_to_xyah(_box(0.10, 0.10, 0.40, 0.60, 0.9)))
    # Build a positive cx velocity from two rightward updates.
    for cx in (0.20, 0.30):
        mean, cov = kf.predict(mean, cov)
        mean, cov = kf.update(mean, cov, _tlbr_to_xyah(_box(cx - 0.15, 0.10, cx + 0.15, 0.60, 0.9)))

    cx0 = float(mean[0])
    mean1, _c1 = kf.predict(mean, cov, dt=1.0)
    mean2, _c2 = kf.predict(mean, cov, dt=2.0)  # same start state, predict does not mutate inputs
    step1 = float(mean1[0]) - cx0
    step2 = float(mean2[0]) - cx0
    assert step1 > 0.0
    assert step2 == pytest.approx(2.0 * step1, rel=1e-9)


def test_default_dt_reproduces_fixed_step_behaviour() -> None:
    # The dt=1.0 default must match a fixed-step predict, so direct ByteTracker
    # use (tests, and any caller not passing dt) is unchanged.
    kf = _KalmanFilter()
    mean, cov = kf.initiate(_tlbr_to_xyah(_box(0.10, 0.10, 0.40, 0.60, 0.9)))
    explicit, _ = kf.predict(mean, cov, dt=1.0)
    default, _ = kf.predict(mean, cov)
    assert np.allclose(explicit, default)


# --------------------------------------------------------------------------- #
# Distance rescue (first association only)
# --------------------------------------------------------------------------- #


def test_fast_mover_keeps_id_via_distance_rescue() -> None:
    """A high detection that no longer overlaps the track's predicted box (a fast
    mover) but is within the distance-rescue range keeps the existing id instead
    of spawning a new one."""
    bt = ByteTracker()
    first = bt.update([_box(0.10, 0.10, 0.20, 0.40, 0.9)], [], now=0.0, max_lost_time=1.0)
    tid = first[0].track_id

    # Jumped right by ~one box width: no overlap with the prediction (IoU 0) but
    # the centre (dist ~0.12) is within 1.5 box-widths (reach 0.15).
    second = bt.update([_box(0.22, 0.10, 0.32, 0.40, 0.9)], [], now=0.03, max_lost_time=1.0)
    assert len(second) == 1
    assert second[0].track_id == tid
    assert second[0].state == "tracked"


def test_distance_rescue_prefers_nearest_candidate() -> None:
    """When IoU fails for two candidates within rescue range, the nearer one wins
    (anti-glitch: a missed frame can't snap the track to an arbitrary farther
    person). Detection order must not matter – ranking is by distance."""
    bt = ByteTracker()
    first = bt.update([_box(0.45, 0.30, 0.55, 0.70, 0.9)], [], now=0.0, max_lost_time=1.0)
    tid = first[0].track_id  # predicted centre ~ (0.50, 0.50), width 0.10, reach 0.15

    near = _box(0.56, 0.30, 0.66, 0.70, 0.9)  # centre 0.61, dist ~0.11, IoU 0
    far = _box(0.59, 0.30, 0.69, 0.70, 0.9)  # centre 0.64, dist ~0.14, IoU 0
    tracks = bt.update([far, near], [], now=0.03, max_lost_time=1.0)  # far listed first

    matched = next(t for t in tracks if t.track_id == tid)
    cx = (matched.tlbr[0] + matched.tlbr[2]) / 2.0
    assert cx == pytest.approx(0.61, abs=0.01)  # bound to the nearer detection, not list order


def test_distance_rescue_ignores_far_detection() -> None:
    """A high detection beyond the rescue range does not steal the track; it
    spawns a fresh one and the original is left to go lost."""
    bt = ByteTracker()
    first = bt.update([_box(0.10, 0.10, 0.20, 0.40, 0.9)], [], now=0.0, max_lost_time=1.0)
    tid = first[0].track_id

    tracks = bt.update([_box(0.80, 0.60, 0.90, 0.90, 0.9)], [], now=0.03, max_lost_time=1.0)
    assert tid not in _tracked_ids(tracks, state="tracked")
    assert tid in _tracked_ids(tracks, state="lost")
    assert len(_tracked_ids(tracks, state="tracked")) == 1  # a new track for the far det


def test_low_stage_has_no_distance_rescue() -> None:
    """The recovery (low) stage stays IoU-strict: a near but non-overlapping low
    box must not pull a track onto it, so the track goes lost instead."""
    bt = ByteTracker()
    first = bt.update([_box(0.10, 0.10, 0.20, 0.40, 0.9)], [], now=0.0, max_lost_time=1.0)
    tid = first[0].track_id

    tracks = bt.update([], [_box(0.22, 0.10, 0.32, 0.40, 0.3)], now=0.03, max_lost_time=1.0)
    assert tid in _tracked_ids(tracks, state="lost")
    assert _tracked_ids(tracks, state="tracked") == set()
