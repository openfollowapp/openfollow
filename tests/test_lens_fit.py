# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Paul Hermann and Michel Honold
"""The plumb-line fit: k1 / k2 back from lines warped with the real overlay functions."""

from __future__ import annotations

import math

import numpy as np
import pytest

from openfollow.lens_model import lens_limit_radius, lens_warp_is_valid
from openfollow.scene.lens_fit import (
    K1_PRIOR,
    K2_PRIOR,
    MAX_LINES,
    MAX_POINTS_PER_LINE,
    RATING_LOW,
    RATING_THRESHOLDS_PX,
    RATINGS,
    LensFitResult,
    LensLineFit,
    _f_upper,
    coverage_hint,
    fit_lens_from_lines,
    fit_result_to_dict,
    rate_uncertainty,
)
from openfollow.scene.solver import apply_overlay_distortion, invert_overlay_distortion

pytestmark = pytest.mark.unit

W, H = 1920.0, 1080.0
HALF_DIAG = 0.5 * float(np.hypot(W, H))
_M = 12.0
# Lines where an operator clicks them: on the distorted image, along its edges.
TOP = ((_M + 40, _M), (W - _M - 40, _M + 8))
BOTTOM = ((_M + 40, H - _M), (W - _M - 40, H - _M - 6))
LEFT = ((_M, _M + 40), (_M + 6, H - _M - 40))
RIGHT = ((W - _M, _M + 40), (W - _M - 8, H - _M - 40))
DIAGONAL = ((120.0, 100.0), (1800.0, 1000.0))
MID_H = ((300.0, 540.0), (1600.0, 520.0))
MID_V = ((960.0, 100.0), (980.0, 980.0))
EDGES = (TOP, BOTTOM, LEFT, RIGHT)


def make_line(
    p0: tuple[float, float],
    p1: tuple[float, float],
    k1: float,
    k2: float,
    *,
    noise: float = 0.0,
    rng: np.random.Generator | None = None,
    n: int = 5,
    sag: float = 0.0,
    cx: float = 0.0,
    cy: float = 0.0,
) -> list[list[float]]:
    """Points of a line that is straight in reality, as seen through the lens."""
    ends = invert_overlay_distortion(np.array([p0, p1], dtype=np.float64), W, H, k1, k2, cx, cy)
    t = np.linspace(0.0, 1.0, n)[:, None]
    straight = ends[0] + t * (ends[1] - ends[0])
    if sag:
        tangent = ends[1] - ends[0]
        normal = np.array([-tangent[1], tangent[0]]) / np.linalg.norm(tangent)
        straight = straight + (sag * 4.0 * t * (1.0 - t)) * normal
    seen = apply_overlay_distortion(straight, W, H, k1, k2, cx, cy)
    if noise and rng is not None:
        seen = seen + rng.normal(0.0, noise, seen.shape)
    return [[float(x), float(y)] for x, y in seen]


def bowed(line: list[list[float]], sag: float) -> list[list[float]]:
    """Traced points bowed off their chord on screen, ``sag`` px at the middle."""
    p = np.asarray(line)
    chord = p[-1] - p[0]
    normal = np.array([-chord[1], chord[0]]) / np.hypot(*chord)
    return [[float(x), float(y)] for x, y in p + sag * np.sin(np.pi * np.linspace(0.0, 1.0, len(p)))[:, None] * normal]


def lines_for(k1: float, k2: float, segments, *, noise: float = 0.0, seed: int = 0, **kw) -> list[list[list[float]]]:
    rng = np.random.default_rng(seed)
    return [make_line(a, b, k1, k2, noise=noise, rng=rng, **kw) for a, b in segments]


# Screen points over the whole frame, where a fitted lens is compared with the truth.
_FRAME = np.column_stack([g.ravel() for g in np.meshgrid(np.linspace(0.0, W, 17), np.linspace(0.0, H, 10))])


def frame_error_px(fitted: tuple[float, ...], truth: tuple[float, ...]) -> float:
    """Largest screen error over the frame once a homography of the pinhole picture (the camera's) is absorbed."""
    q_true = invert_overlay_distortion(_FRAME, W, H, *truth)
    q_fit = invert_overlay_distortion(_FRAME, W, H, *fitted)
    rows = []
    for (x, y), (u, v) in zip(q_fit / HALF_DIAG, q_true / HALF_DIAG, strict=True):
        rows += [[x, y, 1, 0, 0, 0, -u * x, -u * y, -u], [0, 0, 0, x, y, 1, -v * x, -v * y, -v]]
    hom = np.linalg.svd(np.asarray(rows))[2][-1].reshape(3, 3)
    mapped = np.column_stack([q_fit / HALF_DIAG, np.ones(len(q_fit))]) @ hom.T
    seen = apply_overlay_distortion(mapped[:, :2] / mapped[:, 2:] * HALF_DIAG, W, H, *truth)
    return float(np.max(np.hypot(*(seen - _FRAME).T)))


def inner_error_px(fitted: tuple[float, ...], truth: tuple[float, ...]) -> float:
    """:func:`frame_error_px` within 0.8 of the half-diagonal, short of where a strong lens's pole runs off."""
    inner = np.hypot(_FRAME[:, 0] - W / 2, _FRAME[:, 1] - H / 2) / HALF_DIAG <= 0.8
    q_true = invert_overlay_distortion(_FRAME[inner], W, H, *truth)
    q_fit = invert_overlay_distortion(_FRAME[inner], W, H, *fitted)
    rows = []
    for (x, y), (u, v) in zip(q_fit / HALF_DIAG, q_true / HALF_DIAG, strict=True):
        rows += [[x, y, 1, 0, 0, 0, -u * x, -u * y, -u], [0, 0, 0, x, y, 1, -v * x, -v * y, -v]]
    hom = np.linalg.svd(np.asarray(rows))[2][-1].reshape(3, 3)
    mapped = np.column_stack([q_fit / HALF_DIAG, np.ones(len(q_fit))]) @ hom.T
    seen = apply_overlay_distortion(mapped[:, :2] / mapped[:, 2:] * HALF_DIAG, W, H, *truth)
    return float(np.max(np.hypot(*(seen - _FRAME[inner]).T)))


def corner_error_px(fitted: tuple[float, ...], truth: tuple[float, ...]) -> float:
    """How far apart the two lenses put the pinhole frame's corners, less the shift of the pinhole centre."""
    corners = np.array([[0.0, 0.0], [W, 0.0], [0.0, H], [W, H]])
    seen = [
        apply_overlay_distortion(corners, W, H, *lens) - [W / 2 + lens[2] * HALF_DIAG, H / 2 + lens[3] * HALF_DIAG]
        for lens in (fitted, truth)
    ]
    return float(np.max(np.hypot(*(seen[0] - seen[1]).T)))


def _lens(result: LensFitResult) -> tuple[float, float, float, float]:
    return (result.k1, result.k2, result.cx, result.cy)


def _distance_to_polyline(point: np.ndarray, polyline: np.ndarray) -> float:
    a, b = polyline[:-1], polyline[1:]
    ab = b - a
    t = np.clip(np.einsum("ij,ij->i", point - a, ab) / np.einsum("ij,ij->i", ab, ab), 0.0, 1.0)
    nearest = a + t[:, None] * ab
    return float(np.min(np.hypot(*(nearest - point).T)))


class TestRecovery:
    @pytest.mark.parametrize(
        "truth", [(0.0, 0.0), (-0.15, 0.0), (-0.3, 0.05), (0.2, 0.0), (-0.527, -0.069), (-0.3, -0.3)]
    )
    def test_exact_lines_return_the_pair(self, truth: tuple[float, float]) -> None:
        # The gentle prior toward no lens may slide the pair along the valley it lies in, never the frame.
        result = fit_lens_from_lines(lines_for(*truth, EDGES + (DIAGONAL,)), W, H)
        assert result.k1 == pytest.approx(truth[0], abs=0.015)
        assert result.k2 == pytest.approx(truth[1], abs=0.015)
        assert frame_error_px(_lens(result), (*truth, 0.0, 0.0)) < 1.0
        assert (result.cx, result.cy) == (0.0, 0.0)
        assert result.rms_px < 0.05

    @pytest.mark.parametrize("truth", [(-0.45, 0.20), (-0.47, 0.25)])
    def test_strong_barrel_distortion_is_recovered(self, truth: tuple[float, float]) -> None:
        # Plain Gauss-Newton from zero diverges here; the grid seed does not.
        result = fit_lens_from_lines(lines_for(*truth, EDGES + (DIAGONAL,)), W, H)
        assert result.k2_fitted
        assert result.k1 == pytest.approx(truth[0], abs=5e-3)
        assert result.k2 == pytest.approx(truth[1], abs=5e-3)

    @pytest.mark.parametrize("seed", range(3))
    def test_one_pixel_noise_keeps_the_pair_close(self, seed: int) -> None:
        truth = (-0.3, 0.05)
        result = fit_lens_from_lines(lines_for(*truth, EDGES + (DIAGONAL,), noise=1.0, seed=seed), W, H)
        assert result.k1 == pytest.approx(truth[0], abs=0.03)
        assert result.k2 == pytest.approx(truth[1], abs=0.03)

    def test_three_pixel_noise_keeps_the_corners_within_their_rating(self) -> None:
        # At three pixels k1 and k2 trade along their valley; where the corners land is what holds.
        truth = (-0.45, 0.20, 0.0, 0.0)
        ratios = []
        for seed in range(8):
            result = fit_lens_from_lines(lines_for(*truth[:2], EDGES + (DIAGONAL,), noise=3.0, seed=seed, n=8), W, H)
            ratios.append(corner_error_px(_lens(result), truth) / result.uncertainty_px)
        assert max(ratios) < 3.0
        assert float(np.mean(ratios)) < 1.5

    def test_a_line_with_a_switched_off_point_still_counts(self) -> None:
        truth = (-0.2, 0.02)
        lines = lines_for(*truth, EDGES)
        lines[0] = [lines[0][0], lines[0][2], lines[0][4]]  # start, one middle point, end
        result = fit_lens_from_lines(lines, W, H)
        assert result.k1 == pytest.approx(truth[0], abs=5e-3)
        assert len(result.lines) == 4

    def test_result_never_folds_and_stays_bounded(self) -> None:
        # One short line in the middle says almost nothing; the answer must still be a usable pair.
        rng = np.random.default_rng(7)
        line = make_line(*MID_H, -0.2, 0.0, noise=3.0, rng=rng)
        result = fit_lens_from_lines([line], W, H)
        assert abs(result.k1) <= 1.0 and abs(result.k2) <= 1.0
        assert np.isfinite(result.k1) and np.isfinite(result.k2)


class TestK2:
    def test_without_corner_coverage_k2_is_the_prior_s_and_rated_so(self) -> None:
        truth = (-0.3, 0.05)
        result = fit_lens_from_lines(lines_for(*truth, (MID_H, MID_V), noise=1.0, seed=1), W, H)
        assert not result.k2_fitted
        assert abs(result.k2) < 0.1
        assert result.rating == RATING_LOW
        assert "corner" in result.hint

    @pytest.mark.parametrize("seed", range(3))
    def test_a_loose_k2_is_kept_as_drawn_not_traded_into_k1(self, seed: int) -> None:
        # The stage lip and the deck's two sides under a 150 degree lens say less about k2 than its
        # prior does; dropped to 0, the corners' bend went into k1.
        truth = (-0.71, -0.17, 0.0, 0.0)
        segments = (
            ((470.0, 909.0), (1420.0, 904.0)),
            ((432.0, 666.0), (382.0, 846.0)),
            ((1446.0, 628.0), (1510.0, 844.0)),
        )
        result = fit_lens_from_lines(lines_for(*truth[:2], segments, noise=1.0, seed=seed), W, H)
        assert not result.k2_fitted
        assert result.k2 < -0.05
        assert inner_error_px(_lens(result), truth) < 12.0

    def test_k2_is_fitted_once_lines_reach_the_corners(self) -> None:
        truth = (-0.3, 0.05)
        result = fit_lens_from_lines(lines_for(*truth, EDGES, noise=1.0, seed=1), W, H)
        assert result.k2_fitted
        assert result.k2 == pytest.approx(truth[1], abs=0.02)

    @pytest.mark.parametrize("seed", range(4))
    def test_a_strong_lens_keeps_k2_though_its_corner_moves_less_per_unit(self, seed: int) -> None:
        # Through a 140 degree lens a k2 change moves the corner half as far, so its sigma runs to about 0.06;
        # the gate scales with the lens's slope at the corner and keeps it.
        truth = (-0.647, -0.111, 0.0, 0.0)
        result = fit_lens_from_lines(lines_for(*truth[:2], EDGES + (DIAGONAL,), noise=1.0, seed=seed), W, H)
        assert result.k2_fitted
        assert frame_error_px(_lens(result), truth) < 16.0

    def test_lines_that_say_more_than_the_prior_keep_a_loose_k2(self) -> None:
        # Short lines in the outer bands leave k2 loose, but a lens without it bends them visibly wrong.
        truth = (-0.47, 0.25, 0.0, 0.0)
        result = fit_lens_from_lines(lines_for(*truth[:2], tuple(_BAND.values()), noise=0.3, seed=1), W, H)
        assert result.k2_fitted
        assert result.k2 == pytest.approx(truth[1], abs=0.02)
        assert corner_error_px(_lens(result), truth) < 2.0
        assert result.rating == "medium"

    def test_the_prior_weighs_against_the_points_own_scatter(self) -> None:
        # Placed to a pixel, these lines leave a loose k2 that the prior draws toward 0; placed to a
        # tenth of one, the same lines keep it.
        truth = (-0.47, 0.25)
        rough = fit_lens_from_lines(lines_for(*truth, tuple(_BAND.values()), noise=1.0, seed=1), W, H)
        fine = fit_lens_from_lines(lines_for(*truth, tuple(_BAND.values()), noise=0.1, seed=1), W, H)
        assert fine.k2 == pytest.approx(truth[1], abs=0.01)
        assert rough.k2 < fine.k2 - 0.03

    def test_lines_near_the_corners_measure_k2_rather_than_trading_it_into_k1(self) -> None:
        # Risers in the four corners: k1 and k2 trade along a ridge there. Dropping k2 put all of
        # the corners' bend into k1 (about -0.48 for this lens), and bent the middle wrong.
        truth = (-0.21, -0.21, 0.0, 0.0)
        risers = (
            ((180.0, 1030.0), (240.0, 810.0)),
            ((250.0, 925.0), (440.0, 965.0)),
            ((1745.0, 820.0), (1790.0, 1040.0)),
            ((1545.0, 965.0), (1750.0, 928.0)),
            ((500.0, 1018.0), (770.0, 1050.0)),
        )
        result = fit_lens_from_lines(lines_for(*truth[:2], risers, noise=0.5, seed=3), W, H)
        assert result.k2_fitted
        assert result.k1 > -0.33
        assert frame_error_px(_lens(result), truth) < 25.0


# The strongest common lens (126 degrees on 1920 x 1080), its centre 80 px right of and 50 px below the picture's.
DECENTRED = (-0.53, -0.07, 80.0 / HALF_DIAG, 50.0 / HALF_DIAG)


def decentred_trials(lens: tuple[float, float, float, float], seed: int, given: tuple[float, float]):
    """Twelve fits of the four edges and a diagonal through ``lens`` at one pixel of placement noise,
    about the centre ``given``."""
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(12):
        segments = EDGES + (DIAGONAL,)
        lines = [make_line(a, b, lens[0], lens[1], cx=lens[2], cy=lens[3], noise=1.0, rng=rng) for a, b in segments]
        out.append(fit_lens_from_lines(lines, W, H, cx=given[0], cy=given[1]))
    return out


class TestGivenCentre:
    def test_exact_lines_return_the_pair_about_the_given_centre(self) -> None:
        truth = (-0.3, -0.05, 0.06, -0.04)
        lines = [make_line(a, b, -0.3, -0.05, cx=0.06, cy=-0.04) for a, b in EDGES + (DIAGONAL,)]
        result = fit_lens_from_lines(lines, W, H, cx=0.06, cy=-0.04)
        assert _lens(result) == pytest.approx(truth, abs=2e-3)
        assert result.rms_px < 0.05

    def test_a_strong_lens_off_centre_is_followed_with_its_centre_given(self) -> None:
        trials = decentred_trials(DECENTRED, seed=1, given=DECENTRED[2:])
        assert max(frame_error_px(_lens(r), DECENTRED) for r in trials) <= 8.0
        assert all(r.rating in ("good", "excellent") for r in trials)

    def test_a_missing_centre_shows_in_the_rating(self) -> None:
        # About the picture's centre this lens is off by some 45 px; no trial may claim better than poor.
        trials = decentred_trials(DECENTRED, seed=1, given=(0.0, 0.0))
        errors = [frame_error_px(_lens(r), DECENTRED) for r in trials]
        assert min(errors) > 40.0
        assert all(r.rating in ("low", "medium") for r in trials)
        assert all(error < 3.0 * r.uncertainty_px for error, r in zip(errors, trials, strict=True))

    @pytest.mark.parametrize("centre", [(0.0, 0.0), (0.1, -0.05), (-0.25, 0.25)])
    def test_the_fit_never_moves_the_given_centre(self, centre: tuple[float, float]) -> None:
        # Lines whose own bows say nothing true about a centre: real edges bend a little.
        rng = np.random.default_rng(4)
        lines = [
            make_line(a, b, -0.2, -0.03, noise=1.0, rng=rng, sag=float(rng.uniform(-3.0, 3.0)))
            for a, b in EDGES + (DIAGONAL, MID_H, MID_V)
        ]
        result = fit_lens_from_lines(lines, W, H, cx=centre[0], cy=centre[1])
        assert (result.cx, result.cy) == centre
        assert all(np.isfinite(_lens(result)))

    @pytest.mark.parametrize(
        "centre",
        [(0.26, 0.0), (0.0, -0.3), (float("nan"), 0.0), (0.0, float("inf"))],
        ids=["x-past-the-bound", "y-past-the-bound", "nan", "infinite"],
    )
    def test_a_centre_beyond_its_bound_is_an_error(self, centre: tuple[float, float]) -> None:
        with pytest.raises(ValueError, match="lens centre"):
            fit_lens_from_lines(lines_for(-0.2, 0.0, EDGES), W, H, cx=centre[0], cy=centre[1])

    def test_a_lens_whose_far_corner_looks_past_90_degrees_fits_the_nearest_valid_one(self) -> None:
        # A 150 degree fisheye 94 px off centre: its far corner has no pinhole point, which no valid lens can show.
        truth = (-0.74, -0.152, 80.0 / HALF_DIAG, 50.0 / HALF_DIAG)
        assert not lens_warp_is_valid(*truth)
        rng = np.random.default_rng(0)
        lines = [
            make_line(a, b, truth[0], truth[1], cx=truth[2], cy=truth[3], noise=1.0, rng=rng)
            for a, b in (TOP, BOTTOM, RIGHT, DIAGONAL)
        ]
        result = fit_lens_from_lines(lines, W, H, cx=truth[2], cy=truth[3])
        assert lens_warp_is_valid(*_lens(result))
        assert (result.cx, result.cy) == truth[2:]

    def test_a_lens_on_the_edge_of_the_valid_ones_is_fitted_there(self) -> None:
        # Left out in turn, a line leaves a refit that creeps on past the refinement's iteration budget.
        truth = (-1.0, 0.0)
        assert lens_limit_radius(*truth) == pytest.approx(1.0)
        result = fit_lens_from_lines(lines_for(*truth, (LEFT, DIAGONAL, MID_V), noise=1.0, seed=1), W, H)
        assert lens_warp_is_valid(*_lens(result))
        assert lens_limit_radius(result.k1, result.k2) == pytest.approx(1.0, abs=1e-4)
        assert not any(line.misfit for line in result.lines)


class TestScreenPixels:
    def test_a_line_s_scatter_is_measured_in_screen_pixels(self) -> None:
        # Near the top edge of a 126 degree lens a screen pixel spans several pinhole pixels across the line.
        # Its points moved across it by (-1, 2, 0, -2, 1) px, a pattern no line or bow takes up, scatter by
        # sqrt(2) px on screen.
        truth = (-0.527, -0.069)
        lines = lines_for(*truth, EDGES + (DIAGONAL,))
        top = np.asarray(make_line(*TOP, *truth, n=401))
        for j, shift in enumerate((-1.0, 2.0, 0.0, -2.0, 1.0)):
            i = min(max(100 * j, 1), 399)
            tangent = top[i + 1] - top[i - 1]
            lines[0][j] = list(top[100 * j] + shift * np.array([-tangent[1], tangent[0]]) / np.hypot(*tangent))
        result = fit_lens_from_lines(lines, W, H)
        assert result.lines[0].rms_px == pytest.approx(math.sqrt(2.0), abs=0.15)

    def test_a_pincushion_lens_is_rated_where_the_picture_ends(self) -> None:
        # Its pinhole corners land outside the picture, where no line reaches.
        truth = (0.2, 0.0, 0.0, 0.0)
        for seed in range(3):
            result = fit_lens_from_lines(lines_for(*truth[:2], EDGES + (DIAGONAL,), noise=1.0, seed=seed), W, H)
            assert result.rating in ("okay", "good")
            assert corner_error_px(_lens(result), truth) < result.uncertainty_px


class TestRating:
    @pytest.mark.parametrize(
        "uncertainty,rating",
        [
            (0.0, "excellent"),
            (RATING_THRESHOLDS_PX[0][0] - 1e-9, "excellent"),
            (RATING_THRESHOLDS_PX[0][0], "good"),
            (RATING_THRESHOLDS_PX[1][0], "okay"),
            (RATING_THRESHOLDS_PX[2][0], "medium"),
            (RATING_THRESHOLDS_PX[3][0], RATING_LOW),
            (1e6, RATING_LOW),
            (float("inf"), RATING_LOW),
            (float("nan"), RATING_LOW),
        ],
    )
    def test_thresholds(self, uncertainty: float, rating: str) -> None:
        assert rate_uncertainty(uncertainty) == rating

    def test_thresholds_are_the_simulated_values(self) -> None:
        # Fixed from the simulation in the change that added the step: four
        # exact edge lines at one-pixel precision land just under "excellent".
        assert RATING_THRESHOLDS_PX == ((2.5, "excellent"), (5.0, "good"), (10.0, "okay"), (20.0, "medium"))
        assert RATINGS == ("low", "medium", "okay", "good", "excellent")

    def test_four_edges_rate_good_or_better(self) -> None:
        result = fit_lens_from_lines(lines_for(-0.3, 0.05, EDGES, noise=1.0, seed=2), W, H)
        assert result.rating in ("good", "excellent")
        assert result.uncertainty_px < RATING_THRESHOLDS_PX[1][0]

    def test_two_centre_lines_rate_low(self) -> None:
        result = fit_lens_from_lines(lines_for(-0.3, 0.05, (MID_H, MID_V), noise=1.0, seed=2), W, H)
        assert result.rating == RATING_LOW

    def test_a_vertical_line_never_flatters_the_rating(self) -> None:
        # A near-vertical line's fitted direction may come out either way round; read as
        # flipping between the Jacobian's two samples, it rated these two lines excellent.
        mid = make_line(*MID_H, -0.3, 0.05, noise=0.7, rng=np.random.default_rng(11))
        ratings = set()
        for seed in range(80):
            vertical = np.array(make_line((400.0, 80.0), (400.0, 1000.0), -0.3, 0.05, noise=0.0))
            vertical += np.random.default_rng(5000 + seed).normal(0.0, 0.7, vertical.shape)
            ratings.add(fit_lens_from_lines([mid, vertical.tolist()], W, H).rating)
        assert "excellent" not in ratings

    def test_lines_that_fit_two_lenses_alike_rate_low(self) -> None:
        # Two side edges through a pincushion lens: a second lens about 85 px apart at the corners fits
        # them within the noise. The covariance of either minimum alone would rate it fair.
        result = fit_lens_from_lines(lines_for(0.3, 0.0, (LEFT, RIGHT), noise=3.0, seed=3), W, H)
        assert result.rating == RATING_LOW
        assert result.uncertainty_px > 80.0
        assert result.hint == "If possible, add lines near the top and bottom edges."

    def test_a_minimum_the_lines_rule_out_does_not_widen_the_rating(self) -> None:
        # The seeds reach a second lens some 146 px apart, but its cost is well beyond the margin.
        result = fit_lens_from_lines(lines_for(-0.45, 0.2, (TOP, DIAGONAL), noise=5.0, seed=2), W, H)
        assert result.rating == RATING_LOW
        assert 20.0 < result.uncertainty_px < 100.0

    def test_sloppier_points_lower_the_rating(self) -> None:
        precise = fit_lens_from_lines(lines_for(-0.3, 0.05, EDGES, noise=0.5, seed=3), W, H)
        sloppy = fit_lens_from_lines(lines_for(-0.3, 0.05, EDGES, noise=4.0, seed=3), W, H)
        assert sloppy.uncertainty_px > precise.uncertainty_px
        assert RATINGS.index(sloppy.rating) <= RATINGS.index(precise.rating)

    @pytest.mark.parametrize("seed", range(3))
    def test_the_prior_bounds_the_rating_where_the_lines_say_nothing(self, seed: int) -> None:
        # One line through the middle leaves the pair to its prior: the corners are never
        # rated looser than the prior alone puts them, though the line alone puts them nowhere.
        result = fit_lens_from_lines(lines_for(-0.333, -0.025, (MID_H,), noise=1.0, seed=seed), W, H)
        corners = np.array([[0.0, 0.0], [W, 0.0], [0.0, H], [W, H]])
        eps = 1e-5
        jac = np.stack(
            [
                (
                    apply_overlay_distortion(corners, W, H, result.k1 + d1, result.k2 + d2, 0.0, 0.0)
                    - apply_overlay_distortion(corners, W, H, result.k1 - d1, result.k2 - d2, 0.0, 0.0)
                )
                / (2.0 * eps)
                for d1, d2 in ((eps, 0.0), (0.0, eps))
            ],
            axis=-1,
        )
        prior_alone = np.sqrt(np.einsum("caf,f,caf->c", jac, np.array([K1_PRIOR**2, K2_PRIOR**2]), jac).max())
        assert result.rating == RATING_LOW
        assert result.uncertainty_px <= prior_alone


class TestEdgesMeasured:
    # A 2.3 mm-like lens whose centre is 120 px right of and 80 px below the picture's.
    _OFF_CENTRE = (-0.53, -0.07, 120.0 / HALF_DIAG, 80.0 / HALF_DIAG)

    @pytest.mark.parametrize(
        ("segments", "seed"), [((MID_H,), 0), ((MID_H,), 1), ((MID_H, MID_V), 0), ((MID_H, MID_V), 2)]
    )
    def test_lines_near_the_lens_centre_leave_the_edges_unmeasured(self, segments, seed: int) -> None:  # noqa: ANN001
        # Off the lens centre a middle line bows enough to rate fair, and the rating keeps that.
        k1, k2, cx, cy = self._OFF_CENTRE
        lines = lines_for(k1, k2, segments, noise=1.0, seed=seed, cx=cx, cy=cy)
        result = fit_lens_from_lines(lines, W, H, cx=cx, cy=cy)
        assert result.rating == rate_uncertainty(result.uncertainty_px) == "okay"
        assert result.edges_measured is False
        assert fit_result_to_dict(result)["edges_measured"] is False

    def test_lines_reaching_out_on_one_side_only_leave_the_edges_unmeasured(self) -> None:
        # Three upright lines in the left part of the picture: exact, and fair by their number.
        left = (LEFT, ((250.0, 120.0), (240.0, 960.0)), ((420.0, 200.0), (410.0, 880.0)))
        result = fit_lens_from_lines(lines_for(-0.53, -0.07, left), W, H)
        assert result.rating == "okay"
        assert result.edges_measured is False

    @pytest.mark.parametrize(
        "segments",
        [
            (MID_H,),
            (MID_H, MID_V),
            # Two long lines, each out to 0.7 of the half-diagonal on its own side: still the middle.
            (((190.0, 560.0), (960.0, 560.0)), ((960.0, 600.0), (1730.0, 600.0))),
            # A stage seen from the front truss through a 150-degree class lens: its lines
            # reach 0.59 of the half-diagonal, left and right of the centre.
            (
                ((470.0, 909.0), (1420.0, 904.0)),
                ((560.0, 788.0), (1330.0, 782.0)),
                ((560.0, 660.0), (1330.0, 654.0)),
                ((432.0, 666.0), (382.0, 846.0)),
                ((1446.0, 628.0), (1510.0, 844.0)),
            ),
        ],
        ids=["middle-line", "middle-cross", "two-lines-out-to-0.7", "stage-from-the-front-truss"],
    )
    def test_lines_short_of_the_corners_leave_the_edges_unmeasured(self, segments) -> None:  # noqa: ANN001
        assert fit_lens_from_lines(lines_for(-0.71, -0.17, segments, noise=0.5, seed=1), W, H).edges_measured is False

    @pytest.mark.parametrize(
        "segments",
        [
            (TOP, BOTTOM),
            # One line from corner to corner reaches out on both of its sides.
            (TOP,),
            # Each reaches out on one side only, but the two sides differ.
            (((40.0, 400.0), (45.0, 700.0)), ((W - 40.0, 400.0), (W - 45.0, 700.0))),
        ],
        ids=["top-and-bottom-edges", "top-edge-alone", "left-and-right-bands"],
    )
    def test_points_reaching_out_on_two_sides_measure_the_edges(self, segments) -> None:  # noqa: ANN001
        result = fit_lens_from_lines(lines_for(-0.71, -0.17, segments, noise=0.5, seed=1), W, H)
        assert result.edges_measured is True
        assert result.rating == rate_uncertainty(result.uncertainty_px)

    # Short lines 810-930 px left and right of the picture centre, at its height; and near two
    # opposite corners, 860-940 px out.
    _LEVEL = (((30.0, 540.0), (150.0, 540.0)), ((1770.0, 540.0), (1890.0, 540.0)))
    _CORNERS = (((150.0, 60.0), (250.0, 60.0)), ((1670.0, 1020.0), (1770.0, 1020.0)))

    @pytest.mark.parametrize(
        ("segments", "cx", "cy", "measured"),
        [
            (_LEVEL, 0.0, 0.0, True),
            # 110 px right of the picture centre, the right line is under 0.8 of the half-diagonal away.
            (_LEVEL, 0.1, 0.0, False),
            (_LEVEL, -0.1, 0.0, False),
            (_CORNERS, 0.0, 0.0, True),
            # 220 px below the picture centre, the bottom right line is too close; above it, the top left one.
            (_CORNERS, 0.0, 0.2, False),
            (_CORNERS, 0.0, -0.2, False),
        ],
    )
    def test_reach_is_measured_from_the_lens_centre(self, segments, cx: float, cy: float, measured: bool) -> None:  # noqa: ANN001
        result = fit_lens_from_lines(lines_for(0.0, 0.0, segments, cx=cx, cy=cy), W, H, cx=cx, cy=cy)
        assert result.edges_measured is measured


# A short line in each outer band: (x0, y0), (x1, y1) inside the outer 15 % of that side.
_BAND = {
    "top": ((700.0, 40.0), (1200.0, 45.0)),
    "bottom": ((700.0, H - 40.0), (1200.0, H - 45.0)),
    "left": ((40.0, 400.0), (45.0, 700.0)),
    "right": ((W - 40.0, 400.0), (W - 45.0, 700.0)),
}
# Reaches a corner of the picture (beyond 0.8 of the half-diagonal) and no outer band of its own.
_CORNER_REACH = ((150.0, 120.0), (420.0, 300.0))
# Clear of every outer band.
_CENTRE = (MID_H, ((960.0, 200.0), (980.0, 880.0)))


def _hint(segments, *, rating: str = "okay", k2_fitted: bool = True, rms_px: float = 1.0, misfit: bool = False) -> str:
    return coverage_hint(
        lines_for(0.0, 0.0, segments), W, H, rating=rating, k2_fitted=k2_fitted, rms_px=rms_px, misfit=misfit
    )


def _all_but(*weak: str):
    return tuple(_BAND[name] for name in _BAND if name not in weak) + _CENTRE


class TestHint:
    def test_every_band_full_needs_nothing(self) -> None:
        assert _hint(EDGES + (MID_H,), rating="okay") == ""

    @pytest.mark.parametrize("rating", ["low", "medium", "okay", "good", "excellent"])
    def test_a_single_line_asks_for_a_second_at_any_rating(self, rating: str) -> None:
        assert _hint((TOP,), rating=rating) == "Add a second line in another part of the picture."

    def test_unfitted_corner_correction_asks_for_a_corner_line(self) -> None:
        assert _hint(_CENTRE, rating="low", k2_fitted=False) == (
            "Corner correction needs a line that reaches a corner of the picture."
        )

    def test_a_line_into_a_corner_moves_on_to_the_edges(self) -> None:
        hint = _hint((*_CENTRE, _CORNER_REACH), rating="low", k2_fitted=False)
        assert hint.startswith("If possible, add lines near")

    @pytest.mark.parametrize(
        ("weak", "said"),
        [
            (("bottom",), "If possible, add a line near the bottom edge."),
            (("top", "bottom"), "If possible, add lines near the top and bottom edges."),
            (("top", "left", "bottom"), "If possible, add lines near the top, bottom and left edges."),
            (("top", "bottom", "left", "right"), "If possible, add lines near all edges of the picture."),
        ],
        ids=["one", "two", "three", "four"],
    )
    def test_names_every_weak_edge(self, weak: tuple[str, ...], said: str) -> None:
        assert _hint(_all_but(*weak), rating="medium") == said

    def test_the_weakest_edge_comes_first(self) -> None:
        # The top band holds three points of a short line, the left none: both weak, the left first.
        top_few = ((700.0, 40.0), (1200.0, 45.0))
        lines = [*lines_for(0.0, 0.0, _all_but("top", "left")), make_line(*top_few, 0.0, 0.0, n=3)]
        hint = coverage_hint(lines, W, H, rating="medium", k2_fitted=True, rms_px=1.0, misfit=False)
        assert hint == "If possible, add lines near the left and top edges."

    @pytest.mark.parametrize("points", [4, 5])
    def test_a_band_needs_five_points(self, points: int) -> None:
        lines = [*lines_for(0.0, 0.0, _all_but("bottom")), make_line(*_BAND["bottom"], 0.0, 0.0, n=points)]
        hint = coverage_hint(lines, W, H, rating="medium", k2_fitted=True, rms_px=1.0, misfit=False)
        assert hint == ("If possible, add a line near the bottom edge." if points < 5 else "")

    @pytest.mark.parametrize("rating", ["low", "medium", "okay"])
    def test_below_good_an_edge_line_is_asked_for_only_if_possible(self, rating: str) -> None:
        # A picture may show nothing straight near an edge: a crowd, rows of seats.
        assert _hint(_all_but("bottom"), rating=rating, rms_px=2.0) == "If possible, add a line near the bottom edge."

    @pytest.mark.parametrize("rating", ["good", "excellent"])
    def test_good_or_better_asks_only_to_confirm(self, rating: str) -> None:
        assert (
            _hint(_all_but("top", "left"), rating=rating)
            == "If possible, add lines near the top and left edges to confirm it."
        )

    def test_four_lines_that_already_agree_may_be_good(self) -> None:
        four = (_BAND["bottom"], _BAND["left"], _BAND["right"], MID_H)
        assert _hint(four, rating="okay", rms_px=1.5) == (
            "The result may already be good. If possible, add a line near the top edge to confirm it."
        )

    @pytest.mark.parametrize(
        ("segments", "change"),
        [
            ((_BAND["bottom"], _BAND["left"], _BAND["right"]), {}),
            (_all_but("top"), {"misfit": True}),
            (_all_but("top"), {"rms_px": 1.51}),
            (_all_but("top") + (_CORNER_REACH,), {"k2_fitted": False}),
            (_all_but("top"), {"rating": "medium"}),
        ],
        ids=["three lines", "a misfit", "points off their curves", "no corner correction", "rated poor"],
    )
    def test_lines_that_do_not_agree_are_asked_for_more(self, segments, change: dict) -> None:  # noqa: ANN001
        kw = {"rating": "okay", "rms_px": 1.0, **change}
        assert _hint(segments, **kw) == "If possible, add a line near the top edge."

    def test_the_fit_hands_its_own_rms_to_the_hint(self) -> None:
        # Rated fair with the corner correction measured, but the points scatter more than 1.5 px about their curves
        # though one line alone stays within it.
        result = fit_lens_from_lines(lines_for(-0.2, -0.03, (BOTTOM, LEFT, RIGHT, MID_H), noise=1.8, seed=2), W, H)
        assert (result.rating, result.k2_fitted) == ("okay", True)
        assert min(line.rms_px for line in result.lines) <= 1.5 < result.rms_px
        assert not any(line.misfit for line in result.lines)
        assert result.hint == "If possible, add a line near the top edge."

    def test_four_fitted_lines_that_agree_may_be_good_though_one_scatters_more(self) -> None:
        # The whole fit's RMS decides, not the line that scatters the most.
        segments = (BOTTOM, MID_H, MID_V, _BAND["left"])
        result = fit_lens_from_lines(lines_for(-0.2, -0.03, segments, noise=1.3, seed=1), W, H)
        assert (result.rating, result.k2_fitted, len(result.lines)) == ("okay", True, 4)
        assert result.rms_px <= 1.5 < max(line.rms_px for line in result.lines)
        assert not any(line.misfit for line in result.lines)
        assert result.hint == (
            "The result may already be good. If possible, add lines near the top and right edges to confirm it."
        )

    def test_a_fitted_line_that_does_not_fit_is_asked_for_more(self) -> None:
        rng = np.random.default_rng(0)
        lines = [make_line(*s, -0.2, -0.03, noise=0.3, rng=rng) for s in (BOTTOM, MID_H, MID_V, _BAND["left"])]
        lines.append(make_line(*DIAGONAL, -0.2, -0.03, noise=0.3, rng=rng, sag=7.0))
        result = fit_lens_from_lines(lines, W, H)
        assert (result.rating, result.k2_fitted, result.rms_px <= 1.5) == ("okay", True, True)
        assert [line.misfit for line in result.lines] == [False, False, False, False, True]
        assert result.hint == "If possible, add lines near the top and right edges."


class TestMisfit:
    def test_a_bent_line_is_flagged_and_the_straight_ones_are_not(self) -> None:
        truth = (-0.45, 0.2)
        lines = lines_for(*truth, EDGES + (MID_H,), noise=1.0, seed=8)
        rng = np.random.default_rng(9)
        lines.append(make_line(*DIAGONAL, *truth, noise=1.0, rng=rng, sag=20.0))
        result = fit_lens_from_lines(lines, W, H)
        flags = [line.misfit for line in result.lines]
        assert flags[-1] is True
        assert not any(flags[:-1])

    def test_a_line_the_lens_would_bend_for_is_flagged(self) -> None:
        # Bowed 7 px among lines placed to a third of a pixel: some 2.4 px off, past the 2 px floor.
        truth = (-0.3, 0.05)
        lines = lines_for(*truth, EDGES + (MID_H,), noise=0.3, seed=8)
        lines.append(make_line(*DIAGONAL, *truth, noise=0.3, rng=np.random.default_rng(9), sag=7.0))
        result = fit_lens_from_lines(lines, W, H)
        assert [line.misfit for line in result.lines] == [False] * 5 + [True]

    @pytest.mark.parametrize(("sag", "flagged"), [(5.0, False), (7.0, True)])
    def test_a_line_within_two_pixels_of_the_others_fits_however_many_points_it_has(
        self, sag: float, flagged: bool
    ) -> None:
        # Sixteen points would make 1.7 px significant against a third of a pixel; placement
        # nobody beats keeps it fitting.
        truth = (-0.3, 0.05)
        lines = lines_for(*truth, EDGES + (MID_H,), noise=0.3, seed=8)
        lines.append(make_line(*DIAGONAL, *truth, noise=0.3, rng=np.random.default_rng(9), sag=sag, n=16))
        result = fit_lens_from_lines(lines, W, H)
        assert [line.misfit for line in result.lines] == [False] * 5 + [flagged]

    @pytest.mark.parametrize(("sag", "flagged"), [(7.5, False), (9.0, True)])
    def test_a_bowed_line_is_judged_by_its_own_degrees_of_freedom(self, sag: float, flagged: bool) -> None:
        # Five points leave three to show a bow: one more or one fewer moves the F test's
        # threshold past one of these two bows.
        truth = (-0.3, 0.05)
        lines = lines_for(*truth, EDGES, noise=1.0, seed=1)
        lines.append(make_line(*DIAGONAL, *truth, noise=1.0, rng=np.random.default_rng(101), sag=sag))
        result = fit_lens_from_lines(lines, W, H)
        assert [line.misfit for line in result.lines] == [False] * 4 + [flagged]

    @pytest.mark.parametrize(
        ("truth", "segments", "seed"),
        [
            ((-0.333, -0.025), EDGES + (DIAGONAL,), 41),
            ((-0.333, -0.025), EDGES + (DIAGONAL,), 51),
            ((-0.333, -0.025), EDGES + (DIAGONAL,), 92),
            ((-0.527, -0.069), EDGES + (MID_H, MID_V), 51),
            ((-0.527, -0.069), EDGES + (MID_H, MID_V), 54),
            ((-0.527, -0.069), EDGES + (MID_H, MID_V), 92),
        ],
    )
    def test_straight_lines_at_one_pixel_noise_are_not_flagged(self, truth, segments, seed: int) -> None:  # noqa: ANN001
        # Placements where one straight line lands 1.6-2.1 px off the others' fit: chance at
        # this noise, which real snapshots show, judged by the line's own five points.
        result = fit_lens_from_lines(lines_for(*truth, segments, noise=1.0, seed=seed), W, H)
        assert not any(line.misfit for line in result.lines)

    @pytest.mark.parametrize("seed", [1, 2, 5])
    def test_a_line_that_bends_throughout_keeps_its_points_and_is_flagged(self, seed: int) -> None:
        # Setting an end of a bow aside straightens the rest of it.
        truth = (-0.333, -0.025)
        lines = lines_for(*truth, EDGES, noise=1.0, seed=seed)
        lines.append(make_line(*DIAGONAL, *truth, noise=1.0, rng=np.random.default_rng(seed + 50), sag=12.0))
        result = fit_lens_from_lines(lines, W, H)
        assert [line.dropped for line in result.lines] == [()] * 5
        assert [line.misfit for line in result.lines] == [False] * 4 + [True]

    @pytest.mark.parametrize("seed", [5, 10, 18])
    def test_only_the_lines_that_miss_are_weighed_against_each_other(self, seed: int) -> None:
        # Two short bowed lines miss; without the long, gently bowed middle line, which fits,
        # the rest scatter least. Weighed against that, neither bowed line would be set aside.
        truth = (-0.333, -0.025)
        rng = np.random.default_rng(seed)
        layout = ((RIGHT, 16, 0.5, 0.0), (MID_V, 4, 0.5, 18.0), (MID_H, 16, 0.5, 6.0), (BOTTOM, 4, 1.5, 18.0))
        lines = [
            bowed(make_line(*segment, *truth, noise=noise, rng=rng, n=n), sag) for segment, n, noise, sag in layout
        ]
        result = fit_lens_from_lines(lines, W, H)
        assert [line.misfit for line in result.lines] == [False, True, False, True]

    def test_a_line_the_bent_one_leans_on_is_not_flagged_for_it(self) -> None:
        # Without the bottom edge the bowed diagonal sets the lens and the bottom edge misses
        # it by far more than the diagonal misses the rest, but the others then scatter.
        rng = np.random.default_rng(0)
        lines = [make_line(*s, -0.2, -0.03, noise=0.3, rng=rng) for s in (BOTTOM, MID_H, MID_V, _BAND["left"])]
        lines.append(make_line(*DIAGONAL, -0.2, -0.03, noise=0.3, rng=rng, sag=12.0))
        result = fit_lens_from_lines(lines, W, H)
        assert [line.misfit for line in result.lines] == [False, False, False, False, True]

    @pytest.mark.parametrize(("noise", "seed"), [(0.5, 0), (0.5, 1), (0.5, 2), (1.0, 18)])
    def test_a_graphic_drawn_over_the_picture_is_flagged(self, noise: float, seed: int) -> None:
        # Another system's on-screen display: straight in the picture itself, alone near its top,
        # where it alone sets k2. Only a refit of both coefficients without it sees it bent. At
        # 1 px the stage lip misses too, and the others agree alike without either; the graphic
        # misses more.
        truth = (-0.71, -0.17)
        stage = (
            ((470.0, 909.0), (1420.0, 904.0)),
            ((560.0, 788.0), (1330.0, 782.0)),
            ((560.0, 660.0), (1330.0, 654.0)),
            ((432.0, 666.0), (382.0, 846.0)),
            ((1446.0, 628.0), (1510.0, 844.0)),
        )
        lines = lines_for(*truth, stage, noise=noise, seed=seed)
        lines.append([[float(x), 10.0] for x in np.linspace(32.0, 310.0, 5)])
        result = fit_lens_from_lines(lines, W, H)
        assert [line.misfit for line in result.lines] == [False] * 5 + [True]

    def test_the_only_line_with_curvature_is_not_flagged(self) -> None:
        """Without it the rest pin k1 down on nothing, so its misfit under their fit means nothing."""
        truth = (-0.3, 0.05)
        rng = np.random.default_rng(11)
        lines = [
            make_line(*BOTTOM, *truth, noise=1.0, rng=rng),
            make_line((860.0, 500.0), (1060.0, 520.0), *truth, noise=1.0, rng=rng),
            make_line((900.0, 600.0), (1000.0, 560.0), *truth, noise=1.0, rng=rng),
        ]
        result = fit_lens_from_lines(lines, W, H)
        assert not any(line.misfit for line in result.lines)

    def test_lines_through_the_centre_cannot_convict_the_rest(self) -> None:
        """A line through the centre moves along itself under the warp: it says nothing about k1."""
        truth = (-0.3, 0.05)
        rng = np.random.default_rng(12)
        lines = [
            make_line(*BOTTOM, *truth, noise=1.0, rng=rng),
            make_line((760.0, 540.0), (1160.0, 540.0), *truth),
            make_line((960.0, 340.0), (960.0, 740.0), *truth),
        ]
        result = fit_lens_from_lines(lines, W, H)
        assert not any(line.misfit for line in result.lines)

    def test_two_lines_are_never_flagged(self) -> None:
        # With two lines there is no telling which one is wrong.
        rng = np.random.default_rng(10)
        lines = [
            make_line(*TOP, 0.0, 0.0, noise=1.0, rng=rng),
            make_line(*BOTTOM, 0.0, 0.0, noise=1.0, rng=rng, sag=30.0),
        ]
        result = fit_lens_from_lines(lines, W, H)
        assert not any(line.misfit for line in result.lines)

    def test_straight_lines_at_three_pixel_noise_are_mostly_unflagged(self) -> None:
        flagged = 0
        for seed in range(6):
            result = fit_lens_from_lines(lines_for(-0.3, 0.05, EDGES + (MID_H, DIAGONAL), noise=3.0, seed=seed), W, H)
            flagged += sum(line.misfit for line in result.lines)
        assert flagged <= 2


class TestStatistics:
    @pytest.mark.parametrize(
        ("d1", "d2", "tail", "point"),
        [
            (1, 10, 1e-3, 21.04),
            (2, 30, 0.05, 3.316),
            (3, 14, 1e-3, 9.729),
            (2, 1000, 1e-3, 6.956),
            (3, 10**6, 1e-3, 16.266 / 3),
            (14, 30, 1e-3, 3.825),
        ],
    )
    def test_the_f_points_are_the_tabulated_ones(self, d1: int, d2: int, tail: float, point: float) -> None:
        # The misfit and stray tests read their thresholds here; no scipy on a station.
        assert _f_upper(d1, d2, tail) == pytest.approx(point, rel=2e-3)


class TestStrays:
    @staticmethod
    def _moved(seed: int, *, line: int = 1, point: int = 2, by: float = 8.0) -> list[list[list[float]]]:
        """Edges and a diagonal with one point moved across its line: snapped to the other side of a tape."""
        lines = lines_for(-0.3, 0.05, EDGES + (DIAGONAL,), noise=0.5, seed=seed)
        p = np.asarray(lines[line])
        t = p[-1] - p[0]
        lines[line][point] = list(p[point] + by * np.array([-t[1], t[0]]) / np.hypot(*t))
        return lines

    @pytest.mark.parametrize("seed", range(3))
    def test_a_point_that_left_its_line_is_set_aside_before_the_lens_is_fitted(self, seed: int) -> None:
        result = fit_lens_from_lines(self._moved(seed), W, H)
        assert [line.dropped for line in result.lines] == [(), (2,), (), (), ()]
        # Kept, it bent the lens some 4 px across the frame.
        assert frame_error_px(_lens(result), (-0.3, 0.05, 0.0, 0.0)) < 1.5
        assert not any(line.misfit for line in result.lines)

    def test_straight_lines_keep_every_point(self) -> None:
        for seed in range(12):
            result = fit_lens_from_lines(lines_for(-0.3, 0.05, EDGES + (DIAGONAL, MID_H), noise=1.0, seed=seed), W, H)
            assert all(line.dropped == () for line in result.lines)

    @pytest.mark.parametrize(
        ("truth", "segments", "noise", "seed", "bent", "sag"),
        [
            ((-0.097, -0.002), EDGES + (MID_H, MID_V), 0.5, 171, 1, 12.0),
            ((-0.527, -0.069), EDGES, 0.3, 579, 0, 13.25),
            ((0.0, 0.0), (TOP, BOTTOM, LEFT), 1.0, 812, 0, 9.2),
        ],
        ids=["6mm-bottom-edge", "2.3mm-top-edge", "pinhole-top-edge-of-three"],
    )
    def test_a_bowed_line_keeps_its_points_and_takes_the_blame(
        self, truth: tuple[float, float], segments: tuple, noise: float, seed: int, bent: int, sag: float
    ) -> None:
        # Judged under the lens the bowed line pulled its way, its other points ran straight:
        # an end was set aside, and a straight line flagged in its place or nothing at all.
        lines = lines_for(*truth, segments, noise=noise, seed=seed)
        lines[bent] = bowed(lines[bent], sag)
        result = fit_lens_from_lines(lines, W, H)
        assert [line.dropped for line in result.lines] == [()] * len(segments)
        assert [line.misfit for line in result.lines] == [i == bent for i in range(len(segments))]

    def test_a_bowed_line_beside_one_through_the_centre_keeps_its_points(self) -> None:
        # The line through the centre pins the lens nowhere: judged under its unbounded
        # refit, any bow would pass for straight and an end of this one was set aside.
        bowed = make_line(*MID_H, -0.3, 0.05, noise=0.5, rng=np.random.default_rng(2), sag=12.0)
        radial = [[W / 2 + t * 700.0, H / 2 + t * 350.0] for t in (-1.0, -0.5, 0.0, 0.5, 1.0)]
        result = fit_lens_from_lines([bowed, radial], W, H)
        assert [line.dropped for line in result.lines] == [(), ()]

    def test_a_point_off_a_strong_lens_edge_is_set_aside(self) -> None:
        # Judged with neither the refit's own uncertainty nor the refit, the edge's other
        # points looked bent and the edge was flagged instead.
        lines = lines_for(-0.527, -0.069, EDGES, noise=0.5, seed=643)
        p = np.asarray(lines[2])
        chord = p[-1] - p[0]
        lines[2][2] = list(p[2] + 8.0 * np.array([-chord[1], chord[0]]) / np.hypot(*chord))
        result = fit_lens_from_lines(lines, W, H)
        assert [line.dropped for line in result.lines] == [(), (), (2,), ()]
        assert not any(line.misfit for line in result.lines)

    def test_a_point_off_a_line_alone_is_set_aside(self) -> None:
        # With no other line to refit the lens from, the line is judged under its own fit.
        line = [[200.0 + 190.0 * i, 300.0 + (25.0 if i == 7 else 0.0)] for i in range(9)]
        result = fit_lens_from_lines([line], W, H)
        assert result.lines[0].dropped == (7,)

    def test_a_line_of_four_points_keeps_them_all(self) -> None:
        # Set one aside and three are left, which any lens bends through.
        lines = self._moved(0)
        lines[1] = lines[1][:4]
        result = fit_lens_from_lines(lines, W, H)
        assert result.lines[1].dropped == ()


class TestCurves:
    def test_curve_runs_along_the_traced_points(self) -> None:
        truth = (-0.3, 0.05)
        lines = lines_for(*truth, EDGES)
        result = fit_lens_from_lines(lines, W, H)
        for line, pts in zip(result.lines, lines, strict=True):
            curve = np.asarray(line.curve)
            assert curve.shape == (24, 2)
            assert np.isfinite(curve).all()
            # Every traced point lies on the predicted curve.
            for x, y in pts:
                assert _distance_to_polyline(np.array([x, y]), curve) < 1.0


class TestInputRules:
    def test_no_lines_is_an_error(self) -> None:
        with pytest.raises(ValueError, match="at least one line"):
            fit_lens_from_lines([], W, H)

    def test_a_line_with_two_points_is_an_error(self) -> None:
        with pytest.raises(ValueError, match="at least 3 points"):
            fit_lens_from_lines([[[0.0, 0.0], [100.0, 0.0]]], W, H)

    def test_a_non_finite_point_is_an_error(self) -> None:
        with pytest.raises(ValueError, match="non-finite"):
            fit_lens_from_lines([[[0.0, 0.0], [50.0, float("nan")], [100.0, 0.0]]], W, H)

    def test_a_degenerate_line_is_an_error(self) -> None:
        with pytest.raises(ValueError, match="shorter"):
            fit_lens_from_lines([[[0.0, 0.0], [1.0, 1.0], [2.0, 2.0]]], W, H)

    def test_more_lines_than_the_cap_is_an_error(self) -> None:
        line = [[0.0, 2.0], [50.0, 0.0], [100.0, 0.0]]
        with pytest.raises(ValueError, match=f"At most {MAX_LINES} lines"):
            fit_lens_from_lines([line] * (MAX_LINES + 1), W, H)

    def test_a_line_with_more_points_than_the_cap_is_an_error(self) -> None:
        line = [[10.0 * i, 0.0] for i in range(MAX_POINTS_PER_LINE + 1)]
        with pytest.raises(ValueError, match="more than"):
            fit_lens_from_lines([line], W, H)

    @pytest.mark.parametrize("w,h", [(0.0, H), (W, -1.0), (float("nan"), H), (W, float("inf"))])
    def test_a_bad_canvas_is_an_error(self, w: float, h: float) -> None:
        with pytest.raises(ValueError, match="canvas"):
            fit_lens_from_lines(lines_for(0.0, 0.0, EDGES), w, h)

    def test_malformed_point_shape_is_an_error(self) -> None:
        with pytest.raises(ValueError):
            fit_lens_from_lines([[[0.0, 0.0, 1.0], [50.0, 0.0, 1.0], [100.0, 0.0, 1.0]]], W, H)


class TestJsonShape:
    def test_dict_carries_every_field_and_no_non_finite_value(self) -> None:
        result = fit_lens_from_lines(lines_for(-0.2, 0.02, EDGES), W, H)
        data = fit_result_to_dict(result)
        assert set(data) == {
            "k1",
            "k2",
            "k2_fitted",
            "cx",
            "cy",
            "rms_px",
            "uncertainty_px",
            "rating",
            "hint",
            "edges_measured",
            "lines",
        }
        assert data["edges_measured"] is True
        assert set(data["lines"][0]) == {"rms_px", "misfit", "curve", "dropped"}
        assert data["lines"][0]["dropped"] == []
        assert isinstance(data["lines"][0]["curve"][0], list)

    def test_infinite_uncertainty_becomes_null(self) -> None:
        result = LensFitResult(0.0, 0.0, False, 0.0, 0.0, 0.0, float("inf"), RATING_LOW, "", (), True)
        assert fit_result_to_dict(result)["uncertainty_px"] is None

    def test_the_centre_is_answered_as_given(self) -> None:
        result = fit_lens_from_lines(lines_for(-0.2, 0.0, EDGES), W, H, cx=0.06, cy=-0.04)
        data = fit_result_to_dict(result)
        assert (data["cx"], data["cy"]) == (0.06, -0.04)

    def test_a_point_set_aside_is_answered_by_its_index_in_its_line(self) -> None:
        line = LensLineFit(rms_px=0.5, misfit=False, curve=((0.0, 0.0), (1.0, 1.0)), dropped=(2,))
        result = LensFitResult(-0.3, -0.05, True, 0.0, 0.0, 0.5, 3.0, "good", "", (line,), True)
        assert fit_result_to_dict(result)["lines"][0]["dropped"] == [2]


class TestDegenerateGeometry:
    def test_a_single_radial_line_tells_nothing(self) -> None:
        # Through the centre the warp moves every point along the line, so no
        # coefficient changes a residual: the information matrix is singular and
        # the rating is honest about it.
        line = [[W / 2 + t * 700.0, H / 2 + t * 350.0] for t in (-1.0, -0.5, 0.0, 0.5, 1.0)]
        result = fit_lens_from_lines([line], W, H)
        assert result.uncertainty_px == float("inf")
        assert result.rating == RATING_LOW
        assert fit_result_to_dict(result)["uncertainty_px"] is None
