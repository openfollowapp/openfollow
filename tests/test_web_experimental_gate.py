# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Static parity guard for the experimental-feature gate.

Each experimental section root must carry the ``experimental-feature``
class and a ``badge-experimental`` chip in its header. Reads the template
files directly."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_TEMPLATES = Path(__file__).resolve().parents[1] / "openfollow" / "web" / "templates"


def _read(rel: str) -> str:
    return (_TEMPLATES / rel).read_text(encoding="utf-8")


def _line_with(text: str, needle: str) -> str:
    """Return the first line of ``text`` containing ``needle`` (raises if none)."""
    for line in text.splitlines():
        if needle in line:
            return line
    raise AssertionError(f"anchor {needle!r} not found")


# (template, anchor) – each root must carry the gate class.
_GATED_ROOTS = [
    ("index.tpl", 'data-tab="detection"'),  # tab button
    ("index.tpl", 'id="tab-detection"'),  # tab content
    # The detection partial self-gates so it hides even if rendered
    # outside its tab wrapper.
    ("partials/detection.tpl", 'id="detection-section"'),
    ("partials/rttrpm_output.tpl", 'id="rttrpm-output-section"'),
]


@pytest.mark.parametrize("rel,anchor", _GATED_ROOTS)
def test_section_root_carries_gate_class(rel: str, anchor: str) -> None:
    line = _line_with(_read(rel), anchor)
    assert "experimental-feature" in line, f"{rel}: {anchor!r} root is missing the experimental-feature class"


# Each experimental section's header must carry the Experimental badge. The
# Person Detection page has no single header: every collapsible box carries its
# own badge.
_BADGED_HEADERS = [
    ("partials/detection.tpl", "Tracking"),
    ("partials/detection.tpl", "Detection Model"),
    ("partials/detection.tpl", "Sensitivity &amp; Overlay"),
    ("partials/detection_mask_editor.tpl", "Detection Masks"),
    ("partials/rttrpm_output.tpl", "RTTrPM Output"),
]


@pytest.mark.parametrize("rel,heading", _BADGED_HEADERS)
def test_section_header_carries_badge(rel: str, heading: str) -> None:
    line = _line_with(_read(rel), f"<h2>{heading}")
    assert "badge-experimental" in line, f"{rel}: {heading!r} header is missing the Experimental badge"


def test_wizard_lens_controls_are_experimental_gated() -> None:
    # The corner-pinning lens-distortion sliders must hide when experimental
    # features are off – same gate as the Camera-tab group. Both the controls
    # and the tip below them carry the class so nothing lens-related shows.
    wiz = _read("wizard.tpl")
    controls = _line_with(wiz, 'id="cp-lens-controls"')
    assert "experimental-feature" in controls, "wizard.tpl: lens controls missing the gate class"
    tip = _line_with(wiz, "Bow the projected grid")
    assert "experimental-feature" in tip, "wizard.tpl: lens tip missing the gate class"
    # Marked with the Experimental badge like the Camera-tab group.
    label = _line_with(wiz, 'for="cp_lens_k1"')
    assert "badge-experimental" in label, "wizard.tpl: lens controls missing the Experimental badge"


def test_base_tpl_ships_gate_css_and_body_class() -> None:
    base = _read("base.tpl")
    assert "body:not(.show-experimental) .experimental-feature" in base
    # The body class is driven off the persisted setting.
    assert "config.ui.show_experimental_features" in base


def test_general_toggle_is_a_sibling_form_not_nested_in_units() -> None:
    # Toggle lives in its own form posting to /settings/experimental.
    general = _read("partials/general.tpl")
    assert 'hx-post="/settings/experimental"' in general
    assert 'name="show_experimental_features"' in general


def test_toggle_mirrors_server_cascade_clientside() -> None:
    # Turning the toggle off unchecks the detection Enabled box to mirror the
    # server cascade. Mouse input is no longer experimental, so it is untouched.
    base = _read("base.tpl")
    assert "function onExperimentalToggle(cb)" in base
    assert '#detection-section input[name="enabled"]' in base
    assert '#mouse-section input[name="mouse_enabled"]' not in base
    # Checkbox is wired to the handler.
    assert "onExperimentalToggle(this)" in _read("partials/general.tpl")
