# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The marker catalog table (partials/marker.tpl), run in node on the fake DOM: its Conflict flag."""

from __future__ import annotations

import json
import shutil
import subprocess  # nosec B404 - runs the local node binary on a fixed script
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.skipif(shutil.which("node") is None, reason="needs node")]

_TESTS = Path(__file__).parent
_TEMPLATE = _TESTS.parent / "openfollow" / "web" / "templates" / "partials" / "marker.tpl"

_PAGE = """
window.OpenFollow = {
    attachColorPicker: function () {}, nextUnusedColor: function () { return '#ff0000'; }, saveError: {},
};
var root = document.body.appendChild(document.createElement('div'));
root.setAttribute('id', 'marker-catalog-root');
function poll(conflicts) {
    var data = {
        entries: [{id: 3, name: 'Lead', color: '#ff0000'}, {id: 4, name: 'Second', color: '#00ff00'}],
        peer_selections: [{station_id: 'p1', station_name: 'Stage Left', controlled_ids: [3], viewer_ids: [3]}],
        this_station: {station_id: 'me', station_name: 'FOH', controlled_ids: [3], viewer_ids: [3]},
        next_free_id: 5,
    };
    if (conflicts !== undefined) data.marker_conflicts = conflicts;
    document.body.dispatchEvent(makeEvent('htmx:afterRequest', {
        detail: {elt: root, xhr: {responseText: JSON.stringify(data)}},
    }));
}
function flags() {
    var out = {};
    root.querySelectorAll('tr[data-marker-id]').forEach(function (tr) {
        out[tr.getAttribute('data-marker-id')] = {
            red: tr.classList.contains('conflict'),
            chip: tr.querySelectorAll('.conflict-flag').length,
        };
    });
    return out;
}
"""


def _script() -> str:
    text = _TEMPLATE.read_text(encoding="utf-8")
    start = text.index("<script>", text.index('id="marker-catalog-root"')) + len("<script>")
    return text[start : text.index("</script>", start)]


def _run(steps: str) -> Any:
    source = "\n".join(
        [
            (_TESTS / "_fake_dom.js").read_text(encoding="utf-8"),
            _PAGE,
            _script(),
            "var out = {};",
            steps,
            "process.stdout.write(JSON.stringify(out));",
        ]
    )
    node = shutil.which("node")
    assert node is not None
    done = subprocess.run([node, "-e", source], capture_output=True, text=True, timeout=20)  # nosec B603
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


_CONFLICT_3 = '[{"marker_id": 3, "stations": ["Stage Left"], "this_station": true}]'


def test_the_marker_the_runtime_lists_is_flagged_and_no_other() -> None:
    out = _run(f"poll({_CONFLICT_3}); out.flags = flags();")
    assert out["flags"] == {"3": {"red": True, "chip": 1}, "4": {"red": False, "chip": 0}}


@pytest.mark.parametrize("conflicts", ["[]", "undefined"], ids=["empty", "absent"])
def test_two_controllers_the_runtime_has_not_flagged_are_no_conflict(conflicts: str) -> None:
    """This station and a peer both list marker 3, but the flag is the runtime's to raise."""
    out = _run(f"poll({conflicts}); out.flags = flags();")
    assert out["flags"] == {"3": {"red": False, "chip": 0}, "4": {"red": False, "chip": 0}}


def test_a_conflict_the_runtime_withdraws_clears_at_the_next_poll() -> None:
    out = _run(f"poll({_CONFLICT_3}); out.before = flags()['3']; poll([]); out.after = flags()['3'];")
    assert out == {"before": {"red": True, "chip": 1}, "after": {"red": False, "chip": 0}}


_PARK_PAGE = """
window.OpenFollow.saveError = {clear: function () {}, boxFor: function () { return null; }, show: function () {}};
station = function () { return {status: 200, body: {success: true}}; };
function pollPark(parked) {
    document.body.dispatchEvent(makeEvent('htmx:afterRequest', {detail: {elt: root, xhr: {responseText: JSON.stringify({
        entries: [{id: 3, name: 'Lead', color: '#ff0000'}, {id: 4, name: 'Second', color: '#00ff00'}],
        peer_selections: [],
        this_station: {station_id: 'me', station_name: 'FOH', controlled_ids: [3], viewer_ids: [3], parked_ids: parked},
        next_free_id: 5,
    })}}}));
}
function park(id) { return root.querySelector('tr[data-marker-id="' + id + '"] button[data-park]'); }
function parkState(id) { return {pressed: park(id).getAttribute('aria-pressed'), disabled: park(id).disabled}; }
function posted() {
    return requests.filter(function (r) { return r.url === '/api/markers/selection'; })
        .map(function (r) { return r.body.parked_ids; });
}
"""


def _run_async(steps: str) -> Any:
    """Like :func:`_run`, with ``await settle()`` available to let saves answer."""
    source = "\n".join(
        [
            (_TESTS / "_fake_dom.js").read_text(encoding="utf-8"),
            _PAGE,
            _script(),
            _PARK_PAGE,
            "var out = {};",
            "(async function () {",
            steps,
            "process.stdout.write(JSON.stringify(out));",
            "})();",
        ]
    )
    node = shutil.which("node")
    assert node is not None
    done = subprocess.run([node, "-e", source], capture_output=True, text=True, timeout=20)  # nosec B603
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_the_park_toggle_shows_the_saved_park_and_only_a_controlled_row_can_take_one() -> None:
    out = _run_async("pollPark([3]); out.lead = parkState(3); out.second = parkState(4);")
    assert out == {"lead": {"pressed": "true", "disabled": False}, "second": {"pressed": "false", "disabled": True}}


def test_a_click_on_p_saves_the_park_with_the_selection() -> None:
    out = _run_async(
        "pollPark([]); park(3).click(); await settle(); out.on = parkState(3);"
        " park(3).click(); await settle(); out.off = parkState(3); out.posted = posted();"
    )
    assert out["on"] == {"pressed": "true", "disabled": False}
    assert out["off"] == {"pressed": "false", "disabled": False}
    assert out["posted"] == [[3], []]


def test_moving_a_parked_row_off_view_and_control_clears_its_park() -> None:
    out = _run_async(
        "pollPark([3]);"
        ' var view = root.querySelector(\'tr[data-marker-id="3"] input[data-this-station][value="view"]\');'
        ' var ctrl = root.querySelector(\'tr[data-marker-id="3"] input[data-this-station][value="control"]\');'
        " ctrl.checked = false; view.checked = true; view.dispatchEvent(makeEvent('change')); await settle();"
        " out.state = parkState(3); out.posted = posted();"
    )
    assert out == {"state": {"pressed": "false", "disabled": True}, "posted": [[]]}
