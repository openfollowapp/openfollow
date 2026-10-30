# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Paul Hermann, Michel Honold, and Vinzenz Schultz
# Copyright (C) 2026 Paul Hermann and Michel Honold
"""Tests for :mod:`openfollow.runtime.overlay_draw_zones`.

``draw_zones`` paints trigger-zone polygons onto the video overlay.
These tests drive every branch of the dispatcher – gated flags,
degenerate inputs, projection NaN fallback, occupied vs. unoccupied
styling, outline and fill – through the public
signature without touching the GStreamer / Cairo integration layer.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from openfollow.runtime.overlay_draw_scene import project
from openfollow.runtime.overlay_draw_zones import draw_zones
from openfollow.runtime.overlay_state import OverlayState
from tests._fake_cairo import FakeCairo

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
    is_occupied: bool = False,
    z_offset: float = 0.0,
    height: float = 0.0,
    show_zones: bool = True,
    with_camera: bool = True,
) -> OverlayState:
    state = OverlayState()
    if with_camera:
        state.camera_params = _camera_params()
    state.show_zones = show_zones
    state.zone_z_offset = z_offset
    state.zone_polygons = [(vertices, color, is_occupied, height)]
    return state


def _outline_start(cr: FakeCairo) -> tuple[float, float]:
    """The first corner of the filled outline: the move_to that opens the path ``close_path`` ends."""
    end = next(i for i, c in enumerate(cr.calls) if c[0] == "close_path")
    start = max(i for i, c in enumerate(cr.calls[:end]) if c[0] == "move_to")
    return cr.calls[start][1], cr.calls[start][2]


# --------------------------------------------------------------------------- #
# Gated early returns
# --------------------------------------------------------------------------- #


class TestEarlyReturns:
    """`draw_zones` must bail out silently for each gating condition."""

    def test_show_zones_false_draws_nothing(self) -> None:
        state = _state_with_zone(vertices=_square(), show_zones=False)
        cr = FakeCairo()
        draw_zones(cr, state, 1920, 1080)
        assert cr.calls == []

    def test_empty_zone_polygons_draws_nothing(self) -> None:
        state = _state_with_zone(vertices=_square())
        state.zone_polygons = []
        cr = FakeCairo()
        draw_zones(cr, state, 1920, 1080)
        assert cr.calls == []

    def test_no_camera_draws_nothing(self) -> None:
        state = _state_with_zone(vertices=_square(), with_camera=False)
        cr = FakeCairo()
        draw_zones(cr, state, 1920, 1080)
        assert cr.calls == []

    @pytest.mark.parametrize("vertex_count", [0, 1, 2])
    def test_degenerate_polygon_is_skipped(self, vertex_count: int) -> None:
        verts = [(float(i), 0.0) for i in range(vertex_count)]
        state = _state_with_zone(vertices=verts)
        cr = FakeCairo()
        draw_zones(cr, state, 1920, 1080)
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
            (bad, "#ff0000", False, 0.0),
            (good, "#00ff00", False, 0.0),
        ]
        cr = FakeCairo()
        draw_zones(cr, state, 1920, 1080)
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
        state.zone_polygons = [([(0.0, 0.0), (0.0, 0.0), (0.0, 0.0)], "#00ff00", False, 0.0)]
        draw_zones(cr, state, 1920, 1080)
        assert cr.fill_preserves == 0
        assert cr.strokes == 0

    @pytest.mark.filterwarnings("ignore:invalid value encountered in divide:RuntimeWarning")
    def test_partial_straddle_skips_whole_polygon(self) -> None:
        """A zone with only *some* vertices behind the camera is dropped whole.

        Dropping just the non-finite vertices would reconnect non-adjacent
        survivors and warp the closed outline. The whole zone must be skipped
        instead of rendered distorted.
        """
        # Camera at PSN (0, -2, 1.5) looking upstage; the pentagon's last
        # vertex sits behind the camera plane (y = -4) → projects non-finite,
        # while the other four project finite. Pre-fix this drew a 4-gon.
        cam = np.array([0.0, -2.0, 1.5, 0.0, 0.0, 0.0, 60.0], dtype=np.float64)
        pentagon = [(-1.0, 2.0), (1.0, 2.0), (1.5, 1.0), (0.0, -4.0), (-1.5, 1.0)]
        state = _state_with_zone(vertices=pentagon, is_occupied=True)
        state.camera_params = cam
        cr = FakeCairo()

        draw_zones(cr, state, 1920, 1080)

        # Nothing painted: no outline, no fill.
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
        state = _state_with_zone(vertices=straddling)
        state.camera_params = cam
        state.zone_polygons = [
            (straddling, "#ff0000", False, 0.0),
            (visible, "#00ff00", False, 0.0),
        ]
        cr = FakeCairo()

        draw_zones(cr, state, 1920, 1080)

        # Only the fully-visible zone renders.
        assert cr.fill_preserves == 1
        assert cr.strokes == 1


# --------------------------------------------------------------------------- #
# Rendering behaviour
# --------------------------------------------------------------------------- #


class TestRendering:
    def test_square_emits_closed_path_with_one_move_and_three_lines(self) -> None:
        verts = _square()  # 4 vertices
        state = _state_with_zone(vertices=verts)
        cr = FakeCairo()
        draw_zones(cr, state, 1920, 1080)

        # move_to on first vertex, line_to on the remaining three, closed path.
        assert len(cr.move_tos) == 1
        assert len(cr.line_tos) == 3
        assert cr.closes == 1
        # Paint order: fill_preserve first, then stroke after switching alpha.
        assert cr.fill_preserves == 1
        assert cr.strokes == 1

    def test_occupied_zone_uses_higher_alpha_and_thicker_stroke(self) -> None:
        occ_state = _state_with_zone(vertices=_square(), is_occupied=True)
        unocc_state = _state_with_zone(vertices=_square(), is_occupied=False)
        occ_cr = FakeCairo()
        unocc_cr = FakeCairo()
        draw_zones(occ_cr, occ_state, 1920, 1080)
        draw_zones(unocc_cr, unocc_state, 1920, 1080)

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

    @pytest.mark.parametrize("occupied", [False, True], ids=["empty", "occupied"])
    def test_a_zone_is_drawn_without_text(self, occupied: bool) -> None:
        state = _state_with_zone(vertices=_square(), is_occupied=occupied)
        cr = FakeCairo()
        draw_zones(cr, state, 1920, 1080)
        # Outline and fill only: no name, no count, no label background.
        assert cr.fill_preserves == 1
        assert cr.strokes == 1
        assert cr.texts == []
        assert cr.rects == []

    def test_polygon_color_is_parsed_from_hex_channel_string(self) -> None:
        """The zone colour flows to set_source_rgba for fill + stroke."""
        state = _state_with_zone(vertices=_square(), color="#ff0000")
        cr = FakeCairo()
        draw_zones(cr, state, 1920, 1080)

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
        flat = _state_with_zone(vertices=verts, z_offset=0.0)
        raised = _state_with_zone(vertices=verts, z_offset=2.0)

        flat_cr = FakeCairo()
        raised_cr = FakeCairo()
        draw_zones(flat_cr, flat, 1920, 1080)
        draw_zones(raised_cr, raised, 1920, 1080)

        # The projected first vertex must differ between z=0 and z=2 – this
        # is the assertion that ``zone_z_offset`` actually reaches ``project``.
        flat_first = flat_cr.move_tos[0]
        raised_first = raised_cr.move_tos[0]
        assert flat_first != raised_first

    def test_a_zone_is_drawn_at_its_height_above_the_stage_plane(self) -> None:
        """The stage plane and a zone's own height add up: a zone 0.5 m up on a stage at 1.5 m stands at 2 m."""
        verts = _square()
        raised = _state_with_zone(vertices=verts, z_offset=1.5, height=0.5)
        at_two = _state_with_zone(vertices=verts, z_offset=2.0)
        on_the_stage = _state_with_zone(vertices=verts, z_offset=1.5)
        drawn = []
        for state in (raised, at_two, on_the_stage):
            cr = FakeCairo()
            draw_zones(cr, state, 1920, 1080)
            drawn.append(_outline_start(cr))
        assert drawn[0] == pytest.approx(drawn[1])
        assert drawn[0] != pytest.approx(drawn[2])

    def test_multiple_polygons_draw_in_order(self) -> None:
        """Two polygons → two filled regions, in order."""
        state = _state_with_zone(vertices=_square(cx=-3.0))
        state.zone_polygons = [
            (_square(cx=-3.0), "#ff0000", False, 0.0),
            (_square(cx=3.0), "#00ff00", True, 0.0),
        ]
        cr = FakeCairo()
        draw_zones(cr, state, 1920, 1080)
        assert cr.fill_preserves == 2
        assert cr.strokes == 2


class TestLensDistortion:
    """Zone edges bow to match the lens when distortion is active, subdividing
    each straight edge; with distortion off the outline is the raw vertices."""

    def test_off_path_outline_is_raw_vertices(self) -> None:
        state = _state_with_zone(vertices=_square(s=2.0))
        cr = FakeCairo()
        draw_zones(cr, state, 1920, 1080)
        # 4-vertex square: move_to v0, line_to v1..v3, close.
        assert len(cr.line_tos) == 3
        assert cr.closes == 1

    def test_edges_subdivide_when_distortion_active(self) -> None:
        state = _state_with_zone(vertices=_square(s=2.0))
        state.lens_k1 = -0.15
        state.lens_k2 = 0.04
        cr = FakeCairo()
        draw_zones(cr, state, 1920, 1080)
        # 4 edges x 24 chords = 96 outline points: move_to + 95 line_to + close.
        assert len(cr.line_tos) == 95
        assert cr.closes == 1

    def test_subdivided_edge_behind_camera_skips_whole_zone(self, monkeypatch) -> None:
        import openfollow.runtime.overlay_draw_zones as mod

        state = _state_with_zone(vertices=_square(s=2.0))
        state.lens_k1 = -0.15
        state.lens_k2 = 0.04

        def _nan_segments(_cam, segments, _w, _h, _k1, _k2, _cx, _cy, n):
            segs = np.asarray(segments, dtype=np.float64).reshape(-1, 2, 3)
            out = np.full((segs.shape[0], n + 1, 2), 100.0)
            out[0, n // 2] = np.nan  # one sub-point on the first edge behind camera
            return out

        # Vertices project finite (real call), but a subdivided edge point is NaN.
        monkeypatch.setattr(mod, "_project_segments", _nan_segments)
        cr = FakeCairo()
        draw_zones(cr, state, 1920, 1080)
        # A closed outline can't be partially drawn -> whole zone skipped.
        assert cr.closes == 0
        assert cr.fill_preserves == 0


def _strokes(cr: FakeCairo) -> list[dict[str, Any]]:
    """Each stroke: its alpha, dash, whether its path was closed, and the points it ran through."""
    out: list[dict[str, Any]] = []
    alpha, dash, closed, points = 1.0, (), False, []
    for call in cr.calls:
        if call[0] == "rgba":
            alpha = call[4] if len(call) == 5 else 1.0
        elif call[0] == "dash":
            dash = call[1]
        elif call[0] in ("move_to", "line_to"):
            points.append((call[1], call[2]))
        elif call[0] == "close_path":
            closed = True
        elif call[0] == "stroke":
            out.append({"alpha": alpha, "dash": dash, "closed": closed, "points": points})
            closed, points = False, []
        elif call[0] == "fill":
            closed, points = False, []
    return out


class TestRaisedZoneFloor:
    """A zone raised above the stage plane also shows its floor outline and a dotted line up each corner."""

    @staticmethod
    def _drawn(height: float) -> list[dict[str, Any]]:
        cr = FakeCairo()
        draw_zones(cr, _state_with_zone(vertices=_square(), height=height), 1920, 1080)
        return _strokes(cr)

    def test_a_raised_zone_draws_its_floor_outline_solid_and_its_corner_lines_dotted(self) -> None:
        open_strokes = [s for s in self._drawn(0.5) if not s["closed"]]
        assert {s["dash"] for s in open_strokes} == {(), (2.0, 4.0)}

    @pytest.mark.parametrize("height", [0.0, -0.5], ids=["on the stage", "below it"])
    def test_a_zone_not_above_the_stage_draws_only_its_outline(self, height: float) -> None:
        drawn = self._drawn(height)
        assert len(drawn) == 1 and drawn[0]["closed"] is True

    @pytest.mark.parametrize(
        ("world", "alpha", "dash"),
        [
            ((0.0, -1.0, 0.0), 0.7, ()),
            ((0.0, 1.0, 0.0), 0.18, ()),
            ((1.0, -1.0, 0.25), 0.7, (2.0, 4.0)),
            ((1.0, 1.0, 0.25), 0.18, (2.0, 4.0)),
        ],
        ids=["near floor edge", "far floor edge", "near corner line", "far corner line"],
    )
    def test_lines_behind_the_raised_zone_are_drawn_fainter(
        self, world: tuple[float, float, float], alpha: float, dash: tuple[float, ...]
    ) -> None:
        """The camera stands downstage of the zone and above it: its upstage lines run behind the zone."""
        spot = project(_camera_params(), [world], 1920, 1080)[0]
        holding = [
            s
            for s in self._drawn(0.5)
            if not s["closed"] and any(abs(x - spot[0]) < 1e-6 and abs(y - spot[1]) < 1e-6 for x, y in s["points"])
        ]
        assert [(s["alpha"], s["dash"]) for s in holding] == [(pytest.approx(alpha), dash)]

    def test_a_camera_straight_above_the_zone_sees_its_floor_lines_only_through_it(self) -> None:
        state = _state_with_zone(vertices=_square(), height=0.5)
        state.camera_params = np.array([0.0, 0.0, 6.0, -90.0, 0.0, 0.0, 60.0])
        cr = FakeCairo()
        draw_zones(cr, state, 1920, 1080)
        floor = [s for s in _strokes(cr) if not s["closed"]]
        assert floor and all(s["alpha"] == pytest.approx(0.18) for s in floor)

    @pytest.mark.parametrize(("x", "alpha"), [(-0.5, 0.7), (0.5, 0.18)], ids=["in view", "behind the arm"])
    def test_a_line_is_cut_where_the_zone_starts_to_hide_it(self, x: float, alpha: float) -> None:
        """A U open towards a camera downstage right: the right arm hides the right half of the notch's back edge."""
        notched = [
            (-2.0, -1.0),
            (-1.0, -1.0),
            (-1.0, 0.0),
            (1.0, 0.0),
            (1.0, -1.0),
            (2.0, -1.0),
            (2.0, 1.0),
            (-2.0, 1.0),
        ]
        state = _state_with_zone(vertices=notched, height=0.5)
        state.camera_params = np.array([6.0, -6.0, 2.5, -15.0, 0.0, 0.0, 60.0])
        cr = FakeCairo()
        draw_zones(cr, state, 1920, 1080)
        spot = project(state.camera_params, [(x, 0.0, 0.0)], 1920, 1080)[0]
        holding = [
            s["alpha"]
            for s in _strokes(cr)
            if not s["closed"] and any(abs(px - spot[0]) < 1e-6 and abs(py - spot[1]) < 1e-6 for px, py in s["points"])
        ]
        assert holding == [pytest.approx(alpha)]


# --------------------------------------------------------------------------- #
# ZoneCache – every zone as one picture, drawn again only on a change
# --------------------------------------------------------------------------- #


def _blank(scale: float, background: tuple[float, ...]) -> Any:
    import cairo

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, int(1920 * scale), int(1080 * scale))
    cr = cairo.Context(surface)
    cr.set_source_rgba(*background)
    cr.paint()
    return surface


class TestZoneCache:
    @staticmethod
    def _pixels(surface: Any) -> np.ndarray:
        surface.flush()
        h, stride = surface.get_height(), surface.get_stride()
        return np.ndarray((h, stride // 4, 4), np.uint8, surface.get_data())[:, : surface.get_width()].astype(int)

    @staticmethod
    def _state(**overrides: Any) -> OverlayState:
        state = OverlayState()
        state.camera_params = _camera_params()
        state.show_zones = True
        state.zone_polygons = [
            (_square(0.0, 2.0, 1.0), "#ffaa00", False, 0.0),
            (_square(-2.5, 4.0, 0.8), "#33aaff", True, 0.0),
            # So small its outline's miter tips reach past its corners.
            (_square(2.5, 3.0, 0.05), "#ff3333", False, 0.0),
        ]
        for name, value in overrides.items():
            setattr(state, name, value)
        return state

    @pytest.mark.parametrize("background", [(0.2, 0.3, 0.4, 1.0), (0.2, 0.3, 0.4, 0.5)], ids=["opaque", "translucent"])
    @pytest.mark.parametrize(
        ("scale", "overrides"),
        [
            (1.0, {}),
            (2.0, {}),
            (1.0, {"lens_k1": -0.15, "lens_k2": 0.02}),
            # Seen from low down, the corners turn sharp enough for a miter tip to reach well past them.
            (1.0, {"camera_params": np.array([0.0, -10.0, 0.6, -2.0, 0.0, 0.0, 60.0])}),
        ],
        ids=["screen", "hidpi", "lens", "acute-corners"],
    )
    def test_cached_zones_look_like_zones_drawn_directly(
        self, scale: float, overrides: dict, background: tuple[float, ...]
    ) -> None:
        """Over a frame already drawn, so a picture pasted in place of the frame shows."""
        if "camera_params" in overrides:
            overrides = {**overrides, "zone_polygons": [(_square(0.0, 0.0, 2.0), "#ffaa00", False, 0.0)]}
        self._assert_cached_looks_direct(self._state(**overrides), scale, background)

    def _assert_cached_looks_direct(
        self,
        state: OverlayState,
        scale: float = 1.0,
        background: tuple[float, ...] = (0.2, 0.3, 0.4, 1.0),
    ) -> None:
        import cairo

        from openfollow.runtime.overlay_draw_zones import ZoneCache

        surfaces = []
        for cache in (None, ZoneCache()):
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, int(1920 * scale), int(1080 * scale))
            surface.set_device_scale(scale, scale)
            cr = cairo.Context(surface)
            cr.set_source_rgba(*background)
            cr.paint()
            draw_zones(cr, state, 1920, 1080, cache)
            surfaces.append(self._pixels(surface))
        direct, cached = surfaces
        assert (direct != self._pixels(_blank(scale, background))).any(), "the scene must draw something"
        # Two levels: translucent zones blended twice round differently by a level.
        assert np.abs(direct - cached).max() <= 2

    @pytest.mark.parametrize(
        "roll",
        [0.0, 90.0, -90.0, 180.0],
        ids=["past-the-bottom", "past-the-left", "past-the-right", "past-the-top"],
    )
    def test_a_zone_running_far_past_an_edge_looks_like_one_drawn_directly(self, roll: float) -> None:
        """Further past the edge than a picture can be wide, so only the part on screen fits."""
        camera = _camera_params()
        camera[5] = roll
        # Its near end is almost level with the lens, which puts it tens of thousands of pixels out.
        zone = [(-0.05, -6.6), (0.05, -6.6), (0.05, 2.0), (-0.05, 2.0)]
        state = self._state(camera_params=camera, zone_polygons=[(zone, "#ffaa00", False, 0.0)])
        self._assert_cached_looks_direct(state)

    @pytest.mark.parametrize("roll", [0.0, 90.0], ids=["too-wide", "too-tall"])
    def test_zones_too_large_for_cairo_to_measure_still_show(self, roll: float) -> None:
        """A corner a tenth of a millimetre in front of the lens lands far enough out to wrap cairo's fixed point."""
        camera = _camera_params()
        camera[5] = roll
        state = self._state(
            camera_params=camera,
            zone_polygons=[
                ([(-6.0, -6.6698), (6.0, -6.6698), (6.0, -2.9), (-6.0, -2.9)], "#ffaa00", False, 0.0),
                (_square(0.0, 3.0, 1.0), "#33aaff", False, 0.0),
            ],
        )
        self._assert_cached_looks_direct(state)

    @pytest.mark.parametrize("scale", [1.0, 2.0], ids=["screen", "hidpi"])
    def test_the_picture_holds_the_zones_not_the_frame(self, scale: float) -> None:
        import cairo

        from openfollow.runtime.overlay_draw_zones import ZoneCache

        pictures: list[tuple[int, int]] = []

        class _Context(cairo.Context):
            def set_source_surface(self, surface: Any, x: float = 0.0, y: float = 0.0) -> None:
                pictures.append((surface.get_width(), surface.get_height()))
                super().set_source_surface(surface, x, y)

        state = self._state(zone_polygons=[(_square(-1.0, 2.0, 0.5), "#ffaa00", False, 0.0)])
        clear = (0.0, 0.0, 0.0, 0.0)
        surface = _blank(scale, clear)
        surface.set_device_scale(scale, scale)
        draw_zones(_Context(surface), state, 1920, 1080, ZoneCache())
        ys, xs = np.nonzero((self._pixels(surface) != self._pixels(_blank(scale, clear))).any(axis=2))
        ink = (int(xs.max() - xs.min()) + 1, int(ys.max() - ys.min()) + 1)
        # The ink, plus the margin of a pixel on each side.
        assert pictures == [(pytest.approx(ink[0], abs=4 * scale), pytest.approx(ink[1], abs=4 * scale))]

    def test_zones_that_did_not_change_are_not_drawn_again(self) -> None:
        from openfollow.runtime.overlay_draw_zones import ZoneCache

        cache, state = ZoneCache(), self._state()
        pictures = []
        for _ in range(3):
            cr = FakeCairo()
            draw_zones(cr, state, 1920, 1080, cache)
            assert ("paint",) in cr.calls
            pictures += cr.surfaces
        assert len(pictures) == 3 and all(p is pictures[0] for p in pictures)

    @pytest.mark.parametrize(
        "change",
        [
            {"zone": (0, (_square(0.2, 2.0, 1.0), "#ffaa00", False, 0.0))},
            {"zone": (0, (_square(0.0, 2.0, 1.0), "#00ff00", False, 0.0))},
            {"zone": (0, (_square(0.0, 2.0, 1.0), "#ffaa00", True, 0.0))},
            {"zone": (0, (_square(0.0, 2.0, 1.0), "#ffaa00", False, 0.4))},
            {"state": {"zone_z_offset": 0.5}},
            {"state": {"camera_params": np.array([0.2, -6.0, 2.5, -15.0, 0.0, 0.0, 60.0])}},
            {"state": {"lens_k1": 0.05}},
            {"state": {"lens_k2": 0.02}},
            {"state": {"lens_cx": 0.03}},
            {"state": {"lens_cy": -0.02}},
            {"size": (1280, 1080)},
            {"size": (1920, 720)},
            {"scale": 2.0},
        ],
        ids=[
            "vertices",
            "color",
            "occupied",
            "zone-height",
            "z-offset",
            "camera",
            "k1",
            "k2",
            "cx",
            "cy",
            "width",
            "height",
            "scale",
        ],
    )
    def test_anything_a_zone_is_drawn_from_draws_the_zones_again(self, change: dict) -> None:
        import cairo

        from openfollow.runtime.overlay_draw_zones import ZoneCache

        cache, first = ZoneCache(), FakeCairo()
        draw_zones(first, self._state(), 1920, 1080, cache)
        state = self._state(**change.get("state", {}))
        if "zone" in change:
            index, zone = change["zone"]
            state.zone_polygons[index] = zone
        cr = FakeCairo()
        scale = change.get("scale", 1.0)
        cr.get_target = lambda: SimpleNamespace(  # type: ignore[method-assign]
            get_device_scale=lambda: (scale, scale), get_font_options=cairo.FontOptions
        )
        w, h = change.get("size", (1920, 1080))
        draw_zones(cr, state, w, h, cache)
        assert len(cr.surfaces) == 1 and cr.surfaces[0] is not first.surfaces[0]

    @pytest.mark.parametrize("option", ["antialias", "hint_style"])
    def test_a_font_option_change_alone_keeps_the_picture(self, option: str) -> None:
        """Zones carry no text, so the screen's font options never change their picture."""
        import cairo

        from openfollow.runtime.overlay_draw_zones import ZoneCache

        cache, state, before, after = ZoneCache(), self._state(), FakeCairo(), FakeCairo()
        draw_zones(before, state, 1920, 1080, cache)
        options = cairo.FontOptions()
        if option == "antialias":
            options.set_antialias(cairo.ANTIALIAS_SUBPIXEL)
        else:
            options.set_hint_style(cairo.HINT_STYLE_FULL)
        after.get_font_options = lambda: options  # type: ignore[method-assign]
        draw_zones(after, state, 1920, 1080, cache)
        assert after.surfaces[0] is before.surfaces[0]

    def test_zones_all_behind_the_camera_paint_nothing(self) -> None:
        from openfollow.runtime.overlay_draw_zones import ZoneCache

        state = self._state(zone_polygons=[(_square(0.0, -20.0, 1.0), "#ffaa00", False, 0.0)])
        cr = FakeCairo()
        draw_zones(cr, state, 1920, 1080, ZoneCache())
        assert ("paint",) not in cr.calls

    @pytest.mark.parametrize(
        "zone",
        [
            _square(60.0, 2.0, 1.0),
            # Close to the camera: below the frame and far wider than it.
            [(-30.0, -5.5), (30.0, -5.5), (30.0, -5.0), (-30.0, -5.0)],
        ],
        ids=["right-of-the-frame", "below-the-frame"],
    )
    def test_zones_entirely_outside_the_frame_paint_nothing(self, zone: list) -> None:
        from openfollow.runtime.overlay_draw_zones import ZoneCache

        state = self._state(zone_polygons=[(zone, "#ffaa00", False, 0.0)])
        cr = FakeCairo()
        draw_zones(cr, state, 1920, 1080, ZoneCache())
        assert ("paint",) not in cr.calls

    @pytest.mark.parametrize(
        "hidden",
        [{"show_zones": False}, {"zone_polygons": []}, {"camera_params": None}],
        ids=["overlay-off", "no-zones", "no-camera"],
    )
    def test_the_picture_is_let_go_while_no_zone_is_drawn(self, hidden: dict) -> None:
        from openfollow.runtime.overlay_draw_zones import ZoneCache

        cache, state = ZoneCache(), self._state()
        before, after = FakeCairo(), FakeCairo()
        draw_zones(before, state, 1920, 1080, cache)
        draw_zones(FakeCairo(), self._state(**hidden), 1920, 1080, cache)
        # Shown again unchanged, the zones are drawn afresh: no picture was kept meanwhile.
        draw_zones(after, state, 1920, 1080, cache)
        assert after.surfaces[0] is not before.surfaces[0]

    def test_a_cache_asked_to_draw_without_a_camera_draws_nothing(self) -> None:
        from openfollow.runtime.overlay_draw_zones import ZoneCache

        cr = FakeCairo()
        ZoneCache().draw(cr, self._state(camera_params=None), 1920, 1080)
        assert cr.calls == []
