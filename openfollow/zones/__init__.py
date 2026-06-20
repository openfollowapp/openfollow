# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Trigger zones: polygonal world-coordinate regions that emit OSC on entry/exit."""

from openfollow.zones.engine import ZoneEngine, ZoneOccupancy
from openfollow.zones.geometry import point_in_polygon, shrink_polygon

__all__ = [
    "ZoneEngine",
    "ZoneOccupancy",
    "point_in_polygon",
    "shrink_polygon",
]
