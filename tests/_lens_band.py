# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""What the wizard's canvas does for the Lens step: cut the luma band along a traced chord."""

from __future__ import annotations

import base64

import numpy as np

from openfollow.scene.edge_snap import LumaBand, band_cell_positions, band_columns, band_half_size, band_step


def sample_bilinear(img: np.ndarray, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    """Luma at fractional positions; zero outside the image, as a canvas paints nothing there."""
    h, w = img.shape
    inside = (xs >= 0.0) & (xs <= w - 1) & (ys >= 0.0) & (ys <= h - 1)
    x = np.where(inside, xs, 0.0)
    y = np.where(inside, ys, 0.0)
    x0 = np.minimum(np.floor(x).astype(int), w - 2)
    y0 = np.minimum(np.floor(y).astype(int), h - 2)
    fx = x - x0
    fy = y - y0
    out = (
        img[y0, x0] * (1 - fx) * (1 - fy)
        + img[y0, x0 + 1] * fx * (1 - fy)
        + img[y0 + 1, x0] * (1 - fx) * fy
        + img[y0 + 1, x0 + 1] * fx * fy
    )
    return np.where(inside, out, 0.0)


def cut_band(img: np.ndarray, p0, p1, *, step: int | None = None, half: int | None = None) -> LumaBand:
    """The band for the chord ``p0`` to ``p1``, with the wizard's own step and half unless given."""
    w = img.shape[1]
    step = band_step(w) if step is None else step
    half = band_half_size(w) if half is None else half
    length = float(np.hypot(p1[0] - p0[0], p1[1] - p0[1]))
    cols = band_columns(length, step)
    xs, ys = band_cell_positions(p0, p1, step, half, cols)
    luma = np.clip(np.round(sample_bilinear(img, xs, ys)), 0, 255)
    return LumaBand(step, half, luma.astype(np.float64))


def band_payload(band: LumaBand) -> dict:
    """The ``band`` object of a ``/api/wizard/lens/snap`` body."""
    rows, cols = band.luma.shape
    return {
        "step": band.step,
        "half": band.half,
        "cols": cols,
        "rows": rows,
        "data": base64.b64encode(band.luma.astype(np.uint8).tobytes()).decode(),
    }
