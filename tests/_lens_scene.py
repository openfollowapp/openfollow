# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""A synthetic stage seen through a known lens, and the Lens step's whole pipeline run on it.

The scene is drawn in the pinhole frame, where its lines are straight, and every
pixel of the picture is rendered from the pinhole position the lens maps it to,
so a picture through ``(k1, k2)`` is exact: no resampling, and a straight line
bows precisely the way the overlay functions bow it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from openfollow.scene.edge_chains import candidate_sample_points, edge_map_scale, find_edge_candidates
from openfollow.scene.edge_snap import LINE_SAMPLE_FRACTIONS, snap_line_to_edges
from openfollow.scene.lens_fit import LensFitResult, fit_lens_from_lines
from openfollow.scene.solver import invert_overlay_distortion
from tests._lens_band import cut_band, scaled_luma

W, H = 1920, 1080
FLOOR = 40.0


@dataclass(frozen=True)
class Strip:
    """A straight strip between two pinhole points: half its width and its brightness."""

    p0: tuple[float, float]
    p1: tuple[float, float]
    half_width: float
    bright: float


@dataclass(frozen=True)
class Disc:
    """A round patch: sharp-edged when ``soft`` is 0, else a Gaussian falloff over that many pixels."""

    centre: tuple[float, float]
    radius: float
    bright: float
    soft: float = 0.0


@dataclass(frozen=True)
class Scene:
    strips: list[Strip] = field(default_factory=list)
    discs: list[Disc] = field(default_factory=list)


def stage_scene(*, grid: bool = True, distractors: bool = True) -> Scene:
    """A stage seen from the back of a hall: a trapezoid of edges, a tape grid, spotlights."""
    front = ((100.0, 960.0), (1820.0, 960.0))
    back = ((420.0, 180.0), (1500.0, 180.0))
    strips = [
        Strip(front[0], front[1], 6.0, 210.0),
        Strip(back[0], back[1], 4.0, 150.0),
        Strip(front[0], back[0], 5.0, 120.0),
        Strip(front[1], back[1], 5.0, 120.0),
    ]
    if grid:
        for y in (380.0, 580.0, 780.0):
            t = (y - 180.0) / (960.0 - 180.0)
            x0 = 420.0 + t * (100.0 - 420.0)
            x1 = 1500.0 + t * (1820.0 - 1500.0)
            strips.append(Strip((x0, y), (x1, y), 2.5, 110.0))
        for f in (0.25, 0.5, 0.75):
            strips.append(Strip((100.0 + f * 1720.0, 960.0), (420.0 + f * 1080.0, 180.0), 2.5, 110.0))
    discs = []
    if distractors:
        discs = [
            Disc((700.0, 450.0), 120.0, 95.0, soft=40.0),
            Disc((1250.0, 620.0), 140.0, 90.0, soft=50.0),
            Disc((1000.0, 300.0), 100.0, 85.0, soft=30.0),
            Disc((1420.0, 860.0), 70.0, 100.0),
            Disc((560.0, 840.0), 45.0, 70.0),
        ]
    return Scene(strips, discs)


def deck_scene() -> Scene:
    """A 720p stage of decks: its edges and the one-pixel seams between the decks, under a soft wash."""
    front = ((60.0, 660.0), (1220.0, 660.0))
    back = ((300.0, 150.0), (980.0, 150.0))
    strips = [
        Strip(front[0], front[1], 3.0, 200.0),
        Strip(back[0], back[1], 2.0, 170.0),
        Strip(front[0], back[0], 2.0, 160.0),
        Strip(front[1], back[1], 2.0, 160.0),
    ]
    seam = FLOOR + 40.0
    for y in (280.0, 410.0, 540.0):
        t = (y - 150.0) / (660.0 - 150.0)
        strips.append(Strip((300.0 + t * (60.0 - 300.0), y), (980.0 + t * (1220.0 - 980.0), y), 0.5, seam))
    for f in np.linspace(0.1, 0.9, 9):
        strips.append(Strip((60.0 + f * 1160.0, 660.0), (300.0 + f * 680.0, 150.0), 0.5, seam))
    return Scene(strips, [Disc((640.0, 400.0), 300.0, FLOOR + 30.0, soft=1.0)])


def render(
    scene: Scene,
    lens: tuple[float, float],
    *,
    size: tuple[int, int] = (W, H),
    floor: float = FLOOR,
    noise: float = 3.0,
    seed: int = 0,
) -> np.ndarray:
    """The scene as seen through ``lens``: luma ``(height, width)``, the strips and discs on a floor."""
    k1, k2 = lens
    width, height = size
    yy, xx = np.mgrid[0:height, 0:width]
    pixels = np.column_stack([xx.ravel(), yy.ravel()]).astype(np.float64)
    u = invert_overlay_distortion(pixels, width, height, k1, k2)
    img = np.full(u.shape[0], floor)
    for disc in scene.discs:
        d = np.hypot(u[:, 0] - disc.centre[0], u[:, 1] - disc.centre[1])
        if disc.soft > 0.0:
            weight = np.exp(-0.5 * (d / disc.radius) ** 2)
        else:
            weight = np.clip(disc.radius - d + 0.5, 0.0, 1.0)
        img = img + weight * (disc.bright - FLOOR)
    # A strip stands its brightness above whatever lies under it, a spotlight included.
    for strip in scene.strips:
        a = np.asarray(strip.p0)
        d = np.asarray(strip.p1) - a
        t = np.clip(((u - a) @ d) / float(d @ d), 0.0, 1.0)
        distance = np.linalg.norm(u - a - t[:, None] * d[None, :], axis=1)
        weight = np.clip(strip.half_width - distance + 0.5, 0.0, 1.0)
        img = img + weight * (strip.bright - FLOOR)
    rng = np.random.default_rng(seed)
    return np.clip(img.reshape(height, width) + rng.normal(0.0, noise, (height, width)), 0.0, 255.0)


@dataclass(frozen=True)
class Calibration:
    """What the Lens step would hold after tapping every suggestion."""

    lines: list[list[list[float]]]
    unsnapped: int
    fit: LensFitResult


def calibrate(img: np.ndarray) -> Calibration:
    """Run the step as the wizard does: suggest, tap each suggestion, snap it, fit the pair."""
    h, w = img.shape
    scale = edge_map_scale(w)
    found = find_edge_candidates(scaled_luma(img, scale), scale, w, h)
    lines = []
    unsnapped = 0
    for candidate in found.candidates:
        samples = candidate_sample_points(candidate.points, LINE_SAMPLE_FRACTIONS)
        points = snap_line_to_edges(samples[0], samples[4], cut_band(img, samples[0], samples[4]), w, h)
        # The wizard starts a middle point that found no edge switched off; the ends always count.
        active = [[p.x, p.y] for j, p in enumerate(points) if j in (0, 4) or p.snapped]
        unsnapped += sum(1 for p in points if not p.snapped)
        if len(active) >= 3:
            lines.append(active)
    return Calibration(lines, unsnapped, fit_lens_from_lines(lines, w, h))
