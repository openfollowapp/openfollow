# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for web unit-system toggle and imperial form handling.

Spins up its own live ``ConfigWebServer`` + HTTP helpers to exercise
the real route stack: toggle persist, imperial display, imperial→metric
POST parsing, and blur validation.
"""

from __future__ import annotations

import socket
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

import openfollow.web.discovery as discovery_module
from openfollow.configuration import load_config
from openfollow.web.server import ConfigWebServer

pytestmark = pytest.mark.integration


def _find_free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return int(s.getsockname()[1])


def _wait_for_port(port: int, host: str = "127.0.0.1", timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.1):
                return True
        except OSError:
            time.sleep(0.05)
    return False


@pytest.fixture()
def live_server(tmp_path, monkeypatch):  # noqa: ANN001, ANN201
    """ConfigWebServer on a free localhost port; beacon I/O stubbed."""
    for cls in (discovery_module.BeaconSender, discovery_module.BeaconReceiver):
        monkeypatch.setattr(cls, "start", lambda self: None)
        monkeypatch.setattr(cls, "stop", lambda self: None)
    port = _find_free_tcp_port()
    server = ConfigWebServer(
        config_path=str(tmp_path / "config.toml"),
        host="127.0.0.1",
        port=port,
        system_name="TestSystem",
    )
    server.start()
    assert _wait_for_port(port), f"web server did not start on {port}"
    yield server, f"http://127.0.0.1:{port}"
    server.stop()


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
