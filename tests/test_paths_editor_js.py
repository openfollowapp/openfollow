# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The Path Editor's drawing helpers, run in node and held against the Operator Screen's draw pass."""

from __future__ import annotations

import json
import shutil
import subprocess  # nosec B404 - runs the local node binary on a fixed script
from pathlib import Path

import pytest

from openfollow.runtime.overlay_draw_style import PATH_ARROW_LENGTH, PATH_ARROW_WIDTH
from tests._fake_cairo import FakeCairo

pytestmark = [pytest.mark.unit, pytest.mark.skipif(shutil.which("node") is None, reason="needs node")]

_TEMPLATE = Path(__file__).parents[1] / "openfollow" / "web" / "templates" / "partials" / "paths.tpl"


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
