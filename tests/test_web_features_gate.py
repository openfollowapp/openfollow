# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Static parity guard for the Features list gate.

Each section a feature owns carries ``data-feature="<key>"``; experimental
ones also carry the ``badge-experimental`` chip in their header. Reads the
template files directly."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from openfollow.configuration import FEATURE_KEYS, OFFERED_FEATURES

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


# (template, anchor, feature) – each root carries its feature.
_GATED_ROOTS = [
    ("index.tpl", 'data-tab="detection"', "detection"),
    ("index.tpl", 'id="tab-detection"', "detection"),
    # The detection partial gates itself so it hides even outside its tab.
    ("partials/detection.tpl", 'id="detection-section"', "detection"),
    ("partials/rttrpm_output.tpl", 'id="rttrpm-output-section"', "rttrpm"),
    ("partials/camera.tpl", 'id="camera-calibration-points"', "calibration_points"),
    ("wizard.tpl", 'id="calib-section"', "calibration_points"),
    ("wizard.tpl", 'id="review-calib"', "calibration_points"),
    ("partials/trigger_zones.tpl", 'id="trigger-zones-section"', "zones"),
    ("partials/zone_editor.tpl", 'id="zone-editor-section"', "zones"),
    ("partials/paths.tpl", 'id="paths-section"', "paths"),
    ("partials/path_editor.tpl", 'id="path-editor-section"', "paths"),
    ("partials/ground_grid.tpl", 'id="ground-grid-section"', "ground_grid"),
    ("partials/touch_remote.tpl", 'id="touch-remote-section"', "touch_remote"),
    ("partials/midi_fader_detail.tpl", "mf-source-row", "midi"),
]


@pytest.mark.parametrize(("rel", "anchor", "feature"), _GATED_ROOTS)
def test_section_root_carries_its_feature(rel: str, anchor: str, feature: str) -> None:
    line = _line_with(_read(rel), anchor)
    assert f'data-feature="{feature}"' in line, f"{rel}: {anchor!r} is not gated on {feature!r}"


@pytest.mark.parametrize(
    ("rel", "heading", "feature"),
    [
        ("partials/camera.tpl", '<h3 class="group-title">Lens distortion</h3>', "lens_distortion"),
        ("partials/midi.tpl", '<h3 class="group-title">MIDI Patches</h3>', "midi"),
    ],
)
def test_group_above_its_heading_carries_its_feature(rel: str, heading: str, feature: str) -> None:
    text = _read(rel)
    group = text[: text.index(heading)].rsplit("<div", 1)[1]
    assert f'data-feature="{feature}"' in group


# Each experimental section's header carries the Experimental badge. The
# Person Detection page has no single header: every collapsible box carries its
# own badge. Review's calibration-point list has none: it lists what the badged Advanced section edits.
_BADGED_HEADERS = [
    ("partials/detection.tpl", "<h2>Tracking"),
    ("partials/detection.tpl", "<h2>Detection Model"),
    ("partials/detection.tpl", "<h2>Sensitivity &amp; Overlay"),
    ("partials/detection_mask_editor.tpl", "<h2>Detection Masks"),
    ("partials/rttrpm_output.tpl", "<h2>RTTrPM Output"),
    ("partials/camera.tpl", '<h3 class="group-title">Calibration points'),
    ("wizard.tpl", "<summary>Advanced"),
]


@pytest.mark.parametrize("rel,heading", _BADGED_HEADERS)
def test_section_header_carries_badge(rel: str, heading: str) -> None:
    line = _line_with(_read(rel), heading)
    assert "badge-experimental" in line, f"{rel}: {heading!r} header is missing the Experimental badge"


def _template_texts() -> list[str]:
    return [path.read_text(encoding="utf-8") for path in _TEMPLATES.rglob("*.tpl")]


def test_every_data_feature_names_a_known_feature() -> None:
    named = {value for text in _template_texts() for value in re.findall(r'data-feature="([a-z_]+)"', text)}
    assert named <= set(FEATURE_KEYS)


def test_every_feature_but_psn_input_hides_something() -> None:
    texts = "\n".join(_template_texts())
    # Binding rows pass their feature through a helper; the interface row through routes.
    hidden = {key for key in FEATURE_KEYS if f'data-feature="{key}"' in texts or f"feature='{key}'" in texts}
    assert set(FEATURE_KEYS) - hidden == {"psn_input"}


def test_nothing_carries_the_retired_gate_class() -> None:
    for text in _template_texts():
        assert "experimental-feature" not in text
        assert "show-experimental" not in text


def test_wizard_lens_step_has_one_set_of_coefficient_inputs() -> None:
    wiz = _read("wizard.tpl")
    assert wiz.count('id="wiz_lens_k1"') == 1
    assert wiz.count('id="wiz_lens_k2"') == 1
    assert '<input type="hidden" id="wiz_lens_k1"' not in wiz


def test_base_tpl_hides_each_feature_off_its_body_class() -> None:
    base = _read("base.tpl")
    assert 'body:not(.feature-{{_fkey}}) [data-feature="{{_fkey}}"] { display: none !important; }' in base
    assert "'feature-' + k for k in config.ui.shown_features" in base
    assert '<script src="/assets/js/feature-save.js?v={{_asset_v}}"></script>' in base


def test_the_features_table_saves_with_the_station_settings_save() -> None:
    general = _read("partials/general.tpl")
    table = general[general.index('<div id="features-section"') :]
    table = table[: table.index("</table>")]
    assert 'form="general-network-section"' in table
    assert 'name="features_submitted"' in table
    assert "hx-post" not in table and "onchange" not in table
    assert "broadcastSection" not in table


def test_every_feature_box_has_a_bool_rule() -> None:
    from openfollow.web.routes import _as_bool
    from openfollow.web.validation import FIELD_RULES

    assert set(FIELD_RULES["features"]) == set(OFFERED_FEATURES)
    assert all(rule.parser is _as_bool for rule in FIELD_RULES["features"].values())
