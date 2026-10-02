# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for the top-right status badge.

The badge surfaces ``OverlayState.status_flags`` entries as rows. Empty
list ⇒ nothing draws; non-empty ⇒ one row per active flag, with overflow
rolled into a single "+N more" tail row to bound the on-screen footprint.
Each entry carries a status level, drawn in that level's chip colours: error
(warning red, warning sign), caution and info (the "i" sign), success (the
check).

Driven against the project's :class:`FakeCairo` so the tests stay fast
and don't need an actual Cairo surface.
"""

from __future__ import annotations

import pytest

from openfollow.runtime.overlay_draw_style import (
    COLOR_CAUTION_BG,
    COLOR_CAUTION_BORDER,
    COLOR_CAUTION_FILL,
    COLOR_DANGER_BG,
    COLOR_INFO_BG,
    COLOR_INFO_BORDER,
    COLOR_INFO_FILL,
    COLOR_SUCCESS_BG,
    COLOR_SUCCESS_BORDER,
    COLOR_SUCCESS_FILL,
    COLOR_TEXT,
    COLOR_WARNING_BORDER,
    COLOR_WARNING_FILL,
    PANEL_RADIUS,
)
from openfollow.runtime.overlay_state import OverlayState
from openfollow.runtime.overlay_status_badge import (
    _BADGE_MAX_WIDTH,
    _BADGE_MIN_WIDTH,
    _MAX_VISIBLE_ROWS,
    _ROW_HEIGHT,
    _ROW_SPACING,
    _TOP_OFFSET,
    draw_status_badge,
)
from tests._fake_cairo import FakeCairo, FakeRenderer

pytestmark = pytest.mark.unit

_ROW_RADIUS = PANEL_RADIUS
# The badge sits 10 px from the frame's right edge (matches the stats panel).
_GUTTER = 10.0


def _state_with_flags(*flags: tuple[str, ...]) -> OverlayState:
    state = OverlayState()
    # Accept ``(key, message)`` – defaulting to the "error" severity – or an
    # explicit ``(key, message, severity)`` triple, and normalise to triples
    # (the shape the renderer consumes).
    state.status_flags = [(f[0], f[1], f[2] if len(f) > 2 else "error") for f in flags]
    return state


def _badge_x(cr: FakeCairo) -> float:
    """The stack's left edge, read from what was drawn.

    The badge sizes to its content, so the tests read the geometry back rather
    than recomputing the renderer's own width rule and proving nothing.
    ``draw_rounded_rect`` opens at ``x + radius``, the leftmost move_to of any
    row.
    """
    return min(mx for mx, _y in cr.move_tos) - _ROW_RADIUS


def _badge_width(cr: FakeCairo, frame_w: int) -> float:
    return frame_w - _badge_x(cr) - _GUTTER


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

    def test_an_error_row_is_the_huds_warning_red(self) -> None:
        """The same fill and border as every other HUD warning surface."""
        cr = FakeCairo()
        state = _state_with_flags(("midi_unavailable", "MIDI backend error"))
        draw_status_badge(FakeRenderer(state=state), cr, state, 1920, 1080)
        assert ("rgba", *COLOR_WARNING_FILL) in cr.calls
        assert ("rgb", *COLOR_WARNING_BORDER) in cr.calls

    def test_an_error_row_carries_a_warning_sign(self) -> None:
        """An off-white triangle with its "!" in the row's red, drawn in a
        saved state so its rounded corners don't leak into later drawing."""
        cr = FakeCairo()
        state = _state_with_flags(("midi_unavailable", "MIDI backend error"))
        draw_status_badge(FakeRenderer(state=state), cr, state, 1920, 1080)
        kinds = [c[0] for c in cr.calls]
        triangle = kinds.index("rgb", kinds.index("line_join"))
        assert cr.calls[triangle][1:] == COLOR_TEXT
        assert ("fill_preserve",) in cr.calls[triangle:]
        assert ("rgb", *COLOR_DANGER_BG) in cr.calls
        assert cr.rects, "the '!' bar"
        assert cr.saves == cr.restores == 1


class TestSeverity:
    def test_info_row_uses_the_info_blue_and_the_i_sign(self) -> None:
        """An ``"info"`` row takes the info fill and border and leads with the
        off-white "i" sign, its "i" cut out in the info blue, not the triangle."""
        cr = FakeCairo()
        state = _state_with_flags(("update_available", "Update available", "info"))
        draw_status_badge(FakeRenderer(state=state), cr, state, 1920, 1080)
        assert ("rgba", *COLOR_INFO_FILL) in cr.calls
        assert ("rgb", *COLOR_INFO_BORDER) in cr.calls
        assert ("rgb", *COLOR_INFO_BG) in cr.calls
        assert ("rgb", *COLOR_DANGER_BG) not in cr.calls, "no warning sign"
        # No warning red anywhere on a pure-info badge.
        assert ("rgb", *COLOR_WARNING_BORDER) not in cr.calls
        assert ("rgba", *COLOR_WARNING_FILL) not in cr.calls

    def test_error_row_uses_no_info_blue(self) -> None:
        cr = FakeCairo()
        state = _state_with_flags(("midi_unavailable", "Backend down", "error"))
        draw_status_badge(FakeRenderer(state=state), cr, state, 1920, 1080)
        assert ("rgb", *COLOR_WARNING_BORDER) in cr.calls
        assert ("rgb", *COLOR_INFO_BORDER) not in cr.calls
        assert ("rgba", *COLOR_INFO_FILL) not in cr.calls

    def test_a_caution_row_takes_the_caution_chip_and_the_i_sign(self) -> None:
        cr = FakeCairo()
        state = _state_with_flags(("pin", "Something limited", "caution"))
        draw_status_badge(FakeRenderer(state=state), cr, state, 1920, 1080)
        assert ("rgba", *COLOR_CAUTION_FILL) in cr.calls
        assert ("rgb", *COLOR_CAUTION_BORDER) in cr.calls
        # The "i" cut out of the off-white disc in the caution colour.
        assert ("rgb", *COLOR_CAUTION_BG) in cr.calls
        assert ("rgb", *COLOR_DANGER_BG) not in cr.calls, "no warning sign"
        assert ("rgba", *COLOR_WARNING_FILL) not in cr.calls

    def test_a_success_row_takes_the_success_chip_and_the_check(self) -> None:
        cr = FakeCairo()
        state = _state_with_flags(("diagnostics_export", "Diagnostics saved to Stick", "success"))
        draw_status_badge(FakeRenderer(state=state), cr, state, 1920, 1080)
        assert ("rgba", *COLOR_SUCCESS_FILL) in cr.calls
        assert ("rgb", *COLOR_SUCCESS_BORDER) in cr.calls
        # The check is cut out of an off-white disc, so it strokes in the success colour.
        assert ("rgb", *COLOR_SUCCESS_BG) in cr.calls
        assert ("rgb", *COLOR_INFO_BORDER) not in cr.calls
        assert ("rgba", *COLOR_WARNING_FILL) not in cr.calls

    @pytest.mark.parametrize(
        "level",
        ["warning", None, ["error"]],
        ids=["unknown-name", "none", "unhashable"],
    )
    def test_a_level_nobody_defined_reads_as_an_error(self, level: object) -> None:
        """A malformed writer must still show its row, and as a fault, not
        vanish or take the whole draw pass down with it."""
        cr = FakeCairo()
        state = _state_with_flags(("odd", "Something", level))  # type: ignore[arg-type]
        draw_status_badge(FakeRenderer(state=state), cr, state, 1920, 1080)
        assert ("rgba", *COLOR_WARNING_FILL) in cr.calls
        assert ("rgb", *COLOR_WARNING_BORDER) in cr.calls


class TestMultipleFlags:
    def test_two_flags_paint_two_rows(self) -> None:
        cr = FakeCairo()
        state = _state_with_flags(
            ("midi_patch_missing", "Patch missing"),
            ("midi_unavailable", "Backend down"),
        )
        draw_status_badge(FakeRenderer(state=state), cr, state, 1920, 1080)
        assert len(_row_top_ys(cr, _badge_x(cr))) == 2
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
        ys = _row_top_ys(cr, _badge_x(cr))
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
        all-info stack keeps a blue tail."""
        cr = FakeCairo()
        flags = [(f"src_{i}", f"info {i}", "info") for i in range(_MAX_VISIBLE_ROWS + 1)]
        state = _state_with_flags(*flags)
        draw_status_badge(FakeRenderer(state=state), cr, state, 1920, 1080)
        # Pure-info stack incl. the tail never sets the warning red.
        assert ("rgb", *COLOR_WARNING_BORDER) not in cr.calls

    @pytest.mark.parametrize(
        ("hidden", "fill"),
        [
            (["info", "error", "caution"], COLOR_WARNING_FILL),
            (["info", "caution", "success"], COLOR_CAUTION_FILL),
            (["success", "info"], COLOR_INFO_FILL),
            (["success", "success"], COLOR_SUCCESS_FILL),
            (["success", ["info"]], COLOR_WARNING_FILL),
        ],
        ids=["error-first", "then-caution", "then-info", "success-only", "malformed-is-an-error"],
    )
    def test_the_tail_takes_the_gravest_level_it_hides(self, hidden: list[object], fill: tuple[float, ...]) -> None:
        visible = [(f"v{i}", f"visible {i}", "success") for i in range(_MAX_VISIBLE_ROWS)]
        tail = [(f"h{i}", f"hidden {i}", level) for i, level in enumerate(hidden)]
        state = _state_with_flags(*visible, *tail)
        cr = FakeCairo()
        draw_status_badge(FakeRenderer(state=state), cr, state, 1920, 1080)
        fills = [c[1:] for c in cr.calls if c[0] == "rgba"]
        # The tail is the last row drawn; its fill is the last row fill set.
        assert fills[-1] == fill

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
        # edge is that + radius. The sign's "!" dot sits at the left, so the
        # rightmost arcs are the background's corners.
        right_edge = max(cx for (cx, _cy, _r) in cr.arcs) + _ROW_RADIUS
        assert right_edge == frame_w - _GUTTER

    def test_first_row_clears_system_stats_panel(self) -> None:
        cr = FakeCairo()
        state = _state_with_flags(("a", "below stats"))
        frame_w = 1920
        draw_status_badge(FakeRenderer(state=state), cr, state, frame_w, 1080)
        first_y = _row_top_ys(cr, _badge_x(cr))[0]
        assert first_y == float(_TOP_OFFSET)
        assert first_y > 34


class TestTheStackFitsItsContent:
    """A fixed 280 px box around "Video: Unreachable" was mostly empty. The
    stack sizes to its widest row instead, within bounds that keep a one-word
    row readable and a long one from crossing the frame."""

    def _render(self, *flags: tuple[str, ...]) -> tuple[FakeCairo, float]:
        cr = FakeCairo()
        frame_w = 1920
        draw_status_badge(FakeRenderer(), cr, _state_with_flags(*flags), frame_w, 1080)
        return cr, _badge_width(cr, frame_w)

    def test_a_short_row_gets_a_short_box(self) -> None:
        _cr, narrow = self._render(("video_failure", "Video: Stalled"))
        _cr2, wide = self._render(("midi", "MIDI patch missing on 3 bindings, check the mapping"))
        assert narrow < wide

    def test_it_never_collapses_below_the_minimum(self) -> None:
        _cr, width = self._render(("x", "!"))
        assert width >= _BADGE_MIN_WIDTH

    def test_it_never_exceeds_the_maximum(self) -> None:
        _cr, width = self._render(("x", "a stupendously long warning " * 12))
        assert width <= _BADGE_MAX_WIDTH

    def test_every_row_shares_one_width(self) -> None:
        """A ragged stack reads as several widgets rather than one."""
        cr, _w = self._render(("a", "Video: Unreachable"), ("b", "MIDI patch missing on 3 bindings"))
        lefts = {round(mx, 3) for mx, _y in cr.move_tos}
        # Row backgrounds all open at the same x; a ragged stack would add more
        # distinct left edges than the three per-row offsets (rect/glyph/text).
        assert len(lefts) <= 3

    def test_the_overflow_tail_is_measured_too(self) -> None:
        """The tail is a row like any other and must not be clipped by a width
        chosen without it."""
        rows = [(f"k{i}", "short") for i in range(_MAX_VISIBLE_ROWS + 3)]
        _cr, width = self._render(*rows)
        assert width >= _BADGE_MIN_WIDTH
