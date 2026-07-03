# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""ByteTrack multi-object tracker (pure NumPy, no external deps).

Tracking-by-detection for the person detector. A constant-velocity Kalman
filter predicts each tracklet forward; a two-stage IoU association then binds
detections to tracklets. The first stage matches high-confidence detections;
the second stage matches the *low*-confidence detections that a single-stage
tracker discards. That second pass is what holds a performer who dips into
shadow (their detection score dropping with the light) onto their existing
tracklet instead of dropping the target and re-numbering it on reacquisition.

Everything runs in the detector's normalised 0-1 box space. ``ByteTracker``
reads detection boxes structurally (``x1/y1/x2/y2/confidence``) and returns
``STrack`` tracklets carrying a stable ``track_id``; the detector maps those
back to ``DetectionBox`` results.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np
import numpy.typing as npt

if TYPE_CHECKING:
    from openfollow.video.detection import DetectionBox

# IoU gate for the first (high-confidence) association, applied against each
# tracklet's Kalman-predicted box. Loose, because the motion model already
# aligns the prediction with the detection, so a modest overlap confirms it.
_HIGH_IOU_GATE = 0.2
# IoU gate for the second (low-confidence) association. Strict on purpose:
# low-score boxes are noisy, so only a strong overlap with a predicted track is
# trusted to recover a temporarily dim detection.
_LOW_IOU_GATE = 0.5
# Detection score floor for the low set. Boxes below this are ignored; boxes in
# ``[floor, confidence)`` form the second-stage recovery set.
LOW_DETECTION_THRESHOLD = 0.1
# Distance rescue for the first (high-confidence) association. A fast mover's
# Kalman-predicted box can stop overlapping its detection (IoU below the gate)
# between frames; a high detection whose centre lies within this many predicted-
# box *widths* is admitted anyway, so the track follows the person instead of
# being dropped and re-numbered. Width (not the height-dominated diagonal) is the
# axis stage motion happens along, keeping the reach tight enough that a missed
# frame can't snap the track onto a different nearby person. Only the high stage
# uses it – the low stage stays IoU-strict so noisy dim boxes can't pull a track
# onto the wrong blob.
_DIST_RESCUE_FACTOR = 1.5

FloatArray = npt.NDArray[Any]


def _tlbr_to_xyah(box: DetectionBox) -> FloatArray:
    """Normalised ``(x1, y1, x2, y2)`` -> Kalman measurement ``(cx, cy, a, h)``."""
    w = max(float(box.x2) - float(box.x1), 1e-6)
    h = max(float(box.y2) - float(box.y1), 1e-6)
    return np.array([float(box.x1) + w / 2.0, float(box.y1) + h / 2.0, w / h, h], dtype=np.float64)


def _xyah_to_tlbr(mean: FloatArray) -> tuple[float, float, float, float]:
    """Kalman state ``(cx, cy, a, h, ...)`` -> normalised ``(x1, y1, x2, y2)``."""
    cx, cy, aspect, height = float(mean[0]), float(mean[1]), float(mean[2]), float(mean[3])
    height = max(height, 1e-6)
    width = max(aspect * height, 1e-6)
    return (cx - width / 2.0, cy - height / 2.0, cx + width / 2.0, cy + height / 2.0)


def _iou_matrix(tracks_tlbr: FloatArray, dets_tlbr: FloatArray) -> FloatArray:
    """Pairwise IoU between ``(N, 4)`` track boxes and ``(M, 4)`` detection boxes."""
    top_left = np.maximum(tracks_tlbr[:, None, :2], dets_tlbr[None, :, :2])
    bottom_right = np.minimum(tracks_tlbr[:, None, 2:], dets_tlbr[None, :, 2:])
    wh = np.clip(bottom_right - top_left, 0.0, None)
    inter = wh[..., 0] * wh[..., 1]
    area_t = np.clip(tracks_tlbr[:, 2] - tracks_tlbr[:, 0], 0.0, None) * np.clip(
        tracks_tlbr[:, 3] - tracks_tlbr[:, 1], 0.0, None
    )
    area_d = np.clip(dets_tlbr[:, 2] - dets_tlbr[:, 0], 0.0, None) * np.clip(
        dets_tlbr[:, 3] - dets_tlbr[:, 1], 0.0, None
    )
    union = area_t[:, None] + area_d[None, :] - inter
    return np.where(union > 0.0, inter / np.where(union > 0.0, union, 1.0), 0.0)


class _KalmanFilter:
    """8-dim constant-velocity Kalman filter on box state ``[cx, cy, a, h]``.

    State is centre-x, centre-y, aspect ratio (w/h) and height, plus their
    velocities – the standard SORT/ByteTrack parameterisation. Solved with
    ``numpy.linalg`` (the matrices are 4x4 / 8x8, so no SciPy is needed).
    """

    def __init__(self) -> None:
        self._ndim = 4
        self._update_mat: FloatArray = np.eye(self._ndim, 2 * self._ndim)
        # Reused scratch for the constant-velocity transition; ``predict`` only
        # rewrites its dt entries (the tracker runs single-threaded, so reusing
        # it avoids allocating an 8x8 every predict).
        self._motion_mat: FloatArray = np.eye(2 * self._ndim, 2 * self._ndim)
        # Uncertainty scales relative to box height, as in the reference impl.
        self._std_weight_position = 1.0 / 20
        self._std_weight_velocity = 1.0 / 160

    def initiate(self, measurement: FloatArray) -> tuple[FloatArray, FloatArray]:
        """Seed mean (zero velocity) and covariance from a first measurement."""
        mean = np.concatenate([measurement, np.zeros(4, dtype=np.float64)])
        h = float(measurement[3])
        std = np.array(
            [
                2 * self._std_weight_position * h,
                2 * self._std_weight_position * h,
                1e-2,
                2 * self._std_weight_position * h,
                10 * self._std_weight_velocity * h,
                10 * self._std_weight_velocity * h,
                1e-5,
                10 * self._std_weight_velocity * h,
            ],
            dtype=np.float64,
        )
        return mean, np.diag(np.square(std))

    def predict(self, mean: FloatArray, covariance: FloatArray, dt: float = 1.0) -> tuple[FloatArray, FloatArray]:
        """Advance the state by ``dt`` steps under the constant-velocity model.

        ``dt`` is the elapsed time in units of a nominal detection interval (1.0
        at steady cadence). Both the centre extrapolation and the accrued process
        noise scale with it, so a frame drop (``dt`` > 1) propagates the box
        further and widens the gate, and a burst (``dt`` < 1) does less – keeping
        the prediction physically consistent when the real cadence jitters.
        """
        ndim = self._ndim
        h = float(mean[3])
        pos_std = self._std_weight_position * h * dt
        vel_std = self._std_weight_velocity * h * dt
        std = np.array(
            [pos_std, pos_std, 1e-2, pos_std, vel_std, vel_std, 1e-5, vel_std],
            dtype=np.float64,
        )
        motion_cov = np.diag(np.square(std))
        motion_mat = self._motion_mat
        for i in range(ndim):
            motion_mat[i, ndim + i] = dt
        mean = motion_mat @ mean
        covariance = motion_mat @ covariance @ motion_mat.T + motion_cov
        return mean, covariance

    def _project(self, mean: FloatArray, covariance: FloatArray) -> tuple[FloatArray, FloatArray]:
        h = float(mean[3])
        std = np.array(
            [
                self._std_weight_position * h,
                self._std_weight_position * h,
                1e-1,
                self._std_weight_position * h,
            ],
            dtype=np.float64,
        )
        innovation_cov = np.diag(np.square(std))
        projected_mean = self._update_mat @ mean
        projected_cov = self._update_mat @ covariance @ self._update_mat.T
        return projected_mean, projected_cov + innovation_cov

    def update(
        self, mean: FloatArray, covariance: FloatArray, measurement: FloatArray
    ) -> tuple[FloatArray, FloatArray]:
        """Correct the state toward ``measurement`` and return the posterior."""
        projected_mean, projected_cov = self._project(mean, covariance)
        # K = P Hᵀ S⁻¹, solved as Kᵀ = S⁻¹ (P Hᵀ)ᵀ (S symmetric) to avoid an inverse.
        kalman_gain = np.linalg.solve(projected_cov, (covariance @ self._update_mat.T).T).T
        innovation = measurement - projected_mean
        new_mean = mean + innovation @ kalman_gain.T
        new_covariance = covariance - kalman_gain @ projected_cov @ kalman_gain.T
        return new_mean, new_covariance


class STrack:
    """One tracked person: Kalman state, lifecycle, and a stable ``track_id``.

    ``state`` is ``"tracked"`` the frame a detection was bound, else ``"lost"``.
    The reported box (:attr:`tlbr`) is the raw measurement while tracked and the
    Kalman prediction while lost, so a pinned marker glides along the predicted
    trajectory through a brief occlusion instead of freezing.
    """

    def __init__(self, box: DetectionBox, kf: _KalmanFilter, track_id: int, now: float) -> None:
        self._kf = kf
        self.track_id = track_id
        self.score = float(box.confidence)
        self.state = "tracked"
        self.last_seen = now
        self.mean, self.covariance = kf.initiate(_tlbr_to_xyah(box))
        self._tlbr: tuple[float, float, float, float] = (
            float(box.x1),
            float(box.y1),
            float(box.x2),
            float(box.y2),
        )

    def predict(self, dt: float = 1.0) -> None:
        """Advance the Kalman state by ``dt`` steps; refresh the box while lost."""
        if self.state == "lost":
            # Freeze aspect/height velocity so an unobserved box keeps its shape
            # and only its centre extrapolates – a drifting size wrecks the IoU
            # gate when the person reappears.
            self.mean[6] = 0.0
            self.mean[7] = 0.0
        self.mean, self.covariance = self._kf.predict(self.mean, self.covariance, dt)
        if self.state == "lost":
            self._tlbr = _xyah_to_tlbr(self.mean)

    def update(self, box: DetectionBox, now: float) -> None:
        """Bind a detection: correct the filter and report the measured box."""
        self.mean, self.covariance = self._kf.update(self.mean, self.covariance, _tlbr_to_xyah(box))
        self.score = float(box.confidence)
        self.state = "tracked"
        self.last_seen = now
        self._tlbr = (float(box.x1), float(box.y1), float(box.x2), float(box.y2))

    def mark_lost(self) -> None:
        # Switch the reported box to the (already-predicted-this-frame) Kalman
        # box right away, so a marker keeps gliding from the first lost frame
        # instead of freezing one frame at the last measurement.
        self.state = "lost"
        self._tlbr = _xyah_to_tlbr(self.mean)

    @property
    def tlbr(self) -> tuple[float, float, float, float]:
        """Reported box: measured while tracked, predicted while lost."""
        return self._tlbr

    @property
    def predicted_tlbr(self) -> tuple[float, float, float, float]:
        """Kalman-predicted box, used for association IoU (always current)."""
        return _xyah_to_tlbr(self.mean)


class ByteTracker:
    """Two-stage IoU + Kalman tracker. One instance per detector session."""

    def __init__(self) -> None:
        self._kf = _KalmanFilter()
        self._tracks: list[STrack] = []
        self._next_id = 0

    def reset(self) -> None:
        """Drop all tracks and reset id allocation (called on detector start)."""
        self._tracks = []
        self._next_id = 0

    @property
    def tracks(self) -> list[STrack]:
        return self._tracks

    def update(
        self,
        high: list[DetectionBox],
        low: list[DetectionBox],
        now: float,
        max_lost_time: float,
        dt: float = 1.0,
    ) -> list[STrack]:
        """Associate this frame's detections and return the live tracklets.

        ``high`` are detections at/above the configured confidence, ``low`` the
        recovery band ``[LOW_DETECTION_THRESHOLD, confidence)``. ``max_lost_time``
        is how long (seconds) an unmatched track is retained before removal. ``dt``
        is the elapsed time since the previous update in nominal-interval units
        (1.0 at steady cadence) and drives the Kalman extrapolation.
        """
        for track in self._tracks:
            track.predict(dt)

        # First association: every live track vs the high-confidence detections.
        # A lost track matched here is re-identified (back to "tracked"). The
        # distance rescue keeps a fast mover bound when its predicted box no
        # longer overlaps the detection.
        unmatched_tracks, unmatched_high = self._associate(
            self._tracks, high, _HIGH_IOU_GATE, now, dist_factor=_DIST_RESCUE_FACTOR
        )

        # Second association: every unmatched track – including ones already lost
        # – chases the low-confidence band. The shadow-recovery pass: a performer
        # who dims below the high threshold (or stays dim across frames) keeps
        # their track_id instead of being dropped and re-numbered. The strict
        # _LOW_IOU_GATE against the Kalman prediction is what keeps a noisy low
        # box from resurrecting the wrong track.
        still_unmatched, _unmatched_low = self._associate(unmatched_tracks, low, _LOW_IOU_GATE, now)
        for track in still_unmatched:
            if track.state == "tracked":
                track.mark_lost()

        # Unmatched high-confidence detections spawn new tracks. Low-only
        # detections never spawn a track – that is what suppresses noise.
        for box in unmatched_high:
            self._tracks.append(STrack(box, self._kf, self._next_id, now))
            self._next_id += 1

        # Expire lost tracks past the retention window.
        self._tracks = [t for t in self._tracks if t.state != "lost" or (now - t.last_seen) <= max_lost_time]
        return self._tracks

    @staticmethod
    def _associate(
        tracks: list[STrack],
        dets: list[DetectionBox],
        gate: float,
        now: float,
        *,
        dist_factor: float = 0.0,
    ) -> tuple[list[STrack], list[DetectionBox]]:
        """Greedy matching (best first). Matched tracks update in place.

        A pair is admitted when its IoU clears ``gate`` or – when ``dist_factor``
        is set – when the detection centre lies within ``dist_factor`` predicted-
        box widths of the track (the fast-mover distance rescue). Pairs rank by
        ``(IoU, -centre_distance)``: IoU wins first (so an overlapping match always
        beats a pure-distance rescue), and among equal IoU – including the IoU-0
        rescues – the nearer detection wins, so a missed frame can't snap a track
        onto an arbitrary farther person. Returns the still-unmatched
        ``(tracks, detections)``.
        """
        if not tracks or not dets:
            return list(tracks), list(dets)

        tracks_tlbr = np.array([t.predicted_tlbr for t in tracks], dtype=np.float64)
        dets_tlbr = np.array([(d.x1, d.y1, d.x2, d.y2) for d in dets], dtype=np.float64)
        iou = _iou_matrix(tracks_tlbr, dets_tlbr)

        # Centre-distance matrix + per-track reach for the rescue (vectorised once).
        if dist_factor > 0.0:
            t_cx = (tracks_tlbr[:, 0] + tracks_tlbr[:, 2]) / 2.0
            t_cy = (tracks_tlbr[:, 1] + tracks_tlbr[:, 3]) / 2.0
            d_cx = (dets_tlbr[:, 0] + dets_tlbr[:, 2]) / 2.0
            d_cy = (dets_tlbr[:, 1] + dets_tlbr[:, 3]) / 2.0
            dist = np.hypot(t_cx[:, None] - d_cx[None, :], t_cy[:, None] - d_cy[None, :])
            reach = dist_factor * (tracks_tlbr[:, 2] - tracks_tlbr[:, 0])
            within_reach = dist <= reach[:, None]

        pairs: list[tuple[float, float, int, int]] = []
        for ti in range(len(tracks)):
            for di in range(len(dets)):
                score = float(iou[ti, di])
                neg_dist = 0.0
                admit = score >= gate
                if dist_factor > 0.0:
                    admit = admit or bool(within_reach[ti, di])
                    neg_dist = -float(dist[ti, di])
                if admit:
                    pairs.append((score, neg_dist, ti, di))
        pairs.sort(reverse=True)

        matched_t: set[int] = set()
        matched_d: set[int] = set()
        for _score, _neg_dist, ti, di in pairs:
            if ti in matched_t or di in matched_d:
                continue
            tracks[ti].update(dets[di], now)
            matched_t.add(ti)
            matched_d.add(di)

        unmatched_tracks = [t for i, t in enumerate(tracks) if i not in matched_t]
        unmatched_dets = [d for i, d in enumerate(dets) if i not in matched_d]
        return unmatched_tracks, unmatched_dets
