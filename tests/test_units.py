# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Unit formatter / parser contract.

Pins the adaptive imperial format boundaries, the Postel-style parser
(both modes accept any unit token; bare numbers take the mode default),
and the round-trip contract. Imperial display is lossy at 0.01 in, so
the round-trip guarantee is ``abs(parse(format(x)) - x) <= 0.0002 m``
(half the display step) – NOT bit-exact. The web UI's ``Stored: X.XXX m``
echo shows the exact persisted value, which is the real guarantee.
"""

from __future__ import annotations

import pytest

from openfollow.units import (
    UnitSystem,
    format_length,
    format_length_compact,
    format_speed,
    metric_echo,
    metric_echo_speed,
    parse_length,
    parse_speed,
    unit_suffix_length,
    unit_suffix_speed,
)

pytestmark = pytest.mark.unit

M = UnitSystem.METRIC
IMP = UnitSystem.IMPERIAL


class TestFormatLength:
    @pytest.mark.parametrize(
        "meters,expected",
        [
            (0.0, "0.000 m"),
            (1.0, "1.000 m"),
            (0.05, "0.050 m"),
            (-1.234, "-1.234 m"),
            (12.7, "12.700 m"),
        ],
    )
    def test_metric(self, meters: float, expected: str) -> None:
        assert format_length(meters, M) == expected

    @pytest.mark.parametrize(
        "meters,expected",
        [
            # Dyadic halves (n/16) with an even digit before the cut: format()
            # rounds half-to-EVEN (keeps it), NOT half-up. The web echo
            # (units.js toFixedHalfEven) must agree or the operator sees a
            # different stored value than the server persists.
            (0.0625, "0.062 m"),
            (0.3125, "0.312 m"),
            (0.5625, "0.562 m"),
            (0.8125, "0.812 m"),
        ],
    )
    def test_metric_rounds_half_to_even(self, meters: float, expected: str) -> None:
        # JS toFixed(3) would round these half-AWAY (…063/…313/…563/…813).
        assert format_length(meters, M) == expected
        assert metric_echo(meters) == expected

    @pytest.mark.parametrize(
        "meters,expected",
        [
            (0.0, "0.00 in"),
            (0.05, "1.97 in"),  # hysteresis sanity
            (0.30, "11.81 in"),  # genuine sub-foot inches form
            (0.3047, "1 ft 0.00 in"),  # rounds to 12 in → carries to 1 ft, not "12.00 in"
            (-0.3047, "-1 ft 0.00 in"),  # symmetric for negatives
            (0.3048, "1 ft 0.00 in"),  # first ft+in case
            (1.0, "3 ft 3.37 in"),
            (5.0, "16 ft 4.85 in"),
        ],
    )
    def test_imperial(self, meters: float, expected: str) -> None:
        assert format_length(meters, IMP) == expected

    def test_imperial_negative_subfoot(self) -> None:
        assert format_length(-0.05, IMP) == "-1.97 in"

    def test_imperial_negative_ftin(self) -> None:
        assert format_length(-1.234, IMP) == "-4 ft 0.58 in"

    def test_imperial_carry_avoids_twelve_inches(self) -> None:
        # 0.99999 ft worth of metres: total inches ≈ 11.99988 → 12.00 at
        # 2 dp, but magnitude >= 1 ft so it takes the ft+in branch.
        text = format_length(0.3048 * 1.9999958, IMP)
        assert "12.00 in" not in text


class TestFormatSpeed:
    def test_metric(self) -> None:
        assert format_speed(1.5, M) == "1.50 m/s"

    def test_imperial(self) -> None:
        assert format_speed(1.5, IMP) == "4.92 ft/s"


class TestFormatLengthCompact:
    def test_metric_is_signed_metres(self) -> None:
        assert format_length_compact(1.5, M).strip() == "+1.50"
        assert format_length_compact(-0.75, M).strip() == "-0.75"

    def test_imperial_is_decimal_feet(self) -> None:
        # 1.5 m = 4.92 ft (decimal feet, not ft+in – HUD column width).
        assert format_length_compact(1.5, IMP).strip() == "+4.92"


class TestSuffixes:
    def test_length(self) -> None:
        assert unit_suffix_length(M) == "m"
        assert unit_suffix_length(IMP) == "ft / in"

    def test_speed(self) -> None:
        assert unit_suffix_speed(M) == "m/s"
        assert unit_suffix_speed(IMP) == "ft/s"


class TestMetricEcho:
    def test_always_three_dp_metres(self) -> None:
        assert metric_echo(1.6764) == "1.676 m"
        assert metric_echo(0.0) == "0.000 m"
        assert metric_echo(-3.7) == "-3.700 m"

    def test_speed_echo_is_three_dp_mps(self) -> None:
        assert metric_echo_speed(1.5) == "1.500 m/s"
        assert metric_echo_speed(0.0) == "0.000 m/s"
        assert metric_echo_speed(-2.25) == "-2.250 m/s"


class TestParseLength:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("12.5 m", 12.5),
            ("50 cm", 0.5),
            ("500 mm", 0.5),
            ("5 ft", 1.524),
            ("5'", 1.524),
            ("6 in", 0.1524),
            ('6"', 0.1524),
            ("5 ft 6 in", 1.6764),
            ("5'6\"", 1.6764),
            ("5' 6\"", 1.6764),
            ("1.524m", 1.524),
            (".5 m", 0.5),
        ],
    )
    def test_explicit_units_both_modes(self, raw: str, expected: float) -> None:
        # Explicit units parse identically regardless of active mode.
        assert parse_length(raw, M) == pytest.approx(expected, abs=1e-9)
        assert parse_length(raw, IMP) == pytest.approx(expected, abs=1e-9)

    def test_bare_number_default_unit_differs_by_mode(self) -> None:
        assert parse_length("5", M) == pytest.approx(5.0)  # metres
        assert parse_length("5", IMP) == pytest.approx(1.524)  # feet

    def test_leading_sign_distributes_over_compound(self) -> None:
        # -5'6" is -(5 ft + 6 in), not -5 ft + 6 in.
        assert parse_length("-5'6\"", IMP) == pytest.approx(-1.6764, abs=1e-9)
        assert parse_length("+5 ft 6 in", IMP) == pytest.approx(1.6764, abs=1e-9)

    @pytest.mark.parametrize("raw", ["", "   ", "five feet", "5 ft junk", "ft", "5 6 in", "5 ft -6 in"])
    def test_rejects_garbage(self, raw: str) -> None:
        with pytest.raises(ValueError):
            parse_length(raw, IMP)

    def test_rejects_non_finite(self) -> None:
        # A huge digit string matches the numeric regex but float()s to inf;
        # must be rejected, not persisted.
        huge = "1" + "0" * 400
        with pytest.raises(ValueError):
            parse_length(huge, M)


class TestParseSpeed:
    def test_bare_default_by_mode(self) -> None:
        assert parse_speed("1.5", M) == pytest.approx(1.5)  # m/s
        assert parse_speed("4.92", IMP) == pytest.approx(4.92 * 0.3048)

    def test_explicit_units_both_modes(self) -> None:
        assert parse_speed("1.5 m/s", IMP) == pytest.approx(1.5)
        assert parse_speed("4.92 ft/s", M) == pytest.approx(4.92 * 0.3048)

    @pytest.mark.parametrize("raw", ["", "fast", "5 mph", "5 m/s extra"])
    def test_rejects_garbage(self, raw: str) -> None:
        with pytest.raises(ValueError):
            parse_speed(raw, M)

    def test_rejects_non_finite(self) -> None:
        huge = "1" + "0" * 400
        with pytest.raises(ValueError):
            parse_speed(huge, M)


class TestRoundTrip:
    """parse(format(x)) within the display resolution. Metric is exact
    to 3 dp; imperial is lossy at 0.01 in (~0.000254 m), so we assert
    drift <= 0.0002 m (half the inch display step)."""

    GRID = [0.001, 0.05, 0.1, 0.3047, 0.3048, 1.234, 5.0, 12.7, -3.7, 100.0]

    @pytest.mark.parametrize("x", GRID)
    def test_metric_round_trip(self, x: float) -> None:
        assert parse_length(format_length(x, M), M) == pytest.approx(x, abs=1e-3)

    @pytest.mark.parametrize("x", GRID)
    def test_imperial_round_trip_within_display_resolution(self, x: float) -> None:
        back = parse_length(format_length(x, IMP), IMP)
        assert abs(back - x) <= 0.0002, f"{x} -> {format_length(x, IMP)} -> {back}"

    @pytest.mark.parametrize("mps", [0.0, 0.1, 1.5, 4.92, 100.0])
    def test_speed_round_trip(self, mps: float) -> None:
        for mode in (M, IMP):
            back = parse_speed(format_speed(mps, mode), mode)
            # Speed displays to 2 dp in the active unit.
            tol = 0.01 if mode is M else 0.01 * 0.3048
            assert abs(back - mps) <= tol + 1e-9
