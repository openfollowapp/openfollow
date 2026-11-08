# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""Simple mode's Interface picker, run in node on the fake DOM: a pick is confirmed before it posts."""

from __future__ import annotations

import json
import shutil
import subprocess  # nosec B404 - runs the local node binary on a fixed script
from pathlib import Path
from typing import Any

import pytest
from bottle import template

from openfollow.web import server as _server_module  # noqa: F401 – registers the template path
from tests._template_source import function_body

pytestmark = [pytest.mark.unit, pytest.mark.skipif(shutil.which("node") is None, reason="needs node")]

_TESTS = Path(__file__).parent
_BASE = _TESTS.parent / "openfollow" / "web" / "templates" / "base.tpl"
_LIGHTS = "enx9c69d3ac16ab"
_NAMES = {"eth0": "eth0", _LIGHTS: f"Lights ({_LIGHTS})"}

# The dialog answers when the test says so; htmx records what it is asked to trigger.
_HELPERS = """
var asked = [], answers = [], triggered = [];
function modalConfirm(opts) {
    asked.push(opts);
    return new Promise(function (resolve) { answers.push(resolve); });
}
window.htmx = { trigger: function (elt, name) { triggered.push([elt.className, name]); } };
var form = document.querySelector('.net-station-form');
var select = document.getElementById('net-station-iface');
select.addEventListener('change', new Function('event', select.getAttribute('onchange')).bind(select));
function pick(value) { select.value = value; select.dispatchEvent(makeEvent('change')); }
async function answer(ok) { answers.shift()(ok); await settle(); }
"""


def _picker(station: str, auto: str, everywhere: bool) -> str:
    rendered = template(
        "partials/network",
        net={
            "available": True,
            "writable": True,
            "mode": "simple",
            "station_options": [("eth0", "10.0.0.5"), (_LIGHTS, "")],
            "station_iface": station,
            "station_auto": auto,
            "station_names": _NAMES,
            "web_everywhere": everywhere,
        },
    )
    start = rendered.index('<form class="net-station-form"')
    return rendered[start : rendered.index("</form>", start) + len("</form>")]


def _run(steps: str, station: str = "", auto: str = "eth0", everywhere: bool = False) -> Any:
    script = "\n".join(
        [
            (_TESTS / "_fake_dom.js").read_text(encoding="utf-8"),
            f"parseHTML({json.dumps(_picker(station, auto, everywhere))}, document.body);",
            "function netConfirmStation(select) {"
            + function_body(_BASE.read_text(encoding="utf-8"), "netConfirmStation")
            + "}",
            _HELPERS,
            "var out = {};",
            f"(async function () {{ {steps}\n process.stdout.write(JSON.stringify(out)); }})();",
        ]
    )
    node = shutil.which("node")
    assert node is not None
    done = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=20)  # nosec B603
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_a_pick_asks_before_it_posts_and_confirm_posts_it() -> None:
    out = _run(
        f"pick('{_LIGHTS}'); await settle(); out.before = triggered.slice(); out.asked = asked;"
        "await answer(true); out.after = triggered; out.value = select.value;"
        "out.trigger = form.getAttribute('hx-trigger');"
    )
    assert out["before"] == []
    assert out["asked"] == [
        {
            "title": f"Move all traffic to Lights ({_LIGHTS})?",
            "message": f"PSN, OTP, RTTrPM, OSC and video move from eth0 to Lights ({_LIGHTS}).",
            "confirmLabel": "Move traffic",
            "danger": True,
        }
    ]
    # The form posts on the event the confirm sends, never on change.
    assert out["after"] == [["net-station-form", out["trigger"]]]
    assert out["value"] == _LIGHTS


def test_cancel_puts_the_picker_back_without_a_request() -> None:
    out = _run(
        f"pick('{_LIGHTS}'); await settle(); await answer(false); out.value = select.value; out.sent = triggered;"
    )
    assert out == {"value": "", "sent": []}


def test_moving_back_to_automatic_names_both_interfaces() -> None:
    out = _run("pick(''); await settle(); out.asked = asked.map((o) => [o.title, o.message]);", _LIGHTS)
    assert out["asked"] == [
        [
            "Move all traffic to Automatic (eth0)?",
            f"PSN, OTP, RTTrPM, OSC and video move from Lights ({_LIGHTS}) to Automatic (eth0).",
        ]
    ]


def test_a_station_with_no_interface_in_force_names_only_the_new_one() -> None:
    out = _run("pick('eth0'); await settle(); out.message = asked[0].message;", auto="")
    assert out == {"message": "PSN, OTP, RTTrPM, OSC and video move to eth0."}


def test_picking_the_interface_in_force_does_nothing() -> None:
    out = _run(f"pick('{_LIGHTS}'); await settle(); out.asked = asked.length; out.sent = triggered;", _LIGHTS)
    assert out == {"asked": 0, "sent": []}


@pytest.mark.parametrize(
    ("everywhere", "web"), [(True, " The web UI keeps answering on every interface."), (False, "")]
)
def test_the_web_ui_is_said_to_stay_only_where_it_listens_everywhere(everywhere: bool, web: str) -> None:
    out = _run(f"pick('{_LIGHTS}'); await settle(); out.message = asked[0].message;", everywhere=everywhere)
    assert out == {"message": f"PSN, OTP, RTTrPM, OSC and video move from eth0 to Lights ({_LIGHTS})." + web}
