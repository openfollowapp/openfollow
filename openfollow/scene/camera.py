# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Virtual perspective camera (PSN Z-up) plus PSN↔pygfx coordinate conversions."""

from __future__ import annotations

from typing import Any

import numpy as np
import numpy.typing as npt

from openfollow.configuration import CameraConfig


def psn_to_pygfx(x: float, y: float, z: float) -> tuple[float, float, float]:
    """Convert PSN Z-up coords to pygfx Y-up coords.

    PSN: X=stage left, Y=upstage, Z=up
    pygfx: X=right, Y=up, Z=towards viewer
    """
    return (x, z, -y)


def psn_to_pygfx_array(pts: npt.NDArray[Any]) -> npt.NDArray[np.float64]:
    """Convert Nx3 PSN (X-right, Y-upstage, Z-up) to pygfx (X, Y-up, Z-towards-viewer)."""
    # Force float64 rather than inheriting pts' dtype: an integer input array
    # would otherwise truncate the converted coordinates and corrupt the
    # projection that consumes this output.
    out = np.empty((pts.shape[0], 3), dtype=np.float64)
    out[:, 0] = pts[:, 0]  # X unchanged
    out[:, 1] = pts[:, 2]  # pygfx Y = PSN Z (up)
    out[:, 2] = -pts[:, 1]  # pygfx Z = -PSN Y
    return out


class Camera:
    """Virtual perspective camera stored in PSN Z-up convention.

    Position and rotation are kept as plain Python values; coordinate-system
    conversion helpers (psn_to_pygfx / psn_to_pygfx_array) are provided for
    the projection math in solver.py.

    ``fov`` is **horizontal** field of view in degrees – matches the datasheet
    convention every lens/camera vendor uses. The projection kernel in
    ``solver.py`` converts to vertical internally.
    """

    def __init__(
        self,
        position: tuple[float, float, float] = (0.0, -10.0, 5.0),
        pitch: float = -20.0,
        yaw: float = 0.0,
        roll: float = 0.0,
        fov: float = 60.0,
    ) -> None:
        self._pos: list[float] = list(position)
        self.pitch = pitch  # degrees
        self.yaw = yaw
        self.roll = roll
        self._fov: float = max(1.0, min(179.0, fov))

    @classmethod
    def from_config(cls, config: CameraConfig) -> Camera:
        """Create a Camera from a CameraConfig."""
        return cls(
            position=(config.pos_x, config.pos_y, config.pos_z),
            pitch=config.pitch,
            yaw=config.yaw,
            roll=config.roll,
            fov=config.fov,
        )

    def to_config(self) -> CameraConfig:
        return CameraConfig(
            pos_x=self._pos[0],
            pos_y=self._pos[1],
            pos_z=self._pos[2],
            pitch=self.pitch,
            yaw=self.yaw,
            roll=self.roll,
            fov=self._fov,
        )

    @property
    def fov(self) -> float:
        return self._fov

    def move(self, dx: float, dy: float, dz: float) -> None:
        """Move in PSN world coords (Z-up)."""
        self._pos[0] += dx
        self._pos[1] += dy
        self._pos[2] += dz

    def rotate(self, dpitch: float, dyaw: float, droll: float = 0.0) -> None:
        self.pitch = max(-89.0, min(89.0, self.pitch + dpitch))
        self.yaw += dyaw
        self.roll += droll

    def set_fov(self, fov: float) -> None:
        self._fov = max(1.0, min(179.0, fov))

    def apply_config(self, config: CameraConfig) -> None:
        """Update camera parameters in-place from a CameraConfig."""
        self._pos = [config.pos_x, config.pos_y, config.pos_z]
        self.pitch = config.pitch
        self.yaw = config.yaw
        self.roll = config.roll
        self._fov = max(1.0, min(179.0, config.fov))
