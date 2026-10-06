# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The plumb-line fit: k1 / k2 back from lines warped with the real overlay functions."""

from __future__ import annotations

import numpy as np
import pytest

from openfollow.scene.lens_fit import (
    K2_SIGMA_GATE,
    RATING_LOW,
    RATING_THRESHOLDS_PX,
    RATINGS,
    LensFitResult,
    coverage_hint,
    fit_lens_from_lines,
    fit_result_to_dict,
    rate_uncertainty,
)
from openfollow.scene.solver import apply_overlay_distortion, invert_overlay_distortion

pytestmark = pytest.mark.unit

W, H = 1920.0, 1080.0
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
) -> list[list[float]]:
    """Points of a line that is straight in reality, as seen through the lens."""
    ends = invert_overlay_distortion(np.array([p0, p1], dtype=np.float64), W, H, k1, k2)
    t = np.linspace(0.0, 1.0, n)[:, None]
    straight = ends[0] + t * (ends[1] - ends[0])
    if sag:
        tangent = ends[1] - ends[0]
        normal = np.array([-tangent[1], tangent[0]]) / np.linalg.norm(tangent)
        straight = straight + (sag * 4.0 * t * (1.0 - t)) * normal
    seen = apply_overlay_distortion(straight, W, H, k1, k2)
    if noise and rng is not None:
        seen = seen + rng.normal(0.0, noise, seen.shape)
    return [[float(x), float(y)] for x, y in seen]


def lines_for(k1: float, k2: float, segments, *, noise: float = 0.0, seed: int = 0, **kw) -> list[list[list[float]]]:
    rng = np.random.default_rng(seed)
    return [make_line(a, b, k1, k2, noise=noise, rng=rng, **kw) for a, b in segments]


def _distance_to_polyline(point: np.ndarray, polyline: np.ndarray) -> float:
    a, b = polyline[:-1], polyline[1:]
    ab = b - a
    t = np.clip(np.einsum("ij,ij->i", point - a, ab) / np.einsum("ij,ij->i", ab, ab), 0.0, 1.0)
    nearest = a + t[:, None] * ab
    return float(np.min(np.hypot(*(nearest - point).T)))


class TestRecovery:
    @pytest.mark.parametrize("truth", [(0.0, 0.0), (-0.15, 0.0), (-0.3, 0.05), (0.2, 0.0)])
    def test_exact_lines_return_the_pair(self, truth: tuple[float, float]) -> None:
        result = fit_lens_from_lines(lines_for(*truth, EDGES + (DIAGONAL,)), W, H)
        assert result.k1 == pytest.approx(truth[0], abs=2e-3)
        assert result.k2 == pytest.approx(truth[1], abs=2e-3)
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

    def test_three_pixel_noise_stays_within_the_issue_budget(self) -> None:
        truth = (-0.45, 0.20)
        result = fit_lens_from_lines(lines_for(*truth, EDGES + (DIAGONAL,), noise=3.0, seed=4, n=8), W, H)
        assert result.k1 == pytest.approx(truth[0], abs=0.03)
        assert result.k2 == pytest.approx(truth[1], abs=0.03)

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


class TestK2Gate:
    def test_k2_stays_zero_without_corner_coverage(self) -> None:
        truth = (-0.3, 0.05)
        result = fit_lens_from_lines(lines_for(*truth, (MID_H, MID_V), noise=1.0, seed=1), W, H)
        assert not result.k2_fitted
        assert result.k2 == 0.0
        assert "corner" in result.hint

    def test_k2_is_fitted_once_lines_reach_the_corners(self) -> None:
        truth = (-0.3, 0.05)
        result = fit_lens_from_lines(lines_for(*truth, EDGES, noise=1.0, seed=1), W, H)
        assert result.k2_fitted
        assert result.k2 == pytest.approx(truth[1], abs=0.02)

    def test_gate_is_a_small_sigma(self) -> None:
        assert 0.0 < K2_SIGMA_GATE <= 0.1


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

    def test_sloppier_points_lower_the_rating(self) -> None:
        precise = fit_lens_from_lines(lines_for(-0.3, 0.05, EDGES, noise=0.5, seed=3), W, H)
        sloppy = fit_lens_from_lines(lines_for(-0.3, 0.05, EDGES, noise=4.0, seed=3), W, H)
        assert sloppy.uncertainty_px > precise.uncertainty_px
        assert RATINGS.index(sloppy.rating) <= RATINGS.index(precise.rating)


class TestHint:
    def test_excellent_has_no_hint(self) -> None:
        assert coverage_hint(lines_for(0.0, 0.0, EDGES), W, H, rating="excellent", k2_fitted=True) == ""

    def test_single_line_asks_for_a_second(self) -> None:
        assert "second line" in coverage_hint(lines_for(0.0, 0.0, (TOP,)), W, H, rating="low", k2_fitted=True)

    def test_unfitted_k2_asks_for_a_corner(self) -> None:
        assert coverage_hint(lines_for(0.0, 0.0, (MID_H, MID_V)), W, H, rating="low", k2_fitted=False) == (
            "k2 needs a line near a corner."
        )

    def test_names_the_empty_edge_band(self) -> None:
        hint = coverage_hint(lines_for(0.0, 0.0, (TOP, LEFT, RIGHT)), W, H, rating="okay", k2_fitted=True)
        assert hint == "Add a line near the bottom edge."

    def test_balanced_coverage_asks_for_more_or_better_points(self) -> None:
        hint = coverage_hint(lines_for(0.0, 0.0, EDGES), W, H, rating="good", k2_fitted=True)
        assert "more lines" in hint and "precisely" in hint


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
        assert set(data) == {"k1", "k2", "k2_fitted", "rms_px", "uncertainty_px", "rating", "hint", "lines"}
        assert set(data["lines"][0]) == {"rms_px", "misfit", "curve"}
        assert isinstance(data["lines"][0]["curve"][0], list)

    def test_infinite_uncertainty_becomes_null(self) -> None:
        result = LensFitResult(0.0, 0.0, False, 0.0, float("inf"), RATING_LOW, "", ())
        assert fit_result_to_dict(result)["uncertainty_px"] is None


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
