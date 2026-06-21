# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Mouse input handler for marker control.

Left-click activates mouse control, right-click deactivates it.
While active, mouse movement sets the selected marker position by
unprojecting screen coordinates onto the stage plane.  The scroll
wheel adjusts the marker's Z (height) axis.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np

from openfollow.runtime.services_detection_pin import (
    get_or_create_manual_marker,
    is_assist_controlled,
)
from openfollow.scene.solver import unproject_to_plane

if TYPE_CHECKING:
    from openfollow.app import OpenFollowApp
    from openfollow.psn.marker import Marker

logger = logging.getLogger(__name__)

# GTK button constants
_BUTTON_LEFT = 1
_BUTTON_RIGHT = 3

# Height change per scroll tick (metres)
_WHEEL_STEP = 0.1


class MouseHandler:
    """Translates mouse events into absolute marker positions.

    Activation is toggled via left-click (start) / right-click (stop).
    While active, pointer-move events are unprojected from screen
    coordinates onto the stage floor plane and applied to the currently
    selected marker.
    """

    def __init__(self, app: OpenFollowApp) -> None:
        self._app = app
        self._active: bool = False
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
            logger.info("Mouse input deactivated (mouse control disabled)")

    def on_pointer_down(
        self,
        x: float,
        y: float,
        button: int,
    ) -> bool:
        """Handle button press. Returns True if consumed."""
        if button == _BUTTON_LEFT:
            self._active = True
            logger.info("Mouse input activated")
            self._apply_position(x, y)
            return True
        if button == _BUTTON_RIGHT:
            if self._active:
                self._active = False
                logger.info("Mouse input deactivated")
                return True
        return False

    def on_pointer_move(self, x: float, y: float) -> bool:
        """Handle mouse movement. Returns True if consumed."""
        if not self._active:
            return False
        self._apply_position(x, y)
        return True

    def on_pointer_up(
        self,
        x: float,
        y: float,
        button: int,
    ) -> bool:
        """Handle button release. Returns True if consumed."""
        # No action needed on release – activation persists until right-click.
        return False

    def on_wheel(self, dy: float) -> bool:
        """Adjust Z height of selected marker. True if consumed."""
        if not self._active:
            return False
        marker = self._get_selected_marker()
        if marker is None:
            return False
        x, y, z = marker.pos
        # Convention: ``dy`` is sign-normalised at the emit site so scrolling
        # UP is positive on both smooth- and discrete-scroll devices, i.e.
        # scroll up raises the marker (increase Z).
        marker.set_pos(x, y, z + dy * _WHEEL_STEP)
        return True

    # ------------------------------------------------------------------

    def _get_selected_marker(self) -> Marker | None:
        """Return the marker mouse control should steer, or None.

        In detection assist mode the selected marker's input is redirected to
        its manual ghost anchor (the detection pin owns the registered marker
        as the AI-corrected output), so mouse drag + wheel-Z move the anchor.
        """
        app = self._app
        if app._selected_id is None:
            return None
        if is_assist_controlled(app, app._selected_id):
            return get_or_create_manual_marker(app, app._selected_id)
        if app._server is None:
            return None
        return app._server.get_marker(app._selected_id)

    def _apply_position(
        self,
        screen_x: float,
        screen_y: float,
    ) -> None:
        """Unproject screen point onto stage plane."""
        app = self._app
        marker = self._get_selected_marker()
        if marker is None:
            return

        # Canvas size must match the coordinate space of pointer events
        # (window client pixels), not the video's native resolution.
        if app._canvas is not None:
            w, h = app._canvas.get_canvas_size()
        else:
            cfg = app._config
            w, h = cfg.window_width, cfg.window_height
        if w <= 0 or h <= 0:
            return

        if app._camera is None:  # pragma: no cover
            # Mouse handler activates from a click on the canvas, which only
            # exists once the camera is wired up. The None arm is therefore
            # unreachable at runtime, but the strict type checker can't see
            # that – keep it as a defensive no-op rather than ``assert``.
            return
        cam_cfg = app._camera.to_config()
        buf = self._cam_buffer
        buf[0] = cam_cfg.pos_x
        buf[1] = cam_cfg.pos_y
        buf[2] = cam_cfg.pos_z
        buf[3] = cam_cfg.pitch
        buf[4] = cam_cfg.yaw
        buf[5] = cam_cfg.roll
        buf[6] = cam_cfg.fov

        self._screen_buffer[0, 0] = screen_x
        self._screen_buffer[0, 1] = screen_y

        plane_z = app._config.grid.z_offset
        world = unproject_to_plane(
            buf,
            self._screen_buffer,
            float(w),
            float(h),
            plane_z,
        )

        if not np.all(np.isfinite(world[0])):
            return

        # Marker positions are PSN-absolute – same frame as unproject_to_plane
        # output, the renderer, the zone engine, and outbound PSN packets.
        # Keep existing Z – scroll wheel controls it independently.
        marker.set_pos(float(world[0, 0]), float(world[0, 1]), marker.pos[2])
