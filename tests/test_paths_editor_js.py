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
from tests._template_source import reorder_script

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


def _editor(steps: str, setup: str = "") -> Any:
    """Run ``setup``, then the Path Editor's script on the fake DOM, then ``steps`` (async, filling ``out``)."""
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
            reorder_script(),
            f"parseHTML({json.dumps(markup)}, document.body);",
            setup,
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

    def test_the_picture_offers_no_handles_until_it_shows_the_rows_as_they_are(self) -> None:
        out = _editor(
            """
            openRow('cross'); await settle();
            holdWhen = function (req) { return req.url === '/api/paths/view'; };
            control('cross', 'delete-0').click();  // the picture still shows three points
            await settle();
            out.behind = handles().length;
            holdWhen = null; release(); await settle();
            out.caughtUp = handles().length;
            pointer(handles()[0], 'pointerdown', 940, 560);  // (3, 2), now the first point
            pointer(picture(), 'pointerup', 940, 760);
            await settle();
            out.saved = model.paths[0].points.map(function (p) { return [p.x, p.y]; });
            """,
            setup="model.paths[0].points.push({ x: 0, y: 0, z: 0, radius: 0.5 });",
        )
        assert out == {"behind": 0, "caughtUp": 2, "saved": [[3, 4], [0, 0]]}

    def test_a_point_gone_by_the_time_its_drag_ends_moves_nothing(self) -> None:
        out = _editor(
            """
            openRow('cross'); await settle();
            pointer(handles()[2], 'pointerdown', 640, 360);
            model.paths[0].points.pop();  // deleted on another station
            control('loop', 'closed').click();  // a save, then a load
            await settle();
            pointer(picture(), 'pointerup', 740, 460);
            await settle();
            out.saved = model.paths[0].points.map(function (p) { return [p.x, p.y]; });
            out.line = errorLine();
            """,
            setup="model.paths[0].points.push({ x: 0, y: 0, z: 0, radius: 0.5 });",
        )
        assert out == {"saved": [[-3, 2], [3, 2]], "line": None}

    def test_a_dragged_point_follows_the_pointer(self) -> None:
        out = _editor(
            """
            openRow('cross'); await settle();
            var dot = handles()[0];
            pointer(dot, 'pointerdown', 340, 560);
            pointer(picture(), 'pointermove', 400, 600);
            out.during = [dot.getAttribute('cx'), dot.getAttribute('cy')];
            pointer(picture(), 'pointerup', 400, 600);
            pointer(picture(), 'pointermove', 500, 700);  // after the drag: nothing follows
            out.after = [dot.getAttribute('cx'), dot.getAttribute('cy')];
            """
        )
        assert out == {"during": ["400", "600"], "after": ["400", "600"]}


class TestTheSelectedPath:
    def test_the_picture_keeps_the_row_opened_last_across_a_redraw(self) -> None:
        out = _editor(
            """
            openRow('loop'); await settle();
            openRow('cross'); await settle();
            out.before = selectedRow();
            control('cross', 'closed').click();
            await settle();
            out.after = selectedRow();
            out.handles = handles().length;
            out.saved = model.paths[0].closed;
            """
        )
        assert out == {"before": "cross", "after": "cross", "handles": 2, "saved": True}

    def test_a_row_opened_just_before_a_redraw_shows_open(self) -> None:
        out = _editor(
            """
            openRow('cross');  // its toggle event is still to come
            control('loop', 'closed').click();  // redraws the rows now
            await settle();
            out.open = row('cross').open;
            out.selected = selectedRow();
            """
        )
        assert out == {"open": True, "selected": "cross"}

    def test_closing_the_selected_row_leaves_the_picture_without_one(self) -> None:
        out = _editor(
            """
            openRow('cross'); await settle();
            openRow('cross'); await settle();
            control('loop', 'closed').click();  // redraws the rows
            await settle();
            out.selected = selectedRow();
            out.handles = handles().length;
            out.open = !!row('cross').open;
            """
        )
        assert out == {"selected": None, "handles": 0, "open": False}


class TestEditingThePoints:
    def test_tabbing_through_a_point_saves_every_field(self) -> None:
        out = _editor(
            """
            openRow('cross'); await settle();
            var x = inputs('cross', 0)[0], y = inputs('cross', 0)[1];
            typeInto(x, '-2');
            typeInto(y, '7');  // Tab leaves X; Y is typed before the save of X is answered
            await settle();
            var now = inputs('cross', 0)[1];
            out.focused = document.activeElement === now;
            out.typed = now.value;
            inputs('cross', 0)[2].focus();
            await settle();
            out.saved = [model.paths[0].points[0].x, model.paths[0].points[0].y];
            """
        )
        assert out == {"focused": True, "typed": "7", "saved": [-2, 7]}

    def test_one_save_at_a_time_and_the_next_carries_every_edit_since(self) -> None:
        out = _editor(
            """
            openRow('cross'); await settle();
            holdWhen = function (req) { return req.method === 'PUT'; };
            var cells = inputs('cross', 0);
            typeInto(cells[0], '-2'); enter(cells[0]);
            typeInto(cells[1], '7'); enter(cells[1]);
            typeInto(cells[2], '1.5'); enter(cells[2]);
            await settle();
            out.inFlight = puts().length;
            holdWhen = null;
            release(); await settle();
            out.sent = puts().map(function (r) { var p = r.body.points[0]; return [p.x, p.y, p.z]; });
            out.saved = model.paths[0].points[0];
            """
        )
        assert out["inFlight"] == 1
        assert out["sent"] == [[-2, 2, 0], [-2, 7, 1.5]]
        assert (out["saved"]["x"], out["saved"]["y"], out["saved"]["z"]) == (-2, 7, 1.5)

    def test_a_load_that_started_before_an_edit_does_not_undo_it(self) -> None:
        out = _editor(
            """
            openRow('cross'); await settle();
            holdWhen = function (req) { return req.method === 'GET' && req.url === '/api/paths'; };
            var cells = inputs('cross', 0);
            typeInto(cells[0], '-2'); enter(cells[0]);
            await settle();  // saved; the load after it waits
            typeInto(cells[1], '7'); enter(cells[1]);
            await settle();
            releaseFirst(); await settle();  // the load from before Y was typed
            out.shown = inputs('cross', 0)[1].value;
            holdWhen = null;
            release(); await settle();
            out.after = [inputs('cross', 0)[1].value, model.paths[0].points[0].y];
            """
        )
        assert out["shown"] == "7"
        assert out["after"] == ["7", 7]

    def test_a_redraw_while_typing_keeps_the_field_and_what_was_typed(self) -> None:
        out = _editor(
            """
            openRow('loop'); await settle();
            openRow('cross'); await settle();
            var x = inputs('cross', 0)[0], y = inputs('cross', 0)[1];
            typeInto(x, '-2');
            typeInto(y, '7');
            y.setSelectionRange(1, 1);
            model.paths[1].points.push({ x: 2, y: 4, z: 0, radius: 0.5 });  // recorded on the Operator Screen
            await settle();
            var now = inputs('cross', 0)[1];
            out.redrawn = now !== y;
            out.focused = document.activeElement === now;
            out.typed = [now.value, now.selectionStart];
            out.loopPoints = row('loop').querySelectorAll('.pe-points tbody tr').length;
            inputs('cross', 0)[2].focus();  // a redrawn field gets no change event, only blur
            await settle();
            out.saved = model.paths[0].points[0].y;
            """
        )
        assert out == {"redrawn": True, "focused": True, "typed": ["7", 1], "loopPoints": 3, "saved": 7}

    def test_a_redraw_between_tab_and_the_first_key_keeps_the_text_selected(self) -> None:
        out = _editor(
            """
            openRow('cross'); await settle();
            typeInto(inputs('cross', 0)[0], '-2');
            tabTo(inputs('cross', 0)[1]);
            model.paths[1].closed = true;  // changed elsewhere: the load after the save of X redraws
            await settle();
            var y = document.activeElement;
            typeKeys(y, '6');
            inputs('cross', 0)[2].focus();
            await settle();
            out.saved = model.paths[0].points[0].y;
            """
        )
        assert out["saved"] == 6

    def test_a_field_saves_once_when_left_after_a_change_and_not_at_all_untouched(self) -> None:
        out = _editor(
            """
            openRow('cross'); await settle();
            var cells = inputs('cross', 0);
            cells[0].focus();
            cells[1].focus();  // left untouched
            await settle();
            out.untouched = puts().length;
            typeInto(cells[1], '7');
            cells[2].focus();
            await settle();
            out.changed = puts().length;
            """
        )
        assert out == {"untouched": 0, "changed": 1}

    def test_text_that_does_not_read_as_a_length_survives_a_redraw_unsaved(self) -> None:
        out = _editor(
            """
            openRow('cross'); await settle();
            var x = inputs('cross', 0)[0], y = inputs('cross', 0)[1];
            typeInto(x, '-2');
            typeInto(y, 'abc'); enter(y);
            model.paths[1].closed = true;  // changed elsewhere: the load after the save of X redraws
            await settle();
            var now = inputs('cross', 0)[1];
            out.shown = [now !== y, document.activeElement === now, now.value, now.getAttribute('aria-invalid')];
            out.saved = model.paths[0].points[0].y;
            """
        )
        assert out == {"shown": [True, True, "abc", "true"], "saved": 2}

    def test_a_field_left_untouched_shows_what_the_station_changed_and_saves_nothing(self) -> None:
        out = _editor(
            """
            openRow('cross'); await settle();
            var x = inputs('cross', 0)[0], y = inputs('cross', 0)[1];
            typeInto(x, '-2');
            y.focus();
            model.paths[0].points[0].y = 5;  // moved by another station's push before the load
            await settle();
            var now = inputs('cross', 0)[1];
            out.shown = [document.activeElement === now, now.value];
            now.blur(); await settle();
            out.crossSaves = puts().filter(function (r) { return r.url === '/api/paths/cross'; }).length;
            """
        )
        assert out == {"shown": [True, "5.000 m"], "crossSaves": 1}

    def test_a_new_point_shows_in_the_table_at_once(self) -> None:
        out = _editor(
            """
            openRow('cross'); await settle();
            holdWhen = function (req) { return req.method === 'PUT'; };
            row('cross').querySelector('.pe-add button').click();
            out.rows = row('cross').querySelectorAll('.pe-points tbody tr').length;
            holdWhen = null; release(); await settle();
            out.saved = model.paths[0].points.map(function (p) { return [p.x, p.y]; });
            """
        )
        assert out == {"rows": 3, "saved": [[-3, 2], [3, 2], [4, 2]]}

    def test_a_template_loaded_during_a_save_does_not_lose_an_edit(self) -> None:
        out = _editor(
            """
            openRow('cross'); await settle();
            holdWhen = function (req) { return req.method === 'PUT'; };
            var cells = inputs('cross', 0);
            typeInto(cells[0], '-2'); enter(cells[0]);  // in flight
            typeInto(cells[1], '7'); enter(cells[1]);   // waiting
            editor().querySelector('[data-pe="template-load"]').click();
            templateDialog.onApplied();
            await settle();
            cells = inputs('cross', 0);
            typeInto(cells[2], '1.5'); enter(cells[2]);
            holdWhen = null; release(); await settle();
            var p = model.paths[0].points[0];
            out.saved = [p.x, p.y, p.z];
            """
        )
        assert out["saved"] == [-2, 7, 1.5]

    def test_an_older_picture_answer_does_not_replace_a_newer_one(self) -> None:
        out = _editor(
            """
            openRow('cross'); await settle();
            holdWhen = function (req) { return req.url === '/api/paths/view'; };
            var cells = inputs('cross', 0);
            typeInto(cells[0], '-2'); enter(cells[0]);
            await settle();
            typeInto(cells[1], '7'); enter(cells[1]);
            await settle();
            out.asked = held.length;
            releaseLast(); await settle();   // the picture after both edits
            out.handle = handles()[0].getAttribute('cy');
            release(); await settle();       // the one asked for between them
            out.after = handles()[0].getAttribute('cy');
            """
        )
        assert out == {"asked": 2, "handle": "1060", "after": "1060"}

    def test_a_rename_and_an_edit_land_on_the_renamed_path(self) -> None:
        out = _editor(
            """
            openRow('cross'); await settle();
            holdWhen = function (req) { return req.method === 'PUT'; };
            var name = control('cross', 'name');
            typeInto(name, 'ds'); enter(name);
            var cells = inputs('cross', 0);
            typeInto(cells[0], '-2'); enter(cells[0]);
            await settle();
            holdWhen = null;
            release(); await settle();
            out.urls = puts().map(function (r) { return r.url; });
            out.saved = [model.paths[0].name, model.paths[0].points[0].x];
            out.selected = selectedRow();
            """
        )
        assert out == {"urls": ["/api/paths/cross", "/api/paths/ds"], "saved": ["ds", -2], "selected": "ds"}

    def test_a_renamed_row_stays_open_on_its_tab(self) -> None:
        out = _editor(
            """
            openRow('cross'); await settle();
            control('cross', 'tab-points').click();
            var name = control('cross', 'name');
            typeInto(name, 'ds'); enter(name);
            await settle();
            out.open = row('ds').open;
            out.tab = row('ds').querySelector('.row-tab-btn.active').textContent;
            """
        )
        assert out == {"open": True, "tab": "Points"}

    def test_a_rename_the_station_reads_as_the_same_name_keeps_the_row_open(self) -> None:
        out = _editor(
            """
            openRow('cross'); await settle();
            var name = control('cross', 'name');
            typeInto(name, 'cross!'); enter(name);
            await settle();
            out.open = row('cross').open;
            out.shown = control('cross', 'name').value;
            """
        )
        assert out == {"open": True, "shown": "cross"}

    def test_a_refused_rename_puts_the_name_back(self) -> None:
        out = _editor(
            """
            openRow('cross'); await settle();
            refuse = function (req) {
                if (req.method === 'PUT' && 'name' in req.body) return { status: 400, body: { error: 'Taken.' } };
            };
            var name = control('cross', 'name');
            typeInto(name, 'LOOP'); enter(name);
            await settle();
            out.shown = control('cross', 'name').value;
            out.names = model.paths.map(function (p) { return p.name; });
            """
        )
        assert out == {"shown": "cross", "names": ["cross", "loop"]}

    def test_writes_that_are_not_edits_of_one_path_are_each_sent(self) -> None:
        out = _editor(
            """
            openRow('cross'); await settle();
            holdWhen = function (req) { return req.method === 'PUT'; };
            var cells = inputs('cross', 0);
            typeInto(cells[0], '-2'); enter(cells[0]);  // in flight
            editor().querySelector('[data-pe="new"]').click();
            editor().querySelector('[data-pe="new"]').click();
            holdWhen = null; release(); await settle();
            out.names = model.paths.map(function (p) { return p.name; });
            """
        )
        assert out["names"] == ["cross", "loop", "path-3", "path-4"]

    def test_a_deleted_path_does_not_hand_its_open_row_to_a_new_one_of_that_name(self) -> None:
        out = _editor(
            """
            openRow('cross'); await settle();
            model.paths.shift();  // deleted on another station
            control('loop', 'active').click();  // a save, then a load
            await settle();
            out.gone = row('cross') === null;
            model.paths.push(blank('cross'));  // and made again there
            control('loop', 'active').click();
            await settle();
            out.open = row('cross').open;
            out.selected = selectedRow();
            """
        )
        assert out == {"gone": True, "open": False, "selected": None}


class TestFailedSaves:
    def test_a_refused_save_rings_the_editor_and_says_why_with_the_station_s_next_step(self) -> None:
        out = _editor(
            """
            openRow('cross'); await settle();
            refuse = function (req) {
                if (req.method !== 'PUT') return;
                var error = 'This station does not accept changes made through that name';
                var href = 'http://192.0.2.10/';
                return { status: 403, body: { error: error, action: 'Open it at 192.0.2.10', href: href } };
            };
            var x = inputs('cross', 0)[0];
            typeInto(x, '-2'); enter(x);
            await settle();
            var line = editor().querySelector('.save-error');
            out.line = line.textContent;
            out.href = line.querySelector('a').getAttribute('href');
            out.ring = editor().classList.contains('save-failed');
            out.underToolbar = line.previousSibling === editor().querySelector('[data-pe="toolbar"]');
            out.shown = inputs('cross', 0)[0].value;  // the station's points come back
            """
        )
        assert out == {
            "line": "Not saved. This station does not accept changes made through that name. Open it at 192.0.2.10",
            "href": "http://192.0.2.10/",
            "ring": True,
            "underToolbar": True,
            "shown": "-3.000 m",
        }

    def test_a_save_that_gets_no_answer_says_the_station_did_not_answer(self) -> None:
        out = _editor(
            """
            openRow('cross'); await settle();
            refuse = function (req) { if (req.method === 'PUT') return 'unreachable'; };
            var x = inputs('cross', 0)[0];
            typeInto(x, '-2'); enter(x);
            await settle();
            out.line = errorLine();
            """
        )
        assert out["line"] == (
            "Not saved. The station did not answer. Check that it is switched on and on this network, then save again."
        )

    def test_the_next_save_clears_the_line(self) -> None:
        out = _editor(
            """
            openRow('cross'); await settle();
            refuse = function (req) { if (req.method === 'PUT') return 'unreachable'; };
            control('cross', 'closed').click();
            await settle();
            out.before = errorLine() !== null;
            refuse = null;
            row('cross').querySelector('.pe-add button').click();  // a button: no input event to clear it
            await settle();
            out.after = errorLine();
            """
        )
        assert out == {"before": True, "after": None}

    @pytest.mark.parametrize(("answer", "deleted"), [(False, False), (True, True)])
    def test_delete_asks_in_the_station_s_dialog(self, answer: bool, deleted: bool) -> None:
        out = _editor(
            f"""
            confirmAnswer = {json.dumps(answer)};
            openRow('cross'); await settle();
            row('cross').querySelector('.osc-binding-actions button.danger').click();
            await settle();
            out.asked = confirmed.map(function (c) {{ return [c.confirmLabel, c.danger, c.message]; }});
            out.names = model.paths.map(function (p) {{ return p.name; }});
            """
        )
        assert out["asked"] == [["Delete", True, "Delete path cross?"]]
        assert ("cross" not in out["names"]) is deleted

    def test_a_point_past_the_limit_is_not_saved_and_says_so(self) -> None:
        out = _editor(
            """
            openRow('cross'); await settle();
            row('cross').querySelector('.pe-add button').click();
            await settle();
            out.puts = puts().length;
            out.line = errorLine();
            """,
            setup="for (var i = 0; i < 98; i++) model.paths[0].points.push({ x: i, y: 0, z: 0, radius: 0.5 });",
        )
        assert out == {"puts": 0, "line": "Not saved. A path holds at most 100 points."}

    @pytest.mark.parametrize(
        ("refused", "steps", "line"),
        [
            (
                "'/api/paths/marker/2'",
                "row('cross').querySelectorAll('.pe-add button')[1].click();",
                "Not saved. Marker 2 has no position on this station.",
            ),
            (
                "'/api/paths/unproject'",
                "pointer(picture(), 'pointerdown', 640, 10); pointer(picture(), 'click', 640, 10);",
                "Not saved. That point of the picture does not reach the stage.",
            ),
            (
                "'/api/paths/unproject'",
                "pointer(handles()[0], 'pointerdown', 340, 560); pointer(picture(), 'pointerup', 640, 10);",
                "Not saved. That point of the picture does not reach the stage.",
            ),
        ],
    )
    def test_a_point_the_station_cannot_place_says_why(self, refused: str, steps: str, line: str) -> None:
        error = line.removeprefix("Not saved. ")
        out = _editor(
            f"""
            refuse = function (req) {{
                if (req.url === {refused}) return {{ status: 422, body: {{ error: {json.dumps(error)} }} }};
            }};
            openRow('cross'); await settle();
            {steps}
            await settle();
            out.line = errorLine();
            out.points = model.paths[0].points.map(function (p) {{ return [p.x, p.y]; }});
            """
        )
        assert out == {"line": line, "points": [[-3, 2], [3, 2]]}


class TestApplyToAllStations:
    def _push(self, answer: str) -> Any:
        return _editor(
            f"""
            refuse = function (req) {{
                if (req.url === '/api/config/paths') return {{ body: {{ paths: [] }} }};
                if (req.url === '/api/config/paths/broadcast') return {answer};
            }};
            editor().querySelector('[data-pe="push"]').click();
            await settle();
            out.line = errorLine();
            out.toasts = toasts;
            """
        )

    def test_every_station_applied_is_a_toast(self) -> None:
        out = self._push("{ body: { peer_results: [{ name: 'A', success: true }, { name: 'B', success: true }] } }")
        assert out == {"line": None, "toasts": ["Applied to 2 station(s)"]}

    def test_stations_that_did_not_apply_are_named(self) -> None:
        out = self._push(
            "{ body: { peer_results: [{ name: 'A', success: true }, { ip: '192.0.2.7', success: false }] } }"
        )
        assert out == {
            "line": "Not applied on 1 of 2 stations: 192.0.2.7. Check that they are switched on, then apply again.",
            "toasts": [],
        }

    def test_a_push_the_station_refused_is_not_applied(self) -> None:
        out = self._push("{ status: 403, body: { error: 'Refused' } }")
        assert out == {"line": "Not applied. Refused.", "toasts": []}


class TestFollowingARecording:
    def _polls(self, hide: str, recording: str = "cross") -> Any:
        return _editor(
            f"""
            function gets() {{ return requests.filter(function (r) {{ return r.url === '/api/paths'; }}).length; }}
            var before = gets();
            {hide}
            tickIntervals(); await settle();
            out.polled = gets() - before;
            """,
            setup=f"model.recording = {json.dumps(recording or None)};",
        )

    def test_a_point_recorded_while_typing_shows_and_keeps_what_is_typed(self) -> None:
        out = _editor(
            """
            openRow('cross'); await settle();
            var name = control('cross', 'name');
            typeInto(name, 'ds');
            model.paths[0].points.push({ x: 5, y: 2, z: 0, radius: 0.5 });  // a Pathfinder press
            tickIntervals(); await settle();
            var now = control('cross', 'name');
            out.points = row('cross').querySelectorAll('.pe-points tbody tr').length;
            out.typing = [document.activeElement === now, now.value];
            """,
            setup="model.recording = 'cross';",
        )
        assert out == {"points": 3, "typing": [True, "ds"]}

    def test_nothing_is_asked_while_nothing_records(self) -> None:
        assert self._polls("", recording="")["polled"] == 0

    def test_nothing_is_asked_while_a_save_is_on_its_way(self) -> None:
        held = "holdWhen = function (req) { return req.method === 'PUT'; }; openRow('cross'); await settle();"
        held += " control('cross', 'closed').click(); before = gets();"
        assert self._polls(held)["polled"] == 0

    def test_the_rows_follow_a_recording_while_on_screen(self) -> None:
        assert self._polls("")["polled"] == 1

    @pytest.mark.parametrize(
        "hide",
        [
            "document.hidden = true;",  # the browser tab is in the background
            "editor().parentNode.style.display = 'none';",  # another page tab is shown
            "editor().querySelector('[data-pe=\"list\"]').style.display = 'none';",  # the section is folded
        ],
    )
    def test_nothing_is_asked_while_the_rows_are_not_on_screen(self, hide: str) -> None:
        assert self._polls(hide)["polled"] == 0


class TestReorderingThePoints:
    def test_a_drop_reorders_the_points_and_saves_them_once(self) -> None:
        out = _editor(
            """
            openRow('cross'); await settle();
            control('cross', 'tab-points').click();
            dragPoint('cross', 0, 2, 15);  // the first point to the end
            out.shown = pointRows('cross').map(function (r) { return r.querySelectorAll('input')[0].value; });
            await settle();
            out.puts = puts().map(function (r) { return r.body; });
            out.saved = points(0);
            """,
            setup="model.paths[0].points.push({ x: 0, y: 0, z: 0, radius: 0.5 });",
        )
        assert out["shown"] == ["3.000 m", "0.000 m", "-3.000 m"]
        assert out["saved"] == [[3, 2], [0, 0], [-3, 2]]
        assert [list(body) for body in out["puts"]] == [["points"]]

    def test_the_rows_carry_no_up_and_down_buttons(self) -> None:
        out = _editor(
            """
            openRow('cross'); await settle();
            out.buttons = texts(row('cross').querySelectorAll('.pe-points button'));
            out.handles = row('cross').querySelectorAll('.pe-points .row-drag-handle').length;
            """
        )
        assert out == {"buttons": ["Delete", "Delete"], "handles": 2}


class TestPointFromAMarker:
    def test_the_chosen_marker_is_kept_for_the_next_add(self) -> None:
        out = _editor(
            """
            openRow('cross'); await settle();
            var sel = control('cross', 'marker');
            sel.value = '3'; sel.dispatchEvent(makeEvent('change'));
            control('cross', 'add-marker').click(); await settle();
            control('cross', 'add-marker').click(); await settle();
            out.asked = asked(/marker/);
            out.shown = control('cross', 'marker').value;
            """,
            setup="model.markers = [2, 3];",
        )
        assert out == {"asked": ["/api/paths/marker/3", "/api/paths/marker/3"], "shown": "3"}

    def test_without_a_marker_add_asks_nothing(self) -> None:
        out = _editor(
            """
            openRow('cross'); await settle();
            control('cross', 'add-marker').click(); await settle();
            out.asked = asked(/marker/).length;
            out.points = model.paths[0].points.length;
            """,
            setup="model.markers = [];",
        )
        assert out == {"asked": 0, "points": 2}


_TWO_MORE = (
    "model.paths.push(blank('fifth', { points: twoPoints(9) }));"
    "model.paths.push(blank('sixth', { points: twoPoints(8) }));"
    "model.recording = 'loop';"  # the rows follow the recording, so a poll redraws them
)


class TestFocusFollowsItsPath:
    def test_an_earlier_path_deleted_elsewhere_leaves_typing_in_its_own_path(self) -> None:
        out = _editor(
            """
            openRow('sixth'); await settle();
            openRow('fifth'); await settle();
            typeInto(control('fifth', 'x-0'), '9');
            model.paths.shift();  // cross deleted on another station
            tickIntervals(); await settle();
            var now = document.activeElement;
            out.focus = [now.id, now.value];
            control('fifth', 'y-0').focus(); await settle();
            out.puts = puts().map(function (r) { return r.url; });
            out.fifth = points(1)[0];
            """,
            setup=_TWO_MORE,
        )
        assert out == {"focus": ["pe:fifth:x-0", "9"], "puts": ["/api/paths/fifth"], "fifth": [9, 9]}

    def test_typing_in_a_path_deleted_elsewhere_goes_nowhere(self) -> None:
        out = _editor(
            """
            openRow('sixth'); await settle();
            openRow('fifth'); await settle();
            typeInto(control('fifth', 'x-0'), '9');
            model.paths.splice(2, 1);  // fifth deleted on another station
            tickIntervals(); await settle();
            out.focus = document.activeElement.tagName;
            document.body.focus(); await settle();
            out.puts = puts().length;
            """,
            setup=_TWO_MORE,
        )
        assert out == {"focus": "BODY", "puts": 0}

    def test_a_field_follows_its_path_through_a_rename(self) -> None:
        out = _editor(
            """
            openRow('cross'); await settle();
            var name = control('cross', 'name');
            typeInto(name, 'ds'); enter(name);
            await settle();
            out.focus = document.activeElement.id;
            """
        )
        assert out["focus"] == "pe:ds:name"


class TestFocusAfterARowButton:
    @pytest.mark.parametrize("what", ["add-point", "add-marker", "record"])
    def test_stays_on_the_button(self, what: str) -> None:
        out = _editor(
            f"""
            openRow('cross'); await settle();
            control('cross', {json.dumps(what)}).click(); await settle();
            out.focus = document.activeElement.id;
            out.label = document.activeElement.textContent;
            """
        )
        assert out["focus"] == f"pe:cross:{what}"
        assert out["label"] == {"add-point": "+ Point", "add-marker": "Add", "record": "Stop recording"}[what]

    @pytest.mark.parametrize(
        ("deleted", "count", "focus"),
        [(0, 3, "pe:cross:delete-0"), (2, 3, "pe:cross:delete-1"), (0, 1, "pe:cross:add-point")],
    )
    def test_a_point_s_delete_moves_to_the_point_that_takes_its_place(
        self, deleted: int, count: int, focus: str
    ) -> None:
        out = _editor(
            f"""
            openRow('cross'); await settle();
            control('cross', 'delete-{deleted}').click(); await settle();
            out.focus = document.activeElement.id;
            out.points = model.paths[0].points.length;
            """,
            setup=f"model.paths[0].points = model.paths[0].points.concat(twoPoints(0)).slice(0, {count});",
        )
        assert out == {"focus": focus, "points": count - 1}


class TestSavingOnlyWhatChanged:
    @pytest.mark.parametrize(
        ("steps", "body"),
        [
            ("control('cross', 'closed').click();", {"closed": True}),
            (
                "var sel = control('cross', 'end-0'); sel.value = 'release'; sel.dispatchEvent(makeEvent('change'));",
                {"release_at_start": True},
            ),
        ],
    )
    def test_an_option_saves_that_option_alone(self, steps: str, body: dict) -> None:
        out = _editor(
            f"""
            openRow('cross'); await settle();
            {steps}
            await settle();
            out.puts = puts().map(function (r) {{ return r.body; }});
            """
        )
        assert out["puts"] == [body]

    def test_a_point_recorded_just_before_a_shape_change_is_kept(self) -> None:
        out = _editor(
            """
            openRow('cross'); await settle();
            model.paths[0].points.push({ x: 7, y: 7, z: 0, radius: 0.5 });  // a Pathfinder press, saved
            control('cross', 'closed').click();  // before the next poll
            await settle();
            out.points = model.paths[0].points.length;
            out.closed = model.paths[0].closed;
            """,
            setup="model.recording = 'cross';",
        )
        assert out == {"points": 3, "closed": True}


class TestARecordingPath:
    def test_its_points_cannot_be_edited_here(self) -> None:
        out = _editor(
            """
            openRow('cross'); await settle();
            out.disabled = ['x-0', 'y-1', 'z-1', 'delete-0', 'add-point', 'add-marker'].map(function (c) {
                return control('cross', c).disabled;
            });
            out.handles = [row('cross').querySelectorAll('.row-drag-handle').length, handles().length];
            out.options = [control('cross', 'closed').disabled, control('cross', 'end-0').disabled];
            pointer(picture(), 'pointerdown', 640, 860);
            pointer(picture(), 'click', 640, 860);
            await settle();
            out.line = errorLine();
            out.points = model.paths[0].points.length;
            """,
            setup="model.recording = 'cross';",
        )
        assert out == {
            "disabled": [True] * 6,
            "handles": [0, 0],
            "options": [False, False],
            "line": "Not saved. Path cross is recording. Stop recording to change its points.",
            "points": 2,
        }

    def test_its_corner_radius_cannot_be_edited_here(self) -> None:
        out = _editor(
            """
            openRow('cross'); await settle();
            out.disabled = control('cross', 'radius-1').disabled;
            """,
            setup="model.recording = 'cross'; model.paths[0].points.push({ x: 0, y: 0, z: 0, radius: 0.5 });",
        )
        assert out["disabled"] is True


class TestTheRowActions:
    def test_stop_recording_stops_it(self) -> None:
        out = _editor(
            """
            openRow('cross'); await settle();
            control('cross', 'record').click(); await settle();
            out.sent = sent(/record/);
            out.recording = model.recording;
            out.label = control('cross', 'record').textContent;
            """,
            setup="model.recording = 'cross';",
        )
        assert out == {"sent": [{"on": False}], "recording": None, "label": "Record"}

    def test_record_starts_it(self) -> None:
        out = _editor(
            """
            openRow('loop'); await settle();
            control('loop', 'record').click(); await settle();
            out.sent = sent(/record/);
            out.chip = row('loop').querySelector('.stat-chip').textContent;
            """
        )
        assert out == {"sent": [{"on": True}], "chip": "Recording"}

    def test_the_active_switch_switches_the_path(self) -> None:
        out = _editor(
            """
            openRow('loop'); await settle();
            control('loop', 'active').click(); await settle();
            out.sent = sent(/active/);
            out.active = model.paths[1].active;
            out.dot = row('loop').querySelector('.osc-binding-enabled-dot').getAttribute('aria-label');
            """
        )
        assert out == {"sent": [{"active": True}], "active": True, "dot": "Active"}

    def test_a_corner_radius_saves_with_the_points(self) -> None:
        out = _editor(
            """
            openRow('cross'); await settle();
            var r = control('cross', 'radius-1');
            typeInto(r, '25cm'); enter(r);
            await settle();
            out.radius = model.paths[0].points[1].radius;
            out.label = r.getAttribute('aria-label');
            """,
            setup="model.paths[0].points.push({ x: 0, y: 0, z: 0, radius: 0.5 });",
        )
        assert out == {"radius": 0.25, "label": "Point 2 radius"}

    def test_an_end_that_releases_shows_it(self) -> None:
        out = _editor(
            """
            openRow('cross'); await settle();
            out.ends = [control('cross', 'end-0').value, control('cross', 'end-1').value];
            """,
            setup="model.paths[0].release_at_end = true;",
        )
        assert out["ends"] == ["hold", "release"]

    def test_the_header_names_the_markers_on_the_path(self) -> None:
        out = _editor(
            "out.target = row('cross').querySelector('.osc-binding-target').textContent;"
            "out.others = row('loop').querySelector('.osc-binding-target').textContent;",
            setup="model.locks = { 2: 'CROSS', 5: 'cross' };",
        )
        assert out == {"target": "Marker 2, 5", "others": ""}

    def test_a_new_path_opens_as_the_one_the_picture_edits(self) -> None:
        out = _editor(
            """
            editor().querySelector('[data-pe="new"]').click(); await settle();
            out.open = row('path-3').open;
            out.selected = selectedRow();
            """
        )
        assert out == {"open": True, "selected": "path-3"}


class TestNamesForAssistiveTechnology:
    def test_each_point_field_and_delete_names_its_point(self) -> None:
        out = _editor(
            """
            openRow('cross'); await settle();
            out.names = labels('cross', ['x-0', 'y-0', 'z-1', 'delete-1']);
            """
        )
        assert out["names"] == ["Point 1 X", "Point 1 Y", "Point 2 Z", "Delete point 2"]

    def test_a_length_that_does_not_read_says_why(self) -> None:
        out = _editor(
            """
            openRow('cross'); await settle();
            var y = control('cross', 'y-0');
            typeInto(y, 'abc'); enter(y);
            out.invalid = y.getAttribute('aria-invalid');
            out.line = errorLine();
            out.puts = puts().length;
            """
        )
        assert out == {"invalid": "true", "line": 'Not saved. Point 1 Y: "abc" is not a length.', "puts": 0}
