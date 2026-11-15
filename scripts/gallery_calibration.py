# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Michel Honold and Paul Hermann
"""Write a Media Gallery default's calibration next to its picture: the camera and grid it was drawn with.

The file holds ``[camera]`` and ``[grid]`` in ``config.toml`` form and takes the picture's name
(``concert_default.jpg`` gives ``concert_default.toml``). A lens centre is recorded only when the
picture was drawn about one; a missing key is the config's 0. Nothing reads it at runtime.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

import tomli_w

POSE_KEYS = ("pos_x", "pos_y", "pos_z", "pitch", "yaw", "roll", "fov")
CAMERA_KEYS = (*POSE_KEYS, "lens_k1", "lens_k2")
CENTRE_KEYS = ("lens_cx", "lens_cy")
GRID_KEYS = ("width", "depth", "spacing", "x_offset", "y_offset", "z_offset")


def camera_from(params: Sequence[float], k1: float, k2: float, cx: float = 0.0, cy: float = 0.0) -> dict[str, float]:
    """The ``[camera]`` keys from a ``project_points`` parameter vector and a lens (centre in half-diagonals)."""
    camera = {**dict(zip(POSE_KEYS, params, strict=True)), "lens_k1": k1, "lens_k2": k2}
    if cx or cy:
        camera |= {"lens_cx": cx, "lens_cy": cy}
    return camera


def write_calibration(picture: Path, script: str, camera: Mapping[str, float], grid: Mapping[str, float]) -> Path:
    path = picture.with_suffix(".toml")
    body = tomli_w.dumps(
        {
            "camera": {key: float(camera[key]) for key in (*CAMERA_KEYS, *CENTRE_KEYS) if key in camera},
            "grid": {key: float(grid[key]) for key in GRID_KEYS},
        }
    )
    path.write_text(f"# The camera and grid {picture.name} was drawn with, written by {script}.\n\n{body}")
    return path
