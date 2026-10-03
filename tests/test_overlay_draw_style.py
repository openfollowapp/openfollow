# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for :mod:`openfollow.runtime.overlay_draw_style`.

Covers the pure helpers exported for other draw passes:

* :func:`draw_rounded_rect` – the rounded-rect path primitive (degenerate
  ``radius <= 0`` short-circuit plus the four-arc normal path).
* :func:`draw_card_background` – the shared overlay-card chrome (translucent
  fill + soft white border) used by the message cards and every HUD panel.
* :func:`status_level` – any writer's value mapped to a level the HUD draws.
* :func:`draw_level_box` / :func:`draw_level_sign` – a status level's chip
  fill and border, and its sign.
* :func:`parse_hex` – tolerant ``#rrggbb`` → (r, g, b) converter with
  defined fallbacks for malformed input.
* :func:`speed_color` – green→yellow→red gradient used by the marker
  speed bar, including the boundary at ``t == 0.5``.
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from openfollow.runtime.overlay_draw_style import (
    CARD_BG_ALPHA,
    COLOR_BG_BASE,
    COLOR_BORDER,
    COLOR_CAUTION_BORDER,
    COLOR_CAUTION_FILL,
    COLOR_DANGER_BG,
    COLOR_INFO_BORDER,
    COLOR_INFO_FILL,
    COLOR_SUCCESS_BORDER,
    COLOR_SUCCESS_FILL,
    COLOR_WARNING_BORDER,
    COLOR_WARNING_FILL,
    STATUS_LEVEL_COLORS,
    STATUS_LEVELS,
    draw_card_background,
    draw_level_box,
    draw_level_sign,
    draw_rounded_rect,
    parse_hex,
    speed_color,
    status_level,
)
from tests._fake_cairo import FakeCairo

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# draw_card_background
# --------------------------------------------------------------------------- #


class TestDrawCardBackground:
    """Shared overlay-card chrome – translucent fill + soft white border –
    used by the operator-message cards and every HUD panel."""

    def test_paints_translucent_fill_and_soft_border(self) -> None:
        cr = FakeCairo()
        draw_card_background(cr, 5.0, 5.0, 100.0, 50.0, radius=10.0)
        # Translucent COLOR_BG_BASE fill at CARD_BG_ALPHA (not opaque).
        assert ("rgba", *COLOR_BG_BASE, CARD_BG_ALPHA) in cr.calls
        assert ("rgb", *COLOR_BG_BASE) not in cr.calls
        # Soft white border at 1px.
        assert ("rgba", *COLOR_BORDER) in cr.calls
        assert ("line_width", 1.0) in cr.calls
        # Exactly one filled body + one stroked border, no gradient pattern.
        assert cr.fills == 1
        assert cr.strokes == 1
        assert not any(c[0] == "source_pattern" for c in cr.calls)

    def test_default_radius_rounds_corners(self) -> None:
        cr = FakeCairo()
        draw_card_background(cr, 0.0, 0.0, 80.0, 40.0)
        # Default radius > 0 → rounded path (arcs), not plain rectangles.
        assert cr.arcs
        assert cr.rects == []

    def test_non_positive_radius_uses_plain_rectangles(self) -> None:
        cr = FakeCairo()
        draw_card_background(cr, 0.0, 0.0, 80.0, 40.0, radius=0.0)
        # radius <= 0 short-circuits to plain rectangles (fill + border paths).
        assert cr.arcs == []
        assert len(cr.rects) == 2


# --------------------------------------------------------------------------- #
# status_level / draw_level_box
# --------------------------------------------------------------------------- #


class TestStatusLevel:
    """A status row's level comes from whatever a subsystem wrote."""

    @pytest.mark.parametrize("level", ["error", "caution", "info", "success"])
    def test_a_known_level_is_kept(self, level: str) -> None:
        assert status_level(level) == level

    @pytest.mark.parametrize(
        "value",
        ["", "warning", "ERROR", None, 3, ["info"], {"level": "info"}],
        ids=["empty", "unknown", "wrong-case", "none", "int", "unhashable-list", "unhashable-dict"],
    )
    def test_anything_else_reads_as_an_error(self, value: object) -> None:
        """A malformed writer shows as a fault rather than as news, or not at all."""
        assert status_level(value) == "error"

    def test_the_gravity_order_names_every_level_once(self) -> None:
        assert len(STATUS_LEVELS) == len(set(STATUS_LEVELS))
        assert set(STATUS_LEVELS) == set(STATUS_LEVEL_COLORS)


class _PaintRecorder(FakeCairo):
    """Models Cairo's current path, so each paint records the outline, source and line width it used."""

    def __init__(self) -> None:
        super().__init__()
        self._path: list[tuple[Any, ...]] = []
        self.painted: list[tuple[str, tuple[float, ...], float, tuple[tuple[Any, ...], ...]]] = []

    def move_to(self, x: float, y: float) -> None:
        super().move_to(x, y)
        self._path.append(("move_to", x, y))

    def line_to(self, x: float, y: float) -> None:
        super().line_to(x, y)
        self._path.append(("line_to", x, y))

    def arc(self, cx: float, cy: float, r: float, a0: float, a1: float) -> None:
        super().arc(cx, cy, r, a0, a1)
        self._path.append(("arc", cx, cy, r, a0, a1))

    def rectangle(self, x: float, y: float, w: float, h: float) -> None:
        super().rectangle(x, y, w, h)
        self._path.append(("rectangle", x, y, w, h))

    def close_path(self) -> None:
        super().close_path()
        self._path.append(("close_path",))

    def _paint(self, kind: str, *, keep_path: bool) -> None:
        self.painted.append((kind, self._cur_rgba, self._cur_line_width, tuple(self._path)))
        if not keep_path:
            self._path = []

    def fill(self) -> None:
        super().fill()
        self._paint("fill", keep_path=False)

    def fill_preserve(self) -> None:
        super().fill_preserve()
        self._paint("fill", keep_path=True)

    def stroke(self) -> None:
        super().stroke()
        self._paint("stroke", keep_path=False)


class TestDrawLevelBox:
    @pytest.mark.parametrize(
        ("level", "fill", "border"),
        [
            ("error", COLOR_WARNING_FILL, COLOR_WARNING_BORDER),
            ("caution", COLOR_CAUTION_FILL, COLOR_CAUTION_BORDER),
            ("info", COLOR_INFO_FILL, COLOR_INFO_BORDER),
            ("success", COLOR_SUCCESS_FILL, COLOR_SUCCESS_BORDER),
        ],
    )
    def test_a_box_in_the_levels_chip_fill_outlined_in_its_border(
        self, level: str, fill: tuple[float, ...], border: tuple[float, ...]
    ) -> None:
        cr = _PaintRecorder()
        draw_level_box(cr, level, 0.0, 0.0, 120.0, 24.0, radius=4.0, line_width=1.2)
        (filled,) = [p for p in cr.painted if p[0] == "fill"]
        (stroked,) = [p for p in cr.painted if p[0] == "stroke"]
        assert filled[1] == fill
        assert stroked[1] == (*border, 1.0)
        assert stroked[2] == 1.2
        assert filled[3], "nothing was filled"
        assert stroked[3] == filled[3], "the border does not outline the filled box"
        assert {r for _cx, _cy, r in cr.arcs} == {4.0}

    @pytest.mark.parametrize("level", ["warning", ["info"]], ids=["unknown", "unhashable"])
    def test_a_level_nobody_defined_draws_as_an_error(self, level: object) -> None:
        bad, error = FakeCairo(), FakeCairo()
        draw_level_box(bad, level, 0.0, 0.0, 120.0, 24.0, radius=4.0, line_width=1.2)  # type: ignore[arg-type]
        draw_level_box(error, "error", 0.0, 0.0, 120.0, 24.0, radius=4.0, line_width=1.2)
        assert bad.calls == error.calls


class TestDrawLevelSign:
    @pytest.mark.parametrize("level", ["warning", ["info"]], ids=["unknown", "unhashable"])
    def test_a_level_nobody_defined_draws_the_warning_sign(self, level: object) -> None:
        bad, error = FakeCairo(), FakeCairo()
        draw_level_sign(bad, level, 10.0, 10.0)  # type: ignore[arg-type]
        draw_level_sign(error, "error", 10.0, 10.0)
        assert bad.calls == error.calls
        assert ("rgb", *COLOR_DANGER_BG) in error.calls, "the '!' is cut in the warning red"


# --------------------------------------------------------------------------- #
# draw_rounded_rect
# --------------------------------------------------------------------------- #


class TestDrawRoundedRect:
    """The rounded-rect path primitive feeds every panel + card background."""

    @pytest.mark.parametrize("radius", [0, -1.0, -25.0])
    def test_non_positive_radius_emits_plain_rectangle(self, radius: float) -> None:
        cr = FakeCairo()
        draw_rounded_rect(cr, 10.0, 20.0, 100.0, 50.0, radius)
        # Short-circuit: one rectangle, no arcs / move_to / line_to / close_path.
        assert cr.rects == [(10.0, 20.0, 100.0, 50.0)]
        assert cr.arcs == []
        assert cr.move_tos == []
        assert cr.line_tos == []
        assert cr.closes == 0

    def test_positive_radius_emits_four_arcs_and_closed_path(self) -> None:
        cr = FakeCairo()
        draw_rounded_rect(cr, 0.0, 0.0, 100.0, 60.0, 8.0)

        # Four corner arcs, four straight segments between them, closed path.
        assert len(cr.arcs) == 4, cr.arcs
        assert len(cr.line_tos) == 4
        assert len(cr.move_tos) == 1  # initial move_to(x + radius, y)
        assert cr.closes == 1
        # No rectangle primitive – the full path is built from arcs + lines.
        assert cr.rects == []

    def test_arc_centers_are_the_inset_corners(self) -> None:
        """Each arc is centred at a corner inset by ``radius``."""
        cr = FakeCairo()
        x, y, w, h, r = 5.0, 7.0, 40.0, 30.0, 4.0
        draw_rounded_rect(cr, x, y, w, h, r)

        centers = {(round(cx, 6), round(cy, 6)) for cx, cy, _ in cr.arcs}
        expected = {
            (x + w - r, y + r),  # top-right
            (x + w - r, y + h - r),  # bottom-right
            (x + r, y + h - r),  # bottom-left
            (x + r, y + r),  # top-left
        }
        assert centers == expected

    @pytest.mark.parametrize(
        ("w", "h", "requested_radius", "expected_radius"),
        [
            # radius larger than half-width: clamp to w/2.
            (10.0, 40.0, 30.0, 5.0),
            # radius larger than half-height: clamp to h/2.
            (40.0, 10.0, 30.0, 5.0),
            # radius below min(w/2, h/2): passed through unchanged.
            (40.0, 40.0, 6.0, 6.0),
        ],
    )
    def test_radius_is_clamped_to_half_of_smaller_side(
        self, w: float, h: float, requested_radius: float, expected_radius: float
    ) -> None:
        cr = FakeCairo()
        draw_rounded_rect(cr, 0.0, 0.0, w, h, requested_radius)
        arc_radius = cr.arcs[0][2]
        assert math.isclose(arc_radius, expected_radius, abs_tol=1e-9)

    def test_initial_move_to_is_top_side_start(self) -> None:
        cr = FakeCairo()
        draw_rounded_rect(cr, 100.0, 200.0, 60.0, 30.0, 6.0)
        # The path starts on the top edge at (x + radius, y).
        assert cr.move_tos[0] == pytest.approx((106.0, 200.0))


# --------------------------------------------------------------------------- #
# parse_hex
# --------------------------------------------------------------------------- #


class TestParseHex:
    """`#rrggbb` colour strings come from user config and plugin defaults."""

    @pytest.mark.parametrize(
        ("hex_str", "expected"),
        [
            ("#000000", (0.0, 0.0, 0.0)),
            ("#ffffff", (1.0, 1.0, 1.0)),
            ("#ff0000", (1.0, 0.0, 0.0)),
            ("#00ff00", (0.0, 1.0, 0.0)),
            ("#0000ff", (0.0, 0.0, 1.0)),
            # Leading '#' is optional (historical callers sometimes omit it).
            ("ff3333", (1.0, 0.2, 0.2)),
        ],
    )
    def test_valid_hex_parses_channels(self, hex_str: str, expected: tuple[float, float, float]) -> None:
        r, g, b = parse_hex(hex_str)
        assert math.isclose(r, expected[0], abs_tol=1e-2)
        assert math.isclose(g, expected[1], abs_tol=1e-2)
        assert math.isclose(b, expected[2], abs_tol=1e-2)

    def test_mixed_case_hex_is_accepted(self) -> None:
        assert parse_hex("#FfAa00") == parse_hex("#ffaa00")

    @pytest.mark.parametrize("short", ["#", "#fff", "#12345", "", "12"])
    def test_too_short_returns_white(self, short: str) -> None:
        # Documented fallback: white when the string is too short to be rgb.
        assert parse_hex(short) == (1.0, 1.0, 1.0)

    @pytest.mark.parametrize("malformed", ["#zzzzzz", "#gg0011", "xyz123", "#??????"])
    def test_non_hex_digits_return_white(self, malformed: str) -> None:
        # int(..., 16) raises ValueError → we fall back to white.
        assert parse_hex(malformed) == (1.0, 1.0, 1.0)


# --------------------------------------------------------------------------- #
# speed_color
# --------------------------------------------------------------------------- #


class TestSpeedColor:
    """Maps a marker speed to a green→yellow→red gradient."""

    @pytest.mark.parametrize("speed", [0.0, -0.5, -100.0])
    def test_zero_or_negative_is_solid_green(self, speed: float) -> None:
        r, g, b = speed_color(speed)
        # Exact "stationary green" tuple from the module constant.
        assert r == pytest.approx(0.133, abs=1e-3)
        assert g == pytest.approx(0.773, abs=1e-3)
        assert b == pytest.approx(0.369, abs=1e-3)

    def test_at_max_speed_returns_red(self) -> None:
        r, g, b = speed_color(20.0, max_speed=20.0)
        assert (r, g, b) == pytest.approx((1.0, 0.0, 0.0))

    def test_above_max_is_still_clamped_to_red(self) -> None:
        r, g, b = speed_color(100.0, max_speed=20.0)
        # t is clamped to 1.0, so the result must match the max-speed result.
        assert (r, g, b) == pytest.approx(speed_color(20.0, max_speed=20.0))

    def test_at_half_is_yellow(self) -> None:
        # t == 0.5 is the green→yellow boundary; the bottom branch ends on
        # (255/255, 255/255, 0/255).
        r, g, b = speed_color(10.0, max_speed=20.0)
        assert r == pytest.approx(1.0, abs=1e-3)
        assert g == pytest.approx(1.0, abs=1e-3)
        assert b == pytest.approx(0.0, abs=1e-3)

    def test_bottom_half_is_green_to_yellow_gradient(self) -> None:
        """As speed rises from 0 to half-max, R climbs and B falls."""
        low = speed_color(2.0, max_speed=20.0)  # t = 0.1
        mid = speed_color(6.0, max_speed=20.0)  # t = 0.3
        high = speed_color(10.0, max_speed=20.0)  # t = 0.5 (yellow)
        assert low[0] < mid[0] < high[0]  # R monotonically up
        assert low[2] > mid[2] >= 0.0  # B monotonically down to zero
        assert high[1] == pytest.approx(1.0, abs=1e-3)

    def test_top_half_is_yellow_to_red_gradient(self) -> None:
        """As speed rises from half-max to max, G falls while R stays 1.0."""
        low = speed_color(12.0, max_speed=20.0)  # t = 0.6
        mid = speed_color(16.0, max_speed=20.0)  # t = 0.8
        high = speed_color(20.0, max_speed=20.0)  # t = 1.0 (red)
        assert low[0] == pytest.approx(1.0)
        assert mid[0] == pytest.approx(1.0)
        assert high[0] == pytest.approx(1.0)
        assert low[1] > mid[1] > high[1]
        assert high[1] == pytest.approx(0.0, abs=1e-3)

    def test_custom_max_speed_rescales_midpoint(self) -> None:
        """Halving ``max_speed`` halves the speed at which yellow is reached."""
        a = speed_color(5.0, max_speed=10.0)  # t = 0.5 for this max_speed
        b = speed_color(10.0, max_speed=20.0)  # t = 0.5 for that max_speed
        assert a == pytest.approx(b, abs=1e-6)
