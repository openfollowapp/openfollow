# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for :mod:`openfollow.runtime.overlay_draw_zones`.

``draw_zones`` paints trigger-zone polygons onto the video overlay.
These tests drive every branch of the dispatcher – gated flags,
degenerate inputs, projection NaN fallback, occupied vs. unoccupied
styling, labelled vs. unlabelled polygons – through the public
signature without touching the GStreamer / Cairo integration layer.
"""

from __future__ import annotations

import numpy as np
import pytest

from openfollow.runtime.overlay_draw_zones import draw_zones
from openfollow.runtime.overlay_state import OverlayState
from tests._fake_cairo import FakeCairo, FakeRenderer

pytestmark = pytest.mark.unit

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


def _camera_params() -> np.ndarray:
    # Looking down-stage from a typical FOH position.
    return np.array([0.0, -6.0, 2.5, -15.0, 0.0, 0.0, 60.0], dtype=np.float64)


def _square(cx: float = 0.0, cy: float = 0.0, s: float = 1.0) -> list[tuple[float, float]]:
    """Return a closed square (4 vertices) in world XY."""
    return [(cx - s, cy - s), (cx + s, cy - s), (cx + s, cy + s), (cx - s, cy + s)]


def _state_with_zone(
    *,
    vertices: list[tuple[float, float]],
    color: str = "#ffaa00",
    name: str = "Pit",
    is_occupied: bool = False,
    count: int = 0,
    z_offset: float = 0.0,
    show_zones: bool = True,
    with_camera: bool = True,
) -> OverlayState:
    state = OverlayState()
    if with_camera:
        state.camera_params = _camera_params()
    state.show_zones = show_zones
    state.zone_z_offset = z_offset
    state.zone_polygons = [(vertices, color, name, is_occupied, count)]
    return state


# --------------------------------------------------------------------------- #
# Gated early returns
# --------------------------------------------------------------------------- #


class TestEarlyReturns:
    """`draw_zones` must bail out silently for each gating condition."""

    def test_show_zones_false_draws_nothing(self) -> None:
        state = _state_with_zone(vertices=_square(), show_zones=False)
        cr = FakeCairo()
        draw_zones(FakeRenderer(), cr, state, 1920, 1080)
        assert cr.calls == []

    def test_empty_zone_polygons_draws_nothing(self) -> None:
        state = _state_with_zone(vertices=_square())
        state.zone_polygons = []
        cr = FakeCairo()
        draw_zones(FakeRenderer(), cr, state, 1920, 1080)
        assert cr.calls == []

    def test_no_camera_draws_nothing(self) -> None:
        state = _state_with_zone(vertices=_square(), with_camera=False)
        cr = FakeCairo()
        draw_zones(FakeRenderer(), cr, state, 1920, 1080)
        assert cr.calls == []

    @pytest.mark.parametrize("vertex_count", [0, 1, 2])
    def test_degenerate_polygon_is_skipped(self, vertex_count: int) -> None:
        verts = [(float(i), 0.0) for i in range(vertex_count)]
        state = _state_with_zone(vertices=verts)
        cr = FakeCairo()
        draw_zones(FakeRenderer(), cr, state, 1920, 1080)
        # Nothing should have been drawn – but no exception either.
        assert cr.fills == 0
        assert cr.strokes == 0
        assert cr.texts == []

    def test_degenerate_polygon_does_not_block_later_polygons(self) -> None:
        """A bad polygon is skipped; the next one in the list still renders."""
        bad = [(0.0, 0.0), (1.0, 1.0)]  # only 2 verts
        good = _square()
        state = _state_with_zone(vertices=bad)
        state.zone_polygons = [
            (bad, "#ff0000", "", False, 0),
            (good, "#00ff00", "", False, 0),
        ]
        cr = FakeCairo()
        draw_zones(FakeRenderer(), cr, state, 1920, 1080)
        # Exactly one polygon drawn: the good one.  fill_preserve + stroke.
        assert cr.fill_preserves == 1
        assert cr.strokes == 1

    @pytest.mark.filterwarnings("ignore:invalid value encountered in divide:RuntimeWarning")
    def test_nonfinite_projection_skips_polygon(self) -> None:
        """If any vertex projects to NaN/Inf, the whole polygon is skipped."""
        verts = _square()
        state = _state_with_zone(vertices=verts)
        cr = FakeCairo()

        # Force project_points → NaN by pointing the camera straight at a
        # point that coincides with the camera position.
        state.camera_params = np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 60.0], dtype=np.float64)
        state.zone_polygons = [([(0.0, 0.0), (0.0, 0.0), (0.0, 0.0)], "#00ff00", "", False, 0)]
        draw_zones(FakeRenderer(), cr, state, 1920, 1080)
        assert cr.fill_preserves == 0
        assert cr.strokes == 0

    @pytest.mark.filterwarnings("ignore:invalid value encountered in divide:RuntimeWarning")
    def test_partial_straddle_skips_whole_polygon(self) -> None:
        """A zone with only *some* vertices behind the camera is dropped whole.

        Dropping just the non-finite vertices would reconnect non-adjacent
        survivors and warp the closed outline (and mis-place the centroid
        label). The whole zone must be skipped instead of rendered distorted.
        """
        # Camera at PSN (0, -2, 1.5) looking upstage; the pentagon's last
        # vertex sits behind the camera plane (y = -4) → projects non-finite,
        # while the other four project finite. Pre-fix this drew a 4-gon.
        cam = np.array([0.0, -2.0, 1.5, 0.0, 0.0, 0.0, 60.0], dtype=np.float64)
        pentagon = [(-1.0, 2.0), (1.0, 2.0), (1.5, 1.0), (0.0, -4.0), (-1.5, 1.0)]
        state = _state_with_zone(vertices=pentagon, name="Pit", is_occupied=True, count=2)
        state.camera_params = cam
        cr = FakeCairo()

        draw_zones(FakeRenderer(), cr, state, 1920, 1080)

        # Nothing painted: no outline, no fill, no centroid label.
        assert cr.fill_preserves == 0
        assert cr.strokes == 0
        assert cr.move_tos == []
        assert cr.texts == []

    @pytest.mark.filterwarnings("ignore:invalid value encountered in divide:RuntimeWarning")
    def test_partial_straddle_does_not_block_later_polygons(self) -> None:
        """A straddling zone is skipped; a fully-visible later zone still draws."""
        cam = np.array([0.0, -2.0, 1.5, 0.0, 0.0, 0.0, 60.0], dtype=np.float64)
        straddling = [(-1.0, 2.0), (1.0, 2.0), (1.5, 1.0), (0.0, -4.0), (-1.5, 1.0)]
        visible = _square(cy=3.0)
        state = _state_with_zone(vertices=straddling, name="")
        state.camera_params = cam
        state.zone_polygons = [
            (straddling, "#ff0000", "Behind", False, 0),
            (visible, "#00ff00", "Front", False, 0),
        ]
        cr = FakeCairo()

        draw_zones(FakeRenderer(), cr, state, 1920, 1080)

        # Only the fully-visible zone renders.
        assert cr.fill_preserves == 1
        assert cr.strokes == 1
        assert [t.text for t in cr.texts] == ["Front"]


# --------------------------------------------------------------------------- #
# Rendering behaviour
# --------------------------------------------------------------------------- #


class TestRendering:
    def test_square_emits_closed_path_with_one_move_and_three_lines(self) -> None:
        verts = _square()  # 4 vertices
        state = _state_with_zone(vertices=verts, name="")
        cr = FakeCairo()
        draw_zones(FakeRenderer(), cr, state, 1920, 1080)

        # move_to on first vertex, line_to on the remaining three, closed path.
        assert len(cr.move_tos) == 1
        assert len(cr.line_tos) == 3
        assert cr.closes == 1
        # Paint order: fill_preserve first, then stroke after switching alpha.
        assert cr.fill_preserves == 1
        assert cr.strokes == 1

    def test_occupied_zone_uses_higher_alpha_and_thicker_stroke(self) -> None:
        occ_state = _state_with_zone(vertices=_square(), is_occupied=True, count=3, name="")
        unocc_state = _state_with_zone(vertices=_square(), is_occupied=False, name="")
        occ_cr = FakeCairo()
        unocc_cr = FakeCairo()
        draw_zones(FakeRenderer(), occ_cr, occ_state, 1920, 1080)
        draw_zones(FakeRenderer(), unocc_cr, unocc_state, 1920, 1080)

        # Fill alpha: 0.35 occupied vs 0.15 unoccupied → first rgba call of each.
        occ_fill_alpha = next(c[4] for c in occ_cr.calls if c[0] == "rgba")
        unocc_fill_alpha = next(c[4] for c in unocc_cr.calls if c[0] == "rgba")
        assert occ_fill_alpha == pytest.approx(0.35)
        assert unocc_fill_alpha == pytest.approx(0.15)

        # Stroke widths documented in the module: 2.0 vs 1.5.
        occ_stroke_widths = [c[1] for c in occ_cr.calls if c[0] == "line_width"]
        unocc_stroke_widths = [c[1] for c in unocc_cr.calls if c[0] == "line_width"]
        assert occ_stroke_widths == [2.0]
        assert unocc_stroke_widths == [1.5]

    def test_named_zone_renders_label_background_and_text(self) -> None:
        state = _state_with_zone(vertices=_square(), name="Pit", is_occupied=False)
        cr = FakeCairo()
        draw_zones(FakeRenderer(), cr, state, 1920, 1080)

        labels = [t.text for t in cr.texts]
        assert labels == ["Pit"]
        # One rectangle for the label background; the polygon itself draws no
        # rectangle (it uses move_to + line_to + close_path).
        assert len(cr.rects) == 1

    def test_occupied_label_shows_count(self) -> None:
        state = _state_with_zone(vertices=_square(), name="Pit", is_occupied=True, count=4)
        cr = FakeCairo()
        draw_zones(FakeRenderer(), cr, state, 1920, 1080)
        assert [t.text for t in cr.texts] == ["Pit (4)"]

    def test_unoccupied_label_omits_count(self) -> None:
        state = _state_with_zone(vertices=_square(), name="Pit", is_occupied=False, count=99)
        cr = FakeCairo()
        draw_zones(FakeRenderer(), cr, state, 1920, 1080)
        # The count is only shown when occupied; unoccupied uses bare name.
        assert [t.text for t in cr.texts] == ["Pit"]

    def test_occupied_label_uses_bold_font(self) -> None:
        state = _state_with_zone(vertices=_square(), name="Pit", is_occupied=True)
        cr = FakeCairo()
        draw_zones(FakeRenderer(), cr, state, 1920, 1080)
        assert cr.texts[0].bold is True

    def test_unoccupied_label_uses_normal_font(self) -> None:
        state = _state_with_zone(vertices=_square(), name="Pit", is_occupied=False)
        cr = FakeCairo()
        draw_zones(FakeRenderer(), cr, state, 1920, 1080)
        assert cr.texts[0].bold is False

    def test_empty_name_suppresses_label_but_keeps_polygon(self) -> None:
        state = _state_with_zone(vertices=_square(), name="")
        cr = FakeCairo()
        draw_zones(FakeRenderer(), cr, state, 1920, 1080)
        # Polygon still drawn (fill + stroke); no text, no label rectangle.
        assert cr.fill_preserves == 1
        assert cr.strokes == 1
        assert cr.texts == []
        assert cr.rects == []

    def test_polygon_color_is_parsed_from_hex_channel_string(self) -> None:
        """The zone colour flows to set_source_rgba for fill + stroke."""
        state = _state_with_zone(vertices=_square(), color="#ff0000", name="")
        cr = FakeCairo()
        draw_zones(FakeRenderer(), cr, state, 1920, 1080)

        rgba_calls = [c for c in cr.calls if c[0] == "rgba"]
        # First call is the fill (red with 0.15 alpha), second is the stroke
        # (red with 0.7 alpha).  Green and blue channels must stay at 0.
        r_fill, g_fill, b_fill, a_fill = rgba_calls[0][1:]
        r_stroke, g_stroke, b_stroke, a_stroke = rgba_calls[1][1:]
        assert (r_fill, g_fill, b_fill) == pytest.approx((1.0, 0.0, 0.0))
        assert (r_stroke, g_stroke, b_stroke) == pytest.approx((1.0, 0.0, 0.0))
        assert a_fill == pytest.approx(0.15)
        assert a_stroke == pytest.approx(0.7)

    def test_z_offset_is_applied_to_every_vertex(self) -> None:
        """The polygon is projected with a single shared ``zone_z_offset``."""
        verts = _square()
        flat = _state_with_zone(vertices=verts, name="", z_offset=0.0)
        raised = _state_with_zone(vertices=verts, name="", z_offset=2.0)

        flat_cr = FakeCairo()
        raised_cr = FakeCairo()
        draw_zones(FakeRenderer(), flat_cr, flat, 1920, 1080)
        draw_zones(FakeRenderer(), raised_cr, raised, 1920, 1080)

        # The projected first vertex must differ between z=0 and z=2 – this
        # is the assertion that ``zone_z_offset`` actually reaches ``project``.
        flat_first = flat_cr.move_tos[0]
        raised_first = raised_cr.move_tos[0]
        assert flat_first != raised_first

    def test_multiple_polygons_draw_in_order(self) -> None:
        """Two labelled polygons → two filled regions + two text labels."""
        state = _state_with_zone(vertices=_square(cx=-3.0), name="")
        state.zone_polygons = [
            (_square(cx=-3.0), "#ff0000", "Left", False, 0),
            (_square(cx=3.0), "#00ff00", "Right", True, 2),
        ]
        cr = FakeCairo()
        draw_zones(FakeRenderer(), cr, state, 1920, 1080)
        assert cr.fill_preserves == 2
        assert cr.strokes == 2
        assert [t.text for t in cr.texts] == ["Left", "Right (2)"]
