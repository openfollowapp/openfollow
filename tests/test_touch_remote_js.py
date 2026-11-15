# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
# Copyright (C) 2026 Michel Honold and Paul Hermann
"""The touch remote's stick and Z wheel maths (static/js/touch-remote.js), run in node."""

from __future__ import annotations

import json
import shutil
import subprocess  # nosec B404 - runs the local node binary on a fixed script
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.skipif(shutil.which("node") is None, reason="needs node")]

_MODULE = Path(__file__).parents[1] / "openfollow" / "web" / "static" / "js" / "touch-remote.js"

# A page without the card: the script only exports its maths.
_HARNESS = """
var window = {};
var document = { getElementById: function () { return null; } };
"""


def _run(body: str) -> Any:
    source = "\n".join(
        [
            _HARNESS,
            _MODULE.read_text(encoding="utf-8"),
            "var out = {};",
            body,
            "process.stdout.write(JSON.stringify(out));",
        ]
    )
    node = shutil.which("node")
    assert node is not None
    done = subprocess.run([node, "-e", source], capture_output=True, text=True, timeout=10)  # nosec B603
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@pytest.mark.parametrize(
    ("dx", "dy", "flip", "want"),
    [
        # Right and up on screen: stage left (+X) and upstage (+Y).
        (50, 0, False, {"knobX": 50, "knobY": 0, "sx": 0.5, "sy": 0}),
        (0, -100, False, {"knobX": 0, "knobY": -100, "sx": 0, "sy": 1}),
        # Past the rim: the knob stops at the rim and the deflection at full.
        (300, 400, False, {"knobX": 60, "knobY": 80, "sx": 0.6, "sy": -0.8}),
        # Facing FOH turns the stick around, the knob stays under the finger.
        (50, -50, True, {"knobX": 50, "knobY": -50, "sx": -0.5, "sy": -0.5}),
    ],
)
def test_stick_vector(dx: float, dy: float, flip: bool, want: dict[str, float]) -> None:
    out = _run(f"out.v = window.OpenFollow.touchRemote.stickVector({dx}, {dy}, 100, {str(flip).lower()});")
    assert out["v"] == pytest.approx(want)


@pytest.mark.parametrize(
    ("acc", "dy", "want"),
    [
        (0, -45, {"notches": 2, "acc": 5}),  # 45 px up: two notches, 5 px kept
        (5, -15, {"notches": 1, "acc": 0}),
        (0, 30, {"notches": -1, "acc": -10}),  # down
        (-10, -10, {"notches": 0, "acc": 0}),  # back to where it started
        (0, -19, {"notches": 0, "acc": 19}),
    ],
)
def test_drum_notches(acc: float, dy: float, want: dict[str, float]) -> None:
    out = _run(f"out.r = window.OpenFollow.touchRemote.drumNotches({acc}, {dy}, 20);")
    assert out["r"] == want
