# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Mouse input handler for marker control.

Left-click on a marker's ground circle takes control of that marker;
right-click releases. While held, pointer movement unprojects screen
coordinates onto the stage floor plane and steers the marker toward
them. The scroll wheel adjusts the marker's Z (height) axis.

Steering refinements (all configurable on ``ControllerConfig``):
- ``mouse_hysteresis_px`` – a pixel deadband on the cursor so hand-tremor
  doesn't wiggle the marker.
- ``mouse_smoothing`` – an EMA glide toward the cursor target, applied
  every frame by :meth:`MouseHandler.update`.
- ``mouse_max_distance`` – ignore targets farther than this from the world
  origin so a stray move near the camera horizon can't fling the marker.
"""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING

import numpy as np

from openfollow.runtime.services_detection_pin import (
    assist_pinned_marker_id,
    get_or_create_manual_marker,
)
from openfollow.scene.solver import (
    ground_circle_world_ring,
    project_points,
    unproject_to_plane,
)
from openfollow.zones.geometry import point_in_polygon

if TYPE_CHECKING:
    import numpy.typing as npt

    from openfollow.app import OpenFollowApp
    from openfollow.psn.marker import Marker

logger = logging.getLogger(__name__)

# GTK button constants
_BUTTON_LEFT = 1
_BUTTON_RIGHT = 3

# Minimum grab radius (screen px) around a marker's projected ground-circle
# centre, so a small or distant circle stays clickable as a touch target.
_MIN_GRAB_PX = 14.0

# Below this world-distance the EMA glide is treated as settled and snapped to
# the target, so idle frames stop rewriting the marker position.
_SETTLE_EPS = 1e-4


class MouseHandler:
    """Translates mouse events into absolute marker positions.

    Control is grabbed by left-clicking a marker's ground circle and released
    with a right-click. Pointer moves record a target (after the pixel
    hysteresis deadband); :meth:`update` glides the marker toward it each frame.
    """

    def __init__(self, app: OpenFollowApp) -> None:
        self._app = app
        self._active: bool = False
        # Hysteresis baseline: last cursor point that passed the deadband.
        self._anchor_screen: tuple[float, float] | None = None
        # EMA target (world x, y) and the current smoothed world position.
        self._target_world: tuple[float, float] | None = None
        self._smooth_world: tuple[float, float] | None = None
        # Pre-allocated NumPy buffers (same pattern as services_detection_pin)
        self._cam_buffer = np.zeros(7, dtype=np.float64)
        self._screen_buffer = np.zeros((1, 2), dtype=np.float64)

    @property
    def active(self) -> bool:
        return self._active

    def deactivate(self) -> None:
        """Disarm mouse control without a right-click.

        Called when ``mouse_enabled`` is toggled off mid-session so a stale
        ``_active`` can't snap the marker to the cursor on the first pointer
        event after it's re-enabled.
        """
        if self._active:
            self._active = False
            self._reset_tracking()
            logger.info("Mouse input deactivated (mouse control disabled)")

    def on_pointer_down(
        self,
        x: float,
        y: float,
        button: int,
    ) -> bool:
        """Handle button press. Returns True if consumed."""
        if button == _BUTTON_LEFT:
            return self._grab(x, y)
        if button == _BUTTON_RIGHT:
            if self._active:
                self._active = False
                self._reset_tracking()
                logger.info("Mouse input deactivated")
                return True
        return False

    def on_pointer_move(self, x: float, y: float) -> bool:
        """Record the latest cursor target. Returns True if consumed."""
        if not self._active:
            return False
        threshold = self._app._config.controller.mouse_hysteresis_px
        if self._anchor_screen is not None and threshold > 0.0:
            ax, ay = self._anchor_screen
            if math.hypot(x - ax, y - ay) < threshold:
                # Below the deadband – swallow the move so tremor can't wiggle
                # the marker, but keep the existing target.
                return True
        self._anchor_screen = (x, y)
        world = self._unproject(x, y)
        if world is None:
            return True
        wx, wy = world
        max_d = self._app._config.controller.mouse_max_distance
        if max_d > 0.0 and math.hypot(wx, wy) > max_d:
            # Target out of bounds (e.g. near the camera horizon) – hold the
            # last good target rather than flinging the marker to the far field.
            return True
        self._target_world = (wx, wy)
        return True

    def on_pointer_up(
        self,
        x: float,
        y: float,
        button: int,
    ) -> bool:
        """Handle button release. Returns True if consumed."""
        # No action needed on release – control persists until right-click.
        return False

    def on_wheel(self, dy: float) -> bool:
        """Adjust Z height of the controlled marker. True if consumed."""
        if not self._active:
            return False
        cfg = self._app._config.controller
        if not cfg.mouse_wheel_z_enabled:
            return False
        marker = self._get_selected_marker()
        if marker is None:
            return False
        x, y, z = marker.pos
        # ``dy`` is sign-normalised at the emit site so scrolling UP is positive
        # on both smooth- and discrete-scroll devices; invert flips that.
        sign = -1.0 if cfg.mouse_wheel_invert else 1.0
        marker.set_pos(x, y, z + sign * dy * cfg.mouse_wheel_z_step)
        return True

    def update(self) -> None:
        """Glide the controlled marker toward the cursor target.

        Runs every frame (display tick) so smoothing advances independent of
        pointer-event rate and settles on the target when the cursor stops.
        ``mouse_smoothing`` is a per-frame EMA factor (1.0 = instant).
        """
        if not self._active:
            return
        app = self._app
        if not app._config.controller.mouse_enabled:
            return
        marker = self._get_selected_marker()
        if marker is None or self._target_world is None:
            return
        tx, ty = self._target_world
        if self._smooth_world is None:
            mx, my, _ = marker.pos
            self._smooth_world = (mx, my)
        alpha = app._config.controller.mouse_smoothing
        sx, sy = self._smooth_world
        nx = sx + alpha * (tx - sx)
        ny = sy + alpha * (ty - sy)
        if alpha >= 1.0 or (abs(nx - tx) < _SETTLE_EPS and abs(ny - ty) < _SETTLE_EPS):
            nx, ny = tx, ty
        self._smooth_world = (nx, ny)
        cur_x, cur_y, cur_z = marker.pos
        if abs(nx - cur_x) > 1e-9 or abs(ny - cur_y) > 1e-9:
            # Marker positions are PSN-absolute – same frame as the
            # unproject output, the renderer, the zone engine, and outbound
            # PSN packets. Keep existing Z – the scroll wheel owns it.
            marker.set_pos(nx, ny, cur_z)

    # ------------------------------------------------------------------

    def _reset_tracking(self) -> None:
        self._anchor_screen = None
        self._target_world = None
        self._smooth_world = None

    def _grab(self, x: float, y: float) -> bool:
        """Take control of the marker whose ground circle is under the cursor.

        Returns True (consumed) only when a marker is grabbed; a click on empty
        space is a no-op so it can't snap a marker to the cursor.
        """
        tid = self._hit_test(x, y)
        if tid is None:
            return False
        self._app._selected_id = tid
        self._active = True
        self._anchor_screen = (x, y)
        # No target until the first move, so the grab itself never yanks the
        # marker to the click. ``update`` seeds the glide from the marker's
        # current position the first time a move sets a target.
        self._target_world = None
        self._smooth_world = None
        logger.info("Mouse grabbed marker %s", tid)
        return True

    def _get_selected_marker(self) -> Marker | None:
        """Return the marker mouse control should steer, or None.

        In detection assist mode the selected marker's input is redirected to
        its manual ghost anchor (the detection pin owns the registered marker
        as the AI-corrected output), so mouse drag + wheel-Z move the anchor.
        """
        app = self._app
        if app._selected_id is None:
            return None
        if app._selected_id == assist_pinned_marker_id(app):
            return get_or_create_manual_marker(app, app._selected_id)
        if app._server is None:
            return None
        return app._server.get_marker(app._selected_id)

    def _view_inputs(self) -> tuple[npt.NDArray[np.float64], int, int] | None:
        """Build the 7-float camera buffer + canvas size, or None if unusable."""
        app = self._app
        if app._camera is None:  # pragma: no cover
            # Mouse events only flow once the canvas (and camera) are wired up;
            # the None arm is unreachable at runtime but keeps the type checker
            # honest – treat it as a defensive no-op rather than ``assert``.
            return None
        # Canvas size must match the coordinate space of pointer events
        # (window client pixels), not the video's native resolution.
        if app._canvas is not None:
            w, h = app._canvas.get_canvas_size()
        else:
            w, h = app._config.window_width, app._config.window_height
        if w <= 0 or h <= 0:
            return None
        cam_cfg = app._camera.to_config()
        buf = self._cam_buffer
        buf[0] = cam_cfg.pos_x
        buf[1] = cam_cfg.pos_y
        buf[2] = cam_cfg.pos_z
        buf[3] = cam_cfg.pitch
        buf[4] = cam_cfg.yaw
        buf[5] = cam_cfg.roll
        buf[6] = cam_cfg.fov
        return buf, w, h

    def _unproject(self, x: float, y: float) -> tuple[float, float] | None:
        """Unproject a screen point onto the stage plane; None if off-plane."""
        view = self._view_inputs()
        if view is None:
            return None
        buf, w, h = view
        self._screen_buffer[0, 0] = x
        self._screen_buffer[0, 1] = y
        plane_z = self._app._config.grid.z_offset
        world = unproject_to_plane(buf, self._screen_buffer, float(w), float(h), plane_z)
        if not np.all(np.isfinite(world[0])):
            return None
        return float(world[0, 0]), float(world[0, 1])

    def _hit_test(self, x: float, y: float) -> int | None:
        """Return the controlled marker whose ground circle is under (x, y).

        Tests the click against each marker's projected ground-circle polygon
        (falling back to a fixed pixel radius when the circle is too small or
        rendered off), and picks the marker whose centre is nearest the click.
        """
        app = self._app
        if app._server is None:
            return None
        view = self._view_inputs()
        if view is None:
            return None
        buf, w, h = view
        cfg = app._config
        z_off = cfg.grid.z_offset
        gc_on = cfg.marker.ground_circle
        gc_size = cfg.marker.ground_circle_size
        best_tid: int | None = None
        best_dist = float("inf")
        for tid in app._controlled_ids:
            marker = app._server.get_marker(tid)
            if marker is None:
                continue
            mx, my, _ = marker.pos
            center = project_points(buf, np.array([[mx, my, z_off]], dtype=np.float64), float(w), float(h))[0]
            if not np.all(np.isfinite(center)):
                continue
            cx, cy = float(center[0]), float(center[1])
            dist = math.hypot(x - cx, y - cy)
            hit = False
            if gc_on and gc_size > 0.0:
                ring = ground_circle_world_ring(mx, my, z_off, gc_size)
                ring_scr = project_points(buf, np.array(ring, dtype=np.float64), float(w), float(h))
                finite = ring_scr[np.all(np.isfinite(ring_scr), axis=1)]
                if len(finite) >= 3:
                    polygon = [(float(px), float(py)) for px, py in finite]
                    hit = point_in_polygon(x, y, polygon)
            if not hit and dist <= _MIN_GRAB_PX:
                hit = True
            if hit and dist < best_dist:
                best_dist = dist
                best_tid = tid
        return best_tid
