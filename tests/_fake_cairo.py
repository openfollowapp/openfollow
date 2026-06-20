# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Shared recording Cairo context + renderer stubs for overlay_draw_* tests.

These fakes record every drawing call issued by the overlay draw passes so
tests can assert against observable effects (positions, colours, text strings,
fill vs stroke counts) without depending on real font rendering or pixel
output.  ``text_extents`` returns deterministic widths derived from
``len(text) * font_size * 0.6`` so ``_truncate_text_to_width`` exercises its
real truncation loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import cairo
import numpy as np

from openfollow.runtime.overlay_state import OverlayState
from openfollow.video.overlay import CairoOverlayRenderer


@dataclass
class _TextExtents:
    x_bearing: float = 0.0
    y_bearing: float = 0.0
    width: float = 0.0
    height: float = 0.0
    x_advance: float = 0.0
    y_advance: float = 0.0


@dataclass
class TextDraw:
    text: str
    x: float
    y: float
    font_size: float
    bold: bool
    rgba: tuple[float, ...]


class FakeCairo:
    """Recording Cairo context stand-in.

    Each geometry / text / style call is captured both in a typed list
    (``move_tos``, ``arcs``, ``texts`` …) and in the flat ``calls`` trace
    so tests can assert ordering when it matters.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.move_tos: list[tuple[float, float]] = []
        self.line_tos: list[tuple[float, float]] = []
        self.rects: list[tuple[float, float, float, float]] = []
        self.arcs: list[tuple[float, float, float]] = []
        self.texts: list[TextDraw] = []
        self.text_paths: list[TextDraw] = []
        self.fills = 0
        self.strokes = 0
        self.fill_preserves = 0
        self.stroke_preserves = 0
        self.closes = 0
        self.saves = 0
        self.restores = 0
        self.translates: list[tuple[float, float]] = []
        self.scales: list[tuple[float, float]] = []

        self._cur_rgba: tuple[float, ...] = (0.0, 0.0, 0.0, 1.0)
        self._cur_x: float = 0.0
        self._cur_y: float = 0.0
        self._cur_font_size: float = 10.0
        self._cur_font_bold: bool = False
        self._cur_line_width: float = 1.0

    # ------------------------------------------------------------------
    # Style
    # ------------------------------------------------------------------
    def set_source_rgba(self, *args: float) -> None:
        if len(args) == 3:
            self._cur_rgba = (*args, 1.0)
        else:
            self._cur_rgba = tuple(args)
        self.calls.append(("rgba", *args))

    def set_source_rgb(self, *args: float) -> None:
        self._cur_rgba = (*args, 1.0)
        self.calls.append(("rgb", *args))

    def set_source(self, _pattern: Any) -> None:
        self.calls.append(("source_pattern",))

    def set_line_width(self, w: float) -> None:
        self._cur_line_width = w
        self.calls.append(("line_width", w))

    # ------------------------------------------------------------------
    # Geometry
    # ------------------------------------------------------------------
    def move_to(self, x: float, y: float) -> None:
        self._cur_x, self._cur_y = x, y
        self.move_tos.append((x, y))
        self.calls.append(("move_to", x, y))

    def line_to(self, x: float, y: float) -> None:
        self._cur_x, self._cur_y = x, y
        self.line_tos.append((x, y))
        self.calls.append(("line_to", x, y))

    def rectangle(self, x: float, y: float, w: float, h: float) -> None:
        self.rects.append((x, y, w, h))
        self.calls.append(("rectangle", x, y, w, h))

    def arc(self, cx: float, cy: float, r: float, a0: float, a1: float) -> None:
        self.arcs.append((cx, cy, r))
        self.calls.append(("arc", cx, cy, r, a0, a1))

    def close_path(self) -> None:
        self.closes += 1
        self.calls.append(("close_path",))

    # ------------------------------------------------------------------
    # Paint
    # ------------------------------------------------------------------
    def fill(self) -> None:
        self.fills += 1
        self.calls.append(("fill",))

    def stroke(self) -> None:
        self.strokes += 1
        self.calls.append(("stroke",))

    def fill_preserve(self) -> None:
        self.fill_preserves += 1
        self.calls.append(("fill_preserve",))

    def stroke_preserve(self) -> None:
        self.stroke_preserves += 1
        self.calls.append(("stroke_preserve",))

    # ------------------------------------------------------------------
    # Text
    # ------------------------------------------------------------------
    def select_font_face(self, family: str, slant: int, weight: int) -> None:
        self._cur_font_bold = weight == cairo.FONT_WEIGHT_BOLD
        self.calls.append(("font_face", family, slant, weight))

    def set_font_face(self, face: Any) -> None:
        # Production sets a cached ToyFontFace instead of calling select_font_face.
        # Mirror the bold tracking off the face's weight for TextDraw recording.
        self._cur_font_bold = face.get_weight() == cairo.FONT_WEIGHT_BOLD
        self.calls.append(
            (
                "set_font_face",
                face.get_family(),
                face.get_weight(),
            )
        )

    def set_font_size(self, size: float) -> None:
        self._cur_font_size = size
        self.calls.append(("font_size", size))

    def show_text(self, text: str) -> None:
        self.texts.append(
            TextDraw(
                text=text,
                x=self._cur_x,
                y=self._cur_y,
                font_size=self._cur_font_size,
                bold=self._cur_font_bold,
                rgba=self._cur_rgba,
            )
        )
        self.calls.append(("show_text", text))

    def text_path(self, text: str) -> None:
        self.text_paths.append(
            TextDraw(
                text=text,
                x=self._cur_x,
                y=self._cur_y,
                font_size=self._cur_font_size,
                bold=self._cur_font_bold,
                rgba=self._cur_rgba,
            )
        )
        self.calls.append(("text_path", text))

    def text_extents(self, text: str) -> _TextExtents:
        width = len(text) * self._cur_font_size * 0.6
        height = self._cur_font_size * 0.9
        return _TextExtents(
            x_bearing=0.0,
            y_bearing=-height,
            width=width,
            height=height,
            x_advance=width,
            y_advance=0.0,
        )

    # ------------------------------------------------------------------
    # Group / alpha (used by draw_marker_card's viewer-only wrap)
    # ------------------------------------------------------------------
    def push_group(self) -> None:
        self.calls.append(("push_group",))

    def pop_group_to_source(self) -> None:
        self.calls.append(("pop_group_to_source",))

    def paint_with_alpha(self, alpha: float) -> None:
        self.calls.append(("paint_with_alpha", alpha))

    # ------------------------------------------------------------------
    # Transform (used by _draw_icon / calibration)
    # ------------------------------------------------------------------
    def save(self) -> None:
        self.saves += 1
        self.calls.append(("save",))

    def restore(self) -> None:
        self.restores += 1
        self.calls.append(("restore",))

    def clip(self) -> None:
        # Records the clip; geometry is a no-op in the fake (the current
        # path is consumed by the real Cairo, which we don't model).
        self.calls.append(("clip",))

    def translate(self, x: float, y: float) -> None:
        self.translates.append((x, y))
        self.calls.append(("translate", x, y))

    def scale(self, sx: float, sy: float) -> None:
        self.scales.append((sx, sy))
        self.calls.append(("scale", sx, sy))

    # ------------------------------------------------------------------
    # Helpers for assertions
    # ------------------------------------------------------------------
    def find_texts(self, needle: str) -> list[TextDraw]:
        """Return every show_text draw whose content contains ``needle``."""
        return [t for t in self.texts if needle in t.text]

    def show_text_strings(self) -> list[str]:
        return [t.text for t in self.texts]


class FakeRenderer:
    """Stand-in for ``CairoOverlayRenderer`` used by draw-pass tests.

    Uses the real static helpers (``_set_ui_font``, ``_truncate_text_to_width``)
    from ``CairoOverlayRenderer`` so those code paths are exercised as-is.
    ``_draw_icon`` / ``_draw_logo`` are no-op recorders; no SVG handle is loaded
    here. ``_logo_handle`` defaults to ``None`` so draw passes take the text
    fallback unless a test sets it truthy.
    """

    def __init__(self, *, state: OverlayState | None = None) -> None:
        self.state: OverlayState = state if state is not None else OverlayState()
        self._grid_pts_buf = np.zeros((400, 3), dtype=np.float64)
        self.draw_icon_calls: list[tuple[float, float, float]] = []
        self.draw_logo_calls: list[tuple[float, float, float]] = []
        self._logo_handle: object | None = None
        # Cache per-frame HUD memoization, mirroring CairoOverlayRenderer.
        self._info_panel_cache: tuple[object, object] | None = None
        self._help_sections_cache: tuple[object, object] | None = None

    _set_ui_font = staticmethod(CairoOverlayRenderer._set_ui_font)
    _truncate_text_to_width = staticmethod(CairoOverlayRenderer._truncate_text_to_width)

    def _draw_icon(self, cr: FakeCairo, x: float, y: float, size: float) -> None:
        self.draw_icon_calls.append((x, y, size))

    def _draw_logo(self, cr: FakeCairo, x: float, y: float, width: float) -> float:
        self.draw_logo_calls.append((x, y, width))
        # Mirror the real aspect-ratio-preserving return (viewBox 694.06×189.96).
        return width * 189.96 / 694.06
