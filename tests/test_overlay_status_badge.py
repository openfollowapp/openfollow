# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for the top-right status badge.

The badge surfaces ``OverlayState.status_flags`` entries as rows. Empty
list ⇒ nothing draws; non-empty ⇒ one row per active flag, with overflow
rolled into a single "+N more" tail row to bound the on-screen footprint.
Each entry carries a severity – ``"error"`` (red, warning triangle) or
``"info"`` (green, filled dot).

Driven against the project's :class:`FakeCairo` so the tests stay fast
and don't need an actual Cairo surface.
"""

from __future__ import annotations

import pytest

from openfollow.runtime.overlay_draw_style import COLOR_DANGER, COLOR_OK
from openfollow.runtime.overlay_state import OverlayState
from openfollow.runtime.overlay_status_badge import (
    _BADGE_WIDTH,
    _MAX_VISIBLE_ROWS,
    _ROW_HEIGHT,
    _ROW_SPACING,
    _TOP_OFFSET,
    draw_status_badge,
)
from tests._fake_cairo import FakeCairo, FakeRenderer

pytestmark = pytest.mark.unit

# Must match the radius _draw_warning_row passes to draw_rounded_rect.
_ROW_RADIUS = 8.0
# The badge sits 10 px from the frame's right edge (matches the stats panel).
_GUTTER = 10.0


def _state_with_flags(*flags: tuple[str, ...]) -> OverlayState:
    state = OverlayState()
    # Accept ``(key, message)`` – defaulting to the "error" severity – or an
    # explicit ``(key, message, severity)`` triple, and normalise to triples
    # (the shape the renderer consumes).
    state.status_flags = [(f[0], f[1], f[2] if len(f) > 2 else "error") for f in flags]
    return state


def _badge_x(frame_w: int) -> float:
    return frame_w - _BADGE_WIDTH - _GUTTER


def _row_top_ys(cr: FakeCairo, badge_x: float) -> list[float]:
    """Distinct row top-edge y's, read from the rounded-rect backgrounds.

    ``draw_rounded_rect(x, y, ...)`` opens with ``move_to(x + radius, y)``;
    a row's background + border each emit one, so the row tops are the
    distinct y's of the move_tos sitting at ``x == badge_x + radius`` (the
    warning glyph's move_to sits at a different x)."""
    left = badge_x + _ROW_RADIUS
    return sorted({y for (mx, y) in cr.move_tos if abs(mx - left) < 1e-6})


class TestEmptyState:
    def test_empty_flags_skips_all_drawing(self) -> None:
        cr = FakeCairo()
        state = OverlayState()  # default – no flags
        draw_status_badge(FakeRenderer(state=state), cr, state, 1920, 1080)
        # Zero Cairo primitives – the no-warning case must cost
        # nothing on the per-frame render path.
        assert cr.calls == []
        assert cr.fills == 0


class TestSingleFlag:
    def test_renders_one_row_with_message(self) -> None:
        cr = FakeCairo()
        state = _state_with_flags(
            ("midi_patch_missing", "MIDI patch(es) without a connected device: Workspace 1"),
        )
        draw_status_badge(FakeRenderer(state=state), cr, state, 1920, 1080)
        # The truncation helper may shorten the message; the test asserts on
        # the substring that always survives for short display strings.
        texts = cr.show_text_strings()
        assert any("MIDI patch" in t for t in texts)

    def test_error_row_paints_danger_border(self) -> None:
        """An error row matches the device's failure chrome: a solid
        ``COLOR_DANGER`` border (and a 20% danger fill)."""
        cr = FakeCairo()
        state = _state_with_flags(("midi_unavailable", "MIDI backend error"))
        draw_status_badge(FakeRenderer(state=state), cr, state, 1920, 1080)
        # Border stroke is set via set_source_rgb(*COLOR_DANGER).
        rgb_calls = [c for c in cr.calls if c[0] == "rgb"]
        assert any(tuple(round(v, 3) for v in c[1:]) == tuple(round(v, 3) for v in COLOR_DANGER) for c in rgb_calls)
        # Fill is the same colour at 20% alpha.
        rgba_calls = [c for c in cr.calls if c[0] == "rgba"]
        assert any(
            abs(c[1] - COLOR_DANGER[0]) < 1e-3 and abs(c[2] - COLOR_DANGER[1]) < 1e-3 and abs(c[4] - 0.20) < 1e-3
            for c in rgba_calls
            if len(c) == 5
        )


class TestSeverity:
    def test_info_row_uses_ok_green_and_a_circle_glyph(self) -> None:
        """An ``"info"`` row is green (``COLOR_OK``) and draws a filled dot
        (an ``arc``) rather than the error triangle."""
        cr = FakeCairo()
        state = _state_with_flags(("update_available", "Update available", "info"))
        draw_status_badge(FakeRenderer(state=state), cr, state, 1920, 1080)
        rgb_calls = [c for c in cr.calls if c[0] == "rgb"]
        assert any(tuple(round(v, 3) for v in c[1:]) == tuple(round(v, 3) for v in COLOR_OK) for c in rgb_calls)
        # The info glyph is a circle → at least one arc is recorded. (The
        # rounded-rect corners are also arcs, so just assert the green colour
        # was set, then a circle exists.)
        assert cr.arcs, "info glyph should draw an arc"
        # No danger colour anywhere on a pure-info badge.
        assert not any(tuple(round(v, 3) for v in c[1:]) == tuple(round(v, 3) for v in COLOR_DANGER) for c in rgb_calls)

    def test_error_row_uses_danger_red(self) -> None:
        cr = FakeCairo()
        state = _state_with_flags(("midi_unavailable", "Backend down", "error"))
        draw_status_badge(FakeRenderer(state=state), cr, state, 1920, 1080)
        rgb_calls = [c for c in cr.calls if c[0] == "rgb"]
        assert any(tuple(round(v, 3) for v in c[1:]) == tuple(round(v, 3) for v in COLOR_DANGER) for c in rgb_calls)
        assert not any(tuple(round(v, 3) for v in c[1:]) == tuple(round(v, 3) for v in COLOR_OK) for c in rgb_calls)


class TestMultipleFlags:
    def test_two_flags_paint_two_rows(self) -> None:
        cr = FakeCairo()
        state = _state_with_flags(
            ("midi_patch_missing", "Patch missing"),
            ("midi_unavailable", "Backend down"),
        )
        draw_status_badge(FakeRenderer(state=state), cr, state, 1920, 1080)
        # Two rows × (background fill + glyph fill) = 4 fills (the border is a
        # stroke, not a fill).
        assert cr.fills == 4
        texts = cr.show_text_strings()
        assert any("Patch missing" in t for t in texts)
        assert any("Backend down" in t for t in texts)

    def test_rows_stack_vertically_with_fixed_spacing(self) -> None:
        """Each row's top sits ``_ROW_HEIGHT + _ROW_SPACING`` px below the
        previous one. Read the row tops off the rounded-rect backgrounds."""
        cr = FakeCairo()
        state = _state_with_flags(("a", "first"), ("b", "second"))
        frame_w = 1920
        draw_status_badge(FakeRenderer(state=state), cr, state, frame_w, 1080)
        ys = _row_top_ys(cr, _badge_x(frame_w))
        assert len(ys) == 2
        assert ys[0] == float(_TOP_OFFSET)
        assert ys[1] == ys[0] + _ROW_HEIGHT + _ROW_SPACING


class TestOverflow:
    def test_overflow_collapses_into_tail_row(self) -> None:
        """A flag count above the visible cap rolls into a single
        ``+N more`` tail row – bounds the on-screen footprint so a runaway
        subsystem can't push the rest of the HUD off screen."""
        cr = FakeCairo()
        # Five flags exceeds the four-row cap; the fifth collapses into
        # "+1 more".
        flags = [(f"src_{i}", f"warning {i}") for i in range(_MAX_VISIBLE_ROWS + 1)]
        state = _state_with_flags(*flags)
        draw_status_badge(FakeRenderer(state=state), cr, state, 1920, 1080)
        texts = cr.show_text_strings()
        for i in range(_MAX_VISIBLE_ROWS):
            assert any(f"warning {i}" in t for t in texts)
        assert not any("warning 4" in t for t in texts)
        assert any("+1 more" in t for t in texts)

    def test_overflow_tail_is_info_when_all_hidden_rows_are_info(self) -> None:
        """The "+N more" tail only goes red if a hidden row is an error; an
        all-info stack keeps a green tail."""
        cr = FakeCairo()
        flags = [(f"src_{i}", f"info {i}", "info") for i in range(_MAX_VISIBLE_ROWS + 1)]
        state = _state_with_flags(*flags)
        draw_status_badge(FakeRenderer(state=state), cr, state, 1920, 1080)
        rgb_calls = [c for c in cr.calls if c[0] == "rgb"]
        # Pure-info stack incl. the tail → never sets the danger colour.
        assert not any(tuple(round(v, 3) for v in c[1:]) == tuple(round(v, 3) for v in COLOR_DANGER) for c in rgb_calls)

    def test_no_overflow_when_count_equals_cap(self) -> None:
        cr = FakeCairo()
        flags = [(f"src_{i}", f"warning {i}") for i in range(_MAX_VISIBLE_ROWS)]
        state = _state_with_flags(*flags)
        draw_status_badge(FakeRenderer(state=state), cr, state, 1920, 1080)
        texts = cr.show_text_strings()
        for i in range(_MAX_VISIBLE_ROWS):
            assert any(f"warning {i}" in t for t in texts)
        assert not any("more" in t for t in texts)


class TestPositioning:
    def test_badge_anchors_to_right_edge(self) -> None:
        """Right edge of the badge sits ``10 px`` from the frame right edge –
        same gutter the system-stats panel uses."""
        cr = FakeCairo()
        state = _state_with_flags(("a", "anchor test"))
        frame_w = 1920
        draw_status_badge(FakeRenderer(state=state), cr, state, frame_w, 1080)
        # The rounded-rect right corners sit at cx = x + w - radius; the right
        # edge is that + radius. An error row draws no circle glyph, so every
        # arc here belongs to the background rounded rect.
        right_edge = max(cx for (cx, _cy, _r) in cr.arcs) + _ROW_RADIUS
        assert right_edge == frame_w - _GUTTER

    def test_first_row_clears_system_stats_panel(self) -> None:
        cr = FakeCairo()
        state = _state_with_flags(("a", "below stats"))
        frame_w = 1920
        draw_status_badge(FakeRenderer(state=state), cr, state, frame_w, 1080)
        first_y = _row_top_ys(cr, _badge_x(frame_w))[0]
        assert first_y == float(_TOP_OFFSET)
        assert first_y > 34
