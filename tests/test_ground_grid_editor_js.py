# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The Ground Grid section's plan editor, run in node on the fake DOM."""

from __future__ import annotations

import json
import shutil
import subprocess  # nosec B404 - runs the local node binary on a fixed script
from pathlib import Path
from typing import Any

import pytest
from bottle import template

from openfollow.configuration import AppConfig
from openfollow.web import server as _server_module  # noqa: F401 – registers the template path

pytestmark = [pytest.mark.unit, pytest.mark.skipif(shutil.which("node") is None, reason="needs node")]

_TESTS = Path(__file__).parent
_STATIC = _TESTS.parent / "openfollow" / "web" / "static" / "js"

_PAGE = """
El.prototype.getContext = function () {
    return new Proxy({}, {
        get: function (_, k) {
            if (k === 'measureText') return function () { return { width: 10 }; };
            return function () {};
        },
        set: function () { return true; },
    });
};
window.addEventListener = function () {};
station = function (req) {
    if (req.url === '/api/zones') {
        var grid = { width: 10, depth: 6, spacing: 1, x_offset: 0, y_offset: 3 };
        return { body: { zones: [], markers: [], grid: grid } };
    }
    return { body: '' };
};
"""

_SETUP = """
var canvas = document.getElementById('ground-grid-canvas');
canvas.width = 1280; canvas.height = 720;
canvas.getBoundingClientRect = function () { return { left: 0, top: 0, width: 1280, height: 720 }; };
function at(type, x, y) { canvas.dispatchEvent(makeEvent(type, { clientX: x, clientY: y })); }
function button(id) { document.getElementById(id).click(); }
function stored() { return JSON.parse(document.getElementById('ground-grid-vertices').value); }
function status() { return document.getElementById('ground-grid-status').textContent; }
"""


def _cfg(vertices: list | None = None) -> AppConfig:
    cfg = AppConfig()
    cfg.ground_grid.vertices = vertices or []
    return cfg


def _editor(steps: str, cfg: AppConfig | None = None, field: str | None = None) -> Any:
    rendered = template("partials/ground_grid", config=cfg or _cfg())
    start = rendered.index("<script>")
    markup = rendered[:start] + "</form>"
    script = rendered[start + len("<script>") : rendered.index("</script>", start)]
    override = f"document.getElementById('ground-grid-vertices').value = {json.dumps(field)};" if field else ""
    source = "\n".join(
        [
            (_TESTS / "_fake_dom.js").read_text(encoding="utf-8"),
            _PAGE,
            (_STATIC / "polygon-plan.js").read_text(encoding="utf-8"),
            f"parseHTML({json.dumps(markup)}, document.body);",
            _SETUP,
            override,
            script,
            "(async function () { var out = {}; await settle();",
            steps,
            "process.stdout.write(JSON.stringify(out)); })()"
            ".catch(function (e) { console.error(e && e.stack || e); process.exit(1); });",
        ]
    )
    node = shutil.which("node")
    assert node is not None
    done = subprocess.run([node, "-e", source], capture_output=True, text=True, timeout=20)  # nosec B603
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


_SQUARE = [[0.0, 2.0], [2.0, 2.0], [2.0, 4.0], [0.0, 4.0]]


def test_drawing_a_polygon_fills_the_form_field() -> None:
    out = _editor(
        """
        out.before = status();
        button('ground-grid-draw-btn');
        at('click', 640, 360); at('click', 740, 360); at('click', 740, 260);
        button('ground-grid-finish-btn');
        out.verts = stored(); out.after = status();
        """
    )
    assert out["before"] == "No polygon yet."
    assert len(out["verts"]) == 3
    assert out["verts"][0] == [pytest.approx(0.0), pytest.approx(3.0)]
    assert out["after"] == "Polygon changed. Save to keep it."


def test_dragging_a_corner_updates_the_form_field() -> None:
    # The fitted view spans 12 x 8 m around (0, 3) at 85 px per metre: (2, 4) is (810, 275).
    out = _editor(
        """
        out.before = status();
        at('mousedown', 810, 275); at('mousemove', 895, 275); at('mouseup', 895, 275);
        out.verts = stored(); out.after = status();
        """,
        _cfg(_SQUARE),
    )
    assert out["before"] == ""
    assert out["verts"][2] == [pytest.approx(3.0), pytest.approx(4.0)]
    assert out["verts"][:2] == _SQUARE[:2]
    assert out["after"] == "Polygon changed. Save to keep it."


def test_clear_empties_the_polygon() -> None:
    out = _editor(
        "button('ground-grid-clear-btn'); out.verts = stored(); "
        "out.clearDisabled = document.getElementById('ground-grid-clear-btn').disabled;",
        _cfg(_SQUARE),
    )
    assert out == {"verts": [], "clearDisabled": True}


def test_an_unreadable_field_starts_with_no_polygon() -> None:
    out = _editor("out.status = status();", field="not json")
    assert out["status"] == "No polygon yet."


def test_the_plan_starts_on_the_saved_main_grid() -> None:
    cfg = _cfg()
    cfg.grid.width, cfg.grid.x_offset = 14.0, 1.5
    rendered = template("partials/ground_grid", config=cfg)
    assert '"width": 14.0' in rendered
    assert '"x_offset": 1.5' in rendered
