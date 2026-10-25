# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 OpenFollow Project
"""The shared drag-reorder in base.tpl, run in node: the OSC lists POST their order, other lists get an event."""

from __future__ import annotations

import json
import shutil
import subprocess  # nosec B404 - runs the local node binary on a fixed script
from pathlib import Path
from typing import Any

import pytest

from tests._template_source import reorder_script

pytestmark = [pytest.mark.unit, pytest.mark.skipif(shutil.which("node") is None, reason="needs node")]

_TESTS = Path(__file__).parent


# An OSC Destinations list and a list of plain reorder rows, three rows each.
_OSC_ROW = (
    '<details class="osc-destination-row" data-row-id="{0}" data-reorder-url="/section/osc_destinations/reorder"'
    ' data-reorder-target="osc-destinations-section"><summary>'
    '<span class="osc-destination-drag-handle" draggable="true">x</span>{0}</summary></details>'
)
_PLAIN_ROW = (
    '<tr class="reorder-row" data-row-id="{0}"><td><span class="row-drag-handle" draggable="true">x</span></td></tr>'
)
# A second plain list with the same ids, and a note in the OSC list that is not a row.
_MARKUP = (
    '<div class="osc-destinations-list" id="osc">'
    + "".join(_OSC_ROW.format(i) for i in "abc")
    + '<p>note</p></div><table><tbody id="plain">'
    + "".join(_PLAIN_ROW.format(i) for i in "012")
    + '</tbody></table><table><tbody id="other">'
    + "".join(_PLAIN_ROW.format(i) for i in "012")
    + "</tbody></table>"
)

_HELPERS = """
var posted = [], reordered = [];
window.htmx = { ajax: function (verb, url, opts) { posted.push([verb, url, opts]); } };
document.getElementById('plain').addEventListener('rows-reordered', function (ev) { reordered.push(ev.detail.order); });
function rows(list) { return document.getElementById(list).children; }
function ids(list) { return rows(list).map(function (r) { return r.dataset.rowId || r.tagName; }); }
function fire(target, type, y) {
    target.dispatchEvent(makeEvent(type, { clientY: y || 0, dataTransfer: { setData: function () {} } }));
}
function marked(list, i, cls) { return rows(list)[i].classList.contains(cls); }
// Drag the row at ``from`` by its handle onto the row at ``onto``, in its upper (y 5) or lower (y 15) half.
function dragRow(list, from, onto, y, ontoList) {
    var handle = rows(list)[from].querySelector('[draggable]');
    var target = rows(ontoList || list)[onto];
    fire(handle, 'dragstart');
    fire(target, 'dragover', y);
    fire(target, 'drop', y);
    fire(handle, 'dragend');
}
"""


def _run(steps: str) -> Any:
    source = "\n".join(
        [
            (_TESTS / "_fake_dom.js").read_text(encoding="utf-8"),
            f"parseHTML({json.dumps(_MARKUP)}, document.body);",
            reorder_script(),
            _HELPERS,
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


def test_an_osc_list_posts_its_new_order_through_htmx() -> None:
    out = _run("dragRow('osc', 2, 0, 5); out.order = ids('osc'); out.posted = posted; out.events = reordered;")
    assert out["order"] == ["c", "a", "b", "P"]
    assert out["posted"] == [
        [
            "POST",
            "/section/osc_destinations/reorder",
            {"target": "#osc-destinations-section", "swap": "outerHTML", "values": {"order": "c,a,b"}},
        ]
    ]
    assert out["events"] == []


@pytest.mark.parametrize(("onto", "y", "order"), [(2, 15, ["1", "2", "0"]), (2, 5, ["1", "0", "2"])])
def test_a_list_without_a_reorder_url_gets_its_new_order_as_an_event(onto: int, y: int, order: list[str]) -> None:
    out = _run(f"dragRow('plain', 0, {onto}, {y}); out.events = reordered; out.posted = posted;")
    assert out == {"events": [order], "posted": []}


@pytest.mark.parametrize("onto", ["osc", "other"])
def test_a_drag_moves_rows_only_among_their_own_siblings(onto: str) -> None:
    out = _run(
        f"dragRow('plain', 0, 1, 15, '{onto}');"
        "out.lists = [ids('osc'), ids('plain'), ids('other')]; out.posted = posted; out.events = reordered;"
    )
    assert out == {
        "lists": [["a", "b", "c", "P"], ["0", "1", "2"], ["0", "1", "2"]],
        "posted": [],
        "events": [],
    }


def test_a_row_of_another_list_is_not_marked_as_a_place_to_drop() -> None:
    out = _run(
        "var handle = rows('plain')[0].querySelector('[draggable]');"
        "fire(handle, 'dragstart'); fire(rows('other')[2], 'dragover', 15);"
        "out.marked = document.querySelectorAll('.drop-target').length;"
    )
    assert out == {"marked": 0}


def test_a_row_dropped_on_itself_stays_and_sends_nothing() -> None:
    out = _run("dragRow('plain', 1, 1, 15); out.plain = ids('plain'); out.events = reordered;")
    assert out == {"plain": ["0", "1", "2"], "events": []}


def test_the_row_being_dragged_and_the_one_under_it_are_marked_until_the_drop() -> None:
    out = _run(
        "var handle = rows('plain')[0].querySelector('[draggable]');"
        "fire(handle, 'dragstart'); fire(rows('plain')[2], 'dragover', 15);"
        "out.during = [marked('plain', 0, 'dragging'), marked('plain', 2, 'drop-target')];"
        "fire(rows('plain')[2], 'drop', 15); fire(handle, 'dragend');"
        "out.after = document.querySelectorAll('.dragging, .drop-target').length;"
    )
    assert out == {"during": [True, True], "after": 0}
