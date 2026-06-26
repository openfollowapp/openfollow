# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Detection pinning helpers for runtime services."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np
import numpy.typing as npt

from openfollow.scene.solver import invert_overlay_distortion, unproject_to_plane

if TYPE_CHECKING:
    from openfollow.psn.marker import Marker


# The pin filter (velocity EMA, lookahead, output EMA) and the assist outer glide
# are tuned for the ~60fps animate tick. Re-deriving each per-frame factor for the
# real elapsed dt keeps the time constants stable whether animate runs at 60fps
# (Mac) or slower (Pi) and across stalls, so ``smoothing`` / ``prediction`` behave
# the same regardless of frame rate. This is the animate-cadence clock; the
# tracker's Kalman runs on its own detection-cadence clock (see ``video/detection``)
# – two deliberately separate clocks for two loops at different rates.
_NOMINAL_FRAME_DT = 1.0 / 60.0
_MIN_SMOOTH_DT = 1.0 / 1000.0
_MAX_SMOOTH_DT = 0.2


def _dt_steps(dt: float) -> float:
    """Elapsed time as a multiple of the nominal animate frame, clamped."""
    return min(max(dt, _MIN_SMOOTH_DT), _MAX_SMOOTH_DT) / _NOMINAL_FRAME_DT


def _ema_factor(per_frame_alpha: float, steps: float) -> float:
    """Re-derive a per-nominal-frame EMA alpha for ``steps`` frames elapsed.

    At ``steps == 1`` it returns the alpha unchanged (steady 60fps); otherwise it
    compounds the retention so the time constant is frame-rate-independent.
    """
    if steps == 1.0:
        return per_frame_alpha
    base = 1.0 - per_frame_alpha
    if base <= 0.0:
        return 1.0
    # ``base`` is > 0 here, so the power is real; float() keeps mypy from widening
    # ``float ** float`` (which can be complex) to Any.
    return 1.0 - float(base**steps)


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


def assist_active(app: Any) -> bool:
    """True when AI-assisted tracking is engaged.

    Detection's only on/off is ``enabled`` (the Tracking control); assist is the
    active mode when ``pin_mode == "assist"``. Gating on ``enabled`` keeps the
    ghosts, the input redirect, and the assist glide off together when person
    detection is switched off.
    """
    det = app._config.detection
    return bool(det.enabled) and det.pin_mode == "assist"


def is_assist_controlled(app: Any, marker_id: int | None) -> bool:
    """True when ``marker_id`` is under assist control this frame.

    Single source of truth for "is this id being driven by assist": assist
    refines *every* controlled marker, so the input resolvers redirect operator
    movement to a manual ghost for any controlled id, and the assist pin reads
    that ghost as the clip anchor.
    """
    if marker_id is None:
        return False
    return assist_active(app) and marker_id in app._controlled_ids


def _get_pin_state(app: Any, marker_id: int) -> DetectionPinState:
    """Return the per-marker pin-smoothing state, creating it lazily.

    Mirrors :func:`get_or_create_manual_marker`: each driven marker owns its
    smoothing / glide state for its whole driven lifetime, so the never-reset
    outer glide stays continuous across frames.
    """
    state: DetectionPinState | None = app._detection_pin_states.get(marker_id)
    if state is None:
        state = DetectionPinState()
        app._detection_pin_states[marker_id] = state
    return state


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
    dt: float = _NOMINAL_FRAME_DT,
) -> tuple[float, float]:
    """Velocity-EMA + prediction lookahead + EMA smoothing of a world target.

    Mutates ``pin_state`` and returns the smoothed ``(x, y)``. Shared by both pin
    modes so the filter stays identical; tune it once, here. ``dt`` (seconds since
    the previous frame) makes the EMAs and the lookahead frame-rate-independent:
    velocity is tracked per nominal frame and ``prediction`` keeps its scale, so
    the lookahead distance is the same at 60fps or 30fps and across stalls.
    """
    return _advance_smoothing_steps(pin_state, target_x, target_y, cfg, _dt_steps(dt))


def _advance_smoothing_steps(
    pin_state: DetectionPinState,
    target_x: float,
    target_y: float,
    cfg: Any,
    steps: float,
) -> tuple[float, float]:
    """``_advance_smoothing`` body keyed by precomputed ``steps`` (= dt ÷ nominal
    frame). Lets the assist path derive ``steps`` once and share it with the outer
    glide instead of clamping ``dt`` twice per frame."""
    vel_alpha = _ema_factor(0.3, steps)
    if pin_state.prev_target_x is not None and pin_state.prev_target_y is not None:
        # Per-nominal-frame displacement, so a slow frame / dropped detection
        # doesn't inflate the estimated velocity.
        rate_x = (target_x - pin_state.prev_target_x) / steps
        rate_y = (target_y - pin_state.prev_target_y) / steps
        pin_state.vel_x += vel_alpha * (rate_x - pin_state.vel_x)
        pin_state.vel_y += vel_alpha * (rate_y - pin_state.vel_y)
    pin_state.prev_target_x = target_x
    pin_state.prev_target_y = target_y

    prediction = cfg.detection.prediction
    predicted_x = target_x + pin_state.vel_x * prediction
    predicted_y = target_y + pin_state.vel_y * prediction

    alpha = _ema_factor(cfg.detection.smoothing, steps)
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
    dt: float = _NOMINAL_FRAME_DT,
) -> None:
    """Drive controlled marker(s) from detection with EMA smoothing.

    Detection's only on/off is ``enabled``. In ``assist`` mode every controlled
    marker is refined; in ``replace`` mode the single resolved marker is auto-
    pinned. ``dt`` is the seconds elapsed since the previous animate frame; it
    makes the smoothing / prediction frame-rate-independent (see
    :func:`_advance_smoothing`).
    """
    cfg = app._config
    det = cfg.detection
    if not det.enabled:
        # Detection off – drop every ghost + per-marker state so a later
        # re-engage re-seeds fresh from the live markers.
        _prune_manual_markers(app, keep=set())
        _prune_pin_states(app, keep=set())
        return

    if det.pin_mode == "assist":
        # Assist runs even without a live detector: input is redirected to the
        # manual ghosts, so each output keeps gliding to its anchor (degrading to
        # pure manual) rather than freezing.
        _apply_assist_all(
            app,
            person_detector=person_detector,
            unproject_cam_buffer=unproject_cam_buffer,
            screen_point_buffer=screen_point_buffer,
            dt=dt,
        )
        return

    # Replace mode (Fully Automatic): auto-pin the single resolved marker. Assist
    # ghosts are unused here, so drop them all.
    _prune_manual_markers(app, keep=set())
    marker = _resolve_pinned_marker(app, cfg)
    if marker is None:
        _prune_pin_states(app, keep=set())
        return
    _prune_pin_states(app, keep={marker.marker_id})
    pin_state = _get_pin_state(app, marker.marker_id)
    # Recomputed each frame: clear the attached-box marker first so a frame with
    # no attachment (no detector, lost detection) shows no coloured box.
    pin_state.attached_track_id = None
    pin_state.attached_marker_id = None

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

    # The tracked person is the attached box; the overlay paints it in the
    # driven marker's colour.
    pin_state.attached_track_id = best.track_id
    pin_state.attached_marker_id = marker.marker_id

    use_bottom = det.pin_point == "bottom"
    screen_x = (best.x1 + best.x2) / 2.0 * w
    screen_y = (best.y2 if use_bottom else best.y1) * h

    params = _load_camera_params(app, unproject_cam_buffer)
    screen_pt = screen_point_buffer
    screen_pt[0, 0] = screen_x
    screen_pt[0, 1] = screen_y

    # Detections come from the (lens-distorted) video frame, so undistort the pin
    # point back to the pinhole frame before unprojecting. Identity when no lens
    # distortion is configured.
    screen_pt = invert_overlay_distortion(screen_pt, float(w), float(h), cfg.camera.lens_k1, cfg.camera.lens_k2)

    plane_z = cfg.grid.z_offset if use_bottom else marker.pos[2]
    world = unproject_to_plane(params, screen_pt, float(w), float(h), plane_z)

    if not np.all(np.isfinite(world[0])):
        return

    # unproject_to_plane returns PSN-absolute world coords (canonical marker.pos frame).
    smooth_x, smooth_y = _advance_smoothing(pin_state, float(world[0, 0]), float(world[0, 1]), cfg, dt)
    marker.set_pos(smooth_x, smooth_y, marker.pos[2])


def _apply_assist_all(
    app: Any,
    *,
    person_detector: Any,
    unproject_cam_buffer: npt.NDArray[Any],
    screen_point_buffer: npt.NDArray[Any],
    dt: float = _NOMINAL_FRAME_DT,
) -> None:
    """Assist every controlled marker toward the person nearest its own anchor.

    Assist refines all controlled markers simultaneously. Each marker has two
    entities: a **manual ghost** the operator steers (the clip anchor, never
    written here) and the **registered** marker (the AI-corrected output:
    broadcast + zones + normal render). Per marker the smoothed output glides
    toward the detection nearest its anchor (within ``assist_radius_m``, eased
    onto the person by ``assist_strength``) or back toward the anchor when none
    is in range; the glide is seeded once and never reset, so it never snaps. The
    same detection may drive more than one marker – operators legitimately overlap
    spots on one performer, and collisions self-resolve as the anchors diverge.
    """
    cfg = app._config
    target_ids = set(app._controlled_ids)
    _prune_manual_markers(app, keep=target_ids)
    _prune_pin_states(app, keep=target_ids)
    if not target_ids:
        return

    w, h = app._video_receiver.resolution
    if w <= 0 or h <= 0:
        return

    steps = _dt_steps(dt)  # shared by the inner smoothing and the outer glide
    use_bottom = cfg.detection.pin_point == "bottom"
    params = _load_camera_params(app, unproject_cam_buffer)
    screen_pt = screen_point_buffer

    # No live detector (deps missing) → no detections, so each output simply
    # glides to its manual anchor; the operator keeps control.
    detections = person_detector.detections if person_detector is not None else []

    # Unproject each detection's pin point ONCE per plane and memoise by plane Z,
    # so N markers don't re-unproject M detections N times. ``pin_point ==
    # "bottom"`` shares the stage-floor plane across every marker (one pass);
    # ``"top"`` uses each anchor's Z, so markers at the same height still share a
    # single pass and only genuinely-different heights cost extra.
    det_world_cache: dict[float, list[tuple[int, float, float]]] = {}

    def _det_worlds_for(plane_z: float) -> list[tuple[int, float, float]]:
        cached = det_world_cache.get(plane_z)
        if cached is not None:
            return cached
        worlds: list[tuple[int, float, float]] = []
        for box in detections:
            screen_pt[0, 0] = (box.x1 + box.x2) / 2.0 * w
            screen_pt[0, 1] = (box.y2 if use_bottom else box.y1) * h
            # Undistort the detection pin point (it sits on the distorted video)
            # back to the pinhole frame; identity when no lens distortion is set.
            undistorted = invert_overlay_distortion(
                screen_pt, float(w), float(h), cfg.camera.lens_k1, cfg.camera.lens_k2
            )
            world = unproject_to_plane(params, undistorted, float(w), float(h), plane_z)
            if not np.all(np.isfinite(world[0])):
                continue
            worlds.append((box.track_id, float(world[0, 0]), float(world[0, 1])))
        det_world_cache[plane_z] = worlds
        return worlds

    radius_sq = cfg.detection.assist_radius_m**2

    for mid in sorted(target_ids):
        out_marker = app._server.get_marker(mid)
        if out_marker is None:
            continue
        anchor = get_or_create_manual_marker(app, mid)
        state = _get_pin_state(app, mid)
        manual_x, manual_y, manual_z = anchor.pos
        plane_z = cfg.grid.z_offset if use_bottom else manual_z
        _assist_one(
            cfg,
            state,
            out_marker,
            mid,
            manual_x,
            manual_y,
            manual_z,
            _det_worlds_for(plane_z),
            radius_sq,
            steps,
        )


def _assist_one(
    cfg: Any,
    state: DetectionPinState,
    out_marker: Any,
    marker_id: int,
    manual_x: float,
    manual_y: float,
    manual_z: float,
    det_worlds: list[tuple[int, float, float]],
    radius_sq: float,
    steps: float,
) -> None:
    """Glide one assist marker's output toward the person nearest its anchor.

    ``det_worlds`` is the precomputed ``(track_id, x, y)`` of every detection on
    this marker's unproject plane (PSN-absolute). Mutates ``state`` and writes
    ``out_marker``.
    """
    # Always follow the detection nearest the operator's manual anchor – never a
    # bigger box, never a previously-locked one. The operator retargets simply by
    # nudging the anchor toward a different person; the never-reset outer glide
    # absorbs the hand-over so the output still never snaps.
    chosen_id: int | None = None
    chosen_world: tuple[float, float] | None = None
    best_dsq = radius_sq
    for track_id, wx, wy in det_worlds:
        dsq = (wx - manual_x) ** 2 + (wy - manual_y) ** 2
        if dsq <= best_dsq:
            best_dsq = dsq
            chosen_id, chosen_world = track_id, (wx, wy)

    if chosen_world is None:
        # No detection near the anchor – target the anchor itself so the AI
        # marker glides home. Drop the person lock + velocity but keep the
        # outer glide so the output never snaps.
        state.soft_release()
        target_x, target_y = manual_x, manual_y
    else:
        # New lock (or first acquisition) – re-seed the inner de-jitter so the
        # person target tracks cleanly. The outer glide is untouched, so the
        # output still eases (never jumps) onto the newly-locked person.
        if chosen_id != state.locked_track_id:
            state.smooth_x = None
            state.smooth_y = None
            state.vel_x = 0.0
            state.vel_y = 0.0
            state.prev_target_x = None
            state.prev_target_y = None
            state.locked_track_id = chosen_id
        smooth_x, smooth_y = _advance_smoothing_steps(state, chosen_world[0], chosen_world[1], cfg, steps)
        # ``assist_strength`` is the in-range clip ratio: 1.0 sits exactly on the
        # person; lower blends toward the manual anchor.
        strength = cfg.detection.assist_strength
        target_x = manual_x + strength * (smooth_x - manual_x)
        target_y = manual_y + strength * (smooth_y - manual_y)

    # Outer glide: ease the AI marker toward this frame's target. Seeded once
    # from the registered marker's pos; never reset → the output never snaps. The
    # glide rate is frame-rate-independent (re-derived from the shared ``steps``).
    glide = _ema_factor(cfg.detection.smoothing, steps)
    if state.ai_smooth_x is None or state.ai_smooth_y is None:
        out_x, out_y, _ = out_marker.pos
        state.ai_smooth_x = out_x
        state.ai_smooth_y = out_y
    state.ai_smooth_x += glide * (target_x - state.ai_smooth_x)
    state.ai_smooth_y += glide * (target_y - state.ai_smooth_y)
    out_marker.set_pos(state.ai_smooth_x, state.ai_smooth_y, manual_z)

    # The locked person (if any) is the attached box; the overlay paints it in
    # the output marker's colour. ``None`` while gliding home leaves all boxes
    # in the default detection colour.
    state.attached_track_id = state.locked_track_id
    state.attached_marker_id = marker_id


def _prune_manual_markers(app: Any, *, keep: set[int]) -> None:
    """Drop stale manual ghosts so a re-engage re-seeds fresh from the marker.

    ``keep`` is the set of ids currently under assist control (empty when assist
    is off). Any other ghost is discarded – when the operator changes the
    controlled set or turns assist off, the old anchors must not linger.
    """
    stale = [mid for mid in app._assist_manual if mid not in keep]
    for mid in stale:
        del app._assist_manual[mid]


def _prune_pin_states(app: Any, *, keep: set[int]) -> None:
    """Drop per-marker pin states for markers no longer driven.

    Parallels :func:`_prune_manual_markers`. A marker that leaves the driven set
    (controlled set shrinks, mode switch, detection off) sheds its smoothing /
    glide state so a later re-entry seeds fresh from the live marker position.
    """
    stale = [mid for mid in app._detection_pin_states if mid not in keep]
    for mid in stale:
        del app._detection_pin_states[mid]
