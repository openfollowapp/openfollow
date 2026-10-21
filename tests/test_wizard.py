# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Tests for the Setup Wizard page: its steps and markup, the saved calibration it starts from, Apply,
and the Lens step's routes.

The solve route is covered in ``tests/test_wizard_solve_route.py``, the page's maths in
``tests/test_wizard_js.py``.
"""

from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.request
from html.parser import HTMLParser

import numpy as np
import pytest

import openfollow.web.discovery as discovery_module
from openfollow.configuration import CalibrationMarkConfig, CameraConfig, GridConfig, load_config
from openfollow.scene.solver import apply_overlay_distortion, project_points
from openfollow.web.server import ConfigWebServer
from tests._ports import live_on_free_port
from tests._wizard_js import (
    WIZARD_TEMPLATE,
    assert_script_parses,
    needs_node,
    run_wizard_js,
    wizard_function,
    wizard_window_function,
)

# ---------------------------------------------------------------------------
# Markers
# ---------------------------------------------------------------------------

# Unit tests (solver-level, no server)
unit = pytest.mark.unit
# Integration tests (live HTTP server)
integration = pytest.mark.integration

IMG_W, IMG_H = 1920.0, 1080.0


def _quiet_discovery(monkeypatch) -> None:
    monkeypatch.setattr(discovery_module.BeaconSender, "start", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconSender, "stop", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconReceiver, "start", lambda self: None)
    monkeypatch.setattr(discovery_module.BeaconReceiver, "stop", lambda self: None)


@pytest.fixture()
def live_server(tmp_path, monkeypatch):
    _quiet_discovery(monkeypatch)
    config_path = tmp_path / "config.toml"
    with live_on_free_port(
        lambda port: ConfigWebServer(
            config_path=str(config_path),
            host="127.0.0.1",
            port=port,
            system_name="WizardTest",
        )
    ) as (server, base):
        yield server, base


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _get(base: str, path: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(f"{base}{path}", timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def _post_json(base: str, path: str, data: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def _wizard_step(body: str, key: str) -> str:
    """One step's markup, up to whichever step follows it."""
    start = body.index(f'id="wizard-step-{key}"')
    end = body.find('<div class="wizard-content', start)
    return body[start : end if end != -1 else len(body)]


def _without_scripts(page: str) -> str:
    return re.sub(r"<script.*?</script>", "", page, flags=re.S)


def _wizard_script(page: str) -> str:
    """The page's own script: the inline one that runs the wizard."""
    (script,) = [s for s in re.findall(r"<script>(.*?)</script>", page, re.S) if "var WUNIT" in s]
    return script


def _saved(page: str) -> dict:
    """``window.WIZ_SAVED`` as the page carries it, read as strict JSON (a NaN would stop the page)."""
    match = re.search(r"window\.WIZ_SAVED = (.*);\n", page)
    assert match is not None

    def refuse(token: str) -> float:
        raise ValueError(f"{token} is no JSON a browser reads")

    return json.loads(match.group(1), parse_constant=refuse)


def _lens_station(
    tmp_path,
    monkeypatch,
    *,
    developer_mode: bool = False,
    lens: bool = True,
    unit_system: str = "metric",
    mode: str = "simple",
    marks: tuple = (),
):
    """A station with a stored lens pair and, when given, a saved calibration."""
    from openfollow.configuration import AppConfig, save_config

    _quiet_discovery(monkeypatch)
    # Its own file, so a test holding several fixtures keeps the plain station plain.
    config_path = tmp_path / ("config_lens_dev.toml" if developer_mode else "config_lens.toml")
    cfg = AppConfig()
    if not lens:
        cfg.ui.shown_features = [f for f in cfg.ui.shown_features if f != "lens_distortion"]
    cfg.ui.developer_mode = developer_mode
    cfg.camera.lens_k1 = -0.21
    cfg.camera.lens_k2 = 0.03
    cfg.camera.calibration_mode = mode
    cfg.camera.calibration_marks = list(marks)
    cfg.ui.unit_system = unit_system
    save_config(cfg, str(config_path))
    return live_on_free_port(
        lambda port: ConfigWebServer(
            config_path=str(config_path),
            host="127.0.0.1",
            port=port,
            system_name="WizardLensTest",
        )
    )


@pytest.fixture()
def live_server_lens(tmp_path, monkeypatch):
    with _lens_station(tmp_path, monkeypatch) as (server, base):
        yield server, base


@pytest.fixture()
def live_server_lens_dev(tmp_path, monkeypatch):
    """The Lens station in developer mode, which adds the edge view."""
    with _lens_station(tmp_path, monkeypatch, developer_mode=True) as (server, base):
        yield server, base


# ===========================================================================
# Unit tests – configuration defaults
# ===========================================================================


@unit
class TestConfigDefaults:
    def test_camera_defaults(self) -> None:
        cam = CameraConfig()
        assert cam.pos_x == 0.0
        assert cam.pos_y == -11.0
        assert cam.pos_z == 6.0
        assert cam.pitch == -22.0
        assert cam.yaw == 0.0
        assert cam.roll == 0.0
        assert cam.fov == 60.0

    def test_grid_defaults(self) -> None:
        grid = GridConfig()
        assert grid.width == 10.0
        assert grid.depth == 6.0
        assert grid.spacing == 1.0
        assert grid.x_offset == 0.0
        assert grid.y_offset == 3.0
        assert grid.z_offset == 0.0

    def test_defaults_project_to_stock_svg_corners(self) -> None:
        cam = CameraConfig()
        grid = GridConfig()
        hw = grid.width / 2.0
        hd = grid.depth / 2.0
        # Stage convention: +X is stage left, so DSL/USL are at +hw and DSR/USR
        # at -hw (matching openfollow/web/routes.py and scripts/gen_stage_svg.py).
        corners = np.array(
            [
                [grid.x_offset + hw, grid.y_offset - hd, grid.z_offset],  # DSL (stage left)
                [grid.x_offset - hw, grid.y_offset - hd, grid.z_offset],  # DSR (stage right)
                [grid.x_offset - hw, grid.y_offset + hd, grid.z_offset],  # USR (stage right)
                [grid.x_offset + hw, grid.y_offset + hd, grid.z_offset],  # USL (stage left)
            ]
        )
        params = np.array([cam.pos_x, cam.pos_y, cam.pos_z, cam.pitch, cam.yaw, cam.roll, cam.fov])
        projected = project_points(params, corners, 1920.0, 1080.0)
        expected = np.array(
            [
                [1628.0, 732.7],  # DSL (stage left → image right)
                [292.0, 732.7],  # DSR (stage right → image left)
                [498.4, 465.7],  # USR
                [1421.6, 465.7],  # USL
            ]
        )
        assert np.allclose(projected, expected, atol=0.5), (
            f"Grid corners drifted from the stock SVG. "
            f"Re-run scripts/gen_stage_svg.py.\n"
            f"expected={expected.tolist()}\ngot={projected.tolist()}"
        )


# ===========================================================================
# The page
# ===========================================================================


@integration
class TestWizardPage:
    def test_wizard_page_renders(self, live_server) -> None:
        _, base = live_server
        status, body = _get(base, "/wizard")
        assert status == 200
        assert "wizard" in body.lower()
        assert len(body) > 1000
        assert '<div id="wizard-video-saved" class="notice success" role="status" style="display:none">' in body

    def test_save_and_next_advances_only_after_save_resolves(self, live_server) -> None:
        # Save & Next must not advance while the old source is still active: the
        # onclick chains wizardNext() onto the save promise's .then, and
        # saveWizardVideoSource returns the fetch so the chain awaits it.
        _, base = live_server
        status, body = _get(base, "/wizard")
        assert status == 200
        assert "saveWizardVideoSource().then(function(){ wizardNext(); })" in body
        assert "saveWizardVideoSource(); wizardNext()" not in body  # fire-and-forget pattern gone
        assert "return fetch('/section/video_source'" in body

    def test_video_source_step_reports_the_last_failure(self, live_server, monkeypatch) -> None:
        server, base = live_server
        video = {
            "failure": "unreachable",
            "failure_text": "Nothing answered at 192.0.2.10:554.",
            "failure_action": "Check the camera is powered.",
            "error_message": "Could not open resource.",
        }
        monkeypatch.setattr(server, "get_runtime_stats", lambda: {"video": video})
        status, body = _get(base, "/wizard")
        assert status == 200

        step = _wizard_step(body, "video")
        assert "Nothing answered at 192.0.2.10:554." in step
        assert "Check the camera is powered." in step
        assert "Could not open resource." not in step
        assert step.index('<div class="notice error"') < step.index('id="wizard-video-source-type"')

    def test_video_source_step_polls_for_a_failure_while_healthy(self, live_server) -> None:
        _, base = live_server
        status, body = _get(base, "/wizard")
        assert status == 200

        step = _wizard_step(body, "video")
        assert '<div class="notice error"' not in step
        poll = re.search(r'<div id="video-source-failure"[^>]*>', step)
        assert poll
        assert 'hx-get="/section/video_source/failure"' in poll.group(0)

    @pytest.mark.parametrize(
        ("lens", "steps"),
        [
            (True, ["video", "lens", "calib", "review"]),
            (False, ["video", "calib", "review"]),
        ],
        ids=["lens on", "lens off"],
    )
    def test_the_steps_in_order(self, tmp_path, monkeypatch, lens: bool, steps: list[str]) -> None:
        with _lens_station(tmp_path, monkeypatch, lens=lens) as (_, base):
            status, body = _get(base, "/wizard")
        assert status == 200
        titles = {"video": "Video Source", "lens": "Lens", "calib": "Camera Calibration", "review": "Review"}
        nav = re.search(r'<nav class="wizard-steps" aria-label="Setup wizard steps">(.*?)</nav>', body, re.S)
        assert nav is not None
        buttons = re.findall(
            r'<button type="button" class="wizard-step-btn" data-step="(\d)" onclick="wizardGo\((\d)\)">'
            r"([^<]*)</button>",
            nav.group(1),
        )
        assert buttons == [(str(i), str(i), f"{i + 1}. {titles[key]}") for i, key in enumerate(steps)]
        assert "window.WIZ_STEPS = [" + ", ".join(f"'{key}'" for key in steps) + "];" in body
        assert f"var LENS_ENABLED = {'true' if lens else 'false'};" in body
        # Every step the script can show has its content; a linear wizard is a nav landmark, not a tablist.
        for key in steps:
            assert f'<div class="wizard-content" id="wizard-step-{key}">' in body
        assert 'role="tablist"' not in WIZARD_TEMPLATE.read_text(encoding="utf-8")
        assert "btn.setAttribute('aria-current', s === step ? 'step' : 'false');" in _wizard_script(body)

    @pytest.mark.parametrize(
        ("step", "help_id"),
        [
            ("video", "wizard-video"),
            ("lens", "wizard-lens"),
            ("calib", "wizard-calibration-simple"),
            ("review", "wizard-review"),
        ],
    )
    def test_each_step_opens_its_own_help(self, live_server, step: str, help_id: str) -> None:
        _, base = live_server
        _, body = _get(base, "/wizard")
        opened = re.findall(r'data-help-open="([\w-]+)"', _wizard_step(body, step))
        assert opened == [help_id]
        assert _get(base, f"/help/{help_id}.html")[0] == 200

    def test_camera_calibration_opens_the_help_of_its_mode(self, live_server) -> None:
        _, base = live_server
        _, body = _get(base, "/wizard")
        assert "var helpId = 'wizard-calibration-' + S.mode;" in _wizard_script(body)
        assert re.findall(r'<input type="radio" name="cal_mode" value="(\w+)"', body) == ["simple", "advanced"]
        for mode in ("simple", "advanced"):
            assert _get(base, f"/help/wizard-calibration-{mode}.html")[0] == 200

    @pytest.mark.parametrize("lens", [True, False], ids=["lens on", "lens off"])
    def test_every_element_the_script_looks_up_is_on_the_page(self, tmp_path, monkeypatch, lens: bool) -> None:
        with _lens_station(tmp_path, monkeypatch, lens=lens) as (_, base):
            _, body = _get(base, "/wizard")
        script = _wizard_script(body)
        looked_up = set(re.findall(r"(?:\$|getElementById)\('([\w-]+)'\)", script))
        built_by_script = set(re.findall(r'id="([\w-]+)"', script))
        on_page = set(re.findall(r'\bid="([\w-]+)"', _without_scripts(body)))
        # Both modes' panels, the picture's layers and the menu are among them.
        assert {
            "cal-simple",
            "cal-advanced",
            "cal-advanced-points",
            "cal-simple-corners",
            "pin-menu",
            "cal-pins",
        } <= looked_up
        assert looked_up - built_by_script - on_page == set()

    def test_the_three_pictures_go_full_screen(self, live_server) -> None:
        _, base = live_server
        _, body = _get(base, "/wizard")

        class Buttons(HTMLParser):
            """Each full-screen button with the ids of the divs around it."""

            def __init__(self) -> None:
                super().__init__()
                self.divs: list[str] = []
                self.found: dict[str, list[str]] = {}

            def handle_starttag(self, tag: str, attrs: list) -> None:
                a = dict(attrs)
                if tag == "div":
                    self.divs.append(a.get("id") or "")
                elif tag == "button" and "data-fs" in a:
                    assert a["aria-label"] == "Full screen" and a["class"] == "fs-btn"
                    self.found[a["data-fs"]] = list(self.divs)

            def handle_endtag(self, tag: str) -> None:
                if tag == "div":
                    self.divs.pop()

        parser = Buttons()
        parser.feed(_without_scripts(body))
        assert set(parser.found) == {"lens", "cal", "review"}
        # The shell takes the picture from ``<key>-container``.
        for key, around in parser.found.items():
            assert around[-1] == f"{key}-container", key
        assert re.search(r'<div id="fs-shell" class="fs-shell" hidden role="dialog" aria-modal="true"', body)

    def test_the_lens_step_loads_and_saves_lens_calibrations(self, live_server) -> None:
        _, base = live_server
        _, body = _get(base, "/wizard")
        step = _wizard_step(body, "lens")
        assert (
            '<button type="button" class="secondary" onclick="lensLoadDialog()">Load lens calibration&hellip;</button>'
            in step
        )
        # Saving waits for a lens worth keeping.
        assert re.search(
            r'<button type="button" class="secondary" id="lens-save" onclick="lensSaveDialog\(\)" disabled>', step
        )
        script = _wizard_script(body)
        assert "window.lensLoadDialog = function" in script and "window.lensSaveDialog = function" in script

    def test_the_page_names_no_route_or_field_the_station_lacks(self, live_server) -> None:
        _, base = live_server
        _, body = _get(base, "/wizard")
        for text in (WIZARD_TEMPLATE.read_text(encoding="utf-8"), body):
            for gone in ("calibration_points", "corner_pins", "/api/wizard/project", "/unproject"):
                assert gone not in text, gone


@unit
class TestWizardNameFields:
    """A field a name is typed into is never one a browser or a password manager fills with the operator's name."""

    @staticmethod
    def _inputs(pattern: str) -> list[str]:
        return re.findall(pattern, WIZARD_TEMPLATE.read_text(encoding="utf-8"))

    def test_point_fields_are_labelled_point_and_never_named_name(self) -> None:
        fields = self._inputs(r'<label for="([^"]+)">Point</label><input ([^>]*)>')
        assert [f[0] for f in fields] == ["pl-' + id + '", "pm-point"]
        for target, attrs in fields:
            assert f'id="{target}"' in attrs
            assert not re.search(r'\b(?:id|name)="[^"]*name', attrs, re.I), attrs
        assert not re.search(r">\s*Name\s*<", WIZARD_TEMPLATE.read_text(encoding="utf-8"))

    def test_every_text_field_is_kept_from_autofill(self) -> None:
        fields = self._inputs(r'<input type="text" ([^>]*)>')
        assert {re.search(r'id="([^"]+)"', f).group(1) for f in fields} == {"pl-' + id + '", "pm-point", "ls-name"}
        for attrs in fields:
            assert 'autocomplete="off"' in attrs and " data-1p-ignore " in attrs and 'data-lpignore="true"' in attrs


# ---------------------------------------------------------------------------
# The saved calibration the wizard starts from
# ---------------------------------------------------------------------------


def _corner(mark_id: int, name: str, x: float, y: float, pin: list[float]) -> CalibrationMarkConfig:
    return CalibrationMarkConfig(id=mark_id, name=name, kind="known", x=x, y=y, z=0.0, pin=pin)


# The default grid's corners: 10 x 6 with its downstage edge on Y 0.
_SIMPLE_MARKS = (
    _corner(1, "DSL", 5.0, 0.0, [0.81, 0.94]),
    _corner(2, "DSR", -5.0, 0.0, [0.14, 0.95]),
    _corner(3, "USR", -5.0, 6.0, [0.37, 0.61]),
    _corner(4, "USL", 5.0, 6.0, [0.66, 0.6]),
)
_ADVANCED_MARKS = (
    _corner(1, "DSL", 6.5, 0.0, [0.8, 0.85]),
    _corner(2, "DSR", -5.5, 0.0, [0.2, 0.85]),
    CalibrationMarkConfig(id=3, name="Truss leg", kind="known", x=0.0, y=10.0, z=1.2),
    CalibrationMarkConfig(
        id=4, name="Spike", kind="triangulated", z=0.0, from_a=1, from_b=2, distance_a=7.0, distance_b=7.5,
        pin=[0.5, 0.6], position=[0.4, 5.1, 0.0],
    ),
)  # fmt: skip


def _as_saved(mark: CalibrationMarkConfig) -> dict:
    keys = ("id", "name", "kind", "x", "y", "z", "from_a", "from_b", "distance_a", "distance_b", "pin", "position")
    return {key: getattr(mark, key) for key in keys}


@integration
class TestWizardSavedCalibration:
    @pytest.mark.parametrize(("mode", "marks"), [("simple", _SIMPLE_MARKS), ("advanced", _ADVANCED_MARKS)])
    def test_the_page_starts_from_the_stations_marks(self, tmp_path, monkeypatch, mode: str, marks: tuple) -> None:
        with _lens_station(tmp_path, monkeypatch, mode=mode, marks=marks) as (server, base):
            _, body = _get(base, "/wizard")
            kept = load_config(server.config_path).camera.calibration_marks
        assert len(kept) == len(marks)
        saved = _saved(body)
        # Pins stay fractions of the snapshot; the page scales them once it has one.
        assert saved["marks"] == [_as_saved(m) for m in marks]
        assert saved["mode"] == mode
        grid = GridConfig()
        assert saved["grid"] == {
            k: getattr(grid, k) for k in ("width", "depth", "spacing", "x_offset", "y_offset", "z_offset")
        }
        assert (saved["fov"], saved["lens"], saved["max_marks"]) == (CameraConfig().fov, [-0.21, 0.03], 12)

    def test_without_a_calibration_the_page_starts_empty(self, live_server) -> None:
        _, body = _get(live_server[1], "/wizard")
        saved = _saved(body)
        assert (saved["mode"], saved["marks"], saved["lens"]) == ("simple", [], [0.0, 0.0])

    def test_with_lens_distortion_off_the_page_starts_pinhole(self, tmp_path, monkeypatch) -> None:
        with _lens_station(tmp_path, monkeypatch, lens=False) as (_, base):
            _, body = _get(base, "/wizard")
        assert _saved(body)["lens"] == [0.0, 0.0]
        assert re.search(r'id="wiz_lens_k1" step="0.005"\s+value="0.0"', body)


# ---------------------------------------------------------------------------
# Apply & Finish: the grid, then the camera, and only then leave
# ---------------------------------------------------------------------------

_SOLVED = {"pos_x": 0.4, "pos_y": -11.2, "pos_z": 6.1, "pitch": -22.5, "yaw": 0.3, "roll": 0.1, "fov": 61.0}
# A 12 x 7 rectangle centred at X 0.5, Y 3.5: its corners DSL, DSR, USR, USL.
_RECT = {"w": 12.0, "d": 7.0, "cx": 0.5, "cy": 3.5, "z": 0.0, "spacing": 1.0}
_RECT_CORNERS = [[6.5, 0.0, 0.0], [-5.5, 0.0, 0.0], [-5.5, 7.0, 0.0], [6.5, 7.0, 0.0]]
_SIMPLE_PINS = [[1536.0, 1015.2], [288.0, 1015.2], [672.0, 658.8], [1248.0, 648.0]]
_SIMPLE_STATE = {
    "mode": "simple",
    "solve": {
        "cam": _SOLVED,
        "worst": 0.8,
        "positions": {},
        "marks": [
            {"id": i + 1, "name": name, "kind": "known", "pos": pos, "pin": pin, "problem": ""}
            for i, (name, pos, pin) in enumerate(
                zip(("DSL", "DSR", "USR", "USL"), _RECT_CORNERS, _SIMPLE_PINS, strict=True)
            )
        ],
    },
}
_ADVANCED_STATE = {
    "mode": "advanced",
    "solve": {
        "cam": _SOLVED,
        "worst": 2.4,
        "positions": {"4": [0.4, 5.1, 0.0]},
        "marks": [
            {"id": 1, "name": "DSL", "kind": "known", "pos": [6.5, 0.0, 0.0], "pin": [1536.0, 918.0], "problem": ""},
            {"id": 2, "name": "DSR", "kind": "known", "pos": [-5.5, 0.0, 0.0], "pin": [384.0, 918.0], "problem": ""},
            {"id": 3, "name": "Truss leg", "kind": "known", "pos": [0.0, 10.0, 1.2], "pin": None, "problem": ""},
            {"id": 4, "name": "Spike", "kind": "triangulated", "a": 1, "b": 2, "da": 7.0, "db": 7.5, "z": 0.0,
             "pin": [960.0, 648.0], "problem": ""},
            {"id": 5, "name": "P5", "kind": "known", "pin": [10.0, 10.0], "problem": "Enter its X, Y and Z."},
        ],
    },
}  # fmt: skip

_APPLY_PAGE = """
var calls = [], shown = [], removed = [];
var STORAGE_KEY = 'openfollow:wizard', imageWidth = 1920, imageHeight = 1080;
var window = { location: { href: '/wizard' }, OpenFollow: { saveError: {
  show: function(box, err, title) { shown.push([err.error, title]); },
  fromResponse: function(r) { return Promise.resolve({ error: 'Refused with ' + r.status + '.' }); },
  UNREACHABLE: { error: 'Unreachable.' } } } };
var document = { querySelector: function() { return {}; } };
var sessionStorage = { removeItem: function(key) { removed.push(key); } };
function fetch(url, opts) {
  calls.push([url, JSON.parse(opts.body)]);
  var a = answers[url];
  if (a === 'down') return Promise.reject(new TypeError('Failed to fetch'));
  return Promise.resolve({ ok: a === 200, status: a });
}
function wizLensPairValid() { return pairValid; }
function wizReadLensCoeff(id) { return id === 'wiz_lens_k1' ? -0.21 : 0.03; }
function calibGrid() { return RECT; }
"""


def _apply(
    state: dict, *, grid=200, camera=200, lens: bool = True, pair_valid: bool = True, pending: bool = False
) -> dict:
    """Press Apply & Finish on a page in ``state``, the station answering each write with its status."""
    return run_wizard_js(
        "(async function() { window.applyAndFinish(); await new Promise(function(r) { setImmediate(r); });"
        " return { calls: calls, shown: shown, removed: removed, href: window.location.href }; })()",
        prelude=_APPLY_PAGE + wizard_function("applyBlocked") + wizard_window_function("applyAndFinish"),
        variables=("APPLY_MAX_PX",),
        S=state,
        LENS_ENABLED=lens,
        RECT=_RECT,
        answers={"/api/config/grid": grid, "/api/config/camera": camera},
        pairValid=pair_valid,
        solvePending=pending,
    )


@needs_node
@unit
class TestWizardApply:
    def test_simple_writes_its_rectangle_as_the_grid_then_the_camera_then_leaves(self) -> None:
        out = _apply(_SIMPLE_STATE)
        assert [url for url, _ in out["calls"]] == ["/api/config/grid", "/api/config/camera"]
        assert out["calls"][0][1] == {"width": 12.0, "depth": 7.0, "x_offset": 0.5, "y_offset": 3.5, "z_offset": 0.0}
        camera = out["calls"][1][1]
        assert {k: camera[k] for k in _SOLVED} == _SOLVED
        assert (camera["calibration_mode"], camera["lens_k1"], camera["lens_k2"]) == ("simple", -0.21, 0.03)
        assert camera["calibration_marks"] == [
            {"id": i + 1, "name": n, "kind": "known", "pin": [x / IMG_W, y / IMG_H], "x": p[0], "y": p[1], "z": p[2]}
            for i, (n, p, (x, y)) in enumerate(
                zip(("DSL", "DSR", "USR", "USL"), _RECT_CORNERS, _SIMPLE_PINS, strict=True)
            )
        ]
        assert (out["shown"], out["removed"], out["href"]) == ([], ["openfollow:wizard"], "/")

    def test_advanced_leaves_the_grid_and_saves_the_marks_it_can_use(self) -> None:
        out = _apply(_ADVANCED_STATE)
        assert [url for url, _ in out["calls"]] == ["/api/config/camera"]
        marks = out["calls"][0][1]["calibration_marks"]
        assert [m["id"] for m in marks] == [1, 2, 3, 4]
        assert marks[2]["pin"] == []
        assert marks[3] == {
            "id": 4, "name": "Spike", "kind": "triangulated", "pin": [0.5, 0.6], "from_a": 1, "from_b": 2,
            "distance_a": 7.0, "distance_b": 7.5, "z": 0.0, "position": [0.4, 5.1, 0.0],
        }  # fmt: skip
        assert (out["calls"][0][1]["calibration_mode"], out["href"]) == ("advanced", "/")

    @pytest.mark.parametrize(
        ("answers", "sent", "said"),
        [
            ({"grid": 400}, ["/api/config/grid"], ["Refused with 400.", "The grid settings were not applied."]),
            (
                {"camera": 400},
                ["/api/config/grid", "/api/config/camera"],
                ["Refused with 400.", "The camera settings were not applied."],
            ),
            ({"grid": "down"}, ["/api/config/grid"], ["Unreachable.", "Not applied."]),
            ({"camera": "down"}, ["/api/config/grid", "/api/config/camera"], ["Unreachable.", "Not applied."]),
        ],
        ids=["grid refused", "camera refused", "grid unreachable", "camera unreachable"],
    )
    def test_a_write_that_fails_says_so_and_stays(self, answers: dict, sent: list[str], said: list[str]) -> None:
        out = _apply(_SIMPLE_STATE, **answers)
        # The camera waits for the grid's answer: its pins would not survive a grid write after it.
        assert [url for url, _ in out["calls"]] == sent
        assert out["shown"] == [said]
        assert (out["removed"], out["href"]) == ([], "/wizard")

    def test_a_folding_lens_pair_sends_nothing(self) -> None:
        out = _apply(_SIMPLE_STATE, pair_valid=False)
        assert out["calls"] == [] and out["href"] == "/wizard"
        assert out["shown"] == [["The lens pair folds the overlay inside the frame.", "Not applied."]]

    def test_with_lens_distortion_off_no_lens_pair_is_sent(self) -> None:
        out = _apply(_SIMPLE_STATE, lens=False, pair_valid=False)
        camera = out["calls"][1][1]
        assert "lens_k1" not in camera and "lens_k2" not in camera and out["href"] == "/"

    @pytest.mark.parametrize(("worst", "sent"), [(19.9, True), (20.1, False)])
    def test_a_solve_with_a_point_far_off_its_pin_is_not_applied(self, worst: float, sent: bool) -> None:
        out = _apply({**_SIMPLE_STATE, "solve": {**_SIMPLE_STATE["solve"], "worst": worst}})
        assert bool(out["calls"]) is sent and (out["href"] == "/") is sent

    def test_while_a_solve_is_still_running_nothing_is_sent(self) -> None:
        out = _apply(_SIMPLE_STATE, pending=True)
        assert out == {"calls": [], "shown": [], "removed": [], "href": "/wizard"}

    def test_without_a_solve_nothing_is_sent(self) -> None:
        out = _apply({"mode": "simple", "solve": {"marks": [], "need": ["4 more pinned points"]}})
        assert out == {"calls": [], "shown": [], "removed": [], "href": "/wizard"}


@needs_node
@integration
def test_what_apply_sends_is_what_the_station_keeps_and_the_wizard_starts_from(live_server) -> None:
    server, base = live_server
    calls = _apply(_SIMPLE_STATE)["calls"]
    for url, body in calls:
        assert _post_json(base, url, body)[0] == 200, url
    cfg = load_config(server.config_path)
    assert (cfg.grid.width, cfg.grid.depth, cfg.grid.x_offset, cfg.grid.y_offset) == (12.0, 7.0, 0.5, 3.5)
    assert cfg.camera.calibration_mode == "simple" and (cfg.camera.pos_x, cfg.camera.fov) == (0.4, 61.0)
    sent = calls[1][1]["calibration_marks"]
    assert [m.pin for m in cfg.camera.calibration_marks] == [m["pin"] for m in sent]
    _, page = _get(base, "/wizard")
    assert [m["pin"] for m in _saved(page)["marks"]] == [m["pin"] for m in sent]


@needs_node
@integration
def test_a_settings_file_the_station_exports_loads_its_lens(live_server_lens) -> None:
    _, base = live_server_lens
    status, text = _get(base, "/api/config/export")
    assert status == 200
    out = run_wizard_js(
        "lensFromSettings(text)",
        functions=("wizLensFoldRadius", "wizLensIsValid", "lensFromSettings"),
        text=text,
    )
    assert out == {"k1": -0.21, "k2": 0.03}


@unit
@pytest.mark.parametrize("k1,k2", [(5.0, -5.0), (-0.4, -0.2), (-0.4, 0.0), (-0.1, 0.02)])
def test_a_lens_pair_a_request_names_reads_as_the_saved_config_reads_it(k1: float, k2: float) -> None:
    """A folding pair is pulled back toward the fold, never dropped to pinhole, exactly as a save is."""
    from openfollow.web.routes import _wizard_lens_coeffs

    saved = CameraConfig(lens_k1=k1, lens_k2=k2)
    assert _wizard_lens_coeffs({"lens_k1": k1, "lens_k2": k2}) == (saved.lens_k1, saved.lens_k2)
    assert _wizard_lens_coeffs({"lens_k1": k1, "lens_k2": k2}) != (0.0, 0.0)
    # A wide lens passes through unclamped.
    assert _wizard_lens_coeffs({"lens_k1": -0.47, "lens_k2": 0.25}) == pytest.approx((-0.47, 0.25))


# ---------------------------------------------------------------------------
# Lens step: lines that are straight in reality -> k1 / k2
# ---------------------------------------------------------------------------


def _stripe_image(w: int = 1920, h: int = 1080) -> np.ndarray:
    rng = np.random.default_rng(0)
    img = np.full((h, w), 40.0)
    yy, xx = np.mgrid[0:h, 0:w]
    img[np.abs(yy - (600.0 + 0.02 * xx)) <= 7] = 200.0
    return np.clip(img + rng.normal(0.0, 4.0, img.shape), 0, 255)


def _snap_band(img: np.ndarray, p0, p1) -> dict:
    """The band the wizard sends: the luma along the chord, rectified."""
    from tests._lens_band import band_payload, cut_band

    return band_payload(cut_band(img, p0, p1))


def _warped_line(p0, p1, k1: float, k2: float, n: int = 5) -> list[list[float]]:
    from openfollow.scene.solver import invert_overlay_distortion

    ends = invert_overlay_distortion(np.array([p0, p1], dtype=np.float64), IMG_W, IMG_H, k1, k2)
    t = np.linspace(0.0, 1.0, n)[:, None]
    seen = apply_overlay_distortion(ends[0] + t * (ends[1] - ends[0]), IMG_W, IMG_H, k1, k2)
    return seen.tolist()


_LENS_EDGE_LINES = [
    ((52.0, 12.0), (1868.0, 20.0)),
    ((52.0, 1068.0), (1868.0, 1062.0)),
    ((12.0, 52.0), (18.0, 1028.0)),
    ((1908.0, 52.0), (1900.0, 1028.0)),
    ((120.0, 100.0), (1800.0, 1000.0)),
]


@integration
class TestWizardLensSnapEndpoint:
    def test_snaps_the_five_points_onto_the_edge(self, live_server) -> None:
        _, base = live_server
        img = _stripe_image()
        top = -7.0
        p0 = (200.0, 600.0 + 0.02 * 200.0 + top + 5.0)
        p1 = (1700.0, 600.0 + 0.02 * 1700.0 + top - 4.0)
        status, data = _post_json(
            base,
            "/api/wizard/lens/snap",
            {"image_width": 1920, "image_height": 1080, "p0": p0, "p1": p1, "band": _snap_band(img, p0, p1)},
        )
        assert status == 200
        assert len(data["points"]) == 5
        for p in data["points"]:
            assert p["snapped"] is True
            assert abs(p["y"] - (600.0 + 0.02 * p["x"] + top)) < 0.75

    def test_a_covered_point_is_reported_unsnapped(self, live_server) -> None:
        _, base = live_server
        img = _stripe_image()
        img[:, 900:1000] = 40.0
        p0 = (200.0, 600.0 + 0.02 * 200.0 - 7.0)
        p1 = (1700.0, 600.0 + 0.02 * 1700.0 - 7.0)
        _, data = _post_json(
            base,
            "/api/wizard/lens/snap",
            {"image_width": 1920, "image_height": 1080, "p0": p0, "p1": p1, "band": _snap_band(img, p0, p1)},
        )
        assert [p["snapped"] for p in data["points"]] == [True, True, False, True, True]

    def _good_body(self) -> dict:
        img = _stripe_image()
        p0, p1 = (200.0, 596.0), (1700.0, 627.0)
        return {"image_width": 1920, "image_height": 1080, "p0": p0, "p1": p1, "band": _snap_band(img, p0, p1)}

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda b: b.pop("band"),
            lambda b: b.__setitem__("band", "wide"),
            lambda b: b["band"].update(data="not base64!"),
            lambda b: b["band"].update(rows=b["band"]["rows"] + 2, half=b["band"]["half"] + 1),
            lambda b: b["band"].update(rows=b["band"]["rows"] + 1),
            lambda b: b["band"].update(step=0),
            lambda b: b["band"].update(step=99),
            lambda b: b["band"].update(half=8, rows=17),
            lambda b: b["band"].update(half=10_000, rows=20_001),
            lambda b: b["band"].update(cols=1),
            lambda b: b["band"].update(cols=100_000),
            lambda b: b["band"].update(cols=b["band"]["cols"] + 1),
            lambda b: b["band"].update(data=12345),
            lambda b: b["band"].pop("step"),
            lambda b: b.update(p1=[b["p1"][0] - 400.0, b["p1"][1]]),
            lambda b: b.update(p0=[1.0]),
            lambda b: b.update(p0=["a", "b"]),
            lambda b: b.update(p0=[float("nan"), 1.0]),
            lambda b: b.update(p1=b["p0"]),
            lambda b: b.update(image_width=0),
            lambda b: b.update(image_height="tall"),
            lambda b: b.update(image_width=1e170, image_height=1e170),
            lambda b: b.update(image_width=20_000),
            lambda b: b.update(image_width=True),
            lambda b: b["band"].update(step=float("inf")),
            lambda b: b["band"].update(half=float("-inf")),
            lambda b: b["band"].update(step=True),
            lambda b: b["band"].update(step=1.5),
            lambda b: b.update(p0=["200", "596"]),
        ],
        ids=[
            "no-band",
            "band-not-an-object",
            "bad-base64",
            "size-mismatch",
            "rows-not-two-half-plus-one",
            "zero-step",
            "huge-step",
            "half-too-small",
            "half-too-large",
            "one-column",
            "too-many-columns",
            "columns-do-not-match-data",
            "data-not-a-string",
            "missing-step",
            "band-cut-for-another-line",
            "p0-not-a-pair",
            "p0-non-numeric",
            "p0-nan",
            "zero-length-line",
            "zero-canvas",
            "string-canvas",
            "astronomical-canvas",
            "canvas-too-wide",
            "boolean-canvas",
            "infinite-step",
            "negative-infinite-half",
            "boolean-step",
            "fractional-step",
            "string-coordinates",
        ],
    )
    def test_malformed_body_returns_400(self, live_server, mutate) -> None:
        _, base = live_server
        body = self._good_body()
        mutate(body)
        status, data = _post_json(base, "/api/wizard/lens/snap", body)
        assert status == 400, data
        assert "error" in data

    def test_non_object_body_returns_400(self, live_server) -> None:
        _, base = live_server
        status, _ = _post_json(base, "/api/wizard/lens/snap", [1, 2, 3])  # type: ignore[arg-type]
        assert status == 400

    def test_invalid_json_returns_400(self, live_server) -> None:
        _, base = live_server
        req = urllib.request.Request(
            f"{base}/api/wizard/lens/snap",
            data=b"{not json",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req, timeout=5)
        assert exc.value.code == 400


@integration
class TestWizardLensEdgesEndpoint:
    def _body(self, with_map: bool = False) -> dict:
        from openfollow.scene.edge_chains import edge_map_scale
        from tests._lens_band import edges_payload, scaled_luma

        scale = edge_map_scale(1920)
        return edges_payload(scaled_luma(_stripe_image(), scale), scale, 1920, 1080, with_map=with_map)

    def test_suggests_the_stripe_with_five_sample_points(self, live_server) -> None:
        _, base = live_server
        status, data = _post_json(base, "/api/wizard/lens/edges", self._body())
        assert status == 200
        assert data["edges"] is None
        assert len(data["candidates"]) >= 1
        best = data["candidates"][0]
        assert len(best["samples"]) == 5 and len(best["points"]) >= 2
        for x, y in best["samples"]:
            assert abs(abs(y - (600.0 + 0.02 * x)) - 7.0) < 3.0
        assert best["length"] > 1500.0 and best["strength"] > 8.0

    def test_the_developer_map_is_returned_on_request(self, live_server) -> None:
        _, base = live_server
        status, data = _post_json(base, "/api/wizard/lens/edges", self._body(with_map=True))
        assert status == 200
        edges = data["edges"]
        assert (edges["width"], edges["height"]) == (960, 540)
        assert len(base64.b64decode(edges["data"])) == 960 * 540

    @pytest.mark.parametrize("with_map", ["false", 1, "yes"])
    def test_only_true_asks_for_the_developer_map(self, live_server, with_map) -> None:
        _, base = live_server
        status, data = _post_json(base, "/api/wizard/lens/edges", {**self._body(), "with_map": with_map})
        assert status == 200
        assert data["edges"] is None

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda b: b.pop("data"),
            lambda b: b.update(data=123),
            lambda b: b.update(data="not base64!"),
            lambda b: b.update(data=b["data"][:-8]),
            lambda b: b.update(scale=0),
            lambda b: b.update(scale=99),
            lambda b: b.update(scale=1),
            lambda b: b.update(width=b["width"] + 1),
            lambda b: b.update(height=b["height"] - 1),
            # A tall snapshot keeps scale 1, so only the pixel cap refuses it.
            lambda b: b.update(scale=1, width=1280, height=2000, image_width=1280, image_height=2000),
            lambda b: b.update(image_width=0),
            lambda b: b.update(image_height="tall"),
            lambda b: b.pop("scale"),
            lambda b: b.update(scale=float("inf")),
            lambda b: b.update(scale=2.5),
            lambda b: b.update(scale=1, width=1920, height=1080, data=base64.b64encode(bytes(1920 * 1080)).decode()),
        ],
        ids=[
            "no-data",
            "data-not-a-string",
            "bad-base64",
            "data-too-short",
            "zero-scale",
            "huge-scale",
            "scale-does-not-match-size",
            "width-off-by-one",
            "height-off-by-one",
            "too-large",
            "zero-canvas",
            "string-canvas",
            "missing-scale",
            "infinite-scale",
            "fractional-scale",
            "unscaled-snapshot",
        ],
    )
    def test_malformed_body_returns_400(self, live_server, mutate) -> None:
        _, base = live_server
        body = self._body()
        mutate(body)
        status, data = _post_json(base, "/api/wizard/lens/edges", body)
        assert status == 400, data
        assert "error" in data

    def test_non_object_body_returns_400(self, live_server) -> None:
        _, base = live_server
        status, _ = _post_json(base, "/api/wizard/lens/edges", [1, 2, 3])  # type: ignore[arg-type]
        assert status == 400

    def test_invalid_json_returns_400(self, live_server) -> None:
        _, base = live_server
        req = urllib.request.Request(
            f"{base}/api/wizard/lens/edges",
            data=b"{not json",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req, timeout=5)
        assert exc.value.code == 400


@integration
class TestWizardLensFitEndpoint:
    def test_fit_recovers_the_pair_and_describes_each_line(self, live_server) -> None:
        _, base = live_server
        k1, k2 = -0.3, 0.05
        lines = [{"points": _warped_line(a, b, k1, k2)} for a, b in _LENS_EDGE_LINES]
        status, data = _post_json(
            base, "/api/wizard/lens/fit", {"image_width": IMG_W, "image_height": IMG_H, "lines": lines}
        )
        assert status == 200
        assert data["k1"] == pytest.approx(k1, abs=5e-3)
        assert data["k2"] == pytest.approx(k2, abs=5e-3)
        assert data["k2_fitted"] is True
        assert data["rating"] in ("low", "medium", "okay", "good", "excellent")
        assert isinstance(data["hint"], str)
        assert data["uncertainty_px"] is not None
        assert len(data["lines"]) == len(lines)
        for line in data["lines"]:
            assert line["misfit"] is False
            assert line["rms_px"] < 0.1
            assert len(line["curve"]) == 24

    def test_bare_point_lists_are_accepted(self, live_server) -> None:
        _, base = live_server
        lines = [_warped_line(a, b, -0.2, 0.0) for a, b in _LENS_EDGE_LINES[:3]]
        status, data = _post_json(
            base, "/api/wizard/lens/fit", {"image_width": IMG_W, "image_height": IMG_H, "lines": lines}
        )
        assert status == 200
        assert data["k1"] == pytest.approx(-0.2, abs=0.02)

    def test_zero_coefficients_fit_straight_lines_as_pinhole(self, live_server) -> None:
        _, base = live_server
        lines = [_warped_line(a, b, 0.0, 0.0) for a, b in _LENS_EDGE_LINES]
        _, data = _post_json(
            base, "/api/wizard/lens/fit", {"image_width": IMG_W, "image_height": IMG_H, "lines": lines}
        )
        assert data["k1"] == pytest.approx(0.0, abs=2e-3)
        assert data["k2"] == pytest.approx(0.0, abs=2e-3)
        # The predicted curves of a pinhole fit are the straight lines themselves.
        for line, (a, b) in zip(data["lines"], _LENS_EDGE_LINES, strict=True):
            first, last = line["curve"][0], line["curve"][-1]
            assert first == pytest.approx(list(a), abs=0.05)
            assert last == pytest.approx(list(b), abs=0.05)

    @pytest.mark.parametrize(
        "body",
        [
            {"image_width": IMG_W, "image_height": IMG_H, "lines": []},
            {"image_width": IMG_W, "image_height": IMG_H, "lines": "none"},
            {"image_width": IMG_W, "image_height": IMG_H},
            {"image_width": IMG_W, "image_height": IMG_H, "lines": [{"points": [[0, 0], [100, 0]]}]},
            {"image_width": IMG_W, "image_height": IMG_H, "lines": [{"points": [[0, 0], ["a", 0], [100, 0]]}]},
            {"image_width": IMG_W, "image_height": IMG_H, "lines": [{"points": [[0, 0], [1], [100, 0]]}]},
            {"image_width": IMG_W, "image_height": IMG_H, "lines": [{"points": "abc"}]},
            {"image_width": IMG_W, "image_height": IMG_H, "lines": [{"points": [[0, 0], [1, 1], [2, 2]]}]},
            {"image_width": 0, "image_height": IMG_H, "lines": [{"points": [[0, 0], [50, 0], [100, 0]]}]},
            {"image_width": IMG_W, "image_height": float("nan"), "lines": [{"points": [[0, 0], [50, 0], [100, 0]]}]},
            {
                "image_width": IMG_W,
                "image_height": IMG_H,
                "lines": [{"points": [[0, 0], [50, float("inf")], [100, 0]]}],
            },
            {"image_width": IMG_W, "image_height": IMG_H, "lines": [{"points": [[0, 2], [50, 0], [100, 0]]}] * 33},
            {"image_width": IMG_W, "image_height": IMG_H, "lines": [{"points": [[x, 0] for x in range(0, 170, 10)]}]},
            {"image_width": IMG_W, "image_height": IMG_H, "lines": [{"points": ["12", [50, 0], [100, 0]]}]},
            {"image_width": IMG_W, "image_height": IMG_H, "lines": [{"points": [[0, 0, 0], [50, 0, 0], [100, 0, 0]]}]},
            {"image_width": IMG_W, "image_height": IMG_H, "lines": [{"points": [[True, 0], [50, 0], [100, 0]]}]},
            {"image_width": 1e170, "image_height": 1e170, "lines": [{"points": [[0, 0], [50, 0], [100, 0]]}]},
            {"image_width": IMG_W, "image_height": IMG_H, "lines": [{"points": [[10**400, 0], [50, 0], [100, 0]]}]},
        ],
        ids=[
            "no-lines",
            "lines-not-a-list",
            "lines-missing",
            "two-points",
            "non-numeric-point",
            "short-point",
            "points-not-a-list",
            "degenerate-line",
            "zero-canvas",
            "nan-canvas",
            "non-finite-point",
            "too-many-lines",
            "too-many-points",
            "string-point",
            "three-coordinates",
            "boolean-coordinate",
            "astronomical-canvas",
            "integer-past-float-range",
        ],
    )
    def test_malformed_body_returns_400(self, live_server, body: dict) -> None:
        _, base = live_server
        status, data = _post_json(base, "/api/wizard/lens/fit", body)
        assert status == 400, data
        assert "error" in data

    def test_non_object_body_returns_400(self, live_server) -> None:
        _, base = live_server
        status, _ = _post_json(base, "/api/wizard/lens/fit", [[0, 0]])  # type: ignore[arg-type]
        assert status == 400

    @pytest.mark.parametrize(
        "raw",
        [b"[not json", b'{"image_width": 1' + b"0" * 5000 + b"}", b"[" * 100_000 + b"]" * 100_000],
        ids=["not-json", "integer-past-the-digit-limit", "nested-past-the-recursion-limit"],
    )
    def test_invalid_json_returns_400(self, live_server, raw: bytes) -> None:
        _, base = live_server
        req = urllib.request.Request(
            f"{base}/api/wizard/lens/fit",
            data=raw,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req, timeout=5)
        assert exc.value.code == 400


@integration
@needs_node
class TestWizardScriptParses:
    """The page's own script, rendered with every toggle: an edit that breaks its syntax stops the whole wizard."""

    @pytest.mark.parametrize(("lens", "developer"), [(True, False), (False, False), (True, True)])
    def test_every_inline_script_parses(self, tmp_path, monkeypatch, lens: bool, developer: bool) -> None:
        with _lens_station(tmp_path, monkeypatch, developer_mode=developer, lens=lens) as (_, base):
            status, page = _get(base, "/wizard")
        assert status == 200
        scripts = re.findall(r"<script>(.*?)</script>", page, re.S)
        assert len(scripts) >= 3
        for i, source in enumerate(scripts):
            assert_script_parses(source, tmp_path / f"wizard-{i}.js")


@integration
class TestWizardLensStepPage:
    def test_the_lens_step_carries_the_stored_pair_under_fine_tune(self, live_server_lens) -> None:
        _, base = live_server_lens
        status, body = _get(base, "/wizard")
        assert status == 200
        assert 'id="wiz_lens_k1"' in body and 'type="hidden" id="wiz_lens_k1"' not in body
        # The sliders carry the stored pair and sit under the result as Fine-tune.
        assert 'id="wiz_lens_k1" step="0.005"' in body
        assert 'value="-0.21"' in body and 'value="0.03"' in body
        assert "Fine-tune" in body
        # No step is referred to by number anywhere, since the Lens step shifts the numbering.
        assert re.search(r"Step \d", body) is None

    def test_the_operator_never_sees_a_coefficient_name(self, live_server_lens) -> None:
        """The pair is Barrel / fisheye and Edge fit everywhere an operator reads: never k1 or k2."""
        _, base = live_server_lens
        _, page = _get(base, "/wizard")
        # The script keeps the coefficient names; what the page shows and says must not.
        wizard = re.sub(r"<script.*?</script>", "", page, flags=re.S)
        assert "Barrel / fisheye" in wizard and "Edge fit" in wizard
        for leak in ("(k1)", "(k2)", "Lens k1", "Lens k2", "k1 or k2", "k2 not"):
            assert leak not in wizard, leak
        for text in (
            "'Barrel / fisheye ' + fmt(k1, 3) + ' · Edge fit '",
            "' \\u2013 barrel / fisheye '",
            "', edge fit '",
            "'0 (not measured)'",
            "Bring either lens value closer to 0",
        ):
            assert text in page, text
        _, camera = _get(base, "/section/camera")
        assert "<label>Barrel / fisheye</label>" in camera and "<label>Edge fit</label>" in camera
        assert "(k1)" not in camera and "(k2)" not in camera

    def test_the_lens_step_speaks_to_a_screen_reader(self, live_server_lens) -> None:
        _, body = _get(live_server_lens[1], "/wizard")
        # A failed fit interrupts; a rating or a misfit waits its turn.
        assert re.search(r'<div id="lens-status"[^>]*role="alert"', body)
        assert re.search(r'<div id="lens-misfit"[^>]*role="status"', body)
        assert re.search(r'<div id="lens-result"[^>]*role="status"', body)
        # Deleting the last line leaves the keys on the picture.
        assert re.search(r'<svg id="lens-overlay"[^>]*tabindex="-1"', body)

    def test_the_edge_view_is_a_developer_control(self, live_server_lens, live_server_lens_dev) -> None:
        _, plain = _get(live_server_lens[1], "/wizard")
        _, dev = _get(live_server_lens_dev[1], "/wizard")
        assert 'id="lens-show-edges"' in dev and "Show edges" in dev and "var LENS_DEV = true;" in dev
        assert 'id="lens-edges"' in dev
        assert 'id="lens-show-edges"' not in plain and "var LENS_DEV = false;" in plain


@unit
class TestWizardLensTemplate:
    def _src(self) -> str:
        return WIZARD_TEMPLATE.read_text(encoding="utf-8")

    def test_no_script_or_button_carries_a_step_number(self) -> None:
        src = self._src()
        assert "window.WIZ = {" in src and "window.WIZ_STEPS" in src
        assert not re.search(r"wizard-step-\d", src)
        # Only the generated nav buttons call wizardGo with an index; every other button steps relatively.
        assert re.findall(r'onclick="wizardGo\((\d+|\{\{_i\}\})\)"', src) == ["{{_i}}"]
        assert "wizardNext()" in src and "wizardPrev()" in src
        # A session names its step by key, so a page with another set of steps restores the right one.
        assert "stepKey: WIZ_STEPS[currentStep]" in src

    def test_snapshot_revokes_previous_blob_url(self) -> None:
        load = wizard_window_function("loadSnapshot")
        assert "revokeSnapshotUrl(previousUrl);" in load and "revokeSnapshotUrl(nextUrl);" in load

    def test_band_columns_mirror_the_server(self) -> None:
        # One column per step pixels of the chord, both ends included: edge_snap.band_columns.
        assert "Math.ceil(len / step) + 1" in self._src()

    def test_suggestions_are_requested_for_the_lens_step_and_tapped_into_lines(self) -> None:
        src = self._src()
        assert "'/api/wizard/lens/edges'" in src
        assert "if (currentStep === WIZ.lens) lensRequestEdges();" in src
        assert 'id="lens-candidates"' in src
        down = re.search(r"overlay\.addEventListener\('pointerdown', function\(e\) \{(.*?)\n    \}\);", src, re.S)
        assert down is not None and "lensAddCandidate(+candidate.dataset.candidate)" in down.group(1)
        # A deleted or cleared line gives its suggestion back.
        for name in ("lensDeleteSelectedLine", "lensClearLines"):
            body = re.search(r"window\." + name + r" = function\(\) \{(.*?)\n  \};\n", src, re.S)
            assert body is not None and "lensReleaseCandidate" in body.group(1), name

    def test_a_middle_point_without_an_edge_starts_switched_off(self) -> None:
        src = self._src()
        assert "for (var j = 1; j <= 3; j++) line.mids[j - 1].on = line.snapped[j];" in src
        assert "starts switched off" in src

    def test_lines_persist_in_the_session_and_clear_on_a_resolution_change(self) -> None:
        src = self._src()
        assert "lensLines: lensLines," in wizard_function("saveToSession")
        assert "raw.lensLines.filter(lensRestoredLine)" in wizard_function("restoreFromSession")
        assert "lensImageSize[0] !== imageWidth || lensImageSize[1] !== imageHeight" in src
        assert "resolution changed" in src

    def test_a_click_on_a_point_keeps_the_keys_on_it(self) -> None:
        """Rebuilding the handles on pointer-up must not drop the focus the keys need."""
        src = self._src()
        render = re.search(r"function renderLens\(\) \{(.*?)\n  \}\n", src, re.S)
        assert render is not None
        body = render.group(1)
        assert body.index("g.contains(document.activeElement)") < body.index("g.innerHTML = ''")
        assert body.index("g.innerHTML = ''") < body.index("if (hadFocus) lensFocusSelected();")
        focus = re.search(r"function lensFocusSelected\(\) \{(.*?)\n  \}\n", src, re.S)
        assert focus is not None and "focus({ preventScroll: true })" in focus.group(1)
        # The handler that selects a point by pointer relies on the same helper.
        down = re.search(r"overlay\.addEventListener\('pointerdown', function\(e\) \{(.*?)\n    \}\);", src, re.S)
        assert down is not None and "lensFocusSelected();" in down.group(1)

    def test_only_the_latest_fit_and_an_unmoved_line_take_an_answer(self) -> None:
        src = self._src()
        fit = re.search(r"function runLensFit\(\) \{(.*?)\n  \}\n", src, re.S)
        assert fit and "var seq = ++lensFitSeq;" in fit.group(1)
        assert "if (seq !== lensFitSeq) return;" in fit.group(1)
        # Lines cleared by a resolution change take no answer meant for the old ones.
        snapshot = re.search(r"function lensOnSnapshot\(\) \{(.*?)\n  \}\n", src, re.S)
        assert snapshot and "lensFitSeq++;" in snapshot.group(1)
        snap = re.search(r"function lensSnapLine\(line\) \{(.*?)\n  \}\n", src, re.S)
        assert snap and "var sent = JSON.stringify([line.p0, line.p1, line.mids]);" in snap.group(1)
        assert "JSON.stringify([line.p0, line.p1, line.mids]) !== sent) return;" in snap.group(1)

    def test_a_low_fit_is_shown_and_not_applied(self) -> None:
        src = self._src()
        fit = re.search(r"function runLensFit\(\) \{(.*?)\n  \}\n", src, re.S)
        assert fit
        written = fit.group(1).index("wizWriteLensCoeff('wiz_lens_k1'")
        assert fit.group(1).rfind("if (lensFitApplies(lensFit)) {", 0, written) != -1
        assert "if (lensFitApplies(lensFit)) onLensCoeffChanged();" in fit.group(1)
        assert "function lensFitApplies(fit) { return fit.rating !== 'low'; }" in src
        assert "(lensFitApplies(lensFit) ? '' : ' \\u2013 not applied')" in src
        # Review rates the pair only while it is the fit's own.
        review = wizard_function("reviewLensCoverage")
        assert "if (lensPairIsTheFits(k1, k2)) {" in review and "'Fine-tuned'" in review

    def test_a_tap_is_judged_in_screen_pixels_and_a_cancel_traces_nothing(self) -> None:
        src = self._src()
        assert "var far = Math.hypot(e.clientX - lensDrag.client[0], e.clientY - lensDrag.client[1]);" in src
        assert "if (far > LENS_TAP_SLOP_PX) lensDrag.moved = true;" in src
        assert "if (!drag.moved && e.type !== 'pointercancel') lensTraceClick(" in src

    def test_a_written_pair_widens_its_slider(self) -> None:
        src = self._src()
        write = re.search(r"function wizWriteLensCoeff\(id, value\) \{(.*?)\n  \}\n", src, re.S)
        assert write and "range.min = Math.min(parseFloat(range.min), v);" in write.group(1)
        assert "range.max = Math.max(parseFloat(range.max), v);" in write.group(1)

    def test_a_session_from_another_page_keeps_its_values(self) -> None:
        body = wizard_function("restoreFromSession")
        # A restored session is never a fresh visit, whose defaults would overwrite it.
        assert body.rstrip().endswith("return true;\n    } catch (e) { return false; }\n  }")

    def test_suggestions_and_points_work_from_the_keyboard(self) -> None:
        src = self._src()
        assert "svgEl('g', { tabindex: '0', role: 'button', 'aria-label': 'Suggested edge ' + (i + 1) })" in src
        keys = re.search(r"overlay\.addEventListener\('keydown', function\(e\) \{(.*?)\n    \}\);", src, re.S)
        assert keys
        candidate = keys.group(1).split("var handle =")[0]
        assert "e.key === 'Enter' || e.key === ' '" in candidate and "lensAddCandidate(" in candidate
        # Every point says what it is.
        assert "'Line ' + (i + 1) + (j === 0 ? ', start' : j === 4 ? ', end' : ', middle point ' + j)" in src
        # A point reached with Tab is the selected one, so the toolbar acts on what has the focus.
        focus = re.search(r"overlay\.addEventListener\('focusin', function\(e\) \{(.*?)\n    \}\);", src, re.S)
        assert focus and "lensSelected = { line: line, point: point };" in focus.group(1)
        # Delete leaves the keys on the picture.
        delete = wizard_window_function("lensDeleteSelectedLine")
        assert "document.getElementById('lens-overlay').focus({ preventScroll: true })" in delete

    def test_handles_and_labels_are_sized_in_css_pixels(self) -> None:
        src = self._src()
        # The viewBox is the snapshot's own resolution, which a phone shows a 1080p frame at a fifth of.
        assert "width: 44 * px, height: 44 * px" in wizard_function("pointHandle")
        assert "pointHandle(lensPointPos(line, j), j === 0 || j === 4 ? 4 : 3.5, px)" in wizard_function("lensHandle")
        assert "imageWidth / width" in src and "'font-size': 11 * px" in src
        # A rotated phone or a resized window draws them again at the new size.
        assert "window.addEventListener('resize'" in src

    def test_middle_points_move_across_the_line_only(self) -> None:
        src = self._src()
        # Dragging a middle point sets its perpendicular offset; the keyboard keeps only the nudge's perpendicular part.
        assert "function lensSetMidFromPos(line, j, pos)" in src
        assert "line.mids[j - 1].off += dx * f.nx + dy * f.ny;" in src
        for key in ("ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Delete", "Escape"):
            assert f"'{key}'" in src
        assert "e.key === ' '" in src

    def test_fit_runs_debounced_after_a_release_and_writes_the_sliders(self) -> None:
        src = self._src()
        assert "setTimeout(runLensFit, LENS_FIT_DEBOUNCE_MS)" in src
        assert "'/api/wizard/lens/fit'" in src and "'/api/wizard/lens/snap'" in src
        assert "wizLensShownPair(Number(res.data.k1), Number(res.data.k2))" in src
        assert "wizWriteLensCoeff('wiz_lens_k1', shown[0])" in src
        assert "Point off" in src and "Delete line" in src and "Clear lines" in src

    def test_rating_levels_follow_the_status_language(self) -> None:
        src = self._src()
        levels = "{ low: 'caution', medium: 'caution', okay: 'info', good: 'success', excellent: 'success' }"
        assert "LENS_RATING_LEVEL = " + levels in src
        assert "LENS_RATING_SEGMENTS = { low: 1, medium: 2, okay: 3, good: 4, excellent: 5 }" in src
        assert "This line doesn't fit the others – is it really straight?" in src
