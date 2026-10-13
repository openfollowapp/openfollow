# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The Path Editor's drawing helpers, run in node and held against the Operator Screen's draw pass."""

from __future__ import annotations

import json
import shutil
import subprocess  # nosec B404 - runs the local node binary on a fixed script
from pathlib import Path
from typing import Any

import pytest

from openfollow.runtime.overlay_draw_style import PATH_ARROW_LENGTH, PATH_ARROW_WIDTH
from tests._fake_cairo import FakeCairo

pytestmark = [pytest.mark.unit, pytest.mark.skipif(shutil.which("node") is None, reason="needs node")]

_TEMPLATE = Path(__file__).parents[1] / "openfollow" / "web" / "templates" / "partials" / "path_editor.tpl"


def _function(name: str) -> str:
    """The source of top-level ``function name(...) {...}`` in the editor's script."""
    text = _TEMPLATE.read_text(encoding="utf-8")
    start = text.index(f"function {name}(")
    depth, i = 0, text.index("{", start)
    while True:
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        i += 1
        if depth == 0:
            return text[start:i]


def _run(call: str, *names: str) -> object:
    script = "\n".join(_function(n) for n in names) + f"\nprocess.stdout.write(JSON.stringify({call}));"
    node = shutil.which("node")
    assert node is not None
    out = subprocess.run([node, "-e", script], capture_output=True, text=True, check=True, timeout=10)  # nosec B603
    return json.loads(out.stdout)


class TestPolylines:
    def test_a_point_behind_the_camera_breaks_the_line(self) -> None:
        points = [[0, 0], [1, 1], None, [2, 2], [3, 3], [4, 4]]
        assert _run(f"pePolylines({json.dumps(points)})", "pePolylines") == [[[0, 0], [1, 1]], [[2, 2], [3, 3], [4, 4]]]

    def test_a_lone_visible_point_draws_no_line(self) -> None:
        assert _run("pePolylines([[0, 0], null, [5, 5], null])", "pePolylines") == []
        assert _run("pePolylines([[0, 0], [1, 1], null, [5, 5]])", "pePolylines") == [[[0, 0], [1, 1]]]

    def test_no_points_no_lines(self) -> None:
        assert _run("pePolylines([])", "pePolylines") == []


def test_the_arrowhead_is_the_operator_screen_s() -> None:
    from openfollow.runtime.overlay_draw_paths import _arrowhead

    cr = FakeCairo()
    _arrowhead(cr, 100.0, 50.0, 0.6, 0.8)
    hud = [list(cr.move_tos[0]), *(list(p) for p in cr.line_tos)]
    half, side = PATH_ARROW_LENGTH / 2, PATH_ARROW_WIDTH / 2
    js = _run(f"peArrowhead(100, 50, 0.6, 0.8, {half}, {side})", "peArrowhead")
    assert [pytest.approx(p) for p in hud] == js


# -- the whole editor, on the fake DOM against a scripted station -------------------

_STATIC = Path(__file__).parents[1] / "openfollow" / "web" / "static" / "js"


def _editor(steps: str) -> Any:
    """Run the Path Editor's script on the fake DOM, then ``steps`` (async, filling ``out``)."""
    template = _TEMPLATE.read_text(encoding="utf-8")
    markup = template[: template.index("<style>")]
    start = template.index("<script>") + len("<script>")
    script = template[start : template.index("</script>", start)]
    source = "\n".join(
        [
            (Path(__file__).parent / "_fake_dom.js").read_text(encoding="utf-8"),
            (Path(__file__).parent / "_path_editor_page.js").read_text(encoding="utf-8"),
            (_STATIC / "units.js").read_text(encoding="utf-8"),
            (_STATIC / "save-feedback.js").read_text(encoding="utf-8"),
            f"parseHTML({json.dumps(markup)}, document.body);",
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


class TestDraggingAPoint:
    def test_a_drag_moves_the_point_and_adds_none(self) -> None:
        out = _editor(
            """
            openRow('cross'); await settle();
            var h = handles()[0];
            pointer(h, 'pointerdown', 340, 560);
            pointer(picture(), 'pointerup', 440, 660);
            pointer(picture(), 'click', 440, 660);
            await settle();
            out.puts = puts().map(function (r) { return r.body.points; });
            out.saved = model.paths[0].points;
            """
        )
        assert len(out["puts"]) == 1
        assert [(p["x"], p["y"]) for p in out["saved"]] == [(-2.0, 3.0), (3, 2)]

    def test_a_press_on_a_point_without_moving_adds_none(self) -> None:
        out = _editor(
            """
            openRow('cross'); await settle();
            pointer(handles()[1], 'pointerdown', 940, 560);
            pointer(picture(), 'pointerup', 940, 560);
            pointer(picture(), 'click', 940, 560);
            await settle();
            out.count = model.paths[0].points.length;
            """
        )
        assert out["count"] == 2

    def test_a_click_on_the_picture_adds_a_point_at_floor_height(self) -> None:
        out = _editor(
            """
            openRow('cross'); await settle();
            pointer(picture(), 'pointerdown', 640, 860);
            pointer(picture(), 'pointerup', 640, 860);
            pointer(picture(), 'click', 640, 860);
            await settle();
            out.saved = model.paths[0].points;
            """
        )
        assert [(p["x"], p["y"], p["z"]) for p in out["saved"]] == [(-3, 2, 0), (3, 2, 0), (0.0, 5.0, 0)]

    def test_a_drag_that_ended_without_a_click_does_not_eat_the_next_click(self) -> None:
        out = _editor(
            """
            openRow('cross'); await settle();
            pointer(handles()[0], 'pointerdown', 340, 560);
            pointer(picture(), 'pointerup', 440, 660);
            await settle();
            pointer(picture(), 'pointerdown', 640, 860);
            pointer(picture(), 'pointerup', 640, 860);
            pointer(picture(), 'click', 640, 860);
            await settle();
            out.count = model.paths[0].points.length;
            """
        )
        assert out["count"] == 3

    def test_a_cancelled_drag_leaves_the_picture_working(self) -> None:
        out = _editor(
            """
            openRow('cross'); await settle();
            pointer(handles()[0], 'pointerdown', 340, 560);
            pointer(picture(), 'pointercancel', 340, 560);
            pointer(picture(), 'pointerdown', 640, 860);
            pointer(picture(), 'pointerup', 640, 860);
            pointer(picture(), 'click', 640, 860);
            await settle();
            out.saved = model.paths[0].points;
            """
        )
        assert [(p["x"], p["y"]) for p in out["saved"]] == [(-3, 2), (3, 2), (0.0, 5.0)]
