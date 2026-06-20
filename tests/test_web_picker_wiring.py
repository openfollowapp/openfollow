# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for colour-picker wiring across all six sites.

The picker replaces every native ``<input type="color">`` in the web
UI with a circle-swatch button. A regression here either:

- reintroduces a native picker (inconsistent UX); or
- drops the ``window.OPENFOLLOW_PALETTE`` injection (the picker JS
  bails with a console error and every swatch trigger silently
  becomes a dead button).

These tests grep the rendered template HTML for the structural
markers of the wiring so a future template rework can't silently
break either invariant.
"""

from __future__ import annotations

import pytest
from bottle import template

from openfollow.configuration import AppConfig
from openfollow.web import server as _server_module  # noqa: F401 – registers tpl path

pytestmark = pytest.mark.unit


def _render(tpl_name: str, **context) -> str:
    return template(tpl_name, config=AppConfig(), **context)


class TestPaletteInjection:
    """``base.tpl`` embeds ``window.OPENFOLLOW_PALETTE`` inline so the
    picker JS has the palette before any partial-level script runs.
    No fetch, no first-paint flash."""

    def test_palette_injected_into_base_template(self) -> None:
        body = template("base", config=AppConfig(), base="")
        assert "window.OPENFOLLOW_PALETTE = " in body
        # Sanity: the seed includes the three top-level keys the picker
        # reads. A serialization tweak that renamed any of these would
        # silently break the picker, so we pin the literals here.
        assert '"hue_columns":' in body
        assert '"greys":' in body
        assert '"auto_pick_order":' in body

    def test_color_picker_script_loaded(self) -> None:
        body = template("base", config=AppConfig(), base="")
        assert "/assets/js/color-picker.js" in body


class TestPickerCSS:
    """The picker CSS lives in base.tpl alongside other ``htmx-`` /
    ``color-`` rules. Pin a few class names so a stylesheet refactor
    can't silently strip them."""

    def test_swatch_trigger_class_styled(self) -> None:
        body = template("base", config=AppConfig(), base="")
        assert ".color-swatch-trigger" in body
        assert ".color-picker-popover" in body
        assert ".color-picker-swatch" in body
        assert ".color-picker-hex" in body


class TestNoNativeColorInputsLeft:
    """Every site that used to ship ``<input type="color">`` must now
    render the circle-swatch button. The grep is intentionally narrow
    (literal substring) so the test fails the moment a regression
    reintroduces a native picker."""

    @pytest.mark.parametrize(
        "partial,context",
        [
            ("partials/marker", {"saved": False}),
            ("partials/grid", {}),
            ("partials/detection", {}),
        ],
    )
    def test_partial_has_no_native_color_input(self, partial: str, context: dict) -> None:
        body = _render(partial, **context)
        assert 'type="color"' not in body, (
            f"{partial} still ships a native color picker – PR2 "
            "replaced every native picker with the circle-swatch trigger."
        )

    def test_zone_editor_has_no_native_color_input(self) -> None:
        body = _render("partials/zone_editor")
        assert 'type="color"' not in body, (
            "zone_editor still ships a native color picker – PR2 "
            "replaced the JS-rendered detail-panel picker with the "
            "circle-swatch trigger."
        )


class TestSwatchTriggerSites:
    """Pin the six call sites carry the picker mode attribute. Catches
    a refactor that drops ``data-color-picker`` (which would leave the
    button visible but inert)."""

    def test_marker_crosshair_uses_greys_variant(self) -> None:
        body = _render("partials/marker", saved=False)
        # The crosshair colour is intentionally greys-only – a coloured
        # crosshair has no use case and would clash with the marker
        # ball colour.
        assert 'id="marker-crosshair-color"' in body
        assert 'data-color-picker="greys"' in body

    def test_grid_line_color_uses_greys_variant(self) -> None:
        body = _render("partials/grid")
        assert 'id="grid-color"' in body
        assert 'data-color-picker="greys"' in body

    def test_detection_box_color_uses_full_variant(self) -> None:
        body = _render("partials/detection")
        assert 'id="detection-box-color"' in body
        assert 'data-color-picker="full"' in body

    def test_marker_catalog_js_renders_full_swatch(self) -> None:
        """The catalog row JS renderer (poll-driven) must emit the
        swatch markup, not the legacy native picker. Grep the literal
        the inline JS appends so a regression in marker.tpl's
        ``render()`` is caught here."""
        body = _render("partials/marker", saved=False)
        assert 'data-field="color"' in body
        assert "color-swatch-trigger" in body
        assert 'data-color-picker="full"' in body

    def test_marker_palette_const_removed(self) -> None:
        """``MARKER_COLOR_PALETTE`` was the duplicated 20-hex list in
        marker.tpl; it's deleted and ``pickMarkerColor`` now delegates
        to ``window.OpenFollow.nextUnusedColor``."""
        body = _render("partials/marker", saved=False)
        assert "const MARKER_COLOR_PALETTE" not in body
        assert "OpenFollow.nextUnusedColor" in body

    def test_zone_palette_const_removed(self) -> None:
        """Same cleanup for ``ZONE_COLOR_PALETTE`` in zone_editor.tpl."""
        body = _render("partials/zone_editor")
        assert "var ZONE_COLOR_PALETTE" not in body
        assert "OpenFollow.nextUnusedColor" in body
