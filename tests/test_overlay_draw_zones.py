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

from types import SimpleNamespace
from typing import Any

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


class TestLensDistortion:
    """Zone edges bow to match the lens when distortion is active, subdividing
    each straight edge; with distortion off the outline is the raw vertices."""

    def test_off_path_outline_is_raw_vertices(self) -> None:
        state = _state_with_zone(vertices=_square(s=2.0), name="")
        cr = FakeCairo()
        draw_zones(FakeRenderer(), cr, state, 1920, 1080)
        # 4-vertex square: move_to v0, line_to v1..v3, close.
        assert len(cr.line_tos) == 3
        assert cr.closes == 1

    def test_edges_subdivide_when_distortion_active(self) -> None:
        state = _state_with_zone(vertices=_square(s=2.0), name="")
        state.lens_k1 = -0.15
        state.lens_k2 = 0.04
        cr = FakeCairo()
        draw_zones(FakeRenderer(), cr, state, 1920, 1080)
        # 4 edges x 12 chords = 48 outline points: move_to + 47 line_to + close.
        assert len(cr.line_tos) == 47
        assert cr.closes == 1

    def test_subdivided_edge_behind_camera_skips_whole_zone(self, monkeypatch) -> None:
        import openfollow.runtime.overlay_draw_zones as mod

        state = _state_with_zone(vertices=_square(s=2.0), name="")
        state.lens_k1 = -0.15
        state.lens_k2 = 0.04

        def _nan_segments(_cam, segments, _w, _h, _k1, _k2, n):
            segs = np.asarray(segments, dtype=np.float64).reshape(-1, 2, 3)
            out = np.full((segs.shape[0], n + 1, 2), 100.0)
            out[0, n // 2] = np.nan  # one sub-point on the first edge behind camera
            return out

        # Vertices project finite (real call), but a subdivided edge point is NaN.
        monkeypatch.setattr(mod, "_project_segments", _nan_segments)
        cr = FakeCairo()
        draw_zones(FakeRenderer(), cr, state, 1920, 1080)
        # A closed outline can't be partially drawn -> whole zone skipped.
        assert cr.closes == 0
        assert cr.fill_preserves == 0


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
            (_square(0.0, 2.0, 1.0), "#ffaa00", "Pit", False, 0),
            (_square(-2.5, 4.0, 0.8), "#33aaff", "Wing", True, 2),
            # Smaller than its label, which then reaches past the outline.
            (_square(2.5, 3.0, 0.05), "#ff3333", "A long label on a tiny zone", False, 0),
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
            # Leading spaces put the glyphs well left of where the label's box starts.
            (1.0, {"zone_polygons": [(_square(0.0, 2.0, 0.05), "#ffaa00", "      Pit", True, 3)]}),
        ],
        ids=["screen", "hidpi", "lens", "acute-corners", "spaced-label"],
    )
    def test_cached_zones_look_like_zones_drawn_directly(
        self, scale: float, overrides: dict, background: tuple[float, ...]
    ) -> None:
        """Over a frame already drawn, so a picture pasted in place of the frame shows."""
        if "camera_params" in overrides:
            overrides = {**overrides, "zone_polygons": [(_square(0.0, 0.0, 2.0), "#ffaa00", "", False, 0)]}
        self._assert_cached_looks_direct(self._state(**overrides), scale, background)

    def _assert_cached_looks_direct(
        self,
        state: OverlayState,
        scale: float = 1.0,
        background: tuple[float, ...] = (0.2, 0.3, 0.4, 1.0),
        renderer: Any = None,
        font_options: Any = None,
    ) -> None:
        import cairo

        from openfollow.runtime.overlay_draw_zones import ZoneCache

        if font_options is None:
            # Pinned, so the host's fontconfig (subpixel order, hinting) cannot decide the result.
            font_options = cairo.FontOptions()
            font_options.set_antialias(cairo.ANTIALIAS_GRAY)
            font_options.set_hint_style(cairo.HINT_STYLE_NONE)
            font_options.set_hint_metrics(cairo.HINT_METRICS_OFF)
        surfaces = []
        for cache in (None, ZoneCache()):
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, int(1920 * scale), int(1080 * scale))
            surface.set_device_scale(scale, scale)
            cr = cairo.Context(surface)
            cr.set_font_options(font_options)
            cr.set_source_rgba(*background)
            cr.paint()
            draw_zones(renderer or FakeRenderer(), cr, state, 1920, 1080, cache)
            surfaces.append(self._pixels(surface))
        direct, cached = surfaces
        assert (direct != self._pixels(_blank(scale, background))).any(), "the scene must draw something"
        # Two levels: translucent zones blended twice round differently by a level.
        assert np.abs(direct - cached).max() <= 2

    @pytest.mark.parametrize("antialias", ["NONE", "GRAY"])
    def test_labels_in_the_picture_take_the_screens_font_options(self, antialias: str) -> None:
        import cairo

        options = cairo.FontOptions()
        options.set_antialias(getattr(cairo, f"ANTIALIAS_{antialias}"))
        options.set_hint_style(cairo.HINT_STYLE_NONE)
        options.set_hint_metrics(cairo.HINT_METRICS_OFF)
        self._assert_cached_looks_direct(self._state(), font_options=options)

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
        state = self._state(camera_params=camera, zone_polygons=[(zone, "#ffaa00", "Z", False, 0)])
        self._assert_cached_looks_direct(state)

    @pytest.mark.parametrize("roll", [0.0, 90.0], ids=["too-wide", "too-tall"])
    def test_zones_too_large_for_cairo_to_measure_still_show(self, roll: float) -> None:
        """Lens distortion throws a corner far enough out to wrap cairo's fixed point."""
        camera = _camera_params()
        camera[5] = roll
        state = self._state(
            camera_params=camera,
            lens_k1=-0.15,
            lens_k2=0.02,
            zone_polygons=[
                ([(-6.0, -5.9), (6.0, -5.9), (6.0, -2.9), (-6.0, -2.9)], "#ffaa00", "Front", False, 0),
                (_square(0.0, 3.0, 1.0), "#33aaff", "Pit", False, 0),
            ],
        )
        self._assert_cached_looks_direct(state)

    def test_a_label_measured_at_the_screens_scale_is_not_cut_off(self) -> None:
        """Hinted glyphs can measure wider at a higher device scale than at scale one."""

        class _HintedRenderer(FakeRenderer):
            @staticmethod
            def _set_ui_font(cr: Any, size: float, bold: bool = False) -> None:
                FakeRenderer._set_ui_font(cr, size * (0.9 + 0.1 * cr.get_target().get_device_scale()[0]), bold)

        state = self._state(
            zone_polygons=[(_square(0.0, 2.0, 0.05), "#ffaa00", "A long label on a tiny zone", False, 0)]
        )
        self._assert_cached_looks_direct(state, scale=2.0, renderer=_HintedRenderer())

    @pytest.mark.parametrize("scale", [1.0, 2.0], ids=["screen", "hidpi"])
    def test_the_picture_holds_the_zones_not_the_frame(self, scale: float) -> None:
        import cairo

        from openfollow.runtime.overlay_draw_zones import ZoneCache

        pictures: list[tuple[int, int]] = []

        class _Context(cairo.Context):
            def set_source_surface(self, surface: Any, x: float = 0.0, y: float = 0.0) -> None:
                pictures.append((surface.get_width(), surface.get_height()))
                super().set_source_surface(surface, x, y)

        state = self._state(zone_polygons=[(_square(-1.0, 2.0, 0.5), "#ffaa00", "Pit", False, 0)])
        clear = (0.0, 0.0, 0.0, 0.0)
        surface = _blank(scale, clear)
        surface.set_device_scale(scale, scale)
        draw_zones(FakeRenderer(), _Context(surface), state, 1920, 1080, ZoneCache())
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
            draw_zones(FakeRenderer(), cr, state, 1920, 1080, cache)
            assert ("paint",) in cr.calls
            pictures += cr.surfaces
        assert len(pictures) == 3 and all(p is pictures[0] for p in pictures)

    @pytest.mark.parametrize(
        "change",
        [
            {"zone": (0, (_square(0.2, 2.0, 1.0), "#ffaa00", "Pit", False, 0))},
            {"zone": (0, (_square(0.0, 2.0, 1.0), "#00ff00", "Pit", False, 0))},
            {"zone": (0, (_square(0.0, 2.0, 1.0), "#ffaa00", "Stage", False, 0))},
            {"zone": (0, (_square(0.0, 2.0, 1.0), "#ffaa00", "Pit", True, 0))},
            # Another occupant of the occupied Wing.
            {"zone": (1, (_square(-2.5, 4.0, 0.8), "#33aaff", "Wing", True, 3))},
            {"state": {"zone_z_offset": 0.5}},
            {"state": {"camera_params": np.array([0.2, -6.0, 2.5, -15.0, 0.0, 0.0, 60.0])}},
            {"state": {"lens_k1": 0.05}},
            {"state": {"lens_k2": 0.02}},
            {"size": (1280, 720)},
            {"scale": 2.0},
            {"antialias": "NONE"},
        ],
        ids=[
            "vertices",
            "color",
            "name",
            "occupied",
            "count",
            "z-offset",
            "camera",
            "k1",
            "k2",
            "frame",
            "scale",
            "font-options",
        ],
    )
    def test_anything_a_zone_is_drawn_from_draws_the_zones_again(self, change: dict) -> None:
        import cairo

        from openfollow.runtime.overlay_draw_zones import ZoneCache

        cache, first = ZoneCache(), FakeCairo()
        draw_zones(FakeRenderer(), first, self._state(), 1920, 1080, cache)
        state = self._state(**change.get("state", {}))
        if "zone" in change:
            index, zone = change["zone"]
            state.zone_polygons[index] = zone
        cr = FakeCairo()
        scale = change.get("scale", 1.0)
        cr.get_target = lambda: SimpleNamespace(  # type: ignore[method-assign]
            get_device_scale=lambda: (scale, scale), get_font_options=cairo.FontOptions
        )
        if "antialias" in change:
            options = cairo.FontOptions()
            options.set_antialias(getattr(cairo, f"ANTIALIAS_{change['antialias']}"))
            cr.get_font_options = lambda: options  # type: ignore[method-assign]
        w, h = change.get("size", (1920, 1080))
        draw_zones(FakeRenderer(), cr, state, w, h, cache)
        assert len(cr.surfaces) == 1 and cr.surfaces[0] is not first.surfaces[0]

    def test_zones_all_behind_the_camera_paint_nothing(self) -> None:
        from openfollow.runtime.overlay_draw_zones import ZoneCache

        state = self._state(zone_polygons=[(_square(0.0, -20.0, 1.0), "#ffaa00", "Behind", False, 0)])
        cr = FakeCairo()
        draw_zones(FakeRenderer(), cr, state, 1920, 1080, ZoneCache())
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

        state = self._state(zone_polygons=[(zone, "#ffaa00", "Z", False, 0)])
        cr = FakeCairo()
        draw_zones(FakeRenderer(), cr, state, 1920, 1080, ZoneCache())
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
        draw_zones(FakeRenderer(), before, state, 1920, 1080, cache)
        draw_zones(FakeRenderer(), FakeCairo(), self._state(**hidden), 1920, 1080, cache)
        # Shown again unchanged, the zones are drawn afresh: no picture was kept meanwhile.
        draw_zones(FakeRenderer(), after, state, 1920, 1080, cache)
        assert after.surfaces[0] is not before.surfaces[0]

    def test_a_cache_asked_to_draw_without_a_camera_draws_nothing(self) -> None:
        from openfollow.runtime.overlay_draw_zones import ZoneCache

        cr = FakeCairo()
        ZoneCache().draw(FakeRenderer(), cr, self._state(camera_params=None), 1920, 1080)
        assert cr.calls == []
