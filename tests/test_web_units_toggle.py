# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for web unit-system toggle and imperial form handling.

Spins up its own live ``ConfigWebServer`` + HTTP helpers to exercise
the real route stack: toggle persist, imperial display, imperial→metric
POST parsing, and blur validation.
"""

from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request

import pytest

import openfollow.web.discovery as discovery_module
from openfollow.configuration import load_config
from openfollow.web.server import ConfigWebServer
from tests._ports import live_on_free_port
from tests._wizard_js import length_page, needs_node, run_wizard_js

pytestmark = pytest.mark.integration


@pytest.fixture()
def live_server(tmp_path, monkeypatch):  # noqa: ANN001, ANN201
    """ConfigWebServer on a free localhost port; beacon I/O stubbed."""
    for cls in (discovery_module.BeaconSender, discovery_module.BeaconReceiver):
        monkeypatch.setattr(cls, "start", lambda self: None)
        monkeypatch.setattr(cls, "stop", lambda self: None)
    with live_on_free_port(
        lambda port: ConfigWebServer(
            config_path=str(tmp_path / "config.toml"),
            host="127.0.0.1",
            port=port,
            system_name="TestSystem",
        )
    ) as (server, base):
        yield server, base


def _get(base: str, path: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(f"{base}{path}", timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def _post_form(base: str, path: str, data: dict) -> tuple[int, str]:
    req = urllib.request.Request(
        f"{base}{path}",
        data=urllib.parse.urlencode(data).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def _post_json(base: str, path: str, data: dict) -> int:
    req = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status


def _set_unit_system(base: str, value: str) -> tuple[int, str]:
    return _post_form(base, "/settings/unit-system", {"unit_system": value})


class TestToggle:
    def test_toggle_persists_imperial(self, live_server) -> None:
        server, base = live_server
        status, _ = _set_unit_system(base, "imperial")
        assert status == 200
        assert load_config(server.config_path).ui.unit_system == "imperial"

    def test_toggle_back_to_metric(self, live_server) -> None:
        server, base = live_server
        _set_unit_system(base, "imperial")
        _set_unit_system(base, "metric")
        assert load_config(server.config_path).ui.unit_system == "metric"

    def test_unknown_value_falls_back_to_metric(self, live_server) -> None:
        server, base = live_server
        status, _ = _set_unit_system(base, "furlongs")
        assert status == 200
        assert load_config(server.config_path).ui.unit_system == "metric"


class TestImperialDisplay:
    def test_grid_labels_and_echo_in_imperial(self, live_server) -> None:
        server, base = live_server
        _set_unit_system(base, "imperial")
        status, body = _get(base, "/section/grid")
        assert status == 200
        # Label suffix flipped to ft / in.
        assert "Width (ft / in)" in body
        # Imperial-mode metric echo present under at least one field.
        assert "metric-echo" in body
        assert "Stored:" in body
        # The value is rendered in imperial (ft+in or inches), not a bare metre.
        assert "ft" in body or " in" in body

    def test_metric_has_no_echo(self, live_server) -> None:
        server, base = live_server
        _set_unit_system(base, "metric")
        status, body = _get(base, "/section/grid")
        assert status == 200
        assert "Width (m)" in body
        assert "metric-echo" not in body


class TestImperialFormSubmission:
    def test_post_grid_width_imperial_stores_metric(self, live_server) -> None:
        server, base = live_server
        _set_unit_system(base, "imperial")
        status, _ = _post_form(base, "/section/grid", {"width": "5 ft 6 in"})
        assert status == 200
        cfg = load_config(server.config_path)
        assert cfg.grid.width == pytest.approx(1.6764, abs=1e-6)

    def test_post_cone_radii_imperial_store_metric(self, live_server) -> None:
        server, base = live_server
        _set_unit_system(base, "imperial")
        status, _ = _post_form(
            base, "/section/marker", {"marker_style": "cone", "cone_base_diameter": "1 ft", "cone_top_diameter": "6 in"}
        )
        assert status == 200
        cfg = load_config(server.config_path)
        assert cfg.marker.cone_base_diameter == pytest.approx(0.3048, abs=1e-6)
        assert cfg.marker.cone_top_diameter == pytest.approx(0.1524, abs=1e-6)

    def test_post_grid_width_metric_unchanged_behaviour(self, live_server) -> None:
        server, base = live_server  # default metric
        status, _ = _post_form(base, "/section/grid", {"width": "7.5"})
        assert status == 200
        assert load_config(server.config_path).grid.width == pytest.approx(7.5)


class TestImperialSpeed:
    def test_post_movement_speed_imperial_stores_mps(self, live_server) -> None:
        server, base = live_server
        _set_unit_system(base, "imperial")
        # 4.92 ft/s ≈ 1.4996 m/s.
        status, _ = _post_form(base, "/section/movement", {"move_speed": "4.92 ft/s"})
        assert status == 200
        assert load_config(server.config_path).marker.move_speed == pytest.approx(
            4.92 * 0.3048,
            abs=1e-4,
        )

    def test_post_movement_speed_imperial_garbage_preserves_current(self, live_server) -> None:
        # Unparseable imperial input is left as-is by _normalize_unit_fields
        # (the error branch), so the save preserves the current value instead
        # of crashing. The inline error is surfaced by the blur validator, not
        # the POST. Covers _normalize_unit_fields' error path.
        server, base = live_server
        _set_unit_system(base, "imperial")
        before = load_config(server.config_path).marker.move_speed
        status, _ = _post_form(base, "/section/movement", {"move_speed": "quick"})
        assert status == 200
        assert load_config(server.config_path).marker.move_speed == before

    def test_movement_speed_label_imperial(self, live_server) -> None:
        server, base = live_server
        _set_unit_system(base, "imperial")
        status, body = _get(base, "/section/movement")
        assert status == 200
        assert "Min Speed (ft/s)" in body

    def test_valid_imperial_speed_passes_validation(self, live_server) -> None:
        server, base = live_server
        _set_unit_system(base, "imperial")
        status, body = _get(base, "/api/validate/movement/min_speed?min_speed=4.92%20ft%2Fs")
        assert status == 200
        assert "field-error-msg" not in body

    def test_garbage_imperial_speed_errors(self, live_server) -> None:
        server, base = live_server
        _set_unit_system(base, "imperial")
        status, body = _get(base, "/api/validate/movement/min_speed?min_speed=quick")
        assert status == 200
        assert "field-error-msg" in body

    def test_empty_imperial_length_passes_through(self, live_server) -> None:
        """Blank input is not a parse error – it's handled downstream as
        'unchanged' (covers the empty-string early return)."""
        server, base = live_server
        _set_unit_system(base, "imperial")
        status, body = _get(base, "/api/validate/grid/x_offset?x_offset=")
        assert status == 200
        assert "field-error-msg" not in body


class TestImperialBlurValidation:
    def test_valid_imperial_length_passes(self, live_server) -> None:
        server, base = live_server
        _set_unit_system(base, "imperial")
        status, body = _get(base, "/api/validate/grid/width?width=5%20ft%206%20in")
        assert status == 200
        # No error span for a valid imperial length.
        assert "field-error-msg" not in body

    def test_garbage_imperial_length_errors(self, live_server) -> None:
        server, base = live_server
        _set_unit_system(base, "imperial")
        status, body = _get(base, "/api/validate/grid/width?width=five%20feet")
        assert status == 200
        assert "field-error-msg" in body

    def test_below_min_imperial_length_errors(self, live_server) -> None:
        server, base = live_server
        _set_unit_system(base, "imperial")
        status, body = _get(base, "/api/validate/grid/width?width=1%20in")
        assert status == 200
        assert "field-error-msg" in body


class TestSharedUnitJsInjection:
    """Unit system is injected once in base.tpl
    (window.OPENFOLLOW_UNIT_SYSTEM) and ft/in formatter/parser ships as
    a shared static module (units.js -> window.OpenFollow.units) consumed
    by the zone-editor and setup wizard."""

    def test_index_injects_imperial_unit_system(self, live_server) -> None:
        server, base = live_server
        _set_unit_system(base, "imperial")
        status, body = _get(base, "/")
        assert status == 200
        assert 'window.OPENFOLLOW_UNIT_SYSTEM = "imperial"' in body
        assert "/assets/js/units.js" in body
        # The zone editor consumes the shared helper.
        assert "window.OpenFollow.units" in body

    def test_index_injects_metric_unit_system(self, live_server) -> None:
        server, base = live_server  # default metric
        status, body = _get(base, "/")
        assert status == 200
        assert 'window.OPENFOLLOW_UNIT_SYSTEM = "metric"' in body

    def test_units_js_ships_formatter_and_parser(self, live_server) -> None:
        server, base = live_server
        status, body = _get(base, "/assets/js/units.js")
        assert status == 200
        assert "function formatLength" in body
        assert "function parseLength" in body

    def test_units_js_length_format_uses_half_even_not_toFixed(self, live_server) -> None:
        """Structural parity guard (no JS engine in CI to execute units.js): the
        length formatters must round through ``toFixedHalfEven`` to match Python's
        banker's rounding, not raw ``toFixed`` (round half-away)."""
        server, base = live_server
        _status, body = _get(base, "/assets/js/units.js")
        assert "function toFixedHalfEven" in body
        # The only remaining ``.toFixed(`` is the NaN/Inf fallback inside the
        # helper itself; the dp=2/dp=3 length sites must not use it directly.
        assert ".toFixed(3)" not in body
        assert ".toFixed(2)" not in body


class TestWizardUnitInjection:
    """The camera setup wizard renders lengths in the active unit
    (angles and the /api/wizard/* wire stay metric/degrees)."""

    def test_wizard_imperial_labels_and_echo(self, live_server) -> None:
        server, base = live_server
        _set_unit_system(base, "imperial")
        status, body = _get(base, "/wizard")
        assert status == 200
        assert "Width (ft / in)" in body
        assert "Pos X (ft / in)" in body
        # Server-rendered metric echo elements (imperial only).
        assert 'id="grid_width-echo"' in body
        assert 'id="cam_pos_x-echo"' in body
        # The wizard uses the shared JS helper for live readouts/parsing.
        assert "window.OpenFollow.units" in body

    def test_wizard_metric_has_no_echo(self, live_server) -> None:
        server, base = live_server  # default metric
        status, body = _get(base, "/wizard")
        assert status == 200
        assert "Width (m)" in body
        # No imperial echo elements are rendered in metric mode. (The
        # "Stored:" string itself lives in the always-present JS helper, so
        # assert on the server-rendered element id instead.)
        assert 'id="grid_width-echo"' not in body
        assert ' data-meters="' not in body

    @needs_node
    def test_imperial_lengths_read_as_the_metres_saved(self, live_server) -> None:
        server, base = live_server
        _set_unit_system(base, "imperial")
        # Each shows as ft / in rounded to 0.01 in; read back from that text, 0.5 m was 0.500126 m.
        assert _post_json(base, "/api/config/grid", {"width": 7.5, "spacing": 0.5, "x_offset": -1.23}) == 200
        assert _post_json(base, "/api/config/camera", {"pos_x": 0.3, "pos_y": -11.07, "pos_z": 5.5}) == 200
        cfg = load_config(server.config_path)
        saved = {f"grid_{k}": getattr(cfg.grid, k) for k in ("width", "depth", "spacing", "x_offset", "y_offset")}
        saved |= {"grid_z_offset": cfg.grid.z_offset}
        saved |= {f"cam_{k}": getattr(cfg.camera, k) for k in ("pos_x", "pos_y", "pos_z")}
        _status, body = _get(base, "/wizard")
        fields = {}
        for field_id in saved:
            tag = re.search(rf'<input\b[^>]*\bid="{field_id}"[^>]*>', body)
            assert tag, field_id
            value = re.search(r'\bvalue="([^"]*)"', tag.group(0))
            meters = re.search(r'\bdata-meters="([^"]*)"', tag.group(0))
            assert value and meters, field_id
            fields[field_id] = {"value": html.unescape(value.group(1)), "dataset": {"meters": meters.group(1)}}
        read = run_wizard_js(
            "ids.map(function(id) { return wizReadLen(id); })",
            functions=("wizReadLen",),
            prelude=length_page("imperial", fields),
            ids=list(saved),
        )
        assert read == list(saved.values())

    @pytest.mark.parametrize("system", ["metric", "imperial"])
    def test_grid_setup_opens_on_the_unit_choice(self, live_server, system) -> None:
        server, base = live_server
        _set_unit_system(base, system)
        status, body = _get(base, "/wizard")
        assert status == 200

        step = body[body.index('id="wizard-step-grid"') : body.index('id="wizard-step-video"')]
        first_control = re.search(r"<(?:select|input)\b[^>]*>", step)
        assert first_control
        assert 'id="wizard-unit-system"' in first_control.group(0)
        assert f'<option value="{system}" selected>' in step

    def test_the_wizard_unit_choice_saves_through_the_settings_route(self, live_server) -> None:
        server, base = live_server
        _status, body = _get(base, "/wizard")
        assert "fetch('/settings/unit-system'" in body
        assert "body.append('unit_system', select.value)" in body
        # The route the wizard posts to persists the choice.
        _set_unit_system(base, "imperial")
        assert load_config(server.config_path).ui.unit_system == "imperial"


@pytest.mark.parametrize("system", ["metric", "imperial"])
def test_general_shows_the_active_unit_system(live_server, system) -> None:
    server, base = live_server
    _set_unit_system(base, system)
    status, body = _get(base, "/")
    assert status == 200
    select = body[
        body.index('id="general-unit-system"') : body.index("</select>", body.index('id="general-unit-system"'))
    ]
    assert f'<option value="{system}" selected>' in select


class TestDetectInputWidget:
    """The reusable detect-input widget (detect-input.js) is bundled and
    referenced, and the 3D Mouse section wires its button binds to it."""

    def test_index_references_detect_input_js(self, live_server) -> None:
        server, base = live_server
        status, body = _get(base, "/")
        assert status == 200
        assert "/assets/js/detect-input.js" in body

    def test_detect_input_js_is_served(self, live_server) -> None:
        server, base = live_server
        status, body = _get(base, "/assets/js/detect-input.js")
        assert status == 200
        assert "data-detect-input" in body
        assert "data-detect-url" in body
        # The detect poll must bypass the browser cache so a stale {button: null}
        # (or a previous button) can't satisfy the live fetch.
        assert "cache: 'no-store'" in body

    def test_mouse3d_section_wires_detect_widget(self, live_server) -> None:
        server, base = live_server
        status, body = _get(base, "/section/mouse3d")
        assert status == 200
        assert 'class="detect-input"' in body
        assert 'data-detect-input="m3d-btn_reset"' in body
        assert 'data-detect-url="/section/mouse3d/detect"' in body
