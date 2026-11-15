# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Paul Hermann, Michel Honold, and Vinzenz Schultz
# Copyright (C) 2026 Paul Hermann and Michel Honold
"""Detection pinning helpers for runtime services."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np
import numpy.typing as npt

from openfollow.configuration import effective_lens
from openfollow.input.parking import is_parked
from openfollow.input.touch_remote import holds
from openfollow.runtime.frame_timing import NOMINAL_FRAME_DT, dt_steps, ema_factor
from openfollow.runtime.state_maps import get_or_create, prune_to_keep
from openfollow.scene.solver import invert_overlay_distortion, unproject_to_plane
from openfollow.zones.heights import height_at, raising_zones, zone_outlines

if TYPE_CHECKING:
    from openfollow.psn.marker import Marker


# A detection unprojected for assist: track id, world X and Y.
_DetWorld = tuple[int, float, float]


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
    # Replace mode: the PSN tracker status last written to the driven marker, so
    # /api/stats can show the pin's own view beside the wire. None once released.
    status: float | None = None
    # The zone height each track's feet stood at last frame (``_feet_heights``).
    feet_heights: dict[int, float] = field(default_factory=dict)

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
        self.feet_heights = {}
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
        self.feet_heights = {}


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
    refines every unparked controlled marker, so the input resolvers redirect
    operator movement to a manual ghost for such an id, and the assist pin reads
    that ghost as the clip anchor.
    """
    if marker_id is None:
        return False
    return assist_active(app) and marker_id in app._controlled_ids and not is_parked(app, marker_id)


def _get_pin_state(app: Any, marker_id: int) -> DetectionPinState:
    """Return the per-marker pin-smoothing state, creating it lazily.

    Mirrors :func:`get_or_create_manual_marker`: each driven marker owns its
    smoothing / glide state for its whole driven lifetime, so the never-reset
    outer glide stays continuous across frames.
    """
    state: DetectionPinState = get_or_create(app._detection_pin_states, marker_id, DetectionPinState)
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

    ``pin_marker_id`` defaults to -1 (follow the controller-selected marker,
    idle after the selection moved off a marker that parked until the operator
    selects one); a non-negative id pins that exact marker, but only when it's
    controlled.
    """
    configured_id = cfg.detection.pin_marker_id
    if configured_id < 0:
        if getattr(app, "_pin_waits_for_selection", False):
            return None
        selected_id = app._selected_id
    elif configured_id in app._controlled_ids:
        selected_id = configured_id
    else:
        return None
    if selected_id is None:
        return None
    return app._server.get_marker(selected_id)


def detection_status(score: float, threshold: float, *, age_s: float = 0.0, grace_s: float) -> float:
    """PSN tracker validity for a marker driven by one tracked person.

    ``threshold`` (the configured detection confidence) reads 0.5 and a perfect
    score 1.0; a low-band recovery match continues the same line below 0.5,
    clamped at 0.0. A track coasting on prediction (``age_s`` since its last
    match) decays linearly to 0.0 at ``grace_s``, where the tracker drops it.
    """
    if threshold >= 1.0:
        matched = 1.0
    else:
        matched = min(1.0, max(0.0, 0.5 + 0.5 * (score - threshold) / (1.0 - threshold)))
    if age_s <= 0.0:
        return matched
    if grace_s <= 0.0:
        return 0.0
    return matched * max(0.0, 1.0 - age_s / grace_s)


def _write_status(marker: Any, state: DetectionPinState, status: float) -> None:
    """Publish the pin's validity on the driven marker and remember it for /api/stats."""
    marker.set_status(status)
    state.status = status


def _release_status(app: Any, marker_id: int, state: DetectionPinState) -> None:
    """Hand a marker the pin stops driving back to full validity.

    Runs on every such path: detection off, no detector, mode switch (which
    prunes every state), another marker pinned, marker no longer controlled.
    """
    if state.status is None:
        return
    state.status = None
    marker = app._server.get_marker(marker_id)
    if marker is not None:
        marker.set_status(1.0)


class UnprojectCache:
    """Pin points and feet heights by everything they are worked out from, kept while a frame still asks for them.

    The detector steps slower than the frame loop, so most frames ask what the previous one did. Whatever no
    frame asked for in the last two is let go.
    """

    def __init__(self) -> None:
        self._now: dict[tuple[Any, ...], Any] = {}
        self._last: dict[tuple[Any, ...], Any] = {}

    def next_frame(self) -> None:
        self._last, self._now = self._now, {}

    def get(self, key: tuple[Any, ...], compute: Callable[[], Any]) -> Any:
        if key not in self._now:
            self._now[key] = self._last[key] if key in self._last else compute()
        return self._now[key]


@dataclass(frozen=True)
class _View:
    """What an unprojection of a screen point reads besides the point and the plane, and its cache keys."""

    params: npt.NDArray[Any]
    w: float
    h: float
    lens: tuple[float, float, float, float]
    stage_z: float
    raising: list[tuple[Any, float]]
    cache: UnprojectCache
    buffer: npt.NDArray[Any]
    key: tuple[Any, ...]
    zones_key: tuple[Any, ...]


def _view(
    app: Any, cfg: Any, w: int, h: int, cache: UnprojectCache, cam_buffer: npt.NDArray[Any], buffer: npt.NDArray[Any]
) -> _View:
    params = _load_camera_params(app, cam_buffer)
    lens = effective_lens(cfg)
    raising = zone_outlines(raising_zones(cfg))
    key = (tuple(float(v) for v in params), float(w), float(h), lens)
    zones_key = (cfg.grid.z_offset, tuple(raising))
    return _View(params, float(w), float(h), lens, cfg.grid.z_offset, raising, cache, buffer, key, zones_key)


def _unproject_point(view: _View, x: float, y: float, plane_z: float) -> tuple[float, float] | None:
    """Screen ``(x, y)`` on the (lens-distorted) video, unprojected on ``plane_z``; None where it has no point."""

    def compute() -> tuple[float, float] | None:
        view.buffer[0, 0] = x
        view.buffer[0, 1] = y
        # Detections come from the distorted video: undistort back to the pinhole frame first.
        undistorted = invert_overlay_distortion(view.buffer, view.w, view.h, *view.lens)
        world = unproject_to_plane(view.params, undistorted, view.w, view.h, plane_z)
        return (float(world[0, 0]), float(world[0, 1])) if np.all(np.isfinite(world[0])) else None

    result: tuple[float, float] | None = view.cache.get(("point", view.key, x, y, plane_z), compute)
    return result


def _feet_height(view: _View, x: float, y: float) -> float | None:
    """The zone height a person stands at, read from their feet at screen ``(x, y)``; None when no height fits.

    The feet lie on the surface the person stands on, so the height is the highest of 0 and the raising zones'
    ``z_offset`` whose zones lift the feet, unprojected on the stage plane plus that height, by exactly that
    height. Never the marker's lift, which the pinned position itself moves. No zones: 0.
    """
    if not view.raising:
        return 0.0

    def compute() -> float | None:
        for height in sorted({0.0, *(z for _, z in view.raising)}, reverse=True):
            feet = _unproject_point(view, x, y, view.stage_z + height)
            if feet is not None and height_at(view.raising, *feet) == height:
                return float(height)
        return None

    result: float | None = view.cache.get(("feet", view.key, view.zones_key, x, y), compute)
    return result


def _feet_heights(state: DetectionPinState, found: list[tuple[int, float | None]]) -> list[float]:
    """Each track's height: as found, else (feet in the band at a zone's edge where none fits) its last one.

    Remembered per track, so one person's height never passes to another; untracked boxes keep none.
    """
    heights = [height if height is not None else state.feet_heights.get(track_id, 0.0) for track_id, height in found]
    state.feet_heights = {track_id: h for (track_id, _), h in zip(found, heights, strict=True) if track_id >= 0}
    return heights


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
    dt: float = NOMINAL_FRAME_DT,
) -> tuple[float, float]:
    """Velocity-EMA + prediction lookahead + EMA smoothing of a world target.

    Mutates ``pin_state`` and returns the smoothed ``(x, y)``. Shared by both pin
    modes so the filter stays identical; tune it once, here. ``dt`` (seconds since
    the previous frame) makes the EMAs and the lookahead frame-rate-independent:
    velocity is tracked per nominal frame and ``prediction`` keeps its scale, so
    the lookahead distance is the same at 60fps or 30fps and across stalls.
    """
    return _advance_smoothing_steps(pin_state, target_x, target_y, cfg, dt_steps(dt))


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
    vel_alpha = ema_factor(0.3, steps)
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

    alpha = ema_factor(cfg.detection.smoothing, steps)
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
    dt: float = NOMINAL_FRAME_DT,
    unproject_cache: UnprojectCache | None = None,
) -> None:
    """Drive controlled marker(s) from detection with EMA smoothing.

    Detection's only on/off is ``enabled``. In ``assist`` mode every controlled
    marker is refined; in ``replace`` mode the single resolved marker is auto-
    pinned. ``dt`` is the seconds elapsed since the previous animate frame; it
    makes the smoothing / prediction frame-rate-independent (see
    :func:`_advance_smoothing`). ``unproject_cache`` keeps unprojections from frame
    to frame; without one, each call works everything out afresh.
    """
    if unproject_cache is None:
        unproject_cache = UnprojectCache()
    unproject_cache.next_frame()
    cfg = app._config
    det = cfg.detection
    if not det.enabled:
        # Detection off – drop every ghost + per-marker state so a later
        # re-engage re-seeds fresh from the live markers.
        _prune_manual_markers(app, keep=set())
        _prune_pin_states(app, keep=set())
        return

    if det.pin_mode != app._detection_pin_mode:
        # A mode switch starts clean: the other mode's smoothing and status must
        # not leak into its first frame. Releases what the dropped states hold.
        _prune_pin_states(app, keep=set())
        app._detection_pin_mode = det.pin_mode

    if det.pin_mode == "assist":
        # Assist runs even without a live detector: input is redirected to the
        # manual ghosts, so each output keeps gliding to its anchor (degrading to
        # pure manual) rather than freezing.
        _apply_assist_all(
            app,
            person_detector=person_detector,
            unproject_cam_buffer=unproject_cam_buffer,
            screen_point_buffer=screen_point_buffer,
            unproject_cache=unproject_cache,
            dt=dt,
        )
        return

    # Replace mode (Fully Automatic): auto-pin the single resolved marker. Assist
    # ghosts are unused here, so drop them all.
    _prune_manual_markers(app, keep=set())
    marker = _resolve_pinned_marker(app, cfg)
    # A marker the touch remote holds is the remote's alone; a parked one waits until it is unparked.
    if marker is None or holds(app, marker.marker_id) or is_parked(app, marker.marker_id):
        _prune_pin_states(app, keep=set())
        return
    _prune_pin_states(app, keep={marker.marker_id})
    pin_state = _get_pin_state(app, marker.marker_id)
    # Recomputed each frame: clear the attached-box marker first so a frame with
    # no attachment (no detector, lost detection) shows no coloured box.
    pin_state.attached_track_id = None
    pin_state.attached_marker_id = None

    if person_detector is None or not person_detector.available:
        # No detector, or one that never loaded a backend: the operator drives
        # the marker, so it is fully valid.
        _release_status(app, marker.marker_id, pin_state)
        return

    best = person_detector.tracked_detection
    if best is None:
        # Detection dropped – clear smoothing so reacquisition snaps, not lerps.
        pin_state.reset()
        # Nobody tracked: the position is only as valid as detection makes it.
        _write_status(marker, pin_state, 0.0)
        return

    w, h = app._video_receiver.resolution
    if w <= 0 or h <= 0:
        # No feed to place the person on: detection vouches for nothing.
        _write_status(marker, pin_state, 0.0)
        return

    # The tracked person is the attached box; the overlay paints it in the
    # driven marker's colour.
    pin_state.attached_track_id = best.track_id
    pin_state.attached_marker_id = marker.marker_id

    use_bottom = det.pin_point == "bottom"
    screen_x = (best.x1 + best.x2) / 2.0 * w
    screen_y = (best.y2 if use_bottom else best.y1) * h

    view = _view(app, cfg, w, h, unproject_cache, unproject_cam_buffer, screen_point_buffer)
    # The head on the marker's Z, the feet on the stage, each raised by the zone the feet stand in.
    (height,) = _feet_heights(pin_state, [(best.track_id, _feet_height(view, screen_x, best.y2 * h))])
    point = _unproject_point(view, screen_x, screen_y, (cfg.grid.z_offset if use_bottom else marker.pos[2]) + height)
    if point is None:
        _write_status(marker, pin_state, 0.0)
        return

    # PSN-absolute world coords (canonical marker.pos frame).
    smooth_x, smooth_y = _advance_smoothing(pin_state, point[0], point[1], cfg, dt)
    # Threshold and window come from the detector, which drains config on its own cadence.
    status = detection_status(
        best.confidence,
        person_detector.confidence_threshold,
        age_s=best.age_s,
        grace_s=person_detector.coast_s,
    )
    # On a path the marker stays on it: the person's place projects onto the path.
    paths = getattr(app, "_paths", None)
    on_path = paths.project(marker.marker_id, smooth_x, smooth_y) if paths is not None else None
    x, y, z = on_path if on_path is not None else (smooth_x, smooth_y, marker.pos[2])
    # One write: no packet pairs this position with the previous frame's status.
    marker.set_pos(x, y, z, status=status)
    pin_state.status = status


def _apply_assist_all(
    app: Any,
    *,
    person_detector: Any,
    unproject_cam_buffer: npt.NDArray[Any],
    screen_point_buffer: npt.NDArray[Any],
    unproject_cache: UnprojectCache,
    dt: float = NOMINAL_FRAME_DT,
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
    # A parked marker keeps the position it was parked at; its anchor and glide re-seed there on unpark.
    target_ids = {mid for mid in app._controlled_ids if not is_parked(app, mid)}
    _prune_manual_markers(app, keep=target_ids)
    _prune_pin_states(app, keep=target_ids)
    if not target_ids:
        return

    paths = getattr(app, "_paths", None)
    w, h = app._video_receiver.resolution
    if w <= 0 or h <= 0:
        # No feed to place anyone on: an output locked onto a path is still held on it where it stands.
        for mid in target_ids:
            out = app._server.get_marker(mid)
            on_path = paths.constrain(mid, out.pos[0], out.pos[1]) if paths is not None and out is not None else None
            if on_path is not None:
                out.set_pos(*on_path)
        return

    steps = dt_steps(dt)  # shared by the inner smoothing and the outer glide
    use_bottom = cfg.detection.pin_point == "bottom"
    view = _view(app, cfg, w, h, unproject_cache, unproject_cam_buffer, screen_point_buffer)

    # No live detector (deps missing) → no detections, so each output simply
    # glides to its manual anchor; the operator keeps control.
    detections = person_detector.detections if person_detector is not None else []
    # Each detection's zone height is read from its feet; its pin point is unprojected on
    # the marker's plane (the anchor's Z for the head, the stage for the feet) raised by it.
    # ``unproject_cache`` makes both once per question, across markers and frames alike.
    found = [(box.track_id, _feet_height(view, (box.x1 + box.x2) / 2.0 * w, box.y2 * h)) for box in detections]

    def _det_worlds_for(base_z: float, heights: list[float]) -> list[_DetWorld]:
        worlds: list[_DetWorld] = []
        for box, height in zip(detections, heights, strict=True):
            x, y = (box.x1 + box.x2) / 2.0 * w, (box.y2 if use_bottom else box.y1) * h
            point = _unproject_point(view, x, y, base_z + height)
            if point is not None:
                worlds.append((box.track_id, *point))
        return worlds

    radius_sq = cfg.detection.assist_radius_m**2

    for mid in sorted(target_ids):
        out_marker = app._server.get_marker(mid)
        if out_marker is None:
            continue
        anchor = get_or_create_manual_marker(app, mid)
        state = _get_pin_state(app, mid)
        manual_x, manual_y, manual_z = anchor.pos
        det_worlds = _det_worlds_for(cfg.grid.z_offset if use_bottom else manual_z, _feet_heights(state, found))
        _assist_one(
            cfg,
            state,
            out_marker,
            mid,
            manual_x,
            manual_y,
            manual_z,
            det_worlds,
            radius_sq,
            steps,
            paths,
        )


def _assist_one(
    cfg: Any,
    state: DetectionPinState,
    out_marker: Any,
    marker_id: int,
    manual_x: float,
    manual_y: float,
    manual_z: float,
    det_worlds: list[_DetWorld],
    radius_sq: float,
    steps: float,
    paths: Any = None,
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
    glide = ema_factor(cfg.detection.smoothing, steps)
    if state.ai_smooth_x is None or state.ai_smooth_y is None:
        out_x, out_y, _ = out_marker.pos
        state.ai_smooth_x = out_x
        state.ai_smooth_y = out_y
    state.ai_smooth_x += glide * (target_x - state.ai_smooth_x)
    state.ai_smooth_y += glide * (target_y - state.ai_smooth_y)
    # On a path the output stays on it too; the anchor's place there is left alone.
    on_path = paths.constrain(marker_id, state.ai_smooth_x, state.ai_smooth_y) if paths is not None else None
    out_marker.set_pos(*(on_path if on_path is not None else (state.ai_smooth_x, state.ai_smooth_y, manual_z)))

    # The locked person (if any) is the attached box; the overlay paints it in
    # the output marker's colour. ``None`` while gliding home leaves all boxes
    # in the default detection colour.
    state.attached_track_id = state.locked_track_id
    state.attached_marker_id = marker_id


def hand_over_anchors(app: Any) -> None:
    """Drop the anchor of every marker no longer under assist control, its path lock going to the output.

    Called before anything writes a marker in the frame, so input moves a locked marker on from its output.
    """
    _prune_manual_markers(app, keep={m for m in app._assist_manual if is_assist_controlled(app, m)})


def _prune_manual_markers(app: Any, *, keep: set[int]) -> None:
    """Drop stale manual ghosts so a re-engage re-seeds fresh from the marker.

    ``keep`` is the set of ids currently under assist control (empty when assist
    is off). Any other ghost is discarded – when the operator changes the
    controlled set or turns assist off, the old anchors must not linger.
    """
    paths = getattr(app, "_paths", None)
    for marker_id, _anchor in prune_to_keep(app._assist_manual, keep):
        out = app._server.get_marker(marker_id)
        if paths is not None and out is not None:
            # A lock held the anchor's place; the output, which input drives from now, takes it over.
            paths.take_over(marker_id, out.pos)


def _prune_pin_states(app: Any, *, keep: set[int]) -> None:
    """Drop per-marker pin states for markers no longer driven.

    Parallels :func:`_prune_manual_markers`. A marker that leaves the driven set
    (controlled set shrinks, mode switch, detection off) sheds its smoothing /
    glide state so a later re-entry seeds fresh from the live marker position,
    and gets its full validity back on the wire.
    """
    for marker_id, state in prune_to_keep(app._detection_pin_states, keep):
        _release_status(app, marker_id, state)
