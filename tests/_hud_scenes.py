# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Operator Screen scenes for the cost-proxy tests and ``scripts/hw_validation/hud_bench.py``.

Each scene is a list of frames ``(width, height, OverlayState)`` with markers on
fixed curves, so marker cards, zone occupancy and the caches change from frame
to frame as on a running station. Only fields the imported ``OverlayState``
has are set, so the bench can build the same scenes against an older checkout;
``DROPPED`` names the ones left out.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Callable
from typing import Any

import numpy as np

from openfollow.runtime.overlay_state import MarkerOverlayData, OverlayState
from openfollow.zones.geometry import point_in_polygon

WIDTH, HEIGHT = 1920, 1080
FRAMES = 60

# A camera above and behind the audience, looking at the stage, with a mild barrel lens a little off centre.
CAMERA = (0.0, -10.0, 7.0, -28.0, 0.0, 0.0, 62.0)
LENS = (-0.145, -0.033, 0.02, -0.01)
GRID = (12.0, 8.0, 1.0, 0.0, 0.5, 0.0)

ZONES = [
    ("Zone 1", [(-4.5, 3.8), (-4.2, 0.7), (0.2, 1.9), (0.1, 4.0), (-4.0, 4.2)], "#EE5A24"),
    ("Zone 2", [(1.0, -1.5), (4.5, -1.5), (4.5, 0.2), (1.0, 0.2)], "#22CC55"),
    ("Zone 3", [(1.0, 0.5), (3.5, 0.5), (3.5, 2.0), (1.0, 2.0)], "#1E90FF"),
    ("Zone 4", [(-1.5, -1.0), (1.5, -1.0), (1.0, 0.2), (-1.0, 0.2)], "#F1C40F"),
    ("Zone 5", [(2.0, 2.5), (4.0, 3.0), (3.5, 4.2), (2.2, 4.0)], "#9B59B6"),
    ("Zone 6", [(-4.0, -0.8), (-2.0, -0.8), (-2.2, 0.4), (-3.8, 0.5), (-4.2, 0.0)], "#00CED1"),
]
PATHS = [
    ([(-4.0, -0.5), (-1.5, 0.0), (1.5, 0.0), (4.0, -0.5)], False),
    ([(-1.5, 1.5), (1.5, 1.5), (2.0, 3.0), (-2.0, 3.0)], True),
    ([(-3.5, 4.0), (-1.0, 2.5), (1.0, 1.0), (3.5, -0.5), (4.0, -1.0)], False),
]
COLORS = ["#22CC55", "#EA2027", "#833471", "#1E90FF", "#FFA500", "#9B59B6", "#00CED1", "#F1C40F"]


# Scene fields the imported checkout's dataclasses lack, as ``Class.field``.
DROPPED: set[str] = set()


def _make(cls: type, **values: Any) -> Any:
    """``cls(**values)`` with only the fields ``cls`` has; the others go to ``DROPPED``."""
    names = {f.name for f in dataclasses.fields(cls)}
    DROPPED.update(f"{cls.__name__}.{k}" for k in values if k not in names)
    return cls(**{k: v for k, v in values.items() if k in names})


def overlay_state_without(*missing: str) -> type:
    """An ``OverlayState`` lacking ``missing``, as an older checkout's; every field defaults to ``None``."""
    kept = [f.name for f in dataclasses.fields(OverlayState) if f.name not in missing]
    return dataclasses.make_dataclass("OverlayState", [(name, Any, dataclasses.field(default=None)) for name in kept])


def _marker_path(index: int, t: float) -> tuple[float, float]:
    """Where marker ``index`` stands at ``t`` (0..1 over the scene); the first two move."""
    a = 2.0 * math.pi * t
    if index == 0:
        return 1.5 * math.cos(a), 1.0 + 1.5 * math.sin(a)
    if index == 1:
        return 2.0 * math.sin(a), 1.0 + 1.0 * math.sin(2.0 * a)
    return -3.0 + 1.2 * ((index - 2) % 4), -0.5 + 1.5 * ((index - 2) // 4)


def _markers(count: int, controlled: int, t: float) -> list[MarkerOverlayData]:
    markers = []
    for i in range(count):
        x, y = _marker_path(i, t)
        is_controlled = i < controlled
        markers.append(
            _make(
                MarkerOverlayData,
                marker_id=i + 1,
                x=x,
                y=y,
                z=1.6,
                color=COLORS[i % len(COLORS)],
                speed=2.0 if is_controlled else 0.0,
                name=f"Marker {i + 1}",
                is_controlled=is_controlled,
                controller_idx=i if is_controlled else None,
                controller_connected=is_controlled,
                marker_fader=0.0 if is_controlled else None,
            )
        )
    return markers


def _polyline(points: list[tuple[float, float]], closed: bool, steps: int = 12) -> list[tuple[float, float, float]]:
    """The path's corners joined by straight runs of ``steps`` points each."""
    corners = points + points[:1] if closed else points
    out: list[tuple[float, float, float]] = []
    for (x1, y1), (x2, y2) in zip(corners, corners[1:], strict=False):
        out += [(x1 + (x2 - x1) * k / steps, y1 + (y2 - y1) * k / steps, 0.0) for k in range(steps)]
    out.append((*corners[-1], 0.0))
    return out


def _path_lines() -> list[Any]:
    lines = []
    for points, closed in PATHS:
        curve = _polyline(points, closed)
        ends = [] if closed else [(curve[0], curve[1], False), (curve[-1], curve[-2], False)]
        lines.append((curve, ends, False))
    return lines


def _base(t: float, *, markers: int, controlled: int, zones: int, **extra: Any) -> OverlayState:
    marker_list = _markers(markers, controlled, t)
    polygons = []
    for name, verts, color in ZONES[:zones]:
        count = sum(point_in_polygon(m.x, m.y, verts) for m in marker_list)
        polygons.append((verts, color, name, count > 0, count))
    values: dict[str, Any] = {
        "markers": marker_list,
        "selected_id": 1,
        "camera_params": np.array(CAMERA, dtype=np.float64),
        "lens_k1": LENS[0],
        "lens_k2": LENS[1],
        "lens_cx": LENS[2],
        "lens_cy": LENS[3],
        "grid_config": GRID,
        "grid_visible": False,
        "show_ground_circle": True,
        "ground_circle_filled": False,
        "z_display_from_stage": True,
        "transparency": 0.3,
        "mouse_enabled": True,
        "show_hud_help": True,
        "ip_text": "192.0.2.10 (eth0)",
        "hostname_text": "openfollow-bench.local",
        "video_source_type": "rtsp",
        "source_label": "rtsp://192.0.2.20:554/stream",
        "station_name": "OpenFollow bench",
        "cpu_percent": 42.0,
        "ram_percent": 11.0,
        "temperature": 60.0,
        "show_zones": zones > 0,
        "zone_polygons": polygons,
    }
    values.update(extra)
    return _make(OverlayState, **values)


def light(t: float) -> OverlayState:
    return _base(t, markers=2, controlled=2, zones=2)


def full(t: float) -> OverlayState:
    return _base(
        t,
        markers=3,
        controlled=2,
        zones=6,
        grid_visible=True,
        show_paths=True,
        path_lines=_path_lines(),
    )


def eight_markers(t: float) -> OverlayState:
    return _base(t, markers=8, controlled=2, zones=2)


def cones(t: float) -> OverlayState:
    return _base(t, markers=3, controlled=2, zones=2, marker_style="cone")


def menu(t: float) -> OverlayState:
    items = ["Video Source", "Edit Video URL", "Network", "Export Diagnostics", "About", "Restart OpenFollow"]
    return _base(
        t,
        markers=2,
        controlled=2,
        zones=2,
        settings_menu_active=True,
        settings_items=items,
        settings_items_enabled=[True] * len(items),
        settings_items_disabled_reasons=[""] * len(items),
        settings_items_submenu=[True, False, True, True, True, False],
    )


SCENES: dict[str, Callable[[float], OverlayState]] = {
    "light": light,
    "full": full,
    "eight_markers": eight_markers,
    "cones": cones,
    "menu": menu,
}


def frames(name: str, count: int = FRAMES) -> list[tuple[int, int, OverlayState]]:
    """Scene ``name`` as ``count`` frames over one pass of the marker curves."""
    build = SCENES[name]
    return [(WIDTH, HEIGHT, build(i / count)) for i in range(count)]
