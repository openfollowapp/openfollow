# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Turn relative XY input (stick right, stick up) into a stage XY velocity.

``marker.control_frame`` picks the stage directions "right" and "up" mean:

- ``stage``: stage left (+X) and upstage (+Y).
- ``heading``: those turned to the bearing ``marker.control_heading``, in the
  camera's yaw convention.
- ``camera``: the camera's right and view direction, flattened onto the floor.
- ``screen``: right and up the picture at the marker ball, at its own height;
  ``camera`` where the picture gives no answer (ball off the picture, behind
  the camera, at the horizon).

Each source latches its directions when its input leaves zero and drops them
when it returns to zero or stops reporting, so nothing bends a move in
progress. The output keeps the input's magnitude; Z is never touched.
"""

from __future__ import annotations

import math
from collections.abc import Hashable
from typing import TYPE_CHECKING

import numpy as np

from openfollow.scene.solver import PlanarAxes, bearing_axes, camera_params, ground_axes, screen_axes_at
from openfollow.window import operator_screen_size

if TYPE_CHECKING:
    import numpy.typing as npt

    from openfollow.app import OpenFollowApp

STAGE_AXES: PlanarAxes = ((1.0, 0.0), (0.0, 1.0))


class ControlFrame:
    """Per-source latched mapping from relative input to stage XY."""

    def __init__(self, app: OpenFollowApp) -> None:
        self._app = app
        self._latched: dict[Hashable, PlanarAxes] = {}
        self._used: set[Hashable] = set()

    def release_all(self) -> None:
        """Drop every latch, for input coming back from a mode that held it."""
        self._latched.clear()
        self._used.clear()

    def begin_frame(self) -> None:
        """Drop the latch of every source that gave no input last frame."""
        for source in self._latched.keys() - self._used:
            del self._latched[source]
        self._used.clear()

    def orient(
        self, source: Hashable, vx: float, vy: float, marker_pos: tuple[float, float, float]
    ) -> tuple[float, float]:
        """Map ``source``'s (right, up) input to a stage (X, Y) velocity of the same magnitude."""
        if vx == 0.0 and vy == 0.0:
            self._latched.pop(source, None)
            return 0.0, 0.0
        self._used.add(source)
        axes = self._latched.get(source)
        if axes is None:
            axes = self._latched[source] = self._axes(marker_pos)
        (rx, ry), (ux, uy) = axes
        ox, oy = vx * rx + vy * ux, vx * ry + vy * uy
        # Screen axes need not be perpendicular, so a diagonal can change length.
        length = math.hypot(ox, oy)
        if length == 0.0:
            return 0.0, 0.0
        scale = math.hypot(vx, vy) / length
        return ox * scale, oy * scale

    def _axes(self, marker_pos: tuple[float, float, float]) -> PlanarAxes:
        marker_cfg = self._app._config.marker
        frame = marker_cfg.control_frame
        if frame == "heading":
            return bearing_axes(marker_cfg.control_heading)
        params = self._camera_params() if frame in ("camera", "screen") else None
        if params is None:
            return STAGE_AXES
        if frame == "screen":
            w, h = operator_screen_size(self._app)
            if w > 0 and h > 0:
                cam = self._app._config.camera
                axes = screen_axes_at(params, marker_pos, float(w), float(h), cam.lens_k1, cam.lens_k2)
                if axes is not None:
                    return axes
        return ground_axes(params)

    def _camera_params(self) -> npt.NDArray[np.float64] | None:
        camera = self._app._camera
        return None if camera is None else camera_params(camera.to_config())
