# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Scene geometry: virtual camera and 4-point DLT calibration solver."""

from openfollow.scene.camera import Camera
from openfollow.scene.solver import project_points, solve_camera_dlt

__all__ = ["Camera", "project_points", "solve_camera_dlt"]
