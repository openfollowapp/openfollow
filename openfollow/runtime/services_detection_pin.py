# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Detection pinning helpers for runtime services."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np
import numpy.typing as npt

from openfollow.scene.solver import unproject_to_plane

if TYPE_CHECKING:
    from openfollow.psn.marker import Marker


@dataclass
class DetectionPinState:
    """State carried across frames for detection-based marker pin smoothing."""

    smooth_x: float | None = None
    smooth_y: float | None = None
    vel_x: float = 0.0
    vel_y: float = 0.0
    prev_target_x: float | None = None
    prev_target_y: float | None = None
    # Assist mode: the track_id the marker is currently refined toward. Carried
    # across frames so a momentary "another person is closer" doesn't make the
    # lock chatter between performers.
    locked_track_id: int | None = None
    # Assist mode: the AI-corrected marker's smoothed output position. This outer
    # glide is seeded once and *never* reset – it is what guarantees the output
    # never snaps across acquisition / lock-change / loss, gliding smoothly
    # between the locked person and the manual anchor instead.
    ai_smooth_x: float | None = None
    ai_smooth_y: float | None = None
    # Assist mode: the marker id the outer glide is currently seeded for. When the
    # operator switches the assist-controlled marker, this id differs from the new
    # one and the glide is re-seeded from the new marker's own position, so the
    # output starts on it instead of sweeping across from the previous marker.
    glide_marker_id: int | None = None
    # Display only, recomputed each frame: the detection track currently attached
    # to a marker (locked in assist, tracked in replace) and the marker id it
    # drives, so the overlay can paint that one box in the marker's colour.
    attached_track_id: int | None = None
    attached_marker_id: int | None = None

    def reset(self) -> None:
        """Clear smoothing/velocity so the next acquisition re-seeds fresh.

        Used by replace mode; also clears the assist outer-glide seed so a
        full mode/marker change starts cleanly.
        """
        self.smooth_x = None
        self.smooth_y = None
        self.vel_x = 0.0
        self.vel_y = 0.0
        self.prev_target_x = None
        self.prev_target_y = None
        self.locked_track_id = None
        self.ai_smooth_x = None
        self.ai_smooth_y = None
        self.glide_marker_id = None
        self.attached_track_id = None
        self.attached_marker_id = None

    def soft_release(self) -> None:
        """Drop the person lock + velocity but keep the outer glide.

        Assist mode calls this when no detection is in range: the AI marker
        keeps its smoothed position and glides toward the manual anchor rather
        than snapping. The next acquisition re-locks and re-seeds the inner
        de-jitter without disturbing the output position.
        """
        self.smooth_x = None
        self.smooth_y = None
        self.vel_x = 0.0
        self.vel_y = 0.0
        self.prev_target_x = None
        self.prev_target_y = None
        self.locked_track_id = None


def assist_pinned_marker_id(app: Any) -> int | None:
    """Resolve the marker id under assist control this frame, or ``None``.

    Single source of truth for "is this id being driven by assist": the input
    resolvers redirect operator movement to a manual ghost for this id, and the
    assist pin reads that ghost as its clip anchor. Returns ``None`` unless
    detection is ``enabled`` *and* ``pin_marker`` is on *and* ``pin_mode ==
    "assist"`` *and* the configured / selected target resolves to a controlled
    marker. Gating on ``enabled`` keeps the ghost, the input redirect, and the
    assist glide off together when person detection is switched off.
    """
    det = app._config.detection
    if not det.enabled or not det.pin_marker or det.pin_mode != "assist":
        return None
    configured_id = det.pin_marker_id
    if configured_id < 0:
        selected_id = app._selected_id
    elif configured_id in app._controlled_ids:
        selected_id = configured_id
    else:
        return None
    if selected_id is None:
        return None
    return int(selected_id)


def get_or_create_manual_marker(app: Any, marker_id: int) -> Marker:
    """Return the operator-steered manual ghost for ``marker_id``.

    Lazily created and seeded from the registered marker's current position so
    engaging assist produces no jump. The ghost is a real ``Marker`` (mutable,
    lock-guarded pos holder) so existing input code reads/writes it unchanged,
    but it is never registered with ``PsnServer`` – hence never broadcast.
    """
    from openfollow.psn import Marker

    ghost: Marker | None = app._assist_manual.get(marker_id)
    if ghost is None:
        ghost = Marker(marker_id, "")
        registered = app._server.get_marker(marker_id)
        if registered is not None:
            rx, ry, rz = registered.pos
            ghost.set_pos(rx, ry, rz)
        app._assist_manual[marker_id] = ghost
    return ghost


def _resolve_pinned_marker(app: Any, cfg: Any) -> Any:
    """Resolve the marker the detection pin drives, or ``None`` to skip.

    ``pin_marker_id`` defaults to -1 (follow the controller-selected marker);
    a non-negative id pins that exact marker, but only when it's controlled.
    """
    configured_id = cfg.detection.pin_marker_id
    if configured_id < 0:
        selected_id = app._selected_id
    elif configured_id in app._controlled_ids:
        selected_id = configured_id
    else:
        return None
    if selected_id is None:
        return None
    return app._server.get_marker(selected_id)


def _load_camera_params(app: Any, buffer: npt.NDArray[Any]) -> npt.NDArray[Any]:
    """Fill ``buffer`` (len 7) with the live camera params for unprojection."""
    cam_cfg = app._camera.to_config()
    buffer[0] = cam_cfg.pos_x
    buffer[1] = cam_cfg.pos_y
    buffer[2] = cam_cfg.pos_z
    buffer[3] = cam_cfg.pitch
    buffer[4] = cam_cfg.yaw
    buffer[5] = cam_cfg.roll
    buffer[6] = cam_cfg.fov
    return buffer


def _advance_smoothing(
    pin_state: DetectionPinState,
    target_x: float,
    target_y: float,
    cfg: Any,
) -> tuple[float, float]:
    """Velocity-EMA + prediction lookahead + EMA smoothing of a world target.

    Mutates ``pin_state`` and returns the smoothed ``(x, y)``. Shared by both
    pin modes so the filter stays identical; tune it once, here.
    """
    vel_alpha = 0.3
    if pin_state.prev_target_x is not None and pin_state.prev_target_y is not None:
        dx = target_x - pin_state.prev_target_x
        dy = target_y - pin_state.prev_target_y
        pin_state.vel_x += vel_alpha * (dx - pin_state.vel_x)
        pin_state.vel_y += vel_alpha * (dy - pin_state.vel_y)
    pin_state.prev_target_x = target_x
    pin_state.prev_target_y = target_y

    prediction = cfg.detection.prediction
    predicted_x = target_x + pin_state.vel_x * prediction
    predicted_y = target_y + pin_state.vel_y * prediction

    alpha = cfg.detection.smoothing
    if pin_state.smooth_x is None or pin_state.smooth_y is None:
        pin_state.smooth_x = predicted_x
        pin_state.smooth_y = predicted_y
    else:
        pin_state.smooth_x += alpha * (predicted_x - pin_state.smooth_x)
        pin_state.smooth_y += alpha * (predicted_y - pin_state.smooth_y)
    return pin_state.smooth_x, pin_state.smooth_y


def apply_detection_pin(
    app: Any,
    *,
    person_detector: Any,
    unproject_cam_buffer: npt.NDArray[Any],
    screen_point_buffer: npt.NDArray[Any],
    pin_state: DetectionPinState,
) -> None:
    """Pin selected controlled marker to tracked detection with EMA smoothing."""
    cfg = app._config
    # Recomputed each frame: clear the attached-box marker first so a frame with
    # no attachment (pin off, no marker, lost detection) shows no coloured box.
    pin_state.attached_track_id = None
    pin_state.attached_marker_id = None
    # Drop stale manual ghosts whenever assist isn't the active mode, so a later
    # re-engage re-seeds the anchor from the live marker instead of a stale pos.
    if cfg.detection.pin_mode != "assist" or not cfg.detection.pin_marker:
        _prune_manual_markers(app, keep=None)
    if not cfg.detection.pin_marker:
        return

    if cfg.detection.pin_mode == "assist":
        # Assist runs even without a live detector: input is redirected to the
        # manual ghost, so the output must keep gliding to it (degrading to pure
        # manual) rather than freezing while the operator steers an unused anchor.
        _apply_assist_pin(
            app,
            person_detector=person_detector,
            unproject_cam_buffer=unproject_cam_buffer,
            screen_point_buffer=screen_point_buffer,
            pin_state=pin_state,
        )
        return

    if person_detector is None:
        return

    best = person_detector.tracked_detection
    if best is None:
        # Detection dropped – clear smoothing so reacquisition snaps, not lerps.
        pin_state.reset()
        return

    w, h = app._video_receiver.resolution
    if w <= 0 or h <= 0:
        return

    marker = _resolve_pinned_marker(app, cfg)
    if marker is None:
        return

    # The tracked person is the attached box; the overlay paints it in the
    # driven marker's colour.
    pin_state.attached_track_id = best.track_id
    pin_state.attached_marker_id = marker.marker_id

    use_bottom = cfg.detection.pin_point == "bottom"
    screen_x = (best.x1 + best.x2) / 2.0 * w
    screen_y = (best.y2 if use_bottom else best.y1) * h

    params = _load_camera_params(app, unproject_cam_buffer)
    screen_pt = screen_point_buffer
    screen_pt[0, 0] = screen_x
    screen_pt[0, 1] = screen_y

    plane_z = cfg.grid.z_offset if use_bottom else marker.pos[2]
    world = unproject_to_plane(params, screen_pt, float(w), float(h), plane_z)

    if not np.all(np.isfinite(world[0])):
        return

    # unproject_to_plane returns PSN-absolute world coords (canonical marker.pos frame).
    smooth_x, smooth_y = _advance_smoothing(pin_state, float(world[0, 0]), float(world[0, 1]), cfg)
    marker.set_pos(smooth_x, smooth_y, marker.pos[2])


def _apply_assist_pin(
    app: Any,
    *,
    person_detector: Any,
    unproject_cam_buffer: npt.NDArray[Any],
    screen_point_buffer: npt.NDArray[Any],
    pin_state: DetectionPinState,
) -> None:
    """Two-marker assist: glide the AI output marker toward person-near-anchor.

    There are two distinct entities. The operator steers a **manual ghost**
    (input is redirected to it via ``assist_pinned_marker_id``); it is the clip
    anchor and is never written here. The **registered** marker is the
    AI-corrected output (broadcast + zones + normal render): each frame its
    smoothed position glides toward the detection nearest the anchor (within
    ``assist_radius_m``, eased onto the person by ``assist_strength``) or, when
    none is in range, glides back toward the anchor. The glide (rate
    ``smoothing``) is seeded once and never reset, so the output never snaps.
    """
    cfg = app._config
    pinned_id = assist_pinned_marker_id(app)
    if pinned_id is None:
        _prune_manual_markers(app, keep=None)
        pin_state.reset()
        return
    _prune_manual_markers(app, keep=pinned_id)

    out_marker = app._server.get_marker(pinned_id)
    if out_marker is None:
        return
    anchor = get_or_create_manual_marker(app, pinned_id)

    # Operator switched the assist-controlled marker: re-seed everything from the
    # new marker so the output starts on it. Without this the never-reset outer
    # glide would carry the previous marker's position and visibly sweep across.
    if pinned_id != pin_state.glide_marker_id:
        pin_state.reset()
        pin_state.glide_marker_id = pinned_id

    w, h = app._video_receiver.resolution
    if w <= 0 or h <= 0:
        return

    use_bottom = cfg.detection.pin_point == "bottom"
    manual_x, manual_y, manual_z = anchor.pos
    plane_z = cfg.grid.z_offset if use_bottom else manual_z

    params = _load_camera_params(app, unproject_cam_buffer)
    screen_pt = screen_point_buffer

    def _world_of(box: Any) -> tuple[float, float] | None:
        """Unproject a detection's pin point to a PSN-absolute world (x, y).

        Returns ``None`` for a non-finite solve (degenerate camera geometry).
        The nearest-search visits each detection once, so no per-frame cache.
        """
        screen_pt[0, 0] = (box.x1 + box.x2) / 2.0 * w
        screen_pt[0, 1] = (box.y2 if use_bottom else box.y1) * h
        world = unproject_to_plane(params, screen_pt, float(w), float(h), plane_z)
        if not np.all(np.isfinite(world[0])):
            return None
        return float(world[0, 0]), float(world[0, 1])

    # No live detector (e.g. detection deps missing) → no detections, so the
    # output simply glides to the manual anchor; the operator keeps control.
    detections = person_detector.detections if person_detector is not None else []
    radius_sq = cfg.detection.assist_radius_m**2

    # Always follow the detection nearest the operator's manual anchor – never a
    # bigger box, never a previously-locked one. The operator retargets simply by
    # nudging the anchor toward a different person; the never-reset outer glide
    # absorbs the hand-over so the output still never snaps.
    chosen_id: int | None = None
    chosen_world: tuple[float, float] | None = None
    best_dsq = radius_sq
    for box in detections:
        world = _world_of(box)
        if world is None:
            continue
        dsq = (world[0] - manual_x) ** 2 + (world[1] - manual_y) ** 2
        if dsq <= best_dsq:
            best_dsq = dsq
            chosen_id, chosen_world = box.track_id, world

    if chosen_world is None:
        # No detection near the anchor – target the anchor itself so the AI
        # marker glides home. Drop the person lock + velocity but keep the
        # outer glide so the output never snaps.
        pin_state.soft_release()
        target_x, target_y = manual_x, manual_y
    else:
        # New lock (or first acquisition) – re-seed the inner de-jitter so the
        # person target tracks cleanly. The outer glide is untouched, so the
        # output still eases (never jumps) onto the newly-locked person.
        if chosen_id != pin_state.locked_track_id:
            pin_state.smooth_x = None
            pin_state.smooth_y = None
            pin_state.vel_x = 0.0
            pin_state.vel_y = 0.0
            pin_state.prev_target_x = None
            pin_state.prev_target_y = None
            pin_state.locked_track_id = chosen_id
        smooth_x, smooth_y = _advance_smoothing(pin_state, chosen_world[0], chosen_world[1], cfg)
        # ``assist_strength`` is the in-range clip ratio: 1.0 sits exactly on the
        # person; lower blends toward the manual anchor.
        strength = cfg.detection.assist_strength
        target_x = manual_x + strength * (smooth_x - manual_x)
        target_y = manual_y + strength * (smooth_y - manual_y)

    # Outer glide: ease the AI marker toward this frame's target. Seeded once
    # from the registered marker's pos; never reset → the output never snaps.
    glide = cfg.detection.smoothing
    if pin_state.ai_smooth_x is None or pin_state.ai_smooth_y is None:
        out_x, out_y, _ = out_marker.pos
        pin_state.ai_smooth_x = out_x
        pin_state.ai_smooth_y = out_y
    pin_state.ai_smooth_x += glide * (target_x - pin_state.ai_smooth_x)
    pin_state.ai_smooth_y += glide * (target_y - pin_state.ai_smooth_y)
    out_marker.set_pos(pin_state.ai_smooth_x, pin_state.ai_smooth_y, manual_z)

    # The locked person (if any) is the attached box; the overlay paints it in
    # the output marker's colour. ``None`` while gliding home leaves all boxes
    # in the default detection colour.
    pin_state.attached_track_id = pin_state.locked_track_id
    pin_state.attached_marker_id = pinned_id


def _prune_manual_markers(app: Any, *, keep: int | None) -> None:
    """Drop stale manual ghosts so a re-engage re-seeds fresh from the marker.

    ``keep`` is the currently assist-pinned id (or ``None`` when assist is off).
    Any other ghost is discarded – when the operator switches the selected /
    pinned marker or turns assist off, the old anchor must not linger.
    """
    stale = [mid for mid in app._assist_manual if mid != keep]
    for mid in stale:
        del app._assist_manual[mid]
